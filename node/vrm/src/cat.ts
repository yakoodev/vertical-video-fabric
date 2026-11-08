/**
 * Кот, нарисованный в 2D поверх кадра (передний план): запрыгивает снизу на край стола, садится, хвост ходит,
 * уши дёргаются, моргает. Всё — функция времени; рисуется кистью с контуром, как остальные вещи комнаты.
 */
import { glow } from "./plate/draw.ts";

type G = CanvasRenderingContext2D;

export interface CatSpec {
  /** Окно: прыжок начинается в t0, кот виден до t1. */
  t0: number;
  t1: number;
  /** Куда садится (центр низа, px кадра) и масштаб; flip — смотрит влево. */
  x: number;
  y: number;
  scale?: number;
  flip?: boolean;
  color?: string;
}

const smooth = (x: number) => (x <= 0 ? 0 : x >= 1 ? 1 : x * x * (3 - 2 * x));
const LINE = "#1a1020";

function shade(c: string, k: number): string {
  const n = parseInt(c.slice(1), 16);
  const f = (v: number) => Math.max(0, Math.min(255, Math.round(v * k)));
  return `rgb(${f((n >> 16) & 255)},${f((n >> 8) & 255)},${f(n & 255)})`;
}

function stroke(g: G, w: number) {
  g.lineWidth = w;
  g.strokeStyle = LINE;
  g.lineJoin = "round";
  g.lineCap = "round";
  g.stroke();
}

/** Кот сидя (sit = 1) или в прыжке вытянутым (sit = 0); s — масштаб (высота сидящего кота ≈ 300·s). */
function drawCatBody(g: G, s: number, sit: number, t: number, col: string, u: number) {
  const dark = shade(col, 0.72), light = shade(col, 1.18);
  // Хвост: от крупа дугой, кончик ходит (сидя — обвивает лапы).
  const sw = Math.sin(t * 2.4) * (0.4 + 0.6 * sit);
  g.beginPath();
  g.moveTo(60 * s, -30 * s);
  g.bezierCurveTo(150 * s, -20 * s + 40 * s * (1 - sit), 170 * s, -120 * s * sit - 40 * s, (120 + 40 * sw) * s, (-170 * sit - 30) * s);
  g.lineWidth = 30 * s;
  g.strokeStyle = LINE;
  g.lineCap = "round";
  g.stroke();
  g.lineWidth = 22 * s;
  g.strokeStyle = col;
  g.stroke();
  // Тело: сидя — груша, в прыжке — вытянутый овал.
  g.save();
  g.rotate(-0.5 * (1 - sit));
  g.beginPath();
  g.ellipse(10 * s, -95 * s * sit - 40 * s * (1 - sit), (80 + 50 * (1 - sit)) * s, (100 * sit + 45 * (1 - sit)) * s, 0, 0, Math.PI * 2);
  g.fillStyle = col;
  g.fill();
  stroke(g, 6 * s);
  // Грудка светлая.
  g.beginPath();
  g.ellipse(-35 * s, -110 * s * sit - 40 * s * (1 - sit), 38 * s, 60 * s * sit + 25 * s * (1 - sit), 0.2, 0, Math.PI * 2);
  g.fillStyle = light;
  g.fill();
  // Полоски на спине.
  g.strokeStyle = dark;
  g.lineWidth = 9 * s;
  for (let i = 0; i < 3; i++) {
    g.beginPath();
    g.arc(40 * s, (-150 + i * 35) * s * sit - 40 * s * (1 - sit), 30 * s, -0.6, 0.5);
    g.stroke();
  }
  // Лапы.
  g.fillStyle = light;
  for (const dx of sit > 0.5 ? [-55, -20] : [-120, -90, 90, 120]) {
    g.beginPath();
    g.ellipse(dx * s, sit > 0.5 ? -8 * s : (5 + Math.abs(dx) * 0.12) * s, 22 * s, 13 * s, 0, 0, Math.PI * 2);
    g.fill();
    stroke(g, 5 * s);
  }
  g.restore();
  // Голова.
  const hx = -45 * s - 80 * s * (1 - sit), hy = -200 * s * sit - 80 * s * (1 - sit);
  g.save();
  g.translate(hx, hy);
  g.rotate(Math.sin(t * 0.8) * 0.05);
  const twitch = Math.max(0, Math.sin(t * 5.3)) > 0.97 ? 0.25 : 0;
  for (const side of [-1, 1]) {
    g.save();
    g.translate(side * 38 * s, -44 * s);
    g.rotate(side * (0.25 + (side > 0 ? twitch : 0)));
    g.beginPath();
    g.moveTo(-20 * s, 12 * s);
    g.lineTo(0, -40 * s);
    g.lineTo(22 * s, 12 * s);
    g.closePath();
    g.fillStyle = col;
    g.fill();
    stroke(g, 6 * s);
    g.beginPath();
    g.moveTo(-10 * s, 8 * s);
    g.lineTo(0, -22 * s);
    g.lineTo(11 * s, 8 * s);
    g.closePath();
    g.fillStyle = "#ffb3c8";
    g.fill();
    g.restore();
  }
  g.beginPath();
  g.ellipse(0, 0, 62 * s, 52 * s, 0, 0, Math.PI * 2);
  g.fillStyle = col;
  g.fill();
  stroke(g, 6 * s);
  g.beginPath();
  g.ellipse(0, 20 * s, 34 * s, 22 * s, 0, 0, Math.PI * 2);
  g.fillStyle = light;
  g.fill();
  // Глаза (моргает), нос, усы.
  const blink = ((t * 0.6) % 3) < 0.12 ? 0.15 : 1;
  for (const side of [-1, 1]) {
    g.beginPath();
    g.ellipse(side * 24 * s, -6 * s, 11 * s, 14 * s * blink, 0, 0, Math.PI * 2);
    g.fillStyle = "#3ec46a";
    g.fill();
    g.lineWidth = 3 * s;
    g.strokeStyle = LINE;
    g.stroke();
    if (blink > 0.5) {
      g.beginPath();
      g.ellipse(side * 24 * s, -6 * s, 4 * s, 11 * s, 0, 0, Math.PI * 2);
      g.fillStyle = LINE;
      g.fill();
      g.beginPath();
      g.arc(side * 24 * s - 3 * s, -11 * s, 3 * s, 0, Math.PI * 2);
      g.fillStyle = "#ffffff";
      g.fill();
    }
  }
  g.beginPath();
  g.moveTo(-6 * s, 12 * s);
  g.lineTo(6 * s, 12 * s);
  g.lineTo(0, 19 * s);
  g.closePath();
  g.fillStyle = "#ff8aa8";
  g.fill();
  g.strokeStyle = "rgba(255,255,255,0.8)";
  g.lineWidth = 2.5 * s;
  for (const side of [-1, 1]) {
    for (const k of [-1, 0, 1]) {
      g.beginPath();
      g.moveTo(side * 30 * s, 20 * s);
      g.lineTo(side * 80 * s, (20 + k * 10) * s);
      g.stroke();
    }
  }
  g.restore();
  void u;
}

export function drawCats(g: G, W: number, cats: CatSpec[] | undefined, t: number) {
  for (const c of cats || []) {
    if (t < c.t0 || t >= c.t1) continue;
    const s = (c.scale ?? 1) * (W / 1080);
    // Прыжок 0.55 с: из-под нижнего края по дуге на стол, приземление с лёгким «присестью».
    const u = (t - c.t0) / 0.55;
    const k = smooth(Math.min(1, u));
    const sx = c.x + (c.flip ? -1 : 1) * 260 * s, sy = c.y + 520 * s;
    const x = sx + (c.x - sx) * k;
    const y = sy + (c.y - sy) * k - Math.sin(Math.min(1, u) * Math.PI) * 260 * s;
    const sit = u < 0.8 ? 0 : smooth((u - 0.8) / 0.5);
    const land = u > 1 && u < 1.4 ? 1 - 0.06 * Math.sin(((u - 1) / 0.4) * Math.PI) : 1;
    g.save();
    // Тень на столе.
    if (u > 0.7) {
      g.globalAlpha = 0.35 * smooth((u - 0.7) / 0.3);
      glow(g, c.x, c.y, 150 * s, 26 * s, "#000000", 0.8);
      g.globalAlpha = 1;
    }
    g.translate(x, y);
    if (c.flip) g.scale(-1, 1);
    g.scale(1, land);
    drawCatBody(g, s, sit, t, c.color || "#f0a050", 1);
    g.restore();
  }
}
