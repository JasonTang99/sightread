import { useEffect, useRef, useState } from "react";

interface Props {
  videos: string[];
  onError: (msg: string) => void;
}

export function VideoView({ videos, onError }: Props) {
  const [idx, setIdx] = useState(0);
  const [keeps, setKeeps] = useState<Record<string, boolean>>({});
  const [submitting, setSubmitting] = useState(false);

  const idxRef = useRef(idx);
  const keepsRef = useRef(keeps);
  const submittingRef = useRef(submitting);
  const videosRef = useRef(videos);
  const onErrorRef = useRef(onError);

  idxRef.current = idx;
  keepsRef.current = keeps;
  submittingRef.current = submitting;
  videosRef.current = videos;
  onErrorRef.current = onError;

  useEffect(() => {
    const init: Record<string, boolean> = {};
    for (const v of videos) init[v] = true;
    setKeeps(init);
    setIdx(0);
  }, [videos.length]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement || e.target instanceof HTMLTextAreaElement) return;
      // Let space play/pause when video element is focused
      if (e.target instanceof HTMLMediaElement) return;
      switch (e.key) {
        case "j":
        case "ArrowDown":
          e.preventDefault();
          setIdx((i) => Math.min(videosRef.current.length - 1, i + 1));
          break;
        case "k":
        case "ArrowUp":
          e.preventDefault();
          setIdx((i) => Math.max(0, i - 1));
          break;
        case " ": {
          e.preventDefault();
          const v = videosRef.current[idxRef.current];
          if (v) setKeeps((prev) => ({ ...prev, [v]: !prev[v] }));
          break;
        }
        case "Enter":
          e.preventDefault();
          if (!submittingRef.current) {
            const vs = videosRef.current;
            const deletePaths = vs.filter((v) => !keepsRef.current[v]);
            if (deletePaths.length === 0) return;
            setSubmitting(true);
            fetch("/api/confirm", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ delete_paths: deletePaths }),
            })
              .then((res) => { if (!res.ok) throw new Error(`Confirm failed: ${res.status}`); })
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
      const deletePaths = videos.filter((v) => !keeps[v]);
      if (deletePaths.length > 0) {
        const res = await fetch("/api/confirm", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ delete_paths: deletePaths }),
        });
        if (!res.ok) throw new Error(`Confirm failed: ${res.status}`);
      }
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  };

  if (videos.length === 0) {
    return <p className="text-sm text-gray-500 p-4">No videos found in this folder.</p>;
  }

  const current = videos[Math.min(idx, videos.length - 1)];
  const isKept = keeps[current] ?? true;
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
        <span className="text-xs text-gray-300">j/k · space toggle · enter confirm</span>
        <div className="ml-auto flex items-center gap-2">
          {nDelete > 0 && <span className="text-xs text-gray-400">{nDelete} → trash</span>}
          <button onClick={confirm} disabled={submitting}
            className="px-3 py-1 text-sm font-medium bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50">
            {submitting ? "…" : "✓ Confirm"}
          </button>
        </div>
      </div>

      {/* Full-viewport video */}
      <div
        className={`flex-1 flex items-center justify-center bg-black border-4 transition-colors ${
          isKept ? "border-green-400" : "border-red-400"
        }`}
        onClick={() => setKeeps((prev) => ({ ...prev, [current]: !prev[current] }))}
      >
        <video
          key={current}
          src={`/api/video?path=${encodeURIComponent(current)}`}
          controls
          autoPlay
          className="max-w-full max-h-full"
          onClick={(e) => e.stopPropagation()}
        />
      </div>
    </div>
  );
}
