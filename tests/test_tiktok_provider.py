"""TikTok-загрузка без сети: сессия requests и помощник подписи подменены."""

import json

import pytest
import requests

from app.cookies import CookieRecord
from app.providers import tiktok
from app.providers.tiktok import TikTokProvider, _find_item_id
from app.settings import settings


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


class FakeSession:
    """Отвечает по куску URL; ``script`` — очередь ответов/исключений для URL."""

    def __init__(self, script):
        self.script = script
        self.calls = []
        self.headers = {}
        self.cookies = requests.cookies.RequestsCookieJar()
        self.proxies = {}
        self.verify = True

    def _answer(self, method, url, kwargs):
        self.calls.append((method, url, kwargs))
        for key, answers in self.script.items():
            if key in url:
                answer = answers.pop(0) if len(answers) > 1 else answers[0]
                if isinstance(answer, Exception):
                    raise answer
                return answer
        raise AssertionError(f"unexpected request {method} {url}")

    def get(self, url, **kwargs):
        return self._answer("GET", url, kwargs)

    def post(self, url, **kwargs):
        return self._answer("POST", url, kwargs)

    def head(self, url, **kwargs):
        return FakeResponse(200)


def _script(post_body=None, **overrides):
    script = {
        "project/create": [FakeResponse(body={"project": {"project_id": "p1"}})],
        "video/upload/auth": [FakeResponse(body={"video_token_v5": {
            "access_key_id": "k", "secret_acess_key": "s", "session_token": "t"}})],
        "ApplyUploadInner": [FakeResponse(body={"Result": {"InnerUploadAddress": {"UploadNodes": [{
            "Vid": "v0vid", "StoreInfos": [{"StoreUri": "store/uri", "Auth": "auth"}],
            "UploadHost": "upload.example", "SessionKey": "sk"}]}}})],
        "phase=transfer": [FakeResponse()],
        "phase=finish": [FakeResponse()],
        "CommitUploadInner": [FakeResponse()],
        "web/project/post/v1": [FakeResponse(body=post_body if post_body is not None else {
            "status_code": 0, "single_post_resp_list": [{"item_id": "7431234567890123456"}]})],
    }
    script.update(overrides)
    return script


@pytest.fixture
def fake_tiktok(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "runtime_dir", tmp_path / "runtime")
    monkeypatch.setattr(settings, "publish_upload_retry_seconds", 0)
    monkeypatch.setattr(settings, "publish_upload_retries", 2)
    monkeypatch.setattr(tiktok.TikTokProvider, "_ensure_signature_helper", lambda self: None)
    seen = {}

    def signature(signature_dir, user_agent, url, proxy_url=""):
        seen["ua"] = user_agent
        return json.dumps({"data": {"x-bogus": "xb", "signature": "sig"}})

    monkeypatch.setattr(tiktok, "_run_signature_helper", signature)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"0123456789")

    def run(script):
        session = FakeSession(script)
        monkeypatch.setattr(tiktok.requests, "Session", lambda: session)
        result = TikTokProvider().upload(
            cookies=[CookieRecord("sessionid", "abc", ".tiktok.com"), CookieRecord("msToken", "m", ".tiktok.com")],
            file_path=video, title="Заголовок #тест", description="", privacy="public",
            allow_comments=True, account_label="acc", proxy_url="",
        )
        return result, session, seen

    return run


def test_successful_post_keeps_item_id_and_link(fake_tiktok):
    result, session, _ = fake_tiktok(_script())
    assert result.status == "succeeded", result.error
    assert result.remote_id == "7431234567890123456"
    assert result.remote_url == "https://www.tiktok.com/@/video/7431234567890123456"
    assert result.response["video_id"] == "v0vid" and result.response["project_id"] == "p1"
    # Каждый запрос — с таймаутом (зависшее соединение больше не держит воркер).
    assert all("timeout" in kwargs for _, _, kwargs in session.calls)


def test_post_without_item_id_falls_back_to_vod_id(fake_tiktok):
    result, _, _ = fake_tiktok(_script(post_body={"status_code": 0}))
    assert result.status == "succeeded"
    assert result.remote_id == "v0vid" and result.remote_url == ""


def test_transient_failures_of_upload_steps_are_retried(fake_tiktok):
    script = _script(**{
        "project/create": [requests.ConnectionError("reset"),
                           FakeResponse(body={"project": {"project_id": "p1"}})],
        "phase=transfer": [FakeResponse(502), FakeResponse(429), FakeResponse()],
    })
    result, session, _ = fake_tiktok(script)
    assert result.status == "succeeded", result.error
    assert sum("project/create" in url for _, url, _ in session.calls) == 2
    assert sum("phase=transfer" in url for _, url, _ in session.calls) == 3


def test_retries_give_up_after_limit(fake_tiktok):
    result, session, _ = fake_tiktok(_script(**{"phase=transfer": [FakeResponse(503)]}))
    assert result.status == "failed"
    assert sum("phase=transfer" in url for _, url, _ in session.calls) == 3  # 1 + 2 повтора


def test_final_post_is_never_retried(fake_tiktok):
    result, session, _ = fake_tiktok(_script(**{"web/project/post/v1": [FakeResponse(502, {"status_code": 1})]}))
    assert result.status == "failed"
    assert sum("web/project/post/v1" in url for _, url, _ in session.calls) == 1


def test_user_agent_can_match_the_cookie_browser(fake_tiktok, monkeypatch):
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/141.0.0.0 Safari/537.36"
    monkeypatch.setattr(settings, "tiktok_user_agent", ua)
    result, session, seen = fake_tiktok(_script())
    assert result.status == "succeeded"
    assert session.headers["User-Agent"] == ua and seen["ua"] == ua
    post = next(kwargs for _, url, kwargs in session.calls if "web/project/post/v1" in url)
    assert post["headers"]["user-agent"] == ua


def test_find_item_id_shapes():
    assert _find_item_id({"data": {"aweme_id": 7431234567890123456}}) == "7431234567890123456"
    assert _find_item_id({"item_id": "short"}) == ""
    assert _find_item_id({"status_code": 0}) == ""
    assert _find_item_id([{"x": [{"itemId": "1234567890123"}]}]) == "1234567890123"
