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
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Callable

from app.motion import REACT_SOURCES, react_sources
from app.pngtuber import (
    PCM_RATE,
    SILENCE_DB,
    _cached,
    _wav_pcm,
    frame_sources,
    levels_for_points,
    source_reader,
)

# Огибающая: спад за ~0.12 с, шум + 3 дБ — ноль, 97-й перцентиль — 100, ширина не меньше 12 дБ.
LEVEL_RELEASE_SEC = 0.12
FLOOR_PERCENTILE = 0.15
TOP_PERCENTILE = 0.97
FLOOR_OVER_DB = 3.0
MIN_SPAN_DB = 12.0
# Длиннее — дальше тишина (минуты клипа; часы — явная ошибка).
MAX_LEVELS_SEC = 30 * 60.0


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
) -> tuple[dict[str, dict], list[str]]:
    """``comp.levels`` клипа: {источник: {t0: 0, fps, v [0..100]}} по кадрам всего клипа и пометки.

    ``scale`` — шкала времени слоёв (``clip`` — до вырезов, ``final`` — готовый ролик).
    ``music`` — {track_id, start_sec} рендера; нет — из настроек клипа."""
    from app.timebase import ClipClock

    fps = max(1.0, float(fps))
    wanted = [s for s in REACT_SOURCES if s in set(sources or [])]
    if not wanted:
        return {}, []
    segments = plan.get("segments") or []
    clock = ClipClock.for_plan(plan, fps)
    total = min(MAX_LEVELS_SEC, clock.final_duration if scale == "final" else clock.clip_duration)
    count = max(0, int(math.ceil(total * fps - 1e-6)))
    times = [k / fps for k in range(count)]
    notes: list[str] = []
    points = frame_sources(clock, segments, times, scale, plan.get("source_id"))

    def source_levels(track: str) -> list[float | None]:
        read = source_reader(store, track, notes, silent="реакция на звук молчит")
        try:
            return levels_for_points(points, fps, read)
        except Exception as exc:  # noqa: BLE001 - слой без реакции лучше, чем упавший рендер
            notes.append(f"звук источника не разобран ({str(exc)[:160]}) — реакция на звук молчит")
            return [None] * count

    music_db: list[float | None] | None = None
    music_volume = 1.0
    cfg = music_config(plan, music)
    if cfg and ("music" in wanted or "mix" in wanted):
        read, duration, music_volume = _music_reader(store, cfg["track_id"], notes)
        if read is not None:
            final = [clock.clip_to_final(t) if scale == "clip" else t for t in times]
            try:
                music_db = levels_for_points(music_positions(final, cfg["start_sec"], duration), fps, read)
            except Exception as exc:  # noqa: BLE001
                notes.append(f"музыка не разобрана ({str(exc)[:160]}) — реакция на музыку молчит")
    elif "music" in wanted:
        notes.append("у клипа нет фоновой музыки — слои с react source music стоят")

    out: dict[str, dict] = {}
    for source in wanted:
        if source == "voice":
            db = source_levels("speech")
        elif source == "music":
            db = music_db if music_db is not None else [None] * count
        else:
            db = source_levels("mix")
            if music_db is not None:
                # Музыка в смеси — с громкостью трека (рендер кладёт её тише голоса).
                gain = 20 * math.log10(max(1e-3, music_volume))
                db = [power_sum(a, None if b is None else b + gain) for a, b in zip(db, music_db)]
        out[source] = {"t0": 0.0, "fps": fps, "v": envelope(db, fps)}
    return out, notes


def attach_levels(store: Any, plan: dict, motion: dict | None, fps: float, scale: str = "clip",
                  music: dict | None = None) -> dict | None:
    """Огибающие громкости — в моушн (``levels``), если у видимых слоёв есть ``react``."""
    sources = react_sources((motion or {}).get("layers"))
    if not motion or not sources:
        return motion
    levels, notes = plan_levels(store, plan, sources, fps, scale, music)
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


__all__ = ["attach_levels", "envelope", "level_at", "music_positions", "plan_levels", "power_sum", "shift_levels"]
