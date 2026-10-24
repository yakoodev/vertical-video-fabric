/**
 * Картинка (из «Файлов для монтажа» или стикер пака) и эмодзи.
 *
 * Картинку движок не грузит сам — берёт уже загруженную из `env.getImage`.
 * Пока её нет (превью ещё качает файл), картинка не рисуется, а эмодзи
 * рисуется символом системного шрифта; в рендере все картинки загружены заранее.
 */
import { animFrameIndex } from "../anim";
import { emojiFont, emojiRef } from "../fonts";
import type { LayerState } from "../animate";
import type { Crop, EmojiLayer, ImageLayer, ImageLike, PictureProps, RenderEnv } from "../types";
import { drawPhone, phoneMetrics, statusBarHeight } from "./device";
import { clearShadow, drawImageLike, fillRoundRect, hasShadow, imageSize, rgba, roundRectPath, type LayerRenderer } from "./common";

function imageRef(layer: ImageLayer): string {
  // asset_id в JSON бывает и числом — ключ карты картинок всегда строка.
  return String(layer.props.asset_id || layer.props.src || "");
}

type Size = { w: number; h: number };
const FULL: Crop = { x: 0, y: 0, w: 1, h: 1 };

/**
 * Экран слоя (видимая часть содержимого) в опорных px. Окно crop (в долях, уже с ключами
 * и зажатое) — из состояния слоя: размер зависит только от w/h окна, поэтому анимация
 * cropX/cropY двигает содержимое внутри неподвижной рамки. `natural` — размер содержимого
 * (картинки или кадра видео): важна только пропорция.
 */
export function screenSize(p: PictureProps, crop: Crop | null, natural: Size): Size {
  const c = crop ?? FULL;
  const sw = natural.w * c.w;
  const sh = natural.h * c.h;
  const w = Math.max(1, p.w ?? 600);
  // Строка статуса телефона — полоса над содержимым: экран выше на неё, содержимое не сжато.
  return { w, h: (w * sh) / Math.max(1e-6, sw) + statusBarHeight(p, w) };
}

/** Размер слоя-картинки/видео: экран + рамка, или корпус телефона с кнопками. */
export function measurePicture(p: PictureProps, crop: Crop | null, natural: Size): Size {
  const s = screenSize(p, crop, natural);
  if (p.device === "phone") {
    const m = phoneMetrics(s.w, s.h, p.radius);
    return { w: m.outerW, h: m.outerH };
  }
  const b = p.border?.width ?? 0;
  return { w: s.w + b * 2, h: s.h + b * 2 };
}

/**
 * Что рисовать: картинка и её собственный размер в пикселях (окно crop берётся от него).
 * `sx`/`sy` — где кадр лежит в картинке (кадр анимации на листе превью), по умолчанию 0.
 */
export type PictureSource = { image: ImageLike; width: number; height: number; sx?: number; sy?: number };

/**
 * Нарисовать картинку/кадр видео: экран с маской скругления и рамкой — или в корпусе
 * телефона. `opaque` — содержимое непрозрачное (видео): тень даёт подложка формы экрана,
 * а не силуэт по альфе. `src` null — содержимого (ещё) нет: телефон с тёмным экраном.
 */
export function drawPicture(
  ctx: CanvasRenderingContext2D,
  p: PictureProps,
  crop: Crop | null,
  natural: Size,
  src: PictureSource | null,
  env: RenderEnv,
  opaque = false,
): void {
  const s = screenSize(p, crop, natural);
  const c = crop ?? FULL;
  const blit = (x: number, y: number, w: number, h: number) => {
    if (!src) return;
    ctx.save();
    if (p.flipX) ctx.scale(-1, 1);
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    const ox = src.sx ?? 0;
    const oy = src.sy ?? 0;
    drawImageLike(ctx, src.image, ox + src.width * c.x, oy + src.height * c.y, src.width * c.w, src.height * c.h, x, y, w, h, env);
    ctx.restore();
  };
  if (p.device === "phone") {
    // Островок по умолчанию: у картинки (скриншот со строкой статуса) — да; у видео (opaque) —
    // только со statusBar: запись экрана без строки статуса, островок лёг бы на шапку приложения.
    const sb = statusBarHeight(p, s.w);
    const island = p.island ?? (sb > 0 || !opaque);
    const opts = { kind: "phone" as const, radius: p.radius, color: p.deviceColor, island, statusBar: sb, statusBarColor: p.statusBarColor, glare: p.screenGlare };
    drawPhone(ctx, s.w, s.h, opts, blit, env.defaultFont);
    return;
  }
  if (!src) return;
  const r = Math.max(0, Math.min(p.radius ?? 0, s.w / 2, s.h / 2));
  ctx.save();
  if (r > 0 || opaque) {
    // Скруглённую (или непрозрачную) картинку обрезает маска — тень даёт подложка той же формы.
    if (hasShadow(ctx)) fillRoundRect(ctx, -s.w / 2, -s.h / 2, s.w, s.h, r, "#000000");
    clearShadow(ctx);
    if (r > 0) {
      ctx.beginPath();
      roundRectPath(ctx, -s.w / 2, -s.h / 2, s.w, s.h, r);
      ctx.clip();
    }
  }
  blit(-s.w / 2, -s.h / 2, s.w, s.h);
  ctx.restore();
  clearShadow(ctx);
  const border = p.border;
  if (border && border.width > 0) {
    // Рамка снаружи края картинки, чтобы не съедать её пиксели.
    const bw = border.width;
    ctx.beginPath();
    roundRectPath(ctx, -s.w / 2 - bw / 2, -s.h / 2 - bw / 2, s.w + bw, s.h + bw, r > 0 ? r + bw / 2 : 0);
    ctx.lineWidth = bw;
    ctx.lineJoin = "round";
    ctx.strokeStyle = rgba(border.color);
    ctx.stroke();
  }
}

/** Пропорция картинки, пока её нет: телефон — экран 9:19.5, иначе квадрат. */
function naturalOf(img: ImageLike | null, p: PictureProps): Size {
  if (img) return imageSize(img);
  return p.device === "phone" ? { w: 9, h: 19.5 } : { w: 1, h: 1 };
}

/**
 * Анимированная картинка (GIF/APNG/webm-alpha из библиотеки): кадр по локальному времени слоя —
 * гифка начинается с первого кадра, когда слой появился. Нет анимации — null (статичная).
 */
function animPicture(layer: ImageLayer, local: number, env: RenderEnv): PictureSource | null {
  if (!layer.props.asset_id || !env.getAnim) return null;
  const anim = env.getAnim(imageRef(layer));
  if (!anim || anim.times.length < 2) return null;
  const f = anim.frame(animFrameIndex(anim.times, anim.duration, local));
  return f ? { image: f.image, width: f.sw, height: f.sh, sx: f.sx, sy: f.sy } : null;
}

function animNatural(layer: ImageLayer, env: RenderEnv): Size | null {
  const anim = layer.props.asset_id ? env.getAnim?.(imageRef(layer)) : null;
  return anim && anim.times.length > 1 && anim.width > 0 && anim.height > 0 ? { w: anim.width, h: anim.height } : null;
}

export const imageRenderer: LayerRenderer<ImageLayer> = {
  multipart: true,
  measure(_ctx, layer, st: LayerState, env) {
    const natural = animNatural(layer, env) ?? naturalOf(env.getImage(imageRef(layer)), layer.props);
    return measurePicture(layer.props, st.crop, natural);
  },
  draw(ctx, layer, st, env) {
    const frame = animPicture(layer, st.local, env);
    if (frame) {
      // Пропорция — исходника (кадр листа превью бывает уменьшен), окно crop — от самого кадра.
      drawPicture(ctx, layer.props, st.crop, animNatural(layer, env) ?? { w: frame.width, h: frame.height }, frame, env);
      return;
    }
    const img = env.getImage(imageRef(layer));
    if (!img && layer.props.device !== "phone") return;
    const natural = naturalOf(img, layer.props);
    drawPicture(ctx, layer.props, st.crop, natural, img ? { image: img, width: natural.w, height: natural.h } : null, env);
  },
};

/** Эмодзи-слой: картинка Twemoji, при её отсутствии — символ системным шрифтом. */
export function drawEmoji(ctx: CanvasRenderingContext2D, char: string, size: number, env: RenderEnv): void {
  const img = env.getImage(emojiRef(char));
  if (img) {
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    drawImageLike(ctx, img, 0, 0, img.width, img.height, -size / 2, -size / 2, size, size, env);
    return;
  }
  ctx.font = emojiFont(size * 0.86);
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillStyle = "#ffffff";
  ctx.fillText(char, 0, size * 0.04);
}

export const emojiRenderer: LayerRenderer<EmojiLayer> = {
  measure(_ctx, layer) {
    const s = Math.max(8, layer.props.size ?? 160);
    return { w: s, h: s };
  },
  draw(ctx, layer, _st, env) {
    if (!layer.props.char) return;
    drawEmoji(ctx, layer.props.char, Math.max(8, layer.props.size ?? 160), env);
  },
};
