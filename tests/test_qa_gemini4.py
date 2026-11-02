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


# ------------------------------------------------------------------ 2. звук-акцент на слово (at_word)

# Строки 14–15 клипа 896 (clip_after.json): панчлайн уже со звёздочками после цензуры.
QA4_LINES = [
    [("Вот", 25.44, 25.63), ("это", 25.72, 25.81), ("вот,", 25.9, 25.95), ("видите,", 26.04, 26.17),
     ("она", 26.28, 26.33), ("еще", 26.42, 26.53), ("лезет?", 26.62, 27.13)],
    [("Я", 28.0, 29.03), ("о*********,", 29.12, 30.07), ("п*****.", 30.52, 31.17)],
]


def _qa4_plan(tmp_path, monkeypatch):
    store, _service, plan, preset, _calls = _mock_render(tmp_path, monkeypatch, [(0, 32)])
    store.set_clip_plan_render_settings(plan["id"], {"preset_id": preset["id"]})
    store.set_clip_plan_subtitles(plan["id"], {
        "lines": [{"start": ws[0][1], "end": ws[-1][2], "text": " ".join(w for w, _a, _b in ws)} for ws in QA4_LINES],
        "words": [{"word": w, "start": a, "end": b} for ws in QA4_LINES for w, a, b in ws],
    })
    return store, plan


def test_at_word_finds_punchline_under_stars_and_forms(tmp_path, monkeypatch):
    from app.agent_tools import word_at, word_time

    store, plan = _qa4_plan(tmp_path, monkeypatch)
    ctx = ToolContext(store=store, clip_plan_id=plan["id"])
    # Раньше «обосралась» по тексту не находилось: в субтитрах «о*********,».
    hit = word_at(ctx, {"text": "обосралась"})
    assert hit["t"] == 29.12 and hit["line"] == 1 and "звёздочками" in hit["note"]
    assert word_time(ctx, "Обосралась!") == 29.12  # регистр и пунктуация
    assert word_time(ctx, {"text": "лезет"}) == 26.62
    assert word_time(ctx, {"text": "ещё"}) == 26.42  # ё/е
    assert word_time(ctx, {"text": "видит"}) == 26.04  # словоформа
    # Номер строки съехал на 1 (как у агента в QA 4), но слово названо — верим слову.
    fixed = word_at(ctx, {"line": 0, "word": 1, "text": "обосралась"})
    assert fixed["t"] == 29.12 and "«это»" in fixed["note"]
    assert word_at(ctx, {"line": 0, "word": 1})["word"] == "это"


def test_add_layers_reports_the_word_under_the_sound(tmp_path, monkeypatch):
    from test_agent_tools import _sfx_pack

    store, plan = _qa4_plan(tmp_path, monkeypatch)
    _sfx_pack(tmp_path, monkeypatch, files=("drama-boom.ogg", "impact-low.ogg"))
    ctx = ToolContext(store=store, clip_plan_id=plan["id"])
    out, ok = run_tool(ctx, "add_layers", {"layers": [
        {"type": "audio", "at_word": {"line": 0, "word": 1}, "duration": 1.2,
         "props": {"asset_id": "sfx:freesound/drama-boom.ogg", "volume": 0.4}},
        {"type": "audio", "at_word": {"text": "обосралась"}, "duration": 1.2,
         "props": {"asset_id": "sfx:freesound/impact-low.ogg", "volume": 0.4}}]})
    assert ok, out
    assert "«это»" in out["on_words"][0] and "«о*********,»" in out["on_words"][1]
    starts = sorted(float(l["start"]) for l in store.get_clip_plan(plan["id"])["render_settings"]["layers"])
    assert starts == [25.72, 29.12]


# ------------------------------------------------------------------ 3. автомонтаж при активном плейбуке


def _speech(text: str, start: float = 0.5, step: float = 0.4):
    out, t = [], start
    for w in text.split():
        out.append({"word": w, "start": round(t, 2), "end": round(t + 0.3, 2)})
        t += step
    return out


def test_auto_edit_under_vtuber_story_has_no_own_punches_and_white_hook():
    from app.auto_edit import build_auto_edit

    words = _speech("ребят я сижу думаю огромная какая-то лезет по монитору а это в клипе я обосралась! " * 3)
    duration = words[-1]["end"] + 1
    plain = build_auto_edit("Таракан на экране?!", words, [0.3] * 200, duration)
    assert plain["camera"] and any(l["id"] == "auto_boom" for l in plain["layers"])  # без плейбука — как было

    book = build_auto_edit("Таракан на экране?!", words, [0.3] * 200, duration, playbook="vtuber-story")
    ids = {l["id"] for l in book["layers"]}
    assert book["camera"] == [] and "auto_boom" not in ids and "auto_whoosh" not in ids
    hook = next(l for l in book["layers"] if l["id"] == "auto_hook")
    props = hook["props"]
    assert props["color"] == "#ffffff" and not props["uppercase"] and props["size"] >= 90
    assert props["stroke"]["color"] == "#111111" and props["text"] == "Таракан на экране?!"
    assert 8.0 <= hook["end"] <= 12.0
    caps = next(l for l in book["layers"] if l["type"] == "captions")
    assert caps["props"]["maxWords"] == 2 and caps["props"]["uppercase"] is False

    other = build_auto_edit("Таракан", words, [0.3] * 200, duration, playbook="meme-insert")
    assert other["camera"] == [] and {l["type"] for l in other["layers"]} == {"captions"}  # без своего хука


def test_auto_edit_tool_takes_playbook_from_context_and_keeps_agent_camera(tmp_path, monkeypatch):
    from test_clip_spec import _store_plan

    store, plan = _store_plan(tmp_path, monkeypatch, [])
    camera = [{"t": 3.0, "zoom": 1.0}, {"t": 3.2, "zoom": 1.15}]
    store.set_clip_plan_render_settings(plan["id"], {"camera": camera, "layers": [
        {"id": "auto_boom", "type": "audio", "start": 2.0, "end": 3.0,
         "props": {"asset_id": "sfx:freesound/drama-boom.ogg"}}]})
    store.set_clip_plan_subtitles(plan["id"], {"lines": [], "words": _speech("это огромный таракан на мониторе " * 4)})

    def no_waveform(*a, **k):
        raise RuntimeError("нет волны")

    monkeypatch.setattr("app.clip_waveform.clip_waveform", no_waveform)
    ctx = ToolContext(store=store, clip_plan_id=plan["id"], request=REQUEST, playbook="vtuber-story")
    out, ok = run_tool(ctx, "auto_edit", {})
    assert ok, out
    assert out["playbook"] == "vtuber-story" and "наездов" in out["note"]
    render = store.get_clip_plan(plan["id"])["render_settings"]
    assert [round(k["t"], 2) for k in render["camera"]] == [3.0, 3.2]  # камера агента на месте
    assert not [l for l in render["layers"] if l["id"] in ("auto_boom", "auto_whoosh")]


# ------------------------------------------------------------------ 4. реальные люди на вставках


class _FakeVision:
    """Gemini-зрение: отвечает по подписи ассета (как будто смотрит на кадр)."""

    calls = 0
    answers: dict[str, dict] = {}

    def __init__(self, *a, **k):
        pass

    def generate_content(self, model, payload):
        type(self).calls += 1
        prompt = payload["contents"][0]["parts"][0]["text"]
        assert "РЕАЛЬНЫЙ человек" in prompt and any("inlineData" in p for p in payload["contents"][0]["parts"])
        import json

        answer = type(self).answers.get("current", {"real_person": False, "what": "мультяшный таракан"})
        return {"candidates": [{"content": {"parts": [{"text": json.dumps(answer, ensure_ascii=False)}]}}]}


def _vision(monkeypatch, real: bool, what: str):
    from app.settings import settings

    monkeypatch.setattr(settings, "gemini_api_key", "test-key", raising=False)
    monkeypatch.setattr("app.ai.people_check.GeminiClient", _FakeVision)
    _FakeVision.calls = 0
    _FakeVision.answers = {"current": {"real_person": real, "what": what}}


def _image_asset(store, tmp_path, label="angry monitor"):
    from PIL import Image

    path = tmp_path / f"{label.replace(' ', '_')}.png"
    Image.new("RGB", (64, 48), (200, 120, 40)).save(path)
    return store.create_montage_asset(kind="image", label=label, file_path=str(path), width=64, height=48)


def test_people_check_is_cached_in_asset_and_skipped_without_key(tmp_path, monkeypatch):
    from app.ai import people_check
    from app.settings import settings

    store, _plan_ = _plan(tmp_path, monkeypatch)
    asset = _image_asset(store, tmp_path)
    monkeypatch.setattr(settings, "gemini_api_key", "", raising=False)
    skipped = people_check.check_asset(store, asset)
    assert skipped["real_person"] is None and "нет ключа Gemini" in skipped["skipped"]
    assert store.get_montage_asset(asset["id"])["people_check"] is None  # пропуск не кэшируется

    _vision(monkeypatch, True, "офисный работник бьёт компьютер")
    first = people_check.check_asset(store, asset)
    assert first == {"real_person": True, "what": "офисный работник бьёт компьютер"} and _FakeVision.calls == 1
    cached = store.get_montage_asset(asset["id"])
    assert cached["people_check"]["real_person"] is True
    again = people_check.check_asset(store, cached)
    assert again["cached"] and _FakeVision.calls == 1  # второй раз модель не зовём


def test_add_insert_refuses_real_person_under_playbook(tmp_path, monkeypatch):
    store, plan = _plan(tmp_path, monkeypatch)
    asset = _image_asset(store, tmp_path, "sleeping student")
    _vision(monkeypatch, True, "спящий студент за партой")
    ctx = ToolContext(store=store, clip_plan_id=plan["id"], request=REQUEST, playbook="vtuber-story")
    out, ok = run_tool(ctx, "add_insert", {"asset_id": asset["id"], "at": 2.0, "duration": 2.0, "mode": "full"})
    assert not ok and "реальный человек (спящий студент за партой)" in out["error"] and "рисованную" in out["error"]
    assert not store.get_clip_plan(plan["id"])["render_settings"].get("inserts")
    # Без запрета (нет плейбука и слов владельца) — встаёт, но с предупреждением.
    free = ToolContext(store=store, clip_plan_id=plan["id"], request="вставь мем на 2 с")
    out, ok = run_tool(free, "add_insert", {"asset_id": asset["id"], "at": 2.0, "duration": 2.0, "mode": "full"})
    assert ok and any("реальный человек" in w for w in out["warnings"]) and _FakeVision.calls == 1  # кэш
    # Рисованное — можно и при плейбуке.
    cartoon = _image_asset(store, tmp_path, "cartoon cockroach")
    _vision(monkeypatch, False, "мультяшный таракан")
    out, ok = run_tool(ctx, "add_insert", {"asset_id": cartoon["id"], "at": 5.0, "duration": 2.0, "mode": "full"})
    assert ok and out["people_check"].startswith("реальных людей нет")


def test_add_image_from_url_drops_real_person_when_order_forbids(tmp_path, monkeypatch):
    store, plan = _plan(tmp_path, monkeypatch)
    made = {}

    def fake_add_from_url(store_, url, label="", description=""):
        made["asset"] = _image_asset(store_, tmp_path, label or "angry monitor")
        return made["asset"]

    monkeypatch.setattr("app.ai.asset_generate.add_from_url", fake_add_from_url)
    _vision(monkeypatch, True, "офисный работник у монитора")
    ctx = ToolContext(store=store, clip_plan_id=plan["id"], request=REQUEST.replace(
        "Мат заглушить", "Вставки — только мемы/картинки без фото реальных людей. Мат заглушить"))
    out, ok = run_tool(ctx, "add_image_from_url", {"url": "https://example.com/a.gif", "label": "angry monitor"})
    assert not ok and "без фото реальных людей" in out["error"]
    with pytest.raises(KeyError):
        store.get_montage_asset(made["asset"]["id"])  # скачанное под запрет удалено


def test_lint_notes_real_person_inserts(tmp_path, monkeypatch):
    from app.quality_lint import people_findings

    store, _plan_ = _plan(tmp_path, monkeypatch)
    asset = _image_asset(store, tmp_path)
    store.update_montage_asset(asset["id"], people_check={"real_person": True, "what": "офисный работник"})
    found = people_findings(store, {"inserts": [{"asset_id": asset["id"], "at": 12.97, "mode": "full"}]})
    assert len(found) == 1 and "реальный человек (офисный работник)" in found[0]["message"]

