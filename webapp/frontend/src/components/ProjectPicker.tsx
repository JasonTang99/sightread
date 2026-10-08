import { useCallback, useEffect, useMemo, useState } from "react";
import type { FsListing, JobStatus, ProjectEntry, ReviewLeft } from "../types";
import { FolderBrowser } from "./FolderBrowser";
import { PickerActionBar } from "./PickerActionBar";
import { PipelineScreen } from "./PipelineScreen";
import { ProjectList } from "./ProjectList";
import { byTripThenName, projectKey } from "./picker";
import { Button } from "./ui";

export { tripKey } from "./picker";

// A few at a time: the walks share one drive, and the browser caps
// connections per host, which would otherwise starve the details request.
const REVIEW_LEFT_CONCURRENCY = 3;

async function loadReviewLeft(
  projects: ProjectEntry[],
  isCancelled: () => boolean,
  onCounts: (key: string, left: ReviewLeft) => void,
) {
  const queue = [...projects];
  const worker = async () => {
    for (let p = queue.shift(); p && !isCancelled(); p = queue.shift()) {
      const params = new URLSearchParams({ folder: p.folder });
      if (p.subtrip) params.set("subtrip", p.subtrip);
      try {
        const res = await fetch(`/api/projects/review-left?${params}`);
        if (res.ok && !isCancelled()) onCounts(projectKey(p), await res.json());
      } catch {
        // A row without counts is fine; the rest still load.
      }
    }
  };
  await Promise.all(Array.from({ length: REVIEW_LEFT_CONCURRENCY }, worker));
}

interface Props {
  onProjectOpened: () => void;
}

export function ProjectPicker({ onProjectOpened }: Props) {
  const [recents, setRecents] = useState<ProjectEntry[]>([]);
  const [reviewLeft, setReviewLeft] = useState<Record<string, ReviewLeft>>({});
  const [listing, setListing] = useState<FsListing | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [job, setJob] = useState<JobStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Browsing is for folders no project covers yet — rare once the archive
  // has been through the pipeline, so it stays out of the way until asked.
  const [browsing, setBrowsing] = useState(false);

  const loadRecents = useCallback(async (isCancelled: () => boolean) => {
    const res = await fetch("/api/projects");
    if (!res.ok || isCancelled()) return;
    const rows: ProjectEntry[] = await res.json();
    setRecents(rows);
    // Names already painted. What is left to review needs a walk per project
    // for its videos, seconds each on a big trip, so the counts run beside
    // the details below and each row fills in as its own answer lands.
    loadReviewLeft(
      rows.filter((p) => p.status === "ready" && !p.done_at),
      isCancelled,
      (key, left) => setReviewLeft((prev) => ({ ...prev, [key]: left })),
    );
    // Counts / stale / ETA walk the photo tree and can take minutes on a
    // large archive; fill them in when they land.
    const detail = await fetch("/api/projects/details");
    if (detail.ok && !isCancelled()) setRecents(await detail.json());
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
    let cancelled = false;
    loadRecents(() => cancelled);
    return () => { cancelled = true; };
  }, [loadRecents]);

  const byDate = useMemo(() => [...recents].sort(byTripThenName), [recents]);

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
            body: JSON.stringify({ folder: status.folder, subtrip: status.subtrip ?? null }),
          });
          onProjectOpened();
        }
      }
    }, 1000);
    return () => clearInterval(id);
  }, [job?.running, onProjectOpened]);

  const openIfReady = (p: ProjectEntry) => {
    if (busy) return;
    if (p.status === "ready" || p.status === "stale") openProject(p);
  };

  const openProject = async (p: ProjectEntry) => {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/projects/open", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ folder: p.folder, subtrip: p.subtrip ?? null }),
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

  const runPipeline = async (folder: string, subtrip?: string | null) => {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/projects/run-pipeline", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ folder, subtrip: subtrip ?? null }),
      });
      if (!res.ok) {
        const e = await res.json();
        throw new Error(e.detail ?? "Failed to start pipeline");
      }
      setJob({ running: true, done: false, error: null, last_line: null, lines: [], folder, subtrip: subtrip ?? null });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (job?.running || job?.done) {
    return <PipelineScreen job={job} onBack={() => setJob(null)} />;
  }

  const selectedRecent = selected ? recents.find((r) => projectKey(r) === selected) : null;

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white border-b border-gray-200 px-4 py-2">
        <h1 className="text-sm font-semibold text-gray-900">Sightread</h1>
      </header>

      <div className="max-w-5xl mx-auto p-4 grid grid-cols-[280px_1fr] gap-4">
        <ProjectList
          title="Recent"
          testId="recent-projects"
          projects={recents}
          reviewLeft={reviewLeft}
          selected={selected}
          onSelect={setSelected}
          onOpen={openIfReady}
        />

        <div className="flex flex-col gap-3">
          <ProjectList
            title="By trip date"
            testId="projects-by-date"
            projects={byDate}
            reviewLeft={reviewLeft}
            selected={selected}
            onSelect={setSelected}
            onOpen={openIfReady}
          />

          {!browsing ? (
            <div className="self-start">
              <Button
                size="md"
                onClick={() => { setBrowsing(true); browse(); }}
              >
                Other folder…
              </Button>
            </div>
          ) : (
            <FolderBrowser
              listing={listing}
              selected={selected}
              onSelect={setSelected}
              onBrowse={browse}
              onClose={() => setBrowsing(false)}
            />
          )}

          {selected && (
            <PickerActionBar
              selected={selected}
              selectedRecent={selectedRecent ?? null}
              busy={busy}
              error={error}
              onOpen={openProject}
              onRun={runPipeline}
            />
          )}
        </div>
      </div>
    </div>
  );
}
