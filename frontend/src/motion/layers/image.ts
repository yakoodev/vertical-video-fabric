/**
 * Картинка (из «Файлов для монтажа» или стикер пака) и эмодзи.
 *
 * Картинку движок не грузит сам — берёт уже загруженную из `env.getImage`.
 * Пока её нет (превью ещё качает файл), картинка не рисуется, а эмодзи
 * рисуется символом системного шрифта; в рендере все картинки загружены заранее.
 */
import { emojiFont, emojiRef } from "../fonts";
import type { LayerState } from "../animate";
import type { Crop, EmojiLayer, ImageLayer, ImageLike, PictureProps, RenderEnv } from "../types";
import { drawPhone, phoneMetrics } from "./device";
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
  return { w, h: (w * sh) / Math.max(1e-6, sw) };
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

/** Что рисовать: картинка и её собственный размер в пикселях (окно crop берётся от него). */
export type PictureSource = { image: ImageLike; width: number; height: number };

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
    drawImageLike(ctx, src.image, src.width * c.x, src.height * c.y, src.width * c.w, src.height * c.h, x, y, w, h, env);
    ctx.restore();
  };
  if (p.device === "phone") {
    drawPhone(ctx, s.w, s.h, { kind: "phone", radius: p.radius, color: p.deviceColor, island: p.island }, blit);
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

export const imageRenderer: LayerRenderer<ImageLayer> = {
  multipart: true,
  measure(_ctx, layer, st: LayerState, env) {
    return measurePicture(layer.props, st.crop, naturalOf(env.getImage(imageRef(layer)), layer.props));
  },
  draw(ctx, layer, st, env) {
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
