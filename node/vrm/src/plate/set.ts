/**
 * Комната целиком (вид от двери, со спины героя): потолок с LED-лентой, задняя стена со столом, двумя
 * мониторами (эфир и чат — рисуются живыми поверх), окном в ночной город, полкой с фигурками, постеры на
 * левой стене, гирлянда, кровать справа, ковёр, корзина с пустыми стаканами лапши, провода. Нарисовано
 * один раз в «мир» SW × SH (общий план — весь мир, через плечо — кроп у монитора, чуть размыт).
 */
import { blob, glow, ink, layer, mix, poly, rgba, rng, seam, type P } from "./draw.ts";

type G = CanvasRenderingContext2D;

export const SW = 1440, SH = 2560;
/** Экраны в мире: основной (эфир) и вертикальный (чат). */
export const SCREEN_MAIN = { x: 500, y: 905, w: 400, h: 226 };
export const SCREEN_CHAT = { x: 930, y: 860, w: 150, h: 270 };
/** Где сидит Тёма (центр головы) и масштаб на общем плане. */
export const SEAT = { x: 610, y: 1150, k: 0.36 };

const BACK_WALL: P[] = [[170, 270], [1270, 270], [1270, 1490], [170, 1490]];

function wall(g: G) {
  // Потолок.
  const ceil = g.createLinearGradient(0, 0, 0, 270);
  ceil.addColorStop(0, "#120f2a");
  ceil.addColorStop(1, "#1c1840");
  g.fillStyle = ceil;
  poly(g, [[0, 0], [SW, 0], [1270, 270], [170, 270]]);
  g.fill();
  // Задняя стена.
  const bw = g.createLinearGradient(0, 270, 0, 1490);
  bw.addColorStop(0, "#2a2358");
  bw.addColorStop(1, "#1d1940");
  g.fillStyle = bw;
  poly(g, BACK_WALL);
  g.fill();
  // Боковые стены — темнее, с перспективой.
  const lw = g.createLinearGradient(0, 0, 170, 0);
  lw.addColorStop(0, "#130f2c");
  lw.addColorStop(1, "#211c48");
  g.fillStyle = lw;
  poly(g, [[0, 0], [170, 270], [170, 1490], [0, 1760]]);
  g.fill();
  const rw = g.createLinearGradient(SW, 0, 1270, 0);
  rw.addColorStop(0, "#130f2c");
  rw.addColorStop(1, "#211c48");
  g.fillStyle = rw;
  poly(g, [[SW, 0], [1270, 270], [1270, 1490], [SW, 1760]]);
  g.fill();
  // Пол: доски в перспективе.
  const fl = g.createLinearGradient(0, 1490, 0, SH);
  fl.addColorStop(0, "#2a2140");
  fl.addColorStop(1, "#140f22");
  g.fillStyle = fl;
  poly(g, [[0, 1760], [170, 1490], [1270, 1490], [SW, 1760], [SW, SH], [0, SH]]);
  g.fill();
  g.strokeStyle = "rgba(0,0,0,0.3)";
  g.lineWidth = 2;
  for (let i = 0; i <= 14; i++) {
    const x0 = 170 + (i / 14) * 1100, x1 = -900 + (i / 14) * 3240;
    g.beginPath();
    g.moveTo(x0, 1490);
    g.lineTo(x1, SH);
    g.stroke();
  }
  // Углы комнаты и плинтус.
  for (const [a, b] of [[[170, 270], [170, 1490]], [[1270, 270], [1270, 1490]], [[170, 1490], [1270, 1490]], [[170, 270], [1270, 270]]] as P[][]) {
    ink(g, [a, b], "rgba(8,6,20,0.7)", { w: 4, a: 0, b: 0 });
  }
  g.fillStyle = "#15112c";
  g.fillRect(170, 1470, 1100, 20);
  // LED-лента по потолку и её отсвет.
  glow(g, 720, 280, 700, 160, "#ff4fb0", 0.5);
  g.fillStyle = "#ffc1e6";
  g.fillRect(170, 266, 1100, 6);
  poly(g, [[0, 4], [170, 268], [170, 274], [0, 12]]);
  g.fill();
}

function windowCity(g: G, x: number, y: number, w: number, h: number) {
  g.save();
  g.beginPath();
  g.rect(x, y, w, h);
  g.clip();
  const sky = g.createLinearGradient(0, y, 0, y + h);
  sky.addColorStop(0, "#0a0e2e");
  sky.addColorStop(0.6, "#3a2a72");
  sky.addColorStop(1, "#c25a8e");
  g.fillStyle = sky;
  g.fillRect(x, y, w, h);
  glow(g, x + w * 0.7, y + h * 0.2, 80, 80, "#c9d0ff", 0.5);
  g.fillStyle = "#f4f1ff";
  g.beginPath();
  g.arc(x + w * 0.7, y + h * 0.2, 20, 0, Math.PI * 2);
  g.fill();
  const r = rng(31);
  for (const [base, col, lit] of [[0.62, "#2a2862", 0.2], [0.75, "#1b1946", 0.3], [0.88, "#100e2c", 0.35]] as [number, string, number][]) {
    let bx = x - 10;
    while (bx < x + w) {
      const bw = 30 + r() * 50, bh = 80 + r() * 200, top = y + h * base - bh * 0.6;
      g.fillStyle = col;
      g.fillRect(bx, top, bw - 3, y + h - top);
      for (let wy = top + 8; wy < y + h; wy += 13) {
        for (let wx = bx + 5; wx < bx + bw - 8; wx += 9) {
          if (r() > lit) continue;
          g.fillStyle = r() < 0.7 ? "rgba(255,206,130,0.9)" : "rgba(130,220,255,0.85)";
          g.fillRect(wx, wy, 4, 6);
        }
      }
      bx += bw;
    }
  }
  g.restore();
  g.strokeStyle = "#0b0a1e";
  g.lineWidth = 16;
  g.strokeRect(x, y, w, h);
  g.lineWidth = 9;
  g.beginPath();
  g.moveTo(x + w / 2, y);
  g.lineTo(x + w / 2, y + h);
  g.stroke();
  // Подоконник и лунный свет на полу.
  g.fillStyle = "#3a3366";
  g.fillRect(x - 20, y + h, w + 40, 16);
}

/** Плоская картинка на левой стене в перспективе (трапеция). */
function wallPoster(g: G, x0: number, y0: number, w: number, h: number, a: string, b: string) {
  const sk = (x: number) => (x / 170) * 0.28; // ближе к углу — меньше
  const pts: P[] = [[x0, y0 + 170 * sk(x0)], [x0 + w, y0 + 170 * sk(x0 + w) - 26], [x0 + w, y0 + h + 30], [x0, y0 + h]];
  const gr = g.createLinearGradient(x0, y0, x0, y0 + h);
  gr.addColorStop(0, a);
  gr.addColorStop(1, b);
  g.fillStyle = gr;
  poly(g, pts);
  g.fill();
  g.strokeStyle = "rgba(255,255,255,0.25)";
  g.lineWidth = 2;
  g.stroke();
  glow(g, x0 + w * 0.5, y0 + h * 0.45, w * 0.5, h * 0.3, "#ffffff", 0.25);
}

function shelf(g: G) {
  const r = rng(8);
  for (const y of [640, 860]) {
    g.fillStyle = "#3a2a4a";
    g.fillRect(220, y, 300, 14);
    g.fillStyle = "rgba(0,0,0,0.35)";
    g.fillRect(220, y + 14, 300, 10);
    let bx = 232;
    while (bx < 500) {
      const w = 14 + r() * 18, h = 60 + r() * 60;
      if (r() < 0.25) {
        // Фигурка: тело и голова.
        g.fillStyle = ["#ff8ad8", "#8fe0ff", "#ffd36a"][Math.floor(r() * 3)];
        g.fillRect(bx, y - 60, 22, 60);
        g.beginPath();
        g.arc(bx + 11, y - 74, 16, 0, Math.PI * 2);
        g.fill();
        bx += 34;
        continue;
      }
      g.fillStyle = ["#ff6fa8", "#5ac8ff", "#ffd36a", "#8a6aff", "#4a4a8a"][Math.floor(r() * 5)];
      g.fillRect(bx, y - h, w, h);
      g.fillStyle = "rgba(255,255,255,0.25)";
      g.fillRect(bx + 2, y - h + 6, w - 4, 3);
      bx += w + 2;
    }
  }
}

function desk(g: G) {
  // Столешница, передняя кромка, ножки; подсветка под столом.
  glow(g, 720, 1420, 520, 90, "#ff4fb0", 0.4);
  g.fillStyle = "#2f2648";
  poly(g, [[300, 1190], [1140, 1190], [1190, 1330], [250, 1330]]);
  g.fill();
  g.fillStyle = "#1e1834";
  g.fillRect(250, 1330, 940, 34);
  g.fillStyle = "#141024";
  for (const x of [262, 1160]) g.fillRect(x, 1364, 22, 236);
  g.fillStyle = "#ffb3e0";
  g.fillRect(260, 1360, 920, 4);
  // Стойки мониторов.
  g.fillStyle = "#15131f";
  g.fillRect(686, 1131, 28, 70);
  poly(g, [[640, 1200], [760, 1200], [770, 1214], [630, 1214]]);
  g.fill();
  g.fillRect(990, 1130, 20, 70);
  // Клавиатура с подсветкой, мышь, коврик.
  g.fillStyle = "#0d0c1a";
  poly(g, [[440, 1250], [1010, 1250], [1030, 1318], [420, 1318]]);
  g.fill();
  g.fillStyle = "#16142a";
  poly(g, [[540, 1262], [800, 1262], [808, 1296], [532, 1296]]);
  g.fill();
  const r = rng(4);
  for (let row = 0; row < 3; row++) {
    for (let i = 0; i < 18; i++) {
      const hue = (i * 20 + row * 30) % 360;
      g.fillStyle = `hsla(${hue},90%,70%,${0.55 + r() * 0.3})`;
      g.fillRect(544 + i * 14.2 - row * 2, 1265 + row * 10, 11, 7);
    }
  }
  glow(g, 670, 1280, 170, 30, "#8a6aff", 0.4);
  g.fillStyle = "#e9e6f4";
  g.beginPath();
  g.ellipse(880, 1282, 16, 22, 0, 0, Math.PI * 2);
  g.fill();
  glow(g, 880, 1290, 20, 8, "#4fd8ff", 0.8);
  // Стакан лапши с палочками и банка энергетика.
  g.fillStyle = "#f4f1ff";
  poly(g, [[1040, 1180], [1100, 1180], [1092, 1250], [1048, 1250]]);
  g.fill();
  g.fillStyle = "#ff5a4a";
  g.fillRect(1044, 1204, 52, 18);
  g.strokeStyle = "#d9a36a";
  g.lineWidth = 5;
  g.beginPath();
  g.moveTo(1066, 1186);
  g.lineTo(1090, 1120);
  g.moveTo(1074, 1186);
  g.lineTo(1104, 1126);
  g.stroke();
  g.fillStyle = "#36e0a0";
  g.fillRect(380, 1200, 30, 58);
  g.fillStyle = "#0e3a2c";
  g.fillRect(380, 1214, 30, 12);
  // Микрофон на стойке-«журавле».
  ink(g, [[320, 1190], [360, 980], [470, 900]], "#1a1828", { w: 12, a: 0, b: 0 });
  g.fillStyle = "#26243a";
  g.beginPath();
  g.roundRect(452, 880, 44, 80, 20);
  g.fill();
  g.strokeStyle = "#0c0b16";
  g.lineWidth = 3;
  g.stroke();
}

function monitorFrames(g: G) {
  for (const s of [SCREEN_MAIN, SCREEN_CHAT]) {
    g.fillStyle = "#0b0a16";
    g.beginPath();
    g.roundRect(s.x - 12, s.y - 12, s.w + 24, s.h + 24, 8);
    g.fill();
    g.strokeStyle = "rgba(170,160,255,0.3)";
    g.lineWidth = 2;
    g.stroke();
  }
}

function bed(g: G) {
  g.fillStyle = "#1a1636";
  poly(g, [[1180, 1600], [SW, 1560], [SW, 2240], [1080, 2240]]);
  g.fill();
  // Одеяло с складками и подушка.
  g.fillStyle = "#4a3a8a";
  blob(g, [[1120, 1700], [1300, 1640], [SW, 1650], [SW, 2200], [1100, 2220], [1060, 1980]]);
  g.fill();
  g.save();
  g.clip();
  g.fillStyle = "#3a2c70";
  blob(g, [[1200, 1760], [SW, 1720], [SW, 2200], [1250, 2200], [1180, 1960]]);
  g.fill();
  for (const y of [1820, 1940, 2060]) ink(g, [[1100, y], [1260, y - 30], [SW, y - 10]], "rgba(20,10,50,0.5)", { w: 8, a: 0.3, b: 0.3 });
  g.restore();
  g.fillStyle = "#e8e2ff";
  blob(g, [[1260, 1600], [1420, 1580], [1440, 1650], [1280, 1680]]);
  g.fill();
  seam(g, [[1270, 1610], [1420, 1592]], "rgba(120,100,180,0.6)");
}

function floorStuff(g: G) {
  // Ковёр под креслом.
  g.fillStyle = "#3a2c5a";
  g.beginPath();
  g.ellipse(640, 2060, 470, 170, 0, 0, Math.PI * 2);
  g.fill();
  g.strokeStyle = "#ff8ad8";
  g.lineWidth = 6;
  g.setLineDash([16, 12]);
  g.beginPath();
  g.ellipse(640, 2060, 430, 145, 0, 0, Math.PI * 2);
  g.stroke();
  g.setLineDash([]);
  // Корзина с пустыми стаканами.
  g.fillStyle = "#24203a";
  poly(g, [[200, 1600], [300, 1600], [290, 1720], [210, 1720]]);
  g.fill();
  for (const [x, y, a] of [[230, 1590, -0.3], [270, 1585, 0.25]]) {
    g.save();
    g.translate(x, y);
    g.rotate(a);
    g.fillStyle = "#f4f1ff";
    poly(g, [[-18, -30], [18, -30], [14, 20], [-14, 20]]);
    g.fill();
    g.fillStyle = "#ff5a4a";
    g.fillRect(-17, -16, 34, 10);
    g.restore();
  }
  // Провода от стола.
  ink(g, [[700, 1364], [690, 1460], [760, 1530], [900, 1520]], "#0c0b16", { w: 6, a: 0, b: 0 });
  ink(g, [[740, 1364], [760, 1470], [700, 1560]], "#0c0b16", { w: 5, a: 0, b: 0 });
}

/** Нарисовать мир: стены, окно, полки, постеры, стол, кровать, свет. */
export function buildSet(): { sharp: HTMLCanvasElement; soft: HTMLCanvasElement } {
  const sharp = layer(SW, SH);
  const g = sharp.getContext("2d")!;
  wall(g);
  windowCity(g, 1010, 400, 230, 380);
  wallPoster(g, 40, 520, 110, 250, "#ff8ac8", "#5a2a9a");
  wallPoster(g, 50, 860, 100, 180, "#4ad8ff", "#20307a");
  shelf(g);
  // Постеры над столом.
  for (const [x, y, w, h, a, b] of [[560, 470, 150, 210, "#ffd36a", "#c2407a"], [740, 440, 130, 180, "#8fe0ff", "#3a3a9a"]] as [number, number, number, number, string, string][]) {
    const gr = g.createLinearGradient(x, y, x, y + h);
    gr.addColorStop(0, a);
    gr.addColorStop(1, b);
    g.fillStyle = gr;
    g.fillRect(x, y, w, h);
    g.fillStyle = mix(b, "#000000", 0.35);
    g.beginPath();
    g.arc(x + w / 2, y + h * 0.42, w * 0.2, 0, Math.PI * 2);
    g.fill();
    g.fillRect(x + w * 0.25, y + h * 0.55, w * 0.5, h * 0.3);
    g.fillStyle = "rgba(255,255,255,0.8)";
    g.fillRect(x + w * 0.15, y + h * 0.88, w * 0.7, 6);
  }
  // Гирлянда по стене: провод и лампочки.
  const gl: P[] = [];
  for (let i = 0; i <= 30; i++) gl.push([190 + i * 36, 330 + Math.sin(i * 0.7) * 24 + (i % 2) * 6]);
  ink(g, gl, "#0c0a18", { w: 3, a: 0, b: 0 });
  gl.forEach((p, i) => {
    if (i % 2) return;
    glow(g, p[0], p[1] + 8, 18, 18, ["#ffd27a", "#ff8ac8", "#8fe0ff"][i % 3], 0.8, 0.3);
    g.fillStyle = "#fff6e0";
    g.beginPath();
    g.arc(p[0], p[1] + 8, 4, 0, Math.PI * 2);
    g.fill();
  });
  desk(g);
  monitorFrames(g);
  // Свет мониторов на стену и стол.
  g.save();
  g.globalCompositeOperation = "lighter";
  glow(g, 720, 1020, 520, 380, "#4a7aff", 0.28);
  glow(g, 720, 1250, 420, 90, "#6aa8ff", 0.25);
  // Лунная полоса из окна на пол.
  g.fillStyle = "rgba(150,160,255,0.06)";
  poly(g, [[1010, 780], [1240, 780], [1100, 2100], [700, 2100]]);
  g.fill();
  g.restore();
  bed(g);
  floorStuff(g);
  // Мягкий вариант для «через плечо»: лёгкое размытие.
  const soft = layer(SW, SH);
  const sg = soft.getContext("2d")!;
  sg.filter = "blur(2.5px)";
  sg.drawImage(sharp, 0, 0);
  sg.filter = "none";
  return { sharp, soft };
}
