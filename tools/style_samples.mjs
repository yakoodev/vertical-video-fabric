// Образцы карточек стилей ролика (app/style_cards.json): по каждому стилю крошечный ролик 3 с (270×480) и лист кадров.
//
//   node tools/style_samples.mjs --out-dir D:\vvf-offload\tmp\styles [--only riso] [--fps 24]
//
// Рисует тот же движок, что редактор и рендер (frontend/src/motion): фон стиля, заголовок или шаблон из карточки,
// субтитры-слой с видом стиля и эффект стиля на весь кадр (две краски, растр) — как их ставит apply_style.
// Размер маленький нарочно (ПК занят сервисом): 270×480, один ролик за раз; ffmpeg пишет кадры со stdin.
import { mkdtempSync, readFileSync, rmSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";
import { createCanvas, GlobalFonts } from "@napi-rs/canvas";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const arg = (k, d) => {
  const i = process.argv.indexOf(k);
  return i > 0 ? process.argv[i + 1] : d;
};
const OUT = arg("--out-dir", ".");
const ONLY = arg("--only", "");
const FPS = Number(arg("--fps", "24"));
const W = 270;
const H = 480;
const DUR = 3;
mkdirSync(OUT, { recursive: true });

const tmp = mkdtempSync(join(tmpdir(), "vvf-styles-"));
const entry = join(tmp, "engine.mjs");
await build({ entryPoints: [join(ROOT, "frontend", "src", "motion", "index.ts")], bundle: true, platform: "node", format: "esm", outfile: entry, logLevel: "warning" });
const E = await import(pathToFileURL(entry).href);
const FONTS = join(ROOT, "vendor", "fonts");
for (const f of JSON.parse(readFileSync(join(FONTS, "fonts.json"), "utf8"))) GlobalFonts.registerFromPath(join(FONTS, f.file), f.family);
const cards = JSON.parse(readFileSync(join(ROOT, "app", "style_cards.json"), "utf8")).styles.filter((c) => !ONLY || c.id === ONLY);
const env = { width: W, height: H, getImage: () => null, createCanvas: (a, b) => createCanvas(a, b) };

/** Слова субтитров: равномерно по 3 с, с 0.3 с. */
function words(list) {
  const step = (DUR - 0.5) / list.length;
  return list.map((w, i) => ({ w, t0: 0.3 + i * step, t1: 0.3 + (i + 1) * step - 0.05 }));
}

function layersOf(card) {
  const a = card.apply;
  const s = card.sample;
  const out = [];
  if (s.title) {
    out.push({
      id: "title", type: "text", start: 0, end: DUR, x: 0.5, y: s.title.y ?? 0.38, track: 1,
      in: { type: a.entrances.title || "pop", duration: 0.4 },
      props: { text: s.title.text, font: s.title.font, weight: 900, size: s.title.size, color: s.title.color, align: "center" },
    });
  }
  if (s.template) {
    const built = E.buildTemplate(s.template.id, { ...s.template.params, duration: DUR - 0.2 }, 0.2);
    for (const l of built.layers) out.push({ ...l, track: Math.min(l.track ?? 1, 5) });
  }
  out.push({ id: "caps", type: "captions", start: 0, end: DUR, x: 0.5, y: 0.72, track: a.caption_track ?? 2, props: a.captions });
  if (a.fx) {
    const { track, ...props } = a.fx;
    out.push({ id: "style_fx", type: "fx", start: 0, end: DUR, track: track ?? 7, props });
  }
  return out;
}

function frame(card, layers, wds, t) {
  const cv = createCanvas(W, H);
  const g = cv.getContext("2d");
  E.paintBackground(g, E.normalizeBackground(card.sample.background), t, W, H, { createCanvas: env.createCanvas });
  E.drawFrame(g, { layers, words: wds }, t, env);
  return cv;
}

async function video(card, layers, wds, path) {
  const ff = spawn("ffmpeg", ["-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", `${W}x${H}`, "-r", String(FPS), "-i", "-",
    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", path], { stdio: ["pipe", "inherit", "inherit"] });
  const n = Math.round(DUR * FPS);
  for (let i = 0; i < n; i++) {
    const cv = frame(card, layers, wds, i / FPS);
    const data = cv.getContext("2d").getImageData(0, 0, W, H).data;
    if (!ff.stdin.write(Buffer.from(data.buffer, data.byteOffset, data.byteLength))) await new Promise((r) => ff.stdin.once("drain", r));
  }
  ff.stdin.end();
  await new Promise((res, rej) => ff.on("close", (code) => (code === 0 ? res() : rej(new Error(`ffmpeg ${code}`)))));
}

const TIMES = [0.15, 1.2, 2.6];
const LABEL = 26;
const sheet = createCanvas(W * TIMES.length, (H + LABEL) * cards.length);
const sg = sheet.getContext("2d");
sg.fillStyle = "#111";
sg.fillRect(0, 0, sheet.width, sheet.height);
for (const [row, card] of cards.entries()) {
  const layers = layersOf(card);
  const wds = words(card.sample.words);
  await video(card, layers, wds, join(OUT, `${card.id}.mp4`));
  sg.fillStyle = "#eee";
  sg.font = "bold 15px Inter";
  sg.fillText(`${card.short} · t = ${TIMES.join(" / ")} с`, 8, row * (H + LABEL) + 18);
  TIMES.forEach((t, col) => sg.drawImage(frame(card, layers, wds, t), col * W, row * (H + LABEL) + LABEL));
  process.stderr.write(`${card.id}: ${join(OUT, card.id + ".mp4")}\n`);
}
const { writeFileSync } = await import("node:fs");
writeFileSync(join(OUT, ONLY ? `sheet_${ONLY}.png` : "sheet.png"), sheet.toBuffer("image/png"));
rmSync(tmp, { recursive: true, force: true });
process.stderr.write(`лист: ${join(OUT, ONLY ? `sheet_${ONLY}.png` : "sheet.png")}\n`);
