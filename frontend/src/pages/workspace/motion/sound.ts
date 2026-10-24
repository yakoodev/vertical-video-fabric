/**
 * Громкость звуков слоёв в превью — по тем же правилам, что микс рендера
 * (app/motion.py: track_gain, app/motion_render.py: build_audio_layers_args).
 *
 * Эффект (audio без mode) рендер ставит под голос клипа сам — в превью только volume ≤ 1.
 * Дорожка (audio mode "track", звук video с `audio: true`) — volume × gain_db; браузер громче
 * 1 не умеет, поэтому в превью потолок 1 (итог всё равно выравнивает нормализация клипа).
 */
import type { Layer } from "@/motion";

/** Звук video-слоя: при скорости вне этих рамок рендер звук не играет (как VIDEO_AUDIO_*_SPEED). */
export const VIDEO_AUDIO_MIN_SPEED = 0.5;
export const VIDEO_AUDIO_MAX_SPEED = 2;
const TRACK_MAX_GAIN_DB = 12;

/** Множитель дорожки: volume (0..2) × 10^(gain_db/20), не громче +12 дБ. */
export function trackGain(volume: number | undefined, gainDb: number | undefined): number {
  const v = Math.min(2, Math.max(0, Number.isFinite(volume) ? Number(volume) : 1));
  const g = Math.min(TRACK_MAX_GAIN_DB, Math.max(-30, Number(gainDb) || 0));
  return Math.min(v * 10 ** (g / 20), 10 ** (TRACK_MAX_GAIN_DB / 20));
}

/** Громкость HTMLMediaElement (0..1) для звука слоя. */
export function previewVolume(layer: Layer): number {
  if (layer.type === "video") return Math.min(1, trackGain(layer.props.volume, layer.props.gain_db));
  if (layer.type === "audio") {
    if (layer.props.mode === "track") return Math.min(1, trackGain(layer.props.volume, layer.props.gain_db));
    return Math.min(1, Math.max(0, layer.props.volume ?? 1));
  }
  return 0;
}

/** Звучит ли video-слой в рендере (и значит в превью): audio true, не стоп-кадр, скорость 0.5..2. */
export function videoLayerAudible(layer: Layer): boolean {
  if (layer.type !== "video" || layer.props.audio !== true || layer.props.hold) return false;
  const speed = Number(layer.props.speed) || 1;
  return speed >= VIDEO_AUDIO_MIN_SPEED - 1e-6 && speed <= VIDEO_AUDIO_MAX_SPEED + 1e-6;
}

let soundOn: () => boolean = () => false;

/**
 * Включён ли звук превью вообще (основное видео редактора не заглушено владельцем).
 * Регистрирует редактор; пока не зарегистрирован — видео-слои немые.
 */
export function setPreviewSound(fn: () => boolean): void {
  soundOn = fn;
}

export function previewSoundOn(): boolean {
  try {
    return soundOn();
  } catch {
    return false;
  }
}
