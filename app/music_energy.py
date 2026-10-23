"""Где у трека энергия: отметки для старта фоновой музыки.

Проверочный прогон Gemini: «Бодрый фон» поставлен с 0 с, а барабаны начинаются около
10.4 с — первые 10 с промо шли под тихое вступление. Здесь по огибающей громкости
(окна 0.5 с) считаем:

- ``energy_start_sec`` — первая секунда, после которой громкость держится близко к
  «рабочей» громкости трека (не провал, не вступление);
- ``drop_sec`` — самый сильный подъём громкости (вход барабанов/дропа), None — нет такого;
- ``bpm`` — темп по автокорреляции атак (None — не уверены).

Отметки хранятся у трека (audio_tracks.energy_*): считаются при импорте, а для старых
треков — лениво при первом запросе (``ensure_energy``), по одному треку за раз.
"""

from __future__ import annotations

import math
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

# Версия разметки: поменялся алгоритм — старые отметки пересчитаются лениво.
ENERGY_VERSION = 1
WINDOW_SEC = 0.5
SAMPLE_RATE = 8000  # для огибающей и темпа хватает, декодировать быстро
MAX_ANALYZE_SEC = 15 * 60
# «Рабочая» громкость — этот перцентиль окон (громкие места трека, без выбросов).
WORK_PERCENTILE = 70
# Энергичная часть: средняя громкость следующих HOLD_SEC не ниже рабочей минус NEAR_DB,
# и само окно уже громкое (не ниже рабочей минус START_DB).
HOLD_SEC = 4.0
NEAR_DB = 3.0
START_DB = 4.0
# Дроп — подъём средней громкости «после» над «до» (по DROP_SPAN_SEC) хотя бы на DROP_MIN_DB.
DROP_SPAN_SEC = 2.0
DROP_MIN_DB = 3.0
SILENCE_DB = -60.0
# Темп: атаки по 10 мс, автокорреляция в пределах 60..180 BPM, сводим в 75..165.
HOP_SEC = 0.01
BPM_RANGE = (60.0, 180.0)
BPM_FOLD = (75.0, 165.0)
BPM_MIN_CONFIDENCE = 0.15
# Тише, чем на столько дБ ниже рабочей, начинается трек — подсказка «пропусти вступление».
QUIET_INTRO_MIN_SEC = 2.0
# Сколько секунд ленивой разметки за один запрос: остальное — в следующий раз.
BACKFILL_BUDGET_SEC = 20.0

_LOCK = threading.Lock()  # тяжёлое не параллелим: один трек за раз на весь сервис


def decode_pcm(path: str | Path, seconds: float = MAX_ANALYZE_SEC) -> Any:
    """Звук файла: моно float32, SAMPLE_RATE Гц (numpy), не длиннее ``seconds``."""
    import numpy as np

    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-loglevel", "error", "-i", str(path), "-vn", "-t", f"{seconds:g}",
         "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "-"],
        capture_output=True, timeout=120, check=False,
    )
    if proc.returncode != 0 or not proc.stdout:
        raise ValueError("не удалось прочитать звук трека: " + (proc.stderr or b"").decode("utf-8", "replace")[-200:])
    return np.frombuffer(proc.stdout, dtype=np.float32)


def window_levels(samples: Any, rate: int = SAMPLE_RATE, window: float = WINDOW_SEC) -> list[float]:
    """Громкость окон по ``window`` с (RMS, дБ полной шкалы)."""
    import numpy as np

    size = max(1, int(round(rate * window)))
    count = len(samples) // size
    if count <= 0:
        return []
    blocks = np.asarray(samples[: count * size], dtype=np.float64).reshape(count, size)
    rms = np.sqrt(np.mean(blocks * blocks, axis=1))
    return [round(float(20 * math.log10(max(v, 1e-6))), 2) for v in rms]


def _power_mean(levels: list[float]) -> float:
    if not levels:
        return SILENCE_DB
    return 10 * math.log10(sum(10 ** (v / 10) for v in levels) / len(levels))


def work_level(levels: list[float]) -> float | None:
    """«Рабочая» громкость трека: WORK_PERCENTILE-й перцентиль слышимых окон."""
    audible = sorted(v for v in levels if v > SILENCE_DB)
    if not audible:
        return None
    index = min(len(audible) - 1, int(len(audible) * WORK_PERCENTILE / 100))
    return audible[index]


def energy_start(levels: list[float], window: float = WINDOW_SEC) -> float | None:
    """Первая секунда, с которой громкость держится у рабочей (``HOLD_SEC`` вперёд)."""
    work = work_level(levels)
    if work is None:
        return None
    hold = max(1, int(round(HOLD_SEC / window)))
    for i, level in enumerate(levels):
        ahead = levels[i:i + hold]
        if level >= work - START_DB and _power_mean(ahead) >= work - NEAR_DB:
            return round(i * window, 2)
    return None


def drop_time(levels: list[float], window: float = WINDOW_SEC) -> float | None:
    """Самый сильный подъём громкости (секунда, где «после» громче «до» больше всего)."""
    span = max(1, int(round(DROP_SPAN_SEC / window)))
    best, best_rise = None, DROP_MIN_DB
    for i in range(span, len(levels) - span + 1):
        before, after = levels[i - span:i], levels[i:i + span]
        if max(after) <= SILENCE_DB:
            continue
        rise = _power_mean(after) - _power_mean(before)
        if rise > best_rise + 1e-9:
            best, best_rise = i, rise
    return None if best is None else round(best * window, 2)


def estimate_bpm(samples: Any, rate: int = SAMPLE_RATE) -> float | None:
    """Темп по автокорреляции огибающей атак; None — пульса не слышно."""
    import numpy as np

    hop = max(1, int(round(rate * HOP_SEC)))
    count = len(samples) // hop
    if count < int(8 / HOP_SEC):  # меньше 8 с — не судим
        return None
    blocks = np.asarray(samples[: count * hop], dtype=np.float64).reshape(count, hop)
    energy = np.log10(np.mean(blocks * blocks, axis=1) + 1e-10)
    onset = np.maximum(0.0, np.diff(energy))
    onset = onset - onset.mean()
    norm = float(np.dot(onset, onset))
    if norm <= 1e-12:
        return None
    lo = int(round(60.0 / BPM_RANGE[1] / HOP_SEC))
    hi = int(round(60.0 / BPM_RANGE[0] / HOP_SEC))
    corr = np.array([float(np.dot(onset[:-lag], onset[lag:])) / norm for lag in range(lo, hi + 1)])
    k = int(np.argmax(corr))
    if corr[k] < BPM_MIN_CONFIDENCE:
        return None
    lag = lo + k
    if 0 < k < len(corr) - 1:  # уточняем вершину параболой
        a, b, c = corr[k - 1], corr[k], corr[k + 1]
        denom = a - 2 * b + c
        if abs(denom) > 1e-12:
            lag += 0.5 * (a - c) / denom
    bpm = 60.0 / (lag * HOP_SEC)
    while bpm < BPM_FOLD[0]:
        bpm *= 2
    while bpm > BPM_FOLD[1]:
        bpm /= 2
    return round(bpm, 1)


def energy_marks(levels: list[float], window: float = WINDOW_SEC) -> dict:
    """Отметки по огибающей: energy_start_sec, drop_sec (без темпа)."""
    return {"energy_start_sec": energy_start(levels, window), "drop_sec": drop_time(levels, window)}


def analyze_track(path: str | Path) -> dict:
    """Отметки трека: {energy_start_sec, drop_sec, bpm}."""
    samples = decode_pcm(path)
    marks = energy_marks(window_levels(samples))
    try:
        marks["bpm"] = estimate_bpm(samples)
    except Exception:  # noqa: BLE001 - темп — бонус, не роняет разметку
        marks["bpm"] = None
    return marks


def track_fields(path: str | Path) -> dict:
    """Поля трека для store.update_audio_track (с версией разметки)."""
    marks = analyze_track(path)
    return {**marks, "energy_version": ENERGY_VERSION}


def needs_energy(track: dict) -> bool:
    return int(track.get("energy_version") or 0) < ENERGY_VERSION


def mark_track(store: Any, track: dict) -> dict:
    """Разметить один трек (под общим замком); сбой — трек помечен, чтобы не мучить его снова."""
    with _LOCK:
        fresh = store.get_audio_track(int(track["id"]))
        if not needs_energy(fresh):
            return fresh
        try:
            fields = track_fields(fresh["file_path"])
        except Exception:  # noqa: BLE001 - битый файл: отметок нет, но и повторов нет
            fields = {"energy_start_sec": None, "drop_sec": None, "bpm": None, "energy_version": ENERGY_VERSION}
        return store.update_audio_track(int(fresh["id"]), **fields)


def ensure_energy(store: Any, tracks: list[dict] | None = None, budget_sec: float = BACKFILL_BUDGET_SEC) -> list[dict]:
    """Лениво разметить треки без отметок (по одному, не дольше ``budget_sec`` за вызов)."""
    tracks = store.list_audio_tracks() if tracks is None else tracks
    out, started = [], time.monotonic()
    for track in tracks:
        if needs_energy(track) and time.monotonic() - started < budget_sec:
            try:
                track = mark_track(store, track)
            except Exception:  # noqa: BLE001 - трек удалили между запросами и т.п.
                pass
        out.append(track)
    return out


def energy_view(track: dict) -> dict:
    """Отметки для ответа агенту (только известные)."""
    if needs_energy(track):
        return {}
    view = {}
    for key in ("energy_start_sec", "drop_sec", "bpm"):
        value = track.get(key)
        if value is not None:
            view[key] = round(float(value), 1)
    return view


def quiet_intro_sec(track: dict, start_sec: float) -> float:
    """Сколько секунд тихого вступления зазвучит, если играть трек с ``start_sec`` (0 — нет)."""
    energy = track.get("energy_start_sec")
    if needs_energy(track) or energy is None:
        return 0.0
    gap = float(energy) - float(start_sec or 0.0)
    return round(gap, 1) if gap >= QUIET_INTRO_MIN_SEC else 0.0
