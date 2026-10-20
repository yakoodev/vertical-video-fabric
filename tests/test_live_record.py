"""Запись эфира: имя канала, сборка записи в mp4, источник и чат со сдвигом."""

import subprocess

import pytest

from app import live_record
from test_api_pipeline import _require_ffmpeg


def test_parse_channel_accepts_names_and_links():
    assert live_record.parse_channel("Sareamura") == "sareamura"
    assert live_record.parse_channel("https://www.twitch.tv/sareamura/") == "sareamura"
    assert live_record.parse_channel("twitch.tv/some_user") == "some_user"
    for bad in ("", "a", "https://www.twitch.tv/videos", "имя канала"):
        with pytest.raises(ValueError):
            live_record.parse_channel(bad)


def test_record_to_source_finalizes_mp4_and_imports_chat_with_offset(tmp_path, monkeypatch):
    _require_ffmpeg()
    from app.settings import settings

    monkeypatch.setattr(settings, "source_dir", tmp_path)
    monkeypatch.setattr(live_record, "live_info", lambda ch: {"title": "стрим", "started_at": 1000.0})
    monkeypatch.setattr(live_record, "archive_vod_id", lambda ch: "2905714957")

    def fake_record(channel, out_path, token=None, on_progress=None):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=s=320x240:r=30:d=3",
                        "-f", "lavfi", "-i", "sine=d=3", "-c:v", "libx264", "-c:a", "aac", "-f", "mpegts",
                        str(out_path)], check=True)
        return 1000.0 + 3600.0  # запись началась через час после начала стрима

    monkeypatch.setattr(live_record, "record", fake_record)
    created, chat = [], []

    class Ingestor:
        def _create_source_from_file(self, source_type, stored, original_url):
            created.append((source_type, stored, original_url))
            return {"id": 7, "status": "ready"}

    source = live_record.record_to_source(Ingestor(), "Sareamura", on_recorded=lambda s, vod, off: chat.append((s["id"], vod, off)))
    assert source["id"] == 7
    kind, stored, url = created[0]
    assert kind == "twitch_url" and url == "https://www.twitch.tv/sareamura"
    assert stored.path.suffix == ".mp4" and stored.path.exists() and not list(tmp_path.glob("*.ts"))
    assert chat == [(7, "2905714957", round(3600.0 - live_record.LIVE_LATENCY_SEC, 1))]


def test_record_to_source_refuses_offline_channel(monkeypatch):
    monkeypatch.setattr(live_record, "live_info", lambda ch: None)
    with pytest.raises(ValueError, match="не в эфире"):
        live_record.record_to_source(object(), "sareamura")
