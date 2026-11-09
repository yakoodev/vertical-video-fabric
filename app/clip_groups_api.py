"""Ручки клипа как единицы работы (app/clip_groups.py): сетка, экран /clips/:id, версии, откат.

Старые ``/api/clip-plans`` и ``/api/clips`` остаются как были: это планы и рендеры.

* ``GET /api/clip-cards?source_id=N`` или ``?project_id=N`` — сетка, один элемент на клип
* ``GET /api/clip-view/{id}`` — экран клипа; ``id`` — любой план группы
* ``PUT /api/clip-view/{id}/meta`` — заголовок, описание, хэштеги
* ``POST /api/clip-view/{id}/rollback`` — вернуть правки монтажа версии
* ``POST /api/clip-view/{id}/new-version`` — копия монтажа новым планом в той же группе
* ``POST /api/clip-view/{id}/merge``, ``/split`` — объединить и разъединить
* ``GET /api/clip-renders`` — «Готовые ролики» страницами (``GET /api/clips`` отдаёт только 200 новых)
"""

from typing import Any, Callable, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app import clip_groups as G
from app.store_clips import _PUBLISHED_SQL


class ClipMetaPayload(BaseModel):
    title: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=5000)
    hashtags: list[str] = Field(default_factory=list)


class RollbackPayload(BaseModel):
    render_id: int | None = None
    snapshot_id: int | None = None


class MergePayload(BaseModel):
    other_id: int


class SplitPayload(BaseModel):
    plan_id: int


# Фильтры «Готовых роликов» — те же, что были на клиенте (frontend/src/pages/ReadyClipsPage.tsx).
_PAGE_FILTERS: dict[str, str] = {
    "all": "",
    "ready": "c.status = 'succeeded'",
    "vertical": "c.status = 'succeeded' AND c.height >= c.width",
    "horizontal": "c.status = 'succeeded' AND c.width > c.height",
    "error": "c.status IN ('failed', 'error')",
    # Выложен сервисом или отмечен руками (тот же счёт, что у карточки, app/store_clips._PUBLISHED_SQL).
    "published": f"{_PUBLISHED_SQL} > 0",
}


def build_clip_groups_router(auth_dep: Any, get_store: Callable[[], Any]) -> APIRouter:
    router = APIRouter(tags=["Clip groups"])

    @router.get("/api/clip-cards", summary="Клипы видео или проекта: один элемент на клип, а не на план")
    def api_clip_cards(
        _auth: auth_dep,  # type: ignore[valid-type]
        source_id: int | None = Query(default=None),
        project_id: int | None = Query(default=None),
    ) -> dict:
        store = get_store()
        if (source_id is None) == (project_id is None):
            raise HTTPException(status_code=400, detail="нужен source_id или project_id")
        if source_id is not None:
            try:
                store.get_source(source_id)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            return G.source_cards(store, source_id)
        if not store.db.query_one("SELECT id FROM projects WHERE id = ?", (project_id,)):
            raise HTTPException(status_code=404, detail="Проекта нет")
        return G.project_cards(store, int(project_id))

    @router.get("/api/clip-view/{clip_plan_id}", summary="Экран клипа одним ответом: версии, сегменты, соседи, текст")
    def api_clip_view(clip_plan_id: int, _auth: auth_dep) -> dict:  # type: ignore[valid-type]
        try:
            return G.clip_view(get_store(), clip_plan_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.put("/api/clip-view/{clip_plan_id}/meta", summary="Заголовок, описание и хэштеги клипа")
    def api_clip_meta(clip_plan_id: int, payload: ClipMetaPayload, _auth: auth_dep) -> dict:  # type: ignore[valid-type]
        try:
            return G.save_meta(get_store(), clip_plan_id, payload.title, payload.description, payload.hashtags)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post(
        "/api/clip-view/{clip_plan_id}/rollback",
        summary="Вернуть правки монтажа выбранной версии; рендеры не удаляются, новый рендер ставит кнопка",
    )
    def api_clip_rollback(clip_plan_id: int, payload: RollbackPayload, _auth: auth_dep) -> dict:  # type: ignore[valid-type]
        from app.clip_spec import SpecError

        try:
            return G.rollback(get_store(), clip_plan_id, render_id=payload.render_id, snapshot_id=payload.snapshot_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except SpecError as exc:
            raise HTTPException(status_code=422, detail={"problems": exc.problems}) from exc

    @router.post("/api/clip-view/{clip_plan_id}/new-version", summary="Новая версия монтажа: копия новым планом той же группы")
    def api_clip_new_version(clip_plan_id: int, _auth: auth_dep) -> dict:  # type: ignore[valid-type]
        from app.clip_spec import SpecError

        try:
            return G.new_version(get_store(), clip_plan_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except SpecError as exc:
            raise HTTPException(status_code=422, detail={"problems": exc.problems}) from exc

    @router.post("/api/clip-view/{clip_plan_id}/merge", summary="Объединить два клипа в один")
    def api_clip_merge(clip_plan_id: int, payload: MergePayload, _auth: auth_dep) -> dict:  # type: ignore[valid-type]
        try:
            return G.merge_clips(get_store(), clip_plan_id, payload.other_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/api/clip-view/{clip_plan_id}/split", summary="Отделить план в отдельный клип")
    def api_clip_split(clip_plan_id: int, payload: SplitPayload, _auth: auth_dep) -> dict:  # type: ignore[valid-type]
        store = get_store()
        try:
            members = {int(m["id"]) for m in G.group_members(store, clip_plan_id)}
            if payload.plan_id not in members:
                raise HTTPException(status_code=400, detail="этого плана нет в клипе")
            return G.split_plan(store, payload.plan_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/clip-renders", tags=["Render"], summary="Готовые ролики страницами: все, а не 200 новых")
    def api_clips_page(
        _auth: auth_dep,  # type: ignore[valid-type]
        filter: Literal["all", "ready", "vertical", "horizontal", "error", "published"] = "all",  # noqa: A002
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=60, ge=1, le=200),
        clip_plan_id: int | None = Query(default=None, description="Только рендеры этого клипа"),
    ) -> dict:
        store = get_store()
        plan_sql = "c.clip_plan_id = ?" if clip_plan_id is not None else ""
        args = (clip_plan_id,) if clip_plan_id is not None else ()

        def where_of(condition: str) -> str:
            parts = [part for part in (condition, plan_sql) if part]
            return f"WHERE {' AND '.join(parts)}" if parts else ""

        counts = {
            key: int(store.db.query_one(f"SELECT COUNT(*) AS n FROM clips c {where_of(sql)}", args)["n"])
            for key, sql in _PAGE_FILTERS.items()
        }
        return {
            "items": store.list_clips_page(offset=offset, limit=limit, where=where_of(_PAGE_FILTERS[filter]), args=args),
            "total": counts[filter],
            "counts": counts,
            "offset": offset,
            "limit": limit,
        }

    return router
