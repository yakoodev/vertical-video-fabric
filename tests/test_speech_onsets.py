import math
import struct

from app.speech_onsets import snap_payload, snap_words, voiced_frames

RATE = 16000


def _pcm(spans: list[tuple[float, float]], total: float) -> bytes:
    """Тишина (лёгкий шум) и «голос» — синус 220 Гц — на отрезках ``spans``."""
    out = []
    for i in range(int(total * RATE)):
        t = i / RATE
        loud = any(a <= t < b for a, b in spans)
        v = 9000 * math.sin(2 * math.pi * 220 * t) if loud else 30 * math.sin(2 * math.pi * 50 * t)
        out.append(int(v))
    return struct.pack(f"<{len(out)}h", *out)


def test_first_word_at_zero_moves_to_real_onset():
    voiced = voiced_frames(_pcm([(1.78, 2.3), (2.5, 3.0)], 3.5))
    words, moved = snap_words([{"word": "Все", "start": 0.0, "end": 2.3}, {"word": "думают", "start": 2.5, "end": 3.0}], voiced)
    assert moved == 1
    assert abs(words[0]["start"] - 1.78) <= 0.021
    assert words[1] == {"word": "думают", "start": 2.5, "end": 3.0}


def test_late_word_pulled_back_to_phrase_start_after_silence():
    voiced = voiced_frames(_pcm([(0.5, 1.0), (1.6, 2.4)], 3.0))
    words, _ = snap_words([{"word": "Если", "start": 0.5, "end": 1.0}, {"word": "делать", "start": 2.0, "end": 2.4}], voiced)
    assert abs(words[1]["start"] - 1.6) <= 0.021


def test_connected_speech_is_left_alone():
    # Слова без паузы между ними: начало фразы — не начало второго слова.
    voiced = voiced_frames(_pcm([(0.5, 2.0)], 2.5))
    words, moved = snap_words([{"word": "один", "start": 0.5, "end": 1.2}, {"word": "два", "start": 1.2, "end": 2.0}], voiced)
    assert moved == 0


def test_word_end_in_silence_comes_back_and_lines_follow():
    payload = {
        "words": [{"word": "по", "start": 0.3, "end": 0.6}, {"word": "душе", "start": 0.6, "end": 1.6}],
        "lines": [{"start": 0.3, "end": 1.6, "text": "по душе"}],
    }
    moved = snap_payload(payload, _pcm([(0.3, 1.1)], 2.0))
    assert moved == 1
    assert abs(payload["words"][1]["end"] - 1.1) <= 0.021
    assert payload["lines"][0]["end"] == payload["words"][1]["end"]
