/**
 * Плоская тема в 3D-экранах и слоях поверх кадра: те же токены и компоненты, что у 2D-слоя `ui` моушна
 * (frontend/src/motion/flat) — монитор с браузером/чатом, уведомления телефона, HUD, ярлык и всплывающая
 * карточка выглядят так же, как плашки монтажа. Шрифты — Montserrat и Golos Text из vendor/fonts
 * (loadUiFonts в ui.ts регистрирует их под настоящими именами семейств).
 *
 * Тема — поле `theme` спецификации экрана/слоя (red | dark | light | brand + accent/plate); `toon` — прежний
 * мультяшный вид с толстым контуром (оставлен для сцен, где экран — часть рисованного мира).
 */
import { drawFlat, measureFlat } from "../../../frontend/src/motion/flat/components.ts";
import { card, rgbaOf, rrect, text, windowDots } from "../../../frontend/src/motion/flat/draw.ts";
import { drawFlatIcon } from "../../../frontend/src/motion/flat/icons.ts";
import type { FlatUiProps } from "../../../frontend/src/motion/flat/kit.ts";
import { FLAT_FONTS, flatTheme, type FlatPalette, RADII, SHADOWS, TYPE } from "../../../frontend/src/motion/flat/tokens.ts";

export { FLAT_FONTS, flatTheme, rgbaOf, rrect, text, card, drawFlatIcon, windowDots, RADII, SHADOWS, TYPE };
export type { FlatPalette };

/** Прежний мультяшный вид вместо плоской темы. */
export const isToon = (theme: unknown) => theme === "toon";

/** Палитра экрана: тема спецификации (нет — `def`), акцент страницы/спецификации поверх. */
export function screenPalette(theme: unknown, def: string, accent?: string | null, plate?: string | null): FlatPalette {
  return flatTheme(typeof theme === "string" && theme !== "toon" ? theme : def, { accent: accent ?? null, plate: plate ?? null });
}

/** Строка шрифта плоской темы для экранов (display — цифры и заголовки, body — тексты). */
export function flatFont(kind: "display" | "body", weight: number, px: number): string {
  const family = kind === "display" ? FLAT_FONTS.display : FLAT_FONTS.body;
  return `${weight} ${Math.round(px * 100) / 100}px "${family}", "Montserrat", Arial, sans-serif`;
}

/**
 * Компонент плоской темы с центром (cx, cy), масштаб `k` (опорный px компонента → px холста), время `t` от
 * начала его жизни, ход числа `progress`. Возвращает размер в px холста.
 */
export function flatComponent(g: CanvasRenderingContext2D, props: FlatUiProps, cx: number, cy: number, k: number, t: number,
  progress = 1): { w: number; h: number } {
  const pal = flatTheme(props.theme, { accent: props.accent ?? null, plate: props.plate ?? null });
  const box = measureFlat(g, props);
  g.save();
  g.translate(cx, cy);
  g.scale(k, k);
  drawFlat({ g, pal, fonts: FLAT_FONTS, local: t, dur: 99, progress, ds: k }, props);
  g.restore();
  return { w: box.w * k, h: box.h * k };
}

/** Размер компонента в px холста при масштабе k (без отрисовки). */
export function flatSize(g: CanvasRenderingContext2D, props: FlatUiProps, k: number): { w: number; h: number } {
  const box = measureFlat(g, props);
  return { w: box.w * k, h: box.h * k };
}

/** Пилюля с текстом (LIVE, ЧАТ, ОНЛАЙН): заливка, необязательная точка слева. Левый верхний угол (x, y). */
export function flatPill(g: CanvasRenderingContext2D, x: number, y: number, h: number, label: string, fill: string, ink: string,
  dot = false): number {
  g.save();
  g.font = flatFont("display", 900, h * 0.5);
  const tw = g.measureText(label).width;
  const padX = h * 0.42;
  const dotW = dot ? h * 0.42 : 0;
  const w = padX * 2 + dotW + tw;
  g.beginPath();
  rrect(g, x, y, w, h, h / 2);
  g.fillStyle = rgbaOf(fill);
  g.fill();
  if (dot) {
    g.beginPath();
    g.arc(x + padX + h * 0.12, y + h / 2, h * 0.12, 0, Math.PI * 2);
    g.fillStyle = rgbaOf(ink);
    g.fill();
  }
  g.fillStyle = rgbaOf(ink);
  g.textAlign = "left";
  g.textBaseline = "alphabetic";
  g.fillText(label, x + padX + dotW, y + h / 2 + h * 0.5 * 0.35);
  g.restore();
  return w;
}
