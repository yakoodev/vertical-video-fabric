#!/usr/bin/env node
/**
 * Сравнение рендеров: те же кадры пробного 3D-кадра, что рисует Blender, — в three.js (рендерер движка 3D-серий).
 *
 *   npx esbuild node/scene3d/src/glbview.ts --bundle --format=iife --platform=browser --target=chrome110 \
 *       --outfile=node/scene3d/dist/glbview.js
 *   node node/scene3d/compare_glb.mjs --dir <папка с f_XXXX.glb> --out <папка png> [--gl gpu]
 *
 * Кадры GLB выгружает tools/blender/hero_shot.py (config export_frames/export_dir): поза применена к мешам,
 * камера и источники — из Blender. Пишет f_XXXX_three.png и timing.json (мс на кадр, загрузка).
 */
import { mkdirSync, readdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { openScene } from "./render.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const args = Object.fromEntries(process.argv.slice(2).reduce((acc, a, i, all) => {
  if (a.startsWith("--")) acc.push([a.slice(2), all[i + 1] && !all[i + 1].startsWith("--") ? all[i + 1] : "1"]);
  return acc;
}, []));
const dir = resolve(args.dir);
const out = resolve(args.out || dir);
mkdirSync(out, { recursive: true });
const files = readdirSync(dir).filter((f) => /^f_\d+\.glb$/.test(f)).sort();
const POST = {
  ao: 1.0, aoRadius: 0.3, aoScale: 0.5, bloom: { strength: 0.45, radius: 0.5, threshold: 1.4 },
  grade: { lift: [0.012, 0.02, 0.025], gamma: [0.98, 1.0, 1.02], gain: [1.02, 1.0, 0.97], shadows: "#3d5a82",
    highlights: "#d0ab7c", split: 0.2, contrast: 1.1, saturation: 1.0 },
  vignette: 0.5, grain: 0.045, ca: 1.4, dof: false,
};
const spec = { width: 1080, height: 1920, frames: files.map((f) => `http://scene3d.local/packs/${f}`), post: POST,
  exposure: Number(args.exposure || 1), beam: { light: "phone_flash", length: 1.7 } };
const t0 = Date.now();
const sc = await openScene({ bundle: join(HERE, "dist", "glbview.js"), packs: dir, gl: args.gl || "gpu", chrome: process.env.SCENE3D_CHROMIUM }, spec);
const loadMs = Date.now() - t0;
const timing = { load_ms: loadMs, frames: [] };
for (let i = 0; i < files.length; i++) {
  await sc.render(i); // прогрев шейдеров/теней
  const r = await sc.render(i);
  const shot = await sc.shot("png");
  const name = files[i].replace(".glb", "_three.png");
  writeFileSync(join(out, name), shot);
  timing.frames.push({ file: files[i], render_ms: Math.round(r.ms * 10) / 10 });
}
timing.warnings = await sc.warnings();
await sc.close();
writeFileSync(join(out, "timing.json"), JSON.stringify(timing, null, 1));
console.log(JSON.stringify(timing));
