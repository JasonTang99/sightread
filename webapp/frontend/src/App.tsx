import { useCallback, useEffect, useRef, useState } from "react";
import { useShortcuts } from "./hooks/useWindowKeydown";
import { isDecided } from "./decisions";
import { ClusterView } from "./components/ClusterView";
import { FavoritesView } from "./components/FavoritesView";
import { HelpOverlay } from "./components/HelpOverlay";
import { ProjectPicker } from "./components/ProjectPicker";
import { SingletonsView } from "./components/SingletonsView";
import { TimelineView } from "./components/TimelineView";
import { FinishTripPanel } from "./components/FinishTripPanel";
import { VideoView } from "./components/VideoView";
import { Button, Divider, Status, Switch, Tab, TabCount } from "./components/ui";
import { APP_KEYS, typingTarget } from "./shortcuts";
import type { AppState, UserClipsMap, VideoHighlightsMap, VideoStatuses, VideoTagsState } from "./types";

export default function App() {
  const [state, setState] = useState<AppState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<"clusters" | "singles" | "videos" | "favorites" | "timeline" | "finish">("clusters");
  const [undoing, setUndoing] = useState(false);
  const [showHelp, setShowHelp] = useState(false);
  const [videos, setVideos] = useState<string[]>([]);
  const [videoStatuses, setVideoStatuses] = useState<VideoStatuses>({});
  const [videoShotTimes, setVideoShotTimes] = useState<Record<string, string | null>>({});
  const [videoHighlights, setVideoHighlights] = useState<VideoHighlightsMap>({});
  const [videoUserClips, setVideoUserClips] = useState<UserClipsMap>({});
  const [videoTags, setVideoTags] = useState<VideoTagsState>({ tags: [], assignments: {} });
  const [videosLoaded, setVideosLoaded] = useState(false);
  // Skip what has already been decided. A trip opened after its camera folder
  // was reviewed on its own adopts hundreds of decisions, and without this
  // confirm walks the user back through every one of them. Kept in
  // localStorage because it is a way of working, not a per-visit choice.
  const [hideReviewed, setHideReviewed] = useState(() => {
    try {
      return localStorage.getItem("sightread:hideReviewed") === "1";
    } catch {
      return false;
    }
  });
  // persist() writes a video decision without refetching /api/videos, because
  // a refetch would drop delete-marked clips from the Videos tab and break
  // space-toggle. The header still has to count those, so this set covers the
  // gap until the next successful refetch (timeline, undo, bulk confirm).
  const [justDecidedVideos, setJustDecidedVideos] = useState<Set<string>>(new Set());

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
      setVideoStatuses(d.statuses ?? {});
      setVideoShotTimes(d.shot_times ?? {});
      setVideoHighlights(d.highlights ?? {});
      setVideoUserClips(d.user_clips ?? {});
      setVideoTags(d.video_tags ?? { tags: [], assignments: {} });
      setJustDecidedVideos(new Set());
    } catch {
      /* video list is non-critical */
    } finally {
      // Even a failed fetch counts as loaded: the landing choice waits on this
      // flag, and a video list that never arrives must not strand the app on
      // the clusters tab.
      setVideosLoaded(true);
    }
  }, []);

  // Fetch videos when project opens
  const projectOpen = state !== null && !state.no_project && !state.needs_pipeline;
  useEffect(() => {
    if (projectOpen) refetchVideos();
  }, [projectOpen, refetchVideos]);

  // Video decisions are written as they're made in the Videos tab, so the
  // timeline needs fresh statuses each time it's opened or its tiles keep the
  // colours they had when the project loaded.
  useEffect(() => {
    if (projectOpen && tab === "timeline") refetchVideos();
  }, [projectOpen, tab, refetchVideos]);

  // Pick the landing tab once, on the first load of a project: whatever still
  // needs triage, and only the timeline once clusters, singles *and* videos are
  // all decided. Videos used to be left out, so reloading mid-way through the
  // Videos tab dropped you on the timeline with footage still undecided.
  // Deliberately does not re-run on later state changes; confirming a cluster
  // in the middle of the pile shouldn't yank you off the tab you're working
  // in. Enter on the *last* cluster/single/video is the explicit hop — see
  // `advanceTab`.
  const landedRef = useRef(false);
  useEffect(() => {
    if (!state || state.no_project || landedRef.current) return;
    // Wait for /api/videos: it lands separately from /api/state, and deciding
    // before it arrives would read every video as undecided.
    if (!videosLoaded) return;
    landedRef.current = true;
    const decisions = state.photo_decisions ?? {};
    const clusters = state.clusters ?? [];
    const singletons = state.singletons ?? [];
    if (clusters.length + singletons.length === 0) return;  // nothing curated yet
    if (clusters.some((c) => !isDecided(c, decisions))) return;  // stay on clusters
    if (singletons.some((c) => !isDecided(c, decisions))) { setTab("singles"); return; }
    const videosPending = videos.some((v) => (videoStatuses[v] ?? "undecided") === "undecided");
    setTab(videosPending ? "videos" : "timeline");
  }, [state, videosLoaded, videos, videoStatuses]);

  // A different project gets its own landing decision.
  useEffect(() => { landedRef.current = false; }, [state?.no_project]);

  useEffect(() => {
    try {
      localStorage.setItem("sightread:hideReviewed", hideReviewed ? "1" : "0");
    } catch {
      // private window, or site data blocked — the toggle still works, it just
      // starts off again next time.
    }
  }, [hideReviewed]);

  useEffect(() => {
    if (!error) return;
    const t = setTimeout(() => setError(null), 4000);
    return () => clearTimeout(t);
  }, [error]);

  useShortcuts(APP_KEYS, {
    "app-help": (e) => { e.preventDefault(); setShowHelp((s) => !s); },
    "app-help-close": () => setShowHelp(false),
  }, { ignore: typingTarget });

  // Blur on the way out: the views drive off window keydown and ignore events
  // aimed at a button, so leaving focus on the tab you just clicked makes the
  // first space or enter in that view do nothing at all.
  const selectTab = useCallback(
    (next: typeof tab) => (e: React.MouseEvent<HTMLButtonElement>) => {
      e.currentTarget.blur();
      setTab(next);
    },
    [],
  );

  const toggleFavorite = useCallback(async (path: string) => {
    const res = await fetch("/api/favorite", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    if (!res.ok) throw new Error(`Favorite failed: ${res.status}`);
    const data = await res.json();
    await reload();
    // A star is a keep. Photos pick that up from reload(); videos need the
    // status map patched because persist() is what usually writes it, and a
    // refetch would drop delete-marked neighbours from the Videos tab.
    if (data.favorited && videos.includes(path)) {
      setJustDecidedVideos((prev) => new Set(prev).add(path));
      setVideoStatuses((prev) => ({ ...prev, [path]: "keep" }));
    }
  }, [reload, videos]);

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
    setJustDecidedVideos(new Set());
    setTab("clusters");
    landedRef.current = false;
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
  // "Skip reviewed" does not filter these lists. It used to, and the tabs
  // shrank under you: counts dropped on every confirm and arrowing back to
  // something already decided was impossible. Everything stays browsable;
  // the toggle only changes where confirm lands (see the views).
  const clusterList = state.clusters ?? [];
  const singleList = state.singletons ?? [];
  const hasClusters = clusterList.length > 0;
  const hasSingles = singleList.length > 0;
  const hasUnconfirmedSingles = singleList.some((c) => !isDecided(c, decisions));
  // The video reviewer works through footage that isn't marked for deletion
  // yet; the timeline shows everything, marked included, so it can colour a
  // tile by its decision the way it does for photos.
  const reviewableVideos = videos.filter((v) => videoStatuses[v] !== "delete");
  const hasVideos = reviewableVideos.length > 0;
  const favorites = state.favorites ?? [];
  const hasFavorites = favorites.length > 0;

  // Jumps to whichever tab has the next thing still needing a decision —
  // clusters, then singles, then videos — same priority as the initial
  // landing choice above.
  const goToNextUnconfirmed = () => {
    if (clusterList.some((c) => !isDecided(c, decisions))) { setTab("clusters"); return; }
    if (singleList.some((c) => !isDecided(c, decisions))) { setTab("singles"); return; }
    const videosPending = videos.some((v) => (videoStatuses[v] ?? "undecided") === "undecided");
    if (videosPending) { setTab("videos"); return; }
    setTab("timeline");
  };

  // Enter on the last cluster / single / video opens the next visible tab,
  // skipping ones that have nothing to show. Finish stays last so a review
  // pass lands on the timeline before the export/delete panel.
  const advanceTab = () => {
    const visible: typeof tab[] = [];
    if (hasClusters) visible.push("clusters");
    if (hasSingles) visible.push("singles");
    if (hasVideos) visible.push("videos");
    if (hasFavorites) visible.push("favorites");
    visible.push("timeline", "finish");
    const i = visible.indexOf(tab);
    if (i >= 0 && i < visible.length - 1) setTab(visible[i + 1]);
  };

  // Session progress across everything that needs a decision. Clusters and
  // singles count as one unit each — that is how they are reviewed — and every
  // video counts, pending deletes included, since a delete mark is a decision.
  const totalUnits = clusterList.length + singleList.length + videos.length;
  const reviewedUnits =
    clusterList.filter((c) => isDecided(c, decisions)).length +
    singleList.filter((c) => isDecided(c, decisions)).length +
    videos.filter(
      (v) =>
        justDecidedVideos.has(v) ||
        (videoStatuses[v] && videoStatuses[v] !== "undecided"),
    ).length;

  return (
    <div>
      {showHelp && <HelpOverlay onClose={() => setShowHelp(false)} />}
      {/* Three groups, left to right: where you are, where you can go, and
          how the session is doing. They used to be one undivided run of up to
          thirteen items — back button, project name, six tabs, a switch, a
          progress bar, two counts and Undo — so the eye had no way to tell
          navigation from status from action. The dividers do that work; the
          items inside each group are unchanged in meaning. */}
      <header className="bg-white border-b border-gray-200 px-3 py-1.5 flex items-center gap-3">
        <button
          onClick={handleChangeProject}
          className="text-xs text-gray-400 hover:text-blue-600 transition-colors border border-gray-200 rounded px-2 py-0.5 hover:border-blue-400"
          title="Back to project picker"
        >
          ← Projects
        </button>
        {/* Which project this is. Every other project affordance is a verb —
            the picker button, the finish panel — so with several trips half
            reviewed there was nothing on screen that simply said where you
            are. The folder name is what the picker lists; the full path is in
            the tooltip, since trips from different drives can share a name. */}
        {state.folder && (
          <span
            className="text-sm text-gray-900 font-semibold truncate max-w-[16rem]"
            title={state.display_name ? `${state.display_name}\n${state.folder}` : state.folder}
            data-testid="project-name"
          >
            {state.display_name || state.folder.split("/").filter(Boolean).pop()}
          </span>
        )}

        <Divider />

        <nav className="flex items-center self-stretch" aria-label="Review sections">
          {hasClusters && (
            <Tab active={tab === "clusters"} onClick={selectTab("clusters")}>
              Clusters <TabCount>{clusterList.length}</TabCount>
            </Tab>
          )}
          {hasSingles && (
            <Tab active={tab === "singles"} onClick={selectTab("singles")}>
              Singles <TabCount>{singleList.length}</TabCount>
            </Tab>
          )}
          {hasVideos && (
            <Tab active={tab === "videos"} onClick={selectTab("videos")}>
              Videos <TabCount>{reviewableVideos.length}</TabCount>
            </Tab>
          )}
          {hasFavorites && (
            <Tab active={tab === "favorites"} tone="star" onClick={selectTab("favorites")}>
              ★ Favorites <TabCount>{favorites.length}</TabCount>
            </Tab>
          )}
          <Tab active={tab === "timeline"} onClick={selectTab("timeline")}>
            Timeline
          </Tab>
          <Tab
            active={tab === "finish"}
            tone="done"
            onClick={selectTab("finish")}
            data-testid="finish-tab"
          >
            Finish{state.done_at ? " ✓" : ""}
          </Tab>
        </nav>

        <div className="ml-auto flex items-center gap-2">
          {/* Skipping what is already decided: confirm jumps over it. Shown
              once something is decided, since before that it would do nothing. */}
          {reviewedUnits > 0 || hideReviewed ? (
            <Switch
              checked={hideReviewed}
              label="Skip reviewed"
              onClick={() => {
                setHideReviewed((on) => !on);
                goToNextUnconfirmed();
              }}
              title={
                hideReviewed
                  ? "Confirm jumps to the next thing still needing a decision — click to step one at a time"
                  : "Make confirm jump past clusters, singles and videos that have already been decided"
              }
              data-testid="hide-reviewed"
            />
          ) : null}

          {(totalUnits > 0 || state.pending_delete_count > 0 || state.done_at) && <Divider />}

          {/* Progress, the delete queue and the finished flag are one readout,
              not three loose greys next to a button. Spelling out "pending
              delete" costs nothing here and "12 pending" never said pending
              what. */}
          {totalUnits > 0 && (
            <Status
              className="flex items-center gap-1.5"
              title="Clusters, singles and videos that have been decided"
              data-testid="session-progress"
            >
              <span className="w-16 h-1 bg-gray-100 rounded-full overflow-hidden" aria-hidden>
                <span
                  className="block h-full bg-blue-500 transition-all duration-300"
                  style={{ width: `${(reviewedUnits / totalUnits) * 100}%` }}
                />
              </span>
              {reviewedUnits}/{totalUnits} reviewed
            </Status>
          )}
          {state.pending_delete_count > 0 && (
            <Status title="Marked for deletion — nothing leaves disk until you apply deletes in the Finish tab">
              {state.pending_delete_count} pending delete
            </Status>
          )}
          {state.done_at && (
            <Status tone="done" title={state.done_at}>
              Trip finished
            </Status>
          )}

          <Divider />

          <Button onClick={handleUndo} disabled={!state.undo_available || undoing}>
            {undoing ? "…" : "↶ Undo"}
          </Button>
        </div>
      </header>

      <main className="px-2 pt-2">
        {error && (
          <div className="mb-2 p-2 bg-red-50 border border-red-200 rounded text-red-700 text-sm flex items-start justify-between gap-4">
            <span>{error}</span>
            <button onClick={() => setError(null)} className="text-red-400 hover:text-red-600 text-xs underline shrink-0">Dismiss</button>
          </div>
        )}

        {tab === "finish" ? (
          <FinishTripPanel
            pendingCount={state.pending_delete_count}
            onError={setError}
            onFinished={handleChangeProject}
          />
        ) : tab === "timeline" ? (
          <TimelineView
            onError={setError}
            videos={videos}
            videoStatuses={videoStatuses}
            videoShotTimes={videoShotTimes}
            highlights={videoHighlights}
            onVideosChanged={refetchVideos}
          />
        ) : tab === "favorites" ? (
          <FavoritesView favorites={favorites} onToggleFavorite={toggleFavorite} onRefresh={reload} onError={setError} />
        ) : tab === "videos" ? (
          <VideoView
            videos={reviewableVideos}
            skipReviewed={hideReviewed}
            statuses={videoStatuses}
            highlights={videoHighlights}
            userClips={videoUserClips}
            videoTags={videoTags}
            onVideoTagsChange={setVideoTags}
            onError={setError}
            favorites={favorites}
            onToggleFavorite={toggleFavorite}
            onConfirmed={async () => { await reload(); await refetchVideos(); }}
            onAdvance={advanceTab}
            onPersisted={(path) =>
              setJustDecidedVideos((prev) => new Set(prev).add(path))
            }
          />
        ) : !hasClusters && !hasUnconfirmedSingles ? (
          <div className="bg-white rounded border border-gray-200 px-6 py-12 text-center">
            <p className="text-2xl mb-2">🎉</p>
            <p className="text-gray-700 font-medium">All done!</p>
            <p className="text-sm text-gray-500 mt-1">
              {state.pending_delete_count > 0 || favorites.length > 0
                ? "Open the Finish tab to delete and export."
                : "Nothing pending."}
            </p>
            <button
              onClick={selectTab("finish")}
              className="mt-4 px-4 py-2 text-sm font-medium bg-green-600 text-white rounded hover:bg-green-700"
            >
              Go to Finish
            </button>
          </div>
        ) : (
          <>
            {tab === "clusters" && hasClusters && (
              <ClusterView folder={state.folder} clusters={clusterList} decisions={decisions} skipReviewed={hideReviewed} favorites={favorites} onRefresh={reload} onError={setError} onUndo={handleUndo} onToggleFavorite={toggleFavorite} onAdvance={advanceTab} videoTags={videoTags} onVideoTagsChange={setVideoTags} />
            )}
            {tab === "singles" && hasSingles && (
              <SingletonsView
                folder={state.folder}
                singletons={singleList}
                skipReviewed={hideReviewed}
                decisions={decisions}
                favorites={favorites}
                onRefresh={reload}
                onError={setError}
                onToggleFavorite={toggleFavorite}
                onAdvance={advanceTab}
                videoTags={videoTags}
                onVideoTagsChange={setVideoTags}
              />
            )}
          </>
        )}
      </main>
    </div>
  );
}
