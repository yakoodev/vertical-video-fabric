/**
 * Состояние слоя в момент клипового времени `t`.
 *
 * Порядок из спеки: база слоя → ключевые кадры → поверх `in` / `out` / `loop`.
 * Анимации не перезаписывают значения, а комбинируются: сдвиги складываются,
 * масштаб и прозрачность перемножаются. Поэтому «pop» на входе работает и у слоя,
 * которому монтажёр сам поставил ключи масштаба.
 */
import { clamp, clamp01, ease } from "./easing";
import { hashString, interpolateKeys, noise1, rand01 } from "./keys";
import type { Crop, InOut, InOutType, Layer, LayerKeys, LayerType, LoopType } from "./types";
import { reactAt, type Levels } from "./levels";
import { REF_H, REF_W } from "./types";

/**
 * chars — typewriter, words — слова с pop, rise — слова выезжают снизу из-под маски строки,
 * scramble — буквы перебираются случайными символами и «расшифровываются» слева направо.
 */
export type Reveal = { mode: "chars" | "words" | "rise" | "scramble"; fraction: number };

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
  /** Сила глитча текста 0..1 (вход/выход glitch, вспышки цикла glitch); 0 — чисто. */
  glitch: number;
  /** Волна букв текста (цикл wave): сила `wave` (0 — нет) и скорость `waveSpeed`. */
  wave: number;
  waveSpeed: number;
  /** Свечение от звука (react glow) 0..; 0 — нет. */
  glow: number;
  /** Псевдо-3D (space3d.ts): глубина, опорн. px, и наклоны, градусы (база + ключи). */
  z: number;
  rotX: number;
  rotY: number;
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
    case "social":
      // Карточка: лайки считают вверх за первые 60 % слоя.
      return ease("outCubic", local / (dur * 0.6));
    default:
      return 1;
  }
}

/**
 * Ход слоя `progress` в момент `local` (секунды слоя) — ключи или ход по умолчанию типа. Нужен
 * рендерерам, которым ход нужен со сдвигом во времени: разряды одометра стартуют лесенкой.
 */
export function layerProgress(layer: Layer, local: number): number {
  const k = layer.keys?.progress;
  return k?.length ? clamp01(interpolateKeys(k, local, 1)) : defaultProgress(layer.type, local, layerDuration(layer));
}

/**
 * Наезд вокруг точки `pivot` (доли кадра): ключи scale растят слой вокруг неё, а не вокруг центра —
 * центр уходит от точки во столько же раз (во сколько ключ больше базы `scale`). У группы с одним
 * pivot и одинаковыми ключами наезд общий: блоки страницы приближаются как один экран.
 */
function applyPivotZoom(st: LayerState, layer: Layer, keys: LayerKeys, local: number): void {
  const pv = layer.pivot;
  if (!pv || !keys.scale?.length) return;
  const base = layer.scale ?? 1;
  if (!(Math.abs(base) > 1e-6)) return;
  const k = interpolateKeys(keys.scale, local, base) / base;
  if (Math.abs(k - 1) < 1e-9) return;
  // Сдвиг в px кадра растёт в k раз — в долях по каждой оси тоже ровно в k.
  const px = finite(pv.x, 0.5);
  const py = finite(pv.y, 0.5);
  st.x = px + (st.x - px) * k;
  st.y = py + (st.y - py) * k;
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
  // Глитч и расшифровка букв — только у текста; эффект кадра умеет лишь проявляться.
  if ((type === "glitch" || type === "scramble") && layerType !== "text") return "fade";
  if (layerType === "fx" && type !== "none" && type !== "fade") return "fade";
  return type;
}

/** Циклы букв — только у текста: у остальных глитч — тряска, волна — парение. */
function effectiveLoop(type: LoopType, layerType: LayerType): LoopType {
  if (layerType === "fx") return "none";
  if (layerType === "text") return type;
  if (type === "glitch") return "shake";
  if (type === "wave") return "float";
  return type;
}

/** Удар (stomp): момент «впечатывания» — доля входа. */
const STOMP_HIT = 0.38;

/**
 * Применить вход (или выход) к состоянию. `a` — «присутствие»: 0 — слоя ещё
 * (уже) нет, 1 — анимация закончилась. Выход — тот же вход, проигранный назад.
 * `seed` и `local` — для встряски удара (детерминированный шум, как у loop shake).
 */
function applyInOut(st: LayerState, type: InOutType, a: number, seed: number, local: number, ref: { w: number; h: number }): void {
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
    case "scramble":
      st.reveal = mergeReveal(st.reveal, { mode: "scramble", fraction: p });
      return;
    case "glitch":
      // Расслоение и срезы сильные в начале и сходят на нет к концу входа; мерцает рендерер.
      st.glitch = Math.max(st.glitch, 1 - ease("inQuad", p));
      st.opacity *= clamp01(p * 3);
      return;
    case "stomp": {
      if (p < STOMP_HIT) {
        // Влёт: из 2.2× к 1 с разгоном (inQuad) — самая большая скорость в момент удара.
        const q = p / STOMP_HIT;
        st.scale *= 2.2 - 1.2 * q * q;
        st.opacity *= clamp01(q * 3);
        return;
      }
      // После удара: короткое «сплющивание» и встряска, затухающие к концу входа.
      const q = (p - STOMP_HIT) / (1 - STOMP_HIT);
      const decay = (1 - q) * (1 - q);
      st.scale *= 1 - 0.07 * Math.sin(Math.PI * Math.min(1, q * 2)) * (1 - q);
      const f = 38 * local;
      st.x += (22 * decay * noise1(seed + 303, f)) / ref.w;
      st.y += (22 * decay * noise1(seed + 404, f)) / ref.h;
      st.rotation += 2.5 * decay * noise1(seed + 505, f);
      return;
    }
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
  switch (effectiveLoop(loop.type, layer.type)) {
    case "glitch": {
      // Короткие вспышки глитча: раз в период (1.6 с / speed) на ~0.22 с в своём месте периода.
      const seed = hashString(layer.id || "layer");
      const period = 1.6 / speed;
      const k = Math.floor(local / period);
      const at = rand01(seed + 17, k) * (period - 0.22);
      const u = (local - k * period - at) / 0.22;
      if (u > 0 && u < 1) st.glitch = Math.max(st.glitch, Math.min(1, 0.85 * amount) * Math.sin(Math.PI * u));
      return;
    }
    case "wave":
      st.wave = amount;
      st.waveSpeed = speed;
      return;
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
 * Реакция на звук (`react`): громкость 0..1 в момент клипа `t` поверх анимаций — масштаб и
 * прозрачность умножаются, подскок прибавляется (как у остальных анимаций). amount 1: масштаб
 * до +25 %, подскок до 40 опорн. px; прозрачность — в тишине 1 − amount, в громком месте 1.
 */
function applyReact(st: LayerState, layer: Layer, t: number, levels: Levels | null | undefined, ref: { w: number; h: number }): void {
  const r = reactAt(layer.react, levels, t);
  if (!r) return;
  switch (r.prop) {
    case "scale":
      st.scale *= 1 + 0.25 * r.amount * r.value;
      return;
    case "opacity":
      st.opacity *= 1 - Math.min(1, r.amount) * (1 - r.value);
      return;
    case "y":
      st.y -= (40 * r.amount * r.value) / ref.h;
      return;
    case "glow":
      st.glow = Math.max(st.glow, r.amount * r.value);
      return;
  }
}

/**
 * Полное состояние слоя в клиповое время `t`; null — слой сейчас не виден.
 * Чистая функция: одинаковые входы → одинаковый результат (превью == рендер).
 * `ref` — опорный кадр формата (px покачивания и тряски); нет — 1080×1920.
 */
export function resolveLayer(
  layer: Layer,
  t: number,
  ref: { w: number; h: number } = { w: REF_W, h: REF_H },
  levels?: Levels | null,
): LayerState | null {
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
    glitch: 0,
    wave: 0,
    waveSpeed: 1,
    glow: 0,
    z: interpolateKeys(keys.z, local, layer.z ?? 0),
    rotX: interpolateKeys(keys.rotX, local, layer.rotX ?? 0),
    rotY: interpolateKeys(keys.rotY, local, layer.rotY ?? 0),
  };

  applyPivotZoom(st, layer, keys, local);

  const seed = hashString(layer.id || "layer");
  const dIn = animDuration(layer.in, dur, DEFAULT_IN_DURATION);
  if (dIn > 0 && layer.in) applyInOut(st, effectiveType(layer.in.type, layer.type), local / dIn, seed, local, ref);
  const dOut = animDuration(layer.out, dur, DEFAULT_OUT_DURATION);
  if (dOut > 0 && layer.out) applyInOut(st, effectiveType(layer.out.type, layer.type), (dur - local) / dOut, seed, local, ref);
  applyLoop(st, layer, local, ref);
  applyReact(st, layer, t, levels, ref);

  st.opacity = clamp01(st.opacity);
  st.scale = Math.max(0, st.scale);
  return st;
}
