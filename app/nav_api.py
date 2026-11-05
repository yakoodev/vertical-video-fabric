"""Лёгкие ручки навигации (app/nav.py): боковая панель и лестница Проект → Видео → Клип.

* ``GET /api/nav`` — проекты со счётчиком видео, бейдж «Входящие»
* ``GET /api/nav/videos?project_id=N`` — видео проекта списком; без ``project_id`` — вне проектов
* ``GET /api/projects/{id}/clips`` — клипы проекта (карточки вкладки «Клипы»)
"""

from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Query

from app import nav


def build_nav_router(auth_dep: Any, get_store: Callable[[], Any]) -> APIRouter:
    router = APIRouter(tags=["Projects"])

    @router.get("/api/nav", summary="Счётчики боковой панели: проекты, видео, «Входящие»")
    def api_nav(_auth: auth_dep) -> dict:
        return nav.nav_counts(get_store())

    @router.get("/api/nav/videos", summary="Видео проекта лёгким списком (без project_id — вне проектов)")
    def api_nav_videos(_auth: auth_dep, project_id: int | None = Query(default=None)) -> dict:
        return nav.project_videos(get_store(), project_id)

    @router.get("/api/projects/{project_id}/clips", summary="Клипы проекта")
    def api_project_clips(project_id: int, _auth: auth_dep, limit: int = Query(default=300, ge=1, le=1000)) -> list[dict]:
        store = get_store()
        if not store.db.query_one("SELECT id FROM projects WHERE id = ?", (project_id,)):
            raise HTTPException(status_code=404, detail="Проекта нет")
        return store.list_clips(project_id=project_id, limit=limit)

    return router
