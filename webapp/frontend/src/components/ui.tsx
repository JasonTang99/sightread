/** The app's shared chrome: one definition per control instead of six.
 *
 * Before this file the header's tab button carried the same 140-character
 * template literal six times, and `px-2 py-1 text-xs border border-gray-200
 * rounded …` appeared in three components that had drifted apart on padding.
 * Every one of those is a place a later edit changes five of six.
 *
 * Colour rule, because the sprawl was the other half of the mess: blue is
 * "where you are", and nothing else. Green, yellow and red mean finished,
 * favourite and destructive — never decoration. Everything that is merely
 * information is grey. Counting utilities across the components before this
 * pass: five accent hues in the tab strip alone. Exception: picker status
 * pills (`Pill`) — green ready, yellow stale, grey not-run, blue running,
 * purple done — and the slate ETA chip. A trip list is scanned by state.
 *
 * These deliberately take `children` and a few booleans rather than a
 * className grab-bag. A prop that is a class string is the duplication coming
 * back through the front door.
 */
import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from "react";
import type { Shortcut } from "../shortcuts";

/** The one accent. `tone` exists for the three places that carry meaning. */
export type Tone = "accent" | "done" | "star";

const TAB_TONE: Record<Tone, string> = {
  accent: "border-blue-600 text-blue-600",
  done: "border-green-600 text-green-600",
  star: "border-yellow-500 text-yellow-600",
};

/** A header tab. Icon-only to keep the header to one short row, so the
 *  name lives in `label`: it is the tooltip and the accessible name, which
 *  is what tests address tabs by — `get_by_role("button", name="Videos (0/1)")`. */
export function Tab({
  active,
  tone = "accent",
  label,
  children,
  ...rest
}: { active: boolean; tone?: Tone; label: string; children: ReactNode } & ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      {...rest}
      aria-label={label}
      title={label}
      aria-current={active ? "page" : undefined}
      className={`px-2 flex items-center gap-1 text-sm border-b-2 transition-colors ${
        active
          ? TAB_TONE[tone]
          : "border-transparent text-gray-500 hover:text-gray-800"
      }`}
    >
      {children}
    </button>
  );
}

const ICON_PATHS: Record<string, ReactNode> = {
  home: <path d="M3 10.5 12 3l9 7.5M5 9v11h5v-6h4v6h5V9" />,
  clusters: (
    <>
      <rect x="7" y="3" width="14" height="14" rx="2" />
      <path d="M3 7v12a2 2 0 0 0 2 2h12" />
    </>
  ),
  singles: (
    <>
      <rect x="3" y="3" width="18" height="18" rx="2" />
      <circle cx="9" cy="9" r="2" />
      <path d="m21 15-5-5L5 21" />
    </>
  ),
  videos: (
    <>
      <rect x="2" y="5" width="14" height="14" rx="2" />
      <path d="m22 8-6 4 6 4V8z" />
    </>
  ),
  favorites: <path d="m12 2 3.1 6.3 6.9 1-5 4.9 1.2 6.8L12 17.8 5.8 21l1.2-6.8-5-4.9 6.9-1z" />,
  timeline: (
    <>
      <rect x="3" y="4" width="18" height="18" rx="2" />
      <path d="M16 2v4M8 2v4M3 10h18" />
    </>
  ),
  finish: <path d="M4 22V4m0 0h13l-2 4 2 4H4" />,
  unreviewed: <path d="M5 12h12m-5-6 6 6-6 6M21 5v14" />,
};

/** Line icons, stroked in the current text colour. Inline rather than a
 *  package: seven glyphs do not justify a dependency. */
export function Icon({ name, className = "w-4 h-4" }: { name: keyof typeof ICON_PATHS; className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      className={className}
    >
      {ICON_PATHS[name]}
    </svg>
  );
}

/** A count beside a tab icon. Quieter than the icon, and not a second
 *  colour: the number is never the thing you are looking for first. */
export function TabCount({ children }: { children: ReactNode }) {
  return <span className="text-xs text-gray-400 tabular-nums">{children}</span>;
}

export function Button({
  size = "sm",
  children,
  ...rest
}: { size?: "sm" | "md"; children: ReactNode } & ButtonHTMLAttributes<HTMLButtonElement>) {
  const pad = size === "md" ? "px-3 py-1.5 text-sm" : "px-2 py-1 text-xs";
  return (
    <button
      {...rest}
      className={`${pad} border border-gray-200 rounded text-gray-600 transition-colors hover:bg-gray-50 hover:border-gray-300 disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-transparent disabled:hover:border-gray-200`}
    >
      {children}
    </button>
  );
}

/** Picker status label and ETA tag. The colour-rule exception: these stay
 *  filled pills so a list of trips can be scanned by state. `className` is
 *  appended, same reason as `Status`. */
export type PillTone = "ready" | "stale" | "never_run" | "running" | "done" | "eta";

const PILL_CLASS: Record<PillTone, string> = {
  ready: "text-xs px-1.5 py-0.5 rounded font-medium shrink-0 bg-green-100 text-green-700",
  stale: "text-xs px-1.5 py-0.5 rounded font-medium shrink-0 bg-yellow-100 text-yellow-700",
  never_run: "text-xs px-1.5 py-0.5 rounded font-medium shrink-0 bg-gray-100 text-gray-500",
  running: "text-xs px-1.5 py-0.5 rounded font-medium shrink-0 bg-blue-100 text-blue-600",
  done: "text-xs px-1.5 py-0.5 rounded font-medium shrink-0 bg-purple-100 text-purple-700",
  eta: "inline-block mt-0.5 text-[10px] leading-4 px-1.5 rounded bg-slate-100 text-slate-500",
};

export function Pill({
  tone,
  className = "",
  children,
  ...rest
}: { tone: PillTone; children: ReactNode } & React.HTMLAttributes<HTMLSpanElement>) {
  return (
    <span {...rest} className={`${PILL_CLASS[tone]} ${className}`}>
      {children}
    </span>
  );
}

/** The skip-reviewed switch.
 *
 * Green-on / red-off was chosen deliberately when this replaced a confusing
 * "Skip reviewed / Skipping reviewed" button, so the colours stay. What
 * changes is that the off state is no longer `bg-red-400` — a saturated red
 * sitting permanently in the header reads as an error rather than an off
 * switch, and red elsewhere in this app means a delete. Off is now grey with
 * a red dot, so the signal survives without the alarm.
 */
export function Switch({
  checked,
  label,
  compact = false,
  ...rest
}: { checked: boolean; label: string; compact?: boolean } & ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <span className="flex items-center gap-1.5">
      {!compact && <span className="text-xs text-gray-500">{label}</span>}
      <button
        aria-label={label}
        {...rest}
        role="switch"
        aria-checked={checked}
        className={`relative w-9 h-5 rounded-full transition-colors shrink-0 ${
          checked ? "bg-green-500" : "bg-gray-300"
        }`}
      >
        <span
          className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full shadow transition-transform ${
            checked ? "translate-x-4 bg-white" : "bg-red-400"
          }`}
        />
      </button>
    </span>
  );
}

/** A source checkbox. Compact enough to sit in the one header row.
 *  The label text is the accessible name tests click by. */
export function Check({
  checked,
  label,
  ...rest
}: { checked: boolean; label: string } & InputHTMLAttributes<HTMLInputElement>) {
  return (
    <label className="flex items-center gap-1 text-xs text-gray-600 whitespace-nowrap select-none">
      <input type="checkbox" checked={checked} className="h-3 w-3" {...rest} />
      {label}
    </label>
  );
}

/** A key, rendered the same way everywhere.
 *
 * There were two renderings before: a bordered chip in FavoritesView, and bare
 * monospace text in the help overlay's table. */
export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="px-1 py-0.5 text-[11px] font-mono font-medium bg-gray-100 border border-gray-300 rounded text-gray-700 whitespace-nowrap">
      {children}
    </kbd>
  );
}

/** The toolbar crib: the few keys worth having on screen while working.
 *
 * The bars this replaces were `text-gray-300` — around 1.5:1 on white, below
 * any legibility threshold — and ran to thirteen run-together items separated
 * by middots. They were simultaneously too faint to read and busy enough to be
 * visual noise, which is the worst of both. Keys are chips now, at a contrast
 * that can actually be read, and the long tail moved to `?`.
 *
 * `w-full order-last` puts it on its own line at the end of the toolbar rather
 * than inline among the controls. Reference material wedged between a pager
 * and a tag picker reads as one more thing to operate; chips made that worse
 * than the faint text did, because they are heavier. Its container needs
 * `flex-wrap` for this to land.
 */
export function ShortcutBar({ items }: { items: Shortcut[] }) {
  return (
    <span className="w-full order-last flex items-center gap-x-3 gap-y-1 flex-wrap text-[11px] text-gray-500 pt-1.5 mt-0.5 border-t border-gray-100">
      {items.map((s) => (
        <span key={s.keys} className="flex items-center gap-1">
          <Kbd>{s.keys}</Kbd>
          <span>{s.short ?? s.what}</span>
        </span>
      ))}
      <span className="flex items-center gap-1">
        <Kbd>?</Kbd>
        <span>all keys</span>
      </span>
    </span>
  );
}
