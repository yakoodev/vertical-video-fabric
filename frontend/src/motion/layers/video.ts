/**
 * Видео внутри слоя (запись экрана в телефоне, кусок ролика в рамке). Немое: звук
 * видео-слоя не играет ни в превью, ни в рендере.
 *
 * Движок не декодирует видео сам — берёт синхронный источник кадров из `env.getVideo`:
 * в браузере это HTMLVideoElement, в рендере — кадры, заранее нарезанные ffmpeg'ом
 * (app/motion_render.prepare_video_frames). Какая секунда файла видна — чистая функция
 * `videoFileTime`, одна и та же в превью, компоновщике и бэкенде (app/motion.video_file_time).
 */
import type { LayerState } from "../animate";
import type { VideoLayer, VideoProps, VideoSource } from "../types";
import type { LayerRenderer } from "./common";
import { drawPicture, measurePicture } from "./image";

export const VIDEO_MIN_SPEED = 0.25;
export const VIDEO_MAX_SPEED = 4;
/** Последний кадр — чуть раньше конца файла: точно в `duration` кадра уже нет. */
export const VIDEO_TAIL = 0.04;

const finite = (v: unknown, d: number) => (typeof v === "number" && Number.isFinite(v) ? v : d);

/**
 * Секунда файла в локальное время слоя `local` (0 = start): `from + local·speed`.
 * За концом файла (`duration`, 0 — неизвестна): `loop` — снова с `from`, `freeze` (по
 * умолчанию) — последний кадр, иначе null (кадра нет).
 */
export function videoFileTime(p: VideoProps, local: number, duration: number): number | null {
  const from = Math.max(0, finite(p.from, 0));
  const speed = Math.min(VIDEO_MAX_SPEED, Math.max(VIDEO_MIN_SPEED, finite(p.speed, 1)));
  const t = from + Math.max(0, local) * speed;
  if (!(duration > 0) || t < duration - VIDEO_TAIL) return t;
  const last = Math.max(0, duration - VIDEO_TAIL);
  if (p.loop) {
    const span = duration - VIDEO_TAIL - from;
    return span > 0.05 ? from + ((t - from) % span) : last;
  }
  return p.freeze === false ? null : last;
}

/** Пропорция кадра, пока видео нет: в телефоне — экран 9:19.5, иначе вертикальное 9:16. */
function naturalOf(layer: VideoLayer, src: VideoSource | null) {
  if (src && src.width > 0 && src.height > 0) return { w: src.width, h: src.height };
  return layer.props.device === "phone" ? { w: 9, h: 19.5 } : { w: 9, h: 16 };
}

export const videoRenderer: LayerRenderer<VideoLayer> = {
  multipart: true,
  measure(_ctx, layer, st: LayerState, env) {
    return measurePicture(layer.props, st.crop, naturalOf(layer, env.getVideo?.(layer) ?? null));
  },
  draw(ctx, layer, st, env) {
    const src = env.getVideo?.(layer) ?? null;
    const time = src ? videoFileTime(layer.props, st.local, src.duration) : null;
    const frame = src && time !== null ? src.frame(time) : null;
    if (!frame && layer.props.device !== "phone") return;
    drawPicture(ctx, layer.props, st.crop, naturalOf(layer, src), frame, env, true);
  },
};
