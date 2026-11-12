/**
 * 3D-пресеты: кривые камеры и световые схемы детерминированы и ведут себя как задумано, развёртка блока presets
 * в сцену (слияние, явные поля сильнее, предупреждения), окна переходов; в браузере (если есть) — по кадру на
 * категорию: сцена грузится, кадр не чёрный, кадр зависит только от t.
 *
 *   npm run build:scene3d && node --test node/scene3d/presets.test.mjs
 */
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { CAMERA_MOVES, handheldOffset, moveAt, moveKeys, orbitPoint, punchStrength, vertigoFov } from "./src/presets/cameras.ts";
import { flickerKeys, LIGHT_RIGS, lightRig } from "./src/presets/lights.ts";
import { LOOKS } from "./src/presets/looks.ts";
import { FX, MOODS } from "./src/presets/fx.ts";
import { ROOMS, buildRoomPreset } from "./src/presets/rooms.ts";
import { TRANSITIONS, transitionAt } from "./src/presets/transitions.ts";
import { EMOTIONS, GESTURES } from "./src/presets/performance.ts";
import { expandPresets, presetCatalog } from "./src/presets/index.ts";
import { findChrome, openScene } from "./render.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const BUNDLE = join(HERE, "dist", "scene3d.js");
const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
const T = [0, 1.2, 0];
const HERO_C = { id: "hero", style: "voxel", voxel: { shape: "soft" }, at: "hero" };

/* ---------- реестр ---------- */

test("реестр: в каждой категории 6–16 пресетов, у всех русское название и описание", () => {
  const cat = presetCatalog();
  for (const [k, v] of Object.entries(cat)) {
    assert.ok(v.presets.length >= 6 && v.presets.length <= 16, `${k}: ${v.presets.length}`);
    for (const p of v.presets) {
      assert.ok(/[а-яё]/i.test(p.title) && /[а-яё]/i.test(p.ru) && p.ru.length > 20, `${k}/${p.name}`);
      assert.match(p.name, /^[a-z][a-z0-9_]*$/);
    }
  }
  assert.equal(LOOKS.toon_ink.meta.status, "slot");
  assert.equal(LOOKS.toon_ink.env.toon, "ink");
});

/* ---------- камера ---------- */

test("камера: каждое движение детерминировано и непрерывно (нет скачков между кадрами, кроме рывков)", () => {
  for (const name of Object.keys(CAMERA_MOVES)) {
    const m = { move: name, t0: 0, t1: 4, target: T, heading: 0, to: [1.5, 1, 0.5] };
    const a = moveKeys(m, 30), b = moveKeys(m, 30);
    assert.deepEqual(a, b, name);
    assert.equal(a.length, 121, name);
    assert.equal(a[0].cut, true, name);
    const jumpy = ["punch_in", "crash_zoom", "whip_pan"].includes(name);
    for (let i = 1; i < a.length; i++) {
      const step = dist(a[i].position, a[i - 1].position);
      assert.ok(step < (jumpy ? 0.7 : 0.1), `${name}: скачок камеры ${step.toFixed(3)} м на кадре ${i}`);
      assert.ok(a[i].fov > 5 && a[i].fov < 110, name);
    }
  }
});

test("камера: медленный наезд приближает, отъезд удаляет, облёт держит дистанцию", () => {
  const push = moveKeys({ move: "slow_push", t0: 0, t1: 3, target: T }, 30);
  assert.ok(dist(push.at(-1).position, T) < dist(push[0].position, T) * 0.75);
  const pull = moveKeys({ move: "pull_out", t0: 0, t1: 3, target: T }, 30);
  assert.ok(dist(pull.at(-1).position, T) > dist(pull[0].position, T) * 1.6);
  const orbit = moveKeys({ move: "orbit", t0: 0, t1: 3, target: T, arc: 90 }, 30);
  const d0 = dist(orbit[0].position, T);
  for (const k of orbit) assert.ok(Math.abs(dist(k.position, T) - d0) < 1e-3);
  const a0 = Math.atan2(orbit[0].position[0], orbit[0].position[2]), a1 = Math.atan2(orbit.at(-1).position[0], orbit.at(-1).position[2]);
  assert.ok(Math.abs(Math.abs(a1 - a0) * 180 / Math.PI - 90) < 1);
});

test("камера: вертиго держит размер цели (d·tan(fov/2) постоянно), камера едет назад", () => {
  const ks = moveKeys({ move: "dolly_zoom", t0: 0, t1: 2, target: T }, 30);
  const size = (k) => dist(k.position, T) * Math.tan((k.fov * Math.PI) / 360);
  for (const k of ks) assert.ok(Math.abs(size(k) - size(ks[0])) < 0.01 * size(ks[0]), `t=${k.t}`);
  assert.ok(dist(ks.at(-1).position, T) > 2 * dist(ks[0].position, T));
  assert.ok(Math.abs(vertigoFov(40, 2, 2) - 40) < 1e-9);
});

test("камера: краш-зум сужает угол после рывка, рывок вперёд приближает с перелётом, ручная — малая и детерминированная", () => {
  const cz = moveKeys({ move: "crash_zoom", t0: 0, t1: 2, target: T, at: 0.25 }, 30);
  assert.equal(cz[5].fov, cz[0].fov);
  assert.ok(cz.at(-1).fov < cz[0].fov / 2);
  const pi = moveKeys({ move: "punch_in", t0: 0, t1: 2, target: T, at: 0.2 }, 30);
  const ds = pi.map((k) => dist(k.position, T));
  assert.ok(Math.min(...ds) < ds.at(-1) - 1e-4, "перелёт: в рывке ближе, чем в конце");
  for (let t = 0; t < 10; t += 0.37) {
    const o = handheldOffset(t, 0.025, 3);
    assert.ok(Math.hypot(...o) < 0.05);
    assert.deepEqual(o, handheldOffset(t, 0.025, 3));
  }
});

test("камера: сверху — над целью, голландский угол — завал, хлёст — к второй цели, монтаж по форме — то же смещение", () => {
  const top = moveAt({ move: "top_down", t0: 0, t1: 2, target: T }, 1);
  assert.ok(top.position[1] - T[1] > 3);
  const dutch = moveAt({ move: "dutch_tilt", t0: 0, t1: 2, target: T, roll: 18 }, 2);
  assert.ok(Math.abs(dutch.roll - 18) < 1e-6);
  const to = [2, 1, 0];
  const w = moveAt({ move: "whip_pan", t0: 0, t1: 2, target: T, to }, 2);
  const dir = [w.target[0] - w.position[0], w.target[2] - w.position[2]], need = [to[0] - w.position[0], to[2] - w.position[2]];
  assert.ok(Math.abs(Math.atan2(dir[0], dir[1]) - Math.atan2(need[0], need[1])) < 1e-3);
  const prevEnd = moveAt({ move: "static", t0: 0, t1: 1, target: T, distance: 2 }, 1);
  const B = [3, 0.8, -1];
  const mc = moveAt({ move: "match_cut", t0: 1, t1: 2, target: B, prevTarget: T }, 1.5, prevEnd);
  for (let i = 0; i < 3; i++) assert.ok(Math.abs((mc.position[i] - B[i]) - (prevEnd.position[i] - T[i])) < 1e-6);
  assert.deepEqual(orbitPoint([0, 0, 0], 0, 0, 2, 0).map((v) => Math.round(v * 1e6) / 1e6), [0, 0, 2]);
});

test("камера: рывок вперёд по умолчанию — по крупности плана, явный strength — как задан, тряска — по той же силе", () => {
  const closest = (m) => Math.min(...moveKeys({ t0: 0, t1: 2, target: T, at: 0.2, move: "punch_in", ...m }, 30).map((k) => dist(k.position, T)));
  // Средний план (умолчания движения: 2.4 м, 35°) — около 0.5: подъезжает на ~22 %, а не почти вдвое.
  assert.ok(Math.abs(punchStrength(2.4, 35) - 0.5) < 0.03);
  const mid = closest({}) / 2.4;
  assert.ok(mid > 0.7 && mid < 0.85, `средний: ${mid}`);
  // Крупное лицо (узкий угол VRM) — мягко, общий — сильнее, но не больше 0.7.
  assert.equal(punchStrength(2.6, 9.5), 0.3);
  assert.ok(punchStrength(6, 40) <= 0.7 && punchStrength(6, 40) > punchStrength(2.4, 35));
  assert.ok(closest({ distance: 2.6, fov: 9.5 }) / 2.6 > 0.82, "на крупном не влетает в лицо");
  // Старые планы с явной силой — без изменений.
  const one = closest({ strength: 1 }) / 2.4;
  assert.ok(one < 0.55, `strength 1: ${one}`);
  assert.equal(CAMERA_MOVES.punch_in.meta.params.find((p) => p.name === "strength").default, undefined);
  const sc = { characters: [{ id: "hero", style: "voxel", position: [0, 0, 0] }], duration: 2 };
  const shake = (m) => expandPresets({ ...sc, presets: { camera: [{ move: "punch_in", t0: 0, t1: 2, target: "hero", ...m }] } }).camera.shake[0].amp;
  assert.ok(shake({}) < shake({ strength: 1 }) && Math.abs(shake({ strength: 1 }) - 0.006) < 1e-9);
});

/* ---------- свет ---------- */

test("свет: схемы детерминированы, лампы правильных типов, схема поворачивается вместе с героем", () => {
  for (const name of Object.keys(LIGHT_RIGS)) {
    const a = lightRig(name, { subject: [0, 1, 0], heading: 0, duration: 4 });
    assert.deepEqual(a, lightRig(name, { subject: [0, 1, 0], heading: 0, duration: 4 }), name);
    assert.ok(a.lights.length >= 2, name);
    for (const l of a.lights) {
      assert.ok(["ambient", "hemisphere", "directional", "point", "spot"].includes(l.type), `${name}: ${l.type}`);
      assert.ok(l.intensity > 0 && l.intensity < 200, `${name}/${l.id}: ${l.intensity}`);
    }
    // Тот же свет, герой развернулся на 90°: каждая лампа повернулась вокруг героя на 90°.
    const b = lightRig(name, { subject: [0, 1, 0], heading: 90, duration: 4 });
    a.lights.forEach((l, i) => {
      if (!l.position) return;
      const p = l.position, q = b.lights[i].position;
      assert.ok(Math.abs(Math.hypot(p[0], p[2]) - Math.hypot(q[0], q[2])) < 1e-3 && Math.abs(p[1] - q[1]) < 1e-3, `${name}/${l.id}`);
    });
  }
});

test("свет: мерцание огня — в пределах [base·(1−depth), base], шаг 1/15 с; в помещении небо не рисуется", () => {
  const ks = flickerKeys(3, 10, 0.4, 5, 15);
  assert.equal(ks.length, 46);
  for (const k of ks) assert.ok(k.intensity >= 6 - 1e-6 && k.intensity <= 10 + 1e-6);
  assert.ok(new Set(ks.map((k) => k.intensity)).size > 20, "огонь живой");
  assert.ok(lightRig("golden_hour", { subject: T, heading: 0, duration: 2 }).env.sky);
  assert.equal(lightRig("golden_hour", { subject: T, heading: 0, duration: 2, indoor: true }).env.sky, undefined);
});

test("свет: костёр — у костра окружения на высоте пламени, без ореола и жгучего огонька у земли; каустика — узким пятном", () => {
  const fire = (anchors) => lightRig("campfire", { subject: T, heading: 0, duration: 2, anchors }).lights.filter((l) => l.type === "point");
  const at = fire({ fire: { pos: [0.32, 0.3, 1.05] } });
  assert.equal(at.length, 1, "только огонь, без отдельного «угля» у земли");
  assert.deepEqual(at[0].position, [0.32, 0.55, 1.05]);
  assert.ok(!at[0].halo && at[0].distance <= 6);
  const free = fire(undefined)[0];
  assert.ok(free.position[1] >= 0.5 && !free.halo && Math.abs(free.position[0]) > 0.5, "без костра — сбоку, не перед героем");
  const sc = expandPresets({ characters: [{ ...HERO_C }], duration: 2, presets: { room: "forest_clearing", light: "campfire" } });
  const rigFire = sc.lights.find((l) => l.id === "fire");
  assert.deepEqual([rigFire.position[0], rigFire.position[2]], [0.32, 1.05], "свет рига — в костре поляны");
  const uw = lightRig("underwater", { subject: T, heading: 0, duration: 2 }).lights;
  const c = uw.find((l) => l.cookie === "caustics"), h = uw.find((l) => l.type === "hemisphere");
  assert.ok(c.angle <= 34 && c.intensity > h.intensity * 60, "каустика — ключ сильнее заливки, пятно узкое (ячейки мельче)");
});

test("стили: кино, игрушка, пластилин, акварель — каждый со своим видом; параметры стиля — в последний проход", () => {
  const env = (look, p = {}) => expandPresets({ characters: [{ ...HERO_C }], duration: 2, presets: { look: { name: look, ...p } } }).environment;
  const cin = env("cinematic"), toy = env("lowpoly_toy"), clay = env("clay"), wc = env("watercolor"), anime = env("anime_soft");
  assert.ok(cin.post.dof && cin.stylize.anamorphic > 0 && cin.post.grade.split >= 0.7 && cin.toneMapping === "aces");
  assert.equal(cin.stylize.letterbox, 0);
  assert.equal(env("cinematic", { letterbox: 0.12 }).stylize.letterbox, 0.12);
  assert.ok(toy.matstyle.kind === "toy" && toy.matstyle.facets >= 2 && toy.stylize.tiltShift > 0 && !toy.post.dof);
  assert.ok(clay.matstyle.kind === "clay" && clay.matstyle.bump >= 3 && clay.matstyle.sss > 0 && clay.stylize.softness > 0);
  assert.ok(wc.stylize.watercolor && wc.stylize.wash > 0 && wc.stylize.paperGrain > 0 && wc.stylize.paperEdge > 0);
  assert.ok(!anime.stylize, "мягкое аниме — без последнего прохода");
  // Прежние «близнецы»: у anime_soft, cinematic и lowpoly_toy разные материалы и последний проход.
  const sig = (e) => JSON.stringify([e.matstyle?.kind, Object.keys(e.stylize || {}).sort(), e.toneMapping]);
  assert.equal(new Set([anime, cin, toy, clay, wc].map(sig)).size, 5);
  // Неон: тени глубокие и бирюзовые, не розовые.
  const neon = LOOKS.neon_cyber.env;
  const hex = (s) => [1, 3, 5].map((i) => parseInt(s.slice(i, i + 2), 16));
  const sh = hex(neon.post.grade.shadows);
  assert.ok(sh[2] > sh[0] && sh[1] > sh[0], "тени — к бирюзе");
  assert.ok(neon.post.grade.lift.every((v) => v <= 0), "тени не подняты");
});

/* ---------- окружения, эффекты, переходы ---------- */

test("окружения: id вещей уникальны, якорь hero есть, hide убирает вещь с деталями", () => {
  for (const name of Object.keys(ROOMS)) {
    const r = buildRoomPreset(name);
    const ids = r.objects.map((o) => o.id);
    assert.equal(new Set(ids).size, ids.length, `${name}: повтор id`);
    assert.ok(r.anchors.hero && r.anchors.focus, name);
    for (const o of r.objects) if (o.parent) assert.ok(ids.includes(o.parent), `${name}: ${o.id} → ${o.parent}`);
  }
  const full = buildRoomPreset("cozy_desk"), part = buildRoomPreset("cozy_desk", { hide: ["lamp"] });
  assert.ok(part.objects.length < full.objects.length);
  assert.ok(!part.objects.some((o) => String(o.id).split(".")[0] === "lamp"));
});

test("эффекты и настроения: все ссылки на свет и эффекты существуют", () => {
  for (const [name, m] of Object.entries(MOODS)) {
    assert.ok(LIGHT_RIGS[m.light], `${name}: ${m.light}`);
    for (const f of m.fx) assert.ok(FX[typeof f === "string" ? f : f.name], `${name}: ${JSON.stringify(f)}`);
  }
});

test("переходы: окно вокруг склейки, фаза −1..1, половинки in/out", () => {
  for (const kind of Object.keys(TRANSITIONS)) {
    const d = TRANSITIONS[kind].duration;
    const at = transitionAt([{ kind, t: 2 }], 2);
    if (d === 0) { assert.equal(at, null); continue; }
    assert.equal(at.phase, 0, kind);
    assert.equal(transitionAt([{ kind, t: 2 }], 2 - d), null);
    assert.ok(Math.abs(transitionAt([{ kind, t: 2 }], 2 - d / 4).phase + 0.5) < 1e-9);
  }
  assert.equal(transitionAt([{ kind: "glitch", t: 2, half: "out" }], 2.1), null);
  assert.ok(transitionAt([{ kind: "glitch", t: 2, half: "in" }], 2.1));
  assert.equal(transitionAt([{ kind: "glitch", t: 2, half: "in" }], 1.9), null);
});

/* ---------- развёртка ---------- */

const HERO = { id: "hero", style: "voxel", voxel: { shape: "soft" }, at: "hero" };

test("развёртка: комната, свет, стиль, эффекты, камера, переходы и игра — в поля сцены; явное сильнее", () => {
  const spec = {
    duration: 4, characters: [HERO], lights: [{ type: "point", id: "mine", intensity: 1 }], environment: { exposure: 2 },
    presets: { room: "cozy_desk", light: "golden_hour", look: "clay", fx: ["rain", "god_rays"], mood: "night_rain",
      camera: [{ move: "slow_push", t0: 0, t1: 2, target: "hero" }, { move: "crash_zoom", t0: 2, t1: 4, target: "hero" }],
      transitions: [{ kind: "whip", t: 2 }], performance: [{ who: "hero", preset: "shocked", t: 1 }] },
  };
  const a = expandPresets(spec);
  assert.deepEqual(a, expandPresets(spec), "детерминированно");
  assert.equal(spec.presets.look, "clay", "исходная спека не меняется");
  assert.equal(a.presets, undefined);
  assert.ok(a.room && a.objects.length > 20);
  assert.equal(a.lights.at(-1).id, "mine", "свои лампы — после пресетных");
  assert.ok(a.lights.some((l) => l.id === "sun"));
  assert.equal(a.environment.exposure, 2, "явное environment сильнее");
  assert.equal(a.environment.matstyle.kind, "clay");
  assert.ok(a.environment.post.dof && a.environment.post.ao > 1);
  assert.ok(a.environment.stylize.rays);
  assert.ok(a.atmosphere.some((x) => x.kind === "rain"));
  assert.equal(a.environment.sky, undefined, "в комнате неба нет");
  assert.equal(a.camera.keys.length, 61 + 61);
  assert.ok(a.camera.keys.every((k) => k.dof !== undefined), "глубина резкости стиля — в ключах");
  assert.ok(a.camera.shake.length >= 1);
  assert.equal(a.environment.transitions[0].kind, "whip");
  const hero = a.characters[0];
  assert.ok(hero.position && hero.seat !== undefined, "герой сел на место окружения");
  assert.ok(hero.faces.length && hero.actions.length);
  assert.deepEqual(a.presetsApplied.camera, ["slow_push", "crash_zoom"]);
  assert.equal(a.presetsApplied.warnings, undefined);
});

test("развёртка: ошибки имён — предупреждения, сцена без presets не меняется", () => {
  const a = expandPresets({ duration: 2, presets: { look: "nope", light: "nope2", fx: ["nope3"], camera: [{ move: "nope4" }] } });
  assert.equal(a.presetsApplied.warnings.length, 4);
  const plain = { duration: 2, lights: [] };
  assert.equal(expandPresets(plain), plain);
});

test("развёртка: через плечо с героем — ключи follow движка; эмоции и жесты есть для обоих движков", () => {
  const a = expandPresets({ duration: 3, characters: [{ ...HERO, at: undefined, position: [0, 0, 0] }, { id: "b", style: "voxel", position: [0, 0, 2], yaw: 180 }],
    presets: { camera: [{ move: "over_shoulder", who: "hero", target: "b", t0: 0, t1: 3 }] } });
  assert.equal(a.camera.keys[0].follow, "hero");
  assert.ok(Math.abs(a.camera.keys[0].look[2] - 2) < 1e-6);
  for (const d of [...Object.values(EMOTIONS), ...Object.values(GESTURES)]) assert.ok(d.voxel && d.vrm, d.meta.name);
});

/* ---------- браузер: по кадру на категорию ---------- */

let HAVE = false;
try {
  findChrome(process.env.SCENE3D_CHROMIUM || "");
  HAVE = existsSync(BUNDLE);
} catch {
  HAVE = false;
}

const SMOKE = {
  look: { look: "watercolor", light: "studio_3pt", room: "studio_cyclorama" },
  light: { light: "campfire", room: "forest_clearing", look: "anime_soft" },
  camera: { camera: [{ move: "dolly_zoom", target: "hero" }], room: "void_stage", light: "stage_spot" },
  fx: { fx: ["snow", "lens_flare", "light_leak"], light: "moonlight", room: "city_rooftop" },
  mood: { mood: "night_rain", room: "city_rooftop", look: "neon_cyber" },
  room: { room: "kitchen_night", light: "moonlight", look: "psx" },
  emotion: { performance: [{ who: "hero", preset: "happy", t: 0.2 }], room: "gamer_rgb", light: "monitor_glow" },
  gesture: { performance: [{ who: "hero", preset: "wave", t: 0.2 }], room: "train_station", light: "moonlight", look: "toon_comic" },
  transition: { transitions: [{ kind: "glitch", t: 1 }], room: "office_openspace", light: "overcast_soft", look: "pixel_voxel" },
};

test("браузер: по кадру на категорию — грузится, не чёрный, кадр зависит только от t", { skip: !HAVE && "нет браузера/бандла" }, async () => {
  const opts = { bundle: BUNDLE, packs: join(HERE, "..", "..", "vendor", "packs"), gl: process.env.SCENE3D_GL || "swiftshader", post: "full", chrome: process.env.SCENE3D_CHROMIUM || "" };
  for (const [cat, presets] of Object.entries(SMOKE)) {
    const spec = { width: 216, height: 384, fps: 30, duration: 2, characters: [{ ...HERO }], presets: { camera: [{ move: "static", target: "hero:chest", distance: 3.5 }], ...presets } };
    const scene = await openScene(opts, spec);
    try {
      const a = await scene.render(1.0);
      assert.ok(a.luma > 6, `${cat}: чёрный кадр (${a.luma})`);
      const shot1 = createHash("sha1").update(await scene.shot("png")).digest("hex");
      await scene.render(0.3);
      await scene.render(1.0);
      const shot2 = createHash("sha1").update(await scene.shot("png")).digest("hex");
      assert.equal(shot1, shot2, `${cat}: кадр t=1 зависит от прошлых кадров`);
      const warn = (await scene.warnings()).filter((w) => !/camera_|toon/.test(w));
      assert.deepEqual(warn, [], cat);
    } finally {
      await scene.close();
    }
  }
});
