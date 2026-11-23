#!/usr/bin/env node
/**
 * Drive the hair check page (hairsim.ts) in headless Chrome over the shared CDP runner (node/scene3d/render.mjs).
 *
 *   node tools/vrm_springs/sim/run_sim.mjs metrics --packs DIR --model rel.vrm --clips idle,walk --clipdir rel
 *        --out m.json [--seconds stairs=15] [--settle 1.0]
 *   node tools/vrm_springs/sim/run_sim.mjs video --packs DIR --models a.vrm,b.vrm --clip rel/seq.json
 *        --out v.mp4 [--view 34] [--size 1280x720] [--fps 30]
 *   node tools/vrm_springs/sim/run_sim.mjs still --packs DIR --models a.vrm,b.vrm --clip rel/seq.json --at 2.5,4
 *        --out prefix  (jpg per time)
 *
 * Paths of models and clips are relative to --packs (the page fetches them as /packs/...). Physics steps at 60 Hz
 * (the preview's rAF rate), metrics and video frames every 2nd step (30 fps).
 */
import { spawn, spawnSync } from "node:child_process";
import { existsSync, readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { openScene } from "../../../node/scene3d/render.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, "../../..");
const BUNDLE = join(HERE, "dist", "hairsim.js");

function args(argv) {
  const out = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i].startsWith("--")) out[argv[i].slice(2)] = argv[++i];
    else out._.push(argv[i]);
  }
  return out;
}

function build() {
  const r = spawnSync(process.execPath, [join(ROOT, "node_modules", "esbuild", "bin", "esbuild"),
    join(HERE, "hairsim.ts"), "--bundle", "--format=iife", "--platform=browser", "--target=chrome110",
    `--outfile=${BUNDLE}`, "--log-level=warning"], { stdio: "inherit", cwd: ROOT });
  if (r.status !== 0) throw new Error("esbuild failed");
}

const a = args(process.argv.slice(2));
const mode = a._[0];
const log = (m) => process.stderr.write(`${m}\n`);
if (!existsSync(BUNDLE) || a.rebuild) build();
const packs = resolve(a.packs);
const [W, H] = (a.size || "1280x720").split("x").map(Number);
const DT = 1 / 60;

async function open(models, labels, view) {
  const spec = { width: W, height: H, view: view || "34", dist: +(a.dist || 2.1),
    models: models.map((m, i) => ({ url: `/packs/${m.replace(/\\/g, "/")}`, label: labels?.[i] || m })) };
  const sc = await openScene({ bundle: BUNDLE, packs, gl: a.gl || "gpu", post: "full", chrome: a.chrome || "" }, spec, log);
  return sc;
}

const clipJson = (rel) => readFileSync(join(packs, rel), "utf8");

async function metrics() {
  const sc = await open([a.model], [a.label || a.model]);
  const out = { model: a.model, info: sc.info.models[0], clips: {} };
  const secs = Object.fromEntries((a.seconds || "").split(",").filter(Boolean).map((s) => s.split("=")).map(([k, v]) => [k, +v]));
  try {
    for (const name of a.clips.split(",")) {
      const c = JSON.parse(clipJson(`${a.clipdir}/${name}.json`));
      const loop = !["seq", "stairs", "nod", "shake", "turn180"].includes(name);
      const dur = secs[name] ?? (c.frames - 1) / c.fps;
      await sc.evaluate(`window.__hp.clip(${JSON.stringify(c)}, ${loop})`);
      const t0 = Date.now();
      const frames = await sc.evaluate(`window.__hp.run(${dur}, ${DT}, 2, ${+(a.settle || 1.0)}, ${loop})`);
      out.clips[name] = frames.map((f) => ({ t: f.t, ...f.m[0] }));
      log(`${a.label || a.model}: ${name} ${frames.length} frames ${((Date.now() - t0) / 1000).toFixed(1)} s`);
    }
  } finally {
    await sc.close();
  }
  mkdirSync(dirname(resolve(a.out)), { recursive: true });
  writeFileSync(resolve(a.out), JSON.stringify(out));
}

async function video() {
  const models = a.models.split(",");
  const sc = await open(models, (a.labels || "").split(","), a.view);
  const fps = +(a.fps || 30);
  const every = Math.round(1 / DT / fps);
  const c = JSON.parse(clipJson(a.clip));
  const dur = +(a.duration || (c.frames - 1) / c.fps);
  const ff = spawn(a.ffmpeg || "ffmpeg", ["-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", String(fps), "-c:v", "mjpeg",
    "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-preset", "medium", resolve(a.out)], { stdio: ["pipe", "inherit", "inherit"] });
  try {
    await sc.evaluate(`window.__hp.clip(${JSON.stringify(c)}, false)`);
    await sc.evaluate(`window.__hp.settle(${+(a.settle || 1.0)}, ${DT})`);
    const n = Math.round(dur / DT);
    for (let i = 0; i <= n; i++) {
      await sc.evaluate(`window.__hp.step(${i * DT}, ${DT}, false, false)`);
      if (i % every) continue;
      await sc.evaluate("window.__hp.draw()");
      const jpg = await sc.shot("jpeg", 92);
      if (!ff.stdin.write(jpg)) await new Promise((r) => ff.stdin.once("drain", r));
    }
  } finally {
    ff.stdin.end();
    await sc.close();
  }
  await new Promise((r) => ff.on("close", r));
}

async function still() {
  const models = a.models.split(",");
  const sc = await open(models, (a.labels || "").split(","), a.view);
  const c = JSON.parse(clipJson(a.clip));
  const ats = a.at.split(",").map(Number);
  try {
    await sc.evaluate(`window.__hp.clip(${JSON.stringify(c)}, false)`);
    await sc.evaluate(`window.__hp.settle(${+(a.settle || 1.0)}, ${DT})`);
    const n = Math.round(Math.max(...ats) / DT);
    for (let i = 0; i <= n; i++) {
      await sc.evaluate(`window.__hp.step(${i * DT}, ${DT}, false, false)`);
      if (i % 2 === 0) await sc.evaluate("window.__hp.draw()");
      const hit = ats.findIndex((t) => Math.abs(t - i * DT) < DT / 2);
      if (hit < 0) continue;
      if (a.debug) {
        const r = await sc.evaluate(`window.__hp.step(${i * DT}, 0, true, false)`);
        log(`t=${ats[hit]}: ` + r.map((x) => `face ${x.face} body ${x.body} rigid ${x.bodyRigid} cloth ${x.cloth}`).join(" | "));
      }
      await sc.evaluate(`window.__hp.draw(${a.debug ? "true" : "false"})`);
      writeFileSync(`${a.out}_${ats[hit].toFixed(2)}.jpg`, await sc.shot("jpeg", 92));
    }
  } finally {
    await sc.close();
  }
}

const fn = { metrics, video, still }[mode];
if (!fn) {
  log("mode: metrics | video | still");
  process.exit(2);
}
fn().catch((e) => {
  log(String(e?.stack || e));
  process.exit(1);
});
