/**
 * Мем 10 «Сделай за ночь»: дерево файлов растёт (src/meme_painters_night.ts) — строки раскрываются без скачков, счётчик
 * считает только файлы, экран рисуется и меняется со временем; пустое место кнопки (``button_site`` ``empty``); стопка
 * документации ложится по листу (src/meme_props_night.ts, ``pileState``). Холст — @napi-rs/canvas, без браузера.
 *
 *   node --test node/scene3d/night_tree.test.mjs   (в npm run test:scene3d)
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { createCanvas, GlobalFonts } from "@napi-rs/canvas";

import { fileCount, treeLayout } from "./src/meme_painters_night.ts";
import { PROMPT_PAINTERS } from "./src/meme_painters_prompt.ts";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const FONTS = join(ROOT, "vendor", "fonts");
for (const f of JSON.parse(readFileSync(join(FONTS, "fonts.json"), "utf8"))) GlobalFonts.registerFromPath(join(FONTS, f.file), f.family);

const ENTRIES = [
  { t: 1.0, name: "db/", depth: 0, dir: true },
  { t: 1.2, name: "schema.sql", depth: 1 },
  { t: 1.4, name: "migrations/", depth: 1, dir: true },
  { t: 1.6, name: "001_init.sql", depth: 2 },
  { t: 3.0, name: "queue/", depth: 0, dir: true },
  { t: 3.2, name: "kafka.yaml", depth: 1 },
];

function frame(painter, t, p, W = 720, H = 1100) {
  const cv = createCanvas(W, H);
  PROMPT_PAINTERS[painter](cv.getContext("2d"), W, H, t, p);
  return cv;
}

function diff(a, b) {
  const x = a.getContext("2d").getImageData(0, 0, a.width, a.height).data;
  const y = b.getContext("2d").getImageData(0, 0, b.width, b.height).data;
  let n = 0;
  for (let i = 0; i < x.length; i += 4) if (Math.abs(x[i] - y[i]) + Math.abs(x[i + 1] - y[i + 1]) + Math.abs(x[i + 2] - y[i + 2]) > 30) n++;
  return n / (x.length / 4);
}

test("строки дерева раскрываются плавно, скрытые не занимают места", () => {
  assert.equal(treeLayout(ENTRIES, 0.5, 60).rows.length, 0);
  const mid = treeLayout(ENTRIES, 1.1, 60, 0.25);
  assert.equal(mid.rows.length, 1);
  assert.ok(mid.height > 0 && mid.height < 60, "строка на середине раскрытия");
  const full = treeLayout(ENTRIES, 5, 60);
  assert.equal(full.rows.length, ENTRIES.length);
  assert.equal(full.height, 60 * ENTRIES.length);
  assert.equal(full.rows[5].root, "queue", "цвет папки — по корню");
  // Высота непрерывна по времени: шаг кадра не даёт скачка больше строки.
  let prev = 0;
  for (let t = 0; t < 4; t += 1 / 30) {
    const h = treeLayout(ENTRIES, t, 60).height;
    assert.ok(h - prev <= 60 + 1e-6);
    prev = h;
  }
});

test("счётчик — только файлы, по времени", () => {
  assert.equal(fileCount(ENTRIES, 1.3), 1);
  assert.equal(fileCount(ENTRIES, 9), 3);
});

test("экран дерева рисуется и растёт; без кнопки — пустое место", () => {
  const p = { title: "кнопка-купить", entries: ENTRIES, goal: 3, fs: 40 };
  const a = frame("file_tree", 0.5, p), b = frame("file_tree", 4, p);
  assert.ok(diff(a, b) > 0.02, "дерево выросло");
  const site = frame("button_site", 2, { empty: true, at: 1, title: "Мой стартап" });
  const btn = frame("button_site", 2, { at: 1, title: "Мой стартап", label: "КУПИТЬ" });
  assert.ok(diff(site, btn) > 0.03, "без кнопки кадр другой");
  // Курсор ищет кнопку: кадры пустой страницы меняются.
  assert.ok(diff(site, frame("button_site", 2.8, { empty: true, at: 1 })) > 0.001);
});

test("стопка: листы ложатся по одному", async () => {
  const { pileState, landAt } = await import("./src/meme_props_night.ts");
  assert.equal(pileState(30, 1, 2, 0.5).landed, 0);
  assert.equal(pileState(30, 1, 2, 2.01).landed, 30);
  const s = pileState(30, 1, 2, landAt(10, 30, 1, 2) - 0.01);
  assert.equal(s.landed, 10);
  assert.equal(s.falling, 10);
  assert.ok(s.k > 0 && s.k < 1);
});
