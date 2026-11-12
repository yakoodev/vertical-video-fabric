// Перетекающие переходы (слой `transition`, frontend/src/motion/flow.ts): reveal_from, zoom_through,
// shared_element, push_slide. `node --test node/motion-flow.test.mjs` (в `npm run test:motion`). Движок собирается
// esbuild во временную папку, как в motion.test.mjs; кадр CLI node/motion-render.mjs = кадр превью (drawFrame).
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";
import { createCanvas, GlobalFonts, loadImage } from "@napi-rs/canvas";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const CLI = join(ROOT, "node", "motion-render.mjs");
const FONTS = join(ROOT, "vendor", "fonts");
let tmp;
let E;

before(async () => {
  tmp = mkdtempSync(join(tmpdir(), "vvf-flow-"));
  const out = join(tmp, "engine.mjs");
  await build({ entryPoints: [join(ROOT, "frontend", "src", "motion", "index.ts")], bundle: true, platform: "node", format: "esm", outfile: out, logLevel: "warning" });
  E = await import(pathToFileURL(out).href);
  for (const f of JSON.parse(readFileSync(join(FONTS, "fonts.json"), "utf8"))) GlobalFonts.registerFromPath(join(FONTS, f.file), f.family);
  if (!existsSync(CLI)) {
    const r = spawnSync("npm", ["run", "build:motion"], { cwd: ROOT, shell: true, encoding: "utf8" });
    assert.equal(r.status, 0, r.stderr);
  }
});

after(() => {
  if (tmp) rmSync(tmp, { recursive: true, force: true });
});

const W = 270;
const H = 480;
const env = (w = W, h = H) => ({ width: w, height: h, getImage: () => null, createCanvas: (a, b) => createCanvas(a, b) });

function frame(layers, t, w = W, h = H) {
  const cv = createCanvas(w, h);
  const c = cv.getContext("2d");
  c.fillStyle = "#000000";
  c.fillRect(0, 0, w, h);
  E.drawFrame(c, { layers }, t, env(w, h));
  return cv;
}

/** Цвет пикселя в долях кадра: [r, g, b]. */
function px(cv, fx, fy) {
  const d = cv.getContext("2d").getImageData(Math.min(cv.width - 1, Math.round(fx * cv.width)), Math.min(cv.height - 1, Math.round(fy * cv.height)), 1, 1).data;
  return [d[0], d[1], d[2]];
}
const isRed = ([r, g, b]) => r > 200 && g < 60 && b < 60;
const isBlue = ([r, g, b]) => b > 200 && r < 60 && g < 60;
const isGreen = ([r, g, b]) => g > 200 && r < 60 && b < 60;

const full = (id, color, start, end, extra = {}) => ({
  id, type: "shape", start, end, track: 1, x: 0.5, y: 0.5, props: { kind: "rect", w: 1080, h: 1920, fill: color }, in: null, out: null, ...extra,
});
const tr = (props, start = 1, end = 2, extra = {}) => ({ id: "tr", type: "transition", start, end, track: 0, x: 0.5, y: 0.5, props, in: null, out: null, ...extra });

test("автогруппы: A — кончаются к концу перехода, B — начинаются на нём, сквозные и субтитры не трогаем", () => {
  const layers = [
    full("a", "#ff0000", 0, 2),
    full("b", "#0000ff", 1, 4),
    full("bg", "#111111", 0, 4),
    { id: "cap", type: "captions", start: 0, end: 4, props: {} },
    { id: "late", type: "text", start: 3, end: 4, props: { text: "x" } },
    tr({ kind: "push_slide" }),
  ];
  const g = E.flowGroups(layers[5], layers);
  assert.deepEqual([...g.from], ["a"]);
  assert.deepEqual([...g.to], ["b"]);
  // Явные списки — как есть, B не может быть и в A.
  const g2 = E.flowGroups(tr({ kind: "push_slide", from: ["a", "b", "cap"], to: ["b"] }), layers);
  assert.deepEqual([...g2.from], ["a"]);
  assert.deepEqual([...g2.to], ["b"]);
});

test("reveal_from: круг из точки сцены A раскрывает B, A вокруг остаётся; концы — ровно A и ровно B", () => {
  const dot = { id: "dot", type: "shape", start: 0, end: 2, track: 2, x: 0.5, y: 0.6, props: { kind: "ellipse", w: 40, h: 40, fill: "#00ff00" }, in: null, out: null };
  const layers = [full("a", "#ff0000", 0, 2), dot, full("b", "#0000ff", 1, 4), tr({ kind: "reveal_from", target: "dot", from: ["a", "dot"], to: ["b"], depth: 0 })];
  // До перехода — сцена A.
  assert.ok(isRed(px(frame(layers, 0.9), 0.1, 0.1)));
  assert.ok(isGreen(px(frame(layers, 0.9), 0.5, 0.6)));
  // Посередине: у точки — B, в углу — ещё A.
  const mid = frame(layers, 1.5);
  assert.ok(isBlue(px(mid, 0.5, 0.6)), `центр ${px(mid, 0.5, 0.6)}`);
  assert.ok(isRed(px(mid, 0.02, 0.02)), `угол ${px(mid, 0.02, 0.02)}`);
  // Круг растёт: доля синего монотонна.
  const blue = (t) => {
    const cv = frame(layers, t);
    let n = 0;
    for (let y = 0.05; y < 1; y += 0.1) for (let x = 0.05; x < 1; x += 0.1) if (isBlue(px(cv, x, y))) n++;
    return n;
  };
  const series = [1.1, 1.3, 1.5, 1.7, 1.9].map(blue);
  for (let i = 1; i < series.length; i++) assert.ok(series[i] >= series[i - 1], `круг растёт: ${series}`);
  // Конец перехода — всё B (и после него тоже, без скачка).
  assert.ok(isBlue(px(frame(layers, 1.999), 0.01, 0.01)));
  assert.ok(isBlue(px(frame(layers, 2.0), 0.01, 0.01)));
});

test("reveal_from: обратная маска A с растушёвкой и кольцо по краю", () => {
  const layers = [full("a", "#ff0000", 0, 2), full("b", "#0000ff", 1, 4), tr({ kind: "reveal_from", w: 100, feather: 80, edge: "#00ff00", edgeWidth: 10, from: ["a"], to: ["b"], ease: "linear" })];
  const st = E.flowState(layers[2], 1.5, env(), E.flowRegion(layers[2], env(), null));
  assert.equal(st.a.mask.invert, true);
  assert.equal(st.b.mask.invert, false);
  assert.ok(st.ring && st.ring.alpha > 0.9);
  const cv = frame(layers, 1.5);
  // На краю круга — зелёное кольцо, у центра — B.
  const r = st.b.mask.hw / H;
  const ring = px(cv, 0.5, 0.5 + r);
  assert.ok(ring[1] > 120, `кольцо ${ring}`);
  assert.ok(isBlue(px(cv, 0.5, 0.5)));
});

test("zoom_through: камера пролетает сквозь кнопку A, B растёт из неё и в конце занимает кадр", () => {
  const btn = { id: "btn", type: "shape", start: 0, end: 2, track: 2, x: 0.5, y: 0.75, props: { kind: "rect", w: 300, h: 120, radius: 30, fill: "#00ff00" }, in: null, out: null };
  const layers = [full("a", "#ff0000", 0, 2), btn, full("b", "#0000ff", 1, 4), tr({ kind: "zoom_through", target: "btn", from: ["a", "btn"], to: ["b"], radius: 30 })];
  const t = layers[3];
  const reg = E.flowRegion(t, env(), { cx: 0.5 * W, cy: 0.75 * H, w: 300 / 4, h: 120 / 4 });
  const s0 = E.flowState(t, 1.0, env(), reg);
  assert.ok(Math.abs(s0.a.s - 1) < 1e-6 && s0.b.alpha === 0, "в начале A как есть, B не видно");
  const s1 = E.flowState(t, 1.9999, env(), reg);
  assert.ok(Math.abs(s1.b.s - 1) < 0.01, `в конце B в масштабе 1 (${s1.b.s})`);
  assert.ok(s1.a.s > 10, "кнопка выросла на весь кадр");
  // Посередине центр кнопки — уже B внутри неё, вокруг — увеличенная кнопка/фон A.
  const mid = frame(layers, 1.6);
  assert.ok(isBlue(px(mid, 0.5, 0.5)) || isBlue(px(mid, 0.5, 0.6)), `центр ${px(mid, 0.5, 0.55)}`);
  // Кнопка растёт к центру: на 1.3 её центр выше исходного (0.75).
  const st = E.flowState(t, 1.3, env(), reg);
  const cy = (0.75 * H) * st.a.s + st.a.ty;
  assert.ok(cy < 0.75 * H && cy > 0.5 * H, `центр кнопки ${cy}`);
  assert.ok(isBlue(px(frame(layers, 1.999), 0.02, 0.02)));
});

test("shared_element: квадрат A перетекает в плашку B — место, размер, скругление и цвет плавно", () => {
  const a = { id: "ea", type: "shape", start: 0, end: 2, track: 2, x: 0.3, y: 0.3, props: { kind: "rect", w: 200, h: 200, radius: 0, fill: "#ff0000" }, in: null, out: null };
  const b = { id: "eb", type: "shape", start: 1, end: 4, track: 2, x: 0.6, y: 0.7, props: { kind: "rect", w: 600, h: 300, radius: 60, fill: "#0000ff" }, in: null, out: null };
  const other = { id: "o", type: "shape", start: 0, end: 2, track: 1, x: 0.8, y: 0.1, props: { kind: "rect", w: 100, h: 100, fill: "#00ff00" }, in: null, out: null };
  const layers = [other, a, b, tr({ kind: "shared_element", target: "ea", match: "eb", from: ["ea", "o"], to: ["eb"], ease: "linear" })];
  const mid = frame(layers, 1.5);
  // Посередине — один прямоугольник фиолетового цвета на середине пути.
  const c = px(mid, 0.45, 0.5);
  assert.ok(c[0] > 90 && c[2] > 90 && c[1] < 40, `середина пути ${c}`);
  // Старых мест уже нет: ни красного в (0.3, 0.3)… (там пусто или фиолетово), ни синего в (0.6, 0.7) целиком.
  assert.ok(!isRed(px(mid, 0.3, 0.27)));
  // Конец — ровно B, начало — ровно A.
  assert.ok(isBlue(px(frame(layers, 1.999), 0.6, 0.7)));
  assert.ok(isRed(px(frame(layers, 1.0), 0.3, 0.3)));
  // Окружение A гаснет.
  assert.ok(isGreen(px(frame(layers, 1.0), 0.8, 0.1)));
  const g = px(frame(layers, 1.6), 0.8, 0.1);
  assert.ok(g[1] < 60, `окружение A погасло ${g}`);
  // Разные типы — две копии на общем месте (A гаснет, B проявляется).
  const sa = { x: 0.3, y: 0.3, scale: 1, rotation: 0, opacity: 1 };
  const two = E.morphLayers({ ...a, type: "text", props: { text: "A" } }, b, sa, { ...sa, x: 0.6 }, { cx: 10, cy: 10, w: 10, h: 10 }, { cx: 30, cy: 10, w: 40, h: 10 }, 0.5, 1.5, env());
  assert.equal(two.length, 2);
  assert.ok(Math.abs(two[0].x - two[1].x) < 1e-9);
});

test("push_slide: A уезжает влево, B въезжает справа встык, без щели; dip — лёгкий отъезд камеры", () => {
  const layers = [full("a", "#ff0000", 0, 2), full("b", "#0000ff", 1, 4), tr({ kind: "push_slide", direction: "left", dip: 0, ease: "linear", from: ["a"], to: ["b"] })];
  const mid = frame(layers, 1.5);
  assert.ok(isRed(px(mid, 0.25, 0.5)));
  assert.ok(isBlue(px(mid, 0.75, 0.5)));
  assert.ok(isRed(px(mid, 0.49, 0.5)) && isBlue(px(mid, 0.51, 0.5)), "встык посередине");
  const st = E.flowState({ ...layers[2], props: { kind: "push_slide", dip: 0.1 } }, 1.5, env(), { cx: 0, cy: 0, w: 0, h: 0 });
  assert.ok(Math.abs(st.a.s - 0.9) < 0.02, "посередине камера отъехала на dip");
  const up = E.flowState({ ...layers[2], props: { kind: "push_slide", direction: "up", dip: 0 } }, 2.0, env(), { cx: 0, cy: 0, w: 0, h: 0 });
  assert.ok(Math.abs(up.a.ty + H) < 1e-6 && Math.abs(up.b.ty) < 1e-6);
});

test("слой перехода сам не рисуется; без фабрики холстов — без падений", () => {
  const cv = frame([tr({ kind: "reveal_from" })], 1.5);
  assert.deepEqual(px(cv, 0.5, 0.5), [0, 0, 0]);
  const c = createCanvas(W, H);
  const layers = [full("a", "#ff0000", 0, 2), full("b", "#0000ff", 1, 4), tr({ kind: "reveal_from", from: ["a"], to: ["b"] })];
  E.drawFrame(c.getContext("2d"), { layers }, 1.5, { width: W, height: H, getImage: () => null });
});

test("кадр компоновщика (рендер) = кадр превью (drawFrame) на каждом виде перехода", async () => {
  const btn = { id: "btn", type: "shape", start: 0, end: 2, track: 2, x: 0.5, y: 0.75, props: { kind: "rect", w: 300, h: 120, radius: 30, fill: "#00ff00" }, in: null, out: null };
  const txt = { id: "tx", type: "text", start: 1, end: 4, track: 3, x: 0.5, y: 0.4, props: { text: "СЦЕНА B", size: 120, color: "#ffffff" }, in: null, out: null };
  const kinds = [
    { kind: "reveal_from", target: "btn", feather: 30, edge: "#ffd43b" },
    { kind: "zoom_through", target: "btn", radius: 30, feather: 20 },
    { kind: "shared_element", target: "btn", match: "tx" },
    { kind: "push_slide", direction: "up", dip: 0.1 },
  ];
  for (const props of kinds) {
    const layers = [full("a", "#ff0000", 0, 2), btn, full("b", "#0000ff", 1, 4, { track: 1 }), txt, tr({ ...props, from: ["a", "btn"], to: ["b", "tx"] })];
    const comp = { width: W, height: H, fps: 30, duration: 4, layers };
    writeFileSync(join(tmp, "c.json"), JSON.stringify(comp));
    writeFileSync(join(tmp, "c.assets.json"), "{}");
    const out = join(tmp, `${props.kind}.png`);
    const r = spawnSync(process.execPath, [CLI, "--comp", join(tmp, "c.json"), "--assets", join(tmp, "c.assets.json"), "--frame", "1.45", "--png", out, "--fonts-dir", FONTS], { encoding: "utf8", timeout: 120000 });
    assert.equal(r.status, 0, r.stderr);
    await checkSame(out, layers, props.kind);
  }
});

async function checkSame(out, layers, kind) {
  const a = createCanvas(W, H);
  const ac = a.getContext("2d");
  ac.fillStyle = "#000000";
  ac.fillRect(0, 0, W, H);
  ac.drawImage(await loadImage(readFileSync(out)), 0, 0);
  const b = createCanvas(W, H);
  const bc = b.getContext("2d");
  bc.fillStyle = "#000000";
  bc.fillRect(0, 0, W, H);
  E.drawFrame(bc, { layers }, 1.45, env());
  const da = a.getContext("2d").getImageData(0, 0, W, H).data;
  const db = bc.getImageData(0, 0, W, H).data;
  let diff = 0;
  for (let i = 0; i < da.length; i += 4) {
    if (Math.abs(da[i] - db[i]) > 6 || Math.abs(da[i + 1] - db[i + 1]) > 6 || Math.abs(da[i + 2] - db[i + 2]) > 6) diff++;
  }
  assert.ok(diff / (W * H) < 0.01, `${kind}: расхождение ${diff} px`);
}
