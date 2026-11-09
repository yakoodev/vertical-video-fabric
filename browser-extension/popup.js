import { api, isFirefox, loadSettings, readCookies, serviceFetch } from "./browser-api.js";
import {
  PLATFORMS, accountsForPlatform, buildAccountPayload, detectPlatform, filterCookies, normalizeServiceUrl,
  requiredStatus, serviceErrorText, toCookieEditorJson, verdictText,
} from "./lib.js";

const $ = (id) => document.getElementById(id);
const NEW = "new";
const UNREACHABLE = "Сервис недоступен или не пускает расширение: проверьте адрес в настройках и разрешение на него";

const state = { origin: "", token: "", tab: null, platform: null, cookies: [], status: null, accounts: [] };

function show(sectionId) {
  for (const id of ["need-settings", "choose-platform", "need-permission", "main"]) $(id).hidden = id !== sectionId;
}

function setResult(text, kind = "") {
  $("result").textContent = text;
  $("result").className = `result ${kind}`;
}

async function init() {
  $("open-options").addEventListener("click", () => api.runtime.openOptionsPage());
  $("go-options").addEventListener("click", () => api.runtime.openOptionsPage());
  for (const button of document.querySelectorAll("[data-platform]")) {
    button.addEventListener("click", () => start(button.dataset.platform));
  }
  $("grant-platform").addEventListener("click", grantPlatform);
  $("account").addEventListener("change", onAccountChange);
  $("send").addEventListener("click", send);
  $("copy").addEventListener("click", copyJson);
  $("ua").textContent = navigator.userAgent;

  const settings = await loadSettings();
  if (!settings.token) {
    show("need-settings");
    return;
  }
  try {
    state.origin = normalizeServiceUrl(settings.serviceUrl);
  } catch {
    show("need-settings");
    return;
  }
  state.token = settings.token;
  [state.tab] = await api.tabs.query({ active: true, currentWindow: true });
  const platform = detectPlatform(state.tab?.url || "");
  if (platform) await start(platform);
  else show("choose-platform");
}

async function start(platformId) {
  state.platform = platformId;
  const platform = PLATFORMS[platformId];
  $("platform-title").textContent = `${platform.title} → сервис`;
  if (!(await api.permissions.contains({ origins: platform.origins }))) {
    $("permission-hint").textContent = isFirefox
      ? "Firefox может закрыть это окно, когда спросит разрешение, — потом откройте расширение ещё раз."
      : "";
    show("need-permission");
    return;
  }
  show("main");
  state.cookies = filterCookies(platformId, await readCookies(platform.cookieQuery, state.tab));
  state.status = requiredStatus(platformId, state.cookies);
  const login = $("login-state");
  if (state.status.ok) {
    login.textContent = "Вход найден";
    login.className = "state ok";
  } else {
    login.textContent = `Войдите в аккаунт на сайте ${platform.title}`;
    login.className = "state bad";
  }
  $("cookie-count").textContent = state.status.ok
    ? `Cookie площадки: ${state.cookies.length}`
    : `Нет: ${state.status.missing.join(", ")}`;
  $("new-label").placeholder = platform.labelHint;
  $("copy").disabled = state.cookies.length === 0;
  await loadAccounts();
}

function grantPlatform() {
  // Сразу в обработчике клика: иначе Firefox не покажет запрос.
  api.permissions.request({ origins: PLATFORMS[state.platform].origins }).then((granted) => {
    if (granted) start(state.platform);
  });
}

async function loadAccounts(selectId = null) {
  const select = $("account");
  select.replaceChildren();
  let error = "";
  try {
    const response = await serviceFetch(state.origin, state.token, "/api/accounts");
    if (response.ok) state.accounts = accountsForPlatform(response.body, state.platform);
    else error = serviceErrorText(response.status, response.body);
  } catch {
    error = UNREACHABLE;
  }
  for (const account of state.accounts) {
    const mark = account.has_required_cookies ? "вход есть" : "нет входа";
    select.append(new Option(`${account.label} (${mark})`, String(account.id)));
  }
  select.append(new Option("+ новый аккаунт", NEW));
  if (selectId !== null) select.value = String(selectId);
  else if (!state.accounts.length) select.value = NEW;
  onAccountChange();
  if (error) {
    setResult(error, "bad");
    $("send").disabled = true;
  } else {
    $("send").disabled = !state.status.ok;
    if (!state.status.ok) setResult("Отправлять нечего: сначала войдите в аккаунт на сайте.", "");
  }
}

function onAccountChange() {
  const isNew = $("account").value === NEW;
  $("new-label").hidden = !isNew;
  if (isNew) $("new-label").focus();
}

async function send() {
  const selected = $("account").value;
  const label = selected === NEW
    ? $("new-label").value
    : state.accounts.find((a) => String(a.id) === selected)?.label;
  let payload;
  try {
    payload = buildAccountPayload(state.platform, label, state.cookies, navigator.userAgent);
  } catch (error) {
    setResult(error.message, "bad");
    return;
  }
  $("send").disabled = true;
  setResult("Отправляю…");
  try {
    const response = await serviceFetch(state.origin, state.token, "/api/accounts", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      setResult(serviceErrorText(response.status, response.body), "bad");
      return;
    }
    await loadAccounts(response.body?.id ?? null);
    setResult(verdictText(response.body), response.body?.has_required_cookies ? "ok" : "bad");
  } catch {
    setResult(UNREACHABLE, "bad");
  } finally {
    $("send").disabled = !state.status.ok;
  }
}

async function copyJson() {
  const text = toCookieEditorJson(state.cookies);
  const done = `JSON скопирован (cookie: ${state.cookies.length}). Вставляйте только в свой сервис.`;
  try {
    await navigator.clipboard.writeText(text);
    setResult(done, "ok");
    return;
  } catch {
    // ниже — запасной путь
  }
  const area = $("copy-fallback");
  area.hidden = false;
  area.value = text;
  area.select();
  const copied = document.execCommand("copy");
  setResult(copied ? done : "Не удалось скопировать: выделите текст ниже и нажмите Ctrl+C.", copied ? "ok" : "bad");
}

init().catch(() => {
  show("main");
  setResult("Не получилось прочитать cookie или настройки", "bad");
});
