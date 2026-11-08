#!/usr/bin/env node
/**
 * Проверка касаний по всем кадрам ролика (без снимков): пальцы и запястья не ниже поверхностей
 * spec.timeline.surfaces (стол, клавиатура) с допуском 4 мм на толщину пальца. Печатает JSON
 * {ok, frames, min_gap_mm, at, point, surface}; код выхода 1 — есть кадр с нарушением.
 *
 * node node/vrm/contact.mjs --spec node/vrm/examples/motion_test_v2.json [--packs DIR] [--no-clamp]
 * --no-clamp — без подъёма кистей (показать, что проверка ловит проход сквозь клавиатуру).
 */
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { openScene } from "../scene3d/render.mjs";

export const MIN_GAP = 0.004;

const HERE = dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const get = (k, d) => (args.includes(k) ? args[args.indexOf(k) + 1] : d);
const spec = JSON.parse(readFileSync(resolve(get("--spec")), "utf8"));
spec.strictContact = false;
if (args.includes("--no-clamp")) spec.timeline.clamp = false;
const fps = spec.fps || 30, n = Math.round((spec.duration || 10) * fps);
const scene = await openScene({ bundle: join(HERE, "dist", "vrm.js"), packs: resolve(get("--packs", "D:/vvf-offload/vtubika/vrm/vendor")),
  gl: get("--gl", "gpu"), post: "full", chrome: "" }, spec, () => {});
let c;
try {
  for (let i = 0; i < n; i++) await scene.render(i / fps);
  c = await scene.evaluate("window.__scene3d.contact()");
} finally {
  await scene.close();
}
const ok = c.frames === n && c.min >= MIN_GAP;
process.stdout.write(JSON.stringify({ ok, frames: c.frames, min_gap_mm: Math.round(c.min * 10000) / 10, at: c.at, point: c.point,
  surface: c.surface }) + "\n");
process.exit(ok ? 0 : 1);
