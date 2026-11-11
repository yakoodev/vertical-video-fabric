import { api } from "./client";
import type { CameraKey, Layer, LayerTracking, TrackTarget } from "@/motion";

/** Отчёт трекинга слоя (app/tracking.track_layer). */
export type TrackReport = {
  found: number;
  lost: [number, number][];
  samples: number;
  keys: Record<string, number>;
  follow_camera: boolean;
  cached: boolean;
  seconds?: number;
  notes: string[];
  warnings: string[];
  check_times: number[];
};

/** Фоновая задача трекинга (GET /api/track-jobs/{id}). */
export type TrackJob = {
  id: string;
  state: "queued" | "running" | "done" | "error";
  progress: number;
  stage: string;
  result: { layer: Layer; report: TrackReport } | null;
  error: string | null;
  hints: string[];
};

export type TrackPreset = { id: string; title: string; offset: NonNullable<LayerTracking["offset"]>; follow: Partial<NonNullable<LayerTracking["follow"]>> };

/** Шрифт из `vendor/fonts/fonts.json` или свой (`data/fonts`, app/user_fonts.py) — тот же файл подключает и рендер. */
export type MotionFont = {
  family: string;
  file: string;
  weight: number;
  style: string;
  label?: string;
  hint?: string;
  tags?: string[];
  url?: string;
  /** Свой, загруженный владельцем/агентом (можно удалить). */
  user?: boolean;
  /** Вариативный: вес — любой из `weight_range` (ось wght); нет диапазона (WOFF2) — 100..900. */
  variable?: boolean;
  weight_range?: [number, number];
  cyrillic?: boolean | null;
};

export type EmojiItem = { code: string; char: string; name?: string; group?: string; url?: string };

export type PackItem = {
  file: string;
  name?: string;
  tags?: string[];
  duration?: number;
  width?: number;
  height?: number;
  url?: string;
  ref?: string;
  box_count?: number;
};

export type PackSummary = {
  id: string;
  name: string;
  kind: "emoji" | "sticker" | "sfx" | "meme" | string;
  license?: string;
  source?: string;
  count?: number;
  items?: PackItem[];
};

// Ответы списков бывают и голым массивом, и обёрткой {items|fonts|packs: […]} —
// принимаем оба вида, чтобы клиент не ломался от формы конверта.
function listOf<T>(raw: unknown, ...keys: string[]): T[] {
  if (Array.isArray(raw)) return raw as T[];
  if (raw && typeof raw === "object") {
    for (const key of keys) {
      const value = (raw as Record<string, unknown>)[key];
      if (Array.isArray(value)) return value as T[];
    }
  }
  return [];
}

/** Анимация файла библиотеки одним листом кадров (GET /api/montage-assets/{id}/anim). */
export type AssetAnim = {
  animated: boolean;
  count?: number;
  cols?: number;
  rows?: number;
  frame_w?: number;
  frame_h?: number;
  times?: number[];
  duration?: number;
  width?: number;
  height?: number;
  sheet?: string;
  /** webm с альфой: превью играет сам файл (<video>), а не лист кадров. */
  video?: boolean;
};

/** Состояния голоса PNGtuber по кадрам (POST /api/clip-plans/{id}/voice). */
export type VoiceTrackDto = { t0: number; fps: number; states: string; levels?: number[]; threshold_db?: number };

/** Огибающие громкости клипа для react слоёв (POST /api/clip-plans/{id}/levels): v — 0..100 по кадрам. */
export type LevelTrackDto = { t0: number; fps: number; v: number[] };

/** Пресет 3D-движения камеры (POST /api/motion/camera-preset): новые ключи, ничего не сохраняет. */
export type CameraPresetResult = { preset: string; camera: CameraKey[]; layers: unknown[]; keys: unknown[]; range: [number, number]; note: string };

export const motionApi = {
  cameraPreset: (body: { preset: string; start: number; duration?: number; intensity?: number; camera: CameraKey[]; clip_duration?: number }) =>
    api.post<CameraPresetResult>("/api/motion/camera-preset", body),
  /** `layers` — волны со своим звуком и их звуковые слои (огибающие `wave:<id>`); нет — сохранённые. */
  levels: (planId: number, sources: string[], fps = 30, scale: "clip" | "final" = "clip", layers?: unknown[]) =>
    api.post<{ levels: Record<string, LevelTrackDto>; notes: string[] }>(`/api/clip-plans/${planId}/levels`, { sources, fps, scale, ...(layers ? { layers } : {}) }),
  assetAnim: (assetId: string) => api.get<AssetAnim>(`/api/montage-assets/${encodeURIComponent(assetId)}/anim`),
  voice: (planId: number, layers: unknown[], fps = 30, scale: "clip" | "final" = "clip") =>
    api.post<{ voice: Record<string, VoiceTrackDto>; notes: string[] }>(`/api/clip-plans/${planId}/voice`, { layers, fps, scale }),
  fonts: async () => listOf<MotionFont>(await api.get<unknown>("/api/motion/fonts"), "fonts", "items"),
  /** Свой шрифт (TTF/OTF/WOFF2): POST /api/motion/fonts; лицензия — на совести загружающего. */
  uploadFont: (file: File, family = "") => {
    const fd = new FormData();
    fd.append("file", file);
    if (family) fd.append("family", family);
    return api.form<MotionFont>("/api/motion/fonts", fd);
  },
  deleteFont: (file: string) => api.del<{ deleted: boolean }>(`/api/motion/fonts/${encodeURIComponent(file)}`),
  emoji: async (q: string, limit = 120) =>
    listOf<EmojiItem>(
      await api.get<unknown>(`/api/motion/emoji?q=${encodeURIComponent(q)}&limit=${limit}`),
      "items",
      "emoji",
      "results",
    ),
  packs: async () => listOf<PackSummary>(await api.get<unknown>("/api/motion/packs"), "packs", "items"),
  pack: async (id: string, q = "") =>
    listOf<PackItem>(
      await api.get<unknown>(`/api/motion/packs/${encodeURIComponent(id)}?q=${encodeURIComponent(q)}`),
      "items",
    ),
  schema: () => api.get<Record<string, unknown>>("/api/motion/schema"),
  /** Запустить трек слоя (ничего не сохраняет): ответ — id задачи, итог — новый слой. */
  track: (
    planId: number,
    body: { layer: Layer; target: TrackTarget; preset?: string; follow?: { x?: boolean; y?: boolean; scale?: boolean }; smooth?: number },
  ) => api.post<{ job_id: string }>(`/api/clip-plans/${planId}/track`, body),
  trackJob: (jobId: string) => api.get<TrackJob>(`/api/track-jobs/${encodeURIComponent(jobId)}`),
  trackPresets: () => api.get<{ presets: TrackPreset[]; smooth: number }>("/api/motion/track-presets"),
};

/** Снять трек: ключи, записанные трекингом, и пометку; слой остаётся там, где трек начинался. */
export function untrackLayer(layer: Layer): Layer {
  const marker = layer.tracking;
  if (!marker) return layer;
  const keys = { ...(layer.keys ?? {}) } as Record<string, { t: number; v: number }[] | undefined>;
  const next: Record<string, unknown> = { ...layer };
  for (const prop of marker.props ?? []) {
    const first = keys[prop]?.[0];
    if (first) next[prop] = first.v;
    delete keys[prop];
  }
  delete next.tracking;
  next.keys = keys;
  return next as Layer;
}

/** URL файла пака: и для превью картинки, и для прослушивания звука. */
export function packFileUrl(pack: string, file: string): string {
  return `/api/motion/packs/${encodeURIComponent(pack)}/${file.split("/").map(encodeURIComponent).join("/")}`;
}
