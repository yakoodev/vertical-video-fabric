/**
 * Блочный «пиксельный» зверёк (``style: "critter"``) — герой мем-канала в духе маскота Claude Code: широкий плоский
 * терракотовый корпус-кирпичик, два чёрных глаза-прямоугольника, четыре коротких ножки-кубика в ряд и два «обрубка»
 * рук по бокам. Рта нет — эмоции глазами (форма, веки, брови-блоки), телом (сжатие-растяжение, подскок, вздох-
 * «сдувание») и ручками. Всё из скруглённых коробок (обводка тушью toon.ts ложится ровно), без моделей и логотипов.
 *
 * Движение — функция t (как у deskbot.ts, кадры в любом порядке):
 * - ``pose: [{t, ease, lean, tilt, turn, hop, squash, deflate, armL, armR, type, shake}]`` — наклон вперёд/вбок (°),
 *   поворот (° к yaw), подскок (м при ширине 0.3), сжатие (1 — нет, объём сохраняется), ``deflate`` 0..1 — вздох
 *   (оседает, ручки повисают), руки — ``[поднять°, вперёд°, выдвинуть 0..1, покачивание 0..1]`` или имя из
 *   ``NUB_POSES``, ``type`` 0..1 — печать (ручки стучат), ``shake`` 0..1 — дрожь;
 * - ``face: [{t, expr, look, blend}]`` — выражение из ``CRITTER_EXPR`` (blend — переход, с), ``look`` [x, y] −1..1 —
 *   глаза целиком смещаются взглядом; моргание само (засеянное) + ``blinks: [t…]``;
 * - ``voice: {fps, start, values, gain, bounce}`` — огибающая громкости (0..1 на кадр): речь читается телом —
 *   корпус пружинит и подпрыгивает на ударных слогах, глаза чуть раскрываются; ``bounce`` — сила (1 по умолчанию);
 * - ``stepFps`` — поза и лицо «по двойкам» (12–15 раз в секунду), как у рисованной анимации.
 *
 * Внешний вид ``critter: {body, shade, eye, shine, cheeks, headset, headsetAccent, label}``, ``width`` — ширина
 * корпуса в метрах (0.3 по умолчанию). ``critterHelper`` — малыш той же породы для вставок (primitive ``helper_bot``
 * c ``style: "voxel"``).
 */
import * as THREE from "three";
import { RoundedBoxGeometry } from "three/examples/jsm/geometries/RoundedBoxGeometry.js";
import { ease, rng, sampleKeys, sampleNumber, type Key } from "./timeline.ts";
import type { Character } from "./character.ts";
import type { Prop } from "./props.ts";

export interface CritterLook {
  body?: string;
  /** Тёмный тон того же цвета: ножки, низ ручек, детали. */
  shade?: string;
  eye?: string;
  shine?: string;
  cheeks?: string;
  /** Гарнитура поддержки: цвет дужки и чашек; нет — без гарнитуры. */
  headset?: string;
  headsetAccent?: string;
  /** Маленькая бирка с именем в нижнем углу морды (например «Claude»). */
  label?: string;
  /** Бейдж на груди (низ морды по центру): подпись слева, номер крупно в тёмном квадрате справа —
   * «аккаунт» + «3». Одинаковые герои отличаются только номером. */
  badge?: { text?: string; number?: string | number; accent?: string };
  /** Собственное свечение корпуса (тень остаётся тёплой), 0..0.3; по умолчанию 0.12. */
  glow?: number;
}

export interface CritterSpec {
  id: string;
  style: "critter";
  critter?: CritterLook;
  width?: number;
  position?: number[];
  yaw?: number;
  pose?: Key[];
  face?: Key[];
  blinks?: number[];
  voice?: { fps?: number; start?: number; values?: number[]; gain?: number; bounce?: number };
  /** Синоним voice (сцены deskbot передают огибающую полем mouth). */
  mouth?: { fps?: number; start?: number; values?: number[]; gain?: number; bounce?: number };
  stepFps?: number;
  seed?: number;
  /** false — моргать только по ``blinks``. */
  autoBlink?: boolean;
}

/** Ручки-обрубки: [поднять в сторону°, махнуть вперёд°, выдвинуть 0..1, покачивание 0..1]. */
export const NUB_POSES: Record<string, number[]> = {
  rest: [-8, 0, 0, 0],
  down: [-38, 4, 0, 0],
  up: [75, 5, 0.4, 0],
  wave: [70, 10, 0.4, 1],
  flail: [62, 18, 0.5, 1],
  shrug: [42, -6, 0.35, 0],
  point: [12, 62, 0.6, 0],
  jab: [22, 55, 0.7, 0.5],
  fist: [24, 30, 0.3, 0.6],
  cross: [-4, 72, 0.15, 0],
  hips: [-30, -14, 0.1, 0],
  back: [-18, -40, 0, 0],
  type: [-2, 58, 0.25, 0],
  facepalm: [58, 74, 0.55, 0],
  clasp: [-6, 64, 0.1, 0],
};

/** Параметры лица (только глаза и «значки»). */
export interface CritterFace {
  /** Размер глаз (множители), сдвиг вверх (доли высоты глаза), разведение. */
  eyeW: number; eyeH: number; eyeY: number; spread: number;
  /** Верхнее веко: срез сверху (доля), наклон (° — плюс: внутренний край ниже, злость); нижнее веко (доля). */
  lidTop: number; lidAngle: number; lidBot: number;
  /** Формы: 1 — глаза-полоски (—), дуги ^ ^, щёлочки > <, закрытые (вздох, дуги вниз). */
  flat: number; happy: number; squint: number; closed: number;
  /** Один глаз меньше (скепсис), −1..1. */
  asym: number;
  /** Блик в глазу (удивление, умиление). */
  shine: number;
  /** Брови-блоки: видимость, подъём, наклон (° — плюс: злые), разница по высоте. */
  brow: number; browY: number; browAngle: number; browAsym: number;
  blush: number; sweat: number; vein: number; tear: number; dots: number;
  /** Глаза-крестики «x x» (вырубился). */
  dead: number;
  /** Корпус краснеет (ярость), 0..1. */
  tint: number;
}

const BASE: CritterFace = {
  eyeW: 1, eyeH: 1, eyeY: 0, spread: 1,
  lidTop: 0, lidAngle: 0, lidBot: 0,
  flat: 0, happy: 0, squint: 0, closed: 0, asym: 0, shine: 0,
  brow: 0, browY: 0, browAngle: 0, browAsym: 0,
  blush: 0, sweat: 0, vein: 0, tear: 0, dots: 0, dead: 0, tint: 0,
};

/**
 * Выражения (поправки к BASE). Шесть опорных: ``happy`` (^ ^), ``annoyed`` (— —), ``surprised`` (широко),
 * ``angry`` (брови-блоки клином), ``sad``, ``smug``; остальное — оттенки для сценариев (те же имена, что у deskbot).
 */
export const CRITTER_EXPR: Record<string, Partial<CritterFace>> = {
  neutral: {},
  happy: { happy: 1, blush: 0.8, eyeY: 0.05 },
  annoyed: { flat: 1, eyeW: 1.15, eyeY: -0.05 },
  surprised: { eyeW: 1.25, eyeH: 1.12, eyeY: 0.04, shine: 1, brow: 1, browY: 0.6, browAngle: -8 },
  angry: { lidTop: 0.32, lidAngle: 24, brow: 1, browY: -0.2, browAngle: 26, vein: 1 },
  sad: { eyeH: 0.85, lidTop: 0.22, lidAngle: -18, brow: 1, browY: 0.2, browAngle: -22, tear: 0.6 },
  smug: { lidTop: 0.42, lidAngle: 6, lidBot: 0.18, eyeW: 1.1, brow: 1, browY: 0.15, browAsym: 0.8, browAngle: 4 },
  squint: { squint: 1, brow: 1, browY: -0.05, browAngle: 18 },
  // Оттенки (совместимы по именам со сценариями deskbot).
  grumpy: { lidTop: 0.4, lidAngle: 12, brow: 1, browY: -0.15, browAngle: 14 },
  meh: { flat: 1, eyeW: 1.05, eyeY: -0.05 },
  side: { lidTop: 0.45, lidAngle: 6, lidBot: 0.12, brow: 1, browY: 0, browAngle: 8, browAsym: 0.6 },
  roll: { eyeH: 0.75, eyeY: 0.35, lidBot: 0.1, brow: 1, browY: 0.45, browAngle: -4 },
  sigh: { closed: 1, brow: 1, browY: 0.05, browAngle: -16 },
  fake: { happy: 1, brow: 1, browY: 0.35, browAngle: -6, sweat: 0.8 },
  shocked: { eyeW: 1.3, eyeH: 1.15, eyeY: 0.04, shine: 1, brow: 1, browY: 0.6, browAngle: -10, sweat: 0.8 },
  stare: { flat: 1, eyeW: 1.3, eyeH: 1.15 },
  offended: { lidTop: 0.32, lidAngle: -16, lidBot: 0.1, brow: 1, browY: 0.3, browAngle: -22, tear: 0.5 },
  hurt: { lidTop: 0.28, lidAngle: -20, lidBot: 0.15, brow: 1, browY: 0.35, browAngle: -26, tear: 1 },
  focused: { lidTop: 0.3, lidAngle: 6, brow: 1, browY: -0.1, browAngle: 10 },
  working: { lidTop: 0.36, lidAngle: 4, brow: 1, browY: -0.1, browAngle: 8, dots: 1 },
  /** Бурчит под нос: косится, одна бровь выше. */
  mutter: { lidTop: 0.46, lidAngle: 8, lidBot: 0.1, brow: 1, browY: -0.05, browAngle: 10, browAsym: 0.5 },
  /** Зависть: прищур снизу и сверху, бровь домиком. */
  envy: { lidTop: 0.4, lidAngle: -4, lidBot: 0.3, brow: 1, browY: 0.1, browAsym: 0.9, browAngle: -4 },
  /** Брезгливое «это что?»: глаза разного размера. */
  disgust: { lidTop: 0.25, lidAngle: -8, lidBot: 0.22, asym: 0.55, brow: 1, browY: 0.25, browAngle: -10, browAsym: -0.9 },
  /** Ярость: щёлочки-клинья, корпус краснеет, венка. */
  rage: { lidTop: 0.38, lidAngle: 30, eyeW: 1.1, brow: 1, browY: -0.28, browAngle: 32, vein: 1, tint: 1 },
  /** Каменное лицо: глаза-полоски. */
  deadpan: { flat: 1 },
  /** Обречённо: полоски пониже, брови домиком, слеза. */
  doomed: { flat: 1, eyeY: -0.12, brow: 1, browY: 0.15, browAngle: -20, tear: 0.8 },
  /** Мечтательно: прикрытые снизу глаза, румянец, брови домиком. */
  dreamy: { lidTop: 0.12, lidBot: 0.32, eyeY: 0.05, blush: 1, brow: 1, browY: 0.4, browAngle: -10 },
  /** Умиление/«ну ладно»: дуги и румянец. */
  cute: { happy: 1, blush: 1, eyeY: 0.05, brow: 1, browY: 0.35, browAngle: -6 },
  /** Спит: закрытые глаза-ковшики без бровей. */
  sleep: { closed: 1, eyeY: -0.05 },
  /** Вырубился (лимит): глаза-крестики. */
  ko: { dead: 1, sweat: 0.6 },
};

function faceAt(name: string | undefined): CritterFace {
  return { ...BASE, ...(CRITTER_EXPR[name || "neutral"] || {}) };
}

function mixFace(a: CritterFace, b: CritterFace, k: number): CritterFace {
  const out = { ...a } as any;
  for (const key of Object.keys(BASE)) out[key] = (a as any)[key] + ((b as any)[key] - (a as any)[key]) * k;
  return out;
}

type Nub = number[];

function nubOf(v: any): Nub {
  if (Array.isArray(v)) return [Number(v[0]) || 0, Number(v[1]) || 0, Number(v[2]) || 0, Number(v[3]) || 0];
  return NUB_POSES[String(v || "rest")] || NUB_POSES.rest;
}

const D2R = Math.PI / 180;

/** Пропорции в долях ширины корпуса (ширина = 1). */
export const CP = {
  bodyW: 1, bodyH: 0.56, bodyD: 0.5, radius: 0.075,
  legH: 0.15, legW: 0.115, legD: 0.26, legX: [-0.375, -0.2, 0.2, 0.375],
  armW: 0.17, armH: 0.18, armD: 0.26, armY: 0.38,
  eyeX: 0.21, eyeY: 0.55, eyeW: 0.058, eyeH: 0.23,
};

export function blockMat(c: string, name: string, glow = 0) {
  const m = new THREE.MeshStandardMaterial({ color: new THREE.Color(c), roughness: 0.62, metalness: 0, name });
  if (glow > 0) {
    // Чуть «светится» своим цветом: в тени остаётся тёплой терракотой, а не бурой.
    m.emissive = new THREE.Color(c);
    m.emissiveIntensity = glow;
  }
  return m;
}

/**
 * Лицо на канвасе: прозрачный фон, чёрные глаза-блоки, брови, значки. W×H — пиксели (канвас = вся передняя грань
 * корпуса), u — пикселей на единицу. Размеры фигур — от k (высота глаза): глаз-прямоугольник 0.5k × k.
 */
export function paintCritterFace(g: CanvasRenderingContext2D, W: number, H: number, u: number, f: CritterFace, lk: number[],
  blink: number, talk: number, t: number, look: CritterLook = {}, dims: typeof CP = CP) {
  const eye = look.eye || "#1b1214";
  const shineC = look.shine || "#ffffff";
  g.clearRect(0, 0, W, H);
  const cx = W / 2;
  const k = dims.eyeH * u; // высота глаза в покое, px
  const baseY = H - dims.eyeY * dims.bodyH * u;
  const ew = dims.eyeW * u * f.eyeW * (1 + 0.04 * talk); // полуширина глаза
  const eh0 = k * f.eyeH * (1 + 0.1 * talk);
  const ex = dims.eyeX * u * f.spread;
  const lx = lk[0] * u * 0.045, ly = -lk[1] * u * 0.035;
  const open = Math.max(0, 1 - blink);
  g.save();
  g.lineJoin = "miter";
  g.lineCap = "butt";
  g.fillStyle = eye;
  g.strokeStyle = eye;
  for (const s of [-1, 1]) {
    // s = −1 — левый на экране (правый глаз зверька).
    const x = cx + s * ex + lx;
    const sz = 1 - Math.max(0, f.asym * s) * 0.45;
    const eh = eh0 * sz;
    const y = baseY - f.eyeY * k + ly;
    if (f.happy > 0.5) {
      // ^ — уголок толстой линией.
      g.lineWidth = k * 0.22;
      g.beginPath();
      g.moveTo(x - k * 0.5, y + k * 0.18);
      g.lineTo(x, y - k * 0.24);
      g.lineTo(x + k * 0.5, y + k * 0.18);
      g.stroke();
      continue;
    }
    if (f.squint > 0.5) {
      // > < — клинья к переносице.
      const d = -s;
      g.lineWidth = k * 0.2;
      g.beginPath();
      g.moveTo(x - d * k * 0.3, y - k * 0.32);
      g.lineTo(x + d * k * 0.3, y);
      g.lineTo(x - d * k * 0.3, y + k * 0.32);
      g.stroke();
      continue;
    }
    if (f.dead > 0.5) {
      // x — две толстые диагонали.
      g.lineWidth = k * 0.2;
      const r = k * 0.32;
      g.beginPath();
      g.moveTo(x - r, y - r);
      g.lineTo(x + r, y + r);
      g.moveTo(x + r, y - r);
      g.lineTo(x - r, y + r);
      g.stroke();
      continue;
    }
    if (f.closed > 0.5) {
      // Закрытые (вздох): «ковшик» вниз.
      g.lineWidth = k * 0.17;
      g.beginPath();
      g.moveTo(x - k * 0.45, y);
      g.lineTo(x - k * 0.25, y + k * 0.14);
      g.lineTo(x + k * 0.25, y + k * 0.14);
      g.lineTo(x + k * 0.45, y);
      g.stroke();
      continue;
    }
    if (f.flat > 0.5) {
      // Полоска «—» (моргание — тоньше).
      const bw = k * 0.95 * Math.max(1, f.eyeW * 0.9), bh = k * 0.24 * Math.max(0.4, open);
      g.fillRect(x - bw / 2, y - bh / 2 + k * 0.05, bw, bh);
      continue;
    }
    // Прямоугольник, срезанный веками: верхний край — наклонная (lidAngle > 0 — внутренний край ниже).
    const h = Math.max(k * 0.08, eh * open);
    const top = y - h / 2, bot = y + h / 2 - h * f.lidBot;
    const slope = Math.tan(f.lidAngle * D2R) * -s;
    const y0 = top + h * f.lidTop;
    const lidAt = (xx: number) => (f.lidTop > 0.01 || Math.abs(f.lidAngle) > 0.5 ? y0 + slope * (xx - x) : top);
    const xl = x - ew, xr = x + ew;
    const yl = Math.min(bot - 2, Math.max(top, lidAt(xl))), yr = Math.min(bot - 2, Math.max(top, lidAt(xr)));
    g.beginPath();
    g.moveTo(xl, yl);
    // Если наклонная пересекает верхний край внутри глаза — излом в точке пересечения.
    if (Math.abs(slope) > 1e-6) {
      const xc = x + (top - y0) / slope;
      if (xc > xl && xc < xr) g.lineTo(xc, top);
    }
    g.lineTo(xr, yr);
    g.lineTo(xr, bot);
    g.lineTo(xl, bot);
    g.closePath();
    g.fill();
    if (f.shine > 0.05 && open > 0.5) {
      // Блик-пиксель.
      g.save();
      g.globalAlpha = Math.min(1, f.shine);
      g.fillStyle = shineC;
      const ps = k * 0.2;
      g.fillRect(x + ew * 0.05, Math.max(yl, yr) + h * 0.08, ps, ps);
      g.restore();
    }
  }
  // Брови-блоки.
  if (f.brow > 0.02) {
    g.save();
    g.globalAlpha = Math.min(1, f.brow);
    for (const s of [-1, 1]) {
      const x = cx + s * ex + lx * 0.6;
      const raise = f.browY + (s > 0 ? f.browAsym * 0.5 : -f.browAsym * 0.15);
      const by = baseY - f.eyeY * k - eh0 * 0.5 - k * 0.24 - raise * k * 0.3 + ly * 0.5;
      const a = f.browAngle * D2R * -s; // плюс — внутренний край вниз
      g.save();
      g.translate(x - s * k * 0.04, Math.max(k * 0.2, by));
      g.rotate(a);
      g.fillRect(-k * 0.42, -k * 0.09, k * 0.84, k * 0.18);
      g.restore();
    }
    g.restore();
  }
  // Румянец: розовые пиксельные плашки под глазами, снаружи.
  if (f.blush > 0.02) {
    g.save();
    g.globalAlpha = 0.8 * Math.min(1, f.blush);
    g.fillStyle = look.cheeks || "#ff9a8a";
    for (const s of [-1, 1]) g.fillRect(cx + s * (ex + k * 0.42) - k * 0.24, baseY + k * 0.38, k * 0.48, k * 0.16);
    g.restore();
  }
  // Слеза: голубые пиксели катятся под глазом.
  if (f.tear > 0.05) {
    g.save();
    g.globalAlpha = Math.min(1, f.tear);
    g.fillStyle = "#7fd0ff";
    const x = cx + ex + ew * 0.2, y0 = baseY + k * 0.5;
    const kk = (t * 1.2) % 1;
    g.fillRect(x, y0 + kk * k * 0.4, k * 0.16, k * 0.22);
    g.restore();
  }
  // Капля пота у виска: пиксельная капля (узкий верх, широкий низ) снаружи от глаза.
  if (f.sweat > 0.05) {
    g.save();
    g.globalAlpha = Math.min(1, f.sweat);
    g.fillStyle = "#8fd8ff";
    const p = k * 0.09;
    const x = cx - ex - ew - k * 0.55, y0 = baseY - k * 0.55 + Math.sin(t * 3) * k * 0.03;
    g.fillRect(x + p, y0, p, p);
    g.fillRect(x + p * 0.5, y0 + p, p * 2, p);
    g.fillRect(x, y0 + p * 2, p * 3, p * 2);
    g.fillRect(x + p * 0.5, y0 + p * 4, p * 2, p * 0.8);
    g.fillStyle = "#ffffff";
    g.fillRect(x + p * 0.5, y0 + p * 2.2, p * 0.7, p * 0.7);
    g.restore();
  }
  // Венка «злости» — красный пиксельный крестик в углу.
  if (f.vein > 0.05) {
    g.save();
    g.globalAlpha = Math.min(1, f.vein);
    g.fillStyle = "#e8302a";
    const p = k * (0.14 + 0.02 * Math.sin(t * 14));
    const x = cx + ex + ew + k * 0.42, y0 = Math.max(k * 0.35, baseY - k * 0.85);
    for (const [dx, dy] of [[-1, -1], [1, -1], [-1, 1], [1, 1]]) {
      g.fillRect(x + dx * p * 1.1 - p / 2, y0 + dy * p * 0.4 - p * 0.2, p, p * 0.4);
      g.fillRect(x + dx * p * 0.4 - p * 0.2, y0 + dy * p * 1.1 - p / 2, p * 0.4, p);
    }
    g.restore();
  }
  // «…» — работает: три пикселя бегут под глазами.
  if (f.dots > 0.05) {
    g.save();
    g.globalAlpha = Math.min(1, f.dots);
    const p = k * 0.13;
    for (let i = 0; i < 3; i++) {
      const kk = Math.max(0, Math.sin(t * 8 - i * 0.9));
      g.fillRect(cx - p * 2.5 + i * p * 2, baseY + k * 0.62 - kk * p * 0.7, p, p);
    }
    g.restore();
  }
  g.restore();
}

export interface Parts {
  root: THREE.Group;
  lean: THREE.Group;
  turn: THREE.Group;
  squash: THREE.Group;
  body: THREE.Mesh;
  face: THREE.Mesh;
  faceCanvas: HTMLCanvasElement;
  faceTex: THREE.CanvasTexture;
  faceU: number;
  arms: { pivot: THREE.Group; nub: THREE.Mesh; side: number }[];
  tinted: THREE.Mesh[];
  legs: THREE.Mesh[];
}

/** Корпус зверька (общий для героя и малышей): масштаб S = ширина корпуса, м; dims — поправки пропорций. */
export function buildCritter(S: number, body: string, shade: string, dims: Partial<typeof CP> = {}, glow = 0.12): Parts {
  const P = { ...CP, ...dims };
  const bodyMat = blockMat(body, "critter:body", glow);
  const shadeMat = blockMat(shade, "critter:shade", glow * 0.66);
  const root = new THREE.Group();
  const lean = new THREE.Group();
  lean.position.y = P.legH * S;
  root.add(lean);
  // Ножки — кубики в ряд (как у пиксельного маскота), под корпусом; наклоняются вместе с ним.
  const legGeo = new RoundedBoxGeometry(P.legW * S, (P.legH + 0.04) * S, P.legD * S, 3, 0.022 * S);
  const legs: THREE.Mesh[] = [];
  for (const x of P.legX) {
    const l = new THREE.Mesh(legGeo, shadeMat);
    l.position.set(x * S, (-(P.legH + 0.04) / 2 + 0.03) * S, 0);
    lean.add(l);
    legs.push(l);
  }
  const turn = new THREE.Group();
  lean.add(turn);
  const squash = new THREE.Group();
  turn.add(squash);
  const bodyMesh = new THREE.Mesh(new RoundedBoxGeometry(P.bodyW * S, P.bodyH * S, P.bodyD * S, 5, P.radius * S), bodyMat);
  bodyMesh.position.y = (P.bodyH / 2) * S;
  squash.add(bodyMesh);
  // Ручки-обрубки: пивот у боковой грани, блок торчит наружу.
  const armGeo = new RoundedBoxGeometry(P.armW * S, P.armH * S, P.armD * S, 3, 0.03 * S);
  const arms: Parts["arms"] = [];
  for (const side of [1, -1]) {
    const pivot = new THREE.Group();
    pivot.position.set(side * (P.bodyW / 2 - 0.02) * S, P.armY * P.bodyH * S, 0.05 * S);
    pivot.rotation.order = "YZX";
    squash.add(pivot);
    const nub = new THREE.Mesh(armGeo, bodyMat);
    nub.position.x = side * (P.armW / 2) * S;
    pivot.add(nub);
    arms.push({ pivot, nub, side });
  }
  // Лицо — канвас на передней грани (глаза и значки; фон прозрачный, свет не влияет: чёрное остаётся чёрным).
  const FW = 512;
  const fw = P.bodyW - 2 * P.radius * 0.5, fh = P.bodyH;
  const FH = Math.round(FW * fh / fw);
  const cv = document.createElement("canvas");
  cv.width = FW;
  cv.height = FH;
  const tex = new THREE.CanvasTexture(cv);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 8;
  const face = new THREE.Mesh(new THREE.PlaneGeometry(fw * S, fh * S),
    new THREE.MeshBasicMaterial({ map: tex, transparent: true, toneMapped: false, depthWrite: false,
      polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2 }));
  face.position.set(0, (fh / 2) * S, (P.bodyD / 2 + 0.002) * S);
  face.userData.toon = false;
  face.userData.outline = false;
  face.renderOrder = 2;
  squash.add(face);
  root.traverse((o: any) => {
    if (o.isMesh) {
      o.castShadow = o !== face;
      o.receiveShadow = o !== face;
    }
  });
  return { root, lean, turn, squash, body: bodyMesh, face, faceCanvas: cv, faceTex: tex, faceU: FW / fw,
    arms, tinted: [bodyMesh, ...arms.map((a) => a.nub)], legs };
}

/** Поставить ручку по позе [поднять°, вперёд°, выдвинуть, покачивание]. */
export function setNub(a: Parts["arms"][number], n: Nub, S: number, t: number, extraRaise = 0) {
  const wig = n[3] > 0 ? Math.sin(t * 2 * Math.PI * 3.2 + (a.side > 0 ? 0 : 1.7)) * 14 * n[3] : 0;
  // Подъём — вокруг оси z (наружу-вверх), мах вперёд — вокруг вертикали (обрубок смотрит вбок).
  a.pivot.rotation.set(0, -a.side * (n[1]) * D2R, a.side * (n[0] + wig + extraRaise) * D2R);
  a.nub.position.x = a.side * (CP.armW / 2 + 0.06 * n[2]) * S;
}

export function createCritter(spec: CritterSpec): Character {
  const look = spec.critter || {};
  const S = spec.width ?? 0.3;
  const bodyC = look.body || "#d97757";
  const shadeC = look.shade || "#c15f3c";
  const parts = buildCritter(S, bodyC, shadeC, {}, look.glow ?? 0.12);
  const { root, lean, turn, squash, arms } = parts;
  root.name = `critter:${spec.id}`;
  const p = spec.position || [0, 0, 0];
  root.position.set(p[0], p[1], p[2]);
  const baseYaw = spec.yaw ?? 0;
  const hopG = new THREE.Group();
  root.remove(lean);
  root.add(hopG);
  hopG.add(lean);

  const accent = new THREE.MeshStandardMaterial({ color: new THREE.Color(look.headsetAccent || "#f0b45a"), roughness: 0.55,
    emissive: new THREE.Color(look.headsetAccent || "#f0b45a"), emissiveIntensity: 0.25, name: "critter:accent" });
  if (look.headset) {
    // Гарнитура поддержки: плоская П-дужка по крышке корпуса, чашки-кубики по бокам у верха, микрофон к углу.
    const hs = blockMat(look.headset, "critter:headset");
    const H = CP.bodyH, W = CP.bodyW;
    const z = -0.1;
    // Дужка: тонкая полоса поперёк крышки ближе к затылку + стойки по бокам до чашек.
    const bandTop = new THREE.Mesh(new RoundedBoxGeometry((W + 0.09) * S, 0.035 * S, 0.05 * S, 2, 0.014 * S), hs);
    bandTop.position.set(0, (H + 0.018) * S, z * S);
    squash.add(bandTop);
    for (const sx of [-1, 1]) {
      const post = new THREE.Mesh(new RoundedBoxGeometry(0.035 * S, 0.2 * S, 0.05 * S, 2, 0.014 * S), hs);
      post.position.set(sx * (W / 2 + 0.025) * S, (H - 0.07) * S, z * S);
      squash.add(post);
      const cup = new THREE.Mesh(new RoundedBoxGeometry(0.07 * S, 0.19 * S, 0.2 * S, 3, 0.03 * S), hs);
      cup.position.set(sx * (W / 2 + 0.04) * S, (H * 0.7) * S, z * S);
      squash.add(cup);
      const pad = new THREE.Mesh(new RoundedBoxGeometry(0.03 * S, 0.11 * S, 0.12 * S, 2, 0.012 * S), accent);
      pad.position.set(sx * (W / 2 + 0.085) * S, (H * 0.7) * S, z * S);
      squash.add(pad);
    }
    // Микрофон: штанга от правой чашки (справа на экране — левая у зверька? берём сторону +x) к нижнему углу морды.
    const sx = 1;
    const curve = new THREE.CatmullRomCurve3([
      new THREE.Vector3(sx * (W / 2 + 0.07) * S, (H * 0.64) * S, (z + 0.1) * S),
      new THREE.Vector3(sx * (W / 2 + 0.07) * S, (H * 0.42) * S, (CP.bodyD / 2 - 0.02) * S),
      new THREE.Vector3(sx * (W / 2 - 0.04) * S, (H * 0.3) * S, (CP.bodyD / 2 + 0.06) * S),
      new THREE.Vector3(sx * (W / 2 - 0.13) * S, (H * 0.28) * S, (CP.bodyD / 2 + 0.065) * S),
    ]);
    const boom = new THREE.Mesh(new THREE.TubeGeometry(curve, 24, 0.012 * S, 8, false), hs);
    squash.add(boom);
    const mic = new THREE.Mesh(new RoundedBoxGeometry(0.075 * S, 0.05 * S, 0.05 * S, 2, 0.018 * S), hs);
    mic.position.set(sx * (W / 2 - 0.15) * S, (H * 0.28) * S, (CP.bodyD / 2 + 0.065) * S);
    squash.add(mic);
    squash.traverse((o: any) => {
      if (o.isMesh && o !== parts.face && !o.castShadow && o.userData.toon !== false) {
        o.castShadow = true;
        o.receiveShadow = true;
      }
    });
  }

  if (look.label) {
    // Бирка с именем: маленькая кремовая плашка в нижнем углу морды (напротив микрофона), надпись — канвасом.
    const bw = 0.22, bh = 0.075;
    const back = new THREE.Mesh(new RoundedBoxGeometry(bw * S, bh * S, 0.02 * S, 2, 0.014 * S), blockMat("#f6ead8", "critter:tag", 0.1));
    back.position.set(-0.27 * S, 0.13 * S, (CP.bodyD / 2 + 0.008) * S);
    back.rotation.z = 0.06;
    back.castShadow = true;
    squash.add(back);
    const c2 = document.createElement("canvas");
    c2.width = 512;
    c2.height = Math.round(512 * bh / bw);
    const g2 = c2.getContext("2d")!;
    g2.fillStyle = "#f6ead8";
    g2.fillRect(0, 0, c2.width, c2.height);
    g2.fillStyle = "#3a2420";
    g2.font = `800 ${Math.round(c2.height * 0.66)}px Montserrat, Arial, sans-serif`;
    g2.textAlign = "center";
    g2.textBaseline = "middle";
    g2.fillText(look.label, c2.width / 2, c2.height * 0.55);
    const t2 = new THREE.CanvasTexture(c2);
    t2.colorSpace = THREE.SRGBColorSpace;
    t2.anisotropy = 8;
    const lab = new THREE.Mesh(new THREE.PlaneGeometry((bw - 0.025) * S, (bh - 0.02) * S), new THREE.MeshBasicMaterial({ map: t2, toneMapped: false }));
    lab.position.z = 0.0105 * S;
    lab.userData.toon = false;
    lab.userData.outline = false;
    back.add(lab);
  }

  if (look.badge) addBadge(squash, S, look.badge);

  const poseKeys = spec.pose || [];
  const faceKeys = (spec.face || []).slice().sort((a, b) => a.t - b.t);
  const stepFps = spec.stepFps ?? 0;
  const q = (t: number) => (stepFps > 0 ? Math.floor(t * stepFps + 1e-6) / stepFps : t);
  const blinks: number[] = [];
  {
    const r = rng(spec.seed ?? 7);
    if (spec.autoBlink !== false) for (let t = 0.9 + r() * 1.5; t < 600; t += 2.4 + r() * 2.2) blinks.push(t);
    for (const b of spec.blinks || []) blinks.push(b);
    blinks.sort((a, b) => a - b);
  }
  const num = (t: number, f: string, d: number) => sampleNumber(poseKeys, t, f) ?? d;
  const nub = (t: number, f: string): Nub => sampleKeys<Nub>(poseKeys.map((k) => (k[f] !== undefined ? { ...k, [f]: nubOf(k[f]) } : k)), t, f,
    (a, b, k) => a.map((v, i) => v + (b[i] - v) * k)) ?? NUB_POSES.rest;

  function faceParams(t: number): CritterFace {
    if (!faceKeys.length) return faceAt("neutral");
    let i = -1;
    for (let n = 0; n < faceKeys.length; n++) if (faceKeys[n].t <= t + 1e-9) i = n;
    if (i < 0) return faceAt(faceKeys[0].expr);
    const cur = faceAt(faceKeys[i].expr);
    if (i === 0) return cur;
    const blend = Number(faceKeys[i].blend ?? 0.12);
    const k = blend <= 0 ? 1 : ease("outCubic", (t - faceKeys[i].t) / blend);
    if (k >= 1) return cur;
    return mixFace(faceParams(faceKeys[i].t - 1e-6), cur, k);
  }
  const lookAt = (t: number): number[] => sampleKeys<number[]>(faceKeys.filter((k) => k.look).map((k) => ({ ...k, ease: k.ease || "outCubic" })), t, "look",
    (a, b, k) => [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k]) ?? [0, 0];

  const vspec = spec.voice || spec.mouth;
  function voiceAt(t: number): number {
    const m = vspec;
    if (!m?.values?.length) return 0;
    const fps = m.fps ?? 30;
    const x = (t - (m.start ?? 0)) * fps;
    const i = Math.floor(x);
    if (i < 0 || i >= m.values.length - 1) return 0;
    const v = m.values[i] + (m.values[i + 1] - m.values[i]) * (x - i);
    return Math.max(0, Math.min(1, v * (m.gain ?? 1)));
  }
  const bounceK = vspec?.bounce ?? 1;
  /** Ударность: насколько громкость выросла за последние 0.1 с (атака слога). */
  const punchAt = (t: number) => Math.max(0, Math.min(1, (voiceAt(t) - Math.min(voiceAt(t - 0.08), voiceAt(t - 0.16))) * 1.6));

  function blinkAt(t: number): number {
    for (const b of blinks) {
      if (b > t + 0.2) break;
      const d = t - b;
      if (d >= 0 && d < 0.14) return Math.sin((d / 0.14) * Math.PI);
    }
    return 0;
  }

  const g = parts.faceCanvas.getContext("2d")!;
  let lastSig = "";
  function drawFace(t: number, talk: number) {
    const f = faceParams(t);
    const lk = lookAt(t);
    const bl = blinkAt(t) * (1 - f.closed) * (1 - f.happy) * (1 - f.squint);
    const sig = [Math.round(t * 30), ...Object.values(f).map((v) => Math.round(v * 100)), Math.round(lk[0] * 100), Math.round(lk[1] * 100), Math.round(talk * 30), Math.round(bl * 20)].join(",");
    if (sig === lastSig) return f;
    lastSig = sig;
    paintCritterFace(g, parts.faceCanvas.width, parts.faceCanvas.height, parts.faceU, f, lk, bl, talk, t, look);
    parts.faceTex.needsUpdate = true;
    return f;
  }
  const baseCol = new THREE.Color(bodyC);
  const rageCol = new THREE.Color("#e2412c");
  const tmpC = new THREE.Color();

  const ch: Character = {
    id: spec.id,
    root,
    skeletonRoot: root,
    bones: {},
    height: (CP.legH + CP.bodyH) * S,
    head: { up: 0, r: S * 0.45 },
    yawAt: (t: number) => baseYaw + num(q(t), "turn", 0),
    boneWorld(name: string, out = new THREE.Vector3()) {
      root.updateMatrixWorld(true);
      if (name === "hand_l") return arms[0].nub.getWorldPosition(out);
      if (name === "hand_r") return arms[1].nub.getWorldPosition(out);
      if (name === "Head" || name === "head") return parts.face.getWorldPosition(out);
      return parts.body.getWorldPosition(out);
    },
    update(t0: number) {
      const t = q(t0);
      root.rotation.y = baseYaw * D2R;
      // Голос: плавная огибающая (по шагу кадра — как поза) и «атака» слога.
      const v = voiceAt(t) * bounceK;
      const punch = punchAt(t) * bounceK;
      const defl = num(t, "deflate", 0);
      hopG.position.y = Math.max(0, num(t, "hop", 0)) * (S / 0.3) + punch * 0.022 * S;
      lean.rotation.set((num(t, "lean", 0) + v * 2.5) * D2R, 0, -num(t, "tilt", 0) * D2R);
      turn.rotation.y = num(t, "turn", 0) * D2R;
      // Сжатие-растяжение с сохранением объёма: поза × дыхание × речь (тянется на ударных) × вздох (оседает).
      const breathe = 1 + 0.014 * Math.sin(t * 2 * Math.PI * 0.5);
      const sq = num(t, "squash", 1) * breathe * (1 + 0.05 * v + 0.07 * punch) * (1 - 0.16 * defl);
      const side = 1 / Math.sqrt(Math.max(0.3, sq));
      const shrink = 1 - 0.04 * defl;
      squash.scale.set(side * shrink, sq, side * shrink);
      const sh = num(t, "shake", 0);
      if (sh > 0) {
        const r = rng(9000 + Math.floor(t0 * 24));
        squash.position.set((r() - 0.5) * 0.04 * S * sh, 0, (r() - 0.5) * 0.02 * S * sh);
      } else squash.position.set(0, 0, 0);
      const ty = num(t, "type", 0);
      for (const a of arms) {
        const n = nub(t, a.side > 0 ? "armL" : "armR").slice();
        // Речь: ручки чуть «жестикулируют» на ударных (подскакивают вверх), вздох — повисают.
        let raise = punch * 16 - defl * 30;
        if (ty > 0) {
          const ph = t0 * 2 * Math.PI * 6.5 + (a.side > 0 ? 0 : 2.2);
          raise += ty * 14 * Math.max(0, Math.sin(ph));
        }
        setNub(a, n, S, t0, raise);
      }
      const f = drawFace(t, v);
      if (f.tint > 0.01) {
        const k = Math.min(1, f.tint) * (0.75 + 0.25 * Math.sin(t0 * 12));
        tmpC.copy(baseCol).lerp(rageCol, k * 0.75);
      } else tmpC.copy(baseCol);
      for (const m of parts.tinted) (m.material as any).color?.copy(tmpC);
    },
  };
  ch.update(0);
  return ch;
}

/**
 * Бейдж на груди: кремовая карточка на клипсе по центру низа морды (между биркой и микрофоном): слева подпись
 * («аккаунт»), справа номер в тёмном квадрате — читается и на общем плане.
 */
function addBadge(squash: THREE.Group, S: number, b: NonNullable<CritterLook["badge"]>) {
  const bw = 0.27, bh = 0.11;
  const card = new THREE.Mesh(new RoundedBoxGeometry(bw * S, bh * S, 0.018 * S, 2, 0.012 * S), blockMat("#fbf3e6", "critter:badge", 0.1));
  card.position.set(-0.005 * S, 0.1 * S, (CP.bodyD / 2 + 0.01) * S);
  card.rotation.z = -0.03;
  card.castShadow = true;
  squash.add(card);
  const clip = new THREE.Mesh(new RoundedBoxGeometry(0.07 * S, 0.035 * S, 0.026 * S, 2, 0.008 * S), blockMat("#8a8f99", "critter:clip"));
  clip.position.set(0, (bh / 2 + 0.005) * S, 0.004 * S);
  card.add(clip);
  const cv = document.createElement("canvas");
  cv.width = 640;
  cv.height = Math.round(640 * bh / bw);
  const g = cv.getContext("2d")!;
  const H = cv.height, W = cv.width;
  g.fillStyle = "#fbf3e6";
  g.fillRect(0, 0, W, H);
  // Цветная полоса сверху, как у бейджа конференции.
  g.fillStyle = b.accent || "#d97757";
  g.fillRect(0, 0, W, H * 0.16);
  const sq = H * 0.7;
  g.fillStyle = "#2b1c20";
  g.beginPath();
  g.roundRect(W - sq - H * 0.12, H * 0.22, sq, sq, H * 0.08);
  g.fill();
  g.fillStyle = "#fff6ea";
  g.textAlign = "center";
  g.textBaseline = "middle";
  const num = String(b.number ?? "1");
  g.font = `900 ${Math.round(sq * (num.length > 1 ? 0.62 : 0.8))}px Montserrat, Arial, sans-serif`;
  g.fillText(num, W - sq / 2 - H * 0.12, H * 0.22 + sq * 0.54);
  g.fillStyle = "#2b1c20";
  g.textAlign = "left";
  g.font = `800 ${Math.round(H * 0.33)}px Montserrat, Arial, sans-serif`;
  g.fillText(b.text ?? "аккаунт", H * 0.14, H * 0.6);
  const tex = new THREE.CanvasTexture(cv);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 8;
  const face = new THREE.Mesh(new THREE.PlaneGeometry((bw - 0.02) * S, (bh - 0.016) * S), new THREE.MeshBasicMaterial({ map: tex, toneMapped: false }));
  face.position.z = 0.0095 * S;
  face.userData.toon = false;
  face.userData.outline = false;
  card.add(face);
}

/* ---------------------------------------------------------------- малыши для вставок */

function chip(label: string, dot: string, R: number, scale: number): THREE.Sprite {
  const cv = document.createElement("canvas");
  const fpx = 64;
  const g0 = cv.getContext("2d")!;
  g0.font = `800 ${fpx}px Montserrat, Arial, sans-serif`;
  const tw = g0.measureText(label).width;
  cv.width = Math.ceil(tw + fpx * 2.2);
  cv.height = Math.round(fpx * 1.7);
  const g = cv.getContext("2d")!;
  g.fillStyle = "#fffaf2";
  g.strokeStyle = "#2b1810";
  g.lineWidth = 7;
  g.beginPath();
  g.roundRect(4, 4, cv.width - 8, cv.height - 8, cv.height / 2 - 4);
  g.fill();
  g.stroke();
  g.fillStyle = dot;
  g.fillRect(fpx * 0.6, cv.height / 2 - fpx * 0.25, fpx * 0.5, fpx * 0.5);
  g.fillStyle = "#2b1810";
  g.font = `800 ${fpx}px Montserrat, Arial, sans-serif`;
  g.textBaseline = "middle";
  g.fillText(label, fpx * 1.45, cv.height / 2 + fpx * 0.05);
  const tex = new THREE.CanvasTexture(cv);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 8;
  const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, toneMapped: false, transparent: true, depthWrite: false }));
  const h = R * scale;
  sp.scale.set((h * cv.width) / cv.height, h, 1);
  sp.userData.toon = false;
  sp.userData.outline = false;
  sp.renderOrder = 5;
  sp.castShadow = false;
  return sp;
}

/** Тёмный тон цвета (для ножек малыша). */
function darker(c: string, k = 0.78): string {
  const col = new THREE.Color(c);
  const hsl = { h: 0, s: 0, l: 0 };
  col.getHSL(hsl);
  col.setHSL(hsl.h, Math.min(1, hsl.s * 1.05), hsl.l * k);
  return `#${col.getHexString()}`;
}

/**
 * Малыш-помощник той же «пиксельной» породы (``helper_bot`` c ``style: "voxel"``): кубик-корпус повыше героя,
 * глаза-блоки с бликом (или ^ ^ у ``mood: "hop"`` на взлёте), румянец-пиксели, на макушке ``top``: sprout (росток из
 * кубиков), propeller (планка-пропеллер), ears (ушки-кубики). Ярлык-чип ``label`` над головой. ``size`` — высота (м).
 */
export function critterHelper(p: Record<string, any>): Prop {
  const size = p.size ?? 0.13;
  const dims: typeof CP = { bodyW: 1, bodyH: 0.74, bodyD: 0.7, radius: 0.09, legH: 0.16, legW: 0.16, legD: 0.32, legX: [-0.24, 0.24],
    armW: 0.17, armH: 0.2, armD: 0.3, armY: 0.36, eyeX: 0.2, eyeY: 0.58, eyeW: 0.06, eyeH: 0.22 };
  const S = size / (dims.bodyH + dims.legH);
  const col = p.color || "#6fd3c1";
  const parts = buildCritter(S, col, darker(col), dims);
  const grp = new THREE.Group();
  grp.add(parts.root);
  const accentC = p.accent || "#ffc857";
  const acc = blockMat(accentC, "critter:helper-accent");
  const bodyMat = parts.body.material as THREE.Material;
  const topG = new THREE.Group();
  topG.position.y = dims.bodyH * S;
  parts.squash.add(topG);
  const box = (w: number, h: number, d: number, m: THREE.Material, x: number, y: number, z: number, rz = 0) => {
    const b = new THREE.Mesh(new RoundedBoxGeometry(w * S, h * S, d * S, 2, Math.min(w, h, d) * 0.25 * S), m);
    b.position.set(x * S, y * S, z * S);
    b.rotation.z = rz;
    b.castShadow = true;
    b.receiveShadow = true;
    topG.add(b);
    return b;
  };
  const kind = p.top || "sprout";
  let spin: THREE.Group | null = null;
  if (kind === "propeller") {
    box(0.06, 0.14, 0.06, blockMat("#3a2a2e", "critter:stem"), 0, 0.07, 0);
    spin = new THREE.Group();
    spin.position.y = 0.15 * S;
    topG.add(spin);
    const blade = new THREE.Mesh(new RoundedBoxGeometry(0.8 * S, 0.035 * S, 0.12 * S, 2, 0.012 * S), acc);
    blade.castShadow = true;
    spin.add(blade);
  } else if (kind === "ears") {
    for (const s of [-1, 1]) {
      box(0.2, 0.18, 0.14, bodyMat, s * 0.32, 0.07, -0.05);
      box(0.11, 0.1, 0.04, acc, s * 0.32, 0.07, 0.03);
    }
  } else {
    box(0.05, 0.14, 0.05, acc, 0, 0.07, 0);
    box(0.17, 0.05, 0.1, acc, -0.09, 0.15, 0, 0.35);
    box(0.17, 0.05, 0.1, acc, 0.09, 0.15, 0, -0.35);
  }
  let label: THREE.Sprite | null = null;
  if (p.label) {
    label = chip(String(p.label), col, size, p.chipScale ?? 0.38);
    label.position.y = size * 1.5;
    grp.add(label);
  }
  const look: CritterLook = { cheeks: "#ff8fa3" };
  const g = parts.faceCanvas.getContext("2d")!;
  const mood = p.mood || "hop";
  const ph = Number(p.phase ?? 0);
  const face0: CritterFace = { ...BASE, shine: 1, blush: 1 };
  const faceHappy: CritterFace = { ...face0, happy: 1, eyeY: 0.1 };
  let lastHappy = -1;
  const paint = (happy: boolean, t: number) => {
    const key = happy ? 1 : 0;
    if (key === lastHappy) return;
    lastHappy = key;
    paintCritterFace(g, parts.faceCanvas.width, parts.faceCanvas.height, parts.faceU, happy ? faceHappy : face0, [0, 0], 0, 0, t,
      look, dims);
    parts.faceTex.needsUpdate = true;
  };
  return {
    object: grp,
    update: (t0) => {
      const t = t0 + ph;
      let y = 0, sq = 1, tilt = 0, raise = 0, fwd = 0, happy = false;
      if (mood === "hop") {
        const w = (t * 2.1) % 1;
        y = Math.sin(w * Math.PI) * size * 0.35;
        sq = w < 0.12 ? 0.82 + w * 1.5 : 1.05 - 0.05 * Math.sin(w * Math.PI);
        raise = 30 + 50 * Math.sin(w * Math.PI);
        tilt = 0.08 * Math.sin(t * 4.2);
        happy = w > 0.25 && w < 0.8;
      } else if (mood === "wave") {
        y = Math.abs(Math.sin(t * 3)) * size * 0.06;
        tilt = 0.06 * Math.sin(t * 3);
        raise = 20;
        happy = true;
      } else {
        y = Math.abs(Math.sin(t * 6)) * size * 0.03;
        sq = 1 + 0.03 * Math.sin(t * 12);
        fwd = 55;
      }
      parts.root.position.y = y;
      parts.squash.scale.set(1 / Math.sqrt(sq), sq, 1 / Math.sqrt(sq));
      parts.lean.rotation.z = tilt;
      parts.arms.forEach((a, i) => {
        let r = raise;
        if (mood === "work") r = -5 + 18 * Math.max(0, Math.sin(t * 14 + i * 2.2));
        if (mood === "wave" && a.side < 0) r = 75 + 20 * Math.sin(t * 10);
        a.pivot.rotation.set(0, -a.side * fwd * D2R, a.side * r * D2R);
      });
      if (spin) spin.rotation.y = t * 14;
      else topG.rotation.z = 0.12 * Math.sin(t * 5);
      if (label) label.position.y = size * 1.5 + y * 0.4;
      paint(happy, t);
    },
  };
}
