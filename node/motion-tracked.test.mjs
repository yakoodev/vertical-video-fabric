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
