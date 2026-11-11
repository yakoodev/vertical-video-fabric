// Шаблоны рекламного пакета (категория «Реклама»), часть 1: одометр, облако пузырей, рейтинг, карусель.
// Вид — современные UI-промо: нейтральная тёмная палитра, один акцент (параметр), гарнитура Manrope,
// без эмодзи и чужих логотипов. Слои — обычные (counter, bubbles, carousel, text), правятся как любые.
import { AD_ACCENT, AD_FONT, mixHex } from "../layers/adkit";
import { AD_ICONS } from "../layers/adicons";
import type { AdIconName, BubbleItem, CarouselCard, Layer } from "../types";
import { bool, dur, durationParam, glow, layer, type MotionTemplate, num, str, type TemplateParam } from "./kit";

export const AD_CATEGORY = "Реклама";

export const accentParam: TemplateParam = { name: "accent", label: "Акцент", type: "color", default: AD_ACCENT };
export const themeParam: TemplateParam = { name: "theme", label: "Тема", type: "select", default: "тёмная", options: ["тёмная", "светлая"] };
export const themeOf = (p: Parameters<MotionTemplate["build"]>[0]) => (str(p, "theme", "тёмная") === "светлая" ? "light" : "dark") as "light" | "dark";

/** «a | b | c; d | e | f» → [[a, b, c], [d, e, f]] — списки в одном текстовом поле галереи. */
export function parseRows(raw: string, max = 12): string[][] {
  return String(raw || "")
    .split(/[;\n]/)
    .map((row) => row.split("|").map((s) => s.trim()))
    .filter((row) => row.some(Boolean))
    .slice(0, max);
}

const numOf = (s: string | undefined): number | null => {
  const v = Number(String(s ?? "").replace(/\s/g, "").replace(",", "."));
  return s !== undefined && s !== "" && Number.isFinite(v) ? v : null;
};

const iconOf = (s: string | undefined): AdIconName | string | undefined => {
  const v = (s ?? "").trim();
  return v ? ((AD_ICONS as readonly string[]).includes(v) ? (v as AdIconName) : v) : undefined;
};

/* ---------- одометр ---------- */

export const odometerCounter: MotionTemplate = {
  id: "odometer-counter",
  name: "Счётчик-одометр",
  category: AD_CATEGORY,
  description: "Огромное число крутится барабанами по разрядам до цели: «больше 2 000 000», подпись снизу",
  duration: 4,
  params: [
    { name: "caption", label: "Над числом", type: "text", default: "больше" },
    { name: "from", label: "От", type: "number", default: 0 },
    { name: "to", label: "До", type: "number", default: 2000000 },
    { name: "prefix", label: "Перед числом", type: "text", default: "" },
    { name: "suffix", label: "После числа («+», « ₽»)", type: "text", default: "" },
    { name: "label", label: "Подпись", type: "text", default: "пользователей уже с нами" },
    { name: "laps", label: "Лишних оборотов у младших разрядов", type: "number", default: 2, min: 0, max: 6 },
    { name: "stagger", label: "Шаг разрядов, с", type: "number", default: 0.07, min: 0, max: 0.5 },
    { name: "size", label: "Кегль числа", type: "number", default: 168, min: 60, max: 320 },
    accentParam,
    { name: "y", label: "Высота", type: "number", default: 0.42, min: 0.1, max: 0.9 },
    durationParam(4),
  ],
  build(p, at) {
    const end = at + dur(p, 4);
    const y = num(p, "y", 0.42);
    const accent = str(p, "accent", AD_ACCENT);
    const size = num(p, "size", 168);
    const roll = Math.min(2.2, (end - at) * 0.55);
    const layers: Layer[] = [];
    const caption = str(p, "caption", "");
    if (caption)
      layers.push(
        layer(
          "text",
          { start: at, end, y: y - (size * 0.62 + 44) / 1920, track: 1, name: "Над числом", in: { type: "rise", duration: 0.5 }, out: { type: "fade", duration: 0.25 } },
          { text: caption, font: AD_FONT, weight: 600, size: 58, color: mixHex(accent, "#ffffff", 0.7), letterSpacing: 1 },
        ),
      );
    layers.push(
      layer(
        "counter",
        {
          start: at,
          end,
          y,
          track: 2,
          name: "Одометр",
          in: { type: "fade", duration: 0.2 },
          out: { type: "fade", duration: 0.25 },
          shadow: glow(accent, 60, 0.55),
          keys: { progress: [{ t: 0.05, v: 0, ease: "bezier(0.2,0.7,0.15,1)" }, { t: roll, v: 1 }] },
        },
        {
          from: num(p, "from", 0),
          to: num(p, "to", 2000000),
          thousands: " ",
          prefix: str(p, "prefix", ""),
          suffix: str(p, "suffix", ""),
          roll: true,
          laps: Math.round(num(p, "laps", 2)),
          stagger: num(p, "stagger", 0.07),
          font: AD_FONT,
          weight: 800,
          size,
          color: "#ffffff",
          gradient: ["#ffffff", mixHex(accent, "#ffffff", 0.55)],
          letterSpacing: -2,
          maxWidth: 1000,
        },
      ),
    );
    const label = str(p, "label", "");
    if (label)
      layers.push(
        layer(
          "text",
          { start: at + roll * 0.6, end, y: y + (size * 0.62 + 40) / 1920, track: 1, name: "Подпись", in: { type: "rise", duration: 0.5 }, out: { type: "fade", duration: 0.25 } },
          { text: label, font: AD_FONT, weight: 600, size: 46, color: "#c9ccd6", maxWidth: 900 },
        ),
      );
    return { layers };
  },
};

/* ---------- облако пузырей ---------- */

export const bubblesCloud: MotionTemplate = {
  id: "bubbles-cloud",
  name: "Облако пузырей",
  category: AD_CATEGORY,
  description: "Круги со значком, числом и подписью выпрыгивают по очереди и парят; один выходит в фокус",
  duration: 5,
  params: [
    {
      name: "items",
      label: "Пузыри: значок | число | подпись; … (значки: " + AD_ICONS.join(", ") + ")",
      type: "text",
      default: "star | 48000 | отзывов; store | 12000 | продавцов; game | 3400 | игр; shield | 21000 | сделок; chat | 9000 | чатов; gift | 2100 | бонусов; bolt | 640 | в час",
    },
    { name: "focus", label: "В фокус (номер с 1, 0 — без фокуса)", type: "number", default: 1, min: 0, max: 12 },
    { name: "style", label: "Вид", type: "select", default: "стекло", options: ["стекло", "заливка"] },
    { name: "seed", label: "Вариант раскладки", type: "number", default: 7, min: 0, max: 999 },
    accentParam,
    themeParam,
    { name: "y", label: "Высота", type: "number", default: 0.47, min: 0.2, max: 0.8 },
    durationParam(5),
  ],
  build(p, at) {
    const items: BubbleItem[] = parseRows(str(p, "items", "")).map(([icon, value, label]) => ({
      icon: iconOf(icon),
      value: numOf(value),
      label: label || "",
    }));
    const focus = Math.round(num(p, "focus", 1)) - 1;
    return {
      layers: [
        layer(
          "bubbles",
          { start: at, end: at + dur(p, 5), y: num(p, "y", 0.47), track: 2, name: "Облако пузырей", out: { type: "fade", duration: 0.3 } },
          {
            items,
            w: 1000,
            h: 900,
            seed: Math.round(num(p, "seed", 7)),
            focus,
            style: str(p, "style", "стекло") === "заливка" ? "solid" : "glass",
            theme: themeOf(p),
            accent: str(p, "accent", AD_ACCENT),
            font: AD_FONT,
          },
        ),
      ],
    };
  },
};

/* ---------- рейтинг ---------- */

export const ratingBadge: MotionTemplate = {
  id: "rating-badge",
  name: "Рейтинг и отзывы",
  category: AD_CATEGORY,
  description: "Пять звёзд загораются по одной, оценка и число отзывов накручиваются",
  duration: 4,
  params: [
    { name: "rating", label: "Оценка", type: "number", default: 4.9, min: 0, max: 5 },
    { name: "reviews", label: "Отзывов", type: "number", default: 12400, min: 0, max: 1e9 },
    { name: "label", label: "Подпись к отзывам", type: "text", default: "отзывов" },
    { name: "color", label: "Цвет звёзд", type: "color", default: "#ffc83d" },
    { name: "y", label: "Высота", type: "number", default: 0.3, min: 0.1, max: 0.9 },
    durationParam(4),
  ],
  build(p, at) {
    const end = at + dur(p, 4);
    const y = num(p, "y", 0.3);
    const color = str(p, "color", "#ffc83d");
    const stars: Layer[] = Array.from({ length: 5 }, (_, i) =>
      layer(
        "text",
        {
          start: at + 0.1 + i * 0.12,
          end,
          x: 0.5 + (i - 2) * 0.115,
          y,
          track: 2,
          name: `Звезда ${i + 1}`,
          in: { type: "pop", duration: 0.4 },
          out: { type: "fade", duration: 0.25 },
          shadow: glow(color, 30, 0.6),
        },
        { text: "★", font: AD_FONT, weight: 800, size: 118, color, gradient: [mixHex(color, "#ffffff", 0.45), color] },
      ),
    );
    return {
      layers: [
        ...stars,
        layer(
          "counter",
          { start: at + 0.3, end, y: y + 0.085, track: 2, name: "Оценка", in: { type: "fade", duration: 0.2 }, out: { type: "fade", duration: 0.25 }, keys: { progress: [{ t: 0, v: 0, ease: "outCubic" }, { t: 1.2, v: 1 }] } },
          { from: 0, to: num(p, "rating", 4.9), decimals: 1, thousands: " ", font: AD_FONT, weight: 800, size: 120, color: "#ffffff" },
        ),
        layer(
          "counter",
          { start: at + 0.6, end, y: y + 0.152, track: 2, name: "Отзывы", in: { type: "rise", duration: 0.4 }, out: { type: "fade", duration: 0.25 }, keys: { progress: [{ t: 0, v: 0, ease: "outCubic" }, { t: 1.4, v: 1 }] } },
          { from: 0, to: num(p, "reviews", 12400), thousands: " ", suffix: ` ${str(p, "label", "отзывов")}`, font: AD_FONT, weight: 600, size: 50, color: "#c9ccd6" },
        ),
      ],
    };
  },
};

/* ---------- карусель ---------- */

export const cardCarousel: MotionTemplate = {
  id: "card-carousel",
  name: "Карусель карточек",
  category: AD_CATEGORY,
  description: "Карточки персонажей лентой: центральная крупнее, за ней светятся кольца, соседи выглядывают",
  duration: 7,
  params: [
    {
      name: "cards",
      label: "Карточки: имя | подпись | строка снизу | картинка (id файла, можно пусто); …",
      type: "text",
      default: "Лиса Ника | Ласковая и смешная | 1,2 млн прослушиваний; Граф Орлов | Голос рассказчика | 860 тыс. прослушиваний; Миа | Бодрая подруга | 2,4 млн прослушиваний; Робо-7 | Голос из будущего | 410 тыс. прослушиваний",
    },
    { name: "interval", label: "Переключать каждые, с", type: "number", default: 1.7, min: 0.6, max: 10 },
    { name: "halo", label: "Кольца за центральной", type: "bool", default: true },
    accentParam,
    { name: "y", label: "Высота", type: "number", default: 0.45, min: 0.2, max: 0.8 },
    durationParam(7),
  ],
  build(p, at) {
    const cards: CarouselCard[] = parseRows(str(p, "cards", "")).map(([title, subtitle, meta, image]) => ({
      title: title || "",
      subtitle: subtitle || "",
      meta: meta || "",
      ...(image ? { image } : {}),
    }));
    return {
      layers: [
        layer(
          "carousel",
          { start: at, end: at + dur(p, 7), y: num(p, "y", 0.45), track: 2, name: "Карусель", in: { type: "fade", duration: 0.35 }, out: { type: "fade", duration: 0.3 }, shadow: { color: "#000000", blur: 40, x: 0, y: 18, opacity: 0.5 } },
          { cards, interval: num(p, "interval", 1.7), halo: bool(p, "halo", true), accent: str(p, "accent", AD_ACCENT), font: AD_FONT },
        ),
      ],
    };
  },
};
