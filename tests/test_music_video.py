# -*- coding: utf-8 -*-
"""tools/music_video: привязка слов к атакам вокала, слоги, ссылки на время в плане."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "music_video"))

from audio import beat_grid, snap_words, syllables  # noqa: E402
from resolve import Clock, syl_groups  # noqa: E402


def test_snap_prefers_loud_onset_over_breath():
    # Whisper сказал 2.08, но громкая атака слога — 2.31; тихая 2.08 (−36 дБ) — дыхание/хвост
    words = [{"w": "гоу", "s": 2.10, "e": 2.6, "p": 0.9}]
    onsets = [[2.08, 0.39, -36.1], [2.31, 0.53, -19.0], [2.52, 0.36, -24.2]]
    out = snap_words(words, onsets)
    assert out[0]["on"] == 2.31


def test_snap_collects_syllables_and_does_not_share_onsets():
    words = [{"w": "медведь", "s": 65.8, "e": 66.2, "p": 1.0}, {"w": "подойдёт", "s": 66.25, "e": 66.9, "p": 1.0}]
    onsets = [[65.84, 0.6, -13], [66.05, 0.5, -14], [66.33, 0.7, -12], [66.55, 0.5, -13], [66.80, 0.5, -14]]
    out = snap_words(words, onsets)
    assert out[0]["syl"] == [65.84, 66.05]
    assert out[1]["on"] == 66.33 and out[1]["syl"] == [66.33, 66.55, 66.80]


def test_syllables_and_groups():
    assert syllables("огородником") == 5
    assert syl_groups("ОГОРОДНИКОМ", 5) == ["О", "ГО", "РО", "ДНИ", "КОМ"]
    assert "".join(syl_groups("МЕДВЕДЬ", 2)) == "МЕДВЕДЬ"
    assert syl_groups("ЛЕС", 1) == ["ЛЕС"]


def test_clock_refs():
    an = {"start": 0.0, "end": 99.0, "period": 0.5, "beats": [0.024 + 0.5 * i for i in range(198)],
          "words": [{"w": "медведь", "on": 65.84, "syl": [65.84, 66.05]}]}
    c = Clock(an, 30, {"0": {"text": "МЕДВЕДЬ"}})
    assert abs(c.t("w:0") - 65.84) < 1e-9
    assert abs(c.t("w:0.1") - 66.05) < 1e-9
    assert abs(c.t("w:0-0.1") - 65.74) < 1e-9
    assert abs(c.t("b:4") - 2.024) < 1e-9
    assert c.f("w:0") == round(65.84 * 30)
    assert c.snap_beat(2.1) == 2.024
    assert c.word_text(0) == "МЕДВЕДЬ"


def test_beat_grid_finds_tempo():
    sr = 22050
    x = np.zeros(sr * 12, np.float32)
    for k in range(24):                       # щелчки 120 BPM, начиная с 0.25 с
        i = int((0.25 + k * 0.5) * sr)
        x[i:i + 200] = np.hanning(200) * 0.9
    g = beat_grid(x, sr, 0.0, 12.0)
    assert abs(g["bpm"] - 120) < 1.0
    assert min(abs(b - 0.25) for b in g["beats"]) < 0.03
