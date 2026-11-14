"""Серия 9 «Караоке при свечах»: героиня и подруга — разные модели каста (не перекраска), подруга в войсе на эфире
рядом с героиней, слои караоке поверх аватара (сайлиум, строка песни, палочки зала) идут от события сцены, звуки зала
— записи пака целиком (4 с, без обрыва на 2.5 с)."""

from pathlib import Path

from app import vtubika as U
from app import vtubika_plan as P
from app import vtubika_vrm_stage as VS
from app.vtubika_vrm import to_vrm_spec

ROOT = Path(__file__).resolve().parent.parent
PLAN = ROOT / "tools" / "vtubika_plans" / "karaoke.json"


def _words(plan):
    toks = P.tokens(P.caption_text(plan))
    return [{"word": w, "start": round(0.4 + i * 0.42, 3), "end": round(0.4 + i * 0.42 + 0.36, 3)} for i, w in enumerate(toks)]


def _built():
    plan = P.load_plan(PLAN)
    words = _words(plan)
    total = round(words[-1]["end"] + float(plan["tail"]), 3)
    return plan, words, total, P.build(plan, words, total)


def test_cast_models_differ():
    plan, words, total, built = _built()
    assert VS.heroine(plan)["model"] == VS.CAST["lilya"]["model"]
    spec = to_vrm_spec(plan, built, words, total)
    models = {m["id"]: m for m in spec["avatar"]["models"]}
    assert models["lilya_av"]["model"] == VS.CAST["lilya"]["model"]
    assert models["varya_av"]["model"] == VS.CAST["varya"]["model"] != models["lilya_av"]["model"]
    assert models["lilya_av"]["x"] < 0 < models["varya_av"]["x"]


def test_karaoke_layers_are_timed_from_scene_event():
    plan, words, total, built = _built()
    scenes = to_vrm_spec(plan, built, words, total)["stream"]["scenes"]
    karaoke = next(s for s in scenes if len(s.get("front") or []) == 3)
    sticks, lyr, say = karaoke["front"]  # палочки зала — под строкой песни, сайлиум в руке — поверх
    assert "saylium_pink" in say["seq"][0] and "sticks_violet" in sticks["seq"][0] and "lyrics" in lyr["seq"][0] and len(lyr["seq"]) == 220
    assert lyr["t0"] == karaoke["t"]  # строка песни — с начала сцены
    assert sticks["t0"] > lyr["t0"] and say["keys"][0]["t"] > karaoke["t"]


def test_crowd_sounds_play_whole():
    _plan, _words_, _total, built = _built()
    crowd = [s for s in built["sfx"] if s["name"] in ("cheer", "applause")]
    assert {s["name"] for s in crowd} == {"cheer", "applause"}
    assert all(s["dur"] == U.SFX_DUR[s["name"]] == 4.0 for s in crowd)
    assert all("dur" not in s for s in built["sfx"] if s["name"] not in U.SFX_DUR)
