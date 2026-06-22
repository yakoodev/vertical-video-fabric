import json

import pytest

from app.crypto import CookieCipher
from app.db import Database
from app.default_prompts import ANIME_ANALYSIS_PROMPT, ANIME_SUBTITLE_PROMPT, DEFAULT_PROMPT_SEED_KEY, LEGACY_DEFAULT_PROMPTS
from app.settings import settings
from app.store import AppStore


def _store(tmp_path, monkeypatch) -> AppStore:
    data_dir = tmp_path / "data"
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "upload_dir", data_dir / "uploads")
    monkeypatch.setattr(settings, "source_dir", data_dir / "sources")
    monkeypatch.setattr(settings, "clip_dir", data_dir / "clips")
    monkeypatch.setattr(settings, "banner_dir", data_dir / "banners")
    monkeypatch.setattr(settings, "audio_dir", data_dir / "audio")
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
        "clip_plans",
        "clip_plan_segments",
        "ffmpeg_presets",
        "banners",
        "subtitle_profiles",
        "subtitle_tracks",
        "clips",
        "app_settings",
        "prompt_presets",
    }.issubset(tables)
    job_columns = {row["name"] for row in db.query_all("PRAGMA table_info(jobs)")}
    assert "clip_id" in job_columns
    clip_columns = {row["name"] for row in db.query_all("PRAGMA table_info(clips)")}
    assert "clip_plan_id" in clip_columns
    subtitle_profile_columns = {row["name"] for row in db.query_all("PRAGMA table_info(subtitle_profiles)")}
    assert "timing_offset_sec" in subtitle_profile_columns


def test_database_init_seeds_default_prompt_presets_once(tmp_path):
    db = Database(tmp_path / "app.sqlite")
    db.init()

    presets = db.query_all(
        """
        SELECT task, label, prompt, is_default
        FROM prompt_presets
        ORDER BY task, label
        """
    )
    labels = {(row["task"], row["label"]) for row in presets}

    assert ("analysis", "Apex analysis") in labels
    assert ("analysis", "Anime analysis") in labels
    assert ("analysis", "Series analysis") in labels
    assert ("publishing", "Publishing metadata") in labels
    assert ("subtitle", "Apex subtitles") in labels
    assert ("subtitle", "Anime subtitles") in labels
    assert ("subtitle", "Series subtitles") in labels
    anime_prompt = next(row["prompt"] for row in presets if row["label"] == "Anime analysis")
    series_prompt = next(row["prompt"] for row in presets if row["label"] == "Series analysis")
    # Anime now targets strong standalone moments (highlights mode), so it must
    # NOT opt into the recap-first narrative path.
    assert "Episode Story Recap" not in anime_prompt
    assert "main plot recap" not in anime_prompt.lower()
    assert "standalone moments" in anime_prompt
    assert "3 to 5 clips" in anime_prompt
    assert "never longer than 75" in anime_prompt
    assert "Russian" in anime_prompt
    assert "Episode Story Recap" in series_prompt
    assert "clips[0] is mandatory" in series_prompt
    assert "main plot" in series_prompt
    assert "local hook" in series_prompt
    assert "multiple segments" in series_prompt
    assert "90 to 150 seconds" in series_prompt
    assert "Do not return any finished clip around 3 minutes" in series_prompt
    assert "never longer than 75 seconds" in series_prompt
    assert "Russian" in series_prompt
    publishing_prompt = next(row["prompt"] for row in presets if row["label"] == "Publishing metadata")
    assert "Both title and description must be in Russian" in publishing_prompt
    assert "honorifics" in next(row["prompt"] for row in presets if row["label"] == "Anime subtitles")
    assert "foreground speaker" in next(row["prompt"] for row in presets if row["label"] == "Series subtitles")
    assert sum(1 for row in presets if row["task"] == "analysis" and row["is_default"]) == 1

    count = len(presets)
    db.init()
    assert db.query_one("SELECT COUNT(*) AS count FROM prompt_presets")["count"] == count

    db.execute("DELETE FROM prompt_presets WHERE task = ? AND label = ?", ("analysis", "Anime analysis"))
    db.init()
    labels = {
        (row["task"], row["label"])
        for row in db.query_all("SELECT task, label FROM prompt_presets")
    }
    assert ("analysis", "Anime analysis") not in labels


def test_database_init_updates_only_unchanged_legacy_default_prompts(tmp_path):
    db = Database(tmp_path / "app.sqlite")
    db.init()
    db.execute("UPDATE app_settings SET value_json = ? WHERE key = ?", ('"20260611-anime-series-v1"', DEFAULT_PROMPT_SEED_KEY))
    db.execute(
        "UPDATE prompt_presets SET prompt = ? WHERE task = ? AND label = ?",
        (LEGACY_DEFAULT_PROMPTS[("analysis", "Anime analysis")], "analysis", "Anime analysis"),
    )
    db.execute(
        "UPDATE prompt_presets SET prompt = ? WHERE task = ? AND label = ?",
        ("custom series prompt", "analysis", "Series analysis"),
    )
    db.execute(
        "UPDATE prompt_presets SET prompt = ? WHERE task = ? AND label = ?",
        (
            "Transcribe this anime audio for karaoke subtitles.\n"
            "Preserve character names, honorifics, and keep word timestamps tight for karaoke highlighting.",
            "subtitle",
            "Anime subtitles",
        ),
    )
    db.execute(
        "UPDATE prompt_presets SET prompt = ? WHERE task = ? AND label = ?",
        ("custom subtitles prompt", "subtitle", "Series subtitles"),
    )

    db.init()

    anime = db.query_one(
        "SELECT prompt FROM prompt_presets WHERE task = ? AND label = ?",
        ("analysis", "Anime analysis"),
    )
    series = db.query_one(
        "SELECT prompt FROM prompt_presets WHERE task = ? AND label = ?",
        ("analysis", "Series analysis"),
    )
    anime_subtitles = db.query_one(
        "SELECT prompt FROM prompt_presets WHERE task = ? AND label = ?",
        ("subtitle", "Anime subtitles"),
    )
    series_subtitles = db.query_one(
        "SELECT prompt FROM prompt_presets WHERE task = ? AND label = ?",
        ("subtitle", "Series subtitles"),
    )

    assert anime["prompt"] == ANIME_ANALYSIS_PROMPT
    assert series["prompt"] == "custom series prompt"
    assert anime_subtitles["prompt"] == ANIME_SUBTITLE_PROMPT
    assert series_subtitles["prompt"] == "custom subtitles prompt"


def test_pipeline_store_crud_and_segment_validation(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    account = store.upsert_account("youtube", "delete-me", "SID=a; HSID=b; SSID=c; APISID=d; SAPISID=e")
    assert len(store.list_accounts()) == 1
    store.delete_account(account["id"])
    assert store.list_accounts() == []
    with pytest.raises(KeyError):
        store.get_account(account["id"])

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
    preset = store.create_prompt_preset("analysis", "Apex preset", "Find Apex moments", is_default=True)
    assert preset["is_default"] is True
    assert store.get_default_prompt_preset("analysis")["id"] == preset["id"]
    preset = store.update_prompt_preset(preset["id"], label="Apex updated", prompt="Find better Apex moments", is_default=True)
    assert preset["label"] == "Apex updated"
    failed_analysis = store.create_ai_analysis(source["id"], "gemini", model="gemini-test")
    store.mark_ai_analysis_running(failed_analysis["id"])
    failed_analysis = store.finish_ai_analysis(
        failed_analysis["id"],
        "failed",
        response={"error": "Media is too large"},
        error="Media is too large",
    )
    tasks = store.list_active_tasks()
    failed_task = next(task for task in tasks if task["kind"] == "analysis" and task["id"] == failed_analysis["id"])
    assert failed_task["status"] == "failed"
    assert failed_task["error"] == "Media is too large"

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
    segment = store.update_ai_segment_timecodes(segment["id"], 11, 26)
    assert segment["start_sec"] == 11
    assert segment["end_sec"] == 26

    clip_plan = store.create_clip_plan(
        source["id"],
        analysis["id"],
        "Planned clip",
        description="Planned description",
        segment_ids=[segment["id"]],
        score=0.8,
        category="insight",
    )
    assert clip_plan["segments"][0]["id"] == segment["id"]

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
    assert profile["timing_offset_sec"] == 0
    # Gemini profiles no longer carry the legacy +0.35s nudge: the reworked
    # timeline/ASS engine syncs honestly, so the default offset is 0.
    gemini_profile = store.create_subtitle_profile("Gemini subtitles", provider="gemini")
    assert gemini_profile["timing_offset_sec"] == 0
    assert gemini_profile["outline_width"] == 5
    assert gemini_profile["shadow"] == 1

    clip = store.create_clip(
        source["id"],
        segment_id=segment["id"],
        clip_plan_id=clip_plan["id"],
        ffmpeg_preset_id=preset["id"],
        subtitle_profile_id=profile["id"],
    )
    assert clip["title"] == "Planned clip"
    assert clip["clip_plan_id"] == clip_plan["id"]

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
    assert listed_source["analyses_count"] == 2
    assert listed_source["clip_plans_count"] == 1
    assert listed_source["clips_count"] == 1
    source_detail = store.get_source(source["id"], include_related=True)
    assert len(source_detail["analyses"]) == 2
    assert len(source_detail["segments"]) == 1
    assert len(source_detail["clip_plans"]) == 1
    assert len(source_detail["clips"]) == 1

    disposable = store.create_ffmpeg_preset("Disposable")
    store.delete_ffmpeg_preset(disposable["id"])
    with pytest.raises(KeyError):
        store.get_ffmpeg_preset(disposable["id"])


def test_delete_ai_analysis_removes_generated_plans_but_keeps_rendered_clip(tmp_path, monkeypatch):
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
        status="analyzed",
    )
    analysis = store.create_ai_analysis(source["id"], "mock", status="succeeded")
    segment = store.create_ai_segment(analysis["id"], {"start_sec": 10, "end_sec": 25, "title": "Generated"})
    plan = store.create_clip_plan(source["id"], analysis["id"], "Generated plan", segment_ids=[segment["id"]])
    clip = store.create_clip(source["id"], clip_plan_id=plan["id"], segment_id=segment["id"], status="succeeded")

    deleted = store.delete_ai_analysis(analysis["id"])

    assert deleted["id"] == analysis["id"]
    with pytest.raises(KeyError):
        store.get_ai_analysis(analysis["id"])
    assert store.list_ai_segments(source_id=source["id"]) == []
    assert store.list_clip_plans(source_id=source["id"]) == []
    kept_clip = store.get_clip(clip["id"])
    assert kept_clip["status"] == "succeeded"
    assert kept_clip["clip_plan_id"] is None
    assert kept_clip["segment_id"] is None
    assert store.get_source(source["id"])["status"] == "ready"


def test_running_ai_analysis_delete_requires_stale_state(tmp_path, monkeypatch):
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
        status="analyzing",
    )
    analysis = store.create_ai_analysis(source["id"], "mock", status="queued")
    store.mark_ai_analysis_running(analysis["id"])

    with pytest.raises(ValueError, match="running analysis cannot be deleted"):
        store.delete_ai_analysis(analysis["id"])

    store.db.execute(
        "UPDATE ai_analyses SET updated_at = datetime('now', '-3 hours') WHERE id = ?",
        (analysis["id"],),
    )
    deleted = store.delete_ai_analysis(analysis["id"])

    assert deleted["id"] == analysis["id"]
    assert deleted["status"] == "failed"
    assert deleted["error"] == "analysis timed out or was interrupted"
    assert store.get_source(source["id"])["status"] == "ready"
    with pytest.raises(KeyError):
        store.get_ai_analysis(analysis["id"])


def test_recover_interrupted_ai_analyses_marks_running_failed(tmp_path, monkeypatch):
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
        status="analyzing",
    )
    first = store.create_ai_analysis(source["id"], "mock", status="queued")
    second = store.create_ai_analysis(source["id"], "mock", status="queued")
    store.mark_ai_analysis_running(first["id"])
    store.mark_ai_analysis_running(second["id"])

    assert store.recover_interrupted_ai_analyses() == 2

    analyses = store.list_ai_analyses(source_id=source["id"])
    assert {analysis["status"] for analysis in analyses} == {"failed"}
    assert all("interrupted" in analysis["error"] for analysis in analyses)
    assert store.get_source(source["id"])["status"] == "ready"


def test_audio_tracks_and_preset_music_filters(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    track_path = settings.audio_dir / "loop.mp3"
    track_path.write_bytes(b"audio")
    track = store.create_audio_track("Lo-fi loop", track_path, duration_sec=42.5, mime_type="audio/mpeg", volume=0.4)
    assert track["label"] == "Lo-fi loop"
    assert track["duration_sec"] == 42.5
    assert track["volume"] == 0.4
    assert store.list_audio_tracks()[0]["id"] == track["id"]

    track = store.update_audio_track(track["id"], label="Chill loop", volume=0.8)
    assert track["label"] == "Chill loop"
    assert track["volume"] == 0.8

    preset = store.create_ffmpeg_preset(
        "Music + look",
        music_track_id=track["id"],
        music_volume=0.3,
        music_loop=True,
        music_fade_in_sec=1.5,
        music_fade_out_sec=2.0,
        music_duck=True,
        music_duck_amount=0.7,
        color_style="cinematic",
        color_strength=0.8,
        vignette=0.4,
        grain=0.2,
    )
    assert preset["music_track_id"] == track["id"]
    assert preset["music_volume"] == 0.3
    assert preset["music_duck"] == 1
    assert preset["color_style"] == "cinematic"
    assert preset["vignette"] == 0.4
    assert preset["grain"] == 0.2

    preset = store.update_ffmpeg_preset(preset["id"], color_style="noir", music_track_id=0)
    assert preset["color_style"] == "noir"
    assert preset["music_track_id"] is None

    with pytest.raises(ValueError):
        store.create_ffmpeg_preset("Bad style", color_style="lomo")

    store.delete_audio_track(track["id"])
    with pytest.raises(KeyError):
        store.get_audio_track(track["id"])
