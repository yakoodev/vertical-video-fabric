"""Файл записи удалён: источник отвечает has_file, запись качается заново в ТОТ ЖЕ источник."""

import threading
import time

from fastapi.testclient import TestClient

from app.downloads import DownloadService
from app.ingest import MediaMetadata, SourceIngestor, StoredSourceFile
from app.settings import settings
from tests.test_ingest import _store


def _source(store, name="stream.mp4", url="https://www.twitch.tv/videos/1", present=False):
    path = settings.source_dir / name
    if present:
        path.write_bytes(b"video")
    return store.create_source(
        "twitch_url", path, original_url=url, original_filename=name, duration_sec=100, width=1280, height=720,
        fps=60, status="ready",
    )


def _ingestor(store, name="fresh.mp4", duration=101.5):
    def fake_twitch(url: str) -> StoredSourceFile:
        path = settings.source_dir / name
        path.write_bytes(b"fresh")
        return StoredSourceFile(path, name, "newsha", 5)

    return SourceIngestor(
        store,
        probe=lambda _p: MediaMetadata(duration_sec=duration, width=1920, height=1080, fps=60, raw={}),
        twitch_downloader=fake_twitch,
    )


def test_has_file_in_source_and_list(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    gone = _source(store, "gone.mp4")
    here = _source(store, "here.mp4", present=True)
    assert store.get_source(gone["id"])["has_file"] is False
    assert store.get_source(here["id"])["has_file"] is True
    flags = {s["id"]: s["has_file"] for s in store.list_sources()}
    assert flags == {gone["id"]: False, here["id"]: True}


def test_restore_file_keeps_the_same_source_and_its_moments(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    source = _source(store, "gone.mp4")
    ingestor = _ingestor(store)
    before = len(store.list_sources())

    restored = ingestor.restore_file(source["id"])

    assert restored["id"] == source["id"] and restored["has_file"] is True
    assert restored["duration_sec"] == 101.5 and restored["sha256"] == "newsha"
    assert restored["original_filename"] == "gone.mp4"  # название не трогаем
    assert len(store.list_sources()) == before  # новый источник не появился


def test_restore_task_runs_in_background_and_is_not_started_twice(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    source = _source(store, "gone.mp4")
    release = threading.Event()
    calls = []

    class Slow:
        def restore_file(self, source_id, *, quality=""):
            calls.append(source_id)
            release.wait(5)
            return {"id": source_id, "status": "ready", "original_filename": "gone.mp4"}

    service = DownloadService(Slow())
    first = service.start(source["original_url"], restore_source_id=source["id"])
    second = service.start(source["original_url"], restore_source_id=source["id"])
    assert first["id"] == second["id"] and first["restore_source_id"] == source["id"]
    release.set()
    deadline = time.monotonic() + 5
    while service.get(first["id"])["status"] != "succeeded" and time.monotonic() < deadline:
        time.sleep(0.01)
    assert service.get(first["id"])["source_id"] == source["id"]
    assert calls == [source["id"]]


def test_redownload_api(tmp_path, monkeypatch):
    import app.main as main_module

    store = _store(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "auth_enabled", False)
    monkeypatch.setattr(main_module, "store", store)
    ingestor = _ingestor(store)
    service = DownloadService(ingestor)
    monkeypatch.setattr(main_module, "download_service", service)
    client = TestClient(main_module.app)

    gone = _source(store, "gone.mp4")
    here = _source(store, "here.mp4", present=True)
    no_url = _source(store, "file.mp4", url="")

    assert client.post("/api/sources/999999/redownload", json={}).status_code == 404
    assert client.post(f"/api/sources/{here['id']}/redownload", json={}).status_code == 409
    assert client.post(f"/api/sources/{no_url['id']}/redownload", json={}).status_code == 400

    task = client.post(f"/api/sources/{gone['id']}/redownload", json={}).json()
    assert task["restore_source_id"] == gone["id"]
    deadline = time.monotonic() + 5
    while service.get(task["id"])["status"] != "succeeded" and time.monotonic() < deadline:
        time.sleep(0.01)
    # Список источников и карточка источника теперь говорят: файл на месте.
    assert client.get(f"/api/sources/{gone['id']}").json()["has_file"] is True
    listed = {s["id"]: s for s in client.get("/api/sources?lite=1").json()}
    assert listed[gone["id"]]["has_file"] is True and listed[no_url["id"]]["has_file"] is False
