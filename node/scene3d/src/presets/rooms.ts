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
import { seeded, withDefaults, type Meta, type V3 } from "./meta.ts";
import { anchor, box, cable, cyl, glow, mk, put, rbox, ROOM_PARAMS, sph, tiltRot } from "./rooms_kit.ts";
import { HOME_ROOMS } from "./rooms_home.ts";
import { CITY_ROOMS } from "./rooms_city.ts";
import { NATURE_ROOMS } from "./rooms_nature.ts";

export { ROOM_PARAMS };

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
  suggest?: { light?: string; look?: string; mood?: string; fx?: string[];
    /** Общий план окружения по умолчанию (каталог): движение камеры с параметрами ({move, target, distance, height, yaw, fov}). */
    camera?: Record<string, any> };
  fn: (p: Record<string, any>, c: Record<string, string>) => RoomOut;
}

/**
 * Пустая сцена: двухъярусный круглый подиум со светящимися кромками, рампа-огоньки, бархатный занавес дугой в
 * дымке, позади — стойка микрофона и барный стул (стендап; hide: ["mic", "stool"] — чистый подиум).
 */
function voidStage(p: Record<string, any>, c: Record<string, string>): RoomOut {
  const r = seeded(Number(p.seed) || 1);
  const o: Record<string, any>[] = [
    cyl("floor", [0, -0.02, 0], [24, 0.02], c.floor, { roughness: 0.35, segments: 64 }),
    cyl("stage", [0, 0, 0], [3.1, 0.06], c.rim, { roughness: 0.55, segments: 64 }),
    cyl("stage.top", [0, 0.06, 0], [2.6, 0.06], c.stage, { roughness: 0.5, segments: 64 }),
    // Тор держится за низ кольца: лёжа его центр уходит на радиус вперёд — сдвигаем назад.
    mk("torus", "stage.rim", [0, 0.118, -1.3], [2.6, 2.6], c.glow, { tube: 0.008, rot: [90, 0, 0], segments: 64, emissive: c.glow, emissiveIntensity: 1.4, noShadow: true }),
    mk("torus", "stage.rim2", [0, 0.058, -1.55], [3.1, 3.1], c.glow, { tube: 0.006, rot: [90, 0, 0], segments: 64, emissive: c.glow, emissiveIntensity: 0.6, noShadow: true }),
  ];
  // Рампа: маленькие тёплые огни по передней дуге нижнего яруса.
  for (let i = 0; i < 9; i++) {
    const a = ((-60 + i * 15) * Math.PI) / 180;
    o.push(glow("sphere", `footlights.${i}`, [Math.sin(a) * 1.42, 0.055, Math.cos(a) * 1.42], [0.045], c.bulb, 2.6));
  }
  // Занавес: складки-цилиндры дугой позади, тонут в дымке.
  for (let i = 0; i < 34; i++) {
    const a = ((-62 + i * (124 / 33)) * Math.PI) / 180, R = 4.6 + (i % 2) * 0.12;
    o.push(cyl(`curtain.${i}`, [Math.sin(a) * R, 0, -Math.cos(a) * R], [0.34, 6.5], i % 2 ? c.curtain : c.curtain2, { roughness: 0.95, segments: 10 }));
  }
  // Стойка микрофона и барный стул со стаканом воды.
  o.push(cyl("mic.base", [-0.78, 0.12, -0.5], [0.26, 0.02], "#1d1d22", { metalness: 0.7, roughness: 0.3 }));
  o.push(cyl("mic.pole", [-0.78, 0.14, -0.5], [0.022, 1.35], "#2a2a30", { metalness: 0.8, roughness: 0.3 }));
  o.push(mk("capsule", "mic.head", [-0.78, 1.47, -0.5], [0.05, 0.17], "#3a3a40", { rot: [20, 0, 0], metalness: 0.6, roughness: 0.4 }));
  o.push(sph("mic.grill", [-0.78, 1.6, -0.455], 0.055, "#9a9ca3", { metalness: 0.9, roughness: 0.35 }));
  o.push(cyl("stool.seat", [0.82, 0.86, -0.55], [0.36, 0.05], c.stool, { roughness: 0.5, segments: 24 }));
  o.push(cyl("stool.post", [0.82, 0.14, -0.55], [0.04, 0.72], "#2a2a30", { metalness: 0.8, roughness: 0.3 }));
  o.push(cyl("stool.base", [0.82, 0.12, -0.55], [0.34, 0.02], "#2a2a30", { metalness: 0.8, roughness: 0.3 }));
  o.push(mk("torus", "stool.ring", [0.82, 0.45, -0.55 - 0.13], [0.26, 0.26], "#2a2a30", { tube: 0.01, rot: [90, 0, 0], metalness: 0.8 }));
  o.push(cyl("stool.glass", [0.86 + (r() - 0.5) * 0.05, 0.91, -0.52], [0.07, 0.12], "#cfe6ff", { roughness: 0.05, metalness: 0.1, emissive: "#24303a", emissiveIntensity: 0.4 }));
  return {
    objects: o,
    env: { background: c.haze, fog: { color: c.haze, near: 5, far: 16 } },
    anchors: { hero: anchor([0, 0.12, 0], 0), focus: anchor([0, 1.2, 0]), mic: anchor([-0.78, 1.55, -0.5], 0), stool: anchor([0.82, 0.12, -0.55], 0, 0.91) },
    indoor: false,
  };
}

/** Студия: циклорама, по бокам — софтбоксы на треногах, золотой рефлектор, «яблочные» ящики, метка на полу, кабели. */
function studioCyc(_p: Record<string, any>, c: Record<string, string>): RoomOut {
  const o: Record<string, any>[] = [
    { id: "cyc", primitive: "cyclorama", position: [0, 0, -2.2], params: { width: 9, height: 4.5, depth: 6, radius: 1.4, color: c.sweep, roughness: 0.92 } },
  ];
  const tripod = (id: string, x: number, z: number, h: number) => {
    const t: Record<string, any>[] = [cyl(`${id}.pole`, [0, 0.35, 0], [0.035, h - 0.35], c.stand, { roughness: 0.4, metalness: 0.6 })];
    for (let i = 0; i < 3; i++) t.push(cyl(`${id}.leg${i}`, [0, 0.47, 0], [0.025, 0.55], c.stand, { rot: tiltRot(148, i * 120), roughness: 0.4, metalness: 0.6 }));
    return put(t, [x, 0, z], 0);
  };
  for (const [s, id] of [[-1, "softbox_l"], [1, "softbox_r"]] as [number, string][]) {
    o.push(...tripod(`${id}.stand`, s * 2.6, -0.6, 1.9));
    o.push(...put([
      rbox(`${id}`, [0, 0, 0], [0.75, 0.95, 0.12], "#f7f7f5", 0.03, { emissive: "#ffffff", emissiveIntensity: 1.2, noShadow: true }),
      cyl(`${id}.box`, [0, 0.475, -0.06], [0.95, 0.3], "#1d1e22", { top: 0.3, rot: [-90, 45, 0], segments: 4, roughness: 0.8 }),
    ], [s * 2.6, 1.75, -0.6], -s * 35));
  }
  // Рефлектор на треноге справа, стопка «яблочных» ящиков слева — на ровном полу (изгиб циклорамы — дальше z < −0.8).
  o.push(...tripod("reflector.stand", 1.35, -0.35, 1.0));
  o.push(cyl("reflector", [1.35, 1.0, -0.35 - 0.38], [0.8, 0.012], c.gold, { rot: [72, -35, 0], metalness: 0.9, roughness: 0.35, segments: 40 }));
  for (const [i, x, y, z, yaw, w, h, d] of [[0, -1.25, 0, -0.45, 12, 0.46, 0.2, 0.3], [1, -1.24, 0.2, -0.47, -7, 0.46, 0.1, 0.3], [2, -0.82, 0, -0.62, -20, 0.3, 0.46, 0.2]] as number[][]) {
    o.push(rbox(`appleboxes.${i}`, [x, y, z], [w, h, d], c.wood, 0.01, { rot: [0, yaw, 0], roughness: 0.7 }));
  }
  o.push(box("mark.a", [0, 0.001, 0.3], [0.3, 0.002, 0.04], c.tape, { rot: [0, 45, 0], roughness: 0.8 }));
  o.push(box("mark.b", [0, 0.001, 0.3], [0.3, 0.002, 0.04], c.tape, { rot: [0, -45, 0], roughness: 0.8 }));
  o.push(cable("cable.l", [[-2.6, 0.02, -0.6], [-1.9, 0.01, -0.2], [-1.0, 0.01, 0.6]], 0.0));
  o.push(cable("cable.r", [[2.6, 0.02, -0.6], [2.0, 0.01, -0.5], [1.4, 0.01, -0.35]], 0.0));
  return {
    objects: o,
    env: { background: c.sweep, fog: false },
    anchors: { hero: anchor([0, 0, 0], 0), focus: anchor([0, 1.1, 0]), mark: anchor([0, 0, 0.3], 0), softbox: anchor([-2.6, 2.2, -0.6], 35) },
    indoor: true,
  };
}

export const ROOMS: Record<string, RoomDef> = {
  void_stage: {
    meta: { name: "void_stage", title: "Пустая сцена с лучом", ru: "Чёрная пустота, двухъярусный круглый подиум со светящейся кромкой, рампа, бархатный занавес в дымке, позади — стойка микрофона и барный стул. Монолог, признание, стендап, «герой наедине с мыслью». Чистый подиум — hide: mic, stool.",
      params: ROOM_PARAMS, tags: ["stage"] },
    suggest: { light: "stage_spot", look: "anime_soft", camera: { move: "static", target: "hero:chest", distance: 4.0, height: 0.5, yaw: 12, fov: 40 } },
    palette: { floor: "#17161c", stage: "#24222b", rim: "#3a3646", glow: "#8a7cff", bulb: "#ffd59a", curtain: "#3a0f1c", curtain2: "#4a1424", stool: "#7a4a32", haze: "#0b0b10" },
    fn: voidStage,
  },
  studio_cyclorama: {
    meta: { name: "studio_cyclorama", title: "Студийная циклорама", ru: "Бесшовный белый (или цветной) фон-«бесконечность» студии: пол плавно переходит в стену; по бокам — софтбоксы на треногах, золотой рефлектор, ящики, метка на полу. Продукт, интервью, чистый показ героя.",
      params: ROOM_PARAMS, tags: ["studio"] },
    suggest: { light: "studio_3pt", look: "anime_soft", camera: { move: "static", target: "hero:chest", distance: 4.6, height: 0.6, yaw: 18, fov: 42 } },
    palette: { sweep: "#e9e6e1", floor: "#dedad3", stand: "#2b2d33", gold: "#e0b45a", wood: "#c49a6c", tape: "#ff6a3d" },
    fn: studioCyc,
  },
  ...HOME_ROOMS,
  ...CITY_ROOMS,
  ...NATURE_ROOMS,
};

export function buildRoomPreset(name: string, given: Record<string, any> = {}): RoomOut {
  const def = ROOMS[name];
  if (!def) throw new Error(`нет окружения «${name}»`);
  const p = withDefaults(def.meta, given);
  const colors = { ...def.palette, ...(given.colors || {}) };
  const out = def.fn(p, colors);
  // Вещи библиотеки моделей: вариант засеян seed окружения (другой seed — другая клавиатура, растение, монитор).
  for (const o of out.objects) if (o.model && !String(o.model).includes(".") && o.variantSeed === undefined) o.variantSeed = Number(p.seed) || 1;
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
