/**
 * Библиотека 3D-моделей вещей (src/models3d.ts, docs/3d/models.md): проверка манифеста, засеянный выбор варианта,
 * окружения ставят модели с процедурной заменой, и в браузере — замена, когда файла модели нет.
 *
 *   npm run test:scene3d   (или node --test node/scene3d/models3d.test.mjs после сборки бандла)
 */
import assert from "node:assert/strict";
import { existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { hashSeed, isLibraryRef, libraryCatalog, pickModel, validateManifest } from "./src/models3d.ts";
import { buildRoomPreset } from "./src/presets/rooms.ts";
import { findChrome, openScene, serve } from "./render.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const BUNDLE = join(HERE, "dist", "scene3d.js");

function entry(id, extra = {}) {
  const [category, slug] = id.split("/");
  return {
    id, category, slug, file: `${category}/${slug}.glb`, title: id, size: [0.4, 0.03, 0.14], tris: 100, bytes: 1000,
    license: "CC0-1.0", license_url: "https://creativecommons.org/publicdomain/zero/1.0/", author: "x",
    source: { kind: "poly.pizza", url: `https://poly.pizza/m/${slug}` }, attribution: "", tags: [], weight: 1, ...extra,
  };
}

function lib(models) {
  const categories = {};
  for (const m of models) (categories[m.category] ||= []).push(m.id);
  return { version: 1, categories, models: Object.fromEntries(models.map((m) => [m.id, m])) };
}

const LIB = lib([
  entry("keyboard/a", { tags: ["dark"] }), entry("keyboard/b", { tags: ["light"] }), entry("keyboard/c", { tags: ["dark", "mechanical"] }),
  entry("keyboard/off", { weight: 0 }), entry("plant/desk1", { tags: ["desk"] }), entry("plant/floor1", { tags: ["floor"] }),
]);

test("ссылки: библиотека — «категория» или «категория/slug» без расширения, паки — с расширением", () => {
  for (const ok of ["keyboard", "keyboard/mech_tkl_dark", "plant/small_pot"]) assert.ok(isLibraryRef(ok), ok);
  for (const no of ["kenney-furniture/desk.glb", "builtin/hero_raincoat.glb", "Keyboard", "a/b/c", "", undefined, "../x"]) assert.ok(!isLibraryRef(no), String(no));
});

test("манифест: годный проходит, ошибки называются по-русски", () => {
  assert.deepEqual(validateManifest(LIB), []);
  const bad = structuredClone(LIB);
  bad.models["keyboard/a"].license = "editorial";
  bad.models["keyboard/b"].file = "keyboard/other.glb";
  bad.models["keyboard/c"].license = "CC-BY-3.0";
  bad.models["keyboard/c"].attribution = "";
  bad.models["plant/desk1"].size = [0, 1, 1];
  bad.categories.plant.push("plant/ghost");
  const problems = validateManifest(bad);
  assert.ok(problems.some((p) => p.includes("keyboard/a") && p.includes("лицензия")), problems.join("\n"));
  assert.ok(problems.some((p) => p.includes("keyboard/b") && p.includes("file")));
  assert.ok(problems.some((p) => p.includes("keyboard/c") && p.includes("атрибуц")));
  assert.ok(problems.some((p) => p.includes("plant/desk1") && p.includes("size")));
  assert.ok(problems.some((p) => p.includes("plant/ghost")));
  assert.ok(validateManifest({ version: 2 }).length > 0);
});

test("манифест библиотеки на диске (если есть) — годный, у каждой модели файл", { skip: !process.env.SCENE3D_MODELS_DIR && "SCENE3D_MODELS_DIR не задан" }, () => {
  const dir = process.env.SCENE3D_MODELS_DIR;
  const man = JSON.parse(readFileSync(join(dir, "manifest.json"), "utf-8"));
  assert.deepEqual(validateManifest(man), []);
  for (const m of Object.values(man.models)) assert.ok(existsSync(join(dir, m.file)), m.file);
  // Клавиатур — не меньше трёх вариантов, у частых вещей — от двух.
  for (const [cat, n] of Object.entries({ keyboard: 3, monitor: 2, laptop: 2, desk: 2, chair: 2, mug: 2, lamp: 2, plant: 3, headphones: 2, mouse: 2, books: 2, phone: 2 })) {
    assert.ok((man.categories[cat] || []).length >= n, `${cat}: вариантов меньше ${n}`);
  }
  assert.ok(libraryCatalog(man).length >= 12);
});

test("выбор варианта: детерминирован, зависит от засева и id, теги и вес соблюдаются", () => {
  const a1 = pickModel(LIB, "keyboard", { seed: 7, key: "kb" });
  const a2 = pickModel(LIB, "keyboard", { seed: 7, key: "kb" });
  assert.equal(a1.id, a2.id, "тот же засев — та же модель");
  const seen = new Set();
  for (let s = 0; s < 60; s++) seen.add(pickModel(LIB, "keyboard", { seed: s, key: "kb" }).id);
  assert.ok(seen.size >= 3, `разные сцены — разные варианты (${[...seen]})`);
  assert.ok(!seen.has("keyboard/off"), "вес 0 — не выбирается");
  for (let s = 0; s < 30; s++) assert.ok(pickModel(LIB, "keyboard", { seed: s, tags: ["dark"] }).tags.includes("dark"));
  assert.equal(pickModel(LIB, "plant", { seed: 3, tags: ["floor"] }).id, "plant/floor1");
  assert.equal(pickModel(LIB, "keyboard/b").id, "keyboard/b", "точный id — он");
  assert.equal(pickModel(LIB, "keyboard/off").id, "keyboard/off", "точный id выбирается и с весом 0");
  assert.equal(pickModel(LIB, "keyboard/zzz"), null);
  assert.equal(pickModel(LIB, "sofa"), null);
  assert.equal(pickModel(null, "keyboard"), null, "нет библиотеки — замена");
  assert.equal(pickModel(LIB, "keyboard", { tags: ["nope"] }), null);
  assert.equal(hashSeed("abc"), hashSeed("abc"));
  assert.notEqual(hashSeed("abc"), hashSeed("abd"));
});

test("окружения: вещи стола — модели библиотеки с процедурной заменой, засев — seed окружения", () => {
  for (const name of ["cozy_desk", "gamer_rgb"]) {
    const r = buildRoomPreset(name, { seed: 4 });
    const lib = r.objects.filter((o) => isLibraryRef(o.model));
    const cats = new Set(lib.map((o) => o.model));
    for (const c of ["keyboard", "mouse", "monitor", "desk"]) assert.ok(cats.has(c), `${name}: нет ${c}`);
    for (const o of lib) {
      assert.equal(o.variantSeed, 4, `${name}/${o.id}: засев окружения`);
      assert.ok(Array.isArray(o.fallback) && o.fallback.length || o.primitive, `${name}/${o.id}: нет замены`);
    }
    const mon = lib.find((o) => o.model === "monitor");
    assert.equal(mon.mounts[0].anchor, "screen");
    // Замена едет вместе с вещью (put): детали — в мировых координатах рядом с моделью.
    for (const o of lib) for (const p of o.fallback || []) assert.ok(Math.hypot(p.position[0] - o.position[0], p.position[2] - o.position[2]) < 1.2, `${o.id}/${p.id}`);
    const hidden = buildRoomPreset(name, { seed: 4, hide: ["kb"] });
    assert.ok(!hidden.objects.some((o) => String(o.id).startsWith("kb.")), "hide убирает и модель с заменой");
  }
});

test("раздача: /models3d/ — только manifest.json и .glb из каталога библиотеки", () => {
  const dir = mkdtempSync(join(tmpdir(), "vvf-models3d-"));
  try {
    mkdirSync(join(dir, "keyboard"));
    writeFileSync(join(dir, "manifest.json"), "{}");
    writeFileSync(join(dir, "keyboard", "a.glb"), "glTF");
    writeFileSync(join(dir, "secret.txt"), "x");
    const ctx = { width: 10, height: 10, bundle: BUNDLE, packs: dir, models: dir };
    assert.equal(serve("http://scene3d.local/models3d/manifest.json", ctx).status, 200);
    assert.equal(serve("http://scene3d.local/models3d/keyboard/a.glb", ctx).status, 200);
    assert.equal(serve("http://scene3d.local/models3d/secret.txt", ctx).status, 404);
    assert.equal(serve("http://scene3d.local/models3d/../manifest.json", ctx).status, 404);
    assert.equal(serve("http://scene3d.local/models3d/keyboard/none.glb", ctx).status, 404);
    assert.equal(serve("http://scene3d.local/models3d/manifest.json", { ...ctx, models: null }).status, 404);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

/** Минимальный GLB: один треугольник 0.4×0.03 (без материала) — модель «есть». */
function tinyGlb() {
  const pos = new Float32Array([-0.2, 0, 0, 0.2, 0, 0, 0, 0.03, 0.07]);
  const bin = Buffer.from(pos.buffer);
  const json = { asset: { version: "2.0" }, scene: 0, scenes: [{ nodes: [0] }], nodes: [{ mesh: 0 }],
    meshes: [{ primitives: [{ attributes: { POSITION: 0 } }] }],
    accessors: [{ bufferView: 0, componentType: 5126, count: 3, type: "VEC3", min: [-0.2, 0, 0], max: [0.2, 0.03, 0.07] }],
    bufferViews: [{ buffer: 0, byteLength: bin.length }], buffers: [{ byteLength: bin.length }] };
  let js = Buffer.from(JSON.stringify(json));
  js = Buffer.concat([js, Buffer.alloc((4 - (js.length % 4)) % 4, 0x20)]);
  const head = Buffer.alloc(12);
  head.writeUInt32LE(0x46546c67, 0); head.writeUInt32LE(2, 4); head.writeUInt32LE(12 + 8 + js.length + 8 + bin.length, 8);
  const ch = (len, type) => { const b = Buffer.alloc(8); b.writeUInt32LE(len, 0); b.writeUInt32LE(type, 4); return b; };
  return Buffer.concat([head, ch(js.length, 0x4e4f534a), js, ch(bin.length, 0x004e4942), bin]);
}

function chromeOrNull() {
  try { return findChrome(process.env.SCENE3D_CHROMIUM || ""); } catch { return null; }
}

test("браузер: модель из библиотеки грузится, нет файла/модели — процедурная замена, models.off — всё заменой", { skip: !(chromeOrNull() && existsSync(BUNDLE)) && "нет браузера/бандла" }, async () => {
  const dir = mkdtempSync(join(tmpdir(), "vvf-models3d-"));
  try {
    mkdirSync(join(dir, "thing"));
    writeFileSync(join(dir, "thing", "ok.glb"), tinyGlb());
    const man = lib([entry("thing/ok", { size: [0.4, 0.03, 0.07] }), entry("thing/missing")]);
    writeFileSync(join(dir, "manifest.json"), JSON.stringify(man));
    const spec = {
      width: 64, height: 64, duration: 1, environment: { fog: false },
      lights: [{ type: "ambient", intensity: 1 }],
      objects: [
        { id: "a", model: "thing/ok", position: [0, 0, 0], fitMax: [0.8, 0, 0], primitive: "box", params: { size: [0.1, 0.1, 0.1] } },
        { id: "b", model: "thing/missing", position: [1, 0, 0], primitive: "box", params: { size: [0.2, 0.3, 0.2] } },
        { id: "c", model: "nothing", position: [-1, 0, 0], fallback: [{ id: "c.part", primitive: "box", position: [-1, 0, 0], params: { size: [0.25, 0.25, 0.25] } }] },
        { id: "d", model: "kenney_none/x", position: [2, 0, 0] },
      ],
      camera: { keys: [{ t: 0, position: [0, 1, 3], target: [0, 0, 0] }] },
    };
    const opts = { bundle: BUNDLE, packs: dir, models: dir, chrome: chromeOrNull(), gl: "swiftshader", post: "off" };
    const scene = await openScene(opts, spec);
    try {
      const ins = await scene.inspect(0);
      assert.equal(ins.models.a, "thing/ok");
      assert.equal(ins.models.b, "fallback", "файла нет — замена");
      assert.equal(ins.models.c, "fallback", "категории нет — детали fallback");
      const a = ins.objects.a;
      assert.ok(Math.abs(a.max[0] - a.min[0] - 0.8) < 0.01, `fitMax: ширина 0.8, а ${a.max[0] - a.min[0]}`);
      assert.ok(Math.abs(a.min[1]) < 1e-3, "основание модели — на y = 0");
      const b = ins.objects.b;
      assert.ok(Math.abs(b.max[1] - b.min[1] - 0.3) < 0.01, "замена — свой примитив объекта");
      assert.ok(ins.objects["c.part"], "деталь замены на месте");
      const warns = await scene.warnings();
      assert.ok(warns.some((w) => w.includes("thing/missing.glb") || w.includes("не загрузился")), warns.join(" | "));
    } finally {
      await scene.close();
    }
    const off = await openScene(opts, { ...spec, models: { off: true } });
    try {
      const ins = await off.inspect(0);
      assert.equal(ins.models.a, "fallback");
      assert.ok(Math.abs(ins.objects.a.max[1] - ins.objects.a.min[1] - 0.1) < 0.01);
    } finally {
      await off.close();
    }
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("лицензии: CC BY не берётся без allowAttribution — ни по категории, ни по точному id", () => {
  const lib = {
    version: 1,
    categories: { mug: ["mug/a", "mug/b"] },
    models: {
      "mug/a": { id: "mug/a", category: "mug", file: "mug/a.glb", size: [0.1, 0.1, 0.1], license: "CC-BY-3.0" },
      "mug/b": { id: "mug/b", category: "mug", file: "mug/b.glb", size: [0.1, 0.1, 0.1], license: "CC0-1.0" },
    },
  };
  for (let s = 0; s < 20; s++) assert.equal(pickModel(lib, "mug", { seed: s })?.id, "mug/b");
  assert.equal(pickModel(lib, "mug/a"), null);
  assert.equal(pickModel(lib, "mug/a", { allowAttribution: true })?.id, "mug/a");
});
