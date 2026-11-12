/**
 * Плоская тема («flat theme») — дизайн-токены плоской графики: палитры тем, скругления, тени, кромка,
 * отступы, шкала кеглей и шрифты. Одна правда для 2D-слоя `ui` (layers/flatui.ts), шаблонов галереи
 * «Плоский UI» и плоских экранов в 3D (node/scene3d/src: монитор с браузером/чатом, уведомления, HUD).
 *
 * Ориентир — графика «ошибок новичка» (D:\vvf-offload\refs\flat_graphics): одна связная палитра,
 * тёмные скруглённые карточки с тонкой кромкой и воздухом внутри, подписи капсом в разрядку, пилюли-чипы,
 * крупные жирные цифры, полосы прогресса, общая тень, размытый и притушенный фон под плашками.
 *
 * Модуль без зависимостей (только типы и чистые функции): его импортирует и бандл компоновщика, и
 * 3D-движок, и тесты Node напрямую (`.ts` с удалением типов).
 */

export type FlatThemeName = "red" | "dark" | "light" | "brand";
export const FLAT_THEMES: readonly FlatThemeName[] = ["red", "dark", "light", "brand"];

/** Подписи тем по-русски — галерея, шпаргалка агента. */
export const FLAT_THEME_LABELS: Record<FlatThemeName, string> = {
  red: "красная (чёрно-красная, как реф «ошибки»)",
  dark: "тёмная нейтральная (индиго-акцент)",
  light: "светлая чистая",
  brand: "под бренд (accent + plate проекта)",
};

export type FlatPalette = {
  name: FlatThemeName;
  mode: "dark" | "light";
  /** Фон кадра-заставки (хук во весь кадр) и его свечение. */
  bg: string;
  bgGlow: string;
  /** Карточка, вложенные поля (дорожка полосы, поле ввода, плитки), приподнятый слой (шапка окна). */
  surface: string;
  surface2: string;
  surface3: string;
  /** Тонкая кромка карточки и разделители. */
  border: string;
  /** Текст: основной, приглушённый (подписи), бледный (неактивное). */
  text: string;
  muted: string;
  faint: string;
  /** Акцент, текст на акценте, мягкая подложка акцента. */
  accent: string;
  accentText: string;
  accentSoft: string;
  success: string;
  warning: string;
  danger: string;
  /** Тень (цвет) и затемнение фона под плашками. */
  shadow: string;
  scrim: string;
};

/* ---------- цвет (свои мини-утилиты: модуль без зависимостей) ---------- */

type RGB = [number, number, number];

export function hexRgb(color: string): RGB | null {
  const m = /^#([0-9a-f]{3}|[0-9a-f]{6}|[0-9a-f]{8})$/i.exec((color || "").trim());
  if (!m) return null;
  const h = m[1].length === 3 ? m[1].split("").map((c) => c + c).join("") : m[1].slice(0, 6);
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
}

const hex2 = (v: number) => Math.round(Math.max(0, Math.min(255, v))).toString(16).padStart(2, "0");
const toHex = ([r, g, b]: RGB) => `#${hex2(r)}${hex2(g)}${hex2(b)}`;

/** Смесь двух цветов (k = 0 — a, 1 — b). */
export function mix(a: string, b: string, k: number): string {
  const ca = hexRgb(a) ?? [0, 0, 0];
  const cb = hexRgb(b) ?? [0, 0, 0];
  return toHex([ca[0] + (cb[0] - ca[0]) * k, ca[1] + (cb[1] - ca[1]) * k, ca[2] + (cb[2] - ca[2]) * k]);
}

/** `#rrggbb` + прозрачность 0..1 → `#rrggbbaa`. */
export function alpha(color: string, a: number): string {
  const c = hexRgb(color);
  return c ? `${toHex(c)}${hex2(Math.max(0, Math.min(1, a)) * 255)}` : color;
}

/** Относительная яркость 0..1 (WCAG). */
export function luminance(color: string): number {
  const c = hexRgb(color);
  if (!c) return 0;
  const lin = (v: number) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
  };
  return 0.2126 * lin(c[0]) + 0.7152 * lin(c[1]) + 0.0722 * lin(c[2]);
}

/** Контраст двух цветов (1..21). */
export function contrast(a: string, b: string): number {
  const la = luminance(a);
  const lb = luminance(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

/** Читаемый текст поверх цвета: белый, если он контрастнее почти-чёрного. */
export function onColor(color: string): string {
  return contrast(color, "#ffffff") >= contrast(color, "#0f1115") * 0.62 ? "#ffffff" : "#0f1115";
}

/* ---------- палитры ---------- */

export const FLAT_PALETTES: Record<Exclude<FlatThemeName, "brand">, FlatPalette> = {
  // Чёрно-красная: как реф — почти чёрные карточки с тёплым отливом, красный акцент со свечением.
  red: {
    name: "red",
    mode: "dark",
    bg: "#0c0607",
    bgGlow: "#5a0a12",
    surface: "#141012f2",
    surface2: "#241b1e",
    surface3: "#1c1517",
    border: "#ffffff17",
    text: "#ffffff",
    muted: "#ffffff99",
    faint: "#ffffff4d",
    accent: "#ff2d46",
    accentText: "#ffffff",
    accentSoft: "#ff2d4629",
    success: "#ff2d46",
    warning: "#ffb020",
    danger: "#ff2d46",
    shadow: "#000000",
    scrim: "#120406",
  },
  // Тёмная нейтральная: графит, холодный индиго-акцент (как рекламный пакет).
  dark: {
    name: "dark",
    mode: "dark",
    bg: "#0a0b10",
    bgGlow: "#241d5c",
    surface: "#15171ef2",
    surface2: "#22252f",
    surface3: "#1b1d25",
    border: "#ffffff17",
    text: "#f5f6fa",
    muted: "#f5f6fa94",
    faint: "#f5f6fa47",
    accent: "#6c5cff",
    accentText: "#ffffff",
    accentSoft: "#6c5cff2e",
    success: "#22c55e",
    warning: "#f5a524",
    danger: "#ff4757",
    shadow: "#000000",
    scrim: "#06070b",
  },
  // Светлая чистая: белые карточки на светло-сером, синий акцент, мягкие тени.
  light: {
    name: "light",
    mode: "light",
    bg: "#f3f4f7",
    bgGlow: "#d9e3ff",
    surface: "#fffffff7",
    surface2: "#eef0f4",
    surface3: "#f6f7f9",
    border: "#0f111514",
    text: "#0f1115",
    muted: "#0f11159c",
    faint: "#0f111552",
    accent: "#2f6bff",
    accentText: "#ffffff",
    accentSoft: "#2f6bff1f",
    success: "#16a34a",
    warning: "#d97706",
    danger: "#e5263b",
    shadow: "#1b2036",
    scrim: "#0b0d14",
  },
};

export type FlatBrand = { accent?: string | null; plate?: string | null; text?: string | null };

/**
 * Тема «под бренд»: акцент — `accent` проекта, основа — `plate` (тёмная плашка бренда → тёмная тема, светлая →
 * светлая). Поверхности — основа, слегка подкрашенная акцентом; текст — читаемый на основе.
 */
export function brandPalette(brand: FlatBrand = {}): FlatPalette {
  const accent = hexRgb(brand.accent || "") ? (brand.accent as string).slice(0, 7) : FLAT_PALETTES.dark.accent;
  const plate = hexRgb(brand.plate || "") ? (brand.plate as string).slice(0, 7) : null;
  const light = plate ? luminance(plate) > 0.45 : false;
  const base = plate ?? (light ? "#ffffff" : "#111218");
  const tint = (k: number) => mix(base, accent, k);
  if (light) {
    const text = hexRgb(brand.text || "") && contrast(brand.text as string, base) >= 4.5 ? (brand.text as string).slice(0, 7) : "#0f1115";
    return {
      ...FLAT_PALETTES.light,
      name: "brand",
      bg: mix(base, "#e9ebf0", 0.5),
      bgGlow: mix(base, accent, 0.25),
      surface: alpha(base, 0.97),
      surface2: mix(tint(0.06), "#000000", 0.05),
      surface3: tint(0.03),
      text,
      muted: alpha(text, 0.62),
      faint: alpha(text, 0.32),
      accent,
      accentText: onColor(accent),
      accentSoft: alpha(accent, 0.14),
      success: accent,
      danger: accent,
    };
  }
  const text = hexRgb(brand.text || "") && contrast(brand.text as string, base) >= 4.5 ? (brand.text as string).slice(0, 7) : "#ffffff";
  return {
    ...FLAT_PALETTES.dark,
    name: "brand",
    bg: mix(base, "#000000", 0.35),
    bgGlow: mix(mix(base, "#000000", 0.35), accent, 0.45),
    surface: alpha(mix(tint(0.04), "#000000", 0.08), 0.95),
    surface2: mix(tint(0.1), "#ffffff", 0.06),
    surface3: mix(tint(0.07), "#ffffff", 0.03),
    text,
    muted: alpha(text, 0.6),
    faint: alpha(text, 0.3),
    accent,
    accentText: onColor(accent),
    accentSoft: alpha(accent, 0.18),
    success: accent,
    danger: accent,
  };
}

/**
 * Палитра по имени темы (`red`/`dark`/`light`/`brand`; неизвестное — `dark`). `accent` перекрашивает акцент
 * любой темы (и подложку/текст на нём), у `brand` ещё `plate` — основа.
 */
export function flatTheme(name?: string | null, brand: FlatBrand = {}): FlatPalette {
  if (name === "brand") return brandPalette(brand);
  const base = FLAT_PALETTES[(name as Exclude<FlatThemeName, "brand">) in FLAT_PALETTES ? (name as Exclude<FlatThemeName, "brand">) : "dark"];
  const accent = hexRgb(brand.accent || "") ? (brand.accent as string).slice(0, 7) : null;
  if (!accent || accent.toLowerCase() === base.accent) return base;
  return {
    ...base,
    accent,
    accentText: onColor(accent),
    accentSoft: alpha(accent, base.mode === "dark" ? 0.18 : 0.13),
    ...(base.name === "red" ? { success: accent, danger: accent } : {}),
  };
}

/* ---------- геометрия и типографика ---------- */

/** Скругления (опорные px кадра 1080×1920). */
export const RADII = { xs: 10, sm: 16, md: 24, lg: 34, xl: 46, pill: 999 } as const;

/** Шкала отступов: 4-кратная сетка. */
export const SPACE = { xxs: 4, xs: 8, sm: 12, md: 16, lg: 24, xl: 32, xxl: 44, xxxl: 64 } as const;

/** Кромка карточки: тонкая линия цветом `border` (+ светлее сверху — «стекло»). */
export const BORDER = { width: 2, highlight: 0.06 } as const;

export type FlatShadow = { blur: number; y: number; alpha: number };
/** Тени: одна система на все плашки. `glow` — свечение акцентом (чип, хук). */
export const SHADOWS: Record<"card" | "float" | "chip" | "glow", FlatShadow> = {
  card: { blur: 56, y: 22, alpha: 0.5 },
  float: { blur: 34, y: 12, alpha: 0.35 },
  chip: { blur: 18, y: 6, alpha: 0.35 },
  glow: { blur: 40, y: 0, alpha: 0.55 },
};

/** Шрифты (из vendor/fonts): display — хук, заголовки, чипы, цифры; body — подписи, пункты, тексты. */
export type FlatFonts = { display: string; body: string };
export const FLAT_FONTS: FlatFonts = { display: "Montserrat", body: "Golos Text" };

export type TypeRole = { font: keyof FlatFonts; weight: number; size: number; caps?: boolean; tracking?: number; lineHeight?: number };

/**
 * Шкала кеглей (опорные px): от хука во весь кадр до подписей капсом. `tracking` — разрядка в долях кегля
 * (у капса подписи — 0.14 em, как «МОНЕТИЗАЦИЯ TIKTOK» в рефе).
 */
export const TYPE: Record<"hook" | "number" | "title" | "heading" | "chip" | "body" | "item" | "sub" | "label" | "caption", TypeRole> = {
  hook: { font: "display", weight: 900, size: 200, caps: true, tracking: 0.01, lineHeight: 0.95 },
  number: { font: "display", weight: 900, size: 120, lineHeight: 1 },
  title: { font: "display", weight: 900, size: 76, lineHeight: 1.08 },
  heading: { font: "body", weight: 900, size: 44, lineHeight: 1.15 },
  chip: { font: "display", weight: 900, size: 36, caps: true, tracking: 0.02 },
  body: { font: "body", weight: 500, size: 36, lineHeight: 1.3 },
  item: { font: "body", weight: 700, size: 36, lineHeight: 1.2 },
  sub: { font: "body", weight: 500, size: 27, lineHeight: 1.25 },
  label: { font: "body", weight: 700, size: 25, caps: true, tracking: 0.14 },
  caption: { font: "body", weight: 500, size: 25, lineHeight: 1.3 },
};

/** Строка `ctx.font` роли: `weight sizepx "Family", запасные`. */
export function typeFont(role: TypeRole, fonts: FlatFonts = FLAT_FONTS, size = role.size, weight = role.weight): string {
  const family = fonts[role.font].replace(/["\\]/g, "");
  return `${Math.round(weight / 100) * 100} ${Math.round(size * 100) / 100}px "${family}", "Montserrat", "Inter", Arial, sans-serif`;
}

/** Цвета «светофора» окна (macOS): закрыть, свернуть, развернуть. */
export const WINDOW_DOTS = ["#ff5f57", "#febc2e", "#28c840"] as const;
