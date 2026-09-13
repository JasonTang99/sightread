import type { Cluster, PhotoDecision, PhotoDecisions } from "./types";

// Statuses meaning "this photo is on its way out" — pending or already applied.
const DOOMED: PhotoDecision[] = ["to_delete", "deleted"];

export function isDoomed(status: PhotoDecision | undefined): boolean {
  return status !== undefined && DOOMED.includes(status);
}

// A cluster is decided once every image in it has been decided on. Derived
// rather than stored, because decisions live per photo — see types.ts.
export function isDecided(cluster: Cluster, decisions: PhotoDecisions): boolean {
  return cluster.images.length > 0 && cluster.images.every((img) => img.path in decisions);
}

// Where confirm lands with "skip reviewed" on: the next still-pending entry
// after `from`, wrapping round to the top, never `from` itself — it was just
// confirmed, and the decision that says so has not come back from the server
// yet. -1 when nothing else is pending.
export function nextPendingIndex(count: number, from: number, isPending: (i: number) => boolean): number {
  for (let step = 1; step < count; step++) {
    const i = (from + step) % count;
    if (isPending(i)) return i;
  }
  return -1;
}

// ...and wiped when it was decided and nothing survived.
export function isWiped(cluster: Cluster, decisions: PhotoDecisions): boolean {
  return isDecided(cluster, decisions) && cluster.images.every((img) => isDoomed(decisions[img.path]));
}
