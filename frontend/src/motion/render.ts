/**
 * Отрисовка кадра: все видимые слои в момент `t` поверх того, что уже на холсте.
 *
 * Слой рисуется в своей системе координат — опорные px с центром в якоре, — а
 * сюда вынесено общее: перенос/поворот/масштаб (× W / ширина опорного кадра), прозрачность,
 * режим наложения, тень, размытие и маска wipe. Если слою нужна прозрачность
 * «целиком», режим наложения или blur, он сначала рисуется на промежуточный
 * холст размером со свою рамку и уже оттуда кладётся в кадр.
 */
import { isLayerActive, layerDuration, resolveLayer, type LayerState } from "./animate";
import { frameCameraMatrix, isIdentityCamera, type CameraMatrix } from "./camera";
import { hashString } from "./keys";
import { blurSamples, maxTravel, MOTION_BLUR_THRESHOLD, paintLayerBlur, shutterTimes, shutterValue } from "./motionblur";
import { sceneTravel } from "./scene/render";
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
import { sceneRenderer, sceneViewport } from "./layers/scene";
import { shapeRenderer } from "./layers/shape";
import { socialRenderer } from "./layers/social";
import { pageRenderer } from "./layers/page";
import { bubblesRenderer } from "./layers/bubbles";
import { carouselRenderer } from "./layers/carousel";
import { flowRenderer } from "./layers/flow";
import { uiRenderer } from "./layers/flatui";
import { tileWallRenderer } from "./layers/tilewall";
import { waveformRenderer } from "./layers/waveform";
import { textRenderer } from "./layers/text";
import { videoRenderer } from "./layers/video";
import { drawFx } from "./layers/fx";
import { roundRectPath } from "./layers/common";
import {
  flowGroups,
  flowRegion,
  flowState,
  isIdentityXf,
  isTransition,
  morphLayers,
  type FlowBox,
  type FlowFill,
  type FlowMask,
  type FlowRing,
  type FlowState,
  type GroupXf,
} from "./flow";
import type { Levels } from "./levels";
import type { BlendMode, CameraKey, CaptionWord, ChatMessage, Composition, Ctx2D, FxLayer, Layer, LayerType, RenderEnv, SceneLayer, ScratchCanvas, VoiceTrack } from "./types";
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
  social: socialRenderer as LayerRenderer,
  page: pageRenderer as LayerRenderer,
  captions: captionsRenderer as LayerRenderer,
  chat: chatRenderer as LayerRenderer,
  pngtuber: pngtuberRenderer as LayerRenderer,
  scene: sceneRenderer as LayerRenderer,
  bubbles: bubblesRenderer as LayerRenderer,
  carousel: carouselRenderer as LayerRenderer,
  waveform: waveformRenderer as LayerRenderer,
  tile_wall: tileWallRenderer as LayerRenderer,
  flow_diagram: flowRenderer as LayerRenderer,
  ui: uiRenderer as LayerRenderer,
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
  return liftOverGrayscale(
    layers
      .map((layer, index) => ({ layer, index }))
      .filter(({ layer }) => isLayerActive(layer, t))
      .sort((a, b) => (a.layer.track ?? 0) - (b.layer.track ?? 0) || a.index - b.index)
      .map(({ layer }) => layer),
  );
}

/** Слои, которые ч/б и негатив не красят: субтитры (подсветка слова остаётся жёлтой) и плашка канала. */
const ABOVE_GRAYSCALE = new Set<Layer["type"]>(["captions", "nameplate"]);

/**
 * Ч/б (fx grayscale) красит всё, что ниже по дорожкам, — и субтитры на дорожке 0 (QA 02.11, клип 895:
 * жёлтая подсветка слова на панчлайне стала серой). Субтитры и плашку ниже последнего ч/б рисуем сразу
 * над ним, в прежнем порядке; остальное — как было. Так же и фон под плашками (fx backdrop): кадр под
 * плашками размыт, а субтитры читаются резкими.
 */
export function liftOverGrayscale(visible: Layer[]): Layer[] {
  let last = -1;
  visible.forEach((layer, i) => {
    const kind = layer.type === "fx" && !layer.behind ? (layer as FxLayer).props?.kind : null;
    if (kind === "grayscale" || kind === "negate" || kind === "backdrop") last = i;
  });
  if (last < 0) return visible;
  const lifted = visible.slice(0, last).filter((l) => ABOVE_GRAYSCALE.has(l.type) && !l.behind);
  if (!lifted.length) return visible;
  const rest = visible.slice(0, last + 1).filter((l) => !lifted.includes(l));
  return [...rest, ...lifted, ...visible.slice(last + 1)];
}

/**
 * Нарисовать все слои композиции в момент клипового времени `t`.
 * `comp.words` — слова речи клипа для слоёв `captions` (нет — субтитры пустые).
 * `comp.motionBlur` — затвор клипа по умолчанию (motionblur.ts), `comp.fps` — для него же.
 */
export function drawFrame(
  ctx: Ctx2D,
  comp: Pick<Composition, "layers"> & Partial<Pick<Composition, "camera" | "cameraBox" | "key" | "fps" | "motionBlur">> & FrameData,
  t: number,
  env: RenderEnv,
  pass?: "behind" | "front",
): void {
  const data: FrameData = { words: comp.words, chat: comp.chat, voice: comp.voice, levels: comp.levels };
  const visible = visibleLayers(comp.layers || [], t);
  const box = comp.key ? null : comp.cameraBox;
  // 3D-камера (dolly, облёт, фокусное); в раскладке стрима (окно игры) — только плоский наезд.
  const use3d = has3dCamera(comp.camera) && (comp.key || !comp.cameraBox);
  const camAt = (tt: number): CameraMatrix | null => (comp.camera?.length ? frameCameraMatrix(comp.camera, tt, env.width, env.height, box) : null);
  const cam3At = (tt: number): Camera3D | null => (use3d ? camera3dAt(comp.camera, tt) : null);
  let cam: CameraMatrix | null | undefined;
  const cam3 = cam3At(t);
  const fps = Number(comp.fps) > 0 ? Number(comp.fps) : 30;
  // Перетекающие переходы (flow.ts): слои сцен A и B рисуются группами со своей матрицей и маской.
  const flows = pass === "behind" ? null : planFlows(ctx, comp.layers || [], visible, t, env, data);
  for (const layer of visible) {
    if (layer.type === "transition") continue;
    // С хромакеем компоновщик рисует в два прохода: под основой и над ней.
    if (pass && !!layer.behind !== (pass === "behind")) continue;
    const part = flows?.member.get(layer.id);
    if (part) {
      if (cam === undefined) cam = camAt(t);
      drawFlowPart(ctx, part, layer, t, env, data, cam, cam3, comp.camera);
      continue;
    }
    if (layer.type === "fx") {
      // Эффект кадра — во весь кадр поверх уже нарисованного, без матрицы слоя и камеры.
      const st = resolveLayer(layer, t, refSize(env), comp.levels);
      if (st) drawFx(ctx, layer as FxLayer, st, env);
      continue;
    }
    // Крупное слово-панчлайн рядом с субтитрами по высоте: субтитры на это время
    // прячем — иначе два одинаковых текста друг над другом («НЕ ПОЙ БОЛЬШЕ!» дважды).
    if (layer.type === "captions" && visible.some((o) => o !== layer && isNearText(o, layer))) continue;
    // Motion blur: быстрый слой — подкадрами в пределах затвора; медленный и в покое — как всегда.
    const shutter = layerShutter(layer, comp.motionBlur);
    if (shutter > 0 && env.createCanvas) {
      const plan = layerBlurPlan(ctx, layer, t, env, data, shutter, fps, camAt, cam3At);
      if (plan) {
        const flat = normalBlend(layer);
        const ok = paintLayerBlur(
          ctx,
          env,
          plan.times,
          (target, tt) => paintScene(target, flat, tt, env, data, camAt(tt), cam3At(tt), comp.camera),
          plan.region,
          BLEND[layer.blend ?? "normal"] ?? "source-over",
        );
        if (ok) continue;
      }
    }
    if (cam === undefined) cam = camAt(t);
    paintScene(ctx, layer, t, env, data, cam, cam3, comp.camera);
  }
}

/** Один слой кадра в момент `t` со своей камерой (`cam` — плоский наезд, `cam3` — 3D). */
function paintScene(
  ctx: Ctx2D,
  layer: Layer,
  t: number,
  env: RenderEnv,
  data: FrameData,
  cam: CameraMatrix | null,
  cam3: Camera3D | null,
  keys: CameraKey[] | undefined,
): void {
  const scene = inScene(layer);
  if (has3dFields(layer) || (scene && cam3)) {
    // Глубина, наклоны или 3D-камера: плоскость слоя в пространстве (space3d.ts).
    drawLayer3D(ctx, layer, t, env, data, scene ? cam3 : null, scene ? cam : null);
    return;
  }
  if (layer.followCamera && keys?.length && cam && !isIdentityCamera(cam)) {
    // Слой едет и зумится вместе с видео: та же матрица, что у кадра основы
    // (с хромакеем — во весь кадр, в раскладке стрима — внутри окна игры).
    ctx.save();
    ctx.transform(cam.scale, 0, 0, cam.scale, cam.tx, cam.ty);
    drawLayer(ctx, layer, t, env, data);
    ctx.restore();
    return;
  }
  drawLayer(ctx, layer, t, env, data);
}

/* ---------- перетекающие переходы (слой transition, flow.ts) ---------- */

type ActiveFlow = {
  st: FlowState;
  A: Layer[];
  B: Layer[];
  /** Общий элемент (shared_element): копии слоя в промежуточном состоянии и слой, на месте которого их рисовать. */
  morph: Layer[];
  morphAt: Layer | null;
  drawn: Set<"A" | "B" | "ring">;
};
type FlowPart = { flow: ActiveFlow; side: "A" | "B" | "M" };
type FlowPlan = { member: Map<string, FlowPart> };

/** Переходы, активные в момент t: кто в какой группе и как её рисовать. null — переходов нет. */
function planFlows(ctx: Ctx2D, all: Layer[], visible: Layer[], t: number, env: RenderEnv, data: FrameData): FlowPlan | null {
  const trs = visible.filter(isTransition);
  if (!trs.length) return null;
  const member = new Map<string, FlowPart>();
  const order = new Map(visible.map((l, i) => [l.id, i]));
  const boxOf = (l: Layer): FlowBox | null => {
    const b = layerBox(ctx, l, t, env, data, null);
    return b && b.w > 0 && b.h > 0 ? { cx: b.cx, cy: b.cy, w: b.w, h: b.h } : null;
  };
  const register = (flow: ActiveFlow): void => {
    if (!flow.A.length) flow.drawn.add("A");
    if (!flow.B.length) flow.drawn.add("B");
    for (const l of flow.A) member.set(l.id, { flow, side: "A" });
    for (const l of flow.B) member.set(l.id, { flow, side: "B" });
  };
  for (const tr of trs) {
    const g = flowGroups(tr, all);
    const P = tr.props || ({} as typeof tr.props);
    let A = visible.filter((l) => g.from.has(l.id) && !member.has(l.id));
    let B = visible.filter((l) => g.to.has(l.id) && !member.has(l.id));
    const target = P.target ? visible.find((l) => l.id === P.target && !isTransition(l)) ?? null : null;
    const tBox = target ? boxOf(target) : null;
    const region = flowRegion(tr, env, tBox);
    if (P.kind === "shared_element" && target && tBox && !member.has(target.id)) {
      const match = P.match ? visible.find((l) => l.id === P.match && !isTransition(l)) ?? null : null;
      const mBox = match ? boxOf(match) : null;
      const st = flowState(tr, t, env, region, mBox);
      const sa = resolveLayer(target, t, refSize(env), data.levels);
      const sb = match ? resolveLayer(match, t, refSize(env), data.levels) : null;
      const morph = sa ? morphLayers(target, match, sa, sb, tBox, mBox, st.p, t, env) : [];
      const pair = [target, match].filter((l): l is Layer => !!l);
      const morphAt = pair.reduce((best, l) => ((order.get(l.id) ?? 0) > (order.get(best.id) ?? 0) ? l : best), pair[0]);
      A = A.filter((l) => !pair.includes(l));
      B = B.filter((l) => !pair.includes(l));
      const flow: ActiveFlow = { st, A, B, morph, morphAt, drawn: new Set() };
      for (const l of pair) member.set(l.id, { flow, side: "M" });
      register(flow);
      continue;
    }
    register({ st: flowState(tr, t, env, region, null), A, B, morph: [], morphAt: null, drawn: new Set() });
  }
  return member.size ? { member } : null;
}

/** Слой из группы перехода: вся группа рисуется на месте первого своего слоя (по дорожкам). */
function drawFlowPart(
  ctx: Ctx2D,
  part: FlowPart,
  layer: Layer,
  t: number,
  env: RenderEnv,
  data: FrameData,
  cam: CameraMatrix | null,
  cam3: Camera3D | null,
  keys: CameraKey[] | undefined,
): void {
  const f = part.flow;
  if (part.side === "M") {
    if (layer !== f.morphAt) return;
    for (const m of f.morph) paintScene(ctx, m, t, env, data, cam, cam3, keys);
    return;
  }
  if (f.drawn.has(part.side)) return;
  // Приходящая сцена — всегда над уходящей (B внутри кнопки A, B в дыре A): группа B, встреченная
  // раньше A (дорожка ниже), рисуется сразу после A.
  if (part.side === "B" && !f.drawn.has("A")) return;
  f.drawn.add(part.side);
  if (part.side === "B" && f.st.fill && f.st.fill.alpha > 0.002) paintFlowFill(ctx, f.st.fill);
  drawGroup(ctx, part.side === "A" ? f.A : f.B, part.side === "A" ? f.st.a : f.st.b, t, env, data, cam, cam3, keys);
  if (part.side === "A" && !f.drawn.has("B")) {
    f.drawn.add("B");
    if (f.st.fill && f.st.fill.alpha > 0.002) paintFlowFill(ctx, f.st.fill);
    drawGroup(ctx, f.B, f.st.b, t, env, data, cam, cam3, keys);
  }
  if (f.st.ring && f.drawn.has("A") && f.drawn.has("B") && !f.drawn.has("ring")) {
    f.drawn.add("ring");
    if (f.st.ring.alpha > 0.002) paintFlowRing(ctx, f.st.ring);
  }
}

type FlowBufs = { g: { canvas: ScratchCanvas; ctx: Ctx2D }; m: { canvas: ScratchCanvas; ctx: Ctx2D } };
const flowBufsByEnv = new WeakMap<RenderEnv, FlowBufs>();

/** Два холста во весь кадр на окружение: группа слоёв и она же после матрицы (перенос картинкой). */
function flowBuffers(env: RenderEnv): FlowBufs | null {
  if (!env.createCanvas) return null;
  let b = flowBufsByEnv.get(env);
  if (!b || b.g.canvas.width !== env.width || b.g.canvas.height !== env.height) {
    const mk = () => {
      const canvas = env.createCanvas!(Math.max(1, env.width), Math.max(1, env.height));
      return { canvas, ctx: canvas.getContext("2d") as Ctx2D };
    };
    b = { g: mk(), m: mk() };
    flowBufsByEnv.set(env, b);
  }
  return b;
}

function resetBuf(c: Ctx2D, W: number, H: number): void {
  c.setTransform(1, 0, 0, 1, 0, 0);
  c.globalAlpha = 1;
  c.globalCompositeOperation = "source-over";
  c.filter = "none";
  c.clearRect(0, 0, W, H);
}

/** Путь маски (без beginPath): круг или скруглённый прямоугольник. */
function maskShape(c: Ctx2D, m: FlowMask): void {
  if (m.shape === "circle") {
    const r = Math.max(0, m.hw);
    c.moveTo(m.cx + r, m.cy);
    c.arc(m.cx, m.cy, r, 0, Math.PI * 2);
  } else {
    roundRectPath(c, m.cx - m.hw, m.cy - m.hh, Math.max(0, m.hw * 2), Math.max(0, m.hh * 2), m.r);
  }
}

/**
 * Маска на холсте группы (px кадра). Только `destination-out` — Skia и браузер одинаково трогают лишь
 * покрытые пиксели: «оставить внутри» = стереть кадр вокруг фигуры, «вырезать» = стереть саму фигуру.
 * Растушёвка — радиальный градиент у круга и размытие пути у прямоугольника.
 */
function applyMask(c: Ctx2D, m: FlowMask, W: number, H: number): void {
  c.save();
  c.setTransform(1, 0, 0, 1, 0, 0);
  c.globalAlpha = 1;
  c.globalCompositeOperation = "destination-out";
  const f = m.feather;
  if (m.shape === "circle" && f > 0.5) {
    const r = Math.max(0, m.hw);
    const g = c.createRadialGradient(m.cx, m.cy, Math.max(0, r - f / 2), m.cx, m.cy, r + f / 2);
    g.addColorStop(0, m.invert ? "rgba(0,0,0,1)" : "rgba(0,0,0,0)");
    g.addColorStop(1, m.invert ? "rgba(0,0,0,0)" : "rgba(0,0,0,1)");
    c.fillStyle = g;
    c.fillRect(0, 0, W, H);
  } else {
    if (f > 0.5) c.filter = `blur(${(f / 2).toFixed(2)}px)`;
    c.fillStyle = "#000000";
    c.beginPath();
    if (!m.invert) {
      const pad = f * 2 + 4;
      c.rect(-pad, -pad, W + pad * 2, H + pad * 2);
    }
    maskShape(c, m);
    c.fill(m.invert ? "nonzero" : "evenodd");
  }
  c.restore();
}

/** Заливка под сценой B внутри маски (reveal_from props.fill). */
function paintFlowFill(ctx: Ctx2D, fill: FlowFill): void {
  ctx.save();
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.globalAlpha = fill.alpha;
  ctx.globalCompositeOperation = "source-over";
  if (fill.mask.feather > 0.5) ctx.filter = `blur(${(fill.mask.feather / 2).toFixed(2)}px)`;
  ctx.fillStyle = fill.color;
  ctx.beginPath();
  maskShape(ctx, fill.mask);
  ctx.fill();
  ctx.restore();
}

/** Светящееся кольцо по краю маски (props.edge). */
function paintFlowRing(ctx: Ctx2D, ring: FlowRing): void {
  ctx.save();
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.globalAlpha = ring.alpha;
  ctx.globalCompositeOperation = "source-over";
  ctx.strokeStyle = ring.color;
  ctx.lineWidth = ring.width;
  ctx.shadowColor = ring.color;
  ctx.shadowBlur = ring.width * 2.5;
  ctx.beginPath();
  maskShape(ctx, ring.mask);
  ctx.stroke();
  ctx.restore();
}

/**
 * Группа слоёв как одна картинка: матрица x' = s·x + t (px кадра), маска, общая прозрачность.
 * Без 3D-слоёв матрица ставится прямо под слои (векторно — наезд в 8× остаётся резким); со слоями
 * в перспективе (space3d сам сбрасывает матрицу) — группа рисуется как есть и переносится картинкой.
 */
function drawGroup(
  ctx: Ctx2D,
  members: Layer[],
  xf: GroupXf,
  t: number,
  env: RenderEnv,
  data: FrameData,
  cam: CameraMatrix | null,
  cam3: Camera3D | null,
  keys: CameraKey[] | undefined,
): void {
  if (!members.length || xf.alpha <= 0.002) return;
  if (isIdentityXf(xf)) {
    for (const l of members) paintScene(ctx, l, t, env, data, cam, cam3, keys);
    return;
  }
  const W = env.width;
  const H = env.height;
  const bufs = flowBuffers(env);
  if (!bufs) {
    // Нет фабрики холстов — матрица и прямая маска клипом (без растушёвки и общей прозрачности).
    ctx.save();
    if (xf.mask && !xf.mask.invert) {
      ctx.beginPath();
      maskShape(ctx, xf.mask);
      ctx.clip();
    }
    ctx.transform(xf.s, 0, 0, xf.s, xf.tx, xf.ty);
    for (const l of members) paintScene(ctx, l, t, env, data, cam, cam3, keys);
    ctx.restore();
    return;
  }
  const base = typeof ctx.getTransform === "function" ? ctx.getTransform() : null;
  const vector = !members.some((l) => has3dFields(l) || (inScene(l) && cam3));
  const g = bufs.g.ctx;
  resetBuf(g, W, H);
  if (vector) g.setTransform(xf.s, 0, 0, xf.s, xf.tx, xf.ty);
  if (base) g.transform(base.a, base.b, base.c, base.d, base.e, base.f);
  for (const l of members) paintScene(g, l, t, env, data, cam, cam3, keys);
  g.setTransform(1, 0, 0, 1, 0, 0);
  let out = bufs.g;
  if (!vector) {
    const m = bufs.m.ctx;
    resetBuf(m, W, H);
    m.imageSmoothingEnabled = true;
    m.setTransform(xf.s, 0, 0, xf.s, xf.tx, xf.ty);
    m.drawImage(bufs.g.canvas as unknown as CanvasImageSource, 0, 0);
    m.setTransform(1, 0, 0, 1, 0, 0);
    out = bufs.m;
  }
  if (xf.mask) applyMask(out.ctx, xf.mask, W, H);
  ctx.save();
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.globalAlpha = Math.min(1, xf.alpha);
  ctx.globalCompositeOperation = "source-over";
  ctx.drawImage(out.canvas as unknown as CanvasImageSource, 0, 0);
  ctx.restore();
}

/* ---------- motion blur ---------- */

/** Затвор слоя: свой `motionBlur`, иначе клипа; эффекты кадра и звук — без смаза. */
export function layerShutter(layer: Layer, clipDefault?: number | null): number {
  if (layer.type === "fx" || layer.type === "audio") return 0;
  return shutterValue(layer.motionBlur) ?? shutterValue(clipDefault) ?? 0;
}

const flatCache = new WeakMap<Layer, Layer>();

/** Копия слоя с наложением normal (подкадры копятся normal, режим слоя — при переносе в кадр). */
function normalBlend(layer: Layer): Layer {
  if (!layer.blend || layer.blend === "normal") return layer;
  let c = flatCache.get(layer);
  if (!c) flatCache.set(layer, (c = { ...layer, blend: "normal" } as Layer));
  return c;
}

/** Углы рамки слоя на кадре в состоянии `st` (с наездом, глубиной и 3D-камерой), px. */
function layerCorners(layer: Layer, st: LayerState, size: { w: number; h: number }, env: RenderEnv, cam: CameraMatrix | null, cam3: Camera3D | null): number[][] {
  const W = env.width;
  const H = env.height;
  const unit = layerUnit(env);
  const k = unit * st.scale;
  const a = (st.rotation * Math.PI) / 180;
  const c = Math.cos(a);
  const s = Math.sin(a);
  const local = [
    [-1, -1],
    [1, -1],
    [1, 1],
    [-1, 1],
  ].map(([sx, sy]) => {
    const lx = (sx * size.w * k) / 2;
    const ly = (sy * size.h * k) / 2;
    return [lx * c - ly * s, lx * s + ly * c];
  });
  const scene = inScene(layer);
  if (has3dFields(layer) || (scene && cam3)) {
    const pl = layerPlane(st, env, layer);
    const proj = makeProjector(scene ? cam3 : null, W, H, unit, scene ? cam : null);
    return local.map(([dx, dy]) => proj(...planePoint(pl, dx, dy, W, H)) ?? [pl.cx + dx, pl.cy + dy]);
  }
  const cx = st.x * W;
  const cy = st.y * H;
  const pts = local.map(([dx, dy]) => [cx + dx, cy + dy]);
  if (layer.followCamera && cam) return pts.map(([x, y]) => [cam.scale * x + cam.tx, cam.scale * y + cam.ty]);
  return pts;
}

type BlurPlan = { times: number[]; region: { x: number; y: number; w: number; h: number } | null };

/**
 * Нужен ли слою смаз в момент t: путь углов рамки за время затвора (и движение граней сцены
 * внутри рамки у слоя scene). null — медленно или в покое (рисуется один раз, как без размытия);
 * иначе моменты подкадров и прямоугольник кадра, куда они ложатся.
 */
export function layerBlurPlan(
  ctx: Ctx2D,
  layer: Layer,
  t: number,
  env: RenderEnv,
  data: FrameData,
  shutter: number,
  fps: number,
  camAt: (tt: number) => CameraMatrix | null = () => null,
  cam3At: (tt: number) => Camera3D | null = () => null,
): BlurPlan | null {
  const r = RENDERERS[layer.type];
  if (!r || !(shutter > 0) || !(fps > 0)) return null;
  const ref = refSize(env);
  const st = resolveLayer(layer, t, ref, data.levels);
  if (!st || st.opacity <= 0.002 || st.scale <= 0.002) return null;
  const frame = frameOf(t, data);
  ctx.save();
  const size = r.measure(ctx, layer, st, env, frame);
  ctx.restore();
  const lo = layer.start;
  const hi = layer.start + layerDuration(layer) - 1e-4;
  const half = shutter / fps / 2;
  const t0 = Math.max(lo, t - half);
  const t1 = Math.min(hi, t + half);
  const cornersAt = (tt: number): number[][] | null => {
    const s2 = resolveLayer(layer, tt, ref, data.levels);
    return s2 ? layerCorners(layer, s2, size, env, camAt(tt), cam3At(tt)) : null;
  };
  const a = cornersAt(t0);
  const b = cornersAt(t1);
  const unit = layerUnit(env);
  let travel = a && b ? maxTravel(a, b) / unit : 0;
  if (layer.type === "scene" && travel < MOTION_BLUR_THRESHOLD) {
    // Сцена крутится внутри неподвижной рамки — меряем сами грани.
    const vp = sceneViewport(layer as SceneLayer, env);
    const p = (layer as SceneLayer).props;
    const opts = { w: vp.w, h: vp.h, t: 0, dur: layerDuration(layer), env, clip: p?.clip !== false, seed: hashString(layer.id || "scene") };
    travel = Math.max(travel, sceneTravel(ctx, p?.scene, opts, t0 - layer.start, t1 - layer.start) * st.scale);
  }
  const n = blurSamples(travel, env.motionBlurSamples);
  if (n <= 1) return null;
  const times = shutterTimes(t, n, shutter, fps, lo, hi);
  // Частицы и сцена без обрезки рисуют вне рамки — накопитель во весь кадр.
  if (layer.type === "particles" || (layer.type === "scene" && (layer as SceneLayer).props?.clip === false)) return { times, region: null };
  // Прямоугольник всех подкадров с полями под тень, свечение, размытие и обводку.
  let x0 = Infinity;
  let y0 = Infinity;
  let x1 = -Infinity;
  let y1 = -Infinity;
  for (const tt of times) {
    for (const [x, y] of cornersAt(tt) ?? []) {
      x0 = Math.min(x0, x);
      y0 = Math.min(y0, y);
      x1 = Math.max(x1, x);
      y1 = Math.max(y1, y);
    }
  }
  if (!(x1 > x0 && y1 > y0)) return { times, region: null };
  const shadow = shadowParams(layer, st, env);
  const blurPx = st.blur > 0.05 ? st.blur * unit * st.scale : 0;
  const pad =
    48 * unit +
    (shadow ? shadow.blur * 1.6 + Math.max(Math.abs(shadow.x), Math.abs(shadow.y)) : 0) +
    blurPx * 2 +
    0.15 * Math.max(x1 - x0, y1 - y0);
  return { times, region: { x: x0 - pad, y: y0 - pad, w: x1 - x0 + pad * 2, h: y1 - y0 + pad * 2 } };
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

/**
 * Плоскость слоя в мире (px кадра) в его состоянии `st`. С `pivot` наклоны rotX/rotY вращают слой
 * вокруг этой точки (на плоскости кадра), а не вокруг его центра: центр уезжает по дуге, наклон —
 * тот же. Группа слоёв с одним pivot и одинаковыми ключами наклоняется как одна плоскость.
 */
export function layerPlane(st: LayerState, env: RenderEnv, layer?: Layer): Plane {
  const unit = layerUnit(env);
  const pl: Plane = { cx: st.x * env.width, cy: st.y * env.height, z: st.z * unit, rotX: st.rotX, rotY: st.rotY };
  const pv = layer?.pivot;
  if (!pv || (!st.rotX && !st.rotY)) return pl;
  const px = (Number.isFinite(pv.x) ? pv.x : 0.5) * env.width;
  const py = (Number.isFinite(pv.y) ? pv.y : 0.5) * env.height;
  // Вектор от точки к центру слоя — теми же поворотами, что planePoint (сначала rotX, потом rotY).
  const ax = (st.rotX * Math.PI) / 180;
  const ay = (st.rotY * Math.PI) / 180;
  const dx = pl.cx - px;
  const dy = pl.cy - py;
  const dz = pl.z;
  const y1 = dy * Math.cos(ax) + dz * Math.sin(ax);
  const z1 = -dy * Math.sin(ax) + dz * Math.cos(ax);
  const x2 = dx * Math.cos(ay) - z1 * Math.sin(ay);
  const z2 = dx * Math.sin(ay) + z1 * Math.cos(ay);
  return { ...pl, cx: px + x2, cy: py + y1, z: z2 };
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
  const pl = layerPlane(st, env, layer);
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
  // Плоский рисунок — вокруг центра слоя (с pivot плоскость в мире уехала, а рисунок нет).
  const fx = st.x * W;
  const fy = st.y * H;
  const x0 = Math.floor(Math.max(-W / 2, fx - ex));
  const y0 = Math.floor(Math.max(-H / 2, fy - ey));
  const x1 = Math.ceil(Math.min(W * 1.5, fx + ex));
  const y1 = Math.ceil(Math.min(H * 1.5, fy + ey));
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
  const world = (u: number, v: number) => planePoint(pl, u + x0 - fx, v + y0 - fy, W, H);
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
  let cx = st.x * env.width;
  let cy = st.y * env.height;
  if (has3dFields(layer)) {
    // Глубина: рамка — по проекции центра плоскости (наклон рамка не показывает; с pivot центр уехал).
    const pl = layerPlane(st, env, layer);
    const A = affinePlaneMatrix({ ...pl, rotX: 0, rotY: 0 }, makeProjector(null, env.width, env.height, layerUnit(env), inScene(layer) ? cam : null), env.width, env.height);
    if (A) z = { scale: A[0], tx: A[4], ty: A[5] };
    cx = pl.cx;
    cy = pl.cy;
  }
  const k = layerUnit(env) * st.scale * z.scale;
  return {
    cx: cx * z.scale + z.tx,
    cy: cy * z.scale + z.ty,
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
