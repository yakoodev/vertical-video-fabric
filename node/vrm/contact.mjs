#!/usr/bin/env node
/**
 * Проверка касаний по всем кадрам ролика (без снимков): пальцы и запястья не ниже поверхностей
 * spec.timeline.surfaces (стол, клавиатура) с допуском 4 мм на толщину пальца. Печатает JSON
 * {ok, frames, min_gap_mm, at, point, surface}; код выхода 1 — есть кадр с нарушением.
 *
 * Руки против тела (src/handcheck.ts): кисти, пальцы, ладонь и предплечье против лица/головы, корпуса, ног и
 * другой руки — без допуска (зазор < 0 — проникновение); волосы — отдельной строкой (пружины, не валит проверку).
 * --hands-out FILE — список отрезков с проникновением (JSON) для разбора по кадрам.
 *
 * node node/vrm/contact.mjs --spec node/vrm/examples/motion_test_v2.json [--packs DIR] [--no-clamp] [--hands-out F]
 * [--from S --to S] — только отрезок ролика (быстрый разбор одного плана).
 * --no-clamp — без подъёма кистей (показать, что проверка ловит проход сквозь клавиатуру).
 * Пустые замеры (нет кадров, нет зазоров пальцев/кресла/волос/рук) или кадры без модели — ok: false, поле empty.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { openScene } from "../scene3d/render.mjs";
import { spans } from "./handspans.mjs";
import { emptyChecks } from "./checks.mjs";
import { frameProblem } from "../scene3d/render.mjs";

export const MIN_GAP = 0.004;

const HERE = dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const get = (k, d) => (args.includes(k) ? args[args.indexOf(k) + 1] : d);
const spec = JSON.parse(readFileSync(resolve(get("--spec")), "utf8"));
spec.strictContact = false;
if (args.includes("--no-clamp")) spec.timeline.clamp = false;
const fps = spec.fps || 30, n = Math.round((spec.duration || 10) * fps);
const i0 = Math.max(0, Math.round(Number(get("--from", 0)) * fps)), i1 = Math.min(n, Math.round(Number(get("--to", n / fps)) * fps));
const scene = await openScene({ bundle: resolve(get("--bundle", join(HERE, "dist", "vrm.js"))), packs: resolve(get("--packs", "D:/vvf-offload/vtubika/vrm/vendor")),
  gl: get("--gl", "gpu"), post: "full", chrome: "" }, spec, () => {});
let c;
let heroFrames = 0;
const presenceBad = [];
try {
  for (let i = i0; i < i1; i++) {
    const st = await scene.render(i / fps);
    if (st?.presence?.expect > 0) heroFrames++;
    const problem = frameProblem(st);
    if (problem) presenceBad.push(`кадр ${i}: ${problem}`);
  }
  c = await scene.evaluate("window.__scene3d.contact()");
} finally {
  await scene.close();
}
const mm = (v) => (Number.isFinite(v) ? Math.round(v * 10000) / 10 : null);
const H = c.hands || { gaps: {}, bad: [] };
const hs = spans(H.bad.filter((b) => b.key !== "hair"), fps);
if (get("--hands-out")) writeFileSync(resolve(get("--hands-out")), JSON.stringify({ spans: spans(H.bad, fps), gaps: H.gaps, groups: H.groups }, null, 1));
const full = i0 === 0 && i1 === n;
// Пустой замер — не «ок»: проверка, которая ничего не измерила, валит ролик (checks.mjs).
const empty = emptyChecks(c, spec, heroFrames, presenceBad);
const ok = (!full || c.frames === n) && c.min >= MIN_GAP && !(c.chair.min < 0) && hs.length === 0 && empty.length === 0;
process.stdout.write(JSON.stringify({ ok, frames: c.frames, fingers: { min_gap_mm: mm(c.min), at: c.at, point: c.point, surface: c.surface },
  chair: { min_gap_mm: mm(c.chair.min), at: c.chair.at, point: c.chair.point },
  hair: { min_gap_mm: mm(c.hair.min), at: c.hair.at, point: c.hair.point },
  hands: Object.fromEntries(Object.entries(H.gaps || {}).map(([k, g]) => [k, { min_gap_mm: mm(g.min), at: g.at, point: g.point, against: g.against }])),
  hand_spans: hs.length, hero_frames: heroFrames, empty }) + "\n");
if (empty.length) process.stderr.write(`проверки пустые: ${empty.join("; ")}\n`);
process.exit(ok ? 0 : 1);
