// node --test node/vrm/anim.test.mjs — библиотека движений (blend.ts), слой жизни (life.ts), рот по гласным
// (lipsync.ts), ноги IK и выбор клипов (stage.ts) — без браузера.
import { test } from "node:test";
import assert from "node:assert/strict";
import { build } from "esbuild";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const dir = mkdtempSync(join(tmpdir(), "vrm-anim-test-"));
const out = join(dir, "m.mjs");
await build({
  stdin: { contents: `export * from "./src/blend.ts"; export * from "./src/life.ts"; export * from "./src/lipsync.ts"; export { libraryNames, camPos } from "./src/stage.ts"; export { sequencePose } from "./src/sequence.ts";`,
    resolveDir: HERE, loader: "ts" },
  bundle: true, format: "esm", platform: "node", outfile: out, logLevel: "error",
});
const M = await import(pathToFileURL(out).href);
process.on("exit", () => rmSync(dir, { recursive: true, force: true }));

const q = (deg, axis = "y") => {
  const h = (deg * Math.PI) / 360;
  return axis === "y" ? [0, Math.sin(h), 0, Math.cos(h)] : axis === "x" ? [Math.sin(h), 0, 0, Math.cos(h)] : [0, 0, Math.sin(h), Math.cos(h)];
};
/** Клип: голова поворачивается от a до b градусов за n кадров, таз уходит вниз на drop м. */
const clip = (a, b, n = 31, drop = 0, legLength = 0.8) => ({
  fps: 30, frames: n, legLength, spine: "hips",
  bones: { head: Array.from({ length: n }, (_, i) => q(a + ((b - a) * i) / (n - 1))), hips: Array.from({ length: n }, () => q(0)),
    rightUpperArm: Array.from({ length: n }, () => q(-60, "z")) },
  root: Array.from({ length: n }, (_, i) => [0, (-drop * i) / (n - 1), 0]),
});
const yaw = (qq) => (2 * Math.atan2(qq.y, qq.w) * 180) / Math.PI;

test("переход с предвосхищением: ровно 0 и 1 на концах, уходит чуть назад в начале и за 1 в конце", () => {
  assert.equal(M.anticip(0), 0);
  assert.ok(Math.abs(M.anticip(1) - 1) < 1e-12);
  assert.ok(Math.min(...Array.from({ length: 30 }, (_, i) => M.anticip(i / 100))) < 0);
  assert.ok(Math.max(...Array.from({ length: 40 }, (_, i) => M.anticip(0.6 + i / 100))) > 1);
  // Без рывка на стыках: производная на концах ~0.
  assert.ok(Math.abs(M.anticip(0.001) - M.anticip(0)) < 0.001 && Math.abs(M.anticip(1) - M.anticip(0.999)) < 0.001);
});

test("перекрёстный переход основы: детерминирован, без скачка в момент нового клипа, к концу — новый клип", () => {
  const clips = { a: clip(0, 0), b: clip(40, 40) };
  const acts = [{ clip: "a", t: 0 }, { clip: "b", t: 1, fade: 0.4, ease: "smooth" }];
  const at = (t) => yaw(M.blendActions(acts, clips, t, 0.8).bones.head);
  assert.equal(at(1.13), at(1.13));
  assert.ok(Math.abs(at(1.0) - at(0.999)) < 0.5, "в момент нового клипа поза та же");
  assert.ok(Math.abs(at(1.2) - 20) < 0.5, `середина наплыва — половина: ${at(1.2)}`);
  assert.ok(Math.abs(at(1.5) - 40) < 1e-6);
  let prev = at(0.9), maxStep = 0;
  for (let t = 0.9; t < 1.6; t += 1 / 30) {
    const v = at(t);
    maxStep = Math.max(maxStep, Math.abs(v - prev));
    prev = v;
  }
  assert.ok(maxStep < 6, `шаг за кадр ${maxStep}`);
});

test("жест поверх основы: только своя маска, вход и уход по весу, после — снова основа", () => {
  const clips = { base: clip(0, 0), g: clip(30, 30) };
  const acts = [{ clip: "base", t: 0 }, { clip: "g", t: 1, dur: 1, layer: "gesture", mask: ["head"], fade: 0.3, out: 0.3, ease: "smooth" }];
  const at = (t) => M.blendActions(acts, clips, t, 0.8);
  assert.ok(Math.abs(yaw(at(1.5).bones.head) - 30) < 1e-6);
  assert.ok(Math.abs(yaw(at(0.5).bones.head)) < 1e-6 && Math.abs(yaw(at(2.5).bones.head)) < 1e-6);
  assert.equal(at(1.5).layers.gesture, 1);
  // Рука основы жест не трогает (маска — голова).
  assert.deepEqual(at(1.5).bones.rightUpperArm.toArray(), at(0.5).bones.rightUpperArm.toArray());
});

test("таз: перемещение клипа в масштабе ног модели относительно нуля (первый клип основы), solo гасит дорожки", () => {
  const clips = { sit: clip(0, 0, 31, 0.4), up: { ...clip(0, 0, 31, 0), root: Array.from({ length: 31 }, (_, i) => [0, -0.4 + (0.4 * i) / 30, 0]) } };
  const acts = [{ clip: "sit", t: -1 }, { clip: "up", t: 0, fade: 0.01, solo: true, ease: "smooth" }];
  const a = M.blendActions(acts, clips, 1.5, 1.6); // ноги модели вдвое длиннее клипа
  // Ноль — первый кадр «sit» (0); «up» в конце — 0 в единицах клипа → +0 ... sit-ноль 0, up 0 → 0; середина подъёма:
  const mid = M.blendActions(acts, clips, 0.5, 1.6);
  assert.ok(Math.abs(mid.hips[1] - (-0.4 + 0.2) * 2) < 0.02, `таз на середине ${mid.hips[1]}`);
  assert.ok(Math.abs(a.hips[1]) < 1e-6);
  assert.equal(a.solo, 1);
});

test("петля клипа: последний кадр-копия первого не даёт двойного кадра", () => {
  const c = { ...clip(0, 30, 31), loop: true };
  c.bones.hips[30] = c.bones.hips[0];
  const step = (t) => Math.abs(yaw(M.sampleClip(c, "head", t, true)) - yaw(M.sampleClip(c, "head", t - 1 / 30, true)));
  // Период — 30 кадров (1 с): ровно через период поза та же.
  assert.ok(Math.abs(yaw(M.sampleClip(c, "head", 0.2, true)) - yaw(M.sampleClip(c, "head", 1.2, true))) < 1e-9);
  assert.ok(step(0.5) > 0.5);
});

test("слой жизни детерминирован зерном; разные зёрна — разные ритмы", () => {
  const a = M.lifeAt({ seed: 3 }, 7.3, { standing: 1 }), b = M.lifeAt({ seed: 3 }, 7.3, { standing: 1 });
  assert.deepEqual(a, b);
  const c = M.lifeAt({ seed: 4 }, 7.3, { standing: 1 });
  assert.notDeepEqual(a, c);
  // Сидя — без переноса веса.
  assert.deepEqual(M.lifeAt({ seed: 3 }, 7.3, { standing: 0 }).hips, [0, 0, 0]);
  // Дыхание и голова — мелкие углы.
  for (const v of Object.values(a.bones)) for (const x of v) assert.ok(Math.abs(x) < 5, JSON.stringify(a.bones));
});

test("моргание: промежутки 1.6–6.5 с (кроме двойных), глаза закрываются полностью и открываются", () => {
  const ev = M.blinkTimes(11).filter((e) => e.t < 300);
  const gaps = ev.slice(1).map((e, i) => e.t - ev[i].t).filter((g) => g > 0.5);
  assert.ok(Math.min(...gaps) >= 1.6 && Math.max(...gaps) <= 6.5, `${Math.min(...gaps)}..${Math.max(...gaps)}`);
  const mean = gaps.reduce((s, g) => s + g, 0) / gaps.length;
  assert.ok(mean > 2.5 && mean < 4.5, `среднее ${mean}`);
  const e = ev[3];
  const peak = Math.max(...Array.from({ length: 40 }, (_, i) => M.blinkLife(e.t + i * 0.005, 11)));
  assert.ok(peak > 0.95);
  assert.equal(M.blinkLife(e.t + 0.4, 11) < 0.05 || ev.some((x) => Math.abs(x.t - e.t - 0.28) < 0.01), true);
});

test("саккады: мелкие (до 6°), ступеньками, детерминированы", () => {
  let maxA = 0;
  for (let t = 0; t < 60; t += 0.05) {
    const [y, p] = M.saccade(t, 2);
    maxA = Math.max(maxA, Math.hypot(y, p));
  }
  assert.ok(maxA <= 6.01 && maxA > 1, `${maxA}`);
  assert.deepEqual(M.saccade(12.34, 2), M.saccade(12.34, 2));
});

test("рот по гласным: чтение между кадрами, вне дорожки — закрыт, нет дорожки — null", () => {
  const v = { fps: 10, start: 1, aa: [0, 1, 0], ih: [0, 0, 0], ou: [0, 0, 0], ee: [0, 0, 1], oh: [0, 0, 0] };
  assert.equal(M.visemeAt(undefined, 1), null);
  assert.ok(Math.abs(M.visemeAt(v, 1.05).aa - 0.5) < 1e-9);
  assert.ok(Math.abs(M.visemeAt(v, 1.15).ee - 0.5) < 1e-9);
  assert.equal(M.visemeAt(v, 0.5).aa, 0);
  assert.equal(M.openness(M.visemeAt(v, 1.1)), 1);
});

test("ролик с клипами основы: ноги — из клипа (не сидя), рот — гласные вместо громкости, глаза — в камеру", () => {
  const clips = { stand: clip(0, 0) };
  const tl = { clips: {}, tracks: [], actions: [{ clip: "stand", t: 0 }], life: { seed: 1 },
    speech: [[0, 2]], visemes: { fps: 30, start: 0, aa: Array(90).fill(0), ih: Array(90).fill(0.6), ou: Array(90).fill(0), ee: Array(90).fill(0), oh: Array(90).fill(0) } };
  const p = M.sequencePose(tl, clips, 1.0, { cam: [0, 1.5, 3], leg: 0.8 });
  assert.equal(p.bones.leftUpperLeg, undefined);
  assert.ok(Math.abs(p.expr.ih - 0.6) < 1e-9 && p.expr.aa === 0);
  assert.ok(p.look[2] > 2.5, `взгляд ${p.look}`);
  // Без клипов основы — как раньше: сидя.
  const old = M.sequencePose({ clips: {}, tracks: [], speech: [[0, 2]] }, {}, 1.0);
  assert.ok(old.bones.leftUpperLeg[0] < -80);
  assert.deepEqual(M.libraryNames({ clips: { x: "a.json" }, tracks: [{ clip: "x" }, { clip: "wave" }], actions: [{ clip: "cheer", t: 0 }], rootZero: "sit_idle" }).sort(),
    ["cheer", "sit_idle", "wave"]);
});
