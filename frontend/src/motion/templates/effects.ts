// Шаблоны: экран телефона, частицы, эффекты кадра и глитч.
import { GLITCH_SFX, GLITCH_STYLES, glitchKeys } from "../layers/fx";
import type { Layer } from "../types";
import { bool, dur, durationParam, FONT, layer, type MotionTemplate, num, SOFT, str, YELLOW } from "./kit";

/** Цвета корпуса как у мокапов Apple: чёрный, натуральный и белый титан, синий. */
const DEVICE_COLORS: Record<string, string> = {
  "Чёрный титан": "#2c2c2e",
  "Натуральный титан": "#8a857c",
  "Белый титан": "#d9d6cf",
  "Синий титан": "#2f3b4c",
};

export const phoneScreen: MotionTemplate = {
  id: "phone-screen",
  name: "Запись экрана в телефоне",
  category: "Промо",
  description: "Видео (запись экрана приложения) в корпусе телефона: въезжает снизу, мягкая тень, подпись сверху",
  duration: 4,
  params: [
    { name: "asset", label: "Видео (id файла)", type: "text", default: "" },
    { name: "from", label: "С секунды файла", type: "number", default: 0, min: 0, max: 3600 },
    { name: "title", label: "Подпись", type: "text", default: "Смотри, как просто" },
    { name: "color", label: "Корпус", type: "select", default: "Чёрный титан", options: Object.keys(DEVICE_COLORS) },
    durationParam(4),
  ],
  build(p, at) {
    const end = at + dur(p, 4);
    const asset = str(p, "asset", "");
    const out: Layer[] = [
      layer(
        "video",
        {
          start: at,
          end,
          x: 0.5,
          y: 0.57,
          name: "Телефон",
          in: { type: "slideUp", duration: 0.5 },
          out: { type: "fade", duration: 0.25 },
          shadow: { color: "#000000", blur: 70, x: 0, y: 34, opacity: 0.55 },
        },
        {
          ...(asset ? { asset_id: asset } : {}),
          w: 600,
          from: Math.max(0, num(p, "from", 0)),
          speed: 1,
          loop: false,
          freeze: true,
          device: "phone",
          deviceColor: DEVICE_COLORS[str(p, "color", "Чёрный титан")] ?? DEVICE_COLORS["Чёрный титан"],
          island: true,
        },
      ),
    ];
    const title = str(p, "title", "");
    if (title) {
      out.push(
        layer(
          "text",
          { start: at + 0.25, end, track: 1, y: 0.13, name: "Подпись", in: { type: "words", duration: 0.5 }, out: { type: "fade", duration: 0.2 }, shadow: SOFT },
          { text: title, font: FONT, weight: 900, size: 76, color: "#ffffff", maxWidth: 900, lineHeight: 1.05 },
        ),
      );
    }
    return { layers: out };
  },
};

/** Частицы одним слоем: на кульминации вместо десятков слоёв-звёздочек. */
export const particlesTpl: MotionTemplate = {
  id: "particles",
  name: "Искры и конфетти",
  category: "Акценты",
  description: "Один слой частиц: разлёт искр (burst), мерцание звёздочек (sparkle), конфетти или искорки вверх (rise)",
  duration: 2,
  params: [
    { name: "kind", label: "Вид", type: "select", default: "burst", options: ["burst", "sparkle", "confetti", "rise"] },
    { name: "color", label: "Цвет", type: "color", default: YELLOW },
    { name: "count", label: "Сколько", type: "number", default: 60, min: 1, max: 200 },
    { name: "y", label: "Высота", type: "number", default: 0.45, min: 0, max: 1 },
    durationParam(2),
  ],
  build(p, at) {
    const kind = (["burst", "sparkle", "confetti", "rise"] as const).find((k) => k === str(p, "kind", "burst")) ?? "burst";
    const len = dur(p, 2);
    const confetti = kind === "confetti";
    return {
      layers: [
        layer(
          "particles",
          { start: at, end: at + len, track: 2, y: confetti ? 0.5 : num(p, "y", 0.45), name: "Частицы", out: { type: "fade", duration: 0.3 } },
          {
            kind,
            count: Math.max(1, Math.min(200, Math.round(num(p, "count", 60)))),
            colors: confetti ? [str(p, "color", YELLOW), "#FF2D55", "#22D3EE", "#ffffff"] : [str(p, "color", YELLOW), "#ffffff"],
            shape: confetti ? "square" : kind === "rise" ? "dot" : "star4",
            size: confetti ? 26 : kind === "rise" ? 16 : 34,
            spread: confetti ? 560 : kind === "burst" ? 420 : 380,
            duration: kind === "burst" ? Math.min(1.4, len) : confetti ? Math.max(1.5, len) : 1.6,
            glow: !confetti,
          },
        ),
      ],
    };
  },
};

/** Эффект кадра (fx) одним слоем: VHS, строки, засветка, аберрация, вспышка, зерно. */
export const fxTpl: MotionTemplate = {
  id: "fx",
  name: "Эффект кадра",
  category: "Эффекты",
  description: "VHS, строки развёртки, засветка по краям, аберрация, вспышка или зерно — поверх видео и слоёв ниже",
  duration: 3,
  params: [
    { name: "kind", label: "Вид", type: "select", default: "vhs", options: ["vhs", "scanlines", "light_leak", "chromatic", "flash_frame", "grain_overlay"] },
    { name: "intensity", label: "Сила", type: "number", default: 0.6, min: 0, max: 1 },
    durationParam(3),
  ],
  build(p, at) {
    const kinds = ["vhs", "scanlines", "light_leak", "chromatic", "flash_frame", "grain_overlay"] as const;
    const kind = kinds.find((k) => k === str(p, "kind", "vhs")) ?? "vhs";
    // Вспышка — короткая сама по себе; остальное мягко проявляется и гаснет.
    const len = kind === "flash_frame" ? Math.min(0.5, dur(p, 0.35)) : dur(p, 3);
    const soft = kind === "flash_frame" ? null : { type: "fade" as const, duration: 0.3 };
    return {
      layers: [
        layer(
          "fx",
          { start: at, end: at + len, track: 6, name: "Эффект", in: soft ?? { type: "none" }, out: soft ?? { type: "none" } },
          { kind, intensity: Math.max(0, Math.min(1, num(p, "intensity", 0.6))), speed: 1 },
        ),
      ],
    };
  },
};

/**
 * Глитч на отрезке основы (как MCP add_glitch): слой fx стиля со «сбоящей» силой (ключи scale —
 * glitchKeys) под всеми слоями — видео сбоит, титры чистые; по желанию — настоящий звук глитча.
 */
export const glitchRange: MotionTemplate = {
  id: "glitch-range",
  name: "Глитч на отрезке",
  category: "Эффекты",
  description: "Сбой видео на отрезке: RGB-сдвиг, разрывы строк, VHS, датамош или штрихи пикселей — рывками, титры поверх чистые",
  duration: 0.8,
  params: [
    { name: "style", label: "Стиль", type: "select", default: "rgb_split", options: [...GLITCH_STYLES] },
    { name: "intensity", label: "Сила", type: "number", default: 0.8, min: 0, max: 1 },
    { name: "sfx", label: "Звук глитча", type: "bool", default: true },
    durationParam(0.8),
  ],
  build(p, at) {
    const style = GLITCH_STYLES.find((s) => s === str(p, "style", "rgb_split")) ?? "rgb_split";
    const len = dur(p, 0.8);
    const layers: Layer[] = [
      layer(
        "fx",
        { start: at, end: at + len, track: 0, name: "Глитч", keys: { scale: glitchKeys(len) } },
        { kind: style, intensity: Math.max(0, Math.min(1, num(p, "intensity", 0.8))), speed: 1 },
      ),
    ];
    if (bool(p, "sfx", true)) {
      layers.push(layer("audio", { start: at, end: at + Math.max(0.6, Math.min(1.6, len + 0.3)), name: "Звук глитча" }, { asset_id: GLITCH_SFX[style], volume: 0.7, fadeIn: 0, fadeOut: 0.15, offset: 0, duck: false }));
    }
    return { layers };
  },
};

/** Глитч-титр: влетает глитчем и иногда «сбоит» — геймерские и мемные моменты. */
export const glitchTitle: MotionTemplate = {
  id: "glitch-title",
  name: "Глитч-титр",
  category: "Титры",
  description: "Капс влетает RGB-расслоением со срезами и изредка сбоит, пока виден",
  duration: 2.5,
  params: [
    { name: "text", label: "Текст", type: "text", default: "СИСТЕМА СЛОМАНА" },
    { name: "y", label: "Высота (0 — верх)", type: "number", default: 0.3, min: 0, max: 1 },
    durationParam(2.5),
  ],
  build(p, at) {
    return {
      layers: [
        layer(
          "text",
          {
            start: at,
            end: at + dur(p, 2.5),
            track: 2,
            y: num(p, "y", 0.3),
            name: "Глитч-титр",
            in: { type: "glitch", duration: 0.45 },
            out: { type: "glitch", duration: 0.3 },
            loop: { type: "glitch", speed: 1, amount: 0.8 },
          },
          { text: str(p, "text", "СИСТЕМА СЛОМАНА"), font: FONT, weight: 900, size: 104, color: "#ffffff", stroke: { color: "#000000", width: 8 }, uppercase: true, maxWidth: 940 },
        ),
      ],
    };
  },
};
