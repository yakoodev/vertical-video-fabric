// Один код для Chrome/Edge и Firefox: Firefox даёт `browser` (обещания), Chrome MV3 — `chrome` (тоже обещания).
export const api = globalThis.browser ?? globalThis.chrome;

export const isFirefox = typeof globalThis.browser !== "undefined" && api.runtime.getURL("").startsWith("moz-extension:");

export async function loadSettings() {
  const { serviceUrl = "", token = "" } = await api.storage.local.get(["serviceUrl", "token"]);
  return { serviceUrl, token };
}

/** Все cookie площадки в хранилище вкладки (контейнер Firefox, инкогнито). */
export async function readCookies(query, tab) {
  const storeId = await cookieStoreOf(tab);
  const details = storeId ? { ...query, storeId } : { ...query };
  try {
    return await api.cookies.getAll(details);
  } catch (error) {
    // Firefox с изоляцией первой стороны (privacy.firstparty.isolate) требует firstPartyDomain; null — все.
    if (/firstPartyDomain/i.test(String(error && error.message))) {
      return api.cookies.getAll({ ...details, firstPartyDomain: null });
    }
    throw error;
  }
}

async function cookieStoreOf(tab) {
  if (!tab) return undefined;
  if (tab.cookieStoreId) return tab.cookieStoreId; // Firefox
  try {
    const stores = await api.cookies.getAllCookieStores();
    return stores.find((s) => (s.tabIds || []).includes(tab.id))?.id;
  } catch {
    return undefined;
  }
}

/** Запрос к своему сервису. Токен — только в заголовке, никуда не пишется. */
export async function serviceFetch(origin, token, path, options = {}) {
  const response = await fetch(`${origin}${path}`, {
    ...options,
    credentials: "omit",
    cache: "no-store",
    headers: { Authorization: `Bearer ${token}`, ...(options.body ? { "Content-Type": "application/json" } : {}) },
  });
  let body = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  return { ok: response.ok, status: response.status, body };
}
