/**
 * Карусель карточек (`carousel`): картинка, имя, подпись и строка «▶ 1,2 млн прослушиваний».
 * Активная карточка — в центре и крупнее, за ней светятся концентрические кольца (мягко дышат,
 * одно кольцо волной уходит наружу); соседи выглядывают с краёв меньше и бледнее. В моменты
 * `switchAt` (или каждые `interval` секунд) лента переезжает к следующей карточке с плавной кривой.
 *
 * Положение ленты — непрерывный номер карточки c(t): сумма кривых переездов. По кругу (`wrap`) за
 * последней снова первая. Чистая функция props и времени: превью = рендер.
 */
import { clamp01, ease } from "../easing";
import type { CarouselCard, CarouselLayer, CarouselProps, Ctx2D, RenderEnv } from "../types";
import { drawAdIcon, isAdIcon } from "./adicons";
import { accentTone, AD_ACCENT, centerText, drawRoundImage, fitFont, glowDisc, imageRef, mixHex } from "./adkit";
import { clearShadow, hasShadow, rgba, roundRectPath, type LayerRenderer } from "./common";

export const CAROUSEL_MAX = 12;

const cards = (p: CarouselProps): CarouselCard[] => (Array.isArray(p.cards) ? p.cards.slice(0, CAROUSEL_MAX) : []);
const cardW = (p: CarouselProps) => Math.max(120, p.cardW ?? 600);
const cardH = (p: CarouselProps) => Math.max(160, p.cardH ?? 780);

/** Моменты переездов (секунды слоя) до `until`: заданные или каждые `interval` (по умолчанию 1.8 с). */
export function carouselSwitches(p: CarouselProps, until: number): number[] {
  if (Array.isArray(p.switchAt) && p.switchAt.length) return [...p.switchAt].filter((t) => Number.isFinite(t)).sort((a, b) => a - b);
  const step = p.interval ?? 1.8;
  if (!(step > 0.2)) return [];
  const out: number[] = [];
  for (let t = step; t <= until + 1e-6 && out.length < 400; t += step) out.push(t);
  return out;
}

/** Непрерывный номер карточки в центре в момент `local` (0 — первая). */
export function carouselIndex(p: CarouselProps, local: number): number {
  const n = cards(p).length;
  const dur = Math.max(0.1, p.switchDur ?? 0.65);
  let c = Math.round(p.start ?? 0);
  for (const t of carouselSwitches(p, local)) c += ease(p.ease || "inOutCubic", (local - t) / dur);
  if (p.wrap === false) c = Math.max(0, Math.min(n - 1, c));
  return c;
}

/** Сдвиг карточки `i` от центра в карточках (по кругу — ближайший путь). */
export function carouselOffset(p: CarouselProps, i: number, c: number): number {
  const n = cards(p).length;
  let d = i - c;
  if (p.wrap !== false && n > 0) {
    d = ((d % n) + n) % n;
    if (d >= n / 2) d -= n;
  }
  return d;
}

function drawHalo(ctx: Ctx2D, p: CarouselProps, color: string, local: number, power: number): void {
  if (power <= 0.01) return;
  const R = cardH(p) * 0.56;
  glowDisc(ctx, 0, 0, R * 1.55, color, 0.42 * power);
  ctx.save();
  ctx.lineWidth = 3;
  for (let k = 0; k < 4; k++) {
    const breathe = Math.sin(Math.PI * 2 * (0.42 * local - 0.12 * k));
    const r = R * (1 + 0.17 * k) * (1 + 0.018 * breathe);
    ctx.beginPath();
    ctx.arc(0, 0, r, 0, Math.PI * 2);
    ctx.strokeStyle = rgba(mixHex(color, "#ffffff", 0.25), power * 0.5 * Math.pow(0.68, k) * (0.85 + 0.15 * breathe));
    ctx.stroke();
  }
  // Волна: одно кольцо медленно расходится и тает.
  const q = (local * 0.32) % 1;
  ctx.beginPath();
  ctx.arc(0, 0, R * (1 + 0.85 * q), 0, Math.PI * 2);
  ctx.lineWidth = 4;
  ctx.strokeStyle = rgba(color, power * 0.45 * (1 - q));
  ctx.stroke();
  ctx.restore();
}

function drawCard(ctx: Ctx2D, card: CarouselCard, i: number, p: CarouselProps, env: RenderEnv, active: number, castShadow: boolean): void {
  const W = cardW(p);
  const H = cardH(p);
  const r = Math.max(0, p.radius ?? 40);
  const color = card.color || accentTone(p.accent || AD_ACCENT, i);
  const x = -W / 2;
  const y = -H / 2;
  const shadow = [ctx.shadowColor, ctx.shadowBlur, ctx.shadowOffsetX, ctx.shadowOffsetY] as const;
  if (!castShadow) clearShadow(ctx);
  // Подложка карточки (она и отбрасывает тень слоя): цвет карточки градиентом.
  const bg = ctx.createLinearGradient(0, y, 0, y + H);
  bg.addColorStop(0, rgba(mixHex(color, "#ffffff", 0.12)));
  bg.addColorStop(1, rgba(mixHex(color, "#05060a", 0.72)));
  ctx.beginPath();
  roundRectPath(ctx, x, y, W, H, r);
  ctx.fillStyle = bg;
  ctx.fill();
  clearShadow(ctx);
  const img = imageRef(card.image) ? env.getImage(String(card.image)) : null;
  if (img) drawRoundImage(ctx, img, x, y, W, H, r, env);
  else if (card.title) {
    // Без картинки — крупная буква имени поверх градиента.
    const letter = Array.from(card.title.trim())[0]?.toLocaleUpperCase("ru-RU") ?? "";
    const size = fitFont(ctx, letter, p.font, 800, H * 0.34, W * 0.8, env);
    centerText(ctx, letter, 0, y + H * 0.36, size, rgba("#ffffff", 0.88));
  }
  // Низ — в тень, чтобы подписи читались на любой картинке.
  const fade = ctx.createLinearGradient(0, y + H * 0.42, 0, y + H);
  fade.addColorStop(0, "rgba(5,6,10,0)");
  fade.addColorStop(0.55, "rgba(5,6,10,0.62)");
  fade.addColorStop(1, "rgba(5,6,10,0.9)");
  ctx.beginPath();
  roundRectPath(ctx, x, y, W, H, r);
  ctx.fillStyle = fade;
  ctx.fill();
  // Кромка: у активной — цвет карточки.
  ctx.lineWidth = 3;
  ctx.strokeStyle = rgba(mixHex(color, "#ffffff", 0.45), 0.25 + 0.6 * active);
  ctx.stroke();

  const font = p.font;
  let ty = y + H - 54;
  if (card.meta) {
    const size = fitFont(ctx, card.meta, font, 600, 30, W - 140, env);
    ctx.textAlign = "left";
    const tw = ctx.measureText(card.meta).width;
    const icon = isAdIcon(card.metaIcon ?? "play") ? card.metaIcon ?? "play" : null;
    const iw = icon ? size * 1.5 : 0;
    const left = -(tw + iw) / 2;
    if (icon) {
      ctx.beginPath();
      ctx.arc(left + size * 0.55, ty, size * 0.62, 0, Math.PI * 2);
      ctx.fillStyle = rgba("#ffffff", 0.18);
      ctx.fill();
      drawAdIcon(ctx, icon, left + size * 0.58, ty, size * 0.72, "#ffffff");
    }
    ctx.fillStyle = rgba("#ffffff", 0.8);
    ctx.fillText(card.meta, left + iw, ty + size * 0.36);
    ty -= size + 30;
  }
  if (card.subtitle) {
    const size = fitFont(ctx, card.subtitle, font, 500, 32, W - 80, env);
    centerText(ctx, card.subtitle, 0, ty, size, rgba("#ffffff", 0.72));
    ty -= size + 26;
  }
  if (card.title) {
    const size = fitFont(ctx, card.title, font, 800, 58, W - 70, env);
    centerText(ctx, card.title, 0, ty - 4, size, "#ffffff");
  }
  ctx.shadowColor = shadow[0];
  ctx.shadowBlur = shadow[1];
  ctx.shadowOffsetX = shadow[2];
  ctx.shadowOffsetY = shadow[3];
}

export const carouselRenderer: LayerRenderer<CarouselLayer> = {
  multipart: true,
  measure(_ctx, layer) {
    const p = layer.props;
    return { w: Math.max(200, p.w ?? 1080), h: cardH(p) * 1.5 };
  },
  draw(ctx, layer, st, env) {
    const p = layer.props;
    const list = cards(p);
    if (!list.length) return;
    const c = carouselIndex(p, st.local);
    const W = cardW(p);
    const spacing = p.spacing ?? W * 0.86;
    const sideScale = Math.max(0.3, Math.min(1, p.sideScale ?? 0.8));
    const sideOpacity = clamp01(p.sideOpacity ?? 0.5);
    const castShadow = hasShadow(ctx);
    const shadow = [ctx.shadowColor, ctx.shadowBlur, ctx.shadowOffsetX, ctx.shadowOffsetY] as const;
    const views = list.map((card, i) => ({ card, i, d: carouselOffset(p, i, c) })).filter((v) => Math.abs(v.d) < 1.75);
    // Кольца — цветом той карточки, что ближе к центру.
    const near = views.reduce((a, b) => (Math.abs(b.d) < Math.abs(a.d) ? b : a), views[0]);
    if (p.halo !== false && near) {
      clearShadow(ctx);
      drawHalo(ctx, p, near.card.color || accentTone(p.accent || AD_ACCENT, near.i), st.local, 1 - 0.35 * Math.min(1, Math.abs(near.d) * 2));
      ctx.shadowColor = shadow[0];
      ctx.shadowBlur = shadow[1];
      ctx.shadowOffsetX = shadow[2];
      ctx.shadowOffsetY = shadow[3];
    }
    views.sort((a, b) => Math.abs(b.d) - Math.abs(a.d));
    for (const v of views) {
      const a = Math.abs(v.d);
      const scale = a <= 1 ? 1 + (sideScale - 1) * a : sideScale * (1 - 0.12 * (a - 1));
      // Дальше соседа карточка уходит за край и тает — у краёв видно только по одной соседке.
      const alpha = (a <= 1 ? 1 + (sideOpacity - 1) * a : sideOpacity) * clamp01((1.75 - a) / 0.6);
      if (alpha <= 0.002) continue;
      ctx.save();
      ctx.globalAlpha *= alpha;
      ctx.translate(v.d * spacing, 0);
      ctx.scale(scale, scale);
      drawCard(ctx, v.card, v.i, p, env, clamp01(1 - a), castShadow);
      ctx.restore();
    }
    clearShadow(ctx);
  },
};
