/**
 * Лица эмоций героя из примитивов (пресеты игры, presets/performance.ts) — в том же стиле, что и базовые лица
 * pixel.ts: ``soft`` — «аниме»-наклейка 128×128 (глаза x 38/90, y≈62; брови y≈37; рот y≈96; румянец y 84),
 * ``block`` — пиксель-арт 16×16 (глаза в столбцах 3–5 и 10–12, рот в строке 12).
 *
 *   angry   — брови «галочкой» к переносице, веки срезаны наискось, стиснутые зубы, румянец гнева;
 *   sad     — брови домиком, взгляд вниз, слёзы на нижнем веке и капля по щеке, дрожащий рот;
 *   smug    — одна бровь выше, полуприкрытые глаза косят в сторону, ухмылка уголком;
 *   pout    — «хмф»: глаза закрыты чёрточками, брови к переносице, губы бантиком, щёки красные;
 *   think   — одна бровь вверх, взгляд вверх-в сторону, рот чёрточкой сбоку;
 *   panic   — брови домиком, белки с крошечными зрачками, рот волной, капля пота;
 *   shock   — «пустые» круглые глаза-белки с точкой, брови до чёлки, рот большой буквой «О»;
 *   eyeroll — полуприкрытые глаза, зрачки закатились под веко, рот ровной чертой;
 *   sigh    — глаза закрыты опущенными дугами, рот маленьким «о» (выдох);
 *   wince   — зажмурился «> <», стиснутые зубы (испуг, боль, рука-лицо);
 *   meh     — брови вверх, полуприкрытые глаза, рот плоской чертой («ну не знаю»).
 *
 * blink = true — тот же рисунок с закрытыми глазами (моргание открытых эмоций: angry, sad, smug, think, meh).
 */
import * as THREE from "three";

export const EMOTION_FACES = ["angry", "sad", "smug", "pout", "think", "panic", "shock", "eyeroll", "sigh", "wince", "meh"] as const;
export type EmotionFace = (typeof EMOTION_FACES)[number];
/** Эмоции с открытыми глазами, которым нужно моргание (своя закрытая версия, а не нейтральное «blink»). */
export const BLINKING_FACES: string[] = ["angry", "sad", "smug", "think", "meh"];

export interface FacePal {
  skin: string;
  hair: string;
  eyes?: string;
  mouth?: string;
  blush?: string;
  lashes?: boolean;
}

const shade = (color: string, k: number) => `#${new THREE.Color(color).multiplyScalar(k).getHexString()}`;
const TEAR = "#8fd3ff", SWEAT = "#9fdcff", TEETH = "#f6f2f4", MOUTH_IN = "#4a1420";

/* ---------------- мягкое «аниме»-лицо 128×128 ---------------- */

interface EyeOpt {
  rx?: number;
  ry?: number;
  /** Зрачок: сдвиг (px), размер радужки, false — «пустой» белок (только точка). */
  dx?: number;
  dy?: number;
  ir?: number;
  iris?: boolean;
  /** Веко срезает глаз сверху: доля высоты (0 — открыт), наклон к переносице (px, + — внутренний угол ниже). */
  lid?: number;
  slant?: number;
}

function softEye(g: CanvasRenderingContext2D, x: number, y: number, o: EyeOpt, ink: string, iris: string, lashes: boolean) {
  const rx = o.rx ?? 12, ry = o.ry ?? 16;
  const inner = x < 64 ? 1 : -1;
  const lid = o.lid ?? 0, slant = o.slant ?? 0;
  // Линия века: y на внешнем и внутреннем краях.
  const top = y - ry + 2 * ry * lid;
  const yOut = top - slant, yIn = top + slant;
  const xOut = x - inner * (rx + 3), xIn = x + inner * (rx + 3);
  g.save();
  if (lid > 0 || slant) {
    g.beginPath();
    g.moveTo(xOut, yOut);
    g.lineTo(xIn, yIn);
    g.lineTo(xIn, y + ry + 4);
    g.lineTo(xOut, y + ry + 4);
    g.closePath();
    g.clip();
  }
  g.fillStyle = "#ffffff";
  g.beginPath();
  g.ellipse(x, y, rx, ry, 0, 0, Math.PI * 2);
  g.fill();
  const cx = x + (o.dx ?? 0), cy = y + 2 + (o.dy ?? 0);
  if (o.iris === false) {
    g.fillStyle = ink;
    g.beginPath();
    g.arc(cx, cy, o.ir ?? 2.6, 0, Math.PI * 2);
    g.fill();
  } else {
    const ir = o.ir ?? 10.5;
    const grad = g.createLinearGradient(0, cy - ir, 0, cy + ir);
    grad.addColorStop(0, shade(iris, 0.55));
    grad.addColorStop(1, shade(iris, 1.25));
    g.fillStyle = grad;
    g.beginPath();
    g.ellipse(cx, cy, ir * 0.92, ir * 1.15, 0, 0, Math.PI * 2);
    g.fill();
    g.fillStyle = ink;
    g.beginPath();
    g.ellipse(cx, cy, ir * 0.42, ir * 0.55, 0, 0, Math.PI * 2);
    g.fill();
    g.fillStyle = "#ffffff";
    g.beginPath();
    g.arc(cx - 4, cy - 6, Math.max(2, ir * 0.36), 0, Math.PI * 2);
    g.fill();
    g.beginPath();
    g.arc(cx + 4, cy + 5, 2, 0, Math.PI * 2);
    g.fill();
  }
  g.restore();
  // Верхнее веко: дуга (открытый глаз) или прямая по срезу; ресница наружу.
  g.strokeStyle = ink;
  g.lineWidth = lashes ? 6 : 5;
  g.beginPath();
  if (lid > 0 || slant) {
    const k = rx / (rx + 3);
    g.moveTo(x - inner * rx, yOut + (yIn - yOut) * (1 - k) / 2);
    g.lineTo(x + inner * rx, yIn - (yIn - yOut) * (1 - k) / 2);
  } else {
    g.ellipse(x, y, rx + 1, ry + 1, 0, Math.PI * 1.08, Math.PI * 1.92);
  }
  g.stroke();
  const side = -inner;
  const ly = lid > 0 || slant ? yOut + 1 : y - ry * 0.35;
  g.beginPath();
  g.moveTo(x + side * rx, ly);
  g.lineTo(x + side * (rx + 5), ly - 4);
  g.stroke();
}

/** Закрытые глаза: дуги (down — уголки вниз, как у выдоха), «> <» (squeeze) или прямые чёрточки (flat). */
function softClosed(g: CanvasRenderingContext2D, kind: "arc" | "droop" | "squeeze" | "flat", ink: string) {
  g.strokeStyle = ink;
  g.lineWidth = 5;
  for (const x of [38, 90]) {
    const out = x < 64 ? -1 : 1;
    g.beginPath();
    if (kind === "squeeze") {
      g.moveTo(x + out * 11, 53);
      g.lineTo(x - out * 4, 61);
      g.lineTo(x + out * 11, 68);
    } else if (kind === "flat") {
      g.moveTo(x - 12, 62 - out * 2);
      g.lineTo(x + 12, 62 + out * 2);
    } else if (kind === "droop") {
      g.arc(x, 56, 13, Math.PI * 0.15, Math.PI * 0.85);
    } else {
      g.arc(x, 58, 13, Math.PI * 0.12, Math.PI * 0.88);
    }
    g.stroke();
  }
}

/** Брови: outerY, innerY — концы, bend — выгиб середины вверх (px). */
function softBrows(g: CanvasRenderingContext2D, col: string, l: [number, number, number], r: [number, number, number] = l, w = 4) {
  g.strokeStyle = col;
  g.lineWidth = w;
  for (const [x, [oy, iy, bend]] of [[38, l], [90, r]] as const) {
    const inner = x < 64 ? 1 : -1;
    g.beginPath();
    g.moveTo(x - 13 * inner, oy);
    g.quadraticCurveTo(x, (oy + iy) / 2 - bend, x + 12 * inner, iy);
    g.stroke();
  }
}

function blushSoft(g: CanvasRenderingContext2D, col: string, rx = 11, ry = 6, y = 84) {
  g.fillStyle = col;
  for (const x of [26, 102]) {
    g.beginPath();
    g.ellipse(x, y, rx, ry, 0, 0, Math.PI * 2);
    g.fill();
  }
}

function teethSoft(g: CanvasRenderingContext2D, mouth: string, w: number, h: number, y = 98) {
  const x0 = 64 - w / 2;
  g.fillStyle = TEETH;
  g.strokeStyle = mouth;
  g.lineWidth = 3;
  g.beginPath();
  g.roundRect(x0, y - h / 2, w, h, h * 0.45);
  g.fill();
  g.stroke();
  g.lineWidth = 1.6;
  g.beginPath();
  g.moveTo(x0 + 2, y);
  g.lineTo(x0 + w - 2, y);
  for (const k of [0.33, 0.66]) {
    g.moveTo(x0 + w * k, y - h / 2 + 1);
    g.lineTo(x0 + w * k, y + h / 2 - 1);
  }
  g.stroke();
}

function drop(g: CanvasRenderingContext2D, x: number, y: number, s: number, col: string) {
  g.fillStyle = col;
  g.strokeStyle = shade(col, 0.6);
  g.lineWidth = 1.5;
  g.beginPath();
  g.moveTo(x, y - 1.6 * s);
  g.quadraticCurveTo(x + s, y, x, y + s * 0.9);
  g.quadraticCurveTo(x - s, y, x, y - 1.6 * s);
  g.fill();
  g.stroke();
  g.fillStyle = "rgba(255,255,255,0.85)";
  g.beginPath();
  g.arc(x - s * 0.3, y, s * 0.25, 0, Math.PI * 2);
  g.fill();
}

export function softEmotionFace(c: HTMLCanvasElement, face: EmotionFace, p: FacePal, blink = false) {
  const g = c.getContext("2d")!;
  const ink = p.eyes ? shade(p.eyes, 0.42) : "#20152c";
  const iris = p.eyes || "#7a4bd6";
  const brow = shade(p.hair, 0.85);
  const mouth = p.mouth || (p.lashes ? "#b2445c" : "#8a3344");
  const lashes = !!p.lashes;
  const blush = p.blush || "rgba(240,120,140,0.35)";
  g.lineCap = "round";
  g.lineJoin = "round";
  const eyes = (o: EyeOpt, closed: "arc" | "droop" | "squeeze" | "flat" = "arc") => {
    if (blink) softClosed(g, closed, ink);
    else for (const x of [38, 90]) softEye(g, x, 62, o, ink, iris, lashes);
  };
  if (face === "angry") {
    blushSoft(g, "rgba(235,70,70,0.32)", 13, 6);
    softBrows(g, brow, [30, 44, -1], [30, 44, -1], 5);
    eyes({ ry: 15, lid: 0.3, slant: 7, ir: 8.5, dy: 2 }, "flat");
    teethSoft(g, mouth, 22, 10, 99);
  } else if (face === "sad") {
    blushSoft(g, blush, 11, 6, 86);
    softBrows(g, brow, [42, 30, 2]);
    eyes({ ry: 15, dy: 3, ir: 10.5, lid: 0.1 }, "droop");
    if (!blink) {
      // Слёзы на нижнем веке и капля по щеке.
      g.strokeStyle = TEAR;
      g.lineWidth = 3;
      for (const x of [38, 90]) {
        g.beginPath();
        g.arc(x, 62, 14, Math.PI * 0.18, Math.PI * 0.82);
        g.stroke();
      }
      drop(g, 30, 84, 4.5, TEAR);
      drop(g, 98, 88, 3.5, TEAR);
    }
    g.strokeStyle = mouth;
    g.lineWidth = 4;
    g.beginPath();
    g.moveTo(56, 101);
    g.quadraticCurveTo(60, 95, 64, 97);
    g.quadraticCurveTo(68, 95, 72, 101);
    g.stroke();
  } else if (face === "smug") {
    blushSoft(g, blush, 12, 6);
    softBrows(g, brow, [34, 31, 4], [41, 39, 1]);
    eyes({ ry: 15, lid: 0.45, dx: 4, dy: 1, ir: 9.5 }, "flat");
    g.strokeStyle = mouth;
    g.lineWidth = 4;
    g.beginPath();
    g.moveTo(55, 98);
    g.quadraticCurveTo(64, 101, 72, 95);
    g.lineTo(75, 91);
    g.stroke();
  } else if (face === "pout") {
    blushSoft(g, "rgba(240,90,120,0.5)", 14, 7);
    softBrows(g, brow, [33, 41, 1]);
    softClosed(g, "flat", ink);
    // Губы бантиком: маленькое «3»-сердечко.
    g.strokeStyle = mouth;
    g.lineWidth = 3.5;
    g.beginPath();
    g.moveTo(60, 93);
    g.quadraticCurveTo(67, 94, 63, 97.5);
    g.quadraticCurveTo(67, 101, 60, 102);
    g.stroke();
  } else if (face === "think") {
    softBrows(g, brow, [33, 32, 5], [39, 38, 1]);
    eyes({ dx: 4, dy: -5, ir: 9.5 });
    g.strokeStyle = mouth;
    g.lineWidth = 4;
    g.beginPath();
    g.moveTo(64, 98);
    g.quadraticCurveTo(69, 96, 74, 97);
    g.stroke();
  } else if (face === "panic") {
    softBrows(g, brow, [40, 27, 1]);
    eyes({ rx: 13, ry: 17, iris: false, ir: 3 });
    drop(g, 112, 40, 6, SWEAT);
    drop(g, 17, 52, 4, SWEAT);
    // Рот волной: тёмный, с обводкой.
    g.fillStyle = MOUTH_IN;
    g.strokeStyle = mouth;
    g.lineWidth = 3;
    g.beginPath();
    g.moveTo(50, 98);
    for (let i = 1; i <= 6; i++) g.lineTo(50 + i * 4.67, 98 + (i % 2 ? -4 : 1));
    for (let i = 6; i >= 0; i--) g.lineTo(50 + i * 4.67, 104 + (i % 2 ? -1 : 2));
    g.closePath();
    g.fill();
    g.stroke();
  } else if (face === "shock") {
    softBrows(g, brow, [28, 26, 4]);
    eyes({ rx: 15, ry: 19, iris: false, ir: 2.6 });
    g.fillStyle = MOUTH_IN;
    g.strokeStyle = mouth;
    g.lineWidth = 3;
    g.beginPath();
    g.ellipse(64, 101, 9, 12, 0, 0, Math.PI * 2);
    g.fill();
    g.stroke();
    g.fillStyle = "#e0707f";
    g.beginPath();
    g.ellipse(64, 108, 5.5, 3.5, 0, 0, Math.PI * 2);
    g.fill();
  } else if (face === "eyeroll") {
    softBrows(g, brow, [34, 35, 3]);
    eyes({ ry: 15, lid: 0.4, dy: -8, ir: 9.5 }, "flat");
    g.strokeStyle = mouth;
    g.lineWidth = 4;
    g.beginPath();
    g.moveTo(57, 98);
    g.lineTo(72, 96);
    g.stroke();
  } else if (face === "sigh") {
    softBrows(g, brow, [41, 37, 2]);
    softClosed(g, "droop", ink);
    g.fillStyle = mouth;
    g.beginPath();
    g.ellipse(64, 98, 4.5, 4, 0, 0, Math.PI * 2);
    g.fill();
  } else if (face === "wince") {
    blushSoft(g, blush, 12, 6);
    softBrows(g, brow, [38, 34, -2]);
    softClosed(g, "squeeze", ink);
    teethSoft(g, mouth, 26, 11, 98);
  } else if (face === "meh") {
    softBrows(g, brow, [32, 31, 4]);
    eyes({ ry: 15, lid: 0.32, dy: 1, ir: 10 }, "flat");
    g.strokeStyle = mouth;
    g.lineWidth = 4;
    g.beginPath();
    g.moveTo(55, 97);
    g.quadraticCurveTo(60, 95, 64, 97);
    g.quadraticCurveTo(68, 99, 73, 97);
    g.stroke();
  }
}

/* ---------------- пиксельное лицо 16×16 ---------------- */

/**
 * Рисует эмоцию поверх уже залитой кожи и чёлки (blockFaceCanvas): px(x, y, цвет); строки 0–4 — чёлка.
 */
export function blockEmotionFace(px: (x: number, y: number, col: string) => void, face: EmotionFace, p: FacePal, blink = false) {
  const dark = p.eyes || "#1c1426";
  const brow = shade(p.hair, 0.8);
  const white = "#f4f1f6";
  const mouth = p.mouth || "#7a2f3a";
  const lid = shade(p.skin, 0.8);
  const L = [3, 10];
  const row = (y: number, x0: number, n: number, col: string) => {
    for (let x = 0; x < n; x++) px(x0 + x, y, col);
  };
  const closedEyes = () => {
    for (const x0 of L) row(8, x0, 3, dark);
  };
  /** Глаз 3×rows: белок, зрачок в (ix, iy) относительно угла. */
  const eye = (x0: number, y0: number, rows: number, ix: number, iy: number) => {
    for (let y = 0; y < rows; y++) row(y0 + y, x0, 3, white);
    if (ix >= 0) px(x0 + ix, y0 + iy, dark);
  };
  const teeth = (x0: number, x1: number) => {
    for (let x = x0; x <= x1; x++) px(x, 12, x === x0 || x === x1 ? mouth : white);
    row(11, x0 + 1, x1 - x0 - 1, mouth);
    row(13, x0 + 1, x1 - x0 - 1, mouth);
  };
  if (face === "angry") {
    // Брови галочкой: внешний конец выше, внутренний ниже.
    px(2, 5, brow); px(3, 5, brow); px(4, 6, brow); px(5, 6, brow);
    px(10, 6, brow); px(11, 6, brow); px(12, 5, brow); px(13, 5, brow);
    if (blink) closedEyes();
    else {
      eye(3, 7, 2, 2, 1); eye(10, 7, 2, 0, 1);
      px(5, 7, lid); px(10, 7, lid);
    }
    teeth(5, 10);
    px(1, 10, "#e65b5b"); px(14, 10, "#e65b5b");
  } else if (face === "sad") {
    px(3, 6, brow); px(4, 6, brow); px(5, 5, brow);
    px(10, 5, brow); px(11, 6, brow); px(12, 6, brow);
    if (blink) closedEyes();
    else {
      eye(3, 7, 2, 1, 1); eye(10, 7, 2, 1, 1);
      px(3, 9, TEAR); px(3, 10, TEAR); px(12, 9, TEAR);
    }
    px(7, 12, mouth); px(8, 12, mouth); px(6, 13, mouth); px(9, 13, mouth);
  } else if (face === "smug") {
    row(5, 3, 3, brow); row(6, 10, 3, brow);
    if (blink) closedEyes();
    else {
      row(7, 3, 3, lid); row(7, 10, 3, lid);
      eye(3, 8, 1, 2, 0); eye(10, 8, 1, 2, 0);
    }
    row(12, 6, 3, mouth); px(9, 11, mouth); px(10, 11, mouth);
  } else if (face === "pout") {
    px(3, 6, brow); px(4, 6, brow); px(5, 7, brow);
    px(10, 7, brow); px(11, 6, brow); px(12, 6, brow);
    for (const x0 of L) row(8, x0, 3, dark);
    px(7, 12, mouth); px(8, 11, mouth); px(8, 13, mouth);
    for (const [x, y] of [[1, 10], [2, 10], [13, 10], [14, 10]]) px(x, y, "#ec6f8f");
  } else if (face === "think") {
    row(5, 3, 3, brow); row(6, 10, 3, brow);
    if (blink) closedEyes();
    else { eye(3, 7, 2, 2, 0); eye(10, 7, 2, 2, 0); }
    row(12, 8, 3, mouth);
  } else if (face === "panic") {
    px(3, 6, brow); px(4, 5, brow); px(5, 4, brow);
    px(10, 4, brow); px(11, 5, brow); px(12, 6, brow);
    eye(3, 7, 3, 1, 1); eye(10, 7, 3, 1, 1);
    for (const x of [5, 7, 9]) px(x, 12, "#2a0f16");
    for (const x of [6, 8, 10]) px(x, 13, "#2a0f16");
    px(14, 5, SWEAT); px(14, 6, SWEAT); px(15, 6, SWEAT);
  } else if (face === "shock") {
    row(4, 3, 3, brow); row(4, 10, 3, brow);
    eye(3, 6, 3, 1, 1); eye(10, 6, 3, 1, 1);
    for (let y = 11; y < 14; y++) row(y, 6, 4, y === 11 ? mouth : "#2a0f16");
    px(7, 13, "#e86f86"); px(8, 13, "#e86f86");
  } else if (face === "eyeroll") {
    row(5, 3, 3, brow); row(5, 10, 3, brow);
    row(6, 3, 3, lid); row(6, 10, 3, lid);
    eye(3, 7, 2, 1, 0); eye(10, 7, 2, 1, 0);
    row(12, 6, 4, mouth);
  } else if (face === "sigh") {
    row(6, 3, 3, brow); row(6, 10, 3, brow);
    px(3, 8, dark); px(4, 8, dark); px(5, 7, dark);
    px(10, 7, dark); px(11, 8, dark); px(12, 8, dark);
    px(7, 12, mouth); px(8, 12, mouth); px(7, 13, mouth); px(8, 13, mouth);
  } else if (face === "wince") {
    px(3, 6, brow); px(4, 6, brow); px(5, 5, brow);
    px(10, 5, brow); px(11, 6, brow); px(12, 6, brow);
    px(3, 7, dark); px(4, 8, dark); px(5, 8, dark); px(3, 9, dark);
    px(12, 7, dark); px(11, 8, dark); px(10, 8, dark); px(12, 9, dark);
    teeth(5, 10);
  } else if (face === "meh") {
    row(5, 3, 3, brow); row(5, 10, 3, brow);
    if (blink) closedEyes();
    else {
      row(7, 3, 3, lid); row(7, 10, 3, lid);
      eye(3, 8, 1, 1, 0); eye(10, 8, 1, 1, 0);
    }
    row(12, 5, 6, mouth);
  }
  if (p.blush && face !== "angry" && face !== "pout") {
    px(2, 10, p.blush);
    px(13, 10, p.blush);
  }
}
