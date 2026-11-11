// Проверка входа YouTube без сети: клиент youtubei.js подменён (node --test node/youtube-session.test.mjs).
import assert from 'node:assert/strict';
import test from 'node:test';

import { check, loginFromMenu, trackingFlags } from './youtube-session.mjs';

const VTUBIKA = 'UCFqc0NBJUuaUuTa6hLsD53w';
const DEV = 'UCJ0aKr7L0kTqQk2v0kTqQk2';

const menu = (name, handle, channelId) => ({
  actions: [{ openPopupAction: { popup: { multiPageMenuRenderer: { header: { activeAccountHeaderRenderer: {
    accountName: { simpleText: name }, channelHandle: { simpleText: handle },
    manageAccountTitle: {
      runs: [{ text: 'View your channel', navigationEndpoint: { browseEndpoint: { browseId: channelId } } }]
    }
  } } } } } }]
});
const tracking = (key, value) => ({
  responseContext: { serviceTrackingParams: [{ service: 'CSI', params: [{ key: 'c', value: 'WEB' }, { key, value }] }] }
});
const switcher = (items) => ({
  actions: [{ updateChannelSwitcherPageAction: { page: { channelSwitcherPageRenderer: { contents: items.map((it) => ({
    accountItem: {
      accountName: { simpleText: it.name }, channelHandle: { simpleText: it.handle }, hasChannel: true,
      serviceEndpoint: { selectActiveIdentityEndpoint: { supportedTokens: [
        { offlineCacheKeyToken: { clientCacheKey: it.channelId.slice(2) } }
      ] } }
    }
  })) } } } }]
});

// Клиент понарошку: пишет все вызовы; что-то кроме чтения меню и списка каналов — нарушение.
function fakeClient({ menuData, menuError, rotate = {} } = {}) {
  const calls = [];
  const factory = async (jar) => {
    for (const [name, value] of Object.entries(rotate)) jar.set(name, value);
    const session = { account_index: 0 };
    const forbidden = (name) => async () => { calls.push(`FORBIDDEN ${name}`); };
    return {
      session,
      studio: { upload: forbidden('upload'), updateVideoMetadata: forbidden('updateVideoMetadata') },
      actions: {
        async execute(path) {
          calls.push(path);
          if (path === '/account/account_menu') {
            if (menuError) throw menuError;
            return { data: menuData };
          }
          if (path === '/account/accounts_list') {
            if (session.account_index > 0) throw new Error('Request failed with status code 403');
            return { data: switcher([{ name: 'Vtubika store', handle: '@vtubika', channelId: VTUBIKA }]) };
          }
          calls.push(`FORBIDDEN ${path}`);
          return {};
        }
      }
    };
  };
  return { factory, calls };
}

test('logged in: the account menu header, rotated cookies come back, nothing but reads', async () => {
  const { factory, calls } = fakeClient({
    menuData: menu('Vtubika store', '@vtubika', VTUBIKA), rotate: { '__Secure-1PSIDTS': 'new' }
  });
  const result = await check({ cookie: 'SID=1' }, { createClient: factory });
  assert.equal(result.ok, true);
  assert.equal(result.loggedIn, true);
  assert.equal(result.signal, 'menu');
  assert.equal(result.channel, null);
  assert.deepEqual(result.refreshedCookies, { '__Secure-1PSIDTS': 'new' });
  assert.deepEqual(calls, ['/account/account_menu']);
});

test('logged out: no header and yt_li=0', async () => {
  const { factory } = fakeClient({ menuData: tracking('yt_li', '0') });
  const result = await check({ cookie: 'SID=1' }, { createClient: factory });
  assert.equal(result.ok, true);
  assert.equal(result.loggedIn, false);
  assert.equal(result.signal, 'yt_li');
});

test('no signals at all — unknown, not «logged out»', () => {
  assert.deepEqual(loginFromMenu({}), { loggedIn: null, signal: '' });
  assert.deepEqual(loginFromMenu(tracking('logged_in', '1')), { loggedIn: true, signal: 'logged_in' });
  assert.deepEqual(trackingFlags(tracking('yt_li', '1')), { yt_li: '1' });
});

test('channel: only listed (no switching), missing channel does not make the login dead', async () => {
  const found = fakeClient({ menuData: menu('Vtubika store', '@vtubika', VTUBIKA) });
  const ok = await check({ cookie: 'SID=1', channel: '@vtubika' }, { createClient: found.factory });
  assert.deepEqual(ok.channel, { found: true, label: '@vtubika' });
  assert.deepEqual(found.calls, ['/account/account_menu', '/account/accounts_list']);
  const missing = fakeClient({ menuData: menu('Vtubika store', '@vtubika', VTUBIKA) });
  const other = await check({ cookie: 'SID=1', channel: DEV }, { createClient: missing.factory });
  assert.equal(other.loggedIn, true);
  assert.deepEqual(other.channel, { found: false, label: DEV });
  assert.ok(missing.calls.every((path) => !path.startsWith('FORBIDDEN')));
});

test('request failure: error text and the cookies Google already rotated', async () => {
  const { factory } = fakeClient({
    menuError: new Error('Request to /account/account_menu failed with status code 401'), rotate: { SIDCC: 'x' }
  });
  const result = await check({ cookie: 'SID=1' }, { createClient: factory });
  assert.equal(result.ok, false);
  assert.match(result.error, /401/);
  assert.deepEqual(result.refreshedCookies, { SIDCC: 'x' });
});
