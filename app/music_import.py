"""Фоновая музыка по ссылке (YouTube и всё, что умеет yt-dlp) → дорожка сервиса.

Владелец берёт музыку для роликов с ютуба: раньше её надо было скачать самому и
загрузить файлом. Здесь — только звук, mp3, без плейлистов и не длиннее 15 минут.
Громкость под голос клипа выставляет рендер (render.music_volume_for).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from app.settings import settings

MAX_TRACK_SEC = 15 * 60


def build_ytdlp_audio_args(url: str, output_template: Path) -> list[str]:
    return [
        "yt-dlp", "--no-playlist", "-f", "bestaudio/best", "-x", "--audio-format", "mp3",
        "--audio-quality", "2", "--match-filter", f"duration < {MAX_TRACK_SEC}",
        "--print", "after_move:%(title)s", "--no-simulate", "-o", str(output_template), url,
    ]


def import_music_from_url(store, url: str, label: str = "") -> dict:
    """Скачать звук по ссылке и завести дорожку фоновой музыки."""
    from app.ingest import probe_media

    url = (url or "").strip()
    if urlsplit(url).scheme not in ("http", "https"):
        raise ValueError("нужна ссылка http(s) на видео или трек")
    settings.audio_dir.mkdir(parents=True, exist_ok=True)
    token = uuid4().hex
    proc = subprocess.run(
        build_ytdlp_audio_args(url, settings.audio_dir / f"{token}.%(ext)s"),
        capture_output=True, text=True, timeout=60 * 15, check=False,
    )
    files = sorted(settings.audio_dir.glob(f"{token}.*"))
    if proc.returncode != 0 or not files:
        for f in files:
            f.unlink(missing_ok=True)
        tail = [line for line in (proc.stderr or proc.stdout or "").splitlines() if line.strip()][-2:]
        raise ValueError("не удалось скачать звук: " + (" ".join(tail)[:300] or "yt-dlp ничего не скачал"
                         f" (трек длиннее {MAX_TRACK_SEC // 60} мин?)"))
    path = files[0]
    title = next((line.strip() for line in (proc.stdout or "").splitlines() if line.strip()), "")
    duration = probe_media(path).duration_sec
    return store.create_audio_track(
        (label or title or "Музыка по ссылке").strip()[:120], path,
        original_filename=f"{(title or token)[:80]}.mp3", mime_type="audio/mpeg",
        duration_sec=duration, volume=0.5,
    )
