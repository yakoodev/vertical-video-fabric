"""Галлюцинации Whisper на тишине и начала слов по дорожке речи."""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

from app.speech_onsets import snap_words, voiced_frames
from app.subtitles.contracts import SubtitleResult, SubtitleSegment, SubtitleWord
from app.subtitles.phantoms import drop_phantoms, is_phantom_text

RATE = 16000


def _pcm(spans: list[tuple[float, float]], total: float, quiet: int = 30, loud: int = 9000) -> bytes:
    """Тишина (лёгкий шум) и «голос» — синус 220 Гц — на отрезках ``spans``."""
    out = []
    for i in range(int(total * RATE)):
        t = i / RATE
        on = any(a <= t < b for a, b in spans)
        v = loud * math.sin(2 * math.pi * 220 * t) if on else quiet * math.sin(2 * math.pi * 50 * t)
        out.append(int(v))
    return struct.pack(f"<{len(out)}h", *out)


def _write_wav(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(RATE)
        f.writeframes(pcm)


def _result(segments: list[tuple[float, float, str]]) -> SubtitleResult:
    words = []
    for a, b, text in segments:
        tokens = text.split()
        step = (b - a) / len(tokens)
        words += [SubtitleWord(word=t, start=round(a + k * step, 3), end=round(a + (k + 1) * step, 3))
                  for k, t in enumerate(tokens)]
    return SubtitleResult(text=" ".join(s[2] for s in segments), language="ru", duration=segments[-1][1],
                          segments=[SubtitleSegment(a, b, t) for a, b, t in segments], words=words)


def test_known_phantoms_by_text():
    for text in ("Субтитры сделал DimaTorzok", "Продолжение следует...", "Спасибо за просмотр!",
                 "субтитры делал Диматорзок", "Редактор субтитров А.Синецкая", "ПРОДОЛЖЕНИЕ СЛЕДУЕТ…"):
        assert is_phantom_text(text), text
    for text in ("Продолжаем, следующий раунд", "спасибо за игру", "ну и субтитры у тебя", ""):
        assert not is_phantom_text(text), text


def test_silent_and_phantom_segments_dropped_real_speech_kept():
    pcm = _pcm([(0.5, 1.5), (2.0, 2.8)], 5.0)
    result = _result([
        (0.5, 1.5, "привет как дела"),        # речь — остаётся
        (2.0, 2.8, "Продолжение следует"),     # фантом по тексту, хоть и звучит
        (3.2, 4.6, "Спасибо всем кто смотрел"),  # на тишине дорожки речи — выдумка
    ])
    clean, dropped = drop_phantoms(result, pcm)
    assert [s.text for s in clean.segments] == ["привет как дела"]
    assert [w.word for w in clean.words] == ["привет", "как", "дела"]
    assert dropped == 6 and clean.usage["droppedPhantoms"] == 6


def test_loud_mix_keeps_segments_and_no_audio_keeps_all():
    # Смесь с игрой: громко всё время — по тишине ничего не выкидываем.
    pcm = _pcm([(0.0, 5.0)], 5.0)
    result = _result([(0.5, 1.5, "привет как дела"), (3.2, 4.6, "пошли вперёд")])
    clean, dropped = drop_phantoms(result, pcm)
    assert dropped == 0 and clean is result
    # Звука нет (не прочитался) — только стоп-лист.
    clean, dropped = drop_phantoms(_result([(0.5, 1.5, "привет"), (2, 3, "Субтитры сделал DimaTorzok")]), b"")
    assert [w.word for w in clean.words] == ["привет"] and dropped == 3


def test_phantom_words_without_segments():
    words = [SubtitleWord("ну", 0.1, 0.3), SubtitleWord("Продолжение", 0.4, 0.9), SubtitleWord("следует", 0.9, 1.3),
             SubtitleWord("корректор", 1.5, 2.0), SubtitleWord("DimaTorzok", 2.1, 2.6)]
    result = SubtitleResult(text="", language="ru", duration=3, segments=[], words=words)
    clean, dropped = drop_phantoms(result, b"")
    assert [w.word for w in clean.words] == ["ну", "корректор"] and dropped == 3


def test_subtitle_gaps_ignore_phantom_and_silent_cues(monkeypatch):
    """«Речь есть, а субтитров нет» не по галлюцинациям расшифровки."""
    from app.agent_tools import subtitle_gaps

    cues: list[dict] = []

    class Store:
        def get_clip_plan_subtitles(self, _id):
            return {"words": [{"word": "ага", "start": 11.0, "end": 11.4}]}

        def get_source_transcript(self, _id):
            return cues

        def get_source(self, _id):
            return {"id": 5, "local_path": "x.mp4"}

    # Дорожка речи куска 100–110 с: голос только на 104–105 с.
    monkeypatch.setattr("app.source_loudness.read_window_pcm",
                        lambda source, start, length, rate=8000, tracks=None: _pcm([(4.0, 5.0)], length))
    pieces = [{"source_start": 100, "source_end": 110}, {"source_start": 200, "source_end": 210}]
    plan = {"id": 1, "source_id": 5}
    cues[:] = [{"start": 101.0, "end": 103.0, "text": "Субтитры сделал DimaTorzok"},
               {"start": 106.0, "end": 108.5, "text": "Спасибо всем кто досмотрел до конца"}]  # на тишине
    assert subtitle_gaps(Store(), plan, pieces) == []
    cues.append({"start": 104.0, "end": 105.0, "text": "вот это да"})  # звучит — дыра
    assert subtitle_gaps(Store(), plan, pieces) == [0]


def test_generate_for_plan_drops_phantoms(tmp_path, monkeypatch):
    from test_cuts import _two_pieces

    from app.clip_subtitles import generate_for_plan

    store, _service, plan, _preset = _two_pieces(tmp_path, monkeypatch)
    pcm = _pcm([(0.5, 1.5)], 4.0)

    def fake_ffmpeg(args, timeout=0):
        _write_wav(Path(args[-1]), pcm)

    monkeypatch.setattr("app.render._run_ffmpeg", fake_ffmpeg)

    class Whisper:
        def transcribe(self, *_a):
            return _result([(0.5, 1.5, "привет как дела"), (2.2, 3.6, "Субтитры делал кто-то там")])

    monkeypatch.setattr("app.clip_subtitles.get_subtitle_provider", lambda name: Whisper())
    monkeypatch.setattr("app.clip_subtitles._default_profile", lambda store: {"provider": "whisper"})
    payload = generate_for_plan(store, plan["id"], provider="whisper")
    assert [w["word"] for w in payload["words"]] == ["привет", "как", "дела"]
    assert " ".join(l["text"] for l in payload["lines"]) == "привет как дела"
    assert payload["dropped_phantoms"] == 4  # «Субтитры делал кто-то там» — 4 слова


# --------------------------------------------------------------- начала слов по дорожке речи


def test_connected_speech_words_get_the_measured_lead():
    """Whisper спешит на 0.3 с: у слов после пауз это видно по звуку — та же поправка уходит
    словам внутри сплошной речи (раньше агент сдвигал все субтитры на +0.25 с вручную)."""
    speech = [(1.0, 2.0), (2.6, 3.6), (4.2, 5.2), (5.8, 7.4)]
    voiced = voiced_frames(_pcm(speech, 8.0))
    raw = [{"word": "раз", "start": 0.7, "end": 1.6},
           {"word": "два", "start": 2.3, "end": 3.2},
           {"word": "три", "start": 3.9, "end": 4.8},
           {"word": "четыре", "start": 5.5, "end": 6.3},
           {"word": "пять", "start": 6.3, "end": 7.4}]  # внутри сплошной речи 5.8–7.4 (на деле с 6.6)
    words, _moved = snap_words(raw, voiced)
    assert [round(w["start"], 2) for w in words[1:4]] == [2.6, 4.2, 5.8]
    assert abs(words[4]["start"] - 6.6) <= 0.021  # медианная поправка +0.3


def test_no_lead_without_enough_evidence():
    voiced = voiced_frames(_pcm([(1.0, 3.0)], 4.0))
    raw = [{"word": "раз", "start": 0.7, "end": 1.6}, {"word": "два", "start": 1.6, "end": 3.0}]
    words, _ = snap_words(raw, voiced)
    assert words[1]["start"] == 1.6  # одно слово после паузы — мало, сплошную речь не трогаем


def test_onset_track_is_the_microphone_when_recognition_used_the_mix(tmp_path, monkeypatch):
    """speech_tracks не заданы, смесь — игра + микрофон: онсеты по дорожке, похожей на голос."""
    from app.clip_subtitles import onset_pcm

    game = _pcm([(0.0, 4.0)], 4.0, loud=6000)  # гудит ровно
    mic = _pcm([(0.5, 1.2), (2.0, 3.1)], 4.0, quiet=3)  # паузы и речь
    tracks = {0: game, 1: mic}

    def fake_ffmpeg(args, timeout=0):
        graph = args[args.index("-filter_complex") + 1]
        _write_wav(Path(args[-1]), tracks[1] if "0:a:1" in graph else tracks[0])

    monkeypatch.setattr("app.render._run_ffmpeg", fake_ffmpeg)
    monkeypatch.setattr("app.source_audio._tracks_in_file", lambda source: 2)
    source = {"audio_tracks": [0, {"track": 1, "gain_db": 9}], "local_path": "x.mp4"}
    mixed = b"mix"
    assert onset_pcm(Path("x.mp4"), [(0.0, 4.0)], source, mixed) == mic
    # speech_tracks заданы — распознанный звук и есть дорожка речи.
    assert onset_pcm(Path("x.mp4"), [(0.0, 4.0)], {**source, "speech_tracks": [1]}, mixed) == mixed
    # Одна дорожка — как было.
    assert onset_pcm(Path("x.mp4"), [(0.0, 4.0)], {"local_path": "x.mp4"}, mixed) == mixed
