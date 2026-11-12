#!/usr/bin/env node
/**
 * Кисти крупно: та же спека ролика (поза по времени), но камера — на руки, спереди сверху и сбоку; на каждое время
 * — скрутка запястий и предплечий (twist.ts). Лист hands_sheet.jpg: строка на время, слева вид спереди, справа сбоку.
 *
 * node node/vrm/hands.mjs --spec work/scene_vrm.json --times 3,12,33 --out DIR [--sheet DIR/hands_sheet.jpg]
 *   [--packs D:/vvf-offload/vtubika/vrm/vendor] [--site DIR] [--gl gpu] [--cols 3]
 * Печатает JSON {ok, frames: [{t, front, side, twist: {left, right}, touch}]}.
 */
import { spawnSync } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defaultModelsDir, openScene } from "../scene3d/render.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const argv = process.argv.slice(2);
const get = (k, d) => (argv.includes(k) ? argv[argv.indexOf(k) + 1] : d);
const base = JSON.parse(readFileSync(resolve(get("--spec")), "utf8"));
const times = String(get("--times", "")).split(",").map(Number).filter((t) => Number.isFinite(t) && t >= 0);
if (!times.length) throw new Error("--times: секунды через запятую");
const outDir = resolve(get("--out", "."));
mkdirSync(outDir, { recursive: true });
const log = (m) => process.stderr.write(`${m}\n`);

/** Середина кистей; камера смотрит на неё с отступа add (оси модели: герой смотрит в +Z, его левая рука — +X). */
const MID = { mix: [{ bone: "leftHand" }, { bone: "rightHand" }], w: 0.5 };
const VIEWS = {
  front: { pos: { ...MID, add: [0, 0.4, 0.8] }, target: { ...MID, add: [0, -0.02, -0.04] }, fov: 34 },
  side: { pos: { ...MID, add: [0.8, 0.22, 0.42] }, target: { ...MID, add: [0, -0.01, -0.04] }, fov: 30 },
};

/** Плана на время t в исходной спеке: свет и предметы (стол, клавиатура) — оттуда. */
function shotAt(t) {
  return base.shots.find((s) => t >= (s.t0 ?? 0) && t < (s.t1 ?? 1e9)) || base.shots[base.shots.length - 1];
}

function specFor(view) {
  // Один план на время (окно ±1 кадр), камера — на кисти; подложки и вставки не нужны.
  const shots = times.map((t, i) => {
    const src = shotAt(t);
    const props = { ...(src.props || {}) };
    delete props.phone;
    return { name: `h${i}`, t0: t - 0.02, t1: t + 0.02, cam: VIEWS[view], props, light: src.light || {}, plate: {} };
  });
  const s = { ...base, shots, overlays: [], strictContact: false, handCheck: false };
  delete s.plates;
  delete s.avatar;
  return s;
}

const opts = (spec) => ({
  bundle: resolve(get("--bundle", join(HERE, "dist", "vrm.js"))),
  packs: resolve(get("--packs", "D:/vvf-offload/vtubika/vrm/vendor")),
  site: get("--site") ? resolve(get("--site")) : base.siteDir ? resolve(base.siteDir) : null,
  plates: null,
  models: defaultModelsDir(),
  gl: get("--gl", "gpu"),
  post: "full",
  chrome: process.env.SCENE3D_CHROMIUM || "",
  width: spec.width,
  height: spec.height,
});

const frames = times.map((t) => ({ t }));
for (const view of Object.keys(VIEWS)) {
  const spec = specFor(view);
  const scene = await openScene(opts(spec), spec, log);
  try {
    for (const f of frames) {
      await scene.render(f.t);
      const file = join(outDir, `hands_${view}_${f.t.toFixed(2).replace(".", "_")}.png`);
      writeFileSync(file, await scene.shot("png"));
      f[view] = file;
      if (view === "front") {
        const st = await scene.inspect(f.t);
        f.twist = st.twist;
        f.touch = st.touch;
      }
    }
  } finally {
    await scene.close();
  }
}

const sheet = get("--sheet");
if (sheet) {
  // Строка листа на время: спереди | сбоку (квадраты 540 из середины кадра), подпись — время и скрутка запястий.
  const cols = Number(get("--cols", 3));
  const inputs = [], chains = [];
  frames.forEach((f, i) => {
    inputs.push("-i", f.front, "-i", f.side);
    const tw = f.twist || {};
    const lab = `${f.t.toFixed(2)}s  L ${tw.left?.wrist ?? "?"}/${tw.left?.forearm ?? "?"}  R ${tw.right?.wrist ?? "?"}/${tw.right?.forearm ?? "?"}`;
    const crop = "crop=1080:1080:0:420,scale=540:540";
    chains.push(`[${2 * i}:v]${crop}[a${i}];[${2 * i + 1}:v]${crop}[b${i}];[a${i}][b${i}]hstack=inputs=2,` +
      `drawtext=text='${lab}':x=12:y=12:fontsize=26:fontcolor=white:box=1:boxcolor=black@0.6[r${i}]`);
  });
  const layout = frames.map((_, i) => `${(i % cols) * 1080}_${Math.floor(i / cols) * 540}`).join("|");
  const graph = `${chains.join(";")};${frames.map((_, i) => `[r${i}]`).join("")}xstack=inputs=${frames.length}:layout=${layout}:fill=black[out]`;
  const r = spawnSync("ffmpeg", ["-hide_banner", "-loglevel", "error", "-y", ...inputs, "-filter_complex", graph, "-map", "[out]",
    "-frames:v", "1", "-q:v", "3", resolve(sheet)]);
  if (r.status !== 0) throw new Error(`лист кистей не собран: ${String(r.stderr).slice(-400)}`);
}
process.stdout.write(JSON.stringify({ ok: true, frames, sheet: sheet ? resolve(sheet) : null }) + "\n");
