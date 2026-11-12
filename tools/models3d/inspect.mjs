#!/usr/bin/env node
/**
 * Что внутри GLB: узлы с мешами, материалы, габарит каждого узла в мире (для подбора yaw/fit и поиска лишнего —
 * подложек, фона, камер). node tools/models3d/inspect.mjs file.glb [file2.glb …]
 */
import { NodeIO, getBounds } from "@gltf-transform/core";
import { ALL_EXTENSIONS } from "@gltf-transform/extensions";
import { MeshoptDecoder } from "meshoptimizer";

await MeshoptDecoder.ready;
const io = new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({ "meshopt.decoder": MeshoptDecoder });
const f = (v) => v.map((x) => x.toFixed(3)).join(",");
for (const file of process.argv.slice(2)) {
  const doc = await io.read(file);
  const scene = doc.getRoot().listScenes()[0];
  const b = getBounds(scene);
  console.log(`# ${file}\n  scene min [${f(b.min)}] max [${f(b.max)}]`);
  for (const node of doc.getRoot().listNodes()) {
    const mesh = node.getMesh();
    if (!mesh) continue;
    const nb = getBounds(node);
    const mats = mesh.listPrimitives().map((p) => p.getMaterial()?.getName() || "?");
    const tris = mesh.listPrimitives().reduce((s, p) => s + (p.getIndices()?.getCount() ?? p.getAttribute("POSITION").getCount()) / 3, 0);
    console.log(`  ${node.getName() || "(node)"} tris=${tris} mats=${[...new Set(mats)].join("|")} min [${f(nb.min)}] max [${f(nb.max)}]`);
  }
}
