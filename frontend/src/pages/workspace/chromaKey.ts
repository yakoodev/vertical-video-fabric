/**
 * Хромакей в превью редактора — как в рендере (app/chroma_key.py + ffmpeg chromakey).
 *
 * Рендер вырезает зелёнку в компоновщике; превью делает то же на маленьком
 * холсте: расстояние по цветности (U, V) до цвета ключа, ниже `similarity` —
 * прозрачно, дальше мягкий край шириной `blend`, плюс подавление зелёного ореола —
 * той же формулой, что фильтр ffmpeg despill (`despill`: auto / none / сила 0..1).
 */

import {
  DEFAULT_BACKGROUND,
  normalizeBackground,
  paintBackground,
  type Background,
  type BackgroundOptions,
} from "@/motion";

/** Подавление ореола: "auto" — despill ffmpeg как есть, "none" — выкл, число — мягкая сила 0..1. */
export type Despill = "auto" | "none" | number;

export interface ChromaKey {
  color: string;
  similarity: number;
  blend: number;
  /** Нет — "auto" (старые ключи). */
  despill?: Despill;
}

/** Множители каналов на карту ореола — как параметры red/green/blue фильтра despill. */
export function despillScales(key: Pick<ChromaKey, "color" | "despill">): [number, number, number] | null {
  const mode = key.despill ?? "auto";
  if (mode === "none") return null;
  const [, kg, kb] = rgb(key.color);
  const blue = kb > kg;
  // auto — умолчания ffmpeg: опускается зелёный при любом типе (так считает рендер).
  if (mode === "auto") return [0, -1, 0];
  const s = Math.min(1, Math.max(0, Number(mode) || 0));
  if (s <= 0) return null;
  return blue ? [0, 0, -s] : [0, -s, 0];
}

/** Фон за персонажем — процедурный фон движка (анимированный по умолчанию, motion/backgrounds.ts). */
export type Backdrop = Background;

export const DEFAULT_BACKDROP: Backdrop = DEFAULT_BACKGROUND;

/** Как app/chroma_key.normalize_backdrop: старый {color, color2} — заливка, не выбран — аврора. */
export function normalizeBackdrop(raw: unknown): Backdrop {
  return normalizeBackground(raw);
}

function rgb(color: string): [number, number, number] {
  return [parseInt(color.slice(1, 3), 16), parseInt(color.slice(3, 5), 16), parseInt(color.slice(5, 7), 16)];
}

/** Цветность BT.601 в 0..255 — так же считает ffmpeg chromakey. */
function uv(r: number, g: number, b: number): [number, number] {
  return [-0.168736 * r - 0.331264 * g + 0.5 * b + 128, 0.5 * r - 0.418688 * g - 0.081312 * b + 128];
}

/** Вырезать ключ в пикселях холста (на месте). */
export function applyChromaKey(ctx: CanvasRenderingContext2D, w: number, h: number, key: ChromaKey): void {
  const img = ctx.getImageData(0, 0, w, h);
  const d = img.data;
  const [kr, kg, kb] = rgb(key.color);
  const [ku, kv] = uv(kr, kg, kb);
  // Тип ореола — как app/chroma_key.despill_type: синий, только если синий ярче зелёного.
  const greenKey = kb <= kg;
  const scales = despillScales(key);
  const sim = key.similarity;
  const blend = key.blend;
  for (let i = 0; i < d.length; i += 4) {
    const r = d[i];
    const g = d[i + 1];
    const b = d[i + 2];
    const [u, v] = uv(r, g, b);
    const du = u - ku;
    const dv = v - kv;
    const diff = Math.sqrt((du * du + dv * dv) / (255 * 255 * 2));
    let a = 1;
    if (diff < sim) a = 0;
    else if (blend > 0) a = Math.min(1, (diff - sim) / blend);
    d[i + 3] = Math.round(d[i + 3] * a);
    if (!scales) continue;
    // Ореол как ffmpeg despill (mix 0.5, expand 0): превышение канала ключа над
    // средним двух других, умноженное на силу, вычитается.
    const spill = Math.max(0, greenKey ? g - (r + b) / 2 : b - (r + g) / 2);
    if (spill <= 0) continue;
    d[i] = r + spill * scales[0];
    d[i + 1] = g + spill * scales[1];
    d[i + 2] = b + spill * scales[2];
  }
  ctx.putImageData(img, 0, 0);
}

/** Фон за персонажем в момент t клипа — тем же кодом, что и в рендере. */
export function paintBackdrop(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  bg: Backdrop,
  t = 0,
  opts: BackgroundOptions = {},
): void {
  paintBackground(ctx, bg, t, w, h, opts);
}
