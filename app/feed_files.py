"""Файлы «Ленты» (app/feed.py): где лежат, какие пути с сервера можно взять, как путь хоста стал путём контейнера.

Три способа дать файл варианту, кроме клипа сервиса:

* **загрузка** потоком (``PUT /api/feed/versions/{id}/variants/upload``): тело запроса сразу пишется во временный
  файл ``<feed>/tmp`` и переносится в папку версии — фильм на 700 МБ не держится в памяти и не копируется дважды;
* **файл на сервере** (``path``): путь внутри папки данных сервиса (``DATA_DIR``, в контейнере ``/data``). Файл
  остаётся на месте, лента только ссылается на него и при удалении работы его не трогает;
* **импорт с хоста** (``host_path``): путь Windows вида ``D:\\vvf-data\\runtime\\feed_import\\x.mp4``. Он
  переводится в путь контейнера по таблице монтирования (``FEED_HOST_MOUNTS``) и принимается, только если лежит в
  папке импорта (``FEED_IMPORT_DIRS``, по умолчанию ``<data>/runtime/feed_import``). Файл переносится в папку ленты:
  папка импорта — только перевалочная.

Принимаются только видеофайлы (расширение и ffprobe). Папка ленты — ``FEED_DIR``, по умолчанию
``<data>/runtime/feed`` (в проде — том на D:, рядом с папкой импорта, поэтому перенос — переименование).
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path, PurePosixPath
from uuid import uuid4

from app.settings import settings

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv", ".webm"}

# Хост → контейнер (docker-compose.override.yml владельца): D:/vvf-data/{sources,clips,runtime,montage_assets}
# смонтированы в /data/<то же имя>. {data} — папка данных сервиса.
DEFAULT_HOST_MOUNTS = ("D:/vvf-data/sources={data}/sources;D:/vvf-data/clips={data}/clips;"
                       "D:/vvf-data/runtime={data}/runtime;D:/vvf-data/montage_assets={data}/montage_assets")
DEFAULT_IMPORT_DIRS = "{data}/runtime/feed_import"


class FeedFileError(ValueError):
    """Путь не подходит: понятная причина для человека и агента."""


def feed_dir() -> Path:
    raw = os.getenv("FEED_DIR", "").strip()
    return Path(raw).resolve() if raw else (settings.runtime_dir / "feed").resolve()


def tmp_dir() -> Path:
    path = feed_dir() / "tmp"
    path.mkdir(parents=True, exist_ok=True)
    return path


def version_dir(work_id: int, version_id: int) -> Path:
    path = feed_dir() / f"work-{int(work_id)}" / f"version-{int(version_id)}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _expand(text: str) -> str:
    return text.replace("{data}", str(settings.data_dir))


def import_dirs() -> list[Path]:
    raw = os.getenv("FEED_IMPORT_DIRS", "").strip() or DEFAULT_IMPORT_DIRS
    return [Path(_expand(part.strip())).resolve() for part in raw.split(";") if part.strip()]


def _host_key(text: str) -> str:
    """Путь хоста для сравнения: прямые косые, без хвостовой, буква диска и регистр не важны (Windows)."""
    return text.strip().replace("\\", "/").rstrip("/").lower()


def host_mounts() -> list[tuple[str, Path]]:
    raw = os.getenv("FEED_HOST_MOUNTS", "").strip() or DEFAULT_HOST_MOUNTS
    out: list[tuple[str, Path]] = []
    for part in raw.split(";"):
        if "=" not in part:
            continue
        host, container = part.split("=", 1)
        if host.strip() and container.strip():
            out.append((_host_key(host), Path(_expand(container.strip()))))
    # Самое длинное совпадение первым: вложенные монтирования.
    return sorted(out, key=lambda item: len(item[0]), reverse=True)


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _check_video_file(path: Path) -> Path:
    if path.suffix.lower() not in VIDEO_EXTENSIONS:
        raise FeedFileError(f"не видеофайл: {path.name} (нужно {', '.join(sorted(VIDEO_EXTENSIONS))})")
    if not path.is_file():
        raise FeedFileError(f"файла нет: {path}")
    return path


def server_path(value: str) -> Path:
    """Файл на сервере внутри папки данных сервиса. Ссылки и ``..`` раскрываются до проверки."""
    text = str(value or "").strip()
    if not text:
        raise FeedFileError("нужен путь к файлу")
    path = Path(text)
    if not path.is_absolute():
        path = settings.data_dir / path
    resolved = path.resolve(strict=False)
    root = settings.data_dir.resolve()
    if not _inside(resolved, root):
        raise FeedFileError(f"файл должен лежать в папке данных сервиса ({root})")
    return _check_video_file(resolved)


def container_path_of_host(host_path: str) -> Path:
    """Путь Windows хоста → путь контейнера по таблице монтирования; путь контейнера возвращается как есть."""
    text = str(host_path or "").strip()
    if not text:
        raise FeedFileError("нужен путь к файлу")
    norm = text.replace("\\", "/").rstrip("/")
    git_bash = re.match(r"^/[A-Za-z]/", norm)
    if norm.startswith("/") and not git_bash:
        return Path(norm)  # уже путь контейнера
    if git_bash:  # /d/vvf-data/... (Git Bash) — тот же диск D:
        norm = f"{norm[1]}:{norm[2:]}"
    key = norm.lower()
    for host, container in host_mounts():
        if key == host or key.startswith(host + "/"):
            rest = norm[len(host):].lstrip("/")
            parts = PurePosixPath(rest).parts
            if any(part in ("..", ".") for part in parts):
                raise FeedFileError("в пути не должно быть «..»")
            return container.joinpath(*parts)
    raise FeedFileError("этот путь хоста не смонтирован в контейнер (FEED_HOST_MOUNTS): положите файл в "
                        "D:\\vvf-data\\runtime\\feed_import")


def import_path(host_path: str) -> Path:
    """Файл для импорта: путь хоста или контейнера, который лежит в одной из папок импорта."""
    allowed = import_dirs()
    direct = Path(str(host_path or "").strip())
    if direct.is_absolute() and any(_inside(direct.resolve(strict=False), root) for root in allowed):
        resolved = direct.resolve(strict=False)  # сервис на той же машине, что и файл (локальный запуск)
    else:
        resolved = container_path_of_host(host_path).resolve(strict=False)
    if not any(_inside(resolved, root) for root in allowed):
        raise FeedFileError("импорт только из папки " + " или ".join(str(r) for r in allowed))
    return _check_video_file(resolved)


def safe_name(name: str, fallback: str = "video.mp4") -> str:
    base = Path(str(name or "")).name.strip() or fallback
    base = re.sub(r"[^\w.\- ()]+", "_", base, flags=re.UNICODE)[:120].strip(" .") or fallback
    if Path(base).suffix.lower() not in VIDEO_EXTENSIONS:
        base += ".mp4"
    return base


def unique_target(folder: Path, name: str) -> Path:
    return folder / f"{uuid4().hex[:8]}-{safe_name(name)}"


def move_into(src: Path, dest: Path) -> Path:
    """Перенести файл в папку ленты: переименование, а между томами — копия и удаление исходника."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(src, dest)
    except OSError:
        shutil.copyfile(src, dest)
        try:
            src.unlink()
        except OSError:
            pass
    return dest


def remove_within_feed(*paths: str | Path | None) -> int:
    """Удалить файлы, только если они лежат в папке ленты. Ничего не бросает."""
    root = feed_dir()
    removed = 0
    for raw in paths:
        if not raw:
            continue
        try:
            path = Path(raw).resolve(strict=False)
            if _inside(path, root) and path.is_file():
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def inside_feed(path: str | Path) -> bool:
    return _inside(Path(path).resolve(strict=False), feed_dir())


def media_root_ok(path: str | Path) -> bool:
    """Файл можно отдавать: в папке ленты или в папке данных сервиса."""
    resolved = Path(path).resolve(strict=False)
    return _inside(resolved, feed_dir()) or _inside(resolved, settings.data_dir.resolve())
