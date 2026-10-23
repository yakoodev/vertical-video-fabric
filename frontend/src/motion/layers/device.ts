/**
 * Корпус устройства вокруг картинки/видео: `device: "phone"` — телефон в духе мокапов
 * Apple (тонкий металлический пояс с бликом, узкая чёрная рамка, скруглённый экран,
 * боковые кнопки, «островок» камеры).
 *
 * Все размеры — доли ширины экрана `w` (опорные px), поэтому корпус масштабируется вместе
 * со слоем, а экран — ровно содержимое слоя (`w` × высота по пропорции окна crop).
 */
import { DEFAULT_FONT, fontString } from "../fonts";
import type { Ctx2D, DeviceKind, PictureProps } from "../types";
import { clearShadow, fillRoundRect, hasShadow, isLight, parseHex, roundRectPath } from "./common";

export const DEVICE_KINDS: DeviceKind[] = ["phone"];
/** Чёрный титан. */
export const DEFAULT_DEVICE_COLOR = "#2c2c2e";

export type PhoneMetrics = {
  /** Металлический пояс по краю корпуса. */
  rim: number;
  /** Чёрная рамка между поясом и экраном. */
  bezel: number;
  /** Выступ боковых кнопок за корпус. */
  button: number;
  screenR: number;
  bodyW: number;
  bodyH: number;
  bodyR: number;
  /** Полный размер слоя с кнопками. */
  outerW: number;
  outerH: number;
};

/** Размеры корпуса для экрана w×h. `radius` > 0 — своё скругление экрана, иначе как у iPhone. */
export function phoneMetrics(w: number, h: number, radius?: number): PhoneMetrics {
  const rim = w * 0.017;
  const bezel = w * 0.03;
  const screenR = radius && radius > 0 ? Math.min(radius, w / 2, h / 2) : Math.min(w * 0.14, h / 2);
  const edge = rim + bezel;
  const bodyW = w + edge * 2;
  const bodyH = h + edge * 2;
  const button = w * 0.012;
  return { rim, bezel, button, screenR, bodyW, bodyH, bodyR: screenR + edge, outerW: bodyW + button * 2, outerH: bodyH };
}

/** Цвет, осветлённый (k > 0) или затемнённый (k < 0), с прозрачностью `a`. */
function tone(color: string, k: number, a = 1): string {
  const c = parseHex(color) ?? parseHex(DEFAULT_DEVICE_COLOR)!;
  const t = k > 0 ? 255 : 0;
  const m = Math.abs(k);
  const ch = (v: number) => Math.round(v + (t - v) * m);
  return `rgba(${ch(c[0])},${ch(c[1])},${ch(c[2])},${a})`;
}

/** Боковая кнопка: металлическая «таблетка», наполовину утопленная в корпус. */
function sideButton(ctx: Ctx2D, m: PhoneMetrics, color: string, side: -1 | 1, top: number, len: number): void {
  const x = side < 0 ? -m.bodyW / 2 - m.button : m.bodyW / 2 - m.rim;
  const w = m.button + m.rim;
  const y = -m.bodyH / 2 + m.bodyH * top;
  const h = m.bodyH * len;
  const g = ctx.createLinearGradient(x, 0, x + w, 0);
  g.addColorStop(side < 0 ? 0 : 1, tone(color, -0.25));
  g.addColorStop(0.5, tone(color, 0.32));
  g.addColorStop(side < 0 ? 1 : 0, tone(color, 0.05));
  fillRoundRect(ctx, x, y, w, h, m.button * 0.9, g);
}

/** Строка статуса `statusBar: true` — доля ширины экрана, как у iPhone (островок с полями). */
export const STATUS_BAR_AUTO = 0.14;
export const STATUS_BAR_MAX = 0.4;

/** Высота строки статуса в опорных px для экрана шириной `w` (0 — нет; только у телефона). */
export function statusBarHeight(p: PictureProps, w: number): number {
  if (p.device !== "phone") return 0;
  const v = p.statusBar;
  const frac = v === true ? STATUS_BAR_AUTO : typeof v === "number" && Number.isFinite(v) ? Math.min(STATUS_BAR_MAX, Math.max(0, v)) : 0;
  return frac * w;
}

export type DeviceOptions = {
  kind: DeviceKind;
  radius?: number;
  color?: string;
  island?: boolean;
  /** Высота строки статуса сверху экрана, опорн. px (0 — нет): содержимое — под ней. */
  statusBar?: number;
  statusBarColor?: string;
  /** Блик стекла поверх экрана. */
  glare?: boolean;
};

/**
 * Нарисовать телефон с центром в (0, 0) и экраном w×h. `screen` рисует содержимое в
 * прямоугольник экрана (уже обрезанный по скруглению). Тень слоя (если включена)
 * отбрасывает силуэт корпуса, детали поверх своей тени не дают.
 */
export function drawPhone(
  ctx: Ctx2D,
  w: number,
  h: number,
  opts: DeviceOptions,
  screen: (x: number, y: number, w: number, h: number) => void,
  font: string = DEFAULT_FONT,
): void {
  const m = phoneMetrics(w, h, opts.radius);
  const color = opts.color && parseHex(opts.color) ? opts.color : DEFAULT_DEVICE_COLOR;
  const bx = -m.bodyW / 2;
  const by = -m.bodyH / 2;

  if (hasShadow(ctx)) fillRoundRect(ctx, bx, by, m.bodyW, m.bodyH, m.bodyR, "#000000");
  clearShadow(ctx);

  // Кнопки — под корпусом: слева кнопка действия и громкость, справа — питание.
  sideButton(ctx, m, color, -1, 0.17, 0.045);
  sideButton(ctx, m, color, -1, 0.245, 0.075);
  sideButton(ctx, m, color, -1, 0.34, 0.075);
  sideButton(ctx, m, color, 1, 0.265, 0.115);

  // Металлический пояс: края светлее середины — объём скруглённого торца.
  const metal = ctx.createLinearGradient(bx, 0, bx + m.bodyW, 0);
  metal.addColorStop(0, tone(color, 0.38));
  metal.addColorStop(0.012, tone(color, 0.06));
  metal.addColorStop(0.5, tone(color, -0.08));
  metal.addColorStop(0.988, tone(color, 0.02));
  metal.addColorStop(1, tone(color, 0.3));
  fillRoundRect(ctx, bx, by, m.bodyW, m.bodyH, m.bodyR, metal);

  // Тонкий блик по краю: свет сверху-слева, отражение снизу-справа.
  const lw = Math.max(0.6, m.rim * 0.32);
  const glint = ctx.createLinearGradient(bx, by, bx + m.bodyW, by + m.bodyH);
  glint.addColorStop(0, "rgba(255,255,255,0.85)");
  glint.addColorStop(0.18, "rgba(255,255,255,0.22)");
  glint.addColorStop(0.5, "rgba(255,255,255,0.06)");
  glint.addColorStop(0.82, "rgba(255,255,255,0.2)");
  glint.addColorStop(1, "rgba(255,255,255,0.6)");
  ctx.beginPath();
  roundRectPath(ctx, bx + lw / 2, by + lw / 2, m.bodyW - lw, m.bodyH - lw, m.bodyR - lw / 2);
  ctx.lineWidth = lw;
  ctx.strokeStyle = glint;
  ctx.stroke();

  // Чёрная рамка стекла и тёмный шов между ней и металлом.
  const gx = bx + m.rim;
  const gy = by + m.rim;
  const gw = m.bodyW - m.rim * 2;
  const gh = m.bodyH - m.rim * 2;
  const gr = m.bodyR - m.rim;
  fillRoundRect(ctx, gx, gy, gw, gh, gr, "#050506");
  ctx.beginPath();
  roundRectPath(ctx, gx, gy, gw, gh, gr);
  ctx.lineWidth = Math.max(0.5, m.rim * 0.22);
  ctx.strokeStyle = "rgba(0,0,0,0.85)";
  ctx.stroke();
  // Кромка стекла ловит свет — едва заметная светлая линия по рамке.
  ctx.beginPath();
  roundRectPath(ctx, gx + lw, gy + lw, gw - lw * 2, gh - lw * 2, gr - lw);
  ctx.lineWidth = Math.max(0.4, lw * 0.5);
  ctx.strokeStyle = "rgba(255,255,255,0.07)";
  ctx.stroke();

  // Экран: содержимое под маской скругления; без содержимого — выключенный экран.
  ctx.save();
  ctx.beginPath();
  roundRectPath(ctx, -w / 2, -h / 2, w, h, m.screenR);
  ctx.clip();
  ctx.fillStyle = "#000000";
  ctx.fillRect(-w / 2, -h / 2, w, h);
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  const sb = Math.max(0, Math.min(h * 0.5, opts.statusBar ?? 0));
  screen(-w / 2, -h / 2 + sb, w, h - sb);
  if (sb > 0) drawStatusBar(ctx, w, h, sb, opts.statusBarColor, font, opts.island !== false);
  if (opts.glare) drawGlare(ctx, w, h);
  ctx.restore();

  if (opts.island !== false) drawIsland(ctx, w, h);
}

/**
 * Строка статуса как у iOS: полоса цвета `color`, слева время «9:41», справа связь, Wi-Fi и
 * батарея. Значки по середине полосы (или островка, если он в полосе помещается).
 */
function drawStatusBar(ctx: Ctx2D, w: number, h: number, sb: number, color: string | undefined, family: string, island: boolean): void {
  const bg = color && parseHex(color) ? color : "#000000";
  const top = -h / 2;
  ctx.fillStyle = bg;
  ctx.fillRect(-w / 2, top, w, sb);
  const ink = isLight(bg) ? "#000000" : "#ffffff";
  const ih = Math.min(w * 0.088, h * 0.06);
  const islandMid = top + w * 0.032 + ih / 2;
  const cy = island && islandMid + ih / 2 <= top + sb ? islandMid : top + sb / 2;
  const u = Math.min(w * 0.045, sb * 0.42); // кегль времени
  if (u < 2) return;
  // Время — в середине левого «уха» (между краем экрана и островком).
  ctx.fillStyle = ink;
  ctx.font = fontString({ family, weight: 700, size: u }, DEFAULT_FONT);
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText("9:41", -w * 0.31, cy + u * 0.04);
  // Справа: четыре столбика связи, веер Wi-Fi и батарея — в середине правого «уха».
  const x0 = w * 0.31 - u * 2.05;
  const bw = u * 0.17;
  for (let i = 0; i < 4; i++) {
    const bh = u * (0.3 + i * 0.16);
    fillRoundRect(ctx, x0 + i * bw * 1.6, cy + u * 0.38 - bh, bw, bh, bw * 0.3, ink);
  }
  const wx = x0 + u * 1.33;
  const wy = cy + u * 0.36;
  ctx.strokeStyle = ink;
  ctx.fillStyle = ink;
  ctx.lineCap = "round";
  ctx.lineWidth = u * 0.13;
  for (let i = 0; i < 3; i++) {
    const rr = u * (0.22 + i * 0.25);
    ctx.beginPath();
    if (i === 0) {
      ctx.moveTo(wx, wy);
      ctx.arc(wx, wy, rr, -Math.PI * 0.75, -Math.PI * 0.25);
      ctx.closePath();
      ctx.fill();
    } else {
      ctx.arc(wx, wy, rr, -Math.PI * 0.75, -Math.PI * 0.25);
      ctx.stroke();
    }
  }
  const bx = wx + u * 0.72;
  const bodyW = u * 1.25;
  const bodyH = u * 0.62;
  ctx.save();
  ctx.beginPath();
  roundRectPath(ctx, bx, cy - bodyH / 2, bodyW, bodyH, bodyH * 0.3);
  ctx.lineWidth = u * 0.07;
  ctx.globalAlpha *= 0.45;
  ctx.stroke();
  ctx.restore();
  fillRoundRect(ctx, bx + u * 0.1, cy - bodyH / 2 + u * 0.1, bodyW * 0.72, bodyH - u * 0.2, u * 0.08, ink);
  fillRoundRect(ctx, bx + bodyW + u * 0.05, cy - u * 0.1, u * 0.07, u * 0.2, u * 0.03, ink);
}

/** Блик стекла: светлая диагональ сверху-слева с резким краем, как на мокапах. */
function drawGlare(ctx: Ctx2D, w: number, h: number): void {
  const g = ctx.createLinearGradient(-w / 2, -h / 2, w / 2, h / 2);
  g.addColorStop(0, "rgba(255,255,255,0.16)");
  g.addColorStop(0.36, "rgba(255,255,255,0.06)");
  g.addColorStop(0.37, "rgba(255,255,255,0)");
  g.addColorStop(1, "rgba(255,255,255,0)");
  ctx.fillStyle = g;
  ctx.fillRect(-w / 2, -h / 2, w, h);
}

/** «Островок» камеры: чёрная таблетка у верха экрана с линзой справа. */
function drawIsland(ctx: Ctx2D, w: number, h: number): void {
  const iw = w * 0.3;
  const ih = Math.min(w * 0.088, h * 0.06);
  const x = -iw / 2;
  const y = -h / 2 + w * 0.032;
  fillRoundRect(ctx, x, y, iw, ih, ih / 2, "#000000");
  const lx = x + iw - ih / 2;
  const ly = y + ih / 2;
  const lr = ih * 0.27;
  const lens = ctx.createRadialGradient(lx - lr * 0.3, ly - lr * 0.3, 0, lx, ly, lr);
  lens.addColorStop(0, "#2b3a63");
  lens.addColorStop(0.55, "#111827");
  lens.addColorStop(1, "#05060a");
  ctx.beginPath();
  ctx.arc(lx, ly, lr, 0, Math.PI * 2);
  ctx.fillStyle = lens;
  ctx.fill();
  ctx.beginPath();
  ctx.arc(lx - lr * 0.35, ly - lr * 0.35, lr * 0.22, 0, Math.PI * 2);
  ctx.fillStyle = "rgba(160,190,255,0.35)";
  ctx.fill();
}
