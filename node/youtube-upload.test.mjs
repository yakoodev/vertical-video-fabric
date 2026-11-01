// Помощник загрузки YouTube без сети: клиент youtubei.js подменён (node --test node/youtube-upload.test.mjs).
import assert from 'node:assert/strict';
import test from 'node:test';

import { isRetryableUploadError, metadataUpdate, run, withRetry } from './youtube-upload.mjs';

function fakeClient({ uploadErrors = [], metadataError = null } = {}) {
  const calls = { upload: 0, metadata: [] };
  const client = {
    studio: {
      async upload(file, metadata) {
        calls.upload += 1;
        calls.lastUpload = metadata;
        const error = uploadErrors.shift();
        if (error) throw error;
        return { data: { videoId: 'abc123XYZ' } };
      },
      async updateVideoMetadata(videoId, update) {
        calls.metadata.push({ videoId, update });
        if (metadataError && !update.thumbnail) throw metadataError;
        return {};
      }
    }
  };
  return { client, calls };
}

const base = { filePath: 'clip.mp4', title: 'T', description: 'D', privacy: 'PUBLIC', retries: 2, retrySeconds: 0 };
const readFile = async () => Buffer.from('video');
const sleep = async () => {};

test('upload sets audience and category after the video exists', async () => {
  const { client, calls } = fakeClient();
  const result = await run({ ...base, madeForKids: false, categoryId: 20 }, { createClient: () => client, readFile, sleep });
  assert.equal(result.ok, true);
  assert.equal(result.videoId, 'abc123XYZ');
  assert.equal(result.uploadAttempts, 1);
  assert.deepEqual(calls.metadata, [{ videoId: 'abc123XYZ', update: { made_for_kids: false, category: 20 } }]);
  assert.deepEqual(result.metadataSet, { made_for_kids: false, category: 20 });
  assert.deepEqual(calls.lastUpload, { title: 'T', description: 'D', privacy: 'PUBLIC', is_draft: false });
});

test('no audience/category settings — no extra metadata call (old behaviour)', async () => {
  const { client, calls } = fakeClient();
  const result = await run(base, { createClient: () => client, readFile, sleep });
  assert.equal(calls.metadata.length, 0);
  assert.equal(result.metadataSet, undefined);
});

test('metadata failure does not fail the upload', async () => {
  const { client } = fakeClient({ metadataError: new Error('metadata_update 400') });
  const result = await run({ ...base, madeForKids: false }, { createClient: () => client, readFile, sleep });
  assert.equal(result.ok, true);
  assert.match(result.metadataError, /metadata_update 400/);
});

test('pre-createvideo failures are retried, others are not', async () => {
  const flaky = fakeClient({ uploadErrors: [new Error('Could not upload video'), new Error('Could not get initial upload data')] });
  const ok = await run(base, { createClient: () => flaky.client, readFile, sleep });
  assert.equal(ok.uploadAttempts, 3);
  assert.equal(flaky.calls.upload, 3);

  const ambiguous = fakeClient({ uploadErrors: [new TypeError('fetch failed')] });
  await assert.rejects(run(base, { createClient: () => ambiguous.client, readFile, sleep }), /fetch failed/);
  assert.equal(ambiguous.calls.upload, 1);
});

test('retries stop at the limit', async () => {
  const broken = fakeClient({ uploadErrors: Array.from({ length: 5 }, () => new Error('Could not process video.')) });
  await assert.rejects(run(base, { createClient: () => broken.client, readFile, sleep }), /Could not process video/);
  assert.equal(broken.calls.upload, 3);
});

test('helpers', async () => {
  assert.equal(isRetryableUploadError(new Error('Could not upload video')), true);
  assert.equal(isRetryableUploadError(new Error('You must be signed in')), false);
  assert.deepEqual(metadataUpdate({ madeForKids: true, categoryId: 0 }), { made_for_kids: true });
  assert.deepEqual(metadataUpdate({}), {});
  let n = 0;
  assert.equal(await withRetry(async () => { n += 1; if (n < 2) throw new Error('x'); return 'ok'; }, { retries: 1, sleep }), 'ok');
});
