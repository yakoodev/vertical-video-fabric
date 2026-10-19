"""HTTP-ручки моушн-слоев (docs/motion/spec.md).

* ``GET  /api/motion/schema`` — машиночитаемая схема слоёв: типы, props, пределы,
  easing, анимации. Для ИИ-агента и UI — из тех же констант, что нормализатор.
* ``POST /api/clip-plans/{id}/layers/validate`` — проверить слои (присланные или
  сохранённые) по-человечески и вернуть нормализованный вид. Ничего не сохраняет.
* ``POST /api/clip-plans/{id}/motion/frame?t=`` — PNG кадра с камерой и слоями:
  агент «смотрит», что получилось, не дожидаясь рендера.

Роутер собирается функцией: ``main.py`` передаёт свою зависимость авторизации и
доступ к хранилищу (лямбдой — тесты подменяют ``app.main.store``), так что
циклического импорта нет.
"""

# Без from __future__ import annotations: FastAPI читает аннотации вида
# _auth: auth_dep в момент объявления, а auth_dep — локальная переменная.
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

from app.clip_settings import normalize_render_settings
from app.motion import (
    clip_timeline,
    normalize_camera,
    normalize_layers,
    schema_summary,
    validate_camera,
    validate_layers,
)


class MotionPayload(BaseModel):
    """Несохранённый вариант из редактора/агента; поле не прислано — берётся из клипа."""

    layers: list[Any] | None = None
    camera: list[Any] | None = None


def build_motion_router(auth_dep: Any, get_store: Callable[[], Any]) -> APIRouter:
    router = APIRouter(tags=["Motion"])

    @router.get("/api/motion/schema", summary="Схема моушн-слоёв: типы, props, пределы, easing")
    def api_motion_schema(_auth: auth_dep) -> dict:  # type: ignore[valid-type]
        return schema_summary()

    @router.post(
        "/api/clip-plans/{clip_plan_id}/layers/validate",
        summary="Проверить моушн-слои клипа (ничего не сохраняет)",
    )
    def api_validate_layers(
        clip_plan_id: int,
        _auth: auth_dep,  # type: ignore[valid-type]
        payload: MotionPayload | None = None,
    ) -> dict:
        store = get_store()
        try:
            plan = store.get_clip_plan(clip_plan_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        saved = normalize_render_settings(plan.get("render_settings") or {})
        layers = payload.layers if payload and payload.layers is not None else saved["layers"]
        camera = payload.camera if payload and payload.camera is not None else saved["camera"]
        pieces = [(float(s["start_sec"]), float(s["end_sec"])) for s in plan.get("segments") or []]
        _starts, duration = clip_timeline(pieces, saved["transition"])

        def asset_exists(asset_id: int) -> bool:
            try:
                store.get_montage_asset(asset_id)
            except KeyError:
                return False
            return True

        from app.motion_render import pack_refs_available, resolve_pack_ref

        problems = validate_layers(
            layers,
            duration or None,
            asset_exists=asset_exists,
            # Модуля пакетов может ещё не быть — тогда ссылки не проверяем, а не
            # объявляем все пропавшими.
            ref_exists=(lambda ref: resolve_pack_ref(ref) is not None) if pack_refs_available() else None,
        )
        problems += validate_camera(camera, duration or None)
        return {
            "problems": problems,
            "layers": normalize_layers(layers),
            "camera": normalize_camera(camera),
            "clip_duration": duration,
        }

    @router.post(
        "/api/clip-plans/{clip_plan_id}/motion/frame",
        summary="PNG кадра клипа с камерой и слоями (для агента и превью)",
        response_class=Response,
        responses={200: {"content": {"image/png": {}}}},
    )
    def api_motion_frame(
        clip_plan_id: int,
        _auth: auth_dep,  # type: ignore[valid-type]
        t: float = Query(0.0, ge=0, description="секунды готового клипа"),
        payload: MotionPayload | None = None,
    ) -> Response:
        from app.motion_render import CompositorMissing, render_frame_png

        store = get_store()
        try:
            png = render_frame_png(
                store,
                clip_plan_id,
                t,
                layers=payload.layers if payload else None,
                camera=payload.camera if payload else None,
            )
        except CompositorMissing as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=f"кадр не нарисован: {str(exc)[:500]}") from exc
        return Response(content=png, media_type="image/png", headers={"Cache-Control": "no-store"})

    @router.get(
        "/api/clip-plans/{clip_plan_id}/contact-sheet",
        summary="Лист кадров клипа (JPEG-сетка) — увидеть весь монтаж одним взглядом",
        response_class=Response,
        responses={200: {"content": {"image/jpeg": {}}}},
    )
    def api_contact_sheet(
        clip_plan_id: int,
        _auth: auth_dep,  # type: ignore[valid-type]
        n: int = Query(8, ge=2, le=16, description="сколько кадров"),
        width: int = Query(240, ge=120, le=540, description="ширина одного кадра"),
    ) -> Response:
        """Кадры равномерно по клипу с подписью времени — для агента и для проверки глазами."""
        from app.motion_render import CompositorMissing, contact_sheet_jpeg

        try:
            data = contact_sheet_jpeg(get_store(), clip_plan_id, n=n, width=width)
        except CompositorMissing as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)[:500]) from exc
        return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    return router
