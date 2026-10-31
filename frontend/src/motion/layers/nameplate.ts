/**
 * Плашка канала: пилюля со значком площадки и ником («sareamura» с глифом Twitch).
 *
 * glass — тёмное стекло (как ручная плашка шортсов Муры) с обводкой акцентом, значок цветом
 * акцента, ник белым; solid — заливка акцентом, значок и ник белые (на светлом акценте —
 * тёмные). Значки — свои глифы путями (glyphs.ts, PLATFORM_ICONS), ник — общей раскладкой
 * текста: ширина та же в превью и в рендере.
 *
 * Рамка слоя — `boxWidth` (если шире пилюли), пилюля в ней по `align`: так бренд ставит
 * плашку «от левого края» (x — центр рамки), не зная заранее ширины ника.
 */
import { drawPlatformIcon, platformIcon, type PlatformIcon } from "../glyphs";
import type { Ctx2D, NameplateLayer, NameplateProps, RenderEnv } from "../types";
import { clearShadow, fillRoundRect, isLight, rgba, roundRectPath, type LayerRenderer } from "./common";
import { cachedLayout, drawTextLayout, envFont, type TextBlockOptions, type TextLayout } from "./text";

/** Цвет площадки — акцент плашки по умолчанию (app/brand_kit.PLATFORM_COLORS — тот же список). */
export const PLATFORM_COLORS: Record<string, string> = {
  twitch: "#9146ff",
  youtube: "#ff0033",
  tiktok: "#fe2c55",
  telegram: "#229ed9",
  kick: "#53fc18",
  none: "#9146ff",
};
export const NAMEPLATE_PLATFORMS = ["twitch", "youtube", "tiktok", "telegram", "kick", "none"] as const;
export const NAMEPLATE_STYLES = ["glass", "solid"] as const;
/** Высота пилюли по умолчанию, опорн. px (кадр 1080×1920). */
export const NAMEPLATE_SIZE = 74;
/** Стекло: rgba(14,10,24,0.67) — как у ручной плашки. */
const GLASS_FILL = "#0e0a18ab";
const DARK_TEXT = "#0e0a18";
const TIKTOK_CHROMA: [string, string] = ["#25f4ee", "#fe2c55"];

/** Пропорции от высоты пилюли H: поля, значок, зазор, кегль. */
const PAD = 0.42;
const ICON_H = 0.56;
const GAP = 0.26;
const FONT_K = 0.5;

type Geo = {
  h: number;
  w: number;
  /** Ширина рамки слоя (≥ w). */
  boxW: number;
  icon: PlatformIcon | null;
  iconW: number;
  text: TextLayout | null;
  textOpts: TextBlockOptions | null;
  accent: string;
};

export function nameplateAccent(p: NameplateProps): string {
  return p.accent || PLATFORM_COLORS[p.platform ?? "none"] || PLATFORM_COLORS.none;
}

function textColor(p: NameplateProps, accent: string): string {
  if ((p.style ?? "glass") === "solid" && isLight(accent)) return DARK_TEXT;
  return p.color || "#ffffff";
}

const optsCache = new WeakMap<object, TextBlockOptions>();

/** Опции раскладки ника — один объект на props (кэш раскладки текста держится по нему). */
function textOptions(p: NameplateProps, h: number, accent: string): TextBlockOptions {
  const hit = optsCache.get(p);
  const color = textColor(p, accent);
  if (hit && hit.size === h * FONT_K && hit.color === color && hit.text === (p.handle || "") && hit.font === p.font && hit.weight === (p.weight ?? 800))
    return hit;
  const opts: TextBlockOptions = {
    text: p.handle || "",
    font: p.font,
    weight: p.weight ?? 800,
    size: h * FONT_K,
    color,
    align: "left",
    maxWidth: 4000,
    lineHeight: 1,
    uppercase: false,
  };
  optsCache.set(p, opts);
  return opts;
}

function geometry(ctx: Ctx2D, layer: NameplateLayer, env: RenderEnv): Geo {
  const p = layer.props || {};
  const h = Math.max(20, p.size ?? NAMEPLATE_SIZE);
  const accent = nameplateAccent(p);
  const icon = platformIcon(p.platform);
  const iconW = icon ? icon.adv * h * ICON_H : 0;
  const handle = (p.handle || "").trim();
  const textOpts = handle ? textOptions(p, h, accent) : null;
  const text = textOpts ? cachedLayout(ctx, textOpts, envFont(env)) : null;
  const inner = iconW + (icon && text ? h * GAP : 0) + (text ? text.w : 0);
  // Только значок — круглая «таблетка» не уже своей высоты.
  const w = Math.max(h, h * PAD * 2 + inner);
  return { h, w, boxW: Math.max(w, p.boxWidth ?? 0), icon, iconW, text, textOpts, accent };
}

export const nameplateRenderer: LayerRenderer<NameplateLayer> = {
  multipart: true,
  measure(ctx, layer, _st, env) {
    const g = geometry(ctx, layer, env);
    return { w: g.boxW, h: g.h };
  },
  draw(ctx, layer, _st, env) {
    const p = layer.props || {};
    const g = geometry(ctx, layer, env);
    if (!g.icon && !g.text) return;
    const style = p.style ?? "glass";
    const align = p.align ?? "center";
    const left = align === "left" ? -g.boxW / 2 : align === "right" ? g.boxW / 2 - g.w : -g.w / 2;
    const top = -g.h / 2;
    const r = g.h / 2;

    // Пилюля отбрасывает тень слоя (мягкая тень бренда), всё поверх — уже без тени.
    fillRoundRect(ctx, left, top, g.w, g.h, r, style === "solid" ? rgba(g.accent, 0.96) : rgba(GLASS_FILL));
    clearShadow(ctx);
    if (style === "glass") {
      const lw = Math.max(2, g.h * 0.034);
      ctx.beginPath();
      roundRectPath(ctx, left + lw / 2, top + lw / 2, g.w - lw, g.h - lw, r - lw / 2);
      ctx.lineWidth = lw;
      ctx.strokeStyle = rgba(g.accent);
      ctx.stroke();
    }

    const fg = textColor(p, g.accent);
    let x = left + (g.icon && !g.text ? (g.w - g.iconW) / 2 : g.h * PAD);
    if (g.icon) {
      const iconColor = style === "solid" ? fg : p.platform === "tiktok" ? "#ffffff" : rgba(g.accent);
      const chroma = style === "glass" && p.platform === "tiktok" ? TIKTOK_CHROMA : null;
      drawPlatformIcon(ctx, g.icon, x + g.iconW / 2, 0, g.h * ICON_H, iconColor, chroma);
      x += g.iconW + (g.text ? g.h * GAP : 0);
    }
    if (g.text && g.textOpts) {
      ctx.save();
      ctx.translate(x + g.text.w / 2, 0);
      drawTextLayout(ctx, g.text, g.textOpts, env, null);
      ctx.restore();
    }
  },
};
