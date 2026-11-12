#!/usr/bin/env node
/**
 * Крупнейшие плоские грани модели, смотрящие в сторону dir (по умолчанию +z, «перёд»): для якорей манифеста —
 * экран монитора/ноутбука (центр, ширина, высота, наклон). node tools/models3d/faces.mjs file.glb [--dir 0,0,1] [--top 5]
 * Координаты — мировые (после нормализации vvf:normalize), метры.
 */
import { NodeIO } from "@gltf-transform/core";
import { ALL_EXTENSIONS } from "@gltf-transform/extensions";
import { MeshoptDecoder } from "meshoptimizer";

await MeshoptDecoder.ready;
const io = new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({ "meshopt.decoder": MeshoptDecoder });
const file = process.argv[2];
const di = process.argv.indexOf("--dir");
const dir = di > 0 ? process.argv[di + 1].split(",").map(Number) : [0, 0, 1];
const ti = process.argv.indexOf("--top");
const top = ti > 0 ? Number(process.argv[ti + 1]) : 5;
const doc = await io.read(file);
const mul = (m, v) => [m[0] * v[0] + m[4] * v[1] + m[8] * v[2] + m[12], m[1] * v[0] + m[5] * v[1] + m[9] * v[2] + m[13], m[2] * v[0] + m[6] * v[1] + m[10] * v[2] + m[14]];
const groups = new Map();
for (const node of doc.getRoot().listNodes()) {
  const mesh = node.getMesh();
  if (!mesh) continue;
  const M = node.getWorldMatrix();
  for (const prim of mesh.listPrimitives()) {
    const pos = prim.getAttribute("POSITION");
    const idx = prim.getIndices();
    const n = idx ? idx.getCount() : pos.getCount();
    const P = (i) => mul(M, pos.getElement(idx ? idx.getScalar(i) : i, []));
    const mat = prim.getMaterial()?.getName() || "?";
    for (let i = 0; i < n; i += 3) {
      const a = P(i), b = P(i + 1), c = P(i + 2);
      const u = [b[0] - a[0], b[1] - a[1], b[2] - a[2]], v = [c[0] - a[0], c[1] - a[1], c[2] - a[2]];
      const cr = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]];
      const area = Math.hypot(...cr) / 2;
      if (area < 1e-6) continue;
      const nn = cr.map((x) => x / (2 * area));
      const facing = nn[0] * dir[0] + nn[1] * dir[1] + nn[2] * dir[2];
      if (facing < 0.8) continue;
      // Группа — одна плоскость: нормаль и смещение, округлённые.
      const d = nn[0] * a[0] + nn[1] * a[1] + nn[2] * a[2];
      const key = `${mat}|${nn.map((x) => x.toFixed(2)).join(",")}|${d.toFixed(3)}`;
      let g = groups.get(key);
      if (!g) groups.set(key, (g = { mat, n: nn, area: 0, min: [Infinity, Infinity, Infinity], max: [-Infinity, -Infinity, -Infinity] }));
      g.area += area;
      for (const p of [a, b, c]) for (let k = 0; k < 3; k++) { g.min[k] = Math.min(g.min[k], p[k]); g.max[k] = Math.max(g.max[k], p[k]); }
    }
  }
}
const f = (v) => v.map((x) => x.toFixed(3)).join(",");
for (const g of [...groups.values()].sort((x, y) => y.area - x.area).slice(0, top)) {
  const tilt = (Math.atan2(-g.n[1], g.n[2]) * 180) / Math.PI;
  console.log(`${g.mat} area=${g.area.toFixed(4)} n=[${f(g.n)}] tilt=${tilt.toFixed(1)}° min [${f(g.min)}] max [${f(g.max)}] center [${f(g.min.map((v, k) => (v + g.max[k]) / 2))}]`);
}
