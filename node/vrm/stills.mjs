#!/usr/bin/env node
/**
 * Стопкадры VRM-сцены: план i спеки → <out>/<name>.png (браузер и раздача файлов — из scene3d/render.mjs,
 * бандл — node/vrm/dist/vrm.js), плюс лист sheet.jpg (ffmpeg). Модель берётся из --packs (каталог с .vrm).
 *
 * node node/vrm/stills.mjs --spec node/vrm/examples/stills.json --out D:/vvf-offload/vtubika/vrm
 *   --packs D:/vvf-offload/vtubika/vrm/vendor [--only name1,name2] [--gl gpu] [--inspect 0] [--no-sheet]
 */
import { spawnSync } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { openScene } from "../scene3d/render.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));

function args(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i].replace(/^--/, "");
    if (k === "no-sheet") out.noSheet = true;
    else out[k] = argv[++i];
  }
  return out;
}

const a = args(process.argv.slice(2));
const spec = JSON.parse(readFileSync(resolve(a.spec), "utf8"));
const outDir = resolve(a.out || ".");
mkdirSync(outDir, { recursive: true });
const opts = {
  bundle: resolve(a.bundle || join(HERE, "dist", "vrm.js")),
  packs: resolve(a.packs || "D:/vvf-offload/vtubika/vrm/vendor"),
  gl: a.gl || "gpu",
  post: "full",
  chrome: a.chrome || process.env.SCENE3D_CHROMIUM || "",
};
const log = (m) => process.stderr.write(`${m}\n`);
const only = a.only ? new Set(a.only.split(",")) : null;
const t0 = Date.now();
const scene = await openScene(opts, spec, log);
const files = [];
try {
  log(`модель: ${JSON.stringify(scene.info?.meta || {})}`);
  if (a.inspect !== undefined) {
    process.stdout.write(JSON.stringify(await scene.inspect(Number(a.inspect)), null, 1) + "\n");
  } else {
    for (let i = 0; i < spec.shots.length; i++) {
      const name = spec.shots[i].name || `shot${i}`;
      if (only && !only.has(name)) continue;
      const st = await scene.render(i + 0.5);
      const file = join(outDir, `${name}.png`);
      writeFileSync(file, await scene.shot("png"));
      files.push(file);
      log(`${name}: luma ${Math.round(st.luma)}`);
    }
  }
  const tail = scene.console.filter((l) => /error|exception|warn/i.test(l)).slice(-6);
  if (tail.length) log(tail.join("\n"));
} finally {
  await scene.close();
}
if (!a.noSheet && files.length > 1 && !only) {
  // Лист: все кадры в ряд(ы) по 3, уменьшенные до 540×960.
  const cols = 3, rows = Math.ceil(files.length / cols);
  const inputs = files.flatMap((f) => ["-i", f]);
  const scale = files.map((_, i) => `[${i}:v]scale=540:960[s${i}]`).join(";");
  const layout = files.map((_, i) => `${(i % cols) * 540}_${Math.floor(i / cols) * 960}`).join("|");
  const filter = `${scale};${files.map((_, i) => `[s${i}]`).join("")}xstack=inputs=${files.length}:layout=${layout}:fill=black`;
  const r = spawnSync("ffmpeg", ["-hide_banner", "-loglevel", "error", "-y", ...inputs, "-filter_complex", filter, "-frames:v", "1", "-q:v", "3", join(outDir, "sheet.jpg")]);
  if (r.status !== 0) log(`лист не собран: ${r.stderr}`);
  else log(`лист: ${join(outDir, "sheet.jpg")} (${cols}×${rows})`);
}
log(`готово за ${Math.round((Date.now() - t0) / 1000)} с`);
