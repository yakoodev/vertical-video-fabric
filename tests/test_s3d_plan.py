"""3D-серия в сервисе: схема плана и проверка (понятные ошибки с подсказками), правки по шагам, черновик по правилам,
план из сценария с подделкой Gemini, сборка спеки VRM (камера и игра пресетами, рот только у героини, перекраска,
свет локации), импорт историй Витубики. Без браузера, сети и денег."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from app import s3d_plan as SP
from app import s3d_store as D
from app import s3d_vrm as SV
from app import s3d_write as W
from app.explainer_gen_words import proportional_words

ROOT = Path(__file__).resolve().parent.parent


def _lib() -> dict:
    lib = {"characters": {}, "locations": {}, "props": {}}
    for kind, slug, name, data in D.builtin_items():
        lib[f"{kind}s"][slug] = {"slug": slug, "name": name, "kind": kind, **D.normalize_item(kind, data)}
    return lib


PLAN = {
    "title": "Первый эфир", "cast": ["nika", "kira"], "location": "stream_room", "narrator": "narrator",
    "presets": {"light": "monitor_glow"},
    "lines": [
        {"who": "nika", "text": "Привет, это мой первый эфир!",
         "shots": [{"camera": "wide", "move": "slow_push"}, {"camera": "face", "move": "punch_in", "at": "первый"}],
         "performance": [{"preset": "wave", "at": "Привет"}], "events": [{"kind": "title", "text": "Первый эфир"}]},
        {"text": "В чате пока тихо.", "shots": [{"camera": "chat", "move": "slow_push"}],
         "events": [{"kind": "comment", "user": "kotletka_99", "text": "привет!", "card": True}],
         "transition": {"kind": "dip_black"}},
        {"who": "nika", "text": "Ну и ладно, я всё равно счастлива.", "shots": [{"camera": "face_left", "move": "dutch_tilt"}],
         "performance": [{"preset": "happy", "at": "счастлива"}]},
    ],
}


def _words(plan: dict, lead: float = 0.6) -> tuple[list[dict], float]:
    words, t = [], lead
    for ln in plan["lines"]:
        d = len(ln["text"]) / 14
        words += [{**w, "start": round(w["start"] + t, 3), "end": round(w["end"] + t, 3), "line": ln["id"]}
                  for w in proportional_words(ln["text"], d)]
        t += d + ln["pause_after"]
    return words, round(t + 1.8, 3)


def test_valid_plan_gets_ids_and_defaults():
    plan, warnings = SP.validate_plan(PLAN, _lib(), D.DEFAULT_SETTINGS)
    assert [ln["id"] for ln in plan["lines"]] == ["l1", "l2", "l3"]
    assert plan["engine"] == "vrm_desk" and plan["lead"] == 0.6 and plan["captions"]["y"] == 0.8
    assert plan["lines"][1]["who"] == "narrator" and plan["lines"][1]["transition"] == {"kind": "dip_black", "duration": 0.4}
    assert plan["presets"] == {"look": "anime_soft", "light": "monitor_glow"} and warnings == []


def test_errors_are_listed_with_hints():
    bad = json.loads(json.dumps(PLAN))
    bad["cast"] = ["nikka"]
    bad["lines"][0]["shots"][0]["camera"] = "facee"
    bad["lines"][0]["shots"][1]["at"] = "второй"
    bad["lines"][0]["performance"][0]["preset"] = "wavee"
    bad["lines"][1]["events"][0]["kind"] = "commment"
    bad["lines"][1]["transition"] = {"kind": "dissolve"}
    with pytest.raises(SP.PlanError) as err:
        SP.validate_plan(bad, _lib(), D.DEFAULT_SETTINGS)
    text = " | ".join(err.value.problems)
    assert "нет персонажа «nikka»" in text and "может, nika" in text
    assert "ракурс «facee»" in text and "может, face" in text
    assert "нет слова «второй»" in text and "«wavee»" in text and "commment" in text and "dissolve" in text


def test_engine_rules_and_length():
    lib = _lib()
    with pytest.raises(SP.PlanError) as err:
        SP.validate_plan({**PLAN, "cast": ["klod"]}, lib)  # робот — не VRM
    assert any("не подходит движку vrm_desk" in p for p in err.value.problems)
    long = {**PLAN, "lines": [{"text": "очень длинная реплика " * 15}] * 30}
    with pytest.raises(SP.PlanError) as err:
        SP.validate_plan(long, lib)
    assert any("длиннее 120" in p for p in err.value.problems)


def test_changes_set_line_insert_delete_move():
    plan, _ = SP.validate_plan(PLAN, _lib(), D.DEFAULT_SETTINGS)
    out = SP.apply_changes(plan, [
        {"op": "set", "field": "title", "value": "Новое"},
        {"op": "line", "id": "l2", "set": {"text": "В чате тишина."}},
        {"op": "insert", "after": "l1", "line": {"text": "Вставка.", "shots": [{"camera": "two"}]}},
        {"op": "move", "id": "l3"},
        {"op": "delete", "id": "l1"},
    ])
    assert out["title"] == "Новое" and [ln["id"] for ln in out["lines"]] == ["l3", "l4", "l2"]
    assert out["lines"][2]["text"] == "В чате тишина." and plan["lines"][1]["text"] == "В чате пока тихо."
    with pytest.raises(SP.PlanError):
        SP.apply_changes(plan, [{"op": "line", "id": "l9", "set": {}}])


def test_draft_by_rules_dialogue_cameras_and_emotions():
    lib = _lib()
    plan = SP.draft_from_script("Ника: Привет всем! Сегодня рисую.\nВ чате пока тихо.\nНика: Я так устала…", lib,
                                D.DEFAULT_SETTINGS, title="Проба", cast=["nika"])
    plan, _ = SP.validate_plan(plan, lib, D.DEFAULT_SETTINGS)
    lines = plan["lines"]
    assert lines[0]["who"] == "nika" and lines[0]["shots"][0]["camera"] == "wide"
    assert lines[1]["who"] == "narrator" or lines[2]["who"] == "narrator"
    cams = [ln["shots"][0]["camera"] for ln in lines]
    assert all(a != b for a, b in zip(cams, cams[1:])), cams
    assert lines[0]["performance"][0]["preset"] == "wave" and lines[-1]["performance"][0]["preset"] == "tired"
    assert lines[0]["events"][0] == {"kind": "title", "text": "Проба", "dur": 2.6}


class FakeGemini:
    def __init__(self, answer: dict):
        self.answer, self.contexts = answer, []

    def generate_content(self, model, payload):
        self.contexts.append(json.loads(payload["contents"][0]["parts"][1]["text"]))
        return {"candidates": [{"content": {"parts": [{"text": json.dumps(self.answer, ensure_ascii=False)}]}}]}


def test_plan_from_script_with_mocked_gemini(tmp_path, monkeypatch):
    from test_data_foundation import _store
    from app import projects as P

    store = _store(tmp_path, monkeypatch)
    pid = P.create_project(store, "3D", kind="series3d")["id"]
    D.seed_builtins(store, pid)
    fake = FakeGemini({"title": "Лиса", "lines": [
        {"who": "nika", "text": "Тебя зовут Алина.", "shots": [{"camera": "wide", "move": "slow_push"},
                                                                {"camera": "face", "move": "warp", "at": "Алина"}],
         "performance": [{"preset": "happy", "at": "зовут"}, {"preset": "dance"}],
         "events": [{"kind": "comment", "user": "x", "text": "топ"}, {"kind": "explosion"}]},
        {"who": "кто-то", "text": "Рот закрыт.", "shots": [{"camera": "zoom"}], "transition": {"kind": "whip"}}]})
    got = W.plan_from_script(store, pid, script="Тебя зовут Алина. Рот закрыт.", cast=["nika"], client=fake)
    assert got["source"] == "gemini"
    lines = got["plan"]["lines"]
    assert lines[0]["shots"][1] == {"camera": "face", "at": "Алина"}  # неизвестное движение выкинуто
    assert [c["preset"] for c in lines[0]["performance"]] == ["happy"]
    assert [e["kind"] for e in lines[0]["events"]] == ["comment"]
    assert lines[1]["who"] == "narrator" and lines[1]["shots"][0]["camera"] == "face" and lines[1]["transition"]["kind"] == "whip"
    ctx = fake.contexts[0]
    assert "face" in ctx["framings"] and "happy" in ctx["emotion"] and ctx["cast"][0]["slug"] == "nika"
    # Gemini упал — черновик по правилам, но план есть.
    class Broken:
        def generate_content(self, *_a):
            raise RuntimeError("заблокирован")

    got = W.plan_from_script(store, pid, script="Привет! Это проба.", cast=["nika"], client=Broken())
    assert got["source"] == "rules" and got["plan"]["lines"] and "Gemini не ответил" in got["notes"][0]
    # Без Gemini по одной теме плана не написать — честная ошибка.
    monkeypatch.setattr(W, "gemini_client", lambda: None)
    with pytest.raises(SP.PlanError) as err:
        W.plan_from_script(store, pid, topic="про котов", cast=["nika"])
    assert "пришлите текст сценария" in err.value.problems[0]


def _fake_expand(spec: dict) -> dict:
    """Развёртка без node: ключи камеры — два на движение, игра — лицо."""
    keys = []
    for m in spec["presets"].get("camera") or []:
        keys += [{"t": m["t0"], "position": [0, 1.4, 2.5], "target": m["target"], "fov": m["fov"], "roll": 0, "cut": True},
                 {"t": m["t1"], "position": [0, 1.4, 2.2], "target": m["target"], "fov": m["fov"], "roll": 5}]
    tl = {"faces": [{"t": c["t"], "expr": {"happy": 1}, "hold": 1} for c in spec["presets"].get("performance") or []]}
    return {"camera": {"keys": keys}, "characters": [{"timeline": tl}],
            "lights": [{"type": "spot", "color": "#8fb8ff", "intensity": 1.1, "position": [0, 1.3, 0.6]},
                       {"type": "hemisphere", "color": "#26304e", "intensity": 0.3}]}


def test_compile_vrm_spec(monkeypatch):
    monkeypatch.setattr(SV, "expand_presets", _fake_expand)
    lib = _lib()
    lib["characters"]["nika"]["vrm"]["recolor"] = {"Hair": ["#3a1206", "#f08a3c"]}
    lib["locations"]["stream_room"]["plate"]["props"] = {"cup": False, "mic": True, "cats": False}
    plan, _ = SP.validate_plan(PLAN, lib, D.DEFAULT_SETTINGS)
    words, total = _words(plan)
    res = SV.compile_vrm(plan, lib, words, total, envelope=[0.5] * int(total * 30))
    spec = res["spec"]
    assert spec["look"]["repaint"]["Hair"] == ["#3a1206", "#f08a3c"] and spec["model"] == "three-vrm-girl.vrm"
    assert [m["id"] for m in spec["avatar"]["models"]] == ["kira"]
    shots = spec["shots"]
    assert shots[0]["t0"] == 0 and abs(shots[-1]["t1"] - total) < 1e-6
    moves = {s["name"]: s["move"] for s in res["shots"] if s["move"] and not s["insert"]}
    assert set(moves.values()) == {"slow_push", "punch_in", "dutch_tilt"}, moves
    moved = [s for s in shots if s["name"] in moves]
    assert all(s["camKeys"][0]["t"] == 0 and s["camKeys"][1]["roll"] == 5 for s in moved)  # ключи движка, по планам
    assert res["applied"]["camera"] == 3 and res["applied"]["performance"] == 2
    # Рот — только реплики Ники (l1, l3), не голос за кадром.
    l2 = [w for w in words if w["line"] == "l2"]
    assert spec["timeline"]["speech"] and not any(a <= l2[0]["start"] <= b for a, b in spec["timeline"]["speech"])
    assert spec["timeline"]["mouthEnv"]["values"][0] == 0.5
    assert spec["timeline"]["faces"] and any(f.get("hold") for f in spec["timeline"]["faces"])
    assert all(s["light"]["keyColor"] == "#8fb8ff" for s in shots) and spec["cats"] == []
    assert all(not ((s.get("plate") or {}).get("fgDesk") or {}).get("cup") for s in shots)
    assert res["titles"][0]["text"] == "Первый эфир" and res["transitions"][0]["kind"] == "dip_black"
    assert any(o["kind"] == "chat" for o in spec["overlays"]) and any(x["name"] == "pop" for x in res["sfx"])


def test_chat_insert_and_comment_card_keep_clear_of_captions(monkeypatch):
    monkeypatch.setattr(SV, "expand_presets", _fake_expand)
    lib = _lib()
    plan, _ = SP.validate_plan(PLAN, lib, D.DEFAULT_SETTINGS)
    words, total = _words(plan)
    spec = SV.compile_vrm(plan, lib, words, total)["spec"]
    band = SV.caption_band(plan)
    assert band == (1386.0, 1686.0)  # субтитры по умолчанию — 0.8 кадра
    chats = [s["plate"]["insert"] for s in spec["shots"] if (s.get("plate") or {}).get("insert", {}).get("kind") == "chat"]
    assert chats and all(c["avoid"] == [1386.0, 1686.0] for c in chats)
    card = next(o for o in spec["overlays"] if o["kind"] == "chat")
    assert card["y"] + card["h"] / 2 < band[0]  # карточка комментария сверху — субтитры её не задевают
    # Субтитры подняты к карточке — карточка уходит вниз, под полосу, и не вылезает за кадр.
    high = json.loads(json.dumps(PLAN))
    high["captions"] = {"y": 0.3}
    plan2, _ = SP.validate_plan(high, lib, D.DEFAULT_SETTINGS)
    spec2 = SV.compile_vrm(plan2, lib, words, total)["spec"]
    b2 = SV.caption_band(plan2)
    card2 = next(o for o in spec2["overlays"] if o["kind"] == "chat")
    assert card2["y"] - card2["h"] / 2 >= b2[1] + 24 and card2["y"] + card2["h"] / 2 <= 1920


def test_performance_hand_is_validated_and_passed_to_engine(monkeypatch):
    seen = {}

    def fake(spec):
        seen["perf"] = spec["presets"].get("performance")
        return _fake_expand(spec)

    monkeypatch.setattr(SV, "expand_presets", fake)
    lib = _lib()
    p = json.loads(json.dumps(PLAN))
    p["lines"][0]["performance"] = [{"preset": "wave", "at": "Привет", "hand": "left"}]
    plan, _ = SP.validate_plan(p, lib, D.DEFAULT_SETTINGS)
    assert plan["lines"][0]["performance"][0]["hand"] == "left"
    words, total = _words(plan)
    SV.compile_vrm(plan, lib, words, total)
    assert seen["perf"][0]["hand"] == "left"
    p["lines"][0]["performance"][0]["hand"] = "both"
    with pytest.raises(SP.PlanError) as err:
        SP.validate_plan(p, lib, D.DEFAULT_SETTINGS)
    assert "hand — left или right" in " ".join(err.value.problems)


@pytest.mark.skipif(not shutil.which("node"), reason="нет node")
def test_compile_with_real_engine_expansion():
    lib = _lib()
    plan, _ = SP.validate_plan(PLAN, lib, D.DEFAULT_SETTINGS)
    words, total = _words(plan)
    spec = SV.compile_vrm(plan, lib, words, total)["spec"]
    face = next(s for s in spec["shots"] if s["name"].endswith("face_left"))
    assert len(face["camKeys"]) > 10 and face["camKeys"][-1]["roll"] > 5  # голландский угол заваливает кадр
    assert any(a.get("right") == "wave" for a in spec["timeline"]["arms"])


def test_import_vtubika_plans_map_to_series_plan(tmp_path, monkeypatch):
    from test_data_foundation import _store
    from app import s3d_import as I

    store = _store(tmp_path, monkeypatch)
    out = I.import_all(store, plans_dir=ROOT / "tools" / "vtubika_plans", meme=True)
    assert out["project_id"] and len(out["episodes"]) >= 5, out
    eps = {e["source"].get("file"): e for e in (D.get_episode(store, x["id"]) for x in out["episodes"])}
    sticker = eps["sticker.json"]["plan"]
    assert sticker["engine"] == "vrm_desk" and sticker["cast"][0] == "nika" and "kira" in sticker["cast"]
    first = sticker["lines"][0]
    assert first["shots"][0]["camera"] == "chat" and first["vtubika"]["do"] and first["vtubika"]["vrm"]
    meme = next(e for e in out["episodes"] if e["engine"] == "scene3d")
    assert D.get_episode(store, meme["id"])["plan"]["cast"] == ["klod"]
    again = I.import_all(store, plans_dir=ROOT / "tools" / "vtubika_plans", meme=True)
    assert again["project_id"] == out["project_id"] and not again["episodes"]  # повтор ничего не дублирует
