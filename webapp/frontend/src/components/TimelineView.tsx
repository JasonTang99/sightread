import { memo, useCallback, useEffect, useMemo, useState } from "react";
import type { GalleryPhoto, GridStatus, VideoHighlightsMap, VideoStatuses } from "../types";

interface Props {
  onError: (msg: string) => void;
  videos?: string[];
  videoStatuses?: VideoStatuses;
  videoShotTimes?: Record<string, string | null>;
  highlights?: VideoHighlightsMap;
  onVideosChanged?: () => void | Promise<void>;
}

type StatusFilter = "all" | "keep" | "delete";

// Status is conveyed by border colour alone, so tiles carry no text overlay and
// can run large.
const TILE_MIN_PX = 480;
// Kept in sync with TIMELINE_THUMB_WIDTH in webapp/server.py, which prewarms
// this width's cache so the grid isn't waiting on resizes as it scrolls.
// Tiles are square-cropped via object-cover, so a landscape frame needs ~1.5x
// the tile's width to fill it without upscaling.
const THUMB_W = 800;

interface VideoItem {
  path: string;
  shot_at: string | null;
  status: GridStatus;
}

function borderFor(status: GridStatus): string {
  if (status === "keep") return "border-green-400";
  if (status === "delete") return "border-red-400";
  return "border-gray-300";
}

function effectiveStatus(
  item: { path: string; status: GridStatus },
  overrides: Record<string, GridStatus>,
): GridStatus {
  return overrides[item.path] ?? item.status;
}

function matchesFilter(status: GridStatus, filter: StatusFilter): boolean {
  if (filter === "keep") return status !== "delete";
  if (filter === "delete") return status === "delete";
  return true;
}

interface TileProps {
  path: string;
  status: GridStatus;
  // The server's status, which is what a click toggles away from.
  baseStatus: GridStatus;
  onToggle: (path: string, baseStatus: GridStatus) => void;
}

// Memoised: a trip's timeline runs to hundreds of tiles, and without this every
// tile re-renders on each toggle, filter change and day switch.
const PhotoTile = memo(function PhotoTile({ path, status, baseStatus, onToggle }: TileProps) {
  return (
    <div
      className={`relative cursor-pointer rounded overflow-hidden border-4 transition-colors ${borderFor(status)}`}
      onClick={() => onToggle(path, baseStatus)}
      title={`${path.split("/").pop()} — ${status}`}
    >
      <img
        src={`/api/image?path=${encodeURIComponent(path)}&w=${THUMB_W}`}
        alt=""
        className="w-full aspect-square object-cover bg-gray-100"
        loading="lazy"
        decoding="async"
      />
    </div>
  );
});

const VideoTile = memo(function VideoTile({ path, status, baseStatus, clipCount, onToggle }: TileProps & { clipCount: number }) {
  return (
    <div
      className={`relative cursor-pointer rounded overflow-hidden border-4 transition-colors bg-gray-900 ${borderFor(status)}`}
      onClick={() => onToggle(path, baseStatus)}
      title={`${path.split("/").pop()} — ${status}`}
    >
      {/* A still frame, not a <video>: media elements load eagerly and at six
          connections per origin they starve the lazy photo thumbnails further
          down the timeline, which then never load at all. Playback lives in
          the Videos tab. */}
      <img
        src={`/api/video-poster?path=${encodeURIComponent(path)}&w=${THUMB_W}`}
        alt=""
        className="w-full aspect-square object-cover"
        loading="lazy"
        decoding="async"
      />
      {clipCount > 0 && (
        <span className="absolute top-1 right-1 bg-black/60 text-amber-300 text-xs rounded px-1">
          ✨ {clipCount}
        </span>
      )}
      {/* Neutral, not blue: the label sits on top of the frame, so it should
          read as chrome rather than as another status colour. */}
      <span className="absolute bottom-0 left-0 right-0 bg-gray-900/60 text-gray-200 text-xs text-center py-0.5 truncate px-1">
        ▶ {path.split("/").pop()}
      </span>
    </div>
  );
});

function dateOf(shot_at: string | null): string {
  if (!shot_at) return "Unknown";
  return shot_at.slice(0, 10);
}

export function TimelineView({
  onError,
  videos = [],
  videoStatuses = {},
  videoShotTimes = {},
  highlights = {},
  onVideosChanged,
}: Props) {
  const [photos, setPhotos] = useState<GalleryPhoto[]>([]);
  const [overrides, setOverrides] = useState<Record<string, GridStatus>>({});
  const [selectedDate, setSelectedDate] = useState<string>("all");
  const [filter, setFilter] = useState<StatusFilter>("all");
  const [confirming, setConfirming] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchGallery = useCallback(async () => {
    try {
      const res = await fetch("/api/gallery");
      if (!res.ok) throw new Error(`Gallery fetch failed: ${res.status}`);
      const data = await res.json();
      setPhotos(data.photos ?? []);
      setOverrides({});
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [onError]);

  useEffect(() => { fetchGallery(); }, [fetchGallery]);

  // Preload next few photos when the visible slice changes
  useEffect(() => {
    const visible = photos.filter(
      (p) =>
        (selectedDate === "all" || dateOf(p.shot_at) === selectedDate) &&
        matchesFilter(effectiveStatus(p, overrides), filter),
    );
    for (const ph of visible.slice(0, 6)) {
      const el = new Image();
      el.src = `/api/image?path=${encodeURIComponent(ph.path)}&w=${THUMB_W}`;
    }
    // `overrides` deliberately omitted: toggling a tile shouldn't refire preloads.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedDate, photos, filter]);

  const videoItems = useMemo<VideoItem[]>(
    () => videos.map((p) => ({
      path: p,
      shot_at: videoShotTimes[p] ?? null,
      status: videoStatuses[p] ?? "undecided",
    })),
    [videos, videoShotTimes, videoStatuses],
  );

  const dates = useMemo(() => {
    const seen = new Set<string>();
    const result: string[] = [];
    for (const ph of photos) {
      const d = dateOf(ph.shot_at);
      if (!seen.has(d)) { seen.add(d); result.push(d); }
    }
    for (const v of videoItems) {
      const d = dateOf(v.shot_at);
      if (!seen.has(d)) { seen.add(d); result.push(d); }
    }
    return result.sort();
  }, [photos, videoItems]);

  const photosByDate = useMemo(() => {
    const map: Record<string, GalleryPhoto[]> = {};
    for (const ph of photos) {
      const d = dateOf(ph.shot_at);
      (map[d] ??= []).push(ph);
    }
    return map;
  }, [photos]);

  const videosByDate = useMemo(() => {
    const map: Record<string, VideoItem[]> = {};
    for (const v of videoItems) {
      const d = dateOf(v.shot_at);
      (map[d] ??= []).push(v);
    }
    return map;
  }, [videoItems]);

  const countByDate = useMemo(() => {
    const map: Record<string, number> = {};
    for (const [d, ps] of Object.entries(photosByDate)) {
      map[d] = ps.filter((p) => matchesFilter(effectiveStatus(p, overrides), filter)).length;
    }
    for (const [d, vs] of Object.entries(videosByDate)) {
      map[d] = (map[d] ?? 0) + vs.filter((v) => matchesFilter(effectiveStatus(v, overrides), filter)).length;
    }
    return map;
  }, [photosByDate, videosByDate, overrides, filter]);

  const totalPhotoCount = useMemo(
    () => photos.filter((p) => matchesFilter(effectiveStatus(p, overrides), filter)).length,
    [photos, overrides, filter],
  );

  // Under a narrow filter most days can be empty; don't list them.
  const visibleDates = useMemo(
    () => (filter === "all" ? dates : dates.filter((d) => (countByDate[d] ?? 0) > 0)),
    [dates, countByDate, filter],
  );

  // Stable identity, or the memoised tiles re-render on every parent render.
  const toggle = useCallback((path: string, current: GridStatus) => {
    setOverrides((prev) => {
      const now = prev[path] ?? current;
      return { ...prev, [path]: now === "delete" ? "keep" : "delete" };
    });
  }, []);

  const confirmDay = async (date: string) => {
    setConfirming(date);
    try {
      // Decisions are per photo, so confirming a day decides exactly that day's
      // items — no need to drag in the rest of any cluster that straddles it.
      // Videos are decided the same way, which is what puts a colour on their
      // tiles here and drops them out of the video reviewer's queue.
      const dayItems = [...(photosByDate[date] ?? []), ...(videosByDate[date] ?? [])];
      const deletePaths = dayItems
        .filter((p) => effectiveStatus(p, overrides) === "delete")
        .map((p) => p.path);

      const res = await fetch("/api/confirm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          delete_paths: deletePaths,
          decided_paths: dayItems.map((p) => p.path),
        }),
      });
      if (!res.ok) throw new Error(`Confirm failed: ${res.status}`);
      await fetchGallery();
      await onVideosChanged?.();
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    } finally {
      setConfirming(null);
    }
  };

  const renderPhotoGrid = (gridPhotos: GalleryPhoto[]) => {
    const visible = gridPhotos.filter((p) => matchesFilter(effectiveStatus(p, overrides), filter));
    if (visible.length === 0) return <p className="text-sm text-gray-400 py-4">No photos.</p>;
    return (
      <div className="grid gap-2" style={{ gridTemplateColumns: `repeat(auto-fill, minmax(${TILE_MIN_PX}px, 1fr))` }}>
        {visible.map((ph) => (
          <PhotoTile
            key={ph.path}
            path={ph.path}
            status={effectiveStatus(ph, overrides)}
            baseStatus={ph.status}
            onToggle={toggle}
          />
        ))}
      </div>
    );
  };

  const renderVideoGrid = (dayVideos: VideoItem[]) => {
    const visible = dayVideos.filter((v) => matchesFilter(effectiveStatus(v, overrides), filter));
    if (visible.length === 0) return null;
    return (
      <div className="grid gap-2 mt-2" style={{ gridTemplateColumns: `repeat(auto-fill, minmax(${TILE_MIN_PX}px, 1fr))` }}>
        {visible.map((v) => (
          <VideoTile
            key={v.path}
            path={v.path}
            status={effectiveStatus(v, overrides)}
            baseStatus={v.status}
            clipCount={highlights[v.path]?.clips.length ?? 0}
            onToggle={toggle}
          />
        ))}
      </div>
    );
  };

  const renderDaySection = (date: string) => {
    const dayPhotos = photosByDate[date] ?? [];
    const dayVideos = videosByDate[date] ?? [];
    const isConfirming = confirming === date;
    const photoCount = dayPhotos.filter((p) => matchesFilter(effectiveStatus(p, overrides), filter)).length;
    const videoCount = dayVideos.filter((v) => matchesFilter(effectiveStatus(v, overrides), filter)).length;
    return (
      // content-visibility lets the browser skip layout, paint and image decode
      // for days scrolled out of view — a trip is hundreds of tiles, and
      // rendering them all at once is what made the page expensive. The
      // intrinsic size is a placeholder height so the scrollbar stays sane
      // until a section has been measured once.
      <div key={date} style={{ contentVisibility: "auto", containIntrinsicSize: "auto 900px" }}>
        <div className="flex items-center gap-3 mb-2 mt-4 first:mt-0">
          <h2 className="text-sm font-semibold text-gray-700">{date}</h2>
          {photoCount > 0 && <span className="text-xs text-gray-400">{photoCount} photo{photoCount !== 1 ? "s" : ""}</span>}
          {videoCount > 0 && <span className="text-xs text-blue-400">{videoCount} video{videoCount !== 1 ? "s" : ""}</span>}
          {photoCount + videoCount > 0 && (
            <button
              onClick={() => confirmDay(date)}
              disabled={isConfirming}
              className="ml-auto px-3 py-1 text-xs font-medium bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
            >
              {isConfirming ? "…" : "✓ Confirm Day"}
            </button>
          )}
        </div>
        {renderPhotoGrid(dayPhotos)}
        {renderVideoGrid(dayVideos)}
      </div>
    );
  };

  if (loading) return <p className="text-sm text-gray-400 p-4">Loading…</p>;
  if (photos.length === 0 && videoItems.length === 0) return <p className="text-sm text-gray-400 p-4">No photos. Run pipeline first.</p>;

  return (
    <div className="flex" style={{ height: "calc(100vh - 2.25rem)" }}>
      {/* Sidebar */}
      <div className="w-44 shrink-0 border-r border-gray-200 overflow-y-auto bg-white">
        {/* Filter toggle */}
        <div className="px-2 pt-2 pb-1 border-b border-gray-100">
          <div className="flex rounded overflow-hidden border border-gray-200 text-xs">
            <button
              onClick={() => setFilter("all")}
              className={`flex-1 py-1 transition-colors ${filter === "all" ? "bg-blue-600 text-white" : "text-gray-500 hover:bg-gray-50"}`}
            >
              All
            </button>
            <button
              onClick={() => setFilter("keep")}
              className={`flex-1 py-1 transition-colors ${filter === "keep" ? "bg-green-600 text-white" : "text-gray-500 hover:bg-gray-50"}`}
            >
              Keep
            </button>
            <button
              onClick={() => setFilter("delete")}
              className={`flex-1 py-1 transition-colors ${filter === "delete" ? "bg-red-600 text-white" : "text-gray-500 hover:bg-gray-50"}`}
            >
              Delete
            </button>
          </div>
        </div>

        <button
          onClick={() => setSelectedDate("all")}
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
            onClick={() => setSelectedDate(d)}
            className={`w-full text-left px-3 py-1.5 text-xs transition-colors ${
              selectedDate === d ? "bg-blue-50 text-blue-700 font-medium" : "text-gray-500 hover:bg-gray-50"
            }`}
          >
            {d}
            <span className="ml-1 text-gray-400">({countByDate[d] ?? 0})</span>
          </button>
        ))}
      </div>

      {/* Main */}
      <div className="flex-1 overflow-y-auto px-4 py-3">
        {selectedDate === "all"
          ? visibleDates.map((d) => renderDaySection(d))
          : renderDaySection(selectedDate)}
      </div>
    </div>
  );
}
