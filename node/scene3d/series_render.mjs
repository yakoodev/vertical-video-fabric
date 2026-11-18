#!/usr/bin/env node
/**
 * Кадры 3D-серии в three.js по позам из Blender (``render_backend: threejs``, app/series3d_blender.py): один headless
 * Chromium на GPU (та же обвязка CDP, что у render.mjs), бандл dist/seriesview.js, поза — каталог выгрузки
 * tools/blender/series_export.py (scene.glb, scene.json, frames.bin, view_lut.bin).
 *
 *   node node/scene3d/series_render.mjs --plan plan.json
 *
 * plan.json: {dir, cache, width, height, quality, gl, spec: {...seriesview Spec без base}, timing,
 *             chunks: [{key, n, f0, f1, fx: {volume, blur, dof}, todo: [кадры серии], ext: "jpg"}]}
 * Кадр пишется в <cache>/<key>/f_<кадр 5 цифр>.jpg (сначала .part, потом переименование — оборванный кадр не
 * выглядит готовым). stdout: «PROGRESS n total shot=… frame=…», в конце строка JSON с временем.
 * Почти чёрные кадры (яркость < 2 из 255) — в отчёте ``black`` (в блэкауте темно, но не чёрно); код выхода 1 — ошибка.
 */
import { mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { openScene } from "./render.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));

export const BLACK = 2;

export function frameFile(cache, key, f, ext = "jpg") {
  return join(cache, key, `f_${String(f).padStart(5, "0")}.${ext}`);
}

export async function renderPlan(plan, log = (m) => process.stderr.write(`${m}\n`)) {
  const t0 = Date.now();
  const total = plan.chunks.reduce((s, c) => s + (c.todo?.length || 0), 0);
  const spec = { ...(plan.spec || {}), base: "http://scene3d.local/packs/", width: plan.width || 1080, height: plan.height || 1920 };
  const sc = await openScene({ bundle: plan.bundle || join(HERE, "dist", "seriesview.js"), packs: resolve(plan.dir), gl: plan.gl || "gpu",
    chrome: plan.chrome || process.env.SCENE3D_CHROMIUM }, spec, log);
  const timing = { load_ms: Date.now() - t0, info: sc.info, chunks: [], frames: 0, render_ms: 0, shot_ms: 0, black: [] };
  let done = 0;
  process.stdout.write(`PROGRESS 0 ${total}\n`);
  try {
    for (const c of plan.chunks) {
      if (!c.todo?.length) continue;
      const tc = Date.now();
      const info = await sc.evaluate(`window.__scene3d.setChunk(${JSON.stringify({ key: c.key, f0: c.f0, fx: c.fx })})`);
      mkdirSync(join(plan.cache, c.key), { recursive: true });
      const row = { key: c.key, n: c.n, frames: c.todo.length, ...info, render_ms: 0, shot_ms: 0, steps: 0 };
      for (const f of [...c.todo].sort((a, b) => a - b)) {
        const r = await sc.evaluate(`window.__scene3d.render(${Number(f)})`);
        const ts = Date.now();
        const buf = await sc.shot("jpeg", plan.quality || 95);
        row.shot_ms += Date.now() - ts;
        row.render_ms += r.ms;
        row.steps += r.steps;
        if (r.luma < BLACK) timing.black.push(f);
        const out = frameFile(plan.cache, c.key, f, c.ext || "jpg");
        writeFileSync(out + ".part", buf);
        renameSync(out + ".part", out);
        done += 1;
        process.stdout.write(`PROGRESS ${done} ${total} shot=${c.n} frame=${f}\n`);
      }
      row.wall_ms = Date.now() - tc;
      timing.chunks.push(row);
      timing.frames += row.frames;
      timing.render_ms += row.render_ms;
      timing.shot_ms += row.shot_ms;
    }
    timing.warnings = await sc.warnings();
    timing.console = sc.console.filter((l) => /error|warn/i.test(l)).slice(-8);
  } finally {
    await sc.close();
  }
  timing.wall_ms = Date.now() - t0;
  timing.ms_per_frame = timing.frames ? Math.round(timing.wall_ms / timing.frames) : null;
  return timing;
}

async function main() {
  const i = process.argv.indexOf("--plan");
  if (i < 0) {
    process.stderr.write("нужен --plan plan.json\n");
    process.exit(2);
  }
  const plan = JSON.parse(readFileSync(process.argv[i + 1], "utf8"));
  try {
    const timing = await renderPlan(plan);
    if (plan.timing) writeFileSync(plan.timing, JSON.stringify(timing, null, 1));
    process.stdout.write(JSON.stringify({ ok: !timing.black.length, frames: timing.frames, wall_ms: timing.wall_ms,
      ms_per_frame: timing.ms_per_frame, black: timing.black.slice(0, 10), warnings: timing.warnings }) + "\n");
    process.exit(0);
  } catch (err) {
    process.stderr.write(`series_render: ${err.message}\n`);
    process.exit(1);
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) void main();
