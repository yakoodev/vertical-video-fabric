#!/usr/bin/env node
/**
 * Подписи рэп-клипа «Уроню БД» — «иронично-крутая» типографика поверх видео (кадры RGBA в stdout → ffmpeg overlay).
 *
 *   node tools/meme3d_rap_caps.mjs plan.json > frames.rgba      (план — tools/meme3d_rap_caps.py)
 *   node tools/meme3d_rap_caps.mjs plan.json --png 3.2 out.png  (один кадр для проверки)
 *
 * Четыре вида (по смыслу строки, не случайно): chrome — хром Y2K с искрами и бликом (основной), gold — «люксовый»
 * золотой с засечками (деньги), wordart — радуга с объёмом в духе 90-х (панчлайны), glitch — RGB-сдвиг и срезы
 * (rm -rf, «уроню БД», бэкап). Фраза 2–4 слова падает с подскоком (squash & stretch), на ударе бочки пружинит,
 * звучащее слово крупнее. Значки к словам (БД, жетоны, котик), наклейки-«эдлибы» с ошибками, штамп PARENTAL ADVISORY.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { createCanvas, GlobalFonts } from "@napi-rs/canvas";

const plan = JSON.parse(readFileSync(process.argv[2], "utf-8"));
const W = plan.width, H = plan.height, FPS = plan.fps;
const F = (p) => GlobalFonts.registerFromPath(p.file, p.family);
for (const f of plan.fonts || []) F(f);
const canvas = createCanvas(W, H);
const g = canvas.getContext("2d");

const clamp = (x, a = 0, b = 1) => Math.max(a, Math.min(b, x));
const easeOutBack = (x) => { const c1 = 1.70158, c3 = c1 + 1; return 1 + c3 * Math.pow(x - 1, 3) + c1 * Math.pow(x - 1, 2); };
let seed = 1;
const rnd = (s) => { const x = Math.sin(s * 12.9898) * 43758.5453; return x - Math.floor(x); };

const LOOK = {
  chrome: { font: (s) => `900 ${s}px "Montserrat Black"`, size: 92 },
  gold: { font: (s) => `italic 700 ${s}px "Georgia"`, size: 100 },
  wordart: { font: (s) => `${s}px "Impact"`, size: 108 },
  glitch: { font: (s) => `900 ${s}px "Montserrat Black"`, size: 92 },
};

function chromeFill(y0, h) {
  const gr = g.createLinearGradient(0, y0, 0, y0 + h);
  gr.addColorStop(0, "#ffffff");
  gr.addColorStop(0.42, "#cfd8e6");
  gr.addColorStop(0.5, "#56627c");
  gr.addColorStop(0.58, "#9fb0cc");
  gr.addColorStop(0.78, "#f2f7ff");
  gr.addColorStop(1, "#8fa2c2");
  return gr;
}
function goldFill(y0, h) {
  const gr = g.createLinearGradient(0, y0, 0, y0 + h);
  gr.addColorStop(0, "#fff6c2");
  gr.addColorStop(0.35, "#f6c445");
  gr.addColorStop(0.55, "#a8740c");
  gr.addColorStop(0.75, "#ffe27a");
  gr.addColorStop(1, "#c8901c");
  return gr;
}
function rainbowFill(x0, w) {
  const gr = g.createLinearGradient(x0, 0, x0 + w, 0);
  ["#ff3b3b", "#ff9f1c", "#ffe81f", "#3ddc5a", "#2ec8ff", "#8a5cff", "#ff4fd8"].forEach((c, i, a) => gr.addColorStop(i / (a.length - 1), c));
  return gr;
}

/** Четырёхлучевая искра. */
function sparkle(x, y, r, a) {
  if (a <= 0) return;
  g.save();
  g.globalAlpha = a;
  g.fillStyle = "#ffffff";
  g.shadowColor = "#bfefff";
  g.shadowBlur = r * 1.5;
  g.beginPath();
  g.moveTo(x, y - r);
  g.quadraticCurveTo(x, y, x + r, y);
  g.quadraticCurveTo(x, y, x, y + r);
  g.quadraticCurveTo(x, y, x - r, y);
  g.quadraticCurveTo(x, y, x, y - r);
  g.fill();
  g.restore();
}

/** Значки к словам: БД (цилиндры + вспышка), монета, котик-стикер, огонь. */
function icon(kind, x, y, s, t) {
  g.save();
  g.translate(x, y);
  if (kind === "db") {
    const cols = ["#3cc6ff", "#ff5fa2", "#ffd23f"];
    for (let i = 2; i >= 0; i--) {
      g.fillStyle = cols[i];
      g.strokeStyle = "#140a14";
      g.lineWidth = s * 0.07;
      g.beginPath();
      g.ellipse(0, (i - 1) * s * 0.3, s * 0.42, s * 0.16, 0, 0, Math.PI * 2);
      g.rect(-s * 0.42, (i - 1) * s * 0.3 - s * 0.02, s * 0.84, s * 0.2);
      g.fill();
      g.stroke();
    }
    // 💥: зубчатая вспышка сзади-сбоку.
    g.translate(s * 0.45, -s * 0.35);
    g.rotate(t * 2);
    g.fillStyle = "#ffe14d";
    g.strokeStyle = "#ff3b3b";
    g.lineWidth = s * 0.06;
    g.beginPath();
    for (let i = 0; i < 16; i++) {
      const r = (i % 2 ? 0.18 : 0.38) * s, a = (i / 16) * Math.PI * 2;
      g.lineTo(Math.cos(a) * r, Math.sin(a) * r);
    }
    g.closePath();
    g.fill();
    g.stroke();
  } else if (kind === "coin") {
    for (let i = 0; i < 3; i++) {
      const yy = -i * s * 0.18 + Math.sin(t * 8 + i) * s * 0.03;
      g.fillStyle = "#b8860b";
      g.beginPath();
      g.ellipse(i * s * 0.12, yy + s * 0.05, s * 0.32, s * 0.12, 0, 0, Math.PI * 2);
      g.fill();
      g.fillStyle = goldFill(yy - s * 0.12, s * 0.24);
      g.strokeStyle = "#3a2600";
      g.lineWidth = s * 0.05;
      g.beginPath();
      g.ellipse(i * s * 0.12, yy, s * 0.32, s * 0.12, 0, 0, Math.PI * 2);
      g.fill();
      g.stroke();
    }
  } else if (kind === "cat") {
    // Аниме-котик-наклейка: белая обводка, ушки, глаза-блики, румянец.
    g.rotate(-0.15);
    const r = s * 0.42;
    for (const [fill, k] of [["#ffffff", 1.18], ["#ffb6d9", 1]]) {
      g.fillStyle = fill;
      g.beginPath();
      g.arc(0, 0, r * k, 0, Math.PI * 2);
      g.moveTo(-r * 0.9 * k, -r * 0.3);
      g.lineTo(-r * 0.75 * k, -r * 1.35 * k);
      g.lineTo(-r * 0.15, -r * 0.85 * k);
      g.moveTo(r * 0.9 * k, -r * 0.3);
      g.lineTo(r * 0.75 * k, -r * 1.35 * k);
      g.lineTo(r * 0.15, -r * 0.85 * k);
      g.fill();
    }
    g.fillStyle = "#2a1020";
    for (const sx of [-1, 1]) {
      g.beginPath();
      g.ellipse(sx * r * 0.38, -r * 0.05, r * 0.16, r * 0.24, 0, 0, Math.PI * 2);
      g.fill();
      g.fillStyle = "#ffffff";
      g.beginPath();
      g.arc(sx * r * 0.38 - r * 0.05, -r * 0.13, r * 0.06, 0, Math.PI * 2);
      g.fill();
      g.fillStyle = "#2a1020";
    }
    g.fillStyle = "#ff5fa2";
    g.globalAlpha = 0.6;
    for (const sx of [-1, 1]) {
      g.beginPath();
      g.ellipse(sx * r * 0.6, r * 0.3, r * 0.14, r * 0.08, 0, 0, Math.PI * 2);
      g.fill();
    }
    g.globalAlpha = 1;
    g.strokeStyle = "#2a1020";
    g.lineWidth = r * 0.07;
    g.beginPath();
    g.moveTo(-r * 0.15, r * 0.3);
    g.quadraticCurveTo(0, r * 0.42, 0, r * 0.28);
    g.quadraticCurveTo(0, r * 0.42, r * 0.15, r * 0.3);
    g.stroke();
  } else if (kind === "fire") {
    const fl = 1 + 0.1 * Math.sin(t * 20);
    for (const [c, k] of [["#ff3d12", 1], ["#ff9a1f", 0.7], ["#fff1a6", 0.4]]) {
      g.fillStyle = c;
      g.beginPath();
      g.moveTo(0, -s * 0.55 * k * fl);
      g.quadraticCurveTo(s * 0.4 * k, -s * 0.05, 0, s * 0.35 * k);
      g.quadraticCurveTo(-s * 0.4 * k, -s * 0.05, 0, -s * 0.55 * k * fl);
      g.fill();
    }
  }
  g.restore();
}

function measureWords(words, look, size) {
  g.font = LOOK[look].font(size);
  const sp = size * 0.5;
  // Ширина слова — с местом под его значок (значок стоит справа от слова).
  return words.map((w) => g.measureText(w.text).width + (w.icon ? size * 0.95 : 0)).map((w, i, a) => ({ w, sp: i < a.length - 1 ? sp : 0 }));
}

/** Строки фразы: переносы по ширине (до maxW). */
function layout(words, look, size, maxW) {
  const ms = measureWords(words, look, size);
  const lines = [[]];
  let cur = 0;
  ms.forEach((m, i) => {
    const add = m.w + (lines[lines.length - 1].length ? size * 0.5 : 0);
    if (cur + add > maxW && lines[lines.length - 1].length) {
      lines.push([]);
      cur = 0;
    }
    lines[lines.length - 1].push({ i, w: m.w });
    cur += m.w + (lines[lines.length - 1].length > 1 ? size * 0.5 : 0);
  });
  return lines;
}

function drawText(look, text, x, y, size, t, frame, active) {
  g.font = LOOK[look].font(size);
  g.textBaseline = "alphabetic";
  const w = g.measureText(text).width;
  const top = y - size * 0.82, h = size;
  g.lineJoin = "round";
  if (look === "chrome") {
    g.lineWidth = size * 0.2;
    g.strokeStyle = "#0b0d18";
    g.strokeText(text, x, y);
    g.lineWidth = size * 0.06;
    g.strokeStyle = active ? "#7ff6ff" : "#cfe3ff";
    g.strokeText(text, x, y);
    g.fillStyle = chromeFill(top, h);
    g.fillText(text, x, y);
  } else if (look === "gold") {
    g.save();
    g.fillStyle = "rgba(0,0,0,0.55)";
    g.fillText(text, x + size * 0.06, y + size * 0.08);
    g.restore();
    g.lineWidth = size * 0.14;
    g.strokeStyle = "#2a1a00";
    g.strokeText(text, x, y);
    g.fillStyle = goldFill(top, h);
    g.fillText(text, x, y);
  } else if (look === "wordart") {
    // Объём: тёмные копии со сдвигом вниз-вправо, сверху — радуга с обводкой.
    const depth = Math.round(size * 0.12);
    g.fillStyle = "#3a0a5a";
    for (let k = depth; k > 0; k -= 2) g.fillText(text, x + k * 0.8, y + k);
    g.lineWidth = size * 0.1;
    g.strokeStyle = "#ffffff";
    g.strokeText(text, x, y);
    g.fillStyle = rainbowFill(x, w);
    g.fillText(text, x, y);
  } else if (look === "glitch") {
    const j = (k) => (rnd(frame * 7.13 + k) - 0.5) * size * 0.12;
    g.lineWidth = size * 0.16;
    g.strokeStyle = "#0b0d18";
    g.strokeText(text, x, y);
    g.globalCompositeOperation = "lighter";
    g.fillStyle = "#ff1f4f";
    g.fillText(text, x - size * 0.05 + j(1), y + j(2) * 0.3);
    g.fillStyle = "#18e0ff";
    g.fillText(text, x + size * 0.05 + j(3), y + j(4) * 0.3);
    g.globalCompositeOperation = "source-over";
    g.fillStyle = "#ffffff";
    g.fillText(text, x, y);
  }
  return w;
}

/** Срезы-сдвиги поверх глитч-фразы (в пределах её рамки). */
function glitchSlices(x0, y0, w, h, frame) {
  if (rnd(frame * 1.7) < 0.45) return;
  const img = g.getImageData(Math.max(0, x0), Math.max(0, y0), Math.min(W, w), Math.min(H - y0, h));
  for (let k = 0; k < 3; k++) {
    const sy = Math.floor(rnd(frame + k * 9) * h), sh = Math.floor(6 + rnd(frame + k * 5) * 18), dx = Math.round((rnd(frame * 3 + k) - 0.5) * 40);
    const band = g.getImageData(Math.max(0, x0), Math.max(0, y0) + sy, Math.min(W, w), Math.min(sh, h - sy));
    g.putImageData(band, Math.max(0, x0) + dx, Math.max(0, y0) + sy);
  }
  void img;
}

function sticker(st, t) {
  const age = t - st.t0, left = st.t1 - t;
  const k = clamp(age / 0.18) * clamp(left / 0.15);
  if (k <= 0) return;
  g.save();
  g.translate(st.x * W, st.y * H);
  g.rotate((st.rot || 0) * Math.PI / 180 + Math.sin(t * 9) * 0.03);
  const sc = easeOutBack(clamp(age / 0.22)) * (1 + 0.04 * Math.sin(t * 12));
  g.scale(sc, sc);
  g.globalAlpha = k;
  const size = st.size || 54;
  g.font = `900 ${size}px "Comic Sans MS"`;
  const w = g.measureText(st.text).width + size * 0.8, h = size * 1.35;
  g.fillStyle = "#ffffff";
  g.beginPath();
  g.roundRect(-w / 2 - 8, -h / 2 - 8, w + 16, h + 16, h * 0.4);
  g.fill();
  g.fillStyle = st.bg || "#ff3fa4";
  g.beginPath();
  g.roundRect(-w / 2, -h / 2, w, h, h * 0.35);
  g.fill();
  g.fillStyle = st.fg || "#ffffff";
  g.textAlign = "center";
  g.textBaseline = "middle";
  g.lineWidth = size * 0.12;
  g.strokeStyle = "#1a0a1a";
  g.strokeText(st.text, 0, h * 0.04);
  g.fillText(st.text, 0, h * 0.04);
  g.restore();
}

function stamp(sp, t) {
  const age = t - sp.t0, left = sp.t1 - t;
  if (age < 0 || left < 0) return;
  // «Шлёп»: из крупного в размер с тряской, потом лежит.
  const k = age < 0.12 ? 1.6 - (age / 0.12) * 0.6 : 1;
  g.save();
  g.translate(sp.x * W, sp.y * H);
  g.rotate(-0.08);
  g.scale(k, k);
  g.globalAlpha = clamp(left / 0.2);
  const w = 300, h = 160;
  g.fillStyle = "#ffffff";
  g.fillRect(-w / 2, -h / 2, w, h);
  g.fillStyle = "#000000";
  g.fillRect(-w / 2 + 8, -h / 2 + 8, w - 16, h - 16);
  g.fillStyle = "#ffffff";
  g.fillRect(-w / 2 + 8, -h / 2 + 50, w - 16, 62);
  g.textAlign = "center";
  g.textBaseline = "middle";
  g.fillStyle = "#ffffff";
  g.font = '900 26px "Montserrat Black"';
  g.fillText("PARENTAL ADVISORY", 0, -h / 2 + 30);
  g.fillStyle = "#000000";
  g.font = '900 52px "Montserrat Black"';
  g.fillText("EXPLICIT", 0, -h / 2 + 82);
  g.fillStyle = "#ffffff";
  g.font = '900 26px "Montserrat Black"';
  g.fillText("CONTENT", 0, h / 2 - 26);
  g.restore();
}

function kickPulse(t) {
  let v = 0;
  for (const k of plan.kicks) {
    const d = t - k;
    if (d >= 0 && d < 0.2) v = Math.max(v, 1 - d / 0.2);
  }
  return v;
}

function drawChunk(c, t, frame) {
  const age = t - c.t0;
  if (age < 0 || t > c.t1) return;
  const look = c.look;
  const size = (LOOK[look].size || 92) * (c.scale || 1);
  const lines = layout(c.words, look, size, W * 0.86);
  const lh = size * 1.12;
  const cy = plan.y * H;
  // Появление: падение сверху с перелётом, squash & stretch; уход — быстрое затухание.
  const a = clamp(age / 0.16);
  const drop = (1 - easeOutBack(a)) * -70;
  const sq = age < 0.08 ? 1 + 0.25 * (1 - age / 0.08) : age < 0.2 ? 1 - 0.12 * Math.sin(((age - 0.08) / 0.12) * Math.PI) : 1;
  const pulse = 1 + 0.06 * kickPulse(t);
  const fade = clamp((c.t1 - t) / 0.08);
  g.save();
  g.globalAlpha = fade;
  g.translate(W / 2, cy + drop);
  g.scale(pulse / Math.sqrt(sq), pulse * sq);
  const totalH = lines.length * lh;
  let bx0 = W, bx1 = 0;
  lines.forEach((ln, li) => {
    const widths = ln.map((x) => x.w);
    const lw = widths.reduce((s, w) => s + w, 0) + (ln.length - 1) * size * 0.5;
    let x = -lw / 2;
    const y = -totalH / 2 + (li + 0.78) * lh;
    ln.forEach(({ i, w }) => {
      const wd = c.words[i];
      const active = t >= wd.t0 && t < wd.t1 + 0.05;
      const pop = active ? 1.07 : 1;
      const wave = look === "wordart" ? Math.sin(i * 0.9 + t * 7) * size * 0.05 : 0;
      const iw = wd.icon ? size * 0.95 : 0;
      const tw = w - iw;
      g.save();
      g.translate(x + tw / 2, y + wave);
      g.scale(pop, pop);
      g.textAlign = "center";
      drawText(look, wd.text, 0, 0, size, t, frame + i, active);
      g.restore();
      if (wd.icon) icon(wd.icon, x + tw + size * 0.55, y - size * 0.32, size * 0.85, t);
      x += w + size * 0.5;
    });
    bx0 = Math.min(bx0, W / 2 - lw / 2);
    bx1 = Math.max(bx1, W / 2 + lw / 2);
  });
  g.restore();
  if (look === "glitch") glitchSlices(Math.floor(bx0 - 20), Math.floor(cy - totalH / 2 - 10), Math.ceil(bx1 - bx0 + 40), Math.ceil(totalH + 20), frame);
  if (look === "chrome" || look === "gold") {
    // Искры вокруг фразы (мерцают по своему ритму) и блик-полоса на появлении.
    for (let k = 0; k < 4; k++) {
      const sx = bx0 + rnd(c.t0 * 13 + k) * (bx1 - bx0), sy = cy - totalH / 2 + rnd(c.t0 * 7 + k * 3) * totalH;
      sparkle(sx, sy, 14 + 10 * rnd(k + c.t0), Math.max(0, Math.sin(t * 6 + k * 1.9)) * fade);
    }
    if (age < 0.35) {
      const fx = bx0 + (bx1 - bx0) * (age / 0.35);
      const gr = g.createLinearGradient(fx - 140, 0, fx + 140, 0);
      gr.addColorStop(0, "rgba(255,255,255,0)");
      gr.addColorStop(0.5, "rgba(255,255,255,0.75)");
      gr.addColorStop(1, "rgba(255,255,255,0)");
      g.save();
      g.globalCompositeOperation = "lighter";
      g.fillStyle = gr;
      g.fillRect(fx - 140, cy - 6, 280, 12);
      g.restore();
      sparkle(fx, cy, 34, 1 - age / 0.35);
    }
  }
}

function render(frame) {
  const t = plan.t0 + frame / FPS;
  g.clearRect(0, 0, W, H);
  for (const c of plan.chunks) drawChunk(c, t, frame);
  for (const st of plan.stickers) if (t >= st.t0 && t <= st.t1) sticker(st, t);
  for (const sp of plan.stamps) stamp(sp, t);
}

const args = process.argv.slice(3);
if (args[0] === "--png") {
  const t = Number(args[1]);
  render(Math.round((t - plan.t0) * FPS));
  writeFileSync(args[2], canvas.toBuffer("image/png"));
} else {
  const n = Math.round(plan.dur * FPS);
  for (let f = 0; f < n; f++) {
    render(f);
    const data = g.getImageData(0, 0, W, H).data;
    if (!process.stdout.write(Buffer.from(data.buffer, data.byteOffset, data.byteLength))) await new Promise((r) => process.stdout.once("drain", r));
  }
}
