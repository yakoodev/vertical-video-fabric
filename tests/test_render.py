import json
import subprocess

import pytest

from app.ai.service import VideoAnalysisService
from app.crypto import CookieCipher
from app.db import Database
from app.ingest import probe_media
from app.render import ClipRenderService, build_ffmpeg_render_args
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


def test_ffmpeg_render_command_is_argv_list(tmp_path):
    args = build_ffmpeg_render_args(
        source_path=tmp_path / "source.mp4",
        output_path=tmp_path / "clip.mp4",
        start_sec=1,
        end_sec=6,
        preset={
            "output_width": 180,
            "output_height": 320,
            "fps": 30,
            "scale_mode": "cover",
            "crop_anchor": "center",
            "video_codec": "libx264",
            "audio_codec": "aac",
            "extra_json": '{"crf": 28}',
        },
    )

    assert isinstance(args, list)
    assert args[0] == "ffmpeg"
    assert "-filter_complex" in args
    assert all(";" not in item for item in args[: args.index("-filter_complex")])
    assert args[-1].endswith("clip.mp4")


def test_ffmpeg_render_command_can_mix_two_audio_tracks(tmp_path):
    args = build_ffmpeg_render_args(
        source_path=tmp_path / "source.mp4",
        output_path=tmp_path / "clip.mp4",
        start_sec=0,
        end_sec=8,
        preset={
            "output_width": 180,
            "output_height": 320,
            "fps": 30,
            "scale_mode": "cover",
            "crop_anchor": "center",
            "video_codec": "libx264",
            "audio_codec": "aac",
            "audio_mix_mode": "mix",
            "audio_primary_stream": 0,
            "audio_primary_volume": 0.45,
            "audio_secondary_stream": 1,
            "audio_secondary_volume": 1.25,
        },
    )

    filter_complex = args[args.index("-filter_complex") + 1]
    assert "[0:a:0]volume=0.45[a_primary]" in filter_complex
    assert "[0:a:1]volume=1.25[a_secondary]" in filter_complex
    assert "amix=inputs=2" in filter_complex
    audio_map_index = args.index("[aout]")
    assert args[audio_map_index - 1] == "-map"


def test_webm_banner_uses_vp9_decoder_for_alpha(tmp_path):
    args = build_ffmpeg_render_args(
        source_path=tmp_path / "source.mp4",
        output_path=tmp_path / "clip.mp4",
        start_sec=0,
        end_sec=8,
        preset={
            "output_width": 180,
            "output_height": 320,
            "fps": 30,
            "scale_mode": "cover",
            "crop_anchor": "center",
            "video_codec": "libx264",
            "audio_codec": "aac",
            "audio_mix_mode": "mix",
        },
        banner={"file_path": str(tmp_path / "overlay.webm"), "opacity": 1, "position": "bottom"},
    )

    banner_input = args.index(str(tmp_path / "overlay.webm"))
    assert args[banner_input - 3 : banner_input] == ["-c:v", "libvpx-vp9", "-i"]
    assert args.index("[aout]") > banner_input


def test_render_segment_creates_vertical_mp4(tmp_path, monkeypatch):
    _require_ffmpeg()
    store = _store(tmp_path, monkeypatch)
    source_path = settings.source_dir / "source.mp4"
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
            "sine=frequency=1000:sample_rate=44100",
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
        original_filename="source.mp4",
        duration_sec=metadata.duration_sec,
        width=metadata.width,
        height=metadata.height,
        fps=metadata.fps,
        status="ready",
    )
    analysis = VideoAnalysisService(store).run_analysis(source["id"], provider="mock")
    segment = store.list_ai_segments(analysis_id=analysis["id"])[0]
    preset = store.create_ffmpeg_preset(
        "Tiny vertical",
        output_width=180,
        output_height=320,
        fps=25,
        scale_mode="blur_background",
        extra={"crf": 30},
    )

    clip = ClipRenderService(store).render_segment(segment["id"], ffmpeg_preset_id=preset["id"])

    assert clip["status"] == "succeeded"
    assert clip["width"] == 180
    assert clip["height"] == 320
    assert clip["size_bytes"] > 0
    assert store.get_ai_segment(segment["id"])["status"] == "rendered"


def test_render_segment_with_mock_subtitles_burns_ass(tmp_path, monkeypatch):
    _require_ffmpeg()
    _require_ffmpeg_filter("ass")
    store = _store(tmp_path, monkeypatch)
    source_path = settings.source_dir / "source.mp4"
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
            "sine=frequency=1000:sample_rate=44100",
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
        original_filename="source.mp4",
        duration_sec=metadata.duration_sec,
        width=metadata.width,
        height=metadata.height,
        fps=metadata.fps,
        status="ready",
    )
    analysis = VideoAnalysisService(store).run_analysis(source["id"], provider="mock")
    segment = store.list_ai_segments(analysis_id=analysis["id"])[0]
    subtitle_profile = store.create_subtitle_profile(
        "Mock karaoke",
        provider="mock",
        font_size=32,
        max_words_per_line=2,
        margin_v=50,
    )
    preset = store.create_ffmpeg_preset(
        "Tiny subtitled vertical",
        output_width=180,
        output_height=320,
        fps=25,
        scale_mode="cover",
        extra={"crf": 30},
    )

    clip = ClipRenderService(store).render_segment(
        segment["id"],
        ffmpeg_preset_id=preset["id"],
        subtitle_profile_id=subtitle_profile["id"],
    )

    assert clip["status"] == "succeeded"
    assert clip["subtitle_track_id"]
    assert clip["width"] == 180
    assert clip["height"] == 320
    assert clip["size_bytes"] > 0
    track = store.get_subtitle_track(clip["subtitle_track_id"])
    assert track["status"] == "succeeded"
    assert track["ass_path"].endswith(".ass")
    assert "mock" in track["transcript_json"]


def test_render_montage_stitches_segments_and_mixes_audio(tmp_path, monkeypatch):
    _require_ffmpeg()
    store = _store(tmp_path, monkeypatch)
    source_path = settings.source_dir / "two-track-source.mp4"
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
            "sine=frequency=500:sample_rate=44100",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=900:sample_rate=44100",
            "-t",
            "11",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-map",
            "2:a:0",
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
    analysis = store.create_ai_analysis(source["id"], "mock", status="succeeded")
    first = store.create_ai_segment(analysis["id"], {"start_sec": 0, "end_sec": 5, "title": "First"})
    second = store.create_ai_segment(analysis["id"], {"start_sec": 5, "end_sec": 10, "title": "Second"})
    preset = store.create_ffmpeg_preset(
        "Montage mix",
        output_width=180,
        output_height=320,
        fps=25,
        scale_mode="cover",
        audio_mix_mode="mix",
        audio_primary_stream=0,
        audio_primary_volume=0.8,
        audio_secondary_stream=1,
        audio_secondary_volume=1.0,
        extra={"crf": 30},
    )

    clip = ClipRenderService(store).render_montage(
        [first["id"], second["id"]],
        ffmpeg_preset_id=preset["id"],
        title="Unit montage",
    )

    assert clip["status"] == "succeeded"
    assert clip["segment_id"] is None
    assert clip["width"] == 180
    assert clip["height"] == 320
    assert clip["duration_sec"] >= 9
    audio_probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=index,channels",
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


def _require_ffmpeg() -> None:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        pytest.skip("ffmpeg is not available")


def _require_ffmpeg_filter(filter_name: str) -> None:
    try:
        proc = subprocess.run(["ffmpeg", "-filters"], capture_output=True, text=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        pytest.skip("ffmpeg filters cannot be inspected")
    if filter_name not in proc.stdout:
        pytest.skip(f"ffmpeg {filter_name} filter is not available")
