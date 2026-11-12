// Шаблоны: инфографика (счётчик, прогресс, диаграммы, отсчёт, топ-3).
import type { Layer } from "../types";
import {
  bool,
  dur,
  durationParam,
  FONT,
  glow,
  layer,
  type MotionTemplate,
  num,
  parseData,
  ramp,
  shadeHex,
  SOFT,
  str,
  VIOLET,
  VIOLET_DEEP,
  YELLOW,
} from "./kit";
import { flatCounterFromLegacy, flatProgressFromLegacy, flatTop3FromLegacy, isFlat, legacyThemeParam } from "./flat";

export const counter: MotionTemplate = {
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
    legacyThemeParam,
  ],
  build(p, at) {
    const end = at + dur(p, 4);
    if (isFlat(p)) return { layers: flatCounterFromLegacy(p, at, end) };
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

export const progressBar: MotionTemplate = {
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
    legacyThemeParam,
  ],
  build(p, at) {
    const end = at + dur(p, 4);
    if (isFlat(p)) return { layers: flatProgressFromLegacy(p, at, end) };
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

export const barChart: MotionTemplate = {
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

export const donutChart: MotionTemplate = {
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

export const pieChart: MotionTemplate = {
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

export const countdown: MotionTemplate = {
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

export const top3: MotionTemplate = {
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
    legacyThemeParam,
  ],
  build(p, at) {
    const end = at + dur(p, 6);
    if (isFlat(p)) return { layers: flatTop3FromLegacy({ ...p, accent: "" }, at, end) };
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
