import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useShortcuts } from "../hooks/useWindowKeydown";
import { useMediaTags } from "../hooks/useMediaTags";
import { TagBar } from "./TagBar";
import { nextAfterConfirm } from "../decisions";
import type { VideoStatuses, VideoTagsState } from "../types";
import { VIDEO_KEYS, typingTarget } from "../shortcuts";

// Matches UNTAGGED_DIR in webapp/exports.py: a favourited clip with no tag is
// still delivered, into .../untagged/. Naming it in the UI keeps "no tag" from
// reading as "not exported".
const UNTAGGED_LABEL = "untagged";

/** Off-screen buffer warmer for a neighbouring video.
 *
 * Tears its own load down on unmount. Detaching a <video> is not enough: Chrome
 * keeps a detached media element's request alive until the element is garbage
 * collected, and each live request holds one of the six connections the browser
 * will open to a host. Stepping through footage mounts a fresh element per
 * video, so the zombie loads pile up and after roughly ten videos every socket
 * is taken — new videos and even /api/confirm just queue, and the page looks
 * dead until a reload drops the connections. Clearing src and calling load()
 * frees the socket at once.
 */
function PreloadVideo({ src }: { src: string }) {
  const ref = useRef<HTMLVideoElement | null>(null);
  useEffect(() => {
    const el = ref.current;
    return () => {
      if (!el) return;
      el.pause();
      el.removeAttribute("src");
      el.load();
    };
  }, []);
  return <video ref={ref} src={src} preload="auto" muted style={{ display: "none" }} onError={() => {}} />;
}

interface Props {
  // The app header's slot for this view's controls.
  controlsEl: HTMLElement | null;
  videos: string[];
  // Server-side decision per video. "reviewed" means exactly this, not a
  // browser-local memory of having pressed enter — see the note on `reviewed`.
  statuses?: VideoStatuses;
  // Enter jumps to the next unreviewed clip instead of the next one.
  skipReviewed?: boolean;
  onError: (msg: string) => void;
  favorites?: string[];
  onToggleFavorite?: (path: string) => Promise<void>;
  videoTags?: VideoTagsState;
  onVideoTagsChange?: (tags: VideoTagsState) => void;
  // Enter on the last clip (or last still-pending, with skip on) opens the
  // next tab instead of sitting on the row just confirmed.
  onAdvance?: () => void;
}

export function VideoView({
  controlsEl,
  videos,
  statuses = {},
  skipReviewed = false,
  onError,
  favorites = [],
  onToggleFavorite,
  videoTags = { tags: [], assignments: {} },
  onVideoTagsChange,
  onAdvance,
}: Props) {
  const [idx, setIdx] = useState(0);
  const [keeps, setKeeps] = useState<Record<string, boolean>>({});
  // Whether a video has been decided, straight off the server. This used to be
  // a localStorage set of paths, which drifted the moment the two disagreed:
  // clearing the decisions server-side left every video still badged "reviewed"
  // here while the header counted them unreviewed, and the set was one global
  // key pruned against the open project, so switching projects silently wiped
  // the other one's marks.
  // Decisions made since the last refetch. persist() writes through without
  // reloading, so without this the badge would lag a keystroke behind the
  // server. Session-only on purpose: a reload takes the server's word, which is
  // what stops the two from drifting apart the way localStorage did.
  const [justDecided, setJustDecided] = useState<Set<string>>(new Set());
  const reviewed = useCallback(
    (path: string) =>
      justDecided.has(path) || (path in statuses && statuses[path] !== "undecided"),
    [statuses, justDecided],
  );
  // Clips confirmed with Enter this session, ahead of persist() coming back.
  const enteredRef = useRef<Set<string>>(new Set());
  // Browsers block autoplay *with sound* until the user interacts with the page.
  // Start muted so the clip always plays, then unmute on the first gesture.
  const [soundOn, setSoundOn] = useState(false);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [videoSrc, setVideoSrc] = useState<string | null>(null);
  const [videoStatus, setVideoStatus] = useState<string | null>(null);
  const { assignTag, nextTag, busy: tagBusy, flash: tagFlash, clearFlash } = useMediaTags(
    videoTags,
    onVideoTagsChange,
    favorites,
    onToggleFavorite,
    onError,
    UNTAGGED_LABEL,
  );

  // A flash left over from the previous clip would read as this one's tag.
  useEffect(() => {
    clearFlash();
  }, [idx]); // eslint-disable-line react-hooks/exhaustive-deps

  const current: string | undefined = videos[Math.min(idx, videos.length - 1)];
  // Track the previously-shown video so a `videos` prop change (e.g. after
  // confirm shrinks the list, or a same-length refetch swaps paths) doesn't
  // wipe every keep/delete decision or knock idx off the video the user was
  // actually looking at.
  const prevCurrentRef = useRef<string | undefined>(undefined);

  useEffect(() => {
    if (videos.length === 0) return;
    setKeeps((prev) => {
      const next: Record<string, boolean> = {};
      for (const v of videos) next[v] = prev[v] ?? true;
      return next;
    });
    const prevPath = prevCurrentRef.current;
    const stillThere = prevPath != null ? videos.indexOf(prevPath) : -1;
    if (stillThere >= 0) {
      setIdx(stillThere);
    } else {
      // Land on the first thing still needing a decision.
      const first = videos.findIndex((v) => !reviewed(v));
      setIdx(first >= 0 ? first : 0);
    }
  }, [videos, reviewed]);

  useEffect(() => {
    prevCurrentRef.current = current;
  }, [current]);

  // Turning skip-reviewed on jumps straight to the next thing still needing
  // a decision, same landing spot confirm would drop you at.
  useEffect(() => {
    if (!skipReviewed) return;
    const first = videos.findIndex((v) => !reviewed(v));
    if (first >= 0) setIdx(first);
  }, [skipReviewed]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (soundOn) return;
    const enable = () => setSoundOn(true);
    window.addEventListener("pointerdown", enable, { once: true });
    window.addEventListener("keydown", enable, { once: true });
    return () => {
      window.removeEventListener("pointerdown", enable);
      window.removeEventListener("keydown", enable);
    };
  }, [soundOn]);

  // Reset per-video playback state when switching videos (element remounts via key).
  useEffect(() => {
    setVideoSrc(null);
    setVideoStatus(null);
  }, [current]);

  // Probe /api/video before mounting <video>. A corrupt original (no moov atom)
  // and a failed transcode both look like a generic element error otherwise.
  // HEAD, not GET: a miss must not download the file. 503 is "still
  // transcoding" and is polled; 422 and a missing source file are final.
  // no-store so a 503, or a 404 left over from before HEAD reached this
  // route, is not reused for the next probe.
  useEffect(() => {
    if (!current) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const url = `/api/video?path=${encodeURIComponent(current)}`;
    const basename = current.split("/").pop() ?? current;

    const probe = async () => {
      const res = await fetch(url, { method: "HEAD", cache: "no-store" });
      if (cancelled) return;
      if (res.ok) {
        setVideoStatus(null);
        setVideoSrc(url);
        return;
      }
      if (res.status === 503) {
        setVideoSrc(null);
        setVideoStatus("Transcoding…");
        timer = setTimeout(probe, 2000);
        return;
      }
      let detail = res.statusText;
      try {
        const body = await fetch(url, { cache: "no-store" }).then((r) => r.json());
        if (typeof body.detail === "string") detail = body.detail;
      } catch {
        /* non-JSON error body */
      }
      setVideoSrc(null);
      setVideoStatus(detail);
      onError(`${basename}: ${detail}`);
    };

    setVideoSrc(null);
    setVideoStatus("Loading…");
    probe();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [current, onError]);

  // Keep autoplay/mute behavior: apply volume + mute state and kick off playback.
  useEffect(() => {
    const el = videoRef.current;
    if (!el) return;
    el.volume = 1;
    el.muted = !soundOn;
    el.play().catch(() => { /* autoplay race; controls let user start */ });
  }, [current, soundOn]);

  // Release the outgoing video's connection when we move on — same zombie-load
  // problem as PreloadVideo, and this element is the one still streaming. `el`
  // is captured at setup, so the cleanup tears down the video being left, not
  // the one being switched to.
  useEffect(() => {
    const el = videoRef.current;
    return () => {
      if (!el) return;
      el.pause();
      el.removeAttribute("src");
      el.load();
    };
  }, [current]);

  useShortcuts(
    VIDEO_KEYS,
    {
      "video-confirm": (e) => {
        e.preventDefault();
        const saved = current
          ? persist(current, favorites.includes(current) || (keeps[current] ?? true))
          : Promise.resolve();
        // justDecided only fills in once persist() hears back, so a quick
        // second Enter would still see this clip as unreviewed and wrap back
        // onto it. The ref is updated synchronously.
        if (current) enteredRef.current.add(current);
        const hop = nextAfterConfirm(
          videos.length,
          idx,
          skipReviewed,
          (i) => !reviewed(videos[i]) && !enteredRef.current.has(videos[i]),
        );
        // The next tab is usually the timeline, which refetches statuses as it
        // opens. Leaving before the write lands drew this clip as undecided.
        if (hop === "advance") saved.then(() => onAdvance?.());
        else setIdx(hop);
      },
      "video-tag-slot": (e) => {
        e.preventDefault();
        // No favourite check: assignTag stars the clip itself. Requiring the star
        // first made these keys a silent no-op, which reads as a broken feature.
        if (!current || tagBusy) return;
        const tag = videoTags.tags[parseInt(e.key, 10) - 1];
        if (tag) {
          setKeeps((prev) => ({ ...prev, [current]: true }));
          assignTag(current, tag);
        }
      },
      "video-next": (e) => {
        e.preventDefault();
        setIdx((i) => Math.min(videos.length - 1, i + 1));
      },
      "video-prev": (e) => {
        e.preventDefault();
        setIdx((i) => Math.max(0, i - 1));
      },
      "video-toggle": (e) => {
        e.preventDefault();
        const v = videos[idx];
        if (v && !favorites.includes(v)) {
          const next = !(keeps[v] ?? true);
          setKeeps((prev) => ({ ...prev, [v]: next }));
          persist(v, next);
        }
      },
      "video-star": (e) => {
        e.preventDefault();
        if (onToggleFavorite && current) {
          if (!favorites.includes(current)) setKeeps((prev) => ({ ...prev, [current]: true }));
          onToggleFavorite(current).catch((err) => onError(String(err)));
        }
      },
      "video-tag-cycle": (e) => {
        e.preventDefault();
        if (!current || tagBusy) return;
        setKeeps((prev) => ({ ...prev, [current]: true }));
        assignTag(current, nextTag(videoTags.assignments[current] ?? null));
      },
      "video-pause": (e) => {
        e.preventDefault();
        const el = videoRef.current;
        if (!el) return;
        if (el.paused) el.play(); else el.pause();
      },
      "video-seek-fwd": (e) => {
        e.preventDefault();
        const el = videoRef.current;
        if (!el) return;
        el.currentTime = Math.min(el.duration, el.currentTime + 10);
      },
      "video-seek-back": (e) => {
        e.preventDefault();
        const el = videoRef.current;
        if (!el) return;
        el.currentTime = Math.max(0, el.currentTime - 10);
      },
    },
    { ignore: (e) => typingTarget(e, true) },
  );

  // Write one video's decision through as soon as it's made. Keeping it in
  // component state until a bulk confirm meant a reviewed video still read as
  // undecided everywhere else — the timeline drew it grey, and a reload lost
  // the review entirely.
  const persist = (path: string, keep: boolean): Promise<void> =>
    fetch("/api/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ delete_paths: keep ? [] : [path], decided_paths: [path] }),
    })
      .then((res) => {
        if (!res.ok) throw new Error(`Confirm failed: ${res.status}`);
        setJustDecided((prev) => new Set(prev).add(path));
      })
      .catch((e) => onError(e instanceof Error ? e.message : String(e)));

  if (videos.length === 0 || !current) {
    return <p className="text-sm text-gray-500 p-4">No videos found in this folder.</p>;
  }

  const isFavorited = favorites.includes(current);
  const isKept = isFavorited || (keeps[current] ?? true);
  const currentTag = videoTags.assignments[current] ?? null;
  const name = current.split("/").pop() ?? current;

  return (
    <div
      className="-mx-2 -mt-2 flex flex-col"
      style={{ height: "calc(100vh - 2.25rem)" }}
      data-testid="video-view"
      data-index={Math.min(idx, videos.length - 1)}
    >
      {controlsEl && createPortal(
        <>
          <span
            className={`text-xs font-medium px-2 py-0.5 rounded shrink-0 ${
              isFavorited
                ? "bg-yellow-100 text-yellow-700"
                : isKept
                  ? "bg-green-100 text-green-700"
                  : "bg-red-100 text-red-700"
            }`}
            data-testid="video-status"
          >
            {isFavorited ? "★ Favorite" : isKept ? "Keep" : "Delete"}
          </span>
          {reviewed(current) && (
            <span className="text-xs font-medium px-2 py-0.5 rounded shrink-0 bg-blue-50 text-blue-600" title="Reviewed (enter)">
              ✓ reviewed
            </span>
          )}
          <TagBar
            tags={videoTags.tags}
            currentTag={currentTag}
            isFavorited={isFavorited}
            busy={tagBusy}
            flash={tagFlash}
            untaggedLabel={UNTAGGED_LABEL}
            untaggedTitle="No tag → …/untagged/"
            onAssign={(tag) => {
              if (tag !== null) setKeeps((prev) => ({ ...prev, [current]: true }));
              assignTag(current, tag);
            }}
          />
        </>,
        controlsEl,
      )}

      {/* Full-viewport video */}
      <div
        className={`flex-1 min-h-0 overflow-hidden bg-black border-4 transition-colors ${
          isFavorited ? "border-yellow-400" : isKept ? "border-green-400" : "border-red-400"
        }`}
      >
        {videoSrc ? (
        <video
          key={current}
          ref={videoRef}
          src={videoSrc}
          controls
          autoPlay
          loop
          muted={!soundOn}
          className="w-full h-full object-contain"
          onError={() => onError(`Failed to load video: ${name}`)}
        />
        ) : (
          <div className="flex items-center justify-center h-full text-gray-400 text-sm px-6 text-center">
            {videoStatus ?? "Loading…"}
          </div>
        )}
        {/* Warm the browser's cache for neighboring videos so j/k doesn't hit a cold fetch,
            in either direction — going back is just as common as going forward.
            cached_only=1: only pull the bitrate-capped transcode, never the raw
            (often ~190Mbps) original — a 404 here just means "not baked yet".
            One neighbour each way, not two: the browser opens six connections
            per host, and five simultaneous media loads left nothing for
            /api/confirm and /api/state. */}
        {[videos[idx - 1], videos[idx + 1]]
          .filter((v): v is string => v != null)
          .map((v) => (
            <PreloadVideo key={`preload-${v}`} src={`/api/video?path=${encodeURIComponent(v)}&cached_only=1`} />
          ))}
      </div>

    </div>
  );
}
