"""Чьи «Файлы для монтажа»: общие и файлы проекта.

``montage_assets.project_id``: NULL — общий файл (мемы, паки, логотипы брендов), число — файл проекта
(кадры ИИ-видео, маскот, запись экрана промо). Правило одно на всех, кто подбирает файл по id:

* клипу видны общие файлы и файлы его проекта; клипу без проекта — только общие;
* новый файл из чужого проекта в клип не ставится: инструменты агента отвечают понятным отказом;
* то, что в клипе уже стоит, не трогаем: правка такого слоя и рендер работают как раньше
  (рендер читает файл по id и эту проверку не зовёт).

Проект клипа — ``project_of_plan`` (источник или ``plan_projects``). У холстов старых сборок ИИ-видео,
объяснялок и историй этих связей нет: проект берём из их серии, ролика или эпизода (``_pipeline_project``).
"""

from __future__ import annotations

from typing import Any, Iterable

# Откуда у холста без проекта берётся проект: таблица и колонка клипа в ней.
PIPELINE_LINKS = (("aiv_scripts", "clip_plan_id"), ("expl_videos", "clip_id"), ("story_episodes", "clip_id"))


def _pipeline_project(store: Any, clip_plan_id: int) -> int | None:
    for table, column in PIPELINE_LINKS:
        row = store.db.query_one(f"SELECT project_id FROM {table} WHERE {column} = ? LIMIT 1", (int(clip_plan_id),))
        if row and row["project_id"] is not None:
            return int(row["project_id"])
    return None


def clip_project(store: Any, clip_plan_id: int) -> int | None:
    """Проект, чьи файлы видны клипу (None — клипу видны только общие)."""
    from app.projects import project_of_plan

    project = project_of_plan(store, int(clip_plan_id))
    return project if project is not None else _pipeline_project(store, int(clip_plan_id))


def allowed(asset: dict, project_id: int | None) -> bool:
    """Можно ли брать файл в контексте проекта ``project_id`` (None — только общие)."""
    owner = asset.get("project_id")
    return owner is None or (project_id is not None and int(owner) == int(project_id))


def render_asset_ids(render: Any) -> set[int]:
    """Какие файлы библиотеки уже стоят в клипе: слои, вставки, концовка."""
    from app.motion import layer_asset_refs

    render = render if isinstance(render, dict) else {}
    out: set[int] = set()
    for layer in render.get("layers") or []:
        if isinstance(layer, dict):
            out.update(ref for ref in layer_asset_refs(layer) if isinstance(ref, int))
    for insert in render.get("inserts") or []:
        out.update(_ints(insert.get("asset_id") if isinstance(insert, dict) else None))
    end = render.get("end_screen")
    if isinstance(end, dict):
        out.update(_ints(end.get("asset_id"), end.get("svg_asset_id")))
    return out


def _ints(*values: Any) -> list[int]:
    out = []
    for value in values:
        try:
            out.append(int(value))
        except (TypeError, ValueError):
            pass
    return out


def project_title(store: Any, project_id: int | None) -> str:
    if project_id is None:
        return "без проекта"
    row = store.db.query_one("SELECT name FROM projects WHERE id = ?", (int(project_id),))
    return f"«{row['name']}» (#{int(project_id)})" if row else f"#{int(project_id)}"


def refusal(store: Any, asset: dict, project_id: int | None, what: str = "клип") -> str:
    """Текст отказа: чей файл, в каком проекте работа (``what``: клип, мир…) и что делать вместо этого."""
    owner = project_title(store, asset.get("project_id"))
    where = f"{what} в проекте {project_title(store, project_id)}" if project_id is not None else f"{what} без проекта"
    return (f"файл #{asset['id']} «{asset.get('label') or ''}» из проекта {owner}, а {where}: чужие файлы не брать. "
            "Возьми общий или файл этого проекта (list_assets) либо попроси владельца перенести файл в общие")


def visible_assets(store: Any, project_id: int | None) -> list[dict]:
    """Файлы, которые можно предлагать в контексте проекта: общие и его собственные."""
    return store.list_montage_assets(visible_to=() if project_id is None else (int(project_id),))


def assets_for_clip(store: Any, clip_plan_id: int, in_clip: Iterable[int] = ()) -> list[dict]:
    """Файлы для редактора клипа: видимые плюс чужие, что в нём уже стоят (с ``in_clip_only``: их показывает
    только слой, который на них стоит, а в списках выбора их нет)."""
    project_id = clip_project(store, clip_plan_id)
    out = visible_assets(store, project_id)
    have = {int(a["id"]) for a in out}
    extra = []
    for asset_id in set(in_clip) - have:
        try:
            extra.append({**store.get_montage_asset(int(asset_id)), "in_clip_only": True})
        except KeyError:
            continue
    return sorted(extra, key=lambda a: -int(a["id"])) + out


def new_foreign_assets(store: Any, clip_plan_id: int, asset_ids: Iterable[Any], render: Any = None) -> list[dict]:
    """Файлы из ``asset_ids``, которые клипу брать нельзя: чужой проект и ещё не стоят в клипе. ``render`` —
    нынешние настройки клипа (нет — из базы). Несуществующие id — не наше дело, о них скажет проверка слоёв."""
    wanted = {i for value in asset_ids for i in _ints(value)}
    if not wanted:
        return []
    project_id = clip_project(store, clip_plan_id)
    if render is None:
        render = store.get_clip_plan(int(clip_plan_id), include_segments=False).get("render_settings") or {}
    present = render_asset_ids(render)
    out = []
    for asset_id in sorted(wanted - present):
        try:
            asset = store.get_montage_asset(asset_id)
        except KeyError:
            continue
        if not allowed(asset, project_id):
            out.append(asset)
    return out
