"""Фильтр «не речь»: дубляж под музыкой не выкидывается, песня-галлюцинация — да."""

from types import SimpleNamespace

from app.subtitles.whisper_local import _is_hallucination


def _seg(no_speech, logprob, text="Кому пишешь?"):
    return SimpleNamespace(no_speech_prob=no_speech, avg_logprob=logprob, text=text)


def test_dub_over_music_is_speech():
    # Реальная серия: «Кому пишешь?» — no_speech 0.69, уверенность −0.23.
    assert not _is_hallucination(_seg(0.69, -0.23))
    # Сцена в кафе (593–650 с) целиком пропадала: no_speech 0.93, уверенность −0.16.
    assert not _is_hallucination(_seg(0.93, -0.16))


def test_music_and_garbage_dropped():
    assert _is_hallucination(_seg(0.7, -0.8))  # «ла-ла-ла» под музыку: и тишина, и неуверенно
    assert _is_hallucination(_seg(0.95, -0.6))  # почти наверняка не речь и неуверенно
    assert not _is_hallucination(_seg(0.92, -0.33))  # сцена 307–439 с: речь под музыкой
    assert _is_hallucination(_seg(0.1, -1.3))  # мусор декодера
    assert _is_hallucination(_seg(0.2, -0.1, "Субтитры сделал DimaTorzok"))  # известный фантом
    assert not _is_hallucination(_seg(0.1, -0.3))


# --------------------------------------------------------------- спасение слов (пение, TTS)

from app.subtitles.whisper_local import WhisperSubtitleProvider, _salvage_words  # noqa: E402


def _w(word, start, prob):
    return SimpleNamespace(word=word, start=start, end=start + 0.3, probability=prob)


def _sung(no_speech=0.7, logprob=-0.75, words=None, text="звёзды падают в ночи"):
    words = words if words is not None else [
        _w("звёзды", 1.0, 0.92), _w("падают", 1.4, 0.88), _w("в", 1.8, 0.85), _w("ночи", 2.0, 0.9)]
    return SimpleNamespace(no_speech_prob=no_speech, avg_logprob=logprob, text=text, words=words,
                           start=1.0, end=2.3)


def test_confident_words_of_a_rejected_segment_are_salvaged():
    """Пение и TTS: сегмент «не речь» по средней уверенности, но слова модель знает твёрдо."""
    seg = _sung()
    assert _is_hallucination(seg)  # старый фильтр по-прежнему его отбрасывает
    assert [w.word for w in _salvage_words(seg)] == ["звёзды", "падают", "в", "ночи"]


def test_salvage_does_not_bring_back_hallucinations():
    # «ла-ла-ла» под музыку — уверенно, но повтор одного слога.
    assert _salvage_words(_sung(words=[_w("ла", t, 0.95) for t in (1.0, 1.3, 1.6, 1.9)], text="ла ла ла ла")) == []
    # Известный фантом из обучающих титров.
    assert _salvage_words(_sung(text="Субтитры сделал DimaTorzok", words=[
        _w("Субтитры", 1.0, 0.99), _w("сделал", 1.4, 0.99), _w("DimaTorzok", 1.8, 0.99)])) == []
    # Почти наверняка тишина.
    assert _salvage_words(_sung(no_speech=0.95)) == []
    # Мусор декодера: слова неуверенные.
    assert _salvage_words(_sung(words=[_w("бла", 1.0, 0.3), _w("кхм", 1.3, 0.4), _w("ээ", 1.6, 0.2)])) == []
    # Уверенных слов мало (меньше трёх) — не спасаем.
    assert _salvage_words(_sung(words=[_w("да", 1.0, 0.9), _w("ну", 1.3, 0.9)])) == []


def test_transcribe_keeps_salvaged_words_and_drops_garbage():
    speech = SimpleNamespace(no_speech_prob=0.1, avg_logprob=-0.2, text="Привет всем", start=0.0, end=0.9,
                             words=[_w("Привет", 0.0, 0.9), _w("всем", 0.4, 0.9)])
    garbage = _sung(words=[_w("ла", t, 0.95) for t in (3.0, 3.3, 3.6, 3.9)], text="ла ла ла ла")

    class Model:
        def transcribe(self, *_a, **_k):
            info = SimpleNamespace(language="ru", language_probability=0.97)
            return iter([speech, _sung(), garbage]), info

    result = WhisperSubtitleProvider(model_loader=lambda size: Model()).transcribe("x.wav", {}, "tiny")
    assert [w.word for w in result.words] == ["Привет", "всем", "звёзды", "падают", "в", "ночи"]
    assert [s.text for s in result.segments] == ["Привет всем", "звёзды падают в ночи"]
    assert result.usage["salvagedSegments"] == 1 and result.usage["droppedNonSpeech"] == 1
