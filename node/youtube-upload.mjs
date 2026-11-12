import { realpathSync } from 'node:fs';
import fs from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

import { activeChannel, brandUploadRequest, ChannelError, publicIdentities, selectChannel } from './youtube-channel.mjs';

async function readStdin() {
  const chunks = [];
  for await (const chunk of process.stdin) {
    chunks.push(chunk);
  }
  return Buffer.concat(chunks).toString('utf8');
}

export function findVideoId(value, depth = 0) {
  if (!value || depth > 8) return '';
  if (typeof value !== 'object') return '';
  for (const [key, child] of Object.entries(value)) {
    if (['videoId', 'video_id', 'externalVideoId', 'encryptedVideoId'].includes(key) && typeof child === 'string') {
      return child;
    }
  }
  for (const child of Object.values(value)) {
    const found = findVideoId(child, depth + 1);
    if (found) return found;
  }
  return '';
}

function jsonSafe(value) {
  try {
    return JSON.parse(JSON.stringify(value));
  } catch {
    return { string: String(value) };
  }
}

function errorText(error) {
  return String(error && error.message ? error.message : error).slice(0, 300);
}

// youtubei.js на не-2xx бросает InnertubeError('… status code 400', <тело ответа>): само сообщение
// без тела («Request contains an invalid argument» и какое именно поле) ничего не объясняет, поэтому
// в журнал/результат кладём и тело. Тело ответа — ошибка сервера, cookie и заголовков в нём нет.
export function errorDetail(error) {
  const base = errorText(error);
  const info = error && error.info;
  if (!info) return base;
  let text;
  try {
    text = typeof info === 'string' ? info : JSON.stringify(info);
  } catch {
    return base;
  }
  text = String(text).replace(/\s+/g, ' ').trim().slice(0, 700);
  return text ? `${base} | ${text}` : base;
}

// Google rotates a few session cookies (notably __Secure-1PSIDTS/3PSIDTS) on
// almost every request; if we never persist them the stored jar goes stale in
// days. Capture every Set-Cookie we see and report the fresh values so the
// caller can write them back — this keeps the session alive for months.
export function collectSetCookies(headers, jar) {
  try {
    const lines = typeof headers.getSetCookie === 'function' ? headers.getSetCookie() : [];
    for (const line of lines) {
      const first = String(line).split(';', 1)[0];
      const eq = first.indexOf('=');
      if (eq <= 0) continue;
      const name = first.slice(0, eq).trim();
      const value = first.slice(eq + 1).trim();
      // Skip deletions (empty value) so we never wipe a good cookie.
      if (name && value) jar.set(name, value);
    }
  } catch {
    /* header API not available — ignore */
  }
}

// Ошибки youtubei.js до создания видео (/upload/createvideo): файл ещё не стал роликом,
// повтор безопасен. Сетевой сбой без такого текста мог случиться и на createvideo — его
// не повторяем, иначе ролик мог бы появиться на канале дважды.
const RETRYABLE_UPLOAD_ERRORS = [
  'Could not get initial upload data',
  'Could not upload video',
  'Could not process video'
];

export function isRetryableUploadError(error) {
  const text = errorText(error);
  return RETRYABLE_UPLOAD_ERRORS.some((marker) => text.includes(marker));
}

export async function withRetry(fn, { retries = 0, delayMs = 0, shouldRetry = () => true, sleep, onRetry } = {}) {
  const wait = sleep || ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));
  for (let attempt = 1; ; attempt += 1) {
    try {
      return await fn(attempt);
    } catch (error) {
      if (attempt > retries || !shouldRetry(error)) throw error;
      if (onRetry) onRetry(attempt, error);
      await wait(delayMs * attempt);
    }
  }
}

// Аудитория и категория после загрузки (studio.upload их не принимает): «для детей» YouTube
// требует указать у каждого ролика; не указанная — висит «не задано» в Студии.
export function metadataUpdate(payload) {
  const update = {};
  if (typeof payload.madeForKids === 'boolean') update.made_for_kids = payload.madeForKids;
  if (Number.isInteger(payload.categoryId) && payload.categoryId > 0) update.category = payload.categoryId;
  return update;
}

// Не studio.updateVideoMetadata: он шлёт protobuf от клиента ANDROID, и на cookie-входе YouTube отвечает
// 400 INVALID_ARGUMENT (проверено 12.11 на 5ItVRliP5bY, тело — без имени поля). Студия в браузере шлёт тот
// же /video_manager/metadata_update JSON-ом от WEB_CREATOR — так он проходит (200). Имена перечислений
// сверены с сервером: на чужое значение он отвечает «Invalid value at 'made_for_kids.new_mfk'
// (…MdeMadeForKidsType)», а все три ниже принимает. Канал-бренд — как у всех innertube-запросов youtubei.js:
// context.user.onBehalfOfUser и X-Goog-PageId.
export const MFK_OPERATION_SET = 'MDE_MADE_FOR_KIDS_UPDATE_OPERATION_SET';
export const MFK_NOT_FOR_KIDS = 'MDE_MADE_FOR_KIDS_TYPE_NOT_MFK';
export const MFK_FOR_KIDS = 'MDE_MADE_FOR_KIDS_TYPE_MFK';

/** Тело /video_manager/metadata_update (Студия, WEB_CREATOR) для аудитории и категории; нечего менять — null. */
export function metadataUpdateRequest(videoId, update) {
  const body = { client: 'WEB_CREATOR', encryptedVideoId: videoId };
  if (typeof update.made_for_kids === 'boolean') {
    body.madeForKids = { operation: MFK_OPERATION_SET, newMfk: update.made_for_kids ? MFK_FOR_KIDS : MFK_NOT_FOR_KIDS };
  }
  if (Number.isInteger(update.category) && update.category > 0) body.category = { newCategoryId: update.category };
  return Object.keys(body).length > 2 ? body : null;
}

// Что Студия видит у ролика после правки. audienceRestriction — настройка «для детей» (COPPA):
// selfRating VIDEO_TARGETED_AUDIENCE_ALL — «не для детей», …_KIDS — «для детей»; category — внутреннее
// имя (20 «Игры» = CREATOR_VIDEO_CATEGORY_GADGETS).
export async function readVideoMetadata(yt, videoId) {
  const response = await yt.actions.execute('/creator/get_creator_videos', {
    client: 'WEB_CREATOR',
    videoIds: [videoId],
    mask: { videoId: true, category: true, audienceRestriction: { all: true } },
    failOnError: true
  });
  const video = response?.data?.videos?.[0];
  if (!video) return null;
  return {
    category: video.category || '',
    audience: video.audienceRestriction?.selfRating || '',
    audienceBy: video.audienceRestriction?.imposer || ''
  };
}

// cookieJar — снаружи (main), чтобы свежие cookie ушли в ответ и при исключении: 10.11 задание 117
// упало на загрузке, ответ без них — сохранённые cookie устарели, Google разлогинил вход.
export async function run(payload, { createClient, readFile = fs.readFile, sleep, log = () => {}, cookieJar = new Map() } = {}) {
  const retries = Math.max(0, Number(payload.retries) || 0);
  const delayMs = Math.max(0, Number(payload.retrySeconds) || 0) * 1000;
  const attempts = { upload: 0 };
  const create = (options = {}) => withRetry(() => createClient(cookieJar, options), {
    retries, delayMs, sleep, onRetry: (n, e) => log(`innertube create retry ${n}: ${errorText(e)}`)
  });
  let yt = await create();
  // Канал аккаунта (accounts.handle): переключиться на него и убедиться ДО загрузки, что активен
  // именно он; иначе — отказ без загрузки. Без канала — как раньше, но в ответе видно, куда ушло.
  let channel = null;
  let identities = [];
  if (payload.channel) {
    try {
      const selected = await selectChannel(yt, payload.channel, { createClient: create, log });
      yt = selected.yt;
      channel = selected.active;
      identities = publicIdentities(selected.identities);
    } catch (error) {
      if (!(error instanceof ChannelError)) throw error;
      // Свежие cookie — и при отказе: Google мог уже сменить __Secure-*PSIDTS.
      return {
        ok: false, error: error.message, channelError: true, identities: error.identities || [],
        refreshedCookies: Object.fromEntries(cookieJar)
      };
    }
  } else {
    try {
      channel = await activeChannel(yt);
    } catch (error) {
      log(`active channel unknown: ${errorText(error)}`);
    }
  }
  const channelInfo = channel ? { id: channel.channelId, name: channel.name, handle: channel.handle } : null;
  // Проверка без загрузки: каналы входа и итог переключения.
  if (payload.dryRun) {
    return { ok: true, dryRun: true, channel: channelInfo, identities, refreshedCookies: Object.fromEntries(cookieJar) };
  }
  const file = await readFile(payload.filePath);
  const response = await withRetry(
    (attempt) => {
      attempts.upload = attempt;
      return yt.studio.upload(file, {
        title: payload.title,
        description: payload.description || '',
        privacy: payload.privacy || 'PUBLIC',
        is_draft: false
      });
    },
    {
      retries, delayMs, sleep, shouldRetry: isRetryableUploadError,
      onRetry: (n, e) => log(`upload retry ${n}: ${errorText(e)}`)
    }
  );
  const safe = jsonSafe(response);
  const videoId = findVideoId(safe);
  const extra = { uploadAttempts: attempts.upload, channel: channelInfo };
  // Best effort: видео уже загружено, неудача с метаданными/миниатюрой не делает загрузку проваленной.
  const update = metadataUpdate(payload);
  const request = videoId ? metadataUpdateRequest(videoId, update) : null;
  if (request) {
    try {
      await yt.actions.execute('/video_manager/metadata_update', request);
      extra.metadataSet = update;
    } catch (error) {
      extra.metadataError = errorDetail(error);
    }
    if (extra.metadataSet) {
      // Проверка: ответ правки пустой ({}), применилось ли — видно только чтением.
      try {
        extra.metadataCheck = await readVideoMetadata(yt, videoId);
      } catch (error) {
        extra.metadataCheckError = errorDetail(error);
      }
    }
  }
  // Обычное (горизонтальное 16:9) видео: обложка — миниатюрой.
  if (payload.thumbnailPath && videoId) {
    try {
      const thumbnail = await readFile(payload.thumbnailPath);
      await yt.studio.updateVideoMetadata(videoId, { thumbnail: new Uint8Array(thumbnail) });
      extra.thumbnailSet = true;
    } catch (error) {
      extra.thumbnailError = errorDetail(error);
    }
  }
  return {
    ok: true,
    videoId,
    ...extra,
    refreshedCookies: Object.fromEntries(cookieJar),
    response: safe
  };
}

/** fetch клиента: прокси аккаунта, сбор свежих cookie и поправка загрузки на канал-бренд. */
export function clientFetch({ cookieJar, agent = null, target = {}, fetchImpl = fetch }) {
  return async (input, init = {}) => {
    const patched = brandUploadRequest(input, init, target);
    const [url, options] = patched ? [patched.url, patched.init] : [input, init];
    const res = await fetchImpl(url, agent ? { ...options, dispatcher: agent } : options);
    collectSetCookies(res.headers, cookieJar);
    return res;
  };
}

async function main(cookieJar) {
  const payload = JSON.parse(await readStdin());
  const { Innertube } = await import('youtubei.js');
  const proxyUrl = payload.proxyUrl || '';
  let agent = null;
  if (proxyUrl) {
    const { ProxyAgent } = await import('undici');
    agent = new ProxyAgent(proxyUrl);
  }
  const result = await run(payload, {
    cookieJar,
    // accountIndex — вход Google (X-Goog-AuthUser), onBehalfOfUser — pageId канала-бренда,
    // channelId — id этого канала (для createvideo).
    createClient: (jar, { accountIndex = 0, onBehalfOfUser, channelId } = {}) => Innertube.create({
      cookie: payload.cookie,
      account_index: accountIndex,
      on_behalf_of_user: onBehalfOfUser,
      fetch: clientFetch({ cookieJar: jar, agent, target: { authUser: accountIndex, pageId: onBehalfOfUser, channelId } })
    }),
    log: (line) => process.stderr.write(`${line}\n`)
  });
  process.stdout.write(JSON.stringify(result));
}

// Запуск как помощник (node node/youtube-upload.mjs), а не импорт из теста; realpath — чтобы
// путь через симлинк тоже узнавался.
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
      ok: false,
      error: error && error.stack ? error.stack : String(error),
      refreshedCookies: Object.fromEntries(cookieJar)
    }));
    process.exitCode = 1;
  });
}
