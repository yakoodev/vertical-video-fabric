/**
 * Облако пузырей (`bubbles`): круги со значком или картинкой, числом и подписью — «12 тыс. отзывов»,
 * «340 игр». Пузыри выпрыгивают по очереди от центра облака, считают число вверх, мягко парят;
 * фокус (`focus`) увеличивает один пузырь, остальные гаснут и расступаются.
 *
 * Раскладка — детерминированная упаковка без наложений внутри рамки w×h: крупные первыми, каждый —
 * в первую свободную точку спирали от центра (поворот спирали — от `seed`). Размер пузыря — по
 * числу (корень от доли наибольшего) или по зерну. Всё — функция props и времени: превью = рендер.
 */
import { clamp01, ease } from "../easing";
import { rand01 } from "../keys";
import type { BubbleItem, BubblesLayer, BubblesProps, Ctx2D, RenderEnv } from "../types";
import { drawAdIcon, isAdIcon } from "./adicons";
import { accentTone, AD_ACCENT, adPalette, centerText, compactNumber, drawCircleImage, fitFont, glowDisc, imageRef, mixHex } from "./adkit";
import { clearShadow, hasShadow, rgba, type LayerRenderer } from "./common";

export const BUBBLES_MAX = 12;
const POP_SEC = 0.5;
const COUNT_SEC = 0.9;
const FOCUS_SEC = 0.55;

export type BubbleSpot = { i: number; x: number; y: number; r: number; rank: number; fx: number; fy: number; phase: number };

const layoutCache = new WeakMap<object, BubbleSpot[]>();

const items = (p: BubblesProps): BubbleItem[] => (Array.isArray(p.items) ? p.items.slice(0, BUBBLES_MAX) : []);

/** Раскладка облака: центры (опорн. px от центра слоя), радиусы и порядок появления. */
export function bubbleLayout(p: BubblesProps): BubbleSpot[] {
  const hit = layoutCache.get(p);
  if (hit) return hit;
  const list = items(p);
  const W = Math.max(100, p.w ?? 960);
  const H = Math.max(100, p.h ?? 960);
  const seed = Math.round(p.seed ?? 7);
  const minD = Math.max(40, p.minSize ?? 220);
  const maxD = Math.max(minD, p.maxSize ?? 380);
  const gap = Math.max(0, p.gap ?? 22);
  const values = list.map((it) => (typeof it.value === "number" && it.value > 0 ? it.value : null));
  const top = Math.max(0, ...values.map((v) => v ?? 0));
  const radii = list.map((_, i) => {
    const v = values[i];
    // Площадь — по числу, но мелкий пузырь не меньше четверти пути: подпись в нём читается.
    const k = top > 0 ? (v === null ? 0 : Math.max(0.25, Math.sqrt(v / top))) : rand01(seed, i * 7 + 1);
    return (minD + (maxD - minD) * k) / 2;
  });
  const order = list.map((_, i) => i).sort((a, b) => radii[b] - radii[a] || a - b);
  const placed: { i: number; x: number; y: number; r: number }[] = [];
  const turn = rand01(seed, 99) * Math.PI * 2;
  const sx = W / Math.max(W, H);
  const sy = H / Math.max(W, H);
  // Шаг спирали: за 1600 точек она доходит до края рамки — крупным пузырям хватает места.
  const step = Math.max(3, Math.max(W, H) / 2 / 40);
  for (const i of order) {
    let r = radii[i];
    let spot: { x: number; y: number } | null = null;
    for (let shrink = 0; shrink < 8 && !spot; shrink++) {
      for (let s = 0; s < 1600; s++) {
        const a = turn + s * 2.39996;
        const d = Math.sqrt(s) * step;
        const x = Math.cos(a) * d * sx;
        const y = Math.sin(a) * d * sy;
        if (Math.abs(x) + r > W / 2 || Math.abs(y) + r > H / 2) continue;
        if (placed.every((q) => Math.hypot(q.x - x, q.y - y) >= q.r + r + gap)) {
          spot = { x, y };
          break;
        }
      }
      if (!spot) r *= 0.88;
    }
    if (spot) placed.push({ i, x: spot.x, y: spot.y, r });
  }
  // Появление — от центра облака к краям.
  const byDist = [...placed].sort((a, b) => Math.hypot(a.x, a.y) - Math.hypot(b.x, b.y));
  const out = placed
    .map((q) => ({
      ...q,
      rank: byDist.indexOf(q),
      fx: 0.16 + 0.14 * rand01(seed, q.i * 13 + 3),
      fy: 0.13 + 0.12 * rand01(seed, q.i * 13 + 5),
      phase: rand01(seed, q.i * 13 + 8),
    }))
    .sort((a, b) => a.i - b.i);
  layoutCache.set(p, out);
  return out;
}

/** Момент появления пузыря (секунды слоя). */
export function bubbleAppearAt(p: BubblesProps, spot: BubbleSpot): number {
  return Math.max(0, p.delay ?? 0.15) + spot.rank * Math.max(0, p.stagger ?? 0.12);
}

/** Сила фокуса 0..1 в момент `local`: нет фокуса — 0; по умолчанию — когда все появились. */
export function bubbleFocus(p: BubblesProps, spots: BubbleSpot[], local: number): number {
  const f = Math.round(p.focus ?? -1);
  if (!(f >= 0) || !spots.some((s) => s.i === f)) return 0;
  const lastPop = Math.max(0, ...spots.map((s) => bubbleAppearAt(p, s))) + POP_SEC;
  const at = typeof p.focusAt === "number" ? p.focusAt : lastPop + 0.4;
  return ease("inOutCubic", (local - at) / FOCUS_SEC);
}

type Pose = { x: number; y: number; r: number; scale: number; alpha: number; count: number };

/** Где и каким виден каждый пузырь в момент `local` (появление, парение, фокус). */
export function bubblePoses(p: BubblesProps, local: number): Pose[] {
  const spots = bubbleLayout(p);
  const f = bubbleFocus(p, spots, local);
  const focus = Math.round(p.focus ?? -1);
  const fs = Math.max(1, Math.min(2, p.focusScale ?? 1.35));
  const amp = Math.max(0, p.float ?? 12);
  const gap = Math.max(0, p.gap ?? 22);
  const center = spots.find((s) => s.i === focus);
  return spots.map((s) => {
    const u = (local - bubbleAppearAt(p, s)) / POP_SEC;
    const pop = u <= 0 ? 0 : ease("outBack", u);
    let x = s.x + amp * Math.sin(Math.PI * 2 * (s.fx * local + s.phase));
    let y = s.y + amp * 1.2 * Math.sin(Math.PI * 2 * (s.fy * local + s.phase + 0.25));
    let scale = pop;
    let alpha = clamp01(u * 3);
    if (f > 0 && center) {
      if (s.i === focus) {
        scale *= 1 + (fs - 1) * f;
        x += (0 - x) * 0.18 * f;
        y += (0 - y) * 0.18 * f;
      } else {
        scale *= 1 - 0.12 * f;
        alpha *= 1 - 0.62 * f;
        // Расступиться: сосед, которого задел выросший пузырь, отодвигается по линии центров.
        const dx = x - center.x;
        const dy = y - center.y;
        const d = Math.hypot(dx, dy) || 1;
        const need = center.r * fs + s.r * 0.88 + gap;
        if (d < need) {
          x += (dx / d) * (need - d) * f;
          y += (dy / d) * (need - d) * f;
        }
      }
    }
    const count = clamp01(ease("outCubic", (local - bubbleAppearAt(p, s) - 0.1) / COUNT_SEC));
    return { x, y, r: s.r, scale, alpha, count };
  });
}

function bubbleText(it: BubbleItem, k: number): string {
  if (typeof it.value !== "number") return "";
  return `${it.prefix ?? ""}${compactNumber(it.value * k)}${it.suffix ?? ""}`;
}

function drawBubble(ctx: Ctx2D, it: BubbleItem, i: number, pose: Pose, p: BubblesProps, env: RenderEnv, castShadow: boolean): void {
  const pal = adPalette(p.theme);
  const color = it.color || accentTone(p.accent || AD_ACCENT, i);
  const solid = p.style === "solid";
  const r = pose.r * pose.scale;
  const { x, y } = pose;
  const shadow = [ctx.shadowColor, ctx.shadowBlur, ctx.shadowOffsetX, ctx.shadowOffsetY] as const;
  clearShadow(ctx);
  ctx.save();
  ctx.globalAlpha *= pose.alpha;
  glowDisc(ctx, x, y, r * 1.45, color, solid ? 0.32 : 0.26);
  if (castShadow) {
    ctx.shadowColor = shadow[0];
    ctx.shadowBlur = shadow[1];
    ctx.shadowOffsetX = shadow[2];
    ctx.shadowOffsetY = shadow[3];
  }
  // Тело: стекло с подсветкой цветом или плотный шар с объёмом.
  const g = ctx.createRadialGradient(x - r * 0.35, y - r * 0.45, r * 0.05, x, y, r * 1.08);
  if (solid) {
    g.addColorStop(0, rgba(mixHex(color, "#ffffff", 0.32)));
    g.addColorStop(0.6, rgba(color));
    g.addColorStop(1, rgba(mixHex(color, "#000000", 0.45)));
  } else {
    g.addColorStop(0, rgba(mixHex(pal.glass.slice(0, 7), color, 0.42), 0.96));
    g.addColorStop(0.7, rgba(mixHex(pal.glass.slice(0, 7), color, 0.12), 0.94));
    g.addColorStop(1, rgba(mixHex(pal.glass.slice(0, 7), color, 0.22), 0.94));
  }
  ctx.beginPath();
  ctx.arc(x, y, r, 0, Math.PI * 2);
  ctx.fillStyle = g;
  ctx.fill();
  clearShadow(ctx);
  ctx.lineWidth = Math.max(1.5, r * 0.022);
  ctx.strokeStyle = solid ? rgba("#ffffff", 0.28) : rgba(color, 0.7);
  ctx.stroke();
  // Блик стекла сверху-слева.
  ctx.beginPath();
  ctx.arc(x, y, r * 0.86, Math.PI * 1.08, Math.PI * 1.48);
  ctx.lineWidth = r * 0.05;
  ctx.lineCap = "round";
  ctx.strokeStyle = rgba("#ffffff", solid ? 0.35 : 0.22);
  ctx.stroke();

  const value = bubbleText(it, pose.count);
  const label = it.label || "";
  const img = imageRef(it.image) ? env.getImage(String(it.image)) : null;
  // Значок: картинка, свой векторный, или 1–3 буквы (инициалы) — если имя не из набора.
  const letters = !img && !isAdIcon(it.icon) && typeof it.icon === "string" ? it.icon.trim().slice(0, 3) : "";
  const hasIcon = Boolean(img) || isAdIcon(it.icon) || Boolean(letters);
  const ink = solid ? "#ffffff" : pal.text;
  const iconInk = solid ? "#ffffff" : mixHex(color, "#ffffff", 0.35);
  const font = p.font;
  if (img && !value && !label) {
    drawCircleImage(ctx, img, x, y, r * 0.94, env);
  } else {
    const iconY = value ? y - r * 0.36 : label ? y - r * 0.14 : y;
    const iconS = value ? r * 0.56 : r * 0.82;
    if (img) drawCircleImage(ctx, img, x, iconY, iconS * 0.56, env);
    else if (letters) centerText(ctx, letters, x, iconY, fitFont(ctx, letters, font, 800, iconS * 0.62, iconS * 1.2, env), iconInk);
    else if (hasIcon) drawAdIcon(ctx, it.icon, x, iconY, iconS, iconInk);
    if (value) {
      const vy = hasIcon ? y + r * 0.13 : label ? y - r * 0.08 : y;
      const size = fitFont(ctx, value, font, 800, Math.max(26, r * (hasIcon ? 0.4 : 0.5)), r * 1.55, env);
      centerText(ctx, value, x, vy, size, ink);
    }
    if (label) {
      const ly = value ? (hasIcon ? y + r * 0.5 : y + r * 0.36) : hasIcon ? y + r * 0.5 : y;
      const size = fitFont(ctx, label, font, 600, Math.max(22, r * (value || hasIcon ? 0.19 : 0.24)), r * 1.5, env);
      centerText(ctx, label, x, ly, size, solid ? rgba("#ffffff", 0.82) : pal.muted);
    }
  }
  ctx.restore();
}

export const bubblesRenderer: LayerRenderer<BubblesLayer> = {
  multipart: true,
  measure(_ctx, layer) {
    const p = layer.props;
    const pad = Math.max(40, p.maxSize ?? 380) * 0.35;
    return { w: Math.max(100, p.w ?? 960) + pad, h: Math.max(100, p.h ?? 960) + pad };
  },
  draw(ctx, layer, st, env) {
    const p = layer.props;
    const list = items(p);
    const poses = bubblePoses(p, st.local);
    const spots = bubbleLayout(p);
    const castShadow = hasShadow(ctx);
    // Фокус — последним, поверх соседей; остальные — в порядке списка.
    const focus = Math.round(p.focus ?? -1);
    const order = spots.map((s, k) => ({ s, k })).sort((a, b) => Number(a.s.i === focus) - Number(b.s.i === focus));
    for (const { s, k } of order) {
      const pose = poses[k];
      if (pose.scale <= 0.002 || pose.alpha <= 0.002) continue;
      drawBubble(ctx, list[s.i], s.i, pose, p, env, castShadow);
    }
    clearShadow(ctx);
  },
};
