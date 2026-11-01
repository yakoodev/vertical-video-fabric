// Тесты моушн-движка и компоновщика: `npm run test:motion` (или `node --test node/motion.test.mjs`
// после `npm run build:motion`). Движок из frontend/src/motion собирается esbuild во временную
// папку — тесты гоняют ровно тот TypeScript, что рисует превью и рендер.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
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

test("rise: слово выезжает снизу из-под маски строки — в середине обрезано, к концу на месте", () => {
  // n слов: слово i идёт за два слота из n + 1, как words; у не-текста rise — въезд снизу.
  const st = E.resolveLayer(textLayer({ in: { type: "rise", duration: 1 } }), 10.25);
  assert.deepEqual(st.reveal, { mode: "rise", fraction: 0.25 });
  const shape = E.resolveLayer({ ...textLayer(), type: "shape", props: { kind: "rect" }, in: { type: "rise", duration: 1 } }, 10);
  near(shape.y, 0.5 + 0.15);

  const props = { text: "НЕТ", size: 200, color: "#ffffff" };
  const env = { width: 1080, height: 1920, getImage: () => null };
  const bounds = (layer, t) => {
    const cv = createCanvas(1080, 1920);
    const ctx = cv.getContext("2d");
    E.drawLayer(ctx, layer, t, env);
    const d = ctx.getImageData(0, 0, 1080, 1920).data;
    let top = Infinity;
    let bottom = -Infinity;
    for (let y = 600; y < 1320; y++) {
      for (let x = 200; x < 880; x++) {
        if (d[(y * 1080 + x) * 4 + 3] > 0) {
          top = Math.min(top, y);
          bottom = Math.max(bottom, y);
        }
      }
    }
    return { top, bottom, data: d };
  };
  const still = bounds(textLayer({ props }), 12);
  const rise = textLayer({ props, in: { type: "rise", duration: 1 } });
  // Полоса строки (маска) — та же формула, что в text.ts: центр ± (capH/2 + 0.32·size).
  const L = E.layoutText(createCanvas(10, 10).getContext("2d"), props, "Montserrat");
  const half = L.capH / 2 + L.size * 0.32;
  const bandBottom = 960 + L.lines[0].cy + half;
  // Одно слово: ход = доля входа; на 0.2 слово сдвинуто на (1 − outCubic(0.2))·высота полосы.
  const dy = 2 * half * (1 - E.ease("outCubic", 0.2));
  assert.ok(still.bottom + dy > bandBottom + 10, "без маски низ слова вылез бы за полосу");
  const mid = bounds(rise, 10.2);
  assert.ok(Number.isFinite(mid.top), "слово уже видно");
  assert.ok(mid.top >= still.top + dy - 2, `сдвинуто вниз: ${mid.top} vs ${still.top} + ${dy}`);
  assert.ok(mid.bottom <= bandBottom + 1, `маска режет низ: ${mid.bottom} > ${bandBottom}`);
  assert.ok(mid.bottom - mid.top < still.bottom - still.top, "видна только часть слова");
  // Конец входа — ровно как без анимации.
  const end = bounds(rise, 11);
  assert.equal(end.top, still.top);
  assert.equal(end.bottom, still.bottom);
  assert.ok(Buffer.from(end.data).equals(Buffer.from(still.data)));
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

test("камера anchor in_place: точка наезда стоит на месте, кадр не открывает край; по умолчанию — прежний центр", () => {
  const W = 1920;
  const H = 1080;
  // Раскладка «титр слева, медиа справа»: наезд на медиа (0.75, 0.45).
  const keys = [
    { t: 0, zoom: 1, ease: "linear" },
    { t: 1, zoom: 1.6, x: 0.75, y: 0.45, anchor: "in_place", ease: "inOutCubic" },
    { t: 3, zoom: 1.6, x: 0.75, y: 0.45, anchor: "in_place" },
    { t: 4, zoom: 1 },
  ];
  for (let t = 0; t <= 4.5; t += 0.05) {
    const m = E.cameraMatrix(keys, t, W, H);
    // Точка (0.75, 0.45) после матрицы — там же, где была (на всём пути наезда и возврата).
    near(m.scale * 0.75 * W + m.tx, 0.75 * W, 1e-6);
    near(m.scale * 0.45 * H + m.ty, 0.45 * H, 1e-6);
    assert.ok(m.tx <= 1e-6 && m.tx + W * m.scale >= W - 1e-6, `x вылез на t=${t}`);
    assert.ok(m.ty <= 1e-6 && m.ty + H * m.scale >= H - 1e-6, `y вылез на t=${t}`);
  }
  const v = E.cameraView(keys, 2);
  near(v.zoom, 1.6);
  near(v.x, E.inPlaceCenter(0.75, 1.6));
  near(v.x, 0.75 + (0.5 - 0.75) / 1.6);
  // Ключ без anchor — прежнее поведение: точка уезжает в центр кадра (медиа справа — под титр).
  const old = E.cameraMatrix([{ t: 0, zoom: 1.6, x: 0.65, y: 0.45 }], 0, W, H);
  near(old.scale * 0.65 * W + old.tx, W / 2, 1e-6);
  const was = E.cameraMatrix([{ t: 0, zoom: 1.6, x: 0.65, y: 0.45, anchor: "in_place" }], 0, W, H);
  near(was.scale * 0.65 * W + was.tx, 0.65 * W, 1e-6);
  // Точка у самого края (x = 1) — край кадра стоит, ничего не открывается.
  const edge = E.cameraMatrix([{ t: 0, zoom: 2, x: 1, y: 0, anchor: "in_place" }], 0, W, H);
  near(edge.tx + W * edge.scale, W, 1e-6);
  near(edge.ty, 0, 1e-6);
  // Тряска поверх «на месте» тоже не показывает край.
  const shaky = [{ t: 0, zoom: 1.2, x: 0.9, y: 0.1, anchor: "in_place", shake: 1 }];
  for (let t = 0; t <= 2; t += 0.037) {
    const m = E.cameraMatrix(shaky, t, W, H);
    assert.ok(m.tx <= 1e-6 && m.tx + W * m.scale >= W - 1e-6);
    assert.ok(m.ty <= 1e-6 && m.ty + H * m.scale >= H - 1e-6);
  }
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

// Вариативный шрифт из комплекта бренда (только читаем); нет диска — тест пропускается.
const VARIABLE_FONT = "F:/repos/Antpro/marketing/video-kit/brand/fonts/BricolageGrotesque-Variable.ttf";

test("CLI: свои шрифты (--user-fonts-dir) — вариативный рисуется толщиной слоя", { skip: !existsSync(VARIABLE_FONT) && "нет файла вариативного шрифта" }, async () => {
  const dir = join(tmp, "user-fonts");
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "u-brand.ttf"), readFileSync(VARIABLE_FONT));
  writeFileSync(join(dir, "fonts.json"), JSON.stringify([
    { family: "Brand Var", file: "u-brand.ttf", weight: 800, style: "normal", user: true, variable: true,
      weight_range: [200, 800], axes: { wght: [200, 800, 800], opsz: [12, 96, 96] } },
  ]));
  const ink = async (weight) => {
    const comp = { width: 1080, height: 1920, fps: 30, duration: 1,
      layers: [textLayer({ start: 0, end: 1, props: { text: "Hello Brand", font: "Brand Var", weight, size: 140, color: "#ffffff" } })] };
    const p = writeInputs(`var-${weight}`, comp);
    const png = join(tmp, `var-${weight}.png`);
    const r = run(["--comp", p.comp, "--assets", p.assets, "--frame", "0.5", "--png", png, "--user-fonts-dir", dir]);
    assert.equal(r.status, 0, r.stderr);
    assert.match(r.stderr, /свои шрифты: 1/);
    const img = await loadImage(readFileSync(png));
    const cv = createCanvas(img.width, img.height);
    const ctx = cv.getContext("2d");
    ctx.drawImage(img, 0, 0);
    const d = ctx.getImageData(0, 0, img.width, img.height).data;
    let s = 0;
    for (let i = 3; i < d.length; i += 4) s += d[i];
    return s;
  };
  const thin = await ink(200);
  const bold = await ink(800);
  // Без fontVariationSettings Skia рисует начертание по умолчанию (800) при любой толщине.
  assert.ok(bold > thin * 1.4, `800: ${bold}, 200: ${thin}`);
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

test("субтитры: оригинал перевода (s: orig) — только в слое speakers ['orig'], своим цветом", () => {
  const ctx = createCanvas(8, 8).getContext("2d");
  const words = [
    { t0: 0.0, t1: 0.4, w: "привет" },
    { t0: 0.4, t1: 0.8, w: "бро", s: "B" },
    { t0: 0.0, t1: 0.8, w: "hello", s: "orig" },
  ];
  const all = E.captionPages(ctx, words, { style: "hormozi" }, 0, 5, "Montserrat");
  assert.deepEqual(all.flatMap((p) => p.words.map((w) => w.text)), ["ПРИВЕТ", "БРО"]);
  const orig = E.captionPages(ctx, words, { style: "minimal", speakers: ["orig"], uppercase: false }, 0, 5, "Montserrat");
  assert.deepEqual(orig.flatMap((p) => p.words.map((w) => w.text)), ["hello"]);
  assert.equal(E.speakerColor(E.resolveCaptions({ style: "minimal" }), "orig"), null);
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

test("CLI: наезд в раскладке pip — окошко одно: камера зумит игру без него, окошко из полосы под кадром", async () => {
  // Игра — зелёная, окошко килфида — красное (80×40 в точке 180,40 кадра 270×480).
  const W = 270;
  const H = 480;
  const win = { x: 180, y: 40, w: 80, h: 40 };
  const camera = [{ t: 0, zoom: 1.6, x: 0.8, y: 0.15 }];
  const make = (name, h, box) => {
    const file = join(tmp, `${name}.mp4`);
    const g = spawnSync("ffmpeg", ["-v", "error", "-y", "-f", "lavfi", "-i", `color=c=0x00ff00:size=${W}x${h}:rate=30`, "-t", "1",
      "-vf", `drawbox=x=${box.x}:y=${box.y}:w=${box.w}:h=${box.h}:color=0xff0000:t=fill`, "-pix_fmt", "yuv420p", file], { encoding: "utf8" });
    assert.equal(g.status, 0, g.stderr);
    return file;
  };
  const redOutside = async (png) => {
    const cv = createCanvas(W, H);
    const ctx = cv.getContext("2d");
    ctx.drawImage(await loadImage(readFileSync(png)), 0, 0);
    const d = ctx.getImageData(0, 0, W, H).data;
    let outside = 0;
    let inside = 0;
    for (let y = 0; y < H; y++) {
      for (let x = 0; x < W; x++) {
        const i = (y * W + x) * 4;
        if (!(d[i] > 180 && d[i + 1] < 90)) continue;
        const inWin = x >= win.x - 2 && x < win.x + win.w + 2 && y >= win.y - 2 && y < win.y + win.h + 2;
        if (inWin) inside++;
        else outside++;
      }
    }
    return { inside, outside };
  };
  // Было: окошко в самой основе — зум увеличивал и его, рядом с неподвижным — второе.
  const old = writeInputs("pip-old", { width: W, height: H, fps: 30, duration: 1, layers: [], camera, pinned: [win] });
  const oldPng = join(tmp, "pip-old.png");
  const r1 = run(["--comp", old.comp, "--assets", old.assets, "--input", make("pip-old", H, win), "--frame", "0.5", "--png", oldPng]);
  assert.equal(r1.status, 0, r1.stderr);
  assert.ok((await redOutside(oldPng)).outside > 500, "без полосы окно раздваивается (воспроизведение)");
  // Стало: основа 270×(480+40) — игра без окошка, окошко в полосе (строка 480).
  const comp = { width: W, height: H, fps: 30, duration: 1, layers: [], camera, pinned: [{ ...win, sy: H }], baseHeight: H + 40 };
  const p = writeInputs("pip-band", comp);
  const png = join(tmp, "pip-band.png");
  const base = make("pip-band", H + 40, { x: win.x, y: H, w: win.w, h: win.h });
  const r2 = run(["--comp", p.comp, "--assets", p.assets, "--input", base, "--frame", "0.5", "--png", png]);
  assert.equal(r2.status, 0, r2.stderr);
  const got = await redOutside(png);
  assert.equal(got.outside, 0, "копии окошка вне его места нет");
  assert.ok(got.inside >= win.w * win.h * 0.9, "окошко на месте");
  // Видео тоже: размер выхода — кадр, не основа с полосой.
  const out = join(tmp, "pip-band.out.mp4");
  const r3 = run(["--comp", p.comp, "--assets", p.assets, "--input", base, "--output", out]);
  assert.equal(r3.status, 0, r3.stderr);
  const info = ffprobe(out);
  assert.equal(info.height, H);
  assert.equal(Number(info.nb_read_frames), 30);
});

test("CLI: стоп-кадр посреди ролика (comp.freezes) — слои и камера стоят, после вставки идут со сдвигом", () => {
  // Квадрат едет слева направо за 4 с; стоп-кадр в 1 с на 1 с (вставка, слои стоят).
  const layers = [{ id: "sq", type: "shape", x: 0.1, y: 0.5, scale: 1, rotation: 0, opacity: 1, track: 0, start: 0, end: 6,
    keys: { x: [{ t: 0, v: 0.1, ease: "linear" }, { t: 4, v: 0.9 }] }, props: { kind: "rect", w: 60, h: 60, fill: "#ff0000" } }];
  const camera = [{ t: 0, zoom: 1, x: 0.5, y: 0.5 }, { t: 4, zoom: 2, x: 0.5, y: 0.5 }];
  const base = { width: 270, height: 480, fps: 30, duration: 6, layers, camera };
  const frame = (name, comp, t) => {
    const p = writeInputs(name, comp);
    const png = join(tmp, `${name}.png`);
    const r = run(["--comp", p.comp, "--assets", p.assets, "--frame", String(t), "--png", png]);
    assert.equal(r.status, 0, r.stderr);
    return readFileSync(png);
  };
  const frozen = { ...base, freezes: [{ at: 1, d: 1, insert: true, hold: true }] };
  const at1 = frame("fz-plain-1", base, 1);
  assert.deepEqual(frame("fz-in-1", frozen, 1.5), at1, "на стоп-кадре слои стоят в at");
  assert.deepEqual(frame("fz-in-2", frozen, 1.99), at1);
  assert.deepEqual(frame("fz-after", frozen, 2.5), frame("fz-plain-15", base, 1.5), "после вставки — со сдвигом на её длину");
  assert.notDeepEqual(frame("fz-plain-2", base, 2), at1);
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

/* ---------- видео-слой и корпус телефона ---------- */

test("видео: секунда файла — from + local·speed; loop, freeze, скорость зажата", () => {
  const T = (p, local, dur = 10) => E.videoFileTime(p, local, dur);
  near(T({}, 0), 0);
  near(T({ from: 2 }, 1.5), 3.5);
  near(T({ from: 1, speed: 2 }, 1.5), 4);
  near(T({ speed: 100 }, 1), E.VIDEO_MAX_SPEED); // зажата 0.25..4
  near(T({ speed: 0.01 }, 1), E.VIDEO_MIN_SPEED);
  // За концом файла: по умолчанию стоп-кадр (последний кадр), freeze: false — кадра нет.
  near(T({ from: 8 }, 5), 10 - E.VIDEO_TAIL);
  assert.equal(T({ from: 8, freeze: false }, 5), null);
  // loop — снова с from: файл 10 с, from 4 → кусок 4..~10, дальше снова с 4.
  const span = 10 - E.VIDEO_TAIL - 4;
  near(T({ from: 4, loop: true }, span + 1), 5, 1e-9);
  near(T({ from: 4, loop: true }, 2 * span + 0.5), 4.5, 1e-9);
  // Длина файла неизвестна (превью ещё не загрузило) — просто from + local·speed.
  near(T({ from: 3, freeze: false }, 100, 0), 103);
});

/** Источник видео-слоя: кадр «файла» — сплошной цвет, по полсекунды на цвет. */
function fakeVideo(duration = 2, w = 90, h = 195) {
  const colors = ["#ff0000", "#00ff00", "#0000ff", "#ffff00", "#ff00ff", "#00ffff"];
  const canvases = colors.map((c) => {
    const cv = createCanvas(w, h);
    const x = cv.getContext("2d");
    x.fillStyle = c;
    x.fillRect(0, 0, w, h);
    return cv;
  });
  return {
    duration,
    width: w,
    height: h,
    frame(time) {
      const i = Math.min(colors.length - 1, Math.floor(time * 2 + 1e-9));
      return { image: canvases[i], width: w, height: h };
    },
  };
}

const videoLayer = (over = {}) => ({ ...textLayer(), id: "v1", type: "video", ...over, props: { asset_id: "rec", w: 540, ...(over.props || {}) } });

function drawAt(layer, t, env) {
  const cv = createCanvas(1080, 1920);
  const ctx = cv.getContext("2d");
  E.drawLayer(ctx, layer, t, env);
  const box = E.layerBox(ctx, layer, t, env);
  return { box, px: (x, y) => [...ctx.getImageData(x, y, 1, 1).data] };
}

test("видео-слой рисует кадр файла по времени слоя: from, speed, loop, freeze", () => {
  const src = fakeVideo(2.0);
  const env = { width: 1080, height: 1920, getImage: () => null, getVideo: (l) => (l.props.asset_id === "rec" ? src : null) };
  const center = (layer, t) => drawAt(layer, t, env).px(540, 960).slice(0, 3);
  // start 10: в 10.2 — секунда 0.2 (красный), в 10.7 — 0.7 (зелёный).
  assert.deepEqual(center(videoLayer(), 10.2), [255, 0, 0]);
  assert.deepEqual(center(videoLayer(), 10.7), [0, 255, 0]);
  // from 1 → в 10.2 секунда 1.2 (синий); speed 2 → в 10.3 секунда 0.6 (зелёный).
  assert.deepEqual(center(videoLayer({ props: { from: 1 } }), 10.2), [0, 0, 255]);
  assert.deepEqual(center(videoLayer({ props: { speed: 2 } }), 10.3), [0, 255, 0]);
  // Файл 2 с кончился: стоп-кадр — последний (жёлтый, 1.5..2), без freeze — пусто.
  assert.deepEqual(center(videoLayer(), 13), [255, 255, 0]);
  assert.equal(drawAt(videoLayer({ props: { freeze: false } }), 13, env).px(540, 960)[3], 0);
  // loop: в 10 + 2.5 с — снова ~0.54 с от начала (зелёный).
  assert.deepEqual(center(videoLayer({ props: { loop: true } }), 12.5), [0, 255, 0]);
  // Размер слоя — по пропорции кадра: 540 × (540·195/90).
  const box = drawAt(videoLayer(), 10.2, env).box;
  near(box.w, 540, 1e-6);
  near(box.h, 1170, 1e-6);
  // Без источника (превью ещё грузит) — пусто и без ошибок.
  const none = { width: 1080, height: 1920, getImage: () => null };
  assert.equal(drawAt(videoLayer(), 10.2, none).px(540, 960)[3], 0);
});

test("видео-слой: crop-ключи у video, рамка неподвижна, radius обрезает", () => {
  const src = fakeVideo(2.0, 100, 400);
  const env = { width: 1080, height: 1920, getImage: () => null, getVideo: () => src };
  const l = videoLayer({ props: { w: 540, radius: 120, crop: { x: 0, y: 0, w: 1, h: 0.5 } }, keys: { cropY: [{ t: 0, v: 0, ease: "linear" }, { t: 1, v: 0.5 }] } });
  near(E.resolveLayer(l, 10.5).crop.y, 0.25);
  const a = drawAt(l, 10.1, env);
  const b = drawAt(l, 11.2, env);
  near(a.box.h, 1080, 1e-6);
  near(b.box.h, 1080, 1e-6);
  assert.equal(a.px(540 - 268, 960 - 538)[3], 0); // угол за скруглением пуст
  assert.equal(a.px(540, 960)[3], 255);
});

test("корпус телефона: размер от w, металл по краю, тёмная рамка, островок; экран — содержимое", () => {
  const scr = createCanvas(90, 195);
  const sx = scr.getContext("2d");
  sx.fillStyle = "#ffffff";
  sx.fillRect(0, 0, 90, 195);
  const env = { width: 1080, height: 1920, getImage: (ref) => (ref === "scr" ? scr : null) };
  const l = imageLayer({ props: { asset_id: "scr", w: 540, device: "phone", deviceColor: "#2c2c2e" } });
  const m = E.phoneMetrics(540, 1170);
  const { box, px } = drawAt(l, 11, env);
  near(box.w, m.outerW, 1e-6);
  near(box.h, m.outerH, 1e-6);
  assert.ok(m.bodyW > 540 && m.bodyW < 600, `корпус ${m.bodyW}`);
  // Экран — белое содержимое.
  assert.deepEqual(px(540, 1100).slice(0, 3), [255, 255, 255]);
  // Чёрная рамка между металлом и экраном (слева от экрана, на середине высоты).
  const bezel = px(Math.round(540 - 270 - m.bezel / 2), 960);
  assert.ok(bezel[0] < 20 && bezel[3] === 255, `рамка ${bezel}`);
  // Металл пояса — не чёрный и не белый.
  const rim = px(Math.round(540 - m.bodyW / 2 + m.rim / 2), 960);
  assert.ok(rim[3] === 255 && rim[0] > 25 && rim[0] < 200, `пояс ${rim}`);
  // Островок — чёрная таблетка у верха экрана, по центру.
  const iy = Math.round(960 - 1170 / 2 + 540 * 0.032 + (540 * 0.088) / 2);
  const island = px(540, iy);
  assert.ok(island[0] < 20 && island[3] === 255, `островок ${island}`);
  // Без островка там уже экран.
  const off = drawAt(imageLayer({ props: { asset_id: "scr", w: 540, device: "phone", island: false } }), 11, env);
  assert.deepEqual(off.px(540, iy).slice(0, 3), [255, 255, 255]);
  // За углом корпуса — прозрачно.
  assert.equal(px(Math.round(540 - m.bodyW / 2 + 2), Math.round(960 - m.bodyH / 2 + 2))[3], 0);
  // Видео в телефоне без кадров — корпус с тёмным экраном (превью ещё грузит файл).
  const empty = drawAt(videoLayer({ props: { device: "phone" } }), 11, { width: 1080, height: 1920, getImage: () => null });
  assert.equal(empty.px(540, 960)[3], 255);
  assert.ok(empty.px(540, 960)[0] < 10);
});

test("старые слои image без device — прежний вид (обратная совместимость)", () => {
  const scr = createCanvas(100, 100);
  const sx = scr.getContext("2d");
  sx.fillStyle = "#ff0000";
  sx.fillRect(0, 0, 100, 100);
  const env = { width: 1080, height: 1920, getImage: (ref) => (ref === "scr" ? scr : null) };
  const { box, px } = drawAt(imageLayer({ props: { asset_id: "scr", w: 400, border: { color: "#00ff00", width: 10 } } }), 11, env);
  near(box.w, 420, 1e-6);
  near(box.h, 420, 1e-6);
  assert.deepEqual(px(540, 960).slice(0, 3), [255, 0, 0]);
  assert.deepEqual(px(540 - 205, 960).slice(0, 3), [0, 255, 0]); // рамка снаружи
  assert.equal(px(540 - 215, 960)[3], 0);
});

test("CLI: видео-слой из нарезанных кадров (videos.json) — кадр по индексу, стоп-кадр в конце", async () => {
  // «Нарезка» бэкенда: 6 кадров JPEG по 0.5 с файла (rate 2), from 1 → кадр i = секунда 1 + i/2.
  const dir = join(tmp, "vframes");
  mkdirSync(dir, { recursive: true });
  const colors = ["#ff0000", "#00ff00", "#0000ff", "#ffff00", "#ff00ff", "#00ffff"];
  for (let i = 0; i < colors.length; i++) {
    const cv = createCanvas(200, 400);
    const x = cv.getContext("2d");
    x.fillStyle = colors[i];
    x.fillRect(0, 0, 200, 400);
    writeFileSync(join(dir, `${String(i).padStart(5, "0")}.jpg`), await cv.encode("jpeg", 95));
  }
  const comp = {
    width: 540,
    height: 960,
    fps: 2,
    duration: 4,
    layers: [{ id: "rec", type: "video", start: 0, end: 4, x: 0.5, y: 0.5, props: { asset_id: "7", w: 400, from: 1, speed: 1, device: "phone" } }],
  };
  writeFileSync(join(tmp, "vcomp.json"), JSON.stringify(comp));
  writeFileSync(join(tmp, "vvideos.json"), JSON.stringify({ rec: { dir, pattern: "%05d.jpg", start: 1, rate: 2, count: 6, duration: 4 } }));
  const frameAt = async (t) => {
    const png = join(tmp, `v-${t}.png`);
    const r = run(["--comp", join(tmp, "vcomp.json"), "--videos", join(tmp, "vvideos.json"), "--frame", String(t), "--png", png]);
    assert.equal(r.status, 0, r.stderr);
    const img = await loadImage(readFileSync(png));
    const cv = createCanvas(img.width, img.height);
    const x = cv.getContext("2d");
    x.drawImage(img, 0, 0);
    return [...x.getImageData(270, 480, 1, 1).data].slice(0, 3);
  };
  const close = (got, want) => assert.ok(got.every((v, k) => Math.abs(v - want[k]) < 40), `${got} ≉ ${want}`);
  close(await frameAt(0), [255, 0, 0]); // секунда 1 → кадр 0
  close(await frameAt(1), [0, 0, 255]); // секунда 2 → кадр 2
  close(await frameAt(3.9), [0, 255, 255]); // за концом нарезки — последний кадр
  // Видео целиком: кадры подгружаются по ходу, рендер не падает.
  const out = join(tmp, "vmotion.mp4");
  const r = run(["--comp", join(tmp, "vcomp.json"), "--videos", join(tmp, "vvideos.json"), "--output", out]);
  assert.equal(r.status, 0, r.stderr);
  assert.equal(Number(ffprobe(out).nb_read_frames), 8);
});

/* ---------- промо приложения: символы-декор, частицы, стоп-кадр, строка статуса, затемнение ---------- */

const pxOf = (ctx, x, y) => [...ctx.getImageData(Math.round(x), Math.round(y), 1, 1).data];

test("символы-декор ✦ ★ ♥ → в тексте: ширина — доля кегля (не шрифта), рисуются путём цветом текста", () => {
  const ctx = createCanvas(1080, 1920).getContext("2d");
  const L = (text, font) => E.layoutText(ctx, { text, size: 100, font, letterSpacing: 0 }, "Montserrat");
  near(L("✦").w, 100 * E.DECOR_GLYPHS["✦"].adv, 1e-6);
  near(L("★", "Rubik").w, L("★", "Inter").w, 1e-9); // от шрифта не зависит — перенос в превью = рендер
  // Перенос считает ширину символов: 10 звёзд по 92 px не влезают в 600 px одной строкой.
  const many = E.layoutText(ctx, { text: "★ ★ ★ ★ ★ ★ ★ ★ ★ ★", size: 100, maxWidth: 600 }, "Montserrat");
  assert.ok(many.lines.length >= 2, `строк ${many.lines.length}`);
  for (const g of ["✦", "✧", "★", "☆", "✶", "•", "→", "♥", "✓"]) assert.ok(E.decorGlyph(g), g);
  assert.equal(E.decorGlyph("♥️"), null); // с FE0F — эмодзи (Twemoji), а не символ
  // Рисуется: центр ✦ залит цветом текста, между лучами пусто (не «тофу»-квадрат).
  const cv = createCanvas(1080, 1920);
  const c = cv.getContext("2d");
  E.drawLayer(c, textLayer({ props: { text: "✦", size: 300, color: "#ff0000" } }), 11, { width: 1080, height: 1920, getImage: () => null });
  assert.deepEqual(pxOf(c, 540, 960).slice(0, 3), [255, 0, 0]);
  assert.equal(pxOf(c, 540 - 75, 960 - 75)[3], 0, "между лучами искры — пусто");
  // Обводка текста обводит и символ.
  const cv2 = createCanvas(1080, 1920);
  const c2 = cv2.getContext("2d");
  E.drawLayer(c2, textLayer({ props: { text: "★", size: 300, color: "#ffffff", stroke: { color: "#00ff00", width: 12 } } }), 11, { width: 1080, height: 1920, getImage: () => null });
  const top = 960 - 300 * 0.44 + 300 * 0.03; // кончик верхнего луча
  assert.deepEqual(pxOf(c2, 540, top - 5).slice(0, 3), [0, 255, 0]);
  assert.deepEqual(pxOf(c2, 540, 960).slice(0, 3), [255, 255, 255]);
});

test("shape star4 — четырёхлучевая искра: лучи до края w×h, вогнутые бока", () => {
  const pts = E.sparklePoints(150, 200);
  assert.equal(pts.length, 48);
  assert.ok(pts.some(([x, y]) => Math.abs(y + 200) < 1e-9 && Math.abs(x) < 1e-9), "верхний луч");
  const cv = createCanvas(1080, 1920);
  const c = cv.getContext("2d");
  const env = { width: 1080, height: 1920, getImage: () => null };
  const l = { ...textLayer(), type: "shape", props: { kind: "star4", w: 300, h: 400, fill: "#ffd400" } };
  E.drawLayer(c, l, 11, env);
  assert.equal(pxOf(c, 540, 960)[3], 255);
  assert.equal(pxOf(c, 540, 960 - 185)[3], 255); // у кончика луча
  assert.equal(pxOf(c, 540 + 90, 960 - 120)[3], 0); // между лучами (вогнуто)
  near(E.layerBox(c, l, 11, env).w, 300, 1e-6);
});

test("частицы: детерминированы зерном, ≤ 200, burst разлетается и гаснет, confetti падает, rise поднимается", () => {
  const at = (props, local, seed = 7) => E.particlesAt(props, seed, local);
  const a = at({ kind: "burst", count: 60 }, 0.4);
  assert.deepEqual(a, at({ kind: "burst", count: 60 }, 0.4)); // та же раскладка кадр в кадр
  assert.notDeepEqual(a, at({ kind: "burst", count: 60 }, 0.4, 8));
  assert.ok(at({ kind: "sparkle", count: 999 }, 0.3).length <= E.MAX_PARTICLES);
  // burst: в начале у центра, к середине — дальше, после жизни (1.2 с) — пусто; repeat — снова.
  const dist = (ps) => ps.reduce((s, p) => s + Math.hypot(p.x, p.y), 0) / Math.max(1, ps.length);
  assert.ok(dist(at({ kind: "burst", count: 80, gravity: 0 }, 0.05)) < dist(at({ kind: "burst", count: 80, gravity: 0 }, 0.5)));
  assert.equal(at({ kind: "burst", count: 80 }, 1.3).length, 0);
  assert.ok(at({ kind: "burst", count: 80, repeat: true }, 1.2 + 0.3).length > 0);
  // Разлёт не дальше spread (без тяжести).
  assert.ok(at({ kind: "burst", count: 80, spread: 200, gravity: 0 }, 0.6).every((p) => Math.hypot(p.x, p.y) <= 200 + 1e-6));
  // confetti: средняя высота растёт (падают).
  const meanY = (ps) => ps.reduce((s, p) => s + p.y, 0) / ps.length;
  const fall = { kind: "confetti", count: 100, duration: 4 };
  assert.ok(meanY(at(fall, 2.5)) > meanY(at(fall, 2.0)) + 20, `${meanY(at(fall, 2.0))} → ${meanY(at(fall, 2.5))}`);
  // rise: та же частица чуть позже — выше (пока не началась новая петля).
  const one = (t) => at({ kind: "rise", count: 1, duration: 4 }, t, 3)[0];
  const y0 = one(1).y;
  const y1 = one(1.1).y;
  assert.ok(y1 < y0 || y1 > 300, `rise: ${y0} → ${y1}`);
  // sparkle: все внутри круга spread.
  assert.ok(at({ kind: "sparkle", count: 120, spread: 300 }, 0.7).every((p) => Math.hypot(p.x, p.y) <= 300 + 1e-6));
  // Без seed — зерно от id: у копии слоя своя раскладка.
  assert.notEqual(E.particleSeed({ id: "a", props: {} }), E.particleSeed({ id: "b", props: {} }));
  assert.equal(E.particleSeed({ id: "a", props: { seed: 5 } }), 5);
});

test("слой particles рисуется одним слоем; картинка частиц — в списке ресурсов; шаблон", () => {
  const env = { width: 1080, height: 1920, getImage: () => null };
  const l = { ...textLayer(), id: "fx", type: "particles", props: { kind: "sparkle", count: 150, size: 40, spread: 400, colors: ["#ffd400"], glow: true } };
  const paint = () => {
    const cv = createCanvas(1080, 1920);
    const c = cv.getContext("2d");
    E.drawLayer(c, l, 10.8, env);
    return c;
  };
  const c = paint();
  const data = c.getImageData(0, 0, 1080, 1920).data;
  let lit = 0;
  for (let i = 3; i < data.length; i += 4 * 16) if (data[i] > 0) lit++;
  assert.ok(lit > 200, `закрашено ${lit}`);
  // Детерминированно: второй рендер того же момента — те же пиксели.
  const again = paint().getImageData(0, 0, 1080, 1920).data;
  assert.ok(Buffer.from(again.buffer).equals(Buffer.from(data.buffer)));
  near(E.layerBox(c, l, 10.8, env).w, (400 + 40) * 2, 1e-6);
  assert.deepEqual(
    E.collectImageRefs([{ ...l, props: { asset_id: "12" } }, { ...l, props: { src: "sticker:p/x.png" } }]).sort(),
    ["12", "sticker:p/x.png"],
  );
  const tpl = E.buildTemplate("particles", { kind: "confetti", count: 90 }, 2);
  assert.equal(tpl.layers.length, 1);
  assert.equal(tpl.layers[0].type, "particles");
  assert.equal(tpl.layers[0].props.count, 90);
});

test("видео hold — стоп-кадр секунды from на весь слой", () => {
  const T = (p, local, dur = 10) => E.videoFileTime(p, local, dur);
  near(T({ from: 3.2, hold: true }, 0), 3.2);
  near(T({ from: 3.2, hold: true, speed: 4, loop: true }, 9), 3.2);
  near(T({ from: 30, hold: true }, 1), 10 - E.VIDEO_TAIL);
  const src = fakeVideo(2.0);
  const env = { width: 1080, height: 1920, getImage: () => null, getVideo: () => src };
  const center = (t) => drawAt(videoLayer({ props: { from: 1.1, hold: true } }), t, env).px(540, 960).slice(0, 3);
  assert.deepEqual(center(10.1), [0, 0, 255]); // секунда 1.1 — синий
  assert.deepEqual(center(13.5), [0, 0, 255]); // и через 3.5 с — тот же кадр
});

test("телефон: statusBar — полоса над записью (экран выше), островок у видео только с ней, блик", () => {
  const src = fakeVideo(2.0, 90, 195);
  const env = { width: 1080, height: 1920, getImage: () => null, getVideo: () => src };
  const plain = drawAt(videoLayer({ props: { device: "phone", w: 540 } }), 10.2, env);
  const bar = drawAt(videoLayer({ props: { device: "phone", w: 540, statusBar: true } }), 10.2, env);
  near(bar.box.h - plain.box.h, 540 * E.STATUS_BAR_AUTO, 1e-6);
  near(E.statusBarHeight({ device: "phone", statusBar: 0.9 }, 100), 40, 1e-9); // предел 0.4
  near(E.statusBarHeight({ statusBar: true }, 100), 0, 1e-9); // без корпуса — нет
  const m = E.phoneMetrics(540, 1000);
  const screenTop = (box) => 960 - box.h / 2 + m.rim + m.bezel;
  const iy = (box) => screenTop(box) + 540 * 0.032 + (540 * 0.088) / 2;
  // Островок: у видео без строки статуса его нет (там запись — красный кадр), со строкой — есть.
  assert.deepEqual(plain.px(540, iy(plain.box)).slice(0, 3), [255, 0, 0]);
  const withBar = bar.px(540, iy(bar.box));
  assert.ok(withBar[0] < 20 && withBar[1] < 20, `островок ${withBar}`);
  // Рядом с островком — чёрная строка статуса, ниже полосы — запись (красная).
  assert.ok(bar.px(540 - 130, screenTop(bar.box) + 6)[0] < 30);
  assert.deepEqual(bar.px(540, screenTop(bar.box) + 540 * 0.14 + 20).slice(0, 3), [255, 0, 0]);
  // island: true вручную — островок и без строки статуса.
  const forced = drawAt(videoLayer({ props: { device: "phone", w: 540, island: true } }), 10.2, env);
  assert.ok(forced.px(540, iy(forced.box))[0] < 20);
  // Блик стекла светлит верх-лево экрана, низ-право не трогает.
  const glare = drawAt(videoLayer({ props: { device: "phone", w: 540, screenGlare: true } }), 10.2, env);
  const tl = glare.px(540 - 200, 960 - 400);
  assert.ok(tl[1] > 0 && tl[2] > 0, `блик ${tl}`);
  assert.deepEqual(glare.px(540 + 200, 960 + 400).slice(0, 3), plain.px(540 + 200, 960 + 400).slice(0, 3));
});

test("строка статуса: время и значки не заезжают под островок и в скругление — на любом телефоне и шрифте", () => {
  // Раскладка: узкие и широкие экраны, тонкая и толстая полоса, крупное скругление, широкий шрифт.
  for (const w of [90, 160, 240, 360, 540, 900]) {
    for (const frac of [0.06, E.STATUS_BAR_AUTO, 0.4]) {
      for (const radius of [0, w * 0.3]) {
        for (const glyph of [0.6, 1.4]) {
          const h = w * 2.1;
          const m = E.phoneMetrics(w, h, radius);
          const L = E.statusBarLayout(w, h, frac * w, true, m.screenR, (u) => 4 * glyph * u);
          const gap = w * E.STATUS_ISLAND_GAP - 1e-6;
          const at = `w=${w} sb=${frac} r=${radius} glyph=${glyph}`;
          assert.ok(L.time[1] <= L.island[0] - gap, `время под островком: ${at} ${L.time} ${L.island}`);
          assert.ok(L.icons[0] >= L.island[1] + gap, `значки под островком: ${at} ${L.icons} ${L.island}`);
          assert.ok(L.time[0] >= -w / 2 && L.icons[1] <= w / 2, `за краем экрана: ${at}`);
          assert.ok(L.u > 0 && L.iconU > 0 && L.iconU <= L.u + 1e-9, at);
        }
      }
    }
  }
  // Пиксели: белая полоса, чёрные значки — рядом с островком (зазор) чисто белое по всей высоте полосы.
  for (const w of [180, 540]) {
    const scr = createCanvas(90, 195);
    const env = { width: 1080, height: 1920, getImage: () => scr };
    const layer = imageLayer({ props: { asset_id: "scr", w, device: "phone", statusBar: true, statusBarColor: "#ffffff" } });
    const { box, px } = drawAt(layer, 11, env);
    const m = E.phoneMetrics(w, box.h - 2 * (w * 0.017 + w * 0.03));
    const screenTop = 960 - box.h / 2 + m.rim + m.bezel;
    const sb = w * E.STATUS_BAR_AUTO;
    const dark = [];
    for (let y = Math.ceil(screenTop + 1); y < screenTop + sb - 1; y++) {
      for (const side of [-1, 1]) {
        // Зазор между островком и значками: от края островка наружу на 0.8 зазора.
        for (let k = 0.15 * w + 2; k < 0.15 * w + 0.8 * E.STATUS_ISLAND_GAP * w; k++) {
          const p = px(Math.round(540 + side * k), y);
          if (p[0] < 200) dark.push([side, k, y, p[0]]);
        }
      }
    }
    assert.deepEqual(dark, [], `w=${w}: в зазоре у островка значки`);
    // Значки есть: и слева (время), и справа — тёмные пиксели в «ушах».
    const ink = (side) => {
      for (let y = Math.ceil(screenTop + 1); y < screenTop + sb - 1; y++) {
        for (let k = 0.19 * w; k < 0.44 * w; k++) if (px(Math.round(540 + side * k), y)[0] < 100) return true;
      }
      return false;
    };
    assert.ok(ink(-1) && ink(1), `w=${w}: нет времени или значков`);
  }
});

test("text backdrop — мягкое затемнение за текстом: темнит вокруг, тает к краю, рамка его вмещает", () => {
  const env = { width: 1080, height: 1920, getImage: () => null };
  const white = () => {
    const c = createCanvas(1080, 1920).getContext("2d");
    c.fillStyle = "#ffffff";
    c.fillRect(0, 0, 1080, 1920);
    return c;
  };
  const props = { text: "ТИТР", size: 100, color: "#ffffff", backdrop: { opacity: 0.8, spread: 120 } };
  const c = white();
  E.drawLayer(c, textLayer({ props }), 11, env);
  const L = E.layoutText(c, props, "Montserrat");
  const g = E.backdropGeometry(L, props.backdrop);
  const below = pxOf(c, 540, 960 + L.h / 2 + 30); // под текстом, внутри эллипса
  assert.ok(below[0] < 140, `затемнение ${below}`);
  const edge = pxOf(c, 540 + g.rx - 4, 960);
  assert.ok(edge[0] > 230, `край ${edge}`);
  assert.deepEqual(pxOf(c, 540, 960 - g.ry - 10).slice(0, 3), [255, 255, 255]);
  const box = E.layerBox(c, textLayer({ props }), 11, env);
  near(box.w, g.rx * 2, 1e-6);
  assert.ok(E.layerBox(c, textLayer({ props: { ...props, backdrop: null } }), 11, env).w < box.w);
});

test("200 слоёв: время кадра 1080×1920 (предел MAX_LAYERS) и 200 частиц", () => {
  const env = { width: 1080, height: 1920, getImage: () => null, defaultFont: "Montserrat", createCanvas: (w, h) => createCanvas(w, h) };
  const layers = [];
  for (let i = 0; i < 200; i++) {
    const x = 0.08 + (i % 10) * 0.093;
    const y = 0.06 + Math.floor(i / 10) * 0.045;
    const base = { id: `l${i}`, start: 0, end: 10, x, y, scale: 1, rotation: 0, opacity: 1, track: i % 8, in: { type: "pop", duration: 0.3 } };
    if (i % 3 === 0) layers.push({ ...base, type: "text", props: { text: `Т${i}`, size: 40, color: "#ffffff", stroke: { color: "#000000", width: 4 } } });
    else if (i % 3 === 1) layers.push({ ...base, type: "shape", props: { kind: "rect", w: 80, h: 50, radius: 10, fill: "#8b5cf6" } });
    else layers.push({ ...base, type: "shape", loop: { type: "pulse" }, props: { kind: "star4", w: 60, h: 60, fill: "#ffd400" } });
  }
  const c = createCanvas(1080, 1920).getContext("2d");
  const frames = 30;
  const time = (ls) => {
    E.drawFrame(c, { layers: ls }, 0.1, env); // прогрев (раскладки текста)
    const t0 = performance.now();
    for (let f = 0; f < frames; f++) {
      c.clearRect(0, 0, 1080, 1920);
      E.drawFrame(c, { layers: ls }, 0.1 + f / 30, env);
    }
    return (performance.now() - t0) / frames;
  };
  const perLayers = time(layers);
  const fx = [{ id: "fx", type: "particles", start: 0, end: 10, x: 0.5, y: 0.5, props: { kind: "burst", count: 200, glow: true, repeat: true } }];
  const perFx = time(fx);
  console.log(`# 200 слоёв (текст/фигуры, pop/pulse): ${perLayers.toFixed(1)} мс/кадр; 200 частиц с glow: ${perFx.toFixed(1)} мс/кадр (1080×1920, без кодирования)`);
  assert.ok(perLayers < 250, `200 слоёв: ${perLayers.toFixed(1)} мс/кадр`);
  assert.ok(perFx < 120, `200 частиц: ${perFx.toFixed(1)} мс/кадр`);
});

/* ---------- эталон: старые кадры не меняются (формат по умолчанию) ---------- */

// Хэши пикселей сняты на main до универсальных форматов (tests/fixtures/motion_golden.json).
// Обновить: VVF_UPDATE_GOLDEN=1 npm run test:motion.
const GOLDEN = join(ROOT, "tests", "fixtures", "motion_golden.json");

async function pixelsHash(data, w, h) {
  const { createHash } = await import("node:crypto");
  return `${w}x${h}:` + createHash("sha256").update(data).digest("hex").slice(0, 24);
}

async function pngHash(path) {
  const img = await loadImage(readFileSync(path));
  const cv = createCanvas(img.width, img.height);
  cv.getContext("2d").drawImage(img, 0, 0);
  return pixelsHash(cv.getContext("2d").getImageData(0, 0, img.width, img.height).data, img.width, img.height);
}

const canvasHash = (cv) => pixelsHash(cv.getContext("2d").getImageData(0, 0, cv.width, cv.height).data, cv.width, cv.height);

/** Частицы, субтитры, покачивание, размытие, слой за камерой — то, что зависит от опорного кадра. */
function richLayers() {
  const L = (o) => ({ x: 0.5, y: 0.5, scale: 1, rotation: 0, opacity: 1, track: 0, start: 0, end: 5, ...o });
  return [
    L({ id: "wig", type: "text", y: 0.15, loop: { type: "wiggle", amount: 1, speed: 1 }, props: { text: "Покачивание", size: 90, color: "#ffffff" } }),
    L({ id: "flt", type: "shape", x: 0.3, y: 0.3, loop: { type: "float", amount: 1 }, blur: 6, props: { kind: "rect", w: 200, h: 120, fill: "#22D3EE" } }),
    L({ id: "fx", type: "particles", y: 0.45, props: { kind: "burst", count: 40, glow: true, repeat: true } }),
    L({ id: "cap", type: "captions", y: 0.8, props: { size: 80 } }),
    L({ id: "fol", type: "shape", x: 0.7, y: 0.6, followCamera: true, shadow: { color: "#000000", blur: 18, y: 6, opacity: 0.7 }, props: { kind: "ellipse", w: 220, h: 220, fill: "#FF2D55" } }),
    L({ id: "dev", type: "video", x: 0.5, y: 0.55, scale: 0.5, props: { device: "phone", w: 520 } }),
  ];
}
const RICH_WORDS = [{ t0: 0.5, t1: 0.9, w: "Слово" }, { t0: 0.9, t1: 1.3, w: "за" }, { t0: 1.3, t1: 1.8, w: "словом" }];
const RICH_CAMERA = [{ t: 0, zoom: 1, x: 0.5, y: 0.5 }, { t: 1, zoom: 1.5, x: 0.6, y: 0.55 }];

test("эталон: кадры компоновщика и движка 9:16 / 1:1 / 4:5 / превью не изменились", async () => {
  const got = {};
  for (const [name, w, h] of [["v1080", 1080, 1920], ["sq1080", 1080, 1080], ["p45", 1080, 1350], ["prev540", 540, 960]]) {
    const p = writeInputs(`golden-${name}`, { ...everyTypeComp(3), width: w, height: h });
    const png = join(tmp, `golden-${name}.png`);
    const r = run(["--comp", p.comp, "--assets", p.assets, "--frame", "1.2", "--png", png]);
    assert.equal(r.status, 0, r.stderr);
    got[`cli-${name}`] = await pngHash(png);
    const env = { width: w, height: h, getImage: () => null, createCanvas: (a, b) => createCanvas(a, b) };
    const cv = createCanvas(w, h);
    E.drawFrame(cv.getContext("2d"), { layers: richLayers(), camera: RICH_CAMERA, words: RICH_WORDS }, 1.1, env);
    got[`rich-${name}`] = await canvasHash(cv);
    for (const kind of ["aurora", "grid", "bokeh", "waves"]) {
      const bg = createCanvas(w, h);
      E.paintBackground(bg.getContext("2d"), E.normalizeBackground({ kind }), 1.3, w, h, { accent: "#22d3ee", createCanvas: (a, b) => createCanvas(a, b) });
      got[`bg-${kind}-${name}`] = await canvasHash(bg);
    }
  }
  if (process.env.VVF_UPDATE_GOLDEN === "1") {
    writeFileSync(GOLDEN, JSON.stringify(got, null, 1));
    return;
  }
  assert.deepEqual(got, JSON.parse(readFileSync(GOLDEN, "utf8")));
});

/* ---------- форматы вывода: опорный кадр 16:9 ---------- */

test("formats.ts: реестр, старое поле aspect, окружение превью", () => {
  assert.deepEqual(E.frameSize("16:9"), { w: 1920, h: 1080 });
  assert.deepEqual(E.formatRefSize("9:16"), { w: 1080, h: 1920 });
  assert.equal(E.normalizeFormat("landscape"), "16:9");
  assert.equal(E.normalizeFormat("21:9"), "9:16");
  assert.equal(E.clipFormat({ aspect: "1:1" }), "1:1");
  assert.equal(E.clipFormat({ aspect: "1:1", format: "16:9" }), "16:9");
  assert.equal(E.layersFormat({}), "9:16");
  assert.deepEqual(E.formatEnv("9:16", 540), { width: 540, height: 960 });
  assert.deepEqual(E.formatEnv("16:9", 960), { width: 960, height: 540, refWidth: 1920, refHeight: 1080 });
  near(E.layerUnit({ width: 960, refWidth: 1920 }), 0.5);
  near(E.layerUnit({ width: 540 }), 0.5);
});

/** Рамка красных пикселей кадра (r > 200, g < 60). */
function redBox(data, w, h) {
  let x0 = w, y0 = h, x1 = -1, y1 = -1;
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const i = (y * w + x) * 4;
      if (data[i] > 200 && data[i + 1] < 60 && data[i + 3] > 200) {
        if (x < x0) x0 = x;
        if (x > x1) x1 = x;
        if (y < y0) y0 = y;
        if (y > y1) y1 = y;
      }
    }
  }
  return { x0, y0, w: x1 - x0 + 1, h: y1 - y0 + 1 };
}

test("кадр компоновщика 1920×1080: px слоя = px кадра, покачивание и центр в долях", async () => {
  const rect = { id: "r", type: "shape", start: 0, end: 3, x: 0.25, y: 0.5, props: { kind: "rect", w: 400, h: 200, fill: "#ff0000" } };
  const comp = { width: 1920, height: 1080, refWidth: 1920, refHeight: 1080, fps: 30, duration: 3, layers: [rect], camera: [] };
  const p = writeInputs("wide169", comp);
  const png = join(tmp, "wide169.png");
  const r = run(["--comp", p.comp, "--assets", p.assets, "--frame", "1", "--png", png]);
  assert.equal(r.status, 0, r.stderr);
  const img = await loadImage(readFileSync(png));
  assert.equal(img.width, 1920);
  assert.equal(img.height, 1080);
  const cv = createCanvas(1920, 1080);
  cv.getContext("2d").drawImage(img, 0, 0);
  const b = redBox(cv.getContext("2d").getImageData(0, 0, 1920, 1080).data, 1920, 1080);
  near(b.w, 400, 1.5);
  near(b.h, 200, 1.5);
  near(b.x0 + b.w / 2, 480, 1.5); // x = 0.25 кадра
  near(b.y0 + b.h / 2, 540, 1.5);
  // Без опорного кадра (старый comp.json) тот же слой — в единицах 1080 по ширине: в 1920/1080 раз крупнее.
  const old = writeInputs("wide169old", { ...comp, refWidth: undefined, refHeight: undefined });
  const png2 = join(tmp, "wide169old.png");
  assert.equal(run(["--comp", old.comp, "--assets", old.assets, "--frame", "1", "--png", png2]).status, 0);
  const img2 = await loadImage(readFileSync(png2));
  const cv2 = createCanvas(1920, 1080);
  cv2.getContext("2d").drawImage(img2, 0, 0);
  near(redBox(cv2.getContext("2d").getImageData(0, 0, 1920, 1080).data, 1920, 1080).w, (400 * 1920) / 1080, 2);
  // Покачивание float — 14 опорных px по высоте опорного кадра (1080), а не 1920.
  const float = { ...rect, loop: { type: "float", amount: 1, speed: 1 } };
  const t = 1 / (4 * 0.45); // пик синуса
  const st = E.resolveLayer(float, t, { w: 1920, h: 1080 });
  near((st.y - 0.5) * 1080, 14, 1e-6);
  near((E.resolveLayer(float, t).y - 0.5) * 1920, 14, 1e-6); // по умолчанию — как всегда
});

/* ---------- перекомпоновка между форматами (reframe.ts — зеркало app/aspect.py) ---------- */

// Сравнение с допуском: числа округлены до 4 знаков, а у Python и JS может разойтись
// последняя цифра на половинке.
function sameShape(got, want, path, eps = 2e-4) {
  if (typeof want === "number") {
    assert.equal(typeof got, "number", `${path}: ${got} вместо числа ${want}`);
    assert.ok(Math.abs(got - want) <= eps * Math.max(1, Math.abs(want)), `${path}: ${got} ≉ ${want}`);
    return;
  }
  if (Array.isArray(want)) {
    assert.ok(Array.isArray(got), `${path}: не массив`);
    assert.equal(got.length, want.length, `${path}: длина ${got.length} ≠ ${want.length}`);
    want.forEach((w, i) => sameShape(got[i], w, `${path}[${i}]`, eps));
    return;
  }
  if (want && typeof want === "object") {
    assert.ok(got && typeof got === "object", `${path}: не объект`);
    for (const k of Object.keys(want)) sameShape(got[k], want[k], `${path}.${k}`, eps);
    return;
  }
  assert.equal(got, want, path);
}

test("reframe.ts: те же слои и камера, что app/aspect.reframe_between (фикстура Python)", () => {
  const data = JSON.parse(readFileSync(join(ROOT, "tests", "fixtures", "reframe_cases.json"), "utf8"));
  assert.ok(data.cases.length >= 12);
  for (const c of data.cases) {
    const base = data.bases[c.base];
    const out = E.reframeBetween(base.layers, base.camera, c.src, c.dst, base.sizes);
    sameShape(out.layers, c.expect.layers, `${c.name} layers`);
    sameShape(out.camera, c.expect.camera, `${c.name} camera`);
  }
  // Тот же формат — те же объекты, как в Python.
  const base = data.bases.promo;
  assert.equal(E.reframeBetween(base.layers, base.camera, "9:16", "9:16").layers, base.layers);
  assert.equal(E.pyRound(0.125, 2), 0.12);
  assert.equal(E.pyRound(0.375, 2), 0.38);
  assert.equal(E.assetKey("007"), "7");
  assert.equal(E.assetKey("sticker:a/b.png"), "sticker:a/b.png");
});

test("шаблоны в кадре другого формата (fitTemplate): влезают, края и доли — по правилам шаблона", () => {
  const tpl = (id) => E.TEMPLATES.find((t) => t.id === id);
  const defaults = (t) => Object.fromEntries(t.params.map((p) => [p.name, p.default]));
  const build = (id, fmt) => E.fitTemplate(tpl(id).build(defaults(tpl(id)), 0), fmt, tpl(id).fit);
  // 9:16 — как нарисовано.
  const same = tpl("top-3").build(defaults(tpl("top-3")), 0);
  assert.equal(E.fitTemplate(same, "9:16").layers, same.layers);
  for (const fmt of ["1:1", "4:5", "16:9"]) {
    const { w: W, h: H } = E.formatRefSize(fmt);
    // Телефон (600 px, ~1356 px высотой в 9:16) и подпись — целиком в кадре с полями.
    for (const id of ["phone-screen", "top-3", "progress", "pie-chart"]) {
      for (const l of build(id, fmt).layers) {
        const size = E.layerSize(l);
        if (!size) continue;
        const k = l.scale ?? 1;
        const top = l.y * H - (size[1] * k) / 2;
        const bottom = l.y * H + (size[1] * k) / 2;
        assert.ok(top >= H * 0.04 - 1 && bottom <= H * 0.96 + 1, `${id} ${fmt} ${l.name}: ${top}..${bottom} из ${H}`);
        assert.ok(l.x * W - (size[0] * k) / 2 >= -1 && l.x * W + (size[0] * k) / 2 <= W + 1, `${id} ${fmt} ${l.name} по ширине`);
      }
    }
    // Мем-подпись: сверху и снизу — те же доли кадра, кегль тот же.
    const meme = build("meme-caption", fmt).layers;
    assert.deepEqual(meme.map((l) => l.y), [0.12, 0.86]);
    assert.ok(meme.every((l) => (l.scale ?? 1) === 1));
    // Бегущая строка: лента во всю ширину нового кадра, текст уезжает за край, кромка на краю ленты.
    const ticker = build("news-ticker", fmt).layers;
    const band = ticker.find((l) => l.name === "Лента");
    const edge = ticker.find((l) => l.name === "Кромка");
    assert.equal(band.props.w, W);
    near((band.y - edge.y) * H, 52, 0.5);
    const run = ticker.find((l) => l.name === "Бегущий текст");
    assert.ok(run.keys.x[0].v > 1 && run.keys.x[1].v < 0);
    // «Внимание!»: рамка по краю нового кадра, «До/После»: разделитель во всю высоту.
    const frame = build("alert", fmt).layers.find((l) => l.name === "Рамка");
    assert.equal(frame.props.w, W - 20);
    assert.equal(frame.props.h, H - 20);
    assert.equal(build("before-after", fmt).layers.find((l) => l.name === "Разделитель").props.h, H);
  }
  // Телефон в 16:9 меньше (≈0.64), в 4:5 — крупнее, чем в квадрате.
  const phoneScale = (fmt) => build("phone-screen", fmt).layers.find((l) => l.name === "Телефон").scale;
  assert.ok(phoneScale("16:9") < 0.7 && phoneScale("4:5") > phoneScale("1:1"));
  // Стрелка «смотри»: цель (круг) — ровно на своей доле кадра.
  const look = build("look-here", "16:9").layers[0];
  assert.equal(look.x, 0.62);
  assert.equal(look.y, 0.32);
});

/* ---------- PNGtuber и анимации с альфой ---------- */

test("animFrameIndex: кадр по времени по кругу, свои длительности; voiceStateAt — состояние по кадрам", () => {
  const times = [0, 0.06, 0.12, 0.3];
  assert.equal(E.animFrameIndex(times, 0.4, 0), 0);
  assert.equal(E.animFrameIndex(times, 0.4, 0.059), 0);
  assert.equal(E.animFrameIndex(times, 0.4, 0.06), 1); // ровно на границе — уже следующий
  assert.equal(E.animFrameIndex(times, 0.4, 0.29), 2); // длинный кадр (0.18 с)
  assert.equal(E.animFrameIndex(times, 0.4, 0.35), 3);
  assert.equal(E.animFrameIndex(times, 0.4, 0.41), 0); // по кругу
  assert.equal(E.animFrameIndex(times, 0.4, -0.05), 3);
  assert.equal(E.animFrameIndex([0], 0, 5), 0);
  const tr = { t0: 1, fps: 10, states: "0112", levels: [0, 50, 100, 20] };
  assert.equal(E.voiceStateAt(tr, 0.5), 0); // до начала — молчит
  assert.equal(E.voiceStateAt(tr, 1.0), 0);
  assert.equal(E.voiceStateAt(tr, 1.1), 1);
  assert.equal(E.voiceStateAt(tr, 1.35), 2);
  assert.equal(E.voiceStateAt(tr, 2), 0); // за концом — молчит
  assert.equal(E.voiceStateAt(null, 1.2), 0);
  near(E.voiceLevelAt(tr, 1.25), 1);
  assert.equal(E.pngtuberStateRef({ states: { idle: "1", talk: "2" } }, 2), "2"); // нет shout — talk
  assert.equal(E.pngtuberStateRef({ states: { idle: "1", talk: "2", shout: "3" } }, 2), "3");
  assert.equal(E.pngtuberStateRef({ states: { idle: "1", talk: "2" } }, 0), "1");
});

/** Синтетический «GIF»: кадры 8×8 с прозрачным краем (1 px) и цветной серединой. */
function fakeAnim(colors, step = 0.5) {
  const frames = colors.map((c) => {
    const cv = createCanvas(8, 8);
    const x = cv.getContext("2d");
    x.clearRect(0, 0, 8, 8);
    x.fillStyle = c;
    x.fillRect(1, 1, 6, 6);
    return cv;
  });
  return {
    width: 8,
    height: 8,
    times: colors.map((_c, i) => i * step),
    duration: colors.length * step,
    frame: (i) => ({ image: frames[i], sx: 0, sy: 0, sw: 8, sh: 8 }),
  };
}

const pngtuberLayer = (over = {}) => ({
  id: "av",
  type: "pngtuber",
  start: 0,
  end: 10,
  x: 0.5,
  y: 0.5,
  ...over,
  props: { states: { idle: "1", talk: "2" }, w: 400, ...(over.props || {}) },
});

test("PNGtuber: состояние по comp.voice, кадр анимации по времени клипа, альфа — прозрачно", () => {
  const anims = { 1: fakeAnim(["#ff0000", "#00ff00"]), 2: fakeAnim(["#0000ff", "#ffff00"]) };
  const env = { width: 1080, height: 1920, getImage: () => null, getAnim: (ref) => anims[ref] ?? null };
  // Голос: 0–1 с молчит, 1–2 с говорит (fps 2: "0011").
  const voice = { av: { t0: 0, fps: 2, states: "0011" } };
  const at = (t) => {
    const cv = createCanvas(1080, 1920);
    const ctx = cv.getContext("2d");
    E.drawLayer(ctx, pngtuberLayer(), t, env, { voice });
    return (x, y) => [...ctx.getImageData(x, y, 1, 1).data];
  };
  assert.deepEqual(at(0.2)(540, 960), [255, 0, 0, 255]); // молчит, кадр 0
  assert.deepEqual(at(0.7)(540, 960), [0, 255, 0, 255]); // молчит, кадр 1 (0.5 с на кадр)
  assert.deepEqual(at(1.2)(540, 960), [0, 0, 255, 255]); // говорит: talk, кадр 0 (фаза — время клипа)
  assert.deepEqual(at(1.7)(540, 960), [255, 255, 0, 255]); // говорит, кадр 1
  // Прозрачный край кадра остаётся прозрачным (400 / 8 = 50 px на пиксель исходника).
  assert.equal(at(1.2)(540 - 200 + 10, 960)[3], 0);
  // Голоса нет — молчит; рамка слоя — по пропорции idle.
  assert.deepEqual(at(5)(540, 960).slice(0, 3), [255, 0, 0]);
  const box = E.layerBox(createCanvas(10, 10).getContext("2d"), pngtuberLayer(), 1, env, { voice });
  near(box.w, 400, 1e-6);
  near(box.h, 400, 1e-6);
  // Анимации нет (статичная картинка) — рисуется getImage.
  const still = createCanvas(4, 4);
  still.getContext("2d").fillStyle = "#ff00ff";
  still.getContext("2d").fillRect(0, 0, 4, 4);
  const envImg = { width: 1080, height: 1920, getImage: (ref) => (ref === "2" ? still : null) };
  const cv = createCanvas(1080, 1920);
  E.drawLayer(cv.getContext("2d"), pngtuberLayer(), 1.2, envImg, { voice });
  assert.deepEqual([...cv.getContext("2d").getImageData(540, 960, 1, 1).data], [255, 0, 255, 255]);
});

test("PNGtuber: пиксель-арт без сглаживания — соседние пиксели не размыты; pixelated: false — мягко", () => {
  // Шахматка 4×4 → 400 px: клетка 100 px, на границе клеток только чистые цвета.
  const cv = createCanvas(4, 4);
  const x = cv.getContext("2d");
  for (let i = 0; i < 4; i++) {
    for (let j = 0; j < 4; j++) {
      x.fillStyle = (i + j) % 2 ? "#ffffff" : "#000000";
      x.fillRect(i, j, 1, 1);
    }
  }
  const env = { width: 1080, height: 1920, getImage: (ref) => (ref === "1" ? cv : null) };
  const row = (layer) => {
    const out = createCanvas(1080, 1920);
    const ctx = out.getContext("2d");
    E.drawLayer(ctx, layer, 1, env, {});
    return [...ctx.getImageData(340, 1010, 400, 1).data].filter((_v, k) => k % 4 === 0);
  };
  const sharp = row(pngtuberLayer({ props: { states: { idle: "1", talk: "1" } } }));
  assert.ok(sharp.every((v) => v === 0 || v === 255), "только чёрный и белый");
  assert.notEqual(sharp[99], sharp[100]); // граница клетки — ровно через 100 px, без полутонов
  assert.equal(sharp[0], sharp[99]);
  const soft = row(pngtuberLayer({ props: { states: { idle: "1", talk: "1" }, pixelated: false } }));
  assert.ok(soft.some((v) => v > 20 && v < 235), "со сглаживанием есть полутона");
});

test("PNGtuber: подскок в такт огибающей только когда говорит, flipX зеркалит", () => {
  const anims = { 1: fakeAnim(["#ff0000"]), 2: fakeAnim(["#0000ff"]) };
  const env = { width: 1080, height: 1920, getImage: () => null, getAnim: (ref) => anims[ref] ?? null };
  const voice = { av: { t0: 0, fps: 1, states: "01", levels: [0, 100] } };
  const topEdge = (t) => {
    const cv = createCanvas(1080, 1920);
    const ctx = cv.getContext("2d");
    E.drawLayer(ctx, pngtuberLayer({ props: { bounce: { amount: 30 } } }), t, env, { voice });
    const col = ctx.getImageData(540, 0, 1, 1920).data;
    for (let y = 0; y < 1920; y++) if (col[y * 4 + 3] > 0) return y;
    return -1;
  };
  const still = topEdge(0.5);
  assert.equal(still, 960 - 200 + 50); // край кадра (1 px из 8) — 50 px прозрачный
  const up = topEdge(1.5);
  assert.ok(still - up >= 30, `подскок ${still - up}`); // вверх на amount и растяжение
  // flipX: асимметричный кадр отражается.
  const half = createCanvas(2, 1);
  half.getContext("2d").fillStyle = "#00ff00";
  half.getContext("2d").fillRect(0, 0, 1, 1);
  const envFlip = { width: 1080, height: 1920, getImage: (ref) => (ref === "1" ? half : null) };
  const side = (flipX) => {
    const cv = createCanvas(1080, 1920);
    const ctx = cv.getContext("2d");
    E.drawLayer(ctx, pngtuberLayer({ props: { states: { idle: "1", talk: "1" }, flipX } }), 0.5, envFlip, {});
    return ctx.getImageData(440, 960, 1, 1).data[3];
  };
  assert.equal(side(false), 255);
  assert.equal(side(true), 0);
});

test("image с анимированным файлом (getAnim) играет кадры по локальному времени слоя", () => {
  const anim = fakeAnim(["#ff0000", "#00ff00", "#0000ff"], 0.2);
  const env = { width: 1080, height: 1920, getImage: () => null, getAnim: (ref) => (ref === "scr" ? anim : null) };
  const c = (t) => drawAt(imageLayer({ props: { asset_id: "scr", w: 400 } }), t, env).px(540, 960).slice(0, 3);
  assert.deepEqual(c(10.1), [255, 0, 0]); // start 10 — гифка с первого кадра
  assert.deepEqual(c(10.3), [0, 255, 0]);
  assert.deepEqual(c(10.5), [0, 0, 255]);
  assert.deepEqual(c(10.7), [255, 0, 0]); // по кругу
  // Без анимации — прежняя статичная картинка (обратная совместимость).
  const still = createCanvas(4, 4);
  still.getContext("2d").fillStyle = "#123456";
  still.getContext("2d").fillRect(0, 0, 4, 4);
  const plain = { width: 1080, height: 1920, getImage: (ref) => (ref === "scr" ? still : null) };
  assert.deepEqual(drawAt(imageLayer({ props: { asset_id: "scr", w: 400 } }), 10.3, plain).px(540, 960).slice(0, 3), [0x12, 0x34, 0x56]);
});

test("CLI: PNGtuber из кадров anims.json (PNG с альфой) и comp.voice", async () => {
  const mk = async (dir, colors) => {
    mkdirSync(dir, { recursive: true });
    for (let i = 0; i < colors.length; i++) {
      const cv = createCanvas(8, 8);
      const x = cv.getContext("2d");
      x.fillStyle = colors[i];
      x.fillRect(2, 0, 4, 8); // по бокам — прозрачно
      writeFileSync(join(dir, `${String(i).padStart(5, "0")}.png`), await cv.encode("png"));
    }
  };
  await mk(join(tmp, "anim-idle"), ["#ff0000", "#00ff00"]);
  await mk(join(tmp, "anim-talk"), ["#0000ff", "#ffffff"]);
  const anims = {
    11: { dir: join(tmp, "anim-idle"), pattern: "%05d.png", count: 2, times: [0, 0.5], duration: 1, width: 8, height: 8 },
    12: { dir: join(tmp, "anim-talk"), pattern: "%05d.png", count: 2, times: [0, 0.5], duration: 1, width: 8, height: 8 },
  };
  writeFileSync(join(tmp, "panims.json"), JSON.stringify(anims));
  const comp = {
    width: 540,
    height: 960,
    fps: 2,
    duration: 4,
    layers: [{ id: "av", type: "pngtuber", start: 0, end: 4, x: 0.5, y: 0.5, props: { states: { idle: "11", talk: "12" }, w: 400 } }],
    voice: { av: { t0: 0, fps: 2, states: "00110000" } },
  };
  writeFileSync(join(tmp, "pcomp.json"), JSON.stringify(comp));
  const frameAt = async (t) => {
    const png = join(tmp, `p-${t}.png`);
    const r = run(["--comp", join(tmp, "pcomp.json"), "--anims", join(tmp, "panims.json"), "--frame", String(t), "--png", png]);
    assert.equal(r.status, 0, r.stderr);
    const img = await loadImage(readFileSync(png));
    const cv = createCanvas(img.width, img.height);
    const x = cv.getContext("2d");
    x.drawImage(img, 0, 0);
    return (px, py) => [...x.getImageData(px, py, 1, 1).data];
  };
  // 400 опорных px на кадре 540 — 200 px, пиксель исходника — 25 px, без сглаживания.
  let p = await frameAt(0.2);
  assert.deepEqual(p(270, 480), [255, 0, 0, 255]); // молчит, кадр 0
  assert.equal(p(270 - 100 + 10, 480)[3], 0); // прозрачный бок
  p = await frameAt(1.2);
  assert.deepEqual(p(270, 480), [0, 0, 255, 255]); // говорит
  p = await frameAt(1.7);
  assert.deepEqual(p(270, 480), [255, 255, 255, 255]); // говорит, кадр 1
  p = await frameAt(2.6);
  assert.deepEqual(p(270, 480), [0, 255, 0, 255]); // снова молчит, кадр 1
  // Видео целиком: кадры состояний в памяти, рендер не падает.
  const out = join(tmp, "pmotion.mp4");
  const r = run(["--comp", join(tmp, "pcomp.json"), "--anims", join(tmp, "panims.json"), "--output", out]);
  assert.equal(r.status, 0, r.stderr);
  assert.equal(Number(ffprobe(out).nb_read_frames), 8);
});

/* ---------- кинетическая типографика, эффект кадра, реакция на звук ---------- */

const pixels = (layers, t, extra = {}, base = null) => {
  const cv = createCanvas(540, 960);
  const ctx = cv.getContext("2d");
  if (base) base(ctx);
  E.drawFrame(ctx, { layers, ...extra }, t, { width: 540, height: 960, getImage: () => null, createCanvas: (w, h) => createCanvas(w, h) });
  return ctx.getImageData(0, 0, 540, 960).data;
};
const same = (a, b) => a.length === b.length && a.every((v, i) => v === b[i]);
const gradientBase = (ctx) => {
  const g = ctx.createLinearGradient(0, 0, 540, 960);
  g.addColorStop(0, "#1e3a8a");
  g.addColorStop(0.5, "#f59e0b");
  g.addColorStop(1, "#10b981");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, 540, 960);
  ctx.fillStyle = "#ffffff";
  for (let x = 0; x < 540; x += 60) ctx.fillRect(x, 0, 20, 960);
};

test("glitch/scramble — только у текста (у прочих fade), циклы glitch/wave — shake/float, stomp — у любого", () => {
  const shape = (over) => ({ ...textLayer(), type: "shape", props: { kind: "rect" }, ...over });
  const g = E.resolveLayer(shape({ in: { type: "glitch", duration: 1 } }), 10.5);
  const f = E.resolveLayer(shape({ in: { type: "fade", duration: 1 } }), 10.5);
  near(g.opacity, f.opacity);
  assert.equal(g.glitch, 0);
  const sh = E.resolveLayer(shape({ loop: { type: "glitch" } }), 11.3);
  const sk = E.resolveLayer(shape({ loop: { type: "shake" } }), 11.3);
  near(sh.x, sk.x);
  near(E.resolveLayer(shape({ loop: { type: "wave" } }), 11.3).y, E.resolveLayer(shape({ loop: { type: "float" } }), 11.3).y);
  // Текст: цикл wave — волна букв (y слоя на месте), glitch — вспышки силы глитча.
  const w = E.resolveLayer(textLayer({ loop: { type: "wave", amount: 2, speed: 1.5 } }), 11);
  assert.equal(w.wave, 2);
  near(w.y, 0.5);
  const bursts = Array.from({ length: 200 }, (_, i) => E.resolveLayer(textLayer({ loop: { type: "glitch" } }), 10 + i * 0.02).glitch);
  assert.ok(bursts.some((v) => v > 0.3) && bursts.filter((v) => v > 0).length < 150, "вспышки короткие и редкие");
  // Удар: влетает крупным, к концу входа — ровно на месте; встряска одна и та же при повторе.
  const stomp = (t) => E.resolveLayer(shape({ in: { type: "stomp", duration: 0.5 } }), t);
  assert.ok(stomp(10.01).scale > 1.8);
  near(stomp(10.5).scale, 1);
  near(stomp(10.5).x, 0.5);
  assert.deepEqual(stomp(10.3), stomp(10.3));
  assert.ok(Math.abs(stomp(10.3).x - 0.5) > 1e-5, "после удара — встряска");
});

test("scramble: перебор детерминирован, буквы из набора с тем же регистром; после входа — как без анимации", () => {
  const up = E.scrambleChar("Ж", 42, 7, 3);
  const low = E.scrambleChar("ж", 42, 7, 3);
  assert.equal(up, E.scrambleChar("Ж", 42, 7, 3));
  assert.match(up, /^[А-ЯA-Z0-9]$/);
  assert.match(low, /^[а-яa-z0-9]$/);
  assert.notEqual(E.scrambleChar("Ж", 42, 8, 3) + E.scrambleChar("Ж", 42, 9, 3) + E.scrambleChar("Ж", 42, 10, 3), up.repeat(3));
  const props = { text: "Пароль 2077", size: 90, color: "#ffffff" };
  const plain = pixels([textLayer({ props })], 12);
  const scr = textLayer({ props, in: { type: "scramble", duration: 1 } });
  assert.ok(same(pixels([scr], 11.5), plain), "после входа — те же пиксели");
  assert.ok(same(pixels([scr], 10.4), pixels([scr], 10.4)), "превью == рендер");
  assert.ok(!same(pixels([scr], 10.4), plain), "в середине входа — перебор");
  assert.ok(pixels([scr], 10).every((v, i) => i % 4 !== 3 || v === 0), "в начале — пусто");
});

test("glitch и wave текста: кадр детерминирован, вне вспышки/без волны — как обычный текст", () => {
  const props = { text: "СБОЙ", size: 140, color: "#ffffff", stroke: { color: "#000000", width: 8 } };
  const plain = pixels([textLayer({ props })], 12);
  const gl = textLayer({ props, in: { type: "glitch", duration: 0.6 } });
  assert.ok(same(pixels([gl], 10.2), pixels([gl], 10.2)));
  assert.ok(!same(pixels([gl], 10.2), plain), "во входе — расслоение");
  assert.ok(same(pixels([gl], 11), plain), "после входа — чисто");
  // Цветные копии: рядом с белыми буквами есть красные и голубые пиксели.
  const d = pixels([gl], 10.15);
  let red = 0;
  let cyan = 0;
  for (let i = 0; i < d.length; i += 4) {
    if (d[i + 3] < 100) continue;
    if (d[i] > 200 && d[i + 1] < 90 && d[i + 2] < 140) red++;
    if (d[i] < 90 && d[i + 1] > 200 && d[i + 2] > 200) cyan++;
  }
  assert.ok(red > 50 && cyan > 50, `копии видны: красных ${red}, голубых ${cyan}`);
  const wv = textLayer({ props, loop: { type: "wave", amount: 1.5 } });
  assert.ok(same(pixels([wv], 10.7), pixels([wv], 10.7)));
  assert.ok(!same(pixels([wv], 10.7), plain), "буквы волной");
});

test("маркер: прорисовка слева направо после входа, буквы своего цвета; плашка с draw — тоже", () => {
  const hl = { words: [1], style: "marker", bg: "#ffd400", color: "#ffd400", draw: 0.5 };
  const layer = textLayer({ props: { text: "очень важно", size: 120, color: "#ffffff", highlight: hl }, in: { type: "fade", duration: 0.4 } });
  const st = (t) => E.resolveLayer(layer, t);
  near(E.highlightProgress(layer, st(10.3)), 0); // до конца входа маркера нет
  near(E.highlightProgress(layer, st(10.9)), 1);
  assert.ok(E.highlightProgress(layer, st(10.65)) > 0.2 && E.highlightProgress(layer, st(10.65)) < 0.8);
  near(E.highlightProgress({ ...layer, props: { ...layer.props, highlight: { ...hl, delay: 1.2 } } }, st(11)), 0);
  const yellow = (d) => {
    let n = 0;
    for (let i = 0; i < d.length; i += 4) if (d[i] > 200 && d[i + 1] > 170 && d[i + 2] < 60 && d[i + 3] > 200) n++;
    return n;
  };
  const half = yellow(pixels([layer], 10.65));
  const full = yellow(pixels([layer], 12));
  assert.ok(full > 2000 && half > 100 && half < full * 0.85, `маркер растёт: ${half} → ${full}`);
  // Буквы слова под маркером — белые (цвет как у маркера не берётся): белых почти столько же, сколько
  // без маркера (края букв на жёлтом смешиваются), а жёлтыми буквами их было бы вдвое меньше.
  const white = (d) => {
    let n = 0;
    for (let i = 0; i < d.length; i += 4) if (d[i] > 240 && d[i + 1] > 240 && d[i + 2] > 240) n++;
    return n;
  };
  const bare = textLayer({ props: { text: "очень важно", size: 120, color: "#ffffff" } });
  assert.ok(white(pixels([layer], 12)) > white(pixels([bare], 12)) * 0.85);
  // Плашка с draw тоже прорисовывается: на полпути её меньше, чем в конце.
  const plate = textLayer({ props: { text: "очень важно", size: 120, color: "#ffffff", highlight: { words: [1], bg: "#ffd400", color: "#111111", draw: 0.5 } }, in: { type: "fade", duration: 0.4 } });
  assert.ok(yellow(pixels([plate], 10.65)) < yellow(pixels([plate], 12)) * 0.85);
});

test("fx: каждый вид меняет кадр одинаково дважды; слои выше по дорожке — чистые; прозрачность гасит", () => {
  const top = { ...textLayer(), id: "top", type: "shape", track: 3, x: 0.5, y: 0.2, props: { kind: "rect", w: 300, h: 200, fill: "#00ff00" } };
  const under = { ...textLayer(), id: "under", type: "shape", track: 0, x: 0.5, y: 0.6, props: { kind: "ellipse", w: 500, h: 500, fill: "#ff00ff" } };
  const base = pixels([under, top], 11, {}, gradientBase);
  for (const kind of E.FX_KINDS) {
    const fx = { ...textLayer(), id: `fx_${kind}`, type: "fx", track: 1, start: 10.5, end: 12, props: { kind, intensity: 1 } };
    const a = pixels([under, fx, top], 10.62, {}, gradientBase);
    assert.ok(same(a, pixels([under, fx, top], 10.62, {}, gradientBase)), `${kind}: превью == рендер`);
    assert.ok(!same(a, base), `${kind}: кадр изменился`);
    // Центр зелёного прямоугольника над эффектом — ровно зелёный.
    const i = (192 * 540 + 270) * 4;
    assert.deepEqual([...a.slice(i, i + 4)], [0, 255, 0, 255], `${kind}: слой выше чистый`);
    const off = pixels([under, { ...fx, opacity: 0 }, top], 10.62, {}, gradientBase);
    assert.ok(same(off, base), `${kind}: opacity 0 — без эффекта`);
  }
  assert.equal(E.flashEnvelope(0, 0.4), 0);
  near(E.flashEnvelope(0.06, 0.4), 1);
  assert.ok(E.flashEnvelope(0.3, 0.4) < 0.2);
  assert.deepEqual(E.resolveFx({ id: "x", type: "fx", props: { kind: "нет-такого" } }).kind, "vhs");
  // Мышью эффект не ловится, рамка — весь кадр.
  const box = E.layerBox(createCanvas(8, 8).getContext("2d"), { ...textLayer(), type: "fx", props: { kind: "vhs" } }, 11, { width: 540, height: 960, getImage: () => null });
  assert.deepEqual(box, { cx: 270, cy: 480, w: 540, h: 960, rotation: 0 });
});

test("react: огибающая по кадрам, сглаживание окном назад; scale/opacity/y/glow; без levels — как было", () => {
  const track = { t0: 0, fps: 10, v: [0, 0, 100, 100, 0, 0, 50, 0, 0, 0] };
  near(E.levelAt(track, 0.25), 1);
  near(E.levelAt(track, 0.45), 0);
  near(E.levelAt(track, 5), 0);
  const sm = E.levelAt(track, 0.45, 0.4);
  assert.ok(sm > 0.2 && sm < 0.9, `сглажено: ${sm}`);
  near(E.levelAt(track, 0.45, 0.4), sm); // без состояния — то же при повторе
  const levels = { voice: { t0: 10, fps: 10, v: [100, 100, 0, 0] }, music: { t0: 10, fps: 10, v: [0, 0, 0, 0] } };
  const L = (react) => textLayer({ react });
  const loud = (react, t = 10.05) => E.resolveLayer(L(react), t, undefined, levels);
  near(loud({ prop: "scale", amount: 1, smooth: 0 }).scale, 1.25);
  near(loud({ prop: "scale", amount: 1, smooth: 0 }, 10.25).scale, 1);
  near(loud({ prop: "opacity", amount: 1, smooth: 0 }, 10.25).opacity, 0);
  near(loud({ prop: "y", amount: 1, smooth: 0 }).y, 0.5 - 40 / 1920);
  near(loud({ prop: "glow", amount: 2, smooth: 0 }).glow, 2);
  near(loud({ source: "music", prop: "scale", smooth: 0 }).scale, 1);
  near(E.resolveLayer(L({ prop: "scale" }), 10.05).scale, 1); // огибающих нет — слой глух
  // Свечение рисуется тенью: пикселей вокруг букв больше.
  const props = { text: "ГРОМКО", size: 120, color: "#ffffff" };
  const lit = (d) => d.reduce((n, v, i) => n + (i % 4 === 3 && v > 0 ? 1 : 0), 0);
  const glow = pixels([textLayer({ props, react: { prop: "glow", amount: 1.5, smooth: 0, color: "#ff00ff" } })], 10.05, { levels });
  assert.ok(lit(glow) > lit(pixels([textLayer({ props })], 10.05)) * 1.3);
});

test("субтитры stomp: слово видно с момента и «впечатывается», подсвеченное — сильнее; кадр детерминирован", () => {
  const words = [{ t0: 1, t1: 1.3, w: "БАХ" }, { t0: 1.4, t1: 1.8, w: "БУМ" }];
  const caps = (hl = []) => ({ id: "c", type: "captions", start: 0, end: 3, x: 0.5, y: 0.5, props: { style: "stomp", highlightWords: hl } });
  const lit = (d) => d.reduce((n, v, i) => n + (i % 4 === 3 && v > 0 ? 1 : 0), 0);
  assert.equal(lit(pixels([caps()], 0.99, { words })), 0);
  const hit = pixels([caps()], 1.02, { words });
  const rest = pixels([caps()], 1.35, { words });
  assert.ok(same(hit, pixels([caps()], 1.02, { words })));
  assert.ok(lit(hit) > lit(rest) * 1.2, "на ударе слово крупнее");
  assert.ok(lit(pixels([caps([{ i: 0, color: "#ff3b30" }])], 1.02, { words })) > lit(hit), "акцент бьёт сильнее");
  assert.ok(E.CAPTION_STYLES.some((s) => s.id === "stomp" && s.props.animation === "stomp"));
});

test("субтитры: эмфаза слова (comp.words[i].e) — крупнее всю страницу, прыжок в момент, встряска у shake", () => {
  const ctx = createCanvas(8, 8).getContext("2d");
  const plain = [{ t0: 1, t1: 1.3, w: "ДЕНЬГИ" }, { t0: 1.4, t1: 1.8, w: "ТУТ" }];
  const pop = [{ ...plain[0], e: 1 }, plain[1]];
  const shake = [{ ...plain[0], e: 2 }, plain[1]];
  const [page] = E.captionPages(ctx, pop, { style: "hormozi" }, 0, 3, "Montserrat");
  assert.equal(page.words[0].e, 1);
  near(page.words[0].es, 1.25);
  near(page.emph, 1.25);
  near(E.captionPages(ctx, shake, { style: "hormozi" }, 0, 3, "Montserrat")[0].words[0].es, 1.45);
  near(E.captionPages(ctx, pop, { style: "hormozi", emphScale: 1.5 }, 0, 3, "Montserrat")[0].words[0].es, 1.5);
  const [flat] = E.captionPages(ctx, plain, { style: "hormozi" }, 0, 3, "Montserrat");
  assert.ok(page.w > flat.w && page.h > flat.h, "строка с эмфазой шире и выше");
  assert.deepEqual(E.captionWordsFrom([{ word: "да", start: 1, end: 2, emph: 2 }]), [{ t0: 1, t1: 2, w: "да", e: 2 }]);

  const caps = { id: "c", type: "captions", start: 0, end: 3, x: 0.5, y: 0.5, props: { style: "hormozi" } };
  const lit = (d) => d.reduce((n, v, i) => n + (i % 4 === 3 && v > 0 ? 1 : 0), 0);
  // Страница стоит (говорят «ТУТ»): ключевое слово всё равно крупнее обычного, у shake — ещё крупнее.
  const calm = lit(pixels([caps], 1.6, { words: plain }));
  const calmPop = lit(pixels([caps], 1.6, { words: pop }));
  assert.ok(calmPop > calm * 1.1, `эмфаза крупнее: ${calmPop} vs ${calm}`);
  assert.ok(lit(pixels([caps], 1.6, { words: shake })) > calmPop * 1.05);
  // Прыжок: на пике (0.09 с от начала слова) слово крупнее, чем у того же слова без эмфазы.
  const peak = lit(pixels([caps], 1.09, { words: pop }));
  assert.ok(peak > lit(pixels([caps], 1.09, { words: plain })) * 1.4);
  assert.ok(peak > lit(pixels([caps], 1.3, { words: pop })), "после прыжка слово возвращается");
  // Встряска (shake) — тот же кадр каждый раз (превью == рендер); слово сдвинуто от покоя.
  const jolt = pixels([caps], 1.2, { words: shake });
  assert.ok(same(jolt, pixels([caps], 1.2, { words: shake })));
  const still = { ...caps, props: { ...caps.props, emphScale: 1 + 0.25 * 1.8 } }; // тот же кегль, без встряски
  const diff = (a, b) => a.reduce((n, v, i) => n + (Math.abs(v - b[i]) > 60 ? 1 : 0), 0);
  assert.ok(diff(jolt, pixels([still], 1.2, { words: pop })) > 200, "встряска сдвигает слово");
  assert.equal(diff(pixels([caps], 1.7, { words: shake }), pixels([still], 1.7, { words: pop })), 0, "потом затухает");
});

test("CLI: эффект кадра и огибающая громкости из comp.json", async () => {
  const comp = {
    width: 270,
    height: 480,
    fps: 10,
    duration: 1,
    layers: [
      { id: "t", type: "text", start: 0, end: 1, x: 0.5, y: 0.4, react: { source: "mix", prop: "scale", amount: 2, smooth: 0 }, props: { text: "ОК", size: 200, color: "#ffffff" } },
      { id: "f", type: "fx", start: 0, end: 1, track: 2, props: { kind: "grain_overlay", intensity: 1 } },
    ],
    levels: { mix: { t0: 0, fps: 10, v: [0, 0, 0, 0, 0, 100, 100, 100, 100, 100] }, junk: 5 },
  };
  writeFileSync(join(tmp, "fxcomp.json"), JSON.stringify(comp));
  const frame = async (t) => {
    const png = join(tmp, `fx-${t}.png`);
    const r = run(["--comp", join(tmp, "fxcomp.json"), "--frame", String(t), "--png", png]);
    assert.equal(r.status, 0, r.stderr);
    const img = await loadImage(readFileSync(png));
    const cv = createCanvas(img.width, img.height);
    cv.getContext("2d").drawImage(img, 0, 0);
    return cv.getContext("2d").getImageData(0, 0, img.width, img.height).data;
  };
  const quiet = await frame(0.2);
  const loud = await frame(0.7);
  const opaque = (d) => d.reduce((n, v, i) => n + (i % 4 === 3 && v > 250 ? 1 : 0), 0);
  assert.ok(opaque(loud) > opaque(quiet) * 1.5, "громко — текст крупнее");
});

/* ---------- плашка канала (nameplate) ---------- */

const plateLayer = (over = {}) => ({
  id: "np",
  type: "nameplate",
  start: 0,
  end: 10,
  x: (24 + 260) / 1080,
  y: 0.6,
  track: 4,
  ...over,
  props: { platform: "twitch", handle: "sareamura", style: "glass", size: 74, align: "left", boxWidth: 520, ...(over.props || {}) },
});

test("плашка канала: рамка boxWidth, пилюля у левого края, значок цветом площадки, справа пусто", () => {
  const env = { width: 1080, height: 1920, getImage: () => null };
  const ctx = createCanvas(10, 10).getContext("2d");
  const box = E.layerBox(ctx, plateLayer(), 1, env);
  near(box.w, 520, 1e-6);
  near(box.h, 74, 1e-6);
  // Без boxWidth рамка — по пилюле: поля + значок + ник, не уже высоты.
  const tight = E.layerBox(ctx, plateLayer({ props: { boxWidth: undefined } }), 1, env);
  assert.ok(tight.w > 74 * 2 && tight.w < 520, `ширина ${tight.w}`);
  const cv = createCanvas(1080, 1920);
  const c = cv.getContext("2d");
  E.drawLayer(c, plateLayer(), 1, env);
  const cy = 0.6 * 1920;
  // Левый край пилюли — ровно x 24: левее пусто, у края — обводка #9146ff.
  assert.equal(pxOf(c, 20, cy)[3], 0);
  const edge = pxOf(c, 25, cy);
  assert.ok(edge[3] > 200 && edge[2] > 200 && edge[1] < 120, `обводка ${edge}`);
  // Значок Twitch: тело цветом площадки, полоски — дырки (видна тёмная плашка).
  const iconLeft = 24 + 74 * 0.42;
  const iconH = 74 * 0.56;
  const iconW = 0.86 * iconH;
  const body = pxOf(c, iconLeft + iconW * 0.2, cy - iconH * 0.2);
  assert.ok(body[2] > 200 && body[0] > 110 && body[1] < 110, `тело значка ${body}`);
  const bar = pxOf(c, iconLeft + iconW * 0.475, cy - iconH * 0.15);
  assert.ok(bar[0] < 60 && bar[2] < 60, `полоска-дырка ${bar}`);
  // Правее пилюли (в рамке 520) — пусто: пилюля по нику, а не во всю рамку.
  assert.equal(pxOf(c, 24 + 510, cy)[3], 0);
  // align right — пилюля у правого края рамки.
  const cv2 = createCanvas(1080, 1920);
  E.drawLayer(cv2.getContext("2d"), plateLayer({ x: 1 - (24 + 260) / 1080, props: { align: "right" } }), 1, env);
  assert.ok(pxOf(cv2.getContext("2d"), 1080 - 26, cy)[3] > 200);
  assert.equal(pxOf(cv2.getContext("2d"), 1080 - 20, cy)[3], 0);
});

test("плашка канала: значок каждой площадки рисуется, none — без значка, solid на светлом акценте — тёмный ник", () => {
  const env = { width: 1080, height: 1920, getImage: () => null };
  const inked = (layer, x0, x1) => {
    const cv = createCanvas(1080, 1920);
    const c = cv.getContext("2d");
    E.drawLayer(c, layer, 1, env);
    const d = c.getImageData(Math.round(x0), Math.round(0.6 * 1920 - 20), Math.round(x1 - x0), 40).data;
    let bright = 0;
    for (let i = 0; i < d.length; i += 4) if (d[i + 3] > 200 && d[i] + d[i + 1] + d[i + 2] > 300) bright++;
    return bright;
  };
  for (const platform of ["twitch", "youtube", "tiktok", "telegram", "kick"]) {
    assert.ok(E.platformIcon(platform), platform);
    // Только значок (без ника): в середине пилюли — светлые пиксели значка.
    const n = inked(plateLayer({ props: { platform, handle: "" } }), 24 + 10, 24 + 64);
    assert.ok(n > 40, `${platform}: значок пуст (${n})`);
  }
  assert.equal(E.platformIcon("none"), null);
  assert.equal(E.platformIcon("vk"), null);
  // Цвет площадки — акцент по умолчанию; свой accent перекрывает.
  assert.equal(E.nameplateAccent({ platform: "youtube" }), "#ff0033");
  assert.equal(E.nameplateAccent({ platform: "twitch", accent: "#00ff00" }), "#00ff00");
  // solid на светлом акценте (Kick) — ник тёмный, на тёмном (Twitch) — белый.
  const nick = (accent) => {
    const cv = createCanvas(1080, 1920);
    const c = cv.getContext("2d");
    E.drawLayer(c, plateLayer({ props: { platform: "none", style: "solid", accent, handle: "IIIIIIII", size: 120 } }), 1, env);
    const d = c.getImageData(24 + 60, Math.round(0.6 * 1920 - 20), 300, 40).data;
    let dark = 0;
    let white = 0;
    for (let i = 0; i < d.length; i += 4) {
      if (d[i + 3] < 200) continue;
      if (d[i] + d[i + 1] + d[i + 2] < 120) dark++;
      if (d[i] > 240 && d[i + 1] > 240 && d[i + 2] > 240) white++;
    }
    return { dark, white };
  };
  const kick = nick("#53fc18");
  const twitch = nick("#9146ff");
  assert.ok(kick.dark > 100 && kick.white === 0, `kick ${JSON.stringify(kick)}`);
  assert.ok(twitch.white > 100 && twitch.dark === 0, `twitch ${JSON.stringify(twitch)}`);
  // Пустая плашка (ни ника, ни значка) ничего не рисует.
  const cv = createCanvas(1080, 1920);
  E.drawLayer(cv.getContext("2d"), plateLayer({ props: { platform: "none", handle: "" } }), 1, env);
  assert.equal(pxOf(cv.getContext("2d"), 24 + 37, 0.6 * 1920)[3], 0);
});

test("плашка канала: кадр компоновщика (рендер) = кадр движка (превью) пиксель в пиксель; шаблон", async () => {
  // Превью — тот же движок с тем же шрифтом: регистрируем Montserrat ExtraBold, как компоновщик.
  const { GlobalFonts } = await import("@napi-rs/canvas");
  GlobalFonts.registerFromPath(join(FONTS, "Montserrat-ExtraBold.ttf"), "Montserrat");
  E.invalidateTextCache();
  const layers = [
    plateLayer({ shadow: { color: "#000000", blur: 14, x: 0, y: 4, opacity: 0.45 } }),
    plateLayer({ id: "np2", x: 1 - (24 + 260) / 1080, y: 0.13, props: { platform: "youtube", handle: "shop", style: "solid", align: "right" } }),
  ];
  const comp = { width: 1080, height: 1920, fps: 30, duration: 3, layers };
  writeFileSync(join(tmp, "np.json"), JSON.stringify(comp));
  writeFileSync(join(tmp, "np.assets.json"), "{}");
  const png = join(tmp, "np.png");
  const r = run(["--comp", join(tmp, "np.json"), "--assets", join(tmp, "np.assets.json"), "--frame", "1", "--png", png]);
  assert.equal(r.status, 0, r.stderr);
  const img = await loadImage(readFileSync(png));
  const a = createCanvas(1080, 1920);
  a.getContext("2d").drawImage(img, 0, 0);
  const b = createCanvas(1080, 1920);
  E.drawFrame(b.getContext("2d"), { layers }, 1, { width: 1080, height: 1920, getImage: () => null, createCanvas: (w, h) => createCanvas(w, h) });
  const da = a.getContext("2d").getImageData(0, 0, 1080, 1920).data;
  const db = b.getContext("2d").getImageData(0, 0, 1080, 1920).data;
  let diff = 0;
  let inked = 0;
  for (let i = 0; i < da.length; i += 4) {
    if (da[i + 3] > 0) inked++;
    if (Math.abs(da[i] - db[i]) > 2 || Math.abs(da[i + 1] - db[i + 1]) > 2 || Math.abs(da[i + 2] - db[i + 2]) > 2 || Math.abs(da[i + 3] - db[i + 3]) > 2) diff++;
  }
  assert.ok(inked > 5000, `плашки не нарисованы (${inked})`);
  assert.ok(diff <= inked * 0.002, `расхождение рендера и превью: ${diff} из ${inked}`);
  // Шаблон «Плашка канала»: слой nameplate у края кадра, весь ролик.
  const tpl = E.buildTemplate("channel-plate", { handle: "sareamura" }, 0);
  assert.equal(tpl.layers[0].type, "nameplate");
  assert.equal(tpl.layers[0].props.align, "left");
  near(tpl.layers[0].x * 1080 - tpl.layers[0].props.boxWidth / 2, 24, 1e-6);
});

/** Сколько пикселей кадров расходятся больше чем на 2 уровня (и сколько вообще нарисовано). */
function frameDiff(ca, cb, w, h) {
  const da = ca.getContext("2d").getImageData(0, 0, w, h).data;
  const db = cb.getContext("2d").getImageData(0, 0, w, h).data;
  let diff = 0;
  let inked = 0;
  for (let i = 0; i < da.length; i += 4) {
    if (da[i + 3] > 0) inked++;
    if (Math.abs(da[i] - db[i]) > 2 || Math.abs(da[i + 1] - db[i + 1]) > 2 || Math.abs(da[i + 2] - db[i + 2]) > 2 || Math.abs(da[i + 3] - db[i + 3]) > 2) diff++;
  }
  return { diff, inked };
}

test("плашка канала: анимация — вход, блик, тик, LIVE, свёртка; кадр превью = кадр рендера на каждом пресете", async () => {
  const { GlobalFonts } = await import("@napi-rs/canvas");
  GlobalFonts.registerFromPath(join(FONTS, "Montserrat-ExtraBold.ttf"), "Montserrat");
  E.invalidateTextCache();
  const P = { handle: "sareamura" };
  // Ход входа: значок выпрыгивает раньше пилюли, ник — по буквам, к концу входа всё на месте.
  const m = (local, props = {}, dur = 20) => E.nameplateMotion({ ...P, ...props }, local, dur, 9);
  assert.equal(m(0).pop, 0);
  assert.ok(m(0.2).pop > 0.8 && m(0.2).open < 0.3 && m(0.2).front === 0, JSON.stringify(m(0.2)));
  assert.ok(m(0.28).pop > 1, "перелёт outBack");
  assert.ok(m(0.5).front > 0 && m(0.5).front < 9);
  assert.deepEqual([m(0.8).pop, m(0.8).open, m(0.8).front >= 9 + m(0.8).soft], [1, 1, true]);
  // Блик и тик — редко: subtle с 6.6 с каждые 7, lively — тик чаще.
  assert.equal(m(3).shine, null);
  assert.ok(m(7).shine > 0 && m(7).shine < 1 && m(7).tick > 0);
  assert.equal(m(8).shine, null);
  assert.ok(m(13.9).shine > 0);
  assert.ok(m(3.3, { anim: "lively" }).tick > 0 && m(3.3).tick === null);
  // none — всё на месте с первого кадра; LIVE — фаза пульса; свёртка — вход назад в конце.
  assert.deepEqual([m(0, { anim: "none" }).pop, m(0, { anim: "none" }).open, m(7, { anim: "none" }).shine], [1, 1, null]);
  assert.equal(m(0.8, { live: true }).live, 0.5);
  assert.equal(m(19.99, { outro: "collapse" }).pop < 0.2, true);
  assert.equal(m(19.6, { outro: "collapse" }).open < 1, true);
  assert.equal(m(19.6).open, 1);
  // Детерминированно: тот же момент — тот же кадр.
  const env = { width: 1080, height: 1920, getImage: () => null, createCanvas: (w, h) => createCanvas(w, h) };
  const draw = (layer, t) => {
    const cv = createCanvas(1080, 1920);
    E.drawFrame(cv.getContext("2d"), { layers: [layer] }, t, env);
    return cv;
  };
  const lay = (anim, over = {}) => plateLayer({ end: 20, shadow: { color: "#000000", blur: 14, x: 0, y: 4, opacity: 0.45 }, ...over, props: { anim, ...(over.props || {}) } });
  assert.equal(frameDiff(draw(lay("subtle"), 7), draw(lay("subtle"), 7), 1080, 1920).diff, 0);
  // Блик виден: в 7 с пилюля светлее, чем в 5 с; в 0.1 с ника ещё нет.
  const ink = (cv) => {
    const d = cv.getContext("2d").getImageData(24, Math.round(0.6 * 1920 - 37), 400, 74).data;
    let s = 0;
    for (let i = 0; i < d.length; i += 4) s += (d[i] + d[i + 1] + d[i + 2]) * (d[i + 3] / 255);
    return s;
  };
  assert.ok(ink(draw(lay("lively"), 7)) > ink(draw(lay("lively"), 5)) * 1.02, "блик");
  const early = draw(lay("subtle"), 0.1).getContext("2d").getImageData(24 + 120, Math.round(0.6 * 1920 - 20), 250, 40).data;
  assert.ok(early.every((v, i) => i % 4 !== 3 || v === 0), "в 0.1 с ника ещё нет");
  // LIVE: красная точка справа от ника.
  const live = draw(lay("none", { props: { live: true } }), 2);
  const box = E.layerBox(live.getContext("2d"), lay("none", { props: { live: true, boxWidth: undefined } }), 2, env);
  const dot = pxOf(live.getContext("2d"), 24 + box.w - 74 * 0.42 - 74 * 0.11, 0.6 * 1920);
  assert.ok(dot[0] > 200 && dot[1] < 90, `точка LIVE ${dot}`);

  // Кадр компоновщика = кадр движка: каждый пресет в 0.1 / 0.4 / 0.8 / 7 с.
  for (const anim of ["none", "subtle", "lively"]) {
    const layers = [
      lay(anim, { props: { live: anim === "lively" } }),
      lay(anim, { id: "np2", x: 1 - (24 + 260) / 1080, y: 0.13, props: { platform: "youtube", handle: "shop", style: "solid", align: "right" } }),
      lay(anim, { id: "np3", y: 0.3, props: { platform: "telegram", handle: "vtubika_store", outro: "collapse" }, end: 7.3 }),
    ];
    writeFileSync(join(tmp, `npa-${anim}.json`), JSON.stringify({ width: 1080, height: 1920, fps: 30, duration: 20, layers }));
    writeFileSync(join(tmp, "npa.assets.json"), "{}");
    for (const t of [0.1, 0.4, 0.8, 7]) {
      const png = join(tmp, `npa-${anim}-${t}.png`);
      const r = run(["--comp", join(tmp, `npa-${anim}.json`), "--assets", join(tmp, "npa.assets.json"), "--frame", String(t), "--png", png]);
      assert.equal(r.status, 0, r.stderr);
      const img = await loadImage(readFileSync(png));
      const a = createCanvas(1080, 1920);
      a.getContext("2d").drawImage(img, 0, 0);
      const b = createCanvas(1080, 1920);
      E.drawFrame(b.getContext("2d"), { layers }, t, env);
      const { diff, inked } = frameDiff(a, b, 1080, 1920);
      assert.ok(inked > 300, `${anim} ${t}: пусто`);
      assert.ok(diff <= inked * 0.002, `${anim} ${t}: расхождение рендера и превью ${diff} из ${inked}`);
    }
  }
});

/* ---------- псевдо-3D: глубина, перспектива, 3D-камера ---------- */

const env3 = { width: 540, height: 960, getImage: () => null, createCanvas: (w, h) => createCanvas(w, h) };
const draw3 = (layers, camera, t = 1) => {
  const cv = createCanvas(540, 960);
  const ctx = cv.getContext("2d");
  E.drawFrame(ctx, { layers, camera }, t, env3);
  return ctx.getImageData(0, 0, 540, 960).data;
};
const alphaBox = (d) => {
  let x0 = 1e9, x1 = -1, y0 = 1e9, y1 = -1;
  for (let y = 0; y < 960; y++) for (let x = 0; x < 540; x++) if (d[(y * 540 + x) * 4 + 3] > 64) {
    x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y);
  }
  return { x0, x1, y0, y1, w: x1 - x0 + 1, h: y1 - y0 + 1 };
};
const plate = (over = {}) => ({ id: "p", type: "shape", start: 0, end: 5, x: 0.5, y: 0.5, props: { kind: "rect", w: 400, h: 600, fill: "#ff0000" }, ...over });

test("3D: проекция — в покое плоскость кадра один в один, глубина f0 — вдвое меньше, облёт крутит дальнее", () => {
  const f0 = E.focal(960, E.DEFAULT_FOV);
  const p = E.makeProjector(null, 540, 960, 0.5);
  assert.deepEqual(p(0, 0, 0), [270, 480]);
  const [x] = p(100, 0, 0);
  near(x, 370, 1e-9);
  near(p(100, 0, f0)[0], 320, 1e-9); // вдвое дальше от камеры — вдвое меньше
  assert.equal(p(0, 0, -f0), null); // за камерой — не рисуется
  // Облёт вокруг центра плоскости кадра: центр стоит, дальняя точка уходит, ближняя — в другую сторону (параллакс).
  const o = E.makeProjector({ z: 0, rotX: 0, rotY: 10, fov: 40, baseZ: 1000 }, 540, 960, 0.5);
  near(o(0, 0, 0)[0], 270, 1e-9);
  const far = o(0, 0, 500)[0] - 270;
  const close = o(0, 0, -300)[0] - 270;
  assert.ok(far * close < 0 && Math.abs(far) > 20, `параллакс: дальняя ${far}, ближняя ${close}`);
  // Dolly вперёд: ближнее растёт быстрее дальнего.
  const d = E.makeProjector({ z: 300, rotX: 0, rotY: 0, fov: 40, baseZ: 1000 }, 540, 960, 0.5);
  const grow = (z) => (d(100, 0, z)[0] - 270) / (p(100, 0, z)[0] - 270);
  assert.ok(grow(-200) > grow(0) && grow(0) > grow(600));
  // Старые ключи (наезд без 3D) — камера плоская, как раньше.
  assert.equal(E.has3dCamera([{ t: 0, zoom: 1.3, x: 0.4 }]), false);
  assert.equal(E.has3dCamera([{ t: 0, zoom: 1, rotY: 5 }]), true);
  near(E.camera3dAt([{ t: 0, z: 0, ease: "linear" }, { t: 2, z: 200 }], 1).z, 100, 1e-9);
});

test("3D: слой без 3D-полей рисуется как раньше; z: 0 = followCamera; глубина уменьшает слой", () => {
  const camera = [{ t: 0, zoom: 1.5, x: 0.5, y: 0.5 }];
  assert.ok(same(draw3([plate({ followCamera: true })], camera), draw3([plate({ z: 0 })], camera)), "z: 0 — слой в сцене, как followCamera");
  assert.ok(same(draw3([plate()], camera), draw3([plate({ rotX: 0, rotY: 0 })], camera)), "нулевые наклоны — тот же кадр");
  const f0ref = E.focal(1920, E.DEFAULT_FOV);
  const flat = alphaBox(draw3([plate()], []));
  const deep = alphaBox(draw3([plate({ z: f0ref })], []));
  near(deep.w / flat.w, 0.5, 0.02);
  near(deep.h / flat.h, 0.5, 0.02);
});

test("3D: перспектива сеткой — без швов, наклон сужает дальний край, изнанка не рисуется; кадр детерминирован", () => {
  const d = draw3([plate({ props: { kind: "rect", w: 400, h: 600, fill: "#ff000080" }, rotY: 30 })], []);
  let full = 0;
  let seam = 0;
  for (let y = 300; y < 660; y += 2) for (let x = 0; x < 540; x += 2) {
    const a = d[(y * 540 + x) * 4 + 3];
    if (a >= 126 && a <= 130) full++;
    else if (a > 20 && a < 120) seam++;
  }
  assert.ok(seam < full * 0.04, `швы: ${seam} из ${full}`);
  // rotY > 0 — правый край уходит вглубь: справа слой ниже, чем слева.
  const colH = (x) => { let n = 0; for (let y = 0; y < 960; y++) if (d[(y * 540 + x) * 4 + 3] > 64) n++; return n; };
  const box = alphaBox(d);
  assert.ok(colH(box.x1 - 3) < colH(box.x0 + 3) * 0.95, "дальний край ниже ближнего");
  assert.ok(same(d, draw3([plate({ props: { kind: "rect", w: 400, h: 600, fill: "#ff000080" }, rotY: 30 })], [])));
  assert.ok(draw3([plate({ rotY: 120 })], []).every((v, i) => i % 4 !== 3 || v === 0), "изнанка не видна");
  // Ключи наклона: переворот карточки — из ребра (90°) к лицу.
  const flip = plate({ keys: { rotY: [{ t: 0, v: 90 }, { t: 1, v: 0 }] } });
  assert.ok(alphaBox(draw3([flip], [], 0.2)).w < alphaBox(draw3([flip], [], 1.5)).w * 0.6);
});

test("3D: основа — плоскость на глубине baseZ закрывает кадр при облёте; в покое — как раньше", () => {
  const W = 540;
  const H = 960;
  const rest = { z: 0, rotX: 0, rotY: 0, fov: 40, baseZ: 1000 };
  const f0 = E.focal(H, 40);
  near(E.baseCover(rest, W, H, 0.5), (f0 + 500) / f0, 1e-9);
  assert.ok(E.baseCover({ ...rest, rotY: 12, rotX: -6 }, W, H, 0.5) > (f0 + 500) / f0);
  const src = createCanvas(W, H);
  const s = src.getContext("2d");
  s.fillStyle = "#33aa66";
  s.fillRect(0, 0, W, H);
  for (const cam of [rest, { ...rest, rotY: 12, rotX: -6, z: 200 }, { ...rest, z: -1600, fov: 25.5 }]) {
    const cv = createCanvas(W, H);
    const ctx = cv.getContext("2d");
    E.drawBasePlane(ctx, env3, src, cam, null, 0.5);
    const d = ctx.getImageData(0, 0, W, H).data;
    for (const [x, y] of [[0, 0], [W - 1, 0], [0, H - 1], [W - 1, H - 1], [270, 480]]) assert.equal(d[(y * W + x) * 4 + 3], 255, `угол ${x},${y} закрыт`);
  }
});

test("3D: шаблоны галереи — категория 3D, слои с глубиной и наклоном", () => {
  for (const id of ["phone-3d", "depth-title", "cards-fan"]) {
    const t = E.TEMPLATES.find((x) => x.id === id);
    assert.ok(t && t.category === "3D", id);
    const r = E.buildTemplate(id, {}, 2);
    assert.ok(r.layers.some((l) => typeof l.z === "number"), `${id}: глубина`);
    assert.ok(r.layers.every((l) => l.start >= 2 - 1e-6));
  }
  assert.ok(E.buildTemplate("depth-title", {}, 0).camera.some((k) => k.z > 0), "заголовок с глубиной — наезд dolly");
});

test("CLI: 3D-камера двигает основу и слои по-разному (параллакс), без 3D — кадр прежний", async () => {
  const base = join(tmp, "b3d.mp4");
  const r0 = spawnSync("ffmpeg", ["-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=270x480:r=10:d=1", "-pix_fmt", "yuv420p", base]);
  assert.equal(r0.status, 0, String(r0.stderr));
  const layer = { id: "t", type: "shape", start: 0, end: 1, x: 0.5, y: 0.5, z: -300, props: { kind: "rect", w: 200, h: 200, fill: "#ff00ff" } };
  const frame = async (name, camera, layers = [layer]) => {
    writeFileSync(join(tmp, `${name}.json`), JSON.stringify({ width: 270, height: 480, fps: 10, duration: 1, layers, camera }));
    const png = join(tmp, `${name}.png`);
    const r = run(["--comp", join(tmp, `${name}.json`), "--input", base, "--frame", "0.5", "--png", png]);
    assert.equal(r.status, 0, r.stderr);
    const img = await loadImage(readFileSync(png));
    const cv = createCanvas(img.width, img.height);
    cv.getContext("2d").drawImage(img, 0, 0);
    return cv.getContext("2d").getImageData(0, 0, img.width, img.height).data;
  };
  const still = await frame("c3d0", []);
  const old = await frame("c3d1", [{ t: 0, zoom: 1 }]);
  assert.ok(same(still, old), "наезд zoom 1 без 3D-полей — кадр как без камеры");
  const orbit = await frame("c3d2", [{ t: 0, rotY: 10, rotX: -4, z: 150 }]);
  assert.ok(!same(still, orbit));
  const magenta = (d) => {
    let sx = 0, n = 0;
    for (let i = 0; i < d.length; i += 4) if (d[i] > 230 && d[i + 1] < 40 && d[i + 2] > 230) { sx += (i / 4) % 270; n++; }
    return n ? sx / n : -1;
  };
  assert.ok(Math.abs(magenta(orbit) - magenta(still)) > 3, "ближний слой уехал при облёте");
});