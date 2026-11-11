/**
 * Готовые шаблоны графики: функция «параметры + момент клипа → слои».
 *
 * Шаблон не хранится в клипе — он лишь создаёт обычные слои, которые потом
 * правятся как любые другие. Поэтому всё, что тут задано, выражено только
 * через схему слоёв из спеки, без скрытых режимов.
 *
 * Визуальная планка — эфирная графика TikTok/CapCut: жирный капс с обводкой,
 * скошенные плашки, неоновое свечение, «пружинящие» появления.
 */
import { formatRefSize } from "./formats";
import { isTransient, layerSize } from "./reframe";
import { sceneTemplates } from "./scene/templates";
import type { KeyPoint, Layer } from "./types";
import { REF_H, REF_W } from "./types";
import { type MotionTemplate, newLayerId, RED, type TemplateParams, type TemplateResult, VIOLET } from "./templates/kit";
import { captions, keywordBig, memeCaption, punchline, quoteCard, streamChat, titlePop } from "./templates/titles";
import { channelPlate, lowerThird, newsTicker } from "./templates/plates";
import { barChart, countdown, counter, donutChart, pieChart, progressBar, top3 } from "./templates/charts";
import { alertBanner, beforeAfter, boom, lookHere, subscribe } from "./templates/calls";
import { fxTpl, glitchRange, glitchTitle, particlesTpl, phoneScreen } from "./templates/effects";
import { cardFan, depthTitle, phoneSpace } from "./templates/space3d";
import { cursorClicks, fingerTaps, handArrow, handCircle, handUnderline } from "./templates/sketch";
import { commentCard, postCard } from "./templates/social";
import { pagePhone, pageScroll } from "./templates/page";

export { TEMPLATE_CATEGORIES, newLayerId } from "./templates/kit";
export type { TemplateParamType, TemplateParam, TemplateParams, TemplateResult, MotionTemplate } from "./templates/kit";

export const TEMPLATES: MotionTemplate[] = [
  titlePop,
  captions,
  keywordBig,
  streamChat,
  punchline,
  quoteCard,
  memeCaption,
  lowerThird("bar", "Нижняя треть · полоса", "Тёмная плашка с цветной полосой и значком", VIOLET, -10),
  lowerThird("box", "Нижняя треть · эфир", "Заголовок на цветной плашке, подзаголовок — на белой", VIOLET, -10),
  lowerThird("pill", "Нижняя треть · таблетка", "Светлая округлая плашка со значком в кружке", RED, 0),
  lowerThird("neon", "Нижняя треть · неон", "Тёмное стекло с неоновой обводкой", "#22D3EE", 0),
  newsTicker,
  channelPlate,
  counter,
  progressBar,
  barChart,
  donutChart,
  pieChart,
  subscribe,
  lookHere,
  handArrow,
  handCircle,
  handUnderline,
  beforeAfter,
  countdown,
  alertBanner,
  boom,
  top3,
  commentCard,
  postCard,
  phoneScreen,
  pageScroll,
  pagePhone,
  cursorClicks,
  fingerTaps,
  particlesTpl,
  glitchTitle,
  fxTpl,
  glitchRange,
  phoneSpace,
  depthTitle,
  cardFan,
  // Движок сцен (scene/): настоящий 3D/2D одним слоем scene.
  ...sceneTemplates(newLayerId),
];

export function getTemplate(id: string): MotionTemplate | undefined {
  return TEMPLATES.find((t) => t.id === id);
}

/** Собрать шаблон с параметрами по умолчанию, переопределёнными `params`; `fmt` — формат разметки слоёв клипа. */
export function buildTemplate(id: string, params: TemplateParams, at: number, fmt?: unknown): TemplateResult | null {
  const tpl = getTemplate(id);
  if (!tpl) return null;
  const merged: TemplateParams = {};
  for (const prm of tpl.params) merged[prm.name] = prm.default;
  const built = tpl.build({ ...merged, ...params }, at);
  return fmt === undefined ? built : fitTemplate(built, fmt, tpl.fit);
}

/* ---------- шаблон в кадре другого формата ---------- */

const FIT_MARGIN = 0.04;
const FULL_FRAC = 0.98;

/**
 * Шаблоны нарисованы в опорном кадре 9:16 (1080×1920). Для клипа в другом формате (слои
 * размечены в 1:1 / 4:5 / 16:9 — formats.ts) их слои переносятся в опорный кадр формата:
 *
 * - группа (по умолчанию): взаимное расположение слоёв — в px, как в 9:16, общий масштаб
 *   `s` ≤ 1 — чтобы вся группа влезла с полями 4 % (телефон 600 px в 16:9 — ≈0.64), место —
 *   та же доля кадра, сдвинутая внутрь, если край вылез (плашка снизу остаётся снизу);
 * - "frame" по оси — каждый слой на своей доле кадра (мем сверху и снизу, лента у края);
 * - фигура во всю ширину (≥ 98 % ширины 9:16) растягивается до ширины нового кадра с теми же
 *   полями, во всю высоту — до его высоты (рамка «Внимание!», разделитель «До/После»);
 * - ключи x, что уводят слой за кадр (бегущая строка, въезд), остаются долями кадра.
 *
 * Кегль и ширины в px не меняются при s = 1: короткая сторона у всех форматов — 1080.
 */
export function fitTemplate(result: TemplateResult, fmt: unknown, mode: MotionTemplate["fit"] = {}): TemplateResult {
  const { w: W, h: H } = formatRefSize(fmt);
  if (W === REF_W && H === REF_H) return result;
  const SW = REF_W;
  const SH = REF_H;
  const xMode = mode?.x ?? "group";
  const yMode = mode?.y ?? "group";
  const anchorFirst = mode?.anchor === "first";
  type Info = { layer: Layer; size: [number, number] | null; fullW: boolean; fullH: boolean; offX: boolean };
  const infos: Info[] = result.layers.map((layer) => {
    const size = layer.type === "audio" ? null : layerSize(layer);
    const k = layer.scale ?? 1;
    const flat = layer.type === "shape" || layer.type === "image" || layer.type === "video";
    const xs = (layer.keys?.x ?? []).map((p) => p.v);
    return {
      layer,
      size,
      fullW: Boolean(size && flat && size[0] * k >= FULL_FRAC * SW),
      fullH: Boolean(size && layer.type === "shape" && size[1] * k >= FULL_FRAC * SH),
      offX: xs.some((v) => v < -0.02 || v > 1.02),
    };
  });
  const settled = (l: Layer) => {
    const keys = l.keys?.scale ?? [];
    const last = keys.reduce<KeyPoint | null>((b, p) => (!b || p.t > b.t ? p : b), null);
    return Math.max(0, (l.scale ?? 1) * (last ? last.v : 1));
  };
  // Рамка группы в px 9:16: без звука, вспышек и фонов во всю высоту (во всю ширину — только по y).
  let top = Infinity;
  let bottom = -Infinity;
  let left = Infinity;
  let right = -Infinity;
  for (const it of infos) {
    if (!it.size || it.fullH || it.layer.hidden || isTransient(it.layer)) continue;
    const k = settled(it.layer);
    const hh = (it.size[1] * k) / 2;
    for (const y of [it.layer.y ?? 0.5, ...(it.layer.keys?.y ?? []).map((p) => p.v)]) {
      top = Math.min(top, y * SH - hh);
      bottom = Math.max(bottom, y * SH + hh);
    }
    if (it.fullW || it.offX) continue;
    const hw = (it.size[0] * k) / 2;
    for (const x of [it.layer.x ?? 0.5, ...(it.layer.keys?.x ?? []).map((p) => p.v)]) {
      left = Math.min(left, x * SW - hw);
      right = Math.max(right, x * SW + hw);
    }
  }
  const hasY = bottom > top;
  const hasX = right > left;
  let s = 1;
  if (yMode === "group" && hasY) s = Math.min(s, (H * (1 - 2 * FIT_MARGIN)) / (bottom - top));
  if (xMode === "group" && hasX) s = Math.min(s, (W * (1 - 2 * FIT_MARGIN)) / (right - left));
  const first = infos.find((it) => it.size)?.layer;
  // Опорная точка группы (px 9:16) и её место в новом кадре (px): та же доля кадра, сдвинутая
  // внутрь полей; у anchor "first" — ровно доля первого слоя (цель стрелки не уезжает).
  const axis = (lo: number, hi: number, has: boolean, src: number, dst: number, firstV: number) => {
    const a = anchorFirst ? firstV * src : has ? (lo + hi) / 2 : src / 2;
    let to = (a / src) * dst;
    if (has && !anchorFirst) {
      const m = FIT_MARGIN * dst;
      const half = ((hi - lo) * s) / 2;
      to = Math.min(dst - m - half, Math.max(m + half, to));
    }
    return { a, to };
  };
  const ay = axis(top, bottom, hasY, SH, H, first?.y ?? 0.5);
  const ax = axis(left, right, hasX, SW, W, first?.x ?? 0.5);
  const mapY = (v: number) => Number(((ay.to + (v * SH - ay.a) * s) / H).toFixed(4));
  const mapX = (v: number) => Number(((ax.to + (v * SW - ax.a) * s) / W).toFixed(4));
  const layers = infos.map(({ layer, fullW, fullH, offX }) => {
    if (layer.type === "audio") return layer;
    const out = { ...layer } as Layer;
    const groupX = xMode === "group" && !offX && !fullW;
    const groupY = yMode === "group" && !fullH;
    if (groupX) out.x = mapX(layer.x ?? 0.5);
    if (groupY) out.y = mapY(layer.y ?? 0.5);
    if (fullW || fullH) {
      const props = { ...(layer.props as Record<string, unknown>) };
      if (fullW) props.w = Math.round(Number(props.w ?? 400) + (W - SW));
      if (fullH) props.h = Math.round(Number(props.h ?? 400) + (H - SH));
      (out as { props: unknown }).props = props;
    }
    const k = fullW || fullH ? 1 : s;
    if (k !== 1) out.scale = Number(((layer.scale ?? 1) * k).toFixed(4));
    if (layer.keys) {
      const keys = { ...layer.keys };
      if (groupX && keys.x) keys.x = keys.x.map((p) => ({ ...p, v: mapX(p.v) }));
      if (groupY && keys.y) keys.y = keys.y.map((p) => ({ ...p, v: mapY(p.v) }));
      if (k !== 1 && keys.scale) keys.scale = keys.scale.map((p) => ({ ...p, v: Number((p.v * k).toFixed(4)) }));
      out.keys = keys;
    }
    return out;
  });
  return { ...result, layers };
}
