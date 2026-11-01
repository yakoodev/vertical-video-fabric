/**
 * Шаблоны галереи на движке сцен: каждый — ОДИН слой `scene` с JSON-сценой. Сцену потом можно
 * править в инспекторе (JSON) или агентом (update_layer) — шаблон ничего не прячет.
 *
 * Единицы сцены — опорные px на плоскости z = 0 у камеры по умолчанию; y вверх.
 */
import { hashString, rand01 } from "../keys";
import type { Layer } from "../types";
import type { MotionTemplate, TemplateParam, TemplateParams } from "../templates";

type Scene = Record<string, unknown>;

const str = (p: TemplateParams, k: string, d: string) => (p[k] === undefined || p[k] === "" ? d : String(p[k]));
const num = (p: TemplateParams, k: string, d: number) => {
  const v = Number(p[k]);
  return p[k] === undefined || p[k] === "" || !Number.isFinite(v) ? d : v;
};
const bool = (p: TemplateParams, k: string, d: boolean) => (p[k] === undefined ? d : p[k] === true || p[k] === "true");
const dur = (p: TemplateParams, d: number) => Math.max(0.5, num(p, "duration", d));
const durationParam = (d: number): TemplateParam => ({ name: "duration", label: "Длительность, с", type: "number", default: d, min: 0.5, max: 60 });
const bgParam = (d: boolean): TemplateParam => ({ name: "background", label: "Свой фон", type: "bool", default: d });

const INK = "#140c2a";
const PINK = "#FF2D55";
const YELLOW = "#FFD400";
const CYAN = "#22D3EE";
const VIOLET = "#8B5CF6";

/** Слой-сцена: вьюпорт w×h опорных px с центром (x, y). */
function sceneLayer(newId: () => string, at: number, len: number, name: string, scene: Scene, box: { w?: number; h?: number; x?: number; y?: number } = {}): Layer {
  return {
    id: newId(),
    type: "scene",
    name,
    start: at,
    end: at + len,
    track: 1,
    x: box.x ?? 0.5,
    y: box.y ?? 0.5,
    scale: 1,
    rotation: 0,
    opacity: 1,
    in: { type: "fade", duration: 0.15 },
    out: { type: "fade", duration: 0.25 },
    props: { ...(box.w ? { w: box.w } : {}), ...(box.h ? { h: box.h } : {}), scene },
  } as Layer;
}

/** Фон-градиент сцены, если включён (иначе прозрачно поверх видео). */
const bgOf = (p: TemplateParams, on: boolean, a: string, b: string) => (bool(p, "background", on) ? { gradient: [a, b], angle: 90 } : null);

/* ---------- 1. изометрический заголовок ---------- */

function isoTitle(newId: () => string): MotionTemplate {
  return {
    id: "scene-iso-title",
    name: "3D-сцена · изометрический титр",
    category: "3D",
    description: "Объёмные буквы на плите в изометрии (ортокамера): буквы падают по одной с пружиной, кубики крутятся, камера медленно доворачивает",
    duration: 4,
    params: [
      { name: "text", label: "Текст", type: "text", default: "ЛЕВЕЛ АП" },
      { name: "color", label: "Акцент", type: "color", default: PINK },
      { name: "color2", label: "Плита", type: "color", default: CYAN },
      bgParam(false),
      durationParam(4),
    ],
    build(p, at) {
      const len = dur(p, 4);
      const text = str(p, "text", "ЛЕВЕЛ АП").slice(0, 14);
      const accent = str(p, "color", PINK);
      const slabW = Math.max(520, Math.min(1500, text.length * 105 + 160));
      const scene: Scene = {
        background: bgOf(p, false, "#1b1035", "#3a1c71"),
        camera: { type: "orthographic", zoom: Math.min(0.85, 820 / slabW), orbit: { azimuth: { keys: [[0, 18], [len, 30, "inOutQuad"]] }, elevation: 24 } },
        lights: [{ type: "ambient", intensity: 0.45 }, { type: "directional", direction: [-0.5, 1, 0.65], intensity: 0.85 }],
        style: { shading: "toon", bands: 3, outline: { color: INK, width: 5 } },
        nodes: [
          {
            type: "box",
            size: [slabW, 70, 300],
            position: [0, -70, 0],
            color: str(p, "color2", CYAN),
            // Плита под буквами: большая грань без z-буфера рисуется раньше всего, что на ней стоит.
            order: -1,
            enter: { from: { position: [0, -500, 0], opacity: 0 }, duration: 0.55, ease: "outBack" },
          },
          {
            type: "text3d",
            text,
            size: 150,
            depth: 46,
            position: [0, 52, 40],
            color: "#ffffff",
            sideColor: accent,
            material: { outline: { color: INK, width: 4 } },
            split: "letters",
            stagger: 0.06,
            enter: { from: { position: [0, 520, 0], opacity: 0 }, at: 0.25, spring: { stiffness: 230, damping: 14 } },
          },
          {
            type: "repeat",
            count: 2,
            spacing: [slabW + 120, 0, 0],
            position: [0, 0, -40],
            child: {
              type: "box",
              size: 90,
              color: YELLOW,
              position: [0, "=40 + 14*sin(t*3 + i*2)", 0],
              rotation: [0, "=t*80 + i*45", 0],
              enter: { from: { scale: 0 }, at: 0.6, duration: 0.45, ease: "outBack" },
            },
          },
        ],
      };
      return { layers: [sceneLayer(newId, at, len, "Изометрический титр", scene, { h: 980, y: 0.32 })] };
    },
  };
}

/* ---------- 2. вращающийся логотип ---------- */

function logoSpin(newId: () => string): MotionTemplate {
  return {
    id: "scene-logo-spin",
    name: "3D-сцена · вращающийся логотип",
    category: "3D",
    description: "Выдавленный значок (звезда, сердце, круг, многоугольник) крутится в toon-заливке с обводкой и контровым светом; подпись под ним",
    duration: 4,
    params: [
      { name: "shape", label: "Форма", type: "select", default: "star", options: ["star", "heart", "circle", "polygon", "arrow"] },
      { name: "text", label: "Подпись", type: "text", default: "НОВИНКА" },
      { name: "color", label: "Цвет", type: "color", default: YELLOW },
      bgParam(false),
      durationParam(4),
    ],
    build(p, at) {
      const len = dur(p, 4);
      const shape = str(p, "shape", "star");
      const color = str(p, "color", YELLOW);
      const scene: Scene = {
        background: bgOf(p, false, "#0f172a", "#312e81"),
        camera: { orbit: { elevation: 4, target: [0, 40, 0] }, zoom: 1.15 },
        lights: [
          { type: "ambient", intensity: 0.4 },
          { type: "directional", direction: [-0.6, 0.7, 0.8], intensity: 0.9 },
        ],
        style: { shading: "toon", bands: 3, outline: { color: INK, width: 6, crease: 35 } },
        nodes: [
          {
            type: "group",
            position: [0, 150, 0],
            enter: { from: { scale: 0, rotation: [0, -180, 0] }, spring: { stiffness: 160, damping: 12 } },
            children: [
              {
                type: "extrude",
                shape,
                radius: 260,
                sides: 6,
                spikes: 5,
                inner: 0.48,
                size: shape === "arrow" ? [560, 300] : undefined,
                depth: 90,
                color,
                // Пауза лицом к зрителю, оборот на 360° с перелётом — и снова (петля 2.4 с).
                rotation: [0, { keys: [[0, 0], [1.1, 0, "inOutBack"], [2.1, 360]], loop: 2.4 }, { osc: { amp: 6, freq: 0.5 } }],
                material: { rim: { color: "#ffffff", strength: 0.7 }, specular: { size: 0.05 } },
              },
            ],
          },
          {
            type: "text3d",
            text: str(p, "text", "НОВИНКА").slice(0, 18),
            size: 110,
            depth: 26,
            position: [0, -330, 0],
            color: "#ffffff",
            sideColor: color,
            material: { outline: { color: INK, width: 4 } },
            enter: { from: { position: [0, -140, 0], opacity: 0 }, at: 0.35, duration: 0.5, ease: "outBack" },
          },
          { type: "particles", kind: "float", count: 40, area: [1000, 1100, 500], size: 12, colors: ["#ffffff", color], speed: 50 },
        ],
      };
      return { layers: [sceneLayer(newId, at, len, "Логотип 3D", scene, { h: 1200, y: 0.4 })] };
    },
  };
}

/* ---------- 3. 2D-параллакс: пролёт камеры ---------- */

/** Гряда гор многоугольником: ширина w, высота h, зубцы по зерну. */
function ridge(seed: string, w: number, h: number, peaks: number): number[][] {
  const s = hashString(seed);
  const pts: number[][] = [[-w / 2, -h]];
  for (let i = 0; i <= peaks * 2; i++) {
    const x = -w / 2 + (w * i) / (peaks * 2);
    const top = i % 2 === 1;
    const y = top ? h * (0.55 + 0.45 * rand01(s, i)) : h * (0.15 + 0.25 * rand01(s, i));
    pts.push([Math.round(x), Math.round(y)]);
  }
  pts.push([w / 2, -h]);
  return pts;
}

function parallax2d(newId: () => string): MotionTemplate {
  return {
    id: "scene-parallax-2d",
    name: "3D-сцена · 2D-параллакс, пролёт",
    category: "3D",
    description: "Плоская иллюстрация слоями на разной глубине (горы, холмы, облака, солнце): камера пролетает вбок и вперёд — настоящий параллакс; титр спереди",
    duration: 5,
    params: [
      { name: "text", label: "Титр", type: "text", default: "ПУТЕШЕСТВИЕ" },
      { name: "sky", label: "Небо", type: "color", default: "#FF9A62" },
      { name: "color", label: "Горы", type: "color", default: "#4C1D95" },
      durationParam(5),
    ],
    build(p, at) {
      const len = dur(p, 5);
      const sky = str(p, "sky", "#FF9A62");
      const hills = str(p, "color", "#4C1D95");
      const flat = { shading: "flat" };
      const cloud = (x: number, y: number, z: number, s: number) => ({
        type: "group",
        position: [x, y, z],
        scale: s,
        children: [
          { type: "circle", radius: 90, fill: "#ffffff" },
          { type: "circle", radius: 70, position: [-100, -20, 0], fill: "#ffffff" },
          { type: "circle", radius: 75, position: [105, -15, 0], fill: "#ffffff" },
          { type: "rect", size: [300, 70], position: [0, -55, 0], cornerRadius: 35, fill: "#ffffff" },
        ],
        material: { opacity: 0.9 },
      });
      const scene: Scene = {
        background: { gradient: ["#FFD29D", sky, "#7C3AED"], angle: 90 },
        camera: {
          position: [{ keys: [[0, -380], [len, 380, "linear"]] }, 40, { keys: [[0, 2637], [len, 2150, "inOutQuad"]] }],
          target: [{ keys: [[0, -300], [len, 300, "linear"]] }, 60, -1000],
        },
        fog: { color: "#FFB78A", near: 2600, far: 8500, max: 0.75 },
        style: flat,
        nodes: [
          { type: "circle", radius: 320, position: [420, 780, -5200], fill: "#FFF3B0", order: -2 },
          { type: "polygon", points: ridge("far", 9000, 1500, 7), position: [0, -260, -5000], fill: hills, order: -1 },
          cloud(-900, 900, -3600, 2.4),
          cloud(1500, 1150, -3900, 2.8),
          { type: "polygon", points: ridge("mid", 6500, 900, 6), position: [0, -620, -2600], fill: "#6D28D9" },
          cloud(-300, 640, -1500, 1.1),
          { type: "polygon", points: ridge("near", 4400, 520, 5), position: [0, -900, -900], fill: "#2E1065" },
          {
            type: "repeat",
            count: 7,
            spacing: [520, 0, 0],
            position: [0, -760, -420],
            jitter: { position: [140, 40, 0], scale: 0.25 },
            seed: "trees",
            child: { type: "polygon", sides: 3, radius: 120, scale: [0.8, 1.6, 1], fill: "#1E0B3B" },
          },
          {
            type: "text",
            text: str(p, "text", "ПУТЕШЕСТВИЕ").slice(0, 24),
            size: Math.min(120, Math.floor(1500 / Math.max(4, str(p, "text", "ПУТЕШЕСТВИЕ").length))),
            weight: 900,
            position: [{ keys: [[0, -380], [len, 380, "linear"]] }, 560, 0],
            color: "#ffffff",
            stroke: { color: "#2E1065", width: 10 },
            material: { fog: false },
            enter: { from: { position: [0, -120, 0], opacity: 0 }, at: 0.3, duration: 0.7, ease: "outCubic" },
          },
        ],
      };
      return { layers: [sceneLayer(newId, at, len, "Параллакс", scene)] };
    },
  };
}

/* ---------- 4. низкополигональная сцена с облётом ---------- */

function lowPoly(newId: () => string): MotionTemplate {
  return {
    id: "scene-lowpoly-orbit",
    name: "3D-сцена · low-poly остров, облёт",
    category: "3D",
    description: "Гранёный остров: ёлки, камни и домик на плоской заливке с туманом; камера облетает по кругу",
    duration: 6,
    params: [
      { name: "color", label: "Трава", type: "color", default: "#65A30D" },
      { name: "sky", label: "Небо", type: "color", default: "#7DD3FC" },
      { name: "speed", label: "Скорость облёта, °/с", type: "number", default: 24, min: 0, max: 120 },
      durationParam(6),
    ],
    build(p, at) {
      const len = dur(p, 6);
      const sky = str(p, "sky", "#7DD3FC");
      const tree = {
        type: "group",
        children: [
          { type: "cylinder", radius: 16, height: 90, segments: 5, position: [0, 45, 0], color: "#7C4A21" },
          { type: "cone", radius: 75, height: 170, segments: 6, position: [0, 160, 0], color: "#166534" },
          { type: "cone", radius: 55, height: 120, segments: 6, position: [0, 240, 0], color: "#15803D" },
        ],
      };
      const scene: Scene = {
        background: { gradient: ["#E0F2FE", sky], angle: 90 },
        camera: { orbit: { azimuth: { base: 20, rate: num(p, "speed", 24) }, elevation: 26, distance: 3900, target: [0, 60, 0] } },
        lights: [
          { type: "hemisphere", sky: "#ffffff", ground: "#4a3b2a", intensity: 0.55 },
          { type: "directional", direction: [-0.4, 0.9, 0.35], intensity: 0.85, color: "#FFF4D6" },
        ],
        fog: { color: sky, near: 2300, far: 5200, max: 0.55 },
        style: { shading: "lambert", smooth: false },
        nodes: [
          // Земля и трава — подложка: рисуются раньше ёлок и домика (order −1), иначе большая грань
          // травы по глубине центра легла бы поверх того, что на ней стоит.
          { type: "cylinder", radiusTop: 720, radiusBottom: 520, height: 160, segments: 9, position: [0, -80, 0], color: "#8B5A2B", order: -1 },
          { type: "cylinder", radius: 735, height: 24, segments: 9, position: [0, 6, 0], color: str(p, "color", "#65A30D"), order: -1 },
          { type: "repeat", count: 9, layout: "circle", radius: 470, face: "none", jitter: { position: [90, 0, 90], scale: 0.3 }, seed: "forest", child: tree },
          {
            type: "group",
            position: [-120, 18, -60],
            rotation: [0, 25, 0],
            children: [
              { type: "box", size: [200, 150, 170], position: [0, 75, 0], color: "#F5E6C8" },
              { type: "cone", radius: 160, height: 110, segments: 4, position: [0, 205, 0], rotation: [0, 45, 0], color: "#B91C1C" },
              { type: "box", size: [40, 70, 4], position: [0, 35, 86], color: "#5B3A1E" },
            ],
          },
          { type: "repeat", count: 5, layout: "random", area: [800, 0, 800], seed: "rocks", child: { type: "icosphere", radius: 45, detail: 0, position: [0, 20, 0], scale: [1.4, 0.8, 1], color: "#94A3B8" } },
          { type: "icosphere", radius: 120, detail: 1, position: [700, 900, -1400], color: "#FDE047", material: { shading: "flat" } },
          {
            type: "repeat",
            count: 3,
            layout: "line",
            spacing: [520, 60, 300],
            position: [0, 820, -700],
            child: {
              type: "group",
              position: [{ rate: 30 }, 0, 0],
              children: [
                { type: "icosphere", radius: 90, detail: 1, color: "#ffffff", material: { shading: "flat" } },
                { type: "icosphere", radius: 70, detail: 1, position: [100, -15, 0], color: "#ffffff", material: { shading: "flat" } },
              ],
            },
          },
        ],
      };
      return { layers: [sceneLayer(newId, at, len, "Low-poly остров", scene)] };
    },
  };
}

/* ---------- 5. кинетический 3D-текст ---------- */

function kineticText(newId: () => string): MotionTemplate {
  return {
    id: "scene-kinetic-text",
    name: "3D-сцена · кинетический текст",
    category: "3D",
    description: "Буквы влетают переворотом по одной (пружина), потом дышат волной; вторая строка выскакивает по словам",
    duration: 4,
    params: [
      { name: "text", label: "Строка 1", type: "text", default: "СМОТРИ" },
      { name: "text2", label: "Строка 2", type: "text", default: "ДО КОНЦА" },
      { name: "color", label: "Акцент", type: "color", default: YELLOW },
      bgParam(false),
      durationParam(4),
    ],
    build(p, at) {
      const len = dur(p, 4);
      const accent = str(p, "color", YELLOW);
      const scene: Scene = {
        background: bgOf(p, false, "#111827", "#4C1D95"),
        camera: { orbit: { azimuth: { wiggle: { amp: 7, freq: 0.35 } }, elevation: { base: 6, wiggle: { amp: 4, freq: 0.3, seed: 2 } } } },
        style: { shading: "flat" },
        nodes: [
          {
            type: "text3d",
            text: str(p, "text", "СМОТРИ").slice(0, 12),
            size: 190,
            depth: 50,
            position: [0, 130, 0],
            color: "#ffffff",
            sideColor: VIOLET,
            material: { outline: { color: INK, width: 5 } },
            split: "letters",
            stagger: 0.07,
            enter: { from: { rotation: [-100, 0, 0], position: [0, -60, 200], opacity: 0 }, spring: { stiffness: 200, damping: 13 } },
            letters: { position: [0, "=16*sin(t*3.2 - i*0.7)", 0], rotation: [0, "=8*sin(t*2 - i*0.5)", 0] },
          },
          {
            type: "text3d",
            text: str(p, "text2", "ДО КОНЦА").slice(0, 20),
            size: 120,
            depth: 30,
            position: [0, -110, 0],
            color: accent,
            sideColor: "#B45309",
            material: { outline: { color: INK, width: 4 } },
            split: "words",
            stagger: 0.14,
            enter: { from: { scale: 0, rotation: [0, 0, -12] }, at: 0.55, duration: 0.45, ease: "outBack" },
          },
        ],
      };
      return { layers: [sceneLayer(newId, at, len, "Кинетический текст", scene, { h: 900, y: 0.36 })] };
    },
  };
}

/* ---------- 6. конфетти в 3D ---------- */

function confetti3d(newId: () => string): MotionTemplate {
  return {
    id: "scene-confetti-3d",
    name: "3D-сцена · конфетти и титр",
    category: "3D",
    description: "Настоящие 3D-листики конфетти переворачиваются и блестят, вспышка-разлёт на старте, титр выпрыгивает с пружиной",
    duration: 4,
    params: [
      { name: "text", label: "Титр", type: "text", default: "ПОБЕДА!" },
      { name: "color", label: "Цвет титра", type: "color", default: YELLOW },
      { name: "count", label: "Сколько конфетти", type: "number", default: 160, min: 10, max: 400 },
      bgParam(false),
      durationParam(4),
    ],
    build(p, at) {
      const len = dur(p, 4);
      const color = str(p, "color", YELLOW);
      const colors = [color, PINK, CYAN, "#A3E635", "#ffffff", VIOLET];
      const scene: Scene = {
        background: bgOf(p, false, "#0B1020", "#1E1B4B"),
        camera: { shake: { amount: { keys: [[0, 14], [0.6, 0, "outCubic"]] }, freq: 9 } },
        lights: [{ type: "ambient", intensity: 0.55 }, { type: "directional", direction: [-0.3, 0.6, 1], intensity: 0.9 }],
        nodes: [
          { type: "particles", kind: "confetti", count: Math.round(num(p, "count", 160)), area: [1300, 2000, 900], size: 34, colors, speed: 300, spin: 420 },
          { type: "particles", kind: "burst", count: 70, colors, size: 30, speed: 1500, at: 0.05, life: 1.4, position: [0, 80, 100] },
          {
            type: "text3d",
            text: str(p, "text", "ПОБЕДА!").slice(0, 14),
            size: Math.min(150, Math.floor(1300 / Math.max(4, str(p, "text", "ПОБЕДА!").length))),
            depth: 46,
            position: [0, 80, 200],
            color,
            sideColor: "#B45309",
            material: { shading: "flat", outline: { color: INK, width: 6 } },
            rotation: [0, { osc: { amp: 8, freq: 0.4 } }, 0],
            enter: { from: { scale: 0 }, spring: { stiffness: 260, damping: 11 } },
          },
        ],
      };
      return { layers: [sceneLayer(newId, at, len, "Конфетти 3D", scene)] };
    },
  };
}

/* ---------- 7. карточка-переворот ---------- */

function cardFlip(newId: () => string): MotionTemplate {
  return {
    id: "scene-card-flip",
    name: "3D-сцена · карточка-переворот",
    category: "3D",
    description: "Толстая карточка влетает и переворачивается: на лицевой стороне вопрос/«до», на обороте ответ/«после»",
    duration: 4,
    params: [
      { name: "front", label: "Лицо", type: "text", default: "ДО" },
      { name: "back", label: "Оборот", type: "text", default: "ПОСЛЕ" },
      { name: "flip", label: "Переворот на, с", type: "number", default: 1.4, min: 0.2, max: 30 },
      { name: "color", label: "Лицо", type: "color", default: "#1E293B" },
      { name: "color2", label: "Оборот", type: "color", default: PINK },
      durationParam(4),
    ],
    build(p, at) {
      const len = dur(p, 4);
      const flipAt = Math.max(0.2, Math.min(len - 0.6, num(p, "flip", 1.4)));
      const W = 640;
      const H = 840;
      const D = 30;
      const face = (text: string, fill: string, back: boolean) => ({
        type: "group",
        position: [0, 0, back ? -D / 2 - 1 : D / 2 + 1],
        rotation: [0, back ? 180 : 0, 0],
        children: [
          { type: "rect", size: [W - 44, H - 44], cornerRadius: 36, fill, material: { doubleSided: false } },
          { type: "text", text, size: Math.min(170, Math.floor((W - 130) / (Math.max(1, Array.from(text).length) * 0.74))), weight: 900, color: "#ffffff", position: [0, 0, 1] },
        ],
      });
      const scene: Scene = {
        background: null,
        camera: { orbit: { elevation: 5 } },
        lights: [{ type: "ambient", intensity: 0.5 }, { type: "directional", direction: [-0.5, 0.6, 1], intensity: 0.8 }],
        style: { shading: "toon", bands: 2, outline: { color: INK, width: 5 } },
        nodes: [
          {
            type: "group",
            enter: { from: { position: [0, -1400, 0], rotation: [30, 0, 0] }, spring: { stiffness: 150, damping: 15 } },
            rotation: [0, { keys: [[0, 0], [flipAt, 0, "outBack"], [flipAt + 0.75, 180]] }, { osc: { amp: 2.5, freq: 0.5 } }],
            children: [
              { type: "box", size: [W, H, D], color: "#F8FAFC" },
              face(str(p, "front", "ДО").slice(0, 14), str(p, "color", "#1E293B"), false),
              face(str(p, "back", "ПОСЛЕ").slice(0, 14), str(p, "color2", PINK), true),
            ],
          },
        ],
      };
      return { layers: [sceneLayer(newId, at, len, "Карточка-переворот", scene, { h: 1300, y: 0.42 })] };
    },
  };
}

/* ---------- 8. концовка «Подпишись» ---------- */

function subscribe3d(newId: () => string): MotionTemplate {
  return {
    id: "scene-subscribe",
    name: "3D-сцена · концовка «Подпишись»",
    category: "3D",
    description: "Объёмная кнопка выпрыгивает, колокольчик качается, на нажатии кнопка проседает и становится «Вы подписаны»; ник канала снизу",
    duration: 4,
    params: [
      { name: "handle", label: "Канал", type: "text", default: "@channel" },
      { name: "press", label: "Нажатие на, с", type: "number", default: 1.8, min: 0.3, max: 30 },
      { name: "color", label: "Кнопка", type: "color", default: "#FF0033" },
      bgParam(false),
      durationParam(4),
    ],
    build(p, at) {
      const len = dur(p, 4);
      const press = Math.max(0.3, Math.min(len - 0.3, num(p, "press", 1.8)));
      const red = str(p, "color", "#FF0033");
      const scene: Scene = {
        background: bgOf(p, false, "#0F0F1A", "#2A1846"),
        camera: { orbit: { elevation: 3, target: [0, 40, 0] } },
        lights: [{ type: "ambient", intensity: 0.5 }, { type: "directional", direction: [-0.5, 0.8, 0.9], intensity: 0.85 }],
        style: { shading: "toon", bands: 3, outline: { color: INK, width: 6 } },
        nodes: [
          {
            type: "group",
            position: [0, 0, 0],
            enter: { from: { scale: 0 }, at: 0.1, spring: { stiffness: 220, damping: 12 } },
            scale: [1, { keys: [[press - 0.05, 1], [press + 0.06, 0.86, "outBack"], [press + 0.4, 1]] }, 1],
            children: [
              {
                type: "extrude",
                shape: "roundrect",
                size: [840, 200],
                cornerRadius: 100,
                depth: 60,
                color: { keys: [[press, red, "step"], [press + 0.06, "#3F3F46"]] },
                material: { rim: { color: "#ffffff", strength: 0.5 }, specular: { size: 0.03 } },
              },
              { type: "text", text: "ПОДПИСАТЬСЯ", size: 68, weight: 900, color: "#ffffff", position: [0, 0, 32], end: press + 0.06 },
              { type: "text", text: "ВЫ ПОДПИСАНЫ", size: 64, weight: 900, color: "#ffffff", position: [0, 0, 32], start: press + 0.06 },
            ],
          },
          {
            type: "group",
            position: [440, 210, 40],
            rotation: [0, 0, { osc: { amp: 18, freq: 1.4 } }],
            pivot: [0, 120, 0],
            enter: { from: { scale: 0 }, at: 0.35, duration: 0.4, ease: "outBack" },
            children: [
              { type: "lathe", points: [[110, -60], [100, -40], [70, 0], [60, 60], [45, 100], [20, 118], [0, 122]], segments: 24, color: YELLOW, material: { specular: { size: 0.04 } } },
              { type: "sphere", radius: 26, position: [0, -70, 0], segments: 12, color: "#F59E0B" },
            ],
          },
          {
            type: "text3d",
            text: str(p, "handle", "@channel").slice(0, 24),
            size: 92,
            depth: 22,
            position: [0, -230, 0],
            color: "#ffffff",
            sideColor: VIOLET,
            material: { shading: "flat", outline: { color: INK, width: 4 } },
            enter: { from: { position: [0, -80, 0], opacity: 0 }, at: 0.5, duration: 0.45, ease: "outCubic" },
          },
          { type: "particles", kind: "burst", count: 50, colors: [YELLOW, red, "#ffffff"], size: 26, speed: 1300, at: press + 0.05, life: 1.2 },
        ],
      };
      return { layers: [sceneLayer(newId, at, len, "Подпишись 3D", scene, { h: 900, y: 0.62 })] };
    },
  };
}

/** Шаблоны сцен для галереи (templates.ts); `newId` — генератор id слоёв галереи. */
export function sceneTemplates(newId: () => string): MotionTemplate[] {
  return [isoTitle(newId), logoSpin(newId), parallax2d(newId), lowPoly(newId), kineticText(newId), confetti3d(newId), cardFlip(newId), subscribe3d(newId)];
}
