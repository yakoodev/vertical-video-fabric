/**
 * Вставки без героини (2D, во весь кадр): монитор с эфиром (страницы scene3d/ui.ts — лисичка-PNGtuber, магазин
 * с волчонком, «Оплата отклонена», редактор модели), монитор крупно на аватаре, чат крупно, планшет с эскизом
 * лисички (рисуется по ходу окна рисования), банка-копилка с падающими монетами. Наезд и наклон — по ходу плана.
 */
import { drawChatScreen, drawLive, drawStream, liveImages, type StreamScreen } from "../../scene3d/src/ui.ts";
import { drawDrawApp } from "../../scene3d/src/ui_pages.ts";
import { glow, layer, rgba } from "./plate/draw.ts";
import type { Room } from "./plate/room.ts";
import { BH, BW } from "./plate/room.ts";

type G = CanvasRenderingContext2D;

export interface InsertSpec {
  kind: "monitor" | "monitor_close" | "chat" | "tablet" | "jar";
  t0: number;
  t1: number;
  push?: number;
  roll?: number;
  /** Монитор: свой масштаб вместо 1.0 (monitor) / 1.75 (monitor_close) — «крупнее, но с краями окна эфира». */
  zoom?: number;
  /** Банка: моменты падения монет и сколько монет было до первой. */
  drops?: number[];
  coins?: number;
  /** Планшет: вместо эскиза лисички — свой персонаж (живая картинка аватара карандашом, цвет проявляется). */
  sketch?: boolean;
}

const sk = layer(1200, 800);

/** Эскиз персонажа: аватар в оттенках серого «карандашом» на бумаге, слева направо проявляется цвет. */
function drawSketch(g: CanvasRenderingContext2D, W: number, H: number, p: number) {
  g.fillStyle = "#f4efe6";
  g.fillRect(0, 0, W, H);
  g.strokeStyle = "rgba(120,110,150,0.12)";
  g.lineWidth = 2;
  for (let y = 40; y < H; y += 40) {
    g.beginPath();
    g.moveTo(0, y);
    g.lineTo(W, y);
    g.stroke();
  }
  const img = liveImages.avatar;
  if (!img) return;
  g.save();
  g.filter = "grayscale(1) contrast(1.8) brightness(1.25)";
  g.globalCompositeOperation = "multiply";
  drawLive(g, img, 0, 0, W, H);
  g.restore();
  g.save();
  g.beginPath();
  g.rect(0, 0, W * Math.max(0, Math.min(1, p * 1.3 - 0.25)), H);
  g.clip();
  g.globalAlpha = 0.85;
  drawLive(g, img, 0, 0, W, H);
  g.restore();
  // Панель кистей сверху.
  g.fillStyle = "#2a2440";
  g.fillRect(0, 0, W, 54);
  ["#ff6fb5", "#ffd36a", "#7ee0ff", "#9dff7e", "#c9a0ff"].forEach((c, i) => {
    g.fillStyle = c;
    g.beginPath();
    g.arc(40 + i * 54, 27, 16, 0, Math.PI * 2);
    g.fill();
  });
}

const scr = layer(1600, 900);
// Чат крупно: холст в 2× (рисунок масштабируется сам, u = W / 600) — буквы не «лесенкой» при наезде и крене.
const chat = layer(1200, 2000);
const tab = layer(1200, 800);
const ease = (x: number) => (x <= 0 ? 0 : x >= 1 ? 1 : 0.5 - 0.5 * Math.cos(Math.PI * x));

function backdrop(g: G, room: Room, W: number, H: number, dim: number) {
  g.save();
  g.filter = `blur(14px) brightness(${dim})`;
  g.drawImage(room.far, (W - BW) / 2, (H - BH) / 2);
  g.restore();
}

function camera(g: G, W: number, H: number, s: InsertSpec, t: number, cx: number, cy: number, base: number) {
  const k = ease((t - s.t0) / Math.max(0.1, s.t1 - s.t0));
  const z = base * (1 + (s.push ?? 0.15) * 0.6 * k);
  g.translate(W / 2, H / 2);
  g.rotate((((s.roll ?? 0) * Math.PI) / 180) * (0.4 + 0.6 * k));
  g.scale(z, z);
  g.translate(-cx, -cy);
}

/** Монитор: корпус, экран эфира, свечение; close — крупно на окне аватара. */
function monitor(g: G, W: number, H: number, s: InsertSpec, t: number, stream: StreamScreen, room: Room) {
  backdrop(g, room, W, H, 0.55);
  drawStream(scr.getContext("2d")!, scr.width, scr.height, { ...stream, noChat: true }, t);
  const close = s.kind === "monitor_close";
  const w = 1000, h = (w * 9) / 16, x = (W - w) / 2, y = H * 0.42 - h / 2;
  g.save();
  camera(g, W, H, s, t, W / 2, close && !s.zoom ? y + h * 0.55 : y + h / 2, s.zoom ?? (close ? 1.75 : 1.0));
  g.globalCompositeOperation = "lighter";
  glow(g, W / 2, y + h / 2, w * 0.75, h * 1.0, "#9cc4ff", 0.22);
  g.globalCompositeOperation = "source-over";
  g.fillStyle = "#0e0b18";
  g.beginPath();
  g.roundRect(x - 22, y - 22, w + 44, h + 44, 22);
  g.fill();
  g.fillStyle = "#1a1528";
  g.fillRect(W / 2 - 40, y + h + 22, 80, 120);
  g.beginPath();
  g.roundRect(W / 2 - 170, y + h + 136, 340, 26, 12);
  g.fill();
  g.drawImage(scr, x, y, w, h);
  g.fillStyle = "rgba(255,255,255,0.05)";
  g.fillRect(x, y, w, h * 0.3);
  g.restore();
}

function chatInsert(g: G, W: number, H: number, s: InsertSpec, t: number, stream: StreamScreen, room: Room) {
  backdrop(g, room, W, H, 0.5);
  drawChatScreen(chat.getContext("2d")!, chat.width, chat.height, stream, t);
  const w = 760, h = (w * chat.height) / chat.width, x = (W - w) / 2, y = (H - h) / 2 - 40;
  g.save();
  camera(g, W, H, s, t, W / 2, y + h * 0.62, 1.0);
  g.globalCompositeOperation = "lighter";
  glow(g, W / 2, y + h / 2, w * 0.8, h * 0.7, "#c9a0ff", 0.18);
  g.globalCompositeOperation = "source-over";
  g.fillStyle = "#0e0b18";
  g.beginPath();
  g.roundRect(x - 20, y - 20, w + 40, h + 40, 22);
  g.fill();
  g.imageSmoothingEnabled = true;
  g.imageSmoothingQuality = "high";
  g.drawImage(chat, x, y, w, h);
  g.restore();
}

/** Планшет на столе: эскиз лисички проявляется, стилус ведёт линию. */
function tablet(g: G, W: number, H: number, s: InsertSpec, t: number, stream: StreamScreen) {
  const win = (stream as any).drawWin as number[] | undefined;
  const p = win ? Math.max(0, Math.min(1, (t - win[0]) / Math.max(0.1, win[1] - win[0]))) : 1;
  const gr = g.createLinearGradient(0, 0, 0, H);
  gr.addColorStop(0, "#2b2148");
  gr.addColorStop(1, "#140f24");
  g.fillStyle = gr;
  g.fillRect(0, 0, W, H);
  glow(g, W * 0.5, H * 0.35, W * 0.9, H * 0.5, "#8fb8ff", 0.18);
  if (s.sketch) drawSketch(sk.getContext("2d")!, sk.width, sk.height, p);
  else drawDrawApp(tab.getContext("2d")!, tab.width, tab.height, p);
  g.save();
  camera(g, W, H, s, t, W / 2, H * 0.5, 1.0);
  g.translate(W / 2, H * 0.5);
  g.rotate(-0.06);
  const w = 960, h = 660;
  g.fillStyle = "#0d0b16";
  g.beginPath();
  g.roundRect(-w / 2 - 34, -h / 2 - 34, w + 68, h + 68, 40);
  g.fill();
  g.strokeStyle = "rgba(255,120,200,0.5)";
  g.lineWidth = 4;
  g.stroke();
  g.drawImage(s.sketch ? sk : tab, -w / 2, -h / 2, w, h);
  // Стилус: кончик ходит по контуру эскиза.
  const a = t * 5.2, px = Math.cos(a) * 150 + Math.sin(a * 0.7) * 40, py = Math.sin(a * 1.3) * 110 - 10;
  g.save();
  g.translate(px + 60, py + 10);
  g.rotate(-0.75);
  g.fillStyle = "#e8e4f4";
  g.beginPath();
  g.roundRect(-12, 0, 24, 420, 12);
  g.fill();
  g.fillStyle = "#ff6fb5";
  g.fillRect(-12, 360, 24, 60);
  g.fillStyle = "#2a2440";
  g.beginPath();
  g.moveTo(-12, 0);
  g.lineTo(12, 0);
  g.lineTo(0, -34);
  g.fill();
  g.restore();
  g.restore();
}

/** Банка-копилка: стекло, монеты кучкой (растёт), монета падает и звякает в момент drop. */
function jar(g: G, W: number, H: number, s: InsertSpec, t: number) {
  const gr = g.createLinearGradient(0, 0, 0, H);
  gr.addColorStop(0, "#251c40");
  gr.addColorStop(0.7, "#3a2c5e");
  gr.addColorStop(1, "#1c1530");
  g.fillStyle = gr;
  g.fillRect(0, 0, W, H);
  glow(g, W * 0.6, H * 0.3, W, H * 0.6, "#ffb07a", 0.14);
  g.save();
  camera(g, W, H, s, t, W / 2, H * 0.55, 1.0);
  const cx = W / 2, top = H * 0.36, w = 520, h = 700;
  // Стол.
  g.fillStyle = "#2a2146";
  g.fillRect(-200, top + h - 40, W + 400, 800);
  const drops = s.drops || [];
  const landed = drops.filter((d) => t >= d + 0.35).length;
  const pile = Math.min(1, ((s.coins ?? 10) + landed * 6) / 40);
  // Монеты внутри.
  g.save();
  g.beginPath();
  g.roundRect(cx - w / 2, top, w, h, 70);
  g.clip();
  const n = Math.round(20 + pile * 60);
  for (let i = 0; i < n; i++) {
    const r = Math.sin(i * 91.7) * 0.5 + 0.5, q = Math.sin(i * 37.3) * 0.5 + 0.5;
    const yy = top + h - 30 - (i / n) * h * (0.25 + pile * 0.45) - q * 20;
    g.fillStyle = i % 3 ? "#f4c04a" : "#d99a2a";
    g.beginPath();
    g.ellipse(cx - w / 2 + 40 + r * (w - 80), yy, 34, 13, (q - 0.5) * 0.5, 0, Math.PI * 2);
    g.fill();
    g.strokeStyle = "rgba(120,70,0,0.5)";
    g.lineWidth = 3;
    g.stroke();
  }
  g.restore();
  // Падающая монета.
  for (const d of drops) {
    const u = (t - d + 0.35) / 0.35;
    if (u < 0 || u > 1) continue;
    const yy = top - 260 + u * u * (h * 0.55 + 260);
    g.fillStyle = "#ffd25c";
    g.beginPath();
    g.ellipse(cx + 20, yy, 38, 38 * Math.abs(Math.cos(u * 9)) + 6, 0, 0, Math.PI * 2);
    g.fill();
    g.strokeStyle = "#a8701a";
    g.lineWidth = 4;
    g.stroke();
  }
  // Стекло, крышка с прорезью, бирка.
  g.fillStyle = "rgba(200,220,255,0.12)";
  g.beginPath();
  g.roundRect(cx - w / 2, top, w, h, 70);
  g.fill();
  g.strokeStyle = "rgba(230,240,255,0.55)";
  g.lineWidth = 6;
  g.stroke();
  g.fillStyle = "rgba(255,255,255,0.25)";
  g.fillRect(cx - w / 2 + 40, top + 60, 26, h - 140);
  g.fillStyle = "#c8b8e8";
  g.beginPath();
  g.roundRect(cx - w / 2 - 14, top - 50, w + 28, 70, 18);
  g.fill();
  g.fillStyle = "#3a2c5e";
  g.fillRect(cx - 70, top - 24, 140, 14);
  g.fillStyle = "#fff4e0";
  g.beginPath();
  g.roundRect(cx - 170, top + h * 0.3, 340, 110, 16);
  g.fill();
  g.fillStyle = "#c2407a";
  g.font = "900 58px VvfUi, Arial, sans-serif";
  g.textAlign = "center";
  g.textBaseline = "middle";
  g.fillText("КОПИЛКА", cx, top + h * 0.3 + 56);
  g.restore();
  void rgba;
}

export function drawInsert(g: G, W: number, H: number, s: InsertSpec, t: number, stream: StreamScreen, room: Room) {
  if (s.kind === "monitor" || s.kind === "monitor_close") monitor(g, W, H, s, t, stream, room);
  else if (s.kind === "chat") chatInsert(g, W, H, s, t, stream, room);
  else if (s.kind === "tablet") tablet(g, W, H, s, t, stream);
  else if (s.kind === "jar") jar(g, W, H, s, t);
}
