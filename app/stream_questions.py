"""Вопросы по стриму и выбор мемов: монтажёр спрашивает, владелец отвечает нажатием.

Владелец: «агент смотрит весь стрим, формирует список вопросов и варианты ответа и у меня
спрашивает, чтобы я объяснял, что происходит. Мемов не хватает, такую же систему с
вариантами». Вопросы и варианты заполняет агент-монтажёр через инструменты (app/stream_question_tools.py),
ответы читает перед отбором моментов. Модель тут не зашита.

Ответ со ``scope="channel"`` — про канал вообще («кто такой Глеб») — идёт в бриф проекта
(app/projects.py), чтобы не спрашивать на каждом стриме. ``scope="stream"`` — только про этот стрим.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.meme_catalog import CatalogError, fetch_template
from app.projects import project_of_source
from app.settings import settings

KINDS = ("who", "context", "joke", "keep", "other")
SCOPES = ("stream", "channel")
STATUSES = ("open", "answered", "skipped")
MOMENT_STATUSES = ("open", "chosen", "none")
MAX_QUESTIONS_PER_CALL = 60
MAX_MOMENTS_PER_CALL = 30
MIN_OPTIONS, MAX_OPTIONS = 2, 6
MIN_MEME_OPTIONS, MAX_MEME_OPTIONS = 2, 4
MAX_TEXT = 1000
CLIP_PAD_SEC = 3.0
MAX_CLIP_SEC = 60.0
CLIP_HEIGHT = 480
THUMB_WIDTH = 480
MAX_CLIP_CACHE_BYTES = 3 * 1024 ** 3
MAX_URL_IMAGE_BYTES = 20 * 1024 * 1024
FFMPEG_TIMEOUT_SEC = 180

SCHEMA_SQL = (
    """
    CREATE TABLE IF NOT EXISTS stream_questions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
        kind TEXT NOT NULL DEFAULT 'context',
        t_start REAL NOT NULL DEFAULT 0,
        t_end REAL NOT NULL DEFAULT 0,
        question TEXT NOT NULL,
        question_key TEXT NOT NULL,
        options_json TEXT NOT NULL DEFAULT '[]',
        scope TEXT NOT NULL DEFAULT 'stream',
        about TEXT NOT NULL DEFAULT '',
        answer_option INTEGER,
        answer_text TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'open',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        answered_at TEXT,
        UNIQUE (source_id, question_key)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_stream_questions_source ON stream_questions(source_id, t_start, id)",
    """
    CREATE TABLE IF NOT EXISTS meme_moments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
        t_start REAL NOT NULL DEFAULT 0,
        t_end REAL NOT NULL DEFAULT 0,
        why TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'open',
        chosen_option_id INTEGER,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        chosen_at TEXT,
        UNIQUE (source_id, t_start, why)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_meme_moments_source ON meme_moments(source_id, t_start, id)",
    """
    CREATE TABLE IF NOT EXISTS meme_options (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        moment_id INTEGER NOT NULL REFERENCES meme_moments(id) ON DELETE CASCADE,
        position INTEGER NOT NULL DEFAULT 0,
        title TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT '',
        file_path TEXT NOT NULL DEFAULT '',
        mime TEXT NOT NULL DEFAULT '',
        asset_id INTEGER,
        source TEXT NOT NULL DEFAULT '',
        license TEXT NOT NULL DEFAULT '',
        origin_url TEXT NOT NULL DEFAULT ''
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_meme_options_moment ON meme_options(moment_id, position)",
)


def ensure_stream_questions_schema(conn: Any) -> None:
    for sql in SCHEMA_SQL:
        conn.execute(sql)


def _text(value: Any, limit: int = 500) -> str:
    return " ".join(str(value if value is not None else "").split())[:limit]


def _body(value: Any, limit: int = MAX_TEXT) -> str:
    return str(value if value is not None else "").replace("\x00", "").strip()[:limit]


def _key(text: str) -> str:
    return re.sub(r"\W+", " ", text.lower()).strip()


def _times(source: dict, start: Any, end: Any) -> tuple[float, float]:
    try:
        a = float(start)
        b = float(end if end not in (None, "") else a)
    except (TypeError, ValueError) as exc:
        raise ValueError("t_start и t_end — секунды исходника") from exc
    if a != a or b != b or a < 0:
        raise ValueError("t_start и t_end — секунды исходника")
    duration = float(source.get("duration_sec") or 0)
    if duration and a > duration:
        raise ValueError(f"секунда {a:g} за концом исходника ({duration:g} с)")
    b = max(a, min(b, duration) if duration else b)
    return round(a, 2), round(b, 2)


def _options(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        raise ValueError("options — список вариантов ответа")
    out: list[str] = []
    for item in raw:
        text = _text(item, 200)
        if text and text.lower() not in {x.lower() for x in out}:
            out.append(text)
    if not MIN_OPTIONS <= len(out) <= MAX_OPTIONS:
        raise ValueError(f"нужно {MIN_OPTIONS}–{MAX_OPTIONS} разных вариантов ответа")
    return out


# --------------------------------------------------------------------------- #
# Вопросы
# --------------------------------------------------------------------------- #


def _question(row: Any) -> dict:
    import json

    item = dict(row)
    item.pop("question_key", None)
    item["options"] = json.loads(item.pop("options_json") or "[]")
    item["answer"] = answer_value(item)
    return item


def answer_value(item: dict) -> str:
    """Ответ словами: вариант и то, что владелец дописал сам."""
    options = item.get("options") or []
    picked = item.get("answer_option")
    parts = []
    if picked is not None and 0 <= int(picked) < len(options):
        parts.append(options[int(picked)])
    if item.get("answer_text"):
        parts.append(str(item["answer_text"]))
    return ". ".join(parts)


def add_questions(store: Any, source_id: int, questions: Any) -> dict:
    import json

    source = store.get_source(int(source_id))
    if not isinstance(questions, list) or not questions:
        raise ValueError("questions — непустой список")
    if len(questions) > MAX_QUESTIONS_PER_CALL:
        raise ValueError(f"за раз не больше {MAX_QUESTIONS_PER_CALL} вопросов")
    prepared = []
    for index, raw in enumerate(questions):
        if not isinstance(raw, dict):
            raise ValueError(f"вопрос {index}: нужен объект")
        text = _text(raw.get("question"), 500)
        if len(text) < 3:
            raise ValueError(f"вопрос {index}: пустой текст")
        kind = str(raw.get("kind") or "context")
        scope = str(raw.get("scope") or "stream")
        if kind not in KINDS:
            raise ValueError(f"вопрос {index}: kind — {', '.join(KINDS)}")
        if scope not in SCOPES:
            raise ValueError(f"вопрос {index}: scope — {', '.join(SCOPES)}")
        try:
            options = _options(raw.get("options"))
            a, b = _times(source, raw.get("t_start"), raw.get("t_end"))
        except ValueError as exc:
            raise ValueError(f"вопрос {index}: {exc}") from exc
        prepared.append((text, kind, scope, _text(raw.get("about"), 120), options, a, b))
    added, duplicates = [], []
    for text, kind, scope, about, options, a, b in prepared:
        key = _key(text)
        old = store.db.query_one("SELECT id FROM stream_questions WHERE source_id = ? AND question_key = ?",
                                 (int(source_id), key))
        if old:
            duplicates.append(int(old["id"]))
            continue
        cur = store.db.execute(
            "INSERT INTO stream_questions (source_id, kind, t_start, t_end, question, question_key, options_json, "
            "scope, about) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (int(source_id), kind, a, b, text, key, json.dumps(options, ensure_ascii=False), scope, about),
        )
        added.append(int(cur.lastrowid))
    return {"added": added, "duplicates": duplicates}


def list_questions(store: Any, source_id: int, status: str | None = None) -> list[dict]:
    store.get_source(int(source_id))
    sql, params = "SELECT * FROM stream_questions WHERE source_id = ?", [int(source_id)]
    if status:
        if status not in STATUSES:
            raise ValueError(f"status — {', '.join(STATUSES)}")
        sql += " AND status = ?"
        params.append(status)
    return [_question(r) for r in store.db.query_all(sql + " ORDER BY t_start, id", params)]


def get_question(store: Any, question_id: int) -> dict:
    row = store.db.query_one("SELECT * FROM stream_questions WHERE id = ?", (int(question_id),))
    if not row:
        raise KeyError(f"вопроса нет: {question_id}")
    return _question(row)


def _brief_patch(item: dict, value: str, current_notes: str) -> dict:
    """Что дописать в бриф проекта. Кто-то с именем — в people, шутка — в jokes, остальное — строка в notes."""
    about = item.get("about") or ""
    if item["kind"] == "who" and about:
        return {"people": [{"name": about, "who": value}]}
    if item["kind"] == "joke":
        return {"jokes": [{"text": about or item["question"], "about": value}]}
    prefix = f"{item['question']} — "
    lines = [line for line in current_notes.splitlines() if line.strip() and not line.startswith(prefix)]
    return {"notes": "\n".join([*lines, prefix + value])}


def _write_brief(store: Any, item: dict) -> dict:
    from app import projects as P

    project_id = P.project_of_source(store, int(item["source_id"]))
    if project_id is None:
        return {"written": False, "reason": "источник не в проекте: ответ остался только у этого стрима"}
    current = P.get_brief(store, project_id)["brief"]
    patch = _brief_patch(item, answer_value(item), current.get("notes") or "")
    P.update_brief(store, project_id, patch)
    return {"written": True, "project_id": project_id, "section": next(iter(patch))}


def answer_question(store: Any, question_id: int, option: Any = None, text: Any = "", skip: bool = False) -> dict:
    item = get_question(store, question_id)
    custom = _body(text)
    picked = None
    if option not in (None, ""):
        try:
            picked = int(option)
        except (TypeError, ValueError) as exc:
            raise ValueError("option — номер варианта") from exc
        if not 0 <= picked < len(item["options"]):
            raise ValueError("такого варианта нет")
    if skip:
        status = "skipped"
    elif picked is None and not custom:
        status = "open"  # снял выбор и стёр текст — вопрос снова открыт
    else:
        status = "answered"
    store.db.execute(
        "UPDATE stream_questions SET answer_option = ?, answer_text = ?, status = ?, "
        "answered_at = CASE WHEN ? = 'open' THEN NULL ELSE CURRENT_TIMESTAMP END WHERE id = ?",
        (None if skip else picked, "" if skip else custom, status, status, int(question_id)),
    )
    fresh = get_question(store, question_id)
    brief: dict[str, Any] = {"written": False}
    if status == "answered" and fresh["scope"] == "channel":
        try:
            brief = _write_brief(store, fresh)
        except Exception as exc:  # noqa: BLE001 - ответ уже сохранён, бриф можно дописать позже
            brief = {"written": False, "reason": str(exc)[:200]}
    return {**fresh, "brief": brief}


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


# --------------------------------------------------------------------------- #
# Что читает монтажёр и что видит страница
# --------------------------------------------------------------------------- #


def progress(questions: list[dict], moments: list[dict]) -> dict:
    count = lambda rows, status: sum(1 for r in rows if r["status"] == status)  # noqa: E731
    return {
        "questions": {"total": len(questions), "answered": count(questions, "answered"),
                      "skipped": count(questions, "skipped"), "open": count(questions, "open")},
        "memes": {"total": len(moments), "chosen": count(moments, "chosen"), "none": count(moments, "none"),
                  "open": count(moments, "open")},
    }


def page_state(store: Any, source_id: int) -> dict:
    from app import projects as P

    source = store.get_source(int(source_id))
    questions = list_questions(store, source_id)
    moments = list_moments(store, source_id)
    project_id = P.project_of_source(store, int(source_id))
    return {
        "source": {"id": source["id"], "title": source.get("original_filename") or source.get("original_url") or "",
                   "duration": source.get("duration_sec") or 0},
        "project_id": project_id,
        "questions": questions,
        "moments": moments,
        "progress": progress(questions, moments),
    }


def answers_brief(store: Any, source_id: int) -> dict:
    """Короткий контекст стрима для монтажёра: только то, на что владелец ответил."""
    questions = list_questions(store, source_id)
    moments = list_moments(store, source_id)
    answers = [{"id": q["id"], "t_start": q["t_start"], "t_end": q["t_end"], "kind": q["kind"], "scope": q["scope"],
                "question": q["question"], "answer": q["answer"]}
               for q in questions if q["status"] == "answered"]
    memes = []
    for moment in moments:
        if moment["status"] == "open":
            continue
        chosen = next((o for o in moment["options"] if o["id"] == moment["chosen_option_id"]), None)
        memes.append({"id": moment["id"], "t_start": moment["t_start"], "t_end": moment["t_end"], "why": moment["why"],
                      "status": moment["status"],
                      **({"asset_id": chosen["asset_id"], "title": chosen["title"], "license": chosen["license"],
                          "source": chosen["source"]} if chosen else {})})
    return {"answers": answers, "memes": memes, "progress": progress(questions, moments)}


# --------------------------------------------------------------------------- #
# Кусочек исходника вокруг момента
# --------------------------------------------------------------------------- #

_ffmpeg_slots = threading.BoundedSemaphore(2)
_clip_locks: dict[str, threading.Lock] = {}
_clip_locks_guard = threading.Lock()


def clips_dir() -> Path:
    return settings.data_dir / "stream_questions" / "clips"


def _source_file(store: Any, source_id: int) -> tuple[dict, Path]:
    source = store.get_source(int(source_id))
    path = Path(source.get("local_path") or "")
    if not path.is_file():
        raise KeyError("файла записи больше нет")
    try:
        path.resolve(strict=False).relative_to(settings.source_dir.resolve())
    except ValueError as exc:
        raise KeyError("файл записи вне папки исходников") from exc
    return source, path


def _span(source: dict, t_start: float, t_end: float) -> tuple[float, float]:
    duration = float(source.get("duration_sec") or 0)
    start = max(0.0, float(t_start) - CLIP_PAD_SEC)
    end = float(t_end) + CLIP_PAD_SEC
    if duration:
        end = min(end, duration)
    return round(start, 1), max(1.0, round(min(end - start, MAX_CLIP_SEC), 1))


def _trim_cache(keep: Path) -> None:
    folder = keep.parent
    files = sorted((p for p in folder.iterdir() if p.is_file() and p != keep),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    total = keep.stat().st_size
    for path in files:
        total += path.stat().st_size
        if total > MAX_CLIP_CACHE_BYTES:
            path.unlink(missing_ok=True)


def _run_ffmpeg(args: list[str]) -> None:
    with _ffmpeg_slots:
        subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y", *args], check=True,
                       timeout=FFMPEG_TIMEOUT_SEC, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def preview_clip(store: Any, source_id: int, t_start: float, t_end: float) -> Path:
    """Лёгкий кусочек записи вокруг момента. Кэш на диске, чтобы ради вопроса не тянуть 12 ГБ стрима."""
    source, video = _source_file(store, source_id)
    start, length = _span(source, t_start, t_end)
    folder = clips_dir()
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"{int(source_id)}_{int(start * 10)}_{int(length * 10)}.mp4"
    with _clip_locks_guard:
        lock = _clip_locks.setdefault(out.name, threading.Lock())
    with lock:
        if not out.is_file():
            tmp = out.with_name(out.stem + f".{uuid4().hex}.tmp.mp4")
            try:
                _run_ffmpeg(["-ss", f"{start:.2f}", "-i", str(video), "-t", f"{length:.2f}", "-map", "0:v:0", "-map", "0:a:0?",
                             "-vf", f"scale=-2:{CLIP_HEIGHT}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
                             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(tmp)])
                tmp.replace(out)
            finally:
                tmp.unlink(missing_ok=True)
        _trim_cache(out)
    return out


def preview_thumb(store: Any, source_id: int, t: float) -> Path:
    _source, video = _source_file(store, source_id)
    moment = max(0.0, float(t))
    folder = clips_dir().parent / "thumbs"
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"{int(source_id)}_{int(moment * 2)}.jpg"
    if not out.is_file():
        tmp = out.with_name(out.stem + f".{uuid4().hex}.tmp.jpg")
        try:
            _run_ffmpeg(["-ss", f"{moment:.2f}", "-i", str(video), "-frames:v", "1", "-vf", f"scale={THUMB_WIDTH}:-2",
                         "-q:v", "5", str(tmp)])
            tmp.replace(out)
        finally:
            tmp.unlink(missing_ok=True)
    return out
