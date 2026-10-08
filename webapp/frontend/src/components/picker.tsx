import type { ProjectEntry, ProjectStatus, ReviewLeft } from "../types";
import { Pill } from "./ui";

/** Labels only. Colour and pill shape live on `Pill`. */
export const STATUS_LABEL: Record<ProjectStatus, string> = {
  ready: "Ready",
  stale: "Stale",
  never_run: "Not run",
  running: "Running…",
};

/** "~8 min", "~1.7 h" — the fit behind eta_s is ±30%, so no finer. */
export function formatEta(seconds: number): string {
  if (seconds < 60) return "<1 min";
  if (seconds < 90 * 60) return `~${Math.round(seconds / 60)} min`;
  return `~${(seconds / 3600).toFixed(1)} h`;
}

export function EtaTag({ etaS, pending }: { etaS: number | null; pending: number }) {
  if (etaS == null) return null;
  return (
    <Pill
      tone="eta"
      data-testid="pipeline-eta"
      title={`Estimated pipeline time for ${pending} unprocessed photo${pending === 1 ? "" : "s"}`}
    >
      ⏱ {formatEta(etaS)} · {pending} to process
    </Pill>
  );
}

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

/** What is still undecided, in the app's tab order. "Reviewed" once all is. */
export function ReviewLeftTag({ left }: { left: ReviewLeft | undefined }) {
  if (!left) return null;
  const parts = [
    left.clusters ? plural(left.clusters, "cluster") : null,
    left.singles ? plural(left.singles, "single") : null,
    left.videos ? plural(left.videos, "video") : null,
  ].filter(Boolean);
  return (
    <p data-testid="review-left" className="text-xs text-gray-500">
      {parts.length ? `${parts.join(" · ")} left` : "Reviewed"}
    </p>
  );
}

/** The trip a project belongs to, read off the archive's `YYYY_MM_Name`
 * folder convention. The server already rolls device folders up into their
 * trip, so this is normally the folder's own name. Null when nothing in the
 * path is dated. */
export function tripKey(folder: string): string | null {
  const segments = folder.split("/");
  for (let i = segments.length - 1; i >= 0; i--) {
    if (/^\d{4}_\d{2}/.test(segments[i])) return segments[i];
  }
  return null;
}

/** Newest trip first; within a trip, by display name.

Same-trip city rows share a folder, so sorting by path left them in
whatever order `/api/projects` sent — last opened. Opening Hakodate
then jumped it above Tokyo. Name is stable. Undated projects last. */
export function byTripThenName(a: ProjectEntry, b: ProjectEntry): number {
  const ka = tripKey(a.folder);
  const kb = tripKey(b.folder);
  if (ka !== kb) {
    if (ka === null) return 1;
    if (kb === null) return -1;
    return ka < kb ? 1 : -1;
  }
  return (a.display_name || a.folder).localeCompare(b.display_name || b.folder);
}

export function projectKey(p: { folder: string; subtrip?: string | null }): string {
  return p.subtrip ? `${p.folder}#${p.subtrip}` : p.folder;
}
