/**
 * Интерфейс «Мира от зрителей» в стиле PS1-меню: окно с градиентом и двойной рамкой со срезанными углами,
 * пиксельные иконки (сердце, звезда, мешок — рисуются попиксельно), шкалы из сегментов, шрифт Press Start 2P
 * (OFL, vendor/fonts) в размере 8 px внутреннего разрешения — после увеличения ×4 пиксели ровные.
 * Окно раскрывается по вертикали ступеньками (как меню на PS1), текст появляется после рамки.
 */

export interface HudSpec {
  /** Когда появляется (с) и за сколько раскрывается. */
  t?: number;
  open?: number;
  likes?: number;
  likesGoal?: number;
  subs?: number;
  subsGoal?: number;
  inventory?: string[];
  x?: number;
  y?: number;
}

export const HUD_FONT = "VvfPixel";

let loading: Promise<void> | null = null;
export function loadHudFont(): Promise<void> {
  if (loading) return loading;
  loading = (async () => {
    try {
      const f = new FontFace(HUD_FONT, "url(http://scene3d.local/fonts/PressStart2P-Regular.ttf)");
      await f.load();
      (document as any).fonts.add(f);
    } catch {
      /* нет шрифта — моноширинный системный */
    }
  })();
  return loading;
}

const HEART = ["0110110", "1111111", "1111111", "0111110", "0011100", "0001000"];
const STAR = ["0001000", "0011100", "1111111", "0111110", "0110110", "1100011"];
const BAG = ["0011100", "0100010", "1111111", "1111111", "1111111", "0111110"];

function sprite(g: CanvasRenderingContext2D, rows: string[], x: number, y: number, col: string, dark: string) {
  for (let j = 0; j < rows.length; j++) {
    for (let i = 0; i < rows[j].length; i++) {
      if (rows[j][i] !== "1") continue;
      // Нижний ряд и правый край — темнее (объём, как у спрайтов той эпохи).
      g.fillStyle = j === rows.length - 1 || rows[j][i + 1] !== "1" ? dark : col;
      g.fillRect(x + i, y + j, 1, 1);
    }
  }
  g.fillStyle = "#ffffff";
  g.fillRect(x + 1, y + 1, 1, 1);
}

function text(g: CanvasRenderingContext2D, s: string, x: number, y: number, col: string) {
  g.font = `8px ${HUD_FONT}, monospace`;
  g.textBaseline = "top";
  g.fillStyle = "#000010";
  g.fillText(s, x + 1, y + 1);
  g.fillStyle = col;
  g.fillText(s, x, y);
}

function bar(g: CanvasRenderingContext2D, x: number, y: number, w: number, k: number, col: string) {
  const n = 10, seg = Math.floor((w - (n - 1)) / n);
  for (let i = 0; i < n; i++) {
    g.fillStyle = i < Math.round(k * n) ? col : "#283068";
    g.fillRect(x + i * (seg + 1), y, seg, 3);
  }
}

/** Ширина и высота окна (px внутреннего разрешения). */
export const HUD_W = 158, HUD_H = 62;

export function drawHud(g: CanvasRenderingContext2D, h: HudSpec, t: number) {
  const t0 = h.t ?? 0, open = h.open ?? 0.3;
  if (t < t0) return;
  const k = Math.min(1, (t - t0) / open);
  const x = h.x ?? 6, y = h.y ?? 8, W = HUD_W, H = HUD_H;
  // Раскрытие ступеньками по 4 px от середины.
  const hh = Math.max(4, Math.round((H * k) / 4) * 4);
  const top = y + Math.round((H - hh) / 2);
  const grad = g.createLinearGradient(0, top, 0, top + hh);
  grad.addColorStop(0, "rgba(40,56,160,0.62)");
  grad.addColorStop(1, "rgba(10,14,60,0.62)");
  g.fillStyle = grad;
  g.fillRect(x + 1, top + 1, W - 2, hh - 2);
  // Двойная рамка со срезанными углами.
  g.fillStyle = "#e8ecf8";
  g.fillRect(x + 2, top, W - 4, 1);
  g.fillRect(x + 2, top + hh - 1, W - 4, 1);
  g.fillRect(x, top + 2, 1, hh - 4);
  g.fillRect(x + W - 1, top + 2, 1, hh - 4);
  g.fillRect(x + 1, top + 1, 1, 1);
  g.fillRect(x + W - 2, top + 1, 1, 1);
  g.fillRect(x + 1, top + hh - 2, 1, 1);
  g.fillRect(x + W - 2, top + hh - 2, 1, 1);
  g.fillStyle = "#7080c8";
  g.fillRect(x + 2, top + 2, W - 4, 1);
  g.fillRect(x + 2, top + 2, 1, hh - 4);
  if (k < 1) return;
  const L = h.likes ?? 0, LG = h.likesGoal ?? 100, S = h.subs ?? 0, SG = h.subsGoal ?? 10;
  const cx = x + 7, rx = x + W - 7;
  const right = (s: string, yy: number, col: string) => text(g, s, rx - s.length * 8, yy, col);
  let yy = y + 6;
  sprite(g, HEART, cx, yy + 1, "#f83858", "#a01830");
  text(g, "ЛАЙКИ", cx + 10, yy, "#f8f8f8");
  right(`${L}/${LG}`, yy, "#f8d848");
  bar(g, cx + 10, yy + 11, W - 24, L / LG, "#f83858");
  yy += 19;
  sprite(g, STAR, cx, yy + 1, "#f8d030", "#b08010");
  text(g, "ПОДПИСЧ.", cx + 10, yy, "#f8f8f8");
  right(`${S}/${SG}`, yy, "#f8d848");
  bar(g, cx + 10, yy + 11, W - 24, S / SG, "#f8d030");
  yy += 19;
  sprite(g, BAG, cx, yy + 1, "#c88850", "#805028");
  const inv = h.inventory?.length ? h.inventory.join(", ") : "ПУСТО";
  text(g, "ИНВЕНТАРЬ:", cx + 10, yy, "#f8f8f8");
  text(g, inv, cx + 10 + 11 * 8, yy, h.inventory?.length ? "#a8f8a0" : "#8890c0");
}
