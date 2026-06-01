import json

import pytest

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
    settings.ensure_dirs()
    db = Database(data_dir / "app.sqlite")
    db.init()
    return AppStore(db, CookieCipher(data_dir / "secret.key"))


def test_database_init_creates_pipeline_tables_and_job_clip_id(tmp_path):
    db = Database(tmp_path / "app.sqlite")
    db.init()
    tables = {
        row["name"]
        for row in db.query_all(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert {
        "sources",
        "ai_analyses",
        "ai_segments",
        "ffmpeg_presets",
        "banners",
        "subtitle_profiles",
        "subtitle_tracks",
        "clips",
    }.issubset(tables)
    job_columns = {row["name"] for row in db.query_all("PRAGMA table_info(jobs)")}
    assert "clip_id" in job_columns


def test_pipeline_store_crud_and_segment_validation(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    source_path = settings.source_dir / "source.mp4"
    source_path.write_bytes(b"video")

    source = store.create_source(
        "upload",
        source_path,
        original_filename="source.mp4",
        sha256="abc",
        size_bytes=5,
        duration_sec=120,
        width=1920,
        height=1080,
        fps=30,
        metadata={"codec": "h264"},
        status="ready",
    )
    assert source["status"] == "ready"
    assert json.loads(source["metadata_json"]) == {"codec": "h264"}

    analysis = store.create_ai_analysis(
        source["id"],
        "mock",
        model="mock-video",
        prompt_version="v1",
        request={"prompt": "find shorts"},
    )
    store.mark_ai_analysis_running(analysis["id"])
    analysis = store.finish_ai_analysis(
        analysis["id"],
        "succeeded",
        response={"segments": []},
        usage={"tokens": 12},
    )
    assert analysis["status"] == "succeeded"
    assert json.loads(analysis["usage_json"]) == {"tokens": 12}

    segment = store.create_ai_segment(
        analysis["id"],
        {
            "start_sec": 10,
            "end_sec": 25,
            "title": "Best moment",
            "description": "Clip description",
            "score": 1.4,
            "category": "insight",
            "color": "not-a-color",
            "reason": "Works standalone",
        },
    )
    assert segment["score"] == 1
    assert segment["color"] == "#2563EB"

    with pytest.raises(ValueError):
        store.create_ai_segment(
            analysis["id"],
            {"start_sec": 118, "end_sec": 130, "title": "Too long for source"},
        )
    assert len(store.list_ai_segments(source_id=source["id"])) == 1

    banner_path = settings.banner_dir / "banner.webm"
    banner_path.write_bytes(b"webm")
    banner = store.create_banner(
        "Lower third",
        banner_path,
        original_filename="banner.webm",
        mime_type="video/webm",
        width=1080,
        height=320,
        position="bottom",
    )
    banner = store.update_banner(banner["id"], opacity=0.5)
    assert banner["opacity"] == 0.5

    preset = store.create_ffmpeg_preset(
        "Vertical blur",
        scale_mode="blur_background",
        crop_anchor="center",
        banner_id=banner["id"],
        audio_mix_mode="mix",
        audio_primary_stream=0,
        audio_primary_volume=0.75,
        audio_secondary_stream=1,
        audio_secondary_volume=1.25,
        extra={"crf": 20},
    )
    preset = store.update_ffmpeg_preset(preset["id"], crop_anchor="top")
    assert preset["crop_anchor"] == "top"
    assert preset["audio_mix_mode"] == "mix"
    assert preset["audio_primary_volume"] == 0.75
    assert preset["audio_secondary_stream"] == 1

    profile = store.create_subtitle_profile(
        "Karaoke mock",
        provider="mock",
        language="ru",
        uppercase=True,
        active_word_color="#22C55E",
    )
    profile = store.update_subtitle_profile(profile["id"], max_words_per_line=3)
    assert profile["uppercase"] is True
    assert profile["max_words_per_line"] == 3

    clip = store.create_clip(
        source["id"],
        segment_id=segment["id"],
        ffmpeg_preset_id=preset["id"],
        subtitle_profile_id=profile["id"],
    )
    assert clip["title"] == "Best moment"

    output_path = settings.clip_dir / "clip.mp4"
    output_path.write_bytes(b"clip")
    clip = store.update_clip(
        clip["id"],
        status="succeeded",
        output_path=output_path,
        duration_sec=15,
        width=1080,
        height=1920,
        size_bytes=4,
    )
    assert clip["status"] == "succeeded"
    assert clip["width"] == 1080

    ass_path = settings.subtitle_dir / "clip.ass"
    track = store.create_subtitle_track(
        clip["id"],
        "mock",
        subtitle_profile_id=profile["id"],
        status="succeeded",
        transcript={"words": [{"word": "hi", "start": 0, "end": 1}]},
        ass_path=ass_path,
    )
    clip = store.update_clip(clip["id"], subtitle_track_id=track["id"])
    assert clip["subtitle_track_id"] == track["id"]

    listed_source = store.list_sources()[0]
    assert listed_source["analyses_count"] == 1
    assert listed_source["clips_count"] == 1
    source_detail = store.get_source(source["id"], include_related=True)
    assert len(source_detail["analyses"]) == 1
    assert len(source_detail["segments"]) == 1
    assert len(source_detail["clips"]) == 1

    disposable = store.create_ffmpeg_preset("Disposable")
    store.delete_ffmpeg_preset(disposable["id"])
    with pytest.raises(KeyError):
        store.get_ffmpeg_preset(disposable["id"])
