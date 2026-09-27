import { useCallback, useEffect, useMemo, useState } from "react";
import type { FsListing, JobStatus, ProjectEntry } from "../types";
import { FolderBrowser } from "./FolderBrowser";
import { PickerActionBar } from "./PickerActionBar";
import { PipelineScreen } from "./PipelineScreen";
import { ProjectList } from "./ProjectList";
import { byTripThenName, projectKey } from "./picker";
import { Button } from "./ui";

export { tripKey } from "./picker";

interface Props {
  onProjectOpened: () => void;
}

export function ProjectPicker({ onProjectOpened }: Props) {
  const [recents, setRecents] = useState<ProjectEntry[]>([]);
  const [listing, setListing] = useState<FsListing | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [job, setJob] = useState<JobStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Browsing is for folders no project covers yet — rare once the archive
  // has been through the pipeline, so it stays out of the way until asked.
  const [browsing, setBrowsing] = useState(false);

  const loadRecents = useCallback(async () => {
    const res = await fetch("/api/projects");
    if (res.ok) setRecents(await res.json());
    // Names already painted. Counts / stale / ETA walk the photo tree and
    // can take minutes on a large archive; fill them in when they land.
    const detail = await fetch("/api/projects/details");
    if (detail.ok) setRecents(await detail.json());
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
          selected={selected}
          onSelect={setSelected}
          onOpen={openIfReady}
        />

        <div className="flex flex-col gap-3">
          <ProjectList
            title="By trip date"
            testId="projects-by-date"
            projects={byDate}
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
