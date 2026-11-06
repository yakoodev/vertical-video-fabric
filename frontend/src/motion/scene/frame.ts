// Общее для рисования сцены: пределы, типы кадра и материала, цветовые утилиты. Части рендера см. render.ts.
import { noise1 } from "../keys";
import type { Ctx2D, RenderEnv } from "../types";
import type { Mat, ViewCamera } from "./math";
import type { EvalCtx, RGBA } from "./value";
import type { Light } from "./lighting";
import type { Shading } from "./nodeTypes";

export type SceneSpec = Record<string, unknown>;
export type Node = Record<string, unknown>;

export type SceneRenderOptions = {
  /** Вьюпорт в единицах слоя (опорные px): центр — (0, 0). */
  w: number;
  h: number;
  /** Время слоя, с, и его длина. */
  t: number;
  dur: number;
  env: RenderEnv;
  /** Обрезать рисунок по вьюпорту (по умолчанию да). */
  clip?: boolean;
  /** Зерно по умолчанию (от id слоя): у копии слоя своя раскладка частиц. */
  seed?: number;
};

export type SceneStats = { faces: number; drawn: number; items: number; culled: number };

/* ---------- пределы ---------- */

export const SCENE_LIMITS = {
  /** Граней в кадре — дальше узлы не рисуются (и проверка предупреждает с 4000). */
  maxFaces: 20000,
  warnFaces: 4000,
  maxNodes: 5000,
  maxRepeat: 500,
  maxParticles: 600,
  maxDepth: 24,
  maxTextLen: 200,
};

/* ---------- утилиты ---------- */

export const isObj = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
export const fin = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
export const numOr = (v: unknown, d: number) => (fin(v) ? v : d);
export const clamp01 = (v: number) => (v < 0 ? 0 : v > 1 ? 1 : v);

const styleCache = new Map<number, string>();
export function rgbStr(c: RGBA): string {
  const r = Math.max(0, Math.min(255, Math.round(c[0])));
  const g = Math.max(0, Math.min(255, Math.round(c[1])));
  const b = Math.max(0, Math.min(255, Math.round(c[2])));
  const key = (r << 16) | (g << 8) | b;
  let s = styleCache.get(key);
  if (!s) {
    s = `rgb(${r},${g},${b})`;
    if (styleCache.size > 8192) styleCache.clear();
    styleCache.set(key, s);
  }
  return s;
}
export function rgbaStr(c: RGBA, a = 1): string {
  const al = Math.max(0, Math.min(1, c[3] * a));
  if (al >= 0.999) return rgbStr(c);
  return `rgba(${Math.round(c[0])},${Math.round(c[1])},${Math.round(c[2])},${Math.round(al * 1000) / 1000})`;
}
export const WHITE: RGBA = [255, 255, 255, 1];
export const BLACK: RGBA = [0, 0, 0, 1];
export const scaleRGB = (c: RGBA, k: number): RGBA => [c[0] * k, c[1] * k, c[2] * k, c[3]];

export type Material = {
  color: RGBA;
  shading: Shading;
  bands: number;
  ramp: RGBA[] | null;
  shadowColor: RGBA | null;
  opacity: number;
  outline: { color: RGBA; width: number; crease: number } | null;
  rim: { color: RGBA; strength: number; power: number } | null;
  specular: { color: RGBA; size: number } | null;
  emissive: RGBA | null;
  pattern: { kind: "halftone" | "hatch"; size: number; color: RGBA | null } | null;
  doubleSided: boolean | null;
  fog: boolean;
  seams: boolean;
  /** Мягкий свет по сглаженным нормалям; null — по типу сетки (шар, тор, цилиндр — да). */
  smooth: boolean | null;
};

export type Fog = { color: RGBA; near: number; far: number; max: number } | null;

/* ---------- элементы кадра ---------- */

export type Ink = { color: string; width: number };
export type Part = { ring: number[]; fill: string; pattern: CanvasPattern | null };
export type PolyItem = {
  k: "poly";
  rings: number[][];
  /** Грань, разрезанная по полосам света: части со своими заливками (обводка — по rings). */
  parts?: Part[] | null;
  fill: string | CanvasGradient | null;
  alpha: number;
  seam: boolean;
  pattern: CanvasPattern | null;
  ink: Ink | null;
  /** Отрезки обводки: x1, y1, x2, y2 подряд. */
  edges: number[] | null;
  /** Обводка всего контура (stroke плоской фигуры). */
  stroke: Ink | null;
  evenOdd: boolean;
};
export type FnItem = { k: "fn"; draw: (ctx: Ctx2D) => void };
export type Item = (PolyItem | FnItem) & { order: number; depth: number; sub: number; seq: number };

/* ---------- кадр ---------- */

export type Frame = {
  ctx: Ctx2D;
  env: RenderEnv;
  cam: ViewCamera;
  lights: Light[];
  fog: Fog;
  items: Item[];
  T: number;
  dur: number;
  seed: number;
  w: number;
  h: number;
  /** px холста на единицу слоя — для швов и тонких линий. */
  dev: number;
  stats: SceneStats;
  nodes: number;
  seq: number;
  /** Плоскость камеры для отсечения: z ≤ −near. */
  near: number;
  far: number;
  /** Расстояние камеры по умолчанию: 1 единица = 1 px на z = 0 (переменная D выражений). */
  D: number;
};

export type Inherit = {
  m: Mat;
  opacity: number;
  tint: RGBA | null;
  mat: Record<string, unknown> | null;
  /** t узла (с) и переменные выражений. */
  t: number;
  i: number;
  n: number;
  order: number;
  /** Общая глубина группы с sort: "object" (null — каждая грань своей). */
  group: number | null;
  depth: number;
};

export function ectx(f: Frame, inh: Inherit, t = inh.t): EvalCtx {
  return { t, T: f.T, dur: f.dur, i: inh.i, n: inh.n, seed: f.seed, W: f.w, H: f.h, D: f.D };
}

export const noise1v = (seed: number, x: number) => noise1(seed >>> 0, x);
