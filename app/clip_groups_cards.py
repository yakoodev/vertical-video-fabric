"""Группы клипов: сетка — один элемент на клип (из ``app/clip_groups.py``)."""

from __future__ import annotations

from typing import Any

from app.formats import DEFAULT_FORMAT, normalize_format
from app.clip_groups_facts import ALMOST_SAME_MATERIAL, name_key, display_name, _size_format, shared_fraction, _FMT_SQL
from app.clip_groups_snapshots import _chunks

# Перекрёстная ссылка «этот момент в 9:16» по материалу: длины отличаются не больше чем вдвое.
MOMENT_LENGTH_RATIO = 2.0


def _fmt_of_plan(settings_fmt: Any, last: dict | None) -> str:
    if settings_fmt:
        return normalize_format(settings_fmt)
    if last:
        return _size_format(last.get("width"), last.get("height")) or DEFAULT_FORMAT
    return DEFAULT_FORMAT


_PLAN_SELECT = f"""
    SELECT cp.id, cp.source_id, cp.title, cp.status, cp.hidden, cp.favorite, cp.owner_picked, cp.score, cp.created_at,
           cp.clip_group_id, cp.group_locked, {_FMT_SQL} AS fmt,
           (SELECT s.source_type FROM sources s WHERE s.id = cp.source_id) AS source_type
    FROM clip_plans cp
"""


def _member_plans(store: Any, where: str, args: tuple) -> list[dict]:
    """Планы и их соседи по группе: группа могла получить план из другого видео (объединение руками)."""
    rows = [dict(r) for r in store.db.query_all(f"{_PLAN_SELECT} WHERE {where}", args)]
    known = {int(r["id"]) for r in rows}
    groups = sorted({int(r["clip_group_id"]) for r in rows if r["clip_group_id"] is not None})
    for chunk in _chunks(groups):
        marks = ",".join("?" for _ in chunk)
        for row in store.db.query_all(f"{_PLAN_SELECT} WHERE cp.clip_group_id IN ({marks})", chunk):
            if int(row["id"]) not in known:
                known.add(int(row["id"]))
                rows.append(dict(row))
    return rows


def _pieces(store: Any, plan_ids: list[int]) -> dict[int, list[tuple[float, float]]]:
    out: dict[int, list[tuple[float, float]]] = {}
    for chunk in _chunks(plan_ids):
        marks = ",".join("?" for _ in chunk)
        for row in store.db.query_all(
            f"""
            SELECT x.clip_plan_id AS plan, a.start_sec AS s, a.end_sec AS e FROM clip_plan_segments x
            JOIN ai_segments a ON a.id = x.segment_id WHERE x.clip_plan_id IN ({marks}) ORDER BY x.clip_plan_id, x.sort_order
            """,
            chunk,
        ):
            out.setdefault(int(row["plan"]), []).append((float(row["s"]), float(row["e"])))
    return out


def _renders(store: Any, plan_ids: list[int]) -> dict[int, list[dict]]:
    """Рендеры планов, старые первыми, те же поля, что у GET /api/clips."""
    out: dict[int, list[dict]] = {}
    for chunk in _chunks(plan_ids):
        for clip in store.list_clips_for_plans(chunk):
            out.setdefault(int(clip["clip_plan_id"]), []).append(clip)
    for rows in out.values():
        rows.sort(key=lambda c: int(c["id"]))
    return out


_ACTIVE = ("queued", "rendering")
_FAILED = ("failed", "error")


def _card(plans: list[dict], renders: dict[int, list[dict]], pieces: dict[int, list[tuple[float, float]]]) -> dict:
    plans = sorted(plans, key=lambda p: int(p["id"]))
    head = plans[-1]
    members = [r for p in plans for r in renders.get(int(p["id"]), [])]
    members.sort(key=lambda c: int(c["id"]))
    done = [c for c in members if c.get("status") == "succeeded"]
    latest = done[-1] if done else None
    active = any(c.get("status") in _ACTIVE for c in members)
    status = "rendering" if active else ("ready" if latest else ("failed" if members and members[-1].get("status") in _FAILED else "no_render"))
    # Формат клипа — настройка главного плана; у плана без настройки — его последний файл.
    head_renders = [c for c in renders.get(int(head["id"]), []) if c.get("status") == "succeeded"]
    fmt = _fmt_of_plan(head.get("fmt"), head_renders[-1] if head_renders else latest)
    named = next((p for p in reversed(plans) if renders.get(int(p["id"]))), head)
    head_pieces = pieces.get(int(head["id"])) or []
    all_pieces = [x for p in plans for x in pieces.get(int(p["id"]), [])]
    drafts = sum(1 for p in plans[:-1] if not renders.get(int(p["id"])))
    return {
        "id": int(head["id"]),
        "plan_ids": [int(p["id"]) for p in plans],
        "group": head.get("clip_group_id"),
        "name": display_name(named["title"]),
        "title": head["title"],
        "source_id": int(head["source_id"]),
        "uploaded": head.get("source_type") == "clip_upload",
        "format": fmt,
        "versions": len(done),
        "plans": len(plans),
        "drafts": drafts,
        "status": status,
        "favorite": any(p.get("favorite") for p in plans),
        "picked": any(p.get("owner_picked") for p in plans),
        "hidden": all(p.get("hidden") for p in plans),
        "score": max(float(p.get("score") or 0) for p in plans),
        "start_sec": min((a for a, _ in head_pieces), default=0.0),
        "end_sec": max((b for _, b in head_pieces), default=0.0),
        "span_start": min((a for a, _ in all_pieces), default=0.0),
        "span_end": max((b for _, b in all_pieces), default=0.0),
        "duration_sec": float(latest["duration_sec"]) if latest else sum(max(0.0, b - a) for a, b in head_pieces),
        "published": sum(int(c.get("published_targets_count") or 0) for c in members),
        "error": members[-1].get("error", "") if status == "failed" else "",
        "created_at": str(head.get("created_at") or ""),
        "latest": latest,
    }


def _cards_from_plans(store: Any, rows: list[dict], scope_ids: set[int] | None = None) -> list[dict]:
    plan_ids = [int(r["id"]) for r in rows]
    renders = _renders(store, plan_ids)
    pieces = _pieces(store, plan_ids)
    grouped: dict[int, list[dict]] = {}
    for row in rows:
        key = int(row["clip_group_id"]) if row["clip_group_id"] is not None else -int(row["id"])
        grouped.setdefault(key, []).append(row)
    cards = [_card(group, renders, pieces) for group in grouped.values()]
    if scope_ids is not None:
        # Клип принадлежит видео своего главного плана.
        cards = [c for c in cards if c["source_id"] in scope_ids]
    cards.sort(key=lambda c: (c["start_sec"], c["id"]))
    return cards


def _add_moment(card: dict, other: dict, *, exact: bool) -> None:
    """Одна ссылка на формат: то же название важнее общего материала."""
    for item in card["moments"]:
        if item["format"] == other["format"]:
            if exact and not item.get("exact"):
                item.update({"id": other["id"], "exact": True})
            return
    card["moments"].append({"id": other["id"], "format": other["format"], "exact": exact})


def _link_moments(cards: list[dict], pieces: dict[int, list[tuple[float, float]]]) -> None:
    """Перекрёстная ссылка «этот момент в 9:16»: другой формат, то же название или тот же материал."""
    for card in cards:
        card["moments"] = []
    by_source: dict[int, list[dict]] = {}
    for card in cards:
        by_source.setdefault(card["source_id"], []).append(card)
    for group in by_source.values():
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                if a["format"] == b["format"]:
                    continue
                same = name_key(a["name"]) == name_key(b["name"])
                total_a = sum(y - x for x, y in pieces.get(a["id"], []))
                total_b = sum(y - x for x, y in pieces.get(b["id"], []))
                # Часть стрима на 5 минут и шортс из неё — не «этот момент в 9:16».
                comparable = min(total_a, total_b) > 0 and max(total_a, total_b) / min(total_a, total_b) <= MOMENT_LENGTH_RATIO
                if not same and comparable:
                    same = shared_fraction(pieces.get(a["id"], []), pieces.get(b["id"], [])) >= ALMOST_SAME_MATERIAL
                if same:
                    _add_moment(a, b, exact=name_key(a["name"]) == name_key(b["name"]))
                    _add_moment(b, a, exact=name_key(a["name"]) == name_key(b["name"]))
    for card in cards:
        card["moments"] = [{"id": m["id"], "format": m["format"]} for m in card["moments"]]


def _orphan_renders(store: Any, sql_where: str, args: tuple) -> list[dict]:
    """Рендеры без плана (план удалили, файл остался): в сетке клипов их нет, но терять нельзя."""
    ids = [int(r["id"]) for r in store.db.query_all(
        f"SELECT c.id FROM clips c JOIN sources s ON s.id = c.source_id WHERE c.clip_plan_id IS NULL AND {sql_where} ORDER BY c.id DESC",
        args,
    )]
    return [store.get_clip(i) for i in ids]


def source_cards(store: Any, source_id: int) -> dict:
    rows = _member_plans(store, "cp.source_id = ?", (int(source_id),))
    cards = _cards_from_plans(store, rows, {int(source_id)})
    _link_moments(cards, _pieces(store, [c["id"] for c in cards]))
    return {"clips": cards, "orphans": _orphan_renders(store, "c.source_id = ?", (int(source_id),))}


def project_source_ids(store: Any, project_id: int) -> list[int]:
    return [int(r["id"]) for r in store.db.query_all("SELECT id FROM sources WHERE project_id = ?", (int(project_id),))]


def project_cards(store: Any, project_id: int) -> dict:
    """Клипы проекта: его видео плюс холсты, приписанные к нему (скрытый общий источник)."""
    sources = project_source_ids(store, project_id)
    rows: list[dict] = []
    for chunk in _chunks(sources):
        marks = ",".join("?" for _ in chunk)
        rows += _member_plans(store, f"cp.source_id IN ({marks})", tuple(chunk))
    rows += _member_plans(
        store,
        "cp.id IN (SELECT pp.clip_plan_id FROM plan_projects pp WHERE pp.project_id = ?)",
        (int(project_id),),
    )
    unique = {int(r["id"]): r for r in rows}
    cards = _cards_from_plans(store, list(unique.values()))
    _link_moments(cards, _pieces(store, [c["id"] for c in cards]))
    from app.nav import titles_for

    wanted = sorted({c["source_id"] for c in cards})
    infos: list[dict] = []
    for chunk in _chunks(wanted):
        marks = ",".join("?" for _ in chunk)
        infos += [dict(r) for r in store.db.query_all(
            f"SELECT id, source_type, original_filename, created_at, project_id FROM sources WHERE id IN ({marks})", chunk,
        )]
    names = titles_for(store, infos)
    for card in cards:
        card["video_name"] = names.get(card["source_id"], "")
    cards.sort(key=lambda c: -int(c["id"]))
    orphans = _orphan_renders(store, "s.project_id = ?", (int(project_id),))
    return {"clips": cards, "orphans": orphans}


def group_members(store: Any, clip_plan_id: int) -> list[dict]:
    """Все планы клипа, старые первыми. Незнакомый план — KeyError."""
    row = store.db.query_one("SELECT id, clip_group_id FROM clip_plans WHERE id = ?", (int(clip_plan_id),))
    if not row:
        raise KeyError(f"клипа нет: {clip_plan_id}")
    if row["clip_group_id"] is None:
        where, args = "cp.id = ?", (int(clip_plan_id),)
    else:
        where, args = "cp.clip_group_id = ?", (int(row["clip_group_id"]),)
    return [dict(r) for r in store.db.query_all(f"{_PLAN_SELECT} WHERE {where} ORDER BY cp.id", args)]


def head_plan_id(store: Any, clip_plan_id: int) -> int:
    return int(group_members(store, clip_plan_id)[-1]["id"])
