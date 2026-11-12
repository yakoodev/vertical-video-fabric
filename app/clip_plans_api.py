"""План клипа: создание из таймкодов и холста, файл клипа (spec), рендер, куски, фокус, настройки, откат.

Роутер собирается функцией, как ``app/cuts_api.py``: ``main.py`` передаёт зависимость авторизации и доступ к хранилищу
и сервисам лямбдами (тесты подменяют ``app.main.store`` и другие имена)."""

# Без from __future__ import annotations: FastAPI читает аннотации вида
# _auth: auth_dep в момент объявления, а auth_dep — локальная переменная.
import json
import subprocess
from typing import Annotated, Any, Callable, Literal

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.agent_settings_api import refuse_agent_drafts
from app.api_common import _default_subtitle_profile_id
from app.auth import is_agent_request
from app.clip_history import redo as redo_clip, redo_depth, save_with_snapshot, snapshot as snapshot_clip, undo as undo_clip
from app.clip_settings import settings_to_render_kwargs
from app.clip_spec import apply_spec, export_spec, json_schema as clip_spec_json_schema, SpecError, validate_spec
from app.settings import settings
from app.storyboard import plan_thumb
from app.timeline_export import write_timeline
from app.transitions import transition_options


class RenderSegmentRequest(BaseModel):
    ffmpeg_preset_id: int | None = None
    subtitle_profile_id: int | None = None
    banner_id: int | None = None
    music_track_id: int | None = None
    music_volume: float | None = None


class RenderMontageRequest(BaseModel):
    segment_ids: list[int]
    ffmpeg_preset_id: int | None = None
    subtitle_profile_id: int | None = None
    banner_id: int | None = None
    music_track_id: int | None = None
    music_volume: float | None = None
    title: str = "Montage"
    description: str = ""


class SegmentTimecodesPatchPayload(BaseModel):
    start_sec: float
    end_sec: float


class RenderClipPlanRequest(BaseModel):
    ffmpeg_preset_id: int | None = None
    subtitle_profile_id: int | None = None
    banner_id: int | None = None
    music_track_id: int | None = None
    music_volume: float | None = None
    # Формат этого рендера поверх настроек клипа (app/formats.py): "9:16" | "1:1" | "4:5" |
    # "16:9" — у любого клипа. Не задан — из настроек клипа (render_settings.format).
    # ``aspect`` — старое имя того же поля.
    format: str | None = None
    aspect: str | None = None
    # Стоп-кадр в конце этого рендера, с (0..1.5; мему — 0). Не задан — из настроек клипа
    # (render_settings.end_hold_sec), там нет — RENDER_END_HOLD_SEC.
    end_hold_sec: float | None = Field(default=None, ge=0, le=1.5)


class RenderClipPlansRequest(BaseModel):
    clip_plan_ids: list[int]
    ffmpeg_preset_id: int | None = None
    subtitle_profile_id: int | None = None
    subtitle_provider: str | None = None
    subtitle_margin_v: int | None = None
    banner_id: int | None = None
    banner_height_frac: float | None = None
    banner_y_frac: float | None = None
    mirror: bool | None = None
    music_track_id: int | None = None
    music_volume: float | None = None
    # Join settings for plans that have no saved settings of their own.
    transition: dict | None = None


class ClipPlanSegmentCreateRequest(BaseModel):
    start_sec: float
    end_sec: float
    title: str = "Segment"


class ClipPlanSegmentMoveRequest(BaseModel):
    direction: Literal["up", "down"]


class ManualClipPiece(BaseModel):
    start_sec: float
    end_sec: float
    title: str | None = None


class ManualClipRequest(BaseModel):
    title: str
    description: str = ""
    pieces: list[ManualClipPiece]
    # Новая версия этого клипа (id любого его плана): план ляжет в его группу, карточка не задвоится.
    version_of: int | None = None


class CanvasClipRequest(BaseModel):
    duration: float = 15.0
    # Имя стиля (aurora/grid/bokeh/waves/gradient/solid) или {kind, colors, speed, intensity}.
    background: str | dict | None = None
    title: str = ""
    name: str = ""
    description: str = ""
    # Холст из проекта: приписка клипа к проекту (app/projects.py: plan_projects).
    project_id: int | None = None


class FocusPoint(BaseModel):
    t: float
    x: float
    y: float | None = None


class SegmentFocusPayload(BaseModel):
    focus: list[FocusPoint] = Field(default_factory=list)


class AutofocusPayload(BaseModel):
    use_vlm: bool = False


class AutofocusBatchPayload(BaseModel):
    segment_ids: list[int] = Field(default_factory=list)
    use_vlm: bool = False


class ClipPlanFocusPayload(BaseModel):
    x: float | None = None


class ClipPlanFlagsPayload(BaseModel):
    favorite: bool | None = None
    hidden: bool | None = None


class ClipPlanRenderSettingsPayload(BaseModel):
    settings: dict


def build_clip_plans_router(
    auth_dep: Any,
    get_store: Callable[[], Any],
    get_clip_render_service: Callable[[], Any],
) -> APIRouter:
    router = APIRouter()

    @router.get("/media/clip-plans/{clip_plan_id}/thumb", include_in_schema=False)
    def media_clip_plan_thumb(clip_plan_id: int, _auth: auth_dep) -> FileResponse:
        """9:16 cover for a moment card: mid-point of its first segment, cropped at its focus."""
        try:
            plan = get_store().get_clip_plan(clip_plan_id)
            source = get_store().get_source(int(plan["source_id"]))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        segments = plan.get("segments") or []
        if not segments:
            raise HTTPException(status_code=404, detail="clip plan has no segments")
        seg = segments[0]
        start, end = float(seg["start_sec"]), float(seg["end_sec"])
        mid = start + max(0.0, end - start) / 2.0
        focus = seg.get("focus") or []
        if not focus and seg.get("focus_json"):
            try:
                focus = json.loads(seg["focus_json"]) or []
            except (TypeError, ValueError):
                focus = []
        x = 0.5
        if focus:
            rel = mid - start
            x = float(min(focus, key=lambda p: abs(float(p.get("t", 0)) - rel)).get("x", 0.5))
        try:
            path = plan_thumb(source, mid, x)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})

    @router.post(
        "/api/sources/{source_id}/manual-clips",
        tags=["Render"],
        summary="Create a clip from source timecodes (no AI analysis needed)",
    )
    def api_create_manual_clip(source_id: int, payload: ManualClipRequest, request: Request, _auth: auth_dep) -> dict:
        from app.manual_clips import create_manual_clip
        from app.stream_questions_api import refuse_agent_montage

        refuse_agent_montage(request, get_store(), source_id, payload.version_of)
        try:
            return create_manual_clip(
                get_store(), source_id, payload.title, [p.model_dump() for p in payload.pieces], payload.description,
                version_of=payload.version_of,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post(
        "/api/canvas-clips",
        tags=["Render"],
        summary="Create an empty-canvas clip (animated background, no source video)",
    )
    def api_create_canvas_clip(payload: CanvasClipRequest, _auth: auth_dep) -> dict:
        """Клип без исходника (app/canvas.py): анимированный фон нужной длины, дальше —
        слои, музыка, звуки и рендер как у обычного клипа. Скрытый источник холстов в
        «Проектах» не виден; ответ — клип-план и ``editor_url``."""
        from app.canvas import create_canvas_clip

        try:
            # Проект проверяется до создания: несуществующий проект не оставляет ничейного холста.
            plan = create_canvas_clip(get_store(), payload.duration, payload.background,
                                      payload.title or payload.name, payload.description,
                                      project_id=payload.project_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {**plan, "editor_url": f"/projects/{plan['source_id']}/candidates?clip={plan['id']}"}

    @router.post(
        "/api/segments/{segment_id}/realizations",
        tags=["Render"],
        summary="Render a vertical clip from an AI segment",
    )
    def api_render_segment(
        segment_id: int,
        _auth: auth_dep,
        payload: RenderSegmentRequest | None = None,
    ) -> dict:
        try:
            return get_clip_render_service().render_segment(
                segment_id,
                ffmpeg_preset_id=payload.ffmpeg_preset_id if payload else None,
                subtitle_profile_id=payload.subtitle_profile_id if payload else None,
                banner_id=payload.banner_id if payload else None,
                music_track_id=payload.music_track_id if payload else None,
                music_volume=payload.music_volume if payload else None,
            )
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.patch(
        "/api/segments/{segment_id}/timecodes",
        tags=["Render"],
        summary="Update AI segment timecodes",
    )
    def api_update_segment_timecodes(
        segment_id: int,
        payload: SegmentTimecodesPatchPayload,
        _auth: auth_dep,
    ) -> dict:
        try:
            from app.piece_remap import retime_segment

            # Монтаж клипов с этим куском (титры, камера, вставки) едет вслед за краем.
            return retime_segment(get_store(), segment_id, payload.start_sec, payload.end_sec)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.patch("/api/segments/{segment_id}/focus", tags=["Render"], summary="Set a segment's reframe focus track")
    def api_update_segment_focus(segment_id: int, payload: SegmentFocusPayload, _auth: auth_dep) -> dict:
        focus = [p.model_dump(exclude_none=True) for p in payload.focus]
        try:
            return get_store().update_ai_segment_focus(segment_id, focus)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def _autofocus_segment(segment_id: int, use_vlm: bool = False) -> dict:
        from app.autofocus import compute_segment_focus  # lazy: opencv is heavy / optional

        segment = get_store().get_ai_segment(segment_id)
        source = get_store().get_source(segment["source_id"])
        resolver = None
        if use_vlm and settings.gemini_api_key:
            from app.ai.vlm_focus import shots_focus_x  # lazy: only when asked

            resolver = shots_focus_x
        points = compute_segment_focus(
            source,
            float(segment["start_sec"]),
            float(segment["end_sec"]),
            vlm_resolver=resolver,
        )
        return get_store().update_ai_segment_focus(segment_id, points)

    @router.post("/api/segments/{segment_id}/autofocus", tags=["Render"], summary="Detect focus (faces/motion) for a segment")
    def api_autofocus_segment(segment_id: int, _auth: auth_dep, payload: AutofocusPayload | None = None) -> dict:
        try:
            return _autofocus_segment(segment_id, use_vlm=bool(payload and payload.use_vlm))
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/api/sources/{source_id}/autofocus", tags=["Render"], summary="Detect focus for many segments")
    def api_autofocus_segments(source_id: int, payload: AutofocusBatchPayload, _auth: auth_dep) -> dict:
        try:
            get_store().get_source(source_id)
            ids = payload.segment_ids or [s["id"] for s in get_store().list_ai_segments(source_id=source_id)]
            done = 0
            for sid in ids:
                seg = get_store().get_ai_segment(sid)
                if seg["source_id"] != source_id:
                    continue
                _autofocus_segment(sid, use_vlm=payload.use_vlm)
                done += 1
            return {"updated": done}
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post(
        "/api/clip-plans/{clip_plan_id}/render",
        tags=["Render"],
        summary="Render a saved clip plan",
    )
    def api_render_clip_plan(
        clip_plan_id: int,
        _auth: auth_dep,
        payload: RenderClipPlanRequest | None = None,
    ) -> dict:
        try:
            plan = get_store().get_clip_plan(clip_plan_id, include_segments=False)
            # Same rule as the batch: a clip set up in the editor / clip file renders
            # with its own settings; the request only fills in for a clip never set up.
            if plan.get("render_settings"):
                kwargs = settings_to_render_kwargs(plan["render_settings"], _default_subtitle_profile_id(get_store()))
                kwargs["music_volume"] = payload.music_volume if payload else None
            else:
                kwargs = {
                    "ffmpeg_preset_id": payload.ffmpeg_preset_id if payload else None,
                    "subtitle_profile_id": payload.subtitle_profile_id if payload else None,
                    "banner_id": payload.banner_id if payload else None,
                    "music_track_id": payload.music_track_id if payload else None,
                    "music_volume": payload.music_volume if payload else None,
                }
            if payload and (payload.format or payload.aspect):
                kwargs["aspect"] = payload.format or payload.aspect
            if payload and payload.end_hold_sec is not None:
                kwargs["end_hold_sec"] = payload.end_hold_sec
            return get_clip_render_service().render_clip_plan(clip_plan_id, **kwargs)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # ---- clip file (vvf.clip/1): one editable document per clip, see app/clip_spec.py


    @router.get(
        "/api/clip-plans/{clip_plan_id}/spec",
        tags=["Clip file"],
        summary="Get the clip file: pieces, framing, render settings, renders with QC",
    )
    def api_get_clip_spec(clip_plan_id: int, _auth: auth_dep) -> dict:
        try:
            return export_spec(get_store(), clip_plan_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.put(
        "/api/clip-plans/{clip_plan_id}/spec",
        tags=["Clip file"],
        summary="Validate the whole clip file, then apply it (all-or-nothing)",
    )
    def api_put_clip_spec(clip_plan_id: int, spec: Annotated[dict, Body()], _auth: auth_dep) -> dict:
        try:
            snapshot_clip(get_store(), clip_plan_id, "правка клип-файла")
            fresh, changes = apply_spec(get_store(), clip_plan_id, spec)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except SpecError as exc:
            raise HTTPException(status_code=422, detail={"problems": exc.problems}) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"spec": fresh, "changes": changes}

    @router.get(
        "/api/clip-plans/{clip_plan_id}/timeline",
        tags=["Clip file"],
        summary="🎬 Отдать клип в монтажку: .otio / .edl / FCP7 .xml",
    )
    def api_clip_plan_timeline(clip_plan_id: int, _auth: auth_dep, format: str = "otio") -> FileResponse:
        """OTIO и его диалекты: .otio открывает Kdenlive, .edl и .xml — Resolve и Premiere."""
        try:
            path = write_timeline(get_store(), clip_plan_id, format)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return FileResponse(path, filename=path.name, media_type="application/octet-stream")

    @router.get(
        "/api/clip-plans/{clip_plan_id}/waveform",
        tags=["Clip file"],
        summary="Волна звука клипа для таймлайна (речь и паузы)",
    )
    def api_clip_plan_waveform(clip_plan_id: int, _auth: auth_dep, buckets: int = 600) -> dict:
        from app.clip_waveform import clip_waveform

        try:
            return clip_waveform(get_store(), clip_plan_id, buckets)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post(
        "/api/clip-plans/{clip_plan_id}/undo",
        tags=["Clip file"],
        summary="↩ Шаг назад: вернуть клип к состоянию перед последней правкой",
    )
    def api_clip_plan_undo(clip_plan_id: int, _auth: auth_dep) -> dict:
        """Одна отмена на любую правку: настройки, куски, субтитры, ИИ-монтаж."""
        try:
            fresh, changes, label = undo_clip(get_store(), clip_plan_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except SpecError as exc:
            raise HTTPException(status_code=422, detail={"problems": exc.problems}) from exc
        return {
            "spec": fresh,
            "changes": changes,
            "label": label,
            "left": get_store().count_clip_plan_history(clip_plan_id),
            "redo": redo_depth(get_store(), clip_plan_id),
        }

    @router.get(
        "/api/clip-plans/{clip_plan_id}/undo",
        tags=["Clip file"],
        summary="Сколько шагов назад и вперёд доступно",
    )
    def api_clip_plan_undo_depth(clip_plan_id: int, _auth: auth_dep) -> dict:
        return {"left": get_store().count_clip_plan_history(clip_plan_id), "redo": redo_depth(get_store(), clip_plan_id)}

    @router.post(
        "/api/clip-plans/{clip_plan_id}/redo",
        tags=["Clip file"],
        summary="↪ Шаг вперёд: вернуть то, что отменил шаг назад (пока клип после отмены не менялся)",
    )
    def api_clip_plan_redo(clip_plan_id: int, _auth: auth_dep) -> dict:
        try:
            fresh, changes, label = redo_clip(get_store(), clip_plan_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except SpecError as exc:
            raise HTTPException(status_code=422, detail={"problems": exc.problems}) from exc
        return {
            "spec": fresh,
            "changes": changes,
            "label": label,
            "left": get_store().count_clip_plan_history(clip_plan_id),
            "redo": redo_depth(get_store(), clip_plan_id),
        }

    @router.post(
        "/api/clip-plans/{clip_plan_id}/spec/validate",
        tags=["Clip file"],
        summary="Check a clip file without applying it",
    )
    def api_validate_clip_spec(clip_plan_id: int, spec: Annotated[dict, Body()], _auth: auth_dep) -> dict:
        try:
            validate_spec(get_store(), clip_plan_id, spec)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except SpecError as exc:
            return {"ok": False, "problems": exc.problems}
        return {"ok": True, "problems": []}

    @router.get("/api/clip-spec/schema", tags=["Clip file"], summary="JSON Schema of the clip file")
    def api_clip_spec_schema(_auth: auth_dep) -> dict:
        return clip_spec_json_schema()

    @router.post(
        "/api/sources/{source_id}/render-plans",
        tags=["Render"],
        summary="Render selected clip plans for a source",
    )
    def api_render_clip_plans(
        source_id: int,
        payload: RenderClipPlansRequest,
        request: Request,
        _auth: auth_dep,
    ) -> list[dict]:
        if not payload.clip_plan_ids:
            raise HTTPException(status_code=400, detail="clip_plan_ids cannot be empty")
        refuse_agent_drafts(request, get_store(), payload.clip_plan_ids)
        clips: list[dict] = []
        batch_started_at = get_clip_render_service().batch_clock()
        try:
            for clip_plan_id in payload.clip_plan_ids:
                if get_clip_render_service().render_batch_cancelled(source_id, batch_started_at):
                    break
                plan = get_store().get_clip_plan(clip_plan_id, include_segments=False)
                if plan["source_id"] != source_id:
                    raise ValueError("clip plan does not belong to source")
                # Each clip is set up on its own in the editor: its saved settings win.
                # A clip nobody opened falls back to the request (the panel's values).
                if plan.get("render_settings"):
                    kwargs = settings_to_render_kwargs(plan["render_settings"], _default_subtitle_profile_id(get_store()))
                    kwargs["music_volume"] = payload.music_volume
                else:
                    kwargs = {
                        "ffmpeg_preset_id": payload.ffmpeg_preset_id,
                        "subtitle_profile_id": payload.subtitle_profile_id,
                        "banner_id": payload.banner_id,
                        "music_track_id": payload.music_track_id,
                        "music_volume": payload.music_volume,
                        "subtitle_provider": payload.subtitle_provider,
                        "banner_height_frac": payload.banner_height_frac,
                        "banner_y_frac": payload.banner_y_frac,
                        "subtitle_margin_v": payload.subtitle_margin_v,
                        "mirror": bool(payload.mirror),
                        "transition": payload.transition,
                    }
                clips.append(get_clip_render_service().render_clip_plan(clip_plan_id, **kwargs))
            return clips
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post(
        "/api/sources/{source_id}/render-cancel",
        tags=["Render"],
        summary="Stop an in-progress batch render for a source",
    )
    def api_cancel_render(source_id: int, _auth: auth_dep) -> dict:
        try:
            get_store().get_source(source_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        get_clip_render_service().request_render_cancel(source_id)
        return {"cancelling": True, "source_id": source_id}

    @router.patch(
        "/api/clip-plans/{clip_plan_id}/focus",
        tags=["Render"],
        summary="Set a manual frame position for a clip plan (x=null clears it)",
    )
    def api_set_clip_plan_focus(clip_plan_id: int, payload: ClipPlanFocusPayload, _auth: auth_dep) -> dict:
        try:
            return get_store().set_clip_plan_manual_focus(clip_plan_id, payload.x)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.patch(
        "/api/clip-plans/{clip_plan_id}/render-settings",
        tags=["Render"],
        summary="Save one clip's own render settings (subs, banner, music, transitions…)",
    )
    def api_set_clip_plan_render_settings(
        clip_plan_id: int, payload: ClipPlanRenderSettingsPayload, _auth: auth_dep
    ) -> dict:
        try:
            settings_in = dict(payload.settings)
            if "cuts" not in settings_in:
                # Редактор, который ещё не знает о вырезах, не должен стирать их
                # автосохранением: нет ключа — остаются сохранённые. [] — убрать все.
                current = get_store().get_clip_plan(clip_plan_id, include_segments=False).get("render_settings") or {}
                settings_in["cuts"] = current.get("cuts") or []
            if "end_screen" not in settings_in:
                # Концовку ставит агент/бренд (app/end_screen.py): редактор без этого поля её не стирает.
                current = get_store().get_clip_plan(clip_plan_id, include_segments=False).get("render_settings") or {}
                settings_in["end_screen"] = current.get("end_screen")
            if "motion_blur" not in settings_in:
                # Motion blur клипа ставит агент (set_motion_blur): клиент без этого поля его не стирает.
                current = get_store().get_clip_plan(clip_plan_id, include_segments=False).get("render_settings") or {}
                settings_in["motion_blur"] = current.get("motion_blur")
            if "audio" not in settings_in:
                # Звуковой монтаж (J/L, ключи, эффекты — app/audio_edit.py) — так же: нет ключа — остаётся.
                current = get_store().get_clip_plan(clip_plan_id, include_segments=False).get("render_settings") or {}
                settings_in["audio"] = current.get("audio") or {}
            if "mix" not in settings_in:
                # Сведение (app/audio_mix.py: auto_mix / set_mix) — редактор без этого поля его не стирает.
                current = get_store().get_clip_plan(clip_plan_id, include_segments=False).get("render_settings") or {}
                settings_in["mix"] = current.get("mix") or {}
            if "speed" not in settings_in:
                # Скорость/реверс кусков (app/piece_speed.py) ставит инструмент или инспектор куска:
                # автосохранение без поля её не сбрасывает.
                current = get_store().get_clip_plan(clip_plan_id, include_segments=False).get("render_settings") or {}
                settings_in["speed"] = current.get("speed") or []
            if "color_fix" not in settings_in:
                # Цветокоррекция кусков (app/piece_color.py) — так же: ставит инструмент или инспектор.
                current = get_store().get_clip_plan(clip_plan_id, include_segments=False).get("render_settings") or {}
                settings_in["color_fix"] = current.get("color_fix") or []
            if not {"format", "aspect", "layers_format"} <= set(settings_in):
                # Формат вывода и формат разметки слоёв (app/formats.py) — так же: редактор,
                # что их не шлёт, их не сбрасывает (старый шлёт aspect — он и есть формат).
                current = get_store().get_clip_plan(clip_plan_id, include_segments=False).get("render_settings") or {}
                if "format" not in settings_in and "aspect" not in settings_in:
                    settings_in["format"] = current.get("format") or current.get("aspect")
                settings_in.setdefault("layers_format", current.get("layers_format"))
            return save_with_snapshot(
                get_store(), clip_plan_id, "настройки клипа", lambda: get_store().set_clip_plan_render_settings(clip_plan_id, settings_in)
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get(
        "/api/render/transition-options",
        tags=["Render"],
        summary="Transition types, join-sound modes and SFX the renderer supports",
    )
    def api_transition_options(_auth: auth_dep) -> dict:
        return transition_options()

    @router.patch(
        "/api/clip-plans/{clip_plan_id}/flags",
        tags=["Render"],
        summary="Toggle favourite/hidden triage flags on a candidate",
    )
    def api_set_clip_plan_flags(clip_plan_id: int, payload: ClipPlanFlagsPayload, request: Request, _auth: auth_dep) -> dict:
        try:
            flags = get_store().set_clip_plan_flags(clip_plan_id, favorite=payload.favorite, hidden=payload.hidden)
            if payload.favorite is not None and not is_agent_request(request):
                # ★ владельца — его выбор из списка черновиков (app/agent_settings.py). ★ агента выбором не считается.
                from app.agent_settings import set_picked

                set_picked(get_store(), clip_plan_id, bool(payload.favorite))
            return flags
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post(
        "/api/montages",
        tags=["Render"],
        summary="Render one stitched vertical clip from multiple AI segments",
    )
    def api_render_montage(payload: RenderMontageRequest, _auth: auth_dep) -> dict:
        try:
            return get_clip_render_service().render_montage(
                payload.segment_ids,
                ffmpeg_preset_id=payload.ffmpeg_preset_id,
                subtitle_profile_id=payload.subtitle_profile_id,
                banner_id=payload.banner_id,
                music_track_id=payload.music_track_id,
                music_volume=payload.music_volume,
                title=payload.title,
                description=payload.description,
            )
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.delete("/api/clip-plans/{clip_plan_id}", tags=["Clip plans"], summary="Delete a clip plan (moment)")
    def api_delete_clip_plan(clip_plan_id: int, _auth: auth_dep) -> dict:
        """Момент целиком; готовые рендеры остаются в «Клипах»."""
        try:
            get_store().delete_clip_plan(clip_plan_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"deleted": True}

    @router.post("/api/clip-plans/{clip_plan_id}/segments", tags=["Render"], summary="Add segment to clip plan")
    def api_add_clip_plan_segment(
        clip_plan_id: int,
        payload: ClipPlanSegmentCreateRequest,
        _auth: auth_dep,
    ) -> dict:
        try:
            return get_store().add_segment_to_clip_plan(clip_plan_id, payload.start_sec, payload.end_sec, payload.title)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.delete(
        "/api/clip-plans/{clip_plan_id}/segments/{segment_id}",
        tags=["Render"],
        summary="Remove segment from clip plan",
    )
    def api_remove_clip_plan_segment(clip_plan_id: int, segment_id: int, _auth: auth_dep) -> dict:
        try:
            return get_store().remove_segment_from_clip_plan(clip_plan_id, segment_id)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post(
        "/api/clip-plans/{clip_plan_id}/segments/{segment_id}/move",
        tags=["Render"],
        summary="Move clip plan segment",
    )
    def api_move_clip_plan_segment(
        clip_plan_id: int,
        segment_id: int,
        payload: ClipPlanSegmentMoveRequest,
        _auth: auth_dep,
    ) -> dict:
        try:
            return get_store().move_clip_plan_segment(clip_plan_id, segment_id, payload.direction)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router
