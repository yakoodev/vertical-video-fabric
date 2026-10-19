/**
 * Плашка «нижняя треть»: заголовок + подзаголовок в одном из четырёх стилей.
 *
 * bar  — тёмная плашка с цветной полосой слева (классика новостей);
 * box  — заголовок на акцентной плашке, подзаголовок на белой под ней со сдвигом;
 * pill — светлая «таблетка» с цветным кружком-значком;
 * neon — тёмное стекло с неоновой обводкой и свечением.
 * `skew` наклоняет всю плашку вместе с текстом — «спортивный» курсив.
 */
import { emojiRef } from "../fonts";
import type { CalloutLayer, Ctx2D, RenderEnv } from "../types";
import { clearShadow, drawImageLike, fillRoundRect, isLight, rgba, roundRectPath, shade, type LayerRenderer } from "./common";
import { deviceScale } from "./counter";
import { drawEmoji } from "./image";
import { drawTextLayout, envFont, layoutText, type TextBlockOptions, type TextLayout } from "./text";

type Geo = {
  w: number;
  h: number;
  title: TextLayout;
  titleOpts: TextBlockOptions;
  sub: TextLayout | null;
  subOpts: TextBlockOptions | null;
  icon: number;
  padX: number;
  padY: number;
  gap: number;
  bar: number;
};

const TITLE_SIZE = 70;
const SUB_SIZE = 40;

function geometry(ctx: Ctx2D, layer: CalloutLayer, env: RenderEnv): Geo {
  const p = layer.props;
  const style = p.style ?? "bar";
  const maxW = Math.max(200, p.w ?? 760);
  const accent = p.accent || "#8B5CF6";
  const icon = p.icon ? TITLE_SIZE * 1.25 : 0;
  const padX = style === "pill" ? 40 : 36;
  const padY = style === "box" ? 18 : 24;
  const bar = style === "bar" ? 16 : 0;
  const textMax = maxW - padX * 2 - bar - (icon ? icon + 22 : 0);
  const font = envFont(env);

  const titleColor = style === "pill" ? "#14121f" : style === "box" ? (isLight(accent) ? "#14121f" : "#ffffff") : "#ffffff";
  const titleOpts: TextBlockOptions = {
    text: p.title || "",
    font: p.font,
    weight: 900,
    size: TITLE_SIZE,
    color: titleColor,
    align: "left",
    maxWidth: textMax,
    lineHeight: 1.05,
    uppercase: true,
  };
  const title = layoutText(ctx, titleOpts, font);
  let subOpts: TextBlockOptions | null = null;
  let sub: TextLayout | null = null;
  if (p.subtitle) {
    const subColor = style === "pill" ? "#4b4760" : style === "box" ? "#14121f" : style === "neon" ? shade(accent, 0.45) : "#ffffffbf";
    subOpts = { text: p.subtitle, font: p.font, weight: 600, size: SUB_SIZE, color: subColor, align: "left", maxWidth: textMax, lineHeight: 1.1 };
    sub = layoutText(ctx, subOpts, font);
  }
  const gap = sub ? (style === "box" ? padY * 2 + 6 : 12) : 0;
  const textW = Math.max(title.w, sub ? sub.w : 0);
  const w = padX * 2 + bar + (icon ? icon + 22 : 0) + textW;
  const h = padY * 2 + title.h + (sub ? gap + sub.h : 0);
  return { w, h, title, titleOpts, sub, subOpts, icon, padX, padY, gap, bar };
}

function skewOf(layer: CalloutLayer): number {
  const s = Math.max(-30, Math.min(30, layer.props.skew ?? 0));
  return Math.tan((s * Math.PI) / 180);
}

export const calloutRenderer: LayerRenderer<CalloutLayer> = {
  multipart: true,
  measure(ctx, layer, _st, env) {
    const g = geometry(ctx, layer, env);
    return { w: g.w + Math.abs(skewOf(layer)) * g.h + 40, h: g.h + 40 };
  },
  draw(ctx, layer, _st, env) {
    const p = layer.props;
    if (!p.title && !p.subtitle) return;
    const g = geometry(ctx, layer, env);
    const style = p.style ?? "bar";
    const accent = p.accent || "#8B5CF6";
    const left = -g.w / 2;
    const top = -g.h / 2;
    const k = skewOf(layer);
    if (k) ctx.transform(1, 0, -k, 1, 0, 0);

    const titleTop = top + g.padY;
    const subTop = titleTop + g.title.h + g.gap;
    const textLeft = left + g.padX + g.bar + (g.icon ? g.icon + 22 : 0);

    if (style === "bar") {
      fillRoundRect(ctx, left, top, g.w, g.h, 14, "rgba(14,12,26,0.9)");
      const grad = ctx.createLinearGradient(0, top, 0, top + g.h);
      grad.addColorStop(0, shade(accent, 0.2));
      grad.addColorStop(1, shade(accent, -0.25));
      fillRoundRect(ctx, left, top, g.bar + 6, g.h, 6, grad);
    } else if (style === "box") {
      const titleH = g.padY * 2 + g.title.h;
      const grad = ctx.createLinearGradient(left, 0, left + g.w, 0);
      grad.addColorStop(0, shade(accent, -0.1));
      grad.addColorStop(1, shade(accent, 0.15));
      if (g.sub) {
        // Подзаголовок на белой плашке ниже и чуть правее — «лесенка» как в эфирной графике.
        const sw = g.padX * 2 + g.sub.w + (g.icon ? g.icon + 22 : 0);
        fillRoundRect(ctx, left + 26, top + titleH - 4, Math.min(g.w, sw + 26), g.h - titleH + 4, 8, "#ffffff");
      }
      fillRoundRect(ctx, left, top, g.w, titleH, 10, grad);
    } else if (style === "pill") {
      fillRoundRect(ctx, left, top, g.w, g.h, g.h / 2, "rgba(255,255,255,0.97)");
    } else {
      // neon: тёмное стекло + светящаяся обводка в два прохода (широкое свечение и чёткая линия).
      fillRoundRect(ctx, left, top, g.w, g.h, 22, "rgba(10,8,24,0.78)");
      ctx.save();
      ctx.shadowColor = rgba(accent, 0.95);
      ctx.shadowBlur = 26 * deviceScale(ctx);
      ctx.beginPath();
      roundRectPath(ctx, left, top, g.w, g.h, 22);
      ctx.lineWidth = 5;
      ctx.strokeStyle = rgba(shade(accent, 0.25));
      ctx.stroke();
      ctx.stroke();
      ctx.restore();
    }

    clearShadow(ctx);
    if (g.icon) {
      const cx = left + g.padX + g.bar + g.icon / 2;
      const cy = titleTop + g.title.h / 2;
      if (style === "pill" || style === "neon") {
        ctx.beginPath();
        ctx.arc(cx, cy, g.icon / 2 + 6, 0, Math.PI * 2);
        ctx.fillStyle = rgba(accent);
        ctx.fill();
      }
      ctx.save();
      ctx.translate(cx, cy);
      if (k) ctx.transform(1, 0, k, 1, 0, 0); // значок не скашиваем — эмодзи кривым выглядит дёшево
      const img = env.getImage(emojiRef(p.icon as string));
      if (img) drawImageLike(ctx, img, 0, 0, img.width, img.height, -g.icon * 0.38, -g.icon * 0.38, g.icon * 0.76, g.icon * 0.76, env);
      else drawEmoji(ctx, p.icon as string, g.icon * 0.76, env);
      ctx.restore();
    }

    if (style === "neon") {
      ctx.save();
      ctx.shadowColor = rgba(accent, 0.9);
      ctx.shadowBlur = 18 * deviceScale(ctx);
    }
    ctx.save();
    ctx.translate(textLeft + g.title.w / 2, titleTop + g.title.h / 2);
    drawTextLayout(ctx, g.title, g.titleOpts, env, null);
    ctx.restore();
    if (style === "neon") ctx.restore();
    if (g.sub && g.subOpts) {
      ctx.save();
      ctx.translate(textLeft + g.sub.w / 2 + (style === "box" ? 26 : 0), subTop + g.sub.h / 2 + (style === "box" ? -2 : 0));
      drawTextLayout(ctx, g.sub, g.subOpts, env, null);
      ctx.restore();
    }
  },
};
