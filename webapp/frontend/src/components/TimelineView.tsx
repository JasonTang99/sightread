import { useCallback, useEffect, useMemo, useState } from "react";
import type { GalleryPhoto, VideoHighlightsMap } from "../types";

interface Props {
  onError: (msg: string) => void;
  videos?: string[];
  videoShotTimes?: Record<string, string | null>;
  highlights?: VideoHighlightsMap;
}

type StatusFilter = "all" | "keep";

interface VideoItem {
  path: string;
  shot_at: string | null;
}

function effectiveStatus(photo: GalleryPhoto, overrides: Record<string, GalleryPhoto["status"]>): GalleryPhoto["status"] {
  return overrides[photo.path] ?? photo.status;
}

function dateOf(shot_at: string | null): string {
  if (!shot_at) return "Unknown";
  return shot_at.slice(0, 10);
}

export function TimelineView({ onError, videos = [], videoShotTimes = {}, highlights = {} }: Props) {
  const [photos, setPhotos] = useState<GalleryPhoto[]>([]);
  const [overrides, setOverrides] = useState<Record<string, GalleryPhoto["status"]>>({});
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

  // Preload next few photos when selectedDate changes
  useEffect(() => {
    const visible = photos.filter((p) => selectedDate === "all" || dateOf(p.shot_at) === selectedDate);
    for (const ph of visible.slice(0, 6)) {
      const el = new Image();
      el.src = `/api/image?path=${encodeURIComponent(ph.path)}&w=600`;
    }
  }, [selectedDate, photos]);

  const videoItems = useMemo<VideoItem[]>(
    () => videos.map((p) => ({ path: p, shot_at: videoShotTimes[p] ?? null })),
    [videos, videoShotTimes],
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
      map[d] = ps.filter((p) => {
        const s = effectiveStatus(p, overrides);
        return filter === "all" || s !== "delete";
      }).length;
    }
    for (const [d, vs] of Object.entries(videosByDate)) {
      map[d] = (map[d] ?? 0) + vs.length;
    }
    return map;
  }, [photosByDate, videosByDate, overrides, filter]);

  const toggle = (path: string) => {
    setOverrides((prev) => {
      const current = prev[path] ?? photos.find((p) => p.path === path)?.status ?? "undecided";
      const next = current === "delete" ? "keep" : "delete";
      return { ...prev, [path]: next };
    });
  };

  const confirmDay = async (date: string) => {
    setConfirming(date);
    try {
      const dayPhotos = photosByDate[date] ?? [];
      const clusterIds = new Set(dayPhotos.map((p) => p.cluster_id));
      const deletePaths: string[] = [];
      const singletonDecisions: { cluster_id: number; kept: string[]; deleted: string[] }[] = [];

      for (const cid of clusterIds) {
        const clusterPhotos = photos.filter((p) => p.cluster_id === cid);
        const kept: string[] = [];
        const deleted: string[] = [];
        for (const ph of clusterPhotos) {
          const s = effectiveStatus(ph, overrides);
          if (s === "delete") {
            deleted.push(ph.path);
            deletePaths.push(ph.path);
          } else {
            kept.push(ph.path);
          }
        }
        singletonDecisions.push({ cluster_id: cid, kept, deleted });
      }

      const res = await fetch("/api/confirm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ delete_paths: deletePaths, singleton_decisions: singletonDecisions }),
      });
      if (!res.ok) throw new Error(`Confirm failed: ${res.status}`);
      await fetchGallery();
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    } finally {
      setConfirming(null);
    }
  };

  const renderPhotoGrid = (gridPhotos: GalleryPhoto[]) => {
    const visible = filter === "keep"
      ? gridPhotos.filter((p) => effectiveStatus(p, overrides) !== "delete")
      : gridPhotos;
    if (visible.length === 0) return <p className="text-sm text-gray-400 py-4">No photos.</p>;
    return (
      <div className="grid gap-1.5" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))" }}>
        {visible.map((ph) => {
          const status = effectiveStatus(ph, overrides);
          const borderClass = status === "keep" ? "border-green-400" : status === "delete" ? "border-red-400" : "border-gray-300";
          const labelClass = status === "keep" ? "bg-green-500" : status === "delete" ? "bg-red-400" : "bg-gray-300";
          const labelText = status === "keep" ? "keep" : status === "delete" ? "delete" : "?";
          return (
            <div
              key={ph.path}
              className={`relative cursor-pointer rounded overflow-hidden border-2 transition-colors ${borderClass}`}
              onClick={() => toggle(ph.path)}
              title={ph.path.split("/").pop()}
            >
              <img
                src={`/api/image?path=${encodeURIComponent(ph.path)}&w=600`}
                alt=""
                className="w-full aspect-square object-cover bg-gray-100"
                loading="lazy"
              />
              <span className={`absolute bottom-0 left-0 right-0 text-white text-xs text-center py-0.5 ${labelClass} opacity-90`}>
                {labelText}
              </span>
            </div>
          );
        })}
      </div>
    );
  };

  const renderVideoGrid = (dayVideos: VideoItem[]) => {
    if (dayVideos.length === 0) return null;
    return (
      <div className="grid gap-1.5 mt-1.5" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))" }}>
        {dayVideos.map((v) => {
          const clipCount = highlights[v.path]?.clips.length ?? 0;
          return (
            <div key={v.path} className="relative rounded overflow-hidden border-2 border-blue-300 bg-gray-900" title={v.path.split("/").pop()}>
              <video
                src={`/api/video?path=${encodeURIComponent(v.path)}`}
                className="w-full aspect-square object-cover"
                preload="metadata"
                muted
              />
              {clipCount > 0 && (
                <span className="absolute top-1 right-1 bg-black/60 text-amber-300 text-xs rounded px-1">
                  ✨ {clipCount}
                </span>
              )}
              <span className="absolute bottom-0 left-0 right-0 bg-blue-500 text-white text-xs text-center py-0.5 opacity-90 truncate px-1">
                ▶ {v.path.split("/").pop()}
              </span>
            </div>
          );
        })}
      </div>
    );
  };

  const renderDaySection = (date: string) => {
    const dayPhotos = photosByDate[date] ?? [];
    const dayVideos = videosByDate[date] ?? [];
    const isConfirming = confirming === date;
    const photoCount = dayPhotos.length;
    const videoCount = dayVideos.length;
    return (
      <div key={date}>
        <div className="flex items-center gap-3 mb-2 mt-4 first:mt-0">
          <h2 className="text-sm font-semibold text-gray-700">{date}</h2>
          {photoCount > 0 && <span className="text-xs text-gray-400">{photoCount} photo{photoCount !== 1 ? "s" : ""}</span>}
          {videoCount > 0 && <span className="text-xs text-blue-400">{videoCount} video{videoCount !== 1 ? "s" : ""}</span>}
          {photoCount > 0 && (
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
              Keep only
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
          <span className="ml-1 text-xs text-gray-400">({photos.length})</span>
        </button>

        {dates.map((d) => (
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
          ? dates.map((d) => renderDaySection(d))
          : renderDaySection(selectedDate)}
      </div>
    </div>
  );
}
