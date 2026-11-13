/**
 * Текст поверх кадра «Мира от зрителей» в стиле PS1 — всё в холсте интерфейса низкого разрешения (270×480),
 * шрифт Press Start 2P (кириллица есть, проверено) в размерах, кратных 8 px, — после ×4 пиксели ровные:
 *  - субтитры озвучки (drawCaptions): куски по 2–4 слова (режет app/viewer_episode.py), до двух строк, 16 px,
 *    чёрная обводка в пиксель, сказанное слово — жёлтым;
 *  - номер хода (drawTurn) — плашка под окном счётчиков;
 *  - комментарий хода (drawComment): «@ник ♥ лайки» и текст — окно-диалог, набирается по буквам;
 *  - концовка-призыв (drawEndCard): окно-диалог по центру, текст набирается, мигает ▼.
 * Окна раскрываются ступеньками по 4 px, как меню PS1 (тот же приём, что hud.ts).
 */
import { HUD_FONT } from "./hud.ts";

export interface CapWord { w: string; t0: number; t1: number }
export interface CapChunk { t0: number; t1: number; words: CapWord[] }

export interface CommentSpec {
  t0: number;
  t1: number;
  nick: string;
  /** Лайков у коммента; < 0 — неизвестно (в карточке TikTok — сердце без числа). */
  likes: number;
  /** Текст комментария (уже очищенный фильтром и укороченный). */
  text: string;
  /** Подпись над окном: «ХОД 3 · ВЫБРАЛИ ВЫ». */
  title?: string;
  /** «tiktok» — карточка как комментарий в TikTok (серия 2): аватар, ник серым, текст, «Ответить», сердце с числом. */
  style?: "tiktok";
  /** Слова, которые загораются жёлтым с момента t (по подстроке, без регистра). */
  hl?: { w: string; t: number }[];
  y?: number;
  /** Строка над карточкой («1 комментарий») и возраст («1 д.»). */
  header?: string;
  age?: string;
}

export interface EndCardSpec {
  t0: number;
  lines: string[];
  /** Строки, выделенные цветом (индексы). */
  accent?: number[];
}

export interface TitleSpec {
  t0: number;
  t1?: number;
  lines: string[];
  /** Цвет строк по порядку (остальные — белые). */
  colors?: string[];
  y?: number;
  /** Размер шрифта, кратный 8 (24 — крупный крючок первого кадра, 16 — финальный призыв). */
  px?: number;
}

const fontPx = (px: number) => `${px}px ${HUD_FONT}, monospace`;

function outlined(g: CanvasRenderingContext2D, s: string, x: number, y: number, col: string, px: number) {
  g.font = fontPx(px);
  g.textBaseline = "top";
  g.fillStyle = "#000008";
  for (const [dx, dy] of [[-1, 0], [1, 0], [0, -1], [0, 1], [-1, -1], [1, 1], [-1, 1], [1, -1], [0, 2], [1, 2]]) g.fillText(s, x + dx, y + dy);
  g.fillStyle = col;
  g.fillText(s, x, y);
}

/** Раскладка слов по строкам не шире maxW (px), не больше maxLines; слишком длинное слово — своей строкой. */
export function layoutLines(g: CanvasRenderingContext2D, words: string[], maxW: number, px: number): string[][] {
  g.font = fontPx(px);
  const lines: string[][] = [[]];
  for (const w of words) {
    const cur = lines[lines.length - 1];
    const trial = [...cur, w].join(" ");
    if (cur.length && g.measureText(trial).width > maxW) lines.push([w]);
    else cur.push(w);
  }
  return lines;
}

export function drawCaptions(g: CanvasRenderingContext2D, chunks: CapChunk[], t: number, y = 352, px = 16) {
  const c = chunks.find((k) => t >= k.t0 && t < k.t1);
  if (!c) return;
  const W = g.canvas.width;
  const lines = layoutLines(g, c.words.map((w) => w.w), W - 20, px);
  let i = 0;
  lines.forEach((line, li) => {
    g.font = fontPx(px);
    const full = line.join(" ");
    let x = Math.round((W - g.measureText(full).width) / 2);
    const yy = y + li * (px + 6);
    for (const w of line) {
      const word = c.words[i++];
      const said = t >= word.t0;
      outlined(g, w, x, yy, said ? "#f8d848" : "#f8f8f8", px);
      x += g.measureText(w + " ").width;
    }
  });
}

/** Окно PS1: градиент, двойная рамка со срезанными углами; раскрытие k 0..1 ступеньками по вертикали. */
export function panel(g: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, k: number, alpha = 0.78) {
  const hh = Math.max(4, Math.round((h * Math.min(1, k)) / 4) * 4);
  const top = y + Math.round((h - hh) / 2);
  const grad = g.createLinearGradient(0, top, 0, top + hh);
  grad.addColorStop(0, `rgba(40,56,160,${alpha})`);
  grad.addColorStop(1, `rgba(10,14,60,${alpha})`);
  g.fillStyle = grad;
  g.fillRect(x + 1, top + 1, w - 2, hh - 2);
  g.fillStyle = "#e8ecf8";
  g.fillRect(x + 2, top, w - 4, 1);
  g.fillRect(x + 2, top + hh - 1, w - 4, 1);
  g.fillRect(x, top + 2, 1, hh - 4);
  g.fillRect(x + w - 1, top + 2, 1, hh - 4);
  for (const [px, py] of [[x + 1, top + 1], [x + w - 2, top + 1], [x + 1, top + hh - 2], [x + w - 2, top + hh - 2]]) g.fillRect(px, py, 1, 1);
  g.fillStyle = "#7080c8";
  g.fillRect(x + 2, top + 2, w - 4, 1);
  g.fillRect(x + 2, top + 2, 1, hh - 4);
  return k >= 1;
}

function plain(g: CanvasRenderingContext2D, s: string, x: number, y: number, col: string, px = 8) {
  g.font = fontPx(px);
  g.textBaseline = "top";
  g.fillStyle = "#000010";
  g.fillText(s, x + 1, y + 1);
  g.fillStyle = col;
  g.fillText(s, x, y);
}

/** «ХОД N» под окном счётчиков (hud.ts: x 6, y 8, высота 62). */
export function drawTurn(g: CanvasRenderingContext2D, turn: number, t: number, t0 = 0, x = 6, y = 74) {
  if (t < t0) return;
  const s = `ХОД ${turn}`;
  g.font = fontPx(8);
  const w = Math.ceil(g.measureText(s).width) + 14;
  if (panel(g, x, y, w, 16, (t - t0) / 0.25)) plain(g, s, x + 7, y + 4, "#f8d848");
}

/** Набор по буквам: сколько символов видно через dt секунд при cps символов в секунду. */
export const typed = (s: string, dt: number, cps = 40) => s.slice(0, Math.max(0, Math.floor(dt * cps)));

/** Сердце-контур 9×8 px. */
function heart(g: CanvasRenderingContext2D, x: number, y: number, col: string) {
  const rows = ["01100110", "10011001", "10000001", "10000001", "01000010", "00100100", "00011000"];
  g.fillStyle = col;
  rows.forEach((r, j) => [...r].forEach((b, i) => b === "1" && g.fillRect(x + i, y + j, 1, 1)));
}

/** Карточка-комментарий в стиле TikTok (тёмная тема): шапка «1 комментарий», аватар-кружок с буквой, ник, текст
 * (слова hl загораются), «1 д. Ответить», справа сердце-контур и число. Раскрывается ступеньками, текст сразу. */
function drawTikTok(g: CanvasRenderingContext2D, c: CommentSpec, t: number) {
  const W = g.canvas.width, x = 8, w = W - 40, y = c.y ?? 132; // справа — запас под кнопки TikTok
  const words = c.text.split(/\s+/);
  const lines = layoutLines(g, words, w - 52, 8);
  const h = 44 + lines.length * 12 + (c.header ? 16 : 0);
  const k = Math.min(1, (t - c.t0) / 0.25);
  const hh = Math.max(4, Math.round((h * k) / 4) * 4);
  const top = y + Math.round((h - hh) / 2);
  g.fillStyle = "rgba(22,22,26,0.94)";
  g.fillRect(x + 2, top, w - 4, hh);
  g.fillRect(x, top + 2, w, hh - 4);
  g.fillRect(x + 1, top + 1, w - 2, hh - 2);
  if (k < 1) return;
  let yy = y + 8;
  if (c.header) {
    g.font = fontPx(8);
    plain(g, c.header, Math.round(x + (w - g.measureText(c.header).width) / 2), yy, "#e8e8ec");
    g.fillStyle = "#3a3a42";
    g.fillRect(x + 6, yy + 12, w - 12, 1);
    yy += 18;
  }
  // Аватар: пиксельный кружок 18 px с буквой ника.
  const ax = x + 8, ay = yy, R = 9;
  for (let j = 0; j < 2 * R; j++) for (let i = 0; i < 2 * R; i++) {
    const dx = i + 0.5 - R, dy = j + 0.5 - R;
    if (dx * dx + dy * dy <= R * R) {
      g.fillStyle = j < R ? "#d86890" : "#a84a78";
      g.fillRect(ax + i, ay + j, 1, 1);
    }
  }
  g.font = fontPx(8);
  const ch = c.nick[0].toUpperCase();
  plain(g, ch, Math.round(ax + R - g.measureText(ch).width / 2), ay + 5, "#ffffff");
  const tx = x + 34;
  plain(g, c.nick, tx, yy, "#a0a0aa");
  let i = 0;
  lines.forEach((line, li) => {
    let lx = tx;
    for (const wd of line) {
      const on = c.hl?.some((h) => t >= h.t && wd.toLowerCase().includes(h.w.toLowerCase()));
      plain(g, wd, lx, yy + 14 + li * 12, on ? "#f8d848" : "#f4f4f6");
      lx += g.measureText(wd + " ").width;
      i++;
    }
  });
  const by = yy + 16 + lines.length * 12;
  plain(g, `${c.age ?? "1 д."}  Ответить`, tx, by, "#80808a");
  heart(g, x + w - 18, yy + 14, "#a0a0aa");
  // Число лайков неизвестно (likes < 0) — только сердце: счётчики в кадре — только настоящие.
  if (c.likes >= 0) {
    const n = String(c.likes);
    plain(g, n, Math.round(x + w - 14 - g.measureText(n).width / 2), yy + 25, "#a0a0aa");
  }
}

export function drawComment(g: CanvasRenderingContext2D, c: CommentSpec, t: number) {
  if (t < c.t0 || t >= c.t1) return;
  if (c.style === "tiktok") return drawTikTok(g, c, t);
  const W = g.canvas.width, x = 10, w = W - 20, y = 150;
  const lines = layoutLines(g, c.text.split(/\s+/), w - 16, 8).map((l) => l.join(" ")).slice(0, 5);
  const h = 30 + lines.length * 11;
  if (c.title) plain(g, c.title, x + 2, y - 12, "#a8f8a0");
  if (!panel(g, x, y, w, h, (t - c.t0) / 0.3)) return;
  const dt = t - c.t0 - 0.3;
  plain(g, `@${c.nick}`, x + 8, y + 7, "#9ad8ff");
  const likes = `♥ ${c.likes}`;
  g.font = fontPx(8);
  plain(g, likes, x + w - 8 - Math.ceil(g.measureText(likes).width), y + 7, "#f87890");
  const shown = typed(lines.join("\n"), dt, 45).split("\n");
  lines.forEach((_, i) => plain(g, shown[i] ?? "", x + 8, y + 22 + i * 11, "#f8f8f8"));
}

export function drawEndCard(g: CanvasRenderingContext2D, e: EndCardSpec, t: number) {
  if (t < e.t0) return;
  const W = g.canvas.width, w = W - 24, x = 12;
  const h = 26 + e.lines.length * 14;
  const y = 118;
  if (!panel(g, x, y, w, h, (t - e.t0) / 0.3, 0.86)) return;
  const dt = t - e.t0 - 0.3;
  let shown = 0;
  const total = e.lines.join("").length;
  const visible = Math.floor(dt * 48);
  e.lines.forEach((line, i) => {
    const n = Math.max(0, Math.min(line.length, visible - shown));
    shown += line.length;
    g.font = fontPx(8);
    const lx = Math.round(x + (w - g.measureText(line).width) / 2);
    plain(g, line.slice(0, n), lx, y + 13 + i * 14, e.accent?.includes(i) ? "#f8d848" : "#f8f8f8");
  });
  // Мигающий ▼, когда весь текст набран (как «дальше» в диалогах).
  if (visible >= total && Math.floor(t * 2.5) % 2 === 0) {
    g.fillStyle = "#f8f8f8";
    const cx = x + w - 14, cy = y + h - 10;
    for (let r = 0; r < 4; r++) g.fillRect(cx - 3 + r, cy + r, 7 - 2 * r, 1);
  }
}

/** Крупный титр поверх кадра (крючок первого кадра, финальный призыв): центр, толстая обводка, без набора по буквам. */
export function drawTitle(g: CanvasRenderingContext2D, e: TitleSpec, t: number) {
  if (t < e.t0 || (e.t1 !== undefined && t >= e.t1)) return;
  const W = g.canvas.width, px = e.px ?? 24;
  e.lines.forEach((line, i) => {
    g.font = fontPx(px);
    const x = Math.round((W - g.measureText(line).width) / 2);
    outlined(g, line, x, (e.y ?? 60) + i * (px + 8), e.colors?.[i] ?? "#f8f8f8", px);
  });
}
