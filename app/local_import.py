"""Окно локальной записи — новым источником проекта: ``import_local_window(path, start, end, project_id, title)``.

Пилот 17.11: нужный бой — 40 с из записи NVIDIA на 12 ГБ, лежащей на диске ПК. Целиком её не загрузить
(лимит загрузки, часы копирования), а «по ссылке» локальный файл не взять. Теперь — вырез окна прямо из
папки хоста, смонтированной в контейнер:

* **какие папки** — ``LOCAL_IMPORT_DIRS`` (через «;», ``{data}`` — папка данных); по умолчанию папка
  импорта ленты ``{data}/runtime/feed_import`` (на ПК владельца — ``D:\\vvf-data\\runtime\\feed_import``) и
  папка источников ``{data}/sources`` (``D:\\vvf-data\\sources``). Путь хоста Windows переводится в путь
  контейнера по таблице ``FEED_HOST_MOUNTS`` (app/feed_files.py), как у импорта ленты. Папка записей игры
  (``G:\\NVIDIA\\Apex Legends``) в контейнер НЕ смонтирована: либо положить запись в feed_import, либо
  смонтировать её (docker-compose.override.yml: ``G:/NVIDIA/Apex Legends:/host/apex:ro``) и добавить
  ``LOCAL_IMPORT_DIRS=…;/host/apex`` и ``FEED_HOST_MOUNTS=…;G:/NVIDIA/Apex Legends=/host/apex``;
* **как режем** — ``ffmpeg -ss start -to end -map 0:v:0 -map 0:a -c copy``: без перекодирования, секунды на
  гигабайт; ВСЕ звуковые дорожки (у NVIDIA — игра и микрофон отдельно) остаются отдельными. Копия режет по
  ключевому кадру: начало может быть раньше ``start`` на долю секунды (в ответе — настоящая длина).
  ``exact: true`` — картинка перекодируется (кадр в кадр), звук всё равно копией;
* **что дальше** — файл ложится в папку источников, регистрируется обычным источником (ffprobe, хеш), с
  названием ``title`` и в проект ``project_id``; исходная запись не трогается.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.settings import settings

DEFAULT_DIRS = "{data}/runtime/feed_import;{data}/sources"
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv", ".webm"}
MAX_WINDOW_SEC = 4 * 3600.0
MIN_WINDOW_SEC = 0.5
MAX_BYTES = 64 * 2 ** 30  # окно 4K-записи на час — десятки ГБ; потолок, чтобы не забить диск случайно
MOUNT_HINT = ("папка не смонтирована в контейнер: положи запись в D:\\vvf-data\\runtime\\feed_import (в контейнере "
              "/data/runtime/feed_import) или смонтируй папку записей (docker-compose.override.yml + LOCAL_IMPORT_DIRS "
              "и FEED_HOST_MOUNTS, см. app/local_import.py)")


class LocalImportError(ValueError):
    """Путь, окно или файл не подходят — понятная причина для агента и человека."""


def allowed_dirs() -> list[Path]:
    raw = os.getenv("LOCAL_IMPORT_DIRS", "").strip() or DEFAULT_DIRS
    out = []
    for part in raw.split(";"):
        part = part.strip()
        if part:
            out.append(Path(part.replace("{data}", str(settings.data_dir))).resolve())
    return out


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def resolve_path(value: str) -> Path:
    """Путь хоста (``D:\\vvf-data\\…``) или контейнера → файл внутри разрешённой папки. ``..`` и ссылки раскрываются
    до проверки."""
    from app.feed_files import FeedFileError, container_path_of_host

    text = str(value or "").strip().strip('"')
    if not text:
        raise LocalImportError("нужен path — файл записи в разрешённой папке")
    roots = allowed_dirs()
    direct = Path(text)
    resolved: Path | None = None
    if direct.is_absolute():
        candidate = direct.resolve(strict=False)
        if any(_inside(candidate, r) for r in roots):
            resolved = candidate  # путь контейнера (или сервис на той же машине)
    if resolved is None:
        try:
            resolved = container_path_of_host(text).resolve(strict=False)
        except FeedFileError as exc:
            raise LocalImportError(f"{exc}. {MOUNT_HINT}") from exc
    if not any(_inside(resolved, r) for r in roots):
        raise LocalImportError("импорт только из папок " + ", ".join(str(r) for r in roots) + f". {MOUNT_HINT}")
    if resolved.suffix.lower() not in VIDEO_EXTENSIONS:
        raise LocalImportError(f"не видеофайл: {resolved.name} (нужно {', '.join(sorted(VIDEO_EXTENSIONS))})")
    if not resolved.is_file():
        raise LocalImportError(f"файла нет: {resolved}")
    return resolved


def probe_streams(path: Path) -> dict:
    """Длина и потоки записи (ffprobe): ``{duration, video: n, audio: n}``."""
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    if proc.returncode != 0:
        raise LocalImportError(f"ffprobe не прочитал файл: {(proc.stderr or '').strip()[:200]}")
    data = json.loads(proc.stdout or "{}")
    kinds = [s.get("codec_type") for s in data.get("streams") or []]
    try:
        duration = float((data.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    return {"duration": duration, "video": kinds.count("video"), "audio": kinds.count("audio")}


def _seconds(value: Any, name: str) -> float:
    """Секунды числом или «ЧЧ:ММ:СС(.мс)» / «ММ:СС»."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value or "").strip().replace(",", ".")
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return float(text)
    m = re.fullmatch(r"(?:(\d+):)?(\d{1,2}):(\d{1,2}(?:\.\d+)?)", text)
    if not m:
        raise LocalImportError(f"{name} — секунды (число) или ЧЧ:ММ:СС")
    return int(m.group(1) or 0) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def window_args(src: Path, out: Path, start: float, end: float, *, exact: bool = False) -> list[str]:
    """ffmpeg: окно ``start..end`` записи — все звуковые дорожки отдельными потоками, без перекодирования звука."""
    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
            "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(src),
            "-map", "0:v:0", "-map", "0:a?", "-map_metadata", "0"]
    if exact:
        args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "17", "-pix_fmt", "yuv420p", "-c:a", "copy"]
    else:
        args += ["-c", "copy", "-avoid_negative_ts", "make_zero"]
    if out.suffix.lower() in (".mp4", ".mov", ".m4v"):
        args += ["-movflags", "+faststart"]
    return args + [str(out)]


def _target(src: Path, title: str) -> Path:
    stem = re.sub(r"[^\w\-]+", "_", title or src.stem, flags=re.UNICODE).strip("_")[:60] or "window"
    ext = src.suffix.lower() if src.suffix.lower() in (".mp4", ".mkv", ".mov") else ".mkv"
    folder = Path(settings.source_dir)
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"local-{stem}-{uuid4().hex[:8]}{ext}"


def import_local_window(store: Any, path: str, start: Any, end: Any, project_id: Any = None, title: str = "",
                        exact: bool = False, ingestor: Any = None) -> dict:
    """Вырезать окно записи в новый источник (см. модуль). Вернуть ``{source, window, streams, note}``."""
    src = resolve_path(path)
    a, b = _seconds(start, "start"), _seconds(end, "end")
    if a < 0 or b <= a:
        raise LocalImportError("нужно 0 ≤ start < end (секунды записи)")
    if b - a < MIN_WINDOW_SEC:
        raise LocalImportError(f"окно короче {MIN_WINDOW_SEC:g} с")
    if b - a > MAX_WINDOW_SEC:
        raise LocalImportError(f"окно длиннее {MAX_WINDOW_SEC / 3600:g} ч — дроби на части")
    info = probe_streams(src)
    if info["video"] < 1:
        raise LocalImportError("в файле нет видео")
    if info["duration"] and a >= info["duration"]:
        raise LocalImportError(f"start {a:.1f} с за концом записи ({info['duration']:.1f} с)")
    if info["duration"]:
        b = min(b, info["duration"])
    if project_id not in (None, ""):
        from app.projects_core import get_project

        get_project(store, int(project_id), include_sources=False)  # нет проекта — ошибка до вырезки
    out = _target(src, title)
    proc = subprocess.run(window_args(src, out, a, b, exact=exact), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=60 * 60)
    if proc.returncode != 0 or not out.is_file() or out.stat().st_size == 0:
        out.unlink(missing_ok=True)
        raise LocalImportError(f"ffmpeg не вырезал окно: {(proc.stderr or '').strip()[-300:]}")
    got = probe_streams(out)
    if ingestor is None:
        from app.ingest import SourceIngestor

        ingestor = SourceIngestor(store)
    label = (title or "").strip() or f"{src.stem} {a:.0f}–{b:.0f} с"
    source = ingestor.ingest_local_file(
        out, source_type="upload", original_filename=label, max_bytes=MAX_BYTES,
        extra_metadata={"local_window": {"from": str(src), "start": round(a, 3), "end": round(b, 3),
                                         "exact": bool(exact)}})
    if project_id not in (None, ""):
        from app.projects_core import set_source_project

        set_source_project(store, int(source["id"]), int(project_id))
        source = store.get_source(int(source["id"]))
    note = (f"окно {a:.2f}–{b:.2f} с записи {src.name} → источник #{source['id']}: дорожек звука "
            f"{got['audio']} (как в записи — {info['audio']}), длина {got['duration']:.2f} с")
    if not exact:
        note += "; без перекодирования — начало по ключевому кадру (может быть чуть раньше start)"
    return {"ok": source.get("status") == "ready", "source_id": source["id"], "status": source.get("status"),
            "title": label, "project_id": source.get("project_id"), "path": str(out),
            "window": {"start": round(a, 3), "end": round(b, 3), "duration": round(got["duration"], 3)},
            "streams": {"video": got["video"], "audio": got["audio"], "source_audio": info["audio"]},
            "error": source.get("error") or "", "note": note}
