import json

from app.ai.contracts import AnalysisSegment
from app.ai.service import VideoAnalysisService, _segments_for_store
from app.crypto import CookieCipher
from app.db import Database
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
    monkeypatch.setattr(settings, "ai_video_provider", "mock")
    settings.ensure_dirs()
    db = Database(data_dir / "app.sqlite")
    db.init()
    return AppStore(db, CookieCipher(data_dir / "secret.key"))


def test_mock_video_analysis_persists_segments(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    source_path = settings.source_dir / "source.mp4"
    source_path.write_bytes(b"video")
    source = store.create_source(
        "upload",
        source_path,
        original_filename="source.mp4",
        duration_sec=60,
        width=1920,
        height=1080,
        status="ready",
    )

    analysis = VideoAnalysisService(store).run_analysis(source["id"])

    assert analysis["status"] == "succeeded"
    assert json.loads(analysis["usage_json"]) == {"mock": True}
    segments = store.list_ai_segments(source_id=source["id"])
    assert len(segments) == 2
    assert segments[0]["title"] == "Mock short #1"
    assert store.get_source(source["id"])["status"] == "analyzed"


def test_failed_video_analysis_is_recorded_without_losing_source(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    source_path = settings.source_dir / "source.mp4"
    source_path.write_bytes(b"video")
    source = store.create_source(
        "upload",
        source_path,
        original_filename="source.mp4",
        duration_sec=60,
        status="ready",
    )

    analysis = VideoAnalysisService(store).run_analysis(source["id"], provider="polza")

    assert analysis["status"] == "failed"
    assert "not implemented" in analysis["error"]
    assert store.list_ai_segments(source_id=source["id"]) == []
    assert store.get_source(source["id"])["status"] == "ready"


def test_artemox_is_accepted_as_analysis_provider(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    source_path = settings.source_dir / "source.mp4"
    source_path.write_bytes(b"video")
    source = store.create_source(
        "upload",
        source_path,
        original_filename="source.mp4",
        duration_sec=60,
        status="ready",
    )

    analysis = VideoAnalysisService(store).run_analysis(source["id"], provider="artemox")

    assert analysis["provider"] == "artemox"
    assert analysis["model"] == settings.artemox_video_model
    assert analysis["status"] == "failed"
    assert "requires a URL source" in analysis["error"]


def test_ai_segments_are_clamped_to_source_duration():
    segments = _segments_for_store(
        [
            AnalysisSegment(
                start_sec=90,
                end_sec=130,
                title="Overshoot",
                description="",
                score=0.9,
                category="general",
                color="#64748B",
                reason="",
            )
        ],
        source_duration=99.267,
    )

    assert segments[0]["start_sec"] == 90
    assert segments[0]["end_sec"] == 99.267
