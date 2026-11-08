// node --test node/vrm/psx.test.mjs — режим PS1 без браузера: 15 бит и дизеринг, упрощение сеткой, облёт камеры,
// проверка присутствия, стойка.
import { test } from "node:test";
import assert from "node:assert/strict";
import { build } from "esbuild";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const dir = mkdtempSync(join(tmpdir(), "psx-test-"));
const out = join(dir, "m.mjs");
await build({
  stdin: {
    contents: `export { q5, DITHER4, ditherQuantize, hardAlpha, clusterDecimate } from "./src/psx/psx.ts";
export { orbitAt, orbitParams, presence } from "./src/psx/orbit.ts";
export { standPose } from "./src/psx/idle.ts";
export { sitWeight, sitBones, sitHips } from "./src/psx/sit.ts";
export { spawnScale } from "./src/psx/things.ts";
export { typed } from "./src/psx/captions.ts";
export * as THREE from "three";`,
    resolveDir: HERE, loader: "ts",
  },
  bundle: true, format: "esm", platform: "node", outfile: out, logLevel: "error",
});
const M = await import(pathToFileURL(out).href);
process.on("exit", () => rmSync(dir, { recursive: true, force: true }));

test("15 бит: 32 уровня на канал, чёрный и белый сохраняются", () => {
  const levels = new Set();
  for (let v = 0; v < 256; v++) levels.add(M.q5(v));
  assert.equal(levels.size, 32);
  assert.equal(M.q5(0), 0);
  assert.equal(M.q5(255), 255);
});

test("дизеринг: матрица PS1 4×4 со средним около нуля; ровная заливка даёт узор, без дизеринга — нет", () => {
  assert.equal(M.DITHER4.length, 16);
  assert.ok(Math.abs(M.DITHER4.reduce((a, b) => a + b, 0) / 16) <= 0.5);
  const flat = () => {
    const d = new Uint8ClampedArray(4 * 4 * 4);
    for (let i = 0; i < d.length; i += 4) d.set([102, 150, 200, 255], i); // 102 — между уровнями 96 и 104
    return d;
  };
  const a = flat();
  M.ditherQuantize(a, 4, 4, true);
  const reds = new Set();
  for (let i = 0; i < a.length; i += 4) reds.add(a[i]);
  assert.ok(reds.size >= 2, "дизеринг смешивает соседние уровни");
  for (const r of reds) assert.equal(r, M.q5(r), "все цвета — 15-битные");
  const b = flat();
  M.ditherQuantize(b, 4, 4, false);
  const reds2 = new Set();
  for (let i = 0; i < b.length; i += 4) reds2.add(b[i]);
  assert.equal(reds2.size, 1);
});

test("интерфейс: альфа — только 0, ½ и 1", () => {
  const d = new Uint8ClampedArray([10, 10, 10, 30, 10, 10, 10, 120, 10, 10, 10, 220]);
  M.hardAlpha(d);
  assert.deepEqual([d[3], d[7], d[11]], [0, 128, 255]);
});

test("упрощение сеткой: соседние вершины сливаются, вырожденные треугольники уходят, меш с морфами не трогается", () => {
  const { THREE } = M;
  const g = new THREE.PlaneGeometry(1, 1, 20, 20);
  const n0 = g.index.count / 3;
  const [before, after] = M.clusterDecimate(g, 0.2, 1);
  assert.equal(before, n0);
  assert.ok(after < n0 / 4 && after > 0, `${n0} → ${after}`);
  const idx = g.index.array;
  for (let t = 0; t < idx.length; t += 3) assert.ok(idx[t] !== idx[t + 1] && idx[t + 1] !== idx[t + 2] && idx[t] !== idx[t + 2]);
  const m = new THREE.PlaneGeometry(1, 1, 8, 8);
  m.morphAttributes.position = [m.attributes.position.clone()];
  const [b2, a2] = M.clusterDecimate(m, 0.5);
  assert.equal(b2, a2);
});

test("облёт: yaw 0 — спереди (+Z), 90 — с её левой стороны (+X); между ключами — плавно и без перелёта за края", () => {
  const front = M.orbitAt([{ yaw: 0, dist: 3, height: 1.2, target: 0.9 }], 0);
  assert.ok(Math.abs(front.pos[2] - 3) < 1e-9 && Math.abs(front.pos[0]) < 1e-9 && front.pos[1] === 1.2);
  const side = M.orbitAt([{ yaw: 90, dist: 2, height: 1, target: [0, 1, 0] }], 0);
  assert.ok(Math.abs(side.pos[0] - 2) < 1e-9 && Math.abs(side.pos[2]) < 1e-9);
  const keys = [{ t: 0, yaw: 200, dist: 6, height: 1, target: 0 }, { t: 3, yaw: 280, dist: 4, height: 1.4, target: 0.5 },
    { t: 6, yaw: 360, dist: 3.5, height: 1.1, target: 0.85 }];
  let prev = -1e9, prevStep = null;
  for (let t = 0; t <= 6; t += 0.05) {
    const y = M.orbitParams(keys, t).yaw;
    assert.ok(y >= prev - 1e-9 && y <= 360 + 1e-9, `t=${t}: ${y}`);
    if (prev > -1e9) {
      const step = y - prev;
      if (prevStep !== null) assert.ok(Math.abs(step - prevStep) < 0.2, `рывок скорости на t=${t}`);
      prevStep = step;
    }
    prev = y;
  }
  assert.equal(M.orbitParams(keys, 9).yaw, 360);
});

test("присутствие: фигура перед камерой в кадре — да; за спиной камеры или ниже края — нет", () => {
  const { THREE } = M;
  const cam = new THREE.PerspectiveCamera(32, 9 / 16, 0.05, 100);
  cam.position.set(0, 1.1, 3.5);
  cam.lookAt(0, 0.85, 0);
  cam.updateMatrixWorld(true);
  const pts = (dx) => [new THREE.Vector3(dx, 1.55, 0), new THREE.Vector3(dx, 0.85, 0), new THREE.Vector3(dx, 0.08, 0)];
  const ok = M.presence(cam, pts(0));
  assert.ok(ok.inFront && ok.headEdge > 0 && ok.height > 0.5, JSON.stringify(ok));
  assert.ok(M.presence(cam, pts(5)).headEdge < 0, "сбоку за кадром");
  cam.position.set(0, 1.1, -3.5);
  cam.lookAt(0, 0.85, -10);
  cam.updateMatrixWorld(true);
  assert.equal(M.presence(cam, pts(0)).inFront, false);
});

test("стойка: руки у бёдер по бокам, вес переносится с ноги на ногу", () => {
  const a = M.standPose(0.0, { hipsY: 0.8, period: 4 }), b = M.standPose(1.0, { hipsY: 0.8, period: 4 }), c = M.standPose(3.0, { hipsY: 0.8, period: 4 });
  for (const p of [a, b, c]) {
    assert.ok(p.left.target[0] > 0.15 && p.right.target[0] < -0.15, "левая рука — +X, правая — −X");
    assert.ok(p.left.target[1] < 0.8 && p.left.target[1] > 0.55);
  }
  assert.ok(Math.sign(b.hips[0]) === -Math.sign(c.hips[0]) && Math.abs(b.hips[0]) > 0.01, "таз уходит в разные стороны");
});

test("сесть: вес растёт за время перехода и держится; встать — обратно", () => {
  const keys = [{ id: "sit", t: 2, dur: 1 }, { id: "stand", t: 6, dur: 1 }];
  assert.equal(M.sitWeight(keys, 1).k, 0);
  const mid = M.sitWeight(keys, 2.5);
  assert.ok(mid.k > 0.3 && mid.k < 0.7 && mid.phase > 0);
  assert.equal(M.sitWeight(keys, 4).k, 1);
  assert.equal(M.sitWeight(keys, 4).phase, 0);
  assert.equal(M.sitWeight(keys, 8).k, 0);
  const b = M.sitBones({ spine: [1, 0, 0] }, 1, 0);
  assert.ok(b.leftUpperLeg[0] < -90 && b.leftLowerLeg[0] > 90, "бедро вперёд-вверх, колено согнуто");
  assert.deepEqual(M.sitBones({ spine: [1, 0, 0] }, 0, 0), { spine: [1, 0, 0] });
  assert.ok(M.sitHips([0.02, 0, 0], 1)[2] < -0.2, "таз назад");
});

test("предметы появляются ступеньками с перелётом; набор текста по буквам", () => {
  assert.equal(M.spawnScale(1, undefined), 1);
  assert.equal(M.spawnScale(0.9, 1), 0);
  const steps = [1.1, 1.2, 1.3, 1.4, 1.5].map((t) => M.spawnScale(t, 1));
  assert.ok(Math.max(...steps) > 1.05 && M.spawnScale(2, 1) === 1);
  assert.equal(M.typed("привет", 0.1, 30), "при");
  assert.equal(M.typed("привет", -1), "");
});
