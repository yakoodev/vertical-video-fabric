"""Страницы субтитров по фразам (app/subtitles/phrasing.py), ASS-стили libass.

Пилот 17.11: «меня накажут, если я здесь так | и буду их булить» — страница по числу слов рвала фразу и
кончалась на «так». Тот же алгоритм во frontend/src/motion/layers/phrasing.ts (слой captions) — обе
стороны сверяются с tests/fixtures/phrase_pages.json (node/motion.test.mjs — со своей стороны).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.subtitles import phrasing as P

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "phrase_pages.json").read_text(encoding="utf-8"))
PILOT = "меня накажут, если я здесь так и буду их булить"


def _texts(words, pages):
    return [" ".join(words[i][0] for i in page) for page in pages]


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=[c["name"] for c in FIXTURE["cases"]])
def test_fixture_pages(case):
    ws = case["words"]
    got = P.split_phrase([w[0] for w in ws], [w[1] for w in ws], [w[2] for w in ws], case["cap"], None, case["hold"])
    assert got == case["pages"], _texts(ws, got)


def test_word_lists_match_fixture():
    assert sorted(P.FUNCTION_WORDS) == FIXTURE["lists"]["function"]
    assert sorted(P.WEAK_WORDS) == FIXTURE["lists"]["weak"]
    assert sorted(P.ATTACH_WORDS) == FIXTURE["lists"]["attach"]


def test_pilot_sentence_breaks_on_comma_never_after_conjunction():
    case = next(c for c in FIXTURE["cases"] if c["name"] == "pilot_cap3")
    pages = _texts(case["words"], case["pages"])
    assert pages[0] == "меня накажут,"
    assert "так и буду" in pages  # «так и буду» — вместе, а не «…здесь так | и буду…»
    for page in pages[:-1]:
        assert P.bare(page.split()[-1]) not in P.FUNCTION_WORDS, page


def test_break_cost_prefers_punctuation_and_pause():
    assert P.break_cost("накажут,", "если", 0.05) < 0 < P.break_cost("и", "буду", 0.05)
    assert P.break_cost("сюда", "сейчас", 0.4) < P.break_cost("сюда", "сейчас", 0.1) == 0
    assert P.break_cost("баг", "— это", 0.0) == 0 and P.break_cost("баг —", "это", 0.0) < 0
    assert P.break_cost("ты", "бы", 0.0) > P.break_cost("ты", "видел", 0.0)  # «бы» не начинает страницу
    assert P.bare("«Так,»") == "так" and P.ends_with_pause_punct("накажут,»")


def test_min_display_time_merges_fast_words():
    case = next(c for c in FIXTURE["cases"] if c["name"] == "fast_words_cap3")
    ws = case["words"]
    for page in case["pages"]:
        if len(page) >= 2:
            shown = ws[page[-1]][2] - ws[page[0]][1] + case["hold"]
            assert shown >= 0.6  # слова по 0.12 с: страница не мелькает (2 слова было бы 0.67 с)
    assert [len(p) for p in case["pages"]] == [3, 3]


def _ass_pages(tmp_path, monkeypatch, words, **profile):
    from app.settings import settings
    from app.subtitles.ass import write_ass_subtitles
    from app.subtitles.contracts import SubtitleResult, SubtitleSegment, SubtitleWord

    monkeypatch.setattr(settings, "subtitle_dir", tmp_path)
    items = [SubtitleWord(word=w, start=a, end=b) for w, a, b in words]
    result = SubtitleResult(text=" ".join(w for w, _a, _b in words), language="ru", duration=10.0,
                            segments=[SubtitleSegment(start=0, end=10.0, text="")], words=items)
    prof = {"font_family": "Oswald", "font_size": 90, "max_words_per_line": 3, "uppercase": 0, **profile}
    text = write_ass_subtitles(result, prof, tmp_path / "s.ass", width=1080, height=1920).read_text(encoding="utf-8")
    pages: list[str] = []
    import re

    for line in text.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        body = re.sub(r"\{[^}]*\}", "", line.split(",,0,0,0,,", 1)[1]).replace("\\N", " ")
        if not pages or pages[-1] != body:
            pages.append(body)
    return pages


def test_ass_pages_follow_phrases(tmp_path, monkeypatch):
    words = [(w, round(i * 0.3, 2), round(i * 0.3 + 0.26, 2)) for i, w in enumerate(PILOT.split())]
    # Узкая страница (по 2 слова в строке, крупный кегль) — фразу приходится делить.
    pages = _ass_pages(tmp_path, monkeypatch, words, max_words_per_line=2, font_size=120)
    assert pages[0] == "меня накажут,", pages
    assert not any(p.split()[-1] in ("так", "и", "если") for p in pages[:-1]), pages
    assert any(p.startswith("так и") for p in pages), pages


def test_ass_keeps_short_pages_on_screen(tmp_path, monkeypatch):
    # Быстрая речь: страница из 2+ слов не меньше секунды (считая задержку после слова), где можно.
    words = [(w, round(i * 0.15, 2), round(i * 0.15 + 0.12, 2)) for i, w in enumerate("раз два три четыре пять шесть".split())]
    pages = _ass_pages(tmp_path, monkeypatch, words, max_words_per_line=2, font_size=120)
    assert [len(p.split()) for p in pages] == [3, 3], pages
