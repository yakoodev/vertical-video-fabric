/**
 * Векторные символы-«декор» внутри текста (✦ ✧ ★ ☆ ♥ → • …) и общие фигуры-пути.
 *
 * В шрифтах vendor/fonts этих символов нет — канвас рисовал квадрат «тофу», а в браузере
 * подставлялся системный шрифт со своей шириной, и перенос строк в превью расходился с
 * рендером. Поэтому они рисуются всегда путями: размер и ширина — доли кегля, цвет — как у
 * текста (заливка, градиент, подсветка слова, обводка). Одинаково в превью и в Node.
 *
 * Символ с селектором эмодзи FE0F («♥️») остаётся эмодзи (Twemoji) — это явная просьба.
 */
import type { Ctx2D } from "./types";

/** Путь с центром (cx, cy) и «радиусом» r — строит контур в текущем пути канваса. */
type PathFn = (ctx: Ctx2D, cx: number, cy: number, r: number) => void;

export type DecorGlyph = {
  /** Ширина в строке, доли кегля (без разрядки). */
  adv: number;
  /** Размер фигуры (r), доли кегля. */
  r: number;
  /** Сдвиг центра по высоте от середины заглавных, доли кегля (+ вниз). */
  dy?: number;
  /** Контурный символ: толщина линии в долях кегля (иначе — заливка). */
  line?: number;
  path: PathFn;
};

/* ---------- фигуры ---------- */

/** Четырёхлучевая звезда-«искра» (✦): вогнутые стороны квадратичными дугами. `pinch` — насколько втянуты. */
export function sparklePath(ctx: Ctx2D, cx: number, cy: number, rx: number, ry = rx, pinch = 0.14): void {
  const kx = rx * pinch;
  const ky = ry * pinch;
  ctx.moveTo(cx, cy - ry);
  ctx.quadraticCurveTo(cx + kx, cy - ky, cx + rx, cy);
  ctx.quadraticCurveTo(cx + kx, cy + ky, cx, cy + ry);
  ctx.quadraticCurveTo(cx - kx, cy + ky, cx - rx, cy);
  ctx.quadraticCurveTo(cx - kx, cy - ky, cx, cy - ry);
  ctx.closePath();
}

/** Ломаная той же искры (для shape star4: рисование обводки идёт по точкам). */
export function sparklePoints(rx: number, ry: number, pinch = 0.14, steps = 12): [number, number][] {
  const tips: [number, number][] = [
    [0, -ry],
    [rx, 0],
    [0, ry],
    [-rx, 0],
  ];
  const out: [number, number][] = [];
  for (let s = 0; s < 4; s++) {
    const a = tips[s];
    const b = tips[(s + 1) % 4];
    // Контрольная точка — у центра со стороны угла между лучами.
    const c: [number, number] = [Math.sign(a[0] + b[0]) * rx * pinch, Math.sign(a[1] + b[1]) * ry * pinch];
    for (let i = 0; i < steps; i++) {
      const t = i / steps;
      const u = 1 - t;
      out.push([u * u * a[0] + 2 * u * t * c[0] + t * t * b[0], u * u * a[1] + 2 * u * t * c[1] + t * t * b[1]]);
    }
  }
  return out;
}

/** Звезда с `n` лучами (★ — 5), `inner` — радиус впадин в долях. */
export function starPath(ctx: Ctx2D, cx: number, cy: number, r: number, n = 5, inner = 0.45): void {
  for (let i = 0; i < n * 2; i++) {
    const a = -Math.PI / 2 + (i * Math.PI) / n;
    const rr = i % 2 === 0 ? r : r * inner;
    const x = cx + Math.cos(a) * rr;
    const y = cy + Math.sin(a) * rr;
    if (i) ctx.lineTo(x, y);
    else ctx.moveTo(x, y);
  }
  ctx.closePath();
}

export function circlePath(ctx: Ctx2D, cx: number, cy: number, r: number): void {
  ctx.moveTo(cx + r, cy);
  ctx.arc(cx, cy, r, 0, Math.PI * 2);
  ctx.closePath();
}

/** Сердце, вписанное в квадрат 2r: две дуги сверху, острие снизу. */
export function heartPath(ctx: Ctx2D, cx: number, cy: number, r: number): void {
  const top = cy - r * 0.45;
  ctx.moveTo(cx, cy + r * 0.95);
  ctx.bezierCurveTo(cx - r * 0.35, cy + r * 0.62, cx - r, cy + r * 0.22, cx - r, top + r * 0.05);
  ctx.bezierCurveTo(cx - r, top - r * 0.5, cx - r * 0.18, top - r * 0.62, cx, top - r * 0.05);
  ctx.bezierCurveTo(cx + r * 0.18, top - r * 0.62, cx + r, top - r * 0.5, cx + r, top + r * 0.05);
  ctx.bezierCurveTo(cx + r, cy + r * 0.22, cx + r * 0.35, cy + r * 0.62, cx, cy + r * 0.95);
  ctx.closePath();
}

function diamondPath(ctx: Ctx2D, cx: number, cy: number, r: number): void {
  ctx.moveTo(cx, cy - r);
  ctx.lineTo(cx + r * 0.8, cy);
  ctx.lineTo(cx, cy + r);
  ctx.lineTo(cx - r * 0.8, cy);
  ctx.closePath();
}

/** Стрелка вправо (заливкой: древко + наконечник); `angle` поворачивает. */
function arrowPath(angle: number): PathFn {
  return (ctx, cx, cy, r) => {
    const pts: [number, number][] = [
      [-r, -r * 0.13],
      [r * 0.25, -r * 0.13],
      [r * 0.25, -r * 0.5],
      [r, 0],
      [r * 0.25, r * 0.5],
      [r * 0.25, r * 0.13],
      [-r, r * 0.13],
    ];
    const c = Math.cos(angle);
    const s = Math.sin(angle);
    pts.forEach(([x, y], i) => {
      const px = cx + x * c - y * s;
      const py = cy + x * s + y * c;
      if (i) ctx.lineTo(px, py);
      else ctx.moveTo(px, py);
    });
    ctx.closePath();
  };
}

function checkPath(ctx: Ctx2D, cx: number, cy: number, r: number): void {
  ctx.moveTo(cx - r * 0.85, cy + r * 0.05);
  ctx.lineTo(cx - r * 0.25, cy + r * 0.62);
  ctx.lineTo(cx + r * 0.9, cy - r * 0.7);
}

function crossPath(ctx: Ctx2D, cx: number, cy: number, r: number): void {
  ctx.moveTo(cx - r * 0.7, cy - r * 0.7);
  ctx.lineTo(cx + r * 0.7, cy + r * 0.7);
  ctx.moveTo(cx + r * 0.7, cy - r * 0.7);
  ctx.lineTo(cx - r * 0.7, cy + r * 0.7);
}

function playPath(ctx: Ctx2D, cx: number, cy: number, r: number): void {
  ctx.moveTo(cx - r * 0.7, cy - r);
  ctx.lineTo(cx + r, cy);
  ctx.lineTo(cx - r * 0.7, cy + r);
  ctx.closePath();
}

/* ---------- таблица символов ---------- */

const sparkle: PathFn = (ctx, cx, cy, r) => sparklePath(ctx, cx, cy, r * 0.82, r);
const star5: PathFn = (ctx, cx, cy, r) => starPath(ctx, cx, cy, r, 5, 0.45);

export const DECOR_GLYPHS: Record<string, DecorGlyph> = {
  "✦": { adv: 0.86, r: 0.42, path: sparkle },
  "✧": { adv: 0.86, r: 0.42, line: 0.055, path: sparkle },
  "★": { adv: 0.92, r: 0.44, dy: 0.03, path: star5 },
  "☆": { adv: 0.92, r: 0.44, dy: 0.03, line: 0.055, path: star5 },
  "✶": { adv: 0.86, r: 0.42, path: (ctx, cx, cy, r) => starPath(ctx, cx, cy, r, 6, 0.5) },
  "✷": { adv: 0.86, r: 0.42, path: (ctx, cx, cy, r) => starPath(ctx, cx, cy, r, 8, 0.45) },
  "•": { adv: 0.5, r: 0.13, path: circlePath },
  "●": { adv: 0.8, r: 0.3, path: circlePath },
  "○": { adv: 0.8, r: 0.3, line: 0.06, path: circlePath },
  "◆": { adv: 0.78, r: 0.36, path: diamondPath },
  "◇": { adv: 0.78, r: 0.36, line: 0.055, path: diamondPath },
  "♥": { adv: 0.9, r: 0.38, path: heartPath },
  "♡": { adv: 0.9, r: 0.38, line: 0.055, path: heartPath },
  "→": { adv: 1, r: 0.4, path: arrowPath(0) },
  "←": { adv: 1, r: 0.4, path: arrowPath(Math.PI) },
  "↑": { adv: 0.72, r: 0.4, path: arrowPath(-Math.PI / 2) },
  "↓": { adv: 0.72, r: 0.4, path: arrowPath(Math.PI / 2) },
  "✓": { adv: 0.86, r: 0.38, line: 0.11, path: checkPath },
  "✔": { adv: 0.86, r: 0.38, line: 0.15, path: checkPath },
  "✕": { adv: 0.8, r: 0.38, line: 0.1, path: crossPath },
  "✖": { adv: 0.8, r: 0.38, line: 0.15, path: crossPath },
  "▶": { adv: 0.8, r: 0.32, path: playPath },
  "►": { adv: 0.8, r: 0.32, path: playPath },
};

/** Символ-декор, который рисуем путём (не эмодзи с FE0F и не буква шрифта). */
export function decorGlyph(grapheme: string): DecorGlyph | null {
  return DECOR_GLYPHS[grapheme] ?? null;
}

/**
 * Нарисовать символ в ячейку ширины `cellW` с левым краем `x`; `cy` — середина заглавных.
 * fill — заливка текущим fillStyle (контурный символ — линией этим же цветом); stroke —
 * обводка текста текущими strokeStyle/lineWidth (у контурного символа — толще на его линию).
 */
export function drawDecorGlyph(ctx: Ctx2D, g: DecorGlyph, x: number, cellW: number, cy: number, size: number, pass: "fill" | "stroke"): void {
  const r = g.r * size;
  ctx.beginPath();
  g.path(ctx, x + cellW / 2, cy + (g.dy ?? 0) * size, r);
  if (pass === "stroke") {
    if (g.line) {
      const lw = ctx.lineWidth;
      ctx.lineWidth = lw + g.line * size;
      ctx.stroke();
      ctx.lineWidth = lw;
    } else ctx.stroke();
    return;
  }
  if (g.line) {
    ctx.save();
    ctx.lineWidth = g.line * size;
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    ctx.strokeStyle = ctx.fillStyle;
    ctx.stroke();
    ctx.restore();
  } else ctx.fill();
}

/* ---------- значки площадок (плашка канала) ---------- */

/**
 * Значки площадок для плашки канала (layers/nameplate.ts) — свои упрощённые глифы путями,
 * не файлы логотипов: одинаково в превью и в Node, без загрузки картинок. Значок вписан
 * в высоту `h`, ширина — `adv · h`. «Дырки» (полоски Twitch, треугольник YouTube, самолётик
 * Telegram) вырезаются правилом evenodd — сквозь них видна плашка.
 */
export type PlatformIcon = {
  /** Ширина значка, доли его высоты. */
  adv: number;
  /** evenodd — внутренние контуры становятся дырками; nonzero — части складываются. */
  rule: "evenodd" | "nonzero";
  /** Контур значка с центром (cx, cy) высотой h — в текущий путь канваса. */
  path: (ctx: Ctx2D, cx: number, cy: number, h: number) => void;
};

function polyPath(ctx: Ctx2D, pts: [number, number][], cx: number, cy: number, k: number): void {
  pts.forEach(([x, y], i) => {
    if (i) ctx.lineTo(cx + x * k, cy + y * k);
    else ctx.moveTo(cx + x * k, cy + y * k);
  });
  ctx.closePath();
}

/** Прямоугольник по часовой (направление важно для nonzero вместе с другими частями). */
function rectPath(ctx: Ctx2D, x0: number, y0: number, x1: number, y1: number): void {
  ctx.moveTo(x0, y0);
  ctx.lineTo(x1, y0);
  ctx.lineTo(x1, y1);
  ctx.lineTo(x0, y1);
  ctx.closePath();
}

/** Twitch: «пузырь» со срезанным углом и хвостиком снизу, внутри две полоски-дырки. */
function twitchPath(ctx: Ctx2D, cx: number, cy: number, h: number): void {
  const w = h * 0.86;
  const x = cx - w / 2;
  const y = cy - h / 2;
  const c = h * 0.12;
  const pts: [number, number][] = [
    [c, 0], [w, 0], [w, h * 0.62], [w - h * 0.2, h * 0.82], [w * 0.52, h * 0.82], [w * 0.34, h],
    [w * 0.34, h * 0.82], [0, h * 0.82], [0, c],
  ];
  polyPath(ctx, pts, x, y, 1);
  const bw = w * 0.11;
  for (const bx of [w * 0.42, w * 0.68]) rectPath(ctx, x + bx, y + h * 0.2, x + bx + bw, y + h * 0.5);
}

/** YouTube: скруглённый «экран» с треугольником «играть»-дыркой. */
function youtubePath(ctx: Ctx2D, cx: number, cy: number, h: number): void {
  const w = h * 1.36;
  const rh = h * 0.94;
  const r = rh * 0.28;
  const x = cx - w / 2;
  const y = cy - rh / 2;
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + r);
  ctx.lineTo(x + w, y + rh - r);
  ctx.quadraticCurveTo(x + w, y + rh, x + w - r, y + rh);
  ctx.lineTo(x + r, y + rh);
  ctx.quadraticCurveTo(x, y + rh, x, y + rh - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
  polyPath(ctx, [[-0.17, -0.24], [0.27, 0], [-0.17, 0.24]], cx + h * 0.02, cy, h);
}

/** TikTok: нота — головка, ножка и флажок (части складываются, nonzero). */
function tiktokPath(ctx: Ctx2D, cx: number, cy: number, h: number): void {
  const u = h / 2;
  // Головка — круг по часовой.
  const hx = cx - u * 0.2;
  const hy = cy + u * 0.48;
  const hr = u * 0.5;
  ctx.moveTo(hx + hr, hy);
  ctx.arc(hx, hy, hr, 0, Math.PI * 2);
  ctx.closePath();
  // Ножка.
  rectPath(ctx, hx + hr - u * 0.26, cy - u, hx + hr, hy);
  // Флажок: изогнутая лента от верха ножки вправо-вниз.
  const sx = hx + hr - u * 0.26;
  ctx.moveTo(sx, cy - u);
  ctx.quadraticCurveTo(sx + u * 0.26, cy - u * 0.42, sx + u * 0.82, cy - u * 0.42);
  ctx.lineTo(sx + u * 0.82, cy - u * 0.04);
  ctx.quadraticCurveTo(sx + u * 0.26, cy - u * 0.04, sx, cy - u * 0.4);
  ctx.closePath();
}

/** Telegram: круг с бумажным самолётиком-дыркой. */
function telegramPath(ctx: Ctx2D, cx: number, cy: number, h: number): void {
  const u = h / 2;
  ctx.moveTo(cx + u, cy);
  ctx.arc(cx, cy, u, 0, Math.PI * 2);
  ctx.closePath();
  polyPath(ctx, [[-0.6, 0.02], [0.5, -0.44], [0.32, 0.5], [0.04, 0.26], [-0.12, 0.44], [-0.16, 0.14]], cx - u * 0.04, cy, u);
}

/** Kick: «пиксельная» K ступеньками. */
function kickPath(ctx: Ctx2D, cx: number, cy: number, h: number): void {
  const u = h / 2;
  const pts: [number, number][] = [
    [-0.8, -1], [-0.3, -1], [-0.3, -0.3], [0.05, -0.3], [0.05, -0.62], [0.4, -0.62], [0.4, -1], [0.85, -1],
    [0.85, -0.45], [0.5, -0.45], [0.5, -0.12], [0.18, -0.12], [0.18, 0.12], [0.5, 0.12], [0.5, 0.45], [0.85, 0.45],
    [0.85, 1], [0.4, 1], [0.4, 0.62], [0.05, 0.62], [0.05, 0.3], [-0.3, 0.3], [-0.3, 1], [-0.8, 1],
  ];
  polyPath(ctx, pts, cx, cy, u * 0.86);
}

export const PLATFORM_ICONS: Record<string, PlatformIcon> = {
  twitch: { adv: 0.86, rule: "evenodd", path: twitchPath },
  youtube: { adv: 1.36, rule: "evenodd", path: youtubePath },
  tiktok: { adv: 0.92, rule: "nonzero", path: tiktokPath },
  telegram: { adv: 1, rule: "evenodd", path: telegramPath },
  kick: { adv: 0.74, rule: "nonzero", path: kickPath },
};

/** Значок площадки (none и неизвестная — без значка). */
export function platformIcon(platform: string | null | undefined): PlatformIcon | null {
  return (platform && PLATFORM_ICONS[platform]) || null;
}

/**
 * Нарисовать значок площадки цветом `color` с центром (cx, cy) высотой h. `chroma` — у
 * TikTok сдвинутые голубая и красная копии под нотой (его фирменный «глитч»).
 */
export function drawPlatformIcon(
  ctx: Ctx2D,
  icon: PlatformIcon,
  cx: number,
  cy: number,
  h: number,
  color: string,
  chroma?: [string, string] | null,
): void {
  const fill = (x: number, y: number, style: string) => {
    ctx.beginPath();
    icon.path(ctx, x, y, h);
    ctx.fillStyle = style;
    ctx.fill(icon.rule);
  };
  if (chroma) {
    const d = h * 0.06;
    fill(cx - d, cy - d * 0.7, chroma[0]);
    fill(cx + d, cy + d * 0.7, chroma[1]);
  }
  fill(cx, cy, color);
}
