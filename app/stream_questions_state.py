"""Вопросы стрима: состояние страницы, входящих и клипа (из ``app/stream_questions.py``)."""

from __future__ import annotations

from typing import Any

from app.stream_questions_core import LEVELS, LEVEL_OF_SCOPE, _question, _clip_family, list_questions
from app.stream_questions_gate import montage_gate
from app.stream_questions_memes import _moment, list_moments

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
