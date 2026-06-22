import subprocess

import pytest

from app.ai.service import VideoAnalysisService
from app.crypto import CookieCipher
from app.db import Database
from app.ingest import probe_media
from app.render import ClipRenderService
from app.settings import settings
from app.store import AppStore
from app.worker import JobWorker


def test_mock_pipeline_ingest_analyze_render_post(tmp_path, monkeypatch):
    _require_ffmpeg()
    monkeypatch.setattr(settings, "provider_mode", "mock")
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
    store = AppStore(Database(data_dir / "app.sqlite"), CookieCipher(data_dir / "secret.key"))
    store.db.init()
    account = store.upsert_account("tiktok", "pipeline", "sessionid=abc; tt-target-idc=useast2a")
    source_path = settings.source_dir / "pipeline-source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x180:rate=25",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=800:sample_rate=44100",
            "-t",
            "7",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            str(source_path),
        ],
        check=True,
    )
    metadata = probe_media(source_path)
    source = store.create_source(
        "upload",
        source_path,
        original_filename=source_path.name,
        duration_sec=metadata.duration_sec,
        width=metadata.width,
        height=metadata.height,
        fps=metadata.fps,
        status="ready",
    )
    analysis = VideoAnalysisService(store).run_analysis(source["id"], provider="mock")
    segment = store.list_ai_segments(analysis_id=analysis["id"])[0]
    preset = store.create_ffmpeg_preset(
        "Pipeline vertical",
        output_width=180,
        output_height=320,
        fps=25,
        scale_mode="cover",
        extra={"crf": 30},
    )
    subtitle_profile = store.create_subtitle_profile("Pipeline subtitles", provider="mock", font_size=30)

    clip = ClipRenderService(store).render_segment(
        segment["id"],
        ffmpeg_preset_id=preset["id"],
        subtitle_profile_id=subtitle_profile["id"],
    )
    job = store.create_clip_post_job(clip["id"], "", "", [account["id"]], "public", True)

    assert clip["status"] == "succeeded"
    assert job["clip_id"] == clip["id"]
    assert JobWorker(store).process_once()
    done = store.get_job(job["id"])
    assert done["status"] == "succeeded"
    assert done["targets"][0]["remote_id"].startswith("mock-tiktok")


def _require_ffmpeg() -> None:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        pytest.skip("ffmpeg is not available")
