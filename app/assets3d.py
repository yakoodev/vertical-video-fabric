"""«3D-модели» проекта: персонажи (VRM), реквизит и окружение (GLB/glTF) с лёгким превью для браузера.

Строка ``project_assets3d`` + папка ``<runtime>/assets3d/a-<id>`` (``ASSETS3D_DIR`` — своя): исходник
``original.<ext>`` (байт в байт), превью ``preview.glb`` и его gzip ``preview.glb.gz`` (app/assets3d_preview.py),
обложка ``thumb.webp`` — портрет из VRM или снимок из окна просмотра (его присылает страница при первом показе).

Повторная загрузка того же файла в тот же проект (совпал sha256) не плодит копию: возвращается уже заведённая
модель (так импорт ``tools/assets3d_import.py`` можно гонять сколько угодно). Удаление модели или проекта
убирает папку.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import re
import shutil
import threading
from pathlib import Path
from typing import Any

from app import assets3d_preview as AP

log = logging.getLogger(__name__)

KINDS = ("character", "prop", "environment")
KIND_LABELS = {"character": "Персонажи", "prop": "Реквизит", "environment": "Окружение"}
FORMATS = {".vrm": "vrm", ".glb": "glb", ".gltf": "gltf"}
MAX_UPLOAD_BYTES = int(os.getenv("ASSETS3D_MAX_UPLOAD_MB", "200")) * 1024 * 1024
MAX_THUMB_BYTES = 600 * 1024
NAME_LIMIT = 120

SCHEMA = """
CREATE TABLE IF NOT EXISTS project_assets3d (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind TEXT NOT NULL DEFAULT 'prop',
    name TEXT NOT NULL DEFAULT '',
    original_filename TEXT NOT NULL DEFAULT '',
    format TEXT NOT NULL DEFAULT 'glb',
    sha256 TEXT NOT NULL DEFAULT '',
    size_bytes INTEGER NOT NULL DEFAULT 0,
    preview_bytes INTEGER NOT NULL DEFAULT 0,
    preview_gz_bytes INTEGER NOT NULL DEFAULT 0,
    has_thumb INTEGER NOT NULL DEFAULT 0,
    thumb_rev INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'ready',
    error TEXT NOT NULL DEFAULT '',
    info TEXT NOT NULL DEFAULT '{}',
    sort INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

_BUILD_LOCK = threading.Lock()


def ensure_schema(conn: Any) -> None:
    """Таблица 3D-моделей проекта (зовёт app/db.Database.init)."""
    conn.execute(SCHEMA)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_project_assets3d ON project_assets3d(project_id, kind, sort, id)")


# --------------------------------------------------------------------------- файлы


def root_dir() -> Path:
    from app.settings import settings

    raw = os.getenv("ASSETS3D_DIR", "").strip()
    return Path(raw).resolve() if raw else (Path(settings.runtime_dir) / "assets3d").resolve()


def asset_dir(asset_id: int, create: bool = False) -> Path:
    path = root_dir() / f"a-{int(asset_id)}"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def original_path(asset: dict) -> Path:
    return asset_dir(int(asset["id"])) / f"original.{asset.get('format') or 'glb'}"


FILES = {"preview": "preview.glb", "preview_gz": "preview.glb.gz", "thumb": "thumb.webp"}


def file_path(asset: dict, name: str) -> Path | None:
    if name == "original":
        path = original_path(asset)
    elif name in FILES:
        path = asset_dir(int(asset["id"])) / FILES[name]
    else:
        return None
    return path if path.is_file() else None


def remove_files(asset_id: int) -> None:
    shutil.rmtree(asset_dir(asset_id), ignore_errors=True)


# --------------------------------------------------------------------------- строки


def _row(store: Any, asset_id: int) -> dict:
    row = store.db.query_one("SELECT * FROM project_assets3d WHERE id = ?", (int(asset_id),))
    if not row:
        raise KeyError(f"3D-модели {asset_id} нет")
    return dict(row)


def get_asset(store: Any, asset_id: int) -> dict:
    return _row(store, asset_id)


def list_assets(store: Any, project_id: int) -> list[dict]:
    rows = store.db.query_all(
        "SELECT * FROM project_assets3d WHERE project_id = ? ORDER BY sort, id", (int(project_id),))
    return [dict(r) for r in rows]


def public(asset: dict) -> dict:
    """Для клиента: справка, размеры и адреса файлов (с версией — кэш браузера не держит старое)."""
    try:
        info = json.loads(asset.get("info") or "{}")
    except ValueError:
        info = {}
    aid = int(asset["id"])
    rev = f"{asset.get('preview_bytes', 0)}-{asset.get('thumb_rev', 0)}"
    return {
        "id": aid,
        "project_id": int(asset["project_id"]),
        "kind": asset["kind"],
        "kind_label": KIND_LABELS.get(asset["kind"], asset["kind"]),
        "name": asset["name"],
        "original_filename": asset["original_filename"],
        "format": asset["format"],
        "is_vrm": asset["format"] == "vrm" or bool(info.get("vrm")),
        "sha256": asset["sha256"],
        "size_bytes": int(asset["size_bytes"] or 0),
        "preview_bytes": int(asset["preview_bytes"] or 0),
        "preview_gz_bytes": int(asset["preview_gz_bytes"] or 0),
        "status": asset["status"],
        "error": asset["error"],
        "info": info,
        "has_thumb": bool(asset["has_thumb"]),
        "urls": {
            "preview": f"/media/assets3d/{aid}/preview?v={rev}",
            "thumb": f"/media/assets3d/{aid}/thumb?v={rev}" if asset["has_thumb"] else None,
            "original": f"/media/assets3d/{aid}/original",
        },
        "created_at": asset.get("created_at"),
        "updated_at": asset.get("updated_at"),
    }


def grouped(assets: list[dict]) -> list[dict]:
    """Группы в порядке KINDS (пустые тоже — страница показывает их заголовки)."""
    return [{"kind": k, "label": KIND_LABELS[k], "assets": [public(a) for a in assets if a["kind"] == k]} for k in KINDS]


def _clean_name(name: str, fallback: str) -> str:
    name = re.sub(r"\s+", " ", str(name or "")).strip()[:NAME_LIMIT]
    return name or fallback


def guess_kind(fmt: str, info: dict) -> str:
    """VRM и любые модели со скелетом и выражениями — персонажи, остальное — реквизит."""
    if fmt == "vrm" or info.get("vrm"):
        return "character"
    if info.get("skins") and info.get("morph_targets"):
        return "character"
    return "prop"


def format_of(filename: str) -> str:
    fmt = FORMATS.get(Path(filename or "").suffix.lower())
    if not fmt:
        raise ValueError("нужен файл .vrm, .glb или .gltf")
    return fmt


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def find_duplicate(store: Any, project_id: int, sha: str) -> dict | None:
    row = store.db.query_one("SELECT * FROM project_assets3d WHERE project_id = ? AND sha256 = ?",
                             (int(project_id), sha))
    return dict(row) if row else None


def create_from_file(store: Any, project_id: int, path: Path, *, original_name: str, name: str = "",
                     kind: str = "") -> dict:
    """Завести модель из файла (файл копируется, исходник не трогается) и сразу собрать превью.

    Тот же файл в том же проекте — вернуть уже заведённую (ключ ``duplicate``)."""
    from app import projects as P

    P.get_project(store, project_id, include_sources=False)
    path = Path(path)
    fmt = format_of(original_name)
    size = path.stat().st_size
    if size <= 0:
        raise ValueError("файл пустой")
    if size > MAX_UPLOAD_BYTES:
        raise ValueError(f"файл больше {MAX_UPLOAD_BYTES // (1024 * 1024)} МБ")
    # Проверка содержимого до копирования: не модель — сразу отказ, без строки в базе.
    doc, _bin = AP.read_model(path)
    if fmt == "vrm" and not AP.vrm_version(doc):
        raise ValueError("в файле .vrm нет данных VRM — это обычная модель, переименуйте в .glb")
    sha = sha256_file(path)
    dup = find_duplicate(store, project_id, sha)
    if dup:
        return {**dup, "duplicate": True}
    info0 = AP.inspect(doc)
    kind = kind if kind in KINDS else guess_kind(fmt, info0)
    stem = Path(original_name).stem
    sort = store.db.query_one("SELECT COALESCE(MAX(sort), 0) + 1 AS n FROM project_assets3d WHERE project_id = ?",
                              (int(project_id),))["n"]
    cur = store.db.execute(
        "INSERT INTO project_assets3d (project_id, kind, name, original_filename, format, sha256, size_bytes, status, "
        "sort) VALUES (?, ?, ?, ?, ?, ?, ?, 'building', ?)",
        (int(project_id), kind, _clean_name(name, stem), Path(original_name).name[:255], fmt, sha, size, int(sort)),
    )
    asset_id = int(cur.lastrowid)
    folder = asset_dir(asset_id, create=True)
    try:
        shutil.copyfile(path, folder / f"original.{fmt}")
    except OSError:
        store.db.execute("DELETE FROM project_assets3d WHERE id = ?", (asset_id,))
        remove_files(asset_id)
        raise
    return rebuild(store, asset_id)


def rebuild(store: Any, asset_id: int) -> dict:
    """Собрать превью заново (после правки сборщика или упавшей сборки)."""
    asset = _row(store, asset_id)
    folder = asset_dir(int(asset_id), create=True)
    src = original_path(asset)
    if not src.is_file():
        raise KeyError("исходного файла модели нет")
    thumb = folder / FILES["thumb"]
    had_thumb = thumb.is_file()
    try:
        with _BUILD_LOCK:  # по одной: 4 с и до гигабайта памяти на большую модель
            info = AP.build_preview(src, folder / FILES["preview"], thumb=None if had_thumb else thumb)
        prev = folder / FILES["preview"]
        data = prev.read_bytes()
        gz = gzip.compress(data, 6)
        gz_path = folder / FILES["preview_gz"]
        if len(gz) < len(data) * 0.95:
            gz_path.write_bytes(gz)
        else:
            gz_path.unlink(missing_ok=True)
        store.db.execute(
            "UPDATE project_assets3d SET status = 'ready', error = '', info = ?, preview_bytes = ?, preview_gz_bytes = ?, "
            "has_thumb = ?, thumb_rev = thumb_rev + ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (json.dumps(info, ensure_ascii=False), len(data), len(gz) if gz_path.is_file() else 0,
             1 if thumb.is_file() else 0, 0 if had_thumb else 1, int(asset_id)),
        )
    except Exception as exc:  # noqa: BLE001 — модель остаётся в списке с понятной ошибкой
        log.warning("assets3d: превью %s не собралось", asset_id, exc_info=True)
        msg = str(exc) if isinstance(exc, ValueError) else "не получилось подготовить модель к просмотру"
        store.db.execute("UPDATE project_assets3d SET status = 'failed', error = ?, updated_at = CURRENT_TIMESTAMP "
                         "WHERE id = ?", (msg[:500], int(asset_id)))
    return _row(store, asset_id)


def update_asset(store: Any, asset_id: int, *, name: str | None = None, kind: str | None = None) -> dict:
    asset = _row(store, asset_id)
    sets, args = [], []
    if name is not None:
        sets.append("name = ?")
        args.append(_clean_name(name, asset["name"]))
    if kind is not None:
        if kind not in KINDS:
            raise ValueError("группа: персонаж, реквизит или окружение")
        sets.append("kind = ?")
        args.append(kind)
    if sets:
        store.db.execute(f"UPDATE project_assets3d SET {', '.join(sets)}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (*args, int(asset_id)))
    return _row(store, asset_id)


def set_thumbnail(store: Any, asset_id: int, data: bytes) -> dict:
    """Обложка из окна просмотра (снимок холста, WebP/PNG/JPEG) → thumb.webp ≤ 384 px."""
    _row(store, asset_id)
    if not data or len(data) > MAX_THUMB_BYTES:
        raise ValueError("картинка обложки пустая или слишком большая")
    small = AP.shrink_image(data, 384, 80)
    if not small:
        raise ValueError("это не картинка")
    (asset_dir(int(asset_id), create=True) / FILES["thumb"]).write_bytes(small)
    store.db.execute("UPDATE project_assets3d SET has_thumb = 1, thumb_rev = thumb_rev + 1, "
                     "updated_at = CURRENT_TIMESTAMP WHERE id = ?", (int(asset_id),))
    return _row(store, asset_id)


def delete_asset(store: Any, asset_id: int) -> dict:
    asset = _row(store, asset_id)
    store.db.execute("DELETE FROM project_assets3d WHERE id = ?", (int(asset_id),))
    remove_files(int(asset_id))
    return {"deleted": True, "id": int(asset["id"])}


def delete_project_files(store: Any, project_id: int) -> list[int]:
    """id моделей проекта (их папки убираются после удаления строки проекта)."""
    try:
        rows = store.db.query_all("SELECT id FROM project_assets3d WHERE project_id = ?", (int(project_id),))
    except Exception:  # noqa: BLE001 — старая база без таблицы
        return []
    return [int(r["id"]) for r in rows]
