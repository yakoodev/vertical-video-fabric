"""HTTP-ручки выпусков (app/releases.py): страница шагов и промо из «Новое видео».

* ``GET /api/releases/{key}`` — выпуск: шаги, карточка с суммой, клипы, что будет при пересборке;
* ``POST /api/projects/{id}/promo`` {title, spec} — промо приложения: холст и заготовка ``build_app_promo``.

Список выпусков проекта отдаёт ``GET /api/nav/videos`` (поле ``generated``). Создание и действия у остальных типов —
прежние ручки объяснялок, историй и ИИ-видео: новых конвейеров здесь нет.
"""

from typing import Any, Callable

from fastapi import APIRouter, Body, HTTPException

from app import releases as R


def build_releases_router(auth_dep: Any, get_store: Callable[[], Any]) -> APIRouter:
    router = APIRouter(tags=["Projects"])

    @router.get("/api/releases/{key}", summary="Выпуск: шаги, цена, карточка, клипы")
    def api_release(key: str, _auth: auth_dep) -> dict:
        try:
            return R.view(get_store(), key)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Такого выпуска нет") from exc

    @router.post("/api/projects/{project_id}/promo", summary="Промо приложения: холст и заготовка")
    def api_promo(project_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        from app import agent_tools as T
        from app import clip_groups as CG
        from app import projects as P
        from app.canvas import create_canvas_clip

        store = get_store()
        title = " ".join(str(payload.get("title") or "").split())[:100]
        if not title:
            raise HTTPException(status_code=400, detail="Нужно название промо")
        spec = payload.get("spec")
        if not isinstance(spec, dict):
            raise HTTPException(status_code=400, detail="Нужно описание промо: хук, сцены и финал")
        try:
            P.get_project(store, project_id, include_sources=False)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Проекта нет") from exc
        plan = create_canvas_clip(store, 30, spec.get("background"), title, f"Промо приложения: {title}")
        plan_id = int(plan["id"])
        CG.hold_assembly(store, plan_id)  # заготовка не встала — лишней строки в проекте не будет
        P.assign_plan_project(store, plan_id, project_id)
        result, ok = T.run_tool(T.ToolContext(store=store, clip_plan_id=plan_id), "build_app_promo", {"spec": spec})
        if not ok:
            problems = (result or {}).get("problems") or []
            text = str((result or {}).get("error") or "Заготовка промо не собралась")
            raise HTTPException(status_code=400, detail="; ".join([text, *[str(p) for p in problems[:4]]]))
        CG.finish_assembly(store, plan_id, None)
        return {"clip_id": plan_id, "to": f"/clips/{plan_id}", "duration": (result or {}).get("duration"),
                "notes": (result or {}).get("warnings") or []}

    return router
