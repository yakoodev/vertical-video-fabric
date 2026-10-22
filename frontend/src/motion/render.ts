/**
 * Отрисовка кадра: все видимые слои в момент `t` поверх того, что уже на холсте.
 *
 * Слой рисуется в своей системе координат — опорные px с центром в якоре, — а
 * сюда вынесено общее: перенос/поворот/масштаб (× W/1080), прозрачность,
 * режим наложения, тень, размытие и маска wipe. Если слою нужна прозрачность
 * «целиком», режим наложения или blur, он сначала рисуется на промежуточный
 * холст размером со свою рамку и уже оттуда кладётся в кадр.
 */
import { isLayerActive, resolveLayer, type LayerState } from "./animate";
import type { FrameInfo, LayerRenderer } from "./layers/common";
import { captionsRenderer } from "./layers/captions";
import { chatRenderer } from "./layers/chat";
import { rgba } from "./layers/common";
import { calloutRenderer } from "./layers/callout";
import { chartRenderer } from "./layers/chart";
import { counterRenderer, progressRenderer } from "./layers/counter";
import { emojiRenderer, imageRenderer } from "./layers/image";
import { shapeRenderer } from "./layers/shape";
import { textRenderer } from "./layers/text";
import type { BlendMode, CaptionWord, ChatMessage, Composition, Ctx2D, Layer, LayerType, RenderEnv, ScratchCanvas } from "./types";
import { REF_W } from "./types";

const RENDERERS: Partial<Record<LayerType, LayerRenderer>> = {
  text: textRenderer as LayerRenderer,
  shape: shapeRenderer as LayerRenderer,
  image: imageRenderer as LayerRenderer,
  emoji: emojiRenderer as LayerRenderer,
  counter: counterRenderer as LayerRenderer,
  progress: progressRenderer as LayerRenderer,
  chart: chartRenderer as LayerRenderer,
  callout: calloutRenderer as LayerRenderer,
  captions: captionsRenderer as LayerRenderer,
  chat: chatRenderer as LayerRenderer,
};

const NO_WORDS: CaptionWord[] = [];
const NO_CHAT: ChatMessage[] = [];

/**
 * Данные хоста для слоёв, которые рисуют не свои props, а данные клипа:
 * слова речи (`captions`) и сообщения чата (`chat`). Ради совместимости вместо
 * объекта можно передать просто массив слов.
 */
export type FrameData = { words?: CaptionWord[]; chat?: ChatMessage[] };

function frameOf(t: number, data?: CaptionWord[] | FrameData): FrameInfo {
  if (Array.isArray(data)) return { t, words: data, chat: NO_CHAT };
  return { t, words: data?.words || NO_WORDS, chat: data?.chat || NO_CHAT };
}

const BLEND: Record<BlendMode, GlobalCompositeOperation> = {
  normal: "source-over",
  multiply: "multiply",
  screen: "screen",
  overlay: "overlay",
  add: "lighter",
};

/** Слои, видимые в момент `t`, в порядке рисования: дорожка снизу вверх, внутри — по списку. */
export function visibleLayers(layers: Layer[], t: number): Layer[] {
  return layers
    .map((layer, index) => ({ layer, index }))
    .filter(({ layer }) => isLayerActive(layer, t))
    .sort((a, b) => (a.layer.track ?? 0) - (b.layer.track ?? 0) || a.index - b.index)
    .map(({ layer }) => layer);
}

/**
 * Нарисовать все слои композиции в момент клипового времени `t`.
 * `comp.words` — слова речи клипа для слоёв `captions` (нет — субтитры пустые).
 */
export function drawFrame(
  ctx: Ctx2D,
  comp: Pick<Composition, "layers"> & FrameData,
  t: number,
  env: RenderEnv,
  pass?: "behind" | "front",
): void {
  const data: FrameData = { words: comp.words, chat: comp.chat };
  const visible = visibleLayers(comp.layers || [], t);
  for (const layer of visible) {
    // С хромакеем компоновщик рисует в два прохода: под основой и над ней.
    if (pass && !!layer.behind !== (pass === "behind")) continue;
    // Крупное слово-панчлайн рядом с субтитрами по высоте: субтитры на это время
    // прячем — иначе два одинаковых текста друг над другом («НЕ ПОЙ БОЛЬШЕ!» дважды).
    if (layer.type === "captions" && visible.some((o) => o !== layer && isNearText(o, layer))) continue;
    drawLayer(ctx, layer, t, env, data);
  }
}

/** Крупный текст (панчлайн, счётчик) рядом с субтитрами по высоте — ближе 0.16 кадра.
 *  Хук и мелкие подписи (size < 80) субтитры не прячут. */
function isNearText(other: Layer, captions: Layer): boolean {
  if (other.hidden || (other.type !== "text" && other.type !== "counter") || other.id === "auto_hook") return false;
  const size = Number((other.props as { size?: unknown } | undefined)?.size ?? 0);
  if (other.type === "text" && size < 80) return false;
  return Math.abs((other.y ?? 0.5) - (captions.y ?? 0.68)) < 0.16;
}

/* ---------- геометрия слоя на кадре ---------- */

export type LayerBox = {
  /** Центр слоя в пикселях холста. */
  cx: number;
  cy: number;
  /** Размер содержимого в пикселях холста (уже с масштабом слоя). */
  w: number;
  h: number;
  /** Поворот в градусах. */
  rotation: number;
};

/**
 * Рамка слоя на холсте в момент `t` — для рамки выделения и попадания мышью в
 * редакторе. null — слой сейчас не виден.
 */
export function layerBox(ctx: Ctx2D, layer: Layer, t: number, env: RenderEnv, data?: CaptionWord[] | FrameData): LayerBox | null {
  const r = RENDERERS[layer.type];
  const st = resolveLayer(layer, t);
  if (!r || !st) return null;
  ctx.save();
  const size = r.measure(ctx, layer, st, env, frameOf(t, data));
  ctx.restore();
  const k = (env.width / REF_W) * st.scale;
  return { cx: st.x * env.width, cy: st.y * env.height, w: size.w * k, h: size.h * k, rotation: st.rotation };
}

/** Попала ли точка холста (px, py) в слой (с учётом поворота). */
export function hitLayer(box: LayerBox, px: number, py: number): boolean {
  const a = (-box.rotation * Math.PI) / 180;
  const dx = px - box.cx;
  const dy = py - box.cy;
  const x = dx * Math.cos(a) - dy * Math.sin(a);
  const y = dx * Math.sin(a) + dy * Math.cos(a);
  return Math.abs(x) <= box.w / 2 && Math.abs(y) <= box.h / 2;
}

/* ---------- промежуточный холст ---------- */

/** Один промежуточный холст на окружение: слои рисуются по очереди, хватает одного. */
const scratchByEnv = new WeakMap<RenderEnv, { canvas: ScratchCanvas; ctx: Ctx2D }>();

function scratchFor(env: RenderEnv, w: number, h: number): { canvas: ScratchCanvas; ctx: Ctx2D } | null {
  if (!env.createCanvas) return null;
  let s = scratchByEnv.get(env);
  if (!s || s.canvas.width < w || s.canvas.height < h) {
    // Растёт ступенями по 256 px, чтобы не пересоздаваться на каждом кадре «pop».
    const cw = Math.min(env.width, Math.max(s?.canvas.width ?? 0, Math.ceil(w / 256) * 256));
    const ch = Math.min(env.height, Math.max(s?.canvas.height ?? 0, Math.ceil(h / 256) * 256));
    const canvas = env.createCanvas(Math.max(1, cw), Math.max(1, ch));
    s = { canvas, ctx: canvas.getContext("2d") as Ctx2D };
    scratchByEnv.set(env, s);
  }
  return s;
}

/** Описанный прямоугольник повёрнутого слоя на холсте, с полями под обводку и тень. */
function deviceBounds(st: LayerState, size: { w: number; h: number }, env: RenderEnv, margin: number) {
  const k = (env.width / REF_W) * st.scale;
  const hw = (size.w / 2) * k + margin;
  const hh = (size.h / 2) * k + margin;
  const a = (st.rotation * Math.PI) / 180;
  const c = Math.abs(Math.cos(a));
  const s = Math.abs(Math.sin(a));
  const ex = hw * c + hh * s;
  const ey = hw * s + hh * c;
  const cx = st.x * env.width;
  const cy = st.y * env.height;
  const x0 = Math.max(0, Math.floor(cx - ex));
  const y0 = Math.max(0, Math.floor(cy - ey));
  const x1 = Math.min(env.width, Math.ceil(cx + ex));
  const y1 = Math.min(env.height, Math.ceil(cy + ey));
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
}

/* ---------- один слой ---------- */

function applyLayerTransform(ctx: Ctx2D, st: LayerState, env: RenderEnv, ox = 0, oy = 0): void {
  const k = env.width / REF_W;
  ctx.translate(st.x * env.width - ox, st.y * env.height - oy);
  if (st.rotation) ctx.rotate((st.rotation * Math.PI) / 180);
  ctx.scale(st.scale * k, st.scale * k);
}

/** Маска wipe: раскрытие слева направо в системе слоя (поворачивается вместе с ним). */
function applyWipe(ctx: Ctx2D, st: LayerState, size: { w: number; h: number }): void {
  if (st.wipe >= 1) return;
  const pad = 60;
  const w = size.w + pad * 2;
  ctx.beginPath();
  ctx.rect(-size.w / 2 - pad, -size.h / 2 - pad, w * Math.max(0, st.wipe), size.h + pad * 2);
  ctx.clip();
}

function shadowParams(layer: Layer, st: LayerState, env: RenderEnv) {
  const sh = layer.shadow;
  if (!sh) return null;
  const op = sh.opacity ?? 0.6;
  const blur = sh.blur ?? 0;
  const x = sh.x ?? 0;
  const y = sh.y ?? 0;
  if (op <= 0 || (blur <= 0 && x === 0 && y === 0)) return null;
  // Тень задана в опорных px; в canvas shadow* не зависят от матрицы — масштабируем сами.
  const k = (env.width / REF_W) * st.scale;
  return { color: rgba(sh.color || "#000000", op), blur: blur * k, x: x * k, y: y * k };
}

/** Нарисовать один слой в момент `t` (ничего не делает, если он не виден). */
export function drawLayer(ctx: Ctx2D, layer: Layer, t: number, env: RenderEnv, data?: CaptionWord[] | FrameData): void {
  const frame = frameOf(t, data);
  const r = RENDERERS[layer.type];
  if (!r) return;
  const st = resolveLayer(layer, t);
  if (!st || st.opacity <= 0.002 || st.scale <= 0.002 || st.wipe <= 0 || st.draw <= 0) return;

  const blend = BLEND[layer.blend ?? "normal"] ?? "source-over";
  const shadow = shadowParams(layer, st, env);
  const blurPx = st.blur > 0.05 ? st.blur * (env.width / REF_W) * st.scale : 0;
  // Тень не требует промежуточного холста: её отбрасывает «силуэт» слоя (плашка,
  // обводка текста, заливка фигуры) — рендерер сам гасит тень после силуэта.
  // drawImage с тенью в Skia очень дорогой (десятки мс на кадр), поэтому тень
  // никогда не кладётся на готовый промежуточный холст.
  const needsScratch = (st.opacity < 0.999 && r.multipart) || blurPx > 0 || blend !== "source-over";

  ctx.save();
  const size = r.measure(ctx, layer, st, env, frame);
  ctx.restore();

  if (needsScratch) {
    const k = env.width / REF_W;
    const shadowRoom = shadow ? shadow.blur * 1.6 + Math.max(Math.abs(shadow.x), Math.abs(shadow.y)) : 0;
    const margin = 24 * k + shadowRoom + (blurPx ? blurPx * 2 : 0);
    const b = deviceBounds(st, size, env, margin);
    if (b.w <= 0 || b.h <= 0) return;
    const s = scratchFor(env, b.w, b.h);
    if (s) {
      const sc = s.ctx;
      sc.save();
      sc.setTransform(1, 0, 0, 1, 0, 0);
      sc.globalAlpha = 1;
      sc.globalCompositeOperation = "source-over";
      sc.clearRect(0, 0, b.w + 2, b.h + 2);
      if (shadow) setShadow(sc, shadow);
      applyLayerTransform(sc, st, env, b.x, b.y);
      applyWipe(sc, st, size);
      r.draw(sc, layer, st, env, frame);
      sc.restore();

      ctx.save();
      ctx.globalAlpha = st.opacity;
      ctx.globalCompositeOperation = blend;
      if (blurPx > 0) ctx.filter = `blur(${blurPx.toFixed(2)}px)`;
      ctx.drawImage(s.canvas as unknown as CanvasImageSource, 0, 0, b.w, b.h, b.x, b.y, b.w, b.h);
      ctx.restore();
      return;
    }
  }

  // Прямой путь: без промежуточного холста (быстрее; или фабрики холстов нет).
  ctx.save();
  ctx.globalAlpha = st.opacity;
  ctx.globalCompositeOperation = blend;
  if (shadow) setShadow(ctx, shadow);
  applyLayerTransform(ctx, st, env);
  applyWipe(ctx, st, size);
  r.draw(ctx, layer, st, env, frame);
  ctx.restore();
}

function setShadow(ctx: Ctx2D, sh: { color: string; blur: number; x: number; y: number }): void {
  ctx.shadowColor = sh.color;
  ctx.shadowBlur = sh.blur;
  ctx.shadowOffsetX = sh.x;
  ctx.shadowOffsetY = sh.y;
}
