// Шаблоны: плашки (нижняя треть, бегущая строка, плашка канала).
import { NAMEPLATE_PLATFORMS, NAMEPLATE_STYLES } from "../layers/nameplate";
import type { NameplatePlatform } from "../types";
import { REF_W } from "../types";
import { dur, durationParam, FONT, glow, layer, type MotionTemplate, num, RED, SOFT, str } from "./kit";

export function lowerThird(style: "bar" | "box" | "pill" | "neon", name: string, description: string, accent: string, skew: number): MotionTemplate {
  return {
    id: `lower-third-${style}`,
    name,
    category: "Плашки",
    description,
    duration: 4,
    params: [
      { name: "title", label: "Заголовок", type: "text", default: "Лучший момент" },
      { name: "subtitle", label: "Подзаголовок", type: "text", default: style === "bar" ? "" : "Стрим · 12 марта" },
      { name: "icon", label: "Значок", type: "emoji", default: "" },
      { name: "accent", label: "Акцент", type: "color", default: accent },
      { name: "y", label: "Высота", type: "number", default: 0.8, min: 0, max: 1 },
      durationParam(4),
    ],
    build(p, at) {
      const end = at + dur(p, 4);
      const inType = style === "pill" ? "slideRight" : style === "neon" ? "zoom" : "wipe";
      return {
        layers: [
          layer(
            "callout",
            {
              start: at,
              end,
              y: num(p, "y", 0.8),
              name: "Нижняя треть",
              in: { type: inType, duration: 0.45 },
              out: { type: style === "pill" ? "slideRight" : "fade", duration: 0.3 },
              shadow: style === "neon" ? null : SOFT,
            },
            {
              title: str(p, "title", "Лучший момент"),
              subtitle: str(p, "subtitle", ""),
              icon: str(p, "icon", "👑"),
              accent: str(p, "accent", accent),
              style,
              font: FONT,
              w: 1000,
              skew,
            },
          ),
        ],
      };
    },
  };
}

export const newsTicker: MotionTemplate = {
  id: "news-ticker",
  fit: { x: "frame" },
  name: "Бегущая строка",
  category: "Плашки",
  description: "Тёмная лента внизу, скошенная плашка «Новости» и текст бегущей строкой",
  duration: 8,
  params: [
    { name: "label", label: "Плашка", type: "text", default: "Новости" },
    { name: "text", label: "Бегущий текст", type: "text", default: "Главное за сегодня: стример побил рекорд, чат в шоке, клип уже в рекомендациях" },
    { name: "color", label: "Акцент", type: "color", default: RED },
    { name: "y", label: "Высота", type: "number", default: 0.84, min: 0, max: 1 },
    durationParam(8),
  ],
  build(p, at) {
    const len = dur(p, 8);
    const end = at + len;
    const y = num(p, "y", 0.84);
    const color = str(p, "color", RED);
    const text = str(p, "text", "Главное за сегодня");
    // Ширина строки на глаз (~0.58 кегля на букву): текст уходит за левый край к концу слоя.
    const widthFrac = (text.length * 46 * 0.58) / 1080;
    const anim = { in: { type: "wipe" as const, duration: 0.4 }, out: { type: "fade" as const, duration: 0.3 } };
    return {
      layers: [
        layer("shape", { start: at, end, y, name: "Лента", ...anim, shadow: SOFT }, { kind: "rect", w: 1080, h: 104, fill: "#0E0A1FEE", stroke: null }),
        layer("shape", { start: at, end, y: y - 52 / 1920, track: 1, name: "Кромка", ...anim, shadow: glow(color, 20, 0.9) }, { kind: "rect", w: 1080, h: 6, fill: color, stroke: null }),
        layer(
          "text",
          {
            start: at,
            end,
            y,
            track: 1,
            name: "Бегущий текст",
            keys: { x: [{ t: 0.2, v: 1 + widthFrac / 2, ease: "linear" }, { t: len, v: -widthFrac / 2 }] },
            out: { type: "fade", duration: 0.3 },
          },
          { text, font: FONT, weight: 700, size: 46, color: "#ffffff", maxWidth: 20000 },
        ),
        // Подложка слева от плашки: уехавший влево текст прячется под ней, а не торчит у края.
        layer("shape", { start: at, end, x: 0.08, y, track: 2, name: "Подложка", ...anim }, { kind: "rect", w: 173, h: 104, fill: "#0E0A1F", stroke: null }),
        layer(
          "text",
          { start: at, end, x: 0.16, y, track: 2, name: "Плашка", in: { type: "slideRight", duration: 0.4 }, out: { type: "fade", duration: 0.3 }, shadow: glow(color, 30, 0.7) },
          { text: str(p, "label", "Новости"), font: FONT, weight: 900, size: 52, color: "#ffffff", uppercase: true, bg: { color, opacity: 1, radius: 8, padX: 30, padY: 22, skew: -12 } },
        ),
      ],
    };
  },
};

/** Ширина рамки плашки канала: ник до ~16 букв помещается, пилюля прижата к краю рамки. */
const PLATE_BOX = 520;

export const channelPlate: MotionTemplate = {
  id: "channel-plate",
  fit: { x: "frame", y: "frame" },
  name: "Плашка канала",
  category: "Плашки",
  description: "Значок площадки и ник в тёмной стеклянной пилюле — весь ролик, у края кадра",
  duration: 60,
  params: [
    { name: "handle", label: "Ник", type: "text", default: "channel" },
    { name: "platform", label: "Площадка", type: "select", default: "twitch", options: [...NAMEPLATE_PLATFORMS] },
    { name: "style", label: "Вид", type: "select", default: "glass", options: [...NAMEPLATE_STYLES] },
    { name: "side", label: "Сторона", type: "select", default: "left", options: ["left", "right"] },
    { name: "y", label: "Высота", type: "number", default: 0.13, min: 0.1, max: 0.8 },
    durationParam(60),
  ],
  build(p, at) {
    const platform = str(p, "platform", "twitch");
    const style = str(p, "style", "glass");
    const right = str(p, "side", "left") === "right";
    const half = (24 + PLATE_BOX / 2) / REF_W;
    return {
      layers: [
        layer(
          "nameplate",
          { start: at, end: at + dur(p, 60), x: right ? 1 - half : half, y: num(p, "y", 0.13), track: 4, name: "Плашка канала", shadow: { color: "#000000", blur: 14, x: 0, y: 4, opacity: 0.45 } },
          {
            platform: ((NAMEPLATE_PLATFORMS as readonly string[]).includes(platform) ? platform : "twitch") as NameplatePlatform,
            handle: str(p, "handle", "channel"),
            style: style === "solid" ? "solid" : "glass",
            align: right ? "right" : "left",
            boxWidth: PLATE_BOX,
          },
        ),
      ],
    };
  },
};
