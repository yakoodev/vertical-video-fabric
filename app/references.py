"""«Референсы» проекта: чужие ролики, картинки, звуки и страницы, на которые владелец хочет быть похожим.

Владелец кидает в проект пример (файл с телефона или ссылку TikTok/YouTube/Instagram/любую) и пишет, что взять:
заметка ``note`` свободным текстом плюс быстрые теги (``QUICK_TAGS``: темп, склейки, подписи…). ``pinned`` —
«главный» референс: его срез идёт в системный промпт чата проекта как пример стиля.

Строка ``project_references`` + папка ``<runtime>/references/ref-<id>`` (``REFERENCES_DIR`` — своя): исходный файл
``media.<ext>``, лёгкое превью ``preview.mp4`` (≤720p, как у «Ленты», app/feed_preview.py), кадр ``poster.jpg``,
лист кадров ``sheet.jpg`` и ключевые кадры ``k00.jpg…`` — их смотрит агент (``reference_get``). Удаление референса
(и проекта) убирает папку.

Разбор — фоном, по одному, в очереди тяжёлых задач (``heavy_slot``). Очередь — сама таблица: ``status``
queued → running (``stage`` download | analyze) → ready | failed. Перезапуск сервиса возвращает running в queued
(``recover``); скачанный файл повторно не качается. ``REFERENCES_WORKER=0`` — без фона (тесты зовут
``process_pending`` сами). Скачивание — app/references_fetch.py, разбор — app/references_analyze.py, ручки —
app/references_api.py, инструменты агента — app/references_tools.py.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

KINDS = ("video", "image", "audio", "link")
KIND_LABELS = {"video": "видео", "image": "картинка", "audio": "звук", "link": "ссылка"}
# Быстрые теги «что взять» (подсказка в форме и для агента); свои теги тоже можно.
QUICK_TAGS = ("темп", "склейки", "подписи", "цвет", "переходы", "звук", "музыка", "сюжет", "крючок")
STATUSES = ("queued", "running", "ready", "failed")
MAX_TAGS = 12
MAX_TAG_LEN = 32
TEXT_LIMITS = {"title": 160, "note": 4000, "url": 2000, "original_filename": 255, "error": 500}

SCHEMA = """
CREATE TABLE IF NOT EXISTS project_references (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind TEXT NOT NULL DEFAULT 'video',
    title TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    pinned INTEGER NOT NULL DEFAULT 0,
    url TEXT NOT NULL DEFAULT '',
    download INTEGER NOT NULL DEFAULT 1,
    original_filename TEXT NOT NULL DEFAULT '',
    file_name TEXT NOT NULL DEFAULT '',
    mime_type TEXT NOT NULL DEFAULT '',
    size_bytes INTEGER NOT NULL DEFAULT 0,
    duration_sec REAL,
    width INTEGER,
    height INTEGER,
    status TEXT NOT NULL DEFAULT 'queued',
    stage TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    analysis TEXT NOT NULL DEFAULT '',
    analyzed_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


def ensure_schema(conn: Any) -> None:
    """Таблица референсов (зовёт app/db.Database.init)."""
    conn.execute(SCHEMA)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_project_references ON project_references(project_id, id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_project_references_status ON project_references(status, id)")


# --------------------------------------------------------------------------- #
# Файлы
# --------------------------------------------------------------------------- #


def root_dir() -> Path:
    from app.settings import settings

    raw = os.getenv("REFERENCES_DIR", "").strip()
    return Path(raw).resolve() if raw else (Path(settings.runtime_dir) / "references").resolve()


def ref_dir(ref_id: int, create: bool = False) -> Path:
    path = root_dir() / f"ref-{int(ref_id)}"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def media_path(ref: dict) -> Path | None:
    """Исходный файл референса (None — нет: ссылка-страница или ещё не скачан)."""
    name = str(ref.get("file_name") or "")
    if not name:
        return None
    path = ref_dir(int(ref["id"])) / name
    return path if path.is_file() else None


# Что можно отдать по /media/references/{id}/{name}: имя → файл в папке референса.
ARTIFACTS = {"preview": "preview.mp4", "poster": "poster.jpg", "sheet": "sheet.jpg"}
_KEYFRAME = re.compile(r"^k\d{2}\.jpg$")


def artifact_path(ref: dict, name: str) -> Path | None:
    if name == "file":
        return media_path(ref)
    if name == "preview":
        # Лёгкий файл копией не делается (как у «Ленты»): превью — он сам.
        own = ref_dir(int(ref["id"])) / ARTIFACTS["preview"]
        if own.is_file():
            return own
        light = (_analysis(ref) or {}).get("preview_is_original")
        return media_path(ref) if light else None
    file = ARTIFACTS.get(name) or (name if _KEYFRAME.match(name) else "")
    if not file:
        return None
    path = ref_dir(int(ref["id"])) / file
    return path if path.is_file() else None


def remove_files(ref_id: int) -> None:
    shutil.rmtree(ref_dir(ref_id), ignore_errors=True)


def clean_orphans(store: Any) -> int:
    """Папки референсов, которых нет в базе (удалён проект, сбой посреди удаления), — прочь."""
    base = root_dir()
    if not base.is_dir():
        return 0
    known = {int(r["id"]) for r in store.db.query_all("SELECT id FROM project_references")}
    removed = 0
    for child in base.iterdir():
        m = re.match(r"^ref-(\d+)$", child.name)
        if child.is_dir() and m and int(m.group(1)) not in known:
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
    return removed


# --------------------------------------------------------------------------- #
# Поля
# --------------------------------------------------------------------------- #


def normalize_tags(raw: Any) -> list[str]:
    """Теги: список или строка через запятую/#; нижний регистр, без повторов, до MAX_TAGS."""
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw) if raw.strip().startswith("[") else None
        except ValueError:
            parsed = None
        items: Any = parsed if isinstance(parsed, list) else re.split(r"[,;#\n]+", raw)
    elif isinstance(raw, (list, tuple)):
        items = raw
    else:
        items = []
    out: list[str] = []
    for item in items:
        tag = re.sub(r"\s+", " ", str(item or "")).strip().strip("#").lower().replace("ё", "е")[:MAX_TAG_LEN]
        if tag and tag not in out:
            out.append(tag)
        if len(out) >= MAX_TAGS:
            break
    return out


def _text(name: str, value: Any) -> str:
    return str(value or "").strip()[: TEXT_LIMITS.get(name, 1000)]


def _analysis(row: Any) -> dict | None:
    raw = row.get("analysis") if isinstance(row, dict) else row["analysis"]
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "null")
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def aspect_label(width: Any, height: Any) -> str:
    try:
        w, h = int(width or 0), int(height or 0)
    except (TypeError, ValueError):
        return ""
    if w <= 0 or h <= 0:
        return ""
    ratio = w / h
    for label, value in (("9:16", 9 / 16), ("3:4", 3 / 4), ("4:5", 4 / 5), ("1:1", 1.0), ("4:3", 4 / 3),
                         ("16:9", 16 / 9), ("21:9", 21 / 9)):
        if abs(ratio - value) / value < 0.04:
            return label
    return f"{w}:{h}" if max(w, h) < 50 else f"{ratio:.2f}:1"


def _decode(row: Any) -> dict:
    ref = dict(row)
    try:
        tags = json.loads(ref.get("tags") or "[]")
    except (TypeError, ValueError):
        tags = []
    ref["tags"] = tags if isinstance(tags, list) else []
    ref["pinned"] = bool(ref.get("pinned"))
    ref["download"] = bool(ref.get("download"))
    ref["analysis"] = _analysis(ref)
    ref["aspect"] = aspect_label(ref.get("width"), ref.get("height"))
    return ref


def _row(store: Any, ref_id: int) -> dict:
    row = store.db.query_one("SELECT * FROM project_references WHERE id = ?", (int(ref_id),))
    if not row:
        raise KeyError(f"нет референса #{ref_id}")
    return _decode(row)


def get_reference(store: Any, ref_id: int) -> dict:
    return _row(store, ref_id)


def list_references(store: Any, project_id: int, *, pinned: bool | None = None,
                    kind: str | None = None) -> list[dict]:
    sql = "SELECT * FROM project_references WHERE project_id = ?"
    params: list[Any] = [int(project_id)]
    if pinned is not None:
        sql += " AND pinned = ?"
        params.append(1 if pinned else 0)
    if kind:
        sql += " AND kind = ?"
        params.append(str(kind))
    sql += " ORDER BY pinned DESC, id DESC"
    return [_decode(r) for r in store.db.query_all(sql, params)]


def count_references(store: Any, project_id: int) -> dict:
    row = store.db.query_one(
        "SELECT COUNT(*) AS n, COALESCE(SUM(pinned), 0) AS pinned FROM project_references WHERE project_id = ?",
        (int(project_id),))
    return {"total": int(row["n"] or 0), "pinned": int(row["pinned"] or 0)}


def _version(ref: dict) -> str:
    return re.sub(r"\D", "", str(ref.get("analyzed_at") or ref.get("updated_at") or ""))[:14]


def media_url(ref: dict, name: str) -> str:
    v = _version(ref)
    return f"/media/references/{int(ref['id'])}/{name}" + (f"?v={v}" if v else "")


def public(ref: dict) -> dict:
    """Референс для UI и API: без путей на диске, со ссылками на файлы, которые есть."""
    out = {k: v for k, v in ref.items() if k not in ("file_name",)}
    out["kind_label"] = KIND_LABELS.get(ref.get("kind") or "", "")
    out["has_file"] = media_path(ref) is not None
    urls = {}
    for name in ("file", "preview", "poster", "sheet"):
        if artifact_path(ref, name) is not None:
            urls[name] = media_url(ref, name)
    analysis = ref.get("analysis") or {}
    if isinstance(analysis.get("page"), dict) and analysis["page"].get("image") and "poster" not in urls:
        urls["poster_remote"] = analysis["page"]["image"]
    out["urls"] = urls
    return out


def create_reference(store: Any, project_id: int, *, kind: str = "video", title: str = "", note: str = "",
                     tags: Any = None, pinned: bool = False, url: str = "", download: bool = True,
                     original_filename: str = "", status: str = "queued") -> dict:
    from app import projects as P

    P.get_project(store, int(project_id), include_sources=False)
    if kind not in KINDS:
        raise ValueError(f"вид референса: {', '.join(KINDS)}")
    cur = store.db.execute(
        "INSERT INTO project_references (project_id, kind, title, note, tags, pinned, url, download, "
        "original_filename, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (int(project_id), kind, _text("title", title), _text("note", note),
         json.dumps(normalize_tags(tags), ensure_ascii=False), 1 if pinned else 0, _text("url", url),
         1 if download else 0, _text("original_filename", original_filename), status))
    return _row(store, int(cur.lastrowid))


def update_reference(store: Any, ref_id: int, **fields: Any) -> dict:
    _row(store, ref_id)
    sets: list[str] = []
    params: list[Any] = []
    for name in ("title", "note"):
        if fields.get(name) is not None:
            sets.append(f"{name} = ?")
            params.append(_text(name, fields[name]))
    if fields.get("tags") is not None:
        sets.append("tags = ?")
        params.append(json.dumps(normalize_tags(fields["tags"]), ensure_ascii=False))
    if fields.get("pinned") is not None:
        sets.append("pinned = ?")
        params.append(1 if fields["pinned"] else 0)
    if not sets:
        return _row(store, ref_id)
    sets.append("updated_at = CURRENT_TIMESTAMP")
    store.db.execute(f"UPDATE project_references SET {', '.join(sets)} WHERE id = ?", (*params, int(ref_id)))
    return _row(store, ref_id)


def delete_reference(store: Any, ref_id: int) -> dict:
    ref = _row(store, ref_id)
    store.db.execute("DELETE FROM project_references WHERE id = ?", (int(ref_id),))
    remove_files(int(ref_id))
    return {"deleted": True, "id": int(ref_id), "title": ref.get("title") or ""}


def delete_project_files(store: Any, project_id: int) -> list[int]:
    """Перед удалением проекта: id его референсов (строки уйдут каскадом, папки убирает вызывающий)."""
    return [int(r["id"]) for r in store.db.query_all("SELECT id FROM project_references WHERE project_id = ?",
                                                      (int(project_id),))]


def set_fields(store: Any, ref_id: int, **fields: Any) -> None:
    """Служебная запись полей (статус, размеры, файл, разбор) — без проверок пользовательских полей."""
    if not fields:
        return
    if "analysis" in fields and not isinstance(fields["analysis"], str):
        fields["analysis"] = json.dumps(fields["analysis"], ensure_ascii=False)
    cols = ", ".join(f"{k} = ?" for k in fields)
    store.db.execute(f"UPDATE project_references SET {cols}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                     (*fields.values(), int(ref_id)))


# --------------------------------------------------------------------------- #
# Заведение: файл и ссылка
# --------------------------------------------------------------------------- #

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif", ".heic", ".tif", ".tiff"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".flac", ".wma", ".aif", ".aiff"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".3gp", ".ts", ".mts"}


def kind_by_name(name: str) -> str | None:
    ext = Path(str(name or "")).suffix.lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in AUDIO_EXT:
        return "audio"
    if ext in VIDEO_EXT:
        return "video"
    return None


def _safe_ext(name: str, kind: str) -> str:
    ext = Path(str(name or "")).suffix.lower()
    if re.match(r"^\.[a-z0-9]{1,5}$", ext):
        return ext
    return {"image": ".jpg", "audio": ".m4a"}.get(kind, ".mp4")


def create_from_file(store: Any, project_id: int, src: Path, *, original_name: str = "", title: str = "",
                     note: str = "", tags: Any = None, pinned: bool = False, move: bool = True) -> dict:
    """Файл (загрузка с телефона, путь сервера) → референс в очереди разбора. ``move`` — перенести, а не копировать."""
    from app.references_fetch import probe_kind

    src = Path(src)
    if not src.is_file() or src.stat().st_size <= 0:
        raise ValueError("файл пустой или не найден")
    name = original_name or src.name
    kind = probe_kind(src, name)
    if kind is None:
        raise ValueError("не похоже ни на видео, ни на картинку, ни на звук (mp4, mov, jpg, png, mp3, wav…)")
    ref = create_reference(store, project_id, kind=kind, title=title or Path(name).stem, note=note, tags=tags,
                           pinned=pinned, original_filename=name, status="queued")
    folder = ref_dir(int(ref["id"]), create=True)
    file_name = "media" + _safe_ext(name, kind)
    try:
        (shutil.move if move else shutil.copyfile)(str(src), str(folder / file_name))
    except OSError:
        delete_reference(store, int(ref["id"]))
        raise
    set_fields(store, int(ref["id"]), file_name=file_name, size_bytes=(folder / file_name).stat().st_size)
    kick(store)
    return _row(store, int(ref["id"]))


def create_from_url(store: Any, project_id: int, url: str, *, title: str = "", note: str = "", tags: Any = None,
                    pinned: bool = False, download: bool = True, kind: str | None = None) -> dict:
    """Ссылка → референс; скачивание и разбор — фоном. ``download=False`` — сохранить только как ссылку."""
    from urllib.parse import urlsplit

    url = str(url or "").strip()
    if urlsplit(url).scheme not in ("http", "https") or not urlsplit(url).hostname:
        raise ValueError("нужна ссылка http(s)")
    guess = kind if kind in KINDS else (kind_by_name(urlsplit(url).path) or ("video" if download else "link"))
    ref = create_reference(store, project_id, kind=guess if download else "link", title=title, note=note,
                           tags=tags, pinned=pinned, url=url, download=download, status="queued")
    kick(store)
    return ref


# --------------------------------------------------------------------------- #
# Очередь разбора
# --------------------------------------------------------------------------- #

_lock = threading.Lock()
_wake = threading.Event()
_thread: threading.Thread | None = None


def worker_enabled() -> bool:
    return os.getenv("REFERENCES_WORKER", "1").strip().lower() not in ("0", "false", "no", "off")


def recover(store: Any) -> int:
    """Старт сервиса: прерванные разборы — снова в очередь (скачанный файл остаётся, второй раз не качается)."""
    cur = store.db.execute("UPDATE project_references SET status = 'queued', stage = '' WHERE status = 'running'")
    try:
        clean_orphans(store)
    except OSError:
        log.warning("references: orphan cleanup failed", exc_info=True)
    return int(cur.rowcount or 0)


def kick(store: Any) -> None:
    global _thread
    if not worker_enabled():
        return
    with _lock:
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_loop, args=(store,), name="references", daemon=True)
            _thread.start()
    _wake.set()


def _loop(store: Any) -> None:
    while True:
        try:
            done = process_pending(store, limit=1)
        except Exception:  # noqa: BLE001 - работник не должен умирать
            log.exception("references pass failed")
            done = 0
        if not done:
            _wake.wait(300)
            _wake.clear()


def _claim(store: Any) -> dict | None:
    with store.db.transaction():
        row = store.db.query_one("SELECT * FROM project_references WHERE status = 'queued' ORDER BY id LIMIT 1")
        if not row:
            return None
        store.db.execute("UPDATE project_references SET status = 'running', error = '' WHERE id = ?",
                         (int(row["id"]),))
    return _decode(row)


def process_pending(store: Any, limit: int | None = None) -> int:
    """Скачать и разобрать референсы из очереди (не больше ``limit``). Возвращает, сколько обработано."""
    count = 0
    while limit is None or count < limit:
        ref = _claim(store)
        if ref is None:
            break
        process(store, ref)
        count += 1
    return count


def requeue(store: Any, ref_id: int) -> dict:
    ref = _row(store, ref_id)
    if ref["status"] == "running":
        return ref
    set_fields(store, ref_id, status="queued", stage="", error="")
    kick(store)
    return _row(store, ref_id)


def _exists(store: Any, ref_id: int) -> bool:
    return store.db.query_one("SELECT 1 FROM project_references WHERE id = ?", (int(ref_id),)) is not None


def process(store: Any, ref: dict) -> dict:
    """Один референс: скачать (если ссылка и файла нет) → разобрать. Ошибка — в ``error`` (видна в карточке)."""
    from app import references_analyze as RA
    from app import references_fetch as RF
    from app.heavy_queue import heavy_slot

    ref_id = int(ref["id"])
    try:
        if media_path(ref) is None and ref.get("url"):
            set_fields(store, ref_id, stage="download")
            fetched = RF.fetch(ref, ref_dir(ref_id, create=True))
            if not _exists(store, ref_id):
                remove_files(ref_id)
                return {"ok": False, "error": "референс удалён"}
            set_fields(store, ref_id, **fetched["fields"])
            ref = _row(store, ref_id)
            if ref["kind"] == "link":
                set_fields(store, ref_id, status="ready", stage="", error="",
                           analysis=fetched.get("analysis") or {}, analyzed_at=_sql_now(store))
                return {"ok": True, "kind": "link"}
        path = media_path(ref)
        if path is None:
            raise RuntimeError("файла референса нет на диске — загрузи заново")
        set_fields(store, ref_id, stage="analyze")
        with heavy_slot("references", f"референс #{ref_id}"):
            result = RA.analyze(path, ref["kind"], ref_dir(ref_id, create=True))
        if not _exists(store, ref_id):
            remove_files(ref_id)
            return {"ok": False, "error": "референс удалён"}
        fields = {k: result.get(k) for k in ("duration_sec", "width", "height") if result.get(k) is not None}
        if result.get("kind") in KINDS and result["kind"] != ref["kind"]:
            fields["kind"] = result["kind"]
        set_fields(store, ref_id, status="ready", stage="", error="", analysis=result["analysis"],
                   analyzed_at=_sql_now(store), **fields)
    except Exception as exc:  # noqa: BLE001 - ошибка видна в карточке, работник живёт
        from app.proxy_config import mask_secrets

        log.warning("reference %s failed: %s", ref_id, exc)
        if _exists(store, ref_id):
            set_fields(store, ref_id, status="failed", stage="", error=mask_secrets(exc)[:TEXT_LIMITS["error"]]
                       or "ошибка разбора")
        return {"ok": False, "error": str(exc)[:300]}
    return {"ok": True}


def _sql_now(store: Any) -> str:
    row = store.db.query_one("SELECT CURRENT_TIMESTAMP AS now")
    return str(row["now"])


# --------------------------------------------------------------------------- #
# Для агента: короткий срез
# --------------------------------------------------------------------------- #


def summary_line(ref: dict) -> str:
    """Одна строка разбора по-русски: «23 с, 9:16, 18 планов, 46 склеек/мин, 128 BPM, 67 % склеек в долю»."""
    a = ref.get("analysis") or {}
    return str(a.get("summary") or "")


def brief(ref: dict) -> dict:
    """Референс для списка агента: что это, что взять, главный ли, короткий разбор."""
    a = ref.get("analysis") or {}
    out = {"reference_id": int(ref["id"]), "kind": ref["kind"], "title": ref.get("title") or "",
           "note": ref.get("note") or "", "tags": ref.get("tags") or [], "pinned": bool(ref.get("pinned")),
           "status": ref.get("status"), "url": ref.get("url") or "", "created_at": ref.get("created_at")}
    if ref.get("status") == "failed":
        out["error"] = ref.get("error") or ""
    if a.get("summary"):
        out["summary"] = a["summary"]
    for key in ("duration", "aspect", "cuts_per_min", "shots", "bpm"):
        value = a.get(key)
        if value not in (None, "", 0):
            out[key] = value
    return out


def pinned_brief(store: Any, project_id: int, limit: int = 6) -> list[dict]:
    """Главные референсы проекта — для системного промпта чата (пример стиля проекта)."""
    try:
        items = list_references(store, int(project_id), pinned=True)
    except Exception:  # noqa: BLE001 - срез промпта не должен ронять ход
        return []
    return [brief(r) for r in items[:limit]]
