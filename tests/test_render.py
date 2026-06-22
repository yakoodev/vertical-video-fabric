import json
import subprocess
from pathlib import Path

import pytest

from app.ai.service import VideoAnalysisService
from app.crypto import CookieCipher
from app.db import Database
from app.ingest import probe_media
from app.render import (
    ClipRenderService,
    _resolve_music_settings,
    _subtitle_prompt_for_clip,
    _video_style_filter,
    build_ffmpeg_music_args,
    build_ffmpeg_render_args,
)
from app.settings import settings
from app.store import AppStore


def test_preset_with_subtitle_offset_clamps_and_stashes(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    service = ClipRenderService(store)
    assert service._preset_with_subtitle_offset({"id": 1}, None) == {"id": 1}
    assert service._preset_with_subtitle_offset({"id": 1}, 0.5)["subtitle_offset_override"] == 0.5
    # Clamped to the supported nudge range.
    assert service._preset_with_subtitle_offset({"id": 1}, 9)["subtitle_offset_override"] == 2.0
    assert service._preset_with_subtitle_offset({"id": 1}, -9)["subtitle_offset_override"] == -2.0


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
    monkeypatch.setattr(settings, "ai_video_provider", "mock")
    settings.ensure_dirs()
    db = Database(data_dir / "app.sqlite")
    db.init()
    return AppStore(db, CookieCipher(data_dir / "secret.key"))


def test_subtitle_prompt_uses_anime_preset_for_smotvibe_source(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    source_path = settings.source_dir / "smotvibe.mp4"
    source_path.write_bytes(b"video")
    source = store.create_source(
        "smotvibe_url",
        source_path,
        original_url="https://smotvibe.sbs/series/6802576/",
        original_filename="smotvibe-6802576.mp4",
        duration_sec=120,
        width=1280,
        height=720,
        status="ready",
    )
    clip = store.create_clip(source["id"], title="Anime clip")

    prompt = _subtitle_prompt_for_clip(store, clip["id"])

    assert "anime audio" in prompt
    assert "honorifics" in prompt


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


def test_render_filter_applies_color_style_vignette_and_grain(tmp_path):
    args = build_ffmpeg_render_args(
        source_path=tmp_path / "source.mp4",
        output_path=tmp_path / "clip.mp4",
        start_sec=0,
        end_sec=6,
        preset={
            "output_width": 180,
            "output_height": 320,
            "fps": 30,
            "scale_mode": "cover",
            "crop_anchor": "center",
            "video_codec": "libx264",
            "audio_codec": "aac",
            "color_style": "cinematic",
            "color_strength": 1,
            "vignette": 0.5,
            "grain": 0.5,
        },
    )

    filter_complex = args[args.index("-filter_complex") + 1]
    assert "[vbase]" in filter_complex
    assert "colorbalance=" in filter_complex
    assert "vignette=angle=" in filter_complex
    assert "noise=alls=" in filter_complex
    assert "[vstyled]" in filter_complex
    assert filter_complex.rstrip().endswith("[vout]")


def test_render_filter_skips_style_when_disabled(tmp_path):
    args = build_ffmpeg_render_args(
        source_path=tmp_path / "source.mp4",
        output_path=tmp_path / "clip.mp4",
        start_sec=0,
        end_sec=6,
        preset={
            "output_width": 180,
            "output_height": 320,
            "fps": 30,
            "scale_mode": "cover",
            "crop_anchor": "center",
            "video_codec": "libx264",
            "audio_codec": "aac",
            "color_style": "none",
            "vignette": 0,
            "grain": 0,
        },
    )

    filter_complex = args[args.index("-filter_complex") + 1]
    assert "vignette" not in filter_complex
    assert "noise=" not in filter_complex
    assert "[vbase]copy[vout]" in filter_complex


def test_video_style_filter_noir_desaturates():
    chain = _video_style_filter({"color_style": "noir", "color_strength": 1})
    assert "hue=s=0" in chain
    assert "eq=contrast=" in chain


def test_music_args_loop_fade_and_duck(tmp_path):
    args = build_ffmpeg_music_args(
        tmp_path / "base.mp4",
        tmp_path / "out.mp4",
        {
            "track_path": str(tmp_path / "track.mp3"),
            "volume": 0.3,
            "loop": True,
            "fade_in_sec": 1.5,
            "fade_out_sec": 2,
            "duck": True,
            "duck_amount": 0.5,
            "duration_sec": 30,
        },
    )

    # Music input loops to fill the clip.
    track_index = args.index(str(tmp_path / "track.mp3"))
    assert args[track_index - 3 : track_index] == ["-stream_loop", "-1", "-i"]
    filter_complex = args[args.index("-filter_complex") + 1]
    assert "volume=0.3" in filter_complex
    assert "afade=t=in:st=0:d=1.5" in filter_complex
    assert "afade=t=out:st=28:d=2" in filter_complex
    assert "sidechaincompress=" in filter_complex
    assert "amix=inputs=2:duration=first" in filter_complex
    # Video is stream-copied, only audio is rebuilt.
    assert args[args.index("-c:v") + 1] == "copy"


def test_music_args_without_duck_just_mixes(tmp_path):
    args = build_ffmpeg_music_args(
        tmp_path / "base.mp4",
        tmp_path / "out.mp4",
        {
            "track_path": str(tmp_path / "track.mp3"),
            "volume": 0.4,
            "loop": False,
            "duck": False,
            "duration_sec": 20,
        },
    )

    assert "-stream_loop" not in args
    filter_complex = args[args.index("-filter_complex") + 1]
    assert "sidechaincompress" not in filter_complex
    assert "[0:a][music]amix=inputs=2:duration=first" in filter_complex


def test_resolve_music_settings_reads_track_and_preset(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    track_path = settings.audio_dir / "loop.mp3"
    track_path.write_bytes(b"audio")
    track = store.create_audio_track("Lo-fi", track_path, volume=0.3)
    preset = store.create_ffmpeg_preset(
        "With music",
        music_track_id=track["id"],
        music_duck=True,
    )

    resolved = _resolve_music_settings(store, preset)
    assert resolved is not None
    assert resolved["track_path"] == track["file_path"]
    # Volume now comes from the track itself.
    assert resolved["volume"] == 0.3
    assert resolved["duck"] is True

    no_music = store.create_ffmpeg_preset("No music")
    assert _resolve_music_settings(store, no_music) is None

    # A per-render volume override wins over the track's own volume.
    service = ClipRenderService(store)
    overridden = service._preset_with_music(preset, None, music_volume=0.9)
    assert _resolve_music_settings(store, overridden)["volume"] == 0.9


def test_finalize_render_transcribes_before_mixing_music(tmp_path, monkeypatch):
    # Subtitles must be generated from the clean base audio, so the subtitle
    # pass has to run before music is mixed in.
    store = _store(tmp_path, monkeypatch)
    track_path = settings.audio_dir / "loop.mp3"
    track_path.write_bytes(b"audio")
    track = store.create_audio_track("Loop", track_path, volume=0.3)
    preset = store.create_ffmpeg_preset("Music", music_track_id=track["id"])
    service = ClipRenderService(store)

    base = settings.clip_dir / "r.base.mp4"
    base.parent.mkdir(parents=True, exist_ok=True)
    base.write_bytes(b"video")
    output = settings.clip_dir / "r.mp4"
    order: list[str] = []

    def fake_subs(*, clip_id, input_path, output_path, subtitle_profile_id, preset):
        order.append("subtitle")
        Path(output_path).write_bytes(b"subbed")

    monkeypatch.setattr(service, "_render_subtitles", fake_subs)

    class _Meta:
        duration_sec = 5.0

    monkeypatch.setattr("app.render.probe_media", lambda path: _Meta())

    def fake_ffmpeg(args, timeout, cwd=None):
        order.append("music")
        Path(args[-1]).write_bytes(b"mixed")

    monkeypatch.setattr("app.render._run_ffmpeg", fake_ffmpeg)

    final, _temps = service._finalize_render(
        clip_id=1,
        render_id="r",
        base_output_path=base,
        output_path=output,
        preset=preset,
        subtitle_profile_id=7,
    )

    assert order == ["subtitle", "music"]
    assert final == output


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


def test_render_segment_with_music_and_filters(tmp_path, monkeypatch):
    _require_ffmpeg()
    store = _store(tmp_path, monkeypatch)
    source_path = settings.source_dir / "source.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=size=320x180:rate=25",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
            "-t", "6", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-c:a", "aac",
            str(source_path),
        ],
        check=True,
    )
    track_path = settings.audio_dir / "loop.m4a"
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=44100",
            "-t", "3", "-c:a", "aac", str(track_path),
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
    track = store.create_audio_track("Loop", track_path)
    preset = store.create_ffmpeg_preset(
        "Music + cinematic",
        output_width=180,
        output_height=320,
        fps=25,
        color_style="cinematic",
        color_strength=1,
        vignette=0.4,
        grain=0.2,
        music_track_id=track["id"],
        music_volume=0.3,
        music_loop=True,
        music_fade_in_sec=0.5,
        music_fade_out_sec=0.5,
        music_duck=True,
        music_duck_amount=0.6,
        extra={"crf": 30},
    )

    clip = ClipRenderService(store).render_segment(segment["id"], ffmpeg_preset_id=preset["id"])

    assert clip["status"] == "succeeded", clip.get("error")
    assert clip["width"] == 180
    assert clip["size_bytes"] > 0
    # The looped music fills the whole clip, so the output keeps an audio stream.
    output_meta = probe_media(Path(clip["output_path"]))
    assert output_meta.duration_sec > 0


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


def test_render_montage_with_mock_subtitles_transcribes_each_part(tmp_path, monkeypatch):
    _require_ffmpeg()
    _require_ffmpeg_filter("ass")
    store = _store(tmp_path, monkeypatch)
    source_path = settings.source_dir / "subtitle-montage-source.mp4"
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
            "sine=frequency=650:sample_rate=44100",
            "-t",
            "12",
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
    second = store.create_ai_segment(analysis["id"], {"start_sec": 6, "end_sec": 11, "title": "Second"})
    subtitle_profile = store.create_subtitle_profile(
        "Mock montage subtitles",
        provider="mock",
        timing_offset_sec=0.25,
        font_size=32,
        max_words_per_line=2,
        margin_v=50,
    )
    preset = store.create_ffmpeg_preset(
        "Subtitled montage",
        output_width=180,
        output_height=320,
        fps=25,
        scale_mode="cover",
        extra={"crf": 30},
    )

    clip = ClipRenderService(store).render_montage(
        [first["id"], second["id"]],
        ffmpeg_preset_id=preset["id"],
        subtitle_profile_id=subtitle_profile["id"],
        title="Chunked subtitles",
    )

    assert clip["status"] == "succeeded"
    track = store.get_subtitle_track(clip["subtitle_track_id"])
    transcript = json.loads(track["transcript_json"])
    usage = json.loads(track["usage_json"])
    words = transcript["words"]
    # Subtitles are transcribed from the whole stitched clip in a single pass so
    # the timestamps stay locked to the audio they are burned over (no chunked
    # per-part re-seek, which used to drift on real concatenated media).
    assert "subtitleChunkedTranscription" not in usage
    assert usage["subtitleTimingOffsetSec"] == 0.25
    assert words, "expected word-level timestamps"
    # The profile carries a +0.25s offset, so the first word starts no earlier.
    assert words[0]["start"] >= 0.24
    # Every word must stay within the clip duration (no drift past the end).
    assert max(word["end"] for word in words) <= transcript["duration"] + 0.001


def test_render_clip_plan_stitches_multiple_segments(tmp_path, monkeypatch):
    _require_ffmpeg()
    store = _store(tmp_path, monkeypatch)
    source_path = settings.source_dir / "plan-source.mp4"
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
            "sine=frequency=650:sample_rate=44100",
            "-t",
            "13",
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
    first = store.create_ai_segment(analysis["id"], {"start_sec": 0, "end_sec": 5, "title": "Setup"})
    second = store.create_ai_segment(analysis["id"], {"start_sec": 7, "end_sec": 12, "title": "Payoff"})
    plan = store.create_clip_plan(
        source["id"],
        analysis["id"],
        "Setup plus payoff",
        description="Two source ranges in one final clip.",
        segment_ids=[first["id"], second["id"]],
    )
    preset = store.create_ffmpeg_preset(
        "Plan vertical",
        output_width=180,
        output_height=320,
        fps=25,
        scale_mode="cover",
        extra={"crf": 30},
    )

    clip = ClipRenderService(store).render_clip_plan(plan["id"], ffmpeg_preset_id=preset["id"])

    assert clip["status"] == "succeeded"
    assert clip["clip_plan_id"] == plan["id"]
    assert clip["segment_id"] is None
    assert clip["title"] == "Setup plus payoff"
    assert clip["duration_sec"] >= 5
    assert store.get_clip_plan(plan["id"])["status"] == "rendered"


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
