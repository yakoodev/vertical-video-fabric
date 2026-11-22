/**
 * Мем 9 «Промпт на три страницы»: простыня в чате (src/meme_painters_prompt.ts) — разметка ``**…**``, раскладка,
 * прокрутка к абзацу, крошечный ползунок, маркер на ключевых фразах, вылет последней строки, кнопка меняет цвет.
 * Холст — @napi-rs/canvas, без браузера.
 *
 *   node --test node/scene3d/prompt_wall.test.mjs   (в npm run test:scene3d)
 *   PROMPT_WALL_PNG=<папка> — сохранить кадры для глаза
 */
import assert from "node:assert/strict";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { createCanvas, GlobalFonts } from "@napi-rs/canvas";

import { layoutWall, parseMarks, PROMPT_PAINTERS, scrollAt, styleOf } from "./src/meme_painters_prompt.ts";
import { steamPower } from "./src/meme_props_prompt.ts";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const FONTS = join(ROOT, "vendor", "fonts");
for (const f of JSON.parse(readFileSync(join(FONTS, "fonts.json"), "utf8"))) GlobalFonts.registerFromPath(join(FONTS, f.file), f.family);

const PARAS = [
  { text: "Ты — сеньор-разработчик с **тридцатилетним опытом**." },
  { text: "**Ты гений.** Ты лучший в мире." },
  { text: "Отвечай подробно, но кратко. Будь креативным, но строго по делу.", kind: "filler" },
  { text: "Сделаешь хорошо — дам **двести долларов чаевых**." },
  { text: "Пиши чистый код. Соблюдай лучшие практики.", kind: "filler" },
  { text: "Пиши чистый код. Соблюдай лучшие практики.", kind: "filler" },
  { text: "Пиши чистый код. Соблюдай лучшие практики.", kind: "filler" },
  { text: "короче, поменяй цвет кнопки на **синий**", kind: "last" },
];
const fakeMeasure = (s, px) => s.length * px * 0.55;

function frame(painter, t, p, W = 720, H = 1000) {
  const cv = createCanvas(W, H);
  PROMPT_PAINTERS[painter](cv.getContext("2d"), W, H, t, p);
  return cv;
}
const count = (cv, test) => {
  const d = cv.getContext("2d").getImageData(0, 0, cv.width, cv.height).data;
  let n = 0;
  for (let i = 0; i < d.length; i += 4) if (test(d[i], d[i + 1], d[i + 2])) n++;
  return n;
};
const yellow = (r, g, b) => r > 230 && g > 190 && g < 225 && b < 90;
const blue = (r, g, b) => b > 150 && r < 80 && g > 90 && g < 130;
const save = (name, cv) => {
  const dir = process.env.PROMPT_WALL_PNG;
  if (!dir) return;
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, `${name}.png`), cv.toBuffer("image/png"));
};

test("разметка: **…** выделяет слова, в том числе несколько подряд", () => {
  const w = parseMarks("дам **двести долларов чаевых**.");
  assert.deepEqual(w.map((x) => [x.s, x.hl]), [["дам", false], ["двести", true], ["долларов", true], ["чаевых", true], [".", false]]);
  assert.equal(w.at(-1).glue, true, "точка за выделением — без пробела");
  assert.equal(w[1].glue, undefined);
  assert.ok(styleOf("", true, 40).px > styleOf("", false, 40).px, "ключевая фраза крупнее");
  assert.ok(styleOf("last", false, 40).px < styleOf("", false, 40).px, "последняя строка мельче");
});

test("раскладка: строки не шире колонки, абзацы по порядку, высота растёт", () => {
  const wall = layoutWall(PARAS, 400, 40, fakeMeasure);
  for (const ln of wall.lines) for (const r of ln.runs) assert.ok(r.x + r.w <= 400 + 1e-6 || ln.runs.length === 1, `${r.s} вылез`);
  for (let i = 1; i < wall.paraTop.length; i++) assert.ok(wall.paraTop[i] > wall.paraTop[i - 1]);
  assert.ok(wall.height > wall.lines.at(-1).y);
  assert.equal(wall.lines.at(-1).kind, "last");
});

test("прокрутка: к абзацу — его верх на 28% окна, доля 1 — конец, между ключами плавно", () => {
  const wall = layoutWall(PARAS, 400, 40, fakeMeasure);
  const viewH = 300;
  const keys = [{ t: 0, v: 0 }, { t: 1, at: 3 }, { t: 2, v: 1 }];
  assert.equal(scrollAt(keys, 0, wall, viewH), 0);
  assert.ok(Math.abs(scrollAt(keys, 1, wall, viewH) - (wall.paraTop[3] - viewH * 0.28)) < 1e-6);
  const mid = scrollAt(keys, 1.5, wall, viewH);
  assert.ok(mid > scrollAt(keys, 1, wall, viewH) && mid < scrollAt(keys, 2, wall, viewH));
  assert.ok(Math.abs(scrollAt(keys, 5, wall, viewH) - (wall.height - viewH * 0.9)) < 1e-6);
  assert.equal(scrollAt([{ t: 0, v: 0 }, { t: 1, v: 1, ease: "step" }], 0.99, wall, viewH), 0, "step держит до ключа");
});

test("простыня: до вставки пузыря нет, маркер на ключевой фразе, ползунок крошечный, кадр зависит только от t", () => {
  const p = { paras: PARAS, paste: 0.5, fs: 44, scroll: [{ t: 0, at: 0 }, { t: 3, v: 1 }], thumb: 0.05, pages: [{ t: 0, text: "стр. 1 из 3" }] };
  const before = frame("prompt_wall", 0.2, p);
  const shown = frame("prompt_wall", 1.0, p);
  save("wall_before", before);
  save("wall_shown", shown);
  assert.equal(count(before, yellow), 0, "до вставки маркера нет");
  assert.ok(count(shown, yellow) > 2000, "маркер на «тридцатилетним опытом»");
  assert.deepEqual(shown.toBuffer("image/png"), frame("prompt_wall", 1.0, p).toBuffer("image/png"));
  // Ползунок: тёмные пиксели в полосе справа — короткий отрезок.
  const strip = createCanvas(720, 1000);
  const g = strip.getContext("2d");
  g.drawImage(shown, 0, 0);
  const d = g.getImageData(700, 0, 8, 1000).data;
  let dark = 0;
  for (let i = 0; i < d.length; i += 4) if (d[i] < 150 && d[i + 1] < 150 && d[i + 2] < 160) dark++;
  assert.ok(dark > 0 && dark / 8 < 1000 * 0.1, `ползунок ${dark / 8}px`);
});

test("простыня: последняя строка вылетает крупно — синее слово заметно больше", () => {
  const p = { paras: PARAS, paste: 0, fs: 44, scroll: [{ t: 0, v: 1 }], zoom: [{ t: 0, v: 0 }, { t: 1, v: 1 }] };
  const small = frame("prompt_wall", 0, p);
  const big = frame("prompt_wall", 1.2, p);
  save("wall_last_small", small);
  save("wall_last_big", big);
  const bs = count(small, blue), bb = count(big, blue);
  assert.ok(bs > 0, "«синий» видно и мелко");
  assert.ok(bb > bs * 3, `крупно ${bb} против ${bs}`);
});

test("кнопка: серая до клика, синяя после; пар слабеет до нуля", () => {
  const p = { at: 1.0, label: "КУПИТЬ", from: "#9aa1b0", to: "#2f6fb5", toast: "готово" };
  const a = frame("button_site", 0.5, p), b = frame("button_site", 1.6, p);
  save("button_before", a);
  save("button_after", b);
  assert.ok(count(b, blue) > count(a, blue) + 20000);
  const keys = [{ t: 0, v: 1 }, { t: 2, v: 0.3 }, { t: 3, v: 0 }];
  assert.equal(steamPower(keys, -1), 1);
  assert.ok(Math.abs(steamPower(keys, 1) - 0.65) < 1e-9);
  assert.equal(steamPower(keys, 9), 0);
});
