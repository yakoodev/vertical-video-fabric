"""История «Витубики» → спека VRM-рендера: кадры покрывают ролик, общий план в начале и в конце, вставки без героини,
экран монитора и оверлеи — из собранной сцены, проверки касаний включены."""

from pathlib import Path

from app import vtubika_plan as P
from app.vtubika_vrm import INSERTS, to_vrm_spec

ROOT = Path(__file__).resolve().parent.parent


def _built():
    plan = P.load_plan(ROOT / "tools" / "vtubika_plans" / "fox.json")
    toks = P.tokens(P.caption_text(plan))
    words = [{"word": w, "start": round(0.9 + i * 0.4, 3), "end": round(0.9 + i * 0.4 + 0.35, 3)} for i, w in enumerate(toks)]
    total = round(words[-1]["end"] + 2.6, 3)
    return plan, P.build(plan, words, total), words, total


def test_shots_cover_story_and_start_end_wide():
    plan, built, words, total = _built()
    spec = to_vrm_spec(plan, built, words, total)
    shots = spec["shots"]
    assert shots[0]["t0"] == 0 and abs(shots[-1]["t1"] - total) < 1e-6
    for a, b in zip(shots, shots[1:]):
        assert abs(a["t1"] - b["t0"]) < 1e-6
    assert shots[0]["plate"]["kind"] == "wide" and shots[-1]["plate"]["kind"] == "wide"


def test_inserts_overlays_screen_and_checks():
    plan, built, words, total = _built()
    spec = to_vrm_spec(plan, built, words, total)
    kinds = {s["plate"].get("insert", {}).get("kind") for s in spec["shots"]} - {None}
    assert kinds and kinds <= INSERTS and {"tablet", "jar", "monitor"} <= kinds
    assert any(o["kind"] == "phone" for o in spec["overlays"]) and any(o["kind"] == "hud" for o in spec["overlays"])
    assert spec["stream"]["modes"] and spec["stream"]["chat"]
    assert spec["strictContact"] and spec["timeline"]["chair"] and spec["timeline"]["surfaces"]


def test_mouth_only_while_she_talks():
    plan, built, words, total = _built()
    spec = to_vrm_spec(plan, built, words, total)
    faces = built["spec"]["characters"][0]["faces"]
    talk = [(f["t"], faces[i + 1]["t"] if i + 1 < len(faces) else total) for i, f in enumerate(faces) if f["face"] == "talk"]
    for a, _b in spec["timeline"]["speech"]:
        assert any(x <= a < y for x, y in talk)
