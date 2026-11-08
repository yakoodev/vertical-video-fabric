"""Вселенная «Витубики» (app/vtubika.py) и план истории → сцена (app/vtubika_plan.py): кадры и события на словах,
карточки в рублях, монитор/чат/телефон, «фраза → что в кадре». Без рендера и без TTS."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from app import vtubika as U
from app import vtubika_plan as P
from app.explainer_gen_words import proportional_words
from app.scene3d import validate_spec

ROOT = Path(__file__).resolve().parents[1]
FOX = ROOT / "tools" / "vtubika_plans" / "fox.json"


def fox() -> dict:
    return P.load_plan(FOX)


def timed(plan: dict, sec: float = 60.0) -> list[dict]:
    """Слова субтитров равномерно по времени (вместо Whisper)."""
    return proportional_words(P.caption_text(plan), sec)


def test_fox_plan_is_valid_and_uses_script_text():
    plan = fox()
    texts = [s["text"] for s in plan["sentences"]]
    assert texts[0] == "Тебя зовут Алина, тебе девятнадцать, и твой аватар — две картинки."
    assert texts[-1] == "Ник — mama_alina."
    assert len(texts) == 24
    # Цены — словами, ссылки и ники — как читать вслух; в субтитрах — текст сценария.
    say = P.narration_text(plan)
    assert "Две девятьсот девяносто." in say and "витубика точка стор" in say and "mama_alina" not in say
    assert "vtubika.store" in P.caption_text(plan)
    # Ничего из референса.
    blob = FOX.read_text("utf-8")
    for bad in ("dimon228", "max_play", "sonya.k", "на кофе", "$"):
        assert bad not in blob


def test_plan_errors():
    plan = fox()
    bad = copy.deepcopy(plan)
    bad["sentences"][0]["shots"][0]["cam"] = "drone"
    with pytest.raises(P.PlanError):
        P.validate_plan(bad)
    bad = copy.deepcopy(plan)
    bad["sentences"][3]["do"].append({"act": "Dance_Loop"})
    with pytest.raises(P.PlanError):
        P.validate_plan(bad)
    with pytest.raises(P.PlanError):
        P.anchor([{"word": "Рот", "start": 1.0, "end": 1.2}], "кот", 0)


def test_anchor_and_token_times():
    plan = {"sentences": [{"text": "Рот закрыт."}, {"text": "Рот открыт."}]}
    words = [{"word": "Рот", "start": 0.1, "end": 0.3}, {"word": "закрыт.", "start": 0.3, "end": 0.8},
             {"word": "Рот", "start": 1.2, "end": 1.4}, {"word": "открыт.", "start": 1.4, "end": 1.9}]
    times = P.token_times(plan, words)
    assert [len(t) for t in times] == [2, 2]
    assert P.anchor(times[1], "откр", 1) == 1.4
    assert P.anchor(times[1], "end", 1) == 1.9
    assert P.anchor(times[0], None, 0) == 0.1


def test_build_puts_events_on_words():
    plan = fox()
    words = timed(plan)
    built = P.build(plan, words, 62.0)
    spec = built["spec"]
    assert validate_spec(spec, check_assets=False)["models"] == []
    objs = {o["id"]: o for o in spec["objects"]}
    # Монитор: лиса (рот закрыт/открыт), магазин с отказом, редактор с починкой глаза, волчонок.
    mon = objs["monitor_screen"]["params"]
    assert mon["noChat"] is True
    assert [a["kind"] for a in mon["avatars"]] == ["fox", "fox", "wolf"]
    assert [m["mode"] for m in mon["modes"]] == ["stream", "stream", "shop", "stream", "rig", "stream"]
    assert [m["m"] for m in mon["mouth"]][:3] == ["talk", "closed", "open"]
    assert mon["declined"] > mon["modes"][2]["t"] and mon["fixAt"] > mon["modes"][4]["t"]
    assert mon["wolf"]["blinks"] and mon["wolf"]["laugh"]
    # Чат — на вертикальном мониторе, те же сообщения; ссылка от pelmen_404, последнее — mama_alina.
    chat = objs["chat_screen"]["params"]["chat"]
    users = [m["user"] for m in chat]
    assert ("pelmen_404", "vtubika.store") in [(m["user"], m["text"]) for m in chat]
    assert chat[-1]["user"] == "mama_alina" and chat[-1]["text"] == "верните лису, я к ней привыкла"
    assert users.count("pelmen_404") == 2
    # Банка: монетки на «откладываешь» и «Сто»; планшет — окно рисования; стилус в руке на это время.
    assert len(objs["jar"]["params"]["drops"]) == 2
    assert objs["tablet"]["params"]["draw"][1] > objs["tablet"]["params"]["draw"][0]
    assert objs["stylus"]["attach"][0]["to"] == "hero:hand_r"
    # Карточки: копилка в рублях 2 750 → … → 3 100 → 110 (чип −2 990 ₽), зрители 7 → 40, телефон: магазин и мессенджер.
    kinds = {o["kind"]: o for o in spec["overlays"]}
    huds = [o for o in spec["overlays"] if o["kind"] == "hud"]
    kop = next(h for h in huds if h["label"] == "КОПИЛКА")
    assert [v["value"] for v in kop["values"]] == [2750, 2800, 2900, 3100, 110]
    assert kop["suffix"] == " ₽" and kop["group"] and kop["deltas"][-1]["text"] == "−2 990 ₽"
    viewers = next(h for h in huds if h["label"] == "ЗРИТЕЛЕЙ")
    assert [v["value"] for v in viewers["values"]] == [7, 40] and viewers["left"] == 40.0
    phones = [o for o in spec["overlays"] if o["kind"] == "phone"]
    assert [p["screen"] for p in phones] == ["shop", "messenger"]
    assert phones[0]["store"]["price"] == "2 990 ₽" and phones[0]["store"]["paidAt"] > phones[0]["t0"]
    assert phones[1]["message"]["text"] == "Файл модели пришёл"
    # Телефон — крупной вставкой (≈65 % ширины кадра) поверх затемнённой размытой сцены; финал — с карточкой чата.
    assert all(p["scale"] >= 1.5 and p["dim"] > 0 for p in phones)
    assert "tag" in kinds and kinds["chat"]["chat"][-1]["user"] == "mama_alina"
    inserts = [k for k in spec["camera"]["keys"] if k.get("focus")]
    assert inserts and all(k["dof"] >= 3 for k in inserts)
    # Героиня: soft, длинные волосы, ресницы; действия на словах — рисует, монетка, смех, хмурится, ахнула.
    hero = spec["characters"][0]
    assert hero["voxel"]["shape"] == "soft" and hero["voxel"]["hairStyle"] == "long" and hero["voxel"]["lashes"]
    clips = {a["clip"] for a in hero["actions"]}
    assert {"Draw_Loop", "Coin_Drop", "Laugh_Loop", "Frown_Loop", "Gasp", "Chin_Rest_Loop", "Talk_Loop"} <= clips
    assert {"frown", "laugh", "wide", "talk", "blink_once"} <= {f["face"] for f in hero["faces"]}
    # Звуки — только записи паков, на событиях.
    names = {s["name"] for s in built["sfx"]}
    assert {"coin", "donate", "error", "success", "notify", "pop"} <= names
    assert all(s["ref"].startswith("sfx:") for s in built["sfx"])
    # Кадры: склейки, наезды, голландские углы; первый — с нуля, последний — до конца.
    keys = spec["camera"]["keys"]
    assert keys[0]["t"] == 0.0 and keys[-1]["t"] == 62.0
    assert sum(1 for k in keys if k.get("cut")) >= 20 and any(k.get("roll") for k in keys)


def test_build_does_not_mutate_plan_and_looks_differ():
    plan = fox()
    before = copy.deepcopy(plan)
    words = timed(plan)
    a = P.build(plan, words, 62.0, look="cozy")["spec"]
    b = P.build(plan, words, 62.0, look="pastel")["spec"]
    assert plan == before
    chat_a = next(o for o in a["objects"] if o["id"] == "chat_screen")["params"]["chat"]
    chat_b = next(o for o in b["objects"] if o["id"] == "chat_screen")["params"]["chat"]
    assert len(chat_a) == len(chat_b)  # повторная сборка не дублирует сообщения
    assert a["environment"]["background"] != b["environment"]["background"]
    assert {x["id"] for x in a["lights"]} >= {"key", "lamp", "rim"}


def test_camera_presets_stay_inside_room():
    room = U.story_room("cozy")
    W, D, H = room["room"]["width"], room["room"]["depth"], room["room"]["height"]
    for name in U.CAMERAS:
        for act in (None, "Draw_Loop", "Frown_Loop"):
            k0, k1 = U.camera_preset(name, room, act=act, roll=-5)
            for pos in (k0["position"], k1["position"]):
                assert abs(pos[0]) < W / 2 and abs(pos[2]) < D / 2 and 0 < pos[1] < H, (name, pos)
            assert k1["roll"] == pytest.approx(-3.5)
    with pytest.raises(ValueError):
        U.camera_preset("drone", room)
    with pytest.raises(ValueError):
        U.story_room("noir")


def test_caption_highlight_and_checklist():
    plan = fox()
    words = timed(plan)
    comp, marks = P.caption_words(words, plan["highlight"])
    marked = {comp[m["i"]]["w"] for m in marks}
    assert {"принимает.", "mama_alina.", "vtubika.store."} <= marked
    built = P.build(plan, words, 62.0)
    md = P.checklist_md(plan, built, {1: "01.jpg"})
    assert "## 13. В чате pelmen_404 кидает ссылку: vtubika.store." in md
    assert "- [ ] в чате pelmen_404 пишет vtubika.store" in md and "кадр: 01.jpg" in md
