import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ai.service import VideoAnalysisService
from app.crypto import CookieCipher
from app.db import Database
from app.ingest import SourceIngestor
from app.render import ClipRenderService
from app.settings import settings
from app.store import AppStore
from app.worker import JobWorker


def _client(tmp_path, monkeypatch) -> tuple[TestClient, AppStore]:
    data_dir = tmp_path / "data"
    monkeypatch.setattr(settings, "auth_enabled", False)
    monkeypatch.setattr(settings, "provider_mode", "mock")
    monkeypatch.setattr(settings, "ai_video_provider", "mock")
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

    import app.main as app_main

    monkeypatch.setattr(app_main, "store", store)
    monkeypatch.setattr(app_main, "source_ingestor", SourceIngestor(store))
    monkeypatch.setattr(app_main, "video_analysis_service", VideoAnalysisService(store))
    monkeypatch.setattr(app_main, "clip_render_service", ClipRenderService(store))
    monkeypatch.setattr(app_main, "worker", JobWorker(store))
    return TestClient(app_main.app), store


def test_api_preset_accepts_audio_track_mix_settings(tmp_path, monkeypatch):
    client, _store = _client(tmp_path, monkeypatch)

    response = client.post(
        "/api/ffmpeg-presets",
        json={
            "label": "API mix preset",
            "output_width": 180,
            "output_height": 320,
            "fps": 25,
            "scale_mode": "cover",
            "audio_mix_mode": "mix",
            "audio_primary_stream": 0,
            "audio_primary_volume": 0.7,
            "audio_secondary_stream": 1,
            "audio_secondary_volume": 1.25,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["audio_mix_mode"] == "mix"
    assert payload["audio_primary_volume"] == 0.7
    assert payload["audio_secondary_stream"] == 1


def test_api_ingest_analyze_render_and_queue_clip_post(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, store = _client(tmp_path, monkeypatch)
    source_fixture = tmp_path / "api-source.mp4"
    _make_test_video(source_fixture)

    preset = client.post(
        "/api/ffmpeg-presets",
        json={
            "label": "API vertical",
            "output_width": 180,
            "output_height": 320,
            "fps": 25,
            "scale_mode": "cover",
            "extra": {"crf": 30},
        },
    ).json()
    subtitle_profile = client.post(
        "/api/subtitle-profiles",
        json={"label": "API mock subtitles", "provider": "mock", "font_size": 30},
    ).json()
    account = client.post(
        "/api/accounts",
        json={
            "platform": "tiktok",
            "label": "api-target",
            "cookie": "sessionid=abc; tt-target-idc=useast2a",
        },
    ).json()
    with source_fixture.open("rb") as fileobj:
        source_response = client.post(
            "/api/sources",
            files={"file": ("api-source.mp4", fileobj, "video/mp4")},
        )
    assert source_response.status_code == 200
    source = source_response.json()
    assert source["status"] == "ready"

    analysis_response = client.post(f"/api/sources/{source['id']}/analyze", json={"provider": "mock"})
    assert analysis_response.status_code == 200
    source_detail = client.get(f"/api/sources/{source['id']}").json()
    clip_plan = source_detail["clip_plans"][0]
    segment = clip_plan["segments"][0]

    timecode_response = client.patch(
        f"/api/segments/{segment['id']}/timecodes",
        json={"start_sec": 0, "end_sec": 5},
    )
    assert timecode_response.status_code == 200

    clip_response = client.post(
        f"/api/clip-plans/{clip_plan['id']}/render",
        json={
            "ffmpeg_preset_id": preset["id"],
            "subtitle_profile_id": subtitle_profile["id"],
        },
    )
    assert clip_response.status_code == 200
    clip = clip_response.json()
    assert clip["status"] == "succeeded"
    assert clip["clip_plan_id"] == clip_plan["id"]
    assert clip["subtitle_track_id"]

    post_response = client.post(
        f"/api/clips/{clip['id']}/posts",
        json={"targets": [account["id"]], "privacy": "public"},
    )
    assert post_response.status_code == 200
    job = post_response.json()
    assert job["clip_id"] == clip["id"]
    assert job["source_path"] == clip["output_path"]
    assert JobWorker(store).process_once()
    assert store.get_job(job["id"])["status"] == "succeeded"


def test_api_can_render_montage_from_multiple_segments(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, store = _client(tmp_path, monkeypatch)
    source_fixture = tmp_path / "api-montage-source.mp4"
    _make_test_video(source_fixture)
    with source_fixture.open("rb") as fileobj:
        source = client.post(
            "/api/sources",
            files={"file": ("api-montage-source.mp4", fileobj, "video/mp4")},
        ).raise_for_status().json()
    analysis = store.create_ai_analysis(source["id"], "mock", status="succeeded")
    first = store.create_ai_segment(analysis["id"], {"start_sec": 0, "end_sec": 5, "title": "First"})
    second = store.create_ai_segment(analysis["id"], {"start_sec": 1, "end_sec": 6, "title": "Second"})
    preset = client.post(
        "/api/ffmpeg-presets",
        json={
            "label": "API montage preset",
            "output_width": 180,
            "output_height": 320,
            "fps": 25,
            "scale_mode": "cover",
            "extra": {"crf": 30},
        },
    ).raise_for_status().json()

    response = client.post(
        "/api/montages",
        json={
            "segment_ids": [first["id"], second["id"]],
            "ffmpeg_preset_id": preset["id"],
            "title": "API montage",
        },
    )

    assert response.status_code == 200
    clip = response.json()
    assert clip["status"] == "succeeded"
    assert clip["segment_id"] is None
    assert clip["title"] == "API montage"


def _make_test_video(path: Path) -> None:
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
            "sine=frequency=600:sample_rate=44100",
            "-t",
            "7",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
    )


def _require_ffmpeg() -> None:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        pytest.skip("ffmpeg is not available")
