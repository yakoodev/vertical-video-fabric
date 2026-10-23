/**
 * Картинка (из «Файлов для монтажа» или стикер пака) и эмодзи.
 *
 * Картинку движок не грузит сам — берёт уже загруженную из `env.getImage`.
 * Пока её нет (превью ещё качает файл), картинка не рисуется, а эмодзи
 * рисуется символом системного шрифта; в рендере все картинки загружены заранее.
 */
import { emojiFont, emojiRef } from "../fonts";
import type { LayerState } from "../animate";
import type { EmojiLayer, ImageLayer, ImageLike, RenderEnv } from "../types";
import { clearShadow, drawImageLike, fillRoundRect, hasShadow, imageSize, rgba, roundRectPath, type LayerRenderer } from "./common";

function imageRef(layer: ImageLayer): string {
  // asset_id в JSON бывает и числом — ключ карты картинок всегда строка.
  return String(layer.props.asset_id || layer.props.src || "");
}

/**
 * Видимая часть картинки и размер слоя в опорных px. Окно crop (в долях, уже с ключами
 * и зажатое) — из состояния слоя: размер слоя зависит только от w/h окна, поэтому
 * анимация cropX/cropY двигает содержимое внутри неподвижной рамки.
 */
function geometry(layer: ImageLayer, st: LayerState, img: ImageLike | null) {
  const p = layer.props;
  const { w: iw, h: ih } = img ? imageSize(img) : { w: 1, h: 1 };
  const c = st.crop ?? { x: 0, y: 0, w: 1, h: 1 };
  const sw = iw * c.w;
  const sh = ih * c.h;
  const w = Math.max(1, p.w ?? 600);
  const h = (w * sh) / Math.max(1, sw);
  return { sx: iw * c.x, sy: ih * c.y, sw, sh, w, h };
}

export const imageRenderer: LayerRenderer<ImageLayer> = {
  multipart: true,
  measure(_ctx, layer, st, env) {
    const g = geometry(layer, st, env.getImage(imageRef(layer)));
    const b = layer.props.border?.width ?? 0;
    return { w: g.w + b * 2, h: g.h + b * 2 };
  },
  draw(ctx, layer, st, env) {
    const img = env.getImage(imageRef(layer));
    if (!img) return;
    const p = layer.props;
    const g = geometry(layer, st, img);
    const r = Math.max(0, Math.min(p.radius ?? 0, g.w / 2, g.h / 2));
    ctx.save();
    if (p.flipX) ctx.scale(-1, 1);
    if (r > 0) {
      // Скруглённую картинку обрезает маска — тень от неё даёт подложка той же формы.
      if (hasShadow(ctx)) fillRoundRect(ctx, -g.w / 2, -g.h / 2, g.w, g.h, r, "#000000");
      clearShadow(ctx);
      ctx.beginPath();
      roundRectPath(ctx, -g.w / 2, -g.h / 2, g.w, g.h, r);
      ctx.clip();
    }
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    drawImageLike(ctx, img, g.sx, g.sy, g.sw, g.sh, -g.w / 2, -g.h / 2, g.w, g.h, env);
    ctx.restore();
    clearShadow(ctx);
    const border = p.border;
    if (border && border.width > 0) {
      // Рамка снаружи края картинки, чтобы не съедать её пиксели.
      const bw = border.width;
      ctx.beginPath();
      roundRectPath(ctx, -g.w / 2 - bw / 2, -g.h / 2 - bw / 2, g.w + bw, g.h + bw, r > 0 ? r + bw / 2 : 0);
      ctx.lineWidth = bw;
      ctx.lineJoin = "round";
      ctx.strokeStyle = rgba(border.color);
      ctx.stroke();
    }
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
