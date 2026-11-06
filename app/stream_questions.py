"""Вопросы по стриму и выбор мемов: монтажёр спрашивает, владелец отвечает нажатием.

Владелец: «агент смотрит весь стрим, формирует список вопросов и варианты ответа и у меня
спрашивает, чтобы я объяснял, что происходит. Мемов не хватает, такую же систему с
вариантами». Вопросы и варианты заполняет агент-монтажёр через инструменты (app/stream_question_tools.py),
ответы читает перед отбором моментов. Модель тут не зашита.

Уровень вопроса (владелец: «вопросы надо делить на проект, видео, клип, не в общую кучу»). В базе
это ``scope``, наружу ещё и ``level``:

* ``channel`` = ``project``: про проект вообще («кто такой Глеб»). Ответ сразу в бриф проекта
  (app/projects.py), чтобы не спрашивать на каждом стриме.
* ``stream`` = ``video``: только про это видео.
* ``clip`` = ``clip``: только про один клип (``clip_plan_id``, версии одного клипа считаются вместе).

Уровень можно сменить и после ответа (``set_level``): ответ переезжает в бриф или обратно.
Весь список того, что агент знает о проекте, и его правка — app/project_knowledge.py.

Пауза монтажа (владелец: «монтаж ставить на паузу, пока на вопросы не ответили. а то у нас уже 3
версии горизонтальной нарезки, а на вопросы я отвечаю только сейчас»). Открытый обязательный вопрос
видео останавливает агентов: инструменты из ``MONTAGE_TOOLS`` и те же действия по токену отказывают
(``montage_refusal``). Чего пауза не касается: правки владельца руками в редакторе, рендера готовых
клипов, «Смотреть монтаж», субтитров, слоёв и звука. Вопрос проекта держит только то видео, откуда
он задан, вопрос клипа только этот клип. ``optional`` не держит ничего, мемы тоже: они не меняют,
что вырезать. Снять паузу может только владелец кнопкой «Монтировать без ответов»
(``release_montage``); кнопка покрывает вопросы, заданные до нажатия, новый вопрос снова останавливает.
Весь проект может не ждать ответов: настройка «Ждать ответов на вопросы перед монтажом» (app/agent_settings.py).
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
SCOPES = ("stream", "channel", "clip")
LEVELS = ("project", "video", "clip")
LEVEL_OF_SCOPE = {"channel": "project", "stream": "video", "clip": "clip"}
SCOPE_OF_LEVEL = {level: scope for scope, level in LEVEL_OF_SCOPE.items()}
LEVEL_WORD = {"project": "проект", "video": "видео", "clip": "клип"}
# Что начинает новый монтаж или резку. Остальные инструменты клипа пауза не трогает. Руками владелец
# работает мимо этих инструментов (свои маршруты REST), так что его правки сюда не попадают.
MONTAGE_TOOLS = frozenset({"analyze_source", "create_clip", "make_compilation", "add_video_from_source", "auto_edit",
                           "suggest_cuts", "apply_cuts", "trim_piece"})
GATE_SHOWN = 5
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


def correct_answer(store: Any, question_id: int, text: Any) -> dict:
    """Владелец поправил свой ответ на экране «Что агент знает»: остаётся ответом, но своими словами."""
    get_question(store, question_id)
    value = _body(text)
    if not value:
        raise ValueError("нужен текст ответа")
    store.db.execute(
        "UPDATE stream_questions SET answer_option = NULL, answer_text = ?, status = 'answered', "
        "answered_at = CURRENT_TIMESTAMP WHERE id = ?", (value, int(question_id)))
    return get_question(store, question_id)


def forget_answer(store: Any, question_id: int) -> dict:
    """Владелец убрал ответ: для агента его больше нет, вопрос заново не задаём (его можно открыть в «Вопросах»)."""
    store.db.execute(
        "UPDATE stream_questions SET answer_option = NULL, answer_text = '', status = 'skipped', "
        "answered_at = CURRENT_TIMESTAMP WHERE id = ?", (int(question_id),))
    return get_question(store, question_id)


def answer_question(store: Any, question_id: int, option: Any = None, text: Any = "", skip: bool = False) -> dict:
    # Один замок с set_level: смена уровня и ответ не разъедутся в брифе.
    with store.db._lock:
        return _answer_locked(store, question_id, option, text, skip)


def _answer_locked(store: Any, question_id: int, option: Any, text: Any, skip: bool) -> dict:
    item = get_question(store, question_id)
    before = answer_value(item) if item["status"] == "answered" else ""
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
    elif before and fresh["scope"] == "channel":
        try:
            _unwrite_brief(store, fresh, before)
        except Exception as exc:  # noqa: BLE001 - ответ снят, а строку брифа владелец уберёт сам
            brief = {"written": False, "reason": str(exc)[:200]}
    return {**fresh, "brief": brief, "gate": montage_gate(store, int(fresh["source_id"]))}


def set_level(store: Any, question_id: int, level: Any, clip_plan_id: Any = None) -> dict:
    """Владелец поправил уровень вопроса: ответ переезжает в бриф проекта или обратно, сам ответ не меняется.
    Клип вопроса помнится и у видео-уровня, чтобы вернуть вопрос к клипу."""
    level = str(level or "")
    if level not in LEVELS:
        raise ValueError(f"level — {', '.join(LEVELS)}")
    with store.db._lock:
        item = get_question(store, question_id)
        scope = SCOPE_OF_LEVEL[level]
        clip = item.get("clip_plan_id")
        if scope == "clip":
            wanted = clip_plan_id if clip_plan_id not in (None, "") else clip
            if wanted is None:
                raise ValueError("вопрос про клип: нужен clip_id")
            clip = _clip_of_source(store, int(item["source_id"]), wanted)
        brief: dict[str, Any] = {"written": False}
        if scope != item["scope"] or clip != item.get("clip_plan_id"):
            value = answer_value(item) if item["status"] == "answered" else ""
            store.db.execute("UPDATE stream_questions SET scope = ?, clip_plan_id = ? WHERE id = ?",
                             (scope, clip, int(question_id)))
            fresh = get_question(store, question_id)
            try:
                if value and item["scope"] == "channel" and scope != "channel":
                    _unwrite_brief(store, item, value)
                elif value and scope == "channel" and item["scope"] != "channel":
                    brief = _write_brief(store, fresh)
            except Exception as exc:  # noqa: BLE001 - уровень уже сменён, строку брифа владелец поправит сам
                brief = {"written": False, "reason": str(exc)[:200]}
        fresh = get_question(store, question_id)
    return {**fresh, "brief": brief, "gate": montage_gate(store, int(fresh["source_id"]))}


# --------------------------------------------------------------------------- #
# Пауза монтажа
# --------------------------------------------------------------------------- #


def _release_row(store: Any, source_id: int) -> Any:
    return store.db.query_one("SELECT up_to_question_id, released_at FROM stream_montage_release WHERE source_id = ?",
                              (int(source_id),))


def montage_gate(store: Any, source_id: int, clip_plan_id: int | None = None) -> dict:
    """Ждёт ли монтаж ответов. Считаются открытые обязательные вопросы ЭТОГО видео: про проект и про видео
    держат всё видео; про клип только сам этот клип (``clip_plan_id``)."""
    family = _clip_family(store, int(clip_plan_id)) if clip_plan_id else set()
    rows = [dict(r) for r in store.db.query_all(
        "SELECT id, question, scope, clip_plan_id FROM stream_questions "
        "WHERE source_id = ? AND status = 'open' AND optional = 0 ORDER BY t_start, id", (int(source_id),))]
    relevant = [r for r in rows if r["scope"] != "clip" or r["clip_plan_id"] in family]
    release = _release_row(store, source_id)
    covered = int(release["up_to_question_id"]) if release else 0
    waiting = [r for r in relevant if int(r["id"]) > covered]
    from app.agent_settings import waits_for_answers

    waits = waits_for_answers(store, int(source_id))
    return {
        "paused": bool(waiting) and waits,
        "waits": waits,
        "open": len(waiting),
        "project_open": sum(1 for r in waiting if r["scope"] == "channel"),
        "clip_open": sum(1 for r in waiting if r["scope"] == "clip"),
        "items": [{"id": int(r["id"]), "question": r["question"], "level": LEVEL_OF_SCOPE.get(r["scope"], "video")}
                  for r in waiting[:GATE_SHOWN]],
        "released": release is not None,
        "released_open": len(relevant) - len(waiting),
    }


def release_montage(store: Any, source_id: int) -> dict:
    """«Монтировать без ответов»: решение владельца про это видео, инструментов агента к нему нет."""
    store.get_source(int(source_id))
    with store.db._lock:
        top = store.db.query_one("SELECT COALESCE(MAX(id), 0) AS n FROM stream_questions WHERE source_id = ?",
                                 (int(source_id),))
        store.db.execute(
            "INSERT INTO stream_montage_release (source_id, up_to_question_id) VALUES (?, ?) "
            "ON CONFLICT(source_id) DO UPDATE SET up_to_question_id = excluded.up_to_question_id, "
            "released_at = CURRENT_TIMESTAMP", (int(source_id), int(top["n"])))
    return montage_gate(store, source_id)


def restore_montage_pause(store: Any, source_id: int) -> dict:
    """Владелец передумал: снова ждём ответов."""
    store.get_source(int(source_id))
    store.db.execute("DELETE FROM stream_montage_release WHERE source_id = ?", (int(source_id),))
    return montage_gate(store, source_id)


def _ru_questions(count: int) -> str:
    tail = count % 100
    if 11 <= tail <= 14 or count % 10 in (0, 5, 6, 7, 8, 9):
        return f"{count} вопросов"
    return f"{count} вопрос" if count % 10 == 1 else f"{count} вопроса"


def montage_refusal(store: Any, source_id: int, clip_plan_id: int | None = None) -> dict | None:
    """Отказ агенту, пока видео ждёт ответов владельца; ``None`` — можно работать."""
    from app import modules

    if not modules.tool_enabled("add_stream_questions", store):  # модуль выключен: ответить негде, ждать нечего
        return None
    gate = montage_gate(store, int(source_id), clip_plan_id)
    if not gate["paused"]:
        return None
    problems = [f"#{q['id']} ({LEVEL_WORD[q['level']]}): {q['question'][:140]}" for q in gate["items"]]
    if gate["open"] > len(gate["items"]):
        problems.append(f"и ещё {gate['open'] - len(gate['items'])}")
    from app import agent_settings as S

    project_id = project_of_source(store, int(source_id))
    return {
        "ok": False,
        "paused": "open_questions",
        "setting": "wait_answers",
        "source_id": int(source_id),
        "open_questions": [q["id"] for q in gate["items"]],
        "error": (f"Монтаж видео #{source_id} на паузе: владелец ещё не ответил на {_ru_questions(gate['open'])}. "
                  "Сначала ответы, потом нарезка. Дождись их и прочитай list_stream_answers. Пока можно смотреть "
                  "стрим, задавать вопросы и править субтитры, слои и звук готовых клипов. Начать без ответов может "
                  f"только сам владелец: кнопка «Монтировать без ответов» на странице видео /v/{source_id}"
                  + (f" или настройка «{S.NAME_WAIT}»: {S.where(project_id)}." if project_id is not None else ".")),
        "problems": problems,
    }


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


def _source_title(store: Any, source: dict) -> str:
    from app import nav

    return nav.titles_for(store, [source]).get(int(source["id"]), "") or source.get("original_filename") or ""


def _clip_names(store: Any, clip_ids: set[int]) -> dict[int, str]:
    if not clip_ids:
        return {}
    from app.clip_groups import display_name

    ids = sorted(clip_ids)
    marks = ",".join("?" for _ in ids)
    rows = store.db.query_all(f"SELECT id, title FROM clip_plans WHERE id IN ({marks})", ids)
    return {int(r["id"]): display_name(r["title"]) for r in rows}


def _elsewhere(store: Any, source_id: int, in_project: bool) -> dict:
    """Что задано по этому видео, но лежит на другом уровне: страница даёт туда ссылку."""
    rows = store.db.query_all(
        "SELECT scope, clip_plan_id, status FROM stream_questions WHERE source_id = ? AND scope != 'stream'",
        (int(source_id),))
    clips: dict[int, dict] = {}
    project = {"total": 0, "open": 0}
    for row in rows:
        is_open = row["status"] == "open"
        if row["scope"] == "channel":
            project["total"] += 1
            project["open"] += 1 if is_open else 0
        elif row["clip_plan_id"] is not None:
            clip = clips.setdefault(int(row["clip_plan_id"]), {"id": int(row["clip_plan_id"]), "total": 0, "open": 0})
            clip["total"] += 1
            clip["open"] += 1 if is_open else 0
    names = _clip_names(store, set(clips))
    return {"project": project if in_project else {"total": 0, "open": 0},
            "clips": [{**clip, "name": names.get(cid, "")} for cid, clip in sorted(clips.items())]}


def page_state(store: Any, source_id: int) -> dict:
    """Страница видео: его собственные вопросы и мемы, про проект и клипы только ссылки туда.
    Видео без проекта показывает и вопросы про проект: другой страницы у них нет."""
    from app import projects as P

    source = store.get_source(int(source_id))
    project_id = P.project_of_source(store, int(source_id))
    questions = [q for q in list_questions(store, source_id)
                 if q["level"] == "video" or (q["level"] == "project" and project_id is None)]
    moments = list_moments(store, source_id)
    return {
        "source": {"id": source["id"], "title": _source_title(store, source),
                   "duration": source.get("duration_sec") or 0},
        "project_id": project_id,
        "questions": questions,
        "moments": moments,
        "progress": progress(questions, moments),
        "gate": montage_gate(store, source_id),
        "elsewhere": _elsewhere(store, source_id, project_id is not None),
    }


def clip_state(store: Any, clip_plan_id: int) -> dict:
    """Экран клипа: только вопросы про этот клип (с его версиями) и стоит ли монтаж."""
    from app import projects as P

    plan = store.get_clip_plan(int(clip_plan_id), include_segments=False)
    source_id = int(plan["source_id"])
    family = _clip_family(store, int(clip_plan_id))
    questions = [q for q in list_questions(store, source_id, level="clip") if q.get("clip_plan_id") in family]
    return {
        "clip_plan_id": int(clip_plan_id),
        "source_id": source_id,
        "project_id": P.project_of_source(store, source_id),
        "questions": questions,
        "gate": montage_gate(store, source_id, int(clip_plan_id)),
    }


def inbox_state(store: Any, project_id: int | None = None, only_open: bool = True, limit: int = 300) -> dict:
    """Вопросы и мемы всех видео для «Входящих» и вкладки проекта: по видео, новые стримы первыми."""
    from app import nav

    where, args = [], []
    if project_id is not None:
        where.append("s.project_id = ?")
        args.append(int(project_id))
    status_q = " AND x.status = 'open'" if only_open else ""
    scope = (" AND " + " AND ".join(where)) if where else ""
    limit = max(1, int(limit))
    question_rows = store.db.query_all(
        f"SELECT x.* FROM stream_questions x JOIN sources s ON s.id = x.source_id WHERE 1 = 1{status_q}{scope} "
        "ORDER BY x.source_id DESC, x.t_start, x.id LIMIT ?",
        (*args, limit),
    )
    moment_rows = store.db.query_all(
        f"SELECT x.* FROM meme_moments x JOIN sources s ON s.id = x.source_id WHERE 1 = 1{status_q}{scope} "
        "ORDER BY x.source_id DESC, x.t_start, x.id LIMIT ?",
        (*args, limit),
    )
    questions = [_question(r) for r in question_rows]
    moments = [_moment(store, r) for r in moment_rows]
    ids = sorted({int(x["source_id"]) for x in [*questions, *moments]}, reverse=True)
    sources: dict[int, dict] = {}
    if ids:
        marks = ",".join("?" for _ in ids)
        rows = [dict(r) for r in store.db.query_all(
            f"""
            SELECT s.id, s.source_type, s.original_filename, s.created_at, s.project_id, p.name AS project_name
            FROM sources s LEFT JOIN projects p ON p.id = s.project_id WHERE s.id IN ({marks})
            """,
            ids,
        )]
        names = nav.titles_for(store, rows)
        for row in rows:
            sources[int(row["id"])] = {"id": row["id"], "title": names.get(int(row["id"]), ""),
                                       "project_id": row["project_id"], "project_name": row["project_name"] or "",
                                       "paused": montage_gate(store, int(row["id"]))["paused"]}
    counts = {level: {"total": 0, "open": 0} for level in (*LEVELS, "memes")}
    for row in store.db.query_all(
        f"SELECT x.scope, x.status, COUNT(*) AS n FROM stream_questions x JOIN sources s ON s.id = x.source_id "
        f"WHERE 1 = 1{scope} GROUP BY x.scope, x.status", args):
        bucket = counts[LEVEL_OF_SCOPE.get(row["scope"], "video")]
        bucket["total"] += int(row["n"])
        bucket["open"] += int(row["n"]) if row["status"] == "open" else 0
    for row in store.db.query_all(
        f"SELECT x.status, COUNT(*) AS n FROM meme_moments x JOIN sources s ON s.id = x.source_id "
        f"WHERE 1 = 1{scope} GROUP BY x.status", args):
        counts["memes"]["total"] += int(row["n"])
        counts["memes"]["open"] += int(row["n"]) if row["status"] == "open" else 0
    clip_names = _clip_names(store, {int(q["clip_plan_id"]) for q in questions if q.get("clip_plan_id")})
    totals = store.db.query_all(
        """
        SELECT s.project_id, p.name, SUM(n) AS n FROM (
            SELECT source_id, COUNT(*) AS n FROM stream_questions WHERE status = 'open' GROUP BY source_id
            UNION ALL
            SELECT source_id, COUNT(*) AS n FROM meme_moments WHERE status = 'open' GROUP BY source_id
        ) x JOIN sources s ON s.id = x.source_id LEFT JOIN projects p ON p.id = s.project_id
        GROUP BY s.project_id ORDER BY p.name COLLATE NOCASE
        """
    )
    return {
        "sources": [sources[i] for i in ids if i in sources],
        "questions": questions,
        "moments": moments,
        "counts": counts,
        "clips": [{"id": cid, "name": name} for cid, name in sorted(clip_names.items())],
        "open_by_project": [{"project_id": r["project_id"], "name": r["name"] or "Вне проектов", "open": int(r["n"])}
                            for r in totals],
    }


def answers_brief(store: Any, source_id: int) -> dict:
    """Короткий контекст стрима для монтажёра: только то, на что владелец ответил."""
    questions = list_questions(store, source_id)
    moments = list_moments(store, source_id)
    answers = [{"id": q["id"], "t_start": q["t_start"], "t_end": q["t_end"], "kind": q["kind"], "scope": q["scope"],
                "level": q["level"], **({"clip_id": q["clip_plan_id"]} if q["level"] == "clip" else {}),
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
    gate = montage_gate(store, source_id)
    return {"answers": answers, "memes": memes, "progress": progress(questions, moments),
            "montage": {"paused": gate["paused"], "waiting_for": gate["open"], "released_by_owner": gate["released"],
                        "waits_for_answers": gate["waits"],
                        "note": ("Монтаж на паузе: новые клипы и вырезы откажут, пока владелец не ответит."
                                 if gate["paused"] else "Монтировать можно."
                                 + ("" if gate["waits"] else " В этом проекте монтаж не ждёт ответов: так настроил владелец."))}}


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
