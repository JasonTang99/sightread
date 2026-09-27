import type { ProjectEntry } from "../types";
import { EtaTag, STATUS_BADGE, projectKey } from "./picker";

interface ProjectListProps {
  title: string;
  testId: string;
  projects: ProjectEntry[];
  selected: string | null;
  onSelect: (key: string) => void;
  onOpen?: (project: ProjectEntry) => void;
}

export function ProjectList({ title, testId, projects, selected, onSelect, onOpen }: ProjectListProps) {
  return (
    <div data-testid={testId} className="bg-white border border-gray-200 rounded-lg overflow-hidden">
      <div className="px-3 py-2 border-b border-gray-100">
        <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide">{title}</p>
      </div>
      {projects.length === 0 ? (
        <p className="text-xs text-gray-400 px-3 py-4">No projects yet.</p>
      ) : (
        <ul className="max-h-[65vh] overflow-y-auto">
          {projects.map((p) => {
            const badge = p.done_at
              ? { label: "Done", cls: "bg-purple-100 text-purple-700" }
              : STATUS_BADGE[p.status];
            const key = projectKey(p);
            const isSelected = selected === key;
            return (
              <li key={key}>
                <button
                  onClick={() => onSelect(key)}
                  onDoubleClick={() => onOpen?.(p)}
                  className={`w-full text-left px-3 py-2.5 flex items-start gap-2 hover:bg-gray-50 transition-colors border-l-2 ${
                    isSelected ? "border-blue-500 bg-blue-50" : "border-transparent"
                  }`}
                >
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-gray-800 truncate">{p.display_name}</p>
                    <p className="text-xs text-gray-400 truncate">{p.folder}</p>
                    {p.image_count > 0 && (
                      <p className="text-xs text-gray-400">{p.image_count} images</p>
                    )}
                    <EtaTag etaS={p.eta_s} pending={p.pending_count} />
                  </div>
                  <span className={`text-xs px-1.5 py-0.5 rounded font-medium shrink-0 ${badge.cls}`}>
                    {badge.label}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
