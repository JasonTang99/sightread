import { useCallback, useEffect, useState } from "react";
import { TrashPanel } from "./TrashPanel";

interface Preview {
  pending_deletes: number;
  favorites: number;
  export: {
    mode: "link" | "copy";
    shots: number;
    files: number;
    bytes: number;
    delivered: number;
    pending: number;
    pending_bytes: number;
    // HEIFs this run would re-encode as JPEGs, and the space budgeted for them.
    convert: number;
    convert_bytes: number;
    dest: string;
    free_bytes: number | null;
  };
  pipeline_cache_bytes: number;
  derived_cache_bytes: number;
  done_at: string | null;
}

interface Props {
  pendingCount: number;
  favoriteCount: number;
  onRefresh: () => Promise<void>;
  onError: (msg: string) => void;
  onFinished: () => Promise<void>;
}

function formatBytes(n: number) {
  if (!n) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(Math.floor(Math.log(n) / Math.log(1024)), units.length - 1);
  return `${(n / 1024 ** i).toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

export function FinishTripPanel({
  pendingCount,
  favoriteCount,
  onRefresh,
  onError,
  onFinished,
}: Props) {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [step1Done, setStep1Done] = useState(false);
  const [step2Done, setStep2Done] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [notes, setNotes] = useState<Record<number, string>>({});

  const loadPreview = useCallback(async () => {
    try {
      const res = await fetch("/api/finish/preview");
      if (!res.ok) throw new Error(`Preview failed: ${res.status}`);
      const data: Preview = await res.json();
      setPreview(data);
      if (data.pending_deletes === 0) setStep1Done(true);
      // Nothing left to deliver means the export already happened — otherwise a
      // reload would re-arm step 2 and keep step 3 locked behind a no-op click.
      if (data.export.files === 0 || data.export.pending === 0) setStep2Done(true);
      if (data.done_at) {
        setStep1Done(true);
        setStep2Done(true);
      }
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    }
  }, [onError]);

  useEffect(() => {
    loadPreview();
  }, [loadPreview, pendingCount, favoriteCount]);

  useEffect(() => {
    if (pendingCount === 0) setStep1Done(true);
  }, [pendingCount]);

  const handleExport = async () => {
    if (!preview) return;
    setExporting(true);
    try {
      const plan = preview.export;
      if (plan.files === 0) {
        setNotes((n) => ({ ...n, 2: "Nothing to export." }));
        setStep2Done(true);
        return;
      }
      if (plan.pending === 0) {
        setNotes((n) => ({
          ...n,
          2: `All ${plan.files} file(s) already in ${plan.dest}.`,
        }));
        setStep2Done(true);
        return;
      }
      const linking = plan.mode === "link";
      const free = plan.free_bytes == null ? "" : ` (${formatBytes(plan.free_bytes)} free)`;
      const already =
        plan.delivered > 0 ? ` ${plan.delivered} file(s) are already there and will be skipped.` : "";
      const reencode =
        plan.convert > 0
          ? `; ${plan.convert} HEIC re-encoded as JPEG, up to ${formatBytes(plan.convert_bytes)}`
          : "";
      const cost = (linking
        ? `${plan.pending} file(s), ${formatBytes(plan.pending_bytes)} of originals — hardlinked` +
          (plan.convert > 0 ? "" : ", so no extra space is used")
        : `${plan.pending} file(s), ${formatBytes(plan.pending_bytes)} to copy`) + reencode;
      const ok = window.confirm(
        `Export this trip — ${cost} — ` +
          `to\n\n${plan.dest}${free}\n\n` +
          `Every photo that survived curation, as a JPEG (no raws or sidecars), plus starred ` +
          `videos in their tag folders. Nothing already there is overwritten.${already}`
      );
      if (!ok) return;

      const res = await fetch("/api/exports/trip", { method: "POST" });
      if (!res.ok) {
        const detail = await res.json().catch(() => null);
        throw new Error(detail?.detail ?? `Export failed: ${res.status}`);
      }
      const data = await res.json();
      setNotes((n) => ({
        ...n,
        2: data.linked > 0 && data.copied === 0 && !data.converted
          ? `Exported ${data.linked} file(s) to ${data.dest} — hardlinked, no extra space used.`
          : `Exported ${data.delivered} file(s) (${data.linked} linked, ${formatBytes(data.copied_bytes)} copied` +
            (data.converted ? `, ${data.converted} re-encoded from HEIC` : "") +
            `) to ${data.dest}.`,
      }));
      setStep2Done(true);
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    } finally {
      setExporting(false);
    }
  };

  const handleClear = async () => {
    if (!preview) return;
    const pipeline = formatBytes(preview.pipeline_cache_bytes);
    const derived = formatBytes(preview.derived_cache_bytes);
    const ok = window.confirm(
      "Clear this project's pipeline and preview caches?\n\n" +
        `Pipeline artifacts (~${pipeline}) and cached previews (~${derived}) will be deleted. ` +
        "You cannot re-review clusters after this.\n\n" +
        "decisions.json is kept — it remains the record of what was removed and why."
    );
    if (!ok) return;
    setClearing(true);
    try {
      const clean = await fetch("/api/projects/clean-pipeline", { method: "POST" });
      if (!clean.ok) throw new Error(`Clean pipeline failed: ${clean.status}`);
      const cleanData = await clean.json();

      const done = await fetch("/api/projects/done", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ done: true }),
      });
      if (!done.ok) throw new Error(`Mark done failed: ${done.status}`);
      const doneData = await done.json();

      const freed = (cleanData.freed_bytes ?? 0) + (doneData.freed_bytes ?? 0);
      setNotes((n) => ({
        ...n,
        3: `Cleared pipeline (${cleanData.removed?.length ?? 0} item(s)) and previews — ${formatBytes(freed)} freed.`,
      }));
      await onRefresh();
      await onFinished();
      await loadPreview();
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    } finally {
      setClearing(false);
    }
  };

  const stepClass = (n: number, enabled: boolean) =>
    `rounded-lg border p-4 space-y-2 ${
      enabled ? "border-gray-200 bg-white" : "border-gray-100 bg-gray-50 opacity-60"
    }`;

  return (
    <div className="space-y-3 max-w-3xl" data-testid="finish-trip-panel">
      <div className="bg-white rounded-lg border border-gray-200 px-4 py-3">
        <h2 className="text-sm font-semibold text-gray-900">Finish trip</h2>
        <p className="text-xs text-gray-500 mt-1">
          Three steps in order: delete reviewed shots from the primary drive, export the trip,
          then clear caches so you cannot accidentally re-review.
        </p>
      </div>

      <section className={stepClass(1, true)} data-testid="finish-step-1">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-sm font-medium text-gray-800">
              {step1Done ? "✓" : "1."} Apply deletes
            </p>
            <p className="text-xs text-gray-500 mt-0.5">
              {pendingCount > 0
                ? `${pendingCount} shot(s) queued — each takes its raw and sidecars. Mirror verified before unlink.`
                : step1Done
                  ? "Nothing pending on the primary drive."
                  : "Review photos first, then return here."}
            </p>
          </div>
        </div>
        {pendingCount > 0 && (
          <TrashPanel
            pendingCount={pendingCount}
            onRefresh={async () => {
              await onRefresh();
              setStep1Done(true);
              setNotes((n) => ({ ...n, 1: "Deletes applied from the primary drive." }));
            }}
            onError={onError}
          />
        )}
        {notes[1] && <p className="text-xs text-gray-500">{notes[1]}</p>}
      </section>

      <section className={stepClass(2, step1Done)} data-testid="finish-step-2">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-sm font-medium text-gray-800">
              {step2Done ? "✓" : "2."} Export the trip
            </p>
            <p className="text-xs text-gray-500 mt-0.5">
              {preview && preview.export.files === 0
                ? "Nothing to export — skip when ready."
                : preview && preview.export.pending === 0
                  ? `All ${preview.export.files} file(s) already in ${preview.export.dest}.`
                  : `${preview?.export.files ?? 0} file(s) — every surviving photo as a JPEG, plus starred videos by tag — into ${preview?.export.dest ?? "exports folder"}${preview?.export.mode === "link" ? ", hardlinked" : ""}.`}
            </p>
          </div>
          <button
            onClick={handleExport}
            disabled={!step1Done || exporting || step2Done}
            data-testid="finish-export"
            className="px-3 py-1.5 text-sm font-medium bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50 shrink-0"
          >
            {exporting ? "Exporting…" : step2Done ? "Exported" : "Export trip"}
          </button>
        </div>
        {notes[2] && <p className="text-xs text-gray-500">{notes[2]}</p>}
      </section>

      <section className={stepClass(3, step1Done && step2Done)} data-testid="finish-step-3">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-sm font-medium text-gray-800">3. Clear pipeline and caches</p>
            <p className="text-xs text-gray-500 mt-0.5">
              {preview
                ? `~${formatBytes(preview.pipeline_cache_bytes)} pipeline + ~${formatBytes(preview.derived_cache_bytes)} previews. decisions.json kept.`
                : "Loading…"}
            </p>
          </div>
          <button
            onClick={handleClear}
            disabled={!step1Done || !step2Done || clearing || !!preview?.done_at}
            data-testid="finish-clear"
            className="px-3 py-1.5 text-sm font-medium bg-red-600 text-white rounded hover:bg-red-700 disabled:opacity-50 shrink-0"
          >
            {preview?.done_at ? "Cleared" : clearing ? "Clearing…" : "Clear caches"}
          </button>
        </div>
        {notes[3] && <p className="text-xs text-gray-500">{notes[3]}</p>}
      </section>
    </div>
  );
}
