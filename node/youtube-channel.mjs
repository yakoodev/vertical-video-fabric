// Выбор канала YouTube перед загрузкой. Один вход Google (cookie) может держать несколько
// входов (authuser=0,1,2…) и у каждого — личный канал и каналы-бренды (pageId). Переключение
// канала в браузере в cookie не попадает: браузер шлёт его в каждом запросе (X-Goog-AuthUser,
// X-Goog-PageId / context.user.onBehalfOfUser). Без выбора youtubei.js грузит на канал
// authuser=0 — так 10.11 ролик для «Yakoo dev» ушёл на «Vtubika store».

const CHANNEL_ID_RE = /^UC[\w-]{22}$/;
const SWITCHER_REQUEST = {
  requestType: 'ACCOUNTS_LIST_REQUEST_TYPE_CHANNEL_SWITCHER',
  callCircumstance: 'SWITCHING_USERS_FULL',
  client: 'WEB'
};
// Сколько входов Google (authuser) перебирать в поисках канала. Google пускает до 10.
export const MAX_LOGINS = 10;

export class ChannelError extends Error {}

function text(value) {
  if (!value || typeof value !== 'object') return typeof value === 'string' ? value : '';
  if (typeof value.simpleText === 'string') return value.simpleText;
  if (Array.isArray(value.runs)) return value.runs.map((run) => run.text || '').join('');
  return '';
}

function normHandle(value) {
  let handle = String(value || '').trim();
  try {
    handle = decodeURIComponent(handle);
  } catch {
    /* не %-кодировка — как есть */
  }
  return handle.replace(/^@/, '').toLowerCase();
}

/** Цель из настройки аккаунта: id канала (UC…), @ник или имя канала; пусто — null. */
export function parseChannelTarget(raw) {
  const value = String(raw || '').trim();
  if (!value) return null;
  if (CHANNEL_ID_RE.test(value)) return { id: value, label: value };
  if (value.startsWith('@')) return { handle: normHandle(value), label: value };
  return { name: value.toLowerCase(), handle: normHandle(value), label: value };
}

function walk(value, visit, depth = 0) {
  if (!value || typeof value !== 'object' || depth > 40) return;
  visit(value);
  for (const child of Object.values(value)) walk(child, visit, depth + 1);
}

function authUserOf(signinUrl, fallback) {
  const match = /[?&]authuser=(\d+)/.exec(String(signinUrl || ''));
  return match ? Number(match[1]) : fallback;
}

/** Каналы из ответа /account/accounts_list (переключатель каналов одного входа). */
export function identitiesFromAccountsList(data, accountIndex = 0) {
  const out = [];
  walk(data, (node) => {
    const item = node.accountItemRenderer || node.accountItem;
    if (!item || typeof item !== 'object') return;
    const tokens = item.serviceEndpoint?.selectActiveIdentityEndpoint?.supportedTokens || [];
    const identity = {
      name: text(item.accountName),
      handle: text(item.channelHandle),
      channelId: '',
      pageId: '',
      authUser: accountIndex,
      selected: !!item.isSelected,
      hasChannel: !!item.hasChannel
    };
    for (const token of tokens) {
      if (token.pageIdToken?.pageId) identity.pageId = String(token.pageIdToken.pageId);
      // clientCacheKey — id канала без «UC» (проверено на живом ответе 10.11).
      const key = token.offlineCacheKeyToken?.clientCacheKey;
      if (key && !identity.channelId) identity.channelId = CHANNEL_ID_RE.test(key) ? key : `UC${key}`;
      if (token.accountSigninToken?.signinUrl) {
        identity.authUser = authUserOf(token.accountSigninToken.signinUrl, accountIndex);
      }
    }
    if (!CHANNEL_ID_RE.test(identity.channelId)) identity.channelId = '';
    out.push(identity);
  });
  return out;
}

/** Активный канал из /account/account_menu: шапка меню «Посмотреть канал» → browseId. */
export function activeChannelFromMenu(data) {
  let found = null;
  walk(data, (node) => {
    const header = node.activeAccountHeaderRenderer;
    if (!header || found) return;
    let channelId = '';
    walk(header, (part) => {
      const id = part.browseEndpoint?.browseId;
      if (!channelId && typeof id === 'string' && CHANNEL_ID_RE.test(id)) channelId = id;
    });
    found = { channelId, name: text(header.accountName), handle: text(header.channelHandle) };
  });
  return found;
}

/** Подходящий канал: по id, иначе по @нику, иначе по точному имени. */
export function matchIdentity(identities, target) {
  if (!target) return null;
  if (target.id) return identities.find((it) => it.channelId === target.id) || null;
  const byHandle = target.handle && identities.find((it) => it.handle && normHandle(it.handle) === target.handle);
  if (byHandle) return byHandle;
  if (!target.name) return null;
  return identities.find((it) => it.name.trim().toLowerCase() === target.name) || null;
}

export function describeChannel(channel) {
  if (!channel) return '?';
  const parts = [channel.name || channel.handle || '', channel.channelId || ''].filter(Boolean);
  return parts.length > 1 ? `«${parts[0]}» (${parts[1]})` : parts[0] ? `«${parts[0]}»` : '?';
}

/** Каналы без секретов — для результата и журнала (pageId туда не идёт). */
export function publicIdentities(identities) {
  return identities.map((it) => ({ name: it.name, handle: it.handle, channelId: it.channelId, authUser: it.authUser }));
}

export async function activeChannel(yt) {
  const response = await yt.actions.execute('/account/account_menu', { client: 'WEB' });
  return activeChannelFromMenu(response?.data ?? response);
}

/**
 * Каналы всех входов из cookie: authuser 0, 1, 2… у одного клиента (заголовок X-Goog-AuthUser
 * берётся из session.account_index на каждый запрос). Пустой/закрытый вход (403) пропускаем.
 * ``until`` — остановиться, как только нашёлся нужный.
 */
export async function listIdentities(yt, { maxLogins = MAX_LOGINS, until, log = () => {} } = {}) {
  const session = yt.session;
  const original = session.account_index;
  const seen = new Map();
  try {
    for (let index = 0; index < maxLogins; index += 1) {
      session.account_index = index;
      let response;
      try {
        response = await yt.actions.execute('/account/accounts_list', { ...SWITCHER_REQUEST });
      } catch (error) {
        log(`accounts_list authuser=${index}: ${String(error && error.message ? error.message : error).slice(0, 120)}`);
        continue;
      }
      for (const identity of identitiesFromAccountsList(response?.data ?? response, index)) {
        const key = `${identity.channelId}|${identity.pageId}|${identity.authUser}`;
        if (!seen.has(key)) seen.set(key, identity);
      }
      if (until && until([...seen.values()])) break;
    }
  } finally {
    session.account_index = original;
  }
  return [...seen.values()];
}

/**
 * Клиент, который действует от нужного канала, — проверенный ДО загрузки.
 * ``createClient({accountIndex, onBehalfOfUser})`` создаёт новый клиент youtubei.js.
 * Не нашёлся канал или после переключения активен другой — ChannelError, загрузки не будет.
 */
export async function selectChannel(baseClient, rawTarget, { createClient, maxLogins, log = () => {} }) {
  const target = parseChannelTarget(rawTarget);
  if (!target) throw new ChannelError('канал YouTube не задан');
  const identities = await listIdentities(baseClient, {
    maxLogins, log, until: (found) => !!matchIdentity(found, target)
  });
  const identity = matchIdentity(identities, target);
  if (!identity) {
    const known = identities.map(describeChannel).join(', ') || 'ни одного';
    const error = new ChannelError(`канал ${target.label} не найден у этого входа (есть: ${known})`);
    error.identities = publicIdentities(identities);
    throw error;
  }
  const yt = identity.authUser === 0 && !identity.pageId
    ? baseClient
    : await createClient({
      accountIndex: identity.authUser, onBehalfOfUser: identity.pageId || undefined, channelId: identity.channelId || undefined
    });
  let active;
  try {
    active = await activeChannel(yt);
  } catch (error) {
    throw new ChannelError(`не удалось проверить активный канал: ${String(error && error.message ? error.message : error).slice(0, 160)}`);
  }
  const wanted = identity.channelId || '';
  const ok = active && (wanted
    ? active.channelId === wanted
    : !!active.handle && normHandle(active.handle) === normHandle(identity.handle));
  if (!ok) {
    const error = new ChannelError(`активен другой канал: ${describeChannel(active)}, а нужен ${describeChannel(identity)}`);
    error.identities = publicIdentities(identities);
    throw error;
  }
  return { yt, identity, active, identities };
}

const UPLOAD_HOST = 'upload.youtube.com';

function requestParts(input, init) {
  const isRequest = input && typeof input === 'object' && !(input instanceof URL) && typeof input.url === 'string';
  return {
    url: new URL(isRequest ? input.url : String(input)),
    method: init.method || (isRequest ? input.method : '') || 'GET',
    headers: new Headers(init.headers ?? (isRequest ? input.headers : undefined) ?? {})
  };
}

/**
 * Поправка запросов загрузки для входа не по умолчанию / канала-бренда — подмена в fetch
 * клиента youtubei.js (node_modules не трогаем). ``target`` — { authUser, pageId, channelId }.
 * Возвращает { url, init } для fetch или null — запрос идёт как есть.
 *
 * Почему: youtubei.js (HTTPClient) вешает на начало загрузки (upload.youtube.com,
 * x-goog-upload-command: start) те же заголовки, что на innertube, вместе с X-Goog-PageId.
 * Сервер загрузки (Scotty) знает только вход Google, а не канал-бренд: с PageId он отвечает
 * 401 REJECTED_NOT_AUTHENTICATED (10.11 задание 117; LuanRT/YouTube.js#485 — то же у брендов).
 * Студия в браузере начинает загрузку как сам вход (cookie + ?authuser=N, без PageId), а канал
 * задаёт уже в /upload/createvideo. Так и делаем: у start — без PageId, вход и заголовком,
 * и ?authuser; в createvideo (идёт на www.youtube.com с PageId/onBehalfOfUser, как всё
 * innertube) — ещё и channelId канала, как шлёт Студия, чтобы ролик не лёг на другой канал.
 * Вход по умолчанию без бренда (authuser 0) не трогаем — этот путь проверен живьём.
 */
export function brandUploadRequest(input, init = {}, target = {}) {
  const authUser = Number(target.authUser) || 0;
  const pageId = target.pageId ? String(target.pageId) : '';
  if (!authUser && !pageId) return null;
  const { url, method, headers } = requestParts(input, init);
  const command = String(headers.get('x-goog-upload-command') || '').trim().toLowerCase();
  if (url.hostname === UPLOAD_HOST && command === 'start') {
    headers.delete('X-Goog-PageId');
    headers.set('X-Goog-AuthUser', String(authUser));
    url.searchParams.set('authuser', String(authUser));
    return { url: url.toString(), init: { ...init, method, headers } };
  }
  const channelId = CHANNEL_ID_RE.test(String(target.channelId || '')) ? target.channelId : '';
  if (pageId && channelId && url.pathname.endsWith('/upload/createvideo') && typeof init.body === 'string') {
    let body;
    try {
      body = JSON.parse(init.body);
    } catch {
      return null;
    }
    body.channelId = channelId;
    return { url: url.toString(), init: { ...init, method, headers, body: JSON.stringify(body) } };
  }
  return null;
}
