import { useEffect, useState } from "react";
import { useWindowKeydown } from "../hooks/useWindowKeydown";
import type { Cluster } from "../types";

interface Props {
  singletons: Cluster[];
  clusterDecisions: Record<string, unknown>;
  favorites: string[];
  onRefresh: () => Promise<void>;
  onError: (msg: string) => void;
  onToggleFavorite: (path: string) => Promise<void>;
}

interface FlatImage {
  cluster_id: number;
  path: string;
  score: number;
}

export function SingletonsView({ singletons, clusterDecisions, favorites, onRefresh, onError, onToggleFavorite }: Props) {
  const [idx, setIdx] = useState(0);
  const [keeps, setKeeps] = useState<Record<string, boolean>>({});
  const [submitting, setSubmitting] = useState(false);

  const items: FlatImage[] = singletons
    .map((c) => ({ cluster_id: c.cluster_id, ...c.images[0] }))
    .sort((a, b) => a.score - b.score);

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
    const firstUnconfirmed = items.findIndex((it) => !(String(it.cluster_id) in clusterDecisions));
    setIdx(firstUnconfirmed >= 0 ? firstUnconfirmed : 0);
  }, [singletons.length]);

  const confirmCurrent = () => {
    const it = items[idx];
    if (!it) return;
    const keep = keeps[it.path] ?? false;
    fetch("/api/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        delete_paths: keep ? [] : [it.path],
        all_paths: [it.path],
        singleton_decisions: [{ cluster_id: it.cluster_id, kept: keep ? [it.path] : [], deleted: keep ? [] : [it.path] }],
      }),
    })
      .then((r) => { if (!r.ok) throw new Error(`Confirm failed: ${r.status}`); })
      .catch((err) => onError(err instanceof Error ? err.message : String(err)));
    setIdx((i) => Math.min(items.length - 1, i + 1));
  };

  useWindowKeydown((e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      confirmCurrent();
      return;
    }
    if (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement || e.target instanceof HTMLTextAreaElement) return;
    switch (e.key) {
      case "j":
      case "ArrowDown":
        e.preventDefault();
        setIdx((i) => Math.min(items.length - 1, i + 1));
        break;
      case "k":
      case "ArrowUp":
        e.preventDefault();
        setIdx((i) => Math.max(0, i - 1));
        break;
      case " ": {
        e.preventDefault();
        const it = items[idx];
        if (it) setKeeps((prev) => ({ ...prev, [it.path]: !prev[it.path] }));
        break;
      }
      case "s": {
        e.preventDefault();
        const it = items[idx];
        if (it) {
          onToggleFavorite(it.path).catch((err) =>
            onError(err instanceof Error ? err.message : String(err))
          );
        }
        break;
      }
    }
  });

  const confirm = async () => {
    setSubmitting(true);
    try {
      const isKeep = (it: FlatImage) => keeps[it.path] ?? false;
      const deletePaths = items.filter((it) => !isKeep(it)).map((it) => it.path);
      const allPaths = items.map((it) => it.path);
      const singletonDecisions = items.map((it) => ({
        cluster_id: it.cluster_id,
        kept: isKeep(it) ? [it.path] : [],
        deleted: isKeep(it) ? [] : [it.path],
      }));
      const res = await fetch("/api/confirm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ delete_paths: deletePaths, all_paths: allPaths, singleton_decisions: singletonDecisions }),
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
  const isKept = keeps[current.path] ?? false;
  const isConfirmed = String(current.cluster_id) in clusterDecisions;
  const nDelete = items.filter((it) => !(keeps[it.path] ?? false)).length;
  const favSet = new Set(favorites);
  const isFav = favSet.has(current.path);

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
        <span className="text-xs text-gray-300">j/k move · space toggle · s star · enter confirm+next</span>
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
        onClick={() => setKeeps((prev) => ({ ...prev, [current.path]: !prev[current.path] }))}
      >
        <img
          key={current.path}
          src={`/api/image?path=${encodeURIComponent(current.path)}&w=2400`}
          alt=""
          className="max-w-full max-h-full object-contain"
        />
      </div>
    </div>
  );
}
