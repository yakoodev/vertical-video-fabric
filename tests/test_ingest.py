from io import BytesIO

import httpx
import pytest

from app.crypto import CookieCipher
from app.db import Database
from app.ingest import (
    MediaMetadata,
    SourceIngestor,
    StoredSourceFile,
    classify_source_url,
    download_direct_url,
)
from app.settings import settings
from app.store import AppStore


def _store(tmp_path, monkeypatch) -> AppStore:
    data_dir = tmp_path / "data"
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "upload_dir", data_dir / "uploads")
    monkeypatch.setattr(settings, "source_dir", data_dir / "sources")
    monkeypatch.setattr(settings, "clip_dir", data_dir / "clips")
    monkeypatch.setattr(settings, "banner_dir", data_dir / "banners")
    monkeypatch.setattr(settings, "subtitle_dir", data_dir / "subtitles")
    monkeypatch.setattr(settings, "tmp_dir", data_dir / "tmp")
    monkeypatch.setattr(settings, "runtime_dir", data_dir / "runtime")
    monkeypatch.setattr(settings, "log_dir", data_dir / "logs")
    settings.ensure_dirs()
    db = Database(data_dir / "app.sqlite")
    db.init()
    return AppStore(db, CookieCipher(data_dir / "secret.key"))


def test_upload_ingestion_saves_source_and_metadata(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    ingestor = SourceIngestor(
        store,
        probe=lambda path: MediaMetadata(
            duration_sec=12.5,
            width=1080,
            height=1920,
            fps=30,
            raw={"format": "mock"},
        ),
    )

    source = ingestor.ingest_upload(BytesIO(b"video-bytes"), "clip.mp4")

    assert source["source_type"] == "upload"
    assert source["status"] == "ready"
    assert source["duration_sec"] == 12.5
    assert source["width"] == 1080
    assert source["local_path"].endswith("-clip.mp4")


def test_direct_url_downloader_streams_to_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "source_dir", tmp_path / "sources")
    monkeypatch.setattr(settings, "max_upload_bytes", 100)

    class FakeResponse:
        headers = {"content-type": "video/mp4"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def raise_for_status(self):
            return None

        def iter_bytes(self):
            yield b"abc"
            yield b"def"

    monkeypatch.setattr("app.ingest.httpx.stream", lambda *args, **kwargs: FakeResponse())

    stored = download_direct_url("https://cdn.example/video.mp4")

    assert stored.original_filename == "video.mp4"
    assert stored.path.read_bytes() == b"abcdef"
    assert stored.size_bytes == 6


def test_direct_url_downloader_retries_transient_network_error(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "source_dir", tmp_path / "sources")
    monkeypatch.setattr(settings, "max_upload_bytes", 100)
    monkeypatch.setattr(settings, "external_http_retries", 2)
    monkeypatch.setattr(settings, "external_http_retry_seconds", 0)
    calls = {"count": 0}

    class FakeResponse:
        headers = {"content-type": "video/mp4"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def raise_for_status(self):
            return None

        def iter_bytes(self):
            yield b"ok"

    def fake_stream(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise httpx.ConnectError("temporary network failure")
        return FakeResponse()

    monkeypatch.setattr("app.ingest.httpx.stream", fake_stream)

    stored = download_direct_url("https://cdn.example/video.mp4")

    assert stored.path.read_bytes() == b"ok"
    assert calls["count"] == 2


def test_youtube_url_ingestion_uses_downloader_adapter(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    called = {}

    def fake_youtube_downloader(url: str) -> StoredSourceFile:
        called["url"] = url
        path = settings.source_dir / "youtube.mp4"
        path.write_bytes(b"yt")
        return StoredSourceFile(path, "youtube.mp4", "sha", 2)

    ingestor = SourceIngestor(
        store,
        probe=lambda path: MediaMetadata(duration_sec=30, width=1280, height=720, fps=25, raw={}),
        youtube_downloader=fake_youtube_downloader,
    )

    source = ingestor.ingest_url("https://youtu.be/video-id")

    assert called["url"] == "https://youtu.be/video-id"
    assert source["source_type"] == "youtube_url"
    assert source["status"] == "ready"


def test_source_url_validation_rejects_non_http():
    with pytest.raises(ValueError):
        classify_source_url("ftp://example.com/video.mp4")
