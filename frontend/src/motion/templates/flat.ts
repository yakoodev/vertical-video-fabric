// Шаблоны «Плоский UI»: слой `ui` на токенах плоской темы (flat/) + фон под плашками (fx backdrop).
// Тема — параметр `theme` (red | dark | light | brand), акцент и основа бренда — `accent` / `plate`.
import type { FlatItem, FlatUiProps } from "../flat/kit";
import { FLAT_THEMES } from "../flat/tokens";
import type { Layer } from "../types";
import { bool, dur, durationParam, layer, type MotionTemplate, num, str, type TemplateParam, type TemplateParams } from "./kit";

export const FLAT_CATEGORY = "Плоский UI";
/** Темы шаблонов; `classic` у старых шаблонов — прежний вид (без плоской темы). */
export const THEME_OPTIONS = [...FLAT_THEMES];

export const themeParam = (d = "dark"): TemplateParam => ({ name: "theme", label: "Тема (red/dark/light/brand)", type: "select", default: d, options: [...THEME_OPTIONS] });
/** У старых шаблонов: classic — прежний вид, остальное — плоская тема. */
export const legacyThemeParam: TemplateParam = { name: "theme", label: "Тема (classic — прежний вид)", type: "select", default: "classic", options: ["classic", ...THEME_OPTIONS] };
const accentParam: TemplateParam = { name: "accent", label: "Акцент #hex (пусто — по теме)", type: "text", default: "" };
const plateParam: TemplateParam = { name: "plate", label: "Основа бренда #hex (тема brand)", type: "text", default: "" };
const backdropParam = (d: boolean): TemplateParam => ({ name: "backdrop", label: "Размыть и притушить фон под плашкой", type: "bool", default: d });
const yParam = (d: number): TemplateParam => ({ name: "y", label: "Высота", type: "number", default: d, min: 0, max: 1 });

/** Выбрана ли плоская тема (а не прежний вид). */
export const isFlat = (p: TemplateParams) => THEME_OPTIONS.includes(str(p, "theme", "classic") as (typeof THEME_OPTIONS)[number]);

/** Общие поля темы для пропсов ui. */
export function themeProps(p: TemplateParams, def = "dark"): Pick<FlatUiProps, "theme" | "accent" | "plate"> {
  const theme = str(p, "theme", def);
  const hex = (k: string) => (/^#[0-9a-f]{6}$/i.test(str(p, k, "").trim()) ? str(p, k, "").trim() : null);
  return { theme: THEME_OPTIONS.includes(theme as (typeof THEME_OPTIONS)[number]) ? theme : def, accent: hex("accent"), plate: hex("plate") };
}

const TRACK_FX = 4;
const TRACK_UI = 5;

/** Фон под плашками: всё ниже размыто и притушено, наплыв 0.3 с. */
export function backdropLayer(start: number, end: number, blur = 22, dim = 0.45): Layer {
  return layer(
    "fx",
    { start, end, track: TRACK_FX, name: "Фон под плашкой", in: { type: "fade", duration: 0.3 }, out: { type: "fade", duration: 0.25 } },
    { kind: "backdrop", intensity: dim, size: blur, speed: 1 },
  );
}

/** Слой плоского интерфейса с появлением/уходом по умолчанию. */
export function uiLayer(props: FlatUiProps, start: number, end: number, y: number, name: string, inType: "pop" | "slideUp" | "slideDown" | "none" | "fade" | "zoom" = "pop"): Layer {
  return layer(
    "ui",
    { start, end, y, track: TRACK_UI, name, in: inType === "none" ? null : { type: inType, duration: 0.35 }, out: { type: "fade", duration: 0.25 } },
    props,
  );
}

/** «Текст | подпись; текст | подпись» → пункты (до 10). */
export function parseItems(src: string): FlatItem[] {
  return src
    .split(/[;\n]/)
    .map((s) => s.trim())
    .filter(Boolean)
    .slice(0, 10)
    .map((row) => {
      const [text, sub] = row.split("|").map((s) => s.trim());
      return sub ? { text, sub } : { text };
    });
}

/** «Текст | значение | заметка; …» → полосы сравнения. */
export function parseBars(src: string): FlatItem[] {
  return src
    .split(/[;\n]/)
    .map((s) => s.trim())
    .filter(Boolean)
    .slice(0, 8)
    .map((row) => {
      const [text, value, note] = row.split("|").map((s) => s.trim());
      const v = Number(String(value ?? "").replace(",", "."));
      return { text, value: Number.isFinite(v) ? v : 0, ...(note ? { note } : {}) };
    });
}

/** «me: текст; them: текст» (или «я:»/«он:») → сообщения окна-чата. */
export function parseMessages(src: string): FlatItem[] {
  return src
    .split(/[;\n]/)
    .map((s) => s.trim())
    .filter(Boolean)
    .slice(0, 10)
    .map((row, i) => {
      const m = /^(me|я|them|он|она|бот|bot|ии)\s*:\s*(.*)$/i.exec(row);
      const me = m ? /^(me|я)$/i.test(m[1]) : i % 2 === 0;
      return { text: m ? m[2] : row, from: me ? "me" : "them" };
    });
}

function withBackdrop(p: TemplateParams, start: number, end: number, layers: Layer[], def: boolean): Layer[] {
  return bool(p, "backdrop", def) ? [backdropLayer(start, end), ...layers] : layers;
}

/* ---------- шаблоны ---------- */

export const flatHook: MotionTemplate = {
  id: "flat-hook",
  name: "Хук-слово во весь кадр",
  category: FLAT_CATEGORY,
  description: "Огромное слово во всю ширину с первого кадра (заставка темы или поверх видео)",
  duration: 1.2,
  params: [
    { name: "word", label: "Слово (\\n — строки)", type: "text", default: "НАБИРАЮТ" },
    { name: "sub", label: "Подпись", type: "text", default: "" },
    { name: "fill", label: "Заставка темы во весь кадр", type: "bool", default: true },
    { name: "accentLast", label: "Последняя строка акцентом", type: "bool", default: false },
    themeParam("red"),
    accentParam,
    plateParam,
    yParam(0.5),
    durationParam(1.2),
  ],
  build(p, at) {
    const end = at + dur(p, 1.2);
    const fill = bool(p, "fill", true);
    const props: FlatUiProps = {
      component: "hook",
      ...themeProps(p, "red"),
      title: str(p, "word", "НАБИРАЮТ").replace(/\\n/g, "\n"),
      text: str(p, "sub", ""),
      variant: fill ? "fill" : bool(p, "accentLast", false) ? "accent" : "plain",
      w: 1000,
    };
    // С первого кадра — без входа: хук должен читаться на кадре 0.
    return { layers: [uiLayer(props, at, end, num(p, "y", 0.5), "Хук", "none")] };
  },
};

export const flatError: MotionTemplate = {
  id: "flat-error",
  name: "Чип + заголовок («Ошибка 1»)",
  category: FLAT_CATEGORY,
  description: "Пилюля-чип с номером и крупный заголовок под ним, подзаголовок по желанию",
  duration: 3,
  params: [
    { name: "chip", label: "Чип", type: "text", default: "Ошибка 1" },
    { name: "title", label: "Заголовок", type: "text", default: "Скучное начало" },
    { name: "sub", label: "Подзаголовок", type: "text", default: "" },
    { name: "card", label: "На карточке", type: "bool", default: false },
    themeParam("red"),
    accentParam,
    plateParam,
    backdropParam(false),
    yParam(0.13),
    durationParam(3),
  ],
  build(p, at) {
    const end = at + dur(p, 3);
    const props: FlatUiProps = {
      component: "title",
      ...themeProps(p, "red"),
      label: str(p, "chip", "Ошибка 1"),
      title: str(p, "title", "Скучное начало"),
      text: str(p, "sub", ""),
      variant: bool(p, "card", false) ? "card" : "plain",
      w: 980,
    };
    return { layers: withBackdrop(p, at, end, [uiLayer(props, at, end, num(p, "y", 0.13), "Чип + заголовок", "slideDown")], false) };
  },
};

export const flatStat: MotionTemplate = {
  id: "flat-stat",
  name: "Карточка с числом",
  category: FLAT_CATEGORY,
  description: "Подпись капсом со значком, крупное число растёт, чип «+340%», полоса и пояснение",
  duration: 4,
  params: [
    { name: "label", label: "Подпись", type: "text", default: "Просмотры за неделю" },
    { name: "icon", label: "Значок (Lucide: eye, hourglass, trending-up…)", type: "text", default: "eye" },
    { name: "value", label: "Число", type: "number", default: 942692 },
    { name: "from", label: "От", type: "number", default: 0 },
    { name: "prefix", label: "Перед числом", type: "text", default: "" },
    { name: "suffix", label: "После числа", type: "text", default: "" },
    { name: "unit", label: "Слово после числа", type: "text", default: "" },
    { name: "delta", label: "Чип изменения", type: "text", default: "+340%" },
    { name: "progress", label: "Полоса, % (−1 — нет)", type: "number", default: -1, min: -1, max: 100 },
    { name: "caption", label: "Пояснение", type: "text", default: "" },
    themeParam("dark"),
    accentParam,
    plateParam,
    backdropParam(true),
    yParam(0.3),
    durationParam(4),
  ],
  build(p, at) {
    const end = at + dur(p, 4);
    const pr = num(p, "progress", -1);
    const props: FlatUiProps = {
      component: "stat",
      ...themeProps(p),
      label: str(p, "label", ""),
      icon: str(p, "icon", ""),
      value: num(p, "value", 942692),
      from: num(p, "from", 0),
      prefix: str(p, "prefix", ""),
      suffix: str(p, "suffix", ""),
      unit: str(p, "unit", ""),
      delta: str(p, "delta", ""),
      text: str(p, "caption", ""),
      ...(pr >= 0 ? { progress: Math.min(100, pr) / 100 } : {}),
      w: 920,
    };
    return { layers: withBackdrop(p, at, end, [uiLayer(props, at, end, num(p, "y", 0.3), "Карточка с числом")], true) };
  },
};

export const flatCompare: MotionTemplate = {
  id: "flat-compare",
  name: "Сравнение полосами",
  category: FLAT_CATEGORY,
  description: "Карточка с подписью и полосами «было/стало», метка-порог с подписью, заметки справа",
  duration: 4.5,
  params: [
    { name: "label", label: "Подпись", type: "text", default: "Монетизация TikTok" },
    { name: "rows", label: "Полосы «текст | значение | заметка; …»", type: "text", default: "видео 0:50 | 50 | $0; видео 1:04 | 64 | платит ✓" },
    { name: "max", label: "Шкала до (0 — по наибольшему)", type: "number", default: 70 },
    { name: "marker", label: "Метка-порог, значение (−1 — нет)", type: "number", default: 60 },
    { name: "markerLabel", label: "Подпись метки", type: "text", default: "1:00" },
    { name: "dimFirst", label: "Первая полоса бледная («было»)", type: "bool", default: true },
    themeParam("red"),
    accentParam,
    plateParam,
    backdropParam(true),
    yParam(0.3),
    durationParam(4.5),
  ],
  build(p, at) {
    const end = at + dur(p, 4.5);
    const rows = parseBars(str(p, "rows", ""));
    const max = num(p, "max", 0) > 0 ? num(p, "max", 0) : Math.max(1, ...rows.map((r) => r.value ?? 0));
    const dimFirst = bool(p, "dimFirst", true);
    const marker = num(p, "marker", -1);
    const props: FlatUiProps = {
      component: "bars",
      ...themeProps(p, "red"),
      label: str(p, "label", ""),
      items: rows.map((r, i) => ({ ...r, max, ...(dimFirst && i === 0 && rows.length > 1 ? { dim: true } : {}) })),
      ...(marker >= 0 ? { marker: Math.min(1, marker / max), markerLabel: str(p, "markerLabel", "") } : {}),
      delay: 0.4,
      stagger: 0.5,
      w: 920,
    };
    return { layers: withBackdrop(p, at, end, [uiLayer(props, at, end, num(p, "y", 0.3), "Сравнение")], true) };
  },
};

export const flatChecklist: MotionTemplate = {
  id: "flat-checklist",
  name: "Чек-лист в окне",
  category: FLAT_CATEGORY,
  description: "Окно приложения (или карточка) с заголовком и пунктами — галочки ставятся по одной",
  duration: 5,
  params: [
    { name: "app", label: "Окно: имя приложения (пусто — карточка)", type: "text", default: "Claude" },
    { name: "title", label: "Заголовок", type: "text", default: "Исправляю 3 ошибки" },
    { name: "items", label: "Пункты «текст | подпись; …»", type: "text", default: "Цепляющее начало | сценарий с крючком в первые 3 секунды; Видео каждый день | готовый ролик за 15 минут; Длина под монетизацию | ролик 1:04 вместо 0:50" },
    { name: "undone", label: "Сколько последних пунктов не отмечать", type: "number", default: 0, min: 0, max: 10 },
    { name: "stagger", label: "Шаг галочек, с", type: "number", default: 0.8, min: 0.1, max: 5 },
    { name: "appear", label: "Пункты появляются по одному", type: "bool", default: false },
    themeParam("dark"),
    accentParam,
    plateParam,
    backdropParam(true),
    yParam(0.32),
    durationParam(5),
  ],
  build(p, at) {
    const end = at + dur(p, 5);
    const list = parseItems(str(p, "items", ""));
    const undone = Math.max(0, Math.round(num(p, "undone", 0)));
    const app = str(p, "app", "");
    const props: FlatUiProps = {
      component: "checklist",
      ...themeProps(p),
      variant: app ? "window" : "card",
      label: app,
      title: str(p, "title", ""),
      items: list.map((it, i) => (i >= list.length - undone ? { ...it, done: false } : it)),
      delay: 0.6,
      stagger: num(p, "stagger", 0.8),
      reveal: bool(p, "appear", false) ? "appear" : "check",
      w: 920,
    };
    return { layers: withBackdrop(p, at, end, [uiLayer(props, at, end, num(p, "y", 0.32), "Чек-лист")], true) };
  },
};

export const flatToast: MotionTemplate = {
  id: "flat-toast",
  name: "Уведомление",
  category: FLAT_CATEGORY,
  description: "Пуш-уведомление телефона: значок приложения, имя, время и текст — съезжает сверху",
  duration: 3,
  params: [
    { name: "app", label: "Приложение", type: "text", default: "ChatGPT" },
    { name: "text", label: "Текст", type: "text", default: "Да-да! Отличная идея, пишу план на неделю" },
    { name: "time", label: "Время", type: "text", default: "сейчас" },
    { name: "icon", label: "Значок (Lucide; пусто — буква)", type: "text", default: "message-circle" },
    { name: "badge", label: "Счётчик на значке (0 — нет)", type: "number", default: 1, min: 0, max: 99 },
    { name: "glass", label: "Стекло: auto/light/dark", type: "select", default: "auto", options: ["auto", "light", "dark"] },
    themeParam("dark"),
    accentParam,
    plateParam,
    yParam(0.1),
    durationParam(3),
  ],
  build(p, at) {
    const end = at + dur(p, 3);
    const glass = str(p, "glass", "auto");
    const props: FlatUiProps = {
      component: "toast",
      ...themeProps(p),
      title: str(p, "app", "ChatGPT"),
      text: str(p, "text", ""),
      time: str(p, "time", "сейчас"),
      icon: str(p, "icon", ""),
      badge: Math.max(0, Math.round(num(p, "badge", 0))),
      ...(glass === "light" || glass === "dark" ? { variant: glass } : {}),
      w: 940,
    };
    return { layers: [uiLayer(props, at, end, num(p, "y", 0.1), "Уведомление", "slideDown")] };
  },
};

export const flatWindow: MotionTemplate = {
  id: "flat-window",
  name: "Окно браузера / чата",
  category: FLAT_CATEGORY,
  description: "Окно с точками и адресной строкой (браузер), переписка пузырями (чат) или текст приложения",
  duration: 5,
  params: [
    { name: "kind", label: "Вид", type: "select", default: "chat", options: ["chat", "browser", "app"] },
    { name: "app", label: "Имя приложения / вкладки", type: "text", default: "Клод" },
    { name: "url", label: "Адрес (браузер)", type: "text", default: "kotiki-shop.рф" },
    { name: "title", label: "Заголовок страницы", type: "text", default: "Магазин запущен" },
    { name: "text", label: "Текст страницы", type: "text", default: "Сайт онлайн: 128 заказов за первый день." },
    { name: "messages", label: "Сообщения «me: …; them: …»", type: "text", default: "me: Сделай мне сайт за вечер; them: Готово. Премиум — с кислородом" },
    { name: "stagger", label: "Шаг сообщений, с", type: "number", default: 1.2, min: 0.2, max: 5 },
    themeParam("light"),
    accentParam,
    plateParam,
    backdropParam(false),
    yParam(0.35),
    durationParam(5),
  ],
  build(p, at) {
    const end = at + dur(p, 5);
    const kind = str(p, "kind", "chat");
    const chat = kind === "chat";
    const props: FlatUiProps = {
      component: "window",
      ...themeProps(p, "light"),
      variant: chat ? "chat" : kind === "browser" ? "browser" : "app",
      label: str(p, "app", ""),
      url: str(p, "url", ""),
      title: chat ? "" : str(p, "title", ""),
      text: chat ? "" : str(p, "text", ""),
      ...(chat ? { items: parseMessages(str(p, "messages", "")), delay: 0.4, stagger: num(p, "stagger", 1.2) } : {}),
      w: 940,
    };
    return { layers: withBackdrop(p, at, end, [uiLayer(props, at, end, num(p, "y", 0.35), "Окно", "zoom")], false) };
  },
};

export const flatCounter: MotionTemplate = {
  id: "flat-counter",
  name: "Счётчик-пилюля",
  category: FLAT_CATEGORY,
  description: "Пилюля со значком: число накручивается («942 692 просмотров»)",
  duration: 3,
  params: [
    { name: "value", label: "Число", type: "number", default: 942692 },
    { name: "from", label: "От", type: "number", default: 120000 },
    { name: "unit", label: "Слово после числа", type: "text", default: "просмотров" },
    { name: "icon", label: "Значок (Lucide)", type: "text", default: "eye" },
    { name: "style", label: "Вид: accent/pill/plain", type: "select", default: "accent", options: ["accent", "pill", "plain"] },
    themeParam("red"),
    accentParam,
    plateParam,
    yParam(0.2),
    durationParam(3),
  ],
  build(p, at) {
    const end = at + dur(p, 3);
    const props: FlatUiProps = {
      component: "counter",
      ...themeProps(p, "red"),
      value: num(p, "value", 942692),
      from: num(p, "from", 0),
      unit: str(p, "unit", ""),
      icon: str(p, "icon", ""),
      variant: str(p, "style", "accent"),
    };
    return { layers: [uiLayer(props, at, end, num(p, "y", 0.2), "Счётчик")] };
  },
};

export const flatChip: MotionTemplate = {
  id: "flat-chip",
  name: "Чип / бейдж",
  category: FLAT_CATEGORY,
  description: "Пилюля капсом: «ОШИБКА 1», «3 СЕКУНДЫ», «НОВОЕ» — со значком или без",
  duration: 2.5,
  params: [
    { name: "text", label: "Текст", type: "text", default: "3 секунды" },
    { name: "icon", label: "Значок (Lucide; пусто — нет)", type: "text", default: "" },
    { name: "style", label: "Вид: solid/soft/outline/neutral", type: "select", default: "solid", options: ["solid", "soft", "outline", "neutral"] },
    { name: "size", label: "Кегль", type: "number", default: 40, min: 16, max: 120 },
    { name: "tilt", label: "Наклон, °", type: "number", default: -6, min: -30, max: 30 },
    themeParam("red"),
    accentParam,
    plateParam,
    yParam(0.4),
    durationParam(2.5),
  ],
  build(p, at) {
    const end = at + dur(p, 2.5);
    const props: FlatUiProps = { component: "chip", ...themeProps(p, "red"), title: str(p, "text", "3 секунды"), icon: str(p, "icon", ""), variant: str(p, "style", "solid"), size: num(p, "size", 40) };
    const l = uiLayer(props, at, end, num(p, "y", 0.4), "Чип");
    l.rotation = num(p, "tilt", -6);
    return { layers: [l] };
  },
};

export const flatBackdrop: MotionTemplate = {
  id: "flat-backdrop",
  name: "Фон под плашками",
  category: FLAT_CATEGORY,
  description: "Всё ниже по дорожкам размыто и притушено — плашки поверх читаются (субтитры остаются резкими)",
  duration: 3,
  params: [
    { name: "blur", label: "Размытие, px", type: "number", default: 22, min: 0, max: 64 },
    { name: "dim", label: "Затемнение 0..1", type: "number", default: 0.45, min: 0, max: 1 },
    durationParam(3),
  ],
  build(p, at) {
    const end = at + dur(p, 3);
    return { layers: [backdropLayer(at, end, num(p, "blur", 22), Math.max(0, Math.min(1, num(p, "dim", 0.45))))] };
  },
};

export const FLAT_TEMPLATES: MotionTemplate[] = [flatHook, flatError, flatStat, flatCompare, flatChecklist, flatToast, flatWindow, flatCounter, flatChip, flatBackdrop];

/* ---------- плоская тема у старых шаблонов (theme ≠ classic) ---------- */

/** «Счётчик» в плоской теме: карточка с подписью и растущим числом. */
export function flatCounterFromLegacy(p: TemplateParams, at: number, end: number): Layer[] {
  const props: FlatUiProps = {
    component: "stat",
    ...themeProps(p),
    label: str(p, "label", "подписчиков"),
    icon: "trending-up",
    from: num(p, "from", 0),
    value: num(p, "to", 1000000),
    suffix: str(p, "suffix", ""),
    w: 900,
  };
  return [uiLayer(props, at, end, num(p, "y", 0.4), "Счётчик")];
}

/** «Прогресс» в плоской теме: карточка с процентом и полосой. */
export function flatProgressFromLegacy(p: TemplateParams, at: number, end: number): Layer[] {
  const value = Math.max(0, Math.min(100, num(p, "value", 86)));
  const props: FlatUiProps = { component: "stat", ...themeProps(p), label: str(p, "title", "Загрузка"), icon: "loader", value, from: 0, suffix: "%", progress: value / 100, w: 900 };
  return [uiLayer(props, at, end, num(p, "y", 0.45), "Прогресс")];
}

/** «Нижняя треть» в плоской теме: карточка с заголовком и подзаголовком. */
export function flatLowerThirdFromLegacy(p: TemplateParams, at: number, end: number): Layer[] {
  const props: FlatUiProps = { component: "title", ...themeProps(p), variant: "card", title: str(p, "title", "Лучший момент"), text: str(p, "subtitle", ""), size: 60, w: 900 };
  return [uiLayer(props, at, end, num(p, "y", 0.8), "Нижняя треть", "slideUp")];
}

/** «ТОП-3» в плоской теме: чек-лист, пункты появляются по одному. */
export function flatTop3FromLegacy(p: TemplateParams, at: number, end: number): Layer[] {
  const props: FlatUiProps = {
    component: "checklist",
    ...themeProps(p),
    title: str(p, "title", "ТОП-3"),
    items: ["item1", "item2", "item3"].map((k) => ({ text: str(p, k, "") })).filter((it) => it.text),
    reveal: "appear",
    delay: 0.4,
    stagger: 0.4,
    w: 900,
  };
  return [uiLayer(props, at, end, 0.4, "ТОП-3")];
}
