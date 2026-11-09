// Рисованное от руки, курсор с касаниями и карточки соцсети: `node --test node/motion-blocks.test.mjs`
// (в `npm run test:motion`). Движок из frontend/src/motion собирается esbuild во временную папку,
// как в motion.test.mjs; компоновщик — node/motion-render.mjs.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";
import { createCanvas, loadImage } from "@napi-rs/canvas";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const CLI = join(ROOT, "node", "motion-render.mjs");
const FONTS = join(ROOT, "vendor", "fonts");
let tmp;
let E;

before(async () => {
  tmp = mkdtempSync(join(tmpdir(), "vvf-blocks-"));
  const out = join(tmp, "engine.mjs");
  await build({ entryPoints: [join(ROOT, "frontend", "src", "motion", "index.ts")], bundle: true, platform: "node", format: "esm", outfile: out, logLevel: "warning" });
  E = await import(pathToFileURL(out).href);
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
const shapeLayer = (props, over = {}) => ({ id: "s1", type: "shape", start: 0, end: 3, x: 0.5, y: 0.5, ...over, props: { w: 400, h: 240, fill: null, stroke: { color: "#ff0000", width: 14 }, ...props } });
const socialLayer = (props = {}, over = {}) => ({ id: "c1", type: "social", start: 0, end: 4, x: 0.5, y: 0.5, ...over, props: { style: "comment", name: "zritel", text: "Привет", likes: 10, ...props } });

/** Сколько непрозрачных пикселей и их рамка. */
function ink(cv) {
  const d = cv.getContext("2d").getImageData(0, 0, cv.width, cv.height).data;
  let n = 0;
  let x0 = Infinity;
  let y0 = Infinity;
  let x1 = -1;
  let y1 = -1;
  for (let i = 0; i < d.length; i += 4) {
    if (d[i + 3] < 40) continue;
    n++;
    const x = (i / 4) % cv.width;
    const y = Math.floor(i / 4 / cv.width);
    x0 = Math.min(x0, x);
    y0 = Math.min(y0, y);
    x1 = Math.max(x1, x);
    y1 = Math.max(y1, y);
  }
  return { n, x0, y0, x1, y1 };
}

function drawOne(layer, t, e = env()) {
  const cv = createCanvas(e.width, e.height);
  E.drawFrame(cv.getContext("2d"), { layers: [layer] }, t, e);
  return cv;
}

/* ---------- от руки ---------- */

test("от руки: дрожание детерминировано, seed меняет линию, концы стрелки стоят на месте", () => {
  const line = [];
  for (let i = 0; i <= 60; i++) line.push([-300 + i * 10, 0]);
  const a = E.roughen(line, 10, 1, true);
  const b = E.roughen(line, 10, 1, true);
  const c = E.roughen(line, 10, 2, true);
  assert.deepEqual(a, b);
  assert.ok(a.some((p, i) => Math.abs(p[1] - c[i][1]) > 1), "другой seed — другая линия");
  assert.ok(Math.max(...a.map((p) => Math.abs(p[1]))) > 3, "линия дрожит");
  assert.ok(Math.max(...a.map((p) => Math.abs(p[1]))) <= 10.01, "не дальше амплитуды");
  near(a[0][1], 0, 1e-9);
  near(a[a.length - 1][1], 0, 1e-9);
  assert.deepEqual(E.roughen(line, 0, 1, true), line);
});

test("от руки: круг не замыкается, а заходит сам на себя; рамка с перехлёстом", () => {
  const p = { kind: "ellipse", w: 400, h: 240, stroke: { color: "#f00", width: 14 } };
  const pts = E.sketchOutline(p, 0.6, 5, []);
  // Угол по ходу линии: больше полного оборота.
  let sweep = 0;
  for (let i = 1; i < pts.length; i++) {
    let d = Math.atan2(pts[i][1] / 120, pts[i][0] / 200) - Math.atan2(pts[i - 1][1] / 120, pts[i - 1][0] / 200);
    if (d > Math.PI) d -= 2 * Math.PI;
    if (d < -Math.PI) d += 2 * Math.PI;
    sweep += d;
  }
  assert.ok(sweep > 2 * Math.PI * 1.03 && sweep < 2 * Math.PI * 1.25, `оборот ${sweep / Math.PI / 2}`);
  const end = pts[pts.length - 1];
  assert.ok(Math.hypot(end[0] - pts[0][0], end[1] - pts[0][1]) > 10, "конец не совпадает с началом");
  const rect = E.sketchOutline({ kind: "rect", w: 400, h: 240 }, 0.6, 5, []);
  assert.ok(rect[rect.length - 1][0] > rect[0][0] + 20, "перехлёст в углу");
});

test("штриховка: зигзаг на всю рамку; курсор: остриё в левом верхнем углу", () => {
  const z = E.scribblePts(400, 100, 20, 0, 0);
  const xs = z.map((p) => p[0]);
  const ys = z.map((p) => p[1]);
  near(Math.min(...xs), -200, 1e-6);
  near(Math.max(...xs), 200, 1e-6);
  assert.ok(Math.min(...ys) >= -50 && Math.max(...ys) <= 50);
  const cur = E.cursorPts(60, 95);
  assert.deepEqual(cur[0], [-30, -47.5]);
  assert.ok(cur.every(([x, y]) => x >= -30 && x <= 30 && y >= -47.5 && y <= 47.5));
});

test("фигура rough рисуется (draw-вход рисует частями), boil меняет кадр, без rough — как раньше", () => {
  const rough = shapeLayer({ kind: "ellipse", rough: 0.6, seed: 3 }, { in: { type: "draw", duration: 1 } });
  const half = ink(drawOne(rough, 0.5)).n;
  const full = ink(drawOne(rough, 2)).n;
  assert.ok(half > 500 && full > half * 1.5, `${half} → ${full}`);
  // Перехлёст круга выходит за рамку w×h чуть-чуть, но остаётся в оценке measure.
  const box = ink(drawOne(rough, 2));
  assert.ok(box.x1 - box.x0 < 400 * 1.25 + 30 && box.x1 - box.x0 > 400 * 0.9, `ширина ${box.x1 - box.x0}`);
  const boil = shapeLayer({ kind: "line", rough: 0.8, boil: true, w: 600 });
  const f1 = drawOne(boil, 1.0).toBuffer("image/png");
  const f2 = drawOne(boil, 1.05).toBuffer("image/png");
  const f3 = drawOne(boil, 1.2).toBuffer("image/png");
  assert.ok(f1.equals(f2), "внутри 1/8 с кадр тот же");
  assert.ok(!f1.equals(f3), "через 1/8 с — новый рисунок");
  // Ровная фигура без rough не изменилась: замкнутый эллипс рисуется как раньше.
  const plain = ink(drawOne(shapeLayer({ kind: "ellipse" }), 2));
  near(plain.x1 - plain.x0, 414, 3);
  // Стрелка от руки: наконечник из штрихов виден у острия.
  const arrow = shapeLayer({ kind: "arrow", w: 400, rough: 0.6, seed: 1 }, { rotation: 0 });
  const cv = drawOne(arrow, 2);
  const d = cv.getContext("2d").getImageData(540 + 150, 960 - 60, 60, 120).data;
  let headInk = 0;
  for (let i = 3; i < d.length; i += 4) if (d[i] > 40) headInk++;
  assert.ok(headInk > 400, `наконечник ${headInk}`);
});

/* ---------- курсор и касания ---------- */

test("курсор и касания: путь по точкам, остриё — в точке пути, волна — там, где курсор в момент касания", () => {
  assert.deepEqual(E.parsePath("0.3 0.6 0; 0.62,0.45,1.2\n bad; 2 -1 0.5"), [
    { x: 0.3, y: 0.6, t: 0 },
    { x: 1, y: 0, t: 0.5 },
    { x: 0.62, y: 0.45, t: 1.2 },
  ]);
  assert.deepEqual(E.parseTimes("2.5, 1.3; x 0.4"), [0.4, 1.3, 2.5]);
  for (const id of ["cursor-clicks", "finger-taps"]) {
    const res = E.buildTemplate(id, { path: "0.2 0.7 0; 0.6 0.4 1; 0.6 0.4 1.5; 0.4 0.8 2.5", taps: "1.2, 3", duration: 4 }, 2);
    const [pointer, ...rest] = res.layers;
    assert.equal(pointer.type, "shape");
    assert.equal(pointer.props.kind, id === "cursor-clicks" ? "cursor" : "ellipse");
    const dx = id === "cursor-clicks" ? pointer.props.w / 2 / 1080 : 0;
    const dy = id === "cursor-clicks" ? pointer.props.h / 2 / 1920 : 0;
    // Остриё курсора в t=1 (локальное) — ровно в точке пути.
    near(E.interpolateKeys(pointer.keys.x, 1, 0) - dx, 0.6, 1e-3);
    near(E.interpolateKeys(pointer.keys.y, 1, 0) - dy, 0.4, 1e-3);
    // Два касания → по волне и вспышке на каждое, в момент касания и в точке курсора.
    assert.equal(rest.length, 4);
    const waves = rest.filter((l) => l.name === "Волна касания");
    assert.deepEqual(waves.map((l) => l.start), [3.2, 5]);
    near(waves[0].x, 0.6, 1e-3);
    near(waves[0].y, 0.4, 1e-3);
    near(waves[1].x + dx, E.interpolateKeys(pointer.keys.x, 3, 0), 1e-3);
    // Нажатие: масштаб ныряет в момент касания и возвращается.
    assert.ok(E.interpolateKeys(pointer.keys.scale, 1.2, 1) < 0.9);
    near(E.interpolateKeys(pointer.keys.scale, 1.6, 1), 1, 1e-6);
    // Волна гаснет (вспышка, а не содержимое): шаблон в другом формате её не учитывает (isTransient).
    assert.ok(waves[0].keys.opacity.at(-1).v === 0);
  }
  // Пустой или кривой путь — путь по умолчанию, а не пустой шаблон.
  assert.ok(E.buildTemplate("cursor-clicks", { path: "абв" }, 0).layers[0].keys.x.length >= 2);
});

test("стрелка от руки: остриё у цели, подпись в кадре; обвести и подчеркнуть — draw-вход", () => {
  for (const from of ["снизу слева", "снизу справа", "сверху слева", "сверху справа", "слева", "справа"]) {
    const [arrow, label] = E.buildTemplate("hand-arrow", { x: 0.7, y: 0.4, from, length: 400 }, 1).layers;
    const a = (arrow.rotation * Math.PI) / 180;
    const tipX = arrow.x * 1080 + Math.cos(a) * (arrow.props.w / 2);
    const tipY = arrow.y * 1920 + Math.sin(a) * (arrow.props.w / 2);
    assert.ok(Math.hypot(tipX - 0.7 * 1080, tipY - 0.4 * 1920) < 30, `${from}: остриё ${tipX},${tipY}`);
    assert.equal(arrow.in.type, "draw");
    assert.ok(arrow.props.rough > 0);
    // Подпись Caveat целиком в кадре (ширина на глаз — 0.42 кегля на букву).
    const half = (label.props.text.length * label.props.size * 0.42) / 2;
    assert.ok(label.x * 1080 - half >= 0 && label.x * 1080 + half <= 1080, `${from}: подпись за кадром`);
  }
  assert.equal(E.buildTemplate("hand-arrow", { text: "" }, 0).layers.length, 1);
  const circle = E.buildTemplate("hand-circle", { w: 400, h: 200 }, 0).layers[0];
  assert.equal(circle.props.kind, "ellipse");
  assert.ok(circle.props.w > 400 && circle.props.h > 200, "обводка с запасом вокруг области");
  const kinds = ["подчеркнуть", "дважды", "зачеркнуть", "маркером"].map((kind) => E.buildTemplate("hand-underline", { kind }, 0).layers);
  assert.deepEqual(kinds.map((ls) => ls.length), [1, 2, 1, 1]);
  assert.deepEqual(kinds.map((ls) => ls[0].props.kind), ["line", "line", "scribble", "scribble"]);
  assert.match(kinds[3][0].props.stroke.color, /^#[0-9a-fA-F]{6}80$/, "маркер полупрозрачный");
});

/* ---------- карточки соцсети ---------- */

test("карточка: счёт по-русски, лайки по ходу progress, высота по тексту, длинный текст — «…»", () => {
  assert.equal(E.formatSocialCount(128), "128");
  assert.equal(E.formatSocialCount(9876), "9 876");
  assert.equal(E.formatSocialCount(15400), "15,4 тыс.");
  assert.equal(E.formatSocialCount(120000), "120 тыс.");
  assert.equal(E.formatSocialCount(1250000), "1,2 млн");
  assert.equal(E.socialLikes({ likes: 100, likesTo: 300 }, 0.5), 200);
  assert.equal(E.socialLikes({ likes: 100 }, 0.5), 100);
  const ctx = createCanvas(10, 10).getContext("2d");
  const e = env();
  const st = { progress: 1 };
  const h = (props) => E.socialRenderer.measure(ctx, socialLayer(props), st, e, {}).h;
  const short = h({ text: "Коротко" });
  const long = h({ text: "Очень длинный комментарий зрителя ".repeat(4) });
  const huge = h({ text: "слово ".repeat(200) });
  assert.ok(long > short + 80, `${short} → ${long}`);
  assert.equal(huge, h({ text: "другое ".repeat(200) }), "сверх maxLines высота не растёт");
  assert.ok(h({ text: "слово ".repeat(200), maxLines: 3 }) < huge);
  // Пост: картинка 5:3 добавляет рамку по ширине текста.
  const post = (props) => E.socialRenderer.measure(ctx, socialLayer({ style: "post", ...props }), st, e, {}).h;
  near(post({ image: "7" }) - post({}), (900 - 80) / E.SOCIAL_IMAGE_RATIO + 30, 1);
});

test("карточка: рисуется (кириллица, эмодзи-аватар, картинка поста), картинки в списке ресурсов", () => {
  const layers = [
    socialLayer({ text: "Сова, ты опять перепутала 😂", avatar: "🦉", likesTo: 2400 }),
    socialLayer({ style: "post", theme: "light", image: "7", avatarImage: "sticker:a/b.png", replies: 3, reposts: 1, verified: true }, { id: "p1" }),
  ];
  const refs = E.collectImageRefs(layers);
  for (const r of ["emoji:1f602", "emoji:1f989", "7", "sticker:a/b.png"]) assert.ok(refs.includes(r), r);
  // Картинка комментария не нужна: image только у поста.
  assert.ok(!E.collectImageRefs([socialLayer({ image: "9" })]).includes("9"));
  const img = createCanvas(50, 30);
  const g = img.getContext("2d");
  g.fillStyle = "#00ff00";
  g.fillRect(0, 0, 50, 30);
  const e = env(1080, 1920, { 7: img });
  const cv = drawOne(layers[1], 2, e);
  const box = ink(cv);
  near(box.x1 - box.x0, 900, 6);
  const card = E.socialRenderer.measure(cv.getContext("2d"), layers[1], { progress: 1 }, e, {});
  assert.ok(card.h > 600);
  assert.ok(box.y1 - box.y0 >= card.h - 4);
  // Зелёная картинка поста — внутри карточки, в своей рамке 5:3.
  const g2 = cv.getContext("2d").getImageData(0, 0, 1080, 1920).data;
  let green = 0;
  for (let i = 0; i < g2.length; i += 4) if (g2[i] < 30 && g2[i + 1] > 220 && g2[i + 2] < 30) green++;
  near(green, 820 * (820 / E.SOCIAL_IMAGE_RATIO), 820 * 30);
  // Лайки: сердечко красное, когда счёт пошёл.
  const lit = drawOne(layers[0], 3.5);
  const px = lit.getContext("2d").getImageData(0, 0, 1080, 1920).data;
  let red = 0;
  for (let i = 0; i < px.length; i += 4) if (px[i] > 230 && px[i + 1] < 90 && px[i + 2] < 120 && px[i + 3] > 200) red++;
  assert.ok(red > 300, `красное сердечко ${red}`);
});

test("карточка: кадр компоновщика (рендер) = кадр движка (превью); шаблоны комментария и поста", async () => {
  const { GlobalFonts } = await import("@napi-rs/canvas");
  const fonts = JSON.parse(readFileSync(join(FONTS, "fonts.json"), "utf8"));
  for (const f of fonts.filter((x) => x.family === "Inter")) GlobalFonts.registerFromPath(join(FONTS, f.file), "Inter");
  E.invalidateTextCache();
  const comment = E.buildTemplate("comment-card", {}, 0).layers;
  const post = E.buildTemplate("post-card", { y: 0.3 }, 0).layers;
  assert.equal(comment[0].type, "social");
  assert.equal(comment[0].props.likesTo, 2400);
  assert.ok(comment[0].keys.progress.length === 2, "лайки считают вверх после появления");
  assert.equal(post[0].props.style, "post");
  assert.equal(post[0].props.image, undefined, "без картинки по умолчанию");
  assert.equal(E.buildTemplate("comment-card", { likesTo: 128 }, 0).layers[0].props.likesTo, undefined);
  assert.equal(E.buildTemplate("post-card", { replies: -1 }, 0).layers[0].props.replies, undefined);
  const layers = [...comment, ...post];
  writeFileSync(join(tmp, "s.json"), JSON.stringify({ width: 540, height: 960, fps: 30, duration: 6, layers }));
  writeFileSync(join(tmp, "s.assets.json"), "{}");
  const png = join(tmp, "s.png");
  const r = spawnSync(process.execPath, [CLI, "--comp", join(tmp, "s.json"), "--assets", join(tmp, "s.assets.json"), "--frame", "2", "--png", png, "--fonts-dir", FONTS], { encoding: "utf8", timeout: 120000 });
  assert.equal(r.status, 0, r.stderr);
  const a = createCanvas(540, 960);
  a.getContext("2d").drawImage(await loadImage(readFileSync(png)), 0, 0);
  const b = createCanvas(540, 960);
  E.drawFrame(b.getContext("2d"), { layers }, 2, env(540, 960));
  const da = a.getContext("2d").getImageData(0, 0, 540, 960).data;
  const db = b.getContext("2d").getImageData(0, 0, 540, 960).data;
  let diff = 0;
  let inked = 0;
  for (let i = 0; i < da.length; i += 4) {
    if (da[i + 3] > 0) inked++;
    if (Math.abs(da[i] - db[i]) > 2 || Math.abs(da[i + 1] - db[i + 1]) > 2 || Math.abs(da[i + 2] - db[i + 2]) > 2) diff++;
  }
  assert.ok(inked > 50000, `карточки не нарисованы (${inked})`);
  assert.ok(diff <= inked * 0.01, `расхождение рендера и превью: ${diff} из ${inked}`);
});

test("новые шаблоны: в галерее, по умолчанию не закрывают больше 40 % кадра", () => {
  for (const id of ["hand-arrow", "hand-circle", "hand-underline", "cursor-clicks", "finger-taps", "comment-card", "post-card"]) {
    const t = E.getTemplate(id);
    assert.ok(t && E.TEMPLATE_CATEGORIES.includes(t.category), id);
    for (const l of E.buildTemplate(id, {}, 0).layers) {
      const size = E.layerSize(l);
      if (!size) continue;
      const opaque = l.type === "social" || (l.type === "shape" && l.props.fill && !/^#[0-9a-f]{6}[0-7]/i.test(l.props.fill));
      if (opaque) assert.ok((size[0] * size[1]) / (1080 * 1920) <= 0.4, `${id} ${l.name}`);
    }
  }
});
