#!/usr/bin/env node
/**
 * FBX (Mixamo, Unity-ассеты, свой мокап) → нейтральный вид скелета и клипов для tools/anim_retarget.py:
 * {nodes: [{name, parent, t, r}], clips: {имя: {fps, frames, tracks: {индекс узла: {r: [[x,y,z,w]...], t: [[x,y,z]...]}}}}}.
 *
 *   node node/vrm/fbx_dump.mjs FILE.fbx [--fps 30] > skeleton.json
 *
 * Читает three.js FBXLoader (тот же, что в браузере) — без браузера: картинки текстур не нужны, загрузчик изображений
 * подменён пустышкой. Узлы — всё дерево сцены (у FBX над костями бывает узел с поворотом осей и масштабом 0.01 —
 * он нужен прямой кинематике). Масштаб узлов не учитывается: перенос берёт направления костей и отношение длины ноги.
 */
import { readFileSync } from "node:fs";

// Пустышки DOM: FBXLoader создаёт загрузчик текстур при разборе материалов.
globalThis.self ??= globalThis;
globalThis.window ??= globalThis;
globalThis.document ??= {
  createElementNS: () => ({ style: {}, addEventListener() {}, removeEventListener() {}, setAttribute() {} }),
  createElement: () => ({ style: {}, getContext: () => null, addEventListener() {}, removeEventListener() {} }),
};

const args = process.argv.slice(2);
const file = args.find((a) => !a.startsWith("--"));
const fps = Number(args.includes("--fps") ? args[args.indexOf("--fps") + 1] : 30) || 30;
if (!file) {
  process.stderr.write("нужен путь к .fbx\n");
  process.exit(2);
}

const THREE = await import("three");
const { FBXLoader } = await import("three/examples/jsm/loaders/FBXLoader.js");
const origWarn = console.warn;
console.warn = () => {};
const buf = readFileSync(file);
const group = new FBXLoader().parse(buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength), "");
console.warn = origWarn;

const nodes = [];
const index = new Map();
group.traverse((o) => {
  if (o.isMesh || o.isSkinnedMesh || o.isLight || o.isCamera) return;
  index.set(o, nodes.length);
  nodes.push(o);
});
const r5 = (v) => Math.round(v * 1e5) / 1e5;
const out = {
  source: file.split(/[\\/]/).pop(),
  nodes: nodes.map((o) => ({
    name: o.name || "node",
    parent: o.parent && index.has(o.parent) ? index.get(o.parent) : -1,
    t: o.position.toArray().map(r5),
    r: o.quaternion.toArray().map(r5),
  })),
  clips: {},
};
const byName = new Map(nodes.map((o, i) => [o.name, i]));
for (const clip of group.animations || []) {
  const frames = Math.max(1, Math.round(clip.duration * fps) + 1);
  const tracks = {};
  for (const tr of clip.tracks) {
    const m = tr.name.match(/^(.*)\.(quaternion|position)$/);
    if (!m || !byName.has(m[1])) continue;
    const i = byName.get(m[1]);
    const interp = tr.createInterpolant();
    const vals = [];
    for (let f = 0; f < frames; f++) vals.push(Array.from(interp.evaluate(Math.min(clip.duration, f / fps))).map(r5));
    tracks[i] ||= {};
    tracks[i][m[2] === "quaternion" ? "r" : "t"] = vals;
  }
  out.clips[clip.name || `clip${Object.keys(out.clips).length}`] = { fps, frames, tracks };
}
void THREE;
process.stdout.write(JSON.stringify(out));
