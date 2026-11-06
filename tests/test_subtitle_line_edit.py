"""Правка текста строки в редакторе доходит до базы, до слов и до рендера.

Клипы 545, 546, 548, 549: PUT возвращал правку, а следующий GET — старый текст. Дописанное в конец строки
слово вставало за её концом, сверка строк со словами при записи (``sync_lines_with_words``) отдавала его
соседней строке и пересобирала текст обратно. Данные — того же вида, что у клипа 545."""

from __future__ import annotations

import copy

from app.clip_subtitles import rebuild_words, result_from_payload, sync_lines_with_words
from test_agent_tools import _ctx, run_tool
from test_clip_spec import _setup

# Начало субтитров клипа 545: два голоса, строка кончается вместе с последним словом, строки идут встык.
LINES = [
    {"start": 0.18, "end": 0.96, "text": "Полезай в костюм."},
    {"start": 1.04, "end": 1.4, "text": "Чего?"},
    {"start": 1.6, "end": 3.52, "text": "Морти, тут дерьмо мультивселенского масштаба, и мне"},
    {"start": 3.52, "end": 5.64, "text": "нужно сосредоточиться чуть больше обычного. Надень костюм."},
]


def _w(word, start, end, speaker="A", **extra):
    return {"word": word, "start": start, "end": end, "speaker": speaker, **extra}


WORDS = [
    _w("Полезай", 0.18, 0.64), _w("в", 0.64, 0.74), _w("костюм.", 0.74, 0.96),
    _w("Чего?", 1.04, 1.4, "B"),
    _w("Морти,", 1.6, 1.92), _w("тут", 2.02, 2.07), _w("дерьмо", 2.07, 2.32), _w("мультивселенского", 2.32, 2.88),
    _w("масштаба,", 2.88, 3.4), _w("и", 3.4, 3.45), _w("мне", 3.45, 3.52),
    _w("нужно", 3.52, 3.7), _w("сосредоточиться", 3.7, 4.26), _w("чуть", 4.26, 4.36), _w("больше", 4.36, 4.62),
    _w("обычного.", 4.62, 5.0), _w("Надень", 5.0, 5.234), _w("костюм.", 5.26, 5.64),
]


def _clip(tmp_path, monkeypatch, words=None, lines=None):
    client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    store.set_clip_plan_subtitles(plan["id"], {
        "lines": copy.deepcopy(lines or LINES), "words": copy.deepcopy(words or WORDS), "language": "ru",
        "provider": "whisper", "edited": True, "duration": 23.0, "speakers": {"A": "Рик", "B": "Морти"},
    })
    return client, store, plan


def _edit(client, plan, index, text):
    """Как редактор: шлёт только строки. → (ответ PUT, следующий GET)."""
    url = f"/api/clip-plans/{plan['id']}/subtitles"
    lines = client.get(url).json()["lines"]
    lines[index]["text"] = text
    put = client.put(url, json={"lines": lines}).raise_for_status().json()
    return put, client.get(url).json()


def _line_words(payload, index):
    line = payload["lines"][index]
    return [w for w in payload["words"] if float(w["start"]) >= line["start"] and float(w["end"]) <= line["end"]]


def test_word_added_to_the_end_of_a_line_is_saved(tmp_path, monkeypatch):
    client, _store, plan = _clip(tmp_path, monkeypatch)
    put, got = _edit(client, plan, 0, "Полезай в костюм, Морти.")
    assert [l["text"] for l in got["lines"]] == ["Полезай в костюм, Морти.", *[l["text"] for l in LINES[1:]]]
    assert put["lines"] == got["lines"] and put["words"] == got["words"]  # ответ PUT — то, что записано
    own = _line_words(got, 0)
    assert [w["word"] for w in own] == ["Полезай", "в", "костюм,", "Морти."]
    # Первые слова не сдвинулись, новое и его сосед поделили время соседа, строка не вышла за свой конец.
    assert own[0]["start"] == 0.18 and own[1]["start"] == 0.64
    assert own[-1]["end"] <= 0.96 and own[-1]["start"] > own[-2]["start"]
    # Соседняя строка не получила чужого слова, её слова на месте.
    assert [w["word"] for w in _line_words(got, 1)] == ["Чего?"]


def test_word_added_to_the_start_and_middle_is_saved(tmp_path, monkeypatch):
    client, _store, plan = _clip(tmp_path, monkeypatch)
    _put, got = _edit(client, plan, 1, "Чего ещё?")
    assert got["lines"][1]["text"] == "Чего ещё?"
    _put, got = _edit(client, plan, 2, "Слушай, Морти, тут дерьмо мультивселенского масштаба, и мне")
    assert got["lines"][2]["text"].startswith("Слушай, Морти,")
    assert got["lines"][1]["text"] == "Чего ещё?"
    own = _line_words(got, 2)
    assert own[0]["word"] == "Слушай," and own[0]["start"] >= 1.6 and own[0]["end"] <= own[1]["start"] + 0.001
    assert all(w["end"] <= 3.52 for w in own)
    _put, got = _edit(client, plan, 3, "нужно сосредоточиться чуть-чуть больше обычного, понимаешь. Надень костюм.")
    assert got["lines"][3]["text"] == "нужно сосредоточиться чуть-чуть больше обычного, понимаешь. Надень костюм."


def test_same_word_count_keeps_every_word_time(tmp_path, monkeypatch):
    client, _store, plan = _clip(tmp_path, monkeypatch)
    _put, got = _edit(client, plan, 2, "Морти, тут дерьмо мультивселенная масштаба, и мне")
    own = _line_words(got, 2)
    assert [w["word"] for w in own][3] == "мультивселенная"
    assert [(w["start"], w["end"]) for w in own] == [(w["start"], w["end"]) for w in WORDS[4:11]]


def test_fully_retyped_line_stays_inside_its_span(tmp_path, monkeypatch):
    client, _store, plan = _clip(tmp_path, monkeypatch)
    # «Чего?» длится 0.36 с: четыре новых слова всё равно не вылезают к соседям.
    _put, got = _edit(client, plan, 1, "совсем другой текст тут")
    assert got["lines"][1]["text"] == "совсем другой текст тут"
    assert [w["word"] for w in _line_words(got, 1)] == ["совсем", "другой", "текст", "тут"]
    assert {w["speaker"] for w in _line_words(got, 1)} == {"B"}  # голос строки тот же
    assert [l["text"] for l in got["lines"]][2:] == [l["text"] for l in LINES[2:]]


def test_edit_keeps_voice_and_emphasis_of_each_word():
    words = copy.deepcopy(WORDS[:3])
    words[0] = {**words[0], "emph": 2}
    words[2] = {**words[2], "speaker": "B", "emph": 1}
    out = rebuild_words([{"start": 0.18, "end": 0.96, "text": "Полезай на костюм."}], words)
    assert [w.get("emph") for w in out] == [2, None, 1] and [w["speaker"] for w in out] == ["A", "A", "B"]
    out = rebuild_words([{"start": 0.18, "end": 0.96, "text": "Полезай быстро в костюм."}], words)
    assert [w["word"] for w in out] == ["Полезай", "быстро", "в", "костюм."]
    assert [w.get("emph") for w in out] == [2, None, None, 1] and [w["speaker"] for w in out] == ["A", "A", "A", "B"]


def test_lines_over_each_other_do_not_take_each_others_words(tmp_path, monkeypatch):
    # Клипы 831 и 849: строка лежит внутри другой. По времени слово ничьё, решает текст.
    lines = [{"start": 0.02, "end": 2.44, "text": "атаке, поэтому нужно две руки равномерно"},
             {"start": 2.08, "end": 2.44, "text": "пиздить."},
             {"start": 3.0, "end": 5.0, "text": "да ну"},
             {"start": 3.5, "end": 4.5, "text": "да"}]
    words = [_w(t, a, b) for t, a, b in (("атаке,", 0.02, 0.24), ("поэтому", 0.56, 0.61), ("нужно", 0.71, 0.82),
                                         ("две", 1.16, 1.22), ("руки", 1.32, 1.42), ("равномерно", 1.52, 1.98),
                                         ("пиздить.", 2.08, 2.44), ("да", 3.0, 3.3), ("да", 3.7, 4.2), ("ну", 4.6, 5.0))]
    assert sync_lines_with_words({"lines": lines, "words": words})["lines"] == lines
    client, _store, plan = _clip(tmp_path, monkeypatch, words, lines)
    _put, got = _edit(client, plan, 0, "атаке, поэтому нужно было две руки равномерно")
    assert [l["text"] for l in got["lines"]] == ["атаке, поэтому нужно было две руки равномерно", "пиздить.", "да ну", "да"]


def test_edit_reaches_the_render_and_highlight_follows_the_word(tmp_path, monkeypatch):
    client, store, plan = _clip(tmp_path, monkeypatch)
    spec = client.get(f"/api/clip-plans/{plan['id']}/spec").json()
    spec["render"]["layers"] = [{
        "id": "caps", "type": "captions", "name": "Субтитры", "start": 0.0, "end": 5.64, "track": 0,
        # Подсвечено «Чего?»: четвёртое слово клипа, после правки строки выше — пятое.
        "props": {"highlightWords": [{"i": 3, "w": "Чего?", "t": 1.04}]},
    }]
    client.put(f"/api/clip-plans/{plan['id']}/spec", json=spec).raise_for_status()
    _put, got = _edit(client, plan, 0, "Полезай в костюм, Морти.")
    result = result_from_payload(store.get_clip_plan_subtitles(plan["id"]))  # то, что жжёт рендер
    assert [w.word for w in result.words][:5] == ["Полезай", "в", "костюм,", "Морти.", "Чего?"]
    assert result.segments[0].text == "Полезай в костюм, Морти."
    layer = store.get_clip_plan(plan["id"], include_segments=False)["render_settings"]["layers"][0]
    mark = layer["props"]["highlightWords"][0]
    assert got["words"][mark["i"]]["word"] == "Чего?"


def test_agent_line_edit_is_saved_too(tmp_path, monkeypatch):
    _client, store, plan = _clip(tmp_path, monkeypatch)
    out, ok = run_tool(_ctx(store, plan["id"]), "edit_subtitle_lines", {"edits": [{"i": 0, "text": "Полезай в костюм, Морти."}]})
    assert ok, out
    got = store.get_clip_plan_subtitles(plan["id"])
    assert got["lines"][0]["text"] == "Полезай в костюм, Морти."
    assert [w["word"] for w in _line_words(got, 0)] == ["Полезай", "в", "костюм,", "Морти."]
