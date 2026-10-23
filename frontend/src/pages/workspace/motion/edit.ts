/**
 * Правка слоёв в редакторе: выделение на кадре, ручки трансформа, ключевые кадры.
 *
 * Чистые функции над `Layer[]` — компонент держит список в стейте, а во время
 * перетаскивания правит черновик в ref и пишет в стейт один раз на отпускании
 * (как вставки): иначе весь редактор перерисовывался бы на каждое движение мыши.
 */
import {
  clampCrop,
  layerBox,
  hitLayer,
  visibleLayers,
  newLayerId,
  type AnimatableProp,
  type CaptionWord,
  type CropProp,
  type Ctx2D,
  type KeyPoint,
  type Layer,
  type LayerBox,
  type RenderEnv,
} from "@/motion";

export const MAX_TRACK = 7;
/** Ручки рамки в CSS-пикселях превью (переводятся в пиксели холста). */
const HANDLE_CSS = 9;
const ROTATE_GAP_CSS = 26;

export type TransformHandle = "move" | "scale" | "rotate";

/** Верхний видимый слой под точкой холста (звуки не рисуются — их не выбрать). */
export function layerAt(
  ctx: Ctx2D,
  layers: Layer[],
  t: number,
  env: RenderEnv,
  px: number,
  py: number,
  words?: CaptionWord[],
): Layer | null {
  const drawn = visibleLayers(layers, t).filter((l) => l.type !== "audio" && !l.hidden);
  for (let i = drawn.length - 1; i >= 0; i--) {
    const box = layerBox(ctx, drawn[i], t, env, words);
    if (box && hitLayer(box, px, py)) return drawn[i];
  }
  return null;
}

/** Точка в системе слоя (без поворота), относительно центра. */
function toLocal(box: LayerBox, px: number, py: number) {
  const a = (-box.rotation * Math.PI) / 180;
  const dx = px - box.cx;
  const dy = py - box.cy;
  return { x: dx * Math.cos(a) - dy * Math.sin(a), y: dx * Math.sin(a) + dy * Math.cos(a) };
}

/** Какая ручка выделенного слоя под точкой: уголок — масштаб, кружок сверху — поворот. */
export function handleAt(box: LayerBox, px: number, py: number, cssScale: number): TransformHandle | null {
  const r = HANDLE_CSS * cssScale;
  const p = toLocal(box, px, py);
  const hw = box.w / 2;
  const hh = box.h / 2;
  if (Math.hypot(p.x, p.y + hh + ROTATE_GAP_CSS * cssScale) <= r * 1.4) return "rotate";
  for (const [cx, cy] of [
    [-hw, -hh],
    [hw, -hh],
    [-hw, hh],
    [hw, hh],
  ]) {
    if (Math.abs(p.x - cx) <= r && Math.abs(p.y - cy) <= r) return "scale";
  }
  if (Math.abs(p.x) <= hw && Math.abs(p.y) <= hh) return "move";
  return null;
}

/** Рамка выделения: повёрнутый прямоугольник, уголки и ручка поворота. */
export function drawSelection(ctx: Ctx2D, box: LayerBox, cssScale: number, locked = false): void {
  const r = HANDLE_CSS * cssScale * 0.62;
  ctx.save();
  ctx.translate(box.cx, box.cy);
  ctx.rotate((box.rotation * Math.PI) / 180);
  const hw = box.w / 2;
  const hh = box.h / 2;
  ctx.lineWidth = 1.5 * cssScale;
  ctx.strokeStyle = locked ? "rgba(255,255,255,0.6)" : "#8b5cf6";
  ctx.shadowColor = "rgba(0,0,0,0.6)";
  ctx.shadowBlur = 4 * cssScale;
  ctx.strokeRect(-hw, -hh, box.w, box.h);
  ctx.shadowBlur = 0;
  if (!locked) {
    ctx.beginPath();
    ctx.moveTo(0, -hh);
    ctx.lineTo(0, -hh - ROTATE_GAP_CSS * cssScale);
    ctx.stroke();
    ctx.fillStyle = "#fff";
    for (const [cx, cy] of [
      [-hw, -hh],
      [hw, -hh],
      [-hw, hh],
      [hw, hh],
    ]) {
      ctx.fillRect(cx - r, cy - r, r * 2, r * 2);
      ctx.strokeRect(cx - r, cy - r, r * 2, r * 2);
    }
    ctx.beginPath();
    ctx.arc(0, -hh - ROTATE_GAP_CSS * cssScale, r * 1.15, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
  }
  ctx.restore();
}

/** Ключ окна картинки → поле `props.crop`. */
const CROP_FIELD: Record<CropProp, "x" | "y" | "w" | "h"> = { cropX: "x", cropY: "y", cropW: "w", cropH: "h" };

const isCropProp = (prop: AnimatableProp): prop is CropProp => prop in CROP_FIELD;

/** Значение свойства в базе слоя (без ключей и анимаций). */
export function baseValue(layer: Layer, prop: AnimatableProp): number {
  if (isCropProp(prop)) {
    const crop = layer.type === "image" || layer.type === "video" ? layer.props.crop : null;
    return clampCrop(crop ?? {})[CROP_FIELD[prop]];
  }
  const defaults = { x: 0.5, y: 0.5, scale: 1, rotation: 0, opacity: 1, progress: 1 };
  if (prop === "progress") return defaults.progress;
  const v = layer[prop];
  return typeof v === "number" ? v : defaults[prop];
}

const round = (v: number, digits = 4) => Number(v.toFixed(digits));

/**
 * Записать свойство как делают монтажки: если у свойства уже есть ключи, правка
 * ставит (или двигает) ключ в текущий момент слоя; если ключей нет — меняется
 * сама база. Так перетаскивание не ломает уже настроенную анимацию.
 */
export function setAnimated(layer: Layer, prop: AnimatableProp, value: number, localT: number): Layer {
  const keys = layer.keys?.[prop];
  if (keys?.length) {
    return { ...layer, keys: { ...layer.keys, [prop]: upsertKey(keys, localT, value) } };
  }
  if (prop === "progress") return layer;
  if (isCropProp(prop)) {
    if (layer.type !== "image" && layer.type !== "video") return layer;
    const crop = { ...clampCrop(layer.props.crop ?? {}), [CROP_FIELD[prop]]: round(value) };
    return { ...layer, props: { ...layer.props, crop } };
  }
  return { ...layer, [prop]: round(value) } as Layer;
}

export function upsertKey(points: KeyPoint[], t: number, v: number): KeyPoint[] {
  const tt = round(Math.max(0, t), 3);
  const near = points.findIndex((p) => Math.abs(p.t - tt) < 0.02);
  const next = near >= 0 ? points.map((p, i) => (i === near ? { ...p, v: round(v) } : p)) : [...points, { t: tt, v: round(v) }];
  return next.sort((a, b) => a.t - b.t);
}

/** Есть ли у свойства ключ ровно в этот момент слоя. */
export function keyAt(layer: Layer, prop: AnimatableProp, localT: number): KeyPoint | null {
  return layer.keys?.[prop]?.find((p) => Math.abs(p.t - localT) < 0.02) ?? null;
}

/** ◆ у свойства: включить ключ (первый ключ фиксирует текущую базу) или убрать его. */
export function toggleKey(layer: Layer, prop: AnimatableProp, localT: number, current: number): Layer {
  const keys = layer.keys?.[prop] ?? [];
  const existing = keyAt(layer, prop, localT);
  let next: KeyPoint[];
  if (existing) next = keys.filter((p) => p !== existing);
  else next = upsertKey(keys, localT, current);
  const all = { ...(layer.keys ?? {}) };
  if (next.length) all[prop] = next;
  else delete all[prop];
  return { ...layer, keys: all };
}

/** Самая нижняя дорожка, где отрезок [start, end] ни с кем не пересекается. */
export function freeTrack(layers: Layer[], start: number, end: number, skipId?: string): number {
  for (let track = 0; track <= MAX_TRACK; track++) {
    const busy = layers.some(
      (l) => l.id !== skipId && l.type !== "audio" && (l.track ?? 0) === track && l.start < end && l.end > start,
    );
    if (!busy) return track;
  }
  return MAX_TRACK;
}

/**
 * Положить новые слои в клип: свежие id, а графика всей пачки (шаблон из
 * нескольких слоёв) — на одну свободную дорожку. Порядок внутри пачки
 * сохраняется: в кадре слои шаблона лежат друг над другом так, как задумано.
 */
export function placeLayers(existing: Layer[], incoming: Layer[], clipDuration: number, track?: number | null): Layer[] {
  const fitted = incoming.map((raw) => {
    const len = Math.max(0.1, raw.end - raw.start);
    const start = clipDuration > 0 ? Math.max(0, Math.min(raw.start, clipDuration - Math.min(len, clipDuration))) : Math.max(0, raw.start);
    const end = clipDuration > 0 ? Math.min(clipDuration, start + len) : start + len;
    return { ...raw, id: newLayerId(), start: round(start, 3), end: round(Math.max(start + 0.1, end), 3) } as Layer;
  });
  const graphics = fitted.filter((l) => l.type !== "audio");
  if (graphics.length) {
    const from = Math.min(...graphics.map((l) => l.start));
    const to = Math.max(...graphics.map((l) => l.end));
    // Бросили на конкретную дорожку — туда и кладём; иначе — на свободную.
    const target = track != null ? Math.max(0, Math.min(MAX_TRACK, track)) : freeTrack(existing, from, to);
    for (const l of graphics) l.track = target;
  }
  for (const l of fitted) if (l.type === "audio") l.track = 0;
  return [...existing, ...fitted];
}

/** Копия слоя рядом по времени (Ctrl+D). */
export function duplicateLayer(layers: Layer[], id: string, clipDuration: number): { layers: Layer[]; id: string | null } {
  const src = layers.find((l) => l.id === id);
  if (!src) return { layers, id: null };
  const len = src.end - src.start;
  const start = Math.min(src.end, Math.max(0, clipDuration - len));
  const copy = { ...structuredClone(src), id: newLayerId(), start, end: start + len } as Layer;
  copy.track = copy.type === "audio" ? 0 : freeTrack(layers, copy.start, copy.end);
  return { layers: [...layers, copy], id: copy.id };
}

/** Короткое имя слоя для дорожки и списка. */
export function layerTitle(layer: Layer): string {
  if (layer.name) return layer.name;
  switch (layer.type) {
    case "text":
      return layer.props.text?.split("\n")[0]?.slice(0, 40) || "Текст";
    case "emoji":
      return layer.props.char || "Эмодзи";
    case "counter":
      return `${layer.props.prefix ?? ""}${layer.props.to ?? ""}${layer.props.suffix ?? ""}` || "Счётчик";
    case "callout":
      return layer.props.title || "Плашка";
    case "chart":
      return layer.props.title || "График";
    case "progress":
      return "Прогресс";
    case "shape":
      return { rect: "Прямоугольник", ellipse: "Круг", line: "Линия", arrow: "Стрелка", triangle: "Треугольник", star: "Звезда", burst: "Взрыв" }[layer.props.kind] ?? "Фигура";
    case "image":
      return "Картинка";
    case "video":
      return layer.props.device === "phone" ? "Видео в телефоне" : "Видео";
    case "audio":
      return "Звук";
    case "captions":
      return "Субтитры";
    case "chat":
      return "Чат стрима";
    default:
      return "Слой";
  }
}

export const LAYER_ICON: Record<Layer["type"], string> = {
  text: "T",
  shape: "◆",
  image: "🖼",
  video: "🎞",
  emoji: "😀",
  counter: "#",
  progress: "▭",
  chart: "📊",
  callout: "▤",
  captions: "💬",
  chat: "🗨",
  audio: "♪",
};
