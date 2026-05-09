import { useEffect, useRef, useState } from "react";
import type { Cluster } from "../types";

interface Props {
  singletons: Cluster[];
  threshold: number;
  onRefresh: () => Promise<void>;
  onError: (msg: string) => void;
}

function imgUrl(path: string, w = 400) {
  return `/api/image?path=${encodeURIComponent(path)}&w=${w}`;
}

interface FlatImage {
  cluster_id: number;
  path: string;
  score: number;
}

export function SingletonsView({ singletons, threshold: defaultThreshold, onRefresh, onError }: Props) {
  const [keeps, setKeeps] = useState<Record<string, boolean>>({});
  const [cols, setCols] = useState(4);
  const [submitting, setSubmitting] = useState(false);
  const [threshold, setThreshold] = useState(defaultThreshold);
  const [focusedImg, setFocusedImg] = useState(0);
  const imgRefs = useRef<(HTMLDivElement | null)[]>([]);
  // Mutable refs for keyboard handler
  const itemsRef = useRef<FlatImage[]>([]);
  const keepsRef = useRef(keeps);
  const submittingRef = useRef(submitting);
  const focusedImgRef = useRef(focusedImg);
  const thresholdRef = useRef(threshold);
  const onRefreshRef = useRef(onRefresh);
  const onErrorRef = useRef(onError);

  const items: FlatImage[] = singletons
    .map((c) => ({ cluster_id: c.cluster_id, ...c.images[0] }))
    .sort((a, b) => a.score - b.score);

  // Update refs every render
  itemsRef.current = items;
  keepsRef.current = keeps;
  submittingRef.current = submitting;
  focusedImgRef.current = focusedImg;
  thresholdRef.current = threshold;
  onRefreshRef.current = onRefresh;
  onErrorRef.current = onError;

  useEffect(() => {
    const init: Record<string, boolean> = {};
    for (const it of items) init[it.path] = it.score >= threshold;
    setKeeps(init);
    setFocusedImg(0);
  }, [singletons.length]);

  const toggle = (path: string) => setKeeps((prev) => ({ ...prev, [path]: !prev[path] }));

  const applyThreshold = (t: number) => {
    const next: Record<string, boolean> = {};
    for (const it of items) next[it.path] = it.score >= t;
    setKeeps(next);
  };

  const handleThresholdChange = (t: number) => {
    setThreshold(t);
    applyThreshold(t);
  };

  const selectAllBad = () => {
    const next: Record<string, boolean> = {};
    for (const it of items) next[it.path] = it.score >= threshold;
    setKeeps(next);
  };

  const keepAll = () => {
    const next: Record<string, boolean> = {};
    for (const it of items) next[it.path] = true;
    setKeeps(next);
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement || e.target instanceof HTMLTextAreaElement) return;
      const its = itemsRef.current;
      switch (e.key) {
        case "j":
          e.preventDefault();
          setFocusedImg((f) => {
            const next = Math.min(its.length - 1, f + 1);
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
          if (its[focusedImgRef.current]) {
            const path = its[focusedImgRef.current].path;
            setKeeps((prev) => ({ ...prev, [path]: !prev[path] }));
          }
          break;
        case "a":
          e.preventDefault();
          setKeeps(() => {
            const next: Record<string, boolean> = {};
            for (const it of itemsRef.current) next[it.path] = true;
            return next;
          });
          break;
        case "d":
          e.preventDefault();
          setKeeps(() => {
            const next: Record<string, boolean> = {};
            const t = thresholdRef.current;
            for (const it of itemsRef.current) next[it.path] = it.score >= t;
            return next;
          });
          break;
        case "Enter":
          e.preventDefault();
          if (!submittingRef.current) {
            const its2 = itemsRef.current;
            const deletePaths = its2.filter((it) => !keepsRef.current[it.path]).map((it) => it.path);
            const allPaths = its2.map((it) => it.path);
            submittingRef.current = true;
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

  const borderlineHi = threshold + 0.1;
  const bands = [
    { label: "Bad — delete", color: "text-red-600", items: items.filter((it) => it.score < threshold) },
    { label: "Borderline", color: "text-yellow-600", items: items.filter((it) => it.score >= threshold && it.score < borderlineHi) },
    { label: "Good — keep", color: "text-green-600", items: items.filter((it) => it.score >= borderlineHi) },
  ];

  const nKeep = items.filter((it) => keeps[it.path]).length;
  const nDelete = items.length - nKeep;

  return (
    <div className="space-y-4">
      {/* Toolbar */}
      <div className="bg-white rounded-lg border border-gray-200 px-4 py-3 flex items-center justify-between gap-4">
        <div className="flex items-center gap-2">
          <button onClick={selectAllBad} className="px-3 py-1.5 text-sm border border-gray-200 rounded text-gray-600 hover:bg-gray-50">
            🗑️ Select All Bad
          </button>
          <button onClick={keepAll} className="px-3 py-1.5 text-sm border border-gray-200 rounded text-gray-600 hover:bg-gray-50">
            ✅ Keep All
          </button>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2">
            <span className="text-xs text-gray-400">threshold</span>
            <input
              type="range"
              min={0}
              max={1}
              step={0.01}
              value={threshold}
              onChange={(e) => handleThresholdChange(parseFloat(e.target.value))}
              className="w-28 accent-blue-600"
            />
            <span className="text-xs font-mono text-gray-600 w-8">{threshold.toFixed(2)}</span>
          </div>
          <div className="flex items-center gap-1">
            <span className="text-xs text-gray-400 mr-1">cols</span>
            {[3, 4, 5, 6].map((n) => (
              <button
                key={n}
                onClick={() => setCols(n)}
                className={`w-6 h-6 text-xs rounded ${cols === n ? "bg-blue-100 text-blue-700 font-medium" : "text-gray-400 hover:bg-gray-100"}`}
              >
                {n}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Banded grid */}
      {bands.map(({ label, color, items: bandItems }) => {
        if (bandItems.length === 0) return null;
        return (
          <div key={label} className="space-y-2">
            <h3 className={`text-sm font-medium ${color}`}>{label} — {bandItems.length}</h3>
            <div className="grid gap-2" style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}>
              {bandItems.map((it) => {
                const isKept = keeps[it.path] ?? it.score >= threshold;
                const flatIdx = items.findIndex((x) => x.path === it.path);
                const focused = flatIdx === focusedImg;
                return (
                  <div
                    key={it.path}
                    ref={(el) => { imgRefs.current[flatIdx] = el; }}
                    className={`bg-white rounded-lg overflow-hidden border-2 transition-colors ${focused ? "border-blue-400" : "border-transparent"}`}
                  >
                    <div className={`h-0.5 ${isKept ? "bg-green-500" : "bg-red-400"}`} />
                    <div className="relative cursor-pointer" onClick={() => { setFocusedImg(flatIdx); toggle(it.path); }}>
                      <span className="absolute top-1.5 left-1.5 z-10 bg-black/60 text-white text-xs px-1.5 py-0.5 rounded-full">
                        {it.score.toFixed(2)}
                      </span>
                      <img
                        src={imgUrl(it.path)}
                        alt=""
                        className="w-full object-contain max-h-40 bg-gray-50"
                        loading="lazy"
                      />
                    </div>
                    <div className="p-1.5">
                      <button
                        onClick={() => { setFocusedImg(flatIdx); toggle(it.path); }}
                        className={`w-full py-1 text-xs font-medium rounded border transition-colors ${
                          isKept
                            ? "bg-green-50 text-green-700 border-green-200 hover:bg-green-100"
                            : "bg-red-50 text-red-700 border-red-200 hover:bg-red-100"
                        }`}
                      >
                        {isKept ? "✓ Keep" : "✕ Delete"}
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}

      {/* Action bar */}
      <div className="bg-white rounded-lg border border-gray-200 px-4 py-3 flex items-center justify-between">
        <p className="text-sm text-gray-600">
          {nDelete > 0 ? (
            <>
              Keeping <strong>{nKeep}</strong> of <strong>{items.length}</strong> — <strong className="text-red-600">{nDelete}</strong> → trash
            </>
          ) : (
            <>Keeping all <strong>{items.length}</strong></>
          )}
        </p>
        <button
          onClick={confirm}
          disabled={submitting}
          className="px-4 py-1.5 text-sm font-medium bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
        >
          {submitting ? "Saving…" : "✓ Confirm Singles"}
        </button>
      </div>
    </div>
  );
}
