"""Активность и кнопка «Авто»: прогоны автоматики, запуск из формы, текущие и недавние задачи.

Роутер собирается функцией, как ``app/cuts_api.py``: ``main.py`` передаёт зависимость авторизации и доступ к хранилищу
и сервисам лямбдами (тесты подменяют ``app.main.store`` и другие имена)."""

# Без from __future__ import annotations: FastAPI читает аннотации вида
# _auth: auth_dep в момент объявления, а auth_dep — локальная переменная.
from typing import Annotated, Any, Callable

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.api_common import _optional_form_int, _parse_schedule_dt
from app.auth import is_agent_request
from app.settings_api import _settings_panel_data


def build_automation_router(
    auth_dep: Any,
    get_store: Callable[[], Any],
    get_automation_service: Callable[[], Any],
    get_download_service: Callable[[], Any],
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/auto/runs", tags=["Jobs"], summary="List recent automation runs")
    def api_auto_runs(_auth: auth_dep) -> list[dict]:
        return get_automation_service().list_runs()

    @router.post("/ui/auto/start", include_in_schema=False)
    def ui_auto_start(
        request: Request,
        _auth: auth_dep,
        url: Annotated[str, Form()],
        smotvibe_media_url: Annotated[str, Form()] = "",
        smotvibe_referer: Annotated[str, Form()] = "",
        smotvibe_audio_format_id: Annotated[str, Form()] = "",
        smotvibe_filename_label: Annotated[str, Form()] = "",
        provider: Annotated[str, Form()] = "",
        model: Annotated[str, Form()] = "",
        prompt_preset_id: Annotated[int, Form()] = 0,
        use_transcript: Annotated[bool, Form()] = True,
        ffmpeg_preset_id: Annotated[str, Form()] = "",
        subtitle_profile_id: Annotated[str, Form()] = "",
        subtitle_provider: Annotated[str, Form()] = "",
        subtitle_margin_v: Annotated[str, Form()] = "",
        banner_id: Annotated[str, Form()] = "",
        banner_height_frac: Annotated[str, Form()] = "",
        banner_y_frac: Annotated[str, Form()] = "",
        mirror: Annotated[bool, Form()] = False,
        music_track_id: Annotated[str, Form()] = "",
        music_volume: Annotated[str, Form()] = "",
        use_subtitles: Annotated[bool, Form()] = False,
        use_banner: Annotated[bool, Form()] = False,
        use_music: Annotated[bool, Form()] = False,
        targets: Annotated[list[int], Form()] = [],
        privacy: Annotated[str, Form()] = "public",
        allow_comments: Annotated[bool, Form()] = True,
        schedule_start: Annotated[str, Form()] = "",
        interval_hours: Annotated[float, Form()] = 0,
        max_clips: Annotated[int, Form()] = 0,
        ai_metadata: Annotated[bool, Form()] = False,
        ai_montage: Annotated[bool, Form()] = False,
        project_id: Annotated[int, Form()] = 0,
    ) -> RedirectResponse:
        if not url.strip():
            raise HTTPException(status_code=400, detail="url is required")
        if project_id:
            try:
                from app.projects import get_project as get_channel_project

                get_channel_project(get_store(), project_id, include_sources=False)
            except KeyError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        defaults = _settings_panel_data(get_store())
        selected_prompt = ""
        if prompt_preset_id:
            try:
                preset = get_store().get_prompt_preset(prompt_preset_id)
            except KeyError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if preset["task"] != "analysis":
                raise HTTPException(status_code=400, detail="prompt preset must be for analysis")
            selected_prompt = preset["prompt"]

        selected_subtitle_profile_id = _optional_form_int(subtitle_profile_id) if use_subtitles else None
        if use_subtitles and not selected_subtitle_profile_id:
            selected_subtitle_profile_id = defaults["default_subtitle_profile_id"] or None
        selected_banner_id = _optional_form_int(banner_id) if use_banner else 0
        if use_banner and not selected_banner_id:
            selected_banner_id = defaults["default_banner_id"] or None
        volume_provided = str(music_volume).strip() != ""
        music_in_play = use_music or str(music_track_id).strip() != "" or volume_provided
        selected_music_track_id = _optional_form_int(music_track_id) if music_in_play else None
        selected_music_volume = float(music_volume) if volume_provided else None

        get_automation_service().start_run(
            url=url.strip(),
            project_id=project_id or None,  # пусто — серия источника или общий проект «Авто — без серии»
            smotvibe_selection={
                "media_url": smotvibe_media_url.strip(),
                "referer": smotvibe_referer.strip(),
                "audio_format_id": smotvibe_audio_format_id.strip(),
                "filename_label": smotvibe_filename_label.strip(),
            },
            analysis={"provider": provider.strip(), "model": model.strip(), "prompt": selected_prompt, "use_transcript": use_transcript},
            render={
                "ffmpeg_preset_id": _optional_form_int(ffmpeg_preset_id),
                "subtitle_profile_id": selected_subtitle_profile_id,
                "subtitle_provider": (subtitle_provider.strip().lower() or None) if use_subtitles else None,
                "subtitle_margin_v": _optional_form_int(subtitle_margin_v) if use_subtitles else None,
                "banner_id": selected_banner_id,
                "banner_height_frac": (float(banner_height_frac) if str(banner_height_frac).strip() else None) if use_banner else None,
                "banner_y_frac": (float(banner_y_frac) if str(banner_y_frac).strip() else None) if use_banner else None,
                "mirror": bool(mirror),
                "music_track_id": selected_music_track_id,
                "music_volume": selected_music_volume,
            },
            publish={
                "targets": targets,
                "privacy": privacy,
                "allow_comments": allow_comments,
                "start_at": _parse_schedule_dt(schedule_start),
                "interval_hours": interval_hours,
                "max_clips": max_clips,
                "ai_metadata": ai_metadata,
                "ai_montage": ai_montage,
            },
            owner_started=not is_agent_request(request),
        )
        return RedirectResponse(url="/auto", status_code=303)

    @router.get("/api/tasks/active", tags=["Jobs"], summary="List active UI tasks")
    def api_active_tasks(_auth: auth_dep) -> list[dict]:
        return _merge_tasks(get_store().list_active_tasks(), get_download_service().as_tasks())

    @router.get("/api/tasks", tags=["Jobs"], summary="List recent tasks (execution log)")
    def api_recent_tasks(_auth: auth_dep, limit: int = 80) -> list[dict]:
        limit = max(1, min(300, limit))
        return _merge_tasks(get_store().list_recent_tasks(limit), get_download_service().as_tasks())[:limit]

    def _merge_tasks(stored: list[dict], downloads: list[dict]) -> list[dict]:
        """Downloads live in memory, the rest in SQLite — the UI wants one feed."""
        return sorted([*downloads, *stored], key=lambda task: str(task.get("updated_at") or ""), reverse=True)

    return router
