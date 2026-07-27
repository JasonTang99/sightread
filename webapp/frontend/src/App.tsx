import { useCallback, useEffect, useRef, useState } from "react";
import { useWindowKeydown } from "./hooks/useWindowKeydown";
import { isDecided } from "./decisions";
import { ClusterView } from "./components/ClusterView";
import { FavoritesView } from "./components/FavoritesView";
import { HelpOverlay } from "./components/HelpOverlay";
import { ProjectPicker } from "./components/ProjectPicker";
import { SingletonsView } from "./components/SingletonsView";
import { TimelineView } from "./components/TimelineView";
import { TrashPanel } from "./components/TrashPanel";
import { VideoView } from "./components/VideoView";
import type { AppState, UserClipsMap, VideoHighlightsMap } from "./types";

export default function App() {
  const [state, setState] = useState<AppState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<"clusters" | "singles" | "videos" | "favorites" | "timeline">("clusters");
  const [undoing, setUndoing] = useState(false);
  const [showHelp, setShowHelp] = useState(false);
  const [videos, setVideos] = useState<string[]>([]);
  const [videoShotTimes, setVideoShotTimes] = useState<Record<string, string | null>>({});
  const [videoHighlights, setVideoHighlights] = useState<VideoHighlightsMap>({});
  const [videoUserClips, setVideoUserClips] = useState<UserClipsMap>({});

  const reload = useCallback(async () => {
    try {
      const res = await fetch("/api/state");
      if (!res.ok) throw new Error(`Server error ${res.status}`);
      const data: AppState = await res.json();
      setState(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { reload(); }, [reload]);

  const refetchVideos = useCallback(async () => {
    try {
      const r = await fetch("/api/videos");
      if (!r.ok) return;
      const d = await r.json();
      setVideos(d.paths ?? []);
      setVideoShotTimes(d.shot_times ?? {});
      setVideoHighlights(d.highlights ?? {});
      setVideoUserClips(d.user_clips ?? {});
    } catch {
      /* video list is non-critical */
    }
  }, []);

  // Fetch videos when project opens
  const projectOpen = state !== null && !state.no_project && !state.needs_pipeline;
  useEffect(() => {
    if (projectOpen) refetchVideos();
  }, [projectOpen, refetchVideos]);

  // Pick the landing tab once, on the first load of a project: whatever still
  // needs triage, or — when every cluster and single is already decided — the
  // timeline, which reviews the whole trip, videos included. Deliberately does
  // not re-run on later state changes; confirming the last cluster shouldn't
  // yank you off the tab you're working in.
  const landedRef = useRef(false);
  useEffect(() => {
    if (!state || state.no_project || landedRef.current) return;
    landedRef.current = true;
    const decisions = state.photo_decisions ?? {};
    const clusters = state.clusters ?? [];
    const singletons = state.singletons ?? [];
    if (clusters.length + singletons.length === 0) return;  // nothing curated yet
    if (clusters.some((c) => !isDecided(c, decisions))) return;  // stay on clusters
    setTab(singletons.some((c) => !isDecided(c, decisions)) ? "singles" : "timeline");
  }, [state]);

  // A different project gets its own landing decision.
  useEffect(() => { landedRef.current = false; }, [state?.no_project]);

  useEffect(() => {
    if (!error) return;
    const t = setTimeout(() => setError(null), 4000);
    return () => clearTimeout(t);
  }, [error]);

  useWindowKeydown((e) => {
    if (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement || e.target instanceof HTMLTextAreaElement) return;
    if (e.key === "?") { e.preventDefault(); setShowHelp((s) => !s); }
    if (e.key === "Escape") setShowHelp(false);
  });

  const toggleFavorite = useCallback(async (path: string) => {
    const res = await fetch("/api/favorite", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    if (!res.ok) throw new Error(`Favorite failed: ${res.status}`);
    await reload();
  }, [reload]);

  const handleUndo = async () => {
    setUndoing(true);
    try {
      const res = await fetch("/api/undo", { method: "POST" });
      if (!res.ok) throw new Error(`Undo failed: ${res.status}`);
      await reload();
      await refetchVideos();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setUndoing(false);
    }
  };

  const handleChangeProject = async () => {
    setState(null);
    setVideos([]);
    setLoading(false);
  };

  if (loading && !state) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <p className="text-sm text-gray-400">Loading…</p>
      </div>
    );
  }

  if (!state || state.no_project) {
    return <ProjectPicker onProjectOpened={reload} />;
  }

  const decisions = state.photo_decisions ?? {};
  const hasClusters = (state.clusters?.length ?? 0) > 0;
  const hasSingles = (state.singletons?.length ?? 0) > 0;
  const hasUnconfirmedSingles = (state.singletons ?? []).some((c) => !isDecided(c, decisions));
  const hasVideos = videos.length > 0;
  const favorites = state.favorites ?? [];
  const hasFavorites = favorites.length > 0;

  return (
    <div>
      {showHelp && <HelpOverlay onClose={() => setShowHelp(false)} />}
      <header className="bg-white border-b border-gray-200 px-3 py-1.5 flex items-center gap-3">
        <span className="text-sm font-semibold text-gray-900">Sightread</span>
        <button
          onClick={handleChangeProject}
          className="text-xs text-gray-400 hover:text-blue-600 transition-colors border border-gray-200 rounded px-2 py-0.5 hover:border-blue-400"
          title="Back to project picker"
        >
          ← Projects
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
        {hasVideos && (
          <button
            onClick={() => setTab("videos")}
            className={`px-3 py-1 text-sm border-b-2 transition-colors ${
              tab === "videos" ? "border-blue-600 text-blue-600" : "border-transparent text-gray-500 hover:text-gray-700"
            }`}
          >
            Videos ({videos.length})
          </button>
        )}
        {hasFavorites && (
          <button
            onClick={() => setTab("favorites")}
            className={`px-3 py-1 text-sm border-b-2 transition-colors ${
              tab === "favorites" ? "border-yellow-500 text-yellow-600" : "border-transparent text-gray-500 hover:text-gray-700"
            }`}
          >
            ★ Favorites ({favorites.length})
          </button>
        )}
        <button
          onClick={() => setTab("timeline")}
          className={`px-3 py-1 text-sm border-b-2 transition-colors ${
            tab === "timeline" ? "border-blue-600 text-blue-600" : "border-transparent text-gray-500 hover:text-gray-700"
          }`}
        >
          Timeline
        </button>

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

        {tab === "timeline" ? (
          // Timeline is where a finished project lands, so the apply-deletes
          // control has to live here too or it becomes unreachable.
          <div className="space-y-2">
            <TimelineView onError={setError} videos={videos} videoShotTimes={videoShotTimes} highlights={videoHighlights} />
            <TrashPanel pendingCount={state.pending_delete_count} onRefresh={reload} onError={setError} />
          </div>
        ) : tab === "favorites" ? (
          <FavoritesView favorites={favorites} onToggleFavorite={toggleFavorite} onRefresh={reload} onError={setError} />
        ) : tab === "videos" ? (
          <VideoView
            videos={videos}
            highlights={videoHighlights}
            userClips={videoUserClips}
            onError={setError}
            favorites={favorites}
            onToggleFavorite={toggleFavorite}
            onConfirmed={async () => { await reload(); await refetchVideos(); }}
          />
        ) : !hasClusters && !hasUnconfirmedSingles ? (
          <div className="space-y-2">
            <div className="bg-white rounded border border-gray-200 px-6 py-12 text-center">
              <p className="text-2xl mb-2">🎉</p>
              <p className="text-gray-700 font-medium">All done!</p>
              <p className="text-sm text-gray-500 mt-1">
                {state.pending_delete_count > 0
                  ? `${state.pending_delete_count} images pending deletion — apply them from the Trash panel below`
                  : "Nothing pending."}
              </p>
            </div>
            <TrashPanel pendingCount={state.pending_delete_count} onRefresh={reload} onError={setError} />
          </div>
        ) : (
          <>
            {tab === "clusters" && hasClusters && (
              <ClusterView clusters={state.clusters} decisions={decisions} favorites={favorites} onRefresh={reload} onError={setError} onUndo={handleUndo} onToggleFavorite={toggleFavorite} />
            )}
            {tab === "singles" && hasSingles && (
              <SingletonsView
                singletons={state.singletons}
                decisions={decisions}
                favorites={favorites}
                onRefresh={reload}
                onError={setError}
                onToggleFavorite={toggleFavorite}
              />
            )}
            <TrashPanel pendingCount={state.pending_delete_count} onRefresh={reload} onError={setError} />
          </>
        )}
      </main>
    </div>
  );
}
