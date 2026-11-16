// Слои «в мире игры» (frontend/src/motion/layers/tracked.ts): слово на поверхности по треку (tracked_text) и след
// броска (trail). `node --test node/motion-tracked.test.mjs` (в `npm run test:motion`). Треки считает сервис —
// tests/test_world_track.py.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";
import { createCanvas } from "@napi-rs/canvas";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
let tmp;
let E;

before(async () => {
  tmp = mkdtempSync(join(tmpdir(), "vvf-tracked-"));
  const out = join(tmp, "engine.mjs");
  await build({ entryPoints: [join(ROOT, "frontend", "src", "motion", "index.ts")], bundle: true, platform: "node", format: "esm", outfile: out, logLevel: "warning" });
  E = await import(pathToFileURL(out).href);
});

after(() => {
  if (tmp) rmSync(tmp, { recursive: true, force: true });
});

const W = 540;
const H = 960;
const env = () => ({ width: W, height: H, refWidth: 1080, refHeight: 1920, getImage: () => null, createCanvas: (a, b) => createCanvas(a, b) });

function render(layers, t, camera) {
  const cv = createCanvas(W, H);
  E.drawFrame(cv.getContext("2d"), { layers, camera, fps: 30 }, t, env());
  return cv;
}

/** Рамка непрозрачных пикселей кадра: [x0, y0, x1, y1] (px) или null. */
function bbox(cv) {
  const d = cv.getContext("2d").getImageData(0, 0, W, H).data;
  let x0 = W, y0 = H, x1 = -1, y1 = -1;
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      if (d[(y * W + x) * 4 + 3] > 40) {
        if (x < x0) x0 = x;
        if (x > x1) x1 = x;
        if (y < y0) y0 = y;
        if (y > y1) y1 = y;
      }
    }
  }
  return x1 < 0 ? null : [x0, y0, x1, y1];
}

// Плоскость едет вправо на 0.2 кадра за секунду (трек 0..1 с, 11 сэмплов).
function surfaceTrack() {
  const t = [], q = [];
  for (let i = 0; i <= 10; i++) {
    const s = i / 10;
    t.push(s);
    const dx = 0.2 * s;
    // Перспектива (правый край дальше): сетка переноса из многих ячеек, как у настоящих поверхностей.
    q.push(0.2 + dx, 0.4, 0.6 + dx, 0.415, 0.6 + dx, 0.485, 0.2 + dx, 0.5);
  }
  return { kind: "surface", t, q, s: "o".repeat(11), aspect: (0.4 * W) / (0.1 * H) };
}

const word = (props = {}, extra = {}) => ({ id: "w", type: "tracked_text", start: 0, end: 1, props: { text: "STAY TOGETHER", track: "trk_x", trackData: surfaceTrack(), ...props }, ...extra });

test("squareToQuad: углы единичного квадрата ложатся в углы четырёхугольника", () => {
  const q = [0.1, 0.2, 0.8, 0.25, 0.7, 0.6, 0.15, 0.55];
  const m = E.squareToQuad(q);
  const at = (u, v) => {
    const w = m[6] * u + m[7] * v + m[8];
    return [(m[0] * u + m[1] * v + m[2]) / w, (m[3] * u + m[4] * v + m[5]) / w];
  };
  [[0, 0], [1, 0], [1, 1], [0, 1]].forEach(([u, v], i) => {
    const [x, y] = at(u, v);
    assert.ok(Math.abs(x - q[i * 2]) < 1e-9 && Math.abs(y - q[i * 2 + 1]) < 1e-9);
  });
});

test("quadAt: между сэмплами — интерполяция, потерянный — невидим, вне трека — нет", () => {
  const tr = surfaceTrack();
  const a = E.quadAt(tr, 0.55);
  assert.ok(Math.abs(a.q[0] - (0.2 + 0.2 * 0.55)) < 1e-9);
  assert.equal(a.vis, 1);
  assert.equal(E.quadAt({ ...tr, s: "ooooollllll" }, 0.8).vis, 0);
  assert.equal(E.quadAt(tr, 1.5), null);
});

test("tracked_text: слово рисуется в четырёхугольнике трека и едет вместе с ним", () => {
  const b0 = bbox(render([word()], 0.1));
  const b1 = bbox(render([word()], 0.9));
  assert.ok(b0 && b1);
  // Вправо на 0.16 кадра (± обводка/объём), по вертикали — та же полоса 0.4..0.5 (с полями под объём).
  const shift = (b1[0] - b0[0]) / W;
  assert.ok(Math.abs(shift - 0.16) < 0.02, `shift ${shift}`);
  assert.ok(b0[1] > 0.36 * H && b0[3] < 0.56 * H, JSON.stringify(b0));
  // Вся строка видна: правый край текста — у правого края четырёхугольника (регрессия: copy в буфере сетки
  // сжимал и обрезал слово в @napi-rs/canvas).
  assert.ok(b0[2] > (0.62 - 0.06) * W, JSON.stringify(b0));
});

test("tracked_text: слово ещё не спето — его нет; потерян трек — слоя нет", () => {
  assert.equal(bbox(render([word({ words: [{ t: 0.5 }, { t: 0.6 }] })], 0.2)), null);
  assert.ok(bbox(render([word({ words: [{ t: 0.1 }, { t: 0.6 }] })], 0.3)));
  const lost = word({ trackData: { ...surfaceTrack(), s: "l".repeat(11) } });
  assert.equal(bbox(render([lost], 0.5)), null);
  assert.equal(bbox(render([word({ trackData: null })], 0.5)), null);
});

test("tracked_text: наезд камеры двигает слово вместе с основой", () => {
  const plain = bbox(render([word()], 0.5));
  const zoomed = bbox(render([word()], 0.5, [{ t: 0, zoom: 2, x: 0.5, y: 0.5 }]));
  assert.ok((zoomed[2] - zoomed[0]) > 1.6 * (plain[2] - plain[0]));
});

// Объект летит слева направо по кадру 0..0.5 с; камера стоит (g — тождественная).
function objectTrack(shiftPerSec = 0) {
  const t = [], p = [], g = [];
  for (let i = 0; i <= 10; i++) {
    const s = i / 20;
    t.push(s);
    p.push(0.1 + 1.4 * s, 0.6 - 0.8 * s + 1.2 * s * s);
    g.push(1, 0, shiftPerSec * s, 0, 1, 0); // кадр → мир: камера ушла вправо на shift·t
  }
  return { kind: "object", t, p, g, s: "d".repeat(11) };
}

const trail = (props = {}) => ({ id: "tr", type: "trail", start: 0, end: 1.2, props: { track: "trk_y", trackData: objectTrack(), width: 10, glow: 0, head: false, fadeTail: false, ...props } });

test("trail: линия растёт за объектом и остаётся после его падения", () => {
  const early = bbox(render([trail()], 0.2));
  const late = bbox(render([trail()], 0.5));
  const after = bbox(render([trail()], 1.0));
  assert.ok(early && late && after);
  assert.ok(early[2] < late[2], `${early} ${late}`);
  assert.ok(Math.abs(early[2] / W - (0.1 + 1.4 * 0.2)) < 0.03);
  assert.deepEqual(after, late);
});

test("trail: anchor world — дуга едет с фоном при повороте камеры, screen — стоит", () => {
  const moving = { ...objectTrack(0.2) };
  const world = bbox(render([trail({ trackData: moving, to: 0.25 })], 0.5));
  const screen = bbox(render([trail({ trackData: moving, to: 0.25, anchor: "screen" })], 0.5));
  // К 0.5 с камера ушла на 0.1 кадра — нарисованная до 0.25 с дуга в мире сдвинулась влево относительно экрана.
  assert.ok(Math.abs((screen[0] - world[0]) / W - 0.1) < 0.03, `${world} ${screen}`);
});

test("tracked_text: перспективная поверхность в полном кадре — слово целиком (регрессия copy в сетке переноса)", () => {
  // Четырёхугольник из сэмпла (STAY TOGETHER на башне, 1080×1920): с `copy` в буфере @napi-rs/canvas сжимал слово
  // и обрезал «R» по вертикали внутри четырёхугольника.
  const q = [0.067, 0.364, 0.592, 0.345, 0.613, 0.548, 0.016, 0.545];
  const tr = { kind: "surface", t: [0, 1], q: [...q, ...q], s: "oo", aspect: 1.607 };
  const cv = createCanvas(1080, 1920);
  const e = { width: 1080, height: 1920, getImage: () => null, createCanvas: (a, b) => createCanvas(a, b) };
  E.drawFrame(cv.getContext("2d"), { layers: [word({ text: "STAY\nTOGETHER", trackData: tr })] }, 0.5, e);
  const d = cv.getContext("2d").getImageData(0, 0, 1080, 1920).data;
  let right = -1;
  for (let y = 600; y < 1100; y++) for (let x = 0; x < 1080; x++) if (d[(y * 1080 + x) * 4 + 3] > 40 && x > right) right = x;
  assert.ok(right > 0.56 * 1080, `правый край слова ${right}`); // с copy — 587 px, без — 621
});

test("tracked_text: зеркальный (перевёрнутый) четырёхугольник не рисуется — текст не может выйти зеркальным", () => {
  const tr = surfaceTrack();
  // Левый и правый края поменялись местами (обход против часовой) — как «перевёрнутая» плоскость трека.
  const mirrored = { ...tr, q: tr.q.map((v, i) => (i % 8 === 0 || i % 8 === 6 ? v + 0.4 : i % 8 === 2 || i % 8 === 4 ? v - 0.4 : v)) };
  assert.equal(E.quadUpright([0, 0, 1, 0, 1, 1, 0, 1]), true);
  assert.equal(E.quadUpright([1, 0, 0, 0, 0, 1, 1, 1]), false);
  assert.equal(bbox(render([word({ trackData: mirrored })], 0.5)), null);
  assert.ok(bbox(render([word()], 0.5)));
});

/** Кадр с фоном: левая половина тёмная, правая светлая (полосы фактуры), сверху — слово режимом blend. */
function onBackdrop(props, t = 0.5) {
  const cv = createCanvas(W, H);
  const g = cv.getContext("2d");
  g.fillStyle = "#202020";
  g.fillRect(0, 0, W / 2, H);
  g.fillStyle = "#d0d0d0";
  g.fillRect(W / 2, 0, W / 2, H);
  for (let x = 0; x < W; x += 6) {
    g.fillStyle = "rgba(0,0,0,0.25)";
    g.fillRect(x, 0, 2, H);
  }
  const still = { kind: "surface", t: [0, 1], q: [0.05, 0.4, 0.95, 0.4, 0.95, 0.5, 0.05, 0.5, 0.05, 0.4, 0.95, 0.4, 0.95, 0.5, 0.05, 0.5], s: "oo", aspect: (0.9 * W) / (0.1 * H) };
  E.drawFrame(g, { layers: [word({ text: "WWWWWWWW", preset: "plain", color: "#ffd84a", trackData: still, ...props })], fps: 30 }, t, env());
  return g;
}

function meanRGB(g, x0, x1) {
  const d = g.getImageData(x0, Math.round(0.43 * H), x1 - x0, Math.round(0.04 * H)).data;
  let r = 0, gg = 0, b = 0, n = 0;
  for (let i = 0; i < d.length; i += 4) { r += d[i]; gg += d[i + 1]; b += d[i + 2]; n++; }
  return [r / n, gg / n, b / n];
}

test("tracked_text paint: краска берёт свет поверхности — на тёмном темнее, на светлом светлее, фактура проступает", () => {
  const paint = onBackdrop({ blend: "paint", paint: { grain: 0, soften: 0 } });
  const flat = onBackdrop({ blend: "normal" });
  const dark = meanRGB(paint, 60, 200);
  const light = meanRGB(paint, 340, 480);
  const flatDark = meanRGB(flat, 60, 200);
  assert.ok(dark[0] < light[0] - 40, `тёмная ${dark} светлая ${light}`);
  assert.ok(dark[0] < flatDark[0] - 30, `краска на тёмном темнее наклейки: ${dark} vs ${flatDark}`);
  // Фактура: полосы фона меняют пиксели букв — краска с фактурой отличается от краски без неё.
  const plain = onBackdrop({ blend: "paint", paint: { grain: 0, soften: 0, texture: 0 } });
  const a = paint.getImageData(300, Math.round(0.42 * H), 200, Math.round(0.06 * H)).data;
  const b = plain.getImageData(300, Math.round(0.42 * H), 200, Math.round(0.06 * H)).data;
  let diff = 0;
  for (let i = 0; i < a.length; i += 4) diff += Math.abs(a[i + 2] - b[i + 2]); // синий: у жёлтой краски красный упирается в 255
  assert.ok(diff / (a.length / 4) > 2, `разница фактуры ${diff / (a.length / 4)}`);
});

test("tracked_text: режимы наложения отличаются, overlay сохраняет тёмный фон тёмным", () => {
  const normal = meanRGB(onBackdrop({ blend: "normal" }), 60, 200);
  const overlay = meanRGB(onBackdrop({ blend: "overlay" }), 60, 200);
  const screen = meanRGB(onBackdrop({ blend: "screen" }), 60, 200);
  assert.ok(overlay[0] < normal[0] - 60, `${overlay} ${normal}`);
  assert.ok(screen[0] >= overlay[0], `${screen} ${overlay}`);
});

test("tracked_text: смаз по движению плоскости — быстрая плоскость размыта, неподвижная резкая", () => {
  const moving = surfaceTrack();
  // Плоскость едет 0.6 кадра в секунду: за затвор (1/30 с) — 11 px.
  const fast = { ...moving, q: moving.q.map((v, i) => (i % 2 === 0 ? v - 0.2 * (Math.floor(i / 8) / 10) + 0.6 * (Math.floor(i / 8) / 10) - 0.1 : v)) };
  assert.equal(E.blurShots(fast, 0.5, E.quadAt(fast, 0.5).q, 30, 1, W, H).length > 1, true);
  assert.equal(E.blurShots(surfaceTrack(), 0.5, E.quadAt(surfaceTrack(), 0.5).q, 30, 0, W, H).length, 1);
  // Резкость по горизонтали: сумма скачков альфы по строкам полосы слова.
  const edgeContrast = (tr, shutter) => {
    const cv = createCanvas(W, H);
    E.drawFrame(cv.getContext("2d"), { layers: [word({ blend: "normal", trackData: tr }, { motionBlur: shutter })], fps: 30 }, 0.5, env());
    const y0 = Math.round(0.38 * H);
    const rows = Math.round(0.14 * H);
    const d = cv.getContext("2d").getImageData(0, y0, W, rows).data;
    let s = 0;
    for (let r = 0; r < rows; r++) for (let x = 1; x < W; x++) s += Math.abs(d[(r * W + x) * 4 + 3] - d[(r * W + x - 1) * 4 + 3]);
    return s;
  };
  const sharp = edgeContrast(fast, 0);
  const blurred = edgeContrast(fast, 1);
  assert.ok(sharp > 1000 && blurred < sharp * 0.8, `${blurred} ${sharp}`);
});

test("tracked_text: потеря трека — слово гаснет плавно, а не мигает", () => {
  const tr = surfaceTrack();
  const t = [];
  for (let i = 0; i <= 60; i++) t.push(i / 60);
  const q = t.flatMap(() => tr.q.slice(0, 8));
  const s = t.map((x) => (x < 0.5 ? "o" : "l")).join("");
  const track = { ...tr, t, q, s };
  const vis = [0.36, 0.42, 0.48, 0.5, 0.54, 0.6, 0.66].map((x) => E.quadAt(track, x).vis);
  for (let i = 1; i < vis.length; i++) assert.ok(vis[i] <= vis[i - 1] + 1e-9, String(vis));
  assert.ok(vis[0] === 1 && vis[vis.length - 1] === 0 && vis[3] > 0.2 && vis[3] < 0.8, String(vis));
});
