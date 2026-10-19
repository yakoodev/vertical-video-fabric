/**
 * Чат стрима поверх клипа: сообщения Twitch всплывают в такт видео, как в
 * нарезках стримов. Новые — снизу, старые уезжают вверх и гаснут.
 *
 * Сообщения приходят от хоста (`comp.chat`: в браузере — `GET /api/clip-plans/{id}/chat`,
 * в рендере — то же самое из бэкенда), слой задаёт только вид и фильтры.
 * Эмоуты и значки — картинки по ссылке: движок спрашивает их у env как
 * `url:<адрес>` (браузер грузит адрес напрямую, рендер — из кэша бэкенда).
 *
 * Стопка считается от времени, без состояния: позиция и прозрачность каждого
 * сообщения — функция момента t, поэтому превью и рендер совпадают кадр в кадр.
 */
import { clamp01, ease } from "../easing";
import { fontString } from "../fonts";
import type { ChatLayer, ChatMessage, ChatProps, ChatStyle, Ctx2D, RenderEnv } from "../types";
import { clearShadow, drawImageLike, fillRoundRect, isLight, parseHex, rgba, roundRectPath, shade, type FrameInfo, type LayerRenderer } from "./common";
import { deviceScale } from "./counter";
import { envFont, measure, setLetterSpacing } from "./text";

export type ChatStyleInfo = { id: ChatStyle; name: string; description: string };

export const CHAT_STYLES: ChatStyleInfo[] = [
  { id: "twitch-dark", name: "Twitch", description: "Тёмная колонка как в самом Twitch: ник цветом, текст белым" },
  { id: "bubbles", name: "Пузыри", description: "Каждое сообщение в светлом пузыре, ник сверху" },
  { id: "minimal", name: "Минимал", description: "Без подложки: белый текст с тёмной обводкой" },
  { id: "neon", name: "Неон", description: "Тёмное стекло, рамка светится цветом ника" },
];

/** Ссылка на картинку эмоута/значка для env.getImage. */
export const chatImageRef = (url: string) => `url:${url}`;

export type ResolvedChat = Required<Omit<ChatProps, "filter">> & { filter: Required<NonNullable<ChatProps["filter"]>> };

const DEFAULTS: ResolvedChat = {
  style: "twitch-dark",
  w: 760,
  maxMessages: 6,
  fontSize: 34,
  font: "Inter",
  showBadges: true,
  showEmotes: true,
  filter: { minLength: 0, hideLinks: true, hideCommands: true },
  highlightUsers: [],
  delay: 0,
  bg: 0.75,
  ttl: 15,
};

const resolvedCache = new WeakMap<object, ResolvedChat>();

export function resolveChat(props: ChatProps): ResolvedChat {
  const hit = resolvedCache.get(props);
  if (hit) return hit;
  const out = { ...DEFAULTS } as Record<string, unknown>;
  for (const [k, v] of Object.entries(props || {})) if (v !== undefined && v !== null && k in DEFAULTS) out[k] = v;
  const r = out as ResolvedChat;
  r.filter = { ...DEFAULTS.filter, ...(props?.filter || {}) } as ResolvedChat["filter"];
  r.maxMessages = Math.max(1, Math.min(12, Math.round(r.maxMessages)));
  r.fontSize = Math.max(12, r.fontSize);
  r.w = Math.max(200, r.w);
  r.bg = clamp01(r.bg);
  if (!CHAT_STYLES.some((s) => s.id === r.style)) r.style = "twitch-dark";
  if (!r.font) r.font = DEFAULTS.font;
  resolvedCache.set(props, r);
  return r;
}

/* ---------- отбор сообщений ---------- */

const LINK = /(https?:\/\/|www\.)\S+/i;

export function chatMessagePasses(m: ChatMessage, r: ResolvedChat): boolean {
  const text = (m.text || "").trim();
  if (!text && !(m.fragments || []).some((f) => f.emote)) return false;
  if (r.filter.minLength > 0 && Array.from(text).length < r.filter.minLength) return false;
  if (r.filter.hideLinks && LINK.test(text)) return false;
  if (r.filter.hideCommands && text.startsWith("!")) return false;
  return true;
}

type Entry = { msg: ChatMessage; ts: number };

const listCache = new WeakMap<object, WeakMap<object, { key: string; list: Entry[] }>>();

/** Сообщения окна слоя после фильтра, со сдвигом на `delay`, по времени показа. */
export function chatEntries(chat: ChatMessage[], props: ChatProps, from: number, to: number): Entry[] {
  const key = `${from}|${to}`;
  let byProps = listCache.get(chat);
  if (!byProps) {
    byProps = new WeakMap();
    listCache.set(chat, byProps);
  }
  const hit = byProps.get(props);
  if (hit && hit.key === key) return hit.list;
  const r = resolveChat(props);
  const list: Entry[] = [];
  for (const msg of chat) {
    const ts = Number(msg.t) - r.delay;
    if (!Number.isFinite(ts) || ts < from || ts >= to || !chatMessagePasses(msg, r)) continue;
    list.push({ msg, ts });
  }
  list.sort((a, b) => a.ts - b.ts);
  byProps.set(props, { key, list });
  return list;
}

/** Ссылки на все картинки чата (эмоуты, значки) — для предзагрузки. */
export function collectChatImageRefs(chat: ChatMessage[], props?: ChatProps): string[] {
  const r = resolveChat(props || {});
  const out = new Set<string>();
  for (const m of chat || []) {
    if (r.showEmotes) for (const f of m.fragments || []) if (f.emote && f.url) out.add(chatImageRef(f.url));
    if (r.showBadges) for (const b of m.badges || []) if (b.url) out.add(chatImageRef(b.url));
  }
  return [...out];
}

/* ---------- раскладка сообщения ---------- */

type Atom = {
  kind: "text" | "img";
  text: string;
  ref?: string;
  w: number;
  /** Пробел перед атомом (переносить строку можно тут). */
  gap: boolean;
  /** Перенос без пробела: продолжение разрезанного длинного слова. */
  brk?: boolean;
  role: "badge" | "name" | "body" | "emote";
};

type Laid = { atom: Atom; x: number; line: number };

type MsgLayout = {
  items: Laid[];
  lines: number;
  /** Высота сообщения с отступами стиля (опорн. px). */
  h: number;
  /** Есть ещё не загруженные картинки — раскладку надо пересчитать позже. */
  pending: boolean;
};

type Geo = {
  size: number;
  lineH: number;
  emoteH: number;
  badgeH: number;
  padX: number;
  padY: number;
  gap: number;
  /** У пузырей ник — отдельной строкой над текстом. */
  nameLine: boolean;
  nameSize: number;
};

function geoOf(r: ResolvedChat): Geo {
  const size = r.fontSize;
  const bubbles = r.style === "bubbles";
  const boxed = r.style === "bubbles" || r.style === "neon";
  return {
    size,
    lineH: size * 1.38,
    emoteH: size * 1.3,
    badgeH: size * 0.95,
    padX: boxed ? size * 0.55 : r.style === "minimal" ? 0 : size * 0.6,
    padY: boxed ? size * 0.32 : size * 0.12,
    gap: boxed ? size * 0.32 : size * 0.1,
    nameLine: bubbles,
    nameSize: bubbles ? size * 0.78 : size,
  };
}

const bodyFont = (r: ResolvedChat, size: number, def: string) => fontString({ family: r.font, weight: 600, size }, def);
const nameFont = (r: ResolvedChat, size: number, def: string) => fontString({ family: r.font, weight: 800, size }, def);

const layoutCache = new WeakMap<object, { key: object; layout: MsgLayout }>();

function layoutMessage(ctx: Ctx2D, m: ChatMessage, r: ResolvedChat, env: RenderEnv, def: string): MsgLayout {
  const hit = layoutCache.get(m);
  if (hit && hit.key === r && !hit.layout.pending) return hit.layout;
  const g = geoOf(r);
  const bf = bodyFont(r, g.size, def);
  const nf = nameFont(r, g.nameSize, def);
  ctx.font = bf;
  setLetterSpacing(ctx, 0);
  const space = measure(ctx, bf, 0, " ");
  const inner = r.w - g.padX * 2;
  let pending = false;

  const head: Atom[] = [];
  if (r.showBadges) {
    for (const b of m.badges || []) {
      if (!b.url) continue;
      const ref = chatImageRef(b.url);
      if (!env.getImage(ref)) {
        pending = true;
        continue;
      }
      head.push({ kind: "img", text: "", ref, w: g.badgeH, gap: head.length > 0, role: "badge" });
    }
  }
  const nameText = g.nameLine ? m.user : `${m.user}:`;
  // measure() берёт текущий ctx.font при промахе кэша — ставим жирный на время замера ника.
  ctx.font = nf;
  head.push({ kind: "text", text: nameText, w: measure(ctx, nf, 0, nameText), gap: head.length > 0, role: "name" });
  ctx.font = bf;

  const body: Atom[] = [];
  const frags = m.fragments && m.fragments.length ? m.fragments : [{ text: m.text }];
  let pendingGap = !g.nameLine; // после «ник:» — пробел
  for (const f of frags) {
    if (f.emote) {
      const ref = chatImageRef(f.url);
      const img = r.showEmotes ? env.getImage(ref) : null;
      if (img && img.width > 0 && img.height > 0) {
        body.push({ kind: "img", text: f.emote, ref, w: (g.emoteH * img.width) / img.height, gap: pendingGap, role: "emote" });
      } else {
        if (r.showEmotes) pending = true;
        body.push({ kind: "text", text: f.emote, w: measure(ctx, bf, 0, f.emote), gap: pendingGap, role: "body" });
      }
      pendingGap = false;
      continue;
    }
    const parts = (f.text || "").split(/(\s+)/);
    for (const part of parts) {
      if (!part) continue;
      if (/^\s+$/.test(part)) {
        pendingGap = true;
        continue;
      }
      // Слишком длинное слово («ааааааааа») режем по буквам — колонка не раздувается.
      let rest = Array.from(part);
      let first = true;
      while (rest.length) {
        let n = rest.length;
        while (n > 1 && measure(ctx, bf, 0, rest.slice(0, n).join("")) > inner) n = Math.max(1, Math.floor(n * 0.8));
        const chunk = rest.slice(0, n).join("");
        body.push({ kind: "text", text: chunk, w: measure(ctx, bf, 0, chunk), gap: pendingGap, brk: !first, role: "body" });
        pendingGap = false;
        first = false;
        rest = rest.slice(n);
      }
    }
  }

  const items: Laid[] = [];
  let line = 0;
  let x = 0;
  const place = (a: Atom) => {
    const gapW = a.role === "badge" || (items.length && items[items.length - 1].atom.role === "badge") ? g.size * 0.2 : space;
    const need = (x > 0 && a.gap ? gapW : 0) + a.w;
    if (x > 0 && x + need > inner && (a.gap || a.brk || a.role === "emote")) {
      line++;
      x = 0;
    }
    if (x > 0 && (a.gap || a.role === "name")) x += gapW;
    items.push({ atom: a, x, line });
    x += a.w;
  };
  head.forEach(place);
  if (g.nameLine) {
    line++;
    x = 0;
  }
  body.forEach(place);
  const lines = line + 1;
  const nameRows = g.nameLine ? 1 : 0;
  const h = g.padY * 2 + (lines - nameRows) * g.lineH + nameRows * g.nameSize * 1.3;
  const layout = { items, lines, h, pending };
  layoutCache.set(m, { key: r, layout });
  return layout;
}

/* ---------- кадр ---------- */

/** Насколько сообщение «въехало» (0..1) — от этого и сдвиг стопки, и появление. */
const APPEAR = 0.28;
/** Сколько ещё сообщений старше видимых считать (они гаснут наверху). */
const SPARE = 3;

function upperBound(list: Entry[], t: number): number {
  let lo = 0;
  let hi = list.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (list[mid].ts <= t) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

/** Читаемый цвет ника на тёмном или светлом фоне. */
function nameColor(color: string | undefined, onLight: boolean): string {
  const c = color && parseHex(color) ? color : "#9146FF";
  if (onLight) return isLight(c) ? shade(c, -0.45) : c;
  const rgb = parseHex(c);
  const lum = rgb ? 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2] : 255;
  return lum < 90 ? shade(c, 0.45) : c;
}

type Shown = { e: Entry; lay: MsgLayout; a: number; alpha: number; bottom: number };

function stackAt(ctx: Ctx2D, layer: ChatLayer, env: RenderEnv, frame: FrameInfo): { shown: Shown[]; r: ResolvedChat; stackH: number } {
  const r = resolveChat(layer.props);
  const list = chatEntries(frame.chat, layer.props, layer.start, layer.end);
  const t = frame.t;
  const end = upperBound(list, t);
  const begin = Math.max(0, end - r.maxMessages - SPARE);
  const def = envFont(env);
  const shown: Shown[] = [];
  let rank = 0;
  let offset = 0;
  for (let k = end - 1; k >= begin; k--) {
    const e = list[k];
    const age = t - e.ts;
    const a = ease("outCubic", age / APPEAR);
    const lay = layoutMessage(ctx, e.msg, r, env, def);
    const overflow = clamp01(r.maxMessages - rank);
    const ttl = r.ttl > 0 ? clamp01((r.ttl - age) / 0.6) : 1;
    const alpha = clamp01(age / 0.18) * overflow * ttl;
    if (alpha > 0.002) shown.push({ e, lay, a, alpha, bottom: offset });
    // Место в стопке занимает «въехавшая» доля сообщения, исчезающее — освобождает.
    const vis = a * Math.min(overflow, ttl);
    offset += lay.h * vis + geoOf(r).gap * vis;
    rank += a;
  }
  return { shown, r, stackH: offset };
}

function boxHeight(r: ResolvedChat): number {
  const g = geoOf(r);
  return r.maxMessages * (g.lineH + g.padY * 2 + g.gap) + (g.nameLine ? r.maxMessages * g.nameSize * 1.3 : 0);
}

function drawMessage(ctx: Ctx2D, s: Shown, r: ResolvedChat, env: RenderEnv, def: string, left: number, top: number): void {
  const g = geoOf(r);
  const m = s.e.msg;
  const lay = s.lay;
  const hl = r.highlightUsers.some((u) => u && u.toLowerCase() === (m.user || "").toLowerCase());
  const user = m.color || "#9146FF";
  const w = r.w;

  // Подложка сообщения по стилю.
  if (r.style === "bubbles") {
    const bubbleW = Math.min(w, Math.max(...lay.items.map((it) => it.x + it.atom.w)) + g.padX * 2);
    ctx.save();
    ctx.shadowColor = "rgba(0,0,0,0.28)";
    ctx.shadowBlur = g.size * 0.5 * deviceScale(ctx);
    ctx.shadowOffsetY = g.size * 0.12 * deviceScale(ctx);
    fillRoundRect(ctx, left, top, bubbleW, lay.h, g.size * 0.6, hl ? rgba("#9146FF", Math.max(0.85, r.bg)) : rgba("#ffffff", Math.min(1, r.bg + 0.2)));
    ctx.restore();
  } else if (r.style === "neon") {
    fillRoundRect(ctx, left, top, w, lay.h, g.size * 0.45, rgba("#0a0818", r.bg));
    ctx.save();
    const edge = hl ? "#FFD400" : nameColor(user, false);
    ctx.shadowColor = rgba(edge, 0.9);
    ctx.shadowBlur = g.size * 0.55 * deviceScale(ctx);
    ctx.beginPath();
    roundRectPath(ctx, left, top, w, lay.h, g.size * 0.45);
    ctx.lineWidth = Math.max(2, g.size * 0.08);
    ctx.strokeStyle = rgba(edge);
    ctx.stroke();
    ctx.restore();
  } else if (hl) {
    // В колонке Twitch и в минимале выделенный ник — фиолетовая подсветка строки.
    const inset = r.style === "minimal" ? -g.size * 0.3 : g.size * 0.2;
    fillRoundRect(ctx, left + inset, top, w - inset * 2, lay.h, g.size * 0.3, rgba("#9146FF", 0.45));
    fillRoundRect(ctx, left + inset, top, g.size * 0.14, lay.h, g.size * 0.07, "#B98CFF");
  }

  const onLight = r.style === "bubbles" && !hl;
  const bodyColor = onLight ? "#16141f" : "#EFEFF1";
  const minimal = r.style === "minimal";
  const bf = bodyFont(r, g.size, def);
  const nf = nameFont(r, g.nameSize, def);
  ctx.textBaseline = "middle";
  ctx.textAlign = "left";
  setLetterSpacing(ctx, 0);
  if (minimal) {
    ctx.lineJoin = "round";
    ctx.lineWidth = g.size * 0.2;
    ctx.strokeStyle = "rgba(0,0,0,0.85)";
  }
  const rowY = (line: number) => {
    if (!g.nameLine) return top + g.padY + g.lineH * (line + 0.5);
    if (line === 0) return top + g.padY + g.nameSize * 0.65;
    return top + g.padY + g.nameSize * 1.3 + g.lineH * (line - 1 + 0.5);
  };
  for (const it of lay.items) {
    const x = left + g.padX + it.x;
    const cy = rowY(it.line);
    const a = it.atom;
    if (a.kind === "img") {
      const img = a.ref ? env.getImage(a.ref) : null;
      if (!img) continue;
      const h = a.role === "badge" ? g.badgeH : g.emoteH;
      drawImageLike(ctx, img, 0, 0, img.width, img.height, x, cy - h / 2, a.w, h);
      continue;
    }
    const isName = a.role === "name";
    ctx.font = isName ? nf : bf;
    const fill = isName ? (hl && r.style === "bubbles" ? "#ffffff" : nameColor(user, onLight)) : hl && r.style === "bubbles" ? "#ffffff" : bodyColor;
    if (minimal) ctx.strokeText(a.text, x, cy);
    ctx.fillStyle = rgba(fill);
    ctx.fillText(a.text, x, cy);
  }
}

export const chatRenderer: LayerRenderer<ChatLayer> = {
  multipart: true,
  measure(_ctx, layer) {
    const r = resolveChat(layer.props);
    return { w: r.w + r.fontSize, h: boxHeight(r) };
  },
  draw(ctx, layer, _st, env, frame) {
    if (!frame.chat.length) return;
    const { shown, r, stackH } = stackAt(ctx, layer, env, frame);
    if (!shown.length) return;
    const g = geoOf(r);
    const def = envFont(env);
    const left = -r.w / 2;
    const bottomY = boxHeight(r) / 2;

    // Колонка Twitch: одна подложка под всей стопкой, растёт вместе с ней;
    // уходящее наверх сообщение прячется под её край, а не висит над ней.
    if (r.style === "twitch-dark" && stackH > 1) {
      const pad = g.size * 0.35;
      const x = left - pad * 0.4;
      const y = bottomY - stackH - pad + g.gap;
      const w = r.w + pad * 0.8;
      const h = stackH + pad * 2 - g.gap;
      fillRoundRect(ctx, x, y, w, h, g.size * 0.5, rgba("#0e0e12", r.bg));
      clearShadow(ctx);
      ctx.beginPath();
      roundRectPath(ctx, x, y, w, h, g.size * 0.5);
      ctx.clip();
    }
    clearShadow(ctx);

    for (const s of shown) {
      // Новое сообщение въезжает снизу-слева и проявляется.
      const dy = (1 - s.a) * s.lay.h * 0.6;
      const dx = -(1 - s.a) * g.size * 1.2;
      const top = bottomY - s.bottom - s.lay.h + dy;
      ctx.save();
      ctx.globalAlpha *= s.alpha;
      if (dx) ctx.translate(dx, 0);
      drawMessage(ctx, s, r, env, def, left, top);
      ctx.restore();
    }
  },
};
