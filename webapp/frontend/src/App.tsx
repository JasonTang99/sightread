import { useCallback, useEffect, useState } from "react";
import { ClusterView } from "./components/ClusterView";
import { HelpOverlay } from "./components/HelpOverlay";
import { ProjectPicker } from "./components/ProjectPicker";
import { SingletonsView } from "./components/SingletonsView";
import { TrashPanel } from "./components/TrashPanel";
import type { AppState } from "./types";

export default function App() {
  const [state, setState] = useState<AppState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<"clusters" | "singles">("clusters");
  const [undoing, setUndoing] = useState(false);
  const [showHelp, setShowHelp] = useState(false);

  const reload = useCallback(async () => {
    try {
      const res = await fetch("/api/state");
      if (!res.ok) throw new Error(`Server error ${res.status}`);
      const data: AppState = await res.json();
      setState(data);
      if (data.clusters && data.clusters.length === 0 && data.singletons && data.singletons.length > 0) {
        setTab("singles");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { reload(); }, [reload]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement || e.target instanceof HTMLTextAreaElement) return;
      if (e.key === "?") { e.preventDefault(); setShowHelp((s) => !s); }
      if (e.key === "Escape") setShowHelp(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const handleUndo = async () => {
    setUndoing(true);
    try {
      const res = await fetch("/api/undo", { method: "POST" });
      if (!res.ok) throw new Error(`Undo failed: ${res.status}`);
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setUndoing(false);
    }
  };

  const handleChangeProject = async () => {
    // Clear active project server-side by reloading — picker shows when no_project
    setState(null);
    setLoading(false);
  };

  if (loading && !state) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <p className="text-sm text-gray-400">Loading…</p>
      </div>
    );
  }

  // No project open — show picker
  if (!state || state.no_project) {
    return <ProjectPicker onProjectOpened={reload} />;
  }

  const hasClusters = (state.clusters?.length ?? 0) > 0;
  const hasSingles = (state.singletons?.length ?? 0) > 0;

  return (
    <div>
      {showHelp && <HelpOverlay onClose={() => setShowHelp(false)} />}
      <header className="bg-white border-b border-gray-200 px-3 py-1.5 flex items-center gap-3">
        <button
          onClick={handleChangeProject}
          className="text-sm font-semibold text-gray-900 mr-2 hover:text-blue-600 transition-colors"
          title="Change project"
        >
          Sightread
        </button>

        {hasClusters && (
          <button
            onClick={() => setTab("clusters")}
            className={`px-3 py-1 text-sm border-b-2 transition-colors ${
              tab === "clusters" ? "border-blue-600 text-blue-600" : "border-transparent text-gray-500 hover:text-gray-700"
            }`}
          >
            Clusters ({state.clusters.length})
          </button>
        )}
        {hasSingles && (
          <button
            onClick={() => setTab("singles")}
            className={`px-3 py-1 text-sm border-b-2 transition-colors ${
              tab === "singles" ? "border-blue-600 text-blue-600" : "border-transparent text-gray-500 hover:text-gray-700"
            }`}
          >
            Singles ({state.singletons.length})
          </button>
        )}

        <div className="ml-auto flex items-center gap-2">
          {state.pending_delete_count > 0 && (
            <span className="text-xs text-gray-400">{state.pending_delete_count} pending</span>
          )}
          <button
            onClick={handleUndo}
            disabled={!state.undo_available || undoing}
            className="px-2 py-1 text-xs border border-gray-200 rounded text-gray-600 hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {undoing ? "…" : "↶ Undo"}
          </button>
        </div>
      </header>

      <main className="px-2 pt-2">
        {error && (
          <div className="mb-2 p-2 bg-red-50 border border-red-200 rounded text-red-700 text-sm flex items-start justify-between gap-4">
            <span>{error}</span>
            <button onClick={() => setError(null)} className="text-red-400 hover:text-red-600 text-xs underline shrink-0">Dismiss</button>
          </div>
        )}

        {!hasClusters && !hasSingles ? (
          <div className="bg-white rounded border border-gray-200 px-6 py-12 text-center">
            <p className="text-2xl mb-2">🎉</p>
            <p className="text-gray-700 font-medium">All done!</p>
            <p className="text-sm text-gray-500 mt-1">
              {state.pending_delete_count > 0
                ? `${state.pending_delete_count} images pending deletion — run scripts/delete_marked.py`
                : "Nothing pending."}
            </p>
          </div>
        ) : (
          <>
            {tab === "clusters" && hasClusters && (
              <ClusterView clusters={state.clusters} onRefresh={reload} onError={setError} onUndo={handleUndo} />
            )}
            {tab === "singles" && hasSingles && (
              <SingletonsView
                singletons={state.singletons}
                threshold={state.singleton_delete_threshold}
                onRefresh={reload}
                onError={setError}
              />
            )}
            <TrashPanel pendingCount={state.pending_delete_count} onRefresh={reload} onError={setError} />
          </>
        )}
      </main>
    </div>
  );
}
