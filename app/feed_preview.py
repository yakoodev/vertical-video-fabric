"""Лёгкое превью и кадр-обложка вариантов «Ленты» (app/feed.py) — фоном, по одному, в очереди тяжёлых задач.

Владелец смотрит ленту с телефона за границей, ~150 КБ/с: полный фильм на 700 МБ ему не открыть. Поэтому у каждого
варианта — копия ≤720p по короткой стороне, ~1.5 Мбит/с, ≤30 к/с, AAC 96k, ``+faststart`` (играет, пока качается),
и кадр JPEG ≤640 по длинной стороне. Файл и так лёгкий (H.264 в mp4, ≤720p, ≤2.2 Мбит/с) — копией служит он сам.

Очередь — сама таблица ``feed_variants.preview_status``: queued → running → ready / failed. Один поток-работник
на процесс (``kick`` будит его после каждой новой версии); перезапуск сервиса возвращает running в queued
(``recover``). ``FEED_PREVIEW_WORKER=0`` — без фона (тесты зовут ``process_pending`` сами). Оригинал не меняется.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
from pathlib import Path
from typing import Any

from app import feed_files as F

log = logging.getLogger(__name__)

SHORT_SIDE = 720
VIDEO_KBPS = int(os.getenv("FEED_PREVIEW_KBPS", "1400") or 1400)
AUDIO_KBPS = 96
MAX_FPS = 30
# Файл, который не нужно перекодировать: уже лёгкий.
LIGHT_MAX_KBPS = 2200
POSTER_LONG_SIDE = 640
TIMEOUT_SEC = int(os.getenv("FEED_PREVIEW_TIMEOUT_SECONDS", str(3 * 60 * 60)) or 0) or None

_lock = threading.Lock()
_wake = threading.Event()
_thread: threading.Thread | None = None


def worker_enabled() -> bool:
    return os.getenv("FEED_PREVIEW_WORKER", "1").strip().lower() not in ("0", "false", "no", "off")


def recover(store: Any) -> int:
    """Старт сервиса: прерванные сборки — снова в очередь."""
    cur = store.db.execute("UPDATE feed_variants SET preview_status = 'queued' WHERE preview_status = 'running'")
    return int(cur.rowcount or 0)


def kick(store: Any) -> None:
    """Разбудить работника (и запустить, если его нет)."""
    global _thread
    if not worker_enabled():
        return
    with _lock:
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_loop, args=(store,), name="feed-preview", daemon=True)
            _thread.start()
    _wake.set()


def _loop(store: Any) -> None:
    while True:
        try:
            done = process_pending(store, limit=1)
        except Exception:  # noqa: BLE001 - работник не должен умирать
            log.exception("feed preview pass failed")
            done = 0
        if not done:
            _wake.wait(300)
            _wake.clear()


def _claim(store: Any) -> dict | None:
    with store.db.transaction():
        row = store.db.query_one("SELECT * FROM feed_variants WHERE preview_status = 'queued' ORDER BY id LIMIT 1")
        if not row:
            return None
        store.db.execute("UPDATE feed_variants SET preview_status = 'running', preview_error = '' WHERE id = ?",
                         (int(row["id"]),))
    return dict(row)


def process_pending(store: Any, limit: int | None = None) -> int:
    """Собрать превью вариантов из очереди (не больше ``limit``). Возвращает, сколько обработано."""
    count = 0
    while limit is None or count < limit:
        variant = _claim(store)
        if variant is None:
            break
        build(store, variant)
        count += 1
    return count


def requeue(store: Any, variant_id: int) -> None:
    store.db.execute("UPDATE feed_variants SET preview_status = 'queued', preview_error = '' WHERE id = ?",
                     (int(variant_id),))
    kick(store)


def _probe(path: Path) -> dict:
    proc = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
                           str(path)], capture_output=True, text=True, timeout=120, check=False)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or "ffprobe failed").strip()[-300:])
    data = json.loads(proc.stdout or "{}")
    video = next((s for s in data.get("streams") or [] if s.get("codec_type") == "video"), {})
    audio = next((s for s in data.get("streams") or [] if s.get("codec_type") == "audio"), None)
    fmt = data.get("format") or {}
    duration = float(fmt.get("duration") or video.get("duration") or 0)
    size = int(fmt.get("size") or path.stat().st_size)
    return {"codec": video.get("codec_name") or "", "width": int(video.get("width") or 0),
            "height": int(video.get("height") or 0), "duration": duration, "has_audio": audio is not None,
            "kbps": (size * 8 / 1000 / duration) if duration > 0 else 0.0}


def is_light(path: Path, info: dict) -> bool:
    short = min(info["width"], info["height"])
    return (path.suffix.lower() in (".mp4", ".m4v") and info["codec"] == "h264" and 0 < short <= SHORT_SIDE
            and 0 < info["kbps"] <= LIGHT_MAX_KBPS)


def _scale_filter(width: int, height: int) -> str:
    if height >= width:  # вертикаль: ширина — короткая сторона
        target = min(SHORT_SIDE, width - width % 2)
        return f"scale={target}:-2"
    target = min(SHORT_SIDE, height - height % 2)
    return f"scale=-2:{target}"


def _encode(src: Path, dest: Path, info: dict) -> None:
    part = dest.with_name(dest.name + ".part.mp4")
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-map", "0:v:0"]
    if info["has_audio"]:
        cmd += ["-map", "0:a:0"]
    cmd += ["-vf", f"{_scale_filter(info['width'], info['height'])},format=yuv420p", "-fpsmax", str(MAX_FPS),
            "-c:v", "libx264", "-preset", "veryfast", "-profile:v", "main", "-b:v", f"{VIDEO_KBPS}k",
            "-maxrate", f"{int(VIDEO_KBPS * 1.25)}k", "-bufsize", f"{VIDEO_KBPS * 2}k", "-threads", "2"]
    if info["has_audio"]:
        cmd += ["-c:a", "aac", "-b:a", f"{AUDIO_KBPS}k", "-ac", "2"]
    cmd += ["-movflags", "+faststart", str(part)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_SEC, check=False)
    if proc.returncode != 0 or not part.exists():
        part.unlink(missing_ok=True)
        raise RuntimeError((proc.stderr or "ffmpeg failed").strip()[-300:])
    os.replace(part, dest)


def _poster(src: Path, dest: Path, info: dict) -> None:
    at = min(max(info["duration"] * 0.1, 0.0), 20.0) if info["duration"] > 1 else 0.0
    long_side = (f"scale={POSTER_LONG_SIDE}:-2" if info["width"] >= info["height"]
                 else f"scale=-2:{POSTER_LONG_SIDE}")
    proc = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{at:.2f}", "-i", str(src),
                           "-frames:v", "1", "-vf", long_side, "-q:v", "4", str(dest)],
                          capture_output=True, text=True, timeout=120, check=False)
    if proc.returncode != 0 or not dest.exists():
        raise RuntimeError((proc.stderr or "ffmpeg poster failed").strip()[-300:])


def build(store: Any, variant: dict) -> dict:
    """Превью и кадр одного варианта. Ошибка — в ``preview_error`` (видна в ленте), не исключением."""
    from app.feed import variant_media_path
    from app.heavy_queue import heavy_slot

    variant_id = int(variant["id"])
    src = variant_media_path(store, variant)
    if src is None:
        store.db.execute("UPDATE feed_variants SET preview_status = 'failed', preview_error = ? WHERE id = ?",
                         ("файла варианта нет на диске", variant_id))
        return {"ok": False}
    version = store.db.query_one("SELECT work_id FROM feed_versions WHERE id = ?", (int(variant["version_id"]),))
    folder = F.version_dir(int(version["work_id"]), int(variant["version_id"])) if version else F.tmp_dir()
    preview = folder / f"{variant_id}.preview.mp4"
    poster = folder / f"{variant_id}.poster.jpg"
    try:
        info = _probe(src)
        with heavy_slot("feed_preview", f"лента: вариант {variant_id}"):
            if is_light(src, info):
                preview_path = src
            else:
                _encode(src, preview, info)
                preview_path = preview
            _poster(preview_path, poster, {**info, **(_probe(preview_path) if preview_path != src else {})})
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as exc:
        store.db.execute("UPDATE feed_variants SET preview_status = 'failed', preview_error = ? WHERE id = ?",
                         (str(exc)[:300] or "ошибка ffmpeg", variant_id))
        log.warning("feed preview %s failed: %s", variant_id, exc)
        return {"ok": False, "error": str(exc)[:300]}
    store.db.execute(
        "UPDATE feed_variants SET preview_status = 'ready', preview_path = ?, poster_path = ?, preview_error = '', "
        "duration_sec = COALESCE(duration_sec, ?), width = COALESCE(width, ?), height = COALESCE(height, ?) "
        "WHERE id = ?",
        (str(preview_path), str(poster), info["duration"], info["width"], info["height"], variant_id))
    return {"ok": True, "light": preview_path == src}
