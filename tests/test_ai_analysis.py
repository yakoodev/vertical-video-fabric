import json
import struct
from pathlib import Path

from app.ai.contracts import AnalysisClip, AnalysisResult, AnalysisSegment
from app.ai.service import (
    VideoAnalysisService,
    _energy_boundaries_from_pcm,
    _highlights_clip_cap,
    _segments_for_store,
)
from app.analysis_preprocess import build_analysis_preprocess_args, normalize_analysis_preprocessing
from app.crypto import CookieCipher
from app.default_prompts import ANIME_ANALYSIS_PROMPT
from app.db import Database
from app.settings import settings
from app.store import AppStore


# Narrative (recap-first) post-processing is now opted into by prompts that ask
# for the structured recap clip. Tests that exercise that path pass this prompt
# explicitly; the Anime preset deliberately omits it and runs in highlights mode.
_NARRATIVE_PROMPT = (
    "Analyze this TV series episode for vertical publishing. clips[0] must be an "
    "Episode Story Recap with 4 to 6 ordered segments covering the main plot."
)


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


def test_energy_boundaries_detect_low_energy_valley():
    sample_rate = 100
    samples = [12000] * sample_rate + [200] * sample_rate + [12000] * sample_rate
    pcm = b"".join(struct.pack("<h", sample) for sample in samples)

    boundaries = _energy_boundaries_from_pcm(pcm, sample_rate=sample_rate, window_sec=0.2, source_duration=3)

    assert any(1.0 <= boundary <= 2.0 for boundary in boundaries)


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


def test_video_analysis_uses_anime_prompt_for_smotvibe_without_override(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    source_path = settings.source_dir / "smotvibe-source.mp4"
    source_path.write_bytes(b"video")
    source = store.create_source(
        "smotvibe_url",
        source_path,
        original_url="https://smotvibe.sbs/series/6802576/",
        original_filename="smotvibe-6802576.mp4",
        duration_sec=120,
        width=1920,
        height=1080,
        status="ready",
    )
    segment = AnalysisSegment(start_sec=10, end_sec=20, title="Anime beat")
    seen = {}

    class FakeAnalyzer:
        provider = "gemini"

        def analyze(self, source_dict, prompt, model):
            seen["prompt"] = prompt
            return AnalysisResult(
                segments=[segment],
                clips=[AnalysisClip(title="Anime beat", segments=[segment], score=0.8)],
                usage={"fake": True},
            )

    monkeypatch.setattr("app.ai.service.get_video_analyzer", lambda provider: FakeAnalyzer())

    analysis = VideoAnalysisService(store).run_analysis(source["id"], provider="gemini", model="fake")

    assert analysis["status"] == "succeeded"
    # The model gets the anime prompt augmented with the focus-track instruction;
    # the stored request keeps the user's clean prompt.
    assert seen["prompt"].startswith(ANIME_ANALYSIS_PROMPT)
    assert "focus" in seen["prompt"].lower()
    assert json.loads(analysis["request_json"])["prompt"] == ANIME_ANALYSIS_PROMPT


def test_video_analysis_coalesces_adjacent_single_segment_clips_for_real_providers(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    source_path = settings.source_dir / "source.mp4"
    source_path.write_bytes(b"video")
    source = store.create_source(
        "upload",
        source_path,
        original_filename="source.mp4",
        duration_sec=120,
        width=1920,
        height=1080,
        status="ready",
    )

    first = AnalysisSegment(start_sec=10, end_sec=20, title="Setup", description="Setup beat")
    second = AnalysisSegment(start_sec=28, end_sec=40, title="Payoff", description="Payoff beat")

    class FakeAnalyzer:
        provider = "gemini"

        def analyze(self, source_dict, prompt, model):
            return AnalysisResult(
                segments=[first, second],
                clips=[
                    AnalysisClip(title="Setup", description="Setup beat", segments=[first], score=0.8),
                    AnalysisClip(title="Payoff", description="Payoff beat", segments=[second], score=0.9),
                ],
                usage={"fake": True},
            )

    monkeypatch.setattr("app.ai.service.get_video_analyzer", lambda provider: FakeAnalyzer())

    analysis = VideoAnalysisService(store).run_analysis(source["id"], provider="gemini", model="fake")

    assert analysis["status"] == "succeeded"
    clip_plans = store.list_clip_plans(source_id=source["id"])
    assert len(clip_plans) == 1
    assert len(clip_plans[0]["segments"]) == 2
    assert clip_plans[0]["segments"][0]["title"] == "Setup"
    assert clip_plans[0]["segments"][1]["title"] == "Payoff"


def test_video_analysis_does_not_coalesce_adjacent_single_segment_clips_over_budget(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    source_path = settings.source_dir / "source.mp4"
    source_path.write_bytes(b"video")
    source = store.create_source(
        "upload",
        source_path,
        original_filename="source.mp4",
        duration_sec=700,
        width=1920,
        height=1080,
        status="ready",
    )

    first = AnalysisSegment(start_sec=324, end_sec=405, title="First long beat")
    second = AnalysisSegment(start_sec=413, end_sec=550, title="Second long beat")

    class FakeAnalyzer:
        provider = "gemini"

        def analyze(self, source_dict, prompt, model):
            return AnalysisResult(
                segments=[first, second],
                clips=[
                    AnalysisClip(title="First long beat", segments=[first], score=0.75),
                    AnalysisClip(title="Second long beat", segments=[second], score=0.85),
                ],
                usage={"fake": True},
            )

    monkeypatch.setattr("app.ai.service.get_video_analyzer", lambda provider: FakeAnalyzer())

    analysis = VideoAnalysisService(store).run_analysis(source["id"], provider="gemini", model="fake")

    assert analysis["status"] == "succeeded"
    clip_plans = store.list_clip_plans(source_id=source["id"])
    assert len(clip_plans) == 2
    assert [plan["title"] for plan in clip_plans] == ["First long beat", "Second long beat"]


def test_video_analysis_expands_and_merges_tiny_anime_segments(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    source_path = settings.source_dir / "smotvibe-source.mp4"
    source_path.write_bytes(b"video")
    source = store.create_source(
        "smotvibe_url",
        source_path,
        original_url="https://smotvibe.sbs/series/6802576/",
        original_filename="smotvibe-6802576.mp4",
        duration_sec=200,
        width=1920,
        height=1080,
        status="ready",
    )
    first = AnalysisSegment(start_sec=100, end_sec=110, title="Line setup")
    second = AnalysisSegment(start_sec=111, end_sec=120, title="Line payoff")

    class FakeAnalyzer:
        provider = "gemini"

        def analyze(self, source_dict, prompt, model):
            return AnalysisResult(
                segments=[first, second],
                clips=[AnalysisClip(title="Full exchange", segments=[first, second], score=0.9)],
                usage={"fake": True},
            )

    monkeypatch.setattr("app.ai.service.get_video_analyzer", lambda provider: FakeAnalyzer())

    analysis = VideoAnalysisService(store).run_analysis(source["id"], provider="gemini", model="fake")

    assert analysis["status"] == "succeeded"
    clip_plans = store.list_clip_plans(source_id=source["id"])
    assert len(clip_plans) == 1
    assert len(clip_plans[0]["segments"]) == 1
    segment = clip_plans[0]["segments"][0]
    assert segment["start_sec"] < 100
    assert segment["end_sec"] > 120
    assert segment["end_sec"] - segment["start_sec"] >= 20


def test_video_analysis_keeps_merged_anime_segments_under_store_limit(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    source_path = settings.source_dir / "smotvibe-source.mp4"
    source_path.write_bytes(b"video")
    source = store.create_source(
        "smotvibe_url",
        source_path,
        original_url="https://smotvibe.sbs/series/6802576/",
        original_filename="smotvibe-6802576.mp4",
        duration_sec=240,
        width=1920,
        height=1080,
        status="ready",
    )
    first = AnalysisSegment(start_sec=0, end_sec=55, title="Long setup")
    second = AnalysisSegment(start_sec=66, end_sec=120, title="Long payoff")

    class FakeAnalyzer:
        provider = "gemini"

        def analyze(self, source_dict, prompt, model):
            return AnalysisResult(
                segments=[first, second],
                clips=[AnalysisClip(title="Full story beat", segments=[first, second], score=0.9)],
                usage={"fake": True},
            )

    monkeypatch.setattr("app.ai.service.get_video_analyzer", lambda provider: FakeAnalyzer())

    analysis = VideoAnalysisService(store).run_analysis(source["id"], provider="gemini", model="fake")

    assert analysis["status"] == "succeeded"
    clip_plans = store.list_clip_plans(source_id=source["id"])
    assert len(clip_plans) == 1
    assert len(clip_plans[0]["segments"]) == 2
    assert all(
        segment["end_sec"] - segment["start_sec"] <= 85
        for segment in clip_plans[0]["segments"]
    )


def test_video_analysis_merges_overlapping_padded_segments(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    source_path = settings.source_dir / "smotvibe-source.mp4"
    source_path.write_bytes(b"video")
    source = store.create_source(
        "smotvibe_url",
        source_path,
        original_url="https://smotvibe.sbs/series/6802576/",
        original_filename="smotvibe-6802576.mp4",
        duration_sec=1400,
        width=1920,
        height=1080,
        status="ready",
    )
    recap = [
        AnalysisSegment(start_sec=80, end_sec=110, title="Premise"),
        AnalysisSegment(start_sec=300, end_sec=330, title="Incident"),
        AnalysisSegment(start_sec=820, end_sec=850, title="Reveal"),
        AnalysisSegment(start_sec=1190, end_sec=1220, title="Crisis"),
    ]
    first = AnalysisSegment(start_sec=1191, end_sec=1215, title="Setup")
    second = AnalysisSegment(start_sec=1215, end_sec=1276, title="Payoff")

    class FakeAnalyzer:
        provider = "gemini"

        def analyze(self, source_dict, prompt, model):
            return AnalysisResult(
                segments=[*recap, first, second],
                clips=[
                    AnalysisClip(title="Episode Story Recap", segments=recap, score=0.95),
                    AnalysisClip(title="Overlapping arc", segments=[first, second], score=0.9),
                ],
                usage={"fake": True},
            )

    monkeypatch.setattr("app.ai.service.get_video_analyzer", lambda provider: FakeAnalyzer())

    analysis = VideoAnalysisService(store).run_analysis(source["id"], provider="gemini", model="fake")

    assert analysis["status"] == "succeeded"
    clip_plans = store.list_clip_plans(source_id=source["id"])
    overlapping = next(plan for plan in clip_plans if plan["title"] == "Overlapping arc")
    assert len(overlapping["segments"]) == 2
    assert overlapping["segments"][0]["start_sec"] <= 1187
    assert overlapping["segments"][0]["end_sec"] <= overlapping["segments"][1]["start_sec"]
    assert overlapping["segments"][1]["end_sec"] >= 1280


def test_video_analysis_retries_narrative_oversized_segments(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    source_path = settings.source_dir / "smotvibe-source.mp4"
    source_path.write_bytes(b"video")
    source = store.create_source(
        "smotvibe_url",
        source_path,
        original_url="https://smotvibe.sbs/series/6802576/",
        original_filename="smotvibe-6802576.mp4",
        duration_sec=1500,
        width=1920,
        height=1080,
        status="ready",
    )
    calls = {"count": 0, "retry_prompt": ""}

    class FakeAnalyzer:
        provider = "gemini"

        def analyze(self, source_dict, prompt, model):
            calls["count"] += 1
            if calls["count"] == 1:
                oversized = AnalysisSegment(start_sec=100, end_sec=260, title="One huge scene")
                return AnalysisResult(
                    segments=[oversized],
                    clips=[AnalysisClip(title="Huge chapter", segments=[oversized], score=0.9)],
                    usage={"attempt": 1},
                )
            calls["retry_prompt"] = prompt
            recap_segments = [
                AnalysisSegment(start_sec=60, end_sec=92, title="Premise"),
                AnalysisSegment(start_sec=260, end_sec=294, title="Inciting incident"),
                AnalysisSegment(start_sec=520, end_sec=558, title="Reveal"),
                AnalysisSegment(start_sec=980, end_sec=1016, title="Consequence"),
            ]
            standalone = AnalysisSegment(start_sec=900, end_sec=942, title="Standalone arc")
            return AnalysisResult(
                segments=[*recap_segments, standalone],
                clips=[
                    AnalysisClip(title="Episode Story Recap", segments=recap_segments, score=0.9),
                    AnalysisClip(title="Standalone arc", segments=[standalone], score=0.85),
                ],
                usage={"attempt": 2},
            )

    monkeypatch.setattr("app.ai.service.get_video_analyzer", lambda provider: FakeAnalyzer())

    analysis = VideoAnalysisService(store).run_analysis(source["id"], provider="gemini", model="fake", prompt=_NARRATIVE_PROMPT)

    assert analysis["status"] == "succeeded"
    assert calls["count"] == 2
    assert "too long" in json.loads(analysis["usage_json"])["analysis_retry"]["reason"]
    assert "Every individual segment must be 12 to 75 seconds" in calls["retry_prompt"]
    clip_plans = store.list_clip_plans(source_id=source["id"])
    assert [plan["title"] for plan in clip_plans] == ["Episode Story Recap", "Standalone arc"]
    assert [len(plan["segments"]) for plan in clip_plans] == [4, 1]
    assert all(
        segment["end_sec"] - segment["start_sec"] <= 85
        for plan in clip_plans
        for segment in plan["segments"]
    )


def test_video_analysis_does_not_auto_coalesce_narrative_single_segment_clips(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    source_path = settings.source_dir / "smotvibe-source.mp4"
    source_path.write_bytes(b"video")
    source = store.create_source(
        "smotvibe_url",
        source_path,
        original_url="https://smotvibe.sbs/series/6802576/",
        original_filename="smotvibe-6802576.mp4",
        duration_sec=800,
        width=1920,
        height=1080,
        status="ready",
    )
    first = AnalysisSegment(start_sec=100, end_sec=150, title="First standalone arc")
    second = AnalysisSegment(start_sec=170, end_sec=220, title="Second standalone arc")

    class FakeAnalyzer:
        provider = "gemini"

        def analyze(self, source_dict, prompt, model):
            return AnalysisResult(
                segments=[first, second],
                clips=[
                    AnalysisClip(title="First standalone arc", segments=[first], score=0.8),
                    AnalysisClip(title="Second standalone arc", segments=[second], score=0.85),
                ],
                usage={"fake": True},
            )

    monkeypatch.setattr("app.ai.service.get_video_analyzer", lambda provider: FakeAnalyzer())

    analysis = VideoAnalysisService(store).run_analysis(source["id"], provider="gemini", model="fake")

    assert analysis["status"] == "succeeded"
    clip_plans = store.list_clip_plans(source_id=source["id"])
    assert [plan["title"] for plan in clip_plans] == ["First standalone arc", "Second standalone arc"]
    assert [len(plan["segments"]) for plan in clip_plans] == [1, 1]


def test_video_analysis_retries_narrative_missing_episode_recap(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    source_path = settings.source_dir / "smotvibe-source.mp4"
    source_path.write_bytes(b"video")
    source = store.create_source(
        "smotvibe_url",
        source_path,
        original_url="https://smotvibe.sbs/series/6802576/",
        original_filename="smotvibe-6802576.mp4",
        duration_sec=1500,
        width=1920,
        height=1080,
        status="ready",
    )
    calls = {"count": 0}

    class FakeAnalyzer:
        provider = "gemini"

        def analyze(self, source_dict, prompt, model):
            calls["count"] += 1
            if calls["count"] == 1:
                local = AnalysisSegment(start_sec=700, end_sec=760, title="Local moment")
                return AnalysisResult(
                    segments=[local],
                    clips=[AnalysisClip(title="Local moment", segments=[local], score=0.8)],
                    usage={"attempt": 1},
                )
            recap_segments = [
                AnalysisSegment(start_sec=60, end_sec=92, title="Premise"),
                AnalysisSegment(start_sec=320, end_sec=354, title="Incident"),
                AnalysisSegment(start_sec=620, end_sec=655, title="Reveal"),
                AnalysisSegment(start_sec=980, end_sec=1016, title="Consequence"),
            ]
            supporting = AnalysisSegment(start_sec=700, end_sec=745, title="Supporting arc")
            return AnalysisResult(
                segments=[*recap_segments, supporting],
                clips=[
                    AnalysisClip(title="Episode Story Recap", segments=recap_segments, score=0.9),
                    AnalysisClip(title="Supporting arc", segments=[supporting], score=0.85),
                ],
                usage={"attempt": 2},
            )

    monkeypatch.setattr("app.ai.service.get_video_analyzer", lambda provider: FakeAnalyzer())

    analysis = VideoAnalysisService(store).run_analysis(source["id"], provider="gemini", model="fake", prompt=_NARRATIVE_PROMPT)

    assert analysis["status"] == "succeeded"
    assert calls["count"] == 2
    retry_reason = json.loads(analysis["usage_json"])["analysis_retry"]["reason"]
    assert "invalid Episode Story Recap" in retry_reason
    assert "only 1 segment" in retry_reason
    clip_plans = store.list_clip_plans(source_id=source["id"])
    assert [plan["title"] for plan in clip_plans] == ["Episode Story Recap", "Supporting arc"]


def test_video_analysis_retries_recap_that_misses_early_setup(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    source_path = settings.source_dir / "smotvibe-source.mp4"
    source_path.write_bytes(b"video")
    source = store.create_source(
        "smotvibe_url",
        source_path,
        original_url="https://smotvibe.sbs/series/6802576/",
        original_filename="smotvibe-6802576.mp4",
        duration_sec=1400,
        width=1920,
        height=1080,
        status="ready",
    )
    calls = {"count": 0}

    class FakeAnalyzer:
        provider = "gemini"

        def analyze(self, source_dict, prompt, model):
            calls["count"] += 1
            if calls["count"] == 1:
                late_recap = [
                    AnalysisSegment(start_sec=640, end_sec=665, title="Late setup"),
                    AnalysisSegment(start_sec=815, end_sec=850, title="Reveal"),
                    AnalysisSegment(start_sec=1085, end_sec=1115, title="Spell"),
                    AnalysisSegment(start_sec=1203, end_sec=1235, title="Disaster"),
                    AnalysisSegment(start_sec=1360, end_sec=1395, title="New path"),
                ]
                return AnalysisResult(
                    segments=late_recap,
                    clips=[AnalysisClip(title="Episode Story Recap", segments=late_recap, score=0.9)],
                    usage={"attempt": 1},
                )
            good_recap = [
                AnalysisSegment(start_sec=90, end_sec=120, title="Premise"),
                AnalysisSegment(start_sec=310, end_sec=340, title="Inciting incident"),
                AnalysisSegment(start_sec=815, end_sec=845, title="Reveal"),
                AnalysisSegment(start_sec=1203, end_sec=1235, title="Disaster"),
                AnalysisSegment(start_sec=1360, end_sec=1395, title="New path"),
            ]
            return AnalysisResult(
                segments=good_recap,
                clips=[AnalysisClip(title="Episode Story Recap", segments=good_recap, score=0.95)],
                usage={"attempt": 2},
            )

    monkeypatch.setattr("app.ai.service.get_video_analyzer", lambda provider: FakeAnalyzer())

    analysis = VideoAnalysisService(store).run_analysis(source["id"], provider="gemini", model="fake", prompt=_NARRATIVE_PROMPT)

    assert analysis["status"] == "succeeded"
    assert calls["count"] == 2
    retry_reason = json.loads(analysis["usage_json"])["analysis_retry"]["reason"]
    assert "starts too late" in retry_reason
    assert "490.0s" in retry_reason
    [plan] = store.list_clip_plans(source_id=source["id"])
    assert plan["segments"][0]["start_sec"] < source["duration_sec"] * 0.35


def test_video_analysis_accepts_recap_setup_inside_first_third_after_420_seconds(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    source_path = settings.source_dir / "smotvibe-source.mp4"
    source_path.write_bytes(b"video")
    source = store.create_source(
        "smotvibe_url",
        source_path,
        original_url="https://smotvibe.sbs/series/6802576/",
        original_filename="smotvibe-6802576.mp4",
        duration_sec=1424,
        width=1920,
        height=1080,
        status="ready",
    )
    recap_segments = [
        AnalysisSegment(start_sec=456, end_sec=485, title="First-third setup"),
        AnalysisSegment(start_sec=635, end_sec=663, title="Inciting reveal"),
        AnalysisSegment(start_sec=815, end_sec=845, title="Rule reveal"),
        AnalysisSegment(start_sec=1218, end_sec=1245, title="Consequence"),
        AnalysisSegment(start_sec=1362, end_sec=1399, title="New direction"),
    ]

    class FakeAnalyzer:
        provider = "gemini"

        def analyze(self, source_dict, prompt, model):
            return AnalysisResult(
                segments=recap_segments,
                clips=[AnalysisClip(title="Episode Story Recap", segments=recap_segments, score=0.95)],
                usage={"fake": True},
            )

    monkeypatch.setattr("app.ai.service.get_video_analyzer", lambda provider: FakeAnalyzer())

    analysis = VideoAnalysisService(store).run_analysis(source["id"], provider="gemini", model="fake")

    assert analysis["status"] == "succeeded"
    [plan] = store.list_clip_plans(source_id=source["id"])
    assert plan["title"] == "Episode Story Recap"
    assert plan["segments"][0]["start_sec"] < source["duration_sec"] * 0.35


def test_video_analysis_retries_narrative_timeline_digest_that_is_too_broad(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    source_path = settings.source_dir / "smotvibe-source.mp4"
    source_path.write_bytes(b"video")
    source = store.create_source(
        "smotvibe_url",
        source_path,
        original_url="https://smotvibe.sbs/series/6802576/",
        original_filename="smotvibe-6802576.mp4",
        duration_sec=1200,
        width=1920,
        height=1080,
        status="ready",
    )
    calls = {"count": 0}

    class FakeAnalyzer:
        provider = "gemini"

        def analyze(self, source_dict, prompt, model):
            calls["count"] += 1
            if calls["count"] == 1:
                recap_segments = [
                    AnalysisSegment(start_sec=60, end_sec=96, title="Recap premise"),
                    AnalysisSegment(start_sec=260, end_sec=296, title="Recap incident"),
                    AnalysisSegment(start_sec=520, end_sec=556, title="Recap reveal"),
                    AnalysisSegment(start_sec=820, end_sec=856, title="Recap consequence"),
                ]
                clips = [AnalysisClip(title="Episode Story Recap", segments=recap_segments, score=0.9)]
                for index, start in enumerate((220, 420, 620), start=1):
                    segments = [
                        AnalysisSegment(start_sec=start, end_sec=start + 50, title=f"Chapter {index} setup"),
                        AnalysisSegment(start_sec=start + 60, end_sec=start + 110, title=f"Chapter {index} payoff"),
                    ]
                    clips.append(AnalysisClip(title=f"Timeline chapter {index}", segments=segments, score=0.8))
                return AnalysisResult(
                    segments=[segment for clip in clips for segment in clip.segments],
                    clips=clips,
                    usage={"attempt": 1},
                )
            recap_segments = [
                AnalysisSegment(start_sec=100, end_sec=132, title="Best recap premise"),
                AnalysisSegment(start_sec=350, end_sec=386, title="Best recap reveal"),
                AnalysisSegment(start_sec=700, end_sec=738, title="Best recap crisis"),
                AnalysisSegment(start_sec=980, end_sec=1016, title="Best recap consequence"),
            ]
            standalone = AnalysisSegment(start_sec=700, end_sec=752, title="Second arc")
            return AnalysisResult(
                segments=[*recap_segments, standalone],
                clips=[
                    AnalysisClip(title="Episode Story Recap", segments=recap_segments, score=0.9),
                    AnalysisClip(title="Second standalone arc", segments=[standalone], score=0.85),
                ],
                usage={"attempt": 2},
            )

    monkeypatch.setattr("app.ai.service.get_video_analyzer", lambda provider: FakeAnalyzer())

    analysis = VideoAnalysisService(store).run_analysis(source["id"], provider="gemini", model="fake", prompt=_NARRATIVE_PROMPT)

    assert analysis["status"] == "succeeded"
    assert calls["count"] == 2
    retry_reason = json.loads(analysis["usage_json"])["analysis_retry"]["reason"]
    assert "selected timeline is too broad" in retry_reason
    clip_plans = store.list_clip_plans(source_id=source["id"])
    assert [plan["title"] for plan in clip_plans] == ["Episode Story Recap", "Second standalone arc"]


def test_video_analysis_fits_narrative_recap_that_is_too_long_to_render_budget(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    source_path = settings.source_dir / "smotvibe-source.mp4"
    source_path.write_bytes(b"video")
    source = store.create_source(
        "smotvibe_url",
        source_path,
        original_url="https://smotvibe.sbs/series/6802576/",
        original_filename="smotvibe-6802576.mp4",
        duration_sec=1200,
        width=1920,
        height=1080,
        status="ready",
    )
    calls = {"count": 0}

    class FakeAnalyzer:
        provider = "gemini"

        def analyze(self, source_dict, prompt, model):
            calls["count"] += 1
            long_recap = [
                AnalysisSegment(start_sec=80, end_sec=115, title="Long premise"),
                AnalysisSegment(start_sec=240, end_sec=275, title="Long incident"),
                AnalysisSegment(start_sec=420, end_sec=455, title="Long reveal"),
                AnalysisSegment(start_sec=640, end_sec=675, title="Long choice"),
                AnalysisSegment(start_sec=1020, end_sec=1055, title="Long consequence"),
            ]
            return AnalysisResult(
                segments=long_recap,
                clips=[AnalysisClip(title="Episode Story Recap", segments=long_recap, score=0.95)],
                usage={"attempt": 1},
            )

    monkeypatch.setattr("app.ai.service.get_video_analyzer", lambda provider: FakeAnalyzer())

    analysis = VideoAnalysisService(store).run_analysis(source["id"], provider="gemini", model="fake", prompt=_NARRATIVE_PROMPT)

    assert analysis["status"] == "succeeded"
    assert calls["count"] == 1
    clip_plans = store.list_clip_plans(source_id=source["id"])
    assert [plan["title"] for plan in clip_plans] == ["Episode Story Recap"]
    assert len(clip_plans[0]["segments"]) == 4
    assert sum(segment["end_sec"] - segment["start_sec"] for segment in clip_plans[0]["segments"]) <= 165


def test_video_analysis_compacts_recap_padding_before_rejecting(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    source_path = settings.source_dir / "smotvibe-source.mp4"
    source_path.write_bytes(b"video")
    source = store.create_source(
        "smotvibe_url",
        source_path,
        original_url="https://smotvibe.sbs/series/6802576/",
        original_filename="smotvibe-6802576.mp4",
        duration_sec=1500,
        width=1920,
        height=1080,
        status="ready",
    )
    recap_segments = [
        AnalysisSegment(start_sec=140, end_sec=166, title="Premise"),
        AnalysisSegment(start_sec=815, end_sec=865, title="Reveal"),
        AnalysisSegment(start_sec=976, end_sec=1012, title="Choice"),
        AnalysisSegment(start_sec=1199, end_sec=1240, title="Crisis"),
        AnalysisSegment(start_sec=1361, end_sec=1399, title="New path"),
    ]

    class FakeAnalyzer:
        provider = "gemini"

        def analyze(self, source_dict, prompt, model):
            return AnalysisResult(
                segments=recap_segments,
                clips=[AnalysisClip(title="Episode Story Recap", segments=recap_segments, score=0.95)],
                usage={"fake": True},
            )

    monkeypatch.setattr("app.ai.service.get_video_analyzer", lambda provider: FakeAnalyzer())

    analysis = VideoAnalysisService(store).run_analysis(source["id"], provider="gemini", model="fake", prompt=_NARRATIVE_PROMPT)

    assert analysis["status"] == "succeeded"
    [plan] = store.list_clip_plans(source_id=source["id"])
    assert plan["title"] == "Episode Story Recap"
    assert sum(segment["end_sec"] - segment["start_sec"] for segment in plan["segments"]) <= 165


def test_video_analysis_keeps_valid_recap_when_retry_still_has_too_many_optional_clips(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    source_path = settings.source_dir / "smotvibe-source.mp4"
    source_path.write_bytes(b"video")
    source = store.create_source(
        "smotvibe_url",
        source_path,
        original_url="https://smotvibe.sbs/series/6802576/",
        original_filename="smotvibe-6802576.mp4",
        duration_sec=1200,
        width=1920,
        height=1080,
        status="ready",
    )
    calls = {"count": 0}

    def broad_result(attempt: int) -> AnalysisResult:
        recap_segments = [
            AnalysisSegment(start_sec=60, end_sec=96, title="Premise"),
            AnalysisSegment(start_sec=300, end_sec=336, title="Incident"),
            AnalysisSegment(start_sec=600, end_sec=636, title="Reveal"),
            AnalysisSegment(start_sec=980, end_sec=1016, title="Consequence"),
        ]
        clips = [AnalysisClip(title="Episode Story Recap", segments=recap_segments, score=0.95)]
        for index, start in enumerate((140, 430, 730), start=1):
            segment = AnalysisSegment(start_sec=start, end_sec=start + 70, title=f"Optional arc {index}")
            clips.append(AnalysisClip(title=f"Optional arc {index}", segments=[segment], score=0.9 - index / 100))
        return AnalysisResult(
            segments=[segment for clip in clips for segment in clip.segments],
            clips=clips,
            usage={"attempt": attempt},
        )

    class FakeAnalyzer:
        provider = "gemini"

        def analyze(self, source_dict, prompt, model):
            calls["count"] += 1
            return broad_result(calls["count"])

    monkeypatch.setattr("app.ai.service.get_video_analyzer", lambda provider: FakeAnalyzer())

    analysis = VideoAnalysisService(store).run_analysis(source["id"], provider="gemini", model="fake", prompt=_NARRATIVE_PROMPT)

    assert analysis["status"] == "succeeded"
    assert calls["count"] == 2
    assert "selected timeline is too broad" in json.loads(analysis["usage_json"])["analysis_retry"]["reason"]
    clip_plans = store.list_clip_plans(source_id=source["id"])
    assert [plan["title"] for plan in clip_plans] == [
        "Episode Story Recap",
        "Optional arc 1",
    ]


def test_video_analysis_retries_narrative_invalid_timestamps(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    source_path = settings.source_dir / "smotvibe-source.mp4"
    source_path.write_bytes(b"video")
    source = store.create_source(
        "smotvibe_url",
        source_path,
        original_url="https://smotvibe.sbs/series/6802576/",
        original_filename="smotvibe-6802576.mp4",
        duration_sec=1200,
        width=1920,
        height=1080,
        status="ready",
    )
    calls = {"count": 0, "retry_prompt": ""}

    class FakeAnalyzer:
        provider = "gemini"

        def analyze(self, source_dict, prompt, model):
            calls["count"] += 1
            if calls["count"] == 1:
                invalid = AnalysisSegment(start_sec=1300, end_sec=1310, title="Outside source")
                return AnalysisResult(
                    segments=[invalid],
                    clips=[AnalysisClip(title="Bad chapter", segments=[invalid], score=0.9)],
                    usage={"attempt": 1},
                )
            calls["retry_prompt"] = prompt
            recap_segments = [
                AnalysisSegment(start_sec=60, end_sec=92, title="Beginning"),
                AnalysisSegment(start_sec=320, end_sec=354, title="Inciting incident"),
                AnalysisSegment(start_sec=620, end_sec=655, title="Reveal"),
                AnalysisSegment(start_sec=980, end_sec=1015, title="End"),
            ]
            supporting = AnalysisSegment(start_sec=700, end_sec=745, title="Supporting arc")
            return AnalysisResult(
                segments=[*recap_segments, supporting],
                clips=[
                    AnalysisClip(title="Episode Story Recap", segments=recap_segments, score=0.9),
                    AnalysisClip(title="Supporting arc", segments=[supporting], score=0.85),
                ],
                usage={"attempt": 2},
            )

    monkeypatch.setattr("app.ai.service.get_video_analyzer", lambda provider: FakeAnalyzer())

    analysis = VideoAnalysisService(store).run_analysis(source["id"], provider="gemini", model="fake", prompt=_NARRATIVE_PROMPT)

    assert analysis["status"] == "succeeded"
    assert calls["count"] == 2
    assert "Correction pass" in calls["retry_prompt"]
    usage = json.loads(analysis["usage_json"])
    assert usage["attempt"] == 2
    assert "outside the uploaded source duration" in usage["analysis_retry"]["reason"]
    clip_plans = store.list_clip_plans(source_id=source["id"])
    assert [plan["title"] for plan in clip_plans] == ["Episode Story Recap", "Supporting arc"]


def test_video_analysis_fails_when_narrative_retry_still_has_invalid_timestamps(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    source_path = settings.source_dir / "smotvibe-source.mp4"
    source_path.write_bytes(b"video")
    source = store.create_source(
        "smotvibe_url",
        source_path,
        original_url="https://smotvibe.sbs/series/6802576/",
        original_filename="smotvibe-6802576.mp4",
        duration_sec=1200,
        width=1920,
        height=1080,
        status="ready",
    )
    calls = {"count": 0}

    class FakeAnalyzer:
        provider = "gemini"

        def analyze(self, source_dict, prompt, model):
            calls["count"] += 1
            segment = AnalysisSegment(start_sec=1300, end_sec=1310, title=f"Outside source {calls['count']}")
            return AnalysisResult(
                segments=[segment],
                clips=[AnalysisClip(title="Bad chapter", segments=[segment], score=0.9)],
                response={"attempt": calls["count"]},
                usage={"attempt": calls["count"]},
            )

    monkeypatch.setattr("app.ai.service.get_video_analyzer", lambda provider: FakeAnalyzer())

    analysis = VideoAnalysisService(store).run_analysis(source["id"], provider="gemini", model="fake", prompt=_NARRATIVE_PROMPT)

    assert analysis["status"] == "failed"
    assert calls["count"] == 2
    assert "invalid narrative analysis" in analysis["error"]
    assert json.loads(analysis["response_json"])["attempt"] == 2
    usage = json.loads(analysis["usage_json"])
    assert usage["attempt"] == 2
    assert "outside the uploaded source duration" in usage["analysis_retry"]["reason"]
    assert store.list_clip_plans(source_id=source["id"]) == []
    assert store.list_ai_segments(source_id=source["id"]) == []


def test_video_analysis_fails_when_narrative_retry_still_has_oversized_segments(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    source_path = settings.source_dir / "smotvibe-source.mp4"
    source_path.write_bytes(b"video")
    source = store.create_source(
        "smotvibe_url",
        source_path,
        original_url="https://smotvibe.sbs/series/6802576/",
        original_filename="smotvibe-6802576.mp4",
        duration_sec=1200,
        width=1920,
        height=1080,
        status="ready",
    )
    calls = {"count": 0}

    class FakeAnalyzer:
        provider = "gemini"

        def analyze(self, source_dict, prompt, model):
            calls["count"] += 1
            segment = AnalysisSegment(start_sec=100, end_sec=240, title=f"Huge scene {calls['count']}")
            return AnalysisResult(
                segments=[segment],
                clips=[AnalysisClip(title="Bad chapter", segments=[segment], score=0.9)],
                response={"attempt": calls["count"]},
                usage={"attempt": calls["count"]},
            )

    monkeypatch.setattr("app.ai.service.get_video_analyzer", lambda provider: FakeAnalyzer())

    analysis = VideoAnalysisService(store).run_analysis(source["id"], provider="gemini", model="fake", prompt=_NARRATIVE_PROMPT)

    assert analysis["status"] == "failed"
    assert calls["count"] == 2
    assert "longer than 75 seconds" in analysis["error"]
    assert json.loads(analysis["response_json"])["attempt"] == 2
    usage = json.loads(analysis["usage_json"])
    assert usage["attempt"] == 2
    assert "too long" in usage["analysis_retry"]["reason"]
    assert store.list_clip_plans(source_id=source["id"]) == []
    assert store.list_ai_segments(source_id=source["id"]) == []


def test_video_analysis_keeps_distant_single_segment_clips_separate(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    source_path = settings.source_dir / "source.mp4"
    source_path.write_bytes(b"video")
    source = store.create_source(
        "upload",
        source_path,
        original_filename="source.mp4",
        duration_sec=240,
        width=1920,
        height=1080,
        status="ready",
    )

    first = AnalysisSegment(start_sec=10, end_sec=20, title="First", description="")
    second = AnalysisSegment(start_sec=90, end_sec=105, title="Second", description="")

    class FakeAnalyzer:
        provider = "gemini"

        def analyze(self, source_dict, prompt, model):
            return AnalysisResult(
                segments=[first, second],
                clips=[
                    AnalysisClip(title="First", segments=[first], score=0.8),
                    AnalysisClip(title="Second", segments=[second], score=0.9),
                ],
                usage={"fake": True},
            )

    monkeypatch.setattr("app.ai.service.get_video_analyzer", lambda provider: FakeAnalyzer())

    analysis = VideoAnalysisService(store).run_analysis(source["id"], provider="gemini", model="fake")

    assert analysis["status"] == "succeeded"
    clip_plans = store.list_clip_plans(source_id=source["id"])
    assert len(clip_plans) == 2
    assert [len(plan["segments"]) for plan in clip_plans] == [1, 1]


def test_video_analysis_keeps_previous_generated_clip_plans(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    source_path = settings.source_dir / "source.mp4"
    source_path.write_bytes(b"video")
    source = store.create_source(
        "upload",
        source_path,
        original_filename="source.mp4",
        duration_sec=120,
        width=1920,
        height=1080,
        status="ready",
    )
    first = AnalysisSegment(start_sec=10, end_sec=20, title="Old plan")
    second = AnalysisSegment(start_sec=50, end_sec=60, title="New plan")
    calls = {"count": 0}

    class FakeAnalyzer:
        provider = "gemini"

        def analyze(self, source_dict, prompt, model):
            calls["count"] += 1
            segment = first if calls["count"] == 1 else second
            return AnalysisResult(
                segments=[segment],
                clips=[AnalysisClip(title=segment.title, segments=[segment], score=0.8)],
                usage={"fake": True},
            )

    monkeypatch.setattr("app.ai.service.get_video_analyzer", lambda provider: FakeAnalyzer())

    first_analysis = VideoAnalysisService(store).run_analysis(source["id"], provider="gemini", model="fake")
    assert first_analysis["status"] == "succeeded"
    assert [plan["title"] for plan in store.list_clip_plans(source_id=source["id"])] == ["Old plan"]

    second_analysis = VideoAnalysisService(store).run_analysis(source["id"], provider="gemini", model="fake")

    # A second run ADDS candidates instead of replacing them: re-analysing with
    # another preset/model is how you get more options, not a reset. Deleting the
    # analysis is what removes its plans.
    assert second_analysis["status"] == "succeeded"
    clip_plans = store.list_clip_plans(source_id=source["id"])
    assert sorted(plan["title"] for plan in clip_plans) == ["New plan", "Old plan"]
    assert {plan["analysis_id"] for plan in clip_plans} == {first_analysis["id"], second_analysis["id"]}


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

    class _BoomAnalyzer:
        provider = "polza"

        def analyze(self, source, prompt, model):
            raise RuntimeError("analyzer exploded")

    monkeypatch.setattr("app.ai.service.get_video_analyzer", lambda provider: _BoomAnalyzer())

    analysis = VideoAnalysisService(store).run_analysis(source["id"], provider="polza")

    assert analysis["status"] == "failed"
    assert "analyzer exploded" in analysis["error"]
    assert store.list_ai_segments(source_id=source["id"]) == []
    assert store.get_source(source["id"])["status"] == "ready"


def test_failed_video_analysis_cleans_partial_generated_outputs(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    source_path = settings.source_dir / "source.mp4"
    source_path.write_bytes(b"video")
    source = store.create_source(
        "upload",
        source_path,
        original_filename="source.mp4",
        duration_sec=180,
        width=1920,
        height=1080,
        status="ready",
    )
    first = AnalysisSegment(start_sec=10, end_sec=20, title="First")
    second = AnalysisSegment(start_sec=100, end_sec=110, title="Second")

    class FakeAnalyzer:
        provider = "gemini"

        def analyze(self, source_dict, prompt, model):
            return AnalysisResult(
                segments=[first, second],
                clips=[
                    AnalysisClip(title="First", segments=[first], score=0.8),
                    AnalysisClip(title="Second", segments=[second], score=0.9),
                ],
                response={"raw": "provider-response"},
                usage={"fake": True},
            )

    original_create_clip_plan = store.create_clip_plan
    calls = {"count": 0}

    def flaky_create_clip_plan(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 2:
            raise ValueError("persist exploded")
        return original_create_clip_plan(*args, **kwargs)

    monkeypatch.setattr("app.ai.service.get_video_analyzer", lambda provider: FakeAnalyzer())
    monkeypatch.setattr(store, "create_clip_plan", flaky_create_clip_plan)

    analysis = VideoAnalysisService(store).run_analysis(source["id"], provider="gemini", model="fake")

    assert analysis["status"] == "failed"
    assert analysis["error"] == "persist exploded"
    assert json.loads(analysis["response_json"])["raw"] == "provider-response"
    assert store.list_clip_plans(source_id=source["id"]) == []
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


def test_ai_segments_expand_to_nearby_audio_boundaries():
    segments = _segments_for_store(
        [
            AnalysisSegment(
                start_sec=10,
                end_sec=20,
                title="Dialogue beat",
                description="",
                score=0.9,
                category="story",
                color="#64748B",
                reason="",
            )
        ],
        source_duration=60,
        min_duration_sec=5,
        max_duration_sec=30,
        audio_boundaries=[0, 8.75, 22.5, 60],
        boundary_search_sec=5,
        boundary_max_extra_sec=5,
    )

    assert segments[0]["start_sec"] == 8.75
    assert segments[0]["end_sec"] == 22.5


def test_video_analysis_anime_returns_standalone_highlights_without_forcing_recap(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    source_path = settings.source_dir / "smotvibe-source.mp4"
    source_path.write_bytes(b"video")
    source = store.create_source(
        "smotvibe_url",
        source_path,
        original_url="https://smotvibe.sbs/series/6802576/",
        original_filename="smotvibe-6802576.mp4",
        duration_sec=1500,
        width=1920,
        height=1080,
        status="ready",
    )
    moments = [
        AnalysisSegment(start_sec=120, end_sec=150, title="Признание"),
        AnalysisSegment(start_sec=560, end_sec=592, title="Финальный удар"),
        AnalysisSegment(start_sec=900, end_sec=930, title="Шутка с панчлайном"),
        AnalysisSegment(start_sec=1300, end_sec=1332, title="Клиффхэнгер"),
    ]
    calls = {"count": 0}

    class FakeAnalyzer:
        provider = "gemini"

        def analyze(self, source_dict, prompt, model):
            calls["count"] += 1
            return AnalysisResult(
                segments=moments,
                clips=[AnalysisClip(title=m.title, segments=[m], score=0.9) for m in moments],
                usage={"fake": True},
            )

    monkeypatch.setattr("app.ai.service.get_video_analyzer", lambda provider: FakeAnalyzer())

    analysis = VideoAnalysisService(store).run_analysis(source["id"], provider="gemini", model="fake")

    # No recap is forced on anime: the standalone clips are accepted as-is, with
    # no correction retry and no rejection for a missing Episode Story Recap.
    assert analysis["status"] == "succeeded"
    assert calls["count"] == 1
    assert "analysis_retry" not in json.loads(analysis["usage_json"])
    clip_plans = store.list_clip_plans(source_id=source["id"])
    assert len(clip_plans) == 4
    assert all(len(plan["segments"]) == 1 for plan in clip_plans)
    assert not any("recap" in plan["title"].lower() for plan in clip_plans)


def test_video_analysis_anime_caps_highlight_clip_count(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    source_path = settings.source_dir / "smotvibe-source.mp4"
    source_path.write_bytes(b"video")
    source = store.create_source(
        "smotvibe_url",
        source_path,
        original_url="https://smotvibe.sbs/series/6802576/",
        original_filename="smotvibe-6802576.mp4",
        duration_sec=1800,
        width=1920,
        height=1080,
        status="ready",
    )
    moments = [
        AnalysisSegment(start_sec=50 + index * 100, end_sec=80 + index * 100, title=f"Момент {index}")
        for index in range(16)
    ]

    class FakeAnalyzer:
        provider = "gemini"

        def analyze(self, source_dict, prompt, model):
            return AnalysisResult(
                segments=moments,
                clips=[AnalysisClip(title=m.title, segments=[m], score=0.9) for m in moments],
                usage={"fake": True},
            )

    monkeypatch.setattr("app.ai.service.get_video_analyzer", lambda provider: FakeAnalyzer())

    analysis = VideoAnalysisService(store).run_analysis(source["id"], provider="gemini", model="fake")

    assert analysis["status"] == "succeeded"
    clip_plans = store.list_clip_plans(source_id=source["id"])
    # Highlights cap scales with episode length so coverage isn't truncated; for a
    # 30-minute source it keeps more than the base 6 but still caps the flood.
    expected = _highlights_clip_cap(1800)
    assert expected > 6
    assert len(clip_plans) == expected


def test_action_analyzer_builds_clips_from_detected_regions(monkeypatch):
    from app.ai import action_detect
    from app.ai.registry import get_video_analyzer
    from app.ai.action_detect import ActionVideoAnalyzer

    assert isinstance(get_video_analyzer("action"), ActionVideoAnalyzer)
    monkeypatch.setattr(action_detect.Path, "exists", lambda self: True)
    monkeypatch.setattr(
        action_detect, "detect_action_regions",
        lambda path, duration, **kw: [(0.9, 20.0, 48.0), (0.8, 2583.0, 2638.0)],
    )
    result = ActionVideoAnalyzer().analyze({"local_path": "/x.mp4", "duration_sec": 3863}, "", "")
    assert len(result.clips) == 2
    assert result.clips[0].segments[0].start_sec == 20.0
    assert result.clips[0].category == "Экшн"
    assert result.usage["provider"] == "action"
