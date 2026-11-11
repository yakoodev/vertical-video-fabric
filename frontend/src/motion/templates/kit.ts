// Кирпичики шаблонов графики: типы, новый слой, цвета, чтение параметров, искры и утилиты данных.
import type { CameraKey, Layer, LayerBase, LayerPropsMap, LayerType, Shadow } from "../types";

export type TemplateParamType = "text" | "number" | "color" | "emoji" | "select" | "bool" | "sound";

export type TemplateParam = {
  name: string;
  label: string;
  type: TemplateParamType;
  default: string | number | boolean;
  options?: string[];
  min?: number;
  max?: number;
};

export type TemplateParams = Record<string, string | number | boolean | undefined>;

export type TemplateResult = {
  layers: Layer[];
  /** Предложение для камеры (тряска на «бум»): редактор вливает его в `render.camera`. */
  camera?: CameraKey[];
};

export type MotionTemplate = {
  id: string;
  name: string;
  category: string;
  description: string;
  /** Длительность по умолчанию, с. */
  duration: number;
  params: TemplateParam[];
  build(params: TemplateParams, at: number): TemplateResult;
  /**
   * Как шаблон встаёт в кадр другого формата (`fitTemplate`), по осям: "group" (по умолчанию) —
   * весь шаблон одной группой: взаимное расположение в px, масштаб — чтобы влез, место —
   * та же доля кадра; "frame" — каждый слой на своей доле кадра (мем-подпись сверху и снизу,
   * лента у левого края). `anchor: "first"` — на своей доле стоит первый слой (цель стрелки).
   */
  fit?: { x?: "group" | "frame"; y?: "group" | "frame"; anchor?: "box" | "first" };
};

export const TEMPLATE_CATEGORIES = ["Титры", "Плашки", "Инфографика", "Призывы", "Акценты", "Списки", "Мемы", "Промо", "Реклама", "Эффекты", "3D"] as const;

/* ---------- кирпичики ---------- */

let idCounter = 0;

/** Новый id слоя по правилам спеки (^[A-Za-z0-9_-]{1,32}$). Случайность тут ок — это не кадр. */
export function newLayerId(): string {
  idCounter = (idCounter + 1) % 1296;
  const rnd = Math.floor(Math.random() * 36 ** 5).toString(36).padStart(5, "0");
  return `l_${rnd}${idCounter.toString(36).padStart(2, "0")}`;
}

type Base = Partial<Omit<LayerBase, "id">> & { start: number; end: number };

export function layer<T extends LayerType>(type: T, base: Base, props: LayerPropsMap[T]): Layer {
  return {
    id: newLayerId(),
    track: 0,
    x: 0.5,
    y: 0.5,
    scale: 1,
    rotation: 0,
    opacity: 1,
    ...base,
    type,
    props,
  } as Layer;
}

export const VIOLET = "#8B5CF6";
export const VIOLET_DEEP = "#5B21B6";
export const YELLOW = "#FFD400";
export const RED = "#FF2D55";
export const FONT = "Montserrat";

export const SOFT: Shadow = { color: "#000000", blur: 28, x: 0, y: 10, opacity: 0.55 };
export const glow = (color: string, blur = 40, opacity = 0.85): Shadow => ({ color, blur, x: 0, y: 0, opacity });

export const str = (p: TemplateParams, k: string, d: string) => (p[k] === undefined || p[k] === "" ? d : String(p[k]));
export const num = (p: TemplateParams, k: string, d: number) => {
  const v = Number(p[k]);
  return p[k] === undefined || p[k] === "" || !Number.isFinite(v) ? d : v;
};
export const bool = (p: TemplateParams, k: string, d: boolean) => (p[k] === undefined ? d : p[k] === true || p[k] === "true");

export const dur = (p: TemplateParams, d: number) => Math.max(0.5, num(p, "duration", d));
export const durationParam = (d: number): TemplateParam => ({ name: "duration", label: "Длительность, с", type: "number", default: d, min: 0.5, max: 60 });

/** Искры вокруг заголовка: маленькие звёзды с разными задержками и миганием. */
export function sparkles(at: number, end: number, cx: number, cy: number, color: string): Layer[] {
  const spots: [number, number, number][] = [
    [-0.36, -0.07, 54],
    [0.38, -0.09, 40],
    [0.4, 0.07, 30],
    [-0.4, 0.08, 34],
  ];
  return spots.map(([dx, dy, s], i) =>
    layer(
      "shape",
      {
        start: at + 0.12 + i * 0.07,
        end,
        track: 1,
        x: cx + dx,
        y: cy + dy,
        rotation: i * 17,
        in: { type: "pop", duration: 0.35 },
        out: { type: "fade", duration: 0.2 },
        loop: { type: i % 2 ? "blink" : "pulse", speed: 0.8 + i * 0.15, amount: 1 },
        shadow: glow(color, 24, 0.9),
        name: "Искра",
      },
      { kind: "star", w: s, h: s, points: 4, fill: i % 2 ? "#ffffff" : color, stroke: null },
    ),
  );
}

/* ---------- утилиты ---------- */

export function parseData(labels: string, values: string) {
  const ls = labels.split(",").map((s) => s.trim());
  const vs = values.split(",").map((s) => Number(s.trim().replace(",", ".")));
  const n = Math.min(12, Math.max(ls.length, vs.length));
  return Array.from({ length: n }, (_, i) => ({ label: ls[i] ?? "", value: Number.isFinite(vs[i]) ? vs[i] : 0 }));
}

/** n цветов плавно от `a` к `b` — столбцы одного тона, светлеющие к росту. */
export function ramp(a: string, b: string, n: number): string[] {
  const pa = parseInt(a.slice(1, 7), 16);
  const pb = parseInt(b.slice(1, 7), 16);
  return Array.from({ length: Math.max(1, n) }, (_, i) => {
    const k = n > 1 ? i / (n - 1) : 1;
    const ch = (sh: number) => Math.round(((pa >> sh) & 255) + (((pb >> sh) & 255) - ((pa >> sh) & 255)) * k);
    return `#${[16, 8, 0].map((sh) => ch(sh).toString(16).padStart(2, "0")).join("")}`;
  });
}

/** Осветлить (k > 0) или затемнить (k < 0) hex-цвет — для пар градиентов. */
export function shadeHex(color: string, k: number): string {
  const m = /^#([0-9a-f]{6})/i.exec(color);
  if (!m) return color;
  const v = parseInt(m[1], 16);
  const t = k > 0 ? 255 : 0;
  const ch = (c: number) => Math.round(c + (t - c) * Math.abs(k)).toString(16).padStart(2, "0");
  return `#${ch((v >> 16) & 255)}${ch((v >> 8) & 255)}${ch(v & 255)}`;
}
