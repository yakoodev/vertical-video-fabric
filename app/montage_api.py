"""ИИ-монтаж: предложить и применить, откатить, пакет по источнику, выбор лучших моментов.

Роутер собирается функцией, как ``app/cuts_api.py``: ``main.py`` передаёт зависимость авторизации и доступ к хранилищу
и сервисам лямбдами (тесты подменяют ``app.main.store`` и другие имена)."""

# Без from __future__ import annotations: FastAPI читает аннотации вида
# _auth: auth_dep в момент объявления, а auth_dep — локальная переменная.
from typing import Any, Callable, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.agent_settings_api import refuse_agent_drafts
from app.ai.montage import apply_montage, latest_job_for_source, propose_montage, start_montage_job, undo_montage
from app.ai.pick import pick_best
from app.api_common import _default_subtitle_profile_id
from app.auth import is_agent_request
from app.clip_history import snapshot as snapshot_clip
from app.clip_settings import settings_to_render_kwargs
from app.clip_spec import SpecError
from app.settings import settings


class AiMontageRequest(BaseModel):
    goal: str = Field(default="", max_length=500, description="пожелание: «короче», «больше мемности»…")


class AiMontageApplyRequest(BaseModel):
    spec: dict


class AiMontageBatchRequest(BaseModel):
    clip_plan_ids: list[int] = Field(default_factory=list, description="пусто = все ★ избранные (не скрытые)")
    goal: str = Field(default="", max_length=500)
    render: bool = False
    # Второй проход: моушн-монтаж Gemini-агентом (хук, субтитры, панчлайны, звук, мемы).
    polish: bool = True


class AiPickRequest(BaseModel):
    count: int = Field(default=10, ge=1, le=50)
    goal: str = Field(default="", max_length=500)
    # Формат будущих клипов (app/formats.py): нет — настройка analysis_format, иначе 9:16.
    format: Literal["9:16", "1:1", "4:5", "16:9"] | None = None
    # Chain: start 🤖 ИИ-монтаж on the picks right away (optionally rendering).
    montage: bool = False
    render: bool = False
    polish: bool = True  # моушн-монтаж Gemini-агентом после склейки


def build_montage_router(
    auth_dep: Any,
    get_store: Callable[[], Any],
    get_clip_render_service: Callable[[], Any],
) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/api/clip-plans/{clip_plan_id}/ai-montage",
        tags=["Clip file"],
        summary="🤖 ИИ-монтаж: propose a re-edit of the clip (nothing is applied)",
    )
    def api_clip_plan_ai_montage(clip_plan_id: int, _auth: auth_dep, payload: AiMontageRequest | None = None) -> dict:
        """Returns ``{spec, diff, rationale, attempts}``; apply with PUT …/spec."""
        try:
            return propose_montage(get_store(), clip_plan_id, goal=(payload.goal if payload else ""))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.post(
        "/api/clip-plans/{clip_plan_id}/ai-montage/apply",
        tags=["Clip file"],
        summary="Apply an AI re-edit (the pre-AI clip file is kept for undo)",
    )
    def api_clip_plan_ai_montage_apply(clip_plan_id: int, payload: AiMontageApplyRequest, _auth: auth_dep) -> dict:
        try:
            snapshot_clip(get_store(), clip_plan_id, "ИИ-монтаж")
            spec, changes = apply_montage(get_store(), clip_plan_id, payload.spec)
        except SpecError as exc:
            raise HTTPException(status_code=422, detail={"problems": exc.problems}) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"spec": spec, "changes": changes}

    @router.post(
        "/api/clip-plans/{clip_plan_id}/ai-montage/undo",
        tags=["Clip file"],
        summary="↩ Undo the AI re-edit: back to the clip as it was before",
    )
    def api_clip_plan_ai_montage_undo(clip_plan_id: int, _auth: auth_dep) -> dict:
        try:
            spec, changes = undo_montage(get_store(), clip_plan_id)
        except SpecError as exc:
            raise HTTPException(status_code=422, detail={"problems": exc.problems}) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"spec": spec, "changes": changes}

    def _render_plan_own_settings(clip_plan_id: int) -> dict:
        """Render a clip with its own saved settings (what the clip file says)."""
        plan = get_store().get_clip_plan(clip_plan_id, include_segments=False)
        kwargs = (
            settings_to_render_kwargs(plan["render_settings"], _default_subtitle_profile_id(get_store()))
            if plan.get("render_settings")
            else {}
        )
        return get_clip_render_service().render_clip_plan(clip_plan_id, **kwargs)

    @router.post(
        "/api/sources/{source_id}/ai-montage-batch",
        tags=["Clip file"],
        summary="🤖 ИИ-монтаж for many clips in the background (applies, optionally renders)",
    )
    def api_source_ai_montage_batch(source_id: int, payload: AiMontageBatchRequest, request: Request, _auth: auth_dep) -> dict:
        from app.stream_questions_api import refuse_agent_montage

        refuse_agent_montage(request, get_store(), source_id)
        try:
            plans = get_store().list_clip_plans(source_id=source_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        own = {p["id"] for p in plans}
        ids = [i for i in payload.clip_plan_ids if i in own] or [
            p["id"] for p in plans if p.get("favorite") and not p.get("hidden")
        ]
        if not ids:
            raise HTTPException(status_code=400, detail="нет клипов: отметьте ★ избранные или передайте clip_plan_ids")
        refuse_agent_drafts(request, get_store(), ids)
        running = latest_job_for_source(source_id)
        if running and running.get("status") == "running":
            raise HTTPException(status_code=409, detail="ИИ-монтаж этого проекта уже идёт")
        return start_montage_job(
            get_store(), source_id, ids, goal=payload.goal, render=_render_plan_own_settings if payload.render else None,
            polish=payload.polish and bool(settings.gemini_api_key),
        )

    @router.post(
        "/api/sources/{source_id}/ai-pick",
        tags=["Clip file"],
        summary="✨ ИИ выбирает лучшие: star the N most shareable moments (score + reason)",
    )
    def api_source_ai_pick(source_id: int, payload: AiPickRequest, request: Request, _auth: auth_dep) -> dict:
        from app.stream_questions_api import refuse_agent_montage

        refuse_agent_montage(request, get_store(), source_id)
        try:
            get_store().get_source(source_id)
            result = pick_best(get_store(), source_id, count=payload.count, goal=payload.goal,
                               **({"output_format": payload.format} if payload.format else {}))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        # Выбор ИИ по кнопке владельца — его выбор. Тот же запрос агента только ставит ★: монтаж ждёт владельца.
        from app import agent_settings as agent_settings_mod

        picked_ids = [p["id"] for p in result["picks"]]
        refused = agent_settings_mod.drafts_refusal(get_store(), picked_ids) if is_agent_request(request) else None
        if not is_agent_request(request):
            agent_settings_mod.set_picked_many(get_store(), picked_ids)
        if refused and payload.montage:
            result["montage_refused"] = refused["error"]
        elif payload.montage:
            running = latest_job_for_source(source_id)
            if not (running and running.get("status") == "running"):
                result["montage_job"] = start_montage_job(
                    get_store(),
                    source_id,
                    [p["id"] for p in result["picks"]],
                    goal=payload.goal,
                    render=_render_plan_own_settings if payload.render else None,
                    polish=payload.polish and bool(settings.gemini_api_key),
                )
        return result

    @router.get(
        "/api/sources/{source_id}/ai-montage-batch",
        tags=["Clip file"],
        summary="Progress of the latest 🤖 ИИ-монтаж batch of this project (null if none)",
    )
    def api_source_ai_montage_batch_status(source_id: int, _auth: auth_dep) -> dict | None:
        return latest_job_for_source(source_id)

    return router
