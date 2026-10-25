"""Стоп-кадр посреди ролика (freeze_frame, app/freeze.py)."""

from __future__ import annotations

import json
import math
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from app.freeze import (
    FreezeMap,
    build_freeze_args,
    comp_freezes,
    freeze_problem,
    motion_time,
    normalize_freezes,
    post_to_pre,
    shift_voice,
)

HAS_FFMPEG = bool(shutil.which("ffmpeg"))
INSERT = {"at": 2.0, "duration": 1.0, "audio": "silence", "layers": "freeze"}


def test_normalize_and_problems():
    got = normalize_freezes([{"at": 3, "duration": 5}, {"at": 1}, {"at": 1.05, "duration": 0.5}, "мусор",
                             {"at": 4, "duration": 0.5, "audio": "continue", "layers": "continue"}])
    assert [(f["at"], f["duration"], f["audio"], f["layers"]) for f in got] == [
        (1.0, 1.0, "silence", "freeze"), (3.0, 2.0, "silence", "freeze"), (4.0, 0.5, "continue", "continue")]
    # Поверх (звук идёт) занимает отрезок: второй стоп-кадр внутри него — прочь.
    assert len(normalize_freezes([{"at": 1, "duration": 1, "audio": "continue"}, {"at": 1.5}])) == 1
    assert freeze_problem({"at": 1, "duration": 3}) and freeze_problem({"at": 1, "duration": 0.1})
    assert freeze_problem({"at": 1, "audio": "мимо"}) and freeze_problem({"at": -1})
    assert freeze_problem({"at": 9.9, "duration": 1, "audio": "continue"}, finished=10.0)
    assert freeze_problem({"at": 5, "duration": 1}, finished=10.0) is None


def test_freeze_map_shifts_after_insert_and_stretches_through_it():
    fm = FreezeMap([INSERT, {"at": 5.0, "duration": 0.5, "audio": "continue"}])
    assert fm(1.0) == 1.0 and fm(2.0) == 2.0  # сам момент стоп-кадра — его начало
    assert fm(2.5) == 3.5 and fm(6.0) == 7.0  # поверх (continue) время не двигает
    assert fm.range(1.5, 2.5) == (1.5, 3.5)  # слово через стоп-кадр тянется на нём
    assert fm.added(10.0) == 1.0
    assert not FreezeMap([{"at": 1, "audio": "continue"}])


def test_motion_time_hold_and_continue():
    # Слои стоят: на стоп-кадре время слоёв = at, после — за вычетом вставки.
    hold = [INSERT]
    assert [motion_time(u, hold) for u in (1.0, 2.0, 2.7, 3.0, 4.5)] == [1.0, 2.0, 2.0, 2.0, 3.5]
    # Слои идут (сдвинуты FreezeMap): время ролика и есть время слоёв.
    cont = [{**INSERT, "layers": "continue"}]
    assert [motion_time(u, cont) for u in (1.0, 2.5, 3.5)] == [1.0, 2.5, 3.5]
    # Поверх и слои стоят: на стоп-кадре at, после — без сдвига.
    over = [{"at": 2.0, "duration": 1.0, "audio": "continue", "layers": "freeze"}]
    assert [motion_time(u, over) for u in (2.5, 3.2)] == [2.0, 3.2]
    assert comp_freezes(cont) == [] and comp_freezes(hold) == [{"at": 2.0, "d": 1.0, "insert": True, "hold": True}]


def test_post_to_pre_for_view_frame():
    where = post_to_pre(2.5, [INSERT])
    assert where == {"video": 2.0, "layers": 2.0, "audio": 2.0, "freeze": 0}
    assert post_to_pre(4.0, [INSERT])["video"] == 3.0
    over = post_to_pre(2.5, [{"at": 2.0, "duration": 1.0, "audio": "continue", "layers": "continue"}])
    assert over == {"video": 2.0, "layers": 2.5, "audio": 2.5, "freeze": 0}


def test_voice_states_get_silence_on_inserted_freeze():
    voice = {"av": {"t0": 1.0, "fps": 10, "states": "1" * 15}, "late": {"t0": 3.0, "fps": 10, "states": "11"}}
    got = shift_voice(voice, [INSERT])
    # at 2.0 при t0 1.0 и 10 к/с — 10 кадров молчания с 10-го кадра; слой с 3.0 — едет целиком.
    assert got["av"]["states"] == "1" * 10 + "0" * 10 + "1" * 5
    assert got["late"]["t0"] == 4.0 and got["late"]["states"] == "11"


def _rms(raw: bytes) -> float:
    n = len(raw) // 4
    vals = struct.unpack(f"<{n}f", raw[: n * 4])
    return math.sqrt(sum(v * v for v in vals) / max(1, n))


def _probe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration", "-of", "json",
                          str(path)], capture_output=True, text=True).stdout
    return {s["codec_type"]: float(s["duration"]) for s in json.loads(out)["streams"]}


def _gray(path: Path, at: float) -> bytes:
    """Кадр в момент ``at`` — серый, 40×71."""
    return subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{at:.3f}", "-i", str(path), "-frames:v", "1",
                           "-vf", "scale=40:71,format=gray", "-f", "rawvideo", "-"], capture_output=True).stdout


def _diff(path: Path, a: float, b: float) -> float:
    """Средняя разница кадров (0..255): сжатие x264 даёт крохи разницы и у стоящего кадра."""
    x, y = _gray(path, a), _gray(path, b)
    assert x and len(x) == len(y)
    return sum(abs(p - q) for p, q in zip(x, y)) / len(x)


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg не установлен")
def test_base_freeze_real_ffmpeg(tmp_path):
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=160x284:r=30:d=4",
                    "-f", "lavfi", "-i", "sine=f=440:d=4:r=48000", "-c:v", "libx264", "-c:a", "aac", "-ac", "2",
                    "-shortest", str(src)], check=True)
    # Вставка: длиннее на 1 с, кадр стоит, звук исходника молчит.
    out = tmp_path / "insert.mp4"
    subprocess.run(build_freeze_args(src, out, [INSERT], 30, True, 4.0), check=True)
    dur = _probe(out)
    assert dur["video"] == pytest.approx(5.0, abs=0.06) and dur["audio"] == pytest.approx(5.0, abs=0.08)
    assert _diff(out, 2.2, 2.8) < 2  # стоп-кадр
    assert _diff(out, 3.5, 3.8) > 4  # дальше видео идёт
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out), "-af", "atrim=2.2:2.8", "-f", "f32le", "-ac", "1",
                          "-"], capture_output=True).stdout
    loud = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out), "-af", "atrim=3.2:3.8", "-f", "f32le", "-ac", "1",
                           "-"], capture_output=True).stdout
    assert _rms(pcm) < 0.001 < _rms(loud)
    # Поверх: длина та же, звук идёт.
    over = tmp_path / "over.mp4"
    subprocess.run(build_freeze_args(src, over, [{**INSERT, "audio": "continue"}], 30, True, 4.0), check=True)
    assert _probe(over)["video"] == pytest.approx(4.0, abs=0.06)
    assert _diff(over, 2.2, 2.8) < 2
    still = subprocess.run(["ffmpeg", "-v", "error", "-i", str(over), "-af", "atrim=2.2:2.8", "-f", "f32le", "-ac", "1",
                            "-"], capture_output=True).stdout
    assert _rms(still) > 0.01


def _run(tmp_path, monkeypatch, freezes, extra=None):
    sys.path.insert(0, str(Path(__file__).parent))
    from format_golden_cases import run_case

    return run_case("crop_focus_motion", tmp_path, monkeypatch, extra={"freezes": freezes, **(extra or {})})


def test_render_puts_freeze_into_base_before_compositor(tmp_path, monkeypatch):
    got = _run(tmp_path, monkeypatch, [INSERT])
    assert got["status"] == "succeeded", got.get("error")
    kinds = [k for k, _a in got["calls"]]
    freeze_at = next(i for i, (k, a) in enumerate(got["calls"]) if k == "ffmpeg" and any("tpad=stop_mode=clone" in str(x)
                                                                                         for x in a))
    assert freeze_at < kinds.index("compositor")  # стоп-кадр в основе — до слоёв
    comp = next(entry["comp"] for k, entry in got["calls"] if k == "compositor")
    assert comp["freezes"] == [{"at": 2.0, "d": 1.0, "insert": True, "hold": True}]
    # Слои стоят — их время не сдвигается, компоновщик держит его на стоп-кадре.
    assert next(l for l in comp["layers"] if l["id"] == "zoomed")["end"] == 4


def test_render_continue_layers_are_shifted(tmp_path, monkeypatch):
    got = _run(tmp_path, monkeypatch, [{**INSERT, "layers": "continue"}])
    comp = next(entry["comp"] for k, entry in got["calls"] if k == "compositor")
    assert "freezes" not in comp
    layers = {l["id"]: l for l in comp["layers"]}
    assert layers["zoomed"]["end"] == 5.0  # через стоп-кадр — тянется на нём
    assert [round(k["t"], 2) for k in comp["camera"]] == [1.0, 1.4, 4.0]  # ключ после at — на 1 с позже


def test_tool_and_view_frame(tmp_path, monkeypatch):
    from test_stream_feedback import _ctx

    from app.agent_tools import run_tool
    from test_cuts import _two_pieces

    store, _service, plan, _preset = _two_pieces(tmp_path, monkeypatch)
    ctx = _ctx(store, plan["id"])
    out, ok = run_tool(ctx, "freeze_frame", {"at": 5, "duration": 0.8, "audio": "silence"})
    assert ok, out
    assert out["freezes"] == [{"at": 5.0, "duration": 0.8, "audio": "silence", "layers": "freeze"}]
    assert out["finished_with_freezes"] == pytest.approx(out["finished_duration"] + 0.8)
    bad, ok = run_tool(ctx, "freeze_frame", {"at": 5, "duration": 3})
    assert not ok
    clip, _ = run_tool(ctx, "get_clip", {})
    assert clip["render"]["freezes"][0]["at"] == 5.0
    # view_frame на стоп-кадре: кадр основы и слои — в at, субтитры — тоже (звук молчит).
    seen = {}

    def fake_frame(store_, plan_id, t, **kw):
        seen.update(t=t, **kw)
        return b"png"

    monkeypatch.setattr("app.motion_render.render_frame_png", fake_frame)
    _img, ok = run_tool(ctx, "view_frame", {"t": 5.4, "final": True})
    assert ok
    assert seen["t"] == pytest.approx(5.0) and seen["layer_t"] == pytest.approx(5.0) and seen["sub_t"] == pytest.approx(5.0)
    _img, ok = run_tool(ctx, "view_frame", {"t": 7.0, "final": True})
    assert seen["t"] == pytest.approx(6.2)
    out, ok = run_tool(ctx, "freeze_frame", {"reset": True})
    assert ok and out["freezes"] == []
