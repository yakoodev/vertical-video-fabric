import json
import shutil
import struct
import subprocess
from fractions import Fraction
from pathlib import Path

import pytest

from app import publish_master
from app.crypto import CookieCipher
from app.db import Database
from app.publish_master import MasterResult, action_of, assess, encode_args, moov_before_mdat, target_fps, target_size
from app.settings import settings
from app.store import AppStore
from app.worker import JobWorker

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _info(**video):
    stream = {
        "index": 0, "codec_type": "video", "codec_name": "h264", "profile": "High", "pix_fmt": "yuv420p",
        "width": 1080, "height": 1920, "r_frame_rate": "30/1", "avg_frame_rate": "30/1",
        "color_primaries": "bt709", "color_space": "bt709", "color_transfer": "bt709",
    }
    stream.update(video)
    audio = {"index": 1, "codec_type": "audio", "codec_name": "aac", "sample_rate": "48000", "channels": 2}
    return {"streams": [stream, audio], "format": {}}


def test_compliant_render_goes_as_is():
    plan = assess(_info(), True)
    assert action_of(plan) == "none"
    assert plan["reasons"] == []


def test_untagged_render_without_faststart_is_only_remuxed():
    plan = assess(_info(color_primaries="unknown", color_space="unknown", color_transfer="unknown"), False)
    assert action_of(plan) == "remux"
    assert plan["video"] == "copy" and plan["audio"] == "copy"
    assert plan["reasons"] == ["colour_tags", "faststart"]
    args = publish_master.copy_args(Path("in.mp4"), Path("out.mp4"), plan, audio_bitrate="320k")
    assert args[args.index("-c:v") + 1] == "copy"
    assert any(a.startswith("h264_metadata=colour_primaries=1") for a in args)
    assert "+faststart" in args


@pytest.mark.parametrize(
    "video,reason",
    [
        ({"codec_name": "hevc", "profile": "Main"}, "codec:hevc"),
        ({"pix_fmt": "yuv420p10le", "profile": "High 10"}, "pix_fmt:yuv420p10le"),
        ({"pix_fmt": "yuvj420p"}, "pix_fmt:yuvj420p"),
        ({"width": 720, "height": 1280}, "below_1080p:720x1280"),
        ({"r_frame_rate": "60/1", "avg_frame_rate": "2997/100"}, "vfr"),
        ({"r_frame_rate": "120/1", "avg_frame_rate": "120/1"}, "fps:120.00"),
        ({"side_data_list": [{"rotation": -90}]}, "rotation:270"),
    ],
)
def test_off_spec_video_is_reencoded(video, reason):
    plan = assess(_info(**video), True)
    assert action_of(plan) == "encode"
    assert reason in plan["reasons"]


def test_audio_only_problem_keeps_video_copy():
    info = _info()
    info["streams"][1].update({"codec_name": "opus", "sample_rate": "48000"})
    plan = assess(info, True)
    assert action_of(plan) == "audio"
    args = publish_master.copy_args(Path("a.mp4"), Path("b.mp4"), plan, audio_bitrate="320k")
    assert args[args.index("-c:v") + 1] == "copy"
    assert args[args.index("-c:a") + 1] == "aac" and "48000" in args


def test_hdr_and_missing_video_are_left_alone():
    assert assess(_info(color_transfer="smpte2084", codec_name="hevc"), True)["skip"] is True
    assert action_of(assess({"streams": [{"codec_type": "audio"}]}, True)) == "none"


def test_always_mode_reencodes_compliant_file_and_off_mode_never_touches(tmp_path):
    assert action_of(assess(_info(), True, mode="always")) == "encode"
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"x")
    result = publish_master.prepare(src, mode="off")
    assert result.path == src and result.action == "none" and not result.temporary


def test_upscale_can_be_disabled():
    assert action_of(assess(_info(width=720, height=1280), True, upscale=False)) == "none"


def test_encode_args_follow_platform_spec():
    plan = assess(_info(width=720, height=1280, color_space="unknown", color_primaries="unknown"), True)
    args = encode_args(Path("in.mp4"), Path("out.mp4"), plan, crf=17, preset="medium", gop_sec=0.5,
                       audio_bitrate="320k")
    joined = " ".join(args)
    assert "scale=1080:1920:flags=lanczos" in joined and "out_color_matrix=bt709" in joined
    assert "fps=30/1" in joined and "format=yuv420p" in joined
    assert args[args.index("-profile:v") + 1] == "high" and args[args.index("-level:v") + 1] == "4.2"
    assert args[args.index("-g") + 1] == "15" and args[args.index("-bf") + 1] == "2"
    assert args[args.index("-crf") + 1] == "17" and args[args.index("-maxrate") + 1] == "16M"
    for flag in ("-colorspace", "-color_primaries", "-color_trc"):
        assert args[args.index(flag) + 1] == "bt709"
    assert "+faststart" in args
    # Звук AAC 48 кГц и так по спецификации — копией, без второго поколения потерь.
    assert args[args.index("-c:a") + 1] == "copy"


def test_target_fps_and_size():
    assert target_fps(Fraction(30000, 1001)) == Fraction(30000, 1001)
    assert target_fps(Fraction(2997, 100)) == Fraction(30000, 1001)
    assert target_fps(Fraction(29)) == 30
    assert target_fps(Fraction(120)) == 60
    assert target_fps(None) == 30
    assert target_size(720, 1280) == (1080, 1920)
    assert target_size(1280, 720) == (1920, 1080)
    assert target_size(1080, 1920) == (1080, 1920)
    assert target_size(721, 1281) == (1080, 1918)


def _box(kind: bytes, payload: bytes = b"") -> bytes:
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


def test_moov_detection(tmp_path):
    fast = tmp_path / "fast.mp4"
    fast.write_bytes(_box(b"ftyp", b"isom") + _box(b"moov", b"m" * 8) + _box(b"mdat", b"d" * 16))
    slow = tmp_path / "slow.mp4"
    slow.write_bytes(_box(b"ftyp", b"isom") + _box(b"mdat", b"d" * 16) + _box(b"moov", b"m" * 8))
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"fake")
    assert moov_before_mdat(fast) is True
    assert moov_before_mdat(slow) is False
    assert moov_before_mdat(junk) is None


def test_prepare_falls_back_to_source_on_unreadable_file(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "runtime_dir", tmp_path / "runtime")
    src = tmp_path / "fake.mp4"
    src.write_bytes(b"fake-video")
    result = publish_master.prepare(src, mode="auto")
    assert result.path == src and result.action == "none" and result.error
    assert result.summary()["error"]


def test_prepare_falls_back_when_ffmpeg_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "runtime_dir", tmp_path / "runtime")
    src = tmp_path / "clip.mp4"
    src.write_bytes(_box(b"ftyp", b"isom") + _box(b"mdat", b"d") + _box(b"moov", b"m"))

    def fake_run(args, **kwargs):
        if args[0] == "ffprobe":
            return subprocess.CompletedProcess(args, 0, json.dumps(_info(codec_name="hevc")), "")
        return subprocess.CompletedProcess(args, 1, "", "boom: encoder exploded")

    result = publish_master.prepare(src, mode="auto", run=fake_run)
    assert result.path == src and result.action == "none"
    assert "encoder exploded" in result.error and "codec:hevc" in result.reasons
    assert not list((tmp_path / "runtime" / "publish-masters").glob("*.mp4"))


def _ffprobe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg is not installed")
def test_real_encode_of_low_res_vfr_upload(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "runtime_dir", tmp_path / "runtime")
    monkeypatch.setattr(settings, "publish_master_preset", "veryfast")
    src = tmp_path / "phone.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc2=size=360x640:rate=25:duration=1",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=22050:duration=1",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv444p", "-c:a", "aac", "-shortest", str(src)],
        check=True,
    )
    result = publish_master.prepare(src, mode="auto")
    assert result.action == "encode", result
    assert result.temporary and result.path != src and result.path.is_file()
    streams = _ffprobe(result.path)["streams"]
    video = next(s for s in streams if s["codec_type"] == "video")
    audio = next(s for s in streams if s["codec_type"] == "audio")
    assert (video["width"], video["height"]) == (1080, 1920)
    assert video["profile"] == "High" and video["pix_fmt"] == "yuv420p"
    assert video["color_primaries"] == "bt709" and video["color_space"] == "bt709"
    assert video["avg_frame_rate"] == "25/1"
    assert audio["codec_name"] == "aac" and audio["sample_rate"] == "48000"
    assert moov_before_mdat(result.path) is True
    result.cleanup()
    assert not result.path.exists()


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg is not installed")
def test_real_remux_keeps_video_bits(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "runtime_dir", tmp_path / "runtime")
    src = tmp_path / "render.mp4"
    # Как наш рендер: H.264 High 1080×1920 30 к/с, AAC 48 кГц, без меток цвета (и без faststart).
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=30:duration=0.5",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=0.5",
         "-c:v", "libx264", "-preset", "ultrafast", "-profile:v", "high", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(src)],
        check=True,
    )
    result = publish_master.prepare(src, mode="auto")
    assert result.action == "remux", result
    assert "colour_tags" in result.reasons
    video = next(s for s in _ffprobe(result.path)["streams"] if s["codec_type"] == "video")
    assert video["color_primaries"] == "bt709"
    assert moov_before_mdat(result.path) is True

    def frame_hashes(path):
        return subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v", "-f", "framemd5", "-"],
                              capture_output=True, text=True, check=True).stdout.splitlines()[-5:]

    assert [line.split(",")[-1] for line in frame_hashes(src)] == [line.split(",")[-1] for line in frame_hashes(result.path)]


def test_worker_uploads_master_and_removes_it(tmp_path, monkeypatch):
    monkeypatch.setattr("app.settings.settings.provider_mode", "mock")
    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")
    monkeypatch.setattr(settings, "runtime_dir", tmp_path / "runtime")
    settings.ensure_dirs()
    db = Database(tmp_path / "app.sqlite")
    db.init()
    store = AppStore(db, CookieCipher(tmp_path / "secret.key"))
    tiktok = store.upsert_account("tiktok", "tt", "sessionid=abc")
    youtube = store.upsert_account("youtube", "yt", "SID=a; HSID=b; SSID=c; APISID=d; SAPISID=e")
    source = tmp_path / "video.mp4"
    source.write_bytes(b"fake-video")
    with source.open("rb") as f:
        job = store.create_job(f, "video.mp4", "Title", "Desc", [tiktok["id"], youtube["id"]], "public", True)
    master_file = tmp_path / "master.mp4"
    master_file.write_bytes(b"master")
    calls = []
    monkeypatch.setattr(publish_master, "prepare",
                        lambda path: calls.append(path) or MasterResult(master_file, "encode", ["vfr"], temporary=True))
    uploaded = []

    class Recorder:
        def upload(self, **kwargs):
            from app.providers.base import ProviderResult

            uploaded.append(kwargs["file_path"])
            return ProviderResult(status="succeeded", remote_id="x")

    monkeypatch.setattr("app.worker.get_provider", lambda platform: Recorder())

    assert JobWorker(store).process_once()
    assert len(calls) == 1  # один мастер на задание
    assert uploaded == [master_file, master_file]
    assert not master_file.exists()
    done = store.get_job(job["id"])
    assert done["status"] == "succeeded"
    for target in done["targets"]:
        assert json.loads(target["response_json"])["master"] == {"action": "encode", "reasons": ["vfr"]}
