/**
 * Графики: столбцы (bar/hbar), линия, круговая и кольцевая диаграммы.
 *
 * Рост идёт по `progress` (по умолчанию 0→1 за первые 40% слоя) со сдвигом
 * между столбцами/сегментами — «волна», а не все разом. Под графиком тёмная
 * стеклянная карточка: поверх живого видео без неё цифры не читаются.
 */
import { clamp01, ease } from "../easing";
import { fontString } from "../fonts";
import type { ChartLayer, Ctx2D } from "../types";
import { clearShadow, fillRoundRect, rgba, roundRectPath, shade, type LayerRenderer } from "./common";
import { deviceScale } from "./counter";
import { envFont } from "./text";

export const DEFAULT_PALETTE = ["#8B5CF6", "#FF3B6B", "#FFC53D", "#2EE59D", "#3DA9FF", "#FF8A3D", "#00D8C8", "#FF5FD2"];

const PAD = 48;

type Box = { x: number; y: number; w: number; h: number };

function colorOf(p: ChartLayer["props"], i: number): string {
  const d = p.data[i];
  if (d?.color) return d.color;
  const pal = p.palette && p.palette.length ? p.palette : DEFAULT_PALETTE;
  return pal[i % pal.length];
}

/** Доля роста i-го элемента из n при общем ходе `progress` (волна со сдвигом). */
function stagger(progress: number, i: number, n: number): number {
  const lag = n > 1 ? Math.min(0.12, 0.5 / (n - 1)) : 0;
  const span = 1 - lag * (n - 1);
  return clamp01((progress - lag * i) / span);
}

function fmt(v: number): string {
  const abs = Math.abs(v);
  const s = abs >= 100 || Number.isInteger(v) ? Math.round(v).toString() : v.toFixed(1).replace(".", ",");
  return s.replace(/\B(?=(\d{3})+(?!\d))/g, " ");
}

function text(ctx: Ctx2D, s: string, x: number, y: number, size: number, color: string, font: string, weight: number, family: string | undefined, align: CanvasTextAlign = "center"): void {
  ctx.font = fontString({ family, weight, size }, font);
  ctx.textAlign = align;
  ctx.textBaseline = "middle";
  ctx.fillStyle = rgba(color);
  ctx.fillText(s, x, y);
}

/** Подпись, ужатая по ширине (длинные категории не налезают на соседей). */
function fitSize(ctx: Ctx2D, s: string, maxW: number, size: number, font: string, family: string | undefined, weight: number): number {
  ctx.font = fontString({ family, weight, size }, font);
  const w = ctx.measureText(s).width;
  return w > maxW ? Math.max(14, (size * maxW) / w) : size;
}

function glow(ctx: Ctx2D, on: boolean, color: string, blur: number): void {
  if (!on) return;
  ctx.shadowColor = rgba(color, 0.75);
  ctx.shadowBlur = blur * deviceScale(ctx);
}

function noGlow(ctx: Ctx2D): void {
  ctx.shadowColor = "rgba(0,0,0,0)";
  ctx.shadowBlur = 0;
}

function bars(ctx: Ctx2D, p: ChartLayer["props"], area: Box, prog: number, font: string): void {
  const n = p.data.length;
  const max = Math.max(1e-9, ...p.data.map((d) => Math.max(0, d.value)));
  const labelColor = p.labelColor || "#ffffffd9";
  const labelH = 54;
  const valueH = p.showValues === false ? 0 : 56;
  const plot = { x: area.x, y: area.y + valueH, w: area.w, h: area.h - valueH - labelH };
  const slot = plot.w / n;
  const bw = slot * 0.62;

  // Сетка: три тонкие линии — шкала читается, но не спорит со столбцами.
  ctx.lineWidth = 2;
  ctx.strokeStyle = "rgba(255,255,255,0.08)";
  for (let g = 1; g <= 3; g++) {
    const y = plot.y + plot.h - (plot.h * g) / 3;
    ctx.beginPath();
    ctx.moveTo(plot.x, y);
    ctx.lineTo(plot.x + plot.w, y);
    ctx.stroke();
  }

  p.data.forEach((d, i) => {
    const grow = ease("outBack", stagger(prog, i, n));
    const full = (Math.max(0, d.value) / max) * plot.h;
    const bh = Math.max(0, full * grow);
    const x = plot.x + slot * i + (slot - bw) / 2;
    const base = plot.y + plot.h;
    const color = colorOf(p, i);
    if (bh > 1) {
      const grad = ctx.createLinearGradient(0, base - bh, 0, base);
      grad.addColorStop(0, shade(color, 0.25));
      grad.addColorStop(1, shade(color, -0.35));
      ctx.save();
      glow(ctx, p.glow !== false, color, 34);
      fillRoundRect(ctx, x, base - bh, bw, bh, Math.min(bw * 0.24, bh / 2), grad);
      ctx.restore();
      // Светлая кромка сверху — столбик выглядит «стеклянным».
      fillRoundRect(ctx, x + bw * 0.12, base - bh + 4, bw * 0.76, Math.min(10, bh / 3), 5, "rgba(255,255,255,0.35)");
    }
    if (valueH && grow > 0.05) {
      ctx.save();
      ctx.globalAlpha *= clamp01(grow * 1.5);
      const s = fitSize(ctx, fmt(d.value * clamp01(grow)), slot * 0.96, 40, font, p.font, 800);
      text(ctx, fmt(d.value * clamp01(grow)), x + bw / 2, base - bh - 30, s, "#ffffff", font, 800, p.font);
      ctx.restore();
    }
    const ls = fitSize(ctx, d.label, slot * 0.96, 30, font, p.font, 600);
    text(ctx, d.label, x + bw / 2, base + labelH / 2 + 4, ls, labelColor, font, 600, p.font);
  });
  // Базовая линия.
  fillRoundRect(ctx, plot.x - 6, plot.y + plot.h - 2, plot.w + 12, 4, 2, "rgba(255,255,255,0.35)");
}

function hbars(ctx: Ctx2D, p: ChartLayer["props"], area: Box, prog: number, font: string): void {
  const n = p.data.length;
  const max = Math.max(1e-9, ...p.data.map((d) => Math.max(0, d.value)));
  const labelColor = p.labelColor || "#ffffffd9";
  const labelW = area.w * 0.3;
  const valueW = p.showValues === false ? 0 : 110;
  const plot = { x: area.x + labelW, y: area.y, w: area.w - labelW - valueW, h: area.h };
  const slot = plot.h / n;
  const bh = Math.min(84, slot * 0.64);
  p.data.forEach((d, i) => {
    const grow = ease("outCubic", stagger(prog, i, n));
    const cy = plot.y + slot * (i + 0.5);
    const color = colorOf(p, i);
    const ls = fitSize(ctx, d.label, labelW - 20, 32, font, p.font, 700);
    text(ctx, d.label, plot.x - 20, cy, ls, labelColor, font, 700, p.font, "right");
    fillRoundRect(ctx, plot.x, cy - bh / 2, plot.w, bh, bh / 2, "rgba(255,255,255,0.07)");
    const bw = Math.max(0, (Math.max(0, d.value) / max) * plot.w * grow);
    if (bw > 1) {
      const grad = ctx.createLinearGradient(plot.x, 0, plot.x + bw, 0);
      grad.addColorStop(0, shade(color, -0.3));
      grad.addColorStop(1, shade(color, 0.2));
      ctx.save();
      glow(ctx, p.glow !== false, color, 28);
      fillRoundRect(ctx, plot.x, cy - bh / 2, Math.max(bw, bh), bh, bh / 2, grad);
      ctx.restore();
    }
    if (valueW && grow > 0.05) text(ctx, fmt(d.value * grow), plot.x + Math.max(bw, bh) + 16, cy, 36, "#ffffff", font, 800, p.font, "left");
  });
}

function line(ctx: Ctx2D, p: ChartLayer["props"], area: Box, prog: number, font: string): void {
  const n = p.data.length;
  const vals = p.data.map((d) => d.value);
  const max = Math.max(...vals);
  const min = Math.min(0, ...vals);
  const labelH = 54;
  const plot = { x: area.x + 30, y: area.y + 70, w: area.w - 60, h: area.h - 70 - labelH };
  const range = Math.max(1e-9, max - min);
  const pts = p.data.map((d, i) => [
    plot.x + (n > 1 ? (plot.w * i) / (n - 1) : plot.w / 2),
    plot.y + plot.h - ((d.value - min) / range) * plot.h,
  ]);
  const color = colorOf(p, 0);
  const pr = ease("inOutCubic", prog);
  const headX = plot.x + plot.w * pr;

  // Сетка.
  ctx.lineWidth = 2;
  ctx.strokeStyle = "rgba(255,255,255,0.08)";
  for (let g = 0; g <= 3; g++) {
    const y = plot.y + plot.h - (plot.h * g) / 3;
    ctx.beginPath();
    ctx.moveTo(plot.x, y);
    ctx.lineTo(plot.x + plot.w, y);
    ctx.stroke();
  }

  const tracePath = () => {
    ctx.moveTo(pts[0][0], pts[0][1]);
    for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
  };
  // Всё, что правее «головы» линии, ещё не нарисовано — режем маской.
  ctx.save();
  ctx.beginPath();
  ctx.rect(plot.x - 40, area.y - 40, headX - plot.x + 40, area.h + 80);
  ctx.clip();
  // Заливка под линией — градиент в прозрачность.
  const fill = ctx.createLinearGradient(0, plot.y, 0, plot.y + plot.h);
  fill.addColorStop(0, rgba(color, 0.45));
  fill.addColorStop(1, rgba(color, 0));
  ctx.beginPath();
  tracePath();
  ctx.lineTo(pts[n - 1][0], plot.y + plot.h);
  ctx.lineTo(pts[0][0], plot.y + plot.h);
  ctx.closePath();
  ctx.fillStyle = fill;
  ctx.fill();
  ctx.beginPath();
  tracePath();
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  ctx.lineWidth = 12;
  ctx.strokeStyle = rgba(shade(color, 0.15));
  glow(ctx, p.glow !== false, color, 30);
  ctx.stroke();
  ctx.restore();

  p.data.forEach((d, i) => {
    const [x, y] = pts[i];
    const reached = clamp01((headX - x) / 60 + 1);
    const s = ease("outBack", reached);
    if (s > 0.01) {
      ctx.beginPath();
      ctx.arc(x, y, 15 * s, 0, Math.PI * 2);
      ctx.fillStyle = "#ffffff";
      ctx.fill();
      ctx.lineWidth = 7 * s;
      ctx.strokeStyle = rgba(color);
      ctx.stroke();
      if (p.showValues !== false) {
        ctx.save();
        ctx.globalAlpha *= clamp01(reached);
        text(ctx, fmt(d.value), x, y - 46, 36, "#ffffff", font, 800, p.font);
        ctx.restore();
      }
    }
    const ls = fitSize(ctx, d.label, (plot.w / Math.max(1, n - 1)) * 0.95, 30, font, p.font, 600);
    text(ctx, d.label, x, plot.y + plot.h + labelH / 2 + 4, ls, p.labelColor || "#ffffffd9", font, 600, p.font);
  });
}

function pie(ctx: Ctx2D, p: ChartLayer["props"], area: Box, prog: number, font: string, donut: boolean): void {
  const vals = p.data.map((d) => Math.max(0, d.value));
  const total = vals.reduce((a, b) => a + b, 0) || 1;
  // Легенда снизу, если места по высоте с запасом; иначе только круг.
  const legendRows = Math.ceil(p.data.length / 2);
  const legendH = area.h - area.w > 140 ? legendRows * 58 + 28 : 0;
  const R = Math.min(area.w, area.h - legendH) / 2 - 10;
  const cx = area.x + area.w / 2;
  const cy = area.y + (area.h - legendH) / 2;
  const sweepAll = ease("inOutCubic", prog) * Math.PI * 2;
  const thick = donut ? R * 0.34 : R;
  const rMid = R - thick / 2;
  const gap = p.data.length > 1 ? 0.035 : 0;
  let a0 = -Math.PI / 2;

  if (donut) {
    // Пустое кольцо-дорожка, по которому «бегут» сегменты.
    ctx.beginPath();
    ctx.arc(cx, cy, rMid, 0, Math.PI * 2);
    ctx.lineWidth = thick;
    ctx.strokeStyle = "rgba(255,255,255,0.08)";
    ctx.stroke();
  }

  vals.forEach((v, i) => {
    const sweep = (v / total) * Math.PI * 2;
    const start = a0;
    const end = Math.min(a0 + sweep, -Math.PI / 2 + sweepAll);
    a0 += sweep;
    if (end - start <= gap) return;
    const color = colorOf(p, i);
    const grad = ctx.createLinearGradient(cx - R, cy - R, cx + R, cy + R);
    grad.addColorStop(0, shade(color, 0.3));
    grad.addColorStop(1, shade(color, -0.2));
    ctx.save();
    glow(ctx, p.glow !== false, color, 30);
    ctx.beginPath();
    if (donut) {
      ctx.arc(cx, cy, rMid, start + gap / 2, end - gap / 2);
      ctx.lineWidth = thick;
      ctx.lineCap = p.data.length > 1 ? "butt" : "round";
      ctx.strokeStyle = grad;
      ctx.stroke();
    } else {
      ctx.moveTo(cx, cy);
      ctx.arc(cx, cy, R, start, end);
      ctx.closePath();
      ctx.fillStyle = grad;
      ctx.fill();
      ctx.lineWidth = 6;
      ctx.strokeStyle = "rgba(10,10,20,0.9)";
      ctx.stroke();
    }
    ctx.restore();
    // Процент на крупных кусках пирога.
    const share = v / total;
    if (!donut && p.showValues !== false && share >= 0.07 && end >= start + sweep - 1e-3) {
      const mid = (start + end) / 2;
      text(ctx, `${Math.round(share * 100)}%`, cx + Math.cos(mid) * R * 0.62, cy + Math.sin(mid) * R * 0.62, Math.max(26, R * 0.16), "#ffffff", font, 900, p.font);
    }
  });

  if (donut) {
    const share = (vals[0] ?? 0) / total;
    const shown = Math.round(share * 100 * clamp01(prog));
    const big = R * 0.42;
    text(ctx, `${shown}%`, cx, cy - (p.centerLabel ? big * 0.18 : 0), big, "#ffffff", font, 900, p.font);
    if (p.centerLabel) text(ctx, p.centerLabel, cx, cy + big * 0.55, Math.max(22, R * 0.11), p.labelColor || "#ffffffcc", font, 600, p.font);
  }

  if (legendH) {
    const colW = area.w / 2;
    p.data.forEach((d, i) => {
      const col = i % 2;
      const row = Math.floor(i / 2);
      const x = area.x + colW * col + 20;
      const y = area.y + area.h - legendH + 28 + row * 58 + 22;
      fillRoundRect(ctx, x, y - 14, 28, 28, 8, rgba(colorOf(p, i)));
      const label = `${d.label} — ${Math.round((vals[i] / total) * 100)}%`;
      const s = fitSize(ctx, label, colW - 70, 36, font, p.font, 700);
      text(ctx, label, x + 42, y, s, p.labelColor || "#ffffffd9", font, 700, p.font, "left");
    });
  }
}

export const chartRenderer: LayerRenderer<ChartLayer> = {
  multipart: true,
  measure(_ctx, layer) {
    const p = layer.props;
    return { w: Math.max(200, p.w ?? 860), h: Math.max(200, p.h ?? 640) };
  },
  draw(ctx, layer, st, env) {
    const p = layer.props;
    if (!p.data || !p.data.length) return;
    const font = envFont(env);
    const w = Math.max(200, p.w ?? 860);
    const h = Math.max(200, p.h ?? 640);
    const box: Box = { x: -w / 2, y: -h / 2, w, h };
    const panel = p.panel === undefined ? "#0d0b1ad9" : p.panel;
    if (panel) {
      fillRoundRect(ctx, box.x, box.y, w, h, 44, rgba(panel));
      ctx.beginPath();
      roundRectPath(ctx, box.x + 1, box.y + 1, w - 2, h - 2, 43);
      ctx.lineWidth = 2;
      ctx.strokeStyle = "rgba(255,255,255,0.1)";
      ctx.stroke();
    }
    clearShadow(ctx);
    let area: Box = { x: box.x + PAD, y: box.y + PAD, w: w - PAD * 2, h: h - PAD * 2 };
    if (p.title) {
      const s = fitSize(ctx, p.title, area.w, 52, font, p.font, 900);
      text(ctx, p.title, area.x + area.w / 2, area.y + 26, s, "#ffffff", font, 900, p.font);
      area = { ...area, y: area.y + 84, h: area.h - 84 };
    }
    const prog = clamp01(st.progress);
    noGlow(ctx);
    switch (p.kind) {
      case "hbar":
        return hbars(ctx, p, area, prog, font);
      case "line":
        return line(ctx, p, area, prog, font);
      case "pie":
        return pie(ctx, p, area, prog, font, false);
      case "donut":
        return pie(ctx, p, area, prog, font, true);
      case "bar":
      default:
        return bars(ctx, p, area, prog, font);
    }
  },
};
