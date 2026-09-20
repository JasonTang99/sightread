import { useEffect, useRef, useState } from "react";
import { useWindowKeydown } from "../hooks/useWindowKeydown";
import { useMediaTags } from "../hooks/useMediaTags";
import { TagBar } from "./TagBar";
import { LiveMotion } from "./LiveMotion";
import { DeviceBadge } from "./DeviceBadge";
import { deviceOf } from "../device";
import { nextAfterConfirm } from "../decisions";
import type { Cluster, PhotoDecisions, VideoTagsState } from "../types";

interface Props {
  // The project folder, so a tile can name the camera folder it came from.
  folder?: string;
  singletons: Cluster[];
  decisions: PhotoDecisions;
  // Enter jumps to the next unconfirmed single instead of the next one.
  skipReviewed?: boolean;
  favorites: string[];
  onRefresh: () => Promise<void>;
  onError: (msg: string) => void;
  onToggleFavorite: (path: string) => Promise<void>;
  // Enter on the last single (or last still-pending, with skip on) opens
  // the next tab instead of sitting on the row just confirmed.
  onAdvance?: () => void;
  videoTags?: VideoTagsState;
  onVideoTagsChange?: (tags: VideoTagsState) => void;
}

interface FlatImage {
  cluster_id: number;
  path: string;
  score: number;
  motion?: string;
  model?: string;
}

export function SingletonsView({
  folder,
  singletons,
  decisions,
  skipReviewed = false,
  favorites,
  onRefresh,
  onError,
  onToggleFavorite,
  onAdvance,
  videoTags = { tags: [], assignments: {} },
  onVideoTagsChange,
}: Props) {
  const [idx, setIdx] = useState(0);
  const [keeps, setKeeps] = useState<Record<string, boolean>>({});
  const [submitting, setSubmitting] = useState(false);
  // Confirmed this session. Enter does not wait for the refresh, so a quick
  // second press would still see the first single as undecided and wrap
  // back onto it.
  const confirmedRef = useRef<Set<string>>(new Set());
  const favSet = new Set(favorites);

  const { assignTag, addTag, nextTag, busy: tagBusy, flash: tagFlash, clearFlash } = useMediaTags(
    videoTags,
    onVideoTagsChange,
    favorites,
    onToggleFavorite,
    onError,
    "no tag",
  );

  const items: FlatImage[] = singletons
    .map((c) => ({ cluster_id: c.cluster_id, ...c.images[0] }))
    .sort((a, b) => a.score - b.score);

  const isKeptOf = (path: string) => favSet.has(path) || (keeps[path] ?? false);

  useEffect(() => {
    for (const it of items.slice(idx + 1, idx + 4)) {
      const el = new Image();
      el.src = `/api/image?path=${encodeURIComponent(it.path)}&w=2400`;
    }
  }, [idx]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    setKeeps((prev) => {
      const next: Record<string, boolean> = {};
      for (const it of items) {
        next[it.path] = it.path in prev ? prev[it.path] : false;
      }
      return next;
    });
    const firstUnconfirmed = items.findIndex((it) => !(it.path in decisions));
    setIdx(firstUnconfirmed >= 0 ? firstUnconfirmed : 0);
  }, [singletons.length]);

  useEffect(() => {
    clearFlash();
  }, [idx]); // eslint-disable-line react-hooks/exhaustive-deps

  // Turning skip-reviewed on jumps straight to the next thing still needing
  // a decision, same landing spot confirm would drop you at.
  useEffect(() => {
    if (!skipReviewed) return;
    const first = items.findIndex((it) => !(it.path in decisions));
    if (first !== -1) setIdx(first);
  }, [skipReviewed]); // eslint-disable-line react-hooks/exhaustive-deps

  const confirmCurrent = () => {
    const it = items[idx];
    if (!it) return;
    const keep = isKeptOf(it.path);
    fetch("/api/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        delete_paths: keep ? [] : [it.path],
        decided_paths: [it.path],
      }),
    })
      .then((r) => {
        if (!r.ok) throw new Error(`Confirm failed: ${r.status}`);
        return onRefresh();
      })
      .catch((err) => onError(err instanceof Error ? err.message : String(err)));
    confirmedRef.current.add(it.path);
    const hop = nextAfterConfirm(
      items.length,
      idx,
      skipReviewed,
      (i) => !(items[i].path in decisions) && !confirmedRef.current.has(items[i].path),
    );
    if (hop === "advance") onAdvance?.();
    else setIdx(hop);
  };

  useWindowKeydown((e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      confirmCurrent();
      return;
    }
    if (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement || e.target instanceof HTMLTextAreaElement) return;
    if (/^[1-9]$/.test(e.key)) {
      e.preventDefault();
      const it = items[idx];
      if (!it || tagBusy) return;
      const tag = videoTags.tags[parseInt(e.key, 10) - 1];
      if (tag) {
        setKeeps((prev) => ({ ...prev, [it.path]: true }));
        assignTag(it.path, tag);
      }
      return;
    }
    switch (e.key) {
      case "j":
      case "ArrowDown":
      case "ArrowRight":
        e.preventDefault();
        setIdx((i) => Math.min(items.length - 1, i + 1));
        break;
      case "k":
      case "ArrowUp":
      case "ArrowLeft":
        e.preventDefault();
        setIdx((i) => Math.max(0, i - 1));
        break;
      case " ": {
        e.preventDefault();
        const it = items[idx];
        if (it && !favSet.has(it.path)) setKeeps((prev) => ({ ...prev, [it.path]: !prev[it.path] }));
        break;
      }
      case "s": {
        e.preventDefault();
        const it = items[idx];
        if (it) {
          if (!favSet.has(it.path)) setKeeps((prev) => ({ ...prev, [it.path]: true }));
          onToggleFavorite(it.path).catch((err) =>
            onError(err instanceof Error ? err.message : String(err))
          );
        }
        break;
      }
      case "t": {
        e.preventDefault();
        const it = items[idx];
        if (!it || tagBusy) break;
        setKeeps((prev) => ({ ...prev, [it.path]: true }));
        assignTag(it.path, nextTag(videoTags.assignments[it.path] ?? null));
        break;
      }
    }
  });

  const confirm = async () => {
    setSubmitting(true);
    try {
      const deletePaths = items.filter((it) => !isKeptOf(it.path)).map((it) => it.path);
      const res = await fetch("/api/confirm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          delete_paths: deletePaths,
          decided_paths: items.map((it) => it.path),
        }),
      });
      if (!res.ok) throw new Error(`Confirm failed: ${res.status}`);
      await onRefresh();
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  };

  if (items.length === 0) return <p className="text-sm text-gray-500 p-4">No singles.</p>;

  const current = items[Math.min(idx, items.length - 1)];
  const isFav = favSet.has(current.path);
  const isKept = isKeptOf(current.path);
  const isConfirmed = current.path in decisions;
  const nDelete = items.filter((it) => !isKeptOf(it.path)).length;
  const currentTag = videoTags.assignments[current.path] ?? null;

  return (
    <div className="-mx-2 -mt-2 flex flex-col" style={{ height: "calc(100vh - 2.25rem)" }}>
      {/* Toolbar */}
      <div className="bg-white border-b border-gray-200 px-3 py-1.5 flex items-center gap-3 shrink-0">
        <span className="text-xs text-gray-500 tabular-nums">{Math.min(idx, items.length - 1) + 1} / {items.length}</span>
        <span className="text-xs text-gray-600 truncate max-w-xs" title={current.path}>{current.path.split("/").pop()}</span>
        <span className="text-xs font-mono text-gray-600">{current.score.toFixed(2)}</span>
        <span className={`text-xs font-medium px-2 py-0.5 rounded ${isKept ? "bg-green-100 text-green-700" : "bg-red-100 text-red-700"}`}>
          {isKept ? "Keep" : "Delete"}
        </span>
        {isConfirmed && <span className="text-xs font-medium px-2 py-0.5 rounded bg-gray-100 text-gray-500">confirmed</span>}
        {isFav && <span className="text-yellow-500 text-sm leading-none" title="Favorited">★</span>}
        <TagBar
          tags={videoTags.tags}
          currentTag={currentTag}
          isFavorited={isFav}
          busy={tagBusy}
          flash={tagFlash}
          untaggedLabel="no tag"
          untaggedTitle="No tag → trip root"
          emptyBadgeTitle="Exports to trip root"
          onAssign={(tag) => {
            if (tag !== null) setKeeps((prev) => ({ ...prev, [current.path]: true }));
            assignTag(current.path, tag);
          }}
          onAdd={addTag}
        />
        <span className="text-xs text-gray-300">j/k · ←/→ move · space toggle · s star · 1–9 tag · t cycle · enter confirm+next</span>
        <div className="ml-auto flex items-center gap-2">
          {nDelete > 0 && <span className="text-xs text-gray-400">{nDelete} → trash</span>}
          <button onClick={confirm} disabled={submitting}
            className="px-3 py-1 text-sm font-medium bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50">
            {submitting ? "…" : "✓ Confirm"}
          </button>
        </div>
      </div>

      {/* Full-viewport image */}
      <div
        className={`flex-1 min-h-0 overflow-hidden flex items-center justify-center bg-gray-50 cursor-pointer border-4 transition-colors relative ${
          isFav ? "border-yellow-400" : isKept ? "border-green-400" : "border-red-400"
        }`}
        onClick={() => {
          if (!isFav) setKeeps((prev) => ({ ...prev, [current.path]: !prev[current.path] }));
        }}
      >
        <img
          key={current.path}
          src={`/api/image?path=${encodeURIComponent(current.path)}&w=2400`}
          alt=""
          className="max-w-full max-h-full object-contain"
        />
        <DeviceBadge
          device={deviceOf(current.path, folder)}
          model={current.model}
          corner="bottom-2 left-2"
        />
        {current.motion && (
          <LiveMotion key={current.path} motion={current.motion} fit="contain" corner="top-2 left-2" />
        )}
      </div>
    </div>
  );
}
