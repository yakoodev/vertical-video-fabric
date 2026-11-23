"""«Лента»: каждая готовая работа одним местом — версии, варианты файлов, вердикт владельца и где уже выложено.

Зачем. Владелец получал по ~20 сообщений Telegram на один ролик (версии, форматы, части для TikTok) и терял, что
принято и что уже выложено. Лента — один список: карточка работы, её версии (новые сверху) с заметкой «что поменял»,
у версии варианты файлов (9:16, 16:9, «TikTok часть 1/4», «без музыки») с лёгким превью и полным файлом, и отметки
«запощено в TikTok / YouTube / IG».

Модель:

* ``feed_works`` — работа: название, проект (необязательно), вид (movie / amv / meme / promo / series / other),
  обложка — кадр варианта (``cover_variant_id`` или первый готовый кадр последней версии), ``activity_at`` — время
  последнего события (версия, вердикт, отметка публикации): по нему сортируется лента;
* ``feed_versions`` — версия: номер ``n`` (1, 2, 3…), подпись ``label`` («v4.1», необязательно), заметка
  ``note``, статус ``draft`` / ``review`` (на проверке) / ``accepted`` (принято) / ``rejected`` (отклонено) и вердикт
  владельца ``verdict`` — его словами;
* ``feed_variants`` — вариант: подпись, файл — готовый клип сервиса (``clip_id``) или файл (загрузка, путь на
  сервере, импорт с хоста — app/feed_files.py), лёгкое превью ≤720p и кадр-обложка (app/feed_preview.py);
* отметки публикации — НЕ своя таблица, а ручные публикации проектов ``project_publications``
  (app/projects_stats.py) с ``feed_variant_id``: числа просмотров собираются тем же фоном, что у публикаций с экрана
  клипа, а вариант-клип отмечается выложенным и на экране клипа. У варианта-клипа в ленте видны и публикации,
  сделанные мимо ленты: руками с экрана клипа и сервисом (``job_targets``) — их отсюда не снять.

Таблицы создаёт ``ensure_feed_schema`` из ``Database.init``. Существующие данные не трогаются.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import error_text
from app import feed_files as F
from app.projects_core import _date, _long_text, _text

log = logging.getLogger(__name__)

KINDS = {"movie": "Фильм", "amv": "AMV", "meme": "Мем", "promo": "Промо", "series": "Серия", "other": "Другое"}
STATUSES = {"draft": "Черновик", "review": "На проверке", "accepted": "Принято", "rejected": "Отклонено"}
# Отметки площадок: те же, что у ручных публикаций (vk тоже есть там — оставляем).
PLATFORMS = ("tiktok", "youtube", "instagram", "vk", "other")
POSTED_BY = ("owner", "agent")
FILTERS = {"ready_unposted": "готово, не выложено", "review": "на проверке", "posted": "выложено",
           "draft": "черновики", "rejected": "отклонено"}
MAX_VARIANTS = 40

SCHEMA_SQL = [
    """
    CREATE TABLE IF NOT EXISTS feed_works (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
        kind TEXT NOT NULL DEFAULT 'other',
        cover_variant_id INTEGER,
        made_by TEXT NOT NULL DEFAULT '',
        activity_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_feed_works_activity ON feed_works(activity_at)",
    """
    CREATE TABLE IF NOT EXISTS feed_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        work_id INTEGER NOT NULL REFERENCES feed_works(id) ON DELETE CASCADE,
        n INTEGER NOT NULL,
        label TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'review',
        verdict TEXT NOT NULL DEFAULT '',
        status_by TEXT NOT NULL DEFAULT '',
        status_at TEXT,
        made_by TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (work_id, n)
    )
    """,
    # file_path — файл варианта (для клипа — снимок пути его файла на момент добавления); owned — файл лежит в папке
    # ленты и удаляется вместе с вариантом (ссылка на файл сервера — нет). preview_status: queued / running / ready /
    # failed; preview_path может совпасть с file_path (файл и так лёгкий — не перекодируем).
    """
    CREATE TABLE IF NOT EXISTS feed_variants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        version_id INTEGER NOT NULL REFERENCES feed_versions(id) ON DELETE CASCADE,
        position INTEGER NOT NULL DEFAULT 0,
        label TEXT NOT NULL DEFAULT '',
        clip_id INTEGER REFERENCES clips(id) ON DELETE SET NULL,
        file_path TEXT NOT NULL DEFAULT '',
        owned INTEGER NOT NULL DEFAULT 0,
        original_name TEXT NOT NULL DEFAULT '',
        size_bytes INTEGER,
        duration_sec REAL,
        width INTEGER,
        height INTEGER,
        preview_path TEXT NOT NULL DEFAULT '',
        poster_path TEXT NOT NULL DEFAULT '',
        preview_status TEXT NOT NULL DEFAULT 'queued',
        preview_error TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_feed_variants_version ON feed_variants(version_id, position)",
    "CREATE INDEX IF NOT EXISTS idx_feed_variants_clip ON feed_variants(clip_id)",
]


def ensure_feed_schema(conn) -> None:
    """Таблицы ленты (вызывается из ``Database.init`` после проектов: отметки — их ``project_publications``)."""
    for sql in SCHEMA_SQL:
        conn.execute(sql)


# --------------------------------------------------------------------------- #
# Проверки
# --------------------------------------------------------------------------- #


def _kind(value: Any) -> str:
    key = str(value or "other").strip().lower()
    if key not in KINDS:
        raise ValueError(f"вид работы — один из: {', '.join(KINDS)}")
    return key


def _status(value: Any) -> str:
    key = str(value or "").strip().lower()
    aliases = {"на проверке": "review", "принято": "accepted", "отклонено": "rejected", "черновик": "draft",
               "approved": "accepted", "ok": "accepted"}
    key = aliases.get(key, key)
    if key not in STATUSES:
        raise ValueError(f"статус — один из: {', '.join(STATUSES)}")
    return key


def _by(value: Any, default: str = "owner") -> str:
    key = str(value or default).strip().lower()
    if key not in POSTED_BY:
        raise ValueError("кто — owner или agent")
    return key


def _project(store: Any, project_id: Any) -> int | None:
    if project_id in (None, "", 0):
        return None
    from app.projects_core import get_project

    get_project(store, int(project_id), include_sources=False)  # нет проекта — KeyError
    return int(project_id)


def _activity_now() -> str:
    """UTC с микросекундами: два события одной секунды всё равно идут по порядку."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


def _touch(store: Any, work_id: int) -> None:
    store.db.execute("UPDATE feed_works SET activity_at = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                     (_activity_now(), int(work_id)))


def _work_row(store: Any, work_id: int) -> dict:
    row = store.db.query_one("SELECT * FROM feed_works WHERE id = ?", (int(work_id),))
    if not row:
        raise KeyError(f"работы {work_id} нет в ленте")
    return dict(row)


def _version_row(store: Any, version_id: int) -> dict:
    row = store.db.query_one("SELECT * FROM feed_versions WHERE id = ?", (int(version_id),))
    if not row:
        raise KeyError(f"версии {version_id} нет в ленте")
    return dict(row)


def _variant_row(store: Any, variant_id: int) -> dict:
    row = store.db.query_one("SELECT * FROM feed_variants WHERE id = ?", (int(variant_id),))
    if not row:
        raise KeyError(f"варианта {variant_id} нет в ленте")
    return dict(row)


# --------------------------------------------------------------------------- #
# Работа
# --------------------------------------------------------------------------- #


def create_work(store: Any, title: str, project_id: Any = None, kind: Any = "other", made_by: Any = "owner") -> dict:
    name = _text(title, 200)
    if not name:
        raise ValueError("нужно название работы")
    cur = store.db.execute(
        "INSERT INTO feed_works (title, project_id, kind, made_by, activity_at) VALUES (?, ?, ?, ?, ?)",
        (name, _project(store, project_id), _kind(kind), _by(made_by), _activity_now()))
    return get_work(store, int(cur.lastrowid))


def update_work(store: Any, work_id: int, **fields: Any) -> dict:
    _work_row(store, work_id)
    updates: dict[str, Any] = {}
    if "title" in fields:
        updates["title"] = _text(fields["title"], 200)
        if not updates["title"]:
            raise ValueError("нужно название работы")
    if "project_id" in fields:
        updates["project_id"] = _project(store, fields["project_id"])
    if "kind" in fields:
        updates["kind"] = _kind(fields["kind"])
    if "cover_variant_id" in fields:
        cover = fields["cover_variant_id"]
        if cover not in (None, "", 0):
            row = store.db.query_one(
                "SELECT v.id FROM feed_variants v JOIN feed_versions ver ON ver.id = v.version_id "
                "WHERE v.id = ? AND ver.work_id = ?", (int(cover), int(work_id)))
            if not row:
                raise ValueError("обложка — вариант этой же работы")
        updates["cover_variant_id"] = None if cover in (None, "", 0) else int(cover)
    if updates:
        sets = ", ".join(f"{key} = ?" for key in updates)
        store.db.execute(f"UPDATE feed_works SET {sets}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (*updates.values(), int(work_id)))
    return get_work(store, work_id)


def delete_work(store: Any, work_id: int) -> dict:
    """Работа целиком: версии, варианты, свои файлы ленты. Ссылки на файлы сервера и клипы не трогает; отметки
    публикации остаются в проекте/клипе, а отметки, привязанные только к ленте, уходят."""
    _work_row(store, work_id)
    variants = [dict(r) for r in store.db.query_all(
        "SELECT v.* FROM feed_variants v JOIN feed_versions ver ON ver.id = v.version_id WHERE ver.work_id = ?",
        (int(work_id),))]
    with store.db.transaction():
        _drop_postings(store, [int(v["id"]) for v in variants])
        store.db.execute("DELETE FROM feed_works WHERE id = ?", (int(work_id),))
    removed = sum(_remove_variant_files(v) for v in variants)
    return {"deleted": True, "work_id": int(work_id), "files_removed": removed}


def _drop_postings(store: Any, variant_ids: list[int]) -> None:
    if not variant_ids:
        return
    marks = ",".join("?" * len(variant_ids))
    store.db.execute(f"DELETE FROM project_publications WHERE feed_variant_id IN ({marks}) "
                     "AND project_id IS NULL AND clip_plan_id IS NULL AND clip_id IS NULL", variant_ids)


def _remove_variant_files(variant: dict) -> int:
    paths = [variant.get("poster_path")]
    if variant.get("preview_path") and variant.get("preview_path") != variant.get("file_path"):
        paths.append(variant["preview_path"])
    if variant.get("owned"):
        paths.append(variant.get("file_path"))
    return F.remove_within_feed(*paths)


# --------------------------------------------------------------------------- #
# Версии и варианты
# --------------------------------------------------------------------------- #


def _probe(path: Path) -> dict:
    from app.ingest import probe_media

    try:
        meta = probe_media(path)
    except ValueError as exc:
        raise F.FeedFileError(f"{path.name}: не читается как видео ({exc})") from exc
    if not meta.width or not meta.height:
        raise F.FeedFileError(f"{path.name}: в файле нет видео")
    return {"duration_sec": float(meta.duration_sec or 0), "width": int(meta.width), "height": int(meta.height)}


def _resolve_media(store: Any, spec: dict) -> dict:
    """Что за файл у варианта: клип / файл сервера / импорт с хоста. До записи в базу — чтобы плохой путь не оставил
    полверсии. Импорт помечается ``move`` — перенос в папку ленты, когда версия уже есть."""
    given = [k for k in ("clip_id", "path", "host_path") if spec.get(k) not in (None, "")]
    if len(given) != 1:
        raise ValueError("у варианта один источник: clip_id, path (файл на сервере) или host_path (импорт с хоста)")
    if given[0] == "clip_id":
        clip = store.get_clip(int(spec["clip_id"]))  # нет клипа — KeyError
        if clip.get("status") != "succeeded" or not clip.get("output_path"):
            raise ValueError(f"клип {clip['id']} ещё не готов (статус {clip.get('status')})")
        path = Path(clip["output_path"])
        return {"clip_id": int(clip["id"]), "file_path": str(path), "owned": 0, "original_name": path.name,
                "size_bytes": clip.get("size_bytes"), "duration_sec": clip.get("duration_sec"),
                "width": clip.get("width"), "height": clip.get("height"), "title": clip.get("title") or ""}
    if given[0] == "path":
        path = F.server_path(str(spec["path"]))
        return {"clip_id": None, "file_path": str(path), "owned": 0, "original_name": path.name,
                "size_bytes": path.stat().st_size, **_probe(path)}
    path = F.import_path(str(spec["host_path"]))
    return {"clip_id": None, "file_path": str(path), "owned": 1, "original_name": path.name,
            "size_bytes": path.stat().st_size, "move": True, **_probe(path)}


def _label(spec: dict, media: dict, index: int) -> str:
    label = _text(spec.get("label"), 80)
    if label:
        return label
    width, height = media.get("width") or 0, media.get("height") or 0
    if width and height:
        return "9:16" if height > width else ("16:9" if width > height else "1:1")
    return f"Вариант {index + 1}"


def _insert_variant(store: Any, version: dict, spec: dict, media: dict, position: int) -> int:
    cur = store.db.execute(
        """INSERT INTO feed_variants (version_id, position, label, clip_id, file_path, owned, original_name,
                                      size_bytes, duration_sec, width, height)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (int(version["id"]), position, _label(spec, media, position), media.get("clip_id"), media["file_path"],
         int(media.get("owned") or 0), _text(media.get("original_name"), 200), media.get("size_bytes"),
         media.get("duration_sec"), media.get("width"), media.get("height")),
    )
    return int(cur.lastrowid)


def _next_position(store: Any, version_id: int) -> int:
    row = store.db.query_one("SELECT COALESCE(MAX(position), -1) + 1 AS n FROM feed_variants WHERE version_id = ?",
                             (int(version_id),))
    return int(row["n"])


def _after_variants(store: Any, moved: list[tuple[int, Path, Path]]) -> None:
    """Импорт: файл из папки импорта в папку версии (после записи в базу — номер версии уже есть)."""
    for variant_id, src, dest in moved:
        try:
            F.move_into(src, dest)
        except OSError as exc:
            store.db.execute("UPDATE feed_variants SET preview_status = 'failed', preview_error = ? WHERE id = ?",
                             (f"файл не перенесён: {exc}"[:300], variant_id))
            continue
        store.db.execute("UPDATE feed_variants SET file_path = ? WHERE id = ?", (str(dest), variant_id))


def add_version(store: Any, work_id: int, note: str = "", variants: list[dict] | None = None, label: str = "",
                status: Any = "review", made_by: Any = "owner") -> dict:
    """Новая версия работы; номер — следующий по порядку. Варианты — ссылки (``clip_id`` / ``path`` /
    ``host_path``), файлы загрузкой добавляются потом (``add_uploaded_variant``). Плохой вариант — отказ целиком."""
    work = _work_row(store, work_id)
    if _by(made_by) == "agent" and _status(status or "review") in ("accepted", "rejected"):
        raise ValueError("новая версия агента — draft или review; «принято»/«отклонено» ставит feed_set_status со "
                         "словами владельца")
    specs = [dict(v) for v in (variants or []) if isinstance(v, dict)]
    if len(specs) > MAX_VARIANTS:
        raise ValueError(f"не больше {MAX_VARIANTS} вариантов в версии")
    resolved = [_resolve_media(store, spec) for spec in specs]
    same = _same_as_latest(store, int(work_id), [m.get("clip_id") for m in resolved])
    if same is not None:
        # Те же клипы, что в последней версии (её уже добавила загрузка, app/upload_versions.py): второй раз ту же
        # версию не заводим — заметка и подпись агента ложатся на неё.
        updates = {k: v for k, v in (("note", _long_text(note, 2000)), ("label", _text(label, 40))) if v}
        if updates:
            sets = ", ".join(f"{k} = ?" for k in updates)
            store.db.execute(f"UPDATE feed_versions SET {sets} WHERE id = ?", (*updates.values(), same))
            _touch(store, work_id)
        return get_version(store, same)
    moved: list[tuple[int, Path, Path]] = []
    with store.db.transaction():
        row = store.db.query_one("SELECT COALESCE(MAX(n), 0) + 1 AS n FROM feed_versions WHERE work_id = ?",
                                 (int(work_id),))
        cur = store.db.execute(
            "INSERT INTO feed_versions (work_id, n, label, note, status, made_by) VALUES (?, ?, ?, ?, ?, ?)",
            (int(work_id), int(row["n"]), _text(label, 40), _long_text(note, 2000), _status(status or "review"),
             _by(made_by)),
        )
        version = _version_row(store, int(cur.lastrowid))
        folder = F.version_dir(int(work["id"]), int(version["id"]))
        for position, (spec, media) in enumerate(zip(specs, resolved)):
            variant_id = _insert_variant(store, version, spec, media, position)
            if media.get("move"):
                moved.append((variant_id, Path(media["file_path"]), F.unique_target(folder, media["original_name"])))
        _touch(store, work_id)
    _after_variants(store, moved)
    _kick_previews(store)
    return get_version(store, int(version["id"]))


def _same_as_latest(store: Any, work_id: int, clip_ids: list[Any]) -> int | None:
    """id последней версии работы, если в ней ровно эти клипы (и только клипы), иначе None."""
    if not clip_ids or not all(clip_ids):
        return None
    latest = store.db.query_one("SELECT id FROM feed_versions WHERE work_id = ? ORDER BY n DESC LIMIT 1", (int(work_id),))
    if not latest:
        return None
    rows = store.db.query_all("SELECT id, clip_id FROM feed_variants WHERE version_id = ?", (int(latest["id"]),))
    if sorted(int(c) for c in clip_ids) != sorted(int(r["clip_id"] or 0) for r in rows):
        return None
    return int(latest["id"])


def add_variant(store: Any, version_id: int, spec: dict) -> dict:
    """Ещё один вариант к готовой версии (ссылка: clip_id / path / host_path)."""
    version = _version_row(store, version_id)
    media = _resolve_media(store, dict(spec))
    moved: list[tuple[int, Path, Path]] = []
    with store.db.transaction():
        variant_id = _insert_variant(store, version, spec, media, _next_position(store, version_id))
        if media.get("move"):
            folder = F.version_dir(int(version["work_id"]), int(version["id"]))
            moved.append((variant_id, Path(media["file_path"]), F.unique_target(folder, media["original_name"])))
        _touch(store, int(version["work_id"]))
    _after_variants(store, moved)
    _kick_previews(store)
    return get_variant(store, variant_id)


def add_uploaded_variant(store: Any, version_id: int, tmp_path: Path, original_name: str, label: str = "") -> dict:
    """Загруженный файл (уже целиком во временной папке ленты) — вариант версии; файл переносится в её папку."""
    version = _version_row(store, version_id)
    tmp_path = Path(tmp_path)
    try:
        media = {"clip_id": None, "owned": 1, "original_name": F.safe_name(original_name),
                 "size_bytes": tmp_path.stat().st_size, **_probe(tmp_path)}
    except (F.FeedFileError, OSError):
        tmp_path.unlink(missing_ok=True)
        raise
    dest = F.move_into(tmp_path, F.unique_target(F.version_dir(int(version["work_id"]), int(version["id"])),
                                                 media["original_name"]))
    media["file_path"] = str(dest)
    with store.db.transaction():
        variant_id = _insert_variant(store, version, {"label": label}, media, _next_position(store, version_id))
        _touch(store, int(version["work_id"]))
    _kick_previews(store)
    return get_variant(store, variant_id)


def delete_version(store: Any, version_id: int) -> dict:
    version = _version_row(store, version_id)
    variants = [dict(r) for r in store.db.query_all("SELECT * FROM feed_variants WHERE version_id = ?",
                                                    (int(version_id),))]
    with store.db.transaction():
        _drop_postings(store, [int(v["id"]) for v in variants])
        store.db.execute("DELETE FROM feed_versions WHERE id = ?", (int(version_id),))
        _touch(store, int(version["work_id"]))
    return {"deleted": True, "version_id": int(version_id),
            "files_removed": sum(_remove_variant_files(v) for v in variants)}


def delete_variant(store: Any, variant_id: int) -> dict:
    variant = _variant_row(store, variant_id)
    with store.db.transaction():
        _drop_postings(store, [int(variant_id)])
        store.db.execute("DELETE FROM feed_variants WHERE id = ?", (int(variant_id),))
        store.db.execute("UPDATE feed_works SET cover_variant_id = NULL WHERE cover_variant_id = ?", (int(variant_id),))
    return {"deleted": True, "variant_id": int(variant_id), "files_removed": _remove_variant_files(variant)}


def set_status(store: Any, version_id: int, status: Any = None, verdict: Any = None, by: Any = "owner") -> dict:
    """Статус версии и/или вердикт владельца. Агент ставит «принято» / «отклонено» только со словами владельца."""
    version = _version_row(store, version_id)
    who = _by(by)
    updates: dict[str, Any] = {}
    if status not in (None, ""):
        updates["status"] = _status(status)
    if verdict is not None:
        updates["verdict"] = _long_text(verdict, 2000)
    if not updates:
        raise ValueError("нужен статус или вердикт")
    new_status = updates.get("status", version["status"])
    if who == "agent" and new_status in ("accepted", "rejected") and "status" in updates \
            and not updates.get("verdict", version["verdict"]):
        raise ValueError("«принято» и «отклонено» — решение владельца: передай его слова в verdict")
    sets = ", ".join(f"{key} = ?" for key in updates)
    with store.db.transaction():
        store.db.execute(f"UPDATE feed_versions SET {sets}, status_by = ?, status_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (*updates.values(), who, int(version_id)))
        _touch(store, int(version["work_id"]))
    return get_version(store, version_id)


# --------------------------------------------------------------------------- #
# Отметки публикации (project_publications)
# --------------------------------------------------------------------------- #


def mark_posted(store: Any, variant_id: int, platform: Any, url: Any = "", posted_at: Any = None,
                posted_by: Any = "owner", account: Any = "") -> dict:
    """Отметка «запощено»: ручная публикация проекта с ``feed_variant_id``. Вариант-клип — ещё и с клипом (экран
    клипа тоже покажет её); проект — проект работы."""
    from app.projects_stats import add_manual_publication

    variant = _variant_row(store, variant_id)
    version = _version_row(store, int(variant["version_id"]))
    work = _work_row(store, int(version["work_id"]))
    key = str(platform or "").strip().lower()
    if key not in PLATFORMS:
        raise ValueError(f"площадка — одна из: {', '.join(PLATFORMS)}")
    title = f"{work['title']} · {version['label'] or 'v' + str(version['n'])} · {variant['label']}"
    with store.db.transaction():
        pub = add_manual_publication(store, url=str(url or ""), project_id=work["project_id"], clip_id=variant["clip_id"],
                                     title=title, published_at=_date(posted_at) or None, platform=key, account=account,
                                     feed_variant_id=int(variant_id), posted_by=_by(posted_by))
        _touch(store, int(work["id"]))
    return _posting(pub, "feed")


def unmark_posted(store: Any, posting_id: int) -> dict:
    """Снять отметку ленты. Публикации, сделанные с экрана клипа или сервисом, отсюда не снимаются."""
    row = store.db.query_one("SELECT id, feed_variant_id FROM project_publications WHERE id = ?", (int(posting_id),))
    if not row or row["feed_variant_id"] is None:
        raise KeyError(f"отметки {posting_id} нет в ленте")
    variant = _variant_row(store, int(row["feed_variant_id"]))
    version = _version_row(store, int(variant["version_id"]))
    with store.db.transaction():
        store.db.execute("DELETE FROM project_publications WHERE id = ?", (int(posting_id),))
        _touch(store, int(version["work_id"]))
    return {"deleted": True, "posting_id": int(posting_id)}


def unmark_platform(store: Any, variant_id: int, platform: Any) -> dict:
    """Снять все отметки ленты варианта на площадке (инструмент агента: «это не выкладывали»)."""
    _variant_row(store, variant_id)
    rows = store.db.query_all("SELECT id FROM project_publications WHERE feed_variant_id = ? AND platform = ?",
                              (int(variant_id), str(platform or "").strip().lower()))
    for row in rows:
        unmark_posted(store, int(row["id"]))
    return {"deleted": len(rows), "variant_id": int(variant_id)}


def _posting(pub: dict, origin: str) -> dict:
    return {"id": pub.get("id"), "platform": pub.get("platform") or "other", "url": pub.get("url") or "",
            "posted_at": pub.get("published_at") or pub.get("finished_at") or "", "posted_by": pub.get("posted_by")
            or ("service" if origin == "service" else ""), "account": pub.get("account") or "", "origin": origin,
            "removable": origin == "feed"}


def _postings_map(store: Any, variants: list[dict]) -> dict[int, list[dict]]:
    """Отметки по вариантам: свои (лента), ручные с экрана клипа и публикации сервиса того же клипа."""
    out: dict[int, list[dict]] = {int(v["id"]): [] for v in variants}
    if not variants:
        return out
    ids = list(out)
    marks = ",".join("?" * len(ids))
    for row in store.db.query_all(f"SELECT * FROM project_publications WHERE feed_variant_id IN ({marks}) ORDER BY id",
                                  ids):
        out[int(row["feed_variant_id"])].append(_posting(dict(row), "feed"))
    by_clip: dict[int, list[int]] = {}
    for v in variants:
        if v.get("clip_id"):
            by_clip.setdefault(int(v["clip_id"]), []).append(int(v["id"]))
    if by_clip:
        clip_marks = ",".join("?" * len(by_clip))
        for row in store.db.query_all(
                f"SELECT * FROM project_publications WHERE clip_id IN ({clip_marks}) AND feed_variant_id IS NULL "
                "ORDER BY id", list(by_clip)):
            for vid in by_clip[int(row["clip_id"])]:
                out[vid].append(_posting(dict(row), "clip"))
        for row in store.db.query_all(
                f"""SELECT jt.id, jt.platform, jt.remote_url AS url, jt.finished_at, j.clip_id,
                           COALESCE((SELECT a.label FROM accounts a WHERE a.id = jt.account_id), '') AS account
                    FROM job_targets jt JOIN jobs j ON j.id = jt.job_id
                    WHERE j.clip_id IN ({clip_marks}) AND jt.status = 'succeeded' ORDER BY jt.id""", list(by_clip)):
            for vid in by_clip[int(row["clip_id"])]:
                out[vid].append(_posting(dict(row), "service"))
    return out


# --------------------------------------------------------------------------- #
# Чтение
# --------------------------------------------------------------------------- #


def _variant_clip(store: Any, variant: dict) -> dict | None:
    """Клип сервиса, из которого взят вариант (если он ещё есть)."""
    if not variant.get("clip_id"):
        return None
    try:
        return store.get_clip(int(variant["clip_id"]))
    except KeyError:
        return None


def variant_media_path(store: Any, variant: dict, clip: dict | None = None) -> Path | None:
    """Полный файл варианта: файл клипа (если клип ещё есть), иначе сохранённый путь."""
    clip = clip if clip is not None else _variant_clip(store, variant)
    if clip and clip.get("output_path") and Path(clip["output_path"]).exists():
        return Path(clip["output_path"])
    raw = str(variant.get("file_path") or "")
    return Path(raw) if raw and Path(raw).exists() else None


def _variant_view(store: Any, variant: dict, postings: list[dict]) -> dict:
    vid = int(variant["id"])
    has_preview = variant["preview_status"] == "ready" and bool(variant.get("preview_path"))
    has_poster = bool(variant.get("poster_path"))
    clip = _variant_clip(store, variant)
    present = variant_media_path(store, variant, clip) is not None
    return {
        "id": vid, "version_id": int(variant["version_id"]), "label": variant["label"], "position": variant["position"],
        "clip_id": variant["clip_id"],
        # План клипа — экран клипа в сервисе (/clips/<id>): из ленты можно вернуться туда, где работу делали.
        "clip_plan_id": (clip or {}).get("clip_plan_id"),
        "source": "clip" if variant["clip_id"] else ("file" if variant["owned"] else
                                                                                    "server"),
        "original_name": variant["original_name"], "size_bytes": variant["size_bytes"],
        "duration_sec": variant["duration_sec"], "width": variant["width"], "height": variant["height"],
        "orientation": ("vertical" if (variant["height"] or 0) > (variant["width"] or 0) else "horizontal"),
        "file_present": present,
        "preview": {"status": variant["preview_status"], "error": variant["preview_error"],
                    "error_view": error_text.explain(variant["preview_error"]),  # простыми словами
                    "url": f"/media/feed/variants/{vid}/preview" if has_preview else None,
                    "same_as_file": has_preview and variant["preview_path"] == variant["file_path"]},
        "poster_url": f"/media/feed/variants/{vid}/poster" if has_poster else None,
        "download_url": f"/media/feed/variants/{vid}/file" if present else None,
        "postings": postings,
        "created_at": variant["created_at"],
    }


def _version_view(version: dict, variants: list[dict]) -> dict:
    posted = sorted({p["platform"] for v in variants for p in v["postings"]})
    return {
        "id": int(version["id"]), "work_id": int(version["work_id"]), "n": version["n"], "label": version["label"],
        "name": version["label"] or f"v{version['n']}", "note": version["note"], "status": version["status"],
        "status_label": STATUSES.get(version["status"], version["status"]), "verdict": version["verdict"],
        "status_by": version["status_by"], "status_at": version["status_at"], "made_by": version["made_by"],
        "created_at": version["created_at"], "posted_platforms": posted, "variants": variants,
    }


def _load(store: Any, work_ids: list[int] | None = None) -> list[dict]:
    """Работы со всеми версиями и вариантами — несколькими запросами на всю ленту (работ десятки, не тысячи)."""
    where, params = "", []
    if work_ids is not None:
        if not work_ids:
            return []
        where, params = f"WHERE w.id IN ({','.join('?' * len(work_ids))})", list(work_ids)
    works = [dict(r) for r in store.db.query_all(
        f"SELECT w.*, p.name AS project_name FROM feed_works w LEFT JOIN projects p ON p.id = w.project_id {where}",
        params)]
    if not works:
        return []
    ids = [int(w["id"]) for w in works]
    marks = ",".join("?" * len(ids))
    versions = [dict(r) for r in store.db.query_all(
        f"SELECT * FROM feed_versions WHERE work_id IN ({marks}) ORDER BY n DESC", ids)]
    variants: list[dict] = []
    if versions:
        vmarks = ",".join("?" * len(versions))
        variants = [dict(r) for r in store.db.query_all(
            f"SELECT * FROM feed_variants WHERE version_id IN ({vmarks}) ORDER BY position, id",
            [int(v["id"]) for v in versions])]
    postings = _postings_map(store, variants)
    by_version: dict[int, list[dict]] = {}
    for variant in variants:
        by_version.setdefault(int(variant["version_id"]), []).append(
            _variant_view(store, variant, postings.get(int(variant["id"]), [])))
    by_work: dict[int, list[dict]] = {}
    for version in versions:
        by_work.setdefault(int(version["work_id"]), []).append(
            _version_view(version, by_version.get(int(version["id"]), [])))
    out = []
    for work in works:
        out.append(_work_view(work, by_work.get(int(work["id"]), [])))
    return out


def _cover(work: dict, versions: list[dict]) -> str | None:
    all_variants = [v for ver in versions for v in ver["variants"]]
    if work.get("cover_variant_id"):
        for variant in all_variants:
            if variant["id"] == work["cover_variant_id"] and variant["poster_url"]:
                return variant["poster_url"]
    for variant in all_variants:  # версии уже новые сверху
        if variant["poster_url"]:
            return variant["poster_url"]
    return None


def _work_view(work: dict, versions: list[dict]) -> dict:
    latest = versions[0] if versions else None
    accepted = next((v for v in versions if v["status"] == "accepted"), None)
    posted = sorted({p for v in versions for p in v["posted_platforms"]})
    return {
        "id": int(work["id"]), "title": work["title"], "project_id": work["project_id"],
        "project_name": work.get("project_name") or "", "kind": work["kind"],
        "kind_label": KINDS.get(work["kind"], work["kind"]), "cover_url": _cover(work, versions),
        "cover_variant_id": work["cover_variant_id"], "made_by": work["made_by"],
        "created_at": work["created_at"], "activity_at": work["activity_at"],
        "versions_count": len(versions),
        "latest": ({"id": latest["id"], "n": latest["n"], "name": latest["name"], "status": latest["status"],
                    "status_label": latest["status_label"], "variants": len(latest["variants"]),
                    "created_at": latest["created_at"]} if latest else None),
        "posted_platforms": posted,
        # Готово, не выложено: последняя принятая версия нигде не отмечена.
        "ready_unposted": bool(accepted and not accepted["posted_platforms"]),
        "versions": versions,
    }


def get_work(store: Any, work_id: int) -> dict:
    works = _load(store, [int(work_id)])
    if not works:
        raise KeyError(f"работы {work_id} нет в ленте")
    return works[0]


def get_version(store: Any, version_id: int) -> dict:
    version = _version_row(store, version_id)
    work = get_work(store, int(version["work_id"]))
    found = next(v for v in work["versions"] if v["id"] == int(version_id))
    return {**found, "work_title": work["title"]}


def get_variant(store: Any, variant_id: int) -> dict:
    variant = _variant_row(store, variant_id)
    return _variant_view(store, variant, _postings_map(store, [variant]).get(int(variant_id), []))


def _matches(work: dict, flt: str) -> bool:
    latest = work["latest"]
    if flt == "ready_unposted":
        return work["ready_unposted"]
    if flt == "posted":
        return bool(work["posted_platforms"])
    if flt in ("review", "draft", "rejected", "accepted"):
        return bool(latest and latest["status"] == flt)
    return True


def list_feed(store: Any, filter: str = "", project_id: Any = None, kind: str = "", q: str = "",
              limit: int = 50, offset: int = 0, with_versions: bool = False) -> dict:
    """Карточки ленты, свежие сверху (по последнему событию). ``filter``: ready_unposted / review / posted / draft /
    rejected / accepted; ``counts`` — сколько под каждым фильтром при тех же проекте, виде и поиске."""
    flt = str(filter or "").strip().lower()
    if flt and flt not in FILTERS and flt != "accepted":
        raise ValueError(f"фильтр — один из: {', '.join(FILTERS)}")
    works = _load(store)
    if project_id not in (None, "", 0):
        works = [w for w in works if w["project_id"] == int(project_id)]
    if kind:
        kind_key = _kind(kind)
        works = [w for w in works if w["kind"] == kind_key]
    if q:
        needle = str(q).strip().lower()
        works = [w for w in works if needle in w["title"].lower() or needle in w["project_name"].lower()]
    counts = {"all": len(works), **{key: sum(1 for w in works if _matches(w, key)) for key in FILTERS}}
    if flt:
        works = [w for w in works if _matches(w, flt)]
    works.sort(key=lambda w: (str(w["activity_at"] or ""), w["id"]), reverse=True)
    total = len(works)
    limit = max(1, min(200, int(limit or 50)))
    offset = max(0, int(offset or 0))
    page = works[offset:offset + limit]
    if not with_versions:
        page = [{k: v for k, v in w.items() if k != "versions"} for w in page]
    return {"items": page, "total": total, "offset": offset, "counts": counts,
            "filters": FILTERS, "kinds": KINDS, "statuses": STATUSES}


def _kick_previews(store: Any) -> None:
    try:
        from app import feed_preview

        feed_preview.kick(store)
    except Exception:  # noqa: BLE001 - превью — фон; запись версии важнее
        log.warning("feed preview kick failed", exc_info=True)
