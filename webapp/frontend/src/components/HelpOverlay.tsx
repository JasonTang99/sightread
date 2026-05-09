interface Props {
  onClose: () => void;
}

const CLUSTER_KEYS = [
  ["h / l", "prev / next cluster"],
  ["j / k", "move image focus up / down"],
  ["Space", "toggle focused image keep/delete"],
  ["1–9", "toggle image by rank number"],
  ["K", "keep best (rank 1 only)"],
  ["Enter", "confirm cluster"],
  ["b / s", "skip cluster (no confirm)"],
  ["u", "undo last confirm"],
  ["?", "toggle this help"],
];

const SINGLES_KEYS = [
  ["j / k", "move image focus up / down"],
  ["Space", "toggle focused image keep/delete"],
  ["a", "keep all"],
  ["d", "select all bad (reset to threshold)"],
  ["Enter", "confirm singles"],
  ["?", "toggle this help"],
];

export function HelpOverlay({ onClose }: Props) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-lg shadow-xl border border-gray-200 p-6 w-[480px] max-w-full"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-semibold text-gray-900">Keyboard shortcuts</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-lg leading-none">×</button>
        </div>
        <div className="grid grid-cols-2 gap-6">
          <div>
            <p className="text-xs font-medium text-gray-500 uppercase tracking-wide mb-2">Clusters</p>
            <table className="w-full text-xs">
              <tbody>
                {CLUSTER_KEYS.map(([key, desc]) => (
                  <tr key={key} className="border-b border-gray-50">
                    <td className="py-1 pr-3 font-mono font-medium text-gray-700 whitespace-nowrap">{key}</td>
                    <td className="py-1 text-gray-500">{desc}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div>
            <p className="text-xs font-medium text-gray-500 uppercase tracking-wide mb-2">Singles</p>
            <table className="w-full text-xs">
              <tbody>
                {SINGLES_KEYS.map(([key, desc]) => (
                  <tr key={key} className="border-b border-gray-50">
                    <td className="py-1 pr-3 font-mono font-medium text-gray-700 whitespace-nowrap">{key}</td>
                    <td className="py-1 text-gray-500">{desc}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}
