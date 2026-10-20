"""Запись эфира Twitch-канала в источник — когда VOD не скачать.

Видео записей (VOD) Twitch отдаёт с CDN, который у части провайдеров недоступен,
а прямой эфир идёт с серверов самого Twitch — его можно писать. Запись идёт,
пока эфир не кончится или пока её не остановят («Остановить и сохранить»):
файл дособирается в mp4 и становится обычным источником. Чат подтягивается из
архива стрима со сдвигом — запись обычно начинается с середины эфира.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from app.settings import settings

CHANNEL_RE = re.compile(r"^[A-Za-z0-9_]{2,25}$")
# Эфир на экране зрителя (и в записи) отстаёт от «настоящего» времени стрима.
LIVE_LATENCY_SEC = 6.0
REPORT_EVERY_SEC = 10.0


def parse_channel(value: str) -> str:
    """«sareamura», «twitch.tv/sareamura», «https://www.twitch.tv/sareamura» → имя канала."""
    text = (value or "").strip().rstrip("/")
    m = re.search(r"twitch\.tv/([A-Za-z0-9_]+)", text)
    name = m.group(1) if m else text
    if not CHANNEL_RE.match(name) or name.lower() in {"videos", "directory"}:
        raise ValueError("нужно имя канала Twitch или ссылка на него")
    return name.lower()


def _ytdlp(*args: str, timeout: float = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "yt_dlp", *args], capture_output=True, text=True, timeout=timeout, errors="replace"
    )


def live_info(channel: str) -> dict | None:
    """Идёт ли эфир: {title, started_at (эпоха), stream_id} или None."""
    proc = _ytdlp("-J", "--skip-download", f"https://www.twitch.tv/{channel}")
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        return None
    if not data.get("is_live"):
        return None
    return {"title": data.get("description") or data.get("title") or "", "started_at": data.get("timestamp"),
            "stream_id": data.get("id")}


def archive_vod_id(channel: str) -> str | None:
    """Номер архива (VOD) текущего/последнего стрима — по нему тянется чат."""
    proc = _ytdlp("--flat-playlist", "--playlist-end", "1", "--print", "id",
                  f"https://www.twitch.tv/{channel}/videos?filter=archives&sort=time")
    vid = (proc.stdout or "").strip().splitlines()
    return vid[0].lstrip("v") if proc.returncode == 0 and vid and vid[0].lstrip("v").isdigit() else None


def record(channel: str, out_path: Path, token: Any = None, on_progress: Callable[[str], None] | None = None) -> float:
    """Писать эфир в ``out_path`` (MPEG-TS), пока он идёт или пока ``token`` не отменят.

    Возвращает эпоху начала записи. Отмена — это «остановить и сохранить»:
    записанное остаётся. Без ограничения времени — стрим идёт часами."""
    command = [sys.executable, "-m", "yt_dlp", "-f", "best[height<=1080]/best", "--hls-use-mpegts", "--no-part",
               "--retries", "20", "--fragment-retries", "50", "-o", str(out_path), f"https://www.twitch.tv/{channel}"]
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
    started = time.time()
    if token is not None:
        token.attach(proc)
    stop = threading.Event()

    def report() -> None:
        while not stop.wait(REPORT_EVERY_SEC):
            if on_progress and out_path.exists():
                size = out_path.stat().st_size / 1024 ** 3
                mins = int(time.time() - started) // 60
                on_progress(f"● Запись эфира {mins // 60}:{mins % 60:02d} · {size:.2f} ГБ")

    reporter = threading.Thread(target=report, daemon=True)
    reporter.start()
    tail: list[str] = []
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            if line.strip():
                tail = (tail + [line.strip()])[-20:]
    finally:
        proc.wait()
        stop.set()
        if token is not None:
            token.detach()
    cancelled = bool(token is not None and getattr(token, "cancelled", False))
    if not out_path.exists() or out_path.stat().st_size < 1024 * 1024:
        raise RuntimeError("эфир не записался: " + " | ".join(tail[-3:]) if not cancelled else "запись остановлена до начала")
    return started


def finalize(ts_path: Path) -> Path:
    """MPEG-TS записи → mp4 с перемоткой (без перекодирования)."""
    mp4 = ts_path.with_suffix(".mp4")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-fflags", "+genpts", "-i", str(ts_path), "-map", "0:v:0", "-map", "0:a:0?",
         "-c", "copy", "-bsf:a", "aac_adtstoasc", "-movflags", "+faststart", str(mp4)],
        check=True, timeout=3600,
    )
    ts_path.unlink(missing_ok=True)
    return mp4


def record_to_source(
    ingestor: Any,
    channel: str,
    token: Any = None,
    on_progress: Callable[[str], None] | None = None,
    on_recorded: Callable[[dict, str | None, float], None] | None = None,
) -> dict:
    """Записать эфир канала и создать из записи источник.

    ``on_recorded(source, vod_id, vod_start_sec)`` — подтянуть чат архива со сдвигом
    (с какой секунды стрима началась запись)."""
    from app.ingest import StoredSourceFile, file_hash_and_size

    channel = parse_channel(channel)
    info = live_info(channel)
    if not info:
        raise ValueError(f"{channel} сейчас не в эфире")
    settings.source_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{uuid4().hex}-twitch-live-{channel}-{time.strftime('%Y%m%d-%H%M')}"
    ts_path = settings.source_dir / f"{stem}.ts"
    started = record(channel, ts_path, token, on_progress)
    if on_progress:
        on_progress("Собираю запись в mp4…")
    mp4 = finalize(ts_path)
    sha256, size = file_hash_and_size(mp4)
    stored = StoredSourceFile(mp4, f"twitch-live-{channel}-{time.strftime('%Y%m%d-%H%M')}.mp4", sha256, size)
    source = ingestor._create_source_from_file(
        source_type="twitch_url", stored=stored, original_url=f"https://www.twitch.tv/{channel}"
    )
    vod_start = 0.0
    if info.get("started_at"):
        vod_start = max(0.0, started - float(info["started_at"]) - LIVE_LATENCY_SEC)
    if on_recorded and source.get("status") != "failed":
        try:
            on_recorded(source, archive_vod_id(channel), round(vod_start, 1))
        except Exception:  # noqa: BLE001 - чат не должен ломать запись
            pass
    return source
