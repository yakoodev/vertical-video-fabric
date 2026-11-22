// Сжатие геометрии GLB для 3D-превью (app/assets3d_preview.py): каждый bufferView вершин, индексов, морфов и
// анимации — кодеком meshoptimizer (EXT_meshopt_compression), без перестройки документа. Номера узлов, мешей,
// материалов и текстур не меняются, поэтому расширения VRM (VRM 0.x, VRMC_*) остаются верными.
//
//   node node/assets3d/meshopt.mjs IN.glb OUT.glb
//
// Печатает JSON {compressed, kept, before, after}. Без сжатых видов (нечего сжимать, несколько буферов) — копия.
import fs from "node:fs";
import { MeshoptEncoder } from "meshoptimizer";

const COMP = { 5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4 };
const NCOMP = { SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4, MAT2: 4, MAT3: 9, MAT4: 16 };
const pad4 = (n) => (n + 3) & ~3;

function readGlb(buf) {
  if (buf.readUInt32LE(0) !== 0x46546c67) throw new Error("не GLB");
  let off = 12, json = null, bin = null;
  while (off < buf.length) {
    const len = buf.readUInt32LE(off), type = buf.readUInt32LE(off + 4);
    const chunk = buf.subarray(off + 8, off + 8 + len);
    if (type === 0x4e4f534a) json = JSON.parse(chunk.toString("utf8"));
    else if (type === 0x004e4942) bin = chunk;
    off += 8 + len;
  }
  return { json, bin };
}

function writeGlb(json, bin) {
  let js = Buffer.from(JSON.stringify(json), "utf8");
  js = Buffer.concat([js, Buffer.alloc(pad4(js.length) - js.length, 0x20)]);
  const bn = Buffer.concat([bin, Buffer.alloc(pad4(bin.length) - bin.length)]);
  const head = Buffer.alloc(12);
  head.writeUInt32LE(0x46546c67, 0);
  head.writeUInt32LE(2, 4);
  head.writeUInt32LE(12 + 8 + js.length + 8 + bn.length, 8);
  const ch = (len, type) => { const b = Buffer.alloc(8); b.writeUInt32LE(len, 0); b.writeUInt32LE(type, 4); return b; };
  return Buffer.concat([head, ch(js.length, 0x4e4f534a), js, ch(bn.length, 0x004e4942), bn]);
}

/** Как сжимать вид: {mode, stride, count} или null (картинки, смешанные виды, неподходящий шаг). */
function plan(json, i, bv, users, indexUse) {
  if (!users.length) return null;
  const sizes = users.map((a) => COMP[a.componentType] * NCOMP[a.type]);
  let stride = bv.byteStride;
  if (!stride) {
    if (new Set(sizes).size !== 1) return null;
    stride = sizes[0];
  }
  if (!stride || bv.byteLength % stride) return null;
  const count = bv.byteLength / stride;
  if (indexUse.has(i)) {
    if (users.length !== 1 || (stride !== 2 && stride !== 4)) return null;
    return { mode: indexUse.get(i) && count % 3 === 0 ? "TRIANGLES" : "INDICES", stride, count };
  }
  if (stride % 4 || stride > 256) return null;
  return { mode: "ATTRIBUTES", stride, count };
}

async function main() {
  const [inp, outp] = process.argv.slice(2);
  const src = fs.readFileSync(inp);
  const { json, bin } = readGlb(src);
  const views = json.bufferViews || [];
  if (!bin || (json.buffers || []).length !== 1 || views.some((v) => (v.buffer ?? 0) !== 0 || v.extensions?.EXT_meshopt_compression)) {
    fs.writeFileSync(outp, src);
    console.log(JSON.stringify({ compressed: 0, kept: views.length, before: src.length, after: src.length }));
    return;
  }
  await MeshoptEncoder.ready;
  const users = views.map(() => []);
  for (const a of json.accessors || []) {
    if (a.bufferView !== undefined) users[a.bufferView].push(a);
    if (a.sparse) return fs.writeFileSync(outp, src); // редкость; не рискуем
  }
  // Индексы: вид → true, если это треугольники (mode 4 / по умолчанию).
  const indexUse = new Map();
  for (const m of json.meshes || []) {
    for (const p of m.primitives || []) {
      if (p.indices === undefined) continue;
      const bvI = json.accessors[p.indices].bufferView;
      if (bvI === undefined) continue;
      const tri = (p.mode ?? 4) === 4;
      indexUse.set(bvI, (indexUse.get(bvI) ?? true) && tri);
    }
  }
  const parts = [];
  let main = 0, fallback = 0, compressed = 0;
  views.forEach((bv, i) => {
    const data = bin.subarray(bv.byteOffset || 0, (bv.byteOffset || 0) + bv.byteLength);
    const p = plan(json, i, bv, users[i], indexUse);
    let enc = null;
    if (p) {
      try {
        enc = Buffer.from(MeshoptEncoder.encodeGltfBuffer(new Uint8Array(data), p.count, p.stride, p.mode));
      } catch {
        enc = null;
      }
      if (enc && enc.length > data.length * 0.95) enc = null;
    }
    if (enc) {
      bv.extensions = {
        ...(bv.extensions || {}),
        EXT_meshopt_compression: { buffer: 0, byteOffset: main, byteLength: enc.length, byteStride: p.stride, mode: p.mode, count: p.count },
      };
      parts.push(enc, Buffer.alloc(pad4(enc.length) - enc.length));
      main += pad4(enc.length);
      bv.buffer = 1;
      bv.byteOffset = fallback;
      fallback += pad4(bv.byteLength);
      compressed++;
    } else {
      parts.push(data, Buffer.alloc(pad4(data.length) - data.length));
      bv.buffer = 0;
      bv.byteOffset = main;
      main += pad4(data.length);
    }
  });
  if (!compressed) {
    fs.writeFileSync(outp, src);
    console.log(JSON.stringify({ compressed: 0, kept: views.length, before: src.length, after: src.length }));
    return;
  }
  json.buffers = [{ byteLength: main }, { byteLength: fallback, extensions: { EXT_meshopt_compression: { fallback: true } } }];
  for (const key of ["extensionsUsed", "extensionsRequired"]) {
    json[key] = [...new Set([...(json[key] || []), "EXT_meshopt_compression"])];
  }
  const out = writeGlb(json, Buffer.concat(parts));
  fs.writeFileSync(outp, out);
  console.log(JSON.stringify({ compressed, kept: views.length - compressed, before: src.length, after: out.length }));
}

main().catch((e) => {
  console.error(String(e?.stack || e));
  process.exit(1);
});
