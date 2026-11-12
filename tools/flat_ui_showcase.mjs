// Витрина плоской темы: лист «было / стало» по компонентам и композиция демо-ролика в стиле рефа «ошибки»
// (хук-слово → карточки с числами → чек-лист → уведомление) поверх живой сцены с размытым фоном под плашками.
//
//   node tools/flat_ui_showcase.mjs --bg frame.png --out-dir D:\vvf-offload\tmp\flat_ui
//
// Пишет before_after.jpg (кадры 1080×1920 движка, обрезанные вокруг компонента) и demo_comp.json — композицию
// для компоновщика (node node/motion-render.mjs --comp demo_comp.json --input <основа> --output demo.mp4).
// Слои — из шаблонов галереи (buildTemplate), как их ставит ИИ-монтажёр: одна правда с сервисом.
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";
import { createCanvas, GlobalFonts, loadImage } from "@napi-rs/canvas";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const arg = (k, d) => {
  const i = process.argv.indexOf(k);
  return i > 0 ? process.argv[i + 1] : d;
};
const OUT = arg("--out-dir", ".");
const BG = arg("--bg", null);
const BEFORE3D = arg("--before-3d", null);

const tmp = mkdtempSync(join(tmpdir(), "vvf-flat-show-"));
const entry = join(tmp, "engine.mjs");
await build({ entryPoints: [join(ROOT, "frontend", "src", "motion", "index.ts")], bundle: true, platform: "node", format: "esm", outfile: entry, logLevel: "warning" });
const E = await import(pathToFileURL(entry).href);
const FONTS = join(ROOT, "vendor", "fonts");
for (const f of JSON.parse(readFileSync(join(FONTS, "fonts.json"), "utf8"))) GlobalFonts.registerFromPath(join(FONTS, f.file), f.family);
const bg = BG ? await loadImage(BG) : null;
const env = { width: 1080, height: 1920, getImage: () => null, createCanvas: (a, b) => createCanvas(a, b) };

function frameOf(layers, t) {
  const cv = createCanvas(1080, 1920);
  const g = cv.getContext("2d");
  if (bg) g.drawImage(bg, 0, 0, 1080, 1920);
  else {
    g.fillStyle = "#3a2a30";
    g.fillRect(0, 0, 1080, 1920);
  }
  E.drawFrame(g, { layers }, t, env);
  return cv;
}

/* ---------- было / стало ---------- */

// [подпись, старый шаблон + параметры, новый шаблон + параметры, момент, окно обрезки y0..y1 (px кадра)]
const PAIRS = [
  ["Хук-слово", ["title-pop", { text: "НАБИРАЮТ" }], ["flat-hook", { word: "НАБИРАЮТ", fill: false }], 1.0, 640, 1280],
  ["Счётчик", ["counter", { to: 942692, label: "просмотров" }], ["counter", { to: 942692, label: "Просмотры за неделю", theme: "red" }], 3.5, 560, 1140],
  ["Прогресс", ["progress", { title: "Алгоритм изучает", value: 35 }], ["progress", { title: "Алгоритм изучает аккаунт", value: 35, theme: "dark" }], 3.5, 560, 1220],
  ["Плашка", ["lower-third-box", { title: "Скучное начало", subtitle: "Ошибка 1", y: 0.17 }], ["flat-error", { chip: "Ошибка 1", title: "Скучное начало", sub: "зритель листает дальше", y: 0.15 }], 2.5, 80, 620],
  ["Список", ["top-3", { title: "3 ошибки", item1: "Скучное начало", item2: "Сдаёшься рано", item3: "Короче минуты" }], ["top-3", { title: "3 ошибки", item1: "Скучное начало", item2: "Сдаёшься рано", item3: "Короче минуты", theme: "red" }], 4.5, 380, 1100],
];

const CELL_W = 540;
const crops = PAIRS.map(([name, [oid, op], [nid, np], t, y0, y1]) => {
  const old = frameOf(E.buildTemplate(oid, op, 0).layers, t);
  const neu = frameOf(E.buildTemplate(nid, np, 0).layers, t);
  return { name, old, neu, y0, y1 };
});
let extra = null;
if (BEFORE3D) extra = await loadImage(BEFORE3D);
const rowH = crops.map((c) => Math.round(((c.y1 - c.y0) * CELL_W) / 1080));
const headH = 84;
const labelH = 46;
const extraH = extra ? Math.round((extra.height * (CELL_W * 2 + 24)) / extra.width) + labelH + 24 : 0;
const sheet = createCanvas(CELL_W * 2 + 24 + 48, headH + rowH.reduce((a, b) => a + b + labelH + 24, 0) + extraH + 24);
const s = sheet.getContext("2d");
s.fillStyle = "#101014";
s.fillRect(0, 0, sheet.width, sheet.height);
s.fillStyle = "#ffffff";
s.font = '900 34px "Montserrat"';
s.textAlign = "center";
s.fillText("БЫЛО", 24 + CELL_W / 2, 58);
s.fillStyle = "#ff2d46";
s.fillText("СТАЛО — плоская тема", 24 + CELL_W * 1.5 + 24, 58);
let y = headH;
crops.forEach((c, i) => {
  s.fillStyle = "#ffffffb0";
  s.font = '700 26px "Golos Text"';
  s.textAlign = "left";
  s.fillText(c.name, 24, y + 32);
  y += labelH;
  s.drawImage(c.old, 0, c.y0, 1080, c.y1 - c.y0, 24, y, CELL_W, rowH[i]);
  s.drawImage(c.neu, 0, c.y0, 1080, c.y1 - c.y0, 24 + CELL_W + 24, y, CELL_W, rowH[i]);
  y += rowH[i] + 24;
});
if (extra) {
  s.fillStyle = "#ffffffb0";
  s.font = '700 26px "Golos Text"';
  s.fillText("Экраны в 3D: браузер, счётчик, уведомление (слева было, справа стало)", 24, y + 32);
  y += labelH;
  s.drawImage(extra, 24, y, CELL_W * 2 + 24, extraH - labelH - 24);
}
writeFileSync(join(OUT, "before_after.jpg"), sheet.toBuffer("image/jpeg", 90));

/* ---------- демо-ролик ---------- */

const T = (id, params, at) => E.buildTemplate(id, params, at).layers;
const theme = { theme: "red" };
const layers = [
  ...T("flat-hook", { word: "НАБИРАЮТ", sub: "", fill: true, duration: 1.1, ...theme }, 0),
  // Фон под плашками — один слой на всё время карточек: сцена размыта, плашки читаются.
  ...T("flat-backdrop", { blur: 24, dim: 0.5, duration: 11.9 }, 1.1),
  ...T("flat-error", { chip: "Ошибка 1", title: "Скучное начало", y: 0.15, duration: 3.3, backdrop: false, ...theme }, 1.1),
  ...T("flat-stat", { label: "Досматривают первые 3 секунды", icon: "timer", value: 23, from: 0, suffix: "%", delta: "−41%", progress: 23, caption: "зритель листает дальше на первой фразе", y: 0.42, duration: 3.0, backdrop: false, ...theme }, 1.4),
  ...T("flat-error", { chip: "Ошибка 2", title: "Короче минуты", y: 0.15, duration: 3.2, backdrop: false, ...theme }, 4.4),
  ...T("flat-compare", { label: "Монетизация TikTok", rows: "видео 0:50 | 50 | $0; видео 1:04 | 64 | платит ✓", max: 70, marker: 60, markerLabel: "1:00", y: 0.42, duration: 2.9, backdrop: false, ...theme }, 4.7),
  ...T("flat-checklist", { app: "Claude", title: "Исправляю 3 ошибки", items: "Цепляющее начало | крючок в первые 3 секунды; Видео каждый день | готовый ролик за 15 минут; Длина под монетизацию | ролик 1:04 вместо 0:50", stagger: 0.75, y: 0.4, duration: 3.6, backdrop: false, ...theme }, 7.6),
  ...T("flat-toast", { app: "Студия", text: "Твой ролик набрал 942 692 просмотра", time: "сейчас", icon: "trending-up", badge: 3, glass: "dark", y: 0.12, duration: 2.0, ...theme }, 11.0),
  ...T("flat-counter", { value: 942692, from: 120000, unit: "просмотров", icon: "eye", style: "accent", y: 0.45, duration: 1.8, ...theme }, 11.2),
];
writeFileSync(join(OUT, "demo_comp.json"), JSON.stringify({ width: 1080, height: 1920, fps: 30, duration: 13, layers }, null, 1));
// Кадры-проверки демо (лист).
const at = [0.3, 2.6, 5.8, 9.6, 12.4];
const thumbs = createCanvas(at.length * 360, 640);
const tg = thumbs.getContext("2d");
at.forEach((t, i) => tg.drawImage(frameOf(layers, t), i * 360, 0, 360, 640));
writeFileSync(join(OUT, "demo_sheet_preview.jpg"), thumbs.toBuffer("image/jpeg", 88));
rmSync(tmp, { recursive: true, force: true });
console.log("ok", join(OUT, "before_after.jpg"), join(OUT, "demo_comp.json"));
