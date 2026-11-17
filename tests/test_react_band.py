"""Реакция на звук по полосе частот (``react.band``: bass/mid/high) и тряска от звука (``prop shake``).

Огибающая полосы — app/audio_react.py (звук куска фильтруется в спектре, дальше та же громкость кадров и та же
огибающая), ключ в ``comp.levels`` — ``источник:полоса``; движок (frontend/src/motion/levels.ts) читает его по
``reactLevelKey`` — превью и рендер одним массивом.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from app import motion
from app.audio_react import band_filter, band_levels_for_points, level_at, plan_levels
from app.motion import normalize_layers, validate_layers
from app.pngtuber import PCM_RATE, levels_for_points


def _text(**extra):
    return {"id": "t", "type": "text", "start": 0, "end": 5, "props": {"text": "БАС"}, **extra}


def test_band_normalized_only_when_given():
    clean = normalize_layers([
        _text(react={"source": "music", "band": "bass", "prop": "shake"}),
        _text(id="b", react={"source": "music", "band": "full"}),
        _text(id="c", react={"source": "music", "band": "kick", "prop": "jitter"}),
        _text(id="d", react={"source": "voice", "band": "ультразвук"}),
        _text(id="e", react="music"),
    ])
    assert clean[0]["react"] == {"source": "music", "prop": "shake", "band": "bass", "amount": 1, "smooth": 0.15}
    assert "band" not in clean[1]["react"] and "band" not in clean[4]["react"]
    assert clean[2]["react"]["band"] == "bass" and clean[2]["react"]["prop"] == "shake"
    assert "band" not in clean[3]["react"]
    assert any("band" in p for p in validate_layers([_text(react={"source": "voice", "band": "ультразвук"})]))
    assert motion.react_sources(clean) == ["voice", "music", "music:bass"]
    assert motion.level_keys(["mix:high", "junk", "voice:loud", "music", "voice:full"]) == ["voice", "music",
                                                                                          "mix:high"]
    assert motion.parse_level_key("music:bass") == ("music", "bass") and motion.parse_level_key("wave:x") is None
    assert motion.schema_summary()["react"]["bands"] == ["full", "bass", "mid", "high"]


def _tone(freq: float, seconds: float = 1.0, amp: float = 0.5) -> np.ndarray:
    t = np.arange(int(PCM_RATE * seconds)) / PCM_RATE
    return amp * np.sin(2 * math.pi * freq * t)


def _rms(x) -> float:
    return float(np.sqrt(np.mean(np.square(x))))


def test_band_filter_keeps_its_band_only():
    bass, mid, high = _tone(60), _tone(800), _tone(3000)
    mixed = bass + mid + high
    for band, keep, drop in (("bass", bass, (mid, high)), ("mid", mid, (bass, high)), ("high", high, (bass, mid))):
        out = band_filter(mixed, PCM_RATE, band)
        assert _rms(out - keep) < 0.05 * _rms(keep), band
        assert all(abs(float(np.dot(out, d))) / len(out) < 0.01 for d in drop), band


def _reader(signal: np.ndarray):
    pcm = (np.clip(signal, -1, 1) * 32767).astype("<i2").tobytes()

    def read(_sid, start, length):
        a = max(0, int(round(start * PCM_RATE)))
        b = min(len(signal), int(round((start + length) * PCM_RATE)))
        return pcm[a * 2:b * 2]

    return read


def test_band_levels_follow_the_kick_not_the_voice():
    # 0–1 с: бас 60 Гц, 1–2 с: «голос» 1 кГц той же громкости, 2–3 с: тишина
    sig = np.concatenate([_tone(60), _tone(1000), np.zeros(PCM_RATE)])
    fps = 10.0
    points = [(1, k / fps) for k in range(30)]
    full = levels_for_points(points, fps, _reader(sig))
    bass = band_levels_for_points(points, fps, _reader(sig), "bass")
    mid = band_levels_for_points(points, fps, _reader(sig), "mid")
    assert abs(full[5] - full[15]) < 1.5                       # вся громкость не различает
    assert bass[5] > bass[15] + 30 and mid[15] > mid[5] + 30   # полосы различают
    assert band_levels_for_points(points, fps, _reader(sig), "full") == full


def test_plan_levels_band_keys(tmp_path, monkeypatch):
    from test_motion_fx import _clip

    store, _service, plan, *_ = _clip(tmp_path, monkeypatch)
    sig = np.concatenate([np.zeros(2 * PCM_RATE), _tone(70), np.zeros(PCM_RATE), _tone(1200), np.zeros(PCM_RATE)])
    monkeypatch.setattr("app.audio_react.source_reader", lambda *a, **k: _reader(sig))
    levels, _notes = plan_levels(store, plan, ["voice:bass", "voice:mid", "voice"], 10.0, "clip")
    assert set(levels) == {"voice", "voice:bass", "voice:mid"}
    assert level_at(levels["voice:bass"], 2.5) > 80 and level_at(levels["voice:bass"], 4.5) < 20
    assert level_at(levels["voice:mid"], 4.5) > 80 and level_at(levels["voice:mid"], 2.5) < 20


def test_api_accepts_band_keys(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import app.main as main_module
    from test_motion_fx import _clip

    store, _service, plan, *_ = _clip(tmp_path, monkeypatch)
    monkeypatch.setattr("app.audio_react.source_reader", lambda *a, **k: _reader(_tone(80, 6.0)))
    monkeypatch.setattr(main_module, "store", store)
    monkeypatch.setattr("app.settings.settings.auth_enabled", False)
    client = TestClient(main_module.app)
    res = client.post(f"/api/clip-plans/{plan['id']}/levels", json={"sources": ["voice:bass", "junk"], "fps": 20})
    assert res.status_code == 200, res.text
    assert set(res.json()["levels"]) == {"voice:bass"}
    store.set_clip_plan_render_settings(plan["id"], {"layers": [_text(react={"source": "voice", "band": "bass"})]})
    assert set(client.post(f"/api/clip-plans/{plan['id']}/levels", json={}).json()["levels"]) == {"voice:bass"}


@pytest.mark.parametrize("band", ["bass", "mid", "high"])
def test_band_filter_short_and_silent_input(band):
    assert band_filter(np.zeros(4), PCM_RATE, band).shape == (4,)
    assert _rms(band_filter(np.zeros(PCM_RATE), PCM_RATE, band)) == 0
