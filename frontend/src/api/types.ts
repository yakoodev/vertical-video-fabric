import type { Background, CameraKey, FormatId, Layer } from "@/motion";
import type { StreamLayout } from "@/pages/workspace/layout";
import type { AudioEdit } from "@/api/audioEdit";
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
  /** Человеческое название видео (app/nav.py): название VOD, имя кусков, дата. */
  display_name?: string;
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
  /** Клипов для человека: группа версий считается один раз. */
  clip_cards_count?: number;
  clips_count?: number;
  /** Проект-серия/канал источника (app/projects.py); null — без проекта. */
  project_id?: number | null;
  /** Файл записи на месте. false — удалён: кадры и видео не запрашивать, моменты и клипы живы. */
  has_file?: boolean | null;
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
  /** Title/description/hashtags written by the service (after render or ✨), with per-platform variants. */
  publish_meta?: PublishMeta | null;
  origin?: string;
  created_at: string;
  updated_at: string;
}

export interface PublishMeta {
  title: string;
  description: string;
  hashtags: string[];
  platforms?: Partial<Record<"youtube" | "tiktok", { title: string; description: string }>>;
  cover_hooks?: string[];
  model?: string;
  source?: "auto" | "ai" | "manual";
  generated_at?: string;
}

/** A cover variant of a rendered clip (app/cover_variants.py). */
export interface CoverVariant {
  id: number;
  clip_id: number;
  url: string;
  width: number;
  height: number;
  format: string;
  t: number;
  text: string;
  style: string;
  text_y: number;
  faces: number;
  score: number;
  renderer: string;
  chosen: boolean;
  created_at: string;
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
  /** Скорость куска в клипе (set_segment_speed, app/piece_speed.py): нет — 1. */
  speed?: number;
  /** Кусок задом наперёд (в превью играет вперёд, развернёт рендер). */
  reverse?: boolean;
  /** Звук куска со скоростью/реверсом: tempo | pitch | mute | reverse. */
  speed_audio?: SpeedAudio;
  /** 🎨 Цветокоррекция куска (set_piece_color / match_piece_colors, app/piece_color.py). */
  color_fix?: ColorFix;
}

export type SpeedAudio = "tempo" | "pitch" | "mute" | "reverse";

/** Поправка цвета куска: нет поля — нейтрально (exposure в EV, temperature/tint −1..1). */
export interface ColorFix {
  exposure?: number;
  contrast?: number;
  saturation?: number;
  temperature?: number;
  tint?: number;
}

/** Превью поправки цвета CSS-фильтром (приближённо; как app/piece_color.preview_css). */
export function colorFixCss(fix?: ColorFix | null): string {
  if (!fix) return "";
  const bits: string[] = [];
  if (fix.exposure) bits.push(`brightness(${(2 ** fix.exposure).toFixed(3)})`);
  if (fix.contrast != null && fix.contrast !== 1) bits.push(`contrast(${fix.contrast.toFixed(3)})`);
  if (fix.saturation != null && fix.saturation !== 1) bits.push(`saturate(${fix.saturation.toFixed(3)})`);
  const t = fix.temperature ?? 0;
  if (t > 0) bits.push(`sepia(${(0.3 * t).toFixed(3)})`);
  else if (t < 0) bits.push(`hue-rotate(${(12 * t).toFixed(1)}deg)`);
  if (fix.tint) bits.push(`hue-rotate(${(-10 * fix.tint).toFixed(1)}deg)`);
  return bits.join(" ");
}

/** Длина куска на таймлайне клипа: (конец − начало исходника) / скорость. */
export function segLen(piece: { start_sec: number; end_sec: number; speed?: number }): number {
  const speed = piece.speed && piece.speed > 0 ? piece.speed : 1;
  return Math.max(0, piece.end_sec - piece.start_sec) / speed;
}

/** Скорость куска (1 — как в исходнике). */
export function segSpeed(piece: { speed?: number }): number {
  return piece.speed && piece.speed > 0 ? piece.speed : 1;
}

export interface ClipPlan {
  id: number;
  /** Группа версий одного клипа (app/clip_groups.py); null — план сам себе клип. */
  clip_group_id?: number | null;
  group_locked?: number;
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
  /** Владелец взял черновик в работу: ★, правка или кнопка в чате (app/agent_settings.py). */
  owner_picked?: boolean | number;
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
  /** Тишина вместо звука исходника на отрезках (секунды клипа до вырезов); track — одна дорожка. */
  mute?: { start: number; end: number; track?: number }[];
  /** Громкость звука исходника на отрезках, дБ (секунды клипа до вырезов, app/audio_mute.py). */
  gain?: { start: number; end: number; gain_db: number; track?: number }[];
  /** 🎚 Звуковой монтаж: J/L, ключи громкости, ducking и провалы музыки, эффекты (app/audio_edit.py). */
  audio?: AudioEdit;
  /** ⏩ Скорость/реверс кусков [{piece (segment id), speed?, reverse?, audio?}] (app/piece_speed.py). */
  speed?: { piece: number; speed?: number; reverse?: boolean; audio?: SpeedAudio }[];
  /** 🎨 Цветокоррекция кусков [{piece (segment id), exposure?, …}] (app/piece_color.py). */
  color_fix?: ({ piece: number } & ColorFix)[];
  /** Формат вывода (app/formats.py): 9:16 (как всегда), 1:1, 4:5 или 16:9 — у любого клипа. */
  format?: Aspect;
  /** В каком формате размечены слои (их опорный кадр, motion/formats.ts); нет — 9:16. */
  layers_format?: Aspect;
  /** Старое имя `format` (настройки до форматов вывода) — читается как format. */
  aspect?: Aspect;
  /** Стоп-кадр в конце готового ролика, с (0..1.5); null — как у сервиса (RENDER_END_HOLD_SEC). */
  end_hold_sec?: number | null;
  /** Motion blur клипа по умолчанию, затвор 0..1 (motion/motionblur.ts): слоям и камере без своего; null — выкл. */
  motion_blur?: number | null;
  /** Стоп-кадры посреди ролика (app/freeze.py, инструмент агента freeze_frame); at — шкала final. */
  freezes?: FreezeFrame[];
  /**
   * Концовка-баннер (app/end_screen.py, MCP set_end_screen): последние `duration` с видео уходит в
   * размытие (`blur` px) и темнеет (`dim`), поверх — слой `end_banner`. Превью — та же функция движка.
   */
  end_screen?: { duration: number; blur: number; dim: number; ramp: number; template?: string | null; [k: string]: unknown } | null;
  /** 🎚 Сведение (app/audio_mix.py: auto_mix / set_mix): громкость музыки, sidechain, поправки слоёв, эффекты голоса/музыки. */
  mix?: MixSettings;
}

/** render_settings.mix — ручки сведения; пустые поля сервер не хранит. */
export interface MixSettings {
  target_lufs?: number;
  music_db?: number;
  sidechain?: false;
  duck_attack?: number;
  duck_release?: number;
  groups?: Partial<Record<"voice" | "sfx" | "music" | "video", number>>;
  layer_gains?: Record<string, number>;
  voice_fx?: { fx: string; [k: string]: unknown }[];
  music_fx?: { fx: string; [k: string]: unknown }[];
  auto?: Record<string, unknown>;
}

/** Стоп-кадр посреди ролика: кадр в `at` стоит `duration` с; звук молчит (вставка) или идёт дальше. */
export type FreezeFrame = {
  at: number;
  duration: number;
  audio: "silence" | "continue";
  layers: "freeze" | "continue";
};

/** Формат готового ролика (app/formats.py, motion/formats.ts). */
export type Aspect = FormatId;

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
  /** 'agent' — поставила настройка проекта «Публиковать самому». */
  made_by?: string;
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
  /** Серия, в очередь одобрения которой ушли клипы (без серии — «Авто — без серии»). */
  project_id?: number | null;
  plans: number;
  clips: number;
  /** Прежнее поле: «Авто» больше не создаёт заданий публикации, всегда 0. */
  jobs: number;
  approvals?: number;
  auto_approved?: number;
  warnings?: string[];
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
