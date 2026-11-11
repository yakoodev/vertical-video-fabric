/**
 * Пиксельные текстуры «воксельного» стиля (канвас, детерминированный ГПСЧ, фильтр nearest — пиксели
 * остаются квадратными на любом плане): шум-«блок» (лёгкая неровность тона, как у кубиков), доски
 * пола, лица героя.
 *
 * Лица — двух видов, по форме героя (voxel.ts, ``shape``):
 *   block — пиксель-арт 16×16 во всю переднюю грань кубической головы (кожа, чёлка, брови, глаза, рот);
 *   soft  — «аниме»-наклейка 128×128 с прозрачным фоном: большие глаза с бликами, тонкие брови, румянец;
 *           кладётся поверх скруглённой головы цвета кожи.
 * Выражения: neutral, blink, sleep, wide (проснулся/ахнула, глаза круглые), happy, frown, laugh, talk, tired, yawn;
 * эмоции пресетов игры (angry, sad, smug, pout, think, panic, shock, eyeroll, sigh, wince, meh) — voxelfaces.ts.
 */
import * as THREE from "three";
import { rng } from "./timeline.ts";
import { blockEmotionFace, EMOTION_FACES, softEmotionFace, type EmotionFace } from "./voxelfaces.ts";

export type VoxelFace = "neutral" | "blink" | "sleep" | "wide" | "happy" | "frown" | "laugh" | "talk" | "tired" | "yawn" | EmotionFace;
export const VOXEL_FACES: VoxelFace[] = ["neutral", "blink", "sleep", "wide", "happy", "frown", "laugh", "talk", "tired", "yawn", ...EMOTION_FACES];
const isEmotion = (f: string): f is EmotionFace => (EMOTION_FACES as readonly string[]).includes(f);

function canvas(w: number, h: number): [HTMLCanvasElement, CanvasRenderingContext2D] {
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  return [c, c.getContext("2d")!];
}

/** Текстура «пиксели»: без размытия и мипмапов, повтор по UV. */
export function pixelTexture(c: HTMLCanvasElement, srgb = true): THREE.CanvasTexture {
  const t = new THREE.CanvasTexture(c);
  if (srgb) t.colorSpace = THREE.SRGBColorSpace;
  t.magFilter = THREE.NearestFilter;
  t.minFilter = THREE.NearestFilter;
  t.generateMipmaps = false;
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  return t;
}

const shade = (color: string, k: number) => {
  const c = new THREE.Color(color);
  c.multiplyScalar(k);
  return `#${c.getHexString()}`;
};

/** Серый шум 16×16 (0.86–1.0): множитель цвета материала — одна текстура на все цвета. */
let greyNoise: THREE.CanvasTexture | null = null;
export function noiseTexture(): THREE.CanvasTexture {
  if (greyNoise) return greyNoise;
  const [c, g] = canvas(16, 16);
  const r = rng(4242);
  for (let y = 0; y < 16; y++) for (let x = 0; x < 16; x++) {
    const v = Math.round(255 * (0.86 + r() * 0.14));
    g.fillStyle = `rgb(${v},${v},${v})`;
    g.fillRect(x, y, 1, 1);
  }
  greyNoise = pixelTexture(c);
  return greyNoise;
}

/** Цветной шум-«блок» 16×16 (для поверхностей комнаты: surfaces.ts, kind ``pixel``). */
export function pixelNoiseCanvas(color: string, seed = 5, spread = 0.12): HTMLCanvasElement {
  const [c, g] = canvas(16, 16);
  const r = rng(seed);
  for (let y = 0; y < 16; y++) for (let x = 0; x < 16; x++) {
    g.fillStyle = shade(color, 1 - spread + r() * spread * 1.4);
    g.fillRect(x, y, 1, 1);
  }
  return c;
}

/** Доски пола 16×16: четыре доски по 4 пикселя, тёмные швы, торцы со сдвигом, разный тон. */
export function pixelPlanksCanvas(color: string, seed = 9): HTMLCanvasElement {
  const [c, g] = canvas(16, 16);
  const r = rng(seed);
  for (let row = 0; row < 4; row++) {
    const tone = 0.88 + r() * 0.2;
    const cut = Math.floor(r() * 16);
    for (let y = 0; y < 4; y++) for (let x = 0; x < 16; x++) {
      const seam = y === 3 || x === cut;
      g.fillStyle = shade(color, seam ? 0.62 : tone * (0.94 + r() * 0.1));
      g.fillRect(x, row * 4 + y, 1, 1);
    }
  }
  return c;
}

/** Цвета лица. */
export interface FacePalette {
  skin: string;
  hair: string;
  eyes?: string;
  mouth?: string;
  blush?: string;
  lashes?: boolean;
  /** Круги под глазами (не выспался): цвет полумесяцев под нижним веком (мягкое лицо). */
  eyebags?: string;
}

/**
 * Пиксельное лицо 16×16 (блок): кожа с шумом, чёлка рваным краем, брови, глаза 3×2 (белок + радужка),
 * нос тенью, рот. Возвращает канвас — текстуру во всю переднюю грань.
 */
export function blockFaceCanvas(face: VoxelFace, p: FacePalette, blink = false): HTMLCanvasElement {
  const [c, g] = canvas(16, 16);
  const r = rng(77);
  const px = (x: number, y: number, col: string) => {
    g.fillStyle = col;
    g.fillRect(x, y, 1, 1);
  };
  for (let y = 0; y < 16; y++) for (let x = 0; x < 16; x++) px(x, y, shade(p.skin, 0.95 + r() * 0.08));
  // Чёлка: три ряда волос, нижний край рваный (пряди).
  const fringe = [4, 3, 4, 5, 4, 3, 3, 4, 3, 3, 4, 5, 4, 3, 4, 4];
  for (let x = 0; x < 16; x++) for (let y = 0; y < fringe[x]; y++) px(x, y, shade(p.hair, 0.9 + r() * 0.2));
  const dark = p.eyes || "#1c1426";
  const brow = shade(p.hair, 0.8);
  const white = "#f4f1f6";
  const mouth = p.mouth || "#7a2f3a";
  const nose = shade(p.skin, 0.84);
  px(7, 10, nose);
  px(8, 10, nose);
  if (isEmotion(face)) blockEmotionFace(px, face, p, blink);
  else if (face === "sleep" || face === "blink") {
    for (const x0 of [3, 10]) {
      for (let x = 0; x < 3; x++) px(x0 + x, 8, dark);
      px(x0 === 3 ? 3 : 12, 7, shade(p.skin, 0.86));
      px(x0 + 0, 6, brow);
      px(x0 + 1, 6, brow);
      px(x0 + 2, 6, brow);
    }
    if (face === "sleep") {
      px(7, 12, mouth);
      px(8, 12, mouth);
      px(8, 13, shade(mouth, 0.7));
    } else for (let x = 6; x < 10; x++) px(x, 12, mouth);
  } else if (face === "wide") {
    // Брови вверх, глаза 3×3 с точкой-зрачком, рот «о».
    for (const x0 of [3, 10]) {
      for (let x = 0; x < 3; x++) px(x0 + x, 5, brow);
      for (let y = 7; y < 10; y++) for (let x = 0; x < 3; x++) px(x0 + x, y, white);
      px(x0 + 1, 8, dark);
    }
    for (let y = 11; y < 14; y++) for (let x = 7; x < 9; x++) px(x, y, y === 12 ? "#2a0f16" : mouth);
  } else if (face === "happy" || face === "laugh") {
    for (const x0 of [3, 10]) {
      px(x0, 8, dark);
      px(x0 + 1, 7, dark);
      px(x0 + 2, 8, dark);
      for (let x = 0; x < 3; x++) px(x0 + x, 5, brow);
    }
    if (face === "laugh") {
      for (let x = 5; x < 11; x++) px(x, 11, "#2a0f16");
      for (let x = 6; x < 10; x++) px(x, 12, x === 7 || x === 8 ? "#e86f86" : "#2a0f16");
    } else {
      for (let x = 6; x < 10; x++) px(x, 12, mouth);
      px(5, 11, mouth);
      px(10, 11, mouth);
    }
  } else if (face === "frown") {
    // Брови к переносице вверх («домиком»), глаза уже, рот дугой вниз.
    for (const [x0, iris, inner] of [[3, 5, 5], [10, 10, 10]] as const) {
      for (let x = 0; x < 3; x++) px(x0 + x, x0 + x === inner ? 5 : 6, brow);
      for (let x = 0; x < 3; x++) px(x0 + x, 8, x0 + x === iris ? dark : white);
    }
    for (let x = 7; x < 9; x++) px(x, 12, mouth);
    px(6, 13, mouth);
    px(9, 13, mouth);
  } else {
    for (const [x0, iris] of [[3, 5], [10, 10]] as const) {
      for (let x = 0; x < 3; x++) px(x0 + x, 6, brow);
      for (let y = 7; y < 9; y++) for (let x = 0; x < 3; x++) px(x0 + x, y, x0 + x === iris ? dark : white);
    }
    if (face === "talk") for (let y = 11; y < 13; y++) for (let x = 6; x < 10; x++) px(x, y, y === 12 ? "#2a0f16" : mouth);
    else for (let x = 6; x < 10; x++) px(x, 12, mouth);
    if (p.lashes) {
      px(2, 6, dark);
      px(13, 6, dark);
    }
  }
  if (p.blush && !isEmotion(face)) {
    px(2, 10, p.blush);
    px(13, 10, p.blush);
  }
  return c;
}

/**
 * «Аниме»-лицо 128×128 (мягкий герой) на прозрачном фоне: большие глаза (верхнее веко, радужка с
 * градиентом, два блика), тонкие брови, маленький рот, румянец; ``lashes`` — ресницы веером (героиня).
 * Выражения: neutral, blink, sleep (закрытые глаза-дуги, рот приоткрыт), wide (круглые глаза, рот «о»),
 * happy (^^), frown (брови домиком к переносице, рот дугой вниз), laugh (^^ и открытый смех), talk (рот открыт).
 */
export function softFaceCanvas(face: VoxelFace, p: FacePalette, blink = false): HTMLCanvasElement {
  const [c, g] = canvas(128, 128);
  if (isEmotion(face)) {
    softEmotionFace(c, face, p, blink);
    return c;
  }
  const ink = p.eyes ? shade(p.eyes, 0.42) : "#20152c";
  const iris = p.eyes || "#7a4bd6";
  const brow = shade(p.hair, 0.85);
  const mouth = p.mouth || (p.lashes ? "#b2445c" : "#8a3344");
  g.lineCap = "round";
  g.lineJoin = "round";
  // Румянец (сильнее, когда смеётся или ахнула).
  g.fillStyle = p.blush || "rgba(240,120,140,0.35)";
  for (const x of [26, 102]) {
    g.beginPath();
    g.ellipse(x, 84, face === "laugh" || face === "wide" ? 13 : 11, 6, 0, 0, Math.PI * 2);
    g.fill();
  }
  const eyes = [38, 90];
  if (p.eyebags && face !== "laugh" && face !== "happy") {
    // Круги под глазами: мягкие тёмные полумесяцы под нижним веком.
    g.strokeStyle = p.eyebags;
    for (const x of eyes) {
      g.lineWidth = 5;
      g.beginPath();
      g.arc(x, 66, 15, Math.PI * 0.2, Math.PI * 0.8);
      g.stroke();
      g.lineWidth = 2.5;
      g.beginPath();
      g.arc(x, 70, 14, Math.PI * 0.28, Math.PI * 0.72);
      g.stroke();
    }
  }
  const browY = face === "wide" ? 29 : face === "sleep" ? 44 : face === "frown" ? 40 : face === "yawn" ? 31 : face === "tired" ? 40 : 37;
  g.strokeStyle = brow;
  g.lineWidth = 4;
  for (const x of eyes) {
    const inner = x < 64 ? 1 : -1;
    g.beginPath();
    if (face === "frown") {
      // Домиком: внутренние концы вверх-к переносице — расстроилась.
      g.moveTo(x - 13 * inner, browY + 2);
      g.quadraticCurveTo(x, browY - 1, x + 12 * inner, browY - 7);
    } else {
      g.moveTo(x - 13 * inner, browY + (face === "wide" ? -2 : 1));
      g.quadraticCurveTo(x, browY - 5, x + 12 * inner, browY + (face === "sleep" ? 3 : 0));
    }
    g.stroke();
  }
  const closed = face === "sleep" || face === "blink";
  const smiling = face === "happy" || face === "laugh";
  g.strokeStyle = ink;
  if (face === "yawn") {
    // Зевает: глаза зажмурены «><»-дугами.
    g.lineWidth = 5;
    for (const x of eyes) {
      const side = x < 64 ? -1 : 1;
      g.beginPath();
      g.moveTo(x - 11 * side, 54);
      g.quadraticCurveTo(x + 2 * side, 60, x - 11 * side, 66);
      g.stroke();
    }
  } else if (face === "tired") {
    // Не выспался: веки опущены до середины глаза, взгляд вниз.
    for (const x of eyes) {
      const y = 64;
      g.fillStyle = "#ffffff";
      g.beginPath();
      g.ellipse(x, y, 12, 9, 0, 0, Math.PI);
      g.fill();
      g.fillStyle = shade(iris, 0.8);
      g.beginPath();
      g.ellipse(x, y + 1, 9, 7, 0, 0, Math.PI);
      g.fill();
      g.fillStyle = ink;
      g.beginPath();
      g.ellipse(x, y + 1, 4.5, 4, 0, 0, Math.PI);
      g.fill();
      g.lineWidth = 5;
      g.beginPath();
      g.moveTo(x - 13, y);
      g.quadraticCurveTo(x, y - 3, x + 13, y);
      g.stroke();
    }
  } else if (closed) {
    // Закрытые глаза — дуги вниз, ресницы вниз.
    g.lineWidth = 5;
    for (const x of eyes) {
      g.beginPath();
      g.arc(x, 58, 13, Math.PI * 0.12, Math.PI * 0.88);
      g.stroke();
      if (p.lashes) {
        const side = x < 64 ? -1 : 1;
        g.lineWidth = 3;
        g.beginPath();
        g.moveTo(x + side * 11, 64);
        g.lineTo(x + side * 16, 68);
        g.stroke();
        g.lineWidth = 5;
      }
    }
  } else if (smiling) {
    g.lineWidth = 5;
    for (const x of eyes) {
      g.beginPath();
      g.arc(x, 66, 12, Math.PI * 1.15, Math.PI * 1.85);
      g.stroke();
    }
  } else {
    const wide = face === "wide";
    const rx = wide ? 14 : 12, ry = wide ? 18 : face === "frown" ? 14 : 16;
    for (const x of eyes) {
      const y = 62;
      g.fillStyle = "#ffffff";
      g.beginPath();
      g.ellipse(x, y, rx, ry, 0, 0, Math.PI * 2);
      g.fill();
      const ir = wide ? 8 : 10.5;
      const grad = g.createLinearGradient(0, y - ir, 0, y + ir);
      grad.addColorStop(0, shade(iris, 0.55));
      grad.addColorStop(1, shade(iris, 1.25));
      g.fillStyle = grad;
      g.beginPath();
      g.ellipse(x, y + (wide ? 0 : 2), ir * 0.92, ir * 1.15, 0, 0, Math.PI * 2);
      g.fill();
      g.fillStyle = ink;
      g.beginPath();
      g.ellipse(x, y + (wide ? 0 : 2), ir * 0.42, ir * 0.55, 0, 0, Math.PI * 2);
      g.fill();
      g.fillStyle = "#ffffff";
      g.beginPath();
      g.arc(x - 4, y - 5, wide ? 3 : 4, 0, Math.PI * 2);
      g.fill();
      g.beginPath();
      g.arc(x + 4, y + 6, 2, 0, Math.PI * 2);
      g.fill();
      // Верхнее веко (при frown — опущено), ресница наружу.
      g.strokeStyle = ink;
      g.lineWidth = p.lashes ? 6 : 5;
      g.beginPath();
      g.ellipse(x, y + (face === "frown" ? 3 : 0), rx + 1, ry + 1, 0, Math.PI * 1.08, Math.PI * 1.92);
      g.stroke();
      const side = x < 64 ? -1 : 1;
      g.beginPath();
      g.moveTo(x + side * rx, y - ry * 0.35);
      g.lineTo(x + side * (rx + 5), y - ry * 0.6);
      if (p.lashes) {
        g.moveTo(x + side * (rx - 3), y - ry * 0.7);
        g.lineTo(x + side * (rx + 3), y - ry * 1.0);
      }
      g.stroke();
    }
  }
  g.strokeStyle = mouth;
  g.fillStyle = mouth;
  g.lineWidth = 4;
  g.beginPath();
  if (face === "wide") {
    g.ellipse(64, 98, 6, 8, 0, 0, Math.PI * 2);
    g.fill();
  } else if (face === "yawn") {
    // Зевок: рот высоким овалом, тёмный, с языком.
    g.fillStyle = "#4a1420";
    g.ellipse(64, 100, 11, 15, 0, 0, Math.PI * 2);
    g.fill();
    g.fillStyle = "#e0707f";
    g.beginPath();
    g.ellipse(64, 109, 7, 4.5, 0, 0, Math.PI * 2);
    g.fill();
  } else if (face === "tired") {
    g.moveTo(58, 98);
    g.lineTo(70, 98);
    g.stroke();
  } else if (face === "laugh") {
    // Открытый смех: полукруг с языком.
    g.arc(64, 90, 13, Math.PI * 0.02, Math.PI * 0.98);
    g.closePath();
    g.fill();
    g.fillStyle = "#ff8fa3";
    g.beginPath();
    g.ellipse(64, 99, 7, 4, 0, 0, Math.PI * 2);
    g.fill();
  } else if (face === "happy") {
    g.arc(64, 92, 9, Math.PI * 0.1, Math.PI * 0.9);
    g.fill();
  } else if (face === "talk") {
    g.ellipse(64, 97, 6, 5, 0, 0, Math.PI * 2);
    g.fill();
  } else if (face === "sleep") {
    g.ellipse(64, 97, 4, 3, 0, 0, Math.PI * 2);
    g.fill();
  } else if (face === "frown") {
    g.moveTo(57, 100);
    g.quadraticCurveTo(64, 93, 71, 100);
    g.stroke();
  } else {
    g.moveTo(58, 96);
    g.quadraticCurveTo(64, 99, 70, 96);
    g.stroke();
  }
  return c;
}

/** Картинка «Z» (спит): пиксельная буква 7×7 с тёмной обводкой. */
export function sleepZCanvas(color = "#e9dcff"): HTMLCanvasElement {
  const [c, g] = canvas(9, 9);
  const rows = ["1111111", "0000011", "0000110", "0001100", "0011000", "0110000", "1111111"];
  g.fillStyle = "rgba(20,10,30,0.85)";
  for (let y = 0; y < 7; y++) for (let x = 0; x < 7; x++) if (rows[y][x] === "1") g.fillRect(x, y + 1, 2, 2);
  g.fillStyle = color;
  for (let y = 0; y < 7; y++) for (let x = 0; x < 7; x++) if (rows[y][x] === "1") g.fillRect(x + 1, y + 1, 1, 1);
  return c;
}
