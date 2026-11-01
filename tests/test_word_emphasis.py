"""Эмфаза слова в субтитрах (app/word_emphasis.py): метка emph у слова, инструмент
emphasize_words (номера, тексты, авто по строке), метка переживает правку строк и рендер,
comp.words[i].e для слоя captions, ASS — \\fscx/\\fscy на страницу и \\t-прыжок."""

from __future__ import annotations

import re

import pytest

from app.agent_tools import ToolContext, get_tool, run_tool
from app.clip_history import snapshot
from app.clip_subtitles import result_from_payload, save_lines
from app.motion import caption_words, normalize_layers
from app.settings import settings
from app.subtitles.ass import write_ass_subtitles
from app.subtitles.contracts import SubtitleResult, SubtitleWord
from app.subtitles.timing import normalize_subtitle_timeline
from app.word_emphasis import key_word_score, level_of, pick_heuristic
from test_cuts import _mock_render


def _ctx(store, plan_id):
    return ToolContext(store=store, clip_plan_id=plan_id, on_first_change=lambda: snapshot(store, plan_id, "тест"))


WORDS = [
    {"word": "Я", "start": 0.0, "end": 0.2}, {"word": "заработал", "start": 0.25, "end": 0.7},
    {"word": "миллион", "start": 0.75, "end": 1.2}, {"word": "рублей.", "start": 1.25, "end": 1.6},
    {"word": "Это", "start": 2.5, "end": 2.7}, {"word": "безумие!", "start": 2.75, "end": 3.2},
    {"word": "За", "start": 4.2, "end": 4.4}, {"word": "100", "start": 4.45, "end": 4.7},
    {"word": "дней", "start": 4.75, "end": 5.0},
]


def _plan(tmp_path, monkeypatch):
    store, service, plan, preset, calls = _mock_render(tmp_path, monkeypatch, [(0, 10)])
    from app.ai.speakers import lines_from_words

    save_lines(store, plan["id"], lines_from_words(WORDS), WORDS)
    return store, service, store.get_clip_plan(plan["id"]), preset


def test_levels_and_heuristic_pick():
    assert [level_of(v) for v in ("pop", "shake", "off", 1, 2, 5, "1", None, True)] == [1, 2, 0, 1, 2, 2, 1, 0, 1]
    assert key_word_score("это") == 0 and key_word_score("100") > key_word_score("безумие!") > key_word_score("рублей")
    lines = [[{"i": 0, "word": "Я"}, {"i": 1, "word": "заработал"}, {"i": 2, "word": "миллион"}],
             [{"i": 3, "word": "Это"}, {"i": 4, "word": "безумие!"}],
             [{"i": 5, "word": "За"}, {"i": 6, "word": "100"}, {"i": 7, "word": "дней"}],
             [{"i": 8, "word": "ну"}, {"i": 9, "word": "да"}]]
    assert pick_heuristic(lines) == [1, 4, 6]  # самое длинное; «!»; число; служебные — ничего


def test_emphasize_words_tool_marks_words_and_survives_edits(tmp_path, monkeypatch):
    store, _service, plan, _preset = _plan(tmp_path, monkeypatch)
    ctx = _ctx(store, plan["id"])
    out, ok = run_tool(ctx, "emphasize_words", {"indices": [2], "words": ["безумие"], "style": "shake"})
    assert ok, out
    assert {(w["i"], w["emph"]) for w in out["emphasized"]} == {(2, 2), (5, 2)}
    words = store.get_clip_plan_subtitles(plan["id"])["words"]
    assert words[2]["emph"] == 2 and "emph" not in words[0]

    # Снять одно, авто на остальные строки — строки с эмфазой не трогает.
    out, ok = run_tool(ctx, "emphasize_words", {"words": ["безумие"], "style": "off"})
    assert ok and out["count"] == 1
    out, ok = run_tool(ctx, "emphasize_words", {"auto": True})
    assert ok and out["picked_by"] == "heuristic"
    marked = {w["word"]: w["emph"] for w in out["emphasized"]}
    assert marked == {"миллион": 2, "безумие!": 1, "100": 1}

    # Правка текста строки (тайминги слов сохраняются) — метка на месте; get_clip показывает её.
    payload = store.get_clip_plan_subtitles(plan["id"])
    lines = [dict(line) for line in payload["lines"]]
    lines[-1]["text"] = lines[-1]["text"]  # та же строка
    save_lines(store, plan["id"], lines)
    words = store.get_clip_plan_subtitles(plan["id"])["words"]
    assert [w.get("emph", 0) for w in words] == [0, 0, 2, 0, 0, 1, 0, 1, 0]
    clip, ok = run_tool(ctx, "get_clip", {})
    assert ok
    first = clip["subtitles"]["lines"][0]
    assert first["emph"] == {2: 2}

    # comp.words для слоя captions несут уровень (e), ASS-результат — тоже.
    comp = caption_words(store.get_clip_plan_subtitles(plan["id"]))
    assert [w.get("e", 0) for w in comp] == [0, 0, 2, 0, 0, 1, 0, 1, 0]
    result = result_from_payload(store.get_clip_plan_subtitles(plan["id"]))
    assert [w.emph for w in result.words][2] == 2
    assert normalize_subtitle_timeline(result, 4.8).words[2].emph == 2

    # set_subtitle_words: emph принимается и проверяется.
    fresh = [{**w, "emph": 1} if w["word"] == "дней" else w for w in WORDS]
    out, ok = run_tool(ctx, "set_subtitle_words", {"words": fresh})
    assert ok, out
    assert store.get_clip_plan_subtitles(plan["id"])["words"][-1]["emph"] == 1
    assert not run_tool(ctx, "set_subtitle_words", {"words": [{**WORDS[0], "emph": "громко"}]})[1]

    # Ошибки — понятным текстом; clear снимает всё.
    assert not run_tool(ctx, "emphasize_words", {"words": ["нетакого"]})[1]
    assert not run_tool(ctx, "emphasize_words", {"indices": [1], "style": "громко"})[1]
    out, ok = run_tool(ctx, "emphasize_words", {"clear": True})
    assert ok and out["count"] == 0
    assert get_tool("emphasize_words").mutating


def test_rendered_subtitles_keep_emphasis_through_cuts(tmp_path, monkeypatch):
    store, service, plan, preset = _plan(tmp_path, monkeypatch)
    run_tool(_ctx(store, plan["id"]), "emphasize_words", {"words": ["миллион"]})
    store.set_clip_plan_render_settings(plan["id"], {"cuts": [{"start": 1.9, "end": 2.4}]})
    clip = store.create_clip(plan["source_id"], clip_plan_id=plan["id"], ffmpeg_preset_id=preset["id"])
    result = service._plan_subtitles_for_clip(clip["id"], 30.0)
    assert [(w.word, w.emph) for w in result.words if w.emph] == [("миллион", 1)]


def _events(text: str) -> list[str]:
    return re.findall(r"^Dialogue: .*?,,0,0,0,,(.*)$", text, re.M)


def test_ass_scales_emphasized_word_for_whole_page_and_pops_on_its_word(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "subtitle_dir", tmp_path)
    words = [SubtitleWord("Я", 0.0, 0.3), SubtitleWord("заработал", 0.35, 0.8), SubtitleWord("миллион", 0.85, 1.3, emph=1),
             SubtitleWord("Потом", 3.0, 3.3), SubtitleWord("ушёл", 3.35, 3.7)]
    result = SubtitleResult(text="", language="ru", duration=4.0, words=words)
    profile = {"font_size": 74, "max_words_per_line": 4, "active_word_color": "#FACC15"}
    events = _events(write_ass_subtitles(result, profile, tmp_path / "e.ass").read_text(encoding="utf-8"))
    first_page, second_page = events[:3], events[3:]
    # Вся страница: ключевое слово ×125%, остальные явно 100% — раскладка не прыгает между событиями.
    for event in first_page:
        assert r"\fscx125\fscy125" in event
        assert event.count(r"\fscx100\fscy100") == 2
    # Прыжок — только в событии самого слова.
    assert r"\t(0,80,\fscx150\fscy150)\t(80,240,\fscx125\fscy125)" in first_page[2]
    assert r"\t(" not in first_page[0] + first_page[1]
    # Страница без эмфазы — как раньше, без тегов масштаба.
    assert all(r"\fscx" not in event for event in second_page)
    # Сказанное ключевое слово остаётся цветом активного.
    from app.subtitles.ass import ass_color

    assert (r"{\c" + ass_color("#FACC15") + r"\alpha&H00&\fscx125") in first_page[2]


def test_captions_layer_props_and_rest_endpoint(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import app.main as app_main

    layer = normalize_layers([{"type": "captions", "start": 0, "end": 3, "props": {"emphScale": 5, "emphColor": "#ff0000"}}])[0]
    assert layer["props"]["emphScale"] == 2 and layer["props"]["emphColor"] == "#ff0000"

    store, _service, plan, _preset = _plan(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "auth_enabled", False)
    monkeypatch.setattr(app_main, "store", store)
    client = TestClient(app_main.app)
    resp = client.post(f"/api/clip-plans/{plan['id']}/subtitles/emphasis", json={"indices": [1], "style": "pop"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["subtitles"]["words"][1]["emph"] == 1
    resp = client.post(f"/api/clip-plans/{plan['id']}/subtitles/emphasis", json={"indices": [99], "style": "pop"})
    assert resp.status_code == 400
    # Сохранение строк из редактора не теряет эмфазу.
    lines = store.get_clip_plan_subtitles(plan["id"])["lines"]
    resp = client.put(f"/api/clip-plans/{plan['id']}/subtitles", json={"lines": lines})
    assert resp.status_code == 200, resp.text
    assert store.get_clip_plan_subtitles(plan["id"])["words"][1]["emph"] == 1


@pytest.mark.parametrize("ref, index", [(2, 2), ("МИЛЛИОН", 2), ({"text": "это"}, 4), ({"line": 1, "word": 1}, 5)])
def test_word_refs(ref, index):
    from app.agent_tools import _emph_index
    from app.word_emphasis import line_words

    payload = {"words": WORDS, "lines": [{"start": 0, "end": 1.6, "text": "x"}, {"start": 2.5, "end": 3.2, "text": "y"},
                                         {"start": 4.2, "end": 5.0, "text": "z"}]}
    assert _emph_index(WORDS, line_words(payload), ref) == index
