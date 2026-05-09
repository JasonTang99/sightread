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

export interface AppState {
  no_project: boolean;
  needs_pipeline: boolean;
  clusters: Cluster[];
  singletons: Cluster[];
  singleton_delete_threshold: number;
  pending_delete_count: number;
  undo_available: boolean;
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

export interface JobStatus {
  running: boolean;
  done: boolean;
  error: string | null;
  last_line: string | null;
  folder: string | null;
}
