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
  clip_plans_count?: number;
  clips_count?: number;
  has_transcript?: boolean;
  transcript_segments?: number;
  focus_preset?: string;
  focus_strategy?: string;
  cut_strategy?: string;
}

/** Quality report of a finished render (app/render_qc.py). */
export interface RenderQc {
  ok: boolean;
  duration_sec: number;
  width: number;
  height: number;
  loudness_lufs: number | null;
  true_peak_dbfs: number | null;
  issues: { level: "warn" | "info"; code: string; text: string }[];
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
  qc?: RenderQc | null;
  cover_url?: string | null;
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
  started_at?: string | null;
  /** JSON {"stage","done","total"} while running */
  progress_json?: string;
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
  manual_focus_x?: number | null;
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
  quality?: number;
  duplicate_of?: number | null;
  favorite?: boolean | number;
  hidden?: boolean | number;
  // Own render settings of this clip (null = never set up in the editor).
  render_settings?: RenderSettings | null;
  /** A 🤖 ИИ-монтаж was applied and the previous version can be restored. */
  has_montage_backup?: boolean;
  /** ✨ Picked by AI as one of the best moments: 1–10 score + why. */
  ai_pick?: { score: number; reason: string } | null;
}

export type TransitionType =
  | "cut" | "fade" | "fadeblack" | "fadewhite" | "flash"
  | "slide" | "smooth" | "wipe" | "zoom" | "dissolve";

export interface TransitionSettings {
  type: TransitionType;
  duration: number;
  audio: "smooth" | "hard";
  sfx: "none" | "whoosh" | "click" | "pop";
  sfx_volume: number;
}

/** Clip cover: a frame of the clip or an uploaded picture, optionally burned in first. */
export interface CoverSettings {
  mode: "none" | "frame" | "image";
  piece: number;
  offset: number;
  image: string;
  burn: boolean;
  burn_sec: number;
}

/** Per-clip render settings — mirrors the editor's render panel one to one. */
export interface RenderSettings {
  preset_id: number | null;
  subs_on: boolean;
  sub_id: number | null;
  sub_engine: string;
  sub_pos_pct: number;
  banner_on: boolean;
  banner_id: number | null;
  banner_height_pct: number;
  banner_pos_pct: number;
  music_on: boolean;
  track_id: number | null;
  mirror: boolean;
  transition: TransitionSettings;
  cover: CoverSettings;
  /** «Файлы для монтажа» dropped into this clip (clip timeline seconds). */
  inserts?: MontageInsert[];
}

export interface MontageInsert {
  asset_id: number;
  at: number;
  duration: number;
  mode: "full" | "pip" | "sound";
  volume: number;
  duck: boolean;
  reason?: string;
}

export interface TransitionOptions {
  types: { key: TransitionType; label: string }[];
  audio: { key: "smooth" | "hard"; label: string }[];
  sfx: { key: TransitionSettings["sfx"]; label: string }[];
  default: TransitionSettings;
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
  kind: "job" | "clip" | "analysis" | "download";
  id: number;
  status: string;
  label: string;
  error: string;
  created_at: string;
  updated_at: string;
  scheduled_at?: string | null;
  source_id?: number | null;
  detail?: string;
  /** Downloads only: 0–100. */
  progress?: number;
}
