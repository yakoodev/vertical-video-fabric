"""Волна звука клипа для таймлайна: по ней видно речь и паузы.

Монтажёр режет по звуку: где говорят, где пауза, где смех. На дорожке были
только прямоугольники, и паузу приходилось искать ушами, перематывая превью.

Считаем один раз на состав кусков: звук клипа сводится тем же способом, что и
для субтитров (куски встык), а дальше берём максимум амплитуды на корзину.
Результат кэшируем в runtime по хэшу кусков — пересчёт идёт только когда
таймкоды поменялись.
"""

from __future__ import annotations

import array
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from app.clip_subtitles import build_clip_audio_args
from app.settings import settings

BUCKETS = 600
SAMPLE_RATE = 16000


def _cache_path(clip_plan_id: int, key: str) -> Path:
    directory = settings.runtime_dir / "waveforms"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{clip_plan_id}-{key}.json"


def _pieces_key(pieces: list[tuple[float, float]]) -> str:
    raw = ";".join(f"{a:.3f}-{b:.3f}" for a, b in pieces)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def clip_waveform(store: Any, clip_plan_id: int, buckets: int = BUCKETS) -> dict:
    """``{"duration": сек, "peaks": [0..1, …]}`` для дорожки звука."""
    plan = store.get_clip_plan(clip_plan_id)
    source = store.get_source(plan["source_id"])
    pieces = [(float(s["start_sec"]), float(s["end_sec"])) for s in plan.get("segments") or []]
    if not pieces:
        return {"duration": 0.0, "peaks": []}
    duration = round(sum(b - a for a, b in pieces), 3)
    buckets = max(40, min(2000, int(buckets)))
    cache = _cache_path(clip_plan_id, f"{_pieces_key(pieces)}-{buckets}")
    if cache.exists():
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass

    wav = settings.tmp_dir / "waveform" / f"{clip_plan_id}-{_pieces_key(pieces)}.wav"
    wav.parent.mkdir(parents=True, exist_ok=True)
    if not wav.exists():
        subprocess.run(
            build_clip_audio_args(Path(source["local_path"]), pieces, wav),
            check=True,
            timeout=60 * 20,
            capture_output=True,
        )
    peaks = _peaks_of_wav(wav, buckets)
    wav.unlink(missing_ok=True)
    payload = {"duration": duration, "peaks": peaks}
    try:
        cache.write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        pass
    return payload


def _peaks_of_wav(path: Path, buckets: int) -> list[float]:
    """Максимум громкости на корзину, 0..1. Заголовок wav пропускаем поиском 'data'."""
    raw = path.read_bytes()
    start = raw.find(b"data")
    body = raw[start + 8 :] if start >= 0 else raw[44:]
    samples = array.array("h")
    samples.frombytes(body[: len(body) - (len(body) % 2)])
    if not samples:
        return []
    size = max(1, len(samples) // buckets)
    out: list[float] = []
    for index in range(0, len(samples), size):
        chunk = samples[index : index + size]
        if not chunk:
            continue
        out.append(round(max(abs(min(chunk)), abs(max(chunk))) / 32768, 4))
    # Нормируем на самый громкий момент: тихий клип иначе рисуется плоской линией.
    top = max(out) or 1.0
    return [round(min(1.0, value / top), 4) for value in out[:buckets]]
