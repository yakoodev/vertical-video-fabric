/**
 * Готовые шаблоны графики: функция «параметры + момент клипа → слои».
 *
 * Шаблон не хранится в клипе — он лишь создаёт обычные слои, которые потом
 * правятся как любые другие. Поэтому всё, что тут задано, выражено только
 * через схему слоёв из спеки, без скрытых режимов.
 *
 * Визуальная планка — эфирная графика TikTok/CapCut: жирный капс с обводкой,
 * скошенные плашки, неоновое свечение, «пружинящие» появления.
 */
import { CAPTION_STYLES, DEFAULT_CAPTION_STYLE } from "./layers/captions";
import { CHAT_STYLES } from "./layers/chat";
import type { CameraKey, ChatStyle, Layer, LayerBase, LayerPropsMap, LayerType, Shadow } from "./types";

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
};

export const TEMPLATE_CATEGORIES = ["Титры", "Плашки", "Инфографика", "Призывы", "Акценты", "Списки", "Мемы"] as const;

/* ---------- кирпичики ---------- */

let idCounter = 0;

/** Новый id слоя по правилам спеки (^[A-Za-z0-9_-]{1,32}$). Случайность тут ок — это не кадр. */
export function newLayerId(): string {
  idCounter = (idCounter + 1) % 1296;
  const rnd = Math.floor(Math.random() * 36 ** 5).toString(36).padStart(5, "0");
  return `l_${rnd}${idCounter.toString(36).padStart(2, "0")}`;
}

type Base = Partial<Omit<LayerBase, "id">> & { start: number; end: number };

function layer<T extends LayerType>(type: T, base: Base, props: LayerPropsMap[T]): Layer {
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

const VIOLET = "#8B5CF6";
const VIOLET_DEEP = "#5B21B6";
const YELLOW = "#FFD400";
const RED = "#FF2D55";
const FONT = "Montserrat";

const SOFT: Shadow = { color: "#000000", blur: 28, x: 0, y: 10, opacity: 0.55 };
const glow = (color: string, blur = 40, opacity = 0.85): Shadow => ({ color, blur, x: 0, y: 0, opacity });

const str = (p: TemplateParams, k: string, d: string) => (p[k] === undefined || p[k] === "" ? d : String(p[k]));
const num = (p: TemplateParams, k: string, d: number) => {
  const v = Number(p[k]);
  return p[k] === undefined || p[k] === "" || !Number.isFinite(v) ? d : v;
};
const bool = (p: TemplateParams, k: string, d: boolean) => (p[k] === undefined ? d : p[k] === true || p[k] === "true");

const dur = (p: TemplateParams, d: number) => Math.max(0.5, num(p, "duration", d));
const durationParam = (d: number): TemplateParam => ({ name: "duration", label: "Длительность, с", type: "number", default: d, min: 0.5, max: 60 });

/** Искры вокруг заголовка: маленькие звёзды с разными задержками и миганием. */
function sparkles(at: number, end: number, cx: number, cy: number, color: string): Layer[] {
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

/* ---------- шаблоны ---------- */

const titlePop: MotionTemplate = {
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

const punchline: MotionTemplate = {
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

function lowerThird(style: "bar" | "box" | "pill" | "neon", name: string, description: string, accent: string, skew: number): MotionTemplate {
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

const subscribe: MotionTemplate = {
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

const lookHere: MotionTemplate = {
  id: "look-here",
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

const counter: MotionTemplate = {
  id: "counter",
  name: "Счётчик",
  category: "Инфографика",
  description: "Число накручивается до цели, под ним скошенная плашка с подписью",
  duration: 4,
  params: [
    { name: "from", label: "От", type: "number", default: 0 },
    { name: "to", label: "До", type: "number", default: 1000000 },
    { name: "label", label: "Подпись", type: "text", default: "подписчиков" },
    { name: "suffix", label: "Суффикс", type: "text", default: "" },
    { name: "color", label: "Цвет числа", type: "color", default: YELLOW },
    { name: "y", label: "Высота", type: "number", default: 0.4, min: 0, max: 1 },
    durationParam(4),
  ],
  build(p, at) {
    const end = at + dur(p, 4);
    const y = num(p, "y", 0.4);
    return {
      layers: [
        layer(
          "counter",
          { start: at, end, y, rotation: -4, name: "Счётчик", in: { type: "pop", duration: 0.4 }, out: { type: "fade", duration: 0.25 }, shadow: glow(str(p, "color", YELLOW), 50, 0.55) },
          {
            from: num(p, "from", 0),
            to: num(p, "to", 1000000),
            decimals: 0,
            thousands: " ",
            suffix: str(p, "suffix", ""),
            font: FONT,
            weight: 900,
            size: 150,
            color: str(p, "color", YELLOW),
            gradient: [ "#FFF27A", str(p, "color", YELLOW)],
            stroke: { color: "#1A0B3D", width: 10 },
            maxWidth: 980,
          },
        ),
        layer(
          "text",
          { start: at + 0.25, end, y: y + 0.075, rotation: -4, track: 1, name: "Подпись", in: { type: "wipe", duration: 0.4 }, out: { type: "fade", duration: 0.25 }, shadow: SOFT },
          {
            text: str(p, "label", "подписчиков"),
            font: FONT,
            weight: 900,
            size: 64,
            color: "#ffffff",
            uppercase: true,
            bg: { color: VIOLET, opacity: 1, radius: 10, padX: 34, padY: 12, skew: -12, gradient: [VIOLET_DEEP, VIOLET] },
          },
        ),
      ],
    };
  },
};

const progressBar: MotionTemplate = {
  id: "progress",
  name: "Прогресс",
  category: "Инфографика",
  description: "Заголовок, крупный процент и светящаяся полоса загрузки",
  duration: 4,
  params: [
    { name: "title", label: "Заголовок", type: "text", default: "Загрузка" },
    { name: "value", label: "Процент", type: "number", default: 86, min: 0, max: 100 },
    { name: "color", label: "Цвет", type: "color", default: VIOLET },
    { name: "y", label: "Высота", type: "number", default: 0.45, min: 0, max: 1 },
    durationParam(4),
  ],
  build(p, at) {
    const end = at + dur(p, 4);
    const y = num(p, "y", 0.45);
    const value = Math.max(0, Math.min(100, num(p, "value", 86)));
    const color = str(p, "color", VIOLET);
    const anim = { in: { type: "pop" as const, duration: 0.4 }, out: { type: "fade" as const, duration: 0.25 } };
    return {
      layers: [
        layer("text", { start: at, end, y: y - 0.13, name: "Заголовок", ...anim, shadow: SOFT }, { text: str(p, "title", "Загрузка"), font: FONT, weight: 900, size: 84, color: "#ffffff", uppercase: true, letterSpacing: 2 }),
        layer(
          "counter",
          { start: at, end, y: y - 0.055, name: "Процент", ...anim, shadow: glow(color, 40, 0.8) },
          { from: 0, to: value, suffix: "%", font: FONT, weight: 900, size: 150, color: "#ffffff", gradient: ["#ffffff", "#E9D5FF"] },
        ),
        layer(
          "progress",
          { start: at, end, y: y + 0.03, name: "Полоса", in: { type: "zoom", duration: 0.4 }, out: { type: "fade", duration: 0.25 } },
          { w: 780, h: 76, value: value / 100, track: "#ffffff1f", fill: [shadeHex(color, -0.25), shadeHex(color, 0.35)], radius: 38, label: false, glow: true },
        ),
      ],
    };
  },
};

const barChart: MotionTemplate = {
  id: "bar-chart",
  name: "Столбчатый график",
  category: "Инфографика",
  description: "Столбцы растут волной, сверху взлетает стрелка роста",
  duration: 5,
  params: [
    { name: "title", label: "Заголовок", type: "text", default: "Рост за 4 месяца" },
    { name: "labels", label: "Подписи через запятую", type: "text", default: "Янв, Фев, Мар, Апр" },
    { name: "values", label: "Значения через запятую", type: "text", default: "120, 210, 340, 560" },
    { name: "arrow", label: "Стрелка роста", type: "bool", default: true },
    durationParam(5),
  ],
  build(p, at) {
    const end = at + dur(p, 5);
    const data = parseData(str(p, "labels", "Янв, Фев, Мар, Апр"), str(p, "values", "120, 210, 340, 560"));
    const layers = [
      layer(
        "chart",
        { start: at, end, y: 0.45, name: "График", in: { type: "zoom", duration: 0.4 }, out: { type: "fade", duration: 0.3 }, shadow: SOFT },
        { kind: "bar", data, w: 900, h: 760, palette: ramp("#6D28D9", "#C084FC", data.length), title: str(p, "title", "Рост за 4 месяца"), showValues: true, glow: true },
      ),
    ];
    if (bool(p, "arrow", true)) {
      layers.push(
        layer(
          "shape",
          { start: at + 0.9, end, track: 1, x: 0.5, y: 0.405, rotation: -30, name: "Стрелка роста", in: { type: "draw", duration: 0.7 }, out: { type: "fade", duration: 0.3 }, shadow: glow(YELLOW, 30, 0.9) },
          { kind: "arrow", w: 660, fill: null, stroke: { color: YELLOW, width: 18 }, bend: 60 },
        ),
      );
    }
    return { layers };
  },
};

const donutChart: MotionTemplate = {
  id: "donut-chart",
  name: "Кольцевая диаграмма",
  category: "Инфографика",
  description: "Кольцо заполняется до нужной доли, в центре — крупный процент",
  duration: 4,
  params: [
    { name: "value", label: "Процент", type: "number", default: 73, min: 0, max: 100 },
    { name: "label", label: "Подпись в центре", type: "text", default: "Результат" },
    { name: "title", label: "Заголовок", type: "text", default: "" },
    { name: "color", label: "Цвет", type: "color", default: VIOLET },
    durationParam(4),
  ],
  build(p, at) {
    const end = at + dur(p, 4);
    const v = Math.max(0, Math.min(100, num(p, "value", 73)));
    return {
      layers: [
        layer(
          "chart",
          { start: at, end, y: 0.42, name: "Диаграмма", in: { type: "zoom", duration: 0.4 }, out: { type: "fade", duration: 0.3 } },
          {
            kind: "donut",
            data: [
              { label: str(p, "label", "Результат"), value: v, color: str(p, "color", VIOLET) },
              { label: "Остальное", value: 100 - v, color: "#ffffff1a" },
            ],
            w: 760,
            h: 760,
            title: str(p, "title", ""),
            centerLabel: str(p, "label", "Результат"),
            panel: null,
            glow: true,
          },
        ),
      ],
    };
  },
};

const pieChart: MotionTemplate = {
  id: "pie-chart",
  name: "Круговая диаграмма",
  category: "Инфографика",
  description: "Пирог раскрывается по кругу, проценты на долях и легенда снизу",
  duration: 5,
  params: [
    { name: "title", label: "Заголовок", type: "text", default: "На что уходит время" },
    { name: "labels", label: "Подписи через запятую", type: "text", default: "Игры, Стримы, Сон, Еда" },
    { name: "values", label: "Значения через запятую", type: "text", default: "40, 25, 25, 10" },
    durationParam(5),
  ],
  build(p, at) {
    const end = at + dur(p, 5);
    return {
      layers: [
        layer(
          "chart",
          { start: at, end, y: 0.45, name: "Диаграмма", in: { type: "zoom", duration: 0.4 }, out: { type: "fade", duration: 0.3 }, shadow: SOFT },
          {
            kind: "pie",
            data: parseData(str(p, "labels", "Игры, Стримы, Сон, Еда"), str(p, "values", "40, 25, 25, 10")),
            w: 860,
            h: 1180,
            title: str(p, "title", "На что уходит время"),
            showValues: true,
            glow: false,
          },
        ),
      ],
    };
  },
};

const beforeAfter: MotionTemplate = {
  id: "before-after",
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

const quoteCard: MotionTemplate = {
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

const countdown: MotionTemplate = {
  id: "countdown",
  name: "Таймер",
  category: "Акценты",
  description: "Обратный отсчёт в светящемся кольце, которое убывает",
  duration: 10,
  params: [
    { name: "seconds", label: "Секунд", type: "number", default: 10, min: 1, max: 60 },
    { name: "label", label: "Подпись", type: "text", default: "секунд" },
    { name: "color", label: "Цвет", type: "color", default: "#22D3EE" },
  ],
  build(p, at) {
    const secs = Math.max(1, Math.min(60, Math.round(num(p, "seconds", 10))));
    const end = at + secs + 0.6;
    const color = str(p, "color", "#22D3EE");
    const run = [{ t: 0.3, v: 1, ease: "linear" as const }, { t: 0.3 + secs, v: 0 }];
    const anim = { in: { type: "pop" as const, duration: 0.35 }, out: { type: "fade" as const, duration: 0.25 } };
    return {
      layers: [
        layer("shape", { start: at, end, y: 0.42, name: "Дорожка", ...anim }, { kind: "ellipse", w: 440, h: 440, fill: "#0B1220CC", stroke: { color: "#ffffff22", width: 22 } }),
        layer(
          "shape",
          { start: at, end, track: 1, y: 0.42, name: "Кольцо", ...anim, keys: { progress: run }, shadow: glow(color, 34, 0.9) },
          { kind: "ellipse", w: 440, h: 440, fill: null, stroke: { color, width: 22 } },
        ),
        layer(
          "counter",
          { start: at, end, track: 2, y: 0.415, name: "Цифры", ...anim, keys: { progress: [{ t: 0.3, v: 0, ease: "linear" }, { t: 0.3 + secs, v: 1 }] }, shadow: glow(color, 30, 0.6) },
          { from: secs, to: 0, decimals: 0, font: FONT, weight: 900, size: 190, color: "#ffffff" },
        ),
        layer("text", { start: at, end, track: 2, y: 0.475, name: "Подпись", ...anim }, { text: str(p, "label", "секунд"), font: FONT, weight: 800, size: 40, color, uppercase: true, letterSpacing: 4 }),
      ],
    };
  },
};

const top3: MotionTemplate = {
  id: "top-3",
  name: "ТОП-3",
  category: "Списки",
  description: "Заголовок и три пункта с номерами — выезжают по очереди",
  duration: 6,
  params: [
    { name: "title", label: "Заголовок", type: "text", default: "ТОП-3" },
    { name: "item1", label: "Пункт 1", type: "text", default: "Лучший выбор" },
    { name: "item2", label: "Пункт 2", type: "text", default: "Высокое качество" },
    { name: "item3", label: "Пункт 3", type: "text", default: "Отличный результат" },
    { name: "accent", label: "Акцент", type: "color", default: YELLOW },
    durationParam(6),
  ],
  build(p, at) {
    const end = at + dur(p, 6);
    const accent = str(p, "accent", YELLOW);
    const out = { type: "fade" as const, duration: 0.3 };
    const layers: Layer[] = [
      layer(
        "text",
        { start: at, end, y: 0.27, rotation: -3, name: "Заголовок", in: { type: "pop", duration: 0.4 }, out, shadow: SOFT },
        { text: str(p, "title", "ТОП-3"), font: FONT, weight: 900, size: 160, color: accent, stroke: { color: "#000000", width: 12 }, uppercase: true },
      ),
    ];
    const plateW = 820;
    [str(p, "item1", "Лучший выбор"), str(p, "item2", "Высокое качество"), str(p, "item3", "Отличный результат")].forEach((item, i) => {
      const y = 0.39 + i * 0.085;
      const start = at + 0.35 + i * 0.25;
      const anim = { in: { type: "slideLeft" as const, duration: 0.4 }, out };
      layers.push(
        layer("shape", { start, end, y, name: `Плашка ${i + 1}`, ...anim, shadow: glow(VIOLET, 24, 0.6) }, { kind: "rect", w: plateW, h: 128, fill: "#1A1036E6", stroke: { color: VIOLET, width: 4 }, radius: 22 }),
        layer(
          "text",
          { start, end, track: 1, x: 0.5 - (plateW / 2 - 74) / 1080, y, name: `Номер ${i + 1}`, ...anim },
          { text: `0${i + 1}`, font: FONT, weight: 900, size: 64, color: "#111111", bg: { color: accent, opacity: 1, radius: 16, padX: 18, padY: 16 } },
        ),
        layer(
          "text",
          { start, end, track: 1, x: 0.5 + 70 / 1080, y, name: `Пункт ${i + 1}`, ...anim },
          { text: item, font: FONT, weight: 800, size: 46, color: "#ffffff", align: "left", boxWidth: 630, maxWidth: 630 },
        ),
      );
    });
    return { layers };
  },
};

const alertBanner: MotionTemplate = {
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

const boom: MotionTemplate = {
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

const captions: MotionTemplate = {
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

const streamChat: MotionTemplate = {
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

const keywordBig: MotionTemplate = {
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

const memeCaption: MotionTemplate = {
  id: "meme-caption",
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

const newsTicker: MotionTemplate = {
  id: "news-ticker",
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

/* ---------- утилиты ---------- */

function parseData(labels: string, values: string) {
  const ls = labels.split(",").map((s) => s.trim());
  const vs = values.split(",").map((s) => Number(s.trim().replace(",", ".")));
  const n = Math.min(12, Math.max(ls.length, vs.length));
  return Array.from({ length: n }, (_, i) => ({ label: ls[i] ?? "", value: Number.isFinite(vs[i]) ? vs[i] : 0 }));
}

/** n цветов плавно от `a` к `b` — столбцы одного тона, светлеющие к росту. */
function ramp(a: string, b: string, n: number): string[] {
  const pa = parseInt(a.slice(1, 7), 16);
  const pb = parseInt(b.slice(1, 7), 16);
  return Array.from({ length: Math.max(1, n) }, (_, i) => {
    const k = n > 1 ? i / (n - 1) : 1;
    const ch = (sh: number) => Math.round(((pa >> sh) & 255) + (((pb >> sh) & 255) - ((pa >> sh) & 255)) * k);
    return `#${[16, 8, 0].map((sh) => ch(sh).toString(16).padStart(2, "0")).join("")}`;
  });
}

/** Осветлить (k > 0) или затемнить (k < 0) hex-цвет — для пар градиентов. */
function shadeHex(color: string, k: number): string {
  const m = /^#([0-9a-f]{6})/i.exec(color);
  if (!m) return color;
  const v = parseInt(m[1], 16);
  const t = k > 0 ? 255 : 0;
  const ch = (c: number) => Math.round(c + (t - c) * Math.abs(k)).toString(16).padStart(2, "0");
  return `#${ch((v >> 16) & 255)}${ch((v >> 8) & 255)}${ch(v & 255)}`;
}

export const TEMPLATES: MotionTemplate[] = [
  titlePop,
  captions,
  keywordBig,
  streamChat,
  punchline,
  quoteCard,
  memeCaption,
  lowerThird("bar", "Нижняя треть · полоса", "Тёмная плашка с цветной полосой и значком", VIOLET, -10),
  lowerThird("box", "Нижняя треть · эфир", "Заголовок на цветной плашке, подзаголовок — на белой", VIOLET, -10),
  lowerThird("pill", "Нижняя треть · таблетка", "Светлая округлая плашка со значком в кружке", RED, 0),
  lowerThird("neon", "Нижняя треть · неон", "Тёмное стекло с неоновой обводкой", "#22D3EE", 0),
  newsTicker,
  counter,
  progressBar,
  barChart,
  donutChart,
  pieChart,
  subscribe,
  lookHere,
  beforeAfter,
  countdown,
  alertBanner,
  boom,
  top3,
];

export function getTemplate(id: string): MotionTemplate | undefined {
  return TEMPLATES.find((t) => t.id === id);
}

/** Собрать шаблон с параметрами по умолчанию, переопределёнными `params`. */
export function buildTemplate(id: string, params: TemplateParams, at: number): TemplateResult | null {
  const tpl = getTemplate(id);
  if (!tpl) return null;
  const merged: TemplateParams = {};
  for (const prm of tpl.params) merged[prm.name] = prm.default;
  return tpl.build({ ...merged, ...params }, at);
}
