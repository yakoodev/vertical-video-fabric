"""Канал YouTube аккаунта (accounts.handle): настройка, передача помощнику, отказ без загрузки.

10.11 ролик для «Yakoo dev» ушёл на «Vtubika store»: у одного входа Google несколько каналов,
а помощник грузил на канал по умолчанию. Сам выбор канала — node/youtube-upload.test.mjs."""

import json
import subprocess

import pytest

from app.cookies import CookieRecord
from app.crypto import CookieCipher
from app.db import Database
from app.providers import youtube
from app.providers.base import ProviderResult
from app.providers.youtube import YouTubeProvider
from app.settings import settings
from app.store import AppStore
from app.store_accounts import youtube_channel
from app.worker import JobWorker
from test_api_pipeline import _client

YT_COOKIE = "SID=1; HSID=2; SSID=3; APISID=4; SAPISID=5"
CHANNEL = "UCk6k6kwZyYr3LMDAqyQ7pdg"


@pytest.mark.parametrize("raw, stored", [
    ("", ""),
    ("  UCk6k6kwZyYr3LMDAqyQ7pdg ", CHANNEL),
    ("@yakoodev", "@yakoodev"),
    ("https://www.youtube.com/channel/UCk6k6kwZyYr3LMDAqyQ7pdg/videos", CHANNEL),
    ("youtube.com/@yakoodev/shorts", "@yakoodev"),
    ("https://www.youtube.com/@%D0%AF%D0%BA%D0%BE%D0%B2", "@Яков"),
    ("Yakoo dev", "Yakoo dev"),
])
def test_channel_forms_are_normalized(raw, stored):
    assert youtube_channel(raw) == stored


@pytest.mark.parametrize("bad", ["@", "https://www.youtube.com/watch?v=abc", "a\nb", "x" * 101])
def test_bad_channel_is_refused(bad):
    with pytest.raises(ValueError, match="channel"):
        youtube_channel(bad)


@pytest.fixture
def api(tmp_path, monkeypatch):
    return _client(tmp_path, monkeypatch)


def test_channel_is_set_via_api_and_kept_on_fresh_cookies(api):
    client, store = api
    acc = client.post("/api/accounts", json={"platform": "youtube", "label": "yt", "cookie": YT_COOKIE}).json()
    assert acc["handle"] == ""
    response = client.put(f"/api/accounts/{acc['id']}/channel",
                          json={"channel": "https://www.youtube.com/channel/UCk6k6kwZyYr3LMDAqyQ7pdg"})
    assert response.status_code == 200, response.text
    assert response.json()["handle"] == CHANNEL
    assert client.get("/api/accounts").json()[0]["handle"] == CHANNEL
    # Свежие cookie того же аккаунта канал не стирают.
    client.post("/api/accounts", json={"platform": "youtube", "label": "yt", "cookie": YT_COOKIE})
    assert store.get_account(acc["id"])["handle"] == CHANNEL
    assert client.put(f"/api/accounts/{acc['id']}/channel", json={"channel": ""}).json()["handle"] == ""


def test_channel_is_youtube_only_and_unknown_account_is_404(api):
    client, _store = api
    tt = client.post("/api/accounts", json={"platform": "tiktok", "label": "tt", "cookie": "sessionid=s"}).json()
    response = client.put(f"/api/accounts/{tt['id']}/channel", json={"channel": "@x"})
    assert response.status_code == 400 and "YouTube" in response.json()["detail"]
    assert client.put("/api/accounts/9999/channel", json={"channel": "@x"}).status_code == 404


def _worker_store(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")
    monkeypatch.setattr(settings, "runtime_dir", tmp_path / "runtime")
    settings.ensure_dirs()
    db = Database(tmp_path / "app.sqlite")
    db.init()
    return AppStore(db, CookieCipher(tmp_path / "secret.key"))


def _run_job(store, tmp_path, account_ids):
    source = tmp_path / "video.mp4"
    source.write_bytes(b"fake-video")
    with source.open("rb") as fileobj:
        job = store.create_job(fileobj, "video.mp4", "T", "", account_ids, "public", True)
    assert JobWorker(store).process_once()
    return store.get_job(job["id"])


def test_worker_hands_the_channel_to_youtube_only(tmp_path, monkeypatch):
    store = _worker_store(tmp_path, monkeypatch)
    with_channel = store.upsert_account("youtube", "yt-dev", YT_COOKIE)
    store.set_account_channel(with_channel["id"], CHANNEL)
    plain = store.upsert_account("youtube", "yt-plain", YT_COOKIE)
    tt = store.upsert_account("tiktok", "tt", "sessionid=a")
    store.db.execute("UPDATE accounts SET handle = 'tiktok_nick' WHERE id = ?", (tt["id"],))
    calls = []

    class Recorder:
        def __init__(self, platform):
            self.platform = platform

        def upload(self, **kwargs):
            calls.append((self.platform, kwargs["account_label"], kwargs.get("channel", "<нет>")))
            return ProviderResult(status="succeeded", remote_id="1")

    monkeypatch.setattr("app.worker.get_provider", Recorder)
    _run_job(store, tmp_path, [with_channel["id"], plain["id"], tt["id"]])
    assert sorted(calls) == [("tiktok", "tt", "<нет>"), ("youtube", "yt-dev", CHANNEL), ("youtube", "yt-plain", "<нет>")]


def test_mock_mode_accepts_the_channel(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "provider_mode", "mock")
    store = _worker_store(tmp_path, monkeypatch)
    acc = store.upsert_account("youtube", "yt", YT_COOKIE)
    store.set_account_channel(acc["id"], "@yakoodev")
    assert _run_job(store, tmp_path, [acc["id"]])["targets"][0]["status"] == "succeeded"


def test_worker_keeps_rotated_cookies_even_when_the_upload_is_refused(tmp_path, monkeypatch):
    store = _worker_store(tmp_path, monkeypatch)
    acc = store.upsert_account("youtube", "yt", YT_COOKIE)

    class Refuses:
        def upload(self, **kwargs):
            return ProviderResult(status="failed", error="активен другой канал", refreshed_cookies={"SID": "fresh"})

    monkeypatch.setattr("app.worker.get_provider", lambda platform: Refuses())
    done = _run_job(store, tmp_path, [acc["id"]])
    assert done["targets"][0]["status"] == "failed"
    assert {c.name: c.value for c in store.get_account_cookies(acc["id"])}["SID"] == "fresh"


COOKIES = [CookieRecord(name, "v", ".youtube.com") for name in ("SID", "HSID", "SSID", "APISID", "SAPISID")]


def _upload(monkeypatch, tmp_path, stdout, **kwargs):
    seen = {}

    def fake_run(args, input, **_kw):
        seen["payload"] = json.loads(input)
        return subprocess.CompletedProcess(args, 0, stdout, "")

    monkeypatch.setattr(youtube.subprocess, "run", fake_run)
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    result = YouTubeProvider().upload(cookies=COOKIES, file_path=clip, title="T", description="D", privacy="public",
                                      allow_comments=True, account_label="yt", proxy_url="", **kwargs)
    return result, seen["payload"]


def test_provider_sends_the_channel_and_keeps_where_the_video_went(monkeypatch, tmp_path):
    stdout = json.dumps({"ok": True, "videoId": "v1", "channel": {"id": CHANNEL, "name": "Yakoo dev"},
                         "refreshedCookies": {"SID": "n"}})
    result, payload = _upload(monkeypatch, tmp_path, stdout, channel=f" {CHANNEL} ")
    assert payload["channel"] == CHANNEL
    assert result.status == "succeeded" and result.response["channel"]["id"] == CHANNEL
    assert result.refreshed_cookies == {"SID": "n"} and "refreshedCookies" not in result.response


def test_provider_without_channel_keeps_the_old_payload(monkeypatch, tmp_path):
    _, payload = _upload(monkeypatch, tmp_path, json.dumps({"ok": True, "videoId": "v"}))
    assert "channel" not in payload


def test_channel_refusal_is_a_plain_failure_not_reauth(monkeypatch, tmp_path):
    stdout = json.dumps({"ok": False, "channelError": True, "error": f"канал {CHANNEL} не найден у этого входа",
                         "identities": [{"name": "Vtubika store", "channelId": "UCFqc0NBJUuaUuTa6hLsD53w"}],
                         "refreshedCookies": {"SID": "n"}})
    result, _ = _upload(monkeypatch, tmp_path, stdout, channel=CHANNEL)
    assert result.status == "failed" and "не найден" in result.error
    assert result.remote_id == "" and result.refreshed_cookies == {"SID": "n"}
    assert result.response["identities"][0]["name"] == "Vtubika store"
