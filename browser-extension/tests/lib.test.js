// node --test browser-extension/tests/ — чистые функции расширения, без браузера.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  PLATFORMS, accountsForPlatform, buildAccountPayload, detectPlatform, filterCookies, normalizeServiceUrl,
  requiredStatus, servicePermissionPattern, serviceErrorText, toCookieEditorJson, verdictText,
} from "../lib.js";

const cookie = (name, domain, extra = {}) => ({
  name, value: `${name}-v`, domain, path: "/", secure: true, httpOnly: false, hostOnly: false, session: false,
  expirationDate: 1893456000.5, sameSite: "no_restriction", storeId: "0", ...extra,
});

test("площадка по адресу вкладки", () => {
  assert.equal(detectPlatform("https://www.tiktok.com/upload"), "tiktok");
  assert.equal(detectPlatform("https://tiktok.com/"), "tiktok");
  assert.equal(detectPlatform("https://studio.youtube.com/channel/x"), "youtube");
  assert.equal(detectPlatform("https://www.youtube.com/"), "youtube");
  assert.equal(detectPlatform("https://nottiktok.com/"), null);
  assert.equal(detectPlatform("https://tiktok.com.evil.example/"), null);
  assert.equal(detectPlatform("chrome://extensions"), null);
  assert.equal(detectPlatform(""), null);
});

test("cookie площадки: чужие домены, пустые, partitioned и повторы — мимо", () => {
  const input = [
    cookie("sessionid", ".tiktok.com"),
    cookie("sessionid", ".tiktok.com"), // повтор
    cookie("msToken", "www.tiktok.com", { hostOnly: true }),
    cookie("tt_chain", ".tiktok.com", { partitionKey: { topLevelSite: "https://example.com" } }),
    cookie("x", ".nottiktok.com"),
    cookie("", ".tiktok.com"),
    { name: "noval", domain: ".tiktok.com" },
  ];
  const out = filterCookies("tiktok", input);
  assert.deepEqual(out.map((c) => `${c.name}@${c.domain}`), ["sessionid@.tiktok.com", "msToken@www.tiktok.com"]);
  assert.deepEqual(filterCookies("instagram", input), []);
  // Пустой partitionKey (Firefox отдаёт {} у обычных cookie) — обычная cookie.
  assert.equal(filterCookies("tiktok", [cookie("a", ".tiktok.com", { partitionKey: {} })]).length, 1);
});

test("обязательные cookie — те же, что у сервиса (app/cookies.py)", () => {
  const yt = PLATFORMS.youtube.required.map((n) => cookie(n, ".youtube.com"));
  assert.deepEqual(requiredStatus("youtube", yt), { ok: true, missing: [], present: PLATFORMS.youtube.required });
  const partial = requiredStatus("youtube", yt.slice(1));
  assert.equal(partial.ok, false);
  assert.deepEqual(partial.missing, ["SID"]);
  assert.equal(requiredStatus("tiktok", []).ok, false);
  assert.equal(requiredStatus("tiktok", [cookie("sessionid", ".tiktok.com", { value: "" })]).ok, false);

  const py = readFileSync(new URL("../../app/cookies.py", import.meta.url), "utf8");
  assert.match(py, /platform == "tiktok":\s*required = \{"sessionid"\}/);
  assert.match(py, /platform == "youtube":\s*required = \{"SID", "HSID", "SSID", "APISID", "SAPISID"\}/);
});

test("JSON в формате Cookie-Editor", () => {
  const json = toCookieEditorJson([
    cookie("sessionid", ".tiktok.com", { httpOnly: true }),
    cookie("tmp", ".tiktok.com", { session: true, sameSite: "unspecified", storeId: undefined }),
  ]);
  const items = JSON.parse(json);
  assert.equal(items.length, 2);
  assert.deepEqual(items[0], {
    domain: ".tiktok.com", hostOnly: false, httpOnly: true, name: "sessionid", path: "/", sameSite: "no_restriction",
    secure: true, session: false, storeId: "0", value: "sessionid-v", expirationDate: 1893456000.5,
  });
  assert.equal(items[1].expirationDate, undefined);
  assert.equal(items[1].sameSite, null);
  assert.equal(items[1].storeId, null);
  assert.equal(toCookieEditorJson([]), "[]");
});

test("тело запроса в сервис", () => {
  const payload = buildAccountPayload("tiktok", "  tt-main ", [cookie("sessionid", ".tiktok.com")], " UA/1 ");
  assert.deepEqual(Object.keys(payload).sort(), ["cookie", "label", "platform", "user_agent"]);
  assert.equal(payload.label, "tt-main");
  assert.equal(payload.user_agent, "UA/1");
  assert.equal(JSON.parse(payload.cookie)[0].name, "sessionid");
  assert.throws(() => buildAccountPayload("tiktok", " ", [], "UA"), /название/);
  assert.throws(() => buildAccountPayload("myspace", "x", [], "UA"), /площадка/);
});

test("аккаунты площадки по алфавиту", () => {
  const list = [
    { id: 3, platform: "tiktok", label: "b" }, { id: 1, platform: "youtube", label: "a" },
    { id: 2, platform: "tiktok", label: "a" },
  ];
  assert.deepEqual(accountsForPlatform(list, "tiktok").map((a) => a.id), [2, 3]);
  assert.deepEqual(accountsForPlatform(null, "tiktok"), []);
});

test("адрес сервиса и разрешение на него", () => {
  assert.equal(normalizeServiceUrl(""), "https://video.d.yakoo.dev");
  assert.equal(normalizeServiceUrl("video.d.yakoo.dev/projects"), "https://video.d.yakoo.dev");
  assert.equal(normalizeServiceUrl("http://localhost:8088/"), "http://localhost:8088");
  assert.equal(normalizeServiceUrl("http://127.0.0.1:8088"), "http://127.0.0.1:8088");
  assert.throws(() => normalizeServiceUrl("http://video.d.yakoo.dev"), /https/);
  assert.throws(() => normalizeServiceUrl("ftp://x"), /https/);
  assert.equal(servicePermissionPattern("http://localhost:8088"), "http://localhost/*");
  assert.equal(servicePermissionPattern("https://video.d.yakoo.dev"), "https://video.d.yakoo.dev/*");
});

test("ответ сервиса — понятной строкой", () => {
  assert.match(verdictText({ label: "tt", has_required_cookies: true, cookie_count: 12 }), /вход есть.*12/);
  assert.match(verdictText({ label: "tt", has_required_cookies: false, missing_cookies: "sessionid" }), /нет sessionid/);
  assert.match(serviceErrorText(401, null), /токен/);
  assert.match(serviceErrorText(400, { detail: "label is required" }), /label is required/);
  assert.match(serviceErrorText(422, { detail: [{ msg: "bad" }] }), /bad/);
  assert.match(serviceErrorText(500, null), /500/);
});

test("манифест: только нужные разрешения, без фона и чужого кода", () => {
  const manifest = JSON.parse(readFileSync(new URL("../manifest.json", import.meta.url), "utf8"));
  assert.equal(manifest.manifest_version, 3);
  assert.deepEqual(manifest.permissions, ["cookies", "storage"]);
  assert.deepEqual(manifest.host_permissions, ["*://*.tiktok.com/*", "*://*.youtube.com/*"]);
  assert.equal(manifest.background, undefined);
  assert.equal(manifest.content_scripts, undefined);
  assert.equal(manifest.browser_specific_settings.gecko.id, "vvf-cookies@yakoo.local");
  for (const p of Object.values(PLATFORMS)) {
    for (const origin of p.origins) assert.ok(manifest.host_permissions.includes(origin), origin);
  }
});
