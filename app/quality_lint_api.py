"""HTTP-ручка линта качества клипа (app/quality_lint.py) — то же, что MCP ``lint_clip``.

* ``GET /api/clip-plans/{id}/lint?checks=ui_zones,reading&platforms=tiktok,reels&media=auto`` —
  находки (только предупреждения, ничего не сохраняет). Редактор показывает их в панели «Проверка».
* ``GET/PUT /api/clip-plans/{id}/lint/assertions`` — проверки-утверждения клипа (app/quality_lint_assert.py):
  ``{"assertions": ["X появился к 2.0", {"before": ["A", "B"]}]}``; каждый lint сверяет их сам.

Роутер собирается функцией, как ``app/cuts_api.py``: ``main.py`` передаёт зависимость
авторизации и доступ к хранилищу (лямбдой — тесты подменяют ``app.main.store``).
"""

# Без from __future__ import annotations: FastAPI читает аннотации вида
# _auth: auth_dep в момент объявления, а auth_dep — локальная переменная.
from typing import Any, Callable

from fastapi import APIRouter, Body, HTTPException

from app.quality_lint import lint_clip


def build_lint_router(auth_dep: Any, get_store: Callable[[], Any]) -> APIRouter:
    router = APIRouter(tags=["Clip plans"])

    @router.get(
        "/api/clip-plans/{clip_plan_id}/lint",
        summary="🔎 Проверка клипа перед сдачей: зоны интерфейса площадок, темп чтения, вспышки, луп",
    )
    def api_lint_clip(
        clip_plan_id: int,
        _auth: auth_dep,  # type: ignore[valid-type]
        checks: str = "",
        platforms: str = "",
        media: str = "auto",
    ) -> dict:
        try:
            return lint_clip(get_store(), clip_plan_id, checks=checks or None, platforms=platforms or None,
                             media=media or "auto")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/clip-plans/{clip_plan_id}/lint/assertions", summary="Проверки-утверждения клипа")
    def api_get_assertions(clip_plan_id: int, _auth: auth_dep) -> dict:  # type: ignore[valid-type]
        from app.quality_lint_assert import load_assertions

        try:
            get_store().get_clip_plan(clip_plan_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"assertions": load_assertions(clip_plan_id)}

    @router.put("/api/clip-plans/{clip_plan_id}/lint/assertions", summary="Записать проверки-утверждения клипа")
    def api_put_assertions(
        clip_plan_id: int,
        _auth: auth_dep,  # type: ignore[valid-type]
        body: dict = Body(...),
    ) -> dict:
        from app.quality_lint_assert import MAX_ASSERTIONS, parse_assertion, save_assertions

        try:
            get_store().get_clip_plan(clip_plan_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        items = body.get("assertions")
        if not isinstance(items, list) or len(items) > MAX_ASSERTIONS:
            raise HTTPException(status_code=400, detail=f"assertions — список до {MAX_ASSERTIONS}")
        bad = [str(x)[:60] for x in items if parse_assertion(x) is None]
        if bad:
            raise HTTPException(status_code=400, detail="не понял: " + "; ".join(bad[:5]))
        save_assertions(clip_plan_id, items)
        return {"assertions": items}

    return router
