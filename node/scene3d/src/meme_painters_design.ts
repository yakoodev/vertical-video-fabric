/**
 * Картинка мем-серии 11 «Дизайн отдел» (экран ноутбука дизайнера; подключена в PROMPT_PAINTERS meme_painters_prompt.ts):
 *
 * - ``design_banner`` — окно редактора макетов: слева слои, в центре артборд-баннер (градиент, заголовок «SALE −50%»,
 *   кнопка), справа панель свойств с размером «W × H». ``w``/``h`` — размер артборда; ``grow: [{t, w, h}]`` — с момента
 *   ``t`` размер другой (подпись мигает маркером: «на один размер увеличить»).
 */
import { rr } from "./meme_painters_rp.ts";

type G = CanvasRenderingContext2D;
type Painter = (g: G, W: number, H: number, t: number, p: Record<string, any>) => void;

const FONT = (w: number, px: number) => `${w} ${Math.round(px)}px Montserrat, Arial, sans-serif`;

/** Размер артборда в момент t: последний шаг ``grow`` с t ≤ t, иначе исходный. */
export function boardSize(p: Record<string, any>, t: number): { w: number; h: number; since: number } {
  let w = Number(p.w ?? 1920), h = Number(p.h ?? 1080), since = -1;
  for (const s of p.grow || []) if (t >= s.t) ({ w, h, since } = { w: Number(s.w), h: Number(s.h), since: Number(s.t) });
  return { w, h, since };
}

const designBanner: Painter = (g, W, H, t, p) => {
  const s = W / 1280;
  g.fillStyle = "#1e1f24";
  g.fillRect(0, 0, W, H);
  // Шапка и панели.
  g.fillStyle = "#2b2c33";
  g.fillRect(0, 0, W, 64 * s);
  g.fillRect(0, 64 * s, 230 * s, H);
  g.fillRect(W - 280 * s, 64 * s, 280 * s, H);
  for (const [i, c] of ["#ff6b6b", "#ffd166", "#06d6a0"].entries()) {
    g.fillStyle = c;
    g.beginPath();
    g.arc((28 + i * 28) * s, 32 * s, 9 * s, 0, Math.PI * 2);
    g.fill();
  }
  g.fillStyle = "#c9cbd3";
  g.font = FONT(700, 24 * s);
  g.textBaseline = "middle";
  g.fillText("banner_final_v7.fig", 130 * s, 32 * s);
  // Слои.
  g.font = FONT(600, 20 * s);
  ["Frame 1", "Фон", "Заголовок", "Кнопка", "Логотип"].forEach((n, i) => {
    if (i === 0) {
      g.fillStyle = "#3d5afe55";
      g.fillRect(8 * s, (84 + i * 40) * s, 214 * s, 34 * s);
    }
    g.fillStyle = i === 0 ? "#ffffff" : "#a7aab5";
    g.fillText(n, (24 + (i ? 20 : 0)) * s, (101 + i * 40) * s);
  });
  // Артборд.
  const bx = 270 * s, by = 120 * s, bw = W - 590 * s, bh = bw * 9 / 16;
  const gr = g.createLinearGradient(bx, by, bx + bw, by + bh);
  gr.addColorStop(0, "#ff7a59");
  gr.addColorStop(1, "#7b2ff7");
  g.fillStyle = gr;
  g.fillRect(bx, by, bw, bh);
  g.fillStyle = "#ffffff";
  g.font = FONT(900, 64 * s);
  g.textAlign = "center";
  g.fillText("SALE −50%", bx + bw / 2, by + bh * 0.42);
  rr(g, bx + bw / 2 - 110 * s, by + bh * 0.62, 220 * s, 56 * s, 28 * s);
  g.fill();
  g.fillStyle = "#7b2ff7";
  g.font = FONT(800, 26 * s);
  g.fillText("КУПИТЬ", bx + bw / 2, by + bh * 0.62 + 28 * s);
  g.strokeStyle = "#3d8bff";
  g.lineWidth = 3 * s;
  g.strokeRect(bx, by, bw, bh);
  // Размер под артбордом и в свойствах.
  const { w, h, since } = boardSize(p, t);
  const flash = since >= 0 ? Math.max(0, 1 - (t - since) / 0.8) : 0;
  const label = `${w} × ${h}`;
  g.font = FONT(800, 26 * s);
  const lw = g.measureText(label).width + 28 * s;
  g.fillStyle = flash > 0 ? `rgba(255,210,63,${0.4 + 0.6 * flash})` : "#3d8bff";
  rr(g, bx + bw / 2 - lw / 2, by + bh + 16 * s, lw, 40 * s, 8 * s);
  g.fill();
  g.fillStyle = flash > 0 ? "#1e1f24" : "#ffffff";
  g.fillText(label, bx + bw / 2, by + bh + 36 * s);
  g.textAlign = "left";
  g.fillStyle = "#a7aab5";
  g.font = FONT(600, 20 * s);
  const px = W - 260 * s;
  g.fillText("Frame", px, 100 * s);
  [["W", w], ["H", h]].forEach(([k, v], i) => {
    g.fillStyle = "#383a43";
    rr(g, px + i * 125 * s, 120 * s, 110 * s, 40 * s, 6 * s);
    g.fill();
    g.fillStyle = "#e7e8ee";
    g.font = FONT(700, 20 * s);
    g.fillText(`${k}  ${v}`, px + i * 125 * s + 12 * s, 140 * s);
  });
  g.textBaseline = "alphabetic";
};

export const DESIGN_PAINTERS: Record<string, Painter> = { design_banner: designBanner };
