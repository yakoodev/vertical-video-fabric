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
 *
 * Анимация (`anim`: none | subtle | lively) — своя, внутри слоя, функция локального времени
 * (`nameplateMotion`): без состояния и случайности, превью = рендер кадр в кадр.
 * Вход: значок выпрыгивает (outBack) вместе с «таблеткой» вокруг него, пилюля вытягивается
 * вправо, ник проявляется по буквам. Дальше редко и мягко: блик по пилюле и «тик» значка
 * (Twitch — глитч, YouTube — пульс «играть»…). `live` — красная пульсирующая точка.
 * `outro: "collapse"` — в последние NAMEPLATE_OUTRO_SEC вход проигрывается назад: ник гаснет,
 * пилюля сворачивается в значок, значок уходит.
 */
import { ease } from "../easing";
import { graphemes } from "../fonts";
import { drawPlatformIcon, platformIcon, type PlatformIcon } from "../glyphs";
import type { Ctx2D, NameplateLayer, NameplateProps, RenderEnv } from "../types";
import { clearShadow, fillRoundRect, isLight, rgba, roundRectPath, type LayerRenderer } from "./common";
import { cachedLayout, drawTextLayout, envFont, measure, type TextBlockOptions, type TextLayout } from "./text";

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
export const NAMEPLATE_ANIMS = ["none", "subtle", "lively"] as const;
export const NAMEPLATE_OUTROS = ["none", "collapse"] as const;
/** Высота пилюли по умолчанию, опорн. px (кадр 1080×1920). */
export const NAMEPLATE_SIZE = 74;
/** Длина свёртки в значок в конце слоя (`outro: "collapse"`), с. */
export const NAMEPLATE_OUTRO_SEC = 0.6;
/** Стекло: rgba(14,10,24,0.67) — как у ручной плашки. */
const GLASS_FILL = "#0e0a18ab";
const DARK_TEXT = "#0e0a18";
const LIVE_RED = "#ff2d2d";
const TIKTOK_CHROMA: [string, string] = ["#25f4ee", "#fe2c55"];
const GLITCH_CHROMA: [string, string] = ["#25f4ee", "#ff2d8a"];

/** Пропорции от высоты пилюли H: поля, значок, зазор, кегль, точка LIVE. */
const PAD = 0.42;
const ICON_H = 0.56;
const GAP = 0.26;
const FONT_K = 0.5;
const LIVE_R = 0.11;

/* ---------- анимация: тайминги пресетов ---------- */

type Span = [number, number];
type Preset = {
  /** Вход целиком, с. */
  enter: number;
  /** Значок (с «таблеткой» вокруг) выпрыгивает; ease — с перелётом. */
  pop: Span;
  popEase: string;
  /** Пилюля вытягивается вправо от значка. */
  open: Span;
  openEase: string;
  /** Ник проявляется по буквам: фронт идёт за это время, `soft` — сколько букв в полутени. */
  text: Span;
  soft: number;
  /** Блик: первый в `shineFirst` с, дальше каждые `shineEvery`, длится `shineDur`, яркость `shineAlpha`. */
  shineFirst: number;
  shineEvery: number;
  shineDur: number;
  shineAlpha: number;
  /** «Тик» значка: первый и период (с), длина. */
  tickFirst: number;
  tickEvery: number;
  tickDur: number;
  /** Сила тика (1 — как в subtle). */
  tickAmp: number;
};

const PRESETS: Record<"subtle" | "lively", Preset> = {
  subtle: {
    enter: 0.7,
    pop: [0, 0.32],
    popEase: "outBack",
    open: [0.16, 0.52],
    openEase: "outCubic",
    text: [0.3, 0.7],
    soft: 3,
    shineFirst: 6.6,
    shineEvery: 7,
    shineDur: 0.9,
    shineAlpha: 0.22,
    tickFirst: 6.6,
    tickEvery: 7,
    tickDur: 0.45,
    tickAmp: 1,
  },
  lively: {
    enter: 0.8,
    pop: [0, 0.36],
    popEase: "bezier(0.3,1.9,0.55,1)",
    open: [0.18, 0.58],
    openEase: "outBack",
    text: [0.32, 0.8],
    soft: 2,
    shineFirst: 6.6,
    shineEvery: 7,
    shineDur: 0.8,
    shineAlpha: 0.28,
    tickFirst: 3.1,
    tickEvery: 3.5,
    tickDur: 0.5,
    tickAmp: 1.5,
  },
};

/** Состояние плашки в момент `local` слоя длиной `duration` — чистая функция. */
export type NameplateMotion = {
  /** Масштаб значка с «таблеткой» вокруг него (pop), прозрачность пилюли и значка. */
  pop: number;
  alpha: number;
  /** Доля раскрытия пилюли: 0 — таблетка вокруг значка, 1 — целиком (у lively бывает чуть > 1). */
  open: number;
  /** Фронт проявления ника в буквах (n — все видны), полутень `soft` букв. */
  front: number;
  soft: number;
  /** Блик по пилюле 0..1 (ход слева направо) или null; его яркость. */
  shine: number | null;
  shineAlpha: number;
  /** Тик значка 0..1 или null; сила. */
  tick: number | null;
  tickAmp: number;
  /** Фаза пульса точки LIVE 0..1 или null — точка стоит. */
  live: number | null;
};

const span = (t: number, [a, b]: Span) => (b > a ? (t - a) / (b - a) : t >= b ? 1 : 0);

function cycle(local: number, first: number, every: number, dur: number): number | null {
  if (local < first || every <= 0) return null;
  const ph = ((local - first) % every) / dur;
  return ph >= 0 && ph < 1 ? ph : null;
}

export function nameplateMotion(p: NameplateProps, local: number, duration: number, letters: number): NameplateMotion {
  const anim = p.anim ?? "subtle";
  const full: NameplateMotion = { pop: 1, alpha: 1, open: 1, front: letters + 8, soft: 1, shine: null, shineAlpha: 0, tick: null, tickAmp: 0, live: null };
  if (anim === "none" || !(anim in PRESETS)) return full;
  const pr = PRESETS[anim as "subtle" | "lively"];
  // Время входа; при свёртке — вход назад за последние NAMEPLATE_OUTRO_SEC.
  let te = local;
  let outro = false;
  if (p.outro === "collapse" && duration > NAMEPLATE_OUTRO_SEC * 2) {
    const left = duration - local;
    if (left < NAMEPLATE_OUTRO_SEC) {
      te = Math.min(te, (Math.max(0, left) / NAMEPLATE_OUTRO_SEC) * pr.enter);
      outro = true;
    }
  }
  const k = span(te, pr.pop);
  const out: NameplateMotion = {
    pop: k <= 0 ? 0 : ease(pr.popEase, k),
    alpha: Math.min(1, Math.max(0, k * 2.5)),
    open: Math.min(1.06, Math.max(0, ease(pr.openEase, span(te, pr.open)))),
    front: Math.max(0, span(te, pr.text)) * (letters + pr.soft),
    soft: pr.soft,
    shine: null,
    shineAlpha: pr.shineAlpha,
    tick: null,
    tickAmp: pr.tickAmp,
    live: p.live ? (local % 1.6) / 1.6 : null,
  };
  if (span(te, pr.open) <= 0) out.open = 0;
  if (!outro && te >= pr.enter) {
    out.shine = cycle(local, pr.shineFirst, pr.shineEvery, pr.shineDur);
    out.tick = cycle(local, pr.tickFirst, pr.tickEvery, pr.tickDur);
  }
  return out;
}

/* ---------- геометрия ---------- */

type Geo = {
  h: number;
  /** Ширина пилюли целиком и свёрнутой (таблетка вокруг значка). */
  w: number;
  w0: number;
  /** Ширина рамки слоя (≥ w). */
  boxW: number;
  icon: PlatformIcon | null;
  iconW: number;
  text: TextLayout | null;
  textOpts: TextBlockOptions | null;
  accent: string;
  live: boolean;
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
  const live = Boolean(p.live);
  const liveW = live ? h * LIVE_R * 2 : 0;
  const parts = [iconW, text ? text.w : 0, liveW].filter((v) => v > 0);
  const inner = parts.reduce((a, b) => a + b, 0) + Math.max(0, parts.length - 1) * h * GAP;
  // Только значок — круглая «таблетка» не уже своей высоты.
  const w = Math.max(h, h * PAD * 2 + inner);
  const w0 = icon ? Math.max(h, h * PAD * 2 + iconW) : h;
  return { h, w, w0: Math.min(w0, w), boxW: Math.max(w, p.boxWidth ?? 0), icon, iconW, text, textOpts, accent, live };
}

/** Левые края графем ника от левого края текста (по ширине раскладки) — ячейки для проявления по буквам. */
function letterEdges(ctx: Ctx2D, L: TextLayout, handle: string): number[] {
  const gs = graphemes(handle);
  ctx.font = L.font;
  const total = measure(ctx, L.font, L.letterSpacing, handle) || 1;
  const k = L.w / total;
  const edges: number[] = [];
  let prefix = "";
  for (const g of gs) {
    edges.push(measure(ctx, L.font, L.letterSpacing, prefix) * k);
    prefix += g;
  }
  return edges;
}

/** Тик значка: сдвиг, масштаб, наклон и глитч-копии — по площадке. */
function tickTransform(platform: string | undefined, q: number, amp: number, h: number) {
  const bump = Math.sin(Math.PI * Math.min(1, Math.max(0, q)));
  const t = { dx: 0, dy: 0, sx: 1, sy: 1, rot: 0, chroma: null as [string, string] | null, chromaK: 1 };
  switch (platform) {
    case "twitch": {
      // Глитч: две короткие ступеньки вбок с цветными копиями, дальше тишина.
      if (q < 0.14) t.dx = 0.06 * h * amp;
      else if (q < 0.26) t.dx = -0.045 * h * amp;
      else if (q < 0.34) t.dx = 0.025 * h * amp;
      if (q < 0.34) t.chroma = GLITCH_CHROMA;
      break;
    }
    case "youtube":
      t.sx = t.sy = 1 + 0.12 * amp * bump; // пульс «играть»
      break;
    case "tiktok":
      t.chromaK = 1 + 1.8 * amp * bump; // ноту «растаскивает» по цветам
      break;
    case "telegram":
      t.rot = -0.14 * amp * bump; // самолётик подмигивает носом
      t.dx = 0.03 * h * amp * bump;
      t.dy = -0.03 * h * amp * bump;
      break;
    case "kick":
      t.sy = 1 - 0.1 * amp * bump; // приседает
      t.sx = 1 + 0.05 * amp * bump;
      break;
    default:
      break;
  }
  return t;
}

export const nameplateRenderer: LayerRenderer<NameplateLayer> = {
  multipart: true,
  measure(ctx, layer, _st, env) {
    const g = geometry(ctx, layer, env);
    return { w: g.boxW, h: g.h };
  },
  draw(ctx, layer, st, env) {
    const p = layer.props || {};
    const g = geometry(ctx, layer, env);
    if (!g.icon && !g.text) return;
    const handle = (p.handle || "").trim();
    const letters = g.text ? graphemes(handle).length : 0;
    const m = nameplateMotion(p, st.local, st.duration, letters);
    if (m.alpha <= 0 || m.pop <= 0.001) return;
    const style = p.style ?? "glass";
    const align = p.align ?? "center";
    const left = align === "left" ? -g.boxW / 2 : align === "right" ? g.boxW / 2 - g.w : -g.w / 2;
    const top = -g.h / 2;
    const r = g.h / 2;
    const curW = g.w0 + (g.w - g.w0) * m.open;
    const iconCx = left + (g.icon && !g.text && !g.live ? g.w / 2 : g.h * PAD + g.iconW / 2);
    const baseAlpha = ctx.globalAlpha;

    ctx.save();
    ctx.globalAlpha = baseAlpha * m.alpha;
    // Pop: значок вместе с пилюлей вокруг него выпрыгивает из своей середины.
    if (m.pop !== 1) {
      const cx = g.icon ? iconCx : left + curW / 2;
      ctx.translate(cx, 0);
      ctx.scale(m.pop, m.pop);
      ctx.translate(-cx, 0);
    }

    // Пилюля отбрасывает тень слоя (мягкая тень бренда), всё поверх — уже без тени.
    fillRoundRect(ctx, left, top, curW, g.h, r, style === "solid" ? rgba(g.accent, 0.96) : rgba(GLASS_FILL));
    clearShadow(ctx);
    if (style === "glass") {
      const lw = Math.max(2, g.h * 0.034);
      ctx.beginPath();
      roundRectPath(ctx, left + lw / 2, top + lw / 2, curW - lw, g.h - lw, r - lw / 2);
      ctx.lineWidth = lw;
      ctx.strokeStyle = rgba(g.accent);
      ctx.stroke();
    }

    // Всё содержимое — внутри текущей пилюли: ник выезжает из-под её края, пока она раскрывается.
    ctx.save();
    ctx.beginPath();
    roundRectPath(ctx, left, top, curW, g.h, r);
    ctx.clip();

    const fg = textColor(p, g.accent);
    let x = left + g.h * PAD;
    if (g.icon) {
      const iconColor = style === "solid" ? fg : p.platform === "tiktok" ? "#ffffff" : rgba(g.accent);
      let chroma = style === "glass" && p.platform === "tiktok" ? TIKTOK_CHROMA : null;
      const ih = g.h * ICON_H;
      ctx.save();
      if (m.tick !== null) {
        const t = tickTransform(p.platform, m.tick, m.tickAmp, g.h);
        ctx.translate(iconCx + t.dx, t.dy);
        if (t.rot) ctx.rotate(t.rot);
        ctx.scale(t.sx, t.sy);
        ctx.translate(-iconCx, 0);
        if (t.chroma) chroma = t.chroma;
        if (chroma && t.chromaK !== 1) {
          // Растащенные копии ноты: рисуем их шире, саму ноту — поверх обычным вызовом.
          const d = ih * 0.06 * t.chromaK;
          drawPlatformIcon(ctx, g.icon, iconCx - d, -d * 0.7, ih, chroma[0]);
          drawPlatformIcon(ctx, g.icon, iconCx + d, d * 0.7, ih, chroma[1]);
          chroma = null;
        }
      }
      drawPlatformIcon(ctx, g.icon, iconCx, 0, ih, iconColor, chroma);
      ctx.restore();
      x += g.iconW + g.h * GAP;
    }

    if (g.text && g.textOpts) {
      const L = g.text;
      const cx = x + L.w / 2;
      if (m.front >= letters + m.soft) {
        ctx.save();
        ctx.translate(cx, 0);
        drawTextLayout(ctx, L, g.textOpts, env, null);
        ctx.restore();
      } else if (m.front > 0) {
        // По буквам: ячейка буквы i видна с прозрачностью фронта и чуть приподнята, пока проявляется.
        const edges = letterEdges(ctx, L, handle);
        for (let i = 0; i < edges.length; i++) {
          const a = Math.min(1, Math.max(0, (m.front - i) / m.soft));
          if (a <= 0) break;
          const x0 = i === 0 ? -L.w / 2 - g.h : -L.w / 2 + edges[i];
          const x1 = i === edges.length - 1 ? L.w / 2 + g.h : -L.w / 2 + edges[i + 1];
          ctx.save();
          ctx.translate(cx, 0);
          ctx.beginPath();
          ctx.rect(x0, -g.h, x1 - x0, g.h * 2);
          ctx.clip();
          ctx.globalAlpha = baseAlpha * m.alpha * a;
          ctx.translate(0, (1 - ease("outCubic", a)) * g.h * 0.12);
          drawTextLayout(ctx, L, g.textOpts, env, null);
          ctx.restore();
        }
      }
      x += L.w + g.h * GAP;
    }

    if (g.live) {
      // Точка LIVE: проявляется последней, пульсирует кольцом.
      const a = letters ? Math.min(1, Math.max(0, (m.front - letters) / m.soft)) : m.open >= 1 ? 1 : m.open;
      if (a > 0) {
        const dx = left + g.w - g.h * PAD - g.h * LIVE_R;
        const rr = g.h * LIVE_R;
        if (m.live !== null) {
          const q = m.live;
          ctx.beginPath();
          ctx.arc(dx, 0, rr * (1 + 1.3 * q), 0, Math.PI * 2);
          ctx.fillStyle = rgba(LIVE_RED, 0.45 * (1 - q) * a);
          ctx.fill();
        }
        ctx.beginPath();
        ctx.arc(dx, 0, rr, 0, Math.PI * 2);
        ctx.fillStyle = rgba(LIVE_RED, (m.live !== null ? 0.8 + 0.2 * Math.cos(2 * Math.PI * m.live) : 1) * a);
        ctx.fill();
      }
    }

    if (m.shine !== null) {
      // Блик: косая светлая полоса проходит по пилюле слева направо.
      const band = g.h * 0.9;
      const bx = left - band + m.shine * (g.w + band * 2);
      ctx.transform(1, 0, -0.45, 1, 0, 0);
      const grad = ctx.createLinearGradient(bx - band / 2, 0, bx + band / 2, 0);
      grad.addColorStop(0, "rgba(255,255,255,0)");
      grad.addColorStop(0.5, `rgba(255,255,255,${m.shineAlpha})`);
      grad.addColorStop(1, "rgba(255,255,255,0)");
      ctx.fillStyle = grad;
      ctx.fillRect(bx - band / 2, top - g.h, band, g.h * 3);
    }
    ctx.restore(); // клип пилюли
    ctx.restore();
  },
};
