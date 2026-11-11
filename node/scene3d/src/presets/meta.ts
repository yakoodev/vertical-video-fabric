/**
 * Общие типы реестра 3D-пресетов (looks, свет, камера, атмосфера, настроения, комнаты, эмоции, жесты, переходы).
 *
 * Каждый пресет — запись с метаданными для людей и агента (русское имя и описание, параметры с пределами) и
 * чистой функцией/данными, которые разворачивают его в поля сцены ``SceneSpec`` (presets/index.ts). Никакого
 * three.js здесь нет: модули presets/* проверяются node --test напрямую и одинаково работают в браузере.
 */

export type V3 = [number, number, number];

export type Category = "look" | "light" | "camera" | "fx" | "mood" | "room" | "emotion" | "gesture" | "transition";

export const CATEGORIES: Category[] = ["look", "light", "camera", "fx", "mood", "room", "emotion", "gesture", "transition"];

/** Русские названия категорий (каталог, docs/3d/presets.md, агент). */
export const CATEGORY_RU: Record<Category, string> = {
  look: "Стиль картинки",
  light: "Свет",
  camera: "Движение камеры",
  fx: "Пост и атмосфера",
  mood: "Настроение (связка свет + атмосфера)",
  room: "Окружение",
  emotion: "Эмоция персонажа",
  gesture: "Жест персонажа",
  transition: "Переход между кадрами",
};

/** Поле плана, которым пресет категории задаётся (``{"look": "anime_soft", "light": "golden_hour", ...}``). */
export const PLAN_FIELD: Record<Category, string> = {
  look: "look", light: "light", camera: "camera", fx: "fx", mood: "mood", room: "room",
  emotion: "performance", gesture: "performance", transition: "transitions",
};

export interface Param {
  name: string;
  type: "number" | "string" | "color" | "bool" | "point" | "target" | "enum";
  default?: unknown;
  min?: number;
  max?: number;
  options?: string[];
  /** Что делает (по-русски, коротко). */
  ru: string;
}

export interface Meta {
  name: string;
  /** Короткое русское имя для каталога. */
  title: string;
  /** Описание: как выглядит и когда брать. */
  ru: string;
  tags?: string[];
  params?: Param[];
  /** slot — имя занято под чужую реализацию (toon_ink — шейдер мем-пилота): без неё рисуется запасной вид. */
  status?: "ready" | "slot";
  note?: string;
}

export const clamp = (x: number, lo: number, hi: number) => (x < lo ? lo : x > hi ? hi : x);
export const D2R = Math.PI / 180;
export const R2D = 180 / Math.PI;

/** Детерминированный ГПСЧ (mulberry32): одинаковые «случайные» раскладки в любом браузере и в node. */
export function seeded(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Повернуть смещение [вправо, вверх, вперёд] в осях субъекта (heading — куда он смотрит, ° от +z к +x). */
export function local(heading: number, off: number[]): V3 {
  const h = heading * D2R;
  const fx = Math.sin(h), fz = Math.cos(h);
  // «вправо» субъекта, смотрящего вдоль (fx, fz): (-fz, fx)… с точки зрения зрителя напротив — влево.
  const rx = -fz, rz = fx;
  return [off[0] * rx + off[2] * fx, off[1], off[0] * rz + off[2] * fz];
}

export const add3 = (a: number[], b: number[]): V3 => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
export const r4 = (v: number) => Math.round(v * 1e4) / 1e4;
export const r4v = (v: number[]): V3 => [r4(v[0]), r4(v[1]), r4(v[2])];

/** Значения параметров: умолчания из метаданных, поверх — заданные (числа зажаты в [min, max]). */
export function withDefaults(meta: Meta, given: Record<string, any> = {}): Record<string, any> {
  const out: Record<string, any> = {};
  for (const p of meta.params || []) {
    let v = given[p.name] ?? p.default;
    if (p.type === "number" && typeof v === "number") v = clamp(v, p.min ?? -Infinity, p.max ?? Infinity);
    out[p.name] = v;
  }
  for (const [k, v] of Object.entries(given)) if (!(k in out)) out[k] = v;
  return out;
}
