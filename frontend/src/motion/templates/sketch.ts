// Шаблоны: рисованное от руки (стрелка, обводка, подчёркивание) и курсор с касаниями для промо.
// Внешний вид — по мотивам блоков HyperFrames (HeyGen, Apache-2.0); код и пути свои (layers/sketch.ts).
import { interpolateKeys } from "../keys";
import type { KeyPoint, Layer } from "../types";
import { REF_H, REF_W } from "../types";
import { bool, dur, durationParam, layer, type MotionTemplate, num, RED, str, type TemplateParam, YELLOW } from "./kit";

/** Подписи от руки — рукописный шрифт с кириллицей из vendor/fonts. */
const HAND_FONT = "Caveat";

const roughParam: TemplateParam = { name: "rough", label: "Неровность руки (0 — ровно, 1 — сильно)", type: "number", default: 0.6, min: 0, max: 1 };
const boilParam: TemplateParam = { name: "boil", label: "Линия дрожит, как в мультике", type: "bool", default: false };
const drawParam = (d: number): TemplateParam => ({ name: "draw", label: "Рисуется, с", type: "number", default: d, min: 0.1, max: 3 });

/** Подпись от руки рядом с рисунком: печатается по буквам, как будто её дописывают. */
function handLabel(text: string, at: number, end: number, x: number, y: number, color: string, rotation: number): Layer {
  return layer(
    "text",
    {
      start: at,
      end,
      track: 2,
      x: Math.min(0.8, Math.max(0.2, x)),
      y: Math.min(0.92, Math.max(0.08, y)),
      rotation,
      name: "Подпись от руки",
      in: { type: "typewriter", duration: Math.min(0.9, 0.12 + text.length * 0.035) },
      out: { type: "fade", duration: 0.25 },
      shadow: { color: "#000000", blur: 14, x: 0, y: 4, opacity: 0.55 },
    },
    { text, font: HAND_FONT, weight: 700, size: 92, color, stroke: { color: "#ffffff", width: 7 }, maxWidth: 760, lineHeight: 0.95 },
  );
}

const ARROW_FROM: Record<string, [number, number]> = {
  "снизу слева": [-0.72, 0.7],
  "снизу справа": [0.72, 0.7],
  "сверху слева": [-0.72, -0.7],
  "сверху справа": [0.72, -0.7],
  слева: [-1, 0.12],
  справа: [1, 0.12],
};

export const handArrow: MotionTemplate = {
  id: "hand-arrow",
  fit: { anchor: "first" },
  name: "Стрелка от руки",
  category: "Акценты",
  description: "Маркерная стрелка сама рисуется к нужному месту, рядом подпись от руки — «вот здесь ошибка»",
  duration: 3,
  params: [
    { name: "x", label: "Куда показать: x", type: "number", default: 0.6, min: 0, max: 1 },
    { name: "y", label: "Куда показать: y", type: "number", default: 0.42, min: 0, max: 1 },
    { name: "from", label: "Откуда идёт стрелка", type: "select", default: "снизу слева", options: Object.keys(ARROW_FROM) },
    { name: "length", label: "Длина стрелки, px", type: "number", default: 380, min: 120, max: 900 },
    { name: "text", label: "Подпись (можно пустую)", type: "text", default: "Вот здесь ошибка" },
    { name: "color", label: "Цвет", type: "color", default: RED },
    { name: "width", label: "Толщина линии, px", type: "number", default: 16, min: 4, max: 40 },
    drawParam(0.6),
    roughParam,
    boilParam,
    durationParam(3),
  ],
  build(p, at) {
    const end = at + dur(p, 3);
    const tx = num(p, "x", 0.6) * REF_W;
    const ty = num(p, "y", 0.42) * REF_H;
    const [fx, fy] = ARROW_FROM[str(p, "from", "снизу слева")] ?? ARROW_FROM["снизу слева"];
    const len = Math.max(120, num(p, "length", 380));
    const k = len / Math.hypot(fx, fy);
    // Остриё не упирается в цель вплотную — 22 px зазора, как будто рука остановилась рядом.
    const gap = 22 / len;
    const tipX = tx + fx * k * gap;
    const tipY = ty + fy * k * gap;
    const tailX = tx + fx * k;
    const tailY = ty + fy * k;
    const angle = (Math.atan2(tipY - tailY, tipX - tailX) * 180) / Math.PI;
    const color = str(p, "color", RED);
    const draw = Math.max(0.1, num(p, "draw", 0.6));
    const width = num(p, "width", 16);
    const layers: Layer[] = [
      layer(
        "shape",
        {
          start: at,
          end,
          track: 1,
          x: (tipX + tailX) / 2 / REF_W,
          y: (tipY + tailY) / 2 / REF_H,
          rotation: Number(angle.toFixed(2)),
          name: "Стрелка от руки",
          in: { type: "draw", duration: draw },
          out: { type: "fade", duration: 0.25 },
          shadow: { color: "#000000", blur: 10, x: 0, y: 3, opacity: 0.45 },
        },
        // Изгиб наружу: стрелка приходит дугой, а не по линейке.
        { kind: "arrow", w: Math.round(len * (1 - gap)), fill: null, stroke: { color, width }, bend: Math.round(len * 0.12) * (fx < 0 ? 1 : -1), rough: num(p, "rough", 0.6), seed: 11, boil: bool(p, "boil", false) },
      ),
    ];
    const text = str(p, "text", "");
    if (text.trim()) {
      // Подпись у хвоста стрелки (под ним или над ним), целиком в кадре: ширина — на глаз,
      // у Caveat ~0.42 кегля на букву.
      const half = Math.min(760, text.length * 92 * 0.42) / 2 + 40;
      const lx = Math.min(REF_W - half, Math.max(half, tailX + fx * 40)) / REF_W;
      const ly = (tailY + (fy >= 0 ? 80 : -80)) / REF_H;
      layers.push(handLabel(text, at + draw * 0.7, end, lx, ly, color, fx < 0 ? -4 : 4));
    }
    return { layers };
  },
};

export const handCircle: MotionTemplate = {
  id: "hand-circle",
  fit: { anchor: "first" },
  name: "Обвести от руки",
  category: "Акценты",
  description: "Маркер обводит область неровным кругом, который заходит сам на себя; можно подписать",
  duration: 3,
  params: [
    { name: "x", label: "Центр области: x", type: "number", default: 0.5, min: 0, max: 1 },
    { name: "y", label: "Центр области: y", type: "number", default: 0.42, min: 0, max: 1 },
    { name: "w", label: "Ширина области, px", type: "number", default: 420, min: 60, max: 1000 },
    { name: "h", label: "Высота области, px", type: "number", default: 240, min: 40, max: 1400 },
    { name: "text", label: "Подпись под кругом (можно пустую)", type: "text", default: "" },
    { name: "color", label: "Цвет", type: "color", default: RED },
    { name: "width", label: "Толщина линии, px", type: "number", default: 14, min: 4, max: 40 },
    drawParam(0.7),
    roughParam,
    boilParam,
    durationParam(3),
  ],
  build(p, at) {
    const end = at + dur(p, 3);
    const x = num(p, "x", 0.5);
    const y = num(p, "y", 0.42);
    const w = num(p, "w", 420);
    const h = num(p, "h", 240);
    const color = str(p, "color", RED);
    const draw = Math.max(0.1, num(p, "draw", 0.7));
    const layers: Layer[] = [
      layer(
        "shape",
        {
          start: at,
          end,
          track: 1,
          x,
          y,
          rotation: -4,
          name: "Обводка от руки",
          in: { type: "draw", duration: draw },
          out: { type: "fade", duration: 0.25 },
          shadow: { color: "#000000", blur: 10, x: 0, y: 3, opacity: 0.45 },
        },
        // Круг чуть больше области: рука обводит с запасом, а не по краю.
        { kind: "ellipse", w: Math.round(w * 1.16 + 30), h: Math.round(h * 1.2 + 30), fill: null, stroke: { color, width: num(p, "width", 14) }, rough: num(p, "rough", 0.6), seed: 5, boil: bool(p, "boil", false) },
      ),
    ];
    const text = str(p, "text", "");
    if (text.trim()) layers.push(handLabel(text, at + draw * 0.8, end, x + 0.04, y + (h * 0.6 + 90) / REF_H, color, -3));
    return { layers };
  },
};

const UNDERLINE_KINDS = ["подчеркнуть", "дважды", "зачеркнуть", "маркером"];

export const handUnderline: MotionTemplate = {
  id: "hand-underline",
  fit: { anchor: "first" },
  name: "Подчеркнуть от руки",
  category: "Акценты",
  description: "Подчёркивание, двойная черта, зачёркивание каракулями или полупрозрачный маркер по слову",
  duration: 2.5,
  params: [
    { name: "x", label: "Середина слова: x", type: "number", default: 0.5, min: 0, max: 1 },
    { name: "y", label: "Линия по высоте: y", type: "number", default: 0.5, min: 0, max: 1 },
    { name: "w", label: "Длина, px", type: "number", default: 520, min: 60, max: 1040 },
    { name: "kind", label: "Как выделить", type: "select", default: "подчеркнуть", options: UNDERLINE_KINDS },
    { name: "color", label: "Цвет", type: "color", default: YELLOW },
    { name: "width", label: "Толщина линии, px", type: "number", default: 14, min: 4, max: 60 },
    drawParam(0.45),
    roughParam,
    durationParam(2.5),
  ],
  build(p, at) {
    const end = at + dur(p, 2.5);
    const x = num(p, "x", 0.5);
    const y = num(p, "y", 0.5);
    const w = num(p, "w", 520);
    const color = str(p, "color", YELLOW);
    const width = num(p, "width", 14);
    const draw = Math.max(0.1, num(p, "draw", 0.45));
    const rough = num(p, "rough", 0.6);
    const kind = str(p, "kind", "подчеркнуть");
    const base = { end, track: 1, x, in: { type: "draw" as const, duration: draw }, out: { type: "fade" as const, duration: 0.2 } };
    const line = (dy: number, k: number, start: number, seed: number, name: string): Layer =>
      layer(
        "shape",
        { ...base, start, y: y + dy / REF_H, rotation: -1.5 + seed * 0.4, name },
        { kind: "line", w: Math.round(w * k), fill: null, stroke: { color, width }, bend: -6, rough, seed },
      );
    if (kind === "дважды") return { layers: [line(0, 1, at, 1, "Подчёркивание"), line(width * 1.9, 0.86, at + draw * 0.7, 2, "Второе подчёркивание")] };
    if (kind === "зачеркнуть" || kind === "маркером") {
      const marker = kind === "маркером";
      return {
        layers: [
          layer(
            "shape",
            { ...base, start: at, y, rotation: marker ? -1.5 : -3, name: marker ? "Маркер" : "Зачёркивание" },
            {
              kind: "scribble",
              w: Math.round(w),
              h: marker ? Math.max(40, width * 4.5) : Math.max(36, width * 3.5),
              fill: null,
              // Маркер полупрозрачный — слово под ним читается; зачёркивание плотное.
              stroke: { color: marker ? `${color.slice(0, 7)}80` : color, width: marker ? Math.max(18, width * 2.4) : width },
              rough,
              seed: 3,
            },
          ),
        ],
      };
    }
    return { layers: [line(0, 1, at, 1, "Подчёркивание")] };
  },
};

/* ---------- курсор и касания ---------- */

type PathPoint = { x: number; y: number; t: number };

/** «0.3 0.6 0; 0.62 0.45 1.2» → точки пути (доли кадра и секунды от начала шаблона). */
export function parsePath(src: string): PathPoint[] {
  const out: PathPoint[] = [];
  for (const chunk of String(src || "").split(/[;\n]+/)) {
    const nums = chunk
      .trim()
      .split(/[\s,]+/)
      .map((s) => Number(s))
      .filter((v) => Number.isFinite(v));
    if (nums.length >= 3) out.push({ x: Math.min(1, Math.max(0, nums[0])), y: Math.min(1, Math.max(0, nums[1])), t: Math.max(0, nums[2]) });
  }
  out.sort((a, b) => a.t - b.t);
  return out;
}

/** «1.4, 2.9» → секунды касаний. */
export function parseTimes(src: string): number[] {
  return String(src || "")
    .split(/[\s,;]+/)
    .map((s) => Number(s))
    .filter((v) => Number.isFinite(v) && v >= 0)
    .sort((a, b) => a - b);
}

const DEFAULT_PATH = "0.30 0.66 0; 0.62 0.46 1.1; 0.62 0.46 1.6; 0.45 0.72 2.6";
const DEFAULT_TAPS = "1.3, 2.9";

function cursorTemplate(mode: "cursor" | "finger"): MotionTemplate {
  const finger = mode === "finger";
  const size0 = finger ? 104 : 84;
  return {
    id: finger ? "finger-taps" : "cursor-clicks",
    fit: { x: "frame", y: "frame" },
    name: finger ? "Касания пальцем" : "Курсор и клики",
    category: "Промо",
    description: finger
      ? "Кружок касания, как на записи экрана телефона: едет по точкам и нажимает с волной"
      : "Стрелка мыши едет по точкам и кликает: нажатие и расходящийся круг",
    duration: 4,
    params: [
      { name: "path", label: "Путь: x y секунда; …", type: "text", default: DEFAULT_PATH },
      { name: "taps", label: finger ? "Касания на секундах" : "Клики на секундах", type: "text", default: DEFAULT_TAPS },
      { name: "size", label: finger ? "Размер кружка, px" : "Размер курсора, px", type: "number", default: size0, min: 24, max: 240 },
      { name: "color", label: "Цвет волны", type: "color", default: "#ffffff" },
      durationParam(4),
    ],
    build(p, at) {
      const len = dur(p, 4);
      const end = at + len;
      const size = Math.max(24, num(p, "size", size0));
      let path = parsePath(str(p, "path", DEFAULT_PATH));
      if (!path.length) path = parsePath(DEFAULT_PATH);
      const taps = parseTimes(str(p, "taps", DEFAULT_TAPS)).filter((t) => t < len - 0.1);
      const color = str(p, "color", "#ffffff");
      const xs: KeyPoint[] = path.map((pt) => ({ t: Math.min(pt.t, len), v: pt.x, ease: "inOutCubic" }));
      const ys: KeyPoint[] = path.map((pt) => ({ t: Math.min(pt.t, len), v: pt.y, ease: "inOutCubic" }));
      // Курсор рисуется вокруг центра, а точка пути — его остриё: сдвиг на полрамки.
      const cw = finger ? size : Math.round(size * 0.633);
      const ch = size;
      const dx = finger ? 0 : cw / 2 / REF_W;
      const dy = finger ? 0 : ch / 2 / REF_H;
      const press: KeyPoint[] = [{ t: 0, v: 1 }];
      for (const t of taps) press.push({ t: Math.max(0, t - 0.08), v: 1 }, { t, v: finger ? 0.78 : 0.84, ease: "outBack" }, { t: t + 0.16, v: 1 });
      const pointer = layer(
        "shape",
        {
          start: at,
          end,
          track: 3,
          x: path[0].x + dx,
          y: path[0].y + dy,
          name: finger ? "Касание" : "Курсор",
          keys: {
            x: xs.map((k) => ({ ...k, v: Number((k.v + dx).toFixed(4)) })),
            y: ys.map((k) => ({ ...k, v: Number((k.v + dy).toFixed(4)) })),
            ...(taps.length ? { scale: press } : {}),
          },
          in: { type: finger ? "pop" : "fade", duration: 0.25 },
          out: { type: "fade", duration: 0.25 },
          shadow: { color: "#000000", blur: finger ? 18 : 10, x: 0, y: finger ? 4 : 3, opacity: 0.45 },
        },
        finger
          ? { kind: "ellipse", w: cw, h: ch, fill: "#ffffff66", stroke: { color: "#ffffff", width: Math.max(3, Math.round(size * 0.05)) } }
          : { kind: "cursor", w: cw, h: ch, fill: "#ffffff", stroke: { color: "#111111", width: Math.max(3, Math.round(size * 0.065)) } },
      );
      const layers: Layer[] = [pointer];
      for (const t of taps) {
        const px = interpolateKeys(xs, t, path[0].x);
        const py = interpolateKeys(ys, t, path[0].y);
        const ripple = finger ? size * 2.1 : size * 1.5;
        layers.push(
          layer(
            "shape",
            {
              start: at + t,
              end: Math.min(end, at + t + 0.6),
              track: 2,
              x: Number(px.toFixed(4)),
              y: Number(py.toFixed(4)),
              name: "Волна касания",
              keys: { scale: [{ t: 0, v: 0.25, ease: "outCubic" }, { t: 0.6, v: 1 }], opacity: [{ t: 0, v: 1, ease: "inQuad" }, { t: 0.6, v: 0 }] },
            },
            { kind: "ellipse", w: Math.round(ripple), h: Math.round(ripple), fill: null, stroke: { color, width: Math.max(4, Math.round(size * 0.07)) } },
          ),
          layer(
            "shape",
            {
              start: at + t,
              end: Math.min(end, at + t + 0.35),
              track: 2,
              x: Number(px.toFixed(4)),
              y: Number(py.toFixed(4)),
              name: "Вспышка касания",
              keys: { scale: [{ t: 0, v: 0.5, ease: "outCubic" }, { t: 0.35, v: 1.1 }], opacity: [{ t: 0, v: 0.7 }, { t: 0.35, v: 0 }] },
            },
            { kind: "ellipse", w: Math.round(size * 0.9), h: Math.round(size * 0.9), fill: `${color.slice(0, 7)}55`, stroke: null },
          ),
        );
      }
      return { layers };
    },
  };
}

export const cursorClicks = cursorTemplate("cursor");
export const fingerTaps = cursorTemplate("finger");
