"""TikTok-загрузка без сети: сессия requests и помощник подписи подменены."""

import json
import shutil
import subprocess

import pytest
import requests

from app.cookies import CookieRecord
from app.providers import tiktok
from app.publish_format import image_size
from app.providers.base import safe_provider_error
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

    def run(script, **overrides):
        session = FakeSession(script)
        monkeypatch.setattr(tiktok.requests, "Session", lambda: session)
        args = dict(
            cookies=[CookieRecord("sessionid", "abc", ".tiktok.com"), CookieRecord("msToken", "m", ".tiktok.com")],
            file_path=video, title="Заголовок #тест", description="", privacy="public",
            allow_comments=True, account_label="acc", proxy_url="",
        )
        result = TikTokProvider().upload(**{**args, **overrides})
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
    assert result.error == tiktok.NO_POST_LINK  # владелец видит, почему у поста не будет чисел


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


def _post_calls(session):
    return sum("web/project/post/v1" in url for _, url, _ in session.calls)


class HtmlResponse(FakeResponse):
    def __init__(self, status, text):
        super().__init__(status)
        self.text = text

    def json(self):
        raise ValueError("not json")


# --- правдивая причина: cookie, прокси и сеть не путаются ------------------------------------------------------------ #


def test_dead_cookies_are_needs_reauth_with_tiktok_words(fake_tiktok):
    answer = FakeResponse(body={"status_code": 8, "status_msg": "Please login first"})
    result, session, _ = fake_tiktok(_script(**{"project/create": [answer]}))
    assert result.status == "needs_reauth"
    assert "Please login first" in result.error and "KeyError" not in result.error
    assert _post_calls(session) == 0


def test_session_gated_step_without_field_asks_for_fresh_cookies(fake_tiktok):
    result, session, _ = fake_tiktok(_script(**{"video/upload/auth": [FakeResponse(body={"status_code": 5})]}))
    assert result.status == "needs_reauth"
    assert "video_token_v5" in result.error
    assert _post_calls(session) == 0


def test_expired_session_on_401_is_needs_reauth(fake_tiktok):
    result, _, _ = fake_tiktok(_script(**{"video/upload/auth": [FakeResponse(401)]}))
    assert result.status == "needs_reauth"


def test_auth_in_step_name_or_url_does_not_blame_cookies(fake_tiktok):
    result, _, _ = fake_tiktok(_script(**{"video/upload/auth": [FakeResponse(503)]}))
    assert result.status == "failed" and "503" in result.error

    down = requests.ConnectionError("Max retries exceeded with url: /api/v1/video/upload/auth/?aid=1988")
    result, _, _ = fake_tiktok(_script(**{"video/upload/auth": [down]}))
    assert result.status == "failed"


def test_proxy_auth_failure_is_named_and_is_not_a_cookie_problem(fake_tiktok):
    dead = requests.exceptions.ProxyError("Tunnel connection failed: 407 Proxy Authentication Required")
    result, session, _ = fake_tiktok(_script(**{"project/create": [dead]}), proxy_url="http://user:SECRET@geo.example:8080")
    assert result.status == "failed"
    assert "geo.example:8080" in result.error and "407" in result.error
    assert "SECRET" not in result.error and "напрямую не пробовали" in result.error
    assert session.calls and _post_calls(session) == 0


def test_html_error_page_with_auth_words_does_not_blame_cookies(fake_tiktok):
    page = HtmlResponse(502, "<html>Proxy authentication gateway error, please login to the panel</html>")
    result, _, _ = fake_tiktok(_script(**{"project/create": [page]}))
    assert result.status == "failed" and "502" in result.error


def test_local_failure_is_reported_as_it_is(fake_tiktok, tmp_path):
    missing = tmp_path / "session_auth_cookie" / "gone.mp4"
    result, _, _ = fake_tiktok(_script(), file_path=missing)
    assert result.status == "failed"  # слова «session», «auth» в пути — не повод просить cookie
    assert "FileNotFoundError" in result.error


def test_broken_signature_answer_fails_before_the_post(fake_tiktok, monkeypatch):
    monkeypatch.setattr(tiktok, "_run_signature_helper", lambda *a, **k: "not json")
    result, session, _ = fake_tiktok(_script())
    assert result.status == "failed" and "signature" in result.error
    assert _post_calls(session) == 0


# --- сам пост: что точно не вышло, а что могло ---------------------------------------------------------------------- #


def test_timeout_on_the_post_is_not_retried_and_says_it_may_have_gone_out(fake_tiktok):
    result, session, _ = fake_tiktok(_script(**{"web/project/post/v1": [requests.Timeout("read timed out")]}))
    assert result.status == "failed" and result.maybe_posted
    assert "мог выйти" in result.error
    assert _post_calls(session) == 1


def test_gateway_error_on_the_post_may_have_gone_out(fake_tiktok):
    result, session, _ = fake_tiktok(_script(**{"web/project/post/v1": [HtmlResponse(502, "bad gateway")]}))
    assert result.status == "failed" and result.maybe_posted
    assert _post_calls(session) == 1


def test_proxy_refusing_the_post_means_nothing_went_out(fake_tiktok):
    dead = requests.exceptions.ProxyError("Cannot connect to proxy")
    result, session, _ = fake_tiktok(_script(**{"web/project/post/v1": [dead]}), proxy_url="http://u:p@geo.example:8080")
    assert result.status == "failed" and not result.maybe_posted
    assert _post_calls(session) == 1


def test_tiktok_refusal_of_the_post_is_final_and_keeps_its_words(fake_tiktok):
    body = {"status_code": 4, "status_msg": "You are posting too fast. Take a rest."}
    result, session, _ = fake_tiktok(_script(post_body=body))
    assert result.status == "failed" and not result.maybe_posted
    assert result.error == "You are posting too fast. Take a rest."
    assert _post_calls(session) == 1


def test_login_refusal_of_the_post_is_needs_reauth(fake_tiktok):
    result, _, _ = fake_tiktok(_script(post_body={"status_code": 8, "status_msg": "Login expired"}))
    assert result.status == "needs_reauth" and not result.maybe_posted


# --- секреты и файл --------------------------------------------------------------------------------------------------- #


def test_errors_carry_no_tokens_or_passwords(fake_tiktok):
    leak = requests.ConnectionError(
        "HTTPSConnectionPool: url: /tiktok/web/project/post/v1/?msToken=TOKENVALUE&aid=1988 proxy http://u:P@SS@geo.example:1")
    result, _, _ = fake_tiktok(_script(**{"web/project/post/v1": [leak]}))
    assert "TOKENVALUE" not in result.error and "P@SS" not in result.error
    assert result.maybe_posted


def test_safe_provider_error_masks_all_proxy_forms():
    text = safe_provider_error("a socks5://u:pa@ss@h:1 b https://x:y@h:2/ Cookie: sessionid=abc\nnext", "f")
    assert "pa@ss" not in text and "x:y" not in text and "sessionid=abc" not in text
    assert "h:1" in text and "next" in text


def test_file_goes_in_chunks_with_matching_checksums(fake_tiktok, tmp_path, monkeypatch):
    monkeypatch.setattr(tiktok, "_CHUNK_SIZE", 4)
    video = tmp_path / "chunks.mp4"
    video.write_bytes(b"0123456789")
    result, session, _ = fake_tiktok(_script(), file_path=video)
    assert result.status == "succeeded", result.error
    parts = [kwargs["data"] for _, url, kwargs in session.calls if "phase=transfer" in url]
    assert parts == [b"0123", b"4567", b"89"]
    finish = next(kwargs["data"] for _, url, kwargs in session.calls if "phase=finish" in url)
    assert finish == ",".join(f"{i + 1}:{tiktok._crc32(part)}" for i, part in enumerate(parts))
    apply_url = next(url for _, url, _ in session.calls if "ApplyUploadInner" in url)
    assert "FileSize=10&" in apply_url


# --- обложка: кадр ролика, как его шлёт TikTok Studio (poster_delay + cover_info.cover_uri) -------------------- #

_VIDEO_APPLY = FakeResponse(body={"Result": {"InnerUploadAddress": {"UploadNodes": [{
    "Vid": "v0vid", "StoreInfos": [{"StoreUri": "store/uri", "Auth": "auth"}],
    "UploadHost": "upload.example", "SessionKey": "sk"}]}}})
_IMAGE_APPLY = FakeResponse(body={"Result": {"InnerUploadAddress": {"UploadNodes": [{
    "Vid": "", "StoreInfos": [{"StoreUri": "tos-x/cover1", "Auth": "img-auth"}],
    "UploadHost": "img.example", "SessionKey": "img-sk"}]}}})


def _cover_script(**overrides):
    return _script(**{
        "ApplyUploadInner": [_VIDEO_APPLY, _IMAGE_APPLY],
        "upload/v1/": [FakeResponse(body={"code": 2000, "data": {"crc32": "x"}})],
        "CommitUploadInner": [FakeResponse(), FakeResponse(body={"Result": {"Results": [
            {"Uri": "tos-x/cover1", "UriStatus": 2000}]}})],
        **overrides,
    })


def _feature(session):
    post = next(kwargs for _, url, kwargs in session.calls if "web/project/post/v1" in url)
    return json.loads(post["data"])["single_post_req_list"][0]["single_post_feature_info"]


def test_without_cover_the_post_is_as_before(fake_tiktok, monkeypatch):
    monkeypatch.setattr(tiktok, "_cover_frame", lambda *a: pytest.fail("кадр без cover_sec не нужен"))
    result, session, _ = fake_tiktok(_script())
    assert result.status == "succeeded", result.error
    feature = _feature(session)
    assert feature["poster_delay"] == 0 and "cover_info" not in feature
    assert sum("ApplyUploadInner" in url for _, url, _ in session.calls) == 1  # только видео
    assert "cover" not in result.response


def test_cover_second_goes_as_poster_delay_and_uploaded_frame(fake_tiktok, monkeypatch):
    frames = []
    monkeypatch.setattr(tiktok, "_cover_frame", lambda path, sec: frames.append(sec) or b"\xff\xd8" + b"j" * 300)
    result, session, _ = fake_tiktok(_cover_script(), cover_sec=3.6)
    assert result.status == "succeeded", result.error
    assert frames == [3.6]
    feature = _feature(session)
    assert feature["poster_delay"] == 3  # Studio: кадр 3135 мс → 3 (целые секунды)
    info = feature["cover_info"]
    assert info["cover_uri"] == "tos-x/cover1" and info["cover_type"] == 2 and info["frame_duration"] == 3600
    assert (info["cover_width"], info["cover_height"]) == (486, 648)
    # Картинка — в VOD как обложка (Scene=poster), одним куском, с CRC и своим Auth.
    image_apply = [url for _, url, _ in session.calls if "ApplyUploadInner" in url][1]
    assert "FileType=image" in image_apply and "Scene=poster" in image_apply and "FileSize=302&" in image_apply
    upload = next(kwargs for _, url, kwargs in session.calls if "upload/v1/" in url)
    assert upload["headers"]["Authorization"] == "img-auth"
    assert upload["headers"]["Content-Crc32"] == tiktok._crc32(upload["data"])
    commit = [kwargs for _, url, kwargs in session.calls if "CommitUploadInner" in url][1]
    assert json.loads(commit["data"]) == {"SessionKey": "img-sk", "Functions": []}
    assert result.response["cover"] == {"sec": 3.6, "poster_delay": 3, "image": True}
    assert "cover_warning" not in result.response


def test_cover_image_failure_does_not_stop_the_post(fake_tiktok, monkeypatch):
    monkeypatch.setattr(tiktok, "_cover_frame", lambda path, sec: b"j" * 300)
    result, session, _ = fake_tiktok(_cover_script(**{"upload/v1/": [FakeResponse(403)]}), cover_sec=2)
    assert result.status == "succeeded", result.error
    feature = _feature(session)
    assert feature["poster_delay"] == 2 and "cover_info" not in feature
    assert "не удалось загрузить" in result.response["cover_warning"]


def test_cover_second_past_the_end_keeps_tiktok_default(fake_tiktok, monkeypatch):
    monkeypatch.setattr(tiktok, "_cover_frame", lambda path, sec: None)
    result, session, _ = fake_tiktok(_script(), cover_sec=99)
    assert result.status == "succeeded", result.error
    feature = _feature(session)
    assert feature["poster_delay"] == 0 and "cover_info" not in feature
    assert "нет кадра" in result.response["cover_warning"]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="нужен ffmpeg")
def test_cover_frame_is_a_3x4_jpeg_and_none_past_the_end(tmp_path):
    video = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=360x640:rate=10:duration=2",
                    "-pix_fmt", "yuv420p", str(video)], check=True)
    frame = tiktok._cover_frame(video, 1.0)
    assert frame and frame[:2] == b"\xff\xd8"
    probe = tmp_path / "cover.jpg"
    probe.write_bytes(frame)
    assert image_size(probe) == (486, 648)
    assert tiktok._cover_frame(video, 9.0) is None
