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


def _over(voice, game, got) -> float:
    speech = [i for i, v in enumerate(voice) if v > -50]
    return (_power_db([voice[i] + got["gain_db"] for i in speech])
            - _power_db([game[i] + got["others_gain_db"] for i in speech]))


def test_voice_short_of_peak_room_turns_the_game_down_not_itself():
    """«Игра 0 + микрофон 1»: пик речи не даёт голосу нужных +20 дБ. Раньше голосу снижали
    усиление до перевеса +4, а игра оставалась на 0 — голос выходил на −6…+5 дБ к игре.
    Теперь голосу — максимум по пику, игре — отрицательный gain_db: перевес не ниже AUTO_OVER_DB."""
    voice, game = _apex()
    peaks = [v + 14 for v in voice]  # пик речи ≈ −12…−10 дБ: бюджет голоса ~+8.5 дБ
    plain = auto_voice_gain(voice, [game])
    assert plain["others_gain_db"] == 0.0 and plain["gain_db"] > 15  # без пиков — как раньше
    got = auto_voice_gain(voice, [game], peaks, others_peaks=[[g + 10 for g in game]])
    assert got["peak_limited"] and "остальные дорожки тише" in got["note"]
    assert got["gain_db"] + got["peak_db"] <= source_audio.AUTO_VOICE_PEAK_CEIL_DB  # пик голоса в бюджете
    assert got["gain_db"] >= source_audio.AUTO_VOICE_PEAK_CEIL_DB - got["peak_db"] - 0.5  # и бюджет весь
    assert got["others_gain_db"] < 0 and got["others_gain_db"] % 0.5 == 0
    assert got["over_db"] >= AUTO_OVER_DB
    assert AUTO_OVER_DB <= _over(voice, game, got) <= AUTO_OVER_DB + 0.6
    # Баланс тот же, что у «голос +нужное, игра 0»: относительный запас пиков смеси не хуже.
    assert got["gain_db"] - got["others_gain_db"] == pytest.approx(plain["gain_db"], abs=0.5)


def test_voice_never_below_target_even_when_game_peaks_are_hot():
    """Пики игры (взрывы) выше всего — голос в них не виноват; перевес всё равно цель, не меньше."""
    n = 400
    voice = [-30.0 if i % 10 == 0 else -70.0 for i in range(n)]
    peaks = [-18.0 if i % 10 == 0 else -60.0 for i in range(n)]
    game = [-20.0] * n
    plain = auto_voice_gain(voice, [game])
    assert plain["gain_db"] == pytest.approx(-20 + AUTO_OVER_DB + 30, abs=0.5)
    for others_peaks in ([[-10.0] * n], [[0.0] * n]):
        got = auto_voice_gain(voice, [game], peaks, others_peaks=others_peaks)
        assert got["gain_db"] == 16.5  # −1.5 − (−18): весь бюджет пика
        assert got["over_db"] >= AUTO_OVER_DB and _over(voice, game, got) >= AUTO_OVER_DB - 0.01
        assert "peak_over_mix_db" in got
    # Острые пики речи при любом балансе — пометка про компрессор, но перевес цели.
    sharp = [-4.0 if i % 10 == 0 else -60.0 for i in range(n)]
    kept = auto_voice_gain(voice, [game], sharp)
    assert kept["over_db"] >= AUTO_OVER_DB and kept["gain_db"] == 2.5 and kept["others_gain_db"] < -15
    if kept["peak_over_mix_db"] > AUTO_PEAK_ROOM_DB:
        assert "компрессор" in kept["note"]


def test_quiet_mic_capped_gain_still_reaches_target_by_lowering_the_rest():
    voice, game = _apex()
    quiet_mic = [v - 30 for v in voice]
    got = auto_voice_gain(quiet_mic, [game])
    assert got["gain_db"] == source_audio.AUTO_MAX_DB and got["others_gain_db"] < 0
    assert got["over_db"] >= AUTO_OVER_DB and "не даю" in got["note"]
    # Громкий голос (перевес и так больше цели) — остальные не трогаем, голос не ниже AUTO_MIN_DB.
    loud = auto_voice_gain([v + 40 for v in voice], [game])
    assert loud["gain_db"] == source_audio.AUTO_MIN_DB and loud["others_gain_db"] == 0.0
    assert loud["over_db"] > AUTO_OVER_DB


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
    game_gain = out["auto_gain"]["0"]  # громкость всех дорожек смеси
    assert out["audio"]["mix"] == [{"track": 0, "gain_db": game_gain}, {"track": 1, "gain_db": gain}]
    assert game_gain == out["auto"]["1"]["others_gain_db"] <= 0


def test_set_source_audio_auto_lowers_game_with_its_own_gain(monkeypatch):
    """Пик голоса не пускает его выше — игра получает отрицательный gain_db (поверх своего)."""
    voice, game = _apex()

    class Store:
        source = {"id": 4, "duration_sec": 100.0, "metadata": {"streams": [{"codec_type": "audio"}] * 2},
                  "audio_tracks": None, "speech_tracks": None}

        def get_source(self, _id):
            return dict(self.source)

        def update_source(self, _id, **fields):
            self.source = {**self.source, **fields}
            return self.get_source(_id)

    profiles = {0: {"db": game, "peak_db": [g + 10 for g in game], "step": 0.25},
                1: {"db": voice, "peak_db": [v + 14 for v in voice], "step": 0.25}}
    store = Store()
    out = source_audio.set_source_audio(store, 4, tracks=[{"track": 0, "gain_db": -2}, {"track": 1, "gain_db": "auto"}],
                                        measure=lambda _src, track: profiles[track])
    auto = out["auto"]["1"]
    assert auto["peak_limited"] and auto["over_db"] >= AUTO_OVER_DB
    # Игру мерили уже с её −2 дБ, и тише сделали поверх них.
    assert auto["others_db"] == pytest.approx(source_audio.auto_voice_gain(voice, [game])["others_db"] - 2, abs=0.1)
    assert out["auto_gain"] == {"0": -2 + auto["others_gain_db"], "1": auto["gain_db"]}
    assert store.source["audio_tracks"] == [{"track": 0, "gain_db": -2 + auto["others_gain_db"]},
                                            {"track": 1, "gain_db": auto["gain_db"]}]


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
