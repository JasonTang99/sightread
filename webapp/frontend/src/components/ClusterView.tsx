import { useEffect, useRef, useState } from "react";
import type { Cluster } from "../types";

interface Props {
  clusters: Cluster[];
  onRefresh: () => Promise<void>;
  onError: (msg: string) => void;
  onUndo: () => Promise<void>;
}

function imgUrl(path: string, w = 1200) {
  return `/api/image?path=${encodeURIComponent(path)}&w=${w}`;
}

export function ClusterView({ clusters, onRefresh, onError, onUndo }: Props) {
  const [idx, setIdx] = useState(0);
  const [keeps, setKeeps] = useState<Record<string, boolean>>({});
  const [cols, setCols] = useState(2);
  const [submitting, setSubmitting] = useState(false);
  const [focusedImg, setFocusedImg] = useState(0);
  const imgRefs = useRef<(HTMLDivElement | null)[]>([]);
  // Mutable refs — keyboard handler reads these directly, never re-registers
  const clusterRef = useRef<typeof cluster | undefined>(undefined);
  const clustersLenRef = useRef(clusters.length);
  const keepsRef = useRef(keeps);
  const submittingRef = useRef(submitting);
  const focusedImgRef = useRef(focusedImg);
  const onRefreshRef = useRef(onRefresh);
  const onErrorRef = useRef(onError);
  const onUndoRef = useRef(onUndo);

  const clusterIdx = Math.min(idx, clusters.length - 1);
  const cluster = clusters[clusterIdx];

  // Update refs every render
  clusterRef.current = cluster;
  clustersLenRef.current = clusters.length;
  keepsRef.current = keeps;
  submittingRef.current = submitting;
  focusedImgRef.current = focusedImg;
  onRefreshRef.current = onRefresh;
  onErrorRef.current = onError;
  onUndoRef.current = onUndo;

  useEffect(() => {
    if (!cluster) return;
    const init: Record<string, boolean> = {};
    for (const img of cluster.images) init[img.path] = img.rank === 1;
    setKeeps(init);
    setFocusedImg(0);
  }, [cluster?.cluster_id]);

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
      const deletePaths = cluster.images.filter((img) => !keeps[img.path]).map((img) => img.path);
      const res = await fetch("/api/confirm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ delete_paths: deletePaths }),
      });
      if (!res.ok) throw new Error(`Confirm failed: ${res.status}`);
      await onRefresh();
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  };

  // Register once on mount — reads all state via refs
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement || e.target instanceof HTMLTextAreaElement) return;
      const c = clusterRef.current;
      if (!c) return;

      // 1–9: toggle image by rank
      if (/^[1-9]$/.test(e.key)) {
        const rankIdx = parseInt(e.key) - 1;
        const img = c.images.find((img) => img.rank === rankIdx + 1);
        if (img) {
          e.preventDefault();
          setKeeps((prev) => ({ ...prev, [img.path]: !prev[img.path] }));
        }
        return;
      }

      switch (e.key) {
        case "h":
          e.preventDefault();
          setIdx((i) => Math.max(0, Math.min(i, clustersLenRef.current - 1) - 1));
          break;
        case "l":
        case "b":
        case "s":
          e.preventDefault();
          setIdx((i) => Math.min(clustersLenRef.current - 1, Math.min(i, clustersLenRef.current - 1) + 1));
          break;
        case "j":
          e.preventDefault();
          setFocusedImg((f) => {
            const next = Math.min(c.images.length - 1, f + 1);
            imgRefs.current[next]?.scrollIntoView({ behavior: "smooth", block: "nearest" });
            return next;
          });
          break;
        case "k":
          e.preventDefault();
          setFocusedImg((f) => {
            const next = Math.max(0, f - 1);
            imgRefs.current[next]?.scrollIntoView({ behavior: "smooth", block: "nearest" });
            return next;
          });
          break;
        case " ":
          e.preventDefault();
          if (c.images[focusedImgRef.current]) {
            const path = c.images[focusedImgRef.current].path;
            setKeeps((prev) => ({ ...prev, [path]: !prev[path] }));
          }
          break;
        case "K":
          e.preventDefault();
          setKeeps(() => {
            const next: Record<string, boolean> = {};
            for (const img of c.images) next[img.path] = img.rank === 1;
            return next;
          });
          break;
        case "u":
          e.preventDefault();
          onUndoRef.current().catch((err) => onErrorRef.current(err instanceof Error ? err.message : String(err)));
          break;
        case "Enter":
          e.preventDefault();
          if (!submittingRef.current) {
            const deletePaths = c.images.filter((img) => !keepsRef.current[img.path]).map((img) => img.path);
            setSubmitting(true);
            fetch("/api/confirm", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ delete_paths: deletePaths }),
            })
              .then((res) => { if (!res.ok) throw new Error(`Confirm failed: ${res.status}`); return onRefreshRef.current(); })
              .catch((err) => onErrorRef.current(err instanceof Error ? err.message : String(err)))
              .finally(() => setSubmitting(false));
          }
          break;
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []); // mount/unmount only

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

        <span className="text-xs text-gray-300">h/l · j/k · space · 1–9 · K best · enter · b skip · u undo · ? help</span>

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
          return (
            <div
              key={img.path}
              ref={(el) => { imgRefs.current[i] = el; }}
              className={`bg-white rounded overflow-hidden border-2 transition-colors ${
                focused ? "border-blue-400" : "border-transparent"
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
                <img
                  src={imgUrl(img.path)}
                  alt=""
                  className="w-full object-contain bg-gray-50"
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
