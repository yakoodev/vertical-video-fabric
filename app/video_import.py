"""Отрезок видео по ссылке (YouTube и всё, что умеет yt-dlp) → «Файл для монтажа» (kind video).

Мему нужен кусок чужого ролика (припев клипа, реакция): раньше его скачивали целиком
руками, резали ffmpeg'ом и отдельно вынимали звук. Здесь — только отрезок
``start..end`` (yt-dlp ``--download-sections``, не дольше MAX_SECTION_SEC, не выше
1080p) тем же механизмом, что загрузка источников (app/ingest.py): yt-dlp через
``_run_ytdlp`` и, если напрямую не вышло, повтор через прокси сервиса
(``proxy_config.download_proxy``). Файл приводится к h264 + AAC со звуком — звук
слоя ``video`` включает ``props.audio`` (см. docs/AGENTS.md).

Только по ссылке владельца: агент сам ролики в интернете не ищет.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from app.settings import settings

MAX_SECTION_SEC = 180.0
MAX_HEIGHT = 1080
MIN_SECTION_SEC = 0.3
TITLE_MARK = "VVF-TITLE:"


def build_ytdlp_section_args(url: str, output_template: Path, start: float, end: float) -> list[str]:
    """yt-dlp: только отрезок start..end (точно по кадру), видео ≤ 1080p со звуком, mp4."""
    return [
        "--no-playlist",
        "-f", f"bv*[height<={MAX_HEIGHT}]+ba/b[height<={MAX_HEIGHT}]/b",
        "--download-sections", f"*{start:.3f}-{end:.3f}",
        # Без этого отрезок начинается с ближайшего ключевого кадра ДО start — from слоя уезжает.
        "--force-keyframes-at-cuts",
        "--merge-output-format", "mp4",
        "--socket-timeout", "20",
        "--retries", "5",
        "--newline",
        "--print", f"after_move:{TITLE_MARK}%(title)s",
        "--no-simulate",
        "-o", str(output_template),
        url,
    ]


def normalize_args(src: Path, dest: Path, length: float) -> list[str]:
    """ffmpeg: скачанный отрезок → h264 + AAC (≤ 1920 по стороне), ровно ``length`` секунд."""
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src), "-t", f"{length:.3f}",
            "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn",
            "-vf", "scale='min(1920,iw)':'min(1920,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(dest)]


def section_bounds(start: object, end: object) -> tuple[float, float]:
    """Проверить отрезок: start ≥ 0, end > start, длина 0.3..MAX_SECTION_SEC (ValueError — почему нет)."""
    try:
        a = float(start)  # type: ignore[arg-type]
        b = float(end)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError("нужны start и end — секунды ролика по ссылке") from exc
    a = max(0.0, a)
    if b - a < MIN_SECTION_SEC:
        raise ValueError(f"отрезок короче {MIN_SECTION_SEC:g} с — проверь start/end")
    if b - a > MAX_SECTION_SEC + 1e-6:
        raise ValueError(f"отрезок длиннее {MAX_SECTION_SEC:.0f} с — возьми нужное место (from у слоя — "
                         "секунда внутри отрезка)")
    return a, b


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-.")[:50] or "video"


def _download(url: str, template: Path, start: float, end: float):
    """yt-dlp тем же путём, что загрузка источников: напрямую, не вышло — через прокси сервиса."""
    from app.ingest import _run_ytdlp
    from app.proxy_config import download_proxy

    args = build_ytdlp_section_args(url, template, start, end)
    proc = _run_ytdlp(args)
    proxy = download_proxy()
    if proc.returncode != 0 and proxy:
        # Напрямую не вышло (CDN режет соединение) — повтор через прокси (настройки сервиса →
        # «Прокси», запасной — DOWNLOAD_PROXY_URL); из одинаковых по высоте — меньший битрейт.
        for leftover in template.parent.glob("dl.*"):
            leftover.unlink(missing_ok=True)
        proc = _run_ytdlp(["--proxy", proxy, "-S", "res,fps,+br", *args])
    return proc


def import_video_from_url(store, url: str, start: object, end: object, label: str = "") -> dict:
    """Скачать отрезок ролика по ссылке и завести «Файл для монтажа» (video, со звуком)."""
    from app.montage_assets import assets_dir, probe_asset
    from app.render import _run_ffmpeg

    url = (url or "").strip()
    if urlsplit(url).scheme not in ("http", "https"):
        raise ValueError("нужна ссылка http(s) на ролик")
    a, b = section_bounds(start, end)
    work = settings.tmp_dir / f"video-url-{uuid4().hex}"
    work.mkdir(parents=True, exist_ok=True)
    try:
        proc = _download(url, work / "dl.%(ext)s", a, b)
        files = sorted(f for f in work.glob("dl.*") if not f.name.endswith((".part", ".ytdl", ".temp")))
        output = proc.stdout or proc.stderr or ""
        if proc.returncode != 0 or not files:
            tail = [line for line in output.splitlines() if "ERROR" in line] or \
                   [line for line in output.splitlines() if line.strip()][-2:]
            raise ValueError("не удалось скачать отрезок: " + (" ".join(tail)[:300] or "yt-dlp ничего не скачал"))
        title = next((line.split(TITLE_MARK, 1)[1].strip() for line in output.splitlines() if TITLE_MARK in line), "")
        name = f"{_slug(label or title or 'video')}-{a:.0f}-{b:.0f}.mp4"
        dest = assets_dir() / f"{uuid4().hex}-{name}"
        try:
            _run_ffmpeg(normalize_args(files[0], dest, b - a), timeout=900)
        except Exception as exc:  # noqa: BLE001
            dest.unlink(missing_ok=True)
            raise ValueError(f"скачанный отрезок не перекодировался: {str(exc)[:200]}") from exc
        meta = probe_asset(dest) if dest.exists() else {}
        if not meta.get("width") or not meta.get("duration_sec"):
            dest.unlink(missing_ok=True)
            raise ValueError("в скачанном отрезке нет кадров")
        return store.create_montage_asset(
            kind="video",
            label=(label or f"{title or 'Видео по ссылке'} {a:.0f}–{b:.0f} с").strip()[:120],
            description=f"Отрезок {a:g}–{b:g} с по ссылке: {url}"[:500],
            tags="видео",
            file_path=str(dest),
            original_filename=name,
            mime_type="video/mp4",
            size_bytes=dest.stat().st_size,
            **meta,
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)
