export interface ImageData {
  path: string;
  score: number;
  centrality: number;
  rank: number;
  exif_timestamp?: number;
  // The Live Photo motion file, when the still has one.
  motion?: string;
  model?: string;
}

export interface Cluster {
  cluster_id: number;
  best_image: string;
  images: ImageData[];
  // Unix seconds of the cluster's first shot; absent when no image has EXIF.
  cluster_timestamp?: number;
}

// Decisions are keyed by photo path, not cluster id: cluster ids are assigned
// per pipeline run, so re-running renumbers them and strands every decision.
// One status per photo — see webapp/utils.py for why these are a single field.
//   kept       reviewed, staying
//   favorite   starred; a stronger `kept` that deletion must never touch
//   to_delete  marked for deletion, not yet applied — this is the queue
//   deleted    already unlinked from the primary drive
export type PhotoDecision = "kept" | "favorite" | "to_delete" | "deleted";
export type PhotoDecisions = Record<string, PhotoDecision>;

export interface AppState {
  no_project: boolean;
  needs_pipeline: boolean;
  // The project folder. A photo's camera folder is its path relative to this,
  // which is what the device badges show.
  folder?: string;
  subtrip?: string | null;
  display_name?: string;
  clusters: Cluster[];
  singletons: Cluster[];
  singleton_delete_threshold: number;
  pending_delete_count: number;
  undo_available: boolean;
  photo_decisions: PhotoDecisions;
  favorites: string[];
  // When curation was marked finished, or null. A finished project has had its
  // thumbnails, posters and transcodes deleted and is not prewarmed.
  done_at: string | null;
}

// What a grid draws: the four stored decisions collapsed to survives / doomed /
// not yet looked at. Videos carry the same statuses as photos.
export type GridStatus = "keep" | "delete" | "undecided";

export interface GalleryPhoto {
  path: string;
  shot_at: string | null;
  cluster_id: number;
  cluster_size: number;
  status: GridStatus;
  motion?: string;
  // EXIF camera model, when the pipeline read one.
  model?: string;
}

export type VideoStatuses = Record<string, GridStatus>;

export type ProjectStatus = "ready" | "stale" | "never_run" | "running";

export interface ProjectEntry {
  folder: string;
  subtrip?: string | null;
  display_name: string;
  last_opened: string | null;
  last_pipeline_run: string | null;
  image_count: number;
  status: ProjectStatus;
  done_at: string | null;
  /** Photos the project's caches do not cover yet. */
  pending_count: number;
  /** Estimated pipeline run time in seconds; null when there is nothing to run. */
  eta_s: number | null;
}

export interface FsEntry {
  name: string;
  path: string;
  is_dir: boolean;
  image_count: number;
  pending_count: number;
  eta_s: number | null;
}

export interface FsListing {
  path: string;
  parent: string | null;
  entries: FsEntry[];
}

export interface VideoHighlightClip {
  start: number;
  end: number;
  score: number;
  scores: { motion: number; scene_change: number; novelty: number };
}

export interface VideoHighlights {
  duration: number;
  clips: VideoHighlightClip[];
}

export type VideoHighlightsMap = Record<string, VideoHighlights>;

export interface UserClip {
  start: number;
  end: number;
}

export type UserClipsMap = Record<string, { clips: UserClip[] }>;

export interface VideoTagsState {
  tags: string[];
  assignments: Record<string, string>;
}

export interface JobStatus {
  running: boolean;
  done: boolean;
  error: string | null;
  last_line: string | null;
  lines: string[];
  folder: string | null;
  subtrip?: string | null;
}
