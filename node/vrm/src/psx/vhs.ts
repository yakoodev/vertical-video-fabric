/**
 * Картинка старой видеокамеры (VHS-C / Hi8) поверх кадра низкого разрешения (270×480) — по пикселям, до
 * 15-битного дизеринга (PsxPost.scene). Для серий «найденная плёнка»: героиня снимает себя на камеру.
 * Что делает vhs():
 *  - цвет и яркость разведены, как на кассете: резкая яркость, цвет размазан по горизонтали и сдвинут вправо
 *    (цветная каёмка у краёв), чёрные приподняты, светлое слегка выгорает;
 *  - мягкий фокус: общая мягкость + автофокус «ищет» (focusBlur) — картинка плывёт и возвращается;
 *  - строки: чересстрочная гребёнка (нечётные строки смещены на пол-пикселя), тёмные строки развёртки,
 *    плавное «дрожание» строк, вертикальные подскоки кадра;
 *  - полосы трекинга: светлые бегущие полосы с рваными срезами строк; срывы (tear) — много полос сразу,
 *    цвет пропадает, картинка бьётся;
 *  - переключение головок: нижние ~9 строк кадра — сдвиг «клюв» и шум (узнаваемый признак VHS);
 *  - выпадения: короткие светлые и тёмные чёрточки в одну строку, шум яркости (сильнее в тёмном).
 * Шум — хеш (x, y, кадр) из fx.ts: повторный рендер даёт тот же кадр.
 * handheld() — ручная съёмка: покачивание камеры (положение, наклон, крен) по сумме синусов.
 */
import { hash3 } from "./fx.ts";

export interface VhsSpec {
  /** Сила по времени [t, k] (линейно): 0 — выкл, 1 — камера, ~1.8 — затёртая старая плёнка. */
  keys: [number, number][];
  /** Моменты, когда автофокус «ищет» (с): резкость уходит и возвращается за ~0.9 с. */
  hunts?: number[];
  /** Срывы трекинга [от, до] (с): картинку бьёт, полосы по всему кадру. */
  tears?: [number, number][];
  /** Амплитуда ручной съёмки (м) при k = 1 (0 — штатив). */
  shake?: number;
}

/** Сила эффекта на время t. */
export function vhsAt(s: VhsSpec | undefined, t: number): number {
  const ks = s?.keys || [];
  if (!ks.length) return 0;
  if (t <= ks[0][0]) return ks[0][1];
  for (let i = 0; i + 1 < ks.length; i++) {
    const [a, ka] = ks[i], [b, kb] = ks[i + 1];
    if (t >= a && t < b) return ka + (kb - ka) * ((t - a) / Math.max(1e-6, b - a));
  }
  return ks[ks.length - 1][1];
}

/** Мягкость фокуса в пикселях: базовая 0.45; «поиск» — затухающие качания около 3 px. */
export function focusBlur(s: VhsSpec | undefined, t: number): number {
  let b = 0.45;
  for (const h of s?.hunts || []) {
    const u = (t - h) / 0.9;
    if (u >= 0 && u < 1) b += 2.6 * (1 - u) * Math.abs(Math.cos(u * Math.PI * 2.5)) * Math.min(1, u * 8);
  }
  return b;
}

/** Срыв трекинга 0..1: резкий вход, плавный выход. */
export function tearAt(s: VhsSpec | undefined, t: number): number {
  let v = 0;
  for (const [a, b] of s?.tears || []) {
    if (t >= a && t < b) v = Math.max(v, 1);
    else if (t >= b && t < b + 0.25) v = Math.max(v, 1 - (t - b) / 0.25);
  }
  return v;
}

/** Ручная съёмка на время t: сдвиг положения (м), поворот точки взгляда (м) и крен (рад). */
export function handheld(t: number, amp: number): { pos: [number, number, number]; aim: [number, number, number]; roll: number } {
  const w = (f: number, p: number) => Math.sin(t * f + p);
  return {
    pos: [amp * 0.9 * (w(1.9, 0.3) + 0.5 * w(4.7, 1.1)), amp * 0.8 * (w(2.3, 2.0) + 0.5 * w(5.9, 0.4)), amp * 0.5 * w(1.3, 4.0)],
    aim: [amp * 1.6 * (w(1.1, 1.4) + 0.6 * w(3.7, 2.2)), amp * 1.4 * (w(1.7, 3.1) + 0.6 * w(4.3, 0.7)), 0],
    roll: amp * 2.2 * (w(1.5, 0.9) + 0.5 * w(3.9, 2.6)),
  };
}

/** Ящичное размытие строки радиусом r (целое) с зажимом по краям; r = 0 — копия. */
function box(src: Float32Array, out: Float32Array, n: number, r: number, pre: Float32Array) {
  if (r <= 0) {
    out.set(src.subarray(0, n));
    return;
  }
  pre[0] = 0;
  for (let i = 0; i < n; i++) pre[i + 1] = pre[i] + src[i];
  for (let x = 0; x < n; x++) {
    const a = Math.max(0, x - r), b = Math.min(n - 1, x + r);
    out[x] = (pre[b + 1] - pre[a]) / (b - a + 1);
  }
}

/** Мягкость b (px, дробная): смесь ящичных размытий соседних радиусов. */
function soft(src: Float32Array, out: Float32Array, tmp: Float32Array, n: number, b: number, pre: Float32Array) {
  const r = Math.floor(b), f = b - r;
  box(src, out, n, r, pre);
  if (f > 0.02) {
    box(src, tmp, n, r + 1, pre);
    for (let x = 0; x < n; x++) out[x] += (tmp[x] - out[x]) * f;
  }
}

/** Кадр на месте. k — сила (vhsAt), blur — мягкость (focusBlur), tear — срыв (tearAt), t — время, frame — номер кадра. */
export function vhs(d: Uint8ClampedArray | Uint8Array, w: number, h: number, k: number, blur: number, tear: number, t: number, frame: number) {
  if (k <= 0) return;
  const src = new Uint8ClampedArray(d);
  const Y = new Float32Array(w), U = new Float32Array(w), V = new Float32Array(w);
  const Yb = new Float32Array(w), Ub = new Float32Array(w), Vb = new Float32Array(w);
  const tmp = new Float32Array(w), pre = new Float32Array(w + 1);
  // Вертикальный подскок кадра: редко на ±1–2 строки, при срыве чаще.
  const jumpy = hash3(3, 7, frame) < 0.1 * k + 0.5 * tear;
  const dy = jumpy ? Math.round((hash3(4, 8, frame) - 0.5) * 4) : 0;
  // Полосы трекинга: две ползут снизу вверх; при срыве — до шести.
  const nb = tear > 0.05 ? 6 : 2;
  const bands: number[] = [];
  for (let i = 0; i < nb; i++) bands.push(h - ((t * (20 + 9 * i) + i * (h / nb) + hash3(i, 2, 1) * 90) % (h + 40)) + 10);
  const bandH = 5 + 5 * tear;
  const headRows = 9;
  const sat = tear > 0.05 ? 1 - 0.8 * tear : 1;
  for (let y = 0; y < h; y++) {
    const sy = Math.max(0, Math.min(h - 1, y + dy));
    // Сдвиг строки: плавная волна + мелкая дрожь + полоса трекинга + «клюв» нижних строк.
    let sx = 0.9 * k * Math.sin(y * 0.045 + t * 2.6) + (hash3(5, y, frame) - 0.5) * 0.9 * k;
    let snow = 0;
    for (const by of bands) {
      const dd = Math.abs(y - by);
      if (dd < bandH) {
        const m = 1 - dd / bandH;
        sx += (hash3(6, y, frame) - 0.5) * (9 + 22 * tear) * m * k;
        snow = Math.max(snow, 0.22 * m * Math.min(1.4, k) + 0.4 * tear * m);
      }
    }
    if (y >= h - headRows) {
      const e = (y - (h - headRows)) / headRows;
      sx += (4 + 14 * e + 10 * tear) * (0.6 + 0.4 * hash3(7, y, frame)) * (hash3(8, frame, 1) < 0.5 ? 1 : -0.4);
      snow = Math.max(snow, 0.3 + 0.35 * e);
    }
    if (tear > 0.05) sx += (hash3(9, y >> 1, frame) - 0.5) * 16 * tear;
    const ix = Math.round(sx);
    const o = sy * w * 4;
    for (let x = 0; x < w; x++) {
      const px = Math.max(0, Math.min(w - 1, x - ix)) * 4 + o;
      const r = src[px], g = src[px + 1], b = src[px + 2];
      const l = 0.299 * r + 0.587 * g + 0.114 * b;
      Y[x] = l;
      U[x] = b - l;
      V[x] = r - l;
    }
    // Яркость — мягкость фокуса (+ чуть больше в срыве); цвет — размазан на ±5 px и сдвинут вправо на 2 px.
    soft(Y, Yb, tmp, w, blur * (0.6 + 0.4 * k) + 0.6 * tear, pre);
    const cr = Math.round(4 + 2 * k + 2 * tear);
    box(U, tmp, w, cr, pre);
    for (let x = 0; x < w; x++) Ub[x] = tmp[Math.max(0, x - 2)];
    box(V, tmp, w, cr, pre);
    for (let x = 0; x < w; x++) Vb[x] = tmp[Math.max(0, x - 2)];
    const odd = (y & 1) === (frame & 1);
    const scan = odd ? 1 - 0.1 * Math.min(1.4, k) : 1;
    for (let x = 0; x < w; x++) {
      // Гребёнка чересстрочки: на одном из полей строка взята с пол-пикселя левее.
      let l = odd ? 0.5 * (Yb[x] + Yb[Math.max(0, x - 1)]) : Yb[x];
      const u = Ub[x] * sat * 1.05, v = Vb[x] * sat * 1.05;
      // Шум яркости: сильнее в тёмном; поднятые чёрные; светлое выгорает.
      const nz = (hash3(x, y, frame + 11) - 0.5) * (14 + 10 * (1 - l / 255)) * Math.min(1.6, k);
      l = l * 0.92 + 14 * Math.min(1.4, k) + nz;
      if (l > 200) l += (l - 200) * 0.25;
      if (snow > 0) l += (hash3(x >> 1, y, frame + 3) * 255 - l) * snow;
      l *= scan;
      const r = l + v, b = l + u;
      const g = (l - 0.299 * r - 0.114 * b) / 0.587;
      const i = (y * w + x) * 4;
      d[i] = r;
      d[i + 1] = g;
      d[i + 2] = b;
    }
  }
  // Выпадения: светлые/тёмные чёрточки в одну строку.
  const n = Math.round((2 + 7 * tear) * Math.min(1.5, k));
  for (let i = 0; i < n; i++) {
    const y = Math.floor(hash3(i, 21, frame) * h), x0 = Math.floor(hash3(i, 22, frame) * w);
    const len = 6 + Math.floor(hash3(i, 23, frame) * 34), bright = hash3(i, 24, frame) < 0.7 ? 235 : 20;
    for (let x = x0; x < Math.min(w, x0 + len); x++) {
      const j = (y * w + x) * 4;
      d[j] = d[j] * 0.3 + bright * 0.7;
      d[j + 1] = d[j + 1] * 0.3 + bright * 0.7;
      d[j + 2] = d[j + 2] * 0.3 + bright * 0.7;
    }
  }
}
