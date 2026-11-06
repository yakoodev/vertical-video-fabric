"""Анализ видео: границы по звуку (из ``app/ai/service.py``)."""

from __future__ import annotations

import array
import math
import re
import subprocess
from pathlib import Path

NARRATIVE_SILENCE_THRESHOLD_DB = -35
NARRATIVE_SILENCE_MIN_DURATION_SEC = 0.25
NARRATIVE_ENERGY_SAMPLE_RATE = 8000
NARRATIVE_ENERGY_WINDOW_SEC = 0.2
NARRATIVE_ENERGY_LOCAL_RADIUS = 2
NARRATIVE_ENERGY_PERCENTILE = 0.35
SILENCE_START_RE = re.compile(r"silence_start:\s*(?P<time>\d+(?:\.\d+)?)")
SILENCE_END_RE = re.compile(r"silence_end:\s*(?P<time>\d+(?:\.\d+)?)")


def _detect_audio_boundaries(path: Path, source_duration: float) -> list[float]:
    if not path or not path.exists() or source_duration <= 0:
        return []
    try:
        proc = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostdin",
                "-i",
                str(path),
                "-vn",
                "-af",
                f"silencedetect=n={NARRATIVE_SILENCE_THRESHOLD_DB}dB:d={NARRATIVE_SILENCE_MIN_DURATION_SEC:g}",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=60 * 5,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    output = f"{proc.stderr or ''}\n{proc.stdout or ''}"
    boundaries = [0.0, float(source_duration)]
    for line in output.splitlines():
        start_match = SILENCE_START_RE.search(line)
        if start_match:
            boundaries.append(float(start_match.group("time")))
        end_match = SILENCE_END_RE.search(line)
        if end_match:
            boundaries.append(float(end_match.group("time")))
    boundaries.extend(_detect_audio_energy_boundaries(path, source_duration))
    return _unique_sorted_boundaries(boundaries, source_duration)


def _detect_audio_energy_boundaries(path: Path, source_duration: float) -> list[float]:
    if not path or not path.exists() or source_duration <= 0:
        return []
    try:
        proc = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostdin",
                "-i",
                str(path),
                "-vn",
                "-ac",
                "1",
                "-ar",
                str(NARRATIVE_ENERGY_SAMPLE_RATE),
                "-f",
                "s16le",
                "-",
            ],
            capture_output=True,
            check=False,
            timeout=60 * 5,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0 or not proc.stdout:
        return []
    return _energy_boundaries_from_pcm(
        proc.stdout,
        sample_rate=NARRATIVE_ENERGY_SAMPLE_RATE,
        window_sec=NARRATIVE_ENERGY_WINDOW_SEC,
        source_duration=source_duration,
    )


def _energy_boundaries_from_pcm(
    pcm: bytes,
    *,
    sample_rate: int,
    window_sec: float,
    source_duration: float,
) -> list[float]:
    samples = array.array("h")
    samples.frombytes(pcm)
    if not samples:
        return []
    window_size = max(1, int(sample_rate * window_sec))
    energies: list[float] = []
    for offset in range(0, len(samples), window_size):
        chunk = samples[offset : offset + window_size]
        if not chunk:
            continue
        square_sum = sum(float(value) * float(value) for value in chunk)
        energies.append(math.sqrt(square_sum / len(chunk)))
    if len(energies) < NARRATIVE_ENERGY_LOCAL_RADIUS * 2 + 1:
        return []
    sorted_energies = sorted(energies)
    threshold_index = min(len(sorted_energies) - 1, max(0, int(len(sorted_energies) * NARRATIVE_ENERGY_PERCENTILE)))
    threshold = sorted_energies[threshold_index]
    boundaries: list[float] = []
    radius = NARRATIVE_ENERGY_LOCAL_RADIUS
    for index in range(radius, len(energies) - radius):
        value = energies[index]
        if value > threshold:
            continue
        neighborhood = energies[index - radius : index + radius + 1]
        if value > min(neighborhood):
            continue
        timestamp = (index + 0.5) * window_sec
        if 0 < timestamp < source_duration:
            boundaries.append(round(timestamp, 3))
    return boundaries


def _unique_sorted_boundaries(boundaries: list[float], source_duration: float) -> list[float]:
    unique: list[float] = []
    for value in sorted(boundaries):
        if value < 0 or (source_duration > 0 and value > source_duration):
            continue
        if unique and abs(unique[-1] - value) < 0.05:
            continue
        unique.append(round(value, 3))
    return unique


def _expand_range_to_audio_boundaries(
    start: float,
    end: float,
    boundaries: list[float],
    source_duration: float,
    *,
    search_sec: float,
    max_extra_sec: float,
    max_duration_sec: float,
) -> tuple[float, float]:
    search = max(0.0, float(search_sec or 0))
    max_extra = max(0.0, float(max_extra_sec or 0))
    if search <= 0 or max_extra <= 0:
        return start, end
    previous = _nearest_previous_boundary(boundaries, start, min(search, max_extra))
    next_ = _nearest_next_boundary(boundaries, end, min(search, max_extra))
    candidates = [(start, end)]
    if previous is not None:
        candidates.append((previous, end))
    if next_ is not None:
        candidates.append((start, next_))
    if previous is not None and next_ is not None:
        candidates.append((previous, next_))
    valid = []
    for candidate_start, candidate_end in candidates:
        candidate_start = max(0.0, min(start, candidate_start))
        if source_duration > 0:
            candidate_end = min(source_duration, max(end, candidate_end))
        else:
            candidate_end = max(end, candidate_end)
        if candidate_end <= candidate_start:
            continue
        if candidate_end - candidate_start > max_duration_sec:
            continue
        valid.append((candidate_start, candidate_end))
    adjusted = [item for item in valid if item[0] < start or item[1] > end]
    if not adjusted:
        return start, end
    return min(
        adjusted,
        key=lambda item: (
            -int(item[0] < start) - int(item[1] > end),
            (start - item[0]) + (item[1] - end),
            item[0],
            item[1],
        ),
    )


def _nearest_previous_boundary(boundaries: list[float], value: float, max_distance: float) -> float | None:
    previous = [boundary for boundary in boundaries if boundary <= value and value - boundary <= max_distance]
    return max(previous) if previous else None


def _nearest_next_boundary(boundaries: list[float], value: float, max_distance: float) -> float | None:
    next_boundaries = [boundary for boundary in boundaries if boundary >= value and boundary - value <= max_distance]
    return min(next_boundaries) if next_boundaries else None
