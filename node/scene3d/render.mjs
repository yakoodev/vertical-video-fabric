#!/usr/bin/env node
/**
 * Рендер 3D-сцены (node/scene3d/src, бандл dist/scene3d.js) в mp4: headless Chromium рисует
 * каждый кадр WebGL как функцию времени t = i/fps (реального времени нет), кадр уходит в ffmpeg
 * (image2pipe). Кадры делятся на куски, куски считаются параллельно отдельными браузерами в свои
 * сегменты (одинаковые параметры x264), сегменты склеиваются без перекодирования (concat -c copy).
 *
 * Браузер — системный (как node/svg-to-webm.mjs: своя обвязка CDP через --remote-debugging-pipe,
 * без зависимостей). WebGL — SwiftShader (программный, одинаковый на сервере и локально) или GPU
 * (--gl gpu, быстрее на машине с видеокартой). Сеть браузеру закрыта: страница, бандл и модели
 * паков отдаются через CDP Fetch из разрешённых каталогов (только чтение, без выхода за каталог).
 *
 * node node/scene3d/render.mjs --spec scene.json --out out.mp4 [--packs vendor/packs] [--workers 3]
 *   [--fps 30] [--duration 9] [--frames A:B] [--preview 0,2.5,5 --preview-dir DIR] [--inspect T]
 *   [--gl swiftshader|gpu] [--format jpeg|png] [--quality 92] [--crf 18] [--chrome PATH] [--ffmpeg ffmpeg]
 *   [--timeout 1800]
 *
 * stdout — одна строка JSON {ok, frames, fps, duration, width, height, segments, black_frames,
 * luma_min, seconds, warnings} (превью: {ok, previews: [...]}, inspect: {ok, inspect}).
 * Код выхода 0 — готово, 2 — ошибка аргументов, 3 — чёрные кадры, 1 — прочие.
 */
import { spawn } from "node:child_process";
import { cpus } from "node:os";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, extname, join, resolve, sep } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, "..", "..");
export const ORIGIN = "http://scene3d.local";

class UsageError extends Error {}

export function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i++) {
    const tok = argv[i];
    if (!tok.startsWith("--")) throw new UsageError(`лишний аргумент ${tok}`);
    let [key, value] = tok.slice(2).split(/=(.*)/s, 2);
    if (value === undefined) {
      value = argv[++i];
      if (value === undefined) throw new UsageError(`--${key}: нужно значение`);
    }
    out[key.replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = value;
  }
  if (!out.spec) throw new UsageError("нужен --spec scene.json");
  if (!out.out && !out.preview && out.inspect === undefined) throw new UsageError("нужен --out out.mp4 (или --preview / --inspect)");
  const num = (name, def, lo, hi) => {
    if (out[name] === undefined) return def;
    const n = Number(out[name]);
    if (!Number.isFinite(n)) throw new UsageError(`--${name}: не число`);
    return Math.max(lo, Math.min(hi, n));
  };
  let frames = null;
  if (out.frames) {
    const m = String(out.frames).match(/^(\d+):(\d+)$/);
    if (!m || Number(m[2]) <= Number(m[1])) throw new UsageError("--frames A:B (B > A)");
    frames = [Number(m[1]), Number(m[2])];
  }
  const previews = out.preview ? String(out.preview).split(",").map((s) => Number(s.trim())).filter((n) => Number.isFinite(n) && n >= 0) : null;
  if (out.preview && !previews.length) throw new UsageError("--preview: список секунд через запятую");
  const format = out.format || "jpeg";
  if (!["jpeg", "png"].includes(format)) throw new UsageError("--format jpeg|png");
  const gl = out.gl || process.env.SCENE3D_GL || "swiftshader";
  if (!["swiftshader", "gpu"].includes(gl)) throw new UsageError("--gl swiftshader|gpu");
  // Постобработка: full — всё; lite — без затенения щелей и глубины резкости (на CPU/SwiftShader вдвое быстрее);
  // off — без постобработки. По умолчанию: GPU — full, SwiftShader — lite.
  const post = out.post || process.env.SCENE3D_POST || (gl === "gpu" ? "full" : "lite");
  if (!["full", "lite", "off"].includes(post)) throw new UsageError("--post full|lite|off");
  return {
    spec: resolve(out.spec),
    output: out.out ? resolve(out.out) : null,
    packs: resolve(out.packs || process.env.MOTION_PACKS_DIR || join(ROOT, "vendor", "packs")),
    bundle: resolve(out.bundle || join(HERE, "dist", "scene3d.js")),
    workers: Math.round(num("workers", Math.max(1, Math.min(4, Math.floor(cpus().length / 4))), 1, 16)),
    fps: out.fps !== undefined ? Math.round(num("fps", 30, 1, 60)) : null,
    duration: out.duration !== undefined ? num("duration", 5, 0.04, 600) : null,
    frames,
    previews,
    previewDir: out.previewDir ? resolve(out.previewDir) : null,
    inspect: out.inspect !== undefined ? num("inspect", 0, 0, 600) : null,
    gl,
    post,
    format,
    quality: Math.round(num("quality", 92, 50, 100)),
    crf: Math.round(num("crf", 18, 0, 51)),
    chrome: out.chrome || process.env.SCENE3D_CHROMIUM || process.env.SVG_CHROMIUM || process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || "",
    ffmpeg: out.ffmpeg || "ffmpeg",
    timeout: num("timeout", 1800, 10, 24 * 3600),
  };
}

/* ---------- раздача файлов странице (CDP Fetch) ---------- */

const MIME = {
  ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".json": "application/json",
  ".glb": "model/gltf-binary", ".gltf": "model/gltf+json", ".bin": "application/octet-stream",
  ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".ktx2": "image/ktx2",
};

/** Файл внутри каталога base или null (никаких «..», абсолютных путей и выхода по симлинку за каталог). */
export function safeJoin(base, rel) {
  let decoded;
  try {
    decoded = decodeURIComponent(rel);
  } catch {
    return null;
  }
  if (!decoded || decoded.includes("\0") || decoded.split(/[\\/]/).some((p) => p === ".." || p === "")) return null;
  const full = resolve(base, decoded);
  if (!full.startsWith(resolve(base) + sep)) return null;
  return full;
}

export function pageHtml(width, height) {
  return `<!doctype html><html><head><meta charset="utf-8"><style>
html,body{margin:0;padding:0;width:${width}px;height:${height}px;overflow:hidden;background:#000}
canvas{display:block;width:${width}px;height:${height}px}
</style></head><body><canvas id="c" width="${width}" height="${height}"></canvas>
<script src="${ORIGIN}/engine/scene3d.js"></script></body></html>`;
}

/** Ответ на запрос страницы: {status, body(Buffer), mime}. */
export function serve(url, ctx) {
  if (!url.startsWith(ORIGIN + "/")) return { status: 403 };
  const path = url.slice(ORIGIN.length).split(/[?#]/)[0];
  if (path === "/index.html") return { status: 200, body: Buffer.from(pageHtml(ctx.width, ctx.height)), mime: MIME[".html"] };
  if (path === "/engine/scene3d.js") return { status: 200, body: readFileSync(ctx.bundle), mime: MIME[".js"] };
  if (path.startsWith("/packs/") || path.startsWith("/assets/")) {
    // /assets/ — встроенные модели движка (node/scene3d/assets), /packs/ — скачанные паки.
    const assets = path.startsWith("/assets/");
    const file = safeJoin(assets ? join(HERE, "assets") : ctx.packs, path.slice(assets ? "/assets/".length : "/packs/".length));
    const mime = file && MIME[extname(file).toLowerCase()];
    if (!file || !mime || !existsSync(file) || !statSync(file).isFile()) return { status: 404 };
    return { status: 200, body: readFileSync(file), mime };
  }
  return { status: 404 };
}

/* ---------- Chromium по CDP (--remote-debugging-pipe) ---------- */

const CHROME_CANDIDATES = [
  "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable",
  "C:/Program Files/Google/Chrome/Application/chrome.exe", "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
];

export function findChrome(explicit) {
  if (explicit) {
    if (!existsSync(explicit)) throw new Error(`браузер не найден: ${explicit}`);
    return explicit;
  }
  const hit = CHROME_CANDIDATES.find((p) => existsSync(p));
  if (!hit) throw new Error("нет headless Chromium: задай SCENE3D_CHROMIUM (в образе сервиса — /usr/bin/chromium)");
  return hit;
}

class Cdp {
  constructor(proc) {
    this.proc = proc;
    this.seq = 0;
    this.pending = new Map();
    this.listeners = new Map();
    this.chunks = [];
    this.dead = null;
    proc.stdio[4].on("data", (chunk) => this.onData(chunk));
    proc.on("exit", (code) => this.fail(new Error(`браузер завершился (код ${code})`)));
    proc.on("error", (err) => this.fail(err));
  }
  fail(err) {
    if (this.dead) return;
    this.dead = err;
    for (const p of this.pending.values()) p.reject(err);
    this.pending.clear();
  }
  onData(chunk) {
    // Сообщения разделены \0; большие ответы (скриншоты) приходят кусками — собираем буфером.
    let start = 0;
    for (let i = 0; i < chunk.length; i++) {
      if (chunk[i] !== 0) continue;
      this.chunks.push(chunk.subarray(start, i));
      const raw = Buffer.concat(this.chunks).toString("utf8");
      this.chunks = [];
      start = i + 1;
      let msg;
      try {
        msg = JSON.parse(raw);
      } catch {
        continue;
      }
      if (msg.id && this.pending.has(msg.id)) {
        const p = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        if (msg.error) p.reject(new Error(`${p.method}: ${msg.error.message}`));
        else p.resolve(msg.result || {});
      } else if (msg.method) {
        for (const fn of this.listeners.get(msg.method) || []) fn(msg.params || {}, msg.sessionId);
      }
    }
    if (start < chunk.length) this.chunks.push(chunk.subarray(start));
  }
  on(method, fn) {
    if (!this.listeners.has(method)) this.listeners.set(method, []);
    this.listeners.get(method).push(fn);
  }
  once(method, sessionId) {
    return new Promise((resolve) => {
      const fn = (params, sid) => {
        if (sessionId && sid !== sessionId) return;
        this.listeners.set(method, (this.listeners.get(method) || []).filter((f) => f !== fn));
        resolve(params);
      };
      this.on(method, fn);
    });
  }
  send(method, params = {}, sessionId) {
    if (this.dead) return Promise.reject(this.dead);
    const id = ++this.seq;
    const msg = JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) });
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject, method });
      this.proc.stdio[3].write(msg + "\0");
    });
  }
}

export function chromeArgs(profile, gl) {
  const args = [
    "--headless=new", "--remote-debugging-pipe", `--user-data-dir=${profile}`, "--no-first-run",
    "--no-default-browser-check", "--disable-extensions", "--disable-background-networking", "--disable-sync",
    "--disable-component-update", "--disable-default-apps", "--disable-dev-shm-usage", "--no-sandbox",
    "--hide-scrollbars", "--mute-audio", "--force-color-profile=srgb", "--ignore-gpu-blocklist",
    "--disable-background-timer-throttling", "--disable-renderer-backgrounding", "--disable-backgrounding-occluded-windows",
    "--host-resolver-rules=MAP * ~NOTFOUND", "about:blank",
  ];
  if (gl === "gpu") args.splice(1, 0, "--enable-gpu", "--use-angle=default");
  else args.splice(1, 0, "--use-angle=swiftshader", "--enable-unsafe-swiftshader");
  return args;
}

/** Режим постобработки к спеке (копия): lite — без AO и DOF, off — без постобработки. */
export function applyPostMode(spec, mode) {
  const post = spec?.environment?.post;
  if (!post || !mode || mode === "full") return spec;
  const env = { ...spec.environment };
  if (mode === "off") delete env.post;
  else env.post = { ...post, ao: 0, dof: false };
  return { ...spec, environment: env };
}

/** Браузер со страницей сцены, загруженной и готовой: {render(t), shot(), inspect(t), close()}. */
export async function openScene(opts, spec, log = () => {}) {
  spec = applyPostMode(spec, opts.post);
  const width = spec.width || 1080, height = spec.height || 1920;
  if (!existsSync(opts.bundle)) throw new Error(`нет бандла ${opts.bundle}: npm run build:scene3d`);
  const work = mkdtempSync(join(tmpdir(), "vvf-scene3d-"));
  const chrome = spawn(findChrome(opts.chrome), chromeArgs(join(work, "profile"), opts.gl), { stdio: ["ignore", "ignore", "pipe", "pipe", "pipe"], windowsHide: true });
  let chromeErr = "";
  chrome.stderr.on("data", (d) => {
    chromeErr = (chromeErr + d.toString()).slice(-3000);
  });
  const cdp = new Cdp(chrome);
  const ctx = { width, height, bundle: opts.bundle, packs: opts.packs };
  const consoleLines = [];
  const close = async () => {
    try {
      await cdp.send("Browser.close");
    } catch {
      /* уже закрыт */
    }
    if (chrome.exitCode === null) chrome.kill();
    await new Promise((r) => (chrome.exitCode !== null ? r() : chrome.once("exit", r)));
    try {
      rmSync(work, { recursive: true, force: true, maxRetries: 3, retryDelay: 200 });
    } catch {
      /* профиль на Windows отпускается не сразу */
    }
  };
  try {
    const { targetId } = await cdp.send("Target.createTarget", { url: "about:blank" });
    const { sessionId } = await cdp.send("Target.attachToTarget", { targetId, flatten: true });
    const s = (method, params) => cdp.send(method, params, sessionId);
    cdp.on("Fetch.requestPaused", (p, sid) => {
      if (sid !== sessionId) return;
      let res;
      try {
        res = serve(p.request.url, ctx);
      } catch (err) {
        res = { status: 500, body: Buffer.from(String(err)) };
      }
      if (res.status === 403) {
        s("Fetch.failRequest", { requestId: p.requestId, errorReason: "BlockedByClient" }).catch(() => {});
        return;
      }
      s("Fetch.fulfillRequest", {
        requestId: p.requestId, responseCode: res.status,
        responseHeaders: [{ name: "Content-Type", value: res.mime || "text/plain" }, { name: "Access-Control-Allow-Origin", value: "*" }],
        body: (res.body || Buffer.alloc(0)).toString("base64"),
      }).catch(() => {});
    });
    cdp.on("Runtime.consoleAPICalled", (p, sid) => {
      if (sid !== sessionId) return;
      const text = (p.args || []).map((a) => a.value ?? a.description ?? "").join(" ");
      consoleLines.push(`${p.type}: ${text}`.slice(0, 400));
      if (consoleLines.length > 50) consoleLines.shift();
    });
    cdp.on("Runtime.exceptionThrown", (p, sid) => {
      if (sid === sessionId) consoleLines.push(`exception: ${p.exceptionDetails?.exception?.description || p.exceptionDetails?.text}`.slice(0, 600));
    });
    await s("Fetch.enable", { patterns: [{ urlPattern: "*" }] });
    await s("Page.enable");
    await s("Runtime.enable");
    await s("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: false });
    const loaded = cdp.once("Page.loadEventFired", sessionId);
    await s("Page.navigate", { url: `${ORIGIN}/index.html` });
    await loaded;
    const evaluate = async (expression) => {
      const r = await s("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
      if (r.exceptionDetails) {
        const msg = r.exceptionDetails.exception?.description || r.exceptionDetails.text;
        throw new Error(`сцена: ${String(msg).split("\n").slice(0, 3).join(" ")}`);
      }
      return r.result?.value;
    };
    const has = await evaluate("typeof window.__scene3d");
    if (has !== "object") throw new Error(`движок не загрузился (${consoleLines.slice(-3).join(" | ") || "нет __scene3d"})`);
    const info = await evaluate(`window.__scene3d.load(${JSON.stringify(spec)})`);
    log(`сцена загружена: ${width}×${height}${info?.warnings?.length ? `, предупреждения: ${info.warnings.join("; ")}` : ""}`);
    return {
      info,
      width,
      height,
      console: consoleLines,
      render: (t) => evaluate(`window.__scene3d.render(${Number(t).toFixed(6)})`),
      inspect: (t) => evaluate(`window.__scene3d.inspect(${Number(t).toFixed(6)})`),
      warnings: () => evaluate("window.__scene3d.warnings()"),
      shot: async (format = "jpeg", quality = 92) => {
        const r = await s("Page.captureScreenshot", {
          format, ...(format === "jpeg" ? { quality } : {}), fromSurface: true, captureBeyondViewport: false,
          clip: { x: 0, y: 0, width, height, scale: 1 },
        });
        return Buffer.from(r.data, "base64");
      },
      close,
    };
  } catch (err) {
    await close();
    const tail = chromeErr && /error|fail/i.test(chromeErr) ? ` (браузер: ${chromeErr.trim().split("\n").slice(-2).join(" ")})` : "";
    throw new Error(`${err.message}${consoleLines.length ? ` [консоль: ${consoleLines.slice(-4).join(" | ")}]` : ""}${tail}`);
  }
}

/* ---------- ffmpeg ---------- */

export function segmentArgs(fps, crf, format, output) {
  return [
    "-hide_banner", "-loglevel", "error", "-y",
    "-f", "image2pipe", "-framerate", String(fps), "-c:v", format === "png" ? "png" : "mjpeg", "-i", "pipe:0",
    // JPEG-кадры — полный диапазон (yuvj): переводим в обычный ТВ-диапазон BT.709, как у всех роликов сервиса.
    "-an", "-vf", "scale=out_range=tv:out_color_matrix=bt709,format=yuv420p", "-c:v", "libx264", "-preset", "medium",
    "-crf", String(crf), "-color_range", "tv", "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
    "-r", String(fps), "-g", String(fps * 2), "-bf", "2", output,
  ];
}

export function concatArgs(listFile, output) {
  return ["-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", listFile, "-c", "copy", "-movflags", "+faststart", output];
}

function run(cmd, args) {
  return new Promise((resolve, reject) => {
    const p = spawn(cmd, args, { stdio: ["ignore", "ignore", "pipe"], windowsHide: true });
    let err = "";
    p.stderr.on("data", (d) => (err = (err + d).slice(-2000)));
    p.on("error", reject);
    p.on("close", (code) => (code === 0 ? resolve() : reject(new Error(`${cmd}: ${err.trim() || `код ${code}`}`))));
  });
}

function write(stream, buf) {
  return new Promise((resolve, reject) => {
    if (stream.write(buf)) resolve();
    else {
      stream.once("drain", resolve);
      stream.once("error", reject);
    }
  });
}

/** Один кусок кадров [a, b) → сегмент mp4. Возвращает яркость каждого кадра. */
export async function renderChunk(opts, spec, fps, [a, b], segPath, log = () => {}) {
  const scene = await openScene(opts, spec, log);
  const ff = spawn(opts.ffmpeg, segmentArgs(fps, opts.crf, opts.format, segPath), { stdio: ["pipe", "ignore", "pipe"], windowsHide: true });
  let ffErr = "";
  ff.stderr.on("data", (d) => (ffErr = (ffErr + d).slice(-2000)));
  ff.on("error", (e) => (ffErr += String(e.message || e)));
  const ffDone = new Promise((resolve) => ff.on("close", resolve));
  const luma = [];
  try {
    for (let i = a; i < b; i++) {
      const st = await scene.render(i / fps);
      luma.push(st.luma);
      await write(ff.stdin, await scene.shot(opts.format, opts.quality));
      if ((i - a + 1) % 30 === 0 || i + 1 === b) log(`кусок ${a}-${b}: ${i - a + 1}/${b - a}`);
    }
  } finally {
    ff.stdin.end();
    await scene.close();
  }
  const code = await ffDone;
  if (code !== 0) throw new Error(`ffmpeg: ${ffErr.trim() || `код ${code}`}`);
  return { luma, warnings: scene.info?.warnings || [] };
}

export function splitFrames(total, parts, minChunk = 30) {
  const n = Math.max(1, Math.min(parts, Math.ceil(total / Math.max(1, minChunk))));
  const out = [];
  for (let i = 0; i < n; i++) {
    const a = Math.round((total * i) / n), b = Math.round((total * (i + 1)) / n);
    if (b > a) out.push([a, b]);
  }
  return out;
}

export const BLACK_LUMA = 6;

export async function renderVideo(opts, spec, log = () => {}) {
  const started = Date.now();
  const fps = opts.fps || spec.fps || 30;
  const duration = opts.duration || spec.duration || 5;
  const total = Math.max(1, Math.round(duration * fps));
  const [from, to] = opts.frames || [0, total];
  const chunks = splitFrames(to - from, opts.workers).map(([a, b]) => [a + from, b + from]);
  const work = mkdtempSync(join(dirname(opts.output), ".scene3d-"));
  const timer = setTimeout(() => {
    log(`не уложились в ${opts.timeout} с`);
    process.exit(1);
  }, opts.timeout * 1000);
  try {
    log(`${to - from} кадров ${spec.width || 1080}×${spec.height || 1920} @${fps}, кусков: ${chunks.length}, GL: ${opts.gl}`);
    const segs = chunks.map((_, i) => join(work, `seg_${String(i).padStart(3, "0")}.mp4`));
    const results = await Promise.all(chunks.map((c, i) => renderChunk(opts, spec, fps, c, segs[i], log)));
    const list = join(work, "list.txt");
    writeFileSync(list, segs.map((p) => `file '${p.replace(/\\/g, "/").replace(/'/g, "'\\''")}'`).join("\n") + "\n");
    await run(opts.ffmpeg, concatArgs(list, opts.output));
    const luma = results.flatMap((r) => r.luma);
    const black = luma.map((v, i) => [i + from, v]).filter(([, v]) => v < BLACK_LUMA).map(([i]) => i);
    return {
      ok: black.length === 0,
      frames: to - from,
      fps,
      duration: Math.round(((to - from) / fps) * 1000) / 1000,
      width: spec.width || 1080,
      height: spec.height || 1920,
      segments: chunks.length,
      black_frames: black,
      luma_min: Math.round(Math.min(...luma) * 10) / 10,
      luma_mean: Math.round((luma.reduce((s, v) => s + v, 0) / luma.length) * 10) / 10,
      seconds: Math.round((Date.now() - started) / 100) / 10,
      warnings: [...new Set(results.flatMap((r) => r.warnings))],
    };
  } finally {
    clearTimeout(timer);
    rmSync(work, { recursive: true, force: true });
  }
}

export async function renderPreviews(opts, spec, log = () => {}) {
  const scene = await openScene(opts, spec, log);
  const dir = opts.previewDir || dirname(opts.output || opts.spec);
  mkdirSync(dir, { recursive: true });
  const out = [];
  try {
    for (const t of opts.previews) {
      const st = await scene.render(t);
      const file = join(dir, `frame_${t.toFixed(2).replace(".", "_")}.png`);
      writeFileSync(file, await scene.shot("png"));
      out.push({ t, file, luma: Math.round(st.luma * 10) / 10 });
    }
    return { ok: true, previews: out, warnings: await scene.warnings(), console: scene.console.slice(-10) };
  } finally {
    await scene.close();
  }
}

async function main() {
  let opts;
  try {
    opts = parseArgs(process.argv.slice(2));
  } catch (err) {
    process.stderr.write(`${err.message}\n`);
    process.exit(2);
  }
  const log = (m) => process.stderr.write(`${m}\n`);
  try {
    const spec = JSON.parse(readFileSync(opts.spec, "utf8"));
    if (opts.fps) spec.fps = opts.fps;
    if (opts.duration) spec.duration = opts.duration;
    let res;
    if (opts.inspect !== null) {
      const scene = await openScene(opts, spec, log);
      try {
        res = { ok: true, inspect: await scene.inspect(opts.inspect), warnings: await scene.warnings(), console: scene.console.slice(-10) };
      } finally {
        await scene.close();
      }
    } else if (opts.previews) res = await renderPreviews(opts, spec, log);
    else res = await renderVideo(opts, spec, log);
    process.stdout.write(JSON.stringify(res) + "\n");
    if (res.black_frames && res.black_frames.length) process.exit(3);
  } catch (err) {
    process.stderr.write(`scene3d: ${err.message}\n`);
    process.exit(1);
  }
}

if (import.meta.url === pathToFileURL(process.argv[1] || "").href) void main();
