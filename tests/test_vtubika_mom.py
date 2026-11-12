"""Серия 7 «Лёша, ужинать»: первый герой-парень (мужская база VRoid с 52 формами ARKit), крючок одним огромным словом,
реплики мамы другим голосом в паузах, живой товар кадрами видео-превью со страницы магазина, клавиатура и мышь из
библиотеки вещей (только CC0), рот по Wav2ARKit только в окнах речи героя."""

import sys
from pathlib import Path

from app import lipsync
from app import vtubika_plan as P
from app import vtubika_vrm_stage as VS
from app.vtubika_vrm import expand_seq, to_vrm_spec

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
PLAN = ROOT / "tools" / "vtubika_plans" / "mom.json"


def _words(plan):
    toks = P.tokens(P.caption_text(plan))
    return [{"word": w, "start": round(0.4 + i * 0.42, 3), "end": round(0.4 + i * 0.42 + 0.36, 3)} for i, w in enumerate(toks)]


def test_hero_is_male_perfect_sync_model():
    plan = P.load_plan(PLAN)
    her = VS.heroine(plan)
    assert her["model"].startswith("hinzka/") and "Male" in her["model"]
    assert plan["vrm"]["arkit"] is True
    # Вещи библиотеки — только явные id CC0 (CC BY в роликах не берём: атрибуцию в описаниях не пишем).
    assert set(plan["vrm"]["props_models"].values()) == {"keyboard/mech_tkl_dark", "mouse/black"}


def test_hook_first_frame_one_big_word():
    import vtubika_story as V

    plan = P.load_plan(PLAN)
    (layer,) = V.hook_layers(plan)
    assert layer["start"] == 0 and layer["in"]["type"] == "none"
    assert " " not in layer["props"]["text"] and layer["props"]["text"].isupper()
    assert layer["props"]["size"] >= 120
    first = plan["sentences"][0]["text"].lower()
    assert layer["props"]["text"].lower().rstrip("?") in first


def test_talk_faces_and_product_sequence():
    plan = P.load_plan(PLAN)
    words = _words(plan)
    total = round(words[-1]["end"] + 1.0, 3)
    built = P.build(plan, words, total)
    spec = to_vrm_spec(plan, built, words, total)
    # «всем привет» и «это я, мам» — герой говорит (рот только в этих окнах).
    assert len(spec["timeline"]["speech"]) >= 4
    prod = spec["stream"]["product"]
    for f in prod["faces"].values():
        assert len(f["seq"]) == 36 and all(u in spec["siteShots"] for u in f["seq"])
    assert spec["shots"][0]["props"]["keyboard"]["model"] == "keyboard/mech_tkl_dark"
    # Руки на клавишах ближе к себе (keys_pull): иначе левая рука выпрямлена до конца и кисть крутится штопором.
    assert spec["timeline"]["keysPull"] == 0.03 and spec["modelScale"] == 0.885


def test_first_vtubika_line_revoiced_only():
    plan = P.load_plan(PLAN)
    flagged = [s["text"] for s in plan["sentences"] if s.get("revoice")]
    assert flagged == ["Однажды ночью ты открываешь Витубику."]
    assert "Виту́бику" in P.narration_sentences(plan)[4]


def test_expand_seq_pattern_and_list():
    assert expand_seq({"pattern": "/site/a/f_{:03d}.jpg", "from": 1, "count": 3}) == [
        "/site/a/f_001.jpg", "/site/a/f_002.jpg", "/site/a/f_003.jpg"]
    assert expand_seq(["/site/x.jpg"]) == ["/site/x.jpg"]
    assert expand_seq(None) is None


def test_mask_arkit_only_inside_speech_windows():
    ak = {"fps": 10, "start": 1.0, "names": ["jawOpen"], "frames": [[0.5]] * 40}
    out = lipsync.mask_arkit(ak, [(2.0, 2.5)], fade=0.1)
    vals = [r[0] for r in out["frames"]]
    assert vals[0] == 0 and vals[-1] == 0  # 1.0 с и 4.9 с — вне окна
    assert vals[12] == 0.5  # 2.2 с — внутри
    assert out["names"] == ["jawOpen"] and len(vals) == 40
