/**
 * Окружения (комнаты и площадки) — процедурные, из своих примитивов движка (props.ts: box, rounded_box, cylinder,
 * sphere, cone, capsule, torus, plane, cyclorama, …) и комнаты room.ts. Никаких паков: окружение рисуется в любом
 * месте, где есть движок. Сборщик — чистая функция параметров → {room, objects, lights (практические: лампы,
 * экраны), env, anchors}; якоря — места действия: ``hero`` (где стоит/сидит герой, heading — куда смотрит),
 * ``focus`` (цель кадра по умолчанию), другие (``desk``, ``window``, ``screen`` …) — для камеры и вещей.
 *
 * Параметры у всех: ``colors`` — палитра (ключи — у метаданных окружения), ``props`` — список вещей,
 * которые убрать (``{"hide": ["plant"]}``) или добавить из набора окружения, ``seed`` — вариант раскладки.
 */
import { withDefaults, type Meta, type V3 } from "./meta.ts";

export interface Anchor { pos: V3; heading?: number; seat?: number }

export interface RoomOut {
  room?: Record<string, any>;
  objects: Record<string, any>[];
  lights?: Record<string, any>[];
  env?: Record<string, any>;
  anchors: Record<string, Anchor>;
  indoor: boolean;
}

export interface RoomDef {
  meta: Meta;
  palette: Record<string, string>;
  /** Что к окружению идёт лучше всего (каталог, агент): свет, стиль, настроение. */
  suggest?: { light?: string; look?: string; mood?: string; fx?: string[] };
  fn: (p: Record<string, any>, c: Record<string, string>) => RoomOut;
}

export const ROOM_PARAMS = [
  { name: "colors", type: "string" as const, ru: "палитра: {ключ: \"#цвет\"} поверх палитры окружения" },
  { name: "hide", type: "string" as const, ru: "какие вещи убрать: список id (см. props окружения)" },
  { name: "seed", type: "number" as const, default: 1, ru: "вариант раскладки мелочей" },
];

const box = (id: string, pos: number[], size: number[], color: string, extra: Record<string, any> = {}) =>
  ({ id, primitive: "box", position: pos, params: { size, color, ...extra } });
const rbox = (id: string, pos: number[], size: number[], color: string, radius = 0.02, extra: Record<string, any> = {}) =>
  ({ id, primitive: "rounded_box", position: pos, params: { size, color, radius, ...extra } });
const cyl = (id: string, pos: number[], size: number[], color: string, extra: Record<string, any> = {}) =>
  ({ id, primitive: "cylinder", position: pos, params: { size, color, ...extra } });

export const ROOMS: Record<string, RoomDef> = {
  void_stage: {
    meta: { name: "void_stage", title: "Пустая сцена с лучом", ru: "Чёрная пустота, круглый подиум и пятно света. Монолог, признание, стендап, «герой наедине с мыслью».",
      params: ROOM_PARAMS, tags: ["stage"] },
    suggest: { light: "stage_spot", look: "anime_soft" },
    palette: { floor: "#17161c", stage: "#24222b", rim: "#3a3646", haze: "#0b0b10" },
    fn: (_p, c) => ({
      objects: [
        cyl("floor", [0, -0.02, 0], [24, 0.02], c.floor, { roughness: 0.35, segments: 64 }),
        cyl("stage", [0, 0, 0], [2.6, 0.12], c.stage, { roughness: 0.55, segments: 64 }),
        cyl("stage_rim", [0, 0.115, 0], [2.64, 0.012], c.rim, { roughness: 0.4, segments: 64, emissive: "#6a6090", emissiveIntensity: 0.25 }),
      ],
      env: { background: c.haze, fog: { color: c.haze, near: 5, far: 16 } },
      anchors: { hero: { pos: [0, 0.12, 0], heading: 0 }, focus: { pos: [0, 1.2, 0] } },
      indoor: false,
    }),
  },
  studio_cyclorama: {
    meta: { name: "studio_cyclorama", title: "Студийная циклорама", ru: "Бесшовный белый (или цветной) фон-«бесконечность» студии: пол плавно переходит в стену. Продукт, интервью, чистый показ героя.",
      params: ROOM_PARAMS, tags: ["studio"] },
    suggest: { light: "studio_3pt", look: "anime_soft" },
    palette: { sweep: "#e9e6e1", floor: "#dedad3", stand: "#2b2d33" },
    fn: (_p, c) => ({
      objects: [
        { id: "cyc", primitive: "cyclorama", position: [0, 0, -2.2], params: { width: 9, height: 4.5, depth: 6, radius: 1.4, color: c.sweep, roughness: 0.92 } },
        cyl("light_stand_l", [-2.6, 0, -0.6], [0.05, 2.1], c.stand, { roughness: 0.4, metalness: 0.6 }),
        rbox("softbox_l", [-2.6, 1.9, -0.6], [0.7, 0.9, 0.16], "#f7f7f5", 0.03, { emissive: "#ffffff", emissiveIntensity: 1.2, noShadow: true }),
      ],
      env: { background: c.sweep, fog: false },
      anchors: { hero: { pos: [0, 0, 0], heading: 0 }, focus: { pos: [0, 1.1, 0] } },
      indoor: true,
    }),
  },
};

export function buildRoomPreset(name: string, given: Record<string, any> = {}): RoomOut {
  const def = ROOMS[name];
  if (!def) throw new Error(`нет окружения «${name}»`);
  const p = withDefaults(def.meta, given);
  const colors = { ...def.palette, ...(given.colors || {}) };
  const out = def.fn(p, colors);
  const hide = new Set<string>(Array.isArray(given.hide) ? given.hide : []);
  if (hide.size) {
    // Вещь и всё, что к ней крепится (parent), убираются вместе.
    const gone = new Set<string>();
    let changed = true;
    while (changed) {
      changed = false;
      for (const o of out.objects) if (!gone.has(o.id) && (hide.has(o.id) || hide.has(String(o.id).split(".")[0]) || (o.parent && gone.has(o.parent)))) { gone.add(o.id); changed = true; }
    }
    out.objects = out.objects.filter((o) => !gone.has(o.id));
    out.lights = (out.lights || []).filter((l) => !hide.has(String(l.id || "").split(".")[0]));
  }
  return out;
}
