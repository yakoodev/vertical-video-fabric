"""Улики звука против выдумок Whisper (app/subtitles/speech_evidence.py, стенд tools/asr_bench).

Главное: стоп-лист фантомов («Спасибо за просмотр», «Продолжение следует») режет текст только там, где звук его не
подтверждает, — живая фраза с теми же словами остаётся. Проверяется на настоящей записи (Windows-синтез, фикстура
speech_thanks_ru.ogg: «Ну вот и всё на сегодня. Спасибо за просмотр, подписывайтесь на канал.»)."""

from __future__ import annotations

import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from app.subtitles import speech_evidence as SE
from app.subtitles.contracts import SubtitleResult, SubtitleSegment, SubtitleWord
from app.subtitles.phantoms import drop_phantoms, is_phantom_text
from app.subtitles.whisper_local import WhisperSubtitleProvider, _is_hallucination

FIXTURE = Path(__file__).parent / "fixtures" / "speech_thanks_ru.ogg"
RATE = 16000


def _stub(spans, silent=False):
    return SimpleNamespace(share=lambda a, b: SE.vad_share(a, b, spans), silent=lambda a, b: silent)


def _seg(text, no_speech=0.1, logprob=-0.3, start=1.0, end=3.0):
    return SimpleNamespace(text=text, no_speech_prob=no_speech, avg_logprob=logprob, start=start, end=end, words=[])


def _real_audio():
    audio = SE.load_audio(FIXTURE)
    if audio is None or SE.speech_spans(audio) is None:
        pytest.skip("нет декодера или Silero VAD")
    return audio


def _write_wav(path: Path, x: np.ndarray) -> None:
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(RATE)
        f.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())


def _room_tone(sec: float, db: float = -60.0) -> np.ndarray:
    x = np.random.default_rng(1).standard_normal(int(sec * RATE)).astype(np.float32)
    return x * (10 ** (db / 20) / float(np.sqrt(np.mean(x * x))))


# --------------------------------------------------------------------------- чистые функции


def test_vad_share_and_tail_cut():
    spans = [(1.0, 2.0), (3.0, 4.0)]
    assert SE.vad_share(1.0, 2.0, spans) == 1.0
    assert SE.vad_share(1.5, 3.5, spans) == pytest.approx(0.5)
    assert SE.vad_share(5.0, 6.0, spans) == 0.0
    assert SE.vad_share(0, 1, None) is None
    loud, quiet = -20.0, -70.0
    # 2 с звука, 10 с почти тишины — резать через 1 с после звука.
    assert SE.speech_end_cut([loud] * 100 + [quiet] * 500, -45.0) == pytest.approx(3.0)
    assert SE.speech_end_cut([loud] * 100 + [quiet] * 100, -45.0) is None  # хвост 2 с — не трогаем
    assert SE.speech_end_cut([quiet] * 600, -45.0) is None  # звука нет совсем — не судим


def test_new_phantom_patterns_need_whole_phrase():
    for text in ("ПОЗИТИВНАЯ МУЗЫКА", "спокойная музыка", "Thank you.", "Thanks for watching!", "Субтитры добавил Вася"):
        assert is_phantom_text(text), text
    for text in ("музыка", "Спасибо", "мне нравится эта музыка у тебя в стриме", "thank you for the help man"):
        assert not is_phantom_text(text), text


def test_phantom_text_dropped_only_without_acoustic_evidence():
    phantom = "Спасибо за просмотр, подписывайтесь на канал."
    assert _is_hallucination(_seg(phantom))  # улик нет — как раньше, по стоп-листу
    assert not _is_hallucination(_seg(phantom), _stub([(0.9, 3.1)]))  # детектор слышит голос — живая фраза
    assert _is_hallucination(_seg(phantom), _stub([]))  # голоса нет — титры из обучения
    assert _is_hallucination(_seg(phantom), _stub([(0.9, 3.1)], silent=True))  # тишина по громкости


def test_trust_no_speech_needs_the_voice_detector_to_agree():
    # Дубляж под музыкой: no_speech 0.93, текст уверенный, детектор слышит голос — реплика остаётся.
    assert not _is_hallucination(_seg("Кому пишешь?", 0.93, -0.16), _stub([(0.9, 3.1)]))
    # То же, но детектор голоса молчит — «поверить датчику»: выдумка, хоть и уверенная.
    assert _is_hallucination(_seg("Но в то же время в этом нет ничего плохого", 0.71, -0.27), _stub([]))
    # Без улик — старое правило: уверенный текст при no_speech 0.93 остаётся.
    assert not _is_hallucination(_seg("Кому пишешь?", 0.93, -0.16))


# --------------------------------------------------------------------------- настоящая запись


def test_real_spoken_thanks_survives_and_tail_phantom_does_not():
    audio = _real_audio()
    full = np.concatenate([audio, _room_tone(25.0)])
    pcm = (np.clip(full, -1, 1) * 32767).astype("<i2").tobytes()
    tail_at = len(audio) / RATE + 5.0
    result = SubtitleResult(
        text="", language="ru", duration=tail_at + 2,
        segments=[SubtitleSegment(0.1, 1.8, "Ну вот и всё на сегодня."),
                  SubtitleSegment(2.6, 6.3, "Спасибо за просмотр, подписывайтесь на канал."),
                  SubtitleSegment(tail_at, tail_at + 2, "Продолжение следует...")],
        words=[])
    clean, dropped = drop_phantoms(result, pcm)
    assert [s.text for s in clean.segments] == ["Ну вот и всё на сегодня.", "Спасибо за просмотр, подписывайтесь на канал."]
    assert dropped == 1


def test_provider_keeps_spoken_thanks_trims_tail_and_drops_tail_phantom(tmp_path):
    audio = _real_audio()
    wav = tmp_path / "voice.wav"
    _write_wav(wav, np.concatenate([audio, _room_tone(30.0)]))
    speech_end = len(audio) / RATE
    seen = {}

    def w(word, a, b):
        return SimpleNamespace(word=word, start=a, end=b, probability=0.95)

    real = SimpleNamespace(text="Спасибо за просмотр, подписывайтесь на канал.", start=2.6, end=6.3,
                           no_speech_prob=0.02, avg_logprob=-0.4,
                           words=[w("Спасибо", 2.6, 3.1), w("за", 3.1, 3.3), w("просмотр,", 3.3, 4.2),
                                  w("подписывайтесь", 4.6, 5.6), w("на", 5.6, 5.8), w("канал.", 5.8, 6.3)])
    ghost = SimpleNamespace(text="Продолжение следует...", start=speech_end + 2, end=speech_end + 4,
                            no_speech_prob=0.7, avg_logprob=-0.27,
                            words=[w("Продолжение", speech_end + 2, speech_end + 3), w("следует...", speech_end + 3, speech_end + 4)])

    class Model:
        def transcribe(self, source, **kw):
            seen["len"] = len(source)
            return iter([real, ghost]), SimpleNamespace(language="ru", language_probability=0.99)

    WhisperSubtitleProvider._models.clear()  # модели кэшируются на класс — чужая подмена не должна протечь
    try:
        result = WhisperSubtitleProvider(model_loader=lambda size: Model())._transcribe(
            wav, {"language": "ru", "vad_filter": False}, "tiny")
    finally:
        WhisperSubtitleProvider._models.clear()
    assert [x.word for x in result.words] == ["Спасибо", "за", "просмотр,", "подписывайтесь", "на", "канал."]
    assert result.usage["droppedNonSpeech"] == 1
    # Хвост 30 с почти тишины Whisper не получил: отрезано через ~1 с после конца речи.
    assert seen["len"] / RATE < speech_end + 2.0
    assert 6.5 < result.usage["trimmedTailAt"] < speech_end + 1.5  # последний слог ~6.3 с + 1 с запаса


# --------------------------------------------------------------------------- песни (VAD нельзя)


def test_lyrics_guard_uses_vocal_energy_not_vad(tmp_path):
    from app.lyrics_audio import guard_words

    t = np.arange(int(6 * RATE)) / RATE
    stem = np.where(t < 3.0, 0.3 * np.sin(2 * np.pi * 330 * t), 0.0).astype(np.float32)  # поют 0–3 с, дальше проигрыш
    path = tmp_path / "vocals.wav"
    _write_wav(path, stem)
    words = [{"w": "Продолжение", "s": 0.5, "e": 1.2, "p": 0.9}, {"w": "следует", "s": 1.2, "e": 2.0, "p": 0.9},
             {"w": "Субтитры", "s": 4.0, "e": 4.5, "p": 0.9}, {"w": "сделал", "s": 4.5, "e": 5.0, "p": 0.9},
             {"w": "DimaTorzok", "s": 5.0, "e": 5.5, "p": 0.9}]
    # Спетое «продолжение следует» остаётся, титры на проигрыше — прочь.
    assert [w["w"] for w in guard_words(words, path, stem=True)] == ["Продолжение", "следует"]
    # Без стема (полоса из смеси) громкость не улика — стоп-лист как есть.
    assert guard_words(words, path, stem=False) == []
