"""«Что агент знает» о проекте: один список, откуда агент берёт лор, и как он копится.

Владелец: «на проект было бы круто общий контекст собирать, чтобы агент разбирался в лоре со
временем». Это не новое хранилище лора, а вид поверх того, что уже есть, и одна новая таблица:

* бриф проекта (``projects.brief_json``): кратко, заметки, люди, сюжеты, шутки, запреты; сюда уже
  пишутся ответы на вопросы про канал (``stream_questions._write_brief``);
* правила владельца (``project_rules``);
* ответы про отдельное видео и выбранные мемы (``stream_questions``, ``meme_moments``);
* ``project_facts``: то, что владелец сказал в чате («запомни: …»), дописал руками на вкладке, или
  агент записал инструментом со слов владельца. Новое здесь только это.

Правка и удаление идут туда, где факт лежит, поэтому другого пути к агенту у факта нет: убрал здесь
— исчез из брифа, из ``answers_brief`` и из всех запросов.

Как копится (без фоновых запросов к моделям, Gemini для этого не нужен):

1. Ответ на вопрос и выбор мема попадают в список сразу, это те же записи.
2. Слова владельца «запомни», «учти», «имей в виду» разбирает сервис регулярным выражением в любом
   чате (проект, видео, клип), модель для этого не нужна.
3. Кнопка «Запомнить» у своей реплики в чате и «Добавить факт» на вкладке.
4. Агент сам зовёт ``remember_fact`` по ходу разговора, если модель есть.
5. Агент изучил стрим и что-то заметил сам: ``note_observation``. Это ``project_facts`` со статусом
   ``unconfirmed``: владелец видит «агент заметил, не подтверждено», одним нажатием подтверждает или
   убирает. Владелец: «лор проектов так же должен дополняться автоматически или через вопросы».
   В запросы такое идёт отдельным блоком с пометкой и последним: сказанное владельцем важнее.
   Подтвердить может только владелец. Убранное агент второй раз не запишет.

Как доходит до агента: чат проекта и видео берут ``projects.project_brief`` в каждый ход
(``project_chat.project_context``), внешние агенты тот же бриф через ``get_project_brief``, а чат
клипа, нарезка, «ИИ выберет лучшие» и ИИ-монтаж получают текстовый блок ``prompt_block``.

Границы проекта: всё выбирается по ``project_id`` проекта, которому принадлежит видео или клип.
Общие между проектами только правила со ``scope='global'``: они для того и заведены.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

from app import projects as P

_log = logging.getLogger(__name__)

MAX_FACT_CHARS = 600
MAX_FACTS = 500
PROMPT_CHARS = 6000
PROMPT_MEMES = 20
WAITING_SHOWN = 5

ORIGIN_OWNER = "owner"
ORIGIN_CHAT = "chat"
ORIGIN_AGENT = "agent"
ORIGIN_NOTICED = "noticed"  # заметил сам, изучая стрим; слов владельца за этим нет
STATUS_UNCONFIRMED = "unconfirmed"
MAX_NOTICED = 100
PROMPT_NOTICED = 15
NOTICED_CAPTION = ("Агент заметил сам, владелец НЕ подтвердил. Может быть неверно: не выдавай за факт, "
                   "разойдётся со сказанным выше — верь владельцу")

FACT_FORGET_NOTE = "Владелец убрал в «Что агент знает»"

_LABELS = {
    "noticed": "Агент заметил, не подтверждено",
    "facts": "Запомнил из чатов и заметок",
    "about": "О проекте",
    "people": "Люди",
    "arcs": "Сюжеты и сквозные темы",
    "jokes": "Шутки и словечки",
    "taboo": "Запретные темы",
    "rules": "Правила",
    "memes": "Выбранные мемы",
    "videos": "Ответы про отдельные видео",
}
_RULE_WORDS = {"do": "обязательно", "dont": "нельзя", "preference": "желательно"}


def _key(text: str) -> str:
    return re.sub(r"\W+", " ", str(text or "").lower()).strip()


def _clean(text: Any) -> str:
    return " ".join(str(text if text is not None else "").replace("\x00", "").split())[:MAX_FACT_CHARS]


def _short_time(seconds: Any) -> str:
    total = int(float(seconds or 0))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _video_titles(store: Any, source_ids: set[int]) -> dict[int, str]:
    if not source_ids:
        return {}
    from app import nav

    ids = sorted(source_ids)
    marks = ",".join("?" for _ in ids)
    rows = [dict(r) for r in store.db.query_all(
        f"SELECT id, source_type, original_filename, created_at FROM sources WHERE id IN ({marks})", ids)]
    return nav.titles_for(store, rows)


# --------------------------------------------------------------------------- #
# Факты из чатов и заметок
# --------------------------------------------------------------------------- #


def _fact(row: Any) -> dict:
    return dict(row)


def list_facts(store: Any, project_id: int) -> list[dict]:
    return [_fact(r) for r in store.db.query_all(
        "SELECT * FROM project_facts WHERE project_id = ? AND status = 'active' ORDER BY id DESC", (int(project_id),))]


def list_noticed(store: Any, project_id: int) -> list[dict]:
    """Что агент заметил сам и владелец ещё не подтвердил. В ``list_facts`` этого нет намеренно."""
    return [_fact(r) for r in store.db.query_all(
        "SELECT * FROM project_facts WHERE project_id = ? AND status = ? ORDER BY id DESC",
        (int(project_id), STATUS_UNCONFIRMED))]


def get_fact(store: Any, project_id: int, fact_id: int) -> dict:
    """Факт ЭТОГО проекта: чужой — как будто его нет."""
    row = store.db.query_one("SELECT * FROM project_facts WHERE id = ? AND project_id = ?",
                             (int(fact_id), int(project_id)))
    if not row:
        raise KeyError(f"факта #{fact_id} нет в этом проекте")
    return _fact(row)


def add_fact(store: Any, project_id: int, text: Any, *, origin: str = ORIGIN_OWNER, quote: Any = "",
             origin_ref: str = "", source_id: int | None = None, clip_plan_id: int | None = None) -> dict:
    """Новый факт проекта. Такой уже есть — вернётся он (``created: False``). Владелец его убирал:
    агент сам не вернёт (``refused``), а слова владельца возвращают."""
    P.get_project(store, int(project_id), include_sources=False)
    text = _clean(text)
    key = _key(text)
    if len(key) < 3:
        raise ValueError("нужен текст факта")
    with store.db._lock:
        old = store.db.query_one("SELECT * FROM project_facts WHERE project_id = ? AND text_key = ?",
                                 (int(project_id), key))
        if old and old["status"] == "active":
            return {"created": False, "fact": _fact(old)}
        if old and old["status"] == STATUS_UNCONFIRMED and origin == ORIGIN_AGENT and not str(quote or "").strip():
            return {"created": False, "fact": _fact(old),
                    "note": "Это твоё наблюдение, владелец его ещё не подтвердил. Подтвердить может только он."}
        if old and old["status"] != STATUS_UNCONFIRMED and origin == ORIGIN_AGENT:
            return {"created": False, "refused": True, "fact": None,
                    "note": "Владелец это убрал, сам не возвращай. Если он передумал, пусть скажет «запомни»."}
        if old:
            store.db.execute(
                "UPDATE project_facts SET status = 'active', text = ?, origin = ?, origin_ref = ?, quote = ?, "
                "source_id = ?, clip_plan_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (text, origin, origin_ref[:200], P._long_text(quote, 2000), source_id, clip_plan_id, int(old["id"])))
            return {"created": True, "fact": get_fact(store, project_id, int(old["id"]))}
        total = store.db.query_one("SELECT COUNT(*) AS n FROM project_facts WHERE project_id = ? AND status = 'active'",
                                   (int(project_id),))
        if int(total["n"]) >= MAX_FACTS:
            raise ValueError(f"в проекте уже {MAX_FACTS} фактов: убери лишние")
        cur = store.db.execute(
            "INSERT INTO project_facts (project_id, text, text_key, origin, origin_ref, quote, source_id, clip_plan_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (int(project_id), text, key, origin, origin_ref[:200], P._long_text(quote, 2000), source_id, clip_plan_id))
        return {"created": True, "fact": get_fact(store, project_id, int(cur.lastrowid))}


def note_fact(store: Any, project_id: int, text: Any, *, quote: Any = "", source_id: int | None = None) -> dict:
    """Агент заметил сам: запись «не подтверждено». Такое уже знаем — останется как было, сказанное
    владельцем не понижается. Владелец это убирал — ``refused``."""
    P.get_project(store, int(project_id), include_sources=False)
    text = _clean(text)
    key = _key(text)
    if len(key) < 3:
        raise ValueError("нужен текст наблюдения")
    with store.db._lock:
        old = store.db.query_one("SELECT * FROM project_facts WHERE project_id = ? AND text_key = ?",
                                 (int(project_id), key))
        if old and old["status"] not in ("active", STATUS_UNCONFIRMED):
            return {"created": False, "refused": True, "fact": None,
                    "note": "Владелец это убрал, сам не возвращай. Если сомневаешься, задай вопрос level: project."}
        if old:
            return {"created": False, "fact": _fact(old)}
        total = store.db.query_one("SELECT COUNT(*) AS n FROM project_facts WHERE project_id = ? AND status = ?",
                                   (int(project_id), STATUS_UNCONFIRMED))
        if int(total["n"]) >= MAX_NOTICED:
            raise ValueError(f"уже {MAX_NOTICED} неподтверждённых наблюдений: пусть владелец разберёт их")
        cur = store.db.execute(
            "INSERT INTO project_facts (project_id, text, text_key, origin, quote, source_id, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (int(project_id), text, key, ORIGIN_NOTICED, P._long_text(quote, 2000), source_id, STATUS_UNCONFIRMED))
        return {"created": True, "fact": get_fact(store, project_id, int(cur.lastrowid))}


def confirm_fact(store: Any, project_id: int, fact_id: int) -> dict:
    """Владелец подтвердил наблюдение: теперь это обычный факт."""
    fact = get_fact(store, project_id, fact_id)
    if fact["status"] not in ("active", STATUS_UNCONFIRMED):
        raise KeyError(f"факта #{fact_id} уже нет")
    store.db.execute("UPDATE project_facts SET status = 'active', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                     (int(fact_id),))
    return get_fact(store, project_id, fact_id)


def update_fact(store: Any, project_id: int, fact_id: int, text: Any) -> dict:
    get_fact(store, project_id, fact_id)
    text = _clean(text)
    key = _key(text)
    if len(key) < 3:
        raise ValueError("нужен текст факта")
    clash = store.db.query_one("SELECT id FROM project_facts WHERE project_id = ? AND text_key = ? AND id != ?",
                               (int(project_id), key, int(fact_id)))
    if clash:  # такой же уже есть или был убран: одна запись на формулировку
        store.db.execute("DELETE FROM project_facts WHERE id = ?", (int(clash["id"]),))
    store.db.execute("UPDATE project_facts SET text = ?, text_key = ?, status = 'active', "
                     "updated_at = CURRENT_TIMESTAMP WHERE id = ?", (text, key, int(fact_id)))
    return get_fact(store, project_id, fact_id)


def delete_fact(store: Any, project_id: int, fact_id: int) -> dict:
    """Запись остаётся скрытой, чтобы агент не записал то же самое снова из старой переписки."""
    get_fact(store, project_id, fact_id)
    store.db.execute("UPDATE project_facts SET status = 'deleted', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                     (int(fact_id),))
    return {"deleted": int(fact_id)}


# --------------------------------------------------------------------------- #
# «Запомни» в чате: без модели
# --------------------------------------------------------------------------- #

_CUE = r"(?:запомни(?:те)?|зафиксируй(?:те)?|учти(?:те)?|имей(?:те)?\s+в\s+виду|на\s+будущее)"
_REMEMBER_HEAD = re.compile(
    rf"^\W*(?:(?:и|а|ну|кстати|пожалуйста)[,\s]+)*{_CUE}(?:\s+пожалуйста)?(?:\s*[:,—–-]\s*|\s+)(?:что\s+)?(?P<fact>\S.*)$",
    re.IGNORECASE | re.DOTALL)
_REMEMBER_TAIL = re.compile(
    rf"^(?P<fact>\S.*?)[\s,.;:—–-]+{_CUE}(?:\s+это)?[.!…\s]*$", re.IGNORECASE | re.DOTALL)
_TOO_VAGUE = {"это", "что", "вот это", "это всё", "это все", "всё это", "все это", "всё", "все", "об этом", "про это"}


def fact_in_message(text: Any) -> str | None:
    """Текст факта из реплики владельца: «запомни: Глеб — модер», «Глеб модер, запомни»."""
    raw = str(text or "").strip()
    if not raw or raw.rstrip().endswith("?"):
        return None
    match = _REMEMBER_HEAD.match(raw) or _REMEMBER_TAIL.match(raw)
    if not match:
        return None
    fact = _clean(match.group("fact")).strip(" ,.;:—–-")
    if len(_key(fact)) < 5 or _key(fact) in _TOO_VAGUE:
        return None
    return fact


def remember_from_chat(store: Any, project_id: int | None, message: Any, *, origin_ref: str = "",
                       source_id: int | None = None, clip_plan_id: int | None = None) -> dict | None:
    """Реплика с «запомни» → факт проекта. Ничего не нашли или нет проекта — None."""
    if not project_id:
        return None
    fact = fact_in_message(message)
    if not fact:
        return None
    try:
        out = add_fact(store, int(project_id), fact, origin=ORIGIN_CHAT, quote=str(message or "")[:2000],
                       origin_ref=origin_ref, source_id=source_id, clip_plan_id=clip_plan_id)
    except (KeyError, ValueError) as exc:  # разговор важнее записи
        _log.info("remember_from_chat: %s", exc)
        return None
    item = out.get("fact") or {}
    if not item:
        return None
    return {"id": item["id"], "text": item["text"], "created": bool(out["created"]), "project_id": int(project_id)}


def remembered_note(remembered: dict | None) -> str:
    if not remembered:
        return ""
    if remembered.get("created"):
        return f"Запомнил: «{remembered['text']}»."
    return f"Уже помню: «{remembered['text']}»."


# --------------------------------------------------------------------------- #
# Единый список для экрана
# --------------------------------------------------------------------------- #


def _channel_links(store: Any, project_id: int) -> dict[str, dict]:
    """Ответы про канал, записанные в бриф: ссылка строки брифа → вопрос (откуда взялось, куда идёт правка)."""
    from app import stream_questions as Q

    rows = store.db.query_all(
        "SELECT q.* FROM stream_questions q JOIN sources s ON s.id = q.source_id "
        "WHERE s.project_id = ? AND q.scope = 'channel' AND q.status = 'answered' ORDER BY q.id", (int(project_id),))
    links: dict[str, dict] = {}
    for row in rows:
        item = Q._question(row)
        links[Q.brief_place(item)] = item
    return links


def _note_ref(prefix_or_line: str) -> str:
    return "brief:notes:" + hashlib.sha1(prefix_or_line.encode("utf-8")).hexdigest()[:10]


def _brief_rows(store: Any, project_id: int, titles: dict[int, str]) -> dict[str, list[dict]]:
    brief = P.get_brief(store, project_id)["brief"]
    links = _channel_links(store, project_id)

    def origin_of(place: str) -> tuple[str, str, str]:
        question = links.get(place)
        if not question:
            return "лор проекта", "", ""
        name = titles.get(int(question["source_id"]), "")
        where = f", «{name}»" if name else ""
        return f"ваш ответ на вопрос{where}", question.get("answered_at") or "", f"/p/{project_id}?tab=questions"

    groups: dict[str, list[dict]] = {name: [] for name in ("about", "people", "arcs", "jokes", "taboo")}
    if brief["summary"]:
        groups["about"].append({"ref": "brief:summary", "text": brief["summary"], "origin": "лор проекта"})
    for line in (brief["notes"] or "").splitlines():
        line = line.strip()
        if not line:
            continue
        place = next((p for p in links if p.startswith("notes:") and line.startswith(p[6:])), "")
        origin, at, href = origin_of(place)
        groups["about"].append({"ref": _note_ref(line), "text": line, "origin": origin, "at": at, "href": href})
    for section, (key, _fields) in P.BRIEF_SECTIONS.items():
        for item in brief[section]:
            place = f"{section}:{str(item[key]).lower()}"
            origin, at, href = origin_of(place)
            if section == "people":
                body = ". ".join(x for x in (item.get("who"), item.get("traits")) if x)
                aliases = item.get("aliases") or []
                note = f"ещё зовут: {', '.join(aliases)}" if aliases else ""
            elif section == "arcs":
                body, note = item.get("about") or "", "сюжет закрыт" if item.get("status") == "closed" else ""
            elif section == "jokes":
                body, note = item.get("about") or "", ""
            else:
                body, note = item.get("why") or "", ""
            groups[section].append({"ref": f"brief:{section}:{item[key]}", "title": item[key], "text": body,
                                    "origin": origin, "at": at, "href": href, **({"note": note} if note else {})})
    return groups


def _noticed_row(fact: dict, titles: dict[int, str]) -> dict:
    name = titles.get(int(fact["source_id"]), "") if fact.get("source_id") else ""
    return {"ref": f"fact:{fact['id']}", "text": fact["text"], "origin": f"заметил в «{name}»" if name else "заметил сам",
            "at": fact.get("updated_at") or "", "href": f"/v/{fact['source_id']}" if fact.get("source_id") else "",
            "quote": fact.get("quote") or "", "confirmable": True}


def _fact_row(fact: dict, titles: dict[int, str], project_id: int) -> dict:
    href = ""
    if fact["origin"] == ORIGIN_NOTICED:
        origin = "агент заметил, вы подтвердили"
        if fact.get("source_id"):
            href = f"/v/{fact['source_id']}"
    elif fact["origin"] == ORIGIN_OWNER:
        origin = "вы добавили"
    elif fact.get("clip_plan_id"):
        origin = f"из чата клипа #{fact['clip_plan_id']}"
        href = f"/clips/{fact['clip_plan_id']}"
    elif fact.get("source_id"):
        name = titles.get(int(fact["source_id"]), "")
        origin = f"из чата видео «{name}»" if name else "из чата видео"
        href = f"/v/{fact['source_id']}"
    else:
        origin = "из чата проекта"
        href = f"/p/{project_id}?tab=chat"
    if fact["origin"] == ORIGIN_AGENT:
        origin += ", записал агент"
    return {"ref": f"fact:{fact['id']}", "text": fact["text"], "origin": origin, "at": fact.get("updated_at") or "",
            "href": href, "quote": fact.get("quote") or ""}


def _rule_row(rule: dict, project_id: int) -> dict:
    is_global = rule.get("scope") == "global" or rule.get("project_id") is None
    origin = "для всех проектов" if is_global else ("ваше правило" if rule.get("quote") else "правило проекта")
    row = {"ref": f"rule:{rule['id']}", "title": _RULE_WORDS.get(rule.get("kind"), ""), "text": rule["text"],
           "origin": origin, "at": rule.get("said_at") or rule.get("created_at") or "",
           "quote": rule.get("quote") or "", "href": f"/p/{project_id}?tab=rules"}
    if is_global:  # у других проектов оно тоже действует: правится на вкладке «Правила»
        row["editable"] = row["removable"] = False
    if rule.get("needs_review"):
        row["note"] = rule.get("review_note") or "правило под вопросом"
    return row


def _chosen_memes(store: Any, project_id: int, source_id: int | None = None, limit: int | None = None) -> list[dict]:
    rows = store.db.query_all(
        """
        SELECT m.id, m.source_id, m.t_start, m.why, m.chosen_at, o.title, o.asset_id
        FROM meme_moments m JOIN sources s ON s.id = m.source_id
        JOIN meme_options o ON o.id = m.chosen_option_id
        WHERE s.project_id = ? AND m.status = 'chosen'
        ORDER BY CASE WHEN m.source_id = ? THEN 0 ELSE 1 END, m.chosen_at DESC, m.id DESC
        """,
        (int(project_id), int(source_id or 0)),
    )
    items = [dict(r) for r in rows]
    return items[:limit] if limit else items


def _meme_row(meme: dict, titles: dict[int, str]) -> dict:
    name = titles.get(int(meme["source_id"]), "")
    where = f"«{name}», {_short_time(meme['t_start'])}" if name else _short_time(meme["t_start"])
    return {"ref": f"meme:{meme['id']}", "title": meme["title"], "text": meme["why"],
            "origin": f"ваш выбор, {where}", "at": meme.get("chosen_at") or "",
            "href": f"/v/{meme['source_id']}/questions", "editable": False}


def _video_answers(store: Any, project_id: int) -> list[dict]:
    """Ответы, которых нет в брифе: про отдельное видео, и про канал, если записать в бриф было некуда."""
    from app import stream_questions as Q

    rows = store.db.query_all(
        "SELECT q.* FROM stream_questions q JOIN sources s ON s.id = q.source_id "
        "WHERE s.project_id = ? AND q.status = 'answered' ORDER BY q.answered_at DESC, q.id DESC", (int(project_id),))
    brief = P.get_brief(store, project_id)["brief"]
    out = []
    for row in rows:
        item = Q._question(row)
        if item["scope"] == "channel" and Q.in_brief(item, brief):
            continue
        out.append(item)
    return out


def _answer_row(item: dict, titles: dict[int, str]) -> dict:
    from app import stream_questions as Q

    name = titles.get(int(item["source_id"]), "")
    origin = f"только для «{name}»" if name else "только для одного видео"
    href = f"/v/{item['source_id']}/questions"
    if item["level"] == "clip" and item.get("clip_plan_id"):
        origin = f"только для клипа #{item['clip_plan_id']}"
        href = f"/clips/{item['clip_plan_id']}"
    return {"ref": f"question:{item['id']}", "title": item["question"], "text": Q.answer_value(item),
            "origin": origin, "at": item.get("answered_at") or "", "href": href}


def _waiting(store: Any, project_id: int) -> dict:
    questions = store.db.query_all(
        "SELECT q.id, q.source_id, q.question, q.about FROM stream_questions q JOIN sources s ON s.id = q.source_id "
        "WHERE s.project_id = ? AND q.status = 'open' "
        "ORDER BY CASE WHEN q.scope = 'channel' THEN 0 ELSE 1 END, q.source_id DESC, q.t_start, q.id",
        (int(project_id),))
    memes = store.db.query_one(
        "SELECT COUNT(*) AS n FROM meme_moments m JOIN sources s ON s.id = m.source_id "
        "WHERE s.project_id = ? AND m.status = 'open'", (int(project_id),))
    return {"questions": len(questions), "memes": int(memes["n"]), "noticed": len(list_noticed(store, project_id)),
            "items": [{"id": int(q["id"]), "source_id": int(q["source_id"]), "text": q["question"]}
                      for q in questions[:WAITING_SHOWN]]}


def knowledge_view(store: Any, project_id: int) -> dict:
    """Всё, что агент знает о проекте, группами; у строки ссылка ``ref`` для правки и удаления."""
    P.get_project(store, int(project_id), include_sources=False)
    facts = list_facts(store, project_id)
    noticed = list_noticed(store, project_id)
    memes = _chosen_memes(store, project_id)
    answers = _video_answers(store, project_id)
    links = _channel_links(store, project_id)
    needed = {int(f["source_id"]) for f in [*facts, *noticed] if f.get("source_id")} | {int(m["source_id"]) for m in memes} \
        | {int(a["source_id"]) for a in answers} | {int(q["source_id"]) for q in links.values()}
    titles = _video_titles(store, needed)
    brief_rows = _brief_rows(store, project_id, titles)
    rules = P.list_rules(store, project_id=int(project_id))
    grouped = {
        "noticed": [_noticed_row(f, titles) for f in noticed],
        "facts": [_fact_row(f, titles, int(project_id)) for f in facts],
        **brief_rows,
        "rules": [_rule_row(r, int(project_id)) for r in rules],
        "memes": [_meme_row(m, titles) for m in memes],
        "videos": [_answer_row(a, titles) for a in answers],
    }
    order = ("noticed", "about", "facts", "people", "jokes", "arcs", "taboo", "rules", "memes", "videos")
    groups = []
    total = 0
    for key in order:
        items = grouped.get(key) or []
        total += len(items)
        if items:
            for item in items:
                item.setdefault("editable", True)
                item.setdefault("removable", True)
            groups.append({"key": key, "title": _LABELS[key], "items": items})
    return {"project_id": int(project_id), "total": total, "groups": groups, "waiting": _waiting(store, project_id)}


# --------------------------------------------------------------------------- #
# Правка и удаление строки: туда, где она лежит
# --------------------------------------------------------------------------- #


def _rule_of_project(store: Any, project_id: int, rule_id: int) -> dict:
    rule = P.get_rule(store, rule_id)
    if rule.get("project_id") is None or int(rule["project_id"]) != int(project_id):
        raise KeyError(f"правила #{rule_id} нет в этом проекте")
    return rule


def _question_of_project(store: Any, project_id: int, question_id: int) -> dict:
    from app import stream_questions as Q

    item = Q.get_question(store, question_id)
    if P.project_of_source(store, int(item["source_id"])) != int(project_id):
        raise KeyError(f"вопроса #{question_id} нет в этом проекте")
    return item


def _meme_of_project(store: Any, project_id: int, moment_id: int) -> dict:
    from app import stream_questions as Q

    moment = Q.get_moment(store, moment_id)
    if P.project_of_source(store, int(moment["source_id"])) != int(project_id):
        raise KeyError(f"момента #{moment_id} нет в этом проекте")
    return moment


def _note_line(store: Any, project_id: int, ref: str) -> tuple[list[str], int]:
    lines = (P.get_brief(store, project_id)["brief"]["notes"] or "").splitlines()
    for index, line in enumerate(lines):
        if line.strip() and _note_ref(line.strip()) == ref:
            return lines, index
    raise KeyError("такой заметки уже нет")


def _brief_item_of(store: Any, project_id: int, section: str, key_value: str) -> dict:
    if section not in P.BRIEF_SECTIONS:
        raise KeyError(f"нет раздела {section}")
    key = P.BRIEF_SECTIONS[section][0]
    brief = P.get_brief(store, project_id)["brief"]
    for item in brief[section]:
        if str(item[key]).lower() == key_value.lower():
            return item
    raise KeyError("такой строки уже нет")


def _linked_question(store: Any, project_id: int, place: str) -> dict | None:
    return _channel_links(store, project_id).get(place)


def update_row(store: Any, project_id: int, ref: str, text: Any, title: Any = None) -> dict:
    """«Поправить»: новый текст строки (и подпись у людей, шуток, сюжетов, запретов)."""
    from app import stream_questions as Q

    P.get_project(store, int(project_id), include_sources=False)
    ref = str(ref or "")
    if ref.startswith("fact:"):
        update_fact(store, project_id, int(ref[5:]), text)
    elif ref.startswith("rule:"):
        rule = _rule_of_project(store, project_id, int(ref[5:]))
        new_text = P._text(text, 500)
        if P.normalize_rule_text(new_text) != P.normalize_rule_text(rule["text"]):
            out = P.add_rule(store, new_text, kind=rule["kind"], project_id=int(project_id), quote=rule.get("quote") or "",
                             origin="ui", replaces=[rule["id"]], force=True)
            if not out.get("created"):
                raise ValueError(out.get("note") or "не удалось заменить правило")
    elif ref.startswith("question:"):
        _question_of_project(store, project_id, int(ref[9:]))
        Q.correct_answer(store, int(ref[9:]), text)
    elif ref == "brief:summary":
        P.update_brief(store, project_id, {"summary": str(text or "")})
    elif ref.startswith("brief:notes:"):
        lines, index = _note_line(store, project_id, ref)
        old = lines[index].strip()
        new_line = " ".join(str(text or "").split())
        if not new_line:
            raise ValueError("нужен текст")
        question = next((q for place, q in _channel_links(store, project_id).items()
                         if place.startswith("notes:") and old.startswith(place[6:])), None)
        if question is not None:
            prefix = f"{question['question']} — "
            answer = new_line[len(prefix):] if new_line.startswith(prefix) else new_line
            Q.correct_answer(store, int(question["id"]), answer)
            new_line = prefix + answer
        lines[index] = new_line
        P.update_brief(store, project_id, {"notes": "\n".join(lines)})
    elif ref.startswith("brief:"):
        _prefix, section, key_value = ref.split(":", 2)
        _update_brief_item(store, project_id, section, key_value, text, title)
    else:
        raise KeyError(f"нет такой строки: {ref[:60]}")
    return knowledge_view(store, project_id)


_BODY_FIELD = {"people": "who", "arcs": "about", "jokes": "about", "taboo": "why"}


def _update_brief_item(store: Any, project_id: int, section: str, key_value: str, text: Any, title: Any) -> None:
    from app import stream_questions as Q

    item = _brief_item_of(store, project_id, section, key_value)
    key = P.BRIEF_SECTIONS[section][0]
    body_field = _BODY_FIELD[section]
    body = " ".join(str(text or "").split())
    new_title = " ".join(str(title).split()) if title not in (None, "") else item[key]
    fresh = {**item, key: new_title, body_field: body}
    if section == "people":
        fresh["traits"] = ""  # в строке «кто» и «приметы» были одним текстом, теперь он новый
    patch_items: list[dict] = [fresh]
    if new_title.lower() != str(item[key]).lower():
        patch_items.insert(0, {key: item[key], "_delete": True})
    question = _linked_question(store, project_id, f"{section}:{str(item[key]).lower()}")
    P.update_brief(store, project_id, {section: patch_items})
    if question is not None:
        Q.correct_answer(store, int(question["id"]), body)


def delete_row(store: Any, project_id: int, ref: str) -> dict:
    """«Убрать»: агент больше этого не получает ни в одном запросе. Вопрос становится пропущенным
    (его ответ можно вернуть в «Вопросах»), мем — «без мема», правило уходит в историю отмен."""
    from app import stream_questions as Q

    P.get_project(store, int(project_id), include_sources=False)
    ref = str(ref or "")
    if ref.startswith("fact:"):
        delete_fact(store, project_id, int(ref[5:]))
    elif ref.startswith("rule:"):
        rule = _rule_of_project(store, project_id, int(ref[5:]))
        P.revoke_rule(store, rule["id"], reason=FACT_FORGET_NOTE)
    elif ref.startswith("question:"):
        item = _question_of_project(store, project_id, int(ref[9:]))
        Q.forget_answer(store, int(item["id"]))
    elif ref.startswith("meme:"):
        moment = _meme_of_project(store, project_id, int(ref[5:]))
        Q.choose_meme(store, int(moment["id"]), none=True)
    elif ref == "brief:summary":
        P.update_brief(store, project_id, {"summary": ""})
    elif ref.startswith("brief:notes:"):
        lines, index = _note_line(store, project_id, ref)
        old = lines.pop(index).strip()
        question = next((q for place, q in _channel_links(store, project_id).items()
                         if place.startswith("notes:") and old.startswith(place[6:])), None)
        P.update_brief(store, project_id, {"notes": "\n".join(lines)})
        if question is not None:
            Q.forget_answer(store, int(question["id"]))
    elif ref.startswith("brief:"):
        _prefix, section, key_value = ref.split(":", 2)
        item = _brief_item_of(store, project_id, section, key_value)
        key = P.BRIEF_SECTIONS[section][0]
        question = _linked_question(store, project_id, f"{section}:{str(item[key]).lower()}")
        P.update_brief(store, project_id, {section: [{key: item[key], "_delete": True}]})
        if question is not None:
            Q.forget_answer(store, int(question["id"]))
    else:
        raise KeyError(f"нет такой строки: {ref[:60]}")
    return knowledge_view(store, project_id)


# --------------------------------------------------------------------------- #
# Что получает агент
# --------------------------------------------------------------------------- #


def brief_extras(store: Any, project_id: int) -> dict:
    """Что дополняет бриф проекта (``projects.project_brief``): факты и выбранные мемы."""
    facts = [{"id": int(f["id"]), "text": f["text"], "origin": f["origin"]} for f in list_facts(store, project_id)]
    noticed = [{"id": int(f["id"]), "text": f["text"], "unconfirmed": True,
                **({"source_id": int(f["source_id"])} if f.get("source_id") else {})}
               for f in list_noticed(store, project_id)[:PROMPT_NOTICED]]
    memes = [{"moment_id": int(m["id"]), "source_id": int(m["source_id"]), "t_start": m["t_start"], "why": m["why"],
              "title": m["title"], "asset_id": m["asset_id"]}
             for m in _chosen_memes(store, project_id, limit=PROMPT_MEMES)]
    return {"facts": facts, "memes": memes, "noticed": noticed}


def _sections(store: Any, project_id: int, source_id: int | None,
              clip_plan_id: int | None = None) -> list[tuple[str, list[str]]]:
    brief = P.get_brief(store, project_id)["brief"]
    rules = [f"{_RULE_WORDS.get(r.get('kind'), 'желательно')}: {r['text']}" for r in P.list_rules(store, project_id=int(project_id))]
    facts = [f["text"] for f in list_facts(store, project_id)]
    about = [x for x in (brief["summary"], *(brief["notes"] or "").splitlines()) if x and x.strip()]
    people = [(f"{p['name']}: " + ". ".join(x for x in (p.get("who"), p.get("traits")) if x)).rstrip(": ")
              + (f" (ещё зовут: {', '.join(p['aliases'])})" if p.get("aliases") else "") for p in brief["people"]]
    jokes = [f"{j['text']}: {j['about']}" if j.get("about") else j["text"] for j in brief["jokes"]]
    arcs = [f"{a['title']}: {a['about']}" if a.get("about") else a["title"] for a in brief["arcs"]]
    taboo = [f"{t['topic']}: {t['why']}" if t.get("why") else t["topic"] for t in brief["taboo"]]
    video: list[str] = []
    if source_id is not None and P.project_of_source(store, int(source_id)) == int(project_id):
        video = _answers_lines(store, int(source_id), brief, clip_plan_id)
    memes = [f"{m['why']}: мем «{m['title']}»" + (f", файл #{m['asset_id']}" if m.get("asset_id") else "")
             for m in _chosen_memes(store, project_id, source_id, limit=PROMPT_MEMES)]
    noticed = [f"[не подтверждено] {f['text']}" for f in list_noticed(store, project_id)[:PROMPT_NOTICED]]
    return [
        ("Правила владельца", rules),
        ("Запретные темы, не брать", taboo),
        ("Про это видео, ответы владельца", video),
        ("Владелец просил запомнить", facts),
        ("О проекте", about),
        ("Люди", people),
        ("Шутки и словечки", jokes),
        ("Сюжеты и сквозные темы", arcs),
        ("Мемы, которые выбрал владелец", memes),
        (NOTICED_CAPTION, noticed),  # последним: не влезет — выпадет первым, сказанное владельцем важнее
    ]


def _answers_lines(store: Any, source_id: int, brief: dict, clip_plan_id: int | None = None) -> list[str]:
    """Ответы этого видео; про клип — только в работе над этим самым клипом."""
    from app import stream_questions as Q

    family = Q._clip_family(store, int(clip_plan_id)) if clip_plan_id else set()
    lines = []
    for item in Q.list_questions(store, source_id, "answered"):
        if item["scope"] == "channel" and Q.in_brief(item, brief):
            continue
        if item["scope"] == "clip" and item.get("clip_plan_id") not in family:
            continue
        lines.append(f"{item['question']} — {Q.answer_value(item)}")
    return lines


def prompt_block(store: Any, project_id: int | None, source_id: int | None = None, limit: int = PROMPT_CHARS,
                 clip_plan_id: int | None = None) -> str:
    """Текст для запроса к модели: только этот проект (и ответы про это видео). Пусто, если знать нечего."""
    if not project_id:
        if source_id is None:
            return ""
        lines = _answers_lines(store, int(source_id), P.empty_brief(), clip_plan_id)
        return ("Ответы владельца про это видео:\n" + "\n".join(f"- {x}" for x in lines)) if lines else ""
    project = P.get_project(store, int(project_id), include_sources=False)
    head = f"ЧТО ТЫ ЗНАЕШЬ О ПРОЕКТЕ «{project['name']}» (так сказал владелец, держись этого):"
    out = [head]
    used = len(head)
    dropped = 0
    found = False
    for title, lines in _sections(store, int(project_id), source_id, clip_plan_id):
        if not lines:
            continue
        found = True
        caption = f"{title}:"
        if used + len(caption) + 1 > limit:
            dropped += len(lines)
            continue
        out.append(caption)
        used += len(caption) + 1
        for line in lines:
            text = f"- {line}"
            if used + len(text) + 1 > limit:
                dropped += 1
                continue
            out.append(text)
            used += len(text) + 1
    if not found:
        return ""
    if dropped:
        out.append(f"(не поместилось ещё {dropped}: всё остальное в get_project_brief)")
    return "\n".join(out)


def project_mascots(store: Any, project_id: int | None) -> list[dict]:
    """Маскоты объяснялок проекта, коротко: чат начинает с них, а не придумывает героя заново."""
    from app import modules

    if not project_id or not modules.is_enabled("explainer", store):
        return []
    rows = store.db.query_all(
        "SELECT m.id, m.name, m.persona, m.identity_card, "
        "(SELECT COUNT(*) FROM expl_poses p WHERE p.mascot_id = m.id) AS poses "
        "FROM expl_mascots m WHERE m.project_id = ? ORDER BY m.id",
        (int(project_id),),
    )
    return [{"mascot_id": int(r["id"]), "name": r["name"], "about": str(r["persona"] or "")[:200],
             "looks": str(r["identity_card"] or "")[:200], "poses": int(r["poses"])} for r in rows]


def mascot_block_for_plan(store: Any, clip_plan_id: int) -> str:
    """Для чата клипа: у него нет инструментов объяснялок, но выдумывать другого героя он не должен."""
    try:
        from app.asset_scope import clip_project

        mascots = project_mascots(store, clip_project(store, int(clip_plan_id)))
    except Exception as exc:  # noqa: BLE001
        _log.warning("mascots for clip plan %s failed: %s", clip_plan_id, exc)
        return ""
    if not mascots:
        return ""
    lines = [f"- {m['name']}: {m['about']} Внешность: {m['looks']}" for m in mascots]
    return ("МАСКОТ ПРОЕКТА (другого героя не выдумывай):\n" + "\n".join(lines)
            + "\nЭто чат одного клипа. Новую серию с маскотом собирает чат проекта: скажи владельцу открыть его "
              "(кнопка «Проект» в панели чата).")


def block_for_source(store: Any, source_id: int, limit: int = PROMPT_CHARS) -> str:
    """Блок для нарезки и «ИИ выберет лучшие». Сбой здесь не должен останавливать работу."""
    try:
        return prompt_block(store, P.project_of_source(store, int(source_id)), int(source_id), limit=limit)
    except Exception as exc:  # noqa: BLE001
        _log.warning("knowledge block for source %s failed: %s", source_id, exc)
        return ""


def block_for_plan(store: Any, clip_plan_id: int, limit: int = PROMPT_CHARS) -> str:
    """Блок для чата клипа и ИИ-монтажа. У холста проект приписан к клипу, а не к источнику."""
    try:
        from app.asset_scope import clip_project

        plan = store.get_clip_plan(int(clip_plan_id), include_segments=False)
        return prompt_block(store, clip_project(store, int(clip_plan_id)), int(plan["source_id"]), limit=limit,
                            clip_plan_id=int(clip_plan_id))
    except Exception as exc:  # noqa: BLE001
        _log.warning("knowledge block for clip plan %s failed: %s", clip_plan_id, exc)
        return ""
