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
 * Никаких чужих логотипов: иконка вкладки — цветной кружок с буквой, название — просто текст.
 */
import { UI_FONT, roundRect } from "./ui.ts";
import { ease } from "./timeline.ts";

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
}
export interface ChatPage { kind?: "chat"; name: string; accent?: string; bg?: string; bubble?: string; botBubble?: string;
  letter?: string; messages: ChatMessage[]; placeholder?: string }
export interface BrowserTab { title: string; icon?: string; letter?: string; page: ChatPage }
export interface BrowserSpec { tabs: BrowserTab[]; chrome?: string; scale?: number }

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

/** Высота блока ответа и отрисовка (draw=false — только замер). */
function blockLayout(g: CanvasRenderingContext2D, b: ChatBlock, x: number, y: number, w: number, s: number, draw: boolean, ink: string): number {
  if (b.hr) {
    if (draw) {
      g.fillStyle = "rgba(0,0,0,0.12)";
      g.fillRect(x, y + 10 * s, w, 3 * s);
    }
    return 24 * s;
  }
  if (b.table) {
    const rows = b.table, cols = rows[0]?.length || 1, rh = 50 * s, cw = w / cols;
    if (draw) {
      g.lineWidth = 3 * s;
      g.strokeStyle = "rgba(60,40,30,0.35)";
      rows.forEach((r, i) => {
        if (i === 0) {
          g.fillStyle = "rgba(217,119,75,0.18)";
          g.fillRect(x, y, w, rh);
        }
        r.forEach((c, j) => {
          g.fillStyle = ink;
          g.font = font(i === 0 ? 800 : 600, 30 * s);
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
  g.font = font(head ? 800 : 500, (head ? 34 : 30) * s);
  const lines = wrapLines(g, b.h || b.p || "", w);
  const lh = (head ? 46 : 42) * s;
  if (draw) {
    g.fillStyle = ink;
    lines.forEach((l, i) => g.fillText(l, x, y + lh * (i + 0.78)));
  }
  return lines.length * lh + 6 * s;
}

/** Окно браузера с вкладками и чатом активной вкладки. */
export function drawBrowser(g: CanvasRenderingContext2D, W: number, H: number, b: BrowserSpec, t: number, active: number,
  badge?: { tab: number; text: string; t: number } | null) {
  const s = (b.scale ?? 1) * (W / 1280);
  g.save();
  g.fillStyle = b.chrome || "#e6dccd";
  g.fillRect(0, 0, W, H);
  // Вкладки.
  const tabH = 78 * s, tabW = Math.min(420 * s, (W - 40 * s) / Math.max(1, b.tabs.length));
  b.tabs.forEach((tab, i) => {
    const x = 18 * s + i * (tabW + 8 * s), y = 12 * s;
    const on = i === active;
    g.fillStyle = on ? (tab.page.bg || "#fbf7f1") : "rgba(255,255,255,0.35)";
    roundRect(g, x, y, tabW, tabH, 18 * s);
    g.fill();
    g.fillStyle = tab.icon || "#d9774b";
    g.beginPath();
    g.arc(x + 38 * s, y + tabH * 0.47, 17 * s, 0, Math.PI * 2);
    g.fill();
    if (tab.letter) {
      g.fillStyle = "#ffffff";
      g.font = font(900, 20 * s);
      g.textAlign = "center";
      g.fillText(tab.letter, x + 38 * s, y + tabH * 0.47 + 7 * s);
      g.textAlign = "left";
    }
    let title = tab.title;
    if (badge && badge.tab === i && t >= badge.t) title = `${badge.text} ${title}`;
    g.fillStyle = on ? "#2b1d16" : "rgba(43,29,22,0.6)";
    g.font = font(on ? 800 : 700, 30 * s);
    g.fillText(title, x + 66 * s, y + tabH * 0.47 + 10 * s);
    if (badge && badge.tab === i && t >= badge.t) {
      const k = ease("outBack", clamp01((t - badge.t) / 0.3));
      g.fillStyle = "#ff4b3e";
      g.beginPath();
      g.arc(x + tabW - 30 * s, y + tabH * 0.47, 11 * s * k, 0, Math.PI * 2);
      g.fill();
    }
  });
  const tab = b.tabs[Math.max(0, Math.min(b.tabs.length - 1, active))];
  if (tab) drawChat(g, 0, tabH + 12 * s, W, H - tabH - 12 * s, tab.page, t, s);
  g.restore();
}

/** Страница-чат в прямоугольнике. */
export function drawChat(g: CanvasRenderingContext2D, X: number, Y: number, W: number, H: number, page: ChatPage, t: number, s: number) {
  const ink = "#2b1d16";
  g.save();
  g.beginPath();
  g.rect(X, Y, W, H);
  g.clip();
  g.fillStyle = page.bg || "#fbf7f1";
  g.fillRect(X, Y, W, H);
  // Шапка.
  const headH = 84 * s;
  g.fillStyle = "rgba(0,0,0,0.04)";
  g.fillRect(X, Y, W, headH);
  g.fillStyle = page.accent || "#d9774b";
  g.beginPath();
  g.arc(X + 56 * s, Y + headH / 2, 24 * s, 0, Math.PI * 2);
  g.fill();
  if (page.letter) {
    g.fillStyle = "#fff";
    g.font = font(900, 26 * s);
    g.textAlign = "center";
    g.fillText(page.letter, X + 56 * s, Y + headH / 2 + 9 * s);
    g.textAlign = "left";
  }
  g.fillStyle = ink;
  g.font = font(900, 44 * s);
  g.fillText(page.name, X + 96 * s, Y + headH / 2 + 15 * s);
  // Поле ввода внизу: набор текста пользователем перед отправкой.
  const inH = 96 * s, inY = Y + H - inH - 22 * s;
  let typing: ChatMessage | null = null;
  for (const m of page.messages) if (m.from === "user" && m.type && t >= m.t && t < m.t + m.type) typing = m;
  g.fillStyle = "#ffffff";
  roundRect(g, X + 30 * s, inY, W - 60 * s, inH, 30 * s);
  g.fill();
  g.lineWidth = 3 * s;
  g.strokeStyle = "rgba(43,29,22,0.18)";
  g.stroke();
  g.font = font(600, 34 * s);
  if (typing) {
    const k = clamp01((t - typing.t) / typing.type!);
    const txt = (typing.text || "").slice(0, Math.ceil((typing.text || "").length * k));
    g.fillStyle = ink;
    const shown = wrapLines(g, txt, W - 160 * s);
    const last = shown[shown.length - 1] || "";
    g.fillText(last, X + 62 * s, inY + inH * 0.62);
    if (Math.floor(t * 3) % 2 === 0) g.fillRect(X + 64 * s + g.measureText(last).width, inY + inH * 0.3, 4 * s, inH * 0.42);
  } else {
    g.fillStyle = "rgba(43,29,22,0.38)";
    g.fillText(page.placeholder || "Сообщение…", X + 62 * s, inY + inH * 0.62);
  }
  // Сообщения: сначала замер всей высоты, потом прокрутка к последнему.
  const areaTop = Y + headH + 20 * s, areaBot = inY - 18 * s;
  const maxW = W * 0.72;
  type Item = { m: ChatMessage; h: number; kind: "bubble" | "thinking" | "answer"; lines?: string[]; blocks?: ChatBlock[] };
  const items: Item[] = [];
  for (const m of page.messages) {
    const shownAt = m.t + (m.from === "user" ? m.type || 0 : m.think || 0);
    if (m.from === "bot" && m.think && t >= m.t && t < shownAt) {
      items.push({ m, h: 92 * s, kind: "thinking" });
      continue;
    }
    if (t < shownAt) continue;
    if (m.blocks) {
      const k = m.stream ? clamp01((t - shownAt) / m.stream) : 1;
      const n = Math.max(1, Math.ceil(m.blocks.length * k));
      const bl = m.blocks.slice(0, n);
      let h = 40 * s;
      for (const b of bl) h += blockLayout(g, b, 0, 0, maxW - 60 * s, s, false, ink);
      items.push({ m, h, kind: "answer", blocks: bl });
    } else {
      g.font = font(m.from === "user" ? 700 : 600, (m.from === "user" ? 44 : 38) * s);
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
      g.font = font(user ? 700 : 600, (user ? 44 : 38) * s);
      bw = Math.min(maxW, Math.max(...it.lines!.map((l) => g.measureText(l).width)) + 64 * s);
    } else if (it.kind === "thinking") bw = 170 * s;
    const bx = user ? X + W - 34 * s - bw : X + 34 * s;
    g.save();
    const ox = user ? bx + bw : bx;
    g.translate(ox, y + it.h / 2);
    g.scale(0.6 + 0.4 * pop, 0.6 + 0.4 * pop);
    g.globalAlpha = clamp01(pop * 1.4);
    g.translate(-ox, -(y + it.h / 2));
    g.fillStyle = user ? (page.bubble || "#f6cdb6") : (page.botBubble || "#efe6da");
    roundRect(g, bx, y, bw, it.h, 30 * s);
    g.fill();
    g.fillStyle = ink;
    if (it.kind === "thinking") {
      for (let i = 0; i < 3; i++) {
        const k = 0.5 + 0.5 * Math.sin(t * 10 - i * 1.1);
        g.globalAlpha = 0.35 + 0.65 * k;
        g.beginPath();
        g.arc(bx + 50 * s + i * 36 * s, y + it.h / 2 - k * 6 * s, 12 * s, 0, Math.PI * 2);
        g.fill();
      }
    } else if (it.kind === "bubble") {
      g.font = font(user ? 700 : 600, (user ? 44 : 38) * s);
      const lh = (user ? 58 : 50) * s;
      it.lines!.forEach((l, i) => g.fillText(l, bx + 32 * s, y + 22 * s + lh * (i + 0.78)));
    } else {
      let yy = y + 20 * s;
      for (const b of it.blocks!) yy += blockLayout(g, b, bx + 30 * s, yy, bw - 60 * s, s, true, ink);
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
    // Счётчик просмотров.
    const views = (p.from ?? 120000) + ((p.to ?? 1000000) - (p.from ?? 120000)) * ease("outCubic", clamp01(t / (p.dur ?? 2.2)));
    const txt = `${fmt(views)} просмотров`;
    g.font = font(900, H * 0.075);
    const tw = g.measureText(txt).width;
    inkRect(g, W - tw - W * 0.17, py + H * 0.025, tw + W * 0.08, H * 0.11, H * 0.05, "#ff4b3e", lw * 0.8);
    g.fillStyle = "#fff";
    g.fillText(txt, W - tw - W * 0.13, py + H * 0.105);
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
};
