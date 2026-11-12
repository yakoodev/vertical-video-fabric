/**
 * Движения камеры — чистые кривые времени. Каждое движение — функция ``(u, ctx) → {position, target, fov, roll}``
 * на отрезке [t0, t1] (u — доля 0..1, без изинга: изинг — часть движения), из неё ключи камеры с шагом кадра
 * (``moveKeys``) — движок дальше рисует их как обычные ключи (follow, clamp, keepInFrame работают как раньше).
 *
 * Опоры: ``target`` — точка, на которую смотрим (центр кадра), ``heading`` — куда смотрит субъект (камера по
 * умолчанию — напротив него), ``distance`` — дистанция (м), ``yaw`` — азимут камеры вокруг цели от «напротив»,
 * ``height`` — подъём камеры над целью (м). Всё детерминировано (тряска — сумма синусов от seed).
 */
import { add3, clamp, D2R, R2D, local, r4, r4v, withDefaults, type Meta, type V3 } from "./meta.ts";

export interface CamState { position: V3; target: V3; fov: number; roll: number }

export interface CamCtx {
  target: V3;
  heading: number;
  distance: number;
  yaw: number;
  height: number;
  fov: number;
  duration: number;
  /** Вторая цель (whip_pan — куда хлестнуть, match_cut — куда перейти). */
  to?: V3;
  p: Record<string, any>;
}

const easeIO = (x: number) => -(Math.cos(Math.PI * clamp(x, 0, 1)) - 1) / 2;
const easeOutCubic = (x: number) => 1 - Math.pow(1 - clamp(x, 0, 1), 3);
const easeInOutCubic = (x: number) => { x = clamp(x, 0, 1); return x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2; };
/** Упругий выход с небольшим перелётом (punch-in). */
const easeOutBack = (x: number, s = 1.4) => { x = clamp(x, 0, 1) - 1; return x * x * ((s + 1) * x + s) + 1; };
const lerp = (a: number, b: number, k: number) => a + (b - a) * k;
const lerp3 = (a: number[], b: number[], k: number): V3 => [lerp(a[0], b[0], k), lerp(a[1], b[1], k), lerp(a[2], b[2], k)];

/** Камера на сфере вокруг цели: азимут yaw от «напротив субъекта», подъём height (м), дистанция d. */
export function orbitPoint(target: V3, heading: number, yaw: number, d: number, height: number): V3 {
  const a = (heading + yaw) * D2R;
  const horiz = Math.sqrt(Math.max(1e-6, d * d - height * height));
  return [target[0] + Math.sin(a) * horiz, target[1] + height, target[2] + Math.cos(a) * horiz];
}

/** Ручная камера: плавная «дыхательная» тряска (сумма несоизмеримых синусов), амплитуда в метрах. */
export function handheldOffset(t: number, amp: number, seed = 1): V3 {
  const s = seed * 1.37;
  const n = (f: number, ph: number) => Math.sin(t * f + ph + s) * 0.6 + Math.sin(t * f * 2.13 + ph * 1.7 + s * 0.5) * 0.3 + Math.sin(t * f * 4.71 + ph * 0.3) * 0.1;
  return [amp * n(1.1, 0.3), amp * 0.7 * n(1.37, 2.1), amp * 0.5 * n(0.83, 4.2)];
}

/** Угол обзора для dolly-zoom: цель того же размера в кадре при дистанции d (fov0 при d0). */
export function vertigoFov(fov0: number, d0: number, d: number): number {
  return 2 * Math.atan((Math.tan((fov0 * D2R) / 2) * d0) / Math.max(0.05, d)) * R2D;
}

/**
 * Сила punch_in по умолчанию — по крупности плана: высота кадра на дистанции цели h = 2·d·tan(fov/2).
 * Средний план (h ≈ 1.5 м, умолчания движения) — 0.5; крупное лицо (h ≈ 0.3–0.6 м) — 0.3, иначе рывок
 * влетает в глаз; общий (h ≥ 3 м) — до 0.7. Явный strength плана — как задан.
 */
export function punchStrength(distance: number, fov: number): number {
  const h = 2 * Math.max(0.05, distance) * Math.tan((Math.max(1, fov) * D2R) / 2);
  return r4(clamp(0.5 * Math.sqrt(h / 1.5), 0.3, 0.7));
}

type MoveFn = (u: number, c: CamCtx) => CamState;

interface MoveDef { meta: Meta; distance?: number; fov?: number; height?: number; fn: MoveFn }

const P = {
  target: { name: "target", type: "target" as const, ru: "куда смотрим: id персонажа, якорь комнаты или [x, y, z]" },
  distance: (d: number) => ({ name: "distance", type: "number" as const, default: d, min: 0.3, max: 20, ru: "дистанция до цели, м" }),
  yaw: { name: "yaw", type: "number" as const, default: 0, min: -180, max: 180, ru: "азимут камеры от «напротив», °" },
  height: (h: number) => ({ name: "height", type: "number" as const, default: h, min: -2, max: 12, ru: "подъём камеры над целью, м" }),
  fov: (f: number) => ({ name: "fov", type: "number" as const, default: f, min: 8, max: 100, ru: "угол обзора по вертикали, °" }),
  strength: (s: number, ru = "сила движения (1 — как задумано)") => ({ name: "strength", type: "number" as const, default: s, min: 0, max: 3, ru }),
  at: { name: "at", type: "number" as const, min: 0, max: 1, default: 0.15, ru: "когда случается рывок: доля длины плана" },
};

const still = (c: CamCtx, d = c.distance, yaw = c.yaw, h = c.height, target = c.target): V3 => orbitPoint(target, c.heading, yaw, d, h);

export const CAMERA_MOVES: Record<string, MoveDef> = {
  static: {
    meta: { name: "static", title: "Статика", ru: "Камера стоит на штативе. Для диалога и спокойного кадра.", params: [P.target, P.distance(2.2), P.yaw, P.height(0.15), P.fov(35)] },
    fn: (_u, c) => ({ position: still(c), target: c.target, fov: c.fov, roll: 0 }),
  },
  slow_push: {
    meta: { name: "slow_push", title: "Медленный наезд", ru: "Плавно подъезжаем к герою — нарастает внимание, эмоция, «сейчас что-то будет».", tags: ["drama"],
      params: [P.target, P.distance(2.2), P.yaw, P.height(0.12), P.fov(35), P.strength(1, "насколько подъехать (1 — на треть)")] },
    fn: (u, c) => {
      const k = easeIO(u), s = c.p.strength ?? 1;
      const d = c.distance * lerp(1.15, 1.15 - 0.42 * s, k);
      return { position: still(c, d, c.yaw + 4 * s * (k - 0.5), c.height * lerp(1, 0.85, k)), target: c.target, fov: c.fov, roll: 0 };
    },
  },
  pull_out: {
    meta: { name: "pull_out", title: "Отъезд", ru: "Камера медленно отъезжает: герой остаётся один в большом пространстве, финал сцены.",
      params: [P.target, P.distance(1.6), P.yaw, P.height(0.2), P.fov(35), P.strength(1, "насколько отъехать (1 — вдвое)")] },
    fn: (u, c) => {
      const k = easeIO(u), s = c.p.strength ?? 1;
      const d = c.distance * lerp(1, 1 + s, k);
      return { position: still(c, d, c.yaw, c.height * lerp(1, 1.8, k)), target: c.target, fov: c.fov, roll: 0 };
    },
  },
  punch_in: {
    meta: { name: "punch_in", title: "Рывок вперёд", ru: "Резкий короткий наезд с лёгким перелётом — акцент на реакции или фразе. Сила по умолчанию — по крупности плана: на общем заметнее, на крупном лице — мягко.", tags: ["meme"],
      params: [P.target, P.distance(2.4), P.yaw, P.height(0.1), P.fov(35), P.at, { name: "speed", type: "number", default: 0.3, min: 0.08, max: 1, ru: "длина рывка, с" },
        { name: "strength", type: "number", min: 0, max: 3, ru: "насколько ближе (1 — почти вдвое); не задано — по крупности плана: ~0.5 на среднем, 0.3 на крупном лице, до 0.7 на общем" }] },
    fn: (u, c) => {
      const at = c.p.at ?? 0.15, sp = (c.p.speed ?? 0.3) / Math.max(0.1, c.duration), s = c.p.strength ?? punchStrength(c.distance, c.fov);
      const k = u <= at ? 0 : easeOutBack((u - at) / sp, 1.6);
      const d = c.distance * (1 - 0.45 * s * k);
      return { position: still(c, d), target: c.target, fov: c.fov, roll: 0 };
    },
  },
  dolly_zoom: {
    meta: { name: "dolly_zoom", title: "Вертиго (dolly zoom)", ru: "Камера едет назад, объектив приближает: герой того же размера, а мир за ним «растягивается». Шок, осознание.", tags: ["drama", "horror"],
      params: [P.target, P.distance(1.5), P.yaw, P.height(0.05), P.fov(42), { name: "dir", type: "enum", options: ["out", "in"], default: "out", ru: "out — фон растёт, in — фон сжимается" },
        P.strength(1, "насколько сильно (1 — дистанция ×2.3)")] },
    fn: (u, c) => {
      const k = easeInOutCubic(u), s = c.p.strength ?? 1;
      const far = c.distance * (1 + 1.3 * s);
      const [d0, d1] = c.p.dir === "in" ? [far, c.distance] : [c.distance, far];
      const fov0 = c.p.dir === "in" ? vertigoFov(c.fov, c.distance, far) : c.fov;
      const d = lerp(d0, d1, k);
      return { position: still(c, d, c.yaw, c.height * d / d0), target: c.target, fov: vertigoFov(fov0, d0, d), roll: 0 };
    },
  },
  orbit: {
    meta: { name: "orbit", title: "Облёт", ru: "Камера плавно обходит героя по дуге — показать человека и пространство вокруг.",
      params: [P.target, P.distance(2.4), P.yaw, P.height(0.35), P.fov(35), { name: "arc", type: "number", default: 70, min: -360, max: 360, ru: "дуга облёта, ° (минус — в другую сторону)" }] },
    fn: (u, c) => {
      const k = easeIO(u), arc = c.p.arc ?? 70;
      return { position: still(c, c.distance, c.yaw - arc / 2 + arc * k, c.height), target: c.target, fov: c.fov, roll: 0 };
    },
  },
  handheld: {
    meta: { name: "handheld", title: "С рук (документалка)", ru: "Живая ручная камера: лёгкое покачивание и подруливание, как у оператора-документалиста.", tags: ["doc"],
      params: [P.target, P.distance(1.9), P.yaw, P.height(0.1), P.fov(38), P.strength(1, "сила тряски (1 — 2.5 см)"), { name: "seed", type: "number", default: 1, ru: "вариант тряски" }] },
    fn: (u, c) => {
      const t = u * c.duration, s = c.p.strength ?? 1, seed = c.p.seed ?? 1;
      const off = handheldOffset(t, 0.025 * s, seed), aim = handheldOffset(t + 7.3, 0.018 * s, seed + 3);
      const drift = 0.06 * s * Math.sin(t * 0.35 + seed);
      return { position: add3(still(c, c.distance + drift), off), target: add3(c.target, aim), fov: c.fov, roll: 0.8 * s * Math.sin(t * 0.9 + seed * 2) };
    },
  },
  whip_pan: {
    meta: { name: "whip_pan", title: "Хлёсткая панорама", ru: "Камера резко «хлещет» в сторону на другую цель — склейка без склейки (лучше с переходом whip).", tags: ["meme", "transition"],
      params: [P.target, { name: "to", type: "target", ru: "куда хлестнуть (вторая цель)" }, P.distance(2.2), P.yaw, P.height(0.1), P.fov(35),
        { name: "at", type: "number", default: 0.5, min: 0, max: 1, ru: "середина рывка: доля плана" }, { name: "speed", type: "number", default: 0.32, min: 0.12, max: 1.5, ru: "длина рывка, с" }] },
    fn: (u, c) => {
      const at = c.p.at ?? 0.5, sp = (c.p.speed ?? 0.32) / Math.max(0.1, c.duration);
      const k = easeInOutCubic((u - (at - sp / 2)) / sp);
      const pos = still(c);
      const to = c.to || add3(c.target, local(c.heading, [1.6, 0, 0]));
      // Поворот, а не скольжение цели: точка взгляда — по дуге вокруг камеры на одной дистанции.
      const a = lerp3(c.target, to, k);
      const dir = [a[0] - pos[0], a[1] - pos[1], a[2] - pos[2]];
      const len = Math.hypot(dir[0], dir[1], dir[2]) || 1;
      const dist = lerp(Math.hypot(c.target[0] - pos[0], c.target[1] - pos[1], c.target[2] - pos[2]), Math.hypot(to[0] - pos[0], to[1] - pos[1], to[2] - pos[2]), k);
      return { position: pos, target: [pos[0] + (dir[0] / len) * dist, pos[1] + (dir[1] / len) * dist, pos[2] + (dir[2] / len) * dist], fov: c.fov, roll: 3 * Math.sin(Math.PI * clamp((u - (at - sp / 2)) / sp, 0, 1)) };
    },
  },
  crash_zoom: {
    meta: { name: "crash_zoom", title: "Краш-зум", ru: "Резкий зум объективом в лицо на панчлайне, с толчком. Мемный акцент.", tags: ["meme"],
      params: [P.target, P.distance(2.6), P.yaw, P.height(0.05), P.fov(40), P.at, { name: "zoom", type: "number", default: 2.6, min: 1.2, max: 6, ru: "во сколько раз приблизить" },
        { name: "speed", type: "number", default: 0.14, min: 0.05, max: 0.6, ru: "длина зума, с" }] },
    fn: (u, c) => {
      const at = c.p.at ?? 0.15, sp = (c.p.speed ?? 0.14) / Math.max(0.1, c.duration);
      const k = u <= at ? 0 : easeOutCubic((u - at) / sp);
      const fov = 2 * Math.atan(Math.tan((c.fov * D2R) / 2) / lerp(1, c.p.zoom ?? 2.6, k)) * R2D;
      const kick = u > at ? Math.exp(-((u - at) * c.duration) * 9) * Math.sin((u - at) * c.duration * 55) * 0.012 : 0;
      return { position: add3(still(c), [kick, kick * 0.6, 0]), target: c.target, fov, roll: kick * 40 };
    },
  },
  over_shoulder: {
    meta: { name: "over_shoulder", title: "Через плечо", ru: "Камера за плечом героя смотрит туда же, куда он: на собеседника, экран, предмет.",
      params: [P.target, { name: "who", type: "string", ru: "чьё плечо: id персонажа" }, { name: "side", type: "number", default: 1, min: -1, max: 1, ru: "плечо: 1 — правое, −1 — левое" },
        P.fov(38), P.strength(1, "медленный наезд за план (0 — статично)")] },
    fn: (u, c) => {
      // Без движка follow (нет персонажа): ставим камеру позади цели-плеча вручную.
      const s = c.p.strength ?? 1, k = easeIO(u);
      const shoulder = c.p.shoulder as V3 | undefined;
      const back = shoulder || add3(c.target, local(c.heading, [0, 0, 1.4]));
      const off = local(c.heading, [-0.28 * (c.p.side ?? 1), 0.12, -0.55 + 0.12 * s * k]);
      return { position: add3(back, off), target: c.target, fov: c.fov, roll: 0 };
    },
  },
  top_down: {
    meta: { name: "top_down", title: "Сверху (god view)", ru: "Камера строго сверху, медленно поворачивается: схема места, одиночество, «вид бога».",
      params: [P.target, P.distance(4.5), P.fov(40), { name: "spin", type: "number", default: 25, min: -180, max: 180, ru: "поворот кадра за план, °" }] },
    fn: (u, c) => {
      const a = (c.heading + (c.p.spin ?? 25) * easeIO(u)) * D2R;
      const d = c.distance * lerp(1.05, 0.92, easeIO(u));
      // Чуть в сторону от вертикали (иначе lookAt вырожден): «верх» кадра крутится вместе с a.
      return { position: [c.target[0] + Math.sin(a) * 0.02 * d, c.target[1] + d, c.target[2] + Math.cos(a) * 0.02 * d], target: c.target, fov: c.fov, roll: 0 };
    },
  },
  dutch_tilt: {
    meta: { name: "dutch_tilt", title: "Голландский угол", ru: "Кадр заваливается набок — тревога, что-то не так, «крыша едет».", tags: ["horror", "drama"],
      params: [P.target, P.distance(2.0), P.yaw, P.height(-0.1), P.fov(36), { name: "roll", type: "number", default: 16, min: -40, max: 40, ru: "завал, °" }] },
    fn: (u, c) => {
      const k = easeIO(Math.min(1, u * 2.2));
      return { position: still(c, c.distance * lerp(1.08, 0.94, easeIO(u)), c.yaw - 6 * easeIO(u)), target: c.target, fov: c.fov, roll: (c.p.roll ?? 16) * k };
    },
  },
  crane_reveal: {
    meta: { name: "crane_reveal", title: "Подъём крана", ru: "Камера поднимается снизу вверх и назад, открывая место действия — эффектное начало сцены.",
      params: [P.target, P.distance(1.6), P.yaw, P.fov(38), { name: "rise", type: "number", default: 2.4, min: 0.5, max: 10, ru: "насколько подняться, м" }] },
    fn: (u, c) => {
      const k = easeInOutCubic(u), rise = c.p.rise ?? 2.4;
      const h = lerp(-c.target[1] + 0.35, rise, k);
      const d = lerp(c.distance, c.distance * 2 + rise * 0.6, k);
      return { position: still(c, Math.max(d, Math.abs(h) + 0.3), c.yaw + 12 * (1 - k), h), target: c.target, fov: c.fov, roll: 0 };
    },
  },
  truck: {
    meta: { name: "truck", title: "Проезд вбок", ru: "Камера едет параллельно герою — параллакс: передний план бежит быстрее заднего.",
      params: [P.target, P.distance(2.4), P.yaw, P.height(0.1), P.fov(35), { name: "travel", type: "number", default: 1.6, min: -8, max: 8, ru: "сколько проехать вбок, м" }] },
    fn: (u, c) => {
      const k = easeIO(u), tr = c.p.travel ?? 1.6;
      const side = local(c.heading + c.yaw, [tr * (k - 0.5), 0, 0]);
      return { position: add3(still(c), side), target: add3(c.target, [side[0] * 0.8, 0, side[2] * 0.8]), fov: c.fov, roll: 0 };
    },
  },
  match_cut: {
    meta: { name: "match_cut", title: "Монтаж по форме (match cut)", ru: "Новый план с той же композицией, что прошлый: цель встаёт в то же место кадра. Склейка по форме/движению.", tags: ["transition"],
      params: [P.target, P.distance(2.2), P.yaw, P.height(0.12), P.fov(35)], note: "Ставится сразу за другим движением: берёт его финальное смещение камеры от цели и переносит на новую цель." },
    fn: (_u, c) => {
      const off = (c.p.offset as V3 | undefined) || [0, c.height, c.distance];
      return { position: add3(c.target, off), target: c.target, fov: c.fov, roll: c.p.roll ?? 0 };
    },
  },
};

export interface MoveSpec {
  move: string;
  t0: number;
  t1: number;
  target: V3;
  heading?: number;
  to?: V3;
  /** false — продолжить от прошлого движения без склейки (по умолчанию каждое движение — новый план, склейка). */
  cut?: boolean;
  [k: string]: any;
}

export function moveMeta(name: string): Meta | undefined {
  return CAMERA_MOVES[name]?.meta;
}

/** Состояние камеры движения на момент t (внутри [t0, t1]; вне — края). */
export function moveAt(m: MoveSpec, t: number, prevEnd?: CamState): CamState {
  const def = CAMERA_MOVES[m.move] || CAMERA_MOVES.static;
  const p = withDefaults(def.meta, m);
  const dur = Math.max(1e-3, m.t1 - m.t0);
  const ctx: CamCtx = {
    target: m.target, heading: m.heading ?? 0, distance: p.distance ?? 2.2, yaw: p.yaw ?? 0, height: p.height ?? 0.12, fov: p.fov ?? 35,
    duration: dur, to: m.to, p,
  };
  if (m.move === "match_cut" && prevEnd && !p.offset) {
    // Смещение камеры от прошлой цели (её прошлый кадр) — на новую цель.
    ctx.p = { ...p, offset: [prevEnd.position[0] - (m.prevTarget?.[0] ?? prevEnd.target[0]), prevEnd.position[1] - (m.prevTarget?.[1] ?? prevEnd.target[1]),
      prevEnd.position[2] - (m.prevTarget?.[2] ?? prevEnd.target[2])], roll: prevEnd.roll };
    ctx.fov = prevEnd.fov;
  }
  return def.fn(clamp((t - m.t0) / dur, 0, 1), ctx);
}

/**
 * Ключи камеры движения с шагом кадра (точно по кадрам ролика): первый ключ — склейка (cut), если план новый.
 * Поле dof (сила размытия, если глубина резкости включена) — у всех ключей одинаковое.
 */
export function moveKeys(m: MoveSpec, fps = 30, prevEnd?: CamState, dof?: number): Record<string, any>[] {
  const out: Record<string, any>[] = [];
  const n = Math.max(1, Math.round((m.t1 - m.t0) * fps));
  for (let i = 0; i <= n; i++) {
    const t = m.t0 + ((m.t1 - m.t0) * i) / n;
    const s = moveAt(m, t, prevEnd);
    const k: Record<string, any> = { t: r4(t), position: r4v(s.position), target: r4v(s.target), fov: r4(s.fov), roll: r4(s.roll), ease: "linear" };
    if (dof !== undefined) k.dof = dof;
    if (i === 0 && m.cut !== false) k.cut = true;
    out.push(k);
  }
  return out;
}
