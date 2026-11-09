// Чистые функции расширения: без chrome.* и без сети — их проверяет node --test (lib.test.js).
// Что нужно сервису, повторяет app/cookies.py (required_cookie_status) и провайдеры площадок.

export const DEFAULT_SERVICE_URL = "https://video.d.yakoo.dev";

export const PLATFORMS = {
  tiktok: {
    title: "TikTok",
    // Загрузка TikTok кладёт в сессию весь набор cookie площадки (app/providers/tiktok.py).
    cookieQuery: { domain: "tiktok.com" },
    domain: "tiktok.com",
    required: ["sessionid"],
    origins: ["*://*.tiktok.com/*"],
    labelHint: "например, tt-main",
  },
  youtube: {
    title: "YouTube",
    // Сервис берёт только cookie, что уходят на studio.youtube.com (to_cookie_header_for_host) —
    // ровно их и отдаёт браузер по этому адресу. Cookie .google.com сервису не нужны.
    cookieQuery: { url: "https://studio.youtube.com/" },
    domain: "youtube.com",
    required: ["SID", "HSID", "SSID", "APISID", "SAPISID"],
    origins: ["*://*.youtube.com/*"],
    labelHint: "например, yt-main",
  },
};

/** Площадка по адресу вкладки или null. */
export function detectPlatform(url) {
  let host;
  try {
    host = new URL(url).hostname.toLowerCase();
  } catch {
    return null;
  }
  for (const [id, platform] of Object.entries(PLATFORMS)) {
    if (host === platform.domain || host.endsWith(`.${platform.domain}`)) return id;
  }
  return null;
}

function domainMatches(cookieDomain, domain) {
  const d = String(cookieDomain || "").replace(/^\./, "").toLowerCase();
  return d === domain || d.endsWith(`.${domain}`);
}

/** Только cookie площадки, без пустых имён, без разделённых (partitioned) и без повторов имя+домен+путь. */
export function filterCookies(platformId, cookies) {
  const platform = PLATFORMS[platformId];
  if (!platform) return [];
  const seen = new Set();
  const out = [];
  for (const cookie of cookies || []) {
    if (!cookie || !cookie.name || typeof cookie.value !== "string") continue;
    if (cookie.partitionKey && Object.keys(cookie.partitionKey).length) continue;
    if (!domainMatches(cookie.domain, platform.domain)) continue;
    const key = `${cookie.name}\u0000${cookie.domain}\u0000${cookie.path || "/"}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(cookie);
  }
  return out;
}

/** Какие обязательные cookie есть и каких нет — как has_required_cookies / missing_cookies у сервиса. */
export function requiredStatus(platformId, cookies) {
  const platform = PLATFORMS[platformId];
  const names = new Set((cookies || []).filter((c) => c.value).map((c) => c.name));
  const required = platform ? platform.required : [];
  const missing = required.filter((name) => !names.has(name));
  return { ok: required.length > 0 && missing.length === 0, missing, present: required.filter((n) => names.has(n)) };
}

/** Экспорт в формате Cookie-Editor: его принимает поле Cookies сервиса (app/cookies.py, _parse_json). */
export function toCookieEditorJson(cookies) {
  const items = (cookies || []).map((c) => {
    const item = {
      domain: c.domain,
      hostOnly: Boolean(c.hostOnly),
      httpOnly: Boolean(c.httpOnly),
      name: c.name,
      path: c.path || "/",
      sameSite: c.sameSite && c.sameSite !== "unspecified" ? c.sameSite : null,
      secure: Boolean(c.secure),
      session: Boolean(c.session),
      storeId: c.storeId ?? null,
      value: c.value,
    };
    if (!c.session && typeof c.expirationDate === "number") item.expirationDate = c.expirationDate;
    return item;
  });
  return JSON.stringify(items, null, 2);
}

/** Тело POST /api/accounts: создаёт аккаунт или обновляет cookie того же платформа+метка. Прокси не трогает. */
export function buildAccountPayload(platformId, label, cookies, userAgent) {
  const name = String(label || "").trim();
  if (!PLATFORMS[platformId]) throw new Error("неизвестная площадка");
  if (!name) throw new Error("впишите название аккаунта");
  return {
    platform: platformId,
    label: name,
    cookie: toCookieEditorJson(cookies),
    user_agent: String(userAgent || "").trim(),
  };
}

export function accountsForPlatform(accounts, platformId) {
  return (Array.isArray(accounts) ? accounts : [])
    .filter((a) => a && a.platform === platformId)
    .sort((a, b) => String(a.label).localeCompare(String(b.label)));
}

const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);

/** Адрес сервиса из настроек → origin. https обязателен, http — только для своего компьютера. */
export function normalizeServiceUrl(input) {
  const text = String(input || "").trim() || DEFAULT_SERVICE_URL;
  let url;
  try {
    url = new URL(/^[a-z]+:\/\//i.test(text) ? text : `https://${text}`);
  } catch {
    throw new Error("адрес сервиса не разобрать");
  }
  if (url.protocol !== "https:" && !(url.protocol === "http:" && LOCAL_HOSTS.has(url.hostname))) {
    throw new Error("нужен https:// (http — только localhost)");
  }
  return url.origin;
}

/** Шаблон разрешения на адрес сервиса. Без порта: Firefox порты в шаблонах не принимает, Chrome без порта берёт любой. */
export function servicePermissionPattern(origin) {
  const url = new URL(origin);
  return `${url.protocol}//${url.hostname}/*`;
}

/** Итог сервиса после сохранения — строкой для владельца. */
export function verdictText(account) {
  if (!account || typeof account !== "object") return "Сохранено";
  const label = account.label ? `«${account.label}»` : "аккаунт";
  if (account.has_required_cookies) return `Сохранено: ${label}, вход есть (cookie: ${account.cookie_count})`;
  const missing = String(account.missing_cookies || "").split(",").filter(Boolean).join(", ");
  return `Сохранено ${label}, но сервис не видит входа: нет ${missing || "нужных cookie"}`;
}

/** Ошибка ответа сервиса — строкой. */
export function serviceErrorText(status, body) {
  if (status === 401) return "Сервис не принял токен — проверьте его в настройках";
  if (status === 423) return "Публикация в сервисе выключена (модуль)";
  const detail = body && typeof body === "object" ? body.detail : "";
  const text = Array.isArray(detail) ? detail.map((d) => d.msg || "").join("; ") : String(detail || "");
  return text ? `Сервис ответил: ${text}` : `Сервис ответил ошибкой ${status}`;
}
