// Шаблоны: призывы и реакции (подписка, «смотри сюда», до/после, тревога, бум).
import type { CameraKey, Layer } from "../types";
import { bool, dur, durationParam, FONT, glow, layer, type MotionTemplate, num, RED, SOFT, str, VIOLET, YELLOW } from "./kit";

export const subscribe: MotionTemplate = {
  id: "subscribe",
  name: "Подписка",
  category: "Призывы",
  description: "Красная кнопка «Подписаться» с колокольчиком и нажатием курсора",
  duration: 3.5,
  params: [
    { name: "text", label: "Текст", type: "text", default: "Подписаться" },
    { name: "color", label: "Цвет кнопки", type: "color", default: "#FF1F4B" },
    { name: "y", label: "Высота", type: "number", default: 0.72, min: 0, max: 1 },
    durationParam(3.5),
  ],
  build(p, at) {
    const end = at + dur(p, 3.5);
    const y = num(p, "y", 0.72);
    const color = str(p, "color", "#FF1F4B");
    const click = 1.1;
    const pressKeys = {
      scale: [
        { t: click - 0.08, v: 1 },
        { t: click, v: 0.92, ease: "outBack" as const },
        { t: click + 0.18, v: 1 },
      ],
    };
    const common = { in: { type: "pop" as const, duration: 0.45 }, out: { type: "zoom" as const, duration: 0.25 } };
    return {
      layers: [
        layer(
          "text",
          { start: at, end, y, name: "Кнопка", ...common, keys: pressKeys, shadow: glow(color, 46, 0.75) },
          {
            text: str(p, "text", "Подписаться"),
            font: FONT,
            weight: 900,
            size: 74,
            color: "#ffffff",
            uppercase: true,
            bg: { color, opacity: 1, radius: 40, padX: 46, padY: 30, gradient: [color, "#FF5A3C"] },
          },
        ),
      ],
    };
  },
};

export const lookHere: MotionTemplate = {
  id: "look-here",
  fit: { anchor: "first" },
  name: "Стрелка «смотри»",
  category: "Акценты",
  description: "Круг обводится вокруг цели, к нему тянется изогнутая стрелка с подписью",
  duration: 3,
  params: [
    { name: "x", label: "Цель: x", type: "number", default: 0.62, min: 0, max: 1 },
    { name: "y", label: "Цель: y", type: "number", default: 0.32, min: 0, max: 1 },
    { name: "text", label: "Подпись", type: "text", default: "Смотри!" },
    { name: "color", label: "Цвет", type: "color", default: RED },
    durationParam(3),
  ],
  build(p, at) {
    const end = at + dur(p, 3);
    const x = num(p, "x", 0.62);
    const y = num(p, "y", 0.32);
    const color = str(p, "color", RED);
    // Стрелка приходит снизу-слева (или снизу-справа, если цель у левого края).
    const fromLeft = x > 0.4;
    const ax = x + (fromLeft ? -0.2 : 0.2);
    const ay = y + 0.13;
    const angle = fromLeft ? -50 : -130;
    return {
      layers: [
        layer(
          "shape",
          { start: at, end, x, y, rotation: -8, name: "Круг", in: { type: "draw", duration: 0.5 }, out: { type: "fade", duration: 0.25 }, shadow: glow(color, 26, 0.7) },
          { kind: "ellipse", w: 330, h: 270, fill: null, stroke: { color, width: 14 } },
        ),
        layer(
          "shape",
          {
            start: at + 0.25,
            end,
            x: ax,
            y: ay,
            rotation: angle,
            name: "Стрелка",
            in: { type: "draw", duration: 0.45 },
            out: { type: "fade", duration: 0.25 },
            loop: { type: "float", speed: 1.2, amount: 0.6 },
            shadow: glow(color, 26, 0.7),
          },
          { kind: "arrow", w: 300, fill: null, stroke: { color, width: 20 }, bend: fromLeft ? 60 : -60 },
        ),
        layer(
          "text",
          {
            start: at + 0.45,
            end,
            track: 1,
            x: Math.min(0.8, Math.max(0.2, ax + (fromLeft ? -0.08 : 0.08))),
            y: ay + 0.12,
            rotation: -6,
            name: "Подпись",
            in: { type: "pop", duration: 0.35 },
            out: { type: "fade", duration: 0.2 },
            shadow: SOFT,
          },
          { text: str(p, "text", "Смотри!"), font: FONT, weight: 900, size: 84, color: "#ffffff", stroke: { color, width: 10 }, uppercase: true },
        ),
      ],
    };
  },
};

export const beforeAfter: MotionTemplate = {
  id: "before-after",
  // Подписи у верха кадра, ручка — на середине разделителя во всю высоту.
  fit: { x: "frame", y: "frame" },
  name: "ДО / ПОСЛЕ",
  category: "Акценты",
  description: "Светящийся разделитель посередине и подписи «до» и «после»",
  duration: 4,
  params: [
    { name: "left", label: "Слева", type: "text", default: "До" },
    { name: "right", label: "Справа", type: "text", default: "После" },
    { name: "color", label: "Акцент", type: "color", default: VIOLET },
    durationParam(4),
  ],
  build(p, at) {
    const end = at + dur(p, 4);
    const color = str(p, "color", VIOLET);
    const label = (text: string, x: number, bg: string, slide: "slideRight" | "slideLeft") =>
      layer(
        "text",
        { start: at + 0.2, end, track: 1, x, y: 0.1, name: text, in: { type: slide, duration: 0.4 }, out: { type: "fade", duration: 0.25 }, shadow: SOFT },
        { text, font: FONT, weight: 900, size: 76, color: "#ffffff", uppercase: true, bg: { color: bg, opacity: 0.92, radius: 16, padX: 32, padY: 14, skew: -10 } },
      );
    return {
      layers: [
        layer(
          "shape",
          { start: at, end, x: 0.5, y: 0.5, name: "Разделитель", keys: { scale: [{ t: 0, v: 0, ease: "outCubic" }, { t: 0.4, v: 1 }] }, out: { type: "fade", duration: 0.25 }, shadow: glow(color, 30, 1) },
          { kind: "rect", w: 10, h: 1920, fill: "#ffffff", stroke: null, radius: 5 },
        ),
        layer(
          "shape",
          { start: at + 0.3, end, track: 1, x: 0.5, y: 0.5, name: "Ручка", in: { type: "pop", duration: 0.35 }, out: { type: "fade", duration: 0.25 }, shadow: glow(color, 30, 0.9) },
          { kind: "ellipse", w: 96, h: 96, fill: "#ffffff", stroke: { color, width: 8 } },
        ),
        layer(
          "text",
          { start: at + 0.3, end, track: 2, x: 0.5, y: 0.5, name: "Стрелки", in: { type: "pop", duration: 0.35 }, out: { type: "fade", duration: 0.25 } },
          { text: "‹ ›", font: FONT, weight: 900, size: 54, color },
        ),
        label(str(p, "left", "До"), 0.25, "#111111", "slideRight"),
        label(str(p, "right", "После"), 0.75, color, "slideLeft"),
      ],
    };
  },
};

export const alertBanner: MotionTemplate = {
  id: "alert",
  name: "Внимание!",
  category: "Акценты",
  description: "Жёлтая предупреждающая плашка трясётся, по краю кадра мигает красная рамка",
  duration: 3,
  params: [
    { name: "text", label: "Текст", type: "text", default: "Внимание" },
    { name: "subtitle", label: "Подпись", type: "text", default: "Сейчас будет жёстко" },
    { name: "frame", label: "Рамка по краю", type: "bool", default: true },
    durationParam(3),
  ],
  build(p, at) {
    const end = at + dur(p, 3);
    const layers: Layer[] = [
      layer(
        "text",
        { start: at, end, y: 0.3, rotation: -2, name: "Внимание", in: { type: "drop", duration: 0.5 }, out: { type: "zoom", duration: 0.25 }, loop: { type: "shake", speed: 0.6, amount: 0.8 }, shadow: SOFT },
        {
          text: str(p, "text", "Внимание"),
          font: FONT,
          weight: 900,
          size: 96,
          color: "#111111",
          uppercase: true,
          bg: { color: YELLOW, opacity: 1, radius: 18, padX: 44, padY: 26, skew: -8, gradient: ["#FFE45C", "#FFC400"] },
        },
      ),
    ];
    const sub = str(p, "subtitle", "Сейчас будет жёстко");
    if (sub) {
      layers.push(
        layer(
          "text",
          { start: at + 0.35, end, y: 0.375, name: "Подпись", in: { type: "slideUp", duration: 0.35 }, out: { type: "fade", duration: 0.25 } },
          { text: sub, font: FONT, weight: 900, size: 56, color: "#ffffff", stroke: { color: "#000000", width: 8 }, uppercase: true, maxWidth: 900 },
        ),
      );
    }
    if (bool(p, "frame", true)) {
      layers.push(
        layer(
          "shape",
          { start: at, end, track: 2, name: "Рамка", in: { type: "fade", duration: 0.2 }, out: { type: "fade", duration: 0.25 }, loop: { type: "blink", speed: 1.3, amount: 1 }, shadow: glow(RED, 40, 0.9) },
          { kind: "rect", w: 1060, h: 1900, fill: null, stroke: { color: RED, width: 16 }, radius: 36 },
        ),
      );
    }
    return { layers };
  },
};

export const boom: MotionTemplate = {
  id: "boom",
  name: "Бум!",
  category: "Акценты",
  description: "Комиксный взрыв с надписью, звук удара и тряска камеры",
  duration: 1.5,
  params: [
    { name: "text", label: "Надпись", type: "text", default: "Бум!" },
    { name: "sound", label: "Звук", type: "sound", default: "sfx:default/boom.mp3" },
    { name: "x", label: "x", type: "number", default: 0.5, min: 0, max: 1 },
    { name: "y", label: "y", type: "number", default: 0.4, min: 0, max: 1 },
    { name: "shake", label: "Тряска камеры", type: "bool", default: true },
    durationParam(1.5),
  ],
  build(p, at) {
    const end = at + dur(p, 1.5);
    const x = num(p, "x", 0.5);
    const y = num(p, "y", 0.4);
    const pop = { in: { type: "pop" as const, duration: 0.3 }, out: { type: "zoom" as const, duration: 0.25 } };
    const layers: Layer[] = [
      layer(
        "shape",
        { start: at, end, x, y, rotation: -8, name: "Взрыв", ...pop, loop: { type: "pulse", speed: 2, amount: 1 }, shadow: glow("#FF7A00", 40, 0.8) },
        { kind: "burst", w: 640, h: 520, points: 14, fill: YELLOW, stroke: { color: RED, width: 14 } },
      ),
      layer(
        "text",
        { start: at + 0.05, end, track: 1, x, y, rotation: -8, name: "Надпись", ...pop, loop: { type: "shake", speed: 1, amount: 1 } },
        { text: str(p, "text", "Бум!"), font: FONT, weight: 900, size: 150, color: "#ffffff", stroke: { color: "#B00020", width: 14 }, uppercase: true, gradient: ["#ffffff", "#FFE0E0"] },
      ),
    ];
    const sound = str(p, "sound", "");
    if (sound) layers.push(layer("audio", { start: at, end: Math.max(end, at + 1), name: "Звук" }, { asset_id: sound, volume: 1, fadeIn: 0, fadeOut: 0.1, offset: 0, duck: false }));
    const camera: CameraKey[] | undefined = bool(p, "shake", true)
      ? [
          { t: Math.max(0, at - 0.01), zoom: 1, x: 0.5, y: 0.5, shake: 0, ease: "outCubic" },
          { t: at + 0.06, zoom: 1.12, x: 0.5, y: 0.5, shake: 1, ease: "outCubic" },
          { t: at + 0.6, zoom: 1, x: 0.5, y: 0.5, shake: 0 },
        ]
      : undefined;
    return { layers, camera };
  },
};
