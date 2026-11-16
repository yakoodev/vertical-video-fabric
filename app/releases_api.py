"""HTTP-ручки выпусков (app/releases.py): страница шагов и промо из «Новое видео».

* ``GET /api/releases/{key}`` — выпуск: шаги, карточка с суммой, клипы, что будет при пересборке;
* ``POST /api/projects/{id}/promo`` {title, spec} — промо приложения: холст и заготовка ``build_app_promo``;
* ``POST /api/projects/{id}/frag-movie`` {title, source_ids, track_id | music_url, lyrics?, lang?, notes?} — мувик под
  песню: переписка чата проекта с плейбуком frag-movie и первым заданием (app/frag_movie.py);
* ``POST /api/projects/{id}/amv`` {title, source_ids, track_id | music_url, notes?, aspect?} — AMV: работа
  (``<runtime>/amv/<amv_id>``), переписка чата проекта с плейбуком amv и первым заданием (app/amv.py).

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

    @router.post("/api/projects/{project_id}/frag-movie", summary="Мувик под песню: чат проекта с плейбуком и заданием")
    def api_frag_movie(project_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        from app import frag_movie as F
        from app import project_chat as C

        store = get_store()
        try:
            source_ids = [int(x) for x in payload.get("source_ids") or []]
            track_id = int(payload["track_id"]) if payload.get("track_id") not in (None, "", 0) else None
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="source_ids и track_id — числа") from exc
        try:
            return F.start(store, project_id, title=str(payload.get("title") or ""), source_ids=source_ids,
                           track_id=track_id, music_url=str(payload.get("music_url") or ""),
                           lyrics=str(payload.get("lyrics") or ""), lang=str(payload.get("lang") or ""),
                           notes=str(payload.get("notes") or ""), run_agent=payload.get("run_agent") is not False)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc).strip("'\"")) from exc
        except C.ChatBusy as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/api/projects/{project_id}/amv", summary="AMV: работа, чат проекта с плейбуком amv и заданием")
    def api_amv(project_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        from app import amv as AMV
        from app import project_chat as C

        store = get_store()
        try:
            source_ids = [int(x) for x in payload.get("source_ids") or []]
            track_id = int(payload["track_id"]) if payload.get("track_id") not in (None, "", 0) else None
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="source_ids и track_id — числа") from exc
        try:
            return AMV.start(store, project_id, title=str(payload.get("title") or ""), source_ids=source_ids,
                             track_id=track_id, music_url=str(payload.get("music_url") or ""),
                             notes=str(payload.get("notes") or ""), aspect=str(payload.get("aspect") or "9:16"),
                             run_agent=payload.get("run_agent") is not False)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc).strip("'\"")) from exc
        except C.ChatBusy as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router
