/**
 * PNGtuber: аватар, который «говорит», когда звучит голос.
 *
 * Состояние (молчит / говорит / кричит) движок берёт из `comp.voice[id]` — его по кадрам
 * посчитал сервер (app/pngtuber.py) по огибающей дорожки голоса; данных нет — молчит.
 * Каждое состояние — картинка или анимация с альфой (`env.getAnim`), анимация циклится по
 * времени КЛИПА: смена состояния не сбивает её фазу. Пиксель-арт (исходник ≤ 256 px)
 * масштабируется без сглаживания — пиксели остаются квадратными.
 */
import { animFrameIndex, voiceLevelAt, voiceStateAt } from "../anim";
import type { AnimSource, ImageLike, PngtuberLayer, PngtuberProps, RenderEnv } from "../types";
import { drawImageLike, imageSize, type LayerRenderer } from "./common";

/** Исходник не больше — пиксель-арт: масштаб без сглаживания (как app/anim_frames.PIXEL_ART_MAX). */
export const PIXEL_ART_MAX = 256;
export const PNGTUBER_DEFAULT_W = 300;
/** Подскок: растяжение по высоте и сжатие по ширине на полной огибающей. */
const BOUNCE_STRETCH = 0.06;
const BOUNCE_SQUEEZE = 0.04;

const STATE_KEYS = ["idle", "talk", "shout"] as const;

/** Ссылка на файл состояния; нет своего — ближайшее «тише» (shout → talk → idle). */
export function pngtuberStateRef(p: PngtuberProps, state: 0 | 1 | 2): string {
  const s = p.states || {};
  for (let k = state; k >= 0; k--) {
    const ref = s[STATE_KEYS[k]];
    if (ref) return String(ref);
  }
  return String(s.talk || s.shout || "");
}

type Picture = { image: ImageLike; sx: number; sy: number; sw: number; sh: number; natural: { w: number; h: number } };

/** Что рисовать для ссылки в момент клипа `t`: кадр анимации или статичная картинка. */
function pictureOf(env: RenderEnv, ref: string, t: number): Picture | null {
  if (!ref) return null;
  const anim: AnimSource | null = env.getAnim?.(ref) ?? null;
  if (anim && anim.times.length) {
    const f = anim.frame(animFrameIndex(anim.times, anim.duration, t));
    if (f) return { ...f, natural: { w: anim.width || f.sw, h: anim.height || f.sh } };
  }
  const img = env.getImage(ref);
  if (!img) return null;
  const n = imageSize(img);
  return { image: img, sx: 0, sy: 0, sw: n.w, sh: n.h, natural: n };
}

/** Пропорция аватара — по idle (рамка не прыгает при смене состояния); нет — квадрат. */
function naturalOf(env: RenderEnv, p: PngtuberProps): { w: number; h: number } {
  for (const key of STATE_KEYS) {
    const ref = p.states?.[key];
    if (!ref) continue;
    const anim = env.getAnim?.(String(ref));
    if (anim && anim.width > 0 && anim.height > 0) return { w: anim.width, h: anim.height };
    const img = env.getImage(String(ref));
    if (img) return imageSize(img);
  }
  return { w: 1, h: 1 };
}

export function pngtuberSize(env: RenderEnv, p: PngtuberProps): { w: number; h: number } {
  const n = naturalOf(env, p);
  const w = Math.max(1, Number(p.w) || PNGTUBER_DEFAULT_W);
  return { w, h: (w * n.h) / Math.max(1e-6, n.w) };
}

export const pngtuberRenderer: LayerRenderer<PngtuberLayer> = {
  measure(_ctx, layer, _st, env) {
    return pngtuberSize(env, layer.props || { states: {} });
  },
  draw(ctx, layer, _st, env, frame) {
    const p = layer.props || { states: {} };
    const track = frame.voice?.[layer.id];
    const state = voiceStateAt(track, frame.t);
    const pic = pictureOf(env, pngtuberStateRef(p, state), frame.t);
    if (!pic) return;
    const { w, h } = pngtuberSize(env, p);
    ctx.save();
    const amount = Math.max(0, Math.min(40, Number(p.bounce?.amount) || 0));
    if (p.bounce && amount > 0 && state > 0) {
      // Подскок в такт огибающей: вверх на amount·level, тянется вверх от «ног».
      const level = voiceLevelAt(track, frame.t);
      ctx.translate(0, -amount * level + h / 2);
      ctx.scale(1 - BOUNCE_SQUEEZE * level, 1 + BOUNCE_STRETCH * level);
      ctx.translate(0, -h / 2);
    }
    if (p.flipX) ctx.scale(-1, 1);
    const pixel = p.pixelated ?? Math.max(pic.natural.w, pic.natural.h) <= PIXEL_ART_MAX;
    ctx.imageSmoothingEnabled = !pixel;
    if (!pixel) ctx.imageSmoothingQuality = "high";
    drawImageLike(ctx, pic.image, pic.sx, pic.sy, pic.sw, pic.sh, -w / 2, -h / 2, w, h, env);
    ctx.restore();
  },
};
