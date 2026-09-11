import { useEffect, useRef, useState } from "react";
import { useWindowKeydown } from "../hooks/useWindowKeydown";

interface Props {
  favorites: string[];
  onToggleFavorite: (path: string) => Promise<void>;
  onRefresh: () => Promise<void>;
  onError: (msg: string) => void;
}

const COLS = 4;

const VIDEO_EXTS = new Set([".mp4", ".mov", ".avi", ".mkv", ".m4v", ".mts", ".m2ts", ".webm"]);
function isVideo(path: string) {
  return VIDEO_EXTS.has(path.slice(path.lastIndexOf(".")).toLowerCase());
}

function imgUrl(path: string, w = 400) {
  return `/api/image?path=${encodeURIComponent(path)}&w=${w}`;
}

function videoUrl(path: string) {
  return `/api/video?path=${encodeURIComponent(path)}`;
}

function formatBytes(n: number) {
  if (!n) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(Math.floor(Math.log(n) / Math.log(1024)), units.length - 1);
  return `${(n / 1024 ** i).toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

export function FavoritesView({ favorites, onToggleFavorite, onRefresh, onError }: Props) {
  const [busy, setBusy] = useState<string | null>(null);
  const [focus, setFocus] = useState(0);
  const [exporting, setExporting] = useState(false);
  const [exportResult, setExportResult] = useState<string | null>(null);
  const focusedRef = useRef<HTMLDivElement | null>(null);

  // Keep focus index in range as the list shrinks.
  useEffect(() => {
    setFocus((i) => Math.max(0, Math.min(i, favorites.length - 1)));
  }, [favorites.length]);

  useEffect(() => {
    focusedRef.current?.scrollIntoView({ block: "nearest" });
  }, [focus]);

  // Export is two round trips on purpose: the preview prices the run before the
  // user agrees to it, since the exports root can sit on the same drive
  // applying deletes just freed. It delivers the whole trip, not only what is
  // shown here — this button is just the nearest place to reach it.
  const handleExport = async () => {
    setExporting(true);
    try {
      const pres = await fetch("/api/exports/preview");
      if (!pres.ok) throw new Error(`Preview failed: ${pres.status}`);
      const plan = await pres.json();
      if (plan.files === 0) {
        setExportResult("Nothing to export — no photo or starred video is on disk.");
        return;
      }
      const free = plan.free_bytes == null ? "" : ` (${formatBytes(plan.free_bytes)} free)`;
      const reencode =
        plan.convert > 0
          ? `; ${plan.convert} HEIC re-encoded as JPEG, up to ${formatBytes(plan.convert_bytes)}`
          : "";
      const cost =
        (plan.mode === "link"
          ? `${plan.files} file(s), ${formatBytes(plan.bytes)} — hardlinked` +
            (plan.convert > 0 ? "" : ", so no extra space is used")
          : `${plan.files} file(s), ${formatBytes(plan.bytes)} to copy`) + reencode;
      const ok = window.confirm(
        `Export this trip — ${cost} — ` +
          `to\n\n${plan.dest}${free}\n\n` +
          `Every photo that survived curation, as a JPEG — no raws or sidecars, and never a ` +
          `derived cache — plus starred videos in their tag folders. ` +
          `Nothing already there is overwritten.`
      );
      if (!ok) return;

      const res = await fetch("/api/exports/trip", { method: "POST" });
      if (!res.ok) {
        const detail = await res.json().catch(() => null);
        throw new Error(detail?.detail ?? `Export failed: ${res.status}`);
      }
      const data = await res.json();
      const reencoded = data.converted ? `, ${data.converted} re-encoded from HEIC` : "";
      const written =
        data.copied > 0 || data.converted
          ? ` (${data.linked} linked, ${formatBytes(data.copied_bytes)} copied${reencoded})`
          : " (hardlinked)";
      const parts = [`Exported ${data.delivered} file(s)${written} to ${data.dest}`];
      if (data.skipped) parts.push(`${data.skipped} already there`);
      if (data.failed?.length) parts.push(`${data.failed.length} failed`);
      setExportResult(`${parts.join(" · ")}.`);
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    } finally {
      setExporting(false);
    }
  };

  const handleUnfavorite = async (path: string) => {
    setBusy(path);
    try {
      await onToggleFavorite(path);
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  // Space: drop the favorite and mark the item for deletion. Order matters —
  // the backend /api/confirm endpoint skips anything still favorited, so the
  // favorite must be removed first.
  const handleDelete = async (path: string) => {
    setBusy(path);
    try {
      await onToggleFavorite(path);
      const res = await fetch("/api/confirm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ delete_paths: [path] }),
      });
      if (!res.ok) throw new Error(`Confirm failed: ${res.status}`);
      await onRefresh();
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  useWindowKeydown((e) => {
    if (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement || e.target instanceof HTMLTextAreaElement) return;
    if (favorites.length === 0) return;
    const last = favorites.length - 1;
    switch (e.key) {
      case "ArrowRight":
      case "l":
        e.preventDefault();
        setFocus((i) => Math.min(last, i + 1));
        break;
      case "ArrowLeft":
      case "h":
        e.preventDefault();
        setFocus((i) => Math.max(0, i - 1));
        break;
      case "ArrowDown":
      case "j":
        e.preventDefault();
        setFocus((i) => Math.min(last, i + COLS));
        break;
      case "ArrowUp":
      case "k":
        e.preventDefault();
        setFocus((i) => Math.max(0, i - COLS));
        break;
      case " ": {
        e.preventDefault();
        const path = favorites[Math.min(focus, last)];
        if (path && busy === null) handleDelete(path);
        break;
      }
      case "s": {
        e.preventDefault();
        const path = favorites[Math.min(focus, last)];
        if (path && busy === null) handleUnfavorite(path);
        break;
      }
    }
  });

  if (favorites.length === 0) {
    return (
      <div className="bg-white rounded border border-gray-200 px-6 py-12 text-center">
        <p className="text-2xl mb-2">★</p>
        <p className="text-gray-700 font-medium">No favorites yet</p>
        <p className="text-sm text-gray-500 mt-1">
          Press <kbd className="px-1 py-0.5 text-xs bg-gray-100 border border-gray-300 rounded">s</kbd> on any photo or video to star it
        </p>
      </div>
    );
  }

  const focusIdx = Math.min(focus, favorites.length - 1);

  return (
    <div className="space-y-2">
      <div className="bg-white rounded border border-gray-200 px-3 py-2 flex items-center gap-2">
        <span className="text-sm font-medium text-yellow-600">
          ★ {favorites.length} favorite{favorites.length !== 1 ? "s" : ""}
        </span>
        <span className="text-xs text-gray-400">
          h/j/k/l move · <kbd className="px-1 py-0.5 text-xs bg-gray-100 border border-gray-300 rounded">space</kbd> unfavorite + mark delete · <kbd className="px-1 py-0.5 text-xs bg-gray-100 border border-gray-300 rounded">s</kbd> unfavorite
        </span>
        <button
          onClick={handleExport}
          disabled={exporting}
          data-testid="export-trip"
          className="ml-auto px-3 py-1.5 text-sm font-medium bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50 shrink-0"
        >
          {exporting ? "Exporting…" : "📤 Export trip"}
        </button>
      </div>
      {exportResult && (
        <p className="text-xs text-gray-500 px-3">{exportResult}</p>
      )}
      <div className="grid gap-2" style={{ gridTemplateColumns: `repeat(${COLS}, minmax(0, 1fr))` }}>
        {favorites.map((path, i) => (
          <div
            key={path}
            ref={i === focusIdx ? focusedRef : null}
            onClick={() => setFocus(i)}
            className={`bg-white rounded overflow-hidden border-2 relative cursor-pointer ${
              i === focusIdx ? "border-blue-500 ring-2 ring-blue-300" : "border-yellow-400"
            }`}
          >
            <div className="relative">
              <span className="absolute top-1.5 right-1.5 z-10 text-yellow-400 text-base leading-none drop-shadow">★</span>
              {isVideo(path) ? (
                <video
                  src={videoUrl(path)}
                  className="w-full object-contain bg-gray-900 max-h-64"
                  preload="metadata"
                  muted
                />
              ) : (
                <img
                  src={imgUrl(path)}
                  alt=""
                  className="w-full object-contain bg-gray-50 max-h-64"
                  loading="lazy"
                />
              )}
            </div>
            <div className="px-2 py-1.5">
              <p className="text-xs text-gray-500 truncate" title={path}>
                {busy === path ? "… " : ""}{path.split("/").pop()}
              </p>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
