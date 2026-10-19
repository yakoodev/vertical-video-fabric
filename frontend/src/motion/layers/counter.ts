/**
 * Счётчик «0 → 1 000 000» и полоса прогресса.
 *
 * Цифры счётчика стоят в ячейках одной ширины, а блок держит ширину самого
 * длинного значения: иначе центрированное число «дрожит» каждый кадр, потому
 * что «1» уже «8».
 */
import { clamp01 } from "../easing";
import { fontString } from "../fonts";
import type { CounterLayer, CounterProps, Ctx2D, ProgressLayer } from "../types";
import { clearShadow, fillRoundRect, rgba, roundRectPath, shade, type LayerRenderer } from "./common";
import { drawTextLayout, envFont, layoutText, textBoxSize, type TextBlockOptions } from "./text";

/** Число по правилам слоя: знаки после запятой, разделитель тысяч, префикс/суффикс. */
export function formatCounter(value: number, p: CounterProps): string {
  const decimals = Math.max(0, Math.min(3, Math.round(p.decimals ?? 0)));
  const sep = p.thousands ?? " ";
  const fixed = Math.abs(value).toFixed(decimals);
  const [int, frac] = fixed.split(".");
  const grouped = sep ? int.replace(/\B(?=(\d{3})+(?!\d))/g, sep) : int;
  // Русская десятичная запятая, если тысячи не разделяются запятой.
  const dec = sep === "," ? "." : ",";
  const sign = value < 0 && Number(fixed) !== 0 ? "−" : "";
  return `${p.prefix ?? ""}${sign}${grouped}${frac ? dec + frac : ""}${p.suffix ?? ""}`;
}

export function counterValue(p: CounterProps, progress: number): number {
  const from = p.from ?? 0;
  const to = p.to ?? 100;
  return from + (to - from) * clamp01(progress);
}

/** Ширина самого длинного из крайних значений — кэш по объекту props. */
const widthCache = new WeakMap<object, number>();

function counterOptions(ctx: Ctx2D, p: CounterProps, text: string, font: string): TextBlockOptions {
  let minWidth = widthCache.get(p);
  if (minWidth === undefined) {
    const a = formatCounter(p.from ?? 0, p);
    const b = formatCounter(p.to ?? 100, p);
    const longest = a.length >= b.length ? a : b;
    minWidth = layoutText(ctx, { ...p, text: longest, tabularDigits: true, maxWidth: 1e5 }, font).w;
    widthCache.set(p, minWidth);
  }
  return { ...p, text, tabularDigits: true, maxWidth: 1e5, minWidth, highlight: null };
}

export const counterRenderer: LayerRenderer<CounterLayer> = {
  multipart: true,
  measure(ctx, layer, st, env) {
    const p = layer.props;
    const opts = counterOptions(ctx, p, formatCounter(counterValue(p, st.progress), p), envFont(env));
    return textBoxSize(layoutText(ctx, opts, envFont(env)), opts);
  },
  draw(ctx, layer, st, env) {
    const p = layer.props;
    const opts = counterOptions(ctx, p, formatCounter(counterValue(p, st.progress), p), envFont(env));
    drawTextLayout(ctx, layoutText(ctx, opts, envFont(env)), opts, env, null);
  },
};

/* ---------- полоса прогресса ---------- */

/** Во сколько раз текущая матрица увеличивает опорные px (тень и blur её не учитывают). */
export function deviceScale(ctx: Ctx2D): number {
  const m = ctx.getTransform();
  return Math.hypot(m.a, m.b) || 1;
}

export function fillStyleOf(ctx: Ctx2D, fill: string | [string, string] | undefined, x0: number, x1: number, fallback: string) {
  if (Array.isArray(fill) && fill.length === 2) {
    const g = ctx.createLinearGradient(x0, 0, x1, 0);
    g.addColorStop(0, rgba(fill[0]));
    g.addColorStop(1, rgba(fill[1]));
    return g;
  }
  return rgba((fill as string) || fallback);
}

const firstColor = (fill: string | [string, string] | undefined, fallback: string) =>
  Array.isArray(fill) ? fill[1] : fill || fallback;

export const progressRenderer: LayerRenderer<ProgressLayer> = {
  multipart: true,
  measure(_ctx, layer) {
    const p = layer.props;
    const h = Math.max(4, p.h ?? 56);
    return { w: Math.max(10, p.w ?? 800) + h, h: h * (p.label ? 3.4 : 1.6) };
  },
  draw(ctx, layer, st, env) {
    const p = layer.props;
    const w = Math.max(10, p.w ?? 800);
    const h = Math.max(4, p.h ?? 56);
    const r = Math.max(0, Math.min(p.radius ?? h / 2, h / 2));
    const value = clamp01(p.value ?? 0.75) * clamp01(st.progress);
    const accent = firstColor(p.fill, "#8B5CF6");

    // Капсула-дорожка с тонкой светлой окантовкой.
    fillRoundRect(ctx, -w / 2, -h / 2, w, h, r, rgba(p.track || "#ffffff26"));
    clearShadow(ctx);
    ctx.beginPath();
    roundRectPath(ctx, -w / 2, -h / 2, w, h, r);
    ctx.lineWidth = Math.max(2, h * 0.06);
    ctx.strokeStyle = rgba("#ffffff", 0.28);
    ctx.stroke();

    // Заливка утоплена внутрь дорожки — как «индикатор загрузки» в CapCut.
    const inset = Math.max(2, h * 0.14);
    const iw = (w - inset * 2) * value;
    const ih = h - inset * 2;
    if (iw > 0.5) {
      const ir = Math.max(0, Math.min(r - inset, ih / 2));
      const fx = -w / 2 + inset;
      ctx.save();
      if (p.glow !== false) {
        ctx.shadowColor = rgba(accent, 0.85);
        ctx.shadowBlur = h * 0.7 * deviceScale(ctx);
      }
      fillRoundRect(ctx, fx, -ih / 2, Math.max(iw, ir * 2), ih, ir, fillStyleOf(ctx, p.fill, fx, fx + w, "#8B5CF6"));
      ctx.restore();
      // Блик по верхней половине — объём без отдельной текстуры.
      const gloss = ctx.createLinearGradient(0, -ih / 2, 0, ih / 2);
      gloss.addColorStop(0, "rgba(255,255,255,0.38)");
      gloss.addColorStop(0.5, "rgba(255,255,255,0.06)");
      gloss.addColorStop(1, "rgba(255,255,255,0)");
      fillRoundRect(ctx, fx, -ih / 2, Math.max(iw, ir * 2), ih, ir, gloss);
    }

    if (p.label) {
      const size = Math.max(28, h * 0.9);
      const text = `${Math.round(value * 100)}%`;
      ctx.font = fontString({ family: p.font, weight: 900, size }, envFont(env));
      ctx.textBaseline = "alphabetic";
      // Подпись едет над головой заливки, но не вылезает за края полосы.
      const tw = ctx.measureText(text).width;
      const head = -w / 2 + inset + iw;
      const x = Math.max(-w / 2 + tw / 2, Math.min(w / 2 - tw / 2, head));
      ctx.textAlign = "center";
      ctx.lineJoin = "round";
      ctx.lineWidth = size * 0.14;
      ctx.strokeStyle = rgba(shade(accent, -0.75), 0.9);
      ctx.strokeText(text, x, -h / 2 - size * 0.35);
      ctx.fillStyle = rgba(p.labelColor || "#ffffff");
      ctx.fillText(text, x, -h / 2 - size * 0.35);
    }
  },
};

