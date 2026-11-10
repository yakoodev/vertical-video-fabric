"""YouTube-загрузка без сети: subprocess помощника подменён; сам помощник — node --test."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.cookies import CookieRecord
from app.providers import youtube
from app.providers.youtube import YouTubeProvider, _category_id, _made_for_kids
from app.settings import settings

ROOT = Path(__file__).resolve().parents[1]
COOKIES = [CookieRecord(name, "v", ".youtube.com") for name in ("SID", "HSID", "SSID", "APISID", "SAPISID")]


def _upload(monkeypatch, tmp_path, stdout, returncode=0):
    seen = {}

    def fake_run(args, input, **kwargs):
        seen["payload"] = json.loads(input)
        return subprocess.CompletedProcess(args, returncode, stdout, "")

    monkeypatch.setattr(youtube.subprocess, "run", fake_run)
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    result = YouTubeProvider().upload(
        cookies=COOKIES, file_path=clip, title="Title #shorts", description="Desc", privacy="public",
        allow_comments=True, account_label="yt", proxy_url="",
    )
    return result, seen["payload"]


def test_payload_carries_audience_category_and_retries(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "youtube_made_for_kids", "false")
    monkeypatch.setattr(settings, "youtube_category_id", "20")
    monkeypatch.setattr(settings, "publish_upload_retries", 2)
    monkeypatch.setattr(settings, "publish_upload_retry_seconds", 5)
    stdout = json.dumps({"ok": True, "videoId": "abc123XYZ", "uploadAttempts": 1,
                         "metadataSet": {"made_for_kids": False, "category": 20},
                         "refreshedCookies": {"SIDTS": "new"}})
    result, payload = _upload(monkeypatch, tmp_path, stdout)
    assert payload["madeForKids"] is False and payload["categoryId"] == 20
    assert payload["retries"] == 2 and payload["retrySeconds"] == 5
    assert payload["privacy"] == "PUBLIC"
    assert result.status == "succeeded" and result.remote_url == "https://youtu.be/abc123XYZ"
    assert result.response["metadataSet"] == {"made_for_kids": False, "category": 20}
    assert result.refreshed_cookies == {"SIDTS": "new"}
    assert "refreshedCookies" not in result.response


def test_unset_audience_and_empty_category_keep_old_payload(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "youtube_made_for_kids", "unset")
    monkeypatch.setattr(settings, "youtube_category_id", "")
    _, payload = _upload(monkeypatch, tmp_path, json.dumps({"ok": True, "videoId": "v"}))
    assert "madeForKids" not in payload and "categoryId" not in payload


def test_crashed_helper_still_hands_back_rotated_cookies(monkeypatch, tmp_path):
    # 10.11 задание 117: помощник упал на 401, свежие cookie пропали — вход разлогинило.
    stdout = json.dumps({"ok": False, "error": "Error: Request to https://upload.youtube.com failed with status code 401",
                         "refreshedCookies": {"__Secure-1PSIDTS": "new"}})
    result, _ = _upload(monkeypatch, tmp_path, stdout, returncode=1)
    assert result.status == "needs_reauth" and "401" in result.error
    assert result.refreshed_cookies == {"__Secure-1PSIDTS": "new"}
    assert "refreshedCookies" not in result.response
    # Не JSON — как раньше, без cookie.
    plain, _ = _upload(monkeypatch, tmp_path, "boom", returncode=1)
    assert plain.status == "failed" and plain.refreshed_cookies == {}


def test_setting_parsers():
    assert _made_for_kids("false") is False and _made_for_kids("NO") is False
    assert _made_for_kids("true") is True
    assert _made_for_kids("") is None and _made_for_kids("unset") is None
    assert _category_id("24") == 24 and _category_id("") == 0 and _category_id("games") == 0


@pytest.mark.skipif(not shutil.which(settings.node_bin), reason="node is not installed")
def test_node_helper_unit_tests():
    proc = subprocess.run([settings.node_bin, "--test", str(ROOT / "node" / "youtube-upload.test.mjs")],
                          capture_output=True, text=True, timeout=120, check=False, cwd=ROOT)
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-2000:]
