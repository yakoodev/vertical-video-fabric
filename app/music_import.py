"""Фоновая музыка по ссылке (YouTube и всё, что умеет yt-dlp) → дорожка сервиса.

Владелец берёт музыку для роликов с ютуба: раньше её надо было скачать самому и
загрузить файлом. Здесь — только звук, mp3, без плейлистов и не длиннее 15 минут.
Громкость под голос клипа выставляет рендер (render.music_volume_for).
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from app.settings import settings
from app.ytdlp_net import PRINT_TITLE_ARGS, title_from_output

MAX_TRACK_SEC = 15 * 60


def build_ytdlp_audio_args(url: str, output_template: Path) -> list[str]:
    return [
        "yt-dlp", "--no-playlist", "-f", "bestaudio/best", "-x", "--audio-format", "mp3",
        "--audio-quality", "2", "--match-filter", f"duration < {MAX_TRACK_SEC}",
        *PRINT_TITLE_ARGS, "--no-simulate", "-o", str(output_template), url,
    ]


def import_music_from_url(store, url: str, label: str = "") -> dict:
    """Скачать звук по ссылке и завести дорожку фоновой музыки."""
    from app.ingest import probe_media
    from app.proxy_config import download_proxy
    from app.ytdlp_net import is_youtube_url, plain_runner, run_routes

    url = (url or "").strip()
    if urlsplit(url).scheme not in ("http", "https"):
        raise ValueError("нужна ссылка http(s) на видео или трек")
    settings.audio_dir.mkdir(parents=True, exist_ok=True)
    token = uuid4().hex

    def clean() -> None:
        for leftover in settings.audio_dir.glob(f"{token}.*"):
            leftover.unlink(missing_ok=True)

    # Напрямую → прокси сервиса → аккаунт для скачивания (как источники, app/ytdlp_net.py).
    routes = run_routes(plain_runner(60 * 15), build_ytdlp_audio_args(url, settings.audio_dir / f"{token}.%(ext)s")[1:],
                        url=url, proxy=download_proxy(), before_retry=clean)
    proc = routes.proc
    files = sorted(settings.audio_dir.glob(f"{token}.*"))
    if proc.returncode != 0 or not files:
        for f in files:
            f.unlink(missing_ok=True)
        tail = [line for line in (proc.stderr or proc.stdout or "").splitlines() if line.strip()][-2:]
        hint = routes.hint("YouTube" if is_youtube_url(url) else "")
        raise ValueError("не удалось скачать звук: " + (f"{hint} — " if hint else "")
                         + (" ".join(tail)[:300] or "yt-dlp ничего не скачал"
                            f" (трек длиннее {MAX_TRACK_SEC // 60} мин?)"))
    path = files[0]
    title = title_from_output(proc.stdout)
    duration = probe_media(path).duration_sec
    track = store.create_audio_track(
        (label or title or "Музыка по ссылке").strip()[:120], path,
        original_filename=f"{(title or token)[:80]}.mp3", mime_type="audio/mpeg",
        duration_sec=duration, volume=0.5,
    )
    # Где у трека энергия: агент сразу видит, с какой секунды ставить (set_music start_sec).
    from app.music_energy import mark_track

    return mark_track(store, track)
