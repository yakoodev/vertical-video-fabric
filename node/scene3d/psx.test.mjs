/**
 * Стиль «Ретро PS1» v2 (src/psx.ts): шаг времени, деление больших треугольников без щелей, параметры стиля в
 * развёртке пресетов; в браузере (если есть) — кадр с новыми опциями зависит только от t, внутри ступени
 * animFps кадры одинаковые, между ступенями — разные.
 *
 *   npm run build:scene3d && node --test node/scene3d/psx.test.mjs
 */
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import * as THREE from "three";

import { sceneTimes, stepTime, subdivideGeometry } from "./src/psx.ts";
import { LOOKS } from "./src/presets/looks.ts";
import { expandPresets } from "./src/presets/index.ts";
import { findChrome, openScene } from "./render.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const BUNDLE = join(HERE, "dist", "scene3d.js");

test("шаг времени: кадры i/30 встают на ступени точно, 0 — плавно", () => {
  assert.equal(stepTime(1.2345, 0), 1.2345);
  assert.equal(stepTime(1.2345, undefined), 1.2345);
  for (let i = 0; i < 300; i++) {
    const t = i / 30;
    assert.equal(stepTime(t, 15), Math.floor(i / 2) / 15, `кадр ${i}`);
    assert.equal(stepTime(t, 30), i / 30, `кадр ${i}`);
  }
  assert.deepEqual(sceneTimes({ matstyle: { kind: "psx", animFps: 12, camFps: 0 } }, 0.5), { anim: 0.5, cam: 0.5 });
  assert.deepEqual(sceneTimes({ matstyle: { kind: "psx", animFps: 12, camFps: 6 } }, 0.55), { anim: 6 / 12, cam: 3 / 6 });
  assert.deepEqual(sceneTimes({}, 0.55), { anim: 0.55, cam: 0.55 });
});

const edges = (g, i) => {
  const p = g.attributes.position;
  const v = [0, 1, 2].map((c) => new THREE.Vector3().fromBufferAttribute(p, i * 3 + c));
  return [v[0].distanceTo(v[1]), v[1].distanceTo(v[2]), v[2].distanceTo(v[0])];
};
const area = (g) => {
  const p = g.attributes.position;
  let s = 0;
  for (let i = 0; i < p.count / 3; i++) {
    const [a, b, c] = [0, 1, 2].map((k) => new THREE.Vector3().fromBufferAttribute(p, i * 3 + k));
    s += b.clone().sub(a).cross(c.clone().sub(a)).length() / 2;
  }
  return s;
};

test("деление: рёбра не длиннее предела, площадь, нормали и uv сохранены, мелкое не трогается", () => {
  const plane = new THREE.PlaneGeometry(4, 3);
  const g = subdivideGeometry(plane, 1, new THREE.Vector3(1, 1, 1));
  assert.ok(g, "плоскость 4×3 м делится");
  const tris = g.attributes.position.count / 3;
  assert.ok(tris > 20 && tris < 400, `треугольников ${tris}`);
  for (let i = 0; i < tris; i++) for (const e of edges(g, i)) assert.ok(e <= 1.0001, `ребро ${e}`);
  assert.ok(Math.abs(area(g) - 12) < 1e-4, `площадь ${area(g)}`);
  const n = g.attributes.normal, uv = g.attributes.uv;
  for (let i = 0; i < n.count; i++) {
    assert.ok(Math.abs(n.getZ(i) - 1) < 1e-6);
    assert.ok(uv.getX(i) >= -1e-6 && uv.getX(i) <= 1 + 1e-6 && uv.getY(i) >= -1e-6 && uv.getY(i) <= 1 + 1e-6);
    // uv — та же линейная функция положения, что у исходной плоскости.
    assert.ok(Math.abs(uv.getX(i) - (g.attributes.position.getX(i) + 2) / 4) < 1e-5);
  }
  // Масштаб меша учитывается: та же плоскость с масштабом 0.2 — уже мелкая.
  assert.equal(subdivideGeometry(plane, 1, new THREE.Vector3(0.2, 0.2, 0.2)), null);
  assert.equal(subdivideGeometry(new THREE.BoxGeometry(0.5, 0.5, 0.5), 1, new THREE.Vector3(1, 1, 1)), null);
  assert.equal(subdivideGeometry(plane, 0, new THREE.Vector3(1, 1, 1)), null);
});

test("деление: без Т-стыков (общее ребро у соседей делится одинаково), группы материалов сохранены", () => {
  const box = new THREE.BoxGeometry(3, 2, 1.2);
  const g = subdivideGeometry(box, 0.7, new THREE.Vector3(1, 1, 1));
  assert.equal(g.groups.length, 6);
  assert.equal(g.groups.reduce((s, x) => s + x.count, 0), g.attributes.position.count);
  // Т-стык — вершина посреди чужого ребра: на сетке snap он даёт щель. На каждой грани их нет.
  const p = g.attributes.position, V = (i) => new THREE.Vector3().fromBufferAttribute(p, i);
  let checked = 0;
  for (const gr of g.groups) {
    const verts = [];
    for (let i = gr.start; i < gr.start + gr.count; i++) if (!verts.some((v) => v.distanceTo(V(i)) < 1e-6)) verts.push(V(i));
    for (let i = gr.start; i < gr.start + gr.count; i += 3) {
      for (const [x, y] of [[i, i + 1], [i + 1, i + 2], [i + 2, i]]) {
        const P = V(x), Q = V(y), L = P.distanceTo(Q);
        if (L < 1e-9) continue;
        for (const v of verts) {
          const dp = v.distanceTo(P), dq = v.distanceTo(Q);
          if (dp < 1e-5 || dq < 1e-5) continue;
          assert.ok(Math.abs(dp + dq - L) > 1e-6, `Т-стык в ${v.toArray()} на ребре ${P.toArray()}–${Q.toArray()}`);
          checked++;
        }
      }
    }
  }
  assert.ok(checked > 1000);
});

test("деление: узкая длинная полоса (наклейка на полу) — без вывернутых треугольников и дыр", () => {
  const strip = new THREE.BoxGeometry(20, 0.008, 0.3);
  const g = subdivideGeometry(strip, 1, new THREE.Vector3(1, 1, 1));
  const p = g.attributes.position, n = g.attributes.normal;
  for (let i = 0; i < p.count; i += 3) {
    const [a, b, c] = [0, 1, 2].map((k) => new THREE.Vector3().fromBufferAttribute(p, i + k));
    const f = b.clone().sub(a).cross(c.clone().sub(a));
    assert.ok(f.length() > 0, "вырожденный треугольник");
    assert.ok(f.normalize().dot(new THREE.Vector3().fromBufferAttribute(n, i)) > 0.99, `треугольник ${i / 3} вывернут`);
  }
  assert.ok(Math.abs(area(g) - area(strip.toNonIndexed())) < 1e-6);
});

test("пресет psx: параметры идут в matstyle/stylize, реестр не меняется, старый вид — нулями", () => {
  const env = (look) => expandPresets({ duration: 2, presets: { look } }).environment;
  const def = env("psx");
  assert.equal(def.matstyle.kind, "psx");
  for (const k of ["snap", "affine", "subdivide", "jitter", "texRes", "animFps", "camFps"]) assert.equal(typeof def.matstyle[k], "number", k);
  assert.equal(def.matstyle.animFps, 15);
  assert.equal(def.stylize.posterize, 32);
  const tuned = env({ name: "psx", affine: 0.5, animFps: 12, camFps: 12, pixel: 6, snap: 0 });
  assert.equal(tuned.matstyle.affine, 0.5);
  assert.equal(tuned.matstyle.animFps, 12);
  assert.equal(tuned.matstyle.camFps, 12);
  assert.equal(tuned.matstyle.snap, 0);
  assert.equal(tuned.stylize.pixel, 6);
  assert.equal(LOOKS.psx.env.matstyle.affine, 1, "реестр не тронут");
  assert.equal(env({ name: "psx", affine: 7 }).matstyle.affine, 1, "пределы параметра");
  // Остальные стили параметров matstyle не получают.
  assert.equal(env("clay").matstyle.animFps, undefined);
});

let HAVE = false;
try {
  findChrome(process.env.SCENE3D_CHROMIUM || "");
  HAVE = existsSync(BUNDLE);
} catch {
  HAVE = false;
}

test("браузер: psx v2 — кадр зависит только от t, внутри ступени animFps кадры равны", { skip: !HAVE && "нет браузера/бандла" }, async () => {
  const opts = { bundle: BUNDLE, packs: join(HERE, "..", "..", "vendor", "packs"), gl: process.env.SCENE3D_GL || "swiftshader", post: "full", chrome: process.env.SCENE3D_CHROMIUM || "" };
  const spec = { width: 216, height: 384, fps: 30, duration: 3, characters: [{ id: "hero", style: "voxel", voxel: { shape: "soft" }, at: "hero" }],
    presets: { room: "kitchen_night", light: "moonlight", look: { name: "psx", subdivide: 0.8, jitter: 0.02 },
      camera: [{ move: "static", target: "hero:chest", distance: 3.5 }], performance: [{ who: "hero", preset: "wave", t: 0.2 }] } };
  const scene = await openScene(opts, spec);
  const hash = async (t) => {
    await scene.render(t);
    return createHash("sha1").update(await scene.shot("png")).digest("hex");
  };
  try {
    const a = await hash(1.0);
    await hash(0.3);
    assert.equal(await hash(1.0), a, "кадр t=1 зависит от прошлых кадров");
    // 15 к/с: кадры 30 и 31 (t = 1.0 и 1.0333) — одна ступень; камера стоит, эффекты кадра от t в psx выключены.
    assert.equal(await hash(31 / 30), a, "внутри ступени кадр меняется");
    assert.notEqual(await hash(1.2), a, "между ступенями жест не двигается");
    const warn = (await scene.warnings()).filter((w) => !/camera_|библиотека моделей недоступна/.test(w));
    assert.deepEqual(warn, []);
  } finally {
    await scene.close();
  }
});
