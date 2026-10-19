/**
 * Кривые сглаживания из спеки. Все принимают p ∈ [0, 1] и возвращают 0 в начале
 * и 1 в конце (Back/Elastic по пути выходят за пределы — это и есть «пружинка»).
 * Кроме именованных есть CSS-кривая `bezier(x1,y1,x2,y2)` — как cubic-bezier().
 */
import type { Easing, NamedEasing } from "./types";

export const DEFAULT_EASE: NamedEasing = "inOutCubic";

const BACK = 1.70158;
const BACK_IO = BACK * 1.525;

function outBounce(p: number): number {
  const n = 7.5625;
  const d = 2.75;
  if (p < 1 / d) return n * p * p;
  if (p < 2 / d) return n * (p -= 1.5 / d) * p + 0.75;
  if (p < 2.5 / d) return n * (p -= 2.25 / d) * p + 0.9375;
  return n * (p -= 2.625 / d) * p + 0.984375;
}

export const EASINGS: Record<NamedEasing, (p: number) => number> = {
  linear: (p) => p,
  inQuad: (p) => p * p,
  outQuad: (p) => 1 - (1 - p) * (1 - p),
  inOutQuad: (p) => (p < 0.5 ? 2 * p * p : 1 - (-2 * p + 2) ** 2 / 2),
  inCubic: (p) => p * p * p,
  outCubic: (p) => 1 - (1 - p) ** 3,
  inOutCubic: (p) => (p < 0.5 ? 4 * p * p * p : 1 - (-2 * p + 2) ** 3 / 2),
  inBack: (p) => (BACK + 1) * p * p * p - BACK * p * p,
  outBack: (p) => 1 + (BACK + 1) * (p - 1) ** 3 + BACK * (p - 1) ** 2,
  inOutBack: (p) =>
    p < 0.5
      ? ((2 * p) ** 2 * ((BACK_IO + 1) * 2 * p - BACK_IO)) / 2
      : ((2 * p - 2) ** 2 * ((BACK_IO + 1) * (p * 2 - 2) + BACK_IO) + 2) / 2,
  outElastic: (p) =>
    p <= 0 ? 0 : p >= 1 ? 1 : 2 ** (-10 * p) * Math.sin((p * 10 - 0.75) * ((2 * Math.PI) / 3)) + 1,
  outBounce,
  // Ступенька: держим значение точки до самого следующего ключа.
  step: (p) => (p >= 1 ? 1 : 0),
};

export const EASING_NAMES = Object.keys(EASINGS) as NamedEasing[];

/* ---------- cubic-bezier ---------- */

/**
 * Кривая CSS cubic-bezier(x1, y1, x2, y2): по времени p ищем параметр s, при
 * котором x(s) = p (Ньютон, при плохой производной — деление пополам), и
 * возвращаем y(s). x1, x2 зажаты в [0, 1], иначе x(s) не монотонна.
 */
export function cubicBezier(x1: number, y1: number, x2: number, y2: number): (p: number) => number {
  const ax = Math.min(1, Math.max(0, x1));
  const bx = Math.min(1, Math.max(0, x2));
  const cx = 3 * ax;
  const bxx = 3 * (bx - ax) - cx;
  const axx = 1 - cx - bxx;
  const cy = 3 * y1;
  const byy = 3 * (y2 - y1) - cy;
  const ayy = 1 - cy - byy;
  const sampleX = (s: number) => ((axx * s + bxx) * s + cx) * s;
  const sampleY = (s: number) => ((ayy * s + byy) * s + cy) * s;
  const slopeX = (s: number) => (3 * axx * s + 2 * bxx) * s + cx;
  return (p: number) => {
    if (p <= 0) return 0;
    if (p >= 1) return 1;
    let s = p;
    for (let i = 0; i < 8; i++) {
      const err = sampleX(s) - p;
      if (Math.abs(err) < 1e-6) return sampleY(s);
      const d = slopeX(s);
      if (Math.abs(d) < 1e-6) break;
      s -= err / d;
    }
    let lo = 0;
    let hi = 1;
    s = p;
    for (let i = 0; i < 40; i++) {
      const x = sampleX(s);
      if (Math.abs(x - p) < 1e-6) break;
      if (x < p) lo = s;
      else hi = s;
      s = (lo + hi) / 2;
    }
    return sampleY(s);
  };
}

const BEZIER = /^bezier\(\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*\)$/;
const bezierCache = new Map<string, ((p: number) => number) | null>();

/** Разобрать "bezier(x1,y1,x2,y2)"; не разобралось — null. */
export function parseBezier(name: string): ((p: number) => number) | null {
  let fn = bezierCache.get(name);
  if (fn !== undefined) return fn;
  const m = BEZIER.exec(name.trim());
  const v = m ? m.slice(1).map(Number) : null;
  fn = v && v.every(Number.isFinite) ? cubicBezier(v[0], v[1], v[2], v[3]) : null;
  if (bezierCache.size > 256) bezierCache.clear();
  bezierCache.set(name, fn);
  return fn;
}

/** Функция кривой по имени; неизвестное имя — кривая по умолчанию. */
export function easingFn(name: Easing | string | undefined): (p: number) => number {
  if (!name) return EASINGS[DEFAULT_EASE];
  const named = EASINGS[name as NamedEasing];
  if (named) return named;
  if (name.startsWith("bezier(")) return parseBezier(name) ?? EASINGS[DEFAULT_EASE];
  return EASINGS[DEFAULT_EASE];
}

/** Применить ease по имени к p (p зажимается в [0, 1]). */
export function ease(name: Easing | string | undefined, p: number): number {
  const q = p <= 0 ? 0 : p >= 1 ? 1 : p;
  return easingFn(name)(q);
}

export const clamp = (v: number, lo: number, hi: number) => (v < lo ? lo : v > hi ? hi : v);
export const clamp01 = (v: number) => (v < 0 ? 0 : v > 1 ? 1 : v);
export const lerp = (a: number, b: number, p: number) => a + (b - a) * p;
