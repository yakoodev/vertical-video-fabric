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
import { emptyChecks } from "./checks.mjs";
import { FrameError, frameProblem, presenceSummary, renderChunkRetry } from "../scene3d/render.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const dir = mkdtempSync(join(tmpdir(), "vrm-test-"));
const out = join(dir, "m.mjs");
await build({
  stdin: { contents: `export * from "./src/mocap.ts"; export { lookPoint, FINGERS, nodBow, armChain } from "./src/sequence.ts"; export { straightCut, CUT_X, CUT_Y } from "./src/cutcheck.ts";`, resolveDir: HERE, loader: "ts" },
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

test("кадр без героини: потерян контекст GPU, модели нет, сеток нет, холст пуст — проблема; вставка — нет", () => {
  const ok = { luma: 40, presence: { expect: 1, models: 1, meshes: 12, cover: 0.31 }, lost: [] };
  assert.equal(frameProblem(ok), null);
  assert.match(frameProblem({ ...ok, lost: ["webgl 1080×1920"] }), /контекст GPU/);
  assert.match(frameProblem({ ...ok, presence: { ...ok.presence, models: 0 } }), /модели нет/);
  assert.match(frameProblem({ ...ok, presence: { ...ok.presence, meshes: 0 } }), /сеток/);
  assert.match(frameProblem({ ...ok, presence: { ...ok.presence, cover: 0 } }), /холст пуст/);
  assert.equal(frameProblem({ luma: 40, presence: { expect: 0, models: 1, meshes: 12, cover: 0 }, lost: [] }), null);
  assert.equal(frameProblem({ luma: 40 }), null); // движок scene3d присутствие не сообщает
  const sum = presenceSummary([{ presence: [{ frame: 0, expect: 1 }, { frame: 1, expect: 0 }] }, { presence: [{ frame: 2, expect: 1 }] }]);
  assert.deepEqual([sum.frames, sum.expected, sum.missing], [3, 2, 0]);
  assert.equal(presenceSummary([{ luma: [1] }]), null);
});

test("кусок с потерей контекста переснимается один раз; вторая потеря — ошибка", async () => {
  let n = 0;
  const flaky = async () => {
    if (n++ === 0) throw new FrameError("кадр 270: потерян контекст GPU");
    return { luma: [40], presence: [], warnings: [] };
  };
  const r = await renderChunkRetry({}, {}, 30, [0, 10], "seg.mp4", () => {}, flaky);
  assert.equal(n, 2);
  assert.match(r.retried, /контекст GPU/);
  const dead = async () => {
    throw new FrameError("кадр 270: модели нет в сцене");
  };
  await assert.rejects(renderChunkRetry({}, {}, 30, [0, 10], "seg.mp4", () => {}, dead), (e) => e instanceof FrameError && /после пересъёмки/.test(e.message));
  // Другие ошибки (ffmpeg, браузер) не переснимаются.
  let m = 0;
  const other = async () => {
    m++;
    throw new Error("ffmpeg: код 1");
  };
  await assert.rejects(renderChunkRetry({}, {}, 30, [0, 10], "seg.mp4", () => {}, other), /ffmpeg/);
  assert.equal(m, 1);
});

test("пустые проверки касаний — причина, а не «ок»", () => {
  const spec = { timeline: { surfaces: [{ name: "стол" }], chair: { pos: [0, 0.8, 0] } } };
  const good = { frames: 100, min: 0.008, chair: { min: 0.003 }, hair: { min: 0.005 }, hands: { frames: 100, gaps: { torso: { min: 0.02 } } } };
  assert.deepEqual(emptyChecks(good, spec, 80), []);
  assert.deepEqual(emptyChecks(good, { ...spec, timeline: undefined }, 80), []);
  const empty = { frames: 0, min: Infinity, chair: { min: Infinity }, hair: { min: Infinity }, hands: { frames: 0, gaps: {} } };
  const why = emptyChecks(empty, spec, 80).join("; ");
  for (const w of ["пальцы", "кресло", "волосы", "руки"]) assert.match(why, new RegExp(w));
  assert.match(emptyChecks({ ...good, hands: { frames: 100, gaps: {} } }, spec, 80).join(), /руки: нет ни одного/);
  assert.match(emptyChecks({ ...good, hands: null }, spec, 80).join(), /не запускалась/);
  assert.match(emptyChecks(good, spec, 80, ["кадр 5: модели нет в сцене"]).join(), /модели нет на 1 кадрах/);
});

test("разрез персонажа: край сдвинутого холста — ровная вертикаль; живой силуэт и край кадра — нет", () => {
  const w = 270, h = 480;
  const mask = (inside) => {
    const a = new Uint8Array(w * h);
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) a[y * w + x] = inside(x, y) ? 255 : 0;
    return a;
  };
  // Голова и плечи через плечо (эллипс у левого края), холст сдвинут вправо на 37 px: слева от x=37 — пусто.
  const head = (x, y) => ((x - 20) / 110) ** 2 + ((y - 380) / 230) ** 2 < 1;
  const cut = M.straightCut(mask((x, y) => x >= 37 && head(x, y)), w, h);
  assert.ok(cut.x.run >= M.CUT_X, `разрез ${JSON.stringify(cut)}`);
  assert.ok(Math.abs(cut.x.at - 37) <= 1);
  // Тот же силуэт целиком (уходит за левый край кадра) — разреза нет.
  assert.ok(M.straightCut(mask(head), w, h).x.run < M.CUT_X);
  // Волнистая прядь (почти вертикальная) — не разрез.
  assert.ok(M.straightCut(mask((x, y) => x > 120 + 6 * Math.sin(y / 9) && x < 200 + 5 * Math.cos(y / 7)), w, h).x.run < M.CUT_X);
  // RGBA с шагом 4 (альфа — четвёртый байт), горизонтальный разрез снизу.
  const rgba = new Uint8Array(w * h * 4);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) if (y < 300 && x > 20 && x < 250) rgba[(y * w + x) * 4 + 3] = 255;
  const c2 = M.straightCut(rgba, w, h, 4, 3);
  assert.ok(c2.y.run >= M.CUT_Y && Math.abs(c2.y.at - 300) <= 1, JSON.stringify(c2));
});

test("кадр: разрез и камера в теле — проблема", () => {
  const p = { expect: 1, models: 1, meshes: 25, cover: 0.5, near: 1.2, cameraNear: 0.05, cutX: 0.2, cutY: 0.5,
    cut: { x: { at: 120, run: 0.09 }, y: { at: 900, run: 0.3 } } };
  assert.equal(frameProblem({ presence: p, lost: [] }), null);
  assert.match(frameProblem({ presence: { ...p, cut: { ...p.cut, x: { at: 148, run: 0.5 } } }, lost: [] }), /вертикалью x=148/);
  assert.match(frameProblem({ presence: { ...p, cut: { ...p.cut, y: { at: 1500, run: 0.7 } } }, lost: [] }), /горизонталью/);
  assert.match(frameProblem({ presence: { ...p, near: 0.1 }, lost: [] }), /камера в теле/);
});
