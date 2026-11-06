// Шаблоны: титры и текст (заголовки, цитата, мем-подпись, субтитры, чат).
import { CAPTION_STYLES, DEFAULT_CAPTION_STYLE } from "../layers/captions";
import { CHAT_STYLES } from "../layers/chat";
import type { CameraKey, ChatStyle, Layer } from "../types";
import { bool, dur, durationParam, FONT, glow, layer, type MotionTemplate, num, shadeHex, SOFT, sparkles, str, VIOLET, YELLOW } from "./kit";

/* ---------- шаблоны ---------- */

export const titlePop: MotionTemplate = {
  id: "title-pop",
  name: "Заголовок-поп",
  category: "Титры",
  description: "Крупный капс с обводкой выпрыгивает и «дышит», вокруг искры",
  duration: 3,
  params: [
    { name: "text", label: "Текст", type: "text", default: "ОН ЭТО СДЕЛАЛ" },
    { name: "color", label: "Цвет", type: "color", default: YELLOW },
    { name: "y", label: "Высота (0 — верх)", type: "number", default: 0.42, min: 0, max: 1 },
    { name: "sparkles", label: "Искры", type: "bool", default: true },
    durationParam(3),
  ],
  build(p, at) {
    const end = at + dur(p, 3);
    const y = num(p, "y", 0.42);
    const color = str(p, "color", YELLOW);
    const title = layer(
      "text",
      {
        start: at,
        end,
        y,
        rotation: -3,
        name: "Заголовок",
        in: { type: "pop", duration: 0.45 },
        out: { type: "zoom", duration: 0.25 },
        loop: { type: "pulse", speed: 0.8, amount: 0.5 },
        shadow: SOFT,
      },
      {
        text: str(p, "text", "ОН ЭТО СДЕЛАЛ"),
        font: FONT,
        weight: 900,
        size: 132,
        color,
        stroke: { color: "#000000", width: 12 },
        maxWidth: 900,
        lineHeight: 1.0,
        uppercase: true,
        align: "center",
      },
    );
    return { layers: [title, ...(bool(p, "sparkles", true) ? sparkles(at, end, 0.5, y, color) : [])] };
  },
};

export const punchline: MotionTemplate = {
  id: "punchline",
  name: "Панчлайн с выделением",
  category: "Титры",
  description: "Фраза на тёмной плашке, слова выпрыгивают по одному, ключевое — подсвечено",
  duration: 3,
  params: [
    { name: "text", label: "Текст", type: "text", default: "И тут он понял, что это ловушка" },
    { name: "word", label: "Номер выделенного слова (с 1)", type: "number", default: 7, min: 1, max: 60 },
    { name: "accent", label: "Цвет выделения", type: "color", default: YELLOW },
    { name: "y", label: "Высота", type: "number", default: 0.62, min: 0, max: 1 },
    durationParam(3),
  ],
  build(p, at) {
    const end = at + dur(p, 3);
    const text = str(p, "text", "И тут он понял, что это ловушка");
    const words = text.split(/\s+/).filter(Boolean).length;
    return {
      layers: [
        layer(
          "text",
          {
            start: at,
            end,
            y: num(p, "y", 0.62),
            name: "Панчлайн",
            in: { type: "words", duration: Math.min(1.6, 0.12 * words + 0.2) },
            out: { type: "fade", duration: 0.25 },
            shadow: SOFT,
          },
          {
            text,
            font: FONT,
            weight: 900,
            size: 76,
            color: "#ffffff",
            bg: { color: "#0E0B1A", opacity: 0.86, radius: 22, padX: 30, padY: 16 },
            maxWidth: 860,
            lineHeight: 1.22,
            uppercase: true,
            highlight: { words: [Math.max(0, Math.round(num(p, "word", 7)) - 1)], color: "#111111", bg: str(p, "accent", YELLOW) },
          },
        ),
      ],
    };
  },
};

export const quoteCard: MotionTemplate = {
  id: "quote-card",
  name: "Цитата",
  category: "Титры",
  description: "Карточка с огромными кавычками, текстом цитаты и автором",
  duration: 5,
  params: [
    { name: "text", label: "Цитата", type: "text", default: "Каждый момент — это история" },
    { name: "author", label: "Автор", type: "text", default: "Алексей Петров" },
    { name: "color", label: "Акцент", type: "color", default: VIOLET },
    durationParam(5),
  ],
  build(p, at) {
    const end = at + dur(p, 5);
    const color = str(p, "color", VIOLET);
    const out = { type: "fade" as const, duration: 0.3 };
    return {
      layers: [
        layer(
          "shape",
          { start: at, end, y: 0.46, name: "Карточка", in: { type: "zoom", duration: 0.4 }, out, shadow: glow(color, 60, 0.45) },
          { kind: "rect", w: 900, h: 600, fill: "#100B22EB", stroke: { color: shadeHex(color, 0.2), width: 4 }, radius: 48 },
        ),
        layer(
          "text",
          { start: at + 0.2, end, track: 1, x: 0.22, y: 0.33, rotation: -4, name: "Кавычки", in: { type: "pop", duration: 0.4 }, out, shadow: glow(color, 30, 0.8) },
          { text: "“", font: FONT, weight: 900, size: 340, color, gradient: [shadeHex(color, 0.35), color], lineHeight: 0.8 },
        ),
        layer(
          "text",
          { start: at + 0.35, end, track: 1, x: 0.5, y: 0.448, name: "Цитата", in: { type: "words", duration: 0.8 }, out },
          { text: str(p, "text", "Каждый момент — это история"), font: FONT, weight: 900, size: 72, color: "#ffffff", align: "left", boxWidth: 740, maxWidth: 740, lineHeight: 1.12 },
        ),
        layer(
          "text",
          { start: at + 0.9, end, track: 1, x: 0.66, y: 0.556, name: "Автор", in: { type: "slideLeft", duration: 0.4 }, out },
          { text: str(p, "author", "Алексей Петров"), font: FONT, weight: 700, size: 40, color: "#ffffff", bg: { color: "#ffffff", opacity: 0.12, radius: 30, padX: 30, padY: 12 } },
        ),
      ],
    };
  },
};

export const captions: MotionTemplate = {
  id: "captions",
  name: "Анимированные субтитры",
  category: "Титры",
  description: "Слова речи клипа страницами, звучащее слово подсвечено — стиль на выбор",
  duration: 60,
  params: [
    { name: "style", label: "Стиль", type: "select", default: DEFAULT_CAPTION_STYLE, options: CAPTION_STYLES.map((s) => s.id) },
    { name: "y", label: "Высота", type: "number", default: 0.68, min: 0.1, max: 0.8 },
    durationParam(60),
  ],
  build(p, at) {
    return {
      layers: [
        layer(
          "captions",
          { start: at, end: at + dur(p, 60), y: num(p, "y", 0.68), track: 3, name: "Субтитры", shadow: { color: "#000000", blur: 18, x: 0, y: 6, opacity: 0.5 } },
          { style: str(p, "style", DEFAULT_CAPTION_STYLE) },
        ),
      ],
    };
  },
};

export const streamChat: MotionTemplate = {
  id: "stream-chat",
  name: "Чат стрима",
  category: "Акценты",
  description: "Сообщения Twitch-чата всплывают в такт клипу: ники цветом, эмоуты картинками",
  duration: 60,
  params: [
    { name: "style", label: "Стиль", type: "select", default: "twitch-dark", options: CHAT_STYLES.map((s) => s.id) },
    { name: "maxMessages", label: "Сообщений на экране", type: "number", default: 6, min: 1, max: 12 },
    { name: "delay", label: "Задержка чата, с", type: "number", default: 0, min: 0, max: 30 },
    { name: "y", label: "Высота", type: "number", default: 0.36, min: 0.1, max: 0.8 },
    durationParam(60),
  ],
  build(p, at) {
    const style = str(p, "style", "twitch-dark");
    return {
      layers: [
        layer(
          "chat",
          { start: at, end: at + dur(p, 60), x: 0.5, y: num(p, "y", 0.36), track: 2, name: "Чат" },
          {
            style: (CHAT_STYLES.some((s) => s.id === style) ? style : "twitch-dark") as ChatStyle,
            maxMessages: Math.max(1, Math.min(12, Math.round(num(p, "maxMessages", 6)))),
            delay: Math.max(0, num(p, "delay", 0)),
          },
        ),
      ],
    };
  },
};

export const keywordBig: MotionTemplate = {
  id: "keyword-big",
  name: "Ключевое слово крупно",
  category: "Титры",
  description: "Одно слово на весь экран: удар, тряска и наезд камеры",
  duration: 1.6,
  params: [
    { name: "text", label: "Слово", type: "text", default: "Невероятно" },
    { name: "emoji", label: "Эмодзи (необязательно)", type: "emoji", default: "" },
    { name: "color", label: "Цвет", type: "color", default: YELLOW },
    { name: "zoom", label: "Наезд камеры", type: "bool", default: true },
    durationParam(1.6),
  ],
  build(p, at) {
    const len = dur(p, 1.6);
    const end = at + len;
    const color = str(p, "color", YELLOW);
    const layers: Layer[] = [
      layer(
        "text",
        {
          start: at,
          end,
          y: 0.45,
          rotation: -4,
          track: 2,
          name: "Ключевое слово",
          in: { type: "pop", duration: 0.25 },
          out: { type: "zoom", duration: 0.2 },
          loop: { type: "shake", speed: 0.8, amount: 0.7 },
          shadow: glow(color, 50, 0.6),
        },
        {
          text: str(p, "text", "Невероятно"),
          font: FONT,
          weight: 900,
          size: 170,
          color,
          gradient: ["#ffffff", color],
          stroke: { color: "#000000", width: 14 },
          uppercase: true,
          maxWidth: 1000,
          lineHeight: 0.95,
        },
      ),
    ];
    const emoji = str(p, "emoji", "");
    if (emoji) {
      layers.push(
        layer(
          "emoji",
          { start: at + 0.12, end, x: 0.5, y: 0.35, track: 3, rotation: 10, name: "Эмодзи", in: { type: "pop", duration: 0.3 }, out: { type: "fade", duration: 0.2 }, loop: { type: "wiggle", speed: 1.5, amount: 1 }, shadow: SOFT },
          { char: emoji, size: 220 },
        ),
      );
    }
    const camera: CameraKey[] | undefined = bool(p, "zoom", true)
      ? [
          { t: Math.max(0, at - 0.01), zoom: 1, x: 0.5, y: 0.45, ease: "outCubic" },
          { t: at + 0.18, zoom: 1.18, x: 0.5, y: 0.45, ease: "inOutCubic" },
          { t: end - 0.15, zoom: 1.18, x: 0.5, y: 0.45, ease: "inOutCubic" },
          { t: end + 0.1, zoom: 1, x: 0.5, y: 0.5 },
        ]
      : undefined;
    return { layers, camera };
  },
};

export const memeCaption: MotionTemplate = {
  id: "meme-caption",
  fit: { x: "frame", y: "frame" },
  name: "Мем-подпись",
  category: "Мемы",
  description: "Белый капс с чёрной обводкой сверху и снизу, как в классических мемах",
  duration: 3,
  params: [
    { name: "top", label: "Сверху", type: "text", default: "Когда сказал, что на минутку" },
    { name: "bottom", label: "Снизу", type: "text", default: "Прошло 4 часа" },
    { name: "font", label: "Шрифт", type: "select", default: "Oswald", options: ["Oswald", "Montserrat", "Alumni Sans", "Russo One"] },
    durationParam(3),
  ],
  build(p, at) {
    const end = at + dur(p, 3);
    const font = str(p, "font", "Oswald");
    const style = { font, weight: 700, size: 92, color: "#ffffff", stroke: { color: "#000000", width: 9 }, uppercase: true, maxWidth: 980, lineHeight: 1.05 };
    const out: Layer[] = [];
    const top = str(p, "top", "");
    const bottom = str(p, "bottom", "");
    if (top) out.push(layer("text", { start: at, end, y: 0.12, name: "Мем сверху", in: { type: "pop", duration: 0.25 }, out: { type: "fade", duration: 0.2 } }, { text: top, ...style }));
    if (bottom) out.push(layer("text", { start: at + 0.4, end, y: 0.86, name: "Мем снизу", in: { type: "pop", duration: 0.25 }, out: { type: "fade", duration: 0.2 } }, { text: bottom, ...style }));
    return { layers: out };
  },
};
