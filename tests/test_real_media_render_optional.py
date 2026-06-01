import os
import json
import subprocess
from pathlib import Path

import pytest

from app.crypto import CookieCipher
from app.db import Database
from app.ingest import probe_media
from app.render import ClipRenderService
from app.settings import settings
from app.store import AppStore


@pytest.mark.skipif(
    not os.getenv("VVF_RUN_REAL_MEDIA_TESTS"),
    reason="set VVF_RUN_REAL_MEDIA_TESTS=1 to render from tests data media",
)
def test_real_apex_video_render_smoke(tmp_path, monkeypatch):
    source_fixture = Path(
        os.getenv(
            "VVF_REAL_RENDER_SOURCE",
            Path(__file__).resolve().parents[1] / "tests data" / "Apex tests video 1.mp4",
        )
    )
    if not source_fixture.exists():
        pytest.skip("real media fixture is not available")
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        pytest.skip("ffmpeg is not available")

    data_dir = tmp_path / "data"
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "upload_dir", data_dir / "uploads")
    monkeypatch.setattr(settings, "source_dir", source_fixture.parent)
    monkeypatch.setattr(settings, "clip_dir", data_dir / "clips")
    monkeypatch.setattr(settings, "banner_dir", data_dir / "banners")
    monkeypatch.setattr(settings, "subtitle_dir", data_dir / "subtitles")
    monkeypatch.setattr(settings, "tmp_dir", data_dir / "tmp")
    monkeypatch.setattr(settings, "runtime_dir", data_dir / "runtime")
    monkeypatch.setattr(settings, "log_dir", data_dir / "logs")
    settings.ensure_dirs()
    db = Database(data_dir / "app.sqlite")
    db.init()
    store = AppStore(db, CookieCipher(data_dir / "secret.key"))
    metadata = probe_media(source_fixture)
    source = store.create_source(
        "upload",
        source_fixture,
        original_filename=source_fixture.name,
        duration_sec=metadata.duration_sec,
        width=metadata.width,
        height=metadata.height,
        fps=metadata.fps,
        status="ready",
    )
    analysis = store.create_ai_analysis(source["id"], "mock", status="succeeded")
    segment = store.create_ai_segment(
        analysis["id"],
        {
            "start_sec": 0,
            "end_sec": 6,
            "title": "Real Apex smoke",
            "score": 0.9,
            "category": "gameplay",
            "color": "#2563EB",
            "reason": "Real media render smoke",
        },
    )
    preset = store.create_ffmpeg_preset(
        "Real media tiny vertical",
        output_width=180,
        output_height=320,
        fps=30,
        scale_mode="cover",
        audio_mix_mode="mix",
        audio_primary_stream=0,
        audio_primary_volume=0.8,
        audio_secondary_stream=1,
        audio_secondary_volume=1.0,
        extra={"crf": 32},
    )
    subtitle_profile = store.create_subtitle_profile(
        "Real mock subtitles",
        provider="mock",
        font_size=30,
        max_words_per_line=2,
        margin_v=48,
    )

    clip = ClipRenderService(store).render_segment(
        segment["id"],
        ffmpeg_preset_id=preset["id"],
        subtitle_profile_id=subtitle_profile["id"],
    )

    assert clip["status"] == "succeeded"
    assert clip["width"] == 180
    assert clip["height"] == 320
    assert clip["size_bytes"] > 0
    assert clip["subtitle_track_id"]
    assert store.get_subtitle_track(clip["subtitle_track_id"])["status"] == "succeeded"
    audio_probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=index,channels,codec_name",
            "-of",
            "json",
            clip["output_path"],
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    audio_streams = json.loads(audio_probe.stdout)["streams"]
    assert len(audio_streams) == 1
    assert audio_streams[0]["channels"] == 2
