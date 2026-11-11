/**
 * Экранные надписи старой видеокамеры (поверх vhs.ts) в холсте интерфейса 270×480, шрифт Press Start 2P 8 px:
 *  - режим «запись»: мигающая красная точка и REC, значок батареи (к концу садится — «LOW BATT» и красная
 *    рамка), скобки видоискателя по углам, рамка автофокуса по центру (при поиске фокуса дёргается),
 *    штамп времени 03:17:сс — часы и минуты стоят, секунды идут — и дата;
 *  - режим «воспроизведение»: ▶ PLAY вместо REC, без скобок и автофокуса, штамп другой.
 * Подписи нарисованы с цветной каёмкой (красная слева, голубая справа) — как у камеры с плывущим цветом.
 */
import { HUD_FONT } from "./hud.ts";

export interface CamSpec {
  t0: number;
  t1?: number;
  mode: "rec" | "play";
  /** Штамп: «03:17» и секунды на старте (с), дата. */
  stamp: string;
  sec0?: number;
  date: string;
  /** Батарея садится с этого времени (с): три деления → одно мигающее. */
  lowAt?: number;
  /** Надпись севшей батареи (по умолчанию «LOW BATT», серия 2 — «БАТАРЕЯ», мигает). */
  lowText?: string;
}

const fontPx = (px: number) => `${px}px ${HUD_FONT}, monospace`;
const RED = "#e82c20", WHITE = "#ecebe2";

/** Подпись с каёмкой: красная копия на пиксель левее, голубая — правее, поверх — основной цвет. */
function fringe(g: CanvasRenderingContext2D, s: string, x: number, y: number, col = WHITE) {
  g.font = fontPx(8);
  g.textBaseline = "top";
  g.fillStyle = "#000000";
  g.fillText(s, x + 1, y + 1);
  g.fillStyle = "#e0301c";
  g.fillText(s, x - 1, y);
  g.fillStyle = "#30c8e8";
  g.fillText(s, x + 1, y);
  g.fillStyle = col;
  g.fillText(s, x, y);
}

/** Уголки-скобки: [x, y, длина, направление по x, по y]. */
function bracket(g: CanvasRenderingContext2D, x: number, y: number, len: number, sx: 1 | -1, sy: 1 | -1, th = 2) {
  g.fillStyle = WHITE;
  g.fillRect(sx > 0 ? x : x - len + 1, sy > 0 ? y : y - th + 1, len, th);
  g.fillRect(sx > 0 ? x : x - th + 1, sy > 0 ? y : y - len + 1, th, len);
}

export function drawCam(g: CanvasRenderingContext2D, c: CamSpec, t: number, hunting = false) {
  if (t < c.t0 || (c.t1 !== undefined && t >= c.t1)) return;
  const W = g.canvas.width, H = g.canvas.height;
  const dt = t - c.t0;
  const low = c.lowAt !== undefined && t >= c.lowAt;
  if (c.mode === "rec") {
    // Скобки видоискателя и рамка автофокуса.
    const m = 8, L = 14;
    bracket(g, m, m, L, 1, 1);
    bracket(g, W - m, m, L, -1, 1);
    bracket(g, m, H - m, L, 1, -1);
    bracket(g, W - m, H - m, L, -1, -1);
    const cx = W / 2, cy = H * 0.4, a = hunting ? 8 + Math.round(Math.sin(t * 40) * 3) : 11;
    if (!hunting || Math.floor(t * 12) % 2 === 0) {
      bracket(g, cx - a, cy - a, 5, 1, 1, 1);
      bracket(g, cx + a, cy - a, 5, -1, 1, 1);
      bracket(g, cx - a, cy + a, 5, 1, -1, 1);
      bracket(g, cx + a, cy + a, 5, -1, -1, 1);
    }
    // REC: точка мигает раз в секунду.
    if (Math.floor(dt) % 2 === 0) {
      g.fillStyle = RED;
      for (const [x, y, w, h] of [[22, 22, 5, 7], [20, 24, 9, 3], [21, 23, 7, 5]]) g.fillRect(x, y, w, h);
    }
    fringe(g, "REC", 34, 23);
  } else {
    g.fillStyle = WHITE;
    for (let i = 0; i < 4; i++) g.fillRect(22 + i, 22 + i, 1, 7 - 2 * i);
    fringe(g, "PLAY", 32, 23);
  }
  // Батарея: корпус 22×10 с язычком, три деления; на «севшей» — одно, мигает красным.
  const bx = W - 42, by = 21;
  g.fillStyle = low && Math.floor(t * 2.5) % 2 === 0 ? RED : WHITE;
  g.fillRect(bx, by, 22, 1);
  g.fillRect(bx, by + 9, 22, 1);
  g.fillRect(bx, by, 1, 10);
  g.fillRect(bx + 21, by, 1, 10);
  g.fillRect(bx + 22, by + 3, 2, 4);
  const seg = low ? 1 : 3;
  for (let i = 0; i < seg; i++) g.fillRect(bx + 3 + i * 6, by + 3, 5, 4);
  if (low && (!c.lowText || Math.floor(t * 2.5) % 2 === 0)) {
    const s = c.lowText ?? "LOW BATT";
    g.font = fontPx(8);
    fringe(g, s, W - 20 - Math.ceil(g.measureText(s).width), 36, RED);
  }
  // Штамп: часы и минуты стоят на 3:17, секунды идут. Дата справа.
  const sec = Math.floor((c.sec0 ?? 0) + dt) % 60;
  fringe(g, `${c.stamp}:${String(sec).padStart(2, "0")}`, 20, H - 118);
  fringe(g, c.date, W - 20 - 8 * c.date.length, H - 118);
}
