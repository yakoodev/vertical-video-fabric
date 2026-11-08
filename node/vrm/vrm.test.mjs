// node --test node/vrm/vrm.test.mjs — чистая математика движения VRM (без браузера): веса дорожек мокапа,
// зеркало, взгляд с догонянием, пресеты пальцев в пределах суставов.
import { test } from "node:test";
import assert from "node:assert/strict";
import { build } from "esbuild";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { spans } from "./handspans.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const dir = mkdtempSync(join(tmpdir(), "vrm-test-"));
const out = join(dir, "m.mjs");
await build({
  stdin: { contents: `export * from "./src/mocap.ts"; export { lookPoint, FINGERS, nodBow, armChain } from "./src/sequence.ts";`, resolveDir: HERE, loader: "ts" },
  bundle: true, format: "esm", platform: "node", outfile: out, logLevel: "error",
});
const M = await import(pathToFileURL(out).href);
process.on("exit", () => rmSync(dir, { recursive: true, force: true }));

test("вес дорожки: плавный вход и выход, вне окна — 0", () => {
  const tr = { clip: "x", start: 1, end: 3, fadeIn: 0.5, fadeOut: 0.5 };
  assert.equal(M.trackWeight(tr, 0.9), 0);
  assert.equal(M.trackWeight(tr, 2), 1);
  assert.ok(M.trackWeight(tr, 1.25) > 0.4 && M.trackWeight(tr, 1.25) < 0.6);
  assert.equal(M.trackWeight(tr, 3.1), 0);
});

test("зеркало: левая кость берётся у правой, x сохраняется, y и z меняют знак", () => {
  const clip = { fps: 30, frames: 2, bones: { rightUpperArm: [[0.1, 0.2, 0.3, 0.927], [0.1, 0.2, 0.3, 0.927]] }, root: [] };
  const q = M.sampleBone(clip, "leftUpperArm", 0, false, true);
  assert.ok(Math.abs(q.x - 0.1) < 1e-6 && Math.abs(q.y + 0.2) < 1e-6 && Math.abs(q.z + 0.3) < 1e-6);
});

test("смешивание: базовая дорожка + верхняя на половине веса", () => {
  const a = { fps: 30, frames: 1, bones: { head: [[0, 0, 0, 1]] }, root: [] };
  const b = { fps: 30, frames: 1, bones: { head: [[0, Math.sin(Math.PI / 4), 0, Math.cos(Math.PI / 4)]] }, root: [] };
  const pose = M.mocapAt({ a, b }, [
    { clip: "a", start: 0, end: 10, fadeIn: 0, fadeOut: 0, mask: ["head"] },
    { clip: "b", start: 0, end: 10, fadeIn: 0, fadeOut: 0, mask: ["head"], weight: 0.5 },
  ], 5);
  const yaw = 2 * Math.asin(pose.head[1]);
  assert.ok(Math.abs(yaw - Math.PI / 4) < 1e-3, `поворот ${yaw}`);
});

test("взгляд догоняет цель плавно и без перелёта", () => {
  const keys = [{ t: 0, target: [0, 1, 1] }, { t: 1, target: [1, 1, 1] }];
  let prev = -1;
  for (let t = 1; t < 3; t += 0.05) {
    const x = M.lookPoint(keys, t, 0.28)[0];
    assert.ok(x >= prev - 1e-9 && x <= 1 + 1e-9);
    prev = x;
  }
  assert.ok(prev > 0.99);
});

test("пресеты пальцев: сгиб 0..1, каскад — мизинец не прямее указательного", () => {
  for (const [name, f] of Object.entries(M.FINGERS)) {
    for (const c of f.curl) assert.ok(c >= 0 && c <= 1, name);
    assert.ok(f.curl[4] >= f.curl[1] - 1e-9, name);
  }
});

test("кивки и поклон: только в своём окне, поклон доходит до глубины и возвращается", () => {
  const tl = { clips: {}, tracks: [], nods: [{ t: 1, n: 2, amp: 10 }], bows: [{ t: 3, dur: 2, deg: 16 }] };
  assert.equal(M.nodBow(tl, 0.9).head, 0);
  assert.ok(M.nodBow(tl, 1.225).head > 4); // низ первого кивка — середина периода 0.45 с
  assert.equal(M.nodBow(tl, 2.0).head, 0);
  const mid = M.nodBow(tl, 4.0);
  assert.ok(Math.abs(mid.spine + mid.chest + mid.neck + mid.head - 16 * 1.4) < 0.5);
  assert.equal(M.nodBow(tl, 5.01).spine, 0);
});

test("руки: новый ключ посреди перехода не дёргает кисть (цепочка), дальний переход — дугой вперёд", () => {
  const keys = [{ t: -3, left: "idle", right: "idle" }, { t: 1, dur: 0.8, left: "idle", right: "mouth" },
    { t: 2.5, dur: 0.8, left: "idle", right: "idle" }, { t: 2.9, dur: 0.8, left: "keys", right: "keys" }];
  let prev = null, maxStep = 0;
  for (let t = 0.5; t < 4.5; t += 1 / 120) {
    const p = M.armChain(keys, -1, t).target;
    if (prev) maxStep = Math.max(maxStep, Math.hypot(p[0] - prev[0], p[1] - prev[1], p[2] - prev[2]));
    prev = p;
  }
  assert.ok(maxStep < 0.01, `скачок цели ${maxStep.toFixed(4)} м за 1/120 с`);
  // Середина перехода «клавиатура → рот» — впереди прямой между ними (мимо груди).
  const a = M.armChain(keys, -1, 0.9).target, b = M.armChain(keys, -1, 2.4).target, mid = M.armChain(keys, -1, 1.4).target;
  assert.ok(mid[2] > (a[2] + b[2]) / 2 + 0.03, `дуга ${mid[2]}`);
});

test("касание клавиш выключено, пока кисть едет издалека", () => {
  const keys = [{ t: -3, left: "idle", right: "wave" }, { t: 1, dur: 0.8, left: "idle", right: "idle" }];
  assert.equal(!!M.armChain(keys, -1, 1.4).touch, false);
  assert.equal(!!M.armChain(keys, -1, 2.0).touch, true);
});

test("отрезки проникновений рук: подряд идущие кадры одной группы, худший зазор", () => {
  const bad = [{ t: 1, key: "torso", gap: -0.002, point: "a", against: "torso" }, { t: 1 + 1 / 30, key: "torso", gap: -0.005, point: "b", against: "torso" },
    { t: 3, key: "torso", gap: -0.001, point: "c", against: "torso" }, { t: 1, key: "head", gap: -0.0004, point: "d", against: "head" }];
  const s = spans(bad, 30);
  assert.equal(s.length, 3);
  const t = s.find((x) => x.key === "torso" && x.t0 === 1);
  assert.equal(t.gap_mm, -5);
  assert.equal(t.point, "b");
});
