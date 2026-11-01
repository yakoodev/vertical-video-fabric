"""HTTP-ручка линта качества клипа (app/quality_lint.py) — то же, что MCP ``lint_clip``.

* ``GET /api/clip-plans/{id}/lint?checks=ui_zones,reading&platforms=tiktok,reels&media=auto`` —
  находки (только предупреждения, ничего не сохраняет). Редактор показывает их в панели «Проверка».

Роутер собирается функцией, как ``app/cuts_api.py``: ``main.py`` передаёт зависимость
авторизации и доступ к хранилищу (лямбдой — тесты подменяют ``app.main.store``).
"""

# Без from __future__ import annotations: FastAPI читает аннотации вида
# _auth: auth_dep в момент объявления, а auth_dep — локальная переменная.
from typing import Any, Callable

from fastapi import APIRouter, HTTPException

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

    return router
