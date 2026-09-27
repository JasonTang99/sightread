import { useEffect, useMemo, useRef, useState } from "react";
import { useShortcuts } from "../hooks/useWindowKeydown";
import { useMediaTags } from "../hooks/useMediaTags";
import { isDecided, isDoomed, isWiped, nextAfterConfirm } from "../decisions";
import { TagBar } from "./TagBar";
import { LiveMotion } from "./LiveMotion";
import { deviceOf } from "../device";
import type { Cluster, PhotoDecisions, VideoTagsState } from "../types";
import { Divider, ShortcutBar } from "./ui";
import { CLUSTER_KEYS, brief, typingTarget } from "../shortcuts";

type ClusterFilter = "all" | "wiped";

interface Props {
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

// A cluster's size, as colour. The thresholds come from the archive: most
// clusters are a pair or a trio, anything past about eight is a burst — the
// Hoh trip's 44-photo viewpoint and Hawaii's 47-frame pineapple sequence are
// the shape this is warning about.
function sizeClass(n: number): string {
  if (n >= 20) return "bg-red-100 text-red-800 ring-1 ring-red-300";
  if (n >= 8) return "bg-orange-100 text-orange-800";
  if (n >= 4) return "bg-amber-100 text-amber-800";
  return "bg-blue-50 text-blue-700";
}

function imgUrl(path: string, w = 2400) {
  return `/api/image?path=${encodeURIComponent(path)}&w=${w}`;
}

export function ClusterView({ folder, clusters: allClusters, decisions, skipReviewed = false, favorites, onRefresh, onError, onUndo, onToggleFavorite, onAdvance, videoTags = { tags: [], assignments: {} }, onVideoTagsChange }: Props) {
  const [filter, setFilter] = useState<ClusterFilter>("all");
  const [idx, setIdx] = useState(() => {
    const first = allClusters.findIndex((c) => !isDecided(c, decisions));
    return first === -1 ? 0 : first;
  });
  const [keeps, setKeeps] = useState<Record<string, boolean>>({});
  const [cols, setCols] = useState(2);
  const [submitting, setSubmitting] = useState(false);
  const [focusedImg, setFocusedImg] = useState(0);
  const imgRefs = useRef<(HTMLDivElement | null)[]>([]);
  const keepsByClusterRef = useRef<Record<number, Record<string, boolean>>>({});
  // Where confirm lands, by id: the list is rebuilt when the refresh comes
  // back (and can change shape under the fully-deleted filter), so an index
  // taken before it may no longer point at the same cluster.
  const landOnRef = useRef<number | "stay" | undefined>(undefined);

  const wipedCount = useMemo(
    () => allClusters.filter((c) => isWiped(c, decisions)).length,
    [allClusters, decisions],
  );
  const clusters = useMemo(
    () => (filter === "wiped" ? allClusters.filter((c) => isWiped(c, decisions)) : allClusters),
    [allClusters, decisions, filter],
  );

  // Filtering rebuilds the list under the cursor; start over at the top.
  useEffect(() => { setIdx(0); }, [filter]);

  const clusterIdx = Math.min(idx, clusters.length - 1);
  const cluster = clusters[clusterIdx];
  const favSet = new Set(favorites);

  useEffect(() => {
    for (const c of clusters.slice(clusterIdx + 1, clusterIdx + 3)) {
      for (const img of c.images) {
        const el = new Image();
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

  const { assignTag, addTag, nextTag, busy: tagBusy, flash: tagFlash, clearFlash } = useMediaTags(
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
    () => allClusters.filter((c) => !isDecided(c, decisions)).length,
    [allClusters, decisions],
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

  const filterToggle = (
    <div className="flex rounded overflow-hidden border border-gray-200 text-xs">
      <button
        onClick={() => setFilter("all")}
        className={`px-2 py-1 transition-colors ${filter === "all" ? "bg-blue-600 text-white" : "text-gray-500 hover:bg-gray-50"}`}
      >
        All ({allClusters.length})
      </button>
      <button
        onClick={() => setFilter("wiped")}
        className={`px-2 py-1 transition-colors ${filter === "wiped" ? "bg-red-600 text-white" : "text-gray-500 hover:bg-gray-50"}`}
        title="Clusters where every image was marked for deletion"
      >
        Fully deleted ({wipedCount})
      </button>
    </div>
  );

  if (!cluster) {
    return (
      <div className="space-y-2">
        <div className="bg-white border border-gray-200 rounded px-3 py-2 flex items-center gap-3">
          {filterToggle}
        </div>
        <p className="text-sm text-gray-500 px-1">
          {filter === "wiped" ? "No clusters had every image deleted." : "No clusters remaining."}
        </p>
      </div>
    );
  }

  const bestScore = Math.max(...cluster.images.map((img) => img.score));
  const nKeep = cluster.images.filter((img) => isKeptOf(img.path, img.rank)).length;
  const nDelete = cluster.images.length - nKeep;
  const focused = cluster.images[Math.min(focusedImg, cluster.images.length - 1)];
  const focusedTag = focused ? videoTags.assignments[focused.path] ?? null : null;

  return (
    <div className="space-y-2">
      {/* Combined bar */}
      <div className="bg-white border border-gray-200 rounded px-3 py-2 flex items-center gap-3 flex-wrap">
        {filterToggle}
        <button
          onClick={() => setIdx(Math.max(0, clusterIdx - 1))}
          disabled={clusterIdx === 0}
          className="px-2 py-1 text-sm border border-gray-200 rounded text-gray-600 hover:bg-gray-50 disabled:opacity-40"
        >
          ←
        </button>
        <select
          value={clusterIdx}
          onChange={(e) => setIdx(Number(e.target.value))}
          className="px-2 py-1 border border-gray-200 rounded text-sm bg-white text-gray-700"
        >
          {/* Position only: the badge beside it already says how big the
              selected cluster is, and saying it twice just made the bar
              longer. */}
          {clusters.map((c, i) => (
            <option key={c.cluster_id} value={i}>
              {i + 1}/{clusters.length}
            </option>
          ))}
        </select>
        {/* How many photos you are deciding between. It was only legible
            inside the dropdown's own label, which is the one place you cannot
            read it while looking at the photos. Colour carries the size,
            because a 44-photo burst is a different job from a pair and you
            want to know which one you just landed on before you start. */}
        <span
          className={`px-2 py-1 rounded text-sm font-semibold whitespace-nowrap ${sizeClass(cluster.images.length)}`}
          title={`${cluster.images.length} photos in this cluster`}
          data-testid="cluster-size"
        >
          {cluster.images.length} photo{cluster.images.length === 1 ? "" : "s"}
        </span>
        <button
          onClick={() => setIdx(Math.min(clusters.length - 1, clusterIdx + 1))}
          disabled={clusterIdx >= clusters.length - 1}
          className="px-2 py-1 text-sm border border-gray-200 rounded text-gray-600 hover:bg-gray-50 disabled:opacity-40"
        >
          →
        </button>

        <Divider />

        <span className="text-xs text-gray-500">
          {nDelete > 0
            ? <>keep <strong>{nKeep}</strong> · trash <strong className="text-red-500">{nDelete}</strong></>
            : <>keep all <strong>{nKeep}</strong></>}
        </span>

        <span className="text-xs text-gray-500">
          {undecidedCount} of {allClusters.length} left
        </span>

        {focused && <Divider />}

        <ShortcutBar items={brief(CLUSTER_KEYS)} />
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
            onAdd={addTag}
          />
        )}

        <div className="ml-auto flex items-center gap-2">
          <div className="flex items-center gap-1">
            {[2, 3, 4].map((n) => (
              <button
                key={n}
                onClick={() => setCols(n)}
                className={`w-6 h-6 text-xs rounded ${
                  cols === n ? "bg-blue-100 text-blue-700 font-medium" : "text-gray-400 hover:bg-gray-100"
                }`}
              >
                {n}
              </button>
            ))}
          </div>
          <Divider />
          <button
            onClick={jumpToUnreviewed}
            disabled={undecidedCount === 0}
            className="px-2 py-1 text-xs border border-gray-200 rounded text-gray-600 hover:bg-gray-50 disabled:opacity-40"
            title="Jump to the next cluster with no decision (n)"
          >
            → Unreviewed
          </button>
          <button
            onClick={confirm}
            disabled={submitting}
            className="px-3 py-1 text-sm font-medium bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
          >
            {submitting ? "…" : "✓ Confirm"}
          </button>
        </div>
      </div>

      {/* Progress */}
      <div className="w-full bg-gray-100 rounded-full h-0.5" data-testid="cluster-progress">
        <div
          className="bg-blue-500 h-0.5 rounded-full transition-all duration-300"
          style={{ width: `${((clusterIdx + 1) / clusters.length) * 100}%` }}
        />
      </div>

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
                  alt=""
                  className="w-full object-contain bg-gray-50 max-h-[calc(100vh-9rem)]"
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
