/**
 * Реакция слоя на звук (`react`): огибающая громкости клипа → масштаб, свечение, прозрачность,
 * подскок или тряска слоя. `band` — полоса частот (bass/mid/high): огибающая `источник:полоса`.
 *
 * Огибающую считает сервер один раз на клип (app/audio_react.py) и отдаёт массивом по кадрам
 * (`comp.levels`), в превью — тем же массивом (POST /api/clip-plans/{id}/levels). Движок только
 * читает его, а сглаживание `smooth` считает окном по массиву назад от момента — без состояния
 * между кадрами: перемотка в любую точку честная, превью == рендер кадр в кадр.
 */
import { clamp, clamp01 } from "./easing";
import type { LayerReact, LevelTrack, ReactBand, ReactProp, ReactSource } from "./types";

export const REACT_SOURCES: ReactSource[] = ["voice", "music", "mix"];
export const REACT_PROPS: ReactProp[] = ["scale", "glow", "opacity", "y", "shake"];
export const REACT_BANDS: ReactBand[] = ["full", "bass", "mid", "high"];

/** Ключ огибающей в `comp.levels`: `voice` (вся громкость) или `music:bass` — как app/motion.react_level_key. */
export function reactLevelKey(source: ReactSource | string | undefined, band?: ReactBand | string | null): string {
  const src = REACT_SOURCES.includes(source as ReactSource) ? (source as ReactSource) : "voice";
  return !band || band === "full" || !REACT_BANDS.includes(band as ReactBand) ? src : `${src}:${band}`;
}
/** Окно сглаживания при smooth = 1, с. */
export const REACT_MAX_WINDOW = 0.5;

/** Огибающие клипа по источнику (voice / music / mix) и звук слоёв волны (`wave:<id слоя>`). */
export type Levels = { [source: string]: LevelTrack | undefined };

/** Ключ огибающей слоя волны со своим звуком (app/audio_react.py, `wave_tracks`). */
export const waveLevelKey = (layerId: string) => `wave:${layerId}`;

/** Значение кадра k огибающей 0..1; за краем массива — тишина. */
function sample(track: LevelTrack, k: number): number {
  const v = track.v[k];
  return typeof v === "number" && Number.isFinite(v) ? clamp01(v / 100) : 0;
}

/**
 * Громкость в момент `t` (клиповое время) 0..1. `smooth` 0..1 — экспоненциальное окно назад
 * (постоянная времени до 0.5 с, длина окна — 3 постоянных): кадр берёт взвешенное среднее
 * прошлых кадров, а не «память» прошлого вызова.
 */
export function levelAt(track: LevelTrack | null | undefined, t: number, smooth = 0): number {
  if (!track || !track.v?.length) return 0;
  const fps = track.fps > 0 ? track.fps : 30;
  const k = Math.floor((t - (track.t0 || 0)) * fps + 1e-6);
  const tau = clamp(smooth, 0, 1) * REACT_MAX_WINDOW * fps;
  if (tau < 0.5) return sample(track, k);
  const n = Math.ceil(tau * 3);
  let sum = 0;
  let wsum = 0;
  for (let i = 0; i <= n; i++) {
    const w = Math.exp(-i / tau);
    sum += w * sample(track, k - i);
    wsum += w;
  }
  return sum / wsum;
}

/** Что реакция делает со слоем в момент `t`: множители и сдвиги (применяет animate.ts). */
export type ReactEffect = { prop: ReactProp; value: number; amount: number };

/** Реакция слоя в момент `t`; null — реакции нет или огибающей источника нет. */
export function reactAt(react: LayerReact | null | undefined, levels: Levels | null | undefined, t: number): ReactEffect | null {
  if (!react || !levels) return null;
  const track = levels[reactLevelKey(react.source, react.band)];
  if (!track) return null;
  const prop = REACT_PROPS.includes(react.prop as ReactProp) ? (react.prop as ReactProp) : "scale";
  const amount = clamp(typeof react.amount === "number" && Number.isFinite(react.amount) ? react.amount : 1, 0, 3);
  const smooth = typeof react.smooth === "number" && Number.isFinite(react.smooth) ? react.smooth : 0.15;
  return { prop, amount, value: levelAt(track, t, smooth) };
}
