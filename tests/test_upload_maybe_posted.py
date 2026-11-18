"""Обрыв на самой загрузке → «ролик мог выйти» (maybe_posted): повтор задания просит подтверждения.

18.11 подсмотрено у MoneyPrinterTurbo: таймаут без ответа ≠ сбой — сначала проверить площадку.
У TikTok так было; YouTube (час без ответа) и Instagram (обрыв связи на загрузке) писали обычный сбой."""

import subprocess
import sys
import types

from app.cookies import CookieRecord
from app.providers import youtube
from app.providers.instagram import InstagramProvider
from app.providers.youtube import YouTubeProvider
from test_youtube_channel import COOKIES


def _yt(monkeypatch, tmp_path, fake_run):
    monkeypatch.setattr(youtube.subprocess, "run", fake_run)
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    return YouTubeProvider().upload(cookies=COOKIES, file_path=clip, title="T", description="D", privacy="public",
                                    allow_comments=True, account_label="yt", proxy_url="")


def test_youtube_timeout_may_have_posted(monkeypatch, tmp_path):
    def fake_run(args, **_kw):
        raise subprocess.TimeoutExpired(args, 3600)

    result = _yt(monkeypatch, tmp_path, fake_run)
    assert result.status == "failed" and result.maybe_posted
    assert "мог выйти" in result.error


def test_youtube_garbled_answer_may_have_posted(monkeypatch, tmp_path):
    result = _yt(monkeypatch, tmp_path, lambda args, **_kw: subprocess.CompletedProcess(args, 0, "not json", ""))
    assert result.maybe_posted


def test_youtube_refusal_is_not_maybe_posted(monkeypatch, tmp_path):
    result = _yt(monkeypatch, tmp_path,
                 lambda args, **_kw: subprocess.CompletedProcess(args, 1, '{"error": "quota"}', ""))
    assert result.status == "failed" and not result.maybe_posted


def _ig(monkeypatch, tmp_path, *, login_exc=None, upload_exc=None):
    class Client:
        def set_proxy(self, _url):
            pass

        def login_by_sessionid(self, _sid):
            if login_exc:
                raise login_exc

        def clip_upload(self, _path, _caption):
            raise upload_exc

    monkeypatch.setitem(sys.modules, "instagrapi", types.SimpleNamespace(Client=Client))
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    return InstagramProvider().upload(cookies=[CookieRecord("sessionid", "s", ".instagram.com")],
                                      file_path=clip, title="T", description="D", privacy="public",
                                      allow_comments=True, account_label="ig", proxy_url="")


def test_instagram_drop_during_upload_may_have_posted(monkeypatch, tmp_path):
    class ReadTimeout(Exception):
        pass

    result = _ig(monkeypatch, tmp_path, upload_exc=ReadTimeout("Read timed out. (read timeout=60)"))
    assert result.status == "failed" and result.maybe_posted and "мог выйти" in result.error


def test_instagram_drop_at_login_is_a_plain_failure(monkeypatch, tmp_path):
    result = _ig(monkeypatch, tmp_path, login_exc=ConnectionError("connection reset"))
    assert not result.maybe_posted


def test_instagram_answered_refusal_is_not_maybe_posted(monkeypatch, tmp_path):
    result = _ig(monkeypatch, tmp_path, upload_exc=Exception("feedback_required: video too long"))
    assert not result.maybe_posted
