// Слой «страница сайта» (layers/page.ts): `node --test node/motion-page.test.mjs` (в `npm run test:motion`).
// Движок из frontend/src/motion собирается esbuild во временную папку, как в motion.test.mjs; компоновщик —
// node/motion-render.mjs (кадр рендера = кадр превью).
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
  tmp = mkdtempSync(join(tmpdir(), "vvf-page-"));
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

/** «Страница» 390 CSS px × 3000, DPR 3: верх зелёный, низ (от 2000 px) красный. */
function fakePage() {
  const cv = createCanvas(390 * 3, 3000 * 3);
  const g = cv.getContext("2d");
  g.fillStyle = "#00c040";
  g.fillRect(0, 0, cv.width, 2000 * 3);
  g.fillStyle = "#ff1020";
  g.fillRect(0, 2000 * 3, cv.width, 1000 * 3);
  return cv;
}

const pageLayer = (props = {}, over = {}) => ({
  id: "p1", type: "page", start: 0, end: 5, x: 0.5, y: 0.5, ...over,
  props: { page_id: 7, version: 2, pageWidth: 390, region: { x: 0, y: 0, w: 390, h: 700 }, w: 600, ...props },
});

function colors(cv) {
  const d = cv.getContext("2d").getImageData(0, 0, cv.width, cv.height).data;
  let green = 0;
  let red = 0;
  for (let i = 0; i < d.length; i += 4) {
    if (d[i + 3] < 200) continue;
    if (d[i] < 40 && d[i + 1] > 170 && d[i + 2] < 90) green++;
    if (d[i] > 220 && d[i + 1] < 50 && d[i + 2] < 60) red++;
  }
  return { green, red };
}

test("страница: ссылка на кадр версии, окно, прокрутка и приближение по ключам", () => {
  assert.equal(E.pageRef({ page_id: 7, version: 2 }), "webpage:7:2");
  assert.equal(E.pageRef({ page_id: 7 }), "webpage:7:1");
  assert.equal(E.pageRef({ page_id: 0 }), "");
  assert.ok(E.collectImageRefs([pageLayer()]).includes("webpage:7:2"));
  // Без region — первый экран: в телефоне 9:19.5, без телефона 9:16.
  assert.deepEqual(E.pageRegion({ page_id: 1, pageWidth: 390, device: "phone" }), { x: 0, y: 0, w: 390, h: 845 });
  const p = pageLayer({ scroll: [{ t: 1, y: 0 }, { t: 3, y: 1000, ease: "linear" }], zoom: [{ t: 3, z: 1 }, { t: 4, z: 2, cx: 0.5, cy: 0 }] }).props;
  near(E.pageWindow(p, 0).y, 0, 1e-9);
  near(E.pageWindow(p, 2).y, 500, 1e-6); // linear посередине
  near(E.pageWindow(p, 3).y, 1000, 1e-6);
  const z = E.pageWindow(p, 4);
  near(z.w, 195, 1e-6); // вдвое ближе — окно вдвое уже, пропорция та же
  near(z.h, 350, 1e-6);
  near(z.x, 97.5, 1e-6);
  near(z.y, 1000, 1e-6); // cy 0 — верх окна на месте
  // Окно не уходит за низ страницы.
  near(E.pageWindow({ ...p, scroll: [{ t: 0, y: 99999 }], zoom: [] }, 1, 3000).y, 2300, 1e-6);
});

test("страница: рисуется окном снимка, листается к красному низу, касание — волна", () => {
  const img = fakePage();
  const e = env(1080, 1920, { "webpage:7:2": img });
  const layer = pageLayer({ scroll: [{ t: 0.5, y: 0 }, { t: 4, y: 2300 }] });
  const ctx = createCanvas(10, 10).getContext("2d");
  const size = E.pageRenderer.measure(ctx, layer, { local: 0 }, e, {});
  near(size.w, 600, 1e-6);
  near(size.h, (600 * 700) / 390, 1e-6);
  const top = createCanvas(1080, 1920);
  E.drawFrame(top.getContext("2d"), { layers: [layer] }, 0.2, e);
  const a = colors(top);
  assert.ok(a.green > 300000 && a.red === 0, JSON.stringify(a));
  const bottom = createCanvas(1080, 1920);
  E.drawFrame(bottom.getContext("2d"), { layers: [layer] }, 4.5, e);
  const b = colors(bottom);
  assert.ok(b.red > 300000 && b.green < 1000, JSON.stringify(b));
  // Размер слоя от прокрутки не меняется.
  near(E.pageRenderer.measure(ctx, layer, { local: 4.5 }, e, {}).h, size.h, 1e-6);
  // Касание: белая волна в точке страницы.
  const tapped = pageLayer({ taps: [{ t: 1, x: 195, y: 350 }] });
  const cv = createCanvas(1080, 1920);
  E.drawFrame(cv.getContext("2d"), { layers: [tapped] }, 1.2, e);
  const px = cv.getContext("2d").getImageData(540 - 60, 960 - 60, 120, 120).data;
  let white = 0;
  for (let i = 0; i < px.length; i += 4) if (px[i] > 120 && px[i + 2] > 120) white++; // белое поверх зелёного
  assert.ok(white > 200, `волна касания ${white}`);
  // Нет картинки: без телефона — пусто, в телефоне — корпус с тёмным экраном.
  assert.equal(colors(createCanvas(1, 1)).green, 0);
  const empty = createCanvas(1080, 1920);
  E.drawFrame(empty.getContext("2d"), { layers: [pageLayer({ device: "phone" })] }, 1, env());
  const d = empty.getContext("2d").getImageData(0, 0, 1080, 1920).data;
  let ink = 0;
  for (let i = 3; i < d.length; i += 4) if (d[i] > 0) ink++;
  assert.ok(ink > 100000, "корпус телефона нарисован");
});

test("страница: кадр компоновщика (рендер) = кадр движка (превью); шаблоны прокрутки и телефона", async () => {
  const png = join(tmp, "page.png");
  writeFileSync(png, fakePage().toBuffer("image/png"));
  const layers = [
    pageLayer({ scroll: [{ t: 0, y: 1500 }, { t: 4, y: 2300 }], device: "phone", taps: [{ t: 1.8, x: 195, y: 2100 }] }, { y: 0.52 }),
  ];
  writeFileSync(join(tmp, "c.json"), JSON.stringify({ width: 540, height: 960, fps: 30, duration: 5, layers }));
  writeFileSync(join(tmp, "c.assets.json"), JSON.stringify({ "webpage:7:2": png }));
  const out = join(tmp, "c.png");
  const r = spawnSync(process.execPath, [CLI, "--comp", join(tmp, "c.json"), "--assets", join(tmp, "c.assets.json"), "--frame", "2", "--png", out, "--fonts-dir", FONTS], { encoding: "utf8", timeout: 120000 });
  assert.equal(r.status, 0, r.stderr);
  const a = createCanvas(540, 960);
  a.getContext("2d").drawImage(await loadImage(readFileSync(out)), 0, 0);
  const b = createCanvas(540, 960);
  E.drawFrame(b.getContext("2d"), { layers }, 2, env(540, 960, { "webpage:7:2": await loadImage(readFileSync(png)) }));
  const da = a.getContext("2d").getImageData(0, 0, 540, 960).data;
  const db = b.getContext("2d").getImageData(0, 0, 540, 960).data;
  let diff = 0;
  let inked = 0;
  for (let i = 0; i < da.length; i += 4) {
    if (da[i + 3] > 0) inked++;
    if (Math.abs(da[i] - db[i]) > 3 || Math.abs(da[i + 1] - db[i + 1]) > 3 || Math.abs(da[i + 2] - db[i + 2]) > 3) diff++;
  }
  assert.ok(inked > 50000, `страница не нарисована (${inked})`);
  assert.ok(diff <= inked * 0.01, `расхождение рендера и превью: ${diff} из ${inked}`);
  const red = colors(a).red;
  assert.ok(red > 5000, `на 2-й секунде видно красный низ: ${red}`);

  for (const id of ["page-scroll", "page-phone"]) {
    const t = E.getTemplate(id);
    assert.ok(t && E.TEMPLATE_CATEGORIES.includes(t.category), id);
  }
  const [scroll] = E.buildTemplate("page-scroll", { page: "12:3", from: 100, to: 900, duration: 5 }, 0).layers;
  assert.equal(scroll.type, "page");
  assert.deepEqual([scroll.props.page_id, scroll.props.version, scroll.props.scroll.at(-1).y], [12, 3, 900]);
  const [phone] = E.buildTemplate("page-phone", { page: "5", screen: "компьютер" }, 0).layers;
  assert.equal(phone.props.device, "phone");
  assert.equal(phone.props.pageWidth, 1280);
  assert.equal(phone.props.taps.length, 1);
  assert.deepEqual(E.parsePageParam("x"), { page_id: 0, version: 1 });
});
