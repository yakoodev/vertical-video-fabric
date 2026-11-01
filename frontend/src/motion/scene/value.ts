/**
 * Анимируемые значения сцены. Любое числовое поле (и каждая компонента вектора) — это:
 *
 * - число: `30`;
 * - выражение: `"=200*sin(t*2)"` (expr.ts) или `{ "expr": "…" }`;
 * - ключи: `{ "keys": [[0, 0], [1, 360, "outBack"]] }` (или `[{t, v, ease}]`), `loop` — период
 *   (с) или `pingpong`; `v` у вектора — массив;
 * - переход: `{ "from": 0, "to": 1, "at": 0.2, "duration": 0.6, "ease": "outBack" }`, вместо ease —
 *   `"spring": { "stiffness": 170, "damping": 14 }` (затухающая пружина, может перелететь);
 * - добавки поверх основы (или поверх `base`): `rate` (единиц в секунду), `wiggle` (сидовый шум:
 *   число-амплитуда или {amp, freq, seed}), `osc` (синус: {amp, freq, phase}).
 *
 * Время — секунды узла (после stagger/offset). Всё детерминировано: кадр = f(t).
 */
import { EASINGS, easingFn } from "../easing";
import { hashString, noise1 } from "../keys";
import { parseHex } from "../layers/common";
import { compileExpr, type ExprVars } from "./expr";

export type Anim = number | string | AnimObj | (number | string | AnimObj)[];
export type AnimObj = {
  expr?: string;
  keys?: KeyLike[];
  loop?: number | "pingpong" | boolean;
  from?: number | number[] | string;
  to?: number | number[] | string;
  at?: number;
  duration?: number;
  ease?: string;
  spring?: { stiffness?: number; damping?: number; mass?: number } | boolean;
  base?: number | number[];
  value?: number | number[];
  rate?: number | number[];
  wiggle?: number | number[] | { amp?: number | number[]; freq?: number; seed?: number | string; octaves?: number };
  osc?: number | { amp?: number | number[]; freq?: number; phase?: number };
  delay?: number;
};
export type KeyLike = [number, unknown, string?] | { t: number; v: unknown; ease?: string };

/** Контекст вычисления: время узла и переменные выражений. */
export type EvalCtx = ExprVars;

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const fin = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/* ---------- ключи ---------- */

type Key = { t: number; v: unknown; ease?: string };

function keyList(raw: unknown): Key[] {
  if (!Array.isArray(raw)) return [];
  const out: Key[] = [];
  for (const k of raw) {
    if (Array.isArray(k) && fin(k[0])) out.push({ t: k[0], v: k[1], ease: typeof k[2] === "string" ? k[2] : undefined });
    else if (isObj(k) && fin(k.t)) out.push({ t: k.t, v: k.v, ease: typeof k.ease === "string" ? k.ease : undefined });
  }
  out.sort((a, b) => a.t - b.t);
  return out;
}

const keyCache = new WeakMap<object, Key[]>();
function keysOf(obj: AnimObj): Key[] {
  let k = keyCache.get(obj);
  if (!k) {
    k = keyList(obj.keys);
    keyCache.set(obj, k);
  }
  return k;
}

function loopTime(t: number, keys: Key[], loop: AnimObj["loop"]): number {
  if (!loop || !keys.length) return t;
  const t0 = keys[0].t;
  const span = keys[keys.length - 1].t - t0;
  if (loop === "pingpong") {
    if (span <= 0) return t;
    const q = (((t - t0) % (2 * span)) + 2 * span) % (2 * span);
    return t0 + (q > span ? 2 * span - q : q);
  }
  const period = fin(loop) && loop > 0 ? loop : span;
  if (period <= 0) return t;
  return t0 + ((((t - t0) % period) + period) % period);
}

/** Сегмент ключей: индекс левой точки и доля пути с ease. */
function keySegment(keys: Key[], t: number): { a: Key; b: Key; p: number } {
  if (t <= keys[0].t) return { a: keys[0], b: keys[0], p: 0 };
  const last = keys[keys.length - 1];
  if (t >= last.t) return { a: last, b: last, p: 0 };
  for (let i = 0; i < keys.length - 1; i++) {
    const a = keys[i];
    const b = keys[i + 1];
    if (t >= a.t && t < b.t) {
      const span = b.t - a.t;
      const raw = span > 0 ? (t - a.t) / span : 1;
      return { a, b, p: easingFn(a.ease)(raw) };
    }
  }
  return { a: last, b: last, p: 0 };
}

/* ---------- пружина ---------- */

/** Ход затухающей пружины 0 → 1 через τ с после старта (аналитически, без интегрирования). */
export function springProgress(tau: number, stiffness = 170, damping = 14, mass = 1): number {
  if (tau <= 0) return 0;
  const k = Math.max(1, stiffness);
  const m = Math.max(0.01, mass);
  const c = Math.max(0, damping);
  const w0 = Math.sqrt(k / m);
  const zeta = c / (2 * Math.sqrt(k * m));
  if (zeta < 1) {
    const wd = w0 * Math.sqrt(1 - zeta * zeta);
    return 1 - Math.exp(-zeta * w0 * tau) * (Math.cos(wd * tau) + ((zeta * w0) / wd) * Math.sin(wd * tau));
  }
  if (zeta === 1) return 1 - Math.exp(-w0 * tau) * (1 + w0 * tau);
  const s = w0 * Math.sqrt(zeta * zeta - 1);
  const r1 = -zeta * w0 + s;
  const r2 = -zeta * w0 - s;
  return 1 - (r2 * Math.exp(r1 * tau) - r1 * Math.exp(r2 * tau)) / (r2 - r1);
}

/** Доля перехода from→to в момент t: ease по duration или пружина. */
function tweenProgress(o: AnimObj, t: number): number {
  const at = fin(o.at) ? o.at : 0;
  if (o.spring) {
    const s = isObj(o.spring) ? o.spring : {};
    return springProgress(t - at, fin(s.stiffness) ? s.stiffness : 170, fin(s.damping) ? s.damping : 14, fin(s.mass) ? s.mass : 1);
  }
  const d = fin(o.duration) && o.duration > 0 ? o.duration : 1;
  const raw = (t - at) / d;
  return easingFn(o.ease)(raw <= 0 ? 0 : raw >= 1 ? 1 : raw);
}

/* ---------- добавки ---------- */

function seedOf(raw: unknown, ctx: EvalCtx, comp: number): number {
  const s = typeof raw === "string" ? hashString(raw) : fin(raw) ? Math.floor(raw) : 0;
  // Копии repeat шумят каждая по-своему; компоненты вектора — независимо.
  return (s * 2654435761 + ctx.seed * 40503 + ctx.i * 7919 + comp * 104729) >>> 0;
}

function pick(v: unknown, comp: number): number {
  if (Array.isArray(v)) return fin(v[comp]) ? v[comp] : fin(v[0]) ? (v[0] as number) : 0;
  return fin(v) ? v : 0;
}

function additive(o: AnimObj, t: number, ctx: EvalCtx, comp: number): number {
  let out = 0;
  if (o.rate !== undefined) out += pick(o.rate, comp) * t;
  if (o.wiggle !== undefined) {
    const w = o.wiggle;
    const spec = isObj(w) ? w : { amp: w as number | number[] };
    const amp = pick(spec.amp ?? 10, comp);
    if (amp) {
      const freq = fin(spec.freq) ? spec.freq : 1.5;
      const oct = Math.max(1, Math.min(4, fin(spec.octaves) ? Math.round(spec.octaves) : 2));
      const seed = seedOf(spec.seed, ctx, comp);
      let sum = 0;
      let norm = 0;
      let a = 1;
      let f = freq;
      for (let k = 0; k < oct; k++) {
        sum += a * noise1(seed + k * 31, t * f);
        norm += a;
        a *= 0.5;
        f *= 2;
      }
      out += (amp * sum) / norm;
    }
  }
  if (o.osc !== undefined) {
    const spec = isObj(o.osc) ? o.osc : { amp: o.osc as number };
    const amp = pick(spec.amp ?? 10, comp);
    const freq = fin(spec.freq) ? spec.freq : 1;
    const phase = fin(spec.phase) ? spec.phase : 0;
    out += amp * Math.sin(2 * Math.PI * (freq * t + phase));
  }
  return out;
}

/* ---------- числа ---------- */

function exprValue(src: string, ctx: EvalCtx, t: number, fallback: number): number {
  try {
    return compileExpr(src)(t === ctx.t ? ctx : { ...ctx, t });
  } catch {
    return fallback;
  }
}

function scalar(v: unknown, comp: number, fallback: number, ctx: EvalCtx, t: number): number {
  if (fin(v)) return v;
  if (Array.isArray(v)) return scalar(v[comp] ?? v[0], 0, fallback, ctx, t);
  if (typeof v === "string") {
    if (v.startsWith("=")) return exprValue(v.slice(1), ctx, t, fallback);
    const n = Number(v);
    return Number.isFinite(n) && v.trim() !== "" ? n : fallback;
  }
  return fallback;
}

/** Значение анимации (компонента `comp` вектора) в момент ctx.t. */
export function evalAnimComp(v: unknown, comp: number, fallback: number, ctx: EvalCtx): number {
  if (!isObj(v)) return scalar(v, comp, fallback, ctx, ctx.t);
  const o = v as AnimObj;
  const t = ctx.t - (fin(o.delay) ? o.delay : 0);
  let base: number;
  if (typeof o.expr === "string") base = exprValue(o.expr, ctx, t, fallback);
  else if (o.keys !== undefined) {
    const keys = keysOf(o);
    if (!keys.length) base = fallback;
    else {
      const seg = keySegment(keys, loopTime(t, keys, o.loop));
      const a = scalar(seg.a.v, comp, fallback, ctx, t);
      const b = scalar(seg.b.v, comp, fallback, ctx, t);
      base = a + (b - a) * seg.p;
    }
  } else if (o.from !== undefined || o.to !== undefined) {
    const a = scalar(o.from, comp, fallback, ctx, t);
    const b = scalar(o.to, comp, fallback, ctx, t);
    base = a + (b - a) * tweenProgress(o, t);
  } else if (o.base !== undefined || o.value !== undefined) base = scalar(o.base ?? o.value, comp, fallback, ctx, t);
  else base = fallback;
  return base + additive(o, t, ctx, comp);
}

export function evalNum(v: unknown, fallback: number, ctx: EvalCtx): number {
  if (v === undefined || v === null) return fallback;
  if (fin(v)) return v;
  return evalAnimComp(v, 0, fallback, ctx);
}

/**
 * Вектор из n компонент: массив (каждая — анимация), объект-анимация с векторными v/from/to,
 * или число — у масштаба `broadcast` (одно число на все оси), иначе запасное значение.
 */
export function evalVec(v: unknown, fallback: number[], ctx: EvalCtx, broadcast = false): number[] {
  const n = fallback.length;
  if (v === undefined || v === null) return fallback.slice();
  if (Array.isArray(v)) {
    const out = new Array<number>(n);
    for (let k = 0; k < n; k++) out[k] = v[k] === undefined ? fallback[k] : evalNum(v[k], fallback[k], ctx);
    return out;
  }
  if (fin(v) || typeof v === "string") {
    const s = evalNum(v, NaN, ctx);
    if (!Number.isFinite(s)) return fallback.slice();
    return broadcast ? new Array(n).fill(s) : fallback.map((f, k) => (k === 0 ? s : f));
  }
  if (isObj(v)) {
    const scalarBroadcast = broadcast && !hasVector(v as AnimObj);
    const out = new Array<number>(n);
    for (let k = 0; k < n; k++) out[k] = evalAnimComp(v, scalarBroadcast ? 0 : k, fallback[k], ctx);
    return out;
  }
  return fallback.slice();
}

function hasVector(o: AnimObj): boolean {
  if (Array.isArray(o.from) || Array.isArray(o.to) || Array.isArray(o.base) || Array.isArray(o.value)) return true;
  return keysOf(o).some((k) => Array.isArray(k.v));
}

/* ---------- цвета ---------- */

export type RGBA = [number, number, number, number];

const NAMED: Record<string, string> = {
  white: "#ffffff",
  black: "#000000",
  red: "#ff3b30",
  green: "#34c759",
  blue: "#007aff",
  yellow: "#ffd400",
  orange: "#ff9500",
  purple: "#8b5cf6",
  pink: "#ff2d55",
  cyan: "#22d3ee",
  gray: "#8e8e93",
  grey: "#8e8e93",
  transparent: "#00000000",
};

export function parseColor(v: unknown): RGBA | null {
  if (typeof v !== "string") return null;
  const s = v.trim().toLowerCase();
  const hex = NAMED[s] ?? s;
  const c = parseHex(hex);
  if (c) return [c[0], c[1], c[2], c[3]];
  const m = /^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)$/.exec(s);
  if (m) return [Number(m[1]), Number(m[2]), Number(m[3]), m[4] !== undefined ? Number(m[4]) : 1];
  return null;
}

export function mixRGBA(a: RGBA, b: RGBA, p: number): RGBA {
  return [a[0] + (b[0] - a[0]) * p, a[1] + (b[1] - a[1]) * p, a[2] + (b[2] - a[2]) * p, a[3] + (b[3] - a[3]) * p];
}

/** Цвет: строка или анимация `{keys: [[t, "#hex"]…]}` / `{from, to, at, duration, ease}`. */
export function evalColor(v: unknown, fallback: RGBA, ctx: EvalCtx): RGBA {
  if (v === undefined || v === null) return fallback;
  const direct = parseColor(v);
  if (direct) return direct;
  if (!isObj(v)) return fallback;
  const o = v as AnimObj;
  const t = ctx.t - (fin(o.delay) ? o.delay : 0);
  if (o.keys !== undefined) {
    const keys = keysOf(o);
    if (!keys.length) return fallback;
    const seg = keySegment(keys, loopTime(t, keys, o.loop));
    const a = parseColor(seg.a.v) ?? fallback;
    const b = parseColor(seg.b.v) ?? fallback;
    return mixRGBA(a, b, Math.max(0, Math.min(1, seg.p)));
  }
  if (o.from !== undefined || o.to !== undefined) {
    const a = parseColor(o.from) ?? fallback;
    const b = parseColor(o.to) ?? fallback;
    return mixRGBA(a, b, Math.max(0, Math.min(1, tweenProgress(o, t))));
  }
  return fallback;
}

export const EASING_LIST = Object.keys(EASINGS);
