"""Проекты: несколько источников одного канала или сериала под одной крышей.

Зачем. Источник (``sources``) — одно видео: стрим, серия, запись экрана. Владелец режет
сериями — стримы одной витуберши, серии одного аниме, промо одного заказчика, — и
агенту нужна память выше уровня видео: кто есть кто, внутренние шутки, что уже
нарезано, что владелец просил и что запрещал, что заходит по цифрам. Это и есть
проект. На нём позже встанет «автопилот канала».

Про слово «проект». Исторически в UI и в MCP «проект» — это ИСТОЧНИК: страница
``/v/:sourceId``, инструменты ``list_projects`` / ``project_id`` в MCP —
это id источника. Новая сущность живёт в таблице ``projects``; в интерфейсе она
называется «проект» (``/p/:id``), в MCP — ``*_channel_project`` и
инструменты брифа/правил/гипотез, у которых ``project_id`` — id ЭТОЙ таблицы
(вместо него можно дать ``source_id`` — возьмётся проект источника).

Что здесь:

* ``projects`` — имя, тип, описание, ссылки, бренд (``brands``), бриф (JSON со схемой
  и версией); ``sources.project_id`` — источник в одном проекте (или ни в одном);
* бриф (лор) — кратко, люди, арки, шутки, табу + вычисляемое «что уже нарезано»;
* ``project_rules`` — правила владельца с цитатой, датой и историей отмен; поиск
  дублей и противоречий простой нормализацией строк;
* ``project_publications`` — опубликованное руками (не через сервис) по ссылке, числа
  — тем же yt-dlp, что ``app/platform_stats.py``, или вручную;
* ``clip_tags`` и ``project_hypotheses`` — признаки клипов и честное сравнение групп
  медианами («мало данных», пока N мало).

Таблицы создаёт ``ensure_projects_schema`` из ``Database.init`` (как чат Twitch).
Существующие данные не трогаются: у старых источников ``project_id`` пустой.
"""

from __future__ import annotations

import json
import re
import statistics
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any, Callable

# --------------------------------------------------------------------------- #
# Схема
# --------------------------------------------------------------------------- #

PROJECTS_SCHEMA_SQL = [
    """
    CREATE TABLE IF NOT EXISTS projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'other',
        description TEXT NOT NULL DEFAULT '',
        links_json TEXT NOT NULL DEFAULT '[]',
        brand_id INTEGER REFERENCES brands(id) ON DELETE SET NULL,
        brief_json TEXT NOT NULL DEFAULT '{}',
        brief_rev INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    # Правило владельца. project_id NULL — глобальное (scope='global').
    # status: active / revoked / superseded; superseded_by — правило-замена.
    # hypothesis_id — правило родилось из подтверждённой гипотезы; needs_review —
    # статистика перестала его подтверждать («на пересмотр»).
    """
    CREATE TABLE IF NOT EXISTS project_rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
        scope TEXT NOT NULL DEFAULT 'project',
        kind TEXT NOT NULL DEFAULT 'do',
        text TEXT NOT NULL,
        quote TEXT NOT NULL DEFAULT '',
        origin TEXT NOT NULL DEFAULT '',
        origin_ref TEXT NOT NULL DEFAULT '',
        said_at TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'active',
        revoke_reason TEXT NOT NULL DEFAULT '',
        revoke_quote TEXT NOT NULL DEFAULT '',
        revoked_at TEXT,
        superseded_by INTEGER REFERENCES project_rules(id) ON DELETE SET NULL,
        hypothesis_id INTEGER,
        needs_review INTEGER NOT NULL DEFAULT 0,
        review_note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_project_rules ON project_rules(project_id, status, id)",
    # Опубликованное НЕ через сервис: ссылка на TikTok/YouTube/Shorts, привязка к клипу
    # (clip_plan_id) или только к проекту. Клип важнее: проект берётся по его источнику.
    """
    CREATE TABLE IF NOT EXISTS project_publications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
        clip_plan_id INTEGER REFERENCES clip_plans(id) ON DELETE SET NULL,
        url TEXT NOT NULL,
        platform TEXT NOT NULL DEFAULT '',
        title TEXT NOT NULL DEFAULT '',
        published_at TEXT NOT NULL DEFAULT '',
        tags_json TEXT NOT NULL DEFAULT '[]',
        notes TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_project_publications ON project_publications(project_id, id)",
    # Снимки чисел ручных публикаций — как publication_stats у публикаций сервиса.
    # origin: auto (yt-dlp) / manual (ввёл человек).
    """
    CREATE TABLE IF NOT EXISTS project_publication_stats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        publication_id INTEGER NOT NULL REFERENCES project_publications(id) ON DELETE CASCADE,
        captured_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        views INTEGER,
        likes INTEGER,
        comments INTEGER,
        shares INTEGER,
        avg_retention REAL,
        origin TEXT NOT NULL DEFAULT 'auto',
        error TEXT NOT NULL DEFAULT ''
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_project_publication_stats ON project_publication_stats(publication_id, id)",
    # Теги клипа — признаки для гипотез («hook_question», «мура_vs_джойстик»).
    """
    CREATE TABLE IF NOT EXISTS clip_tags (
        clip_plan_id INTEGER NOT NULL REFERENCES clip_plans(id) ON DELETE CASCADE,
        tag TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (clip_plan_id, tag)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS project_hypotheses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        statement TEXT NOT NULL,
        group_json TEXT NOT NULL DEFAULT '{}',
        baseline_json TEXT NOT NULL DEFAULT '',
        metric TEXT NOT NULL DEFAULT 'views',
        direction TEXT NOT NULL DEFAULT 'higher',
        min_n INTEGER NOT NULL DEFAULT 5,
        status TEXT NOT NULL DEFAULT 'testing',
        status_locked INTEGER NOT NULL DEFAULT 0,
        conclusion TEXT NOT NULL DEFAULT '',
        result_json TEXT NOT NULL DEFAULT '{}',
        rule_text TEXT NOT NULL DEFAULT '',
        origin TEXT NOT NULL DEFAULT '',
        evaluated_at TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_project_hypotheses ON project_hypotheses(project_id, id)",
    # Клип без источника проекта (холст-клипы объяснялок: источник — общий скрытый «Холсты»)
    # приписан к проекту напрямую. ``project_of_plan`` и статистика проекта смотрят и сюда.
    """
    CREATE TABLE IF NOT EXISTS plan_projects (
        clip_plan_id INTEGER PRIMARY KEY REFERENCES clip_plans(id) ON DELETE CASCADE,
        project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_plan_projects ON plan_projects(project_id)",
    # «Что агент знает» (app/project_knowledge.py): факты из чатов и заметок владельца.
    # status: active / deleted (убран владельцем: запись остаётся, чтобы агент не записал его снова).
    # source_id, clip_plan_id — откуда факт, а не его границы: факт принадлежит всему проекту.
    """
    CREATE TABLE IF NOT EXISTS project_facts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        text TEXT NOT NULL,
        text_key TEXT NOT NULL,
        origin TEXT NOT NULL DEFAULT 'owner',
        origin_ref TEXT NOT NULL DEFAULT '',
        quote TEXT NOT NULL DEFAULT '',
        source_id INTEGER REFERENCES sources(id) ON DELETE SET NULL,
        clip_plan_id INTEGER REFERENCES clip_plans(id) ON DELETE SET NULL,
        status TEXT NOT NULL DEFAULT 'active',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (project_id, text_key)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_project_facts ON project_facts(project_id, status, id)",
]


def ensure_projects_schema(conn) -> None:
    """Таблицы проектов + ``sources.project_id`` (вызывается из ``Database.init``)."""
    for sql in PROJECTS_SCHEMA_SQL:
        conn.execute(sql)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(sources)")}
    if "project_id" not in columns:
        # Старые источники остаются без проекта (NULL) — ничего не теряется.
        conn.execute("ALTER TABLE sources ADD COLUMN project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sources_project ON sources(project_id)")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(montage_assets)")}
    if columns and "project_id" not in columns:
        # «Файлы для монтажа»: NULL — общий файл, число — файл проекта (app/asset_scope.py). Старые файлы — общие.
        conn.execute("ALTER TABLE montage_assets ADD COLUMN project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL")
    if columns:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_montage_assets_project ON montage_assets(project_id)")


# --------------------------------------------------------------------------- #
# Общее
# --------------------------------------------------------------------------- #

PROJECT_KINDS = ("stream_channel", "series", "promo", "ai_video", "explainer", "other")
PROJECT_KIND_LABELS = {"stream_channel": "стрим-канал", "series": "сериал / аниме", "promo": "промо",
                       "ai_video": "ИИ-видео", "explainer": "канал объяснялок", "other": "другое"}
RULE_KINDS = ("do", "dont", "preference")
RULE_SCOPES = ("project", "global")
RULE_STATUSES = ("active", "revoked", "superseded")
HYPOTHESIS_STATUSES = ("testing", "confirmed", "rejected", "inconclusive")
METRICS = ("views", "likes", "comments", "shares", "like_rate", "views_per_day", "avg_retention")
METRIC_LABELS = {"views": "просмотров", "likes": "лайков", "comments": "комментариев", "shares": "репостов",
                 "like_rate": "доли лайков", "views_per_day": "просмотров в день", "avg_retention": "удержания"}
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
MAX_SECTION_ITEMS = 200


class BriefConflict(Exception):
    """Бриф успели поменять после того, как его прочитали (base_rev устарел)."""


def empty_brief() -> dict:
    return {"schema": BRIEF_SCHEMA, "version": BRIEF_VERSION, "summary": "", "notes": "",
            **{name: [] for name in BRIEF_SECTIONS}}


def _brief_item(section: str, raw: Any) -> dict | None:
    key, fields = BRIEF_SECTIONS[section]
    if isinstance(raw, str):
        raw = {key: raw}
    if not isinstance(raw, dict):
        return None
    item: dict[str, Any] = {}
    for name in fields:
        if name == "aliases":
            aliases = raw.get("aliases") or []
            if isinstance(aliases, str):
                aliases = [a for a in re.split(r"[,;]", aliases)]
            item["aliases"] = [a for a in (_text(x, 60) for x in aliases if x) if a][:10]
        elif name == "status":
            status = _text(raw.get("status"), 20).lower()
            item["status"] = status if status in ("ongoing", "closed") else "ongoing"
        else:
            item[name] = _text(raw.get(name), 600)
    return item if item.get(key) else None


def normalize_brief(raw: Any) -> dict:
    """Бриф в форме схемы v1: лишние поля отбрасываются, строки обрезаются."""
    raw = raw if isinstance(raw, dict) else {}
    brief = empty_brief()
    brief["summary"] = _long_text(raw.get("summary"), 3000)
    brief["notes"] = _long_text(raw.get("notes"), 3000)
    for section in BRIEF_SECTIONS:
        seen: set[str] = set()
        key = BRIEF_SECTIONS[section][0]
        for value in (raw.get(section) or [])[:MAX_SECTION_ITEMS]:
            item = _brief_item(section, value)
            if item and item[key].lower() not in seen:
                seen.add(item[key].lower())
                brief[section].append(item)
    return brief


def merge_brief(current: dict, patch: Any) -> dict:
    """Частичное обновление брифа.

    * ``summary`` / ``notes`` — строка заменяет прежнюю;
    * раздел-список (``people``, ``arcs``, ``jokes``, ``taboo``) — элементы
      добавляются или дополняются по ключу (имя, название, текст шутки, тема) без
      учёта регистра; ``{"name": "Влад", "_delete": true}`` — убрать элемент;
    * ``replace_sections: ["people"]`` — эти разделы заменить целиком.
    """
    if not isinstance(patch, dict):
        raise ValueError("patch — объект")
    unknown = set(patch) - {"summary", "notes", "replace_sections", "schema", "version", *BRIEF_SECTIONS}
    if unknown:
        raise ValueError(f"неизвестные поля брифа: {', '.join(sorted(unknown))} "
                         f"(есть summary, notes, {', '.join(BRIEF_SECTIONS)})")
    brief = normalize_brief(current)
    for name in ("summary", "notes"):
        if name in patch:
            brief[name] = _long_text(patch[name], 3000)
    replace = set(patch.get("replace_sections") or [])
    for section, (key, _fields) in BRIEF_SECTIONS.items():
        if section not in patch:
            continue
        items = patch[section]
        if not isinstance(items, list):
            raise ValueError(f"{section} — список")
        if section in replace:
            brief[section] = normalize_brief({section: items})[section]
            continue
        existing = brief[section]
        for raw in items:
            if isinstance(raw, dict) and raw.get("_delete"):
                target = _text(raw.get(key), 600).lower()
                existing[:] = [item for item in existing if item[key].lower() != target]
                continue
            item = _brief_item(section, raw)
            if not item:
                continue
            found = next((old for old in existing if old[key].lower() == item[key].lower()), None)
            if found is None:
                if len(existing) < MAX_SECTION_ITEMS:
                    existing.append(item)
                continue
            given = raw if isinstance(raw, dict) else {key: raw}
            for name, value in item.items():
                if name in given:  # дополняем только присланные поля
                    found[name] = value
    return brief


def get_brief(store: Any, project_id: int) -> dict:
    row = store.db.query_one("SELECT brief_json, brief_rev FROM projects WHERE id = ?", (int(project_id),))
    if not row:
        raise KeyError(f"project not found: {project_id}")
    return {"brief": normalize_brief(_loads(row["brief_json"], {})), "brief_rev": int(row["brief_rev"] or 0)}


def update_brief(store: Any, project_id: int, patch: Any, base_rev: int | None = None) -> dict:
    with store.db._lock:
        current = get_brief(store, project_id)
        if base_rev is not None and int(base_rev) != current["brief_rev"]:
            raise BriefConflict(f"бриф уже изменён (ревизия {current['brief_rev']}, у вас {base_rev}) — "
                                "перечитайте и повторите")
        brief = merge_brief(current["brief"], patch)
        store.db.execute(
            "UPDATE projects SET brief_json = ?, brief_rev = brief_rev + 1, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ?",
            (_json(brief), int(project_id)),
        )
    return get_brief(store, project_id)


def covered_clips(store: Any, project_id: int, limit: int = 150) -> dict:
    """«Что уже нарезано»: клипы проекта, которые сделаны всерьёз — отрендерены, ★,
    собраны агентом руками или опубликованы. Кандидаты анализа без рендера сюда не
    попадают: это ещё не нарезка, а предложения."""
    rows = store.db.query_all(
        """
        SELECT cp.id, cp.title, cp.source_id, cp.status, cp.favorite, cp.created_at,
               s.original_filename AS source_name,
               (SELECT COUNT(*) FROM clips c WHERE c.clip_plan_id = cp.id) AS renders,
               EXISTS (SELECT 1 FROM clips c JOIN jobs j ON j.clip_id = c.id
                         JOIN job_targets jt ON jt.job_id = j.id
                       WHERE c.clip_plan_id = cp.id AND jt.status = 'succeeded') AS published_service,
               EXISTS (SELECT 1 FROM project_publications mp WHERE mp.clip_plan_id = cp.id) AS published_manual
        FROM clip_plans cp
        JOIN sources s ON s.id = cp.source_id
        LEFT JOIN ai_analyses a ON a.id = cp.analysis_id
        WHERE s.project_id = ? AND cp.hidden = 0 AND cp.status != 'rejected'
          AND (cp.favorite = 1 OR cp.status IN ('rendered', 'rendering') OR a.provider = 'manual'
               OR EXISTS (SELECT 1 FROM clips c WHERE c.clip_plan_id = cp.id)
               OR EXISTS (SELECT 1 FROM project_publications mp WHERE mp.clip_plan_id = cp.id))
        ORDER BY cp.id DESC
        """,
        (int(project_id),),
    )
    total = len(rows)
    rows = rows[: max(1, int(limit))]
    ids = [int(r["id"]) for r in rows]
    pieces: dict[int, list] = {}
    tags = clip_tags_map(store, ids)
    if ids:
        marks = ",".join("?" * len(ids))
        for seg in store.db.query_all(
            f"""SELECT cps.clip_plan_id, s.start_sec, s.end_sec FROM clip_plan_segments cps
                JOIN ai_segments s ON s.id = cps.segment_id
                WHERE cps.clip_plan_id IN ({marks}) ORDER BY cps.clip_plan_id, cps.sort_order, cps.id""",
            ids,
        ):
            pieces.setdefault(int(seg["clip_plan_id"]), []).append(
                [round(float(seg["start_sec"]), 1), round(float(seg["end_sec"]), 1)])
    clips = [{
        "clip_id": int(r["id"]), "title": r["title"], "source_id": int(r["source_id"]),
        "source": r["source_name"] or "", "pieces": pieces.get(int(r["id"]), []),
        "status": r["status"], "favorite": bool(r["favorite"]), "rendered": bool(r["renders"]),
        "published": bool(r["published_service"] or r["published_manual"]), "tags": tags.get(int(r["id"]), []),
    } for r in rows]
    return {"clips": clips, "total": total}


def project_brief(store: Any, project_id: int, covered_limit: int = 150) -> dict:
    """Всё, что агент читает перед нарезкой и названиями: проект, лор, активные правила
    (проекта и глобальные), что уже нарезано, гипотезы и короткая статистика."""
    project = get_project(store, project_id)
    brief = get_brief(store, project_id)
    covered = covered_clips(store, project_id, limit=covered_limit)
    stats = project_stats(store, project_id, top=3)
    hypotheses = [{key: h[key] for key in ("id", "statement", "status", "conclusion", "group", "metric")}
                  for h in list_hypotheses(store, project_id)]
    from app.project_knowledge import brief_extras

    return {
        "project": {key: project.get(key) for key in ("id", "name", "kind", "kind_label", "description", "links",
                                                       "brand")} | {
            "sources": [{"source_id": s["id"], "name": s.get("original_filename") or s.get("original_url") or "",
                         "duration_sec": s.get("duration_sec"), "status": s.get("status")}
                        for s in project["sources"]]},
        **brief,
        "rules": [_rule_brief(r) for r in list_rules(store, project_id=project_id)],
        **brief_extras(store, project_id),
        "covered": covered["clips"],
        "covered_total": covered["total"],
        "hypotheses": hypotheses,
        "stats": {"totals": stats["totals"], "best": stats["best"], "worst": stats["worst"], "note": stats["note"]},
        "how_to": ("Перед нарезкой и названиями: держись лора (люди, шутки, арки), не бери темы из taboo, "
                   "соблюдай rules (dont — запрет, do — обязательно, preference — по возможности; "
                   "needs_review — правило под вопросом, уточни у владельца). facts — что владелец просил "
                   "запомнить, memes — мемы, которые он выбрал. covered — что уже нарезано: "
                   "не повторяй те же моменты (pieces — секунды источника). Новое о проекте со слов владельца — "
                   "remember_fact или update_project_brief; правило на будущее — add_rule с цитатой и датой."),
    }


def _rule_brief(rule: dict) -> dict:
    out = {key: rule.get(key) for key in ("id", "kind", "text", "scope", "quote", "said_at", "origin")}
    if rule.get("needs_review"):
        out["needs_review"] = True
        out["review_note"] = rule.get("review_note") or ""
    return out


# Хук черновика брифа из расшифровок. Платный шлюз (Polza) запрещён, Gemini часто
# недоступен — поэтому генератор не встроен: кто-то регистрирует функцию
# ``drafter(material) -> patch`` (например, свой локальный ИИ), а без неё агент
# пишет бриф сам: читает get_source_transcript и зовёт update_project_brief.
_brief_drafter: Callable[[dict], dict] | None = None


def register_brief_drafter(fn: Callable[[dict], dict] | None) -> None:
    global _brief_drafter
    _brief_drafter = fn


def brief_draft_material(store: Any, project_id: int, chars_per_source: int = 6000) -> dict:
    """Что дать генератору черновика: текущий бриф и отрывки расшифровок источников."""
    project = get_project(store, project_id)
    sources = []
    for source in project["sources"]:
        try:
            cues = store.get_source_transcript(int(source["id"]))
        except KeyError:
            cues = []
        text = " ".join(str(c.get("text") or "").strip() for c in cues)
        sources.append({"source_id": source["id"], "name": source.get("original_filename") or "",
                        "has_transcript": bool(cues), "excerpt": text[:chars_per_source]})
    return {"project": {"id": project["id"], "name": project["name"], "kind": project["kind"],
                        "description": project.get("description") or ""},
            "brief": get_brief(store, project_id)["brief"], "sources": sources,
            "instructions": ("Составь patch брифа vvf.project_brief v1: summary, people [{name, who, traits, aliases}], "
                             "arcs [{title, about}], jokes [{text, about}], taboo [{topic, why}]. Только то, что "
                             "видно в расшифровках; не выдумывай.")}


def draft_brief(store: Any, project_id: int, apply: bool = False) -> dict:
    material = brief_draft_material(store, project_id)
    if _brief_drafter is None:
        return {"ok": False, "available": False, "material": material,
                "note": ("Генератор черновика не подключён (платный шлюз запрещён, Gemini не используется). "
                         "Агент пишет бриф сам: get_source_transcript по источникам → update_project_brief.")}
    patch = _brief_drafter(material)
    out = {"ok": True, "available": True, "patch": patch}
    if apply:
        out.update(update_brief(store, project_id, patch))
    return out


# --------------------------------------------------------------------------- #
# Правила владельца
# --------------------------------------------------------------------------- #

_NEGATIONS = {"не", "нет", "ни", "никогда", "нельзя", "без", "запрещено", "запрет", "избегать", "избегай",
              "хватит", "перестань", "no", "not", "never", "dont", "avoid", "without", "stop"}
_STOPWORDS = {"и", "в", "во", "на", "с", "со", "к", "ко", "по", "за", "из", "от", "до", "а", "но", "или", "что",
              "это", "как", "для", "о", "об", "у", "же", "ли", "бы", "то", "там", "тут", "всегда", "лучше",
              "надо", "нужно", "стоит", "можно", "пожалуйста", "клип", "клипы", "клипах", "клипе", "клипов",
              "ролик", "ролики", "роликах", "видео", "the", "a", "an", "to", "of", "in", "on", "and", "or",
              "for", "with", "clip", "clips", "video", "always", "please"}
DUPLICATE_SCORE = 0.85
SIMILAR_SCORE = 0.55
CONFLICT_SCORE = 0.6


def _stem(word: str) -> str:
    word = word[:6]
    stripped = word.rstrip("аеёиоуыэюяйь")
    return stripped if len(stripped) >= 3 else word


def rule_tokens(text: str) -> tuple[list[str], bool]:
    """(основы значимых слов, есть ли отрицание). «Не брать политику» → (['брат', 'полит'], True)."""
    text = str(text or "").lower().replace("ё", "е").replace("n't", " not")
    words = re.findall(r"[a-zа-я0-9]+", text)
    negative = any(w in _NEGATIONS for w in words)
    core = [_stem(w) for w in words if w not in _NEGATIONS and w not in _STOPWORDS]
    return core, negative


def normalize_rule_text(text: str) -> str:
    return " ".join(rule_tokens(text)[0])


def rule_polarity(text: str, kind: str) -> bool:
    """True — запрет («не делай»): тип dont или отрицание в тексте."""
    return kind == "dont" or rule_tokens(text)[1]


def rule_similarity(a: str, b: str) -> float:
    """0..1: пересечение основ слов (Жаккар) или похожесть строк основ — что больше."""
    ta, tb = rule_tokens(a)[0], rule_tokens(b)[0]
    if not ta or not tb:
        return 0.0
    sa, sb = set(ta), set(tb)
    jaccard = len(sa & sb) / len(sa | sb)
    ratio = SequenceMatcher(None, " ".join(sorted(sa)), " ".join(sorted(sb))).ratio()
    return round(max(jaccard, ratio), 3)


def _rule_from_row(row: Any) -> dict:
    data = dict(row)
    data["needs_review"] = bool(data.get("needs_review"))
    return data


def get_rule(store: Any, rule_id: int) -> dict:
    row = store.db.query_one("SELECT * FROM project_rules WHERE id = ?", (int(rule_id),))
    if not row:
        raise KeyError(f"rule not found: {rule_id}")
    return _rule_from_row(row)


def list_rules(store: Any, project_id: int | None = None, status: str = "active",
               include_global: bool = True) -> list[dict]:
    """Правила проекта (+ глобальные). status: active (агенту — только они) / all / revoked / superseded."""
    where, params = [], []
    if project_id is not None:
        where.append("(project_id = ?" + (" OR scope = 'global')" if include_global else ")"))
        params.append(int(project_id))
    elif not include_global:
        where.append("scope = 'project'")
    if status and status != "all":
        where.append("status = ?")
        params.append(_choice(status, RULE_STATUSES, "status"))
    sql = "SELECT * FROM project_rules"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, scope DESC, id DESC"
    return [_rule_from_row(row) for row in store.db.query_all(sql, params)]


def find_similar_rules(store: Any, text: str, kind: str, project_id: int | None, scope: str = "project",
                       exclude: set[int] | None = None) -> dict:
    """Похожие активные правила: дубли (то же самое), противоречия (то же, но с обратным
    знаком: «брать политику» против «не брать политику») и просто похожие."""
    negative = rule_polarity(text, kind)
    pool = list_rules(store, project_id=project_id if scope == "project" else None, status="active",
                      include_global=True)
    if scope == "global" and project_id is not None:
        pool += [r for r in list_rules(store, project_id=project_id, status="active") if r not in pool]
    out = {"duplicates": [], "conflicts": [], "similar": []}
    for rule in pool:
        if exclude and rule["id"] in exclude:
            continue
        score = rule_similarity(text, rule["text"])
        same_sign = rule_polarity(rule["text"], rule["kind"]) == negative
        entry = {**_rule_brief(rule), "score": score}
        if same_sign and score >= DUPLICATE_SCORE:
            out["duplicates"].append(entry)
        elif not same_sign and score >= CONFLICT_SCORE:
            out["conflicts"].append(entry)
        elif score >= SIMILAR_SCORE:
            out["similar"].append(entry)
    for key in out:
        out[key].sort(key=lambda r: -r["score"])
    return out


def add_rule(store: Any, text: str, kind: str = "do", scope: str = "project", project_id: int | None = None,
             quote: str = "", origin: str = "", origin_ref: str = "", said_at: Any = None,
             replaces: list | None = None, hypothesis_id: int | None = None, force: bool = False) -> dict:
    """Записать правило владельца. Дубль не создаётся (вернётся существующее);
    противоречие с активным правилом — не создаётся без решения владельца:
    ``replaces=[id]`` (старое станет superseded) или ``force=True`` (оставить оба)."""
    text = _text(text, 500)
    if not text:
        raise ValueError("нужен текст правила")
    kind = _choice(kind, RULE_KINDS, "kind", "do")
    scope = _choice(scope, RULE_SCOPES, "scope", "project")
    if scope == "project":
        if project_id is None:
            raise ValueError("правило проекта — нужен project_id (или scope=global)")
        get_project(store, int(project_id), include_sources=False)
    else:
        project_id = None
    replaces_ids = {int(r) for r in (replaces or [])}
    for rule_id in replaces_ids:
        if get_rule(store, rule_id)["status"] != "active":
            raise ValueError(f"правило #{rule_id} уже не активно")
    found = find_similar_rules(store, text, kind, project_id, scope, exclude=replaces_ids)
    if found["duplicates"] and not force:
        dup = found["duplicates"][0]
        return {"ok": True, "created": False, "duplicate_of": dup, **found,
                "note": (f"Такое правило уже есть: #{dup['id']} «{dup['text']}» — не дублирую. Если владелец "
                         f"уточнил формулировку — replaces=[{dup['id']}].")}
    if found["conflicts"] and not force:
        clash = found["conflicts"][0]
        when = f" ({clash['said_at']})" if clash.get("said_at") else ""
        return {"ok": True, "created": False, "needs_confirmation": True, **found,
                "question": (f"Новое «{text}» противоречит правилу #{clash['id']} «{clash['text']}»{when}. "
                             f"Спроси владельца: заменить старое (replaces=[{clash['id']}]) или оставить оба "
                             "(force=true)?")}
    said = _date(said_at) or _now()
    cur = store.db.execute(
        """
        INSERT INTO project_rules (project_id, scope, kind, text, quote, origin, origin_ref, said_at, hypothesis_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (project_id, scope, kind, text, _long_text(quote, 2000), _text(origin, 40), _text(origin_ref, 300), said,
         int(hypothesis_id) if hypothesis_id else None),
    )
    rule_id = int(cur.lastrowid)
    for old in sorted(replaces_ids):
        store.db.execute(
            """UPDATE project_rules SET status = 'superseded', superseded_by = ?, revoked_at = ?,
               revoke_reason = ?, revoke_quote = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
            (rule_id, _now(), f"заменено правилом #{rule_id}", _long_text(quote, 2000), old),
        )
    return {"ok": True, "created": True, "rule": get_rule(store, rule_id),
            "superseded": sorted(replaces_ids), **found}


def revoke_rule(store: Any, rule_id: int | None = None, reason: str = "", quote: str = "",
                text: str | None = None, project_id: int | None = None) -> dict:
    """Отменить правило («пересмотрел, старое не работает»): оно остаётся в истории
    (status=revoked, причина, цитата, дата), агенту больше не отдаётся. Без rule_id —
    по тексту: ищем самое похожее активное; неоднозначно — вернём кандидатов."""
    if rule_id is None:
        if not text:
            raise ValueError("нужен rule_id или text")
        pool = list_rules(store, project_id=project_id, status="active")
        scored = sorted(((rule_similarity(text, r["text"]), r) for r in pool), key=lambda x: -x[0])
        scored = [(s, r) for s, r in scored if s >= SIMILAR_SCORE]
        if not scored or (len(scored) > 1 and scored[0][0] - scored[1][0] < 0.1):
            return {"ok": False, "revoked": False,
                    "error": "не понял, какое правило отменить — назови rule_id",
                    "candidates": [{**_rule_brief(r), "score": s} for s, r in scored[:5]]}
        rule_id = int(scored[0][1]["id"])
    rule = get_rule(store, int(rule_id))
    if rule["status"] != "active":
        return {"ok": False, "revoked": False, "error": f"правило #{rule_id} уже {rule['status']}", "rule": rule}
    store.db.execute(
        """UPDATE project_rules SET status = 'revoked', revoked_at = ?, revoke_reason = ?, revoke_quote = ?,
           updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
        (_now(), _text(reason, 500), _long_text(quote, 2000), int(rule_id)),
    )
    return {"ok": True, "revoked": True, "rule": get_rule(store, int(rule_id))}


# --------------------------------------------------------------------------- #
# Теги клипов
# --------------------------------------------------------------------------- #


def normalize_tag(tag: Any) -> str:
    text = str(tag or "").strip().lower().replace("ё", "е")
    text = re.sub(r"[\s\-]+", "_", text)
    text = re.sub(r"[^\w:]", "", text)
    return text[:60]


def _tags(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = re.split(r"[,\s]+", value)
    out = []
    for tag in value:
        norm = normalize_tag(tag)
        if norm and norm not in out:
            out.append(norm)
    return out[:30]


def clip_tags_map(store: Any, plan_ids: list[int]) -> dict[int, list[str]]:
    if not plan_ids:
        return {}
    marks = ",".join("?" * len(plan_ids))
    out: dict[int, list[str]] = {}
    for row in store.db.query_all(
        f"SELECT clip_plan_id, tag FROM clip_tags WHERE clip_plan_id IN ({marks}) ORDER BY tag", list(plan_ids)
    ):
        out.setdefault(int(row["clip_plan_id"]), []).append(row["tag"])
    return out


def get_clip_tags(store: Any, clip_plan_id: int) -> list[str]:
    return clip_tags_map(store, [int(clip_plan_id)]).get(int(clip_plan_id), [])


def set_clip_tags(store: Any, clip_plan_id: int, add: Any = None, remove: Any = None,
                  replace: Any = None) -> list[str]:
    store.get_clip_plan(int(clip_plan_id), include_segments=False)
    if replace is not None:
        store.db.execute("DELETE FROM clip_tags WHERE clip_plan_id = ?", (int(clip_plan_id),))
        add = replace
    for tag in _tags(remove):
        store.db.execute("DELETE FROM clip_tags WHERE clip_plan_id = ? AND tag = ?", (int(clip_plan_id), tag))
    for tag in _tags(add):
        store.db.execute("INSERT OR IGNORE INTO clip_tags (clip_plan_id, tag) VALUES (?, ?)",
                         (int(clip_plan_id), tag))
    return get_clip_tags(store, clip_plan_id)


# --------------------------------------------------------------------------- #
# Ручные публикации (выложено не через сервис)
# --------------------------------------------------------------------------- #


def _pub_from_row(row: Any) -> dict:
    data = dict(row)
    data["tags"] = _loads(data.pop("tags_json", "[]"), [])
    return data


def get_manual_publication(store: Any, publication_id: int) -> dict:
    row = store.db.query_one("SELECT * FROM project_publications WHERE id = ?", (int(publication_id),))
    if not row:
        raise KeyError(f"publication not found: {publication_id}")
    data = _pub_from_row(row)
    data["stats"] = latest_manual_stats(store, [int(publication_id)]).get(int(publication_id))
    return data


def add_manual_publication(store: Any, url: str, project_id: int | None = None, clip_plan_id: int | None = None,
                           title: str = "", published_at: Any = None, tags: Any = None, notes: str = "",
                           stats: dict | None = None) -> dict:
    """Видео, выложенное руками, по ссылке — к клипу или к проекту. ``stats`` — числа,
    введённые вручную (Instagram без сессии и т.п.); YouTube и TikTok сервис подтянет сам."""
    from app.platform_stats import platform_of_url

    url = _text(url, 500)
    if not re.match(r"^https?://", url):
        raise ValueError("нужна ссылка http(s)://")
    if clip_plan_id is not None:
        plan_project = project_of_plan(store, int(clip_plan_id))
        if project_id is None:
            project_id = plan_project
    if project_id is not None:
        get_project(store, int(project_id), include_sources=False)
    if project_id is None and clip_plan_id is None:
        raise ValueError("укажите проект или клип")
    if store.db.query_one("SELECT id FROM project_publications WHERE url = ?", (url,)):
        raise ValueError("эта ссылка уже добавлена")
    cur = store.db.execute(
        """INSERT INTO project_publications (project_id, clip_plan_id, url, platform, title, published_at, tags_json, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (project_id, clip_plan_id, url, platform_of_url(url), _text(title, 200), _date(published_at),
         _json(_tags(tags)), _long_text(notes, 1000)),
    )
    pub_id = int(cur.lastrowid)
    if stats:
        add_manual_stats(store, pub_id, stats)
    return get_manual_publication(store, pub_id)


def update_manual_publication(store: Any, publication_id: int, **fields: Any) -> dict:
    get_manual_publication(store, publication_id)
    updates: dict[str, Any] = {}
    if "title" in fields:
        updates["title"] = _text(fields["title"], 200)
    if "published_at" in fields:
        updates["published_at"] = _date(fields["published_at"])
    if "tags" in fields:
        updates["tags_json"] = _json(_tags(fields["tags"]))
    if "notes" in fields:
        updates["notes"] = _long_text(fields["notes"], 1000)
    if "clip_plan_id" in fields:
        plan_id = fields["clip_plan_id"]
        if plan_id is not None:
            project_of_plan(store, int(plan_id))
        updates["clip_plan_id"] = None if plan_id is None else int(plan_id)
    if updates:
        sets = ", ".join(f"{key} = ?" for key in updates)
        store.db.execute(f"UPDATE project_publications SET {sets}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (*updates.values(), int(publication_id)))
    return get_manual_publication(store, publication_id)


def delete_manual_publication(store: Any, publication_id: int) -> dict:
    get_manual_publication(store, publication_id)
    store.db.execute("DELETE FROM project_publications WHERE id = ?", (int(publication_id),))
    return {"deleted": True}


def _count_value(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"не число: {value}") from exc


def add_manual_stats(store: Any, publication_id: int, stats: dict | None, origin: str = "manual",
                     error: str = "") -> dict:
    """Снимок чисел ручной публикации. avg_retention — доля 0..1 (или проценты 0..100)."""
    stats = stats or {}
    retention = stats.get("avg_retention")
    if retention not in (None, ""):
        retention = float(retention)
        retention = retention / 100 if retention > 1 else retention
        retention = max(0.0, min(1.0, retention))
    else:
        retention = None
    values = {key: _count_value(stats.get(key)) for key in ("views", "likes", "comments", "shares")}
    if not error and all(v is None for v in values.values()) and retention is None:
        raise ValueError("нет ни одного числа")
    cur = store.db.execute(
        """INSERT INTO project_publication_stats
           (publication_id, views, likes, comments, shares, avg_retention, origin, error)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (int(publication_id), values["views"], values["likes"], values["comments"], values["shares"], retention,
         origin, _text(error, 300)),
    )
    return dict(store.db.query_one("SELECT * FROM project_publication_stats WHERE id = ?", (cur.lastrowid,)))


def latest_manual_stats(store: Any, publication_ids: list[int] | None = None) -> dict[int, dict]:
    """publication_id → последние удачные числа (+ последняя попытка и её ошибка)."""
    where = ""
    params: list = []
    if publication_ids is not None:
        if not publication_ids:
            return {}
        where = f" AND publication_id IN ({','.join('?' * len(publication_ids))})"
        params = list(publication_ids)
    out: dict[int, dict] = {}
    for row in store.db.query_all(
        f"""SELECT ps.* FROM project_publication_stats ps JOIN (
              SELECT publication_id, MAX(id) AS mid FROM project_publication_stats
              WHERE error = ''{where} GROUP BY publication_id) m ON ps.id = m.mid""",
        params,
    ):
        out[int(row["publication_id"])] = {key: row[key] for key in
                                           ("views", "likes", "comments", "shares", "avg_retention", "captured_at",
                                            "origin")}
    for row in store.db.query_all(
        f"""SELECT ps.publication_id, ps.captured_at, ps.error FROM project_publication_stats ps JOIN (
              SELECT publication_id, MAX(id) AS mid FROM project_publication_stats
              WHERE 1 = 1{where} GROUP BY publication_id) m ON ps.id = m.mid""",
        params,
    ):
        entry = out.setdefault(int(row["publication_id"]), {"views": None, "likes": None, "comments": None,
                                                            "shares": None, "avg_retention": None,
                                                            "captured_at": None, "origin": ""})
        entry["last_attempt_at"] = row["captured_at"]
        entry["error"] = row["error"] or ""
    return out


def refresh_manual_publication(store: Any, publication_id: int) -> dict:
    """Числа ручной публикации тем же путём, что публикации сервиса (yt-dlp для
    YouTube/TikTok). Instagram без входа чисел не отдаёт — тогда только вручную."""
    from app import platform_stats

    pub = get_manual_publication(store, publication_id)
    platform = platform_stats.platform_of_url(pub["url"])
    if platform not in ("youtube", "tiktok"):
        reason = ("Instagram без входа чисел не отдаёт — введите вручную" if platform == "instagram"
                  else "площадка не поддерживается — введите числа вручную")
        add_manual_stats(store, publication_id, None, origin="auto", error=f"пропуск: {reason}")
        return {"ok": False, "skipped": True, "error": reason}
    try:
        stats = platform_stats.fetch_ytdlp_stats(pub["url"])
    except RuntimeError as exc:
        add_manual_stats(store, publication_id, None, origin="auto", error=str(exc)[:300] or "ошибка")
        return {"ok": False, "error": str(exc)[:300]}
    add_manual_stats(store, publication_id, stats, origin="auto")
    return {"ok": True, "stats": stats}


def due_manual_publications(store: Any, *, force: bool) -> list[dict]:
    """Ручные публикации YouTube/TikTok, по которым пора за числами (те же интервалы, что у сервиса)."""
    from app import platform_stats

    rows = store.db.query_all(
        """SELECT mp.id, mp.url, mp.platform,
                  (SELECT MAX(captured_at) FROM project_publication_stats ps
                    WHERE ps.publication_id = mp.id AND ps.origin = 'auto') AS last_attempt_at
           FROM project_publications mp WHERE mp.platform IN ('youtube', 'tiktok') ORDER BY mp.id"""
    )
    return platform_stats.due_targets([dict(r) for r in rows], force=force)


def collect_manual_stats(store: Any, *, force: bool = False, sleep: Callable[[float], None] | None = None,
                         pause: float = 0.0) -> dict:
    """Проход сбора по ручным публикациям (зовётся из platform_stats.collect_stats)."""
    summary = {"checked": 0, "ok": 0, "failed": 0}
    for index, pub in enumerate(due_manual_publications(store, force=force)):
        if index and pause > 0 and sleep:
            sleep(pause)
        summary["checked"] += 1
        result = refresh_manual_publication(store, int(pub["id"]))
        summary["ok" if result.get("ok") else "failed"] += 1
    return summary


def list_manual_publications(store: Any, project_id: int) -> list[dict]:
    rows = store.db.query_all(
        """SELECT mp.*, cp.title AS clip_title FROM project_publications mp
           LEFT JOIN clip_plans cp ON cp.id = mp.clip_plan_id
           LEFT JOIN sources s ON s.id = cp.source_id
           WHERE (mp.clip_plan_id IS NOT NULL AND s.project_id = ?)
              OR (mp.clip_plan_id IS NULL AND mp.project_id = ?)
              OR (mp.clip_plan_id IS NOT NULL AND s.project_id IS NULL AND mp.project_id = ?)
           ORDER BY mp.id DESC""",
        (int(project_id), int(project_id), int(project_id)),
    )
    pubs = [_pub_from_row(r) for r in rows]
    latest = latest_manual_stats(store, [p["id"] for p in pubs])
    for pub in pubs:
        pub["stats"] = latest.get(int(pub["id"]))
    return pubs


# --------------------------------------------------------------------------- #
# Статистика проекта
# --------------------------------------------------------------------------- #


def _parse_time(text: Any) -> datetime | None:
    text = str(text or "").strip().replace("T", " ")
    for fmt, size in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d %H:%M", 16), ("%Y-%m-%d", 10)):
        try:
            return datetime.strptime(text[:size], fmt)
        except ValueError:
            continue
    return None


def _sum(values: list) -> int | None:
    known = [int(v) for v in values if v is not None]
    return sum(known) if known else None


def project_items(store: Any, project_id: int, with_features: bool = False) -> list[dict]:
    """Единицы статистики проекта: клип (все его публикации — сервиса и ручные — в сумме)
    или ручная публикация без клипа. У каждой — теги, числа и, по запросу, признаки монтажа."""
    get_project(store, project_id, include_sources=False)
    latest = store.latest_publication_stats()
    items: dict[str, dict] = {}
    now = datetime.utcnow()

    def item_for(key: str, **base: Any) -> dict:
        return items.setdefault(key, {"key": key, "clip_plan_id": None, "title": "", "source_id": None,
                                      "tags": [], "publications": [], **base})

    for row in store.db.query_all(
        """
        SELECT jt.id AS target_id, jt.platform, jt.remote_url, jt.finished_at, j.title, j.clip_id,
               c.clip_plan_id, c.source_id, cp.title AS plan_title
        FROM job_targets jt
        JOIN jobs j ON j.id = jt.job_id
        JOIN clips c ON c.id = j.clip_id
        JOIN sources s ON s.id = c.source_id
        LEFT JOIN clip_plans cp ON cp.id = c.clip_plan_id
        WHERE jt.status = 'succeeded' AND jt.remote_url != ''
          AND (s.project_id = ? OR (s.project_id IS NULL AND c.clip_plan_id IN
               (SELECT pp.clip_plan_id FROM plan_projects pp WHERE pp.project_id = ?)))
        ORDER BY jt.id
        """,
        (int(project_id), int(project_id)),
    ):
        plan_id = row["clip_plan_id"]
        key = f"plan:{plan_id}" if plan_id else f"clip:{row['clip_id']}"
        item = item_for(key, clip_plan_id=plan_id, title=row["plan_title"] or row["title"] or "",
                        source_id=row["source_id"])
        stats = latest.get(int(row["target_id"]), {})
        item["publications"].append({
            "kind": "service", "id": row["target_id"], "platform": row["platform"] or "", "url": row["remote_url"],
            "published_at": row["finished_at"], **{k: stats.get(k) for k in
                                                   ("views", "likes", "comments", "shares", "avg_retention",
                                                    "captured_at")},
            "error": stats.get("error") or "",
        })
    manual = list_manual_publications(store, project_id)
    for pub in manual:
        plan_id = pub.get("clip_plan_id")
        if plan_id:
            item = item_for(f"plan:{plan_id}", clip_plan_id=plan_id, title=pub.get("clip_title") or pub["title"])
        else:
            item = item_for(f"pub:{pub['id']}", title=pub["title"] or pub["url"], manual_id=pub["id"])
            item["tags"] = list(pub["tags"])
        stats = pub.get("stats") or {}
        item["publications"].append({
            "kind": "manual", "id": pub["id"], "platform": pub["platform"], "url": pub["url"],
            "published_at": pub["published_at"] or pub["created_at"],
            **{k: stats.get(k) for k in ("views", "likes", "comments", "shares", "avg_retention", "captured_at")},
            "error": stats.get("error") or "",
        })
        if plan_id:  # теги ручной публикации клипа тоже признаки клипа
            item["tags"] = sorted(set(item["tags"]) | set(pub["tags"]))
    plan_ids = [int(i["clip_plan_id"]) for i in items.values() if i["clip_plan_id"]]
    tags = clip_tags_map(store, plan_ids)
    for item in items.values():
        pubs = item["publications"]
        if item["clip_plan_id"]:
            item["tags"] = sorted(set(item["tags"]) | set(tags.get(int(item["clip_plan_id"]), [])))
        views = _sum([p["views"] for p in pubs])
        likes = _sum([p["likes"] for p in pubs])
        published = [t for t in (_parse_time(p["published_at"]) for p in pubs) if t]
        age = round((now - min(published)).total_seconds() / 86400, 1) if published else None
        retention = [float(p["avg_retention"]) for p in pubs if p.get("avg_retention") is not None]
        item.update(
            views=views, likes=likes, comments=_sum([p["comments"] for p in pubs]),
            shares=_sum([p["shares"] for p in pubs]),
            like_rate=round(likes / views, 4) if views and likes is not None else None,
            views_per_day=round(views / max(1.0, age), 1) if views is not None and age is not None else None,
            avg_retention=round(sum(retention) / len(retention), 3) if retention else None,
            age_days=age, platforms=sorted({p["platform"] for p in pubs if p["platform"]}),
        )
        item["features"] = None
        if with_features and item["clip_plan_id"]:
            from app.platform_stats import plan_features

            try:
                item["features"] = plan_features(store, int(item["clip_plan_id"]))
            except Exception:  # noqa: BLE001 - клип сломан/удалён: числа всё равно считаем
                item["features"] = None
    out = list(items.values())
    out.sort(key=lambda i: (i["views"] is None, -(i["views"] or 0)))
    return out


def _median(values: list[float]) -> float | None:
    return float(statistics.median(values)) if values else None


def project_stats(store: Any, project_id: int, top: int = 5) -> dict:
    """Агрегаты проекта: суммы, медиана просмотров, удержание (если есть), площадки,
    лучшие и худшие клипы."""
    items = project_items(store, project_id)
    with_views = [i for i in items if i["views"] is not None]
    retention = [i["avg_retention"] for i in items if i["avg_retention"] is not None]
    platforms: dict[str, dict] = {}
    for item in items:
        for pub in item["publications"]:
            entry = platforms.setdefault(pub["platform"] or "другое", {"publications": 0, "views": None})
            entry["publications"] += 1
            if pub["views"] is not None:
                entry["views"] = (entry["views"] or 0) + int(pub["views"])

    def brief(item: dict) -> dict:
        return {key: item.get(key) for key in ("key", "clip_plan_id", "title", "views", "likes", "like_rate",
                                               "views_per_day", "avg_retention", "age_days", "platforms", "tags")}

    top = max(1, int(top))
    best = with_views[:top]
    worst = [i for i in with_views[::-1][:top] if i not in best]
    note = ("Просмотры зависят от возраста публикации и площадки — сравнивай views_per_day.")
    if len(with_views) < 4:
        note = "Мало данных (меньше 4 клипов с числами) — выводов не делай. " + note
    return {
        "totals": {
            "items": len(items), "items_with_stats": len(with_views),
            "publications": sum(len(i["publications"]) for i in items),
            "views": _sum([i["views"] for i in items]), "likes": _sum([i["likes"] for i in items]),
            "comments": _sum([i["comments"] for i in items]),
            "median_views": _median([float(i["views"]) for i in with_views]),
            "avg_retention": round(sum(retention) / len(retention), 3) if retention else None,
        },
        "platforms": platforms,
        "best": [brief(i) for i in best],
        "worst": [brief(i) for i in worst],
        "items": [brief(i) | {"publications": i["publications"]} for i in items],
        "note": note,
    }


# --------------------------------------------------------------------------- #
# Гипотезы
# --------------------------------------------------------------------------- #

CONFIRM_LIFT = 1.2  # группа лучше остальных хотя бы на 20% по медиане
CONFIRM_SHARE = 0.6  # и не меньше 60% группы выше медианы остальных
VERDICT_WORDS = {"confirmed": "подтверждается", "rejected": "не подтверждается", "inconclusive": "неясно"}
_OPS = {
    "==": lambda a, b: a == b, "!=": lambda a, b: a != b, ">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
}
FEATURE_NAMES = ("duration", "pieces", "hook_title", "subtitles", "music", "layers", "sfx", "cuts", "inserts",
                 "camera_moves", "layout", "chroma_key", "transition")


def normalize_condition(raw: Any) -> dict:
    """Признак группы: ``{"tag": "hook_question"}``, ``{"feature": "duration", "op": "<", "value": 30}``,
    ``{"all": [...]}``, ``{"any": [...]}``, ``{"not": {...}}``. Строка — это тег (``"tag:x"`` тоже)."""
    if isinstance(raw, str):
        text = raw.strip()
        if text.startswith("tag:"):
            text = text[4:]
        tag = normalize_tag(text)
        if not tag:
            raise ValueError("пустой тег группы")
        return {"tag": tag}
    if not isinstance(raw, dict) or not raw:
        raise ValueError("группа — тег или условие {tag} / {feature, op, value}")
    if "tag" in raw:
        tag = normalize_tag(raw["tag"])
        if not tag:
            raise ValueError("пустой тег группы")
        return {"tag": tag}
    if "feature" in raw:
        feature = str(raw["feature"])
        if feature not in FEATURE_NAMES:
            raise ValueError(f"признак: одно из {', '.join(FEATURE_NAMES)}")
        op = str(raw.get("op") or "==")
        if op not in _OPS:
            raise ValueError(f"op: одно из {' '.join(_OPS)}")
        value = raw.get("value", True)
        if isinstance(value, (dict, list)):
            raise ValueError("value — число, строка или да/нет")
        return {"feature": feature, "op": op, "value": value}
    for key in ("all", "any"):
        if key in raw:
            parts = raw[key]
            if not isinstance(parts, list) or not parts:
                raise ValueError(f"{key} — непустой список условий")
            return {key: [normalize_condition(p) for p in parts]}
    if "not" in raw:
        return {"not": normalize_condition(raw["not"])}
    raise ValueError("группа — тег или условие {tag} / {feature, op, value}")


def condition_uses_features(cond: dict) -> bool:
    if "feature" in cond:
        return True
    return any(condition_uses_features(c) for c in cond.get("all", []) + cond.get("any", [])) or (
        "not" in cond and condition_uses_features(cond["not"]))


def matches(cond: dict, item: dict) -> bool | None:
    """Попадает ли клип в группу. None — не известно (признаков монтажа нет)."""
    if "tag" in cond:
        return cond["tag"] in (item.get("tags") or [])
    if "feature" in cond:
        features = item.get("features")
        if not features or cond["feature"] not in features:
            return None
        try:
            return bool(_OPS[cond["op"]](features[cond["feature"]], cond["value"]))
        except TypeError:
            return None
    if "all" in cond:
        results = [matches(c, item) for c in cond["all"]]
        return False if False in results else (None if None in results else True)
    if "any" in cond:
        results = [matches(c, item) for c in cond["any"]]
        return True if True in results else (None if None in results else False)
    if "not" in cond:
        inner = matches(cond["not"], item)
        return None if inner is None else not inner
    return None


def describe_condition(cond: dict) -> str:
    if "tag" in cond:
        return f"тег {cond['tag']}"
    if "feature" in cond:
        return f"{cond['feature']} {cond['op']} {cond['value']}"
    if "all" in cond:
        return " и ".join(describe_condition(c) for c in cond["all"])
    if "any" in cond:
        return " или ".join(describe_condition(c) for c in cond["any"])
    if "not" in cond:
        return f"не ({describe_condition(cond['not'])})"
    return "?"


def _hyp_from_row(row: Any) -> dict:
    data = dict(row)
    data["group"] = _loads(data.pop("group_json", "{}"), {})
    baseline = data.pop("baseline_json", "")
    data["baseline"] = _loads(baseline, {}) or None
    data["result"] = _loads(data.pop("result_json", "{}"), {})
    data["status_locked"] = bool(data.get("status_locked"))
    return data


def get_hypothesis(store: Any, hypothesis_id: int) -> dict:
    row = store.db.query_one("SELECT * FROM project_hypotheses WHERE id = ?", (int(hypothesis_id),))
    if not row:
        raise KeyError(f"hypothesis not found: {hypothesis_id}")
    return _hyp_from_row(row)


def list_hypotheses(store: Any, project_id: int, status: str | None = None) -> list[dict]:
    sql, params = "SELECT * FROM project_hypotheses WHERE project_id = ?", [int(project_id)]
    if status:
        sql += " AND status = ?"
        params.append(_choice(status, HYPOTHESIS_STATUSES, "status"))
    return [_hyp_from_row(r) for r in store.db.query_all(sql + " ORDER BY id DESC", params)]


def add_hypothesis(store: Any, project_id: int, statement: str, group: Any, metric: str = "views",
                   direction: str = "higher", min_n: int = 5, baseline: Any = None, rule_text: str = "",
                   origin: str = "") -> dict:
    get_project(store, project_id, include_sources=False)
    statement = _text(statement, 500)
    if not statement:
        raise ValueError("нужно утверждение гипотезы")
    group_cond = normalize_condition(group)
    baseline_cond = normalize_condition(baseline) if baseline not in (None, "", {}) else None
    min_n = max(2, min(int(min_n or 5), 1000))
    cur = store.db.execute(
        """INSERT INTO project_hypotheses (project_id, statement, group_json, baseline_json, metric, direction,
           min_n, rule_text, origin) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (int(project_id), statement, _json(group_cond), _json(baseline_cond) if baseline_cond else "",
         _choice(metric, METRICS, "metric", "views"), _choice(direction, ("higher", "lower"), "direction", "higher"),
         min_n, _text(rule_text, 500), _text(origin, 40)),
    )
    return get_hypothesis(store, int(cur.lastrowid))


def update_hypothesis(store: Any, hypothesis_id: int, **fields: Any) -> dict:
    """Правка гипотезы. Ручной status фиксируется (status_locked) — пересчёт его не перетрёт;
    ``status_locked: false`` — снова считать автоматически."""
    get_hypothesis(store, hypothesis_id)
    updates: dict[str, Any] = {}
    if "statement" in fields:
        updates["statement"] = _text(fields["statement"], 500)
    if "group" in fields:
        updates["group_json"] = _json(normalize_condition(fields["group"]))
    if "baseline" in fields:
        updates["baseline_json"] = _json(normalize_condition(fields["baseline"])) if fields["baseline"] else ""
    if "metric" in fields:
        updates["metric"] = _choice(fields["metric"], METRICS, "metric")
    if "direction" in fields:
        updates["direction"] = _choice(fields["direction"], ("higher", "lower"), "direction")
    if "min_n" in fields:
        updates["min_n"] = max(2, min(int(fields["min_n"]), 1000))
    if "rule_text" in fields:
        updates["rule_text"] = _text(fields["rule_text"], 500)
    if "status" in fields:
        updates["status"] = _choice(fields["status"], HYPOTHESIS_STATUSES, "status")
        updates["status_locked"] = 1
    if "status_locked" in fields:
        updates["status_locked"] = 1 if fields["status_locked"] else 0
    if "conclusion" in fields:
        updates["conclusion"] = _long_text(fields["conclusion"], 1000)
    if updates:
        sets = ", ".join(f"{key} = ?" for key in updates)
        store.db.execute(f"UPDATE project_hypotheses SET {sets}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (*updates.values(), int(hypothesis_id)))
    return get_hypothesis(store, hypothesis_id)


def delete_hypothesis(store: Any, hypothesis_id: int) -> dict:
    get_hypothesis(store, hypothesis_id)
    store.db.execute("DELETE FROM project_hypotheses WHERE id = ?", (int(hypothesis_id),))
    return {"deleted": True}


def _fmt(value: float | None, metric: str) -> str:
    if value is None:
        return "—"
    if metric in ("like_rate", "avg_retention"):
        return f"{value * 100:.1f}%".replace(".", ",")
    if abs(value) >= 100:
        return f"{round(value):,}".replace(",", " ")
    return f"{value:.1f}".rstrip("0").rstrip(".").replace(".", ",")


def evaluate_hypothesis(hyp: dict, items: list[dict]) -> dict:
    """Сравнить группу с остальными (или с baseline) по метрике: медианы, размеры групп,
    во сколько раз, доля группы выше медианы остальных. Без p-value: при N в десятки
    клипов это псевдоточность. Пока в любой группе меньше min_n — «мало данных»."""
    metric = hyp.get("metric") or "views"
    cond = hyp["group"]
    base = hyp.get("baseline")
    group_vals, rest_vals, unknown = [], [], 0
    for item in items:
        value = item.get(metric)
        if value is None:
            continue
        hit = matches(cond, item)
        if hit is None:
            unknown += 1
            continue
        if hit:
            group_vals.append(float(value))
        elif base is None or matches(base, item):
            rest_vals.append(float(value))
    min_n = int(hyp.get("min_n") or 5)
    med_g, med_r = _median(group_vals), _median(rest_vals)
    result: dict[str, Any] = {
        "metric": metric, "direction": hyp.get("direction") or "higher", "min_n": min_n,
        "group": {"label": describe_condition(cond), "n": len(group_vals), "median": med_g},
        "rest": {"label": describe_condition(base) if base else "остальные клипы проекта", "n": len(rest_vals),
                 "median": med_r},
        "unknown": unknown, "lift": None, "share_above": None, "enough": False,
    }
    label = METRIC_LABELS.get(metric, metric)
    if len(group_vals) < min_n or len(rest_vals) < min_n:
        result["status"] = "testing"
        result["verdict"] = (f"мало данных: клипов с числами в группе {len(group_vals)}, в сравнении "
                             f"{len(rest_vals)} — нужно хотя бы по {min_n}")
        return result
    result["enough"] = True
    lower = result["direction"] == "lower"
    num, den = (med_r, med_g) if lower else (med_g, med_r)
    if den == 0:
        lift = float("inf") if num > 0 else 1.0
    else:
        lift = num / den
    share = sum(1 for v in group_vals if (v < med_r if lower else v > med_r)) / len(group_vals)
    result["lift"] = None if lift == float("inf") else round(lift, 2)
    result["share_above"] = round(share, 2)
    if lift >= CONFIRM_LIFT and share >= CONFIRM_SHARE:
        status = "confirmed"
    elif lift <= 1.0:
        status = "rejected"
    else:
        status = "inconclusive"
    times = "∞" if lift == float("inf") else f"{lift:.2f}".rstrip("0").rstrip(".").replace(".", ",")
    better = "ниже" if lower else "выше"
    result["status"] = status
    result["verdict"] = (
        f"{VERDICT_WORDS[status]}: медиана {label} в группе {_fmt(med_g, metric)} (n={len(group_vals)}) "
        f"против {_fmt(med_r, metric)} (n={len(rest_vals)}) — ×{times}; "
        f"{round(share * len(group_vals))} из {len(group_vals)} {better} медианы сравнения"
    )
    return result


def evaluate_hypotheses(store: Any, project_id: int, hypothesis_ids: list | None = None) -> dict:
    """Пересчитать гипотезы проекта по свежим числам. Правила, рождённые гипотезой,
    которую статистика перестала подтверждать, помечаются «на пересмотр»."""
    hyps = list_hypotheses(store, project_id)
    if hypothesis_ids:
        wanted = {int(h) for h in hypothesis_ids}
        hyps = [h for h in hyps if h["id"] in wanted]
    need_features = any(condition_uses_features(h["group"]) or
                        (h.get("baseline") and condition_uses_features(h["baseline"])) for h in hyps)
    items = project_items(store, project_id, with_features=need_features)
    out = []
    for hyp in hyps:
        result = evaluate_hypothesis(hyp, items)
        status = hyp["status"] if hyp["status_locked"] else result["status"]
        store.db.execute(
            """UPDATE project_hypotheses SET status = ?, conclusion = ?, result_json = ?, evaluated_at = ?,
               updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
            (status, result["verdict"], _json(result), _now(), hyp["id"]),
        )
        reviewed = _review_rules_of(store, hyp["id"], status, result)
        out.append({"id": hyp["id"], "statement": hyp["statement"], "status": status,
                    "conclusion": result["verdict"], "result": result, "rules_flagged": reviewed,
                    "suggest_rule": bool(status == "confirmed" and not _rules_of(store, hyp["id"]))})
    return {"project_id": int(project_id), "items_with_numbers": sum(1 for i in items if i.get("views") is not None),
            "hypotheses": out,
            "note": ("Подтверждённая гипотеза — повод предложить владельцу правило (add_rule с hypothesis_id). "
                     "Медианы и размеры групп — без претензии на научность.")}


def _rules_of(store: Any, hypothesis_id: int) -> list[dict]:
    return [_rule_from_row(r) for r in store.db.query_all(
        "SELECT * FROM project_rules WHERE hypothesis_id = ? AND status = 'active'", (int(hypothesis_id),))]


def _review_rules_of(store: Any, hypothesis_id: int, status: str, result: dict) -> list[int]:
    flagged = []
    for rule in _rules_of(store, hypothesis_id):
        if status == "confirmed":
            if rule["needs_review"]:
                store.db.execute("UPDATE project_rules SET needs_review = 0, review_note = '', "
                                 "updated_at = CURRENT_TIMESTAMP WHERE id = ?", (rule["id"],))
        elif result.get("enough") and status in ("rejected", "inconclusive"):
            store.db.execute(
                "UPDATE project_rules SET needs_review = 1, review_note = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                (f"гипотеза #{hypothesis_id} больше не подтверждается: {result['verdict']}"[:500], rule["id"]),
            )
            flagged.append(int(rule["id"]))
    return flagged


def promote_hypothesis(store: Any, hypothesis_id: int, text: str = "", kind: str = "do", quote: str = "",
                       force: bool = False) -> dict:
    """Подтверждённая гипотеза → правило проекта (через add_rule: дубли и противоречия те же)."""
    hyp = get_hypothesis(store, hypothesis_id)
    if hyp["status"] != "confirmed":
        raise ValueError("правило — только из подтверждённой гипотезы")
    return add_rule(store, text or hyp["rule_text"] or hyp["statement"], kind=kind, scope="project",
                    project_id=hyp["project_id"], quote=quote or hyp["conclusion"], origin="hypothesis",
                    origin_ref=f"hypothesis:{hyp['id']}", hypothesis_id=hyp["id"], force=force)
