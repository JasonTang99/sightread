/** Every keyboard shortcut, written once.
 *
 * They used to be written twice, in two visual languages that had already
 * drifted. HelpOverlay held a table for Clusters and Singles only; each view
 * carried its own one-line crib in the toolbar — ClusterView's ran to thirteen
 * items — and the Videos and Favorites cribs had no entry in the overlay at
 * all. Nothing kept the two in step, and the bindings they describe lived in a
 * third place again (the views' own keydown handlers), so a binding could be
 * changed in one of three places and look right in the other two.
 *
 * The lists here are now the bindings as well as the copy. Each entry names
 * the KeyboardEvent chords that fire it and an `id` the view must implement.
 * `useShortcuts` looks the event up in this list and calls that id; a handler
 * that still switched on `e.key` would be a fourth copy. `brief` is what the
 * toolbar shows: the handful you reach for without thinking. Everything else
 * is overlay-only. Splitting on `brief` rather than keeping two lists is the
 * point — an entry cannot exist in the crib and be missing from the reference,
 * and a key cannot be changed in a handler without going stale here, because
 * the handler no longer names keys.
 *
 * `?` is deliberately not in any view list. It was in all four, so the overlay
 * printed "toggle this help" four times, and the crib appends its own "?
 * all keys" already. It belongs to the app, not to a view — see APP_KEYS.
 */

export interface Chord {
  /** KeyboardEvent.key. Omit when `digit` is set. */
  key?: string;
  /** Match "1" through "9". */
  digit?: boolean;
  /** When set, require this Shift state. When omitted, Shift is ignored. */
  shift?: boolean;
  /** When true, require Ctrl (or Cmd). When omitted, Ctrl is ignored. */
  ctrl?: boolean;
}

export interface Bind {
  id: string;
  chords: Chord[];
}

export interface Shortcut {
  /** As the user would say it. Rendered in a <kbd>. */
  keys: string;
  what: string;
  /** Show in the view's toolbar crib as well as the overlay. */
  brief?: boolean;
  /** Crib wording, when the overlay's sentence is too long for one line.
   *  The overlay explains; the crib reminds. */
  short?: string;
  /** The chords that fire this row, and the action id the view implements.
   *  One display row can name two directions, so this is a list. Two rows
   *  may share an id when they are aliases for the same action (j/k and
   *  ←/→ on Singles both mean next/prev). */
  bind: Bind[];
}

export const CLUSTER_KEYS: Shortcut[] = [
  {
    keys: "← / →",
    what: "prev / next cluster",
    brief: true,
    short: "clusters",
    bind: [
      { id: "cluster-prev", chords: [{ key: "ArrowLeft" }] },
      { id: "cluster-next", chords: [{ key: "ArrowRight" }] },
    ],
  },
  {
    keys: "h / l",
    what: "move image focus left / right",
    bind: [
      { id: "cluster-focus-left", chords: [{ key: "h" }] },
      { id: "cluster-focus-right", chords: [{ key: "l" }] },
    ],
  },
  {
    keys: "j / k",
    what: "move image focus up / down",
    bind: [
      { id: "cluster-focus-down", chords: [{ key: "j" }] },
      { id: "cluster-focus-up", chords: [{ key: "k" }] },
    ],
  },
  {
    keys: "Space",
    what: "toggle focused image keep/delete",
    brief: true,
    short: "keep/delete",
    bind: [{ id: "cluster-toggle", chords: [{ key: " ", shift: false }] }],
  },
  {
    keys: "Shift+Space",
    what: "keep only the focused image (starred stay kept)",
    bind: [{ id: "cluster-keep-only", chords: [{ key: " ", shift: true }] }],
  },
  {
    keys: "1–9",
    what: "toggle image by rank number",
    bind: [{ id: "cluster-toggle-rank", chords: [{ digit: true }] }],
  },
  {
    keys: "n",
    what: "jump to next unreviewed cluster",
    bind: [{ id: "cluster-unreviewed", chords: [{ key: "n" }] }],
  },
  {
    keys: "Enter",
    what: "confirm cluster (last → next tab)",
    brief: true,
    short: "confirm",
    bind: [{ id: "cluster-confirm", chords: [{ key: "Enter" }] }],
  },
  {
    keys: "s",
    what: "star / unstar focused image (star keeps)",
    brief: true,
    short: "star",
    bind: [{ id: "cluster-star", chords: [{ key: "s" }] }],
  },
  {
    keys: "t",
    what: "cycle tag on focused image",
    bind: [{ id: "cluster-tag", chords: [{ key: "t" }] }],
  },
  {
    keys: "u",
    what: "undo last confirm",
    bind: [{ id: "cluster-undo", chords: [{ key: "u" }] }],
  },
];

export const SINGLES_KEYS: Shortcut[] = [
  {
    keys: "j / k",
    what: "move image focus up / down",
    brief: true,
    short: "move",
    bind: [
      { id: "singles-next", chords: [{ key: "j" }, { key: "ArrowDown" }] },
      { id: "singles-prev", chords: [{ key: "k" }, { key: "ArrowUp" }] },
    ],
  },
  {
    keys: "← / →",
    what: "prev / next single",
    bind: [
      { id: "singles-prev", chords: [{ key: "ArrowLeft" }] },
      { id: "singles-next", chords: [{ key: "ArrowRight" }] },
    ],
  },
  {
    keys: "Space",
    what: "toggle focused image keep/delete",
    brief: true,
    short: "keep/delete",
    bind: [{ id: "singles-toggle", chords: [{ key: " " }] }],
  },
  {
    keys: "s",
    what: "star / unstar (star keeps)",
    brief: true,
    short: "star",
    bind: [{ id: "singles-star", chords: [{ key: "s" }] }],
  },
  {
    keys: "1–9",
    what: "tag current image",
    bind: [{ id: "singles-tag-slot", chords: [{ digit: true }] }],
  },
  {
    keys: "t",
    what: "cycle tag",
    bind: [{ id: "singles-tag-cycle", chords: [{ key: "t" }] }],
  },
  {
    keys: "Enter",
    what: "confirm single (last → next tab)",
    brief: true,
    short: "confirm",
    bind: [{ id: "singles-confirm", chords: [{ key: "Enter" }] }],
  },
];

export const VIDEO_KEYS: Shortcut[] = [
  {
    keys: "j / k",
    what: "prev / next clip",
    brief: true,
    short: "clips",
    bind: [
      { id: "video-next", chords: [{ key: "j" }, { key: "ArrowDown" }] },
      { id: "video-prev", chords: [{ key: "k" }, { key: "ArrowUp" }] },
    ],
  },
  {
    keys: "← / →",
    what: "seek ±10s",
    bind: [
      { id: "video-seek-back", chords: [{ key: "ArrowLeft" }] },
      { id: "video-seek-fwd", chords: [{ key: "ArrowRight" }] },
    ],
  },
  {
    keys: "Space",
    what: "toggle keep/delete",
    brief: true,
    short: "keep/delete",
    bind: [{ id: "video-toggle", chords: [{ key: " " }] }],
  },
  {
    keys: "l",
    what: "play / pause",
    brief: true,
    short: "pause",
    bind: [{ id: "video-pause", chords: [{ key: "l" }] }],
  },
  {
    keys: "s",
    what: "star / unstar (star keeps)",
    brief: true,
    short: "star",
    bind: [{ id: "video-star", chords: [{ key: "s" }] }],
  },
  {
    keys: "1–9",
    what: "tag current clip",
    bind: [{ id: "video-tag-slot", chords: [{ digit: true }] }],
  },
  {
    keys: "t",
    what: "cycle tag",
    bind: [{ id: "video-tag-cycle", chords: [{ key: "t" }] }],
  },
  {
    keys: "Enter",
    what: "confirm clip (last → next tab)",
    brief: true,
    short: "confirm",
    bind: [{ id: "video-confirm", chords: [{ key: "Enter" }] }],
  },
];

/** Only shown once the clip has suggested or user clips to step through. */
export const VIDEO_CLIP_KEYS: Shortcut[] = [
  {
    keys: "n / p",
    what: "next / prev clip marker",
    brief: true,
    short: "markers",
    bind: [
      { id: "video-marker-next", chords: [{ key: "n" }] },
      { id: "video-marker-prev", chords: [{ key: "p" }] },
    ],
  },
];

/** Only shown when the clip can be edited. */
export const VIDEO_EDIT_KEYS: Shortcut[] = [
  {
    keys: "i / o",
    what: "set clip in / out",
    brief: true,
    short: "in / out",
    bind: [
      { id: "video-in", chords: [{ key: "i" }] },
      { id: "video-out", chords: [{ key: "o" }] },
    ],
  },
  {
    keys: "x",
    what: "delete selected clip",
    // Backspace was never written down; it has always done the same as x.
    bind: [{ id: "video-clip-delete", chords: [{ key: "x" }, { key: "Backspace" }] }],
  },
  {
    keys: "u",
    what: "undo",
    bind: [{ id: "video-undo", chords: [{ key: "u" }] }],
  },
];

export const FAVORITES_KEYS: Shortcut[] = [
  {
    keys: "h/j/k/l",
    what: "move focus",
    brief: true,
    short: "move",
    bind: [
      { id: "fav-right", chords: [{ key: "l" }, { key: "ArrowRight" }] },
      { id: "fav-left", chords: [{ key: "h" }, { key: "ArrowLeft" }] },
      { id: "fav-down", chords: [{ key: "j" }, { key: "ArrowDown" }] },
      { id: "fav-up", chords: [{ key: "k" }, { key: "ArrowUp" }] },
    ],
  },
  {
    keys: "d",
    what: "unfavorite and mark for deletion",
    brief: true,
    short: "unstar + delete",
    bind: [{ id: "fav-delete", chords: [{ key: "d" }] }],
  },
  {
    keys: "s",
    what: "unfavorite",
    brief: true,
    short: "unstar",
    bind: [{ id: "fav-unstar", chords: [{ key: "s" }] }],
  },
];

/** App-level, not a view. The overlay footer names these; the crib does not. */
export const APP_KEYS: Shortcut[] = [
  { keys: "?", what: "toggle this help", bind: [{ id: "app-help", chords: [{ key: "?" }] }] },
  { keys: "Esc", what: "close help", bind: [{ id: "app-help-close", chords: [{ key: "Escape" }] }] },
  {
    keys: "Ctrl+Z",
    what: "undo last confirm",
    bind: [{ id: "app-undo", chords: [{ key: "z", ctrl: true, shift: false }] }],
  },
  {
    keys: "Ctrl+Shift+Z",
    what: "redo",
    // Shift turns the key into "Z" on most layouts, but not every browser
    // agrees, so both spellings count.
    bind: [{ id: "app-redo", chords: [{ key: "Z", ctrl: true, shift: true }, { key: "z", ctrl: true, shift: true }] }],
  },
];

export function brief(...lists: Shortcut[][]): Shortcut[] {
  return lists.flat().filter((s) => s.brief);
}

export function chordMatch(e: KeyboardEvent, c: Chord): boolean {
  if (c.digit) {
    if (!/^[1-9]$/.test(e.key)) return false;
  } else if (e.key !== c.key) {
    return false;
  }
  if (c.shift !== undefined && e.shiftKey !== c.shift) return false;
  if (c.ctrl && !(e.ctrlKey || e.metaKey)) return false;
  return true;
}

/** First matching bind in list order. Undefined if the event is not ours. */
export function matchBind(e: KeyboardEvent, lists: Shortcut[]): Bind | undefined {
  for (const s of lists) {
    for (const b of s.bind) {
      if (b.chords.some((c) => chordMatch(e, c))) return b;
    }
  }
}

/** Unique action ids, in first-seen order. Two display rows may share an id. */
export function bindIds(lists: Shortcut[]): string[] {
  const seen = new Set<string>();
  const ids: string[] = [];
  for (const s of lists) {
    for (const b of s.bind) {
      if (!seen.has(b.id)) {
        seen.add(b.id);
        ids.push(b.id);
      }
    }
  }
  return ids;
}

export function typingTarget(e: KeyboardEvent, alsoButtons = false): boolean {
  const t = e.target;
  if (
    t instanceof HTMLInputElement ||
    t instanceof HTMLSelectElement ||
    t instanceof HTMLTextAreaElement
  ) {
    return true;
  }
  return alsoButtons && t instanceof HTMLButtonElement;
}
