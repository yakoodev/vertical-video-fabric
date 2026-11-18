// Слой физики (frontend/src/motion/physics.ts, layers/physics.ts): детерминизм (дважды одно и то же; подмена
// Math.sin/cos/atan2/pow хозяина не меняет мир — превью в любом браузере = рендер в Node), пределы тел и времени,
// производительность, разумность мира (лежат на полу, улетают без стенок, разлёт, толчок) и кадр слоя.
// `node --test node/motion-physics.test.mjs` (в `npm run test:motion`). Движок собирается esbuild во временную папку.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";
import { createCanvas, GlobalFonts } from "@napi-rs/canvas";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FONTS = join(ROOT, "vendor", "fonts");
const W = 1080, H = 1920;
let tmp;
let E;

before(async () => {
  tmp = mkdtempSync(join(tmpdir(), "vvf-physics-"));
  const out = join(tmp, "engine.mjs");
  await build({ entryPoints: [join(ROOT, "frontend", "src", "motion", "index.ts")], bundle: true, platform: "node", format: "esm", outfile: out, logLevel: "warning" });
  E = await import(pathToFileURL(out).href);
  for (const f of JSON.parse(readFileSync(join(FONTS, "fonts.json"), "utf8"))) GlobalFonts.registerFromPath(join(FONTS, f.file), f.family);
});

after(() => {
  if (tmp) rmSync(tmp, { recursive: true, force: true });
});

const LETTERS = { bodies: [{ kind: "text", text: "БАМ ПАДАЕТ", split: "letters", size: 200, colors: ["#ffd400"], stroke: "#000000" }], spawn: "drop", walls: "frame", origin: { x: 0.5, y: 0.25 } };
const RAIN = { bodies: [{ kind: "shape", shape: "circle", size: 90, count: 40 }, { kind: "shape", shape: "box", size: 110, count: 20 }], spawn: "rain", walls: "frame", spread: 1.2 };
const sim = (p, seed = 7, sec = 4) => E.simulatePhysics(p, seed, W, H, sec);

test("детерминизм: тот же мир дважды — те же числа", () => {
  for (const p of [LETTERS, RAIN, { ...RAIN, spawn: "burst", walls: "none" }]) {
    const a = sim(p);
    const b = sim(p);
    assert.equal(a.steps, b.steps);
    assert.deepEqual(Array.from(a.data), Array.from(b.data));
    assert.deepEqual(Array.from(a.spawn), Array.from(b.spawn));
  }
  // Другое зерно — другая раскладка дождя.
  assert.notDeepEqual(Array.from(sim(RAIN, 7).data.slice(0, 300)), Array.from(sim(RAIN, 8).data.slice(0, 300)));
});

test("превью = рендер: последний бит Math.sin/cos/atan2/pow хозяина на мир не влияет", () => {
  const base = sim(RAIN, 3, 3);
  const orig = { sin: Math.sin, cos: Math.cos, atan2: Math.atan2, pow: Math.pow };
  // Как другой браузер: те же функции, но с ошибкой в последних битах.
  Math.sin = (x) => orig.sin(x) * (1 + 3e-16);
  Math.cos = (x) => orig.cos(x) * (1 - 3e-16);
  Math.atan2 = (y, x) => orig.atan2(y, x) * (1 + 3e-16);
  Math.pow = (x, y) => orig.pow(x, y) * (1 + 3e-16);
  let other;
  try {
    other = sim(RAIN, 3, 3);
  } finally {
    Object.assign(Math, orig);
  }
  assert.deepEqual(Array.from(other.data), Array.from(base.data));
  assert.equal(Math.sin, orig.sin, "Math.sin вернулся");
});

test("свои sin/cos — с точностью Math", () => {
  for (let x = -50; x <= 50; x += 0.0137) {
    assert.ok(Math.abs(E.detSin(x) - Math.sin(x)) < 1e-14, `sin ${x}`);
    assert.ok(Math.abs(E.detCos(x) - Math.cos(x)) < 1e-14, `cos ${x}`);
  }
});

test("кеш: трек не зависит от размера выхода, кадр — от порядка перемотки", () => {
  E.clearPhysicsCache();
  const layer = { id: "ph", type: "physics", start: 0, end: 3, x: 0.5, y: 0.5, props: LETTERS };
  const draw = (w, h, t) => {
    const cv = createCanvas(w, h);
    E.drawFrame(cv.getContext("2d"), { layers: [layer] }, t, { width: w, height: h, getImage: () => null, createCanvas: (a, b) => createCanvas(a, b) });
    return cv.getContext("2d").getImageData(0, 0, w, h).data;
  };
  const forward = [0.3, 0.9, 1.6, 2.5].map((t) => Buffer.from(draw(270, 480, t)).toString("base64"));
  E.clearPhysicsCache();
  const backward = [2.5, 1.6, 0.9, 0.3].map((t) => Buffer.from(draw(270, 480, t)).toString("base64")).reverse();
  assert.deepEqual(forward, backward);
  // Превью (270×480) и рендер (1080×1920) читают один трек опорного кадра.
  const seed = E.physicsSeed(layer);
  const tr = E.physicsTrack(LETTERS, seed, W, H, 3);
  assert.equal(E.physicsTrack(LETTERS, seed, W, H, 3), tr, "из кеша — тот же объект");
  const big = draw(1080, 1920, 1.6);
  let ink = 0;
  for (let i = 3; i < big.length; i += 4 * 97) if (big[i] > 0) ink++;
  assert.ok(ink > 30, "буквы нарисованы");
});

test("пределы: тел не больше MAX_PHYSICS_BODIES, мир не дольше MAX_SIM_SEC", () => {
  const many = E.physicsUnits({ bodies: [{ kind: "text", text: "А".repeat(190) }, { kind: "shape", count: 1000 }] });
  assert.equal(many.length, E.MAX_PHYSICS_BODIES);
  assert.equal(E.physicsUnits({ bodies: [{ kind: "image", src: "emoji:1f525", count: 500 }] }).length, E.MAX_PHYSICS_BODIES);
  const long = E.physicsTrack({ ...RAIN, gravity: 0.3, restitution: 0.95 }, 1, W, H, 60);
  assert.ok(long.steps <= E.MAX_SIM_SEC * E.PHYSICS_STEP_HZ + 1, `шагов ${long.steps}`);
  // После конца счёта — последнее состояние.
  const last = E.bodyAt(long, 0, long.steps / long.hz);
  assert.deepEqual(E.bodyAt(long, 0, 59), last);
});

test("производительность: 150 тел дождём 20 с считаются за разумное время", () => {
  const p = { bodies: [{ kind: "shape", shape: "circle", size: 60, count: 150 }], spawn: "rain", walls: "frame", spread: 3, restitution: 0.2 };
  const tr = E.simulatePhysics(p, 11, W, H, E.MAX_SIM_SEC);
  assert.equal(tr.units.length, 150);
  assert.ok(tr.ms < 8000, `счёт ${Math.round(tr.ms)} мс`);
});

test("мир: буквы ложатся на пол в рамке, без стенок улетают, разлёт расходится, куча ждёт толчка", () => {
  const drop = sim(LETTERS, 5, 4);
  assert.ok(drop.settled !== null || drop.steps > 200);
  for (let i = 0; i < drop.units.length; i++) {
    const at = E.bodyAt(drop, i, 4);
    assert.ok(at && at.x > 0 && at.x < W && at.y > H * 0.7 && at.y < H, `буква ${i}: ${JSON.stringify(at)}`);
  }
  const none = sim({ ...RAIN, walls: "none" }, 5, 4);
  assert.ok(Array.from(none.gone).every((g) => Number.isFinite(g)), "без стенок всё ушло за кадр");
  assert.equal(E.bodyAt(none, 0, 3.9), null);
  const burst = sim({ bodies: [{ kind: "shape", shape: "circle", size: 50, count: 24 }], spawn: "burst", walls: "none", gravity: 0, origin: { x: 0.5, y: 0.5 }, power: 1500 }, 2, 1);
  const r = (t) => {
    let s = 0;
    for (let i = 0; i < 24; i++) {
      const b = E.bodyAt(burst, i, t);
      if (b) s += Math.hypot(b.x - W / 2, b.y - H / 2);
    }
    return s / 24;
  };
  assert.ok(r(0.5) > r(0.1) + 200, `разлёт ${r(0.1)} → ${r(0.5)}`);
  const plate = { bodies: [{ kind: "shape", shape: "plate", size: 300 }], spawn: "stack", walls: "frame", origin: { x: 0.5, y: 0.6 }, friction: 0.08,
                  platforms: [{ x: 0.42, y: 0.61, w: 0.5, h: 0.02 }], impulse: { at: 0.8, x: 0.2, y: 0.598, force: 2600, radius: 0.5, lift: 0 } };
  const pt = sim(plate, 1, 3);
  const before = E.bodyAt(pt, 0, 0.7);
  assert.ok(Math.abs(before.x - 0.5 * W) < 2 && Math.abs(before.angle) < 1e-3, `тарелка стоит: ${JSON.stringify(before)}`);
  const after = E.bodyAt(pt, 0, 2.9);
  assert.ok(after.x > before.x + 100 && after.y > before.y + 300, `сбита и упала: ${JSON.stringify(after)}`);
});

test("слой физики: картинки тел в ссылках, шаблон собирается", () => {
  const refs = E.collectImageRefs([{ id: "p", type: "physics", start: 0, end: 2, props: { bodies: [{ kind: "image", src: "emoji:1f525", count: 3 }, { kind: "text", text: "ОГОНЬ 🔥" }] } }]);
  assert.deepEqual(refs.sort(), ["emoji:1f525"]);
  for (const mode of ["letters", "rain", "burst", "plate"]) {
    const tpl = E.buildTemplate("physics", { mode }, 1);
    assert.equal(tpl.layers[0].type, "physics", mode);
    assert.ok(tpl.layers[0].props.bodies.length >= 1, mode);
  }
});
