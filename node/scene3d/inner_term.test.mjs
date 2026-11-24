/**
 * Мем 12 «Про себя»: терминал легаси-проекта (src/meme_painters_inner.ts) — фаза по времени, счётчик файлов добегает
 * ровно до total, экран рисуется в каждой фазе и меняется между ними. Холст — @napi-rs/canvas, без браузера.
 *
 *   node --test node/scene3d/inner_term.test.mjs   (в npm run test:scene3d)
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { createCanvas, GlobalFonts } from "@napi-rs/canvas";

import { countUp, fileName, phaseAt } from "./src/meme_painters_inner.ts";
import { PROMPT_PAINTERS } from "./src/meme_painters_prompt.ts";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const FONTS = join(ROOT, "vendor", "fonts");
for (const f of JSON.parse(readFileSync(join(FONTS, "fonts.json"), "utf8"))) GlobalFonts.registerFromPath(join(FONTS, f.file), f.family);

const PHASES = [
  { t: 1, kind: "files", total: 400, dur: 1.5 },
  { t: 4, kind: "final", file: "final_final_2.js" },
  { t: 6, kind: "tests", total: 147, stampAt: 0.9 },
  { t: 9, kind: "run", total: 147, dur: 1.0 },
  { t: 10.5, kind: "pass", total: 147 },
];

test("фаза по времени: последняя начавшаяся, до первой — первая", () => {
  assert.equal(phaseAt(PHASES, 0).ph.kind, "files");
  assert.equal(phaseAt(PHASES, 4.0).ph.kind, "final");
  assert.equal(phaseAt(PHASES, 8.9).ph.kind, "tests");
  assert.equal(phaseAt(PHASES, 20).ph.kind, "pass");
  assert.ok(Math.abs(phaseAt(PHASES, 7.5).local - 1.5) < 1e-9);
});

test("счётчик добегает ровно до total и не убывает", () => {
  let prev = -1;
  for (let i = 0; i <= 40; i++) {
    const n = countUp(400, i * 0.05, 1.5);
    assert.ok(n >= prev);
    prev = n;
  }
  assert.equal(countUp(400, 1.5, 1.5), 400);
  assert.equal(countUp(400, 0, 1.5), 0);
});

test("каждый третий файл — jQuery", () => {
  const jq = Array.from({ length: 60 }, (_, i) => fileName(i)).filter((f) => f.jq);
  assert.equal(jq.length, 20);
  assert.ok(jq.every((f) => f.name.startsWith("jquery")));
});

test("экран рисуется в каждой фазе и отличается между фазами", () => {
  const paint = PROMPT_PAINTERS.inner_term;
  assert.ok(paint, "inner_term в реестре экранов");
  const sums = [];
  for (const t of [1.2, 4.5, 7.5, 9.5, 11]) {
    const c = createCanvas(360, 560);
    paint(c.getContext("2d"), 360, 560, t, { phases: PHASES });
    const px = c.getContext("2d").getImageData(0, 0, 360, 560).data;
    let s = 0;
    for (let i = 0; i < px.length; i += 97) s += px[i];
    sums.push(s);
  }
  assert.equal(new Set(sums).size, sums.length);
});
