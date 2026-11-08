/**
 * Кисть и контуры для аниме-лайнарта на Canvas2D: сглаженные пути по опорным точкам (Catmull–Rom),
 * линии переменной толщины (кисть с нажимом — сужение на концах, «вес» в середине или у конца),
 * цвета (смешивание hex) и мягкие пятна (радиальные градиенты). Без состояния — всё от аргументов.
 */

export type P = [number, number];
type G = CanvasRenderingContext2D;

export const clamp = (x: number, a = 0, b = 1) => (x < a ? a : x > b ? b : x);
export const lerp = (a: number, b: number, k: number) => a + (b - a) * k;
export const smooth = (x: number) => {
  const k = clamp(x);
  return k * k * (3 - 2 * k);
};

/** Точки Catmull–Rom через опорные (centripetal-ish: обычный uniform, сегментов seg на участок). */
export function spline(pts: P[], seg = 10, closed = false): P[] {
  const n = pts.length;
  if (n < 3) return pts.slice();
  const out: P[] = [];
  const at = (i: number): P => (closed ? pts[(i + n) % n] : pts[Math.max(0, Math.min(n - 1, i))]);
  const last = closed ? n : n - 1;
  for (let i = 0; i < last; i++) {
    const p0 = at(i - 1), p1 = at(i), p2 = at(i + 1), p3 = at(i + 2);
    for (let s = 0; s < seg; s++) {
      const t = s / seg, t2 = t * t, t3 = t2 * t;
      out.push([
        0.5 * (2 * p1[0] + (-p0[0] + p2[0]) * t + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2 + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3),
        0.5 * (2 * p1[1] + (-p0[1] + p2[1]) * t + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2 + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3),
      ]);
    }
  }
  if (!closed) out.push(pts[n - 1]);
  return out;
}

/** Путь по точкам (уже сглаженным или ломаной). */
export function poly(g: G, pts: P[], closed = true) {
  g.beginPath();
  pts.forEach((p, i) => (i ? g.lineTo(p[0], p[1]) : g.moveTo(p[0], p[1])));
  if (closed) g.closePath();
}

/** Гладкая замкнутая фигура по опорным точкам. */
export function blob(g: G, pts: P[], seg = 8) {
  poly(g, spline(pts, seg, true), true);
}

export interface BrushOpt {
  /** Толщина в самом широком месте. */
  w: number;
  /** Доля длины на сужение в начале и в конце (0 — тупой конец). */
  a?: number;
  b?: number;
  /** Минимальная толщина на концах. */
  min?: number;
  /** Где «вес» линии (0..1, по умолчанию середина) — у аниме ресниц он у внешнего угла. */
  peak?: number;
  /** Не сглаживать (точки уже плотные). */
  raw?: boolean;
}

/** Линия кистью: полигон из смещений по нормали, толщина — профиль нажима. Заливает текущим fillStyle. */
export function brush(g: G, pts: P[], o: BrushOpt) {
  const s = o.raw ? pts : spline(pts, 8, false);
  const n = s.length;
  if (n < 2) return;
  const len: number[] = [0];
  for (let i = 1; i < n; i++) len.push(len[i - 1] + Math.hypot(s[i][0] - s[i - 1][0], s[i][1] - s[i - 1][1]));
  const L = len[n - 1] || 1;
  const a = o.a ?? 0.25, b = o.b ?? 0.25, min = o.min ?? 0.15, peak = o.peak ?? 0.5;
  const width = (u: number) => {
    let k = 1;
    if (a > 0 && u < a) k = Math.pow(u / a, 0.65);
    if (b > 0 && u > 1 - b) k = Math.min(k, Math.pow((1 - u) / b, 0.65));
    // Смещение «веса»: линия толще ближе к peak.
    const bias = 1 - 0.35 * Math.abs(u - peak);
    return Math.max(min, o.w * k * bias);
  };
  const left: P[] = [], right: P[] = [];
  for (let i = 0; i < n; i++) {
    const p = s[Math.min(n - 1, i + 1)], q = s[Math.max(0, i - 1)];
    let dx = p[0] - q[0], dy = p[1] - q[1];
    const d = Math.hypot(dx, dy) || 1;
    dx /= d;
    dy /= d;
    const w = width(len[i] / L) / 2;
    left.push([s[i][0] - dy * w, s[i][1] + dx * w]);
    right.push([s[i][0] + dy * w, s[i][1] - dx * w]);
  }
  g.beginPath();
  left.forEach((p, i) => (i ? g.lineTo(p[0], p[1]) : g.moveTo(p[0], p[1])));
  for (let i = n - 1; i >= 0; i--) g.lineTo(right[i][0], right[i][1]);
  g.closePath();
  g.fill();
}

/** Линия кистью цветом color. */
export function ink(g: G, pts: P[], color: string, o: BrushOpt) {
  g.fillStyle = color;
  brush(g, pts, o);
}

/* ---------- цвет ---------- */

function hex(c: string): [number, number, number] {
  const h = c.replace("#", "");
  const v = h.length === 3 ? h.split("").map((x) => x + x).join("") : h;
  return [parseInt(v.slice(0, 2), 16), parseInt(v.slice(2, 4), 16), parseInt(v.slice(4, 6), 16)];
}

/** Смешать два hex-цвета (k = 0 — a, 1 — b). */
export function mix(a: string, b: string, k: number): string {
  const x = hex(a), y = hex(b);
  const c = x.map((v, i) => Math.round(lerp(v, y[i], clamp(k))));
  return `#${c.map((v) => v.toString(16).padStart(2, "0")).join("")}`;
}

/** hex + прозрачность → rgba(). */
export function rgba(c: string, a: number): string {
  const [r, gg, b] = hex(c);
  return `rgba(${r},${gg},${b},${clamp(a)})`;
}

/** Мягкое пятно: радиальный градиент от цвета к прозрачному (эллипс rx × ry). */
export function glow(g: G, x: number, y: number, rx: number, ry: number, color: string, alpha: number, hard = 0) {
  g.save();
  g.translate(x, y);
  g.scale(1, ry / rx);
  const gr = g.createRadialGradient(0, 0, rx * hard, 0, 0, rx);
  gr.addColorStop(0, rgba(color, alpha));
  gr.addColorStop(1, rgba(color, 0));
  g.fillStyle = gr;
  g.beginPath();
  g.arc(0, 0, rx, 0, Math.PI * 2);
  g.fill();
  g.restore();
}

/** Детерминированный генератор (mulberry32). */
export function rng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Гладкий шум 1D (сумма синусов) — мерцание монитора, дыхание, покачивание. */
export function wobble(t: number, seed = 0): number {
  return (Math.sin(t * 1.7 + seed) * 0.5 + Math.sin(t * 2.9 + seed * 1.3) * 0.3 + Math.sin(t * 5.3 + seed * 2.1) * 0.2);
}

/** [r, g, b] → hex. */
export function hexOf(c: number[]): string {
  return `#${c.map((v) => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, "0")).join("")}`;
}

/** Новый офскрин-канвас. */
export function layer(w: number, h: number): HTMLCanvasElement {
  const c = document.createElement("canvas");
  c.width = Math.max(1, Math.round(w));
  c.height = Math.max(1, Math.round(h));
  return c;
}

/** Строчка (пунктир по сплайну) — из node/anime2d/src/clothes.ts. */
export function seam(g: CanvasRenderingContext2D, pts: P[], color: string, w = 1.6) {
  g.save();
  g.setLineDash([7, 6]);
  g.strokeStyle = color;
  g.lineWidth = w;
  poly(g, spline(pts, 6), false);
  g.stroke();
  g.restore();
}
