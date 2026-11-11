// Шаблоны рекламного пакета, часть 2: экран с наклоном и наездом, волна голоса, стена плиток,
// схема безопасной сделки, фраза по словам в карточке (караоке субтитров клипа в рамке).
import { AD_ACCENT, AD_FONT, mixHex } from "../layers/adkit";
import type { FlowNode, KeyPoint, Layer, LayerKeys, LayerPivot, WaveformStyle } from "../types";
import { bool, dur, durationParam, glow, layer, type MotionTemplate, num, str } from "./kit";
import { AD_CATEGORY, accentParam, parseRows, themeOf, themeParam } from "./ads";

/* ---------- наклон экрана с наездом ---------- */

const SOFT_EASE = "bezier(0.45,0,0.55,1)";

/**
 * Ключи «экран наклонён и выпрямляется, пока камера наезжает в точку»: rotX/rotY и scale (вокруг
 * pivot). Те же числа пишет app/camera3d.py (пресет screen_tilt) для готовой группы слоёв.
 */
export function screenTiltKeys(len: number, tilt: number, zoom: number): Pick<LayerKeys, "rotX" | "rotY" | "scale"> {
  const t1 = Math.min(len * 0.35, 1.4);
  const t2 = Math.min(len * 0.8, t1 + 1.6);
  const r = (v: number) => Math.round(v * 100) / 100;
  const k = (pts: [number, number][]): KeyPoint[] => pts.map(([t, v], i) => ({ t: r(t), v: r(v), ...(i < pts.length - 1 ? { ease: SOFT_EASE } : {}) }));
  return {
    rotX: k([[0, tilt], [t1, tilt * 0.45], [t2, tilt * 0.12]]),
    rotY: k([[0, -tilt * 0.6], [t1, -tilt * 0.28], [t2, tilt * 0.1]]),
    scale: k([[0, 0.9], [t1, 1], [t2, zoom]]),
  };
}

export const screenTilt: MotionTemplate = {
  id: "screen-tilt",
  name: "Экран с наклоном и наездом",
  category: AD_CATEGORY,
  description: "Интерфейс наклонён в 3D и выпрямляется, пока камера наезжает на кнопку — одной группой",
  duration: 5,
  params: [
    { name: "title", label: "Заголовок экрана", type: "text", default: "Каталог" },
    { name: "rows", label: "Строки: название | цена; …", type: "text", default: "Аккаунт Pro | 1 490 ₽; Набор скинов | 690 ₽; Ключ игры | 2 290 ₽" },
    { name: "button", label: "Кнопка", type: "text", default: "Купить безопасно" },
    { name: "tilt", label: "Наклон, градусы", type: "number", default: 24, min: 0, max: 45 },
    { name: "zoom", label: "Наезд в кнопку, раз", type: "number", default: 1.35, min: 1, max: 2.5 },
    accentParam,
    durationParam(5),
  ],
  build(p, at) {
    const len = dur(p, 5);
    const end = at + len;
    const accent = str(p, "accent", AD_ACCENT);
    const tilt = num(p, "tilt", 24);
    const keys = screenTiltKeys(len, tilt, num(p, "zoom", 1.35));
    const pivot: LayerPivot = { x: 0.5, y: 0.66 };
    const group = (extra: Partial<Layer>, i: number) => ({
      start: at + (i > 0 ? 0.12 + i * 0.06 : 0),
      end,
      pivot,
      keys,
      out: { type: "fade" as const, duration: 0.3 },
      ...extra,
    });
    const layers: Layer[] = [
      layer("shape", { ...group({ y: 0.48, track: 1, name: "Экран", in: { type: "fade", duration: 0.35 }, shadow: { color: "#000000", blur: 60, x: 0, y: 30, opacity: 0.6 } }, 0) }, {
        kind: "rect", w: 800, h: 1180, radius: 56, fill: "#12141c", stroke: { color: "#ffffff1f", width: 2 },
      }),
      layer("text", { ...group({ y: 0.225, track: 2, name: "Заголовок экрана", in: { type: "fade", duration: 0.35 } }, 1) }, {
        text: str(p, "title", "Каталог"), font: AD_FONT, weight: 800, size: 60, color: "#ffffff",
      }),
    ];
    parseRows(str(p, "rows", ""), 3).forEach(([name, price], i) => {
      const y = 0.33 + i * 0.095;
      // Строка въезжает целиком: плашка, значок, название и цена — одним стартом.
      const n = 2 + i * 3;
      layers.push(
        layer("shape", { ...group({ y, track: 2, name: `Строка ${i + 1}`, in: { type: "slideUp", duration: 0.4 } }, n) }, {
          kind: "rect", w: 700, h: 156, radius: 34, fill: "#1c1f2b", stroke: { color: "#ffffff14", width: 2 },
        }),
        layer("shape", { ...group({ x: 0.262, y, track: 3, name: `Значок ${i + 1}`, in: { type: "slideUp", duration: 0.4 } }, n) }, {
          kind: "rect", w: 96, h: 96, radius: 26, fill: mixHex(accent, i % 2 ? "#ff5c8a" : "#22b8e6", 0.35 + 0.2 * i), stroke: null,
        }),
        layer("text", { ...group({ x: 0.52, y, track: 3, name: `Название ${i + 1}`, in: { type: "slideUp", duration: 0.4 } }, n) }, {
          text: name || "", font: AD_FONT, weight: 700, size: 40, color: "#f2f3f7", align: "left", boxWidth: 400, maxWidth: 400,
        }),
        layer("text", { ...group({ x: 0.735, y, track: 3, name: `Цена ${i + 1}`, in: { type: "slideUp", duration: 0.4 } }, n) }, {
          text: price || "", font: AD_FONT, weight: 800, size: 40, color: mixHex(accent, "#ffffff", 0.55), align: "right", boxWidth: 220, maxWidth: 220,
        }),
      );
    });
    layers.push(
      layer("shape", { ...group({ y: 0.66, track: 2, name: "Кнопка", in: { type: "pop", duration: 0.45 }, shadow: glow(accent, 40, 0.6) }, 11) }, {
        kind: "rect", w: 700, h: 136, radius: 68, fill: accent, stroke: null,
      }),
      layer("text", { ...group({ y: 0.66, track: 3, name: "Текст кнопки", in: { type: "pop", duration: 0.45 } }, 11) }, {
        text: str(p, "button", "Купить безопасно"), font: AD_FONT, weight: 800, size: 48, color: "#ffffff",
      }),
    );
    return { layers };
  },
};

/* ---------- волна голоса ---------- */

const WAVE_STYLE: Record<string, WaveformStyle> = { столбики: "bars", волна: "wave", точки: "dots" };

export const voiceWave: MotionTemplate = {
  id: "voice-wave",
  name: "Волна голоса",
  category: AD_CATEGORY,
  description: "Столбики/волна двигаются с настоящей громкостью звукового слоя (или образцом) в стеклянной плашке",
  duration: 5,
  params: [
    { name: "style", label: "Вид", type: "select", default: "столбики", options: Object.keys(WAVE_STYLE) },
    { name: "audio_layer", label: "Звуковой слой (id; пусто — образец)", type: "text", default: "" },
    { name: "bars", label: "Столбиков", type: "number", default: 36, min: 8, max: 96 },
    { name: "plate", label: "Стеклянная плашка", type: "bool", default: true },
    accentParam,
    { name: "y", label: "Высота", type: "number", default: 0.6, min: 0.1, max: 0.9 },
    durationParam(5),
  ],
  build(p, at) {
    const end = at + dur(p, 5);
    const y = num(p, "y", 0.6);
    const accent = str(p, "accent", AD_ACCENT);
    const audio = str(p, "audio_layer", "").trim();
    const layers: Layer[] = [];
    if (bool(p, "plate", true))
      layers.push(
        layer("shape", { start: at, end, y, track: 1, name: "Плашка волны", in: { type: "zoom", duration: 0.4 }, out: { type: "fade", duration: 0.3 }, shadow: { color: "#000000", blur: 40, x: 0, y: 16, opacity: 0.45 } }, {
          kind: "rect", w: 900, h: 250, radius: 125, fill: "#161821e6", stroke: { color: mixHex(accent, "#ffffff", 0.3) + "66", width: 2 },
        }),
      );
    layers.push(
      layer("waveform", { start: at + 0.1, end, y, track: 2, name: "Волна голоса", in: { type: "fade", duration: 0.3 }, out: { type: "fade", duration: 0.3 } }, {
        style: WAVE_STYLE[str(p, "style", "столбики")] ?? "bars",
        ...(audio ? { audio_layer: audio } : {}),
        bars: Math.round(num(p, "bars", 36)),
        w: 760,
        h: 170,
        gradient: [mixHex(accent, "#ffffff", 0.5), accent],
      }),
    );
    return { layers };
  },
};

/* ---------- стена плиток ---------- */

export const tileWallTpl: MotionTemplate = {
  id: "tile-wall",
  name: "Стена плиток",
  category: AD_CATEGORY,
  description: "Наклонная стена значков/обложек бесконечно едет по диагонали, сверху затемнение и заголовок",
  duration: 6,
  params: [
    { name: "title", label: "Заголовок", type: "text", default: "Всё в одном приложении" },
    { name: "subtitle", label: "Подзаголовок", type: "text", default: "игры, подписки и ключи — без риска" },
    { name: "images", label: "Картинки плиток: id файлов через запятую (пусто — свои значки)", type: "text", default: "" },
    { name: "tilt", label: "Наклон вглубь, градусы", type: "number", default: 30, min: 0, max: 45 },
    { name: "speed", label: "Скорость, px/с", type: "number", default: 46, min: 0, max: 400 },
    { name: "overlay", label: "Затемнение 0..1", type: "number", default: 0.66, min: 0, max: 1 },
    durationParam(6),
  ],
  build(p, at) {
    const end = at + dur(p, 6);
    const images = str(p, "images", "").split(",").map((s) => s.trim()).filter(Boolean);
    const layers: Layer[] = [
      layer("tile_wall", { start: at, end, track: 0, name: "Стена плиток", in: { type: "fade", duration: 0.5 }, out: { type: "fade", duration: 0.4 } }, {
        ...(images.length ? { images } : {}),
        tiltX: num(p, "tilt", 30),
        speed: num(p, "speed", 46),
        overlay: num(p, "overlay", 0.66),
      }),
    ];
    const title = str(p, "title", "");
    if (title)
      layers.push(
        layer("text", { start: at + 0.4, end, y: 0.46, track: 2, name: "Заголовок", in: { type: "rise", duration: 0.6 }, out: { type: "fade", duration: 0.3 } }, {
          text: title, font: AD_FONT, weight: 800, size: 104, color: "#ffffff", maxWidth: 900, lineHeight: 1.05, backdrop: { color: "#05060a", opacity: 0.55 },
        }),
      );
    const sub = str(p, "subtitle", "");
    if (sub)
      layers.push(
        layer("text", { start: at + 0.8, end, y: 0.56, track: 2, name: "Подзаголовок", in: { type: "rise", duration: 0.5 }, out: { type: "fade", duration: 0.3 } }, {
          text: sub, font: AD_FONT, weight: 600, size: 46, color: "#c9ccd6", maxWidth: 860,
        }),
      );
    return { layers };
  },
};

/* ---------- схема сделки ---------- */

export const dealFlow: MotionTemplate = {
  id: "deal-flow",
  name: "Схема безопасной сделки",
  category: AD_CATEGORY,
  description: "Покупатель → щит с замком → продавец: пунктир тянется по шагам, в конце «Готово ✓»",
  duration: 5,
  params: [
    { name: "nodes", label: "Узлы: подпись | пояснение | значок; …", type: "text", default: "Покупатель | оплачивает | user; Сервис | держит деньги | shield; Продавец | получает после проверки | store" },
    { name: "done", label: "Итог (пусто — без плашки)", type: "text", default: "Сделка завершена" },
    { name: "stepGap", label: "Шаг, с", type: "number", default: 0.8, min: 0.3, max: 3 },
    accentParam,
    themeParam,
    { name: "y", label: "Высота", type: "number", default: 0.5, min: 0.2, max: 0.8 },
    durationParam(5),
  ],
  build(p, at) {
    const nodes: FlowNode[] = parseRows(str(p, "nodes", ""), 6).map(([label, sub, icon]) => ({ label: label || "", sub: sub || "", ...(icon ? { icon } : {}) }));
    return {
      layers: [
        layer("flow_diagram", { start: at, end: at + dur(p, 5), y: num(p, "y", 0.5), track: 2, name: "Схема сделки", out: { type: "fade", duration: 0.3 } }, {
          nodes,
          done: str(p, "done", ""),
          stepGap: num(p, "stepGap", 0.8),
          accent: str(p, "accent", AD_ACCENT),
          theme: themeOf(p),
          font: AD_FONT,
        }),
      ],
    };
  },
};

/* ---------- фраза по словам в карточке ---------- */

export const karaokeCard: MotionTemplate = {
  id: "karaoke-card",
  name: "Фраза по словам в карточке",
  category: AD_CATEGORY,
  description: "Сказанные слова озвучки подсвечиваются по одному внутри стеклянной карточки (субтитры клипа)",
  duration: 5,
  params: [
    { name: "label", label: "Подпись над фразой", type: "text", default: "Пример голоса" },
    { name: "wave", label: "Волна голоса в карточке", type: "bool", default: true },
    accentParam,
    { name: "y", label: "Высота", type: "number", default: 0.62, min: 0.2, max: 0.75 },
    durationParam(5),
  ],
  build(p, at) {
    const end = at + dur(p, 5);
    const y = num(p, "y", 0.62);
    const accent = str(p, "accent", AD_ACCENT);
    const label = str(p, "label", "");
    const layers: Layer[] = [
      layer("shape", { start: at, end, y, track: 1, name: "Карточка фразы", in: { type: "zoom", duration: 0.4 }, out: { type: "fade", duration: 0.3 }, shadow: { color: "#000000", blur: 50, x: 0, y: 20, opacity: 0.5 } }, {
        kind: "rect", w: 940, h: 420, radius: 48, fill: "#161821eb", stroke: { color: mixHex(accent, "#ffffff", 0.3) + "55", width: 2 },
      }),
      // Слова — из субтитров клипа (comp.words): звучащее — акцентом, сказанные — белым, впереди — бледно.
      layer("captions", { start: at, end, y: y + 0.03, track: 3, name: "Фраза по словам" }, {
        style: "karaoke",
        animation: "karaoke",
        font: AD_FONT,
        weight: 800,
        size: 66,
        color: "#ffffff",
        dimColor: "#ffffff52",
        activeColor: mixHex(accent, "#ffffff", 0.35),
        stroke: null,
        bg: null,
        glow: null,
        uppercase: false,
        maxWords: 6,
        maxLines: 2,
        maxWidth: 820,
        wordScale: 1.06,
      }),
    ];
    if (label)
      layers.push(
        layer("text", { start: at + 0.2, end, y: y - 0.083, track: 2, name: "Подпись фразы", in: { type: "fade", duration: 0.3 }, out: { type: "fade", duration: 0.3 } }, {
          text: label.toLocaleUpperCase("ru-RU"), font: AD_FONT, weight: 700, size: 30, color: mixHex(accent, "#ffffff", 0.55), letterSpacing: 4,
        }),
      );
    if (bool(p, "wave", true))
      layers.push(
        layer("waveform", { start: at + 0.2, end, x: 0.73, y: y - 0.083, track: 2, name: "Волна фразы", in: { type: "fade", duration: 0.3 }, out: { type: "fade", duration: 0.3 } }, {
          source: "voice", bars: 18, w: 220, h: 56, color: accent, glow: false,
        }),
      );
    return { layers };
  },
};
