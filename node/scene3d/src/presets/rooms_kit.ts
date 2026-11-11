/**
 * Набор мебели и природы для окружений (rooms_*.ts): чистые функции → список объектов сцены из примитивов
 * движка (props.ts). Каждая вещь строится в своих осях — начало в центре основания, «перёд» — +z, — и ставится
 * в мир через ``put(части, позиция, поворот)``. Части одной вещи — ``id.деталь``: ``hide: ["desk"]`` убирает
 * вещь целиком (buildRoomPreset режет id по точке). Свет ламп и экранов — поле ``light`` у детали: едет и
 * поворачивается вместе с ней и исчезает вместе с ней.
 */
import { r4, r4v, seeded, type V3 } from "./meta.ts";

export type O = Record<string, any>;
const D2R = Math.PI / 180;

/** Параметры у всех окружений (здесь, а не в rooms.ts — модули окружений и реестр не зависят друг от друга по кругу). */
export const ROOM_PARAMS = [
  { name: "colors", type: "string" as const, ru: "палитра: {ключ: \"#цвет\"} поверх палитры окружения" },
  { name: "hide", type: "string" as const, ru: "какие вещи убрать: список id (см. props окружения)" },
  { name: "seed", type: "number" as const, default: 1, ru: "вариант раскладки мелочей" },
];

/** Якорь окружения: точка, куда смотрит (heading, °), высота сиденья (seat, м). */
export const anchor = (pos: number[], heading?: number, seat?: number) =>
  ({ pos: r4v(pos) as V3, ...(heading !== undefined ? { heading } : {}), ...(seat !== undefined ? { seat } : {}) });

// ---------- примитивы ----------

/** Деталь: kind — примитив движка; x — параметры примитива, а rot/scale/light/state — поля объекта. */
export function mk(kind: string, id: string, pos: number[], size: number[] | null, color: string, x: Record<string, any> = {}): O {
  const { rot, scale, light, state, ...params } = x;
  const o: O = { id, primitive: kind, position: r4v(pos), params: { ...(size ? { size: size.map(r4) } : {}), color, ...params } };
  if (rot) o.rotation = rot.map(r4);
  if (scale !== undefined) o.scale = Array.isArray(scale) ? scale.map(r4) : r4(scale);
  if (light) o.light = light;
  if (state) o.state = state;
  return o;
}
export const box = (id: string, pos: number[], size: number[], color: string, x: Record<string, any> = {}) => mk("box", id, pos, size, color, x);
export const rbox = (id: string, pos: number[], size: number[], color: string, radius = 0.02, x: Record<string, any> = {}) =>
  mk("rounded_box", id, pos, size, color, { radius, ...x });
/** Цилиндр [диаметр, высота]; x.top — доля верхнего радиуса (конус-усечённый). */
export const cyl = (id: string, pos: number[], size: number[], color: string, x: Record<string, any> = {}) => mk("cylinder", id, pos, size, color, x);
export const sph = (id: string, pos: number[], d: number, color: string, x: Record<string, any> = {}) => mk("sphere", id, pos, [d], color, x);
export const cone = (id: string, pos: number[], size: number[], color: string, x: Record<string, any> = {}) => mk("cone", id, pos, size, color, x);
/** Вертикальная двусторонняя плоскость [ширина, высота], лицом к +z. */
export const plane = (id: string, pos: number[], size: number[], color: string, x: Record<string, any> = {}) => mk("plane", id, pos, size, color, x);
/** Светящаяся деталь: тёмная основа + свечение (экраны, неон, лампочки), без тени. */
export const glow = (kind: string, id: string, pos: number[], size: number[], color: string, k = 1.4, x: Record<string, any> = {}) =>
  mk(kind, id, pos, size, "#0a0a0c", { emissive: color, emissiveIntensity: k, noShadow: true, roughness: 0.4, ...x });

// ---------- повороты (Euler XYZ, как у three.js) ----------

type M3 = number[];
function eulerM(r: number[]): M3 {
  const [x, y, z] = [r[0] * D2R, r[1] * D2R, r[2] * D2R];
  const a = Math.cos(x), b = Math.sin(x), c = Math.cos(y), d = Math.sin(y), e = Math.cos(z), f = Math.sin(z);
  const ae = a * e, af = a * f, be = b * e, bf = b * f;
  return [c * e, -c * f, d, af + be * d, ae - bf * d, -b * c, bf - ae * d, be + af * d, a * c];
}
function mul(m: M3, n: M3): M3 {
  const o: number[] = [];
  for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) o.push(m[i * 3] * n[j] + m[i * 3 + 1] * n[3 + j] + m[i * 3 + 2] * n[6 + j]);
  return o;
}
function toEuler(m: M3): number[] {
  const y = Math.asin(Math.max(-1, Math.min(1, m[2])));
  const [x, z] = Math.abs(m[2]) < 0.9999999 ? [Math.atan2(-m[5], m[8]), Math.atan2(-m[1], m[0])] : [Math.atan2(m[7], m[4]), 0];
  return [x / D2R, y / D2R, z / D2R].map(r4);
}
/** Поворот «наклонить на tilt° от вертикали в сторону азимута az°» (стебли, лучи, брёвна шалашом). */
export const tiltRot = (tilt: number, az: number, spin = 0) => toEuler(mul(eulerM([0, az, 0]), eulerM([tilt, spin, 0])));
/** Точка v, повёрнутая Euler-поворотом rot (где окажется верх стебля длины L: rotVec(rot, [0, L, 0])). */
export function rotVec(rot: number[], v: number[]): number[] {
  const m = eulerM(rot);
  return [m[0] * v[0] + m[1] * v[1] + m[2] * v[2], m[3] * v[0] + m[4] * v[1] + m[5] * v[2], m[6] * v[0] + m[7] * v[1] + m[8] * v[2]];
}

/** Поставить вещь (части в её осях) в мир: позиция основания и поворот вокруг вертикали (° как heading). */
export function put(list: O[], pos: number[], yaw = 0): O[] {
  const c = Math.cos(yaw * D2R), s = Math.sin(yaw * D2R);
  const ry = eulerM([0, yaw, 0]);
  return list.map((o) => {
    const p = o.position || [0, 0, 0];
    const out: O = { ...o, position: r4v([pos[0] + c * p[0] + s * p[2], (pos[1] || 0) + p[1], pos[2] - s * p[0] + c * p[2]]) };
    if (yaw) {
      const r = o.rotation || [0, 0, 0];
      out.rotation = !r[0] && !r[2] ? [r[0], r4(r[1] + yaw), r[2]] : toEuler(mul(ry, eulerM(r)));
    }
    return out;
  });
}

/** Сдвинуть вещь целиком по высоте (на столешницу, полку). */
export const lift = (list: O[], y: number) => list.map((o) => ({ ...o, position: r4v([o.position[0], o.position[1] + y, o.position[2]]) }));

// ---------- мебель ----------

/** Стол: столешница со скруглением, ножки-рамы, царга сзади; drawers — тумба с ящиками слева. */
export function desk(id: string, o: { w?: number; d?: number; h?: number; top: string; leg: string; drawers?: string }): O[] {
  const w = o.w ?? 1.4, d = o.d ?? 0.7, h = o.h ?? 0.75, t = 0.035;
  const out: O[] = [rbox(`${id}.top`, [0, h - t, 0], [w, t, d], o.top, 0.01, { roughness: 0.55 })];
  for (const sx of [-1, 1]) {
    const x = sx * (w / 2 - 0.08);
    for (const sz of [-1, 1]) out.push(box(`${id}.leg${sx}${sz}`, [x, 0.03, sz * (d / 2 - 0.08)], [0.04, h - t - 0.03, 0.04], o.leg, { roughness: 0.35, metalness: 0.6 }));
    out.push(rbox(`${id}.foot${sx}`, [x, 0, 0], [0.06, 0.03, d - 0.08], o.leg, 0.01, { roughness: 0.35, metalness: 0.6 }));
    out.push(box(`${id}.rail${sx}`, [x, h - t - 0.05, 0], [0.03, 0.05, d - 0.16], o.leg, { roughness: 0.35, metalness: 0.6 }));
  }
  out.push(box(`${id}.back`, [0, h - t - 0.08, -(d / 2 - 0.08)], [w - 0.2, 0.08, 0.02], o.leg, { roughness: 0.35, metalness: 0.6 }));
  if (o.drawers) {
    const dw = 0.4, dh = h - t - 0.05, x = -(w / 2 - 0.34);
    out.push(rbox(`${id}.drawers`, [x, 0.02, 0], [dw, dh, d - 0.12], o.drawers, 0.01, { roughness: 0.6 }));
    for (let i = 0; i < 3; i++) {
      const fh = dh / 3 - 0.012, y = 0.02 + 0.006 + i * (dh / 3);
      out.push(rbox(`${id}.drawer${i}`, [x, y, (d - 0.12) / 2], [dw - 0.03, fh, 0.016], o.drawers, 0.006, { roughness: 0.5 }));
      out.push(rbox(`${id}.handle${i}`, [x, y + fh * 0.62, (d - 0.12) / 2 + 0.016], [0.12, 0.012, 0.014], o.leg, 0.005, { metalness: 0.7, roughness: 0.3 }));
    }
  }
  return out;
}

/** Кресло на колёсиках: крестовина, газлифт, сиденье, спинка, подлокотники; gaming — высокая спинка с «ушами». */
export function chair(id: string, o: { color: string; accent?: string; frame?: string; kind?: "office" | "gaming"; seat?: number }): O[] {
  const fr = o.frame || "#2a2b30", seatTop = o.seat ?? 0.47, g = o.kind === "gaming";
  const met = { roughness: 0.35, metalness: 0.6 };
  const out: O[] = [];
  for (let i = 0; i < 5; i++) {
    const a = i * 72 + 18, s = Math.sin(a * D2R), c = Math.cos(a * D2R);
    out.push(rbox(`${id}.spoke${i}`, [s * 0.15, 0.045, c * 0.15], [0.045, 0.035, 0.3], fr, 0.01, { rot: [0, a, 0], ...met }));
    out.push(sph(`${id}.wheel${i}`, [s * 0.29, 0, c * 0.29], 0.055, "#18181b", { roughness: 0.5 }));
  }
  out.push(cyl(`${id}.hub`, [0, 0.04, 0], [0.09, 0.06], fr, met));
  out.push(cyl(`${id}.lift`, [0, 0.09, 0], [0.05, seatTop - 0.17], "#9a9ca3", { roughness: 0.25, metalness: 0.9 }));
  out.push(rbox(`${id}.base`, [0, seatTop - 0.11, 0], [0.34, 0.03, 0.3], fr, 0.01, met));
  out.push(rbox(`${id}.seat`, [0, seatTop - 0.09, 0.02], [g ? 0.54 : 0.5, 0.09, 0.5], o.color, 0.04, { roughness: 0.75 }));
  if (g) {
    for (const sx of [-1, 1]) out.push(rbox(`${id}.bolster${sx}`, [sx * 0.24, seatTop - 0.06, 0.02], [0.08, 0.07, 0.48], o.color, 0.03, { roughness: 0.7 }));
    out.push(rbox(`${id}.back`, [0, seatTop - 0.04, -0.25], [0.52, 0.9, 0.11], o.color, 0.05, { rot: [-9, 0, 0], roughness: 0.75 }));
    for (const sx of [-1, 1]) out.push(rbox(`${id}.wing${sx}`, [sx * 0.24, seatTop + 0.18, -0.24], [0.08, 0.5, 0.13], o.color, 0.035, { rot: [-9, 0, -sx * 4], roughness: 0.7 }));
    if (o.accent) {
      for (const sx of [-1, 1]) out.push(box(`${id}.stripe${sx}`, [sx * 0.12, seatTop + 0.06, -0.185], [0.035, 0.62, 0.012], o.accent, { rot: [-9, 0, 0], roughness: 0.6 }));
      out.push(rbox(`${id}.pillow`, [0, seatTop + 0.66, -0.22], [0.26, 0.12, 0.08], o.accent, 0.04, { rot: [-9, 0, 0], roughness: 0.8 }));
    }
  } else {
    out.push(box(`${id}.spine`, [0, seatTop - 0.08, -0.22], [0.06, 0.25, 0.03], fr, { ...met, rot: [-8, 0, 0] }));
    out.push(rbox(`${id}.back`, [0, seatTop + 0.08, -0.26], [0.46, 0.5, 0.08], o.color, 0.04, { rot: [-10, 0, 0], roughness: 0.75 }));
    if (o.accent) out.push(rbox(`${id}.lumbar`, [0, seatTop + 0.12, -0.215], [0.32, 0.12, 0.03], o.accent, 0.015, { rot: [-10, 0, 0], roughness: 0.8 }));
  }
  for (const sx of [-1, 1]) {
    out.push(box(`${id}.armpost${sx}`, [sx * 0.27, seatTop - 0.1, -0.02], [0.03, 0.24, 0.04], fr, met));
    out.push(rbox(`${id}.arm${sx}`, [sx * 0.27, seatTop + 0.14, 0.0], [0.06, 0.03, 0.26], "#232327", 0.012, { roughness: 0.7 }));
  }
  return out;
}

/** Содержимое экрана без текста: code — строки кода, game — пейзаж игры, chart — графики, calm — обои. */
function screenArt(id: string, w: number, h: number, kind: string, accent: string, z: number, seed: number): O[] {
  const r = seeded(seed);
  const out: O[] = [];
  const bg = kind === "game" ? "#3a6fb8" : kind === "calm" ? "#2b3f6b" : "#121726";
  out.push(glow("plane", `${id}.bg`, [0, 0, z], [w, h], bg, 1.1));
  const k = 1.5;
  if (kind === "code") {
    out.push(glow("plane", `${id}.side`, [-w / 2 + w * 0.09, 0, z + 0.001], [w * 0.18, h], "#1b2134", 1.1));
    const cols = ["#7fd1ff", "#ff9bd2", "#ffd479", "#9dffb0", "#c6b5ff", "#e6e8f0"];
    const n = Math.max(6, Math.round(h / 0.022));
    for (let i = 0; i < n; i++) {
      const y = h - 0.03 - i * (h - 0.05) / n;
      let x = -w / 2 + w * 0.22 + (r() < 0.5 ? 0 : w * 0.04) + (r() < 0.25 ? w * 0.08 : 0);
      for (let j = 0, m = 1 + Math.floor(r() * 3); j < m && x < w * 0.42; j++) {
        const len = w * (0.05 + r() * 0.16);
        out.push(glow("plane", `${id}.ln${i}_${j}`, [x + len / 2, y - 0.004, z + 0.002], [len, 0.008], cols[Math.floor(r() * cols.length)], k));
        x += len + w * 0.02;
      }
    }
    for (let i = 0; i < 5; i++) out.push(glow("plane", `${id}.tree${i}`, [-w / 2 + w * 0.08, h - 0.04 - i * 0.025, z + 0.002], [w * 0.1, 0.007], "#5b6788", k));
  } else if (kind === "game") {
    out.push(glow("plane", `${id}.sky2`, [0, h * 0.45, z + 0.001], [w, h * 0.55], "#ff9a7a", 1.0));
    out.push(glow("plane", `${id}.sky3`, [0, h * 0.62, z + 0.0015], [w, h * 0.38], "#7a8fe0", 1.1));
    out.push(glow("sphere", `${id}.sun`, [w * 0.18, h * 0.42, z + 0.003], [h * 0.22], "#ffe9a8", 1.8, { scale: [1, 1, 0.05] }));
    for (let i = 0; i < 4; i++) {
      const mw = w * (0.35 + r() * 0.3), mh = h * (0.18 + r() * 0.16);
      out.push(glow("cone", `${id}.mt${i}`, [-w / 2 + r() * w, h * 0.3, z + 0.003], [mw, mh], i % 2 ? "#4b3f8a" : "#6a4f9a", 1.1, { scale: [1, 1, 0.02], segments: 4 }));
    }
    out.push(glow("plane", `${id}.ground`, [0, 0, z + 0.004], [w, h * 0.32], "#2f8a6a", 1.1));
    out.push(glow("plane", `${id}.hud`, [-w * 0.32, h * 0.86, z + 0.005], [w * 0.26, h * 0.04], "#ff5577", k));
    out.push(glow("plane", `${id}.hud2`, [-w * 0.32, h * 0.8, z + 0.005], [w * 0.2, h * 0.03], "#55d6ff", k));
    out.push(glow("capsule", `${id}.hero`, [-w * 0.05, h * 0.3, z + 0.006], [h * 0.06, h * 0.16], "#ffd166", 1.8, { scale: [1, 1, 0.05] }));
  } else if (kind === "chart") {
    for (let i = 0; i < 9; i++) {
      const bh = h * (0.15 + r() * 0.55);
      out.push(glow("plane", `${id}.bar${i}`, [-w * 0.4 + i * w * 0.075, h * 0.12, z + 0.002], [w * 0.05, bh], i % 3 === 0 ? accent : "#5fa8ff", k));
    }
    out.push(glow("plane", `${id}.panel`, [w * 0.33, h * 0.12, z + 0.002], [w * 0.26, h * 0.76], "#1f2840", 1.1));
    for (let i = 0; i < 6; i++) out.push(glow("plane", `${id}.row${i}`, [w * 0.33, h * 0.78 - i * h * 0.11, z + 0.003], [w * 0.2, h * 0.025], i === 0 ? accent : "#8fa3c8", k));
  } else {
    out.push(glow("plane", `${id}.band`, [0, h * 0.2, z + 0.001], [w, h * 0.35], "#7a5fb0", 1.1));
    out.push(glow("sphere", `${id}.moon`, [w * 0.25, h * 0.6, z], [h * 0.18], "#ffe2b8", 1.6, { scale: [1, 1, 0.05] }));
    for (let i = 0; i < 5; i++) out.push(glow("plane", `${id}.icon${i}`, [-w * 0.42, h * 0.82 - i * h * 0.13, z + 0.002], [h * 0.07, h * 0.07], accent, k));
  }
  return out;
}

/**
 * Монитор на ножке: подставка, стойка, корпус с рамкой, экран. content: ui — живой экран движка (ui_screen,
 * эфир с чатом), иначе — screenArt. Свет экрана — мягкий прожектор вперёд (light: false — без него).
 */
export function monitor(id: string, o: { w?: number; h?: number; lift?: number; body?: string; screen?: string; content?: string; seed?: number;
  light?: number | false; ui?: Record<string, any> }): O[] {
  const w = o.w ?? 0.6, h = o.h ?? 0.34, ly = o.lift ?? 0.12, body = o.body || "#1c1d22", sc = o.screen || "#7fb2ff";
  const out: O[] = [
    rbox(`${id}.foot`, [0, 0, -0.03], [0.24, 0.014, 0.17], body, 0.006, { roughness: 0.4, metalness: 0.4 }),
    box(`${id}.neck`, [0, 0.01, -0.07], [0.045, ly + h * 0.4, 0.025], body, { roughness: 0.4, metalness: 0.5 }),
    rbox(`${id}.panel`, [0, ly, -0.01], [w + 0.024, h + 0.024, 0.022], body, 0.008, { roughness: 0.45 }),
    rbox(`${id}.hump`, [0, ly + h * 0.2, -0.045], [w * 0.5, h * 0.55, 0.05], body, 0.015, { roughness: 0.5 }),
  ];
  const z = 0.0015;
  const scr = o.ui
    ? [{ id: `${id}.screen`, primitive: "ui_screen", position: r4v([0, ly + 0.012, z]), params: { size: [w, h], ...o.ui } }]
    : put(screenArt(`${id}.screen`, w, h, o.content || "code", sc, 0, o.seed ?? 3), [0, ly + 0.012, z]);
  out.push(...scr);
  if (o.light !== false) {
    out.push(glow("box", `${id}.glowlight`, [0, ly + h * 0.5, 0.003], [0.001, 0.001, 0.001], sc, 0.01, {
      light: { type: "spot", id: `${id}.light`, color: sc, intensity: o.light ?? 1.6, position: [0, 0, 0.08], target: [0, -0.25, 1.4], angle: 70, penumbra: 1, decay: 2, distance: 4 } }));
  }
  return out;
}

/** Клавиатура, мышь и коврик; rgb — цвет подсветки под клавиатурой. */
export function keyboardSet(id: string, o: { body?: string; keys?: string; mat?: string; rgb?: string; mouseX?: number }): O[] {
  const out: O[] = [
    rbox(`${id}.mat`, [0.08, 0, 0], [0.86, 0.004, 0.34], o.mat || "#2c2f3a", 0.002, { roughness: 0.95 }),
    rbox(`${id}.kb`, [0, 0.004, 0.0], [0.42, 0.022, 0.135], o.body || "#24262d", 0.006, { rot: [3, 0, 0], roughness: 0.5 }),
    rbox(`${id}.keys`, [0, 0.022, 0.003], [0.395, 0.013, 0.112], o.keys || "#e9e6df", 0.004, { rot: [3, 0, 0], roughness: 0.6 }),
    rbox(`${id}.space`, [0, 0.033, 0.042], [0.13, 0.006, 0.02], o.keys || "#e9e6df", 0.003, { rot: [3, 0, 0], roughness: 0.6 }),
    sph(`${id}.mouse`, [o.mouseX ?? 0.34, 0.004, 0.02], 0.068, o.body || "#24262d", { scale: [0.62, 0.48, 1], roughness: 0.4 }),
  ];
  if (o.rgb) out.push(glow("box", `${id}.rgb`, [0, 0.002, 0], [0.43, 0.004, 0.14], o.rgb, 2.2));
  return out;
}

/** Настольная лампа на шарнирах: свет вниз-вперёд (прожектор с тенью) и тёплое свечение плафона. */
export function deskLamp(id: string, o: { color: string; light?: string; power?: number; shadow?: boolean }): O[] {
  const lc = o.light || "#ffc98a";
  const r1 = [-18, 0, 0], r2 = [62, 0, 0];
  const j = rotVec(r1, [0, 0.3, 0]), top = rotVec(r2, [0, 0.27, 0]);
  const jp = [0, 0.02 + j[1], j[2]], hp = [0, jp[1] + top[1], jp[2] + top[2]];
  const met = { roughness: 0.35, metalness: 0.5 };
  return [
    cyl(`${id}.base`, [0, 0, 0], [0.13, 0.025], o.color, met),
    cyl(`${id}.arm1`, [0, 0.02, 0], [0.016, 0.3], o.color, { rot: r1, ...met }),
    sph(`${id}.joint`, [jp[0], jp[1] - 0.016, jp[2]], 0.032, "#2b2b2f", met),
    cyl(`${id}.arm2`, jp, [0.016, 0.27], o.color, { rot: r2, ...met }),
    cyl(`${id}.shade`, [hp[0], hp[1] - 0.1, hp[2] + 0.02], [0.16, 0.12], o.color, { top: 0.42, rot: [18, 0, 0], ...met }),
    glow("sphere", `${id}.bulb`, [hp[0], hp[1] - 0.115, hp[2] + 0.035], [0.055], lc, 3.2, {
      light: { type: "spot", id: `${id}.light`, color: lc, intensity: o.power ?? 2.4, position: [0, 0.02, 0], target: [0, -0.6, 0.25], angle: 58, penumbra: 0.85, decay: 2,
        distance: 4, castShadow: o.shadow !== false, shadow: { size: 1024, radius: 4 }, halo: { size: 0.16, opacity: 0.55 } } }),
  ];
}

/** Торшер: тканевый абажур светится изнутри, тёплый точечный свет. */
export function floorLamp(id: string, o: { shade: string; pole?: string; light?: string; power?: number; h?: number }): O[] {
  const h = o.h ?? 1.55, pole = o.pole || "#2d2a28", lc = o.light || "#ffbd7a";
  return [
    cyl(`${id}.base`, [0, 0, 0], [0.3, 0.03], pole, { roughness: 0.4, metalness: 0.5 }),
    cyl(`${id}.pole`, [0, 0.03, 0], [0.025, h - 0.15], pole, { roughness: 0.4, metalness: 0.5 }),
    cyl(`${id}.shade`, [0, h - 0.2, 0], [0.42, 0.3], o.shade, { top: 0.72, roughness: 0.9, emissive: lc, emissiveIntensity: 0.55, segments: 40 }),
    glow("sphere", `${id}.bulb`, [0, h - 0.13, 0], [0.07], lc, 3, {
      light: { type: "point", id: `${id}.light`, color: lc, intensity: o.power ?? 2.2, position: [0, 0, 0], distance: 5, decay: 2 } }),
  ];
}

const BOOKS = ["#b5544a", "#d9a441", "#4f6d8f", "#7e9a6e", "#e8dcc4", "#8a5a7b", "#c97b5a", "#2f4058", "#a7b8c9", "#5e4b3c", "#d48a8a", "#3f6b5c"];

/** Ряд книг от x0 до x1 на высоте y: разные корешки, стопка лёжа, книга-«падалка» в конце, промежутки. */
export function books(id: string, x0: number, x1: number, y: number, z: number, o: { seed: number; maxH?: number; depth?: number; colors?: string[] }): O[] {
  const r = seeded(o.seed), cols = o.colors || BOOKS, mh = o.maxH ?? 0.24, dep = o.depth ?? 0.17;
  const out: O[] = [];
  let x = x0, i = 0;
  while (x < x1 - 0.03) {
    const c = cols[Math.floor(r() * cols.length)];
    if (r() < 0.1 && x1 - x > 0.22) {
      // Стопка лёжа.
      let yy = y;
      const n = 2 + Math.floor(r() * 2);
      for (let k = 0; k < n; k++) {
        const bh = 0.025 + r() * 0.02, bw = 0.15 + r() * 0.05;
        out.push(rbox(`${id}.${i++}`, [x + 0.1, yy, z + (r() - 0.5) * 0.02], [bw, bh, dep * (0.85 + r() * 0.15)], cols[Math.floor(r() * cols.length)], 0.004, { rot: [0, (r() - 0.5) * 10, 0], roughness: 0.8 }));
        yy += bh;
      }
      x += 0.22;
      continue;
    }
    const bw = 0.02 + r() * 0.028, bh = mh * (0.7 + r() * 0.3);
    if (r() < 0.08 && x1 - x < 0.2) {
      out.push(rbox(`${id}.${i++}`, [x + bh * 0.2, y, z], [bw, bh, dep], c, 0.004, { rot: [0, 0, -16], roughness: 0.8 }));
      break;
    }
    out.push(rbox(`${id}.${i++}`, [x + bw / 2, y, z + (r() - 0.5) * 0.015], [bw, bh, dep * (0.85 + r() * 0.15)], c, 0.004, { roughness: 0.8 }));
    if (r() < 0.4) out.push(box(`${id}.${i++}`, [x + bw / 2, y + bh * (0.72 + r() * 0.12), z], [bw + 0.002, 0.012, dep * 0.86], r() < 0.5 ? "#e9d9a8" : "#f2ede2", { roughness: 0.6 }));
    x += bw + (r() < 0.12 ? 0.02 + r() * 0.04 : 0.002);
  }
  return out;
}

/** Настенная полка с кронштейнами (верх полки — y = 0.025; вещи ставить с lift). */
export function shelf(id: string, w: number, o: { color: string; bracket?: string; depth?: number }): O[] {
  const dep = o.depth ?? 0.22;
  return [
    rbox(`${id}.board`, [0, 0, 0], [w, 0.025, dep], o.color, 0.006, { roughness: 0.6 }),
    ...[-1, 1].map((s) => box(`${id}.br${s}`, [s * (w / 2 - 0.1), -0.12, -dep / 2 + 0.015], [0.02, 0.12, 0.03], o.bracket || "#2a2827", { roughness: 0.4, metalness: 0.5 })),
    ...[-1, 1].map((s) => box(`${id}.brl${s}`, [s * (w / 2 - 0.1), -0.012, -dep / 2 + 0.1], [0.02, 0.012, 0.17], o.bracket || "#2a2827", { roughness: 0.4, metalness: 0.5 })),
  ];
}

/** Книжный стеллаж: короб, полки, книги и мелочи по засеянному ГПСЧ. */
export function bookcase(id: string, o: { w?: number; h?: number; d?: number; color: string; seed: number; shelves?: number; decor?: string[] }): O[] {
  const w = o.w ?? 0.9, h = o.h ?? 1.8, d = o.d ?? 0.32, n = o.shelves ?? 5, t = 0.025;
  const r = seeded(o.seed + 7);
  const out: O[] = [
    box(`${id}.back`, [0, 0, -d / 2 + 0.006], [w, h, 0.012], o.color, { roughness: 0.8 }),
    ...[-1, 1].map((s) => rbox(`${id}.side${s}`, [s * (w / 2 - t / 2), 0, 0], [t, h, d], o.color, 0.005, { roughness: 0.65 })),
    rbox(`${id}.top`, [0, h - t, 0], [w, t, d], o.color, 0.005, { roughness: 0.65 }),
    box(`${id}.kick`, [0, 0, d / 2 - 0.02], [w - 2 * t, 0.07, 0.02], o.color, { roughness: 0.65 }),
  ];
  const step = (h - 0.07 - t) / n;
  const decor = o.decor || ["#e4d6c0", "#9fb7a0", "#d98b6f"];
  for (let i = 0; i < n; i++) {
    const y = 0.07 + i * step;
    out.push(box(`${id}.sh${i}`, [0, y - t, 0], [w - 2 * t, t, d - 0.02], o.color, { roughness: 0.65 }));
    const x0 = -w / 2 + t + 0.01, x1 = w / 2 - t - 0.01;
    const split = r();
    if (split < 0.35) {
      // Полка: книги слева, справа — ваза/коробка/рамка.
      out.push(...books(`${id}.b${i}`, x0, x0 + (x1 - x0) * 0.6, y, 0, { seed: o.seed * 13 + i, maxH: Math.min(0.28, step - 0.06) }));
      const cx = x0 + (x1 - x0) * 0.8, kind = Math.floor(r() * 3);
      if (kind === 0) out.push(cyl(`${id}.vase${i}`, [cx, y, 0], [0.09, 0.16], decor[i % decor.length], { top: 0.6, roughness: 0.4, segments: 20 }));
      else if (kind === 1) out.push(rbox(`${id}.boxd${i}`, [cx, y, 0], [0.16, 0.11, 0.16], decor[(i + 1) % decor.length], 0.01, { roughness: 0.85 }));
      else out.push({ id: `${id}.frame${i}`, primitive: "poster", position: r4v([cx, y, -0.02]), rotation: [-8, 0, 0], params: { width: 0.12, height: 0.15, seed: o.seed + i, frame: "#efe8dc" } });
    } else out.push(...books(`${id}.b${i}`, x0, x1, y, 0, { seed: o.seed * 13 + i, maxH: Math.min(0.28, step - 0.06) }));
  }
  return out;
}

// ---------- растения ----------

/**
 * Растение в горшке: snake (сансевиерия — острые листья-клинки), bush (лоу-поли куст), rosette (розетка
 * крупных листьев — монстера/фикус), cactus. Листья — сплюснутые многогранники двух оттенков.
 */
export function plant(id: string, o: { kind?: string; pot?: string; leaf?: string; leaf2?: string; size?: number; seed?: number; potH?: number }): O[] {
  const s = o.size ?? 1, r = seeded(o.seed ?? 5), kind = o.kind || "rosette";
  const leaf = o.leaf || "#4f8a5b", leaf2 = o.leaf2 || "#6fae6a";
  const pd = 0.18 * s, ph = (o.potH ?? 0.17) * s;
  const out: O[] = [
    cyl(`${id}.pot`, [0, 0, 0], [pd, ph], o.pot || "#d9cbb8", { top: 1.18, roughness: 0.55, segments: 28 }),
    cyl(`${id}.rim`, [0, ph - 0.02 * s, 0], [pd * 1.22, 0.03 * s], o.pot || "#d9cbb8", { roughness: 0.5, segments: 28 }),
    cyl(`${id}.soil`, [0, ph - 0.005, 0], [pd * 1.08, 0.012], "#3b2a20", { roughness: 1, segments: 20 }),
  ];
  const base = [0, ph, 0];
  if (kind === "snake") {
    for (let i = 0; i < 9; i++) {
      const rot = tiltRot(4 + r() * 14, i * 40 + r() * 20, r() * 90);
      out.push(cone(`${id}.l${i}`, [(r() - 0.5) * pd * 0.5, ph - 0.01, (r() - 0.5) * pd * 0.5], [0.06 * s, (0.32 + r() * 0.28) * s], i % 2 ? leaf : leaf2,
        { rot, scale: [1, 1, 0.3], segments: 6, flat: true, roughness: 0.6 }));
    }
  } else if (kind === "bush") {
    for (let i = 0; i < 11; i++) {
      const a = r() * Math.PI * 2, rr = r() * pd * 0.55, y = ph + 0.02 + r() * 0.16 * s;
      out.push(sph(`${id}.l${i}`, [Math.cos(a) * rr, y - 0.04 * s, Math.sin(a) * rr], (0.1 + r() * 0.08) * s, i % 3 ? leaf : leaf2, { segments: 7, flat: true, roughness: 0.75, rot: [r() * 40, r() * 90, 0] }));
    }
  } else if (kind === "cactus") {
    out.push(mk("capsule", `${id}.c0`, [0, ph - 0.01, 0], [0.09 * s, 0.32 * s], leaf, { segments: 10, flat: true, roughness: 0.7 }));
    out.push(mk("capsule", `${id}.c1`, [0.04 * s, ph + 0.1 * s, 0], [0.05 * s, 0.14 * s], leaf2, { rot: [0, 0, -38], segments: 8, flat: true, roughness: 0.7 }));
    out.push(sph(`${id}.flower`, [0, ph + 0.3 * s, 0], 0.035 * s, "#ff7aa8", { roughness: 0.6 }));
  } else {
    // Розетка: листья-эллипсоиды на стеблях расходятся вверх-наружу.
    const n = 9;
    for (let i = 0; i < n; i++) {
      const az = (i * 360) / n + r() * 25, tilt = 20 + r() * 40, L = (0.16 + r() * 0.2) * s;
      const rot = tiltRot(tilt, az);
      out.push(cyl(`${id}.st${i}`, base, [0.01 * s, L], "#5d7b46", { rot, segments: 5 }));
      const tip = rotVec(rot, [0, L, 0]);
      const el = 50 - tilt * 0.6 + r() * 15;
      const lr = tiltRot(90 - el, az);
      const dir = rotVec(lr, [0, 1, 0]);
      const len = (0.17 + r() * 0.07) * s;
      out.push(sph(`${id}.l${i}`, [base[0] + tip[0] - dir[0] * len * 0.05, base[1] + tip[1] - dir[1] * len * 0.05, base[2] + tip[2] - dir[2] * len * 0.05], len, i % 2 ? leaf : leaf2,
        { rot: lr, scale: [0.62, 1, 0.1], segments: 8, flat: true, roughness: 0.6 }));
    }
  }
  return out;
}

/** Свисающий плющ с полки: цепочки мелких листьев вниз. */
export function ivy(id: string, o: { leaf?: string; leaf2?: string; len?: number; seed?: number; strands?: number }): O[] {
  const r = seeded(o.seed ?? 9), out: O[] = [];
  for (let s = 0; s < (o.strands ?? 3); s++) {
    let x = (s - 1) * 0.06, z = 0, y = 0;
    const n = 6 + Math.floor(r() * 5);
    for (let i = 0; i < n; i++) {
      x += (r() - 0.5) * 0.04;
      z += 0.006;
      y -= (o.len ?? 0.5) / n;
      out.push(sph(`${id}.${s}_${i}`, [x, y, z], 0.045 + r() * 0.02, (i + s) % 2 ? o.leaf || "#4a8a52" : o.leaf2 || "#76b26a", { scale: [1, 0.5, 0.85], segments: 6, flat: true, rot: [r() * 60, r() * 180, 0] }));
    }
  }
  return out;
}

// ---------- декор ----------

/** Картина/постер в рамке (процедурная абстракция). */
export const poster = (id: string, pos: number[], o: { w: number; h: number; colors?: string[]; frame?: string; seed?: number; rot?: number[] }): O =>
  ({ id, primitive: "poster", position: r4v(pos), ...(o.rot ? { rotation: o.rot } : {}), params: { width: o.w, height: o.h, colors: o.colors, frame: o.frame || "#2d2622", seed: o.seed ?? 21 } });

/** Гирлянда по точкам (точки — в мировых осях, если вещь не ставить через put). */
export const fairy = (id: string, points: number[][], o: { color?: string; count?: number; sag?: number; seed?: number } = {}): O =>
  ({ id, primitive: "string_lights", position: [0, 0, 0], params: { points: points.map(r4v), color: o.color || "#ffc773", count: o.count ?? 24, sag: o.sag ?? 0.08, seed: o.seed ?? 4 } });

/** Провод (гирлянда без лампочек). */
export const cable = (id: string, points: number[][], sag = 0.05): O => fairy(id, points, { count: 0, sag });

/** Ковёр: основа, кайма и центральное поле; round — круглый. */
export function rug(id: string, o: { w: number; d: number; color: string; border: string; field?: string; round?: boolean }): O[] {
  if (o.round) return [
    cyl(`${id}.base`, [0, 0, 0], [o.w, 0.008], o.border, { roughness: 1, segments: 48 }),
    cyl(`${id}.field`, [0, 0.008, 0], [o.w - 0.16, 0.003], o.color, { roughness: 1, segments: 48 }),
    cyl(`${id}.mid`, [0, 0.011, 0], [o.w * 0.45, 0.002], o.field || o.border, { roughness: 1, segments: 40 }),
  ];
  return [
    rbox(`${id}.base`, [0, 0, 0], [o.w, 0.008, o.d], o.border, 0.003, { roughness: 1 }),
    rbox(`${id}.field`, [0, 0.008, 0], [o.w - 0.16, 0.003, o.d - 0.16], o.color, 0.001, { roughness: 1 }),
    ...(o.field ? [0, 1, 2].map((i) => box(`${id}.stripe${i}`, [0, 0.011, (i - 1) * o.d * 0.22], [o.w - 0.3, 0.002, 0.05], o.field!, { roughness: 1 })) : []),
  ];
}

/** Неоновая вывеска-фигура: ломаная из трубок-капсул (точки на плоскости стены, z = 0) и подложка. */
export function neon(id: string, pts: number[][], o: { color: string; k?: number; tube?: number; backing?: string; light?: number }): O[] {
  const out: O[] = [];
  const t = o.tube ?? 0.022;
  for (let i = 0; i < pts.length - 1; i++) {
    const [x0, y0] = pts[i], [x1, y1] = pts[i + 1];
    const len = Math.hypot(x1 - x0, y1 - y0);
    const ang = (Math.atan2(x1 - x0, y1 - y0) * 180) / Math.PI;
    out.push(glow("capsule", `${id}.t${i}`, [x0 - ((x1 - x0) / len) * t / 2, y0 - ((y1 - y0) / len) * t / 2, 0.03], [t, len + t], o.color, o.k ?? 3, { rot: [0, 0, -ang], segments: 10 }));
  }
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
  const cx = (Math.min(...xs) + Math.max(...xs)) / 2, cy = (Math.min(...ys) + Math.max(...ys)) / 2;
  if (o.backing) out.push(rbox(`${id}.plate`, [cx, Math.min(...ys) - 0.06, 0], [Math.max(...xs) - Math.min(...xs) + 0.14, Math.max(...ys) - Math.min(...ys) + 0.12, 0.015], o.backing, 0.006, { roughness: 0.2 }));
  if (o.light) out.push(glow("box", `${id}.lamp`, [cx, cy, 0.05], [0.001, 0.001, 0.001], o.color, 0.01, { light: { type: "point", id: `${id}.light`, color: o.color, intensity: o.light, position: [0, 0, 0.15], distance: 3.5, decay: 2 } }));
  return out;
}

/** Колонка-полочник: корпус и динамики (вперёд — +z). */
export function speaker(id: string, o: { color: string; cone?: string; h?: number }): O[] {
  const h = o.h ?? 0.22;
  return [
    rbox(`${id}.body`, [0, 0, 0], [0.13, h, 0.16], o.color, 0.012, { roughness: 0.6 }),
    cyl(`${id}.woofer`, [0, h * 0.32, 0.075], [0.09, 0.012], o.cone || "#1a1a1d", { rot: [90, 0, 0], roughness: 0.8 }),
    cyl(`${id}.tweeter`, [0, h * 0.75, 0.075], [0.035, 0.012], o.cone || "#1a1a1d", { rot: [90, 0, 0], roughness: 0.4 }),
  ];
}

/** Системный блок со стеклом и светящимися вентиляторами (стекло — сбоку, +x). */
export function pcTower(id: string, o: { color?: string; fan: string; fan2?: string; light?: number }): O[] {
  const out: O[] = [
    rbox(`${id}.case`, [0, 0.02, 0], [0.22, 0.46, 0.46], o.color || "#16171b", 0.012, { roughness: 0.4, metalness: 0.3 }),
    glow("plane", `${id}.glass`, [0.1105, 0.06, 0], [0.4, 0.38], "#1a1030", 1.2, { rot: [0, 90, 0] }),
  ];
  for (let i = 0; i < 3; i++) {
    out.push(glow("torus", `${id}.fan${i}`, [0, 0.07 + i * 0.13, 0.232], [0.1, 0.1], i % 2 && o.fan2 ? o.fan2 : o.fan, 3, { tube: 0.006, segments: 28 }));
  }
  out.push(glow("torus", `${id}.gpu`, [0.115, 0.17, 0.05], [0.08, 0.08], o.fan2 || o.fan, 2.5, { tube: 0.005, rot: [0, 90, 0] }));
  for (let i = 0; i < 4; i++) out.push(box(`${id}.foot${i}`, [(i % 2 ? 1 : -1) * 0.08, 0, (i < 2 ? 1 : -1) * 0.18], [0.03, 0.02, 0.04], "#0e0e10"));
  if (o.light) out.push(glow("box", `${id}.lamp`, [0.12, 0.25, 0], [0.001, 0.001, 0.001], o.fan, 0.01,
    { light: { type: "point", id: `${id}.light`, color: o.fan, intensity: o.light, position: [0.1, 0, 0], distance: 2.5, decay: 2 } }));
  return out;
}

// ---------- улица и природа ----------

/** Скамья: чугунные боковины, деревянные рейки сиденья и спинки (садятся лицом к +z, сиденье ~0.45). */
export function bench(id: string, o: { w?: number; wood: string; metal: string }): O[] {
  const w = o.w ?? 1.6, out: O[] = [];
  const met = { roughness: 0.4, metalness: 0.6 };
  for (const s of [-1, 1]) {
    const x = s * (w / 2 - 0.12);
    out.push(rbox(`${id}.legf${s}`, [x, 0, 0.17], [0.05, 0.43, 0.05], o.metal, 0.01, met));
    out.push(rbox(`${id}.legb${s}`, [x, 0, -0.17], [0.05, 0.43, 0.05], o.metal, 0.01, met));
    out.push(box(`${id}.arm${s}`, [x, 0.41, 0], [0.05, 0.04, 0.42], o.metal, met));
    out.push(box(`${id}.post${s}`, [x, 0.43, -0.2], [0.05, 0.42, 0.04], o.metal, { ...met, rot: [-12, 0, 0] }));
  }
  for (let i = 0; i < 4; i++) out.push(rbox(`${id}.slat${i}`, [0, 0.43, -0.15 + i * 0.1], [w, 0.03, 0.08], o.wood, 0.01, { roughness: 0.7 }));
  for (let i = 0; i < 3; i++) out.push(rbox(`${id}.back${i}`, [0, 0.55 + i * 0.12, -0.24 - i * 0.025], [w, 0.08, 0.025], o.wood, 0.008, { rot: [-12, 0, 0], roughness: 0.7 }));
  return out;
}

/** Фонарь: столб, кронштейн, плафон, светящееся стекло и тёплый прожектор вниз с ореолом. */
export function lampPost(id: string, o: { h?: number; metal: string; light: string; power?: number; beam?: boolean; shadow?: boolean }): O[] {
  const h = o.h ?? 3.4;
  return [
    cyl(`${id}.base`, [0, 0, 0], [0.22, 0.35], o.metal, { top: 0.6, roughness: 0.4, metalness: 0.6, segments: 16 }),
    cyl(`${id}.pole`, [0, 0.3, 0], [0.09, h - 0.3], o.metal, { top: 0.7, roughness: 0.4, metalness: 0.6, segments: 16 }),
    box(`${id}.arm`, [0, h - 0.05, 0.22], [0.05, 0.05, 0.5], o.metal, { roughness: 0.4, metalness: 0.6 }),
    cyl(`${id}.hood`, [0, h - 0.12, 0.45], [0.36, 0.12], o.metal, { top: 0.35, roughness: 0.4, metalness: 0.6, segments: 20 }),
    glow("cylinder", `${id}.lens`, [0, h - 0.14, 0.45], [0.3, 0.03], o.light, 3.2, {
      light: { type: "spot", id: `${id}.light`, color: o.light, intensity: o.power ?? 30, position: [0, -0.02, 0], target: [0, -h, 0.2], angle: 52, penumbra: 0.75, decay: 2, distance: 12,
        castShadow: !!o.shadow, shadow: { size: 1024, radius: 3 }, ...(o.beam ? { beam: { length: h * 0.95, opacity: 0.08 } } : {}), halo: { size: 0.5, opacity: 0.6 } } }),
  ];
}

/** Ель: ствол и ярусы конусов (лоу-поли, плоские грани). */
export function pine(id: string, o: { h?: number; color: string; color2?: string; trunk?: string; seed?: number }): O[] {
  const h = o.h ?? 4, r = seeded(o.seed ?? 3), out: O[] = [cyl(`${id}.trunk`, [0, 0, 0], [h * 0.06, h * 0.4], o.trunk || "#5a4030", { top: 0.6, segments: 7, flat: true, roughness: 0.9 })];
  const n = 4;
  for (let i = 0; i < n; i++) {
    const d = h * (0.56 - i * 0.11) * (0.92 + r() * 0.16);
    out.push(cone(`${id}.c${i}`, [0, h * (0.2 + i * 0.17), 0], [d, h * (0.36 - i * 0.03)], i % 2 ? o.color2 || o.color : o.color,
      { segments: 7, flat: true, roughness: 0.85, rot: [(r() - 0.5) * 6, r() * 90, (r() - 0.5) * 6] }));
  }
  return out;
}

/** Лиственное дерево: ствол с двумя ветками и крона из многогранников. */
export function roundTree(id: string, o: { h?: number; color: string; color2?: string; trunk?: string; seed?: number }): O[] {
  const h = o.h ?? 3.6, r = seeded(o.seed ?? 4), tr = o.trunk || "#6a4a36";
  const out: O[] = [cyl(`${id}.trunk`, [0, 0, 0], [h * 0.07, h * 0.62], tr, { top: 0.6, segments: 7, flat: true, roughness: 0.9 })];
  for (const s of [-1, 1]) out.push(cyl(`${id}.branch${s}`, [0, h * 0.4, 0], [h * 0.03, h * 0.28], tr, { top: 0.5, rot: [0, 0, s * 38], segments: 6, flat: true }));
  const n = 6;
  for (let i = 0; i < n; i++) {
    const a = (i / n) * Math.PI * 2 + r(), rr = h * (0.12 + r() * 0.12), d = h * (0.34 + r() * 0.16);
    out.push(sph(`${id}.leaf${i}`, [Math.cos(a) * rr, h * (0.5 + r() * 0.22), Math.sin(a) * rr], d, i % 2 ? o.color2 || o.color : o.color,
      { segments: 6, flat: true, roughness: 0.85, rot: [r() * 60, r() * 90, 0] }));
  }
  out.push(sph(`${id}.crown`, [0, h * 0.72, 0], h * 0.42, o.color, { segments: 6, flat: true, roughness: 0.85 }));
  return out;
}

/** Камень: сплюснутый многогранник, засеянный поворот. */
export const rock = (id: string, pos: number[], d: number, color: string, seed = 1): O => {
  const r = seeded(seed);
  return sph(id, [pos[0], pos[1] - d * 0.12, pos[2]], d, color, { segments: 5 + Math.floor(r() * 2), flat: true, roughness: 0.95, scale: [1, 0.55 + r() * 0.25, 0.75 + r() * 0.3], rot: [0, r() * 180, (r() - 0.5) * 16] });
};

/** Пучок травы: 4–5 тонких конусов веером. */
export function tuft(id: string, pos: number[], o: { color: string; color2?: string; h?: number; seed?: number }): O[] {
  const r = seeded(o.seed ?? 2), h = o.h ?? 0.22, out: O[] = [];
  for (let i = 0; i < 5; i++) out.push(cone(`${id}.${i}`, [pos[0] + (r() - 0.5) * 0.06, pos[1], pos[2] + (r() - 0.5) * 0.06], [0.035, h * (0.6 + r() * 0.5)], i % 2 ? o.color2 || o.color : o.color,
    { rot: tiltRot(8 + r() * 22, r() * 360), scale: [1, 1, 0.35], segments: 4, flat: true, roughness: 0.9, noShadow: true }));
  return out;
}

/** Бревно лёжа (вдоль x) с торцами светлее. */
export function log(id: string, o: { len?: number; d?: number; bark: string; cut: string }): O[] {
  const L = o.len ?? 1.4, d = o.d ?? 0.3;
  return [
    cyl(`${id}.bark`, [-L / 2, d / 2, 0], [d, L], o.bark, { rot: [0, 0, -90], segments: 9, flat: true, roughness: 0.95 }),
    cyl(`${id}.endl`, [-L / 2 - 0.005, d / 2, 0], [d * 0.86, 0.012], o.cut, { rot: [0, 0, -90], segments: 9, flat: true, roughness: 0.9 }),
    cyl(`${id}.endr`, [L / 2 - 0.007, d / 2, 0], [d * 0.86, 0.012], o.cut, { rot: [0, 0, -90], segments: 9, flat: true, roughness: 0.9 }),
  ];
}

/** Пень: срез с кольцами, корни. */
export function stump(id: string, o: { d?: number; h?: number; bark: string; cut: string; seed?: number }): O[] {
  const d = o.d ?? 0.45, h = o.h ?? 0.38, r = seeded(o.seed ?? 6), out: O[] = [
    cyl(`${id}.body`, [0, 0, 0], [d, h], o.bark, { top: 0.9, segments: 9, flat: true, roughness: 0.95 }),
    cyl(`${id}.cut`, [0, h, 0], [d * 0.84, 0.01], o.cut, { segments: 9, flat: true, roughness: 0.9 }),
    // Тор держится за низ кольца: лёжа его центр уходит на радиус вперёд — сдвигаем назад.
    mk("torus", `${id}.ring`, [0, h + 0.005, -d * 0.225], [d * 0.45, d * 0.45], "#a8875e", { tube: 0.006, rot: [90, 0, 0], segments: 18 }),
  ];
  for (let i = 0; i < 4; i++) {
    const a = i * 90 + r() * 40;
    out.push(cone(`${id}.root${i}`, [Math.sin(a * D2R) * d * 0.4, 0, Math.cos(a * D2R) * d * 0.4], [d * 0.3, h * 0.6], o.bark, { rot: tiltRot(62, a), segments: 5, flat: true, roughness: 0.95 }));
  }
  return out;
}

/**
 * Дом-силуэт вдали (фасад — к +z): короб-фасад с окнами-текстурой (primitive facade), карниз, надстройка,
 * огонёк на крыше. Размеры — в любом масштабе: k — во сколько раз дом меньше настоящего (0.1 — макет:
 * этаж 0.28, дом на 40 м даёт тот же вид, что настоящий на 400 м — дальний город без тумана и обрезки камерой).
 * style: windows — жилой (доля lit горящих окон), bands — офисная башня (ленты этажей).
 */
export function building(id: string, o: { w: number; d: number; h: number; color: string; seed: number; lit?: number; win?: string[]; k?: number;
  beacon?: boolean; style?: "windows" | "bands"; glow?: number }): O[] {
  const r = seeded(o.seed), k = o.k ?? 1;
  const out: O[] = [
    mk("facade", `${id}.body`, [0, 0, 0], [o.w, o.h, o.d], o.color, { roughness: 0.9, cols: Math.round(o.w / (1.6 * k)), rows: Math.round(o.h / (2.9 * k)),
      lit: o.lit ?? 0.3, seed: o.seed, bands: o.style === "bands", glow: o.glow ?? 1.6, ...(o.win ? { win: o.win } : {}) }),
    box(`${id}.cap`, [0, o.h, 0], [o.w + 0.3 * k, 0.4 * k, o.d + 0.3 * k], o.color, { roughness: 0.9 }),
  ];
  let roof = o.h + 0.4 * k;
  if (r() < 0.6) {
    const th = (1.5 + r() * 2.5) * k * 1.5;
    out.push(box(`${id}.top`, [(r() - 0.5) * o.w * 0.3, roof, 0], [o.w * (0.25 + r() * 0.25), th, o.d * 0.5], o.color, { roughness: 0.9 }));
    roof += th;
  }
  if (o.beacon) out.push(glow("sphere", `${id}.beacon`, [0, roof, 0], [Math.max(0.12, 0.6 * k)], "#ff3b30", 4));
  return out;
}
