/**
 * Картинки мем-серии 10 «Сделай за ночь» (экран окна ``chat_panel`` и ноутбука ``laptop``; подключены в
 * PROMPT_PAINTERS meme_painters_prompt.ts):
 *
 * - ``file_tree`` — проводник редактора: дерево проекта растёт за ночь. ``entries: [{t, name, depth, dir?}]`` — строки
 *   по порядку показа (``depth`` — отступ, ``dir`` — папка); строка появляется в ``t`` — раскрывается по высоте за
 *   ``grow`` с и вспыхивает маркером ``flash`` с. Список сам прокручивается так, чтобы новая строка была видна. В шапке —
 *   ``title`` проекта и счётчик «N файлов» (папки не считаются; ``countWord`` — подпись; при смене N — поп). ``fs`` —
 *   кегль при ширине 720, ``scale``; ``follow`` — на какой доле высоты окна держится новая строка (0.94; выше —\n *   чтобы её не закрывали субтитры). Цвет папки — по корневой папке (``colors: {name: color}``).
 * - ``site_empty`` — режим ``button_site`` без кнопки: на месте кнопки — пунктирная пустота, курсор ищет её.
 *
 * Чистые функции (``treeLayout``, ``fileCount``) — без холста, для тестов.
 */
import { rr } from "./meme_painters_rp.ts";

type G = CanvasRenderingContext2D;
type Painter = (g: G, W: number, H: number, t: number, p: Record<string, any>) => void;

const FONT = (w: number, px: number) => `${w} ${Math.round(px)}px Montserrat, Arial, sans-serif`;
const clamp01 = (x: number) => (x < 0 ? 0 : x > 1 ? 1 : x);
const inOut = (k: number) => 0.5 - 0.5 * Math.cos(Math.PI * clamp01(k));

export interface Entry { t: number; name: string; depth: number; dir?: boolean }
export interface Row { e: Entry; y: number; h: number; k: number; root: string }

/** Строки дерева в момент t: высота каждой появившейся строки растёт 0→1 за ``grow`` с (список не прыгает). */
export function treeLayout(entries: Entry[], t: number, rowH: number, grow = 0.25): { rows: Row[]; height: number } {
  const rows: Row[] = [];
  let y = 0;
  let root = "";
  for (const e of entries) {
    if (e.depth === 0) root = e.name.replace(/\/$/, "");
    if (t < e.t) continue;
    const k = inOut((t - e.t) / Math.max(1e-3, grow));
    rows.push({ e, y, h: rowH * k, k, root });
    y += rowH * k;
  }
  return { rows, height: y };
}

/** Сколько файлов (не папок) появилось к моменту t. */
export function fileCount(entries: Entry[], t: number): number {
  return entries.filter((e) => !e.dir && t >= e.t).length;
}

const BG = "#1f2330";
const PANEL = "#272c3b";
const TEXT = "#e8ebf4";
const DIM = "#8f97ad";

function folderIcon(g: G, x: number, y: number, s: number, color: string) {
  g.fillStyle = color;
  rr(g, x, y - s * 0.72, s * 0.5, s * 0.22, s * 0.06);
  g.fill();
  rr(g, x, y - s * 0.6, s * 1.05, s * 0.72, s * 0.1);
  g.fill();
}

function fileIcon(g: G, x: number, y: number, s: number, color: string) {
  g.fillStyle = "#d7dcea";
  g.beginPath();
  g.moveTo(x, y - s * 0.78);
  g.lineTo(x + s * 0.52, y - s * 0.78);
  g.lineTo(x + s * 0.74, y - s * 0.56);
  g.lineTo(x + s * 0.74, y + s * 0.14);
  g.lineTo(x, y + s * 0.14);
  g.closePath();
  g.fill();
  g.fillStyle = color;
  rr(g, x - s * 0.08, y - s * 0.22, s * 0.62, s * 0.26, s * 0.06);
  g.fill();
}

const EXT: Record<string, string> = { sql: "#4f9bff", go: "#38c6d9", py: "#f2c94c", ts: "#4f9bff", tsx: "#4f9bff", yaml: "#ff7a7a",
  yml: "#ff7a7a", md: "#7ad18a", proto: "#c08cff", json: "#f2a65e", toml: "#f2a65e" };

const fileTree: Painter = (g, W, H, t, p) => {
  const s = (W / 720) * Number(p.scale ?? 1);
  const entries: Entry[] = p.entries || [];
  g.fillStyle = BG;
  g.fillRect(0, 0, W, H);
  // Шапка: название проекта и счётчик файлов.
  const headH = 132 * s;
  g.fillStyle = PANEL;
  g.fillRect(0, 0, W, headH);
  g.fillStyle = "#151824";
  g.fillRect(0, headH - 3 * s, W, 3 * s);
  for (const [i, c] of ["#ff6b5e", "#ffc23d", "#3fc454"].entries()) {
    g.fillStyle = c;
    g.beginPath();
    g.arc((30 + i * 28) * s, 34 * s, 9 * s, 0, Math.PI * 2);
    g.fill();
  }
  g.fillStyle = DIM;
  g.font = FONT(700, 25 * s);
  g.textBaseline = "alphabetic";
  g.fillText("ПРОВОДНИК", 30 * s, 92 * s);
  g.fillStyle = TEXT;
  g.font = FONT(800, 30 * s);
  g.fillText(String(p.title || "проект"), 30 * s, 122 * s);
  const n = fileCount(entries, t);
  const lastT = entries.filter((e) => !e.dir && t >= e.t).reduce((a, e) => Math.max(a, e.t), -1e9);
  const pk = clamp01((t - lastT) / 0.22);
  const big = 1 + 0.22 * Math.sin(pk * Math.PI);
  const goal = Number(p.goal ?? 47);
  const px = 40 * s * big;
  g.font = FONT(900, px);
  const label = `${n} ${String(p.countWord ?? "файлов")}`;
  const tw = g.measureText(label).width + px * 0.9;
  const cx = W - 24 * s - tw / 2, cy = headH * 0.52;
  g.fillStyle = n >= goal ? "#e8542f" : "#3a4258";
  rr(g, cx - tw / 2, cy - px * 0.78, tw, px * 1.5, px * 0.5);
  g.fill();
  g.fillStyle = "#ffffff";
  g.textAlign = "center";
  g.fillText(label, cx, cy + px * 0.34);
  g.textAlign = "left";
  // Дерево.
  const fs = Number(p.fs ?? 40) * s;
  const rowH = fs * 1.55;
  const top = headH + 14 * s, viewH = H - top - 10 * s;
  const lay = treeLayout(entries, t, rowH, Number(p.grow ?? 0.25));
  const scroll = Math.max(0, lay.height - viewH * Number(p.follow ?? 0.94));
  g.save();
  g.beginPath();
  g.rect(0, top, W, viewH);
  g.clip();
  const colors: Record<string, string> = p.colors || {};
  const flash = Number(p.flash ?? 0.7);
  for (const r of lay.rows) {
    const y = top + r.y - scroll;
    if (y + r.h < top - rowH || y > top + viewH + rowH) continue;
    const base = y + r.h * 0.5 + fs * 0.36;
    const x = (26 + 38 * r.e.depth) * s;
    g.globalAlpha = r.k;
    // Новая строка — вспышка-маркер.
    const age = t - r.e.t;
    if (age < flash) {
      g.fillStyle = `rgba(255,210,63,${0.55 * (1 - age / flash)})`;
      g.fillRect(0, y, W, r.h);
    }
    // Линии отступа.
    g.fillStyle = "rgba(143,151,173,0.25)";
    for (let d = 1; d <= r.e.depth; d++) g.fillRect((26 + 38 * d - 20) * s, y, 2 * s, r.h);
    const color = colors[r.root] || "#8fa6ff";
    if (r.e.dir) {
      g.fillStyle = DIM;
      g.beginPath();
      g.moveTo(x, base - fs * 0.52);
      g.lineTo(x + fs * 0.42, base - fs * 0.52);
      g.lineTo(x + fs * 0.21, base - fs * 0.18);
      g.closePath();
      g.fill();
      folderIcon(g, x + fs * 0.58, base, fs, color);
    } else {
      const ext = r.e.name.split(".").pop() || "";
      fileIcon(g, x + fs * 0.66, base, fs, EXT[ext] || "#9aa1b0");
    }
    g.fillStyle = r.e.dir ? TEXT : "#c9cfdf";
    g.font = FONT(r.e.dir ? 800 : 600, fs);
    g.fillText(r.e.name, x + fs * 1.85, base);
    g.globalAlpha = 1;
  }
  g.restore();
  // Ползунок справа.
  if (lay.height > viewH) {
    const track = viewH - 16 * s;
    const thumb = Math.max(30 * s, (track * viewH) / lay.height);
    const ty = top + 8 * s + (track - thumb) * clamp01(scroll / Math.max(1, lay.height - viewH * Number(p.follow ?? 0.94)));
    g.fillStyle = "rgba(200,206,224,0.35)";
    rr(g, W - 16 * s, ty, 7 * s, thumb, 4 * s);
    g.fill();
  }
};

/** Пустое место на месте кнопки: пунктирная рамка и курсор, который её ищет. ``at`` — с этого момента курсор кружит. */
export function drawEmptySpot(g: G, W: number, H: number, t: number, p: Record<string, any>) {
  const s = W / 720;
  const bw = W * 0.66, bh = 150 * s;
  const bx = (W - bw) / 2, by = H * 0.56;
  g.save();
  g.strokeStyle = "rgba(138,144,160,0.75)";
  g.lineWidth = 5 * s;
  g.setLineDash([22 * s, 16 * s]);
  rr(g, bx, by, bw, bh, bh * 0.3);
  g.stroke();
  g.restore();
  const at = Number(p.at ?? 0);
  const k = Math.max(0, t - at);
  const cx = W / 2 + bw * 0.18 * Math.sin(k * 1.6) - bw * 0.05, cy = by + bh * 0.4 + bh * 0.25 * Math.sin(k * 2.3 + 0.6);
  g.fillStyle = "#ffffff";
  g.strokeStyle = "#121016";
  g.lineWidth = 4 * s;
  g.beginPath();
  g.moveTo(cx, cy);
  g.lineTo(cx, cy + 70 * s);
  g.lineTo(cx + 18 * s, cy + 54 * s);
  g.lineTo(cx + 32 * s, cy + 84 * s);
  g.lineTo(cx + 44 * s, cy + 78 * s);
  g.lineTo(cx + 30 * s, cy + 50 * s);
  g.lineTo(cx + 52 * s, cy + 48 * s);
  g.closePath();
  g.fill();
  g.stroke();
}

export const NIGHT_PAINTERS: Record<string, Painter> = { file_tree: fileTree };
