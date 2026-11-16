/**
 * Как слово на поверхности ложится в кадр (слой `tracked_text`, layers/tracked.ts): режимы наложения.
 *
 * Владелец 16.11: слова должны быть ЧАСТЬЮ сцены, а не наклейкой. Поэтому по умолчанию — `paint` («краска»):
 * буквы берут освещение и фактуру поверхности под собой — кадр под словом (то, что уже нарисовано под слоем:
 * в рендере — тот же холст, в превью — `env.underlay`) раскладывается на низкие частоты (свет, тени: буквы
 * темнеют в тени и светлеют на свету) и высокие (фактура: камень, швы, царапины проступают сквозь краску);
 * экспозиция и цветовая температура подгоняются под участок, край чуть мягче, лёгкое зерно. Остальные режимы —
 * обычные режимы наложения холста: normal (наклейка, «граффити 3D» как в референсе), multiply, screen, overlay,
 * soft-light.
 *
 * Работает по пикселям прямоугольника слова (getImageData) — сотни тысяч пикселей, миллисекунды; без фабрики
 * холстов или без кадра под слоем `paint` падает в `soft-light`.
 */
import type { Ctx2D, RenderEnv, ScratchCanvas } from "../types";
import { layerUnit } from "../types";

export const SURFACE_BLENDS = ["paint", "normal", "multiply", "screen", "overlay", "soft-light"] as const;
export type SurfaceBlend = (typeof SURFACE_BLENDS)[number];

/** Настройки «краски» (доли 0..1, кроме soften — опорные px). */
export type PaintLook = {
  /** Сколько света/теней поверхности берут буквы (0 — ровная краска). */
  shade?: number;
  /** Сколько фактуры проступает сквозь краску. */
  texture?: number;
  /** Подгонка яркости букв к яркости участка (тёмный угол — тёмная краска). */
  exposure?: number;
  /** Подгонка цвета к средней температуре участка. */
  tint?: number;
  /** Зерно (0 — нет). */
  grain?: number;
  /** Мягкость края, опорные px. */
  soften?: number;
  /** Плотность краски (кроющая способность). */
  opacity?: number;
};

export const PAINT_DEFAULTS: Required<PaintLook> = { shade: 0.85, texture: 0.6, exposure: 0.7, tint: 0.3, grain: 0.05, soften: 0.8, opacity: 0.92 };

const OPS: Record<Exclude<SurfaceBlend, "paint">, GlobalCompositeOperation> = {
  normal: "source-over",
  multiply: "multiply",
  screen: "screen",
  overlay: "overlay",
  "soft-light": "soft-light",
};

type Scratch = { canvas: ScratchCanvas; ctx: Ctx2D };
const scratch = new WeakMap<RenderEnv, Partial<Record<"L" | "R", Scratch>>>();

/** Холст во весь кадр окружения (слово в координатах кадра — L, кадр под ним и итог — R). */
export function frameScratch(env: RenderEnv, slot: "L" | "R"): Scratch | null {
  if (!env.createCanvas) return null;
  let rec = scratch.get(env);
  if (!rec) scratch.set(env, (rec = {}));
  const cur = rec[slot];
  if (cur && cur.canvas.width === env.width && cur.canvas.height === env.height) return cur;
  const canvas = env.createCanvas(Math.max(1, env.width), Math.max(1, env.height));
  const made = { canvas, ctx: canvas.getContext("2d") as Ctx2D };
  rec[slot] = made;
  return made;
}

export type Region = { x: number; y: number; w: number; h: number };

/** Слово (уже в координатах кадра на холсте `L`, прямоугольник `r`) — в кадр режимом `mode`. */
export function blendOnto(ctx: Ctx2D, env: RenderEnv, L: Scratch, r: Region, mode: SurfaceBlend, look: PaintLook | null | undefined, alpha: number, t: number): void {
  if (r.w <= 0 || r.h <= 0 || alpha <= 0.002) return;
  if (mode === "paint" && paintOnto(ctx, env, L, r, { ...PAINT_DEFAULTS, ...(look || {}) }, alpha, t)) return;
  const op = OPS[mode === "paint" ? "soft-light" : mode] ?? "source-over";
  ctx.save();
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.globalAlpha = alpha;
  ctx.globalCompositeOperation = op;
  ctx.drawImage(L.canvas as unknown as CanvasImageSource, r.x, r.y, r.w, r.h, r.x, r.y, r.w, r.h);
  ctx.restore();
}

/** Кадр под слоем: превью — подложка (видео на своём холсте), рендер — сам холст кадра. */
function backdrop(ctx: Ctx2D, env: RenderEnv): (CanvasImageSource & { width: number; height: number }) | null {
  const under = env.underlay as unknown as { width?: number; height?: number } | null | undefined;
  if (under && Number(under.width) > 0) return under as unknown as CanvasImageSource & { width: number; height: number };
  const own = (ctx as unknown as { canvas?: { width: number; height: number } }).canvas;
  return own && own.width > 0 ? (own as unknown as CanvasImageSource & { width: number; height: number }) : null;
}

/** Разделимое размытие «ящиком» радиуса r (два прохода — почти гаусс) — новый массив. */
export function boxBlur(src: Float32Array, w: number, h: number, r: number, passes = 2): Float32Array {
  const a = Float32Array.from(src);
  const b = new Float32Array(src.length);
  const rr = Math.max(1, Math.round(r));
  for (let pass = 0; pass < passes; pass++) {
    for (let y = 0; y < h; y++) {
      const row = y * w;
      let acc = 0;
      let n = 0;
      for (let x = -rr; x <= rr; x++) if (x >= 0 && x < w) { acc += a[row + x]; n++; }
      for (let x = 0; x < w; x++) {
        b[row + x] = acc / n;
        const out = x - rr;
        const inn = x + rr + 1;
        if (out >= 0) { acc -= a[row + out]; n--; }
        if (inn < w) { acc += a[row + inn]; n++; }
      }
    }
    for (let x = 0; x < w; x++) {
      let acc = 0;
      let n = 0;
      for (let y = -rr; y <= rr; y++) if (y >= 0 && y < h) { acc += b[y * w + x]; n++; }
      for (let y = 0; y < h; y++) {
        a[y * w + x] = acc / n;
        const out = y - rr;
        const inn = y + rr + 1;
        if (out >= 0) { acc -= b[out * w + x]; n--; }
        if (inn < h) { acc += b[inn * w + x]; n++; }
      }
    }
  }
  return a;
}

function hash3(x: number, y: number, z: number): number {
  let h = (x * 374761393 + y * 668265263 + z * 2147483647) | 0;
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  h ^= h >>> 16;
  return (h >>> 0) / 4294967295;
}

/** «Краска»: свет и фактура поверхности в буквах, экспозиция и температура участка. false — кадра под слоем нет. */
function paintOnto(ctx: Ctx2D, env: RenderEnv, L: Scratch, r: Region, look: Required<PaintLook>, alpha: number, t: number): boolean {
  const bg = backdrop(ctx, env);
  const R = frameScratch(env, "R");
  if (!bg || !R) return false;
  const { x, y, w, h } = r;
  const kx = bg.width / env.width;
  const ky = bg.height / env.height;
  const rc = R.ctx;
  rc.save();
  rc.setTransform(1, 0, 0, 1, 0, 0);
  rc.globalAlpha = 1;
  rc.globalCompositeOperation = "copy";
  rc.filter = "none";
  rc.drawImage(bg, x * kx, y * ky, w * kx, h * ky, 0, 0, w, h);
  rc.restore();
  let B: ImageData;
  let T: ImageData;
  try {
    B = rc.getImageData(0, 0, w, h);
    T = L.ctx.getImageData(x, y, w, h);
  } catch {
    return false; // подложка с другого источника (превью без CORS) — режим попроще
  }
  const n = w * h;
  const bd = B.data;
  const td = T.data;
  const Y = new Float32Array(n);
  for (let i = 0; i < n; i++) Y[i] = 0.299 * bd[i * 4] + 0.587 * bd[i * 4 + 1] + 0.114 * bd[i * 4 + 2];
  const unit = layerUnit(env);
  const low = boxBlur(Y, w, h, Math.max(2, 14 * unit));
  // Средние участка под буквами (по маске букв): яркость и цвет.
  let sa = 0;
  let sy = 0;
  let sr = 0;
  let sg = 0;
  let sb = 0;
  for (let i = 0; i < n; i++) {
    const a = td[i * 4 + 3] / 255;
    if (a <= 0.01) continue;
    sa += a;
    sy += Y[i] * a;
    sr += bd[i * 4] * a;
    sg += bd[i * 4 + 1] * a;
    sb += bd[i * 4 + 2] * a;
  }
  if (sa <= 0) return true;
  const meanY = Math.max(4, sy / sa);
  const mr = sr / sa;
  const mg = sg / sa;
  const mb = sb / sa;
  const avg = Math.max(1, (mr + mg + mb) / 3);
  const tint = [1 + look.tint * (mr / avg - 1), 1 + look.tint * (mg / avg - 1), 1 + look.tint * (mb / avg - 1)];
  const ex = Math.pow(Math.min(1.1, Math.max(0.3, meanY / 140)), look.exposure);
  const frame = Math.round(t * 30);
  const out = rc.createImageData(w, h);
  const od = out.data;
  for (let i = 0; i < n; i++) {
    const a = td[i * 4 + 3];
    if (a === 0) continue;
    const shade = Math.pow(Math.min(1.6, Math.max(0.45, low[i] / meanY)), look.shade);
    const detail = (Y[i] - low[i]) * look.texture;
    const g = look.grain ? (hash3((x + (i % w)) | 0, (y + ((i / w) | 0)) | 0, frame) - 0.5) * look.grain * 255 : 0;
    for (let c = 0; c < 3; c++) {
      const v = td[i * 4 + c] * ex * shade * tint[c] + detail + g;
      od[i * 4 + c] = v < 0 ? 0 : v > 255 ? 255 : v;
    }
    od[i * 4 + 3] = a * look.opacity;
  }
  rc.putImageData(out, 0, 0);
  ctx.save();
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.globalAlpha = alpha;
  ctx.globalCompositeOperation = "source-over";
  const soft = look.soften * unit;
  if (soft > 0.25) ctx.filter = `blur(${soft.toFixed(2)}px)`;
  ctx.drawImage(R.canvas as unknown as CanvasImageSource, 0, 0, w, h, x, y, w, h);
  ctx.restore();
  return true;
}
