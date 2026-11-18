"""Монтаж прямо в редакторе: секунда ролика ⇄ секунда клипа на таймлайне (preview/montageClock.ts)."""

from __future__ import annotations

import json

from app.settings import settings
from test_api_pipeline import _require_ffmpeg
from test_clip_spec import _setup
from test_frontend_mute import _run_node
from test_playback import _set

CLOCK_TS = settings.root_dir / "frontend" / "src" / "pages" / "workspace" / "preview" / "montageClock.ts"


def _clock(plan: dict, pieces: list[dict], to_clip: list[float], to_montage: list[float]) -> dict:
    script = f"""
import {{ montageToClip, clipToMontage }} from {json.dumps(CLOCK_TS.as_uri())};
const plan = {json.dumps(plan)};
const pieces = {json.dumps(pieces)};
const r = (x) => x == null ? null : Math.round(x * 1e4) / 1e4;
console.log(JSON.stringify({{
  clip: {json.dumps(to_clip)}.map((t) => r(montageToClip(plan, pieces, t))),
  montage: {json.dumps(to_montage)}.map((c) => r(clipToMontage(plan, pieces, c))),
  none: [montageToClip(null, pieces, 1), clipToMontage({{ parts: [] }}, pieces, 1)],
}}));
"""
    return _run_node(script)


def test_cuts_speed_reverse_and_freeze_map_onto_the_clip_timeline():
    # Куски клипа: 5 с, 4 с (8 с записи на ×2), 2 с реверсом. Таймлайн: 0–5, 5–9, 9–11.
    pieces = [{"start_sec": 10, "end_sec": 15}, {"start_sec": 100, "end_sec": 108, "speed": 2}, {"start_sec": 200, "end_sec": 202}]
    plan = {
        "parts": [
            # Вырез 1–2 с внутри первого куска: две части встык, потом наплыв 0.5 с на второй кусок.
            {"start": 0, "end": 1, "src_start": 10, "src_end": 11, "speed": 1, "reverse": False, "piece": 0},
            {"start": 1, "end": 4, "src_start": 12, "src_end": 15, "speed": 1, "reverse": False, "piece": 0},
            {"start": 3.5, "end": 7.5, "src_start": 100, "src_end": 108, "speed": 2, "reverse": False, "piece": 1},
            {"start": 7.5, "end": 9.5, "src_start": 200, "src_end": 202, "speed": 1, "reverse": True, "piece": 2},
        ],
        # Вставленный стоп-кадр 1 с на секунде основы 2.
        "freezes_raw": [{"at": 2, "duration": 1, "audio": "silence", "layers": "freeze"}],
        "base_duration": 9.5,
    }
    got = _clock(plan, pieces, [0.5, 1.5, 2.5, 4.0, 5.0, 9.0, 99], [0.5, 1.5, 2.5, 3.0, 4.5, 5.5, 9.5, 11])
    # Ролик → клип: после выреза — на секунду дальше, на стоп-кадре стоим, реверс идёт по таймлайну вперёд.
    assert got["clip"] == [0.5, 2.5, 3.0, 4.0, 5.5, 9.5, 11.0]
    # Клип → ролик: вырезанное место — начало следующей части, ровно на стоп-кадре — его начало.
    assert got["montage"] == [0.5, 1.0, 1.5, 2.0, 4.5, 5.0, 9.0, 10.5]
    assert got["none"] == [None, None]


def test_round_trip_on_a_real_playback_plan(tmp_path, monkeypatch):
    """План сервера (тот же код, что у рендера): вырез, наплыв склейки и стоп-кадр."""
    _require_ffmpeg()
    client, _store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    _set(client, plan["id"], end_hold_sec=0, cuts=[{"start": 1, "end": 2}],
         transition={"type": "fade", "duration": 0.5, "smart": False},
         freezes=[{"at": 3, "duration": 1, "audio": "silence", "layers": "freeze"}])
    got_plan = client.get(f"/api/clip-plans/{plan['id']}/playback").raise_for_status().json()
    pieces = [{"start_sec": 0, "end_sec": 5}, {"start_sec": 1, "end_sec": 6}]  # таймлайн 0–5, 5–10
    clip_times = [0.5, 2.5, 4.0, 6.0, 8.0, 9.9]
    first = _clock(got_plan, pieces, [], clip_times)
    back = _clock(got_plan, pieces, first["montage"], [])
    # Вне выреза и наплыва секунда клипа возвращается той же.
    assert back["clip"] == clip_times
    # Конец ролика — конец клипа; ролик короче клипа на вырез и наплыв и длиннее на стоп-кадр.
    end = _clock(got_plan, pieces, [got_plan["duration"]], [10])
    assert end["clip"] == [10.0]
    assert abs(end["montage"][0] - got_plan["duration"]) < 1e-3
    assert abs(got_plan["duration"] - (10 - 1 - 0.5 + 1)) < 1e-3
