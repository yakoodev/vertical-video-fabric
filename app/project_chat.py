"""💬 Чат с агентом: страница «Чат» (переписки по проектам и общая), вкладка «Чат» серии и Telegram.

Владелец: «было бы круто еще общаться с тем агентом как с тобой. типа на каждый проект
свои чаты» и «общаться как с тобой в плане текстом в сервисе».

Строится на встроенном ИИ-монтажёре (``app/ai/editor_agent.py``): тот же клиент Gemini
(``editor_agent.client_factory`` — тесты подставляют фейк), та же сборка истории и ответов
инструментов, тот же реестр инструментов, что у MCP (``app/agent_tools.py``). Устройство:

* **Переписки** (``chat_threads``): у переписки может быть проект-серия/канал или нет
  (общий чат). У проекта — своя «основная» переписка (``is_default``): её же показывает
  вкладка «Чат» серии и в неё пишет Telegram-тема проекта. Реплики — ``project_chat_messages``.
  Чат видео — переписка с ``source_id``: одна на видео, проект берётся у видео в момент чтения
  (перенесли видео — чат работает уже в новом проекте, лор старого к нему не идёт), удаляется вместе с видео.
* **Инструменты группами** (``app/chat_tools.py``): база (проект + посмотреть клип) в каждом
  ходе, остальное — ``load_tools`` по запросу модели или по словам владельца.
* **Ответ печатается по ходу**: ход идёт в фоновом потоке, текст Gemini приходит потоком
  (``stream_generate_content``) в живое состояние хода (``_Run``), а SSE-ручка
  (``app/agent_chat_api.py``) отдаёт его браузеру. Клиент без потока — индикатор «думает…» и шаги.
* **Шаги видны**: каждый вызов — шаг с аргументами, кратким результатом и превью (кадр,
  лист кадров, клип, карточка одобрения).
* **Откат**: перед первой правкой клипа в ходе — снимок истории клипа (Ctrl+Z в редакторе) и
  копия состояния в ``chat_checkpoints`` (кнопка «Откатить» в чате, не зависит от глубины истории).
* **Опасное** (публикация, удаление…) — не выполняется сразу: карточка ``chat_confirmations``,
  владелец жмёт «Подтвердить» — ``decide_confirmation`` выполняет вызов.
* **Память**: модели — последние реплики и сжатое резюме старых (у переписки; у основной
  переписки проекта резюме дублируется в ``project_autopilot.chat_summary``).

Разговор в проекте — источник обучения: правилоподобная фраза владельца («такое больше не бери»,
«вот так круто») сразу разбирается эвристикой автопилота (``autopilot.learn_from_text``).
Настройка проекта ``auto_rules``: ``confirm`` (по умолчанию) — карточка «Записать правило?»,
ответ «да» в чате её одобряет; ``auto`` — однозначное записывается сразу (с цитатой).
Провайдер — Gemini сервиса; нет ключа или он лёг — так и отвечаем, с подсказкой, что делать.
Платный Polza не используется.
"""

from __future__ import annotations

import base64
import copy
import json
import logging
import re
import threading
import time
from typing import Any, Callable

from app import autopilot as A
from app import chat_tools as CT
from app import project_knowledge as K
from app import projects as P
from app.agent_tools import ImageResult, ToolContext
from app.settings import settings

_log = logging.getLogger(__name__)

HISTORY_KEEP = 12  # последних реплик — модели целиком
SUMMARIZE_AFTER = 30  # больше несжатых реплик — старые уходят в резюме
SUMMARY_CHARS = 3000
CONTEXT_CHARS = 24000
CONTEXT_SOURCES = 40  # у Apex 218 видео: их список не должен вытеснять лор из обрезки
VIDEO_THREAD_TITLE = "Чат видео"
HIDDEN_SOURCE_TYPES = ("canvas", "clip_upload")  # app/store.py: _HIDDEN_SOURCE_TYPES
MAX_ROUNDS = 24
MAX_OUTPUT_TOKENS = 8192
SAY = "_say"
# Клиповые инструменты, полезные в разговоре о проекте (совместимость: прежний список чата проекта).
CLIP_TOOLS = ("get_clip", "get_transcript", "get_performance", "get_playbook", "list_clips", "list_projects")

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
    columns = {r[1]: r for r in conn.execute("PRAGMA table_info(project_chat_messages)").fetchall()}
    if not columns:
        conn.execute(_MESSAGES_SQL.format(name="project_chat_messages"))
    elif "thread_id" not in columns:
        _migrate_messages(conn)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_project_chat ON project_chat_messages(project_id, id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_thread_messages ON project_chat_messages(thread_id, id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_threads_project ON chat_threads(project_id, id)")


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


class ChatBusy(RuntimeError):
    """Агент ещё отвечает на прошлое сообщение этой переписки."""


class ChatCancelled(Exception):
    pass


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
    if thread["is_default"] and thread["project_id"]:
        A.set_state(store, thread["project_id"], chat_summary="", chat_summary_upto=0)
    return {"deleted": int(thread_id)}


def list_threads(store: Any) -> dict:
    """Левая колонка страницы «Чат»: проекты (с их переписками), общие и чаты видео вне проектов."""
    from app import nav

    rows = store.db.query_all(
        "SELECT t.*, (SELECT COUNT(*) FROM project_chat_messages m WHERE m.thread_id = t.id) AS messages_count, "
        "(SELECT text FROM project_chat_messages m WHERE m.thread_id = t.id AND m.text != '' ORDER BY m.id DESC "
        "LIMIT 1) AS last_text, s.project_id AS video_project_id, s.source_type, s.original_filename, "
        "s.created_at AS source_created_at FROM chat_threads t LEFT JOIN sources s ON s.id = t.source_id "
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
        if item.get("source_id"):
            item["project_id"] = video_project
            if item["title"] == VIDEO_THREAD_TITLE:
                item["title"] = f"Видео: {names.get(int(item['source_id'])) or item['source_id']}"
        item["last_text"] = " ".join(re.sub(r"[*`#>_]+", "", str(item.get("last_text") or "")).split())[:140]
        item["running"] = active_run(item["id"]) is not None
        item.pop("summary", None)
        threads.append(item)
    # Чат видео заводится при открытии вкладки: пустые в общий список не идут.
    visible = [t for t in threads if not t.get("source_id") or t.get("messages_count")]
    projects = [{"id": p["id"], "name": p["name"], "kind": p.get("kind"),
                 "threads": [t for t in visible if t["project_id"] == p["id"]]}
                for p in P.list_projects(store)]
    return {"projects": projects, "general": [t for t in visible if not t["project_id"] and not t.get("source_id")],
            "videos": [t for t in visible if not t["project_id"] and t.get("source_id")], "ai": ai_status()}


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
    return int(cur.rowcount)


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


def _gemini(client: Any = None) -> Any:
    from app.ai import editor_agent

    if client is not None:
        return client
    if editor_agent.client_factory:
        return editor_agent.client_factory()
    if not settings.gemini_api_key:
        return None
    from app.ai.gemini import GeminiClient

    return GeminiClient(timeout=300)


def error_hint(exc: Exception) -> str:
    """Честная причина сбоя Gemini и что сделать владельцу."""
    text = str(exc)
    low = text.lower()
    if "кредиты" in low or "billing" in low:
        return text
    if "location is not supported" in low:
        return ("Gemini недоступен из этой страны. Включи VPN на сервере или задай GEMINI_PROXY_URL "
                "в .env сервиса.")
    if "auth failed" in low or "api key" in low:
        return "Gemini не принял ключ: проверь GEMINI_API_KEY в .env сервиса."
    if "network" in low or "timed out" in low or "timeout" in low:
        return "Gemini не ответил по сети. Попробуй ещё раз через минуту; если повторяется — проверь VPN/прокси."
    if "429" in low or "overloaded" in low or "high demand" in low or "503" in low:
        return "Gemini перегружен. Попробуй ещё раз через минуту или выбери другую модель в шапке чата."
    return f"Ошибка Gemini: {text[:300]}"


# --------------------------------------------------------------------------- #
# Контекст и резюме
# --------------------------------------------------------------------------- #

SYSTEM = """\
Ты — агент сервиса Vertical Video Fabric (нарезка стримов, сериалов и роликов в вертикальные шортсы и \
публикация). Владелец общается с тобой в чате, как с ассистентом.

Как работать:
- Отвечай по-русски, коротко и по делу, как коллега. Можно Markdown: списки, **жирный**, `код`.
- Не только советуй — делай инструментами. Не выдумывай: факты — из контекста ниже и инструментов.
  Не знаешь или инструмент не сработал — так и скажи.
- Инструменты подгружаются группами (их объявления дорого слать в каждом ходе). Сейчас есть база:
  проект, найти и посмотреть клип (list_clips, get_clip, view_frame, contact_sheet), плейбуки (get_playbook),
  нарезка источника (analyze_source), источник по ссылке (add_source_from_url). Нужна правка клипа или
  другое — сначала load_tools с нужными группами, потом вызывай их инструменты. Группы:
{groups}
- Клиповым инструментам нужен clip_id (list_clips по source_id). Время клиповых инструментов — секунды клипа.
  Таймкоды в ответе пиши так: «клип #123, 12.5 с» или «источник #7, 1:05:30» — владелец кликает по ним.
- Правки клипа откатываются кнопкой «Откатить» в чате и Ctrl+Z в редакторе — правь смело, но только то,
  что просили. Посмотри результат (view_frame / contact_sheet), прежде чем отчитаться.
- Опасное (публикация, удаление клипа, снятие всех слоёв, отмена правила) сервис сразу не выполняет:
  владелец подтверждает карточкой в чате. Ответ pending_confirmation — скажи, что ждёшь подтверждения,
  и не повторяй вызов.
- Публикуется только одобренное владельцем. Перед отправкой — lint_clip (зоны кнопок площадок, темп чтения,
  вспышки, луп): warn поправь и перерендерь. Клип на публикацию — submit_for_approval. Заголовок, описание
  и хэштеги сервис пишет сам после рендера; обложку — generate_covers → choose_cover (группа montage).
- Правила проекта — только словами владельца, с цитатой. Если в сообщении владельца правило или отмена
  правила, сервис уже разобрал его (блок «Сервис уже сделал»): опирайся на это и не дублируй.
  Своё наблюдение — гипотеза (add_hypothesis) или вопрос владельцу (propose_rule), не правило.
- Что ты знаешь о проекте — в срезе ниже: лор (brief), rules, facts (что владелец просил запомнить), memes (какие мемы
  он выбрал). Держись этого и не выдумывай сверх. Владелец рассказал новое о проекте (кто это, что значит словечко,
  как тут принято) — запиши remember_fact одной фразой; «забудь» — forget_fact. Он видит и правит этот список сам.
  noticed в срезе — что агент заметил сам, владелец не подтвердил: не выдавай за факт, при расхождении верь владельцу.
- Ссылка в сообщении: видео-исходник (YouTube, Twitch, файл) — add_source_from_url; картинка, гифка, мем
  в клип — add_image_from_url / add_video_from_url (группа assets).
- Приложенные файлы: «файл для монтажа #N» — asset_id для add_insert и слоёв; «источник #N» — source_id
  для analyze_source, create_clip, get_source_transcript.
- Стиль монтажа владельца: без эмодзи, текст по центру, динамика наездами и звуком в слово.
- Стрим режем так: посмотри его, запиши, что заметил о проекте (note_observation), спроси, что непонятно
  (add_stream_questions; кто это и что за шутка — level: project), дождись ответов, потом режь. Пока ответов
  нет, нарезка и вырезы откажут и назовут открытые вопросы: скажи об этом владельцу, не обходи. Снять паузу может только он.
- Новая часть ролика с маскотом — группа explainer: explainer_overview, потом explainer_create. Объяснялка делается
  в проекте любого вида: тип и настройки проекта ради неё не меняй. Голос у маскота один на все ролики: не меняй его
  сам, даже чтобы не платить. Платный голос и новые позы запускает только «Разрешить» владельца: скажи сумму и что
  выбор за ним: разрешить или сменить голос маскота. Про голос, цену, субтитры и что не сделано говори только то, что
  написано в полях summary, voice, spent_rub, skipped результата explainer_status: своими догадками не дополняй. Пока
  status не done — клипа нет, так и скажи.
"""

PROJECT_NOTE = ("\nЭто переписка проекта-серии/канала #{pid}. В инструментах проекта project_id — id этого "
                "проекта (он ниже); у клиповых — clip_id. Лор и правила других проектов отсюда недоступны.\n"
                "Задание на проект: новый стрим по ссылке — add_source_from_url (видео ляжет в этот проект, скачается "
                "в фоне), уже скачанное — analyze_source(source_id).\n")
VIDEO_NOTE = ("\nЭто чат одного видео: source_id {sid}, «{title}». «Это видео», «стрим», «здесь» — оно. Инструментам "
              "источника давай source_id {sid}, клипы этого видео — list_clips(source_id={sid}), ещё нарезать — "
              "analyze_source(source_id={sid}). Что про него известно — в блоке video среза.\n")
GENERAL_NOTE = ("\nЭто общий чат, без проекта. Проекты-серии/каналы и источники — в срезе ниже; инструментам "
                "проекта давай project_id (list_channel_projects) или source_id. Просят новую серию про героя, а он "
                "похож на маскота проекта (поле mascots): это объяснялка с этим маскотом, группа explainer, нового "
                "персонажа не заводи.\n")
MASCOT_NOTE = ("\nУ этого проекта уже есть маскот: {names} (блок mascots среза). Новая серия, часть или ролик, где не "
               "назван другой герой, — это объяснялка с ним: load_tools с группой explainer, "
               "explainer_overview(project_id={pid}), explainer_create(mascot_id, topic). Нового персонажа не заводи и "
               "внешность не придумывай (aiv_define_character), пока владелец прямо не попросит другого героя; "
               "не ясно, о ком речь, — спроси.\n")


def video_context(store: Any, source_id: int) -> dict:
    """Какое это видео: для чата видео, в каждом ходе. Ответы владельца — только про него."""
    from app.stream_questions import answers_brief

    source = store.get_source(int(source_id))
    plans = store.db.query_one("SELECT COUNT(*) AS n FROM clip_plans WHERE source_id = ? AND hidden = 0", (int(source_id),))
    return {"source_id": int(source_id), "title": _video_title(store, source_id), "status": source.get("status"),
            "duration_sec": source.get("duration_sec"), "size": [source.get("width"), source.get("height")],
            "original_url": source.get("original_url") or "", "has_transcript": bool(source.get("has_transcript")),
            "moments_and_clips": int(plans["n"]), "owner_answers": answers_brief(store, int(source_id))}


def project_context(store: Any, project_id: int, source_id: int | None = None) -> str:
    """Свежий срез проекта в каждый ход: бриф, правила, статистика, гипотезы, одобрение, расписание.
    Текст обрезается по длине, поэтому сначала идёт то, что агент знает, а длинные списки — в конце."""
    brief = P.project_brief(store, project_id, covered_limit=30)
    project = brief.pop("project")
    sources = project.get("sources") or []
    project = {**project, "sources": sources[:CONTEXT_SOURCES], "sources_total": len(sources)}
    stats = P.project_stats(store, project_id, top=3)
    stats.pop("items", None)
    approvals = [{k: a.get(k) for k in ("id", "kind", "status", "title", "question", "decision_comment",
                                          "scheduled_local", "publish_status")}
                 for a in A.list_approvals(store, project_id, status="pending,changes_requested", limit=20)]
    schedule = A.get_schedule(store, project_id)
    cfg = A.get_config(store, project_id)
    mascots = K.project_mascots(store, int(project_id))
    data = {
        "project_id": int(project_id),
        **({"video": video_context(store, int(source_id))} if source_id else {}),
        **({"mascots": mascots} if mascots else {}),
        "brief": brief, "stats": stats, "approvals_open": approvals,
        "schedule": {"timezone": schedule["timezone"], "upcoming": [
            {k: u.get(k) for k in ("id", "title", "scheduled_local", "publish_status", "platforms")}
            for u in schedule["upcoming"][:10]]},
        "autopilot": {"trust": cfg["trust"], "slots": cfg["slots"], "per_day": cfg["per_day"],
                      "auto_rules": cfg["auto_rules"]},
        "project": project,
    }
    text = json.dumps(data, ensure_ascii=False, default=str)
    return text if len(text) <= CONTEXT_CHARS else text[:CONTEXT_CHARS] + "…(обрезано)"


def general_context(store: Any) -> str:
    projects = []
    for p in P.list_projects(store):
        mascots = [{k: m[k] for k in ("mascot_id", "name", "about")} for m in K.project_mascots(store, p["id"])]
        projects.append({"project_id": p["id"], "name": p["name"], "kind": p.get("kind"),
                         "rules": p.get("active_rules_count"), **({"mascots": mascots} if mascots else {})})
    sources = [{"source_id": s["id"], "name": s.get("original_filename") or "", "status": s.get("status"),
                "duration_sec": s.get("duration_sec"), "clips": s.get("clip_plans_count"),
                "channel_project_id": s.get("project_id")} for s in store.list_sources(limit=40)]
    counts = store.count_sources()
    # Число — отдельно от списка: иначе на «сколько у меня источников» модель считает 40 строк.
    text = json.dumps({"channel_projects": projects, "channel_projects_total": len(projects),
                       "sources_total": counts["total"], "sources_by_status": counts["by_status"],
                       "sources_note": f"ниже последние {len(sources)} из {counts['total']} источников; "
                                       "остальные — list_projects(offset=…)",
                       "sources": sources}, ensure_ascii=False, default=str)
    return text if len(text) <= CONTEXT_CHARS else text[:CONTEXT_CHARS] + "…(обрезано)"


def _heuristic_summary(old_summary: str, messages: list[dict]) -> str:
    lines = [old_summary] if old_summary else []
    for m in messages:
        who = "Владелец" if m["role"] == "user" else "Агент"
        text = " ".join(str(m.get("text") or "").split())
        if text:
            lines.append(f"{who}: {text[:160]}")
    out = "\n".join(lines)
    return out[-SUMMARY_CHARS:]


def summarize_thread(store: Any, thread_id: int, client: Any = None) -> bool:
    """Старые реплики → резюме (Gemini, если есть; иначе выжимка строк). True — сжали."""
    thread = get_thread(store, thread_id)
    upto = int(thread.get("summary_upto") or 0)
    fresh = [m for m in thread_messages(store, thread_id, after=upto, limit=1000) if m["status"] != "running"]
    if len(fresh) <= SUMMARIZE_AFTER:
        return False
    old = fresh[:-HISTORY_KEEP]
    summary = ""
    if client is not None:
        prompt = ("Сожми переписку владельца с агентом в 5–12 пунктов: решения, договорённости, "
                  "просьбы владельца, открытые вопросы, id клипов и источников. По-русски, без воды.\n\n"
                  f"Прежнее резюме:\n{thread.get('summary') or '—'}\n\nНовые реплики:\n" + _heuristic_summary("", old))
        try:
            from app.ai.publish_meta import _extract_response_text

            response = client.generate_content(settings.gemini_text_model,
                                               {"contents": [{"role": "user", "parts": [{"text": prompt}]}]})
            summary = _extract_response_text(response).strip()[:SUMMARY_CHARS]
        except Exception as exc:  # noqa: BLE001 - не сжалось моделью — сожмём строками
            _log.info("chat summary via model failed: %s", exc)
    summary = summary or _heuristic_summary(thread.get("summary") or "", old)
    update_thread(store, thread_id, summary=summary, summary_upto=int(old[-1]["id"]))
    if thread["is_default"] and thread["project_id"]:
        A.set_state(store, thread["project_id"], chat_summary=summary, chat_summary_upto=int(old[-1]["id"]))
    return True


def maybe_summarize(store: Any, project_id: int, client: Any = None) -> bool:
    return summarize_thread(store, default_thread(store, project_id)["id"], client)


# --------------------------------------------------------------------------- #
# Инструменты (совместимость с прежним чатом проекта)
# --------------------------------------------------------------------------- #


def chat_tools() -> list:
    known = CT.all_tools()
    return [known[n] for n in CT.base_names()]


def _declarations(json_schema: bool = True, groups: list[str] | None = None) -> list[dict]:
    return CT.declarations(list(groups or []), json_schema)


# --------------------------------------------------------------------------- #
# Живое состояние хода (для SSE)
# --------------------------------------------------------------------------- #


class _Run:
    def __init__(self, thread_id: int, project_id: int | None, reply_id: int) -> None:
        self.thread_id = int(thread_id)
        self.project_id = project_id
        self.reply_id = int(reply_id)
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


def active_run(thread_id: int) -> _Run | None:
    with _LOCK:
        run = _RUNS.get(int(thread_id))
        if run is not None and run.thread is not None and not run.thread.is_alive() and run.done:
            return None
        return run if run is not None and not run.done else None


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


# --------------------------------------------------------------------------- #
# Модель: поток или целиком
# --------------------------------------------------------------------------- #


def _pure_text(part: dict) -> bool:
    return "text" in part and not part.get("thought") and set(part) <= {"text", "thoughtSignature"}


def _merge_part(parts: list[dict], part: dict) -> None:
    if _pure_text(part) and parts and _pure_text(parts[-1]) and "thoughtSignature" not in parts[-1]:
        parts[-1]["text"] += part.get("text") or ""
        if part.get("thoughtSignature"):
            parts[-1]["thoughtSignature"] = part["thoughtSignature"]
        return
    parts.append(dict(part))


def call_model(gemini: Any, model: str, payload: dict, run: _Run | None = None) -> dict:
    """Ответ модели; если клиент умеет поток — текст по кусочкам в ``run``. Сбой потока до
    первого куска — повтор обычным запросом (у него ретраи и прокси)."""
    stream = getattr(gemini, "stream_generate_content", None)
    if stream is None:
        if run:
            run.set_phase("thinking")
        return gemini.generate_content(model, payload)
    parts: list[dict] = []
    feedback = None
    finish = None
    got = False
    try:
        for chunk in stream(model, payload):
            got = True
            if run and run.cancel.is_set():
                raise ChatCancelled()
            if not isinstance(chunk, dict):
                continue
            feedback = chunk.get("promptFeedback") or feedback
            candidates = chunk.get("candidates") or []
            if not candidates:
                continue
            finish = candidates[0].get("finishReason") or finish
            for part in (candidates[0].get("content") or {}).get("parts") or []:
                if not isinstance(part, dict):
                    continue
                _merge_part(parts, part)
                if run and _pure_text(part) and part.get("text"):
                    run.add_text(part["text"])
    except ChatCancelled:
        raise
    except Exception as exc:  # noqa: BLE001
        if got:
            raise
        _log.info("chat: stream failed before first chunk (%s) — plain request", exc)
        return gemini.generate_content(model, payload)
    if not parts:
        return {"candidates": [], "promptFeedback": feedback} if feedback else {"candidates": []}
    return {"candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": finish}]}


# --------------------------------------------------------------------------- #
# Ход агента
# --------------------------------------------------------------------------- #


def _service_note(learned: dict | None, decided: dict | None) -> str:
    parts = []
    if decided:
        parts.append(decided.get("note") or "")
    text = A.describe_learning(learned)
    if text:
        parts.append(text)
    return " ".join(p for p in parts if p).strip()


def _quick_decision(store: Any, project_id: int, text: str, actor: str) -> dict | None:
    """«да»/«нет» в чате — ответ на последнее «Записать правило?» из этого чата."""
    cls = A.classify_reply(text)
    if cls["intent"] not in ("approve", "reject") or cls.get("praise"):
        return None
    row = store.db.query_one(
        "SELECT id FROM approvals WHERE project_id = ? AND kind = 'rule_proposal' AND status = 'pending' "
        "AND submitted_by = 'chat' ORDER BY id DESC LIMIT 1", (int(project_id),))
    if not row:
        return None
    result = A.decide(store, int(row["id"]), cls["intent"], actor=actor)
    from app.notify_tg import decision_note

    return {"approval_id": int(row["id"]), "status": result["status"], "note": decision_note(result)}


RULE_JUDGE = """Владелец канала написал агенту реплику. Это ПРАВИЛО на будущее для всего проекта (что делать/не делать
всегда, оценка типа контента) или РАЗОВАЯ правка конкретного клипа? Ответь только JSON:
{{"rule": true|false, "kind": "do"|"dont", "text": "правило коротко, по-русски"}}
Реплика: «{text}»"""


def rule_judge(gemini: Any) -> Callable[[str], dict | None]:
    """Спорная реплика (без «всегда/больше не…» и без «убери этот…») → Gemini одной короткой
    подсказкой. Не ответил или ответ не разобрать — None, решает эвристика автопилота."""

    def judge(text: str) -> dict | None:
        from app.ai.gemini import _extract_response_text

        response = gemini.generate_content(settings.gemini_text_model, {
            "contents": [{"role": "user", "parts": [{"text": RULE_JUDGE.format(text=str(text)[:600])}]}],
            "generationConfig": {"maxOutputTokens": 200, "responseMimeType": "application/json"}})
        raw = _extract_response_text(response).strip()
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(match.group(0)) if match else None
        if not isinstance(data, dict) or not isinstance(data.get("rule"), bool):
            return None
        return {"rule": data["rule"], "kind": data.get("kind"), "text": str(data.get("text") or "")[:300]}

    return judge


def _system(thread: dict, groups: list[str]) -> str:
    text = SYSTEM.format(groups=CT.groups_hint())
    if thread.get("project_id"):
        text += PROJECT_NOTE.format(pid=thread["project_id"])
        if thread.get("mascots"):
            text += MASCOT_NOTE.format(names=", ".join(m["name"] for m in thread["mascots"]), pid=thread["project_id"])
    elif not thread.get("source_id"):
        text += GENERAL_NOTE
    if thread.get("source_id"):
        text += VIDEO_NOTE.format(sid=thread["source_id"], title=thread.get("source_title") or "")
    if groups:
        text += "\nПодгружены группы: " + ", ".join(groups) + ".\n"
    if any(g in groups for g in ("montage", "subtitles", "screens")):
        from app.ai.editor_agent import CRAFT

        text += "\nРЕМЕСЛО МОНТАЖА (для правок клипа; справка по слоям — get_motion_reference)\n" + CRAFT
    if thread.get("summary"):
        text += f"\nРезюме прежней переписки:\n{thread['summary']}\n"
    return text


def _short(text: str, limit: int = 50) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _checkpoint(store: Any, thread_id: int, message_id: int, clip_plan_id: int, label: str) -> int | None:
    from app.clip_history import capture, snapshot

    state = snapshot(store, clip_plan_id, label)  # Ctrl+Z в редакторе — тот же шаг
    if state is None:
        try:
            state = capture(store, clip_plan_id)
        except Exception:  # noqa: BLE001
            return None
    cur = store.db.execute(
        "INSERT INTO chat_checkpoints (thread_id, message_id, clip_plan_id, label, state_json) VALUES (?, ?, ?, ?, ?)",
        (int(thread_id), int(message_id), int(clip_plan_id), label, _dumps(state)))
    return int(cur.lastrowid)


def _step_from_result(step: dict, result: Any, ok: bool) -> dict:
    from app.ai.editor_agent import _step_from_result as base

    base(step, result, ok)
    step["result"] = CT.result_preview(result)
    return step


def run_turn(store: Any, thread: dict | int, message: str, *, actor: str = "chat:ui", message_id: int | None = None,
             reply_id: int | None = None, history: list[dict] | None = None, client: Any = None,
             on_step: Callable[[list], None] | None = None, run: _Run | None = None,
             max_rounds: int = MAX_ROUNDS) -> dict:
    """Один ход: обучение на словах владельца (в проекте), затем модель ↔ инструменты.
    Возвращает ``{text, steps, learned, decided, ai, model, changes}``."""
    from app.ai.editor_agent import _clean_reply, _history_contents, _parts_text, _prune_images, _response_object, \
        _shrink_frame

    if not isinstance(thread, dict):
        thread = get_thread(store, int(thread))
    project_id = thread.get("project_id")
    source_id = thread.get("source_id")
    if source_id:
        thread = {**thread, "source_title": _video_title(store, int(source_id))}
    if project_id:
        thread = {**thread, "mascots": K.project_mascots(store, int(project_id))}
    learned = decided = remembered = None
    gemini = _gemini(client)
    if project_id:
        cfg = A.get_config(store, project_id)
        decided = _quick_decision(store, project_id, message, actor)
        if decided is None:
            learned = A.learn_from_text(store, project_id, message, origin="chat",
                                        origin_ref=f"chat#{message_id}" if message_id else "chat",
                                        auto_rules=cfg["auto_rules"],
                                        judge=rule_judge(gemini) if gemini is not None else None)
            if (learned or {}).get("action") == "none":
                # Не правило, а факт: «запомни: Глеб — модер». Модель для этого не нужна.
                remembered = K.remember_from_chat(store, project_id, message.split("\n[Прикреплён")[0],
                                                  origin_ref=f"chat#{message_id}" if message_id else "chat",
                                                  source_id=int(source_id) if source_id else None)
    note = " ".join(x for x in (_service_note(learned, decided), K.remembered_note(remembered)) if x)
    if gemini is None:
        text = ("ИИ-агент сейчас недоступен: в сервисе не настроен GEMINI_API_KEY. Сообщение сохранено. "
                "Что сделать: добавь ключ Gemini из AI Studio в .env сервиса и перезапусти его.") \
            + (f"\n{note}" if note else "")
        return {"text": text, "steps": [], "learned": learned, "decided": decided, "remembered": remembered,
                "ai": False, "changes": []}

    groups = list(dict.fromkeys([*thread.get("tool_groups", []), *CT.preload_for(message)]))
    if project_id:
        context = project_context(store, project_id, source_id=source_id)
    elif source_id:  # видео вне проектов: лора нет, только оно само
        context = json.dumps({"video": video_context(store, int(source_id))}, ensure_ascii=False, default=str)
    else:
        context = general_context(store)
    label = "срез проекта" if project_id else ("срез видео" if source_id else "срез сервиса")
    first = f"{message.strip()}\n\n---\nСвежий {label}:\n{context}"
    if note:
        first += f"\n\nСервис уже сделал: {note}"
    contents = _history_contents(history or []) + [{"role": "user", "parts": [{"text": first}]}]
    if len(contents) > 1 and contents[-2]["role"] == "user":  # история кончилась вопросом без ответа
        contents.insert(-1, {"role": "model", "parts": [{"text": "(ответ прерван)"}]})
    steps: list[dict] = []
    changes: list[dict] = []
    ctxs: dict[int, ToolContext] = {}
    use_json = True
    used_model = thread.get("model") or settings.gemini_agent_model
    final = ""

    def emit(phase: str | None = None) -> None:
        if run:
            run.set_steps(steps, phase)
        if on_step:
            on_step(copy.deepcopy(steps))

    def clip_ctx(clip_id: int) -> ToolContext:
        if clip_id not in ctxs:
            def first_change(cid: int = clip_id) -> None:
                if reply_id is None:
                    return
                cp = _checkpoint(store, thread["id"], reply_id, cid, f"Чат: {_short(message)}")
                if cp:
                    changes.append({"checkpoint_id": cp, "clip_plan_id": cid})

            ctxs[clip_id] = ToolContext(store=store, clip_plan_id=clip_id, on_first_change=first_change)
        return ctxs[clip_id]

    for rounds in range(max_rounds + 1):
        if run and run.cancel.is_set():
            raise ChatCancelled()
        tools_on = rounds < max_rounds
        payload: dict = {"systemInstruction": {"parts": [{"text": _system(thread, groups)}]}, "contents": contents,
                         "generationConfig": {"maxOutputTokens": MAX_OUTPUT_TOKENS}}
        if tools_on:
            payload["tools"] = [{"functionDeclarations": CT.declarations(groups, use_json)}]
        elif rounds:
            contents.append({"role": "user", "parts": [{"text": "Лимит шагов исчерпан — коротко отчитайся, что "
                                                                 "сделал и что осталось."}]})
        try:
            response = call_model(gemini, used_model, payload, run)
        except RuntimeError as exc:
            if tools_on and use_json and "parametersJsonSchema" in str(exc):
                use_json = False
                payload["tools"] = [{"functionDeclarations": CT.declarations(groups, False)}]
                response = call_model(gemini, used_model, payload, run)
            else:
                raise
        candidates = (response or {}).get("candidates") if isinstance(response, dict) else None
        if not candidates:
            blocked = ((response or {}).get("promptFeedback") or {}).get("blockReason") \
                if isinstance(response, dict) else None
            if blocked:
                final = f"Gemini остановился на своём фильтре контента ({blocked}). Переформулируй или выбери " \
                        f"другую модель."
                break
            raise RuntimeError("модель не ответила")
        parts = [p for p in (candidates[0].get("content") or {}).get("parts") or [] if isinstance(p, dict)]
        calls = [p for p in parts if isinstance(p.get("functionCall"), dict)]
        if parts:
            contents.append({"role": "model", "parts": parts})
        if not calls:
            final = _clean_reply(_parts_text(parts))
            break
        said = _parts_text(parts)
        if run:
            run.take_text()
        if said:
            steps.append({"tool": SAY, "label": said[:600], "ok": True, "kind": "say"})
        responses = []
        images: list[dict] = []
        for part in calls:
            if run and run.cancel.is_set():
                raise ChatCancelled()
            call = part["functionCall"]
            name = str(call.get("name") or "")
            args = call.get("args") if isinstance(call.get("args"), dict) else {}
            tool = CT.all_tools().get(name)
            label_text = tool.step_label(args) if tool else f"Неизвестный инструмент {name}"
            from app.ai.editor_agent import _brief_args

            step: dict = {"tool": name, "label": label_text, "ok": None, "args": _brief_args(args), "kind": "tool"}
            if tool and tool.scope == "clip" and args.get("clip_id") not in (None, ""):
                try:
                    step["clip_id"] = int(args["clip_id"])
                except (TypeError, ValueError):
                    pass
            steps.append(step)
            emit("tool")
            if tool and CT.is_dangerous(store, name, args, project_id):
                conf_id = None
                if reply_id is not None:
                    cur = store.db.execute(
                        "INSERT INTO chat_confirmations (thread_id, message_id, tool, args_json, label) "
                        "VALUES (?, ?, ?, ?, ?)", (thread["id"], reply_id, name, _dumps(args), label_text))
                    conf_id = int(cur.lastrowid)
                step.update(kind="confirm", ok=None, confirmation_id=conf_id, danger=CT.danger_label(name))
                result: Any = {"ok": False, "pending_confirmation": True,
                               "note": "не выполнено: ждёт подтверждения владельца кнопкой в чате; не повторяй "
                                       "вызов, скажи владельцу, что ждёшь подтверждения"}
            else:
                group = CT.group_of(name) if tool is not None and name != CT.LOAD_TOOLS else None
                if group and group not in groups:
                    groups.append(group)  # позвала инструмент неподгруженной группы — оставим её
                result, ok, clip_id = CT.run(store, name, args, project_id=project_id, clip_ctx=clip_ctx)
                _step_from_result(step, result, ok)
                if ok and name == CT.LOAD_TOOLS:
                    groups = list(dict.fromkeys([*groups, *(result.get("loaded") or [])]))
                if clip_id and "clip_id" not in step:
                    step["clip_id"] = clip_id
                preview = CT.preview_for(store, name, result, clip_id) if ok else None
                if preview:
                    step["preview"] = preview
            emit()
            body = _response_object(result)
            if isinstance(result, ImageResult):
                data, mime = _shrink_frame(result)
                body["note"] = "кадр приложен следующей картинкой"
                images.append({"text": f"Кадр на {result.info.get('t')} с:"})
                images.append({"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode("ascii")}})
            response_part: dict = {"functionResponse": {"name": name, "response": body}}
            if call.get("id"):
                response_part["functionResponse"]["id"] = call["id"]
            responses.append(response_part)
        contents.append({"role": "user", "parts": responses + images})
        _prune_images(contents)
    if not final:
        done = [s["label"] for s in steps if s.get("ok") and s.get("kind") == "tool"]
        final = note or ("Сделал:\n" + "\n".join(f"- {d}" for d in done[:20]) if done else "Готово.")
    try:
        if sorted(groups) != sorted(thread.get("tool_groups", [])):
            update_thread(store, thread["id"], tool_groups=groups)
    except KeyError:
        pass
    return {"text": final, "steps": steps, "learned": learned, "decided": decided, "remembered": remembered, "ai": True,
            "model": used_model, "changes": changes}


# --------------------------------------------------------------------------- #
# Фоновые ходы
# --------------------------------------------------------------------------- #


def _attachments_note(attachments: list[dict]) -> tuple[str, list[dict]]:
    clean, lines = [], []
    for item in attachments or []:
        kind = str(item.get("kind") or "")
        try:
            ident = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        name = str(item.get("name") or "")[:120]
        media = str(item.get("media") or "")[:20]
        if kind == "asset":
            lines.append(f"[Прикреплён файл для монтажа #{ident} «{name}» ({media or 'файл'}) — asset_id {ident}]")
        elif kind == "source":
            lines.append(f"[Прикреплён источник #{ident} «{name}» — source_id {ident}]")
        else:
            continue
        clean.append({"kind": kind, "id": ident, "name": name, "media": media})
    return "\n".join(lines), clean


def send_to_thread(store: Any, thread_id: int, text: str, *, origin: str = "ui", actor: str = "chat:ui",
                   client: Any = None, attachments: list[dict] | None = None,
                   on_done: Callable[[str], None] | None = None) -> dict:
    """Сообщение владельца → ход агента в фоне. Возвращает {message_id, reply_id, thread_id}."""
    text = str(text or "").strip()
    note, files = _attachments_note(attachments or [])
    if not text and not files:
        raise ValueError("пустое сообщение")
    thread = get_thread(store, thread_id)
    with _LOCK:
        current = _RUNS.get(int(thread_id))
        if current is not None and not current.done:
            raise ChatBusy("агент ещё отвечает на прошлое сообщение")
        history = [m for m in thread_messages(store, thread_id, after=int(thread.get("summary_upto") or 0))
                   if m["status"] != "running"][-HISTORY_KEEP:]
        user_id = add_thread_message(store, thread_id, "user", text or "(файл)", origin=origin,
                                     meta={"attachments": files} if files else None)
        model = thread.get("model") or settings.gemini_agent_model
        reply_id = add_thread_message(store, thread_id, "assistant", "", status="running", origin=origin, model=model)
        if thread["title"] in ("Новый чат", "") and not thread["is_default"] and text:
            update_thread(store, thread_id, title=_short(text, 60))
        run = _Run(int(thread_id), thread.get("project_id"), reply_id)
        _RUNS[int(thread_id)] = run
        prompt = (text + ("\n" + note if note else "")).strip()

        def work() -> None:
            def on_step(steps: list) -> None:
                update_message(store, reply_id, steps=steps)

            reply = ""
            try:
                result = run_turn(store, get_thread(store, thread_id), prompt, actor=actor, message_id=user_id,
                                  reply_id=reply_id, history=history, client=client, on_step=on_step, run=run)
                meta = {"changes": result.get("changes") or [],
                        **({"remembered": result["remembered"]} if result.get("remembered") else {})}
                update_message(store, reply_id, text=result["text"], steps=result["steps"],
                               learned=result.get("learned") or {}, status="done",
                               model=result.get("model") or "", meta=meta)
                reply = result["text"]
            except ChatCancelled:
                partial = run.snapshot()
                reply = (partial["text"].strip() + "\n\n" if partial["text"].strip() else "") + \
                    "Остановлено. Уже сделанные правки остались — «Откатить» в чате или Ctrl+Z в редакторе."
                update_message(store, reply_id, text=reply, steps=partial["steps"], status="cancelled",
                               meta={"changes": _changes_of(store, reply_id)})
            except Exception as exc:  # noqa: BLE001 - честно: ИИ недоступен
                _log.warning("chat thread %s failed: %s", thread_id, exc)
                partial = run.snapshot()
                reply = f"ИИ-агент не ответил. {error_hint(exc)} Сообщение сохранено."
                update_message(store, reply_id, text=reply, status="error", steps=partial["steps"],
                               meta={"changes": _changes_of(store, reply_id)})
            try:
                summarize_thread(store, thread_id, _gemini(client) if settings.gemini_api_key or client else None)
            except Exception:  # noqa: BLE001
                pass
            with run.lock:
                run.done = True
                run.phase = "done"
                run.steps_seq += 1
            _touch(store, thread_id)
            if on_done:
                try:
                    on_done(reply)
                except Exception as exc:  # noqa: BLE001
                    _log.info("chat on_done failed: %s", exc)

        thread_obj = threading.Thread(target=work, name=f"chat-{thread_id}", daemon=True)
        run.thread = thread_obj
        thread_obj.start()
    return {"message_id": user_id, "reply_id": reply_id, "thread_id": int(thread_id)}


def _changes_of(store: Any, message_id: int) -> list[dict]:
    rows = store.db.query_all("SELECT id, clip_plan_id FROM chat_checkpoints WHERE message_id = ? ORDER BY id",
                              (int(message_id),))
    return [{"checkpoint_id": int(r["id"]), "clip_plan_id": int(r["clip_plan_id"])} for r in rows]


def send(store: Any, project_id: int, text: str, *, origin: str = "ui", actor: str = "chat:ui",
         client: Any = None, on_done: Callable[[str], None] | None = None) -> dict:
    """Сообщение в основную переписку проекта (вкладка «Чат» серии, Telegram)."""
    P.get_project(store, project_id, include_sources=False)
    thread = default_thread(store, project_id)
    return send_to_thread(store, thread["id"], text, origin=origin, actor=actor, client=client, on_done=on_done)


# --------------------------------------------------------------------------- #
# Подтверждения и откат
# --------------------------------------------------------------------------- #


def _confirmation(row: Any) -> dict:
    data = dict(row)
    data["args"] = _loads(data.pop("args_json", "{}"), {})
    data["result"] = _loads(data.pop("result_json", "{}"), {})
    return data


def get_confirmation(store: Any, confirmation_id: int) -> dict:
    row = store.db.query_one("SELECT * FROM chat_confirmations WHERE id = ?", (int(confirmation_id),))
    if not row:
        raise KeyError(f"подтверждение #{confirmation_id} не найдено")
    return _confirmation(row)


def _patch_step(store: Any, message_id: int, key: str, value: int, **fields: Any) -> None:
    try:
        message = get_message(store, message_id)
    except KeyError:
        return
    steps = message["steps"]
    for step in steps:
        if step.get(key) == value:
            step.update(fields)
    update_message(store, message_id, steps=steps)


def decide_confirmation(store: Any, confirmation_id: int, approve: bool, actor: str = "chat:ui") -> dict:
    """Кнопка владельца на карточке опасного действия: выполнить вызов или отказаться."""
    conf = get_confirmation(store, confirmation_id)
    if conf["status"] != "pending":
        raise ValueError(f"уже решено: {conf['status']}")
    thread = get_thread(store, conf["thread_id"])
    if not approve:
        store.db.execute("UPDATE chat_confirmations SET status = 'rejected', decided_at = CURRENT_TIMESTAMP "
                         "WHERE id = ?", (int(confirmation_id),))
        _patch_step(store, conf["message_id"], "confirmation_id", int(confirmation_id), ok=False,
                    error="владелец отказался", confirm_status="rejected")
        add_thread_message(store, thread["id"], "assistant", f"Отменено владельцем: {conf['label']}.",
                           meta={"service": True})
        return {"ok": True, "status": "rejected", "confirmation": get_confirmation(store, confirmation_id)}
    changes: list[dict] = []

    def clip_ctx(clip_id: int) -> ToolContext:
        def first_change() -> None:
            cp = _checkpoint(store, thread["id"], conf["message_id"], clip_id, f"Чат: {conf['label']}")
            if cp:
                changes.append({"checkpoint_id": cp, "clip_plan_id": clip_id})

        return ToolContext(store=store, clip_plan_id=clip_id, on_first_change=first_change)

    result, ok, _clip = CT.run(store, conf["tool"], conf["args"], project_id=thread.get("project_id"),
                               clip_ctx=clip_ctx)
    status = "done" if ok else "failed"
    store.db.execute("UPDATE chat_confirmations SET status = ?, result_json = ?, decided_at = CURRENT_TIMESTAMP "
                     "WHERE id = ?", (status, _dumps(CT.result_preview(result)), int(confirmation_id)))
    fields: dict = {"ok": ok, "confirm_status": status, "result": CT.result_preview(result)}
    if not ok and isinstance(result, dict):
        fields["error"] = str(result.get("error") or "")[:300]
    preview = CT.preview_for(store, conf["tool"], result, None) if ok else None
    if preview:
        fields["preview"] = preview
    _patch_step(store, conf["message_id"], "confirmation_id", int(confirmation_id), **fields)
    if changes:
        try:
            message = get_message(store, conf["message_id"])
            meta = message["meta"]
            meta["changes"] = [*meta.get("changes", []), *changes]
            update_message(store, conf["message_id"], meta=meta)
        except KeyError:
            pass
    text = (f"Владелец подтвердил: {conf['label']} — выполнено." if ok
            else f"Владелец подтвердил: {conf['label']} — не вышло: {fields.get('error') or 'ошибка'}.")
    add_thread_message(store, thread["id"], "assistant", text, meta={"service": True})
    return {"ok": ok, "status": status, "result": CT.result_preview(result),
            "confirmation": get_confirmation(store, confirmation_id)}


def revert_checkpoint(store: Any, checkpoint_id: int) -> dict:
    """«Откатить»: клип — к состоянию до правок агента в этом ответе. Сам откат тоже в истории
    клипа (Ctrl+Z в редакторе вернёт правки агента)."""
    from app.clip_history import restore, snapshot

    row = store.db.query_one("SELECT * FROM chat_checkpoints WHERE id = ?", (int(checkpoint_id),))
    if not row:
        raise KeyError(f"точка отката #{checkpoint_id} не найдена")
    if row["reverted_at"]:
        raise ValueError("уже откачено")
    clip_id = int(row["clip_plan_id"])
    store.get_clip_plan(clip_id, include_segments=False)
    snapshot(store, clip_id, "Чат: перед откатом")
    _fresh, changed = restore(store, clip_id, _loads(row["state_json"], {}))
    store.db.execute("UPDATE chat_checkpoints SET reverted_at = CURRENT_TIMESTAMP WHERE id = ?", (int(checkpoint_id),))
    add_thread_message(store, int(row["thread_id"]), "assistant",
                       f"Откатил правки клипа #{clip_id} из этого ответа.", meta={"service": True})
    return {"ok": True, "clip_plan_id": clip_id, "changes": changed}


# --------------------------------------------------------------------------- #
# Просмотр
# --------------------------------------------------------------------------- #


def _decorate(store: Any, messages: list[dict]) -> list[dict]:
    ids = [m["id"] for m in messages if m["role"] == "assistant"]
    if not ids:
        return messages
    marks = ",".join("?" * len(ids))
    checkpoints: dict[int, list[dict]] = {}
    for row in store.db.query_all(f"SELECT id, message_id, clip_plan_id, reverted_at FROM chat_checkpoints "
                                  f"WHERE message_id IN ({marks}) ORDER BY id", ids):
        brief = CT.clip_brief(store, int(row["clip_plan_id"])) or {"clip_plan_id": int(row["clip_plan_id"])}
        checkpoints.setdefault(int(row["message_id"]), []).append(
            {"checkpoint_id": int(row["id"]), "reverted": bool(row["reverted_at"]), **brief})
    confirmations: dict[int, list[dict]] = {}
    for row in store.db.query_all(f"SELECT * FROM chat_confirmations WHERE message_id IN ({marks}) ORDER BY id", ids):
        conf = _confirmation(row)
        confirmations.setdefault(int(conf["message_id"]), []).append(
            {k: conf[k] for k in ("id", "tool", "args", "label", "status", "result")})
    for m in messages:
        m["changes"] = checkpoints.get(m["id"], [])
        m["confirmations"] = confirmations.get(m["id"], [])
    return messages


def thread_view(store: Any, thread_id: int, after: int = 0) -> dict:
    thread = get_thread(store, thread_id)
    run = active_run(thread_id)
    messages = thread_messages(store, thread_id, after=after)
    for m in messages:
        if m["status"] == "running" and (run is None or run.reply_id != m["id"]):  # сервер перезапускали
            update_message(store, m["id"], status="interrupted", text=m["text"] or "Прервано перезапуском сервера.")
            m["status"], m["text"] = "interrupted", m["text"] or "Прервано перезапуском сервера."
    project = None
    if thread.get("project_id"):
        try:
            p = P.get_project(store, thread["project_id"], include_sources=False)
            project = {"id": p["id"], "name": p["name"], "kind": p.get("kind"),
                       "auto_rules": A.get_config(store, p["id"])["auto_rules"]}
        except KeyError:
            project = None
    video = None
    if thread.get("source_id"):
        video = {"id": int(thread["source_id"]), "title": _video_title(store, int(thread["source_id"]))}
    return {"thread": {k: v for k, v in thread.items()}, "project": project, "video": video,
            "messages": _decorate(store, messages), "running": run is not None,
            "live": run.snapshot() if run else None, "summary": thread.get("summary") or "",
            "model": thread.get("model") or settings.gemini_agent_model, "ai": ai_status(),
            "tool_groups": thread.get("tool_groups") or []}


def chat_view(store: Any, project_id: int, after: int = 0) -> dict:
    """Основная переписка проекта — для вкладки «Чат» серии (прежний ответ + thread_id)."""
    P.get_project(store, project_id, include_sources=False)
    thread = default_thread(store, project_id)
    view = thread_view(store, thread["id"], after=after)
    state = A._state_row(store, project_id)
    from app import notify_tg

    return {"messages": view["messages"], "running": view["running"], "summary": view["summary"],
            "thread_id": thread["id"], "ai_available": view["ai"]["available"],
            "telegram": {"enabled": notify_tg.is_enabled(store), "thread_id": state.get("tg_thread_id"),
                         "root_message_id": state.get("tg_root_message_id")},
            "auto_rules": A.get_config(store, project_id)["auto_rules"]}


def handle_telegram(store: Any, project_id: int, text: str, actor: str, message_id: int) -> dict:
    """Сообщение в теме проекта (или reply на сообщение бота проекта) → ход агента, ответ — туда же."""
    from app import notify_tg

    def reply(answer: str) -> None:
        notify_tg.send_text(store, answer, project_id=project_id, purpose="chat", reply_to=message_id)

    try:
        return send(store, project_id, text, origin="tg", actor=actor, on_done=reply)
    except ChatBusy as exc:
        reply(f"⏳ {exc}")
        return {"busy": True}
