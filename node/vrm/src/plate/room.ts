/**
 * Ночная комната стримера, написанная «под живопись»: стена в градиентах и цветных пятнах света, окно с
 * ночным городом (небо, луна, три слоя домов с огнями, неон), шторка, постеры, полка, гирлянда, LED-лента,
 * спинка игрового кресла. Статичное рисуется один раз в офскрин-слои и размывается (глубина резкости),
 * живое — каждый кадр: боке огней, лучи света из окна, пылинки, пар над лапшой.
 *
 * Пространство фона — BW × BH (с запасом под наезд камеры), центр совпадает с центром кадра.
 */
import { blob, glow, ink, layer, mix, poly, rgba, rng, wobble, type P } from "./draw.ts";

type G = CanvasRenderingContext2D;

export const BW = 1400, BH = 2400;

export interface Room {
  far: HTMLCanvasElement;
  chair: HTMLCanvasElement;
  cup: HTMLCanvasElement;
  bokeh: { x: number; y: number; r: number; c: string; a: number; ph: number }[];
  dust: { x: number; y: number; r: number; sp: number; ph: number }[];
}

/** Окно: небо, луна, город слоями, рама. */
function windowView(g: G, x: number, y: number, w: number, h: number) {
  g.save();
  g.beginPath();
  g.rect(x, y, w, h);
  g.clip();
  const sky = g.createLinearGradient(0, y, 0, y + h);
  sky.addColorStop(0, "#090d2c");
  sky.addColorStop(0.45, "#1d1d5a");
  sky.addColorStop(0.8, "#5a2f78");
  sky.addColorStop(1, "#c25a8e");
  g.fillStyle = sky;
  g.fillRect(x, y, w, h);
  const r = rng(7);
  for (let i = 0; i < 70; i++) {
    g.fillStyle = `rgba(255,255,255,${0.3 + r() * 0.6})`;
    g.fillRect(x + r() * w, y + r() * h * 0.5, 2, 2);
  }
  // Луна-серп с ореолом.
  const mx = x + w * 0.72, my = y + h * 0.2;
  glow(g, mx, my, 180, 180, "#b9c4ff", 0.35);
  g.fillStyle = "#f4f1ff";
  g.beginPath();
  g.arc(mx, my, 46, 0, Math.PI * 2);
  g.fill();
  g.fillStyle = "#1d1f5c";
  g.beginPath();
  g.arc(mx + 20, my - 12, 42, 0, Math.PI * 2);
  g.fill();
  // Дома: дальний, средний, ближний слой — темнее и крупнее; огни окон тёплые/холодные.
  const layers = [
    { base: y + h * 0.55, hmin: 120, hmax: 330, bw: [50, 110], col: "#2c2a66", lit: 0.18, seed: 11 },
    { base: y + h * 0.68, hmin: 160, hmax: 420, bw: [80, 150], col: "#1d1a48", lit: 0.28, seed: 23 },
    { base: y + h * 0.82, hmin: 220, hmax: 560, bw: [110, 200], col: "#120f30", lit: 0.35, seed: 37 },
  ];
  for (const L of layers) {
    const rr = rng(L.seed);
    let bx = x - 30;
    while (bx < x + w) {
      const bw = L.bw[0] + rr() * (L.bw[1] - L.bw[0]);
      const bh = L.hmin + rr() * (L.hmax - L.hmin);
      const top = L.base - bh + h * 0.35;
      g.fillStyle = L.col;
      g.fillRect(bx, top, bw - 6, y + h - top);
      // Антенна с красным огнём.
      if (rr() < 0.3) {
        g.fillRect(bx + bw * 0.5, top - 40, 3, 40);
        glow(g, bx + bw * 0.5 + 1, top - 40, 12, 12, "#ff4060", 0.9);
      }
      for (let wy = top + 14; wy < y + h; wy += 22) {
        for (let wx = bx + 10; wx < bx + bw - 16; wx += 16) {
          if (rr() > L.lit) continue;
          const warm = rr() < 0.7;
          g.fillStyle = warm ? `rgba(255,${190 + rr() * 50},${110 + rr() * 60},${0.55 + rr() * 0.45})` : `rgba(130,220,255,${0.5 + rr() * 0.4})`;
          g.fillRect(wx, wy, 8, 11);
        }
      }
      bx += bw;
    }
  }
  // Неоновая вывеска на ближнем доме.
  glow(g, x + w * 0.3, y + h * 0.78, 90, 40, "#ff4fa8", 0.8);
  g.fillStyle = "#ffd0ec";
  g.fillRect(x + w * 0.3 - 50, y + h * 0.78 - 8, 100, 16);
  // Дымка у горизонта.
  const haze = g.createLinearGradient(0, y + h * 0.6, 0, y + h);
  haze.addColorStop(0, "rgba(255,120,180,0)");
  haze.addColorStop(1, "rgba(255,120,180,0.28)");
  g.fillStyle = haze;
  g.fillRect(x, y + h * 0.6, w, h * 0.4);
  g.restore();
  // Рама и переплёт, блик по кромке.
  g.strokeStyle = "#0b0b1f";
  g.lineWidth = 34;
  g.strokeRect(x, y, w, h);
  g.lineWidth = 18;
  g.beginPath();
  g.moveTo(x + w * 0.5, y);
  g.lineTo(x + w * 0.5, y + h);
  g.moveTo(x, y + h * 0.42);
  g.lineTo(x + w, y + h * 0.42);
  g.stroke();
  g.strokeStyle = "rgba(160,170,255,0.25)";
  g.lineWidth = 3;
  g.strokeRect(x - 15, y - 15, w + 30, h + 30);
}

/** Постер: градиент, «персонаж» силуэтом, плашки текста. */
function poster(g: G, x: number, y: number, w: number, h: number, rot: number, a: string, b: string, accent: string) {
  g.save();
  g.translate(x + w / 2, y + h / 2);
  g.rotate(rot);
  g.fillStyle = "rgba(0,0,0,0.35)";
  g.fillRect(-w / 2 + 10, -h / 2 + 12, w, h);
  const gr = g.createLinearGradient(0, -h / 2, 0, h / 2);
  gr.addColorStop(0, a);
  gr.addColorStop(1, b);
  g.fillStyle = gr;
  g.fillRect(-w / 2, -h / 2, w, h);
  glow(g, 0, -h * 0.05, w * 0.45, w * 0.45, accent, 0.7);
  g.fillStyle = mix(b, "#000000", 0.35);
  blob(g, [[-w * 0.3, h * 0.5], [-w * 0.2, 0], [-w * 0.14, -h * 0.2], [0, -h * 0.27], [w * 0.14, -h * 0.2], [w * 0.2, 0], [w * 0.32, h * 0.5]]);
  g.fill();
  g.beginPath();
  g.arc(0, -h * 0.2, w * 0.15, 0, Math.PI * 2);
  g.fill();
  g.fillStyle = "rgba(255,255,255,0.85)";
  g.fillRect(-w * 0.38, h * 0.3, w * 0.76, h * 0.06);
  g.fillStyle = rgba(accent, 0.9);
  g.fillRect(-w * 0.25, h * 0.39, w * 0.5, h * 0.035);
  g.restore();
}

/** Дальний слой: стена, свет, окно, постеры, полка, гирлянда, LED. */
function paintFar(g: G) {
  const wall = g.createLinearGradient(0, 0, 0, BH);
  wall.addColorStop(0, "#14163a");
  wall.addColorStop(0.5, "#221d4c");
  wall.addColorStop(1, "#191433");
  g.fillStyle = wall;
  g.fillRect(0, 0, BW, BH);
  // Цветные пятна света на стене — «живописный» фон.
  glow(g, 200, 300, 600, 500, "#ff4fa8", 0.25);
  glow(g, 1100, 1700, 700, 600, "#3a6bff", 0.25);
  glow(g, 300, 1500, 500, 500, "#6a3aff", 0.2);
  windowView(g, 690, 420, 640, 980);
  // Шторка слева от окна.
  g.fillStyle = "#2a1f52";
  blob(g, [[640, 360], [740, 380], [760, 900], [720, 1500], [760, 1700], [600, 1700], [620, 900]]);
  g.fill();
  for (const fx of [660, 700, 730]) ink(g, [[fx, 400], [fx + 10, 900], [fx - 6, 1600]], "rgba(150,130,255,0.35)", { w: 6, a: 0.3, b: 0.3 });
  poster(g, 120, 520, 280, 400, -0.05, "#ff8ac8", "#5a2a9a", "#ffe08a");
  poster(g, 380, 610, 200, 280, 0.06, "#4ad8ff", "#20307a", "#ffffff");
  poster(g, 150, 1000, 230, 160, 0.02, "#ffd36a", "#c2407a", "#ffffff");
  // Полка с книгами и растением.
  g.fillStyle = "#0f0d26";
  g.fillRect(70, 1340, 520, 22);
  const r = rng(5);
  let bx = 100;
  for (let i = 0; i < 9; i++) {
    const w = 22 + r() * 26, h = 90 + r() * 70;
    g.fillStyle = ["#ff6fa8", "#5ac8ff", "#ffd36a", "#8a6aff", "#3a3a7a"][i % 5];
    g.fillRect(bx, 1340 - h, w, h);
    bx += w + 3;
  }
  g.fillStyle = "#2a8a6a";
  for (let i = 0; i < 6; i++) blob(g, [[480, 1330], [440 + i * 18, 1230 - (i % 2) * 30], [470 + i * 14, 1220], [500, 1330]]), g.fill();
  // LED-лента под потолком и её отсвет.
  glow(g, BW / 2, 230, BW * 0.7, 120, "#ff4fb0", 0.55);
  g.fillStyle = "#ffc1e6";
  g.fillRect(0, 222, BW, 8);
  // Гирлянда — провод.
  g.strokeStyle = "rgba(20,15,40,0.9)";
  g.lineWidth = 3;
  poly(g, garland(), false);
  g.stroke();
}

/** Точки провода гирлянды (тот же путь — для лампочек-боке). */
function garland(): P[] {
  const pts: P[] = [];
  for (let i = 0; i <= 40; i++) {
    const u = i / 40;
    pts.push([60 + u * 600, 420 + Math.sin(u * Math.PI * 3) * 50 + u * 60]);
  }
  return pts;
}

/** Спинка игрового кресла за плечами. */
function paintChair(g: G) {
  const cx = BW / 2 + 30, top = 820;
  g.fillStyle = "#16142c";
  blob(g, [[cx - 330, BH], [cx - 360, top + 500], [cx - 300, top + 80], [cx - 160, top], [cx + 160, top], [cx + 300, top + 80], [cx + 360, top + 500], [cx + 330, BH]]);
  g.fill();
  g.strokeStyle = "#ff4fa8";
  g.lineWidth = 10;
  blob(g, [[cx - 300, BH], [cx - 325, top + 500], [cx - 270, top + 100], [cx - 150, top + 30], [cx + 150, top + 30], [cx + 270, top + 100], [cx + 325, top + 500], [cx + 300, BH]]);
  g.stroke();
  g.fillStyle = "rgba(255,255,255,0.06)";
  blob(g, [[cx - 250, top + 120], [cx - 120, top + 60], [cx - 80, top + 300], [cx - 230, top + 400]]);
  g.fill();
}

/** Стакан лапши на столе (передний план). */
function paintCup(g: G, w: number, h: number) {
  const cx = w * 0.5, top = 120, bot = h - 40, rt = w * 0.36, rb = w * 0.28;
  // Палочки.
  g.save();
  g.translate(cx + 30, top + 20);
  g.rotate(0.35);
  g.fillStyle = "#d9a36a";
  g.fillRect(-6, -200, 12, 260);
  g.fillRect(14, -190, 11, 250);
  g.restore();
  const body: P[] = [[cx - rt, top], [cx + rt, top], [cx + rb, bot], [cx - rb, bot]];
  const gr = g.createLinearGradient(cx - rt, 0, cx + rt, 0);
  gr.addColorStop(0, "#c9d6ff");
  gr.addColorStop(0.35, "#ffffff");
  gr.addColorStop(1, "#8a86b8");
  g.fillStyle = gr;
  poly(g, body);
  g.fill();
  // Полоса этикетки.
  g.save();
  poly(g, body);
  g.clip();
  g.fillStyle = "#ff5a4a";
  g.fillRect(0, top + 90, w, 120);
  g.fillStyle = "#ffd34a";
  g.font = "900 64px Arial, sans-serif";
  g.textAlign = "center";
  g.fillText("РАМЕН", cx, top + 175);
  g.fillStyle = "rgba(40,30,90,0.35)";
  g.fillRect(cx + rb * 0.4, 0, w, h);
  g.restore();
  // Край и приоткрытая крышка.
  g.fillStyle = "#f2efff";
  g.beginPath();
  g.ellipse(cx, top, rt, 26, 0, 0, Math.PI * 2);
  g.fill();
  g.fillStyle = "#e8b06a";
  g.beginPath();
  g.ellipse(cx, top + 2, rt - 12, 18, 0, 0, Math.PI * 2);
  g.fill();
  g.fillStyle = "#d8d4ff";
  poly(g, [[cx - rt, top - 4], [cx - rt * 0.1, top - 6], [cx - rt * 0.5, top - 120], [cx - rt * 1.15, top - 90]]);
  g.fill();
}

export function buildRoom(): Room {
  const far = layer(BW, BH);
  const raw = layer(BW, BH);
  paintFar(raw.getContext("2d")!);
  const fg = far.getContext("2d")!;
  fg.filter = "blur(9px)";
  fg.drawImage(raw, 0, 0);
  fg.filter = "none";
  const chairRaw = layer(BW, BH);
  paintChair(chairRaw.getContext("2d")!);
  const chair = layer(BW, BH);
  const cg = chair.getContext("2d")!;
  cg.filter = "blur(4px)";
  cg.drawImage(chairRaw, 0, 0);
  cg.filter = "none";
  const cupRaw = layer(420, 560);
  paintCup(cupRaw.getContext("2d")!, 420, 560);
  const cup = layer(420, 560);
  const ug = cup.getContext("2d")!;
  ug.filter = "blur(6px)";
  ug.drawImage(cupRaw, 0, 0);
  ug.filter = "none";
  // Боке: лампочки гирлянды и огни города в окне.
  const r = rng(99);
  const bokeh: Room["bokeh"] = [];
  garland().forEach((p, i) => {
    if (i % 3) return;
    bokeh.push({ x: p[0], y: p[1] + 10, r: 16 + r() * 14, c: ["#ffd27a", "#ff8ac8", "#8fe0ff"][i % 3], a: 0.55, ph: r() * 6 });
  });
  for (let i = 0; i < 26; i++) {
    bokeh.push({ x: 720 + r() * 600, y: 900 + r() * 480, r: 10 + r() * 26, c: r() < 0.6 ? "#ffc27a" : r() < 0.5 ? "#ff7ab8" : "#7ad8ff", a: 0.25 + r() * 0.3, ph: r() * 6 });
  }
  const dust: Room["dust"] = [];
  for (let i = 0; i < 46; i++) dust.push({ x: r() * BW, y: r() * BH, r: 1.5 + r() * 3.5, sp: 8 + r() * 20, ph: r() * 6 });
  return { far, chair, cup, bokeh, dust };
}

/** Боке поверх дальнего слоя (координаты фона): мягкие диски, мерцают. */
export function drawBokeh(g: G, room: Room, t: number) {
  g.save();
  g.globalCompositeOperation = "lighter";
  for (const b of room.bokeh) {
    const tw = 0.75 + 0.25 * Math.sin(t * 1.3 + b.ph);
    glow(g, b.x, b.y, b.r, b.r, b.c, b.a * tw, 0.55);
    g.strokeStyle = rgba(b.c, 0.18 * tw);
    g.lineWidth = 1.5;
    g.beginPath();
    g.arc(b.x, b.y, b.r * 0.92, 0, Math.PI * 2);
    g.stroke();
  }
  g.restore();
}

/** Лучи из окна (сверху справа вниз влево) и пылинки в них. Координаты кадра. */
export function drawShafts(g: G, W: number, H: number, t: number, power = 1) {
  g.save();
  g.globalCompositeOperation = "lighter";
  const beams = [[W * 0.95, -100, 160, 0.09], [W * 1.15, -60, 220, 0.07], [W * 0.75, -140, 110, 0.06]];
  beams.forEach(([x, y, w, a], i) => {
    const k = (0.75 + 0.25 * Math.sin(t * 0.7 + i * 2)) * power;
    const ex = x - W * 0.9, ey = H * 0.95;
    const gr = g.createLinearGradient(x, y, ex, ey);
    gr.addColorStop(0, `rgba(170,180,255,${a * k})`);
    gr.addColorStop(0.6, `rgba(200,150,255,${a * k * 0.5})`);
    gr.addColorStop(1, "rgba(200,150,255,0)");
    g.fillStyle = gr;
    poly(g, [[x - w * 0.3, y], [x + w * 0.3, y], [ex + w * 1.6, ey], [ex - w * 1.6, ey]]);
    g.fill();
  });
  g.restore();
}

export function drawDust(g: G, room: Room, W: number, H: number, t: number) {
  g.save();
  g.globalCompositeOperation = "lighter";
  for (const d of room.dust) {
    const x = ((d.x + Math.sin(t * 0.5 + d.ph) * 30 - t * d.sp * 0.6) % W + W) % W;
    const y = ((d.y * (H / BH) + t * d.sp * 0.4 + wobble(t, d.ph) * 10) % H + H) % H;
    glow(g, x, y, d.r * 2.2, d.r * 2.2, "#dcd6ff", 0.35 * (0.6 + 0.4 * Math.sin(t * 2 + d.ph)), 0.3);
  }
  g.restore();
}

/** Пар над лапшой: мягкие завитки, поднимаются и тают. */
export function drawSteam(g: G, x: number, y: number, s: number, t: number) {
  g.save();
  g.globalCompositeOperation = "lighter";
  for (let i = 0; i < 7; i++) {
    const u = ((t * 0.35 + i / 7) % 1);
    const px = x + Math.sin(u * 6 + i) * 30 * s, py = y - u * 320 * s;
    glow(g, px, py, (30 + u * 50) * s, (40 + u * 60) * s, "#e8e4ff", 0.12 * Math.sin(Math.PI * u));
  }
  g.restore();
}
