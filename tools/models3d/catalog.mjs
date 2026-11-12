#!/usr/bin/env node
/**
 * Лист-каталог библиотеки вещей: каждая модель — свой кадр (вид 3/4 спереди-справа, toon «ink», как в мемах),
 * подписи — id и габарит. Один браузер на все кадры (превью render.mjs по t = номер модели).
 *
 *   node tools/models3d/catalog.mjs --models D:/vvf-data/runtime/models3d --out D:/vvf-offload/tmp/assets3d/catalog.jpg
 *     [--only keyboard/,mug/] [--cols 8] [--cell 320] [--front] [--gl gpu]
 * --front — строго спереди (проверка, что перёд модели смотрит на +z).
 */
import { spawnSync } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync, existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createCanvas, loadImage } from "@napi-rs/canvas";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, "..", "..");
const a = {};
for (let i = 2; i < process.argv.length; i++) {
  const k = process.argv[i].replace(/^--/, "");
  const v = process.argv[i + 1];
  if (v === undefined || v.startsWith("--")) a[k] = true;
  else { a[k] = v; i++; }
}
const models = resolve(a.models || "D:/vvf-data/runtime/models3d");
const out = resolve(a.out || "D:/vvf-offload/tmp/assets3d/catalog.jpg");
const cols = Number(a.cols || 8), cell = Number(a.cell || 320);
const only = a.only ? String(a.only).split(",") : null;
const man = JSON.parse(readFileSync(join(models, "manifest.json"), "utf-8"));
const list = Object.values(man.models).filter((m) => !only || only.some((p) => m.id === p || (p.endsWith("/") && m.id.startsWith(p))));
const work = join(dirname(out), "catalog_work");
mkdirSync(work, { recursive: true });

const SPACING = 8;
const objects = [{ id: "floor", primitive: "box", params: { size: [SPACING * cols + 10, 0.5, SPACING * Math.ceil(list.length / cols) + 10], color: "#d9c7ae", roughness: 0.9 },
  position: [SPACING * (cols - 1) / 2, -0.5, SPACING * (Math.ceil(list.length / cols) - 1) / 2] }];
const keys = [];
list.forEach((m, i) => {
  const x = (i % cols) * SPACING, z = Math.floor(i / cols) * SPACING;
  objects.push({ id: `m${i}`, model: m.id, position: [x, 0, z] });
  const [w, h, d] = m.size;
  const r = Math.max(w, h, d);
  const dist = r * 2.3 + 0.12;
  const dir = a.front ? [0, 0.25, 1] : [0.62, 0.48, 1];
  const l = Math.hypot(...dir);
  const tgt = [x, h * 0.48, z];
  const pos = [x + (dir[0] / l) * dist, tgt[1] + (dir[1] / l) * dist, z + (dir[2] / l) * dist];
  keys.push({ t: i, position: pos, target: tgt }, { t: i + 0.98, position: pos, target: tgt });
});
const spec = {
  width: cell, height: cell, fps: 30, duration: list.length + 1,
  environment: { background: "#9fb7c9", fog: false, exposure: 0.95, envIntensity: 0, toneMapping: "neutral",
    toon: { preset: "ink", outline: { width: 6.0, ref: 1.9 } }, post: { ao: 0, bloom: false, vignette: 0.1, grain: 0, ca: 0 } },
  lights: [
    { type: "ambient", color: "#c79aa8", intensity: 0.75 },
    { type: "hemisphere", color: "#ffe6c8", ground: "#8a5a72", intensity: 0.3 },
    { type: "directional", id: "key", color: "#fff0d8", intensity: 2.3, position: [30, 40, 50], target: [SPACING * cols / 2, 0, SPACING * 3], castShadow: true,
      shadow: { extent: SPACING * cols, size: 4096, bias: -0.003, normalBias: 0.08, near: 1, far: 200 } },
  ],
  objects,
  camera: { fov: 30, near: 0.05, far: 80, keys },
};
const specPath = join(work, "catalog_scene.json");
writeFileSync(specPath, JSON.stringify(spec));
const times = list.map((_, i) => i + 0.5);
const res = spawnSync(process.execPath, [join(ROOT, "node", "scene3d", "render.mjs"), "--spec", specPath, "--models", models,
  "--preview", times.join(","), "--preview-dir", work, "--gl", a.gl || "gpu", "--post", "full"], { encoding: "utf-8", maxBuffer: 64 * 1024 * 1024 });
const last = (res.stdout || "").trim().split("\n").pop();
let rep;
try { rep = JSON.parse(last); } catch { console.error(res.stdout, res.stderr); process.exit(1); }
if (!rep.ok) { console.error(rep); process.exit(1); }
if (rep.warnings?.length) console.log("warnings:", rep.warnings.join(" | "));
const rows = Math.ceil(list.length / cols);
const c = createCanvas(cols * cell, rows * (cell + 34));
const g = c.getContext("2d");
g.fillStyle = "#1d1b22";
g.fillRect(0, 0, c.width, c.height);
for (let i = 0; i < list.length; i++) {
  const img = await loadImage(readFileSync(rep.previews[i].file));
  const x = (i % cols) * cell, y = Math.floor(i / cols) * (cell + 34);
  g.drawImage(img, x, y, cell, cell);
  const m = list[i];
  g.fillStyle = "#ffffff";
  g.font = "bold 15px sans-serif";
  g.fillText(m.id, x + 6, y + cell + 15);
  g.fillStyle = "#b9b4c4";
  g.font = "12px sans-serif";
  g.fillText(`${m.size.map((v) => v.toFixed(2)).join("×")} м · ${m.license === "CC0-1.0" ? "CC0" : "CC BY"} · ${m.tris} tris`, x + 6, y + cell + 30);
}
mkdirSync(dirname(out), { recursive: true });
writeFileSync(out, await c.encode("jpeg", 88));
console.log(out, list.length);
