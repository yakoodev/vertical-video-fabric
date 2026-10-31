"""Редактор и рендер одинаково понимают звуковой монтаж (frontend/src/api/audioEdit.ts, gain.ts).

Превью играет J/L вторым плеером там же, где рендер наложит звук соседнего куска; ключи
громкости рисуются той же кривой; громкость одной дорожки нормализуется как на сервере.
"""

import json

from app.audio_edit import keys_value, normalize_keys, part_shifts, plan_jl
from app.audio_mute import normalize_gain
from app.settings import settings
from test_frontend_mute import _run_node

AUDIO_TS = settings.root_dir / "frontend" / "src" / "api" / "audioEdit.ts"
GAIN_TS = settings.root_dir / "frontend" / "src" / "api" / "gain.ts"

PIECES = [{"id": 7, "start_sec": 0, "end_sec": 5}, {"id": 8, "start_sec": 10, "end_sec": 16},
          {"id": 9, "start_sec": 20, "end_sec": 24}]
JOINS = [{"piece": 7, "lag": 0.5}, {"piece": 9, "lead": 0.3}]
KEYS = normalize_keys([{"t": 1, "gain_db": 0}, {"t": 3, "gain_db": -12, "ease": "smooth"},
                       {"t": 4, "gain_db": -6, "ease": "out"}, {"t": 5, "gain_db": 0, "ease": "step"}])
TIMES = [4.9, 5.0, 5.2, 5.49, 5.5, 10.6, 10.71, 10.9, 11.0, 11.1]
GAINS = [{"start": 0, "end": 2, "gain_db": -6, "track": 0}, {"start": 1, "end": 3, "gain_db": 6},
         {"start": 1.5, "end": 2.5, "gain_db": 3, "track": 0}, {"start": 4, "end": 5, "gain_db": 2, "track": 12}]


def _expected_source_at(t: float):
    """То же, что jlSourceAt, — от плана рендера (plan_jl): какой звук играет у стыка."""
    lengths = [p["end_sec"] - p["start_sec"] for p in PIECES]
    plan = plan_jl(lengths, [0.0, 0.0], part_shifts(PIECES, PIECES, JOINS))
    bound = 0.0
    for k, shift in enumerate(plan["shift"]):
        bound += lengths[k]
        if shift > 0 and bound <= t < bound + shift:
            return round(PIECES[k]["end_sec"] + t - bound, 6)
        if shift < 0 and bound + shift <= t < bound:
            return round(PIECES[k + 1]["start_sec"] - (bound - t), 6)
    return None


def test_frontend_audio_edit_matches_server():
    script = f"""
import {{ jlSourceAt, setJoinShift, joinShift, keyDbAt, maxShift, compactAudio }} from {json.dumps(AUDIO_TS.as_uri())};
import {{ normalizeGain }} from {json.dumps(GAIN_TS.as_uri())};
const pieces = {json.dumps(PIECES)};
const joins = {json.dumps(JOINS)};
const keys = {json.dumps(KEYS)};
let edited = setJoinShift(joins, 8, 9, 0.4);
console.log(JSON.stringify({{
  at: {json.dumps(TIMES)}.map((t) => jlSourceAt(pieces, joins, t)),
  shift: joinShift(joins, 7, 8),
  edited,
  cleared: setJoinShift(edited, 8, 9, 0),
  limit: maxShift({{ id: 1, start_sec: 0, end_sec: 1 }}, pieces[1]),
  keys: [0, 1, 2, 2.9, 3.5, 4.5, 4.99, 5, 9].map((t) => keyDbAt(keys, t)),
  compact: compactAudio({{ joins: [], fx: [{{ start: 1, end: 2, fx: "echo" }}] }}),
  gains: normalizeGain({json.dumps(GAINS)}),
}}));
"""
    out = _run_node(script)
    want = [_expected_source_at(t) for t in TIMES]
    assert [None if v is None else round(v, 6) for v in out["at"]] == want
    assert out["shift"] == 0.5
    assert out["edited"] == [{"piece": 7, "lag": 0.5}, {"piece": 8, "lag": 0.4}]  # lead куска 9 снят
    assert out["cleared"] == [{"piece": 7, "lag": 0.5}]
    assert out["limit"] == 0.45
    for got, t in zip(out["keys"], [0, 1, 2, 2.9, 3.5, 4.5, 4.99, 5, 9]):
        assert abs(got - keys_value(KEYS, t)) < 1e-9, t
    assert out["compact"] == {"fx": [{"start": 1, "end": 2, "fx": "echo"}]}
    assert out["gains"] == normalize_gain(GAINS)
