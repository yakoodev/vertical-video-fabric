// Тесты моушн-движка и компоновщика: `npm run test:motion` (или `node --test node/motion.test.mjs`
// после `npm run build:motion`). Движок из frontend/src/motion собирается esbuild во временную
// папку — тесты гоняют ровно тот TypeScript, что рисует превью и рендер.
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
  tmp = mkdtempSync(join(tmpdir(), "vvf-motion-"));
  const out = join(tmp, "engine.mjs");
  await build({
    entryPoints: [join(ROOT, "frontend", "src", "motion", "index.ts")],
    bundle: true,
    platform: "node",
    format: "esm",
    outfile: out,
    logLevel: "warning",
  });
  E = await import(pathToFileURL(out).href);
  if (!existsSync(CLI)) {
    const r = spawnSync("npm", ["run", "build:motion"], { cwd: ROOT, shell: true, encoding: "utf8" });
    assert.equal(r.status, 0, r.stderr);
  }
});

after(() => {
  if (tmp) rmSync(tmp, { recursive: true, force: true });
});

const near = (a, b, eps = 1e-6) => assert.ok(Math.abs(a - b) <= eps, `${a} ≉ ${b}`);

/* ---------- easing и ключи ---------- */

test("все кривые: 0 → 0, 1 → 1, вне отрезка зажаты", () => {
  for (const name of E.EASING_NAMES) {
    near(E.ease(name, 0), 0);
    near(E.ease(name, 1), 1);
    near(E.ease(name, -5), 0);
    near(E.ease(name, 7), 1);
  }
  // Перелёт: outBack на пути выходит за 1 (это «пружинка» pop).
  assert.ok(E.ease("outBack", 0.6) > 1);
  near(E.ease("step", 0.99), 0);
  near(E.ease("неизвестная", 0.5), E.ease("inOutCubic", 0.5));
});

test("bezier(x1,y1,x2,y2) — как CSS cubic-bezier", () => {
  near(E.ease("bezier(0,0,1,1)", 0.37), 0.37, 1e-4); // прямая
  near(E.ease("bezier(0.25,0.1,0.25,1)", 0.5), 0.8024, 2e-3); // CSS ease
  near(E.ease("bezier(0.3,1.6,0.6,1)", 0), 0);
  near(E.ease("bezier(0.3,1.6,0.6,1)", 1), 1);
  assert.ok(E.ease("bezier(0.3,1.6,0.6,1)", 0.5) > 1, "y > 1 даёт перелёт");
  near(E.ease("bezier(кривая)", 0.5), E.ease("inOutCubic", 0.5));
});

test("ключи: до первого — первое, после последнего — последнее, ease на отрезке слева", () => {
  const pts = [
    { t: 1, v: 10, ease: "linear" },
    { t: 2, v: 20, ease: "step" },
    { t: 3, v: 0 },
  ];
  near(E.interpolateKeys(pts, 0, 99), 10);
  near(E.interpolateKeys(pts, 1.5, 99), 15);
  near(E.interpolateKeys(pts, 2.5, 99), 20); // step держит значение до следующей точки
  near(E.interpolateKeys(pts, 3, 99), 0);
  near(E.interpolateKeys(pts, 10, 99), 0);
  near(E.interpolateKeys([], 1, 42), 42);
  // Неотсортированные точки тоже работают.
  near(E.interpolateKeys([pts[2], pts[0], pts[1]], 1.5, 99), 15);
});

/* ---------- анимации слоя ---------- */

const textLayer = (over = {}) => ({
  id: "t1",
  type: "text",
  start: 10,
  end: 14,
  x: 0.5,
  y: 0.5,
  scale: 1,
  rotation: 0,
  opacity: 1,
  props: { text: "Привет" },
  ...over,
});

test("слой виден на [start, end)", () => {
  const l = textLayer();
  assert.equal(E.resolveLayer(l, 9.99), null);
  assert.ok(E.resolveLayer(l, 10));
  assert.equal(E.resolveLayer(l, 14), null);
  assert.equal(E.resolveLayer({ ...l, hidden: true }, 11), null);
});

test("in/out комбинируются с ключами: масштаб и прозрачность умножаются, сдвиг складывается", () => {
  const l = textLayer({
    keys: { scale: [{ t: 0, v: 2 }, { t: 4, v: 2 }] },
    in: { type: "pop", duration: 0.4 },
    out: { type: "fade", duration: 0.5 },
  });
  near(E.resolveLayer(l, 10).scale, 0); // pop с нуля
  near(E.resolveLayer(l, 10.4).scale, 2); // вход закончился — ключ 2 как есть
  near(E.resolveLayer(l, 12).opacity, 1);
  const mid = E.resolveLayer(l, 13.75); // середина выхода fade
  near(mid.opacity, 0.5, 1e-6);
  near(mid.scale, 2);

  const slide = E.resolveLayer(textLayer({ y: 0.3, in: { type: "slideUp", duration: 1 } }), 10);
  near(slide.y, 0.3 + 0.15); // въезд снизу на 0.15 кадра
  const zoom = E.resolveLayer(textLayer({ opacity: 0.5, in: { type: "zoom", duration: 1 } }), 10.5);
  assert.ok(zoom.opacity < 0.5 && zoom.scale < 1 && zoom.scale > 0.6);
});

test("длительность входа не больше половины слоя; typewriter/words/draw/wipe дают доли", () => {
  near(E.animDuration({ type: "fade", duration: 3 }, 1, 0.35), 0.5);
  near(E.animDuration({ type: "fade", duration: 0.001 }, 4, 0.35), 0.05);
  assert.equal(E.animDuration({ type: "none", duration: 1 }, 4, 0.35), 0);
  const tw = E.resolveLayer(textLayer({ in: { type: "typewriter", duration: 1 } }), 10.25);
  assert.deepEqual(tw.reveal, { mode: "chars", fraction: 0.25 });
  const draw = E.resolveLayer({ ...textLayer(), type: "shape", props: { kind: "ellipse" }, in: { type: "draw", duration: 1 } }, 10.5);
  near(draw.draw, 0.5);
  // draw у не-фигуры превращается в wipe.
  const wipe = E.resolveLayer(textLayer({ in: { type: "draw", duration: 1 } }), 10.5);
  near(wipe.wipe, 0.5);
});

/* ---------- окно картинки (crop) ключами: скролл экрана внутри телефона ---------- */

const imageLayer = (over = {}) => ({ ...textLayer(), type: "image", props: { asset_id: "scr", w: 540 }, ...over });

test("crop: ключи cropY/cropX интерполируются, середина — между кадрами", () => {
  const l = imageLayer({
    props: { asset_id: "scr", w: 540, crop: { x: 0, y: 0, w: 1, h: 0.25 } },
    keys: { cropY: [{ t: 0, v: 0, ease: "linear" }, { t: 2, v: 0.75 }], cropX: [{ t: 1, v: 0.5, ease: "step" }, { t: 3, v: 0 }] },
  });
  const at = (t) => E.resolveLayer(l, 10 + t).crop;
  near(at(0).y, 0);
  near(at(1).y, 0.375); // середина линейного скролла
  near(at(2).y, 0.75);
  near(at(3.5).y, 0.75); // после последнего ключа — последнее значение
  // Размер окна (= размер слоя) не меняется, пока едет экран.
  for (const t of [0, 0.5, 1, 1.5, 2]) {
    near(at(t).w, 1);
    near(at(t).h, 0.25);
  }
  // cropX зажат так, чтобы окно шириной 1 не вышло за картинку.
  near(at(1).x, 0);
  // Без статичного crop база — вся картинка; ключ cropW сужает окно.
  const half = E.resolveLayer(imageLayer({ keys: { cropW: [{ t: 0, v: 1, ease: "linear" }, { t: 2, v: 0.5 }] } }), 11).crop;
  near(half.w, 0.75);
  near(half.h, 1);
  // У не-картинки окна нет, даже если ключи есть.
  assert.equal(E.resolveLayer(textLayer({ keys: { cropY: [{ t: 0, v: 0.5 }] } }), 11).crop, null);
});

test("crop: значения зажаты 0..1, у края окно стоит, а не сжимается", () => {
  near(E.clampCrop({ x: -1, y: 2, w: 0.3, h: 0.4 }).x, 0);
  near(E.clampCrop({ x: -1, y: 2, w: 0.3, h: 0.4 }).y, 0.6); // упор: 1 − h
  near(E.clampCrop({ x: 0, y: 0, w: 5, h: 0 }).w, 1);
  near(E.clampCrop({ x: 0, y: 0, w: 5, h: 0 }).h, 0.01);
  assert.deepEqual(E.clampCrop({}), { x: 0, y: 0, w: 1, h: 1 });
  assert.deepEqual(E.clampCrop({ x: NaN, y: 0.2, w: undefined, h: 0.5 }), { x: 0, y: 0.2, w: 1, h: 0.5 });
  // Перелёт кривой (outBack) и ключи за пределами не выводят окно за картинку.
  const l = imageLayer({
    props: { asset_id: "scr", w: 540, crop: { x: 0, y: 0, w: 1, h: 0.3 } },
    keys: { cropY: [{ t: 0, v: 0, ease: "outBack" }, { t: 1, v: 0.7 }, { t: 2, v: 1.5 }] },
  });
  for (let t = 0; t <= 2.5; t += 0.05) {
    const c = E.resolveLayer(l, 10 + t).crop;
    assert.ok(c.y >= 0 && c.y + c.h <= 1 + 1e-9, `t=${t}: y=${c.y}`);
    near(c.h, 0.3);
  }
});

test("crop: статичный crop как раньше, без crop — null (вся картинка)", () => {
  assert.equal(E.resolveLayer(imageLayer(), 11).crop, null);
  assert.equal(E.resolveLayer(imageLayer({ props: { asset_id: "scr", crop: null } }), 11).crop, null);
  const c = E.resolveLayer(imageLayer({ props: { asset_id: "scr", crop: { x: 0.1, y: 0.2, w: 0.5, h: 0.3 } } }), 11).crop;
  assert.deepEqual(c, { x: 0.1, y: 0.2, w: 0.5, h: 0.3 });
});

test("crop рисуется: экран едет внутри неподвижной рамки, radius обрезает картинку", () => {
  // «Скриншот» 100×400: четыре полосы сверху вниз — красная, зелёная, синяя, жёлтая.
  const src = createCanvas(100, 400);
  const sx = src.getContext("2d");
  ["#ff0000", "#00ff00", "#0000ff", "#ffff00"].forEach((c, i) => {
    sx.fillStyle = c;
    sx.fillRect(0, i * 100, 100, 100);
  });
  const env = { width: 1080, height: 1920, getImage: (ref) => (ref === "scr" ? src : null) };
  const l = imageLayer({
    props: { asset_id: "scr", w: 540, radius: 120, crop: { x: 0, y: 0, w: 1, h: 0.25 } },
    keys: { cropY: [{ t: 0, v: 0, ease: "linear" }, { t: 2, v: 0.75 }] },
  });
  const frame = (t) => {
    const cv = createCanvas(1080, 1920);
    const ctx = cv.getContext("2d");
    E.drawLayer(ctx, l, 10 + t, env);
    const box = E.layerBox(ctx, l, 10 + t, env);
    const px = (x, y) => [...ctx.getImageData(x, y, 1, 1).data];
    return { box, px };
  };
  const a = frame(0);
  const b = frame(1);
  const c = frame(2);
  // Рамка слоя одна и та же: 540×540 в центре кадра.
  for (const f of [a, b, c]) {
    near(f.box.w, 540, 1e-6);
    near(f.box.h, 540, 1e-6);
  }
  assert.deepEqual(a.px(540, 960).slice(0, 3), [255, 0, 0]); // в начале — первая полоса
  assert.deepEqual(c.px(540, 960).slice(0, 3), [255, 255, 0]); // в конце — последняя
  // Середина скролла: окно 0.375..0.625 — сверху зелёная, снизу синяя.
  assert.deepEqual(b.px(540, 960 - 200).slice(0, 3), [0, 255, 0]);
  assert.deepEqual(b.px(540, 960 + 200).slice(0, 3), [0, 0, 255]);
  // Угол за скруглением пуст: radius обрезает саму картинку, не только рамку.
  assert.equal(a.px(540 - 268, 960 - 268)[3], 0);
  assert.equal(a.px(540 - 260, 960)[3], 255);
});

test("loop детерминирован: тот же id и время — тот же результат", () => {
  const l = textLayer({ loop: { type: "shake", speed: 1, amount: 2 } });
  const a = E.resolveLayer(l, 11.234);
  const b = E.resolveLayer(structuredClone(l), 11.234);
  assert.deepEqual(a, b);
  assert.notDeepEqual(E.resolveLayer(l, 11.3), a);
  const other = E.resolveLayer({ ...l, id: "t2" }, 11.234);
  assert.notEqual(other.x, a.x); // разный id — разная тряска
});

test("прогресс по умолчанию: счётчик — за первую половину, график — за 40%", () => {
  const c = { ...textLayer(), type: "counter", props: { from: 0, to: 100 } };
  near(E.resolveLayer(c, 10).progress, 0);
  near(E.resolveLayer(c, 12).progress, 1);
  assert.ok(E.resolveLayer(c, 11).progress < 1);
  const ch = { ...textLayer(), type: "chart", props: { kind: "bar", data: [] } };
  near(E.resolveLayer(ch, 11.6).progress, 1);
  const withKeys = { ...c, keys: { progress: [{ t: 0, v: 0.2 }, { t: 4, v: 0.2 }] } };
  near(E.resolveLayer(withKeys, 13).progress, 0.2);
  assert.equal(E.formatCounter(1234567.891, { decimals: 2, thousands: " ", prefix: "$", suffix: "!" }), "$1 234 567,89!");
  assert.equal(E.formatCounter(-5, { decimals: 0, thousands: "" }), "−5");
});

test("порядок рисования: дорожка снизу вверх, внутри — по списку; audio не рисуется", () => {
  const ls = [
    textLayer({ id: "a", track: 2 }),
    textLayer({ id: "b", track: 0 }),
    textLayer({ id: "c", track: 2 }),
    { ...textLayer({ id: "d" }), type: "audio", props: { asset_id: "sfx:x/y" } },
  ];
  assert.deepEqual(E.visibleLayers(ls, 11).map((l) => l.id), ["b", "a", "c"]);
});

/* ---------- камера ---------- */

test("камера: зум 1..4, центр зажат так, что окно не выходит за кадр", () => {
  near(E.cameraView([], 3).zoom, 1);
  const v = E.cameraView([{ t: 0, zoom: 2, x: 0, y: 1 }], 1);
  near(v.zoom, 2);
  near(v.x, 0.25);
  near(v.y, 0.75);
  near(E.cameraView([{ t: 0, zoom: 0.3 }], 0).zoom, 1);
  near(E.cameraView([{ t: 0, zoom: 9 }], 0).zoom, 4);
  // Интерполяция между ключами с ease левого ключа.
  const keys = [
    { t: 1, zoom: 1, x: 0.5, y: 0.5, ease: "linear" },
    { t: 2, zoom: 2, x: 0.5, y: 0.5 },
  ];
  near(E.cameraView(keys, 1.5).zoom, 1.5);
  const m = E.cameraMatrix(keys, 2, 1080, 1920);
  near(m.scale, 2);
  near(m.tx, 540 - 0.5 * 1080 * 2);
  assert.ok(E.isIdentityCamera(E.cameraMatrix([], 0, 1080, 1920)));
});

test("тряска камеры никогда не показывает край кадра", () => {
  const keys = [
    { t: 0, zoom: 1, x: 0.5, y: 0.5, shake: 1 },
    { t: 5, zoom: 1.3, x: 0.02, y: 0.98, shake: 1 },
  ];
  for (let t = 0; t <= 6; t += 0.013) {
    const m = E.cameraMatrix(keys, t, 1080, 1920);
    assert.ok(m.scale >= 1);
    // Кадр основы [0, W] после матрицы должен накрывать холст [0, W] целиком.
    assert.ok(m.tx <= 1e-6 && m.tx + 1080 * m.scale >= 1080 - 1e-6, `x вылез на t=${t}`);
    assert.ok(m.ty <= 1e-6 && m.ty + 1920 * m.scale >= 1920 - 1e-6, `y вылез на t=${t}`);
  }
  const a = E.cameraMatrix(keys, 1.234, 1080, 1920);
  assert.deepEqual(a, E.cameraMatrix(keys, 1.234, 1080, 1920));
});

test("followCamera: слой едет и зумится с видео, без флага — стоит; в раскладке — по окну игры", () => {
  // Красный квадрат 40×40 опорных px в точке (0.6, 0.5); камера — зум 2 в центр.
  const square = (over = {}) => ({
    id: "sq", type: "shape", start: 0, end: 5, x: 0.6, y: 0.5,
    props: { kind: "rect", w: 40, h: 40, fill: "#ff0000" }, ...over,
  });
  const camera = [{ t: 0, zoom: 2, x: 0.5, y: 0.5 }];
  const env = { width: 1080, height: 1920, getImage: () => null };
  const redAt = (comp, x, y) => {
    const cv = createCanvas(1080, 1920);
    const ctx = cv.getContext("2d");
    E.drawFrame(ctx, comp, 1, env);
    const d = ctx.getImageData(x, y, 1, 1).data;
    return d[0] > 200 && d[3] > 200;
  };
  // Без флага — на месте (старые клипы не меняются).
  assert.ok(redAt({ layers: [square()], camera }, 648, 960));
  // С флагом — там, куда камера увела точку видео: 540 + (648 − 540)·2 = 756, и вдвое крупнее.
  const follow = { layers: [square({ followCamera: true })], camera };
  assert.ok(!redAt(follow, 648, 960));
  assert.ok(redAt(follow, 756, 960));
  assert.ok(redAt(follow, 756 + 35, 960)); // половина стороны ×2 = 40 px
  assert.ok(!redAt(follow, 756 + 45, 960));
  // Без ключей камеры флаг ничего не меняет.
  assert.ok(redAt({ layers: [square({ followCamera: true })] }, 648, 960));
  // Раскладка стрима: камера двигает окно игры (нижняя половина) — слой вместе с ним.
  const box = { x: 0, y: 960, w: 1080, h: 960 };
  const m = E.frameCameraMatrix(camera, 1, 1080, 1920, box);
  near(m.scale, 2);
  near(m.tx, -540);
  near(m.ty, -1440);
  const inGame = { layers: [square({ followCamera: true, y: 0.75 })], camera, cameraBox: box };
  assert.ok(redAt(inGame, 756, 1440));
  // С хромакеем кадр зумится целиком — окно игры не учитывается.
  // y 0.6 → 1152 px: по окну игры 2·1152 − 1440 = 864, во весь кадр 2·1152 − 960 = 1344.
  const keyed = { layers: [square({ followCamera: true, y: 0.6 })], camera, cameraBox: box };
  assert.ok(redAt(keyed, 756, 864));
  const key = { color: "0x00ff00", similarity: 0.25, blend: 0.08, despill: "green" };
  assert.ok(redAt({ ...keyed, key }, 756, 1344));
  assert.ok(!redAt({ ...keyed, key }, 756, 864));
  // Рамка выделения в редакторе — там же, где слой нарисован.
  const ctx = createCanvas(10, 10).getContext("2d");
  const lb = E.layerBox(ctx, square({ followCamera: true }), 1, env, undefined, E.cameraMatrix(camera, 1, 1080, 1920));
  near(lb.cx, 756);
  near(lb.w, 80);
  near(E.layerBox(ctx, square(), 1, env, undefined, E.cameraMatrix(camera, 1, 1080, 1920)).cx, 648);
});

/* ---------- шаблоны и ссылки ---------- */

test("шаблоны собираются в валидные слои с уникальными id", () => {
  assert.ok(E.TEMPLATES.length >= 20);
  const ids = new Set();
  for (const tpl of E.TEMPLATES) {
    assert.ok(tpl.name && tpl.category && tpl.description, tpl.id);
    assert.ok(E.TEMPLATE_CATEGORIES.includes(tpl.category), tpl.id);
    const res = E.buildTemplate(tpl.id, {}, 5);
    assert.ok(res.layers.length > 0, tpl.id);
    for (const l of res.layers) {
      assert.match(l.id, /^[A-Za-z0-9_-]{1,32}$/);
      assert.ok(!ids.has(l.id), `повтор id ${l.id}`);
      ids.add(l.id);
      assert.ok(l.end - l.start >= 0.1, `${tpl.id}: слой короче 0.1 с`);
      assert.ok(l.start >= 5 - 0.011, `${tpl.id}: слой раньше момента вставки`);
    }
  }
  assert.equal(E.buildTemplate("нет-такого", {}, 0), null);
  const boom = E.buildTemplate("boom", {}, 3);
  assert.ok(boom.camera.some((k) => k.shake > 0));
  assert.ok(boom.layers.some((l) => l.type === "audio"));
});

test("ссылки на картинки: Twemoji-имена и эмодзи внутри текста", () => {
  assert.equal(E.emojiRef("🔥"), "emoji:1f525");
  assert.equal(E.emojiRef("❤️"), "emoji:2764"); // FE0F без ZWJ выкидывается
  assert.equal(E.emojiRef("👁️‍🗨️"), "emoji:1f441-fe0f-200d-1f5e8-fe0f");
  const refs = E.collectImageRefs([
    textLayer({ props: { text: "Огонь 🔥 и 😂" } }),
    { ...textLayer(), type: "image", props: { asset_id: 7 } },
    { ...textLayer(), type: "callout", props: { title: "x", icon: "👑" } },
  ]);
  assert.deepEqual(refs.sort(), ["7", "emoji:1f451", "emoji:1f525", "emoji:1f602"].sort());
});

/* ---------- компоновщик целиком ---------- */

const ffprobe = (file) => {
  const r = spawnSync(
    "ffprobe",
    ["-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries", "stream=width,height,nb_read_frames,codec_name,pix_fmt:format=duration", "-of", "json", file],
    { encoding: "utf8" },
  );
  assert.equal(r.status, 0, r.stderr);
  const j = JSON.parse(r.stdout);
  return { ...j.streams[0], duration: Number(j.format.duration) };
};

const EMOJI_SVG = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 36 36"><circle cx="18" cy="18" r="17" fill="#FFAC33"/><circle cx="12" cy="14" r="3" fill="#664500"/><circle cx="24" cy="14" r="3" fill="#664500"/><path d="M9 22c3 6 15 6 18 0" stroke="#664500" stroke-width="3" fill="none"/></svg>`;

/** Слой каждого типа — дымовой тест, что все рендереры отрабатывают без ошибок. */
function everyTypeComp(duration) {
  const at = 0;
  const L = (o) => ({ x: 0.5, y: 0.5, scale: 1, rotation: 0, opacity: 1, track: 0, start: at, end: duration, ...o });
  return {
    width: 1080,
    height: 1920,
    fps: 30,
    duration,
    camera: [
      { t: 0, zoom: 1, x: 0.5, y: 0.5 },
      { t: 0.8, zoom: 1.4, x: 0.4, y: 0.35, shake: 0.6 },
      { t: 1.6, zoom: 1, x: 0.5, y: 0.5 },
    ],
    layers: [
      L({ id: "txt", type: "text", y: 0.1, in: { type: "pop", duration: 0.3 }, shadow: { color: "#000000", blur: 20, y: 8, opacity: 0.6 }, props: { text: "Привет, мир 😀", font: "Montserrat", weight: 900, size: 110, color: "#FFD400", stroke: { color: "#000000", width: 10 }, uppercase: true } }),
      L({ id: "txt2", type: "text", y: 0.2, in: { type: "words", duration: 0.6 }, props: { text: "Плашка с выделенным словом", size: 64, color: "#ffffff", bg: { color: "#111111", opacity: 0.85, radius: 18, padX: 24, padY: 12, skew: -8 }, highlight: { words: [2], color: "#111111", bg: "#FFD400" }, maxWidth: 800 } }),
      L({ id: "rect", type: "shape", x: 0.25, y: 0.32, props: { kind: "rect", w: 260, h: 160, fill: "#8B5CF6", radius: 28, stroke: { color: "#ffffff", width: 6 } } }),
      L({ id: "arrow", type: "shape", x: 0.7, y: 0.32, rotation: -30, in: { type: "draw", duration: 0.6 }, props: { kind: "arrow", w: 300, bend: 50, fill: null, stroke: { color: "#FF2D55", width: 18 } } }),
      L({ id: "star", type: "shape", x: 0.2, y: 0.45, loop: { type: "spin", speed: 1, amount: 1 }, props: { kind: "star", w: 160, h: 160, points: 5, fill: "#FFD400", stroke: null } }),
      L({ id: "burst", type: "shape", x: 0.5, y: 0.45, props: { kind: "burst", w: 220, h: 180, points: 12, fill: "#FF7A00", stroke: { color: "#ffffff", width: 6 }, dash: true } }),
      L({ id: "ell", type: "shape", x: 0.8, y: 0.45, props: { kind: "ellipse", w: 180, h: 140, fill: null, stroke: { color: "#22D3EE", width: 10 }, dash: true } }),
      L({ id: "tri", type: "shape", x: 0.8, y: 0.55, blend: "screen", props: { kind: "triangle", w: 140, h: 120, fill: "#2EE59D" } }),
      L({ id: "line", type: "shape", x: 0.2, y: 0.55, props: { kind: "line", w: 200, stroke: { color: "#ffffff", width: 8 } } }),
      L({ id: "img", type: "image", x: 0.5, y: 0.56, opacity: 0.8, props: { asset_id: "smile", w: 160, radius: 40, border: { color: "#ffffff", width: 6 }, flipX: true } }),
      L({ id: "emo", type: "emoji", x: 0.35, y: 0.56, loop: { type: "float" }, props: { char: "😀", size: 120 } }),
      L({ id: "cnt", type: "counter", y: 0.64, props: { from: 0, to: 1000000, thousands: " ", size: 100, color: "#ffffff", stroke: { color: "#000000", width: 8 } } }),
      L({ id: "prg", type: "progress", y: 0.7, props: { w: 700, h: 50, value: 0.73, fill: ["#6D28D9", "#C084FC"], label: true } }),
      L({ id: "bar", type: "chart", x: 0.27, y: 0.82, scale: 0.5, props: { kind: "bar", title: "Столбцы", data: [{ label: "А", value: 3 }, { label: "Б", value: 5 }, { label: "В", value: 8 }] } }),
      L({ id: "pie", type: "chart", x: 0.73, y: 0.82, scale: 0.5, props: { kind: "donut", centerLabel: "Итог", data: [{ label: "Да", value: 70 }, { label: "Нет", value: 30 }] } }),
      L({ id: "line2", type: "chart", x: 0.27, y: 0.95, scale: 0.35, props: { kind: "line", data: [{ label: "1", value: 2 }, { label: "2", value: 5 }, { label: "3", value: 3 }] } }),
      L({ id: "hbar", type: "chart", x: 0.73, y: 0.95, scale: 0.35, props: { kind: "hbar", data: [{ label: "X", value: 2 }, { label: "Y", value: 5 }] } }),
      L({ id: "co", type: "callout", y: 0.77, in: { type: "wipe", duration: 0.5 }, props: { title: "Нижняя треть", subtitle: "подзаголовок", style: "neon", accent: "#22D3EE", icon: "😀", skew: -6 } }),
      L({ id: "snd", type: "audio", props: { asset_id: "sfx:x/boom.mp3" } }),
    ],
  };
}

function writeInputs(name, comp) {
  const svg = join(tmp, "smile.svg");
  writeFileSync(svg, EMOJI_SVG);
  const assets = { smile: svg, "emoji:1f600": svg };
  writeFileSync(join(tmp, `${name}.assets.json`), JSON.stringify(assets));
  writeFileSync(join(tmp, `${name}.json`), JSON.stringify(comp));
  return { comp: join(tmp, `${name}.json`), assets: join(tmp, `${name}.assets.json`) };
}

const run = (args) => spawnSync(process.execPath, [CLI, ...args, "--fonts-dir", FONTS], { encoding: "utf8", timeout: 300000 });

test("CLI: один кадр в PNG без основы (прозрачный фон)", () => {
  const p = writeInputs("frame", everyTypeComp(3));
  const png = join(tmp, "frame.png");
  const r = run(["--comp", p.comp, "--assets", p.assets, "--frame", "1.2", "--png", png]);
  assert.equal(r.status, 0, r.stderr);
  const buf = readFileSync(png);
  assert.equal(buf.subarray(1, 4).toString(), "PNG");
  assert.equal(buf.readUInt32BE(16), 1080);
  assert.equal(buf.readUInt32BE(20), 1920);
});

test("CLI: 2 с видео поверх синтетической основы — длительность, размер, кадры", () => {
  const base = join(tmp, "base.mp4");
  const g = spawnSync("ffmpeg", ["-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=30", "-t", "2", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", base], { encoding: "utf8" });
  assert.equal(g.status, 0, g.stderr);
  const p = writeInputs("video", everyTypeComp(2));
  const out = join(tmp, "motion.mp4");
  const t0 = Date.now();
  const r = run(["--comp", p.comp, "--assets", p.assets, "--input", base, "--output", out, "--crf", "20", "--preset", "veryfast"]);
  const sec = (Date.now() - t0) / 1000;
  assert.equal(r.status, 0, r.stderr);
  assert.match(r.stderr, /progress 60\/60/);
  const info = ffprobe(out);
  assert.equal(info.width, 1080);
  assert.equal(info.height, 1920);
  assert.equal(info.codec_name, "h264");
  assert.equal(info.pix_fmt, "yuv420p");
  assert.equal(Number(info.nb_read_frames), 60);
  near(info.duration, 2, 0.05);
  console.log(`# компоновщик: 60 кадров 1080×1920 за ${sec.toFixed(1)} с ≈ ${(60 / sec).toFixed(1)} кадр/с (вместе с запуском)`);
});

test("CLI: основа короче композиции — последний кадр держится; .mov — с альфой", () => {
  const base = join(tmp, "short.mp4");
  const g = spawnSync("ffmpeg", ["-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=540x960:rate=30", "-t", "0.5", "-pix_fmt", "yuv420p", base], { encoding: "utf8" });
  assert.equal(g.status, 0, g.stderr);
  const comp = { ...everyTypeComp(1), width: 270, height: 480 };
  const p = writeInputs("short", comp);
  const out = join(tmp, "short.mp4.out.mp4");
  const r = run(["--comp", p.comp, "--assets", p.assets, "--input", base, "--output", out]);
  assert.equal(r.status, 0, r.stderr);
  assert.equal(Number(ffprobe(out).nb_read_frames), 30);

  const mov = join(tmp, "alpha.mov");
  const r2 = run(["--comp", p.comp, "--assets", p.assets, "--output", mov]);
  assert.equal(r2.status, 0, r2.stderr);
  const info = ffprobe(mov);
  assert.equal(info.codec_name, "prores");
  assert.match(info.pix_fmt, /yuva/);

  // Кадр за концом основы — берётся её последний кадр, а не ошибка.
  const r3 = run(["--comp", p.comp, "--assets", p.assets, "--input", base, "--frame", "0.9", "--png", join(tmp, "late.png")]);
  assert.equal(r3.status, 0, r3.stderr);
});

test("CLI: понятные ошибки аргументов", () => {
  const r = spawnSync(process.execPath, [CLI, "--comp"], { encoding: "utf8" });
  assert.equal(r.status, 2);
  assert.match(r.stderr, /ошибка аргументов/);
  const r2 = spawnSync(process.execPath, [CLI, "--comp", join(tmp, "nope.json"), "--output", join(tmp, "x.mp4")], { encoding: "utf8" });
  assert.equal(r2.status, 1);
});

/* ---------- анимированные субтитры ---------- */

const SPEECH = [
  { t0: 0.3, t1: 0.7, w: "Ребята," },
  { t0: 0.75, t1: 1.0, w: "вы" },
  { t0: 1.05, t1: 1.3, w: "это" },
  { t0: 1.35, t1: 1.8, w: "видите?" }, // конец предложения
  { t0: 1.9, t1: 2.2, w: "Он" },
  { t0: 3.5, t1: 3.9, w: "сделал" }, // пауза 1.3 с перед словом
  { t0: 3.95, t1: 4.3, w: "это!" },
];

test("субтитры: пресет + свои props, null перекрывает, рамки зажаты", () => {
  const r = E.resolveCaptions({ style: "box", size: 70, activeBg: null, maxWords: 20, wordScale: 3 });
  assert.equal(r.size, 70);
  assert.equal(r.activeBg, null); // явный null выключает плашку пресета
  assert.equal(r.font, "Montserrat"); // остальное — из пресета box
  assert.equal(r.maxWords, 8);
  assert.equal(r.wordScale, 1.4);
  assert.equal(E.resolveCaptions({ style: "нет-такого" }).activeColor, E.resolveCaptions({}).activeColor);
  assert.ok(E.CAPTION_STYLES.length >= 10);
  for (const s of E.CAPTION_STYLES) assert.ok(s.id && s.name && s.description && s.props, s.id);
});

test("субтитры: страницы по числу слов, паузам и концу предложения", () => {
  const ctx = createCanvas(8, 8).getContext("2d");
  const pages = E.captionPages(ctx, SPEECH, { style: "hormozi", maxWords: 3 }, 0, 10, "Montserrat");
  assert.deepEqual(pages.map((p) => p.words.map((w) => w.text)), [
    ["РЕБЯТА", "ВЫ"], // фраза из 4 слов при maxWords 3 — поровну, 2 + 2
    ["ЭТО", "ВИДИТЕ"],
    ["ОН"], // после «видите?» — новое предложение, после «Он» — пауза
    ["СДЕЛАЛ", "ЭТО"],
  ]);
  // Страницы не перекрываются; короткий зазор перекрыт, пауза — пустой экран.
  for (let k = 1; k < pages.length; k++) assert.ok(pages[k - 1].t1 <= pages[k].t0 + 1e-9);
  assert.equal(pages[0].t1, pages[1].t0);
  assert.equal(E.captionPageAt(pages, 2.9), null);
  assert.equal(E.captionPageAt(pages, 1.0).index, 0);
  assert.equal(E.captionPageAt(pages, 4.0).index, 3);
  // Окно слоя: слова до start и после end не показываются.
  const late = E.captionPages(ctx, SPEECH, { style: "hormozi" }, 3, 10, "Montserrat");
  assert.deepEqual(late.flatMap((p) => p.words.map((w) => w.i)), [5, 6]);
  // Знаки препинания у «минимала» остаются, регистр — как сказано.
  const minimal = E.captionPages(ctx, SPEECH, { style: "minimal" }, 0, 10, "Montserrat");
  assert.equal(minimal[0].words[0].text, "Ребята,");
  assert.deepEqual(E.captionWordsFrom([{ word: "да", start: 1, end: 2 }]), [{ t0: 1, t1: 2, w: "да" }]);
});

/** Сколько непрозрачных пикселей в горизонтальной полосе кадра. */
async function inkInBand(png, y0, y1) {
  const img = await loadImage(readFileSync(png));
  const c = createCanvas(img.width, img.height);
  const cx = c.getContext("2d");
  cx.drawImage(img, 0, 0);
  const data = cx.getImageData(0, Math.round(img.height * y0), img.width, Math.round(img.height * (y1 - y0))).data;
  let n = 0;
  for (let i = 3; i < data.length; i += 4) if (data[i] > 40) n++;
  return n;
}

test("CLI: слой субтитров рисует слова из comp.words каждым стилем и молчит в паузе", async () => {
  for (const style of E.CAPTION_STYLES.map((s) => s.id)) {
    const comp = {
      width: 540,
      height: 960,
      fps: 30,
      duration: 5,
      words: SPEECH,
      layers: [{ id: "cap", type: "captions", start: 0, end: 5, x: 0.5, y: 0.68, props: { style, emoji: [{ i: 1, char: "😀" }] } }],
    };
    const p = writeInputs(`cap-${style}`, comp);
    const png = join(tmp, `cap-${style}.png`);
    const r = run(["--comp", p.comp, "--assets", p.assets, "--frame", "0.9", "--png", png]);
    assert.equal(r.status, 0, r.stderr);
    assert.ok((await inkInBand(png, 0.55, 0.8)) > 500, `${style}: нет текста в зоне субтитров`);
    assert.equal(await inkInBand(png, 0.82, 1), 0, `${style}: залез под интерфейс площадки`);
  }
  const p = writeInputs("cap-pause", {
    width: 540, height: 960, fps: 30, duration: 5, words: SPEECH,
    layers: [{ id: "cap", type: "captions", start: 0, end: 5, y: 0.68, props: {} }],
  });
  const png = join(tmp, "cap-pause.png");
  assert.equal(run(["--comp", p.comp, "--assets", p.assets, "--frame", "2.9", "--png", png]).status, 0);
  assert.equal(await inkInBand(png, 0, 1), 0);
});

/* ---------- чат стрима ---------- */

const CHAT = [
  { t: 0.5, user: "Tanya", color: "#FF69B4", text: "привет KEKW", fragments: [{ text: "привет " }, { emote: "KEKW", url: "https://cdn.test/kekw.png" }], badges: [{ id: "sub/1", url: "https://cdn.test/sub.png" }] },
  { t: 0.9, user: "bot", color: "#00FF00", text: "!розыгрыш" },
  { t: 1.2, user: "spam", color: "#00FF00", text: "го www.example.com" },
  { t: 1.6, user: "Дима", color: "#1E90FF", text: "ЧТО ОН СДЕЛАЛ" },
  { t: 2.4, user: "a", text: "+" },
  { t: 3.0, user: "Мод", color: "#9ACD32", text: "имба" },
];

test("чат: фильтры, задержка и окно слоя", () => {
  const r = E.resolveChat({ maxMessages: 40, filter: { minLength: 2 } });
  assert.equal(r.maxMessages, 12);
  assert.deepEqual(r.filter, { minLength: 2, hideLinks: true, hideCommands: true });
  assert.equal(E.resolveChat({ style: "irc" }).style, "twitch-dark");
  const props = { filter: { minLength: 2 }, delay: 0.5 };
  const list = E.chatEntries(CHAT, props, 0, 2.5);
  // Команда, ссылка и «+» короче 2 символов отброшены; время показа — t − delay; 3.0 − 0.5 = 2.5 — уже за концом окна [0, 2.5).
  assert.deepEqual(list.map((e) => [e.msg.user, e.ts]), [["Tanya", 0], ["Дима", 1.1]]);
  assert.deepEqual(E.collectChatImageRefs(CHAT).sort(), ["url:https://cdn.test/kekw.png", "url:https://cdn.test/sub.png"]);
  assert.deepEqual(E.collectChatImageRefs(CHAT, { showEmotes: false, showBadges: false }), []);
  assert.equal(E.assetUrl("url:https://cdn.test/kekw.png"), "https://cdn.test/kekw.png");
  assert.ok(E.CHAT_STYLES.length === 4 && E.getTemplate("stream-chat"));
});

test("CLI: слой чата рисует сообщения с эмоутами каждым стилем, до первого сообщения — пусто", async () => {
  // Эмоут и значок — настоящие PNG, как из кэша бэкенда.
  const emote = createCanvas(56, 56);
  const ec = emote.getContext("2d");
  ec.fillStyle = "#FFB000";
  ec.beginPath();
  ec.arc(28, 28, 26, 0, Math.PI * 2);
  ec.fill();
  writeFileSync(join(tmp, "kekw.png"), await emote.encode("png"));
  writeFileSync(join(tmp, "sub.png"), await emote.encode("png"));
  const assets = { "url:https://cdn.test/kekw.png": join(tmp, "kekw.png"), "url:https://cdn.test/sub.png": join(tmp, "sub.png") };
  writeFileSync(join(tmp, "chat.assets.json"), JSON.stringify(assets));
  for (const style of E.CHAT_STYLES.map((s) => s.id)) {
    const comp = {
      width: 540, height: 960, fps: 30, duration: 5, chat: CHAT,
      layers: [{ id: "chat", type: "chat", start: 0, end: 5, x: 0.5, y: 0.4, props: { style, highlightUsers: ["Мод"] } }],
    };
    writeFileSync(join(tmp, `chat-${style}.json`), JSON.stringify(comp));
    const png = join(tmp, `chat-${style}.png`);
    const r = run(["--comp", join(tmp, `chat-${style}.json`), "--assets", join(tmp, "chat.assets.json"), "--frame", "3.5", "--png", png]);
    assert.equal(r.status, 0, r.stderr);
    assert.ok((await inkInBand(png, 0.2, 0.6)) > 2000, `${style}: сообщений не видно`);
    assert.equal(await inkInBand(png, 0.7, 1), 0, `${style}: чат вылез из своей колонки вниз`);
  }
  const png = join(tmp, "chat-early.png");
  const r = run(["--comp", join(tmp, "chat-twitch-dark.json"), "--assets", join(tmp, "chat.assets.json"), "--frame", "0.3", "--png", png]);
  assert.equal(r.status, 0, r.stderr);
  assert.equal(await inkInBand(png, 0, 1), 0);
});

test("субтитры: два голоса — разные страницы и цвет второго", () => {
  const ctx = createCanvas(8, 8).getContext("2d");
  const talk = [
    { t0: 0.0, t1: 0.3, w: "ну", s: "A" },
    { t0: 0.3, t1: 0.6, w: "что", s: "A" },
    { t0: 0.6, t1: 0.9, w: "привет", s: "B" }, // второй голос вступил без паузы
    { t0: 0.9, t1: 1.2, w: "всем", s: "B" },
  ];
  const pages = E.captionPages(ctx, talk, { style: "hormozi", maxWords: 4 }, 0, 5, "Montserrat");
  assert.deepEqual(pages.map((p) => p.words.map((w) => w.s)), [["A", "A"], ["B", "B"]]);
  const r = E.resolveCaptions({ style: "hormozi" });
  assert.equal(E.speakerColor(r, "A"), null);
  assert.ok(E.speakerColor(r, "B"));
  const own = E.resolveCaptions({ style: "hormozi", speakerColors: [{ s: "B", color: "#ff00ff" }] });
  assert.equal(E.speakerColor(own, "B"), "#ff00ff");
  assert.deepEqual(E.captionWordsFrom([{ word: "да", start: 1, end: 2, speaker: "B" }]), [{ t0: 1, t1: 2, w: "да", s: "B" }]);
});

test("субтитры: фильтр голосов — главный слой без чужих слов, второй — только они", () => {
  const ctx = createCanvas(8, 8).getContext("2d");
  const talk = [
    { t0: 0.0, t1: 0.3, w: "я", s: "A" },
    { t0: 0.2, t1: 0.5, w: "передач", s: "B" }, // говорят одновременно
    { t0: 0.4, t1: 0.7, w: "смотрю" }, // без метки — главный
  ];
  const main = E.captionPages(ctx, talk, { style: "hormozi", speakers: ["A"] }, 0, 5, "Montserrat");
  assert.deepEqual(main.flatMap((p) => p.words.map((w) => w.text)), ["Я", "СМОТРЮ"]);
  const others = E.captionPages(ctx, talk, { style: "hormozi", speakers: ["B", "C", "D"] }, 0, 5, "Montserrat");
  assert.deepEqual(others.flatMap((p) => p.words.map((w) => w.text)), ["ПЕРЕДАЧ"]);
});

/* ---------- анимированные фоны ---------- */

test("фон: старый {color, color2} — заливка, не выбран — аврора, имя стиля строкой", () => {
  const A = { kind: "aurora", colors: [], speed: 1, intensity: 0.7 };
  assert.deepEqual(E.normalizeBackground(null), A);
  assert.deepEqual(E.normalizeBackground({ color: "#0b0d14", color2: "" }), A); // фон никто не выбирал
  assert.deepEqual(E.normalizeBackground({ color: "#FF0000" }), { ...A, kind: "solid", colors: ["#ff0000"] });
  assert.deepEqual(E.normalizeBackground({ color: "#112233", color2: "#445566" }), { ...A, kind: "gradient", colors: ["#112233", "#445566"] });
  assert.deepEqual(E.normalizeBackground("grid"), { ...A, kind: "grid" });
  assert.deepEqual(E.normalizeBackground({ kind: "waves", colors: ["#ABCDEF", "bad"], speed: 9, intensity: -2 }), { kind: "waves", colors: ["#abcdef"], speed: 4, intensity: 0 });
  // Палитра «в цветах клипа»: тёмная основа + акцент субтитров; белый акцент не годится.
  assert.equal(E.backgroundPalette(A, "#ff6b2c")[1], "#ff6b2c");
  assert.equal(E.backgroundPalette(A, "#ffffff")[1], E.DEFAULT_ACCENT);
  assert.equal(E.clipAccent([{ id: "c", type: "captions", props: { style: "hormozi" } }]), "#ffe81f");
});

function bgPixels(bg, t, w = 216, h = 384) {
  const cv = createCanvas(w, h);
  E.paintBackground(cv.getContext("2d"), bg, t, w, h, { accent: "#22d3ee", createCanvas: (a, b) => createCanvas(a, b) });
  return cv.getContext("2d").getImageData(0, 0, w, h).data;
}

const maxDiff = (a, b) => {
  let m = 0;
  for (let i = 0; i < a.length; i++) m = Math.max(m, Math.abs(a[i] - b[i]));
  return m;
};

test("фон: цикл бесшовный — кадр в t и t + цикл совпадает, а в середине цикла другой", () => {
  for (const kind of E.ANIMATED_BACKGROUNDS) {
    for (const speed of [1, 1.7]) {
      const bg = E.normalizeBackground({ kind, speed });
      const loop = E.backgroundLoop(bg);
      for (const t of [0, 0.37, 4.2]) {
        assert.ok(maxDiff(bgPixels(bg, t), bgPixels(bg, t + loop)) <= 1, `${kind} ×${speed}: шов на t=${t}`);
      }
      assert.ok(maxDiff(bgPixels(bg, 0.37), bgPixels(bg, 0.37 + loop / 2)) > 20, `${kind}: фон не двигается`);
    }
  }
});

test("CLI: зона фона рисуется вне видео, видео в дыре не тронуто; кадр 1080×1920 быстрый", async () => {
  const base = join(tmp, "bgbase.mp4");
  const g = spawnSync("ffmpeg", ["-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0xff0000:size=1080x1920:rate=30", "-t", "1", "-pix_fmt", "yuv420p", base], { encoding: "utf8" });
  assert.equal(g.status, 0, g.stderr);
  const hole = { x: 0, y: 78, w: 1080, h: 604 };
  for (const kind of E.ANIMATED_BACKGROUNDS) {
    const p = writeInputs(`bgzone-${kind}`, { width: 1080, height: 1920, fps: 30, duration: 1, layers: [], backgrounds: [{ t0: 0, t1: 5, bg: { kind }, hole }] });
    const png = join(tmp, `bgzone-${kind}.png`);
    const r = run(["--comp", p.comp, "--assets", p.assets, "--input", base, "--frame", "0.5", "--png", png]);
    assert.equal(r.status, 0, r.stderr);
    const cv = createCanvas(1080, 1920);
    const ctx = cv.getContext("2d");
    ctx.drawImage(await loadImage(readFileSync(png)), 0, 0);
    const px = (x, y) => [...ctx.getImageData(x, y, 1, 1).data.slice(0, 3)];
    const red = ([r0, g0, b0]) => r0 > 200 && g0 < 40 && b0 < 40;
    assert.ok(red(px(540, 380)), `${kind}: видео в дыре закрыто фоном`);
    assert.ok(!red(px(540, 1400)) && !red(px(540, 30)), `${kind}: вне видео нет фона`);
  }
  // Скорость: 30 кадров фона-сетки (самый тяжёлый: линии на весь кадр) во весь кадр.
  const p = writeInputs("bgspeed", { width: 1080, height: 1920, fps: 30, duration: 1, layers: [], backgrounds: [{ t0: 0, t1: 5, bg: { kind: "grid" } }] });
  const t0 = Date.now();
  const r = run(["--comp", p.comp, "--assets", p.assets, "--output", join(tmp, "bgspeed.mp4"), "--preset", "ultrafast"]);
  assert.equal(r.status, 0, r.stderr);
  console.log(`# фон grid: 30 кадров 1080×1920 за ${((Date.now() - t0) / 1000).toFixed(1)} с (с запуском и кодированием)`);
});

test("CLI: хромакей кладёт анимированный фон, старый {color} — заливкой", async () => {
  const base = join(tmp, "green.mp4");
  const g = spawnSync("ffmpeg", ["-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x00fe00:size=270x480:rate=30", "-t", "1", "-pix_fmt", "yuv420p", base], { encoding: "utf8" });
  assert.equal(g.status, 0, g.stderr);
  const key = { color: "0x00fe00", similarity: 0.3, blend: 0.05 };
  const colorAt = async (background, x, y) => {
    const p = writeInputs("keyed", { width: 270, height: 480, fps: 30, duration: 1, layers: [], key, background });
    const png = join(tmp, "keyed.png");
    const r = run(["--comp", p.comp, "--assets", p.assets, "--input", base, "--frame", "0.5", "--png", png]);
    assert.equal(r.status, 0, r.stderr);
    const cv = createCanvas(270, 480);
    cv.getContext("2d").drawImage(await loadImage(readFileSync(png)), 0, 0);
    return [...cv.getContext("2d").getImageData(x, y, 1, 1).data.slice(0, 3)];
  };
  assert.deepEqual(await colorAt({ color: "#336699" }, 135, 240), [0x33, 0x66, 0x99]);
  const a = await colorAt({ kind: "aurora", colors: ["#101020", "#ff00aa"] }, 135, 120);
  const b = await colorAt({ kind: "aurora", colors: ["#101020", "#ff00aa"] }, 135, 400);
  assert.notDeepEqual(a, b, "аврора — не одна заливка");
});

test("CLI: хромакей — despill auto рыжит жёлтое, none оставляет, spill — мягко", async () => {
  // Жёлтая карточка на зелёнке: ключ её не вырезает, а подавление ореола задевает.
  const base = join(tmp, "green-yellow.mp4");
  const g = spawnSync("ffmpeg", ["-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x00fe00:size=270x480:rate=30", "-t", "1",
    "-vf", "drawbox=x=85:y=190:w=100:h=100:color=0xffff00:t=fill", "-pix_fmt", "yuv444p", "-c:v", "libx264", "-qp", "0", base], { encoding: "utf8" });
  assert.equal(g.status, 0, g.stderr);
  const yellowAt = async (despill) => {
    const key = { color: "0x00fe00", similarity: 0.3, blend: 0.05, ...despill };
    const p = writeInputs("despill", { width: 270, height: 480, fps: 30, duration: 1, layers: [], key, background: { color: "#000000" } });
    const png = join(tmp, "despill.png");
    const r = run(["--comp", p.comp, "--assets", p.assets, "--input", base, "--frame", "0.5", "--png", png]);
    assert.equal(r.status, 0, r.stderr);
    const cv = createCanvas(270, 480);
    cv.getContext("2d").drawImage(await loadImage(readFileSync(png)), 0, 0);
    return [...cv.getContext("2d").getImageData(135, 240, 1, 1).data.slice(0, 3)];
  };
  const auto = await yellowAt({ despill: "green" });
  const none = await yellowAt({ despill: "none" });
  const soft = await yellowAt({ despill: "green", spill: 0.3 });
  assert.ok(auto[0] > 230 && auto[1] < 150, `auto: ${auto}`); // жёлтое → оранжевое (как было)
  assert.ok(none[0] > 230 && none[1] > 235 && none[2] < 30, `none: ${none}`); // жёлтое осталось
  assert.ok(soft[1] > auto[1] + 40 && soft[1] < none[1] - 20, `soft: ${soft}`); // между
});
