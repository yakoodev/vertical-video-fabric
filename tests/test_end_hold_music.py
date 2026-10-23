"""Музыка на стоп-кадре: доигрывает и затухает на нём, а не гаснет до него (app/render.py).

Готовый ролик на RENDER_END_HOLD_SEC длиннее клипа — последний кадр стоит. Раньше
музыка кончалась вместе с клипом, а стоп-кадр шёл в тишине. Теперь проход музыки
тянет её на стоп-кадр (голос клипа затухает на своём конце и дотягивается тишиной),
а финальный проход громкости видит хвост звука и держит кадр ровно на него.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.render import build_ffmpeg_music_args, music_tail_sec
from app.settings import settings


def _music(tmp_path, **extra):
    return {"track_path": str(tmp_path / "track.mp3"), "volume": 0.3, "loop": True, "duck": False,
            "duration_sec": 30, **extra}


def _graph(args):
    return args[args.index("-filter_complex") + 1]


def test_music_plays_into_hold_and_fades_at_file_end(tmp_path):
    args = build_ffmpeg_music_args(tmp_path / "b.mp4", tmp_path / "o.mp4", _music(tmp_path, hold_sec=0.5))
    graph = _graph(args)
    # Голос: затухание на своём конце (как end_fade_filter), дальше — тишина до конца стоп-кадра.
    assert graph.startswith("[0:a]afade=t=out:st=29.650:d=0.350,apad=whole_dur=30.5[voice];")
    # Музыка: затухает на стоп-кадре, фейд кончается ровно в конце файла (30 + 0.5).
    assert "afade=t=out:st=30:d=0.5[music]" in graph
    assert "[voice][music]amix=inputs=2:duration=first" in graph
    # Звук длиннее картинки на стоп-кадр — -shortest его срезал бы.
    assert "-shortest" not in args and args[args.index("-c:v") + 1] == "copy"


def test_preset_fade_longer_than_hold_still_ends_at_file_end(tmp_path):
    graph = _graph(build_ffmpeg_music_args(tmp_path / "b.mp4", tmp_path / "o.mp4",
                                           _music(tmp_path, hold_sec=0.5, fade_out_sec=2, duck=True)))
    assert "afade=t=out:st=28.5:d=2[music]" in graph
    assert "[voice]asplit=2[spk][key]" in graph  # приглушение под речь — по голосу с хвостом


def test_without_hold_music_graph_is_unchanged(tmp_path):
    args = build_ffmpeg_music_args(tmp_path / "b.mp4", tmp_path / "o.mp4", _music(tmp_path))
    assert _graph(args) == ("[1:a]volume=0.3[music];[0:a][music]amix=inputs=2:duration=first:dropout_transition=0:"
                            "normalize=0,aresample=48000[aout]")
    assert "-shortest" in args


class _Meta:
    def __init__(self, video, audio):
        self.raw = {"streams": [{"codec_type": "video", "duration": str(video)},
                                {"codec_type": "audio", "duration": str(audio)}]}


def test_music_tail_seen_only_when_audio_is_clearly_longer():
    assert music_tail_sec(_Meta(30.0, 30.5)) == 0.5
    assert music_tail_sec(_Meta(30.0, 30.023)) == 0.0  # хвост AAC-кадра — не музыка
    assert music_tail_sec(_Meta(30.0, 29.9)) == 0.0
    assert music_tail_sec(type("M", (), {"raw": {}})()) == 0.0


def test_hold_reaches_music_pass_only_with_final_audio_pass(monkeypatch):
    from app.render import ClipRenderService, _preset_with_output

    monkeypatch.setattr(settings, "render_end_hold_sec", 0.5)
    monkeypatch.setattr(settings, "render_loudnorm", True)
    assert ClipRenderService._end_hold_sec() == 0.5
    monkeypatch.setattr(settings, "render_loudnorm", False)  # стоп-кадр кладёт финальный проход
    assert ClipRenderService._end_hold_sec() == 0.0
    preset = {"id": 1, "output_width": 1080, "output_height": 1920}
    assert _preset_with_output(preset, {"source_type": "upload"}, None, 0.0) is preset
    assert _preset_with_output(preset, {"source_type": "upload"}, "9:16", 0.5)["end_hold_sec"] == 0.5


# ---------------------------------------------------------------- настоящий ffmpeg


def _require_ffmpeg():
    if not shutil.which("ffmpeg"):
        pytest.skip("нет ffmpeg")


def _rms_db(path: Path, start: float, end: float) -> float:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{start}", "-to", f"{end}", "-i", str(path), "-vn",
         "-af", "astats=measure_overall=RMS_level:measure_perchannel=none", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    found = re.findall(r"RMS level dB: (-?[\d.]+|-inf)", proc.stderr)
    return float(found[-1]) if found and found[-1] != "-inf" else -200.0


def _durations(path: Path) -> tuple[float, float]:
    from app.ingest import probe_media
    from app.render import stream_durations

    video, audio = stream_durations(probe_media(path))
    return float(video or 0), float(audio or 0)


def _store(tmp_path, monkeypatch):
    from test_render import _store as base

    return base(tmp_path, monkeypatch)


def _make_clip(path: Path, seconds: float, voice: bool) -> None:
    """Видео + голос-тон (или тишина) ровно ``seconds`` секунд."""
    audio = "sine=f=300:r=48000:d=%g" % seconds if voice else "anullsrc=r=48000:cl=stereo"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x203040:s=108x192:r=30:d={seconds}",
         "-f", "lavfi", "-i", audio, "-t", f"{seconds}", "-map", "0:v", "-map", "1:a", "-c:v", "libx264",
         "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", str(path)],
        check=True,
    )


@pytest.mark.parametrize("with_music", [True, False])
def test_real_render_tail(tmp_path, monkeypatch, with_music):
    """Проход музыки с hold + финальный проход громкости на настоящих файлах: с музыкой
    стоп-кадр звучит (музыка затухает к концу файла), без музыки — тишина, как раньше."""
    _require_ffmpeg()
    from app.render import ClipRenderService

    store = _store(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "render_loudnorm", True)
    monkeypatch.setattr(settings, "render_end_hold_sec", 0.5)
    monkeypatch.setattr(ClipRenderService, "_attach_qc", lambda self, c, p, m: c)
    base = tmp_path / "base.mp4"
    _make_clip(base, 4.0, voice=True)
    out = settings.clip_dir / "clip.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    if with_music:
        track = tmp_path / "track.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=660:r=44100:d=3", str(track)],
                       check=True)
        music = {"track_path": str(track), "volume": 0.5, "loop": True, "duck": False, "duration_sec": 4.0,
                 "hold_sec": 0.5}
        subprocess.run(build_ffmpeg_music_args(base, out, music), check=True)
        video, audio = _durations(out)
        assert abs(video - 4.0) < 0.05 and abs(audio - 4.5) < 0.05  # хвост музыки на стоп-кадр
    else:
        shutil.copy(base, out)
    src = settings.source_dir / "s.mp4"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"src")
    source = store.create_source("upload", src, duration_sec=5, width=108, height=192, status="ready")
    clip = store.create_clip(source["id"])
    clip = store.finish_clip_render(clip["id"], status="succeeded", output_path=out, preview_path=out)

    done = ClipRenderService(store)._normalize_loudness(clip)
    assert not (done.get("error") or ""), done.get("error")
    video, audio = _durations(out)
    assert abs(video - 4.5) < 0.08 and abs(audio - 4.5) < 0.08  # стоп-кадр 0.5 с, звук той же длины
    hold = _rms_db(out, 4.05, 4.3)
    if with_music:
        assert hold > -40, hold  # музыка звучит на стоп-кадре…
        assert _rms_db(out, 4.38, 4.48) < hold - 6  # …и затухает к самому концу файла
    else:
        assert hold < -60, hold  # без музыки стоп-кадр — тишина, как было
