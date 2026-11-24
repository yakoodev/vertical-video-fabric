/**
 * Картинки мем-серии 9 «Промпт на три страницы» (экран окна чата ``chat_panel`` и ноутбука ``laptop``):
 *
 * - ``prompt_wall`` — мессенджер с одной огромной «простынёй» юзера. ``paras: [{text, kind?}]`` — абзацы пузыря;
 *   в ``text`` разметка ``**…**`` — ключевая фраза (крупнее, жёлтый маркер проводится, когда фраза въезжает в
 *   кадр); ``kind``: "filler" — мелкий серый «бойлерплейт», "last" — последняя строка мелко и серо (её ``**…**`` —
 *   синим). ``scroll: [{t, at?, v?, ease?}]`` — прокрутка: ``at`` — номер абзаца (его верх встаёт на 28% окна),
 *   ``v`` — доля всей прокрутки 0..1; между ключами inOutSine (``ease: "step"`` — скачком). ``thumb`` — доля
 *   ползунка (крошечный, «страниц много»), ``pages: [{t, text}]`` — плашка «стр. N из 3» (поп при смене),
 *   ``zoom: [{t, v}]`` — последняя строка вылетает крупно в центр, остальное темнеет. ``paste`` — когда пузырь
 *   появился (поп), ``status: [{t, text, color}]`` — статус в шапке, ``fs`` — кегль при ширине 720, ``scale``.
 * - ``button_site`` — страничка сайта с большой кнопкой: цвет ``from`` → ``to`` в ``at`` (волна от клика и курсор),
 *   ``label`` — надпись кнопки, ``title`` — заголовок, ``toast`` — плашка после смены; ``empty`` — кнопки нет
 *   (серия 10): пунктир на её месте, курсор ищет её с ``at``.
 *
 * Чистые функции разметки и прокрутки (``parseMarks``, ``layoutWall``, ``scrollAt``) — без холста, для тестов.
 */
import { drawRich, drawUserAvatar, measureRich, rr } from "./meme_painters_rp.ts";
import { DESIGN_PAINTERS } from "./meme_painters_design.ts";
import { drawEmptySpot, NIGHT_PAINTERS } from "./meme_painters_night.ts";

type G = CanvasRenderingContext2D;
type Painter = (g: G, W: number, H: number, t: number, p: Record<string, any>) => void;

const FONT = (w: number, px: number) => `${w} ${Math.round(px)}px Montserrat, Arial, sans-serif`;
const clamp01 = (x: number) => (x < 0 ? 0 : x > 1 ? 1 : x);
const inOut = (k: number) => 0.5 - 0.5 * Math.cos(Math.PI * clamp01(k));
const outBack = (k: number) => {
  const c1 = 1.70158, c3 = c1 + 1;
  return 1 + c3 * Math.pow(k - 1, 3) + c1 * Math.pow(k - 1, 2);
};
const lerp = (a: number, b: number, k: number) => a + (b - a) * k;

/* ---------------------------------------------------------------- разметка и раскладка (без холста) */

export interface Word { s: string; hl: boolean; glue?: boolean }
export interface Para { text: string; kind?: string }
export interface Run { x: number; w: number; s: string; hl: boolean; px: number }
export interface Line { para: number; y: number; h: number; base: number; runs: Run[]; kind: string }
export interface Wall { lines: Line[]; paraTop: number[]; height: number }
export type Measure = (s: string, px: number, weight: number) => number;

/** Слова абзаца с флагом выделения: ``**…**`` может охватывать несколько слов. */
export function parseMarks(text: string): Word[] {
  const out: Word[] = [];
  const pieces = String(text).split("**");
  pieces.forEach((piece, i) => {
    // Знак сразу за выделением («**опытом**.») — без пробела, приклеен к слову.
    const glueFirst = i > 0 && piece.length > 0 && !/^\s/.test(piece) && !/\s$/.test(pieces[i - 1]) && out.length > 0;
    piece.split(/\s+/).filter(Boolean).forEach((s, j) => out.push({ s, hl: i % 2 === 1, ...(glueFirst && j === 0 ? { glue: true } : {}) }));
  });
  return out;
}

/** Кегль и жирность слова по виду абзаца. */
export function styleOf(kind: string, hl: boolean, fs: number): { px: number; weight: number } {
  if (kind === "filler") return { px: fs * 0.82, weight: 600 };
  if (kind === "last") return { px: fs * 0.7, weight: hl ? 800 : 600 };
  return hl ? { px: fs * 1.34, weight: 900 } : { px: fs, weight: 700 };
}

/** Раскладка абзацев в колонку ширины ``maxW``: жадный перенос по словам, высота строки — по самому крупному слову. */
export function layoutWall(paras: Para[], maxW: number, fs: number, measure: Measure): Wall {
  const lines: Line[] = [];
  const paraTop: number[] = [];
  let y = 0;
  paras.forEach((para, pi) => {
    const kind = para.kind || "";
    paraTop.push(y);
    const words = parseMarks(para.text);
    let cur: Run[] = [];
    let x = 0;
    const flush = () => {
      if (!cur.length) return;
      const top = Math.max(...cur.map((r) => r.px));
      const h = top * 1.3;
      lines.push({ para: pi, y, h, base: y + top * 1.02, runs: cur, kind });
      y += h;
      cur = [];
      x = 0;
    };
    for (const w of words) {
      const st = styleOf(kind, w.hl, fs);
      const ww = measure(w.s, st.px, st.weight);
      const gap = cur.length && !w.glue ? measure(" ", st.px, st.weight) : 0;
      if (cur.length && !w.glue && x + gap + ww > maxW) flush();
      const sp = cur.length && !w.glue ? measure(" ", st.px, st.weight) : 0;
      cur.push({ x: x + sp, w: ww, s: w.s, hl: w.hl, px: st.px });
      x += sp + ww;
    }
    flush();
    y += fs * (kind === "filler" ? 0.35 : 0.55);
  });
  return { lines, paraTop, height: y };
}

/** Прокрутка (пиксели) в момент ``t``: ключи ``at`` (абзац) или ``v`` (доля), между ними — inOutSine. */
export function scrollAt(keys: Record<string, any>[] | undefined, t: number, wall: Wall, viewH: number): number {
  const max = Math.max(0, wall.height - viewH * 0.9);
  const val = (k: Record<string, any>) => {
    if (k.at !== undefined) {
      const top = wall.paraTop[Math.max(0, Math.min(wall.paraTop.length - 1, Number(k.at)))] ?? 0;
      return Math.max(0, Math.min(max, top - viewH * 0.28));
    }
    return max * clamp01(Number(k.v ?? 0));
  };
  if (!keys || !keys.length) return 0;
  if (t <= keys[0].t) return val(keys[0]);
  for (let i = 0; i < keys.length - 1; i++) {
    const a = keys[i], b = keys[i + 1];
    if (t < b.t) {
      if (b.ease === "step") return val(a);
      return lerp(val(a), val(b), inOut((t - a.t) / Math.max(1e-4, b.t - a.t)));
    }
  }
  return val(keys[keys.length - 1]);
}

function sample(keys: Record<string, any>[] | undefined, t: number, field: string, dflt: number): number {
  if (!keys || !keys.length) return dflt;
  if (t <= keys[0].t) return Number(keys[0][field]);
  for (let i = 0; i < keys.length - 1; i++) {
    const a = keys[i], b = keys[i + 1];
    if (t < b.t) {
      if (b.ease === "step") return Number(a[field]);
      return lerp(Number(a[field]), Number(b[field]), inOut((t - a.t) / Math.max(1e-4, b.t - a.t)));
    }
  }
  return Number(keys[keys.length - 1][field]);
}

function stepOf<T extends Record<string, any>>(keys: T[] | undefined, t: number): T | null {
  let cur: T | null = null;
  for (const k of keys || []) if (k.t <= t + 1e-9) cur = k;
  return cur;
}

/* ---------------------------------------------------------------- простыня */

const INK = "#121016";
const GREY = "#4c5162";
const MARK = "#ffd23f";
const BLUE = "#2f6fb5";

function drawHeader(g: G, W: number, s: number, t: number, p: Record<string, any>): number {
  const headH = 118 * s;
  g.fillStyle = "#ffffff";
  g.fillRect(0, 0, W, headH);
  g.fillStyle = "#d9dee8";
  g.fillRect(0, headH - 3 * s, W, 3 * s);
  const ar = 38 * s;
  drawUserAvatar(g, 34 * s + ar, headH / 2, ar, p.hoodie || "#6f86e0");
  g.fillStyle = INK;
  g.font = FONT(800, 38 * s);
  g.textBaseline = "alphabetic";
  g.fillText(String(p.name || "юзер"), 34 * s + ar * 2 + 22 * s, headH * 0.47);
  const st = stepOf(p.status, t);
  if (st) {
    g.font = FONT(700, 27 * s);
    g.fillStyle = st.color || "#2e9d5b";
    g.fillText(String(st.text), 34 * s + ar * 2 + 22 * s, headH * 0.8);
  }
  return headH;
}

const promptWall: Painter = (g, W, H, t, p) => {
  const s = (W / 720) * Number(p.scale ?? 1);
  g.fillStyle = p.bg || "#e9eef6";
  g.fillRect(0, 0, W, H);
  g.fillStyle = "rgba(120,140,180,0.12)";
  for (let y = 0; y < H; y += 46 * s) for (let x = ((y / (46 * s)) % 2) * 23 * s; x < W; x += 46 * s) {
    g.beginPath();
    g.arc(x, y, 4 * s, 0, Math.PI * 2);
    g.fill();
  }
  const headH = drawHeader(g, W, s, t, p);
  // Поле ввода.
  const inH = 96 * s;
  const inY = H - inH;
  g.fillStyle = "#ffffff";
  g.fillRect(0, inY, W, inH);
  g.fillStyle = "#eef1f6";
  rr(g, 24 * s, inY + 18 * s, W - 140 * s, inH - 36 * s, 30 * s);
  g.fill();
  g.font = FONT(700, 28 * s);
  g.fillStyle = "#9aa1b0";
  g.fillText("Сообщение…", 52 * s, inY + inH / 2 + 10 * s);
  g.fillStyle = p.accent || "#d97757";
  g.beginPath();
  g.arc(W - 64 * s, inY + inH / 2, 30 * s, 0, Math.PI * 2);
  g.fill();
  // Пузырь-простыня.
  const paste = Number(p.paste ?? 0);
  if (t < paste) return;
  const fs = Number(p.fs ?? 44) * s;
  const areaTop = headH + 12 * s, areaBot = inY - 12 * s;
  const viewH = areaBot - areaTop;
  const bx = 22 * s, bw = W - 64 * s;
  const pad = 26 * s;
  const measure: Measure = (str, px, weight) => {
    g.font = FONT(weight, px);
    return measureRich(g, str, px);
  };
  const wall = layoutWall(p.paras || [], bw - pad * 2, fs, measure);
  const scroll = scrollAt(p.scroll, t, wall, viewH);
  const k = clamp01((t - paste) / 0.28);
  g.save();
  g.beginPath();
  g.rect(0, areaTop, W, viewH);
  g.clip();
  const y0 = areaTop + pad - scroll;
  g.translate(bx, areaTop);
  const sc = 0.4 + 0.6 * outBack(k);
  g.scale(sc, sc);
  g.translate(-bx, -areaTop);
  g.fillStyle = "#ffffff";
  rr(g, bx, y0 - pad, bw, wall.height + pad * 2, 30 * s);
  g.fill();
  for (const ln of wall.lines) {
    const yy = y0 + ln.y;
    if (yy > areaBot + 20 * s || yy + ln.h < areaTop - 20 * s) continue;
    const base = y0 + ln.base;
    // Маркер: проводится слева направо, когда строка поднимается от низа окна к его середине.
    const rise = clamp01((areaBot - base) / (viewH * 0.32));
    let runStart = -1;
    const hlRuns: [number, number, number][] = [];
    ln.runs.forEach((r, i) => {
      if (r.hl && runStart < 0) runStart = i;
      const endRun = r.hl && (i === ln.runs.length - 1 || !ln.runs[i + 1].hl);
      if (endRun) {
        hlRuns.push([runStart, i, r.px]);
        runStart = -1;
      }
    });
    if (ln.kind !== "last" && ln.kind !== "filler") {
      for (const [a, b, px] of hlRuns) {
        const x0 = bx + pad + ln.runs[a].x - px * 0.12;
        const x1 = bx + pad + ln.runs[b].x + ln.runs[b].w + px * 0.12;
        g.fillStyle = MARK;
        rr(g, x0, base - px * 0.86, (x1 - x0) * inOut(rise), px * 1.08, px * 0.18);
        g.fill();
      }
    }
    for (const r of ln.runs) {
      const st = styleOf(ln.kind, r.hl, fs);
      g.font = FONT(st.weight, r.px);
      g.fillStyle = ln.kind === "filler" ? GREY : ln.kind === "last" ? (r.hl ? BLUE : GREY) : INK;
      drawRich(g, r.s, bx + pad + r.x, base, r.px);
    }
  }
  g.restore();
  // Ползунок: крошечный — «простыня» длиннее, чем видно.
  const track = viewH - 16 * s;
  const thumb = Math.max(14 * s, track * Number(p.thumb ?? 0.05));
  const max = Math.max(1, wall.height - viewH * 0.9);
  const ty = areaTop + 8 * s + (track - thumb) * clamp01(scroll / max);
  g.fillStyle = "rgba(60,64,80,0.12)";
  rr(g, W - 20 * s, areaTop + 8 * s, 8 * s, track, 4 * s);
  g.fill();
  g.fillStyle = "rgba(60,64,80,0.55)";
  rr(g, W - 20 * s, ty, 8 * s, thumb, 4 * s);
  g.fill();
  // Плашка страниц.
  const pg = stepOf(p.pages, t);
  if (pg) {
    const pk = clamp01((t - pg.t) / 0.3);
    const big = 1 + 0.25 * (1 - pk) * Math.sin(pk * Math.PI);
    const px = 46 * s * big;
    g.font = FONT(900, px);
    const tw = g.measureText(String(pg.text)).width + px * 1.1;
    const cx = W - 26 * s - tw / 2, cy = headH / 2;
    g.fillStyle = "#2b1810";
    rr(g, cx - tw / 2, cy - px * 0.78, tw, px * 1.5, px * 0.5);
    g.fill();
    g.fillStyle = "#fff6e8";
    g.textAlign = "center";
    g.fillText(String(pg.text), cx, cy + px * 0.33);
    g.textAlign = "left";
  }
  // Последняя строка — крупно в центр.
  const z = sample(p.zoom, t, "v", 0);
  if (z > 0.001) {
    const li = wall.lines.findIndex((l) => l.kind === "last");
    const ln = li >= 0 ? wall.lines[li] : null;
    g.fillStyle = `rgba(22,16,24,${0.62 * z})`;
    g.fillRect(0, 0, W, H);
    if (ln) {
      const text = (p.paras || []).find((pp: Para) => pp.kind === "last")?.text || "";
      const words = parseMarks(text);
      const fromPx = ln.runs[0]?.px ?? fs * 0.7;
      const toPx = fs * 1.55;
      const px = lerp(fromPx, toPx, z);
      const maxW = lerp(bw - pad * 2, W * 0.84, z);
      // Перенос крупного текста в колонку maxW.
      const rows: Word[][] = [[]];
      let x = 0;
      g.font = FONT(900, px);
      for (const w of words) {
        const ww = measureRich(g, w.s, px), sp = w.glue ? 0 : g.measureText(" ").width;
        if (rows[rows.length - 1].length && !w.glue && x + sp + ww > maxW) {
          rows.push([]);
          x = 0;
        }
        x += (rows[rows.length - 1].length ? sp : 0) + ww;
        rows[rows.length - 1].push(w);
      }
      const lh = px * 1.25;
      const fromY = y0 + ln.base, toY = H * 0.5 - ((rows.length - 1) * lh) / 2 + px * 0.35;
      const cy = lerp(fromY, toY, z);
      const cardW = Math.max(...rows.map((r) => r.reduce((a, w, i) => a + measureRich(g, w.s, px) + (i ? g.measureText(" ").width : 0), 0)));
      g.fillStyle = "#ffffff";
      rr(g, W / 2 - cardW / 2 - px * 0.6, cy - px * 1.15, cardW + px * 1.2, rows.length * lh + px * 0.75, px * 0.4);
      g.fill();
      rows.forEach((row, ri) => {
        const rw = row.reduce((a, w, i) => a + measureRich(g, w.s, px) + (i ? g.measureText(" ").width : 0), 0);
        let cx = W / 2 - rw / 2;
        for (const w of row) {
          g.fillStyle = w.hl ? BLUE : INK;
          drawRich(g, w.s, cx, cy + ri * lh, px);
          cx += measureRich(g, w.s, px) + g.measureText(" ").width;
        }
      });
    }
  }
};

/* ---------------------------------------------------------------- кнопка на сайте */

function mix(a: string, b: string, k: number): string {
  const pa = [1, 3, 5].map((i) => parseInt(a.slice(i, i + 2), 16));
  const pb = [1, 3, 5].map((i) => parseInt(b.slice(i, i + 2), 16));
  return `rgb(${pa.map((v, i) => Math.round(lerp(v, pb[i], clamp01(k)))).join(",")})`;
}

const buttonSite: Painter = (g, W, H, t, p) => {
  const s = W / 720;
  g.fillStyle = "#fbf7f1";
  g.fillRect(0, 0, W, H);
  // Адресная строка.
  g.fillStyle = "#e6e0d8";
  g.fillRect(0, 0, W, 96 * s);
  g.fillStyle = "#ffffff";
  rr(g, 120 * s, 24 * s, W - 150 * s, 48 * s, 24 * s);
  g.fill();
  for (const [i, c] of ["#ff6b5e", "#ffc23d", "#3fc454"].entries()) {
    g.fillStyle = c;
    g.beginPath();
    g.arc((30 + i * 30) * s, 48 * s, 10 * s, 0, Math.PI * 2);
    g.fill();
  }
  g.fillStyle = "#8a90a0";
  g.font = FONT(700, 26 * s);
  g.fillText(String(p.url || "мой-стартап.рф"), 146 * s, 57 * s);
  // Заголовок и «текст» полосками.
  g.fillStyle = INK;
  g.font = FONT(900, 62 * s);
  g.textAlign = "center";
  g.fillText(String(p.title || "Мой стартап"), W / 2, 230 * s);
  g.textAlign = "left";
  g.fillStyle = "#d9d3ca";
  for (let i = 0; i < 3; i++) {
    const w = (i === 2 ? 0.42 : 0.7) * W;
    rr(g, (W - w) / 2, (290 + i * 44) * s, w, 20 * s, 10 * s);
    g.fill();
  }
  // Серия 10: кнопки нет — пунктирная пустота на её месте (meme_painters_night.ts).
  if (p.empty) {
    drawEmptySpot(g, W, H, t, p);
    return;
  }
  // Кнопка.
  const at = Number(p.at ?? 1e9);
  const k = clamp01((t - at) / 0.22);
  const bw = W * 0.66, bh = 150 * s;
  const bx = (W - bw) / 2, by = H * 0.56;
  const press = t >= at && t < at + 0.16 ? 0.94 : 1;
  g.save();
  g.translate(W / 2, by + bh / 2);
  g.scale(press, press);
  g.translate(-W / 2, -(by + bh / 2));
  g.fillStyle = "rgba(43,24,16,0.25)";
  rr(g, bx + 8 * s, by + 14 * s, bw, bh, bh * 0.3);
  g.fill();
  g.fillStyle = mix(p.from || "#9aa1b0", p.to || BLUE, k);
  rr(g, bx, by, bw, bh, bh * 0.3);
  g.fill();
  g.fillStyle = "#ffffff";
  g.font = FONT(900, 64 * s);
  g.textAlign = "center";
  g.fillText(String(p.label || "КУПИТЬ"), W / 2, by + bh / 2 + 23 * s);
  g.textAlign = "left";
  g.restore();
  // Волна от клика.
  if (t >= at && t < at + 0.6) {
    const r = (t - at) / 0.6;
    g.strokeStyle = `rgba(47,111,181,${0.7 * (1 - r)})`;
    g.lineWidth = 10 * s * (1 - r) + 2 * s;
    g.beginPath();
    g.arc(W / 2, by + bh / 2, bw * 0.3 + bw * 0.45 * r, 0, Math.PI * 2);
    g.stroke();
  }
  // Курсор-стрелка у кнопки.
  const cx = W / 2 + bw * 0.2, cy = by + bh * 0.62 + (t < at ? 40 * s * clamp01((at - t) / 0.4) : 0);
  g.fillStyle = "#ffffff";
  g.strokeStyle = INK;
  g.lineWidth = 4 * s;
  g.beginPath();
  g.moveTo(cx, cy);
  g.lineTo(cx, cy + 70 * s);
  g.lineTo(cx + 18 * s, cy + 54 * s);
  g.lineTo(cx + 32 * s, cy + 84 * s);
  g.lineTo(cx + 44 * s, cy + 78 * s);
  g.lineTo(cx + 30 * s, cy + 50 * s);
  g.lineTo(cx + 52 * s, cy + 48 * s);
  g.closePath();
  g.fill();
  g.stroke();
  // Плашка «готово».
  if (p.toast && t >= at + 0.25) {
    const tk = outBack(clamp01((t - at - 0.25) / 0.3));
    const px = 40 * s;
    g.font = FONT(800, px);
    const tw = measureRich(g, String(p.toast), px) + px * 1.2;
    g.save();
    g.translate(W / 2, by + bh + 120 * s);
    g.scale(tk, tk);
    g.fillStyle = "#2e9d5b";
    rr(g, -tw / 2, -px, tw, px * 1.8, px * 0.6);
    g.fill();
    g.fillStyle = "#ffffff";
    drawRich(g, String(p.toast), -tw / 2 + px * 0.6, px * 0.36, px);
    g.restore();
  }
};

// Серия 10 «Сделай за ночь»: дерево файлов (meme_painters_night.ts) — через этот же реестр экранов окна и ноутбука.
export const PROMPT_PAINTERS: Record<string, Painter> = { prompt_wall: promptWall, button_site: buttonSite, ...NIGHT_PAINTERS,
  ...DESIGN_PAINTERS };
