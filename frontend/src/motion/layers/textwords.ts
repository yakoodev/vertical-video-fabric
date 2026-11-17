/**
 * Входы слов текстового слоя (in/out text, animate.ts): `focus` — из размытия в резкость, `colorize` — из
 * серого в цвет, `slot` — одно слово прокручивается барабаном слот-машины, `letters` — буквы падают по одной.
 * Раскладка — та же (text.ts): эти помощники только решают, как нарисовать уже разложенное слово.
 * Всё «случайное» — от id слоя и номера шага: превью и рендер совпадают кадр в кадр.
 */
import { clamp01, ease } from "../easing";
import { graphemes } from "../fonts";
import type { Ctx2D, Highlight } from "../types";
import { parseHex } from "./common";
import { measure, scrambleChar, type LaidWord, type TextLayout } from "./text";

/** Полоса строки по вертикали (система слоя): окно барабана. */
export type Reel = { pos: number; speed: number; band: { top: number; bottom: number } };

/** Сколько шагов барабана прокручивает слово слот-машины за вход. */
export const SLOT_TURNS = 6;
/** Доля входа, за которую проявляются остальные слова (слот-машина): барабан крутится уже при них. */
export const SLOT_OTHERS = 0.22;

/** Слово слот-машины: первое подсвеченное (highlight.words), иначе последнее. */
export function slotWordIndex(L: TextLayout, hl?: Highlight | null): number {
  const n = Math.max(1, L.totalWords);
  const first = hl?.words?.find((i) => Number.isInteger(i) && i >= 0 && i < n);
  return first ?? n - 1;
}

/**
 * Позиция барабана слот-машины при доле входа `fraction`: SLOT_TURNS → 0 с перелётом outBack
 * (слово проскакивает чуть ниже и встаёт на место), и скорость в шагах на долю входа (смаз).
 */
export function slotReel(fraction: number): { pos: number; speed: number } {
  const p = clamp01(fraction);
  const pos = SLOT_TURNS * (1 - ease("outBack", p));
  const h = 0.01;
  const speed = (SLOT_TURNS * Math.abs(ease("outBack", Math.min(1, p + h)) - ease("outBack", Math.max(0, p - h)))) / (2 * h);
  return { pos, speed };
}

/** По буквам (letters): доля падения графемы `gi` при доле входа `fraction` (соседние перекрываются). */
export function letterProgress(L: TextLayout, fraction: number, gi: number): number {
  const n = Math.max(1, L.totalG);
  const span = Math.max(1.5, Math.min(4, n * 0.35));
  return clamp01((clamp01(fraction) * (n + span) - gi) / span);
}

/** Своя прозрачность и масштаб графемы (letters) вокруг её центра (x, y); вызывать внутри save/restore. */
export function glyphTransform(ctx: Ctx2D, v: { alpha?: number; scale?: number }, x: number, y: number): void {
  if (v.alpha !== undefined && v.alpha < 1) ctx.globalAlpha *= v.alpha;
  if (v.scale !== undefined && v.scale !== 1 && v.scale > 0) {
    ctx.translate(x, y);
    ctx.scale(v.scale, v.scale);
    ctx.translate(-x, -y);
  }
}

/* ---------- входы слов: размытие, серое → цвет, барабан ---------- */

/** Серый цвета: белое → #8c8c8c, чёрное → тёмно-серое (цвет «ещё не включился»). */
function grayOf(c: [number, number, number, number]): [number, number, number] {
  const l = (0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]) / 255;
  const v = Math.round(255 * (0.15 + 0.4 * l));
  return [v, v, v];
}

/** Цвет `color`, смешанный со своим серым на долю `g` (colorize); не hex — как есть. */
export function grayMix(color: string, g: number): string {
  const m = /^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)$/i.exec(color.trim());
  const c: [number, number, number, number] | null = m
    ? [Number(m[1]), Number(m[2]), Number(m[3]), m[4] === undefined ? 1 : Number(m[4])]
    : parseHex(color);
  if (!c || g <= 0) return color;
  const k = clamp01(g);
  const gr = grayOf(c);
  const mix = (a: number, b: number) => Math.round(a + (b - a) * k);
  return `rgba(${mix(c[0], gr[0])},${mix(c[1], gr[1])},${mix(c[2], gr[2])},${c[3]})`;
}

/** Далеко за кадр (px устройства): там рисуется само слово, а его тень-размытие ложится на место. */
const BLUR_FAR = 20000;

/**
 * Нарисовать `fn` размытым на `blur` опорн. px — тенью (shadowBlur): одинаково в браузере и в Skia,
 * без ctx.filter. Само слово уезжает за кадр на BLUR_FAR px устройства, тень цвета `color`
 * возвращается ровно на место (сдвиг тени — в px устройства; поворот и масштаб слоя учтены).
 */
export function drawBlurred(ctx: Ctx2D, blur: number, color: string, fn: () => void): void {
  const m = ctx.getTransform();
  const ds = Math.hypot(m.a, m.b) || 1;
  const det = m.a * m.d - m.b * m.c;
  if (!(Math.abs(det) > 1e-9)) return;
  ctx.save();
  // Сдвиг на (−BLUR_FAR, 0) px устройства — обратной линейной частью текущей матрицы.
  ctx.translate((m.d * -BLUR_FAR) / det, (m.b * BLUR_FAR) / det);
  ctx.shadowColor = color;
  ctx.shadowBlur = Math.max(0.5, blur * ds * 2);
  ctx.shadowOffsetX = BLUR_FAR;
  ctx.shadowOffsetY = 0;
  fn();
  ctx.restore();
}

/** «Символ» барабана слот-машины: случайные буквы того же регистра на месте букв слова (шаг j). */
function reelText(text: string, seed: number, j: number): string {
  let gi = 0;
  return graphemes(text)
    .map((g) => (/[\p{L}\p{N}]/u.test(g) ? scrambleChar(g, seed + 7, j, gi++) : g))
    .join("");
}

/**
 * Барабан слот-машины: слово и случайные «символы» над ним едут вниз под окном своей строки
 * (маска — полоса строки); на скорости — смаз двумя бледными копиями по ходу. j = 0 — само
 * слово (pos шагов над местом), j > 0 — символы, проехавшие раньше (ниже). Символы — по центру слова.
 */
export function drawReel(
  ctx: Ctx2D,
  w: LaidWord,
  x: number,
  baseline: number,
  L: TextLayout,
  pass: "stroke" | "fill",
  reel: Reel,
  seed: number,
  drawWord: () => void,
): void {
  const step = reel.band.bottom - reel.band.top;
  const text = w.segs.map((s) => s.text).join("");
  const smear = clamp01(reel.speed / (SLOT_TURNS * 2.5));
  const alpha0 = ctx.globalAlpha;
  const item = (j: number, dy: number, a: number) => {
    if (a <= 0.002) return;
    ctx.save();
    ctx.globalAlpha = alpha0 * Math.min(1, a);
    ctx.translate(0, dy);
    if (j === 0) drawWord();
    else {
      const t = reelText(text, seed, j);
      const off = (w.w - measure(ctx, L.font, L.letterSpacing, t)) / 2;
      if (pass === "stroke") ctx.strokeText(t, x + off, baseline);
      else ctx.fillText(t, x + off, baseline);
    }
    ctx.restore();
  };
  const last = Math.ceil(reel.pos) + 1;
  for (let j = 0; j <= last; j++) {
    const off = j - reel.pos;
    if (Math.abs(off) > 1.05) continue;
    // Край окна мягкий: чем дальше от середины, тем прозрачнее.
    const a = Math.max(0, 1 - Math.abs(off) * 0.9);
    if (smear > 0.05) {
      for (const k of [-1, 1]) item(j, (off + k * 0.14 * smear) * step, a * 0.3 * smear);
      item(j, off * step, a * (1 - 0.35 * smear));
    } else item(j, off * step, a);
  }
}
