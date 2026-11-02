"""Scene-cut timestamps for a whole source via ffmpeg scenedetect.

Used by the "cuts" refinement hypothesis to pull clip boundaries onto real shot
changes. One cheap ffmpeg pass; the caller caches the result on the source.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

_PTS_RE = re.compile(r"pts_time:(\d+(?:\.\d+)?)")


def detect_scene_cuts(local_path: str | None, threshold: float = 0.35, *, start: float | None = None,
                      duration: float | None = None) -> list[float]:
    """Return scene-change times (seconds of the source), or [] on any failure.

    ``start``/``duration`` — only this window of the source (seconds still of the whole source):
    avatar_box looks for the shot change between two frames without decoding the whole stream."""
    if not local_path:
        return []
    path = Path(local_path)
    if not path.exists():
        return []
    window: list[str] = []
    offset = 0.0
    if start is not None:
        offset = max(0.0, float(start))
        window += ["-ss", f"{offset:.3f}"]
    if duration is not None:
        window += ["-t", f"{max(0.05, float(duration)):.3f}"]
    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-nostats", *window, "-i", str(path),
                "-filter_complex", f"select='gt(scene,{threshold})',showinfo",
                "-f", "null", "-",
            ],
            capture_output=True,
            text=True,
            timeout=60 * 20,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    # showinfo writes to stderr.
    times = sorted({round(float(m) + offset, 3) for m in _PTS_RE.findall(proc.stderr or "")})
    return times


# ------------------------------------------------------------------ смена сцены наплывом

TRANSITION_FPS = 30
TRANSITION_SIZE = (64, 36)
TRANSITION_HALF_SEC = 0.5     # мера смены: кадр через полсекунды против кадра за полсекунды
TRANSITION_REF_SEC = 1.2      # опорные кадры «до» и «после» — так далеко от середины смены
TRANSITION_MIN_CHANGE = 12.0  # средняя разница яркости (0..255) — меньше это не смена сцены
TRANSITION_EDGE = 0.1         # доля пути от «до» к «после»: до неё кадр ещё чистый, после 1−её — уже
MAX_TRANSITION_WINDOW_SEC = 120.0


def gray_frames(local_path: str, start: float, duration: float, fps: int = TRANSITION_FPS,
                size: tuple[int, int] = TRANSITION_SIZE) -> tuple[list[float], Any]:
    """Маленькие серые кадры отрезка исходника: (секунды исходника кадров, массив n×h×w float32)."""
    import numpy as np

    w, h = size
    start = max(0.0, float(start))
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-t", f"{max(0.05, float(duration)):.3f}",
         "-i", str(local_path), "-an", "-vf", f"fps={fps},scale={w}:{h}:flags=area,format=gray",
         "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True, timeout=60 * 5,
    )
    data = proc.stdout or b""
    n = len(data) // (w * h)
    frames = np.frombuffer(data[: n * w * h], dtype=np.uint8).reshape(n, h, w).astype(np.float32)
    return [round(start + k / fps, 4) for k in range(n)], frames


def transition_span(local_path: str | None, lo: float, hi: float, hint: float | None = None,
                    fps: int = TRANSITION_FPS) -> dict | None:
    """Смена сцены между секундами исходника ``lo`` и ``hi``: ``{start, end, mid, hard, change}`` или None.

    QA 02.11 (клип 895): сцена в OBS сменилась наплывом ≈0.4 с, а ``detect_scene_cuts`` (порог на
    разницу соседних кадров) отметил только конец наплыва — граница мест аватара встала там, и рамка
    старого места 0.35 с показывала ютуб. Здесь смена ищется по кадрам через полсекунды (наплыв виден
    целиком), а её начало и конец — по доле пути каждого кадра от опорного «до» к опорному «после»:
    ``start`` — первый кадр, где уже видна новая сцена, ``end`` — первый, где старой уже нет. Жёсткая
    склейка — ``hard``. ``hint`` — склейка, найденная ffmpeg: ищем в секунде от неё."""
    import numpy as np

    if not local_path or not Path(local_path).exists() or hi <= lo:
        return None
    if hint is not None:
        a, b = max(lo, float(hint) - 2.0), min(hi, float(hint) + 1.0)
    else:
        a, b = lo, hi
    if b - a > MAX_TRANSITION_WINDOW_SEC or b - a < 2 * TRANSITION_HALF_SEC:
        return None
    try:
        times, frames = gray_frames(str(local_path), a, b - a, fps)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    n = len(times)
    k = max(1, int(round(TRANSITION_HALF_SEC * fps)))
    if n < 2 * k + 2:
        return None
    # change[j] — разница кадров j+2k и j, то есть вокруг кадра j+k.
    change = np.abs(frames[2 * k:] - frames[: n - 2 * k]).mean(axis=(1, 2))
    centers = np.arange(k, n - k)
    if hint is not None:
        near = np.abs(np.asarray(times)[centers] - float(hint)) <= 1.0
        if not near.any():
            return None
        change = np.where(near, change, -1.0)
    peak = float(change.max())
    if peak < TRANSITION_MIN_CHANGE:
        return None
    # У склейки и у наплыва мера почти максимальна много кадров подряд — середина этого плато.
    strong = change >= peak * 0.9
    best = int(np.argmax(change))
    first, last = best, best
    while first > 0 and strong[first - 1]:
        first -= 1
    while last < len(change) - 1 and strong[last + 1]:
        last += 1
    center = int(centers[(first + last) // 2])
    ref = max(k, int(round(TRANSITION_REF_SEC * fps)))
    ia, ib = max(0, center - ref), min(n - 1, center + ref)
    before = frames[max(0, ia - 1): ia + 2].mean(axis=0)
    after = frames[ib - 1: min(n, ib + 2)].mean(axis=0)
    if float(np.abs(after - before).mean()) < TRANSITION_MIN_CHANGE:
        return None
    da = np.abs(frames[ia: ib + 1] - before).mean(axis=(1, 2))
    db = np.abs(frames[ia: ib + 1] - after).mean(axis=(1, 2))
    path = da / np.maximum(da + db, 1e-6)  # 0 — ещё «до», 1 — уже «после»
    c = center - ia
    s = 0
    for i in range(c, -1, -1):
        if path[i] <= TRANSITION_EDGE:
            s = i + 1
            break
    e = len(path) - 1
    for i in range(min(s, len(path) - 1), len(path)):
        if path[i] >= 1 - TRANSITION_EDGE:
            e = i
            break
    s, e = ia + min(s, e), ia + e
    start, end = times[s], times[e]
    return {"start": round(start, 3), "end": round(end, 3), "mid": round((start + end) / 2, 3),
            "hard": e - s <= 1, "change": round(peak, 1)}
