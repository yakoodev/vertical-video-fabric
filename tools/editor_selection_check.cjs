/*
 * Браузерная проверка: выделение в инспекторе и правка переживают свою запись во время чтения клипа.
 *
 * Как ломалось: опрос ревизии видит свою же запись и перечитывает запись; пока ответ едет, уходит следующая правка.
 * Опоздавший ответ старее панели, он выглядел как правка извне: панель откатывалась на него, выделение снималось.
 * Здесь порядок задан жёстко: ответ чтения держим, пока не вернётся запись следующей правки.
 *
 * Только против локальной копии сервиса, никогда не против живого :8088: редактор автосохраняет. Запуск:
 *
 *   node tools/editor_selection_check.cjs <id видео> <id клипа с текстовым слоем> [папка для кадров]
 *
 * Окружение: VVF_UI (адрес копии), VVF_TOKEN_FILE (токен копии, если вход включён), PLAYWRIGHT_CORE, CHROME.
 * Код выхода 1, если выделение или правка пропали.
 */
const fs = require("fs");
const path = require("path");

const BASE = (process.env.VVF_UI || "http://127.0.0.1:8496").replace(/\/$/, "");
const { chromium } = require(process.env.PLAYWRIGHT_CORE || "D:/vvf-offload/tmp/stream_preview/browser/node_modules/playwright-core");
const CHROME = process.env.CHROME || "C:/Program Files/Google/Chrome/Application/chrome.exe";
const [SOURCE, CLIP, OUT = "D:/vvf-offload/tmp/editor_selection"] = process.argv.slice(2);
if (!SOURCE || !CLIP) throw new Error("нужны id видео и id клипа");
fs.mkdirSync(OUT, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const browser = await chromium.launch({ executablePath: CHROME, headless: true, args: ["--disable-gpu", "--mute-audio", "--no-proxy-server"] });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: "dark", locale: "ru-RU" });
  if (process.env.VVF_TOKEN_FILE) {
    await context.addCookies([{ name: "vvf_token", value: fs.readFileSync(process.env.VVF_TOKEN_FILE, "utf8").trim(), domain: new URL(BASE).hostname, path: "/" }]);
  }
  // Придержать ответ чтения записи: сервер читает базу сразу, браузер получает ответ, когда отпустят.
  let hold = null;
  let held = () => undefined;
  const heldRead = new Promise((r) => (held = r));
  await context.route(/.*/, async (route) => {
    const req = route.request();
    const u = new URL(req.url());
    if (u.origin !== BASE && u.protocol !== "data:" && u.protocol !== "blob:") return route.abort();
    if (hold && req.method() === "GET" && u.pathname === `/api/sources/${SOURCE}`) {
      const response = await route.fetch();
      held();
      await hold;
      return route.fulfill({ response }).catch(() => undefined);
    }
    return route.continue();
  });
  const page = await context.newPage();
  await page.goto(`${BASE}/v/${SOURCE}/candidates?clip=${CLIP}`, { waitUntil: "domcontentloaded" });
  await page.locator(".ed").first().waitFor({ state: "attached", timeout: 20000 });
  await page.waitForLoadState("networkidle", { timeout: 6000 }).catch(() => undefined);
  await sleep(2500); // начальное сохранение панели и первая ревизия
  await page.locator(".insp-tab", { hasText: "Слои" }).first().click();
  const list = page.locator(".insp-pane:not([hidden])").getByText("Слои клипа").first();
  if (!(await page.locator(".ed-layer-row").count())) await list.click();
  await page.locator(".ed-layer-row").first().click();
  const field = page.locator(".insp-pane:not([hidden]) textarea").first();
  await field.waitFor({ timeout: 5000 });

  const saved = () => page.waitForResponse((r) => r.request().method() === "PATCH" && r.url().endsWith(`/clip-plans/${CLIP}/render-settings`), { timeout: 8000 });
  const edit = async (ch) => {
    const done = saved();
    await field.focus();
    await page.keyboard.press("Control+End");
    await page.keyboard.type(ch);
    await done;
  };
  let release = () => undefined;
  hold = new Promise((r) => (release = r));
  await edit("А"); // опрос увидит эту запись и начнёт чтение: ответ придержан
  await Promise.race([heldRead, sleep(6000).then(() => { throw new Error("чтение после записи не началось"); })]);
  await edit("Б"); // запись новее придержанного чтения
  const typed = await field.inputValue();
  hold = null;
  release();
  await sleep(600);
  const on = await page.locator(".ed-layer-row.on").count();
  const after = on ? await field.inputValue() : "";
  await page.screenshot({ path: path.join(OUT, on ? "selection-ok.png" : "selection-lost.png") });
  // Настоящая правка извне (агент, другая вкладка) по-прежнему доезжает до панели сама.
  await sleep(3000);
  const plan = (await (await context.request.get(`${BASE}/api/sources/${SOURCE}`)).json()).clip_plans.find((p) => String(p.id) === CLIP);
  const outside = `извне ${Date.now() % 10000}`;
  const settings = { ...plan.render_settings, layers: plan.render_settings.layers.map((l, n) => (n === 0 ? { ...l, name: outside } : l)) };
  await context.request.patch(`${BASE}/api/clip-plans/${CLIP}/render-settings`, { data: { settings } });
  const arrived = await page.locator(".ed-layer-row", { hasText: outside }).first().waitFor({ timeout: 8000 }).then(() => true, () => false);
  await browser.close();
  const ok = on === 1 && after === typed && arrived;
  console.log(ok ? "ok: выделение и правка на месте, правка извне доехала" : `СЛОМАНО: выделено ${on}, в поле ${JSON.stringify(after)}, ожидали ${JSON.stringify(typed)}, правка извне ${arrived ? "доехала" : "не доехала"}`);
  process.exit(ok ? 0 : 1);
})().catch((e) => {
  console.error(e);
  process.exit(2);
});
