"""Проекты: общее и сами проекты (из ``app/projects.py``)."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

# --------------------------------------------------------------------------- #
# Общее
# --------------------------------------------------------------------------- #

PROJECT_KINDS = ("stream_channel", "series", "promo", "ai_video", "explainer", "series3d", "other")
PROJECT_KIND_LABELS = {"stream_channel": "стрим-канал", "series": "сериал / аниме", "promo": "промо",
                       "ai_video": "ИИ-видео", "explainer": "канал объяснялок", "series3d": "3D-серия",
                       "other": "другое"}


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?$")


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _text(value: Any, limit: int = 500) -> str:
    return " ".join(str(value or "").split())[:limit]


def _long_text(value: Any, limit: int = 4000) -> str:
    """Текст с абзацами: пробелы внутри строк схлопываем, переносы оставляем."""
    lines = [" ".join(line.split()) for line in str(value or "").replace("\r", "").split("\n")]
    return "\n".join(lines).strip()[:limit]


def _date(value: Any) -> str:
    text = str(value or "").strip().replace("T", " ")
    if not text:
        return ""
    if not DATE_RE.match(text):
        raise ValueError("дата — ГГГГ-ММ-ДД или ГГГГ-ММ-ДД ЧЧ:ММ")
    return text


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _loads(text: Any, default: Any) -> Any:
    try:
        value = json.loads(text) if text else default
    except (TypeError, ValueError):
        return default
    return value if isinstance(value, type(default)) else default


def _choice(value: Any, allowed: tuple, name: str, default: str | None = None) -> str:
    text = str(value or "").strip().lower() or (default or "")
    if text not in allowed:
        raise ValueError(f"{name}: одно из {', '.join(allowed)}")
    return text


# --------------------------------------------------------------------------- #
# Проекты
# --------------------------------------------------------------------------- #


def _links(value: Any) -> list[dict]:
    """Ссылки проекта: [{label, url}] — канал на Twitch, YouTube, TikTok, страница сериала."""
    if value is None:
        return []
    if isinstance(value, str):
        value = [line for line in value.splitlines() if line.strip()]
    if not isinstance(value, list):
        raise ValueError("links — список ссылок")
    out = []
    for item in value[:20]:
        if isinstance(item, str):
            item = {"url": item}
        if not isinstance(item, dict):
            continue
        url = _text(item.get("url"), 500)
        if not url:
            continue
        out.append({"label": _text(item.get("label"), 80), "url": url})
    return out


def _project_from_row(store: Any, row: Any) -> dict:
    data = dict(row)
    data["links"] = _loads(data.pop("links_json", "[]"), [])
    data.pop("brief_json", None)
    data["kind_label"] = PROJECT_KIND_LABELS.get(data.get("kind") or "", data.get("kind"))
    brand = None
    if data.get("brand_id"):
        try:
            found = store.get_brand(int(data["brand_id"]))
            brand = {"id": found["id"], "name": found.get("name") or ""}
        except KeyError:
            brand = None
    data["brand"] = brand
    return data


def _check_brand(store: Any, brand_id: Any) -> int | None:
    if brand_id in (None, "", 0):
        return None
    try:
        return int(store.get_brand(int(brand_id))["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"нет бренда {brand_id}") from exc


def create_project(store: Any, name: str, kind: str = "other", description: str = "", links: Any = None,
                   brand_id: Any = None, source_ids: list | None = None) -> dict:
    name = _text(name, 120)
    if not name:
        raise ValueError("нужно имя проекта")
    cur = store.db.execute(
        "INSERT INTO projects (name, kind, description, links_json, brand_id, brief_json) VALUES (?, ?, ?, ?, ?, ?)",
        (name, _choice(kind, PROJECT_KINDS, "kind", "other"), _long_text(description, 2000),
         _json(_links(links)), _check_brand(store, brand_id), _json(empty_brief())),
    )
    project_id = int(cur.lastrowid)
    for source_id in source_ids or []:
        set_source_project(store, int(source_id), project_id)
    return get_project(store, project_id)


def list_projects(store: Any) -> list[dict]:
    rows = store.db.query_all(
        """
        SELECT p.*,
               (SELECT COUNT(*) FROM sources s WHERE s.project_id = p.id) AS sources_count,
               (SELECT COUNT(*) FROM clip_plans cp JOIN sources s ON s.id = cp.source_id
                 WHERE s.project_id = p.id AND EXISTS (SELECT 1 FROM clips c WHERE c.clip_plan_id = cp.id))
                 AS clips_count,
               (SELECT COUNT(*) FROM project_rules r WHERE r.project_id = p.id AND r.status = 'active')
                 AS active_rules_count,
               (SELECT COUNT(*) FROM project_hypotheses h WHERE h.project_id = p.id) AS hypotheses_count
        FROM projects p
        ORDER BY p.name COLLATE NOCASE, p.id
        """
    )
    return [_project_from_row(store, row) for row in rows]


def get_project(store: Any, project_id: int, include_sources: bool = True) -> dict:
    row = store.db.query_one("SELECT * FROM projects WHERE id = ?", (int(project_id),))
    if not row:
        raise KeyError(f"project not found: {project_id}")
    data = _project_from_row(store, row)
    # Вкладка «Объяснялки»: у проекта с маскотом — какого бы вида он ни был.
    data["has_mascots"] = bool(store.db.query_one("SELECT 1 FROM expl_mascots WHERE project_id = ? LIMIT 1",
                                                  (int(project_id),)))
    if include_sources:
        data["sources"] = project_sources(store, int(project_id))
    return data


def project_sources(store: Any, project_id: int) -> list[dict]:
    rows = store.db.query_all(
        """
        SELECT s.id, s.status, s.source_type, s.original_filename, s.original_url, s.duration_sec, s.created_at,
               (SELECT COUNT(*) FROM clip_plans cp WHERE cp.source_id = s.id) AS clip_plans_count,
               (SELECT COUNT(DISTINCT COALESCE(cp.clip_group_id, -cp.id)) FROM clip_plans cp WHERE cp.source_id = s.id AND COALESCE(cp.hidden, 0) = 0) AS clip_cards_count,
               (SELECT COUNT(*) FROM clips c WHERE c.source_id = s.id) AS clips_count
        FROM sources s WHERE s.project_id = ? ORDER BY s.id
        """,
        (int(project_id),),
    )
    from app.nav import with_titles

    return with_titles(store, [dict(row) for row in rows])


def update_project(store: Any, project_id: int, **fields: Any) -> dict:
    get_project(store, project_id, include_sources=False)
    updates: dict[str, Any] = {}
    if "name" in fields:
        name = _text(fields["name"], 120)
        if not name:
            raise ValueError("нужно имя проекта")
        updates["name"] = name
    if "kind" in fields:
        updates["kind"] = _choice(fields["kind"], PROJECT_KINDS, "kind")
    if "description" in fields:
        updates["description"] = _long_text(fields["description"], 2000)
    if "links" in fields:
        updates["links_json"] = _json(_links(fields["links"]))
    if "brand_id" in fields:
        updates["brand_id"] = _check_brand(store, fields["brand_id"])
    if updates:
        sets = ", ".join(f"{key} = ?" for key in updates)
        store.db.execute(f"UPDATE projects SET {sets}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (*updates.values(), int(project_id)))
    if "source_ids" in fields and fields["source_ids"] is not None:
        wanted = {int(s) for s in fields["source_ids"]}
        current = {int(s["id"]) for s in project_sources(store, project_id)}
        for source_id in current - wanted:
            set_source_project(store, source_id, None)
        for source_id in wanted - current:
            set_source_project(store, source_id, int(project_id))
    return get_project(store, project_id)


def delete_project(store: Any, project_id: int) -> dict:
    """Удалить проект: источники и клипы остаются (без проекта), файлы для монтажа становятся общими, правила,
    гипотезы и ручные публикации проекта уходят вместе с ним."""
    project = get_project(store, project_id)
    with store.db.transaction():
        store.db.execute("UPDATE sources SET project_id = NULL WHERE project_id = ?", (int(project_id),))
        store.db.execute("DELETE FROM projects WHERE id = ?", (int(project_id),))
    return {"deleted": True, "project": project}


def set_source_project(store: Any, source_id: int, project_id: int | None) -> dict:
    """Источник — в один проект (или ни в какой: None)."""
    store.get_source(int(source_id))
    if project_id is not None:
        get_project(store, int(project_id), include_sources=False)
    store.db.execute("UPDATE sources SET project_id = ? WHERE id = ?",
                     (None if project_id is None else int(project_id), int(source_id)))
    return {"source_id": int(source_id), "project_id": project_id}


def project_of_source(store: Any, source_id: int) -> int | None:
    row = store.db.query_one("SELECT project_id FROM sources WHERE id = ?", (int(source_id),))
    if not row:
        raise KeyError(f"source not found: {source_id}")
    return int(row["project_id"]) if row["project_id"] is not None else None


def project_of_plan(store: Any, clip_plan_id: int) -> int | None:
    """Проект клипа: по источнику, а у клипа без проекта источника (холст) — приписка ``plan_projects``."""
    row = store.db.query_one(
        "SELECT s.project_id, (SELECT pp.project_id FROM plan_projects pp WHERE pp.clip_plan_id = cp.id) AS direct "
        "FROM clip_plans cp JOIN sources s ON s.id = cp.source_id WHERE cp.id = ?",
        (int(clip_plan_id),),
    )
    if not row:
        raise KeyError(f"clip plan not found: {clip_plan_id}")
    if row["project_id"] is not None:
        return int(row["project_id"])
    return int(row["direct"]) if row["direct"] is not None else None


def assign_plan_project(store: Any, clip_plan_id: int, project_id: int | None) -> dict:
    """Приписать клип к проекту напрямую (клип-холст объяснялки → канал). None — снять приписку."""
    store.get_clip_plan(int(clip_plan_id), include_segments=False)
    if project_id is None:
        store.db.execute("DELETE FROM plan_projects WHERE clip_plan_id = ?", (int(clip_plan_id),))
    else:
        get_project(store, int(project_id), include_sources=False)
        store.db.execute("INSERT OR REPLACE INTO plan_projects (clip_plan_id, project_id) VALUES (?, ?)",
                         (int(clip_plan_id), int(project_id)))
    return {"clip_plan_id": int(clip_plan_id), "project_id": project_id}


# --------------------------------------------------------------------------- #
# Бриф (лор) проекта
# --------------------------------------------------------------------------- #

BRIEF_SCHEMA = "vvf.project_brief"
BRIEF_VERSION = 1
# Разделы-списки: ключ, по которому элемент обновляется (без учёта регистра), и поля.
BRIEF_SECTIONS: dict[str, tuple[str, tuple[str, ...]]] = {
    "people": ("name", ("name", "who", "traits", "aliases")),
    "arcs": ("title", ("title", "about", "status")),
    "jokes": ("text", ("text", "about", "example")),
    "taboo": ("topic", ("topic", "why")),
}
BRIEF_SECTION_LABELS = {"people": "персонажи и люди", "arcs": "арки и сквозные темы",
                        "jokes": "внутренние шутки и мемы", "taboo": "запретные темы"}


class BriefConflict(Exception):
    """Бриф успели поменять после того, как его прочитали (base_rev устарел)."""


def empty_brief() -> dict:
    return {"schema": BRIEF_SCHEMA, "version": BRIEF_VERSION, "summary": "", "notes": "",
            **{name: [] for name in BRIEF_SECTIONS}}
