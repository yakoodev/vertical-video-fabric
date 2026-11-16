// Карта высот верхних граней модели (крыша, сцена): куда можно ставить/катить вещи. Шаг --step, значения — (y − --base)/0.25 в base36.
//   node tools/models3d/heightmap.mjs D:/vvf-data/runtime/models3d/building/kenney_sky_a.glb --x -15,15 --z -15,15 --base 58.33
import { NodeIO } from "@gltf-transform/core";
import { ALL_EXTENSIONS } from "@gltf-transform/extensions";
import { MeshoptDecoder } from "meshoptimizer";

const argv = process.argv.slice(2);
const file = argv[0];
const opt = (k, d) => {
  const i = argv.indexOf(`--${k}`);
  return i >= 0 ? argv[i + 1] : d;
};
const xr = opt("x", "-10,10").split(",").map(Number), zr = opt("z", "-10,10").split(",").map(Number);
const step = Number(opt("step", 1)), base = Number(opt("base", 0));
await MeshoptDecoder.ready;
const io = new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({ "meshopt.decoder": MeshoptDecoder });
const doc = await io.read(file);
const bins = new Map();
const mul = (m, v) => [m[0] * v[0] + m[4] * v[1] + m[8] * v[2] + m[12], m[1] * v[0] + m[5] * v[1] + m[9] * v[2] + m[13], m[2] * v[0] + m[6] * v[1] + m[10] * v[2] + m[14]];
const key = (gx, gz) => `${Math.round(gx / step)},${Math.round(gz / step)}`;
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
      const ys = (a[1] + b[1] + c[1]) / 3;
      const x0 = Math.min(a[0], b[0], c[0]), x1 = Math.max(a[0], b[0], c[0]), z0 = Math.min(a[2], b[2], c[2]), z1 = Math.max(a[2], b[2], c[2]);
      for (let gx = Math.ceil(x0 / step) * step; gx <= x1 + 1e-9; gx += step)
        for (let gz = Math.ceil(z0 / step) * step; gz <= z1 + 1e-9; gz += step) {
          const k = key(gx, gz);
          if (!bins.has(k) || bins.get(k) < ys) bins.set(k, ys);
        }
    }
  }
}
let head = "       ";
for (let gx = xr[0]; gx <= xr[1]; gx += step) head += String(Math.round(gx)).slice(-1).padStart(2);
console.log(head);
for (let gz = zr[0]; gz <= zr[1]; gz += step) {
  let row = `${gz.toFixed(1).padStart(6)} `;
  for (let gx = xr[0]; gx <= xr[1]; gx += step) {
    const v = bins.get(key(gx, gz));
    row += v === undefined ? " ." : v - base < 0.05 ? " 0" : " " + Math.min(35, Math.round((v - base) / 0.25)).toString(36);
  }
  console.log(row);
}
