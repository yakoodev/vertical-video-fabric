/**
 * 3D-движок сцен: чистые функции времени (timeline.ts), раздача файлов странице, нарезка кадров —
 * и в настоящем headless Chromium (если есть браузер, паки и бандл): загрузка GLB/glTF со скелетом,
 * детерминированность кадра (тот же t в любом порядке — те же байты), ничего «не висит».
 *
 *   npm run test:scene3d        (собирает бандл и запускает этот файл)
 */
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve, dirname } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import {
  actionStates, ease, facingYaw, lerpAngle, pathYaw, sampleKeys, sampleVec, samplePath, shake, windowWeight,
} from "./src/timeline.ts";
import { chromeArgs, concatArgs, findChrome, openScene, parseArgs, safeJoin, segmentArgs, serve, splitFrames } from "./render.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, "..", "..");
const PACKS = join(ROOT, "vendor", "packs");
const BUNDLE = join(HERE, "dist", "scene3d.js");

/* ---------- время ---------- */

test("изинги: концы 0 и 1, inOutSine симметричен", () => {
  for (const name of ["linear", "inSine", "outSine", "inOutSine", "inOutCubic", "outCubic", "smooth"]) {
    assert.ok(Math.abs(ease(name, 0)) < 1e-9, name);
    assert.ok(Math.abs(ease(name, 1) - 1) < 1e-9, name);
  }
  assert.ok(Math.abs(ease("inOutSine", 0.5) - 0.5) < 1e-9);
  assert.equal(ease("нет-такого", 0.5), ease("inOutSine", 0.5));
});

test("ключи: до/после края, изинг сегмента, склейка cut", () => {
  const keys = [{ t: 0, v: [0, 0, 0] }, { t: 2, v: [2, 0, 0], ease: "linear" }, { t: 2, cut: true, v: [10, 0, 0] }, { t: 4, v: [12, 0, 0], ease: "linear" }];
  assert.deepEqual(sampleVec(keys, -1, "v"), [0, 0, 0]);
  assert.deepEqual(sampleVec(keys, 1, "v"), [1, 0, 0]);
  assert.deepEqual(sampleVec(keys, 3, "v"), [11, 0, 0]);
  assert.deepEqual(sampleVec(keys, 9, "v"), [12, 0, 0]);
  assert.equal(sampleKeys([], 1, "v", (a) => a), undefined);
});

test("углы по кратчайшей дуге", () => {
  assert.equal(lerpAngle(170, -170, 0.5), 180);
  assert.equal(lerpAngle(-10, 10, 0.5), 0);
});

test("путь: проходит через точки, стоянка неподвижна, направление по движению", () => {
  const path = [{ t: 0, p: [0, 0] }, { t: 1, p: [0, 2] }, { t: 2, p: [2, 2] }, { t: 3, p: [2, 2] }];
  assert.deepEqual(samplePath(path, 0), [0, 0, 0]);
  const mid = samplePath(path, 1);
  assert.ok(Math.abs(mid[0]) < 1e-9 && Math.abs(mid[2] - 2) < 1e-9);
  assert.deepEqual(samplePath(path, 2.5), [2, 0, 2]);
  assert.ok(Math.abs(pathYaw(path, 0.5) - 0) < 15, "сначала идём вдоль +z");
  assert.ok(Math.abs(pathYaw(path, 1.7) - 90) < 15, "потом вдоль +x");
  assert.ok(Math.abs(pathYaw(path, 2.8) - 90) < 15, "стоим — смотрим, куда шли");
  // facing: явный поворот за dur, потом снова «по пути».
  const f = [{ t: 2.2, yaw: 180, dur: 0.4 }];
  assert.ok(Math.abs(facingYaw(path, f, 2.7) - 180) < 1e-6);
});

test("действия: веса нормируются, первое не вплывает из T-позы, циклы по модулю", () => {
  const acts = [{ clip: "Walk_Loop", start: 0, end: 2, fade: 0.4 }, { clip: "Idle_Loop", start: 1.8, fade: 0.4 }];
  const d = { Walk_Loop: 1.33, Idle_Loop: 2.5 };
  const s0 = actionStates(acts, 0, d);
  assert.equal(s0.length, 1);
  assert.equal(s0[0].weight, 1);
  const s = actionStates(acts, 1.9, d);
  assert.ok(Math.abs(s.reduce((a, b) => a + b.weight, 0) - 1) < 1e-9);
  const walk = actionStates(acts, 1.5, d).find((x) => x.clip === "Walk_Loop");
  assert.ok(walk.time >= 0 && walk.time < 1.33);
  assert.deepEqual(actionStates(acts, 5, d).map((x) => x.clip), ["Idle_Loop"]);
});

test("окно веса и тряска — 0 вне интервала, затухают", () => {
  assert.equal(windowWeight(-1, 0, 1), 0);
  assert.equal(windowWeight(0.5, 0, 1, 0.1, 0.1), 1);
  assert.equal(shake(0.9, 1, 1), 0);
  assert.ok(Math.abs(shake(1.05, 1, 1)) > Math.abs(shake(1.85, 1, 1)));
});

test("нарезка кадров на куски: подряд, без дыр и пересечений", () => {
  for (const [total, parts] of [[270, 4], [10, 4], [301, 3], [1, 8]]) {
    const chunks = splitFrames(total, parts);
    assert.equal(chunks[0][0], 0);
    assert.equal(chunks.at(-1)[1], total);
    for (let i = 1; i < chunks.length; i++) assert.equal(chunks[i][0], chunks[i - 1][1]);
  }
  assert.equal(splitFrames(10, 4).length, 1, "мелкие куски не дробим");
});

/* ---------- обвязка рендера ---------- */

test("аргументы: обязательные, кадры, превью, gl", () => {
  assert.throws(() => parseArgs([]), /--spec/);
  assert.throws(() => parseArgs(["--spec", "a.json"]), /--out/);
  const o = parseArgs(["--spec", "a.json", "--out", "b.mp4", "--frames", "10:20", "--gl", "gpu", "--workers", "99"]);
  assert.deepEqual(o.frames, [10, 20]);
  assert.equal(o.gl, "gpu");
  assert.equal(o.workers, 16);
  assert.throws(() => parseArgs(["--spec", "a", "--out", "b", "--frames", "20:10"]), /B > A/);
  assert.deepEqual(parseArgs(["--spec", "a", "--preview", "0,1.5"]).previews, [0, 1.5]);
  assert.ok(chromeArgs("p", "swiftshader").includes("--use-angle=swiftshader"));
  assert.ok(chromeArgs("p", "swiftshader").includes("--host-resolver-rules=MAP * ~NOTFOUND"), "сеть закрыта");
});

test("раздача файлов: только паки и движок, без выхода из каталога", () => {
  const base = resolve(tmpdir());
  assert.equal(safeJoin(base, "../x"), null);
  assert.equal(safeJoin(base, "a/../../x"), null);
  assert.equal(safeJoin(base, "%2e%2e/x"), null);
  assert.equal(safeJoin(base, "/etc/passwd"), null);
  assert.ok(safeJoin(base, "pack/file.glb").startsWith(base));
  const ctx = { width: 100, height: 200, bundle: BUNDLE, packs: PACKS };
  assert.equal(serve("https://evil.example/x", ctx).status, 403);
  assert.equal(serve("http://scene3d.local/packs/../package.json", ctx).status, 404);
  assert.equal(serve("http://scene3d.local/etc", ctx).status, 404);
  const page = serve("http://scene3d.local/index.html", ctx);
  assert.equal(page.status, 200);
  assert.match(page.body.toString(), /width:100px/);
});

test("ffmpeg: сегменты в ТВ-диапазоне BT.709, склейка без перекодирования", () => {
  const a = segmentArgs(30, 18, "jpeg", "s.mp4");
  assert.ok(a.includes("libx264") && a.includes("mjpeg"));
  assert.ok(a.join(" ").includes("out_range=tv"));
  assert.equal(a[a.indexOf("-g") + 1], "60");
  const c = concatArgs("list.txt", "o.mp4");
  assert.equal(c[c.indexOf("-c") + 1], "copy");
});

/* ---------- настоящий браузер ---------- */

function chromeOrNull() {
  try {
    return findChrome(process.env.SCENE3D_CHROMIUM || process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || "");
  } catch {
    return null;
  }
}

const HAVE = chromeOrNull() && existsSync(BUNDLE) && existsSync(join(PACKS, "kenney-furniture", "bedDouble.glb")) &&
  existsSync(join(PACKS, "quaternius-characters", "Superhero_Female_FullBody.gltf")) &&
  existsSync(join(PACKS, "quaternius-animations", "UAL1_Standard.glb"));

const SMALL = {
  width: 216, height: 384, fps: 30, duration: 2,
  room: { width: 3, depth: 3, height: 2.5, openings: [{ wall: "back", type: "window", x: 0, y: 0.9, width: 1, height: 1 }] },
  lights: [{ type: "hemisphere", intensity: 0.6 }, { type: "point", position: [0.5, 2, 0.5], intensity: 4, castShadow: true }],
  objects: [
    { id: "bed", model: "kenney-furniture/bedDouble.glb", position: [0.6, 0, -0.6], scale: 1.4 },
    { id: "box", primitive: "box", params: { size: [0.3, 0.3, 0.3], color: "#cc8844" }, position: [-0.8, 0, 0.2], shakes: [{ t: 0.5, amp: 0.02 }] },
    { id: "mug", primitive: "mug", parent: "box", position: [0, 0.3, 0], attach: [{ to: "hero:hand_r", start: 0, end: 0.8 }] },
  ],
  characters: [{
    id: "hero", model: "quaternius-characters/Superhero_Female_FullBody.gltf", height: 1.6,
    animations: ["quaternius-animations/UAL1_Standard.glb"],
    path: [{ t: 0, p: [-0.5, 0.8] }, { t: 1.5, p: [0.2, -0.2] }],
    actions: [{ clip: "Walk_Loop", start: 0, end: 1.6 }, { clip: "Idle_Loop", start: 1.4 }],
    poses: [{ start: 1.2, end: 2, aim: { upperarm_l: [0, 1, 0] } }],
  }],
  camera: { keys: [{ t: 0, position: [0, 1.4, 1.3], target: [0, 0.9, -0.5], fov: 55 }, { t: 2, position: [0.2, 1.3, 1.0], target: [0, 0.9, -0.5] }] },
};

const sha = (b) => createHash("sha256").update(b).digest("hex");

test("браузер: GLB/glTF со скелетом грузятся, персонаж нужного роста, ничего не висит в воздухе", { skip: !HAVE && "нет браузера/паков/бандла" }, async () => {
  const scene = await openScene({ bundle: BUNDLE, packs: PACKS, chrome: chromeOrNull(), gl: "swiftshader" }, SMALL);
  try {
    assert.deepEqual(scene.info.warnings, []);
    const ins = await scene.inspect(1.0);
    const hero = ins.characters.hero;
    assert.ok(Math.abs(hero.max[1] - hero.min[1] - 1.6) < 0.25, `рост ${hero.max[1] - hero.min[1]}`);
    assert.ok(Math.abs(hero.min[1]) < 0.05, `ноги на полу: ${hero.min[1]}`);
    const bed = ins.objects.bed;
    assert.ok(bed.max[0] - bed.min[0] > 1, "кровать масштабирована");
    assert.ok(Math.abs(bed.min[1]) < 0.01, "кровать стоит на полу");
    // Кружка в руке до 0.8 с, потом на коробке (не ниже её верха).
    const atHand = await scene.inspect(0.3);
    const mug = atHand.objects.mug, hand = atHand.characters.hero.hand_r;
    assert.ok(Math.hypot((mug.min[0] + mug.max[0]) / 2 - hand[0], (mug.min[2] + mug.max[2]) / 2 - hand[2]) < 0.15, "кружка у руки");
    const placed = (await scene.inspect(1.8)).objects.mug;
    assert.ok(Math.abs(placed.min[1] - 0.3) < 0.02, `кружка на коробке: ${placed.min[1]}`);
    const st = await scene.render(1.0);
    assert.ok(st.luma > 10, `кадр не чёрный: ${st.luma}`);
  } finally {
    await scene.close();
  }
});

test("браузер: кадр зависит только от t — тот же t в другом порядке даёт те же байты", { skip: !HAVE && "нет браузера/паков/бандла" }, async () => {
  const opts = { bundle: BUNDLE, packs: PACKS, chrome: chromeOrNull(), gl: "swiftshader" };
  const a = await openScene(opts, SMALL);
  let first, again, other;
  try {
    await a.render(0.9);
    first = sha(await a.shot("png"));
    await a.render(1.7);
    await a.render(0.1);
    await a.render(0.6); // тряска коробки, кружка на родителе
    await a.render(0.9);
    again = sha(await a.shot("png"));
  } finally {
    await a.close();
  }
  // Отдельный браузер, сразу нужный кадр — как первый кадр куска при параллельном рендере.
  const b = await openScene(opts, SMALL);
  try {
    await b.render(0.9);
    other = sha(await b.shot("png"));
  } finally {
    await b.close();
  }
  assert.equal(again, first, "тот же браузер, другой порядок");
  assert.equal(other, first, "другой браузер");
});

test("браузер: превью-кадры пишутся PNG", { skip: !HAVE && "нет браузера/паков/бандла" }, async () => {
  const { renderPreviews } = await import("./render.mjs");
  const dir = join(tmpdir(), `scene3d-test-${process.pid}`);
  mkdirSync(dir, { recursive: true });
  const spec = join(dir, "s.json");
  writeFileSync(spec, JSON.stringify(SMALL));
  const res = await renderPreviews({ bundle: BUNDLE, packs: PACKS, chrome: chromeOrNull(), gl: "swiftshader", previews: [0, 1.5], previewDir: dir, spec }, SMALL);
  assert.equal(res.previews.length, 2);
  for (const p of res.previews) assert.ok(existsSync(p.file) && p.luma > 10);
});
