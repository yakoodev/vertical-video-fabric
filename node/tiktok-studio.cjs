// Аналитика роликов из TikTok Studio (зовёт app/tiktok_studio_collect.py): удержание, досмотры, источники.
//
// Одна сессия Chromium на аккаунт: его cookie, его user agent, его прокси. Ролики открываются по одному
// (https://www.tiktok.com/tiktokstudio/analytics/<id>) с паузой; ответы /aweme/v2/data/insight/, которые
// запрашивает сама страница, перехватываются, из них берутся только нужные поля. Подписывать запросы
// самим (X-Bogus) не нужно: их подписывает страница.
//
// Секреты — только окружением, не аргументами (аргументы видны в ps): TT_STUDIO_PROXY — адрес прокси
// с паролем, TT_STUDIO_COOKIES — cookie в формате Playwright (JSON), TT_STUDIO_UA — user agent.
// TT_STUDIO_PAUSE_MS — пауза между роликами, TT_STUDIO_WAIT_MS — сколько ждать чисел на странице.
//
// node tiktok-studio.cjs <папка playwright-chromium> [id,id,...]
// Без id — только узнать ник аккаунта (страница /tiktokstudio). Ответ — одна строка JSON в stdout:
// {"status":"ok","handle":"…","login_required":false,"videos":{"<id>":{"ok":true,"insight":{…}}|{"ok":false,"error":"…"}}}
const path = require("path");

// Поля ответа insight, которые разбирает Python (app/tiktok_studio.py parse_insight). Остальное не нужно.
const KEEP = [
  "realtime_total_video_views",
  "video_finish_rate_realtime",
  "video_per_duration_realtime",
  "video_total_duration_realtime",
  "video_new_followers",
  "realtime_new_followers",
  "video_traffic_source_percent_realtime",
  "video_retention_rate_realtime",
];
const READY = ["video_retention_rate_realtime", "video_per_duration_realtime"];

function proxyOption(raw) {
  if (!raw) return undefined;
  const url = new URL(raw);
  const option = { server: `${url.protocol}//${url.host}` };
  if (url.username) option.username = decodeURIComponent(url.username);
  if (url.password) option.password = decodeURIComponent(url.password);
  return option;
}

// Ник в ответе /tiktokstudio/api/web/user (на 09.11 — userBaseInfo.UserProfile.UserBase.UniqId): форма ответа
// не документирована, поэтому ключ ищется на любой глубине.
function findHandle(node, depth = 0) {
  if (!node || typeof node !== "object" || depth > 6) return "";
  for (const key of ["UniqId", "unique_id", "uniqueId"]) {
    if (typeof node[key] === "string" && node[key].trim()) return node[key].trim();
  }
  for (const value of Object.values(node)) {
    const found = findHandle(value, depth + 1);
    if (found) return found;
  }
  return "";
}

// «Нет данных» TikTok отдаёт как {"status": 2, "value": null} — такие поля не перетирают уже найденные.
function hasData(value) {
  if (value == null) return false;
  if (typeof value === "object" && !Array.isArray(value) && value.status === 2 && value.value == null) return false;
  return true;
}

function videoInfo(info) {
  if (!info || typeof info !== "object") return null;
  return {
    aweme_id: String(info.aweme_id || ""),
    create_time: info.create_time || null,
    duration_ms: (info.video && info.video.duration) || null,
    author: (info.author && info.author.unique_id) || "",
  };
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const firstLine = (err) => String((err && err.message) || err).split("\n")[0].slice(0, 300);

(async function main() {
  const [playwrightDir, idsArg] = process.argv.slice(2);
  const ids = String(idsArg || "").split(",").map((s) => s.trim()).filter((s) => /^\d{6,30}$/.test(s));
  const pauseMs = Number(process.env.TT_STUDIO_PAUSE_MS || 8000);
  const waitMs = Number(process.env.TT_STUDIO_WAIT_MS || 25000);
  const cookies = JSON.parse(process.env.TT_STUDIO_COOKIES || "[]");
  const { chromium } = require(playwrightDir || path.join(__dirname, "node_modules", "playwright-chromium"));
  const browser = await chromium.launch({
    proxy: proxyOption(process.env.TT_STUDIO_PROXY),
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || undefined,
    args: ["--no-sandbox"],
  });
  const out = { status: "ok", handle: "", login_required: false, videos: {} };
  try {
    const context = await browser.newContext({
      locale: "ru-RU",
      viewport: { width: 1400, height: 1000 },
      userAgent: process.env.TT_STUDIO_UA || undefined,
    });
    await context.addCookies(cookies);
    const page = await context.newPage();
    let current = null; // { id, merged, info }
    page.on("response", async (response) => {
      const url = response.url();
      if (url.includes("/tiktokstudio/api/web/user")) {
        try {
          const found = findHandle(await response.json());
          if (found && !out.handle) out.handle = found;
        } catch {}
        return;
      }
      if (!url.includes("/aweme/v2/data/insight/") || !current) return;
      const target = current;
      let body;
      try {
        body = await response.json();
      } catch {
        return;
      }
      if (!body || body.status_code !== 0) return;
      const info = videoInfo(body.video_info);
      if (info && info.aweme_id && info.aweme_id !== target.id) return; // чужой ролик — не наш ответ
      if (info && info.aweme_id) target.info = info;
      if (info && info.author && !out.handle) out.handle = info.author;
      for (const key of KEEP) {
        if (hasData(body[key])) target.merged[key] = body[key];
      }
    });

    if (!ids.length) {
      await page.goto("https://www.tiktok.com/tiktokstudio", { waitUntil: "domcontentloaded", timeout: 90000 });
      for (let waited = 0; waited < waitMs && !out.handle; waited += 500) await page.waitForTimeout(500);
      out.login_required = /\/login/.test(page.url());
    }
    for (let index = 0; index < ids.length; index++) {
      const id = ids[index];
      if (index && pauseMs > 0) await sleep(pauseMs);
      current = { id, merged: {}, info: null };
      try {
        await page.goto(`https://www.tiktok.com/tiktokstudio/analytics/${id}`, {
          waitUntil: "domcontentloaded",
          timeout: 90000,
        });
        if (/\/login/.test(page.url())) {
          out.login_required = true;
          break; // cookie не пускают — дальше открывать страницы бессмысленно
        }
        for (let waited = 0; waited < waitMs; waited += 500) {
          if (READY.every((key) => key in current.merged)) break;
          await page.waitForTimeout(500);
        }
        await page.waitForTimeout(800); // поздние ответы той же страницы
        if (!Object.keys(current.merged).length) {
          out.videos[id] = { ok: false, error: "страница не отдала числа (ролик чужой, удалён или ещё не обработан)" };
        } else {
          out.videos[id] = { ok: true, insight: { ...current.merged, video_info: current.info } };
        }
      } catch (err) {
        out.videos[id] = { ok: false, error: firstLine(err) };
      }
      current = null;
    }
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(out));
})().catch((err) => {
  // Только первая строка: в журнале Playwright дальше идёт вся команда запуска браузера.
  console.error(firstLine(err));
  process.exit(1);
});
