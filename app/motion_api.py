"""HTTP-ручки моушн-слоев (docs/motion/spec.md).

* ``GET  /api/motion/schema`` — машиночитаемая схема слоёв: типы, props, пределы,
  easing, анимации. Для ИИ-агента и UI — из тех же констант, что нормализатор.
* ``POST /api/clip-plans/{id}/layers/validate`` — проверить слои (присланные или
  сохранённые) по-человечески и вернуть нормализованный вид. Ничего не сохраняет.
* ``POST /api/clip-plans/{id}/motion/frame?t=`` — PNG кадра с камерой и слоями:
  агент «смотрит», что получилось, не дожидаясь рендера.
* ``POST /api/clip-plans/{id}/voice`` — состояния голоса PNGtuber по кадрам (``comp.voice``) для
  превью: тот же массив, что получает компоновщик рендера (app/pngtuber.py).
* `POST /api/clip-plans/{id}/levels` — огибающие громкости клипа (голос / музыка / весь звук) для
  `react` слоёв в превью — тот же массив, что `comp.levels` рендера (app/audio_react.py).
* ``GET /api/montage-assets/{id}/anim`` и ``…/anim-sheet.png`` — анимация файла (GIF/APNG/webm с
  альфой) одним листом кадров с их временами: превью рисует те же кадры, что рендер.

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


class VoicePayload(BaseModel):
    """Слои PNGtuber (несохранённые из редактора; нет — сохранённые) и частота кадров состояний."""

    layers: list[Any] | None = None
    fps: float | None = None
    # Шкала ответа: clip — как слои редактора, final — как у рендера (предпросмотр монтажа).
    scale: str | None = None


class LevelsPayload(BaseModel):
    """Чьи огибающие нужны (voice/music/mix; нет — по react сохранённых слоёв) и частота кадров."""

    sources: list[str] | None = None
    fps: float | None = None
    scale: str | None = None
    # Слои для волн со своим звуком (waveform audio_layer/asset_id → wave:<id>); нет — сохранённые.
    layers: list | None = None


class CameraPresetPayload(BaseModel):
    """Пресет 3D-движения для редактора: текущие (несохранённые) ключи камеры и слои — ответ с новыми."""

    preset: str
    start: float
    duration: float | None = None
    intensity: float | None = None
    layer_id: str | None = None
    camera: list[Any] | None = None
    layers: list[Any] | None = None
    clip_duration: float | None = None
    motion_blur: float | None = None
    # screen_tilt: группа слоёв (id или префикс id), точка наезда (доли кадра) и наезд.
    layer_ids: list[str] | None = None
    layer_prefix: str | None = None
    x: float | None = None
    y: float | None = None
    zoom: float | None = None


class TrackPayload(BaseModel):
    """Трекинг слоя (app/tracking.py): слой — несохранённый из редактора, ответ — новый слой."""

    layer: dict
    target: dict
    offset: dict | None = None
    follow: dict | None = None
    smooth: float | None = None
    fps: float | None = None
    preset: str | None = None


class MotionPayload(BaseModel):
    """Несохранённый вариант из редактора/агента; поле не прислано — берётся из клипа."""

    layers: list[Any] | None = None
    camera: list[Any] | None = None


def build_motion_router(auth_dep: Any, get_store: Callable[[], Any]) -> APIRouter:
    router = APIRouter(tags=["Motion"])

    @router.get("/api/motion/schema", summary="Схема моушн-слоёв: типы, props, пределы, easing")
    def api_motion_schema(_auth: auth_dep) -> dict:  # type: ignore[valid-type]
        return schema_summary()

    @router.get("/api/motion/camera-presets", summary="Пресеты 3D-движения камеры (app/camera3d.py)")
    def api_camera_presets(_auth: auth_dep) -> dict:  # type: ignore[valid-type]
        from app.camera3d import describe

        return {"presets": describe()}

    @router.post("/api/motion/camera-preset", summary="Поставить пресет 3D-движения (ничего не сохраняет)")
    def api_camera_preset(payload: CameraPresetPayload, _auth: auth_dep) -> dict:  # type: ignore[valid-type]
        """``{preset, camera, layers, keys, range, note}`` — новые ключи камеры (и слои у card_flip);
        редактор кладёт их в свои настройки, автосохранение и Ctrl+Z — как у остальных правок."""
        from app.camera3d import PresetError, apply_preset

        try:
            return apply_preset(payload.preset, payload.start, payload.duration, payload.intensity,
                                camera=payload.camera, layers=payload.layers, layer_id=payload.layer_id,
                                clip_duration=payload.clip_duration, motion_blur=payload.motion_blur,
                                layer_ids=payload.layer_ids, layer_prefix=payload.layer_prefix,
                                point={"x": payload.x, "y": payload.y}, zoom=payload.zoom)
        except PresetError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc), "hints": exc.hints}) from exc

    @router.get("/api/motion/track-presets", summary="Подсказки трекинга: куда ставить слой от цели")
    def api_track_presets(_auth: auth_dep) -> dict:  # type: ignore[valid-type]
        from app.tracking import DEFAULT_SMOOTH, PRESETS

        return {"presets": [{"id": k, "title": v["title"], "offset": v["offset"], "follow": v.get("follow") or {}}
                            for k, v in PRESETS.items()], "smooth": DEFAULT_SMOOTH}

    @router.post("/api/clip-plans/{clip_plan_id}/track", summary="Трекинг слоя: запустить (ничего не сохраняет)")
    def api_track_layer(clip_plan_id: int, payload: TrackPayload, _auth: auth_dep) -> dict:  # type: ignore[valid-type]
        """``{job_id}`` — трек считается в фоне (очередь тяжёлых задач); ход и итог —
        ``GET /api/track-jobs/{job_id}``: ``{state, progress, stage, result: {layer, report}, error}``.
        Редактор кладёт ``result.layer`` в свои слои — автосохранение и Ctrl+Z как у остальных правок."""
        from app.tracking import TrackError, normalize_target, start_job, track_layer

        store = get_store()
        try:
            store.get_clip_plan(clip_plan_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        layer = normalize_layers([payload.layer])[0] if payload.layer else None
        if not layer:
            raise HTTPException(status_code=400, detail="нужен слой")
        try:
            normalize_target(payload.target)
        except TrackError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc), "hints": exc.hints}) from exc

        def run(progress: Callable[[float, str], None]) -> dict:
            fresh, report = track_layer(store, clip_plan_id, layer, payload.target, offset=payload.offset,
                                        follow=payload.follow, smooth=payload.smooth, fps=payload.fps,
                                        preset=payload.preset, progress=progress)
            return {"layer": normalize_layers([fresh])[0], "report": report}

        return {"job_id": start_job(run)}

    @router.get("/api/track-jobs/{job_id}", summary="Ход трекинга слоя")
    def api_track_job(job_id: str, _auth: auth_dep) -> dict:  # type: ignore[valid-type]
        from app.tracking import job_status

        job = job_status(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="задачи трекинга нет (сервис перезапускался?)")
        return job

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
        pieces = [(float(s["start_sec"]), float(s["end_sec"]), float(s.get("speed") or 1)) for s in plan.get("segments") or []]
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
        from app.asset_scope import clip_project, new_foreign_assets, refusal, render_asset_ids

        foreign = new_foreign_assets(store, clip_plan_id, render_asset_ids({"layers": normalize_layers(layers)}), saved)
        problems += [refusal(store, asset, clip_project(store, clip_plan_id)) for asset in foreign]
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

    @router.post(
        "/api/clip-plans/{clip_plan_id}/voice",
        summary="PNGtuber: состояния голоса по кадрам (молчит/говорит/кричит) — для превью",
    )
    def api_clip_voice(
        clip_plan_id: int,
        _auth: auth_dep,  # type: ignore[valid-type]
        payload: VoicePayload | None = None,
    ) -> dict:
        """``{voice: {id слоя: {t0, fps, states, levels, threshold_db}}, notes}`` — шкала clip (как слои)."""
        from app.pngtuber import plan_voice

        store = get_store()
        try:
            plan = store.get_clip_plan(clip_plan_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        saved = normalize_render_settings(plan.get("render_settings") or {})
        layers = normalize_layers(payload.layers) if payload and payload.layers is not None else saved["layers"]
        fps = max(5.0, min(60.0, float(payload.fps or 30))) if payload and payload.fps else 30.0
        scale = "final" if payload and payload.scale == "final" else "clip"
        voice, notes = plan_voice(store, plan, layers, fps, scale)
        return {"voice": voice, "notes": notes, "scale": scale}

    @router.post(
        "/api/clip-plans/{clip_plan_id}/levels",
        summary="Реакция на звук: огибающие громкости клипа по кадрам (голос, музыка, весь звук) — для превью",
    )
    def api_clip_levels(
        clip_plan_id: int,
        _auth: auth_dep,  # type: ignore[valid-type]
        payload: LevelsPayload | None = None,
    ) -> dict:
        """``{levels: {voice|music|mix: {t0, fps, v [0..100]}}, notes}`` — шкала clip (как слои)."""
        from app.audio_react import plan_levels
        from app.motion import REACT_SOURCES, react_sources

        store = get_store()
        try:
            plan = store.get_clip_plan(clip_plan_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        saved = normalize_render_settings(plan.get("render_settings") or {})["layers"]
        if payload and payload.sources is not None:
            sources = [s for s in REACT_SOURCES if s in set(payload.sources)]
        else:
            sources = react_sources(saved)
        # Волны со своим звуком (wave:<id>): слои из запроса (несохранённые правки) или сохранённые.
        layers = normalize_layers(payload.layers) if payload and payload.layers is not None else saved
        fps = max(5.0, min(60.0, float(payload.fps or 30))) if payload and payload.fps else 30.0
        scale = "final" if payload and payload.scale == "final" else "clip"
        levels, notes = plan_levels(store, plan, sources, fps, scale, layers=layers)
        return {"levels": levels, "notes": notes, "scale": scale}

    def _asset_path(asset_id: int):
        from pathlib import Path

        try:
            asset = get_store().get_montage_asset(asset_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        path = Path(str(asset.get("file_path") or ""))
        if not path.is_file():
            raise HTTPException(status_code=404, detail="файла нет на диске")
        return path

    def _sheet(asset_id: int):
        from app.anim_frames import anim_sheet
        from app.settings import settings

        try:
            return anim_sheet(_asset_path(asset_id), settings.runtime_dir / "anim-sheets")
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=f"лист кадров не собран: {str(exc)[:300]}") from exc

    @router.get(
        "/api/montage-assets/{asset_id}/anim",
        summary="Анимация файла (GIF/APNG/webm с альфой): разметка листа кадров и времена",
    )
    def api_asset_anim(asset_id: int, _auth: auth_dep) -> dict:  # type: ignore[valid-type]
        """``{animated: false}`` — статичная картинка; иначе сетка листа, ``times``, ``duration``
        и ``sheet`` — адрес листа кадров (PNG с альфой)."""
        _path, meta = _sheet(asset_id)
        if meta.get("animated"):
            meta = {**meta, "sheet": f"/api/montage-assets/{asset_id}/anim-sheet.png"}
        return meta

    @router.get(
        "/api/montage-assets/{asset_id}/anim-sheet.png",
        summary="Лист кадров анимации (PNG с альфой)",
        response_class=Response,
        responses={200: {"content": {"image/png": {}}}},
    )
    def api_asset_anim_sheet(asset_id: int, _auth: auth_dep) -> Response:  # type: ignore[valid-type]
        path, _meta = _sheet(asset_id)
        if path is None:
            raise HTTPException(status_code=404, detail="файл не анимация")
        return Response(content=path.read_bytes(), media_type="image/png",
                        headers={"Cache-Control": "private, max-age=3600"})

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
