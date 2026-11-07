"""Группы клипов: версии и вид клипа (из ``app/clip_groups.py``)."""

from __future__ import annotations

import re
from typing import Any

from app.clip_groups_facts import version_mark, _size_format
from app.clip_groups_snapshots import _history_times, history_candidate, _chunks
from app.clip_groups_cards import (
    _member_plans, _pieces, _renders, _card, _cards_from_plans, _link_moments, group_members
)

# ---- версии ----------------------------------------------------------------------------------------


def _version_labels(plans: list[dict]) -> dict[int, int]:
    """Номер версии плана: из названия, если он там есть и растёт; иначе порядковый."""
    numbers: dict[int, int] = {}
    last = 0
    for plan in plans:
        mark = version_mark(plan["title"])
        last = mark if mark and mark >= last else last + 1
        numbers[int(plan["id"])] = last
    return numbers


def clip_versions(store: Any, plans: list[dict]) -> list[dict]:
    """Версии клипа новыми первыми: рендеры всех планов плюс снимки «до отката» без своего файла."""
    from app.clip_history import HISTORY_DEPTH

    plan_ids = [int(p["id"]) for p in plans]
    numbers = _version_labels(plans)
    renders = _renders(store, plan_ids)
    history = _history_times(store, plan_ids)
    saved = set()
    extra: list[dict] = []
    marks_by_plan: dict[int, list[dict]] = {}
    for chunk in _chunks(plan_ids):
        marks = ",".join("?" for _ in chunk)
        for row in store.db.query_all(
            f"SELECT id, clip_plan_id, clip_id, kind, label, created_at FROM clip_versions WHERE clip_plan_id IN ({marks}) ORDER BY id",
            chunk,
        ):
            if row["clip_id"] is not None:
                saved.add(int(row["clip_id"]))
            elif row["kind"] == "rollback_mark":
                marks_by_plan.setdefault(int(row["clip_plan_id"]), []).append(dict(row))
            elif row["kind"] != "render":
                extra.append(dict(row))
    versions: list[dict] = []
    for plan in plans:
        pid = int(plan["id"])
        rows = renders.get(pid, [])
        done = [c for c in rows if c.get("status") == "succeeded"]
        prev = ""
        for clip in rows:
            created = str(clip.get("created_at") or "")
            changes: list[str] = []
            for entry in history.get(pid, []) + marks_by_plan.get(pid, []):
                at = str(entry["created_at"])
                label = str(entry.get("label") or "").replace("агент: ", "")
                if prev < at <= created and label and label not in changes:
                    changes.append(label)
            prev = created
            ok = clip.get("status") == "succeeded"
            index = done.index(clip) + 1 if ok else 0
            label = f"v{numbers[pid]}" + (f".{index}" if ok and len(done) > 1 else "")
            how = "saved" if int(clip["id"]) in saved else history_candidate(history.get(pid, []), created, HISTORY_DEPTH)[0]
            qc = clip.get("qc") if isinstance(clip.get("qc"), dict) else None
            versions.append({
                "key": f"r{clip['id']}",
                "render_id": int(clip["id"]),
                "snapshot_id": None,
                "plan_id": pid,
                "label": label,
                "at": clip.get("finished_at") or created,
                "status": clip.get("status"),
                "error": clip.get("error") or "",
                "format": _size_format(clip.get("width"), clip.get("height")) or "",
                "width": int(clip.get("width") or 0),
                "height": int(clip.get("height") or 0),
                "duration_sec": float(clip.get("duration_sec") or 0),
                "qc_ok": qc.get("ok") if qc else None,
                "changes": changes[-5:],
                "can_rollback": ok and how != "lost",
                "why_not": "" if not ok or how != "lost" else "Снимок правок этой старой версии не сохранился: смотреть и скачать можно, откатить нельзя",
                "url": f"/media/clips/{clip['id']}" if ok else None,
                "thumb": f"/media/clips/{clip['id']}/thumb?v={clip.get('updated_at') or ''}" if ok else None,
            })
    for row in extra:
        versions.append({
            "key": f"s{row['id']}",
            "render_id": None,
            "snapshot_id": int(row["id"]),
            "plan_id": int(row["clip_plan_id"]),
            "label": row["label"] or "Правки до отката",
            "at": row["created_at"],
            "status": "snapshot",
            "error": "",
            "format": "",
            "width": 0,
            "height": 0,
            "duration_sec": 0.0,
            "qc_ok": None,
            "changes": [],
            "can_rollback": True,
            "why_not": "",
            "url": None,
            "thumb": None,
        })
    versions.sort(key=lambda v: (str(v["at"]), v["render_id"] or 0), reverse=True)
    return versions


def _hashtags(meta: dict | None, description: str) -> tuple[str, list[str]]:
    """Описание и хэштеги отдельно: в файле описания они дописаны в конец (full_description)."""
    tags = [str(t) for t in (meta or {}).get("hashtags") or []]
    text = str((meta or {}).get("description") or description or "").strip()
    if not tags:
        found = re.findall(r"(?:^|\s)(#[\w-]+)", text)
        if found:
            tags = found
            text = re.sub(r"(?:^|\s)#[\w-]+", "", text).strip()
    return text, tags


def _project_of(store: Any, plan: dict) -> dict | None:
    row = store.db.query_one(
        """
        SELECT p.id, p.name FROM projects p WHERE p.id = COALESCE(
            (SELECT s.project_id FROM sources s WHERE s.id = ?),
            (SELECT pp.project_id FROM plan_projects pp WHERE pp.clip_plan_id = ? LIMIT 1)
        )
        """,
        (int(plan["source_id"]), int(plan["id"])),
    )
    return {"id": int(row["id"]), "name": row["name"]} if row else None


def clip_view(store: Any, clip_plan_id: int) -> dict:
    """Всё для экрана клипа /clips/:id одним ответом."""
    from app.nav import titles_for
    from app.quality_lint import render_is_stale
    from app.store import source_file_present

    plans = group_members(store, clip_plan_id)
    head = plans[-1]
    head_id = int(head["id"])
    plan_ids = [int(p["id"]) for p in plans]
    renders = _renders(store, plan_ids)
    pieces = _pieces(store, plan_ids)
    card = _card(plans, renders, pieces)
    source_row = store.db.query_one(
        """
        SELECT id, source_type, original_filename, original_url, local_path, duration_sec, width, height, created_at,
               project_id, status FROM sources WHERE id = ?
        """,
        (int(head["source_id"]),),
    )
    source = dict(source_row)
    source_name = titles_for(store, [source]).get(int(source["id"])) or f"Видео #{source['id']}"
    canvas = str(source["source_type"]) == "canvas"
    project = _project_of(store, head)
    # Выпуски — только холсты и компиляции: у клипа из записи список выпусков не читаем.
    release = None
    described = store.db.query_one("SELECT description FROM clip_plans WHERE id = ?", (head_id,))
    if canvas or str((described or {"description": ""})["description"] or "").startswith("Компиляция: "):
        from app.releases import of_plan

        release = of_plan(store, head_id)
    if project is None and release and release.get("project_id"):
        # Сборка старых дней не приписала клип к проекту: проект всё равно известен по выпуску.
        row = store.db.query_one("SELECT id, name FROM projects WHERE id = ?", (int(release["project_id"]),))
        project = {"id": int(row["id"]), "name": row["name"]} if row else None

    # Соседи: те же клипы, что в сетке видео, в том же порядке. У холстов «видео» — проект.
    if canvas and project:
        rows = _member_plans(
            store, "cp.id IN (SELECT pp.clip_plan_id FROM plan_projects pp WHERE pp.project_id = ?)", (project["id"],)
        )
        siblings = _cards_from_plans(store, rows)
    elif canvas:
        siblings = [card]
    else:
        siblings = _cards_from_plans(store, _member_plans(store, "cp.source_id = ?", (int(source["id"]),)), {int(source["id"])})
    siblings = [c for c in siblings if not c["hidden"] or c["id"] == head_id]
    sibling_pieces = _pieces(store, [c["id"] for c in siblings])
    _link_moments(siblings, sibling_pieces)
    me = next((c for c in siblings if c["id"] == head_id), None)
    index = siblings.index(me) if me in siblings else -1
    neighbors = [
        {"id": c["id"], "name": c["name"], "format": c["format"], "status": c["status"], "versions": c["versions"],
         "start_sec": c["start_sec"]}
        for c in siblings
    ]

    head_plan = store.get_clip_plan(head_id)
    segments = [
        {"id": s["id"], "title": s.get("title") or "", "start_sec": float(s["start_sec"]), "end_sec": float(s["end_sec"])}
        for s in head_plan.get("segments") or []
    ]
    latest = card["latest"]
    stale = False
    if latest and int(latest.get("clip_plan_id") or 0) == head_id:
        stale = render_is_stale(store, head_id, latest)
    elif latest:
        stale = True  # новый план группы ещё не рендерили
    meta = (latest or {}).get("publish_meta") if latest else None
    text, tags = _hashtags(meta, str((latest or {}).get("description") or head_plan.get("description") or ""))

    cover_doc = None
    render_ids = [int(c["id"]) for rows in renders.values() for c in rows]
    with_cover = sorted((c for rows in renders.values() for c in rows if c.get("cover_url")), key=lambda c: int(c["id"]))
    for chunk in _chunks(render_ids):
        marks = ",".join("?" for _ in chunk)
        row = store.db.query_one(f"SELECT id, title, rev FROM cover_docs WHERE clip_id IN ({marks}) ORDER BY id DESC LIMIT 1", chunk)
        if row:
            cover_doc = {"id": int(row["id"]), "title": row["title"]}

    older_chat: list[dict] = []
    for plan in plans[:-1]:
        # Старая переписка клипа и новая (app/clip_chat.py); id разные таблицы делят, поэтому у старых реплик он отрицательный.
        for row in store.db.query_all(
            "SELECT id, role, text, created_at FROM clip_agent_messages WHERE clip_plan_id = ? ORDER BY id DESC LIMIT 30",
            (int(plan["id"]),),
        ):
            older_chat.append({**dict(row), "id": -int(row["id"]), "plan_id": int(plan["id"])})
        for row in store.db.query_all(
            "SELECT m.id, m.role, m.text, m.created_at FROM project_chat_messages m JOIN chat_threads t ON t.id = m.thread_id "
            "WHERE t.clip_plan_id = ? ORDER BY m.id DESC LIMIT 30",
            (int(plan["id"]),),
        ):
            older_chat.append({**dict(row), "plan_id": int(plan["id"])})
    older_chat.sort(key=lambda m: (str(m["created_at"] or ""), abs(int(m["id"]))))

    numbers = _version_labels(plans)
    has_file = source_file_present(source.get("local_path"))
    # Что изменилось после последнего файла главного плана: подпись строки «Текущий монтаж».
    pending: list[str] = []
    if stale:
        last_done = [c for c in renders.get(head_id, []) if c.get("status") == "succeeded"]
        since = str(last_done[-1].get("created_at") or "") if last_done else ""
        rows = store.db.query_all(
            """
            SELECT label, created_at FROM clip_plan_history WHERE clip_plan_id = ?
            UNION ALL
            SELECT label, created_at FROM clip_versions WHERE clip_plan_id = ? AND kind = 'rollback_mark'
            ORDER BY created_at
            """,
            (head_id, head_id),
        )
        for row in rows:
            label = str(row["label"] or "").replace("агент: ", "")
            if str(row["created_at"]) > since and label and label not in pending:
                pending.append(label)
    return {
        "id": head_id,
        "requested": int(clip_plan_id),
        "group": head.get("clip_group_id"),
        "name": card["name"],
        "title": head["title"],
        "format": card["format"],
        "status": card["status"],
        "start_sec": card["start_sec"],
        "duration_sec": card["duration_sec"],
        "published": card["published"],
        "favorite": card["favorite"],
        "stale": stale,
        "pending": pending[-4:],
        "canvas": canvas,
        "source": {
            "id": int(source["id"]),
            "name": source_name,
            "source_type": source["source_type"],
            "has_file": has_file,
            "original_url": source.get("original_url") or "",
            "status": source.get("status"),
            "project_id": source.get("project_id"),
            "duration_sec": float(source.get("duration_sec") or 0),
        },
        "project": project,
        "release": release,
        "plans": [
            {
                "id": int(p["id"]),
                "title": p["title"],
                "label": f"v{numbers[int(p['id'])]}",
                "renders": sum(1 for c in renders.get(int(p["id"]), []) if c.get("status") == "succeeded"),
                "created_at": p["created_at"],
                "is_head": int(p["id"]) == head_id,
            }
            for p in plans
        ],
        "versions": clip_versions(store, plans),
        "version_count": card["versions"],
        "drafts": card["drafts"],
        "segments": segments,
        "latest": latest,
        "meta": {
            "title": str((latest or {}).get("title") or head_plan.get("title") or ""),
            "description": text,
            "hashtags": tags,
            "source": (meta or {}).get("source") or "",
        },
        "cover_url": with_cover[-1]["cover_url"] if with_cover else None,
        "cover_doc": cover_doc,
        "moments": me["moments"] if me else [],
        "neighbors": neighbors,
        "prev_id": siblings[index - 1]["id"] if index > 0 else None,
        "next_id": siblings[index + 1]["id"] if 0 <= index < len(siblings) - 1 else None,
        "older_chat": older_chat[-40:],
    }
