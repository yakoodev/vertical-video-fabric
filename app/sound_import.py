"""Звуковой эффект по ссылке владельца → «Файл для монтажа» (kind audio).

Пак freesound — закреплённый набор; когда владельцу нужен конкретный звук
(«вот этот бум», «вот этот смех»), он даёт ссылку. Страница звука freesound.org —
берём HQ-превью с её CDN (лицензию пишем в описание); всё остальное — yt-dlp,
только звук. Эффект — короткий: режем тишину в начале и обрезаем до
MAX_SOUND_SEC с мягким хвостом. Громкость под голос клипа ставит рендер
(motion_render.effect_gain_db), поэтому файл не нормализуем.

Только по ссылке, которую дал владелец: агент сам звуки в интернете не ищет.
"""

from __future__ import annotations

import html
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from app.settings import settings

MAX_SOUND_SEC = 6.0
MAX_SOURCE_SEC = 15 * 60  # yt-dlp: ролики длиннее не трогаем (это не звук, а фильм)
MAX_PREVIEW_BYTES = 8 * 1024 * 1024
USER_AGENT = "vertical-video-fabric/1 (+sound import)"
FREESOUND_PAGE = re.compile(r"^https?://(?:www\.)?freesound\.org/(?:people/[^/]+/sounds/(\d+)|s/(\d+))/?", re.I)


def _http_get(url: str, max_bytes: int) -> bytes:
    if urlsplit(url).scheme != "https":
        raise ValueError(f"только https: {url[:80]}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 - схема проверена выше
        data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("файл звука слишком большой")
    return data


def _license_of(page: str) -> str:
    if "creativecommons.org/publicdomain/zero" in page:
        return "CC0"
    m = re.search(r"creativecommons\.org/licenses/([a-z-]+)/", page)
    return f"CC {m.group(1).upper()}" if m else ""


def freesound_preview(url: str) -> dict:
    """Страница звука freesound → {preview, title, license, sound_id}."""
    m = FREESOUND_PAGE.match(url)
    if not m:
        raise ValueError("это не страница звука freesound.org")
    sound_id = m.group(1) or m.group(2)
    page = _http_get(url if url.startswith("https") else "https" + url[4:], 4 * 1024 * 1024).decode("utf-8", "replace")
    # На странице есть и «похожие звуки» — берём превью именно этого id.
    pm = re.search(r'data-mp3="(https://cdn\.freesound\.org/previews/\d+/' + sound_id + r'_\d+-[lh]q\.mp3)"', page)
    if not pm:
        raise ValueError("на странице freesound не нашлось превью звука")
    # data-title — в том же плеере, сразу за data-mp3.
    title = re.search(r'data-title="([^"]*)"', page[pm.start():pm.start() + 3000])
    return {
        "sound_id": sound_id,
        "preview": pm.group(1).replace("-lq.mp3", "-hq.mp3"),
        "title": html.unescape(title.group(1) if title else "").strip(),
        "license": _license_of(page),
    }


def build_ytdlp_sound_args(url: str, output_template: Path, start: float = 0.0) -> list[str]:
    end = start + MAX_SOUND_SEC + 1.0
    return [
        "yt-dlp", "--no-playlist", "-f", "bestaudio/best", "-x", "--audio-format", "mp3",
        "--match-filter", f"duration < {MAX_SOURCE_SEC}", "--download-sections", f"*{start:.2f}-{end:.2f}",
        "--print", "after_move:%(title)s", "--no-simulate", "-o", str(output_template), url,
    ]


def trim_args(src: Path, dest: Path, start: float = 0.0) -> list[str]:
    """Тишина в начале долой, не длиннее MAX_SOUND_SEC, короткое затухание в конце, без обложки."""
    fade = 0.12
    chain = (
        "silenceremove=start_periods=1:start_threshold=-45dB,"
        f"atrim=0:{MAX_SOUND_SEC:.2f},"
        f"areverse,afade=t=in:d={fade},areverse"
    )
    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    if start > 0:
        args += ["-ss", f"{start:.3f}"]
    return [*args, "-i", str(src), "-vn", "-af", chain, "-ar", "44100", "-c:a", "libmp3lame", "-q:a", "2", str(dest)]


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-.")[:50] or "sound"


def import_sound_from_url(store, url: str, label: str = "", start: float = 0.0) -> dict:
    """Скачать звук по ссылке, обрезать до MAX_SOUND_SEC и завести «Файл для монтажа» (audio)."""
    from app.montage_assets import assets_dir, probe_asset

    url = (url or "").strip()
    if urlsplit(url).scheme not in ("http", "https"):
        raise ValueError("нужна ссылка http(s) на звук (страница freesound.org или видео)")
    start = max(0.0, float(start or 0))
    work = settings.tmp_dir / f"sound-{uuid4().hex}"
    work.mkdir(parents=True, exist_ok=True)
    try:
        description = f"Звук по ссылке: {url}"
        if FREESOUND_PAGE.match(url):
            info = freesound_preview(url)
            raw = work / "preview.mp3"
            raw.write_bytes(_http_get(info["preview"], MAX_PREVIEW_BYTES))
            title = info["title"]
            description += f" · лицензия: {info['license'] or 'не указана'}"
            cut_start = start
        else:
            proc = subprocess.run(build_ytdlp_sound_args(url, work / "dl.%(ext)s", start),
                                  capture_output=True, text=True, timeout=10 * 60, check=False)
            files = sorted(work.glob("dl.*"))
            if proc.returncode != 0 or not files:
                tail = [line for line in (proc.stderr or proc.stdout or "").splitlines() if line.strip()][-2:]
                raise ValueError("не удалось скачать звук: " + (" ".join(tail)[:300] or "yt-dlp ничего не скачал"))
            raw = files[0]
            title = next((line.strip() for line in (proc.stdout or "").splitlines() if line.strip()), "")
            cut_start = 0.0  # yt-dlp уже скачал с нужной секунды
        name = f"{_slug(label or title or 'sound')}.mp3"
        dest = assets_dir() / f"{uuid4().hex}-{name}"
        proc = subprocess.run(trim_args(raw, dest, cut_start), capture_output=True, text=True, timeout=120, check=False)
        meta = probe_asset(dest) if dest.exists() else {}
        if proc.returncode != 0 or not meta.get("has_audio") or not meta.get("duration_sec"):
            dest.unlink(missing_ok=True)
            raise ValueError("в скачанном нет звука" + (f": {proc.stderr.strip()[:200]}" if proc.stderr else ""))
        return store.create_montage_asset(
            kind="audio",
            label=(label or title or "Звук по ссылке").strip()[:120],
            description=description[:500],
            tags="звук",
            file_path=str(dest),
            original_filename=name,
            mime_type="audio/mpeg",
            size_bytes=dest.stat().st_size,
            **{**meta, "width": 0, "height": 0},
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)
