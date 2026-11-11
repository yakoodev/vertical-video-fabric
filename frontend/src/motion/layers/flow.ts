/**
 * Схема «покупатель → защита → продавец» (`flow_diagram`): узлы (значок или картинка + подпись),
 * связанные пунктиром, который прорисовывается шаг за шагом и «бежит»; центральный узел — щит
 * цветом акцента с замком; в конце — плашка итога «Готово ✓» с прорисовкой галочки.
 *
 * Шаги: узел k появляется в `delay + k·stepGap`, связь k → k+1 тянется между ними (точка-«платёж»
 * едет на её конце), замок защёлкивается после щита, плашка — после последнего узла (`doneAt`).
 * Чистая функция props и времени: превью = рендер.
 */
import { clamp01, ease } from "../easing";
import type { Ctx2D, FlowDiagramLayer, FlowDiagramProps, FlowNode, RenderEnv } from "../types";
import { drawAdIcon, isAdIcon } from "./adicons";
import { AD_ACCENT, adPalette, centerText, drawCircleImage, fitFont, glowDisc, imageRef, mixHex } from "./adkit";
import { clearShadow, partialPolyline, rgba, roundRectPath, type LayerRenderer, type Pt } from "./common";

export const FLOW_MAX = 6;
const POP = 0.45;
const DONE_GREEN = "#22c55e";

const nodesOf = (p: FlowDiagramProps): FlowNode[] => (Array.isArray(p.nodes) ? p.nodes.slice(0, FLOW_MAX) : []);

export type FlowGeo = { pts: Pt[]; r: number[]; center: number; badge: Pt; column: boolean; labelW: number };

/** Где стоят узлы (опорн. px от центра слоя), их радиусы и место плашки итога. */
export function flowGeometry(p: FlowDiagramProps): FlowGeo {
  const n = nodesOf(p).length;
  const W = Math.max(200, p.w ?? 1000);
  const H = Math.max(200, p.h ?? 620);
  const column = p.layout === "column";
  const center = Math.max(0, Math.min(n - 1, Math.round(p.center ?? (n - 1) / 2)));
  const pts: Pt[] = [];
  let rn: number;
  if (column) {
    const top = -H / 2 + H * 0.1;
    const span = H * 0.62;
    rn = Math.min(W * 0.11, (span / Math.max(1, n - 1)) * 0.3);
    for (let k = 0; k < n; k++) pts.push([-W * 0.22, top + rn + (n > 1 ? (span * k) / (n - 1) : span / 2)]);
  } else {
    const margin = W * 0.14;
    const span = W - margin * 2;
    rn = Math.min(H * 0.15, (span / Math.max(1, n - 1)) * 0.3);
    for (let k = 0; k < n; k++) pts.push([n > 1 ? -W / 2 + margin + (span * k) / (n - 1) : 0, -H * 0.16]);
  }
  const r = pts.map((_, k) => (k === center ? rn * 1.34 : rn));
  const badge: Pt = column ? [W * 0.12, H / 2 - H * 0.1] : [0, H / 2 - H * 0.11];
  // Ширина подписи: в ряд — почти весь шаг между узлами, в столбец — справа до края.
  const labelW = column ? W * 0.62 : n > 1 ? ((W * 0.72) / (n - 1)) * 0.96 : W * 0.8;
  return { pts, r, center, badge, column, labelW };
}

/** Моменты шагов (секунды слоя): узел k, связь k (начало, конец), замок, плашка. */
export function flowTimes(p: FlowDiagramProps): { node: number[]; link: [number, number][]; lock: number; done: number } {
  const n = nodesOf(p).length;
  const d0 = Math.max(0, p.delay ?? 0.3);
  const gap = Math.max(0.2, p.stepGap ?? 0.8);
  const node = Array.from({ length: n }, (_, k) => d0 + k * gap);
  const link = node.slice(0, -1).map((t) => [t + POP * 0.6, t + gap] as [number, number]);
  const center = flowGeometry(p).center;
  const done = typeof p.doneAt === "number" ? p.doneAt : (node[n - 1] ?? d0) + POP + 0.35;
  return { node, link, lock: (node[center] ?? d0) + POP + 0.1, done };
}

/** Кривая связи от края узла a к краю узла b с изгибом `bend` (точки ломаной). */
function linkPoints(a: Pt, ra: number, b: Pt, rb: number, bend: number, column: boolean): Pt[] {
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  const len = Math.hypot(dx, dy) || 1;
  const ux = dx / len;
  const uy = dy / len;
  const s: Pt = [a[0] + ux * (ra + 18), a[1] + uy * (ra + 18)];
  const e: Pt = [b[0] - ux * (rb + 18), b[1] - uy * (rb + 18)];
  const sign = column ? 1 : -1;
  const c: Pt = [(s[0] + e[0]) / 2 - uy * bend * sign, (s[1] + e[1]) / 2 + ux * bend * sign];
  const out: Pt[] = [];
  for (let i = 0; i <= 32; i++) {
    const t = i / 32;
    const m = 1 - t;
    out.push([m * m * s[0] + 2 * m * t * c[0] + t * t * e[0], m * m * s[1] + 2 * m * t * c[1] + t * t * e[1]]);
  }
  return out;
}

function drawNode(ctx: Ctx2D, node: FlowNode, at: Pt, r: number, isCenter: boolean, p: FlowDiagramProps, env: RenderEnv, landed: number, labelW: number): void {
  const pal = adPalette(p.theme);
  const accent = p.accent || AD_ACCENT;
  const [x, y] = at;
  if (isCenter) {
    glowDisc(ctx, x, y, r * 1.9, accent, 0.45);
    // Кольцо-импульс, когда щит встал.
    if (landed > 0 && landed < 1) {
      ctx.beginPath();
      ctx.arc(x, y, r * (1 + 0.6 * landed), 0, Math.PI * 2);
      ctx.lineWidth = 6;
      ctx.strokeStyle = rgba(accent, 0.6 * (1 - landed));
      ctx.stroke();
    }
  }
  const g = ctx.createRadialGradient(x - r * 0.3, y - r * 0.4, r * 0.1, x, y, r * 1.05);
  if (isCenter) {
    g.addColorStop(0, rgba(mixHex(accent, "#ffffff", 0.3)));
    g.addColorStop(1, rgba(mixHex(accent, "#000000", 0.35)));
  } else {
    g.addColorStop(0, rgba(mixHex(pal.glass.slice(0, 7), accent, 0.22), 0.97));
    g.addColorStop(1, rgba(pal.glass.slice(0, 7), 0.95));
  }
  ctx.beginPath();
  ctx.arc(x, y, r, 0, Math.PI * 2);
  ctx.fillStyle = g;
  ctx.fill();
  clearShadow(ctx);
  ctx.lineWidth = 3;
  ctx.strokeStyle = isCenter ? rgba("#ffffff", 0.35) : rgba(mixHex(accent, "#ffffff", 0.4), 0.55);
  ctx.stroke();
  const img = imageRef(node.image) ? env.getImage(String(node.image)) : null;
  const icon = node.icon ?? (isCenter ? "shield" : "user");
  if (img) drawCircleImage(ctx, img, x, y, r * 0.86, env);
  else if (isAdIcon(icon)) drawAdIcon(ctx, icon, x, y, r * 1.0, isCenter ? "#ffffff" : mixHex(accent, "#ffffff", 0.55));
  else if (icon) centerText(ctx, icon.slice(0, 3), x, y, fitFont(ctx, icon.slice(0, 3), p.font, 800, r * 0.7, r * 1.4, env), pal.text);
  // Подписи под узлом (в столбец — справа от узла).
  const column = p.layout === "column";
  const lx = column ? x + r + 34 : x;
  let ly = column ? y - (node.sub ? 22 : 0) : y + r + 54;
  if (node.label) {
    const size = fitFont(ctx, node.label, p.font, 800, 40, labelW, env);
    if (column) {
      ctx.textAlign = "left";
      ctx.fillStyle = pal.text;
      ctx.fillText(node.label, lx, ly + size * 0.36);
    } else centerText(ctx, node.label, lx, ly, size, pal.text);
    ly += size + 8;
  }
  if (node.sub) {
    const size = fitFont(ctx, node.sub, p.font, 500, 29, labelW, env);
    if (column) {
      ctx.textAlign = "left";
      ctx.fillStyle = pal.muted;
      ctx.fillText(node.sub, lx, ly + size * 0.36);
    } else centerText(ctx, node.sub, lx, ly, size, pal.muted);
  }
}

function drawLock(ctx: Ctx2D, at: Pt, r: number, k: number, accent: string): void {
  if (k <= 0) return;
  const s = ease("outBack", k);
  const x = at[0] + r * 0.72;
  const y = at[1] + r * 0.72;
  const rr = r * 0.36 * s;
  ctx.beginPath();
  ctx.arc(x, y, rr, 0, Math.PI * 2);
  ctx.fillStyle = "#ffffff";
  ctx.fill();
  ctx.lineWidth = 4;
  ctx.strokeStyle = rgba(accent, 0.9);
  ctx.stroke();
  drawAdIcon(ctx, "lock", x, y - rr * 0.04, rr * 1.15, mixHex(accent, "#000000", 0.2));
}

function drawDone(ctx: Ctx2D, p: FlowDiagramProps, at: Pt, k: number, env: RenderEnv): void {
  const text = p.done ?? "Готово";
  if (!text || k <= 0) return;
  const pal = adPalette(p.theme);
  const green = p.doneColor || DONE_GREEN;
  const s = ease("outBack", clamp01(k / 0.6));
  const size = fitFont(ctx, text, p.font, 800, 42, 640, env);
  const tw = ctx.measureText(text).width;
  const h = 104;
  const w = tw + h * 1.35 + 40;
  ctx.save();
  ctx.translate(at[0], at[1]);
  ctx.scale(s, s);
  glowDisc(ctx, 0, 0, w * 0.62, green, 0.22);
  ctx.beginPath();
  roundRectPath(ctx, -w / 2, -h / 2, w, h, h / 2);
  ctx.fillStyle = rgba(pal.glass.slice(0, 7), 0.96);
  ctx.fill();
  ctx.lineWidth = 3;
  ctx.strokeStyle = rgba(green, 0.75);
  ctx.stroke();
  const cx = -w / 2 + h / 2 + 4;
  ctx.beginPath();
  ctx.arc(cx, 0, h * 0.34, 0, Math.PI * 2);
  ctx.fillStyle = green;
  ctx.fill();
  // Галочка прорисовывается следом за появлением плашки.
  const draw = clamp01((k - 0.25) / 0.4);
  if (draw > 0) {
    const pts: Pt[] = [
      [cx - h * 0.15, h * 0.01],
      [cx - h * 0.04, h * 0.12],
      [cx + h * 0.17, -h * 0.11],
    ];
    ctx.beginPath();
    partialPolyline(ctx, pts, false, ease("outCubic", draw));
    ctx.lineWidth = h * 0.075;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.strokeStyle = "#ffffff";
    ctx.stroke();
  }
  ctx.textAlign = "left";
  ctx.textBaseline = "alphabetic";
  ctx.fillStyle = pal.text;
  ctx.fillText(text, cx + h * 0.34 + 22, size * 0.36);
  ctx.restore();
}

export const flowRenderer: LayerRenderer<FlowDiagramLayer> = {
  multipart: true,
  measure(_ctx, layer) {
    const p = layer.props;
    return { w: Math.max(200, p.w ?? 1000), h: Math.max(200, p.h ?? 620) };
  },
  draw(ctx, layer, st, env) {
    const p = layer.props;
    const nodes = nodesOf(p);
    if (!nodes.length) return;
    const geo = flowGeometry(p);
    const times = flowTimes(p);
    const accent = p.accent || AD_ACCENT;
    const local = st.local;
    const shadow = [ctx.shadowColor, ctx.shadowBlur, ctx.shadowOffsetX, ctx.shadowOffsetY] as const;
    clearShadow(ctx);
    // Связи — под узлами: пунктир «бежит», конец тянется к следующему узлу с точкой-платежом.
    const bend = p.bend ?? 46;
    ctx.save();
    ctx.lineCap = "round";
    for (let k = 0; k < nodes.length - 1; k++) {
      const [t0, t1] = times.link[k];
      const frac = ease("inOutCubic", (local - t0) / Math.max(0.05, t1 - t0));
      if (frac <= 0) continue;
      const pts = linkPoints(geo.pts[k], geo.r[k], geo.pts[k + 1], geo.r[k + 1], bend, geo.column);
      ctx.beginPath();
      const head = partialPolyline(ctx, pts, false, frac);
      ctx.setLineDash([16, 15]);
      ctx.lineDashOffset = -local * 60;
      ctx.lineWidth = 6;
      ctx.strokeStyle = rgba(mixHex(accent, "#ffffff", 0.35), 0.9);
      ctx.stroke();
      ctx.setLineDash([]);
      if (frac < 1) {
        glowDisc(ctx, head.end[0], head.end[1], 34, accent, 0.8);
        ctx.beginPath();
        ctx.arc(head.end[0], head.end[1], 10, 0, Math.PI * 2);
        ctx.fillStyle = "#ffffff";
        ctx.fill();
      } else {
        // Наконечник — шеврон по направлению конца связи.
        const [ex, ey] = head.end;
        const a = head.angle;
        ctx.beginPath();
        ctx.moveTo(ex - Math.cos(a - 0.6) * 22, ey - Math.sin(a - 0.6) * 22);
        ctx.lineTo(ex, ey);
        ctx.lineTo(ex - Math.cos(a + 0.6) * 22, ey - Math.sin(a + 0.6) * 22);
        ctx.lineWidth = 6;
        ctx.strokeStyle = rgba(mixHex(accent, "#ffffff", 0.35), 0.95);
        ctx.stroke();
      }
    }
    ctx.restore();
    nodes.forEach((node, k) => {
      const u = (local - times.node[k]) / POP;
      if (u <= 0) return;
      const s = ease("outBack", u);
      const [x, y] = geo.pts[k];
      ctx.save();
      ctx.globalAlpha *= clamp01(u * 2.5);
      ctx.translate(x, y);
      ctx.scale(s, s);
      ctx.translate(-x, -y);
      ctx.shadowColor = shadow[0];
      ctx.shadowBlur = shadow[1];
      ctx.shadowOffsetX = shadow[2];
      ctx.shadowOffsetY = shadow[3];
      const isCenter = k === geo.center;
      drawNode(ctx, node, geo.pts[k], geo.r[k], isCenter, p, env, clamp01((local - times.node[k] - POP * 0.6) / 0.7), geo.labelW);
      if (isCenter && p.lock !== false) drawLock(ctx, geo.pts[k], geo.r[k], clamp01((local - times.lock) / 0.4), accent);
      ctx.restore();
    });
    drawDone(ctx, p, geo.badge, local - times.done, env);
  },
};
