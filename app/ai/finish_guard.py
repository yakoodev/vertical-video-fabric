"""Конец хода агента в чате: без рендера не сдаёт, чужой клип за свой не выдаёт.

Повтор эксперимента 13.11 (Gemini в чате):

* RC1, RC2 — задание «результат — отрендеренный клип», плейбук велит render_clip → render_status, а ход кончился
  списком «что осталось: render_clip → render_status». Пометка сервиса (``render_claims``) не помогла: ответ честно
  говорил «не отрендерено». Теперь цикл (``agent_loop.run_loop`` → ``TurnPolicy.finish_guard``) не даёт закончить ход,
  пока у клипа, который агент правил или создал в этом ходе, нет готового рендера, — если задание просит готовый
  ролик или плейбук велит рендер: реплика сервиса «рендера нет — вызови render_clip и дождись render_status done»,
  не больше ``MAX_FINISH_NUDGES`` раз за ход.
* RC3 — агент нашёл клип 1074, сделанный другим прогоном, и выдал его за свою серию. Клипы, созданные и правленные в
  этой переписке, считаются по её шагам (``thread_clips``); ответ, где результатом назван другой клип, получает
  пометку сервиса (``foreign_note``).
"""

from __future__ import annotations

import re
from typing import Any

MAX_FINISH_NUDGES = 2
# Сколько раундов модели добавить ходу, когда сервис дожимает рендер: render_clip и несколько render_status.
FINISH_BONUS_ROUNDS = 8

# Задание просит готовый ролик: «результат — отрендеренный клип», «готовый ролик», «mp4», «сделай рендер».
WANTS_RENDER_RE = re.compile(
    r"(отрендер|срендер|рендер|render|\bmp4\b|готов(ый|ое|ую)\s+(ролик|клип|видео|mp4)|"
    r"результат\w*\s*[—:-]?\s*(готов|ролик|клип|видео))",
    re.IGNORECASE,
)
# «Не рендерь», «без рендера» — владелец рендер не просит.
NO_RENDER_RE = re.compile(r"(не\s+(от|с)?рендер|без\s+рендер|рендер\s+не\s+нуж|не\s+нужен\s+рендер|render\s*:\s*no)",
                          re.IGNORECASE)


def playbook_wants_render(name: str | None) -> bool:
    """Плейбук хода велит рендер (в его тексте есть render_clip)."""
    if not name:
        return False
    if name == "series":
        from app.project_chat_playbook import SERIES_NOTE

        return "render_clip" in SERIES_NOTE
    from app import playbooks

    try:
        return "render_clip" in playbooks.get_playbook(name)["text"]
    except KeyError:
        return False


def wants_render(message: str, playbook: str | None = None) -> bool:
    text = str(message or "")
    if NO_RENDER_RE.search(text):
        return False
    return bool(WANTS_RENDER_RE.search(text)) or playbook_wants_render(playbook)


def unrendered(store: Any, clips: list[int], since: str | None) -> dict[int, str]:
    """Клипы без готового рендера после правок хода → состояние (``none``/``stale``/``running``)."""
    from app.ai.render_claims import render_state

    out: dict[int, str] = {}
    for clip in clips[:6]:
        try:
            state = render_state(store, int(clip), since)
        except Exception:  # noqa: BLE001 - клип удалён — не наш вопрос
            continue
        if state != "fresh":
            out[int(clip)] = state
    return out


def render_nudge(missing: dict[int, str]) -> str:
    running = [c for c, s in missing.items() if s == "running"]
    absent = [c for c, s in missing.items() if s != "running"]
    parts = []
    if absent:
        names = ", ".join(f"#{c}" for c in absent)
        parts.append(f"рендера нет ({names}) — вызови render_clip(clip_id) и дождись render_status(render_id, "
                     "wait_sec=30) со status done")
    if running:
        names = ", ".join(f"#{c}" for c in running)
        parts.append(f"рендер ещё идёт ({names}) — дождись render_status(render_id, wait_sec=30) со status done")
    return ("Сервис: ход ещё не окончен — задание просит готовый ролик, а " + "; ".join(parts)
            + ". Не пиши «осталось отрендерить»: сделай это сам, потом короткий отчёт с номером рендера.")


def render_guard(store: Any, message: str, playbook: str | None, changed: list[int], since: str | None) -> str | None:
    """Реплика сервиса, если ход нельзя закончить без рендера; иначе None."""
    if not changed or not wants_render(message, playbook):
        return None
    missing = unrendered(store, changed, since)
    return render_nudge(missing) if missing else None


# --------------------------------------------------------------------------- чужие клипы

# «клип #1074», «клип 1074», «[клип #1074](clip:1074)», «clip_id: 1074», «холст #1075». Не «render_clip_id: 1050».
CLIP_MENTION_RE = re.compile(r"(?<![\w])(?:клип\w*|холст\w*|clip(?:_id)?|серия\s+в\s+клипе)\s*[:=]?\s*\**\s*[#№]?\s*(\d{2,7})"
                             r"|clip:(\d{2,7})|/clips/(\d{2,7})", re.IGNORECASE)
RESULT_RE = re.compile(r"(готов|сделал|собрал|смонтировал|отрендер|результат|выпуск|получил\w*\s+(ролик|клип)|"
                       r"серия\s+\d+\s+[^.\n]{0,40}готов)", re.IGNORECASE)
DISCLAIMER_RE = re.compile(r"(чуж|не\s+мо[йяеё]|не\s+в\s+этой\s+переписк|друг(ой|им|ая)\s+(переписк|агент|прогон)|"
                           r"уже\s+был|сделан\w*\s+раньше|до\s+меня|создан\w*\s+не\s|не\s+я\s+дела|не\s+я\s+созда|"
                           r"для\s+справки|образ(ец|ца)|как\s+пример)", re.IGNORECASE)


def mentioned_clips(text: str) -> list[tuple[int, str]]:
    """(id клипа, строка ответа, где он назван)."""
    out: list[tuple[int, str]] = []
    for line in str(text or "").splitlines():
        for m in CLIP_MENTION_RE.finditer(line):
            raw = next(g for g in m.groups() if g)
            out.append((int(raw), line))
    return out


def thread_clips(store: Any, thread_id: int | None, steps: list[dict] | None = None) -> set[int]:
    """Клипы, созданные или правленные в этой переписке: шаги всех её ответов, точки отката и шаги текущего хода."""
    from app.ai.render_claims import touched_clips

    own: set[int] = set(touched_clips(steps or [])[0])
    if thread_id is None:
        return own
    try:
        for row in store.db.query_all("SELECT DISTINCT clip_plan_id FROM chat_checkpoints WHERE thread_id = ?",
                                      (int(thread_id),)):
            own.add(int(row["clip_plan_id"]))
        from app.project_chat_threads import thread_messages

        for msg in thread_messages(store, int(thread_id)):
            own.update(touched_clips(msg.get("steps") or [])[0])
    except Exception:  # noqa: BLE001 - переписки нет (тесты, монтаж без чата) — только этот ход
        pass
    return own


def _clip_exists(store: Any, clip_id: int) -> bool:
    try:
        store.get_clip_plan(int(clip_id), include_segments=False)
        return True
    except Exception:  # noqa: BLE001
        return False


def foreign_note(store: Any, text: str, own: set[int], exempt: set[int] | None = None) -> str:
    """Пометка сервиса, если ответ выдаёт за результат клип, созданный не в этой переписке; иначе пусто."""
    from app.ai.render_claims import claims_render

    if not (claims_render(text) or RESULT_RE.search(str(text or ""))):
        return ""
    foreign: list[int] = []
    for clip, line in mentioned_clips(text):
        if clip in own or clip in (exempt or set()) or clip in foreign or DISCLAIMER_RE.search(line):
            continue
        if _clip_exists(store, clip):
            foreign.append(clip)
    if not foreign:
        return ""
    names = ", ".join(f"#{c}" for c in foreign[:4])
    return (f"⚠ Сервис: клип {names} создан не в этой переписке — это не работа агента здесь, результатом задания его "
            "не считать (кто его сделал — get_clip, поле made_in).")


def with_foreign_note(store: Any, text: str, own: set[int], exempt: set[int] | None = None) -> str:
    try:
        note = foreign_note(store, text, own, exempt)
    except Exception:  # noqa: BLE001 - проверка не должна ронять ответ
        return text
    return f"{text.rstrip()}\n\n{note}" if note else text


# --------------------------------------------------------------------------- кто сделал клип


_EDIT_KINDS = (("Чат:", "chat"), ("ИИ:", "clip_chat"), ("агент:", "mcp"))


def clip_origin(store: Any, clip_id: int) -> dict | None:
    """Кто делал клип: переписка чата, где он создан, переписки, где правлен, и правки по журналу «Шаг назад»
    (чат проекта, чат клипа / ИИ-монтаж, внешний агент MCP, редактор). Ничего не известно — None.
    ``{created_in: {thread_id, title, at}|None, edited_in: [{thread_id, title}], edits: {chat, clip_chat, mcp, editor},
    last_edit_at}``."""
    import json

    cid = int(clip_id)
    created = None
    try:
        rows = store.db.query_all(
            "SELECT m.thread_id, t.title, m.created_at, m.steps_json FROM project_chat_messages m LEFT JOIN chat_threads t "
            "ON t.id = m.thread_id WHERE m.role = 'assistant' AND (m.steps_json LIKE ? OR m.steps_json LIKE ?) "
            "ORDER BY m.id LIMIT 50", (f'%"clip_id": {cid},%', f'%"clip_id": {cid}}}%'))
        for row in rows:
            steps = json.loads(row["steps_json"] or "[]")
            if any(isinstance(st, dict) and st.get("ok") and st.get("tool") in ("create_clip", "create_canvas_clip")
                   and str(st.get("clip_id")) == str(cid) for st in steps):
                created = {"thread_id": int(row["thread_id"]), "title": row["title"] or "", "at": row["created_at"]}
                break
    except Exception:  # noqa: BLE001 - схемы чата нет
        created = None
    try:
        edited = [{"thread_id": int(r["thread_id"]), "title": r["title"] or ""} for r in store.db.query_all(
            "SELECT DISTINCT c.thread_id, t.title FROM chat_checkpoints c LEFT JOIN chat_threads t ON t.id = c.thread_id "
            "WHERE c.clip_plan_id = ? ORDER BY c.thread_id", (cid,))]
    except Exception:  # noqa: BLE001
        edited = []
    edits: dict[str, int] = {}
    last = None
    try:
        for row in store.db.query_all("SELECT label, created_at FROM clip_plan_history WHERE clip_plan_id = ? ORDER BY id",
                                      (cid,)):
            label = str(row["label"] or "")
            kind = next((k for prefix, k in _EDIT_KINDS if label.startswith(prefix)), "editor")
            edits[kind] = edits.get(kind, 0) + 1
            last = row["created_at"]
    except Exception:  # noqa: BLE001
        pass
    if not created and not edited and not edits:
        return None
    return {"created_in": created, "edited_in": edited[:5], "edits": edits, "last_edit_at": last}


_KIND_NAMES = {"chat": "чат проекта", "clip_chat": "чат клипа/ИИ-монтаж", "mcp": "внешний агент (MCP)", "editor": "редактор"}


def origin_label(origin: dict | None) -> str:
    """Коротко для списка клипов: «создан в переписке #46; правки: внешний агент (MCP) 12»."""
    if not origin:
        return ""
    parts = []
    made = origin.get("created_in")
    if made:
        parts.append(f"создан в переписке #{made['thread_id']}" + (f" «{made['title'][:40]}»" if made.get("title") else ""))
    threads = [t["thread_id"] for t in origin.get("edited_in") or [] if not made or t["thread_id"] != made["thread_id"]]
    if threads:
        parts.append("правлен в переписках " + ", ".join(f"#{t}" for t in threads))
    edits = origin.get("edits") or {}
    if edits:
        parts.append("правки: " + ", ".join(f"{_KIND_NAMES.get(k, k)} {n}" for k, n in edits.items()))
    return "; ".join(parts)
