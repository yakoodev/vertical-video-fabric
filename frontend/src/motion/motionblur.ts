/**
 * Motion blur — размытие движения быстрых слоёв, наездов камеры и сцен (docs/motion/spec.md).
 *
 * Метод — накопление подкадров: в пределах «затвора» (`shutter` — доля интервала кадра, 0..1;
 * 0.5 — киношные 180°) рисунок повторяется в N моментах вокруг t и усредняется. Дорого, поэтому
 * только там, где правда быстро: сначала считается, на сколько опорных px сместился слой (углы
 * рамки) за время затвора; меньше порога — рисуется один раз, ровно как без размытия (в покое
 * размытие — тождество, пиксель в пиксель). Иначе N = 4..12 по длине пути.
 *
 * Всё — функция t: те же N и те же моменты в превью и рендере (детерминировано). Превью может
 * ограничить N (`env.motionBlurSamples`) — ширина смаза та же, ступенек на смазе чуть больше.
 *
 * Усреднение без getImageData (одинаково в Skia и браузере):
 * - непрозрачная основа — бегущее среднее source-over: k-й подкадр с прозрачностью 1/k;
 * - прозрачный слой — на своём холсте-накопителе: копия × (1 − 1/k) (destination-out), плюс
 *   подкадр × 1/k (lighter); итог кладётся в кадр режимом наложения слоя. Ошибка округления —
 *   1–2 уровня из 255 при 12 подкадрах (замер — docs/motion/spec.md).
 */
import { frameCameraMatrix } from "./camera";
import { clamp, clamp01 } from "./easing";
import { baseCover, camera3dAt, makeProjector } from "./space3d";
import type { CameraKey, Ctx2D, RenderEnv, ScratchCanvas } from "./types";

export const MOTION_BLUR_MIN_SAMPLES = 4;
export const MOTION_BLUR_MAX_SAMPLES = 12;
/** Подкадров не больше в превью редактора (ширина смаза та же). */
export const MOTION_BLUR_PREVIEW_SAMPLES = 6;
/** Путь за затвор меньше — слой рисуется один раз (опорн. px, ~1.5 px кадра 1080). */
export const MOTION_BLUR_THRESHOLD = 1.5;
/** Опорных px пути на подкадр: длиннее путь — больше подкадров (до максимума). */
export const MOTION_BLUR_PX_PER_SAMPLE = 6;

/** Затвор 0..1 из поля (нет / не число — null). */
export function shutterValue(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? clamp01(v) : null;
}

/** Сколько подкадров на путь `travel` (опорн. px за время затвора): 1 — без размытия. */
export function blurSamples(travel: number, cap: number = MOTION_BLUR_MAX_SAMPLES): number {
  if (!(travel >= MOTION_BLUR_THRESHOLD)) return 1;
  const hi = clamp(Math.floor(cap), MOTION_BLUR_MIN_SAMPLES, MOTION_BLUR_MAX_SAMPLES);
  return clamp(Math.ceil(travel / MOTION_BLUR_PX_PER_SAMPLE) + 1, MOTION_BLUR_MIN_SAMPLES, hi);
}

/**
 * Моменты подкадров: затвор по центру кадра (t − s/2fps … t + s/2fps), зажатые в [lo, hi] —
 * на первом и последнем кадре слоя смаз не «гасит» его наполовину.
 */
export function shutterTimes(t: number, n: number, shutter: number, fps: number, lo = -Infinity, hi = Infinity): number[] {
  if (n <= 1 || !(shutter > 0) || !(fps > 0)) return [t];
  const span = shutter / fps;
  const out: number[] = [];
  for (let i = 0; i < n; i++) out.push(clamp(t + (i / (n - 1) - 0.5) * span, lo, hi));
  return out;
}

/* ---------- камера ---------- */

/**
 * Затвор камеры в момент t: `motionBlur` ключа, с которого идёт текущий отрезок (как `ease`),
 * до первого ключа — первого; у ключа нет поля — значение клипа `fallback`.
 */
export function cameraShutterAt(keys: CameraKey[] | null | undefined, t: number, fallback?: number | null): number {
  const def = shutterValue(fallback) ?? 0;
  if (!keys?.length) return def;
  let pick: CameraKey | null = null;
  let first: CameraKey | null = null;
  for (const k of keys) {
    if (!first || k.t < first.t) first = k;
    if (k.t <= t && (!pick || k.t >= pick.t)) pick = k;
  }
  return shutterValue((pick ?? first)?.motionBlur) ?? def;
}

/** Углы кадра основы после камеры в момент t, px. */
function cameraCorners(keys: CameraKey[], t: number, W: number, H: number, unit: number, box: CameraBox, threeD: boolean): number[][] {
  const m = frameCameraMatrix(keys, t, W, H, box);
  if (threeD) {
    const cam = camera3dAt(keys, t);
    const k = baseCover(cam, W, H, unit);
    const zb = Math.max(0, cam.baseZ) * unit;
    const proj = makeProjector(cam, W, H, unit, m);
    const pts = [
      proj((-W / 2) * k, (-H / 2) * k, zb),
      proj((W / 2) * k, (-H / 2) * k, zb),
      proj((W / 2) * k, (H / 2) * k, zb),
      proj((-W / 2) * k, (H / 2) * k, zb),
    ];
    if (pts.every(Boolean)) return pts as number[][];
  }
  const b = box ?? { x: 0, y: 0, w: W, h: H };
  return [
    [b.x, b.y],
    [b.x + b.w, b.y],
    [b.x + b.w, b.y + b.h],
    [b.x, b.y + b.h],
  ].map(([x, y]) => [m.scale * x + m.tx, m.scale * y + m.ty]);
}

type CameraBox = { x: number; y: number; w: number; h: number } | null | undefined;

/** Наибольшее смещение точки (углов) между двумя наборами, px. */
export function maxTravel(a: number[][], b: number[][]): number {
  let d = 0;
  for (let i = 0; i < Math.min(a.length, b.length); i++) d = Math.max(d, Math.hypot(b[i][0] - a[i][0], b[i][1] - a[i][1]));
  return d;
}

export type CameraBlurOptions = {
  fps: number;
  W: number;
  H: number;
  /** px кадра на опорный px (layerUnit). */
  unit: number;
  /** Затвор клипа по умолчанию (render.motion_blur). */
  fallback?: number | null;
  /** Раскладка стрима: камера двигает только окно игры. */
  box?: CameraBox;
  /** 3D-камера (облёт, dolly) — основа плоскостью на глубине. */
  threeD?: boolean;
  /** Предел подкадров (превью — меньше). */
  maxSamples?: number;
};

/**
 * Моменты подкадров основы под камерой: [t] — камера в покое или без размытия (кадр рисуется
 * ровно как раньше), иначе 4..12 моментов в пределах затвора.
 */
export function cameraBlurTimes(keys: CameraKey[] | null | undefined, t: number, o: CameraBlurOptions): number[] {
  if (!keys || keys.length < 2) return [t];
  const shutter = cameraShutterAt(keys, t, o.fallback);
  if (!(shutter > 0) || !(o.fps > 0)) return [t];
  const half = shutter / o.fps / 2;
  const t0 = Math.max(0, t - half);
  const t1 = t + half;
  const a = cameraCorners(keys, t0, o.W, o.H, o.unit, o.box, Boolean(o.threeD));
  const b = cameraCorners(keys, t1, o.W, o.H, o.unit, o.box, Boolean(o.threeD));
  const n = blurSamples(maxTravel(a, b) / Math.max(1e-6, o.unit), o.maxSamples);
  return shutterTimes(t, n, shutter, o.fps, 0);
}

/* ---------- накопление ---------- */

type Buf = { canvas: ScratchCanvas; ctx: Ctx2D };
type Bufs = { w: number; h: number; sample: Buf; acc: Buf | null };
const bufsByEnv = new WeakMap<RenderEnv, Bufs>();

function makeBuf(env: RenderEnv, w: number, h: number): Buf {
  const canvas = (env.createCanvas as NonNullable<RenderEnv["createCanvas"]>)(w, h);
  return { canvas, ctx: canvas.getContext("2d") as Ctx2D };
}

/** Холсты размером с кадр: подкадр и накопитель (один набор на окружение). */
function buffers(env: RenderEnv, acc: boolean): Bufs | null {
  if (!env.createCanvas) return null;
  const w = Math.max(1, Math.round(env.width));
  const h = Math.max(1, Math.round(env.height));
  let b = bufsByEnv.get(env);
  if (!b || b.w !== w || b.h !== h) {
    b = { w, h, sample: makeBuf(env, w, h), acc: null };
    bufsByEnv.set(env, b);
  }
  if (acc && !b.acc) b.acc = makeBuf(env, w, h);
  return b;
}

function reset(c: Ctx2D): void {
  c.setTransform(1, 0, 0, 1, 0, 0);
  c.globalAlpha = 1;
  c.globalCompositeOperation = "source-over";
  c.filter = "none";
}

const src = (c: ScratchCanvas) => c as unknown as CanvasImageSource;

export type Region = { x: number; y: number; w: number; h: number };

function clipRegion(r: Region | null | undefined, w: number, h: number): Region {
  if (!r) return { x: 0, y: 0, w, h };
  const x0 = clamp(Math.floor(r.x), 0, w);
  const y0 = clamp(Math.floor(r.y), 0, h);
  const x1 = clamp(Math.ceil(r.x + r.w), 0, w);
  const y1 = clamp(Math.ceil(r.y + r.h), 0, h);
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
}

/**
 * Непрозрачный кадр (основа под камерой) со смазом: первый подкадр прямо в кадр, k-й — на
 * свой холст и сверху с прозрачностью 1/k (бегущее среднее). `paint(target, tt)` рисует
 * кадр момента tt целиком (с камерой). Нет фабрики холстов — один кадр, как без размытия.
 */
export function paintOpaqueBlur(ctx: Ctx2D, env: RenderEnv, times: number[], paint: (target: Ctx2D, tt: number) => void): void {
  const b = times.length > 1 ? buffers(env, false) : null;
  paint(ctx, times[Math.floor(times.length / 2)] ?? times[0]);
  if (!b) return;
  // Средний подкадр уже в кадре; остальные — по одному сверху.
  const order = times.filter((_, i) => i !== Math.floor(times.length / 2));
  const m = ctx.getTransform();
  order.forEach((tt, i) => {
    const s = b.sample.ctx;
    s.save();
    reset(s);
    s.clearRect(0, 0, b.w, b.h);
    s.setTransform(m.a, m.b, m.c, m.d, m.e, m.f);
    paint(s, tt);
    s.restore();
    ctx.save();
    reset(ctx);
    ctx.globalAlpha = 1 / (i + 2);
    ctx.drawImage(src(b.sample.canvas), 0, 0, b.w, b.h, 0, 0, b.w, b.h);
    ctx.restore();
  });
}

/**
 * Прозрачный рисунок (слой) со смазом: подкадры усредняются на накопителе внутри `region`
 * (px кадра; нет — весь кадр), итог кладётся в кадр режимом `composite`. `paint(target, tt)`
 * рисует слой момента tt режимом normal. Возвращает false — фабрики холстов нет (не нарисовано).
 */
export function paintLayerBlur(
  ctx: Ctx2D,
  env: RenderEnv,
  times: number[],
  paint: (target: Ctx2D, tt: number) => void,
  region?: Region | null,
  composite: GlobalCompositeOperation = "source-over",
): boolean {
  const b = buffers(env, true);
  if (!b || !b.acc) return false;
  const r = clipRegion(region, b.w, b.h);
  if (r.w <= 0 || r.h <= 0) return true;
  const m = ctx.getTransform();
  const a = b.acc.ctx;
  const s = b.sample.ctx;
  times.forEach((tt, i) => {
    s.save();
    reset(s);
    s.clearRect(r.x, r.y, r.w, r.h);
    s.setTransform(m.a, m.b, m.c, m.d, m.e, m.f);
    paint(s, tt);
    s.restore();
    a.save();
    reset(a);
    if (i === 0) {
      a.globalCompositeOperation = "copy";
    } else {
      // Накопленное × (1 − 1/k), затем + подкадр × 1/k: точное среднее с прозрачностью.
      a.globalCompositeOperation = "destination-out";
      a.globalAlpha = 1 / (i + 1);
      a.fillStyle = "#000000";
      a.fillRect(r.x, r.y, r.w, r.h);
      a.globalCompositeOperation = "lighter";
    }
    a.drawImage(src(b.sample.canvas), r.x, r.y, r.w, r.h, r.x, r.y, r.w, r.h);
    a.restore();
  });
  ctx.save();
  reset(ctx);
  ctx.globalCompositeOperation = composite;
  ctx.drawImage(src(b.acc.canvas), r.x, r.y, r.w, r.h, r.x, r.y, r.w, r.h);
  ctx.restore();
  return true;
}

