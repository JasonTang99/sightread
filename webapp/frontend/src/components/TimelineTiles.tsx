import { memo } from "react";
import type { GridStatus } from "../types";
import { LiveMotion } from "./LiveMotion";
import { DeviceBadge } from "./DeviceBadge";
import { THUMB_W, borderFor } from "../timeline";

interface TileProps {
  path: string;
  status: GridStatus;
  // The server's status, which is what a click toggles away from.
  baseStatus: GridStatus;
  onToggle: (path: string, baseStatus: GridStatus) => void;
}

// Memoised: a trip's timeline runs to hundreds of tiles, and without this every
// tile re-renders on each toggle, filter change and day switch.
export const PhotoTile = memo(function PhotoTile(
  { path, status, baseStatus, onToggle, motion, device, model }:
    TileProps & { motion?: string; device: string; model?: string },
) {
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
      {motion && <LiveMotion motion={motion} />}
      <DeviceBadge device={device} model={model} />
    </div>
  );
});

export const VideoTile = memo(function VideoTile(
  { path, status, baseStatus, clipCount, onToggle, device }:
    TileProps & { clipCount: number; device: string },
) {
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
      <DeviceBadge device={device} corner="top-1 left-1" />
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
