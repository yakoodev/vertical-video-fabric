"""Видео-источники: загрузка и скачивание, список и карточка, кроп, ключ, звук, раскадровка, расшифровка, анализ.

``download_source_url`` — ``_download_source_url`` из ``main.py``: скачивание ссылкой с ожиданием результата.

Роутер собирается функцией, как ``app/cuts_api.py``: ``main.py`` передаёт зависимость авторизации и доступ к хранилищу
и сервисам лямбдами (тесты подменяют ``app.main.store`` и другие имена)."""

# Без from __future__ import annotations: FastAPI читает аннотации вида
# _auth: auth_dep в момент объявления, а auth_dep — локальная переменная.
import asyncio
import subprocess
from pathlib import Path
from typing import Annotated, Any, Callable, Literal

from fastapi import APIRouter, Form, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field

from app.api_common import _safe_next, THUMB_CACHE
from app.cut_refine import list_cut_strategies
from app.focus_presets import list_focus_presets, list_focus_strategies
from app.settings import settings
from app.smotvibe import discover_smotvibe_download_options
from app.storyboard import ensure_storyboard, frame_path
from app.thumbs import source_thumb
from app.video_crop import detect_content_crop


class SourceRead(BaseModel):
    id: int
    status: str
    source_type: str
    original_url: str
    original_filename: str
    local_path: str
    sha256: str
    size_bytes: int
    duration_sec: float
    width: int
    height: int
    fps: float
    metadata_json: str
    error: str
    created_at: str
    updated_at: str
    analyses_count: int | None = None
    clip_plans_count: int | None = None
    # Клипов для человека: группа версий считается один раз (app/clip_groups.py).
    clip_cards_count: int | None = None
    clips_count: int | None = None
    # Проект-серия/канал источника (app/projects.py); None — без проекта.
    project_id: int | None = None
    # Файл записи на месте? False — удалён (клипы и моменты живут), кадры и видео не запрашивать.
    has_file: bool | None = None
    # Человеческое название видео (app/nav.py), а не twitch-XXXX.mp4.
    display_name: str = ""


class SourceDownloadRequest(BaseModel):
    url: str
    quality: str = ""
    smotvibe_media_url: str = ""
    smotvibe_referer: str = ""
    smotvibe_audio_format_id: str = ""
    smotvibe_filename_label: str = ""
    # «Новое видео» из проекта: готовый источник сразу попадает в этот проект.
    project_id: int | None = None


class SmotvibeOptionsRequest(BaseModel):
    url: str


class SmotvibeOptionRead(BaseModel):
    id: str
    provider: str
    season: str
    episode: str
    translation: str
    quality: str
    title: str
    duration_sec: float
    media_url: str
    referer: str
    audio_format_id: str
    filename_label: str


class AnalyzeSourceRequest(BaseModel):
    provider: Literal["mock", "action", "polza", "gemini", "artemox"] | None = Field(
        default=None,
        description="Analyzer provider override. Defaults to AI_VIDEO_PROVIDER.",
    )
    model: str | None = Field(default=None, description="Optional model override.")
    prompt: str | None = Field(default=None, description="Analyzer prompt override.")
    prompt_preset_id: int | None = Field(default=None, description="Saved analysis prompt preset id.")
    use_transcript: bool | None = Field(
        default=None,
        description="Feed a Whisper speech transcript to the video LLM. Null = default (on for Gemini).",
    )
    format: Literal["9:16", "1:1", "4:5", "16:9"] | None = Field(
        default=None,
        description=(
            "Output format the clips are picked for (app/formats.py). Null = app setting analysis_format, "
            "else 9:16. 16:9 — regular horizontal video, not Shorts: longer clips allowed "
            "(LANDSCAPE_MAX_CLIP_SECONDS), no reframing focus for a horizontal source."
        ),
    )
    merge_audio_for_analysis: bool = Field(
        default=False,
        description="Merge source audio tracks before sending video to the analyzer.",
    )
    preprocess_for_analysis: bool = Field(
        default=False,
        description="Create an optimized analysis copy before uploading video to the LLM.",
    )
    downscale_for_analysis: bool = Field(default=True, description="Downscale the analysis copy.")
    analysis_max_dimension: int = Field(default=1080, ge=144, le=2160)
    reduce_fps_for_analysis: bool = Field(default=False, description="Reduce FPS in the analysis copy.")
    analysis_target_fps: float = Field(default=12, ge=1, le=60)
    analysis_video_crf: int = Field(default=26, ge=18, le=40)


class SourceRedownloadRequest(BaseModel):
    quality: str = ""


class SourceRenamePayload(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class CutStrategyPayload(BaseModel):
    strategy: str = ""


class SourceFocusPresetPayload(BaseModel):
    focus_preset: str | None = None
    focus_strategy: str | None = None


class CropRect(BaseModel):
    x: float
    y: float
    w: float
    h: float


class SourceCropPayload(BaseModel):
    crop: CropRect | None = None


def build_sources_router(
    auth_dep: Any,
    get_store: Callable[[], Any],
    get_source_ingestor: Callable[[], Any],
    get_download_service: Callable[[], Any],
    get_video_analysis_service: Callable[[], Any],
    download_source_url: Callable[..., Any],
) -> APIRouter:
    router = APIRouter()

    @router.get("/media/sources/{source_id}", include_in_schema=False)
    def source_media(source_id: int, _auth: auth_dep) -> FileResponse:
        try:
            source = get_store().get_source(source_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        path = Path(source["local_path"])
        try:
            path.resolve(strict=False).relative_to(settings.source_dir.resolve())
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="source media not found") from exc
        if not path.exists():
            raise HTTPException(status_code=404, detail="source media not found")
        return FileResponse(path)

    @router.post("/ui/ai-analyses/{analysis_id}/delete", include_in_schema=False)
    def ui_delete_ai_analysis(
        analysis_id: int,
        _auth: auth_dep,
        next: Annotated[str, Form()] = "/sources",
    ) -> RedirectResponse:
        try:
            get_store().delete_ai_analysis(analysis_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse(url=_safe_next(next), status_code=303)

    @router.post(
        "/api/smotvibe/options",
        response_model=list[SmotvibeOptionRead],
        tags=["Sources"],
        summary="Discover Smotvibe seasons, episodes, and voice tracks",
    )
    def api_smotvibe_options(payload: SmotvibeOptionsRequest, _auth: auth_dep) -> list[dict]:
        try:
            return [option.to_dict() for option in discover_smotvibe_download_options(payload.url)]
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post(
        "/api/sources",
        response_model=SourceRead,
        tags=["Sources"],
        summary="Ingest a source video",
        description=(
            "Accepts either multipart `file` upload or JSON body `{ \"url\": \"...\" }`. "
            "Direct mp4/mov/webm URLs, YouTube URLs, Twitch URLs, and Smotvibe player pages are supported. "
            "A URL is downloaded as a background task (same as `/api/sources/download`: visible in "
            "`/api/tasks/active`, cancellable); the request waits for it and returns the source. "
            "With `wait: false` it returns the download task at once (202)."
        ),
    )
    async def api_create_source(request: Request, _auth: auth_dep) -> dict:
        try:
            if "multipart/form-data" in request.headers.get("content-type", ""):
                form = await request.form()
                upload = form.get("file")
                url = str(form.get("url") or "").strip()
                if upload is not None and hasattr(upload, "file"):
                    try:
                        # Копия многогигабайтного файла, sha256 и ffprobe — в потоке, не в event loop.
                        return await asyncio.to_thread(
                            get_source_ingestor().ingest_upload, upload.file, upload.filename or "source.mp4"
                        )
                    finally:
                        await upload.close()
                if url:
                    return await download_source_url(url, form)
                raise ValueError("multipart request must include file or url")
            payload = await request.json()
            url = str(payload.get("url") or "").strip()
            if not url:
                raise ValueError("json request must include url")
            return await download_source_url(url, payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post(
        "/api/sources/download",
        tags=["Sources"],
        summary="Start a background source download",
        description=(
            "Returns immediately with a download task. Progress and cancellation are "
            "exposed through `/api/tasks/active` and `/api/downloads/{id}/cancel`."
        ),
    )
    def api_start_download(payload: SourceDownloadRequest, _auth: auth_dep) -> dict:
        try:
            return get_download_service().start(
                payload.url,
                quality=payload.quality,
                selection={
                    "media_url": payload.smotvibe_media_url,
                    "referer": payload.smotvibe_referer,
                    "audio_format_id": payload.smotvibe_audio_format_id,
                    "filename_label": payload.smotvibe_filename_label,
                },
                on_source=_assign_to_project(payload.project_id),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def _assign_to_project(project_id: int | None):
        """Что сделать с готовым источником: положить в проект, откуда нажали «Новое видео»."""
        if project_id is None:
            return None

        def assign(source: dict) -> None:
            from app import projects as P

            P.set_source_project(get_store(), int(source["id"]), int(project_id))

        return assign

    @router.post(
        "/api/sources/{source_id}/redownload",
        tags=["Sources"],
        summary="Скачать удалённую запись заново в тот же источник (в фоне)",
        description=(
            "Возвращает задачу загрузки сразу, как `/api/sources/download`, но файл встаёт в этот же "
            "источник: моменты, клипы и монтаж остаются на месте. Прогресс — `/api/tasks/active`, "
            "отмена — `/api/downloads/{id}/cancel`."
        ),
    )
    def api_redownload_source(source_id: int, _auth: auth_dep, payload: SourceRedownloadRequest | None = None) -> dict:
        try:
            source = get_store().get_source(source_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if source.get("has_file"):
            raise HTTPException(status_code=409, detail="Файл записи уже на месте")
        url = str(source.get("original_url") or "").strip()
        if not url:
            raise HTTPException(status_code=400, detail="У записи нет ссылки: её загружали файлом")
        try:
            return get_download_service().start(url, quality=payload.quality if payload else "", restore_source_id=source_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/downloads", tags=["Sources"], summary="List source download tasks")
    def api_downloads(_auth: auth_dep) -> list[dict]:
        return get_download_service().list_tasks()

    @router.post("/api/downloads/{download_id}/cancel", tags=["Sources"], summary="Cancel a source download")
    def api_cancel_download(download_id: int, _auth: auth_dep) -> dict:
        try:
            return get_download_service().cancel(download_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get(
        "/api/sources",
        response_model=list[SourceRead],
        tags=["Sources"],
        summary="List ingested sources",
    )
    def api_sources(_auth: auth_dep, lite: bool = False) -> list[dict]:
        # lite — для экрана проектов: ffprobe в metadata_json даёт 90 % веса ответа (1 МБ на 243 источника).
        from app.nav import with_titles

        sources = with_titles(get_store(), get_store().list_sources())
        if lite:
            return [{**source, "metadata_json": ""} for source in sources]
        return sources

    @router.get(
        "/api/sources/{source_id}",
        tags=["Sources"],
        summary="Get source details",
    )
    def api_source(source_id: int, _auth: auth_dep) -> dict:
        try:
            get_store().ensure_clip_plans_for_source(source_id)
            from app.nav import with_titles

            return with_titles(get_store(), [get_store().get_source(source_id, include_related=True)])[0]
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.delete(
        "/api/sources/{source_id}",
        tags=["Sources"],
        summary="Delete a source and all derived data",
    )
    def api_delete_source(source_id: int, _auth: auth_dep) -> dict:
        try:
            return get_store().delete_source(source_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.patch("/api/sources/{source_id}", tags=["Sources"], summary="Rename a source")
    def api_rename_source(source_id: int, payload: SourceRenamePayload, _auth: auth_dep) -> dict:
        try:
            return get_store().update_source(source_id, original_filename=payload.name.strip())
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/api/focus-presets", tags=["Render"], summary="List autofocus presets")
    def api_focus_presets(_auth: auth_dep) -> dict:
        return {"presets": list_focus_presets(), "strategies": list_focus_strategies()}

    @router.get("/api/cut-strategies", tags=["Sources"], summary="List cut-refinement hypotheses")
    def api_cut_strategies(_auth: auth_dep) -> list[dict]:
        return list_cut_strategies()

    @router.post("/api/sources/{source_id}/refine-cuts", tags=["Sources"], summary="Re-snap clip boundaries")
    def api_refine_cuts(source_id: int, payload: CutStrategyPayload, _auth: auth_dep) -> dict:
        try:
            return get_store().refine_source_cuts(source_id, payload.strategy.strip() or None)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.patch("/api/sources/{source_id}/focus-preset", tags=["Render"], summary="Set the autofocus preset/strategy")
    def api_set_source_focus_preset(source_id: int, payload: SourceFocusPresetPayload, _auth: auth_dep) -> dict:
        fields = {}
        if payload.focus_preset is not None:
            fields["focus_preset"] = payload.focus_preset.strip()
        if payload.focus_strategy is not None:
            fields["focus_strategy"] = payload.focus_strategy.strip()
        if not fields:
            return get_store().get_source(source_id)
        try:
            return get_store().update_source(source_id, **fields)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/api/sources/{source_id}/detect-crop", tags=["Sources"], summary="Auto-detect content bars")
    def api_detect_crop(source_id: int, _auth: auth_dep) -> dict:
        try:
            source = get_store().get_source(source_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        try:
            crop = detect_content_crop(source)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"crop": crop}

    @router.post("/api/sources/{source_id}/detect-key", tags=["Sources"], summary="Auto-detect a green-screen chroma key")
    def api_detect_key(source_id: int, _auth: auth_dep) -> dict:
        from app.chroma_key import detect_key

        try:
            source = get_store().get_source(source_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        try:
            return {"chroma_key": detect_key(source)}
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.patch("/api/sources/{source_id}/chroma-key", tags=["Sources"], summary="Set or clear the chroma key")
    def api_set_chroma_key(source_id: int, payload: dict, _auth: auth_dep) -> dict:
        """``{"chroma_key": {color, similarity, blend} | null}`` — null выключает ключ."""
        try:
            return get_store().update_source(source_id, chroma_key=payload.get("chroma_key"))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/api/sources/{source_id}/audio", tags=["Sources"], summary="Audio tracks of the source and the chosen ones")
    def api_source_audio(source_id: int, _auth: auth_dep) -> dict:
        from app.source_audio import audio_view

        try:
            return audio_view(get_store().get_source(source_id))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.patch("/api/sources/{source_id}/audio", tags=["Sources"], summary="Choose (and mix) the source audio tracks")
    def api_set_source_audio(source_id: int, payload: dict, _auth: auth_dep) -> dict:
        """``{"audio_tracks": [0, 1]}`` — смешать игру и микрофон; ``[1]`` — только вторую; null — первая.
        ``[{"track": 1, "gain_db": 12}]`` — своя громкость дорожки (``"auto"`` — замерить);
        ``{"speech_tracks": [1]}`` — дорожка для распознавания речи (null — как audio_tracks)."""
        from app.source_audio import _KEEP, set_source_audio

        tracks = payload.get("audio_tracks", payload.get("tracks", _KEEP))
        try:
            return set_source_audio(get_store(), source_id, tracks, payload.get("speech_tracks", _KEEP))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.patch("/api/sources/{source_id}/crop", tags=["Sources"], summary="Set the content crop rect")
    def api_set_crop(source_id: int, payload: SourceCropPayload, _auth: auth_dep) -> dict:
        crop = payload.crop.model_dump() if payload.crop else None
        try:
            return get_store().update_source(source_id, content_crop=crop)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get(
        "/api/sources/{source_id}/storyboard",
        tags=["Sources"],
        summary="Storyboard thumbnails sampled across the video",
    )
    def api_source_storyboard(source_id: int, _auth: auth_dep) -> dict:
        try:
            source = get_store().get_source(source_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        try:
            meta = ensure_storyboard(source)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "count": meta["count"],
            "interval_sec": meta["interval_sec"],
            "frames": [f"/media/sources/{source_id}/storyboard/{i}" for i in range(meta["count"])],
        }

    @router.get("/media/sources/{source_id}/thumb", include_in_schema=False)
    def media_source_thumb(source_id: int, _auth: auth_dep) -> FileResponse:
        try:
            return FileResponse(source_thumb(get_store().get_source(source_id)), headers=THUMB_CACHE)
        except (KeyError, ValueError, OSError, subprocess.SubprocessError) as exc:
            raise HTTPException(status_code=404, detail="source thumb not found") from exc

    @router.get("/media/sources/{source_id}/storyboard/{index}", include_in_schema=False)
    def media_source_storyboard_frame(source_id: int, index: int, _auth: auth_dep) -> FileResponse:
        path = frame_path(source_id, index)
        if not path.exists():
            raise HTTPException(status_code=404, detail="frame not found")
        return FileResponse(path, media_type="image/jpeg")

    @router.get(
        "/api/sources/{source_id}/stats",
        tags=["Sources"],
        summary="Get source project stats",
    )
    def api_source_stats(source_id: int, _auth: auth_dep) -> dict:
        try:
            return get_store().get_source_stats(source_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get(
        "/api/sources/{source_id}/clip-plans",
        tags=["Render"],
        summary="List clip plans for a source",
    )
    def api_source_clip_plans(source_id: int, _auth: auth_dep) -> list[dict]:
        try:
            get_store().get_source(source_id)
            return get_store().list_clip_plans(source_id=source_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get(
        "/api/sources/{source_id}/transcript",
        tags=["Sources"],
        summary="Source transcript (local Whisper in the background, cached; 202 + progress while it runs)",
    )
    def api_source_transcript(
        source_id: int,
        _auth: auth_dep,
        response: Response,
        refresh: bool = False,
        from_sec: float | None = None,
        to_sec: float | None = None,
        wait: float | None = None,
    ) -> dict:
        """Распознавание идёт фоном (одно на источник); запрос ждёт его не дольше ``wait``
        (по умолчанию 45 с, максимум 55) — иначе 202 и ``{status: running, done_min,
        total_min, retry_after_sec}``: повтори тот же запрос. Готово — реплики из кэша."""
        from app.manual_clips import transcript_job

        limit = None if wait is None else min(55.0, max(0.0, wait))
        try:
            out = transcript_job(get_store(), source_id, refresh=refresh, wait=limit)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if out.get("status") == "running":
            response.status_code = 202
            response.headers["Retry-After"] = str(out["retry_after_sec"])
            return out
        if from_sec is not None or to_sec is not None:
            lo, hi = from_sec or 0.0, to_sec if to_sec is not None else float("inf")
            out["cues"] = [c for c in out["cues"] if float(c["end"]) > lo and float(c["start"]) < hi]
        return out

    @router.post(
        "/api/sources/{source_id}/analyze",
        tags=["Sources"],
        summary="Run AI analysis for a source",
    )
    def api_analyze_source(
        source_id: int,
        request: Request,
        _auth: auth_dep,
        payload: AnalyzeSourceRequest | None = None,
    ) -> dict:
        from app.stream_questions_api import refuse_agent_montage

        refuse_agent_montage(request, get_store(), source_id)
        try:
            return get_video_analysis_service().run_analysis(
                source_id,
                provider=payload.provider if payload else None,
                model=payload.model if payload else None,
                prompt=_analysis_prompt_from_payload(payload),
                use_transcript=payload.use_transcript if payload else None,
                output_format=payload.format if payload else None,
                preprocessing={
                    "enabled": bool(payload.preprocess_for_analysis) if payload else False,
                    "merge_audio_for_analysis": bool(payload.merge_audio_for_analysis)
                    if payload else False,
                    "downscale": bool(payload.downscale_for_analysis) if payload else True,
                    "max_dimension": payload.analysis_max_dimension if payload else 1080,
                    "reduce_fps": bool(payload.reduce_fps_for_analysis) if payload else False,
                    "target_fps": payload.analysis_target_fps if payload else 12,
                    "video_crf": payload.analysis_video_crf if payload else 26,
                },
            )
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def _analysis_prompt_from_payload(payload: AnalyzeSourceRequest | None) -> str | None:
        if not payload:
            return None
        if payload.prompt_preset_id:
            preset = get_store().get_prompt_preset(payload.prompt_preset_id)
            if preset["task"] != "analysis":
                raise ValueError("prompt preset must be for analysis")
            return preset["prompt"]
        return payload.prompt if payload.prompt else None

    @router.delete("/api/ai-analyses/{analysis_id}", tags=["Sources"], summary="Delete an AI analysis generation")
    def api_delete_ai_analysis(analysis_id: int, _auth: auth_dep) -> dict:
        try:
            deleted = get_store().delete_ai_analysis(analysis_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"deleted": True, "analysis": deleted}

    @router.post("/api/ai-analyses/{analysis_id}/cancel", tags=["Sources"], summary="Cancel a running AI analysis")
    def api_cancel_ai_analysis(analysis_id: int, _auth: auth_dep) -> dict:
        try:
            analysis = get_store().request_ai_analysis_cancel(analysis_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"cancelling": True, "analysis": analysis}

    @router.get(
        "/api/sources/{source_id}/frame",
        tags=["Clip file"],
        summary="A still of the source at t: full frame, or the 9:16 window at x",
    )
    def api_source_frame(source_id: int, _auth: auth_dep, t: float = 0.0, x: float | None = None, w: int = 960) -> FileResponse:
        """For deciding framing without downloading the source. With ``x`` you get
        exactly the 9:16 window the render keeps for that focus position."""
        try:
            source = get_store().get_source(source_id)
            from app.storyboard import plan_thumb, source_frame

            path = plan_thumb(source, t, x) if x is not None else source_frame(source, t, w)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return FileResponse(path, media_type="image/jpeg")

    return router
