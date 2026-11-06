"""Вопросы стрима: схема, вопросы, запись в бриф (из ``app/stream_questions.py``)."""

from __future__ import annotations

import re
from typing import Any

KINDS = ("who", "context", "joke", "keep", "other")
SCOPES = ("stream", "channel", "clip")
LEVELS = ("project", "video", "clip")
LEVEL_OF_SCOPE = {"channel": "project", "stream": "video", "clip": "clip"}
SCOPE_OF_LEVEL = {level: scope for scope, level in LEVEL_OF_SCOPE.items()}


STATUSES = ("open", "answered", "skipped")


MAX_QUESTIONS_PER_CALL = 60


MIN_OPTIONS, MAX_OPTIONS = 2, 6


MAX_TEXT = 1000


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
    # Решение владельца «Монтировать без ответов»: покрывает вопросы видео до up_to_question_id.
    """
    CREATE TABLE IF NOT EXISTS stream_montage_release (
        source_id INTEGER PRIMARY KEY REFERENCES sources(id) ON DELETE CASCADE,
        up_to_question_id INTEGER NOT NULL DEFAULT 0,
        released_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
)


def ensure_stream_questions_schema(conn: Any) -> None:
    for sql in SCHEMA_SQL:
        conn.execute(sql)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(stream_questions)")}
    if "clip_plan_id" not in columns:
        conn.execute("ALTER TABLE stream_questions ADD COLUMN clip_plan_id INTEGER "
                     "REFERENCES clip_plans(id) ON DELETE SET NULL")
    if "optional" not in columns:
        conn.execute("ALTER TABLE stream_questions ADD COLUMN optional INTEGER NOT NULL DEFAULT 0")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_stream_questions_clip ON stream_questions(clip_plan_id)")


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
    item["level"] = LEVEL_OF_SCOPE.get(item.get("scope"), "video")
    item["optional"] = bool(item.get("optional"))
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


def _clip_family(store: Any, clip_plan_id: int) -> set[int]:
    """Клип и его версии (app/clip_groups.py): вопрос про клип не теряется при новой версии."""
    row = store.db.query_one("SELECT clip_group_id FROM clip_plans WHERE id = ?", (int(clip_plan_id),))
    if not row or row["clip_group_id"] is None:
        return {int(clip_plan_id)}
    rows = store.db.query_all("SELECT id FROM clip_plans WHERE clip_group_id = ?", (int(row["clip_group_id"]),))
    return {int(r["id"]) for r in rows} | {int(clip_plan_id)}


def _clip_of_source(store: Any, source_id: int, clip_id: Any) -> int:
    try:
        plan = store.get_clip_plan(int(clip_id), include_segments=False)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"клипа нет: {clip_id}") from exc
    if int(plan["source_id"]) != int(source_id):
        raise ValueError(f"клип #{clip_id} не из этого видео")
    return int(plan["id"])


def _asked_scope(raw: dict) -> tuple[str, Any]:
    """Уровень вопроса от агента: ``level`` (project|video|clip), старое ``scope`` тоже понимаем."""
    clip_id = raw.get("clip_id") if raw.get("clip_id") not in (None, "") else raw.get("clip_plan_id")
    clip_id = None if clip_id in (None, "") else clip_id
    if raw.get("level"):
        if str(raw["level"]) not in LEVELS:
            raise ValueError(f"level — {', '.join(LEVELS)}")
        scope = SCOPE_OF_LEVEL[str(raw["level"])]
    elif raw.get("scope"):
        scope = str(raw["scope"])
        if scope not in SCOPES:
            raise ValueError(f"scope — {', '.join(SCOPES)}")
    else:
        scope = "clip" if clip_id is not None else "stream"
    if scope == "clip" and clip_id is None:
        raise ValueError("вопрос про клип: нужен clip_id")
    return scope, clip_id


def add_questions(store: Any, source_id: int, questions: Any) -> dict:
    import json

    from app import projects as P

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
        if kind not in KINDS:
            raise ValueError(f"вопрос {index}: kind — {', '.join(KINDS)}")
        try:
            scope, clip_id = _asked_scope(raw)
            clip_plan_id = _clip_of_source(store, int(source_id), clip_id) if scope == "clip" else None
            options = _options(raw.get("options"))
            a, b = _times(source, raw.get("t_start"), raw.get("t_end"))
        except ValueError as exc:
            raise ValueError(f"вопрос {index}: {exc}") from exc
        prepared.append((text, kind, scope, clip_plan_id, raw.get("optional") is True, _text(raw.get("about"), 120),
                         options, a, b))
    project_id = P.project_of_source(store, int(source_id))
    added, duplicates = [], []
    for text, kind, scope, clip_plan_id, optional, about, options, a, b in prepared:
        key = _key(text)
        old = store.db.query_one("SELECT id FROM stream_questions WHERE source_id = ? AND question_key = ?",
                                 (int(source_id), key))
        if not old and scope == "channel" and project_id is not None:
            # Вопрос про проект задают один раз на проект, а не на каждом стриме.
            old = store.db.query_one(
                "SELECT q.id FROM stream_questions q JOIN sources s ON s.id = q.source_id "
                "WHERE s.project_id = ? AND q.scope = 'channel' AND q.question_key = ?", (int(project_id), key))
        if old:
            duplicates.append(int(old["id"]))
            continue
        cur = store.db.execute(
            "INSERT INTO stream_questions (source_id, kind, t_start, t_end, question, question_key, options_json, "
            "scope, clip_plan_id, optional, about) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (int(source_id), kind, a, b, text, key, json.dumps(options, ensure_ascii=False), scope, clip_plan_id,
             1 if optional else 0, about),
        )
        added.append(int(cur.lastrowid))
    return {"added": added, "duplicates": duplicates}


def list_questions(store: Any, source_id: int, status: str | None = None, level: str | None = None) -> list[dict]:
    store.get_source(int(source_id))
    sql, params = "SELECT * FROM stream_questions WHERE source_id = ?", [int(source_id)]
    if status:
        if status not in STATUSES:
            raise ValueError(f"status — {', '.join(STATUSES)}")
        sql += " AND status = ?"
        params.append(status)
    if level:
        if level not in LEVELS:
            raise ValueError(f"level — {', '.join(LEVELS)}")
        sql += " AND scope = ?"
        params.append(SCOPE_OF_LEVEL[level])
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
    # Строка заметок на один ответ: перенос в ответе оставил бы хвост, который потом не снять.
    return {"notes": "\n".join([*lines, prefix + " ".join(value.split())])}


def _write_brief(store: Any, item: dict) -> dict:
    from app import projects as P

    project_id = P.project_of_source(store, int(item["source_id"]))
    if project_id is None:
        return {"written": False, "reason": "источник не в проекте: ответ остался только у этого стрима"}
    current = P.get_brief(store, project_id)["brief"]
    patch = _brief_patch(item, answer_value(item), current.get("notes") or "")
    fresh = P.update_brief(store, project_id, patch)["brief"]
    if not in_brief(item, fresh):
        return {"written": False, "project_id": project_id,
                "reason": "В лоре проекта не осталось места: ответ пока только у этого видео"}
    return {"written": True, "project_id": project_id, "section": next(iter(patch))}


def brief_place(item: dict) -> str:
    """Где в брифе лежит ответ про канал: ``people:имя``, ``jokes:текст`` или ``notes:начало строки``
    (та же раскладка, что у ``_brief_patch``). Экран «Что агент знает» по ней находит вопрос строки."""
    about = item.get("about") or ""
    if item["kind"] == "who" and about:
        return f"people:{about.lower()}"
    if item["kind"] == "joke":
        return f"jokes:{(about or item['question']).lower()}"
    return f"notes:{item['question']} — "


def in_brief(item: dict, brief: dict) -> bool:
    place = brief_place(item)
    section, _, value = place.partition(":")
    if section == "notes":
        return any(line.startswith(value) for line in (brief.get("notes") or "").splitlines())
    from app.projects import BRIEF_SECTIONS

    key = BRIEF_SECTIONS[section][0]
    return any(str(row.get(key) or "").lower() == value for row in brief.get(section) or [])


def _unwrite_brief(store: Any, item: dict, value: str) -> None:
    """Ответ больше не про весь проект: строка уходит из брифа, если её с тех пор никто не менял.
    То, что дописали к человеку отдельно (приметы, другие имена), остаётся."""
    from app import projects as P

    project_id = P.project_of_source(store, int(item["source_id"]))
    if project_id is None:
        return
    brief = P.get_brief(store, project_id)["brief"]
    place = brief_place(item)
    section, _, target = place.partition(":")
    removed = False
    if section == "notes":
        kept = [line for line in brief["notes"].splitlines() if not line.startswith(target)]
        if len(kept) != len(brief["notes"].splitlines()):
            P.update_brief(store, project_id, {"notes": "\n".join(kept)})
            removed = True
    else:
        key, body = {"people": ("name", "who"), "jokes": ("text", "about")}[section]
        for row in brief[section]:
            if str(row[key]).lower() == target and row.get(body) == P._text(value, 600):
                keep = section == "people" and (row.get("traits") or row.get("aliases"))
                P.update_brief(store, project_id, {section: [{key: row[key], **({body: ""} if keep else {"_delete": True})}]})
                removed = True
                break
    if removed:
        _restore_other_answer(store, int(project_id), int(item["id"]), place)


def _restore_other_answer(store: Any, project_id: int, question_id: int, place: str) -> None:
    """На то же место мог отвечать вопрос другого видео: его ответ снова действует."""
    rows = store.db.query_all(
        "SELECT q.* FROM stream_questions q JOIN sources s ON s.id = q.source_id "
        "WHERE s.project_id = ? AND q.scope = 'channel' AND q.status = 'answered' AND q.id != ? "
        "ORDER BY q.answered_at DESC, q.id DESC", (int(project_id), int(question_id)))
    other = next((item for item in map(_question, rows) if brief_place(item) == place), None)
    if other is not None:
        _write_brief(store, other)
