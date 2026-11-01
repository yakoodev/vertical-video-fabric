import { realpathSync } from 'node:fs';
import fs from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

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

export async function run(payload, { createClient, readFile = fs.readFile, sleep, log = () => {} } = {}) {
  const file = await readFile(payload.filePath);
  const cookieJar = new Map();
  const retries = Math.max(0, Number(payload.retries) || 0);
  const delayMs = Math.max(0, Number(payload.retrySeconds) || 0) * 1000;
  const attempts = { upload: 0 };
  const yt = await withRetry(() => createClient(cookieJar), {
    retries, delayMs, sleep, onRetry: (n, e) => log(`innertube create retry ${n}: ${errorText(e)}`)
  });
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
  const extra = { uploadAttempts: attempts.upload };
  // Best effort: видео уже загружено, неудача с метаданными/миниатюрой не делает загрузку проваленной.
  const update = metadataUpdate(payload);
  if (videoId && Object.keys(update).length) {
    try {
      await yt.studio.updateVideoMetadata(videoId, update);
      extra.metadataSet = update;
    } catch (error) {
      extra.metadataError = errorText(error);
    }
  }
  // Обычное (горизонтальное 16:9) видео: обложка — миниатюрой.
  if (payload.thumbnailPath && videoId) {
    try {
      const thumbnail = await readFile(payload.thumbnailPath);
      await yt.studio.updateVideoMetadata(videoId, { thumbnail: new Uint8Array(thumbnail) });
      extra.thumbnailSet = true;
    } catch (error) {
      extra.thumbnailError = errorText(error);
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

async function main() {
  const payload = JSON.parse(await readStdin());
  const { Innertube } = await import('youtubei.js');
  const proxyUrl = payload.proxyUrl || '';
  let agent = null;
  if (proxyUrl) {
    const { ProxyAgent } = await import('undici');
    agent = new ProxyAgent(proxyUrl);
  }
  const result = await run(payload, {
    createClient: (cookieJar) => Innertube.create({
      cookie: payload.cookie,
      fetch: async (input, init = {}) => {
        const res = agent
          ? await fetch(input, { ...init, dispatcher: agent })
          : await fetch(input, init);
        collectSetCookies(res.headers, cookieJar);
        return res;
      }
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
  main().catch((error) => {
    process.stdout.write(JSON.stringify({
      ok: false,
      error: error && error.stack ? error.stack : String(error)
    }));
    process.exitCode = 1;
  });
}
