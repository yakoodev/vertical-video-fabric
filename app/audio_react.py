"""Реакция слоёв на звук (``react`` любого слоя, docs/motion/spec.md «Реакция на звук»).

Слой «дышит» громкостью клипа: масштаб, свечение, прозрачность или подскок. Огибающую считает
сервер один раз на клип — тем же путём, что рот PNGtuber (app/pngtuber.py): звук читается
моно 8 кГц кусками по кускам клипа, громкость кадра — RMS окна 1/fps в дБ, время слоя (шкала
``clip`` — редактор и кадр агента, ``final`` — рендер после вырезов) переводится в секунды
источника через ``app.timebase.ClipClock``. Движок получает ``comp.levels`` массивом по кадрам
и только читает его (сглаживание ``smooth`` — окном по массиву, без состояния): превью и рендер
совпадают кадр в кадр.

Источники:

* ``voice`` — дорожки распознавания речи источника (``speech_tracks``; не заданы — смесь рендера);
* ``music`` — фоновая музыка клипа (трек, ``music_start_sec``, по кругу) во времени готового ролика;
* ``mix`` — весь звук: смесь дорожек рендера источника плюс музыка с громкостью трека.

Громкость → 0..100: от шума (15-й перцентиль слышимых кадров) + 3 дБ, но не ближе 12 дБ к громкому
(97-й перцентиль); спад плавный (~0.12 с) — слой не дёргается между слогами и долями.

Полоса частот (``react.band``, одобрено владельцем 17.11): ``bass`` 20–150 Гц — бочка и бас (логотип качает в долю,
а не от вокала), ``mid`` 150–2000 Гц — голос и гитары, ``high`` 2–4 кГц — хэты (верх ограничен тем же анализом
8 кГц). Звук куска чтения фильтруется в спектре (rfft, мягкие края полосы), дальше — та же громкость кадров и та
же огибающая (своя нормировка у каждой полосы). Ключ в ``comp.levels`` — ``источник:полоса`` (``music:bass``);
без полосы — прежний ключ источника.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Callable

from app.motion import parse_level_key, react_level_key, react_sources
from app.pngtuber import (
    PCM_RATE,
    SILENCE_DB,
    _cached,
    _wav_pcm,
    frame_sources,
    levels_for_points,
    rms_db,
    source_reader,
    spans_of,
)

# Огибающая: спад за ~0.12 с, шум + 3 дБ — ноль, 97-й перцентиль — 100, ширина не меньше 12 дБ.
LEVEL_RELEASE_SEC = 0.12
FLOOR_PERCENTILE = 0.15
TOP_PERCENTILE = 0.97
FLOOR_OVER_DB = 3.0
MIN_SPAN_DB = 12.0
# Длиннее — дальше тишина (минуты клипа; часы — явная ошибка).
MAX_LEVELS_SEC = 30 * 60.0
# Полосы частот реакции, Гц (верх high — половина частоты анализа PCM_RATE).
BANDS_HZ: dict[str, tuple[float, float]] = {"bass": (20.0, 150.0), "mid": (150.0, 2000.0), "high": (2000.0, 4000.0)}
BAND_EDGE = 0.25        # мягкий край полосы — четверть октавы косинусом (без звона на резких краях)


def band_filter(samples: Any, rate: int, band: str) -> Any:
    """Сэмплы (−1..1, float) → только полоса ``band`` (bass/mid/high): маска в спектре с мягкими краями."""
    import numpy as np

    lo, hi = BANDS_HZ[band]
    x = np.asarray(samples, dtype=np.float64)
    if x.size < 8:
        return x
    spec = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(x.size, 1.0 / float(rate))
    with np.errstate(divide="ignore"):
        octave = np.log2(np.maximum(freqs, 1e-6))
    gain = np.ones_like(freqs)
    for edge, side in ((lo, -1.0), (hi, 1.0)):
        if edge >= rate / 2 - 1:
            continue  # верх high — до самого конца спектра
        # side −1: ниже lo — гасим; +1: выше hi — гасим. Переход в ±BAND_EDGE/2 октавы вокруг края.
        u = np.clip((octave - math.log2(edge)) * side / BAND_EDGE + 0.5, 0.0, 1.0)
        gain *= 0.5 + 0.5 * np.cos(np.pi * u)
    return np.fft.irfft(spec * gain, n=x.size)


def band_levels_for_points(points: list[tuple[Any, float] | None], fps: float, read: Callable,
                           band: str) -> list[float | None]:
    """Громкость полосы частот по кадрам: как ``levels_for_points``, только звук куска сначала фильтруется."""
    import numpy as np

    if band not in BANDS_HZ:
        return levels_for_points(points, fps, read)
    levels: list[float | None] = [None] * len(points)
    half = max(1, int(round(PCM_RATE / max(1.0, fps) / 2)))
    for sid, a, b, idx in spans_of(points, fps):
        pcm = read(sid, a, b - a)
        raw = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2").astype(np.float64) / 32768.0
        x = band_filter(raw, PCM_RATE, band)
        for i in idx:
            center = int(round((points[i][1] - a) * PCM_RATE))  # type: ignore[index]
            lo, hi = max(0, center - half), min(len(x), center + half)
            levels[i] = round(rms_db(x[lo:hi]), 2) if hi > lo else SILENCE_DB
    return levels


def envelope(levels: list[float | None], fps: float) -> list[int]:
    """Громкость кадров (дБ; None — звука нет) → огибающая 0..100 с плавным спадом."""
    heard = sorted(v for v in levels if v is not None and v > SILENCE_DB + 1)
    if len(heard) < 4:
        return [0] * len(levels)
    top = heard[min(len(heard) - 1, int(len(heard) * TOP_PERCENTILE))]
    # Ноль — шум + 3 дБ, но не ближе 12 дБ к громкому: микрофон с шумодавом (между фразами
    # цифровой ноль) или ровный тон без пауз — иначе вся речь оказалась бы «шумом».
    floor = min(heard[int(len(heard) * FLOOR_PERCENTILE)] + FLOOR_OVER_DB, top - MIN_SPAN_DB)
    span = top - floor
    decay = math.exp(-1.0 / (max(1.0, fps) * LEVEL_RELEASE_SEC))
    out: list[int] = []
    prev = 0.0
    for db in levels:
        raw = min(1.0, max(0.0, (db - floor) / span)) if db is not None else 0.0
        prev = max(raw, prev * decay)
        out.append(int(round(prev * 100)))
    return out


def power_sum(a: float | None, b: float | None) -> float | None:
    """Сумма двух громкостей в дБ (по мощности); None — нет звука."""
    if a is None and b is None:
        return None
    total = sum(10 ** (v / 10) for v in (a, b) if v is not None and v > SILENCE_DB + 1)
    return max(SILENCE_DB, 10 * math.log10(total)) if total > 0 else SILENCE_DB


def music_config(plan: dict, music: dict | None = None) -> dict | None:
    """Музыка клипа: {track_id, start_sec} из настроек (или переданная рендером); None — без музыки."""
    from app.clip_settings import normalize_render_settings

    if music is None:
        rs = normalize_render_settings(plan.get("render_settings") or {})
        if not rs.get("music_on") or not rs.get("track_id"):
            return None
        music = {"track_id": rs["track_id"], "start_sec": rs.get("music_start_sec") or 0.0}
    if not music.get("track_id") or int(music["track_id"]) <= 0:
        return None
    return {"track_id": int(music["track_id"]), "start_sec": max(0.0, float(music.get("start_sec") or 0.0))}


def music_positions(final_times: list[float | None], start: float, duration: float) -> list[tuple[Any, float] | None]:
    """Секунда трека в моменты готового ролика: с ``start`` и по кругу с начала трека (как рендер)."""
    out: list[tuple[Any, float] | None] = []
    for t in final_times:
        if t is None:
            out.append(None)
            continue
        pos = start + max(0.0, t)
        if duration > 0.5 and pos >= duration:
            pos = math.fmod(pos - duration, duration)
        out.append((None, pos))
    return out


def _music_reader(store: Any, track_id: int, notes: list[str]) -> tuple[Callable | None, float, float]:
    """Чтение трека моно 8 кГц, его длина и громкость (для смеси ``mix``)."""
    from uuid import uuid4

    from app.settings import settings
    from app.source_audio import extract_args

    try:
        track = store.get_audio_track(int(track_id))
    except (KeyError, TypeError, ValueError):
        notes.append(f"трека музыки #{track_id} нет — реакция на музыку молчит")
        return None, 0.0, 0.0
    path = Path(str(track.get("file_path") or ""))
    if not path.is_file():
        notes.append(f"файла трека #{track_id} нет на диске — реакция на музыку молчит")
        return None, 0.0, 0.0

    def read(_sid: Any, start: float, length: float) -> bytes:
        key = ("music", str(path), path.stat().st_mtime, round(start, 3), round(length, 3))
        out = settings.tmp_dir / f"react-{uuid4().hex}.wav"
        return _cached(key, lambda: _wav_pcm(out, extract_args(path, out, None, start=start, duration=length,
                                                                rate=PCM_RATE)))

    volume = float(track.get("volume") if track.get("volume") is not None else 0.25)
    return read, float(track.get("duration_sec") or 0.0), volume


def plan_levels(
    store: Any,
    plan: dict,
    sources: list[str],
    fps: float,
    scale: str = "clip",
    music: dict | None = None,
    layers: list[dict] | None = None,
) -> tuple[dict[str, dict], list[str]]:
    """``comp.levels`` клипа: {источник: {t0: 0, fps, v [0..100]}} по кадрам всего клипа и пометки.

    ``scale`` — шкала времени слоёв (``clip`` — до вырезов, ``final`` — готовый ролик).
    ``music`` — {track_id, start_sec} рендера; нет — из настроек клипа. ``layers`` — слои клипа в этой
    шкале: волнам со своим звуком добавляются ``wave:<id>`` (app/waveform_levels.py)."""
    from app.timebase import ClipClock
    from app.waveform_levels import wave_tracks

    fps = max(1.0, float(fps))
    waves, wave_notes = wave_tracks(store, layers, fps) if layers else ({}, [])
    keys = [(k, parse_level_key(k)) for k in dict.fromkeys(sources or [])]
    keys = [(k, p) for k, p in keys if p]
    wanted = sorted({src for _k, (src, _b) in keys})
    bands = sorted({band for _k, (_s, band) in keys})
    if not wanted:
        return waves, wave_notes
    segments = plan.get("segments") or []
    clock = ClipClock.for_plan(plan, fps)
    total = min(MAX_LEVELS_SEC, clock.final_duration if scale == "final" else clock.clip_duration)
    count = max(0, int(math.ceil(total * fps - 1e-6)))
    times = [k / fps for k in range(count)]
    notes: list[str] = []
    points = frame_sources(clock, segments, times, scale, plan.get("source_id"))

    readers: dict[str, Callable] = {}

    def source_levels(track: str, band: str) -> list[float | None]:
        if track not in readers:
            readers[track] = source_reader(store, track, notes, silent="реакция на звук молчит")
        try:
            return band_levels_for_points(points, fps, readers[track], band)
        except Exception as exc:  # noqa: BLE001 - слой без реакции лучше, чем упавший рендер
            notes.append(f"звук источника не разобран ({str(exc)[:160]}) — реакция на звук молчит")
            return [None] * count

    music_db: dict[str, list[float | None]] = {}
    music_volume = 1.0
    cfg = music_config(plan, music)
    if cfg and ("music" in wanted or "mix" in wanted):
        read, duration, music_volume = _music_reader(store, cfg["track_id"], notes)
        if read is not None:
            final = [clock.clip_to_final(t) if scale == "clip" else t for t in times]
            positions = music_positions(final, cfg["start_sec"], duration)
            try:
                for band in bands:
                    music_db[band] = band_levels_for_points(positions, fps, read, band)
            except Exception as exc:  # noqa: BLE001
                music_db = {}
                notes.append(f"музыка не разобрана ({str(exc)[:160]}) — реакция на музыку молчит")
    elif "music" in wanted:
        notes.append("у клипа нет фоновой музыки — слои с react source music стоят")

    out: dict[str, dict] = {}
    for key, (source, band) in keys:
        if source == "voice":
            db = source_levels("speech", band)
        elif source == "music":
            db = music_db.get(band) or [None] * count
        else:
            db = source_levels("mix", band)
            if band in music_db:
                # Музыка в смеси — с громкостью трека (рендер кладёт её тише голоса).
                gain = 20 * math.log10(max(1e-3, music_volume))
                db = [power_sum(a, None if b is None else b + gain) for a, b in zip(db, music_db[band])]
        out[react_level_key(source, band)] = {"t0": 0.0, "fps": fps, "v": envelope(db, fps)}
    out.update(waves)
    return out, notes + wave_notes


def attach_levels(store: Any, plan: dict, motion: dict | None, fps: float, scale: str = "clip",
                  music: dict | None = None) -> dict | None:
    """Огибающие громкости — в моушн (``levels``), если у видимых слоёв есть ``react`` или волна ``waveform``."""
    from app.waveform_levels import wave_layers

    layers = (motion or {}).get("layers")
    sources = react_sources(layers)
    if not motion or not (sources or wave_layers(layers)):
        return motion
    levels, notes = plan_levels(store, plan, sources, fps, scale, music, layers=layers)
    out = {**motion, "levels": levels}
    if notes:
        out["host_notes"] = [*(motion.get("host_notes") or []), *notes]
    return out


def shift_levels(levels: dict | None, freezes: list[dict], only: Callable[[dict], bool] | None = None) -> dict | None:
    """Вставленные стоп-кадры (``app/freeze.py``) — тишина в огибающих на их длину, чтобы реакция
    шла со звуком (как ``shift_voice`` у PNGtuber)."""
    from app.freeze import is_insert, normalize_freezes

    inserts = [f for f in normalize_freezes(freezes) if is_insert(f) and (only is None or only(f))]
    if not levels or not inserts:
        return levels
    out = {}
    for key, track in levels.items():
        fps = float(track.get("fps") or 30)
        t0 = float(track.get("t0") or 0)
        v = list(track.get("v") or [])
        for f in sorted(inserts, key=lambda f: -f["at"]):
            k = int(round((f["at"] - t0) * fps))
            if 0 <= k <= len(v):
                v = v[:k] + [0] * int(round(f["duration"] * fps)) + v[k:]
        out[key] = {**track, "v": v}
    return out


def level_at(track: dict | None, t: float) -> int:
    """Значение огибающей в момент ``t`` — как ``levelAt`` движка без сглаживания (для тестов)."""
    if not track or not track.get("v"):
        return 0
    k = math.floor((t - float(track.get("t0") or 0)) * float(track.get("fps") or 30) + 1e-6)
    v = track["v"]
    return int(v[k]) if 0 <= k < len(v) else 0


__all__ = ["attach_levels", "band_filter", "band_levels_for_points", "envelope", "level_at", "music_positions",
           "plan_levels", "power_sum", "shift_levels"]
