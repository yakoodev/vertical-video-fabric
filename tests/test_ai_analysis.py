import json
from pathlib import Path

from app.ai.contracts import AnalysisResult, AnalysisSegment
from app.ai.service import VideoAnalysisService, _segments_for_store
from app.analysis_preprocess import build_analysis_preprocess_args, normalize_analysis_preprocessing
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
    clip_plans = store.list_clip_plans(source_id=source["id"])
    assert len(clip_plans) == 2
    assert clip_plans[0]["segments"][0]["id"] == segments[0]["id"]
    assert store.get_source(source["id"])["status"] == "analyzed"


def test_video_analysis_uses_saved_defaults(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    store.set_app_setting("default_ai_provider", "mock")
    store.set_app_setting("default_ai_model", "mock-apex-default")
    store.set_app_setting("analysis_prompt", "Apex Legends context prompt")
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

    assert analysis["provider"] == "mock"
    assert analysis["model"] == "mock-apex-default"
    assert json.loads(analysis["request_json"])["prompt"] == "Apex Legends context prompt"


def test_video_analysis_preprocesses_source_for_analyzer(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    source_path = settings.source_dir / "source.mp4"
    prepared_path = settings.runtime_dir / "analysis.mp4"
    source_path.write_bytes(b"video")
    prepared_path.write_bytes(b"small")
    source = store.create_source(
        "upload",
        source_path,
        original_filename="source.mp4",
        duration_sec=60,
        width=1920,
        height=1080,
        status="ready",
    )
    seen = {}

    def fake_prepare(source_dict, preprocessing):
        seen["preprocessing"] = preprocessing
        prepared = dict(source_dict)
        prepared["local_path"] = str(prepared_path)
        return prepared, {"enabled": True, "output_path": str(prepared_path)}

    class FakeAnalyzer:
        provider = "mock"

        def analyze(self, source_dict, prompt, model):
            seen["local_path"] = source_dict["local_path"]
            return AnalysisResult(
                segments=[
                    AnalysisSegment(
                        start_sec=0,
                        end_sec=10,
                        title="Prepared",
                        description="",
                    )
                ],
                usage={"mock": True},
            )

    monkeypatch.setattr("app.ai.service.prepare_source_for_analysis", fake_prepare)
    monkeypatch.setattr("app.ai.service.get_video_analyzer", lambda provider: FakeAnalyzer())

    analysis = VideoAnalysisService(store).run_analysis(
        source["id"],
        provider="mock",
        preprocessing={
            "enabled": True,
            "merge_audio_for_analysis": True,
            "max_dimension": 720,
            "target_fps": 12,
        },
    )

    assert analysis["status"] == "succeeded"
    assert seen["local_path"] == str(prepared_path)
    assert seen["preprocessing"]["enabled"] is True
    assert seen["preprocessing"]["merge_audio_for_analysis"] is True
    usage = json.loads(analysis["usage_json"])
    assert usage["analysis_preprocessing"]["output_path"] == str(prepared_path)


def test_analysis_preprocess_args_merge_audio_and_reduce_video():
    options = normalize_analysis_preprocessing(
        {
            "enabled": True,
            "merge_audio_for_analysis": True,
            "max_dimension": 720,
            "target_fps": 12,
            "video_crf": 32,
        }
    )

    args = build_analysis_preprocess_args(Path("input.mp4"), Path("out.mp4"), options, audio_streams=2)

    assert args[0] == "ffmpeg"
    assert "-filter_complex" in args
    assert "amix=inputs=2" in args[args.index("-filter_complex") + 1]
    assert "-vf" in args
    assert "fps=12" in args[args.index("-vf") + 1]
    assert "-crf" in args
    assert args[args.index("-crf") + 1] == "32"


def test_analysis_preprocess_legacy_merge_audio_enables_preprocessing():
    options = normalize_analysis_preprocessing({"merge_audio_for_analysis": True})

    assert options["enabled"] is True
    assert options["merge_audio_for_analysis"] is True


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
