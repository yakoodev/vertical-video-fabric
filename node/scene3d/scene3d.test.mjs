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
import { applyPostMode, chromeArgs, concatArgs, findChrome, openScene, parseArgs, safeJoin, segmentArgs, serve, splitFrames } from "./render.mjs";

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

test("раздача файлов: встроенные модели движка (/assets/) — только из node/scene3d/assets", () => {
  const ctx = { width: 100, height: 200, bundle: BUNDLE, packs: PACKS };
  const hero = serve("http://scene3d.local/assets/hero_raincoat.glb", ctx);
  assert.equal(hero.status, 200);
  assert.equal(hero.mime, "model/gltf-binary");
  assert.equal(serve("http://scene3d.local/assets/../render.mjs", ctx).status, 404);
  assert.equal(serve("http://scene3d.local/assets/%2e%2e/render.mjs", ctx).status, 404);
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

test("браузер: блокинг — модель вписана в габарит, поза из клипа на сиденье, голова в кадре, открытая декорация", { skip: !HAVE && "нет браузера/паков/бандла" }, async () => {
  const spec = {
    width: 216, height: 384, fps: 30, duration: 1,
    environment: { style: "flat", fog: false, toneMapping: "neutral" },
    room: { width: 3, depth: 3, height: 2.5, open: ["front"], ceiling: false, extend: 6,
      openings: [{ wall: "back", type: "window", x: 0, y: 0.9, width: 1, height: 1, curtains: { color: "#efe2cf" } }] },
    lights: [{ type: "hemisphere", intensity: 1 }],
    objects: [
      { id: "bed", model: "kenney-furniture/bedDouble.glb", fit: [1.4, 0.5, 2.0], position: [0.6, 0, -0.4] },
      { id: "bot", primitive: "robot_disc", params: { radius: 0.14, screen: "rim", stripe: "#8fd3c1", antenna: "#ff3b30", face: "innocent" }, position: [-0.8, 0, 0.3] },
    ],
    characters: [
      { id: "sit", model: "quaternius-characters/Superhero_Female_FullBody.gltf", height: 1.6, style: "flat", seat: 0.46,
        hair: { model: "quaternius-characters/Hair_Long.gltf", color: "#8fd3c1" }, glasses: { color: "#333333" },
        outfit: { shirt: "#b8a9f0", shorts: "#9a9a9a", fit: "loose" }, animations: ["quaternius-animations/UAL1_Standard.glb"],
        position: [-0.3, 0, 0.2], yaw: 0, actions: [{ clip: "Sitting_Idle_Loop", start: 0, speed: 0, offset: 0.5 }] },
    ],
    // Камера вплотную к голове и за передней стеной — clamp выключен, голову держит keepInFrame.
    camera: { clamp: false, keepInFrame: { ids: ["sit"], margin: 0.06 }, keys: [{ t: 0, position: [-0.3, 1.1, 0.75], target: [-0.3, 1.0, 0], fov: 40 }] },
  };
  const scene = await openScene({ bundle: BUNDLE, packs: PACKS, chrome: chromeOrNull(), gl: "swiftshader" }, spec);
  try {
    const ins = await scene.inspect(0);
    const bed = ins.objects.bed;
    assert.ok(Math.abs(bed.max[0] - bed.min[0] - 1.4) < 0.02 && Math.abs(bed.max[1] - bed.min[1] - 0.5) < 0.02 &&
      Math.abs(bed.max[2] - bed.min[2] - 2.0) < 0.02, `габарит кровати ${JSON.stringify(bed)}`);
    const sit = ins.characters.sit;
    assert.ok(sit.max[1] < 1.45, `сидит (верх ${sit.max[1]})`);
    assert.ok(sit.min[1] > -0.05 && sit.min[1] < 0.1, `ступни на полу (${sit.min[1]})`);
    // Камера отъехала назад: голова целиком в кадре.
    assert.ok(ins.camera[2] > 0.8, `камера отъехала: ${ins.camera}`);
    assert.ok((await scene.warnings()).includes("camera_pulled_back"));
    const st = await scene.render(0);
    assert.ok(st.luma > 30);
  } finally {
    await scene.close();
  }
});

test("браузер: follow-камера — за плечом на высоте роста, смотрит в точку действия, герой сбоку", { skip: !HAVE && "нет браузера/паков/бандла" }, async () => {
  const look = [0.6, 0.45, -0.6];
  const spec = {
    ...SMALL,
    // Комната 3×3 м: дистанция явная, чтобы камеру не прижало к стене.
    camera: { keys: [{ t: 0, follow: "hero", look, fov: 55, distance: 1.1 }, { t: 2, follow: "hero", look: "bed", lookOffset: [0, 0.4, 0], height: 1.3, fov: 55, distance: 1.1 }] },
  };
  const scene = await openScene({ bundle: BUNDLE, packs: PACKS, chrome: chromeOrNull(), gl: "swiftshader" }, spec);
  try {
    for (const t of [0.4, 1.6]) {
      const ins = await scene.inspect(t);
      const h = ins.characters.hero;
      const cam = ins.camera, dir = ins.cameraDir;
      // Высота: рост 1.6 × [0.95; 1.05] (на 2 с просили 1.3 — движок прижимает к 1.05).
      assert.ok(cam[1] / 1.6 >= 0.949 && cam[1] / 1.6 <= 1.051, `высота камеры ${cam[1]}`);
      const hero = [(h.min[0] + h.max[0]) / 2, (h.min[2] + h.max[2]) / 2];
      const view = Math.atan2(dir[0], dir[2]);
      const toHero = Math.atan2(hero[0] - cam[0], hero[1] - cam[2]);
      const off = ((toHero - view + 3 * Math.PI) % (2 * Math.PI)) - Math.PI;
      // За правым плечом: герой левее центра кадра (положительный угол — влево), но в кадре.
      assert.ok(off > 0.06 && off < 0.3, `герой сбоку: ${off.toFixed(3)} рад`);
      // Камера позади героя относительно точки действия.
      const bed = ins.objects.bed;
      const target = t < 1 ? [look[0], look[2]] : [(bed.min[0] + bed.max[0]) / 2, (bed.min[2] + bed.max[2]) / 2];
      assert.ok(Math.hypot(target[0] - cam[0], target[1] - cam[2]) > Math.hypot(target[0] - hero[0], target[1] - hero[1]),
        "камера за спиной, действие впереди");
    }
    // Взгляд — в точку действия, не в центр тела.
    const ins = await scene.inspect(0);
    const d = ins.cameraDir, cam = ins.camera;
    const ang = Math.abs(Math.atan2(d[0], d[2]) - Math.atan2(look[0] - cam[0], look[2] - cam[2]));
    assert.ok(ang < 0.02, `смотрит в точку действия (${ang})`);
  } finally {
    await scene.close();
  }
});

test("браузер: лоу-поли серия — игрушка на костях UAL, срез стены по камере, табло и кнопки, общий план сверху", { skip: !HAVE && "нет браузера/паков/бандла" }, async () => {
  const spec = {
    width: 216, height: 384, fps: 30, duration: 2,
    environment: { style: "lowpoly", background: "#06070a", fog: { color: "#07080c", near: 3, far: 11 } },
    room: { width: 2, depth: 2.2, height: 2.5, style: "lowpoly", cutaway: true, baseboard: false, hallway: false },
    lights: [{ type: "hemisphere", intensity: 0.6 }, { type: "spot", id: "main", position: [0, 2.4, 0], target: [0, 0, 0], intensity: 30,
      castShadow: true, keys: [{ t: 0, intensity: 30 }, { t: 1, intensity: 30 }, { t: 1.04, intensity: 0 }] }],
    objects: [
      { id: "rail", primitive: "cylinder", wall: "back", params: { size: [0.04, 1.6], flat: true }, position: [0, 0.9, -1.04], rotation: [0, 0, 90] },
      { id: "display", primitive: "display", wall: "front", params: { text: "22" }, position: [0, 2.2, 1.08], rotation: [0, 180, 0],
        state: [{ t: 0, text: "22", glow: 1 }, { t: 1.5, text: "23" }] },
      { id: "panel", primitive: "button_panel", wall: "right", params: { rows: 6, cols: 2 }, position: [0.98, 1, 0.6], rotation: [0, -90, 0],
        state: [{ t: 0, glow: 1 }, { t: 1, glow: 0, alarm: true }] },
      { id: "lamp", primitive: "rounded_box", hideAbove: 2.55, params: { size: [1, 0.03, 0.6], radius: 0.01, emissive: "#fff3dc" }, position: [0, 2.46, 0] },
    ],
    characters: [{
      id: "hero", model: "quaternius-characters/Superhero_Male_FullBody.gltf", height: 1.72, style: "toy",
      toy: { top: "#f2c21b", coat: true, hood: true, backpack: "#5d6b3a", hair: "#a8452a" },
      animations: ["quaternius-animations/UAL1_Standard.glb", "quaternius-animations/UAL2_Standard.glb"],
      position: [0, 0, 0.3], yaw: 0, actions: [{ clip: "Idle_Lantern_Loop", start: 0 }],
    }],
    // 0–1 с — за задней стеной (стена срезана), 1–2 с — общий план сверху (потолок и лампа срезаны).
    camera: { clamp: false, fill: { intensity: 0.8 }, keys: [
      { t: 0, position: [0.3, 1.65, -1.7], target: [0, 1.2, 1.0], fov: 52 },
      { t: 1, cut: true, free: true, position: [0, 4.6, 0.7], target: [0, 0.5, 0.3], fov: 50 },
      { t: 2, free: true, position: [0, 4.4, 0.7], target: [0, 0.5, 0.3], fov: 50 }] },
  };
  const scene = await openScene({ bundle: BUNDLE, packs: PACKS, chrome: chromeOrNull(), gl: "swiftshader" }, spec);
  try {
    assert.deepEqual(scene.info.warnings, []);
    const back = await scene.inspect(0.5);
    const h = back.characters.hero;
    assert.ok(Math.abs(h.max[1] - h.min[1] - 1.72) < 0.3, `рост игрушки ${h.max[1] - h.min[1]}`);
    assert.ok(Math.abs(h.min[1]) < 0.06, `стоит на полу: ${h.min[1]}`);
    assert.ok(back.camera[2] < -1.1, "камера за задней стеной (clamp выключен)");
    assert.ok((await scene.render(0.5)).luma > 8, "за срезанной стеной — комната, а не заливка стены");
    const top = await scene.inspect(1.5);
    assert.ok(top.camera[1] > 4, "кадр free — камеру не прижало под потолок");
    assert.ok((await scene.render(1.5)).luma > 3, "сверху видно кабину без потолка");
  } finally {
    await scene.close();
  }
});

/** «Кино»: PBR-поверхности, живые отражения, лучи и ореолы, постобработка, сгенерированный герой на скелете. */
const CINEMA = {
  width: 216, height: 384, fps: 10, duration: 2,
  environment: { background: "#040507", style: "cinematic", reflections: { position: [0, 1.3, 0], size: 32 },
    post: { ao: 1, aoScale: 0.5, bloom: { strength: 0.5, threshold: 1.2 }, grade: { shadows: "#3f6f78", highlights: "#c9a27a", contrast: 1.1 },
      vignette: 0.5, grain: 0.05, ca: 1.4, dof: true } },
  room: { width: 2, depth: 2.2, height: 2.5, style: "lowpoly", cutaway: true, baseboard: false, hallway: false,
    floor: { surface: { kind: "coin_rubber" } }, walls: { surface: { kind: "steel_panel" } }, ceiling: { surface: { kind: "painted_metal" } } },
  lights: [
    { type: "hemisphere", color: "#4a6a78", intensity: 0.4 },
    { type: "spot", id: "main", intensity: 20, position: [0, 2.4, 0], target: [0, 0, 0], angle: 60, castShadow: true, beam: { length: 2.3, opacity: 0.05 } },
    { type: "point", id: "emergency", color: "#ff2a14", intensity: 2, position: [-0.8, 2.2, -1], halo: { size: 0.4 },
      keys: [{ t: 0, intensity: 0 }, { t: 1, intensity: 2 }] },
  ],
  objects: [{ id: "door", primitive: "box", params: { size: [0.5, 2, 0.04], surface: { kind: "brushed_steel" } }, position: [0, 0, 1.05] }],
  characters: [{ id: "hero", model: "quaternius-characters/Superhero_Male_FullBody.gltf", height: 1.72, style: "mesh",
    mesh: { model: "builtin/hero_raincoat.glb", gloss: { hue: [0.105, 0.2] }, joints: {
      pelvis: [-0.08, -0.4, 0], spine_01: [-0.08, -0.28, 0], spine_02: [-0.08, -0.08, 0], spine_03: [-0.08, 0.1, 0],
      neck_01: [-0.08, 0.32, 0], Head: [-0.08, 0.43, 0], top: [-0.08, 0.96, 0],
      upperarm_l: [0.19, 0.17, 0], lowerarm_l: [0.45, -0.05, 0], hand_l: [0.5, -0.2, 0],
      upperarm_r: [-0.29, 0.17, 0], lowerarm_r: [-0.5, -0.05, 0], hand_r: [-0.57, -0.2, 0],
      thigh_l: [0.09, -0.43, 0], calf_l: [0.1, -0.7, 0], foot_l: [0.11, -0.91, 0], ball_l: [0.11, -0.97, 0.12],
      thigh_r: [-0.25, -0.43, 0], calf_r: [-0.26, -0.7, 0], foot_r: [-0.27, -0.91, 0], ball_r: [-0.27, -0.97, 0.12],
      armpit_l: [0.355, 0.1, 0], hem_l: [0.36, -0.45, 0], armpit_r: [-0.31, 0.1, 0], hem_r: [-0.4, -0.45, 0] } },
    animations: ["quaternius-animations/UAL1_Standard.glb"], actions: [{ clip: "Idle_Loop", start: 0, end: 3 }],
    position: [0, 0, 0] }],
  camera: { fov: 45, clamp: false, keys: [{ t: 0, position: [0.3, 1.4, -0.9], target: [0, 1.1, 0.6], dof: 1 }, { t: 2, position: [0.2, 1.4, -0.8], target: [0, 1.1, 0.6], dof: 1 }],
    keepInFrame: { ids: ["hero"] } },
};

test("браузер: «кино» — пост-обработка, отражения, лучи и сгенерированный герой детерминированы, герой на ногах", { skip: !HAVE && "нет браузера/паков/бандла" }, async () => {
  const opts = { bundle: BUNDLE, packs: PACKS, chrome: chromeOrNull(), gl: "swiftshader" };
  const a = await openScene(opts, CINEMA);
  let first, again, other, info, lumaLit, lumaDark;
  try {
    assert.deepEqual(a.info.warnings, []);
    lumaDark = (await a.render(0.05)).luma;
    await a.render(1.5);
    first = sha(await a.shot("png"));
    lumaLit = (await a.render(1.9)).luma;
    await a.render(0.4);
    await a.render(1.5);
    again = sha(await a.shot("png"));
    info = await a.inspect(1.5);
  } finally {
    await a.close();
  }
  const b = await openScene(opts, CINEMA);
  try {
    await b.render(1.5);
    other = sha(await b.shot("png"));
  } finally {
    await b.close();
  }
  assert.equal(again, first, "тот же браузер, другой порядок");
  assert.equal(other, first, "другой браузер");
  assert.ok(lumaLit > 8 && lumaDark > 3, `кадр не чёрный (${lumaDark} / ${lumaLit})`);
  const hero = info.characters.hero;
  // Меш на скелете: голова (с причёской) — верх фигуры, рост ~1.72 м, кисти ниже плеч в покое.
  assert.ok(hero.headCenter[1] > 1.3 && hero.headCenter[1] < 1.6, `центр головы ${hero.headCenter}`);
  assert.ok(Math.abs(hero.headR - 0.26) < 0.02, `радиус головы ${hero.headR}`);
  assert.ok(hero.hand_r[1] < 1.0 && hero.hand_l[1] < 1.0, "руки опущены");
});

test("постобработка: GPU — полная, SwiftShader — облегчённая (без AO и DOF), off — без неё", () => {
  assert.equal(parseArgs(["--spec", "s.json", "--out", "o.mp4", "--gl", "gpu"]).post, "full");
  assert.equal(parseArgs(["--spec", "s.json", "--out", "o.mp4", "--gl", "swiftshader"]).post, "lite");
  assert.throws(() => parseArgs(["--spec", "s.json", "--out", "o.mp4", "--post", "max"]));
  const spec = { environment: { post: { ao: 1, dof: true, grain: 0.05 } } };
  assert.deepEqual(applyPostMode(spec, "lite").environment.post, { ao: 0, dof: false, grain: 0.05 });
  assert.equal(applyPostMode(spec, "off").environment.post, undefined);
  assert.equal(applyPostMode(spec, "full"), spec);
  assert.equal(spec.environment.post.ao, 1, "исходная спека не меняется");
});
