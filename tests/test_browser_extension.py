"""Своё расширение браузера (browser-extension/): CORS только ему и только на /api/accounts, UA браузера при аккаунте,
TikTok грузит с UA аккаунта, а без него — как раньше."""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from app.cookies import CookieRecord
from app.crypto import CookieCipher
from app.db import Database
from app.providers import tiktok
from app.settings import BROWSER_EXTENSION_ORIGIN, settings
from app.store import AppStore
from app.worker import JobWorker
from test_api_pipeline import _client

ROOT = Path(__file__).resolve().parents[1]
EXT = BROWSER_EXTENSION_ORIGIN
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
COOKIE_EDITOR = json.dumps([
    {"domain": ".tiktok.com", "expirationDate": 1893456000.5, "hostOnly": False, "httpOnly": True, "name": "sessionid",
     "path": "/", "sameSite": "no_restriction", "secure": True, "session": False, "storeId": "0", "value": "S1"},
    {"domain": ".tiktok.com", "hostOnly": False, "httpOnly": False, "name": "msToken", "path": "/", "secure": True,
     "session": True, "value": "m"},
])


@pytest.fixture
def api(tmp_path, monkeypatch):
    return _client(tmp_path, monkeypatch)


def _post(client, **body):
    return client.post("/api/accounts", json={"platform": "tiktok", "label": "tt-main", "cookie": COOKIE_EDITOR, **body},
                       headers={"Origin": EXT})


# --- CORS ------------------------------------------------------------------------------------------------------------ #

def _preflight(client, path="/api/accounts", origin=EXT, method="POST"):
    return client.options(path, headers={"Origin": origin, "Access-Control-Request-Method": method,
                                         "Access-Control-Request-Headers": "authorization,content-type"})


def test_extension_id_comes_from_the_manifest_key():
    manifest = json.loads((ROOT / "browser-extension" / "manifest.json").read_text(encoding="utf-8"))
    digest = hashlib.sha256(base64.b64decode(manifest["key"])).hexdigest()[:32]
    assert EXT == "chrome-extension://" + "".join(chr(ord("a") + int(ch, 16)) for ch in digest)
    assert settings.browser_extension_origins == (EXT,)


def test_preflight_from_the_extension_is_allowed(api):
    client, _ = api
    response = _preflight(client)
    assert response.status_code == 204
    assert response.headers["access-control-allow-origin"] == EXT
    assert set(response.headers["access-control-allow-methods"].replace(" ", "").split(",")) == {"GET", "POST"}
    assert "authorization" in response.headers["access-control-allow-headers"]
    assert "access-control-allow-credentials" not in response.headers  # только токен, без куки входа


@pytest.mark.parametrize("origin", ["chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "https://evil.example",
                                    "null", f"{EXT}.evil.example"])
def test_other_origins_get_no_cors(api, origin):
    client, _ = api
    assert "access-control-allow-origin" not in _preflight(client, origin=origin).headers
    assert "access-control-allow-origin" not in client.get("/api/accounts", headers={"Origin": origin}).headers


@pytest.mark.parametrize("path", ["/api/accounts/1", "/api/jobs", "/api/settings/export", "/api/accounts/"])
def test_other_paths_get_no_cors_even_for_the_extension(api, path):
    client, _ = api
    assert "access-control-allow-origin" not in _preflight(client, path=path, method="DELETE").headers
    assert "access-control-allow-origin" not in client.get(path, headers={"Origin": EXT}).headers


def test_list_and_save_answer_the_extension(api):
    client, _ = api
    saved = _post(client, user_agent=UA)
    assert saved.status_code == 200 and saved.headers["access-control-allow-origin"] == EXT
    listed = client.get("/api/accounts", headers={"Origin": EXT})
    assert listed.headers["access-control-allow-origin"] == EXT and "origin" in listed.headers["vary"].lower()
    assert [a["label"] for a in listed.json()] == ["tt-main"]


def test_wrong_token_is_readable_by_the_extension(api, monkeypatch):
    client, _ = api
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "api_token", "right-token")
    denied = client.get("/api/accounts", headers={"Origin": EXT, "Authorization": "Bearer wrong"})
    assert denied.status_code == 401 and denied.headers["access-control-allow-origin"] == EXT
    ok = client.get("/api/accounts", headers={"Origin": EXT, "Authorization": "Bearer right-token"})
    assert ok.status_code == 200
    assert _preflight(client).status_code == 204  # preflight без токена: браузер его не шлёт


def test_extra_origins_from_env(api, monkeypatch):
    client, _ = api
    firefox = "moz-extension://0d6c1d39-5f0a-4c3b-9d1e-1f2a3b4c5d6e"
    monkeypatch.setattr(settings, "browser_extension_origins", (EXT, firefox))
    assert _preflight(client, origin=firefox).headers["access-control-allow-origin"] == firefox


# --- UA аккаунта ----------------------------------------------------------------------------------------------------- #

def test_user_agent_is_stored_with_the_cookies(api):
    client, store = api
    account = _post(client, user_agent=UA, proxy_url="http://u:p@1.2.3.4:8080").json()
    assert account["user_agent"] == UA and account["has_required_cookies"] and account["cookie_count"] == 2
    assert store.get_account(account["id"])["user_agent"] == UA
    assert [c.name for c in store.get_account_cookies(account["id"])] == ["sessionid", "msToken"]

    # Свежие cookie из другого браузера — новый UA, прокси прежний.
    again = _post(client, user_agent="Mozilla/5.0 Firefox/150.0").json()
    assert again["id"] == account["id"] and again["user_agent"] == "Mozilla/5.0 Firefox/150.0"
    assert store.get_account_proxy_url(account["id"]) == "http://u:p@1.2.3.4:8080"

    # Cookie руками без UA: прежний UA к ним не относится — стирается, TikTok возьмёт TIKTOK_USER_AGENT.
    manual = client.post("/api/accounts", json={"platform": "tiktok", "label": "tt-main", "cookie": "sessionid=S2"}).json()
    assert manual["user_agent"] == ""


@pytest.mark.parametrize("bad", ["UA\r\nX-Evil: 1", "UA\x00", "x" * 513])
def test_broken_user_agent_is_refused(api, bad):
    client, store = api
    response = _post(client, user_agent=bad)
    assert response.status_code == 400 and "user_agent" in response.json()["detail"]
    assert store.list_accounts() == []


def test_old_database_gets_the_column(tmp_path):
    path = tmp_path / "old.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute(
            """CREATE TABLE accounts (id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT NOT NULL, label TEXT NOT NULL,
               encrypted_cookies TEXT NOT NULL, cookie_count INTEGER NOT NULL DEFAULT 0,
               has_required_cookies INTEGER NOT NULL DEFAULT 0, missing_cookies TEXT NOT NULL DEFAULT '',
               created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
               UNIQUE(platform, label))""")
        cipher = CookieCipher(tmp_path / "secret.key")
        conn.execute("INSERT INTO accounts (platform, label, encrypted_cookies, cookie_count, has_required_cookies) "
                     "VALUES ('tiktok', 'old', ?, 1, 1)",
                     (cipher.encrypt_json([{"name": "sessionid", "value": "s", "domain": ".tiktok.com"}]),))
    db = Database(path)
    db.init()
    store = AppStore(db, cipher)
    [old] = store.list_accounts()
    assert old["label"] == "old" and old["user_agent"] == ""


def test_bundle_keeps_the_user_agent(tmp_path):
    db = Database(tmp_path / "a.sqlite")
    db.init()
    store = AppStore(db, CookieCipher(tmp_path / "a.key"))
    store.upsert_account("tiktok", "tt", "sessionid=s", user_agent=UA)
    bundle = store.export_bundle(include_accounts=True)
    assert bundle["accounts"][0]["user_agent"] == UA
    db2 = Database(tmp_path / "b.sqlite")
    db2.init()
    other = AppStore(db2, CookieCipher(tmp_path / "b.key"))
    other.import_bundle(bundle)
    assert other.list_accounts()[0]["user_agent"] == UA


# --- TikTok грузит с UA аккаунта ------------------------------------------------------------------------------------- #

def test_account_user_agent_beats_the_service_setting(monkeypatch):
    monkeypatch.setattr(settings, "tiktok_user_agent", "Service/1")
    assert tiktok._user_agent(UA) == UA
    assert tiktok._user_agent("") == "Service/1"
    monkeypatch.setattr(settings, "tiktok_user_agent", "")
    assert tiktok._user_agent("  ") == tiktok._UA


def test_provider_uses_the_account_user_agent(monkeypatch, tmp_path):
    seen = {}

    def stop_after_ua(self, **kwargs):
        seen.update(kwargs)
        return tiktok.ProviderResult(status="failed", error="stop")

    monkeypatch.setattr(tiktok.TikTokProvider, "_ensure_signature_helper", lambda self: None)
    monkeypatch.setattr(tiktok.TikTokProvider, "_upload_and_post", stop_after_ua)
    tiktok.TikTokProvider().upload(
        cookies=[CookieRecord("sessionid", "s", ".tiktok.com")], file_path=tmp_path / "v.mp4", title="t",
        description="", privacy="public", allow_comments=True, account_label="a", proxy_url="", user_agent=UA)
    assert seen["user_agent"] == UA


def _worker_store(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")
    monkeypatch.setattr(settings, "runtime_dir", tmp_path / "runtime")
    settings.ensure_dirs()
    db = Database(tmp_path / "app.sqlite")
    db.init()
    return AppStore(db, CookieCipher(tmp_path / "secret.key"))


def test_worker_hands_the_account_user_agent_to_tiktok_only(tmp_path, monkeypatch):
    store = _worker_store(tmp_path, monkeypatch)
    with_ua = store.upsert_account("tiktok", "tt-ua", "sessionid=a", user_agent=UA)
    without = store.upsert_account("tiktok", "tt-plain", "sessionid=b")
    youtube = store.upsert_account("youtube", "yt", "SID=1; HSID=2; SSID=3; APISID=4; SAPISID=5", user_agent=UA)
    calls = []

    class Recorder:
        def __init__(self, platform):
            self.platform = platform

        def upload(self, **kwargs):
            calls.append((self.platform, kwargs.get("account_label"), kwargs.get("user_agent", "<нет>")))
            return tiktok.ProviderResult(status="succeeded", remote_id="1")

    monkeypatch.setattr("app.worker.get_provider", Recorder)
    source = tmp_path / "video.mp4"
    source.write_bytes(b"fake-video")
    with source.open("rb") as fileobj:
        store.create_job(fileobj, "video.mp4", "T", "", [with_ua["id"], without["id"], youtube["id"]], "public", True)
    assert JobWorker(store).process_once()
    assert sorted(calls) == [("tiktok", "tt-plain", "<нет>"), ("tiktok", "tt-ua", UA), ("youtube", "yt", "<нет>")]
