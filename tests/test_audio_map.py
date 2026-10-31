"""Карта звука для агента (audio_map): громкость, речь, тишина, удары, паузы, склейки, J/L, доли —
на синтетике (тон, тишина, шум, щелчки, клик-трек), речь подменяется готовыми отрезками."""

from __future__ import annotations

import json
import subprocess

import numpy as np
import pytest

from app.audio_map import (
    RATE,
    analyze_pcm,
    beat_grid,
    build_view,
    choose_step,
    cut_points,
    find_pauses,
    jl_candidates,
    sparkline,
)


def _tone(sec, freq=220.0, amp=0.3):
    t = np.arange(int(sec * RATE)) / RATE
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _silence(sec):
    return np.zeros(int(sec * RATE), np.float32)


def _clicks(sec, times, amp=0.9, floor=0.003):
    rng = np.random.default_rng(1)
    x = (floor * rng.standard_normal(int(sec * RATE))).astype(np.float32)
    for t in times:
        i = int(t * RATE)
        x[i:i + 160] += amp * np.hanning(160).astype(np.float32) * np.sign(np.sin(np.arange(160)))
    return x


def _synthetic():
    rng = np.random.default_rng(0)
    speech = _tone(2.0, 180) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * np.arange(int(2 * RATE)) / RATE)).astype(np.float32)
    noise = (0.5 * rng.standard_normal(int(2 * RATE))).astype(np.float32)
    x = np.concatenate([speech, _silence(1.0), noise, _clicks(2.0, [0.5, 1.5], floor=0.02), _tone(1.0, 200)])
    return x, [(0.0, 2.0), (7.0, 8.0)]


def test_analyze_finds_silence_loud_hits_and_pauses():
    x, speech = _synthetic()
    raw = analyze_pcm(x, speech=speech)
    labels = {lab for _a, _b, lab in raw["spans"]}
    assert {"речь", "тишина", "громко"} <= labels
    silent = [s for s in raw["spans"] if s[2] == "тишина"]
    assert any(a <= 2.2 and b >= 2.8 for a, b, _l in silent)
    hit_times = [h["t"] for h in raw["hits"]]
    for want in (3.0, 5.5, 6.5):  # шум включился, два щелчка
        assert any(abs(t - want) <= 0.12 for t in hit_times), (want, hit_times)
    [pause] = raw["pauses"]
    assert (pause["start"], pause["end"], pause["len"]) == (2.0, 7.0, 5.0) and pause["event"].startswith("удар 2.9")
    assert raw["info"]["speech_share"] == pytest.approx(3 / 8, abs=0.03)


def test_pause_before_punch_and_cut_points():
    feat_x = np.concatenate([_tone(1.0, 200, 0.05), _silence(0.5), _tone(1.0, 200, 0.4)])
    raw = analyze_pcm(feat_x, speech=[(0.0, 1.0), (1.5, 2.5)])
    assert raw["pauses"][0]["before_punch"] is True  # пауза 0.5 с, за ней фраза громче
    cuts = cut_points([(0.0, 1.0), (1.5, 2.5)], None, 2.5)
    assert cuts[0] == {"t": 1.1, "why": "конец фразы +3 кадра (пауза 0.50 с)", "gap": 0.5}
    assert any(c["t"] == pytest.approx(1.43, abs=0.01) for c in cuts)  # начало фразы −2 кадра
    # По словам субтитров — точнее: конец слова + 3 кадра, но не в следующее слово.
    words = [{"start": 0.1, "end": 0.9}, {"start": 0.98, "end": 1.2}, {"start": 1.5, "end": 2.0}]
    cuts = cut_points([], words, 2.5)
    assert [c["t"] for c in cuts if "конец" in c["why"]] == [1.3, 2.1]
    assert find_pauses([(0, 1)], 3.0)[-1]["tail"] is True


def test_beat_grid_on_a_click_track():
    period = 0.5  # 120 BPM, первая доля на 0.2 с
    x = _clicks(16.0, [0.2 + k * period for k in range(32)], amp=0.6)
    grid = beat_grid(x)
    assert grid is not None and grid["bpm"] == pytest.approx(120, abs=1.5)
    phase = (grid["first_beat"] - 0.2) % period
    assert min(phase, period - phase) < 0.03
    assert grid["downbeat_every"] == 4 and grid["first_downbeat"] >= grid["first_beat"]
    assert beat_grid(_silence(10.0)) is None and beat_grid(_tone(3.0)) is None


def test_view_is_compact():
    x, speech = _synthetic()
    raw = analyze_pcm(np.tile(x, 7), speech=[(a + 8 * k, b + 8 * k) for k in range(7) for a, b in speech])
    view = build_view(raw)
    assert view["loudness"]["step"] == choose_step(56.0) == 0.25
    assert len(view["loudness"]["lufs"]) == len(view["loudness"]["line"]) == 224
    assert set(view["loudness"]["line"]) <= set("▁▂▃▄▅▆▇█")
    assert len(json.dumps(view, ensure_ascii=False)) < 9000
    assert sparkline([-60, -10], -50, -10) == "▁█"


def test_jl_candidates_suggest_l_j_and_nothing():
    base = {"at": 5.0, "piece_a": 0, "piece_b": 1, "b_hit": None}
    l_cut = jl_candidates([{**base, "join": 0, "a_tail_speech": 0.3, "b_head_speech": None, "after_a": 0.6,
                            "before_b": 0.0}])[0]
    assert l_cut["suggest"] == {"piece": 0, "lag": 0.7}
    j_cut = jl_candidates([{**base, "join": 0, "a_tail_speech": 0.0, "b_head_speech": 0.02, "after_a": 0.0,
                            "before_b": 0.8}])[0]
    assert j_cut["suggest"] == {"piece": 1, "lead": 0.8}
    both = jl_candidates([{**base, "join": 0, "a_tail_speech": 0.3, "b_head_speech": 0.1, "after_a": 0.0,
                           "before_b": 0.0}])[0]
    assert "suggest" not in both and "закроет" in both["why"]
    hit = jl_candidates([{**base, "join": 0, "a_tail_speech": 0.0, "b_head_speech": None, "after_a": 0.0,
                          "before_b": 0.0, "b_hit": 0.2}])[0]
    assert hit["suggest"] == {"piece": 1, "lead": 0.35}


def test_audio_map_tool_on_a_real_two_track_file(tmp_path, monkeypatch):
    from test_api_pipeline import _client, _require_ffmpeg

    from app.agent_tools import ImageResult, ToolContext, run_tool
    from app.settings import settings

    _require_ffmpeg()
    _client_, store = _client(tmp_path, monkeypatch)
    path = settings.source_dir / "two.mkv"
    # Игра: тон и щелчок на 6 с; микрофон: «речь» 1–3 с и 9–11 с.
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=64x64:d=14:r=25",
                    "-f", "lavfi", "-i", "sine=f=150:d=14,volume=0.1",
                    "-f", "lavfi", "-i", "sine=f=400:d=14,volume='between(t,1,3)+between(t,9,11)':eval=frame",
                    "-map", "0", "-map", "1", "-map", "2", "-c:v", "libx264", "-c:a", "pcm_s16le", str(path)],
                   check=True)
    meta = {"streams": [{"codec_type": "video"}, {"codec_type": "audio"}, {"codec_type": "audio"}]}
    source = store.create_source("upload", path, duration_sec=14, width=64, height=64, status="ready")
    store.update_source(source["id"], metadata=meta, audio_tracks=[0, 1], speech_tracks=[1])
    analysis = store.create_ai_analysis(source["id"], "mock", status="succeeded")
    segs = [store.create_ai_segment(analysis["id"], {"start_sec": a, "end_sec": b, "title": "P"})
            for a, b in ((0.5, 6.0), (8.0, 13.5))]
    plan = store.create_clip_plan(source["id"], analysis["id"], "План", segment_ids=[s["id"] for s in segs])
    speech = [(0.5, 2.5), (6.5, 8.5)]  # куски подряд: 1–3 с исходника и 9–11 с
    monkeypatch.setattr("app.audio_map.vad_segments",
                        lambda x, rate=16000: ([s for s in speech if s[1] <= len(x) / rate + 0.01]
                                               if len(x) > rate * 5 else [], "given"))
    ctx = ToolContext(store=store, clip_plan_id=plan["id"])
    out, ok = run_tool(ctx, "audio_map", {})
    assert ok, out
    assert out["time"] == "clip" and out["clip_duration"] == 11.0
    assert set(out["tracks"]) == {"0", "1"} and out["tracks"]["1"]["speech_share"] > 0.3
    assert out["speech"][0] == [0.5, 2.5]
    assert len(out["jl"]) == 1 and out["jl"][0]["at"] == 5.5
    assert len(json.dumps(out, ensure_ascii=False)) < 9000
    cached = list((settings.runtime_dir / "audio_map").glob("*.json"))
    assert cached
    again, ok = run_tool(ctx, "audio_map", {"from_sec": 4.0, "to_sec": 8.5})
    assert ok and again["window"] == [4.0, 8.5] and all(p["end"] > 4.0 for p in again["pauses"])
    picture, ok = run_tool(ctx, "audio_map", {"image": True})
    assert ok and isinstance(picture, ImageResult) and picture.data[:4] == b"\x89PNG"
    src, ok = run_tool(ctx, "audio_map", {"source": True, "from_sec": 0, "to_sec": 12, "track": 1})
    assert ok and src["time"] == "source" and src["from_sec"] == 0.0
