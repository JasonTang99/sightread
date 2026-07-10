export interface ImageData {
  path: string;
  score: number;
  centrality: number;
  rank: number;
}

export interface Cluster {
  cluster_id: number;
  best_image: string;
  images: ImageData[];
}

export interface ClusterDecision {
  kept: string[];
  deleted: string[];
}

export interface AppState {
  no_project: boolean;
  needs_pipeline: boolean;
  clusters: Cluster[];
  singletons: Cluster[];
  singleton_delete_threshold: number;
  pending_delete_count: number;
  undo_available: boolean;
  cluster_decisions: Record<string, ClusterDecision>;
  favorites: string[];
}

export interface GalleryPhoto {
  path: string;
  shot_at: string | null;
  cluster_id: number;
  cluster_size: number;
  status: "keep" | "delete" | "undecided";
}

export type ProjectStatus = "ready" | "stale" | "never_run" | "running";

export interface ProjectEntry {
  folder: string;
  display_name: string;
  last_opened: string | null;
  last_pipeline_run: string | null;
  image_count: number;
  status: ProjectStatus;
}

export interface FsEntry {
  name: string;
  path: string;
  is_dir: boolean;
  image_count: number;
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

export interface JobStatus {
  running: boolean;
  done: boolean;
  error: string | null;
  last_line: string | null;
  lines: string[];
  folder: string | null;
}
