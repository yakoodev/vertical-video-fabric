"""«Файлы для монтажа»: inserts are normalized, and really land in the video."""

from __future__ import annotations

import subprocess
from pathlib import Path

from app.montage_assets import build_inserts_args, kind_for, normalize_inserts, probe_asset
from test_api_pipeline import _require_ffmpeg


def test_normalize_clamps_and_drops_junk():
    out = normalize_inserts(
        [
            {"asset_id": 3, "at": 5, "duration": 99, "mode": "weird", "volume": 9},
            {"asset_id": "x"},
            "junk",
            {"asset_id": 1, "at": -2, "duration": 0.01, "mode": "pip", "reason": "  смешно  "},
        ]
    )
    assert out[0] == {
        "asset_id": 1,
        "at": 0.0,
        "duration": 0.3,
        "mode": "pip",
        "volume": 1.0,
        "duck": False,
        # Геометрия вставки: центр и ширина в долях кадра — по умолчанию как было.
        "x": 0.5,
        "y": 0.32,
        "scale": 0.82,
        "reason": "смешно",
    }
    assert out[1]["duration"] == 8.0 and out[1]["mode"] == "full" and out[1]["volume"] == 2.0 and out[1]["duck"] is True


def test_kind_for():
    assert kind_for("a.gif") == "gif"
    assert kind_for("a.PNG") == "image"
    assert kind_for("a.mp4") == "video"
    assert kind_for("a.mp3") == "audio"
    assert kind_for("a.txt") is None


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args], check=True, timeout=60)


def _mean_rgb(video: Path, t: float, tmp: Path) -> tuple[int, int, int]:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{t}", "-i", str(video), "-frames:v", "1", "-vf", "scale=8:8",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True, timeout=60,
    ).stdout
    px = [raw[i:i + 3] for i in range(0, len(raw), 3)]
    return tuple(sum(p[c] for p in px) // len(px) for c in range(3))  # type: ignore[return-value]


def test_full_image_and_sound_inserts_render(tmp_path):
    _require_ffmpeg()
    clip = tmp_path / "clip.mp4"
    _ffmpeg("-f", "lavfi", "-i", "color=c=blue:s=360x640:d=4", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
            "-t", "4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(clip))
    meme = tmp_path / "meme.png"
    _ffmpeg("-f", "lavfi", "-i", "color=c=red:s=200x200", "-frames:v", "1", str(meme))
    beep = tmp_path / "beep.wav"
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=880:duration=1", str(beep))

    assets = {
        1: {"kind": "image", "file_path": str(meme), "has_audio": False},
        2: {"kind": "audio", "file_path": str(beep), "has_audio": True},
    }
    inserts = normalize_inserts(
        [
            {"asset_id": 1, "at": 1.0, "duration": 1.5, "mode": "full"},
            {"asset_id": 2, "at": 1.0, "duration": 1.0, "mode": "sound"},
            {"asset_id": 99, "at": 0.5, "duration": 1.0},  # unknown file — skipped, not fatal
        ]
    )
    out = tmp_path / "out.mp4"
    args = build_inserts_args(clip, out, inserts, assets, 360, 640, 4.0)
    assert args is not None
    subprocess.run(args, check=True, timeout=120)

    meta = probe_asset(out)
    assert abs(meta["duration_sec"] - 4.0) < 0.3 and meta["has_audio"]
    r, g, b = _mean_rgb(out, 1.8, tmp_path)
    assert r > 120 and b < 90, (r, g, b)  # the meme covers the frame (square letterboxed on black)
    r, g, b = _mean_rgb(out, 3.3, tmp_path)
    assert b > 150 and r < 60, (r, g, b)  # back to the clip after the insert


def test_pip_insert_lands_where_the_editor_put_it(tmp_path):
    """Мем-вставку двигают ручками на превью — рендер обязан поставить её туда же."""

    _require_ffmpeg()
    clip = tmp_path / "clip.mp4"
    _ffmpeg("-f", "lavfi", "-i", "color=c=black:s=360x640:d=3", "-t", "3",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip))
    meme = tmp_path / "meme.png"
    _ffmpeg("-f", "lavfi", "-i", "color=c=red:s=100x100", "-frames:v", "1", str(meme))

    assets = {1: {"kind": "image", "file_path": str(meme), "has_audio": False}}
    # Четверть ширины, центр — в левом верхнем углу кадра.
    inserts = normalize_inserts(
        [{"asset_id": 1, "at": 0.5, "duration": 2.0, "mode": "pip", "x": 0.25, "y": 0.2, "scale": 0.25}]
    )
    assert inserts[0]["x"] == 0.25 and inserts[0]["scale"] == 0.25

    out = tmp_path / "out.mp4"
    args = build_inserts_args(clip, out, inserts, assets, 360, 640, 3.0, clip_has_audio=False)
    assert args is not None
    graph = args[args.index("-filter_complex") + 1]
    assert "overlay=x=(W*0.2500)-(w/2):y=(H*0.2000)-(h/2)" in graph
    subprocess.run(args, check=True, timeout=120)

    frame = tmp_path / "frame.png"
    _ffmpeg("-ss", "1.5", "-i", str(out), "-frames:v", "1", str(frame))
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(frame), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True, timeout=60,
    ).stdout

    def pixel(fx: float, fy: float) -> tuple[int, int, int]:
        x, y = int(360 * fx), int(640 * fy)
        i = (y * 360 + x) * 3
        return raw[i], raw[i + 1], raw[i + 2]

    assert pixel(0.25, 0.2)[0] > 150  # красный мем там, куда его поставили
    assert pixel(0.5, 0.32)[0] < 60  # и его нет на старом прибитом месте
