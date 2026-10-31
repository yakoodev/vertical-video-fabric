/**
 * Эффект кадра (`fx`): процедурно, без файлов — поверх видео и слоёв на дорожках ниже.
 *
 * Два рода видов. Накладки (scanlines, light_leak, flash_frame, grain_overlay) просто рисуют
 * поверх своим режимом наложения. Пиксельные (vhs, chromatic) двигают сам кадр: собирают его
 * копию (в рендере — холст компоновщика, в превью — подложка-видео `env.underlay` + холст слоёв),
 * раскладывают на каналы умножением на чистый цвет и складывают со сдвигом (`lighter`) — без
 * getImageData, дёшево и одинаково в Skia и в браузере.
 *
 * Всё — функция времени и зерна (id слоя или `seed`): «случайные» полосы, зерно и вспышки
 * берутся из rand01 по номеру шага (не кадра превью), превью и рендер совпадают кадр в кадр.
 * Зерно — плитки шума 256×256 из того же rand01, один раз на окружение.
 */
import { clamp, clamp01 } from "../easing";
import { hashString, noise1, rand01 } from "../keys";
import type { LayerState } from "../animate";
import type { BlendMode, Ctx2D, FxKind, FxLayer, FxProps, RenderEnv, ScratchCanvas } from "../types";
import { layerUnit } from "../types";
import { rgba } from "./common";

export const FX_KINDS: FxKind[] = ["vhs", "scanlines", "light_leak", "chromatic", "flash_frame", "grain_overlay"];

/** Сила и наложение по умолчанию у каждого вида. */
export const FX_DEFAULTS: Record<FxKind, { intensity: number; blend: BlendMode }> = {
  vhs: { intensity: 0.6, blend: "normal" },
  scanlines: { intensity: 0.45, blend: "multiply" },
  light_leak: { intensity: 0.7, blend: "screen" },
  chromatic: { intensity: 0.5, blend: "normal" },
  flash_frame: { intensity: 0.9, blend: "add" },
  grain_overlay: { intensity: 0.5, blend: "overlay" },
};

const BLEND: Record<BlendMode, GlobalCompositeOperation> = {
  normal: "source-over",
  multiply: "multiply",
  screen: "screen",
  overlay: "overlay",
  add: "lighter",
};

/** Тёплые краски пятен света по умолчанию. */
export const LEAK_COLORS = ["#ff7a18", "#ff3d5a", "#ffc46b", "#ff9e3d"];

export type ResolvedFx = {
  kind: FxKind;
  intensity: number;
  speed: number;
  blend: BlendMode;
  color: string;
  colors: string[];
  size: number;
  seed: number;
};

export function resolveFx(layer: FxLayer): ResolvedFx {
  const p: FxProps = layer.props || {};
  const kind = FX_KINDS.includes(p.kind as FxKind) ? (p.kind as FxKind) : "vhs";
  const def = FX_DEFAULTS[kind];
  const num = (v: unknown, d: number) => (typeof v === "number" && Number.isFinite(v) ? v : d);
  const blend = p.blend && p.blend !== "auto" && p.blend in BLEND ? (p.blend as BlendMode) : def.blend;
  return {
    kind,
    intensity: clamp01(num(p.intensity, def.intensity)),
    speed: clamp(num(p.speed, 1), 0.1, 4),
    blend,
    color: p.color || "#ffffff",
    colors: Array.isArray(p.colors) && p.colors.length ? p.colors.slice(0, 4) : LEAK_COLORS,
    size: clamp(num(p.size, kind === "scanlines" ? 4 : 1.6), 0.5, 12),
    seed: Number.isFinite(p.seed) && (p.seed as number) > 0 ? Math.floor(p.seed as number) : hashString(layer.id || "fx"),
  };
}

/* ---------- промежуточные холсты ---------- */

type Buf = { canvas: ScratchCanvas; ctx: Ctx2D };
type FxBuffers = { w: number; h: number; frame: Buf; chan: Buf; out: Buf };
const buffersByEnv = new WeakMap<RenderEnv, FxBuffers>();

function makeBuf(env: RenderEnv, w: number, h: number): Buf {
  const canvas = (env.createCanvas as NonNullable<RenderEnv["createCanvas"]>)(w, h);
  return { canvas, ctx: canvas.getContext("2d") as Ctx2D };
}

/** Три холста размером с кадр: копия кадра, канал, сборка (одни на окружение). */
function buffers(env: RenderEnv): FxBuffers | null {
  if (!env.createCanvas) return null;
  const W = Math.max(1, Math.round(env.width));
  const H = Math.max(1, Math.round(env.height));
  let b = buffersByEnv.get(env);
  if (!b || b.w !== W || b.h !== H) {
    b = { w: W, h: H, frame: makeBuf(env, W, H), chan: makeBuf(env, W, H), out: makeBuf(env, W, H) };
    buffersByEnv.set(env, b);
  }
  return b;
}

const src = (c: ScratchCanvas) => c as unknown as CanvasImageSource;

function reset(c: Ctx2D, w: number, h: number): void {
  c.setTransform(1, 0, 0, 1, 0, 0);
  c.globalAlpha = 1;
  c.globalCompositeOperation = "source-over";
  c.filter = "none";
  c.clearRect(0, 0, w, h);
}

/** Копия того, что сейчас в кадре: подложка (превью) + уже нарисованное на холсте. */
function snapshot(ctx: Ctx2D, env: RenderEnv, b: FxBuffers): boolean {
  const own = (ctx as unknown as { canvas?: CanvasImageSource }).canvas;
  if (!own) return false;
  const f = b.frame.ctx;
  reset(f, b.w, b.h);
  if (env.underlay) f.drawImage(env.underlay, 0, 0, b.w, b.h);
  f.drawImage(own, 0, 0, b.w, b.h);
  return true;
}

/**
 * Канал кадра цветом `color` (#ff0000 — красный и т.п.) в `b.chan`: копия × чистый цвет,
 * прозрачное оставляем прозрачным (превью без подложки).
 */
function channel(b: FxBuffers, color: string): void {
  const c = b.chan.ctx;
  reset(c, b.w, b.h);
  c.drawImage(src(b.frame.canvas), 0, 0);
  c.globalCompositeOperation = "multiply";
  c.fillStyle = color;
  c.fillRect(0, 0, b.w, b.h);
  c.globalCompositeOperation = "destination-in";
  c.drawImage(src(b.frame.canvas), 0, 0);
}

/** Сложить канал в сборку со сдвигом dx и масштабом k вокруг центра кадра. */
function addChannel(b: FxBuffers, dx: number, k: number): void {
  const o = b.out.ctx;
  o.save();
  o.globalCompositeOperation = "lighter";
  const w = b.w * k;
  const h = b.h * k;
  o.drawImage(src(b.chan.canvas), (b.w - w) / 2 + dx, (b.h - h) / 2, w, h);
  o.restore();
}

/**
 * Кадр, разложенный на каналы: R и B со сдвигом `shift` px по горизонтали (vhs) и/или
 * масштабом `zoom` от центра (chromatic) — в `b.out`. Все масштабы ≥ 1: края кадра закрыты.
 */
function splitChannels(b: FxBuffers, shift: number, zoom: number): void {
  reset(b.out.ctx, b.w, b.h);
  channel(b, "#ff0000");
  addChannel(b, -shift, 1 + zoom);
  channel(b, "#00ff00");
  addChannel(b, 0, 1 + zoom * 0.5);
  channel(b, "#0000ff");
  addChannel(b, shift, 1);
}

/* ---------- шум ---------- */

const NOISE_TILE = 256;
const NOISE_TILES = 6;
const noiseByEnv = new WeakMap<RenderEnv, ScratchCanvas[]>();

/**
 * Плитки зерна 256×256: серый около 128 (сумма трёх rand01 — почти гаусс), непрозрачные.
 * Одни и те же в браузере и в Node — чистая математика от номера пикселя.
 */
function noiseTiles(env: RenderEnv): ScratchCanvas[] | null {
  if (!env.createCanvas) return null;
  let tiles = noiseByEnv.get(env);
  if (tiles) return tiles;
  tiles = [];
  for (let n = 0; n < NOISE_TILES; n++) {
    const canvas = env.createCanvas(NOISE_TILE, NOISE_TILE);
    const c = canvas.getContext("2d") as Ctx2D;
    const img = c.createImageData(NOISE_TILE, NOISE_TILE);
    const seed = 0x5eed + n * 7919;
    for (let i = 0; i < NOISE_TILE * NOISE_TILE; i++) {
      const v = (rand01(seed, i * 3) + rand01(seed, i * 3 + 1) + rand01(seed, i * 3 + 2)) / 3;
      const g = Math.round(clamp(128 + (v - 0.5) * 2.4 * 128, 0, 255));
      img.data[i * 4] = g;
      img.data[i * 4 + 1] = g;
      img.data[i * 4 + 2] = g;
      img.data[i * 4 + 3] = 255;
    }
    c.putImageData(img, 0, 0);
    tiles.push(canvas);
  }
  noiseByEnv.set(env, tiles);
  return tiles;
}

/** Зерно на весь кадр: плитка шага `tick`, сдвинутая на псевдослучайное место, пиксель зерна — `grain` px. */
function paintGrain(ctx: Ctx2D, env: RenderEnv, tick: number, seed: number, grain: number): void {
  const tiles = noiseTiles(env);
  if (!tiles) return;
  const tile = tiles[Math.floor(rand01(seed, tick * 3) * NOISE_TILES) % NOISE_TILES];
  const step = NOISE_TILE * Math.max(0.5, grain);
  const ox = -rand01(seed, tick * 3 + 1) * step;
  const oy = -rand01(seed, tick * 3 + 2) * step;
  ctx.imageSmoothingEnabled = false;
  for (let y = oy; y < env.height; y += step) {
    for (let x = ox; x < env.width; x += step) ctx.drawImage(src(tile), x, y, step, step);
  }
  ctx.imageSmoothingEnabled = true;
}

/* ---------- виды ---------- */

type FxCtx = { ctx: Ctx2D; env: RenderEnv; fx: ResolvedFx; local: number; dur: number; alpha: number; k: number };

function scanlines({ ctx, env, fx, local, alpha, k }: FxCtx): void {
  // Тёмная строка на каждый шаг size·k px, медленно ползёт вниз; лёгкое мерцание яркости.
  const step = Math.max(2, fx.size * k);
  const dark = step * 0.45;
  const offset = (local * fx.speed * 24 * k) % step;
  const flicker = 1 + 0.08 * noise1(fx.seed, local * 9 * fx.speed);
  ctx.globalAlpha = clamp01(alpha * fx.intensity * 0.85 * flicker);
  ctx.fillStyle = "#000000";
  ctx.beginPath();
  for (let y = offset - step; y < env.height; y += step) ctx.rect(0, y, env.width, dark);
  ctx.fill();
}

function lightLeak({ ctx, env, fx, local, alpha }: FxCtx): void {
  // 3–4 тёплых пятна плывут по эллипсу шире кадра — у краёв и в углах, дышат яркостью.
  const W = env.width;
  const H = env.height;
  const n = 4;
  for (let i = 0; i < n; i++) {
    const base = rand01(fx.seed, i * 5) * Math.PI * 2;
    const dir = rand01(fx.seed, i * 5 + 1) < 0.5 ? -1 : 1;
    const a = base + dir * local * fx.speed * (0.18 + 0.12 * rand01(fx.seed, i * 5 + 2));
    const cx = W * (0.5 + 0.62 * Math.cos(a));
    const cy = H * (0.5 + 0.56 * Math.sin(a * 0.8 + i));
    const r = Math.max(W, H) * (0.32 + 0.22 * rand01(fx.seed, i * 5 + 3));
    const breathe = 0.65 + 0.35 * Math.sin(local * fx.speed * (0.9 + i * 0.37) + base);
    const color = fx.colors[i % fx.colors.length];
    const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, r);
    const op = alpha * fx.intensity * 0.75 * breathe;
    g.addColorStop(0, rgba(color, op));
    g.addColorStop(0.45, rgba(color, op * 0.45));
    g.addColorStop(1, rgba(color, 0));
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, W, H);
  }
}

function flashFrame({ ctx, env, fx, local, dur, alpha }: FxCtx): void {
  const e = flashEnvelope(local, dur);
  ctx.globalAlpha = clamp01(alpha * fx.intensity * e);
  ctx.fillStyle = fx.color;
  ctx.fillRect(0, 0, env.width, env.height);
}

function grainOverlay({ ctx, env, fx, local, alpha, k }: FxCtx): void {
  // Новая плитка 24 раза в секунду (× speed) — «живое» киношное зерно.
  const tick = Math.floor(local * 24 * fx.speed);
  ctx.globalAlpha = clamp01(alpha * fx.intensity * 0.9);
  paintGrain(ctx, env, tick, fx.seed, fx.size * k);
}

function chromatic({ ctx, env, fx, local, alpha }: FxCtx): void {
  const b = buffers(env);
  if (!b || !snapshot(ctx, env, b)) return;
  // Красный крупнее, зелёный — вполовину: цветная кайма растёт от центра к краям; чуть дышит.
  const zoom = 0.018 * fx.intensity * (1 + 0.25 * Math.sin(local * fx.speed * 2.2));
  splitChannels(b, 0, zoom);
  ctx.globalAlpha = clamp01(alpha);
  ctx.drawImage(src(b.out.canvas), 0, 0, env.width, env.height);
}

function vhs({ ctx, env, fx, local, alpha, k }: FxCtx): void {
  const W = env.width;
  const H = env.height;
  const i = fx.intensity;
  const tick = Math.floor(local * 10 * fx.speed);
  const r = (n: number) => rand01(fx.seed, tick * 64 + n);
  const b = buffers(env);
  if (b && snapshot(ctx, env, b)) {
    // RGB-расслоение по горизонтали с дрожью.
    const shift = (2 + 6 * i) * k * (1 + 0.35 * noise1(fx.seed + 9, local * 6 * fx.speed));
    splitChannels(b, shift, 0);
    ctx.globalAlpha = clamp01(alpha);
    ctx.drawImage(src(b.out.canvas), 0, 0, W, H);
    // Срезы: 0–3 полосы на шаг сдвинуты вбок (с переносом через край).
    const bands = Math.floor(r(0) * (1 + 3 * i) + i * 0.6);
    for (let n = 0; n < bands; n++) {
      const h = (8 + 70 * r(10 + n)) * k;
      const y = r(20 + n) * (H - h);
      const dx = (r(30 + n) * 2 - 1) * (14 + 70 * i) * k;
      ctx.drawImage(src(b.out.canvas), 0, y, W, h, dx, y, W, h);
      ctx.drawImage(src(b.out.canvas), 0, y, W, h, dx - Math.sign(dx) * W, y, W, h);
    }
    // Полоса «трекинга»: раз в 5 с / speed ползёт сверху вниз, кадр в ней тянется вбок.
    const period = 5 / fx.speed;
    const u = (local % period) / period;
    const th = (40 + 80 * i) * k;
    const ty = u * (H + th * 2) - th;
    const tdx = (10 + 40 * i) * k * (0.5 + 0.5 * noise1(fx.seed + 31, local * 12));
    ctx.drawImage(src(b.out.canvas), 0, Math.max(0, ty), W, th, tdx, Math.max(0, ty), W, th);
    ctx.globalAlpha = clamp01(alpha * 0.18 * i);
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, ty + th * 0.4, W, th * 0.2);
  }
  // Шум и выпадения: зерно «перекрытием», редкие белые штрихи.
  ctx.globalCompositeOperation = "overlay";
  ctx.globalAlpha = clamp01(alpha * 0.55 * i);
  paintGrain(ctx, env, Math.floor(local * 24 * fx.speed), fx.seed, 1.4 * k);
  ctx.globalCompositeOperation = "source-over";
  ctx.fillStyle = "#ffffff";
  const drops = Math.floor(r(40) * 5 * i);
  for (let n = 0; n < drops; n++) {
    ctx.globalAlpha = clamp01(alpha * (0.35 + 0.4 * r(50 + n)));
    ctx.fillRect(r(60 + n) * W, r(70 + n) * H, (40 + 260 * r(80 + n)) * k, Math.max(1, 2 * k));
  }
}

const PAINT: Record<FxKind, (c: FxCtx) => void> = {
  vhs,
  scanlines,
  light_leak: lightLeak,
  chromatic,
  flash_frame: flashFrame,
  grain_overlay: grainOverlay,
};

/**
 * Нарисовать эффект поверх того, что уже в кадре. Положение и поворот слоя не действуют
 * (эффект во весь кадр); прозрачность слоя (ключи, fade, react opacity) гасит эффект целиком,
 * масштаб (ключи, react scale) умножает силу.
 */
export function drawFx(ctx: Ctx2D, layer: FxLayer, st: LayerState, env: RenderEnv): void {
  const fx = resolveFx(layer);
  const strength = clamp01(fx.intensity * Math.max(0, st.scale));
  if (strength <= 0.002 || st.opacity <= 0.002) return;
  ctx.save();
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.globalAlpha = 1;
  ctx.globalCompositeOperation = BLEND[fx.blend] ?? "source-over";
  ctx.shadowColor = "rgba(0,0,0,0)";
  ctx.shadowBlur = 0;
  PAINT[fx.kind]({
    ctx,
    env,
    fx: { ...fx, intensity: strength },
    local: st.local,
    dur: st.duration,
    alpha: st.opacity,
    k: layerUnit(env),
  });
  ctx.restore();
}

/** Ход вспышки flash_frame 0..1: быстрый подъём (≤ 0.06 с) и затухание к концу слоя. */
export function flashEnvelope(local: number, dur: number): number {
  const rise = Math.min(0.06, dur * 0.25);
  if (local < rise) return clamp01(local / Math.max(1e-3, rise));
  return Math.pow(1 - clamp01((local - rise) / Math.max(1e-3, dur - rise)), 2);
}
