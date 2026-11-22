"""Мимика «вебка против айфона» (мини-гайд 5): деревянное лицо почти не двигается, живое — показывает брови, щёки,
прищур и язык ровно на событиях, план гайда 5 — без английских слов в озвучке и без обещаний."""

import json
import re
from pathlib import Path

import pytest

from app import vtubika_facetrack as F
from app import vtubika_plan as P

ROOT = Path(__file__).resolve().parent.parent
PLAN = ROOT / "tools" / "vtubika_plans" / "guide_phone.json"


def test_stiff_face_barely_moves() -> None:
    fr = F.track("stiff", 6.0, 15)
    assert len(fr) == 90
    mouth = [f["expr"].get("aa", 0.0) for f in fr]
    assert max(mouth) <= F.STIFF_MOUTH_MAX
    assert max(mouth) > 0.05, "рот всё же приоткрывается — деревянный, а не мёртвый"
    for f in fr:
        assert all(abs(v) <= F.STIFF_HEAD_MAX for v in f["head"])
        # Ни бровей, ни щёк, ни улыбки: только рот и редкое моргание.
        assert set(f["expr"]) <= {"aa", "blink"}
    # Ступеньки трекинга по картинке: рот меняется не чаще 5 раз в секунду.
    changes = sum(1 for a, b in zip(mouth, mouth[1:]) if a != b)
    assert changes <= 6 * 5


def test_lively_face_moves_and_shows_features_on_events() -> None:
    ev = [{"t": 1.0, "what": "brows"}, {"t": 2.0, "what": "cheeks"}, {"t": 3.0, "what": "squint"}, {"t": 4.0, "what": "tongue"}]
    fr = F.track("lively", 6.0, 15, ev)
    by_t = {round(f["t"], 2): f for f in fr}
    at = lambda t: by_t[round(round(t * 15) / 15, 2)]  # noqa: E731
    assert at(1.3)["expr"].get("browInnerUp", 0) > 0.8
    assert at(2.3)["expr"].get("cheekPuff", 0) > 0.8
    assert at(3.3)["expr"].get("eyeSquintLeft", 0) > 0.8
    assert at(4.3)["expr"].get("tongueOut", 0) > 0.8
    # Щёки надуты и язык наружу — гласные молчат.
    for t in (2.3, 4.3):
        assert all(at(t)["expr"].get(v, 0) < 0.2 for v in F.VOWELS)
    # До событий ничего этого нет, а голова и рот живые.
    assert at(0.5)["expr"].get("browInnerUp", 0) == 0
    yaw = [f["head"][1] for f in fr]
    assert max(yaw) - min(yaw) > 8
    assert max(max(f["expr"].get(v, 0) for v in F.VOWELS) for f in fr) > 0.6


def test_unknown_mode_and_feature_fail() -> None:
    with pytest.raises(ValueError):
        F.frame("blurry", 0.0)
    with pytest.raises(ValueError):
        F.frame("lively", 0.1, [{"t": 0.0, "what": "ears"}])


def test_word_events_land_on_words() -> None:
    plan = P.load_plan(PLAN)
    toks = P.tokens(P.caption_text(plan))
    words = [{"word": w, "start": round(0.4 + i * 0.4, 3), "end": round(0.4 + i * 0.4 + 0.3, 3)} for i, w in enumerate(toks)]
    times = P.token_times(plan, words)
    t0 = times[1][0]["start"]
    ev = F.word_events(times, 2, {"язык.": "tongue"}, t0, lead=0.0)
    assert ev[0]["what"] == "tongue"
    assert abs(ev[0]["t"] + t0 - P.anchor(times[2], "язык.", 2)) < 1e-6


def test_plan_guide_phone_rules() -> None:
    plan = json.loads(PLAN.read_text("utf-8"))
    assert plan["vrm"]["heroine"] == "olya_host"
    assert plan["voice"] == {"provider": "polza", "model": "elevenlabs/eleven-v4", "voice": "lily", "paid_ok": True}
    assert plan["hook"]["word"].upper() == "ДЕРЕВЯННАЯ?"
    loaded = P.load_plan(PLAN)
    said = P.narration_text(loaded)
    # Владелец: английские названия в озвучке не произносить (айфон, андроид — по-русски, имена — на экране).
    assert not re.search(r"[A-Za-z]", said), said
    assert "Виту́бики" in said, "витУбика — ударение на «у»"
    # Цифры — только «три тысячи» из сценария; на экране — никаких сумм, скидок и сроков, кроме цены вебки.
    screen = json.dumps([s.get("gfx") for s in plan["sentences"]], ensure_ascii=False)
    assert re.findall(r"\d[\d\s]*₽", screen) == ["3000 ₽"]
    assert not re.search(r"скидк|бесплатн|гарант|%|дн(я|ей)\b|час(а|ов)\b", screen, re.I)
    # Предметы — значки Lucide из набора плоской темы, без самодельных картинок.
    icons = {g.get("icon") for s in plan["sentences"] for g in s.get("gfx") or [] if g.get("icon")}
    icons |= {it["icon"] for s in plan["sentences"] for g in s.get("gfx") or [] for it in g.get("items", []) if it.get("icon")}
    src = (ROOT / "frontend" / "src" / "motion" / "flat" / "icons.ts").read_text("utf-8")
    for name in icons:
        assert f'"{name}": [' in src, name
    assert {"webcam", "smartphone", "scan-face"} <= icons
