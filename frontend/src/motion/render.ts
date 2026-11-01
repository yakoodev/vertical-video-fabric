/**
 * Отрисовка кадра: все видимые слои в момент `t` поверх того, что уже на холсте.
 *
 * Слой рисуется в своей системе координат — опорные px с центром в якоре, — а
 * сюда вынесено общее: перенос/поворот/масштаб (× W / ширина опорного кадра), прозрачность,
 * режим наложения, тень, размытие и маска wipe. Если слою нужна прозрачность
 * «целиком», режим наложения или blur, он сначала рисуется на промежуточный
 * холст размером со свою рамку и уже оттуда кладётся в кадр.
 */
import { isLayerActive, resolveLayer, type LayerState } from "./animate";
import { frameCameraMatrix, isIdentityCamera, type CameraMatrix } from "./camera";
import {
  affinePlaneMatrix,
  camera3dAt,
  cameraDepth,
  depthSkew,
  envBuffer,
  has3dCamera,
  isAffinePlane,
  makeProjector,
  planePoint,
  warpImage,
  type Camera3D,
  type Plane,
} from "./space3d";
import type { FrameInfo, LayerRenderer } from "./layers/common";
import { captionsRenderer } from "./layers/captions";
import { chatRenderer } from "./layers/chat";
import { rgba } from "./layers/common";
import { calloutRenderer } from "./layers/callout";
import { chartRenderer } from "./layers/chart";
import { counterRenderer, progressRenderer } from "./layers/counter";
import { nameplateRenderer } from "./layers/nameplate";
import { emojiRenderer, imageRenderer } from "./layers/image";
import { particlesRenderer } from "./layers/particles";
import { pngtuberRenderer } from "./layers/pngtuber";
import { sceneRenderer } from "./layers/scene";
import { shapeRenderer } from "./layers/shape";
import { textRenderer } from "./layers/text";
import { videoRenderer } from "./layers/video";
import { drawFx } from "./layers/fx";
import type { Levels } from "./levels";
import type { BlendMode, CaptionWord, ChatMessage, Composition, Ctx2D, FxLayer, Layer, LayerType, RenderEnv, ScratchCanvas, VoiceTrack } from "./types";
import { layerUnit, refSize } from "./types";

const RENDERERS: Partial<Record<LayerType, LayerRenderer>> = {
  text: textRenderer as LayerRenderer,
  shape: shapeRenderer as LayerRenderer,
  image: imageRenderer as LayerRenderer,
  video: videoRenderer as LayerRenderer,
  emoji: emojiRenderer as LayerRenderer,
  particles: particlesRenderer as LayerRenderer,
  counter: counterRenderer as LayerRenderer,
  progress: progressRenderer as LayerRenderer,
  chart: chartRenderer as LayerRenderer,
  callout: calloutRenderer as LayerRenderer,
  nameplate: nameplateRenderer as LayerRenderer,
  captions: captionsRenderer as LayerRenderer,
  chat: chatRenderer as LayerRenderer,
  pngtuber: pngtuberRenderer as LayerRenderer,
  scene: sceneRenderer as LayerRenderer,
};

const NO_WORDS: CaptionWord[] = [];
const NO_CHAT: ChatMessage[] = [];

/**
 * Данные хоста для слоёв, которые рисуют не свои props, а данные клипа:
 * слова речи (`captions`), сообщения чата (`chat`), состояния голоса (`pngtuber`,
 * по id слоя) и огибающие громкости (`react` любого слоя). Ради совместимости вместо
 * объекта можно передать просто массив слов.
 */
export type FrameData = { words?: CaptionWord[]; chat?: ChatMessage[]; voice?: Record<string, VoiceTrack>; levels?: Levels };

function frameOf(t: number, data?: CaptionWord[] | FrameData): FrameInfo {
  if (Array.isArray(data)) return { t, words: data, chat: NO_CHAT };
  return { t, words: data?.words || NO_WORDS, chat: data?.chat || NO_CHAT, voice: data?.voice, levels: data?.levels };
}

const BLEND: Record<BlendMode, GlobalCompositeOperation> = {
  normal: "source-over",
  multiply: "multiply",
  screen: "screen",
  overlay: "overlay",
  add: "lighter",
};

/** Слой «до конца клипа» — как app/motion_render.FINAL_HOLD_EPS. */
export const FINAL_HOLD_EPS = 0.05;
const heldCache = new WeakMap<Layer, { duration: number; layer: Layer }>();

/**
 * Слой до конца клипа держится на финальном стоп-кадре без анимации выхода — как
 * рендер (app/motion_render.hold_final_layers): ролик на стоп-кадр длиннее, и плашка-итог
 * с `out` гасла до него. Превью гасило её — теперь показывает то же, что рендер.
 * Копия слоя кэшируется, чтобы каждый кадр превью не плодил новые объекты.
 */
export function holdFinalLayers(layers: Layer[], duration: number | undefined): Layer[] {
  if (!duration || duration <= 0) return layers;
  let changed = false;
  const out = layers.map((layer) => {
    const anim = layer.out;
    if (!(Number(layer.end) >= duration - FINAL_HOLD_EPS) || !anim || anim.type === "none") return layer;
    changed = true;
    const hit = heldCache.get(layer);
    if (hit && hit.duration === duration) return hit.layer;
    const held: Layer = { ...layer, out: { ...anim, type: "none" } };
    heldCache.set(layer, { duration, layer: held });
    return held;
  });
  return changed ? out : layers;
}

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
  comp: Pick<Composition, "layers"> & Partial<Pick<Composition, "camera" | "cameraBox" | "key">> & FrameData,
  t: number,
  env: RenderEnv,
  pass?: "behind" | "front",
): void {
  const data: FrameData = { words: comp.words, chat: comp.chat, voice: comp.voice, levels: comp.levels };
  const visible = visibleLayers(comp.layers || [], t);
  let cam: CameraMatrix | null | undefined;
  // 3D-камера (dolly, облёт, фокусное); в раскладке стрима (окно игры) — только плоский наезд.
  const cam3 = has3dCamera(comp.camera) && (comp.key || !comp.cameraBox) ? camera3dAt(comp.camera, t) : null;
  for (const layer of visible) {
    // С хромакеем компоновщик рисует в два прохода: под основой и над ней.
    if (pass && !!layer.behind !== (pass === "behind")) continue;
    if (layer.type === "fx") {
      // Эффект кадра — во весь кадр поверх уже нарисованного, без матрицы слоя и камеры.
      const st = resolveLayer(layer, t, refSize(env), comp.levels);
      if (st) drawFx(ctx, layer as FxLayer, st, env);
      continue;
    }
    // Крупное слово-панчлайн рядом с субтитрами по высоте: субтитры на это время
    // прячем — иначе два одинаковых текста друг над другом («НЕ ПОЙ БОЛЬШЕ!» дважды).
    if (layer.type === "captions" && visible.some((o) => o !== layer && isNearText(o, layer))) continue;
    const scene = inScene(layer);
    if (has3dFields(layer) || (scene && cam3)) {
      // Глубина, наклоны или 3D-камера: плоскость слоя в пространстве (space3d.ts).
      cam ??= comp.camera?.length ? frameCameraMatrix(comp.camera, t, env.width, env.height, comp.key ? null : comp.cameraBox) : null;
      drawLayer3D(ctx, layer, t, env, data, scene ? cam3 : null, scene ? cam : null);
      continue;
    }
    if (layer.followCamera && comp.camera?.length) {
      // Слой едет и зумится вместе с видео: та же матрица, что у кадра основы
      // (с хромакеем — во весь кадр, в раскладке стрима — внутри окна игры).
      cam ??= frameCameraMatrix(comp.camera, t, env.width, env.height, comp.key ? null : comp.cameraBox);
      if (!isIdentityCamera(cam)) {
        ctx.save();
        ctx.transform(cam.scale, 0, 0, cam.scale, cam.tx, cam.ty);
        drawLayer(ctx, layer, t, env, data);
        ctx.restore();
        continue;
      }
    }
    drawLayer(ctx, layer, t, env, data);
  }
}

/* ---------- псевдо-3D ---------- */

/** Слой в сцене: едет с камерой (наезд и 3D) — `followCamera` или задана глубина `z`. */
export function inScene(layer: Layer): boolean {
  return Boolean(layer.followCamera) || typeof layer.z === "number";
}

/** У слоя есть 3D-поля (глубина, наклоны или их ключи) — рисуется через space3d. */
export function has3dFields(layer: Layer): boolean {
  const k = layer.keys;
  return (
    typeof layer.z === "number" ||
    Boolean(layer.rotX) ||
    Boolean(layer.rotY) ||
    Boolean(k?.z?.length || k?.rotX?.length || k?.rotY?.length)
  );
}

/** Плоскость слоя в мире (px кадра) в его состоянии `st`. */
function layerPlane(st: LayerState, env: RenderEnv): Plane {
  return { cx: st.x * env.width, cy: st.y * env.height, z: st.z * layerUnit(env), rotX: st.rotX, rotY: st.rotY };
}

/**
 * Слой в пространстве: без наклонов (только глубина, dolly, фокусное) — обычный рисунок под
 * аффинной матрицей проекции; с наклоном — плоский рисунок на промежуточный холст и перенос
 * сеткой с перспективой (space3d.warpImage). `cam3`/`m2d` — камера сцены (null — слой на экране).
 */
function drawLayer3D(ctx: Ctx2D, layer: Layer, t: number, env: RenderEnv, data: FrameData, cam3: Camera3D | null, m2d: CameraMatrix | null): void {
  const st = resolveLayer(layer, t, refSize(env), data.levels);
  const r = RENDERERS[layer.type];
  if (!st || !r || st.opacity <= 0.002 || st.scale <= 0.002) return;
  const W = env.width;
  const H = env.height;
  const unit = layerUnit(env);
  const pl = layerPlane(st, env);
  const proj = makeProjector(cam3, W, H, unit, m2d);
  if (isAffinePlane(pl, cam3)) {
    const A = affinePlaneMatrix(pl, proj, W, H);
    if (!A) return;
    ctx.save();
    ctx.transform(A[0], A[1], A[2], A[3], A[4], A[5]);
    drawLayer(ctx, layer, t, env, data);
    ctx.restore();
    return;
  }
  // Плоский рисунок слоя — рамка с полями под обводку, тень и размытие; не шире кадра вдвое.
  const frame = frameOf(t, data);
  ctx.save();
  const size = r.measure(ctx, layer, st, env, frame);
  ctx.restore();
  const shadow = shadowParams(layer, st, env);
  const blurPx = st.blur > 0.05 ? st.blur * unit * st.scale : 0;
  const margin = 24 * unit + (shadow ? shadow.blur * 1.6 + Math.max(Math.abs(shadow.x), Math.abs(shadow.y)) : 0) + blurPx * 2;
  const k = unit * st.scale;
  const a = (st.rotation * Math.PI) / 180;
  const hw = (size.w / 2) * k + margin;
  const hh = (size.h / 2) * k + margin;
  const ex = hw * Math.abs(Math.cos(a)) + hh * Math.abs(Math.sin(a));
  const ey = hw * Math.abs(Math.sin(a)) + hh * Math.abs(Math.cos(a));
  const x0 = Math.floor(Math.max(-W / 2, pl.cx - ex));
  const y0 = Math.floor(Math.max(-H / 2, pl.cy - ey));
  const x1 = Math.ceil(Math.min(W * 1.5, pl.cx + ex));
  const y1 = Math.ceil(Math.min(H * 1.5, pl.cy + ey));
  if (x1 <= x0 || y1 <= y0) return;
  const buf = envBuffer(env, "plane", x1 - x0, y1 - y0);
  if (!buf) {
    // Нет фабрики холстов — хотя бы без перспективы, по центру проекции.
    const A = affinePlaneMatrix({ ...pl, rotX: 0, rotY: 0 }, makeProjector(cam3 ? { ...cam3, rotX: 0, rotY: 0 } : null, W, H, unit, m2d), W, H);
    if (!A) return;
    ctx.save();
    ctx.transform(A[0], A[1], A[2], A[3], A[4], A[5]);
    drawLayer(ctx, layer, t, env, data);
    ctx.restore();
    return;
  }
  const bw = Math.min(x1 - x0, buf.canvas.width);
  const bh = Math.min(y1 - y0, buf.canvas.height);
  const p = buf.ctx;
  p.setTransform(1, 0, 0, 1, 0, 0);
  p.globalAlpha = 1;
  p.globalCompositeOperation = "source-over";
  p.clearRect(0, 0, bw + 2, bh + 2);
  p.translate(-x0, -y0);
  // Наложение — уже при переносе на кадр: на прозрачном холсте оно бессмысленно.
  drawLayer(p, layer.blend && layer.blend !== "normal" ? ({ ...layer, blend: "normal" } as Layer) : layer, t, env, data);
  p.setTransform(1, 0, 0, 1, 0, 0);
  const world = (u: number, v: number) => planePoint(pl, u + x0 - pl.cx, v + y0 - pl.cy, W, H);
  const [skX, skY] = depthSkew((u, v) => cameraDepth(cam3, H, unit, ...world(u, v)), 0, 0, bw, bh);
  warpImage(ctx, env, buf.canvas as unknown as CanvasImageSource, 0, 0, bw, bh, (u, v) => proj(...world(u, v)), skX, skY, {
    composite: BLEND[layer.blend ?? "normal"] ?? "source-over",
    cull: true,
  });
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
 * редакторе. null — слой сейчас не виден. `cam` — матрица камеры кадра: слою
 * `followCamera` рамка ставится туда, где он нарисован после наезда.
 */
export function layerBox(
  ctx: Ctx2D,
  layer: Layer,
  t: number,
  env: RenderEnv,
  data?: CaptionWord[] | FrameData,
  cam?: CameraMatrix | null,
): LayerBox | null {
  const frame = frameOf(t, data);
  if (layer.type === "fx") {
    // Эффект — во весь кадр: рамка выделения по краю кадра (мышью его не выбрать, см. edit.ts).
    return resolveLayer(layer, t, refSize(env), frame.levels)
      ? { cx: env.width / 2, cy: env.height / 2, w: env.width, h: env.height, rotation: 0 }
      : null;
  }
  const r = RENDERERS[layer.type];
  const st = resolveLayer(layer, t, refSize(env), frame.levels);
  if (!r || !st) return null;
  ctx.save();
  const size = r.measure(ctx, layer, st, env, frame);
  ctx.restore();
  let z = cam && layer.followCamera ? cam : { scale: 1, tx: 0, ty: 0 };
  if (has3dFields(layer)) {
    // Глубина: рамка — по проекции центра плоскости (наклон рамка не показывает).
    const pl = layerPlane(st, env);
    const A = affinePlaneMatrix({ ...pl, rotX: 0, rotY: 0 }, makeProjector(null, env.width, env.height, layerUnit(env), inScene(layer) ? cam : null), env.width, env.height);
    if (A) z = { scale: A[0], tx: A[4], ty: A[5] };
  }
  const k = layerUnit(env) * st.scale * z.scale;
  return {
    cx: st.x * env.width * z.scale + z.tx,
    cy: st.y * env.height * z.scale + z.ty,
    w: size.w * k,
    h: size.h * k,
    rotation: st.rotation,
  };
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
  const k = layerUnit(env) * st.scale;
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
  const k = layerUnit(env);
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
  if (st.glow > 0.002) {
    // Свечение от звука (react glow): тень цветом свечения, растёт с громкостью поверх
    // своей тени слоя (её размытие — основа, сдвиг сохраняется).
    const k = layerUnit(env) * st.scale;
    const g = Math.min(2, st.glow);
    const color = layer.react?.color || sh?.color || "#ffffff";
    const op = Math.min(1, (sh?.opacity ?? 0.9) * Math.min(1, 0.35 + g));
    return { color: rgba(color, op), blur: ((sh?.blur ?? 0) + 60 * g) * k, x: (sh?.x ?? 0) * k, y: (sh?.y ?? 0) * k };
  }
  if (!sh) return null;
  const op = sh.opacity ?? 0.6;
  const blur = sh.blur ?? 0;
  const x = sh.x ?? 0;
  const y = sh.y ?? 0;
  if (op <= 0 || (blur <= 0 && x === 0 && y === 0)) return null;
  // Тень задана в опорных px; в canvas shadow* не зависят от матрицы — масштабируем сами.
  const k = layerUnit(env) * st.scale;
  return { color: rgba(sh.color || "#000000", op), blur: blur * k, x: x * k, y: y * k };
}

/** Нарисовать один слой в момент `t` (ничего не делает, если он не виден). */
export function drawLayer(ctx: Ctx2D, layer: Layer, t: number, env: RenderEnv, data?: CaptionWord[] | FrameData): void {
  const frame = frameOf(t, data);
  const r = RENDERERS[layer.type];
  if (!r) return;
  const st = resolveLayer(layer, t, refSize(env), frame.levels);
  if (!st || st.opacity <= 0.002 || st.scale <= 0.002 || st.wipe <= 0 || st.draw <= 0) return;

  const blend = BLEND[layer.blend ?? "normal"] ?? "source-over";
  const shadow = shadowParams(layer, st, env);
  const blurPx = st.blur > 0.05 ? st.blur * layerUnit(env) * st.scale : 0;
  // Тень не требует промежуточного холста: её отбрасывает «силуэт» слоя (плашка,
  // обводка текста, заливка фигуры) — рендерер сам гасит тень после силуэта.
  // drawImage с тенью в Skia очень дорогой (десятки мс на кадр), поэтому тень
  // никогда не кладётся на готовый промежуточный холст.
  const needsScratch = (st.opacity < 0.999 && r.multipart) || blurPx > 0 || blend !== "source-over";

  ctx.save();
  const size = r.measure(ctx, layer, st, env, frame);
  ctx.restore();

  if (needsScratch) {
    const k = layerUnit(env);
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
