// Логотип, который пишется кистью (frontend/src/motion/layers/logoreveal.ts): расписание мазков, ход кисти,
// удар, маска по мазкам, режимы sweep/ink, уход, рамка и ссылки на картинку. `node --test node/motion-logo.test.mjs`
// (в `npm run test:motion`). Мазки извлекает app/logo_vectorize.py — его тесты в tests/test_logo_vectorize.py.
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
  tmp = mkdtempSync(join(tmpdir(), "vvf-logo-"));
  const out = join(tmp, "engine.mjs");
  await build({ entryPoints: [join(ROOT, "frontend", "src", "motion", "index.ts")], bundle: true, platform: "node", format: "esm", outfile: out, logLevel: "warning" });
  E = await import(pathToFileURL(out).href);
});

after(() => {
  if (tmp) rmSync(tmp, { recursive: true, force: true });
});

// Картинка логотипа: белая полоса во всю ширину (1000×200) — мазок по её оси открывает её слева направо.
function logoImage() {
  const cv = createCanvas(1000, 200);
  const g = cv.getContext("2d");
  g.fillStyle = "#ffffff";
  g.fillRect(0, 60, 1000, 80);
  return cv;
}

// Мазки в долях ширины: один горизонтальный (группа 0) и короткий вертикальный (группа 1).
const VECTOR = {
  v: 1,
  aspect: 0.2,
  strokes: [
    { p: [0.02, 0.1, 0.04, 0.5, 0.1, 0.04, 0.98, 0.1, 0.04], g: 0 },
    { p: [0.9, 0.02, 0.01, 0.9, 0.18, 0.01], g: 1 },
  ],
  dots: [[0.5, 0.19, 0.005, 0, 0.5]],
};

const layer = (props = {}, extra = {}) => ({ id: "lg", type: "logo_reveal", start: 0, end: 3, x: 0.5, y: 0.5, props: { asset_id: "7", logo: VECTOR, w: 1000, glow: null, splatter: 0, hit: null, ...props }, ...extra });

function frame(l, t, img = logoImage()) {
  const cv = createCanvas(1080, 1920);
  const env = { width: 1080, height: 1920, getImage: (ref) => (ref === "7" ? img : null), createCanvas: (a, b) => createCanvas(a, b) };
  E.drawFrame(cv.getContext("2d"), { layers: [l] }, t, env);
  return cv;
}

// Сколько непрозрачных пикселей в левой и правой половине полосы логотипа (y 960±40 опорного кадра).
function halves(cv) {
  const d = cv.getContext("2d").getImageData(40, 925, 1000, 70).data;
  let left = 0;
  let right = 0;
  for (let i = 3; i < d.length; i += 4) {
    if (d[i] < 128) continue;
    const x = ((i - 3) / 4) % 1000;
    if (x < 500) left++;
    else right++;
  }
  return { left, right };
}

test("расписание: сумма — ровно draw, мазки по порядку, пауза между буквами, свой порядок", () => {
  const s = E.logoSchedule(VECTOR, 1.2);
  assert.equal(s.strokes.length, 2);
  assert.ok(Math.abs(s.end - 1.2) < 1e-9);
  const [a, b] = s.strokes;
  assert.equal(a.index, 0);
  assert.ok(a.t0 === 0 && a.t1 < b.t0, "между группами — пауза");
  assert.ok(a.t1 - a.t0 > b.t1 - b.t0, "длинный мазок пишется дольше");
  const swapped = E.logoSchedule(VECTOR, 1.2, [1]);
  assert.deepEqual(swapped.strokes.map((x) => x.index), [1, 0]);
  assert.ok(Math.abs(E.strokeLength(VECTOR.strokes[0].p) - 0.96) < 1e-9);
});

test("ход кисти: монотонный, обратная функция сходится, удар — замах, рывок, затухание", () => {
  let prev = -1;
  for (let i = 0; i <= 20; i++) {
    const v = E.brushEase(i / 20);
    assert.ok(v >= prev);
    prev = v;
  }
  const tm = { index: 0, t0: 0.5, t1: 1.5, len: 1, group: 0 };
  for (const u of [0.1, 0.5, 0.9]) assert.ok(Math.abs(E.strokeProgress(tm, E.timeAtProgress(tm, u)) - u) < 1e-3);
  const r = { hitAt: 1, hitScale: 1.1 };
  assert.equal(E.hitScaleAt(r, 0.5), 1);
  assert.ok(E.hitScaleAt(r, 0.97) < 1, "замах перед ударом");
  assert.ok(Math.abs(E.hitScaleAt(r, 1) - 1.1) < 1e-9);
  assert.equal(E.hitScaleAt(r, 2), 1);
  assert.equal(E.hitScaleAt({ hitAt: null, hitScale: 1.1 }, 1), 1);
});

test("маска по мазку: середина письма — открыта левая часть, после письма — весь логотип", () => {
  const l = layer({ draw: 1 });
  const mid = halves(frame(l, 0.3));
  assert.ok(mid.left > 1000 && mid.right < mid.left * 0.2, JSON.stringify(mid));
  const done = halves(frame(l, 1.5));
  const full = 1000 * 70 * 0.5;
  assert.ok(done.left > full * 0.95 && done.right > full * 0.95, JSON.stringify(done));
  // Нет картинки — слой ничего не рисует.
  const empty = frame(l, 1.5, null);
  assert.equal(halves(empty).left, 0);
});

test("без мазков, sweep и ink: открывается по-своему и к концу целиком", () => {
  for (const props of [{ logo: null }, { reveal: "sweep" }, { reveal: "ink" }]) {
    const l = layer({ draw: 1, ...props });
    const done = halves(frame(l, 1.6));
    assert.ok(done.left > 30000 && done.right > 30000, `${JSON.stringify(props)} ${JSON.stringify(done)}`);
    const early = halves(frame(l, 0.25));
    assert.ok(early.left + early.right < done.left + done.right, JSON.stringify(props));
  }
});

test("уход и брызги: fade гасит к концу слоя, капли ложатся за пределами полосы", () => {
  const fade = layer({ draw: 0.5, exit: { kind: "fade", dur: 0.5 } });
  const late = halves(frame(fade, 2.95));
  const held = halves(frame(fade, 2.0));
  assert.ok(late.left < held.left * 0.3, `${late.left} vs ${held.left}`);
  for (const kind of ["wipe", "slash"]) {
    const l = layer({ draw: 0.5, exit: { kind, dur: 0.6 } });
    const a = halves(frame(l, 2.95));
    assert.ok(a.left + a.right < held.left + held.right, kind);
  }
  // Брызги: с splatter > 0 вокруг полосы пикселей больше, чем без него.
  const around = (cv) => {
    const d = cv.getContext("2d").getImageData(40, 760, 1000, 140).data;
    let n = 0;
    for (let i = 3; i < d.length; i += 4) if (d[i] > 60) n++;
    return n;
  };
  // (блик на кончике кисти рисуется и без брызг — сравниваем с ним, а не с нулём)
  const base = around(frame(layer({ draw: 1 }), 0.6));
  assert.ok(around(frame(layer({ draw: 1, splatter: 1 }), 0.6)) > base + 50);
});

test("рамка и ссылки: размер по aspect мазков, картинка в списке загрузки", () => {
  assert.deepEqual(E.layerSize(layer({ w: 800 })), [800, 160]);
  assert.deepEqual(E.layerSize(layer({ w: 500, logo: null }), { 7: [1000, 500] }), [500, 250]);
  assert.ok(E.collectImageRefs([layer()]).includes("7"));
  const r = E.resolveLogo(layer({ exit: { kind: "nope" }, hit: { scale: 9 } }).props, null);
  assert.equal(r.exitKind, "none");
  assert.equal(r.hitScale, 1.5);
});

test("кипение: рисунки на двойках, петля после рывка, эскиз — тёмная тушь с белым контуром", () => {
  assert.equal(E.BOIL_FPS, 12);
  // До петли — свой рисунок на каждую 1/12 с, после рывка — по кругу из loop рисунков, до ухода.
  assert.equal(E.boilDrawing(0.5, 1, 4, 3), 6);
  const inLoop = [1.0, 1.0 + 4 / 12, 1.0 + 8 / 12].map((t) => E.boilDrawing(t + 0.01, 1, 4, 3));
  assert.equal(new Set(inLoop).size, 1, "через loop рисунков — тот же рисунок");
  assert.equal(E.boilDrawing(3.5, 1, 4, 3), 42, "на уходе петля кончается");
  const l = layer({ draw: 1, boil: 3, sketch: true, burst: 0.3, exit: { kind: "scratch", dur: 0.4 } });
  const sketchCv = frame(l, 0.95);
  // Контур — по краю полосы (y 920 и 1000), берём с запасом.
  const d = sketchCv.getContext("2d").getImageData(40, 905, 1000, 110).data;
  let dark = 0;
  let white = 0;
  for (let i = 0; i < d.length; i += 4) {
    if (d[i + 3] < 100) continue;
    if (d[i] < 60) dark++;
    else if (d[i] > 200) white++;
  }
  assert.ok(dark > 5000 && white > 500, `тушь ${dark}, контур ${white}`);
  // После рывка — залитый белый логотип; два кадра одного рисунка совпадают, соседние рисунки — нет.
  const filled = halves(frame(l, 2.0));
  assert.ok(filled.left > 30000 && filled.right > 30000, JSON.stringify(filled));
  const a = frame(l, 2.0).toBuffer("image/png");
  const b = frame(l, 2.0 + 0.02).toBuffer("image/png");
  const c = frame(l, 2.0 + 1 / 12).toBuffer("image/png");
  assert.ok(a.equals(b) && !a.equals(c));
  // Уход царапинами стирает логотип к концу слоя.
  const gone = halves(frame(l, 2.97));
  assert.ok(gone.left + gone.right < (filled.left + filled.right) * 0.3, JSON.stringify(gone));
});
