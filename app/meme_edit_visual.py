"""Дешёвый взгляд на картинку записи для мем-эдита (app/meme_edit.py): движение и экран смерти/зрителя.

Один проход ffmpeg: кадры 64×36 (2 в секунду; запись длиннее 15 мин — только ключевые кадры), numpy:

* ``motion`` — средняя разница соседних кадров (0..255): рубка не ложится на дверь и пустой коридор;
* ``sat`` — средняя насыщенность кадра: в Apex после смерти (нок, коробка, режим зрителя) кадр почти серый
  во весь экран и с плашкой снизу — это ``dead_spans``: там не бывает «килла» и «нока», это ``death``.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

W, H = 64, 36
FPS = 2.0
LONG_SEC = 15 * 60.0
MAX_SEC = 3 * 3600.0
DEAD_SAT = 0.09  # средняя насыщенность серого экрана смерти (живой геймплей ≈ 0.25–0.45)
DEAD_REL = 0.4  # …и меньше этой доли медианы записи (снежная карта сама по себе бледная)
DEAD_MIN_SEC = 1.5
BANNER_ROWS = 6  # низ кадра: плашка «режим зрителя» — тёмная полоса или яркий текст на сером
BANNER_MIN = 20.0  # разница яркости низа и остального кадра у плашки (живой геймплей обычно 2–30, в среднем ниже)
GREY_SAT = 0.06  # совсем серый кадр — смерть и без плашки
_PTS = re.compile(r"pts_time:(\d+(?:\.\d+)?)")


def profile(path: str | Path, duration: float) -> dict:
    """``{t, motion, sat, banner}`` по кадрам записи; пусто, если ffmpeg не прочитал файл."""
    import numpy as np

    long = duration > LONG_SEC
    args = ["ffmpeg", "-hide_banner", "-nostats"]
    if long:
        args += ["-skip_frame", "nokey"]
    args += ["-t", f"{min(duration, MAX_SEC):.3f}", "-i", str(path), "-an", "-vf",
             (f"scale={W}:{H},showinfo" if long else f"fps={FPS},scale={W}:{H},showinfo"),
             "-vsync", "vfr", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    try:
        proc = subprocess.run(args, capture_output=True, timeout=1800, check=False)
    except (OSError, subprocess.SubprocessError):
        return {"t": [], "motion": [], "sat": [], "banner": []}
    frame = W * H * 3
    count = len(proc.stdout) // frame
    if count < 2:
        return {"t": [], "motion": [], "sat": [], "banner": []}
    frames = np.frombuffer(proc.stdout[: count * frame], dtype=np.uint8).reshape(count, H, W, 3).astype(np.float32)
    times = [float(m) for m in _PTS.findall(proc.stderr.decode("utf-8", "replace"))][:count]
    if len(times) < count:
        times = [i / FPS for i in range(count)]
    gray = frames.mean(axis=3)
    motion = np.zeros(count)
    motion[1:] = np.abs(np.diff(gray, axis=0)).mean(axis=(1, 2))
    motion[0] = motion[1]
    hi, lo = frames.max(axis=3), frames.min(axis=3)
    sat = ((hi - lo) / np.maximum(hi, 1.0)).mean(axis=(1, 2))
    bottom = gray[:, -BANNER_ROWS:, :]
    banner = np.abs(bottom.mean(axis=(1, 2)) - gray[:, : H - BANNER_ROWS, :].mean(axis=(1, 2)))
    return {"t": [round(t, 3) for t in times], "motion": [round(float(v), 3) for v in motion],
            "sat": [round(float(v), 4) for v in sat], "banner": [round(float(v), 2) for v in banner]}


def dead_spans(prof: dict) -> list[tuple[float, float]]:
    """Отрезки «серого экрана» (смерть, нок, режим зрителя): насыщенность ниже ``DEAD_SAT`` и ``DEAD_REL`` медианы
    не меньше ``DEAD_MIN_SEC`` подряд."""
    import numpy as np

    sat, times = np.asarray(prof.get("sat") or []), list(prof.get("t") or [])
    banners = np.asarray(prof.get("banner") or [])
    if len(sat) < 4:
        return []
    limit = min(DEAD_SAT, float(np.median(sat)) * DEAD_REL)
    grey = sat < limit
    spans: list[tuple[float, float]] = []
    i = 0
    while i < len(grey):
        if not grey[i]:
            i += 1
            continue
        j = i
        while j < len(grey) and grey[j]:
            j += 1
        a, b = times[i], times[j - 1] + (times[1] - times[0] if len(times) > 1 else 0.5)
        banner = float(np.mean(banners[i:j])) if len(banners) >= j else BANNER_MIN
        # Плашка снизу (режим зрителя, «жди восстановления») или совсем серый кадр — не просто бледная снежная карта.
        if b - a >= DEAD_MIN_SEC and (banner >= BANNER_MIN or float(np.mean(sat[i:j])) < GREY_SAT):
            spans.append((round(a, 2), round(b, 2)))
        i = j
    return spans


def apply_deaths(events: list[dict], spans: list[tuple[float, float]], lead: float = 4.0) -> list[dict]:
    """События с учётом смерти: внутри серого экрана нока, килла и выстрелов не бывает — прочь; самый сильный удар
    за ``lead`` с до серого экрана — это попадание ПО ИГРОКУ: ``death``; удара нет — ``death`` в начале экрана."""
    out = [e for e in events if not any(a <= e["t"] <= b for a, b in spans) or e["kind"] in ("death", "reaction")]
    for a, _b in spans:
        before = [e for e in out if a - lead <= e["t"] < a and e["kind"] in ("knock", "kill", "shot_burst")]
        if before:
            hit = max(before, key=lambda e: e["score"])
            out = [e for e in out if e is not hit]
            out.append({**hit, "kind": "death", "score": max(0.6, hit["score"]),
                        "detail": "серый экран смерти/зрителя сразу после удара (по картинке)"})
        elif not any(e["kind"] == "death" and abs(e["t"] - a) < lead for e in out):
            out.append({"t": round(a, 3), "kind": "death", "score": 0.6,
                        "detail": "серый экран смерти/зрителя (по картинке)"})
    return sorted(out, key=lambda e: e["t"])


def motion_at(prof: dict) -> Any:
    """Функция t → движение (0..1 относительно 90-го перцентиля записи); без профиля — всегда 1."""
    import bisect

    import numpy as np

    times, motion = list(prof.get("t") or []), np.asarray(prof.get("motion") or [], dtype=float)
    if len(motion) < 2:
        return lambda _t: 1.0
    top = float(np.percentile(motion, 90)) or 1.0
    norm = np.minimum(1.0, motion / top)

    def at(t: float) -> float:
        i = bisect.bisect_left(times, t)
        lo, hi = max(0, i - 1), min(len(times) - 1, i)
        return float(max(norm[lo], norm[hi]))

    return at
