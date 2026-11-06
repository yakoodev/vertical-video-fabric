"""Правки клипа кнопками редактора: скорость и цвет куска, доли, автомонтаж, обложка, компиляция.

Роутер собирается функцией, как ``app/cuts_api.py``: ``main.py`` передаёт зависимость авторизации и доступ к хранилищу
и сервисам лямбдами (тесты подменяют ``app.main.store`` и другие имена)."""

# Без from __future__ import annotations: FastAPI читает аннотации вида
# _auth: auth_dep в момент объявления, а auth_dep — локальная переменная.
from typing import Any, Callable, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.agent_settings_api import refuse_agent_drafts
from app.clip_history import snapshot as snapshot_clip


class SegmentSpeedRequest(BaseModel):
    piece: int = Field(ge=0, description="номер куска с 0")
    speed: float = Field(default=1.0, ge=0.25, le=4.0, description="0.5 — вдвое медленнее, 2 — вдвое быстрее")
    reverse: bool = False
    audio: Literal["tempo", "pitch", "mute", "reverse"] | None = None


class PieceColorRequest(BaseModel):
    piece: int = Field(ge=0, description="номер куска с 0")
    exposure: float | None = Field(default=None, ge=-2, le=2, description="ступени EV")
    contrast: float | None = Field(default=None, ge=0.5, le=1.5)
    saturation: float | None = Field(default=None, ge=0, le=2)
    temperature: float | None = Field(default=None, ge=-1, le=1, description="+ теплее, − холоднее")
    tint: float | None = Field(default=None, ge=-1, le=1, description="+ пурпурнее, − зеленее")
    reset: bool = False


class PieceColorMatchRequest(BaseModel):
    reference: int | None = Field(default=None, ge=0, description="кусок-образец; нет — самый длинный")
    pieces: list[int] | None = None
    strength: float = Field(default=1.0, ge=0, le=1)


class SnapBeatsRequest(BaseModel):
    targets: list[Literal["layers", "keys", "camera", "joins", "all"]] = Field(default_factory=lambda: ["layers"])
    tolerance: float = Field(default=0.15, ge=0.01, le=0.5)
    grid: Literal["beat", "downbeat", "half"] = "beat"
    source: Literal["auto", "music", "audio"] = "auto"
    bpm: float | None = Field(default=None, ge=40, le=240)
    first_beat: float | None = None
    first_downbeat: float | None = None
    dry_run: bool = False


class CoverComposeRequest(BaseModel):
    t: float = Field(ge=0, description="секунда готового клипа — кадр обложки")
    title: str = Field(min_length=1, max_length=80)
    emoji: str = Field(default="", max_length=16)
    style: str = Field(default="yellow", max_length=20, description="yellow | fire | white | plate")
    y: float = Field(default=0.2, ge=0.05, le=0.9)


class CompilationRequest(BaseModel):
    plan_ids: list[int] = Field(min_length=2, max_length=12)
    title: str = Field(default="", max_length=100)
    countdown: bool = Field(default=True, description="#N … #1 — лучший последним")
    caption_style: str = Field(default="hormozi", max_length=40)


class AutoEditRequest(BaseModel):
    style: str = Field(default="hormozi", max_length=40, description="стиль анимированных субтитров")
    keep_layers: bool = Field(default=False, description="оставить свои слои, заменить только авто-слои")


def build_clip_edit_router(auth_dep: Any, get_store: Callable[[], Any]) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/api/clip-plans/{clip_plan_id}/segment-speed",
        tags=["Render"],
        summary="⏩ Скорость и реверс куска основы (slow-mo, ускорение, «перемотка»)",
    )
    def api_clip_plan_segment_speed(clip_plan_id: int, payload: SegmentSpeedRequest, _auth: auth_dep) -> dict:
        """Тот же инструмент, что у агентов (set_segment_speed, app/piece_speed.py): слои, камера,
        вставки и субтитры едут за новой длиной куска; шаг отмены — один."""
        from app import agent_tools

        try:
            get_store().get_clip_plan(clip_plan_id, include_segments=False)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        ctx = agent_tools.ToolContext(
            store=get_store(), clip_plan_id=clip_plan_id,
            on_first_change=lambda: snapshot_clip(get_store(), clip_plan_id, "скорость куска"),
        )
        result, ok = agent_tools.run_tool(ctx, "set_segment_speed", payload.model_dump(exclude_none=True))
        if not ok:
            raise HTTPException(status_code=400, detail=str(result.get("error") or "скорость не принята"))
        return result

    def _run_clip_tool(clip_plan_id: int, name: str, args: dict, label: str, fail: str) -> dict:
        """Инструмент агента из редактора: тот же код, один шаг отмены (снимок перед правкой)."""
        from app import agent_tools

        try:
            get_store().get_clip_plan(clip_plan_id, include_segments=False)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        ctx = agent_tools.ToolContext(
            store=get_store(), clip_plan_id=clip_plan_id, on_first_change=lambda: snapshot_clip(get_store(), clip_plan_id, label),
        )
        result, ok = agent_tools.run_tool(ctx, name, args)
        if not ok:
            problems = result.get("problems") or []
            raise HTTPException(status_code=400, detail=str(result.get("error") or fail)
                                + (f": {'; '.join(map(str, problems))}" if problems else ""))
        return result

    @router.post(
        "/api/clip-plans/{clip_plan_id}/piece-color",
        tags=["Render"],
        summary="🎨 Цветокоррекция куска: экспозиция, контраст, насыщенность, баланс белого",
    )
    def api_clip_plan_piece_color(clip_plan_id: int, payload: PieceColorRequest, _auth: auth_dep) -> dict:
        """Тот же инструмент, что у агентов (set_piece_color, app/piece_color.py)."""
        return _run_clip_tool(clip_plan_id, "set_piece_color", payload.model_dump(exclude_none=True), "цвет куска",
                              "цвет куска не принят")

    @router.post(
        "/api/clip-plans/{clip_plan_id}/piece-color/match",
        tags=["Render"],
        summary="🎨 Подогнать цвет кусков к образцу (замер signalstats)",
    )
    def api_clip_plan_piece_color_match(clip_plan_id: int, payload: PieceColorMatchRequest, _auth: auth_dep) -> dict:
        """Тот же инструмент, что у агентов (match_piece_colors, app/piece_color.py)."""
        return _run_clip_tool(clip_plan_id, "match_piece_colors", payload.model_dump(exclude_none=True),
                              "подгонка цвета", "подгонка цвета не удалась")

    @router.post(
        "/api/clip-plans/{clip_plan_id}/snap-beats",
        tags=["Render"],
        summary="♪ К долям: начала слоёв, ключи, камера и склейки — на ближайшую долю музыки",
    )
    def api_clip_plan_snap_beats(clip_plan_id: int, payload: SnapBeatsRequest, _auth: auth_dep) -> dict:
        """Тот же инструмент, что у агентов (snap_to_beats, app/beat_snap.py)."""
        return _run_clip_tool(clip_plan_id, "snap_to_beats", payload.model_dump(exclude_none=True), "к долям",
                              "к долям не вышло")

    @router.post(
        "/api/clip-plans/{clip_plan_id}/cover/compose",
        tags=["Render"],
        summary="🖼 Обложка с заголовком: кадр клипа + крупный титр, включается сразу",
    )
    def api_cover_compose(clip_plan_id: int, payload: CoverComposeRequest, _auth: auth_dep) -> dict:
        from app.cover_compose import compose_cover
        from app.motion_render import CompositorMissing

        try:
            return compose_cover(get_store(), clip_plan_id, payload.t, payload.title, emoji=payload.emoji, style=payload.style, y=payload.y)
        except CompositorMissing as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post(
        "/api/sources/{source_id}/compilation",
        tags=["Render"],
        summary="🧩 Компиляция «ТОП-N»: моменты подряд с номерами, субтитрами и whoosh",
    )
    def api_source_compilation(source_id: int, payload: CompilationRequest, request: Request, _auth: auth_dep) -> dict:
        from app.compilation import build_compilation
        from app.stream_questions_api import refuse_agent_montage

        refuse_agent_montage(request, get_store(), source_id)
        refuse_agent_drafts(request, get_store(), payload.plan_ids)
        try:
            return build_compilation(
                get_store(), source_id, payload.plan_ids, title=payload.title, countdown=payload.countdown,
                caption_style=payload.caption_style,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post(
        "/api/clip-plans/{clip_plan_id}/auto-edit",
        tags=["Render"],
        summary="⚡ Автомонтаж без ИИ: хук, субтитры по словам, наезды и звуки по речи",
    )
    def api_clip_plan_auto_edit(clip_plan_id: int, request: Request, _auth: auth_dep, payload: AutoEditRequest | None = None) -> dict:
        from app.auto_edit import apply_auto_edit
        from app.stream_questions_api import refuse_agent_clip_montage

        refuse_agent_clip_montage(request, get_store(), clip_plan_id)
        body = payload or AutoEditRequest()
        try:
            return apply_auto_edit(get_store(), clip_plan_id, style=body.style, keep_layers=body.keep_layers)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router
