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

async function main() {
  const payload = JSON.parse(await readStdin());
  const file = await fs.readFile(payload.filePath);
  const proxyUrl = payload.proxyUrl || '';
  const agent = proxyUrl ? new ProxyAgent(proxyUrl) : null;
  const yt = await Innertube.create({
    cookie: payload.cookie,
    fetch: async (input, init = {}) => {
      if (agent) {
        return fetch(input, { ...init, dispatcher: agent });
      }
      return fetch(input, init);
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
