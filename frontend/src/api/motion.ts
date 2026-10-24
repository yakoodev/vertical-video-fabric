import { api } from "./client";

/** Шрифт из `vendor/fonts/fonts.json` — тот же файл подключает и рендер. */
export type MotionFont = {
  family: string;
  file: string;
  weight: number;
  style: string;
  label?: string;
  hint?: string;
  tags?: string[];
  url?: string;
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
};

/** Состояния голоса PNGtuber по кадрам (POST /api/clip-plans/{id}/voice). */
export type VoiceTrackDto = { t0: number; fps: number; states: string; levels?: number[]; threshold_db?: number };

export const motionApi = {
  assetAnim: (assetId: string) => api.get<AssetAnim>(`/api/montage-assets/${encodeURIComponent(assetId)}/anim`),
  voice: (planId: number, layers: unknown[], fps = 30) =>
    api.post<{ voice: Record<string, VoiceTrackDto>; notes: string[] }>(`/api/clip-plans/${planId}/voice`, { layers, fps }),
  fonts: async () => listOf<MotionFont>(await api.get<unknown>("/api/motion/fonts"), "fonts", "items"),
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
};

/** URL файла пака: и для превью картинки, и для прослушивания звука. */
export function packFileUrl(pack: string, file: string): string {
  return `/api/motion/packs/${encodeURIComponent(pack)}/${file.split("/").map(encodeURIComponent).join("/")}`;
}
