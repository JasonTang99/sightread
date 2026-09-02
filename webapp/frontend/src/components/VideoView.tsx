import { useCallback, useEffect, useRef, useState } from "react";
import { useWindowKeydown } from "../hooks/useWindowKeydown";
import type { UserClip, UserClipsMap, VideoHighlightsMap, VideoStatuses, VideoTagsState } from "../types";

const MIN_CLIP_LEN = 0.5;
const NEW_CLIP_LEN = 4;

function fmtTime(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

/** A clip on the strip: user clips are bare {start,end}; suggestions carry a score. */
type EditableClip = { start: number; end: number; score?: number };

const toUserClip = (c: EditableClip): UserClip => ({ start: c.start, end: c.end });

interface ClipEditorProps {
  clips: EditableClip[];
  duration: number;
  playhead: number;
  /** true once the list is user-owned (blue); false = untouched suggestions (amber). */
  owned: boolean;
  selected: number | null;
  onSelect: (i: number | null) => void;
  onSeek: (time: number) => void;
  /** Drag about to start: snapshot the pre-drag list for undo. */
  onDragStart: () => void;
  /** Live edge-drag update: replace clip i, seek video to the dragged edge. No sorting. */
  onDragClip: (i: number, clip: UserClip, edgeTime: number) => void;
  /** Drag finished: commit (sort + persist). */
  onDragEnd: () => void;
  onDelete: (i: number) => void;
}

function ClipEditor({ clips, duration, playhead, owned, selected, onSelect, onSeek, onDragStart, onDragClip, onDragEnd, onDelete }: ClipEditorProps) {
  const stripRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<{ idx: number; edge: "start" | "end"; rect: DOMRect } | null>(null);
  const maxScore = Math.max(0, ...clips.map((c) => c.score ?? 0));

  const handleDown = (e: React.PointerEvent<HTMLDivElement>, idx: number, edge: "start" | "end") => {
    e.stopPropagation();
    e.preventDefault();
    const rect = stripRef.current?.getBoundingClientRect();
    if (!rect || rect.width <= 0) return;
    onDragStart();
    dragRef.current = { idx, edge, rect };
    e.currentTarget.setPointerCapture(e.pointerId);
    onSelect(idx);
  };

  const handleMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const d = dragRef.current;
    if (!d) return;
    const c = clips[d.idx];
    if (!c) return;
    let t = ((e.clientX - d.rect.left) / d.rect.width) * duration;
    if (d.edge === "start") t = Math.min(t, c.end - MIN_CLIP_LEN);
    else t = Math.max(t, c.start + MIN_CLIP_LEN);
    t = Math.min(duration, Math.max(0, t));
    onDragClip(d.idx, d.edge === "start" ? { start: t, end: c.end } : { start: c.start, end: t }, t);
  };

  const handleUp = () => {
    if (!dragRef.current) return;
    dragRef.current = null;
    onDragEnd();
  };

  return (
    <div
      className="shrink-0 bg-gray-950 px-3 py-2 select-none"
      onClick={() => onSelect(null)}
    >
      <div
        ref={stripRef}
        className="relative h-6 w-full rounded-md bg-white/[0.07] shadow-[inset_0_1px_2px_rgba(0,0,0,0.6)] touch-none cursor-pointer"
        onClick={(e) => {
          const rect = stripRef.current?.getBoundingClientRect();
          if (!rect || rect.width <= 0) return;
          const t = Math.min(duration, Math.max(0, ((e.clientX - rect.left) / rect.width) * duration));
          onSeek(t);
        }}
      >
        {[0.25, 0.5, 0.75].map((f) => (
          <div
            key={f}
            className="absolute top-1 bottom-1 w-px bg-white/10 pointer-events-none"
            style={{ left: `${f * 100}%` }}
          />
        ))}
        {clips.length === 0 && (
          <span className="absolute inset-0 flex items-center justify-center text-[10px] tracking-wide text-gray-500 pointer-events-none">
            no clips — press i to add one at the playhead
          </span>
        )}
        {clips.map((c, i) => {
          // Clamp to [0, 1]: the media's real duration can be shorter than the
          // duration the pipeline analyzed, which would push segments past 100%.
          const startFrac = Math.min(1, Math.max(0, c.start / duration));
          const endFrac = Math.min(1, Math.max(startFrac, c.end / duration));
          if (endFrac <= startFrac) return null;
          const isSel = i === selected;
          return (
            <div
              key={i}
              className={`group absolute -top-0.5 -bottom-0.5 rounded cursor-pointer transition-[filter] hover:brightness-110 ${
                owned
                  ? "bg-gradient-to-b from-sky-300 to-sky-500"
                  : "bg-gradient-to-b from-amber-300 to-amber-500"
              } ${isSel ? "ring-2 ring-white shadow-lg z-10" : "ring-1 ring-black/30"}`}
              style={{
                left: `${startFrac * 100}%`,
                width: `${(endFrac - startFrac) * 100}%`,
                minWidth: "10px",
                opacity: owned ? 1 : 0.5 + 0.5 * (maxScore > 0 ? (c.score ?? 0) / maxScore : 1),
              }}
              title={`${fmtTime(c.start)}–${fmtTime(c.end)}${c.score != null ? ` · score ${c.score.toFixed(2)}` : ""}`}
              onClick={(e) => { e.stopPropagation(); onSelect(i); onSeek(Math.max(0, c.start)); }}
            >
              <div
                className="absolute inset-y-0 left-0 w-2 rounded-l cursor-ew-resize flex items-center justify-center"
                onClick={(e) => e.stopPropagation()}
                onPointerDown={(e) => handleDown(e, i, "start")}
                onPointerMove={handleMove}
                onPointerUp={handleUp}
                onPointerCancel={handleUp}
              >
                <div className={`h-3 w-0.5 rounded-full bg-black/40 transition-opacity ${isSel ? "opacity-100" : "opacity-0 group-hover:opacity-100"}`} />
              </div>
              <div
                className="absolute inset-y-0 right-0 w-2 rounded-r cursor-ew-resize flex items-center justify-center"
                onClick={(e) => e.stopPropagation()}
                onPointerDown={(e) => handleDown(e, i, "end")}
                onPointerMove={handleMove}
                onPointerUp={handleUp}
                onPointerCancel={handleUp}
              >
                <div className={`h-3 w-0.5 rounded-full bg-black/40 transition-opacity ${isSel ? "opacity-100" : "opacity-0 group-hover:opacity-100"}`} />
              </div>
              {isSel && (
                <button
                  className="absolute top-1/2 -translate-y-1/2 right-3 z-20 w-4 h-4 rounded-full bg-black/50 text-white text-[10px] leading-none flex items-center justify-center hover:bg-black/80 shadow"
                  title="Delete clip (x)"
                  onClick={(e) => { e.stopPropagation(); onDelete(i); }}
                >×</button>
              )}
            </div>
          );
        })}
        <div
          className="absolute -top-1 -bottom-1 w-0.5 -translate-x-1/2 rounded-full bg-white shadow-[0_0_4px_rgba(255,255,255,0.9)] pointer-events-none z-20"
          style={{ left: `${Math.min(100, Math.max(0, (playhead / duration) * 100))}%` }}
        />
      </div>
    </div>
  );
}

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
  videos: string[];
  // Server-side decision per video. "reviewed" means exactly this, not a
  // browser-local memory of having pressed enter — see the note on `reviewed`.
  statuses?: VideoStatuses;
  onError: (msg: string) => void;
  onConfirmed: () => Promise<void>;
  favorites?: string[];
  onToggleFavorite?: (path: string) => Promise<void>;
  highlights?: VideoHighlightsMap;
  userClips?: UserClipsMap;
  videoTags?: VideoTagsState;
  onVideoTagsChange?: (tags: VideoTagsState) => void;
}

export function VideoView({
  videos,
  statuses = {},
  onError,
  onConfirmed,
  favorites = [],
  onToggleFavorite,
  highlights = {},
  userClips = {},
  videoTags = { tags: [], assignments: {} },
  onVideoTagsChange,
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
  const [submitting, setSubmitting] = useState(false);
  // Browsers block autoplay *with sound* until the user interacts with the page.
  // Start muted so the clip always plays, then unmute on the first gesture.
  const [soundOn, setSoundOn] = useState(false);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  // Duration from the video element once loadedmetadata fires; null until then.
  const [mediaDuration, setMediaDuration] = useState<number | null>(null);
  const [playhead, setPlayhead] = useState(0);
  // Working clip lists edited this session, keyed by path. Takes precedence
  // over server user_clips, which takes precedence over suggested highlights.
  // A null entry means "reverted to suggestions" — it masks a stale server
  // user_clips prop until the parent refetches.
  const [localClips, setLocalClips] = useState<Record<string, UserClip[] | null>>({});
  const [selected, setSelected] = useState<number | null>(null);
  const [exporting, setExporting] = useState(false);
  const [exportNote, setExportNote] = useState<string | null>(null);
  const [tagBusy, setTagBusy] = useState(false);

  const applyVideoTags = useCallback(
    async (body: { tags?: string[]; assign?: Record<string, string | null> }) => {
      setTagBusy(true);
      try {
        const res = await fetch("/api/video-tags", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (!res.ok) {
          const detail = await res.json().catch(() => null);
          throw new Error(detail?.detail ?? `Tag update failed: ${res.status}`);
        }
        const data = await res.json();
        onVideoTagsChange?.({ tags: data.tags ?? [], assignments: data.assignments ?? {} });
      } catch (e) {
        onError(e instanceof Error ? e.message : String(e));
      } finally {
        setTagBusy(false);
      }
    },
    [onError, onVideoTagsChange],
  );

  const assignTag = useCallback(
    (path: string, tag: string | null) => {
      applyVideoTags({ assign: { [path]: tag } });
    },
    [applyVideoTags],
  );

  const addTag = useCallback(async () => {
    const name = window.prompt("Tag name (export subfolder):");
    if (!name?.trim()) return;
    const next = [...videoTags.tags];
    if (!next.includes(name.trim())) next.push(name.trim());
    await applyVideoTags({ tags: next });
  }, [applyVideoTags, videoTags.tags]);

  const nextTag = useCallback(
    (cur: string | null): string | null => {
      const { tags } = videoTags;
      if (tags.length === 0) return null;
      if (!cur) return tags[0];
      const i = tags.indexOf(cur);
      return i < 0 || i >= tags.length - 1 ? null : tags[i + 1];
    },
    [videoTags],
  );

  const current: string | undefined = videos[Math.min(idx, videos.length - 1)];
  const suggested = (current && highlights[current]?.clips) || [];
  const localEntry = current ? localClips[current] : undefined;
  const ownedList =
    localEntry === null ? undefined : localEntry ?? (current ? userClips[current]?.clips : undefined);
  const owned = ownedList !== undefined;
  const clips: EditableClip[] = ownedList ?? suggested;
  const duration = mediaDuration ?? (current ? highlights[current]?.duration ?? null : null);
  const canEdit = current != null && duration != null && duration > 0;

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
    setMediaDuration(null);
    setPlayhead(0);
    setSelected(null);
  }, [current]);

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

  // --- Clip persistence: debounce a PUT per video path after any change. ---
  const putTimers = useRef<Record<string, number>>({});
  const pendingPuts = useRef<Record<string, UserClip[]>>({});

  const doPut = useCallback(async (path: string, clipList: UserClip[]) => {
    try {
      const res = await fetch("/api/clips", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path, clips: clipList.map(toUserClip) }),
        // Survives page unload — without this, a PUT in flight when the tab
        // closes gets aborted and the edit is silently lost.
        keepalive: true,
      });
      if (!res.ok) throw new Error(`Save clips failed: ${res.status}`);
    } catch (e) {
      // Keep local state so the user's edits survive; they retry on next change.
      onError(e instanceof Error ? e.message : String(e));
    }
  }, [onError]);

  const schedulePut = useCallback((path: string, clipList: UserClip[]) => {
    pendingPuts.current[path] = clipList;
    if (putTimers.current[path] != null) window.clearTimeout(putTimers.current[path]);
    putTimers.current[path] = window.setTimeout(() => {
      delete putTimers.current[path];
      const c = pendingPuts.current[path];
      delete pendingPuts.current[path];
      if (c) doPut(path, c);
    }, 600);
  }, [doPut]);

  const flushPuts = useCallback(async () => {
    const entries = Object.entries(pendingPuts.current);
    for (const [path] of entries) {
      if (putTimers.current[path] != null) window.clearTimeout(putTimers.current[path]);
      delete putTimers.current[path];
    }
    pendingPuts.current = {};
    await Promise.all(entries.map(([p, c]) => doPut(p, c)));
  }, [doPut]);

  // A still-debounced clip edit (up to 600ms unfired) would otherwise be lost
  // if the tab closes or this view unmounts before the timer fires.
  useEffect(() => {
    window.addEventListener("pagehide", flushPuts);
    return () => {
      window.removeEventListener("pagehide", flushPuts);
      flushPuts();
    };
  }, [flushPuts]);

  // --- Clip editing. First edit of any kind materializes the working list. ---
  // One-entry-per-action undo stack, per video path. A null snapshot means the
  // video was still on untouched suggestions, so undoing past the first edit
  // reverts it all the way back to the amber suggestion list.
  const clipHistoryRef = useRef<Record<string, (UserClip[] | null)[]>>({});

  const pushHistory = (path: string, snapshot: UserClip[] | null) => {
    const stack = clipHistoryRef.current[path] ?? (clipHistoryRef.current[path] = []);
    stack.push(snapshot);
    if (stack.length > 20) stack.shift();
  };

  /** What to record before an edit: the owned list, or null if still on suggestions. */
  const snapshotClips = (): UserClip[] | null => (owned ? clips.map(toUserClip) : null);

  const revertToSuggestions = async (path: string) => {
    // Cancel any queued PUT so it can't resurrect the entry after the DELETE.
    if (putTimers.current[path] != null) window.clearTimeout(putTimers.current[path]);
    delete putTimers.current[path];
    delete pendingPuts.current[path];
    setLocalClips((prev) => ({ ...prev, [path]: null }));
    try {
      const res = await fetch(`/api/clips?path=${encodeURIComponent(path)}`, { method: "DELETE" });
      if (!res.ok) throw new Error(`Revert clips failed: ${res.status}`);
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    }
  };

  const undoClipEdit = () => {
    if (!current) return;
    const stack = clipHistoryRef.current[current];
    if (!stack || stack.length === 0) return;
    const prev = stack.pop()!;
    setSelected(null);
    if (prev === null) revertToSuggestions(current);
    else applyClips(prev);
  };

  const applyClips = (next: UserClip[]) => {
    if (!current) return;
    setLocalClips((prev) => ({ ...prev, [current]: next }));
    schedulePut(current, next);
  };

  /** Sort by start, keep `keep` (a member of `list`) selected, persist. */
  const commitClips = (list: UserClip[], keep: UserClip | null) => {
    const sorted = [...list].sort((a, b) => a.start - b.start);
    setSelected(keep ? sorted.indexOf(keep) : null);
    applyClips(sorted);
  };

  const addClipAtPlayhead = () => {
    if (!canEdit || duration == null || !current) return;
    pushHistory(current, snapshotClips());
    const t = Math.max(0, Math.min(videoRef.current?.currentTime ?? playhead, duration - MIN_CLIP_LEN));
    const clip: UserClip = { start: t, end: Math.min(t + NEW_CLIP_LEN, duration) };
    commitClips([...clips.map(toUserClip), clip], clip);
  };

  const setInPoint = () => {
    if (!canEdit || duration == null || selected == null || !current) return;
    const cur = clips[selected];
    const t = Math.max(0, videoRef.current?.currentTime ?? playhead);
    if (!cur || t >= cur.end) return;
    pushHistory(current, snapshotClips());
    const list = clips.map(toUserClip);
    const updated: UserClip = { start: t, end: cur.end };
    list[selected] = updated;
    commitClips(list, updated);
  };

  const setOutPoint = () => {
    if (!canEdit || duration == null || selected == null || !current) return;
    const cur = clips[selected];
    const t = Math.min(videoRef.current?.currentTime ?? playhead, duration);
    if (!cur || t <= cur.start) return;
    pushHistory(current, snapshotClips());
    const list = clips.map(toUserClip);
    const updated: UserClip = { start: cur.start, end: t };
    list[selected] = updated;
    commitClips(list, updated);
  };

  const deleteClip = (i: number) => {
    if (!canEdit || i < 0 || i >= clips.length || !current) return;
    pushHistory(current, snapshotClips());
    const list = clips.map(toUserClip);
    list.splice(i, 1);
    setSelected(null);
    applyClips(list);
  };

  const beginDrag = () => {
    if (!current) return;
    pushHistory(current, snapshotClips());
  };

  const handleDragClip = (i: number, clip: UserClip, edgeTime: number) => {
    const list = clips.map(toUserClip);
    if (!list[i]) return;
    list[i] = clip;
    applyClips(list);
    // Scrub feedback: follow the dragged edge in the video.
    const el = videoRef.current;
    if (el) el.currentTime = edgeTime;
  };

  const handleDragEnd = () => {
    const list = clips.map(toUserClip);
    commitClips(list, selected != null ? list[selected] ?? null : null);
  };

  const exportClips = async () => {
    if (!current || exporting || clips.length === 0) return;
    setExporting(true);
    setExportNote(null);
    try {
      await flushPuts();
      const res = await fetch("/api/clips/export", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: current, mode: "reencode" }),
      });
      if (!res.ok) throw new Error(`Export failed: ${res.status}`);
      const d = await res.json();
      const n = (d.files ?? []).length;
      setExportNote(`✓ exported ${n} file${n === 1 ? "" : "s"}`);
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    } finally {
      setExporting(false);
    }
  };

  useEffect(() => {
    if (!exportNote) return;
    const t = setTimeout(() => setExportNote(null), 5000);
    return () => clearTimeout(t);
  }, [exportNote]);

  useWindowKeydown((e) => {
    if (
      e.target instanceof HTMLInputElement ||
      e.target instanceof HTMLSelectElement ||
      e.target instanceof HTMLTextAreaElement ||
      e.target instanceof HTMLButtonElement
    ) return;
    if (e.key === "Enter") {
      e.preventDefault();
      if (current) persist(current, keeps[current] ?? true);
      setIdx((i) => Math.min(videos.length - 1, i + 1));
      return;
    }
    // 1–9: apply tag by slot (same convention as cluster rank keys).
    if (/^[1-9]$/.test(e.key)) {
      e.preventDefault();
      if (!current || !favorites.includes(current) || tagBusy) return;
      const tag = videoTags.tags[parseInt(e.key, 10) - 1];
      if (tag) assignTag(current, tag);
      return;
    }
    switch (e.key) {
      case "j":
      case "ArrowDown":
        e.preventDefault();
        setIdx((i) => Math.min(videos.length - 1, i + 1));
        break;
      case "k":
      case "ArrowUp":
        e.preventDefault();
        setIdx((i) => Math.max(0, i - 1));
        break;
      case " ": {
        e.preventDefault();
        const v = videos[idx];
        if (v) {
          const next = !(keeps[v] ?? true);
          setKeeps((prev) => ({ ...prev, [v]: next }));
          persist(v, next);
        }
        break;
      }
      case "s": {
        e.preventDefault();
        if (onToggleFavorite && current) onToggleFavorite(current).catch((err) => onError(String(err)));
        break;
      }
      case "t": {
        e.preventDefault();
        if (!current || !favorites.includes(current) || tagBusy) break;
        assignTag(current, nextTag(videoTags.assignments[current] ?? null));
        break;
      }
      case "l": {
        e.preventDefault();
        const el = videoRef.current;
        if (!el) break;
        if (el.paused) el.play(); else el.pause();
        break;
      }
      case "ArrowRight": {
        e.preventDefault();
        const el = videoRef.current;
        if (!el) break;
        el.currentTime = Math.min(duration ?? el.duration, el.currentTime + 10);
        break;
      }
      case "ArrowLeft": {
        e.preventDefault();
        const el = videoRef.current;
        if (!el) break;
        el.currentTime = Math.max(0, el.currentTime - 10);
        break;
      }
      case "n": {
        if (clips.length === 0) break;
        e.preventDefault();
        const el = videoRef.current;
        if (!el) break;
        const next = clips.find((c) => c.start > el.currentTime);
        if (next) el.currentTime = next.start;
        break;
      }
      case "p": {
        if (clips.length === 0) break;
        e.preventDefault();
        const el = videoRef.current;
        if (!el) break;
        const before = clips.filter((c) => c.start < el.currentTime - 1);
        if (before.length > 0) el.currentTime = before[before.length - 1].start;
        break;
      }
      case "i": {
        if (!canEdit) break;
        e.preventDefault();
        // With a clip selected, i trims its start (mirror of o); otherwise
        // it drops a fresh clip at the playhead.
        if (selected != null) setInPoint();
        else addClipAtPlayhead();
        break;
      }
      case "o": {
        if (!canEdit || selected == null) break;
        e.preventDefault();
        setOutPoint();
        break;
      }
      case "x":
      case "Backspace": {
        if (!canEdit || selected == null) break;
        e.preventDefault();
        deleteClip(selected);
        break;
      }
      case "u": {
        if (!canEdit) break;
        e.preventDefault();
        undoClipEdit();
        break;
      }
    }
  });

  // Write one video's decision through as soon as it's made. Keeping it in
  // component state until a bulk confirm meant a reviewed video still read as
  // undecided everywhere else — the timeline drew it grey, and a reload lost
  // the review entirely.
  const persist = (path: string, keep: boolean) => {
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
  };

  const confirm = async () => {
    setSubmitting(true);
    try {
      const deletePaths = videos.filter((v) => !keeps[v]);
      const res = await fetch("/api/confirm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        // Send the keeps too, not just the deletes: an unrecorded keep is
        // indistinguishable from unreviewed footage, which is what left the
        // timeline unable to colour a video tile.
        body: JSON.stringify({ delete_paths: deletePaths, decided_paths: videos }),
      });
      if (!res.ok) throw new Error(`Confirm failed: ${res.status}`);
      await onConfirmed();
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  };

  if (videos.length === 0 || !current) {
    return <p className="text-sm text-gray-500 p-4">No videos found in this folder.</p>;
  }

  const isKept = keeps[current] ?? true;
  const isFavorited = favorites.includes(current);
  const name = current.split("/").pop() ?? current;
  const nDelete = videos.filter((v) => !keeps[v]).length;

  return (
    <div className="-mx-2 -mt-2 flex flex-col" style={{ height: "calc(100vh - 2.25rem)" }}>
      {/* Toolbar */}
      <div className="bg-white border-b border-gray-200 px-3 py-1.5 flex items-center gap-3 shrink-0">
        <span className="text-xs text-gray-500 tabular-nums">{Math.min(idx, videos.length - 1) + 1} / {videos.length}</span>
        <span className="text-xs text-gray-600 truncate max-w-xs">{name}</span>
        <span className={`text-xs font-medium px-2 py-0.5 rounded shrink-0 ${isKept ? "bg-green-100 text-green-700" : "bg-red-100 text-red-700"}`}>
          {isKept ? "Keep" : "Delete"}
        </span>
        {reviewed(current) && (
          <span className="text-xs font-medium px-2 py-0.5 rounded shrink-0 bg-blue-50 text-blue-600" title="Reviewed (enter)">
            ✓ reviewed
          </span>
        )}
        <button
          onClick={() => onToggleFavorite?.(current).catch((e) => onError(String(e)))}
          className={`text-base leading-none shrink-0 transition-colors ${isFavorited ? "text-yellow-400" : "text-gray-300 hover:text-yellow-400"}`}
          title="Toggle favorite (s)"
        >★</button>
        {isFavorited && (
          <div className="flex items-center gap-1 shrink-0 max-w-md overflow-x-auto">
            <button
              type="button"
              disabled={tagBusy}
              onClick={() => assignTag(current, null)}
              className={`text-[10px] px-1.5 py-0.5 rounded border ${
                !videoTags.assignments[current]
                  ? "bg-violet-100 border-violet-300 text-violet-800"
                  : "border-gray-200 text-gray-500 hover:bg-gray-50"
              }`}
              title="No tag → …/untagged/"
            >—</button>
            {videoTags.tags.map((tag, i) => (
              <button
                key={tag}
                type="button"
                disabled={tagBusy}
                onClick={() => assignTag(current, tag)}
                className={`text-[10px] px-1.5 py-0.5 rounded border whitespace-nowrap ${
                  videoTags.assignments[current] === tag
                    ? "bg-violet-100 border-violet-300 text-violet-800"
                    : "border-gray-200 text-gray-600 hover:bg-gray-50"
                }`}
                title={i < 9 ? `Tag ${i + 1} (${i + 1}) → …/${tag}/` : `Export to …/${tag}/`}
              >
                {i < 9 && <span className="font-bold mr-0.5">{i + 1}</span>}
                {tag}
              </button>
            ))}
            <button
              type="button"
              disabled={tagBusy}
              onClick={() => addTag()}
              className="text-[10px] px-1.5 py-0.5 rounded border border-dashed border-gray-300 text-gray-500 hover:bg-gray-50"
              title="Add tag"
            >+</button>
          </div>
        )}
        <span className="text-xs text-gray-300 truncate">
          j/k · ←/→ ±10s · space toggle · l pause · s star · 1–9 tag · t cycle tag · enter confirm{clips.length > 0 ? " · n/p clips" : ""}{canEdit ? " · i/o in-out · x del · u undo" : ""}
        </span>
        <div className="ml-auto flex items-center gap-2 shrink-0">
          {exportNote && <span className="text-xs text-emerald-600">{exportNote}</span>}
          {canEdit && (
            <>
              <button
                onClick={addClipAtPlayhead}
                className="px-2 py-1 text-xs border border-gray-200 rounded text-gray-600 hover:bg-gray-50"
                title="Add a 4s clip at the playhead (i, with no clip selected)"
              >＋ clip</button>
              <button
                onClick={exportClips}
                disabled={clips.length === 0 || exporting}
                className="px-2 py-1 text-xs border border-gray-200 rounded text-gray-600 hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed inline-flex items-center gap-1.5"
                title="Export clips as files (re-encode)"
              >
                {exporting && <span className="inline-block w-3 h-3 border-2 border-gray-400 border-t-transparent rounded-full animate-spin" />}
                {exporting ? "Exporting…" : `✂ Export ${clips.length} clip${clips.length === 1 ? "" : "s"}`}
              </button>
            </>
          )}
          {nDelete > 0 && <span className="text-xs text-gray-400">{nDelete} → trash</span>}
          <button onClick={confirm} disabled={submitting}
            title={nDelete === 0
              ? "Record every video in this list as kept"
              : `Move ${nDelete} video${nDelete === 1 ? "" : "s"} to trash, keep the rest`}
            className="px-3 py-1 text-sm font-medium bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed">
            {submitting ? "…" : "✓ Confirm"}
          </button>
        </div>
      </div>

      {/* Full-viewport video */}
      <div
        className={`flex-1 min-h-0 overflow-hidden bg-black border-4 transition-colors ${
          isFavorited ? "border-yellow-400" : isKept ? "border-green-400" : "border-red-400"
        }`}
      >
        <video
          key={current}
          ref={videoRef}
          src={`/api/video?path=${encodeURIComponent(current)}`}
          controls
          autoPlay
          loop
          muted={!soundOn}
          className="w-full h-full object-contain"
          onLoadedMetadata={(e) => {
            const d = e.currentTarget.duration;
            if (Number.isFinite(d) && d > 0) setMediaDuration(d);
          }}
          onTimeUpdate={(e) => setPlayhead(e.currentTarget.currentTime)}
          onError={() => onError(`Failed to load video: ${name}`)}
        />
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

      {/* Clip editor strip — below the video, mirroring the scrubber above it.
          Amber = untouched suggestions, blue = user-owned clip list. */}
      {canEdit && duration != null && (
        <ClipEditor
          clips={clips}
          duration={duration}
          playhead={playhead}
          owned={owned}
          selected={selected}
          onSelect={setSelected}
          onSeek={(t) => { const el = videoRef.current; if (el) el.currentTime = t; }}
          onDragStart={beginDrag}
          onDragClip={handleDragClip}
          onDragEnd={handleDragEnd}
          onDelete={deleteClip}
        />
      )}
    </div>
  );
}
