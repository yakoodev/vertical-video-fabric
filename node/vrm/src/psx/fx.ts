/**
 * Плёнка «хоррора на PS1» поверх кадра низкого разрешения (PsxPost, psx.ts) — чистые функции по пикселям:
 *  - grade (до 15 бит и дизеринга): обесцвечивание к серому, тонировка, контраст, зерно (крупное, слабое, узор
 *    держится GRAIN_HOLD кадров — grainFrame), виньетка, затемнение в чёрное (black 0..1);
 *  - noise (после интерфейса): «радиопомехи» — приглушённый серый снег, строки-срывы, катящаяся светлая полоса;
 *    amount 0..1. Короткие всплески (≤ 6 кадров) задаёт Python: app/psx_look.py.
 * Уровни во времени — ключи spec.fx.keys (линейно между ключами): black — затемнение, static — помехи.
 * Шум — хеш (x, y, кадр), без Math.random: кадр всегда одинаковый при повторном рендере.
 */

export interface FxKey {
  t: number;
  black?: number;
  static?: number;
}

export interface FxSpec {
  /** Обесцвечивание 0..1 (1 — серый). */
  desat?: number;
  /** Множитель цвета после обесцвечивания: холодный зелёно-серый, ржавый и т. п. */
  tint?: string;
  /** Контраст вокруг середины (1 — без изменений). */
  contrast?: number;
  /** Экспозиция: множитель яркости после контраста (1 — без изменений) и подъём чёрного (уровней 8-бит). */
  gain?: number;
  lift?: number;
  /** Сила зерна (уровней 8-бит, ±; на деле ×GRAIN_GAIN). */
  grain?: number;
  /** Зерно: кадров на узор (GRAIN_HOLD), узоров по кругу (GRAIN_CYCLE), клетка (GRAIN_CELL). */
  grainHold?: number;
  grainCycle?: number;
  grainCell?: number;
  /** Виньетка 0..1: насколько темнеют углы. */
  vignette?: number;
  /** Помехи, которые есть всегда (фон), 0..1 — к ним прибавляются ключи. */
  staticBase?: number;
  keys?: FxKey[];
}

/** Целочисленный хеш → 0..1. */
export function hash3(x: number, y: number, z: number): number {
  let h = (x * 374761393 + y * 668265263 + z * 2147483647) | 0;
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  h ^= h >>> 16;
  return (h >>> 0) / 4294967296;
}

/**
 * Зерно и шум плёнки — не свои на каждый кадр, а из нескольких заранее «отснятых» узоров: узор держится
 * GRAIN_HOLD кадров (10 к/с при 30 к/с) и узоры идут по кругу из GRAIN_CYCLE. Шум на каждый кадр по всему
 * экрану TikTok пережимает в кашу и в рывки (lag_report.md) — так кадры между сменами узора кодек берёт почти даром.
 */
export const GRAIN_HOLD = 3;
export const GRAIN_CYCLE = 6;
/** Размер зерна (пиксели низкого разрешения 270×480): крупнее — меньше мелкой «каши» после пережатия. */
export const GRAIN_CELL = 2;
/** Зерно слабее, чем было (±grain уровней): старые пресеты (grain 12–18) теперь дают ±5–8. */
export const GRAIN_GAIN = 0.45;

/** Номер узора шума для кадра: меняется раз в hold кадров, по кругу из cycle (повторный рендер даёт тот же кадр). */
export function grainFrame(frame: number, hold = GRAIN_HOLD, cycle = GRAIN_CYCLE): number {
  const f = Math.max(0, Math.floor(frame));
  return Math.floor(f / Math.max(1, hold)) % Math.max(1, cycle);
}

/** Шум узора n в точке (x, y): одно значение на клетку cell×cell, −0.5..0.5. */
export function grainAt(x: number, y: number, n: number, cell = GRAIN_CELL, salt = 0): number {
  return hash3(Math.floor(x / cell), Math.floor(y / cell), n * 7919 + salt) - 0.5;
}

/** Уровни black / static на время t: линейно между ключами, за краями — крайний ключ (нет ключей — 0). */
export function fxAt(spec: FxSpec | undefined, t: number): { black: number; static: number } {
  const keys = spec?.keys || [];
  const get = (name: "black" | "static") => {
    const ks = keys.filter((k) => k[name] !== undefined);
    if (!ks.length) return 0;
    if (t <= ks[0].t) return ks[0][name]!;
    for (let i = 0; i + 1 < ks.length; i++) {
      const a = ks[i], b = ks[i + 1];
      if (t >= a.t && t < b.t) return a[name]! + (b[name]! - a[name]!) * ((t - a.t) / Math.max(1e-6, b.t - a.t));
    }
    return ks[ks.length - 1][name]!;
  };
  return { black: Math.max(0, Math.min(1, get("black"))), static: Math.max(0, Math.min(1, get("static") + (spec?.staticBase ?? 0))) };
}

const hex = (s: string): [number, number, number] => {
  const v = parseInt(s.replace("#", ""), 16);
  return [((v >> 16) & 255) / 255, ((v >> 8) & 255) / 255, (v & 255) / 255];
};

/** Цветокор кадра на месте (до дизеринга). frame — номер кадра для зерна. */
export function grade(d: Uint8ClampedArray | Uint8Array, w: number, h: number, s: FxSpec, black: number, frame: number) {
  const ds = s.desat ?? 0, tn = s.tint ? hex(s.tint) : [1, 1, 1], ct = s.contrast ?? 1, gr = (s.grain ?? 0) * GRAIN_GAIN;
  const gn = s.gain ?? 1, lf = s.lift ?? 0;
  const gf = grainFrame(frame, s.grainHold, s.grainCycle), gc = s.grainCell ?? GRAIN_CELL;
  const vg = s.vignette ?? 0, keep = 1 - black;
  const cx = (w - 1) / 2, cy = (h - 1) / 2, rr = cx * cx + cy * cy;
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const i = (y * w + x) * 4;
      let r = d[i], g = d[i + 1], b = d[i + 2];
      const l = 0.2126 * r + 0.7152 * g + 0.0722 * b;
      r = l + (r - l) * (1 - ds);
      g = l + (g - l) * (1 - ds);
      b = l + (b - l) * (1 - ds);
      r = ((r * tn[0] - 128) * ct + 128) * gn + lf;
      g = ((g * tn[1] - 128) * ct + 128) * gn + lf;
      b = ((b * tn[2] - 128) * ct + 128) * gn + lf;
      // Зерно: одно на клетку (не по каналам) — «плёнка», а не цветной шум; узор держится несколько кадров.
      const n = gr ? grainAt(x, y, gf, gc) * 2 * gr : 0;
      const dx = x - cx, dy = y - cy;
      const v = vg ? 1 - vg * Math.min(1, ((dx * dx + dy * dy) / rr) * 1.15) ** 1.6 : 1;
      d[i] = Math.max(0, Math.min(255, (r + n) * v * keep));
      d[i + 1] = Math.max(0, Math.min(255, (g + n) * v * keep));
      d[i + 2] = Math.max(0, Math.min(255, (b + n) * v * keep));
    }
  }
}

/** Контраст «снега» помех: 1 — во весь диапазон 0..255 (как было), 0.5 — ≈64..192. */
export const STATIC_CONTRAST = 0.5;

/**
 * Радиопомехи на месте: серый «снег» (пиксели 1×1 и строки, растянутые по горизонтали), сдвиг строк-срывов,
 * катящиеся полосы трекинга, «уплывание» кадра по вертикали (срыв синхронизации: картинка съезжает вниз с
 * тёмной полосой гашения, тем сильнее, чем сильнее помехи). amount 0 — кадр не трогается.
 */
export function noise(d: Uint8ClampedArray | Uint8Array, w: number, h: number, amount: number, frame: number) {
  if (amount <= 0) return;
  const a = Math.min(1, amount);
  const sf = frame >> 1; // «снег» и срывы строк меняются раз в 2 кадра (не каждый кадр — см. GRAIN_HOLD)
  const bar = ((frame * 7) % (h + 60)) - 30; // светлая полоса катится вниз
  const row = new Uint8ClampedArray(w * 4);
  // Кадр уплывает вниз (с переносом), на шве — тёмная полоса гашения в 4–7 строк.
  const roll = Math.round(a * a * 46);
  if (roll > 0) {
    const src = new Uint8ClampedArray(d);
    for (let y = 0; y < h; y++) d.set(src.subarray(((y - roll + h) % h) * w * 4, (((y - roll + h) % h) + 1) * w * 4), y * w * 4);
    const band = 4 + Math.round(3 * a);
    for (let y = roll - band; y < roll; y++) if (y >= 0) for (let x = 0; x < w; x++) for (let c = 0; c < 3; c++) d[(y * w + x) * 4 + c] *= 0.15;
  }
  // Полосы трекинга: две широкие светлые полосы с разной скоростью, внутри — сдвиг строк.
  const tracks = [((frame * 5) % (h + 80)) - 40, ((frame * 3 + 211) % (h + 80)) - 40];
  for (let y = 0; y < h; y++) {
    const inTrack = tracks.some((ty) => Math.abs(y - ty) < 3 + 9 * a);
    if (inTrack) {
      const sh = Math.round((hash3(5, y, sf) - 0.3) * 18 * a);
      for (let x = 0; x < w * 4; x++) row[x] = d[y * w * 4 + x];
      for (let x = 0; x < w; x++) {
        const sx = Math.max(0, Math.min(w - 1, x - sh));
        for (let c = 0; c < 3; c++) d[(y * w + x) * 4 + c] = Math.min(255, row[sx * 4 + c] * (1 - 0.5 * a) + 110 * a * hash3(x >> 2, y, sf));
      }
    }
    // Срыв строки: сдвиг по горизонтали у части строк (чаще при сильных помехах).
    const tear = hash3(0, y >> 1, sf) < a * 0.18 ? Math.round((hash3(1, y, sf) - 0.5) * 24 * a) : 0;
    if (tear) {
      for (let x = 0; x < w * 4; x++) row[x] = d[y * w * 4 + x];
      for (let x = 0; x < w; x++) {
        const sx = Math.max(0, Math.min(w - 1, x - tear));
        for (let c = 0; c < 3; c++) d[(y * w + x) * 4 + c] = row[sx * 4 + c];
      }
    }
    const lineK = 0.65 + 0.7 * hash3(2, y, sf); // строки разной яркости
    const inBar = Math.abs(y - bar) < 10 ? 1 - Math.abs(y - bar) / 10 : 0;
    for (let x = 0; x < w; x++) {
      const i = (y * w + x) * 4;
      // «Снег» мельче по вертикали, чем по горизонтали: пиксель шума — 2 по x. Контраст приглушён (STATIC_CONTRAST):
      // серый вокруг середины, а не чёрно-белая крупа во весь диапазон.
      const s = 128 + (hash3(x >> 1, y, sf) - 0.5) * 255 * STATIC_CONTRAST * lineK;
      const k = Math.min(1, a * (0.85 + 0.15 * hash3(x, y, sf + 7)) + inBar * 0.25 * a);
      d[i] = d[i] + (s - d[i]) * k;
      d[i + 1] = d[i + 1] + (s - d[i + 1]) * k;
      d[i + 2] = d[i + 2] + (s * 0.96 - d[i + 2]) * k;
    }
  }
}
