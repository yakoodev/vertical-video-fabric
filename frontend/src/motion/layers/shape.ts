/**
 * Фигуры: прямоугольник, эллипс, линия, стрелка, треугольник, звезда, «взрыв».
 *
 * Каждая фигура строится ломаной с мелким шагом — так «рисование обводки»
 * (in/out `draw` и ключи `progress`) работает одинаково для всех форм и не
 * зависит от поддержки setLineDash/lineDashOffset у конкретного канваса.
 */
import { clamp01 } from "../easing";
import type { ShapeLayer, Stroke } from "../types";
import { clearShadow, partialPolyline, rgba, type LayerRenderer, type Pt } from "./common";

type Outline = { pts: Pt[]; closed: boolean };

const STEP = 6; // опорных px на отрезок дуги — на глаз гладко даже на 4K

function arcPts(out: Pt[], cx: number, cy: number, rx: number, ry: number, a0: number, a1: number): void {
  const len = Math.abs(a1 - a0) * Math.max(rx, ry);
  const n = Math.max(2, Math.ceil(len / STEP));
  for (let i = 0; i <= n; i++) {
    const a = a0 + ((a1 - a0) * i) / n;
    out.push([cx + Math.cos(a) * rx, cy + Math.sin(a) * ry]);
  }
}

function rectPts(w: number, h: number, r: number): Pt[] {
  const rr = Math.max(0, Math.min(r, w / 2, h / 2));
  const x = -w / 2;
  const y = -h / 2;
  const pts: Pt[] = [];
  // Начинаем с середины верхней стороны и идём по часовой — так рамка «рисуется» сверху.
  pts.push([0, y]);
  if (rr > 0) {
    arcPts(pts, x + w - rr, y + rr, rr, rr, -Math.PI / 2, 0);
    arcPts(pts, x + w - rr, y + h - rr, rr, rr, 0, Math.PI / 2);
    arcPts(pts, x + rr, y + h - rr, rr, rr, Math.PI / 2, Math.PI);
    arcPts(pts, x + rr, y + rr, rr, rr, Math.PI, Math.PI * 1.5);
  } else {
    pts.push([x + w, y], [x + w, y + h], [x, y + h], [x, y]);
  }
  return pts;
}

function curvePts(w: number, bend: number): Pt[] {
  if (!bend) return [[-w / 2, 0], [w / 2, 0]];
  // Квадратичная кривая: вершина дуги на высоте bend над серединой.
  const pts: Pt[] = [];
  const n = Math.max(8, Math.ceil((w + Math.abs(bend)) / STEP));
  const cy = -2 * bend;
  for (let i = 0; i <= n; i++) {
    const t = i / n;
    const x = (1 - t) * (1 - t) * (-w / 2) + t * t * (w / 2);
    const y = 2 * (1 - t) * t * cy;
    pts.push([x, y]);
  }
  return pts;
}

function starPts(w: number, h: number, n: number, inner: number, jitter: boolean): Pt[] {
  const pts: Pt[] = [];
  const count = Math.max(3, Math.min(24, Math.round(n)));
  for (let i = 0; i < count * 2; i++) {
    const a = -Math.PI / 2 + (i * Math.PI) / count;
    let r = i % 2 === 0 ? 1 : inner;
    // У «взрыва» лучи разной длины — живее, но без случайности (стабильно кадр в кадр).
    if (jitter && i % 2 === 0) r *= i % 4 === 0 ? 1 : 0.86;
    pts.push([(Math.cos(a) * w * r) / 2, (Math.sin(a) * h * r) / 2]);
  }
  return pts;
}

export function shapeOutline(p: ShapeLayer["props"]): Outline {
  const w = Math.max(1, p.w ?? 300);
  const h = Math.max(1, p.h ?? 300);
  switch (p.kind) {
    case "ellipse": {
      const pts: Pt[] = [];
      arcPts(pts, 0, 0, w / 2, h / 2, -Math.PI / 2, Math.PI * 1.5);
      pts.pop();
      return { pts, closed: true };
    }
    case "line":
    case "arrow":
      return { pts: curvePts(w, p.bend ?? 0), closed: false };
    case "triangle":
      return { pts: [[0, -h / 2], [w / 2, h / 2], [-w / 2, h / 2]], closed: true };
    case "star":
      return { pts: starPts(w, h, p.points ?? 5, 0.45, false), closed: true };
    case "burst":
      return { pts: starPts(w, h, p.points ?? 14, 0.7, true), closed: true };
    case "rect":
    default:
      return { pts: rectPts(w, h, p.radius ?? 0), closed: true };
  }
}

function arrowStroke(p: ShapeLayer["props"]): Stroke {
  if (p.stroke && p.stroke.width > 0) return p.stroke;
  return { color: p.fill || "#ffffff", width: 18 };
}

export const shapeRenderer: LayerRenderer<ShapeLayer> = {
  multipart: true,
  measure(_ctx, layer) {
    const p = layer.props;
    const sw = p.stroke?.width ?? (p.kind === "arrow" || p.kind === "line" ? 18 : 0);
    const w = Math.max(1, p.w ?? 300);
    if (p.kind === "line" || p.kind === "arrow") {
      const head = p.kind === "arrow" ? Math.max(sw * 3.2, 28) : 0;
      return { w: w + sw + head, h: Math.abs(p.bend ?? 0) * 2 + sw + head * 1.2 };
    }
    return { w: w + sw, h: Math.max(1, p.h ?? 300) + sw };
  },
  draw(ctx, layer, st) {
    const p = layer.props;
    const frac = clamp01(Math.min(st.draw, layer.keys?.progress?.length ? st.progress : 1));
    if (frac <= 0) return;
    const { pts, closed } = shapeOutline(p);
    const isLine = p.kind === "line" || p.kind === "arrow";
    const stroke = isLine ? arrowStroke(p) : p.stroke && p.stroke.width > 0 ? p.stroke : null;

    // Заливка проявляется к концу рисования обводки (или просто гаснет, если обводки нет).
    if (!isLine && p.fill) {
      const fillAlpha = frac >= 1 ? 1 : stroke ? clamp01((frac - 0.55) / 0.45) : frac;
      if (fillAlpha > 0) {
        ctx.beginPath();
        pts.forEach((pt, i) => (i ? ctx.lineTo(pt[0], pt[1]) : ctx.moveTo(pt[0], pt[1])));
        ctx.closePath();
        ctx.fillStyle = rgba(p.fill, fillAlpha);
        ctx.fill();
        // Тень дала заливка; обводка поверх — без своей тени (иначе она ляжет внутрь фигуры).
        clearShadow(ctx);
      }
    }
    if (!stroke) return;

    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    ctx.lineWidth = stroke.width;
    ctx.strokeStyle = rgba(stroke.color);
    if (p.dash) ctx.setLineDash([stroke.width * 1.2, stroke.width * 2.2]);

    const head = p.kind === "arrow" ? Math.max(stroke.width * 3.2, 28) : 0;
    let tip: { end: Pt; angle: number };
    ctx.beginPath();
    if (frac >= 1 && !head) {
      pts.forEach((pt, i) => (i ? ctx.lineTo(pt[0], pt[1]) : ctx.moveTo(pt[0], pt[1])));
      if (closed) ctx.closePath();
      tip = { end: pts[pts.length - 1], angle: 0 };
    } else {
      // Линию стрелки укорачиваем на длину наконечника, чтобы она не торчала из острия.
      const shorten = head ? Math.min(0.5, (head * 0.55) / Math.max(1, polyLen(pts))) : 0;
      tip = partialPolyline(ctx, pts, closed, frac * (1 - shorten));
      if (head) tip = partialEnd(pts, frac);
    }
    ctx.stroke();
    if (p.dash) ctx.setLineDash([]);

    if (head) {
      // Наконечник едет вместе с кончиком линии — стрелка «растёт» к цели.
      const [ex, ey] = tip.end;
      const a = tip.angle;
      const hw = head * 0.62;
      ctx.beginPath();
      ctx.moveTo(ex + Math.cos(a) * head * 0.25, ey + Math.sin(a) * head * 0.25);
      ctx.lineTo(ex - Math.cos(a) * head * 0.75 + Math.cos(a + Math.PI / 2) * hw, ey - Math.sin(a) * head * 0.75 + Math.sin(a + Math.PI / 2) * hw);
      ctx.lineTo(ex - Math.cos(a) * head * 0.75 - Math.cos(a + Math.PI / 2) * hw, ey - Math.sin(a) * head * 0.75 - Math.sin(a + Math.PI / 2) * hw);
      ctx.closePath();
      ctx.fillStyle = rgba(stroke.color);
      ctx.lineWidth = Math.max(4, stroke.width * 0.6);
      ctx.fill();
      ctx.stroke();
    }
  },
};

function polyLen(pts: Pt[]): number {
  let len = 0;
  for (let i = 1; i < pts.length; i++) len += Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
  return len;
}

/** Точка и направление на доле `frac` длины ломаной (без рисования). */
function partialEnd(pts: Pt[], frac: number): { end: Pt; angle: number } {
  let left = polyLen(pts) * clamp01(frac);
  let angle = 0;
  for (let i = 1; i < pts.length; i++) {
    const a = pts[i - 1];
    const b = pts[i];
    const seg = Math.hypot(b[0] - a[0], b[1] - a[1]);
    if (seg <= 0) continue;
    angle = Math.atan2(b[1] - a[1], b[0] - a[0]);
    if (left <= seg) {
      const k = left / seg;
      return { end: [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k], angle };
    }
    left -= seg;
  }
  return { end: pts[pts.length - 1], angle };
}
