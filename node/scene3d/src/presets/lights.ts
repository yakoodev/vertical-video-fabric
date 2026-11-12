/**
 * Световые схемы — чистые функции: опора — субъект (точка, куда смотрит кадр) и его heading; на выходе — список
 * ламп в формате движка (LightSpec), плюс окружение (фон/небо, туман, экспозиция) и мягкий подтон цвета.
 * Позиции ламп — в осях субъекта ([вправо, вверх, вперёд] от него), поэтому схема «едет» вместе с героем:
 * ключевой свет всегда с той же стороны лица, контровой — сзади. Мерцание огня/лампы — ключи силы света
 * от засеянного шума (детерминированно).
 */
import { add3, local, r4, r4v, seeded, withDefaults, type Meta, type V3 } from "./meta.ts";

export interface LightOut {
  lights: Record<string, any>[];
  env: Record<string, any>;
  grade?: Record<string, any>;
}

export interface LightCtx {
  subject: V3;
  heading: number;
  duration: number;
  /** В помещении (есть комната): «солнце» светит через окно, небо не рисуется. */
  indoor?: boolean;
  /** Якоря окружения (костёр — fire): схема ставит свет туда, где его источник в кадре. */
  anchors?: Record<string, { pos: number[] }>;
  p: Record<string, any>;
}

const at = (c: LightCtx, off: number[]): V3 => r4v(add3(c.subject, local(c.heading, off)));
/** Направленный свет: позиция по азимуту az (° от «перед субъектом», + — вправо от него) и высоте elev (°). */
function sunPos(c: LightCtx, az: number, elev: number, dist = 8): V3 {
  const a = (az * Math.PI) / 180, e = (elev * Math.PI) / 180;
  return at(c, [Math.sin(a) * Math.cos(e) * dist, Math.sin(e) * dist, Math.cos(a) * Math.cos(e) * dist]);
}

/** Ключи силы света «живого огня»: сумма шумов 2–14 Гц, шаг 1/15 с, от base·(1−depth) до base. */
export function flickerKeys(duration: number, base: number, depth: number, seed = 7, rate = 15): Record<string, any>[] {
  const rnd = seeded(seed);
  const ph = [rnd() * 6.28, rnd() * 6.28, rnd() * 6.28, rnd() * 6.28];
  const out = [];
  const n = Math.max(2, Math.ceil(duration * rate));
  for (let i = 0; i <= n; i++) {
    const t = (duration * i) / n;
    const v = 0.5 + 0.22 * Math.sin(t * 2.1 * 6.28 + ph[0]) + 0.15 * Math.sin(t * 4.7 * 6.28 + ph[1]) + 0.08 * Math.sin(t * 9.3 * 6.28 + ph[2]) + 0.05 * Math.sin(t * 13.9 * 6.28 + ph[3]);
    out.push({ t: r4(t), intensity: r4(base * (1 - depth + depth * Math.min(1, Math.max(0, v)))), ease: "linear" });
  }
  return out;
}

const SH = (extent = 4, radius = 3, size = 2048) => ({ size, extent, radius, bias: -0.0004, normalBias: 0.03 });
const k = (c: LightCtx) => c.p.intensity ?? 1;

interface RigDef { meta: Meta; fn: (c: LightCtx) => LightOut }

const PI = { name: "intensity", type: "number" as const, default: 1, min: 0, max: 4, ru: "общая яркость схемы (1 — как задумано)" };
const PSIDE = { name: "side", type: "number" as const, default: 1, min: -1, max: 1, ru: "с какой стороны ключевой свет: 1 — справа от героя, −1 — слева" };

export const LIGHT_RIGS: Record<string, RigDef> = {
  golden_hour: {
    meta: { name: "golden_hour", title: "Золотой час", ru: "Низкое тёплое солнце сбоку-сзади, длинные тени, медовый контровой ободок на волосах. Тепло, ностальгия.", params: [PI, PSIDE], tags: ["warm", "outdoor"] },
    fn: (c) => ({
      lights: [
        { type: "hemisphere", id: "sky", color: "#ffd2a6", ground: "#5a4a6e", intensity: 0.75 * k(c) },
        { type: "directional", id: "sun", color: "#ffad5c", intensity: 3.4 * k(c), position: sunPos(c, 125 * (c.p.side ?? 1), 13), target: c.subject, castShadow: true, shadow: SH(5, 2) },
        { type: "point", id: "bounce", color: "#ff9a6a", intensity: 1.2 * k(c), position: at(c, [-0.6 * (c.p.side ?? 1), 0.2, 1.6]), distance: 6, decay: 2 },
      ],
      env: { background: "#e9a46e", exposure: 1.05, envIntensity: 0.25, fog: { color: "#e8a777", near: 9, far: 34 },
        sky: { top: "#5f7fc0", horizon: "#ffc387", bottom: "#c98a6a", sun: { dir: sunPos(c, 125 * (c.p.side ?? 1), 13, 1), color: "#ffd09a", size: 0.06 } } },
      grade: { shadows: "#5a4c8a", highlights: "#ffb070", split: 0.35 },
    }),
  },
  overcast_soft: {
    meta: { name: "overcast_soft", title: "Пасмурно, мягко", ru: "Ровный мягкий свет облачного дня без резких теней. Спокойно, честно, «как в жизни».", params: [PI], tags: ["soft", "outdoor"] },
    fn: (c) => ({
      lights: [
        { type: "hemisphere", id: "sky", color: "#e4ecf4", ground: "#6b6a66", intensity: 1.6 * k(c) },
        { type: "directional", id: "sun", color: "#eef2f8", intensity: 0.9 * k(c), position: sunPos(c, 35, 62), target: c.subject, castShadow: true, shadow: SH(5, 9) },
      ],
      env: { background: "#c9d1d8", exposure: 1.0, envIntensity: 0.45, fog: { color: "#c8d0d6", near: 6, far: 28 },
        sky: { top: "#a9b6c4", horizon: "#dfe4e8", bottom: "#9aa09c" } },
      grade: { saturation: 0.92 },
    }),
  },
  noon_sun: {
    meta: { name: "noon_sun", title: "Полдень", ru: "Высокое белое солнце, короткие резкие тени, голубое небо. Лето, жара, бодрость.", params: [PI], tags: ["outdoor", "bright"] },
    fn: (c) => ({
      lights: [
        { type: "hemisphere", id: "sky", color: "#bcd8ff", ground: "#8a7a5c", intensity: 0.9 * k(c) },
        { type: "directional", id: "sun", color: "#fff6e6", intensity: 3.6 * k(c), position: sunPos(c, 30, 68), target: c.subject, castShadow: true, shadow: SH(5, 1.5) },
      ],
      env: { background: "#8fc3ff", exposure: 1.0, envIntensity: 0.35, fog: { color: "#bcdcff", near: 12, far: 45 },
        sky: { top: "#3d7fe0", horizon: "#bfe0ff", bottom: "#a89a7c", sun: { dir: sunPos(c, 30, 68, 1), color: "#fffbe8", size: 0.04 } } },
    }),
  },
  night_neon: {
    meta: { name: "night_neon", title: "Ночная улица, неон", ru: "Тёмная ночь, малиновый неон с одной стороны, бирюзовый — с другой, оранжевый фонарь сверху. Город, киберпанк.", params: [PI, PSIDE], tags: ["night", "neon"] },
    fn: (c) => {
      const s = c.p.side ?? 1;
      return {
        lights: [
          { type: "hemisphere", id: "night", color: "#2a2f5a", ground: "#120c1c", intensity: 0.35 * k(c) },
          { type: "point", id: "neon_a", color: "#ff2fa8", intensity: 2.4 * k(c), position: at(c, [1.3 * s, 0.6, 0.5]), distance: 7, decay: 2 },
          { type: "point", id: "neon_b", color: "#22d8ff", intensity: 4.5 * k(c), position: at(c, [-1.4 * s, 0.9, -0.9]), distance: 7, decay: 2 },
          { type: "spot", id: "street", color: "#ffa04a", intensity: 26 * k(c), position: at(c, [0.4 * s, 3.6, 1.2]), target: at(c, [0, -1.2, 0.3]), angle: 34, penumbra: 0.7, castShadow: true,
            beam: { length: 4.2, opacity: 0.12 }, halo: { size: 0.35, opacity: 0.7 } },
        ],
        env: { background: "#090a16", exposure: 1.1, envIntensity: 0.12, fog: { color: "#140f26", near: 4, far: 18 },
          sky: { top: "#05060f", horizon: "#2a1a44", bottom: "#07060c" } },
        grade: { shadows: "#2b2a7a", highlights: "#ff7ac8", split: 0.45, saturation: 1.12 },
      };
    },
  },
  horror_lamp: {
    meta: { name: "horror_lamp", title: "Одна лампа (хоррор)", ru: "Голая лампочка над героем и чернота вокруг; свет иногда вздрагивает. Хоррор, допрос, тревога.", params: [PI, { name: "flicker", type: "number", default: 1, min: 0, max: 1, ru: "сила мерцания" }], tags: ["horror", "dark"] },
    fn: (c) => {
      const base = 3.2 * k(c);
      const f = c.p.flicker ?? 1;
      return {
        lights: [
          { type: "ambient", id: "dark", color: "#1b2026", intensity: 0.08 * k(c) },
          { type: "point", id: "bulb", color: "#ffd7a0", intensity: base, position: at(c, [0.25, 1.35, 0.1]), distance: 6, decay: 2, castShadow: true, shadow: { size: 1024, radius: 1.5, far: 9 },
            halo: { size: 0.28, opacity: 0.85 }, keys: f > 0 ? flickerKeys(c.duration, base, 0.18 * f, 13, 12) : undefined,
            flicker: f > 0 ? [{ t: c.duration * 0.42, amount: 0.85 * f, duration: 0.5 }, { t: c.duration * 0.78, amount: 0.6 * f, duration: 0.35 }] : undefined },
        ],
        env: { background: "#030304", exposure: 1.0, envIntensity: 0.04, fog: { color: "#040405", near: 2.5, far: 9 } },
        grade: { shadows: "#1c2a26", highlights: "#ffd8a0", split: 0.4, saturation: 0.82, contrast: 1.12 },
      };
    },
  },
  studio_3pt: {
    meta: { name: "studio_3pt", title: "Студия, три точки", ru: "Классика интервью и продукта: мягкий ключ, слабая заливка, яркий контровой ободок. Чисто и дорого.", params: [PI, PSIDE], tags: ["studio"] },
    fn: (c) => {
      const s = c.p.side ?? 1;
      return {
        lights: [
          { type: "hemisphere", id: "room", color: "#cfd4dc", ground: "#3a3c42", intensity: 0.35 * k(c) },
          { type: "spot", id: "key", color: "#fff4e8", intensity: 18 * k(c), position: at(c, [1.7 * s, 1.3, 1.9]), target: c.subject, angle: 40, penumbra: 0.95, castShadow: true, shadow: { size: 2048, radius: 6 } },
          { type: "spot", id: "fill", color: "#dfe8ff", intensity: 4.5 * k(c), position: at(c, [-2.1 * s, 0.4, 1.6]), target: c.subject, angle: 50, penumbra: 1 },
          { type: "spot", id: "rim", color: "#ffffff", intensity: 16 * k(c), position: at(c, [-0.9 * s, 1.9, -1.9]), target: at(c, [0, 0.1, 0]), angle: 34, penumbra: 0.8 },
        ],
        env: { background: "#2b2e35", exposure: 1.0, envIntensity: 0.3, fog: false },
      };
    },
  },
  monitor_glow: {
    meta: { name: "monitor_glow", title: "Свет монитора ночью", ru: "Тёмная комната, лицо подсвечено холодным экраном снизу-спереди, вдали тёплая лампа. Стример, ночной геймер.", params: [PI, { name: "screen", type: "color", default: "#8fb8ff", ru: "цвет экрана" }], tags: ["night", "indoor"] },
    fn: (c) => {
      const scr = c.p.screen || "#8fb8ff";
      const base = 1.1 * k(c);
      // Экран «живёт»: картинка меняется — свет плавно гуляет ±12 % (смена сцены в игре/вкладки).
      const keys = [];
      const rnd = seeded(31);
      for (let t = 0; t <= c.duration + 1e-6; t += 0.5) keys.push({ t: r4(t), intensity: r4(base * (0.88 + 0.12 * rnd())), ease: "inOutSine" });
      return {
        lights: [
          { type: "hemisphere", id: "night", color: "#26304e", ground: "#0e0c14", intensity: 0.32 * k(c) },
          { type: "spot", id: "screen", color: scr, intensity: base, position: at(c, [0, -0.12, 0.62]), target: at(c, [0, 0.05, 0]), angle: 62, penumbra: 1, decay: 2, castShadow: true, shadow: { size: 1024, radius: 5 }, keys },
          { type: "point", id: "lamp", color: "#ffad66", intensity: 2.6 * k(c), position: at(c, [-1.4, 0.4, -1.6]), distance: 4, decay: 2, halo: { size: 0.22, opacity: 0.55 } },
          { type: "point", id: "rgb", color: "#b04cff", intensity: 1.4 * k(c), position: at(c, [1.1, -0.5, -0.8]), distance: 3, decay: 2 },
        ],
        env: { background: "#07080f", exposure: 1.1, envIntensity: 0.08, fog: { color: "#0a0b14", near: 3, far: 12 } },
        grade: { shadows: "#1d2a5a", highlights: "#cfe0ff", split: 0.35 },
      };
    },
  },
  sunrise_window: {
    meta: { name: "sunrise_window", title: "Рассвет в окне", ru: "Розово-золотой утренний луч из окна, видимые лучи в воздухе, холодные тени. Утро, надежда, новый день.", params: [PI, PSIDE], tags: ["morning", "indoor"] },
    fn: (c) => {
      const s = c.p.side ?? 1;
      return {
        lights: [
          { type: "hemisphere", id: "dawn", color: "#c9b8e6", ground: "#4b4258", intensity: 0.7 * k(c) },
          { type: "directional", id: "sun", color: "#ffb48a", intensity: 3.2 * k(c), position: sunPos(c, -110 * s, 18), target: c.subject, castShadow: true, shadow: SH(4, 1.5) },
          { type: "spot", id: "shaft", color: "#ffc9a0", intensity: 14 * k(c), position: at(c, [-2.4 * s, 1.6, -0.9]), target: at(c, [0.3 * s, -0.9, 0.3]), angle: 22, penumbra: 0.6, beam: { length: 4.5, opacity: 0.16 } },
        ],
        env: { background: "#c8a6b8", exposure: 1.05, envIntensity: 0.2, fog: { color: "#b9a0b6", near: 7, far: 26 },
          sky: { top: "#7d8fd6", horizon: "#ffbfa0", bottom: "#a88aa8", sun: { dir: sunPos(c, -110 * s, 18, 1), color: "#ffd8b0", size: 0.07 } } },
        grade: { shadows: "#5d5aa0", highlights: "#ffc79a", split: 0.4 },
      };
    },
  },
  campfire: {
    meta: { name: "campfire", title: "Костёр", ru: "Тёплый живой свет снизу, дрожащий как пламя, и холодная луна в спину. Лагерь, страшилки у огня, уют.", params: [PI], tags: ["night", "warm", "outdoor"] },
    fn: (c) => {
      // Огонь — там, где костёр окружения (якорь fire); без костра — сбоку-спереди от героя, вне кадра по центру.
      // Лампа — на высоте языков пламени (0.55 м), не у земли: у земли точечный свет даёт жгучее пятно под собой.
      // Ореола у рига нет: у костра окружения свой, а без костра светящийся шар висел бы в пустоте.
      const anchor = c.anchors?.fire?.pos;
      const fire: V3 = anchor ? r4v([anchor[0], 0.55, anchor[2]]) : at(c, [0.9, -c.subject[1] + 0.55, 1.3]);
      const base = (anchor ? 2.6 : 3.4) * k(c);
      return {
        lights: [
          { type: "hemisphere", id: "night", color: "#2a3a66", ground: "#120c0a", intensity: 0.3 * k(c) },
          { type: "directional", id: "moon", color: "#8fa8ff", intensity: 0.7 * k(c), position: sunPos(c, 160, 35), target: c.subject, castShadow: true, shadow: SH(5, 3) },
          { type: "point", id: "fire", color: "#ff7a2c", intensity: base, position: fire, distance: 5.5, decay: 2, castShadow: true, shadow: { size: 1024, radius: 3 },
            keys: flickerKeys(c.duration, base, 0.4, 5, 15) },
        ],
        env: { background: "#06070e", exposure: 1.05, envIntensity: 0.06, fog: { color: "#0a0b16", near: 4, far: 16 },
          sky: { top: "#03040a", horizon: "#141a33", bottom: "#050507" } },
        grade: { shadows: "#1c2650", highlights: "#ffa860", split: 0.5 },
      };
    },
  },
  underwater: {
    meta: { name: "underwater", title: "Под водой", ru: "Сине-зелёная толща, сверху бегут блики каустики, даль тонет в мути. Сон, глубина, тишина.", params: [PI], tags: ["fantasy"] },
    fn: (c) => ({
      lights: [
        // Заливка слабее, чем ключ с каустикой: иначе бегущие блики тонут в ровном свете.
        { type: "hemisphere", id: "water", color: "#5fd6d0", ground: "#0b2a3a", intensity: 0.62 * k(c) },
        { type: "spot", id: "caustics", color: "#c8f8ff", intensity: 62 * k(c), position: at(c, [0.4, 5, 0.6]), target: at(c, [0, -c.subject[1], 0]), angle: 32, penumbra: 0.6, cookie: "caustics", castShadow: true, shadow: { size: 1024, radius: 2 } },
        { type: "directional", id: "down", color: "#7fe0e8", intensity: 0.6 * k(c), position: sunPos(c, 10, 80), target: c.subject },
      ],
      env: { background: "#0b4a5c", exposure: 1.05, envIntensity: 0.2, fog: { color: "#0d5566", near: 1.5, far: 11 },
        sky: { top: "#3fb4c4", horizon: "#0d5566", bottom: "#062530" } },
      grade: { shadows: "#08354a", highlights: "#a8fff0", split: 0.5, saturation: 0.95 },
    }),
  },
  moonlight: {
    meta: { name: "moonlight", title: "Лунная ночь", ru: "Холодный голубой свет луны из окна или с неба, глубокие синие тени, тёплая искра где-то вдали. Тишина, тайна.", params: [PI, PSIDE], tags: ["night"] },
    fn: (c) => ({
      lights: [
        { type: "hemisphere", id: "night", color: "#5a6fae", ground: "#141420", intensity: 0.55 * k(c) },
        { type: "directional", id: "moon", color: "#a6bcff", intensity: 1.5 * k(c), position: sunPos(c, -120 * (c.p.side ?? 1), 32), target: c.subject, castShadow: true, shadow: SH(5, 2.5) },
        { type: "point", id: "warm", color: "#ffb36a", intensity: 1.6 * k(c), position: at(c, [1.6 * (c.p.side ?? 1), 0.2, -2.2]), distance: 4, decay: 2, halo: { size: 0.18, opacity: 0.6 } },
      ],
      env: { background: "#0e1428", exposure: 1.1, envIntensity: 0.1, fog: { color: "#111a33", near: 5, far: 20 },
        sky: { top: "#050816", horizon: "#22305a", bottom: "#0a0c14", moon: { dir: sunPos(c, -120 * (c.p.side ?? 1), 32, 1), color: "#e8eeff", size: 0.035 } } },
      grade: { shadows: "#1a2a66", highlights: "#d8e4ff", split: 0.4, saturation: 0.9 },
    }),
  },
  stage_spot: {
    meta: { name: "stage_spot", title: "Луч прожектора", ru: "Один жёсткий луч сверху на героя, видимый конус в дымке, вокруг — темнота. Сцена, признание, стендап.", params: [PI, { name: "color", type: "color", default: "#fff1d8", ru: "цвет луча" }], tags: ["stage", "dark"] },
    fn: (c) => ({
      lights: [
        { type: "ambient", id: "dark", color: "#1a1c24", intensity: 0.12 * k(c) },
        { type: "spot", id: "follow", color: c.p.color || "#fff1d8", intensity: 80 * k(c), position: at(c, [0.5, 4.4, 1.4]), target: at(c, [0, -c.subject[1], 0]), angle: 15, penumbra: 0.35,
          castShadow: true, shadow: { size: 2048, radius: 2 }, beam: { length: 5.4, opacity: 0.2 }, halo: { size: 0.5, opacity: 0.8 } },
        { type: "spot", id: "rim", color: "#8fa0ff", intensity: 12 * k(c), position: at(c, [-1.2, 2.2, -2.4]), target: c.subject, angle: 30, penumbra: 0.9 },
      ],
      env: { background: "#050507", exposure: 1.0, envIntensity: 0.05, fog: { color: "#0b0b10", near: 4, far: 15 } },
    }),
  },
};

export function lightRig(name: string, ctx: Omit<LightCtx, "p"> & { p?: Record<string, any> }): LightOut {
  const def = LIGHT_RIGS[name];
  if (!def) throw new Error(`нет световой схемы «${name}»`);
  const out = def.fn({ ...ctx, p: withDefaults(def.meta, ctx.p || {}) });
  // Чистим пустые поля (ключи без мерцания и т. п.) — спека короче и стабильна для сверки.
  out.lights = out.lights.map((l) => Object.fromEntries(Object.entries(l).filter(([, v]) => v !== undefined)));
  if (ctx.indoor) delete out.env.sky;
  return out;
}
