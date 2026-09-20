import { Kbd } from "./ui";
import {
  CLUSTER_KEYS,
  FAVORITES_KEYS,
  SINGLES_KEYS,
  VIDEO_CLIP_KEYS,
  VIDEO_EDIT_KEYS,
  VIDEO_KEYS,
  type Shortcut,
} from "../shortcuts";

interface Props {
  onClose: () => void;
}

/** The full reference. The key lists live in ../shortcuts and are shared with
 *  the toolbar cribs, so the two can no longer describe different keys — which
 *  they did: this overlay covered Clusters and Singles only, while the Videos
 *  and Favorites tabs had cribs naming keys that appeared nowhere in here. */
const SECTIONS: [string, Shortcut[]][] = [
  ["Clusters", CLUSTER_KEYS],
  ["Singles", SINGLES_KEYS],
  ["Videos", [...VIDEO_KEYS, ...VIDEO_CLIP_KEYS, ...VIDEO_EDIT_KEYS]],
  ["Favorites", FAVORITES_KEYS],
];

function Section({ title, keys }: { title: string; keys: Shortcut[] }) {
  return (
    <div>
      <p className="text-xs font-medium text-gray-500 uppercase tracking-wide mb-2">{title}</p>
      <table className="w-full text-xs">
        <tbody>
          {keys.map(({ keys: k, what }) => (
            <tr key={k} className="border-b border-gray-50 last:border-0">
              <td className="py-1 pr-3 align-top whitespace-nowrap">
                <Kbd>{k}</Kbd>
              </td>
              <td className="py-1 text-gray-600">{what}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function HelpOverlay({ onClose }: Props) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-lg shadow-xl border border-gray-200 p-6 w-[720px] max-w-full max-h-full overflow-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-semibold text-gray-900">Keyboard shortcuts</h2>
          <button
            onClick={onClose}
            aria-label="Close"
            className="text-gray-400 hover:text-gray-600 text-lg leading-none"
          >
            ×
          </button>
        </div>
        {/* Two columns at this width, one on a narrow window. Four sections in
            a fixed two-column grid left Favorites stranded under a tall
            Clusters list. */}
        <div className="grid gap-x-8 gap-y-5 sm:grid-cols-2">
          {SECTIONS.map(([title, keys]) => (
            <Section key={title} title={title} keys={keys} />
          ))}
        </div>
      </div>
    </div>
  );
}
