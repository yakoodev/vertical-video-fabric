/**
 * Программный 3D/2D-рендер сцены на обычном 2D-холсте (без WebGL: в Node рисует Skia через
 * @napi-rs/canvas, в браузере — тот же код). Кадр = f(t): ни Date, ни Math.random.
 *
 * Конвейер кадра:
 * 1. камера и свет в момент t (value.ts — ключи, пружины, шум, выражения);
 * 2. обход графа узлов: матрицы (перенос · поворот X→Y→Z · масштаб), окна видимости,
 *    enter/exit, follow-path, repeat/stagger, частицы;
 * 3. каждая грань сетки → пространство камеры → отсечение задних граней → отсечение ближней
 *    плоскостью → цвет по материалу (flat / lambert / toon / gradient / halftone / hatch,
 *    rim, блик, туман) → проекция; плоские фигуры, текст, картинки, линии — так же «элементы»;
 * 4. сортировка художника: order, потом глубина (центр грани), потом порядок появления;
 * 5. рисование дальних первыми; обводка силуэта/изломов — рёбрами вместе с гранью.
 *
 * Пределы метода (docs/motion/scene-engine.md): пересекающиеся грани и циклическое перекрытие
 * не разрешаются (нет z-буфера) — для этого `order` и `sort: "object"`.
 */
import { fontString } from "../fonts";
import { hashString, noise1, rand01 } from "../keys";
import type { Ctx2D, RenderEnv } from "../types";
import {
  boxMesh,
  cachedMesh,
  catmull,
  cylinderMesh,
  extrudeMesh,
  icosphereMesh,
  latheMesh,
  parsePath,
  planeMesh,
  polylineAt,
  presetShape,
  smoothNormals,
  sphereMesh,
  torusMesh,
  type Mesh,
  type Pt2,
} from "./geometry";
import { apply, compose, DEG, focalFor, identity, lookAt, mul, normalMatrix, norm, type Mat, type Vec3, type ViewCamera } from "./math";
import { evalColor, evalNum, evalVec, mixRGBA, parseColor, springProgress, type EvalCtx, type RGBA } from "./value";
import { easingFn } from "../easing";

export type SceneSpec = Record<string, unknown>;
type Node = Record<string, unknown>;

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

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const fin = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const numOr = (v: unknown, d: number) => (fin(v) ? v : d);
const clamp01 = (v: number) => (v < 0 ? 0 : v > 1 ? 1 : v);

const styleCache = new Map<number, string>();
function rgbStr(c: RGBA): string {
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
function rgbaStr(c: RGBA, a = 1): string {
  const al = Math.max(0, Math.min(1, c[3] * a));
  if (al >= 0.999) return rgbStr(c);
  return `rgba(${Math.round(c[0])},${Math.round(c[1])},${Math.round(c[2])},${Math.round(al * 1000) / 1000})`;
}
const WHITE: RGBA = [255, 255, 255, 1];
const BLACK: RGBA = [0, 0, 0, 1];
const scaleRGB = (c: RGBA, k: number): RGBA => [c[0] * k, c[1] * k, c[2] * k, c[3]];

/* ---------- материал ---------- */

export const SHADINGS = ["flat", "lambert", "toon", "gradient", "halftone", "hatch"] as const;
export type Shading = (typeof SHADINGS)[number];

type Material = {
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

/** Слить материалы: родитель (группа, style сцены) → узел. Поля узла перекрывают. */
function mergeMat(parent: Record<string, unknown> | null, own: unknown, colorShortcut: unknown): Record<string, unknown> | null {
  if (!isObj(own) && colorShortcut === undefined) return parent;
  const out: Record<string, unknown> = { ...(parent || {}) };
  if (isObj(own)) Object.assign(out, own);
  if (colorShortcut !== undefined && !(isObj(own) && own.color !== undefined)) out.color = colorShortcut;
  return out;
}

function resolveMat(raw: Record<string, unknown> | null, ctx: EvalCtx, tint: RGBA | null, fallbackShading: Shading): Material {
  const m = raw || {};
  const shading = (SHADINGS as readonly string[]).includes(String(m.shading)) ? (m.shading as Shading) : fallbackShading;
  let color = evalColor(m.color, [210, 210, 220, 1], ctx);
  if (tint) color = tint;
  const outlineRaw = m.outline;
  let outline: Material["outline"] = null;
  if (outlineRaw === true || isObj(outlineRaw)) {
    const o = isObj(outlineRaw) ? outlineRaw : {};
    if (o.enabled !== false) {
      outline = {
        color: evalColor(o.color, BLACK, ctx),
        width: Math.max(0, evalNum(o.width, 4, ctx)),
        crease: Math.max(0, Math.min(180, numOr(o.crease, 40))),
      };
      if (outline.width <= 0) outline = null;
    }
  }
  const rimRaw = m.rim;
  let rim: Material["rim"] = null;
  if (rimRaw === true || isObj(rimRaw)) {
    const r = isObj(rimRaw) ? rimRaw : {};
    rim = { color: evalColor(r.color, WHITE, ctx), strength: clamp01(evalNum(r.strength, 0.6, ctx)), power: Math.max(0.5, numOr(r.power, 2.5)) };
  }
  const specRaw = m.specular;
  let specular: Material["specular"] = null;
  if (specRaw === true || isObj(specRaw)) {
    const s = isObj(specRaw) ? specRaw : {};
    specular = { color: evalColor(s.color, WHITE, ctx), size: Math.max(0.001, Math.min(0.9, numOr(s.size, 0.04))) };
  }
  let pattern: Material["pattern"] = null;
  if (shading === "halftone" || shading === "hatch") {
    const p = isObj(m[shading]) ? (m[shading] as Record<string, unknown>) : {};
    pattern = { kind: shading, size: Math.max(3, Math.min(64, numOr(p.size, shading === "halftone" ? 10 : 9))), color: parseColor(p.color) };
  }
  const ramp = Array.isArray(m.colors) ? (m.colors.map(parseColor).filter(Boolean) as RGBA[]) : null;
  return {
    color,
    shading,
    bands: Math.max(2, Math.min(6, Math.round(numOr(m.bands, 3)))),
    ramp: ramp && ramp.length >= 2 ? ramp : null,
    shadowColor: m.shadowColor !== undefined ? evalColor(m.shadowColor, BLACK, ctx) : null,
    opacity: clamp01(evalNum(m.opacity, 1, ctx)),
    outline,
    rim,
    specular,
    emissive: m.emissive !== undefined ? evalColor(m.emissive, BLACK, ctx) : null,
    pattern,
    doubleSided: typeof m.doubleSided === "boolean" ? m.doubleSided : null,
    fog: m.fog !== false,
    seams: m.seams !== false,
    smooth: typeof m.smooth === "boolean" ? m.smooth : null,
  };
}

function sampleRamp(ramp: RGBA[], p: number): RGBA {
  const q = clamp01(p) * (ramp.length - 1);
  const i = Math.min(ramp.length - 2, Math.floor(q));
  return mixRGBA(ramp[i], ramp[i + 1], q - i);
}

/* ---------- свет и туман ---------- */

type Light =
  | { kind: "ambient"; color: RGBA; i: number }
  | { kind: "hemisphere"; sky: RGBA; ground: RGBA; i: number }
  | { kind: "directional"; color: RGBA; i: number; dir: Vec3 }
  | { kind: "point"; color: RGBA; i: number; pos: Vec3; range: number };

const DEFAULT_LIGHTS: Node[] = [
  { type: "ambient", intensity: 0.42 },
  { type: "directional", direction: [-0.6, 0.8, 0.7], intensity: 0.85 },
];

function evalLights(raw: unknown, ctx: EvalCtx): Light[] {
  const list = Array.isArray(raw) ? raw.filter(isObj) : DEFAULT_LIGHTS;
  const out: Light[] = [];
  for (const l of list) {
    const i = Math.max(0, evalNum(l.intensity, l.type === "ambient" || l.type === "hemisphere" ? 0.4 : 0.8, ctx));
    const color = evalColor(l.color, WHITE, ctx);
    switch (l.type) {
      case "ambient":
        out.push({ kind: "ambient", color, i });
        break;
      case "hemisphere":
        out.push({ kind: "hemisphere", sky: evalColor(l.sky, [200, 220, 255, 1], ctx), ground: evalColor(l.ground, [60, 50, 70, 1], ctx), i });
        break;
      case "point":
        out.push({ kind: "point", color, i, pos: evalVec(l.position, [0, 300, 600], ctx) as Vec3, range: Math.max(1, evalNum(l.range, 3000, ctx)) });
        break;
      default:
        out.push({ kind: "directional", color, i, dir: norm(evalVec(l.direction, [-0.6, 0.8, 0.7], ctx) as Vec3) });
    }
  }
  return out;
}

type Fog = { color: RGBA; near: number; far: number; max: number } | null;

/* ---------- элементы кадра ---------- */

type Ink = { color: string; width: number };
type Part = { ring: number[]; fill: string; pattern: CanvasPattern | null };
type PolyItem = {
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
type FnItem = { k: "fn"; draw: (ctx: Ctx2D) => void };
type Item = (PolyItem | FnItem) & { order: number; depth: number; sub: number; seq: number };

/* ---------- кадр ---------- */

type Frame = {
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
};

type Inherit = {
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

function ectx(f: Frame, inh: Inherit, t = inh.t): EvalCtx {
  return { t, T: f.T, dur: f.dur, i: inh.i, n: inh.n, seed: f.seed };
}

/* ---------- камера ---------- */

export const CAMERA_TYPES = ["perspective", "orthographic"] as const;

/** Камера в момент t: `position`/`target` или `orbit` {azimuth, elevation, distance}, `shake`. */
export function evalCamera(raw: unknown, ctx: EvalCtx, h: number): ViewCamera {
  const c = isObj(raw) ? raw : {};
  const type = c.type === "orthographic" || c.type === "ortho" ? "orthographic" : "perspective";
  const fov = Math.max(1, Math.min(170, evalNum(c.fov, 40, ctx)));
  const zoom = Math.max(0.01, evalNum(c.zoom, 1, ctx));
  const base = focalFor(h, fov);
  let target = evalVec(c.target, [0, 0, 0], ctx) as Vec3;
  let eye: Vec3;
  if (isObj(c.orbit)) {
    const o = c.orbit;
    if (o.target !== undefined) target = evalVec(o.target, target, ctx) as Vec3;
    const az = evalNum(o.azimuth, 0, ctx) * DEG;
    const el = Math.max(-89.9, Math.min(89.9, evalNum(o.elevation, 0, ctx))) * DEG;
    const dist = Math.max(1, evalNum(o.distance, base, ctx));
    eye = [target[0] + dist * Math.cos(el) * Math.sin(az), target[1] + dist * Math.sin(el), target[2] + dist * Math.cos(el) * Math.cos(az)];
  } else eye = evalVec(c.position, [0, 0, base], ctx) as Vec3;
  let roll = evalNum(c.roll, 0, ctx);
  if (isObj(c.shake)) {
    const s = c.shake;
    const amount = evalNum(s.amount, 12, ctx);
    const freq = Math.max(0.01, evalNum(s.freq, 6, ctx));
    const seed = hashString(String(s.seed ?? "shake")) ^ ctx.seed;
    const n1 = (k: number) => noise1v(seed + k * 101, ctx.t * freq) * 0.7 + noise1v(seed + k * 101 + 7, ctx.t * freq * 2.1) * 0.3;
    const dx = amount * n1(1);
    const dy = amount * n1(2);
    eye = [eye[0] + dx, eye[1] + dy, eye[2]];
    target = [target[0] + dx, target[1] + dy, target[2]];
    roll += evalNum(s.roll, amount * 0.05, ctx) * n1(3);
  }
  const up = evalVec(c.up, [0, 1, 0], ctx) as Vec3;
  const view = lookAt(eye, target, up, roll);
  return {
    type,
    view,
    focal: type === "orthographic" ? zoom : base * zoom,
    zoom,
    near: Math.max(0.01, numOr(c.near, 5)),
    far: Math.max(10, numOr(c.far, 1e6)),
    eye,
  };
}

const noise1v = (seed: number, x: number) => noise1(seed >>> 0, x);

/* ---------- проекция ---------- */

function project(f: Frame, x: number, y: number, z: number): [number, number] | null {
  if (f.cam.type === "orthographic") return [x * f.cam.focal, -y * f.cam.focal];
  if (z > -f.near) return null;
  const k = f.cam.focal / -z;
  return [x * k, -y * k];
}

/** Многоугольник в пространстве камеры (x,y,z подряд) → отсечение ближней плоскостью. */
function clipNear(pts: number[], near: number): number[] {
  const out: number[] = [];
  const n = pts.length / 3;
  for (let i = 0; i < n; i++) {
    const ax = pts[i * 3];
    const ay = pts[i * 3 + 1];
    const az = pts[i * 3 + 2];
    const j = (i + 1) % n;
    const bx = pts[j * 3];
    const by = pts[j * 3 + 1];
    const bz = pts[j * 3 + 2];
    const ain = az <= -near;
    const bin = bz <= -near;
    if (ain) out.push(ax, ay, az);
    if (ain !== bin) {
      const k = (-near - az) / (bz - az);
      out.push(ax + (bx - ax) * k, ay + (by - ay) * k, -near);
    }
  }
  return out;
}

/* ---------- цвет грани ---------- */

/** Освещённость точки: доля света d (0..1), окружающий свет и сумма прямого света по каналам. */
type Lit = { d: number; ar: number; ag: number; ab: number; lr: number; lg: number; lb: number; key: Vec3 | null };

function lightAt(f: Frame, N: Vec3, P: Vec3): Lit {
  let ar = 0;
  let ag = 0;
  let ab = 0;
  let lr = 0;
  let lg = 0;
  let lb = 0;
  let dsum = 0;
  let dmax = 0;
  let key: Vec3 | null = null;
  let keyI = 0;
  for (const l of f.lights) {
    if (l.kind === "ambient") {
      ar += (l.color[0] / 255) * l.i;
      ag += (l.color[1] / 255) * l.i;
      ab += (l.color[2] / 255) * l.i;
    } else if (l.kind === "hemisphere") {
      const s = (N[1] + 1) / 2;
      ar += ((l.ground[0] + (l.sky[0] - l.ground[0]) * s) / 255) * l.i;
      ag += ((l.ground[1] + (l.sky[1] - l.ground[1]) * s) / 255) * l.i;
      ab += ((l.ground[2] + (l.sky[2] - l.ground[2]) * s) / 255) * l.i;
    } else {
      let L: Vec3;
      let att = 1;
      if (l.kind === "directional") L = l.dir;
      else {
        const dx = l.pos[0] - P[0];
        const dy = l.pos[1] - P[1];
        const dz = l.pos[2] - P[2];
        const dist = Math.hypot(dx, dy, dz) || 1;
        L = [dx / dist, dy / dist, dz / dist];
        att = clamp01(1 - dist / l.range);
      }
      const ndl = Math.max(0, N[0] * L[0] + N[1] * L[1] + N[2] * L[2]) * att;
      lr += (l.color[0] / 255) * l.i;
      lg += (l.color[1] / 255) * l.i;
      lb += (l.color[2] / 255) * l.i;
      dsum += l.i * ndl;
      dmax += l.i;
      if (l.i * att > keyI) {
        keyI = l.i * att;
        key = L;
      }
    }
  }
  return { d: dmax > 0 ? clamp01(dsum / dmax) : 1, ar, ag, ab, lr, lg, lb, key };
}

/** Полоса toon по доле света: floor(d·n + 0.25) — светлая часть чуть шире тени. */
const toonBand = (d: number, n: number) => Math.max(0, Math.min(n - 1, Math.floor(d * n + 0.25)));
/** Уровень узора halftone/hatch 0..4 по доле света. */
const patternLevel = (d: number) => Math.max(0, Math.min(4, Math.round((1 - d) * 4.4 - 0.4)));

/** Пороги d, на которых меняется цвет полосатой заливки (toon / halftone / hatch). */
function bandThresholds(mat: Material): number[] {
  if (mat.shading === "toon") return Array.from({ length: mat.bands - 1 }, (_, k) => (k + 1 - 0.25) / mat.bands);
  if (mat.shading === "halftone" || mat.shading === "hatch") return [1 - 3.9 / 4.4, 1 - 2.9 / 4.4, 1 - 1.9 / 4.4, 0.62, 1 - 0.9 / 4.4].sort((a, b) => a - b);
  return [];
}

/** Цвет модели освещения при доле света d (без бликов, rim и тумана) и уровень узора. */
function modelColor(mat: Material, lit: Lit, d: number): { c: RGBA; level: number } {
  const base = mat.color;
  switch (mat.shading) {
    case "flat":
      return { c: base, level: 0 };
    case "lambert":
      return {
        c: [base[0] * Math.min(1.25, lit.ar + d * lit.lr), base[1] * Math.min(1.25, lit.ag + d * lit.lg), base[2] * Math.min(1.25, lit.ab + d * lit.lb), base[3]],
        level: 0,
      };
    case "toon": {
      const ramp = mat.ramp ?? [mat.shadowColor ?? scaleRGB(base, 0.48), base];
      return { c: sampleRamp(ramp, toonBand(d, mat.bands) / (mat.bands - 1)), level: 0 };
    }
    case "gradient": {
      const ramp = mat.ramp ?? [mat.shadowColor ?? scaleRGB(base, 0.35), base, mixRGBA(base, WHITE, 0.35)];
      const amb = clamp01((lit.ar + lit.ag + lit.ab) / 3);
      return { c: sampleRamp(ramp, clamp01(amb * 0.5 + d * (1 - amb * 0.5))), level: 0 };
    }
    case "halftone":
    case "hatch":
      // Светлая заливка + узор тени: чем темнее грань, тем крупнее точки / гуще штрих.
      return { c: d > 0.62 ? base : scaleRGB(base, 0.9), level: patternLevel(d) };
    default:
      return { c: base, level: 0 };
  }
}

const banded = (s: Shading) => s === "toon" || s === "halftone" || s === "hatch";

/** Блик: скаляр N·H с главным светом. */
function specDot(f: Frame, N: Vec3, P: Vec3, key: Vec3 | null): number {
  if (!key) return -1;
  const V = viewDir(f, P);
  const H = norm([key[0] + V[0], key[1] + V[1], key[2] + V[2]]);
  return N[0] * H[0] + N[1] * H[1] + N[2] * H[2];
}

/** Обратный свет: (1 − N·V)^power · strength. */
function rimTerm(f: Frame, mat: Material, N: Vec3, P: Vec3): number {
  if (!mat.rim) return 0;
  const V = viewDir(f, P);
  const ndv = Math.max(0, N[0] * V[0] + N[1] * V[1] + N[2] * V[2]);
  return Math.pow(1 - ndv, mat.rim.power) * mat.rim.strength;
}

/** Порог «жёсткого» блика и rim у полосатых заливок. */
const RIM_CUT = 0.28;

/**
 * Довести цвет: блик и rim (у полосатых — жёсткими пятнами по флагам, у мягких — плавно),
 * свечение, туман.
 */
function finishColor(f: Frame, mat: Material, c: RGBA, spec: number, rim: number, depth: number): RGBA {
  if (mat.specular && spec > -1) {
    if (banded(mat.shading) || mat.shading === "flat") {
      if (spec > 1 - mat.specular.size) c = mat.specular.color;
    } else {
      const k = Math.pow(Math.max(0, spec), 2 / mat.specular.size) * 0.9;
      c = mixRGBA(c, mat.specular.color, clamp01(k));
    }
  }
  if (mat.rim && rim > 0.004) c = banded(mat.shading) ? (rim >= RIM_CUT ? mixRGBA(c, mat.rim.color, 0.85) : c) : mixRGBA(c, mat.rim.color, clamp01(rim));
  if (mat.emissive) c = [c[0] + mat.emissive[0], c[1] + mat.emissive[1], c[2] + mat.emissive[2], c[3]];
  if (f.fog && mat.fog) c = fogged(f, c, depth);
  return c;
}

/** Цвет плоской грани с нормалью N (мир) в точке P (мир) и уровень узора. */
function shadeFace(f: Frame, mat: Material, N: Vec3, P: Vec3, depth: number): { c: RGBA; level: number } {
  if (mat.shading === "flat" && !mat.rim && !mat.specular) return { c: f.fog && mat.fog ? fogged(f, mat.color, depth) : mat.color, level: 0 };
  const lit = lightAt(f, N, P);
  const m = modelColor(mat, lit, lit.d);
  return { c: finishColor(f, mat, m.c, mat.specular ? specDot(f, N, P, lit.key) : -1, rimTerm(f, mat, N, P), depth), level: m.level };
}

/* ---------- разрезание грани по полосам света ---------- */

/**
 * Многоугольник экрана (x, y подряд) с полями в углах (по F чисел на угол) → часть, где поле
 * `fi` ≥ tau (`above`) или < tau. Линейная интерполяция по рёбрам — граница полосы ровная.
 */
function clipField(pts: number[], vals: number[], F: number, fi: number, tau: number, above: boolean): [number[], number[]] {
  const n = pts.length / 2;
  const op: number[] = [];
  const ov: number[] = [];
  for (let i = 0; i < n; i++) {
    const j = (i + 1) % n;
    const a = vals[i * F + fi];
    const b = vals[j * F + fi];
    const ain = above ? a >= tau : a < tau;
    const bin = above ? b >= tau : b < tau;
    if (ain) {
      op.push(pts[i * 2], pts[i * 2 + 1]);
      for (let k = 0; k < F; k++) ov.push(vals[i * F + k]);
    }
    if (ain !== bin) {
      const q = (tau - a) / (b - a);
      op.push(pts[i * 2] + (pts[j * 2] - pts[i * 2]) * q, pts[i * 2 + 1] + (pts[j * 2 + 1] - pts[i * 2 + 1]) * q);
      for (let k = 0; k < F; k++) ov.push(vals[i * F + k] + (vals[j * F + k] - vals[i * F + k]) * q);
    }
  }
  return [op, ov];
}

/** Разрезать по порогам поля fi: части от нижней полосы к верхней (пустые пропущены) с номером полосы. */
function splitBands(pts: number[], vals: number[], F: number, fi: number, ths: number[]): { pts: number[]; vals: number[]; band: number }[] {
  const out: { pts: number[]; vals: number[]; band: number }[] = [];
  let restP = pts;
  let restV = vals;
  for (let k = ths.length - 1; k >= 0 && restP.length >= 6; k--) {
    const [ap, av] = clipField(restP, restV, F, fi, ths[k], true);
    if (ap.length >= 6) out.push({ pts: ap, vals: av, band: k + 1 });
    [restP, restV] = clipField(restP, restV, F, fi, ths[k], false);
  }
  if (restP.length >= 6) out.push({ pts: restP, vals: restV, band: 0 });
  return out;
}

function fogged(f: Frame, c: RGBA, depth: number): RGBA {
  const fog = f.fog!;
  const k = clamp01((depth - fog.near) / Math.max(1e-6, fog.far - fog.near)) * fog.max;
  return k > 0.002 ? mixRGBA(c, [fog.color[0], fog.color[1], fog.color[2], c[3]], k) : c;
}

function viewDir(f: Frame, P: Vec3): Vec3 {
  if (f.cam.type === "orthographic") {
    // Ортография: на зрителя — третья строка вида (ось −z камеры в мире, с минусом).
    const v = f.cam.view;
    return [v[8], v[9], v[10]];
  }
  return norm([f.cam.eye[0] - P[0], f.cam.eye[1] - P[1], f.cam.eye[2] - P[2]]);
}

/* ---------- узоры halftone / hatch ---------- */

const patternCache = new WeakMap<object, Map<string, CanvasPattern | null>>();

function patternFor(f: Frame, kind: "halftone" | "hatch", level: number, size: number, color: RGBA): CanvasPattern | null {
  if (level <= 0 || !f.env.createCanvas) return null;
  let byCtx = patternCache.get(f.ctx as unknown as object);
  if (!byCtx) {
    byCtx = new Map();
    patternCache.set(f.ctx as unknown as object, byCtx);
  }
  const key = `${kind}|${level}|${size}|${rgbStr(color)}`;
  if (byCtx.has(key)) return byCtx.get(key)!;
  const s = Math.max(3, Math.round(size));
  const cv = f.env.createCanvas(s, s);
  const c = cv.getContext("2d") as Ctx2D;
  c.fillStyle = rgbStr(color);
  c.strokeStyle = rgbStr(color);
  if (kind === "halftone") {
    // Точка в центре и четверти по углам — сетка под 45°, как у печатного растра.
    const r = s * 0.5 * Math.sqrt(level / 4) * 0.98;
    c.beginPath();
    c.arc(s / 2, s / 2, r, 0, Math.PI * 2);
    for (const [x, y] of [[0, 0], [s, 0], [0, s], [s, s]]) {
      c.moveTo(x + r, y);
      c.arc(x, y, r, 0, Math.PI * 2);
    }
    c.fill();
  } else {
    c.lineWidth = Math.max(0.8, (s * level) / 10);
    c.lineCap = "square";
    c.beginPath();
    c.moveTo(0, s);
    c.lineTo(s, 0);
    c.moveTo(-s / 2, s / 2);
    c.lineTo(s / 2, -s / 2);
    c.moveTo(s / 2, s * 1.5);
    c.lineTo(s * 1.5, s / 2);
    if (level >= 3) {
      // Густая тень — перекрёстный штрих.
      c.moveTo(0, 0);
      c.lineTo(s, s);
      c.moveTo(-s / 2, s / 2);
      c.lineTo(s / 2, s * 1.5);
      c.moveTo(s / 2, -s / 2);
      c.lineTo(s * 1.5, s / 2);
    }
    c.stroke();
  }
  let pat: CanvasPattern | null = null;
  try {
    pat = f.ctx.createPattern(cv as unknown as CanvasImageSource, "repeat");
  } catch {
    pat = null;
  }
  byCtx.set(key, pat);
  return pat;
}

/* ---------- обход графа ---------- */

export const NODE_TYPES = [
  "group",
  "repeat",
  "box",
  "sphere",
  "icosphere",
  "cylinder",
  "cone",
  "plane",
  "torus",
  "lathe",
  "extrude",
  "text",
  "text3d",
  "image",
  "line",
  "rect",
  "circle",
  "polygon",
  "star",
  "path",
  "shape",
  "particles",
] as const;

export const MESH_TYPES = new Set(["box", "sphere", "icosphere", "cylinder", "cone", "plane", "torus", "lathe", "extrude"]);
export const FLAT_TYPES = new Set(["rect", "circle", "polygon", "star", "path", "shape"]);

function enterExit(node: Node, f: Frame, inh: Inherit, t: number) {
  // Возвращает добавки к положению/повороту, множители масштаба и прозрачности.
  const out = { dp: [0, 0, 0] as Vec3, dr: [0, 0, 0] as Vec3, ks: [1, 1, 1] as Vec3, ko: 1 };
  const one = (spec: unknown, isEnter: boolean) => {
    if (!isObj(spec)) return;
    const pose = isObj(isEnter ? spec.from : spec.to) ? ((isEnter ? spec.from : spec.to) as Record<string, unknown>) : {};
    const duration = Math.max(0.01, numOr(spec.duration, isEnter ? 0.6 : 0.4));
    const offset = inh.t - t; // stagger/offset родителей уже в t узла
    const at = fin(spec.at) ? spec.at : isEnter ? 0 : f.dur - duration - (f.T - inh.t) - offset;
    let p: number;
    if (spec.spring) {
      const s = isObj(spec.spring) ? spec.spring : {};
      p = springProgress(t - at, numOr(s.stiffness, 170), numOr(s.damping, 14), numOr(s.mass, 1));
    } else {
      const raw = (t - at) / duration;
      p = easingFn(typeof spec.ease === "string" ? spec.ease : isEnter ? "outCubic" : "inCubic")(raw <= 0 ? 0 : raw >= 1 ? 1 : raw);
    }
    // enter: «от позы к покою» — вес позы (1−p); exit: «от покоя к позе» — вес p.
    const wgt = isEnter ? 1 - p : p;
    if (Math.abs(wgt) < 1e-6) return;
    const c = ectx(f, inh, t);
    if (pose.position !== undefined) {
      const v = evalVec(pose.position, [0, 0, 0], c);
      for (let k = 0; k < 3; k++) out.dp[k] += v[k] * wgt;
    }
    if (pose.rotation !== undefined) {
      const v = evalVec(pose.rotation, [0, 0, 0], c);
      for (let k = 0; k < 3; k++) out.dr[k] += v[k] * wgt;
    }
    if (pose.scale !== undefined) {
      const v = evalVec(pose.scale, [1, 1, 1], c, true);
      for (let k = 0; k < 3; k++) out.ks[k] *= 1 + (v[k] - 1) * wgt;
    }
    if (pose.opacity !== undefined) out.ko *= Math.max(0, 1 + (evalNum(pose.opacity, 1, c) - 1) * Math.min(1, Math.max(0, wgt)));
  };
  one(node.enter, true);
  one(node.exit, false);
  return out;
}

/** Время узла: offset/delay, скорость, петля. */
const NO_EE = { dp: [0, 0, 0] as Vec3, dr: [0, 0, 0] as Vec3, ks: [1, 1, 1] as Vec3, ko: 1 };

function nodeTime(node: Node, t: number): number {
  let tt = t;
  const tm = isObj(node.time) ? node.time : null;
  if (fin(node.delay)) tt -= node.delay;
  if (tm) {
    if (fin(tm.offset)) tt -= tm.offset;
    if (fin(tm.speed)) tt *= tm.speed;
    if (fin(tm.loop) && tm.loop > 0) tt = ((tt % tm.loop) + tm.loop) % tm.loop;
  }
  return tt;
}

function visit(node: unknown, f: Frame, inh: Inherit): void {
  if (!isObj(node) || node.visible === false) return;
  if (++f.nodes > SCENE_LIMITS.maxNodes || inh.depth > SCENE_LIMITS.maxDepth) return;
  if (f.stats.faces >= SCENE_LIMITS.maxFaces) return;
  const t = nodeTime(node, inh.t);
  if (fin(node.start) && t < node.start) return;
  if (fin(node.end) && t >= node.end) return;
  const c = ectx(f, inh, t);
  const type = String(node.type ?? "group");
  // Текст по буквам: enter/exit играет каждая буква со своим stagger, не весь блок.
  const splitText = (type === "text" || type === "text3d") && !node.__glyph && (node.split === "letters" || node.split === "words" || node.split === "lines");
  const ee = splitText ? NO_EE : enterExit(node, f, inh, t);
  let pos = evalVec(node.position, [0, 0, 0], c) as Vec3;
  let rot = evalVec(node.rotation, [0, 0, 0], c) as Vec3;
  const scl = evalVec(node.scale, [1, 1, 1], c, true) as Vec3;
  if (isObj(node.follow)) {
    const fl = node.follow;
    const pts = (Array.isArray(fl.points) ? fl.points : []).filter((p) => Array.isArray(p)).map((p) => [numOr(p[0], 0), numOr(p[1], 0), numOr(p[2], 0)] as Vec3);
    if (pts.length >= 2) {
      const closed = fl.closed === true;
      const s = evalNum(fl.progress, f.dur > 0 ? clamp01(t / f.dur) : 0, c);
      const at = (q: number) => (fl.smooth === false ? polylineAt(pts, closed, q) : catmull(pts, closed, q));
      const sp = closed ? ((s % 1) + 1) % 1 : clamp01(s);
      const p = at(sp);
      pos = [pos[0] + p[0], pos[1] + p[1], pos[2] + p[2]];
      if (fl.orient === true) {
        const q = at(Math.min(1, sp + 0.002));
        const b = at(Math.max(0, sp - 0.002));
        const d = norm([q[0] - b[0], q[1] - b[1], q[2] - b[2]]);
        const yaw = Math.atan2(-d[2], d[0]) / DEG;
        const pitch = Math.atan2(d[1], Math.hypot(d[0], d[2])) / DEG;
        rot = [rot[0], rot[1] + yaw, rot[2] + pitch];
      }
    }
  }
  pos = [pos[0] + ee.dp[0], pos[1] + ee.dp[1], pos[2] + ee.dp[2]];
  rot = [rot[0] + ee.dr[0], rot[1] + ee.dr[1], rot[2] + ee.dr[2]];
  const s3: Vec3 = [scl[0] * ee.ks[0], scl[1] * ee.ks[1], scl[2] * ee.ks[2]];
  if (Math.abs(s3[0]) < 1e-6 && Math.abs(s3[1]) < 1e-6) return;
  let local = compose(pos, rot, s3);
  if (node.pivot !== undefined) {
    const pv = evalVec(node.pivot, [0, 0, 0], c);
    local = mul(local, compose([-pv[0], -pv[1], -pv[2]], [0, 0, 0], [1, 1, 1]));
  }
  const m = mul(inh.m, local);
  const opacity = inh.opacity * clamp01(evalNum(node.opacity, 1, c)) * ee.ko;
  if (opacity <= 0.002) return;
  const mat = mergeMat(inh.mat, node.material, node.color);
  const order = inh.order + (fin(node.order) ? Math.round(node.order) : 0);
  let group = inh.group;
  if (node.sort === "object" && group === null) {
    const o = apply(mul(f.cam.view, m), 0, 0, 0);
    group = o[2];
  }
  const me: Inherit = { ...inh, m, opacity, mat, t, order, group, depth: inh.depth + 1 };

  if (MESH_TYPES.has(type)) drawMesh(node, type, f, me, c);
  else if (FLAT_TYPES.has(type)) drawFlat(node, type, f, me, c);
  else if (type === "text" || type === "text3d") drawText(node, type, f, me, c);
  else if (type === "image") drawImageNode(node, f, me, c);
  else if (type === "line") drawLine(node, f, me, c);
  else if (type === "particles") drawParticles(node, f, me, c);
  else if (type === "repeat") {
    visitRepeat(node, f, me, c);
    return;
  }

  const kids = Array.isArray(node.children) ? node.children : null;
  if (kids) visitChildren(kids, node, f, me);
}

function staggerOrder(n: number, from: unknown, seed: number): number[] {
  const idx = Array.from({ length: n }, (_, i) => i);
  if (from === "end") return idx.map((i) => n - 1 - i);
  if (from === "center") return idx.map((i) => Math.abs(i - (n - 1) / 2));
  if (from === "edges") return idx.map((i) => (n - 1) / 2 - Math.abs(i - (n - 1) / 2));
  if (from === "random") return idx.map((i) => rand01(seed, i) * (n - 1));
  return idx;
}

function visitChildren(kids: unknown[], node: Node, f: Frame, me: Inherit): void {
  const st = fin(node.stagger) ? node.stagger : 0;
  const order = st ? staggerOrder(kids.length, node.staggerFrom, f.seed ^ 0x51ed) : null;
  for (let k = 0; k < kids.length; k++) {
    const off = order ? order[k] * st : 0;
    visit(kids[k], f, off ? { ...me, t: me.t - off } : me);
  }
}

/* ---------- repeat ---------- */

function visitRepeat(node: Node, f: Frame, me: Inherit, c: EvalCtx): void {
  const n = Math.max(0, Math.min(SCENE_LIMITS.maxRepeat, Math.round(evalNum(node.count, 6, c))));
  const child = isObj(node.child) ? node.child : null;
  const kids = child ? [child] : Array.isArray(node.children) ? node.children : [];
  if (!n || !kids.length) return;
  const layout = String(node.layout ?? "line");
  const seed = (hashString(String(node.seed ?? node.id ?? "repeat")) ^ f.seed) >>> 0;
  const st = fin(node.stagger) ? node.stagger : 0;
  const order = st ? staggerOrder(n, node.staggerFrom, seed) : null;
  const colors = Array.isArray(node.colors) ? (node.colors.map(parseColor).filter(Boolean) as RGBA[]) : [];
  const step = isObj(node.step) ? node.step : null;
  const jit = isObj(node.jitter) ? node.jitter : null;
  const spacing = evalVec(node.spacing, layout === "grid" ? [160, 160, 0] : [160, 0, 0], c);
  const cols = Math.max(1, Math.round(evalNum(node.cols, Math.ceil(Math.sqrt(n)), c)));
  const radius = evalNum(node.radius, 400, c);
  const arc = evalNum(node.arc, 360, c);
  const startAngle = evalNum(node.startAngle, 0, c);
  const axis = node.axis === "z" ? "z" : node.axis === "x" ? "x" : "y";
  const area = evalVec(node.area, [800, 800, 800], c);
  for (let i = 0; i < n; i++) {
    const vars: Inherit = { ...me, i, n, t: me.t - (order ? order[i] * st : 0) };
    const cc = ectx(f, vars);
    let p: Vec3 = [0, 0, 0];
    let r: Vec3 = [0, 0, 0];
    let s: Vec3 = [1, 1, 1];
    if (layout === "grid") {
      const rows = Math.ceil(n / cols);
      const col = i % cols;
      const row = Math.floor(i / cols);
      p = [(col - (cols - 1) / 2) * spacing[0], ((rows - 1) / 2 - row) * spacing[1], 0];
    } else if (layout === "circle") {
      const a = (startAngle + (arc >= 359.999 ? (360 * i) / n : n > 1 ? (arc * i) / (n - 1) : 0)) * DEG;
      if (axis === "y") {
        p = [radius * Math.sin(a), 0, radius * Math.cos(a)];
        if (node.face !== "none") r = [0, a / DEG, 0];
      } else if (axis === "x") {
        p = [0, radius * Math.sin(a), radius * Math.cos(a)];
        if (node.face !== "none") r = [-a / DEG, 0, 0];
      } else {
        p = [radius * Math.sin(a), radius * Math.cos(a), 0];
        if (node.face !== "none") r = [0, 0, -a / DEG];
      }
    } else if (layout === "random") {
      p = [(rand01(seed, i * 3) - 0.5) * area[0], (rand01(seed, i * 3 + 1) - 0.5) * area[1], (rand01(seed, i * 3 + 2) - 0.5) * area[2]];
    } else {
      p = [(i - (n - 1) / 2) * spacing[0], (i - (n - 1) / 2) * spacing[1], (i - (n - 1) / 2) * spacing[2]];
    }
    if (step) {
      const sp = evalVec(step.position, [0, 0, 0], cc);
      const sr = evalVec(step.rotation, [0, 0, 0], cc);
      const ss = evalVec(step.scale, [1, 1, 1], cc, true);
      p = [p[0] + sp[0] * i, p[1] + sp[1] * i, p[2] + sp[2] * i];
      r = [r[0] + sr[0] * i, r[1] + sr[1] * i, r[2] + sr[2] * i];
      s = [s[0] * ss[0] ** i, s[1] * ss[1] ** i, s[2] * ss[2] ** i];
    }
    if (jit) {
      const jp = evalVec(jit.position, [0, 0, 0], cc);
      const jr = evalVec(jit.rotation, [0, 0, 0], cc);
      const js = evalNum(jit.scale, 0, cc);
      const q = (k: number) => rand01(seed ^ 0x9e37, i * 8 + k) * 2 - 1;
      p = [p[0] + jp[0] * q(0), p[1] + jp[1] * q(1), p[2] + jp[2] * q(2)];
      r = [r[0] + jr[0] * q(3), r[1] + jr[1] * q(4), r[2] + jr[2] * q(5)];
      const k = 1 + js * q(6);
      s = [s[0] * k, s[1] * k, s[2] * k];
    }
    const m = mul(me.m, compose(p, r, s));
    const tint = colors.length ? colors[i % colors.length] : me.tint;
    const inst: Inherit = { ...vars, m, tint };
    for (const kid of kids) visit(kid, f, inst);
  }
}

/* ---------- сетки ---------- */

function meshFor(node: Node, type: string): Mesh | null {
  const n = (k: string, d: number) => Math.abs(numOr(node[k], d));
  switch (type) {
    case "box": {
      const s = node.size;
      const v = Array.isArray(s) ? [numOr(s[0], 200), numOr(s[1] ?? s[0], 200), numOr(s[2] ?? s[0], 200)] : [n("size", 200), n("size", 200), n("size", 200)];
      return cachedMesh(`box|${v.join(",")}`, () => boxMesh(v[0], v[1], v[2]));
    }
    case "plane": {
      const s = node.size;
      const w = Array.isArray(s) ? numOr(s[0], 400) : n("size", 400);
      const h = Array.isArray(s) ? numOr(s[1] ?? s[0], 400) : n("size", 400);
      const sg = node.segments;
      const sx = Array.isArray(sg) ? numOr(sg[0], 1) : numOr(sg, 1);
      const sy = Array.isArray(sg) ? numOr(sg[1] ?? sg[0], 1) : numOr(sg, 1);
      return cachedMesh(`plane|${w},${h},${sx},${sy}`, () => planeMesh(w, h, sx, sy));
    }
    case "sphere": {
      const r = n("radius", 120);
      const seg = numOr(node.segments, 32);
      const rings = numOr(node.rings, Math.max(4, Math.round(seg * 0.66)));
      return cachedMesh(`sphere|${r},${seg},${rings}`, () => sphereMesh(r, seg, rings));
    }
    case "icosphere": {
      const r = n("radius", 120);
      const d = numOr(node.detail, 1);
      return cachedMesh(`ico|${r},${d}`, () => icosphereMesh(r, d));
    }
    case "cylinder": {
      const r = n("radius", 100);
      const rt = numOr(node.radiusTop, r);
      const rb = numOr(node.radiusBottom, r);
      const h = n("height", 200);
      const seg = numOr(node.segments, 24);
      const caps = node.caps !== false;
      return cachedMesh(`cyl|${rt},${rb},${h},${seg},${caps}`, () => cylinderMesh(Math.abs(rt), Math.abs(rb), h, seg, caps));
    }
    case "cone": {
      const r = n("radius", 100);
      const h = n("height", 200);
      const seg = numOr(node.segments, 24);
      return cachedMesh(`cone|${r},${h},${seg}`, () => cylinderMesh(0, r, h, seg, true));
    }
    case "torus": {
      const R = n("radius", 150);
      const r = n("tube", 45);
      const seg = numOr(node.segments, 32);
      const tb = numOr(node.tubeSegments, 12);
      return cachedMesh(`torus|${R},${r},${seg},${tb}`, () => torusMesh(R, r, seg, tb));
    }
    case "lathe": {
      const pts = pts2(node.points);
      const seg = numOr(node.segments, 24);
      return cachedMesh(`lathe|${JSON.stringify(pts)},${seg}`, () => latheMesh(pts, seg));
    }
    case "extrude": {
      const depth = n("depth", 60);
      const ring = outlineOf(node);
      if (!ring || ring.length < 3) return null;
      return cachedMesh(`extrude|${depth}|${JSON.stringify(ring)}`, () => extrudeMesh(ring, depth));
    }
    default:
      return null;
  }
}

function pts2(raw: unknown): Pt2[] {
  return (Array.isArray(raw) ? raw : []).filter((p) => Array.isArray(p) && fin(p[0]) && fin(p[1])).map((p) => [p[0], p[1]] as Pt2);
}

const outlineCache = new WeakMap<object, Pt2[][]>();

/** Контуры плоской фигуры/выдавливания: points, d (SVG-путь) или готовая фигура shape. */
function ringsOf(node: Node, type: string): Pt2[][] {
  const hit = outlineCache.get(node);
  if (hit) return hit;
  let rings: Pt2[][] = [];
  if (typeof node.d === "string") rings = parsePath(node.d, node.center !== false).rings;
  else if (Array.isArray(node.points) && type !== "star") rings = [pts2(node.points)];
  else {
    const kind = type === "rect" ? (fin(node.cornerRadius) && node.cornerRadius > 0 ? "roundrect" : "rect") : type === "circle" ? "circle" : type === "polygon" ? "polygon" : type === "star" ? "star" : String(node.shape ?? "rect");
    const p: Record<string, unknown> = { ...node };
    if (type === "circle" && !Array.isArray(node.size)) p.radius = numOr(node.radius, 100);
    rings = [presetShape(kind, p)];
  }
  rings = rings.filter((r) => r.length >= 2);
  outlineCache.set(node, rings);
  return rings;
}

function outlineOf(node: Node): Pt2[] | null {
  const rings = ringsOf(node, "shape");
  return rings[0] ?? null;
}

function pushItem(f: Frame, inh: Inherit, depth: number, item: PolyItem | FnItem): void {
  const it = item as Item;
  it.order = inh.order;
  it.depth = inh.group ?? depth;
  it.sub = inh.group !== null ? depth : 0;
  it.seq = f.seq++;
  f.items.push(it);
}

/** Сетки, у которых свет по умолчанию мягкий (сглаженные нормали): шар, тор, цилиндр, конус, lathe. */
const SMOOTH_TYPES = new Set(["sphere", "torus", "cylinder", "cone", "lathe"]);
const cornerOffsetCache = new WeakMap<Mesh, Int32Array>();

function cornerOffsets(mesh: Mesh): Int32Array {
  let o = cornerOffsetCache.get(mesh);
  if (!o) {
    o = new Int32Array(mesh.faces.length);
    let k = 0;
    mesh.faces.forEach((face, i) => {
      o![i] = k;
      k += face.length;
    });
    cornerOffsetCache.set(mesh, o);
  }
  return o;
}

const lum = (c: RGBA) => 0.3 * c[0] + 0.59 * c[1] + 0.11 * c[2];

/** Мягкая заливка грани: линейный градиент вдоль роста яркости по углам (≈ Гуро). */
function smoothFill(f: Frame, ring: number[], colors: RGBA[]): string | CanvasGradient {
  const n = Math.min(ring.length / 2, colors.length);
  const avg: RGBA = [0, 0, 0, 0];
  for (let k = 0; k < n; k++) for (let j = 0; j < 4; j++) avg[j] += colors[k][j] / n;
  if (n < 3) return rgbStr(avg);
  const x0 = ring[0];
  const y0 = ring[1];
  const e1x = ring[2] - x0;
  const e1y = ring[3] - y0;
  const e2x = ring[4] - x0;
  const e2y = ring[5] - y0;
  const d1 = lum(colors[1]) - lum(colors[0]);
  const d2 = lum(colors[2]) - lum(colors[0]);
  const det = e1x * e2y - e1y * e2x;
  if (Math.abs(det) < 1e-9 || (Math.abs(d1) < 0.6 && Math.abs(d2) < 0.6)) return rgbStr(avg);
  // Градиент яркости в экране: решение [e1; e2]·g = [d1; d2].
  let gx = (d1 * e2y - d2 * e1y) / det;
  let gy = (e1x * d2 - e2x * d1) / det;
  const gl = Math.hypot(gx, gy);
  if (gl < 1e-9) return rgbStr(avg);
  gx /= gl;
  gy /= gl;
  const ts: { t: number; c: RGBA }[] = [];
  for (let k = 0; k < n; k++) ts.push({ t: (ring[k * 2] - x0) * gx + (ring[k * 2 + 1] - y0) * gy, c: colors[k] });
  ts.sort((a, b) => a.t - b.t);
  const t0 = ts[0].t;
  const t1 = ts[ts.length - 1].t;
  if (t1 - t0 < 0.5) return rgbStr(avg);
  const g = f.ctx.createLinearGradient(x0 + gx * t0, y0 + gy * t0, x0 + gx * t1, y0 + gy * t1);
  for (const s of ts) g.addColorStop(clamp01((s.t - t0) / (t1 - t0)), rgbStr(s.c));
  return g;
}

function drawMesh(node: Node, type: string, f: Frame, inh: Inherit, c: EvalCtx): void {
  const mesh = meshFor(node, type);
  if (!mesh || !mesh.faces.length) return;
  const mat = resolveMat(inh.mat, c, inh.tint, "toon");
  const alpha = inh.opacity * mat.opacity;
  if (alpha <= 0.002) return;
  const doubleSided = mat.doubleSided ?? type === "plane";
  const smooth = (mat.smooth ?? SMOOTH_TYPES.has(type)) && mat.shading !== "flat";
  const cn = smooth ? smoothNormals(mesh, 60) : null;
  const co = smooth ? cornerOffsets(mesh) : null;
  const mv = mul(f.cam.view, inh.m);
  const M = inh.m;
  const nm = normalMatrix(M);
  const P = mesh.pos;
  const nv = P.length / 3;
  // Вершины: в мире (свет), в пространстве камеры (отсечение, глубина) и их проекции.
  const world = new Float64Array(nv * 3);
  const cam = new Float64Array(nv * 3);
  const scr = new Float64Array(nv * 2);
  const ok = new Uint8Array(nv);
  for (let i = 0; i < nv; i++) {
    const x = P[i * 3];
    const y = P[i * 3 + 1];
    const z = P[i * 3 + 2];
    world[i * 3] = M[0] * x + M[1] * y + M[2] * z + M[3];
    world[i * 3 + 1] = M[4] * x + M[5] * y + M[6] * z + M[7];
    world[i * 3 + 2] = M[8] * x + M[9] * y + M[10] * z + M[11];
    const cx = mv[0] * x + mv[1] * y + mv[2] * z + mv[3];
    const cy = mv[4] * x + mv[5] * y + mv[6] * z + mv[7];
    const cz = mv[8] * x + mv[9] * y + mv[10] * z + mv[11];
    cam[i * 3] = cx;
    cam[i * 3 + 1] = cy;
    cam[i * 3 + 2] = cz;
    const p = project(f, cx, cy, cz);
    if (p) {
      scr[i * 2] = p[0];
      scr[i * 2 + 1] = p[1];
      ok[i] = 1;
    }
  }
  const nf = mesh.faces.length;
  f.stats.faces += nf;
  // Лицевая ли грань (для отсечения и силуэта) и мировые нормали.
  const front = new Uint8Array(nf);
  const wn = new Float64Array(nf * 3);
  const V = f.cam.view;
  for (let fi = 0; fi < nf; fi++) {
    const ox = mesh.normals[fi * 3];
    const oy = mesh.normals[fi * 3 + 1];
    const oz = mesh.normals[fi * 3 + 2];
    let nx = nm[0] * ox + nm[1] * oy + nm[2] * oz;
    let ny = nm[3] * ox + nm[4] * oy + nm[5] * oz;
    let nz = nm[6] * ox + nm[7] * oy + nm[8] * oz;
    const l = Math.hypot(nx, ny, nz) || 1;
    nx /= l;
    ny /= l;
    nz /= l;
    wn[fi * 3] = nx;
    wn[fi * 3 + 1] = ny;
    wn[fi * 3 + 2] = nz;
    // Нормаль в пространстве камеры (вид — жёсткое движение).
    const cnx = V[0] * nx + V[1] * ny + V[2] * nz;
    const cny = V[4] * nx + V[5] * ny + V[6] * nz;
    const cnz = V[8] * nx + V[9] * ny + V[10] * nz;
    if (f.cam.type === "orthographic") front[fi] = cnz > 1e-9 ? 1 : 0;
    else {
      const v0 = mesh.faces[fi][0] * 3;
      front[fi] = cnx * cam[v0] + cny * cam[v0 + 1] + cnz * cam[v0 + 2] < 0 ? 1 : 0;
    }
  }
  const outline = mat.outline;
  const creaseCos = outline ? Math.cos(outline.crease * DEG) : 1;
  const ink: Ink | null = outline ? { color: rgbaStr(outline.color), width: outline.width } : null;
  const patColor = mat.pattern ? mat.pattern.color ?? scaleRGB(mat.color, 0.32) : null;
  const ths = smooth && banded(mat.shading) ? bandThresholds(mat) : null;
  const specCut = mat.specular ? 1 - mat.specular.size : 2;
  const halfW = f.w / 2 + 50;
  const halfH = f.h / 2 + 50;
  for (let fi = 0; fi < nf; fi++) {
    const isFront = front[fi] === 1;
    if (!isFront && !doubleSided) {
      f.stats.culled++;
      continue;
    }
    const face = mesh.faces[fi];
    // Глубина — центр грани в пространстве камеры.
    let zc = 0;
    let allIn = true;
    for (const v of face) {
      zc += cam[v * 3 + 2];
      if (!ok[v]) allIn = false;
    }
    zc /= face.length;
    if (-zc > f.far) continue;
    let ring: number[];
    if (allIn) {
      ring = new Array(face.length * 2);
      for (let k = 0; k < face.length; k++) {
        ring[k * 2] = scr[face[k] * 2];
        ring[k * 2 + 1] = scr[face[k] * 2 + 1];
      }
    } else {
      const pts: number[] = [];
      for (const v of face) pts.push(cam[v * 3], cam[v * 3 + 1], cam[v * 3 + 2]);
      const clipped = clipNear(pts, f.near);
      if (clipped.length < 9) continue;
      ring = [];
      for (let k = 0; k < clipped.length; k += 3) {
        const p = project(f, clipped[k], clipped[k + 1], clipped[k + 2]);
        if (!p) continue;
        ring.push(p[0], p[1]);
      }
      if (ring.length < 6) continue;
    }
    // Вне вьюпорта целиком — не рисуем.
    let minX = Infinity;
    let maxX = -Infinity;
    let minY = Infinity;
    let maxY = -Infinity;
    for (let k = 0; k < ring.length; k += 2) {
      if (ring[k] < minX) minX = ring[k];
      if (ring[k] > maxX) maxX = ring[k];
      if (ring[k + 1] < minY) minY = ring[k + 1];
      if (ring[k + 1] > maxY) maxY = ring[k + 1];
    }
    if (maxX < -halfW || minX > halfW || maxY < -halfH || minY > halfH) continue;
    const flip = isFront ? 1 : -1;
    const N: Vec3 = [wn[fi * 3] * flip, wn[fi * 3 + 1] * flip, wn[fi * 3 + 2] * flip];
    let wx = 0;
    let wy = 0;
    let wz = 0;
    for (const v of face) {
      wx += world[v * 3];
      wy += world[v * 3 + 1];
      wz += world[v * 3 + 2];
    }
    const Pw: Vec3 = [wx / face.length, wy / face.length, wz / face.length];
    const depth = -zc;
    let fill: string | CanvasGradient | null = null;
    let level = 0;
    let parts: Part[] | null = null;
    let faceAlpha = 1;
    if (cn && co && allIn) {
      // Мягкий свет: поля в углах (доля света, блик, rim) по сглаженным нормалям.
      const F = 3;
      const vals: number[] = new Array(face.length * F);
      const litC = lightAt(f, N, Pw);
      for (let k = 0; k < face.length; k++) {
        const o = (co[fi] + k) * 3;
        const ox = cn[o];
        const oy = cn[o + 1];
        const oz = cn[o + 2];
        const Nc = norm([(nm[0] * ox + nm[1] * oy + nm[2] * oz) * flip, (nm[3] * ox + nm[4] * oy + nm[5] * oz) * flip, (nm[6] * ox + nm[7] * oy + nm[8] * oz) * flip]);
        const v = face[k];
        const Pc: Vec3 = [world[v * 3], world[v * 3 + 1], world[v * 3 + 2]];
        const lit = lightAt(f, Nc, Pc);
        vals[k * F] = lit.d;
        vals[k * F + 1] = mat.specular ? specDot(f, Nc, Pc, lit.key) : -1;
        vals[k * F + 2] = rimTerm(f, mat, Nc, Pc);
      }
      if (ths) {
        // Полосы toon/halftone: грань режется по порогам — граница света ровная, не «ступеньками».
        let bands = splitBands(ring, vals, F, 0, ths);
        if (mat.specular) bands = bands.flatMap((b) => splitBands(b.pts, b.vals, F, 1, [specCut]).map((s) => ({ ...s, band: b.band })));
        if (mat.rim) bands = bands.flatMap((b) => splitBands(b.pts, b.vals, F, 2, [RIM_CUT]).map((s) => ({ ...s, band: b.band })));
        parts = bands.map((b) => {
          const m = b.vals.length / F;
          let d = 0;
          let s = 0;
          let r = 0;
          for (let k = 0; k < m; k++) {
            d += b.vals[k * F];
            s += b.vals[k * F + 1];
            r += b.vals[k * F + 2];
          }
          const mc = modelColor(mat, litC, d / m);
          const col = finishColor(f, mat, mc.c, s / m, r / m, depth);
          faceAlpha = col[3];
          return { ring: b.pts, fill: rgbStr(col), pattern: mat.pattern && patColor ? patternFor(f, mat.pattern.kind, mc.level, mat.pattern.size, patColor) : null };
        });
        if (parts.length === 1 && !parts[0].pattern) {
          fill = parts[0].fill;
          parts = null;
        }
      } else {
        const colors: RGBA[] = [];
        for (let k = 0; k < face.length; k++) {
          const mc = modelColor(mat, { ...litC, d: vals[k * F] }, vals[k * F]);
          colors.push(finishColor(f, mat, mc.c, vals[k * F + 1], vals[k * F + 2], depth));
        }
        faceAlpha = colors[0][3];
        fill = smoothFill(f, ring, colors);
      }
    } else {
      const sh = shadeFace(f, mat, N, Pw, depth);
      fill = rgbStr(sh.c);
      level = sh.level;
      faceAlpha = sh.c[3];
    }
    let edges: number[] | null = null;
    if (ink) {
      for (const e of mesh.faceEdges[fi]) {
        const f1 = mesh.edges[e * 4 + 2];
        const f2 = mesh.edges[e * 4 + 3];
        const other = f1 === fi ? f2 : f1;
        let draw = other < 0 || front[other] !== front[fi];
        if (!draw) {
          const d = wn[fi * 3] * wn[other * 3] + wn[fi * 3 + 1] * wn[other * 3 + 1] + wn[fi * 3 + 2] * wn[other * 3 + 2];
          draw = d < creaseCos;
        }
        if (!draw) continue;
        const a = mesh.edges[e * 4];
        const b = mesh.edges[e * 4 + 1];
        if (!ok[a] || !ok[b]) continue;
        (edges ??= []).push(scr[a * 2], scr[a * 2 + 1], scr[b * 2], scr[b * 2 + 1]);
      }
    }
    const a = alpha * faceAlpha;
    pushItem(f, inh, zc, {
      k: "poly",
      rings: [ring],
      parts,
      fill: parts ? null : fill,
      alpha: a,
      seam: mat.seams && a >= 0.999,
      pattern: !parts && level > 0 && mat.pattern && patColor ? patternFor(f, mat.pattern.kind, level, mat.pattern.size, patColor) : null,
      ink,
      edges,
      stroke: null,
      evenOdd: false,
    });
    f.stats.drawn++;
  }
}

/* ---------- плоские фигуры ---------- */

/**
 * Текст и плоские фигуры по умолчанию без света (flat) — даже если style сцены задаёт toon для
 * сеток: титр должен быть своего цвета. Свет — только своим material.shading узла.
 */
function ownShading(node: Node, inherited: Record<string, unknown> | null): Record<string, unknown> {
  const own = isObj(node.material) && typeof node.material.shading === "string";
  return own ? inherited || {} : { ...(inherited || {}), shading: "flat" };
}

function fillStyleFor(f: Frame, raw: unknown, c: EvalCtx, ringsLocal: Pt2[][], m: Mat): { fill: string | CanvasGradient | null; color: RGBA | null } {
  if (raw === null || raw === "none") return { fill: null, color: null };
  if (isObj(raw) && Array.isArray(raw.gradient)) {
    const stops = raw.gradient.map((s) => evalColor(s, WHITE, c));
    if (stops.length < 2) return { fill: stops.length ? rgbaStr(stops[0]) : null, color: stops[0] ?? null };
    let minX = Infinity;
    let maxX = -Infinity;
    let minY = Infinity;
    let maxY = -Infinity;
    for (const r of ringsLocal)
      for (const [x, y] of r) {
        minX = Math.min(minX, x);
        maxX = Math.max(maxX, x);
        minY = Math.min(minY, y);
        maxY = Math.max(maxY, y);
      }
    const a = numOr(raw.angle, 90) * DEG;
    const cx = (minX + maxX) / 2;
    const cy = (minY + maxY) / 2;
    const half = (Math.abs(Math.cos(a)) * (maxX - minX) + Math.abs(Math.sin(a)) * (maxY - minY)) / 2;
    const p0 = apply(mul(f.cam.view, m), cx - Math.cos(a) * half, cy - Math.sin(a) * half, 0);
    const p1 = apply(mul(f.cam.view, m), cx + Math.cos(a) * half, cy + Math.sin(a) * half, 0);
    const s0 = project(f, p0[0], p0[1], p0[2]);
    const s1 = project(f, p1[0], p1[1], p1[2]);
    if (!s0 || !s1) return { fill: rgbaStr(stops[0]), color: stops[0] };
    const g = f.ctx.createLinearGradient(s0[0], s0[1], s1[0], s1[1]);
    stops.forEach((s, i) => g.addColorStop(i / (stops.length - 1), rgbaStr(s)));
    return { fill: g, color: stops[0] };
  }
  const col = evalColor(raw, [255, 255, 255, 1], c);
  return { fill: null, color: col };
}

function drawFlat(node: Node, type: string, f: Frame, inh: Inherit, c: EvalCtx): void {
  const rings = ringsOf(node, type);
  if (!rings.length) return;
  const open = type === "path" && node.closed === false;
  const mv = mul(f.cam.view, inh.m);
  const nm = normalMatrix(inh.m);
  let N = norm([nm[2], nm[5], nm[8]]);
  const cn = [f.cam.view[0] * N[0] + f.cam.view[1] * N[1] + f.cam.view[2] * N[2], f.cam.view[4] * N[0] + f.cam.view[5] * N[1] + f.cam.view[6] * N[2], f.cam.view[8] * N[0] + f.cam.view[9] * N[1] + f.cam.view[10] * N[2]];
  const o = apply(mv, 0, 0, 0);
  const isFront = f.cam.type === "orthographic" ? cn[2] > 0 : cn[0] * o[0] + cn[1] * o[1] + cn[2] * o[2] < 0;
  const mat = resolveMat(ownShading(node, inh.mat), c, inh.tint, "flat");
  const doubleSided = mat.doubleSided ?? true;
  if (!isFront && !doubleSided) return;
  if (!isFront) N = [-N[0], -N[1], -N[2]];
  const fs = fillStyleFor(f, node.fill !== undefined ? node.fill : null, c, rings, inh.m);
  // Цвета repeat (tint) перекрывают и сплошную заливку — иначе «colors» не видны у копий.
  let fillColor: RGBA | null = node.fill === undefined ? mat.color : fs.fill ? null : inh.tint ?? fs.color;
  if (node.fill === null || node.fill === "none") fillColor = null;
  const out: number[][] = [];
  let zsum = 0;
  let zn = 0;
  for (const r of rings) {
    const pts: number[] = [];
    for (const [x, y] of r) {
      const q = apply(mv, x, y, 0);
      pts.push(q[0], q[1], q[2]);
      zsum += q[2];
      zn++;
    }
    const clipped = pts.some((_, k) => k % 3 === 2 && pts[k] > -f.near) && f.cam.type !== "orthographic" ? clipNear(pts, f.near) : pts;
    const ring: number[] = [];
    for (let k = 0; k < clipped.length; k += 3) {
      const p = project(f, clipped[k], clipped[k + 1], clipped[k + 2]);
      if (p) ring.push(p[0], p[1]);
    }
    if (ring.length >= 4) out.push(ring);
  }
  if (!out.length) return;
  const zc = zsum / Math.max(1, zn);
  const alpha = inh.opacity * mat.opacity;
  let fill: string | CanvasGradient | null = fs.fill;
  let level = 0;
  if (!fill && fillColor && !open) {
    const sh = shadeFace(f, { ...mat, color: fillColor }, N, apply(inh.m, 0, 0, 0), -zc);
    fill = rgbStr(sh.c);
    level = sh.level;
    if (sh.c[3] < 1) fill = rgbaStr(sh.c);
  }
  const st = isObj(node.stroke) ? node.stroke : null;
  const stroke: Ink | null = st ? { color: rgbaStr(evalColor(st.color, BLACK, c)), width: Math.max(0, evalNum(st.width, 4, c)) } : mat.outline ? { color: rgbaStr(mat.outline.color), width: mat.outline.width } : null;
  if (!fill && !stroke) return;
  f.stats.faces++;
  f.stats.drawn++;
  pushItem(f, inh, zc, {
    k: "poly",
    rings: out,
    fill: open ? null : fill,
    alpha,
    seam: false,
    pattern: mat.pattern && fillColor ? patternFor(f, mat.pattern.kind, level, mat.pattern.size, mat.pattern.color ?? scaleRGB(fillColor, 0.32)) : null,
    ink: null,
    edges: null,
    stroke: stroke && stroke.width > 0 ? stroke : null,
    evenOdd: rings.length > 1,
    ...(open ? { open: true } : {}),
  } as PolyItem);
}

/* ---------- текст ---------- */

type Glyph = { text: string; x: number; y: number; w: number };
const layoutCache = new Map<string, { glyphs: Glyph[]; w: number; h: number }>();

/** Раскладка строк/букв/слов: центр блока в (0, 0), y вверх (строки идут вниз). */
function layoutText(ctx: Ctx2D, text: string, font: string, size: number, lineHeight: number, letterSpacing: number, align: string, split: string): { glyphs: Glyph[]; w: number; h: number } {
  const key = `${font}|${size}|${lineHeight}|${letterSpacing}|${align}|${split}|${text}`;
  const hit = layoutCache.get(key);
  if (hit) return hit;
  ctx.save();
  ctx.font = font;
  const lines = text.split("\n");
  const lh = size * lineHeight;
  const glyphs: Glyph[] = [];
  let maxW = 0;
  const widths = lines.map((line) => {
    const chars = Array.from(line);
    const w = ctx.measureText(line).width + letterSpacing * Math.max(0, chars.length - 1);
    maxW = Math.max(maxW, w);
    return w;
  });
  lines.forEach((line, li) => {
    const lw = widths[li];
    const x0 = align === "left" ? -maxW / 2 : align === "right" ? maxW / 2 - lw : -lw / 2;
    const y = ((lines.length - 1) / 2 - li) * lh;
    if (split === "letters") {
      const chars = Array.from(line);
      let acc = "";
      chars.forEach((ch, k) => {
        const left = ctx.measureText(acc).width + letterSpacing * k;
        const cw = ctx.measureText(ch).width;
        acc += ch;
        if (ch.trim()) glyphs.push({ text: ch, x: x0 + left + cw / 2, y, w: cw });
      });
    } else if (split === "words") {
      const parts = line.split(/(\s+)/);
      let acc = "";
      for (const part of parts) {
        const left = ctx.measureText(acc).width + letterSpacing * Array.from(acc).length;
        const cw = ctx.measureText(part).width + letterSpacing * Math.max(0, Array.from(part).length - 1);
        acc += part;
        if (part.trim()) glyphs.push({ text: part, x: x0 + left + cw / 2, y, w: cw });
      }
    } else glyphs.push({ text: line, x: x0 + lw / 2, y, w: lw });
  });
  ctx.restore();
  const out = { glyphs, w: maxW, h: lh * lines.length };
  if (layoutCache.size > 512) layoutCache.clear();
  layoutCache.set(key, out);
  return out;
}

/** Аффинная матрица плоскости XY узла в точке (ox, oy, oz) — для текста и картинок. */
function planeAffine(f: Frame, mv: Mat, ox: number, oy: number, oz: number, h: number): [number, number, number, number, number, number] | null {
  const p0c = apply(mv, ox, oy, oz);
  const p0 = project(f, p0c[0], p0c[1], p0c[2]);
  const pxc = apply(mv, ox + h, oy, oz);
  const pyc = apply(mv, ox, oy + h, oz);
  const px = project(f, pxc[0], pxc[1], pxc[2]);
  const py = project(f, pyc[0], pyc[1], pyc[2]);
  if (!p0 || !px || !py) return null;
  // Холст: u вправо, v вниз; плоскость узла: x вправо, y вверх → v = −y.
  return [(px[0] - p0[0]) / h, (px[1] - p0[1]) / h, -(py[0] - p0[0]) / h, -(py[1] - p0[1]) / h, p0[0], p0[1]];
}

const splitCache = new WeakMap<object, { key: string; kids: Node[] }>();

function drawText(node: Node, type: string, f: Frame, inh: Inherit, c: EvalCtx): void {
  let text = typeof node.text === "string" ? node.text.slice(0, SCENE_LIMITS.maxTextLen) : "";
  if (node.uppercase === true) text = text.toUpperCase();
  if (!text.trim()) return;
  const size = Math.max(1, Math.min(2000, numOr(node.size, 120)));
  const font = fontString({ family: typeof node.font === "string" ? node.font : undefined, weight: numOr(node.weight, 900), size, italic: node.italic === true }, f.env.defaultFont);
  const split = node.split === "letters" || node.split === "words" || node.split === "lines" ? (node.split as string) : "none";
  const align = node.align === "left" || node.align === "right" ? node.align : "center";
  const lineHeight = numOr(node.lineHeight, 1.1);
  const ls = numOr(node.letterSpacing, 0);
  if (split !== "none" && !node.__glyph) {
    // Буквы/слова/строки — отдельные узлы-копии: у каждой своё enter/exit со stagger.
    const lay = layoutText(f.ctx, text, font, size, lineHeight, ls, align, split === "lines" ? "none" : split);
    const key = `${font}|${text}|${split}|${align}|${ls}|${lineHeight}`;
    let cached = splitCache.get(node);
    if (!cached || cached.key !== key) {
      const { split: _s, stagger: _st, children: _c, position: _p, rotation: _r, scale: _sc, pivot: _pv, follow: _f, time: _t, delay: _d, start: _a, end: _b, opacity: _o, ...rest } = node;
      void _s; void _st; void _c; void _p; void _r; void _sc; void _pv; void _f; void _t; void _d; void _a; void _b; void _o;
      cached = {
        key,
        kids: lay.glyphs.map((g) => ({ ...rest, type, text: g.text, align: "center", position: [g.x, g.y, 0], __glyph: true, __pos: true })),
      };
      splitCache.set(node, cached);
    }
    const st = fin(node.stagger) ? node.stagger : 0.05;
    const order = staggerOrder(cached.kids.length, node.staggerFrom, f.seed ^ 0x7e57);
    const n = cached.kids.length;
    cached.kids.forEach((kid, k) => {
      // Переменные i/n в выражениях буквы — её номер; время — со сдвигом stagger.
      visitGlyph(kid, f, { ...inh, i: k, n, t: inh.t - order[k] * st, mat: inh.mat });
    });
    return;
  }
  const mat = resolveMat(ownShading(node, inh.mat), c, inh.tint, "flat");
  const alpha = inh.opacity * mat.opacity;
  if (alpha <= 0.002) return;
  const depth = Math.max(0, numOr(node.depth, type === "text3d" ? 40 : 0));
  const mv = mul(f.cam.view, inh.m);
  const lay = layoutText(f.ctx, text, font, size, lineHeight, ls, align, "none");
  const front = planeAffine(f, mv, 0, 0, 0, size * 0.5);
  if (!front) return;
  const det = front[0] * front[3] - front[1] * front[2];
  const doubleSided = mat.doubleSided ?? false;
  if (det <= 0 && !doubleSided) return;
  const steps = depth > 0 ? Math.max(2, Math.min(numOr(node.slices, 14), Math.ceil(depth / 3))) : 0;
  const slices: [number, number, number, number, number, number][] = [];
  for (let s = steps; s >= 1; s--) {
    const A = planeAffine(f, mv, 0, 0, (-depth * s) / steps, size * 0.5);
    if (A) slices.push(A);
  }
  const oc = apply(mv, 0, 0, -depth / 2);
  const fogDepth = -oc[2];
  // Цвет лица: материал с освещением по нормали плоскости (flat — как задан).
  const nm = normalMatrix(inh.m);
  let N = norm([nm[2], nm[5], nm[8]]);
  if (det <= 0) N = [-N[0], -N[1], -N[2]];
  const faceC = shadeFace(f, mat, N, apply(inh.m, 0, 0, 0), fogDepth).c;
  const sideBase = node.sideColor !== undefined ? evalColor(node.sideColor, scaleRGB(mat.color, 0.55), c) : scaleRGB(faceC, 0.55);
  const sideC = f.fog && mat.fog ? fogged(f, sideBase, fogDepth) : sideBase;
  const stRaw = isObj(node.stroke) ? node.stroke : null;
  const stroke = stRaw ? { color: rgbaStr(evalColor(stRaw.color, BLACK, c)), width: Math.max(0, evalNum(stRaw.width, 6, c)) } : null;
  const ink = mat.outline ? { color: rgbaStr(mat.outline.color), width: mat.outline.width } : null;
  const glyphs = lay.glyphs;
  f.stats.faces++;
  f.stats.drawn++;
  pushItem(f, inh, oc[2], {
    k: "fn",
    draw: (ctx) => {
      ctx.save();
      const base = ctx.globalAlpha;
      ctx.globalAlpha = base * alpha * faceC[3];
      ctx.font = font;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.lineJoin = "round";
      ctx.miterLimit = 2;
      const put = (A: number[], fn: (g: Glyph) => void) => {
        ctx.save();
        ctx.transform(A[0], A[1], A[2], A[3], A[4], A[5]);
        for (const g of glyphs) fn(g);
        ctx.restore();
      };
      // Пиксель холста в единицах слоя — обводка и швы задаются в px слоя.
      if (ink && slices.length) {
        // Силуэт всего тела: толстый штрих под срезами.
        ctx.strokeStyle = ink.color;
        for (const A of slices) {
          const k = Math.sqrt(Math.abs(A[0] * A[3] - A[1] * A[2])) || 1;
          ctx.lineWidth = (ink.width * 2) / k;
          put(A, (g) => ctx.strokeText(g.text, g.x, -g.y));
        }
      }
      slices.forEach((A, si) => {
        const shadeK = 0.75 + (0.25 * (si + 1)) / slices.length;
        ctx.fillStyle = rgbStr(scaleRGB(sideC, shadeK));
        put(A, (g) => ctx.fillText(g.text, g.x, -g.y));
      });
      // Обводки лица — ДО заливки (половина ширины уходит под неё): у шрифтов с
      // перекрывающимися контурами штрих поверх заливки рисует линии внутри букв.
      const k = Math.sqrt(Math.abs(det)) || 1;
      const strokeW = (stroke && stroke.width > 0 ? stroke.width : 0) + (ink ? (slices.length ? ink.width * 0.6 : ink.width) : 0);
      if (ink && strokeW > 0) {
        ctx.strokeStyle = ink.color;
        ctx.lineWidth = (strokeW * 2) / k;
        put(front, (g) => ctx.strokeText(g.text, g.x, -g.y));
      }
      if (stroke && stroke.width > 0) {
        ctx.strokeStyle = stroke.color;
        ctx.lineWidth = (stroke.width * 2) / k;
        put(front, (g) => ctx.strokeText(g.text, g.x, -g.y));
      }
      ctx.fillStyle = rgbStr(faceC);
      put(front, (g) => ctx.fillText(g.text, g.x, -g.y));
      ctx.globalAlpha = base;
      ctx.restore();
    },
  });
}

function visitGlyph(kid: Node, f: Frame, inh: Inherit): void {
  visit(kid, f, inh);
}

/* ---------- картинки ---------- */

function drawImageNode(node: Node, f: Frame, inh: Inherit, c: EvalCtx): void {
  const ref = String(node.src ?? node.asset_id ?? "");
  if (!ref) return;
  const img = f.env.getImage(ref);
  if (!img || !img.width || !img.height) return;
  const mat = resolveMat(inh.mat, c, null, "flat");
  const alpha = inh.opacity * mat.opacity;
  if (alpha <= 0.002) return;
  const size = Array.isArray(node.size) ? node.size : null;
  const w = size ? numOr(size[0], 400) : numOr(node.width, 400);
  const h = size ? numOr(size[1], (w * img.height) / img.width) : (w * img.height) / img.width;
  const mv = mul(f.cam.view, inh.m);
  const corners: ([number, number] | null)[] = [
    [-w / 2, h / 2],
    [w / 2, h / 2],
    [w / 2, -h / 2],
    [-w / 2, -h / 2],
  ].map(([x, y]) => {
    const q = apply(mv, x, y, 0);
    return project(f, q[0], q[1], q[2]);
  });
  if (corners.some((p) => !p)) return;
  const q = corners as [number, number][];
  const area = (q[1][0] - q[0][0]) * (q[3][1] - q[0][1]) - (q[1][1] - q[0][1]) * (q[3][0] - q[0][0]);
  const doubleSided = mat.doubleSided ?? true;
  if (area <= 0 && !doubleSided) return;
  // Насколько плоскость далека от аффинной: четвёртый угол против предсказанного по трём.
  const err = Math.hypot(q[1][0] + q[3][0] - q[0][0] - q[2][0], q[1][1] + q[3][1] - q[0][1] - q[2][1]);
  const cells = err < 0.75 ? 1 : Math.max(2, Math.min(10, Math.ceil(Math.sqrt(err))));
  const zc = apply(mv, 0, 0, 0)[2];
  const iw = img.width;
  const ih = img.height;
  const grid: ([number, number] | null)[][] = [];
  for (let j = 0; j <= cells; j++) {
    const row: ([number, number] | null)[] = [];
    for (let i = 0; i <= cells; i++) {
      const x = -w / 2 + (w * i) / cells;
      const y = h / 2 - (h * j) / cells;
      const p = apply(mv, x, y, 0);
      row.push(project(f, p[0], p[1], p[2]));
    }
    grid.push(row);
  }
  f.stats.faces++;
  f.stats.drawn++;
  pushItem(f, inh, zc, {
    k: "fn",
    draw: (ctx) => {
      ctx.save();
      ctx.globalAlpha *= alpha;
      const src = img as unknown as CanvasImageSource;
      if (cells === 1) {
        const [p0, p1, , p3] = q;
        ctx.transform((p1[0] - p0[0]) / iw, (p1[1] - p0[1]) / iw, (p3[0] - p0[0]) / ih, (p3[1] - p0[1]) / ih, p0[0], p0[1]);
        ctx.drawImage(src, 0, 0);
      } else {
        const cw = iw / cells;
        const ch = ih / cells;
        for (let j = 0; j < cells; j++)
          for (let i = 0; i < cells; i++) {
            const a = grid[j][i];
            const b = grid[j][i + 1];
            const d = grid[j + 1][i];
            const e = grid[j + 1][i + 1];
            if (!a || !b || !d || !e) continue;
            // Два треугольника ячейки, каждый своей аффинной (с запасом на шов).
            drawTri(ctx, src, a, b, d, i * cw, j * ch, cw, ch, false);
            drawTri(ctx, src, e, d, b, i * cw, j * ch, cw, ch, true);
          }
      }
      ctx.restore();
    },
  });
}

function drawTri(ctx: Ctx2D, src: CanvasImageSource, p0: [number, number], p1: [number, number], p2: [number, number], sx: number, sy: number, sw: number, sh: number, flip: boolean): void {
  ctx.save();
  const cx = (p0[0] + p1[0] + p2[0]) / 3;
  const cy = (p0[1] + p1[1] + p2[1]) / 3;
  const grow = (p: [number, number]): [number, number] => {
    const dx = p[0] - cx;
    const dy = p[1] - cy;
    const l = Math.hypot(dx, dy) || 1;
    return [p[0] + (dx / l) * 0.6, p[1] + (dy / l) * 0.6];
  };
  const g0 = grow(p0);
  const g1 = grow(p1);
  const g2 = grow(p2);
  ctx.beginPath();
  ctx.moveTo(g0[0], g0[1]);
  ctx.lineTo(g1[0], g1[1]);
  ctx.lineTo(g2[0], g2[1]);
  ctx.closePath();
  ctx.clip();
  // Ячейка источника: (0,0)→p0, (sw,0)→p1, (0,sh)→p2 (у второго треугольника — зеркально).
  if (!flip) ctx.transform((p1[0] - p0[0]) / sw, (p1[1] - p0[1]) / sw, (p2[0] - p0[0]) / sh, (p2[1] - p0[1]) / sh, p0[0], p0[1]);
  else ctx.transform((p0[0] - p1[0]) / sw, (p0[1] - p1[1]) / sw, (p0[0] - p2[0]) / sh, (p0[1] - p2[1]) / sh, p1[0] + p2[0] - p0[0], p1[1] + p2[1] - p0[1]);
  ctx.drawImage(src, sx, sy, sw, sh, 0, 0, sw, sh);
  ctx.restore();
}

/* ---------- линии ---------- */

function drawLine(node: Node, f: Frame, inh: Inherit, c: EvalCtx): void {
  const raw = (Array.isArray(node.points) ? node.points : []).filter((p) => Array.isArray(p)).map((p) => [numOr(p[0], 0), numOr(p[1], 0), numOr(p[2], 0)] as Vec3);
  if (raw.length < 2) return;
  const closed = node.closed === true;
  let pts = raw;
  if (node.smooth === true) {
    const n = Math.min(400, raw.length * 12);
    pts = Array.from({ length: n + 1 }, (_, k) => catmull(raw, closed, k / n));
  } else if (closed) pts = [...raw, raw[0]];
  const mat = resolveMat(inh.mat, c, inh.tint, "flat");
  const color = node.color !== undefined ? evalColor(node.color, WHITE, c) : mat.color;
  const width = Math.max(0.1, evalNum(node.width, 6, c));
  const progress = clamp01(evalNum(node.progress, 1, c));
  const alpha = inh.opacity * mat.opacity;
  if (progress <= 0 || alpha <= 0.002) return;
  const mv = mul(f.cam.view, inh.m);
  const cam = pts.map((p) => apply(mv, p[0], p[1], p[2]));
  // Доля пути progress по длине в мире.
  const lens = [0];
  for (let k = 1; k < cam.length; k++) lens.push(lens[k - 1] + Math.hypot(cam[k][0] - cam[k - 1][0], cam[k][1] - cam[k - 1][1], cam[k][2] - cam[k - 1][2]));
  const total = lens[lens.length - 1];
  const stop = total * progress;
  const segs: number[][] = [];
  let cur: number[] = [];
  let zs = 0;
  for (let k = 0; k < cam.length; k++) {
    let p = cam[k];
    if (lens[k] > stop) {
      const a = cam[k - 1];
      const q = (stop - lens[k - 1]) / Math.max(1e-9, lens[k] - lens[k - 1]);
      p = [a[0] + (p[0] - a[0]) * q, a[1] + (p[1] - a[1]) * q, a[2] + (p[2] - a[2]) * q];
    }
    const s = project(f, p[0], p[1], p[2]);
    zs += p[2];
    if (s) cur.push(s[0], s[1]);
    else if (cur.length) {
      segs.push(cur);
      cur = [];
    }
    if (lens[k] > stop) break;
  }
  if (cur.length >= 4) segs.push(cur);
  if (!segs.length) return;
  const dash = Array.isArray(node.dash) ? node.dash.filter(fin) : null;
  const col = f.fog && mat.fog ? fogged(f, color, -zs / cam.length) : color;
  f.stats.faces++;
  f.stats.drawn++;
  pushItem(f, inh, zs / cam.length, {
    k: "fn",
    draw: (ctx) => {
      ctx.save();
      ctx.globalAlpha *= alpha * col[3];
      ctx.strokeStyle = rgbStr(col);
      ctx.lineWidth = width;
      ctx.lineCap = "round";
      ctx.lineJoin = "round";
      if (dash && dash.length) ctx.setLineDash(dash);
      ctx.beginPath();
      for (const s of segs) {
        ctx.moveTo(s[0], s[1]);
        for (let k = 2; k < s.length; k += 2) ctx.lineTo(s[k], s[k + 1]);
      }
      ctx.stroke();
      ctx.restore();
    },
  });
}

/* ---------- частицы ---------- */

export const PARTICLE3D_KINDS = ["confetti", "snow", "float", "burst"] as const;

function drawParticles(node: Node, f: Frame, inh: Inherit, c: EvalCtx): void {
  const kind = (PARTICLE3D_KINDS as readonly string[]).includes(String(node.kind)) ? String(node.kind) : "confetti";
  const count = Math.max(0, Math.min(SCENE_LIMITS.maxParticles, Math.round(evalNum(node.count, 80, c))));
  if (!count) return;
  const seed = (hashString(String(node.seed ?? node.id ?? "particles")) ^ f.seed) >>> 0;
  const area = evalVec(node.area, [1000, 1600, 600], c);
  const size = Math.max(1, evalNum(node.size, kind === "confetti" ? 34 : 14, c));
  const speed = evalNum(node.speed, kind === "confetti" ? 320 : kind === "burst" ? 900 : 60, c);
  const spin = evalNum(node.spin, 360, c);
  const colors = (Array.isArray(node.colors) ? node.colors.map(parseColor).filter(Boolean) : []) as RGBA[];
  const palette = colors.length ? colors : [[255, 212, 0, 1], [255, 59, 48, 1], [52, 199, 89, 1], [0, 122, 255, 1], [175, 82, 222, 1], [255, 255, 255, 1]] as RGBA[];
  const shape = node.shape === "dot" || kind === "snow" || kind === "float" ? (node.shape === "quad" ? "quad" : "dot") : "quad";
  const t = c.t;
  const gravity = evalNum(node.gravity, kind === "burst" ? 900 : 0, c);
  const life = Math.max(0.1, evalNum(node.life, 1.6, c));
  const at = evalNum(node.at, 0, c);
  const mat = resolveMat(inh.mat && "shading" in inh.mat ? inh.mat : { ...(inh.mat || {}), shading: "lambert" }, c, null, "lambert");
  const mv = mul(f.cam.view, inh.m);
  for (let i = 0; i < count; i++) {
    const r = (k: number) => rand01(seed, i * 16 + k);
    let p: Vec3;
    let rot: Vec3 = [0, 0, 0];
    let alpha = 1;
    let sz = size * (0.6 + 0.8 * r(11));
    if (kind === "confetti" || kind === "snow") {
      const H = area[1];
      const v = speed * (0.6 + 0.8 * r(3));
      const y = H / 2 - ((((t * v + r(1) * H) % H) + H) % H);
      const sway = Math.sin(t * (1 + r(4) * 2) + r(5) * Math.PI * 2) * area[0] * 0.03;
      p = [(r(0) - 0.5) * area[0] + sway, y, (r(2) - 0.5) * area[2]];
      rot = [r(6) * 360 + t * spin * (r(7) - 0.5) * 2, r(8) * 360 + t * spin * (r(9) - 0.5) * 2, r(10) * 360];
      // Мягко проявляться у верхнего и таять у нижнего края области.
      const edge = (H / 2 - Math.abs(y)) / (H * 0.08);
      alpha = clamp01(edge);
    } else if (kind === "float") {
      const H = area[1];
      const v = speed * (0.5 + r(3));
      const y = ((((t * v + r(1) * H) % H) + H) % H) - H / 2;
      p = [(r(0) - 0.5) * area[0] + Math.sin(t * 0.7 + r(4) * 6.28) * area[0] * 0.02, y, (r(2) - 0.5) * area[2]];
      alpha = clamp01((H / 2 - Math.abs(y)) / (H * 0.15)) * (0.5 + 0.5 * r(6));
    } else {
      const age = t - at - r(0) * 0.12;
      if (age < 0 || age > life) continue;
      const th = r(1) * Math.PI * 2;
      const ph = Math.acos(2 * r(2) - 1);
      const sp = speed * (0.35 + 0.65 * r(3));
      const ease = 1 - Math.exp(-age * 3);
      const dist = (sp / 3) * ease;
      p = [Math.sin(ph) * Math.cos(th) * dist, Math.cos(ph) * dist - 0.5 * gravity * age * age * 0.3, Math.sin(ph) * Math.sin(th) * dist];
      rot = [r(6) * 360 + age * spin, r(8) * 360 + age * spin * 0.7, r(10) * 360];
      const q = age / life;
      alpha = q < 0.7 ? 1 : 1 - (q - 0.7) / 0.3;
      sz *= 1 - 0.4 * q;
    }
    const color = palette[Math.floor(r(12) * palette.length)] ?? palette[0];
    const a = inh.opacity * mat.opacity * alpha;
    if (a <= 0.01) continue;
    if (shape === "dot") {
      const q = apply(mv, p[0], p[1], p[2]);
      const s = project(f, q[0], q[1], q[2]);
      if (!s) continue;
      const k = f.cam.type === "orthographic" ? f.cam.focal : f.cam.focal / -q[2];
      const rad = (sz / 2) * k;
      if (rad < 0.2) continue;
      const col = f.fog && mat.fog ? fogged(f, color, -q[2]) : color;
      f.stats.faces++;
      f.stats.drawn++;
      pushItem(f, inh, q[2], {
        k: "fn",
        draw: (ctx) => {
          ctx.save();
          ctx.globalAlpha *= a;
          ctx.fillStyle = rgbStr(col);
          ctx.beginPath();
          ctx.arc(s[0], s[1], rad, 0, Math.PI * 2);
          ctx.fill();
          ctx.restore();
        },
      });
      continue;
    }
    // Листок: четырёхугольник sz × 0.6·sz, двусторонний, свет по наклону — «мерцает» при вращении.
    const m = mul(inh.m, compose(p, rot, [1, 1, 1]));
    const pm = mul(f.cam.view, m);
    const hw = sz / 2;
    const hh = sz * 0.3;
    const quad = [
      [-hw, -hh],
      [hw, -hh],
      [hw, hh],
      [-hw, hh],
    ].map(([x, y]) => apply(pm, x, y, 0));
    const ring: number[] = [];
    let zc = 0;
    let bad = false;
    for (const q of quad) {
      const s = project(f, q[0], q[1], q[2]);
      if (!s) {
        bad = true;
        break;
      }
      ring.push(s[0], s[1]);
      zc += q[2];
    }
    if (bad) continue;
    zc /= 4;
    const nm = normalMatrix(m);
    const N = norm([nm[2], nm[5], nm[8]]);
    const V = viewDir(f, [m[3], m[7], m[11]]);
    const facing = N[0] * V[0] + N[1] * V[1] + N[2] * V[2];
    const Nf: Vec3 = facing >= 0 ? N : [-N[0], -N[1], -N[2]];
    const sh = shadeFace(f, { ...mat, color }, Nf, [m[3], m[7], m[11]], -zc);
    f.stats.faces++;
    f.stats.drawn++;
    pushItem(f, inh, zc, { k: "poly", rings: [ring], fill: rgbStr(sh.c), alpha: a, seam: false, pattern: null, ink: null, edges: null, stroke: null, evenOdd: false });
  }
}

/* ---------- рисование ---------- */

function drawPoly(ctx: Ctx2D, it: PolyItem, base: number, dev: number): void {
  ctx.globalAlpha = base * it.alpha;
  const open = (it as PolyItem & { open?: boolean }).open === true;
  if (it.parts) {
    for (const part of it.parts) {
      const r = part.ring;
      ctx.beginPath();
      ctx.moveTo(r[0], r[1]);
      for (let k = 2; k < r.length; k += 2) ctx.lineTo(r[k], r[k + 1]);
      ctx.closePath();
      ctx.fillStyle = part.fill;
      ctx.fill();
      if (it.seam) {
        ctx.strokeStyle = part.fill;
        ctx.lineWidth = 1 / dev;
        ctx.stroke();
      }
      if (part.pattern) {
        ctx.fillStyle = part.pattern;
        ctx.fill();
      }
    }
  }
  ctx.beginPath();
  for (const r of it.rings) {
    ctx.moveTo(r[0], r[1]);
    for (let k = 2; k < r.length; k += 2) ctx.lineTo(r[k], r[k + 1]);
    if (!open) ctx.closePath();
  }
  if (it.fill) {
    ctx.fillStyle = it.fill;
    ctx.fill(it.evenOdd ? "evenodd" : "nonzero");
    if (it.seam) {
      // Швы сглаживания между соседними гранями: тонкий штрих тем же цветом.
      ctx.strokeStyle = it.fill;
      ctx.lineWidth = 1 / dev;
      ctx.stroke();
    }
    if (it.pattern) {
      ctx.fillStyle = it.pattern;
      ctx.fill(it.evenOdd ? "evenodd" : "nonzero");
    }
  }
  if (it.stroke) {
    ctx.strokeStyle = it.stroke.color;
    ctx.lineWidth = it.stroke.width;
    ctx.stroke();
  }
  if (it.ink && it.edges && it.edges.length) {
    ctx.beginPath();
    const e = it.edges;
    for (let k = 0; k < e.length; k += 4) {
      ctx.moveTo(e[k], e[k + 1]);
      ctx.lineTo(e[k + 2], e[k + 3]);
    }
    ctx.strokeStyle = it.ink.color;
    ctx.lineWidth = it.ink.width;
    ctx.stroke();
  }
}

function drawBackground(ctx: Ctx2D, raw: unknown, w: number, h: number, c: EvalCtx): void {
  if (raw === undefined || raw === null || raw === "none" || raw === "transparent") return;
  if (isObj(raw) && Array.isArray(raw.gradient)) {
    const stops = raw.gradient.map((s) => evalColor(s, BLACK, c));
    if (!stops.length) return;
    const a = numOr(raw.angle, 90) * DEG;
    const half = (Math.abs(Math.cos(a)) * w + Math.abs(Math.sin(a)) * h) / 2;
    let fill: string | CanvasGradient = rgbaStr(stops[0]);
    if (raw.radial === true) {
      const g = ctx.createRadialGradient(0, 0, 0, 0, 0, Math.hypot(w, h) / 2);
      stops.forEach((s, i) => g.addColorStop(i / Math.max(1, stops.length - 1), rgbaStr(s)));
      fill = g;
    } else if (stops.length > 1) {
      // angle 90 — снизу вверх (первый цвет внизу), 0 — слева направо.
      const g = ctx.createLinearGradient(-Math.cos(a) * half, Math.sin(a) * half, Math.cos(a) * half, -Math.sin(a) * half);
      stops.forEach((s, i) => g.addColorStop(i / (stops.length - 1), rgbaStr(s)));
      fill = g;
    }
    ctx.fillStyle = fill;
    ctx.fillRect(-w / 2, -h / 2, w, h);
    return;
  }
  const col = evalColor(raw, BLACK, c);
  ctx.fillStyle = rgbaStr(col);
  ctx.fillRect(-w / 2, -h / 2, w, h);
}

/**
 * Нарисовать сцену в момент `opts.t` в текущей системе координат холста: вьюпорт w×h
 * с центром в (0, 0), единица сцены = 1 единица холста на плоскости z = 0 у камеры по умолчанию.
 */
export function renderScene(ctx: Ctx2D, spec: unknown, opts: SceneRenderOptions): SceneStats {
  const stats: SceneStats = { faces: 0, drawn: 0, items: 0, culled: 0 };
  if (!isObj(spec)) return stats;
  const w = Math.max(1, opts.w);
  const h = Math.max(1, opts.h);
  const seed = ((fin(spec.seed) ? Math.floor(spec.seed) : 0) ^ (opts.seed ?? 0)) >>> 0;
  const root: EvalCtx = { t: opts.t, T: opts.t, dur: opts.dur, i: 0, n: 1, seed };
  const cam = evalCamera(spec.camera, root, h);
  const m0 = ctx.getTransform();
  const dev = Math.hypot(m0.a, m0.b) || 1;
  let fog: Fog = null;
  if (isObj(spec.fog)) {
    const fg = spec.fog;
    fog = {
      color: evalColor(fg.color, [16, 16, 24, 1], root),
      near: evalNum(fg.near, focalFor(h, 40) * 0.8, root),
      far: evalNum(fg.far, focalFor(h, 40) * 3, root),
      max: clamp01(evalNum(fg.max, 1, root)),
    };
  }
  const f: Frame = {
    ctx,
    env: opts.env,
    cam,
    lights: evalLights(spec.lights, root),
    fog,
    items: [],
    T: opts.t,
    dur: opts.dur,
    seed,
    w,
    h,
    dev,
    stats,
    nodes: 0,
    seq: 0,
    near: cam.near,
    far: cam.far,
  };
  const style = isObj(spec.style) ? spec.style : null;
  const inh: Inherit = { m: identity(), opacity: 1, tint: null, mat: style, t: opts.t, i: 0, n: 1, order: 0, group: null, depth: 0 };
  const nodes = Array.isArray(spec.nodes) ? spec.nodes : [];
  for (const n of nodes) visit(n, f, inh);
  f.items.sort((a, b) => a.order - b.order || a.depth - b.depth || a.sub - b.sub || a.seq - b.seq);
  stats.items = f.items.length;

  ctx.save();
  if (opts.clip !== false) {
    ctx.beginPath();
    ctx.rect(-w / 2, -h / 2, w, h);
    ctx.clip();
  }
  drawBackground(ctx, spec.background, w, h, root);
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  const base = ctx.globalAlpha;
  for (const it of f.items) {
    if (it.k === "poly") drawPoly(ctx, it, base, dev);
    else {
      ctx.globalAlpha = base;
      it.draw(ctx);
    }
  }
  ctx.globalAlpha = base;
  ctx.restore();
  return stats;
}

/** Ссылки на картинки сцены (узлы image: src / asset_id) — рендер грузит их заранее. */
export function sceneImageRefs(spec: unknown, out: Set<string> = new Set(), depth = 0): Set<string> {
  if (depth > SCENE_LIMITS.maxDepth) return out;
  const walk = (n: unknown, d: number) => {
    if (!isObj(n) || d > SCENE_LIMITS.maxDepth) return;
    if (n.type === "image") {
      const ref = n.src ?? n.asset_id;
      if (ref !== undefined && ref !== null && ref !== "") out.add(String(ref));
    }
    if (Array.isArray(n.children)) for (const k of n.children) walk(k, d + 1);
    if (isObj(n.child)) walk(n.child, d + 1);
  };
  if (isObj(spec) && Array.isArray(spec.nodes)) for (const n of spec.nodes) walk(n, depth);
  return out;
}
