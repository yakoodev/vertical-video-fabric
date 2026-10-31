"""Цензура мата одной командой (app/profanity.py, app/censor.py).

Детектор (с ложными срабатываниями), отрезки тишины по уровням, звёздочки в субтитрах
(идемпотентно), ручные mute не затираются, круги «рендер → Whisper» (оба замоканы).
"""

from __future__ import annotations

import threading

import pytest

from app import censor
from app.agent_tools import ToolContext, run_tool
from app.profanity import classify_token, find_profanity, mute_range, star_token
from test_cuts import _mock_render

# ------------------------------------------------------------------ детектор

MAT = ["хуй", "Нахуй", "похуй", "охуеть", "ахуенно", "нихуя", "хуйня", "пизда", "пиздец", "спиздил",
       "распиздяй", "ебать", "Ёбаный", "заебал", "наебали", "уебок", "долбоёб", "съебался", "разъебал",
       "въебал", "выебываться", "бля", "блядь", "Блять", "выблядок", "мудак", "мудила", "залупа",
       "пидор", "пидорас", "гандон", "гондон", "шлюха", "шлюшка", "сука", "суки", "сукин", "сучара"]
CLEAN = ["небо", "небе", "хлеб", "себя", "себе", "вебка", "ребёнок", "учебник", "употреблять",
         "оскорблять", "оскорбляю", "рубля", "корабля", "сабля", "ансамбля", "бляшка", "страхуй",
         "психую", "похудеть", "хуже", "мудрый", "мудрость", "педаль", "Педро", "гондола", "шлюз",
         "сук", "сукно", "сучок", "суккулент", "команда", "блин", "хрен", "херес", "Херсон", "сразу"]


@pytest.mark.parametrize("word", MAT)
def test_detector_finds_mat(word):
    assert classify_token(word) in ("mat", "rude"), word


@pytest.mark.parametrize("word", CLEAN)
def test_detector_skips_ordinary_words(word):
    assert classify_token(word) is None, word


def test_detector_groups_punctuation_and_hyphens():
    assert classify_token("«Блять!»") == "mat"
    assert classify_token("пиздец-то") == "mat"
    assert classify_token("сука") == "rude" and classify_token("сука", ["mat"]) is None
    assert classify_token("блин") is None and classify_token("блин", ["mat", "soft"]) == "soft"
    assert classify_token("херня", ["soft"]) == "soft" and classify_token("херес", ["soft"]) is None
    # Уже звёздочками — тоже мат: повторный запуск находит своё.
    assert classify_token("б****,") == "starred" and classify_token("бл*ть") == "starred"
    assert classify_token("***") is None


def test_stars_keep_punctuation_and_are_idempotent():
    assert star_token("блять") == "б****"
    assert star_token("Блять,") == "Б****,"
    assert star_token("«пиздец-то!»") == "«п*****-то!»"
    assert star_token("небо") == "небо"
    assert star_token("бл*ть") == "б****"
    for token in ("Блять,", "«пиздец-то!»", "сука?"):
        once = star_token(token)
        assert star_token(once) == once


def test_find_profanity_skip():
    words = [{"word": "ну", "start": 0, "end": 0.2}, {"word": "блять", "start": 0.3, "end": 0.7},
             {"word": "сука", "start": 1, "end": 1.4}]
    assert [f["i"] for f in find_profanity(words)] == [1, 2]
    assert [f["i"] for f in find_profanity(words, skip=[2])] == [1]


# ------------------------------------------------------------------ уровни

def test_levels_widen_and_level3_leaves_first_consonant():
    one, two, three = (mute_range(10.0, 10.4, lvl) for lvl in (1, 2, 3))
    assert one == (10.12, 10.34)  # 30–85 %
    assert two[0] < one[0] and two[1] > one[1]
    assert three[0] == pytest.approx(10.06) and three[1] == pytest.approx(10.44)  # слышна первая согласная
    assert three[0] <= two[0] and three[1] >= two[1]
    a, b = mute_range(5.0, 5.08, 1)  # короткое слово — не меньше MIN_SPAN_SEC
    assert b - a == pytest.approx(0.06, abs=1e-3)
    assert mute_range(1, 2, 7) == mute_range(1, 2, 3)  # уровень зажат 1..3


# ------------------------------------------------------------------ клип: mute + звёздочки

def _plan(tmp_path, monkeypatch, mute=None):
    store, service, plan, preset, _calls = _mock_render(tmp_path, monkeypatch, [(0, 10)])
    store.set_clip_plan_render_settings(plan["id"], {"preset_id": preset["id"], "mute": mute or []})
    store.set_clip_plan_subtitles(plan["id"], {
        "lines": [{"start": 1.0, "end": 3.0, "text": "ну блять какое небо"},
                  {"start": 5.0, "end": 6.5, "text": "Сука, опять!"}],
        "words": [{"word": "ну", "start": 1.0, "end": 1.2}, {"word": "блять", "start": 1.3, "end": 1.7},
                  {"word": "какое", "start": 1.8, "end": 2.2}, {"word": "небо", "start": 2.3, "end": 2.8},
                  {"word": "Сука,", "start": 5.0, "end": 5.4}, {"word": "опять!", "start": 5.5, "end": 6.0}],
    })
    return store, service, plan, preset


def _ctx(store, plan_id):
    return ToolContext(store=store, clip_plan_id=plan_id)


def test_censor_quick_mutes_middles_stars_and_keeps_manual(tmp_path, monkeypatch):
    store, _s, plan, _p = _plan(tmp_path, monkeypatch, mute=[{"start": 8.0, "end": 9.0}])
    out, ok = run_tool(_ctx(store, plan["id"]), "censor_profanity", {"verify": False})
    assert ok, out
    assert [(w["i"], w["level"]) for w in out["words"]] == [(1, 1), (4, 1)]
    mute = store.get_clip_plan(plan["id"])["render_settings"]["mute"]
    assert {"start": 8.0, "end": 9.0} in mute  # ручной на месте
    ours = [m for m in mute if m.get("by") == "censor"]
    assert ours == [{"start": 1.42, "end": 1.64, "by": "censor", "level": 1, "style": "silence"},
                    {"start": 5.12, "end": 5.34, "by": "censor", "level": 1, "style": "silence"}]
    subs = store.get_clip_plan_subtitles(plan["id"])
    assert [w["word"] for w in subs["words"]] == ["ну", "б****", "какое", "небо", "С***,", "опять!"]
    assert [line["text"] for line in subs["lines"]] == ["ну б**** какое небо", "С***, опять!"]
    assert [w["start"] for w in subs["words"]] == [1.0, 1.3, 1.8, 2.3, 5.0, 5.5]  # время не тронуто

    # Повторный запуск: звёздочки найдены как мат, свои отрезки заменены, а не удвоены.
    again, ok = run_tool(_ctx(store, plan["id"]), "censor_profanity", {"verify": False})
    assert ok and again["stars"] == 0 and len(again["words"]) == 2
    assert store.get_clip_plan(plan["id"])["render_settings"]["mute"] == mute
    assert store.get_clip_plan_subtitles(plan["id"])["words"] == subs["words"]


def test_censor_levels_remembered_and_overridable(tmp_path, monkeypatch):
    store, _s, plan, _p = _plan(tmp_path, monkeypatch)
    censor.censor_plan(store, plan["id"], {"1": 3})
    levels = lambda: [m["level"] for m in store.get_clip_plan(plan["id"])["render_settings"]["mute"]]  # noqa: E731
    assert levels() == [3, 1]
    censor.censor_plan(store, plan["id"])  # без levels — прежние уровни остаются
    assert levels() == [3, 1]
    censor.censor_plan(store, plan["id"], 2)  # число — всем
    assert levels() == [2, 2]
    out = censor.censor_plan(store, plan["id"], clear=True)
    assert out["removed"] == 2 and store.get_clip_plan(plan["id"])["render_settings"]["mute"] == []


def test_mute_audio_keeps_censor_spans(tmp_path, monkeypatch):
    store, _s, plan, _p = _plan(tmp_path, monkeypatch)
    censor.censor_plan(store, plan["id"])
    out, ok = run_tool(_ctx(store, plan["id"]), "mute_audio", {"ranges": [{"start": 8, "end": 9}]})
    assert ok, out
    mute = store.get_clip_plan(plan["id"])["render_settings"]["mute"]
    assert len([m for m in mute if m.get("by") == "censor"]) == 2 and {"start": 8.0, "end": 9.0} in mute


def test_censor_needs_words(tmp_path, monkeypatch):
    store, _s, plan, _p = _plan(tmp_path, monkeypatch)
    store.set_clip_plan_subtitles(plan["id"], None)
    out, ok = run_tool(_ctx(store, plan["id"]), "censor_profanity", {"verify": False})
    assert not ok and "generate_subtitles" in out["error"]


# ------------------------------------------------------------------ круги проверки (рендер и Whisper — моки)

def _mock_rounds(monkeypatch, store, plan, hearing):
    """``hearing(round, mute)`` → что «слышит» Whisper в готовом файле."""
    calls = {"render": 0, "unload": 0}

    def fake_render(_store, plan_id):
        calls["render"] += 1
        return {"id": 100 + calls["render"], "status": "succeeded", "ffmpeg_preset_id": None}

    def fake_hear(_store, clip):
        mute = store.get_clip_plan(plan["id"])["render_settings"]["mute"]
        return hearing(calls["render"], mute)

    monkeypatch.setattr(censor, "render_plan", fake_render)
    monkeypatch.setattr(censor, "hear_render", fake_hear)
    monkeypatch.setattr(censor, "_release_whisper", lambda: calls.__setitem__("unload", calls["unload"] + 1))
    return calls


def test_verify_bumps_level_until_whisper_stops_hearing(tmp_path, monkeypatch):
    store, _s, plan, _p = _plan(tmp_path, monkeypatch)

    def hearing(n, mute):
        # «блять» узнаётся, пока уровень < 3; «сука» не узнаётся с первого круга.
        lvl = next(m["level"] for m in mute if m["start"] < 2)
        heard = [{"word": "ну", "start": 1.0, "end": 1.2}]
        if lvl < 3:
            heard.append({"word": "блять", "start": 1.35, "end": 1.65})
        return heard

    calls = _mock_rounds(monkeypatch, store, plan, hearing)
    out = censor.verify_plan(store, plan["id"])
    assert calls == {"render": 3, "unload": 1}
    assert [r["heard"] for r in out["rounds"]] == [[1], [1], []]
    assert out["render_id"] == 103 and out["verified"]
    assert [(w["i"], w["level"], w["heard"]) for w in out["words"]] == [(1, 3, False), (4, 1, False)]


def test_verify_reports_context_guess_and_stray(tmp_path, monkeypatch):
    store, _s, plan, _p = _plan(tmp_path, monkeypatch)
    hearing = lambda n, mute: [{"word": "сука", "start": 5.1, "end": 5.3},  # noqa: E731
                               {"word": "пиздец", "start": 8.0, "end": 8.4}]
    calls = _mock_rounds(monkeypatch, store, plan, hearing)
    out = censor.verify_plan(store, plan["id"], {"4": 2}, max_rounds=5)
    # «сука» на 2 → 3, на 3 всё ещё слышна — дальше тишиной не помочь: два круга, не пять.
    assert calls["render"] == 2
    suka = next(w for w in out["words"] if w["i"] == 4)
    assert suka["heard"] and suka["level"] == 3 and censor.CONTEXT_NOTE in suka["note"]
    assert out["unmuted_heard"] == [{"word": "пиздец", "start": 8.0, "end": 8.4}]


def test_verify_without_mat_does_not_render(tmp_path, monkeypatch):
    store, _s, plan, _p = _plan(tmp_path, monkeypatch)
    store.set_clip_plan_subtitles(plan["id"], {"lines": [{"start": 1, "end": 2, "text": "небо"}],
                                               "words": [{"word": "небо", "start": 1, "end": 1.5}]})
    calls = _mock_rounds(monkeypatch, store, plan, lambda n, m: [])
    out = censor.verify_plan(store, plan["id"])
    assert calls["render"] == 0 and out["render_id"] is None and out["words"] == []


def test_censor_job_runs_in_background_once(tmp_path, monkeypatch):
    store, _s, plan, _p = _plan(tmp_path, monkeypatch)
    gate, started = threading.Event(), []

    def slow_verify(_store, plan_id, progress=None, **_kw):
        started.append(plan_id)
        progress({"round": 1, "stage": "рендер"})
        gate.wait(5)
        return {"words": [], "render_id": 7}

    monkeypatch.setattr(censor, "verify_plan", slow_verify)
    first = censor.censor_job(store, plan["id"], wait=0.05)
    assert first["status"] == "running" and first["retry_after_sec"] > 0
    assert censor.job_running(plan["id"])
    out, ok = run_tool(_ctx(store, plan["id"]), "censor_profanity", {"verify": False})
    assert not ok and "идёт проверка" in out["error"]
    gate.set()
    done = censor.censor_job(store, plan["id"], wait=5)
    assert done["status"] == "done" and done["render_id"] == 7 and started == [plan["id"]]
    assert not censor.job_running(plan["id"])


# ------------------------------------------------------------------ HTTP

def test_http_censor_quick_snapshot_and_errors(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import app.main as app_main
    from app.settings import settings

    store, _s, plan, _p = _plan(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "auth_enabled", False)
    monkeypatch.setattr(app_main, "store", store)
    client = TestClient(app_main.app)
    resp = client.post(f"/api/clip-plans/{plan['id']}/censor", json={"verify": False})
    assert resp.status_code == 200, resp.text
    assert [w["word"] for w in resp.json()["words"]] == ["б****", "С***,"]
    assert store.count_clip_plan_history(plan["id"]) == 1  # Ctrl+Z откатит
    assert client.post("/api/clip-plans/999999/censor", json={"verify": False}).status_code == 404
    store.set_clip_plan_subtitles(plan["id"], None)
    bad = client.post(f"/api/clip-plans/{plan['id']}/censor", json={"verify": False})
    assert bad.status_code == 400 and "generate_subtitles" in bad.json()["detail"]
