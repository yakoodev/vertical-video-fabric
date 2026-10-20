"""Концовки не обрываются: хвост после последней фразы + затухание звука."""

from app.cut_refine import refine_boundaries
from app.render import audible, end_fade_filter

CUES = [
    {"start": 10.0, "end": 14.0, "text": "раз"},
    {"start": 15.0, "end": 19.0, "text": "два"},
    {"start": 19.3, "end": 22.0, "text": "три"},
]


def test_end_gets_tail_after_last_phrase():
    _s, e = refine_boundaries(10.0, 18.0, CUES, "phrase")
    # договорили фразу до 19.0, но следующая начинается в 19.3 — хвост не залезает в неё
    assert 19.0 <= e <= 19.2


def test_end_tail_in_open_silence():
    _s, e = refine_boundaries(15.0, 21.0, CUES, "phrase")
    assert e == 22.6  # конец фразы 22.0 + хвост 0.6


def test_off_strategy_keeps_raw_end():
    assert refine_boundaries(10.0, 18.0, CUES, "off") == (10.0, 18.0)


def test_end_fade_filter():
    assert end_fade_filter(30.0) == "afade=t=out:st=29.700:d=0.300"
    assert end_fade_filter(0.5) == ""


def test_audible():
    assert audible({"input_i": "-18.0"})
    assert not audible({"input_i": "-inf"})
    assert not audible({})


class _Store:
    def get_source_transcript(self, _sid):
        return CUES


def test_ending_warning_mid_phrase_and_flush():
    from app.agent_tools import ending_warning

    assert "полуслове" in ending_warning(_Store(), 1, 17.0)
    assert "хвост" in ending_warning(_Store(), 1, 22.05)
    assert ending_warning(_Store(), 1, 22.6) is None
