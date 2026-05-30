import fs from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { Innertube } from 'youtubei.js';
import { ProxyAgent } from 'undici';

async function readStdin() {
  const chunks = [];
  for await (const chunk of process.stdin) {
    chunks.push(chunk);
  }
  return Buffer.concat(chunks).toString('utf8');
}

function findVideoId(value, depth = 0) {
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

function getCookie(cookie, name) {
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = cookie.match(new RegExp(`(?:^|;\\s*)${escaped}=([^;]+)`));
  return match ? match[1] : '';
}

function sidAuth(cookie, origin) {
  const sapisid = getCookie(cookie, 'SAPISID') || getCookie(cookie, '__Secure-3PAPISID') || getCookie(cookie, '__Secure-1PAPISID');
  if (!sapisid) return '';
  const timestamp = Math.floor(Date.now() / 1000);
  const digest = createHash('sha1').update(`${timestamp} ${sapisid} ${origin}`).digest('hex');
  return `SAPISIDHASH ${timestamp}_${digest}`;
}

async function main() {
  const payload = JSON.parse(await readStdin());
  const file = await fs.readFile(payload.filePath);
  const proxyUrl = payload.proxyUrl || '';
  const agent = proxyUrl ? new ProxyAgent(proxyUrl) : null;
  const yt = await Innertube.create({
    cookie: payload.cookie,
    fetch: async (input, init = {}) => {
      const rawUrl = typeof input === 'string' ? input : input.url;
      const url = new URL(rawUrl, 'https://www.youtube.com');
      const headers = new Headers(init.headers || (typeof input === 'string' ? undefined : input.headers));
      if (url.hostname === 'upload.youtube.com') {
        const authOrigin = 'https://www.youtube.com';
        const auth = sidAuth(payload.cookie, authOrigin);
        if (auth) {
          headers.set('Authorization', auth);
        }
        headers.set('Origin', authOrigin);
        headers.set('X-Origin', authOrigin);
        headers.set('Referer', `${authOrigin}/`);
      }
      const nextInit = { ...init, headers };
      if (agent) {
        return fetch(input, { ...nextInit, dispatcher: agent });
      }
      return fetch(input, nextInit);
    }
  });
  const response = await yt.studio.upload(file, {
    title: payload.title,
    description: payload.description || '',
    privacy: payload.privacy || 'PUBLIC',
    is_draft: false
  });
  const safe = jsonSafe(response);
  const videoId = findVideoId(safe);
  process.stdout.write(JSON.stringify({ ok: true, videoId, response: safe }));
}

main().catch((error) => {
  process.stdout.write(JSON.stringify({
    ok: false,
    error: error && error.stack ? error.stack : String(error)
  }));
  process.exitCode = 1;
});
