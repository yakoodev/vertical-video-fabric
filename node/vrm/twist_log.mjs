#!/usr/bin/env node
/**
 * Журнал скрутки рук по кадрам (twist.ts): на каждое время — скрутка кисти и предплечья, сгиб запястья, нормаль
 * ладони, сгиб локтя и решение IK (rig.info: flex, roll, short) по обеим рукам. Для разбора «руки выкручиваются».
 *
 * node node/vrm/twist_log.mjs --spec work/scene_vrm.json [--from 0 --to 10 --step 0.5] [--packs DIR] [--gl gpu]
 * Печатает JSON-строку на время.
 */
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defaultModelsDir, openScene } from "../scene3d/render.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const argv = process.argv.slice(2);
const get = (k, d) => (argv.includes(k) ? argv[argv.indexOf(k) + 1] : d);
const spec = JSON.parse(readFileSync(resolve(get("--spec")), "utf8"));
spec.strictContact = false;
delete spec.plates;
const from = Number(get("--from", 0)), to = Number(get("--to", spec.duration || 10)), step = Number(get("--step", 0.5));
const scene = await openScene({ bundle: resolve(get("--bundle", join(HERE, "dist", "vrm.js"))),
  packs: resolve(get("--packs", "D:/vvf-offload/vtubika/vrm/vendor")), site: spec.siteDir ? resolve(spec.siteDir) : null,
  plates: null, models: defaultModelsDir(), gl: get("--gl", "gpu"), post: "off", chrome: "" }, spec, () => {});
try {
  for (let t = from; t <= to + 1e-6; t += step) {
    const st = await scene.inspect(t);
    process.stdout.write(JSON.stringify({ t: Math.round(t * 100) / 100, twist: st.twist, ik: st.arms, touch: st.touch,
      resolved: st.resolved, bones: Object.fromEntries(["hips", "chest", "head", "leftUpperArm", "leftLowerArm", "leftHand",
        "rightUpperArm", "rightLowerArm", "rightHand"].map((b) => [b, st.bones[b]])) }) + "\n");
  }
} finally {
  await scene.close();
}
