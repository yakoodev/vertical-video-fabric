"""Серия 10 «Трекинг отвалился»: героиня — модель каста, которой не было в главной роли недавних серий, её модель на
эфире та же (трекинг повторяет её); трекинг отваливается и возвращается действиями ``freeze``/``track`` модели на
мониторе, рука с фраппе (кадры превью товара すやみや) — слой эфира от события сцены, звуки сбоя — записи паков."""

import json
from pathlib import Path

from app import vtubika as U
from app import vtubika_plan as P
from app import vtubika_vrm_stage as VS
from app.vtubika_vrm import to_vrm_spec

ROOT = Path(__file__).resolve().parent.parent
PLAN = ROOT / "tools" / "vtubika_plans" / "tracking.json"
AVATAR_TS = ROOT / "node" / "vrm" / "src" / "avatar.ts"


def _words(plan):
    toks = P.tokens(P.caption_text(plan))
    return [{"word": w, "start": round(0.4 + i * 0.42, 3), "end": round(0.4 + i * 0.42 + 0.36, 3)} for i, w in enumerate(toks)]


def _built():
    plan = P.load_plan(PLAN)
    words = _words(plan)
    total = round(words[-1]["end"] + float(plan["tail"]), 3)
    return plan, words, total, P.build(plan, words, total)


def test_lead_is_new_cast_model_and_streams_as_herself():
    plan, words, total, built = _built()
    hero = VS.heroine(plan)
    assert hero["model"] == VS.CAST["mila"]["model"]
    # Главные героини недавних серий (8, 9) — не она.
    recent = {VS.CAST[h]["model"] for h in ("taya", "asya", "lilya", "varya")}
    assert hero["model"] not in recent
    spec = to_vrm_spec(plan, built, words, total)
    models = {m["id"]: m for m in spec["avatar"]["models"]}
    assert models["mila_av"]["model"] == hero["model"]


def test_tracking_freezes_and_comes_back():
    plan, words, total, built = _built()
    acts = [a for a in to_vrm_spec(plan, built, words, total)["avatar"]["acts"] if a["who"] == "mila_av"]
    seq = [a["act"] for a in acts]
    # Крючок (застыла) → трекинг → отвалился → вернулся → кнопка: замерла и отмерла → номер программы: замерла.
    assert seq == ["freeze", "track", "freeze", "track", "freeze", "track", "freeze"]
    assert [a["t"] for a in acts] == sorted(a["t"] for a in acts)
    # Первое действие держится и до своего времени (avatar.ts: keys[max(0, i)]) — на кадре 0 модель уже застыла.
    assert acts[0]["t"] < words[0]["start"]
    # Действие «freeze» есть в движке аватара и меняется рывком, а не наплывом 0.5 с.
    src = AVATAR_TS.read_text("utf-8")
    assert 'act.act === "freeze"' in src and "FREEZE_SNAP" in src


def test_frappe_hand_layer_is_timed_from_scene_event():
    plan, words, total, built = _built()
    scenes = to_vrm_spec(plan, built, words, total)["stream"]["scenes"]
    sc = next(s for s in scenes if s.get("front"))
    (hand,) = sc["front"]
    assert "frappe_hand" in hand["seq"][0] and len(hand["seq"]) == 36
    assert hand["t0"] > sc["t"] and hand["keys"][0]["t"] > sc["t"]


def test_glitch_sounds_are_pack_recordings():
    _plan, _words_, _total, built = _built()
    names = {s["name"] for s in built["sfx"]}
    assert {"glitch", "scratch", "shutter", "toggle", "laugh", "cheer"} <= names
    for n in ("glitch", "scratch", "shutter", "toggle", "laugh"):
        assert U.SFX[n][0].startswith("sfx:")
    laugh = [s for s in built["sfx"] if s["name"] == "laugh"]
    assert all(s["dur"] == U.SFX_DUR["laugh"] for s in laugh)


def test_shown_product_is_green_and_priced():
    plan = json.loads(PLAN.read_text("utf-8"))
    phones = [ev["phone"] for s in plan["sentences"] for ev in s.get("do") or [] if "phone" in ev]
    shop = [p for p in phones if p.get("screen") == "shop"]
    assert [p["store"]["price"] for p in shop] == ["650 ₽"]
    assert plan["site_shots"]["product"]["file"] == "product_frappe.png"
    assert plan["end_card"]["promo"].startswith("Промокод Free10")
    assert any("Free10" in s["text"] for s in plan["sentences"])
