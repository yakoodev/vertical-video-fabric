/**
 * Карточка «в стиле соцсети» (`social`): комментарий под роликом или пост ленты.
 *
 * comment — кружок аватара, ник и время, текст, «Ответить», справа сердечко со счётом
 * (как комментарий под вертикальным роликом); post — аватар, имя с галочкой, @ник · время,
 * текст, картинка 5:3, внизу ответы / репосты / лайки (как пост ленты). Логотипов площадок нет:
 * значки — свои простые пути (сердце из glyphs.ts, «облачко», стрелки по кругу).
 *
 * Высота карточки — по тексту: перенос по словам общей раскладкой `layoutText` (кириллица,
 * эмодзи — как у текста), сверх `maxLines` строк текст обрезается «…». Лайки считают вверх
 * с `likes` до `likesTo` по ходу `progress` (нет ключей — за первые 60 % слоя), сердечко
 * при этом закрашивается с «пульсом». Всё — функции props и времени: превью = рендер.
 *
 * Вид — по мотивам блоков карточек каталога HyperFrames (HeyGen, Apache-2.0); код свой.
 */
import { clamp01 } from "../easing";
import { emojiRef, graphemes, isEmoji } from "../fonts";
import { heartPath } from "../glyphs";
import type { Ctx2D, RenderEnv, SocialLayer, SocialProps } from "../types";
import { clearShadow, drawImageLike, fillRoundRect, imageSize, rgba, roundRectPath, shade, type LayerRenderer } from "./common";
import { drawEmoji } from "./image";
import { drawTextLayout, envFont, layoutText, textCacheGeneration, type TextBlockOptions, type TextLayout } from "./text";

export const SOCIAL_STYLES = ["comment", "post"] as const;
export const SOCIAL_THEMES = ["dark", "light"] as const;
export const SOCIAL_MAX_LINES = 8;
/** Пропорция картинки поста (ширина / высота) — рамка постоянная, картинка обрезается в неё. */
export const SOCIAL_IMAGE_RATIO = 5 / 3;
const LIKED = "#ff3b5c";
const FONT = "Inter";

type Palette = { bg: string; edge: string; main: string; muted: string };

const PALETTES: Record<"dark" | "light", Palette> = {
  dark: { bg: "#17171df5", edge: "#ffffff1a", main: "#f4f4f7", muted: "#9b9ba8" },
  light: { bg: "#ffffff", edge: "#0000001a", main: "#15151b", muted: "#6c6c79" },
};

/** Размеры раскладки в опорных px — у comment и post свои. */
const DIM = {
  comment: { pad: 34, avatar: 92, gap: 26, side: 130, name: 34, text: 44, line: 1.28, reply: 31, count: 28 },
  post: { pad: 40, avatar: 100, gap: 24, side: 0, name: 40, text: 44, line: 1.3, reply: 0, count: 32 },
} as const;

/** Компактное число по-русски: 9 876, 12,3 тыс., 1,2 млн. */
export function formatSocialCount(n: number): string {
  const v = Math.max(0, Math.round(n));
  const short = (x: number, unit: string) => `${(Math.floor(x * 10) / 10).toFixed(x < 100 ? 1 : 0).replace(".", ",").replace(/,0$/, "")} ${unit}`;
  if (v >= 1_000_000) return short(v / 1_000_000, "млн");
  if (v >= 10_000) return short(v / 1000, "тыс.");
  return String(v).replace(/\B(?=(\d{3})+(?!\d))/g, " ");
}

/** Число лайков в момент хода `progress`. */
export function socialLikes(p: SocialProps, progress: number): number {
  const from = Math.max(0, Number(p.likes) || 0);
  const to = p.likesTo === null || p.likesTo === undefined ? from : Math.max(0, Number(p.likesTo) || 0);
  return from + (to - from) * clamp01(progress);
}

const ticking = (p: SocialProps) => p.likesTo !== null && p.likesTo !== undefined && Number(p.likesTo) !== Number(p.likes ?? 0);

type Geo = {
  w: number;
  h: number;
  text: TextLayout | null;
  textOpts: TextBlockOptions | null;
  textX: number;
  textY: number;
  name: TextLayout;
  nameOpts: TextBlockOptions;
  sub: TextLayout | null;
  subOpts: TextBlockOptions | null;
  imageY: number;
  imageH: number;
  footY: number;
};

const cache = new WeakMap<object, { key: string; geo: Geo }>();

function opts(text: string, font: string | undefined, size: number, weight: number, color: string, maxWidth: number, lineHeight = 1.15): TextBlockOptions {
  return { text, font: font || FONT, weight, size, color, align: "left", maxWidth, lineHeight };
}

/** Текст не длиннее `maxLines` строк: лишнее срезается по графемам и кончается «…». */
function clampLines(ctx: Ctx2D, o: TextBlockOptions, font: string, maxLines: number): TextLayout {
  const full = layoutText(ctx, o, font);
  if (full.lines.length <= maxLines) return full;
  const gs = graphemes(o.text);
  let lo = 0;
  let hi = gs.length;
  while (lo < hi) {
    const mid = Math.ceil((lo + hi) / 2);
    const L = layoutText(ctx, { ...o, text: `${gs.slice(0, mid).join("").trimEnd()}…` }, font);
    if (L.lines.length <= maxLines) lo = mid;
    else hi = mid - 1;
  }
  o.text = `${gs.slice(0, lo).join("").trimEnd()}…`;
  return layoutText(ctx, o, font);
}

function geometry(ctx: Ctx2D, layer: SocialLayer, env: RenderEnv): Geo {
  const p = layer.props;
  const font = envFont(env);
  const key = `${font}|${textCacheGeneration()}`;
  const hit = cache.get(p);
  if (hit && hit.key === key) return hit.geo;
  const post = p.style === "post";
  const d = post ? DIM.post : DIM.comment;
  const pal = PALETTES[p.theme === "light" ? "light" : "dark"];
  const w = Math.max(400, Math.min(2160, p.w ?? 900));
  const colX = d.pad + d.avatar + d.gap;
  const maxLines = Math.max(1, Math.min(20, Math.round(p.maxLines ?? SOCIAL_MAX_LINES)));
  const name = p.name || "зритель";
  let geo: Geo;
  if (post) {
    const nameOpts = opts(name, p.font, d.name, 800, pal.main, w - colX - d.pad - (p.verified ? d.name * 1.2 : 0));
    const subText = [p.handle ? (p.handle.startsWith("@") ? p.handle : `@${p.handle}`) : "", p.time || ""].filter(Boolean).join(" · ");
    const subOpts = subText ? opts(subText, p.font, 34, 400, pal.muted, w - colX - d.pad) : null;
    const innerW = w - d.pad * 2;
    const textOpts = p.text ? opts(p.text, p.font, d.text, 400, pal.main, innerW, d.line) : null;
    const text = textOpts ? clampLines(ctx, textOpts, font, maxLines) : null;
    const textY = d.pad + d.avatar + 26;
    const hasImage = p.image !== null && p.image !== undefined && p.image !== "";
    const imageY = textY + (text ? text.h + 28 : 0);
    const imageH = hasImage ? innerW / SOCIAL_IMAGE_RATIO : 0;
    const footY = imageY + (hasImage ? imageH + 30 : 0) + 22;
    geo = {
      w,
      h: footY + 22 + d.pad,
      text,
      textOpts,
      textX: d.pad,
      textY,
      name: layoutText(ctx, nameOpts, font),
      nameOpts,
      sub: subOpts ? layoutText(ctx, subOpts, font) : null,
      subOpts,
      imageY,
      imageH,
      footY,
    };
  } else {
    const nameOpts = opts(name, p.font, d.name, 600, pal.muted, w - colX - d.side - d.pad - (p.time ? 120 : 0));
    const subOpts = p.time ? opts(p.time, p.font, d.name, 400, shade(pal.muted, p.theme === "light" ? 0.25 : -0.25), 300) : null;
    const textMax = w - colX - d.side - d.pad;
    const textOpts = p.text ? opts(p.text, p.font, d.text, 400, pal.main, textMax, d.line) : null;
    const text = textOpts ? clampLines(ctx, textOpts, font, maxLines) : null;
    const nameL = layoutText(ctx, nameOpts, font);
    const textY = d.pad + nameL.h + 12;
    const footY = textY + (text ? text.h : 0) + 16 + d.reply * 0.6;
    const h = Math.max(d.pad * 2 + d.avatar, footY + d.reply * 0.6 + d.pad);
    geo = { w, h, text, textOpts, textX: colX, textY, name: nameL, nameOpts, sub: subOpts ? layoutText(ctx, subOpts, font) : null, subOpts, imageY: 0, imageH: 0, footY };
  }
  cache.set(p, { key, geo });
  return geo;
}

/** Блок текста левым верхним углом в (x, y). */
function drawAt(ctx: Ctx2D, L: TextLayout, o: TextBlockOptions, env: RenderEnv, x: number, y: number): void {
  ctx.save();
  ctx.translate(x + L.w / 2, y + L.h / 2);
  drawTextLayout(ctx, L, o, env, null);
  ctx.restore();
}

function drawAvatar(ctx: Ctx2D, p: SocialProps, env: RenderEnv, cx: number, cy: number, size: number): void {
  const accent = p.accent || "#8b5cf6";
  const r = size / 2;
  const ref = p.avatarImage !== null && p.avatarImage !== undefined && p.avatarImage !== "" ? String(p.avatarImage) : "";
  const img = ref ? env.getImage(ref) : null;
  ctx.save();
  ctx.beginPath();
  ctx.arc(cx, cy, r, 0, Math.PI * 2);
  if (img) {
    ctx.clip();
    const { w, h } = imageSize(img);
    const k = Math.max(size / w, size / h);
    drawImageLike(ctx, img, 0, 0, w, h, cx - (w * k) / 2, cy - (h * k) / 2, w * k, h * k, env);
    ctx.restore();
    return;
  }
  const g = ctx.createLinearGradient(cx - r, cy - r, cx + r, cy + r);
  g.addColorStop(0, shade(accent, 0.25));
  g.addColorStop(1, shade(accent, -0.2));
  ctx.fillStyle = g;
  ctx.fill();
  ctx.restore();
  const mark = (p.avatar || graphemes((p.name || "?").replace(/^@/, ""))[0] || "?").trim();
  const gs = graphemes(mark);
  if (gs.length && isEmoji(gs[0])) {
    ctx.save();
    ctx.translate(cx, cy);
    const pic = env.getImage(emojiRef(gs[0]));
    if (pic) drawImageLike(ctx, pic, 0, 0, pic.width, pic.height, -size * 0.31, -size * 0.31, size * 0.62, size * 0.62, env);
    else drawEmoji(ctx, gs[0], size * 0.62, env);
    ctx.restore();
    return;
  }
  const letters = gs.slice(0, 2).join("").toUpperCase();
  const o = opts(letters, p.font, size * (letters.length > 1 ? 0.36 : 0.44), 800, "#ffffff", size);
  const L = layoutText(ctx, o, envFont(env));
  ctx.save();
  ctx.translate(cx, cy);
  drawTextLayout(ctx, L, o, env, null);
  ctx.restore();
}

/** Галочка у имени: кружок акцента с белой «птичкой». */
function drawBadge(ctx: Ctx2D, cx: number, cy: number, r: number, color: string): void {
  ctx.beginPath();
  ctx.arc(cx, cy, r, 0, Math.PI * 2);
  ctx.fillStyle = rgba(color);
  ctx.fill();
  ctx.beginPath();
  ctx.moveTo(cx - r * 0.45, cy + r * 0.02);
  ctx.lineTo(cx - r * 0.1, cy + r * 0.38);
  ctx.lineTo(cx + r * 0.5, cy - r * 0.32);
  ctx.lineWidth = r * 0.28;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.strokeStyle = "#ffffff";
  ctx.stroke();
}

function drawHeart(ctx: Ctx2D, cx: number, cy: number, r: number, filled: boolean, color: string, scale = 1): void {
  ctx.save();
  ctx.translate(cx, cy);
  ctx.scale(scale, scale);
  ctx.beginPath();
  heartPath(ctx, 0, 0, r);
  if (filled) {
    ctx.fillStyle = LIKED;
    ctx.fill();
  } else {
    ctx.lineWidth = r * 0.16;
    ctx.lineJoin = "round";
    ctx.strokeStyle = rgba(color);
    ctx.stroke();
  }
  ctx.restore();
}

/** «Облачко» ответа: скруглённый пузырь с хвостиком. */
function drawBubble(ctx: Ctx2D, cx: number, cy: number, r: number, color: string): void {
  ctx.beginPath();
  roundRectPath(ctx, cx - r, cy - r * 0.8, r * 2, r * 1.5, r * 0.55);
  ctx.moveTo(cx - r * 0.45, cy + r * 0.7);
  ctx.lineTo(cx - r * 0.75, cy + r * 1.05);
  ctx.lineTo(cx - r * 0.05, cy + r * 0.7);
  ctx.lineWidth = r * 0.16;
  ctx.lineJoin = "round";
  ctx.strokeStyle = rgba(color);
  ctx.stroke();
}

/** Репост: две стрелки по кругу (верхняя вправо, нижняя влево). */
function drawRepost(ctx: Ctx2D, cx: number, cy: number, r: number, color: string): void {
  ctx.lineWidth = r * 0.16;
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  ctx.strokeStyle = rgba(color);
  for (const s of [1, -1]) {
    ctx.beginPath();
    ctx.moveTo(cx - s * r, cy + s * 0.15 * r);
    ctx.lineTo(cx - s * r, cy - s * 0.45 * r);
    ctx.lineTo(cx + s * r * 0.8, cy - s * 0.45 * r);
    ctx.moveTo(cx + s * r * 0.45, cy - s * 0.8 * r);
    ctx.lineTo(cx + s * r * 0.85, cy - s * 0.45 * r);
    ctx.lineTo(cx + s * r * 0.45, cy - s * 0.1 * r);
    ctx.stroke();
  }
}

export const socialRenderer: LayerRenderer<SocialLayer> = {
  multipart: true,
  measure(ctx, layer, _st, env) {
    const g = geometry(ctx, layer, env);
    return { w: g.w, h: g.h };
  },
  draw(ctx, layer, st, env) {
    const p = layer.props;
    const g = geometry(ctx, layer, env);
    const post = p.style === "post";
    const d = post ? DIM.post : DIM.comment;
    const pal = PALETTES[p.theme === "light" ? "light" : "dark"];
    const left = -g.w / 2;
    const top = -g.h / 2;
    const radius = post ? 34 : 38;
    fillRoundRect(ctx, left, top, g.w, g.h, radius, rgba(pal.bg));
    clearShadow(ctx);
    ctx.beginPath();
    roundRectPath(ctx, left + 1, top + 1, g.w - 2, g.h - 2, radius - 1);
    ctx.lineWidth = 2;
    ctx.strokeStyle = rgba(pal.edge);
    ctx.stroke();

    drawAvatar(ctx, p, env, left + d.pad + d.avatar / 2, top + d.pad + d.avatar / 2, d.avatar);

    // Счёт лайков и «пульс» сердечка, пока счёт идёт вверх.
    const prog = st.progress;
    const likes = formatSocialCount(socialLikes(p, prog));
    const tick = ticking(p);
    const filled = Boolean(p.liked) || (tick && prog > 0.02);
    const pulse = tick && prog > 0 && prog < 0.3 ? 1 + 0.35 * Math.sin((prog / 0.3) * Math.PI) : 1;
    const countOpts = (text: string) => opts(text, p.font, d.count, 600, pal.muted, 400);

    if (post) {
      const colX = left + d.pad + d.avatar + d.gap;
      const nameY = top + d.pad + (g.sub ? d.avatar * 0.3 : d.avatar / 2) - g.name.h / 2;
      drawAt(ctx, g.name, g.nameOpts, env, colX, nameY);
      if (p.verified) drawBadge(ctx, colX + g.name.w + d.name * 0.6, nameY + g.name.h / 2, d.name * 0.42, p.accent || "#3b82f6");
      if (g.sub && g.subOpts) drawAt(ctx, g.sub, g.subOpts, env, colX, top + d.pad + d.avatar * 0.74 - g.sub.h / 2);
      if (g.text && g.textOpts) drawAt(ctx, g.text, g.textOpts, env, left + g.textX, top + g.textY);
      if (g.imageH > 0) {
        const ix = left + d.pad;
        const iy = top + g.imageY;
        const iw = g.w - d.pad * 2;
        const img = env.getImage(String(p.image));
        ctx.save();
        ctx.beginPath();
        roundRectPath(ctx, ix, iy, iw, g.imageH, 26);
        if (img) {
          ctx.clip();
          const { w, h } = imageSize(img);
          const k = Math.max(iw / w, g.imageH / h);
          drawImageLike(ctx, img, 0, 0, w, h, ix + iw / 2 - (w * k) / 2, iy + g.imageH / 2 - (h * k) / 2, w * k, h * k, env);
        } else {
          ctx.fillStyle = rgba(pal.edge);
          ctx.fill();
        }
        ctx.restore();
      }
      // Счётчики: ответы, репосты, лайки — слева направо по трети ширины.
      const fy = top + g.footY;
      const slot = (g.w - d.pad * 2) / 3;
      const items: [number, string, (x: number) => void][] = [];
      if (p.replies !== null && p.replies !== undefined) items.push([0, formatSocialCount(p.replies), (x) => drawBubble(ctx, x, fy, 19, pal.muted)]);
      if (p.reposts !== null && p.reposts !== undefined) items.push([1, formatSocialCount(p.reposts), (x) => drawRepost(ctx, x, fy, 20, pal.muted)]);
      items.push([2, likes, (x) => drawHeart(ctx, x, fy, 22, filled, pal.muted, pulse)]);
      for (const [i, text, icon] of items) {
        const x = left + d.pad + slot * i + 22;
        icon(x);
        const o = countOpts(text);
        if (filled && i === 2) o.color = LIKED;
        const L = layoutText(ctx, o, envFont(env));
        drawAt(ctx, L, o, env, x + 36, fy - L.h / 2);
      }
      return;
    }

    // comment: ник · время, текст, «Ответить»; справа — сердечко и счёт.
    const colX = left + g.textX;
    drawAt(ctx, g.name, g.nameOpts, env, colX, top + d.pad);
    if (g.sub && g.subOpts) drawAt(ctx, g.sub, g.subOpts, env, colX + g.name.w + 16, top + d.pad);
    if (g.text && g.textOpts) drawAt(ctx, g.text, g.textOpts, env, colX, top + g.textY);
    const reply = opts("Ответить", p.font, d.reply, 600, pal.muted, 400);
    const RL = layoutText(ctx, reply, envFont(env));
    drawAt(ctx, RL, reply, env, colX, top + g.footY - RL.h / 2);
    const hx = left + g.w - d.pad - d.side / 2;
    const hy = top + d.pad + 30;
    drawHeart(ctx, hx, hy, 24, filled, pal.muted, pulse);
    const co = countOpts(likes);
    if (filled) co.color = LIKED;
    const CL = layoutText(ctx, co, envFont(env));
    ctx.save();
    // «15,4 тыс.» шире колонки сердечка — прижимаем к правому полю, а не на текст.
    ctx.translate(Math.min(hx, left + g.w - d.pad * 0.6 - CL.w / 2), hy + 30 + CL.h / 2 + 6);
    drawTextLayout(ctx, CL, { ...co, align: "center" }, env, null);
    ctx.restore();
  },
};
