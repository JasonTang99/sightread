import type { FsListing } from "../types";
import { EtaTag } from "./picker";

interface Props {
  listing: FsListing | null;
  selected: string | null;
  onSelect: (path: string) => void;
  onBrowse: (path?: string) => void;
  onClose: () => void;
}

export function FolderBrowser({ listing, selected, onSelect, onBrowse, onClose }: Props) {
  return (
    <div className="bg-white border border-gray-200 rounded-lg overflow-hidden flex-1">
      <div className="px-3 py-2 border-b border-gray-100 flex items-center gap-2">
        <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide">Browse</p>
        {listing && (
          <p className="flex-1 text-xs text-gray-400 truncate font-mono">{listing.path}</p>
        )}
        <button
          onClick={onClose}
          className="ml-auto text-xs text-gray-400 hover:text-gray-600 shrink-0"
          title="Close browser"
        >
          ✕
        </button>
      </div>

      {listing && (
        <>
          {listing.parent && (
            <button
              onClick={() => onBrowse(listing.parent!)}
              className="w-full text-left px-3 py-2 text-xs text-gray-500 hover:bg-gray-50 flex items-center gap-2 border-b border-gray-100"
            >
              <span>↑</span>
              <span className="font-mono">..</span>
            </button>
          )}
          <ul className="divide-y divide-gray-50 max-h-[400px] overflow-y-auto">
            {listing.entries.map((entry) => {
              const isSelected = selected === entry.path;
              return (
                <li key={entry.path}>
                  <button
                    onClick={() => onSelect(entry.path)}
                    onDoubleClick={() => entry.is_dir && onBrowse(entry.path)}
                    className={`w-full text-left px-3 py-2 flex items-center gap-2 hover:bg-gray-50 transition-colors border-l-2 ${
                      isSelected ? "border-blue-500 bg-blue-50" : "border-transparent"
                    }`}
                  >
                    <span className="text-base leading-none">
                      {entry.image_count > 0 ? "📸" : "📁"}
                    </span>
                    <span className="flex-1 min-w-0 flex flex-col items-start">
                      <span className="w-full text-sm text-gray-700 truncate">{entry.name}</span>
                      <EtaTag etaS={entry.eta_s} pending={entry.pending_count} />
                    </span>
                    {entry.image_count > 0 && (
                      <span className="text-xs text-gray-400 shrink-0">{entry.image_count}</span>
                    )}
                    {entry.is_dir && (
                      <button
                        onClick={(e) => { e.stopPropagation(); onBrowse(entry.path); }}
                        className="text-xs text-gray-400 hover:text-gray-600 shrink-0 px-1"
                        title="Open folder"
                      >
                        →
                      </button>
                    )}
                  </button>
                </li>
              );
            })}
            {listing.entries.length === 0 && (
              <li className="px-3 py-4 text-xs text-gray-400">No subdirectories.</li>
            )}
          </ul>
        </>
      )}
    </div>
  );
}
