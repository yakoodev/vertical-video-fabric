#!/usr/bin/env node
/**
 * Подписи рэп-клипа «Уроню БД» — «дорогая» кинетическая типографика (кадры RGBA в stdout → ffmpeg overlay).
 *
 *   node tools/meme3d_rap_caps.mjs plan.json > frames.rgba      (план — tools/meme3d_rap_caps.py)
 *   node tools/meme3d_rap_caps.mjs plan.json --png 3.2 out.png  (один кадр для проверки)
 *
 * Два шрифта: узкий гротеск (Oswald, капс) — строка песни; утончённый курсив с засечками (Cambria Italic, строчные,
 * цветом сцены) — ключевое слово строки. Каждое слово выезжает из-под маски в момент, когда его поют (с размытием
 * движения), разрядка букв сжимается, на ударе бочки — лёгкий толчок масштаба, тонкая линия под фразой растёт.
 * Сноски-«лог» — мелкий моноширинный текст под фразой, печатается по буквам, сухо. Никаких наклеек и блёсток.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { createCanvas, GlobalFonts } from "@napi-rs/canvas";

const plan = JSON.parse(readFileSync(process.argv[2], "utf-8"));
const W = plan.width, H = plan.height, FPS = plan.fps;
for (const f of plan.fonts || []) GlobalFonts.registerFromPath(f.file, f.family);
const canvas = createCanvas(W, H);
const g = canvas.getContext("2d");

const clamp = (x, a = 0, b = 1) => Math.max(a, Math.min(b, x));
const easeOut = (x) => 1 - Math.pow(1 - clamp(x), 3);
const easeOutBack = (x) => { const c1 = 1.4, c3 = c1 + 1; x = clamp(x); return 1 + c3 * Math.pow(x - 1, 3) + c1 * Math.pow(x - 1, 2); };

const SANS = (s) => `700 ${s}px "Oswald"`;
const SERIF = (s) => `italic 400 ${s}px "Cambria"`;
const MONO = (s) => `400 ${s}px "Consolas"`;
const SIZE = 104;
const GAP = 0.26;
const TRACK_MAX = 0.19; // разрядка в начале выезда слова (доля кегля на букву) — по ней и верстаем, чтобы не вылезать
// Безопасная зона кадра 9:16: вся надпись (и во время анимации) — внутри; правый край — с запасом под кнопки площадок.
const SAFE = { x0: 0.06, x1: 0.9, y0: 0.035, y1: 0.9 };
const LINE_MAX = (SAFE.x1 - SAFE.x0) * 0.97; // доля ширины кадра на строку

function accentAt(t) {
  for (const [a, b, c] of plan.palette) if (t >= a && t < b) return c;
  return "#ffffff";
}

function kickPulse(t) {
  let v = 0;
  for (const k of plan.kicks) {
    const d = t - k;
    if (d >= 0 && d < 0.18) v = Math.max(v, 1 - d / 0.18);
  }
  return v;
}

function wordFont(w, size) {
  return w.accent ? SERIF(size * 1.12) : SANS(size);
}

function wordWidth(w, size, track) {
  g.font = wordFont(w, size);
  const text = w.accent ? w.text.toLowerCase() : w.text;
  return g.measureText(text).width + (w.accent ? 0 : track * (text.length - 1));
}

/** Слово с разрядкой букв (track, px) — посимвольно, от левого края x. */
function drawTracked(text, x, y, track) {
  let cx = x;
  for (const ch of text) {
    g.fillText(ch, cx, y);
    cx += g.measureText(ch).width + track;
  }
}

function drawWord(w, x, y, size, t, color) {
  // Выезд из-под маски: снизу вверх за 0.2 с, размытие движения — полупрозрачные копии по ходу.
  const a = clamp((t - w.t0 + 0.06) / 0.2);
  if (a <= 0) return;
  // Выезд снизу с проявлением (без жёсткой маски: на стоп-кадре слово не выглядит обрезанным).
  const rise = (1 - easeOutBack(a)) * size * 0.5;
  const track = (1 - easeOut(a)) * size * (TRACK_MAX - 0.01) + size * 0.01;
  const text = w.accent ? w.text.toLowerCase() : w.text;
  const fadeIn = easeOut(a * 1.4);
  g.save();
  g.font = wordFont(w, size);
  const fill = w.accent ? color : "#ffffff";
  const ghosts = a < 1 ? 3 : 0;
  const base = g.globalAlpha;
  for (let k = ghosts; k >= 0; k--) {
    g.globalAlpha = base * fadeIn * (k === 0 ? 1 : 0.18 / k);
    const yy = y + rise + k * size * 0.07 * (1 - a);
    g.shadowColor = "rgba(0,0,0,0.55)";
    g.shadowBlur = size * 0.18;
    g.shadowOffsetY = size * 0.04;
    g.fillStyle = fill;
    if (w.accent) g.fillText(text, x, yy);
    else drawTracked(text, x, yy, track);
  }
  g.restore();
}

/** Раскладка фразы по строкам. Ширина слова — с наибольшей разрядкой выезда; не влезает строка (или одно длинное
 * слово) — кегль фразы уменьшается, пока всё не встанет в безопасную зону. */
function layout(c) {
  let size = SIZE * (c.scale || 1);
  for (let pass = 0; pass < 6; pass++) {
    const ws = c.words.map((w) => ({ w, width: wordWidth(w, size, size * 0.01), wide: wordWidth(w, size, size * TRACK_MAX) }));
    const lines = [[]];
    let cur = 0;
    for (const it of ws) {
      const add = it.wide + (lines[lines.length - 1].length ? size * GAP : 0);
      if (cur + add > W * LINE_MAX && lines[lines.length - 1].length) {
        lines.push([]);
        cur = 0;
      }
      lines[lines.length - 1].push(it);
      cur += add;
    }
    const widest = Math.max(...lines.map((ln) => ln.reduce((s, it) => s + it.wide, 0) + (ln.length - 1) * size * GAP)) * 1.035; // + толчок на бит
    if (widest <= W * LINE_MAX) return { size, lines };
    size *= (W * LINE_MAX) / widest;
  }
  return null;
}

function drawChunk(c, t) {
  if (t < c.t0 || t > c.t1) return;
  const color = accentAt(c.words[0].t0);
  const { size, lines } = c._layout || (c._layout = layout(c));
  const lh = size * 1.08;
  // Центр по x — середина безопасной зоны; по y — не выше/ниже её краёв с учётом числа строк.
  const half = ((lines.length - 1) / 2) * lh + size;
  const cy = clamp((c.y ?? plan.y) * H, SAFE.y0 * H + half, SAFE.y1 * H - half);
  const cx = ((SAFE.x0 + SAFE.x1) / 2) * W;
  const fadeOut = clamp((c.t1 - t) / 0.16);
  const exitLift = (1 - fadeOut) * size * 0.35;
  const pulse = 1 + 0.035 * kickPulse(t);
  g.save();
  g.globalAlpha = fadeOut;
  g.translate(cx, cy - exitLift);
  g.scale(pulse, pulse);
  g.textBaseline = "alphabetic";
  let maxW = 0;
  lines.forEach((ln, li) => {
    const lw = ln.reduce((s, it) => s + it.width, 0) + (ln.length - 1) * size * GAP;
    maxW = Math.max(maxW, lw);
    // Последнее слово при выезде шире на разрядку — строку сдвигаем на половину этого запаса (симметрично).
    const last = ln[ln.length - 1];
    let x = -(lw + (last.wide - last.width)) / 2;
    const y = (li - (lines.length - 1) / 2) * lh + size * 0.36;
    for (const it of ln) {
      drawWord(it.w, x, y, size, t, color);
      x += it.width + size * GAP;
    }
  });
  // Тонкая линия под фразой: растёт от центра за 0.4 с, цвет сцены.
  const k = easeOut((t - c.t0) / 0.4);
  const ly = ((lines.length - 1) / 2) * lh + size * 0.62;
  g.globalAlpha = fadeOut * 0.9;
  g.fillStyle = color;
  g.fillRect((-maxW / 2) * k, ly, maxW * k, Math.max(2, size * 0.025));
  g.restore();
}

function drawNote(n, t) {
  if (t < n.t0 || t > n.t1) return;
  const age = t - n.t0;
  const text = "// " + n.text;
  const shown = Math.min(text.length, Math.floor(age * 26) + 1); // печатается по буквам
  const fade = clamp((n.t1 - t) / 0.25);
  const s = text.slice(0, shown);
  g.save();
  g.globalAlpha = fade * 0.88;
  g.font = MONO(38);
  g.textBaseline = "middle";
  g.shadowColor = "rgba(0,0,0,0.75)";
  g.shadowBlur = 8;
  g.fillStyle = "#ece9f4";
  const y = (n.y ?? plan.noteY ?? plan.y + 0.09) * H;
  let px = 38;
  if (g.measureText(text).width > W * LINE_MAX) {
    px = Math.floor((38 * W * LINE_MAX) / g.measureText(text).width);
    g.font = MONO(px);
  }
  const full = g.measureText(text).width;
  const x0 = ((SAFE.x0 + SAFE.x1) / 2) * W - full / 2;
  g.fillText(s, x0, y);
  // Курсор мигает, пока печатается и немного после.
  if (age < text.length / 26 + 0.6 && Math.floor(t * 4) % 2 === 0) g.fillRect(x0 + g.measureText(s).width + 4, y - px * 0.4, px * 0.42, px * 0.8);
  g.restore();
}

function render(frame) {
  const t = plan.t0 + frame / FPS;
  g.clearRect(0, 0, W, H);
  for (const c of plan.chunks) drawChunk(c, t);
  for (const n of plan.notes || []) drawNote(n, t);
}

/** Габарит непрозрачных пикселей кадра (альфа > 110 — без мягкой тени). */
function inkBox() {
  const d = g.getImageData(0, 0, W, H).data;
  let x0 = W, y0 = H, x1 = -1, y1 = -1;
  for (let y = 0; y < H; y++) {
    const row = y * W * 4;
    for (let x = 0; x < W; x++) {
      if (d[row + x * 4 + 3] > 110) {
        if (x < x0) x0 = x;
        if (x > x1) x1 = x;
        if (y < y0) y0 = y;
        if (y > y1) y1 = y;
      }
    }
  }
  return x1 < 0 ? null : [x0, y0, x1, y1];
}

/** Проверка: каждая фраза и сноска — в моменты выезда каждого слова, полного показа и ухода — целиком в безопасной зоне. */
function check() {
  const bad = [];
  const sx0 = SAFE.x0 * W, sx1 = SAFE.x1 * W, sy0 = SAFE.y0 * H, sy1 = SAFE.y1 * H;
  const test = (label, t, draw) => {
    g.clearRect(0, 0, W, H);
    draw(t);
    const b = inkBox();
    if (b && (b[0] < sx0 || b[2] > sx1 || b[1] < sy0 || b[3] > sy1)) bad.push(`${t.toFixed(2)} ${label}: [${b.join(",")}] вне [${[sx0, sy0, sx1, sy1].map(Math.round).join(",")}]`);
  };
  for (const c of plan.chunks) {
    if (!c._layout) c._layout = layout(c);
    if (!c._layout) {
      bad.push(`${c.t0.toFixed(2)} «${c.words.map((w) => w.text).join(" ")}»: не верстается`);
      continue;
    }
    const lab = `«${c.words.map((w) => w.text).join(" ")}»`;
    const ts = [c.t0 + 0.01, ...c.words.flatMap((w) => [w.t0 + 0.0, w.t0 + 0.06, w.t0 + 0.12]), c.t1 - 0.08];
    for (const t of ts) if (t >= c.t0 && t <= c.t1) test(lab, t, (tt) => drawChunk(c, tt));
  }
  for (const n of plan.notes || []) test(`// ${n.text}`, n.t1 - 0.3, (tt) => drawNote(n, tt));
  return bad;
}

const args = process.argv.slice(3);
if (args[0] === "--check") {
  console.log(JSON.stringify(check()));
} else if (args[0] === "--png") {
  render(Math.round((Number(args[1]) - plan.t0) * FPS));
  writeFileSync(args[2], canvas.toBuffer("image/png"));
} else {
  // Кадры — прямо в stdin ffmpeg (аргументы — plan.ffmpeg): запись ждёт «drain», память не растёт (через stdout
  // процесса на Windows обратного давления не было — node набирал 10 ГБ).
  const { spawn } = await import("node:child_process");
  const ff = spawn("ffmpeg", plan.ffmpeg, { stdio: ["pipe", "inherit", "inherit"] });
  const done = new Promise((r) => ff.on("close", r));
  const n = Math.round(plan.dur * FPS);
  for (let f = 0; f < n; f++) {
    render(f);
    const data = g.getImageData(0, 0, W, H).data;
    if (!ff.stdin.write(Buffer.from(data.buffer, data.byteOffset, data.byteLength))) await new Promise((r) => ff.stdin.once("drain", r));
  }
  ff.stdin.end();
  const code = await done;
  process.exit(code ?? 1);
}
