// Заход браузера аккаунта TikTok (зовёт app/session_keepalive.py): держит вход живым.
//
// Только TikTok. YouTube браузером не ходим: у сервиса лишь cookie youtube.com, без accounts.google.com, и
// настоящая страница YouTube, сверив вход с Google, сама стирает вход youtube.com (11.11 — ложное «вылетел»).
// Вход YouTube проверяет node/youtube-session.mjs через youtubei.js, как загрузка.
//
// Свой постоянный профиль Chromium на аккаунт (launchPersistentContext): тот же UA, окно, язык и часовой пояс
// каждый раз. Открывает главную площадки, ждёт, чуть листает колесом, стоит 20–40 с и закрывается. Только
// чтение: ни одного клика, ввода, лайка, подписки или поста. Картинки, видео и шрифты не грузит (память машины).
//
// Секреты — только окружением (аргументы видны в ps): KA_COOKIES — cookie аккаунта в формате Playwright (JSON),
// KA_PROXY — прокси с паролем, KA_UA — user agent. Остальное — KA_CONFIG (JSON):
// {"platform","url","profileDir","reseed","stayMs","pauseMs":[от,до],"viewport":{"width","height"},"locale","timezoneId"}.
//
// node session-keepalive.cjs <папка playwright-chromium>
// Ответ — одна строка JSON в stdout: {"status":"ok","url":"…","signals":{…},"cookies":[{name,value,domain}]}
// (cookie — только доменов площадки; Python пишет обратно лишь известные имена, app/session_cookies.py).
const path = require("path");

const DOMAINS = { tiktok: "tiktok.com" };
const BLOCK_TYPES = new Set(["image", "media", "font"]);
// Видео площадки идёт и обычными запросами (MSE), не только как media.
const BLOCK_URL = /googlevideo\.com\/videoplayback|\.mp4(\?|$)|mime_type=video|\/video\/tos\/|webapp-prime\.tiktok\.com\/video|tiktokcdn[^/]*\/[^?]*\/video\//i;

function proxyOption(raw) {
  if (!raw) return undefined;
  const url = new URL(raw);
  const option = { server: `${url.protocol}//${url.host}` };
  if (url.username) option.username = decodeURIComponent(url.username);
  if (url.password) option.password = decodeURIComponent(url.password);
  return option;
}

function domainOk(platform, domain) {
  const base = DOMAINS[platform];
  const host = String(domain || "").replace(/^\./, "").toLowerCase();
  return Boolean(base) && (host === base || host.endsWith(`.${base}`));
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const firstLine = (err) => String((err && err.message) || err).split("\n")[0].slice(0, 300);
const between = (lo, hi) => lo + Math.floor(Math.random() * Math.max(1, hi - lo));

// Без адреса страницы с параметрами: в них бывают токены.
function bareUrl(raw) {
  try {
    const url = new URL(raw);
    return `${url.origin}${url.pathname}`;
  } catch {
    return "";
  }
}

async function tiktokSignals(page) {
  return page.evaluate(() => {
    const out = { captcha: false, app_context: false, user: "" };
    // Только видимая капча: пустой контейнер под неё страница держит и без проверки.
    out.captcha = [...document.querySelectorAll(
      '[id*="captcha" i], [class*="captcha_verify" i], [class*="secsdk-captcha" i], iframe[src*="captcha"], iframe[src*="verify"]')]
      .some((el) => {
        const box = el.getBoundingClientRect();
        return box.width > 80 && box.height > 80 && getComputedStyle(el).visibility !== "hidden";
      });
    try {
      const node = document.getElementById("__UNIVERSAL_DATA_FOR_REHYDRATION__");
      const data = JSON.parse(node.textContent || "{}");
      const ctx = data.__DEFAULT_SCOPE__ && data.__DEFAULT_SCOPE__["webapp.app-context"];
      if (ctx) {
        out.app_context = true;
        const user = ctx.user || {};
        out.user = String(user.uniqueId || user.unique_id || user.uid || "");
      }
    } catch {}
    return out;
  });
}

(async function main() {
  const [playwrightDir] = process.argv.slice(2);
  const config = JSON.parse(process.env.KA_CONFIG || "{}");
  const platform = config.platform;
  if (!DOMAINS[platform]) throw new Error(`неизвестная площадка: ${platform}`);
  const cookies = JSON.parse(process.env.KA_COOKIES || "[]");
  const { chromium } = require(playwrightDir || path.join(__dirname, "node_modules", "playwright-chromium"));
  const context = await chromium.launchPersistentContext(config.profileDir, {
    headless: true,
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || undefined,
    proxy: proxyOption(process.env.KA_PROXY),
    userAgent: process.env.KA_UA || undefined,
    viewport: config.viewport || { width: 1280, height: 720 },
    locale: config.locale || "ru-RU",
    timezoneId: config.timezoneId || undefined,
    serviceWorkers: "block",
    acceptDownloads: false,
    args: [
      "--no-sandbox", "--disable-dev-shm-usage", "--mute-audio", "--disable-gpu", "--no-first-run",
      "--renderer-process-limit=2", "--disk-cache-size=16777216",
    ],
  });
  const out = { status: "ok", url: "", signals: {}, cookies: [] };
  try {
    // Хранилище сервиса — главное: в профиль кладём его cookie (там и ротации загрузок). Свежие от владельца —
    // старые cookie профиля сначала убираем целиком.
    if (config.reseed) await context.clearCookies();
    if (cookies.length) await context.addCookies(cookies);
    await context.route("**/*", (route) => {
      const request = route.request();
      if (BLOCK_TYPES.has(request.resourceType()) || BLOCK_URL.test(request.url())) return route.abort();
      return route.continue();
    });
    const page = context.pages()[0] || (await context.newPage());
    await page.goto(config.url, { waitUntil: "domcontentloaded", timeout: 60000 });
    await page.waitForLoadState("networkidle", { timeout: 15000 }).catch(() => {});
    const started = Date.now();
    const stayMs = Math.max(0, Number(config.stayMs ?? 30000));
    const [pauseLo, pauseHi] = Array.isArray(config.pauseMs) ? config.pauseMs : [3000, 7000];
    // Чуть листаем колесом — без кликов и наведения на кнопки.
    for (let step = 0; step < 3 && Date.now() - started < stayMs; step++) {
      await sleep(between(pauseLo, pauseHi));
      await page.mouse.wheel(0, between(300, 700)).catch(() => {});
    }
    const left = stayMs - (Date.now() - started);
    if (left > 0) await sleep(left);
    out.url = bareUrl(page.url());
    out.signals = await tiktokSignals(page);
    out.cookies = (await context.cookies())
      .filter((c) => domainOk(platform, c.domain))
      .map((c) => ({ name: c.name, value: c.value, domain: c.domain }));
  } finally {
    await context.close();
  }
  console.log(JSON.stringify(out));
})().catch((err) => {
  // Только первая строка: в журнале Playwright дальше идёт вся команда запуска браузера.
  console.error(firstLine(err));
  process.exit(1);
});
