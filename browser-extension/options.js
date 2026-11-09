import { api, isFirefox, loadSettings, serviceFetch } from "./browser-api.js";
import { DEFAULT_SERVICE_URL, PLATFORMS, normalizeServiceUrl, servicePermissionPattern, serviceErrorText } from "./lib.js";

const $ = (id) => document.getElementById(id);

function setStatus(text, kind = "") {
  $("status").textContent = text;
  $("status").className = `result ${kind}`;
}

async function init() {
  // Слушатель — сразу: иначе ранний Enter отправил бы форму обычным GET с токеном в адресе.
  $("form").addEventListener("submit", save);
  const settings = await loadSettings();
  // Что владелец успел вписать до загрузки настроек, не затираем.
  if (!$("service-url").value) $("service-url").value = settings.serviceUrl || DEFAULT_SERVICE_URL;
  if (!$("token").value) $("token").value = settings.token;
  $("save").disabled = false;
}

function save(event) {
  event.preventDefault();
  let origin;
  try {
    origin = normalizeServiceUrl($("service-url").value);
  } catch (error) {
    setStatus(error.message, "bad");
    return;
  }
  const token = $("token").value.trim();
  if (!token) {
    setStatus("Впишите токен", "bad");
    return;
  }
  // Chrome/Edge: сайты площадок разрешены при установке, а сервис пускает расширение по CORS (его id закреплён ключом
  // в manifest.json) — лишних разрешений не просим. Firefox даёт расширению случайный адрес и по умолчанию не выдаёт
  // доступ к сайтам: просим адрес сервиса и площадки — сразу в обработчике клика, иначе Firefox откажет.
  const origins = [servicePermissionPattern(origin), ...Object.values(PLATFORMS).flatMap((p) => p.origins)];
  const permission = isFirefox ? api.permissions.request({ origins }).catch(() => false) : Promise.resolve(true);
  finishSave(origin, token, permission);
}

async function finishSave(origin, token, permission) {
  await api.storage.local.set({ serviceUrl: origin, token });
  $("service-url").value = origin;
  setStatus("Сохранено. Проверяю связь…");
  const granted = await permission;
  try {
    const response = await serviceFetch(origin, token, "/api/accounts");
    if (response.ok) {
      const count = Array.isArray(response.body) ? response.body.length : 0;
      setStatus(`Сохранено. Связь с сервисом есть, аккаунтов: ${count}`, "ok");
    } else {
      setStatus(`Сохранено, но ${serviceErrorText(response.status, response.body)}`, "bad");
    }
  } catch {
    setStatus(granted
      ? "Сохранено, но сервис не отвечает или не пускает расширение — проверьте адрес"
      : "Сохранено, но Firefox не дал доступ к адресу сервиса: нажмите «Сохранить» ещё раз и разрешите",
    "bad");
  }
}

init();
