import { api } from "@/api/client";
import type { CropRect, Source, SourceDetail } from "@/api/types";

export interface Storyboard {
  count: number;
  interval_sec: number;
  frames: string[];
}

export interface FocusPreset {
  key: string;
  label: string;
  hint: string;
}
export interface FocusOptions {
  presets: FocusPreset[];
  strategies: FocusPreset[];
}

// One downloadable variant of a Kinobox-style player page (Smotvibe, gromfaer, …):
// a concrete season/episode with a concrete voice track.
export interface PlayerOption {
  id: string;
  provider: string;
  season: string;
  episode: string;
  translation: string;
  quality: string;
  title: string;
  duration_sec: number;
  media_url: string;
  referer: string;
  audio_format_id: string;
  filename_label: string;
}

/** Звуковая дорожка записи (app/source_audio.audio_streams): index — номер среди звуковых (0:a:N). */
export interface SourceAudioStream {
  index: number;
  title: string;
  channels?: number | null;
  language?: string;
}
/** Дорожка в звуке ролика и её громкость, дБ. */
export interface SourceAudioMix {
  track: number;
  gain_db: number;
}
/** GET /api/sources/{id}/audio — дорожки записи и что выбрано (app/source_audio.audio_view). */
export interface SourceAudio {
  tracks_total: number;
  selected: number[];
  mix: SourceAudioMix[];
  /** Дорожки для распознавания речи (без своей — те же, что selected). */
  speech_tracks: number[];
  /** Речь распознаётся по своей дорожке, а не по смеси ролика. */
  speech_separate: boolean;
  streams: SourceAudioStream[];
  note?: string;
}
export interface SourceAudioSaved {
  ok: boolean;
  project_id: number;
  audio: SourceAudio;
  /** Замер «Авто»: громкость дорожек, дБ. */
  levels?: Record<string, { median_db: number; p90_db: number }>;
  auto_gain?: Record<string, number>;
  /** Замер «auto» по дорожке голоса: громкость голоса и игры в моменты речи, итоговый перевес. */
  auto?: Record<string, { gain_db: number; voice_db?: number; others_db?: number; over_db?: number;
    speech_sec?: number; peak_limited?: boolean; note?: string }>;
  note?: string;
}
export interface SourceAudioPatch {
  /** Дорожки в звук ролика; gain_db "auto" — замерить и поставить голос над игрой; [] — по умолчанию (первая). */
  audio_tracks?: { track: number; gain_db: number | "auto" }[];
  /** Дорожки для распознавания; [] — как audio_tracks. */
  speech_tracks?: number[];
}

export const sourcesApi = {
  list: () => api.get<Source[]>("/api/sources"),
  focusOptions: () => api.get<FocusOptions>("/api/focus-presets"),
  setFocus: (id: number | string, body: { focus_preset?: string; focus_strategy?: string }) =>
    api.patch<Source>(`/api/sources/${id}/focus-preset`, body),
  cutStrategies: () => api.get<FocusPreset[]>("/api/cut-strategies"),
  refineCuts: (id: number | string, strategy: string) =>
    api.post<{ updated: number; strategy: string }>(`/api/sources/${id}/refine-cuts`, { strategy }),
  get: (id: number | string) => api.get<SourceDetail>(`/api/sources/${id}`),
  remove: (id: number | string) => api.del<{ deleted: boolean }>(`/api/sources/${id}`),
  rename: (id: number | string, name: string) => api.patch<Source>(`/api/sources/${id}`, { name }),
  storyboard: (id: number | string) => api.get<Storyboard>(`/api/sources/${id}/storyboard`),
  detectCrop: (id: number | string) => api.post<{ crop: CropRect | null }>(`/api/sources/${id}/detect-crop`),
  setCrop: (id: number | string, crop: CropRect | null) =>
    api.patch<SourceDetail>(`/api/sources/${id}/crop`, { crop }),
  /** Хромакей: найти зелёнку по кадрам (ничего не сохраняет), null — фон обычный. */
  detectKey: (id: number | string) =>
    api.post<{ chroma_key: NonNullable<Source["chroma_key"]> | null }>(`/api/sources/${id}/detect-key`),
  /** Сохранить ключ источника; null — выключить. Ответ — источник без клипов. */
  setChromaKey: (id: number | string, chroma_key: Source["chroma_key"]) =>
    api.patch<Source>(`/api/sources/${id}/chroma-key`, { chroma_key }),
  /** Звуковые дорожки записи: какие в звуке ролика, громкость каждой, по какой распознавать речь. */
  audio: (id: number | string) => api.get<SourceAudio>(`/api/sources/${id}/audio`),
  setAudio: (id: number | string, body: SourceAudioPatch) => api.patch<SourceAudioSaved>(`/api/sources/${id}/audio`, body),
  uploadFile: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return api.form<Source>("/api/sources", fd);
  },
  ingestUrl: (url: string, quality = "", option?: PlayerOption) =>
    api.post<Source>("/api/sources", {
      url,
      ...(quality ? { quality } : {}),
      ...(option
        ? {
            smotvibe_media_url: option.media_url,
            smotvibe_referer: option.referer,
            smotvibe_audio_format_id: option.audio_format_id,
            smotvibe_filename_label: option.filename_label,
          }
        : {}),
    }),
  // Seasons / episodes / voice tracks behind a player page.
  playerOptions: (url: string) => api.post<PlayerOption[]>("/api/smotvibe/options", { url }),
};

/** Вебка стримера для раскладок split / pip_cam (app/layout_api.py). */
export interface FacecamDetect {
  cam: CropRect | null;
  /** Доля кадров, где лицо стояло на месте (0..1). */
  confidence: number;
  frames: number;
  /** Найденная вебка запомнена на источнике. */
  saved: boolean;
}

export const facecamApi = {
  /** Найти вебку по ~20 кадрам VOD; найденная сохраняется на источнике. */
  detect: (sourceId: number | string) => api.post<FacecamDetect>(`/api/sources/${sourceId}/detect-facecam`),
  /** Сохранённая вебка источника (null — не задана). */
  get: (sourceId: number | string) => api.get<{ cam: CropRect | null }>(`/api/sources/${sourceId}/facecam`),
  /** Запомнить вебку для новых клипов источника (null — сбросить). */
  save: (sourceId: number | string, cam: CropRect | null) =>
    api.patch<{ cam: CropRect | null }>(`/api/sources/${sourceId}/facecam`, { cam }),
};

// Player pages hide their episode list behind a Kinobox iframe, so the picker is
// only offered for links that are not already a plain file or a known platform.
export function looksLikePlayerPage(url: string): boolean {
  let parsed: URL;
  try {
    parsed = new URL(url.trim());
  } catch {
    return false;
  }
  if (!/^https?:$/.test(parsed.protocol)) return false;
  const host = parsed.hostname.toLowerCase();
  if (/(^|\.)(youtube\.com|youtu\.be|twitch\.tv)$/.test(host)) return false;
  return !/\.(mp4|mov|webm|m3u8)$/i.test(parsed.pathname);
}

// Download-quality options for URL ingestion (mirrors app/ingest.py QUALITY_CHOICES).
export const QUALITY_OPTIONS: { value: string; label: string }[] = [
  { value: "", label: "Качество: авто" },
  { value: "best", label: "Максимальное" },
  { value: "1080", label: "1080p" },
  { value: "720", label: "720p" },
  { value: "480", label: "480p" },
  { value: "360", label: "360p" },
];
