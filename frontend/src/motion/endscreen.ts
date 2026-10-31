/**
 * Концовка-баннер (`render.end_screen`, app/end_screen.py): на последних секундах ролика основа
 * плавно уходит в размытие и чуть темнеет, поверх — анимированный баннер (обычный слой).
 *
 * Это фильтр основы, а не слой: компоновщик (node/motion-render.ts) и превью редактора
 * (CandidatesTab) зовут одну и ту же `paintEndScreen` между основой и слоями — размыто всё, что
 * под слоями (видео, фон раскладки, слои `behind`), а слои, баннер и субтитры остаются резкими.
 */
import { clamp01, ease } from "./easing";
import type { ScratchCanvas } from "./types";

/** comp.endScreen: с `start` (время композиции) за `ramp` с размытие растёт до `blur`, затемнение — до `dim`. */
export type EndScreenFx = {
  start: number;
  ramp: number;
  /** Сила размытия, px опорного кадра (короткая сторона 1080; CSS blur — сигма). */
  blur: number;
  /** Затемнение основы 0..0.8 (чёрный поверх с этой непрозрачностью). */
  dim: number;
};

export const END_SCREEN_MAX_BLUR = 60;
export const END_SCREEN_MAX_DIM = 0.8;

/** Мусор → null; числа зажаты. */
export function readEndScreen(raw: unknown): EndScreenFx | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const start = Number(r.start);
  if (!Number.isFinite(start)) return null;
  const num = (v: unknown, lo: number, hi: number, def: number) => {
    const n = Number(v);
    return Number.isFinite(n) ? Math.max(lo, Math.min(hi, n)) : def;
  };
  const fx = {
    start: Math.max(0, start),
    ramp: num(r.ramp, 0, 5, 0.6),
    blur: num(r.blur, 0, END_SCREEN_MAX_BLUR, 18),
    dim: num(r.dim, 0, END_SCREEN_MAX_DIM, 0.35),
  };
  return fx.blur > 0 || fx.dim > 0 ? fx : null;
}

/** Слой баннера концовки (app/end_screen.END_LAYER_ID). */
export const END_SCREEN_LAYER_ID = "end_banner";

/**
 * comp.endScreen из `render.end_screen` клипа — зеркало app/end_screen.comp_end_screen: начало —
 * начало слоя баннера (в шкале этих слоёв), без слоя — `duration − end_screen.duration`.
 */
export function endScreenFor(
  raw: unknown,
  layers: { id: string; start: number; hidden?: boolean }[],
  duration: number,
): EndScreenFx | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const banner = layers.find((l) => l.id === END_SCREEN_LAYER_ID);
  // Баннер удалили руками — концовка снята целиком (app/clip_settings._end_screen).
  if (!banner && r.asset_id) return null;
  const len = Number(r.duration) || 3;
  return readEndScreen({ ...r, start: banner ? banner.start : Math.max(0, duration - len) });
}

/** Сила эффекта в момент t: null — ещё не началась. Ход — inOutCubic за `ramp`. */
export function endScreenAt(fx: EndScreenFx | null | undefined, t: number): { blur: number; dim: number; k: number } | null {
  if (!fx || t < fx.start) return null;
  const k = fx.ramp > 0 ? ease("inOutCubic", clamp01((t - fx.start) / fx.ramp)) : 1;
  if (k <= 0) return null;
  return { blur: fx.blur * k, dim: fx.dim * k, k };
}

type Ctx = {
  canvas?: unknown;
  save(): void;
  restore(): void;
  setTransform(a: number, b: number, c: number, d: number, e: number, f: number): void;
  drawImage(...args: unknown[]): void;
  fillRect(x: number, y: number, w: number, h: number): void;
  clearRect(x: number, y: number, w: number, h: number): void;
  fillStyle: unknown;
  filter: string;
  imageSmoothingEnabled: boolean;
  imageSmoothingQuality?: string;
  globalAlpha: number;
  globalCompositeOperation: string;
};

const scratchCache = new WeakMap<object, Map<string, ScratchCanvas>>();

function scratchOf(make: (w: number, h: number) => ScratchCanvas, w: number, h: number): ScratchCanvas {
  let byFactory = scratchCache.get(make);
  if (!byFactory) scratchCache.set(make, (byFactory = new Map()));
  const key = `${w}x${h}`;
  let c = byFactory.get(key);
  if (!c) byFactory.set(key, (c = make(w, h)));
  return c;
}

/**
 * Размыть и притемнить то, что уже нарисовано на холсте W×H (основа), на момент t.
 *
 * Размытие считается на уменьшенной копии (сигма ≥ 4 px — в 2–4 раза меньше: результат тот же,
 * а Skia компоновщика и браузер считают его в разы быстрее) и растягивается обратно с запасом
 * по краям — у кромки кадра нет тёмной каймы от «пустоты» за краем. Возвращает, было ли что
 * рисовать.
 */
export function paintEndScreen(
  ctx: unknown,
  source: unknown,
  W: number,
  H: number,
  fx: EndScreenFx | null | undefined,
  t: number,
  createCanvas: (w: number, h: number) => ScratchCanvas,
): boolean {
  const at = endScreenAt(fx, t);
  if (!at) return false;
  const c = ctx as Ctx;
  const unit = Math.min(W, H) / 1080;
  const sigma = at.blur * unit;
  c.save();
  c.setTransform(1, 0, 0, 1, 0, 0);
  if (sigma > 0.05) {
    // Уменьшенная копия с полями m: поля — растянутые крайние строки/столбцы (как edge-clamp),
    // поэтому размытие у кромки кадра не тянет «пустоту» из-за края — ни каймы, ни наезда.
    const q = Math.max(0.25, Math.min(1, 4 / Math.max(sigma, 1e-6)));
    const sw = Math.max(2, Math.ceil(W * q));
    const sh = Math.max(2, Math.ceil(H * q));
    const m = Math.ceil(sigma * q * 3) + 1;
    const pw = sw + 2 * m;
    const ph = sh + 2 * m;
    const padded = scratchOf(createCanvas, pw, ph);
    const blurred = scratchOf(createCanvas, pw, ph + 1); // другой ключ — второй холст того же размера
    const p = padded.getContext("2d") as Ctx;
    p.save();
    p.setTransform(1, 0, 0, 1, 0, 0);
    p.clearRect(0, 0, pw, ph);
    p.imageSmoothingEnabled = true;
    p.imageSmoothingQuality = "high";
    p.drawImage(source, 0, 0, W, H, m, m, sw, sh);
    p.imageSmoothingEnabled = false;
    p.drawImage(padded, m, m, sw, 1, m, 0, sw, m); // верх
    p.drawImage(padded, m, m + sh - 1, sw, 1, m, m + sh, sw, m); // низ
    p.drawImage(padded, m, 0, 1, ph, 0, 0, m, ph); // лево (с углами)
    p.drawImage(padded, m + sw - 1, 0, 1, ph, m + sw, 0, m, ph); // право
    p.restore();
    const b = blurred.getContext("2d") as Ctx;
    b.save();
    b.setTransform(1, 0, 0, 1, 0, 0);
    b.clearRect(0, 0, pw, ph + 1);
    b.filter = `blur(${(sigma * q).toFixed(2)}px)`;
    b.drawImage(padded, 0, 0);
    b.filter = "none";
    b.restore();
    c.imageSmoothingEnabled = true;
    c.imageSmoothingQuality = "high";
    c.globalAlpha = 1;
    c.drawImage(blurred, m, m, sw, sh, 0, 0, W, H);
  }
  if (at.dim > 0.001) {
    c.globalAlpha = 1;
    c.fillStyle = `rgba(0,0,0,${at.dim.toFixed(3)})`;
    c.fillRect(0, 0, W, H);
  }
  c.restore();
  return true;
}
