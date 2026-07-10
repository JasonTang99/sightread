import { useCallback, useEffect, useRef, useState } from "react";
import { useWindowKeydown } from "../hooks/useWindowKeydown";
import type { UserClip, UserClipsMap, VideoHighlightsMap } from "../types";

const CONFIRMED_KEY = "sightread_confirmed_videos";
const MIN_CLIP_LEN = 0.5;
const NEW_CLIP_LEN = 4;

function loadConfirmed(): Set<string> {
  try { return new Set(JSON.parse(localStorage.getItem(CONFIRMED_KEY) ?? "[]")); }
  catch { return new Set(); }
}

function saveConfirmed(s: Set<string>) {
  try { localStorage.setItem(CONFIRMED_KEY, JSON.stringify([...s])); } catch {}
}

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
  /** Live edge-drag update: replace clip i, seek video to the dragged edge. No sorting. */
  onDragClip: (i: number, clip: UserClip, edgeTime: number) => void;
  /** Drag finished: commit (sort + persist). */
  onDragEnd: () => void;
  onDelete: (i: number) => void;
}

function ClipEditor({ clips, duration, playhead, owned, selected, onSelect, onSeek, onDragClip, onDragEnd, onDelete }: ClipEditorProps) {
  const stripRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<{ idx: number; edge: "start" | "end"; rect: DOMRect } | null>(null);
  const maxScore = Math.max(0, ...clips.map((c) => c.score ?? 0));

  const handleDown = (e: React.PointerEvent<HTMLDivElement>, idx: number, edge: "start" | "end") => {
    e.stopPropagation();
    e.preventDefault();
    const rect = stripRef.current?.getBoundingClientRect();
    if (!rect || rect.width <= 0) return;
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
      ref={stripRef}
      className="relative h-7 w-full shrink-0 bg-gray-900 select-none touch-none"
      onClick={() => onSelect(null)}
    >
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
            className={`absolute top-0.5 bottom-0.5 rounded cursor-pointer transition-[filter] hover:brightness-125 ${
              owned ? "bg-sky-400" : "bg-amber-400"
            } ${isSel ? "ring-2 ring-white z-10" : ""}`}
            style={{
              left: `${startFrac * 100}%`,
              width: `${(endFrac - startFrac) * 100}%`,
              minWidth: "8px",
              opacity: owned ? 0.9 : 0.45 + 0.55 * (maxScore > 0 ? (c.score ?? 0) / maxScore : 1),
            }}
            title={`${fmtTime(c.start)}–${fmtTime(c.end)}${c.score != null ? ` · score ${c.score.toFixed(2)}` : ""}`}
            onClick={(e) => { e.stopPropagation(); onSelect(i); onSeek(Math.max(0, c.start)); }}
          >
            <div
              className="absolute inset-y-0 left-0 w-1.5 rounded-l cursor-ew-resize bg-black/25 hover:bg-black/50"
              onClick={(e) => e.stopPropagation()}
              onPointerDown={(e) => handleDown(e, i, "start")}
              onPointerMove={handleMove}
              onPointerUp={handleUp}
              onPointerCancel={handleUp}
            />
            <div
              className="absolute inset-y-0 right-0 w-1.5 rounded-r cursor-ew-resize bg-black/25 hover:bg-black/50"
              onClick={(e) => e.stopPropagation()}
              onPointerDown={(e) => handleDown(e, i, "end")}
              onPointerMove={handleMove}
              onPointerUp={handleUp}
              onPointerCancel={handleUp}
            />
            {isSel && (
              <button
                className="absolute top-1/2 -translate-y-1/2 right-2.5 z-20 w-3.5 h-3.5 rounded-full bg-black/50 text-white text-[9px] leading-none flex items-center justify-center hover:bg-black/80"
                title="Delete clip (x)"
                onClick={(e) => { e.stopPropagation(); onDelete(i); }}
              >×</button>
            )}
          </div>
        );
      })}
      <div
        className="absolute top-0 bottom-0 w-px bg-white pointer-events-none z-20"
        style={{ left: `${Math.min(100, Math.max(0, (playhead / duration) * 100))}%` }}
      />
    </div>
  );
}

interface Props {
  videos: string[];
  onError: (msg: string) => void;
  onConfirmed: () => Promise<void>;
  favorites?: string[];
  onToggleFavorite?: (path: string) => Promise<void>;
  highlights?: VideoHighlightsMap;
  userClips?: UserClipsMap;
}

export function VideoView({ videos, onError, onConfirmed, favorites = [], onToggleFavorite, highlights = {}, userClips = {} }: Props) {
  const [idx, setIdx] = useState(0);
  const [keeps, setKeeps] = useState<Record<string, boolean>>({});
  const [confirmed, setConfirmed] = useState<Set<string>>(loadConfirmed);
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
  const [localClips, setLocalClips] = useState<Record<string, UserClip[]>>({});
  const [selected, setSelected] = useState<number | null>(null);
  const [exporting, setExporting] = useState(false);
  const [exportNote, setExportNote] = useState<string | null>(null);

  const current: string | undefined = videos[Math.min(idx, videos.length - 1)];
  const suggested = (current && highlights[current]?.clips) || [];
  const ownedList = current ? localClips[current] ?? userClips[current]?.clips : undefined;
  const owned = ownedList !== undefined;
  const clips: EditableClip[] = ownedList ?? suggested;
  const duration = mediaDuration ?? (current ? highlights[current]?.duration ?? null : null);
  const canEdit = current != null && duration != null && duration > 0;

  useEffect(() => {
    const init: Record<string, boolean> = {};
    for (const v of videos) init[v] = true;
    setKeeps(init);
    const stored = loadConfirmed();
    const first = videos.findIndex((v) => !stored.has(v));
    setIdx(first >= 0 ? first : 0);
  }, [videos.length]);

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

  // --- Clip persistence: debounce a PUT per video path after any change. ---
  const putTimers = useRef<Record<string, number>>({});
  const pendingPuts = useRef<Record<string, UserClip[]>>({});

  const doPut = useCallback(async (path: string, clipList: UserClip[]) => {
    try {
      const res = await fetch("/api/clips", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path, clips: clipList.map(toUserClip) }),
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

  // --- Clip editing. First edit of any kind materializes the working list. ---
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
    if (!canEdit || duration == null) return;
    const t = Math.max(0, Math.min(videoRef.current?.currentTime ?? playhead, duration - MIN_CLIP_LEN));
    const clip: UserClip = { start: t, end: Math.min(t + NEW_CLIP_LEN, duration) };
    commitClips([...clips.map(toUserClip), clip], clip);
  };

  const setOutPoint = () => {
    if (!canEdit || duration == null || selected == null) return;
    const cur = clips[selected];
    const t = Math.min(videoRef.current?.currentTime ?? playhead, duration);
    if (!cur || t <= cur.start) return;
    const list = clips.map(toUserClip);
    const updated: UserClip = { start: cur.start, end: t };
    list[selected] = updated;
    commitClips(list, updated);
  };

  const deleteClip = (i: number) => {
    if (!canEdit || i < 0 || i >= clips.length) return;
    const list = clips.map(toUserClip);
    list.splice(i, 1);
    setSelected(null);
    applyClips(list);
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
    if (e.key === "Enter") {
      e.preventDefault();
      const cur = videos[idx];
      if (cur) {
        setConfirmed((prev) => {
          const next = new Set(prev);
          next.add(cur);
          saveConfirmed(next);
          return next;
        });
      }
      setIdx((i) => Math.min(videos.length - 1, i + 1));
      return;
    }
    if (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement || e.target instanceof HTMLTextAreaElement) return;
    // Let space play/pause when video element is focused
    if (e.target instanceof HTMLMediaElement) return;
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
        if (v) setKeeps((prev) => ({ ...prev, [v]: !prev[v] }));
        break;
      }
      case "s": {
        e.preventDefault();
        if (onToggleFavorite) onToggleFavorite(videos[idx]).catch((err) => onError(String(err)));
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
        addClipAtPlayhead();
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
    }
  });

  const confirm = async () => {
    setSubmitting(true);
    try {
      const deletePaths = videos.filter((v) => !keeps[v]);
      if (deletePaths.length > 0) {
        const res = await fetch("/api/confirm", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ delete_paths: deletePaths }),
        });
        if (!res.ok) throw new Error(`Confirm failed: ${res.status}`);
        await onConfirmed();
      }
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
        <button
          onClick={() => onToggleFavorite?.(current).catch((e) => onError(String(e)))}
          className={`text-base leading-none shrink-0 transition-colors ${isFavorited ? "text-yellow-400" : "text-gray-300 hover:text-yellow-400"}`}
          title="Toggle favorite (s)"
        >★</button>
        <span className="text-xs text-gray-300 truncate">
          j/k · space toggle · s star · enter confirm{clips.length > 0 ? " · n/p clips" : ""}{canEdit ? " · i/o in-out · x del" : ""}
        </span>
        <div className="ml-auto flex items-center gap-2 shrink-0">
          {exportNote && <span className="text-xs text-emerald-600">{exportNote}</span>}
          {canEdit && (
            <>
              <button
                onClick={addClipAtPlayhead}
                className="px-2 py-1 text-xs border border-gray-200 rounded text-gray-600 hover:bg-gray-50"
                title="Add a 4s clip at the playhead (i)"
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
            className="px-3 py-1 text-sm font-medium bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50">
            {submitting ? "…" : "✓ Confirm"}
          </button>
        </div>
      </div>

      {/* Full-viewport video */}
      <div
        className={`flex-1 min-h-0 overflow-hidden bg-black border-4 transition-colors ${
          isFavorited ? "border-yellow-400" : isKept ? "border-green-400" : "border-red-400"
        }`}
        onClick={() => setKeeps((prev) => ({ ...prev, [current]: !prev[current] }))}
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
          onClick={(e) => e.stopPropagation()}
          onLoadedMetadata={(e) => {
            const d = e.currentTarget.duration;
            if (Number.isFinite(d) && d > 0) setMediaDuration(d);
          }}
          onTimeUpdate={(e) => setPlayhead(e.currentTarget.currentTime)}
        />
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
          onDragClip={handleDragClip}
          onDragEnd={handleDragEnd}
          onDelete={deleteClip}
        />
      )}
    </div>
  );
}
