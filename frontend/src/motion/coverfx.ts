/**
 * Фото-фильтры слоя-картинки документа обложки. Работают по пикселям (getImageData), поэтому холст редактора
 * (браузер) и экспорт сервера (@napi-rs/canvas) дают одно и то же. Поля и границы — app/cover_doc_fx.py.
 *
 * Порядок: насыщенность и «ЧБ с акцентом» → яркость и контраст → размытие → резкость → затемнение краёв и
 * виньетка. Картинка фильтруется уже в размере рамки (px документа), поэтому радиусы — в px документа.
 */

export type CoverAccentRange = { from: number; to: number; minSat: number };
export type CoverShade = { side: "left" | "right" | "top" | "bottom"; size: number; strength: number; curve: number; hold: number };
export type CoverFilters = {
  /** −1…1: сдвиг яркости (×128). */
  brightness: number;
  /** −1…1: контраст от средней яркости кадра (как PIL ImageEnhance.Contrast(1 + contrast)). */
  contrast: number;
  /** −1…1: насыщенность всего кадра. */
  saturation: number;
  /** ЧБ с акцентом: цвета в диапазонах оттенков остаются, у остального насыщенность × rest. */
  accent: { ranges: CoverAccentRange[]; rest: number } | null;
  /** Размытие, σ в px документа. */
  blur: number;
  /** Резкость: сила нерезкой маски (σ 2 px, порог 2). */
  sharpen: number;
  /** 0…1: затемнение по краям кадра. */
  vignette: number;
  shade: CoverShade[];
  shadeColor: string;
} | null;

type Placement = { sx: number; sy: number; sw: number; sh: number; dw: number; dh: number };
type MakeCanvas = (w: number, h: number) => { getContext(type: "2d"): unknown };
type Px = Uint8ClampedArray;

const FEATHER_DEG = 6;
const MAX_SIDE = 4096;

/* ---------- цвет ---------- */

function hueDistance(h: number, from: number, to: number): number {
  // Диапазон по кругу: from > to — через 0° (красный 344…8).
  const inside = from <= to ? h >= from && h <= to : h >= from || h <= to;
  if (inside) return 0;
  const d = (a: number, b: number) => Math.min(Math.abs(a - b), 360 - Math.abs(a - b));
  return Math.min(d(h, from), d(h, to));
}

function smooth(edge0: number, edge1: number, x: number): number {
  const t = Math.max(0, Math.min(1, (x - edge0) / (edge1 - edge0)));
  return t * t * (3 - 2 * t);
}

/** Насколько пиксель — «цвет акцента» (0…1). */
export function accentKeep(h: number, s: number, ranges: CoverAccentRange[]): number {
  let keep = 0;
  for (const r of ranges) {
    const byHue = Math.max(0, 1 - hueDistance(h, r.from, r.to) / FEATHER_DEG);
    const bySat = r.minSat > 0 ? smooth(r.minSat - 0.06, r.minSat + 0.06, s) : 1;
    keep = Math.max(keep, byHue * bySat);
  }
  return keep;
}

function saturate(px: Px, f: NonNullable<CoverFilters>): void {
  const base = 1 + f.saturation;
  const accent = f.accent;
  for (let i = 0; i < px.length; i += 4) {
    const r = px[i], g = px[i + 1], b = px[i + 2];
    const max = Math.max(r, g, b);
    const min = Math.min(r, g, b);
    if (max === 0 || max === min) continue;
    const s = (max - min) / max;
    let factor = base;
    if (accent) {
      let h: number;
      const d = max - min;
      if (max === r) h = ((g - b) / d + 6) % 6;
      else if (max === g) h = (b - r) / d + 2;
      else h = (r - g) / d + 4;
      const keep = accentKeep(h * 60, s, accent.ranges);
      factor *= accent.rest + (1 - accent.rest) * keep;
    }
    // HSV: тон и яркость (max) на месте, насыщенность × factor.
    const ratio = Math.min(1 / s, factor);
    px[i] = max - (max - r) * ratio;
    px[i + 1] = max - (max - g) * ratio;
    px[i + 2] = max - (max - b) * ratio;
  }
}

function levels(px: Px, f: NonNullable<CoverFilters>): void {
  let mean = 128;
  if (f.contrast) {
    let sum = 0;
    for (let i = 0; i < px.length; i += 4) sum += 0.299 * px[i] + 0.587 * px[i + 1] + 0.114 * px[i + 2];
    mean = Math.round(sum / Math.max(1, px.length / 4));
  }
  const k = 1 + f.contrast;
  const shift = f.brightness * 128;
  for (let i = 0; i < px.length; i += 4) {
    px[i] = mean + (px[i] - mean) * k + shift;
    px[i + 1] = mean + (px[i + 1] - mean) * k + shift;
    px[i + 2] = mean + (px[i + 2] - mean) * k + shift;
  }
}

/* ---------- размытие: три прохода прямоугольником ≈ гаусс ---------- */

function boxSizes(sigma: number): number[] {
  const n = 3;
  const ideal = Math.sqrt((12 * sigma * sigma) / n + 1);
  let wl = Math.floor(ideal);
  if (wl % 2 === 0) wl -= 1;
  const wu = wl + 2;
  const m = Math.round((12 * sigma * sigma - n * wl * wl - 4 * n * wl - 3 * n) / (-4 * wl - 4));
  return [0, 1, 2].map((i) => (i < m ? wl : wu));
}

function boxPass(src: Float32Array, dst: Float32Array, w: number, h: number, r: number, horizontal: boolean): void {
  const len = horizontal ? w : h;
  const lines = horizontal ? h : w;
  const step = horizontal ? 1 : w;
  const scale = 1 / (2 * r + 1);
  for (let line = 0; line < lines; line++) {
    const start = horizontal ? line * w : line;
    const first = src[start];
    const last = src[start + (len - 1) * step];
    let acc = (r + 1) * first;
    for (let j = 0; j < r; j++) acc += src[start + Math.min(j, len - 1) * step];
    for (let j = 0; j < len; j++) {
      const add = j + r < len ? src[start + (j + r) * step] : last;
      const sub = j - r - 1 >= 0 ? src[start + (j - r - 1) * step] : first;
      acc += add - sub;
      dst[start + j * step] = acc * scale;
    }
  }
}

function blurPlane(plane: Float32Array, w: number, h: number, sigma: number): Float32Array {
  const a = Float32Array.from(plane);
  const tmp = new Float32Array(plane.length);
  for (const size of boxSizes(sigma)) {
    const r = (size - 1) / 2;
    if (r < 1) continue;
    boxPass(a, tmp, w, h, r, true);
    boxPass(tmp, a, w, h, r, false);
  }
  return a;
}

function planes(px: Px, w: number, h: number): Float32Array[] {
  const out = [new Float32Array(w * h), new Float32Array(w * h), new Float32Array(w * h)];
  for (let i = 0, p = 0; p < w * h; i += 4, p++) {
    out[0][p] = px[i];
    out[1][p] = px[i + 1];
    out[2][p] = px[i + 2];
  }
  return out;
}

function blur(px: Px, w: number, h: number, sigma: number): void {
  const blurred = planes(px, w, h).map((pl) => blurPlane(pl, w, h, sigma));
  for (let i = 0, p = 0; p < w * h; i += 4, p++) {
    px[i] = blurred[0][p];
    px[i + 1] = blurred[1][p];
    px[i + 2] = blurred[2][p];
  }
}

function sharpen(px: Px, w: number, h: number, amount: number): void {
  const blurred = planes(px, w, h).map((pl) => blurPlane(pl, w, h, 2));
  for (let i = 0, p = 0; p < w * h; i += 4, p++) {
    for (let c = 0; c < 3; c++) {
      const diff = px[i + c] - blurred[c][p];
      if (Math.abs(diff) >= 2) px[i + c] = px[i + c] + diff * amount;
    }
  }
}

/* ---------- затемнение краёв ---------- */

function parseColor(hex: string): [number, number, number] {
  const m = /^#?([0-9a-f]{6})/i.exec(hex || "");
  const v = m ? parseInt(m[1], 16) : 0;
  return [(v >> 16) & 255, (v >> 8) & 255, v & 255];
}

/** Сила затемнения (0…1) в точке рамки: максимум по полосам и виньетке. */
export function shadeAlpha(f: NonNullable<CoverFilters>, x: number, y: number, w: number, h: number): number {
  let a = 0;
  for (const s of f.shade) {
    const d = s.side === "left" ? x / w : s.side === "right" ? (w - 1 - x) / w : s.side === "top" ? y / h : (h - 1 - y) / h;
    if (d >= s.size) continue;
    const solid = s.size * s.hold;
    const v = d < solid ? s.strength : s.strength * Math.pow(1 - (d - solid) / Math.max(1e-6, s.size - solid), s.curve);
    if (v > a) a = v;
  }
  if (f.vignette > 0) {
    const dx = Math.min(x, w - 1 - x) / (0.14 * w);
    const dy = Math.min(y, h - 1 - y) / (0.167 * h);
    const t = Math.min(1, Math.max(0, Math.min(dx, dy)));
    const v = f.vignette * Math.pow(1 - t, 1.2);
    if (v > a) a = v;
  }
  return a;
}

function shadePixels(px: Px, w: number, h: number, f: NonNullable<CoverFilters>): void {
  if (!f.shade.length && !(f.vignette > 0)) return;
  const [cr, cg, cb] = parseColor(f.shadeColor);
  for (let y = 0, i = 0; y < h; y++) {
    for (let x = 0; x < w; x++, i += 4) {
      const a = shadeAlpha(f, x, y, w, h);
      if (a <= 0) continue;
      px[i] = px[i] * (1 - a) + cr * a;
      px[i + 1] = px[i + 1] * (1 - a) + cg * a;
      px[i + 2] = px[i + 2] * (1 - a) + cb * a;
    }
  }
}

/** Все фильтры по пикселям RGBA (на месте). */
export function applyFilters(px: Px, w: number, h: number, f: CoverFilters): void {
  if (!f) return;
  if (f.saturation || f.accent) saturate(px, f);
  if (f.brightness || f.contrast) levels(px, f);
  if (f.blur > 0.3) blur(px, w, h, f.blur);
  if (f.sharpen > 0) sharpen(px, w, h, f.sharpen);
  shadePixels(px, w, h, f);
}

/** Без пиксельного холста (миниатюры шаблонов): хотя бы затемнение краёв градиентами. */
export function paintShades(ctx: CanvasRenderingContext2D, f: CoverFilters, x: number, y: number, w: number, h: number): void {
  if (!f || !f.shade.length) return;
  const [r, g, b] = parseColor(f.shadeColor);
  for (const s of f.shade) {
    const horizontal = s.side === "left" || s.side === "right";
    const span = (horizontal ? w : h) * s.size;
    const x0 = s.side === "right" ? x + w : x;
    const y0 = s.side === "bottom" ? y + h : y;
    const dir = s.side === "right" || s.side === "bottom" ? -1 : 1;
    const grad = horizontal ? ctx.createLinearGradient(x0, 0, x0 + dir * span, 0) : ctx.createLinearGradient(0, y0, 0, y0 + dir * span);
    for (let i = 0; i <= 8; i++) {
      const d = i / 8;
      const solid = s.hold;
      const a = d < solid ? s.strength : s.strength * Math.pow(1 - (d - solid) / Math.max(1e-6, 1 - solid), s.curve);
      grad.addColorStop(d, `rgba(${r},${g},${b},${a.toFixed(3)})`);
    }
    ctx.fillStyle = grad;
    if (horizontal) ctx.fillRect(s.side === "right" ? x + w - span : x, y, span, h);
    else ctx.fillRect(x, s.side === "bottom" ? y + h - span : y, w, span);
  }
}

/* ---------- кэш: тот же кадр с теми же фильтрами не пересчитывается на каждом движении мыши ---------- */

const cache = new WeakMap<object, { key: string; canvas: unknown }>();

export function filteredImage(img: object, f: CoverFilters, p: Placement, makeCanvas?: MakeCanvas): unknown | null {
  if (!f || !makeCanvas) return null;
  const scale = Math.min(1, MAX_SIDE / Math.max(p.dw, p.dh));
  const w = Math.max(1, Math.round(p.dw * scale));
  const h = Math.max(1, Math.round(p.dh * scale));
  const key = [w, h, p.sx, p.sy, p.sw, p.sh].map((v) => v.toFixed(2)).join("|") + JSON.stringify(f);
  const hit = cache.get(img);
  if (hit && hit.key === key) return hit.canvas;
  const canvas = makeCanvas(w, h);
  const ctx = canvas.getContext("2d") as CanvasRenderingContext2D | null;
  if (!ctx || typeof ctx.getImageData !== "function") return null;
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(img as CanvasImageSource, p.sx, p.sy, p.sw, p.sh, 0, 0, w, h);
  const data = ctx.getImageData(0, 0, w, h);
  applyFilters(data.data, w, h, f);
  ctx.putImageData(data, 0, 0);
  cache.set(img, { key, canvas });
  return canvas;
}
