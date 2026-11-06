"""Чат проекта: схема, нити и сообщения, идущий запуск (из ``app/project_chat.py``)."""

from __future__ import annotations

import copy
import json
import re
import threading
import time
import uuid
from typing import Any

from app import autopilot as A
from app import chat_tools as CT
from app import projects as P
from app.ai.agent_loop import TurnBusy, TurnCancelled
from app.settings import settings

VIDEO_THREAD_TITLE = "Чат видео"
CLIP_THREAD_TITLE = "Чат клипа"
LEGACY_LIMIT = 1000  # сколько старых реплик клипа читать
HIDDEN_SOURCE_TYPES = ("canvas", "clip_upload")  # app/store.py: _HIDDEN_SOURCE_TYPES


_MESSAGES_SQL = """
    CREATE TABLE IF NOT EXISTS {name} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        thread_id INTEGER REFERENCES chat_threads(id) ON DELETE CASCADE,
        project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
        role TEXT NOT NULL,
        text TEXT NOT NULL DEFAULT '',
        steps_json TEXT NOT NULL DEFAULT '[]',
        status TEXT NOT NULL DEFAULT 'done',
        model TEXT NOT NULL DEFAULT '',
        origin TEXT NOT NULL DEFAULT 'ui',
        learned_json TEXT NOT NULL DEFAULT '{{}}',
        meta_json TEXT NOT NULL DEFAULT '{{}}',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
"""

CHAT_SCHEMA_SQL = [
    # Переписка: проект-серия/канал (NULL — общий чат), основная переписка проекта, модель,
    # подгруженные группы инструментов, резюме старых реплик.
    """
    CREATE TABLE IF NOT EXISTS chat_threads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
        source_id INTEGER REFERENCES sources(id) ON DELETE CASCADE,
        clip_plan_id INTEGER REFERENCES clip_plans(id) ON DELETE CASCADE,
        title TEXT NOT NULL DEFAULT '',
        is_default INTEGER NOT NULL DEFAULT 0,
        model TEXT NOT NULL DEFAULT '',
        tool_groups_json TEXT NOT NULL DEFAULT '[]',
        summary TEXT NOT NULL DEFAULT '',
        summary_upto INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    # Состояние клипа ДО правок агента в одном ответе — для «Откатить» в чате.
    """
    CREATE TABLE IF NOT EXISTS chat_checkpoints (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        thread_id INTEGER NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
        message_id INTEGER NOT NULL,
        clip_plan_id INTEGER NOT NULL,
        label TEXT NOT NULL DEFAULT '',
        state_json TEXT NOT NULL,
        reverted_at TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    # Опасный вызов агента, ждущий кнопки владельца. status: pending / done / failed / rejected.
    """
    CREATE TABLE IF NOT EXISTS chat_confirmations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        thread_id INTEGER NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
        message_id INTEGER NOT NULL,
        tool TEXT NOT NULL,
        args_json TEXT NOT NULL DEFAULT '{}',
        label TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'pending',
        result_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        decided_at TEXT
    )
    """,
]


def ensure_chat_schema(conn) -> None:
    for sql in CHAT_SCHEMA_SQL:
        conn.execute(sql)
    if "source_id" not in {r[1] for r in conn.execute("PRAGMA table_info(chat_threads)").fetchall()}:
        # Чат видео. Старые переписки остаются как были: без видео.
        conn.execute("ALTER TABLE chat_threads ADD COLUMN source_id INTEGER REFERENCES sources(id) ON DELETE CASCADE")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_threads_source ON chat_threads(source_id)")
    if "clip_plan_id" not in {r[1] for r in conn.execute("PRAGMA table_info(chat_threads)").fetchall()}:
        # Чат клипа. Только добавляем: прежние переписки и clip_agent_messages не меняются.
        conn.execute("ALTER TABLE chat_threads ADD COLUMN clip_plan_id INTEGER REFERENCES clip_plans(id) ON DELETE CASCADE")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_threads_clip ON chat_threads(clip_plan_id) "
                 "WHERE clip_plan_id IS NOT NULL")
    columns = {r[1]: r for r in conn.execute("PRAGMA table_info(project_chat_messages)").fetchall()}
    if not columns:
        conn.execute(_MESSAGES_SQL.format(name="project_chat_messages"))
    elif "thread_id" not in columns:
        _migrate_messages(conn)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_project_chat ON project_chat_messages(project_id, id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_thread_messages ON project_chat_messages(thread_id, id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_threads_project ON chat_threads(project_id, id)")
    _link_old_clip_chats(conn)


def _migrate_messages(conn) -> None:
    """Прежний чат проекта (реплики по project_id, NOT NULL) → переписки: у каждого проекта
    с репликами — основная переписка, туда же его резюме."""
    conn.execute("DROP TABLE IF EXISTS project_chat_messages__new")
    conn.execute(_MESSAGES_SQL.format(name="project_chat_messages__new"))
    conn.execute(
        "INSERT INTO project_chat_messages__new (id, project_id, role, text, steps_json, status, model, origin, "
        "learned_json, created_at) SELECT id, project_id, role, text, steps_json, status, model, origin, "
        "learned_json, created_at FROM project_chat_messages")
    conn.execute("DROP TABLE project_chat_messages")
    conn.execute("ALTER TABLE project_chat_messages__new RENAME TO project_chat_messages")
    for row in conn.execute("SELECT DISTINCT project_id FROM project_chat_messages WHERE project_id IS NOT NULL"
                            ).fetchall():
        project_id = int(row[0])
        state = conn.execute("SELECT chat_summary, chat_summary_upto FROM project_autopilot WHERE project_id = ?",
                             (project_id,)).fetchone()
        cur = conn.execute(
            "INSERT INTO chat_threads (project_id, title, is_default, summary, summary_upto) VALUES (?, ?, 1, ?, ?)",
            (project_id, "Основной чат", state[0] if state else "", int(state[1] or 0) if state else 0))
        conn.execute("UPDATE project_chat_messages SET thread_id = ? WHERE project_id = ?", (cur.lastrowid, project_id))


def _link_old_clip_chats(conn) -> None:
    """Прежние чаты клипов — в список переписок: у каждого клипа со старой перепиской своя строка в chat_threads.
    Реплики не копируем: они остаются в clip_agent_messages, их читает ``legacy_clip_messages``."""
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
    if not {"clip_agent_messages", "clip_plans"} <= tables:
        return
    conn.execute(
        "INSERT INTO chat_threads (clip_plan_id, title, created_at, updated_at) "
        "SELECT m.clip_plan_id, ?, MIN(m.created_at), MAX(m.created_at) FROM clip_agent_messages m "
        "JOIN clip_plans p ON p.id = m.clip_plan_id "
        "WHERE NOT EXISTS (SELECT 1 FROM chat_threads t WHERE t.clip_plan_id = m.clip_plan_id) "
        "GROUP BY m.clip_plan_id", (CLIP_THREAD_TITLE,))


ChatBusy = TurnBusy  # один на все ходы (app/ai/agent_loop.py)


ChatCancelled = TurnCancelled  # «Стоп»: один на все ходы (app/ai/agent_loop.py)


def _loads(text: Any, default: Any) -> Any:
    return P._loads(text, default)


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


# --------------------------------------------------------------------------- #
# Переписки
# --------------------------------------------------------------------------- #


def _thread(row: Any) -> dict:
    data = dict(row)
    data["tool_groups"] = _loads(data.pop("tool_groups_json", "[]"), [])
    data["is_default"] = bool(data.get("is_default"))
    return data


def get_thread(store: Any, thread_id: int) -> dict:
    row = store.db.query_one("SELECT * FROM chat_threads WHERE id = ?", (int(thread_id),))
    if not row:
        raise KeyError(f"переписка #{thread_id} не найдена")
    data = _thread(row)
    if data.get("source_id"):  # у чата видео проект — тот, где видео лежит сейчас
        data["project_id"] = P.project_of_source(store, int(data["source_id"]))
    elif data.get("clip_plan_id"):  # и у чата клипа: проект у клипа, колонка project_id пустая
        from app.asset_scope import clip_project

        data["project_id"] = clip_project(store, int(data["clip_plan_id"]))
    return data


def video_thread(store: Any, source_id: int) -> dict:
    """Чат видео: один на видео, заводится при первом открытии. Колонка project_id у него пустая, см. ``get_thread``."""
    source = store.get_source(int(source_id))
    if source.get("source_type") in HIDDEN_SOURCE_TYPES:
        raise ValueError("это служебная запись холстов и загруженных клипов, а не видео: у неё нет своего чата")
    row = store.db.query_one("SELECT id FROM chat_threads WHERE source_id = ? ORDER BY id LIMIT 1", (int(source_id),))
    if row:
        return get_thread(store, int(row["id"]))
    cur = store.db.execute("INSERT INTO chat_threads (source_id, title) VALUES (?, ?)",
                           (int(source_id), VIDEO_THREAD_TITLE))
    return get_thread(store, int(cur.lastrowid))


def find_clip_thread(store: Any, clip_plan_id: int) -> dict | None:
    row = store.db.query_one("SELECT id FROM chat_threads WHERE clip_plan_id = ?", (int(clip_plan_id),))
    return get_thread(store, int(row["id"])) if row else None


def clip_thread(store: Any, clip_plan_id: int) -> dict:
    """Чат клипа: один на клип (план монтажа), заводится при первом открытии. Клипа нет — KeyError."""
    store.get_clip_plan(int(clip_plan_id), include_segments=False)
    found = find_clip_thread(store, clip_plan_id)
    if found:
        return found
    store.db.execute("INSERT OR IGNORE INTO chat_threads (clip_plan_id, title) VALUES (?, ?)",
                     (int(clip_plan_id), CLIP_THREAD_TITLE))
    return find_clip_thread(store, clip_plan_id)


def legacy_clip_messages(store: Any, clip_plan_id: int) -> list[dict]:
    """Прежняя переписка клипа из clip_agent_messages: только чтение, в виде реплик чата. Строки там старше всех новых
    реплик этого клипа: новых туда больше не пишем. id отрицательные: с id реплик чата они не пересекаются. Ход, шедший
    во время обновления сервиса, показываем прерванным, не трогая строку."""
    out = []
    for row in store.list_clip_agent_messages(int(clip_plan_id), limit=LEGACY_LIMIT):
        running = row.get("status") == "running"
        out.append({
            "id": -int(row["id"]), "thread_id": None, "project_id": None, "role": row.get("role") or "assistant",
            "text": (row.get("text") or "Прервано перезапуском сервера.") if running else row.get("text") or "",
            "steps": row.get("steps") or [], "status": "interrupted" if running else row.get("status") or "done",
            "model": row.get("model") or "", "origin": "ui", "learned": {}, "meta": {"legacy": True},
            "created_at": row.get("created_at"),
        })
    return out


_CLIP_CHAT_ROUTE = re.compile(r"^/api/chat/(?:(?:clip-thread|models|media/[^/]+|clips/\d+)$"
                              r"|threads/(\d+)(?:/messages|/stream|/cancel)?$"
                              r"|confirmations/(\d+)$|checkpoints/(\d+)/revert$)")


def clip_chat_route(path: str, store: Any) -> bool:
    """Адрес нужен чату клипа: для ``modules.blocked_route``, когда страница «Чат» выключена, а монтажёр нет."""
    match = _CLIP_CHAT_ROUTE.match(str(path or ""))
    if not match:
        return False
    thread_id, confirmation_id, checkpoint_id = match.groups()
    if confirmation_id or checkpoint_id:
        table, ident = ("chat_confirmations", confirmation_id) if confirmation_id else ("chat_checkpoints", checkpoint_id)
        row = store.db.query_one(f"SELECT thread_id FROM {table} WHERE id = ?", (int(ident),))
        thread_id = row["thread_id"] if row else None
        if thread_id is None:
            return False
    if thread_id is None:
        return True
    row = store.db.query_one("SELECT clip_plan_id FROM chat_threads WHERE id = ?", (int(thread_id),))
    return bool(row and row["clip_plan_id"])


def _clip_title(store: Any, clip_plan_id: int) -> str:
    plan = store.get_clip_plan(int(clip_plan_id), include_segments=False)
    return plan.get("title") or f"#{clip_plan_id}"


def _video_title(store: Any, source_id: int) -> str:
    from app import nav

    source = store.get_source(int(source_id))
    return nav.titles_for(store, [source]).get(int(source_id), "") or source.get("original_filename") or f"видео #{source_id}"


def create_thread(store: Any, project_id: int | None = None, title: str = "", model: str = "",
                  is_default: bool = False) -> dict:
    if project_id:
        project = P.get_project(store, int(project_id), include_sources=False)
        title = title or ("Основной чат" if is_default else f"Чат: {project['name']}")
    cur = store.db.execute(
        "INSERT INTO chat_threads (project_id, title, is_default, model) VALUES (?, ?, ?, ?)",
        (int(project_id) if project_id else None, str(title or "Новый чат")[:120], 1 if is_default else 0,
         str(model or "")[:80]))
    return get_thread(store, int(cur.lastrowid))


def default_thread(store: Any, project_id: int) -> dict:
    """Основная переписка проекта (её же видит вкладка «Чат» серии и Telegram)."""
    row = store.db.query_one("SELECT * FROM chat_threads WHERE project_id = ? AND is_default = 1 ORDER BY id LIMIT 1",
                             (int(project_id),))
    if row:
        return _thread(row)
    return create_thread(store, int(project_id), is_default=True)


def update_thread(store: Any, thread_id: int, **fields: Any) -> dict:
    get_thread(store, thread_id)
    allowed: dict[str, Any] = {}
    if "title" in fields and fields["title"] is not None:
        allowed["title"] = str(fields["title"]).strip()[:120] or "Новый чат"
    if "model" in fields and fields["model"] is not None:
        model = str(fields["model"]).strip()
        if model and model not in {m["id"] for m in models()["models"]}:
            raise ValueError(f"неизвестная модель {model}")
        allowed["model"] = model
    if "tool_groups" in fields:
        allowed["tool_groups_json"] = _dumps([g for g in fields["tool_groups"] or [] if g in CT.all_groups()])
    for key in ("summary", "summary_upto"):
        if key in fields:
            allowed[key] = fields[key]
    if allowed:
        sets = ", ".join(f"{k} = ?" for k in allowed)
        store.db.execute(f"UPDATE chat_threads SET {sets}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (*allowed.values(), int(thread_id)))
    return get_thread(store, thread_id)


def _touch(store: Any, thread_id: int) -> None:
    store.db.execute("UPDATE chat_threads SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (int(thread_id),))


def delete_thread(store: Any, thread_id: int) -> dict:
    thread = get_thread(store, thread_id)
    if active_run(thread_id):
        raise ChatBusy("агент ещё отвечает — сначала останови его")
    store.db.execute("DELETE FROM chat_threads WHERE id = ?", (int(thread_id),))
    if thread.get("clip_plan_id"):  # удалена по кнопке владельца: и старая переписка этого клипа, иначе чат вернётся
        store.clear_clip_agent_messages(int(thread["clip_plan_id"]))
    if thread["is_default"] and thread["project_id"]:
        A.set_state(store, thread["project_id"], chat_summary="", chat_summary_upto=0)
    return {"deleted": int(thread_id)}


def list_threads(store: Any) -> dict:
    """Левая колонка страницы «Чат»: проекты (с их переписками), общие, чаты видео и клипов вне проектов."""
    from app import nav
    from app.asset_scope import clip_project

    rows = store.db.query_all(
        "SELECT t.*, (SELECT COUNT(*) FROM project_chat_messages m WHERE m.thread_id = t.id) AS messages_count, "
        "(SELECT text FROM project_chat_messages m WHERE m.thread_id = t.id AND m.text != '' ORDER BY m.id DESC "
        "LIMIT 1) AS last_text, s.project_id AS video_project_id, s.source_type, s.original_filename, "
        "s.created_at AS source_created_at, cp.title AS clip_title, cp.source_id AS clip_source_id, "
        "cs.project_id AS clip_source_project, "
        "(SELECT pp.project_id FROM plan_projects pp WHERE pp.clip_plan_id = t.clip_plan_id) AS clip_direct_project, "
        "(SELECT COUNT(*) FROM clip_agent_messages c WHERE c.clip_plan_id = t.clip_plan_id) AS legacy_count, "
        "(SELECT text FROM clip_agent_messages c WHERE c.clip_plan_id = t.clip_plan_id AND c.text != '' "
        "ORDER BY c.id DESC LIMIT 1) AS legacy_last "
        "FROM chat_threads t LEFT JOIN sources s ON s.id = t.source_id "
        "LEFT JOIN clip_plans cp ON cp.id = t.clip_plan_id LEFT JOIN sources cs ON cs.id = cp.source_id "
        "ORDER BY t.updated_at DESC, t.id DESC")
    videos = [{"id": r["source_id"], "source_type": r["source_type"], "original_filename": r["original_filename"],
               "created_at": r["source_created_at"]} for r in rows if r["source_id"]]
    names = nav.titles_for(store, videos) if videos else {}
    threads = []
    for row in rows:
        item = _thread(row)
        for key in ("source_type", "original_filename", "source_created_at"):
            item.pop(key, None)
        video_project = item.pop("video_project_id", None)
        clip_title = item.pop("clip_title", None)
        clip_source = item.pop("clip_source_project", None)
        clip_direct = item.pop("clip_direct_project", None)
        legacy_count = int(item.pop("legacy_count", 0) or 0)
        legacy_last = item.pop("legacy_last", None)
        if item.get("source_id"):
            item["project_id"] = video_project
            if item["title"] == VIDEO_THREAD_TITLE:
                item["title"] = f"Видео: {names.get(int(item['source_id'])) or item['source_id']}"
        elif item.get("clip_plan_id"):
            item["messages_count"] = int(item.get("messages_count") or 0) + legacy_count
            item["last_text"] = item.get("last_text") or legacy_last
            if item["title"] == CLIP_THREAD_TITLE:
                item["title"] = f"Клип: {clip_title or item['clip_plan_id']}"
            # Источник, приписка плана; реже всего — цепочка серии или ролика (clip_project): только для тех, кому нужно.
            item["project_id"] = clip_source if clip_source is not None else clip_direct
            if item["project_id"] is None and item["messages_count"]:
                item["project_id"] = clip_project(store, int(item["clip_plan_id"]))
        item["last_text"] = " ".join(re.sub(r"[*`#>_]+", "", str(item.get("last_text") or "")).split())[:140]
        item["running"] = active_run(item["id"]) is not None
        item.pop("summary", None)
        threads.append(item)
    # Чаты видео и клипа заводятся при открытии: пустые в общий список не идут.
    visible = [t for t in threads if not (t.get("source_id") or t.get("clip_plan_id")) or t.get("messages_count")]
    projects = [{"id": p["id"], "name": p["name"], "kind": p.get("kind"),
                 "threads": [t for t in visible if t["project_id"] == p["id"]]}
                for p in P.list_projects(store)]
    loose = [t for t in visible if not t["project_id"]]
    return {"projects": projects, "general": [t for t in loose if not t.get("source_id") and not t.get("clip_plan_id")],
            "videos": [t for t in loose if t.get("source_id")], "clips": [t for t in loose if t.get("clip_plan_id")],
            "ai": ai_status()}


# --------------------------------------------------------------------------- #
# Реплики
# --------------------------------------------------------------------------- #


def _msg(row: Any) -> dict:
    data = dict(row)
    data["steps"] = _loads(data.pop("steps_json", "[]"), [])
    data["learned"] = _loads(data.pop("learned_json", "{}"), {})
    data["meta"] = _loads(data.pop("meta_json", "{}"), {})
    return data


def add_thread_message(store: Any, thread_id: int, role: str, text: str, status: str = "done", origin: str = "ui",
                       model: str = "", meta: dict | None = None) -> int:
    thread = get_thread(store, thread_id)
    cur = store.db.execute(
        "INSERT INTO project_chat_messages (thread_id, project_id, role, text, status, origin, model, meta_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (int(thread_id), thread["project_id"], role, str(text or "")[:20000], status, origin, model,
         _dumps(meta or {})))
    _touch(store, thread_id)
    return int(cur.lastrowid)


def add_message(store: Any, project_id: int, role: str, text: str, status: str = "done", origin: str = "ui",
                model: str = "") -> int:
    """Реплика в основную переписку проекта (прежний API чата проекта)."""
    return add_thread_message(store, default_thread(store, project_id)["id"], role, text, status=status,
                              origin=origin, model=model)


def update_message(store: Any, message_id: int, **fields: Any) -> None:
    for key in ("steps", "learned", "meta"):
        if key in fields:
            fields[f"{key}_json"] = _dumps(fields.pop(key))
    sets = ", ".join(f"{k} = ?" for k in fields)
    store.db.execute(f"UPDATE project_chat_messages SET {sets} WHERE id = ?", (*fields.values(), int(message_id)))


def get_message(store: Any, message_id: int) -> dict:
    row = store.db.query_one("SELECT * FROM project_chat_messages WHERE id = ?", (int(message_id),))
    if not row:
        raise KeyError(f"реплика #{message_id} не найдена")
    return _msg(row)


def thread_messages(store: Any, thread_id: int, after: int = 0, limit: int = 300) -> list[dict]:
    rows = store.db.query_all(
        "SELECT * FROM (SELECT * FROM project_chat_messages WHERE thread_id = ? AND id > ? ORDER BY id DESC LIMIT ?) "
        "ORDER BY id", (int(thread_id), int(after), int(limit)))
    return [_msg(r) for r in rows]


def list_messages(store: Any, project_id: int, after: int = 0, limit: int = 200) -> list[dict]:
    return thread_messages(store, default_thread(store, project_id)["id"], after=after, limit=limit)


def clear_thread(store: Any, thread_id: int) -> int:
    thread = get_thread(store, thread_id)
    if active_run(thread_id):
        raise ChatBusy("агент ещё отвечает — подожди")
    cur = store.db.execute("DELETE FROM project_chat_messages WHERE thread_id = ?", (int(thread_id),))
    update_thread(store, thread_id, summary="", summary_upto=0, tool_groups=[])
    if thread["is_default"] and thread["project_id"]:
        A.set_state(store, thread["project_id"], chat_summary="", chat_summary_upto=0)
    deleted = int(cur.rowcount)
    if thread.get("clip_plan_id"):  # «Очистить» у чата клипа всегда стирало и его старую переписку
        deleted += store.clear_clip_agent_messages(int(thread["clip_plan_id"]))
    return deleted


def clear_chat(store: Any, project_id: int) -> int:
    return clear_thread(store, default_thread(store, project_id)["id"])


# --------------------------------------------------------------------------- #
# Провайдер
# --------------------------------------------------------------------------- #


def ai_status() -> dict:
    from app.ai import editor_agent

    available = bool(settings.gemini_api_key or editor_agent.client_factory)
    return {"provider": "gemini", "available": available,
            "hint": "" if available else ("В сервисе не задан GEMINI_API_KEY: добавь ключ из AI Studio в .env "
                                          "сервиса и перезапусти его. Платный Polza чат не использует.")}


def models() -> dict:
    """Модели для выбора в шапке чата. Провайдер один — Gemini сервиса (Polza не используем,
    других текстовых провайдеров с инструментами в сервисе нет)."""
    seen: dict[str, str] = {}
    for model, label in ((settings.gemini_agent_model, "умнее (как ИИ-монтажёр)"),
                         (settings.gemini_text_model, "быстрее и дешевле"),
                         (settings.gemini_video_model, "как анализ видео"),
                         (settings.gemini_montage_model, "как ИИ-монтаж")):
        if model and model not in seen:
            seen[model] = label
    items = [{"id": m, "label": m, "note": label, "provider": "gemini"} for m, label in seen.items()]
    return {"provider": "gemini", "default": settings.gemini_agent_model, "models": items, **ai_status()}


# --------------------------------------------------------------------------- #
# Живое состояние хода (для SSE)
# --------------------------------------------------------------------------- #


class _Run:
    def __init__(self, thread_id: int, project_id: int | None, reply_id: int, clip_plan_id: int | None = None) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.thread_id = int(thread_id)
        self.project_id = project_id
        self.reply_id = int(reply_id)
        self.clip_plan_id = clip_plan_id
        self.cancel = threading.Event()
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.text = ""
        self.steps: list[dict] = []
        self.phase = "thinking"  # thinking / streaming / tool / done
        self.text_seq = 0
        self.steps_seq = 0
        self.done = False
        self.started_at = time.time()

    def add_text(self, delta: str) -> None:
        with self.lock:
            self.text += delta
            self.text_seq += 1
            self.phase = "streaming"

    def take_text(self) -> str:
        """Текст раунда, закончившегося вызовами, — это реплика между шагами: убираем из ответа."""
        with self.lock:
            text, self.text = self.text, ""
            self.text_seq += 1
            return text

    def set_steps(self, steps: list[dict], phase: str | None = None) -> None:
        with self.lock:
            self.steps = copy.deepcopy(steps)
            self.steps_seq += 1
            if phase:
                self.phase = phase

    def set_phase(self, phase: str) -> None:
        with self.lock:
            self.phase = phase
            self.steps_seq += 1

    def snapshot(self) -> dict:
        with self.lock:
            return {"text": self.text, "steps": copy.deepcopy(self.steps), "phase": self.phase,
                    "text_seq": self.text_seq, "steps_seq": self.steps_seq, "done": self.done,
                    "reply_id": self.reply_id}


_RUNS: dict[int, _Run] = {}
_LOCK = threading.Lock()


def _live(run: _Run | None) -> _Run | None:
    """Ход идёт: не закончен и поток жив. Умерший поток занятым чат не держит."""
    if run is None or run.done or (run.thread is not None and not run.thread.is_alive()):
        return None
    return run


def active_run(thread_id: int) -> _Run | None:
    with _LOCK:
        return _live(_RUNS.get(int(thread_id)))


def active_clip_run(clip_plan_id: int) -> _Run | None:
    with _LOCK:
        return next((r for r in _RUNS.values() if r.clip_plan_id == int(clip_plan_id) and _live(r)), None)


def wait_clip_run(clip_plan_id: int, timeout: float | None = None) -> bool:
    with _LOCK:
        runs = [r for r in _RUNS.values() if r.clip_plan_id == int(clip_plan_id) and r.thread is not None]
    if not runs:
        return True
    runs[-1].thread.join(timeout)
    return not runs[-1].thread.is_alive()


def cancel(thread_id: int) -> bool:
    run = active_run(thread_id)
    if run is None:
        return False
    run.cancel.set()
    return True


def wait_thread(thread_id: int, timeout: float | None = None) -> bool:
    with _LOCK:
        run = _RUNS.get(int(thread_id))
    if run is None or run.thread is None:
        return True
    run.thread.join(timeout)
    return not run.thread.is_alive()


def wait(project_id: int, timeout: float | None = None) -> bool:
    """Дождаться ходов всех переписок проекта (тесты, CLI)."""
    with _LOCK:
        runs = [r for r in _RUNS.values() if r.project_id == int(project_id) and r.thread is not None]
    deadline = None if timeout is None else time.time() + timeout
    for run in runs:
        left = None if deadline is None else max(0.0, deadline - time.time())
        run.thread.join(left)
        if run.thread.is_alive():
            return False
    return True


def _active(project_id: int) -> bool:
    with _LOCK:
        return any(r.project_id == int(project_id) and not r.done for r in _RUNS.values())
