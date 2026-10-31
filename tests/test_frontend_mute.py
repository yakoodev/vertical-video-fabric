"""Редактор и рендер одинаково понимают render.mute.

Фронт (frontend/src/api/mute.ts) сам сливает отрезки, когда их тянут мышью по
дорожке «Звук», и глушит превью внутри них. Если его нормализация разойдётся с
серверной (app/audio_mute.py), превью будет молчать не там, где рендер.
Модуль без импортов — node (≥22.6, снятие типов) запускает его как есть.
"""

import json
import shutil
import subprocess

import pytest

from app.audio_mute import normalize_mute
from app.settings import settings

MUTE_TS = settings.root_dir / "frontend" / "src" / "api" / "mute.ts"

CASES = [
    [],
    [{"start": 3, "end": 4}, {"start": 1, "end": 2}, {"start": 1.5, "end": 2.5}, {"start": 5, "end": 5.01}],
    [{"start": 2, "end": 3}, {"start": 3, "end": 3.5}],  # стык сливается
    [{"start": -1, "end": 0.5}],  # отрицательное начало — с нуля
    [{"start": 4, "end": 2}],  # перевёрнутый — выброшен, как на сервере
    [{"start": i, "end": i + 0.5} for i in range(30)],  # больше MAX_MUTES
    [{"start": 1.23456, "end": 2.34567}],
    # Отрезки цензуры (by/level) не сливаются ни с ручными, ни между собой; ручные — как прежде.
    [{"start": 1, "end": 2}, {"start": 1.5, "end": 1.7, "by": "censor", "level": 2},
     {"start": 1.6, "end": 2.5}, {"start": 1.65, "end": 1.9, "by": "censor", "level": 9},
     {"start": 1.5, "end": 1.7, "by": "censor", "level": 2}, {"start": 3, "end": 3.2, "by": "other"}],
    [{"start": i * 0.4, "end": i * 0.4 + 0.2, "by": "censor", "level": 1} for i in range(70)],
    # Тишина одной дорожки (track): сливается только со своей дорожкой, мат дорожки — отдельно.
    [{"start": 1, "end": 2, "track": 1}, {"start": 1.5, "end": 3, "track": 1}, {"start": 1.2, "end": 1.8},
     {"start": 1.9, "end": 2.2, "track": 0}, {"start": 1, "end": 2, "by": "censor", "level": 2, "track": 1},
     {"start": 1, "end": 2, "by": "censor", "level": 2}, {"start": 4, "end": 5, "track": 9}],
]


def _run_node(script: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node не установлен")
    proc = subprocess.run(
        [node, "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script],
        capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0 and "strip-types" in (proc.stderr or ""):
        pytest.skip("node без поддержки TypeScript (нужен ≥22.6)")
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_frontend_mute_matches_server():
    script = f"""
import {{ normalizeMute, addMute, isMutedAt, muteTotal }} from {json.dumps(MUTE_TS.as_uri())};
const cases = {json.dumps(CASES)};
const spans = normalizeMute([{{ start: 1, end: 2 }}, {{ start: 5, end: 6 }}]);
console.log(JSON.stringify({{
  normalized: cases.map((c) => normalizeMute(c)),
  added: addMute(spans, 2.5, 1.5),
  addedReversed: addMute([], 8, 7),
  inside: [0.99, 1, 1.5, 1.999, 2, 5.5].map((t) => isMutedAt(spans, t)),
  total: muteTotal(spans),
}}));
"""
    out = _run_node(script)
    assert out["normalized"] == [normalize_mute(c) for c in CASES]
    assert out["added"] == [{"start": 1, "end": 2.5}, {"start": 5, "end": 6}]
    assert out["addedReversed"] == [{"start": 7, "end": 8}]
    # Конец отрезка — уже со звуком: [start, end).
    assert out["inside"] == [False, True, True, True, False, True]
    assert out["total"] == 2
