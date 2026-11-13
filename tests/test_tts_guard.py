"""Без молчаливой обрезки tts_preview и защита синтеза Gemini TTS (Whisper первых секунд). Сети и Whisper нет."""

from __future__ import annotations

import pytest

from app import dubbing, voice_asr
from test_gemini_tts import _fake_generate, _tts, needs_ffmpeg


def _w(*words):
    return [{"word": x, "start": i * 0.3, "end": i * 0.3 + 0.25} for i, x in enumerate(words)]


def test_tts_preview_long_text_is_refused_not_truncated():
    """Раньше 470 знаков молча резались до 300 («…платишь в рублях — и»): теперь отказ с подсказкой."""
    from test_translation_dubbing import ExplodingTTS

    with pytest.raises(dubbing.DubError, match="не режу молча.*add_voiceover"):
        dubbing.tts_preview(None, tts=ExplodingTTS(), text="Слово " * 200)
    assert dubbing.PREVIEW_MAX_CHARS >= 1000
    text = ("Привет " * 120).strip()  # ~840 знаков: раньше обрезалось бы до 300
    out = dubbing.tts_preview(None, tts=ExplodingTTS(), text=text)
    assert out["chars"] == len(text) and out["text"] == text and out["truncated"] is False


def test_tts_preview_style_is_reported_as_ignored():
    from test_translation_dubbing import ExplodingTTS

    out = dubbing.tts_preview(None, tts=ExplodingTTS(), text="Привет", style="весело")
    assert out["style_ignored"] == "весело" and "зачитывает" in out["style_note"]


@needs_ffmpeg
def test_guard_resynthesizes_when_start_is_not_the_text(monkeypatch, tmp_path):
    """Первые слова звука не из текста (зачитанное указание) — синтез ещё раз; второй чистый — его и берём."""
    calls = _fake_generate(monkeypatch, 0.4)
    heard = [_w("Говори", "бодро."), _w("Витубика")]
    monkeypatch.setattr(voice_asr, "head_transcribe", lambda path, seconds=2.5: heard.pop(0))
    tts = _tts(guard=True)
    speech = tts.synthesize("Витубика — магазин моделей", tmp_path / "g.mp3")
    assert len(calls) == 2
    assert tts.last_guard == {"ok": True, "retried": True, "heard_first": "Говори бодро."}
    assert speech.raw_usage["guard"]["ok"] is True


@needs_ffmpeg
def test_guard_keeps_matching_start_and_is_off_in_tests(monkeypatch, tmp_path):
    calls = _fake_generate(monkeypatch, 0.4)
    monkeypatch.setattr(voice_asr, "head_transcribe", lambda path, seconds=2.5: _w("витупика", "магазин"))
    tts = _tts(guard=True)
    tts.synthesize("Витубика — магазин моделей", tmp_path / "g.mp3")
    assert len(calls) == 1 and tts.last_guard == {"ok": True}
    _tts().synthesize("Что угодно", tmp_path / "h.mp3")  # TTS_GUARD=0 в тестах: Whisper не зовётся
    assert len(calls) == 2


@needs_ffmpeg
def test_guard_without_whisper_keeps_speech(monkeypatch, tmp_path):
    calls = _fake_generate(monkeypatch, 0.4)

    def broken(path, seconds=2.5):
        raise RuntimeError("faster-whisper is not installed")

    monkeypatch.setattr(voice_asr, "head_transcribe", broken)
    tts = _tts(guard=True)
    tts.synthesize("Привет", tmp_path / "g.mp3")
    assert len(calls) == 1 and tts.last_guard is None


def test_head_match_and_text_comparison_rules():
    A = voice_asr
    assert A.head_matches("Хочешь стать витубером?", _w("Хочешь", "стать"))
    assert not A.head_matches("Хочешь стать витубером?", _w("Говори", "быстро,", "живо"))
    assert A.head_matches("Промокод Free10 даёт скидку", _w("Промокод", "FREE10"))
    assert A.head_matches("Текст", [])  # ничего не распознано — не повод синтезировать заново
    assert A.lead_extra("Хочешь стать витубером?", _w("Говори", "бодро", "Хочешь", "стать")) == ["Говори", "бодро"]
    assert A.lead_extra("Хочешь стать", _w("Совсем", "другое")) == []  # текст не прозвучал — не «лишнее в начале»
    wer, missing, extra = A.word_errors("Оплата в рублях без комиссии", _w("Оплата", "в", "рублях"))
    assert wer == pytest.approx(0.4) and missing == ["без", "комиссии"] and extra == []
    assert A.missing_tail("Оплата в рублях без комиссии", _w("Оплата", "в", "рублях")) == ["без", "комиссии"]
    assert A.missing_tail("Оплата в рублях", _w("Оплата", "в", "рублях")) == []


def test_whisper_spelling_is_not_an_error():
    """E2E 13.11: «лайв-два-дэ» Whisper пишет «Live 2D», «фри десять» — «FREE10»: это не лишнее и не пропуск."""
    A = voice_asr
    assert A.expand("FREE10") == ["фри", "десять"] and A.expand("2D") == ["два", "дэ"] and A.expand("10%")[-1] == "процентов"
    text = "Лайв-два-дэ и три-дэ модели, милые"
    heard = _w("Live", "2D", "и", "3D", "-модели", "—", "милые")
    assert A.lead_extra(text, heard) == [] and A.word_errors(text, heard)[0] == 0.0
    text = "Заходи и вводи промокод фри десять."
    assert A.missing_tail(text, _w("Заходи", "и", "вводи", "промокод", "FREE10.")) == []
    assert A.heard_share("скидка десять процентов", _w("скидка", "10%")) == 1.0
    # А настоящая подмена остаётся подменой.
    assert A.lead_extra("Хочешь стать витубером", _w("Говори", "бодро", "Хочешь", "стать")) == ["Говори", "бодро"]
