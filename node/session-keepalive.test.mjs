// Заход браузера аккаунта без сети и без Chromium: Playwright подменён (node --test node/session-keepalive.test.mjs).
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const SCRIPT = path.join(here, 'session-keepalive.cjs');

// Поддельный playwright-chromium: пишет в журнал всё, что с ним делали; любое действие, меняющее страницу
// (click/fill/type/press/hover/tap), — в журнал как нарушение.
const FAKE = String.raw`
const fs = require("fs");
const log = [];
const save = () => fs.writeFileSync(process.env.FAKE_PW_LOG, JSON.stringify(log));
const forbidden = (name) => async () => { log.push(["FORBIDDEN", name]); save(); };
exports.chromium = {
  async launchPersistentContext(dir, options) {
    log.push(["launch", dir, { ...options, proxy: options.proxy ? { ...options.proxy, password: options.proxy.password ? "***" : "" } : null }]);
    let handler = null;
    const page = {
      async goto(url) {
        log.push(["goto", url]);
        for (const [type, url2] of [["document", url], ["image", "https://p16.tiktokcdn.com/a.jpeg"],
          ["media", "https://v16.tiktokcdn.com/v.mp4"], ["xhr", "https://rr1.googlevideo.com/videoplayback?x=1"],
          ["fetch", "https://www.tiktok.com/api/recommend/item_list/"], ["font", "https://x/f.woff2"]]) {
          await handler({ request: () => ({ resourceType: () => type, url: () => url2 }),
            abort: async () => log.push(["abort", type]), continue: async () => log.push(["continue", type]) });
        }
      },
      async waitForLoadState(state) { log.push(["wait", state]); },
      mouse: { async wheel(x, y) { log.push(["wheel", y > 0]); }, click: forbidden("mouse.click"), down: forbidden("mouse.down") },
      url: () => process.env.FAKE_PW_URL || "https://www.tiktok.com/foryou?lang=ru&token=SECRET",
      async evaluate() { return JSON.parse(process.env.FAKE_PW_SIGNALS || "{}"); },
      click: forbidden("click"), fill: forbidden("fill"), type: forbidden("type"), press: forbidden("press"),
      hover: forbidden("hover"), tap: forbidden("tap"), check: forbidden("check"),
    };
    return {
      pages: () => [page],
      async newPage() { return page; },
      async clearCookies() { log.push(["clearCookies"]); },
      async addCookies(items) { log.push(["addCookies", items.map((c) => c.name)]); },
      async route(pattern, fn) { handler = fn; log.push(["route", pattern]); },
      async cookies() {
        return [
          { name: "sessionid", value: "s1", domain: ".tiktok.com" },
          { name: "msToken", value: "fresh", domain: ".tiktok.com" },
          { name: "SID", value: "g", domain: ".google.com" },
          { name: "_ga", value: "x", domain: ".example.com" },
        ];
      },
      async close() { log.push(["close"]); save(); },
    };
  },
};
`;

function run({ config = {}, env = {} } = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ka-test-'));
  fs.mkdirSync(path.join(dir, 'pw'));
  fs.writeFileSync(path.join(dir, 'pw', 'index.js'), FAKE);
  const logFile = path.join(dir, 'log.json');
  const proc = spawnSync(process.execPath, [SCRIPT, path.join(dir, 'pw')], {
    encoding: 'utf8',
    env: {
      ...process.env,
      FAKE_PW_LOG: logFile,
      KA_COOKIES: JSON.stringify([{ name: 'sessionid', value: 's1', domain: '.tiktok.com', path: '/' }]),
      KA_PROXY: 'http://user:p%40ss@proxy.example:8080',
      KA_UA: 'Mozilla/5.0 Test',
      KA_CONFIG: JSON.stringify({
        platform: 'tiktok', url: 'https://www.tiktok.com/foryou', profileDir: path.join(dir, 'profile'),
        reseed: false, stayMs: 30, pauseMs: [1, 3], viewport: { width: 1280, height: 720 }, locale: 'ru-RU',
        timezoneId: 'Asia/Tbilisi', ...config,
      }),
      ...env,
    },
  });
  const log = fs.existsSync(logFile) ? JSON.parse(fs.readFileSync(logFile, 'utf8')) : [];
  fs.rmSync(dir, { recursive: true, force: true });
  return { proc, log, out: proc.stdout ? JSON.parse(proc.stdout.trim().split('\n').pop()) : null };
}

test('visit reads only: no clicks, media blocked, cookies of the platform only', () => {
  const { proc, log, out } = run({ env: { FAKE_PW_SIGNALS: JSON.stringify({ captcha: false, app_context: true, user: 'owl' }) } });
  assert.equal(proc.status, 0, proc.stderr);
  assert.deepEqual(log.filter((e) => e[0] === 'FORBIDDEN'), []);
  const [, , options] = log.find((e) => e[0] === 'launch');
  assert.equal(options.userAgent, 'Mozilla/5.0 Test');
  assert.deepEqual(options.viewport, { width: 1280, height: 720 });
  assert.equal(options.locale, 'ru-RU');
  assert.equal(options.timezoneId, 'Asia/Tbilisi');
  assert.equal(options.proxy.username, 'user');
  assert.equal(options.proxy.server, 'http://proxy.example:8080');
  const aborted = log.filter((e) => e[0] === 'abort').map((e) => e[1]).sort();
  assert.deepEqual(aborted, ['font', 'image', 'media', 'xhr']); // xhr — видео googlevideo
  assert.deepEqual(log.filter((e) => e[0] === 'continue').map((e) => e[1]).sort(), ['document', 'fetch']);
  assert.ok(log.some((e) => e[0] === 'wheel'));
  assert.equal(log.at(-1)[0], 'close');
  assert.ok(!log.some((e) => e[0] === 'clearCookies'));
  assert.deepEqual(out.cookies.map((c) => c.name).sort(), ['msToken', 'sessionid']);
  assert.equal(out.url, 'https://www.tiktok.com/foryou'); // без параметров
  assert.equal(out.signals.user, 'owl');
});

test('fresh cookies from the owner clear the profile cookies first', () => {
  const { proc, log } = run({ config: { reseed: true } });
  assert.equal(proc.status, 0, proc.stderr);
  const order = log.map((e) => e[0]);
  assert.ok(order.indexOf('clearCookies') < order.indexOf('addCookies'));
});

test('unknown platform fails without opening a browser', () => {
  const { proc, log } = run({ config: { platform: 'vk' } });
  assert.equal(proc.status, 1);
  assert.match(proc.stderr, /неизвестная площадка/);
  assert.deepEqual(log, []);
});

test('YouTube is never opened in the browser (youtubei.js checks it, node/youtube-session.mjs)', () => {
  const { proc, log } = run({ config: { platform: 'youtube', url: 'https://www.youtube.com/' } });
  assert.equal(proc.status, 1);
  assert.match(proc.stderr, /неизвестная площадка: youtube/);
  assert.deepEqual(log, []);
});
