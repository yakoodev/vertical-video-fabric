"""Картинки для списков: источник, клип и файл библиотеки вместо ``<video preload>``.

Каждое видео в списке браузер открывал отдельно и тянул мегабайты, а через домен у него всего
6 соединений: страница ждала, пока прогрузятся чужие видео (замер 05.11: «Файлы для монтажа» —
141 запрос и 38 МБ, «Проекты» и «Клипы» — по 180 запросов). Кадр делается один раз и лежит на диске.
"""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path

from app.settings import settings

THUMB_WIDTH = 480
# Файл меньше этого отдаём как есть: уменьшать незачем.
KEEP_BYTES = 200 * 1024
# Одновременно не больше двух ffmpeg: первый заход на страницу не должен мешать рендеру.
_slots = threading.BoundedSemaphore(2)


def _ffmpeg_still(src: Path, out: Path, at: float, *encode: str) -> bool:
    tmp = out.with_name(f"{out.stem}.tmp{out.suffix}")
    tmp.unlink(missing_ok=True)
    seek = ["-ss", f"{at:.3f}"] if at > 0 else []
    done = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", *seek, "-i", str(src), "-frames:v", "1",
         "-vf", f"scale='min({THUMB_WIDTH},iw)':-2", *encode, str(tmp)],
        capture_output=True, timeout=30, check=False,
    )
    if done.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        return False
    tmp.replace(out)
    return True


def _cached(src: Path, folder: Path, stem: str, at: float, *, alpha: bool = False) -> Path:
    """Кадр из ``src``: имя с mtime файла, заменили файл — будет новый кадр."""
    if not src.exists():
        raise ValueError("file missing")
    stat = src.stat()
    stem = f"{stem}_{stat.st_mtime_ns}_{stat.st_size}"
    # Прозрачность наклеек нужна и в превью, поэтому не JPEG.
    formats = ((".webp", ("-c:v", "libwebp", "-q:v", "75")), (".png", ())) if alpha else ((".jpg", ("-q:v", "5")),)
    for suffix, _ in formats:
        if (folder / f"{stem}{suffix}").exists():
            return folder / f"{stem}{suffix}"
    folder.mkdir(parents=True, exist_ok=True)
    with _slots:
        for suffix, encode in formats:
            out = folder / f"{stem}{suffix}"
            if out.exists() or _ffmpeg_still(src, out, at, *encode):
                return out
    raise ValueError("ffmpeg produced no frame")


def source_thumb(source: dict) -> Path:
    """Кадр источника. Лежит в его папке раскадровки: удалится вместе с источником."""
    from app.storyboard import storyboard_dir

    raw = str(source.get("local_path") or "")
    if not raw:
        raise ValueError("source has no playable file")
    duration = float(source.get("duration_sec") or 0)
    # Начало стрима — чаще всего заставка, берём кадр подальше.
    at = duration * 0.1 if duration > 0 else 0.0
    return _cached(Path(raw), storyboard_dir(int(source["id"])), "thumb", at)


def clip_thumb(clip: dict, cover_path: str | None = None) -> Path:
    """Обложка клипа, уменьшенная; обложки нет — кадр с начала ролика."""
    folder = settings.storyboard_dir / "thumbs" / "clips"
    if cover_path and Path(cover_path).exists():
        return _cached(Path(cover_path), folder, f"{int(clip['id'])}_cover", 0.0)
    raw = str(clip.get("output_path") or "")
    if not raw:
        raise ValueError("clip has no file")
    return _cached(Path(raw), folder, str(int(clip["id"])), 0.5)


def asset_thumb(asset: dict) -> Path:
    """Файл библиотеки: звука нет, небольшая картинка — сама, остальное — уменьшенный кадр."""
    kind = str(asset.get("kind") or "")
    if kind == "audio":
        raise ValueError("audio has no picture")
    path = Path(str(asset.get("file_path") or ""))
    if not path.is_file():
        raise ValueError("file missing")
    if kind == "image" and path.stat().st_size <= KEEP_BYTES:
        return path
    folder = settings.storyboard_dir / "thumbs" / "assets"
    return _cached(path, folder, str(int(asset["id"])), 0.5 if kind == "video" else 0.0, alpha=kind != "video")
