// Помощник загрузки YouTube без сети: клиент youtubei.js подменён (node --test node/youtube-upload.test.mjs).
import assert from 'node:assert/strict';
import test from 'node:test';

import { activeChannelFromMenu, identitiesFromAccountsList, matchIdentity, parseChannelTarget } from './youtube-channel.mjs';
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

// --- Канал: у одного входа Google несколько каналов (10.11 ролик ушёл не на тот) ---------------------- //

const VTUBIKA = 'UCFqc0NBJUuaUuTa6hLsD53w';
const DEV = 'UCk6k6kwZyYr3LMDAqyQ7pdg';
const PERSONAL = 'UCSBt6RRscIXKNOYHI5VNv1g';

function accountItem({ name, handle, channelId, pageId, authUser, selected = false }) {
  const tokens = [];
  if (pageId) tokens.push({ pageIdToken: { pageId } });
  tokens.push({ accountStateToken: { hasChannel: true, isMerged: true, obfuscatedGaiaId: '1' } });
  tokens.push({ offlineCacheKeyToken: { clientCacheKey: channelId.slice(2) } });
  const signinUrl = `/signin?action_handle_signin=true&authuser=${authUser}${pageId ? `&pageid=${pageId}` : ''}&next=%2F`;
  tokens.push({ accountSigninToken: { signinUrl } });
  return {
    accountItemRenderer: {
      accountName: { simpleText: name }, isSelected: selected, hasChannel: true,
      channelHandle: { simpleText: handle },
      serviceEndpoint: { selectActiveIdentityEndpoint: { supportedTokens: tokens } }
    }
  };
}

// Ответ переключателя каналов, как пришёл 10.11 (только нужные поля): authuser=1 — 403.
const LOGINS = {
  0: [accountItem({ name: 'Vtubika store', handle: '@vtubika', channelId: VTUBIKA, authUser: 0, selected: true })],
  2: [
    accountItem({ name: 'Яков Мишка', handle: '@ЯковМишка-о2ь', channelId: PERSONAL, authUser: 2, selected: true }),
    accountItem({ name: 'Yakoo dev', handle: '@yakoodev', channelId: DEV, pageId: '113000', authUser: 2 })
  ]
};
const switcher = (items) => ({
  actions: [{ updateChannelSwitcherPageAction: { page: { channelSwitcherPageRenderer: { contents: items } } } }]
});
const menu = (name, handle, channelId) => ({
  actions: [{ openPopupAction: { popup: { multiPageMenuRenderer: { header: { activeAccountHeaderRenderer: {
    accountName: { simpleText: name }, channelHandle: { simpleText: handle },
    manageAccountTitle: { runs: [{ text: 'View your channel', navigationEndpoint: { browseEndpoint: { browseId: channelId } } }] }
  } } } } } }]
});
const CHANNELS = {
  [VTUBIKA]: ['Vtubika store', '@vtubika'], [DEV]: ['Yakoo dev', '@yakoodev'], [PERSONAL]: ['Яков Мишка', '@ЯковМишка-о2ь']
};

// Клиент youtubei.js понарошку: какой канал активен — по authuser + pageId, как у YouTube.
function channelClient({ accountIndex = 0, onBehalfOfUser, activeOverride } = {}, calls) {
  const session = { account_index: accountIndex };
  return {
    session,
    studio: {
      async upload() { calls.upload += 1; return { data: { videoId: 'abc123XYZ' } }; },
      async updateVideoMetadata() { return {}; }
    },
    actions: {
      async execute(path) {
        if (path === '/account/accounts_list') {
          const items = LOGINS[session.account_index];
          if (!items) throw new Error('Request failed with status code 403');
          return { data: switcher(items) };
        }
        if (path === '/account/account_menu') {
          let id = session.account_index === 2 ? PERSONAL : VTUBIKA;
          if (onBehalfOfUser === '113000') id = DEV;
          if (activeOverride) id = activeOverride;
          return { data: menu(...CHANNELS[id], id) };
        }
        throw new Error(`unexpected ${path}`);
      }
    }
  };
}

async function channelRun(payload, { activeOverride } = {}) {
  const calls = { upload: 0, created: [], read: 0 };
  const createClient = (_jar, options = {}) => {
    calls.created.push(options);
    return channelClient({ ...options, activeOverride: calls.created.length > 1 ? activeOverride : undefined }, calls);
  };
  const reader = async () => { calls.read += 1; return Buffer.from('video'); };
  const result = await run({ ...base, ...payload }, { createClient, readFile: reader, sleep });
  return { result, calls };
}

test('channel id: switches to the brand channel of the other login and uploads there', async () => {
  const { result, calls } = await channelRun({ channel: DEV });
  assert.equal(result.ok, true);
  assert.equal(calls.upload, 1);
  assert.deepEqual(calls.created.at(-1), { accountIndex: 2, onBehalfOfUser: '113000' });
  assert.deepEqual(result.channel, { id: DEV, name: 'Yakoo dev', handle: '@yakoodev' });
});

test('handle and exact name match too; the default channel needs no new client', async () => {
  const byHandle = await channelRun({ channel: '@YakooDev' });
  assert.equal(byHandle.result.channel.id, DEV);
  const byName = await channelRun({ channel: 'yakoo dev' });
  assert.equal(byName.result.channel.id, DEV);
  const own = await channelRun({ channel: VTUBIKA });
  assert.equal(own.calls.created.length, 1);
  assert.equal(own.result.channel.id, VTUBIKA);
});

test('unknown channel: no upload, clear error with what the login has', async () => {
  const { result, calls } = await channelRun({ channel: 'UCxxxxxxxxxxxxxxxxxxxxxx' });
  assert.equal(result.ok, false);
  assert.equal(result.channelError, true);
  assert.match(result.error, /канал UCxxxxxxxxxxxxxxxxxxxxxx не найден у этого входа/);
  assert.match(result.error, /Vtubika store/);
  assert.equal(calls.upload, 0);
  assert.equal(calls.read, 0);
  assert.ok(result.identities.length === 3 && result.identities.every((it) => !('pageId' in it)));
});

test('switch did not take: verification fails, nothing is uploaded', async () => {
  const { result, calls } = await channelRun({ channel: DEV }, { activeOverride: VTUBIKA });
  assert.equal(result.ok, false);
  assert.match(result.error, /активен другой канал: «Vtubika store» \(UCFqc0NBJUuaUuTa6hLsD53w\), а нужен «Yakoo dev»/);
  assert.equal(calls.upload, 0);
});

test('no channel set: old behaviour, the result tells where the video went', async () => {
  const { result, calls } = await channelRun({});
  assert.equal(result.ok, true);
  assert.equal(calls.created.length, 1);
  assert.equal(result.channel.id, VTUBIKA);
});

test('dry run stops before reading or uploading the file', async () => {
  const { result, calls } = await channelRun({ channel: DEV, dryRun: true, filePath: '/no/such/file' });
  assert.equal(result.ok, true);
  assert.equal(result.dryRun, true);
  assert.equal(result.channel.id, DEV);
  assert.equal(calls.read, 0);
  assert.equal(calls.upload, 0);
  assert.deepEqual(result.identities.map((it) => it.channelId), [VTUBIKA, PERSONAL, DEV]);
  assert.deepEqual(result.refreshedCookies, {});
});

test('channel parsing helpers', () => {
  assert.deepEqual(parseChannelTarget(DEV), { id: DEV, label: DEV });
  assert.equal(parseChannelTarget('@ЯковМишка-о2ь').handle, 'яковмишка-о2ь');
  assert.equal(parseChannelTarget('  '), null);
  const ids = identitiesFromAccountsList(switcher(LOGINS[2]), 5);
  assert.deepEqual(ids.map((it) => [it.channelId, it.authUser, it.pageId]), [[PERSONAL, 2, ''], [DEV, 2, '113000']]);
  assert.equal(matchIdentity(ids, parseChannelTarget('@yakoodev')).channelId, DEV);
  assert.equal(matchIdentity(ids, parseChannelTarget('@nobody')), null);
  assert.deepEqual(activeChannelFromMenu(menu('Yakoo dev', '@yakoodev', DEV)), { channelId: DEV, name: 'Yakoo dev', handle: '@yakoodev' });
  assert.equal(activeChannelFromMenu({}), null);
});
