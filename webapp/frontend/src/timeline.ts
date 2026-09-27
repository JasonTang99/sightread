import type { GridStatus } from "./types";

export type StatusFilter = "all" | "keep" | "delete";

export interface VideoItem {
  path: string;
  shot_at: string | null;
  status: GridStatus;
}

export function borderFor(status: GridStatus): string {
  if (status === "keep") return "border-green-400";
  if (status === "delete") return "border-red-400";
  return "border-gray-300";
}

export function effectiveStatus(
  item: { path: string; status: GridStatus },
  overrides: Record<string, GridStatus>,
): GridStatus {
  return overrides[item.path] ?? item.status;
}

export function matchesFilter(status: GridStatus, filter: StatusFilter): boolean {
  if (filter === "keep") return status !== "delete";
  if (filter === "delete") return status === "delete";
  return true;
}

export function dateOf(shot_at: string | null): string {
  if (!shot_at) return "Unknown";
  return shot_at.slice(0, 10);
}

// Status is conveyed by border colour alone, so tiles carry no text overlay and
// can run large.
export const TILE_MIN_PX = 480;
// Kept in sync with TIMELINE_THUMB_WIDTH in webapp/server.py, which prewarms
// this width's cache so the grid isn't waiting on resizes as it scrolls.
// Tiles are square-cropped via object-cover, so a landscape frame needs ~1.5x
// the tile's width to fill it without upscaling.
export const THUMB_W = 800;
