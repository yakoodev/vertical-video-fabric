/**
 * Форматы вывода — зеркало app/formats.py (один реестр на бэкенд, превью и компоновщик).
 *
 * `frame` — размер готового кадра, `ref` — опорный кадр моушна: в нём заданы px слоёв
 * (кегль, ширина плашки), а x/y слоёв — доли кадра в любом формате. Короткая сторона
 * у всех — 1080, поэтому кегль 96 на любом формате — те же 96 px готового ролика.
 * У 9:16 опорный кадр ровно 1080×1920 — всё старое рисуется как раньше.
 */
import { REF_H, REF_W } from "./types";

export type FormatId = "9:16" | "1:1" | "4:5" | "16:9";

export type FormatInfo = {
  id: FormatId;
  label: string;
  frame: { w: number; h: number };
  ref: { w: number; h: number };
};

export const DEFAULT_FORMAT: FormatId = "9:16";

export const FORMATS: Record<FormatId, FormatInfo> = {
  "9:16": { id: "9:16", label: "Вертикаль 9:16", frame: { w: 1080, h: 1920 }, ref: { w: REF_W, h: REF_H } },
  "1:1": { id: "1:1", label: "Квадрат 1:1", frame: { w: 1080, h: 1080 }, ref: { w: 1080, h: 1080 } },
  "4:5": { id: "4:5", label: "4:5", frame: { w: 1080, h: 1350 }, ref: { w: 1080, h: 1350 } },
  "16:9": { id: "16:9", label: "Горизонталь 16:9", frame: { w: 1920, h: 1080 }, ref: { w: 1920, h: 1080 } },
};

export const FORMAT_IDS = Object.keys(FORMATS) as FormatId[];

const ALIASES: Record<string, FormatId> = {
  "9x16": "9:16", vertical: "9:16", "вертикаль": "9:16",
  "1x1": "1:1", square: "1:1", "квадрат": "1:1",
  "4x5": "4:5",
  "16x9": "16:9", horizontal: "16:9", landscape: "16:9", "горизонталь": "16:9",
};

/** Любое значение → id формата; мусор и пусто — 9:16 (как settings_format на бэкенде). */
export function normalizeFormat(value: unknown): FormatId {
  const text = String(value ?? "").trim().toLowerCase().replace(/\s+/g, "");
  const id = (ALIASES[text] ?? text) as FormatId;
  return id in FORMATS ? id : DEFAULT_FORMAT;
}

/** Формат клипа из его render_settings: `format`, у старых — `aspect`; нет — 9:16. */
export function clipFormat(settings: { format?: unknown; aspect?: unknown } | null | undefined): FormatId {
  return normalizeFormat(settings?.format ?? settings?.aspect);
}

/** Формат, в котором размечены слои клипа (`layers_format`); нет — 9:16. */
export function layersFormat(settings: { layers_format?: unknown } | null | undefined): FormatId {
  return normalizeFormat(settings?.layers_format);
}

export function frameSize(fmt: unknown): { w: number; h: number } {
  return { ...FORMATS[normalizeFormat(fmt)].frame };
}

export function formatRefSize(fmt: unknown): { w: number; h: number } {
  return { ...FORMATS[normalizeFormat(fmt)].ref };
}

/** Безопасная зона площадок формата (доли кадра) — зеркало app/safe_zones.py. */
export type SafeZone = {
  platforms: string;
  top: number;
  bottom: number;
  left: number;
  right: number;
  /** Низ блока субтитров (слой captions и прожиг ASS) — не ниже этой доли кадра. */
  captionsBottom: number;
  /** Угол водяного знака канала (16:9, YouTube) — туда титры не ставим. */
  watermark: { x0: number; y0: number; x1: number; y1: number } | null;
};

export const SAFE_ZONES: Record<FormatId, SafeZone> = {
  // Shorts / Reels / TikTok: сверху шапка, снизу подпись и кнопки; бока не проверяем (как всегда).
  "9:16": { platforms: "YouTube Shorts / Instagram Reels / TikTok", top: 0.1, bottom: 0.8, left: 0, right: 1, captionsBottom: 0.82, watermark: null },
  // Лента Instagram / Telegram: интерфейса поверх нет, превью режут углы — поля 5 %.
  "1:1": { platforms: "лента Instagram / Telegram", top: 0.05, bottom: 0.95, left: 0.05, right: 0.95, captionsBottom: 0.94, watermark: null },
  "4:5": { platforms: "лента Instagram / Telegram", top: 0.05, bottom: 0.95, left: 0.05, right: 0.95, captionsBottom: 0.94, watermark: null },
  // YouTube: поля 5 % со всех сторон, в правом нижнем углу — водяной знак канала.
  "16:9": {
    platforms: "YouTube", top: 0.05, bottom: 0.95, left: 0.05, right: 0.95, captionsBottom: 0.93,
    watermark: { x0: 0.86, y0: 0.78, x1: 0.985, y1: 0.97 },
  },
};

export function safeZone(fmt: unknown): SafeZone {
  return SAFE_ZONES[normalizeFormat(fmt)];
}

/**
 * Формат по размеру кадра (канвас превью, кадр компоновщика): ближайший по пропорции.
 * Вертикаль (высота ≥ 1.7 ширины) — всегда 9:16.
 */
export function formatOfFrame(width: number, height: number): FormatId {
  const w = Number(width);
  const h = Number(height);
  if (!(w > 0 && h > 0) || h >= w * 1.7) return DEFAULT_FORMAT;
  const ratio = w / h;
  let best: FormatId = DEFAULT_FORMAT;
  let gap = Infinity;
  for (const id of FORMAT_IDS) {
    const f = FORMATS[id].frame;
    const d = Math.abs(Math.log(ratio / (f.w / f.h)));
    if (d < gap) {
      gap = d;
      best = id;
    }
  }
  return best;
}

/**
 * Поля RenderEnv для формата: размер кадра превью `width` (высота — по пропорции)
 * и опорный кадр. У 9:16 опорный не задаётся — движок берёт 1080×1920 сам.
 */
export function formatEnv(fmt: unknown, width: number): { width: number; height: number; refWidth?: number; refHeight?: number } {
  const info = FORMATS[normalizeFormat(fmt)];
  const height = Math.round((width * info.frame.h) / info.frame.w);
  if (info.ref.w === REF_W && info.ref.h === REF_H) return { width, height };
  return { width, height, refWidth: info.ref.w, refHeight: info.ref.h };
}
