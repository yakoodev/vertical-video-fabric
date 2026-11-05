"""REST концовки-баннера и SVG → webm (app/end_screen.py, app/svg_webm.py).

* ``GET /api/end-screen/templates`` — встроенные шаблоны баннера и пределы полей.
* ``POST /api/clip-plans/{id}/end-screen`` — поставить/заменить концовку (поля как у MCP
  ``set_end_screen``; ``remove: true`` — снять); ``DELETE`` — снять.
* ``POST /api/svg-to-webm`` — SVG (текст ``svg`` или ``asset_id`` файла библиотеки) → webm с альфой
  в «Файлы для монтажа» (кэш по хешу: тот же SVG — тот же файл).
"""

# Без from __future__ import annotations: FastAPI читает аннотации _auth: auth_dep при объявлении.
from typing import Any, Callable

from fastapi import APIRouter, Body, HTTPException


def build_end_screen_router(auth_dep: Any, get_store: Callable[[], Any]) -> APIRouter:
    router = APIRouter(tags=["Motion"])

    @router.get("/api/end-screen/templates", summary="Шаблоны концовки-баннера")
    def api_end_screen_templates(_auth: auth_dep) -> dict:  # type: ignore[valid-type]
        from app import end_screen as es

        return {
            "templates": [{"id": k, "description": v} for k, v in es.TEMPLATES.items()],
            "default": es.TEMPLATE_DEFAULT,
            "platforms": list(es.PLATFORMS),
            "limits": {"duration": [es.DURATION_MIN, es.DURATION_MAX, es.DURATION_DEFAULT],
                       "blur": [0, es.BLUR_MAX, es.BLUR_DEFAULT], "dim": [0, es.DIM_MAX, es.DIM_DEFAULT],
                       "ramp": [es.RAMP_MIN, es.RAMP_MAX, es.RAMP_DEFAULT]},
        }

    def _set(clip_plan_id: int, body: dict, remove: bool) -> dict:
        from app.agent_tools import END_SCREEN_FIELDS
        from app.end_screen import set_end_screen
        from app.svg_webm import SvgWebmError

        fields = {k: body[k] for k in END_SCREEN_FIELDS if k in body}
        brand_id = body.get("brand_id")
        try:
            return set_end_screen(get_store(), clip_plan_id, fields, brand_id=int(brand_id) if brand_id else None,
                                  remove=remove or body.get("remove") is True)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except SvgWebmError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @router.post("/api/clip-plans/{clip_plan_id}/end-screen", summary="Концовка-баннер: размытие + баннер")
    def api_set_end_screen(clip_plan_id: int, _auth: auth_dep, body: dict = Body(default_factory=dict)) -> dict:  # type: ignore[valid-type]
        return _set(clip_plan_id, body or {}, False)

    @router.delete("/api/clip-plans/{clip_plan_id}/end-screen", summary="Снять концовку-баннер")
    def api_remove_end_screen(clip_plan_id: int, _auth: auth_dep) -> dict:  # type: ignore[valid-type]
        return _set(clip_plan_id, {}, True)

    @router.post("/api/svg-to-webm", summary="SVG с анимацией → webm с альфой в «Файлы для монтажа»")
    def api_svg_to_webm(_auth: auth_dep, body: dict = Body(...)) -> dict:  # type: ignore[valid-type]
        from app.svg_webm import SvgWebmError, asset_svg_text, svg_to_asset

        store = get_store()
        try:
            svg = asset_svg_text(store, body["asset_id"]) if body.get("asset_id") else body.get("svg")
            asset, info = svg_to_asset(store, svg, body, label=str(body.get("label") or "SVG-анимация"),
                                       project_id=int(body["project_id"]) if body.get("project_id") else None)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except SvgWebmError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {**asset, "conversion": {k: info.get(k) for k in ("frames", "fps", "duration", "width", "height", "cached")}}

    return router
