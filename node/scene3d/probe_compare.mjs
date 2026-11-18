#!/usr/bin/env node
/**
 * Пробник «Blender против three.js»: кадры серии, выгруженные tools/probe3d_blender.py (f_XXXX.glb + f_XXXX.json),
 * рисуются в headless Chromium двумя способами — растр движка с улучшенным светом и трассировка путей
 * (three-gpu-pathtracer). Пишет <out>/f_XXXX_<режим>.png и timing.json (мс на кадр, загрузка, BVH, сэмплы).
 *
 *   npx esbuild node/scene3d/src/probeview.ts --bundle --format=iife --platform=browser --target=chrome110 \
 *       --outfile=node/scene3d/dist/probeview.js
 *   node node/scene3d/probe_compare.mjs --dir <export> --out <папка> [--modes raster,pathtrace] [--samples 512]
 *       [--gl gpu] [--exposure 1] [--light 1]
 *
 * Один браузер на кадр и режим (закрывается сразу).
 */
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
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
const only = args.frames ? new Set(String(args.frames).split(",").map(Number)) : null;
const files = readdirSync(dir).filter((f) => /^f_\d+\.glb$/.test(f)).sort()
  .filter((f) => !only || only.has(Number(f.slice(2, 6))));
const modes = String(args.modes || "raster,pathtrace").split(",");
const tag = args.tag ? `_${args.tag}` : "";
// вид Blender (AgX + Medium High Contrast) 3D-таблицей из tools/blender/bake_view_lut.py; нет — AgX three.js + контраст
const lut = args.lut === "0" ? null : existsSync(join(dir, args.lut || "agx_mhc.bin")) ? `http://scene3d.local/packs/${args.lut || "agx_mhc.bin"}` : null;
// Пост ближе к компоновке Blender серии: свечение порог 1.2 / слабое, виньетка ~0.38, лёгкое зерно, AgX + контраст
// («Medium High Contrast»), без раздельного тонирования (у Blender его нет).
const POST = {
  ao: 0.9, aoRadius: 0.25, aoScale: 0.5, bloom: { strength: 0.32, radius: 0.55, threshold: 1.1 },
  grade: { contrast: Number(args.contrast || (lut ? 1 : 1.18)), saturation: Number(args.saturation || (lut ? 1 : 1.05)) },
  vignette: 0.42, grain: Number(args.grain || 0.03), ca: 0.6, dof: true,
};
const timing = { frames: [] };
for (const f of files) {
  const meta = JSON.parse(readFileSync(join(dir, f.replace(".glb", ".json")), "utf-8"));
  for (const mode of modes) {
    const spec = {
      glb: `http://scene3d.local/packs/${f}`, meta, mode, width: Number(args.width || 1080), height: Number(args.height || 1920),
      exposure: Number(args.exposure || 1), lightScale: Number(args.light || 1), envIntensity: Number(args.env || 1),
      post: POST, haze: Number(args.haze ?? 1), samples: Number(args.samples || 512), bounces: Number(args.bounces || 6),
      denoise: args.denoise === "1", dofScale: Number(args.dof || 1), lut, brushed: args.brushed !== "0", ptHaze: args.pthaze || "analytic",
      lightMul: args.lightmul ? Object.fromEntries(String(args.lightmul).split(",").map((kv) => [kv.split("=")[0], Number(kv.split("=")[1])])) : undefined,
    };
    const t0 = Date.now();
    const sc = await openScene({ bundle: join(HERE, "dist", "probeview.js"), packs: dir, gl: args.gl || "gpu", chrome: process.env.SCENE3D_CHROMIUM }, spec);
    const loadMs = Date.now() - t0;
    let r;
    if (mode === "raster") {
      await sc.render(0); // прогрев шейдеров и теней
      r = await sc.render(0);
      const r2 = await sc.render(1);
      r.ms = Math.min(r.ms, r2.ms);
    } else {
      r = await sc.render(0);
    }
    const png = await sc.shot("png");
    const name = f.replace(".glb", `_${mode}${tag}.png`);
    writeFileSync(join(out, name), png);
    const warn = await sc.warnings();
    const cons = sc.console.filter((l) => /error|warn/i.test(l)).slice(-5);
    await sc.close();
    const row = { file: f, mode, tag: args.tag || "", load_ms: loadMs, render_ms: Math.round(r.ms), stats: r.stats, warnings: warn, console: cons };
    timing.frames.push(row);
    console.log(JSON.stringify(row));
  }
}
writeFileSync(join(out, `timing${tag}.json`), JSON.stringify(timing, null, 1));
