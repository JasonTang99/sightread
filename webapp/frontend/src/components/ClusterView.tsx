import { useEffect, useRef, useState } from "react";
import { useWindowKeydown } from "../hooks/useWindowKeydown";
import type { Cluster, ClusterDecision } from "../types";

interface Props {
  clusters: Cluster[];
  clusterDecisions: Record<string, ClusterDecision>;
  favorites: string[];
  onRefresh: () => Promise<void>;
  onError: (msg: string) => void;
  onUndo: () => Promise<void>;
  onToggleFavorite: (path: string) => Promise<void>;
}

function imgUrl(path: string, w = 2400) {
  return `/api/image?path=${encodeURIComponent(path)}&w=${w}`;
}

export function ClusterView({ clusters, clusterDecisions, favorites, onRefresh, onError, onUndo, onToggleFavorite }: Props) {
  const [idx, setIdx] = useState(() => {
    const first = clusters.findIndex((c) => !(String(c.cluster_id) in clusterDecisions));
    return first === -1 ? 0 : first;
  });
  const [keeps, setKeeps] = useState<Record<string, boolean>>({});
  const [cols, setCols] = useState(2);
  const [submitting, setSubmitting] = useState(false);
  const [focusedImg, setFocusedImg] = useState(0);
  const imgRefs = useRef<(HTMLDivElement | null)[]>([]);
  const keepsByClusterRef = useRef<Record<number, Record<string, boolean>>>({});

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
      const decision = clusterDecisions[String(clusterId)];
      const init: Record<string, boolean> = {};
      if (decision) {
        const deletedSet = new Set(decision.deleted);
        for (const img of cluster.images) init[img.path] = !deletedSet.has(img.path);
      } else {
        for (const img of cluster.images) init[img.path] = img.rank === 1;
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

  const toggle = (path: string) => setKeeps((prev) => ({ ...prev, [path]: !prev[path] }));

  const keepBest = () => {
    const next: Record<string, boolean> = {};
    for (const img of cluster.images) next[img.path] = img.rank === 1;
    setKeeps(next);
  };

  const confirm = async () => {
    setSubmitting(true);
    try {
      const deletePaths = cluster.images
        .filter((img) => !(keeps[img.path] ?? img.rank === 1))
        .map((img) => img.path);
      const res = await fetch("/api/confirm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          cluster_id: cluster.cluster_id,
          delete_paths: deletePaths,
          all_paths: cluster.images.map((img) => img.path),
        }),
      });
      if (!res.ok) throw new Error(`Confirm failed: ${res.status}`);
      setIdx((i) => i + 1);
      await onRefresh();
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  };

  useWindowKeydown((e) => {
    if (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement || e.target instanceof HTMLTextAreaElement) return;
    if (!cluster) return;

    const focusImage = (next: number) => {
      imgRefs.current[next]?.scrollIntoView({ behavior: "smooth", block: "nearest" });
      setFocusedImg(next);
    };

    // 1–9: toggle image by rank
    if (/^[1-9]$/.test(e.key)) {
      const img = cluster.images.find((img) => img.rank === parseInt(e.key));
      if (img) {
        e.preventDefault();
        toggle(img.path);
      }
      return;
    }

    switch (e.key) {
      case "ArrowLeft":
        e.preventDefault();
        setIdx(Math.max(0, clusterIdx - 1));
        break;
      case "ArrowRight":
      case "b":
        e.preventDefault();
        setIdx(Math.min(clusters.length - 1, clusterIdx + 1));
        break;
      case "s":
        e.preventDefault();
        if (cluster.images[focusedImg]) {
          onToggleFavorite(cluster.images[focusedImg].path).catch((err) =>
            onError(err instanceof Error ? err.message : String(err))
          );
        }
        break;
      case "h":
        e.preventDefault();
        focusImage(Math.max(0, focusedImg - 1));
        break;
      case "l":
        e.preventDefault();
        focusImage(Math.min(cluster.images.length - 1, focusedImg + 1));
        break;
      case "j":
        e.preventDefault();
        focusImage(Math.min(cluster.images.length - 1, focusedImg + cols));
        break;
      case "k":
        e.preventDefault();
        focusImage(Math.max(0, focusedImg - cols));
        break;
      case " ":
        e.preventDefault();
        if (cluster.images[focusedImg]) toggle(cluster.images[focusedImg].path);
        break;
      case "K":
        e.preventDefault();
        keepBest();
        break;
      case "u":
        e.preventDefault();
        onUndo().catch((err) => onError(err instanceof Error ? err.message : String(err)));
        break;
      case "Enter":
        e.preventDefault();
        if (!submitting) confirm();
        break;
    }
  });

  if (!cluster) return <p className="text-sm text-gray-500">No clusters remaining.</p>;

  const bestScore = Math.max(...cluster.images.map((img) => img.score));
  const nKeep = cluster.images.filter((img) => keeps[img.path] ?? img.rank === 1).length;
  const nDelete = cluster.images.length - nKeep;

  return (
    <div className="space-y-2">
      {/* Combined bar */}
      <div className="bg-white border border-gray-200 rounded px-3 py-2 flex items-center gap-3 flex-wrap">
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
          {clusters.map((c, i) => (
            <option key={c.cluster_id} value={i}>
              {i + 1}/{clusters.length} — {c.images.length} imgs
            </option>
          ))}
        </select>
        <button
          onClick={() => setIdx(Math.min(clusters.length - 1, clusterIdx + 1))}
          disabled={clusterIdx >= clusters.length - 1}
          className="px-2 py-1 text-sm border border-gray-200 rounded text-gray-600 hover:bg-gray-50 disabled:opacity-40"
        >
          →
        </button>

        <div className="w-px h-4 bg-gray-200" />

        <span className="text-xs text-gray-500">
          {nDelete > 0
            ? <>keep <strong>{nKeep}</strong> · trash <strong className="text-red-500">{nDelete}</strong></>
            : <>keep all <strong>{nKeep}</strong></>}
        </span>

        <span className="text-xs text-gray-300">hjkl · space · 1–9 · K best · enter · ←/→ clusters · b skip · s star · u undo · ? help</span>

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
          <div className="w-px h-4 bg-gray-200" />
          <button onClick={keepBest} className="px-2 py-1 text-xs border border-gray-200 rounded text-gray-600 hover:bg-gray-50">
            🏆 Best
          </button>
          <button
            onClick={() => setIdx(Math.min(clusters.length - 1, clusterIdx + 1))}
            disabled={clusterIdx >= clusters.length - 1}
            className="px-2 py-1 text-xs border border-gray-200 rounded text-gray-600 hover:bg-gray-50 disabled:opacity-40"
          >
            Skip
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
      <div className="w-full bg-gray-100 rounded-full h-0.5">
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
          const isKept = keeps[img.path] ?? img.rank === 1;
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
                  title={img.path}
                >
                  {img.path.split("/").pop()}
                </span>
                <img
                  src={imgUrl(img.path)}
                  alt=""
                  className="w-full object-contain bg-gray-50 max-h-[calc(100vh-9rem)]"
                  loading="lazy"
                />
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
