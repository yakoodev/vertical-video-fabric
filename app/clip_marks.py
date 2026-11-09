"""Заметка владельца к клипу и все публикации клипа: через сервис и отмеченные руками.

Владелец выкладывает клипы сам, часто с телефона, на несколько площадок и аккаунтов, иногда один и
тот же клип дважды. Чтобы видеть, что уже выложено, у клипа есть:

* заметка (``clip_notes``) — свободный текст. Клип — группа планов (``app/clip_groups.py``), поэтому
  заметка читается по всей группе и пишется в главный план. Если после объединения клипов заметки
  оказались у нескольких планов, их показывают одним текстом через пустую строку, а при сохранении
  этот текст ложится в главный план, остальные убираются: владелец сохранил то, что видел;
* публикации руками — ``project_publications`` (``app/projects_stats.py``) с ``clip_plan_id`` любого
  плана группы: площадка, аккаунт, дата, необязательная ссылка, короткая пометка, ``clip_id`` — какой
  готовый файл выложен;
* публикации сервиса — успешные ``job_targets`` рендеров планов группы (правка — только на «Публикациях»).

Счёт «опубликовано ×N» у карточки клипа — сумма обоих видов (``marks_for_plans``).
"""

from __future__ import annotations

from typing import Any

from app.clip_groups_cards import group_members
from app.clip_groups_snapshots import _chunks
from app.projects_core import _long_text

MAX_NOTE = 5000


# ---- заметка ------------------------------------------------------------------------------------------


def _notes_by_plan(store: Any, plan_ids: list[int]) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for chunk in _chunks(plan_ids):
        marks = ",".join("?" for _ in chunk)
        for row in store.db.query_all(
            f"SELECT clip_plan_id, text, updated_at FROM clip_notes WHERE clip_plan_id IN ({marks})", chunk
        ):
            out[int(row["clip_plan_id"])] = {"text": row["text"] or "", "updated_at": row["updated_at"]}
    return out


def _merged_note(plan_ids: list[int], notes: dict[int, dict]) -> dict:
    """Заметка клипа из заметок его планов: старые планы первыми, пустые пропускаются."""
    rows = [notes[i] for i in sorted(plan_ids) if i in notes]
    texts = [r["text"] for r in rows if r["text"].strip()]
    stamps = [str(r["updated_at"]) for r in rows if r["updated_at"]]
    return {"text": "\n\n".join(texts), "updated_at": max(stamps) if stamps else None}


def get_clip_note(store: Any, clip_plan_id: int) -> dict:
    members = [int(m["id"]) for m in group_members(store, clip_plan_id)]
    return {"clip_id": members[-1], **_merged_note(members, _notes_by_plan(store, members))}


def set_clip_note(store: Any, clip_plan_id: int, text: Any) -> dict:
    """Сохранить заметку клипа целиком (пустой текст — заметки нет)."""
    members = [int(m["id"]) for m in group_members(store, clip_plan_id)]
    head = members[-1]
    with store.db.transaction():
        store.db.execute(
            """INSERT INTO clip_notes (clip_plan_id, text, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(clip_plan_id) DO UPDATE SET text = excluded.text, updated_at = CURRENT_TIMESTAMP""",
            (head, _long_text(text, MAX_NOTE)),
        )
        others = [i for i in members if i != head]
        if others:
            store.db.execute(f"DELETE FROM clip_notes WHERE clip_plan_id IN ({','.join('?' * len(others))})", others)
    return get_clip_note(store, head)


# ---- публикации ---------------------------------------------------------------------------------------


def _day(value: Any) -> str:
    return str(value or "")[:10]


def _publications_by_plan(store: Any, plan_ids: list[int]) -> dict[int, list[dict]]:
    """plan_id → публикации (сервиса и руками) коротко: площадка, аккаунт, дата, ссылка, файл."""
    out: dict[int, list[dict]] = {}
    for chunk in _chunks(plan_ids):
        marks = ",".join("?" for _ in chunk)
        for row in store.db.query_all(
            f"""
            SELECT jt.id, c.clip_plan_id AS plan, jt.platform, COALESCE(a.label, '') AS account,
                   COALESCE(jt.finished_at, jt.updated_at) AS at, jt.remote_url AS url, c.id AS clip_id
            FROM job_targets jt JOIN jobs j ON j.id = jt.job_id JOIN clips c ON c.id = j.clip_id
            LEFT JOIN accounts a ON a.id = jt.account_id
            WHERE c.clip_plan_id IN ({marks}) AND jt.status = 'succeeded'
            """,
            chunk,
        ):
            out.setdefault(int(row["plan"]), []).append({
                "kind": "service", "id": int(row["id"]), "platform": row["platform"] or "",
                "account": row["account"], "date": _day(row["at"]), "url": row["url"] or "",
                "clip_id": row["clip_id"], "note": "",
            })
        for row in store.db.query_all(
            f"""
            SELECT id, clip_plan_id AS plan, platform, account, published_at, created_at, url, clip_id, notes
            FROM project_publications WHERE clip_plan_id IN ({marks})
            """,
            chunk,
        ):
            out.setdefault(int(row["plan"]), []).append({
                "kind": "manual", "id": int(row["id"]), "platform": row["platform"] or "",
                "account": row["account"] or "", "date": _day(row["published_at"] or row["created_at"]),
                "url": row["url"] or "", "clip_id": row["clip_id"], "note": row["notes"] or "",
            })
    return out


def _sorted(pubs: list[dict]) -> list[dict]:
    """Новые сверху; в один день — позже добавленные сверху."""
    return sorted(pubs, key=lambda p: (p["date"], p["kind"] == "manual", p["id"]), reverse=True)


def marks_for_plans(store: Any, plan_ids: list[int]) -> dict:
    """Для сетки клипов одним проходом: публикации и заметки планов (группировку делает карточка)."""
    ids = sorted({int(i) for i in plan_ids})
    return {"pubs": _publications_by_plan(store, ids), "notes": _notes_by_plan(store, ids)}


def card_marks(plan_ids: list[int], marks: dict) -> dict:
    """Поля карточки клипа: опубликовано всего, список публикаций, заметка."""
    pubs = _sorted([p for i in plan_ids for p in marks["pubs"].get(int(i), [])])
    note = _merged_note([int(i) for i in plan_ids], marks["notes"])
    return {"published": len(pubs), "publications": pubs, "note": note["text"], "note_updated_at": note["updated_at"]}


def clip_publications(store: Any, clip_plan_id: int) -> list[dict]:
    """Публикации клипа для его экрана: всё из карточки + название, числа и ошибка сбора."""
    from app.projects_stats import latest_manual_stats

    members = [int(m["id"]) for m in group_members(store, clip_plan_id)]
    pubs = card_marks(members, marks_for_plans(store, members))["publications"]
    manual_ids = [p["id"] for p in pubs if p["kind"] == "manual"]
    manual = latest_manual_stats(store, manual_ids) if manual_ids else {}
    service = store.latest_publication_stats() if any(p["kind"] == "service" for p in pubs) else {}
    titles = {}
    if manual_ids:
        rows = store.db.query_all(
            f"SELECT id, title, published_at FROM project_publications WHERE id IN ({','.join('?' * len(manual_ids))})",
            manual_ids,
        )
        titles = {int(r["id"]): r for r in rows}
    out = []
    for pub in pubs:
        stats = (manual if pub["kind"] == "manual" else service).get(int(pub["id"])) or {}
        extra = titles.get(int(pub["id"])) if pub["kind"] == "manual" else None
        out.append({
            **pub,
            "title": (extra["title"] if extra else "") or "",
            "published_at": (extra["published_at"] if extra else pub["date"]) or "",
            "views": stats.get("views"), "likes": stats.get("likes"), "captured_at": stats.get("captured_at"),
            "error": stats.get("error") or "",
        })
    return out
