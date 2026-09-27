import type { ProjectEntry } from "../types";

interface Props {
  selected: string;
  selectedRecent: ProjectEntry | null;
  busy: boolean;
  error: string | null;
  onOpen: (p: ProjectEntry) => void;
  onRun: (folder: string, subtrip?: string | null) => void;
}

export function PickerActionBar({
  selected,
  selectedRecent,
  busy,
  error,
  onOpen,
  onRun,
}: Props) {
  return (
    <div className="bg-white border border-gray-200 rounded-lg px-4 py-3 flex items-center gap-3">
      <div className="flex-1 min-w-0">
        <p className="text-xs text-gray-500">Selected</p>
        <p className="text-sm font-medium text-gray-800 truncate font-mono">
          {selectedRecent?.display_name ?? selected}
        </p>
      </div>

      {error && (
        <p className="text-xs text-red-600 shrink-0">{error}</p>
      )}

      {selectedRecent ? (
        <>
          {(selectedRecent.status === "ready" || selectedRecent.status === "stale") && (
            <button
              onClick={() => selectedRecent && onOpen(selectedRecent)}
              disabled={busy}
              className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50 shrink-0"
            >
              Open
            </button>
          )}
          <button
            onClick={() => selectedRecent && onRun(selectedRecent.folder, selectedRecent.subtrip)}
            disabled={busy}
            className="px-3 py-1.5 text-sm border border-gray-300 text-gray-700 rounded hover:bg-gray-50 disabled:opacity-50 shrink-0"
          >
            {selectedRecent.status === "stale" ? "Re-run Pipeline" : "Run Pipeline"}
          </button>
        </>
      ) : (
        <button
          onClick={() => selected && onRun(selected)}
          disabled={busy}
          className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50 shrink-0"
        >
          Run Pipeline
        </button>
      )}
    </div>
  );
}
