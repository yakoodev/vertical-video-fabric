"""Записи 60 и 120 к/с в одном ролике: частота каждого исходника — из файла, не по умолчанию.

Журнал монтажа 15.11 (мувик BURN v4.1): запись B23 в 60 к/с читалась как 120 — кусок шёл вдвое быстрее и
кончался «застывшим» кадром. Баг был в ручных скриптах; здесь — проверка, что движок сервиса так не может:

* часть рендера (``build_ffmpeg_render_args``) читает СЕКУНДЫ исходника (``-ss``/``-t``) и пересобирает кадры
  на сетку пресета (``fps=``) — частота исходника в расчётах не участвует вовсе;
* склейка частей (``build_ffmpeg_concat_args``) — сумма длин и кадров;
* склейка блоков мем-эдита (``app/meme_edit_concat.py``) берёт частоту КАЖДОГО файла (``video_frames``) — блок
  без длины в плане считается по своей частоте, а не по частоте первого блока.

Содержимое исходника — яркость, растущая со временем (16 + 60·t): по кадру готовой части видно, какая секунда
исходника в нём, — двойная скорость дала бы вдвое круче рост.
"""

from __future__ import annotations

import subprocess

import pytest

SLOPE = 60.0  # уровней яркости Y в секунду исходника
BASE = 16.0


def _source(tmp_path, fps: int, seconds: float = 3.0):
    from test_api_pipeline import _require_ffmpeg

    _require_ffmpeg()
    path = tmp_path / f"src{fps}.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=black:s=160x90:r={fps}:d={seconds}",
                    "-f", "lavfi", "-i", f"sine=f=440:d={seconds}", "-vf",
                    f"geq=lum='{BASE:g}+{SLOPE:g}*T':cb=128:cr=128", "-shortest", "-c:v", "libx264", "-preset",
                    "ultrafast", "-qp", "0", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)],
                   check=True, capture_output=True)
    return path


def _luma(path) -> list[float]:
    """Средняя яркость Y каждого кадра (плоскость Y как есть, без пересчёта диапазона)."""
    # passthrough: кадры как есть, без добивки по меткам времени (у склейки copy первая метка сдвинута).
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf", "scale=8:8:flags=area", "-fps_mode",
                          "passthrough", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"],
                         check=True, capture_output=True).stdout
    frame = 8 * 8 * 3 // 2
    return [sum(raw[i:i + 64]) / 64 for i in range(0, len(raw) - frame + 1, frame)]


def _probe(path) -> dict:
    import json

    out = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-show_entries",
                          "stream=codec_type,nb_read_frames,duration,r_frame_rate", "-of", "json", str(path)],
                         check=True, capture_output=True, text=True).stdout
    streams = json.loads(out)["streams"]
    video = next(s for s in streams if s["codec_type"] == "video")
    audio = next(s for s in streams if s["codec_type"] == "audio")
    return {"frames": int(video["nb_read_frames"]), "video": float(video["duration"]), "audio": float(audio["duration"]),
            "rate": video["r_frame_rate"]}


PRESET = {"output_width": 90, "output_height": 160, "fps": 30, "video_codec": "libx264", "audio_codec": "aac",
          "scale_mode": "cover", "extra_json": {"crf": 1}}


def _part(tmp_path, source, name, start, end, retime=None):
    from app.render_args import build_ffmpeg_render_args

    out = tmp_path / name
    preset = {**PRESET, **({"piece_retime": retime} if retime else {})}
    subprocess.run(build_ffmpeg_render_args(source, out, start, end, preset), check=True, capture_output=True)
    return out


def _assert_source_time(luma: list[float], start: float, per_frame: float):
    """Кадр k части показывает секунду исходника start + k·per_frame (±1/60 с и шум кодека)."""
    for k in (0, len(luma) // 2, len(luma) - 1):
        want = BASE + SLOPE * (start + k * per_frame)
        assert luma[k] == pytest.approx(want, abs=3.5), (k, luma[k], want)


def test_parts_from_60_and_120_fps_sources_keep_real_time(tmp_path):
    from app.render_args import build_ffmpeg_concat_args
    from app.timebase import part_frames

    s60, s120 = _source(tmp_path, 60), _source(tmp_path, 120)
    a = _part(tmp_path, s60, "a.mp4", 0.5, 2.0)  # 1.5 с записи 60 к/с
    b = _part(tmp_path, s120, "b.mp4", 1.0, 2.0)  # 1.0 с записи 120 к/с
    # Ускоренный ×2 кусок записи 120 к/с: 2 с исходника → 1 с на клипе, яркость растёт вдвое быстрее — так и задумано.
    seg = {"start_sec": 0.5, "end_sec": 2.5, "speed": 2.0}
    c = _part(tmp_path, s120, "c.mp4", 0.5, 2.5, retime={"speed": 2.0, "has_audio": True, "segment": seg})

    for path, length, start, per_frame in ((a, 1.5, 0.5, 1 / 30), (b, 1.0, 1.0, 1 / 30), (c, 1.0, 0.5, 2 / 30)):
        info = _probe(path)
        assert info["frames"] == part_frames(length, 30), (path.name, info)
        assert info["rate"] == "30/1" and info["video"] == pytest.approx(length, abs=0.04), (path.name, info)
        assert info["audio"] == pytest.approx(length, abs=0.06), (path.name, info)
        _assert_source_time(_luma(path), start, per_frame)

    joined_list = tmp_path / "parts.txt"
    joined_list.write_text("".join(f"file '{p.as_posix()}'\n" for p in (a, b, c)), encoding="utf-8")
    joined = tmp_path / "joined.mp4"
    subprocess.run(build_ffmpeg_concat_args(joined_list, joined), check=True, capture_output=True)
    info = _probe(joined)
    assert info["frames"] == part_frames(1.5, 30) + part_frames(1.0, 30) * 2
    assert info["video"] == pytest.approx(3.5, abs=0.06)
    luma = _luma(joined)
    # Начало второй части (запись 120 к/с с 1.0 с) — сразу за 45 кадрами первой.
    assert luma[45] == pytest.approx(BASE + SLOPE * 1.0, abs=3.5)


def test_meme_edit_concat_reads_each_block_rate(tmp_path):
    """Блоки 60 и 120 к/с без длины в плане: каждый — по своей частоте, ролик 1 + 1 с, а не 1 + 2 с."""
    from app.meme_edit_concat import concat_reel, video_frames

    files = []
    for fps in (60, 120):
        path = tmp_path / f"block{fps}.mp4"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=blue:s=96x54:r={fps}:d=1",
                        "-f", "lavfi", "-i", "sine=f=330:d=1", "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)], check=True, capture_output=True)
        files.append(path)
    assert [round(video_frames(p)[1]) for p in files] == [60, 120]

    blocks = [{"key": "a", "words": [], "inserts": []}, {"key": "b", "words": [], "inserts": []}]
    out = tmp_path / "reel.mp4"
    tl = concat_reel(blocks, files, out, music_path=None, segments=[], work=tmp_path / "w", flashes=False)
    assert tl["fps"] == 60 and [b["len"] for b in tl["blocks"]] == [pytest.approx(1.0, abs=0.02)] * 2
    assert tl["total"] == pytest.approx(2.0, abs=0.03)
    frames, rate = video_frames(out)
    assert rate == 60 and frames == pytest.approx(120, abs=1)
