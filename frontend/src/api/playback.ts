/**
 * ▶ Смотреть монтаж: план проигрывания клипа (app/playback.py) и прокси 480p (app/preview_proxy.py).
 */
import { api } from "./client";
import type { Aspect, ColorFix, FocusPoint } from "./types";
import type { CameraKey, CaptionWord, ChatMessage, EndScreenFx, Layer } from "@/motion";
import type { FreezeSpan, MusicAutomation, PlayPart, ValueSpan, VolumeKey } from "@/pages/workspace/preview/timeline";
import type { SubtitleLine } from "./clipPlans";
import type { SubtitleStyle, SubtitleWord } from "@/pages/workspace/subtitlePreview";

export type ProxyStatus = {
  status: "none" | "not_needed" | "queued" | "running" | "ready" | "failed";
  progress: number;
  url: string | null;
  size_bytes?: number;
  stale?: boolean;
  rebuilding?: string;
  error?: string;
};

/** Куски 480p по частям клипа (app/preview_proxy.py). */
export type PiecesStatus = {
  total: number;
  ready: number;
  failed: number;
  parts: { index: number; start: number; end: number; status: "none" | "queued" | "running" | "ready" | "failed"; url?: string; offset?: number; error?: string }[];
};

export type PlaybackMedia = {
  kind: "proxy" | "pieces" | "original" | "canvas" | "render" | "none";
  /** Готовый рендер клипа, если есть: его предлагает плеер, когда живой монтаж не успевает. */
  render?: { url: string; poster: string; clip_id: number; stale: boolean } | null;
  url: string | null;
  has_file: boolean;
  proxy: ProxyStatus;
  /** Подпись к проигрыванию с прокси при удалённой записи. */
  note?: string | null;
  /** Почему показан готовый рендер (или ничего). */
  message?: string;
  poster?: string;
  clip_id?: number;
  stale?: boolean;
};

export type PlaybackPart = PlayPart & {
  piece: number;
  segment_id: number;
  focus: FocusPoint[];
  fit?: unknown;
  color_fix?: ColorFix | null;
  speed_audio?: string | null;
  /** Куски 480p этой части, секунды записи (у длинной части их несколько). */
  chunks?: [number, number][];
  /** Начало части в записи, если это её кусок: по нему считается трек фокуса. */
  path_start?: number;
};

export type PlaybackInsert = {
  asset_id: number;
  at: number;
  duration: number;
  mode: "full" | "pip" | "sound";
  volume: number;
  x?: number;
  y?: number;
  scale?: number;
  url: string;
  kind: string;
  label: string;
  has_audio: boolean;
  duck_volume?: number | null;
};

export type PlaybackSfx = {
  id: string;
  url: string;
  start: number;
  end: number;
  offset: number;
  volume: number;
  gain_db: number;
  track: boolean;
};

export type PlaybackMusic = {
  url: string;
  track_id: number;
  label: string;
  volume: number;
  fixed_db?: number | null;
  start_sec: number;
  loop: boolean;
  fade_in: number;
  fade_out: number;
  duck: boolean;
  duck_amount: number;
  automation: MusicAutomation;
};

export type PlaybackAudioFx = { start: number; end: number; fx: string; amount?: number };

export type PlaybackPlan = {
  clip_plan_id: number;
  title: string;
  revision: string;
  source: {
    id: number;
    width: number;
    height: number;
    duration: number;
    content_crop: { x: number; y: number; w: number; h: number } | null;
    chroma_key: unknown;
    focus_preset?: string;
    canvas: boolean;
  };
  media: PlaybackMedia;
  format: Aspect;
  frame: { w: number; h: number };
  fps: number;
  duration: number;
  base_duration: number;
  hold: number;
  parts: PlaybackPart[];
  transition: { type: string; duration: number; audio: string; xfade: string | null; sfx: string; sfx_volume: number };
  freezes: { at: number; d: number; insert: boolean; hold: boolean }[];
  freezes_raw: FreezeSpan[];
  layers: Layer[];
  camera: CameraKey[];
  layers_format: Aspect;
  words: CaptionWord[];
  chat?: ChatMessage[];
  motion_blur?: number | null;
  end_screen?: EndScreenFx | null;
  layout: unknown;
  backdrop: unknown;
  mirror: boolean;
  look: { color_style?: string | null; color_strength?: number | null; vignette?: number | null; grain?: number | null; label?: string; id?: number };
  subtitles: { on: boolean; pos_pct: number; profile: SubtitleStyle | null; lines: SubtitleLine[]; words: SubtitleWord[] };
  banner: { url: string; height_pct: number; pos_pct: number } | null;
  inserts: PlaybackInsert[];
  audio: {
    mutes: ValueSpan[];
    gains: ValueSpan[];
    source_keys: VolumeKey[];
    fx: PlaybackAudioFx[];
    jl: { at: number; shift: number; prev_end: number; next_start: number }[];
    transition_audio: string;
    music: PlaybackMusic | null;
    sfx: PlaybackSfx[];
    join_sfx: { url: string; at: number; volume: number }[];
  };
  warnings: string[];
};

/** Рендер 480p во время показа (app/stream_preview.py): как идёт сборка. */
export type StreamStatus = {
  key: string | null;
  state: "queued" | "recording" | "rendering" | "ready" | "failed" | "no_source";
  error?: string | null;
  stage?: string;
  /** Адрес HLS: появляется, как только готов звук и план кадров (длина ролика известна сразу). */
  playlist?: string;
  total?: number;
  ready?: number;
  duration?: number;
  width?: number;
  height?: number;
  notes?: string[];
  message?: string;
  render?: { url: string; poster?: string; clip_id: number; stale: boolean } | null;
};

export const playbackApi = {
  stream: (clipPlanId: number, retry = false) => api.post<StreamStatus>(`/api/clip-plans/${clipPlanId}/stream`, { retry }),
  plan: (clipPlanId: number) => api.get<PlaybackPlan>(`/api/clip-plans/${clipPlanId}/playback`),
  revision: (clipPlanId: number) => api.get<{ revision: string }>(`/api/clip-plans/${clipPlanId}/revision`),
  proxyStatus: (sourceId: number | string) => api.get<ProxyStatus>(`/api/sources/${sourceId}/proxy`),
  proxyEnsure: (sourceId: number | string, force = false) => api.post<ProxyStatus>(`/api/sources/${sourceId}/proxy`, { force }),
  piecesEnsure: (clipPlanId: number, focus = 0, retry = false) =>
    api.post<PiecesStatus>(`/api/clip-plans/${clipPlanId}/preview-pieces`, { focus, retry }),
  packageUrl: (clipPlanId: number, pieces = false) => `/api/clip-plans/${clipPlanId}/timeline/package${pieces ? "?pieces=true" : ""}`,
};
