#!/usr/bin/env node
/**
 * Страницы сайтов как материал для роликов (app/webpages.py): снимок страницы и правка её копии.
 *
 *   node node/webpage/snapshot.mjs --capture URL --out DIR [--viewport mobile|desktop] [--max-height 6000]
 *       Открыть страницу в headless Chromium (ТОЛЬКО ПРОСМОТР: обычная загрузка GET, прокрутка для ленивых
 *       картинок; никаких кликов, форм, входа, «Купить», согласий с баннерами, cookie аккаунтов — профиль
 *       пустой и временный). Сохранить копию: DIR/v1/index.html (DOM без скриптов, адреса — на локальные),
 *       DIR/assets/* (стили, картинки, шрифты из ответов сети, url() в css переписаны), кадры всей страницы
 *       DIR/v1/tile_*.png (склеивает Python). Капча / «вы не робот» — status "blocked" и стоп.
 *   node node/webpage/snapshot.mjs --render DIR --from v1 --to v2 [--ops ops.json] [--viewport …]
 *       Открыть копию DIR/<from>/index.html без сети (файлы — из DIR через CDP Fetch), применить правки DOM
 *       (ops), сохранить DIR/<to>/index.html и кадры. Без --ops — просто переснять.
 *
 * stdout — одна строка JSON: {ok, status, reason?, url, final_url, title, page_width, page_height, shot_height,
 * dpr, tiles: [{file, y, h}], outline, applied?, region?, assets?}. Код выхода 0 — готово (в том числе blocked),
 * 2 — аргументы, 1 — прочее. Браузер один и закрывается в finally.
 */
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { extname, join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { Cdp, findChrome, safeJoin } from "../scene3d/render.mjs";
import { applyOps, detectBlock, outlinePage, serializePage } from "./page-scripts.mjs";

export const LOCAL_ORIGIN = "http://vvf-page.local";
export const VIEWPORTS = {
  mobile: { width: 390, height: 844, deviceScaleFactor: 3, mobile: true },
  desktop: { width: 1280, height: 800, deviceScaleFactor: 2, mobile: false },
};
const MOBILE_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1";
// Один кадр Chromium — не выше этого числа пикселей (большой кадр GPU-текстурой не снимается).
const TILE_PX = 4000;
const MAX_ASSET_BYTES = 15 * 1024 * 1024;
const MAX_TOTAL_BYTES = 80 * 1024 * 1024;
const MAX_ASSETS = 600;
const KEEP_TYPES = new Set(["Stylesheet", "Image", "Font", "Other"]);
const EXT_BY_MIME = {
  "text/css": ".css", "image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/webp": ".webp", "image/gif": ".gif",
  "image/svg+xml": ".svg", "image/avif": ".avif", "image/x-icon": ".ico", "image/vnd.microsoft.icon": ".ico",
  "font/woff2": ".woff2", "font/woff": ".woff", "font/ttf": ".ttf", "font/otf": ".otf", "application/font-woff": ".woff",
  "application/font-woff2": ".woff2", "application/x-font-ttf": ".ttf", "application/vnd.ms-fontobject": ".eot",
};
const MIME_BY_EXT = Object.fromEntries(Object.entries(EXT_BY_MIME).map(([m, e]) => [e, m]));
MIME_BY_EXT[".html"] = "text/html; charset=utf-8";
MIME_BY_EXT[".jpg"] = "image/jpeg";

class UsageError extends Error {}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
// Таймер-предел, который не держит процесс живым (иначе node ждёт его до конца после работы).
const idleTimer = (ms) => new Promise((r) => setTimeout(r, ms).unref());
const T0 = Date.now();
const debug = (m) => process.env.WEBPAGE_DEBUG && process.stderr.write(`[${((Date.now() - T0) / 1000).toFixed(1)}s] ${m}\n`);

export function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i++) {
    const tok = argv[i];
    if (!tok.startsWith("--")) throw new UsageError(`лишний аргумент ${tok}`);
    const key = tok.slice(2).replace(/-([a-z])/g, (_, c) => c.toUpperCase());
    const next = argv[i + 1];
    out[key] = next !== undefined && !next.startsWith("--") ? argv[++i] : true;
  }
  if (!out.capture && !out.render) throw new UsageError("нужно --capture URL --out DIR или --render DIR --from v1 --to v2");
  if (out.capture && !out.out) throw new UsageError("--capture: нужен --out DIR");
  if (out.render && (!out.from || !out.to)) throw new UsageError("--render: нужны --from и --to");
  if (out.capture && !/^https?:\/\//i.test(String(out.capture))) throw new UsageError("--capture: адрес http(s)://");
  out.viewport = VIEWPORTS[out.viewport] ? out.viewport : "mobile";
  out.maxHeight = Math.max(800, Math.min(20000, Number(out.maxHeight) || 6000));
  out.timeout = Math.max(10, Math.min(180, Number(out.timeout) || 45)) * 1000;
  return out;
}

function chromePath() {
  return findChrome(process.env.WEBPAGE_CHROMIUM || process.env.SCENE3D_CHROMIUM || process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || undefined);
}

/** Браузер с пустым временным профилем; offline — сеть закрыта (копия отдаётся через Fetch). */
async function openBrowser(offline) {
  const work = mkdtempSync(join(tmpdir(), "vvf-webpage-"));
  const args = [
    "--headless=new", "--remote-debugging-pipe", `--user-data-dir=${join(work, "profile")}`, "--no-first-run",
    "--no-default-browser-check", "--disable-extensions", "--disable-sync", "--disable-background-networking",
    "--disable-component-update", "--disable-default-apps", "--disable-dev-shm-usage", "--no-sandbox", "--hide-scrollbars",
    "--mute-audio", "--force-color-profile=srgb", "--disable-features=Translate,MediaRouter", "--deny-permission-prompts",
    "--js-flags=--max-old-space-size=512",
  ];
  if (offline) args.push("--host-resolver-rules=MAP * ~NOTFOUND");
  args.push("about:blank");
  const chrome = spawn(chromePath(), args, { stdio: ["ignore", "ignore", "pipe", "pipe", "pipe"], windowsHide: true });
  chrome.stderr.on("data", () => {});
  const cdp = new Cdp(chrome);
  const { targetId } = await cdp.send("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await cdp.send("Target.attachToTarget", { targetId, flatten: true });
  const s = (m, p) => cdp.send(m, p, sessionId);
  const evaluate = async (expression) => {
    const r = await s("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
    return r.result?.value;
  };
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
      /* профиль отпускается не сразу */
    }
  };
  await s("Page.enable");
  await s("Runtime.enable");
  // Диалоги страницы (alert/confirm) — закрываем «Отмена»; загрузки файлов запрещены.
  cdp.on("Page.javascriptDialogOpening", (_p, sid) => {
    if (sid === sessionId) s("Page.handleJavaScriptDialog", { accept: false }).catch(() => {});
  });
  await cdp.send("Browser.setDownloadBehavior", { behavior: "deny" }).catch(() => {});
  return { cdp, s, sessionId, evaluate, close };
}

async function setViewport(b, name) {
  const vp = VIEWPORTS[name];
  await b.s("Emulation.setDeviceMetricsOverride", vp);
  // «Меньше движения»: многие сайты тогда сразу показывают блоки без анимации входа — кадр не ловит их на полпути.
  await b.s("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: "reduce" }] });
  if (vp.mobile) {
    await b.s("Emulation.setUserAgentOverride", { userAgent: MOBILE_UA, acceptLanguage: "ru-RU,ru;q=0.9,en;q=0.8", platform: "iPhone" });
    await b.s("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 5 });
  } else {
    // Обычный Chrome вместо «HeadlessChrome» — иначе часть сайтов сразу показывает заглушку.
    const { userAgent } = await b.cdp.send("Browser.getVersion");
    await b.s("Emulation.setUserAgentOverride", { userAgent: String(userAgent).replace("HeadlessChrome", "Chrome"), acceptLanguage: "ru-RU,ru;q=0.9,en;q=0.8" });
  }
  return vp;
}

// «Появление при прокрутке» и ленивые картинки на IntersectionObserver: каждый наблюдаемый элемент один раз сразу
// считается видимым. Иначе блоки ниже первого экрана на кадре всей страницы пустые (анимация входа ещё не
// сыграла или откатилась, когда прокрутка ушла). Один раз на элемент — бесконечная лента не грузится без конца.
const REVEAL_ALL = `(() => { const Native = window.IntersectionObserver; if (!Native) return;
  const fired = new WeakSet();
  window.IntersectionObserver = class { constructor(cb) { this.cb = cb; }
    observe(el) { if (!el || fired.has(el)) return; fired.add(el);
      setTimeout(() => { const r = el.getBoundingClientRect();
        try { this.cb([{ target: el, isIntersecting: true, intersectionRatio: 1, boundingClientRect: r, intersectionRect: r,
          rootBounds: null, time: performance.now() }], this); } catch (e) { /* чужой обработчик */ } }, 30); }
    unobserve() {} disconnect() {} takeRecords() { return []; } };
})();`;

/** Ждать, пока сеть не затихнет на quietMs (или cap): в полёте не больше двух запросов (долгий опрос не ждём). */
async function networkIdle(inflight, quietMs, cap) {
  const end = Date.now() + cap;
  let quietSince = Date.now();
  while (Date.now() < end) {
    if (inflight.size > 2) quietSince = Date.now();
    else if (Date.now() - quietSince >= quietMs) return true;
    await sleep(100);
  }
  return false;
}

const SCROLL_THROUGH = `(async () => { const H = Math.min(document.documentElement.scrollHeight, MAXH);
  for (let y = 0; y < H; y += Math.round(innerHeight * 0.7)) { window.scrollTo(0, y); await new Promise(r => setTimeout(r, 180)); }
  window.scrollTo(0, 0); await new Promise(r => setTimeout(r, 200)); return true; })()`;
const WAIT_IMAGES = `Promise.all([...document.images].filter(i => !i.complete).map(i => new Promise(r => { i.onload = i.onerror = r; setTimeout(r, 4000); })))
  .then(() => document.fonts ? document.fonts.ready : null).then(() => true)`;
const PAGE_SIZE = `(() => ({ w: document.documentElement.scrollWidth, h: Math.max(document.documentElement.scrollHeight, document.body ? document.body.scrollHeight : 0), title: document.title, url: location.href }))()`;

// Подготовка кадра всей страницы (HTML копии уже записан — это только для картинки и карты outline):
// 1) блоки с content-visibility:auto рисуются и вне экрана, анимации (в том числе привязанные к прокрутке,
//    animation-timeline: view()) — сразу в конечном виде, без переходов: на кадре нет полупрозрачных «недоехавших» блоков;
// 2) полосы, прибитые к низу экрана (cookie, «Купить» внизу), на кадре легли бы после первого экрана посреди
//    страницы — ставим их в самый низ, как при прокрутке до конца.
const PIN_BOTTOM_BARS = `(() => {
  const still = document.createElement("style");
  still.id = "vvf-still-frame";
  still.textContent = "*,*::before,*::after{content-visibility:visible!important;animation-timeline:auto!important;" +
    "animation-duration:0s!important;animation-delay:0s!important;animation-iteration-count:1!important;transition:none!important}";
  document.head.appendChild(still);
  const H = Math.max(document.documentElement.scrollHeight, document.body ? document.body.scrollHeight : 0);
  let n = 0;
  for (const el of document.querySelectorAll("body *")) {
    if (getComputedStyle(el).position !== "fixed") continue;
    const r = el.getBoundingClientRect();
    if (r.height <= 0 || r.top < innerHeight / 2) continue;
    el.style.setProperty("position", "absolute", "important");
    el.style.setProperty("top", Math.max(0, H - r.height - Math.max(0, innerHeight - r.bottom)) + "px", "important");
    el.style.setProperty("bottom", "auto", "important");
    n++;
  }
  return n; })()`;

/** Кадры всей страницы полосами (≤ TILE_PX пикселей в высоту): DIR/tile_<i>.png. */
async function captureTiles(b, vp, outDir, maxHeight) {
  await b.evaluate("window.scrollTo(0, 0), true");
  const size = await b.evaluate(PAGE_SIZE);
  const height = Math.max(1, Math.min(size.h, maxHeight));
  const step = Math.floor(TILE_PX / vp.deviceScaleFactor);
  const tiles = [];
  mkdirSync(outDir, { recursive: true });
  for (let y = 0, i = 0; y < height; y += step, i++) {
    const h = Math.min(step, height - y);
    const shot = await b.s("Page.captureScreenshot", {
      format: "png", captureBeyondViewport: true, fromSurface: true, clip: { x: 0, y, width: vp.width, height: h, scale: 1 },
    });
    const file = `tile_${i}.png`;
    writeFileSync(join(outDir, file), Buffer.from(shot.data, "base64"));
    tiles.push({ file, y, h });
  }
  return { tiles, page_height: size.h, shot_height: height, page_width: vp.width, dpr: vp.deviceScaleFactor, title: size.title };
}

function assetName(url, mime) {
  const clean = String(mime || "").split(";")[0].trim().toLowerCase();
  let ext = EXT_BY_MIME[clean];
  if (!ext) {
    const e = extname(new URL(url).pathname).toLowerCase();
    ext = /^\.[a-z0-9]{2,5}$/.test(e) ? e : ".bin";
  }
  return createHash("sha1").update(url).digest("hex").slice(0, 16) + ext;
}

/** url() и @import внутри css → имена файлов той же папки assets (чего нет — пусто). */
export function rewriteCss(css, base, names) {
  const map = (u) => {
    if (!u || u.startsWith("data:") || u.startsWith("#")) return null;
    try {
      return names[new URL(u, base).href.split("#")[0]] || "";
    } catch {
      return "";
    }
  };
  return String(css)
    .replace(/@import\s+(?:url\(\s*)?(['"]?)([^'")\s;]+)\1\s*\)?([^;]*);/g, (m, _q, u, media) => {
      const local = map(u);
      return local ? `@import url("${local}")${media};` : local === null ? m : "";
    })
    .replace(/url\(\s*(['"]?)([^'")]*)\1\s*\)/g, (m, _q, u) => {
      const local = map(u);
      return local === null ? m : local ? `url("${local}")` : 'url("data:,")';
    });
}

async function capture(opts) {
  const root = resolve(String(opts.out));
  const vdir = join(root, "v1");
  const adir = join(root, "assets");
  mkdirSync(vdir, { recursive: true });
  mkdirSync(adir, { recursive: true });
  const b = await openBrowser(false);
  try {
    await b.s("Network.enable", { maxTotalBufferSize: 120 * 1024 * 1024, maxResourceBufferSize: MAX_ASSET_BYTES });
    const vp = await setViewport(b, opts.viewport);
    const inflight = new Set();
    const responses = new Map();
    const bodies = [];
    let mainStatus = 0;
    let total = 0;
    b.cdp.on("Network.requestWillBeSent", (p, sid) => sid === b.sessionId && inflight.add(p.requestId));
    b.cdp.on("Network.loadingFailed", (p, sid) => sid === b.sessionId && inflight.delete(p.requestId));
    b.cdp.on("Network.responseReceived", (p, sid) => {
      if (sid !== b.sessionId) return;
      if (p.type === "Document" && !mainStatus) mainStatus = p.response.status;
      if (KEEP_TYPES.has(p.type) && p.response.status < 400 && /^https?:/.test(p.response.url)) {
        responses.set(p.requestId, { url: p.response.url.split("#")[0], mime: p.response.mimeType, type: p.type });
      }
    });
    b.cdp.on("Network.loadingFinished", (p, sid) => {
      if (sid !== b.sessionId) return;
      inflight.delete(p.requestId);
      const meta = responses.get(p.requestId);
      if (!meta || bodies.length >= MAX_ASSETS || total > MAX_TOTAL_BYTES || p.encodedDataLength > MAX_ASSET_BYTES) return;
      bodies.push(
        b.s("Network.getResponseBody", { requestId: p.requestId })
          .then((r) => {
            const buf = Buffer.from(r.body, r.base64Encoded ? "base64" : "utf8");
            if (buf.length > MAX_ASSET_BYTES || total + buf.length > MAX_TOTAL_BYTES) return null;
            total += buf.length;
            return { ...meta, buf };
          })
          .catch(() => null),
      );
    });
    await b.s("Page.addScriptToEvaluateOnNewDocument", { source: REVEAL_ALL });
    const loaded = b.cdp.once("Page.loadEventFired", b.sessionId);
    const nav = await b.s("Page.navigate", { url: String(opts.capture) });
    if (nav.errorText) return { ok: false, status: "error", reason: `страница не открылась: ${nav.errorText}`, url: opts.capture };
    await Promise.race([loaded, idleTimer(opts.timeout)]);
    debug("load");
    await networkIdle(inflight, 800, 10000);
    debug(`idle, inflight ${inflight.size}`);
    const early = await b.evaluate(`(${detectBlock})()`);
    if (early || [403, 429, 503].includes(mainStatus)) {
      const shot = await b.s("Page.captureScreenshot", { format: "png" });
      writeFileSync(join(vdir, "blocked.png"), Buffer.from(shot.data, "base64"));
      const size = await b.evaluate(PAGE_SIZE);
      return { ok: true, status: "blocked", reason: early || `сайт ответил ${mainStatus}`, url: opts.capture, final_url: size.url, title: size.title, preview: "v1/blocked.png" };
    }
    if (mainStatus >= 400) return { ok: false, status: "error", reason: `сайт ответил ${mainStatus}`, url: opts.capture };
    await b.evaluate(SCROLL_THROUGH.replace("MAXH", String(opts.maxHeight)));
    debug("scrolled");
    await networkIdle(inflight, 800, 8000);
    debug(`idle2, inflight ${inflight.size}`);
    await b.evaluate(WAIT_IMAGES);
    debug("images");
    const got = (await Promise.all(bodies)).filter(Boolean);
    const names = {};
    for (const item of got) names[item.url] = assetName(item.url, item.mime);
    for (const item of got) {
      const name = names[item.url];
      const isCss = name.endsWith(".css") || item.type === "Stylesheet";
      writeFileSync(join(adir, name), isCss ? rewriteCss(item.buf.toString("utf8"), item.url, names) : item.buf);
    }
    const pageMap = Object.fromEntries(Object.entries(names).map(([u, n]) => [u, `../assets/${n}`]));
    const serial = await b.evaluate(`(${serializePage})(${JSON.stringify(pageMap)})`);
    writeFileSync(join(vdir, "index.html"), serial.html);
    debug("serialized");
    await b.evaluate(PIN_BOTTOM_BARS); // после записи HTML: только для кадра и карты
    const outline = await b.evaluate(`(${outlinePage})(80)`);
    const shots = await captureTiles(b, vp, vdir, opts.maxHeight);
    debug("tiles");
    const size = await b.evaluate(PAGE_SIZE);
    return {
      ok: true, status: "ok", url: opts.capture, final_url: size.url, ...shots, outline, assets: got.length,
      assets_bytes: total, viewport: opts.viewport,
    };
  } finally {
    await b.close();
  }
}

/** Отдать файл копии страницы (только из её папки, только чтение). */
function serveLocal(root, url) {
  if (!url.startsWith(LOCAL_ORIGIN + "/")) return null;
  const rel = url.slice(LOCAL_ORIGIN.length + 1).split(/[?#]/)[0];
  const file = safeJoin(root, rel);
  if (!file || !existsSync(file) || !statSync(file).isFile()) return null;
  return { body: readFileSync(file), mime: MIME_BY_EXT[extname(file).toLowerCase()] || "application/octet-stream" };
}

async function render(opts) {
  const root = resolve(String(opts.render));
  const fromRel = `${opts.from}/index.html`;
  if (!safeJoin(root, fromRel) || !existsSync(join(root, fromRel))) throw new UsageError(`нет ${fromRel}`);
  const toDir = safeJoin(root, String(opts.to));
  if (!toDir) throw new UsageError("--to: имя папки версии");
  const ops = opts.ops ? JSON.parse(readFileSync(String(opts.ops), "utf8")) : [];
  const b = await openBrowser(true);
  try {
    b.cdp.on("Fetch.requestPaused", (p, sid) => {
      if (sid !== b.sessionId) return;
      const hit = serveLocal(root, p.request.url);
      if (!hit) {
        b.s("Fetch.failRequest", { requestId: p.requestId, errorReason: "BlockedByClient" }).catch(() => {});
        return;
      }
      b.s("Fetch.fulfillRequest", {
        requestId: p.requestId, responseCode: 200, body: hit.body.toString("base64"),
        responseHeaders: [{ name: "Content-Type", value: hit.mime }, { name: "Access-Control-Allow-Origin", value: "*" }],
      }).catch(() => {});
    });
    await b.s("Fetch.enable", { patterns: [{ urlPattern: "*" }] });
    const vp = await setViewport(b, opts.viewport);
    const loaded = b.cdp.once("Page.loadEventFired", b.sessionId);
    await b.s("Page.navigate", { url: `${LOCAL_ORIGIN}/${fromRel}` });
    await Promise.race([loaded, idleTimer(20000)]);
    await b.evaluate(WAIT_IMAGES);
    const result = ops.length ? await b.evaluate(`(${applyOps})(${JSON.stringify(ops)})`) : { applied: [], region: null };
    mkdirSync(toDir, { recursive: true });
    if (resolve(toDir) !== resolve(join(root, String(opts.from)))) {
      const html = await b.evaluate(`"<!doctype html>\\n" + document.documentElement.outerHTML`);
      writeFileSync(join(toDir, "index.html"), html);
    }
    await sleep(150);
    await b.evaluate(PIN_BOTTOM_BARS); // после записи HTML: только для кадра и карты
    const outline = await b.evaluate(`(${outlinePage})(80)`);
    const shots = await captureTiles(b, vp, toDir, opts.maxHeight);
    return { ok: true, status: "ok", ...shots, outline, applied: result.applied, region: result.region, viewport: opts.viewport };
  } finally {
    await b.close();
  }
}

async function main() {
  let opts;
  try {
    opts = parseArgs(process.argv.slice(2));
  } catch (err) {
    process.stderr.write(`webpage: ${err.message}\n`);
    process.exit(2);
  }
  try {
    const res = opts.capture ? await capture(opts) : await render(opts);
    process.stdout.write(JSON.stringify(res) + "\n");
  } catch (err) {
    process.stderr.write(`webpage: ${err.message}\n`);
    process.exit(err instanceof UsageError ? 2 : 1);
  }
}

if (import.meta.url === pathToFileURL(process.argv[1] || "").href) void main();
