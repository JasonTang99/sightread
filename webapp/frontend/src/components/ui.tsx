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
 * pass: five accent hues in the tab strip alone.
 *
 * These deliberately take `children` and a few booleans rather than a
 * className grab-bag. A prop that is a class string is the duplication coming
 * back through the front door.
 */
import type { ButtonHTMLAttributes, ReactNode } from "react";
import type { Shortcut } from "../shortcuts";

/** The one accent. `tone` exists for the three places that carry meaning. */
export type Tone = "accent" | "done" | "star";

const TAB_TONE: Record<Tone, string> = {
  accent: "border-blue-600 text-blue-600",
  done: "border-green-600 text-green-600",
  star: "border-yellow-500 text-yellow-600",
};

export function Tab({
  active,
  tone = "accent",
  children,
  ...rest
}: { active: boolean; tone?: Tone; children: ReactNode } & ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      {...rest}
      aria-current={active ? "page" : undefined}
      className={`px-3 py-1 text-sm border-b-2 -mb-px transition-colors ${
        active
          ? TAB_TONE[tone]
          : "border-transparent text-gray-500 hover:text-gray-800"
      }`}
    >
      {children}
    </button>
  );
}

/** A count beside a tab label. Quieter than the label, and not a second
 *  colour: the number is never the thing you are looking for first.
 *
 *  No margin, and callers write the separating space into the JSX text. The
 *  tabs are addressed by their rendered label — `get_by_text("Clusters (3)")`,
 *  `get_by_role("button", name="Videos (1)")` — and a margin instead of a real
 *  space renders "Clusters(3)", which matches neither. */
export function TabCount({ children }: { children: ReactNode }) {
  return <span className="text-gray-400 tabular-nums">({children})</span>;
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

/** Passive information. Grey by default; `tone` only where it carries meaning.
 *
 *  `className` is appended rather than accepted and dropped. Spreading `rest`
 *  and then writing `className=` after it silently discards whatever the
 *  caller passed, which is how the progress readout lost its flex layout the
 *  first time this was wired up — it rendered, it just stacked. */
export function Status({
  tone,
  className = "",
  children,
  ...rest
}: { tone?: "done"; children: ReactNode } & React.HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      {...rest}
      className={`text-xs tabular-nums ${
        tone === "done" ? "text-green-600" : "text-gray-400"
      } ${className}`}
    >
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
  ...rest
}: { checked: boolean; label: string } & ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <span className="flex items-center gap-1.5">
      <span className="text-xs text-gray-500">{label}</span>
      <button
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

/** A thin rule between groups in a single-row header. Cheaper than a border
 *  on every child and it survives items appearing and disappearing, which
 *  most of the header's contents do. */
export function Divider() {
  return <span aria-hidden className="w-px h-4 bg-gray-200 shrink-0" />;
}
