"""Громкость звука по времени: профиль исходника и громкие места клипа.

Для игр и стримов без речи (и без Gemini) моменты ищут по звуку: перестрелка,
взрыв, крик — это всплеск громкости над фоном. Агент-монтажёр Apex Legends
так и искал, только руками. Здесь: RMS в дБ по окнам ``step``, пики, сводка
(медиана, порог «громко» = медиана + LOUD_OVER_MEDIAN_DB) и слитые громкие
отрезки. Звук окна читается ffmpeg с быстрым поиском (``-ss``/``-t`` до ``-i``)
и тихой частотой — 4-часовой исходник целиком в память не попадает.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

LOUD_OVER_MEDIAN_DB = 6.0  # «громко» — на столько выше медианы окна/клипа
SILENCE_DB = -90.0
MAX_WINDOW_SEC = 600.0  # по умолчанию и максимум за вызов — 10 минут
MIN_STEP = 0.5
MAX_STEP = 30.0
RATE = 8000  # для громкости хватает; 10 минут — 9.6 МБ в памяти
MAX_PEAKS = 30


def pcm_levels(raw: bytes, rate: int, step: float) -> list[float]:
    """RMS в дБ (полная шкала) по окнам ``step`` секунд из 16-битного моно PCM."""
    import numpy as np

    samples = np.frombuffer(raw[: len(raw) // 2 * 2], dtype="<i2").astype(np.float64) / 32768.0
    win = max(1, int(round(rate * step)))
    count = len(samples) // win
    if count <= 0:
        return []
    chunks = samples[: count * win].reshape(count, win)
    rms = np.sqrt(np.mean(chunks * chunks, axis=1))
    return [round(max(SILENCE_DB, 20 * math.log10(v)) if v > 0 else SILENCE_DB, 1) for v in rms.tolist()]


def median(values: list[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        return SILENCE_DB
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def loud_spans(
    levels: list[float],
    step: float,
    threshold: float,
    *,
    t0: float = 0.0,
    gap: float = 0.3,
    min_len: float = 0.0,
    skip: Callable[[float, float], bool] | None = None,
) -> list[dict]:
    """Слитые отрезки громче ``threshold``: окна подряд и разрывы короче ``gap`` — один
    отрезок (раньше выходили сотни кусков по 0.1 с). ``skip(a, b)`` — окно не считать
    (там речь). Каждый отрезок: start, end, loudest_db."""
    spans: list[list[float]] = []
    for i, db in enumerate(levels):
        a = t0 + i * step
        b = a + step
        if db <= threshold or (skip is not None and skip(a, b)):
            continue
        if spans and a - spans[-1][1] <= gap + 1e-6:
            spans[-1][1] = b
            spans[-1][2] = max(spans[-1][2], db)
        else:
            spans.append([a, b, db])
    return [{"start": round(a, 2), "end": round(b, 2), "loudest_db": round(d, 1)}
            for a, b, d in spans if b - a >= min_len - 1e-6]


def top_peaks(levels: list[float], step: float, *, t0: float = 0.0, top: int = 10,
              min_sep: float = 3.0, floor: float | None = None) -> list[dict]:
    """Самые громкие моменты (по убыванию), не ближе ``min_sep`` секунд друг к другу."""
    order = sorted(range(len(levels)), key=lambda i: levels[i], reverse=True)
    picked: list[int] = []
    for i in order:
        if floor is not None and levels[i] <= floor:
            break
        if all(abs(i - j) * step >= min_sep for j in picked):
            picked.append(i)
            if len(picked) >= top:
                break
    return [{"t": round(t0 + (i + 0.5) * step, 2), "db": levels[i]} for i in picked]


def summary(levels: list[float]) -> dict:
    """Медиана, порог «громко», доля громкого и максимум."""
    med = median(levels)
    loud = med + LOUD_OVER_MEDIAN_DB
    share = sum(1 for v in levels if v > loud) / len(levels) if levels else 0.0
    return {"median_db": round(med, 1), "loud_db": round(loud, 1), "loud_share": round(share, 3),
            "max_db": round(max(levels), 1) if levels else SILENCE_DB}


def read_window_pcm(source: dict, start: float, length: float, rate: int = RATE) -> bytes:
    """Звук окна исходника (выбранные дорожки, моно ``rate`` Гц) — сырые 16-битные сэмплы."""
    from app.render import _run_ffmpeg
    from app.settings import settings
    from app.source_audio import extract_args, source_tracks
    from app.speech_onsets import wav_pcm

    path = Path(str(source.get("local_path") or ""))
    if not path.is_file():
        raise ValueError("файла исходника нет")
    out = settings.tmp_dir / f"loudness-{uuid4().hex}.wav"
    try:
        _run_ffmpeg(extract_args(path, out, source_tracks(source), start=start, duration=length, rate=rate),
                    timeout=60 * 5)
        return wav_pcm(out)
    finally:
        out.unlink(missing_ok=True)


def source_loudness(store: Any, source_id: int, from_sec: float | None = None, to_sec: float | None = None,
                    step: float | None = None, top: int = 10) -> dict:
    """Профиль громкости окна исходника (секунды исходника)."""
    source = store.get_source(int(source_id))
    total = float(source.get("duration_sec") or 0)
    lo = max(0.0, float(from_sec or 0.0))
    if total and lo >= total:
        raise ValueError(f"from_sec за концом исходника ({total:.1f} с)")
    hi = float(to_sec) if to_sec is not None else lo + MAX_WINDOW_SEC
    if total:
        hi = min(hi, total)
    notes = []
    if hi - lo > MAX_WINDOW_SEC:
        hi = lo + MAX_WINDOW_SEC
        notes.append(f"окно урезано до {MAX_WINDOW_SEC / 60:g} мин — дальше следующим вызовом с from_sec={hi:.0f}")
    if hi - lo < 1.0:
        raise ValueError("окно короче секунды")
    step = min(MAX_STEP, max(MIN_STEP, float(step or 1.0)))
    levels = pcm_levels(read_window_pcm(source, lo, hi - lo), RATE, step)
    if not levels:
        raise ValueError("в этом окне нет звука (или нет звуковой дорожки)")
    info = summary(levels)
    top = max(1, min(MAX_PEAKS, int(top or 10)))
    return {
        "project_id": source["id"],
        "from_sec": round(lo, 2),
        "to_sec": round(lo + len(levels) * step, 2),
        "step": step,
        "db": levels,
        "summary": info,
        "peaks": top_peaks(levels, step, t0=lo, top=top, min_sep=max(3.0, 3 * step), floor=info["median_db"]),
        "loud": loud_spans(levels, step, info["loud_db"], t0=lo, gap=max(1.0, step), min_len=step)[:60],
        "note": ("db[i] — RMS дБ отрезка from_sec + i·step (секунды исходника); peaks — самые громкие места; "
                 "loud — отрезки громче loud_db. Перестрелки/реакции — всплески над медианой: смотри их кадры "
                 "source_contact_sheet") + ("; " + "; ".join(notes) if notes else ""),
    }
