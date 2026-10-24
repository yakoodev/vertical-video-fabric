/**
 * Время анимаций и голоса — чистые функции, одни и те же в превью и компоновщике.
 *
 * Кадр анимации (GIF/APNG/webm-alpha) — по времени по кругу: у каждого кадра своя длина
 * (`times` — начала кадров, `duration` — длина цикла). Состояние PNGtuber — из массива по
 * кадрам, который посчитал сервер (app/pngtuber.py); движок ничего не слушает сам.
 */
import type { VoiceTrack } from "./types";

/** Номер кадра анимации в момент `t` (по кругу). Пустая анимация — 0. */
export function animFrameIndex(times: number[], duration: number, t: number): number {
  const n = times.length;
  if (n <= 1 || !(duration > 0)) return 0;
  let u = t % duration;
  if (u < 0) u += duration;
  // Двоичный поиск последнего начала ≤ u (+ε: момент ровно на границе — уже следующий кадр).
  let lo = 0;
  let hi = n - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (times[mid] <= u + 1e-6) lo = mid;
    else hi = mid - 1;
  }
  return lo;
}

/** Номер кадра голоса в момент `t` или −1 (вне массива). Как app/pngtuber.state_at. */
function voiceIndex(track: VoiceTrack, t: number): number {
  const fps = Number(track.fps) > 0 ? Number(track.fps) : 30;
  const k = Math.floor((t - (Number(track.t0) || 0)) * fps + 1e-6);
  return k >= 0 && k < (track.states?.length ?? 0) ? k : -1;
}

export type VoiceState = 0 | 1 | 2;

/** Состояние PNGtuber в момент `t`: 0 молчит, 1 говорит, 2 кричит; данных нет — молчит. */
export function voiceStateAt(track: VoiceTrack | null | undefined, t: number): VoiceState {
  if (!track || !track.states) return 0;
  const k = voiceIndex(track, t);
  if (k < 0) return 0;
  const c = track.states.charCodeAt(k) - 48;
  return c === 2 ? 2 : c === 1 ? 1 : 0;
}

/** Огибающая голоса 0..1 в момент `t` (подскок аватара). */
export function voiceLevelAt(track: VoiceTrack | null | undefined, t: number): number {
  if (!track || !track.levels?.length) return 0;
  const k = voiceIndex(track, t);
  const v = k >= 0 ? Number(track.levels[k]) : 0;
  return Number.isFinite(v) ? Math.max(0, Math.min(1, v / 100)) : 0;
}
