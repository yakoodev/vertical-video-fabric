import json

from app.crypto import CookieCipher
from app.db import Database
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
