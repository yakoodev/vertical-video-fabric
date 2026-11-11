"""Живой вход аккаунтов: свежие cookie обратно в хранилище (загрузка TikTok, TikTok Studio, браузер аккаунта),
заходы браузера по одному и перед загрузкой, состояние входа и одно сообщение в Telegram на случай.
Без сети и без Chromium: сессия requests, node и Telegram подменены."""

from __future__ import annotations

import json
import random
import subprocess
from datetime import datetime, timedelta

import pytest
import requests

from app import session_cookies as SC
from app import session_health as H
from app import session_keepalive as K
from app import tiktok_studio_collect as TS
from app.browser_lock import BROWSER_LOCK
from app.cookies import CookieRecord
from app.crypto import CookieCipher
from app.db import Database
from app.providers import tiktok
from app.providers.base import ProviderResult
from app.settings import settings
from app.store import AppStore
from app.worker import JobWorker

PROXY = "http://user:s3cret@proxy.example:8080"
TT_COOKIES = "sessionid=S1; msToken=OLD; ttwid=T1"
YT_COOKIES = "SID=1; HSID=2; SSID=3; APISID=4; SAPISID=5; __Secure-1PSIDTS=old"
NOW = datetime(2026, 11, 11, 12, 0, 0)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")
    monkeypatch.setattr(settings, "runtime_dir", tmp_path / "runtime")
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    settings.ensure_dirs()
    db = Database(tmp_path / "app.sqlite")
    db.init()
    monkeypatch.setattr(K, "now", lambda: NOW)
    return AppStore(db, CookieCipher(tmp_path / "secret.key"))


def _values(store, account_id) -> dict:
    return {c.name: c.value for c in store.get_account_cookies(account_id)}


def _row(store, account_id) -> dict:
    return dict(store.db.query_one("SELECT * FROM accounts WHERE id = ?", (account_id,)))


# --------------------------------------------------------------------------- #
# Фильтр свежих cookie
# --------------------------------------------------------------------------- #


def test_rotated_values_keep_only_platform_domains_known_names_and_real_changes():
    stored = [CookieRecord("sessionid", "S1", ".tiktok.com"), CookieRecord("msToken", "OLD", ".tiktok.com"),
              CookieRecord("custom_kept", "a", ".tiktok.com")]
    fresh = [
        {"name": "sessionid", "value": "S1", "domain": ".tiktok.com"},  # не сменилась
        {"name": "msToken", "value": "SUB", "domain": "www.tiktok.com"},  # поддомен уступает общему
        {"name": "msToken", "value": "NEW", "domain": ".tiktok.com"},
        {"name": "ttwid", "value": "T2", "domain": ".tiktok.com"},  # известное имя, у аккаунта не было
        {"name": "custom_kept", "value": "b", "domain": ".tiktok.com"},  # уже лежит у аккаунта
        {"name": "_ga", "value": "ad", "domain": ".tiktok.com"},  # неизвестное имя
        {"name": "sid_tt", "value": "", "domain": ".tiktok.com"},  # пустое — выход, не ротация
        {"name": "sid_guard", "value": "deleted", "domain": ".tiktok.com"},
        {"name": "odin_tt", "value": "x", "domain": ".tiktok.com.evil.example"},
        {"name": "ttwid", "value": "GOOGLE", "domain": ".google.com"},
    ]
    assert SC.rotated_values("tiktok", stored, fresh) == {"msToken": "NEW", "ttwid": "T2", "custom_kept": "b"}


def test_youtube_never_takes_google_com_cookies():
    stored = [CookieRecord("SID", "1", ".youtube.com"), CookieRecord("__Secure-1PSIDTS", "old", ".youtube.com")]
    fresh = [{"name": "SID", "value": "google-sid", "domain": ".google.com"},
             {"name": "__Secure-1PSIDTS", "value": "new", "domain": ".youtube.com"}]
    assert SC.rotated_values("youtube", stored, fresh) == {"__Secure-1PSIDTS": "new"}


def test_save_rotated_logs_names_never_values(store, caplog):
    acc = store.upsert_account("tiktok", "tt", TT_COOKIES)
    caplog.set_level("INFO")
    names = SC.save_rotated(store, acc["id"], "tiktok", store.get_account_cookies(acc["id"]),
                            [{"name": "msToken", "value": "SECRET-NEW", "domain": ".tiktok.com"}], source="test")
    assert names == ["msToken"] and _values(store, acc["id"])["msToken"] == "SECRET-NEW"
    assert "msToken" in caplog.text and "SECRET-NEW" not in caplog.text


# --------------------------------------------------------------------------- #
# Загрузка TikTok: банка сессии после шагов — и при отказе
# --------------------------------------------------------------------------- #


class _Resp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


class _RotatingSession:
    """Отвечает по куску URL и, как TikTok, меняет cookie ответами (Set-Cookie)."""

    def __init__(self, script, rotate):
        self.script, self.rotate = script, rotate
        self.headers, self.proxies, self.verify = {}, {}, True
        self.cookies = requests.cookies.RequestsCookieJar()

    def _answer(self, url):
        for key, answer in self.script.items():
            if key in url:
                for name, value, domain in self.rotate.pop(key, []):
                    self.cookies.set(name, value, domain=domain, path="/")
                if isinstance(answer, Exception):
                    raise answer
                return answer
        raise AssertionError(f"unexpected {url}")

    def get(self, url, **kwargs):
        return self._answer(url)

    def post(self, url, **kwargs):
        return self._answer(url)

    def head(self, url, **kwargs):
        return _Resp()


def _tt_script(**overrides):
    script = {
        "project/create": _Resp(body={"project": {"project_id": "p1"}}),
        "video/upload/auth": _Resp(body={"video_token_v5": {"access_key_id": "k", "secret_acess_key": "s",
                                                             "session_token": "t"}}),
        "ApplyUploadInner": _Resp(body={"Result": {"InnerUploadAddress": {"UploadNodes": [{
            "Vid": "v", "StoreInfos": [{"StoreUri": "store/uri", "Auth": "a"}], "UploadHost": "up.example",
            "SessionKey": "sk"}]}}}),
        "phase=transfer": _Resp(), "phase=finish": _Resp(), "CommitUploadInner": _Resp(),
        "web/project/post/v1": _Resp(body={"status_code": 0, "single_post_resp_list": [{"item_id": "7431234567890"}]}),
    }
    script.update(overrides)
    return script


def _tt_upload(monkeypatch, tmp_path, script, rotate):
    monkeypatch.setattr(settings, "publish_upload_retry_seconds", 0)
    monkeypatch.setattr(settings, "publish_upload_retries", 0)
    monkeypatch.setattr(tiktok.TikTokProvider, "_ensure_signature_helper", lambda self: None)
    monkeypatch.setattr(tiktok, "_run_signature_helper",
                        lambda *a, **k: json.dumps({"data": {"x-bogus": "xb", "signature": "sig"}}))
    session = _RotatingSession(script, rotate)
    monkeypatch.setattr(tiktok.requests, "Session", lambda: session)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"0123456789")
    cookies = [CookieRecord("sessionid", "S1", ".tiktok.com"), CookieRecord("msToken", "OLD", ".tiktok.com")]
    return tiktok.TikTokProvider().upload(cookies=cookies, file_path=video, title="t", description="",
                                          privacy="public", allow_comments=True, account_label="a", proxy_url="")


def test_tiktok_upload_returns_rotated_cookies_on_success(monkeypatch, tmp_path):
    result = _tt_upload(monkeypatch, tmp_path, _tt_script(), {
        "project/create": [("msToken", "NEW1", ".tiktok.com"), ("_ga", "ad", ".tiktok.com")],
        "web/project/post/v1": [("msToken", "NEW2", ".tiktok.com"), ("ttwid", "TW", ".tiktok.com")],
    })
    assert result.status == "succeeded", result.error
    assert result.refreshed_cookies == {"msToken": "NEW2", "ttwid": "TW"}


def test_tiktok_upload_returns_rotated_cookies_when_a_step_fails(monkeypatch, tmp_path):
    script = _tt_script(**{"ApplyUploadInner": _Resp(500, {"error": "busy"})})
    result = _tt_upload(monkeypatch, tmp_path, script, {"video/upload/auth": [("msToken", "NEW", ".tiktok.com")]})
    assert result.status == "failed"
    assert result.refreshed_cookies == {"msToken": "NEW"}


def test_tiktok_upload_network_failure_still_returns_rotated(monkeypatch, tmp_path):
    script = _tt_script(**{"ApplyUploadInner": requests.ConnectionError("reset")})
    result = _tt_upload(monkeypatch, tmp_path, script, {"project/create": [("msToken", "NEW", ".tiktok.com")]})
    assert result.status == "failed" and result.refreshed_cookies == {"msToken": "NEW"}


def test_tiktok_login_refusal_writes_nothing_back(monkeypatch, tmp_path):
    script = _tt_script(**{"project/create": _Resp(body={"status_code": 8, "status_msg": "Please login first"})})
    result = _tt_upload(monkeypatch, tmp_path, script, {"project/create": [("msToken", "GUEST", ".tiktok.com")]})
    assert result.status == "needs_reauth" and result.refreshed_cookies == {}


def _job(store, tmp_path, account_ids, content: bytes = b"fake-video"):
    source = tmp_path / f"video-{len(content)}.mp4"
    source.write_bytes(content)
    with source.open("rb") as fileobj:
        store.create_job(fileobj, "video.mp4", "T", "", account_ids, "public", True)


def test_worker_saves_rotated_cookies_even_on_failure_and_marks_reauth(store, tmp_path, monkeypatch):
    ok = store.upsert_account("tiktok", "tt-ok", TT_COOKIES)
    dead = store.upsert_account("tiktok", "tt-dead", TT_COOKIES)
    answers = {ok["id"]: ProviderResult(status="failed", error="busy", refreshed_cookies={"msToken": "NEW"}),
               dead["id"]: ProviderResult(status="needs_reauth", error="Please login first")}
    labels = {"tt-ok": ok["id"], "tt-dead": dead["id"]}

    class Provider:
        def __init__(self, platform):
            pass

        def upload(self, **kwargs):
            return answers[labels[kwargs["account_label"]]]

    sent = []
    monkeypatch.setattr("app.worker.get_provider", Provider)
    monkeypatch.setattr(H, "_send_telegram", lambda store_, text: sent.append(text) or True)
    _job(store, tmp_path, [ok["id"], dead["id"]])
    assert JobWorker(store).process_once()
    assert _values(store, ok["id"])["msToken"] == "NEW"
    assert _values(store, dead["id"])["msToken"] == "OLD"
    assert _row(store, dead["id"])["session_state"] == "needs_reauth"
    assert sent == ["Аккаунт «tt-dead» вылетел — обнови куки расширением"]


# --------------------------------------------------------------------------- #
# TikTok Studio: cookie контекста обратно
# --------------------------------------------------------------------------- #


def _studio_runner(answer: dict, code: int = 0):
    def run(args, **kwargs):
        run.env = kwargs["env"]
        run.locked = BROWSER_LOCK.locked()
        return subprocess.CompletedProcess(args, code, json.dumps(answer) + "\n", "Error: boom" if code else "")
    return run


def test_studio_session_writes_back_rotated_cookies_and_drops_them_from_the_answer(store):
    acc = store.upsert_account("tiktok", "tt", TT_COOKIES, proxy_url=PROXY)
    runner = _studio_runner({"status": "ok", "handle": "owl", "login_required": False, "videos": {},
                             "cookies": [{"name": "msToken", "value": "NEW", "domain": ".tiktok.com"},
                                         {"name": "_ttp", "value": "ad", "domain": ".tiktok.com"}]})
    data = TS.run_session(store, acc["id"], [], runner=runner)
    assert "cookies" not in data and runner.locked  # один Chromium на сервис
    assert _values(store, acc["id"]) == {"sessionid": "S1", "msToken": "NEW", "ttwid": "T1"}


def test_studio_failure_mid_run_still_saves_cookies(store):
    acc = store.upsert_account("tiktok", "tt", TT_COOKIES)
    runner = _studio_runner({"status": "error", "videos": {}, "login_required": False,
                             "cookies": [{"name": "msToken", "value": "NEW", "domain": ".tiktok.com"}]}, code=1)
    with pytest.raises(RuntimeError):
        TS.run_session(store, acc["id"], ["7300000000000000042"], runner=runner)
    assert _values(store, acc["id"])["msToken"] == "NEW"


def test_studio_login_page_does_not_overwrite_and_marks_reauth(store, monkeypatch):
    acc = store.upsert_account("tiktok", "tt", TT_COOKIES)
    monkeypatch.setattr(H, "_send_telegram", lambda store_, text: True)
    runner = _studio_runner({"status": "ok", "login_required": True, "videos": {},
                             "cookies": [{"name": "msToken", "value": "GUEST", "domain": ".tiktok.com"}]})
    TS.run_session(store, acc["id"], [], runner=runner)
    assert _values(store, acc["id"])["msToken"] == "OLD"
    assert _row(store, acc["id"])["session_state"] == "needs_reauth"


# --------------------------------------------------------------------------- #
# Состояние входа и одно сообщение на случай
# --------------------------------------------------------------------------- #


def test_transition_rules():
    assert H.transition("", "", "needs_reauth") == {"state": "needs_reauth", "alerted": "needs_reauth", "send": True}
    assert H.transition("needs_reauth", "needs_reauth", "needs_reauth")["send"] is False
    assert H.transition("needs_reauth", "needs_reauth", "error") == {
        "state": "needs_reauth", "alerted": "needs_reauth", "send": False}
    assert H.transition("ok", "", "error") == {"state": "error", "alerted": "", "send": False}
    assert H.transition("needs_reauth", "needs_reauth", "captcha")["send"] is True
    assert H.transition("captcha", "captcha", "ok") == {"state": "ok", "alerted": "", "send": False}
    with pytest.raises(ValueError):
        H.transition("", "", "weird")


def test_alert_once_per_incident_and_again_after_recovery(store):
    acc = store.upsert_account("youtube", "yakoodev yt", YT_COOKIES)
    sent: list[str] = []
    sender = lambda store_, text: sent.append(text) or True  # noqa: E731
    for _ in range(3):
        H.record(store, acc["id"], "needs_reauth", "кнопка «Войти»", sender=sender)
    H.record(store, acc["id"], "error", "прокси упал", sender=sender)
    assert sent == ["Аккаунт «yakoodev yt» вылетел — обнови куки расширением"]
    assert _row(store, acc["id"])["session_state"] == "needs_reauth"
    H.record(store, acc["id"], "ok", sender=sender)
    H.record(store, acc["id"], "needs_reauth", sender=sender)
    assert len(sent) == 2


def test_unsent_alert_is_retried_and_new_cookies_close_the_incident(store):
    acc = store.upsert_account("tiktok", "сова", TT_COOKIES)
    H.record(store, acc["id"], "needs_reauth", sender=lambda s, t: False)  # Telegram не ответил
    sent: list[str] = []
    H.record(store, acc["id"], "needs_reauth", sender=lambda s, t: sent.append(t) or True)
    assert len(sent) == 1
    store.upsert_account("tiktok", "сова", "sessionid=S2")  # владелец обновил cookie
    row = _row(store, acc["id"])
    assert row["session_state"] == "" and row["session_alerted"] == ""
    H.record(store, acc["id"], "captcha", sender=lambda s, t: sent.append(t) or True)
    assert "капча" in sent[-1] and len(sent) == 2


# --------------------------------------------------------------------------- #
# Заход браузера: кто, когда, что сохранить
# --------------------------------------------------------------------------- #


class FakeBrowser:
    """Подмена ``node session-keepalive.cjs``: записывает вызовы, отвечает заданным итогом."""

    def __init__(self, answers=None, fail: str = ""):
        self.calls: list[dict] = []
        self.answers = answers or {}
        self.fail = fail

    def __call__(self, args, **kwargs):
        env = kwargs["env"]
        config = json.loads(env["KA_CONFIG"])
        self.calls.append({"args": list(args), "env": dict(env), "config": config, "locked": BROWSER_LOCK.locked()})
        if self.fail:
            return subprocess.CompletedProcess(args, 1, "", self.fail)
        answer = self.answers.get(config["platform"]) or _logged_in(config["platform"])
        return subprocess.CompletedProcess(args, 0, json.dumps(answer) + "\n", "")


def _logged_in(platform):
    if platform == "tiktok":
        return {"status": "ok", "url": "https://www.tiktok.com/foryou",
                "signals": {"captcha": False, "app_context": True, "user": "owl"},
                "cookies": [{"name": "sessionid", "value": "S1", "domain": ".tiktok.com"},
                            {"name": "msToken", "value": "ROTATED", "domain": ".tiktok.com"},
                            {"name": "_ttp", "value": "ad", "domain": ".tiktok.com"}]}
    return {"status": "ok", "url": "https://www.youtube.com/",
            "signals": {"captcha": False, "logged_in": True, "sign_in_button": False},
            "cookies": [{"name": n, "value": v, "domain": ".youtube.com"} for n, v in
                        (("SID", "1"), ("SAPISID", "5"), ("__Secure-1PSIDTS", "new"))]
            + [{"name": "SID", "value": "google", "domain": ".google.com"}]}


def _enable(store, **cfg):
    K.save_settings(store, {"enabled": True, **cfg})


def test_default_on_only_with_own_proxy_and_per_account_switch(store):
    with_proxy = store.upsert_account("tiktok", "tt-proxy", TT_COOKIES, proxy_url=PROXY)
    plain = store.upsert_account("tiktok", "tt-plain", TT_COOKIES)
    yt = store.upsert_account("youtube", "yt", YT_COOKIES, proxy_url=PROXY)
    store.upsert_account("instagram", "ig", "sessionid=1", proxy_url=PROXY)
    assert [r["id"] for r in K.eligible(store)] == [with_proxy["id"], yt["id"]]
    store.set_account_keepalive(plain["id"], True)
    store.set_account_keepalive(yt["id"], False)
    assert [r["id"] for r in K.eligible(store)] == [with_proxy["id"], plain["id"]]
    listed = {a["label"]: a for a in store.list_accounts()}
    assert listed["tt-proxy"]["keepalive_on"] is True and listed["tt-proxy"]["keepalive"] is None
    assert listed["yt"]["keepalive_on"] is False and listed["yt"]["keepalive"] is False


def test_tick_visits_one_account_at_a_time_through_the_browser_lock(store):
    _enable(store)
    tt = store.upsert_account("tiktok", "tt", TT_COOKIES, proxy_url=PROXY, user_agent="Mozilla/5.0 Owl")
    yt = store.upsert_account("youtube", "yt", YT_COOKIES, proxy_url=PROXY)
    browser = FakeBrowser()
    first = K.tick(store, runner=browser)
    assert first["account_id"] == tt["id"] and len(browser.calls) == 1
    call = browser.calls[0]
    assert call["locked"]
    assert call["env"]["KA_PROXY"] == PROXY and call["env"]["KA_UA"] == "Mozilla/5.0 Owl"
    assert not any("s3cret" in a or "sessionid" in a for a in call["args"])  # секреты — только окружением
    assert call["config"]["url"] == "https://www.tiktok.com/foryou"
    assert call["config"]["locale"] == "ru-RU" and call["config"]["timezoneId"] == "Asia/Tbilisi"
    assert call["config"]["reseed"] is True and 20 <= call["config"]["stayMs"] / 1000 <= 40
    # Свежий msToken — в хранилище; рекламная _ttp — нет.
    assert _values(store, tt["id"]) == {"sessionid": "S1", "msToken": "ROTATED", "ttwid": "T1"}
    row = _row(store, tt["id"])
    assert row["session_state"] == "ok" and row["keepalive_at"] == "2026-11-11 12:00:00"
    gap = datetime.strptime(row["keepalive_next_at"], K.TIME_FMT) - NOW
    assert timedelta(hours=4) <= gap <= timedelta(hours=6)  # 5 ч ± 20 %

    second = K.tick(store, runner=browser)
    assert second["account_id"] == yt["id"]
    assert _values(store, yt["id"])["__Secure-1PSIDTS"] == "new" and _values(store, yt["id"])["SID"] == "1"
    assert K.tick(store, runner=browser) is None and len(browser.calls) == 2  # обоим ещё рано


def test_disabled_setting_means_no_visits(store):
    K.save_settings(store, {"enabled": False})
    store.upsert_account("tiktok", "tt", TT_COOKIES, proxy_url=PROXY)
    browser = FakeBrowser()
    assert K.tick(store, runner=browser) is None and not browser.calls


def test_interval_setting_is_validated(store):
    assert K.save_settings(store, {"interval_hours": 8})["interval_hours"] == 8
    with pytest.raises(ValueError):
        K.save_settings(store, {"interval_hours": 0.1})


def test_profile_is_reseeded_only_when_the_owner_sends_new_cookies(store):
    _enable(store)
    acc = store.upsert_account("tiktok", "tt", TT_COOKIES, proxy_url=PROXY)
    browser = FakeBrowser()
    K.visit(store, acc["id"], runner=browser)
    marker = K.profile_dir(acc["id"]) / "keep.txt"
    marker.write_text("profile data")
    K.visit(store, acc["id"], runner=browser)  # ротация от захода — не повод стирать профиль
    assert browser.calls[1]["config"]["reseed"] is False and marker.exists()
    store.db.execute("UPDATE accounts SET cookies_set_at = '2026-11-12 08:00:00' WHERE id = ?", (acc["id"],))
    K.visit(store, acc["id"], runner=browser)
    assert browser.calls[2]["config"]["reseed"] is True and not marker.exists()


def test_logged_out_visit_marks_reauth_alerts_once_and_keeps_cookies(store, monkeypatch):
    _enable(store)
    sent: list[str] = []
    monkeypatch.setattr(H, "_send_telegram", lambda store_, text: sent.append(text) or True)
    acc = store.upsert_account("tiktok", "сова", TT_COOKIES, proxy_url=PROXY)
    guest = {"status": "ok", "url": "https://www.tiktok.com/login", "signals": {},
             "cookies": [{"name": "msToken", "value": "GUEST", "domain": ".tiktok.com"}]}
    out = K.visit(store, acc["id"], runner=FakeBrowser({"tiktok": guest}))
    assert out["state"] == "needs_reauth" and out["rotated"] == []
    assert _values(store, acc["id"])["msToken"] == "OLD"
    assert sent == ["Аккаунт «сова» вылетел — обнови куки расширением"]
    # Вылетевший не посещается, пока владелец не пришлёт новые cookie.
    assert K.tick(store, runner=FakeBrowser()) is None
    store.upsert_account("tiktok", "сова", "sessionid=S2; msToken=M2", proxy_url=PROXY)
    assert K.tick(store, runner=FakeBrowser())["state"] == "ok"


def test_captcha_is_reported_not_solved(store, monkeypatch):
    _enable(store)
    sent: list[str] = []
    monkeypatch.setattr(H, "_send_telegram", lambda store_, text: sent.append(text) or True)
    acc = store.upsert_account("youtube", "yakoodev yt", YT_COOKIES, proxy_url=PROXY)
    sorry = {"status": "ok", "url": "https://www.google.com/sorry/index", "signals": {}, "cookies": []}
    K.visit(store, acc["id"], runner=FakeBrowser({"youtube": sorry}))
    K.visit(store, acc["id"], runner=FakeBrowser({"youtube": sorry}))
    assert _row(store, acc["id"])["session_state"] == "captcha" and len(sent) == 1 and "капча" in sent[0]


def test_browser_failure_is_not_an_incident_and_retries_in_an_hour(store, monkeypatch):
    _enable(store)
    monkeypatch.setattr(H, "_send_telegram", lambda store_, text: pytest.fail("сбой захода — не повод писать"))
    acc = store.upsert_account("tiktok", "tt", TT_COOKIES, proxy_url=PROXY)
    out = K.visit(store, acc["id"], runner=FakeBrowser(fail=f"Error: net::ERR_PROXY at {PROXY}\n"))
    assert out["state"] == "error" and "s3cret" not in out["reason"]
    row = _row(store, acc["id"])
    assert row["keepalive_at"] is None and row["keepalive_next_at"] == "2026-11-11 13:00:00"


@pytest.mark.parametrize("platform,data,state", [
    ("tiktok", {"url": "https://www.tiktok.com/foryou", "signals": {"captcha": True}, "cookies": []}, "captcha"),
    ("tiktok", {"url": "https://www.tiktok.com/foryou", "signals": {"app_context": True, "user": ""},
                "cookies": [{"name": "sessionid", "value": "S", "domain": ".tiktok.com"}]}, "needs_reauth"),
    ("tiktok", {"url": "https://www.tiktok.com/foryou", "signals": {}, "cookies": []}, "needs_reauth"),
    ("tiktok", {"url": "https://www.tiktok.com/foryou", "signals": {},
                "cookies": [{"name": "sessionid", "value": "S", "domain": ".tiktok.com"}]}, "ok"),
    ("youtube", {"url": "https://accounts.google.com/v3/signin/challenge/pwd", "cookies": []}, "captcha"),
    ("youtube", {"url": "https://accounts.google.com/ServiceLogin", "cookies": []}, "needs_reauth"),
    ("youtube", {"url": "https://www.youtube.com/", "signals": {"logged_in": True},
                 "cookies": [{"name": "SID", "value": "1", "domain": ".youtube.com"}]}, "needs_reauth"),
    ("youtube", {"url": "https://www.youtube.com/", "signals": {"sign_in_button": True},
                 "cookies": [{"name": n, "value": "1", "domain": ".youtube.com"} for n in ("SID", "SAPISID")]},
     "needs_reauth"),
])
def test_classify_logged_out_signals(platform, data, state):
    assert K.classify(platform, data)[0] == state


# --------------------------------------------------------------------------- #
# Перед загрузкой — свежая сессия
# --------------------------------------------------------------------------- #


def test_fresh_visit_rule():
    cfg = {"enabled": True, "fresh_before_upload_hours": 2}
    row = {"platform": "tiktok", "has_required_cookies": 1, "keepalive": None, "session_state": "ok",
           "keepalive_at": "2026-11-11 10:30:00"}
    assert not K.needs_fresh_visit(row, cfg, NOW, has_own_proxy=True)  # 1,5 ч — свежая
    assert K.needs_fresh_visit({**row, "keepalive_at": "2026-11-11 09:00:00"}, cfg, NOW, True)
    assert K.needs_fresh_visit({**row, "keepalive_at": None}, cfg, NOW, True)
    assert not K.needs_fresh_visit({**row, "keepalive_at": None}, cfg, NOW, False)  # без прокси — выключено
    assert not K.needs_fresh_visit({**row, "keepalive_at": None, "session_state": "needs_reauth"}, cfg, NOW, True)
    assert not K.needs_fresh_visit({**row, "keepalive_at": None}, {**cfg, "fresh_before_upload_hours": 0}, NOW, True)
    assert not K.needs_fresh_visit({**row, "keepalive_at": None}, {**cfg, "enabled": False}, NOW, True)


def test_upload_goes_with_cookies_from_a_fresh_visit(store, tmp_path, monkeypatch):
    _enable(store)
    acc = store.upsert_account("tiktok", "tt", TT_COOKIES, proxy_url=PROXY)
    browser = FakeBrowser()
    monkeypatch.setattr(K, "_default_runner", browser)
    seen = []

    class Provider:
        def __init__(self, platform):
            pass

        def upload(self, **kwargs):
            seen.append({c.name: c.value for c in kwargs["cookies"]})
            return ProviderResult(status="succeeded", remote_id="1")

    monkeypatch.setattr("app.worker.get_provider", Provider)
    _job(store, tmp_path, [acc["id"]])
    assert JobWorker(store).process_once()
    assert len(browser.calls) == 1 and 12 <= browser.calls[0]["config"]["stayMs"] / 1000 <= 20  # короткий заход
    assert seen[0]["msToken"] == "ROTATED"
    # Заход только что был — следующая загрузка идёт без него.
    _job(store, tmp_path, [acc["id"]], b"another-video")
    assert JobWorker(store).process_once() and len(browser.calls) == 1


def test_failed_fresh_visit_does_not_block_the_upload(store, tmp_path, monkeypatch):
    _enable(store)
    acc = store.upsert_account("tiktok", "tt", TT_COOKIES, proxy_url=PROXY)
    monkeypatch.setattr(K, "_default_runner", FakeBrowser(fail="Error: boom"))
    uploads = []

    class Provider:
        def __init__(self, platform):
            pass

        def upload(self, **kwargs):
            uploads.append(1)
            return ProviderResult(status="succeeded", remote_id="1")

    monkeypatch.setattr("app.worker.get_provider", Provider)
    _job(store, tmp_path, [acc["id"]])
    assert JobWorker(store).process_once() and uploads == [1]


def test_next_visit_is_jittered_within_bounds():
    rng = random.Random(1)
    gaps = {K.next_visit(NOW, 5, rng) - NOW for _ in range(50)}
    assert len(gaps) > 10 and all(timedelta(hours=4) <= g <= timedelta(hours=6) for g in gaps)


def test_api_switch_and_settings(tmp_path, monkeypatch):
    from test_api_pipeline import _client

    client, store_ = _client(tmp_path, monkeypatch)
    acc = store_.upsert_account("tiktok", "tt", TT_COOKIES, proxy_url=PROXY)
    listed = client.get("/api/accounts").json()[0]
    assert listed["keepalive_on"] is True and listed["session_state"] == ""
    off = client.put(f"/api/accounts/{acc['id']}/keepalive", json={"enabled": False})
    assert off.status_code == 200 and off.json()["keepalive_on"] is False
    back = client.put(f"/api/accounts/{acc['id']}/keepalive", json={"enabled": None}).json()
    assert back["keepalive"] is None and back["keepalive_on"] is True
    saved = client.put("/api/settings/session-keepalive", json={"interval_hours": 6, "enabled": True})
    assert saved.status_code == 200 and saved.json()["interval_hours"] == 6
    assert client.put("/api/settings/session-keepalive", json={"interval_hours": 100}).status_code == 400
    assert client.get("/api/settings/session-keepalive").json()["interval_hours"] == 6
