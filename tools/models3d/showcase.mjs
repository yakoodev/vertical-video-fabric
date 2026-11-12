#!/usr/bin/env node
/**
 * «До/после» библиотеки вещей: одна и та же сцена с models.off (процедурные вещи) и с моделями библиотеки.
 * Сцены: стол мем-пилота (spec из --meme, вещи — как в tools/meme3d_scene.py) и окружения-пресеты (cozy_desk, gamer_rgb).
 *
 *   node tools/models3d/showcase.mjs --models D:/vvf-data/runtime/models3d --meme D:/vvf-offload/memes/pilot3/work/scene.json \
 *     --out D:/vvf-offload/tmp/assets3d/before_after.jpg [--seeds 1,2,3] [--gl gpu]
 */
import { spawnSync } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
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
const out = resolve(a.out || "D:/vvf-offload/tmp/assets3d/before_after.jpg");
const seeds = String(a.seeds || "1").split(",");
const work = join(dirname(out), "showcase_work");
mkdirSync(work, { recursive: true });
const TOP = 0.75;

/** Сцена мем-пилота → вещи из библиотеки (то же, что теперь пишет tools/meme3d_scene.py). */
function memeWithModels(spec) {
  const s = structuredClone(spec);
  delete s.overlays;
  // Герой — робот-помощник deskbot (стиль «critter» пилота 3 — в другой ветке движка): для снимка вещей стола хватит.
  s.characters = (s.characters || []).map((c) => (c.style === "deskbot" || c.style === "voxel" || c.model ? c
    : { id: c.id, style: "deskbot", height: 0.34, position: c.position, yaw: c.yaw ?? -8, seed: c.seed ?? 5,
      deskbot: { body: "#d9774b", trim: "#a9512f", plate: "#f6e6cf", screen: "#2a1f2d", glow: "#fff1dc", accent: "#ffc857" } }));
  const books = s.objects.filter((o) => /^book\d$/.test(o.id));
  s.objects = s.objects.filter((o) => !/^book\d$/.test(o.id)).map((o) => {
    if (o.id === "keyboard") return { ...o, model: "keyboard", fitMax: [0.4, 0.05, 0.16] };
    if (o.id === "mug") return { ...o, model: "mug", fitMax: [0.12, 0.1, 0.12] };
    if (o.id === "plant") return { ...o, model: "plant", modelTags: ["desk"], fitMax: [0.26, 0.3, 0.26] };
    if (o.id === "lamp") return { ...o, model: "lamp", fitMax: [0, 0.45, 0] };
    return o;
  });
  if (books.length) s.objects.push({ id: "books", model: "books", modelTags: ["shelf"], fitMax: [0.3, 0.22, 0.14], position: [-0.6, TOP + 0.43, -0.52], fallback: books });
  return s;
}

function roomScene(room, light, cam) {
  return { width: 1080, height: 1920, fps: 30, duration: 2, presets: { room, light, look: "toon_ink" },
    camera: { fov: 40, keys: [{ t: 0, position: cam.pos, target: cam.target }] } };
}

const shots = [];
if (a.meme) {
  const meme = memeWithModels(JSON.parse(readFileSync(resolve(a.meme), "utf-8")));
  // Ближе к столу, чем общий план ролика: клавиатура, кружка, лампа, растение, книги на полке.
  meme.camera = { ...meme.camera, fov: 50, keys: [{ t: 0, position: [-0.3, 1.08, 0.9], target: [0.2, 0.86, -0.12] }], keepInFrame: undefined };
  shots.push({ name: "мем-стол", spec: meme, t: Number(a.t || 1.8) });
}
shots.push({ name: "cozy_desk", spec: roomScene("cozy_desk", "golden_hour", { pos: [0.3, 1.8, 0.45], target: [0.0, 0.76, -0.45] }), t: 0.5 });
shots.push({ name: "gamer_rgb", spec: roomScene("gamer_rgb", "monitor_glow", { pos: [0.55, 1.45, -0.35], target: [0.0, 0.85, -1.72] }), t: 0.5 });

function render(spec, t, tag) {
  const p = join(work, `${tag}.json`);
  writeFileSync(p, JSON.stringify(spec));
  const dir = join(work, tag);
  const res = spawnSync(process.execPath, [join(ROOT, "node", "scene3d", "render.mjs"), "--spec", p, "--models", models, "--preview", String(t),
    "--preview-dir", dir, "--gl", a.gl || "gpu", "--post", "full", "--packs", join(ROOT, "vendor", "packs")], { encoding: "utf-8", maxBuffer: 64 * 1024 * 1024 });
  const rep = JSON.parse((res.stdout || "").trim().split("\n").pop() || "{}");
  if (!rep.ok) throw new Error(`${tag}: ${res.stdout} ${res.stderr}`);
  const w = (rep.warnings || []).filter((x) => !/camera_/.test(x));
  if (w.length) console.log(tag, "warnings:", w.join(" | "));
  return rep.previews[0].file;
}

const rows = [];
for (const s of shots) {
  const before = render({ ...s.spec, models: { off: true } }, s.t, `${s.name}_before`);
  const after = seeds.map((seed) => render({ ...s.spec, models: { seed } }, s.t, `${s.name}_after_${seed}`));
  rows.push({ name: s.name, files: [before, ...after] });
}
const W = 360, H = 640, cols = 1 + seeds.length;
const c = createCanvas(cols * W, rows.length * (H + 30));
const g = c.getContext("2d");
g.fillStyle = "#151419";
g.fillRect(0, 0, c.width, c.height);
for (let r = 0; r < rows.length; r++) {
  for (let i = 0; i < rows[r].files.length; i++) {
    const img = await loadImage(readFileSync(rows[r].files[i]));
    g.drawImage(img, i * W, r * (H + 30) + 30, W, H);
    g.fillStyle = "#fff";
    g.font = "bold 17px sans-serif";
    g.fillText(`${rows[r].name} — ${i === 0 ? "было (процедурные)" : `стало, seed ${seeds[i - 1]}`}`, i * W + 8, r * (H + 30) + 21);
  }
}
writeFileSync(out, await c.encode("jpeg", 88));
console.log(out);
