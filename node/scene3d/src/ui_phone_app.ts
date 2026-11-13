/**
 * Экраны телефона-вставки, кроме сайта магазина (ui_site.ts):
 *
 *   * ``app`` — НАСТОЯЩИЕ кадры записи экрана приложения (например, Коллабики: инбокс → приглашение → «Коллаб!»),
 *     картинки по времени (склейка без анимации, как в самом приложении), перед сменой — касание пальцем
 *     (наш полупрозрачный кружок с «волной»). Кадр вписан по высоте под строкой состояния, поля — цветом фона приложения.
 *   * ``dm`` — личка в мессенджере (тёмная тема): шапка с аватаром-буквой, именем и статусом («печатает…» перед
 *     каждым сообщением собеседника), пузыри входящих/исходящих по времени с «попом».
 *
 * Всё — функция времени t (кадры загружает loadSite по spec.siteShots).
 */
import { roundRect } from "./ui.ts";
import { siteImages } from "./ui_site.ts";

type G = CanvasRenderingContext2D;
const clamp01 = (x: number) => (x <= 0 ? 0 : x >= 1 ? 1 : x);
const smooth = (x: number) => {
  const k = clamp01(x);
  return k * k * (3 - 2 * k);
};
const FONT = "VvfUi, Montserrat, Arial, sans-serif";
const font = (w: number, px: number) => `${w} ${Math.round(px)}px ${FONT}`;

export interface AppFrames {
  frames: { t: number; url: string; tap?: [number, number]; tapT?: number }[];
  bg?: string;
  clock?: string;
}

export interface DmScreen {
  title: string;
  status?: string;
  letter?: string;
  color?: string;
  clock?: string;
  msgs: { t: number; me?: boolean; text: string; time?: string }[];
}

function statusBar(g: G, W: number, h: number, clock: string, bg: string) {
  const u = W / 500;
  g.fillStyle = bg;
  g.fillRect(0, 0, W, h);
  g.fillStyle = "#eef1f6";
  g.font = font(800, 19 * u);
  g.textBaseline = "middle";
  g.textAlign = "left";
  g.fillText(clock, 34 * u, h * 0.56);
  for (let i = 0; i < 4; i++) g.fillRect(W - (118 - i * 8) * u, h * 0.56 + (4 - 4 * i) * u, 5 * u, (6 + 4 * i) * u);
  g.strokeStyle = "#eef1f6";
  g.lineWidth = 1.8 * u;
  roundRect(g, W - 76 * u, h * 0.56 - 8 * u, 34 * u, 17 * u, 4.5 * u);
  g.stroke();
  g.fillRect(W - 73 * u, h * 0.56 - 5 * u, 24 * u, 11 * u);
}

/** Палец: полупрозрачный кружок, нажатие — сжатие и «волна» (как у экрана сайта). */
function finger(g: G, W: number, x: number, y: number, t: number, tTap: number) {
  const k = W / 390;
  const a = Math.min(clamp01((t - (tTap - 0.45)) / 0.2), clamp01((tTap + 0.35 - t) / 0.2));
  if (a <= 0) return;
  const press = t >= tTap - 0.08 && t <= tTap + 0.12 ? 0.82 : 1;
  g.save();
  g.globalAlpha = a;
  g.shadowColor = "rgba(0,0,0,0.45)";
  g.shadowBlur = 14 * k;
  g.fillStyle = "rgba(255,255,255,0.55)";
  g.beginPath();
  g.arc(x, y, 22 * k * press, 0, Math.PI * 2);
  g.fill();
  g.shadowBlur = 0;
  g.strokeStyle = "rgba(255,255,255,0.9)";
  g.lineWidth = 2 * k;
  g.stroke();
  const u = (t - tTap) / 0.45;
  if (u > 0 && u < 1) {
    g.globalAlpha = a * (1 - u);
    g.lineWidth = 3 * k;
    g.beginPath();
    g.arc(x, y, (24 + 40 * u) * k, 0, Math.PI * 2);
    g.stroke();
  }
  g.restore();
}

/** Экран «app»: кадр записи на время t (последний с frames[i].t ≤ t), касание — на кадре, где оно задано. */
export function drawAppFrames(g: G, W: number, H: number, a: AppFrames, t: number) {
  const bg = a.bg || "#0b1018";
  g.fillStyle = bg;
  g.fillRect(0, 0, W, H);
  const fr = a.frames.filter((f) => f.t <= t + 1e-9);
  const cur = fr.length ? fr[fr.length - 1] : a.frames[0];
  const bar = 88 * (W / 1000);
  const img = cur ? siteImages[cur.url] : undefined;
  if (img) {
    const k = Math.min(W / img.width, (H - bar) / img.height);
    const iw = img.width * k, ih = img.height * k, ix = (W - iw) / 2;
    g.imageSmoothingEnabled = true;
    g.imageSmoothingQuality = "high";
    g.drawImage(img, ix, bar, iw, ih);
    if (cur.tap && cur.tapT !== undefined) finger(g, W, ix + cur.tap[0] * iw, bar + cur.tap[1] * ih, t, cur.tapT);
  }
  statusBar(g, W, bar, a.clock || "18:04", bg);
}

/** Перенос по словам в ширину max. */
function wrap(g: G, text: string, max: number): string[] {
  const out: string[] = [];
  let cur = "";
  for (const w of text.split(/\s+/).filter(Boolean)) {
    const next = cur ? `${cur} ${w}` : w;
    if (g.measureText(next).width > max && cur) {
      out.push(cur);
      cur = w;
    } else cur = next;
  }
  if (cur) out.push(cur);
  return out;
}

/** Экран «dm»: тёмная тема мессенджера, сообщения снизу вверх (последнее — над полем ввода). */
export function drawDm(g: G, W: number, H: number, d: DmScreen, t: number) {
  const u = W / 500;
  const BG = "#0e1621", HEAD = "#17212b", IN = "#182533", OUT = "#2b5278", INK = "#f5f7fa", MUTED = "#7f91a4";
  g.fillStyle = BG;
  g.fillRect(0, 0, W, H);
  // Узор фона чата: редкие мягкие точки.
  g.fillStyle = "rgba(255,255,255,0.025)";
  for (let i = 0; i < 70; i++) {
    const x = ((i * 137.5) % 500) * u, y = ((i * 97.3) % 1000) * u * 2;
    g.beginPath();
    g.arc(x, y, (3 + (i % 4)) * u, 0, Math.PI * 2);
    g.fill();
  }
  const bar = 44 * u, head = 104 * u;
  // Шапка: назад, аватар-буква, имя, статус.
  g.fillStyle = HEAD;
  g.fillRect(0, bar, W, head);
  g.strokeStyle = INK;
  g.lineWidth = 4 * u;
  g.lineCap = "round";
  g.lineJoin = "round";
  g.beginPath();
  g.moveTo(42 * u, bar + head / 2 - 14 * u);
  g.lineTo(28 * u, bar + head / 2);
  g.lineTo(42 * u, bar + head / 2 + 14 * u);
  g.stroke();
  const ax = 96 * u, ay = bar + head / 2, ar = 32 * u;
  const grad = g.createLinearGradient(ax - ar, ay - ar, ax + ar, ay + ar);
  grad.addColorStop(0, d.color || "#35d0f0");
  grad.addColorStop(1, "#5a6cff");
  g.fillStyle = grad;
  g.beginPath();
  g.arc(ax, ay, ar, 0, Math.PI * 2);
  g.fill();
  g.fillStyle = "#ffffff";
  g.font = font(900, 30 * u);
  g.textAlign = "center";
  g.textBaseline = "middle";
  g.fillText(d.letter || d.title.slice(0, 1), ax, ay + 1 * u);
  g.textAlign = "left";
  g.fillStyle = INK;
  g.font = font(800, 27 * u);
  g.fillText(d.title, 142 * u, ay - 15 * u);
  const shown = d.msgs.filter((m) => m.t <= t);
  const next = d.msgs.find((m) => m.t > t);
  const typing = !!next && !next.me && next.t - t < 1.0;
  g.font = font(500, 20 * u);
  g.fillStyle = typing ? "#64b5ef" : MUTED;
  g.fillText(typing ? "печатает…" : d.status || "в сети", 142 * u, ay + 17 * u);
  // Поле ввода внизу.
  const inH = 84 * u;
  g.fillStyle = HEAD;
  g.fillRect(0, H - inH, W, inH);
  g.fillStyle = MUTED;
  g.font = font(500, 22 * u);
  g.fillText("Сообщение", 40 * u, H - inH / 2);
  // Пузыри снизу вверх.
  const maxW = W * 0.78, pad = 22 * u, size = 30 * u, lh = size * 1.28;
  let y = H - inH - 24 * u;
  g.font = font(600, size);
  for (let i = shown.length - 1; i >= 0 && y > bar + head; i--) {
    const m = shown[i];
    g.font = font(600, size);
    const lines = wrap(g, m.text, maxW - 2 * pad);
    const tw = Math.max(...lines.map((l) => g.measureText(l).width), 70 * u);
    const bw = tw + 2 * pad + 40 * u, bh = lines.length * lh + 2 * pad - 4 * u;
    const age = t - m.t, k = smooth(age / 0.25);
    const bx = m.me ? W - 18 * u - bw : 18 * u;
    g.save();
    g.globalAlpha = 0.2 + 0.8 * k;
    g.translate(bx + (m.me ? bw : 0), y);
    g.scale(0.85 + 0.15 * k, 0.85 + 0.15 * k);
    g.translate(-(bx + (m.me ? bw : 0)), -y);
    g.fillStyle = m.me ? OUT : IN;
    roundRect(g, bx, y - bh, bw, bh, 20 * u);
    g.fill();
    g.fillStyle = INK;
    g.textBaseline = "alphabetic";
    lines.forEach((l, j) => g.fillText(l, bx + pad, y - bh + pad + size * 0.95 + j * lh));
    g.font = font(500, 15 * u);
    g.fillStyle = m.me ? "#7fb2e6" : MUTED;
    g.textAlign = "right";
    g.fillText(m.time || "", bx + bw - 14 * u, y - 12 * u);
    g.textAlign = "left";
    g.restore();
    y -= bh + 14 * u;
  }
  statusBar(g, W, bar, d.clock || "00:41", HEAD);
}
