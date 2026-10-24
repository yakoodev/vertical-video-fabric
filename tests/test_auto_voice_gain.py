"""set_source_audio gain_db "auto": голос над игрой по громкости В МОМЕНТЫ РЕЧИ (app/source_audio.auto_voice_gain).

Апекс (клипы 583–585): прежний «auto» дал микрофону +12…+16 дБ, а голос вышел на 2–3 дБ над игрой
или ниже — медиана игры за всё время тише игры в моменты, когда стример говорит (там стреляют).
"""

from __future__ import annotations

import math

import pytest

from app import source_audio
from app.source_audio import AUTO_OVER_DB, AUTO_PEAK_ROOM_DB, auto_voice_gain


def _power_db(values: list[float]) -> float:
    return 10 * math.log10(sum(10 ** (v / 10) for v in values) / len(values))


def _apex(n: int = 400, speech_every: int = 3):
    """Игра −24 дБ в тишине и −14 дБ, пока стример говорит; микрофон: шум −62, речь −26 дБ."""
    voice, game = [], []
    for i in range(n):
        talking = (i // 8) % speech_every == 0  # фразы по 2 с через паузу
        voice.append(-26.0 + (i % 3) if talking else -62.0 + (i % 2))
        game.append(-14.0 + (i % 2) if talking else -24.0 + (i % 3))
    return voice, game


def test_voice_over_game_measured_where_speech_is():
    voice, game = _apex()
    got = auto_voice_gain(voice, [game])
    speech = [i for i, v in enumerate(voice) if v > -50]
    over = _power_db([voice[i] + got["gain_db"] for i in speech]) - _power_db([game[i] for i in speech])
    assert 8.0 <= over <= 10.0  # голос на ~9 дБ над игрой в моменты речи
    assert got["over_db"] == pytest.approx(over, abs=0.3)
    assert got["speech_sec"] == pytest.approx(len(speech) * 0.25)
    assert got["others_db"] > -16  # игру мерили там, где речь (−14), а не медиану за всё время
    # Прежний расчёт (90-й перцентиль голоса против медианы игры + 6) дал бы на ~10 дБ меньше —
    # голос ниже игры в моменты речи.
    old = source_audio._percentile(game, 0.5) + 6.0 - source_audio._percentile(voice, 0.9)
    assert got["gain_db"] - old >= 8


def test_gain_is_capped_and_no_speech_keeps_zero():
    voice, game = _apex()
    quiet_mic = [v - 30 for v in voice]  # микрофон почти не слышно
    assert auto_voice_gain(quiet_mic, [game])["gain_db"] == source_audio.AUTO_MAX_DB
    silent = [-70.0 + (i % 2) for i in range(400)]  # в дорожке только шум
    got = auto_voice_gain(silent, [game])
    assert got["gain_db"] == 0.0 and "речи" in got["note"]
    assert auto_voice_gain(voice, [])["gain_db"] == 0.0


def test_peaks_eating_headroom_lower_the_gain():
    """Речь редкая (10% окон), игра громкая всё время: пик усиленного голоса выше громкости смеси
    больше, чем дотянет нормализация (−14 LUFS при −1.5 dBTP и ~3 дБ лимитера) — снижаем."""
    n = 400
    voice = [-30.0 if i % 10 == 0 else -70.0 for i in range(n)]
    peaks = [-18.0 if i % 10 == 0 else -60.0 for i in range(n)]
    game = [-20.0] * n
    plain = auto_voice_gain(voice, [game])
    assert plain["gain_db"] == pytest.approx(-20 + AUTO_OVER_DB + 30, abs=0.5)
    got = auto_voice_gain(voice, [game], peaks, others_peaks=[[-10.0] * n])
    assert got["peak_limited"] and got["gain_db"] < plain["gain_db"]
    assert got["peak_over_mix_db"] <= AUTO_PEAK_ROOM_DB
    assert got["over_db"] >= source_audio.AUTO_MIN_OVER_DB  # голос всё равно над игрой
    # Пики самой речи острые при любом усилении (снижение не вернёт запас) — усиление не трогаем.
    sharp = [-4.0 if i % 10 == 0 else -60.0 for i in range(n)]
    kept = auto_voice_gain(voice, [game], sharp)
    assert kept["gain_db"] == plain["gain_db"] and not kept["peak_limited"] and "компрессор" in kept["note"]
    # Пики игры (взрывы) и так выше — голос в них не виноват: усиление прежнее.
    boom = auto_voice_gain(voice, [game], peaks, others_peaks=[[0.0] * n])
    assert boom["gain_db"] == plain["gain_db"] and not boom["peak_limited"]


def test_peak_room_matches_render_loudness_chain():
    from app import render
    from app.settings import settings

    assert AUTO_PEAK_ROOM_DB == pytest.approx(render.LOUDNESS_PEAK_DB + render.LIMITER_ROOM_DB
                                              - settings.render_loudnorm_target)


def test_set_source_audio_auto_uses_profiles(monkeypatch):
    voice, game = _apex()

    class Store:
        source = {"id": 3, "duration_sec": 100.0, "metadata": {"streams": [{"codec_type": "audio"}] * 2},
                  "audio_tracks": None, "speech_tracks": None}

        def get_source(self, _id):
            return dict(self.source)

        def update_source(self, _id, **fields):
            self.source = {**self.source, **fields}
            return self.get_source(_id)

    profiles = {0: {"db": game, "peak_db": [g + 10 for g in game], "step": 0.25},
                1: {"db": voice, "peak_db": [v + 12 for v in voice], "step": 0.25}}
    out = source_audio.set_source_audio(Store(), 3, tracks=[0, {"track": 1, "gain_db": "auto"}],
                                        measure=lambda _src, track: profiles[track])
    gain = out["auto_gain"]["1"]
    assert gain == auto_voice_gain(voice, [game], profiles[1]["peak_db"], others_peaks=[profiles[0]["peak_db"]])[
        "gain_db"]
    assert 8.0 <= out["auto"]["1"]["over_db"] <= 10.0
    assert set(out["levels"]) == {"0", "1"} and "median_db" in out["levels"]["0"]
    assert out["audio"]["mix"] == [{"track": 0, "gain_db": 0.0}, {"track": 1, "gain_db": gain}]


def test_pcm_peaks_on_synthetic_tone():
    import struct

    from app.source_loudness import pcm_levels, pcm_peaks

    rate = 8000
    samples = [int(0.5 * 32767 * math.sin(2 * math.pi * 220 * i / rate)) if i < rate else 0 for i in range(2 * rate)]
    raw = struct.pack(f"<{len(samples)}h", *samples)
    peaks = pcm_peaks(raw, rate, 0.5)
    levels = pcm_levels(raw, rate, 0.5)
    assert len(peaks) == len(levels) == 4
    assert peaks[0] == pytest.approx(-6.0, abs=0.2)  # пик синуса 0.5 — −6 дБ
    assert levels[0] == pytest.approx(-9.0, abs=0.2)  # RMS синуса на 3 дБ ниже пика
    assert peaks[3] <= -89
