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

/** Горизонтальный кадр (16:9). */
export function isLandscape(fmt: unknown): boolean {
  const { w, h } = frameSize(fmt);
  return w > h;
}

/** Исходник уже кадра горизонтального формата больше, чем во столько раз, — «fit» (app/formats.py). */
export const FIT_SOURCE_RATIO = 0.75;

/**
 * Как основа ложится в кадр формата (app/formats.source_strategy): «cover» — умный кроп
 * окном пропорции кадра; «fit» — исходник целиком по высоте на размытой подложке
 * (вертикальный/квадратный исходник в 16:9). У 9:16, 1:1, 4:5 — всегда «cover».
 */
export function sourceStrategy(
  fmt: unknown,
  srcW: number,
  srcH: number,
  crop?: { w?: number; h?: number } | null,
): "cover" | "fit" {
  if (!isLandscape(fmt)) return "cover";
  let sw = Number(srcW) || 0;
  let sh = Number(srcH) || 0;
  if (sw <= 0 || sh <= 0) return "cover";
  if (crop) {
    sw *= Number(crop.w) || 1;
    sh *= Number(crop.h) || 1;
  }
  if (sh <= 0) return "cover";
  const { w, h } = frameSize(fmt);
  return sw / sh < (w / h) * FIT_SOURCE_RATIO ? "fit" : "cover";
}
