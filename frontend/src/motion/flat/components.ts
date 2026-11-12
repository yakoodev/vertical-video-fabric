/**
 * Компоненты плоской темы (слой `ui`, плоские экраны 3D): чип, заголовок, хук во весь кадр, счётчик-пилюля,
 * уведомление — здесь; карточка с числом, сравнение полосами, чек-лист и окно — flat/cards.ts.
 *
 * Каждый компонент — пара «разметка → отрисовка» в своей системе координат: опорные px, центр в (0, 0).
 * Разметка зависит только от пропсов и шрифтов (не от времени) — рамка слоя не «дышит», пока число растёт
 * или пункты отмечаются. Всё — функция времени слоя: превью и рендер совпадают кадр в кадр.
 */
import { drawCards, measureCards } from "./cards.ts";
import { card, castShadow, fitSize, formatNumber, type G, iconTile, outBack, outCubic, pill, rgbaOf, rrect, setFont, text, textWidth, wrap } from "./draw.ts";
import { drawFlatIcon, flatIconName } from "./icons.ts";
import { type FlatFrame, type FlatUiProps, fontsOf, num, str } from "./kit.ts";
import { alpha, type FlatFonts, type FlatPalette, RADII, SHADOWS, TYPE } from "./tokens.ts";

export type Box = { w: number; h: number };

/* ---------- чип ---------- */

type ChipL = { w: number; h: number; size: number; label: string; icon: number; padX: number; gap: number };

export function chipLayout(g: G, label: string, size: number, icon: boolean, fonts: FlatFonts): ChipL {
  setFont(g, TYPE.chip, fonts, size);
  const tw = textWidth(g, label, TYPE.chip, size);
  const h = Math.round(size * 1.74);
  const padX = size * 0.82;
  const ic = icon ? size * 1.08 : 0;
  const gap = icon ? size * 0.34 : 0;
  return { w: padX * 2 + tw + ic + gap, h, size, label, icon: ic, padX, gap };
}

/** Чип с левым верхним углом (x, y). */
export function drawChip(g: G, pal: FlatPalette, L: ChipL, x: number, y: number, variant: string, iconName: string | undefined, fonts: FlatFonts, ds: number): void {
  const solid = variant !== "soft" && variant !== "outline" && variant !== "neutral";
  if (solid) pill(g, x, y, L.w, L.h, pal.accent, { ...SHADOWS.glow, blur: L.h * 0.62, alpha: pal.mode === "dark" ? 0.6 : 0.35 }, ds);
  else if (variant === "neutral") card(g, pal, x, y, L.w, L.h, { radius: L.h / 2, shadow: SHADOWS.chip, ds });
  else {
    g.save();
    g.beginPath();
    rrect(g, x + 1.5, y + 1.5, L.w - 3, L.h - 3, L.h / 2);
    if (variant === "soft") {
      g.fillStyle = rgbaOf(pal.accentSoft);
      g.fill();
    }
    g.lineWidth = 3;
    g.strokeStyle = rgbaOf(pal.accent, variant === "soft" ? 0.55 : 1);
    g.stroke();
    g.restore();
  }
  const ink = solid ? pal.accentText : variant === "neutral" ? pal.text : pal.accent;
  let cx = x + L.padX;
  if (L.icon) {
    drawFlatIcon(g, iconName, cx + L.icon / 2, y + L.h / 2, L.icon, rgbaOf(ink), 2.6);
    cx += L.icon + L.gap;
  }
  text(g, L.label, cx, y + L.h / 2, TYPE.chip, ink, { fonts, size: L.size });
}

function chipBox(g: G, p: FlatUiProps): ChipL {
  const label = str(p.title || p.text || p.label || "Чип");
  return chipLayout(g, label, num(p.size, TYPE.chip.size), !!flatIconName(p.icon), fontsOf(p));
}

/* ---------- заголовок: чип + крупный текст + подзаголовок ---------- */

type TitleL = { w: number; h: number; pad: number; chip: ChipL | null; lines: string[]; size: number; sub: string[]; subSize: number; gap: number };

function titleLayout(g: G, p: FlatUiProps): TitleL {
  const fonts = fontsOf(p);
  const cardV = p.variant === "card";
  const pad = cardV ? 52 : 0;
  const maxW = Math.max(200, num(p.w, 960)) - pad * 2;
  const chip = p.label ? chipLayout(g, p.label, Math.round(num(p.size, TYPE.title.size) * 0.45), !!flatIconName(p.icon), fonts) : null;
  const size = num(p.size, TYPE.title.size);
  setFont(g, TYPE.title, fonts, size);
  const lines = p.title ? wrap(g, p.title, maxW, 3) : [];
  const subSize = Math.round(size * 0.47);
  setFont(g, TYPE.body, fonts, subSize);
  const sub = p.text ? wrap(g, p.text, maxW, 3) : [];
  const gap = Math.round(size * 0.32);
  setFont(g, TYPE.title, fonts, size);
  let w = Math.max(chip?.w ?? 0, ...lines.map((l) => g.measureText(l).width));
  setFont(g, TYPE.body, fonts, subSize);
  w = Math.max(w, ...sub.map((l) => g.measureText(l).width));
  const h = (chip ? chip.h + gap : 0) + lines.length * size * 1.08 + (sub.length ? gap * 0.6 + sub.length * subSize * 1.3 : 0);
  return { w: Math.min(maxW, w) + pad * 2, h: h + pad * 2, pad, chip, lines, size, sub, subSize, gap };
}

function drawTitle(fc: FlatFrame, p: FlatUiProps, L: TitleL): void {
  const { g, pal, fonts } = fc;
  const top = -L.h / 2;
  if (L.pad) card(g, pal, -L.w / 2, top, L.w, L.h, { radius: RADII.lg, shadow: SHADOWS.card, ds: fc.ds });
  let y = top + L.pad;
  if (L.chip) {
    // Чип выпрыгивает чуть раньше текста — «ОШИБКА 1», затем заголовок.
    const k = outBack(fc.local / 0.35);
    if (k > 0.001) {
      g.save();
      g.translate(0, y + L.chip.h / 2);
      g.scale(k, k);
      drawChip(g, pal, L.chip, -L.chip.w / 2, -L.chip.h / 2, "solid", p.icon, fonts, fc.ds);
      g.restore();
    }
    y += L.chip.h + L.gap;
  }
  g.save();
  if (!L.pad) castShadow(g, "#000000", { blur: 26, y: 6, alpha: pal.mode === "dark" ? 0.55 : 0.18 }, fc.ds);
  for (const line of L.lines) {
    text(g, line, 0, y + L.size * 0.54, TYPE.title, pal.text, { fonts, size: L.size, align: "center" });
    y += L.size * 1.08;
  }
  if (L.sub.length) {
    // Подзаголовок без карточки — плотнее (жирнее, почти белый) и с той же тенью: читается на пёстром кадре.
    y += L.gap * 0.6;
    for (const line of L.sub) {
      const ink = pal.mode === "dark" && !L.pad ? alpha(pal.text.slice(0, 7), 0.9) : pal.muted;
      text(g, line, 0, y + L.subSize * 0.62, TYPE.body, ink, { fonts, size: L.subSize, align: "center", weight: L.pad ? 500 : 700 });
      y += L.subSize * 1.3;
    }
  }
  g.restore();
}

/* ---------- хук: огромное слово во всю ширину ---------- */

type HookL = { w: number; h: number; lines: string[]; size: number; sub: string[]; subSize: number };

function hookLayout(g: G, p: FlatUiProps): HookL {
  const fonts = fontsOf(p);
  const maxW = Math.max(200, num(p.w, 1000));
  const lines = str(p.title || p.text || "ХУК").split("\n").map((s) => s.trim()).filter(Boolean);
  let size = num(p.size, TYPE.hook.size);
  for (const l of lines) size = Math.min(size, fitSize(g, l, TYPE.hook, maxW, fonts, num(p.size, TYPE.hook.size), 0.25));
  const subSize = Math.max(30, Math.round(size * 0.24));
  setFont(g, TYPE.body, fonts, subSize, 700);
  const sub = p.title && p.text ? wrap(g, p.text, maxW, 2) : [];
  const h = lines.length * size * 0.96 + (sub.length ? size * 0.22 + sub.length * subSize * 1.25 : 0);
  return { w: maxW, h, lines, size, sub, subSize };
}

function drawHook(fc: FlatFrame, p: FlatUiProps, L: HookL): void {
  const { g, pal, fonts } = fc;
  if (p.variant === "fill" && fc.frame) {
    // Своя заставка во весь кадр: тёмная основа, свечение акцентом из центра, виньетка по краям.
    const f = fc.frame;
    g.save();
    g.fillStyle = rgbaOf(pal.bg);
    g.fillRect(f.x, f.y, f.w, f.h);
    const r = Math.max(f.w, f.h) * 0.62;
    const glow = g.createRadialGradient(0, 0, 0, 0, 0, r);
    glow.addColorStop(0, rgbaOf(pal.bgGlow, 0.95));
    glow.addColorStop(0.55, rgbaOf(pal.bgGlow, 0.35));
    glow.addColorStop(1, rgbaOf(pal.bgGlow, 0));
    g.fillStyle = glow;
    g.fillRect(f.x, f.y, f.w, f.h);
    g.restore();
  }
  // Въезд: слово чуть крупнее и «садится» на место за 0.45 с.
  const settle = 1 + 0.07 * (1 - outCubic(fc.local / 0.45));
  g.save();
  g.scale(settle, settle);
  let y = -L.h / 2;
  const accentLast = p.variant === "accent";
  g.save();
  castShadow(g, pal.mode === "dark" ? "#000000" : pal.shadow, { blur: L.size * 0.22, y: L.size * 0.04, alpha: pal.mode === "dark" ? 0.6 : 0.2 }, fc.ds);
  L.lines.forEach((line, i) => {
    const color = accentLast && i === L.lines.length - 1 ? pal.accent : pal.text;
    text(g, line, 0, y + L.size * 0.5, TYPE.hook, color, { fonts, size: L.size, align: "center" });
    y += L.size * 0.96;
  });
  g.restore();
  if (L.sub.length) {
    y += L.size * 0.22;
    for (const line of L.sub) {
      text(g, line, 0, y + L.subSize * 0.55, { ...TYPE.body, weight: 700 }, pal.muted, { fonts, size: L.subSize, align: "center" });
      y += L.subSize * 1.25;
    }
  }
  g.restore();
}

/* ---------- счётчик-пилюля ---------- */

type CounterL = { w: number; h: number; size: number; numW: number; unit: string; unitSize: number; unitW: number; icon: number; padL: number; padR: number; gap: number };

function counterLayout(g: G, p: FlatUiProps): CounterL {
  const fonts = fontsOf(p);
  const size = num(p.size, 58);
  const plain = p.variant === "plain";
  const h = plain ? size * 1.3 : Math.round(size * 1.8);
  const icon = flatIconName(p.icon) ? h - 30 : 0;
  setFont(g, TYPE.number, fonts, size);
  const fmt = { decimals: p.decimals, prefix: p.prefix, suffix: p.suffix };
  const numW = Math.max(g.measureText(formatNumber(num(p.from, 0), fmt)).width, g.measureText(formatNumber(num(p.value, 100), fmt)).width);
  const unit = str(p.unit);
  const unitSize = Math.round(size * 0.66);
  setFont(g, TYPE.item, fonts, unitSize, 700);
  const unitW = unit ? g.measureText(unit).width : 0;
  const padL = icon ? 15 : plain ? 0 : size * 0.7;
  const padR = plain ? 0 : size * 0.72;
  const gap = size * 0.3;
  const w = padL + (icon ? icon + gap : 0) + numW + (unit ? gap + unitW : 0) + padR;
  return { w, h, size, numW, unit, unitSize, unitW, icon, padL, padR, gap };
}

function drawCounter(fc: FlatFrame, p: FlatUiProps, L: CounterL): void {
  const { g, pal, fonts } = fc;
  const left = -L.w / 2;
  const accentV = p.variant === "accent";
  const plain = p.variant === "plain";
  if (accentV) pill(g, left, -L.h / 2, L.w, L.h, pal.accent, { ...SHADOWS.glow, blur: L.h * 0.5, alpha: pal.mode === "dark" ? 0.5 : 0.3 }, fc.ds);
  else if (!plain) card(g, pal, left, -L.h / 2, L.w, L.h, { radius: L.h / 2, shadow: SHADOWS.float, ds: fc.ds });
  let x = left + L.padL;
  if (L.icon) {
    g.save();
    g.beginPath();
    g.arc(x + L.icon / 2, 0, L.icon / 2, 0, Math.PI * 2);
    g.fillStyle = rgbaOf(accentV ? "#ffffff33" : pal.accent);
    g.fill();
    g.restore();
    drawFlatIcon(g, p.icon, x + L.icon / 2, 0, L.icon * 0.56, rgbaOf(pal.accentText), 2.3);
    x += L.icon + L.gap;
  }
  const v = num(p.from, 0) + (num(p.value, 100) - num(p.from, 0)) * fc.progress;
  const ink = accentV ? pal.accentText : pal.text;
  g.save();
  if (plain) castShadow(g, "#000000", { blur: 22, y: 5, alpha: 0.5 }, fc.ds);
  text(g, formatNumber(v, { decimals: p.decimals, prefix: p.prefix, suffix: p.suffix }), x, 0, TYPE.number, ink, { fonts, size: L.size });
  x += L.numW + L.gap;
  if (L.unit) text(g, L.unit, x, 0, { ...TYPE.item, weight: 700 }, accentV ? alpha(pal.accentText.slice(0, 7), 0.86) : pal.muted, { fonts, size: L.unitSize });
  g.restore();
}

/* ---------- уведомление ---------- */

type ToastL = { w: number; h: number; pad: number; icon: number; title: string; time: string; lines: string[]; textSize: number; titleSize: number };

function toastLayout(g: G, p: FlatUiProps): ToastL {
  const fonts = fontsOf(p);
  const w = Math.max(400, num(p.w, 920));
  const pad = 30;
  const icon = 92;
  const titleSize = 31;
  const textSize = 31;
  setFont(g, TYPE.body, fonts, textSize);
  const lines = p.text ? wrap(g, p.text, w - pad * 3 - icon, 2) : [];
  const inner = titleSize * 1.2 + (lines.length ? 8 + lines.length * textSize * 1.25 : 0);
  return { w, h: pad * 2 + Math.max(icon, inner), pad, icon, title: str(p.title || p.label || "Уведомление"), time: str(p.time ?? "сейчас"), lines, textSize, titleSize };
}

function drawToast(fc: FlatFrame, p: FlatUiProps, L: ToastL): void {
  const { g, fonts } = fc;
  // Уведомление телефона — своё «стекло»: светлое у светлой темы и у variant light, иначе тёмное.
  const lightV = p.variant === "light" || (p.variant !== "dark" && fc.pal.mode === "light");
  const pal: FlatPalette = lightV
    ? { ...fc.pal, mode: "light", surface: "#fbfbfdf0", text: "#0f1115", muted: "#0f11158f", border: "#0f111512", shadow: "#0b0d14" }
    : fc.pal;
  const left = -L.w / 2;
  const top = -L.h / 2;
  card(g, pal, left, top, L.w, L.h, { radius: RADII.xl, shadow: SHADOWS.card, ds: fc.ds });
  const icx = left + L.pad + L.icon / 2;
  const icy = top + L.h / 2;
  const letter = str(p.letter || L.title.trim()[0] || "•");
  iconTile(g, pal, p.icon, icx, icy, L.icon, { letter, fonts, radius: L.icon * 0.26 });
  if (num(p.badge, 0) > 0) {
    const r = 21;
    const bx = icx + L.icon / 2 - 6;
    const by = icy - L.icon / 2 + 6;
    g.save();
    g.beginPath();
    g.arc(bx, by, r + 4, 0, Math.PI * 2);
    g.fillStyle = rgbaOf(pal.mode === "light" ? "#ffffff" : "#151515");
    g.fill();
    g.beginPath();
    g.arc(bx, by, r, 0, Math.PI * 2);
    g.fillStyle = "#ff3b30";
    g.fill();
    g.restore();
    text(g, String(Math.round(num(p.badge, 1))), bx, by, { ...TYPE.label, tracking: 0, caps: false }, "#ffffff", { fonts, size: 23, weight: 800, align: "center" });
  }
  const tx = left + L.pad * 2 + L.icon;
  const blockH = L.titleSize * 1.2 + (L.lines.length ? 8 + L.lines.length * L.textSize * 1.25 : 0);
  let y = top + (L.h - blockH) / 2 + L.titleSize * 0.6;
  const timeW = L.time ? text(g, L.time, left + L.w - L.pad - 4, y, TYPE.sub, pal.muted, { fonts, size: 25, align: "right" }) : 0;
  setFont(g, TYPE.item, fonts, L.titleSize, 800);
  let title = L.title;
  const room = L.w - (tx - left) - L.pad - timeW - 20;
  while (title.length > 1 && g.measureText(title).width > room) title = title.slice(0, -1);
  text(g, title === L.title ? title : `${title.trimEnd()}…`, tx, y, TYPE.item, pal.text, { fonts, size: L.titleSize, weight: 800 });
  y += L.titleSize * 0.6 + 8;
  for (const line of L.lines) {
    text(g, line, tx, y + L.textSize * 0.62, TYPE.body, pal.mode === "dark" ? alpha(pal.text.slice(0, 7), 0.86) : "#0f1115d9", { fonts, size: L.textSize });
    y += L.textSize * 1.25;
  }
}

/* ---------- раздача ---------- */

type Layouts = { chip: ChipL; title: TitleL; hook: HookL; counter: CounterL; toast: ToastL };
const cache = new WeakMap<object, { key: string; L: unknown }>();

function layoutOf<K extends keyof Layouts>(g: G, p: FlatUiProps, kind: K): Layouts[K] {
  // Кэш на объект пропсов: разметка не зависит от времени, а меряется каждый кадр.
  const key = `${kind}|${p.font ?? ""}|${p.bodyFont ?? ""}`;
  const hit = cache.get(p);
  if (hit && hit.key === key) return hit.L as Layouts[K];
  const L = kind === "chip" ? chipBox(g, p) : kind === "title" ? titleLayout(g, p) : kind === "hook" ? hookLayout(g, p) : kind === "counter" ? counterLayout(g, p) : toastLayout(g, p);
  cache.set(p, { key, L });
  return L as Layouts[K];
}

/** Размер компонента в опорных px (без тени). */
export function measureFlat(g: G, p: FlatUiProps): Box {
  switch (p.component) {
    case "chip":
    case "title":
    case "hook":
    case "counter":
    case "toast": {
      const L = layoutOf(g, p, p.component);
      return { w: L.w, h: L.h };
    }
    default:
      return measureCards(g, p);
  }
}

/** Нарисовать компонент с центром в (0, 0). */
export function drawFlat(fc: FlatFrame, p: FlatUiProps): void {
  const g = fc.g;
  g.save();
  switch (p.component) {
    case "chip": {
      const L = layoutOf(g, p, "chip");
      drawChip(g, fc.pal, L, -L.w / 2, -L.h / 2, p.variant ?? "solid", p.icon, fc.fonts, fc.ds);
      break;
    }
    case "title":
      drawTitle(fc, p, layoutOf(g, p, "title"));
      break;
    case "hook":
      drawHook(fc, p, layoutOf(g, p, "hook"));
      break;
    case "counter":
      drawCounter(fc, p, layoutOf(g, p, "counter"));
      break;
    case "toast":
      drawToast(fc, p, layoutOf(g, p, "toast"));
      break;
    default:
      drawCards(fc, p);
  }
  g.restore();
}
