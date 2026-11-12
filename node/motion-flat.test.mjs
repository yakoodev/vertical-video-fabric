// Плоская тема (frontend/src/motion/flat): токены и палитры тем, значки Lucide, компоненты слоя ui, фон под
// плашками (fx backdrop), шаблоны «Плоский UI» и тема у старых шаблонов. `node --test node/motion-flat.test.mjs`
// (в `npm run test:motion`). Движок собирается esbuild во временную папку, как в motion-ads.test.mjs.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";
import { createCanvas, GlobalFonts } from "@napi-rs/canvas";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const CLI = join(ROOT, "node", "motion-render.mjs");
const FONTS = join(ROOT, "vendor", "fonts");
let tmp;
let E;

before(async () => {
  tmp = mkdtempSync(join(tmpdir(), "vvf-flat-"));
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

const env = (w = 1080, h = 1920) => ({ width: w, height: h, getImage: () => null, createCanvas: (a, b) => createCanvas(a, b) });

function ink(cv, x = 0, y = 0, w = cv.width, h = cv.height) {
  const d = cv.getContext("2d").getImageData(x, y, w, h).data;
  let n = 0;
  for (let i = 3; i < d.length; i += 4) if (d[i] > 10) n++;
  return n;
}

function frame(layers, t, w = 1080, h = 1920, under = null) {
  const cv = createCanvas(w, h);
  const g = cv.getContext("2d");
  if (under) under(g);
  E.drawFrame(g, { layers }, t, env(w, h));
  return cv;
}

const px = (cv, x, y) => Array.from(cv.getContext("2d").getImageData(x, y, 1, 1).data);
const ui = (props, extra = {}) => ({ id: `u${Math.random().toString(36).slice(2, 7)}`, type: "ui", start: 0, end: 4, x: 0.5, y: 0.5, props, ...extra });

/* ---------- токены ---------- */

test("темы: red/dark/light/brand, текст читается на карточке, акцент перекрашивает", () => {
  assert.deepEqual([...E.FLAT_THEMES], ["red", "dark", "light", "brand"]);
  const hex = (c) => c.slice(0, 7);
  const lum = (c) => {
    const v = [1, 3, 5].map((i) => parseInt(c.slice(i, i + 2), 16) / 255).map((s) => (s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4));
    return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2];
  };
  const contrast = (a, b) => (Math.max(lum(a), lum(b)) + 0.05) / (Math.min(lum(a), lum(b)) + 0.05);
  for (const name of ["red", "dark", "light"]) {
    const p = E.flatTheme(name);
    assert.equal(p.name, name);
    assert.ok(contrast(hex(p.text), hex(p.surface)) >= 7, `${name}: основной текст на карточке`);
    assert.ok(contrast(hex(p.accentText), hex(p.accent)) >= 3, `${name}: текст на акценте`);
  }
  assert.equal(E.flatTheme("нет такой").name, "dark");
  assert.equal(E.flatTheme("red", { accent: "#22c55e" }).accent, "#22c55e");
  // Бренд: тёмная основа → тёмная тема, светлая → светлая; текст на жёлтом акценте — тёмный.
  const dark = E.flatTheme("brand", { accent: "#ffb703", plate: "#1d3557" });
  assert.equal(dark.mode, "dark");
  assert.equal(dark.accent, "#ffb703");
  assert.equal(dark.accentText, "#0f1115");
  assert.equal(E.flatTheme("brand", { accent: "#e63946", plate: "#fdf6ec" }).mode, "light");
});

/* ---------- значки ---------- */

test("значки Lucide: все пути разбираются, алиасы по-русски, рисуются обводкой", () => {
  assert.equal(E.LUCIDE_LICENSE, "ISC");
  assert.ok(E.FLAT_ICON_NAMES.length >= 100);
  for (const name of E.FLAT_ICON_NAMES) {
    for (const d of E.FLAT_ICONS[name]) {
      const cmds = E.parseSvgPath(d);
      assert.ok(cmds.length > 0, name);
      for (const c of cmds) for (const v of c.slice(1)) if (typeof v === "number") assert.ok(Number.isFinite(v), `${name}: ${d}`);
    }
  }
  // Дуга с флагами и относительный путь после неё: кружок-галочка.
  const arc = E.parseSvgPath("M21.801 10A10 10 0 1 1 17 3.335");
  assert.equal(arc[1][0], "E");
  assert.ok(Math.abs(arc[1][1] - 12) < 0.01 && Math.abs(arc[1][2] - 12) < 0.01, "центр дуги");
  assert.equal(E.flatIconName("глаз"), "eye");
  assert.equal(E.flatIconName("галочка"), "check");
  assert.equal(E.flatIconName("нет-такого"), null);
  const cv = createCanvas(60, 60);
  assert.equal(E.drawFlatIcon(cv.getContext("2d"), "eye", 30, 30, 48, "#ffffff"), true);
  assert.ok(ink(cv) > 150);
  assert.equal(E.drawFlatIcon(cv.getContext("2d"), "nope", 30, 30, 48, "#ffffff"), false);
});

/* ---------- компоненты ---------- */

const ALL = [
  { component: "hook", title: "НАБИРАЮТ" },
  { component: "chip", title: "Ошибка 1" },
  { component: "title", label: "Ошибка 2", title: "Ты сдаёшься", text: "после пяти видео" },
  { component: "stat", label: "Просмотры", icon: "eye", value: 942692, delta: "+340%", progress: 0.4, text: "за неделю" },
  { component: "bars", label: "Монетизация", marker: 0.8, markerLabel: "1:00", items: [{ text: "0:50", value: 50, max: 70, dim: true }, { text: "1:04", value: 64, max: 70, note: "$" }] },
  { component: "counter", value: 942692, unit: "просмотров", icon: "eye", variant: "accent" },
  { component: "checklist", variant: "window", label: "Claude", title: "Исправляю", items: [{ text: "Начало", sub: "крючок" }, { text: "Каждый день" }] },
  { component: "toast", title: "ChatGPT", text: "Отличная идея", badge: 1, icon: "message-circle" },
  { component: "window", variant: "browser", url: "kotiki-shop.рф", label: "Сайт", title: "Онлайн", text: "128 заказов" },
  { component: "window", variant: "chat", label: "Клод", items: [{ text: "Сделай сайт", from: "me" }, { text: "Готово", from: "them" }] },
];

test("каждый компонент меряется, рисуется во всех темах; оценка рамки (uiSize) — того же порядка", () => {
  const g = createCanvas(10, 10).getContext("2d");
  for (const props of ALL) {
    const box = E.measureFlat(g, props);
    assert.ok(box.w > 40 && box.h > 30, `${props.component}: ${box.w}×${box.h}`);
    const [ew, eh] = E.uiSize(props);
    assert.ok(ew / box.w > 0.55 && ew / box.w < 1.8, `${props.component}: ширина оценки ${ew} против ${box.w}`);
    assert.ok(eh / box.h > 0.5 && eh / box.h < 2, `${props.component}: высота оценки ${eh} против ${box.h}`);
    for (const theme of ["red", "dark", "light", "brand"]) {
      const cv = frame([ui({ ...props, theme })], 3);
      assert.ok(ink(cv) > 2000, `${props.component}/${theme} пустой`);
    }
  }
});

test("число растёт по ходу, чек-лист отмечает пункты по одному, done:false не отмечается", () => {
  // Счётчик: в начале и в конце — разный кадр, конец стоит (число дошло).
  const c = ui({ component: "counter", from: 0, value: 999999, theme: "dark" });
  const a = frame([c], 0.3).toBuffer("image/png");
  const b = frame([c], 3.5).toBuffer("image/png");
  assert.notDeepEqual(a, b);
  assert.deepEqual(frame([c], 3.5).toBuffer("image/png"), frame([c], 3.9).toBuffer("image/png"), "после хода — стоп");
  // Чек-лист: кружок первого пункта красный после delay, третий (done:false) — нет до конца слоя.
  const props = { component: "checklist", theme: "red", delay: 0.5, stagger: 0.5, items: [{ text: "Один" }, { text: "Два" }, { text: "Три", done: false }] };
  const layer = ui(props, { end: 6 });
  const g = createCanvas(10, 10).getContext("2d");
  const box = E.measureFlat(g, props);
  // Центр кружка пункта i: левый край + поле 44 + радиус 22; верх строк — поле 44 (без заголовка).
  const cx = Math.round(540 - box.w / 2 + 44 + 22);
  const rowY = (i) => Math.round(960 - box.h / 2 + 44 + i * (34 * 1.2 + 30) + 34 * 0.6);
  const red = (cv, i) => {
    const [r, gg, bb] = px(cv, cx, rowY(i) - 13); // над галочкой: белая черта проходит через центр
    return r > 200 && gg < 90 && bb < 110;
  };
  const early = frame([layer], 0.3);
  assert.equal(red(early, 0), false, "до delay пункт не отмечен");
  const mid = frame([layer], 1.2);
  assert.equal(red(mid, 0), true, "первый отмечен");
  assert.equal(red(mid, 1), true, "второй отмечен к 1.2 с (0.5 + 0.5 + ход 0.4 до половины)");
  const late = frame([layer], 5);
  assert.equal(red(late, 2), false, "done:false так и не отмечен");
});

test("хук с заливкой закрывает кадр с первого кадра, без заливки — только слово", () => {
  const fill = frame([ui({ component: "hook", title: "ХУК", variant: "fill", theme: "red" }, { end: 1.2 })], 0);
  assert.equal(ink(fill), 1080 * 1920, "заставка во весь кадр на кадре 0");
  const plain = frame([ui({ component: "hook", title: "ХУК", theme: "red" }, { end: 1.2 })], 0);
  assert.ok(ink(plain) > 20000 && ink(plain) < 1080 * 1920 * 0.4);
});

/* ---------- фон под плашками ---------- */

test("fx backdrop: всё ниже размыто и притушено, субтитры остаются резкими поверх", () => {
  // Шахматка под фоном: после размытия соседние клетки сближаются по яркости, кадр темнее.
  const checker = (g) => {
    for (let y = 0; y < 1920; y += 40) for (let x = 0; x < 1080; x += 40) {
      g.fillStyle = (x + y) % 80 ? "#ffffff" : "#000000";
      g.fillRect(x, y, 40, 40);
    }
  };
  const fx = { id: "bd", type: "fx", start: 0, end: 4, track: 4, props: { kind: "backdrop", size: 24, intensity: 0.5 } };
  const sharp = frame([], 1, 1080, 1920, checker);
  const blurred = frame([fx], 1, 1080, 1920, checker);
  const spread = (cv) => {
    const a = px(cv, 520, 940)[0];
    const b = px(cv, 560, 940)[0];
    return Math.abs(a - b);
  };
  assert.ok(spread(sharp) > 200);
  assert.ok(spread(blurred) < 60, `размыто: ${spread(blurred)}`);
  const mean = (cv) => {
    const d = cv.getContext("2d").getImageData(300, 700, 400, 400).data;
    let s = 0;
    for (let i = 0; i < d.length; i += 4) s += d[i];
    return s / (d.length / 4);
  };
  assert.ok(mean(blurred) < mean(sharp) * 0.75, "притушено");
  // Порядок: субтитры ниже фона по дорожкам поднимаются над ним (как у ч/б).
  const cap = { id: "cap", type: "captions", start: 0, end: 4, track: 0, props: {} };
  const order = E.liftOverGrayscale([cap, fx]).map((l) => l.id);
  assert.deepEqual(order, ["bd", "cap"]);
});

/* ---------- шаблоны ---------- */

test("шаблоны «Плоский UI» собираются; старые шаблоны: classic — прежний вид, тема — слой ui", () => {
  const flat = E.TEMPLATES.filter((t) => t.category === "Плоский UI");
  assert.ok(flat.length >= 10);
  for (const t of flat) {
    const r = E.buildTemplate(t.id, {}, 1);
    assert.ok(r.layers.length >= 1, t.id);
    for (const l of r.layers) assert.ok(l.type === "ui" || l.type === "fx", `${t.id}: ${l.type}`);
    if (t.params.some((p) => p.name === "theme")) {
      const red = E.buildTemplate(t.id, { theme: "red" }, 1);
      for (const l of red.layers) if (l.type === "ui") assert.equal(l.props.theme, "red");
    }
  }
  const hook = E.buildTemplate("flat-hook", { word: "НАБИРАЮТ" }, 0);
  assert.equal(hook.layers[0].in, null, "хук без входа — читается с кадра 0");
  const stat = E.buildTemplate("flat-stat", { backdrop: true }, 2);
  assert.deepEqual(stat.layers.map((l) => l.type), ["fx", "ui"]);
  assert.ok(stat.layers[0].track < stat.layers[1].track, "фон ниже карточки по дорожкам");
  const cmp = E.buildTemplate("flat-compare", { rows: "a | 50 | $0; b | 64 | ok", max: 70, marker: 60 }, 0);
  const bars = cmp.layers.find((l) => l.type === "ui").props;
  assert.equal(bars.items.length, 2);
  assert.equal(bars.items[0].dim, true);
  assert.ok(Math.abs(bars.marker - 60 / 70) < 1e-9);
  const brand = E.buildTemplate("flat-chip", { theme: "brand", accent: "#ffb703", plate: "#1d3557" }, 0).layers[0].props;
  assert.deepEqual([brand.theme, brand.accent, brand.plate], ["brand", "#ffb703", "#1d3557"]);
  // Обратная совместимость: без theme — прежние слои.
  assert.deepEqual(E.buildTemplate("counter", {}, 0).layers.map((l) => l.type), ["counter", "text"]);
  assert.deepEqual(E.buildTemplate("counter", { theme: "red" }, 0).layers.map((l) => l.type), ["ui"]);
  assert.deepEqual(E.buildTemplate("progress", { theme: "light" }, 0).layers.map((l) => l.type), ["ui"]);
  assert.equal(E.buildTemplate("lower-third-bar", {}, 0).layers[0].type, "callout");
  assert.equal(E.buildTemplate("lower-third-bar", { theme: "dark" }, 0).layers[0].type, "ui");
  assert.equal(E.buildTemplate("top-3", { theme: "dark" }, 0).layers[0].props.component, "checklist");
});

test("компоновщик: шаблон плоской темы через --build-template и кадр PNG", () => {
  const job = join(tmp, "job.json");
  writeFileSync(job, JSON.stringify({ id: "flat-checklist", params: { theme: "red" }, at: 0 }));
  const r = spawnSync(process.execPath, [CLI, "--build-template", job], { encoding: "utf8" });
  assert.equal(r.status, 0, r.stderr);
  const built = JSON.parse(r.stdout);
  assert.ok(built.layers.some((l) => l.type === "ui" && l.props.component === "checklist"));
  const comp = join(tmp, "comp.json");
  writeFileSync(comp, JSON.stringify({ width: 540, height: 960, fps: 30, duration: 5, layers: built.layers }));
  const png = join(tmp, "f.png");
  const out = spawnSync(process.execPath, [CLI, "--comp", comp, "--fonts-dir", FONTS, "--frame", "3", "--png", png], { encoding: "utf8" });
  assert.equal(out.status, 0, out.stderr);
  assert.ok(existsSync(png));
});
