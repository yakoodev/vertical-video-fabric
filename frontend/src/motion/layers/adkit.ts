/**
 * Общее у слоёв рекламного пакета (пузыри, карусель, схема, стена плиток): нейтральная палитра
 * с одним акцентом, подгонка подписи по ширине, картинка «cover» в круге/скруглении, оттенки
 * акцента. Всё — чистые функции: превью и рендер рисуют одно и то же.
 */
import { fontString } from "../fonts";
import type { AdTheme, Ctx2D, ImageLike, RenderEnv } from "../types";
import { drawImageLike, imageSize, parseHex, roundRectPath } from "./common";

/** Акцент по умолчанию — холодный индиго: нейтрально и современно, меняется пропом `accent`. */
export const AD_ACCENT = "#6c5cff";
/** Гарнитура по умолчанию: Manrope — геометричный гротеск интерфейсов, с кириллицей. */
export const AD_FONT = "Manrope";

export type AdPalette = {
  /** Стекло карточки/пузыря, кромка, текст, приглушённый текст, тень. */
  glass: string;
  edge: string;
  text: string;
  muted: string;
  shade: string;
};

export const AD_PALETTES: Record<AdTheme, AdPalette> = {
  dark: { glass: "#161821e6", edge: "#ffffff24", text: "#f5f6fa", muted: "#a2a7b5", shade: "#000000" },
  light: { glass: "#ffffffef", edge: "#0b0d1418", text: "#12141b", muted: "#5d6372", shade: "#1b2036" },
};

export const adPalette = (theme: unknown): AdPalette => AD_PALETTES[theme === "light" ? "light" : "dark"];

/* ---------- цвет ---------- */

function toHex(r: number, g: number, b: number): string {
  const h = (v: number) => Math.round(Math.max(0, Math.min(255, v))).toString(16).padStart(2, "0");
  return `#${h(r)}${h(g)}${h(b)}`;
}

/** Смешать два цвета (k = 0 — `a`, 1 — `b`), результат `#rrggbb`. */
export function mixHex(a: string, b: string, k: number): string {
  const ca = parseHex(a) ?? [0, 0, 0, 1];
  const cb = parseHex(b) ?? [0, 0, 0, 1];
  return toHex(ca[0] + (cb[0] - ca[0]) * k, ca[1] + (cb[1] - ca[1]) * k, ca[2] + (cb[2] - ca[2]) * k);
}

/** Повернуть тон цвета на `deg` градусов (оттенки акцента для пузырей и плиток). */
export function hueShift(color: string, deg: number): string {
  const c = parseHex(color);
  if (!c) return color;
  const [r, g, b] = [c[0] / 255, c[1] / 255, c[2] / 255];
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const l = (max + min) / 2;
  const d = max - min;
  let h = 0;
  const s = d === 0 ? 0 : d / (1 - Math.abs(2 * l - 1));
  if (d !== 0) {
    if (max === r) h = ((g - b) / d) % 6;
    else if (max === g) h = (b - r) / d + 2;
    else h = (r - g) / d + 4;
  }
  h = (((h * 60 + deg) % 360) + 360) % 360;
  const C = (1 - Math.abs(2 * l - 1)) * s;
  const X = C * (1 - Math.abs(((h / 60) % 2) - 1));
  const m = l - C / 2;
  const [r1, g1, b1] = h < 60 ? [C, X, 0] : h < 120 ? [X, C, 0] : h < 180 ? [0, C, X] : h < 240 ? [0, X, C] : h < 300 ? [X, 0, C] : [C, 0, X];
  return toHex((r1 + m) * 255, (g1 + m) * 255, (b1 + m) * 255);
}

/** Оттенок акцента номер `i`: соседние тона ±28° по кругу, без кислотной радуги. */
export function accentTone(accent: string, i: number): string {
  const steps = [0, 28, -28, 52, -52, 14, -14, 40, -40, 66, -66, 0];
  return hueShift(accent, steps[i % steps.length]);
}

/* ---------- текст ---------- */

/**
 * Поставить шрифт так, чтобы `text` влез в `maxW` (кегль уменьшается не ниже 55 %); вернуть кегль.
 * Подпись одной строкой: у плашек пакета переносов нет — длинное слово просто мельче.
 */
export function fitFont(ctx: Ctx2D, text: string, family: string | undefined, weight: number, size: number, maxW: number, env: RenderEnv): number {
  let s = size;
  ctx.font = fontString({ family: family || AD_FONT, weight, size: s }, env.defaultFont);
  const w = ctx.measureText(text).width;
  if (w > maxW && w > 0) {
    s = Math.max(size * 0.55, (size * maxW) / w);
    ctx.font = fontString({ family: family || AD_FONT, weight, size: s }, env.defaultFont);
  }
  return s;
}

/** Подпись по центру точки (x, y — середина строки по высоте заглавных). */
export function centerText(ctx: Ctx2D, text: string, x: number, y: number, size: number, color: string | CanvasGradient): void {
  if (!text) return;
  ctx.textAlign = "center";
  ctx.textBaseline = "alphabetic";
  ctx.fillStyle = color;
  ctx.fillText(text, x, y + size * 0.36);
}

/* ---------- картинки ---------- */

/** Картинка «cover» в прямоугольнике (обрезка по центру) — без тени. */
export function drawCover(ctx: Ctx2D, img: ImageLike, x: number, y: number, w: number, h: number, env: RenderEnv): void {
  const n = imageSize(img);
  const k = Math.max(w / n.w, h / n.h);
  const sw = w / k;
  const sh = h / k;
  drawImageLike(ctx, img, (n.w - sw) / 2, (n.h - sh) / 2, sw, sh, x, y, w, h, env);
}

/** Картинка по кругу (cover). */
export function drawCircleImage(ctx: Ctx2D, img: ImageLike, cx: number, cy: number, r: number, env: RenderEnv): void {
  ctx.save();
  ctx.beginPath();
  ctx.arc(cx, cy, r, 0, Math.PI * 2);
  ctx.clip();
  drawCover(ctx, img, cx - r, cy - r, r * 2, r * 2, env);
  ctx.restore();
}

/** Картинка в скруглённом прямоугольнике (cover). */
export function drawRoundImage(ctx: Ctx2D, img: ImageLike, x: number, y: number, w: number, h: number, radius: number, env: RenderEnv): void {
  ctx.save();
  ctx.beginPath();
  roundRectPath(ctx, x, y, w, h, radius);
  ctx.clip();
  drawCover(ctx, img, x, y, w, h, env);
  ctx.restore();
}

/** Ссылка картинки из props (id файла числом или строкой, `sticker:…`) — как ключ env.getImage. */
export function imageRef(v: unknown): string | null {
  if (v === null || v === undefined || v === "") return null;
  return String(v);
}

/** Мягкое свечение кругом (радиальный градиент) — дешевле shadowBlur и одинаково в Skia и браузере. */
export function glowDisc(ctx: Ctx2D, cx: number, cy: number, r: number, color: string, alpha: number): void {
  if (alpha <= 0.002 || r <= 0) return;
  const c = parseHex(color) ?? [255, 255, 255, 1];
  const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, r);
  const at = (a: number) => `rgba(${c[0]},${c[1]},${c[2]},${Math.round(Math.max(0, Math.min(1, a)) * 1000) / 1000})`;
  g.addColorStop(0, at(alpha));
  g.addColorStop(0.45, at(alpha * 0.55));
  g.addColorStop(1, at(0));
  ctx.fillStyle = g;
  ctx.beginPath();
  ctx.arc(cx, cy, r, 0, Math.PI * 2);
  ctx.fill();
}

/** Число по-русски коротко: 9 876, 12,4 тыс., 2 млн — подписи пузырей и карточек. */
export function compactNumber(n: number): string {
  const v = Math.max(0, Math.round(n));
  const short = (x: number, unit: string) => `${(Math.floor(x * 10) / 10).toFixed(x < 100 ? 1 : 0).replace(".", ",").replace(/,0$/, "")} ${unit}`;
  if (v >= 1_000_000) return short(v / 1_000_000, "млн");
  if (v >= 100_000) return short(v / 1000, "тыс.");
  return String(v).replace(/\B(?=(\d{3})+(?!\d))/g, " ");
}
