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
