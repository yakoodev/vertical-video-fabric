"""Что площадка умеет: TikTok без «по ссылке» и с подписью до 2200 знаков. Отказ — при постановке, а не публичный пост."""

from __future__ import annotations

import pytest

from app.cookies import CookieRecord
from app.providers.tiktok import TikTokProvider
from app.publish_format import TIKTOK_UNLISTED, platform_refusal
from test_publish_duplicates import env  # noqa: F401 - та же среда: клип, аккаунт TikTok, подмена площадки


def test_unlisted_on_tiktok_is_refused_at_queue_time(env):  # noqa: F811
    response = env["client"].post(f"/api/clips/{env['clip']['id']}/posts",
                                  json={"title": "Т", "targets": [env["account"]["id"]], "privacy": "unlisted"})
    assert response.status_code == 400 and response.json()["detail"] == TIKTOK_UNLISTED
    assert env["store"].list_jobs() == []


def test_unlisted_stays_allowed_on_youtube():
    assert platform_refusal({"youtube"}, "Т", "", "unlisted") == ""


def test_private_and_public_are_fine_on_tiktok():
    assert platform_refusal({"tiktok"}, "Т", "", "private") == ""
    assert platform_refusal({"tiktok"}, "Т", "", "public") == ""


def test_caption_over_the_limit_is_refused_and_at_the_limit_is_fine():
    title = "З" * 100
    assert platform_refusal({"tiktok"}, title, "о" * (2200 - 102), "public") == ""
    refused = platform_refusal({"tiktok"}, title, "о" * (2200 - 101), "public")
    assert "2201" in refused and "2200" in refused


def test_batch_reports_the_refusal_instead_of_queueing(env):  # noqa: F811
    response = env["client"].post("/api/clips/posts-batch", json={
        "clip_ids": [env["clip"]["id"]], "targets": [env["account"]["id"]], "privacy": "unlisted"})
    assert response.status_code == 200
    assert response.json()["jobs"] == [] and response.json()["skipped"][0]["reason"] == TIKTOK_UNLISTED


def test_provider_never_posts_unlisted_publicly(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    result = TikTokProvider().upload(cookies=[CookieRecord("sessionid", "a")], file_path=video, title="Т",
                                     description="", privacy="unlisted", allow_comments=True, account_label="a",
                                     proxy_url="")
    assert result.status == "failed" and result.error == TIKTOK_UNLISTED


@pytest.mark.parametrize("privacy, visibility", [("public", 0), ("private", 1)])
def test_visibility_sent_to_tiktok(privacy, visibility, monkeypatch, tmp_path):
    import json

    from app.providers import tiktok
    from test_tiktok_provider import FakeSession, _script

    monkeypatch.setattr(tiktok.TikTokProvider, "_ensure_signature_helper", lambda self: None)
    monkeypatch.setattr(tiktok, "_run_signature_helper",
                        lambda *a, **k: json.dumps({"data": {"x-bogus": "xb", "signature": "sig"}}))
    session = FakeSession(_script())
    monkeypatch.setattr(tiktok.requests, "Session", lambda: session)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    result = TikTokProvider().upload(cookies=[CookieRecord("sessionid", "a", ".tiktok.com")], file_path=video,
                                     title="Т", description="", privacy=privacy, allow_comments=False,
                                     account_label="a", proxy_url="")
    assert result.status == "succeeded", result.error
    post = next(kwargs for _, url, kwargs in session.calls if "web/project/post/v1" in url)
    info = json.loads(post["data"])["feature_common_info_list"][0]["privacy_setting_info"]
    assert info["visibility_type"] == visibility and info["allow_comment"] == 0
