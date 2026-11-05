/**
 * Время смонтированного клипа: секунда готового ролика → секунда основы, кусок, секунда исходника.
 *
 * Модуль без импортов — его гоняет тест через node (tests/test_frontend_playback.py) и сверяет с
 * app/freeze.py. Числа (куски, наплывы, стоп-кадры) считает сервер (app/playback.py) тем же кодом,
 * что рендер; здесь только чтение плана в нужный момент.
 *
 * Шкалы: `t` — готовый ролик со стоп-кадрами; `base` — основа (куски после вырезов и наплывов).
 */

export type PlayPart = {
  index: number;
  start: number;
  end: number;
  src_start: number;
  src_end: number;
  speed: number;
  reverse: boolean;
  /** Наплыв на следующий кусок, с (0 — встык). */
  overlap: number;
  jump: boolean;
};

export type FreezeSpan = { at: number; duration: number; audio?: string; layers?: string };

const isInsert = (f: FreezeSpan) => (f.audio ?? "silence") === "silence";

/** Секунда основы в момент `t`: на вставленном стоп-кадре стоит, «поверх» — картинка стоит, звук идёт. */
export function baseAt(
  freezes: readonly FreezeSpan[] | undefined,
  t: number,
): { base: number; inserted: boolean; overlaid: number | null } {
  let off = 0;
  for (const f of freezes ?? []) {
    const atPost = f.at + off;
    if (t < atPost) break;
    if (isInsert(f)) {
      if (t < atPost + f.duration) return { base: f.at, inserted: true, overlaid: null };
      off += f.duration;
    } else if (t < atPost + f.duration) {
      return { base: t - off, inserted: false, overlaid: f.at };
    }
  }
  return { base: t - off, inserted: false, overlaid: null };
}

/** Обратно: секунда основы → готовый ролик (ровно на стоп-кадре — его начало). */
export function finalOf(freezes: readonly FreezeSpan[] | undefined, base: number): number {
  let off = 0;
  for (const f of freezes ?? []) {
    if (isInsert(f) && f.at < base) off += f.duration;
  }
  return base + off;
}

/** Длина всех вставленных стоп-кадров. */
export function insertedTotal(freezes: readonly FreezeSpan[] | undefined): number {
  return (freezes ?? []).reduce((sum, f) => sum + (isInsert(f) ? f.duration : 0), 0);
}

/** Первый вставленный стоп-кадр, в который основа вошла на этом шаге (prev, next]. */
export function crossedFreeze(freezes: readonly FreezeSpan[] | undefined, prev: number, next: number): FreezeSpan | null {
  for (const f of freezes ?? []) {
    if (isInsert(f) && f.at > prev && f.at <= next) return f;
  }
  return null;
}

/** Время слоёв на стоп-кадре посреди ролика — как app/freeze.motion_time и motionTime компоновщика. */
export function motionTime(
  freezes: readonly { at: number; d: number; insert: boolean; hold: boolean }[] | undefined,
  u: number,
): number {
  if (!freezes?.length) return u;
  let offAll = 0;
  let offCont = 0;
  for (const f of freezes) {
    const atPost = f.at + offAll;
    if (u < atPost) break;
    if (u < atPost + f.d) {
      if (f.hold) return f.at + offCont;
      if (f.insert) return f.at + offCont + (u - atPost);
      return u - offAll + offCont;
    }
    if (f.insert) {
      offAll += f.d;
      if (!f.hold) offCont += f.d;
    }
  }
  return u - offAll + offCont;
}

/** Кусок основы в момент `base`: последний начавшийся (во время наплыва — следующий). */
export function partIndexAt(parts: readonly PlayPart[], base: number): number {
  let index = 0;
  for (let i = 0; i < parts.length; i++) {
    if (parts[i].start <= base + 1e-6) index = i;
    else break;
  }
  return index;
}

/** Секунда исходника куска: со скоростью, у реверса — от конца к началу. */
export function srcOf(part: PlayPart, base: number): number {
  const local = Math.max(0, base - part.start) * (part.speed > 0 ? part.speed : 1);
  const t = part.reverse ? part.src_end - local : part.src_start + local;
  return Math.min(part.src_end, Math.max(part.src_start, t));
}

/** Секунда основы по секунде исходника внутри куска (обратная к srcOf без реверса). */
export function baseOfSrc(part: PlayPart, src: number): number {
  return part.start + (src - part.src_start) / (part.speed > 0 ? part.speed : 1);
}

/**
 * Просмотр с кусков: длинная часть — несколько файлов, для плеера каждый как своя часть со стыком встык
 * (наплыв на следующую часть остаётся у последнего куска).
 */
export function expandChunks<T extends PlayPart & { chunks?: [number, number][]; path_start?: number; path_of?: number }>(parts: readonly T[]): T[] {
  const out: T[] = [];
  parts.forEach((part, pathOf) => {
    const chunks = part.chunks?.length ? part.chunks : [[part.src_start, part.src_end] as [number, number]];
    const speed = part.speed > 0 ? part.speed : 1;
    chunks.forEach(([a, b], k) => {
      const last = k === chunks.length - 1;
      out.push({
        ...part,
        index: out.length,
        start: part.start + (a - part.src_start) / speed,
        end: part.start + (b - part.src_start) / speed,
        src_start: a,
        src_end: b,
        overlap: last ? part.overlap : 0,
        jump: last ? part.jump : true,
        path_start: part.src_start,
        path_of: pathOf,
      });
    });
  });
  return out;
}

export type ValueSpan = { start: number; end: number; value: number };

const clip01 = (v: number) => Math.min(1, Math.max(0, v));

/** Множитель тишины (mute): внутри отрезка — его пол, по краям рампа 30 мс, как у ffmpeg-прохода. */
export function muteFactorAt(spans: readonly ValueSpan[] | undefined, t: number, ramp = 0.03): number {
  let factor = 1;
  for (const s of spans ?? []) {
    const inside = Math.min(clip01((t - s.start) / ramp), clip01((s.end - t) / ramp));
    factor *= 1 + (s.value - 1) * inside;
  }
  return factor;
}

/** Множитель громкости отрезков (gain_db): рампа 50 мс, как app/audio_mute.gain_volume_expr. */
export function gainFactorAt(spans: readonly ValueSpan[] | undefined, t: number, ramp = 0.05): number {
  let factor = 1;
  for (const s of spans ?? []) {
    const k = 10 ** (s.value / 20);
    const inside = Math.min(clip01((t - s.start) / ramp), clip01((s.end - t) / ramp));
    factor *= 1 + (k - 1) * inside;
  }
  return factor;
}

export type VolumeKey = { t: number; gain_db: number; ease?: string };

/** Громкость по ключам, дБ — как app/audio_edit.keys_value. */
export function keyDbAt(keys: readonly VolumeKey[] | undefined, t: number): number {
  if (!keys?.length) return 0;
  if (t < keys[0].t) return keys[0].gain_db;
  for (let i = 0; i < keys.length - 1; i++) {
    const a = keys[i];
    const b = keys[i + 1];
    if (t < b.t) {
      let u = clip01((t - a.t) / Math.max(b.t - a.t, 1e-3));
      const ease = b.ease ?? "linear";
      u = ease === "smooth" ? u * u * (3 - 2 * u) : ease === "in" ? u * u : ease === "out" ? 1 - (1 - u) ** 2 : ease === "step" ? 0 : u;
      return a.gain_db + (b.gain_db - a.gain_db) * u;
    }
  }
  return keys[keys.length - 1].gain_db;
}

export type MusicAutomation = {
  keys?: VolumeKey[];
  duck?: { start: number; end: number; gain_db: number }[];
  dips?: { at: number; before: number }[];
  ramp_in?: number;
  ramp_out?: number;
} | null;

const DUCK_RAMP_SEC = 0.05;
const DIP_FADE_SEC = 0.04;
const DIP_BACK_SEC = 0.08;

const spanFactor = (t: number, a: number, b: number, factor: number, rampIn: number, rampOut: number) => {
  const inside = Math.min(clip01((t - a) / Math.max(rampIn, 1e-3)), clip01((b - t) / Math.max(rampOut, 1e-3)));
  return 1 + (factor - 1) * inside;
};

/** Автоматизация музыки: ключи × ручной ducking × провалы перед ударом — как app/audio_edit.music_volume_expr. */
export function musicFactorAt(auto: MusicAutomation | undefined, t: number): number {
  if (!auto) return 1;
  let f = auto.keys?.length ? 10 ** (keyDbAt(auto.keys, t) / 20) : 1;
  for (const d of auto.duck ?? []) {
    f *= spanFactor(t, d.start, d.end, 10 ** (d.gain_db / 20), auto.ramp_in ?? DUCK_RAMP_SEC, auto.ramp_out ?? DUCK_RAMP_SEC);
  }
  for (const d of auto.dips ?? []) {
    f *= spanFactor(t, d.at - d.before, d.at + DIP_BACK_SEC, 0, DIP_FADE_SEC, DIP_BACK_SEC);
  }
  return f;
}

/** J/L: чей звук звучит вместо звука картинки в момент `t` (секунда исходника) или null. */
export function jlSourceAt(
  jl: readonly { at: number; shift: number; prev_end: number; next_start: number }[] | undefined,
  t: number,
): number | null {
  for (const j of jl ?? []) {
    if (j.shift > 0 && t >= j.at && t < j.at + j.shift) return j.prev_end + (t - j.at);
    if (j.shift < 0 && t >= j.at + j.shift && t < j.at) return j.next_start - (j.at - t);
  }
  return null;
}
