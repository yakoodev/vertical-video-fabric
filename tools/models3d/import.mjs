#!/usr/bin/env node
/**
 * Импорт 3D-моделей в библиотеку ассетов сцен (docs/3d/models.md).
 *
 *   cd tools/models3d && npm install            # один раз: gltf-transform, meshoptimizer, @napi-rs/canvas
 *   node tools/models3d/import.mjs --out D:/vvf-data/runtime/models3d --dl D:/vvf-offload/tmp/assets3d/dl [--only keyboard/,mug/white]
 *
 * Для каждой записи tools/models3d/sources.json: скачивает GLB (poly.pizza — в свой новый пустой каталог --dl/<id>;
 * скачанное — только данные, ничего из него не запускается) или строит свою модель (gen_keyboard.mjs), затем
 * нормализует: поворот yaw (перёд → +z), масштаб до габарита fit (метры), основание — центр низа в начале координат
 * (всё — в корневом узле "vvf:normalize"); чистит (dedup/prune/weld), текстуры — не больше 1024 px (JPEG/PNG),
 * геометрия — meshopt (EXT_meshopt_compression, в движке — MeshoptDecoder). Пишет <out>/<категория>/<slug>.glb и
 * <out>/manifest.json (источник, автор, лицензия, габарит, треугольники, байты).
 */
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { Document, NodeIO, getBounds } from "@gltf-transform/core";
import { ALL_EXTENSIONS, EXTMeshoptCompression } from "@gltf-transform/extensions";
import { dedup, normals, prune, reorder, quantize, unweld, weld, simplify } from "@gltf-transform/functions";
import { MeshoptEncoder, MeshoptDecoder, MeshoptSimplifier } from "meshoptimizer";
import { createCanvas, loadImage } from "@napi-rs/canvas";
import { generateKeyboard } from "./gen_keyboard.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const LICENSES = {
  "CC0-1.0": { name: "CC0 1.0", url: "https://creativecommons.org/publicdomain/zero/1.0/" },
  "CC-BY-3.0": { name: "CC BY 3.0", url: "https://creativecommons.org/licenses/by/3.0/" },
  "CC-BY-4.0": { name: "CC BY 4.0", url: "https://creativecommons.org/licenses/by/4.0/" },
};
const MAX_TEX = 1024;
const MAX_TRIS = 40000;
const BUDGET = 2 * 1024 * 1024;

function args(argv) {
  const o = {};
  for (let i = 0; i < argv.length; i++) {
    const [k, v] = argv[i].replace(/^--/, "").split(/=(.*)/s, 2);
    o[k] = v !== undefined ? v : argv[i + 1] && !argv[i + 1].startsWith("--") ? argv[++i] : true;
  }
  return o;
}

async function fetchGlb(entry, dlRoot) {
  // Каждая модель — в свой каталог; повторный запуск берёт уже скачанное.
  const dir = join(dlRoot, entry.id.replace("/", "__"));
  const file = join(dir, "source.glb");
  if (existsSync(file) && statSync(file).size > 0) return readFileSync(file);
  mkdirSync(dir, { recursive: true });
  const url = `https://static.poly.pizza/${entry.res}.glb`;
  const res = await fetch(url, { headers: { "user-agent": "Mozilla/5.0 (asset import)" } });
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  const buf = Buffer.from(await res.arrayBuffer());
  if (buf.length > 60 * 1024 * 1024) throw new Error(`${url}: слишком большой файл (${buf.length})`);
  if (buf.readUInt32LE(0) !== 0x46546c67) throw new Error(`${url}: не GLB`);
  writeFileSync(file, buf);
  return buf;
}

function triCount(doc) {
  let n = 0;
  for (const mesh of doc.getRoot().listMeshes()) {
    for (const p of mesh.listPrimitives()) {
      if (p.getMode() !== 4) continue;
      const idx = p.getIndices();
      n += (idx ? idx.getCount() : p.getAttribute("POSITION").getCount()) / 3;
    }
  }
  return Math.round(n);
}

/** Поворот вокруг Y на yaw° → кватернион. */
function yawQuat(deg) {
  const a = (deg * Math.PI) / 360;
  return [0, Math.sin(a), 0, Math.cos(a)];
}

function normalize(doc, entry) {
  const scene = doc.getRoot().getDefaultScene() || doc.getRoot().listScenes()[0];
  const wrap = doc.createNode("vvf:normalize");
  for (const child of scene.listChildren()) {
    scene.removeChild(child);
    wrap.addChild(child);
  }
  scene.addChild(wrap);
  const up = entry.up || "y";
  // Ось «вверх» z (выгрузки из CAD/Blender без конверсии): поворот −90° вокруг x.
  let q = yawQuat(entry.yaw || 0);
  if (up === "z") q = mulQ(q, [-Math.SQRT1_2, 0, 0, Math.SQRT1_2]);
  wrap.setRotation(q);
  let b = getBounds(scene);
  const size = [b.max[0] - b.min[0], b.max[1] - b.min[1], b.max[2] - b.min[2]];
  const f = entry.fit || { dim: "max", value: 1 };
  const dimV = f.dim === "width" ? size[0] : f.dim === "height" ? size[1] : f.dim === "depth" ? size[2] : Math.max(...size);
  const s = f.value / Math.max(1e-9, dimV);
  wrap.setScale([s, s, s]);
  wrap.setTranslation([-s * (b.min[0] + b.max[0]) / 2, -s * b.min[1], -s * (b.min[2] + b.max[2]) / 2]);
  b = getBounds(scene);
  return { scale: s, size: [0, 1, 2].map((i) => +(b.max[i] - b.min[i]).toFixed(4)), min: b.min.map((v) => +v.toFixed(4)) };
}

function mulQ(a, b) {
  const [ax, ay, az, aw] = a, [bx, by, bz, bw] = b;
  return [aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx, aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz];
}

async function shrinkTextures(doc) {
  for (const tex of doc.getRoot().listTextures()) {
    const img = tex.getImage();
    if (!img) continue;
    let im;
    try {
      im = await loadImage(Buffer.from(img));
    } catch {
      continue;
    }
    const png = tex.getMimeType() === "image/png";
    const k = Math.min(1, MAX_TEX / Math.max(im.width, im.height));
    if (k >= 1 && img.byteLength < 400 * 1024) continue;
    const w = Math.max(1, Math.round(im.width * k)), h = Math.max(1, Math.round(im.height * k));
    const c = createCanvas(w, h);
    c.getContext("2d").drawImage(im, 0, 0, w, h);
    const out = png ? await c.encode("png") : await c.encode("jpeg", 85);
    tex.setImage(new Uint8Array(out)).setMimeType(png ? "image/png" : "image/jpeg");
  }
}

async function main() {
  const a = args(process.argv.slice(2));
  const out = resolve(a.out || process.env.SCENE3D_MODELS_DIR || "D:/vvf-data/runtime/models3d");
  const dl = resolve(a.dl || "D:/vvf-offload/tmp/assets3d/dl");
  const only = a.only ? String(a.only).split(",").map((s) => s.trim()).filter(Boolean) : null;
  const src = JSON.parse(readFileSync(join(HERE, "sources.json"), "utf-8"));
  await MeshoptEncoder.ready;
  await MeshoptDecoder.ready;
  const io = new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({ "meshopt.encoder": MeshoptEncoder, "meshopt.decoder": MeshoptDecoder });
  const manifestPath = join(out, "manifest.json");
  const manifest = existsSync(manifestPath) ? JSON.parse(readFileSync(manifestPath, "utf-8")) : { version: 1, models: {} };
  const ids = new Set(src.models.map((m) => m.id));
  for (const id of Object.keys(manifest.models)) if (!ids.has(id)) delete manifest.models[id];
  const report = [];
  for (const entry of src.models) {
    if (only && !only.some((p) => entry.id === p || (p.endsWith("/") && entry.id.startsWith(p)))) continue;
    const [category, slug] = entry.id.split("/");
    try {
      let doc;
      if (entry.gen) {
        doc = new Document();
        doc.createBuffer();
        const scene = doc.createScene("scene");
        scene.addChild(generateKeyboard(doc, entry.gen));
        doc.getRoot().setDefaultScene(scene);
      } else {
        doc = await io.readBinary(new Uint8Array(await fetchGlb(entry, dl)));
      }
      // Анимации и камеры/свет из исходника не нужны (свет сцены ставит движок).
      for (const anim of doc.getRoot().listAnimations()) anim.dispose();
      for (const node of doc.getRoot().listNodes()) {
        if (node.getCamera()) node.setCamera(null);
      }
      const norm = normalize(doc, entry);
      // Без нормалей меш в движке чёрный (часть выгрузок Poly): плоские нормали по граням — лоу-поли вид.
      const noNormals = doc.getRoot().listMeshes().some((m) => m.listPrimitives().some((p) => !p.getAttribute("NORMAL")));
      if (noNormals) await doc.transform(unweld(), normals({ overwrite: false }));
      await doc.transform(dedup(), prune(), weld());
      let tris = triCount(doc);
      if (tris > MAX_TRIS) {
        await MeshoptSimplifier.ready;
        await doc.transform(simplify({ simplifier: MeshoptSimplifier, ratio: MAX_TRIS / tris, error: 0.002 }));
        tris = triCount(doc);
      }
      await shrinkTextures(doc);
      await doc.transform(reorder({ encoder: MeshoptEncoder }), quantize());
      doc.createExtension(EXTMeshoptCompression).setRequired(true).setEncoderOptions({ method: EXTMeshoptCompression.EncoderMethod.FILTER });
      const glb = await io.writeBinary(doc);
      const file = `${category}/${slug}.glb`;
      mkdirSync(join(out, category), { recursive: true });
      writeFileSync(join(out, file), glb);
      const lic = entry.gen ? { name: "CC0 1.0 (своя модель)", url: LICENSES["CC0-1.0"].url } : LICENSES[entry.license];
      if (!lic) throw new Error(`неизвестная лицензия ${entry.license}`);
      const materials = doc.getRoot().listMaterials().map((m) => m.getName()).filter(Boolean);
      manifest.models[entry.id] = {
        id: entry.id, category, slug, file, title: entry.title,
        size: norm.size, tris, bytes: glb.byteLength, sha1: createHash("sha1").update(glb).digest("hex").slice(0, 12),
        textures: doc.getRoot().listTextures().length, materials,
        normalize: { up: entry.up || "y", yaw: entry.yaw || 0, scale: +norm.scale.toPrecision(5), fit: entry.fit, pivot: "bottom-center", front: "+z" },
        outline: entry.outline ?? 1, tags: entry.tags || [], weight: entry.weight ?? 1,
        ...(entry.anchors ? { anchors: entry.anchors } : {}),
        source: entry.gen
          ? { kind: "generated", generator: `tools/models3d/gen_keyboard.mjs#${entry.gen}` }
          : { kind: "poly.pizza", url: `https://poly.pizza/m/${entry.pp}`, download: `https://static.poly.pizza/${entry.res}.glb` },
        author: entry.gen ? "vertical-video-fabric" : entry.author,
        license: entry.gen ? "CC0-1.0" : entry.license, license_name: lic.name, license_url: lic.url,
        attribution: entry.gen ? "" : `«${entry.title}» — ${entry.author}, poly.pizza/m/${entry.pp}, ${lic.name}`,
      };
      report.push(`${entry.id.padEnd(28)} ${String(tris).padStart(6)} tris ${(glb.byteLength / 1024).toFixed(0).padStart(5)} KB size ${norm.size.join("×")}${glb.byteLength > BUDGET ? "  ! больше 2 МБ" : ""}`);
    } catch (e) {
      report.push(`${entry.id.padEnd(28)} ОШИБКА ${e.message}`);
    }
  }
  const sorted = Object.fromEntries(Object.keys(manifest.models).sort().map((k) => [k, manifest.models[k]]));
  const categories = {};
  for (const m of Object.values(sorted)) (categories[m.category] ||= []).push(m.id);
  const final = { version: 1, generated: new Date().toISOString().slice(0, 10), doc: "docs/3d/models.md", categories, models: sorted };
  mkdirSync(out, { recursive: true });
  writeFileSync(manifestPath, JSON.stringify(final, null, 2) + "\n");
  console.log(report.join("\n"));
  console.log(`manifest: ${manifestPath} (${Object.keys(sorted).length} моделей)`);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
