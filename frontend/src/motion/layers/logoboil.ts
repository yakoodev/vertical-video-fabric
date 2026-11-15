/**
 * «Кипение» логотипа (`logo_reveal` с `boil` > 0) — покадровая рисованная анимация на двойках, как в
 * исходной анимации владельца: каждый рисунок (12 в секунду) слегка перерисован, эскиз — белый контур
 * от руки поверх тёмной туши, рывок — диагональные царапины, рваные полосы и смаз, уход — царапины стирают.
 *
 * Только операции холста (полосы drawImage, сдвиги, destination-out) — работает и в превью, и в рендере.
 * Всё детерминировано: номер рисунка `k` и `seed` → одни и те же царапины и сдвиги.
 */
import type { Ctx2D, ImageLike, ScratchCanvas } from "../types";

export const BOIL_FPS = 12;

export type GetCanvas = (tag: string, w: number, h: number) => { canvas: ScratchCanvas; ctx: Ctx2D } | null;

export function boilHash(seed: number, a: number, b = 0): number {
  let h = (Math.imul(seed | 0, 0x27d4eb2f) ^ Math.imul(a + 3, 0x165667b1) ^ Math.imul(b + 11, 0x9e3779b1)) >>> 0;
  h = Math.imul(h ^ (h >>> 15), 0x85ebca6b) >>> 0;
  h = Math.imul(h ^ (h >>> 13), 0xc2b2ae35) >>> 0;
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
}

/** Плавный сдвиг −1..1 вдоль координаты u (0..1) для рисунка k: сумма двух синусов со своими фазами. */
function wobble(seed: number, k: number, u: number, salt: number): number {
  const p1 = boilHash(seed, k, salt) * Math.PI * 2;
  const p2 = boilHash(seed, k, salt + 1) * Math.PI * 2;
  return 0.65 * Math.sin(u * Math.PI * 2 * 1.3 + p1) + 0.35 * Math.sin(u * Math.PI * 2 * 3.7 + p2);
}

/**
 * Картинка, перерисованная «от руки»: полосы по высоте сдвинуты плавной волной (рисунок k свой),
 * и весь рисунок чуть смещён. `amp` — размах в px холста.
 */
export function drawBoiled(ctx: Ctx2D, img: ImageLike, x: number, y: number, w: number, h: number, amp: number, seed: number, k: number): void {
  const iw = img.width || 1;
  const ih = img.height || 1;
  const strips = 32;
  const gx = (boilHash(seed, k, 21) - 0.5) * amp;
  const gy = (boilHash(seed, k, 22) - 0.5) * amp;
  for (let i = 0; i < strips; i++) {
    const u0 = i / strips;
    const sy = u0 * ih;
    const sh = ih / strips + 0.5;
    const dx = wobble(seed, k, u0, 5) * amp;
    // Полосы чуть перекрываются — без щелей на сдвиге.
    ctx.drawImage(img as unknown as CanvasImageSource, 0, sy, iw, Math.min(sh, ih - sy), x + dx + gx, y + u0 * h + gy, w, (h / strips) * 1.04 + 0.5);
  }
}

/**
 * Эскиз: из белых букв на `main` — тёмная тушь внутри и белый двойной контур (как контур пера, чуть
 * мимо заливки). `ring` — толщина линии в px холста.
 */
export function sketchOutline(main: { canvas: ScratchCanvas; ctx: Ctx2D }, getCanvas: GetCanvas, w: number, h: number, ring: number, seed: number, k: number): void {
  const out = getCanvas("boil-ring", w, h);
  if (!out) return;
  const src = main.canvas as unknown as CanvasImageSource;
  const o = out.ctx;
  const jx = (boilHash(seed, k, 31) - 0.5) * ring * 1.4;
  const jy = (boilHash(seed, k, 32) - 0.5) * ring * 1.4;
  for (let a = 0; a < 8; a++) {
    const ang = (a / 8) * Math.PI * 2;
    o.drawImage(src, Math.cos(ang) * ring + jx, Math.sin(ang) * ring + jy);
  }
  o.globalCompositeOperation = "destination-out";
  o.drawImage(src, jx * 0.4, jy * 0.4);
  // Перо местами отрывается: редкие поперечные разрывы контура.
  for (let i = 0; i < 7; i++) {
    const cx = boilHash(seed, k, 40 + i) * w;
    const cy = boilHash(seed, k, 50 + i) * h;
    o.fillRect(cx, cy, ring * 3, ring * 3);
  }
  o.globalCompositeOperation = "source-over";
  // Вторая, бледная обводка — рука прошла дважды.
  const m = main.ctx;
  m.globalCompositeOperation = "source-in";
  m.fillStyle = "rgba(8,8,10,0.62)";
  m.fillRect(0, 0, w, h);
  m.globalCompositeOperation = "source-over";
  m.drawImage(out.canvas as unknown as CanvasImageSource, 0, 0);
  m.globalAlpha = 0.45;
  m.drawImage(out.canvas as unknown as CanvasImageSource, -jx * 1.6 + ring * 0.6, -jy * 1.6 - ring * 0.4);
  m.globalAlpha = 1;
}

/** Сужающаяся диагональная царапина сухой кисти (подпуть). */
function scratchPath(ctx: Ctx2D, cx: number, cy: number, len: number, wd: number, ang: number): void {
  const ux = Math.cos(ang);
  const uy = Math.sin(ang);
  const nx = -uy;
  const ny = ux;
  const n = 10;
  const left: [number, number][] = [];
  const right: [number, number][] = [];
  for (let i = 0; i <= n; i++) {
    const u = i / n;
    const taper = Math.pow(Math.sin(Math.PI * Math.min(1, u * 1.15)), 0.7);
    const px = cx + ux * (u - 0.5) * len;
    const py = cy + uy * (u - 0.5) * len;
    left.push([px + (nx * wd * taper) / 2, py + (ny * wd * taper) / 2]);
    right.push([px - (nx * wd * taper) / 2, py - (ny * wd * taper) / 2]);
  }
  ctx.moveTo(left[0][0], left[0][1]);
  for (const [x, y] of left.slice(1)) ctx.lineTo(x, y);
  for (const [x, y] of right.reverse()) ctx.lineTo(x, y);
  ctx.closePath();
}

/** Царапины поверх рисунка: `count` штук внутри рамки (x0..x1, y0..y1), белые (или стирающие — op). */
export function drawScratches(
  ctx: Ctx2D,
  box: [number, number, number, number],
  count: number,
  width: [number, number],
  alpha: number,
  seed: number,
  k: number,
  op: GlobalCompositeOperation = "source-over",
): void {
  if (count <= 0 || alpha <= 0) return;
  const [x0, y0, x1, y1] = box;
  ctx.save();
  ctx.globalCompositeOperation = op;
  for (let i = 0; i < count; i++) {
    const h = (q: number) => boilHash(seed + 7, k * 97 + i, q);
    const ang = ((-58 + (h(1) - 0.5) * 18) * Math.PI) / 180;
    const len = (0.25 + 0.5 * h(2)) * (x1 - x0);
    const wd = width[0] + (width[1] - width[0]) * h(3);
    ctx.fillStyle = `rgba(255,255,255,${(alpha * (0.6 + 0.4 * h(4))).toFixed(3)})`;
    ctx.beginPath();
    scratchPath(ctx, x0 + h(5) * (x1 - x0), y0 + h(6) * (y1 - y0), len, wd, ang);
    ctx.fill();
  }
  ctx.restore();
}

/**
 * Рывок: рисунок на `main` рвётся полосами (сдвиги в стороны) и смазывается по диагонали; сила `g` 0..1.
 * `shift` — наибольший сдвиг полосы в px холста.
 */
export function glitchSmear(main: { canvas: ScratchCanvas; ctx: Ctx2D }, getCanvas: GetCanvas, w: number, h: number, g: number, shift: number, seed: number, k: number, angleDeg = 122): void {
  if (g <= 0) return;
  const tmp = getCanvas("boil-tmp", w, h);
  if (!tmp) return;
  tmp.ctx.drawImage(main.canvas as unknown as CanvasImageSource, 0, 0);
  const m = main.ctx;
  m.clearRect(0, 0, w, h);
  let y = 0;
  let i = 0;
  while (y < h) {
    const bh = Math.max(2, Math.round((0.02 + 0.07 * boilHash(seed, k, 200 + i)) * h));
    const moved = boilHash(seed, k, 300 + i) < 0.55;
    const dx = moved ? (boilHash(seed, k, 400 + i) * 2 - 1) * shift * g : 0;
    m.drawImage(tmp.canvas as unknown as CanvasImageSource, 0, y, w, Math.min(bh, h - y), dx, y, w, Math.min(bh, h - y));
    y += bh;
    i++;
  }
  // Смаз: копии со сдвигом вдоль диагонали и затуханием.
  const ang = (angleDeg * Math.PI) / 180;
  const len = shift * 1.5 * g;
  tmp.ctx.clearRect(0, 0, w, h);
  tmp.ctx.drawImage(main.canvas as unknown as CanvasImageSource, 0, 0);
  for (let s = 1; s <= 6; s++) {
    m.globalAlpha = (1 - s / 7) * 0.7;
    m.drawImage(tmp.canvas as unknown as CanvasImageSource, Math.cos(ang) * len * (s / 6), Math.sin(ang) * len * (s / 6));
  }
  m.globalAlpha = 1;
}

/**
 * Номер рисунка в момент `local` (секунды слоя): 12 в секунду; после рывка — петля из `loop`
 * рисунков (живой логотип можно держать сколько угодно, без шва).
 */
export function boilDrawing(local: number, loopFrom: number | null, loop: number, loopUntil: number): number {
  const k = Math.floor(local * BOIL_FPS + 1e-6);
  if (loopFrom === null || local < loopFrom || local >= loopUntil) return k;
  const k0 = Math.floor(loopFrom * BOIL_FPS + 1e-6);
  return k0 + ((k - k0) % Math.max(1, loop));
}
