"""Вопросы стрима: выбор мема (из ``app/stream_questions.py``)."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.meme_catalog import CatalogError, fetch_template
from app.projects import project_of_source
from app.settings import settings
from app.stream_questions_core import _text, _times

MOMENT_STATUSES = ("open", "chosen", "none")


MAX_MOMENTS_PER_CALL = 30


MIN_MEME_OPTIONS, MAX_MEME_OPTIONS = 2, 4


MAX_URL_IMAGE_BYTES = 20 * 1024 * 1024


# --------------------------------------------------------------------------- #
# Мемы
# --------------------------------------------------------------------------- #


def choices_dir() -> Path:
    return settings.data_dir / "meme_choices"


def _save_url_image(url: str) -> tuple[Path, str]:
    from app.ai.asset_search import fetch_preview

    data, mime = fetch_preview(url)
    if len(data) > MAX_URL_IMAGE_BYTES:
        raise ValueError("картинка слишком большая")
    ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp"}.get(mime)
    if ext is None:
        raise ValueError("по ссылке не картинка JPG, PNG, GIF или WebP")
    folder = choices_dir()
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"{uuid4().hex}{ext}"
    dest.write_bytes(data)
    return dest, mime


def _mime_of(path: Path) -> str:
    return {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif",
            ".webp": "image/webp"}.get(path.suffix.lower(), "application/octet-stream")


def _prepare_option(store: Any, raw: Any, project_id: int | None = None) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("вариант — объект {catalog} | {asset_id} | {url}")
    note = _text(raw.get("note"), 300)
    if raw.get("catalog"):
        got = fetch_template(str(raw["catalog"]))
        return {"title": _text(raw.get("title"), 120) or got["title"], "note": note or got["use"],
                "file_path": str(got["path"]), "mime": _mime_of(got["path"]), "asset_id": None,
                "source": got["source"], "license": got["license"], "origin_url": got["origin_url"]}
    if raw.get("asset_id") not in (None, ""):
        asset = store.get_montage_asset(int(raw["asset_id"]))
        from app.asset_scope import allowed, refusal

        if not allowed(asset, project_id):
            raise ValueError(refusal(store, asset, project_id, "стрим"))
        if asset.get("kind") not in ("image", "video"):
            raise ValueError(f"файл #{asset['id']} не картинка и не видео")
        return {"title": _text(raw.get("title"), 120) or str(asset.get("label") or ""), "note": note,
                "file_path": str(asset.get("file_path") or ""), "mime": str(asset.get("mime_type") or ""),
                "asset_id": int(asset["id"]), "source": _text(raw.get("source"), 120) or "библиотека сервиса",
                "license": _text(raw.get("license"), 200), "origin_url": ""}
    if raw.get("url"):
        url = str(raw["url"]).strip()
        license_note = _text(raw.get("license"), 200)
        source = _text(raw.get("source"), 120)
        if not source or not license_note:
            raise ValueError("у варианта по ссылке нужны source и license: откуда картинка и какие права")
        path, mime = _save_url_image(url)
        return {"title": _text(raw.get("title"), 120) or "Мем", "note": note, "file_path": str(path), "mime": mime,
                "asset_id": None, "source": source, "license": license_note, "origin_url": url[:500]}
    raise ValueError("вариант — {catalog}, {asset_id} или {url}")


def _moment(store: Any, row: Any) -> dict:
    item = dict(row)
    options = store.db.query_all("SELECT * FROM meme_options WHERE moment_id = ? ORDER BY position, id", (item["id"],))
    item["options"] = [dict(o) for o in options]
    for option in item["options"]:
        option.pop("file_path", None)  # путь на диске наружу не отдаём: есть /preview
    return item


def add_meme_choices(store: Any, source_id: int, moments: Any) -> dict:
    source = store.get_source(int(source_id))
    if not isinstance(moments, list) or not moments:
        raise ValueError("moments — непустой список")
    if len(moments) > MAX_MOMENTS_PER_CALL:
        raise ValueError(f"за раз не больше {MAX_MOMENTS_PER_CALL} моментов")
    added, duplicates, failed = [], [], []
    for index, raw in enumerate(moments):
        if not isinstance(raw, dict):
            failed.append({"index": index, "error": "нужен объект"})
            continue
        try:
            a, b = _times(source, raw.get("t_start"), raw.get("t_end"))
        except ValueError as exc:
            failed.append({"index": index, "error": str(exc)})
            continue
        why = _text(raw.get("why"), 300)
        if not why:
            failed.append({"index": index, "error": "why: зачем здесь мем"})
            continue
        old = store.db.query_one("SELECT id FROM meme_moments WHERE source_id = ? AND t_start = ? AND why = ?",
                                 (int(source_id), a, why))
        if old:
            duplicates.append(int(old["id"]))
            continue
        options, problems = [], []
        project_id = project_of_source(store, int(source_id))
        for option in (raw.get("options") or [])[:MAX_MEME_OPTIONS]:
            try:
                options.append(_prepare_option(store, option, project_id))
            except (ValueError, KeyError, CatalogError) as exc:
                problems.append(str(exc)[:200])
        if len(options) < MIN_MEME_OPTIONS:
            failed.append({"index": index, "error": f"нужно {MIN_MEME_OPTIONS}–{MAX_MEME_OPTIONS} рабочих варианта, "
                                                    f"годных {len(options)}", "options": problems})
            continue
        cur = store.db.execute(
            "INSERT INTO meme_moments (source_id, t_start, t_end, why) VALUES (?, ?, ?, ?)",
            (int(source_id), a, b, why))
        moment_id = int(cur.lastrowid)
        for position, option in enumerate(options):
            store.db.execute(
                "INSERT INTO meme_options (moment_id, position, title, note, file_path, mime, asset_id, source, "
                "license, origin_url) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (moment_id, position, option["title"], option["note"], option["file_path"], option["mime"],
                 option["asset_id"], option["source"], option["license"], option["origin_url"]))
        added.append({"id": moment_id, "options": len(options), **({"option_errors": problems} if problems else {})})
    return {"added": added, "duplicates": duplicates, "failed": failed}


def list_moments(store: Any, source_id: int, status: str | None = None) -> list[dict]:
    store.get_source(int(source_id))
    sql, params = "SELECT * FROM meme_moments WHERE source_id = ?", [int(source_id)]
    if status:
        if status not in MOMENT_STATUSES:
            raise ValueError(f"status — {', '.join(MOMENT_STATUSES)}")
        sql += " AND status = ?"
        params.append(status)
    return [_moment(store, r) for r in store.db.query_all(sql + " ORDER BY t_start, id", params)]


def get_moment(store: Any, moment_id: int) -> dict:
    row = store.db.query_one("SELECT * FROM meme_moments WHERE id = ?", (int(moment_id),))
    if not row:
        raise KeyError(f"момента нет: {moment_id}")
    return _moment(store, row)


def option_file(store: Any, option_id: int) -> tuple[Path, str]:
    row = store.db.query_one("SELECT file_path, mime FROM meme_options WHERE id = ?", (int(option_id),))
    if not row:
        raise KeyError(f"варианта нет: {option_id}")
    path = Path(row["file_path"])
    if not path.is_file():
        raise KeyError("файла варианта больше нет")
    root = settings.data_dir.resolve()
    if root not in path.resolve().parents:
        raise KeyError("файл варианта вне папки данных")
    return path, str(row["mime"] or "application/octet-stream")


def _ensure_asset(store: Any, option_id: int) -> int:
    """Выбранный мем — в библиотеку сервиса: монтажёр вставляет его по asset_id, происхождение в описании."""
    from app.montage_assets import assets_dir, kind_for, probe_asset

    row = store.db.query_one("SELECT * FROM meme_options WHERE id = ?", (int(option_id),))
    # Мем выбрали для стрима этого проекта: в другие проекты он не попадёт.
    moment = store.db.query_one("SELECT source_id FROM meme_moments WHERE id = ?", (int(row["moment_id"]),))
    if row["asset_id"]:
        return int(row["asset_id"])
    source = Path(row["file_path"])
    kind = kind_for(source.name, row["mime"] or "")
    if kind is None or not source.is_file():
        raise ValueError("файл варианта не подходит для библиотеки")
    dest = assets_dir() / f"{uuid4().hex}{source.suffix.lower()}"
    shutil.copyfile(source, dest)
    origin = "; ".join(x for x in (f"источник: {row['source']}", f"права: {row['license']}", row["origin_url"]) if x)
    asset = store.create_montage_asset(
        kind=kind, label=row["title"] or "Мем", description=origin[:500], tags="мем",
        file_path=str(dest), original_filename=source.name, mime_type=row["mime"] or "",
        size_bytes=dest.stat().st_size, project_id=project_of_source(store, int(moment["source_id"])),
        **probe_asset(dest))
    store.db.execute("UPDATE meme_options SET asset_id = ? WHERE id = ?", (int(asset["id"]), int(option_id)))
    return int(asset["id"])


def choose_meme(store: Any, moment_id: int, option_id: Any = None, none: bool = False) -> dict:
    moment = get_moment(store, moment_id)
    if none:
        store.db.execute("UPDATE meme_moments SET status = 'none', chosen_option_id = NULL, "
                         "chosen_at = CURRENT_TIMESTAMP WHERE id = ?", (int(moment_id),))
        return get_moment(store, moment_id)
    if option_id in (None, ""):
        store.db.execute("UPDATE meme_moments SET status = 'open', chosen_option_id = NULL, chosen_at = NULL "
                         "WHERE id = ?", (int(moment_id),))
        return get_moment(store, moment_id)
    if int(option_id) not in {o["id"] for o in moment["options"]}:
        raise ValueError("это вариант другого момента")
    _ensure_asset(store, int(option_id))
    store.db.execute("UPDATE meme_moments SET status = 'chosen', chosen_option_id = ?, "
                     "chosen_at = CURRENT_TIMESTAMP WHERE id = ?", (int(option_id), int(moment_id)))
    return get_moment(store, moment_id)
