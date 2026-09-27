import type { StatusFilter } from "../timeline";

interface Props {
  filter: StatusFilter;
  onFilter: (f: StatusFilter) => void;
  devices: string[];
  device: string;
  onDevice: (d: string) => void;
  selectedDate: string;
  onDate: (d: string) => void;
  totalPhotoCount: number;
  visibleDates: string[];
  countByDate: Record<string, number>;
}

export function TimelineSidebar({
  filter,
  onFilter,
  devices,
  device,
  onDevice,
  selectedDate,
  onDate,
  totalPhotoCount,
  visibleDates,
  countByDate,
}: Props) {
  return (
    <div className="w-44 shrink-0 border-r border-gray-200 overflow-y-auto bg-white">
      {/* Filter toggle */}
      <div className="px-2 pt-2 pb-1 border-b border-gray-100">
        <div className="flex rounded overflow-hidden border border-gray-200 text-xs">
          <button
            onClick={() => onFilter("all")}
            className={`flex-1 py-1 transition-colors ${filter === "all" ? "bg-blue-600 text-white" : "text-gray-500 hover:bg-gray-50"}`}
          >
            All
          </button>
          <button
            onClick={() => onFilter("keep")}
            className={`flex-1 py-1 transition-colors ${filter === "keep" ? "bg-green-600 text-white" : "text-gray-500 hover:bg-gray-50"}`}
          >
            Keep
          </button>
          <button
            onClick={() => onFilter("delete")}
            className={`flex-1 py-1 transition-colors ${filter === "delete" ? "bg-red-600 text-white" : "text-gray-500 hover:bg-gray-50"}`}
          >
            Delete
          </button>
        </div>
      </div>

      {devices.length > 1 && (
        <div className="px-2 pt-2 pb-1 border-b border-gray-100">
          <div className="text-[10px] uppercase tracking-wide text-gray-400 mb-1">Device</div>
          <div className="flex flex-wrap gap-1">
            {["all", ...devices].map((d) => (
              <button
                key={d}
                onClick={() => onDevice(d)}
                className={`px-1.5 py-0.5 text-[11px] rounded border transition-colors ${
                  device === d
                    ? "bg-blue-600 text-white border-blue-600"
                    : "text-gray-500 border-gray-200 hover:bg-gray-50"
                }`}
              >
                {d === "all" ? "All" : d}
              </button>
            ))}
          </div>
        </div>
      )}

      <button
        onClick={() => onDate("all")}
        className={`w-full text-left px-3 py-2 text-sm transition-colors ${
          selectedDate === "all" ? "bg-blue-50 text-blue-700 font-medium" : "text-gray-600 hover:bg-gray-50"
        }`}
      >
        All photos
        <span className="ml-1 text-xs text-gray-400">({totalPhotoCount})</span>
      </button>

      {visibleDates.map((d) => (
        <button
          key={d}
          onClick={() => onDate(d)}
          className={`w-full text-left px-3 py-1.5 text-xs transition-colors ${
            selectedDate === d ? "bg-blue-50 text-blue-700 font-medium" : "text-gray-500 hover:bg-gray-50"
          }`}
        >
          {d}
          <span className="ml-1 text-gray-400">({countByDate[d] ?? 0})</span>
        </button>
      ))}
    </div>
  );
}
