/**
 * Экраны «рабочего стола» для мультяшных сцен (канвас 2D, всё — функция t):
 *
 * - ``drawBrowser`` — окно браузера с вкладками (иконка-кружок + название, счётчик «(1)») и страницей-чатом
 *   ``chat``: шапка с именем собеседника, пузыри сообщений (пользователь справа, помощник слева), набор текста в
 *   поле ввода (``type`` с), «печатает…», длинный ответ, который «льётся» блоками (заголовки, строки, таблица),
 *   автопрокрутка к последнему. Активная вкладка — state ``tab`` монитора.
 * - ``PANEL_PAINTERS`` — картинки для облачков мыслей: ``editor`` (монтажка: превью со вспышкой, дорожки клипов,
 *   счётчик просмотров растёт), ``graph`` (график сайта уходит вверх, «+340%», монетки), ``text`` (крупная надпись).
 *   Нарисованы плоскими цветами с толстым контуром — под toon-обводку сцены. Новый рисунок — функция в словаре.
 *
 * Оформление — плоская тема (ui_flat.ts: те же токены и значки, что у плашек монтажа): по умолчанию светлая, акцент
 * — ``accent`` страницы; ``theme`` окна — red | dark | light | brand, ``toon`` — прежний вид с тёплой бумагой. Картинки
 * облачков остаются рисованными, плоские у них — счётчик просмотров и адресная строка (``theme`` в panelParams).
 *
 * Никаких чужих логотипов: иконка вкладки — цветная плашка с буквой, название — просто текст.
 */
import { UI_FONT, roundRect } from "./ui.ts";
import { ease } from "./timeline.ts";
import { MEME_PAINTERS } from "./meme_painters.ts";
import { drawFlatIcon, flatComponent, flatFont, flatPill, flatSize, isToon, rgbaOf, screenPalette, windowDots } from "./ui_flat.ts";

const font = (w: number, px: number) => `${w} ${Math.round(px)}px ${UI_FONT}, Arial, sans-serif`;
const clamp01 = (x: number) => (x < 0 ? 0 : x > 1 ? 1 : x);

export interface ChatBlock { h?: string; p?: string; table?: string[][]; hr?: boolean }
export interface ChatMessage {
  t: number;
  from: "user" | "bot";
  text?: string;
  /** Пользователь: столько секунд текст набирается в поле ввода до отправки (сообщение появляется в t + type). */
  type?: number;
  /** Помощник: «печатает…» столько секунд до ответа. */
  think?: number;
  /** Длинный ответ блоками, льётся за stream с. */
  blocks?: ChatBlock[];
  stream?: number;
  /** Картинка-вложение: рисунок из PANEL_PAINTERS (``panelParams``), высота ``panelH`` (пиксели при scale 1). */
  panel?: string;
  panelParams?: Record<string, any>;
  panelH?: number;
}
export interface ChatPage { kind?: "chat"; name: string; accent?: string; bg?: string; bubble?: string; botBubble?: string;
  letter?: string; messages: ChatMessage[]; placeholder?: string;
  /** Плоская тема страницы (red | dark | light | brand; toon — прежний мультяшный вид); подпись под именем. */
  theme?: string; status?: string }
/** url — адресная строка под вкладками (плоская тема). */
export interface BrowserTab { title: string; icon?: string; letter?: string; url?: string; page: ChatPage }
/** theme — плоская тема окна (по умолчанию light, акцент — accent страницы); "toon" — прежний вид (chrome — цвет рамки). */
export interface BrowserSpec { tabs: BrowserTab[]; chrome?: string; scale?: number; theme?: string }

function wrapLines(g: CanvasRenderingContext2D, text: string, max: number): string[] {
  const out: string[] = [];
  for (const para of text.split("\n")) {
    let line = "";
    for (const w of para.split(/\s+/)) {
      const cand = line ? `${line} ${w}` : w;
      if (g.measureText(cand).width > max && line) {
        out.push(line);
        line = w;
      } else line = cand;
    }
    out.push(line);
  }
  return out;
}

/**
 * Оформление окна: плоская тема (по умолчанию светлая, акцент — `accent` страницы) или прежний мультяшный вид
 * (`theme: "toon"` — тёплая бумага и контур под toon-обводку сцены).
 */
interface Look {
  toon: boolean;
  chrome: string;
  bg: string;
  ink: string;
  muted: string;
  faint: string;
  line: string;
  head: string;
  me: string;
  meInk: string;
  bot: string;
  botInk: string;
  input: string;
  inputLine: string;
  accent: string;
  accentInk: string;
  tableHead: string;
  font: (w: number, px: number) => string;
}

function lookOf(page: ChatPage, theme: unknown, chrome?: string): Look {
  if (isToon(theme)) {
    return {
      toon: true, chrome: chrome || "#e6dccd", bg: page.bg || "#fbf7f1", ink: "#2b1d16", muted: "rgba(43,29,22,0.6)",
      faint: "rgba(43,29,22,0.38)", line: "rgba(43,29,22,0.18)", head: "rgba(0,0,0,0.04)", me: page.bubble || "#f6cdb6",
      meInk: "#2b1d16", bot: page.botBubble || "#efe6da", botInk: "#2b1d16", input: "#ffffff", inputLine: "rgba(43,29,22,0.18)",
      accent: page.accent || "#d9774b", accentInk: "#ffffff", tableHead: "rgba(217,119,75,0.18)", font,
    };
  }
  const pal = screenPalette(theme, "light", page.accent);
  return {
    toon: false, chrome: pal.surface3, bg: rgbaOf(pal.surface, 1), ink: pal.text, muted: pal.muted, faint: pal.faint,
    line: pal.border, head: pal.surface, me: pal.accent, meInk: pal.accentText, bot: pal.surface2, botInk: pal.text,
    input: pal.surface2, inputLine: pal.border, accent: pal.accent, accentInk: pal.accentText, tableHead: pal.accentSoft,
    font: (w: number, px: number) => flatFont("body", w, px),
  };
}

/** Высота блока ответа и отрисовка (draw=false — только замер). */
function blockLayout(g: CanvasRenderingContext2D, b: ChatBlock, x: number, y: number, w: number, s: number, draw: boolean, L: Look): number {
  if (b.hr) {
    if (draw) {
      g.fillStyle = rgbaOf(L.line);
      g.fillRect(x, y + 10 * s, w, 3 * s);
    }
    return 24 * s;
  }
  if (b.table) {
    const rows = b.table, cols = rows[0]?.length || 1, rh = 50 * s, cw = w / cols;
    if (draw) {
      g.lineWidth = (L.toon ? 3 : 2) * s;
      g.strokeStyle = L.toon ? "rgba(60,40,30,0.35)" : rgbaOf(L.line);
      rows.forEach((r, i) => {
        if (i === 0) {
          g.fillStyle = rgbaOf(L.tableHead);
          g.fillRect(x, y, w, rh);
        }
        r.forEach((c, j) => {
          g.fillStyle = rgbaOf(L.ink);
          g.font = L.font(i === 0 ? 800 : 600, 30 * s);
          g.textAlign = "center";
          g.fillText(c, x + cw * (j + 0.5), y + i * rh + rh * 0.68);
        });
      });
      g.textAlign = "left";
      for (let i = 0; i <= rows.length; i++) {
        g.beginPath();
        g.moveTo(x, y + i * rh);
        g.lineTo(x + w, y + i * rh);
        g.stroke();
      }
      for (let j = 0; j <= cols; j++) {
        g.beginPath();
        g.moveTo(x + j * cw, y);
        g.lineTo(x + j * cw, y + rows.length * rh);
        g.stroke();
      }
    }
    return rows.length * rh + 16 * s;
  }
  const head = !!b.h;
  g.font = L.font(head ? 800 : 500, (head ? 34 : 30) * s);
  const lines = wrapLines(g, b.h || b.p || "", w);
  const lh = (head ? 46 : 42) * s;
  if (draw) {
    g.fillStyle = rgbaOf(L.ink);
    lines.forEach((l, i) => g.fillText(l, x, y + lh * (i + 0.78)));
  }
  return lines.length * lh + 6 * s;
}

/** Окно браузера с вкладками и чатом активной вкладки. */
export function drawBrowser(g: CanvasRenderingContext2D, W: number, H: number, b: BrowserSpec, t: number, active: number,
  badge?: { tab: number; text: string; t: number } | null) {
  const s = (b.scale ?? 1) * (W / 1280);
  const cur = b.tabs[Math.max(0, Math.min(b.tabs.length - 1, active))];
  const L = lookOf(cur?.page || { name: "", messages: [] }, b.theme, b.chrome);
  g.save();
  g.fillStyle = rgbaOf(L.chrome);
  g.fillRect(0, 0, W, H);
  // Плоская тема: «светофор» окна слева, вкладки за ним; мультяшный вид — вкладки от края.
  const tabH = 78 * s;
  const x0 = L.toon ? 18 * s : 140 * s;
  if (!L.toon) windowDots(g, 36 * s, 12 * s + tabH * 0.47, 10 * s, 32 * s);
  const tabW = Math.min(420 * s, (W - x0 - 22 * s) / Math.max(1, b.tabs.length));
  b.tabs.forEach((tab, i) => {
    const x = x0 + i * (tabW + 8 * s), y = 12 * s;
    const on = i === active;
    const TL = lookOf(tab.page, b.theme, b.chrome);
    if (L.toon) {
      g.fillStyle = on ? (tab.page.bg || "#fbf7f1") : "rgba(255,255,255,0.35)";
      roundRect(g, x, y, tabW, tabH, 18 * s);
      g.fill();
    } else if (on) {
      // Активная вкладка сливается со страницей: скругление сверху, низ — в страницу.
      g.fillStyle = rgbaOf(L.bg);
      roundRect(g, x, y, tabW, tabH + 30 * s, 20 * s);
      g.fill();
    } else if (i !== active - 1) {
      g.fillStyle = rgbaOf(L.line);
      g.fillRect(x + tabW + 3 * s, y + tabH * 0.25, 2 * s, tabH * 0.5);
    }
    const ix = x + 38 * s, iy = y + tabH * 0.47;
    const color = tab.icon || TL.accent;
    if (L.toon) {
      g.fillStyle = color;
      g.beginPath();
      g.arc(ix, iy, 17 * s, 0, Math.PI * 2);
      g.fill();
    } else {
      g.fillStyle = rgbaOf(color);
      roundRect(g, ix - 17 * s, iy - 17 * s, 34 * s, 34 * s, 9 * s);
      g.fill();
    }
    if (tab.letter) {
      g.fillStyle = "#ffffff";
      g.font = L.toon ? font(900, 20 * s) : flatFont("display", 900, 20 * s);
      g.textAlign = "center";
      g.fillText(tab.letter, ix, iy + 7 * s);
      g.textAlign = "left";
    }
    const hasBadge = !!(badge && badge.tab === i && t >= badge.t);
    let title = tab.title;
    if (hasBadge && L.toon) title = `${badge!.text} ${title}`;
    g.fillStyle = rgbaOf(on ? L.ink : L.muted);
    g.font = L.font(on ? 800 : 700, 30 * s);
    g.fillText(title, x + 66 * s, y + tabH * 0.47 + 10 * s);
    if (hasBadge) {
      const k = ease("outBack", clamp01((t - badge!.t) / 0.3));
      if (L.toon) {
        g.fillStyle = "#ff4b3e";
        g.beginPath();
        g.arc(x + tabW - 30 * s, y + tabH * 0.47, 11 * s * k, 0, Math.PI * 2);
        g.fill();
      } else {
        // Счётчик непрочитанного — красная пилюля с числом у правого края вкладки.
        const num = badge!.text.replace(/[()]/g, "").trim() || "1";
        g.save();
        g.translate(x + tabW - 36 * s, y + tabH * 0.47);
        g.scale(k, k);
        g.font = flatFont("display", 900, 22 * s);
        const bw = Math.max(34 * s, g.measureText(num).width + 22 * s);
        g.fillStyle = "#ff3b30";
        roundRect(g, -bw / 2, -17 * s, bw, 34 * s, 17 * s);
        g.fill();
        g.fillStyle = "#ffffff";
        g.textAlign = "center";
        g.fillText(num, 0, 8 * s);
        g.restore();
      }
    }
  });
  if (cur) {
    let top = tabH + 12 * s;
    if (!L.toon && cur.url) {
      // Адресная строка: замок и адрес в серой пилюле.
      const bh = 58 * s, by = top + 10 * s;
      g.fillStyle = rgbaOf(L.bg);
      g.fillRect(0, top, W, bh + 20 * s);
      g.fillStyle = rgbaOf(L.input);
      roundRect(g, 24 * s, by, W - 48 * s, bh, bh / 2);
      g.fill();
      drawFlatIcon(g, "lock", 58 * s, by + bh / 2, 26 * s, rgbaOf(L.muted), 2.2);
      g.fillStyle = rgbaOf(L.muted);
      g.font = L.font(500, 28 * s);
      g.fillText(cur.url, 84 * s, by + bh / 2 + 10 * s);
      top += bh + 20 * s;
      g.fillStyle = rgbaOf(L.line);
      g.fillRect(0, top - 2 * s, W, 2 * s);
    }
    drawChat(g, 0, top, W, H - top, cur.page, t, s, b.theme);
  }
  g.restore();
}

/** Страница-чат в прямоугольнике. */
export function drawChat(g: CanvasRenderingContext2D, X: number, Y: number, W: number, H: number, page: ChatPage, t: number, s: number,
  theme?: unknown) {
  const L = lookOf(page, theme ?? page.theme);
  const ink = L.ink;
  g.save();
  g.beginPath();
  g.rect(X, Y, W, H);
  g.clip();
  g.fillStyle = rgbaOf(L.bg);
  g.fillRect(X, Y, W, H);
  // Шапка: кружок собеседника, имя; в плоской теме — подпись «в сети» и тонкий разделитель.
  const headH = 84 * s;
  g.fillStyle = rgbaOf(L.head);
  g.fillRect(X, Y, W, headH);
  if (!L.toon) {
    g.fillStyle = rgbaOf(L.line);
    g.fillRect(X, Y + headH - 2 * s, W, 2 * s);
  }
  g.fillStyle = rgbaOf(L.accent);
  g.beginPath();
  g.arc(X + 56 * s, Y + headH / 2, 24 * s, 0, Math.PI * 2);
  g.fill();
  if (page.letter) {
    g.fillStyle = rgbaOf(L.accentInk);
    g.font = L.toon ? font(900, 26 * s) : flatFont("display", 900, 24 * s);
    g.textAlign = "center";
    g.fillText(page.letter, X + 56 * s, Y + headH / 2 + 9 * s);
    g.textAlign = "left";
  }
  g.fillStyle = rgbaOf(ink);
  if (L.toon) {
    g.font = font(900, 44 * s);
    g.fillText(page.name, X + 96 * s, Y + headH / 2 + 15 * s);
  } else {
    g.font = L.font(800, 36 * s);
    g.fillText(page.name, X + 96 * s, Y + headH / 2 + 2 * s);
    g.fillStyle = rgbaOf(L.muted);
    g.font = L.font(500, 22 * s);
    g.fillText(page.status ?? "в сети", X + 96 * s, Y + headH / 2 + 28 * s);
  }
  // Поле ввода внизу: набор текста пользователем перед отправкой.
  const inH = 96 * s, inY = Y + H - inH - 22 * s;
  let typing: ChatMessage | null = null;
  for (const m of page.messages) if (m.from === "user" && m.type && t >= m.t && t < m.t + m.type) typing = m;
  g.fillStyle = rgbaOf(L.input);
  roundRect(g, X + 30 * s, inY, W - 60 * s, inH, (L.toon ? 30 : 48) * s);
  g.fill();
  g.lineWidth = (L.toon ? 3 : 2) * s;
  g.strokeStyle = rgbaOf(L.inputLine);
  g.stroke();
  if (!L.toon) {
    // Кнопка «отправить» — кружок акцентом со стрелкой.
    const r = inH * 0.34, bx = X + W - 30 * s - inH / 2, byc = inY + inH / 2;
    g.fillStyle = rgbaOf(typing ? L.accent : L.faint);
    g.beginPath();
    g.arc(bx, byc, r, 0, Math.PI * 2);
    g.fill();
    drawFlatIcon(g, "arrow-right", bx, byc, r * 1.15, rgbaOf(L.accentInk), 2.6);
  }
  g.font = L.font(600, 34 * s);
  if (typing) {
    const k = clamp01((t - typing.t) / typing.type!);
    const txt = (typing.text || "").slice(0, Math.ceil((typing.text || "").length * k));
    g.fillStyle = rgbaOf(ink);
    const shown = wrapLines(g, txt, W - 200 * s);
    const last = shown[shown.length - 1] || "";
    g.fillText(last, X + 62 * s, inY + inH * 0.62);
    if (Math.floor(t * 3) % 2 === 0) {
      g.fillStyle = rgbaOf(L.toon ? ink : L.accent);
      g.fillRect(X + 64 * s + g.measureText(last).width, inY + inH * 0.3, 4 * s, inH * 0.42);
    }
  } else {
    g.fillStyle = rgbaOf(L.faint);
    g.fillText(page.placeholder || "Сообщение…", X + 62 * s, inY + inH * 0.62);
  }
  // Сообщения: сначала замер всей высоты, потом прокрутка к последнему.
  const areaTop = Y + headH + 20 * s, areaBot = inY - 18 * s;
  const maxW = W * 0.72;
  type Item = { m: ChatMessage; h: number; kind: "bubble" | "thinking" | "answer" | "image"; lines?: string[]; blocks?: ChatBlock[] };
  const items: Item[] = [];
  for (const m of page.messages) {
    const shownAt = m.t + (m.from === "user" ? m.type || 0 : m.think || 0);
    if (m.from === "bot" && m.think && t >= m.t && t < shownAt) {
      items.push({ m, h: 92 * s, kind: "thinking" });
      continue;
    }
    if (t < shownAt) continue;
    if (m.panel) {
      items.push({ m, h: (m.panelH ?? 300) * s, kind: "image" });
    } else if (m.blocks) {
      const k = m.stream ? clamp01((t - shownAt) / m.stream) : 1;
      const n = Math.max(1, Math.ceil(m.blocks.length * k));
      const bl = m.blocks.slice(0, n);
      let h = 40 * s;
      for (const b of bl) h += blockLayout(g, b, 0, 0, maxW - 60 * s, s, false, L);
      items.push({ m, h, kind: "answer", blocks: bl });
    } else {
      g.font = L.font(m.from === "user" ? 700 : 600, (m.from === "user" ? 44 : 38) * s);
      const lines = wrapLines(g, m.text || "", maxW - 64 * s);
      const lh = (m.from === "user" ? 58 : 50) * s;
      items.push({ m, h: lines.length * lh + 44 * s, kind: "bubble", lines });
    }
  }
  const gap = 22 * s;
  const total = items.reduce((a, it) => a + it.h + gap, 0);
  const scroll = Math.max(0, total - (areaBot - areaTop));
  g.save();
  g.beginPath();
  g.rect(X, areaTop - 8 * s, W, areaBot - areaTop + 8 * s);
  g.clip();
  let y = areaTop - scroll;
  for (const it of items) {
    const m = it.m;
    const user = m.from === "user";
    const appear = m.t + (user ? m.type || 0 : m.think || 0);
    const pop = it.kind === "thinking" ? 1 : ease("outBack", clamp01((t - appear) / 0.28));
    let bw = maxW;
    if (it.kind === "bubble") {
      g.font = L.font(user ? 700 : 600, (user ? 44 : 38) * s);
      bw = Math.min(maxW, Math.max(...it.lines!.map((l) => g.measureText(l).width)) + 64 * s);
    } else if (it.kind === "thinking") bw = 170 * s;
    else if (it.kind === "image") bw = Math.min(maxW, it.h * 1.6);
    const bx = user ? X + W - 34 * s - bw : X + 34 * s;
    g.save();
    const ox = user ? bx + bw : bx;
    g.translate(ox, y + it.h / 2);
    g.scale(0.6 + 0.4 * pop, 0.6 + 0.4 * pop);
    g.globalAlpha = clamp01(pop * 1.4);
    g.translate(-ox, -(y + it.h / 2));
    g.fillStyle = rgbaOf(user ? L.me : L.bot);
    roundRect(g, bx, y, bw, it.h, (L.toon ? 30 : 32) * s);
    g.fill();
    g.fillStyle = rgbaOf(user ? L.meInk : L.botInk);
    if (it.kind === "thinking") {
      for (let i = 0; i < 3; i++) {
        const k = 0.5 + 0.5 * Math.sin(t * 10 - i * 1.1);
        g.globalAlpha = 0.35 + 0.65 * k;
        g.fillStyle = rgbaOf(L.toon ? ink : L.muted);
        g.beginPath();
        g.arc(bx + 50 * s + i * 36 * s, y + it.h / 2 - k * 6 * s, 12 * s, 0, Math.PI * 2);
        g.fill();
      }
    } else if (it.kind === "image") {
      // Вложение: рисунок в скруглённой рамке (у рисунка свои координаты 0..w, 0..h).
      const pad = 10 * s;
      g.save();
      roundRect(g, bx + pad, y + pad, bw - pad * 2, it.h - pad * 2, 22 * s);
      g.clip();
      g.translate(bx + pad, y + pad);
      const painter = PANEL_PAINTERS[m.panel || "text"] || PANEL_PAINTERS.text;
      painter(g, bw - pad * 2, it.h - pad * 2, Math.max(0, t - appear), { ...(m.panelParams || {}), theme: m.panelParams?.theme ?? (L.toon ? "toon" : undefined) });
      g.restore();
    } else if (it.kind === "bubble") {
      g.font = L.font(user ? 700 : 600, (user ? 44 : 38) * s);
      const lh = (user ? 58 : 50) * s;
      it.lines!.forEach((l, i) => g.fillText(l, bx + 32 * s, y + 22 * s + lh * (i + 0.78)));
    } else {
      let yy = y + 20 * s;
      for (const b of it.blocks!) yy += blockLayout(g, b, bx + 30 * s, yy, bw - 60 * s, s, true, L);
    }
    g.restore();
    y += it.h + gap;
  }
  g.restore();
  g.restore();
}

/* ---------------------------------------------------------------- картинки облачков */

type Painter = (g: CanvasRenderingContext2D, W: number, H: number, t: number, p: Record<string, any>) => void;

const INK = "#2b1810";

function inkRect(g: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number, fill: string, lw: number) {
  roundRect(g, x, y, w, h, r);
  g.fillStyle = fill;
  g.fill();
  g.lineWidth = lw;
  g.strokeStyle = INK;
  g.stroke();
}

function star(g: CanvasRenderingContext2D, cx: number, cy: number, r: number, n: number, rot: number) {
  g.beginPath();
  for (let i = 0; i < n * 2; i++) {
    const a = rot + (i * Math.PI) / n;
    const rr = i % 2 ? r * 0.45 : r;
    const x = cx + Math.cos(a) * rr, y = cy + Math.sin(a) * rr;
    if (i) g.lineTo(x, y);
    else g.moveTo(x, y);
  }
  g.closePath();
}

const fmt = (n: number) => Math.round(n).toLocaleString("ru-RU").replace(/ /g, " ");

export const PANEL_PAINTERS: Record<string, Painter> = {
  /** Монтажка: превью со вспышкой и прыгающим человечком, дорожки клипов, плейхед, счётчик просмотров. */
  editor(g, W, H, t, p) {
    const lw = W * 0.012;
    g.fillStyle = p.bg || "#fff6e8";
    g.fillRect(0, 0, W, H);
    // Превью.
    const px = W * 0.08, py = H * 0.07, pw = W * 0.84, ph = H * 0.5;
    inkRect(g, px, py, pw, ph, W * 0.03, "#3b2a4a", lw);
    g.save();
    roundRect(g, px, py, pw, ph, W * 0.03);
    g.clip();
    const k = (t * 1.3) % 1;
    g.fillStyle = "#ffb347";
    star(g, px + pw * 0.62, py + ph * 0.5, ph * (0.42 + 0.08 * Math.sin(t * 9)), 9, t * 1.5);
    g.fill();
    g.fillStyle = "#ffe066";
    star(g, px + pw * 0.62, py + ph * 0.5, ph * (0.24 + 0.05 * Math.sin(t * 11)), 7, -t * 2);
    g.fill();
    // Человечек в прыжке (силуэт).
    const jy = py + ph * (0.62 - 0.22 * Math.abs(Math.sin(t * 4)));
    g.fillStyle = "#fff6e8";
    g.beginPath();
    g.arc(px + pw * 0.3, jy - ph * 0.2, ph * 0.08, 0, Math.PI * 2);
    g.fill();
    g.lineWidth = lw * 1.6;
    g.strokeStyle = "#fff6e8";
    g.lineCap = "round";
    g.beginPath();
    g.moveTo(px + pw * 0.3, jy - ph * 0.12);
    g.lineTo(px + pw * 0.3, jy + ph * 0.05);
    g.moveTo(px + pw * 0.3, jy - ph * 0.08);
    g.lineTo(px + pw * 0.22, jy - ph * 0.2);
    g.moveTo(px + pw * 0.3, jy - ph * 0.08);
    g.lineTo(px + pw * 0.38, jy - ph * 0.2);
    g.moveTo(px + pw * 0.3, jy + ph * 0.05);
    g.lineTo(px + pw * 0.24, jy + ph * 0.16);
    g.moveTo(px + pw * 0.3, jy + ph * 0.05);
    g.lineTo(px + pw * 0.36, jy + ph * 0.16);
    g.stroke();
    g.restore();
    // Дорожки клипов.
    const cols = ["#ff7a59", "#4fb3bf", "#ffc857", "#9b7bd4", "#7bc96f"];
    for (let r = 0; r < 3; r++) {
      let x = px - ((t * 60 + r * 37) % 80);
      const y = py + ph + H * 0.06 + r * H * 0.1;
      let n = 0;
      while (x < px + pw) {
        const w = W * (0.12 + ((n * 7 + r * 3) % 5) * 0.03);
        inkRect(g, x, y, w - W * 0.015, H * 0.075, W * 0.015, cols[(n + r * 2) % cols.length], lw * 0.7);
        x += w;
        n++;
      }
    }
    // Плейхед.
    const hx = px + pw * (0.2 + 0.6 * ((t * 0.35) % 1));
    g.fillStyle = "#ff4b3e";
    g.fillRect(hx - lw / 2, py + ph + H * 0.04, lw, H * 0.33);
    void k;
    // Счётчик просмотров: плоская пилюля темы (как слой ui counter) или прежняя мультяшная плашка.
    const prog = ease("outCubic", clamp01(t / (p.dur ?? 2.2)));
    const views = (p.from ?? 120000) + ((p.to ?? 1000000) - (p.from ?? 120000)) * prog;
    if (isToon(p.theme)) {
      const txt = `${fmt(views)} просмотров`;
      g.font = font(900, H * 0.075);
      const tw = g.measureText(txt).width;
      inkRect(g, W - tw - W * 0.17, py + H * 0.025, tw + W * 0.08, H * 0.11, H * 0.05, "#ff4b3e", lw * 0.8);
      g.fillStyle = "#fff";
      g.fillText(txt, W - tw - W * 0.13, py + H * 0.105);
    } else {
      const props = { component: "counter" as const, theme: p.theme ?? "red", accent: p.accent ?? null, from: p.from ?? 120000, value: p.to ?? 1000000,
        unit: p.unit ?? "просмотров", icon: "eye", variant: "accent", size: 58 };
      const k = (H * 0.15) / (58 * 1.8);
      const box = flatSize(g, props, k);
      const kk = box.w > pw * 1.02 ? (k * pw * 1.02) / box.w : k;
      flatComponent(g, props, px + pw - (box.w * kk) / k / 2 + W * 0.02, py + H * 0.02, kk, 9, prog);
    }
  },
  /** Сайт: линия графика растёт и уходит за край, «+340%», монетки. */
  graph(g, W, H, t, p) {
    const lw = W * 0.012;
    g.fillStyle = p.bg || "#f1f8ef";
    g.fillRect(0, 0, W, H);
    const x0 = W * 0.1, y0 = H * 0.88, x1 = W * 0.92, y1 = H * 0.12;
    // Сетка.
    g.strokeStyle = "rgba(43,24,16,0.12)";
    g.lineWidth = lw * 0.5;
    for (let i = 0; i < 5; i++) {
      g.beginPath();
      g.moveTo(x0, y0 - (i * (y0 - y1)) / 4);
      g.lineTo(x1, y0 - (i * (y0 - y1)) / 4);
      g.stroke();
    }
    const k = ease("inOutSine", clamp01(t / (p.dur ?? 2.0)));
    const pts: number[][] = [];
    const N = 40;
    for (let i = 0; i <= N * k; i++) {
      const u = i / N;
      const v = Math.pow(u, 2.6) * 0.95 + 0.04 * Math.sin(u * 19) * (1 - u) + 0.05;
      pts.push([x0 + (x1 - x0) * u, y0 - (y0 - y1) * v]);
    }
    if (pts.length > 1) {
      g.fillStyle = "rgba(79,191,111,0.28)";
      g.beginPath();
      g.moveTo(pts[0][0], y0);
      for (const q of pts) g.lineTo(q[0], q[1]);
      g.lineTo(pts[pts.length - 1][0], y0);
      g.closePath();
      g.fill();
      g.lineCap = "round";
      g.lineJoin = "round";
      g.strokeStyle = INK;
      g.lineWidth = lw * 2.6;
      g.beginPath();
      pts.forEach((q, i) => (i ? g.lineTo(q[0], q[1]) : g.moveTo(q[0], q[1])));
      g.stroke();
      g.strokeStyle = "#3fbf6a";
      g.lineWidth = lw * 1.5;
      g.stroke();
      // Стрелка на конце.
      const a = pts[pts.length - 1], b = pts[Math.max(0, pts.length - 3)];
      const ang = Math.atan2(a[1] - b[1], a[0] - b[0]);
      g.fillStyle = "#3fbf6a";
      g.strokeStyle = INK;
      g.lineWidth = lw * 0.8;
      g.beginPath();
      g.moveTo(a[0] + Math.cos(ang) * W * 0.06, a[1] + Math.sin(ang) * W * 0.06);
      g.lineTo(a[0] + Math.cos(ang + 2.4) * W * 0.05, a[1] + Math.sin(ang + 2.4) * W * 0.05);
      g.lineTo(a[0] + Math.cos(ang - 2.4) * W * 0.05, a[1] + Math.sin(ang - 2.4) * W * 0.05);
      g.closePath();
      g.fill();
      g.stroke();
    }
    // Монетки взлетают.
    for (let i = 0; i < 4; i++) {
      const ph = (t * 0.9 + i * 0.27) % 1;
      const cx = W * (0.2 + i * 0.16), cy = y0 - ph * H * 0.6;
      g.globalAlpha = 1 - ph * 0.7;
      g.fillStyle = "#ffc857";
      g.beginPath();
      g.ellipse(cx, cy, W * 0.035 * Math.abs(Math.cos(t * 5 + i)), W * 0.035, 0, 0, Math.PI * 2);
      g.fill();
      g.lineWidth = lw * 0.6;
      g.strokeStyle = INK;
      g.stroke();
    }
    g.globalAlpha = 1;
    const pct = Math.round((p.to ?? 340) * k);
    const txt = `+${pct}%`;
    g.font = font(900, H * 0.14);
    const tw = g.measureText(txt).width;
    inkRect(g, W * 0.08, H * 0.08, tw + W * 0.08, H * 0.18, H * 0.06, "#3fbf6a", lw * 0.8);
    g.fillStyle = "#fff";
    g.fillText(txt, W * 0.12, H * 0.215);
    if (p.label) {
      // Подпись графика («ПРОДАЖИ»): тёмная плашка справа сверху.
      g.font = font(900, H * 0.1);
      const lw2 = g.measureText(String(p.label)).width;
      inkRect(g, W * 0.92 - lw2 - W * 0.06, H * 0.09, lw2 + W * 0.06, H * 0.15, H * 0.05, INK, lw * 0.6);
      g.fillStyle = "#fff6e8";
      g.fillText(String(p.label), W * 0.92 - lw2 - W * 0.03, H * 0.2);
    }
  },
  /** Сайт запущен: окно браузера, ракета взлетает с дымом, «ОНЛАЙН», конфетти. */
  launch(g, W, H, t, p) {
    const lw = W * 0.012;
    g.fillStyle = p.bg || "#e9f1ff";
    g.fillRect(0, 0, W, H);
    // Полоска браузера: точки и адрес (плоская тема — как окно слоя ui; toon — мультяшная).
    const pop = ease("outBack", clamp01((t - 0.3) / 0.35));
    if (!isToon(p.theme)) {
      const pal = screenPalette(p.theme, "light", p.accent);
      g.fillStyle = rgbaOf(pal.surface3);
      g.fillRect(0, 0, W, H * 0.15);
      g.fillStyle = rgbaOf(pal.border);
      g.fillRect(0, H * 0.15 - 2, W, 2);
      windowDots(g, W * 0.05, H * 0.075, H * 0.022, H * 0.065);
      const ax = W * 0.2, ah = H * 0.09, ay = H * 0.03, aw = W * 0.46;
      g.fillStyle = rgbaOf(pal.surface2);
      roundRect(g, ax, ay, aw, ah, ah / 2);
      g.fill();
      drawFlatIcon(g, "lock", ax + ah * 0.55, ay + ah / 2, ah * 0.5, rgbaOf(pal.muted), 2.2);
      g.fillStyle = rgbaOf(pal.muted);
      g.font = flatFont("body", 500, ah * 0.62);
      g.fillText(String(p.url ?? "мой-сайт.рф"), ax + ah * 1.0, ay + ah / 2 + ah * 0.22);
      if (pop > 0) {
        g.save();
        g.translate(W * 0.83, H * 0.075);
        g.scale(pop, pop);
        const ph = H * 0.1;
        g.font = flatFont("display", 900, ph * 0.5);
        const pwid = g.measureText("ОНЛАЙН").width + ph * 0.84 + ph * 0.42;
        flatPill(g, -pwid / 2, -ph / 2, ph, "ОНЛАЙН", "#16a34a", "#ffffff", Math.floor(t * 2) % 2 === 0);
        g.restore();
      }
    } else {
    inkRect(g, -lw, -lw, W + lw * 2, H * 0.15 + lw, 0, "#fff6e8", lw);
    ["#ff7a59", "#ffc857", "#7bc96f"].forEach((c, i) => {
      g.fillStyle = c;
      g.beginPath();
      g.arc(W * (0.05 + i * 0.045), H * 0.075, H * 0.03, 0, Math.PI * 2);
      g.fill();
    });
    inkRect(g, W * 0.2, H * 0.03, W * 0.44, H * 0.09, H * 0.045, "#ffffff", lw * 0.5);
    g.fillStyle = "rgba(43,24,16,0.6)";
    g.font = font(700, H * 0.06);
    g.fillText(String(p.url ?? "мой-сайт.рф"), W * 0.23, H * 0.095);
    }
    // «ОНЛАЙН» с мигающей точкой.
    if (pop > 0 && isToon(p.theme)) {
      g.save();
      g.translate(W * 0.83, H * 0.075);
      g.scale(pop, pop);
      g.font = font(900, H * 0.065);
      const tw = g.measureText("ОНЛАЙН").width;
      inkRect(g, -tw / 2 - W * 0.05, -H * 0.05, tw + W * 0.08, H * 0.1, H * 0.05, "#3fbf6a", lw * 0.6);
      g.fillStyle = Math.floor(t * 3) % 2 ? "#ffffff" : "#d8ffe2";
      g.beginPath();
      g.arc(-tw / 2 - W * 0.018, 0, H * 0.018, 0, Math.PI * 2);
      g.fill();
      g.fillStyle = "#fff";
      g.fillText("ОНЛАЙН", -tw / 2 + W * 0.005, H * 0.024);
      g.restore();
    }
    // Ракета: взлёт по ease, лёгкая тряска, пламя мерцает, дым клубами.
    const k = ease("inQuad", clamp01(t / (p.dur ?? 2.4)));
    const rx = W * 0.5 + Math.sin(t * 40) * W * 0.004, ry = H * (0.78 - 0.55 * k);
    for (let i = 0; i < 7; i++) {
      const u = (t * 1.6 + i / 7) % 1;
      g.globalAlpha = 0.85 * (1 - u);
      g.fillStyle = "#ffffff";
      g.beginPath();
      g.arc(rx + Math.sin(i * 2.3) * W * 0.12 * u, Math.min(H * 0.98, ry + H * 0.2 + u * H * 0.25), W * (0.03 + 0.05 * u), 0, Math.PI * 2);
      g.fill();
    }
    g.globalAlpha = 1;
    const rs = H * 0.2;
    g.fillStyle = Math.floor(t * 14) % 2 ? "#ffc857" : "#ff7a59";
    g.beginPath();
    g.moveTo(rx - rs * 0.22, ry + rs * 0.55);
    g.quadraticCurveTo(rx, ry + rs * (1.25 + 0.15 * Math.sin(t * 30)), rx + rs * 0.22, ry + rs * 0.55);
    g.closePath();
    g.fill();
    g.lineWidth = lw;
    g.strokeStyle = INK;
    g.fillStyle = "#ff7a59";
    for (const sx of [-1, 1]) {
      g.beginPath();
      g.moveTo(rx + sx * rs * 0.25, ry + rs * 0.1);
      g.lineTo(rx + sx * rs * 0.55, ry + rs * 0.6);
      g.lineTo(rx + sx * rs * 0.22, ry + rs * 0.5);
      g.closePath();
      g.fill();
      g.stroke();
    }
    g.fillStyle = "#fff6e8";
    g.beginPath();
    g.moveTo(rx, ry - rs * 0.8);
    g.quadraticCurveTo(rx + rs * 0.36, ry - rs * 0.35, rx + rs * 0.28, ry + rs * 0.55);
    g.lineTo(rx - rs * 0.28, ry + rs * 0.55);
    g.quadraticCurveTo(rx - rs * 0.36, ry - rs * 0.35, rx, ry - rs * 0.8);
    g.closePath();
    g.fill();
    g.stroke();
    g.fillStyle = "#4fb3bf";
    g.beginPath();
    g.arc(rx, ry - rs * 0.1, rs * 0.14, 0, Math.PI * 2);
    g.fill();
    g.stroke();
    // Конфетти.
    const cols = ["#ff4b3e", "#ffc857", "#4fb3bf", "#9b7bd4", "#7bc96f"];
    for (let i = 0; i < 18; i++) {
      const u = (t * 0.5 + (i * 0.618) % 1) % 1;
      const x = W * ((i * 0.37) % 1), y = H * (0.15 + u * 0.85);
      g.save();
      g.translate(x, y);
      g.rotate(t * 4 + i);
      g.fillStyle = cols[i % cols.length];
      g.fillRect(-W * 0.012, -H * 0.012, W * 0.024, H * 0.03);
      g.restore();
    }
  },
  /** Фури-пазл: лисья мордочка на пазле, одна деталь выпала, наклейка «1000 деталей». */
  furpuzzle(g, W, H, t, p) {
    const lw = Math.max(2, W * 0.01);
    g.fillStyle = p.bg || "#d9c8ff";
    g.fillRect(0, 0, W, H);
    // Фон: сердечки в шахматку.
    g.fillStyle = "rgba(255,255,255,0.45)";
    for (let y = 0; y < 5; y++) for (let x = 0; x < 7; x++) {
      if ((x + y) % 2) continue;
      const cx = (x + 0.5) * W / 7, cy = (y + 0.5) * H / 5, r = H * 0.035;
      g.beginPath();
      g.arc(cx - r * 0.5, cy, r * 0.55, Math.PI, 0);
      g.arc(cx + r * 0.5, cy, r * 0.55, Math.PI, 0);
      g.lineTo(cx, cy + r * 1.1);
      g.closePath();
      g.fill();
    }
    // Мордочка лиса.
    const cx = W * 0.5, cy = H * 0.56, r = H * 0.34;
    g.lineWidth = lw;
    g.strokeStyle = INK;
    for (const s of [-1, 1]) {
      g.fillStyle = "#ff8f3d";
      g.beginPath();
      g.moveTo(cx + s * r * 0.25, cy - r * 0.75);
      g.lineTo(cx + s * r * 0.95, cy - r * 1.35);
      g.lineTo(cx + s * r * 0.9, cy - r * 0.35);
      g.closePath();
      g.fill();
      g.stroke();
      g.fillStyle = "#ffb3c7";
      g.beginPath();
      g.moveTo(cx + s * r * 0.42, cy - r * 0.72);
      g.lineTo(cx + s * r * 0.85, cy - r * 1.12);
      g.lineTo(cx + s * r * 0.82, cy - r * 0.5);
      g.closePath();
      g.fill();
    }
    g.fillStyle = "#ff8f3d";
    g.beginPath();
    g.ellipse(cx, cy, r, r * 0.86, 0, 0, Math.PI * 2);
    g.fill();
    g.stroke();
    g.fillStyle = "#fff6e8";
    g.beginPath();
    g.moveTo(cx - r * 0.95, cy + r * 0.05);
    g.quadraticCurveTo(cx - r * 0.3, cy - r * 0.05, cx, cy + r * 0.25);
    g.quadraticCurveTo(cx + r * 0.3, cy - r * 0.05, cx + r * 0.95, cy + r * 0.05);
    g.quadraticCurveTo(cx + r * 0.6, cy + r * 0.85, cx, cy + r * 0.86);
    g.quadraticCurveTo(cx - r * 0.6, cy + r * 0.85, cx - r * 0.95, cy + r * 0.05);
    g.closePath();
    g.fill();
    // Глаза-блюдца с бликами, нос, румянец.
    for (const s of [-1, 1]) {
      g.fillStyle = INK;
      g.beginPath();
      g.ellipse(cx + s * r * 0.38, cy - r * 0.12, r * 0.15, r * 0.19, 0, 0, Math.PI * 2);
      g.fill();
      g.fillStyle = "#fff";
      g.beginPath();
      g.arc(cx + s * r * 0.38 + r * 0.05, cy - r * 0.19, r * 0.06, 0, Math.PI * 2);
      g.fill();
      g.fillStyle = "rgba(255,90,120,0.45)";
      g.beginPath();
      g.ellipse(cx + s * r * 0.62, cy + r * 0.22, r * 0.13, r * 0.07, 0, 0, Math.PI * 2);
      g.fill();
    }
    g.fillStyle = INK;
    g.beginPath();
    g.ellipse(cx, cy + r * 0.3, r * 0.1, r * 0.07, 0, 0, Math.PI * 2);
    g.fill();
    g.lineWidth = lw * 0.8;
    g.beginPath();
    g.moveTo(cx - r * 0.14, cy + r * 0.45);
    g.quadraticCurveTo(cx - r * 0.07, cy + r * 0.53, cx, cy + r * 0.42);
    g.quadraticCurveTo(cx + r * 0.07, cy + r * 0.53, cx + r * 0.14, cy + r * 0.45);
    g.stroke();
    // Линии пазла (3×2 с «ушками»), одна деталь выпала — видна дырка.
    const cols = 3, rows = 2, pw = W / cols, ph = H / rows;
    const hole = [2, 1];
    g.fillStyle = "#6b4fa8";
    g.fillRect(hole[0] * pw, hole[1] * ph, pw, ph);
    g.strokeStyle = "rgba(255,255,255,0.95)";
    g.lineWidth = lw * 0.9;
    const knob = Math.min(pw, ph) * 0.13;
    for (let c = 1; c < cols; c++) {
      const x = c * pw;
      g.beginPath();
      g.moveTo(x, 0);
      for (let rI = 0; rI < rows; rI++) {
        const ym = (rI + 0.5) * ph, sgn = (c + rI) % 2 ? 1 : -1;
        g.lineTo(x, ym - knob);
        g.arc(x + sgn * knob * 0.6, ym, knob, -Math.PI / 2 * sgn - (sgn < 0 ? Math.PI : 0), Math.PI / 2 * sgn + (sgn < 0 ? Math.PI : 0), sgn < 0);
        g.lineTo(x, (rI + 1) * ph);
      }
      g.stroke();
    }
    for (let rI = 1; rI < rows; rI++) {
      const y = rI * ph;
      g.beginPath();
      g.moveTo(0, y);
      for (let c = 0; c < cols; c++) {
        const xm = (c + 0.5) * pw, sgn = c % 2 ? 1 : -1;
        g.lineTo(xm - knob, y);
        g.arc(xm, y + sgn * knob * 0.6, knob, Math.PI, 0, sgn > 0);
        g.lineTo((c + 1) * pw, y);
      }
      g.stroke();
    }
    // Выпавшая деталь лежит наискосок.
    g.save();
    g.translate(W * 0.86, H * 0.72);
    g.rotate(0.35 + 0.05 * Math.sin(t * 3));
    inkRect(g, -pw * 0.32, -ph * 0.32, pw * 0.64, ph * 0.64, pw * 0.06, "#ff8f3d", lw * 0.8);
    g.restore();
    // Наклейка.
    g.save();
    g.translate(W * 0.17, H * 0.14);
    g.rotate(-0.12);
    g.font = font(900, H * 0.075);
    const lab = String(p.label ?? "1000 деталей");
    const tw = g.measureText(lab).width;
    inkRect(g, -tw / 2 - W * 0.03, -H * 0.06, tw + W * 0.06, H * 0.12, H * 0.04, "#ffc857", lw * 0.7);
    g.fillStyle = INK;
    g.textAlign = "center";
    g.fillText(lab, 0, H * 0.027);
    g.textAlign = "left";
    g.restore();
  },
  /** Крупная надпись (заглушка, вопрос, «?!»). */
  text(g, W, H, _t, p) {
    g.fillStyle = p.bg || "#fff6e8";
    g.fillRect(0, 0, W, H);
    g.fillStyle = p.color || INK;
    g.font = font(900, H * (p.size ?? 0.3));
    g.textAlign = "center";
    g.fillText(String(p.text ?? "?"), W / 2, H * 0.62);
    g.textAlign = "left";
  },
  // Мем-серии: рабочая сессия «аккаунта» с лимитом, набросок карандашом (meme_painters.ts).
  ...MEME_PAINTERS,
};
