#!/usr/bin/env node
/**
 * Где на модели можно стоять: горизонтальные грани «вверх» (нормаль y > 0.9), площадь по высотам (шаг 1 см) — сиденье
 * трона, пол сцены, крыша дома. Координаты — как в библиотеке (после нормализации), в метрах.
 *
 *   node tools/models3d/tops.mjs D:/vvf-data/runtime/models3d/lux/throne.glb [--x -0.3,0.3 --z -0.3,0.3] [--top 8]
 */
import { NodeIO } from "@gltf-transform/core";
import { ALL_EXTENSIONS } from "@gltf-transform/extensions";
import { MeshoptDecoder } from "meshoptimizer";

const argv = process.argv.slice(2);
const file = argv[0];
const opt = (k, d) => {
  const i = argv.indexOf(`--${k}`);
  return i >= 0 ? argv[i + 1] : d;
};
const xr = opt("x", "-1e9,1e9").split(",").map(Number), zr = opt("z", "-1e9,1e9").split(",").map(Number);
const top = Number(opt("top", 8));
await MeshoptDecoder.ready;
const io = new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({ "meshopt.decoder": MeshoptDecoder });
const doc = await io.read(file);
const bins = new Map();
const mul = (m, v) => [m[0] * v[0] + m[4] * v[1] + m[8] * v[2] + m[12], m[1] * v[0] + m[5] * v[1] + m[9] * v[2] + m[13], m[2] * v[0] + m[6] * v[1] + m[10] * v[2] + m[14]];
for (const node of doc.getRoot().listNodes()) {
  const mesh = node.getMesh();
  if (!mesh) continue;
  const M = node.getWorldMatrix();
  for (const p of mesh.listPrimitives()) {
    const pos = p.getAttribute("POSITION");
    const idx = p.getIndices();
    const n = idx ? idx.getCount() : pos.getCount();
    const get = (i) => mul(M, pos.getElement(idx ? idx.getScalar(i) : i, []));
    for (let i = 0; i + 2 < n; i += 3) {
      const a = get(i), b = get(i + 1), c = get(i + 2);
      const u = [b[0] - a[0], b[1] - a[1], b[2] - a[2]], v = [c[0] - a[0], c[1] - a[1], c[2] - a[2]];
      const cr = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]];
      const len = Math.hypot(...cr);
      if (len < 1e-12 || cr[1] / len < 0.9) continue;
      const cx = (a[0] + b[0] + c[0]) / 3, cz = (a[2] + b[2] + c[2]) / 3;
      if (cx < xr[0] || cx > xr[1] || cz < zr[0] || cz > zr[1]) continue;
      const y = Math.round(((a[1] + b[1] + c[1]) / 3) * 100) / 100;
      bins.set(y, (bins.get(y) || 0) + len / 2);
    }
  }
}
const list = [...bins.entries()].sort((p, q) => q[1] - p[1]).slice(0, top);
for (const [y, area] of list) console.log(`y=${y.toFixed(2)} м  площадь ${area.toFixed(3)} м²`);
