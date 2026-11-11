// Проверка входа YouTube без браузера (зовёт app/session_keepalive.py): тот же youtubei.js, cookie и прокси,
// что у загрузки (node/youtube-upload.mjs), — так Google видит одного и того же клиента.
//
// Почему не браузер: у сервиса только cookie youtube.com, без accounts.google.com. Настоящая страница YouTube
// сверяет вход с Google, не находит его и сама стирает вход youtube.com — ложное «вылетел» (11.11, «vtubika»),
// а Google видит браузер, который теряет вход. youtubei.js ходит теми же cookie, что загрузка, и с ними работает.
//
// Только чтение: /account/account_menu (меню аккаунта) и, если задан канал, /account/accounts_list
// (переключатель каналов). Ни загрузок, ни правок, ни переключений канала.
//
// node node/youtube-session.mjs < {"cookie","proxyUrl","channel"}
// Ответ — JSON в stdout: {"ok":true,"loggedIn":true|false|null,"signal":"menu"|"yt_li"|"logged_in"|"",
// "channel":{"found":bool,"label":"…"}|null,"refreshedCookies":{имя: значение}}; сбой — {"ok":false,"error",…}.
// Свежие cookie — и при сбое: Google мог уже сменить __Secure-*PSIDTS (Python пишет их обратно).
import { realpathSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { activeChannelFromMenu, listIdentities, matchIdentity, parseChannelTarget } from './youtube-channel.mjs';
import { clientFetch, errorDetail } from './youtube-upload.mjs';

async function readStdin() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  return Buffer.concat(chunks).toString('utf8');
}

/** Признаки входа из responseContext: CSI yt_li и GFEEDBACK logged_in («1» / «0»). */
export function trackingFlags(data) {
  const out = {};
  const services = data?.responseContext?.serviceTrackingParams;
  if (!Array.isArray(services)) return out;
  for (const service of services) {
    for (const param of Array.isArray(service?.params) ? service.params : []) {
      if (param && (param.key === 'yt_li' || param.key === 'logged_in') && typeof param.value === 'string') {
        out[param.key] = param.value;
      }
    }
  }
  return out;
}

/**
 * Вход по ответу меню аккаунта: шапка activeAccountHeaderRenderer есть только у вошедшего;
 * без неё — по yt_li / logged_in. Ничего из этого нет — null (не знаем; не повод писать «вылетел»).
 */
export function loginFromMenu(data) {
  if (activeChannelFromMenu(data)) return { loggedIn: true, signal: 'menu' };
  const flags = trackingFlags(data);
  for (const key of ['yt_li', 'logged_in']) {
    if (flags[key] === '1') return { loggedIn: true, signal: key };
  }
  for (const key of ['yt_li', 'logged_in']) {
    if (flags[key] === '0') return { loggedIn: false, signal: key };
  }
  return { loggedIn: null, signal: '' };
}

export async function check(payload, { createClient, cookieJar = new Map(), log = () => {} } = {}) {
  const refreshed = () => Object.fromEntries(cookieJar);
  try {
    const yt = await createClient(cookieJar);
    const response = await yt.actions.execute('/account/account_menu', { client: 'WEB' });
    const login = loginFromMenu(response?.data ?? response);
    let channel = null;
    const target = parseChannelTarget(payload.channel);
    if (login.loggedIn && target) {
      // Канал аккаунта виден у этого входа? Только список, без переключения. Не нашёлся — вход всё равно жив.
      try {
        const identities = await listIdentities(yt, { log, until: (found) => !!matchIdentity(found, target) });
        channel = { found: !!matchIdentity(identities, target), label: target.label };
      } catch (error) {
        log(`channel list failed: ${errorDetail(error).slice(0, 160)}`);
      }
    }
    return { ok: true, ...login, channel, refreshedCookies: refreshed() };
  } catch (error) {
    return { ok: false, error: errorDetail(error), refreshedCookies: refreshed() };
  }
}

async function main(cookieJar) {
  const payload = JSON.parse(await readStdin());
  const { Innertube } = await import('youtubei.js');
  let agent = null;
  if (payload.proxyUrl) {
    const { ProxyAgent } = await import('undici');
    agent = new ProxyAgent(payload.proxyUrl);
  }
  const result = await check(payload, {
    cookieJar,
    createClient: (jar) => Innertube.create({ cookie: payload.cookie, fetch: clientFetch({ cookieJar: jar, agent }) }),
    log: (line) => process.stderr.write(`${line}\n`)
  });
  process.stdout.write(JSON.stringify(result));
}

function isMain() {
  if (!process.argv[1]) return false;
  try {
    return realpathSync(process.argv[1]) === realpathSync(fileURLToPath(import.meta.url));
  } catch {
    return false;
  }
}

if (isMain()) {
  const cookieJar = new Map();
  main(cookieJar).catch((error) => {
    process.stdout.write(JSON.stringify({
      ok: false, error: String(error && error.message ? error.message : error).slice(0, 300),
      refreshedCookies: Object.fromEntries(cookieJar)
    }));
    process.exitCode = 1;
  });
}
