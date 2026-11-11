#!/usr/bin/env node
/**
 * Разбор копии страницы на слои (app/webpage_layers.py): каждый осмысленный блок — своя прозрачная PNG на своём
 * месте страницы, плюс «фон страницы» (всё, кроме блоков) и полный кадр области (для сверки).
 *
 *   node node/webpage/layers.mjs --root DIR --from v1 --out DIR/v1/layers/<key> [--viewport mobile|desktop]
 *        [--region '{"x":0,"y":0,"w":390,"h":844}' | --selector CSS] [--max-layers 40] [--max-height 6000] [--focus-cards]
 *
 * Копия открывается без сети (файлы — из DIR через CDP Fetch), тем же экраном и DPR, что при снимке. Блок
 * снимается так: всё на странице visibility:hidden, кроме блока и его содержимого (дочерние блоки тоже скрыты —
 * они свои слои), фон окна прозрачный. Свои фон, скругление и тень блока остаются, предки не рисуются.
 *
 * stdout — одна строка JSON: {ok, dpr, page_width, page_height, region, blocks: [{..., clip, tiles}],
 * plate: {clip, tiles}, full: {clip, tiles}, font_faces}. Полосы кадров (≤ TILE_PX пикселей в высоту) склеивает
 * Python. Браузер один и закрывается в finally.
 */
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { safeJoin } from "../scene3d/render.mjs";
import { focusBlock, freezeFixed, isolateStyle, pickBlocks, plateStyle, dropStyle } from "./layer-scripts.mjs";
import { LOCAL_ORIGIN, PIN_BOTTOM_BARS, TILE_PX, VIEWPORTS, WAIT_IMAGES, openBrowser, serveLocal, setViewport } from "./snapshot.mjs";

class UsageError extends Error {}
const idleTimer = (ms) => new Promise((r) => setTimeout(r, ms).unref());
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i++) {
    const tok = argv[i];
    if (!tok.startsWith("--")) throw new UsageError(`лишний аргумент ${tok}`);
    const key = tok.slice(2).replace(/-([a-z])/g, (_, c) => c.toUpperCase());
    const next = argv[i + 1];
    out[key] = next !== undefined && !next.startsWith("--") ? argv[++i] : true;
  }
  if (!out.root || !out.from || !out.out) throw new UsageError("нужно --root DIR --from v1 --out DIR");
  out.viewport = VIEWPORTS[out.viewport] ? out.viewport : "mobile";
  out.maxLayers = Math.max(1, Math.min(80, Number(out.maxLayers) || 40));
  out.maxHeight = Math.max(800, Math.min(20000, Number(out.maxHeight) || 6000));
  if (out.region && out.region !== true) {
    const r = JSON.parse(String(out.region));
    out.region = { x: Number(r.x) || 0, y: Number(r.y) || 0, w: Number(r.w) || 0, h: Number(r.h) || 0 };
    if (out.region.w <= 0 || out.region.h <= 0) throw new UsageError("--region: нужны w и h > 0");
  } else out.region = null;
  out.selector = out.selector && out.selector !== true ? String(out.selector) : null;
  return out;
}

/** Кадр прямоугольника страницы (CSS px) полосами: <name>_<k>.png; вернуть [{file, y, h}] (y, h — в CSS px от clip.y). */
async function shoot(b, clip, dpr, outDir, name) {
  const step = Math.max(1, Math.floor(TILE_PX / dpr));
  const tiles = [];
  for (let y = 0, k = 0; y < clip.h; y += step, k++) {
    const h = Math.min(step, clip.h - y);
    const shot = await b.s("Page.captureScreenshot", {
      format: "png", captureBeyondViewport: true, fromSurface: true,
      clip: { x: clip.x, y: clip.y + y, width: clip.w, height: h, scale: 1 },
    });
    const file = `${name}_${k}.png`;
    writeFileSync(join(outDir, file), Buffer.from(shot.data, "base64"));
    tiles.push({ file, y, h });
  }
  return tiles;
}

/** Прямоугольник ∩ область, края — по целым CSS px (кадры полос стыкуются без щелей). */
function clipTo(box, pad, region) {
  const x0 = Math.max(region.x, Math.floor(box[0] - pad));
  const y0 = Math.max(region.y, Math.floor(box[1] - pad));
  const x1 = Math.min(region.x + region.w, Math.ceil(box[0] + box[2] + pad));
  const y1 = Math.min(region.y + region.h, Math.ceil(box[1] + box[3] + pad));
  return x1 > x0 && y1 > y0 ? { x: x0, y: y0, w: x1 - x0, h: y1 - y0 } : null;
}

async function decompose(opts) {
  const root = resolve(String(opts.root));
  const fromRel = `${opts.from}/index.html`;
  if (!safeJoin(root, fromRel) || !existsSync(join(root, fromRel))) throw new UsageError(`нет ${fromRel}`);
  const outDir = resolve(String(opts.out));
  mkdirSync(outDir, { recursive: true });
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
    const dpr = vp.deviceScaleFactor;
    const loaded = b.cdp.once("Page.loadEventFired", b.sessionId);
    await b.s("Page.navigate", { url: `${LOCAL_ORIGIN}/${fromRel}` });
    await Promise.race([loaded, idleTimer(20000)]);
    await b.evaluate(WAIT_IMAGES);
    await sleep(150);
    // Кадр как у снимка: полосы низа экрана — в самый низ, анимации — в конечном виде; плюс верх прибит.
    await b.evaluate(PIN_BOTTOM_BARS);
    await b.evaluate(`(${freezeFixed})()`);
    const picked = await b.evaluate(`(${pickBlocks})(${JSON.stringify({
      region: opts.region, selector: opts.selector, maxLayers: opts.maxLayers, maxHeight: opts.maxHeight,
      focusCards: Boolean(opts.focusCards || opts.region || opts.selector),
    })})`);
    if (picked.problem) return { ok: false, reason: picked.problem };
    const region = picked.region;
    await b.evaluate("window.scrollTo(0, 0), true");
    // 1) Полный кадр области (для сверки) и 2) фон без блоков — с обычным белым фоном окна, как снимок.
    const full = { clip: region, tiles: await shoot(b, region, dpr, outDir, "full") };
    const top = picked.blocks.filter((x) => x.parent < 0).map((x) => x.i);
    await b.evaluate(`(${plateStyle})(${JSON.stringify(top)})`);
    const plate = { clip: region, tiles: await shoot(b, region, dpr, outDir, "plate") };
    await b.evaluate(`(${dropStyle})("vvf-plate")`);
    // 3) Блоки: прозрачное окно, видно только блок без своих дочерних блоков.
    await b.s("Emulation.setDefaultBackgroundColorOverride", { color: { r: 0, g: 0, b: 0, a: 0 } });
    await b.evaluate(`(${isolateStyle})()`);
    const kids = new Map();
    for (const x of picked.blocks) if (x.parent >= 0) kids.set(x.parent, [...(kids.get(x.parent) || []), x.i]);
    const blocks = [];
    for (const blk of picked.blocks) {
      const clip = clipTo(blk.box, blk.pad, region);
      if (!clip) continue;
      const ok = await b.evaluate(`(${focusBlock})(${JSON.stringify({ i: blk.i, children: kids.get(blk.i) || [] })})`);
      if (!ok) continue;
      blocks.push({ ...blk, clip, tiles: await shoot(b, clip, dpr, outDir, `b${blk.i}`) });
    }
    return {
      ok: true, dpr, viewport: opts.viewport, page_width: picked.page_width, page_height: picked.page_height, region,
      blocks, plate, full, font_faces: picked.font_faces,
    };
  } finally {
    await b.close();
  }
}

async function main() {
  let opts;
  try {
    opts = parseArgs(process.argv.slice(2));
  } catch (err) {
    process.stderr.write(`webpage-layers: ${err.message}\n`);
    process.exit(2);
  }
  try {
    process.stdout.write(JSON.stringify(await decompose(opts)) + "\n");
  } catch (err) {
    process.stderr.write(`webpage-layers: ${err.message}\n`);
    process.exit(err instanceof UsageError ? 2 : 1);
  }
}

if (import.meta.url === pathToFileURL(process.argv[1] || "").href) void main();
