/**
 * Кирпичики плоской темы на Canvas 2D: карточка с тенью и кромкой, пилюля, подпись капсом в разрядку,
 * перенос по словам, галочка-кружок, полоса, хром окна, значок в плашке. Без зависимостей от движка
 * моушна — те же функции рисуют 2D-слой `ui` и плоские экраны в 3D (node/scene3d).
 *
 * Координаты — px текущей матрицы; `ds` — во сколько раз матрица увеличивает px (shadowBlur матрицу не
 * учитывает — размытие тени домножается на ds, иначе тень на 4K была бы «тоньше», чем в превью).
 */
import { drawFlatIcon } from "./icons.ts";
import { alpha, FLAT_FONTS, hexRgb, type FlatFonts, type FlatPalette, type FlatShadow, type TypeRole, typeFont, WINDOW_DOTS } from "./tokens.ts";

export type G = CanvasRenderingContext2D;

/** Цвет `#rrggbb[aa]` с домноженной прозрачностью → `rgba(…)`; не hex — как есть. */
export function rgbaOf(color: string, a = 1): string {
  const c = hexRgb(color);
  if (!c) return color;
  const m = /^#[0-9a-f]{8}$/i.exec(color.trim()) ? parseInt(color.trim().slice(7, 9), 16) / 255 : 1;
  return `rgba(${c[0]},${c[1]},${c[2]},${Math.round(Math.max(0, Math.min(1, m * a)) * 1000) / 1000})`;
}

/** Скруглённый прямоугольник как подпуть (без beginPath). */
export function rrect(g: G, x: number, y: number, w: number, h: number, r: number): void {
  const rr = Math.max(0, Math.min(r, w / 2, h / 2));
  g.moveTo(x + rr, y);
  g.lineTo(x + w - rr, y);
  g.arcTo(x + w, y, x + w, y + rr, rr);
  g.lineTo(x + w, y + h - rr);
  g.arcTo(x + w, y + h, x + w - rr, y + h, rr);
  g.lineTo(x + rr, y + h);
  g.arcTo(x, y + h, x, y + h - rr, rr);
  g.lineTo(x, y + rr);
  g.arcTo(x, y, x + rr, y, rr);
  g.closePath();
}

function noShadow(g: G): void {
  g.shadowColor = "rgba(0,0,0,0)";
  g.shadowBlur = 0;
  g.shadowOffsetX = 0;
  g.shadowOffsetY = 0;
}

/** Тень по токену под фигурой (рисуется заливкой той же формы). */
export function castShadow(g: G, color: string, sh: FlatShadow | null, ds: number, k = 1): void {
  if (!sh || k <= 0) return noShadow(g);
  g.shadowColor = rgbaOf(color, sh.alpha * k);
  g.shadowBlur = sh.blur * ds;
  g.shadowOffsetX = 0;
  g.shadowOffsetY = sh.y * ds;
}

export type CardOpts = { radius: number; fill?: string; shadow?: FlatShadow | null; border?: boolean; ds?: number; glass?: boolean };

/**
 * Карточка: тень (одна система на все плашки) → заливка `surface` → кромка: тонкая линия `border`, сверху
 * светлее (свет падает сверху, как стекло), и едва заметный блик-градиент по верхней трети.
 */
export function card(g: G, pal: FlatPalette, x: number, y: number, w: number, h: number, o: CardOpts): void {
  const ds = o.ds ?? 1;
  const fill = o.fill ?? pal.surface;
  g.save();
  if (o.shadow) {
    castShadow(g, pal.shadow, o.shadow, ds);
    g.beginPath();
    rrect(g, x, y, w, h, o.radius);
    g.fillStyle = rgbaOf(fill, 1);
    g.fill();
    noShadow(g);
  }
  g.beginPath();
  rrect(g, x, y, w, h, o.radius);
  g.fillStyle = rgbaOf(fill);
  g.fill();
  if (o.glass !== false && pal.mode === "dark") {
    const gl = g.createLinearGradient(0, y, 0, y + Math.min(h, 220));
    gl.addColorStop(0, "rgba(255,255,255,0.045)");
    gl.addColorStop(1, "rgba(255,255,255,0)");
    g.fillStyle = gl;
    g.fill();
  }
  if (o.border !== false) {
    const lw = 2;
    g.beginPath();
    rrect(g, x + lw / 2, y + lw / 2, w - lw, h - lw, Math.max(0, o.radius - lw / 2));
    const edge = g.createLinearGradient(0, y, 0, y + h);
    const top = pal.mode === "dark" ? "rgba(255,255,255,0.16)" : "rgba(255,255,255,0.9)";
    edge.addColorStop(0, top);
    edge.addColorStop(0.35, rgbaOf(pal.border));
    edge.addColorStop(1, rgbaOf(pal.border));
    g.lineWidth = lw;
    g.strokeStyle = edge;
    g.stroke();
  }
  g.restore();
}

/** Пилюля (чип, бейдж): заливка + необязательное свечение цветом заливки. */
export function pill(g: G, x: number, y: number, w: number, h: number, fill: string, glow: FlatShadow | null, ds = 1, radius = h / 2): void {
  g.save();
  if (glow) castShadow(g, fill.slice(0, 7), glow, ds);
  g.beginPath();
  rrect(g, x, y, w, h, radius);
  g.fillStyle = rgbaOf(fill);
  g.fill();
  g.restore();
}

/* ---------- текст ---------- */

export function setFont(g: G, role: TypeRole, fonts: FlatFonts = FLAT_FONTS, size = role.size, weight = role.weight): void {
  g.font = typeFont(role, fonts, size, weight);
}

const caps = (s: string, role: TypeRole) => (role.caps ? s.toLocaleUpperCase("ru-RU") : s);

/** Ширина строки с разрядкой роли (в долях кегля). */
export function textWidth(g: G, text: string, role: TypeRole, size = role.size): number {
  const s = caps(text, role);
  const track = (role.tracking ?? 0) * size;
  return g.measureText(s).width + (track ? track * Math.max(0, Array.from(s).length - 1) : 0);
}

/**
 * Строка текста: `y` — середина заглавных (cap-height ≈ 0.7 кегля у Montserrat и Golos), `align` — по x.
 * Разрядка рисуется по символам (letterSpacing есть не во всех средах: старый Safari, Skia-обёртки).
 */
export function text(g: G, str: string, x: number, y: number, role: TypeRole, color: string, o: { fonts?: FlatFonts; size?: number; weight?: number; align?: "left" | "center" | "right" } = {}): number {
  const size = o.size ?? role.size;
  setFont(g, role, o.fonts, size, o.weight ?? role.weight);
  const s = caps(str, role);
  const w = textWidth(g, str, role, size);
  const align = o.align ?? "left";
  const left = align === "left" ? x : align === "center" ? x - w / 2 : x - w;
  const base = y + size * 0.35;
  g.fillStyle = rgbaOf(color);
  g.textBaseline = "alphabetic";
  const track = (role.tracking ?? 0) * size;
  if (!track) {
    g.textAlign = "left";
    g.fillText(s, left, base);
    return w;
  }
  g.textAlign = "left";
  let cx = left;
  for (const ch of Array.from(s)) {
    g.fillText(ch, cx, base);
    cx += g.measureText(ch).width + track;
  }
  return w;
}

/** Перенос по словам в ширину `maxW` (шрифт уже стоит); не больше `maxLines` строк — последняя с «…». */
export function wrap(g: G, str: string, maxW: number, maxLines = 99): string[] {
  const out: string[] = [];
  for (const para of String(str).split("\n")) {
    let line = "";
    for (const word of para.split(/\s+/).filter(Boolean)) {
      const cand = line ? `${line} ${word}` : word;
      if (g.measureText(cand).width > maxW && line) {
        out.push(line);
        line = word;
      } else line = cand;
    }
    out.push(line);
  }
  if (out.length > maxLines) {
    const kept = out.slice(0, maxLines);
    let last = kept[maxLines - 1];
    while (last.length > 1 && g.measureText(`${last}…`).width > maxW) last = last.slice(0, -1);
    kept[maxLines - 1] = `${last.trimEnd()}…`;
    return kept;
  }
  return out;
}

/** Кегль, при котором строка влезает в `maxW` (не меньше `min` доли исходного). */
export function fitSize(g: G, str: string, role: TypeRole, maxW: number, fonts: FlatFonts = FLAT_FONTS, size = role.size, min = 0.4): number {
  setFont(g, role, fonts, size);
  const w = textWidth(g, str, role, size);
  if (w <= maxW || w <= 0) return size;
  return Math.max(size * min, (size * maxW) / w);
}

/* ---------- элементы ---------- */

/**
 * Кружок-галочка пункта списка: k = 0 — пустое кольцо (ждёт), 0..1 — кольцо заливается акцентом с «попом»
 * и прорисовывается галочка, 1 — готово.
 */
export function checkCircle(g: G, pal: FlatPalette, cx: number, cy: number, r: number, k: number, color = pal.accent): void {
  g.save();
  if (k <= 0) {
    g.beginPath();
    g.arc(cx, cy, r - 1.5, 0, Math.PI * 2);
    g.lineWidth = 3;
    g.strokeStyle = rgbaOf(pal.faint);
    g.stroke();
    g.restore();
    return;
  }
  const pop = k < 0.5 ? 0.6 + 0.9 * k : 1.05 - 0.05 * Math.min(1, (k - 0.5) / 0.5);
  g.translate(cx, cy);
  g.scale(pop, pop);
  g.beginPath();
  g.arc(0, 0, r, 0, Math.PI * 2);
  g.fillStyle = rgbaOf(color);
  g.fill();
  // Галочка рисуется штрихом за вторую половину хода.
  const d = Math.max(0, Math.min(1, (k - 0.3) / 0.6));
  if (d > 0) {
    const pts: [number, number][] = [[-0.42 * r, 0.02 * r], [-0.12 * r, 0.32 * r], [0.44 * r, -0.3 * r]];
    const l1 = Math.hypot(pts[1][0] - pts[0][0], pts[1][1] - pts[0][1]);
    const l2 = Math.hypot(pts[2][0] - pts[1][0], pts[2][1] - pts[1][1]);
    const len = (l1 + l2) * d;
    g.beginPath();
    g.moveTo(pts[0][0], pts[0][1]);
    if (len <= l1) g.lineTo(pts[0][0] + ((pts[1][0] - pts[0][0]) * len) / l1, pts[0][1] + ((pts[1][1] - pts[0][1]) * len) / l1);
    else {
      g.lineTo(pts[1][0], pts[1][1]);
      const k2 = (len - l1) / l2;
      g.lineTo(pts[1][0] + (pts[2][0] - pts[1][0]) * k2, pts[1][1] + (pts[2][1] - pts[1][1]) * k2);
    }
    g.lineWidth = r * 0.24;
    g.lineCap = "round";
    g.lineJoin = "round";
    g.strokeStyle = rgbaOf(pal.accentText);
    g.stroke();
  }
  g.restore();
}

/** Полоса: дорожка `surface2` и заливка акцентом на долю `k` (свечение по желанию). */
export function bar(g: G, pal: FlatPalette, x: number, y: number, w: number, h: number, k: number, color = pal.accent, glow = true, ds = 1): void {
  g.save();
  g.beginPath();
  rrect(g, x, y, w, h, h / 2);
  g.fillStyle = rgbaOf(pal.surface2);
  g.fill();
  const fw = Math.max(0, Math.min(1, k)) * w;
  if (fw > 0.5) {
    if (glow && pal.mode === "dark") castShadow(g, color.slice(0, 7), { blur: h * 1.4, y: 0, alpha: 0.55 }, ds);
    const grad = g.createLinearGradient(x, 0, x + Math.max(fw, h), 0);
    grad.addColorStop(0, rgbaOf(color, 0.82));
    grad.addColorStop(1, rgbaOf(color));
    g.beginPath();
    rrect(g, x, y, Math.max(fw, Math.min(h, w)), h, h / 2);
    g.fillStyle = grad;
    g.fill();
  }
  g.restore();
}

/** Значок в скруглённой плашке (иконка приложения, значок карточки): `soft` — подложка акцента, иначе заливка. */
export function iconTile(g: G, pal: FlatPalette, icon: string | null | undefined, cx: number, cy: number, size: number, o: { soft?: boolean; color?: string; letter?: string; fonts?: FlatFonts; radius?: number } = {}): void {
  const color = o.color ?? pal.accent;
  g.save();
  g.beginPath();
  rrect(g, cx - size / 2, cy - size / 2, size, size, o.radius ?? size * 0.28);
  g.fillStyle = rgbaOf(o.soft ? alpha(color.slice(0, 7), pal.mode === "dark" ? 0.18 : 0.12) : color);
  g.fill();
  const ink = o.soft ? color : pal.accentText;
  if (!drawFlatIcon(g, icon, cx, cy, size * 0.54, rgbaOf(ink), 2.1) && o.letter) {
    g.font = `900 ${Math.round(size * 0.48)}px "${(o.fonts ?? FLAT_FONTS).display}", Arial, sans-serif`;
    g.fillStyle = rgbaOf(ink);
    g.textAlign = "center";
    g.textBaseline = "alphabetic";
    g.fillText(o.letter.slice(0, 2).toLocaleUpperCase("ru-RU"), cx, cy + size * 0.17);
  }
  g.restore();
}

/** Три точки окна (macOS) с центром первой в (x, y). */
export function windowDots(g: G, x: number, y: number, r: number, gap = r * 3.2): void {
  WINDOW_DOTS.forEach((c, i) => {
    g.beginPath();
    g.arc(x + i * gap, y, r, 0, Math.PI * 2);
    g.fillStyle = c;
    g.fill();
  });
}

/** Число по-русски: разряды неразрывным пробелом, запятая, префикс/суффикс. */
export function formatNumber(v: number, o: { decimals?: number; prefix?: string; suffix?: string; group?: string } = {}): string {
  const d = Math.max(0, Math.min(3, Math.round(o.decimals ?? 0)));
  const fixed = Math.abs(v).toFixed(d);
  const [int, frac] = fixed.split(".");
  const sep = o.group ?? " ";
  const grouped = sep ? int.replace(/\B(?=(\d{3})+(?!\d))/g, sep) : int;
  const sign = v < 0 && Number(fixed) !== 0 ? "−" : "";
  return `${o.prefix ?? ""}${sign}${grouped}${frac ? `,${frac}` : ""}${o.suffix ?? ""}`;
}

/** Плавная кривая «выход» (outCubic) и «пружинка» (outBack) — свои, модуль без зависимостей. */
export const outCubic = (x: number) => 1 - Math.pow(1 - Math.max(0, Math.min(1, x)), 3);
export function outBack(x: number): number {
  const t = Math.max(0, Math.min(1, x));
  const c1 = 1.70158;
  const c3 = c1 + 1;
  return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2);
}
