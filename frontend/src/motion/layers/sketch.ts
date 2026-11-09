/**
 * «Нарисовано от руки» для фигур (`shape` с `rough` > 0): обводка маркером вместо геометрии.
 *
 * Круг не замыкается, а заходит сам на себя с небольшим сдвигом радиуса, рамка — с перехлёстом
 * в углу, линия и стрелка чуть гуляют, наконечник стрелки — два коротких штриха, `scribble` —
 * штриховка зигзагом. Дрожание — сумма трёх синусов по длине пути с фазами от `seed`: никакой
 * случайности, кадр превью совпадает с кадром рендера. `boil` — seed меняется 8 раз в секунду.
 *
 * Идея и «ощущение» — по рисованным блокам каталога HyperFrames (HeyGen, Apache-2.0); код свой,
 * пути и ассеты оттуда не копировались.
 */
import type { ShapeProps } from "../types";
import type { Pt } from "./common";

const STEP = 6;
/** Сколько раз в секунду «кипит» линия (`boil`). */
export const BOIL_RATE = 8;

/** Детерминированное 0..1 от пары целых — фазы и сдвиги рисунка. */
function hash01(seed: number, i: number): number {
  let h = (Math.imul(seed | 0, 0x9e3779b1) ^ Math.imul(i + 1, 0x85ebca6b)) >>> 0;
  h = Math.imul(h ^ (h >>> 15), 0x2c1b3c6d) >>> 0;
  h = Math.imul(h ^ (h >>> 12), 0x297a2d39) >>> 0;
  return ((h ^ (h >>> 15)) >>> 0) / 4294967296;
}

/** Сила дрожания 0..1 (нет — 0: ровная фигура, как раньше). */
export function roughOf(p: ShapeProps): number {
  const r = Number(p.rough);
  return Number.isFinite(r) ? Math.max(0, Math.min(1, r)) : 0;
}

/** seed рисунка в момент `local`: у `boil` — свой на каждую 1/8 с. */
export function sketchSeed(p: ShapeProps, local: number): number {
  const base = Math.round(Number(p.seed) || 0);
  return p.boil ? base + 1 + Math.floor(Math.max(0, local) * BOIL_RATE) * 7919 : base;
}

/** Амплитуда дрожания в опорных px: от размера фигуры и толщины линии. */
export function wobbleAmp(p: ShapeProps, rough: number): number {
  const size = Math.max(p.w ?? 300, p.kind === "line" || p.kind === "arrow" ? 0 : p.h ?? 300);
  const sw = p.stroke?.width ?? 18;
  return rough * (Math.min(16, Math.max(3, size * 0.016)) + sw * 0.18);
}

/**
 * Сдвиг точек по нормали на плавный шум по длине пути. `pin` — концы стоят на месте
 * (остриё стрелки попадает в цель, подчёркивание не уезжает с края слова).
 */
export function roughen(pts: Pt[], amp: number, seed: number, pin: boolean): Pt[] {
  if (amp <= 0 || pts.length < 2) return pts;
  const s: number[] = [0];
  for (let i = 1; i < pts.length; i++) s.push(s[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]));
  const total = s[s.length - 1] || 1;
  // Три «волны»: на весь путь, на треть и мелкая дрожь руки.
  const waves = [
    { len: Math.max(total * 0.9, 120), k: 0.55 },
    { len: Math.max(total * 0.3, 60), k: 0.3 },
    { len: 48, k: 0.15 },
  ].map((w, j) => ({ ...w, ph: hash01(seed, j) * Math.PI * 2 }));
  return pts.map((pt, i) => {
    const a = pts[Math.max(0, i - 1)];
    const b = pts[Math.min(pts.length - 1, i + 1)];
    const dx = b[0] - a[0];
    const dy = b[1] - a[1];
    const d = Math.hypot(dx, dy) || 1;
    let n = 0;
    for (const w of waves) n += Math.sin((s[i] / w.len) * Math.PI * 2 + w.ph) * w.k;
    const u = s[i] / total;
    const env = pin ? Math.min(1, u * 7, (1 - u) * 7) : 1;
    const off = n * amp * env;
    return [pt[0] - (dy / d) * off, pt[1] + (dx / d) * off];
  });
}

function linePts(out: Pt[], a: Pt, b: Pt): void {
  const n = Math.max(1, Math.ceil(Math.hypot(b[0] - a[0], b[1] - a[1]) / STEP));
  for (let i = out.length ? 1 : 0; i <= n; i++) out.push([a[0] + ((b[0] - a[0]) * i) / n, a[1] + ((b[1] - a[1]) * i) / n]);
}

/** Круг от руки: начинается чуть левее верха, проходит ~1.1 оборота, к концу радиус «уходит». */
function sketchEllipse(w: number, h: number, rough: number, seed: number): Pt[] {
  const rx = w / 2;
  const ry = h / 2;
  const a0 = -Math.PI / 2 - 0.5 - hash01(seed, 11) * 0.5;
  const sweep = Math.PI * 2 * (1.06 + 0.08 * rough + hash01(seed, 12) * 0.04);
  const drift = 0.07 * rough * (hash01(seed, 13) > 0.5 ? 1 : -1);
  const n = Math.max(24, Math.ceil((sweep * Math.max(rx, ry)) / STEP));
  const pts: Pt[] = [];
  for (let i = 0; i <= n; i++) {
    const u = i / n;
    const a = a0 + sweep * u;
    // Радиус чуть меняется по ходу: линия на заходе ложится рядом с началом, а не точно в него.
    const k = 1 + drift * (u - 0.5) * 2 + 0.035 * rough * Math.sin(u * Math.PI * 3 + hash01(seed, 14) * 6);
    pts.push([Math.cos(a) * rx * k, Math.sin(a) * ry * k]);
  }
  return pts;
}

/** Рамка от руки: стороны чуть «плывут», первый угол перехлёстывается на ~8 % стороны. */
function sketchRect(w: number, h: number, rough: number, seed: number): Pt[] {
  const j = (i: number) => (hash01(seed, 20 + i) - 0.5) * 0.04 * rough;
  const x = w / 2;
  const y = h / 2;
  const corners: Pt[] = [
    [-x * (1 + j(0)), -y * (1 + j(1))],
    [x * (1 + j(2)), -y * (1 + j(3))],
    [x * (1 + j(4)), y * (1 + j(5))],
    [-x * (1 + j(6)), y * (1 + j(7))],
  ];
  const over: Pt = [corners[0][0] + w * (0.08 + 0.06 * rough), corners[0][1] - h * 0.01];
  const pts: Pt[] = [];
  const path = [corners[0], corners[1], corners[2], corners[3], corners[0], over];
  for (let i = 1; i < path.length; i++) linePts(pts, path[i - 1], path[i]);
  return pts;
}

/** Штриховка маркером: зигзаг слева направо и обратно, сверху вниз по рамке w×h. */
export function scribblePts(w: number, h: number, sw: number, rough: number, seed: number): Pt[] {
  const passes = Math.max(2, Math.min(24, Math.ceil(h / Math.max(4, sw * 0.8))));
  const pts: Pt[] = [];
  const top = -h / 2 + Math.min(h / 2, sw / 2);
  const bottom = h / 2 - Math.min(h / 2, sw / 2);
  let prev: Pt | null = null;
  for (let i = 0; i <= passes; i++) {
    const y = passes ? top + ((bottom - top) * i) / passes : 0;
    const right = i % 2 === 1;
    // Края штрихов неровные: рука то не дотягивает, то заходит за край.
    const reach = (w / 2) * (1 - 0.06 * rough * hash01(seed, 40 + i) + 0.03 * rough);
    const p: Pt = [right ? reach : -reach, y];
    if (prev) linePts(pts, prev, p);
    prev = p;
  }
  return pts;
}

/** Ломаная фигуры «от руки»: открытая (кроме заливки — её рисуем по замкнутой). */
export function sketchOutline(p: ShapeProps, rough: number, seed: number, curve: Pt[]): Pt[] {
  const w = Math.max(1, p.w ?? 300);
  const h = Math.max(1, p.h ?? 300);
  const amp = wobbleAmp(p, rough);
  switch (p.kind) {
    case "ellipse":
      return roughen(sketchEllipse(w, h, rough, seed), amp, seed, false);
    case "rect":
      return roughen(sketchRect(w, h, rough, seed), amp * 0.7, seed, false);
    case "scribble":
      return roughen(scribblePts(w, h, p.stroke?.width ?? 24, rough, seed), amp * 0.6, seed, false);
    case "line":
    case "arrow":
    default: {
      // Прямая — это две точки: дрожать нечему, поэтому сначала дробим её мелким шагом.
      const dense: Pt[] = [];
      for (let i = 1; i < curve.length; i++) linePts(dense, curve[i - 1], curve[i]);
      return roughen(dense.length ? dense : curve, amp, seed, true);
    }
  }
}

/**
 * Наконечник стрелки от руки: два штриха от острия назад под ±28° (+ небольшой разнобой).
 * `frac` 0..1 — насколько дорисованы штрихи.
 */
export function sketchHead(tip: Pt, angle: number, head: number, seed: number, frac: number): Pt[][] {
  if (frac <= 0) return [];
  return [-1, 1].map((side, i) => {
    const a = angle + Math.PI + side * (0.5 + (hash01(seed, 60 + i) - 0.5) * 0.18);
    const len = head * (1.05 + (hash01(seed, 62 + i) - 0.5) * 0.25) * Math.min(1, frac);
    const end: Pt = [tip[0] + Math.cos(a) * len, tip[1] + Math.sin(a) * len];
    // Штрих ведётся от острия наружу — как рисует рука после линии.
    const pts: Pt[] = [];
    linePts(pts, tip, end);
    return roughen(pts, head * 0.04, seed + 7 * (i + 1), false);
  });
}

/**
 * Курсор-стрелка мыши: классический силуэт в рамке w×h, остриё — левый верхний угол
 * (-w/2, -h/2). Рисуется по часовой — `draw` обводит его от острия.
 */
export function cursorPts(w: number, h: number): Pt[] {
  // Сетка 12.4 × 19.6, как у стрелки системного курсора.
  const grid: Pt[] = [
    [0, 0],
    [12.4, 12.3],
    [7.2, 12.6],
    [10.2, 18.6],
    [7.6, 19.6],
    [4.6, 13.6],
    [0, 17.3],
  ];
  return grid.map(([gx, gy]) => [-w / 2 + (gx / 12.4) * w, -h / 2 + (gy / 19.6) * h]);
}
