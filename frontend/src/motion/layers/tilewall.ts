/**
 * Наклонная стена плиток (`tile_wall`): сетка картинок (обложки, значки приложений, скриншоты),
 * повёрнутая в плоскости (`tiltZ`) и наклонённая вглубь (`tiltX`) с перспективой, бесконечно
 * едет по диагонали; сверху — затемнение под текст. Без картинок — свои плитки-значки (градиент +
 * векторный значок) цветами `colors`.
 *
 * Плоскость стены бесконечная: плитка (i, j) стоит в (i·шаг + сдвиг, j·шаг + сдвиг), сдвиг растёт
 * со временем, картинка плитки — от хэша (i, j): петля бесшовная, ничего не «перескакивает».
 * Каждая плитка рисуется своим аффинным преобразованием по проекции углов (ошибка перспективы
 * внутри плитки < 1–2 px), рамка слоя w×h обрезает стену. Чистая функция props и времени.
 */
import { clamp01 } from "../easing";
import { hashString, rand01 } from "../keys";
import type { Ctx2D, TileWallLayer, TileWallProps } from "../types";
import { AD_ICONS, drawAdIcon } from "./adicons";
import { drawCover, mixHex } from "./adkit";
import { clearShadow, rgba, roundRectPath, type LayerRenderer } from "./common";

export const TILE_COLORS = ["#6c5cff", "#ff5c8a", "#22b8e6", "#ffb020", "#2ec987", "#ff7a45", "#9b6bff", "#3b82f6"];
/** Фокусное стены, опорн. px — как у камеры покоя движка (кадр 40° по вертикали). */
const FOCAL = 2638;

type Grid = { cell: number; tile: number; tiltX: number; tiltZ: number; cos: number; sin: number; ct: number; st: number };

function grid(p: TileWallProps): Grid {
  const tile = Math.max(40, p.tile ?? 230);
  const gap = Math.max(0, p.gap ?? 34);
  const tiltX = ((p.tiltX ?? 26) * Math.PI) / 180;
  const tiltZ = ((p.tiltZ ?? -16) * Math.PI) / 180;
  return { cell: tile + gap, tile, tiltX, tiltZ, cos: Math.cos(tiltZ), sin: Math.sin(tiltZ), ct: Math.cos(tiltX), st: Math.sin(tiltX) };
}

/** Точка стены (u, v — опорн. px в сетке) → точка слоя с перспективой; null — за камерой. */
export function wallProject(g: Grid, u: number, v: number): [number, number] | null {
  const a = u * g.cos - v * g.sin;
  const b = u * g.sin + v * g.cos;
  // Наклон: верх (b < 0) уходит вглубь.
  const y = b * g.ct;
  const z = -b * g.st;
  const d = FOCAL + z;
  if (d < FOCAL * 0.1) return null;
  const k = FOCAL / d;
  return [a * k, y * k];
}

/** Обратная проекция точки слоя на стену (для выбора видимых плиток). */
function wallUnproject(g: Grid, X: number, Y: number): [number, number] {
  const b = (Y * FOCAL) / (g.ct * FOCAL + Y * g.st);
  const a = (X * (FOCAL - b * g.st)) / FOCAL;
  return [a * g.cos + b * g.sin, -a * g.sin + b * g.cos];
}

/** Сдвиг стены в момент `local`, опорн. px по осям сетки. */
export function wallShift(p: TileWallProps, local: number): [number, number] {
  const speed = p.speed ?? 46;
  const ang = ((p.angle ?? -45) * Math.PI) / 180;
  return [Math.cos(ang) * speed * local, Math.sin(ang) * speed * local];
}

function placeholderTile(ctx: Ctx2D, size: number, radius: number, i: number, j: number, seed: number, colors: string[]): void {
  const h = hashString(`${i}:${j}:${seed}`);
  const c1 = colors[h % colors.length];
  const c2 = mixHex(c1, colors[(h >>> 5) % colors.length], 0.55);
  const g = ctx.createLinearGradient(0, 0, size, size);
  g.addColorStop(0, rgba(mixHex(c1, "#ffffff", 0.18)));
  g.addColorStop(1, rgba(mixHex(c2, "#000000", 0.25)));
  ctx.beginPath();
  roundRectPath(ctx, 0, 0, size, size, radius);
  ctx.fillStyle = g;
  ctx.fill();
  // Блик сверху — плитка «стеклянная», как значок приложения.
  const gloss = ctx.createLinearGradient(0, 0, 0, size * 0.55);
  gloss.addColorStop(0, "rgba(255,255,255,0.22)");
  gloss.addColorStop(1, "rgba(255,255,255,0)");
  ctx.fillStyle = gloss;
  ctx.fill();
  drawAdIcon(ctx, AD_ICONS[(h >>> 9) % AD_ICONS.length], size / 2, size / 2, size * 0.46, "rgba(255,255,255,0.94)");
}

export const tileWallRenderer: LayerRenderer<TileWallLayer> = {
  multipart: true,
  measure(_ctx, layer) {
    const p = layer.props;
    return { w: Math.max(100, p.w ?? 1080), h: Math.max(100, p.h ?? 1920) };
  },
  draw(ctx, layer, st, env) {
    const p = layer.props;
    const W = Math.max(100, p.w ?? 1080);
    const H = Math.max(100, p.h ?? 1920);
    const g = grid(p);
    const seed = Math.round(p.seed ?? 3);
    const images = (Array.isArray(p.images) ? p.images : []).map((ref) => env.getImage(String(ref)));
    const colors = Array.isArray(p.colors) && p.colors.length ? p.colors : TILE_COLORS;
    const radius = g.tile * Math.max(0, Math.min(0.5, p.radius ?? 0.24));
    const [ou, ov] = wallShift(p, st.local);
    clearShadow(ctx);
    ctx.save();
    ctx.beginPath();
    ctx.rect(-W / 2, -H / 2, W, H);
    ctx.clip();
    // Видимая часть стены: углы рамки слоя назад на стену (+ запас в клетку).
    const corners = [
      [-W / 2, -H / 2],
      [W / 2, -H / 2],
      [W / 2, H / 2],
      [-W / 2, H / 2],
    ].map(([x, y]) => wallUnproject(g, x, y));
    const us = corners.map((c) => c[0]);
    const vs = corners.map((c) => c[1]);
    const i0 = Math.floor((Math.min(...us) - ou) / g.cell) - 1;
    const i1 = Math.ceil((Math.max(...us) - ou) / g.cell) + 1;
    const j0 = Math.floor((Math.min(...vs) - ov) / g.cell) - 1;
    const j1 = Math.ceil((Math.max(...vs) - ov) / g.cell) + 1;
    const T = g.tile;
    for (let j = j0; j <= j1 && j - j0 < 80; j++) {
      for (let i = i0; i <= i1 && i - i0 < 80; i++) {
        const u = i * g.cell + ou - g.cell / 2;
        const v = j * g.cell + ov - g.cell / 2;
        const p00 = wallProject(g, u, v);
        const p10 = wallProject(g, u + T, v);
        const p01 = wallProject(g, u, v + T);
        const p11 = wallProject(g, u + T, v + T);
        if (!p00 || !p10 || !p01 || !p11) continue;
        const xs = [p00[0], p10[0], p01[0], p11[0]];
        const ys = [p00[1], p10[1], p01[1], p11[1]];
        if (Math.max(...xs) < -W / 2 || Math.min(...xs) > W / 2 || Math.max(...ys) < -H / 2 || Math.min(...ys) > H / 2) continue;
        // Дальние плитки (верх стены) чуть темнее — глубина читается и без тумана.
        const b = (u + T / 2) * g.sin + (v + T / 2) * g.cos;
        const depth = clamp01((-b * g.st + 300) / 1400);
        ctx.save();
        ctx.globalAlpha *= 1 - 0.38 * depth;
        ctx.transform((p10[0] - p00[0]) / T, (p10[1] - p00[1]) / T, (p01[0] - p00[0]) / T, (p01[1] - p00[1]) / T, p00[0], p00[1]);
        const img = images.length ? images[Math.floor(rand01(seed, i * 7919 + j * 104729) * images.length)] : null;
        if (img) {
          ctx.beginPath();
          roundRectPath(ctx, 0, 0, T, T, radius);
          ctx.clip();
          drawCover(ctx, img, 0, 0, T, T, env);
        } else placeholderTile(ctx, T, radius, i, j, seed, colors);
        ctx.restore();
      }
    }
    // Затемнение под текст: ровный слой + виньетка к краям.
    const op = clamp01(p.overlay ?? 0.55);
    if (op > 0) {
      const color = p.overlayColor || "#06070c";
      ctx.fillStyle = rgba(color, op * 0.72);
      ctx.fillRect(-W / 2, -H / 2, W, H);
      const vg = ctx.createRadialGradient(0, 0, Math.min(W, H) * 0.25, 0, 0, Math.max(W, H) * 0.62);
      vg.addColorStop(0, rgba(color, 0));
      vg.addColorStop(1, rgba(color, Math.min(1, op * 0.9)));
      ctx.fillStyle = vg;
      ctx.fillRect(-W / 2, -H / 2, W, H);
    }
    ctx.restore();
  },
};
