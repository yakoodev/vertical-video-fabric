"""Кадрирование видеомема под кадр клипа: срезать вшитые чёрные поля (letterbox/pillarbox) и подрезать под формат.

* ``content_box(path)`` — где в кадре мема картинка без чёрных полей: ffmpeg ``cropdetect`` по трём кадрам (25/50/75 %
  длины), берётся самое широкое окно (тёмная сцена в одном кадре полей не прибавит). Кэш по файлу.
* ``face_center(path, box)`` — центр самого крупного лица в среднем кадре (детектор лиц app/autofocus.py: YuNet,
  иначе Haar); нет лица или детектора — центр окна.
* ``insert_framing(...)`` — окно вставки (доли кадра мема) и чем заполнить кадр: если до кадра клипа не хватает не
  больше ``MAX_CROP_LOSS`` (45 %) ширины или высоты — подрезка во весь кадр (``cover``) вокруг лица; иначе мем
  подрезается ровно на 45 % (так он крупнее) и вокруг — размытая копия (``blur``).

Окно уходит во вставку полем ``crop`` (app/montage_assets.normalize_crop) — рендер режет его до остального графа.
"""

from __future__ import annotations

import re
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

MAX_CROP_LOSS = 0.45
MIN_BAR = 0.02  # поле уже 2 % стороны — это поле, тоньше — шум кодека
_CROP_RE = re.compile(r"crop=(\d+):(\d+):(\d+):(\d+)")


def _duration(path: str) -> float:
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                             capture_output=True, text=True, timeout=30, check=False).stdout
        return float(out.strip() or 0)
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0.0


def _size(path: str) -> tuple[int, int]:
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                              "stream=width,height", "-of", "csv=p=0", path],
                             capture_output=True, text=True, timeout=30, check=False).stdout
        w, h = out.strip().split(",")[:2]
        return int(w), int(h)
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0, 0


def parse_cropdetect(log: str, width: int, height: int) -> dict | None:
    """Самое широкое окно из строк ``crop=w:h:x:y`` → доли кадра; полей нет (тоньше ``MIN_BAR``) — None."""
    boxes = [tuple(int(v) for v in m.groups()) for m in _CROP_RE.finditer(log)]
    boxes = [b for b in boxes if b[0] > 0 and b[1] > 0]
    if not boxes or not width or not height:
        return None
    x0 = min(b[2] for b in boxes)
    y0 = min(b[3] for b in boxes)
    x1 = max(b[2] + b[0] for b in boxes)
    y1 = max(b[3] + b[1] for b in boxes)
    left, top, right, bottom = x0 / width, y0 / height, 1 - x1 / width, 1 - y1 / height
    if max(left, right) < MIN_BAR and max(top, bottom) < MIN_BAR:
        return None
    if (x1 - x0) < width * 0.3 or (y1 - y0) < height * 0.3:
        return None  # почти весь кадр чёрный — это тёмная сцена, а не поля
    return {"x": round(x0 / width, 4), "y": round(y0 / height, 4),
            "w": round((x1 - x0) / width, 4), "h": round((y1 - y0) / height, 4)}


def content_box(path: str | Path) -> dict | None:
    try:
        st = Path(path).stat()
    except OSError:
        return None
    return _content_box(str(path), st.st_mtime_ns, st.st_size)


@lru_cache(maxsize=256)
def _content_box(path: str, _mtime: int, _size_b: int) -> dict | None:
    width, height = _size(path)
    length = _duration(path)
    logs = []
    for frac in (0.25, 0.5, 0.75):
        try:
            logs.append(subprocess.run(
                ["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{length * frac:.3f}", "-i", path, "-frames:v", "6",
                 "-vf", "cropdetect=limit=24:round=2:reset=0", "-f", "null", "-"],
                capture_output=True, text=True, timeout=30, check=False).stderr)
        except (OSError, subprocess.SubprocessError):
            continue
    return parse_cropdetect("\n".join(logs), width, height)


def face_center(path: str | Path, box: dict | None = None) -> tuple[float, float] | None:
    """Центр самого крупного лица в среднем кадре мема (доли всего кадра) или None."""
    try:
        import cv2
        import numpy as np

        from app.autofocus import _detect_faces
    except Exception:  # noqa: BLE001 - нет OpenCV/модели — без лица, по центру
        return None
    length = _duration(str(path))
    try:
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{length * 0.5:.3f}", "-i", str(path), "-frames:v", "1",
                              "-vf", "scale=320:-2", "-f", "image2pipe", "-vcodec", "png", "-"],
                             capture_output=True, timeout=30, check=False).stdout
        img = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR) if raw else None
    except Exception:  # noqa: BLE001
        img = None
    if img is None:
        return None
    h, w = img.shape[:2]
    faces = _detect_faces(img, cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
    if box:
        faces = [f for f in faces if box["x"] * w <= f[0] + f[2] / 2 <= (box["x"] + box["w"]) * w]
    if not faces:
        return None
    fx, fy, fw, fh = max(faces, key=lambda f: f[2] * f[3])
    return (fx + fw / 2) / w, (fy + fh / 2) / h


def framing(box: dict | None, src_w: int, src_h: int, out_w: int, out_h: int,
            face: tuple[float, float] | None = None, max_loss: float = MAX_CROP_LOSS) -> tuple[dict | None, str]:
    """Окно вставки и заполнение (``cover`` | ``blur``) — чистая функция (её проверяют тесты)."""
    box = box or {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
    if not src_w or not src_h or not out_w or not out_h:
        return (box if box != {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0} else None), "blur"
    a_in = box["w"] * src_w / (box["h"] * src_h)
    a_out = out_w / out_h
    cx, cy = face if face else (box["x"] + box["w"] / 2, box["y"] + box["h"] / 2)
    x, y, w, h = box["x"], box["y"], box["w"], box["h"]
    if a_in > a_out:  # мем шире кадра — режем ширину
        loss = 1 - a_out / a_in
        keep = 1 - min(loss, max_loss)
        nw = w * keep
        x = min(box["x"] + w - nw, max(box["x"], cx - nw / 2))
        w = nw
    else:  # мем уже/выше кадра — режем высоту (лицо — в верхней трети окна)
        loss = 1 - a_in / a_out
        keep = 1 - min(loss, max_loss)
        nh = h * keep
        y = min(box["y"] + h - nh, max(box["y"], cy - nh * 0.4))
        h = nh
    fill = "cover" if loss <= max_loss + 1e-6 else "blur"
    window = {"x": round(x, 4), "y": round(y, 4), "w": round(w, 4), "h": round(h, 4)}
    if window["w"] > 0.995 and window["h"] > 0.995:
        return None, fill
    return window, fill


def insert_framing(path: str | Path, out_w: int, out_h: int) -> dict:
    """Окно и заполнение для полнокадровой вставки мема ``path`` в кадр ``out_w``×``out_h``:
    ``{crop, fill, bars, face}`` (``crop`` None — весь кадр мема)."""
    box = content_box(path)
    src_w, src_h = _size(str(path))
    face = face_center(path, box)
    crop, fill = framing(box, src_w, src_h, out_w, out_h, face)
    return {"crop": crop, "fill": fill, "bars": box, "face": face}
