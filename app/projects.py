"""Проекты: несколько источников одного канала или сериала под одной крышей.

Зачем. Источник (``sources``) — одно видео: стрим, серия, запись экрана. Владелец режет
сериями — стримы одной витуберши, серии одного аниме, промо одного заказчика, — и
агенту нужна память выше уровня видео: кто есть кто, внутренние шутки, что уже
нарезано, что владелец просил и что запрещал, что заходит по цифрам. Это и есть
проект. На нём позже встанет «автопилот канала».

Про слово «проект». Исторически в UI и в MCP «проект» — это ИСТОЧНИК: страница
``/v/:sourceId``, инструмент ``list_projects``. У инструментов источника
(``agent_tools.SOURCE_ID_TOOLS``) аргумент — ``source_id``, а ``project_id`` — его старое имя
(оба и разные — отказ). Новая сущность живёт в таблице ``projects``; в интерфейсе она
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

Где что лежит. Здесь — схема, сводка для агента ``project_brief`` и черновик брифа ``draft_brief``. По файлам: проекты и общее ``app/projects_core.py``, правила
``app/projects_rules.py``, теги, публикации и статистика ``app/projects_stats.py``, гипотезы ``app/projects_hypotheses.py``, бриф
``app/projects_brief.py``. Те модули этот не импортируют, а он их — поэтому все имена по-прежнему доступны отсюда.
"""

from __future__ import annotations

from typing import Any, Callable

from app.projects_core import (  # noqa: F401
    PROJECT_KINDS, PROJECT_KIND_LABELS, DATE_RE, _now, _text, _long_text, _date, _json, _loads, _choice, _links,
    _project_from_row, _check_brand, create_project, list_projects, get_project, project_sources, update_project,
    delete_project, set_source_project, project_of_source, project_of_plan, assign_plan_project, BRIEF_SCHEMA,
    BRIEF_VERSION, BRIEF_SECTIONS, BRIEF_SECTION_LABELS, BriefConflict, empty_brief,
)
from app.projects_rules import (  # noqa: F401
    RULE_KINDS, RULE_SCOPES, RULE_STATUSES, _rule_brief, _NEGATIONS, _STOPWORDS, DUPLICATE_SCORE, SIMILAR_SCORE,
    CONFLICT_SCORE, _stem, rule_tokens, normalize_rule_text, rule_polarity, rule_similarity, _rule_from_row,
    get_rule, list_rules, find_similar_rules, add_rule, revoke_rule,
)
from app.projects_stats import (  # noqa: F401
    normalize_tag, _tags, clip_tags_map, get_clip_tags, set_clip_tags, _pub_from_row, get_manual_publication,
    add_manual_publication, update_manual_publication, delete_manual_publication, _count_value, add_manual_stats,
    latest_manual_stats, refresh_manual_publication, due_manual_publications, collect_manual_stats,
    list_manual_publications, _parse_time, _sum, project_items, _median, project_stats,
)
from app.projects_hypotheses import (  # noqa: F401
    HYPOTHESIS_STATUSES, METRICS, METRIC_LABELS, CONFIRM_LIFT, CONFIRM_SHARE, VERDICT_WORDS, _OPS, FEATURE_NAMES,
    normalize_condition, condition_uses_features, matches, describe_condition, _hyp_from_row, get_hypothesis,
    list_hypotheses, add_hypothesis, update_hypothesis, delete_hypothesis, _fmt, evaluate_hypothesis,
    evaluate_hypotheses, _rules_of, _review_rules_of, promote_hypothesis,
)
from app.projects_brief import (  # noqa: F401
    MAX_SECTION_ITEMS, MAX_NOTES, _brief_item, normalize_brief, merge_brief, get_brief, update_brief, covered_clips,
    project_brief, brief_draft_material,
)

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


# Хук черновика брифа из расшифровок. Платный шлюз (Polza) запрещён, Gemini часто
# недоступен — поэтому генератор не встроен: кто-то регистрирует функцию
# ``drafter(material) -> patch`` (например, свой локальный ИИ), а без неё агент
# пишет бриф сам: читает get_source_transcript и зовёт update_project_brief.
_brief_drafter: Callable[[dict], dict] | None = None


def register_brief_drafter(fn: Callable[[dict], dict] | None) -> None:
    global _brief_drafter
    _brief_drafter = fn


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
