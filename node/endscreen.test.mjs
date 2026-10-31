// Концовка-баннер и SVG → webm: `node --test node/endscreen.test.mjs` (в `npm run test:motion`).
// Движок (frontend/src/motion/endscreen.ts) собирается esbuild во временную папку, как в motion.test.mjs;
// компоновщик — node/motion-render.mjs; конвертер — node/svg-to-webm.mjs (браузер нужен только
// последнему тесту — нет Chromium, тест пропускается).
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";
import { createCanvas, loadImage } from "@napi-rs/canvas";
import * as S from "./svg-to-webm.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const CLI = join(ROOT, "node", "motion-render.mjs");
const FONTS = join(ROOT, "vendor", "fonts");
let tmp;
let E;

before(async () => {
  tmp = mkdtempSync(join(tmpdir(), "vvf-endscreen-"));
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

/** Средняя разница соседних пикселей по горизонтали (резкость) в прямоугольнике. */
function sharpness(data, W, x0, y0, x1, y1) {
  let sum = 0;
  let n = 0;
  for (let y = y0; y < y1; y++)
    for (let x = x0; x < x1 - 1; x++) {
      const i = (y * W + x) * 4;
      sum += Math.abs(data[i] - data[i + 4]) + Math.abs(data[i + 1] - data[i + 5]);
      n++;
    }
  return sum / n;
}

test("endScreenAt: до начала — ничего, за ramp — inOutCubic, дальше — полная сила", () => {
  const fx = E.readEndScreen({ start: 2, ramp: 0.6, blur: 20, dim: 0.4 });
  assert.equal(E.endScreenAt(fx, 1.99), null);
  near(E.endScreenAt(fx, 2.3).blur, 10, 1e-6); // середина inOutCubic — ровно половина
  near(E.endScreenAt(fx, 2.3).dim, 0.2, 1e-6);
  assert.deepEqual(E.endScreenAt(fx, 9), { blur: 20, dim: 0.4, k: 1 });
});

test("readEndScreen: мусор — null, числа зажаты, без размытия и тени — null", () => {
  assert.equal(E.readEndScreen(null), null);
  assert.equal(E.readEndScreen({ blur: 5 }), null); // нет start
  assert.equal(E.readEndScreen({ start: 1, blur: 0, dim: 0 }), null);
  const fx = E.readEndScreen({ start: -3, ramp: 99, blur: 500, dim: 2 });
  assert.deepEqual(fx, { start: 0, ramp: 5, blur: E.END_SCREEN_MAX_BLUR, dim: E.END_SCREEN_MAX_DIM });
});

test("endScreenFor: начало — слой end_banner, без него — хвост ролика; баннер удалён — концовки нет", () => {
  const es = { duration: 3, blur: 18, dim: 0.35, ramp: 0.6, asset_id: 7 };
  assert.equal(E.endScreenFor(es, [{ id: "end_banner", start: 4.25 }], 10).start, 4.25);
  assert.equal(E.endScreenFor(es, [], 10), null);
  assert.equal(E.endScreenFor({ ...es, asset_id: null }, [], 10).start, 7);
  assert.equal(E.endScreenFor(null, [], 10), null);
});

test("paintEndScreen: ровный кадр остаётся ровным до края (нет каймы), тень — ровно dim", () => {
  const W = 200;
  const H = 320;
  const c = createCanvas(W, H);
  const ctx = c.getContext("2d");
  ctx.fillStyle = "rgb(200,120,60)";
  ctx.fillRect(0, 0, W, H);
  const make = (w, h) => createCanvas(w, h);
  assert.ok(E.paintEndScreen(ctx, c, W, H, { start: 0, ramp: 0, blur: 40, dim: 0 }, 1, make));
  const d = ctx.getImageData(0, 0, W, H).data;
  for (const [x, y] of [[0, 0], [W - 1, 0], [0, H - 1], [W - 1, H - 1], [W / 2, H / 2]]) {
    const i = (y * W + x) * 4;
    near(d[i], 200, 3);
    near(d[i + 1], 120, 3);
    near(d[i + 3], 255, 0);
  }
  E.paintEndScreen(ctx, c, W, H, { start: 0, ramp: 0, blur: 0, dim: 0.5 }, 1, make);
  near(ctx.getImageData(W / 2, H / 2, 1, 1).data[0], 100, 2);
  // До начала — ничего не рисует.
  assert.equal(E.paintEndScreen(ctx, c, W, H, { start: 5, ramp: 0.5, blur: 20, dim: 0.3 }, 1, make), false);
});

test("компоновщик: comp.endScreen размывает основу под слоями, слой поверх остаётся резким", () => {
  const W = 216;
  const H = 384;
  const base = join(tmp, "base.mp4");
  const ff = spawnSync("ffmpeg", ["-v", "error", "-y", "-f", "lavfi", "-i", `testsrc2=s=${W}x${H}:r=30:d=2`, "-pix_fmt", "yuv420p", base], { encoding: "utf8" });
  assert.equal(ff.status, 0, ff.stderr);
  const comp = {
    width: W, height: H, fps: 30, duration: 2,
    endScreen: { start: 1, ramp: 0.2, blur: 24, dim: 0.3 },
    layers: [{ id: "end_banner", type: "shape", start: 1, end: 2, x: 0.5, y: 0.5, track: 7, scale: 1, rotation: 0, opacity: 1,
      props: { kind: "rect", w: 400, h: 400, fill: null, stroke: { color: "#ffffff", width: 12 } } }],
  };
  writeFileSync(join(tmp, "comp.json"), JSON.stringify(comp));
  writeFileSync(join(tmp, "assets.json"), "{}");
  const frame = (t, png) => {
    const r = spawnSync(process.execPath, [CLI, "--comp", join(tmp, "comp.json"), "--assets", join(tmp, "assets.json"), "--input", base,
      "--fonts-dir", FONTS, "--frame", String(t), "--png", png], { encoding: "utf8" });
    assert.equal(r.status, 0, r.stderr);
  };
  frame(0.5, join(tmp, "a.png"));
  frame(1.5, join(tmp, "b.png"));
  return Promise.all([loadImage(join(tmp, "a.png")), loadImage(join(tmp, "b.png"))]).then(([a, b]) => {
    const read = (img) => {
      const c = createCanvas(W, H);
      const x = c.getContext("2d");
      x.drawImage(img, 0, 0);
      return x.getImageData(0, 0, W, H).data;
    };
    const da = read(a);
    const db = read(b);
    // Угол кадра (вне рамки слоя): после начала концовки — заметно мягче и темнее.
    const sa = sharpness(da, W, 0, 0, 40, 60);
    const sb = sharpness(db, W, 0, 0, 40, 60);
    assert.ok(sb < sa * 0.5, `основа не размылась: ${sa} → ${sb}`);
    // Белая обводка слоя — резкая и белая (слой рисуется после фильтра).
    // Левая сторона рамки: x = W/2 − 200·(W/1080); берём самый светлый пиксель рядом с ней.
    const edge = Math.round(W / 2 - (200 * W) / 1080);
    const y = Math.round(H / 2);
    const best = Math.max(...[-2, -1, 0, 1, 2].map((dx) => Math.min(db[(y * W + edge + dx) * 4], db[(y * W + edge + dx) * 4 + 1])));
    assert.ok(best > 235, `обводка слоя не белая: ${best}`);
  });
});

/* ---------- svg-to-webm.mjs ---------- */

test("svg-to-webm: размер из width/height или viewBox, авто-длительность — доминирующая", () => {
  assert.deepEqual(S.svgMetrics('<svg width="800" height="240" viewBox="0 0 400 120">').width, 800);
  assert.equal(S.svgMetrics('<svg viewBox="0 0 400 120">').height, 120);
  assert.equal(S.svgMetrics("<svg>").width, 800);
  const svg = `<svg><style>.a{animation:x 12s infinite}.b{animation:y .3s}.c{animation-duration:12s}</style>
    <rect><animate attributeName="x" dur="0.5s" repeatCount="indefinite"/></rect></svg>`;
  assert.equal(S.detectDuration(svg), 12);
  assert.equal(S.detectDuration("<svg/>"), 5);
  assert.equal(S.detectDuration('<svg><animate begin="1s" dur="2s" repeatCount="2"/></svg>'), 5);
});

test("svg-to-webm: скрипты и обработчики вырезаны, шрифты — только упомянутые", () => {
  const clean = S.sanitizeSvg('<?xml version="1.0"?><svg onload="x()"><script>alert(1)</script><a href="javascript:x()"><text>ok</text></a></svg>');
  assert.ok(!/script|onload|javascript/i.test(clean), clean);
  assert.ok(clean.includes("<text>ok</text>"));
  const faces = S.fontFaces('<text font-family="Unbounded">x</text>', [FONTS]);
  assert.ok(faces.includes('font-family:"Unbounded"') && !faces.includes("Montserrat"), faces);
});

test("svg-to-webm: аргументы — числа зажаты, нужны --in и --out", () => {
  assert.throws(() => S.parseArgs(["--in", "a.svg"]));
  const o = S.parseArgs(["--in", "a.svg", "--out", "a.webm", "--fps=500", "--duration", "auto", "--strip-bg", "--fonts-dir", "x"]);
  assert.equal(o.fps, 60);
  assert.equal(o.duration, null);
  assert.equal(o.stripBg, true);
  assert.deepEqual(o.fontsDirs, ["x"]);
});

test("svg-to-webm: кадров ровно duration·fps, альфа есть, анимация перемотана по времени", async (t) => {
  let chrome;
  try {
    chrome = S.findChrome(process.env.SVG_CHROMIUM || process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || "");
  } catch {
    t.skip("нет Chromium");
    return;
  }
  const svgPath = join(tmp, "t.svg");
  writeFileSync(svgPath, `<svg xmlns="http://www.w3.org/2000/svg" width="64" height="32">
    <style>@keyframes mv{from{transform:translateX(0)}to{transform:translateX(48px)}}.b{animation:mv 1s linear both}</style>
    <rect width="64" height="32" fill="#123456"/><rect class="b" x="0" y="8" width="16" height="16" fill="#ff00ff"/></svg>`);
  const out = join(tmp, "t.webm");
  const res = await S.convert({ ...S.parseArgs(["--in", svgPath, "--out", out, "--fps", "10", "--duration", "1", "--strip-bg"]), chrome });
  assert.equal(res.frames, 10);
  const probe = spawnSync("ffprobe", ["-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
    "stream=nb_read_frames:stream_tags=alpha_mode", "-of", "json", out], { encoding: "utf8" });
  const info = JSON.parse(probe.stdout).streams[0];
  assert.equal(Number(info.nb_read_frames), 10);
  assert.equal(String(info.tags.ALPHA_MODE ?? info.tags.alpha_mode), "1");
  // Кадр 5 (t = 0.5 с): квадрат в x 24..40; фон снят — угол прозрачный.
  const png = join(tmp, "f5.png");
  const ff = spawnSync("ffmpeg", ["-v", "error", "-y", "-c:v", "libvpx-vp9", "-i", out, "-vf", "select=eq(n\\,5)", "-frames:v", "1", "-pix_fmt", "rgba", png], { encoding: "utf8" });
  assert.equal(ff.status, 0, ff.stderr);
  const img = await loadImage(png);
  const c = createCanvas(64, 32);
  const x = c.getContext("2d");
  x.drawImage(img, 0, 0);
  const at = (px, py) => x.getImageData(px, py, 1, 1).data;
  assert.ok(at(2, 2)[3] < 20, "угол не прозрачный");
  assert.ok(at(32, 16)[3] > 230 && at(32, 16)[0] > 200 && at(32, 16)[1] < 60, `квадрат не на месте: ${at(32, 16)}`);
  assert.ok(at(8, 16)[3] < 20, "квадрат не уехал от начала");
});
