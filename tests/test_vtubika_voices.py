"""Рот по своему голосу (app/vtubika_voices.py): героиня говорит своими репликами, рассказчик ей рот не двигает;
модель напарницы на мониторе — гласные по её голосу."""

from pathlib import Path

import numpy as np

from app import series3d_voice as VF
from app import vtubika_voices as V


def _voice(path: Path, dur: float) -> Path:
    t = np.arange(int(dur * VF.SR)) / VF.SR
    # «Слоги»: гласный тон с огибающей 4 Гц (громко — тихо), чтобы у голоса были громкие кадры.
    pcm = 0.3 * np.sin(2 * np.pi * 220 * t) * (0.55 + 0.45 * np.sin(2 * np.pi * 4 * t)) * np.sin(2 * np.pi * 900 * t + 1)
    VF._write_wav(path, pcm.astype(np.float32))
    return path


def _words(text: str, dur: float) -> list[dict]:
    toks = text.split()
    step = (dur - 0.2) / len(toks)
    return [{"word": w, "start": round(0.1 + i * step, 3), "end": round(0.1 + (i + 0.85) * step, 3)} for i, w in enumerate(toks)]


def test_hero_and_avatar_mouths_follow_their_own_lines(tmp_path):
    nar = _voice(tmp_path / "nar.wav", 12.0)
    lines = [
        {"id": "kat", "who": "kat_av", "t": 3.0, "dur": 1.6, "path": str(_voice(tmp_path / "kat.wav", 1.6)), "text": "Кампай за коллаб"},
        {"id": "mira", "who": "hero", "t": 6.0, "dur": 1.2, "path": str(_voice(tmp_path / "mira.wav", 1.2)), "text": "Кампай"},
    ]
    texts = {str(tmp_path / "kat.wav"): ("Кампай за коллаб", 1.6), str(tmp_path / "mira.wav"): ("Кампай", 1.2)}
    words = [{"word": "Рассказчик", "start": 0.4, "end": 1.0}, {"word": "говорит", "start": 1.1, "end": 1.8},
             {"word": "дальше", "start": 8.5, "end": 9.2}]
    spec = {"timeline": {"speech": []}, "avatar": {"models": [{"id": "kat_av"}], "talk": []}}
    rep = V.attach({"vrm": {}}, spec, lines, nar, words, 12.0, 0.0, tmp_path,
                   lambda p: _words(*texts[str(p)]))
    vis = spec["timeline"]["visemes"]
    fps = vis["fps"]
    opened = lambda v, t: sum(v[k][int(t * fps)] for k in ("aa", "ih", "ou", "ee", "oh"))  # noqa: E731
    # Героиня: рот в своей реплике, закрыт на словах рассказчика.
    assert max(opened(vis, 6.0 + k / 30) for k in range(30)) > 0.1
    assert max(opened(vis, 0.4 + k / 30) for k in range(40)) == 0
    assert rep["hero"]["windows"][0][0] >= 6.0
    # Напарница на мониторе: гласные по её голосу в её окне, вне — рот закрыт.
    kv = spec["avatar"]["visemes"]["kat_av"]
    assert max(opened(kv, 3.0 + k / 30) for k in range(45)) > 0.1
    assert max(opened(kv, 6.0 + k / 30) for k in range(30)) == 0
    assert [w["t0"] >= 3.0 for w in spec["avatar"]["talk"]] == [True]


def test_without_own_lines_narrator_path_unchanged(tmp_path):
    nar = _voice(tmp_path / "nar.wav", 4.0)
    words = [{"word": "всем", "start": 0.5, "end": 0.9}, {"word": "привет", "start": 1.0, "end": 1.6}]
    spec = {"timeline": {"speech": [[0.4, 1.7]]}}
    rep = V.attach({"vrm": {}}, spec, [], nar, words, 4.0, 0.0, tmp_path, lambda p: [])
    assert rep == {} and spec["timeline"].get("visemes")
