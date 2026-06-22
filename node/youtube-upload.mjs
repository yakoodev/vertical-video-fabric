import fs from 'node:fs/promises';
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

// Google rotates a few session cookies (notably __Secure-1PSIDTS/3PSIDTS) on
// almost every request; if we never persist them the stored jar goes stale in
// days. Capture every Set-Cookie we see and report the fresh values so the
// caller can write them back — this keeps the session alive for months.
function collectSetCookies(headers, jar) {
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

async function main() {
  const payload = JSON.parse(await readStdin());
  const file = await fs.readFile(payload.filePath);
  const proxyUrl = payload.proxyUrl || '';
  const agent = proxyUrl ? new ProxyAgent(proxyUrl) : null;
  const cookieJar = new Map();
  const yt = await Innertube.create({
    cookie: payload.cookie,
    fetch: async (input, init = {}) => {
      const res = agent
        ? await fetch(input, { ...init, dispatcher: agent })
        : await fetch(input, init);
      collectSetCookies(res.headers, cookieJar);
      return res;
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
  process.stdout.write(JSON.stringify({
    ok: true,
    videoId,
    refreshedCookies: Object.fromEntries(cookieJar),
    response: safe
  }));
}

main().catch((error) => {
  process.stdout.write(JSON.stringify({
    ok: false,
    error: error && error.stack ? error.stack : String(error)
  }));
  process.exitCode = 1;
});
