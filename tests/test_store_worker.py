import json

from app.crypto import CookieCipher
from app.db import Database
from app.settings import settings
from app.store import AppStore
from app.worker import JobWorker


def test_job_lifecycle_with_mock_provider(tmp_path, monkeypatch):
    monkeypatch.setattr("app.settings.settings.provider_mode", "mock")
    monkeypatch.setattr("app.settings.settings.upload_dir", tmp_path / "uploads")
    monkeypatch.setattr("app.settings.settings.runtime_dir", tmp_path / "runtime")
    (tmp_path / "uploads").mkdir()
    db = Database(tmp_path / "app.sqlite")
    db.init()
    store = AppStore(db, CookieCipher(tmp_path / "secret.key"))
    account = store.upsert_account(
        "tiktok",
        "test",
        "sessionid=abc; tt-target-idc=useast2a",
        "http://user:pass@proxy.example:8080",
    )
    assert account["proxy_configured"] is True
    assert account["proxy_display"] == "http://***@proxy.example:8080"
    assert "encrypted_proxy_url" not in account
    assert store.get_account_proxy_url(account["id"]) == "http://user:pass@proxy.example:8080"
    source = tmp_path / "video.mp4"
    source.write_bytes(b"fake-video")
    with source.open("rb") as f:
        job = store.create_job(f, "video.mp4", "Title", "Desc", [account["id"]], "public", True)
    assert job["status"] == "queued"
    assert JobWorker(store).process_once()
    done = store.get_job(job["id"])
    assert done["status"] == "succeeded"
    assert done["targets"][0]["remote_id"].startswith("mock-tiktok")
    response = json.loads(done["targets"][0]["response_json"])
    assert response["proxy_configured"] is True


def test_account_proxy_validation(tmp_path):
    db = Database(tmp_path / "app.sqlite")
    db.init()
    store = AppStore(db, CookieCipher(tmp_path / "secret.key"))
    try:
        store.upsert_account("tiktok", "test", "sessionid=abc", "ftp://proxy.example:21")
    except ValueError as exc:
        assert "proxy_url" in str(exc)
    else:
        raise AssertionError("invalid proxy URL was accepted")


def test_clip_post_job_reuses_rendered_clip_file(tmp_path, monkeypatch):
    monkeypatch.setattr("app.settings.settings.provider_mode", "mock")
    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")
    monkeypatch.setattr(settings, "source_dir", tmp_path / "sources")
    monkeypatch.setattr(settings, "clip_dir", tmp_path / "clips")
    monkeypatch.setattr(settings, "runtime_dir", tmp_path / "runtime")
    settings.ensure_dirs()
    db = Database(tmp_path / "app.sqlite")
    db.init()
    store = AppStore(db, CookieCipher(tmp_path / "secret.key"))
    account = store.upsert_account("youtube", "test", "SID=a; HSID=b; SSID=c; APISID=d; SAPISID=e")
    source_path = settings.source_dir / "source.mp4"
    source_path.write_bytes(b"source")
    source = store.create_source("upload", source_path, original_filename="source.mp4", duration_sec=10, status="ready")
    clip = store.create_clip(source["id"], title="Rendered title", description="Rendered desc")
    clip_path = settings.clip_dir / "clip.mp4"
    clip_path.write_bytes(b"rendered clip bytes")
    clip = store.finish_clip_render(
        clip["id"],
        status="succeeded",
        output_path=clip_path,
        preview_path=clip_path,
        duration_sec=10,
        width=1080,
        height=1920,
        size_bytes=clip_path.stat().st_size,
    )

    job = store.create_clip_post_job(clip["id"], "", "", [account["id"]], "unlisted", False)

    assert job["clip_id"] == clip["id"]
    assert job["source_path"] == str(clip_path)
    assert job["source_size_bytes"] == clip_path.stat().st_size
    assert job["title"] == "Rendered title"
    assert job["targets"][0]["platform"] == "youtube"
    assert JobWorker(store).process_once()
    assert store.get_job(job["id"])["status"] == "succeeded"


def test_failed_clip_cannot_be_posted(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "source_dir", tmp_path / "sources")
    monkeypatch.setattr(settings, "clip_dir", tmp_path / "clips")
    settings.ensure_dirs()
    db = Database(tmp_path / "app.sqlite")
    db.init()
    store = AppStore(db, CookieCipher(tmp_path / "secret.key"))
    account = store.upsert_account("youtube", "test", "SID=a; HSID=b; SSID=c; APISID=d; SAPISID=e")
    source_path = settings.source_dir / "source.mp4"
    source_path.write_bytes(b"source")
    source = store.create_source("upload", source_path, original_filename="source.mp4", duration_sec=10, status="ready")
    clip = store.create_clip(source["id"], status="failed")

    try:
        store.create_clip_post_job(clip["id"], "Title", "", [account["id"]], "public", True)
    except ValueError as exc:
        assert "succeeded" in str(exc)
    else:
        raise AssertionError("failed clip was accepted for posting")


def test_worker_persists_provider_exception_as_failed_target(tmp_path, monkeypatch):
    monkeypatch.setattr("app.settings.settings.provider_mode", "mock")
    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")
    monkeypatch.setattr(settings, "runtime_dir", tmp_path / "runtime")
    settings.ensure_dirs()
    db = Database(tmp_path / "app.sqlite")
    db.init()
    store = AppStore(db, CookieCipher(tmp_path / "secret.key"))
    account = store.upsert_account("tiktok", "test", "sessionid=abc; tt-target-idc=useast2a")
    source = tmp_path / "video.mp4"
    source.write_bytes(b"fake-video")
    with source.open("rb") as fileobj:
        job = store.create_job(fileobj, "video.mp4", "Title", "Desc", [account["id"]], "public", True)

    class BrokenProvider:
        def upload(self, **kwargs):
            raise RuntimeError("provider exploded")

    monkeypatch.setattr("app.worker.get_provider", lambda platform: BrokenProvider())

    assert JobWorker(store).process_once()
    done = store.get_job(job["id"])
    assert done["status"] == "failed"
    assert done["targets"][0]["status"] == "failed"
    assert "provider exploded" in done["targets"][0]["error"]
