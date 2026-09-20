/** Every keyboard shortcut, written once.
 *
 * They used to be written twice, in two visual languages that had already
 * drifted. HelpOverlay held a table for Clusters and Singles only; each view
 * carried its own one-line crib in the toolbar — ClusterView's ran to thirteen
 * items — and the Videos and Favorites cribs had no entry in the overlay at
 * all. Nothing kept the two in step, and the bindings they describe live in a
 * third place again (the views' own keydown handlers), so a binding could be
 * changed in one of three places and look right in the other two.
 *
 * This does not fix that third gap — the handlers still own the behaviour —
 * but it collapses the two descriptions into one list per view, so the crib
 * and the overlay can no longer disagree with each other.
 *
 * `brief` is what the toolbar shows: the handful you reach for without
 * thinking. Everything else is overlay-only. Splitting on `brief` rather than
 * keeping two lists is the point — an entry cannot exist in the crib and be
 * missing from the reference.
 */
export interface Shortcut {
  /** As the user would say it. Rendered in a <kbd>. */
  keys: string;
  what: string;
  /** Show in the view's toolbar crib as well as the overlay. */
  brief?: boolean;
  /** Crib wording, when the overlay's sentence is too long for one line.
   *  The overlay explains; the crib reminds. */
  short?: string;
}

export const CLUSTER_KEYS: Shortcut[] = [
  { keys: "← / →", what: "prev / next cluster", brief: true, short: "clusters" },
  { keys: "h / l", what: "move image focus left / right" },
  { keys: "j / k", what: "move image focus up / down" },
  { keys: "Space", what: "toggle focused image keep/delete", brief: true, short: "keep/delete" },
  { keys: "Shift+Space", what: "keep only the focused image (starred stay kept)" },
  { keys: "1–9", what: "toggle image by rank number" },
  { keys: "K", what: "keep best (rank 1 only)", brief: true, short: "keep best" },
  { keys: "n", what: "jump to next unreviewed cluster" },
  { keys: "Enter", what: "confirm cluster (last → next tab)", brief: true, short: "confirm" },
  { keys: "b", what: "skip cluster (no confirm)" },
  { keys: "s", what: "star / unstar focused image (star keeps)", brief: true, short: "star" },
  { keys: "t", what: "cycle tag on focused image" },
  { keys: "u", what: "undo last confirm" },
  { keys: "?", what: "toggle this help" },
];

export const SINGLES_KEYS: Shortcut[] = [
  { keys: "j / k", what: "move image focus up / down", brief: true, short: "move" },
  { keys: "← / →", what: "prev / next single" },
  { keys: "Space", what: "toggle focused image keep/delete", brief: true, short: "keep/delete" },
  { keys: "s", what: "star / unstar (star keeps)", brief: true, short: "star" },
  { keys: "1–9", what: "tag current image" },
  { keys: "t", what: "cycle tag" },
  { keys: "Enter", what: "confirm single (last → next tab)", brief: true, short: "confirm" },
  { keys: "?", what: "toggle this help" },
];

export const VIDEO_KEYS: Shortcut[] = [
  { keys: "j / k", what: "prev / next clip", brief: true, short: "clips" },
  { keys: "← / →", what: "seek ±10s" },
  { keys: "Space", what: "toggle keep/delete", brief: true, short: "keep/delete" },
  { keys: "l", what: "play / pause", brief: true, short: "pause" },
  { keys: "s", what: "star / unstar (star keeps)", brief: true, short: "star" },
  { keys: "1–9", what: "tag current clip" },
  { keys: "t", what: "cycle tag" },
  { keys: "Enter", what: "confirm clip (last → next tab)", brief: true, short: "confirm" },
  { keys: "?", what: "toggle this help" },
];

/** Only shown once the clip has suggested or user clips to step through. */
export const VIDEO_CLIP_KEYS: Shortcut[] = [
  { keys: "n / p", what: "next / prev clip marker", brief: true, short: "markers" },
];

/** Only shown when the clip can be edited. */
export const VIDEO_EDIT_KEYS: Shortcut[] = [
  { keys: "i / o", what: "set clip in / out", brief: true, short: "in / out" },
  { keys: "x", what: "delete selected clip" },
  { keys: "u", what: "undo" },
];

export const FAVORITES_KEYS: Shortcut[] = [
  { keys: "h/j/k/l", what: "move focus", brief: true, short: "move" },
  { keys: "d", what: "unfavorite and mark for deletion", brief: true, short: "unstar + delete" },
  { keys: "s", what: "unfavorite", brief: true, short: "unstar" },
  { keys: "?", what: "toggle this help" },
];

export function brief(...lists: Shortcut[][]): Shortcut[] {
  return lists.flat().filter((s) => s.brief);
}
