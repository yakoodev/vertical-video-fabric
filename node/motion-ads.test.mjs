// Рекламный пакет (одометр, пузыри, карусель, волна, стена плиток, схема сделки, наклон группы по pivot):
// `node --test node/motion-ads.test.mjs` (в `npm run test:motion`). Движок собирается esbuild во временную папку,
// как в motion.test.mjs; компоновщик — node/motion-render.mjs (кадр рендера = кадр превью, шаблоны для агента).
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
  tmp = mkdtempSync(join(tmpdir(), "vvf-ads-"));
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

const near = (a, b, eps) => assert.ok(Math.abs(a - b) <= eps, `${a} ≉ ${b}`);
const env = (w = 1080, h = 1920, images = {}) => ({ width: w, height: h, getImage: (ref) => images[ref] ?? null, createCanvas: (a, b) => createCanvas(a, b) });

function ink(cv, x = 0, y = 0, w = cv.width, h = cv.height) {
  const d = cv.getContext("2d").getImageData(x, y, w, h).data;
  let n = 0;
  for (let i = 3; i < d.length; i += 4) if (d[i] > 10) n++;
  return n;
}

function frame(layers, t, levels, w = 1080, h = 1920) {
  const cv = createCanvas(w, h);
  E.drawFrame(cv.getContext("2d"), { layers, levels }, t, env(w, h));
  return cv;
}

test("одометр: барабаны разрядов от цифр «от» к цифрам «до», лесенка, смаз и итоговое число", () => {
  const layer = { id: "c", type: "counter", start: 0, end: 4, props: { from: 0, to: 2000000, thousands: " ", roll: true, stagger: 0.1, laps: 2, size: 120 } };
  const plan = E.odometerPlan(layer.props);
  assert.equal(plan.n, 7);
  assert.equal(plan.shown, 2000000);
  assert.deepEqual(plan.from, [null, null, null, null, null, null, 0]);
  assert.deepEqual(plan.to, [2, 0, 0, 0, 0, 0, 0]);
  // Старший разряд: до «2» и один оборот, младший — laps оборотов.
  assert.equal(plan.dist[0], 12);
  assert.equal(plan.dist[6], 20);
  const at = (t) => E.odometerCells(layer, t);
  // В начале разряды, которых не было, прозрачны; младший стоит на 0.
  assert.equal(at(0)(0).alpha, 0);
  near(at(0)(6).pos, 0, 1e-9);
  // Лесенка: младший разряд (позже стартует) отстаёт от старшего по ходу.
  const mid = at(0.6);
  assert.ok(mid(0).pos / 12 > mid(6).pos / 20, "старший разряд впереди");
  assert.ok(mid(3).blur > 0, "быстрый барабан смазан");
  // После хода барабаны встали — обычные цифры (null), раскладка итога не «дышит».
  for (let i = 0; i < 7; i++) assert.equal(at(3.5)(i), null);
  assert.equal(E.formatCounter(2000000, layer.props), "2 000 000");
  // Обратный счёт: барабан крутится вниз и заканчивает на своей цифре.
  const down = E.odometerPlan({ from: 950, to: 120, roll: true, laps: 0 });
  assert.deepEqual(down.dist.map((d) => d + 0), [-8, -3, 0]);
  assert.ok(ink(frame([layer], 0.5)) > 1000 && ink(frame([layer], 3.5)) > 5000);
  // Без roll счётчик прежний: текст — значение хода.
  const plain = { ...layer, props: { ...layer.props, roll: false } };
  assert.ok(ink(frame([plain], 1)) > 1000);
});

test("облако пузырей: раскладка без наложений в рамке, по зерну; появление по очереди, счёт, фокус", () => {
  const items = [48000, 12000, 3400, 21000, 9000, 2100, 640].map((value, i) => ({ value, label: `п${i}`, icon: E.AD_ICONS[i] }));
  const p = { items, w: 1000, h: 900, seed: 7, gap: 20, focus: 0, focusAt: 3 };
  const spots = E.bubbleLayout(p);
  assert.equal(spots.length, 7);
  for (const a of spots) {
    assert.ok(Math.abs(a.x) + a.r <= 500 + 1e-6 && Math.abs(a.y) + a.r <= 450 + 1e-6, "в рамке");
    for (const b of spots) if (a !== b) assert.ok(Math.hypot(a.x - b.x, a.y - b.y) >= a.r + b.r + 20 - 1e-6, "без наложений");
  }
  // Крупнее число — крупнее пузырь; то же зерно — та же раскладка, другое — другая.
  assert.ok(spots[0].r > spots[6].r);
  assert.deepEqual(E.bubbleLayout({ ...p }).map((s) => [s.x, s.y]), spots.map((s) => [s.x, s.y]));
  assert.notDeepEqual(E.bubbleLayout({ ...p, seed: 8 }).map((s) => [s.x, s.y]), spots.map((s) => [s.x, s.y]));
  // Появление от центра: самый центральный — первый (rank 0).
  const first = spots.reduce((a, b) => (Math.hypot(a.x, a.y) <= Math.hypot(b.x, b.y) ? a : b));
  assert.equal(first.rank, 0);
  const early = E.bubblePoses(p, 0.2);
  assert.ok(early.filter((s) => s.scale > 0).length < 7, "не все сразу");
  const later = E.bubblePoses(p, 2.5);
  assert.ok(later.every((s) => s.scale > 0.9 && s.count > 0.99));
  // Фокус: первый растёт, остальные гаснут.
  near(E.bubbleFocus(p, spots, 2.9), 0, 1e-9);
  const focused = E.bubblePoses(p, 4);
  assert.ok(focused[0].scale > 1.3 && focused[1].alpha < 0.5);
  assert.ok(ink(frame([{ id: "b", type: "bubbles", start: 0, end: 5, props: p }], 2.5)) > 100000);
});

test("карусель: переезды по switchAt/interval, по кругу, центральная крупнее, кольца", () => {
  const cards = ["А", "Б", "В", "Г"].map((title) => ({ title, subtitle: "подпись", meta: "1 млн" }));
  const p = { cards, interval: 1.5, switchDur: 0.5 };
  near(E.carouselIndex(p, 1.0), 0, 1e-9);
  near(E.carouselIndex(p, 1.75), 0.5, 1e-9);
  near(E.carouselIndex(p, 2.2), 1, 1e-9);
  near(E.carouselIndex({ ...p, switchAt: [0.5], interval: 0 }, 2), 1, 1e-9);
  // По кругу: после последней — первая справа.
  near(E.carouselOffset(p, 0, 3), 1, 1e-9);
  near(E.carouselOffset({ ...p, wrap: false }, 0, 3), -3, 1e-9);
  const cv = frame([{ id: "k", type: "carousel", start: 0, end: 6, y: 0.5, props: p }], 1);
  // Центральная карточка непрозрачна, соседи выглядывают у краёв, кольца — над и под карточкой.
  assert.ok(ink(cv, 400, 900, 280, 120) > 30000);
  assert.ok(ink(cv, 0, 900, 120, 120) > 3000 && ink(cv, 960, 900, 120, 120) > 3000);
  assert.ok(ink(cv, 500, 960 - 470, 80, 40) > 500, "кольца за карточкой");
});

test("волна: огибающая своего звука (wave:<id>) или клипа, иначе образец по зерну", () => {
  const layer = { id: "w1", type: "waveform", start: 0, end: 4, props: { bars: 20, h: 200, sensitivity: 1, minLevel: 0.05 } };
  const loud = { "wave:w1": { t0: 0, fps: 30, v: Array(120).fill(100) } };
  const quiet = { "wave:w1": { t0: 0, fps: 30, v: Array(120).fill(0) } };
  const hl = E.waveHeights(layer, { levels: loud }, 2);
  const hq = E.waveHeights(layer, { levels: quiet }, 2);
  assert.equal(hl.length, 20);
  assert.ok(Math.min(...hl) > 0.45 && Math.max(...hq) <= 0.05 + 1e-9);
  // source voice — огибающая голоса клипа; fake — всегда образец, даже если звук есть.
  const voice = { ...layer, props: { ...layer.props, source: "voice" } };
  assert.equal(E.waveTrack(voice, { levels: { voice: loud["wave:w1"] } }), loud["wave:w1"]);
  assert.equal(E.waveTrack({ ...layer, props: { ...layer.props, source: "fake" } }, { levels: loud }), null);
  // Образец живой: высоты меняются во времени и одинаковы при одном зерне.
  const a = E.waveHeights(layer, {}, 1);
  assert.notDeepEqual(a, E.waveHeights(layer, {}, 1.4));
  assert.deepEqual(a, E.waveHeights(layer, {}, 1));
  for (const style of ["bars", "wave", "dots"]) {
    const l = { ...layer, props: { ...layer.props, style } };
    assert.ok(ink(frame([l], 1, loud)) > ink(frame([l], 1, quiet)), style);
  }
});

test("стена плиток: наклон с перспективой, бесшовная петля, затемнение", () => {
  const p = { tiltX: 28, tiltZ: -16, speed: 50, angle: -45, overlay: 0.6 };
  const [x1, y1] = E.wallShift(p, 2);
  near(Math.hypot(x1, y1), 100, 1e-9);
  // Перспектива: верх стены дальше — та же плитка мельче вверху, чем внизу.
  const g = { cell: 264, tile: 230, tiltX: (28 * Math.PI) / 180, tiltZ: 0, cos: 1, sin: 0, ct: Math.cos((28 * Math.PI) / 180), st: Math.sin((28 * Math.PI) / 180) };
  const top = E.wallProject(g, 230, -800)[0] - E.wallProject(g, 0, -800)[0];
  const bottom = E.wallProject(g, 230, 800)[0] - E.wallProject(g, 0, 800)[0];
  assert.ok(top < bottom * 0.8, `${top} < ${bottom}`);
  const layer = { id: "tw", type: "tile_wall", start: 0, end: 10, props: p };
  const a = frame([layer], 1, null, 540, 960);
  const b = frame([layer], 1.5, null, 540, 960);
  assert.ok(ink(a) > 540 * 960 * 0.98, "закрывает рамку");
  const da = a.getContext("2d").getImageData(0, 0, 540, 960).data;
  const db = b.getContext("2d").getImageData(0, 0, 540, 960).data;
  let diff = 0;
  for (let i = 0; i < da.length; i += 4) if (Math.abs(da[i] - db[i]) > 8) diff++;
  assert.ok(diff > 20000, "стена едет");
  // Без затемнения ярче.
  const bright = frame([{ ...layer, props: { ...p, overlay: 0 } }], 1, null, 540, 960).getContext("2d").getImageData(0, 0, 540, 960).data;
  let sa = 0;
  let sb = 0;
  for (let i = 0; i < da.length; i += 4) {
    sa += da[i] + da[i + 1] + da[i + 2];
    sb += bright[i] + bright[i + 1] + bright[i + 2];
  }
  assert.ok(sa < sb * 0.6, "затемнение под текст");
});

test("схема сделки: узлы по шагам, связи между ними, замок на щите, плашка «Готово» в конце", () => {
  const p = { nodes: [{ label: "Покупатель", icon: "user" }, { label: "Сервис" }, { label: "Продавец", icon: "store" }], stepGap: 0.8, delay: 0.3, done: "Готово" };
  const geo = E.flowGeometry(p);
  assert.equal(geo.center, 1);
  assert.ok(geo.r[1] > geo.r[0]);
  assert.ok(geo.pts[0][0] < geo.pts[1][0] && geo.pts[1][0] < geo.pts[2][0]);
  const t = E.flowTimes(p);
  assert.deepEqual(t.node.map((v) => Math.round(v * 100) / 100), [0.3, 1.1, 1.9]);
  assert.ok(t.link[0][0] > t.node[0] && t.link[0][1] <= t.node[1] + 1e-9);
  assert.ok(t.lock > t.node[1] && t.done > t.node[2]);
  const layer = { id: "f", type: "flow_diagram", start: 0, end: 5, y: 0.5, props: p };
  const at = (s) => frame([layer], s, null);
  // Плашка внизу рамки появляется только после последнего шага.
  const badge = (cv) => ink(cv, 340, 960 + 200, 400, 120);
  assert.ok(badge(at(1.5)) < 100 && badge(at(4)) > 5000);
  assert.ok(ink(at(0.1)) < ink(at(1.5)) && ink(at(1.5)) < ink(at(3)));
});

test("pivot: наклон и наезд группы вокруг одной точки — слои как одна плоскость", () => {
  const keys = { rotX: [{ t: 0, v: 30 }], scale: [{ t: 0, v: 1 }, { t: 2, v: 2 }] };
  const pivot = { x: 0.5, y: 0.5 };
  const a = { id: "a", type: "shape", start: 0, end: 3, x: 0.5, y: 0.25, pivot, keys, props: { kind: "rect", w: 200, h: 200, fill: "#ffffff" } };
  // Наезд: центр уходит от точки во столько же раз, во сколько вырос ключ scale.
  const st = E.resolveLayer(a, 2);
  near(st.scale, 2, 1e-9);
  near(st.y, 0.5 + (0.25 - 0.5) * 2, 1e-9);
  // Без pivot — центр стоит.
  near(E.resolveLayer({ ...a, pivot: undefined }, 2).y, 0.25, 1e-9);
  // Наклон вокруг точки: центр слоя уезжает по дуге (вглубь и к точке), с той же плоскостью, что у соседа.
  const e = env();
  const s0 = E.resolveLayer(a, 0);
  const pl = E.layerPlane(s0, e, a);
  near(pl.cy, 960 - 480 * Math.cos(Math.PI / 6), 1e-6);
  near(pl.z, 480 * Math.sin(Math.PI / 6), 1e-6);
  // Точка плоскости соседа (pivot тот же) = та же точка плоскости группы: две половины одного прямоугольника.
  const b = { ...a, id: "b", y: 0.75 };
  const pb = E.layerPlane(E.resolveLayer(b, 0), e, b);
  const fromA = E.planePoint(pl, 0, 480, 1080, 1920);
  const fromB = E.planePoint(pb, 0, -480, 1080, 1920);
  for (let i = 0; i < 3; i++) near(fromA[i], fromB[i], 1e-6);
  // Рамка выделения — по проекции сдвинутого центра.
  const cv = createCanvas(1080, 1920);
  const box = E.layerBox(cv.getContext("2d"), a, 0, e);
  assert.ok(box.cy > 480 + 20, `${box.cy}`);
});

test("шаблоны «Реклама»: собираются, слои своих типов; компоновщик отдаёт список и сборку агенту", () => {
  const ids = ["odometer-counter", "bubbles-cloud", "rating-badge", "screen-tilt", "card-carousel", "voice-wave", "karaoke-card", "tile-wall", "deal-flow"];
  for (const id of ids) {
    const t = E.getTemplate(id);
    assert.ok(t && t.category === "Реклама", id);
    const built = E.buildTemplate(id, {}, 1);
    assert.ok(built.layers.length > 0, id);
    for (const l of built.layers) assert.ok(l.start >= 1 && l.end > l.start, `${id}: ${l.type}`);
  }
  const tilt = E.buildTemplate("screen-tilt", {}, 0).layers;
  assert.ok(tilt.length >= 6 && tilt.every((l) => l.pivot && l.keys.rotX.length === 3), "группа с общей точкой");
  assert.deepEqual(E.screenTiltKeys(5, 24, 1.4).scale.at(-1).v, 1.4);
  const [cloud] = E.buildTemplate("bubbles-cloud", { items: "star | 10 | a; бук | 5 | б", focus: 2 }, 0).layers;
  assert.deepEqual(cloud.props.items.map((i) => [i.icon, i.value, i.label]), [["star", 10, "a"], ["бук", 5, "б"]]);
  assert.equal(cloud.props.focus, 1);
  assert.deepEqual(E.parseRows("a | b; ; c"), [["a", "b"], ["c"]]);

  const list = spawnSync(process.execPath, [CLI, "--list-templates"], { encoding: "utf8", timeout: 60000 });
  assert.equal(list.status, 0, list.stderr);
  const all = JSON.parse(list.stdout);
  for (const id of ids) assert.ok(all.some((t) => t.id === id && t.params.length), id);
  writeFileSync(join(tmp, "job.json"), JSON.stringify({ id: "deal-flow", params: { done: "Ок" }, at: 2, format: "1:1" }));
  const built = spawnSync(process.execPath, [CLI, "--build-template", join(tmp, "job.json")], { encoding: "utf8", timeout: 60000 });
  assert.equal(built.status, 0, built.stderr);
  const [flow] = JSON.parse(built.stdout).layers;
  assert.equal(flow.type, "flow_diagram");
  assert.equal(flow.props.done, "Ок");
  assert.equal(flow.start, 2);
  writeFileSync(join(tmp, "bad.json"), JSON.stringify({ id: "nope" }));
  assert.equal(spawnSync(process.execPath, [CLI, "--build-template", join(tmp, "bad.json")], { encoding: "utf8" }).status, 2);
});

test("кадр компоновщика (рендер) = кадр движка (превью) со всеми слоями пакета и огибающей волны", async () => {
  const icon = createCanvas(64, 64);
  icon.getContext("2d").fillStyle = "#ff3366";
  icon.getContext("2d").fillRect(0, 0, 64, 64);
  const png = join(tmp, "icon.png");
  writeFileSync(png, icon.toBuffer("image/png"));
  const layers = [
    { id: "tw", type: "tile_wall", start: 0, end: 5, track: 0, props: { overlay: 0.5, images: ["7"] } },
    { id: "c", type: "counter", start: 0, end: 5, y: 0.1, track: 2, props: { from: 0, to: 2000000, thousands: " ", roll: true, size: 110, font: "Manrope" } },
    { id: "b", type: "bubbles", start: 0, end: 5, y: 0.35, track: 2, props: { items: [{ value: 10, image: "7", label: "x" }, { value: 4, icon: "star" }], w: 700, h: 400 } },
    { id: "k", type: "carousel", start: 0, end: 5, y: 0.62, track: 2, props: { cards: [{ title: "Ника", image: "7" }, { title: "Граф" }], cardH: 500, cardW: 380 } },
    { id: "w", type: "waveform", start: 0, end: 5, y: 0.82, track: 2, props: {} },
    { id: "f", type: "flow_diagram", start: 0, end: 5, y: 0.9, scale: 0.6, track: 3, props: { nodes: [{ label: "А" }, { label: "Б" }, { label: "В" }] } },
  ];
  const levels = { "wave:w": { t0: 0, fps: 30, v: Array.from({ length: 150 }, (_, i) => (i * 7) % 100) } };
  writeFileSync(join(tmp, "a.json"), JSON.stringify({ width: 540, height: 960, fps: 30, duration: 5, layers, levels }));
  writeFileSync(join(tmp, "a.assets.json"), JSON.stringify({ 7: png }));
  const out = join(tmp, "a.png");
  const r = spawnSync(process.execPath, [CLI, "--comp", join(tmp, "a.json"), "--assets", join(tmp, "a.assets.json"), "--frame", "1.3", "--png", out, "--fonts-dir", FONTS], { encoding: "utf8", timeout: 120000 });
  assert.equal(r.status, 0, r.stderr);
  const a = createCanvas(540, 960);
  a.getContext("2d").drawImage(await loadImage(readFileSync(out)), 0, 0);
  const b = createCanvas(540, 960);
  E.drawFrame(b.getContext("2d"), { layers, levels }, 1.3, env(540, 960, { 7: await loadImage(readFileSync(png)) }));
  const da = a.getContext("2d").getImageData(0, 0, 540, 960).data;
  const db = b.getContext("2d").getImageData(0, 0, 540, 960).data;
  let diff = 0;
  let inked = 0;
  for (let i = 0; i < da.length; i += 4) {
    if (da[i + 3] > 0) inked++;
    if (Math.abs(da[i] - db[i]) > 3 || Math.abs(da[i + 1] - db[i + 1]) > 3 || Math.abs(da[i + 2] - db[i + 2]) > 3) diff++;
  }
  assert.ok(inked > 400000, `нарисовано ${inked}`);
  assert.ok(diff <= inked * 0.01, `расхождение рендера и превью: ${diff} из ${inked}`);
  // Огибающая wave:<id> дошла до компоновщика: без неё волна другая.
  const noWave = createCanvas(540, 960);
  E.drawFrame(noWave.getContext("2d"), { layers, levels: {} }, 1.3, env(540, 960, { 7: await loadImage(readFileSync(png)) }));
  const dn = noWave.getContext("2d").getImageData(0, 760, 540, 70).data;
  const dr = a.getContext("2d").getImageData(0, 760, 540, 70).data;
  let waveDiff = 0;
  for (let i = 0; i < dn.length; i += 4) if (Math.abs(dn[i] - dr[i]) > 10) waveDiff++;
  assert.ok(waveDiff > 50, `волна по огибающей, а не по образцу (${waveDiff})`);
});
