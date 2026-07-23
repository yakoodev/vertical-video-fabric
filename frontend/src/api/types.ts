export interface CropRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface FocusPoint {
  t: number;
  x: number;
  y?: number;
  /** Hard scene cut — the reframe jumps here instead of easing across shots. */
  cut?: boolean;
}

export interface Source {
  id: number;
  status: string;
  source_type: string;
  content_crop?: CropRect | null;
  original_url: string;
  original_filename: string;
  local_path: string;
  duration_sec: number;
  width: number;
  height: number;
  fps: number;
  size_bytes: number;
  error: string;
  created_at: string;
  updated_at: string;
  analyses_count?: number;
  clips_count?: number;
  has_transcript?: boolean;
  transcript_segments?: number;
  focus_preset?: string;
  focus_strategy?: string;
}

export interface Clip {
  id: number;
  source_id: number;
  clip_plan_id: number | null;
  segment_id: number | null;
  status: string;
  title: string;
  description: string;
  duration_sec: number;
  width: number;
  height: number;
  size_bytes: number;
  error: string;
  posts_count?: number;
  published_targets_count?: number;
  origin?: string;
  created_at: string;
  updated_at: string;
}

export interface AiAnalysis {
  id: number;
  source_id: number;
  provider: string;
  model: string;
  status: string;
  error: string;
  created_at: string;
  updated_at: string;
}

export interface AiSegment {
  id: number;
  source_id: number;
  analysis_id: number;
  start_sec: number;
  end_sec: number;
  title: string;
  description: string;
  score: number;
  category: string;
  color: string;
  status: string;
  focus?: FocusPoint[];
}

export interface ClipPlan {
  id: number;
  source_id: number;
  analysis_id: number | null;
  status: string;
  title: string;
  description: string;
  score: number;
  category: string;
  color: string;
  segments: AiSegment[];
}

export interface SourceDetail extends Source {
  analyses: AiAnalysis[];
  segments: AiSegment[];
  clip_plans: ClipPlan[];
  clips: Clip[];
}

export interface JobTarget {
  id: number;
  job_id: number;
  account_id: number;
  platform: string;
  status: string;
  remote_id: string;
  remote_url: string;
  error: string;
  account_label: string;
}

export interface Job {
  id: number;
  clip_id: number | null;
  status: string;
  title: string;
  description: string;
  privacy: string;
  scheduled_at: string;
  created_at: string;
  updated_at: string;
  error: string;
  targets: JobTarget[];
}

export interface Account {
  id: number;
  platform: string;
  label: string;
  cookie_count: number;
  has_required_cookies: boolean;
  missing_cookies: string;
  proxy_configured: boolean;
  proxy_display: string;
  updated_at: string;
}

export interface AutoRun {
  id: number;
  label: string;
  status: string;
  message: string;
  error: string;
  source_id: number | null;
  plans: number;
  clips: number;
  jobs: number;
}

export interface ActiveTask {
  kind: "job" | "clip" | "analysis";
  id: number;
  status: string;
  label: string;
  error: string;
  created_at: string;
  updated_at: string;
  scheduled_at?: string | null;
  source_id?: number | null;
  detail?: string;
}
