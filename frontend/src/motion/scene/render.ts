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
import type { Ctx2D } from "../types";
import { DEG, focalFor, identity } from "./math";
import { evalColor, evalNum, type EvalCtx } from "./value";
import {
  BLACK,
  clamp01,
  fin,
  type Fog,
  type Frame,
  type Inherit,
  isObj,
  numOr,
  type PolyItem,
  rgbaStr,
  SCENE_LIMITS,
  type SceneRenderOptions,
  type SceneStats,
} from "./frame";
import { evalCamera, evalLights } from "./lighting";
import { visit } from "./traverse";

export { SCENE_LIMITS } from "./frame";
export type { SceneSpec, SceneRenderOptions, SceneStats } from "./frame";
export { CAMERA_TYPES, evalCamera } from "./lighting";
export { SHADINGS, NODE_TYPES, TYPE_ALIASES, MESH_TYPES, FLAT_TYPES } from "./nodeTypes";
export type { Shading } from "./nodeTypes";
export { PARTICLE3D_KINDS } from "./drawMedia";

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
/** Граф сцены в момент opts.t: элементы кадра (ещё не нарисованы), без сортировки. */
function collectScene(ctx: Ctx2D, spec: Record<string, unknown>, opts: SceneRenderOptions, stats: SceneStats): { f: Frame; root: EvalCtx; w: number; h: number } {
  const w = Math.max(1, opts.w);
  const h = Math.max(1, opts.h);
  const seed = ((fin(spec.seed) ? Math.floor(spec.seed) : 0) ^ (opts.seed ?? 0)) >>> 0;
  const D = focalFor(h, 40);
  const root: EvalCtx = { t: opts.t, T: opts.t, dur: opts.dur, i: 0, n: 1, seed, W: w, H: h, D };
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
    D,
  };
  const style = isObj(spec.style) ? spec.style : null;
  const inh: Inherit = { m: identity(), opacity: 1, tint: null, mat: style, t: opts.t, i: 0, n: 1, order: 0, group: null, depth: 0 };
  const nodes = Array.isArray(spec.nodes) ? spec.nodes : [];
  for (const n of nodes) visit(n, f, inh);
  return { f, root, w, h };
}

export function renderScene(ctx: Ctx2D, spec: unknown, opts: SceneRenderOptions): SceneStats {
  const stats: SceneStats = { faces: 0, drawn: 0, items: 0, culled: 0 };
  if (!isObj(spec)) return stats;
  const { f, root, w, h } = collectScene(ctx, spec, opts, stats);
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
    if (it.k === "poly") drawPoly(ctx, it, base, f.dev);
    else {
      ctx.globalAlpha = base;
      it.draw(ctx);
    }
  }
  ctx.globalAlpha = base;
  ctx.restore();
  return stats;
}

/**
 * На сколько единиц вьюпорта сдвинулись грани сцены между моментами `t0` и `t1` (наибольшее
 * смещение вершины у граней в одном порядке обхода) — для motion blur слоя scene: сцена
 * крутится внутри неподвижной рамки, по рамке её движения не видно. Элементы-функции (текст,
 * картинки) не меряются. Ничего не рисует.
 */
export function sceneTravel(ctx: Ctx2D, spec: unknown, opts: SceneRenderOptions, t0: number, t1: number): number {
  if (!isObj(spec)) return 0;
  const pts = (t: number): number[][] => {
    const stats: SceneStats = { faces: 0, drawn: 0, items: 0, culled: 0 };
    ctx.save();
    const { f } = collectScene(ctx, spec, { ...opts, t }, stats);
    ctx.restore();
    const out: number[][] = [];
    for (const it of f.items) if (it.k === "poly" && it.rings[0]?.length) out.push(it.rings[0]);
    return out;
  };
  const a = pts(t0);
  const b = pts(t1);
  let d = 0;
  for (let i = 0; i < Math.min(a.length, b.length); i++) {
    const ra = a[i];
    const rb = b[i];
    for (let j = 0; j + 1 < Math.min(ra.length, rb.length); j += 2) d = Math.max(d, Math.hypot(rb[j] - ra[j], rb[j + 1] - ra[j + 1]));
  }
  // Граней стало больше или меньше (появление, частицы) — хотя бы как сдвиг на порог.
  if (a.length !== b.length) d = Math.max(d, 2);
  return d;
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
