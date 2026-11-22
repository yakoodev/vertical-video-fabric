"""Стенд выдумок распознавания (tools/asr_bench): записи без речи детерминированы, метрики считают как надо."""

from __future__ import annotations

import numpy as np

from tools.asr_bench import gen, metrics


def test_no_speech_set_is_14_clips_and_reproducible():
    bed = np.sin(np.arange(25 * gen.RATE) / gen.RATE * 2 * np.pi * 220).astype(np.float32)
    a, b = gen.no_speech_set(bed), gen.no_speech_set(bed)
    assert len(a) == 14
    assert all(np.array_equal(a[k], b[k]) for k in a)
    assert not np.any(a["silence_30s"]) and len(a["silence_60s"]) == 60 * gen.RATE
    assert abs(gen.rms_db(a["white_quiet"]) + 60) < 0.1 and abs(gen.rms_db(a["pink_loud"]) + 25) < 0.1
    assert abs(gen.rms_db(gen.near_silence()) - gen.TAIL_DB) < 0.1


def test_wer_normalizes_case_stress_and_tags():
    assert metrics.norm_words("Вити́бику, [laughs] Ёлка!") == ["витибику", "елка"]
    assert metrics.wer("раз два три", "раз два три") == (0, 3)
    assert metrics.wer("раз два три", "раз три четыре пять") == (3, 3)
    assert metrics.norm_words("Подписка за 200 баксов, 12 тысяч, 2021") == [
        "подписка", "за", "двести", "баксов", "двенадцать", "тысяч", "две", "тысячи", "двадцать", "один"]


def test_summary_counts_hallucinations_and_tail_words():
    items = [{"id": "s", "set": "nospeech", "ref": "", "speech_end": 0.0},
             {"id": "q", "set": "nospeech", "ref": "", "speech_end": 0.0},
             {"id": "v", "set": "tail", "ref": "привет мир", "speech_end": 2.0, "control": True}]
    res = {"nospeech/s": {"text": "Продолжение следует...", "words": []},
           "nospeech/q": {"text": "", "words": []},
           "tail/v": {"text": "привет мир Продолжение следует", "words": [
               {"word": "привет", "start": 0.1, "end": 0.5}, {"word": "мир", "start": 0.6, "end": 1.0},
               {"word": "Продолжение", "start": 5.0, "end": 5.6}, {"word": "следует", "start": 5.6, "end": 6.0}]}}
    s = metrics.summarize(items, res)
    assert s["nospeech"]["hallucinated"] == 1 and s["nospeech"]["clips"] == 2
    assert s["tail"]["clips_with_tail_text"] == 1 and s["tail"]["tail_words"] == 2
    assert s["tail"]["wer"] == 1.0 and s["tail"]["controls_lost"] == []
