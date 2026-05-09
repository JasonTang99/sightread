import { useCallback, useEffect, useState } from "react";
import type { FsListing, JobStatus, ProjectEntry, ProjectStatus } from "../types";

interface Props {
  onProjectOpened: () => void;
}

const STATUS_BADGE: Record<ProjectStatus, { label: string; cls: string }> = {
  ready:     { label: "Ready",     cls: "bg-green-100 text-green-700" },
  stale:     { label: "Stale",     cls: "bg-yellow-100 text-yellow-700" },
  never_run: { label: "Not run",   cls: "bg-gray-100 text-gray-500" },
  running:   { label: "Running…",  cls: "bg-blue-100 text-blue-600" },
};

export function ProjectPicker({ onProjectOpened }: Props) {
  const [recents, setRecents] = useState<ProjectEntry[]>([]);
  const [listing, setListing] = useState<FsListing | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [job, setJob] = useState<JobStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadRecents = useCallback(async () => {
    const res = await fetch("/api/projects");
    if (res.ok) setRecents(await res.json());
  }, []);

  const browse = useCallback(async (path?: string) => {
    const url = path ? `/api/fs/list?path=${encodeURIComponent(path)}` : "/api/fs/list";
    const res = await fetch(url);
    if (res.ok) {
      const data: FsListing = await res.json();
      setListing(data);
      setSelected(data.path);
    }
  }, []);

  useEffect(() => {
    loadRecents();
    browse();
  }, [loadRecents, browse]);

  // Poll job status while running
  useEffect(() => {
    if (!job?.running) return;
    const id = setInterval(async () => {
      const res = await fetch("/api/projects/job-status");
      if (!res.ok) return;
      const status: JobStatus = await res.json();
      setJob(status);
      if (status.done) {
        clearInterval(id);
        if (!status.error) {
          // Open the project now that pipeline finished
          await fetch("/api/projects/open", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ folder: status.folder }),
          });
          onProjectOpened();
        }
      }
    }, 1000);
    return () => clearInterval(id);
  }, [job?.running, onProjectOpened]);

  const openProject = async (folder: string) => {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/projects/open", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ folder }),
      });
      if (!res.ok) {
        const e = await res.json();
        throw new Error(e.detail ?? "Failed to open");
      }
      onProjectOpened();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const runPipeline = async (folder: string) => {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/projects/run-pipeline", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ folder }),
      });
      if (!res.ok) {
        const e = await res.json();
        throw new Error(e.detail ?? "Failed to start pipeline");
      }
      setJob({ running: true, done: false, error: null, last_line: null, folder });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (job?.running || (job?.done && !job.error)) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center gap-4 bg-gray-50">
        <div className="bg-white border border-gray-200 rounded-lg p-8 w-full max-w-lg text-center shadow-sm">
          {job.done ? (
            <p className="text-sm font-medium text-green-600">Pipeline complete — loading…</p>
          ) : (
            <>
              <p className="text-sm font-semibold text-gray-700 mb-1">Running pipeline</p>
              <p className="text-xs text-gray-400 mb-4 truncate">{job.folder}</p>
              <div className="w-full bg-gray-100 rounded-full h-1.5 mb-3">
                <div className="bg-blue-500 h-1.5 rounded-full animate-pulse w-1/2" />
              </div>
              {job.last_line && (
                <p className="text-xs text-gray-500 font-mono truncate">{job.last_line}</p>
              )}
            </>
          )}
          {job.error && (
            <p className="text-xs text-red-600 mt-2">{job.error}</p>
          )}
        </div>
      </div>
    );
  }

  const selectedIsNew = selected && !recents.find((r) => r.folder === selected);
  const selectedRecent = selected ? recents.find((r) => r.folder === selected) : null;

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white border-b border-gray-200 px-4 py-2">
        <h1 className="text-sm font-semibold text-gray-900">Sightread</h1>
      </header>

      <div className="max-w-5xl mx-auto p-4 grid grid-cols-[280px_1fr] gap-4">
        {/* Recents */}
        <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
          <div className="px-3 py-2 border-b border-gray-100">
            <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide">Recent</p>
          </div>
          {recents.length === 0 ? (
            <p className="text-xs text-gray-400 px-3 py-4">No recent projects.</p>
          ) : (
            <ul>
              {recents.map((p) => {
                const badge = STATUS_BADGE[p.status];
                const isSelected = selected === p.folder;
                return (
                  <li key={p.folder}>
                    <button
                      onClick={() => setSelected(p.folder)}
                      className={`w-full text-left px-3 py-2.5 flex items-start gap-2 hover:bg-gray-50 transition-colors border-l-2 ${
                        isSelected ? "border-blue-500 bg-blue-50" : "border-transparent"
                      }`}
                    >
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium text-gray-800 truncate">{p.display_name}</p>
                        <p className="text-xs text-gray-400 truncate">{p.folder}</p>
                        {p.image_count > 0 && (
                          <p className="text-xs text-gray-400">{p.image_count} images</p>
                        )}
                      </div>
                      <span className={`text-xs px-1.5 py-0.5 rounded font-medium shrink-0 ${badge.cls}`}>
                        {badge.label}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        {/* Browser + actions */}
        <div className="flex flex-col gap-3">
          <div className="bg-white border border-gray-200 rounded-lg overflow-hidden flex-1">
            <div className="px-3 py-2 border-b border-gray-100 flex items-center gap-2">
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide">Browse</p>
              {listing && (
                <p className="text-xs text-gray-400 truncate font-mono">{listing.path}</p>
              )}
            </div>

            {listing && (
              <>
                {listing.parent && (
                  <button
                    onClick={() => browse(listing.parent!)}
                    className="w-full text-left px-3 py-2 text-xs text-gray-500 hover:bg-gray-50 flex items-center gap-2 border-b border-gray-100"
                  >
                    <span>↑</span>
                    <span className="font-mono">..</span>
                  </button>
                )}
                <ul className="divide-y divide-gray-50 max-h-[400px] overflow-y-auto">
                  {listing.entries.map((entry) => {
                    const isSelected = selected === entry.path;
                    return (
                      <li key={entry.path}>
                        <button
                          onClick={() => setSelected(entry.path)}
                          onDoubleClick={() => entry.is_dir && browse(entry.path)}
                          className={`w-full text-left px-3 py-2 flex items-center gap-2 hover:bg-gray-50 transition-colors border-l-2 ${
                            isSelected ? "border-blue-500 bg-blue-50" : "border-transparent"
                          }`}
                        >
                          <span className="text-base leading-none">
                            {entry.image_count > 0 ? "📸" : "📁"}
                          </span>
                          <span className="flex-1 text-sm text-gray-700 truncate">{entry.name}</span>
                          {entry.image_count > 0 && (
                            <span className="text-xs text-gray-400 shrink-0">{entry.image_count}</span>
                          )}
                          {entry.is_dir && (
                            <button
                              onClick={(e) => { e.stopPropagation(); browse(entry.path); }}
                              className="text-xs text-gray-400 hover:text-gray-600 shrink-0 px-1"
                              title="Open folder"
                            >
                              →
                            </button>
                          )}
                        </button>
                      </li>
                    );
                  })}
                  {listing.entries.length === 0 && (
                    <li className="px-3 py-4 text-xs text-gray-400">No subdirectories.</li>
                  )}
                </ul>
              </>
            )}
          </div>

          {/* Action bar */}
          {selected && (
            <div className="bg-white border border-gray-200 rounded-lg px-4 py-3 flex items-center gap-3">
              <div className="flex-1 min-w-0">
                <p className="text-xs text-gray-500">Selected</p>
                <p className="text-sm font-medium text-gray-800 truncate font-mono">{selected}</p>
              </div>

              {error && (
                <p className="text-xs text-red-600 shrink-0">{error}</p>
              )}

              {selectedRecent ? (
                <>
                  {(selectedRecent.status === "ready" || selectedRecent.status === "stale") && (
                    <button
                      onClick={() => openProject(selected)}
                      disabled={busy}
                      className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50 shrink-0"
                    >
                      Open
                    </button>
                  )}
                  <button
                    onClick={() => runPipeline(selected)}
                    disabled={busy}
                    className="px-3 py-1.5 text-sm border border-gray-300 text-gray-700 rounded hover:bg-gray-50 disabled:opacity-50 shrink-0"
                  >
                    {selectedRecent.status === "stale" ? "Re-run Pipeline" : "Run Pipeline"}
                  </button>
                </>
              ) : (
                <button
                  onClick={() => runPipeline(selected)}
                  disabled={busy}
                  className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50 shrink-0"
                >
                  Run Pipeline
                </button>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
