"""Скорость и реверс кусков основы: длины на таймлайне, субтитры (растяжение в 1/speed, у реверса —
скрыты), вырезы внутри ускоренного куска, перенос слоёв, инструмент set_segment_speed, команды
ffmpeg и настоящий рендер ffmpeg на синтетике."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app.agent_tools import ToolContext, clip_timeline, get_tool, run_tool
from app.clip_history import snapshot
from app.cuts import plan_cuts
from app.motion_render import plan_caption_words
from app.piece_speed import (
    atempo_chain, attach, audio_chain, normalize_speed, seg_length, to_source, video_chain,
)
from app.render import build_ffmpeg_render_args
from app.timebase import ClipClock
from test_api_pipeline import _require_ffmpeg
from test_cuts import _mock_render, _two_pieces


def _ctx(store, plan_id):
    return ToolContext(store=store, clip_plan_id=plan_id, on_first_change=lambda: snapshot(store, plan_id, "тест"))


# ------------------------------------------------------------------ чистые функции


def test_normalize_attach_and_lengths():
    raw = [{"piece": 7, "speed": 0.5}, {"piece": 8, "speed": 1}, {"piece": 9, "reverse": True, "audio": "reverse"},
           {"piece": 10, "speed": 9}, {"piece": 11, "speed": 2, "audio": "reverse"}, {"piece": 7, "speed": 0.5}]
    clean = normalize_speed(raw)
    assert clean == [{"piece": 7, "speed": 0.5}, {"piece": 9, "reverse": True, "audio": "reverse"},
                     {"piece": 10, "speed": 4.0}, {"piece": 11, "speed": 2.0}]
    segs = attach([{"id": 7, "start_sec": 10, "end_sec": 14}, {"id": 9, "start_sec": 20, "end_sec": 23},
                   {"id": 12, "start_sec": 0, "end_sec": 1}], {"speed": clean})
    assert segs[0]["speed"] == 0.5 and segs[0]["speed_audio"] == "tempo" and seg_length(segs[0]) == 8.0
    assert segs[1]["reverse"] and segs[1]["speed_audio"] == "reverse" and seg_length(segs[1]) == 3.0
    assert "speed" not in segs[2] and seg_length(segs[2]) == 1.0
    assert to_source(segs[0], 2.0) == 11.0 and to_source(segs[1], 1.0) == 22.0
    assert atempo_chain(0.25) == "atempo=0.5,atempo=0.5" and atempo_chain(3) == "atempo=2,atempo=1.5"
    assert video_chain(segs[0], 30) == "setpts=(PTS-STARTPTS)/0.5,fps=30,tpad=stop_mode=clone:stop=3"
    assert video_chain(segs[1], 30).startswith("reverse,")
    assert audio_chain(segs[0]) == "atempo=0.5,asetpts=PTS-STARTPTS,apad=whole_dur=8.000"
    assert audio_chain(segs[1]).startswith("areverse")
    assert audio_chain({**segs[1], "speed_audio": "mute"}).startswith("volume=0")
    assert audio_chain({**segs[0], "speed_audio": "pitch"}).startswith("asetrate=48000*0.5")
    assert video_chain(segs[2], 30) is None and audio_chain(segs[2]) is None


def test_timeline_cuts_and_clock_follow_speed():
    segs = [{"id": 1, "start_sec": 0, "end_sec": 4, "speed": 0.5}, {"id": 2, "start_sec": 10, "end_sec": 14}]
    clock = ClipClock(segs, None)
    assert clock.lengths == [8.0, 4.0] and clock.clip_duration == 12.0
    # Вырез 2–3 с шкалы клипа внутри замедленного куска = 1–1.5 с исходника.
    cut = plan_cuts(segs, [{"start": 2.0, "end": 3.0}], 30.0)
    assert [(p["start_sec"], p["end_sec"]) for p in cut.parts[:2]] == [(0.0, 1.0), (1.5, 4.0)]
    assert cut.parts[0]["speed"] == 0.5 and cut.total_after == pytest.approx(11.0)
    # Реверс: начало куска на шкале клипа — конец исходника.
    rev = plan_cuts([{"id": 1, "start_sec": 0, "end_sec": 4, "reverse": True}], [{"start": 1.0, "end": 2.0}], 30.0)
    assert [(p["start_sec"], p["end_sec"]) for p in rev.parts] == [(3.0, 4.0), (0.0, 2.0)]


# ------------------------------------------------------------------ инструмент, субтитры, слои


def test_set_segment_speed_moves_timeline_subtitles_and_layers(tmp_path, monkeypatch):
    store, _service, plan, _preset = _two_pieces(tmp_path, monkeypatch)  # куски 0–10 и 30–40, наплыв 0.5
    ctx = _ctx(store, plan["id"])
    out, ok = run_tool(ctx, "add_layers", {"layers": [{"type": "text", "start": 14.0, "end": 15.0, "x": 0.5, "y": 0.3,
                                                       "props": {"text": "ПОСЛЕ", "size": 80}}]})
    assert ok, out
    before = clip_timeline(store, plan["id"])["duration"]

    out, ok = run_tool(ctx, "set_segment_speed", {"piece": 0, "speed": 0.5})
    assert ok, out
    assert out["length_before"] == 10.0 and out["length"] == 20.0
    assert out["duration"] == pytest.approx(before + 10.0)
    timeline = clip_timeline(store, plan["id"])
    assert timeline["pieces"][0]["speed"] == 0.5 and timeline["pieces"][1]["clip_start"] == pytest.approx(19.5)
    # Слой во втором куске уехал на 10 с.
    layer = store.get_clip_plan(plan["id"])["render_settings"]["layers"][0]
    assert layer["start"] == pytest.approx(24.0)
    # Субтитры: «раз» (5 с первого куска) — на 10 с, «два» (2 с второго куска) — на 22 с «кусков подряд».
    words = {w["word"]: w for w in store.get_clip_plan_subtitles(plan["id"])["words"]}
    assert words["раз"]["start"] == pytest.approx(10.0) and words["раз"]["end"] == pytest.approx(11.0)
    assert words["два"]["start"] == pytest.approx(22.0) and words["два"]["end"] == pytest.approx(22.5)
    assert [w["w"] for w in plan_caption_words(store, store.get_clip_plan(plan["id"]))] == ["раз", "два"]

    # Реверс второго куска: его субтитры скрыты, длина та же.
    out, ok = run_tool(ctx, "set_segment_speed", {"piece": 1, "reverse": True})
    assert ok and out["audio"] == "mute" and out["length"] == 10.0
    assert [w["w"] for w in plan_caption_words(store, store.get_clip_plan(plan["id"]))] == ["раз"]

    # Снять скорость: слова вернулись на прежние места, длина — прежняя.
    out, ok = run_tool(ctx, "set_segment_speed", {"piece": 0, "speed": 1})
    assert ok and out["length"] == 10.0
    words = {w["word"]: w for w in store.get_clip_plan_subtitles(plan["id"])["words"]}
    assert words["раз"]["start"] == pytest.approx(5.0) and words["два"]["start"] == pytest.approx(12.0)
    assert store.get_clip_plan(plan["id"])["render_settings"]["speed"] == [
        {"piece": plan["segments"][1]["id"], "reverse": True}]

    # Ошибки — понятным текстом.
    assert not run_tool(ctx, "set_segment_speed", {"piece": 0, "speed": 5})[1]
    assert not run_tool(ctx, "set_segment_speed", {"piece": 0, "speed": 0.5, "audio": "reverse"})[1]
    assert not run_tool(ctx, "set_segment_speed", {"piece": 9, "speed": 0.5})[1]
    assert "set_segment_speed" in [t.name for t in [get_tool("set_segment_speed")]]


def test_reverse_is_limited_to_short_pieces(tmp_path, monkeypatch):
    store, _service, plan, _preset = _two_pieces(tmp_path, monkeypatch, pieces=((0, 12), (30, 40)))
    out, ok = run_tool(_ctx(store, plan["id"]), "set_segment_speed", {"piece": 0, "reverse": True})
    assert not ok and "10" in out["error"]


def test_editor_autosave_keeps_speed(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import app.main as app_main
    from app.settings import settings

    store, _service, plan, _preset = _two_pieces(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "auth_enabled", False)
    monkeypatch.setattr(app_main, "store", store)
    run_tool(_ctx(store, plan["id"]), "set_segment_speed", {"piece": 0, "speed": 2})
    client = TestClient(app_main.app)
    resp = client.patch(f"/api/clip-plans/{plan['id']}/render-settings", json={"settings": {"mirror": True}})
    assert resp.status_code == 200, resp.text
    assert store.get_clip_plan(plan["id"])["render_settings"]["speed"] == [
        {"piece": plan["segments"][0]["id"], "speed": 2.0}]
    assert store.get_clip_plan(plan["id"])["segments"][0]["speed"] == 2.0


# ------------------------------------------------------------------ рендер


def test_render_routes_retimed_piece_through_parts_with_filters(tmp_path, monkeypatch):
    store, service, plan, preset, calls = _mock_render(tmp_path, monkeypatch, [(0, 6)])
    seg_id = plan["segments"][0]["id"]
    store.set_clip_plan_render_settings(plan["id"], {"speed": [{"piece": seg_id, "speed": 0.5}]})
    service.render_clip_plan(plan["id"], ffmpeg_preset_id=preset["id"])
    part = next(c for c in calls if any("part-000" in str(a) for a in c))
    graph = part[part.index("-filter_complex") + 1]
    assert "setpts=(PTS-STARTPTS)/0.5" in graph and "atempo=0.5" in graph
    assert part[part.index("-frames:v") + 1] == str(12 * 25)  # 6 с исходника → 12 с на 25 к/с
    assert part[part.index("-t") + 1].startswith("6")  # читаем секунды исходника


def _make_ramp(path: Path, seconds: float = 2.0) -> None:
    """Синтетика: яркость растёт со временем (по ней видно направление) + тон 440 Гц."""
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"color=c=black:s=320x240:r=30:d={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={seconds}",
         "-filter_complex", "[0:v]geq=lum='min(250,T*110)':cb=128:cr=128,format=yuv420p[v]",
         "-map", "[v]", "-map", "1:a", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
         str(path)], check=True, capture_output=True)


def _probe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration", "-of", "json",
                          str(path)], check=True, capture_output=True, text=True).stdout
    return {s["codec_type"]: float(s["duration"]) for s in json.loads(out)["streams"]}


def _luma(path: Path, at: float) -> float:
    out = subprocess.run(["ffmpeg", "-hide_banner", "-ss", f"{at:.3f}", "-i", str(path), "-frames:v", "1",
                          "-vf", "signalstats,metadata=print:key=lavfi.signalstats.YAVG", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    return float(out.split("lavfi.signalstats.YAVG=")[-1].split()[0])


def _peak_db(path: Path) -> float:
    out = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-af", "volumedetect", "-vn", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    return float(out.split("max_volume:")[-1].split("dB")[0])


@pytest.mark.parametrize("seg, length", [
    ({"id": 1, "start_sec": 0.0, "end_sec": 2.0, "speed": 0.5, "speed_audio": "tempo"}, 4.0),
    ({"id": 1, "start_sec": 0.0, "end_sec": 2.0, "speed": 2.0, "speed_audio": "pitch"}, 1.0),
    ({"id": 1, "start_sec": 0.0, "end_sec": 2.0, "reverse": True, "speed_audio": "mute"}, 2.0),
])
def test_real_ffmpeg_speed_and_reverse(tmp_path, seg, length):
    _require_ffmpeg()
    from app.render import _piece_retime

    src, out = tmp_path / "ramp.mp4", tmp_path / "out.mp4"
    _make_ramp(src)
    preset = {"output_width": 180, "output_height": 320, "fps": 30,
              "piece_retime": _piece_retime(seg, {"metadata_json": None})}
    subprocess.run(build_ffmpeg_render_args(src, out, 0.0, 2.0, preset), check=True, capture_output=True)
    durations = _probe(out)
    assert durations["video"] == pytest.approx(length, abs=0.1)
    assert durations["audio"] == pytest.approx(length, abs=0.15)
    first, last = _luma(out, 0.05), _luma(out, length - 0.1)
    if seg.get("reverse"):
        assert first > last + 50  # задом наперёд: было светлое в конце — стало в начале
        assert _peak_db(out) < -60  # звук реверса по умолчанию — тишина
    else:
        assert last > first + 50
        assert _peak_db(out) > -20


def test_real_render_of_clip_with_slow_and_reversed_pieces(tmp_path, monkeypatch):
    """Весь рендер клипа: кусок A (0–5 с) ×0.5 и развёрнутый B (1–6 с) с наплывом — длина ролика
    = 10 + 5 − наплыв; одиночный кусок ×2 идёт путём частей и выходит вдвое короче."""
    _require_ffmpeg()
    from app.render import ClipRenderService
    from app.transitions import part_overlaps
    from test_clip_spec import _setup

    _client, store, source, plan, a, b = _setup(tmp_path, monkeypatch)
    preset = store.create_ffmpeg_preset("S", output_width=180, output_height=320, fps=25)
    fade = {"type": "fade", "duration": 0.4, "smart": False}
    store.set_clip_plan_render_settings(plan["id"], {"transition": fade, "end_hold_sec": 0, "speed": [
        {"piece": a["id"], "speed": 0.5}, {"piece": b["id"], "reverse": True}]})
    fresh = store.get_clip_plan(plan["id"])
    overlap = part_overlaps(fresh["segments"], fade)[0]
    assert overlap > 0
    service = ClipRenderService(store)
    clip = service.render_clip_plan(plan["id"], ffmpeg_preset_id=preset["id"], transition=fade)
    assert clip["status"] == "succeeded", clip.get("error")
    durations = _probe(Path(clip["output_path"]))
    assert durations["video"] == pytest.approx(10.0 + 5.0 - overlap, abs=0.2)
    assert clip_timeline(store, plan["id"])["duration"] == pytest.approx(10.0 + 5.0 - overlap, abs=0.01)

    single = store.create_clip_plan(source["id"], plan["analysis_id"], "Один", segment_ids=[a["id"]])
    store.set_clip_plan_render_settings(single["id"], {"end_hold_sec": 0, "speed": [{"piece": a["id"], "speed": 2}]})
    clip = service.render_clip_plan(single["id"], ffmpeg_preset_id=preset["id"])
    assert clip["status"] == "succeeded", clip.get("error")
    assert _probe(Path(clip["output_path"]))["video"] == pytest.approx(2.5, abs=0.15)
