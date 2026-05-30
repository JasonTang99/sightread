import { useEffect, useRef, useState } from "react";
import type { Cluster } from "../types";

interface Props {
  singletons: Cluster[];
  threshold: number;
  onRefresh: () => Promise<void>;
  onError: (msg: string) => void;
}

interface FlatImage {
  cluster_id: number;
  path: string;
  score: number;
}

export function SingletonsView({ singletons, threshold: defaultThreshold, onRefresh, onError }: Props) {
  const [idx, setIdx] = useState(0);
  const [keeps, setKeeps] = useState<Record<string, boolean>>({});
  const [threshold, setThreshold] = useState(defaultThreshold);
  const [submitting, setSubmitting] = useState(false);

  const items: FlatImage[] = singletons
    .map((c) => ({ cluster_id: c.cluster_id, ...c.images[0] }))
    .sort((a, b) => a.score - b.score);

  const itemsRef = useRef<FlatImage[]>([]);
  const keepsRef = useRef(keeps);
  const idxRef = useRef(idx);
  const submittingRef = useRef(submitting);
  const thresholdRef = useRef(threshold);
  const onRefreshRef = useRef(onRefresh);
  const onErrorRef = useRef(onError);

  itemsRef.current = items;
  keepsRef.current = keeps;
  idxRef.current = idx;
  submittingRef.current = submitting;
  thresholdRef.current = threshold;
  onRefreshRef.current = onRefresh;
  onErrorRef.current = onError;

  useEffect(() => {
    const init: Record<string, boolean> = {};
    for (const it of items) init[it.path] = it.score >= defaultThreshold;
    setKeeps(init);
    setIdx(0);
  }, [singletons.length]);

  const handleThresholdChange = (t: number) => {
    setThreshold(t);
    const next: Record<string, boolean> = {};
    for (const it of items) next[it.path] = it.score >= t;
    setKeeps(next);
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement || e.target instanceof HTMLTextAreaElement) return;
      switch (e.key) {
        case "j":
        case "ArrowDown":
          e.preventDefault();
          setIdx((i) => Math.min(itemsRef.current.length - 1, i + 1));
          break;
        case "k":
        case "ArrowUp":
          e.preventDefault();
          setIdx((i) => Math.max(0, i - 1));
          break;
        case " ": {
          e.preventDefault();
          const it = itemsRef.current[idxRef.current];
          if (it) setKeeps((prev) => ({ ...prev, [it.path]: !prev[it.path] }));
          break;
        }
        case "Enter":
          e.preventDefault();
          if (!submittingRef.current) {
            const its = itemsRef.current;
            const deletePaths = its.filter((it) => !keepsRef.current[it.path]).map((it) => it.path);
            const allPaths = its.map((it) => it.path);
            setSubmitting(true);
            fetch("/api/confirm", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ delete_paths: deletePaths, all_paths: allPaths }),
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
  }, []);

  const confirm = async () => {
    setSubmitting(true);
    try {
      const deletePaths = items.filter((it) => !keeps[it.path]).map((it) => it.path);
      const allPaths = items.map((it) => it.path);
      const res = await fetch("/api/confirm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ delete_paths: deletePaths, all_paths: allPaths }),
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
  const isKept = keeps[current.path] ?? current.score >= threshold;
  const nDelete = items.filter((it) => !(keeps[it.path] ?? it.score >= threshold)).length;

  return (
    <div className="-mx-2 -mt-2 flex flex-col" style={{ height: "calc(100vh - 2.25rem)" }}>
      {/* Toolbar */}
      <div className="bg-white border-b border-gray-200 px-3 py-1.5 flex items-center gap-3 shrink-0">
        <span className="text-xs text-gray-500 tabular-nums">{Math.min(idx, items.length - 1) + 1} / {items.length}</span>
        <span className="text-xs font-mono text-gray-600">{current.score.toFixed(2)}</span>
        <span className={`text-xs font-medium px-2 py-0.5 rounded ${isKept ? "bg-green-100 text-green-700" : "bg-red-100 text-red-700"}`}>
          {isKept ? "Keep" : "Delete"}
        </span>
        <div className="flex items-center gap-2">
          <span className="text-xs text-gray-400">threshold</span>
          <input
            type="range" min={0} max={1} step={0.01} value={threshold}
            onChange={(e) => handleThresholdChange(parseFloat(e.target.value))}
            className="w-24 accent-blue-600"
          />
          <span className="text-xs font-mono text-gray-600 w-8">{threshold.toFixed(2)}</span>
        </div>
        <span className="text-xs text-gray-300">j/k · space toggle · enter confirm</span>
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
        className={`flex-1 flex items-center justify-center bg-gray-50 cursor-pointer border-4 transition-colors ${
          isKept ? "border-green-400" : "border-red-400"
        }`}
        onClick={() => setKeeps((prev) => ({ ...prev, [current.path]: !prev[current.path] }))}
      >
        <img
          key={current.path}
          src={`/api/image?path=${encodeURIComponent(current.path)}&w=1600`}
          alt=""
          className="max-w-full max-h-full object-contain"
        />
      </div>
    </div>
  );
}
