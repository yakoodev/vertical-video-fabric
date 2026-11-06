/*
 * Браузерная проверка пяти починенных багов редактора (шаг 7): правка строки субтитров, шаг вперёд,
 * таймкод на первом кадре, переключатель «скрытые», Esc в «Смотреть монтаж».
 *
 * Только против локальной копии сервиса с копией базы, никогда не против живого :8088: редактор
 * автосохраняет, а проверка скрывает черновики и правит субтитры. Запуск:
 *
 *   node tools/editor_bugs_check.cjs <метка>
 *
 * Окружение: как у tools/editor_check.cjs: VVF_UI, VVF_TOKEN_FILE, VVF_CHECK_OUT, PLAYWRIGHT_CORE, CHROME.
 * Итог: <OUT>/<метка>/bugs/result.json и кадры. Код выхода 1, если что-то упало.
 */
const fs = require("fs");
const path = require("path");

const BASE = (process.env.VVF_UI || "http://127.0.0.1:8496").replace(/\/$/, "");
const OUT = process.env.VVF_CHECK_OUT || "D:/vvf-offload/tmp/step6fe/results";
const TOKEN = fs.readFileSync(process.env.VVF_TOKEN_FILE || "D:/vvf-offload/tmp/step6fe/test_token.txt", "utf8").trim();
const { chromium } = require(process.env.PLAYWRIGHT_CORE || "D:/vvf-offload/tmp/stream_preview/browser/node_modules/playwright-core");
const CHROME = process.env.CHROME || "C:/Program Files/Google/Chrome/Application/chrome.exe";

// Копия базы: видео 40, клипы со словами.
const VID = 40;
const CLIPS_WITH_WORDS = [545, 546, 548, 549];
const CLIP = 549;

const label = process.argv[2] || "run";
const dir = path.join(OUT, label, "bugs");
fs.rmSync(dir, { recursive: true, force: true });
fs.mkdirSync(dir, { recursive: true });
const result = { label, steps: [], errors: [] };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const browser = await chromium.launch({
    executablePath: CHROME,
    headless: true,
    args: ["--disable-gpu", "--mute-audio", "--no-proxy-server", "--autoplay-policy=no-user-gesture-required"],
  });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 }, colorScheme: "dark", baseURL: BASE, locale: "ru-RU", timezoneId: "Europe/Moscow",
  });
  await context.addCookies([{ name: "vvf_token", value: TOKEN, domain: "127.0.0.1", path: "/" }]);
  await context.route(/.*/, (route) => {
    const u = new URL(route.request().url());
    if (u.hostname === "127.0.0.1" || u.protocol === "data:" || u.protocol === "blob:") return route.continue();
    return route.abort();
  });
  const page = await context.newPage();
  page.on("pageerror", (e) => result.errors.push(String(e).slice(0, 300)));

  let current = null;
  const check = (name, ok, value) => current.checks.push({ name, ok: Boolean(ok), ...(value === undefined ? {} : { value }) });
  const step = async (name, fn) => {
    current = { name, checks: [] };
    result.steps.push(current);
    try {
      await Promise.race([fn(), sleep(120_000).then(() => { throw new Error("шаг не уложился в 120 с"); })]);
    } catch (e) {
      current.error = String(e).split("\n")[0].slice(0, 300);
      await page.screenshot({ path: path.join(dir, `FAIL-${name.replace(/[^\wа-яё]+/gi, "_").slice(0, 40)}.jpg`), type: "jpeg", quality: 60 }).catch(() => undefined);
    }
    current.ok = !current.error && current.checks.every((c) => c.ok);
    process.stdout.write(`${current.ok ? "ok  " : "FAIL"} ${name}${current.error ? ` :: ${current.error}` : ""}\n`);
    for (const c of current.checks) process.stdout.write(`  ${c.ok ? "ok" : "НЕТ"} ${c.name}${c.value === undefined ? "" : ` = ${JSON.stringify(c.value)}`}\n`);
  };
  const shot = (name) => page.screenshot({ path: path.join(dir, `${name}.png`), style: ".toast-stack{visibility:hidden!important}" });
  const apiGet = async (url) => (await context.request.get(BASE + url)).json();
  const subsOf = (clip) => apiGet(`/api/clip-plans/${clip}/subtitles`);
  const settle = async (ms = 600) => {
    await page.waitForLoadState("networkidle", { timeout: 4000 }).catch(() => undefined);
    await sleep(ms);
  };
  const seeked = () => page.waitForFunction(() => {
    const v = document.querySelector(".ed-stage video, .mo-stage video");
    return v && v.readyState >= 1 && !v.seeking;
  }, null, { timeout: 15000 }).catch(() => undefined);
  const openEditor = async (clip) => {
    await page.goto(`/v/${VID}/candidates?clip=${clip}`, { waitUntil: "domcontentloaded" });
    await page.locator(".ed").first().waitFor({ state: "attached", timeout: 15000 });
    await settle();
    await seeked();
    await sleep(300);
  };
  const inspTab = async (name) => {
    await page.locator(".insp-tab", { hasText: name }).first().click();
    await sleep(350);
  };
  const clock = () => page.evaluate(() => {
    const v = document.querySelector(".ed-stage video, .mo-stage video");
    return { tc: document.querySelector(".ed-tc-now")?.textContent, t: v?.currentTime ?? null, ready: v?.readyState ?? null };
  });

  // ------------------------------------------------------------ 1. Правка строки субтитров
  await step("1. правка строки доходит до базы и не пропадает после перезагрузки", async () => {
    await openEditor(CLIP);
    await inspTab("Субтитры");
    const text = page.locator(".insp-pane:not([hidden]) .subed-text").first();
    const edited = `${await text.inputValue()} правка владельца`;
    await text.fill(edited);
    await sleep(100);
    const put = page.waitForResponse((r) => r.request().method() === "PUT" && r.url().endsWith(`/clip-plans/${CLIP}/subtitles`), { timeout: 8000 });
    await page.locator(".insp-pane:not([hidden]) .subed-foot .btn.primary").click();
    check("PUT прошёл", (await (await put).status()) === 200);
    await settle(300);
    const saved = await subsOf(CLIP);
    check("в базе новая строка", saved.lines[0].text === edited, saved.lines[0].text);
    check("в словах новые слова", saved.words.some((w) => w.word === "владельца"));
    check("в поле редактора то, что записано", (await text.inputValue()) === edited);
    await shot("1-saved");
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.locator(".ed").first().waitFor({ state: "attached", timeout: 15000 });
    await settle();
    await inspTab("Субтитры");
    check("после перезагрузки строка на месте", (await page.locator(".insp-pane:not([hidden]) .subed-text").first().inputValue()) === edited);
    check("блок на таймлайне с правкой", (await page.locator(".ctl-block--sub").first().innerText()).includes("владельца"));
  });

  // ------------------------------------------------------------ 2. Шаг вперёд
  await step("2. Ctrl+Z откатывает, Ctrl+Shift+Z и кнопка возвращают", async () => {
    const edited = (await subsOf(CLIP)).lines[0].text;
    await openEditor(CLIP);
    await page.evaluate(() => document.activeElement instanceof HTMLElement && document.activeElement.blur());
    await page.keyboard.press("Control+z");
    await settle(800);
    const undone = (await subsOf(CLIP)).lines[0].text;
    check("Ctrl+Z откатил правку", !undone.includes("владельца"), undone);
    await sleep(2500); // автосохранение настроек после отмены не должно съесть шаг вперёд
    check("шаг вперёд ещё доступен", (await apiGet(`/api/clip-plans/${CLIP}/undo`)).redo >= 1);
    await page.keyboard.press("Control+Shift+z");
    await settle(800);
    check("Ctrl+Shift+Z вернул правку", (await subsOf(CLIP)).lines[0].text === edited, (await subsOf(CLIP)).lines[0].text);
    await page.keyboard.press("Control+z");
    await settle(800);
    check("снова назад", !(await subsOf(CLIP)).lines[0].text.includes("владельца"));
    const redo = page.locator("button", { hasText: "Шаг вперёд" });
    check("кнопка видна", await redo.isVisible());
    await shot("2-buttons");
    await redo.click();
    await settle(800);
    check("кнопка вернула правку", (await subsOf(CLIP)).lines[0].text === edited);
    await page.keyboard.press("Control+y");
    await settle(500);
    check("вперёд больше некуда, клип тот же", (await subsOf(CLIP)).lines[0].text === edited);
    // Новая правка после отмены закрывает шаг вперёд.
    await page.keyboard.press("Control+z");
    await settle(800);
    await inspTab("Субтитры");
    const text = page.locator(".insp-pane:not([hidden]) .subed-text").first();
    await text.fill(`${await text.inputValue()} другая`);
    await page.locator(".insp-pane:not([hidden]) .subed-foot .btn.primary").click();
    await settle(800);
    check("после новой правки вперёд нельзя", (await apiGet(`/api/clip-plans/${CLIP}/undo`)).redo === 0);
  });

  // ------------------------------------------------------------ 3. Таймкод на первом кадре
  await step("3. таймкод на первом кадре, при открытии и после Home", async () => {
    for (const clip of CLIPS_WITH_WORDS) {
      await openEditor(clip);
      const opened = await clock();
      check(`клип ${clip}: при открытии`, opened.tc === "00:00.00", opened);
      await page.evaluate(() => document.activeElement instanceof HTMLElement && document.activeElement.blur());
      await page.keyboard.press("End");
      await sleep(400);
      await page.keyboard.press("Home");
      await seeked();
      await sleep(400);
      const home = await clock();
      check(`клип ${clip}: после Home`, home.tc === "00:00.00", home);
    }
    await shot("3-first-frame");
  });

  // ------------------------------------------------------------ 4. Переключатель «скрытые»
  await step("4. без скрытых фильтр выключается вместе с переключателем", async () => {
    await page.goto(`/v/${VID}/candidates`, { waitUntil: "domcontentloaded" });
    await page.locator(".mo-card").first().waitFor({ timeout: 15000 });
    await settle();
    const hiddenSwitch = page.locator("label.switch", { hasText: "скрытые" });
    if (await hiddenSwitch.count()) throw new Error("в копии уже есть скрытые черновики: возьми чистую копию базы");
    const cards = page.locator(".mo-card");
    const before = await cards.count();
    const titleOf = (card) => card.locator(".mo-act").last();
    await titleOf(cards.first()).click(); // скрыть первый
    await settle(500);
    check("первый скрылся", (await cards.count()) === before - 1, await cards.count());
    await hiddenSwitch.locator("input").check({ force: true });
    await sleep(300);
    check("со включённым фильтром он виден", (await cards.count()) === before);
    await shot("4-hidden-on");
    await page.locator(".mo-card .mo-act[title='Вернуть черновик']").first().click(); // вернуть последний скрытый
    await settle(500);
    check("переключатель исчез", (await hiddenSwitch.count()) === 0);
    await titleOf(cards.first()).click(); // скрыть следующий
    await settle(500);
    check("следующий скрытый пропал из списка", (await cards.count()) === before - 1, await cards.count());
    check("переключатель снова есть и выключен", (await hiddenSwitch.count()) === 1 && !(await hiddenSwitch.locator("input").isChecked()));
    await shot("4-next-hidden");
    await hiddenSwitch.locator("input").check({ force: true });
    await sleep(300);
    await page.locator(".mo-card .mo-act[title='Вернуть черновик']").first().click();
    await settle(400);
  });

  // ------------------------------------------------------------ 5. Esc в «Смотреть монтаж»
  const fsOn = () => page.evaluate(() => Boolean(document.fullscreenElement));
  const watchOpen = () => page.locator(".mwatch").count();
  const inEditor = () => page.locator(".ed").count();
  const openWatch = async () => {
    await openEditor(CLIP);
    await page.locator(".ed-watch-btn").first().click();
    await page.locator(".mwatch").waitFor({ timeout: 8000 });
    await sleep(700);
  };
  await step("5. F и Esc в «Смотреть монтаж» не выводят из редактора", async () => {
    await openWatch();
    const started = await fsOn();
    check("открылся", (await watchOpen()) === 1);
    result.watchStartedFullscreen = started;
    await page.keyboard.press("f");
    await sleep(500);
    check("после F плеер открыт", (await watchOpen()) === 1, { fs: await fsOn() });
    check("F сменил весь экран", (await fsOn()) !== started);
    await page.keyboard.press("Escape");
    await sleep(600);
    check("после Esc мы в редакторе", (await inEditor()) >= 1 && page.url().includes(`clip=${CLIP}`), page.url());
    check("после Esc плеер закрыт", (await watchOpen()) === 0);
    check("весь экран снят", !(await fsOn()));
    await shot("5-after-esc");
    await page.keyboard.press("Escape");
    await sleep(500);
    check("следующий Esc уже уводит к черновикам: так и задумано", !page.url().includes(`clip=${CLIP}`) || (await inEditor()) === 0, page.url());
  });

  await browser.close();
  const bad = result.steps.filter((s) => !s.ok);
  fs.writeFileSync(path.join(dir, "result.json"), JSON.stringify(result, null, 1), "utf8");
  console.log(`\nшагов ${result.steps.length}, упало ${bad.length}, ошибок страницы ${result.errors.length}`);
  for (const e of result.errors) console.log("  page:", e);
  process.exit(bad.length || result.errors.length ? 1 : 0);
})().catch((e) => {
  console.error(e);
  fs.writeFileSync(path.join(dir, "result.json"), JSON.stringify(result, null, 1), "utf8");
  process.exit(2);
});
