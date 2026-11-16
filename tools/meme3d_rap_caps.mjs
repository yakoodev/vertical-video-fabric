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
  const rise = (1 - easeOutBack(a)) * size * 0.9;
  const track = (1 - easeOut(a)) * size * 0.18 + size * 0.01;
  const text = w.accent ? w.text.toLowerCase() : w.text;
  g.save();
  // Маска: слово «поднимается из-под строки».
  g.beginPath();
  g.rect(x - size, y - size * 1.25, wordWidth(w, size, track) + size * 2, size * 1.5);
  g.clip();
  g.font = wordFont(w, size);
  const fill = w.accent ? color : "#ffffff";
  const ghosts = a < 1 ? 3 : 0;
  for (let k = ghosts; k >= 0; k--) {
    g.globalAlpha = k === 0 ? 1 : 0.18 / k;
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

function drawChunk(c, t) {
  if (t < c.t0 || t > c.t1) return;
  const color = accentAt(c.words[0].t0);
  const size = SIZE * (c.scale || 1);
  const cy = plan.y * H;
  const fadeOut = clamp((c.t1 - t) / 0.16);
  const exitLift = (1 - fadeOut) * size * 0.35;
  const pulse = 1 + 0.035 * kickPulse(t);
  // Раскладка: строки до 84% ширины, слова — с итоговой (сжатой) разрядкой.
  const ws = c.words.map((w) => ({ w, width: wordWidth(w, size, size * 0.01) }));
  const lines = [[]];
  let cur = 0;
  for (const it of ws) {
    const add = it.width + (lines[lines.length - 1].length ? size * GAP : 0);
    if (cur + add > W * 0.84 && lines[lines.length - 1].length) {
      lines.push([]);
      cur = 0;
    }
    lines[lines.length - 1].push(it);
    cur += add;
  }
  const lh = size * 1.08;
  g.save();
  g.globalAlpha = fadeOut;
  g.translate(W / 2, cy - exitLift);
  g.scale(pulse, pulse);
  g.textBaseline = "alphabetic";
  let maxW = 0;
  lines.forEach((ln, li) => {
    const lw = ln.reduce((s, it) => s + it.width, 0) + (ln.length - 1) * size * GAP;
    maxW = Math.max(maxW, lw);
    let x = -lw / 2;
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
  const y = (plan.noteY || plan.y + 0.09) * H;
  const full = g.measureText(text).width;
  const x0 = W / 2 - full / 2;
  g.fillText(s, x0, y);
  // Курсор мигает, пока печатается и немного после.
  if (age < text.length / 26 + 0.6 && Math.floor(t * 4) % 2 === 0) g.fillRect(x0 + g.measureText(s).width + 4, y - 15, 16, 30);
  g.restore();
}

function render(frame) {
  const t = plan.t0 + frame / FPS;
  g.clearRect(0, 0, W, H);
  for (const c of plan.chunks) drawChunk(c, t);
  for (const n of plan.notes || []) drawNote(n, t);
}

const args = process.argv.slice(3);
if (args[0] === "--png") {
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
