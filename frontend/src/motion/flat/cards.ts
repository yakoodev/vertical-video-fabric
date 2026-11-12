/**
 * Карточки плоской темы: карточка с подписью и крупным числом (`stat`), сравнение полосами (`bars`),
 * чек-лист с галочками по одной (`checklist`) и окно приложения/браузера/чата (`window`).
 * Общая сетка: поля 44, подпись капсом сверху, крупное — display 900, мелкое — body; одна кромка и тень.
 */
import { bar, card, checkCircle, formatNumber, type G, iconTile, outBack, outCubic, pill, rgbaOf, rrect, setFont, text, textWidth, windowDots, wrap } from "./draw.ts";
import { drawFlatIcon, flatIconName } from "./icons.ts";
import { type FlatFrame, type FlatItem, type FlatUiProps, fontsOf, itemAt, items, num, str } from "./kit.ts";
import { alpha, type FlatFonts, RADII, SHADOWS, TYPE } from "./tokens.ts";

const PAD = 44;

/** Шапка карточки: значок (мягкая плашка) + подпись капсом. Возвращает высоту. */
function headerH(p: FlatUiProps): number {
  if (!p.label) return 0;
  return flatIconName(p.icon) ? 60 : TYPE.label.size * 1.1;
}

function drawHeader(fc: FlatFrame, p: FlatUiProps, x: number, y: number, h: number): void {
  if (!p.label) return;
  const { g, pal, fonts } = fc;
  let tx = x;
  if (flatIconName(p.icon)) {
    iconTile(g, pal, p.icon, x + h / 2, y + h / 2, h, { soft: true, radius: RADII.sm });
    tx += h + 20;
  }
  text(g, p.label, tx, y + h / 2, TYPE.label, pal.muted, { fonts });
}

/* ---------- карточка с числом ---------- */

type StatL = { w: number; h: number; head: number; size: number; numW: number; unitSize: number; unitW: number; delta: { w: number; h: number; size: number } | null; caption: string[]; bar: boolean };

function statLayout(g: G, p: FlatUiProps): StatL {
  const fonts = fontsOf(p);
  const w = Math.max(360, num(p.w, 900));
  const inner = w - PAD * 2;
  const fmt = { decimals: p.decimals, prefix: p.prefix, suffix: p.suffix };
  const longest = [formatNumber(num(p.from, 0), fmt), formatNumber(num(p.value, 0), fmt)].sort((a, b) => b.length - a.length)[0];
  const unit = str(p.unit);
  let delta: StatL["delta"] = null;
  let size = num(p.size, TYPE.number.size);
  const measure = (s: number) => {
    setFont(g, TYPE.number, fonts, s);
    const nw = g.measureText(longest).width;
    const us = Math.round(s * 0.36);
    setFont(g, TYPE.heading, fonts, us, 800);
    const uw = unit ? g.measureText(unit).width : 0;
    let d: StatL["delta"] = null;
    if (p.delta) {
      const ds = Math.round(Math.max(26, s * 0.26));
      setFont(g, TYPE.chip, fonts, ds);
      d = { w: textWidth(g, p.delta, { ...TYPE.chip, caps: false }, ds) + ds * 1.5 + ds * 1.1, h: Math.round(ds * 1.7), size: ds };
    }
    return { nw, us, uw, d };
  };
  let m = measure(size);
  const total = () => m.nw + (unit ? 18 + m.uw : 0) + (m.d ? 24 + m.d.w : 0);
  for (let i = 0; i < 8 && total() > inner; i++) {
    size *= Math.max(0.6, inner / total());
    m = measure(size);
  }
  delta = m.d;
  setFont(g, TYPE.sub, fonts, 28);
  const caption = p.text ? wrap(g, p.text, inner, 3) : [];
  const head = headerH(p);
  const hasBar = typeof p.progress === "number";
  const h = PAD * 2 + (head ? head + 18 : 0) + size * 1.02 + (hasBar ? 30 + 20 : 0) + (caption.length ? 22 + caption.length * 28 * 1.3 : 0);
  return { w, h, head, size, numW: m.nw, unitSize: m.us, unitW: m.uw, delta, caption, bar: hasBar };
}

function drawStat(fc: FlatFrame, p: FlatUiProps, L: StatL): void {
  const { g, pal, fonts } = fc;
  const left = -L.w / 2;
  const top = -L.h / 2;
  card(g, pal, left, top, L.w, L.h, { radius: RADII.lg, shadow: SHADOWS.card, ds: fc.ds });
  const x = left + PAD;
  let y = top + PAD;
  if (L.head) {
    drawHeader(fc, p, x, y, L.head);
    y += L.head + 18;
  }
  const v = num(p.from, 0) + (num(p.value, 0) - num(p.from, 0)) * fc.progress;
  const cy = y + L.size * 0.5;
  text(g, formatNumber(v, { decimals: p.decimals, prefix: p.prefix, suffix: p.suffix }), x, cy, TYPE.number, pal.text, { fonts, size: L.size });
  if (p.unit) text(g, p.unit, x + L.numW + 18, cy + L.size * 0.5 * 0.7 - L.unitSize * 0.35, TYPE.heading, pal.muted, { fonts, size: L.unitSize, weight: 800 });
  if (L.delta && p.delta) {
    // Чип изменения справа: мягкая подложка акцента, стрелка роста и текст; выпрыгивает к концу счёта.
    const k = outBack((fc.progress - 0.55) / 0.45);
    if (k > 0.01) {
      const d = L.delta;
      const dx = left + L.w - PAD - d.w / 2;
      g.save();
      g.translate(dx, cy);
      g.scale(k, k);
      pill(g, -d.w / 2, -d.h / 2, d.w, d.h, pal.accentSoft, null);
      const down = /^[−-]/.test(p.delta.trim());
      drawFlatIcon(g, down ? "trending-down" : "trending-up", -d.w / 2 + d.size * 0.75 + d.size * 0.5, 0, d.size * 1.05, rgbaOf(pal.accent), 2.6);
      text(g, p.delta, -d.w / 2 + d.size * 0.75 + d.size * 1.25, 0, { ...TYPE.chip, caps: false }, pal.accent, { fonts, size: d.size });
      g.restore();
    }
  }
  y += L.size * 1.02;
  if (L.bar) {
    y += 30;
    bar(g, pal, x, y, L.w - PAD * 2, 20, num(p.progress, 0) * fc.progress, pal.accent, true, fc.ds);
    y += 20;
  }
  if (L.caption.length) {
    y += 22;
    for (const line of L.caption) {
      text(g, line, x, y + 28 * 0.62, TYPE.sub, pal.muted, { fonts, size: 28 });
      y += 28 * 1.3;
    }
  }
}

/* ---------- сравнение полосами ---------- */

type BarsL = { w: number; h: number; head: number; rows: FlatItem[]; max: number; rowH: number; markerTop: number };

const BAR_H = 24;
const ROW_TEXT = 32;
const ROW_GAP = 34;

function barsLayout(_g: G, p: FlatUiProps): BarsL {
  const w = Math.max(400, num(p.w, 920));
  const rows = items(p).slice(0, 8);
  const max = Math.max(1e-9, ...rows.map((r) => num(r.max, 0)), ...rows.map((r) => num(r.value, 0)), typeof p.value === "number" ? p.value : 0);
  const head = headerH(p) || (p.markerLabel ? TYPE.label.size * 1.1 : 0);
  const rowH = ROW_TEXT * 1.2 + 16 + BAR_H;
  const markerTop = PAD + (head ? head + 30 : 0);
  const h = markerTop + rows.length * rowH + Math.max(0, rows.length - 1) * ROW_GAP + PAD;
  return { w, h, head, rows, max, rowH, markerTop };
}

function drawBars(fc: FlatFrame, p: FlatUiProps, L: BarsL): void {
  const { g, pal, fonts } = fc;
  const left = -L.w / 2;
  const top = -L.h / 2;
  card(g, pal, left, top, L.w, L.h, { radius: RADII.lg, shadow: SHADOWS.card, ds: fc.ds });
  const x = left + PAD;
  const bw = L.w - PAD * 2;
  if (L.head) drawHeader(fc, p, x, top + PAD, L.head);
  let y = top + L.markerTop;
  L.rows.forEach((row, i) => {
    const k = outCubic((fc.local - itemAt(p, i)) / 0.9);
    const frac = Math.max(0, Math.min(1, num(row.value, 0) / (num(row.max, 0) || L.max)));
    const color = row.color || (row.dim ? (pal.mode === "dark" ? "#ffffff40" : "#0f111538") : pal.accent);
    const ty = y + ROW_TEXT * 0.6;
    text(g, str(row.text), x, ty, TYPE.item, row.dim ? pal.muted : pal.text, { fonts, size: ROW_TEXT, weight: 700 });
    if (row.note) {
      const a = Math.min(1, Math.max(0, (k - 0.6) / 0.4));
      g.save();
      g.globalAlpha *= a;
      text(g, row.note, x + bw, ty, TYPE.item, row.dim ? pal.muted : pal.accent, { fonts, size: ROW_TEXT - 2, weight: 800, align: "right" });
      g.restore();
    }
    bar(g, pal, x, y + ROW_TEXT * 1.2 + 16, bw, BAR_H, frac * k, color.slice(0, 9), !row.dim, fc.ds);
    y += L.rowH + ROW_GAP;
  });
  if (typeof p.marker === "number") {
    // Метка-порог («1:00» — граница монетизации) поверх полос: тонкая линия с подписью сверху.
    const mx = x + bw * Math.max(0, Math.min(1, p.marker));
    const y0 = top + L.markerTop - 6;
    const y1 = top + L.h - PAD + 8;
    g.save();
    g.fillStyle = rgbaOf(pal.text, 0.9);
    g.fillRect(mx - 1.5, y0, 3, y1 - y0);
    g.restore();
    if (p.markerLabel) text(g, p.markerLabel, mx, y0 - 22, { ...TYPE.label, tracking: 0.04 }, pal.text, { fonts, size: 26, align: "center" });
  }
}

/* ---------- чек-лист ---------- */

type Row = { lines: string[]; sub: string[]; h: number };
type CheckL = { w: number; h: number; chrome: number; head: string[]; headSize: number; label: boolean; rows: Row[]; top: number };

const CHROME = 80;
const ITEM = 34;
const SUB = 26;
const CIRCLE = 22;
const ITEM_GAP = 30;

function rowsLayout(g: G, list: FlatItem[], width: number, fonts: FlatFonts): Row[] {
  return list.slice(0, 10).map((it) => {
    setFont(g, TYPE.item, fonts, ITEM);
    const lines = wrap(g, str(it.text), width, 2);
    setFont(g, TYPE.sub, fonts, SUB);
    const sub = it.sub ? wrap(g, it.sub, width, 2) : [];
    return { lines, sub, h: lines.length * ITEM * 1.2 + (sub.length ? 6 + sub.length * SUB * 1.25 : 0) };
  });
}

function checkLayout(g: G, p: FlatUiProps): CheckL {
  const fonts = fontsOf(p);
  const w = Math.max(420, num(p.w, 900));
  const windowV = p.variant === "window";
  const chrome = windowV ? CHROME : 0;
  const headSize = 42;
  setFont(g, TYPE.heading, fonts, headSize);
  const head = p.title ? wrap(g, p.title, w - PAD * 2, 2) : [];
  const label = !windowV && !!p.label;
  const rows = rowsLayout(g, items(p), w - PAD * 2 - CIRCLE * 2 - 22, fonts);
  const top = chrome + PAD + (label ? TYPE.label.size * 1.1 + 18 : 0) + (head.length ? head.length * headSize * 1.15 + 30 : 0);
  const h = top + rows.reduce((s, r) => s + r.h, 0) + Math.max(0, rows.length - 1) * ITEM_GAP + PAD;
  return { w, h, chrome, head, headSize, label, rows, top };
}

/** Строки чек-листа с левым верхним углом (x, y): галочки по времени пунктов. */
function drawRows(fc: FlatFrame, p: FlatUiProps, rows: Row[], x: number, y: number): void {
  const { g, pal, fonts } = fc;
  const list = items(p);
  const appear = p.reveal === "appear";
  rows.forEach((row, i) => {
    const it = list[i] || {};
    const t0 = itemAt(p, i);
    const doneK = it.done === false ? 0 : Math.max(0, Math.min(1, (fc.local - t0) / 0.4));
    const showK = appear ? outCubic((fc.local - t0 + 0.25) / 0.35) : 1;
    if (showK <= 0.001) {
      y += row.h + ITEM_GAP;
      return;
    }
    g.save();
    g.globalAlpha *= showK;
    g.translate(0, (1 - showK) * 18);
    const on = doneK > 0;
    const cy = y + ITEM * 0.6;
    checkCircle(g, pal, x + CIRCLE, cy, CIRCLE, doneK, it.color || pal.accent);
    const tx = x + CIRCLE * 2 + 22;
    let ty = y;
    // Неотмеченное — бледнее; отмеченное «загорается» вместе с галочкой.
    const ink = on || appear ? pal.text : alpha(pal.text.slice(0, 7), pal.mode === "dark" ? 0.5 : 0.45);
    for (const line of row.lines) {
      text(g, line, tx, ty + ITEM * 0.6, TYPE.item, ink, { fonts, size: ITEM });
      ty += ITEM * 1.2;
    }
    if (row.sub.length) {
      ty += 6;
      for (const line of row.sub) {
        text(g, line, tx, ty + SUB * 0.62, TYPE.sub, on || appear ? pal.muted : pal.faint, { fonts, size: SUB });
        ty += SUB * 1.25;
      }
    }
    g.restore();
    y += row.h + ITEM_GAP;
  });
}

/** Хром окна (точки + имя приложения / адресная строка) в прямоугольнике шапки. */
function drawChrome(fc: FlatFrame, x: number, y: number, w: number, h: number, title: string, url: string | null, radius: number): void {
  const { g, pal, fonts } = fc;
  g.save();
  g.beginPath();
  rrect(g, x, y, w, h + radius, radius);
  g.clip();
  g.fillStyle = rgbaOf(pal.surface3);
  g.fillRect(x, y, w, h);
  g.fillStyle = rgbaOf(pal.border);
  g.fillRect(x, y + h - 2, w, 2);
  g.restore();
  windowDots(g, x + 40, y + h / 2, 10.5);
  if (url !== null) {
    const ax = x + 150;
    const aw = w - 150 - 34;
    const ah = 50;
    g.save();
    g.beginPath();
    rrect(g, ax, y + (h - ah) / 2, aw, ah, ah / 2);
    g.fillStyle = rgbaOf(pal.surface2);
    g.fill();
    g.restore();
    drawFlatIcon(g, "lock", ax + 32, y + h / 2, 24, rgbaOf(pal.muted), 2.2);
    setFont(g, TYPE.sub, fonts, 27);
    let u = url;
    while (u.length > 1 && g.measureText(u).width > aw - 90) u = u.slice(0, -1);
    text(g, u === url ? u : `${u}…`, ax + 58, y + h / 2, TYPE.sub, pal.muted, { fonts, size: 27 });
  } else if (title) {
    text(g, title, x + 146, y + h / 2, TYPE.item, pal.text, { fonts, size: 30, weight: 700 });
  }
}

function drawChecklist(fc: FlatFrame, p: FlatUiProps, L: CheckL): void {
  const { g, pal, fonts } = fc;
  const left = -L.w / 2;
  const top = -L.h / 2;
  card(g, pal, left, top, L.w, L.h, { radius: RADII.lg, shadow: SHADOWS.card, ds: fc.ds });
  if (L.chrome) drawChrome(fc, left, top, L.w, L.chrome, str(p.label), null, RADII.lg);
  let y = top + L.chrome + PAD;
  if (L.label) {
    text(g, str(p.label), left + PAD, y + TYPE.label.size * 0.55, TYPE.label, pal.muted, { fonts });
    y += TYPE.label.size * 1.1 + 18;
  }
  for (const line of L.head) {
    text(g, line, left + PAD, y + L.headSize * 0.58, TYPE.heading, pal.text, { fonts, size: L.headSize });
    y += L.headSize * 1.15;
  }
  if (L.head.length) y += 30;
  drawRows(fc, p, L.rows, left + PAD, top + L.top);
}

/* ---------- окно: приложение, браузер, чат ---------- */

type Bubble = { lines: string[]; w: number; h: number; me: boolean };
type WindowL = { w: number; h: number; chrome: number; rows: Row[] | null; bubbles: Bubble[] | null; body: string[]; titleLines: string[] };

const MSG = 31;

function windowLayout(g: G, p: FlatUiProps): WindowL {
  const fonts = fontsOf(p);
  const w = Math.max(480, num(p.w, 940));
  const chrome = 84;
  const inner = w - PAD * 2;
  const list = items(p);
  let rows: Row[] | null = null;
  let bubbles: Bubble[] | null = null;
  let body: string[] = [];
  // Шапка окна — имя приложения (label, иначе title); title при заданном label — заголовок в теле.
  setFont(g, TYPE.heading, fonts, 42);
  const titleLines = p.variant !== "chat" && p.label && p.title ? wrap(g, p.title, inner, 2) : [];
  const headH = titleLines.length ? titleLines.length * 42 * 1.15 + 30 : 0;
  let contentH = 0;
  if (p.variant === "chat") {
    setFont(g, TYPE.body, fonts, MSG);
    bubbles = list.slice(0, 10).map((it) => {
      const lines = wrap(g, str(it.text), inner * 0.74 - 52, 4);
      const tw = Math.max(...lines.map((l) => g.measureText(l).width), 40);
      return { lines, w: tw + 52, h: lines.length * MSG * 1.28 + 30, me: it.from === "me" };
    });
    contentH = bubbles.reduce((s, b) => s + b.h, 0) + Math.max(0, bubbles.length - 1) * 16;
  } else if (list.length) {
    rows = rowsLayout(g, list, inner - CIRCLE * 2 - 22, fonts);
    contentH = headH + rows.reduce((s, r) => s + r.h, 0) + Math.max(0, rows.length - 1) * ITEM_GAP;
  } else {
    setFont(g, TYPE.body, fonts, 32);
    body = p.text ? wrap(g, p.text, inner, 8) : [];
    contentH = headH + body.length * 32 * 1.35;
  }
  const h = Math.max(num(p.h, 0), chrome + PAD * 2 + Math.max(60, contentH));
  return { w, h, chrome, rows, bubbles, body, titleLines };
}

function drawWindow(fc: FlatFrame, p: FlatUiProps, L: WindowL): void {
  const { g, pal, fonts } = fc;
  const left = -L.w / 2;
  const top = -L.h / 2;
  card(g, pal, left, top, L.w, L.h, { radius: RADII.lg, shadow: SHADOWS.card, ds: fc.ds });
  const browser = p.variant === "browser";
  drawChrome(fc, left, top, L.w, L.chrome, browser ? "" : str(p.label || p.title || (p.variant === "chat" ? "Чат" : "Приложение")), browser ? str(p.url || "example.com") : null, RADII.lg);
  const x = left + PAD;
  let y = top + L.chrome + PAD;
  for (const line of L.titleLines) {
    text(g, line, x, y + 42 * 0.58, TYPE.heading, pal.text, { fonts, size: 42 });
    y += 42 * 1.15;
  }
  if (L.titleLines.length) y += 30;
  if (L.rows) {
    drawRows(fc, p, L.rows, x, y);
    return;
  }
  if (L.bubbles) {
    const list = items(p);
    const inner = L.w - PAD * 2;
    L.bubbles.forEach((b, i) => {
      const t0 = itemAt(p, i);
      const typing = !b.me && list[i] && fc.local >= t0 - 0.7 && fc.local < t0;
      const k = outBack((fc.local - t0) / 0.32);
      const bx = b.me ? x + inner - b.w : x;
      if (typing) {
        // «Печатает…»: три точки в пузыре, пока ответ не пришёл.
        const tw = 108;
        const th = MSG * 1.28 + 30;
        g.save();
        g.beginPath();
        rrect(g, x, y, tw, th, 28);
        g.fillStyle = rgbaOf(pal.surface2);
        g.fill();
        for (let d = 0; d < 3; d++) {
          const a = 0.35 + 0.65 * (0.5 + 0.5 * Math.sin(fc.local * 9 - d * 1.1));
          g.beginPath();
          g.arc(x + 32 + d * 22, y + th / 2, 7, 0, Math.PI * 2);
          g.fillStyle = rgbaOf(pal.text, a * 0.8);
          g.fill();
        }
        g.restore();
      }
      if (k > 0.01) {
        g.save();
        const ox = b.me ? bx + b.w : bx;
        g.translate(ox, y + b.h);
        g.scale(0.7 + 0.3 * k, 0.7 + 0.3 * k);
        g.translate(-ox, -(y + b.h));
        g.globalAlpha *= Math.min(1, k * 1.5);
        g.beginPath();
        rrect(g, bx, y, b.w, b.h, 28);
        g.fillStyle = rgbaOf(b.me ? pal.accent : pal.surface2);
        g.fill();
        b.lines.forEach((line, j) => text(g, line, bx + 26, y + 15 + MSG * 1.28 * j + MSG * 0.64, TYPE.body, b.me ? pal.accentText : pal.text, { fonts, size: MSG }));
        g.restore();
      }
      y += b.h + 16;
    });
    return;
  }
  for (const line of L.body) {
    text(g, line, x, y + 32 * 0.62, TYPE.body, pal.text, { fonts, size: 32 });
    y += 32 * 1.35;
  }
}

/* ---------- раздача ---------- */

type Layouts = { stat: StatL; bars: BarsL; checklist: CheckL; window: WindowL };
type CardKind = keyof Layouts;
const cache = new WeakMap<object, { key: string; L: unknown }>();

function kindOf(p: FlatUiProps): CardKind {
  return p.component === "bars" || p.component === "checklist" || p.component === "window" ? p.component : "stat";
}

function layoutOf<K extends CardKind>(g: G, p: FlatUiProps, kind: K): Layouts[K] {
  const key = `${kind}|${p.font ?? ""}|${p.bodyFont ?? ""}`;
  const hit = cache.get(p);
  if (hit && hit.key === key) return hit.L as Layouts[K];
  const L = kind === "bars" ? barsLayout(g, p) : kind === "checklist" ? checkLayout(g, p) : kind === "window" ? windowLayout(g, p) : statLayout(g, p);
  cache.set(p, { key, L });
  return L as Layouts[K];
}

export function measureCards(g: G, p: FlatUiProps): { w: number; h: number } {
  const L = layoutOf(g, p, kindOf(p));
  return { w: L.w, h: L.h };
}

export function drawCards(fc: FlatFrame, p: FlatUiProps): void {
  const kind = kindOf(p);
  if (kind === "bars") drawBars(fc, p, layoutOf(fc.g, p, "bars"));
  else if (kind === "checklist") drawChecklist(fc, p, layoutOf(fc.g, p, "checklist"));
  else if (kind === "window") drawWindow(fc, p, layoutOf(fc.g, p, "window"));
  else drawStat(fc, p, layoutOf(fc.g, p, "stat"));
}
