/*
 * Проверка «Черновиков» и редактора в браузере: у фронтенда нет модульных тестов, поэтому при разборе
 * большого файла (шаг 6, docs/review-2026-11-06.md, п. 7) ходим по экрану, как человек, и сверяем итог
 * и с сервером, и с прошлым прогоном (tools/editor_check_compare.py).
 *
 * Только против локальной копии сервиса с копией базы, никогда не против живого :8088: редактор
 * автосохраняет. Клипы — из копии базы, номера в начале файла. Запуск:
 *
 *   node tools/editor_check.cjs <метка> desktop|phone
 *
 * Окружение: VVF_UI (адрес копии), VVF_TOKEN_FILE (токен копии), VVF_CHECK_OUT (куда класть результаты),
 * PLAYWRIGHT_CORE (путь к playwright-core), CHROME (путь к Chrome).
 * Итог: <OUT>/<метка>/<ширина>/result.json и кадры NN-имя.png.
 */
const fs = require("fs");
const path = require("path");

const BASE = (process.env.VVF_UI || "http://127.0.0.1:8496").replace(/\/$/, "");
const OUT = process.env.VVF_CHECK_OUT || "D:/vvf-offload/tmp/step6fe/results";
const TOKEN = fs.readFileSync(process.env.VVF_TOKEN_FILE || "D:/vvf-offload/tmp/step6fe/test_token.txt", "utf8").trim();
const { chromium } = require(process.env.PLAYWRIGHT_CORE || "D:/vvf-offload/tmp/stream_preview/browser/node_modules/playwright-core");
const CHROME = process.env.CHROME || "C:/Program Files/Google/Chrome/Application/chrome.exe";

// Копия базы: видео 40 (22 минуты, 8 черновиков, запись на месте), холст 65.
const VID = 40;
const CLIP = 545; // слои, вставка, вырез, субтитры, один кусок
const CLIP_SUBS = 549; // субтитры включены
const CLIP_BARE = 542; // клип без своих настроек
const CLIP_SPEED = 546;
const CANVAS_VID = 65;
const CANVAS_CLIP = 946;

const label = process.argv[2] || "run";
const widthName = process.argv[3] || "desktop";
const PHONE = widthName === "phone";
const dir = path.join(OUT, label, widthName);
fs.rmSync(dir, { recursive: true, force: true });
fs.mkdirSync(dir, { recursive: true });

const result = { label, width: widthName, steps: [], errors: [], failedApi: [], payloads: {} };
let shotNo = 0;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const r3 = (v) => (typeof v === "number" ? Number(v.toFixed(3)) : v);

(async () => {
  const browser = await chromium.launch({
    executablePath: CHROME,
    headless: true,
    args: ["--disable-gpu", "--mute-audio", "--no-proxy-server", "--autoplay-policy=no-user-gesture-required"],
  });
  const context = await browser.newContext({
    viewport: PHONE ? { width: 390, height: 844 } : { width: 1440, height: 900 },
    colorScheme: "dark",
    baseURL: BASE,
    locale: "ru-RU",
    timezoneId: "Europe/Moscow",
    isMobile: PHONE,
    hasTouch: PHONE,
    acceptDownloads: false,
  });
  await context.addCookies([{ name: "vvf_token", value: TOKEN, domain: "127.0.0.1", path: "/" }]);
  // Наружу ни запроса: только копия.
  await context.route(/.*/, (route) => {
    const u = new URL(route.request().url());
    if (u.hostname === "127.0.0.1" || u.protocol === "data:" || u.protocol === "blob:") return route.continue();
    return route.abort();
  });
  const page = await context.newPage();
  const here = () => page.url().replace(BASE, "");
  page.on("pageerror", (e) => result.errors.push(`page ${here()} :: ${String(e).slice(0, 400)}`));
  page.on("console", (m) => {
    if (m.type() === "error" && !/Failed to load resource/.test(m.text())) result.errors.push(`console ${here()} :: ${m.text().slice(0, 300)}`);
  });
  page.on("response", (r) => {
    if (r.status() >= 400 && r.url().includes("/api/")) result.failedApi.push(`${r.status()} ${r.request().method()} ${r.url().replace(BASE, "").slice(0, 120)}`);
  });

  // ---------------------------------------------------------------- помощники
  let current = null;
  const check = (name, ok, value) => {
    current.checks.push({ name, ok: Boolean(ok), ...(value === undefined ? {} : { value }) });
  };
  const note = (name, value) => check(name, true, value);
  const checkLive = (name, ok, value) => check(name, ok, ok ? undefined : value);
  const step = async (name, fn, { skipPhone = false } = {}) => {
    current = { name, checks: [], ok: true };
    result.steps.push(current);
    if (skipPhone && PHONE) {
      current.skipped = "не для телефона";
      return;
    }
    const t0 = Date.now();
    try {
      await Promise.race([fn(), sleep(90_000).then(() => { throw new Error("шаг не уложился в 90 с"); })]);
    } catch (e) {
      current.error = `${String(e).split("\n")[0].slice(0, 300)} @ ${here()}`;
      await page.screenshot({ path: path.join(dir, `FAIL-${name.replace(/[^\wа-яё]+/gi, "_").slice(0, 50)}.jpg`), type: "jpeg", quality: 60, fullPage: true }).catch(() => undefined);
    }
    current.ok = !current.error && current.checks.every((c) => c.ok);
    current.ms = Date.now() - t0;
    process.stdout.write(`${current.ok ? "ok  " : "FAIL"} ${name}${current.error ? ` :: ${current.error}` : ""}${current.checks.filter((c) => !c.ok).map((c) => ` [${c.name}=${JSON.stringify(c.value)}]`).join("")}\n`);
  };
  /** Кадр для сверки: анимации стоят, всплывашки не мешают, видео на паузе. */
  const shot = async (name) => {
    await page.evaluate(() => document.querySelectorAll("video").forEach((v) => v.pause())).catch(() => undefined);
    await quiet();
    shotNo += 1;
    const file = `${String(shotNo).padStart(2, "0")}-${name}.png`;
    await page.screenshot({
      path: path.join(dir, file),
      fullPage: true,
      animations: "disabled",
      style: ".toast-stack{visibility:hidden!important} video::-webkit-media-controls{display:none!important}",
    });
  };
  /** Плеер дошёл до кадра, а отрисовка успела подхватить. */
  const quiet = async () => {
    await page
      .waitForFunction(() => [...document.querySelectorAll("video")].every((v) => !v.seeking && (v.readyState >= 2 || !v.getAttribute("src"))), null, { timeout: 8000 })
      .catch(() => undefined);
    // Кадры таймлайна догружаются, полоса вкладок докручивается плавно: снимаем, когда всё улеглось.
    await page.waitForLoadState("networkidle", { timeout: 2500 }).catch(() => undefined);
    await page
      .waitForFunction(
        () => {
          const now = [...document.querySelectorAll("*")].filter((e) => e.scrollLeft > 0).map((e) => e.scrollLeft).join();
          const same = window.__scrolls === now;
          window.__scrolls = now;
          return same;
        },
        null,
        { timeout: 3000, polling: 250 },
      )
      .catch(() => undefined);
    await sleep(400);
  };
  const settle = async (ms = 900) => {
    await page.waitForLoadState("networkidle", { timeout: 4000 }).catch(() => undefined);
    await sleep(ms);
  };
  const go = async (to, ms = 1000) => {
    await page.goto(to, { waitUntil: "domcontentloaded" });
    await settle(ms);
  };
  const apiGet = async (url) => {
    const r = await context.request.get(BASE + url);
    return r.json();
  };
  const planOf = async (id, sid = VID) => (await apiGet(`/api/sources/${sid}`)).clip_plans.find((p) => p.id === id);
  const settingsOf = async (id, sid = VID) => (await planOf(id, sid))?.render_settings ?? null;
  /** Ждём, пока сервер увидит то, что должно было сохраниться. Возвращает итоговое значение. */
  const until = async (read, pred, ms = 5000) => {
    const t0 = Date.now();
    let v = await read();
    while (!pred(v) && Date.now() - t0 < ms) {
      await sleep(200);
      v = await read();
    }
    return v;
  };
  const layerOf = async (layerId, clip = CLIP) => ((await settingsOf(clip))?.layers ?? []).find((l) => l.id === layerId);
  const vid = () =>
    page.evaluate(() => {
      const v = document.querySelector(".ed-stage video, .mo-stage video");
      return v ? { paused: v.paused, t: Number(v.currentTime.toFixed(2)), rate: v.playbackRate, src: v.getAttribute("src"), ready: v.readyState, dur: v.duration } : null;
    });
  const tc = () => page.locator(".ed-tc-now").innerText().catch(() => null);
  const toastText = () => page.locator(".toast").allInnerTexts().catch(() => []);
  const openEditorUrl = async (clip, extra = "", sid = VID) => {
    await go(`/v/${sid}/candidates?clip=${clip}${extra}`, 1200);
    await page.locator(".ed").first().waitFor({ state: "attached", timeout: 15000 });
    await settle(700);
    await quiet();
  };
  const inspTab = async (name) => {
    await page.locator(".insp-tab", { hasText: name }).first().click();
    await sleep(350);
  };
  const libTab = async (name) => {
    await page.locator(".ed-tab", { hasText: name }).first().click();
    await sleep(500);
  };
  const openGroup = async (title) => {
    const head = page.locator(".insp-pane:not([hidden]) .rgroup-head", { hasText: title }).first();
    const grp = head.locator("xpath=..");
    if (!((await grp.getAttribute("class")) || "").includes("open")) await head.click();
    await sleep(300);
  };
  /** Фокус не в поле: иначе клавиши редактора молчат. Без клика: на телефоне шапка могла бы его перехватить. */
  const neutralClick = () => page.evaluate(() => document.activeElement instanceof HTMLElement && document.activeElement.blur());
  /** Клик мимо перехвата укрытия (липкий подвал инспектора на телефоне): обработчик тот же. */
  const domClick = (loc) => loc.evaluate((el) => el.click());
  const setRange = (loc, value) =>
    loc.evaluate((el, v) => {
      const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
      set.call(el, String(v));
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
    }, value);
  /** Выбрать слой из списка «Слои»: заодно курсор встаёт на него. */
  const selectLayerRow = async (title) => {
    await inspTab("Слои");
    await openGroup("Слои клипа");
    await page.locator(".ed-layer-row", { hasText: title }).first().click();
    await sleep(600);
  };
  /** Где на кадре ручки выделенного слоя: спрашиваем сам кадр — курсор над ним меняется по попаданию. */
  const scanHandles = async () => {
    const cv = page.locator(".ed-phone-overlay");
    await cv.scrollIntoViewIfNeeded();
    const box = await cv.boundingBox();
    const found = await cv.evaluate((el, stepPx) => {
      const r = el.getBoundingClientRect();
      const out = { scale: [], rotate: [] };
      for (let y = r.top + 2; y < r.bottom - 2; y += stepPx) {
        for (let x = r.left + 2; x < r.right - 2; x += stepPx) {
          el.dispatchEvent(new PointerEvent("pointermove", { bubbles: true, clientX: x, clientY: y }));
          const c = el.style.cursor;
          if (c === "nwse-resize") out.scale.push([x, y]);
          else if (c === "crosshair") out.rotate.push([x, y]);
        }
      }
      return out;
    }, 3);
    const mid = (pts) => (pts.length ? [pts.reduce((a, p) => a + p[0], 0) / pts.length, pts.reduce((a, p) => a + p[1], 0) / pts.length] : null);
    return { box, move: mid(found.scale), scale: found.scale[found.scale.length - 1] ?? null, rotate: mid(found.rotate) };
  };
  const drag = async (from, to) => {
    await page.mouse.move(from[0], from[1]);
    await page.mouse.down();
    await page.mouse.move((from[0] + to[0]) / 2, (from[1] + to[1]) / 2, { steps: 4 });
    await page.mouse.move(to[0], to[1], { steps: 4 });
    await page.mouse.up();
    await sleep(200);
  };
  const phoneDrawn = () =>
    page.evaluate(() => {
      const cv = document.querySelector(".ed-phone-screen canvas");
      if (!cv) return null;
      const g = cv.getContext("2d");
      const d = g.getImageData(0, 0, cv.width, cv.height).data;
      let sum = 0;
      for (let i = 0; i < d.length; i += 400) sum += d[i] + d[i + 1] + d[i + 2];
      return sum > 0;
    });
  const pieces = async (clip = CLIP) => (await planOf(clip)).segments.length;

  // ---------------------------------------------------------------- черновики
  await step("список: открылся", async () => {
    await go(`/v/${VID}/candidates`, 1500);
    await page.locator(".mo-card").first().waitFor({ timeout: 15000 });
    check("карточек 8", (await page.locator(".mo-card").count()) === 8, await page.locator(".mo-card").count());
    check("редактор не открыт", (await page.locator(".ed").count()) === 0);
    check("студия выключена", !(await page.evaluate(() => document.body.classList.contains("vvf-studio"))));
    note("шапка", (await page.locator(".mo-stats-line").innerText()).replace(/\s+/g, " "));
    await shot("list");
  });

  await step("список: поиск", async () => {
    await page.locator(".mo-search input").fill("Морти");
    await sleep(300);
    const n = await page.locator(".mo-card").count();
    check("отфильтровал", n > 0 && n < 8, n);
    note("счётчик", await page.locator(".mo-count").innerText());
    await shot("list-search");
  });

  await step("список: фильтр не теряется, а рельс редактора идёт за ним", async () => {
    const n = await page.locator(".mo-card").count();
    await page.locator(".mo-card .mo-title").first().click();
    await page.locator(".ed").first().waitFor({ timeout: 15000 });
    await settle(500);
    check("адрес с clip=", /clip=\d+/.test(here()), here());
    check("студия включена", await page.evaluate(() => document.body.classList.contains("vvf-studio")));
    await libTab("Черновики").catch(() => undefined);
    const rail = await page.locator(".ed-rail-item").count();
    check("рельс показывает отфильтрованные", rail === n, { rail, n });
    await page.locator("button.ed-back", { hasText: "Назад к черновикам" }).click();
    await page.locator(".mo-card").first().waitFor({ timeout: 10000 });
    await sleep(300);
    check("поиск на месте", (await page.locator(".mo-search input").inputValue()) === "Морти");
    check("карточек столько же", (await page.locator(".mo-card").count()) === n);
    await page.locator(".mo-search input").fill("");
    await sleep(200);
  });

  await step("список: анализы", async () => {
    const chips = page.locator(".mo-toolbar .chip", { hasText: "Анализ #" });
    note("анализов", await chips.count());
    await chips.first().click();
    await sleep(200);
    const n = await page.locator(".mo-card").count();
    check("анализ скрыт", n < 8, n);
    await shot("list-analysis-off");
    await chips.first().click();
    await sleep(200);
    check("анализ вернулся", (await page.locator(".mo-card").count()) === 8);
  });

  await step("список: звезда и фильтр избранного", async () => {
    const card = page.locator(".mo-card", { hasText: "Разборки в кафе" });
    await card.locator(".mo-act").first().click();
    await sleep(300);
    const plan = await until(() => planOf(543), (p) => Boolean(p.favorite));
    check("звезда на сервере", Boolean(plan.favorite));
    check("выбор владельца", Boolean(plan.owner_picked));
    const fav = page.locator(".mo-toolbar .chip", { hasText: "Избранное" });
    note("чип", (await fav.innerText()).replace(/\s+/g, " "));
    await fav.click();
    await sleep(200);
    check("только избранные", (await page.locator(".mo-card").count()) === 1);
    await shot("list-favorites");
    await fav.click();
    await sleep(200);
    await card.locator(".mo-act").first().click();
    const plan2 = await until(() => planOf(543), (p) => !p.favorite);
    check("звезда снята", !plan2.favorite);
  });

  await step("список: скрыть и вернуть", async () => {
    const card = page.locator(".mo-card", { hasText: "Разборки в кафе" });
    await card.locator(".mo-act").nth(1).click();
    await sleep(300);
    check("карточка ушла", (await page.locator(".mo-card").count()) === 7);
    check("скрыта на сервере", Boolean((await until(() => planOf(543), (p) => Boolean(p.hidden))).hidden));
    const sw = page.locator(".mo-toolbar label.switch", { hasText: "скрытые" });
    await sw.click();
    await sleep(200);
    check("показана со скрытыми", (await page.locator(".mo-card").count()) === 8);
    await shot("list-hidden");
    await page.locator(".mo-card", { hasText: "Разборки в кафе" }).locator(".mo-act").nth(1).click();
    check("вернулась", !(await until(() => planOf(543), (p) => !p.hidden)).hidden);
  });

  await step("список: отметки «в рендер»", async () => {
    const btn = page.locator(".mo-toolbar-acts .btn", { hasText: "Рендер" });
    note("было", (await btn.innerText()).replace(/\s+/g, " "));
    await page.locator(".mo-card .check input").first().click({ force: true });
    await sleep(150);
    check("минус один", /7/.test(await btn.innerText()), await btn.innerText());
    await page.locator(".mo-group-sel").first().click();
    await sleep(150);
    note("выбрать группу", (await btn.innerText()).replace(/\s+/g, " "));
    await page.locator(".mo-toolbar .btn", { hasText: /Снять все|Выбрать все/ }).first().click();
    await sleep(150);
    note("все", (await btn.innerText()).replace(/\s+/g, " "));
    if (/ 0/.test(await btn.innerText()) || !(await btn.isEnabled())) {
      await page.locator(".mo-toolbar .btn", { hasText: /Снять все|Выбрать все/ }).first().click();
      await sleep(150);
    }
    check("все снова", /8/.test(await btn.innerText()), await btn.innerText());
  });

  await step("список: рендер отправляет настройки панели", async () => {
    let body = null;
    await page.route("**/api/sources/*/render-plans", async (route) => {
      body = route.request().postDataJSON();
      await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
    });
    await page.locator(".mo-toolbar-acts .btn", { hasText: "Рендер" }).click();
    await until(async () => body, (b) => b !== null, 3000);
    await page.unroute("**/api/sources/*/render-plans");
    check("запрос ушёл", body !== null);
    result.payloads.listRender = body;
    await sleep(200);
  });

  await step("список: клик по кадру играет клип", async () => {
    await page.locator(".mo-card .mo-thumb").nth(2).click();
    await sleep(1500);
    const v = await vid();
    checkLive("играет", v && !v.paused, v);
    checkLive("время идёт", v && v.t > 0, v?.t);
    const left = await page.locator(".mo-heat-head").evaluate((el) => el.style.left);
    checkLive("метка на полосе", parseFloat(left) > 0, left);
    const active = await page.locator(".mo-card.active").count();
    check("карточка подсвечена", active === 1, active);
    const drawn = await page.evaluate(() => {
      const cv = document.querySelector(".mo-phone canvas");
      if (!cv) return null;
      const d = cv.getContext("2d").getImageData(0, 0, cv.width, cv.height).data;
      let s = 0;
      for (let i = 0; i < d.length; i += 400) s += d[i];
      return s > 0;
    });
    check("телефон рисует", drawn === true, drawn);
    await page.evaluate(() => document.querySelector(".mo-stage video").pause());
  });

  await step("список: клавиши F, X", async () => {
    const card = page.locator(".mo-card", { hasText: "Суть настоящей тюрьмы" });
    await page.evaluate(() => document.activeElement instanceof HTMLElement && document.activeElement.blur());
    await card.hover();
    await sleep(100);
    await page.keyboard.press("f");
    check("F ставит звезду", Boolean((await until(() => planOf(547), (p) => Boolean(p.favorite))).favorite));
    await page.keyboard.press("f");
    check("F снимает звезду", !(await until(() => planOf(547), (p) => !p.favorite)).favorite);
    await page.keyboard.press("x");
    check("X скрывает", Boolean((await until(() => planOf(547), (p) => Boolean(p.hidden))).hidden));
    await sleep(300);
    // Выше вернули последний скрытый: переключатель исчез, и фильтр выключился вместе с ним.
    check("скрытый пропал из списка", (await card.count()) === 0, await card.count());
    if (!(await card.count())) {
      await page.locator(".mo-toolbar label.switch", { hasText: "скрытые" }).click();
      await page.evaluate(() => document.activeElement instanceof HTMLElement && document.activeElement.blur());
      await sleep(200);
    }
    await card.hover();
    await page.keyboard.press("x");
    check("X возвращает", !(await until(() => planOf(547), (p) => !p.hidden)).hidden);
    await page.mouse.move(2, 2);
    await sleep(200);
  });

  await step("список: Ctrl+C и Ctrl+D не работают со слоями", async () => {
    await page.keyboard.press("Control+c");
    await page.keyboard.press("Control+d");
    await sleep(200);
    const t = await toastText();
    check("нет всплывашки про слой", !t.some((x) => /скопирован/.test(x)), t);
  });

  await step("список: E открывает редактор, Esc возвращает", async () => {
    const card = page.locator(".mo-card", { hasText: "Морти в костюм" });
    await card.hover();
    await page.keyboard.press("e");
    await page.locator(".ed").first().waitFor({ timeout: 15000 });
    await settle(600);
    check("адрес", here().includes(`clip=${CLIP}`), here());
    await neutralClick();
    await page.keyboard.press("Escape");
    await page.locator(".mo-card").first().waitFor({ timeout: 10000 });
    check("назад в список", !here().includes("clip="), here());
  });

  // ---------------------------------------------------------------- редактор: открытие и плеер
  await step("редактор: открылся по ссылке", async () => {
    await openEditorUrl(CLIP);
    const v = await vid();
    check("плеер встал на начало клипа", v && Math.abs(v.t - 268.4) < 0.3, v);
    check("метаданные есть", v && v.ready >= 1, v?.ready);
    check("телефон рисует", (await phoneDrawn()) === true);
    check("таймлайн есть", (await page.locator(".ctl").count()) === 1);
    note("блоки", {
      pieces: await page.locator(".ctl-block--piece").count(),
      subs: await page.locator(".ctl-block--sub").count(),
      layers: await page.locator(".ctl-block--layer").count(),
      inserts: await page.locator(".ctl-block--ins").count(),
    });
    note("таймкод", await tc());
    note("сохранено", await page.locator(".ed-saved").innerText());
    await shot("editor");
  });

  await step("редактор: вкладки библиотеки", async () => {
    for (const name of ["Кадр", "Шаблоны", "Текст", "Стикеры", "Звуки", "Мемы", "Файлы"]) {
      const tab = page.locator(".ed-tab", { hasText: name }).first();
      if (!(await tab.count())) {
        note(`нет вкладки ${name}`, true);
        continue;
      }
      await tab.click();
      await settle(500);
      check(`активна ${name}`, ((await tab.getAttribute("class")) || "").includes("active"));
      await shot(`lib-${name}`);
    }
    note("вкладки", await page.locator(".ed-tab-label").allInnerTexts());
    note("запомнилась", await page.evaluate(() => localStorage.getItem("vvf.ed.libTab")));
    await page.locator(".ed-tab").first().click();
  });

  await step("редактор: панель агента", async () => {
    const tab = page.locator(".ed-tab", { hasText: /ИИ/ }).first();
    if (!(await tab.count())) return note("вкладки ИИ нет", true);
    await tab.click();
    await settle(500);
    check("панель открылась", (await page.locator(".agent").count()) === 1);
    note("есть поле ввода", await page.locator(".agent textarea").count());
    await shot("lib-agent");
    await page.locator(".ed-tab").first().click();
    await sleep(300);
  });

  await step("редактор: вкладки инспектора", async () => {
    note("вкладки", await page.locator(".insp-tab").allInnerTexts());
    for (const name of ["Клип", "Слои", "Кусок", "Звук", "Субтитры", "Проверка"]) {
      await inspTab(name);
      check(`видна ${name}`, (await page.locator(".insp-pane:not([hidden])").count()) === 1);
      note(`${name}: групп`, await page.locator(".insp-pane:not([hidden]) .rgroup-head").count());
      await shot(`insp-${name}`);
    }
    await inspTab("Клип");
  });

  await step("редактор: плей, стоп, скорость", async () => {
    await page.locator(".ed-transport .btn", { hasText: "Превью клипа" }).click();
    await sleep(1400);
    const a = await vid();
    checkLive("играет", a && !a.paused, a);
    const tc1 = await tc();
    await sleep(500);
    const tc2 = await tc();
    checkLive("таймкод идёт", tc1 !== tc2, [tc1, tc2]);
    await page.locator(".speed-seg .seg-item", { hasText: "2×" }).click();
    await sleep(200);
    check("скорость 2×", (await vid()).rate === 2, (await vid()).rate);
    await page.locator(".speed-seg .seg-item", { hasText: "1×" }).first().click();
    await sleep(100);
    await page.locator(".speed-seg .seg-item").nth(1).click();
    await sleep(200);
    check("скорость 1×", (await vid()).rate === 1, (await vid()).rate);
    await page.locator(".ed-transport .btn", { hasText: "Стоп" }).click();
    await sleep(200);
    check("остановился", (await vid()).paused);
  });

  await step("редактор: клавиши плеера", async () => {
    await neutralClick();
    await page.keyboard.press("Home");
    await sleep(400);
    check("Home — начало", /^00:00\.00|^—/.test(await tc()), await tc());
    await page.keyboard.press("Shift+ArrowRight");
    await sleep(400);
    check("Shift+→ — секунда", /^00:01\.0/.test(await tc()), await tc());
    await page.keyboard.press("ArrowRight");
    await sleep(300);
    note("→ — кадр", await tc());
    await page.keyboard.press("ArrowLeft");
    await sleep(300);
    note("← — кадр", await tc());
    await page.keyboard.press(" ");
    await sleep(900);
    check("пробел — плей", !(await vid()).paused);
    await page.keyboard.press(" ");
    await sleep(200);
    check("пробел — пауза", (await vid()).paused);
    await page.keyboard.press("Home");
    await sleep(400);
  });

  await step("редактор: ссылка с секундой (t=)", async () => {
    await openEditorUrl(CLIP, "&t=3");
    check("встал на 3 с", /^00:03\.0/.test((await tc()) ?? ""), await tc());
  });

  await step("редактор: таймлайн", async () => {
    const ruler = page.locator(".ctl-ruler");
    await ruler.scrollIntoViewIfNeeded();
    const box = await ruler.boundingBox();
    await page.mouse.click(box.x + box.width * 0.5, box.y + box.height / 2);
    await sleep(500);
    note("клик по линейке", await tc());
    check("плеер на паузе после перемотки", (await vid()).paused);
    await page.locator(".ctl-block--piece").first().click();
    await sleep(400);
    check("кусок выделен", (await page.locator(".ctl-block--piece.on").count()) === 1);
    check("открылась вкладка «Кусок»", ((await page.locator(".insp-tab.active, .insp-tab[aria-selected=true]").first().innerText()) || "").includes("Кусок"));
    note("заголовок куска", await page.locator(".insp-piece-title").innerText());
    await shot("timeline-piece");
    const zoom = page.locator(".ctl-bar .btn").nth(2);
    await zoom.click();
    await sleep(300);
    await shot("timeline-zoom");
    await page.locator(".ctl-bar .btn").nth(1).click();
    await sleep(200);
    await page.locator(".ctl-block--sub").nth(2).click();
    await sleep(400);
    check("строка субтитров выделена", (await page.locator(".ctl-block--sub.on").count()) === 1);
    check("открылась вкладка «Субтитры»", ((await page.locator(".insp-tab.active, .insp-tab[aria-selected=true]").first().innerText()) || "").includes("Субтитры"));
  });

  await step("редактор: разрез куска и откат (S, Ctrl+Z)", async () => {
    await neutralClick();
    await page.keyboard.press("Home");
    await sleep(300);
    await page.keyboard.press("Shift+ArrowRight");
    await page.keyboard.press("Shift+ArrowRight");
    await page.keyboard.press("Shift+ArrowRight");
    await sleep(400);
    await page.keyboard.press("s");
    check("кусков два", (await until(() => pieces(), (n) => n === 2, 8000)) === 2);
    await settle(400);
    check("на таймлайне два", (await page.locator(".ctl-block--piece").count()) === 2);
    await shot("timeline-split");
    await neutralClick();
    await page.keyboard.press("Control+z");
    check("откат: кусок один", (await until(() => pieces(), (n) => n === 1, 8000)) === 1);
    await settle(400);
    note("всплывашка", (await toastText()).filter((t) => /Шаг назад/.test(t)).length > 0);
    check("на таймлайне один", (await page.locator(".ctl-block--piece").count()) === 1);
  });

  await step("редактор: блок слоя двигается по таймлайну", async () => {
    await openEditorUrl(CLIP);
    const before = (await layerOf("p1_text")).start;
    const block = page.locator(".ctl-block--layer", { hasText: "ЕМУ КОСТЮМ" }).first();
    await block.scrollIntoViewIfNeeded();
    const b = await block.boundingBox();
    await drag([b.x + b.width / 2, b.y + b.height / 2], [b.x + b.width / 2 + 40, b.y + b.height / 2]);
    const after = await until(() => layerOf("p1_text"), (l) => l.start !== before);
    check("слой сдвинулся", after.start > before, [before, r3(after.start)]);
    note("новое начало", r3(after.start));
  });

  await step("редактор: добавить слой из текста", async () => {
    await openEditorUrl(CLIP);
    const n0 = (await settingsOf(CLIP)).layers.length;
    await libTab("Текст");
    const items = page.locator(".lib-text");
    note("текстовых заготовок", await items.count());
    await items.first().click();
    const n1 = (await until(() => settingsOf(CLIP), (s) => s.layers.length === n0 + 1)).layers.length;
    check("слой добавлен", n1 === n0 + 1, [n0, n1]);
    await sleep(400);
    check("инспектор открылся на слоях", ((await page.locator(".insp-tab.active, .insp-tab[aria-selected=true]").first().innerText()) || "").includes("Слои"));
    await shot("layer-added");
  });

  let draggedId = null;
  await step("редактор: рамка слоя: сдвиг, масштаб, поворот", async () => {
    const layers = (await settingsOf(CLIP)).layers;
    const added = layers[layers.length - 1];
    draggedId = added.id;
    note("тип слоя", added.type);
    await neutralClick();
    await page.keyboard.press("Home");
    await sleep(100);
    await inspTab("Слои");
    await openGroup("Слои клипа");
    const row = page.locator(".ed-layer-row.on");
    check("новый слой выделен", (await row.count()) === 1);
        await row.click();
    await neutralClick();
    await page.keyboard.press("Shift+ArrowRight");
    await sleep(600);
    let h = await scanHandles();
    check("есть тело слоя", Boolean(h.move), h.move);
    check("есть ручка масштаба", Boolean(h.scale), h.scale);
    check("есть ручка поворота", Boolean(h.rotate), h.rotate);
    await shot("transform-frame");
    const before = await layerOf(draggedId);
    await drag(h.move, [h.move[0] + 30, h.move[1] + 45]);
    const moved = await until(() => layerOf(draggedId), (l) => l.x !== before.x || l.y !== before.y);
    check("сдвинулся", moved.x > before.x && moved.y > before.y, [r3(before.x), r3(before.y), r3(moved.x), r3(moved.y)]);
    note("положение", [r3(moved.x), r3(moved.y)]);
    h = await scanHandles();
    await drag(h.scale, [h.scale[0] + 25, h.scale[1] + 25]);
    const scaled = await until(() => layerOf(draggedId), (l) => (l.scale ?? 1) !== (moved.scale ?? 1));
    check("масштаб поменялся", (scaled.scale ?? 1) > (moved.scale ?? 1), [r3(moved.scale), r3(scaled.scale)]);
    note("масштаб", r3(scaled.scale));
    h = await scanHandles();
    await drag(h.rotate, [h.rotate[0] + 40, h.rotate[1] + 10]);
    const rotated = await until(() => layerOf(draggedId), (l) => (l.rotation ?? 0) !== (scaled.rotation ?? 0));
    check("поворот поменялся", (rotated.rotation ?? 0) !== (scaled.rotation ?? 0), r3(rotated.rotation));
    note("поворот", r3(rotated.rotation));
    await shot("transform-done");
  });

  await step("редактор: ключевые кадры", async () => {
    await neutralClick();
    await page.keyboard.press("Home"); // курсор вне слоя: клик по строке поставит его внутрь
    await sleep(300);
    await selectLayerRow("ЕМУ КОСТЮМ");
    const key = page.locator(".li-key").first();
    await key.scrollIntoViewIfNeeded();
    check("кнопка ключа доступна", await key.isEnabled());
    const keysBefore = await page.locator(".ctl-key").count();
    await key.click();
    const saved = await until(() => layerOf("p1_text"), (l) => Object.keys(l.keys ?? {}).length > 0);
    check("ключ сохранён", Object.keys(saved.keys ?? {}).length > 0, Object.keys(saved.keys ?? {}));
    await sleep(300);
    check("ромб на таймлайне", (await page.locator(".ctl-key").count()) > keysBefore, await page.locator(".ctl-key").count());
    await shot("keyframe-added");
    // Второй ключ — тем же ◆ через 5 кадров.
    await neutralClick();
    for (let i = 0; i < 5; i += 1) await page.keyboard.press("ArrowRight");
    await sleep(400);
    await domClick(page.locator(".li-key").first());
    const prop = Object.keys(saved.keys)[0];
    const two = await until(() => layerOf("p1_text"), (l) => (l.keys?.[prop]?.length ?? 0) >= 2);
    note("ключей", two.keys?.[prop]?.length ?? 0);
    await domClick(page.locator(".li-key").first());
    await sleep(300);
  });

  await step("редактор: инспектор слоя", async () => {
    await neutralClick();
    await selectLayerRow("ИЛИ ДОМОЙ");
    check("инспектор показан", (await page.locator(".li-tr").count()) > 0);
    await shot("layer-inspector");
    const text = page.locator(".insp-pane:not([hidden]) textarea").first();
    check("есть поле текста", (await text.count()) === 1);
    await text.fill("ПРОВЕРКА ИНСПЕКТОРА");
    await text.blur();
    const saved = await until(() => layerOf("p2_text"), (l) => l.props?.text === "ПРОВЕРКА ИНСПЕКТОРА");
    check("текст сохранён", saved.props?.text === "ПРОВЕРКА ИНСПЕКТОРА", saved.props?.text);
    const block = page.locator(".ctl-block--layer", { hasText: "ПРОВЕРКА" });
    check("таймлайн видит новый текст", (await block.count()) === 1);
  });

  await step("редактор: правка текста на кадре", async () => {
    await neutralClick();
    const h = await scanHandles();
    check("слой под курсором", Boolean(h.move));
    await page.mouse.dblclick(h.move[0], h.move[1]);
    await sleep(300);
    const box = page.locator(".ed-inline-text");
    check("поле поверх кадра", (await box.count()) === 1);
    await shot("inline-edit");
    await box.fill("С КАДРА");
    await page.keyboard.press("Enter");
    const saved = await until(() => layerOf("p2_text"), (l) => l.props?.text === "С КАДРА");
    check("сохранён", saved.props?.text === "С КАДРА", saved.props?.text);
    await sleep(200);
    await page.mouse.dblclick(h.move[0], h.move[1]);
    await sleep(300);
    await page.locator(".ed-inline-text").fill("ЭТО НЕ СОХРАНИТСЯ");
    await page.keyboard.press("Escape");
    await sleep(800);
    check("Esc отменил", (await layerOf("p2_text")).props?.text === "С КАДРА");
    check("редактор не закрылся", here().includes("clip="));
  });

  await step("редактор: клавиши слоя (Ctrl+C/V/D, Alt+стрелки, Del)", async () => {
    await neutralClick();
    await selectLayerRow("С КАДРА");
    await neutralClick();
    const n0 = (await settingsOf(CLIP)).layers.length;
    await page.keyboard.press("Control+c");
    await sleep(200);
    check("Ctrl+C: всплывашка", (await toastText()).some((t) => /скопирован/.test(t)), await toastText());
    await page.keyboard.press("Control+v");
    check("Ctrl+V: плюс слой", (await until(() => settingsOf(CLIP), (s) => s.layers.length === n0 + 1)).layers.length === n0 + 1);
    await sleep(200);
    await neutralClick();
    await page.keyboard.press("Control+d");
    check("Ctrl+D: плюс слой", (await until(() => settingsOf(CLIP), (s) => s.layers.length === n0 + 2)).layers.length === n0 + 2);
    await sleep(300);
    await neutralClick();
    const list = (await settingsOf(CLIP)).layers;
    const newest = list[list.length - 1];
    await page.keyboard.press("Alt+ArrowRight");
    const moved = await until(() => layerOf(newest.id), (l) => JSON.stringify(l) !== JSON.stringify(newest));
    check("Alt+→ двигает", JSON.stringify(moved) !== JSON.stringify(newest));
    await page.keyboard.press("Delete");
    check("Del удаляет", (await until(() => settingsOf(CLIP), (s) => s.layers.length === n0 + 1)).layers.length === n0 + 1);
    await sleep(300);
    await page.locator(".ed-layer-row").last().click();
    await sleep(300);
    await neutralClick();
    await page.keyboard.press("Delete");
    check("Del удаляет ещё", (await until(() => settingsOf(CLIP), (s) => s.layers.length === n0)).layers.length === n0);
  });

  await step("редактор: галерея шаблонов", async () => {
    await libTab("Шаблоны");
    await settle(400);
    const cards = page.locator(".lib-tpl");
    const n = await cards.count();
    check("шаблоны есть", n > 0, n);
    await page.locator(".lib-cat").nth(1).click();
    await sleep(300);
    note("в категории", await cards.count());
    await shot("templates-category");
    await page.locator(".lib-cat").first().click();
    await sleep(200);
    await page.locator(".lib-search").fill("zzzzzz");
    await sleep(200);
    check("поиск пуст", (await cards.count()) === 0);
    await page.locator(".lib-search").fill("");
    await sleep(200);
    const n0 = (await settingsOf(CLIP)).layers.length;
    await cards.first().click();
    const n1 = (await until(() => settingsOf(CLIP), (s) => s.layers.length > n0)).layers.length;
    check("шаблон лёг слоями", n1 > n0, [n0, n1]);
    await sleep(300);
    await shot("template-added");
  });

  await step("редактор: субтитры", async () => {
    await openEditorUrl(CLIP_SUBS);
    await inspTab("Субтитры");
    await settle(400);
    const lines = await page.locator(".insp-pane:not([hidden]) .subed-line").count();
    check("строки есть", lines > 0, lines);
    check("блоки на таймлайне", (await page.locator(".ctl-block--sub").count()) === lines, await page.locator(".ctl-block--sub").count());
    await shot("subs");
    const s0 = await settingsOf(CLIP_SUBS);
    const range = page.locator(".insp-pane:not([hidden]) input[type=range]").first();
    await setRange(range, 25);
    check("положение сохранено", (await until(() => settingsOf(CLIP_SUBS), (s) => s.sub_pos_pct === 25)).sub_pos_pct === 25);
    note("было положение", s0.sub_pos_pct);
    const text = page.locator(".insp-pane:not([hidden]) .subed-text").first();
    const original = await text.inputValue();
    await text.fill(`${original} проверка`);
    await sleep(100);
    check("таймлайн видит правку сразу", (await page.locator(".ctl-block--sub").first().innerText()).includes("проверка"));
    const put = page.waitForRequest((r) => r.method() === "PUT" && r.url().endsWith(`/clip-plans/${CLIP_SUBS}/subtitles`), { timeout: 5000 });
    await page.locator(".insp-pane:not([hidden]) .subed-foot .btn.primary").click();
    const sent = (await put).postDataJSON();
    check("запрос с правкой ушёл", (sent.lines?.[0]?.text ?? "").includes("проверка"));
    await settle(400);
    note("строка после чтения", (await apiGet(`/api/clip-plans/${CLIP_SUBS}/subtitles`)).lines?.[0]?.text);
    const toggle = page.locator(".insp-pane:not([hidden]) label.switch", { hasText: "Накладывать субтитры" });
    await toggle.click();
    check("выключились", (await until(() => settingsOf(CLIP_SUBS), (s) => s.subs_on === false)).subs_on === false);
    await sleep(200);
    await shot("subs-off");
    await toggle.click();
    check("включились", (await until(() => settingsOf(CLIP_SUBS), (s) => s.subs_on === true)).subs_on === true);
  });

  await step("редактор: субтитры широко", async () => {
    await page.locator(".insp-pane:not([hidden]) .btn", { hasText: "Править строки широко" }).click();
    await sleep(500);
    check("окно открылось", (await page.locator(".subs-wide").count()) === 1);
    check("строки в окне", (await page.locator(".subs-wide .subed-line").count()) > 0);
    await shot("subs-wide");
    await page.keyboard.press("f"); // пока окно открыто, клавиши редактора молчат
    await sleep(200);
    check("F в окне не ставит звезду", !(await planOf(CLIP_SUBS)).favorite);
    await page.locator(".subs-wide .pub-x").click();
    await sleep(300);
    check("окно закрылось", (await page.locator(".subs-wide").count()) === 0);
  });

  await step("редактор: вырезы", async () => {
    await openEditorUrl(CLIP);
    await inspTab("Клип");
    await openGroup("Паузы");
    const saved = page.locator(".cut-list-saved .cut-row");
    check("сохранённый вырез виден", (await saved.count()) === 1, await saved.count());
    await shot("cuts");
    // Превью перепрыгивает вырез (8.34–9.98) на ходу.
    await page.locator(".cut-list-saved .cut-when").first().click();
    await sleep(400);
    note("перешёл к вырезу", await tc());
    await neutralClick();
    await page.keyboard.press("Home");
    await sleep(200);
    for (let i = 0; i < 8; i += 1) await page.keyboard.press("Shift+ArrowRight");
    await sleep(400);
    await page.keyboard.press(" ");
    const jumped = await until(tc, (t) => parseFloat((t || "0:0").split(":")[1]) >= 9.9, 5000);
    await page.keyboard.press(" ");
    checkLive("плеер перепрыгнул вырез", parseFloat((jumped || "0:0").split(":")[1]) >= 9.9, jumped);
    await sleep(200);
    await page.locator(".cut-find .btn").first().click();
    await page.locator(".cut-found, .toast").first().waitFor({ timeout: 25000 }).catch(() => undefined);
    await sleep(400);
    const found = await page.locator(".cut-found .cut-row").count();
    note("найдено пауз", found);
    if (found) {
      await page.locator(".cut-apply").click();
      const after = await until(() => settingsOf(CLIP), (s) => (s.cuts ?? []).length !== 1, 6000);
      note("вырезов после применения", (after.cuts ?? []).length);
    }
    await sleep(300);
  });

  await step("редактор: автосохранение и перезагрузка", async () => {
    await openEditorUrl(CLIP_SPEED);
    await inspTab("Клип");
    const mirror = page.locator(".insp-pane:not([hidden]) label.switch", { hasText: "Зеркало" });
    await mirror.scrollIntoViewIfNeeded();
    await mirror.click();
    check("сохранилось на сервере", (await until(() => settingsOf(CLIP_SPEED), (s) => s.mirror === true)).mirror === true);
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.locator(".ed").first().waitFor({ timeout: 15000 });
    await settle(800);
    await inspTab("Клип");
    check("после перезагрузки включено", await page.locator(".insp-pane:not([hidden]) label.switch", { hasText: "Зеркало" }).locator("input").isChecked());
    check("сцена зеркальна", (await page.locator(".stage-frame.mirrored").count()) === 1);
    await shot("mirror-reloaded");
  });

  await step("редактор: откат кнопкой", async () => {
    await page.locator(".ed-undo-btn", { hasText: "Шаг назад" }).click();
    const s = await until(() => settingsOf(CLIP_SPEED), (x) => x.mirror === false, 6000);
    check("откат на сервере", s.mirror === false);
    await settle(300);
    check("панель перечитала", !(await page.locator(".insp-pane:not([hidden]) label.switch", { hasText: "Зеркало" }).locator("input").isChecked()));
    check("всплывашка", (await toastText()).some((t) => /Шаг назад/.test(t)), await toastText());
    await shot("undo-done");
    note("кнопки «вернуть вперёд» в редакторе нет", (await page.locator(".ed-viewbar .btn", { hasText: /Вернуть|Повтор/ }).count()) === 0);
  });

  await step("редактор: быстрый уход не теряет правку", async () => {
    await inspTab("Клип");
    const mirror = page.locator(".insp-pane:not([hidden]) label.switch", { hasText: "Зеркало" });
    await mirror.click();
    await page.locator("button.ed-back", { hasText: "Назад к черновикам" }).click(); // меньше 600 мс
    await page.locator(".mo-card").first().waitFor({ timeout: 10000 });
    check("сохранилась при уходе", (await until(() => settingsOf(CLIP_SPEED), (s) => s.mirror === true)).mirror === true);
    check("студия выключена", !(await page.evaluate(() => document.body.classList.contains("vvf-studio"))));
  });

  await step("редактор: панель живёт между клипами", async () => {
    // Панель настроена в прошлом клипе (зеркало) — клип без своих настроек стартует с неё, а слои чужие не едут.
    await page.locator(".mo-card", { hasText: "Секретная переписка" }).locator(".mo-title").click();
    await page.locator(".ed").first().waitFor({ timeout: 15000 });
    await settle(600);
    check("адрес", here().includes(`clip=${CLIP_BARE}`), here());
    await inspTab("Клип");
    check("панель унаследована", await page.locator(".insp-pane:not([hidden]) label.switch", { hasText: "Зеркало" }).locator("input").isChecked());
    check("слоёв нет", (await page.locator(".ctl-block--layer").count()) === 0);
    const s = await until(() => settingsOf(CLIP_BARE), (x) => x !== null);
    check("автосохранение записало старт", s && s.mirror === true, s && s.mirror);
    await shot("bare-clip-inherit");
    await page.locator("button.ed-back", { hasText: "Назад к черновикам" }).click();
    await page.locator(".mo-card").first().waitFor({ timeout: 10000 });
    await sleep(200);
    let body = null;
    await page.route("**/api/sources/*/render-plans", async (route) => {
      body = route.request().postDataJSON();
      await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
    });
    await page.locator(".mo-toolbar-acts .btn", { hasText: "Рендер" }).click();
    await until(async () => body, (b) => b !== null, 3000);
    await page.unroute("**/api/sources/*/render-plans");
    check("рендер из списка берёт панель редактора", body && body.mirror === true, body && body.mirror);
    result.payloads.listRenderAfterEditor = body;
  });

  await step("редактор: выделение и Ctrl+C после ухода в список", async () => {
    await openEditorUrl(CLIP);
    await selectLayerRow("ЕМУ КОСТЮМ");
    await page.locator("button.ed-back", { hasText: "Назад к черновикам" }).click();
    await page.locator(".mo-card").first().waitFor({ timeout: 10000 });
    await sleep(600);
    await page.evaluate(() => localStorage.removeItem("vvf.layerClipboard"));
    await page.keyboard.press("Control+c");
    await sleep(200);
    check("в списке Ctrl+C молчит", (await page.evaluate(() => localStorage.getItem("vvf.layerClipboard"))) === null);
    check("без всплывашки", !(await toastText()).some((t) => /скопирован/.test(t)), await toastText());
    await page.keyboard.press("Delete");
    await sleep(300);
    check("Del в списке молчит", (await settingsOf(CLIP)).layers.some((l) => l.id === "p2_text"));
  });

  await step("редактор: рендер отправляет настройки панели", async () => {
    await openEditorUrl(CLIP);
    let body = null;
    await page.route("**/api/sources/*/render-plans", async (route) => {
      body = route.request().postDataJSON();
      await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
    });
    await page.locator(".ed-render-btns .btn.primary").click();
    await until(async () => body, (b) => b !== null, 3000);
    await page.unroute("**/api/sources/*/render-plans");
    check("один клип", body && body.clip_plan_ids?.length === 1 && body.clip_plan_ids[0] === CLIP, body?.clip_plan_ids);
    result.payloads.editorRender = body;
  });

  await step("редактор: звук", async () => {
    await inspTab("Звук");
    const gainBtn = page.locator(".insp-pane:not([hidden]) .btn", { hasText: "Громче у курсора" });
    await openGroup("Громкость видео");
    await gainBtn.click();
    const s = await until(() => settingsOf(CLIP), (x) => (x.gain ?? []).length === 1);
    check("отрезок громкости сохранён", (s.gain ?? []).length === 1, s.gain);
    await openGroup("Музыка");
    await page.locator(".insp-pane:not([hidden]) label.switch", { hasText: "Фоновый трек" }).click();
    check("музыка включена", (await until(() => settingsOf(CLIP), (x) => x.music_on === true)).music_on === true);
    await shot("audio");
  });

  await step("редактор: заглушить отрезок", async () => {
    const btn = page.locator(".ctl-tools .btn", { hasText: "Заглушить" });
    if (!(await btn.count())) return note("кнопки нет", true);
    await neutralClick();
    await page.keyboard.press("Home");
    await sleep(300);
    await btn.click();
    await sleep(200);
    for (let i = 0; i < 2; i += 1) await page.keyboard.press("Shift+ArrowRight");
    await sleep(300);
    await page.locator(".ctl-tools .ctl-mute-armed").click();
    const s = await until(() => settingsOf(CLIP), (x) => (x.mute ?? []).length === 1);
    check("отрезок тишины сохранён", (s.mute ?? []).length === 1, s.mute);
    await sleep(200);
  });

  await step("редактор: скорость куска", async () => {
    await openEditorUrl(CLIP_SPEED);
    await inspTab("Кусок");
    await sleep(400);
    const sel = page.locator(".insp-pane:not([hidden]) select").first();
    await openGroup("Скорость куска");
    await sel.selectOption("2");
    const plan = await until(() => planOf(CLIP_SPEED), (p) => p.segments[0].speed === 2, 8000);
    check("скорость сохранена", plan.segments[0].speed === 2, plan.segments[0].speed);
    await settle(400);
    await shot("piece-speed");
    await page.locator(".ed-undo-btn", { hasText: "Шаг назад" }).click();
    const back = await until(() => planOf(CLIP_SPEED), (p) => (p.segments[0].speed ?? 1) === 1, 8000);
    check("откат скорости", (back.segments[0].speed ?? 1) === 1);
    await settle(300);
  });

  await step("редактор: смотреть монтаж", async () => {
    await openEditorUrl(CLIP);
    const t0 = (await vid()).t;
    await page.locator(".ed-watch-btn").click();
    await page.locator(".mwatch").waitFor({ timeout: 8000 });
    await sleep(800);
    check("плеер открыт", (await page.locator(".mwatch").count()) === 1);
    const parked = await page.evaluate(() => document.querySelector(".ed-stage video")?.getAttribute("src") ?? null);
    check("плеер редактора припаркован", parked === null, parked);
    await page.keyboard.press("s"); // в окне клавиши редактора молчат
    await sleep(500);
    check("S не режет кусок", (await pieces()) === 1);
    await page.keyboard.press("Escape");
    await sleep(800);
    check("закрылся по Esc", (await page.locator(".mwatch").count()) === 0);
    check("остался в редакторе", here().includes("clip="), here());
    const v = await until(vid, (x) => x && x.src && x.ready >= 1, 8000);
    check("плеер вернул запись", Boolean(v?.src), v);
    check("вернулся на ту же секунду", v && Math.abs(v.t - t0) < 0.3, [t0, v?.t]);
    await shot("after-watch");
  });

  await step("редактор: к клипу и обратно", async () => {
    await page.locator("a.ed-back", { hasText: "К клипу" }).click();
    await page.waitForURL(`**/clips/${CLIP}**`, { timeout: 10000 });
    await settle(600);
    check("экран клипа", here().startsWith(`/clips/${CLIP}`), here());
    check("студия выключена", !(await page.evaluate(() => document.body.classList.contains("vvf-studio"))));
    await page.goBack();
    await page.locator(".ed").first().waitFor({ timeout: 15000 });
    await settle(600);
    check("вернулся в монтаж", here().includes(`clip=${CLIP}`), here());
    check("студия включена", await page.evaluate(() => document.body.classList.contains("vvf-studio")));
  });

  await step("редактор: назад и вперёд браузером", async () => {
    await go(`/v/${VID}/candidates`, 900);
    await page.locator(".mo-card", { hasText: "Морти в костюм" }).locator(".mo-title").click();
    await page.locator(".ed").first().waitFor({ timeout: 15000 });
    await settle(400);
    await page.goBack();
    await page.locator(".mo-card").first().waitFor({ timeout: 10000 });
    check("назад — список", !here().includes("clip="), here());
    await page.goForward();
    await page.locator(".ed").first().waitFor({ timeout: 15000 });
    await settle(400);
    check("вперёд — монтаж", here().includes(`clip=${CLIP}`), here());
    await page.locator("button.ed-back", { hasText: "Назад к черновикам" }).click();
    await page.locator(".mo-card").first().waitFor({ timeout: 10000 });
    await sleep(200);
    await page.locator(".mo-card .mo-thumb").first().click();
    await sleep(900);
    const v = await vid();
    checkLive("после монтажа плеер списка играет", v && !v.paused, v);
    await page.evaluate(() => document.querySelector(".mo-stage video")?.pause());
  });

  await step("редактор: звезда (F) в монтаже", async () => {
    await openEditorUrl(CLIP);
    await neutralClick();
    await page.keyboard.press("f");
    check("F ставит", Boolean((await until(() => planOf(CLIP), (p) => Boolean(p.favorite))).favorite));
    await sleep(300);
    check("звезда в шапке таймлайна", (await page.locator(".ed-tl-head .mo-act.fav").count()) === 1);
    await page.keyboard.press("f");
    check("F снимает", !(await until(() => planOf(CLIP), (p) => !p.favorite)).favorite);
  });

  await step("редактор: файл клипа и ИИ-монтаж открываются", async () => {
    await page.locator(".ed-foot-tools .btn", { hasText: "Файл клипа" }).click();
    await page.locator(".modal").first().waitFor({ timeout: 8000 });
    await settle(400);
    await shot("clip-file");
    await page.keyboard.press("Escape");
    await sleep(200);
    if (await page.locator(".modal").count()) await page.locator(".modal .pub-x").first().click({ timeout: 3000 }).catch(() => undefined);
    await sleep(300);
    const ai = page.locator(".ed-ai-btn");
    if (await ai.count()) {
      await ai.click();
      await page.locator(".modal").first().waitFor({ timeout: 8000 });
      await sleep(400);
      await shot("ai-montage");
      await page.keyboard.press("Escape");
      await sleep(200);
      if (await page.locator(".modal").count()) await page.locator(".modal .pub-x").first().click({ timeout: 3000 }).catch(() => undefined);
      await sleep(300);
    }
    check("окна закрыты", (await page.locator(".modal").count()) === 0, await page.locator(".modal").count());
  });

  await step("холст: редактор открывается", async () => {
    await openEditorUrl(CANVAS_CLIP, "", CANVAS_VID);
    check("фон холста", (await page.locator(".stage-canvas-bg").count()) === 1);
    check("вкладки без «Кусок»", !(await page.locator(".insp-tab").allInnerTexts()).some((t) => t.includes("Кусок")), await page.locator(".insp-tab").allInnerTexts());
    note("блоки", await page.locator(".ctl-block--layer").count());
    await shot("canvas");
  });

  await step("редактор: уход убирает студийный режим", async () => {
    await page.locator("button.ed-back", { hasText: "Назад к черновикам" }).click();
    await page.locator(".mo-card").first().waitFor({ timeout: 10000 });
    await sleep(300);
    check("студия выключена", !(await page.evaluate(() => document.body.classList.contains("vvf-studio"))));
    await shot("canvas-list");
  });

  await browser.close();
  const bad = result.steps.filter((s) => !s.ok && !s.skipped);
  result.summary = { steps: result.steps.length, failed: bad.map((s) => s.name), skipped: result.steps.filter((s) => s.skipped).map((s) => s.name), pageErrors: result.errors.length, failedApi: result.failedApi.length };
  fs.writeFileSync(path.join(dir, "result.json"), JSON.stringify(result, null, 1), "utf8");
  console.log(`\n${widthName}: шагов ${result.steps.length}, упало ${bad.length}, ошибок страницы ${result.errors.length}, ошибок API ${result.failedApi.length}`);
})().catch((e) => {
  console.error(e);
  fs.writeFileSync(path.join(dir, "result.json"), JSON.stringify(result, null, 1), "utf8");
  process.exit(2);
});
