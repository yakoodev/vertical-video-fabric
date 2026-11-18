#!/usr/bin/env node
/**
 * Переходы gl-transitions кадрами (app/transitions_gl.py): один headless Chromium с WebGL на весь рендер,
 * задания — строками JSON на stdin, ответ — строка JSON на stdout на каждое задание.
 *
 * Задание: {id, shader: "Cube", width, height, frames, progress: [p0…], a, b, out, params?}
 *   a, b — файлы сырых RGB (frames кадров width×height, строки сверху вниз), out — куда записать столько же кадров.
 * Ответ: {id, ok: true, frames, ms, params: [имена параметров шейдера]} или {id, ok: false, error}.
 *
 * Браузер и обвязка CDP — те же, что у 3D-сцен (node/scene3d/render.mjs): сеть закрыта, страница, шейдеры и кадры
 * отдаются через CDP Fetch (шейдеры — только из node/gltrans/shaders, кадры — только файлы текущего задания).
 * WebGL — SwiftShader (программный: одинаково на сервере и локально, видеокарту не трогаем); --gl gpu — на видеокарте.
 *
 * node node/gltrans/render.mjs [--chrome PATH] [--gl swiftshader|gpu]   (задания — на stdin)
 */
import { spawn } from "node:child_process";
import { closeSync, existsSync, mkdtempSync, openSync, readFileSync, readSync, rmSync, statSync, writeSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { createInterface } from "node:readline";
import { fileURLToPath, pathToFileURL } from "node:url";

import { Cdp, chromeArgs, chromeErrorTail, findChrome, pageErrorText, safeJoin } from "../scene3d/render.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
export const ORIGIN = "http://gltrans.local";
export const SHADERS = join(HERE, "shaders");
/** Предел стороны кадра и числа кадров окна (окно перехода — доли секунды). */
export const MAX_SIDE = 4096;
export const MAX_FRAMES = 240;

export function pageHtml() {
  return `<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;background:#000}canvas{display:block}</style></head>
<body><canvas id="c" width="16" height="16"></canvas><script src="${ORIGIN}/harness.js"></script></body></html>`;
}

/** Ответ странице: {status, body, mime}. ``frames`` — файлы текущего задания {a, b}. */
export function serve(url, frames) {
  if (!url.startsWith(ORIGIN + "/")) return { status: 403 };
  const path = url.slice(ORIGIN.length).split(/[?#]/)[0];
  if (path === "/index.html") return { status: 200, body: Buffer.from(pageHtml()), mime: "text/html; charset=utf-8" };
  if (path === "/harness.js") return { status: 200, body: readFileSync(join(HERE, "harness.js")), mime: "text/javascript; charset=utf-8" };
  if (path.startsWith("/shaders/")) {
    const file = safeJoin(SHADERS, path.slice("/shaders/".length));
    if (!file || !file.endsWith(".glsl") || !existsSync(file)) return { status: 404 };
    return { status: 200, body: readFileSync(file), mime: "text/plain; charset=utf-8" };
  }
  const m = /^\/frames\/(a|b)\/(\d{1,4})$/.exec(path);
  if (m && frames && frames[m[1]] && existsSync(frames[m[1]])) {
    // Один кадр файла задания (сдвиг j × размер кадра): весь файл в CDP за раз не гоняем.
    const j = Number(m[2]);
    if (j >= frames.count) return { status: 404 };
    const size = frames.size;
    const body = Buffer.alloc(size);
    const fd = openSync(frames[m[1]], "r");
    try {
      if (readSync(fd, body, 0, size, j * size) !== size) return { status: 404 };
    } finally {
      closeSync(fd);
    }
    return { status: 200, body, mime: "application/octet-stream" };
  }
  return { status: 404 };
}

/** Проверка задания: ошибка строкой или null. */
export function jobProblem(job) {
  if (!job || typeof job !== "object") return "задание — объект JSON";
  if (!/^[A-Za-z0-9_]{1,64}$/.test(String(job.shader || ""))) return "shader — имя файла шейдера без .glsl";
  if (!existsSync(join(SHADERS, `${job.shader}.glsl`))) return `нет шейдера ${job.shader}`;
  const w = Number(job.width), h = Number(job.height), n = Number(job.frames);
  if (!Number.isInteger(w) || !Number.isInteger(h) || w < 2 || h < 2 || w > MAX_SIDE || h > MAX_SIDE) return "width/height — 2…4096";
  if (!Number.isInteger(n) || n < 1 || n > MAX_FRAMES) return `frames — 1…${MAX_FRAMES}`;
  if (!Array.isArray(job.progress) || job.progress.length !== n || job.progress.some((p) => !Number.isFinite(p))) return "progress — по числу на кадр";
  for (const k of ["a", "b", "out"]) if (typeof job[k] !== "string" || !job[k]) return `${k} — путь к файлу`;
  if (!existsSync(job.a) || !existsSync(job.b)) return "нет файла кадров a/b";
  return null;
}

/** Браузер со страницей обвязки: {run(job), close()}. */
export async function openRenderer(opts = {}) {
  const work = mkdtempSync(join(tmpdir(), "vvf-gltrans-"));
  const gl = opts.gl || process.env.GLTRANS_GL || "swiftshader";
  const chrome = spawn(findChrome(opts.chrome), chromeArgs(join(work, "profile"), gl, "cpu"), {
    stdio: ["ignore", "ignore", "pipe", "pipe", "pipe"], windowsHide: true,
  });
  let chromeErr = "";
  chrome.stderr.on("data", (d) => {
    chromeErr = (chromeErr + d.toString()).slice(-3000);
  });
  const cdp = new Cdp(chrome);
  const ctx = { frames: null };
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
        res = serve(p.request.url, ctx.frames);
      } catch (err) {
        res = { status: 500, body: Buffer.from(String(err)) };
      }
      if (res.status === 403) {
        s("Fetch.failRequest", { requestId: p.requestId, errorReason: "BlockedByClient" }).catch(() => {});
        return;
      }
      s("Fetch.fulfillRequest", {
        requestId: p.requestId, responseCode: res.status,
        responseHeaders: [{ name: "Content-Type", value: res.mime || "text/plain" }],
        body: (res.body || Buffer.alloc(0)).toString("base64"),
      }).catch(() => {});
    });
    cdp.on("Runtime.exceptionThrown", (p, sid) => {
      if (sid === sessionId) consoleLines.push(pageErrorText(p.exceptionDetails?.exception?.description || p.exceptionDetails?.text));
    });
    await s("Fetch.enable", { patterns: [{ urlPattern: "*" }] });
    await s("Page.enable");
    await s("Runtime.enable");
    const loaded = cdp.once("Page.loadEventFired", sessionId);
    await s("Page.navigate", { url: `${ORIGIN}/index.html` });
    await loaded;
    const evaluate = async (expression) => {
      const r = await s("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
      if (r.exceptionDetails) throw new Error(pageErrorText(r.exceptionDetails.exception?.description || r.exceptionDetails.text));
      return r.result?.value;
    };
    const ok = await evaluate("!!(window.__gltrans && window.__gltrans.ok)");
    if (!ok) throw new Error(`WebGL недоступен${consoleLines.length ? ` (${consoleLines.slice(-2).join(" | ")})` : ""}`);
    const run = async (job) => {
      const problem = jobProblem(job);
      if (problem) throw new Error(problem);
      const t0 = Date.now();
      const size = job.width * job.height * 3;
      for (const k of ["a", "b"]) {
        if (statSync(job[k]).size < size * job.frames) throw new Error(`в ${k} меньше ${job.frames} кадров ${job.width}×${job.height}`);
      }
      ctx.frames = { a: resolve(job.a), b: resolve(job.b), size, count: job.frames };
      const spec = { shader: `${ORIGIN}/shaders/${job.shader}.glsl`, width: job.width, height: job.height, frames: job.frames,
                     a: `${ORIGIN}/frames/a`, b: `${ORIGIN}/frames/b`, params: job.params || {} };
      const info = await evaluate(`window.__gltrans.load(${JSON.stringify(spec)})`);
      const fd = openSync(job.out, "w");
      try {
        for (let j = 0; j < job.frames; j++) {
          const b64 = await evaluate(`window.__gltrans.frame(${j}, ${Number(job.progress[j]).toFixed(6)})`);
          const buf = Buffer.from(String(b64 || ""), "base64");
          if (buf.length !== job.width * job.height * 3) throw new Error(`кадр ${j}: ${buf.length} байт вместо ${job.width * job.height * 3}`);
          writeSync(fd, buf);
        }
      } finally {
        closeSync(fd);
        ctx.frames = null;
      }
      return { frames: job.frames, ms: Date.now() - t0, params: info?.params || [] };
    };
    return { run, close, evaluate };
  } catch (err) {
    await close();
    const browser = chromeErrorTail(chromeErr);
    throw new Error(`${err.message}${browser.length ? ` (браузер: ${browser.join(" ").slice(0, 300)})` : ""}`);
  }
}

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i++) {
    const tok = argv[i];
    if (!tok.startsWith("--")) continue;
    const [key, value] = tok.slice(2).split(/=(.*)/s, 2);
    out[key] = value !== undefined ? value : argv[++i];
  }
  return out;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const say = (obj) => process.stdout.write(JSON.stringify(obj) + "\n");
  let renderer;
  try {
    renderer = await openRenderer({ chrome: args.chrome || process.env.SCENE3D_CHROMIUM || process.env.SVG_CHROMIUM || "", gl: args.gl });
  } catch (err) {
    say({ id: null, ok: false, fatal: true, error: err.message });
    process.exit(1);
  }
  say({ id: null, ok: true, ready: true });
  const lines = createInterface({ input: process.stdin, crlfDelay: Infinity });
  for await (const line of lines) {
    if (!line.trim()) continue;
    let job;
    try {
      job = JSON.parse(line);
    } catch {
      say({ id: null, ok: false, error: "строка задания — не JSON" });
      continue;
    }
    try {
      say({ id: job.id ?? null, ok: true, ...(await renderer.run(job)) });
    } catch (err) {
      say({ id: job.id ?? null, ok: false, error: err.message });
    }
  }
  await renderer.close();
}

if (import.meta.url === pathToFileURL(process.argv[1] || "").href) void main();
