/**
 * Пост и атмосфера — накладываемые поверх look эффекты (список ``fx`` в плане), и настроения (``mood``) — готовые
 * связки «свет + атмосфера + подтон». Эффект — заплатка полей сцены: ``post`` (post.ts: свечение, зерно, аберрация,
 * виньетка, глубина резкости), ``stylize`` (stylize.ts: лучи, блики объектива, засветки), ``atmosphere``
 * (atmos.ts: дождь, снег, пылинки, светлячки, слои тумана). Параметры — у метаданных, сила ``amount`` — общая.
 */
import { withDefaults, type Meta } from "./meta.ts";

export interface FxPatch {
  post?: Record<string, any>;
  stylize?: Record<string, any>;
  atmosphere?: Record<string, any>[];
  /** Сила глубины резкости в ключах камеры. */
  dof?: number;
}

const AMT = (d = 1) => ({ name: "amount", type: "number" as const, default: d, min: 0, max: 3, ru: "сила (1 — как задумано)" });

interface FxDef { meta: Meta; fn: (p: Record<string, any>) => FxPatch }

export const FX: Record<string, FxDef> = {
  bloom: {
    meta: { name: "bloom", title: "Свечение", ru: "Яркие места мягко светятся ореолом: лампы, экраны, неон, блики.", params: [AMT()] },
    fn: (p) => ({ post: { bloom: { strength: 0.75 * p.amount, radius: 0.6, threshold: 0.72 } } }),
  },
  film_grain: {
    meta: { name: "film_grain", title: "Зерно плёнки", ru: "Живое зерно, сильнее в средних тонах, — картинка «дышит», не выглядит пластиковой.", params: [AMT()] },
    fn: (p) => ({ post: { grain: 0.085 * p.amount } }),
  },
  chromatic: {
    meta: { name: "chromatic", title: "Хроматическая аберрация", ru: "Цветная кайма по краям кадра, как у дешёвого объектива. Тревога, сон, ретро.", params: [AMT()] },
    fn: (p) => ({ post: { ca: 2.6 * p.amount } }),
  },
  vignette: {
    meta: { name: "vignette", title: "Виньетка", ru: "Затемнённые углы — взгляд сам идёт в центр кадра.", params: [AMT()] },
    fn: (p) => ({ post: { vignette: Math.min(1, 0.55 * p.amount) } }),
  },
  depth_of_field: {
    meta: { name: "depth_of_field", title: "Глубина резкости", ru: "Резко только то, на что смотрит камера, фон и передний план мягко размыты. Фокус всегда на цели кадра.", params: [AMT()] },
    fn: (p) => ({ post: { dof: true }, dof: 1.4 * p.amount }),
  },
  fog_layers: {
    meta: { name: "fog_layers", title: "Слои тумана", ru: "Медленно плывущие полосы тумана у пола и в глубине — объём и тайна.", params: [AMT(), { name: "color", type: "color", ru: "цвет тумана (по умолчанию — от схемы света)" }] },
    fn: (p) => ({ atmosphere: [{ kind: "fog", amount: p.amount, color: p.color }] }),
  },
  rain: {
    meta: { name: "rain", title: "Дождь", ru: "Косые струи дождя с размытием движения, ближние капли крупнее. Тоска, драма, ночной город.", params: [AMT(), { name: "wind", type: "number", default: 0.25, min: -2, max: 2, ru: "ветер: наклон струй" }] },
    fn: (p) => ({ atmosphere: [{ kind: "rain", amount: p.amount, wind: p.wind }], post: { grain: 0.04 } }),
  },
  snow: {
    meta: { name: "snow", title: "Снег", ru: "Пушистые хлопья медленно кружатся и падают, вдали — мельче. Зима, Новый год, тишина.", params: [AMT(), { name: "wind", type: "number", default: 0.15, min: -2, max: 2, ru: "ветер" }] },
    fn: (p) => ({ atmosphere: [{ kind: "snow", amount: p.amount, wind: p.wind }] }),
  },
  dust_motes: {
    meta: { name: "dust_motes", title: "Пылинки в свете", ru: "Пылинки медленно парят и вспыхивают в луче света. Чердак, утро, старая квартира.", params: [AMT()] },
    fn: (p) => ({ atmosphere: [{ kind: "dust", amount: p.amount }] }),
  },
  fireflies: {
    meta: { name: "fireflies", title: "Светлячки", ru: "Тёплые огоньки плавают и мерцают в сумерках. Лес, сказка, романтика.", params: [AMT(), { name: "color", type: "color", default: "#ffe27a", ru: "цвет огоньков" }] },
    fn: (p) => ({ atmosphere: [{ kind: "fireflies", amount: p.amount, color: p.color }], post: { bloom: { strength: 0.7, radius: 0.6, threshold: 0.65 } } }),
  },
  god_rays: {
    meta: { name: "god_rays", title: "Божественные лучи", ru: "Видимые лучи света от солнца/окна/лампы, расходящиеся в воздухе.", params: [AMT(), { name: "from", type: "string", default: "auto", ru: "откуда: id лампы, sun или auto (самая яркая)" }] },
    fn: (p) => ({ stylize: { rays: { strength: 0.55 * p.amount, from: p.from } } }),
  },
  lens_flare: {
    meta: { name: "lens_flare", title: "Блики объектива", ru: "Кольца и полоса блика, когда в кадре яркая лампа или солнце. Летнее, «киношное».", params: [AMT(), { name: "from", type: "string", default: "auto", ru: "от какой лампы: id, sun или auto" }] },
    fn: (p) => ({ stylize: { flare: { strength: 0.7 * p.amount, from: p.from } } }),
  },
  light_leak: {
    meta: { name: "light_leak", title: "Засветка плёнки", ru: "Тёплые пятна засвеченной плёнки медленно плывут по краю кадра. Воспоминание, лето, винтаж.", params: [AMT(), { name: "color", type: "color", default: "#ff9a4a", ru: "цвет засветки" }] },
    fn: (p) => ({ stylize: { leak: { strength: 0.6 * p.amount, color: p.color } } }),
  },
};

export function fxPatch(name: string, given: Record<string, any> = {}): FxPatch {
  const def = FX[name];
  if (!def) throw new Error(`нет эффекта «${name}»`);
  return def.fn(withDefaults(def.meta, given));
}

export interface MoodDef { meta: Meta; light: string; lightParams?: Record<string, any>; fx: (string | Record<string, any>)[]; grade?: Record<string, any> }

export const MOODS: Record<string, MoodDef> = {
  night_rain: {
    meta: { name: "night_rain", title: "Ночной дождь", ru: "Мокрая неоновая ночь: дождь, туман в глубине, свечение вывесок, холодный раскол цвета." },
    light: "night_neon", fx: ["rain", "fog_layers", "bloom", { name: "chromatic", amount: 0.5 }], grade: { saturation: 1.05 },
  },
  foggy_morning: {
    meta: { name: "foggy_morning", title: "Туманное утро", ru: "Молочный рассеянный свет, слои тумана, пылинки. Тихо, сонно, немного грустно." },
    light: "overcast_soft", fx: [{ name: "fog_layers", amount: 1.4 }, { name: "dust_motes", amount: 0.6 }, { name: "film_grain", amount: 0.6 }], grade: { saturation: 0.85, lift: [0.04, 0.045, 0.05] },
  },
  snowy_evening: {
    meta: { name: "snowy_evening", title: "Снежный вечер", ru: "Синий зимний вечер, медленный снег, тёплые огни вдали. Новогоднее, уютное." },
    light: "moonlight", fx: ["snow", "bloom", { name: "vignette", amount: 0.7 }], grade: { shadows: "#24346e", highlights: "#ffd2a0", split: 0.4 },
  },
  dusty_attic: {
    meta: { name: "dusty_attic", title: "Пыльный чердак", ru: "Утренний луч из окна, видимые лучи, пылинки в свете. Находка, воспоминание." },
    light: "sunrise_window", fx: ["god_rays", { name: "dust_motes", amount: 1.4 }, { name: "film_grain", amount: 0.7 }],
  },
  horror_flicker: {
    meta: { name: "horror_flicker", title: "Хоррор-мерцание", ru: "Одна мигающая лампа, темнота, туман, тяжёлое зерно и тёмные углы." },
    light: "horror_lamp", fx: [{ name: "fog_layers", amount: 0.8, color: "#0b0c0e" }, { name: "film_grain", amount: 1.6 }, { name: "vignette", amount: 1.3 }, { name: "chromatic", amount: 0.6 }],
  },
  dreamy_pastel: {
    meta: { name: "dreamy_pastel", title: "Пастельный сон", ru: "Мягкий свет, засветки плёнки, свечение, приподнятые пастельные тени. Мечта, флешбэк." },
    light: "overcast_soft", fx: [{ name: "light_leak", color: "#ff9ad0" }, { name: "bloom", amount: 1.2 }, { name: "depth_of_field", amount: 0.8 }], grade: { lift: [0.07, 0.05, 0.08], saturation: 0.9 },
  },
  summer_noon: {
    meta: { name: "summer_noon", title: "Летний полдень", ru: "Яркое солнце, блики объектива, тёплые засветки. Каникулы, жара." },
    light: "noon_sun", fx: [{ name: "lens_flare", amount: 0.8 }, { name: "light_leak", amount: 0.5 }, "bloom"], grade: { saturation: 1.12 },
  },
  cyber_night: {
    meta: { name: "cyber_night", title: "Кибер-ночь", ru: "Неон, сильное свечение, блики, аберрация и лёгкий дождь. Будущее, хакеры, клубы." },
    light: "night_neon", fx: [{ name: "bloom", amount: 1.4 }, "lens_flare", "chromatic", { name: "rain", amount: 0.5 }],
  },
  cozy_evening: {
    meta: { name: "cozy_evening", title: "Уютный вечер", ru: "Тёплый закатный свет, пылинки, лёгкая засветка, мягкая виньетка. Дом, чай, плед." },
    light: "golden_hour", lightParams: { intensity: 0.8 }, fx: [{ name: "dust_motes", amount: 0.7 }, { name: "light_leak", amount: 0.4 }, { name: "vignette", amount: 0.8 }],
  },
  campfire_tales: {
    meta: { name: "campfire_tales", title: "Страшилки у костра", ru: "Живой огонь снизу, светлячки, ночная дымка — сказка или страшилка." },
    light: "campfire", fx: [{ name: "fireflies", amount: 0.8, color: "#ffb04a" }, { name: "fog_layers", amount: 0.6 }, { name: "film_grain", amount: 0.8 }],
  },
};
