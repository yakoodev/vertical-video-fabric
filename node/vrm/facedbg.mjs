// node node/vrm/facedbg.mjs spec.json t1,t2,… — веса выражений лица (sequencePose + facemix) по кадрам, без браузера.
import { build } from "esbuild";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const dir = mkdtempSync(join(tmpdir(), "vrm-face-dbg-"));
const out = join(dir, "m.mjs");
await build({ stdin: { contents: `export { sequencePose } from "./src/sequence.ts";`, resolveDir: HERE, loader: "ts" },
  bundle: true, format: "esm", platform: "node", outfile: out, logLevel: "error" });
const M = await import(pathToFileURL(out).href);
rmSync(dir, { recursive: true, force: true });
const spec = JSON.parse(readFileSync(process.argv[2], "utf8"));
const handles = ["browJoy", "browFun", "browSorrow", "browAngry", "browSurprised", "eyeJoy", "eyeSorrow", "eyeAngry", "eyeSurprised",
  "mouthJoy", "mouthFun", "mouthSorrow", "mouthAngry", "mouthSurprised"];
for (const t of process.argv[3].split(",").map(Number)) {
  const p = M.sequencePose(spec.timeline, {}, t, { cam: [0, 1.4, 2.6], leg: 0.8, face: { handles } });
  const e = Object.fromEntries(Object.entries(p.expr).filter(([, v]) => v > 0.01).map(([k, v]) => [k, Math.round(v * 100) / 100]));
  console.log(t.toFixed(2), JSON.stringify(e));
}
