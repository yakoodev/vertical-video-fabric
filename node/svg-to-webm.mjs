#!/usr/bin/env node
/**
 * SVG (CSS @keyframes / SMIL) → WebM VP9 с альфой, покадрово и детерминированно.
 *
 * Перенос функционала svg-to-webm владельца (https://github.com/yakoodev/svg-to-webm, MIT,
 * export-node/export-svg.js + src/engine.js): SVG открывается в headless Chromium, на каждый
 * кадр анимации ставятся на паузу и перематываются на время кадра (SMIL — pauseAnimations +
 * setCurrentTime у каждого <svg>, CSS — document.getAnimations(): pause + currentTime), кадр
 * снимается PNG с прозрачным фоном и уходит в ffmpeg (libvpx-vp9, yuva420p, alpha_mode=1).
 * Реального времени нет нигде: тяжёлый SVG просто считается дольше, кадров ровно
 * round(duration·fps). Авто-длительность и «убрать фон» (--strip-bg) — как у оригинала.
 *
 * Отличия от оригинала: без Puppeteer (своя минимальная обвязка CDP через
 * --remote-debugging-pipe — зависимостей ноль, браузер берётся системный: в образе сервиса это
 * /usr/bin/chromium), кадры идут в ffmpeg потоком (image2pipe), а не во временные PNG; шрифты
 * сервиса (vendor/fonts, data/fonts — fonts.json) подключаются @font-face по семействам, что
 * упомянуты в SVG; <script> и on*-атрибуты из SVG вырезаются, сеть браузеру закрыта; текст с
 * data-fit="<px>" ужимается кеглем до этой ширины (шаблоны концовок, app/end_screen.py).
 *
 * node node/svg-to-webm.mjs --in a.svg --out a.webm [--fps 30] [--duration auto|3] [--width W]
 *   [--height H] [--scale 1] [--crf 18] [--strip-bg] [--fonts-dir DIR]… [--chrome PATH]
 *   [--ffmpeg ffmpeg] [--timeout 300]
 *
 * stdout — одна строка JSON {ok, frames, width, height, fps, duration, duration_source};
 * код выхода 0 — готово, 2 — ошибка аргументов, 1 — прочие (текст в stderr).
 */
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

class UsageError extends Error {}

/* ---------- аргументы ---------- */

export function parseArgs(argv) {
  const out = { fontsDirs: [], stripBg: false };
  for (let i = 0; i < argv.length; i++) {
    const tok = argv[i];
    if (!tok.startsWith("--")) throw new UsageError(`лишний аргумент ${tok}`);
    let [key, value] = tok.slice(2).split(/=(.*)/s, 2);
    if (key === "strip-bg") {
      out.stripBg = true;
      continue;
    }
    if (value === undefined) {
      value = argv[++i];
      if (value === undefined) throw new UsageError(`--${key}: нужно значение`);
    }
    if (key === "fonts-dir") out.fontsDirs.push(value);
    else out[key.replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = value;
  }
  if (!out.in || !out.out) throw new UsageError("нужны --in a.svg и --out a.webm");
  const num = (name, def, lo, hi) => {
    if (out[name] === undefined || out[name] === "auto") return def;
    const n = Number(out[name]);
    if (!Number.isFinite(n)) throw new UsageError(`--${name}: не число`);
    return Math.max(lo, Math.min(hi, n));
  };
  return {
    input: resolve(out.in),
    output: resolve(out.out),
    fps: Math.round(num("fps", 30, 1, 60)),
    duration: num("duration", null, 0.05, 60),
    width: num("width", null, 2, 4096),
    height: num("height", null, 2, 4096),
    scale: num("scale", 1, 0.25, 4),
    crf: Math.round(num("crf", 18, 0, 63)),
    stripBg: out.stripBg,
    fontsDirs: out.fontsDirs,
    chrome: out.chrome || process.env.SVG_CHROMIUM || process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || "",
    ffmpeg: out.ffmpeg || "ffmpeg",
    timeout: num("timeout", 300, 10, 3600),
  };
}

/* ---------- разбор SVG (как export-svg.js оригинала) ---------- */

function parseSvgLength(value) {
  if (!value) return null;
  const m = String(value).trim().match(/^(-?\d*\.?\d+(?:e[-+]?\d+)?)(px)?$/i);
  if (!m) return null;
  const n = Number(m[1]);
  return Number.isFinite(n) && n > 0 ? n : null;
}

/** Размер SVG: width/height корня, иначе viewBox, иначе 800×600. */
export function svgMetrics(svg) {
  const root = (svg.match(/<svg\b[^>]*>/i) || [""])[0];
  const vbm = root.match(/viewBox\s*=\s*["']([^"']+)["']/i);
  const nums = vbm ? vbm[1].trim().split(/[\s,]+/).map(Number) : [];
  const viewBox = nums.length === 4 && nums.every(Number.isFinite) && nums[2] > 0 && nums[3] > 0 ? nums : null;
  const w = parseSvgLength((root.match(/\bwidth\s*=\s*["']([^"']+)["']/i) || [])[1]);
  const h = parseSvgLength((root.match(/\bheight\s*=\s*["']([^"']+)["']/i) || [])[1]);
  return { width: Math.round(w || (viewBox ? viewBox[2] : 800)), height: Math.round(h || (viewBox ? viewBox[3] : 600)), viewBox };
}

const attr = (attrs, name) => {
  const m = String(attrs || "").match(new RegExp(`${name}\\s*=\\s*(["'])((?:(?!\\1).)*)\\1`, "i"));
  return m ? m[2] : null;
};
const parseTime = (s) => {
  if (!s) return null;
  const v = parseFloat(s);
  if (!Number.isFinite(v) || v <= 0) return null;
  return /ms\s*$/.test(String(s)) ? v / 1000 : v;
};
const parseCssTime = (value) => {
  const m = String(value || "").trim().match(/^(-?(?:\d+\.?\d*|\.\d+))(ms|s)$/i);
  if (!m) return null;
  const n = Number(m[1]);
  return Number.isFinite(n) && n > 0 ? (m[2].toLowerCase() === "ms" ? n / 1000 : n) : null;
};

function splitCssList(value) {
  const parts = [];
  let cur = "";
  let depth = 0;
  for (const ch of String(value)) {
    if (ch === "(") depth++;
    if (ch === ")") depth = Math.max(0, depth - 1);
    if (ch === "," && depth === 0) {
      parts.push(cur);
      cur = "";
    } else cur += ch;
  }
  if (cur.trim()) parts.push(cur);
  return parts;
}

/**
 * Длительность анимации SVG — «доминирующая» (самая частая из 1..120 с), как у оригинала: главный
 * сценарий 12 с не тонет в мелких 0.3-секундных подёргиваниях. Нет анимаций — 5 с.
 */
export function detectDuration(svg) {
  const list = [];
  const smil = /<(animate|animateTransform|animateMotion|set)\b([^>]*)>/gi;
  let m;
  while ((m = smil.exec(svg))) {
    const a = m[2] || "";
    const dur = parseTime(attr(a, "dur"));
    const beginRaw = String(attr(a, "begin") || "").split(";")[0].trim();
    const begin = Math.max(0, beginRaw && beginRaw !== "indefinite" ? parseFloat(beginRaw) * (/ms$/.test(beginRaw) ? 0.001 : 1) || 0 : 0);
    const repeatDur = parseTime(attr(a, "repeatDur"));
    const rc = attr(a, "repeatCount");
    let total = 0;
    if (repeatDur) total = begin + repeatDur;
    else if (dur) total = begin + dur * (rc && rc !== "indefinite" && Number(rc) > 0 ? Number(rc) : 1);
    if (total > 0.01) list.push(total);
  }
  const prop = /animation-duration\s*:\s*([^;}]+)/gi;
  while ((m = prop.exec(svg))) for (const part of m[1].split(",")) {
    const t = parseCssTime(part);
    if (t) list.push(t);
  }
  const short = /(?:^|[;{\s])animation\s*:\s*([^;}]+)/gi;
  while ((m = short.exec(svg))) for (const part of splitCssList(m[1])) {
    const tm = part.match(/(^|[^\w.-])(-?(?:\d+\.?\d*|\.\d+)(?:ms|s))\b/i);
    const t = tm ? parseCssTime(tm[2]) : null;
    if (t) list.push(t);
  }
  const round = (n) => Math.round(n * 100) / 100;
  if (!list.length) return 5;
  const usable = list.map(round).filter((d) => d >= 1 && d <= 120);
  const pool = usable.length ? usable : list.map(round).filter((d) => d > 0 && d <= 120);
  if (!pool.length) return 5;
  const counts = new Map();
  for (const d of pool) counts.set(Math.round(d * 10) / 10, (counts.get(Math.round(d * 10) / 10) || 0) + 1);
  return round([...counts.entries()].sort((a, b) => b[1] - a[1] || b[0] - a[0])[0][0]);
}

/** Вырезать исполняемое из SVG: <script>, on*-обработчики, javascript:-ссылки. */
export function sanitizeSvg(svg) {
  return String(svg)
    .replace(/<\?xml[^>]*\?>/gi, "")
    .replace(/<!DOCTYPE[^>]*>/gi, "")
    .replace(/<script\b[\s\S]*?<\/script\s*>/gi, "")
    .replace(/<script\b[^>]*\/>/gi, "")
    .replace(/\son[a-z]+\s*=\s*("[^"]*"|'[^']*')/gi, "")
    .replace(/(href\s*=\s*["'])\s*javascript:[^"']*/gi, "$1#");
}

/** @font-face для шрифтов сервиса, чьи семейства встречаются в тексте SVG. */
export function fontFaces(svg, dirs) {
  const text = svg.toLowerCase();
  const rules = [];
  const seen = new Set();
  for (const dir of dirs) {
    let list;
    try {
      list = JSON.parse(readFileSync(join(dir, "fonts.json"), "utf8"));
    } catch {
      continue;
    }
    for (const f of Array.isArray(list) ? list : []) {
      const family = String(f.family || "").trim();
      const file = String(f.file || "");
      if (!family || !file || !text.includes(family.toLowerCase())) continue;
      const path = join(dir, file);
      const key = `${family}|${f.weight}|${f.style}`;
      if (seen.has(key) || !existsSync(path)) continue;
      seen.add(key);
      const weight = f.variable && Array.isArray(f.weight_range) ? f.weight_range.join(" ") : String(f.weight || 400);
      rules.push(`@font-face{font-family:"${family.replace(/"/g, "")}";src:url("${pathToFileURL(path).href}");` +
        `font-weight:${weight};font-style:${f.style === "italic" ? "italic" : "normal"};font-display:block}`);
    }
  }
  return rules.join("\n");
}

/** Страница кадра: SVG во весь вьюпорт W×H, прозрачный фон, свои шрифты и управление временем. */
export function pageHtml(svg, width, height, fonts, stripBg) {
  return `<!doctype html><html><head><meta charset="utf-8"><style>
${fonts}
html,body{margin:0;padding:0;width:${width}px;height:${height}px;overflow:hidden;background:transparent}
body>svg:first-of-type{display:block;width:${width}px;height:${height}px;background:transparent}
*{-webkit-font-smoothing:antialiased}
</style></head><body>${svg}<script>
(() => {
  const W = ${width}, H = ${height}, STRIP = ${stripBg ? "true" : "false"};
  const root = document.querySelector("body > svg");
  const raf = () => new Promise((r) => requestAnimationFrame(() => r()));
  function stripBackground() {
    const skip = new Set(["defs", "style", "title", "desc", "metadata"]);
    const first = Array.from(root.children).find((el) => !skip.has(el.tagName.toLowerCase()));
    if (!first || first.tagName.toLowerCase() !== "rect") return;
    const vb = root.viewBox && root.viewBox.baseVal && root.viewBox.baseVal.width ? root.viewBox.baseVal : { width: W, height: H };
    const rw = first.getAttribute("width") || "", rh = first.getAttribute("height") || "";
    const fill = first.getAttribute("fill"), stroke = first.getAttribute("stroke");
    const covers = (rw === "100%" || Math.abs(parseFloat(rw) - vb.width) < 0.5) && (rh === "100%" || Math.abs(parseFloat(rh) - vb.height) < 0.5) &&
      parseFloat(first.getAttribute("x") || "0") === 0 && parseFloat(first.getAttribute("y") || "0") === 0 && fill && fill !== "none" && (!stroke || stroke === "none");
    if (covers) first.remove();
  }
  function fit() {
    // data-fit="<ширина в единицах viewBox>": кегль ужимается, пока строка не влезет.
    for (const el of document.querySelectorAll("[data-fit]")) {
      const max = parseFloat(el.getAttribute("data-fit"));
      if (!(max > 0) || typeof el.getComputedTextLength !== "function") continue;
      for (let i = 0; i < 6; i++) {
        const len = el.getComputedTextLength();
        if (!(len > max)) break;
        const size = parseFloat(getComputedStyle(el).fontSize) || 16;
        el.style.fontSize = (size * max / len * 0.995).toFixed(2) + "px";
      }
    }
  }
  function seek(t) {
    for (const svg of document.querySelectorAll("svg")) {
      if (typeof svg.pauseAnimations === "function") { svg.pauseAnimations(); svg.setCurrentTime(t); }
    }
    for (const a of document.getAnimations ? document.getAnimations() : []) {
      try { a.pause(); a.currentTime = t * 1000; } catch (_) {}
    }
  }
  window.__vvf = {
    async ready() {
      if (!root) throw new Error("в SVG нет корневого <svg>");
      seek(0);
      root.setAttribute("width", String(W));
      root.setAttribute("height", String(H));
      if (!root.getAttribute("viewBox")) root.setAttribute("viewBox", "0 0 " + W + " " + H);
      if (STRIP) stripBackground();
      if (document.fonts && document.fonts.ready) { try { await document.fonts.ready; } catch (_) {} }
      fit();
      seek(0);
      await raf();
      return { animations: document.getAnimations ? document.getAnimations().length : 0 };
    },
    async seek(t) { seek(t); await raf(); return true; },
  };
})();
</script></body></html>`;
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
  if (!hit) throw new Error("нет headless Chromium: задай SVG_CHROMIUM (в образе сервиса — /usr/bin/chromium)");
  return hit;
}

class Cdp {
  constructor(proc) {
    this.proc = proc;
    this.seq = 0;
    this.pending = new Map();
    this.waiters = [];
    this.buf = "";
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
    for (const w of this.waiters) w.reject(err);
    this.waiters = [];
  }
  onData(chunk) {
    this.buf += chunk.toString("utf8");
    let idx;
    while ((idx = this.buf.indexOf("\0")) >= 0) {
      const raw = this.buf.slice(0, idx);
      this.buf = this.buf.slice(idx + 1);
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
        this.waiters = this.waiters.filter((w) => {
          if (w.method !== msg.method || (w.sessionId && w.sessionId !== msg.sessionId)) return true;
          w.resolve(msg.params || {});
          return false;
        });
      }
    }
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
  once(method, sessionId) {
    if (this.dead) return Promise.reject(this.dead);
    return new Promise((resolve, reject) => this.waiters.push({ method, sessionId, resolve, reject }));
  }
}

function launchChrome(path, profile) {
  const args = [
    "--headless=new", "--remote-debugging-pipe", `--user-data-dir=${profile}`, "--no-first-run",
    "--no-default-browser-check", "--disable-extensions", "--disable-background-networking", "--disable-sync",
    "--disable-component-update", "--disable-default-apps", "--disable-gpu", "--disable-dev-shm-usage",
    "--no-sandbox", "--hide-scrollbars", "--mute-audio", "--font-render-hinting=none", "--force-color-profile=srgb",
    "--allow-file-access-from-files", "--host-resolver-rules=MAP * ~NOTFOUND", "about:blank",
  ];
  return spawn(path, args, { stdio: ["ignore", "ignore", "pipe", "pipe", "pipe"], windowsHide: true });
}

/* ---------- ffmpeg ---------- */

export function encoderArgs(fps, crf, output) {
  return [
    "-hide_banner", "-loglevel", "error", "-y",
    "-f", "image2pipe", "-framerate", String(fps), "-c:v", "png", "-i", "pipe:0",
    "-an", "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-auto-alt-ref", "0", "-metadata:s:v:0", "alpha_mode=1",
    "-b:v", "0", "-crf", String(crf), "-deadline", "good", "-cpu-used", "2", "-row-mt", "1",
    "-g", String(Math.max(1, fps * 2)), output,
  ];
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

/* ---------- главное ---------- */

export async function convert(opts, log = () => {}) {
  const raw = readFileSync(opts.input, "utf8");
  const svg = sanitizeSvg(raw);
  if (!/<svg\b/i.test(svg)) throw new Error("в файле нет <svg>");
  const metrics = svgMetrics(svg);
  const width = Math.round(opts.width || metrics.width);
  const height = Math.round(opts.height || metrics.height);
  const durationSource = opts.duration ? "given" : "auto";
  const duration = opts.duration || detectDuration(svg);
  const frames = Math.max(1, Math.round(duration * opts.fps));
  // yuv420 — чётные стороны выхода.
  const outW = Math.max(2, Math.round(width * opts.scale) & ~1);
  const outH = Math.max(2, Math.round(height * opts.scale) & ~1);
  const work = mkdtempSync(join(tmpdir(), "vvf-svg-"));
  const page = join(work, "page.html");
  writeFileSync(page, pageHtml(svg, width, height, fontFaces(svg, opts.fontsDirs), opts.stripBg), "utf8");

  const chrome = launchChrome(findChrome(opts.chrome), join(work, "profile"));
  let chromeErr = "";
  chrome.stderr.on("data", (d) => {
    chromeErr = (chromeErr + d.toString()).slice(-2000);
  });
  const cdp = new Cdp(chrome);
  const ff = spawn(opts.ffmpeg, encoderArgs(opts.fps, opts.crf, opts.output), { stdio: ["pipe", "ignore", "pipe"], windowsHide: true });
  let ffErr = "";
  ff.stderr.on("data", (d) => {
    ffErr = (ffErr + d.toString()).slice(-2000);
  });
  const ffDone = new Promise((resolve) => ff.on("close", (code) => resolve(code)));
  ff.on("error", (err) => {
    ffErr += String(err.message || err);
  });
  const timer = setTimeout(() => cdp.fail(new Error(`не уложились в ${opts.timeout} с`)), opts.timeout * 1000);
  try {
    const { targetId } = await cdp.send("Target.createTarget", { url: "about:blank" });
    const { sessionId } = await cdp.send("Target.attachToTarget", { targetId, flatten: true });
    const s = (method, params) => cdp.send(method, params, sessionId);
    await s("Page.enable");
    await s("Runtime.enable");
    await s("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: outW / width, mobile: false });
    await s("Emulation.setDefaultBackgroundColorOverride", { color: { r: 0, g: 0, b: 0, a: 0 } });
    const loaded = cdp.once("Page.loadEventFired", sessionId);
    await s("Page.navigate", { url: pathToFileURL(page).href });
    await loaded;
    const evaluate = async (expression) => {
      const r = await s("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
      if (r.exceptionDetails) throw new Error(`SVG: ${r.exceptionDetails.exception?.description || r.exceptionDetails.text}`);
      return r.result?.value;
    };
    const ready = await evaluate("window.__vvf ? window.__vvf.ready() : Promise.reject(new Error('страница не готова'))");
    log(`svg ${width}×${height} → webm ${outW}×${outH}, ${opts.fps} fps, ${duration} с (${frames} кадров), анимаций CSS: ${ready?.animations ?? 0}`);
    for (let i = 0; i < frames; i++) {
      await evaluate(`window.__vvf.seek(${(i / opts.fps).toFixed(6)})`);
      const shot = await s("Page.captureScreenshot", {
        format: "png", fromSurface: true, captureBeyondViewport: false,
        clip: { x: 0, y: 0, width, height, scale: 1 },
      });
      await write(ff.stdin, Buffer.from(shot.data, "base64"));
      if ((i + 1) % 30 === 0 || i + 1 === frames) log(`progress ${i + 1}/${frames}`);
    }
    try {
      await cdp.send("Browser.close");
    } catch {
      /* браузер мог уже закрыться */
    }
  } catch (err) {
    ff.stdin.destroy();
    throw new Error(`${err.message}${chromeErr && /error|fail/i.test(chromeErr) ? ` (браузер: ${chromeErr.trim().split("\n").slice(-2).join(" ")})` : ""}`);
  } finally {
    clearTimeout(timer);
    if (chrome.exitCode === null) chrome.kill();
    ff.stdin.end();
    await new Promise((r) => (chrome.exitCode !== null ? r() : chrome.once("exit", r)));
    try {
      rmSync(work, { recursive: true, force: true, maxRetries: 3, retryDelay: 200 });
    } catch {
      /* профиль браузера на Windows отпускается не сразу */
    }
  }
  const code = await ffDone;
  if (code !== 0) throw new Error(`ffmpeg: ${ffErr.trim() || `код ${code}`}`);
  return { ok: true, frames, width: outW, height: outH, fps: opts.fps, duration: Math.round((frames / opts.fps) * 1000) / 1000, duration_source: durationSource };
}

async function main() {
  let opts;
  try {
    opts = parseArgs(process.argv.slice(2));
  } catch (err) {
    process.stderr.write(`${err.message}\n`);
    process.exit(2);
  }
  try {
    const res = await convert(opts, (m) => process.stderr.write(`${m}\n`));
    process.stdout.write(JSON.stringify(res) + "\n");
  } catch (err) {
    process.stderr.write(`svg-to-webm: ${err.message}\n`);
    process.exit(1);
  }
}

if (import.meta.url === pathToFileURL(process.argv[1] || "").href) void main();
