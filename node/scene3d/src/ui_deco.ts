/**
 * Оформление окна эфира (истории «Витубики», StreamScreen.decor): три вида, меняются по времени.
 *
 *   retro         — старый эфир: чёрный фон, «тот самый шрифт» (Comic Sans), рамка вебки в огне;
 *   studio        — анимированная музыкальная комната за героиней (окно с закатом, гирлянда флажков, проигрыватель
 *                   с крутящейся пластинкой, колонки в такт, гитара) и попкорн в углу: хлопает на каждое сообщение чата;
 *   studio_ember  — то же и совсем маленький огонёк в левом углу (тот самый огонь, но «чуть-чуть»).
 *
 * Это наши картинки «по мотивам» (рисуются кодом), а не картинки товаров магазина: настоящие страницы товаров — только
 * в телефоне-вставке (ui_site.ts). Всё — функция времени t, без накопленного состояния.
 */
type G = CanvasRenderingContext2D;

export type Decor = "plain" | "retro" | "studio" | "studio_ember" | "retro_frame";
export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

const KINDS = new Set<string>(["retro", "studio", "studio_ember"]);

/** Оформление на момент t (последний ключ ≤ t); нет ключей — обычный эфир. */
export function decorAt(keys: { t: number; kind: string }[] | undefined, t: number): Decor {
  let v = "plain";
  for (const k of keys || []) if (k.t <= t + 1e-9) v = KINDS.has(k.kind) ? k.kind : "plain";
  return v as Decor;
}

/** «Тот самый шрифт» старого эфира (системный Comic Sans, есть на машине сборки; запасные — похожие рукописные). */
export function retroFamily(_g: G): string {
  return `"Comic Sans MS", "Comic Sans", "Chalkboard SE", cursive`;
}

const hash = (n: number) => {
  const s = Math.sin(n * 127.1 + 311.7) * 43758.5453;
  return s - Math.floor(s);
};
const clamp = (x: number, a = 0, b = 1) => Math.max(a, Math.min(b, x));

/* ---------------- огонь ---------------- */

/** Один язык пламени: основание (bx, by), рост по (nx, ny), три слоя — красный, оранжевый, жёлтый. */
function tongue(g: G, bx: number, by: number, nx: number, ny: number, len: number, wid: number, ph: number, t: number) {
  const px = -ny, py = nx;
  const flick = 0.78 + 0.22 * Math.sin(t * 9 + ph) + 0.1 * Math.sin(t * 17 + ph * 2.3);
  const sway = Math.sin(t * 5 + ph * 1.7) * wid * 0.8;
  const layers: [string, number, number][] = [["#d8260f", 1, 1], ["#ff7a14", 0.78, 0.72], ["#ffd34a", 0.5, 0.45]];
  for (const [col, ls, ws] of layers) {
    const l = len * flick * ls, w = wid * ws;
    const tipx = bx + nx * l + px * sway * ls, tipy = by + ny * l + py * sway * ls;
    g.fillStyle = col;
    g.beginPath();
    g.moveTo(bx - px * w, by - py * w);
    g.bezierCurveTo(bx - px * w * 1.25 + nx * l * 0.4, by - py * w * 1.25 + ny * l * 0.4,
      bx + nx * l * 0.72 + px * sway * 0.5, by + ny * l * 0.72 + py * sway * 0.5, tipx, tipy);
    g.bezierCurveTo(bx + nx * l * 0.72 + px * sway * 0.5, by + ny * l * 0.72 + py * sway * 0.5,
      bx + px * w * 1.25 + nx * l * 0.4, by + py * w * 1.25 + ny * l * 0.4, bx + px * w, by + py * w);
    g.closePath();
    g.fill();
  }
}

/** Рамка вебки в огне: свечение, раскалённая кайма, языки пламени по трём сторонам, искры вверх. */
function retroFrame(g: G, r: Rect, u: number, t: number) {
  g.save();
  g.shadowColor = "rgba(255,110,20,0.95)";
  g.shadowBlur = 34 * u;
  g.lineWidth = 9 * u;
  const rim = g.createLinearGradient(0, r.y, 0, r.y + r.h);
  rim.addColorStop(0, "#ffb030");
  rim.addColorStop(0.5, "#ff6a14");
  rim.addColorStop(1, "#c8200c");
  g.strokeStyle = rim;
  g.beginPath();
  g.roundRect(r.x, r.y, r.w, r.h, 14 * u);
  g.stroke();
  g.shadowBlur = 0;
  // Языки: сверху вверх, по бокам наружу и вверх.
  const step = 44 * u;
  const nTop = Math.max(6, Math.round(r.w / step));
  for (let i = 0; i < nTop; i++) {
    const k = hash(i + 3);
    const x = r.x + ((i + 0.5) / nTop) * r.w;
    tongue(g, x, r.y + 3 * u, Math.sin(t * 2 + i) * 0.12, -1, (34 + 40 * k) * u, (16 + 7 * k) * u, i * 1.9, t);
  }
  const nSide = Math.max(4, Math.round(r.h / (52 * u)));
  for (let i = 0; i < nSide; i++) {
    const k = hash(i + 41);
    const y = r.y + ((i + 0.6) / (nSide + 0.2)) * r.h;
    const len = (22 + 22 * k) * u, wid = (12 + 5 * k) * u;
    tongue(g, r.x + 3 * u, y, -0.55, -0.83, len, wid, i * 2.3 + 1, t);
    tongue(g, r.x + r.w - 3 * u, y, 0.55, -0.83, len, wid, i * 2.1 + 5, t);
  }
  // Низ — тлеющая линия.
  g.strokeStyle = "rgba(255,90,20,0.8)";
  g.lineWidth = 4 * u;
  g.beginPath();
  g.moveTo(r.x + 14 * u, r.y + r.h);
  g.lineTo(r.x + r.w - 14 * u, r.y + r.h);
  g.stroke();
  // Искры.
  for (let i = 0; i < 22; i++) {
    const life = (t * (0.35 + 0.3 * hash(i)) + hash(i + 9)) % 1;
    const x = r.x + hash(i + 17) * r.w + Math.sin(t * 3 + i) * 12 * u;
    const y = r.y - life * 150 * u;
    g.globalAlpha = (1 - life) * 0.9;
    g.fillStyle = life < 0.5 ? "#ffd34a" : "#ff8a2a";
    g.beginPath();
    g.arc(x, y, (2 + 3 * hash(i + 2)) * u * (1 - life * 0.6), 0, Math.PI * 2);
    g.fill();
  }
  g.restore();
}

/** Маленький огонёк в углу: два язычка над парой угольков. */
function ember(g: G, cx: number, cy: number, s: number, t: number) {
  g.save();
  g.fillStyle = "rgba(255,120,30,0.18)";
  g.beginPath();
  g.arc(cx, cy - s * 0.4, s * 1.5, 0, Math.PI * 2);
  g.fill();
  g.fillStyle = "#3a2218";
  g.beginPath();
  g.roundRect(cx - s * 0.62, cy - s * 0.12, s * 1.24, s * 0.26, s * 0.12);
  g.fill();
  g.fillStyle = "#ff6a1a";
  g.beginPath();
  g.roundRect(cx - s * 0.5, cy - s * 0.1, s, s * 0.1, s * 0.05);
  g.fill();
  tongue(g, cx - s * 0.18, cy - s * 0.1, -0.1, -1, s * 0.95, s * 0.26, 0.4, t);
  tongue(g, cx + s * 0.2, cy - s * 0.1, 0.12, -1, s * 0.72, s * 0.22, 2.1, t);
  g.restore();
}

/* ---------------- музыкальная комната ---------------- */

function vinyl(g: G, cx: number, cy: number, rx: number, ry: number, ang: number) {
  g.fillStyle = "#14101c";
  g.beginPath();
  g.ellipse(cx, cy, rx, ry, 0, 0, Math.PI * 2);
  g.fill();
  // Блик канавок бежит по кругу — пластинка крутится.
  g.strokeStyle = "rgba(255,255,255,0.22)";
  g.lineWidth = Math.max(1, rx * 0.05);
  for (let k = 0; k < 2; k++) {
    g.beginPath();
    g.ellipse(cx, cy, rx * (0.8 - k * 0.2), ry * (0.8 - k * 0.2), 0, ang + k * 2.6, ang + k * 2.6 + 1.0);
    g.stroke();
  }
  g.fillStyle = "#d8432c";
  g.beginPath();
  g.ellipse(cx, cy, rx * 0.33, ry * 0.33, 0, 0, Math.PI * 2);
  g.fill();
  g.fillStyle = "#fff4e0";
  g.beginPath();
  g.arc(cx + Math.cos(ang) * rx * 0.2, cy + Math.sin(ang) * ry * 0.2, Math.max(1.5, rx * 0.05), 0, Math.PI * 2);
  g.fill();
}

function guitar(g: G, x: number, y: number, s: number) {
  g.save();
  g.translate(x, y);
  g.rotate(0.16);
  g.fillStyle = "#c27a3c";
  g.beginPath();
  g.ellipse(0, 0, s * 0.28, s * 0.3, 0, 0, Math.PI * 2);
  g.ellipse(0, -s * 0.34, s * 0.21, s * 0.22, 0, 0, Math.PI * 2);
  g.fill();
  g.fillStyle = "#3a2214";
  g.beginPath();
  g.arc(0, -s * 0.08, s * 0.08, 0, Math.PI * 2);
  g.fill();
  g.fillRect(-s * 0.04, -s * 1.05, s * 0.08, s * 0.7);
  g.fillRect(-s * 0.07, -s * 1.2, s * 0.14, s * 0.18);
  g.restore();
}

function studioBg(g: G, r: Rect, u: number, t: number) {
  const { x, y, w, h } = r;
  const wall = g.createLinearGradient(0, y, 0, y + h);
  wall.addColorStop(0, "#4a3466");
  wall.addColorStop(0.55, "#8a5a78");
  wall.addColorStop(1, "#d49a78");
  g.fillStyle = wall;
  g.fillRect(x, y, w, h);
  const fy = y + h * 0.8;
  const floor = g.createLinearGradient(0, fy, 0, y + h);
  floor.addColorStop(0, "#7a5034");
  floor.addColorStop(1, "#3f2a1e");
  g.fillStyle = floor;
  g.fillRect(x, fy, w, y + h - fy);
  // Окно: закат над городом, мерцающие звёзды, переплёт.
  const wx = x + w * 0.05, wy = y + h * 0.13, ww = w * 0.23, wh = h * 0.46;
  const sky = g.createLinearGradient(0, wy, 0, wy + wh);
  sky.addColorStop(0, "#1c2a5a");
  sky.addColorStop(0.6, "#8a5aa0");
  sky.addColorStop(1, "#ff9a6a");
  g.fillStyle = sky;
  g.fillRect(wx, wy, ww, wh);
  for (let i = 0; i < 9; i++) {
    g.globalAlpha = 0.4 + 0.6 * Math.abs(Math.sin(t * 1.5 + i * 2.1));
    g.fillStyle = "#fff6e0";
    g.beginPath();
    g.arc(wx + hash(i) * ww, wy + hash(i + 8) * wh * 0.5, 1.6 * u, 0, Math.PI * 2);
    g.fill();
  }
  g.globalAlpha = 1;
  g.fillStyle = "rgba(40,20,60,0.85)";
  for (let i = 0; i < 6; i++) {
    const bw = ww / 6, bh = wh * (0.12 + 0.2 * hash(i + 21));
    g.fillRect(wx + i * bw, wy + wh - bh, bw - 2 * u, bh);
  }
  g.strokeStyle = "#f4e6d8";
  g.lineWidth = 5 * u;
  g.strokeRect(wx, wy, ww, wh);
  g.beginPath();
  g.moveTo(wx + ww / 2, wy);
  g.lineTo(wx + ww / 2, wy + wh);
  g.moveTo(wx, wy + wh * 0.5);
  g.lineTo(wx + ww, wy + wh * 0.5);
  g.stroke();
  // Лучи света из окна (чуть дышат).
  const breath = 0.05 + 0.025 * Math.sin(t * 0.9);
  g.fillStyle = `rgba(255,214,160,${breath})`;
  for (let k = 0; k < 3; k++) {
    g.beginPath();
    g.moveTo(wx + ww, wy + wh * (0.1 + k * 0.3));
    g.lineTo(x + w * 0.62, y + h * (0.45 + k * 0.28));
    g.lineTo(x + w * 0.5, y + h * (0.62 + k * 0.28));
    g.lineTo(wx + ww, wy + wh * (0.3 + k * 0.3));
    g.closePath();
    g.fill();
  }
  // Гирлянда флажков под потолком, качается.
  const cols = ["#ff8ad8", "#ffd35c", "#7ee0ff", "#9dff7e", "#c9a0ff"];
  const n = 14;
  for (let i = 0; i < n; i++) {
    const f = (i + 0.5) / n;
    const px = x + f * w, py = y + h * 0.045 + Math.sin(f * Math.PI) * h * 0.05;
    const sw = Math.sin(t * 1.6 + i * 0.7) * 0.12;
    g.fillStyle = cols[i % cols.length];
    g.beginPath();
    g.moveTo(px - w / n * 0.32, py);
    g.lineTo(px + w / n * 0.32, py);
    g.lineTo(px + sw * h * 0.1, py + h * 0.075);
    g.closePath();
    g.fill();
  }
  // Полка с пластинками (справа вверху) и гитара.
  const sx = x + w * 0.72, sy = y + h * 0.3;
  g.fillStyle = "#4a2e1c";
  g.fillRect(sx, sy + h * 0.1, w * 0.24, 6 * u);
  const sleeve = ["#e84a6a", "#ffd35c", "#4ab8e8", "#7ed89a", "#c9a0ff", "#ff9a3d"];
  for (let i = 0; i < 6; i++) {
    g.fillStyle = sleeve[i];
    g.fillRect(sx + i * w * 0.04, sy - h * 0.02 * (i % 2), w * 0.034, h * 0.12 + h * 0.02 * (i % 2));
  }
  guitar(g, x + w * 0.88, y + h * 0.7, h * 0.34);
  // Колонки (пульс в такт ~2 Гц).
  const pulse = Math.pow(Math.max(0, Math.sin(t * Math.PI * 2 * 1.9)), 3);
  for (let k = 0; k < 2; k++) {
    const bx = x + w * (0.745 + k * 0.1), bw = w * 0.092, by = y + h * (0.5 + k * 0.08), bh = h * (0.27 - k * 0.08);
    g.fillStyle = "#1a1620";
    g.fillRect(bx, by, bw, bh);
    g.fillStyle = "#2c2634";
    g.beginPath();
    g.arc(bx + bw / 2, by + bh * 0.62, bw * (0.3 + 0.03 * pulse), 0, Math.PI * 2);
    g.fill();
    g.fillStyle = "#4a4258";
    g.beginPath();
    g.arc(bx + bw / 2, by + bh * 0.62, bw * (0.12 + 0.02 * pulse), 0, Math.PI * 2);
    g.fill();
  }
  // Тумба с проигрывателем слева внизу.
  const cx0 = x + w * 0.04, cw = w * 0.27, cy0 = y + h * 0.66;
  g.fillStyle = "#5a3a24";
  g.fillRect(cx0, cy0, cw, y + h * 0.86 - cy0);
  g.fillStyle = "#2a2430";
  g.fillRect(cx0 - 4 * u, cy0 - h * 0.05, cw + 8 * u, h * 0.06);
  vinyl(g, cx0 + cw * 0.42, cy0 - h * 0.022, cw * 0.28, h * 0.034, t * 3.4);
  g.strokeStyle = "#d8d0e0";
  g.lineWidth = 3 * u;
  g.beginPath();
  g.moveTo(cx0 + cw * 0.9, cy0 - h * 0.05);
  g.lineTo(cx0 + cw * 0.62, cy0 - h * 0.02);
  g.stroke();
  // Пылинки в луче света.
  for (let i = 0; i < 18; i++) {
    const life = (t * 0.06 * (0.5 + hash(i)) + hash(i + 5)) % 1;
    g.globalAlpha = 0.55 * Math.sin(life * Math.PI);
    g.fillStyle = "#ffe8c8";
    g.beginPath();
    g.arc(x + (0.18 + 0.5 * hash(i + 11) + Math.sin(t + i) * 0.01) * w, y + (0.1 + 0.8 * life) * h, 2.2 * u, 0, Math.PI * 2);
    g.fill();
  }
  g.globalAlpha = 1;
}

/** Попкорн: полосатое ведро с горкой, на каждое сообщение чата несколько зёрен вылетают и падают обратно. */
function popcorn(g: G, r: Rect, u: number, t: number, chat: { t: number }[]) {
  const bw = r.w * 0.17, bh = r.h * 0.27;
  const cx = r.x + r.w * 0.865, base = r.y + r.h - 6 * u;
  const last = chat.filter((m) => m.t <= t).map((m) => m.t).pop();
  const hit = last === undefined ? 9 : t - last;
  const jig = hit < 0.3 ? Math.sin(hit * 38) * (1 - hit / 0.3) * 0.07 : 0;
  g.save();
  g.translate(cx, base);
  g.rotate(jig);
  g.translate(-cx, -base);
  const top = base - bh;
  // Горка попкорна над ободом.
  for (let i = 0; i < 11; i++) {
    const a = hash(i + 60), b = hash(i + 70);
    g.fillStyle = i % 3 ? "#fff1c4" : "#ffe08a";
    g.beginPath();
    g.arc(cx + (a - 0.5) * bw * 0.9, top - 4 * u - b * bh * 0.2, bw * (0.1 + 0.05 * a), 0, Math.PI * 2);
    g.fill();
  }
  // Ведро: трапеция в красно-белую полоску.
  const tw = bw, bwb = bw * 0.78;
  g.beginPath();
  g.moveTo(cx - tw / 2, top);
  g.lineTo(cx + tw / 2, top);
  g.lineTo(cx + bwb / 2, base);
  g.lineTo(cx - bwb / 2, base);
  g.closePath();
  g.save();
  g.clip();
  const stripes = 6;
  for (let i = 0; i < stripes; i++) {
    g.fillStyle = i % 2 ? "#fff4ea" : "#d8283a";
    g.fillRect(cx - tw / 2 + (i * tw) / stripes, top, tw / stripes + 1, bh);
  }
  g.restore();
  g.strokeStyle = "#7a1420";
  g.lineWidth = 3 * u;
  g.beginPath();
  g.moveTo(cx - tw / 2, top);
  g.lineTo(cx + tw / 2, top);
  g.lineTo(cx + bwb / 2, base);
  g.lineTo(cx - bwb / 2, base);
  g.closePath();
  g.stroke();
  g.restore();
  // Зёрна: вылет вверх по параболе (на каждое сообщение — своя пачка).
  for (const m of chat) {
    const u0 = t - m.t;
    if (u0 < 0 || u0 > 1.1) continue;
    for (let i = 0; i < 7; i++) {
      const sd = m.t * 7.3 + i * 3.7;
      const vx = (hash(sd) - 0.5) * bw * 2.4, vy = -(0.8 + 0.7 * hash(sd + 1)) * r.h * 1.1;
      const px = cx + vx * u0, py = top + vy * u0 + 0.5 * r.h * 3.4 * u0 * u0;
      if (py > base) continue;
      g.fillStyle = i % 2 ? "#fff1c4" : "#ffe08a";
      g.beginPath();
      g.arc(px, py, bw * 0.075, 0, Math.PI * 2);
      g.arc(px + bw * 0.05, py - bw * 0.03, bw * 0.06, 0, Math.PI * 2);
      g.fill();
    }
  }
}

/** Фон окна эфира (до аватара): чёрный старый эфир или музыкальная комната. */
export function drawDecorBack(g: G, kind: Decor, r: Rect, u: number, t: number, _chat: { t: number }[]) {
  if (kind === "retro") {
    g.fillStyle = "#000";
    g.fillRect(r.x, r.y, r.w, r.h);
    g.fillStyle = "rgba(255,255,255,0.035)";
    for (let y = r.y; y < r.y + r.h; y += 5 * u) g.fillRect(r.x, y, r.w, 1.5 * u);
  } else if (kind === "studio" || kind === "studio_ember") studioBg(g, r, u, t);
}

/** Поверх аватара: попкорн и огонёк студии; «retro_frame» — рамка в огне (рисуется уже вне окна). */
export function drawDecorFront(g: G, kind: Decor, r: Rect, u: number, t: number, chat: { t: number }[]) {
  if (kind === "retro_frame") retroFrame(g, r, u, t);
  else if (kind === "studio" || kind === "studio_ember") {
    popcorn(g, r, u, t, chat);
    if (kind === "studio_ember") ember(g, r.x + r.w * 0.115, r.y + r.h - 10 * u, r.h * 0.11, t);
  }
}
