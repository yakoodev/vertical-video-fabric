/**
 * Логотип, который пишется кистью (`logo_reveal`): картинка логотипа открывается вдоль его
 * мазков — так, как их вела бы рука. Мазки (осевые линии с толщиной) заранее извлекает сервер
 * (app/logo_vectorize.py: порог → скелет → граф → порядок) и кладёт в `props.logo`; движок их не
 * угадывает, а только проигрывает, поэтому кадр превью совпадает с кадром рендера.
 *
 * Кадр: маска из открытой части мазков (рваный «ворс» на кончике кисти, брызги-точки, которые
 * проявляются, когда кисть проходит мимо) → маской вырезается сама картинка (её пиксели, не
 * перерисовка) → летящие капли чернил → свечение цвета `glow` → удар (`hit`: пружина масштаба,
 * вспышка свечения и россыпь капель) → уход (`exit`: fade, wipe — стирание кистью, slash —
 * росчерк и разлёт половин).
 *
 * Никакой случайности: капли и ворс — хэш от `seed`, номера мазка и номера капли.
 */
import { clamp01 } from "../easing";
import type { Ctx2D, ImageLike, LogoRevealLayer, LogoRevealProps, LogoVector, RenderEnv, ScratchCanvas } from "../types";
import { rgba, type LayerRenderer } from "./common";
import { deviceScale } from "./counter";
import { BOIL_FPS, boilDrawing, drawBoiled, drawScratches, glitchSmear, sketchOutline } from "./logoboil";

export const LOGO_REVEALS = ["brush", "sweep", "ink"] as const;
export const LOGO_EXITS = ["none", "fade", "wipe", "slash", "scratch"] as const;
export const LOGO_DEFAULTS = {
  w: 900,
  draw: 1.1,
  glowSize: 26,
  glowStrength: 1,
  splatter: 0.5,
  brush: 0.6,
  hitScale: 1.07,
  hitFlash: 0.8,
  exitDur: 0.35,
  slashAngle: -16,
  burst: 0.3,
  loop: 6,
  scratches: 6,
};
/** Как у app/logo_vectorize.py: радиус открытия = r·spread + pad (доли ширины). */
const SPREAD = 1.35;
const PAD = 0.004;
/** Поле холста вокруг логотипа (доля ширины): там летят капли и светится ореол. */
const MARGIN = 0.14;
/** Проявление остатка (зубцы края, которых не коснулся ни один мазок) после последнего мазка, с. */
const SETTLE = 0.18;
/** Сколько длится вспышка точки-брызги, с. */
const DOT_POP = 0.07;

function hash01(seed: number, a: number, b = 0): number {
  let h = (Math.imul(seed | 0, 0x9e3779b1) ^ Math.imul(a + 1, 0x85ebca6b) ^ Math.imul(b + 7, 0xc2b2ae35)) >>> 0;
  h = Math.imul(h ^ (h >>> 15), 0x2c1b3c6d) >>> 0;
  h = Math.imul(h ^ (h >>> 12), 0x297a2d39) >>> 0;
  return ((h ^ (h >>> 15)) >>> 0) / 4294967296;
}

/* ---------- расписание мазков ---------- */

export type StrokeTiming = { index: number; t0: number; t1: number; len: number; group: number };
export type LogoSchedule = { strokes: StrokeTiming[]; end: number };

/** Длина мазка в долях ширины. */
export function strokeLength(p: number[]): number {
  let len = 0;
  for (let i = 3; i + 2 < p.length; i += 3) len += Math.hypot(p[i] - p[i - 3], p[i + 1] - p[i - 2]);
  return len;
}

/**
 * Когда пишется каждый мазок (секунды от начала письма): время мазка ∝ длина^0.75 (короткий
 * штрих не мелькает), между буквами (группами) — короткая пауза руки. Сумма — ровно `draw`.
 * `order` — свой порядок номеров мазков (остальные — следом, как в данных).
 */
export function logoSchedule(logo: LogoVector, draw: number, order?: number[] | null): LogoSchedule {
  const n = logo.strokes.length;
  const seq: number[] = [];
  const used = new Set<number>();
  for (const i of order ?? []) if (Number.isInteger(i) && i >= 0 && i < n && !used.has(i)) seq.push(i), used.add(i);
  for (let i = 0; i < n; i++) if (!used.has(i)) seq.push(i);
  const lens = seq.map((i) => strokeLength(logo.strokes[i].p));
  const weights = lens.map((l) => Math.pow(Math.max(l, 0.015), 0.75));
  const mean = weights.reduce((a, b) => a + b, 0) / Math.max(1, weights.length);
  const groupGap = mean * 0.35;
  let gaps = 0;
  for (let k = 1; k < seq.length; k++) if ((logo.strokes[seq[k]].g ?? 0) !== (logo.strokes[seq[k - 1]].g ?? 0)) gaps += groupGap;
  const total = weights.reduce((a, b) => a + b, 0) + gaps;
  const unit = total > 0 ? Math.max(0.05, draw) / total : 0;
  const out: StrokeTiming[] = [];
  let t = 0;
  seq.forEach((i, k) => {
    const g = logo.strokes[i].g ?? 0;
    if (k > 0 && g !== (logo.strokes[seq[k - 1]].g ?? 0)) t += groupGap * unit;
    const d = weights[k] * unit;
    out.push({ index: i, t0: t, t1: t + d, len: lens[k], group: g });
    t += d;
  });
  return { strokes: out, end: t };
}

/** Ход кисти внутри мазка 0..1: чуть разгоняется и тормозит, но не замирает на стыках мазков. */
export function brushEase(x: number): number {
  const p = clamp01(x);
  return 0.55 * p + 0.45 * (0.5 - 0.5 * Math.cos(Math.PI * p));
}

/** Доля открытого мазка в момент `t` (секунды от начала письма). */
export function strokeProgress(tm: StrokeTiming, t: number): number {
  if (t <= tm.t0) return 0;
  if (t >= tm.t1) return 1;
  return brushEase((t - tm.t0) / Math.max(1e-6, tm.t1 - tm.t0));
}

/** Момент (от начала письма), когда кисть проходит долю пути `u` мазка (обратная к brushEase). */
export function timeAtProgress(tm: StrokeTiming, u: number): number {
  let lo = 0;
  let hi = 1;
  for (let k = 0; k < 18; k++) {
    const mid = (lo + hi) / 2;
    if (brushEase(mid) < u) lo = mid;
    else hi = mid;
  }
  return tm.t0 + (tm.t1 - tm.t0) * (lo + hi) * 0.5;
}

/* ---------- параметры слоя ---------- */

type Resolved = {
  w: number;
  h: number;
  aspect: number;
  draw: number;
  delay: number;
  color: string | null;
  glow: string | null;
  glowSize: number;
  glowStrength: number;
  splatter: number;
  splatterColor: string;
  brush: number;
  seed: number;
  hitAt: number | null;
  hitScale: number;
  hitFlash: number;
  hitBurst: number;
  hitShake: number;
  boil: number;
  sketch: boolean;
  burst: number;
  loop: number;
  scratches: number;
  reveal: "brush" | "sweep" | "ink";
  exitKind: (typeof LOGO_EXITS)[number];
  exitDur: number;
  slashAngle: number;
  spread: number;
  pad: number;
};

function num(v: unknown, d: number): number {
  const n = Number(v);
  return v === null || v === undefined || v === "" || !Number.isFinite(n) ? d : n;
}

export function logoAspect(p: LogoRevealProps, img: ImageLike | null): number {
  const a = num(p.logo?.aspect, 0);
  if (a > 0) return a;
  if (img && img.width > 0 && img.height > 0) return img.height / img.width;
  return 0.4;
}

export function resolveLogo(p: LogoRevealProps, img: ImageLike | null): Resolved {
  const w = Math.max(20, num(p.w, LOGO_DEFAULTS.w));
  const aspect = logoAspect(p, img);
  const draw = Math.max(0.1, num(p.draw, LOGO_DEFAULTS.draw));
  const delay = Math.max(0, num(p.delay, 0));
  const hit = p.hit === undefined ? {} : p.hit;
  const exit = p.exit ?? null;
  const exitKind = (LOGO_EXITS as readonly string[]).includes(String(exit?.kind)) ? (exit!.kind as Resolved["exitKind"]) : "none";
  const color = p.color ?? null;
  return {
    w,
    h: w * aspect,
    aspect,
    draw,
    delay,
    color,
    glow: p.glow === undefined ? null : p.glow,
    glowSize: Math.max(0, num(p.glowSize, LOGO_DEFAULTS.glowSize)),
    glowStrength: Math.max(0, Math.min(3, num(p.glowStrength, LOGO_DEFAULTS.glowStrength))),
    splatter: clamp01(num(p.splatter, LOGO_DEFAULTS.splatter)),
    splatterColor: p.splatterColor || color || "#ffffff",
    brush: clamp01(num(p.brush, LOGO_DEFAULTS.brush)),
    seed: Math.round(num(p.seed, 7)),
    // Удар — по умолчанию в конце письма; `hit: null` — без удара.
    hitAt: hit === null ? null : Math.max(0, num(hit.at, delay + draw)),
    hitScale: hit === null ? 1 : Math.max(1, Math.min(1.5, num(hit.scale, LOGO_DEFAULTS.hitScale))),
    hitFlash: hit === null ? 0 : clamp01(num(hit.flash, LOGO_DEFAULTS.hitFlash)),
    hitBurst: hit === null ? 0 : clamp01(num(hit.burst, 1)),
    hitShake: hit === null ? 0 : Math.max(0, Math.min(80, num(hit.shake, 0))),
    reveal: p.reveal === "sweep" || p.reveal === "ink" ? p.reveal : "brush",
    // Кипение (рисунки на двойках): размах в опорных px; эскиз — контур от руки до рывка в заливку.
    boil: Math.max(0, Math.min(20, num(p.boil, 0))),
    sketch: p.sketch === true,
    burst: Math.max(0, Math.min(2, num(p.burst, LOGO_DEFAULTS.burst))),
    loop: Math.max(1, Math.min(48, Math.round(num(p.loop, LOGO_DEFAULTS.loop)))),
    scratches: Math.max(0, Math.min(24, Math.round(num(p.scratches, LOGO_DEFAULTS.scratches)))),
    exitKind,
    exitDur: Math.max(0.05, num(exit?.dur, LOGO_DEFAULTS.exitDur)),
    slashAngle: num(exit?.angle, LOGO_DEFAULTS.slashAngle),
    spread: Math.max(0.5, num(p.logo?.spread, SPREAD)),
    pad: Math.max(0, num(p.logo?.pad, PAD)),
  };
}

/** Масштаб удара в момент `local`: замах −2 % за 0.07 с, рывок до hitScale и затухающая пружина. */
export function hitScaleAt(r: Pick<Resolved, "hitAt" | "hitScale">, local: number): number {
  if (r.hitAt === null || r.hitScale <= 1) return 1;
  const dt = local - r.hitAt;
  const amp = r.hitScale - 1;
  if (dt < -0.07) return 1;
  if (dt < 0) return 1 - amp * 0.3 * (1 + dt / 0.07);
  if (dt > 0.6) return 1;
  return 1 + amp * Math.exp(-dt * 9) * Math.cos(dt * 24);
}

/* ---------- холсты ---------- */

const scratchPool = new Map<string, ScratchCanvas>();

function scratch(env: RenderEnv, tag: string, w: number, h: number): { canvas: ScratchCanvas; ctx: Ctx2D } | null {
  if (!env.createCanvas) return null;
  const key = `${tag}:${w}x${h}`;
  let c = scratchPool.get(key);
  if (!c) {
    if (scratchPool.size > 8) scratchPool.clear();
    c = env.createCanvas(w, h);
    scratchPool.set(key, c);
  }
  const ctx = c.getContext("2d") as Ctx2D;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.globalCompositeOperation = "source-over";
  ctx.globalAlpha = 1;
  ctx.filter = "none";
  ctx.clearRect(0, 0, w, h);
  return { canvas: c, ctx };
}

/* ---------- маска ---------- */

type Frame = { W: number; M: number; s: number };

/** Открытая часть мазка: капсулы по отрезкам до головы + рваный «ворс» на кончике. */
function drawStrokeMask(ctx: Ctx2D, p: number[], frac: number, r: Resolved, f: Frame, salt: number, tq: number): { x: number; y: number; a: number; R: number } | null {
  const n = Math.floor(p.length / 3);
  if (n < 1 || frac <= 0) return null;
  const k = f.W * f.s;
  const X = (i: number) => (p[i * 3] * f.W + f.M) * f.s;
  const Y = (i: number) => (p[i * 3 + 1] * f.W + f.M) * f.s;
  const R = (i: number) => (p[i * 3 + 2] * r.spread + r.pad) * k;
  const total = strokeLength(p) * k;
  // Голова чуть впереди тела: ворс дотягивается до неё неровно.
  const lead = frac < 1 ? R(0) * 0.55 * r.brush : 0;
  let left = Math.max(0, total * frac - lead);
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.strokeStyle = "#ffffff";
  ctx.fillStyle = "#ffffff";
  let hx = X(0);
  let hy = Y(0);
  let ang = 0;
  let hr = R(0);
  if (n === 1 || total <= 0) {
    ctx.beginPath();
    ctx.arc(hx, hy, hr, 0, Math.PI * 2);
    ctx.fill();
    return frac < 1 ? { x: hx, y: hy, a: 0, R: hr } : null;
  }
  ctx.beginPath();
  ctx.arc(hx, hy, hr * Math.min(1, frac * 8), 0, Math.PI * 2);
  ctx.fill();
  // Отрезки пачками близкой толщины — один stroke() на пачку.
  let batchW = -1;
  for (let i = 1; i < n; i++) {
    const ax = X(i - 1);
    const ay = Y(i - 1);
    const bx = X(i);
    const by = Y(i);
    const seg = Math.hypot(bx - ax, by - ay);
    if (seg <= 0) continue;
    const t = Math.min(1, left / seg);
    const ex = ax + (bx - ax) * t;
    const ey = ay + (by - ay) * t;
    const wdt = 2 * (R(i - 1) + (R(i) - R(i - 1)) * t);
    if (batchW < 0 || Math.abs(wdt - batchW) > batchW * 0.12) {
      if (batchW >= 0) ctx.stroke();
      ctx.beginPath();
      ctx.lineWidth = wdt;
      batchW = wdt;
      ctx.moveTo(ax, ay);
    }
    ctx.lineTo(ex, ey);
    hx = ex;
    hy = ey;
    ang = Math.atan2(by - ay, bx - ax);
    hr = wdt / 2;
    left -= seg;
    if (left <= 0) break;
  }
  if (batchW >= 0) ctx.stroke();
  if (frac >= 1) return null;
  // Ворс: пятна по ширине кисти, одни дотягиваются вперёд, другие отстают — рваный фронт.
  const bristles = 9;
  const nx = -Math.sin(ang);
  const ny = Math.cos(ang);
  const tx = Math.cos(ang);
  const ty = Math.sin(ang);
  for (let b = 0; b < bristles; b++) {
    const across = ((b + 0.5) / bristles - 0.5) * 2 * hr * 0.92;
    const reach = (hash01(r.seed + salt, b, tq) * 1.15 - 0.15) * lead * 1.6;
    const rb = hr * (0.22 + 0.22 * hash01(r.seed + salt + 3, b, tq));
    ctx.beginPath();
    ctx.arc(hx + nx * across + tx * reach, hy + ny * across + ty * reach, rb, 0, Math.PI * 2);
    ctx.fill();
  }
  return { x: hx + tx * lead, y: hy + ty * lead, a: ang, R: hr };
}

/* ---------- капли ---------- */

type Drop = { x: number; y: number; px: number; py: number; size: number; alpha: number };

/**
 * Капли, которые срываются с кисти: на мазок — n вылетов по длине (тем больше, чем выше
 * splatter), у каждого свой момент — когда кисть проходит это место. Летят вбок от хода
 * кисти с сопротивлением, оседают; часть прилипает и остаётся (как брызги исходника).
 */
function headDrops(logo: LogoVector, sched: LogoSchedule, r: Resolved, f: Frame, t: number, out: Drop[]): void {
  if (r.splatter <= 0) return;
  const k = f.W * f.s;
  for (const tm of sched.strokes) {
    if (t < tm.t0) continue;
    const p = logo.strokes[tm.index].p;
    const npts = Math.floor(p.length / 3);
    if (npts < 2) continue;
    const count = Math.round((tm.len / 0.05) * r.splatter * 1.6);
    for (let e = 0; e < count; e++) {
      const u = (e + 0.3 + 0.4 * hash01(r.seed, tm.index * 131 + e, 1)) / Math.max(1, count);
      const born = timeAtProgress(tm, u);
      const age = t - born;
      if (age < 0) continue;
      // Точка мазка на доле пути u и направление хода.
      const at = pointAt(p, u);
      const per = 1 + Math.floor(hash01(r.seed, tm.index * 131 + e, 2) * 2.2);
      for (let j = 0; j < per; j++) {
        const h = (q: number) => hash01(r.seed + q, tm.index * 977 + e * 13 + j, q);
        const side = h(3) < 0.5 ? -1 : 1;
        const dir = at.a + side * (Math.PI / 2 - 0.25 + h(4) * 0.9) - side * 0.35;
        const dist = (0.012 + 0.05 * h(5) * h(5)) * (0.6 + r.splatter) * k;
        const drag = 13;
        const stick = h(6) < 0.42;
        const life = stick ? Infinity : 0.25 + 0.2 * h(7);
        if (age > life) continue;
        const travel = (a: number) => dist * (1 - Math.exp(-drag * a));
        const fall = (a: number) => 0.09 * k * a * a * (stick ? 0.4 : 1);
        const x0 = (at.x * f.W + f.M) * f.s;
        const y0 = (at.y * f.W + f.M) * f.s;
        const a1 = Math.max(0, age - 0.022);
        const size = (0.0018 + 0.0055 * h(8) * h(8)) * k * (stick ? 1 : 0.85);
        const alpha = stick ? 0.92 : 1 - age / life;
        out.push({
          x: x0 + Math.cos(dir) * travel(age),
          y: y0 + Math.sin(dir) * travel(age) + fall(age),
          px: x0 + Math.cos(dir) * travel(a1),
          py: y0 + Math.sin(dir) * travel(a1) + fall(a1),
          size,
          alpha,
        });
      }
    }
  }
}

/** Россыпь капель на ударе: из точек мазков наружу от центра логотипа, гаснут за ~0.45 с. */
function burstDrops(logo: LogoVector, r: Resolved, f: Frame, local: number, out: Drop[]): void {
  if (r.hitAt === null || r.hitBurst <= 0 || r.splatter <= 0) return;
  const age = local - r.hitAt;
  if (age < 0 || age > 0.55) return;
  const k = f.W * f.s;
  const count = Math.round(34 * r.splatter * r.hitBurst);
  const cx = 0.5;
  const cy = r.aspect / 2;
  for (let i = 0; i < count; i++) {
    const h = (q: number) => hash01(r.seed + 101 + q, i, q);
    const st = logo.strokes[Math.floor(h(1) * logo.strokes.length) % Math.max(1, logo.strokes.length)];
    if (!st) continue;
    const at = pointAt(st.p, h(2));
    const dir = Math.atan2(at.y - cy, at.x - cx) + (h(3) - 0.5) * 0.9;
    const dist = (0.03 + 0.11 * h(4)) * k;
    const life = 0.3 + 0.25 * h(5);
    if (age > life) continue;
    const travel = (a: number) => dist * (1 - Math.exp(-10 * a));
    const x0 = (at.x * f.W + f.M) * f.s;
    const y0 = (at.y * f.W + f.M) * f.s;
    const a1 = Math.max(0, age - 0.025);
    out.push({
      x: x0 + Math.cos(dir) * travel(age),
      y: y0 + Math.sin(dir) * travel(age) + 0.08 * k * age * age,
      px: x0 + Math.cos(dir) * travel(a1),
      py: y0 + Math.sin(dir) * travel(a1) + 0.08 * k * a1 * a1,
      size: (0.002 + 0.005 * h(6)) * k,
      alpha: 1 - age / life,
    });
  }
}

/** Точка мазка на доле пути `u` (доли ширины) и направление хода. */
export function pointAt(p: number[], u: number): { x: number; y: number; a: number } {
  const n = Math.floor(p.length / 3);
  if (n < 2) return { x: p[0] ?? 0, y: p[1] ?? 0, a: 0 };
  const total = strokeLength(p);
  let left = clamp01(u) * total;
  for (let i = 1; i < n; i++) {
    const ax = p[(i - 1) * 3];
    const ay = p[(i - 1) * 3 + 1];
    const bx = p[i * 3];
    const by = p[i * 3 + 1];
    const seg = Math.hypot(bx - ax, by - ay);
    if (left <= seg || i === n - 1) {
      const t = seg > 0 ? Math.min(1, left / seg) : 0;
      return { x: ax + (bx - ax) * t, y: ay + (by - ay) * t, a: Math.atan2(by - ay, bx - ax) };
    }
    left -= seg;
  }
  return { x: p[(n - 1) * 3], y: p[(n - 1) * 3 + 1], a: 0 };
}

function drawDrops(ctx: Ctx2D, drops: Drop[], color: string): void {
  ctx.lineCap = "round";
  for (const d of drops) {
    if (d.alpha <= 0.01) continue;
    ctx.strokeStyle = rgba(color, d.alpha);
    ctx.lineWidth = d.size * 2;
    ctx.beginPath();
    ctx.moveTo(d.px, d.py);
    // Неподвижная капля — точка (отрезок нулевой длины с круглым концом).
    ctx.lineTo(d.x + (d.x === d.px && d.y === d.py ? 0.01 : 0), d.y);
    ctx.stroke();
  }
}

/* ---------- уход ---------- */

/** Рваный край стирания: сумма синусов по высоте (детерминированно). */
function raggedX(seed: number, y: number, amp: number): number {
  return amp * (Math.sin(y * 0.031 + seed) * 0.5 + Math.sin(y * 0.093 + seed * 1.7) * 0.3 + Math.sin(y * 0.27 + seed * 2.3) * 0.2);
}

/* ---------- рендерер ---------- */

function imageRef(p: LogoRevealProps): string {
  return String(p.asset_id ?? p.src ?? "");
}

export const logoRevealRenderer: LayerRenderer<LogoRevealLayer> = {
  multipart: true,
  measure(_ctx, layer, _st, env) {
    const img = env.getImage(imageRef(layer.props));
    const r = resolveLogo(layer.props, img);
    return { w: r.w, h: r.h };
  },
  draw(ctx, layer, st, env) {
    const p = layer.props;
    const img = env.getImage(imageRef(p));
    if (!img) return;
    const r = resolveLogo(p, img);
    const local = st.local;
    const boilOn = r.boil > 0;
    const exitStart = st.duration - (r.exitKind !== "none" ? r.exitDur : 0);
    // Рисунок k (12 в секунду); после рывка — петля из loop рисунков до ухода.
    const kDraw = boilOn ? boilDrawing(local, r.delay + r.draw + (r.sketch ? r.burst : 0), r.loop, exitStart) : 0;
    const tl = boilOn ? Math.min(local, Math.floor(local * BOIL_FPS + 1e-6) / BOIL_FPS) : local;
    const t = tl - r.delay;
    if (t <= 0 && r.delay > 0) return;
    const logo = p.logo && Array.isArray(p.logo.strokes) && p.logo.strokes.length ? p.logo : null;
    const sched = logo ? logoSchedule(logo, r.draw, p.order) : { strokes: [], end: r.draw };
    const ds = deviceScale(ctx);
    const M = r.w * MARGIN;
    // Холст — в пикселях экрана (не мылится на любом масштабе), не больше 2400 px по стороне.
    const s = Math.min(ds, 2400 / (r.w + 2 * M), 2400 / (r.h + 2 * M));
    const cw = Math.max(1, Math.ceil((r.w + 2 * M) * s));
    const ch = Math.max(1, Math.ceil((r.h + 2 * M) * s));
    const main = scratch(env, "logo", cw, ch);
    const f: Frame = { W: r.w, M, s };
    const k = r.hitAt !== null ? hitScaleAt(r, local) : 1;

    // Уход: доля 0..1 на последних exitDur секундах слоя.
    const exitE = r.exitKind !== "none" ? clamp01((local - (st.duration - r.exitDur)) / r.exitDur) : 0;

    if (!main) {
      // Без промежуточного холста (старый браузер): картинка открывается слева направо.
      const e = clamp01(t / r.draw);
      ctx.save();
      ctx.beginPath();
      ctx.rect(-r.w / 2, -r.h / 2, r.w * e, r.h);
      ctx.clip();
      ctx.drawImage(img as unknown as CanvasImageSource, -r.w / 2, -r.h / 2, r.w, r.h);
      ctx.restore();
      return;
    }
    const mc = main.ctx;
    const heads: { x: number; y: number; a: number; R: number }[] = [];
    // 1. Маска: открытые мазки, точки-брызги, проявление остатка.
    if (logo && r.reveal === "brush") {
      const tq = boilOn ? kDraw : Math.floor(local * 24);
      for (const tm of sched.strokes) {
        const frac = strokeProgress(tm, t);
        if (frac <= 0) continue;
        const head = drawStrokeMask(mc, logo.strokes[tm.index].p, frac, r, f, tm.index * 17, tq);
        if (head) heads.push(head);
      }
      const byIndex = new Map(sched.strokes.map((tm) => [tm.index, tm] as const));
      mc.fillStyle = "#ffffff";
      for (const d of logo.dots ?? []) {
        const tm = byIndex.get(Number(d[3]));
        if (!tm) continue;
        const td = timeAtProgress(tm, Number(d[4]) || 0) + 0.04;
        const g = clamp01((t - td) / DOT_POP);
        if (g <= 0) continue;
        const rr = (Number(d[2]) * 1.6 + r.pad) * r.w * s * (g < 1 ? 0.6 + 0.6 * g : 1);
        mc.beginPath();
        mc.arc((Number(d[0]) * r.w + M) * s, (Number(d[1]) * r.w + M) * s, rr, 0, Math.PI * 2);
        mc.fill();
      }
      const settle = clamp01((t - sched.end) / SETTLE);
      if (settle > 0) {
        mc.globalAlpha = settle;
        mc.fillRect(0, 0, cw, ch);
        mc.globalAlpha = 1;
      }
    } else if (r.reveal === "ink") {
      // Пятно чернил растекается из центра рваным краем — буквы проступают изнутри наружу.
      const e = clamp01(t / r.draw);
      const grow = 1 - Math.pow(1 - e, 2.2);
      const cx = cw / 2;
      const cy = ch / 2;
      const rad = Math.hypot(cw, ch) * 0.55 * grow;
      mc.fillStyle = "#ffffff";
      mc.beginPath();
      const steps = 72;
      for (let i = 0; i <= steps; i++) {
        const a = (i / steps) * Math.PI * 2;
        const wob = 1 + 0.18 * Math.sin(a * 5 + r.seed) + 0.1 * Math.sin(a * 11 + r.seed * 2.1) + 0.06 * Math.sin(a * 23 + local * 9);
        const x = cx + Math.cos(a) * rad * wob * (cw / Math.max(cw, ch));
        const y = cy + Math.sin(a) * rad * wob * (ch / Math.max(cw, ch)) * 1.6;
        if (i === 0) mc.moveTo(x, y);
        else mc.lineTo(x, y);
      }
      mc.closePath();
      mc.fill();
    } else {
      // Полоса света (sweep) или мазков нет: фронт идёт слева направо — у кисти рваный, у света ровный.
      const e = brushEase(clamp01(t / r.draw));
      const front = -M * s + (r.w + 2 * M) * s * e;
      mc.fillStyle = "#ffffff";
      mc.beginPath();
      mc.moveTo(0, 0);
      const rag = r.reveal === "sweep" ? 0 : r.w * 0.03 * r.brush;
      const skewS = r.reveal === "sweep" ? Math.tan((18 * Math.PI) / 180) : 0;
      for (let y = 0; y <= ch; y += 6) mc.lineTo(Math.max(0, front + (ch / 2 - y) * skewS + raggedX(r.seed, y / s, rag) * s), y);
      mc.lineTo(0, ch);
      mc.closePath();
      mc.fill();
    }
    // 2. Картинка сквозь маску (её собственные пиксели) и, если задан, цвет поверх.
    mc.globalCompositeOperation = "source-in";
    mc.imageSmoothingEnabled = true;
    mc.imageSmoothingQuality = "high";
    // Полосы кипения рисуются на своём холсте: source-in каждой полосы стёр бы остальные.
    const boiled = boilOn ? scratch(env, "boil-img", cw, ch) : null;
    if (boiled) {
      drawBoiled(boiled.ctx, img, M * s, M * s, r.w * s, r.h * s, r.boil * s, r.seed, kDraw);
      mc.drawImage(boiled.canvas as unknown as CanvasImageSource, 0, 0);
    }
    else mc.drawImage(img as unknown as CanvasImageSource, M * s, M * s, r.w * s, r.h * s);
    if (r.color) {
      mc.fillStyle = rgba(r.color);
      mc.fillRect(0, 0, cw, ch);
    }
    if (r.reveal === "sweep" && t < r.draw + 0.35) {
      const e = brushEase(clamp01(t / r.draw));
      const front = -M * s + (r.w + 2 * M) * s * e;
      const band = r.w * 0.09 * s;
      const skewS = Math.tan((18 * Math.PI) / 180);
      const fadeOut = 1 - clamp01((t - r.draw) / 0.35);
      mc.globalCompositeOperation = "source-atop";
      mc.save();
      mc.transform(1, 0, -skewS, 1, skewS * ch / 2, 0);
      const g = mc.createLinearGradient(front - band, 0, front + band * 0.3, 0);
      g.addColorStop(0, rgba(r.glow || "#ffffff", 0));
      g.addColorStop(0.75, rgba("#ffffff", 0.95 * fadeOut));
      g.addColorStop(1, rgba("#ffffff", 0));
      mc.fillStyle = g;
      mc.fillRect(front - band, -ch, band * 1.3, ch * 3);
      mc.restore();
    }
    mc.globalCompositeOperation = "source-over";
    // 2б. Кипение: эскиз контуром до конца письма, потом рывок царапинами в заливку.
    const getCanvas = (tag: string, w: number, h: number) => scratch(env, tag, w, h);
    const box: [number, number, number, number] = [(M - r.w * 0.08) * s, (M - r.h * 0.3) * s, (M + r.w * 1.08) * s, (M + r.h * 1.3) * s];
    if (r.sketch && t < r.draw) {
      sketchOutline(main, getCanvas, cw, ch, Math.max(1, r.w * 0.0035 * s), r.seed, kDraw);
    } else if (r.sketch && t < r.draw + r.burst) {
      const g = 1 - (t - r.draw) / Math.max(1e-6, r.burst);
      glitchSmear(main, getCanvas, cw, ch, g, r.w * 0.045 * s, r.seed, kDraw);
      drawScratches(mc, box, r.scratches, [r.w * 0.004 * s, r.w * 0.02 * s], 0.95 * g + 0.15, r.seed, kDraw);
    }
    // 3. Капли чернил с кисти и с удара.
    if (logo) {
      const drops: Drop[] = [];
      if (r.reveal === "brush") headDrops(logo, sched, r, f, t, drops);
      burstDrops(logo, r, f, local, drops);
      drawDrops(mc, drops, r.splatterColor);
    }
    // 4. Уход: стирание кистью — до свечения, чтобы ореол стирался вместе с буквами.
    if (r.exitKind === "wipe" && exitE > 0) {
      const e = exitE * exitE * (3 - 2 * exitE);
      const front = -M * s + (r.w + 2 * M) * s * 1.15 * e;
      const skew = Math.tan((r.slashAngle * Math.PI) / 180);
      mc.globalCompositeOperation = "destination-out";
      mc.beginPath();
      mc.moveTo(-2, -2);
      for (let y = 0; y <= ch; y += 6) mc.lineTo(front + (y - ch / 2) * skew + raggedX(r.seed + 9, y / s, r.w * 0.025) * s, y);
      mc.lineTo(-2, ch + 2);
      mc.closePath();
      mc.fill();
      mc.globalCompositeOperation = "source-over";
    }

    if (r.exitKind === "scratch" && exitE > 0) {
      // Уход царапинами: смаз, стирающие царапины (больше и шире к концу) и последние белые.
      const kk = boilOn ? kDraw : Math.floor(local * BOIL_FPS);
      glitchSmear(main, getCanvas, cw, ch, exitE * 0.8, r.w * 0.06 * s, r.seed + 3, kk, -58);
      drawScratches(mc, box, Math.round(6 + 18 * exitE), [r.w * 0.01 * s, r.w * (0.04 + 0.06 * exitE) * s], Math.min(1, exitE * 1.6), r.seed + 5, kk, "destination-out");
      drawScratches(mc, box, Math.max(2, Math.round(r.scratches / 2)), [r.w * 0.004 * s, r.w * 0.02 * s], 0.8 * (1 - exitE), r.seed, kk);
    }

    // 5. Вывод: свечение под буквами, буквы, блики на кончике кисти.
    ctx.save();
    if (r.hitAt !== null && r.hitShake > 0 && local >= r.hitAt && local < r.hitAt + 0.5) {
      // Тряска на ударе: затухающая, по хэшу кадра (без случайности).
      const dt = local - r.hitAt;
      const amp = r.hitShake * Math.exp(-dt * 9);
      const q = Math.floor(local * 60);
      ctx.translate((hash01(r.seed, q, 1) - 0.5) * 2 * amp, (hash01(r.seed, q, 2) - 0.5) * 2 * amp);
    }
    if (k !== 1) ctx.scale(k, k);
    let alpha = 1;
    if (r.exitKind === "fade") alpha = 1 - exitE;
    else if (r.exitKind === "scratch") alpha = 1 - exitE * exitE * exitE;
    const flash = r.hitAt !== null && r.hitFlash > 0 ? r.hitFlash * Math.max(0, Math.exp(-Math.max(0, local - r.hitAt) * 5)) * (local >= r.hitAt ? 1 : 0) : 0;
    const dx = -r.w / 2 - M;
    const dy = -r.h / 2 - M;
    const dw = r.w + 2 * M;
    const dh = r.h + 2 * M;
    const paint = (offX: number, offY: number, a: number) => {
      if (a <= 0.003) return;
      if (r.glow && r.glowStrength > 0 && r.glowSize > 0) {
        const smoke = r.reveal === "ink" ? 1 - clamp01(t / (r.draw * 1.2)) : 0;
        drawGlow(ctx, env, main.canvas, cw, ch, r.glow, r.glowSize * s * (1 + smoke * 3), (r.glowStrength * 0.9 + flash * 1.3 + smoke * 1.2) * a, dx + offX, dy + offY, dw, dh);
      }
      ctx.globalAlpha = a;
      ctx.drawImage(main.canvas as unknown as CanvasImageSource, dx + offX, dy + offY, dw, dh);
      ctx.globalAlpha = 1;
    };
    if (r.exitKind === "slash" && exitE > 0) {
      drawSlashExit(ctx, r, exitE, paint, dw, dh);
    } else {
      paint(0, 0, alpha);
    }
    // Влажный блик на кончике кисти: тёплое пятно цвета свечения, «light» поверх.
    if (heads.length && r.exitKind !== "slash") {
      const gc = r.glow || "#ffffff";
      ctx.globalCompositeOperation = "lighter";
      for (const hd of heads) {
        const x = dx + hd.x / s;
        const y = dy + hd.y / s;
        const rad = (hd.R / s) * 2.2;
        const g = ctx.createRadialGradient(x, y, 0, x, y, rad);
        g.addColorStop(0, rgba(gc, 0.55 * alpha));
        g.addColorStop(1, rgba(gc, 0));
        ctx.fillStyle = g;
        ctx.fillRect(x - rad, y - rad, rad * 2, rad * 2);
      }
      ctx.globalCompositeOperation = "source-over";
    }
    ctx.restore();
  },
};

/** Свечение: копия слоя размыта на уменьшенном холсте, перекрашена в цвет ореола и подложена. */
function drawGlow(
  ctx: Ctx2D,
  env: RenderEnv,
  src: ScratchCanvas,
  cw: number,
  ch: number,
  color: string,
  sizePx: number,
  strength: number,
  dx: number,
  dy: number,
  dw: number,
  dh: number,
): void {
  if (strength <= 0.003) return;
  const q = 4;
  const gw = Math.max(1, Math.ceil(cw / q));
  const gh = Math.max(1, Math.ceil(ch / q));
  const layers: [number, number][] = [
    [sizePx, Math.min(1, strength * 0.75)],
    [sizePx * 0.35, Math.min(1, strength * 0.6)],
  ];
  layers.forEach(([blur, a], i) => {
    const g = scratch(env, `glow${i}`, gw, gh);
    if (!g) return;
    g.ctx.filter = `blur(${Math.max(0.5, blur / q).toFixed(2)}px)`;
    g.ctx.drawImage(src as unknown as CanvasImageSource, 0, 0, gw, gh);
    g.ctx.filter = "none";
    g.ctx.globalCompositeOperation = "source-in";
    g.ctx.fillStyle = rgba(color);
    g.ctx.fillRect(0, 0, gw, gh);
    g.ctx.globalCompositeOperation = "source-over";
    // Ореол сильнее одного прохода: при strength > 1 кладём его дважды.
    const passes = strength > 1.05 && i === 0 ? 2 : 1;
    for (let k = 0; k < passes; k++) {
      ctx.globalAlpha = Math.min(1, a);
      ctx.drawImage(g.canvas as unknown as CanvasImageSource, dx, dy, dw, dh);
    }
    ctx.globalAlpha = 1;
  });
}

/**
 * Уход «slash»: яркий росчерк пересекает логотип по диагонали (первые 30 %), затем половины по
 * обе стороны росчерка разъезжаются вдоль него и гаснут.
 */
function drawSlashExit(
  ctx: Ctx2D,
  r: Resolved,
  e: number,
  paint: (offX: number, offY: number, a: number) => void,
  dw: number,
  dh: number,
): void {
  const ang = (r.slashAngle * Math.PI) / 180;
  const ux = Math.cos(ang);
  const uy = Math.sin(ang);
  const nx = -uy;
  const ny = ux;
  const half = Math.hypot(dw, dh) / 2;
  const split = clamp01((e - 0.22) / 0.78);
  const sp = split * split;
  const shift = sp * r.w * 0.22;
  const gap = sp * r.h * 0.18;
  const a = Math.pow(1 - split, 1.4);
  for (const side of [-1, 1]) {
    ctx.save();
    ctx.beginPath();
    // Полуплоскость по одну сторону линии росчерка (через центр).
    ctx.moveTo(-ux * half * 2, -uy * half * 2);
    ctx.lineTo(ux * half * 2, uy * half * 2);
    ctx.lineTo(ux * half * 2 + nx * side * half * 2, uy * half * 2 + ny * side * half * 2);
    ctx.lineTo(-ux * half * 2 + nx * side * half * 2, -uy * half * 2 + ny * side * half * 2);
    ctx.closePath();
    ctx.clip();
    paint(-side * ux * shift + nx * side * gap, -side * uy * shift + ny * side * gap, a);
    ctx.restore();
  }
  // Сам росчерк: растёт слева направо, потом гаснет.
  const grow = clamp01(e / 0.3);
  const fade = 1 - clamp01((e - 0.3) / 0.45);
  if (fade <= 0) return;
  const len = half * 1.1;
  const x0 = -ux * len;
  const y0 = -uy * len;
  const x1 = x0 + ux * 2 * len * grow;
  const y1 = y0 + uy * 2 * len * grow;
  ctx.save();
  ctx.lineCap = "round";
  ctx.globalCompositeOperation = "lighter";
  const glow = r.glow || "#ffffff";
  ctx.strokeStyle = rgba(glow, 0.5 * fade);
  ctx.lineWidth = r.h * 0.07;
  ctx.beginPath();
  ctx.moveTo(x0, y0);
  ctx.lineTo(x1, y1);
  ctx.stroke();
  ctx.strokeStyle = rgba("#ffffff", 0.95 * fade);
  ctx.lineWidth = r.h * 0.018;
  ctx.beginPath();
  ctx.moveTo(x0, y0);
  ctx.lineTo(x1, y1);
  ctx.stroke();
  ctx.restore();
}
