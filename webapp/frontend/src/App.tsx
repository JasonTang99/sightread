import { useCallback, useEffect, useRef, useState } from "react";
import { useShortcuts } from "./hooks/useWindowKeydown";
import { isDecided } from "./decisions";
import { sourceOf, sourcesIn } from "./device";
import { ClusterView } from "./components/ClusterView";
import { FavoritesView } from "./components/FavoritesView";
import { HelpOverlay } from "./components/HelpOverlay";
import { ProjectPicker } from "./components/ProjectPicker";
import { SingletonsView } from "./components/SingletonsView";
import { TimelineView } from "./components/TimelineView";
import { FinishTripPanel } from "./components/FinishTripPanel";
import { VideoView } from "./components/VideoView";
import { Check, Icon, Switch, Tab, TabCount } from "./components/ui";
import { APP_KEYS, typingTarget } from "./shortcuts";
import type { AppState, Cluster, VideoHighlightsMap, VideoStatuses, VideoTagsState } from "./types";

// Unchecked source names for one project. Missing or unreadable storage
// means every source stays on — the same default as a first visit.
function sourcesStorageKey(folder: string, subtrip?: string | null): string {
  return `sightread:sources:${subtrip ? `${folder}#${subtrip}` : folder}`;
}

function readOffSources(folder: string, subtrip?: string | null): string[] {
  try {
    const raw = localStorage.getItem(sourcesStorageKey(folder, subtrip));
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed) || parsed.some((s) => typeof s !== "string")) return [];
    return parsed;
  } catch {
    return [];
  }
}

function writeOffSources(folder: string, subtrip: string | null | undefined, off: string[]) {
  try {
    localStorage.setItem(sourcesStorageKey(folder, subtrip), JSON.stringify(off));
  } catch {
    // private window, or site data blocked — the choice still holds this visit
  }
}

// Sources the user left on. An empty result is not a choice: stored data
// that would turn every source off is ignored, and so is unchecking the last.
function sourcesOn(sources: string[], off: string[]): Set<string> {
  const on = sources.filter((s) => !off.includes(s));
  return new Set(on.length === 0 ? sources : on);
}

export default function App() {
  const [state, setState] = useState<AppState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<"clusters" | "singles" | "videos" | "favorites" | "timeline" | "finish">("clusters");
  const undoingRef = useRef(false);
  // The header slot the review views portal their own controls into, so the
  // whole chrome is one row. State rather than a ref: the views only render
  // into it once it exists.
  const [controlsEl, setControlsEl] = useState<HTMLDivElement | null>(null);
  const [showHelp, setShowHelp] = useState(false);
  const [videos, setVideos] = useState<string[]>([]);
  const [videoStatuses, setVideoStatuses] = useState<VideoStatuses>({});
  const [videoShotTimes, setVideoShotTimes] = useState<Record<string, string | null>>({});
  const [videoHighlights, setVideoHighlights] = useState<VideoHighlightsMap>({});
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
  // Override of the stored source filter for the project in `key`. Absent
  // means "read localStorage", so the first render that knows the folder
  // already has the choice the landing tab needs.
  const [sourceChoice, setSourceChoice] = useState<{ key: string; off: string[] } | null>(null);

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
      setVideoTags(d.video_tags ?? { tags: [], assignments: {} });
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

  const folder = state?.folder;
  const subtrip = state?.subtrip ?? null;
  const sourceKey = folder ? sourcesStorageKey(folder, subtrip) : "";
  const storedOff = sourceChoice && sourceChoice.key === sourceKey
    ? sourceChoice.off
    : (folder ? readOffSources(folder, subtrip) : []);
  const rawClusters = state?.clusters ?? [];
  const rawSingles = state?.singletons ?? [];
  const decisions = state?.photo_decisions ?? {};
  const sources = sourcesIn(
    [
      ...rawClusters.flatMap((c) => c.images.map((img) => img.path)),
      ...rawSingles.flatMap((c) => c.images.map((img) => img.path)),
      ...videos,
    ],
    folder,
  );
  const onSources = sourcesOn(sources, storedOff);
  // One source is not a choice. Fewer than two and the checkboxes stay hidden,
  // and the lists below are the whole project.
  const sourceFilterOn = sources.length >= 2;
  const inOnSource = (path: string) => onSources.has(sourceOf(path, folder));
  const keepCluster = (c: Cluster) => !sourceFilterOn || c.images.some((img) => inOnSource(img.path));
  // Skip-reviewed does not filter. It used to, and the tabs shrank under you
  // on every confirm. The source checkboxes do: a cluster stays whole when
  // any frame is in a checked source, and is dropped only when none are.
  const clusterList = sourceFilterOn ? rawClusters.filter(keepCluster) : rawClusters;
  const singleList = sourceFilterOn ? rawSingles.filter(keepCluster) : rawSingles;
  const sourceVideos = sourceFilterOn ? videos.filter(inOnSource) : videos;
  const reviewableVideos = sourceVideos.filter((v) => videoStatuses[v] !== "delete");

  // Pick the landing tab once, on the first load of a project: whatever still
  // needs triage, and only the timeline once clusters, singles *and* videos are
  // all decided. Videos used to be left out, so reloading mid-way through the
  // Videos tab dropped you on the timeline with footage still undecided.
  // Deliberately does not re-run on later state changes; confirming a cluster
  // in the middle of the pile shouldn't yank you off the tab you're working
  // in. Enter on the *last* cluster/single/video is the explicit hop — see
  // `advanceTab`. The lists are the source-filtered ones, so a camera you
  // turned off is not where you land.
  const landedRef = useRef(false);
  useEffect(() => {
    if (!state || state.no_project || landedRef.current) return;
    // Wait for /api/videos: it lands separately from /api/state, and deciding
    // before it arrives would read every video as undecided.
    if (!videosLoaded) return;
    landedRef.current = true;
    if (rawClusters.length + rawSingles.length === 0) return;  // nothing curated yet
    if (clusterList.some((c) => !isDecided(c, decisions))) return;  // stay on clusters
    if (singleList.some((c) => !isDecided(c, decisions))) { setTab("singles"); return; }
    const videosPending = reviewableVideos.some((v) => (videoStatuses[v] ?? "undecided") === "undecided");
    setTab(videosPending ? "videos" : "timeline");
  }, [state, videosLoaded, videos, videoStatuses, rawClusters, rawSingles, clusterList, singleList, reviewableVideos, decisions]);

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
    "app-undo": (e) => { e.preventDefault(); handleUndo(); },
    "app-redo": (e) => { e.preventDefault(); handleRedo(); },
  }, { ignore: typingTarget, enabled: projectOpen });

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
      setVideoStatuses((prev) => ({ ...prev, [path]: "keep" }));
    }
  }, [reload, videos]);

  // Undo and redo are keys only now. Holding Ctrl+Z must not stack up
  // requests, and an empty stack is a no-op rather than an error banner:
  // there is no disabled button left to say so.
  const step = async (kind: "undo" | "redo") => {
    if (undoingRef.current) return;
    undoingRef.current = true;
    try {
      const res = await fetch(`/api/${kind}`, { method: "POST" });
      if (res.status === 400) return;
      if (!res.ok) throw new Error(`${kind === "undo" ? "Undo" : "Redo"} failed: ${res.status}`);
      await reload();
      await refetchVideos();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      undoingRef.current = false;
    }
  };
  const handleUndo = () => step("undo");
  const handleRedo = () => step("redo");

  const handleChangeProject = async () => {
    setState(null);
    setVideos([]);
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

  const hasClusters = clusterList.length > 0;
  const hasSingles = singleList.length > 0;
  // The video reviewer works through footage that isn't marked for deletion
  // yet; the timeline shows everything, marked included, so it can colour a
  // tile by its decision the way it does for photos. `reviewableVideos` is
  // already limited to the checked sources.
  const hasVideos = reviewableVideos.length > 0;
  // Reviewed/total on the three review tabs. Clusters and singles are
  // decided per cluster; a video counts once its status is set and is
  // anything other than undecided. Favorites stays a plain count.
  const reviewedClusters = clusterList.filter((c) => isDecided(c, decisions)).length;
  const reviewedSingles = singleList.filter((c) => isDecided(c, decisions)).length;
  const reviewedVideos = reviewableVideos.filter((v) => {
    const status = videoStatuses[v];
    return status !== undefined && status !== "undecided";
  }).length;
  const clusterProgress = `${reviewedClusters}/${clusterList.length}`;
  const singleProgress = `${reviewedSingles}/${singleList.length}`;
  const videoProgress = `${reviewedVideos}/${reviewableVideos.length}`;
  const favorites = state.favorites ?? [];
  const hasFavorites = favorites.length > 0;

  // Jumps to whichever tab has the next thing still needing a decision —
  // clusters, then singles, then videos — same priority as the initial
  // landing choice above.
  const goToNextUnconfirmed = () => {
    if (clusterList.some((c) => !isDecided(c, decisions))) { setTab("clusters"); return; }
    if (singleList.some((c) => !isDecided(c, decisions))) { setTab("singles"); return; }
    const videosPending = reviewableVideos.some((v) => (videoStatuses[v] ?? "undecided") === "undecided");
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

  const hasReviewables = clusterList.length + singleList.length + sourceVideos.length > 0;

  const toggleSource = (name: string) => {
    if (!folder || !sourceFilterOn) return;
    if (onSources.has(name) && onSources.size === 1) return;
    const offNow = sources.filter((s) => !onSources.has(s));
    const next = onSources.has(name) ? [...offNow, name] : offNow.filter((s) => s !== name);
    setSourceChoice({ key: sourceKey, off: next });
    writeOffSources(folder, subtrip, next);
  };

  return (
    <div>
      {showHelp && <HelpOverlay onClose={() => setShowHelp(false)} />}
      {/* One row, and only what is used while reviewing: the photos get the
          height. Undo/redo are Ctrl+Z / Ctrl+Shift+Z, Enter confirms, and the
          progress readouts went — the tab counts carry the same news. The
          right-hand slot belongs to the open view (ClusterView's per-row and
          next-unreviewed, the tag chips), portalled in so its state stays in
          the view. */}
      <header className="bg-white border-b border-gray-200 px-2 h-9 flex items-stretch gap-2">
        <button
          onClick={handleChangeProject}
          className="px-1 text-gray-400 hover:text-blue-600 transition-colors"
          aria-label="Back to projects"
          title="Back to projects"
        >
          <Icon name="home" />
        </button>
        {/* The folder name is what the picker lists; the full path is in the
            tooltip, since trips from different drives can share a name. */}
        {state.folder && (
          <span
            className="self-center text-sm text-gray-900 font-semibold truncate max-w-[12rem]"
            title={state.display_name ? `${state.display_name}\n${state.folder}` : state.folder}
            data-testid="project-name"
          >
            {state.display_name || state.folder.split("/").filter(Boolean).pop()}
          </span>
        )}

        <nav className="flex items-stretch" aria-label="Review sections">
          {hasClusters && (
            <Tab active={tab === "clusters"} label={`Clusters (${clusterProgress})`} onClick={selectTab("clusters")}>
              <Icon name="clusters" /> <TabCount>{clusterProgress}</TabCount>
            </Tab>
          )}
          {hasSingles && (
            <Tab active={tab === "singles"} label={`Singles (${singleProgress})`} onClick={selectTab("singles")}>
              <Icon name="singles" /> <TabCount>{singleProgress}</TabCount>
            </Tab>
          )}
          {hasVideos && (
            <Tab active={tab === "videos"} label={`Videos (${videoProgress})`} onClick={selectTab("videos")}>
              <Icon name="videos" /> <TabCount>{videoProgress}</TabCount>
            </Tab>
          )}
          {hasFavorites && (
            <Tab active={tab === "favorites"} tone="star" label={`Favorites (${favorites.length})`} onClick={selectTab("favorites")}>
              <Icon name="favorites" /> <TabCount>{favorites.length}</TabCount>
            </Tab>
          )}
          <Tab active={tab === "timeline"} label="Timeline" onClick={selectTab("timeline")}>
            <Icon name="timeline" />
          </Tab>
          <Tab
            active={tab === "finish"}
            tone="done"
            label={state.done_at ? "Finish ✓" : "Finish"}
            onClick={selectTab("finish")}
            data-testid="finish-tab"
          >
            <Icon name="finish" />
            {state.done_at && <span className="text-xs">✓</span>}
          </Tab>
        </nav>

        {sourceFilterOn && (
          <div className="self-center flex items-center gap-2" role="group" aria-label="Sources">
            {sources.map((name) => (
              <Check
                key={name}
                checked={onSources.has(name)}
                label={name}
                onChange={(e) => {
                  e.currentTarget.blur();
                  toggleSource(name);
                }}
              />
            ))}
          </div>
        )}

        {/* Skipping what is already decided: confirm jumps over it. */}
        {hasReviewables && (
          <span className="self-center">
            <Switch
              compact
              checked={hideReviewed}
              label="Skip reviewed"
              onClick={(e) => {
                e.currentTarget.blur();
                const next = !hideReviewed;
                setHideReviewed(next);
                if (next) goToNextUnconfirmed();
              }}
              title={
                hideReviewed
                  ? "Skip reviewed: on — confirm jumps to the next thing still needing a decision"
                  : "Skip reviewed: off — confirm steps one at a time"
              }
              data-testid="hide-reviewed"
            />
          </span>
        )}

        <div ref={setControlsEl} className="flex-1 min-w-0 flex items-center justify-end gap-3" />
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
            controlsEl={controlsEl}
            videos={reviewableVideos}
            skipReviewed={hideReviewed}
            statuses={videoStatuses}
            videoTags={videoTags}
            onVideoTagsChange={setVideoTags}
            onError={setError}
            favorites={favorites}
            onToggleFavorite={toggleFavorite}
            onAdvance={advanceTab}
          />
        ) : rawClusters.length === 0 && !rawSingles.some((c) => !isDecided(c, decisions)) ? (
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
              <ClusterView controlsEl={controlsEl} folder={state.folder} clusters={clusterList} decisions={decisions} skipReviewed={hideReviewed} favorites={favorites} onRefresh={reload} onError={setError} onUndo={handleUndo} onToggleFavorite={toggleFavorite} onAdvance={advanceTab} videoTags={videoTags} onVideoTagsChange={setVideoTags} />
            )}
            {tab === "singles" && hasSingles && (
              <SingletonsView
                controlsEl={controlsEl}
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
