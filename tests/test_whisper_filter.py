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
