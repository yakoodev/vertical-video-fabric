import type { Background, CameraKey, Layer } from "@/motion";
import type { StreamLayout } from "@/pages/workspace/layout";
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
  /** Хромакей: источник на зелёнке, фон вырезается (app/chroma_key.py). */
  chroma_key?: { color: string; similarity: number; blend: number; despill?: "auto" | "none" | number } | null;
  /** Вебка стримера (доли кадра) — по умолчанию для раскладок split/pip_cam. */
  facecam?: CropRect | null;
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
  /** Звуковые дорожки (app/source_audio.py): номера в звуке ролика, они же с громкостью, для речи. */
  audio_tracks?: number[] | null;
  audio_mix?: { track: number; gain_db: number }[] | null;
  speech_tracks?: number[] | null;
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
  /** «Кусок шире окна 9:16» (set_piece_fit): доля ширины исходника и фон; null — обычный кадр. */
  fit?: { w: number; bg: string } | null;
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
  /** Субтитры, сделанные до рендера (правятся в редакторе). */
  subtitles?: { lines: { start: number; end: number; text: string }[]; edited?: boolean; provider?: string } | null;
  /** ✨ Picked by AI as one of the best moments: 1–10 score + why. */
  ai_pick?: { score: number; reason: string } | null;
}

export type TransitionType =
  | "cut" | "whip" | "zoompunch" | "glitch" | "flash"
  | "fade" | "fadeblack" | "fadewhite"
  | "slide" | "smooth" | "wipe" | "zoom" | "dissolve";

export interface TransitionSettings {
  type: TransitionType;
  duration: number;
  /** Умные склейки: jump-cut внутри сцены — встык, эффект только на смене сцены. */
  smart: boolean;
  audio: "smooth" | "hard";
  sfx: "none" | "whoosh" | "click" | "pop" | "boom";
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
  /** С какой секунды трека играет музыка на 0 с клипа (0 — с начала). */
  music_start_sec?: number;
  mirror: boolean;
  transition: TransitionSettings;
  cover: CoverSettings;
  /** «Файлы для монтажа» dropped into this clip (clip timeline seconds). */
  inserts?: MontageInsert[];
  /** Моушн-слои поверх клипа (docs/motion/spec.md). */
  layers?: Layer[];
  /** Наезды камеры: ключи зума по времени клипа. */
  camera?: CameraKey[];
  /** Раскладка стрима: вебка сверху + игра снизу и т.п. (app/stream_layout.py). */
  layout?: StreamLayout;
  /** ✂ Джамп-каты: вырезанные паузы/слова-паразиты, секунды клипа ДО вырезов (app/cuts.py). */
  cuts?: { start: number; end: number }[];
  /** Фон за вырезанным персонажем (источник с хромакеем), анимированный — app/backgrounds.py. */
  backdrop?: Background;
  /** Тишина вместо звука исходника на отрезках (секунды клипа до вырезов). */
  mute?: { start: number; end: number }[];
  /** Громкость звука исходника на отрезках, дБ (секунды клипа до вырезов, app/audio_mute.py). */
  gain?: { start: number; end: number; gain_db: number }[];
  /** Формат кадра: 9:16 (как всегда), 1:1 или 4:5 — пока только у холста (app/aspect.py). */
  aspect?: Aspect;
}

/** Формат готового ролика (app/aspect.py). */
export type Aspect = "9:16" | "1:1" | "4:5";

export interface MontageInsert {
  asset_id: number;
  at: number;
  duration: number;
  mode: "full" | "pip" | "sound";
  volume: number;
  duck: boolean;
  /** Центр вставки в долях кадра (двигается ручками на превью).
   *  Новую вставку можно создать без геометрии — сервер подставит умолчания. */
  x?: number;
  y?: number;
  /** Ширина вставки в долях ширины кадра. */
  scale?: number;
  reason?: string;
}

export interface TransitionOptions {
  types: { key: TransitionType; label: string }[];
  audio: { key: "smooth" | "hard"; label: string }[];
  sfx: { key: TransitionSettings["sfx"]; label: string }[];
  /** Переходы с фиксированной длиной — для них ползунок длительности не нужен. */
  snap: Partial<Record<TransitionType, number>>;
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
