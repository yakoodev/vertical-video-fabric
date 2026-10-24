/**
 * Состояние слоя в момент клипового времени `t`.
 *
 * Порядок из спеки: база слоя → ключевые кадры → поверх `in` / `out` / `loop`.
 * Анимации не перезаписывают значения, а комбинируются: сдвиги складываются,
 * масштаб и прозрачность перемножаются. Поэтому «pop» на входе работает и у слоя,
 * которому монтажёр сам поставил ключи масштаба.
 */
import { clamp, clamp01, ease } from "./easing";
import { hashString, interpolateKeys, noise1 } from "./keys";
import type { Crop, InOut, InOutType, Layer, LayerKeys, LayerType } from "./types";
import { REF_H, REF_W } from "./types";

/** chars — typewriter, words — слова с pop, rise — слова выезжают снизу из-под маски строки. */
export type Reveal = { mode: "chars" | "words" | "rise"; fraction: number };

export type LayerState = {
  /** Локальное время слоя (0 = start) и его длина. */
  local: number;
  duration: number;
  /** Центр слоя в долях кадра — уже со сдвигами анимаций. */
  x: number;
  y: number;
  scale: number;
  /** Градусы. */
  rotation: number;
  opacity: number;
  /** Общий «ход» 0..1 для counter/progress/chart/shape. */
  progress: number;
  /** Размытие в опорных px (анимация blur). */
  blur: number;
  /** Доля раскрытия маской слева направо (wipe), 1 — целиком. */
  wipe: number;
  /** Доля нарисованной обводки (draw), 1 — целиком. */
  draw: number;
  /** Посимвольное / пословное появление текста (typewriter / words / rise). */
  reveal: Reveal | null;
  /** Окно картинки/видео (image, video) в долях — база `props.crop` + ключи cropX/Y/W/H; null — вся картинка. */
  crop: Crop | null;
};

export const DEFAULT_IN_DURATION = 0.35;
export const DEFAULT_OUT_DURATION = 0.25;

/** Длина слоя на экране; у сломанного слоя — минимальные 0.1 с из спеки. */
export function layerDuration(layer: Layer): number {
  return Math.max(0.1, (layer.end ?? 0) - (layer.start ?? 0));
}

/** Виден ли слой в момент `t` (полуинтервал [start, end), как у субтитров). */
export function isLayerActive(layer: Layer, t: number): boolean {
  return !layer.hidden && layer.type !== "audio" && t >= layer.start && t < layer.start + layerDuration(layer);
}

/** Длительность входа/выхода: 0.05..3 с и не больше половины слоя. */
export function animDuration(anim: InOut | null | undefined, layerDur: number, fallback: number): number {
  if (!anim || anim.type === "none") return 0;
  const d = clamp(anim.duration ?? fallback, 0.05, 3);
  return Math.min(d, layerDur / 2);
}

/** Ход по умолчанию, если у слоя нет ключей `progress`. */
function defaultProgress(type: LayerType, local: number, dur: number): number {
  switch (type) {
    case "counter":
    case "progress":
      // Растёт за первую половину слоя и дальше держит итог.
      return ease("outCubic", local / (dur * 0.5));
    case "chart":
      return ease("outCubic", local / (dur * 0.4));
    default:
      return 1;
  }
}

const finite = (v: unknown, fallback: number) => (typeof v === "number" && Number.isFinite(v) ? v : fallback);

/**
 * Окно crop внутри картинки: размер 0.01..1, затем сдвиг 0..1−размер. Упёршись в край,
 * окно не сжимается, а стоит — иначе на конце скролла слой (экран телефона) менял бы высоту.
 */
export function clampCrop(c: Partial<Crop>): Crop {
  const w = clamp(finite(c.w, 1), 0.01, 1);
  const h = clamp(finite(c.h, 1), 0.01, 1);
  return { x: clamp(finite(c.x, 0), 0, 1 - w), y: clamp(finite(c.y, 0), 0, 1 - h), w, h };
}

/** Окно картинки в момент `local`: ключи cropX/Y/W/H поверх статичного `props.crop`. */
function resolveCrop(layer: Layer, keys: LayerKeys, local: number): Crop | null {
  if (layer.type !== "image" && layer.type !== "video") return null;
  const base = layer.props?.crop;
  const animated = keys.cropX?.length || keys.cropY?.length || keys.cropW?.length || keys.cropH?.length;
  if (!base && !animated) return null;
  const b = clampCrop(base ?? {});
  return clampCrop({
    x: interpolateKeys(keys.cropX, local, b.x),
    y: interpolateKeys(keys.cropY, local, b.y),
    w: interpolateKeys(keys.cropW, local, b.w),
    h: interpolateKeys(keys.cropH, local, b.h),
  });
}

/** Анимации, которые имеют смысл только для своих типов, у чужих заменяются похожими. */
function effectiveType(type: InOutType, layerType: LayerType): InOutType {
  if ((type === "typewriter" || type === "words") && layerType !== "text") return type === "words" ? "pop" : "fade";
  // Маска по строкам есть только у текста — остальным просто въезд снизу.
  if (type === "rise" && layerType !== "text") return "slideUp";
  if (type === "draw" && layerType !== "shape") return "wipe";
  return type;
}

/**
 * Применить вход (или выход) к состоянию. `a` — «присутствие»: 0 — слоя ещё
 * (уже) нет, 1 — анимация закончилась. Выход — тот же вход, проигранный назад.
 */
function applyInOut(st: LayerState, type: InOutType, a: number): void {
  if (a >= 1) return;
  const p = clamp01(a);
  switch (type) {
    case "none":
      return;
    case "fade":
      st.opacity *= ease("inOutQuad", p);
      return;
    case "pop":
      // outBack с перелётом ~10%: 0 → 1.1 → 1, как в спеке.
      st.scale *= Math.max(0, ease("outBack", p));
      st.opacity *= clamp01(p * 4);
      return;
    case "zoom":
      st.scale *= 0.6 + 0.4 * ease("outCubic", p);
      st.opacity *= ease("outCubic", p);
      return;
    case "slideUp":
    case "slideDown":
    case "slideLeft":
    case "slideRight": {
      const rest = 1 - ease("outCubic", p);
      // Сдвиг на 0.15 кадра: slideUp въезжает снизу вверх, slideLeft — справа налево.
      if (type === "slideUp") st.y += 0.15 * rest;
      if (type === "slideDown") st.y -= 0.15 * rest;
      if (type === "slideLeft") st.x += 0.15 * rest;
      if (type === "slideRight") st.x -= 0.15 * rest;
      // Видимым — за первую треть: иначе на коротком въезде (0.2 с) движение
      // проходит почти прозрачным и слайд выглядит как простой фейд.
      st.opacity *= clamp01(p * 3);
      return;
    }
    case "drop":
      st.y -= 0.22 * (1 - ease("outBounce", p));
      st.opacity *= clamp01(p * 5);
      return;
    case "spin":
      st.rotation += -90 * (1 - ease("outBack", p));
      st.scale *= ease("outCubic", p);
      st.opacity *= clamp01(p * 3);
      return;
    case "blur":
      st.blur = Math.max(st.blur, 28 * (1 - ease("outCubic", p)));
      st.opacity *= ease("outQuad", p);
      return;
    case "wipe":
      st.wipe = Math.min(st.wipe, ease("inOutCubic", p));
      return;
    case "draw":
      st.draw = Math.min(st.draw, ease("inOutCubic", p));
      return;
    case "typewriter":
      st.reveal = mergeReveal(st.reveal, { mode: "chars", fraction: p });
      return;
    case "words":
      st.reveal = mergeReveal(st.reveal, { mode: "words", fraction: p });
      return;
    case "rise":
      st.reveal = mergeReveal(st.reveal, { mode: "rise", fraction: p });
      return;
  }
}

function mergeReveal(prev: Reveal | null, next: Reveal): Reveal {
  if (!prev) return next;
  return next.fraction < prev.fraction ? next : prev;
}

/** Постоянное движение. Тряска — детерминированный шум от id слоя, без Math.random. */
function applyLoop(st: LayerState, layer: Layer, local: number, ref: { w: number; h: number }): void {
  const loop = layer.loop;
  if (!loop || loop.type === "none") return;
  const speed = clamp(loop.speed ?? 1, 0.1, 5);
  const amount = clamp(loop.amount ?? 1, 0, 3);
  const tau = Math.PI * 2;
  switch (loop.type) {
    case "pulse":
      st.scale *= 1 + 0.06 * amount * Math.sin(tau * 1.2 * speed * local);
      return;
    case "float":
      st.y += (14 * amount * Math.sin(tau * 0.45 * speed * local)) / ref.h;
      return;
    case "shake": {
      const seed = hashString(layer.id || "layer");
      const f = 14 * speed * local;
      st.x += (9 * amount * noise1(seed, f)) / ref.w;
      st.y += (9 * amount * noise1(seed + 101, f)) / ref.h;
      st.rotation += 1.6 * amount * noise1(seed + 202, f);
      return;
    }
    case "wiggle":
      st.rotation += 7 * amount * Math.sin(tau * 1.4 * speed * local);
      return;
    case "spin":
      st.rotation += 180 * speed * Math.max(amount, 0.05) * local;
      return;
    case "blink": {
      // Мягкий «квадрат»: долго виден, быстро гаснет и загорается.
      const s = Math.sin(tau * 1.5 * speed * local);
      const off = clamp01((-s - 0.2) / 0.5);
      st.opacity *= 1 - Math.min(1, amount) * 0.85 * off;
      return;
    }
  }
}

/**
 * Полное состояние слоя в клиповое время `t`; null — слой сейчас не виден.
 * Чистая функция: одинаковые входы → одинаковый результат (превью == рендер).
 * `ref` — опорный кадр формата (px покачивания и тряски); нет — 1080×1920.
 */
export function resolveLayer(layer: Layer, t: number, ref: { w: number; h: number } = { w: REF_W, h: REF_H }): LayerState | null {
  if (!isLayerActive(layer, t)) return null;
  const dur = layerDuration(layer);
  const local = t - layer.start;
  const keys = layer.keys || {};
  const st: LayerState = {
    local,
    duration: dur,
    x: interpolateKeys(keys.x, local, layer.x ?? 0.5),
    y: interpolateKeys(keys.y, local, layer.y ?? 0.5),
    scale: interpolateKeys(keys.scale, local, layer.scale ?? 1),
    rotation: interpolateKeys(keys.rotation, local, layer.rotation ?? 0),
    opacity: clamp01(interpolateKeys(keys.opacity, local, layer.opacity ?? 1)),
    progress: keys.progress?.length
      ? clamp01(interpolateKeys(keys.progress, local, 1))
      : defaultProgress(layer.type, local, dur),
    blur: 0,
    wipe: 1,
    draw: 1,
    reveal: null,
    crop: resolveCrop(layer, keys, local),
  };

  const dIn = animDuration(layer.in, dur, DEFAULT_IN_DURATION);
  if (dIn > 0 && layer.in) applyInOut(st, effectiveType(layer.in.type, layer.type), local / dIn);
  const dOut = animDuration(layer.out, dur, DEFAULT_OUT_DURATION);
  if (dOut > 0 && layer.out) applyInOut(st, effectiveType(layer.out.type, layer.type), (dur - local) / dOut);
  applyLoop(st, layer, local, ref);

  st.opacity = clamp01(st.opacity);
  st.scale = Math.max(0, st.scale);
  return st;
}
