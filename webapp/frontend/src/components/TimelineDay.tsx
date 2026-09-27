import type { GalleryPhoto, GridStatus, VideoHighlightsMap } from "../types";
import { deviceOf } from "../device";
import { PhotoTile, VideoTile } from "./TimelineTiles";
import { TILE_MIN_PX, type VideoItem } from "../timeline";

interface Props {
  date: string;
  dayPhotos: GalleryPhoto[];
  dayVideos: VideoItem[];
  shown: (item: { path: string; status: GridStatus }) => boolean;
  statusOf: (item: { path: string; status: GridStatus }) => GridStatus;
  folder: string | null;
  highlights: VideoHighlightsMap;
  onToggle: (path: string, baseStatus: GridStatus) => void;
  confirming: boolean;
  onConfirm: (date: string) => void;
}

export function TimelineDay({
  date,
  dayPhotos,
  dayVideos,
  shown,
  statusOf,
  folder,
  highlights,
  onToggle,
  confirming,
  onConfirm,
}: Props) {
  const visiblePhotos = dayPhotos.filter(shown);
  const visibleVideos = dayVideos.filter(shown);
  const photoCount = visiblePhotos.length;
  const videoCount = visibleVideos.length;

  return (
    // content-visibility lets the browser skip layout, paint and image decode
    // for days scrolled out of view — a trip is hundreds of tiles, and
    // rendering them all at once is what made the page expensive. The
    // intrinsic size is a placeholder height so the scrollbar stays sane
    // until a section has been measured once.
    <div style={{ contentVisibility: "auto", containIntrinsicSize: "auto 900px" }}>
      <div className="flex items-center gap-3 mb-2 mt-4 first:mt-0">
        <h2 className="text-sm font-semibold text-gray-700">{date}</h2>
        {photoCount > 0 && <span className="text-xs text-gray-400">{photoCount} photo{photoCount !== 1 ? "s" : ""}</span>}
        {videoCount > 0 && <span className="text-xs text-blue-400">{videoCount} video{videoCount !== 1 ? "s" : ""}</span>}
        {photoCount + videoCount > 0 && (
          <button
            onClick={() => onConfirm(date)}
            disabled={confirming}
            className="ml-auto px-3 py-1 text-xs font-medium bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
          >
            {confirming ? "…" : "✓ Confirm Day"}
          </button>
        )}
      </div>
      {visiblePhotos.length === 0 ? (
        <p className="text-sm text-gray-400 py-4">No photos.</p>
      ) : (
        <div className="grid gap-2" style={{ gridTemplateColumns: `repeat(auto-fill, minmax(${TILE_MIN_PX}px, 1fr))` }}>
          {visiblePhotos.map((ph) => (
            <PhotoTile
              key={ph.path}
              path={ph.path}
              status={statusOf(ph)}
              baseStatus={ph.status}
              onToggle={onToggle}
              motion={ph.motion}
              device={deviceOf(ph.path, folder)}
              model={ph.model}
            />
          ))}
        </div>
      )}
      {visibleVideos.length > 0 && (
        <div className="grid gap-2 mt-2" style={{ gridTemplateColumns: `repeat(auto-fill, minmax(${TILE_MIN_PX}px, 1fr))` }}>
          {visibleVideos.map((v) => (
            <VideoTile
              key={v.path}
              path={v.path}
              status={statusOf(v)}
              baseStatus={v.status}
              clipCount={highlights[v.path]?.clips.length ?? 0}
              onToggle={onToggle}
              device={deviceOf(v.path, folder)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
