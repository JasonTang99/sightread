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

// ...and wiped when it was decided and nothing survived.
export function isWiped(cluster: Cluster, decisions: PhotoDecisions): boolean {
  return isDecided(cluster, decisions) && cluster.images.every((img) => isDoomed(decisions[img.path]));
}
