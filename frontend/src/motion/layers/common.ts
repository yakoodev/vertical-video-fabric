/**
 * Общие кирпичики рендереров: цвета, скруглённые прямоугольники, ломаные.
 *
 * Своя реализация скругления вместо `ctx.roundRect`: его нет в старом Safari, а
 * форма плашки должна совпадать пиксель в пиксель в превью и в рендере.
 */
import type { CaptionWord, ChatMessage, Ctx2D, ImageLike, Layer, RenderEnv, ScratchCanvas, VoiceTrack } from "../types";
import type { LayerState } from "../animate";

/** Что знает кадр целиком: клиповое время, слова речи (субтитры), чат и голос (PNGtuber). */
export type FrameInfo = { t: number; words: CaptionWord[]; chat: ChatMessage[]; voice?: Record<string, VoiceTrack> };

/** Слой в своей системе координат: опорные px, якорь (центр) в (0, 0). */
export type LayerRenderer<L extends Layer = Layer> = {
  /** Размер содержимого в опорных px (для маски wipe, рамки выделения, промежуточного холста). */
  measure(ctx: Ctx2D, layer: L, st: LayerState, env: RenderEnv, frame: FrameInfo): { w: number; h: number };
  draw(ctx: Ctx2D, layer: L, st: LayerState, env: RenderEnv, frame: FrameInfo): void;
  /**
   * Рисуется в несколько проходов, которые перекрываются (обводка + заливка,
   * плашка + текст). Такой слой при полупрозрачности гасится целиком через
   * промежуточный холст, иначе на фейде просвечивают швы.
   */
  multipart?: boolean;
};

/* ---------- цвета ---------- */

type RGBA = [number, number, number, number];
const colorCache = new Map<string, RGBA | null>();

/** `#rgb`, `#rrggbb`, `#rrggbbaa` → компоненты; прочие CSS-цвета → null. */
export function parseHex(color: string): RGBA | null {
  const hit = colorCache.get(color);
  if (hit !== undefined) return hit;
  let out: RGBA | null = null;
  const m = /^#([0-9a-f]{3,8})$/i.exec(color.trim());
  if (m) {
    const h = m[1];
    if (h.length === 3 || h.length === 4) {
      const v = h.split("").map((c) => parseInt(c + c, 16));
      out = [v[0], v[1], v[2], h.length === 4 ? v[3] / 255 : 1];
    } else if (h.length === 6 || h.length === 8) {
      out = [
        parseInt(h.slice(0, 2), 16),
        parseInt(h.slice(2, 4), 16),
        parseInt(h.slice(4, 6), 16),
        h.length === 8 ? parseInt(h.slice(6, 8), 16) / 255 : 1,
      ];
    }
  }
  if (colorCache.size > 512) colorCache.clear();
  colorCache.set(color, out);
  return out;
}

/** Цвет с домноженной прозрачностью — строкой для fillStyle/strokeStyle/shadowColor. */
export function rgba(color: string | null | undefined, alpha = 1): string {
  if (!color) return "rgba(0,0,0,0)";
  const c = parseHex(color);
  if (!c) return alpha >= 1 ? color : color; // именованный цвет: прозрачность не трогаем
  const a = Math.max(0, Math.min(1, c[3] * alpha));
  return `rgba(${c[0]},${c[1]},${c[2]},${Math.round(a * 1000) / 1000})`;
}

/** Светлый ли цвет — чтобы выбрать контрастный текст поверх акцента. */
export function isLight(color: string): boolean {
  const c = parseHex(color);
  if (!c) return false;
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2] > 165;
}

/** Смешать цвет с белым (k > 0) или чёрным (k < 0) — для бликов и теней на плашках. */
export function shade(color: string, k: number): string {
  const c = parseHex(color);
  if (!c) return color;
  const t = k > 0 ? 255 : 0;
  const a = Math.abs(k);
  const mix = (v: number) => Math.round(v + (t - v) * a);
  return `rgba(${mix(c[0])},${mix(c[1])},${mix(c[2])},${c[3]})`;
}

/* ---------- контуры ---------- */

/** Скруглённый прямоугольник как подпуть (без beginPath — можно склеивать несколько). */
export function roundRectPath(ctx: Ctx2D, x: number, y: number, w: number, h: number, r: number): void {
  const rr = Math.max(0, Math.min(r, w / 2, h / 2));
  ctx.moveTo(x + rr, y);
  ctx.lineTo(x + w - rr, y);
  ctx.arcTo(x + w, y, x + w, y + rr, rr);
  ctx.lineTo(x + w, y + h - rr);
  ctx.arcTo(x + w, y + h, x + w - rr, y + h, rr);
  ctx.lineTo(x + rr, y + h);
  ctx.arcTo(x, y + h, x, y + h - rr, rr);
  ctx.lineTo(x, y + rr);
  ctx.arcTo(x, y, x + rr, y, rr);
  ctx.closePath();
}

export function fillRoundRect(ctx: Ctx2D, x: number, y: number, w: number, h: number, r: number, style: string | CanvasGradient): void {
  ctx.beginPath();
  roundRectPath(ctx, x, y, w, h, r);
  ctx.fillStyle = style;
  ctx.fill();
}

export type Pt = [number, number];

/** Длина ломаной. */
export function polylineLength(pts: Pt[], closed: boolean): number {
  let len = 0;
  for (let i = 1; i < pts.length; i++) len += Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
  if (closed && pts.length > 1) {
    const a = pts[pts.length - 1];
    len += Math.hypot(pts[0][0] - a[0], pts[0][1] - a[1]);
  }
  return len;
}

/**
 * Подпуть из начальной доли `frac` ломаной — для «рисования обводки».
 * Возвращает конечную точку и направление последнего отрезка (для наконечника стрелки).
 */
export function partialPolyline(ctx: Ctx2D, pts: Pt[], closed: boolean, frac: number): { end: Pt; angle: number } {
  const all = closed && pts.length > 1 ? [...pts, pts[0]] : pts;
  const total = polylineLength(all, false);
  let left = total * Math.max(0, Math.min(1, frac));
  ctx.moveTo(all[0][0], all[0][1]);
  let end: Pt = all[0];
  let angle = 0;
  for (let i = 1; i < all.length; i++) {
    const a = all[i - 1];
    const b = all[i];
    const seg = Math.hypot(b[0] - a[0], b[1] - a[1]);
    if (seg <= 0) continue;
    angle = Math.atan2(b[1] - a[1], b[0] - a[0]);
    if (left >= seg) {
      ctx.lineTo(b[0], b[1]);
      end = b;
      left -= seg;
      continue;
    }
    const k = left / seg;
    end = [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k];
    ctx.lineTo(end[0], end[1]);
    break;
  }
  return { end, angle };
}

/* ---------- картинки ---------- */

/* ---------- тень ---------- */

/** Погасить тень: дальше рисуются детали, которые своей тени не отбрасывают. */
export function clearShadow(ctx: Ctx2D): void {
  ctx.shadowColor = "rgba(0,0,0,0)";
  ctx.shadowBlur = 0;
  ctx.shadowOffsetX = 0;
  ctx.shadowOffsetY = 0;
}

export function hasShadow(ctx: Ctx2D): boolean {
  if (!(ctx.shadowBlur > 0 || ctx.shadowOffsetX !== 0 || ctx.shadowOffsetY !== 0)) return false;
  return !/^rgba\([^)]*,\s*0\s*\)$/.test(ctx.shadowColor) && ctx.shadowColor !== "transparent";
}

/**
 * Тень картинки — заранее размытый «спрайт» силуэта. drawImage с shadowBlur в
 * Skia стоит десятки миллисекунд на кадр, а спрайт строится один раз на
 * картинку/цвет/относительное размытие и дальше рисуется обычным drawImage.
 */
const SPRITE_BASE = 192;
const spriteCache = new WeakMap<object, Map<string, { canvas: ScratchCanvas; pad: number; w: number; h: number }>>();

function shadowSprite(env: RenderEnv, img: ImageLike, sx: number, sy: number, sw: number, sh: number, color: string, relBlur: number) {
  let byImg = spriteCache.get(img as object);
  if (!byImg) {
    byImg = new Map();
    spriteCache.set(img as object, byImg);
  }
  const key = `${color}|${relBlur}|${sx},${sy},${sw},${sh}`;
  let sp = byImg.get(key);
  if (sp) return sp;
  const k = SPRITE_BASE / Math.max(sw, sh, 1);
  const w = Math.max(1, Math.round(sw * k));
  const h = Math.max(1, Math.round(sh * k));
  const blur = relBlur * SPRITE_BASE;
  const pad = Math.ceil(blur * 1.6) + 2;
  const canvas = env.createCanvas!(w + pad * 2, h + pad * 2);
  const c = canvas.getContext("2d") as Ctx2D;
  // Сама картинка уезжает за край холста, на холст попадает только её тень.
  const off = w + pad * 2 + 16;
  c.shadowColor = color;
  c.shadowBlur = blur;
  c.shadowOffsetX = off;
  c.drawImage(img as unknown as CanvasImageSource, sx, sy, sw, sh, pad - off, pad, w, h);
  sp = { canvas, pad, w, h };
  if (byImg.size > 12) byImg.clear();
  byImg.set(key, sp);
  return sp;
}

/** Нарисовать картинку из env (drawImage принимает и браузерные, и napi-картинки). */
export function drawImageLike(
  ctx: Ctx2D,
  img: ImageLike,
  sx: number,
  sy: number,
  sw: number,
  sh: number,
  dx: number,
  dy: number,
  dw: number,
  dh: number,
  env?: RenderEnv,
): void {
  if (env?.createCanvas && hasShadow(ctx)) {
    const m = ctx.getTransform();
    const scale = Math.hypot(m.a, m.b) || 1;
    const devSize = Math.max(Math.abs(dw), Math.abs(dh)) * scale;
    // Размытие относительно размера картинки, с шагом 1% — спрайт переиспользуется на «pop».
    const rel = Math.round((ctx.shadowBlur / Math.max(devSize, 1)) * 100) / 100;
    const sp = shadowSprite(env, img, sx, sy, sw, sh, ctx.shadowColor, rel);
    // Смещение тени задано в пикселях экрана — переводим в локальные координаты слоя.
    const det = m.a * m.d - m.b * m.c || 1;
    const ox = (m.d * ctx.shadowOffsetX - m.c * ctx.shadowOffsetY) / det;
    const oy = (-m.b * ctx.shadowOffsetX + m.a * ctx.shadowOffsetY) / det;
    const kx = dw / sp.w;
    const ky = dh / sp.h;
    ctx.save();
    clearShadow(ctx);
    ctx.drawImage(sp.canvas as unknown as CanvasImageSource, dx - sp.pad * kx + ox, dy - sp.pad * ky + oy, (sp.w + sp.pad * 2) * kx, (sp.h + sp.pad * 2) * ky);
    ctx.drawImage(img as unknown as CanvasImageSource, sx, sy, sw, sh, dx, dy, dw, dh);
    ctx.restore();
    return;
  }
  ctx.drawImage(img as unknown as CanvasImageSource, sx, sy, sw, sh, dx, dy, dw, dh);
}

export function imageSize(img: ImageLike): { w: number; h: number } {
  const w = img.width || 0;
  const h = img.height || 0;
  // SVG без width/height в браузере может отдать 0 — считаем квадратом.
  if (w <= 0 || h <= 0) return { w: 512, h: 512 };
  return { w, h };
}
