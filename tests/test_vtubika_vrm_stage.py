"""История 3 «Пробная» на VRM: героиня Вера (своя модель и кресло), аватар на мониторе (кто виден, что делает),
кот только на средних планах, жесты рук поверх действий, карточка эфира с аватаром — по плану trial.json."""

from pathlib import Path

import pytest

from app import vtubika_plan as P
from app import vtubika_vrm_stage as VS
from app.vtubika_vrm import to_vrm_spec

ROOT = Path(__file__).resolve().parent.parent


def _story(name: str = "trial.json"):
    plan = P.load_plan(ROOT / "tools" / "vtubika_plans" / name)
    toks = P.tokens(P.caption_text(plan))
    words = [{"word": w, "start": round(0.9 + i * 0.4, 3), "end": round(0.9 + i * 0.4 + 0.35, 3)} for i, w in enumerate(toks)]
    total = round(words[-1]["end"] + 2.6, 3)
    built = P.build(plan, words, total)
    return plan, built, words, total, to_vrm_spec(plan, built, words, total)


def test_vera_model_chair_and_look_differ_from_story1():
    *_, spec = _story()
    _, _, _, _, fox = _story("fox.json")
    assert spec["model"] == VS.GIRL and fox["model"] == VS.TWIST
    assert spec["timeline"]["chair"]["pos"][1] < fox["timeline"]["chair"]["pos"][1]
    assert all(s["props"]["chair"]["pos"][1] == spec["timeline"]["chair"]["pos"][1] for s in spec["shots"] if "props" in s)
    assert spec["look"]["stains"]["Tops"]["colors"] and spec["headphones"]["kind"] == "headphones"
    assert "avatar" not in fox and not fox["cats"]


def test_avatar_on_monitor_trial_then_own_then_both():
    plan, built, *_rest, spec = _story()
    av = spec["avatar"]
    assert [m["id"] for m in av["models"]] == ["trial", "vera_oc"]
    assert av["models"][0]["model"] == VS.TWIST and av["models"][1]["model"] == VS.GIRL
    shows = [k["who"] for k in av["show"]]
    assert shows[1] == ["trial"] and ["vera_oc"] in shows[2:] and shows[-1] == ["trial", "vera_oc"]
    acts = {k["act"] for k in av["acts"]}
    assert {"copy", "dance", "mug", "bow", "showcase", "idle"} <= acts
    mug = next(k for k in av["acts"] if k["act"] == "mug")
    assert mug["t"] < mug["drop"] < next(k["t"] for k in av["acts"] if k["act"] == "bow")
    assert {l["text"] for l in av["labels"]} == {"жена", "бывшая"}
    assert any(a["kind"] == "live" for a in spec["stream"]["avatars"])


def test_cat_only_on_desk_shots_and_gestures_merge():
    *_, spec = _story()
    assert len(spec["cats"]) == 1
    cat = spec["cats"][0]
    for s in spec["shots"]:
        if s["t1"] > cat["t0"] and s["t0"] < cat["t1"] and s["plate"].get("cats"):
            assert s["name"].endswith(("two", "typing", "insert"))
    rights = [k["right"] for k in spec["timeline"]["arms"]]
    assert "wave" in rights and "mouth" in rights
    assert spec["timeline"]["nods"] and spec["timeline"]["bows"]


def test_merge_arms_returns_to_action_after_gesture():
    base = [{"t": -3, "left": "idle", "right": "idle"}, {"t": 5.0, "dur": 0.8, "left": "keys", "right": "keys"}]
    out = VS.merge_arms(base, [(2.0, 3.0, "idle", "wave"), (6.0, 7.0, "idle", "mouth")])
    seq = [(k["t"], k["left"], k["right"]) for k in out]
    assert seq == [(-3, "idle", "idle"), (2.0, "idle", "wave"), (3.0, "idle", "idle"), (5.0, "keys", "keys"),
                   (6.0, "idle", "mouth"), (7.0, "keys", "keys")]


def test_unknown_heroine_or_avatar_rejected():
    with pytest.raises(P.PlanError):
        VS.heroine({"vrm": {"heroine": "nobody"}})
    plan, *_ = _story()
    with pytest.raises(P.PlanError):
        VS.stage({**plan, "vrm": {"avatars": ["ghost"]}}, [], 10.0)


def test_mid_phrases_mostly_show_vera():
    """Вера в кадре хотя бы в половине фраз (крупно, средне, через плечо или общий план)."""
    plan, built, *_rest, spec = _story()
    hero_shots = {"face", "face_left", "face_close", "smile", "two", "typing", "insert", "ots", "wide"}
    with_vera = 0
    for s in built["sentences"]:
        names = {sh["name"].split("_", 1)[1] for sh in spec["shots"] if sh["t0"] < s["t1"] and sh["t1"] > s["t0"]}
        with_vera += bool(names & hero_shots)
    assert with_vera >= len(built["sentences"]) * 0.75


def test_real_product_card_has_neutral_picture_without_site_shots():
    """Без скриншотов сайта карточка с настоящим товаром — без нашей модели на превью (иначе реклама выдаёт её за
    этот товар): нейтральная плитка. Со снимками — настоящая страница (tests/test_vtubika_site.py)."""
    plan = P.load_plan(ROOT / "tools" / "vtubika_plans" / "trial.json")
    plan.pop("site_shots", None)
    toks = P.tokens(P.caption_text(plan))
    words = [{"word": w, "start": round(0.9 + i * 0.4, 3), "end": round(0.9 + i * 0.4 + 0.35, 3)} for i, w in enumerate(toks)]
    total = round(words[-1]["end"] + 2.6, 3)
    spec = to_vrm_spec(plan, P.build(plan, words, total), words, total)
    cards = [o for o in spec["overlays"] if o["kind"] == "phone" and o.get("screen") == "shop"]
    assert cards and all(c["store"]["preview"] == "vrm" for c in cards)
    assert "siteShots" not in spec


def test_sticker_episode_pngtuber_then_tracked_avatar():
    """Серия 4 «Стикер»: Ника (своя героиня), её картинка Кира — сначала застывший PNGtuber, потом повторяет за ней,
    в конце снова заставка; карточка — настоящий Live2D-товар Ferret, а не наша модель; цена — как на сайте."""
    plan, _built, _words, _total, spec = _story("sticker.json")
    _, _, _, _, trial = _story()
    assert spec["model"] == VS.GIRL and spec["look"]["repaint"]["Hair"] != trial["look"]["repaint"]["Hair"]
    assert spec["look"]["repaint"]["Tops"] != trial["look"]["repaint"]["Tops"] and "stains" not in spec["look"]
    av = spec["avatar"]
    assert [m["id"] for m in av["models"]] == ["kira"]
    acts = [k["act"] for k in av["acts"]]
    assert acts[0] == "png" and "copy" in acts and acts[-1] == "png"
    assert any(a["kind"] == "live" for a in spec["stream"]["avatars"])
    cards = [o for o in spec["overlays"] if o["kind"] == "phone"]
    assert cards and cards[0]["site"]["price"] == "1 290 ₽" and "Ferret" in cards[0]["site"]["title"]
    assert cards[0]["site"]["steps"] and "тысячу двести девяносто" in P.caption_text(plan)
