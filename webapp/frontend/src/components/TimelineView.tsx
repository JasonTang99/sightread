import { useCallback, useEffect, useMemo, useState } from "react";
import type { GalleryPhoto, GridStatus, VideoHighlightsMap, VideoStatuses } from "../types";
import { deviceOf, devicesIn } from "../device";
import { TimelineDay } from "./TimelineDay";
import { TimelineSidebar } from "./TimelineSidebar";
import {
  THUMB_W,
  dateOf,
  effectiveStatus,
  matchesFilter,
  type StatusFilter,
  type VideoItem,
} from "../timeline";

interface Props {
  onError: (msg: string) => void;
  videos?: string[];
  videoStatuses?: VideoStatuses;
  videoShotTimes?: Record<string, string | null>;
  highlights?: VideoHighlightsMap;
  onVideosChanged?: () => void | Promise<void>;
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
  // The project folder, so a trip opened as one project can say which camera
  // folder each shot came from.
  const [folder, setFolder] = useState<string | null>(null);
  const [device, setDevice] = useState<string>("all");
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
      setFolder(data.folder ?? null);
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
      (p) => (selectedDate === "all" || dateOf(p.shot_at) === selectedDate) && shown(p),
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

  const devices = useMemo(
    () => devicesIn([...photos.map((p) => p.path), ...videoItems.map((v) => v.path)], folder),
    [photos, videoItems, folder],
  );

  // One predicate for every count and grid: a tile shows when its decision
  // passes the keep/delete filter and it came from the chosen device.
  const shown = useCallback(
    (item: { path: string; status: GridStatus }) =>
      matchesFilter(effectiveStatus(item, overrides), filter) &&
      (device === "all" || deviceOf(item.path, folder) === device),
    [overrides, filter, device, folder],
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
      map[d] = ps.filter(shown).length;
    }
    for (const [d, vs] of Object.entries(videosByDate)) {
      map[d] = (map[d] ?? 0) + vs.filter(shown).length;
    }
    return map;
  }, [photosByDate, videosByDate, shown]);

  const totalPhotoCount = useMemo(
    () => photos.filter(shown).length,
    [photos, shown],
  );

  // Under a narrow filter most days can be empty; don't list them.
  const visibleDates = useMemo(
    () =>
      filter === "all" && device === "all"
        ? dates
        : dates.filter((d) => (countByDate[d] ?? 0) > 0),
    [dates, countByDate, filter, device],
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

  const statusOf = useCallback(
    (item: { path: string; status: GridStatus }) => effectiveStatus(item, overrides),
    [overrides],
  );

  if (loading) return <p className="text-sm text-gray-400 p-4">Loading…</p>;
  if (photos.length === 0 && videoItems.length === 0) return <p className="text-sm text-gray-400 p-4">No photos. Run pipeline first.</p>;

  return (
    <div className="flex" style={{ height: "calc(100vh - 2.25rem)" }}>
      <TimelineSidebar
        filter={filter}
        onFilter={setFilter}
        devices={devices}
        device={device}
        onDevice={setDevice}
        selectedDate={selectedDate}
        onDate={setSelectedDate}
        totalPhotoCount={totalPhotoCount}
        visibleDates={visibleDates}
        countByDate={countByDate}
      />

      <div className="flex-1 overflow-y-auto px-4 py-3">
        {selectedDate === "all"
          ? visibleDates.map((d) => (
              <TimelineDay
                key={d}
                date={d}
                dayPhotos={photosByDate[d] ?? []}
                dayVideos={videosByDate[d] ?? []}
                shown={shown}
                statusOf={statusOf}
                folder={folder}
                highlights={highlights}
                onToggle={toggle}
                confirming={confirming === d}
                onConfirm={confirmDay}
              />
            ))
          : (
              <TimelineDay
                key={selectedDate}
                date={selectedDate}
                dayPhotos={photosByDate[selectedDate] ?? []}
                dayVideos={videosByDate[selectedDate] ?? []}
                shown={shown}
                statusOf={statusOf}
                folder={folder}
                highlights={highlights}
                onToggle={toggle}
                confirming={confirming === selectedDate}
                onConfirm={confirmDay}
              />
            )}
      </div>
    </div>
  );
}
