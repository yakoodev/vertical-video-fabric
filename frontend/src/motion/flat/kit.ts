/**
 * Общее у компонентов плоской темы: пропсы слоя `ui`, контекст кадра, время пунктов, кегли по умолчанию.
 * Компоненты — flat/components.ts (чип, заголовок, хук, счётчик, уведомление) и flat/cards.ts (карточка с
 * числом, сравнение полосами, чек-лист, окно).
 */
import type { G } from "./draw.ts";
import { FLAT_FONTS, type FlatFonts, type FlatPalette } from "./tokens.ts";

export type FlatComponent = "hook" | "chip" | "title" | "stat" | "bars" | "counter" | "checklist" | "toast" | "window";
export const FLAT_COMPONENTS: readonly FlatComponent[] = ["hook", "chip", "title", "stat", "bars", "counter", "checklist", "toast", "window"];

/** Подписи компонентов по-русски — инспектор, шпаргалка агента. */
export const FLAT_COMPONENT_LABELS: Record<FlatComponent, string> = {
  hook: "хук-слово во весь кадр",
  chip: "чип-пилюля («ОШИБКА 1»)",
  title: "заголовок (чип + крупный текст + подзаголовок)",
  stat: "карточка с подписью и крупным числом",
  bars: "сравнение полосами",
  counter: "счётчик-пилюля («942 692 просмотров»)",
  checklist: "чек-лист (галочки по одной)",
  toast: "уведомление",
  window: "окно приложения / браузера / чата",
};

/** Пункт списка: строка чек-листа, полоса сравнения, сообщение чата в окне. */
export type FlatItem = {
  text?: string;
  sub?: string;
  /** Справа у полосы («$0», «платит ✓»). */
  note?: string;
  icon?: string;
  value?: number;
  max?: number;
  color?: string | null;
  /** Полоса «проигравшего» — бледная. */
  dim?: boolean;
  /** Своя секунда слоя: галочка / появление / сообщение. */
  at?: number;
  /** false — пункт так и остаётся неотмеченным (бледный). */
  done?: boolean;
  /** Сообщение окна-чата: me — справа акцентом, them — слева. */
  from?: "me" | "them";
};

export type FlatUiProps = {
  component: FlatComponent;
  /** red | dark | light | brand (по умолчанию dark). */
  theme?: string;
  /** Акцент поверх темы; у brand — акцент бренда. */
  accent?: string | null;
  /** brand: основа (плашка бренда) — тёмная → тёмная тема, светлая → светлая. */
  plate?: string | null;
  /** Вариант компонента: chip solid|soft|outline|neutral, title plain|card, hook plain|fill|accent,
   * counter pill|accent|plain, checklist card|window, window app|browser|chat, toast — light|dark (не задан — по теме). */
  variant?: string;
  w?: number;
  h?: number;
  /** Кегль главного текста (хук, число, заголовок, чип). */
  size?: number;
  /** Подпись капсом (stat, bars), чип над заголовком (title), имя приложения (toast, checklist window). */
  label?: string;
  title?: string;
  text?: string;
  icon?: string;
  /** Число: от `from` к `value` по ходу (`progress`), знаки, префикс/суффикс, слово после числа. */
  value?: number;
  from?: number;
  decimals?: number;
  prefix?: string;
  suffix?: string;
  unit?: string;
  /** Чип изменения у числа («+340%»). */
  delta?: string;
  /** Полоса в карточке с числом, 0..1. */
  progress?: number;
  items?: FlatItem[];
  /** Сравнение: вертикальная метка на доле `marker` с подписью. */
  marker?: number;
  markerLabel?: string;
  /** Окно-браузер: адрес; уведомление: время («сейчас»), счётчик; буква значка. */
  url?: string;
  time?: string;
  badge?: number;
  letter?: string;
  /** Время пунктов: первый на `delay` с слоя, дальше через `stagger`. */
  delay?: number;
  stagger?: number;
  /** checklist: check — все видны, галочки по одной; appear — пункты появляются по одному. */
  reveal?: "check" | "appear";
  /** Свои гарнитуры: display (хук, заголовки, цифры), body (подписи, тексты). */
  font?: string;
  bodyFont?: string;
};

/** Кадр компонента: контекст, палитра, шрифты, время слоя, ход числа/полос, масштаб устройства. */
export type FlatFrame = {
  g: G;
  pal: FlatPalette;
  fonts: FlatFonts;
  local: number;
  dur: number;
  progress: number;
  ds: number;
  /** Прямоугольник всего кадра в координатах компонента (хук с заливкой). */
  frame?: { x: number; y: number; w: number; h: number };
};

export const fontsOf = (p: FlatUiProps): FlatFonts => ({ display: p.font || FLAT_FONTS.display, body: p.bodyFont || FLAT_FONTS.body });

export const items = (p: FlatUiProps): FlatItem[] => (Array.isArray(p.items) ? p.items.filter((it) => it && typeof it === "object") : []);

/** Секунда слоя, когда срабатывает пункт i. */
export function itemAt(p: FlatUiProps, i: number): number {
  const own = items(p)[i]?.at;
  if (typeof own === "number" && Number.isFinite(own)) return own;
  return (p.delay ?? 0.5) + i * (p.stagger ?? 0.6);
}

export const num = (v: unknown, d: number) => (typeof v === "number" && Number.isFinite(v) ? v : d);
export const str = (v: unknown) => (v === undefined || v === null ? "" : String(v));
