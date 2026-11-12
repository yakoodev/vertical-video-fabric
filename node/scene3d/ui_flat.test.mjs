/**
 * Плоские экраны 3D на токенах плоской темы (src/ui_flat.ts ↔ frontend/src/motion/flat): палитры те же, что у
 * 2D-слоя ui; монитор с браузером/чатом, уведомления, ярлык и HUD рисуются плоско по умолчанию и прежним видом
 * при theme "toon". Холст — @napi-rs/canvas, без браузера.
 *
 *   node --test node/scene3d/ui_flat.test.mjs   (в npm run test:scene3d)
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { createCanvas, GlobalFonts } from "@napi-rs/canvas";

import { flatTheme as flat2d } from "../../frontend/src/motion/flat/tokens.ts";
import { drawBrowser, PANEL_PAINTERS } from "./src/desk_ui.ts";
import { drawHud, drawTag, drawToast } from "./src/ui.ts";
import { flatComponent, isToon, screenPalette } from "./src/ui_flat.ts";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const FONTS = join(ROOT, "vendor", "fonts");
for (const f of JSON.parse(readFileSync(join(FONTS, "fonts.json"), "utf8"))) GlobalFonts.registerFromPath(join(FONTS, f.file), f.family);

const png = (cv) => cv.toBuffer("image/png");
function canvas(w, h, fn) {
  const cv = createCanvas(w, h);
  fn(cv.getContext("2d"));
  return cv;
}
const ink = (cv) => {
  const d = cv.getContext("2d").getImageData(0, 0, cv.width, cv.height).data;
  let n = 0;
  for (let i = 3; i < d.length; i += 4) if (d[i] > 10) n++;
  return n;
};
const px = (cv, x, y) => Array.from(cv.getContext("2d").getImageData(x, y, 1, 1).data);

test("палитры экранов — те же токены, что у 2D-слоя ui; toon — прежний вид", () => {
  for (const name of ["red", "dark", "light"]) assert.deepEqual(screenPalette(name, "dark"), flat2d(name));
  assert.deepEqual(screenPalette(undefined, "light", "#c96f4a"), flat2d("light", { accent: "#c96f4a" }));
  assert.equal(isToon("toon"), true);
  assert.equal(isToon(undefined), false);
});

const msgs = [{ t: 0, from: "user", text: "Сделай сайт" }, { t: 0, from: "bot", text: "Готово" }];
const spec = (theme) => ({ theme, tabs: [{ title: "Клод", letter: "К", url: "claude.ai", page: { name: "Клод", accent: "#c96f4a", letter: "К", messages: msgs } }] });

test("браузер на мониторе: плоская тема по умолчанию (точки окна, белая страница), toon — тёплая бумага", () => {
  const flat = canvas(1280, 800, (g) => drawBrowser(g, 1280, 800, spec(undefined), 2, 0, null));
  const toon = canvas(1280, 800, (g) => drawBrowser(g, 1280, 800, spec("toon"), 2, 0, null));
  assert.notDeepEqual(png(flat), png(toon));
  // Первая точка «светофора» окна — красная (#ff5f57); у toon её нет.
  const [r, g, b] = px(flat, 36, Math.round(12 + 78 * 0.47));
  assert.ok(r > 220 && g < 120 && b < 120, `точка окна ${r},${g},${b}`);
  // Страница плоской темы — белая, у toon — бумага #fbf7f1.
  assert.deepEqual(px(flat, 640, 500).slice(0, 3), [255, 255, 255]);
  assert.notDeepEqual(px(toon, 640, 500).slice(0, 3), [255, 255, 255]);
  // Тёмная тема перекрашивает окно целиком.
  const dark = canvas(1280, 800, (g) => drawBrowser(g, 1280, 800, spec("dark"), 2, 0, null));
  assert.ok(px(dark, 640, 500)[0] < 60);
});

test("счётчик просмотров на мониторе — пилюля ui counter, уведомление, ярлык и HUD рисуются плоско", () => {
  const ed = canvas(1280, 800, (g) => PANEL_PAINTERS.editor(g, 1280, 800, 3, { from: 120000, to: 942692 }));
  const edToon = canvas(1280, 800, (g) => PANEL_PAINTERS.editor(g, 1280, 800, 3, { theme: "toon", from: 120000, to: 942692 }));
  assert.notDeepEqual(png(ed), png(edToon));
  for (const theme of [undefined, "toon", "red"]) {
    const cv = canvas(1080, 600, (g) => {
      drawToast(g, 1, 540, 20, { t0: 0, t1: 9, title: "ChatGPT", text: "Отличная идея", icon: "message-circle", theme }, 3);
      drawTag(g, 1, 40, 300, "Будни Клода", theme);
      drawHud(g, 1, 1040, 400, { label: "Бюджет", values: [{ t: 0, value: 175 }], theme }, 3);
    });
    assert.ok(ink(cv) > 20000, `тема ${theme}`);
  }
  const size = canvas(400, 200, (g) => {
    const box = flatComponent(g, { component: "chip", title: "Ошибка 1", theme: "red" }, 200, 100, 1, 1);
    assert.ok(box.w > 150 && box.h > 50);
  });
  assert.ok(ink(size) > 3000);
});
