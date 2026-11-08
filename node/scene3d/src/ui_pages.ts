/**
 * 2D-аватары и страницы для экранов (ui.ts): PNGtuber-лисичка из двух картинок (рот закрыт / открыт),
 * «Live2D»-волчонок (кукла: моргает, хвост машет, голова покачивается, может косить левым глазом),
 * эскиз лисички в рисовалке, страница зарубежного магазина, редактор модели, карточка товара vtubika.store,
 * мессенджер с файлом. Всё рисуется канвасом, функция t, без картинок со стороны.
 */
import { ease } from "./timeline.ts";

const FONT = "VvfUi";
const font = (w: number, px: number) => `${w} ${Math.round(px)}px ${FONT}, Arial, sans-serif`;

function rr(g: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  const k = Math.min(r, w / 2, h / 2);
  g.beginPath();
  g.moveTo(x + k, y);
  g.arcTo(x + w, y, x + w, y + h, k);
  g.arcTo(x + w, y + h, x, y + h, k);
  g.arcTo(x, y + h, x, y, k);
  g.arcTo(x, y, x + w, y, k);
  g.closePath();
}

/* ---------------- лисичка (PNGtuber, нарисована «за один вечер») ---------------- */

export interface FoxOpts { mouthOpen?: boolean; progress?: number; sketch?: boolean }

/**
 * Лисичка: голова с ушами, белые щёки, глаза-бусинки, нос, рот (закрыт «w» / открыт с языком), плечи.
 * ``progress`` < 1 — рисуется по частям (эскиз в рисовалке): контур, уши, щёки, глаза, заливка.
 */
export function drawFox(g: CanvasRenderingContext2D, cx: number, cy: number, s: number, o: FoxOpts = {}) {
  const p = o.progress ?? 1;
  const line = "#4a2616", orange = "#f2893b", cream = "#fff1e0", inner = "#ffd2b0";
  const fill = !o.sketch || p > 0.8;
  g.save();
  g.lineJoin = "round";
  g.lineCap = "round";
  g.lineWidth = s * (o.sketch ? 0.05 : 0.035);
  g.strokeStyle = line;
  // Плечи.
  if (p > 0.15) {
    g.beginPath();
    g.moveTo(cx - s * 0.62, cy + s * 0.95);
    g.quadraticCurveTo(cx - s * 0.55, cy + s * 0.42, cx, cy + s * 0.4);
    g.quadraticCurveTo(cx + s * 0.55, cy + s * 0.42, cx + s * 0.62, cy + s * 0.95);
    g.closePath();
    if (fill) {
      g.fillStyle = orange;
      g.fill();
      g.fillStyle = cream;
      g.beginPath();
      g.ellipse(cx, cy + s * 0.75, s * 0.2, s * 0.3, 0, 0, Math.PI * 2);
      g.fill();
    }
    g.stroke();
  }
  // Уши.
  if (p > 0.3) for (const side of [-1, 1]) {
    g.beginPath();
    g.moveTo(cx + side * s * 0.18, cy - s * 0.3);
    g.lineTo(cx + side * s * 0.46, cy - s * 0.72);
    g.lineTo(cx + side * s * 0.5, cy - s * 0.12);
    g.closePath();
    if (fill) {
      g.fillStyle = orange;
      g.fill();
    }
    g.stroke();
    if (fill) {
      g.fillStyle = inner;
      g.beginPath();
      g.moveTo(cx + side * s * 0.26, cy - s * 0.28);
      g.lineTo(cx + side * s * 0.44, cy - s * 0.58);
      g.lineTo(cx + side * s * 0.45, cy - s * 0.22);
      g.closePath();
      g.fill();
    }
  }
  // Голова: широкая, книзу — острые щёки.
  if (p > 0.05) {
    g.beginPath();
    g.moveTo(cx - s * 0.5, cy - s * 0.05);
    g.quadraticCurveTo(cx - s * 0.48, cy - s * 0.42, cx, cy - s * 0.42);
    g.quadraticCurveTo(cx + s * 0.48, cy - s * 0.42, cx + s * 0.5, cy - s * 0.05);
    g.quadraticCurveTo(cx + s * 0.56, cy + s * 0.12, cx + s * 0.3, cy + s * 0.26);
    g.quadraticCurveTo(cx, cy + s * 0.42, cx - s * 0.3, cy + s * 0.26);
    g.quadraticCurveTo(cx - s * 0.56, cy + s * 0.12, cx - s * 0.5, cy - s * 0.05);
    g.closePath();
    if (fill) {
      g.fillStyle = orange;
      g.fill();
    }
    g.stroke();
  }
  if (p > 0.5 && fill) {
    g.fillStyle = cream;
    for (const side of [-1, 1]) {
      g.beginPath();
      g.ellipse(cx + side * s * 0.2, cy + s * 0.16, s * 0.24, s * 0.15, side * 0.25, 0, Math.PI * 2);
      g.fill();
    }
  }
  if (p > 0.6) {
    // Глаза-бусинки с бликом, нос.
    for (const side of [-1, 1]) {
      g.fillStyle = "#1d1210";
      g.beginPath();
      g.ellipse(cx + side * s * 0.19, cy - s * 0.04, s * 0.065, s * 0.085, 0, 0, Math.PI * 2);
      g.fill();
      g.fillStyle = "#ffffff";
      g.beginPath();
      g.arc(cx + side * s * 0.19 - s * 0.02, cy - s * 0.07, s * 0.022, 0, Math.PI * 2);
      g.fill();
    }
    g.fillStyle = "#1d1210";
    g.beginPath();
    g.ellipse(cx, cy + s * 0.1, s * 0.05, s * 0.035, 0, 0, Math.PI * 2);
    g.fill();
  }
  if (p > 0.7) {
    g.lineWidth = s * 0.025;
    if (o.mouthOpen) {
      g.fillStyle = "#6e1d22";
      g.beginPath();
      g.ellipse(cx, cy + s * 0.22, s * 0.09, s * 0.08, 0, 0, Math.PI * 2);
      g.fill();
      g.fillStyle = "#ff8fa0";
      g.beginPath();
      g.ellipse(cx, cy + s * 0.26, s * 0.055, s * 0.035, 0, 0, Math.PI * 2);
      g.fill();
    } else {
      g.beginPath();
      g.moveTo(cx - s * 0.09, cy + s * 0.18);
      g.quadraticCurveTo(cx - s * 0.045, cy + s * 0.23, cx, cy + s * 0.17);
      g.quadraticCurveTo(cx + s * 0.045, cy + s * 0.23, cx + s * 0.09, cy + s * 0.18);
      g.stroke();
    }
  }
  g.restore();
}

/* ---------------- волчонок («Live2D»-кукла) ---------------- */

export interface WolfOpts {
  /** 0 — глаза открыты, 1 — закрыты. */
  blink?: number;
  /** Угол взмаха хвоста (рад) и смех (рот открыт, глаза ^^). */
  tail?: number;
  laugh?: boolean;
  /** Косит левый глаз (зрачок уехал к переносице и вверх). */
  squint?: boolean;
  sway?: number;
}

export function drawWolf(g: CanvasRenderingContext2D, cx: number, cy: number, s: number, o: WolfOpts = {}) {
  const fur = "#8fa3c8", dark = "#5b6c94", light = "#eef2fa", line = "#2b3150";
  g.save();
  g.lineJoin = "round";
  g.lineCap = "round";
  g.lineWidth = s * 0.03;
  g.strokeStyle = line;
  // Хвост за спиной справа: пушистая «капля», качается вокруг основания.
  g.save();
  g.translate(cx + s * 0.38, cy + s * 0.78);
  g.rotate(-0.5 + (o.tail ?? 0));
  g.beginPath();
  g.moveTo(0, 0);
  g.quadraticCurveTo(s * 0.32, -s * 0.12, s * 0.42, -s * 0.52);
  g.quadraticCurveTo(s * 0.3, -s * 0.66, s * 0.18, -s * 0.5);
  g.quadraticCurveTo(s * 0.12, -s * 0.2, -s * 0.06, -s * 0.08);
  g.closePath();
  g.fillStyle = fur;
  g.fill();
  g.stroke();
  g.fillStyle = light;
  g.beginPath();
  g.ellipse(s * 0.33, -s * 0.5, s * 0.08, s * 0.1, -0.4, 0, Math.PI * 2);
  g.fill();
  g.restore();
  // Тело.
  g.beginPath();
  g.moveTo(cx - s * 0.5, cy + s * 0.98);
  g.quadraticCurveTo(cx - s * 0.48, cy + s * 0.42, cx, cy + s * 0.4);
  g.quadraticCurveTo(cx + s * 0.48, cy + s * 0.42, cx + s * 0.5, cy + s * 0.98);
  g.closePath();
  g.fillStyle = fur;
  g.fill();
  g.stroke();
  g.fillStyle = light;
  g.beginPath();
  g.ellipse(cx, cy + s * 0.72, s * 0.18, s * 0.26, 0, 0, Math.PI * 2);
  g.fill();
  // Голова покачивается (как у Live2D-моделей «дышит»).
  g.translate(cx, cy);
  g.rotate(o.sway ?? 0);
  for (const side of [-1, 1]) {
    g.beginPath();
    g.moveTo(side * s * 0.16, -s * 0.36);
    g.lineTo(side * s * 0.42, -s * 0.78 + Math.abs(o.sway ?? 0) * s);
    g.lineTo(side * s * 0.5, -s * 0.2);
    g.closePath();
    g.fillStyle = dark;
    g.fill();
    g.stroke();
    g.fillStyle = "#c9b8d8";
    g.beginPath();
    g.moveTo(side * s * 0.25, -s * 0.33);
    g.lineTo(side * s * 0.41, -s * 0.64);
    g.lineTo(side * s * 0.45, -s * 0.27);
    g.closePath();
    g.fill();
  }
  // Голова с «пушком» на щеках (зубчики).
  g.beginPath();
  g.moveTo(-s * 0.5, -s * 0.1);
  g.quadraticCurveTo(-s * 0.46, -s * 0.48, 0, -s * 0.48);
  g.quadraticCurveTo(s * 0.46, -s * 0.48, s * 0.5, -s * 0.1);
  g.lineTo(s * 0.6, s * 0.04);
  g.lineTo(s * 0.48, s * 0.08);
  g.lineTo(s * 0.54, s * 0.2);
  g.lineTo(s * 0.36, s * 0.22);
  g.quadraticCurveTo(0, s * 0.42, -s * 0.36, s * 0.22);
  g.lineTo(-s * 0.54, s * 0.2);
  g.lineTo(-s * 0.48, s * 0.08);
  g.lineTo(-s * 0.6, s * 0.04);
  g.closePath();
  g.fillStyle = fur;
  g.fill();
  g.stroke();
  g.fillStyle = light;
  g.beginPath();
  g.ellipse(0, s * 0.16, s * 0.26, s * 0.17, 0, 0, Math.PI * 2);
  g.fill();
  // Глаза: большие, янтарные, с бликами; веки по blink; смех — ^^.
  const blink = Math.max(0, Math.min(1, o.blink ?? 0));
  for (const side of [-1, 1]) {
    const ex = side * s * 0.2, ey = -s * 0.08, rx = s * 0.1, ry = s * 0.12;
    if (o.laugh || blink > 0.85) {
      g.lineWidth = s * 0.03;
      g.beginPath();
      if (o.laugh) g.arc(ex, ey + ry * 0.4, rx * 0.8, Math.PI * 1.15, Math.PI * 1.85);
      else g.arc(ex, ey - ry * 0.2, rx * 0.85, Math.PI * 0.15, Math.PI * 0.85);
      g.stroke();
      continue;
    }
    g.fillStyle = "#ffffff";
    g.beginPath();
    g.ellipse(ex, ey, rx, ry, 0, 0, Math.PI * 2);
    g.fill();
    // Левый глаз персонажа (справа на картинке) косит: зрачок к переносице и вверх.
    const off = o.squint && side > 0 ? [-rx * 0.45, -ry * 0.35] : [0, ry * 0.1];
    g.fillStyle = "#f2a33a";
    g.beginPath();
    g.ellipse(ex + off[0], ey + off[1], rx * 0.7, ry * 0.75, 0, 0, Math.PI * 2);
    g.fill();
    g.fillStyle = "#2a1a10";
    g.beginPath();
    g.ellipse(ex + off[0], ey + off[1], rx * 0.34, ry * 0.42, 0, 0, Math.PI * 2);
    g.fill();
    g.fillStyle = "#ffffff";
    g.beginPath();
    g.arc(ex + off[0] - rx * 0.3, ey + off[1] - ry * 0.35, rx * 0.22, 0, Math.PI * 2);
    g.fill();
    // Веко сверху.
    if (blink > 0.02) {
      g.fillStyle = fur;
      g.fillRect(ex - rx * 1.1, ey - ry * 1.1, rx * 2.2, ry * 2.2 * blink);
    }
    g.lineWidth = s * 0.028;
    g.beginPath();
    g.ellipse(ex, ey, rx, ry, 0, Math.PI * 1.05, Math.PI * 1.95);
    g.stroke();
  }
  // Нос и рот.
  g.fillStyle = line;
  g.beginPath();
  g.ellipse(0, s * 0.08, s * 0.05, s * 0.035, 0, 0, Math.PI * 2);
  g.fill();
  g.lineWidth = s * 0.022;
  if (o.laugh) {
    g.fillStyle = "#7a1f2c";
    g.beginPath();
    g.arc(0, s * 0.15, s * 0.08, 0, Math.PI);
    g.closePath();
    g.fill();
  } else {
    g.beginPath();
    g.moveTo(-s * 0.07, s * 0.15);
    g.quadraticCurveTo(-s * 0.035, s * 0.2, 0, s * 0.14);
    g.quadraticCurveTo(s * 0.035, s * 0.2, s * 0.07, s * 0.15);
    g.stroke();
  }
  // Румянец.
  g.fillStyle = "rgba(255,140,170,0.45)";
  for (const side of [-1, 1]) {
    g.beginPath();
    g.ellipse(side * s * 0.32, s * 0.1, s * 0.07, s * 0.04, 0, 0, Math.PI * 2);
    g.fill();
  }
  g.restore();
}

/** Параметры куклы на момент t: авто-моргание, моргания по ключам, хвост, смех, «косит». */
export function wolfPose(t: number, p: { blinks?: number[]; wag?: { t: number; amp: number }[]; laugh?: number[][];
  squint?: { t: number; on: boolean }[] }): WolfOpts {
  let blink = 0;
  for (const b of p.blinks || []) if (t >= b && t < b + 0.22) blink = Math.sin(Math.PI * (t - b) / 0.22);
  const auto = (t + 0.7) % 3.7;
  if (auto < 0.18) blink = Math.max(blink, Math.sin(Math.PI * auto / 0.18));
  let amp = 0.12;
  for (const w of p.wag || []) if (w.t <= t) amp = w.amp;
  const laugh = (p.laugh || []).some(([a, b]) => t >= a && t < b);
  if (laugh) amp = Math.max(amp, 0.55);
  let squint = false;
  for (const q of p.squint || []) if (q.t <= t) squint = q.on;
  return { blink, tail: amp * Math.sin(2 * Math.PI * (laugh ? 3.2 : 1.1) * t), laugh, squint, sway: 0.05 * Math.sin(2 * Math.PI * 0.35 * t) };
}

/* ---------------- страницы на мониторе ---------------- */

/** Рисовалка: холст с эскизом лисички (появляется по progress), палитра, кисть. */
export function drawDrawApp(g: CanvasRenderingContext2D, W: number, H: number, progress: number) {
  const u = W / 1000;
  g.fillStyle = "#2a2433";
  g.fillRect(0, 0, W, H);
  g.fillStyle = "#3a3346";
  g.fillRect(0, 0, W, 60 * u);
  g.fillStyle = "#d9cfee";
  g.font = font(700, 26 * u);
  g.textBaseline = "middle";
  g.fillText("лиса_финал_ТОЧНО.png", 24 * u, 31 * u);
  const colors = ["#f2893b", "#fff1e0", "#4a2616", "#ffd2b0", "#1d1210", "#ff8fa0"];
  colors.forEach((c, i) => {
    g.fillStyle = c;
    g.beginPath();
    g.arc(48 * u, (110 + i * 70) * u, 24 * u, 0, Math.PI * 2);
    g.fill();
  });
  g.fillStyle = "#fbf8f2";
  rr(g, 110 * u, 80 * u, W - 140 * u, H - 110 * u, 10 * u);
  g.fill();
  drawFox(g, 110 * u + (W - 140 * u) / 2, 80 * u + (H - 110 * u) * 0.45, (H - 110 * u) * 0.5,
    { sketch: true, progress: Math.max(0.02, progress) });
}

/**
 * Чужой магазин моделей (без бренда): товар «Wolf Pup», превью-кукла, кнопка покупки. Всё важное — в центральной
 * колонке (в вертикальном кадре монитор виден по центру).
 */
export function drawForeignShop(g: CanvasRenderingContext2D, W: number, H: number, t: number, wolf: WolfOpts) {
  const u = W / 1000;
  g.fillStyle = "#f4f4f6";
  g.fillRect(0, 0, W, H);
  g.fillStyle = "#1f2430";
  g.fillRect(0, 0, W, 64 * u);
  g.fillStyle = "#ffffff";
  g.font = font(800, 28 * u);
  g.textBaseline = "middle";
  g.textAlign = "center";
  g.fillText("ARTIST  SHOP", W / 2, 33 * u);
  const cx = W / 2, colW = 440 * u;
  g.fillStyle = "#e3e6ee";
  rr(g, cx - colW / 2, 84 * u, colW, H * 0.52, 16 * u);
  g.fill();
  drawWolf(g, cx, 84 * u + H * 0.27, H * 0.2, wolf);
  g.fillStyle = "#1f2430";
  g.font = font(800, 40 * u);
  g.fillText("Wolf Pup · Live2D", cx, 84 * u + H * 0.52 + 46 * u);
  g.fillStyle = "#3d7bff";
  rr(g, cx - colW / 2, H - 112 * u, colW, 74 * u, 14 * u);
  g.fill();
  g.fillStyle = "#ffffff";
  g.font = font(800, 32 * u);
  g.fillText("Buy now", cx, H - 75 * u);
  g.textAlign = "left";
}

/** Карточка «Оплата отклонена» поверх страницы (поп с перелётом). */
export function drawDeclined(g: CanvasRenderingContext2D, W: number, H: number, k: number) {
  const u = W / 1000;
  g.save();
  g.fillStyle = `rgba(10,10,20,${0.45 * Math.min(1, k)})`;
  g.fillRect(0, 0, W, H);
  g.translate(W / 2, H / 2);
  g.scale(0.5 + 0.5 * k, 0.5 + 0.5 * k);
  g.fillStyle = "#ffffff";
  rr(g, -205 * u, -150 * u, 410 * u, 290 * u, 26 * u);
  g.fill();
  g.fillStyle = "#ff3b4e";
  g.beginPath();
  g.arc(0, -78 * u, 52 * u, 0, Math.PI * 2);
  g.fill();
  g.strokeStyle = "#ffffff";
  g.lineWidth = 16 * u;
  g.lineCap = "round";
  g.beginPath();
  g.moveTo(-18 * u, -96 * u);
  g.lineTo(18 * u, -60 * u);
  g.moveTo(18 * u, -96 * u);
  g.lineTo(-18 * u, -60 * u);
  g.stroke();
  g.fillStyle = "#1a1626";
  g.textAlign = "center";
  g.textBaseline = "middle";
  g.font = font(900, 44 * u);
  g.fillText("Оплата", 0, -2 * u);
  g.fillText("отклонена", 0, 46 * u);
  g.fillStyle = "#6a6178";
  g.font = font(600, 24 * u);
  g.fillText("карта не поддерживается", 0, 100 * u);
  g.textAlign = "left";
  g.restore();
}

/** Редактор модели: кукла по центру, под ней ползунки «Глаз L» (едут при починке), всё в центральной колонке. */
export function drawRig(g: CanvasRenderingContext2D, W: number, H: number, t: number, wolf: WolfOpts, fixK: number) {
  const u = W / 1000;
  g.fillStyle = "#1b1d24";
  g.fillRect(0, 0, W, H);
  g.fillStyle = "#262934";
  g.fillRect(0, 0, W, 54 * u);
  g.fillStyle = "#c9d1e8";
  g.font = font(700, 24 * u);
  g.textBaseline = "middle";
  g.textAlign = "center";
  g.fillText("Live2D Viewer — volchonok.model3", W / 2, 28 * u);
  const cx = W / 2, colW = 380 * u;
  g.fillStyle = "#2c303c";
  rr(g, cx - colW / 2, 70 * u, colW, H * 0.56, 12 * u);
  g.fill();
  drawWolf(g, cx, 70 * u + H * 0.28, H * 0.21, wolf);
  const rows: [string, number][] = [["Глаз L · X", 0.5 + 0.35 * (1 - fixK)], ["Глаз L · Y", 0.5 - 0.3 * (1 - fixK)]];
  rows.forEach(([name, v], i) => {
    const y = 70 * u + H * 0.56 + (40 + i * 74) * u;
    g.textAlign = "left";
    g.fillStyle = fixK < 1 ? "#ffcf5c" : "#9fe8b0";
    g.font = font(700, 26 * u);
    g.fillText(name, cx - colW / 2, y);
    g.fillStyle = "#3a3f4d";
    rr(g, cx - colW / 2, y + 22 * u, colW, 12 * u, 6 * u);
    g.fill();
    g.fillStyle = "#6c8cff";
    g.beginPath();
    g.arc(cx - colW / 2 + colW * v, y + 28 * u, 15 * u, 0, Math.PI * 2);
    g.fill();
  });
  g.textAlign = "left";
}

/* ---------------- телефон: vtubika.store и мессенджер ---------------- */

/** Карточка товара vtubika.store: превью, название, цена в ₽, «Оплатить картой РФ» → «Оплачено». */
export function drawStoreCard(g: CanvasRenderingContext2D, W: number, H: number, t: number,
  p: { title?: string; price?: string; paidAt?: number }) {
  const u = W / 500;
  g.fillStyle = "#ffffff";
  g.fillRect(0, 0, W, H);
  g.fillStyle = "#eef0f6";
  rr(g, 20 * u, 70 * u, W - 40 * u, 46 * u, 23 * u);
  g.fill();
  g.fillStyle = "#3b3f52";
  g.font = font(700, 22 * u);
  g.textBaseline = "middle";
  g.textAlign = "center";
  g.fillText("vtubika.store", W / 2, 93 * u);
  g.fillStyle = "#e8e3f7";
  rr(g, 30 * u, 140 * u, W - 60 * u, 400 * u, 22 * u);
  g.fill();
  drawWolf(g, W / 2, 315 * u, 150 * u, wolfPose(t, {}));
  g.textAlign = "left";
  g.fillStyle = "#15121f";
  g.font = font(800, 30 * u);
  g.fillText(p.title || "Live2D-модель «Волчонок»", 34 * u, 585 * u);
  g.fillStyle = "#6b6580";
  g.font = font(500, 22 * u);
  g.fillText("файл придёт после оплаты", 34 * u, 625 * u);
  g.fillStyle = "#15121f";
  g.font = font(900, 54 * u);
  g.fillText(p.price || "2 990 ₽", 34 * u, 700 * u);
  const paid = p.paidAt !== undefined && t >= p.paidAt;
  const k = paid ? ease("outBack", Math.min(1, (t - (p.paidAt as number)) / 0.4)) : 0;
  g.fillStyle = paid ? "#1fbf5f" : "#6b4dff";
  rr(g, 30 * u, 760 * u, W - 60 * u, 96 * u, 24 * u);
  g.fill();
  g.fillStyle = "#ffffff";
  g.textAlign = "center";
  g.font = font(800, 30 * u);
  g.fillText(paid ? "Оплачено" : "Оплатить картой РФ", W / 2 + (paid ? 22 * u : 0), 808 * u);
  if (paid) {
    g.save();
    g.translate(W / 2 - 95 * u, 808 * u);
    g.scale(k, k);
    g.strokeStyle = "#ffffff";
    g.lineWidth = 8 * u;
    g.lineCap = "round";
    g.beginPath();
    g.moveTo(-16 * u, 0);
    g.lineTo(-4 * u, 12 * u);
    g.lineTo(18 * u, -14 * u);
    g.stroke();
    g.restore();
  }
  g.textAlign = "left";
}

/** Мессенджер: шапка «Витубика», баннер «файл модели пришёл», пузырь с файлом. */
export function drawMessenger(g: CanvasRenderingContext2D, W: number, H: number, t: number,
  p: { from?: string; file?: string; size?: string; text?: string; at?: number }) {
  const u = W / 500;
  g.fillStyle = "#dfe8f1";
  g.fillRect(0, 0, W, H);
  g.fillStyle = "#ffffff";
  g.fillRect(0, 0, W, 130 * u);
  g.fillStyle = "#6b4dff";
  g.beginPath();
  g.arc(60 * u, 92 * u, 28 * u, 0, Math.PI * 2);
  g.fill();
  g.fillStyle = "#ffffff";
  g.font = font(900, 30 * u);
  g.textAlign = "center";
  g.textBaseline = "middle";
  g.fillText("V", 60 * u, 93 * u);
  g.textAlign = "left";
  g.fillStyle = "#15121f";
  g.font = font(800, 26 * u);
  g.fillText(p.from || "Витубика", 104 * u, 82 * u);
  g.fillStyle = "#3aa86b";
  g.font = font(500, 20 * u);
  g.fillText("онлайн", 104 * u, 110 * u);
  const k = p.at !== undefined ? ease("outBack", Math.max(0, Math.min(1, (t - p.at) / 0.4))) : 1;
  if (k <= 0) return;
  g.save();
  g.globalAlpha = Math.min(1, k * 1.5);
  g.translate(0, (1 - Math.min(1, k)) * 60 * u);
  g.fillStyle = "#ffffff";
  rr(g, 24 * u, 420 * u, W - 90 * u, 170 * u, 24 * u);
  g.fill();
  g.fillStyle = "#6b4dff";
  rr(g, 44 * u, 444 * u, 74 * u, 74 * u, 16 * u);
  g.fill();
  g.fillStyle = "#ffffff";
  g.font = font(900, 26 * u);
  g.textAlign = "center";
  g.fillText("ZIP", 81 * u, 482 * u);
  g.textAlign = "left";
  g.fillStyle = "#15121f";
  g.font = font(800, 24 * u);
  g.fillText(p.file || "volchonok_live2d.zip", 134 * u, 466 * u);
  g.fillStyle = "#6b6580";
  g.font = font(500, 20 * u);
  g.fillText(p.size || "18 МБ", 134 * u, 498 * u);
  g.fillStyle = "#15121f";
  g.font = font(600, 24 * u);
  g.fillText(p.text || "Файл модели пришёл", 44 * u, 556 * u);
  g.restore();
  // Баннер уведомления сверху.
  g.save();
  g.globalAlpha = Math.min(1, k * 1.5);
  g.fillStyle = "rgba(30,26,44,0.92)";
  rr(g, 20 * u, (150 - (1 - Math.min(1, k)) * 80) * u, W - 40 * u, 90 * u, 22 * u);
  g.fill();
  g.fillStyle = "#ffffff";
  g.font = font(800, 24 * u);
  g.fillText(p.from || "Витубика", 44 * u, 182 * u);
  g.fillStyle = "#d4cdf0";
  g.font = font(500, 22 * u);
  g.fillText((p.text || "Файл модели пришёл").toLowerCase(), 44 * u, 214 * u);
  g.restore();
}
