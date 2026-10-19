"""HTTP-ручки джамп-катов (app/cuts.py).

* ``POST /api/clip-plans/{id}/cuts/suggest`` — что вырезать (паузы, слова-паразиты,
  повторы) по сохранённым пословным субтитрам клипа. Ничего не сохраняет.
* ``POST /api/clip-plans/{id}/cuts/apply`` — сохранить вырезы в настройки клипа
  (``render.cuts``) со снимком для отмены (Ctrl+Z).

Роутер собирается функцией, как ``app/motion_api.py``: ``main.py`` передаёт
зависимость авторизации и доступ к хранилищу (лямбдой — тесты подменяют
``app.main.store``).
"""

# Без from __future__ import annotations: FastAPI читает аннотации вида
# _auth: auth_dep в момент объявления, а auth_dep — локальная переменная.
from typing import Any, Callable, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.cuts import check_cuts_for_plan, cuts_total, plan_duration, suggest_for_plan, validate_cuts


class CutsSuggestPayload(BaseModel):
    mode: Literal["pauses", "fillers", "both"] = "both"
    min_pause: float = Field(default=0.45, ge=0.1, le=5.0, description="пауза длиннее этого — вырезается")
    keep_pad: float = Field(default=0.12, ge=0.0, le=1.0, description="сколько тишины оставить у каждого слова")


class CutsApplyPayload(BaseModel):
    cuts: list[Any] = Field(default_factory=list, description="[{start, end}] в секундах клипа до вырезов; [] — вернуть всё")


def build_cuts_router(auth_dep: Any, get_store: Callable[[], Any]) -> APIRouter:
    router = APIRouter(tags=["Cuts"])

    @router.post(
        "/api/clip-plans/{clip_plan_id}/cuts/suggest",
        summary="✂ Найти паузы и слова-паразиты (по сохранённым словам субтитров, ничего не сохраняет)",
    )
    def api_suggest_cuts(
        clip_plan_id: int,
        _auth: auth_dep,  # type: ignore[valid-type]
        payload: CutsSuggestPayload | None = None,
    ) -> dict:
        body = payload or CutsSuggestPayload()
        try:
            return suggest_for_plan(get_store(), clip_plan_id, body.mode, body.min_pause, body.keep_pad)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post(
        "/api/clip-plans/{clip_plan_id}/cuts/apply",
        summary="✂ Сохранить вырезы клипа (со снимком для отмены)",
    )
    def api_apply_cuts(
        clip_plan_id: int,
        payload: CutsApplyPayload,
        _auth: auth_dep,  # type: ignore[valid-type]
    ) -> dict:
        from app.clip_history import snapshot

        store = get_store()
        problems = validate_cuts(payload.cuts)
        if problems:
            raise HTTPException(status_code=422, detail={"problems": problems})
        try:
            plan = store.get_clip_plan(clip_plan_id)
            # Проверка по самому клипу (как его разложит рендер): за концом
            # клипа или на весь клип — 400 сразу, а не упавший рендер потом.
            cuts, problems = check_cuts_for_plan(plan, payload.cuts)
            if problems:
                raise HTTPException(status_code=400, detail="; ".join(problems))
            snapshot(store, clip_plan_id, "джамп-каты" if cuts else "вернуть вырезанное")
            settings = {**(plan.get("render_settings") or {}), "cuts": cuts}
            fresh = store.set_clip_plan_render_settings(clip_plan_id, settings)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        before = plan_duration(plan)
        saved = cuts_total(cuts)
        return {
            "cuts": cuts,
            "saved_sec": saved,
            "before_sec": before,
            "after_sec": round(max(0.0, before - saved), 3),
            "render_settings": fresh.get("render_settings"),
        }

    return router
