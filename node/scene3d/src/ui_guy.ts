/**
 * 2D-парни для экранов (ui.ts) и страницы истории «Без лица»: нарисованный аватар-«Live2D» (парень в худи —
 * моргает, кивает, зевает синхронно с героем; другие палитры — чужой витубер и превью в магазинах), заглушка
 * «камера выключена», три зарубежных магазина моделей (японский, в долларах, оплата картой) с каскадом
 * «Оплата отклонена». Всё рисуется канвасом, функция t, без картинок и без чужих брендов.
 */
import { drawDeclined } from "./ui_pages.ts";

const FONT = "VvfUi";
const font = (w: number, px: number) => `${w} ${Math.round(px)}px ${FONT}, "Yu Gothic UI", "Meiryo", Arial, sans-serif`;

function rr(g: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  const k = Math.min(r, w / 2, h / 2);
  g.beginPath();
  g.moveTo(x + k, y);
  g.arcTo(x + w, y, x + w, y + h, k);
  g.arcTo(x + w, y + h, x, y + h, k);
  g.arcTo(x, y + h, x, y, k);
  g.arcTo(x, y, x + w, y, k);
  g.closePath();
}

/* ---------------- парень-аватар ---------------- */

interface GuyPalette { skin: string; hair: string; hairTip?: string; top: string; topDark: string; eyes: string; line: string;
  strings?: string; catEars?: string; headphones?: string; collar?: string }

/** Палитры: hoodie — аватар Тёмы (серое худи, лохматые тёмные волосы, выспался); vtuber — чужой витубер
 * (серебряные волосы, наушники с ушками); knight, cat — превью в магазинах. */
export const GUY_STYLES: Record<string, GuyPalette> = {
  hoodie: { skin: "#f6d2b8", hair: "#3b2a24", top: "#9aa0aa", topDark: "#6f7580", eyes: "#4f8fd8", line: "#2a2030", strings: "#f4f1ec" },
  vtuber: { skin: "#fbe0cf", hair: "#e9ecf8", top: "#6b4bd6", topDark: "#4a2fa8", eyes: "#ff6fa8", line: "#2a2040",
    headphones: "#1f1a2e", catEars: "#ff8ac8", collar: "#ffffff" },
  knight: { skin: "#f6d8c4", hair: "#3f6fd8", top: "#c9ced9", topDark: "#8d95a8", eyes: "#3fd0c8", line: "#1f2440", collar: "#ffd24a" },
  cat: { skin: "#ffe3d2", hair: "#ff9a4a", top: "#ffe08a", topDark: "#e0b04a", eyes: "#3fb86a", line: "#3a2418", catEars: "#ff9a4a" },
};

export interface GuyOpts { style?: string; blink?: number; nod?: number; yawn?: number; talk?: boolean; sway?: number; smile?: boolean }

/**
 * Бюст «нарисованного» парня: плечи в худи (капюшон, шнурки), шея, голова с волосами-прядями, большие глаза.
 * ``nod`` 0..1 — голова вниз (кивок), ``yawn`` 0..1 — зевок (голова назад, глаза «><», рот овалом), ``blink`` 0..1.
 */
export function drawGuy(g: CanvasRenderingContext2D, cx: number, cy: number, s: number, o: GuyOpts = {}) {
  const P = GUY_STYLES[o.style || "hoodie"] || GUY_STYLES.hoodie;
  const yawn = Math.max(0, Math.min(1, o.yawn ?? 0)), nod = Math.max(0, Math.min(1, o.nod ?? 0));
  g.save();
  g.lineJoin = "round";
  g.lineCap = "round";
  g.strokeStyle = P.line;
  g.lineWidth = s * 0.03;
  // Плечи и худи (плечи поднимаются на зевке — потягивается).
  const lift = yawn * s * 0.06;
  g.beginPath();
  g.moveTo(cx - s * 0.72, cy + s * 1.02);
  g.quadraticCurveTo(cx - s * 0.66, cy + s * 0.46 - lift, cx - s * 0.18, cy + s * 0.4 - lift);
  g.lineTo(cx + s * 0.18, cy + s * 0.4 - lift);
  g.quadraticCurveTo(cx + s * 0.66, cy + s * 0.46 - lift, cx + s * 0.72, cy + s * 1.02);
  g.closePath();
  g.fillStyle = P.top;
  g.fill();
  g.stroke();
  // Капюшон вокруг шеи — тёмный валик.
  g.fillStyle = P.collar && !P.strings ? P.topDark : P.topDark;
  g.beginPath();
  g.ellipse(cx, cy + s * 0.46 - lift, s * 0.34, s * 0.12, 0, 0, Math.PI * 2);
  g.fill();
  g.stroke();
  if (P.collar) {
    g.fillStyle = P.collar;
    g.beginPath();
    g.moveTo(cx - s * 0.2, cy + s * 0.5 - lift);
    g.lineTo(cx, cy + s * 0.7 - lift);
    g.lineTo(cx + s * 0.2, cy + s * 0.5 - lift);
    g.closePath();
    g.fill();
  }
  // Шея.
  g.fillStyle = P.skin;
  g.beginPath();
  g.rect(cx - s * 0.1, cy + s * 0.22, s * 0.2, s * 0.24 - lift);
  g.fill();
  if (P.strings) {
    g.strokeStyle = P.strings;
    g.lineWidth = s * 0.022;
    for (const side of [-1, 1]) {
      g.beginPath();
      g.moveTo(cx + side * s * 0.1, cy + s * 0.52 - lift);
      g.quadraticCurveTo(cx + side * s * 0.12, cy + s * 0.68, cx + side * s * 0.09, cy + s * 0.8);
      g.stroke();
      g.fillStyle = P.strings;
      g.beginPath();
      g.arc(cx + side * s * 0.09, cy + s * 0.82, s * 0.022, 0, Math.PI * 2);
      g.fill();
    }
    // Карман-«кенгуру».
    g.strokeStyle = P.topDark;
    g.lineWidth = s * 0.02;
    g.beginPath();
    g.moveTo(cx - s * 0.32, cy + s * 1.0);
    g.lineTo(cx - s * 0.26, cy + s * 0.86);
    g.lineTo(cx + s * 0.26, cy + s * 0.86);
    g.lineTo(cx + s * 0.32, cy + s * 1.0);
    g.stroke();
  }
  g.strokeStyle = P.line;
  g.lineWidth = s * 0.03;
  // Голова: кивок — вниз и чуть меньше (наклон к зрителю), зевок — назад и чуть вбок.
  g.translate(cx, cy + nod * s * 0.1 - yawn * s * 0.04);
  g.rotate((o.sway ?? 0) - yawn * 0.08);
  g.scale(1, 1 - nod * 0.06);
  // Волосы сзади (объём за головой).
  g.fillStyle = P.hair;
  g.beginPath();
  g.ellipse(0, -s * 0.1, s * 0.46, s * 0.46, 0, 0, Math.PI * 2);
  g.fill();
  if (P.headphones) {
    g.strokeStyle = P.headphones;
    g.lineWidth = s * 0.06;
    g.beginPath();
    g.arc(0, -s * 0.06, s * 0.5, Math.PI * 1.08, Math.PI * 1.92);
    g.stroke();
    if (P.catEars) for (const side of [-1, 1]) {
      g.fillStyle = P.headphones;
      g.beginPath();
      g.moveTo(side * s * 0.2, -s * 0.5);
      g.lineTo(side * s * 0.36, -s * 0.78);
      g.lineTo(side * s * 0.44, -s * 0.38);
      g.closePath();
      g.fill();
      g.fillStyle = P.catEars;
      g.beginPath();
      g.moveTo(side * s * 0.26, -s * 0.5);
      g.lineTo(side * s * 0.36, -s * 0.68);
      g.lineTo(side * s * 0.4, -s * 0.44);
      g.closePath();
      g.fill();
    }
  } else if (P.catEars) for (const side of [-1, 1]) {
    g.fillStyle = P.hair;
    g.beginPath();
    g.moveTo(side * s * 0.16, -s * 0.46);
    g.lineTo(side * s * 0.34, -s * 0.8);
    g.lineTo(side * s * 0.44, -s * 0.34);
    g.closePath();
    g.fill();
    g.stroke();
  }
  g.strokeStyle = P.line;
  g.lineWidth = s * 0.03;
  // Уши и лицо.
  g.fillStyle = P.skin;
  for (const side of [-1, 1]) {
    g.beginPath();
    g.ellipse(side * s * 0.36, s * 0.0, s * 0.07, s * 0.1, 0, 0, Math.PI * 2);
    g.fill();
    g.stroke();
  }
  g.beginPath();
  g.moveTo(-s * 0.36, -s * 0.2);
  g.quadraticCurveTo(-s * 0.37, s * 0.18, -s * 0.12, s * 0.33);
  g.quadraticCurveTo(0, s * 0.39, s * 0.12, s * 0.33);
  g.quadraticCurveTo(s * 0.37, s * 0.18, s * 0.36, -s * 0.2);
  g.quadraticCurveTo(s * 0.34, -s * 0.42, 0, -s * 0.43);
  g.quadraticCurveTo(-s * 0.34, -s * 0.42, -s * 0.36, -s * 0.2);
  g.closePath();
  g.fill();
  g.stroke();
  // Чёлка: лохматые пряди треугольниками поверх лба, вихор на макушке.
  g.fillStyle = P.hair;
  g.beginPath();
  g.moveTo(-s * 0.44, -s * 0.06);
  g.quadraticCurveTo(-s * 0.46, -s * 0.52, 0, -s * 0.56);
  g.quadraticCurveTo(s * 0.46, -s * 0.52, s * 0.44, -s * 0.06);
  const tips = [[0.36, -0.1], [0.26, -0.24], [0.18, -0.08], [0.06, -0.22], [-0.04, -0.06], [-0.14, -0.22], [-0.24, -0.1], [-0.34, -0.22], [-0.44, -0.06]];
  for (let i = 0; i < tips.length; i++) {
    const [x, y] = tips[i];
    g.lineTo(s * x, s * (y + (i % 2 ? 0 : 0.02)));
  }
  g.closePath();
  g.fill();
  g.stroke();
  g.beginPath();
  g.moveTo(-s * 0.02, -s * 0.54);
  g.quadraticCurveTo(s * 0.06, -s * 0.72, s * 0.16, -s * 0.66);
  g.quadraticCurveTo(s * 0.08, -s * 0.62, s * 0.08, -s * 0.52);
  g.closePath();
  g.fill();
  g.stroke();
  if (P.hairTip) {
    g.fillStyle = P.hairTip;
    for (const [x, y] of [[0.26, -0.24], [-0.14, -0.22], [0.06, -0.22]]) {
      g.beginPath();
      g.arc(s * x, s * (y - 0.03), s * 0.04, 0, Math.PI * 2);
      g.fill();
    }
  }
  // Брови.
  g.strokeStyle = P.hair === "#e9ecf8" ? "#8a90a8" : P.hair;
  g.lineWidth = s * 0.03;
  for (const side of [-1, 1]) {
    g.beginPath();
    g.moveTo(side * s * 0.08, -s * (0.1 + yawn * 0.05));
    g.quadraticCurveTo(side * s * 0.16, -s * (0.14 + yawn * 0.06), side * s * 0.26, -s * (0.11 + yawn * 0.04));
    g.stroke();
  }
  // Глаза.
  const blink = Math.max(0, Math.min(1, o.blink ?? 0));
  g.strokeStyle = P.line;
  for (const side of [-1, 1]) {
    const ex = side * s * 0.17, ey = s * 0.02, rx = s * 0.075, ry = s * 0.095;
    if (yawn > 0.35) {
      g.lineWidth = s * 0.03;
      g.beginPath();
      g.moveTo(ex - side * rx, ey - ry * 0.6);
      g.lineTo(ex + side * rx * 0.7, ey);
      g.lineTo(ex - side * rx, ey + ry * 0.6);
      g.stroke();
      continue;
    }
    if (blink > 0.85 || o.smile) {
      g.lineWidth = s * 0.03;
      g.beginPath();
      if (o.smile) g.arc(ex, ey + ry * 0.4, rx * 0.85, Math.PI * 1.15, Math.PI * 1.85);
      else g.arc(ex, ey - ry * 0.2, rx * 0.9, Math.PI * 0.15, Math.PI * 0.85);
      g.stroke();
      continue;
    }
    g.fillStyle = "#ffffff";
    g.beginPath();
    g.ellipse(ex, ey, rx, ry, 0, 0, Math.PI * 2);
    g.fill();
    g.fillStyle = P.eyes;
    g.beginPath();
    g.ellipse(ex, ey + ry * 0.12, rx * 0.72, ry * 0.76, 0, 0, Math.PI * 2);
    g.fill();
    g.fillStyle = "#1a1424";
    g.beginPath();
    g.ellipse(ex, ey + ry * 0.12, rx * 0.36, ry * 0.42, 0, 0, Math.PI * 2);
    g.fill();
    g.fillStyle = "#ffffff";
    g.beginPath();
    g.arc(ex - rx * 0.3, ey - ry * 0.3, rx * 0.26, 0, Math.PI * 2);
    g.fill();
    if (blink > 0.02) {
      g.fillStyle = P.skin;
      g.fillRect(ex - rx * 1.15, ey - ry * 1.15, rx * 2.3, ry * 2.3 * blink);
    }
    g.lineWidth = s * 0.032;
    g.beginPath();
    g.ellipse(ex, ey + (blink > 0.02 ? ry * 2 * blink - ry : 0), rx * 1.05, ry, 0, Math.PI * 1.05, Math.PI * 1.95);
    g.stroke();
  }
  // Румянец.
  g.fillStyle = "rgba(255,130,150,0.38)";
  for (const side of [-1, 1]) {
    g.beginPath();
    g.ellipse(side * s * 0.23, s * 0.15, s * 0.06, s * 0.03, 0, 0, Math.PI * 2);
    g.fill();
  }
  // Рот: улыбка / говорит / зевок.
  g.strokeStyle = P.line;
  g.lineWidth = s * 0.024;
  if (yawn > 0.15) {
    const k = Math.min(1, (yawn - 0.15) / 0.5);
    g.fillStyle = "#5a1a26";
    g.beginPath();
    g.ellipse(0, s * 0.23, s * (0.04 + 0.04 * k), s * (0.03 + 0.09 * k), 0, 0, Math.PI * 2);
    g.fill();
    g.fillStyle = "#e57a8a";
    g.beginPath();
    g.ellipse(0, s * (0.25 + 0.05 * k), s * 0.04 * k, s * 0.025 * k, 0, 0, Math.PI * 2);
    g.fill();
  } else if (o.talk) {
    g.fillStyle = "#5a1a26";
    g.beginPath();
    g.ellipse(0, s * 0.22, s * 0.05, s * 0.04, 0, 0, Math.PI * 2);
    g.fill();
  } else {
    g.beginPath();
    g.moveTo(-s * 0.06, s * 0.2);
    g.quadraticCurveTo(0, s * 0.25, s * 0.06, s * 0.2);
    g.stroke();
  }
  g.restore();
}

export interface GuyKeys { blinks?: number[]; nods?: number[]; yawns?: number[][]; talk?: number[][] }

/** Поза аватара на t: авто-моргание, моргания/кивки по ключам, окна зевков (вход и выход плавные). */
export function guyPose(t: number, p: GuyKeys = {}, style = "hoodie"): GuyOpts {
  let blink = 0;
  for (const b of p.blinks || []) if (t >= b && t < b + 0.22) blink = Math.sin(Math.PI * (t - b) / 0.22);
  const auto = (t + 1.3) % 4.1;
  if (auto < 0.18) blink = Math.max(blink, Math.sin(Math.PI * auto / 0.18));
  let nod = 0;
  for (const n of p.nods || []) if (t >= n && t < n + 1.0) {
    const k = Math.max(0, Math.min(1, (t - n) / 0.1, (n + 1.0 - t) / 0.25));
    nod = Math.max(nod, k * Math.max(0, Math.sin(2 * Math.PI * (t - n) / 0.5)));
  }
  let yawn = 0;
  for (const [a, b] of p.yawns || []) if (t >= a && t < b) yawn = Math.max(yawn, Math.min(1, (t - a) / 0.5, (b - t) / 0.5));
  const talk = (p.talk || []).some(([a, b]) => t >= a && t < b) && Math.floor(t * 7) % 3 !== 2;
  return { style, blink: yawn > 0.3 ? 0 : blink, nod, yawn, talk, sway: 0.04 * Math.sin(2 * Math.PI * 0.3 * t) };
}

/* ---------------- эфир без камеры ---------------- */

/** Окно эфира без камеры: тёмная плашка, перечёркнутая камера, «камера выключена». */
export function drawNoCam(g: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, u: number) {
  g.save();
  g.fillStyle = "#15111f";
  rr(g, x, y, w, h, 14 * u);
  g.fill();
  const cx = x + w / 2, cy = y + h * 0.3;  // выше середины: в кадре монитора низ экрана закрывают субтитры
  g.strokeStyle = "#5c5470";
  g.fillStyle = "#5c5470";
  g.lineWidth = 12 * u;
  g.lineJoin = "round";
  rr(g, cx - 90 * u, cy - 55 * u, 130 * u, 110 * u, 18 * u);
  g.stroke();
  g.beginPath();
  g.moveTo(cx + 52 * u, cy - 18 * u);
  g.lineTo(cx + 100 * u, cy - 48 * u);
  g.lineTo(cx + 100 * u, cy + 48 * u);
  g.lineTo(cx + 52 * u, cy + 18 * u);
  g.closePath();
  g.fill();
  g.strokeStyle = "#ff4d6a";
  g.lineWidth = 14 * u;
  g.lineCap = "round";
  g.beginPath();
  g.moveTo(cx - 120 * u, cy - 90 * u);
  g.lineTo(cx + 120 * u, cy + 90 * u);
  g.stroke();
  g.fillStyle = "#9b93b0";
  g.font = font(800, 40 * u);
  g.textAlign = "center";
  g.textBaseline = "middle";
  g.fillText("камера выключена", cx, cy + 130 * u);
  g.textAlign = "left";
  g.restore();
}

/* ---------------- зарубежные магазины ---------------- */

const SHOPS = [
  // Японский: всё по-японски, цена в иенах.
  { bar: "#2b2140", barText: "#ffffff", bg: "#fbf7ff", title: "モデルショップ", nav: "ホーム   カテゴリー   カート",
    name: "Live2Dモデル「ネコ」", price: "¥18,000", note: "日本国内のカードのみ", button: "カートに入れる", btn: "#e2457a", preview: "cat" },
  // В долларах: «только USD».
  { bar: "#14324a", barText: "#ffffff", bg: "#f2f6fa", title: "MODEL  MARKET", nav: "Models   Rigging   Cart",
    name: "Knight · Live2D", price: "$129.00", note: "Prices in USD only", button: "Checkout", btn: "#2f7be0", preview: "knight" },
  // Оплата картой.
  { bar: "#1d1d24", barText: "#ffffff", bg: "#f5f5f7", title: "SECURE  CHECKOUT", nav: "Order #20481",
    name: "Payment details", price: "$129.00", note: "Card number   •••• •••• •••• 4417", button: "Pay $129.00", btn: "#1f9d6a", preview: "" },
];

/**
 * Страница зарубежного магазина ``page`` (0 — японский, 1 — в долларах, 2 — оплата картой). ``declines`` —
 * моменты «Оплата отклонена»: карточки всплывают каскадом (каждая следующая — со сдвигом).
 */
export function drawShopPage(g: CanvasRenderingContext2D, W: number, H: number, t: number, page: number, declines: number[] = []) {
  const u = W / 1000;
  const S = SHOPS[Math.max(0, Math.min(SHOPS.length - 1, page))];
  g.fillStyle = S.bg;
  g.fillRect(0, 0, W, H);
  g.fillStyle = S.bar;
  g.fillRect(0, 0, W, 66 * u);
  g.textBaseline = "middle";
  g.textAlign = "center";
  g.fillStyle = S.barText;
  g.font = font(800, 30 * u);
  g.fillText(S.title, W / 2, 35 * u);
  g.fillStyle = "#6b6580";
  g.font = font(700, 22 * u);
  g.fillText(S.nav, W / 2, 92 * u);
  const cx = W / 2, colW = 460 * u;
  if (S.preview) {
    g.fillStyle = "#e6e2f0";
    rr(g, cx - colW / 2, 112 * u, colW, H * 0.34, 16 * u);
    g.fill();
    drawGuy(g, cx, 112 * u + H * 0.17, H * 0.13, guyPose(t + page, {}, S.preview));
  } else {
    // Форма карты: поля и иконка карты.
    g.fillStyle = "#ffffff";
    rr(g, cx - colW / 2, 112 * u, colW, H * 0.34, 16 * u);
    g.fill();
    g.strokeStyle = "#d9d6e2";
    g.lineWidth = 3 * u;
    g.stroke();
    for (let i = 0; i < 3; i++) {
      g.fillStyle = "#eeedf3";
      rr(g, cx - colW / 2 + 24 * u, 126 * u + i * 60 * u, colW - 48 * u, 44 * u, 10 * u);
      g.fill();
    }
    g.fillStyle = "#3a3550";
    g.font = font(700, 22 * u);
    g.textAlign = "left";
    g.fillText("•••• •••• •••• 4417", cx - colW / 2 + 40 * u, 148 * u);
    g.fillText("12 / 28", cx - colW / 2 + 40 * u, 208 * u);
    g.fillText("CVC  •••", cx - colW / 2 + 40 * u, 268 * u);
    g.textAlign = "center";
  }
  g.fillStyle = "#1c1828";
  g.font = font(800, 34 * u);
  const by = 112 * u + H * 0.34;
  g.fillText(S.name, cx, by + 34 * u);
  g.fillStyle = "#15121f";
  g.font = font(900, 46 * u);
  g.fillText(S.price, cx, by + 84 * u);
  g.fillStyle = "#8a8498";
  g.font = font(700, 22 * u);
  g.fillText(S.note, cx, by + 124 * u);
  g.fillStyle = S.btn;
  rr(g, cx - colW / 2, H - 92 * u, colW, 64 * u, 14 * u);
  g.fill();
  g.fillStyle = "#ffffff";
  g.font = font(800, 28 * u);
  g.fillText(S.button, cx, H - 59 * u);
  g.textAlign = "left";
  // Каскад «Оплата отклонена»: 1-я по центру, следующие ниже-правее, затемнение — один раз.
  const shown = declines.filter((d) => t >= d);
  shown.forEach((d, i) => {
    const k = Math.min(1, ease01((t - d) / 0.3));
    drawDeclined(g, W, H, k, 30 * u + (i - (declines.length - 1) / 2) * 34 * u, (i - (declines.length - 1) / 2) * 64 * u, i === 0);
  });
}

function ease01(x: number) {
  const k = Math.max(0, Math.min(1, x));
  const c1 = 1.70158, c3 = c1 + 1;
  return 1 + c3 * Math.pow(k - 1, 3) + c1 * Math.pow(k - 1, 2);
}
