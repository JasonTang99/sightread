import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useShortcuts } from "../hooks/useWindowKeydown";
import { useMediaTags } from "../hooks/useMediaTags";
import { isDecided, isDoomed, nextAfterConfirm } from "../decisions";
import { TagBar } from "./TagBar";
import { LiveMotion } from "./LiveMotion";
import { deviceOf } from "../device";
import type { Cluster, PhotoDecisions, VideoTagsState } from "../types";
import { Icon } from "./ui";
import { CLUSTER_KEYS, typingTarget } from "../shortcuts";

interface Props {
  // The app header's slot for this view's controls.
  controlsEl: HTMLElement | null;
  // The project folder, so a tile can name the camera folder it came from.
  folder?: string;
  clusters: Cluster[];
  decisions: PhotoDecisions;
  // Confirm jumps to the next undecided cluster instead of the next one.
  skipReviewed?: boolean;
  favorites: string[];
  onRefresh: () => Promise<void>;
  onError: (msg: string) => void;
  onUndo: () => Promise<void>;
  onToggleFavorite: (path: string) => Promise<void>;
  // Enter on the last cluster (or last still-pending, with skip on) opens
  // the next tab instead of sitting on the row just confirmed.
  onAdvance?: () => void;
  videoTags?: VideoTagsState;
  onVideoTagsChange?: (tags: VideoTagsState) => void;
}

function imgUrl(path: string, w = 2400) {
  return `/api/image?path=${encodeURIComponent(path)}&w=${w}`;
}

export function ClusterView({ controlsEl, folder, clusters, decisions, skipReviewed = false, favorites, onRefresh, onError, onUndo, onToggleFavorite, onAdvance, videoTags = { tags: [], assignments: {} }, onVideoTagsChange }: Props) {
  const [idx, setIdx] = useState(() => {
    const first = clusters.findIndex((c) => !isDecided(c, decisions));
    return first === -1 ? 0 : first;
  });
  const [keeps, setKeeps] = useState<Record<string, boolean>>({});
  // Photos per row. Each cluster starts at its own default — three when every
  // frame is portrait, since two tall frames side by side leave most of the
  // width empty — and a pick from the header holds for that cluster only.
  const [colsOverride, setColsOverride] = useState<number | null>(null);
  // Portrait or not, per path, learnt from the decoded image. The pipeline
  // knows framing but does not ship it, and this way existing projects work
  // without a re-run. The preload below fills it for the next clusters, so
  // the default is usually settled before you arrive.
  const [portrait, setPortrait] = useState<Record<string, boolean>>({});
  const notePortrait = (path: string, el: HTMLImageElement) => {
    if (!el.naturalWidth) return;
    const tall = el.naturalHeight > el.naturalWidth;
    setPortrait((prev) => (prev[path] === tall ? prev : { ...prev, [path]: tall }));
  };
  const [submitting, setSubmitting] = useState(false);
  const [focusedImg, setFocusedImg] = useState(0);
  const imgRefs = useRef<(HTMLDivElement | null)[]>([]);
  const keepsByClusterRef = useRef<Record<number, Record<string, boolean>>>({});
  // Where confirm lands, by id: the list is rebuilt when the refresh comes
  // back, so an index taken before it may no longer point at the same
  // cluster.
  const landOnRef = useRef<number | "stay" | undefined>(undefined);

  const clusterIdx = Math.min(idx, clusters.length - 1);
  const cluster = clusters[clusterIdx];
  const favSet = new Set(favorites);

  useEffect(() => {
    for (const c of clusters.slice(clusterIdx + 1, clusterIdx + 3)) {
      for (const img of c.images) {
        const el = new Image();
        el.onload = () => notePortrait(img.path, el);
        el.src = imgUrl(img.path);
      }
    }
  }, [clusterIdx]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!cluster) return;
    const clusterId = cluster.cluster_id;
    const saved = keepsByClusterRef.current[clusterId];
    if (saved) {
      setKeeps(saved);
    } else {
      const init: Record<string, boolean> = {};
      // Decisions are per photo. A cluster can mix adopted keeps (the XT5
      // folder already reviewed) with still-blank iPhone frames — each
      // photo that has a status uses it; the rest fall back to rank.
      for (const img of cluster.images) {
        init[img.path] = img.path in decisions
          ? !isDoomed(decisions[img.path])
          : img.rank === 1;
      }
      setKeeps(init);
    }
    setFocusedImg(0);
    setColsOverride(null);
  }, [cluster?.cluster_id]); // eslint-disable-line react-hooks/exhaustive-deps

  // Remember selections per cluster so navigating back restores them
  useEffect(() => {
    if (cluster) keepsByClusterRef.current[cluster.cluster_id] = keeps;
  }, [keeps]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (idx >= clusters.length) setIdx(Math.max(0, clusters.length - 1));
  }, [clusters.length]);

  // Turning skip-reviewed on jumps straight to the next thing still needing
  // a decision, same landing spot confirm would drop you at.
  useEffect(() => {
    if (!skipReviewed) return;
    const first = clusters.findIndex((c) => !isDecided(c, decisions));
    if (first !== -1) setIdx(first);
  }, [skipReviewed]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const target = landOnRef.current;
    if (target === undefined) return;
    landOnRef.current = undefined;
    if (target === "stay") return;
    const next = clusters.findIndex((c) => c.cluster_id === target);
    if (next !== -1) setIdx(next);
  }, [clusters]);

  const isKeptOf = (path: string, rank: number) =>
    favSet.has(path) || (keeps[path] ?? rank === 1);

  const toggle = (path: string) => {
    if (favSet.has(path)) return;
    setKeeps((prev) => ({ ...prev, [path]: !prev[path] }));
  };

  // Keep this photo and mark the rest of the cluster for deletion. Starred
  // photos are kept regardless, so this leaves them alone.
  const keepOnly = (path: string) => {
    const next: Record<string, boolean> = {};
    for (const img of cluster.images) next[img.path] = img.path === path;
    setKeeps(next);
  };

  const star = (path: string) => {
    if (!favSet.has(path)) setKeeps((prev) => ({ ...prev, [path]: true }));
    onToggleFavorite(path).catch((err) =>
      onError(err instanceof Error ? err.message : String(err))
    );
  };

  const allPortrait = !!cluster && cluster.images.every((img) => portrait[img.path]);
  const cols = colsOverride ?? (allPortrait ? 3 : 2);

  const { assignTag, nextTag, busy: tagBusy, flash: tagFlash, clearFlash } = useMediaTags(
    videoTags,
    onVideoTagsChange,
    favorites,
    onToggleFavorite,
    onError,
    "no tag",
  );

  useEffect(() => {
    clearFlash();
  }, [cluster?.cluster_id, focusedImg]); // eslint-disable-line react-hooks/exhaustive-deps

  const undecidedCount = useMemo(
    () => clusters.filter((c) => !isDecided(c, decisions)).length,
    [clusters, decisions],
  );

  // Skipping leaves gaps behind you, and past a hundred clusters finding them
  // again by arrowing through is the slow part of a second pass.
  const jumpToUnreviewed = () => {
    const from = clusterIdx + 1;
    const ahead = clusters.findIndex((c, i) => i >= from && !isDecided(c, decisions));
    const next = ahead === -1 ? clusters.findIndex((c) => !isDecided(c, decisions)) : ahead;
    if (next !== -1) setIdx(next);
  };

  const confirm = async () => {
    setSubmitting(true);
    try {
      const deletePaths = cluster.images
        .filter((img) => !isKeptOf(img.path, img.rank))
        .map((img) => img.path);
      const res = await fetch("/api/confirm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          delete_paths: deletePaths,
          decided_paths: cluster.images.map((img) => img.path),
        }),
      });
      if (!res.ok) throw new Error(`Confirm failed: ${res.status}`);
      // With skip on, jump past clusters that already have a decision —
      // everything stays in the list for ←/→, only this hop changes.
      const hop = nextAfterConfirm(
        clusters.length,
        clusterIdx,
        skipReviewed,
        (i) => !isDecided(clusters[i], decisions),
      );
      if (hop === "advance") {
        landOnRef.current = "stay";
        await onRefresh();
        onAdvance?.();
        return;
      }
      landOnRef.current = clusters[hop].cluster_id;
      await onRefresh();
    } catch (e) {
      landOnRef.current = undefined;
      onError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  };

  const focusImage = (next: number) => {
    imgRefs.current[next]?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    setFocusedImg(next);
  };

  const skipCluster = () => setIdx(Math.min(clusters.length - 1, clusterIdx + 1));

  useShortcuts(CLUSTER_KEYS, {
    "cluster-prev": (e) => {
      e.preventDefault();
      setIdx(Math.max(0, clusterIdx - 1));
    },
    "cluster-next": (e) => {
      e.preventDefault();
      skipCluster();
    },
    "cluster-focus-left": (e) => {
      e.preventDefault();
      if (cluster) focusImage(Math.max(0, focusedImg - 1));
    },
    "cluster-focus-right": (e) => {
      e.preventDefault();
      if (cluster) focusImage(Math.min(cluster.images.length - 1, focusedImg + 1));
    },
    "cluster-focus-down": (e) => {
      e.preventDefault();
      if (cluster) focusImage(Math.min(cluster.images.length - 1, focusedImg + cols));
    },
    "cluster-focus-up": (e) => {
      e.preventDefault();
      if (cluster) focusImage(Math.max(0, focusedImg - cols));
    },
    "cluster-toggle": (e) => {
      e.preventDefault();
      const img = cluster?.images[focusedImg];
      if (img) toggle(img.path);
    },
    "cluster-keep-only": (e) => {
      e.preventDefault();
      const img = cluster?.images[focusedImg];
      if (img) keepOnly(img.path);
    },
    "cluster-toggle-rank": (e) => {
      const img = cluster?.images.find((img) => img.rank === parseInt(e.key));
      if (img) {
        e.preventDefault();
        toggle(img.path);
      }
    },
    "cluster-unreviewed": (e) => {
      e.preventDefault();
      jumpToUnreviewed();
    },
    "cluster-confirm": (e) => {
      e.preventDefault();
      if (!submitting) confirm();
    },
    "cluster-star": (e) => {
      e.preventDefault();
      if (cluster?.images[focusedImg]) star(cluster.images[focusedImg].path);
    },
    "cluster-tag": (e) => {
      e.preventDefault();
      const img = cluster?.images[focusedImg];
      if (!img || tagBusy) return;
      setKeeps((prev) => ({ ...prev, [img.path]: true }));
      assignTag(img.path, nextTag(videoTags.assignments[img.path] ?? null));
    },
    "cluster-undo": (e) => {
      e.preventDefault();
      onUndo().catch((err) => onError(err instanceof Error ? err.message : String(err)));
    },
  }, { ignore: typingTarget, enabled: !!cluster });

  if (!cluster) {
    return <p className="text-sm text-gray-500 px-1">No clusters remaining.</p>;
  }

  const focused = cluster.images[Math.min(focusedImg, cluster.images.length - 1)];
  const focusedTag = focused ? videoTags.assignments[focused.path] ?? null : null;
  const bestScore = Math.max(...cluster.images.map((img) => img.score));

  const controls = (
    <>
      {focused && (
        <TagBar
          tags={videoTags.tags}
          currentTag={focusedTag}
          isFavorited={favSet.has(focused.path)}
          busy={tagBusy}
          flash={tagFlash}
          showSlotKeys={false}
          untaggedLabel="no tag"
          untaggedTitle="No tag → trip root"
          emptyBadgeTitle="Exports to trip root"
          onAssign={(tag) => {
            if (tag !== null) setKeeps((prev) => ({ ...prev, [focused.path]: true }));
            assignTag(focused.path, tag);
          }}
        />
      )}
      <div className="flex items-center gap-0.5 shrink-0" role="group" aria-label="Photos per row">
        {[2, 3, 4].map((n) => (
          <button
            key={n}
            onClick={(e) => { e.currentTarget.blur(); setColsOverride(n); }}
            aria-pressed={cols === n}
            aria-label={`${n} photos per row`}
            title={`${n} photos per row`}
            className={`w-6 h-6 text-xs rounded ${
              cols === n ? "bg-blue-100 text-blue-700 font-medium" : "text-gray-400 hover:bg-gray-100"
            }`}
          >
            {n}
          </button>
        ))}
      </div>
      <button
        onClick={(e) => { e.currentTarget.blur(); jumpToUnreviewed(); }}
        disabled={undecidedCount === 0}
        aria-label="Next unreviewed"
        title={`Jump to the next cluster with no decision (n) — ${undecidedCount} left`}
        className="flex items-center gap-1 px-1.5 py-0.5 text-xs border border-gray-200 rounded text-gray-600 hover:bg-gray-50 disabled:opacity-40 shrink-0"
      >
        <Icon name="unreviewed" className="w-3.5 h-3.5" />
        <span className="tabular-nums">{undecidedCount}</span>
      </button>
    </>
  );

  // Position and size are no longer on screen; the attributes keep them
  // addressable for the UI tests without spending header width on them.
  return (
    <div data-testid="cluster-view" data-index={clusterIdx} data-size={cluster.images.length}>
      {controlsEl && createPortal(controls, controlsEl)}

      {/* Image grid */}
      <div
        className="grid gap-2"
        style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}
      >
        {cluster.images.map((img, i) => {
          const isKept = isKeptOf(img.path, img.rank);
          const delta = img.score - bestScore;
          const focused = i === focusedImg;
          const isFav = favSet.has(img.path);
          return (
            <div
              key={img.path}
              ref={(el) => { imgRefs.current[i] = el; }}
              className={`bg-white rounded overflow-hidden border-2 transition-colors ${
                focused ? "border-blue-400" : isFav ? "border-yellow-400" : "border-transparent"
              }`}
            >
              <div className={`h-1 ${isKept ? "bg-green-500" : "bg-red-400"}`} />
              <div
                className="relative cursor-pointer"
                onClick={() => { setFocusedImg(i); toggle(img.path); }}
              >
                <span className="absolute top-1.5 left-1.5 z-10 bg-black/60 text-white text-xs px-1.5 py-0.5 rounded-full">
                  <span className="font-bold">{img.rank}</span> · {img.score.toFixed(2)}{delta !== 0 && ` (Δ${delta.toFixed(2)})`}
                </span>
                {isFav && (
                  <span className="absolute top-1.5 right-1.5 z-10 text-yellow-400 text-base leading-none drop-shadow">★</span>
                )}
                <span
                  className="absolute bottom-1.5 left-1.5 z-10 bg-black/60 text-white text-xs px-1.5 py-0.5 rounded-full max-w-[70%] truncate"
                  title={img.model ? `${img.path} — ${img.model}` : img.path}
                >
                  {deviceOf(img.path, folder) && (
                    <span className="text-gray-300">{deviceOf(img.path, folder)} · </span>
                  )}
                  {img.path.split("/").pop()}
                </span>
                <img
                  src={imgUrl(img.path)}
                  onLoad={(e) => notePortrait(img.path, e.currentTarget)}
                  alt=""
                  className="w-full object-contain bg-gray-50 max-h-[calc(100vh-5.5rem)]"
                  loading="lazy"
                  decoding="async"
                />
                {img.motion && (
                  <LiveMotion motion={img.motion} fit="contain" corner="bottom-1.5 right-1.5" />
                )}
              </div>
              <button
                onClick={() => { setFocusedImg(i); toggle(img.path); }}
                className={`w-full py-1 text-sm font-medium transition-colors ${
                  isKept
                    ? "bg-green-50 text-green-700 hover:bg-green-100"
                    : "bg-red-50 text-red-700 hover:bg-red-100"
                }`}
              >
                {isKept ? "✓ Keep" : "✕ Delete"}
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}
