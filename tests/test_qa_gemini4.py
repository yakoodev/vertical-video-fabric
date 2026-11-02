"""QA 4 Gemini-монтажёра (клип 896, плейбук vtuber-story): находки и их починка.

1. Цензура заглушила сам панчлайн «обосралась» (5 вызовов, soft включил агент).
2. Звук-акцент встал на «это» за 2.8 с до панчлайна (at_word).
3. Автомонтаж при плейбуке поставил свой жёлтый хук, удар и 5 наездов.
4. Реальные люди на вставках (гифка с офисным работником, фото студента).
5. Прозрачная гифка во весь кадр — на чёрном.
6. trim_ui срезал макушку, когда чат можно было убрать сбоку.
7. Отчёт агента умолчал о заглушённом панчлайне, реальных людях и промахе звука.
"""

from __future__ import annotations

import pytest

from app import censor
from app.agent_tools import ToolContext, run_tool
from app.profanity import DEFAULT_GROUPS, GROUPS, classify_token
from test_cuts import _mock_render

REQUEST = ("Смонтируй по плейбуку vtuber-story: история витубера Муры — она думает, что по монитору ползёт "
           "огромный таракан; панчлайн «Я обосралась» в конце. Мат заглушить (censor_profanity).")


def _plan(tmp_path, monkeypatch, words=None, mute=None, layers=None):
    store, _service, plan, preset, _calls = _mock_render(tmp_path, monkeypatch, [(0, 10)])
    store.set_clip_plan_render_settings(plan["id"], {"preset_id": preset["id"], "mute": mute or [],
                                                     **({"layers": layers} if layers else {})})
    words = words or [("Вот", 1.0, 1.2), ("это", 1.3, 1.5), ("лезет.", 1.6, 2.0),
                      ("Я", 4.0, 4.9), ("обосралась,", 5.0, 5.9), ("пиздец.", 6.2, 6.8)]
    store.set_clip_plan_subtitles(plan["id"], {
        "lines": [{"start": 1.0, "end": 2.0, "text": " ".join(w for w, _a, _b in words[:3])},
                  {"start": 4.0, "end": 6.8, "text": " ".join(w for w, _a, _b in words[3:])}],
        "words": [{"word": w, "start": a, "end": b} for w, a, b in words],
    })
    return store, plan


def _words(store, plan):
    return [w["word"] for w in store.get_clip_plan_subtitles(plan["id"])["words"]]


def _censor_spans(store, plan):
    return [m for m in store.get_clip_plan(plan["id"])["render_settings"].get("mute") or [] if m.get("by") == "censor"]


# ------------------------------------------------------------------ 1. цензура и панчлайн


def test_dictionary_rude_vernacular_is_not_mat():
    # «обосралась» — грубое просторечие (soft), не обсценная лексика: по умолчанию не глушится.
    assert classify_token("обосралась,", DEFAULT_GROUPS) is None
    assert classify_token("обосралась", GROUPS) == "soft"
    assert classify_token("пиздец.", DEFAULT_GROUPS) == "mat"


def test_quoted_words_and_keep_match():
    assert censor.quoted_words(REQUEST) == ["обосралась"]
    assert censor.keep_match("о*********,", ["обосралась"]) == "обосралась"  # звёздочки: первая буква и длина
    assert censor.keep_match("Обосрался!", ["обосралась"]) == "обосралась"  # словоформа
    assert censor.keep_match("п*****", ["обосралась"]) is None
    assert censor.owner_asks_soft(REQUEST) is False  # «обосралась» в кавычках — не просьба глушить soft
    assert censor.owner_asks_soft("заглуши и мягкий мат тоже") is True


def test_soft_from_agent_is_ignored_and_punchline_stays(tmp_path, monkeypatch):
    store, plan = _plan(tmp_path, monkeypatch)
    ctx = ToolContext(store=store, clip_plan_id=plan["id"], request=REQUEST)
    out, ok = run_tool(ctx, "censor_profanity", {"verify": False, "soft": True, "groups": ["mat", "rude", "soft"]})
    assert ok, out
    assert [w["word"] for w in out["words"]] == ["п*****."]
    assert any("не мат" in n for n in out["notes"])
    assert _words(store, plan)[4] == "обосралась,"
    assert len(_censor_spans(store, plan)) == 1


def test_repeat_calls_are_already_applied_even_with_explicit_args(tmp_path, monkeypatch):
    # QA 4: 5 вызовов — явные levels/style/groups обходили «уже применено».
    store, plan = _plan(tmp_path, monkeypatch)
    ctx = ToolContext(store=store, clip_plan_id=plan["id"], request=REQUEST)
    assert run_tool(ctx, "censor_profanity", {"verify": False})[1]
    spans = _censor_spans(store, plan)
    for args in ({"levels": 3}, {"style": "silence"}, {"groups": ["mat", "soft"]}, {"soft": True}):
        again, ok = run_tool(ctx, "censor_profanity", {"verify": False, **args})
        assert ok and again["status"] == "already" and "НЕ применены" in again["note"], args
    assert _censor_spans(store, plan) == spans
    # Переделать — только явным redo.
    redo, ok = run_tool(ctx, "censor_profanity", {"verify": False, "redo": True, "levels": 3})
    assert ok and redo["words"][0]["level"] == 3


def test_punchline_muted_earlier_is_restored(tmp_path, monkeypatch):
    # Состояние клипа 896: панчлайн уже со звёздочками и под отрезком цензуры.
    words = [("Вот", 1.0, 1.2), ("это", 1.3, 1.5), ("лезет.", 1.6, 2.0),
             ("Я", 4.0, 4.9), ("о*********,", 5.0, 5.9), ("п*****.", 6.2, 6.8)]
    mute = [{"start": 5.18, "end": 5.86, "by": "censor", "level": 2, "style": "reverse"},
            {"start": 6.38, "end": 6.71, "by": "censor", "level": 1, "style": "reverse"}]
    store, plan = _plan(tmp_path, monkeypatch, words=words, mute=mute)
    ctx = ToolContext(store=store, clip_plan_id=plan["id"], request=REQUEST)
    out, ok = run_tool(ctx, "censor_profanity", {"verify": False})
    assert ok, out
    assert out.get("kept") == [{"i": 4, "word": "обосралась"}] and "НЕ глушу" in out["note"]
    assert _words(store, plan)[4:] == ["обосралась,", "п*****."]
    assert store.get_clip_plan_subtitles(plan["id"])["lines"][1]["text"] == "Я обосралась, п*****."
    assert [(m["start"], m["end"]) for m in _censor_spans(store, plan)] == [(6.38, 6.71)]
    # Явно по просьбе владельца — force_words глушит и слово из кавычек.
    owner = REQUEST + " Мягкий мат тоже глуши."
    forced, ok = run_tool(ToolContext(store=store, clip_plan_id=plan["id"], request=owner), "censor_profanity",
                          {"verify": False, "soft": True, "force_words": [4]})
    assert ok and [w["i"] for w in forced["words"]] == [4, 5]


def test_word_under_sound_accent_is_kept(tmp_path, monkeypatch):
    layers = [{"id": "boom", "type": "audio", "start": 5.0, "end": 6.2,
               "props": {"asset_id": "sfx:freesound/drama-boom.ogg", "volume": 0.4}}]
    store, plan = _plan(tmp_path, monkeypatch, layers=layers)
    owner = "Смонтируй, мягкий мат тоже глуши."  # soft разрешён, но слово под ударом — панчлайн
    out, ok = run_tool(ToolContext(store=store, clip_plan_id=plan["id"], request=owner), "censor_profanity",
                       {"verify": False, "soft": True})
    assert ok and [w["i"] for w in out["words"]] == [5] and out["kept"] == [{"i": 4, "word": "обосралась"}]


def test_without_owner_request_soft_works_as_before(tmp_path, monkeypatch):
    # MCP/прямые вызовы без запроса владельца: soft — как раньше.
    store, plan = _plan(tmp_path, monkeypatch)
    out, ok = run_tool(ToolContext(store=store, clip_plan_id=plan["id"]), "censor_profanity",
                       {"verify": False, "soft": True})
    assert ok and [w["i"] for w in out["words"]] == [4, 5]
