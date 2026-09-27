import { useCallback, useEffect, useState } from "react";

interface Preview {
  pending_deletes: number;
  remaining_photos: number;
  remaining_videos: number;
  export: {
    files: number;
    pending: number;
    dest: string;
  };
  done_at: string | null;
}

interface Props {
  pendingCount: number;
  onError: (msg: string) => void;
  onFinished: () => void | Promise<void>;
}

function remainLabel(n: number, noun: string): string {
  return n === 1 ? `1 ${noun} remains` : `${n} ${noun}s remain`;
}

export function FinishTripPanel({ pendingCount, onError, onFinished }: Props) {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);

  const loadPreview = useCallback(async () => {
    try {
      const res = await fetch("/api/finish/preview");
      if (!res.ok) throw new Error(`Preview failed: ${res.status}`);
      setPreview(await res.json());
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    }
  }, [onError]);

  useEffect(() => {
    loadPreview();
  }, [loadPreview, pendingCount]);

  const run = async () => {
    if (!preview) return;
    const deletes = preview.pending_deletes;
    const ok = window.confirm(
      (deletes > 0
        ? `Permanently delete ${deletes} shot(s) from the primary drive ` +
          `(raws and sidecars included; mirror verified first). `
        : "Nothing queued to delete. ") +
        `${remainLabel(preview.remaining_photos, "photo")}, ` +
        `${remainLabel(preview.remaining_videos, "video")}. ` +
        "Then export, clear caches, and return to projects.",
    );
    if (!ok) return;

    setBusy(true);
    try {
      if (deletes > 0) {
        const res = await fetch("/api/apply-deletes", { method: "POST" });
        if (!res.ok) {
          const detail = await res.json().catch(() => null);
          throw new Error(detail?.detail ?? `Apply deletes failed: ${res.status}`);
        }
        const data = await res.json();
        if (data.unmirrored?.length) {
          throw new Error(
            `${data.unmirrored.length} left pending (no verified mirror copy). Nothing else was finished.`,
          );
        }
      }

      if (preview.export.files > 0 && preview.export.pending > 0) {
        const res = await fetch("/api/exports/trip", { method: "POST" });
        if (!res.ok) {
          const detail = await res.json().catch(() => null);
          throw new Error(detail?.detail ?? `Export failed: ${res.status}`);
        }
      }

      const clean = await fetch("/api/projects/clean-pipeline", { method: "POST" });
      if (!clean.ok) throw new Error(`Clean pipeline failed: ${clean.status}`);
      const done = await fetch("/api/projects/done", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ done: true }),
      });
      if (!done.ok) throw new Error(`Mark done failed: ${done.status}`);
      await onFinished();
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
      await loadPreview();
    } finally {
      setBusy(false);
    }
  };

  const finished = !!preview?.done_at;
  const deletes = preview?.pending_deletes ?? pendingCount;
  const photos = preview?.remaining_photos;
  const videos = preview?.remaining_videos;

  return (
    <div className="space-y-3 max-w-lg" data-testid="finish-trip-panel">
      <div className="bg-white rounded-lg border border-gray-200 px-4 py-4 space-y-3">
        <h2 className="text-sm font-semibold text-gray-900">Finish trip</h2>
        <div className="text-sm text-gray-700 space-y-0.5">
          <p data-testid="finish-delete-count">
            {deletes === 1 ? "1 to delete" : `${deletes} to delete`}
          </p>
          <p data-testid="finish-remain-photos">
            {photos == null ? "…" : remainLabel(photos, "photo")}
          </p>
          <p data-testid="finish-remain-videos">
            {videos == null ? "…" : remainLabel(videos, "video")}
          </p>
        </div>
        <button
          onClick={run}
          disabled={busy || finished || !preview}
          data-testid="finish-trip"
          className="px-3 py-1.5 text-sm font-medium bg-green-600 text-white rounded hover:bg-green-700 disabled:opacity-50"
        >
          {busy ? "Working…" : finished ? "Already finished" : "Finish trip"}
        </button>
      </div>
    </div>
  );
}
