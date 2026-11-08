#!/usr/bin/env node
/**
 * Скриншоты сайта владельца (vtubika.store) для вставки «покупка на телефоне»: headless Chromium по CDP,
 * мобильный экран (390×844, DPR 3, тач, мобильный UA). ТОЛЬКО ПРОСМОТР: обычная загрузка страницы (GET) и
 * прокрутка. Никаких кликов, форм, входа, кнопок «Купить»/«В корзину»/«Оплатить», согласий с баннерами.
 *
 *   node node/vrm/site_shots.mjs --probe URL                     — заголовок, ссылки и баннеры страницы (JSON)
 *   node node/vrm/site_shots.mjs --shot URL --out FILE.png [--max-screens 4] [--find "Оплатить"]
 *       кадр всей страницы (до N экранов в высоту, PNG 1170 px шириной) + JSON: высота страницы, где на ней
 *       текст --find (координаты в CSS px; для анимации прокрутки «к цене и кнопке»), цвета и шрифт страницы.
 *
 * Без зависимостей: обвязка CDP — из node/scene3d/render.mjs.
 */
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { Cdp, findChrome } from "../scene3d/render.mjs";

const VIEW = { width: 390, height: 844, deviceScaleFactor: 3, mobile: true };
const UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1";

function args(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i].replace(/^--/, "").replace(/-([a-z])/g, (_, c) => c.toUpperCase());
    out[k] = argv[i + 1] && !argv[i + 1].startsWith("--") ? argv[++i] : true;
  }
  return out;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function open() {
  const work = mkdtempSync(join(tmpdir(), "vvf-site-"));
  const chrome = spawn(findChrome(process.env.SCENE3D_CHROMIUM), [
    "--headless=new", "--remote-debugging-pipe", `--user-data-dir=${join(work, "profile")}`, "--no-first-run",
    "--no-default-browser-check", "--disable-extensions", "--disable-sync", "--hide-scrollbars", "--mute-audio",
    "--force-color-profile=srgb", "about:blank",
  ], { stdio: ["ignore", "ignore", "pipe", "pipe", "pipe"], windowsHide: true });
  const cdp = new Cdp(chrome);
  const { targetId } = await cdp.send("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await cdp.send("Target.attachToTarget", { targetId, flatten: true });
  const s = (m, p) => cdp.send(m, p, sessionId);
  await s("Page.enable");
  await s("Runtime.enable");
  await s("Emulation.setDeviceMetricsOverride", VIEW);
  await s("Emulation.setUserAgentOverride", { userAgent: UA, acceptLanguage: "ru-RU,ru", platform: "iPhone" });
  await s("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 5 });
  const evaluate = async (expression) => {
    const r = await s("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
    return r.result?.value;
  };
  const close = async () => {
    try {
      await cdp.send("Browser.close");
    } catch {
      /* закрыт */
    }
    if (chrome.exitCode === null) chrome.kill();
    await new Promise((r) => (chrome.exitCode !== null ? r() : chrome.once("exit", r)));
    try {
      rmSync(work, { recursive: true, force: true, maxRetries: 3, retryDelay: 200 });
    } catch {
      /* профиль отпускается не сразу */
    }
  };
  return { s, cdp, sessionId, evaluate, close };
}

/** Обычная загрузка страницы (GET) и ожидание сети/шрифтов/картинок. */
async function load(b, url) {
  const loaded = b.cdp.once("Page.loadEventFired", b.sessionId);
  await b.s("Page.navigate", { url });
  await Promise.race([loaded, sleep(30000)]);
  await b.evaluate("document.fonts ? document.fonts.ready.then(() => true) : true");
  // Ленивые картинки: прокрутка вниз и обратно (только прокрутка — без кликов).
  await b.evaluate(`(async () => { const H = document.documentElement.scrollHeight;
    for (let y = 0; y < Math.min(H, 9000); y += 600) { window.scrollTo(0, y); await new Promise(r => setTimeout(r, 120)); }
    window.scrollTo(0, 0); return true; })()`);
  await b.evaluate(`Promise.all([...document.images].filter(i => !i.complete).map(i => new Promise(r => { i.onload = i.onerror = r; setTimeout(r, 6000); }))).then(() => true)`);
  await sleep(1200);
}

const PROBE = `(() => {
  const vis = (e) => { const r = e.getBoundingClientRect(); const cs = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && cs.visibility !== "hidden" && cs.display !== "none"; };
  const links = [...document.querySelectorAll("a[href]")].map(a => ({ text: (a.innerText || a.getAttribute("aria-label") || "").trim().replace(/\\s+/g, " ").slice(0, 90), href: a.href }))
    .filter(l => l.href.startsWith(location.origin));
  const fixed = [...document.querySelectorAll("body *")].filter(e => { const cs = getComputedStyle(e);
    return (cs.position === "fixed" || cs.position === "sticky") && vis(e); })
    .map(e => { const r = e.getBoundingClientRect(); return { tag: e.tagName, cls: String(e.className).slice(0, 80),
      text: (e.innerText || "").trim().replace(/\\s+/g, " ").slice(0, 120), box: [r.x, r.y, r.width, r.height].map(Math.round) }; });
  return { url: location.href, title: document.title, height: document.documentElement.scrollHeight, links, fixed };
})()`;

/** Цвета и шрифт страницы (для своих экранов «Оплачено» / «Модель пришла» в стиле сайта). */
const STYLE = `(() => {
  const cs = (e) => e && getComputedStyle(e);
  const body = cs(document.body);
  const btn = [...document.querySelectorAll("button, a")].find(e => /оплат|купить|корзин/i.test(e.innerText || ""));
  const h = document.querySelector("h1") || document.querySelector("h2");
  const pick = (e) => e ? { color: cs(e).color, bg: cs(e).backgroundColor, font: cs(e).fontFamily, weight: cs(e).fontWeight,
    radius: cs(e).borderRadius, size: cs(e).fontSize } : null;
  return { body: pick(document.body), heading: pick(h), button: pick(btn), buttonText: btn ? btn.innerText.trim() : null };
})()`;

function findExpr(text) {
  return `(() => { const want = ${JSON.stringify(String(text || ""))}.toLowerCase(); if (!want) return null;
    const all = [...document.querySelectorAll("body *")].filter(e => e.children.length === 0 || e.tagName === "BUTTON");
    const hits = all.filter(e => (e.innerText || "").toLowerCase().includes(want)).map(e => { const r = e.getBoundingClientRect();
      return { tag: e.tagName, text: (e.innerText || "").trim().slice(0, 80), box: [r.x, r.y + scrollY, r.width, r.height].map(Math.round) }; })
      .filter(h => h.box[2] > 0 && h.box[3] > 0);
    return hits.slice(0, 8); })()`;
}

async function main() {
  const a = args(process.argv.slice(2));
  const b = await open();
  try {
    if (a.probe) {
      await load(b, a.probe);
      process.stdout.write(JSON.stringify(await b.evaluate(PROBE), null, 1) + "\n");
      return;
    }
    if (!a.shot || !a.out) throw new Error("нужно --probe URL или --shot URL --out FILE.png");
    await load(b, a.shot);
    const info = await b.evaluate(PROBE);
    const maxH = VIEW.height * Number(a.maxScreens || 4);
    const h = Math.min(info.height, maxH);
    await b.s("Emulation.setDeviceMetricsOverride", { ...VIEW, height: h });
    await sleep(800);
    await b.evaluate("window.scrollTo(0, 0), true");
    const shot = await b.s("Page.captureScreenshot", { format: "png", captureBeyondViewport: true,
      clip: { x: 0, y: 0, width: VIEW.width, height: h, scale: 1 } });
    await b.s("Emulation.setDeviceMetricsOverride", VIEW);
    const out = resolve(String(a.out));
    mkdirSync(dirname(out), { recursive: true });
    writeFileSync(out, Buffer.from(shot.data, "base64"));
    const meta = { url: info.url, title: info.title, page_height: info.height, shot_height: h, width: VIEW.width,
      dpr: VIEW.deviceScaleFactor, viewport: VIEW.height, fixed: info.fixed, style: await b.evaluate(STYLE),
      find: a.find ? await b.evaluate(findExpr(a.find)) : null, file: out };
    process.stdout.write(JSON.stringify(meta, null, 1) + "\n");
  } finally {
    await b.close();
  }
}

main().catch((e) => {
  process.stderr.write(`site_shots: ${e.message}\n`);
  process.exit(1);
});
