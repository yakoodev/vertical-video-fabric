from __future__ import annotations

import json
import shutil
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app.ai.schema import ANALYSIS_RESPONSE_SCHEMA
from app.ai.service import VideoAnalysisService
from app.automation import AutomationService
from app.auth import AuthRequired, auth_required_handler, require_auth, token_is_valid
from app.crypto import CookieCipher
from app.db import Database
from app.default_pack import seed_default_pack
from app.focus_presets import list_focus_presets, list_focus_strategies
from app.default_prompts import BASE_ANALYSIS_PROMPT, DEFAULT_PUBLISHING_PROMPT, DEFAULT_SUBTITLE_PROMPT
from app.ingest import SourceIngestor, probe_media
from app.render import ClipRenderService
from app.settings import settings
from app.smotvibe import discover_smotvibe_download_options
from app.storyboard import ensure_storyboard, frame_path
from app.store import AppStore
from app.video_crop import detect_content_crop
from app.subtitles.gemini import gemini_subtitle_schema
from app.worker import JobWorker

settings.ensure_dirs()
settings.ensure_api_token()
db = Database(settings.db_path)
db.init()
store = AppStore(db, CookieCipher(settings.secret_key_path))
seed_default_pack(store)
worker = JobWorker(store)
source_ingestor = SourceIngestor(store)
video_analysis_service = VideoAnalysisService(store)
clip_render_service = ClipRenderService(store)
automation_service = AutomationService(store, source_ingestor, video_analysis_service, clip_render_service)
AuthDep = Annotated[None, Depends(require_auth)]


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.recover_interrupted_ai_analyses()
    worker.start()
    try:
        yield
    finally:
        worker.stop()


app = FastAPI(
    title="Vertical Video Fabric API",
    summary="Cookie/session-based autoposting for vertical videos.",
    description=(
        "FastAPI service for managing cookie-auth accounts and queueing video posts "
        "to YouTube and TikTok. Authenticate with `Authorization: Bearer <token>`."
    ),
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    openapi_tags=[
        {"name": "Accounts", "description": "Add and list saved platform sessions."},
        {"name": "Sources", "description": "Ingest source videos for AI analysis and rendering."},
        {"name": "Render", "description": "Manage ffmpeg presets, banners, subtitle profiles, and clips."},
        {"name": "Posts", "description": "Upload a source video and queue publication jobs."},
        {"name": "Jobs", "description": "Inspect queued, running, and completed jobs."},
    ],
)
app.add_exception_handler(AuthRequired, auth_required_handler)
templates = Jinja2Templates(directory=settings.root_dir / "app" / "templates")

PUBLISHING_METADATA_SCHEMA = {
    "type": "object",
    "required": ["title", "description"],
    "properties": {
        "title": {"type": "string", "maxLength": 100},
        "description": {"type": "string", "maxLength": 500},
    },
}


def _ui_context(active: str = "", **extra) -> dict:
    context = {"active": active, "settings_panel": _settings_panel_data()}
    context.update(extra)
    return context


def _settings_panel_data() -> dict:
    saved = store.list_app_settings(include_secrets=False)
    store.ensure_prompt_presets(
        {
            "analysis": ("Apex analysis", saved.get("analysis_prompt") or BASE_ANALYSIS_PROMPT),
            "publishing": ("Publishing metadata", saved.get("publishing_prompt") or DEFAULT_PUBLISHING_PROMPT),
            "subtitle": ("Apex subtitles", saved.get("subtitle_prompt") or DEFAULT_SUBTITLE_PROMPT),
        }
    )
    analysis_prompt_preset = store.get_default_prompt_preset("analysis")
    publishing_prompt_preset = store.get_default_prompt_preset("publishing")
    subtitle_prompt_preset = store.get_default_prompt_preset("subtitle")
    default_banner_id = _setting_int(saved.get("default_banner_id"))
    default_subtitle_profile_id = _setting_int(saved.get("default_subtitle_profile_id"))
    return {
        "accounts": store.list_accounts(),
        "presets": store.list_ffmpeg_presets(),
        "banners": store.list_banners(),
        "audio_tracks": store.list_audio_tracks(),
        "subtitle_profiles": store.list_subtitle_profiles(),
        "prompt_presets": store.list_prompt_presets(),
        "analysis_prompt_presets": store.list_prompt_presets("analysis"),
        "publishing_prompt_presets": store.list_prompt_presets("publishing"),
        "subtitle_prompt_presets": store.list_prompt_presets("subtitle"),
        "analysis_prompt": (analysis_prompt_preset or {}).get("prompt") or saved.get("analysis_prompt") or BASE_ANALYSIS_PROMPT,
        "publishing_prompt": (publishing_prompt_preset or {}).get("prompt") or saved.get("publishing_prompt") or DEFAULT_PUBLISHING_PROMPT,
        "subtitle_prompt": (subtitle_prompt_preset or {}).get("prompt") or saved.get("subtitle_prompt") or DEFAULT_SUBTITLE_PROMPT,
        "default_analysis_prompt_preset_id": (analysis_prompt_preset or {}).get("id") or 0,
        "default_ai_provider": saved.get("default_ai_provider") or settings.ai_video_provider,
        "default_ai_model": saved.get("default_ai_model") or "",
        "default_subtitle_provider": saved.get("default_subtitle_provider") or settings.subtitle_provider,
        "default_subtitle_model": saved.get("default_subtitle_model") or "",
        "default_banner_id": default_banner_id,
        "default_subtitle_profile_id": default_subtitle_profile_id,
        "global_proxy_configured": bool(store.get_global_proxy_url()),
        "global_proxy_display": "configured" if store.get_global_proxy_url() else "direct",
        "analysis_schema_json": json.dumps(ANALYSIS_RESPONSE_SCHEMA, ensure_ascii=False, indent=2),
        "publishing_schema_json": json.dumps(PUBLISHING_METADATA_SCHEMA, ensure_ascii=False, indent=2),
        "subtitle_schema_json": json.dumps(gemini_subtitle_schema(), ensure_ascii=False, indent=2),
    }


def _setting_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _source_analysis_prompt_preset_id(source: dict, presets: list[dict], fallback_id: int) -> int:
    source_type = str(source.get("source_type") or "").lower()
    original_url = str(source.get("original_url") or "").lower()
    original_filename = str(source.get("original_filename") or "").lower()
    haystack = f"{source_type} {original_url} {original_filename}"
    if any(marker in haystack for marker in ("smotvibe", "yummyani", "anime")):
        for preset in presets:
            if str(preset.get("label") or "").strip().lower() == "anime analysis":
                return int(preset["id"])
    return fallback_id


class AccountCreate(BaseModel):
    platform: Literal["youtube", "tiktok", "instagram"] = Field(description="Target platform.")
    label: str = Field(description="Human-readable account label.", examples=["youtube-main"])
    proxy_url: str = Field(
        default="",
        description=(
            "Optional per-account proxy URL used for publishing from this account. "
            "Example: http://user:pass@host:port. Stored encrypted and never returned raw."
        ),
        examples=["http://user:pass@host:port"],
    )
    cookie: str = Field(
        description="Raw Cookie header or Netscape cookie export. Never returned by API responses.",
        examples=["SID=...; HSID=...; SSID=...; APISID=...; SAPISID=..."],
    )


class AccountRead(BaseModel):
    id: int
    platform: str
    label: str
    cookie_count: int
    has_required_cookies: bool
    missing_cookies: str
    proxy_configured: bool
    proxy_display: str
    created_at: str
    updated_at: str


class JobTargetRead(BaseModel):
    id: int
    job_id: int
    account_id: int
    platform: str
    status: str
    remote_id: str
    remote_url: str
    error: str
    response_json: str
    created_at: str
    updated_at: str
    started_at: str | None = None
    finished_at: str | None = None
    account_label: str


class JobRead(BaseModel):
    id: int
    clip_id: int | None = None
    status: str
    title: str
    description: str
    privacy: str
    allow_comments: bool
    scheduled_at: str | None = None
    source_filename: str
    source_path: str
    source_sha256: str
    source_size_bytes: int
    created_at: str
    updated_at: str
    started_at: str | None = None
    finished_at: str | None = None
    error: str
    result_json: str
    targets: list[JobTargetRead]


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
    clips_count: int | None = None


class SourceCreateRequest(BaseModel):
    url: str
    smotvibe_media_url: str = ""
    smotvibe_referer: str = ""
    smotvibe_audio_format_id: str = ""
    smotvibe_filename_label: str = ""


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


class FfmpegPresetPayload(BaseModel):
    label: str
    description: str = ""
    output_width: int = 1080
    output_height: int = 1920
    fps: float = 30
    video_codec: str = "libx264"
    audio_codec: str = "aac"
    video_bitrate: str = ""
    audio_bitrate: str = ""
    audio_mix_mode: Literal["primary", "secondary", "mix"] = "primary"
    audio_primary_stream: int = 0
    audio_primary_volume: float = 1
    audio_secondary_stream: int | None = None
    audio_secondary_volume: float = 1
    scale_mode: Literal["cover", "contain", "blur_background"] = "cover"
    crop_anchor: Literal["center", "top", "bottom"] = "center"
    banner_id: int | None = None
    subtitle_profile_id: int | None = None
    music_track_id: int | None = None
    music_volume: float = 0.25
    music_loop: bool = True
    music_fade_in_sec: float = 0
    music_fade_out_sec: float = 0
    music_duck: bool = True
    music_duck_amount: float = 0.6
    color_style: Literal["none", "warm", "cold", "cinematic", "vibrant", "noir", "vintage"] = "none"
    color_strength: float = 1
    vignette: float = 0
    grain: float = 0
    extra: dict = Field(default_factory=dict)


class FfmpegPresetPatchPayload(BaseModel):
    label: str | None = None
    description: str | None = None
    output_width: int | None = None
    output_height: int | None = None
    fps: float | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    video_bitrate: str | None = None
    audio_bitrate: str | None = None
    audio_mix_mode: Literal["primary", "secondary", "mix"] | None = None
    audio_primary_stream: int | None = None
    audio_primary_volume: float | None = None
    audio_secondary_stream: int | None = None
    audio_secondary_volume: float | None = None
    scale_mode: Literal["cover", "contain", "blur_background"] | None = None
    crop_anchor: Literal["center", "top", "bottom"] | None = None
    banner_id: int | None = None
    subtitle_profile_id: int | None = None
    music_track_id: int | None = None
    music_volume: float | None = None
    music_loop: bool | None = None
    music_fade_in_sec: float | None = None
    music_fade_out_sec: float | None = None
    music_duck: bool | None = None
    music_duck_amount: float | None = None
    color_style: Literal["none", "warm", "cold", "cinematic", "vibrant", "noir", "vintage"] | None = None
    color_strength: float | None = None
    vignette: float | None = None
    grain: float | None = None
    extra: dict | None = None


class AudioTrackPatchPayload(BaseModel):
    label: str | None = None
    volume: float | None = None


class BannerPatchPayload(BaseModel):
    label: str | None = None
    position: Literal["top", "center", "bottom", "custom"] | None = None
    x: int | None = None
    y: int | None = None
    opacity: float | None = None


class SubtitleProfilePayload(BaseModel):
    label: str
    provider: Literal["mock", "whisper", "polza", "gemini", "artemox"] = "mock"
    model: str = "openai/gpt-4o-transcribe"
    language: str = ""
    timing_offset_sec: float | None = None
    font_family: str = "Arial"
    font_size: int = 64
    primary_color: str = "#FFFFFF"
    active_word_color: str = "#FACC15"
    outline_color: str = "#111827"
    back_color: str = "#000000"
    alignment: int = 2
    margin_v: int = 160
    max_words_per_line: int = 5
    uppercase: bool = False
    outline_width: float = 5
    shadow: float = 1


class SubtitleProfilePatchPayload(BaseModel):
    label: str | None = None
    provider: Literal["mock", "whisper", "polza", "gemini", "artemox"] | None = None
    model: str | None = None
    language: str | None = None
    timing_offset_sec: float | None = None
    font_family: str | None = None
    font_size: int | None = None
    primary_color: str | None = None
    active_word_color: str | None = None
    outline_color: str | None = None
    back_color: str | None = None
    alignment: int | None = None
    margin_v: int | None = None
    max_words_per_line: int | None = None
    uppercase: bool | None = None
    outline_width: float | None = None
    shadow: float | None = None


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


class ClipPostRequest(BaseModel):
    title: str = ""
    description: str = ""
    targets: list[int]
    privacy: Literal["public", "unlisted", "private"] = "public"
    allow_comments: bool = True
    scheduled_at: str = ""


class ClipPlanSegmentCreateRequest(BaseModel):
    start_sec: float
    end_sec: float
    title: str = "Segment"


class ClipPlanSegmentMoveRequest(BaseModel):
    direction: Literal["up", "down"]


@app.get("/", include_in_schema=False)
def root(_auth: AuthDep) -> RedirectResponse:
    return RedirectResponse(url="/projects", status_code=303)


@app.get("/login", response_class=HTMLResponse, include_in_schema=False)
def login_page(request: Request, next: str = "/projects") -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "login.html",
        {"active": "", "next": _safe_next(next), "error": ""},
    )


@app.post("/login", response_class=HTMLResponse, include_in_schema=False)
def login_submit(
    request: Request,
    token: Annotated[str, Form()],
    next: Annotated[str, Form()] = "/projects",
):
    safe_next = _safe_next(next)
    if not token_is_valid(token):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"active": "", "next": safe_next, "error": "Invalid token"},
            status_code=401,
        )
    response = RedirectResponse(url=safe_next, status_code=303)
    response.set_cookie(
        settings.auth_cookie_name,
        token,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
    )
    return response


@app.post("/logout", include_in_schema=False)
def logout() -> RedirectResponse:
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(settings.auth_cookie_name)
    return response


@app.get("/docs", response_class=HTMLResponse, include_in_schema=False)
def swagger_docs(_auth: AuthDep) -> HTMLResponse:
    return get_swagger_ui_html(
        openapi_url="/openapi.json",
        title="Vertical Video Fabric API Docs",
        swagger_ui_parameters={
            "persistAuthorization": True,
            "displayRequestDuration": True,
            "filter": True,
            "tryItOutEnabled": True,
        },
    )


@app.get("/openapi.json", include_in_schema=False)
def openapi_json(_auth: AuthDep) -> JSONResponse:
    return JSONResponse(app.openapi())


@app.post("/ui/accounts", include_in_schema=False)
def ui_add_account(
    _auth: AuthDep,
    platform: Annotated[str, Form()],
    label: Annotated[str, Form()],
    cookie: Annotated[str, Form()],
    proxy_url: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "/accounts",
) -> RedirectResponse:
    try:
        store.upsert_account(platform, label, cookie, proxy_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=_safe_next(next), status_code=303)


@app.post("/ui/accounts/{account_id}/delete", include_in_schema=False)
def ui_delete_account(
    account_id: int,
    _auth: AuthDep,
    next: Annotated[str, Form()] = "/accounts",
) -> RedirectResponse:
    try:
        store.delete_account(account_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RedirectResponse(url=_safe_next(next), status_code=303)


@app.post("/ui/sources/upload", include_in_schema=False)
async def ui_upload_source(
    _auth: AuthDep,
    file: Annotated[UploadFile, File()],
) -> RedirectResponse:
    try:
        source = source_ingestor.ingest_upload(file.file, file.filename or "source.mp4")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        await file.close()
    return RedirectResponse(url=f"/sources/{source['id']}", status_code=303)


@app.post("/ui/sources/url", include_in_schema=False)
def ui_add_source_url(
    _auth: AuthDep,
    url: Annotated[str, Form()],
    smotvibe_media_url: Annotated[str, Form()] = "",
    smotvibe_referer: Annotated[str, Form()] = "",
    smotvibe_audio_format_id: Annotated[str, Form()] = "",
    smotvibe_filename_label: Annotated[str, Form()] = "",
) -> RedirectResponse:
    try:
        source = _ingest_source_url(
            url,
            smotvibe_media_url=smotvibe_media_url,
            smotvibe_referer=smotvibe_referer,
            smotvibe_audio_format_id=smotvibe_audio_format_id,
            smotvibe_filename_label=smotvibe_filename_label,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/sources/{source['id']}", status_code=303)


def _ingest_source_url(
    url: str,
    *,
    smotvibe_media_url: str = "",
    smotvibe_referer: str = "",
    smotvibe_audio_format_id: str = "",
    smotvibe_filename_label: str = "",
) -> dict:
    if smotvibe_media_url.strip():
        return source_ingestor.ingest_smotvibe_selection(
            url,
            media_url=smotvibe_media_url,
            referer=smotvibe_referer,
            audio_format_id=smotvibe_audio_format_id,
            filename_label=smotvibe_filename_label,
        )
    return source_ingestor.ingest_url(url)


@app.post("/ui/sources/{source_id}/analyze", include_in_schema=False)
def ui_analyze_source(
    source_id: int,
    _auth: AuthDep,
    provider: Annotated[str, Form()] = "",
    model: Annotated[str, Form()] = "",
    prompt: Annotated[str, Form()] = "",
    prompt_preset_id: Annotated[int, Form()] = 0,
    preprocess_for_analysis: Annotated[bool, Form()] = False,
    merge_audio_for_analysis: Annotated[bool, Form()] = False,
    downscale_for_analysis: Annotated[bool, Form()] = False,
    analysis_max_dimension: Annotated[int, Form()] = 1080,
    reduce_fps_for_analysis: Annotated[bool, Form()] = False,
    analysis_target_fps: Annotated[float, Form()] = 12,
    analysis_video_crf: Annotated[int, Form()] = 26,
    next: Annotated[str, Form()] = "",
) -> RedirectResponse:
    try:
        selected_prompt = prompt.strip()
        if prompt_preset_id:
            preset = store.get_prompt_preset(prompt_preset_id)
            if preset["task"] != "analysis":
                raise ValueError("prompt preset must be for analysis")
            selected_prompt = preset["prompt"]
        video_analysis_service.run_analysis(
            source_id,
            provider=provider or None,
            model=model or None,
            prompt=selected_prompt or None,
            preprocessing={
                "enabled": bool(preprocess_for_analysis),
                "merge_audio_for_analysis": bool(merge_audio_for_analysis),
                "downscale": bool(downscale_for_analysis),
                "max_dimension": analysis_max_dimension,
                "reduce_fps": bool(reduce_fps_for_analysis),
                "target_fps": analysis_target_fps,
                "video_crf": analysis_video_crf,
            },
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=_safe_next(next or f"/sources/{source_id}/studio?stage=preview"), status_code=303)


@app.get("/media/sources/{source_id}", include_in_schema=False)
def source_media(source_id: int, _auth: AuthDep) -> FileResponse:
    try:
        source = store.get_source(source_id)
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


@app.post("/ui/segments/{segment_id}/render", include_in_schema=False)
def ui_render_segment(
    segment_id: int,
    _auth: AuthDep,
    ffmpeg_preset_id: Annotated[int | None, Form()] = None,
    subtitle_profile_id: Annotated[int | None, Form()] = None,
) -> RedirectResponse:
    try:
        clip = clip_render_service.render_segment(
            segment_id,
            ffmpeg_preset_id=ffmpeg_preset_id,
            subtitle_profile_id=subtitle_profile_id,
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/clips/{clip['id']}", status_code=303)


@app.post("/ui/sources/{source_id}/render-plans", include_in_schema=False)
def ui_render_clip_plans(
    source_id: int,
    _auth: AuthDep,
    clip_plan_ids: Annotated[list[int], Form()] = [],
    ffmpeg_preset_id: Annotated[str, Form()] = "",
    subtitle_profile_id: Annotated[str, Form()] = "",
    banner_id: Annotated[str, Form()] = "",
    music_track_id: Annotated[str, Form()] = "",
    music_volume: Annotated[str, Form()] = "",
    use_subtitles: Annotated[bool, Form()] = False,
    use_banner: Annotated[bool, Form()] = False,
    use_music: Annotated[bool, Form()] = False,
    subtitle_offset_sec: Annotated[str, Form()] = "",
) -> RedirectResponse:
    if not clip_plan_ids:
        raise HTTPException(status_code=400, detail="select at least one clip plan")
    defaults = _settings_panel_data()
    selected_subtitle_profile_id = _optional_form_int(subtitle_profile_id) if use_subtitles else None
    if use_subtitles and not selected_subtitle_profile_id:
        selected_subtitle_profile_id = defaults["default_subtitle_profile_id"] or None
    selected_banner_id = _optional_form_int(banner_id) if use_banner else 0
    if use_banner and not selected_banner_id:
        selected_banner_id = defaults["default_banner_id"] or None
    # Music defaults to whatever the preset defines. Touching any music control
    # (checkbox, track or volume) opts this render in. Track value semantics:
    # ""/"Из пресета" -> keep preset track, "0"/"Без музыки" -> disable, id -> use it.
    volume_provided = str(music_volume).strip() != ""
    music_in_play = use_music or str(music_track_id).strip() != "" or volume_provided
    selected_music_track_id = _optional_form_int(music_track_id) if music_in_play else None
    selected_music_volume = float(music_volume) if volume_provided else None
    try:
        for clip_plan_id in clip_plan_ids:
            plan = store.get_clip_plan(clip_plan_id, include_segments=False)
            if plan["source_id"] != source_id:
                raise ValueError("clip plan does not belong to source")
            clip_render_service.render_clip_plan(
                clip_plan_id,
                ffmpeg_preset_id=_optional_form_int(ffmpeg_preset_id),
                subtitle_profile_id=selected_subtitle_profile_id,
                banner_id=selected_banner_id,
                music_track_id=selected_music_track_id,
                music_volume=selected_music_volume,
                subtitle_offset_sec=_optional_form_float(subtitle_offset_sec),
            )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/sources/{source_id}/studio?stage=publish", status_code=303)


@app.post("/ui/clip-plans/{clip_plan_id}/segments", include_in_schema=False)
def ui_add_clip_plan_segment(
    clip_plan_id: int,
    _auth: AuthDep,
    start_sec: Annotated[float, Form()],
    end_sec: Annotated[float, Form()],
    title: Annotated[str, Form()] = "Segment",
    next: Annotated[str, Form()] = "",
) -> RedirectResponse:
    try:
        store.add_segment_to_clip_plan(clip_plan_id, start_sec, end_sec, title)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=_safe_next(next or "/sources"), status_code=303)


@app.post("/ui/clip-plans/{clip_plan_id}/segments/{segment_id}/remove", include_in_schema=False)
def ui_remove_clip_plan_segment(
    clip_plan_id: int,
    segment_id: int,
    _auth: AuthDep,
    next: Annotated[str, Form()] = "",
) -> RedirectResponse:
    try:
        store.remove_segment_from_clip_plan(clip_plan_id, segment_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=_safe_next(next or "/sources"), status_code=303)


@app.post("/ui/clip-plans/{clip_plan_id}/segments/{segment_id}/move", include_in_schema=False)
def ui_move_clip_plan_segment(
    clip_plan_id: int,
    segment_id: int,
    _auth: AuthDep,
    direction: Annotated[str, Form()] = "down",
    next: Annotated[str, Form()] = "",
) -> RedirectResponse:
    try:
        store.move_clip_plan_segment(clip_plan_id, segment_id, "up" if direction == "up" else "down")
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=_safe_next(next or "/sources"), status_code=303)


@app.post("/ui/clips/upload", include_in_schema=False)
async def ui_upload_clip(
    _auth: AuthDep,
    file: Annotated[UploadFile, File()],
    title: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
) -> RedirectResponse:
    try:
        clip = await _register_uploaded_clip(file, title, description)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        await file.close()
    return RedirectResponse(url=f"/clips/{clip['id']}", status_code=303)


@app.post("/ui/clips/{clip_id}/render", include_in_schema=False)
def ui_render_uploaded_clip(
    clip_id: int,
    _auth: AuthDep,
    ffmpeg_preset_id: Annotated[str, Form()] = "",
    subtitle_profile_id: Annotated[str, Form()] = "",
    banner_id: Annotated[str, Form()] = "",
    music_track_id: Annotated[str, Form()] = "",
    music_volume: Annotated[str, Form()] = "",
    use_subtitles: Annotated[bool, Form()] = False,
    use_banner: Annotated[bool, Form()] = False,
    use_music: Annotated[bool, Form()] = False,
    subtitle_offset_sec: Annotated[str, Form()] = "",
) -> RedirectResponse:
    defaults = _settings_panel_data()
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
    try:
        clip = clip_render_service.render_uploaded_clip(
            clip_id,
            ffmpeg_preset_id=_optional_form_int(ffmpeg_preset_id),
            subtitle_profile_id=selected_subtitle_profile_id,
            banner_id=selected_banner_id,
            music_track_id=selected_music_track_id,
            music_volume=selected_music_volume,
            subtitle_offset_sec=_optional_form_float(subtitle_offset_sec),
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/clips/{clip['id']}", status_code=303)


@app.get("/media/clips/{clip_id}", include_in_schema=False)
def clip_media(clip_id: int, _auth: AuthDep) -> FileResponse:
    try:
        clip = store.get_clip(clip_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    path = Path(clip["output_path"])
    try:
        path.resolve(strict=False).relative_to(settings.clip_dir.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="clip media not found") from exc
    if not path.exists():
        raise HTTPException(status_code=404, detail="clip media not found")
    return FileResponse(path)


@app.get("/media/banners/{banner_id}", include_in_schema=False)
def banner_media(banner_id: int, _auth: AuthDep) -> FileResponse:
    try:
        banner = store.get_banner(banner_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    path = Path(banner["file_path"])
    try:
        path.resolve(strict=False).relative_to(settings.banner_dir.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="banner media not found") from exc
    if not path.exists():
        raise HTTPException(status_code=404, detail="banner media not found")
    return FileResponse(path)


@app.get("/media/audio-tracks/{track_id}", include_in_schema=False)
def audio_track_media(track_id: int, _auth: AuthDep) -> FileResponse:
    try:
        track = store.get_audio_track(track_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    path = Path(track["file_path"])
    try:
        path.resolve(strict=False).relative_to(settings.audio_dir.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="audio track media not found") from exc
    if not path.exists():
        raise HTTPException(status_code=404, detail="audio track media not found")
    return FileResponse(path)


@app.post("/ui/clips/{clip_id}/posts", include_in_schema=False)
def ui_create_post_from_clip(
    clip_id: int,
    _auth: AuthDep,
    title: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
    targets: Annotated[list[int], Form()] = [],
    privacy: Annotated[str, Form()] = "public",
    allow_comments: Annotated[bool, Form()] = True,
    scheduled_at: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "",
) -> RedirectResponse:
    try:
        job = store.create_clip_post_job(
            clip_id,
            title,
            description,
            targets,
            privacy,
            allow_comments,
            scheduled_at=_normalize_schedule_text(scheduled_at),
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=_safe_next(next or f"/jobs/{job['id']}"), status_code=303)


@app.post("/ui/clips/{clip_id}/delete", include_in_schema=False)
def ui_delete_clip(
    clip_id: int,
    _auth: AuthDep,
    next: Annotated[str, Form()] = "/clips",
) -> RedirectResponse:
    try:
        store.delete_clip(clip_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RedirectResponse(url=_safe_next(next), status_code=303)


@app.post("/ui/ai-analyses/{analysis_id}/delete", include_in_schema=False)
def ui_delete_ai_analysis(
    analysis_id: int,
    _auth: AuthDep,
    next: Annotated[str, Form()] = "/sources",
) -> RedirectResponse:
    try:
        store.delete_ai_analysis(analysis_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=_safe_next(next), status_code=303)


@app.post("/ui/ffmpeg-presets", include_in_schema=False)
def ui_create_ffmpeg_preset(
    _auth: AuthDep,
    label: Annotated[str, Form()],
    output_width: Annotated[int, Form()] = 1080,
    output_height: Annotated[int, Form()] = 1920,
    fps: Annotated[float, Form()] = 30,
    scale_mode: Annotated[str, Form()] = "cover",
    crop_anchor: Annotated[str, Form()] = "center",
    audio_mix_mode: Annotated[str, Form()] = "primary",
    audio_primary_stream: Annotated[int, Form()] = 0,
    audio_primary_volume: Annotated[float, Form()] = 1,
    audio_secondary_stream: Annotated[int | None, Form()] = None,
    audio_secondary_volume: Annotated[float, Form()] = 1,
    banner_id: Annotated[str, Form()] = "",
    subtitle_profile_id: Annotated[str, Form()] = "",
    music_track_id: Annotated[str, Form()] = "",
    music_volume: Annotated[float, Form()] = 0.25,
    music_loop: Annotated[bool, Form()] = False,
    music_fade_in_sec: Annotated[float, Form()] = 0,
    music_fade_out_sec: Annotated[float, Form()] = 0,
    music_duck: Annotated[bool, Form()] = False,
    music_duck_amount: Annotated[float, Form()] = 0.6,
    color_style: Annotated[str, Form()] = "none",
    color_strength: Annotated[float, Form()] = 1,
    vignette: Annotated[float, Form()] = 0,
    grain: Annotated[float, Form()] = 0,
    next: Annotated[str, Form()] = "/presets",
) -> RedirectResponse:
    try:
        store.create_ffmpeg_preset(
            label,
            output_width=output_width,
            output_height=output_height,
            fps=fps,
            scale_mode=scale_mode,
            crop_anchor=crop_anchor,
            audio_mix_mode=audio_mix_mode,
            audio_primary_stream=audio_primary_stream,
            audio_primary_volume=audio_primary_volume,
            audio_secondary_stream=audio_secondary_stream,
            audio_secondary_volume=audio_secondary_volume,
            banner_id=_optional_form_int(banner_id),
            subtitle_profile_id=_optional_form_int(subtitle_profile_id),
            music_track_id=_optional_form_int(music_track_id),
            music_volume=music_volume,
            music_loop=music_loop,
            music_fade_in_sec=music_fade_in_sec,
            music_fade_out_sec=music_fade_out_sec,
            music_duck=music_duck,
            music_duck_amount=music_duck_amount,
            color_style=color_style,
            color_strength=color_strength,
            vignette=vignette,
            grain=grain,
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=_safe_next(next), status_code=303)


@app.post("/ui/audio-tracks", include_in_schema=False)
async def ui_create_audio_track(
    _auth: AuthDep,
    file: Annotated[UploadFile, File()],
    label: Annotated[str, Form()],
    volume: Annotated[float, Form()] = 0.25,
    next: Annotated[str, Form()] = "/sources",
) -> RedirectResponse:
    try:
        await _save_audio_upload(file, label, volume=volume)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        await file.close()
    return RedirectResponse(url=_safe_next(next), status_code=303)


@app.post("/ui/audio-tracks/{track_id}/update", include_in_schema=False)
def ui_update_audio_track(
    track_id: int,
    _auth: AuthDep,
    label: Annotated[str, Form()],
    volume: Annotated[float, Form()] = 0.25,
    next: Annotated[str, Form()] = "/sources",
) -> RedirectResponse:
    try:
        store.update_audio_track(track_id, label=label, volume=volume)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=_safe_next(next), status_code=303)


@app.post("/ui/audio-tracks/{track_id}/delete", include_in_schema=False)
def ui_delete_audio_track(
    track_id: int,
    _auth: AuthDep,
    next: Annotated[str, Form()] = "/sources",
) -> RedirectResponse:
    try:
        store.delete_audio_track(track_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RedirectResponse(url=_safe_next(next), status_code=303)


@app.post("/ui/banners", include_in_schema=False)
async def ui_create_banner(
    _auth: AuthDep,
    file: Annotated[UploadFile, File()],
    label: Annotated[str, Form()],
    position: Annotated[str, Form()] = "bottom",
    opacity: Annotated[float, Form()] = 1,
    make_default: Annotated[bool, Form()] = False,
    next: Annotated[str, Form()] = "/presets",
) -> RedirectResponse:
    try:
        banner = await _save_banner_upload(file, label, position=position, opacity=opacity)
        if make_default:
            store.set_app_setting("default_banner_id", banner["id"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        await file.close()
    return RedirectResponse(url=_safe_next(next), status_code=303)


@app.post("/ui/banners/{banner_id}/update", include_in_schema=False)
def ui_update_banner(
    banner_id: int,
    _auth: AuthDep,
    label: Annotated[str, Form()],
    position: Annotated[str, Form()] = "bottom",
    opacity: Annotated[float, Form()] = 1,
    make_default: Annotated[bool, Form()] = False,
    next: Annotated[str, Form()] = "/presets",
) -> RedirectResponse:
    try:
        store.update_banner(banner_id, label=label, position=position, opacity=opacity)
        if make_default:
            store.set_app_setting("default_banner_id", banner_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=_safe_next(next), status_code=303)


@app.post("/ui/subtitle-profiles", include_in_schema=False)
def ui_create_subtitle_profile(
    _auth: AuthDep,
    label: Annotated[str, Form()],
    provider: Annotated[str, Form()] = "mock",
    model: Annotated[str, Form()] = "openai/gpt-4o-transcribe",
    language: Annotated[str, Form()] = "",
    timing_offset_sec: Annotated[float, Form()] = 0.0,
    font_family: Annotated[str, Form()] = "Arial",
    font_size: Annotated[int, Form()] = 64,
    primary_color: Annotated[str, Form()] = "#FFFFFF",
    active_word_color: Annotated[str, Form()] = "#FACC15",
    outline_color: Annotated[str, Form()] = "#111827",
    back_color: Annotated[str, Form()] = "#000000",
    margin_v: Annotated[int, Form()] = 160,
    max_words_per_line: Annotated[int, Form()] = 5,
    uppercase: Annotated[bool, Form()] = False,
    outline_width: Annotated[float, Form()] = 5,
    shadow: Annotated[float, Form()] = 1,
    make_default: Annotated[bool, Form()] = False,
    next: Annotated[str, Form()] = "/presets",
) -> RedirectResponse:
    try:
        profile = store.create_subtitle_profile(
            label,
            provider=provider,
            model=model,
            language=language,
            timing_offset_sec=timing_offset_sec,
            font_family=font_family,
            font_size=font_size,
            primary_color=primary_color,
            active_word_color=active_word_color,
            outline_color=outline_color,
            back_color=back_color,
            margin_v=margin_v,
            max_words_per_line=max_words_per_line,
            uppercase=uppercase,
            outline_width=outline_width,
            shadow=shadow,
        )
        if make_default:
            store.set_app_setting("default_subtitle_profile_id", profile["id"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=_safe_next(next), status_code=303)


@app.post("/ui/subtitle-profiles/{profile_id}/update", include_in_schema=False)
def ui_update_subtitle_profile(
    profile_id: int,
    _auth: AuthDep,
    label: Annotated[str, Form()],
    provider: Annotated[str, Form()] = "mock",
    model: Annotated[str, Form()] = "openai/gpt-4o-transcribe",
    language: Annotated[str, Form()] = "",
    timing_offset_sec: Annotated[float, Form()] = 0.0,
    font_family: Annotated[str, Form()] = "Arial",
    font_size: Annotated[int, Form()] = 64,
    primary_color: Annotated[str, Form()] = "#FFFFFF",
    active_word_color: Annotated[str, Form()] = "#FACC15",
    outline_color: Annotated[str, Form()] = "#111827",
    back_color: Annotated[str, Form()] = "#000000",
    margin_v: Annotated[int, Form()] = 160,
    max_words_per_line: Annotated[int, Form()] = 5,
    uppercase: Annotated[bool, Form()] = False,
    outline_width: Annotated[float, Form()] = 5,
    shadow: Annotated[float, Form()] = 1,
    make_default: Annotated[bool, Form()] = False,
    next: Annotated[str, Form()] = "/presets",
) -> RedirectResponse:
    try:
        store.update_subtitle_profile(
            profile_id,
            label=label,
            provider=provider,
            model=model,
            language=language,
            timing_offset_sec=timing_offset_sec,
            font_family=font_family,
            font_size=font_size,
            primary_color=primary_color,
            active_word_color=active_word_color,
            outline_color=outline_color,
            back_color=back_color,
            margin_v=margin_v,
            max_words_per_line=max_words_per_line,
            uppercase=uppercase,
            outline_width=outline_width,
            shadow=shadow,
        )
        if make_default:
            store.set_app_setting("default_subtitle_profile_id", profile_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=_safe_next(next), status_code=303)


@app.get("/api/settings", tags=["Render"], summary="Default models, proxy and asset settings")
def api_settings(_auth: AuthDep) -> dict:
    data = _settings_panel_data()
    return {
        "default_ai_provider": data["default_ai_provider"],
        "default_ai_model": data["default_ai_model"],
        "default_subtitle_provider": data["default_subtitle_provider"],
        "default_subtitle_model": data["default_subtitle_model"],
        "default_banner_id": data["default_banner_id"],
        "default_subtitle_profile_id": data["default_subtitle_profile_id"],
        "global_proxy_configured": data["global_proxy_configured"],
        "global_proxy_display": data["global_proxy_display"],
        "banners": [{"id": b["id"], "label": b["label"]} for b in data["banners"]],
        "subtitle_profiles": [{"id": p["id"], "label": p["label"]} for p in data["subtitle_profiles"]],
    }


@app.post("/ui/settings/defaults", include_in_schema=False)
def ui_save_settings_defaults(
    _auth: AuthDep,
    analysis_prompt: Annotated[str, Form()] = BASE_ANALYSIS_PROMPT,
    publishing_prompt: Annotated[str, Form()] = DEFAULT_PUBLISHING_PROMPT,
    subtitle_prompt: Annotated[str, Form()] = DEFAULT_SUBTITLE_PROMPT,
    default_ai_provider: Annotated[str, Form()] = "mock",
    default_ai_model: Annotated[str, Form()] = "",
    default_subtitle_provider: Annotated[str, Form()] = "mock",
    default_subtitle_model: Annotated[str, Form()] = "",
    global_proxy_url: Annotated[str | None, Form()] = None,
    default_banner_id: Annotated[int, Form()] = 0,
    default_subtitle_profile_id: Annotated[int, Form()] = 0,
    next: Annotated[str, Form()] = "/sources",
) -> RedirectResponse:
    try:
        provider = default_ai_provider.strip().lower() or "mock"
        subtitle_provider = default_subtitle_provider.strip().lower() or "mock"
        if provider not in {"mock", "action", "polza", "gemini", "artemox"}:
            raise ValueError(f"unsupported provider: {default_ai_provider}")
        if subtitle_provider not in {"mock", "whisper", "polza", "gemini", "artemox"}:
            raise ValueError(f"unsupported subtitle provider: {default_subtitle_provider}")
        if global_proxy_url is not None and global_proxy_url.strip() and not global_proxy_url.strip().startswith(("http://", "https://")):
            raise ValueError("global proxy must start with http:// or https://")
        if default_banner_id:
            store.get_banner(default_banner_id)
        if default_subtitle_profile_id:
            store.get_subtitle_profile(default_subtitle_profile_id)
        store.set_app_setting("default_ai_provider", provider)
        store.set_app_setting("default_ai_model", default_ai_model.strip())
        store.set_app_setting("default_subtitle_provider", subtitle_provider)
        store.set_app_setting("default_subtitle_model", default_subtitle_model.strip())
        store.set_app_setting("default_banner_id", int(default_banner_id or 0))
        store.set_app_setting("default_subtitle_profile_id", int(default_subtitle_profile_id or 0))
        if global_proxy_url is not None:
            store.set_app_setting("global_proxy_url", global_proxy_url.strip(), encrypted=True)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=_safe_next(next), status_code=303)


@app.get("/api/prompt-presets", tags=["Render"], summary="List prompt presets")
def api_prompt_presets(_auth: AuthDep, task: str | None = None) -> list[dict]:
    return store.list_prompt_presets(task)


@app.post("/ui/prompt-presets", include_in_schema=False)
def ui_create_prompt_preset(
    _auth: AuthDep,
    task: Annotated[str, Form()],
    label: Annotated[str, Form()],
    prompt: Annotated[str, Form()],
    make_default: Annotated[bool, Form()] = False,
    next: Annotated[str, Form()] = "/sources",
) -> RedirectResponse:
    try:
        store.create_prompt_preset(task, label, prompt, is_default=make_default)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=_safe_next(next), status_code=303)


@app.post("/ui/prompt-presets/{preset_id}/update", include_in_schema=False)
def ui_update_prompt_preset(
    preset_id: int,
    _auth: AuthDep,
    label: Annotated[str, Form()],
    prompt: Annotated[str, Form()],
    make_default: Annotated[bool, Form()] = False,
    next: Annotated[str, Form()] = "/sources",
) -> RedirectResponse:
    try:
        store.update_prompt_preset(preset_id, label=label, prompt=prompt, is_default=make_default)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=_safe_next(next), status_code=303)


@app.post("/ui/prompt-presets/{preset_id}/delete", include_in_schema=False)
def ui_delete_prompt_preset(
    preset_id: int,
    _auth: AuthDep,
    next: Annotated[str, Form()] = "/sources",
) -> RedirectResponse:
    try:
        store.delete_prompt_preset(preset_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=_safe_next(next), status_code=303)


@app.post("/ui/posts", include_in_schema=False)
async def ui_create_post(
    _auth: AuthDep,
    file: Annotated[UploadFile, File()],
    title: Annotated[str, Form()],
    description: Annotated[str, Form()] = "",
    targets: Annotated[list[int], Form()] = [],
    privacy: Annotated[str, Form()] = "public",
    allow_comments: Annotated[bool, Form()] = True,
    scheduled_at: Annotated[str, Form()] = "",
) -> RedirectResponse:
    job = await _create_post_from_upload(file, title, description, targets, privacy, allow_comments, scheduled_at)
    return RedirectResponse(url=f"/jobs/{job['id']}", status_code=303)


@app.get("/api/auto/runs", tags=["Jobs"], summary="List recent automation runs")
def api_auto_runs(_auth: AuthDep) -> list[dict]:
    return automation_service.list_runs()


@app.post("/ui/auto/start", include_in_schema=False)
def ui_auto_start(
    _auth: AuthDep,
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
) -> RedirectResponse:
    if not url.strip():
        raise HTTPException(status_code=400, detail="url is required")
    defaults = _settings_panel_data()
    selected_prompt = ""
    if prompt_preset_id:
        try:
            preset = store.get_prompt_preset(prompt_preset_id)
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

    automation_service.start_run(
        url=url.strip(),
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
        },
    )
    return RedirectResponse(url="/auto", status_code=303)


@app.post(
    "/api/accounts",
    response_model=AccountRead,
    tags=["Accounts"],
    summary="Add or update an account",
    description="Stores encrypted cookies for a YouTube or TikTok account. The cookie value is never returned.",
)
def api_add_account(payload: AccountCreate, _auth: AuthDep) -> dict:
    try:
        return store.upsert_account(payload.platform, payload.label, payload.cookie, payload.proxy_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get(
    "/api/accounts",
    response_model=list[AccountRead],
    tags=["Accounts"],
    summary="List accounts",
    description="Returns account metadata only; encrypted cookies are not included.",
)
def api_accounts(_auth: AuthDep) -> list[dict]:
    return store.list_accounts()


@app.delete(
    "/api/accounts/{account_id}",
    tags=["Accounts"],
    summary="Delete an account",
)
def api_delete_account(account_id: int, _auth: AuthDep) -> dict:
    try:
        deleted = store.delete_account(account_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": True, "account": deleted}


@app.post(
    "/api/smotvibe/options",
    response_model=list[SmotvibeOptionRead],
    tags=["Sources"],
    summary="Discover Smotvibe seasons, episodes, and voice tracks",
)
def api_smotvibe_options(payload: SmotvibeOptionsRequest, _auth: AuthDep) -> list[dict]:
    try:
        return [option.to_dict() for option in discover_smotvibe_download_options(payload.url)]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post(
    "/api/sources",
    response_model=SourceRead,
    tags=["Sources"],
    summary="Ingest a source video",
    description=(
        "Accepts either multipart `file` upload or JSON body `{ \"url\": \"...\" }`. "
        "Direct mp4/mov/webm URLs, YouTube URLs, Twitch URLs, and Smotvibe player pages are supported."
    ),
)
async def api_create_source(request: Request, _auth: AuthDep) -> dict:
    try:
        if "multipart/form-data" in request.headers.get("content-type", ""):
            form = await request.form()
            upload = form.get("file")
            url = str(form.get("url") or "").strip()
            if upload is not None and hasattr(upload, "file"):
                try:
                    return source_ingestor.ingest_upload(upload.file, upload.filename or "source.mp4")
                finally:
                    await upload.close()
            if url:
                return _ingest_source_url(
                    url,
                    smotvibe_media_url=str(form.get("smotvibe_media_url") or ""),
                    smotvibe_referer=str(form.get("smotvibe_referer") or ""),
                    smotvibe_audio_format_id=str(form.get("smotvibe_audio_format_id") or ""),
                    smotvibe_filename_label=str(form.get("smotvibe_filename_label") or ""),
                )
            raise ValueError("multipart request must include file or url")
        payload = await request.json()
        url = str(payload.get("url") or "").strip()
        if not url:
            raise ValueError("json request must include url")
        return _ingest_source_url(
            url,
            smotvibe_media_url=str(payload.get("smotvibe_media_url") or ""),
            smotvibe_referer=str(payload.get("smotvibe_referer") or ""),
            smotvibe_audio_format_id=str(payload.get("smotvibe_audio_format_id") or ""),
            smotvibe_filename_label=str(payload.get("smotvibe_filename_label") or ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get(
    "/api/sources",
    response_model=list[SourceRead],
    tags=["Sources"],
    summary="List ingested sources",
)
def api_sources(_auth: AuthDep) -> list[dict]:
    return store.list_sources()


@app.get(
    "/api/sources/{source_id}",
    tags=["Sources"],
    summary="Get source details",
)
def api_source(source_id: int, _auth: AuthDep) -> dict:
    try:
        store.ensure_clip_plans_for_source(source_id)
        return store.get_source(source_id, include_related=True)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete(
    "/api/sources/{source_id}",
    tags=["Sources"],
    summary="Delete a source and all derived data",
)
def api_delete_source(source_id: int, _auth: AuthDep) -> dict:
    try:
        return store.delete_source(source_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class SourceRenamePayload(BaseModel):
    name: str = Field(min_length=1, max_length=200)


@app.patch("/api/sources/{source_id}", tags=["Sources"], summary="Rename a source")
def api_rename_source(source_id: int, payload: SourceRenamePayload, _auth: AuthDep) -> dict:
    try:
        return store.update_source(source_id, original_filename=payload.name.strip())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/focus-presets", tags=["Render"], summary="List autofocus presets")
def api_focus_presets(_auth: AuthDep) -> dict:
    return {"presets": list_focus_presets(), "strategies": list_focus_strategies()}


class SourceFocusPresetPayload(BaseModel):
    focus_preset: str | None = None
    focus_strategy: str | None = None


@app.patch("/api/sources/{source_id}/focus-preset", tags=["Render"], summary="Set the autofocus preset/strategy")
def api_set_source_focus_preset(source_id: int, payload: SourceFocusPresetPayload, _auth: AuthDep) -> dict:
    fields = {}
    if payload.focus_preset is not None:
        fields["focus_preset"] = payload.focus_preset.strip()
    if payload.focus_strategy is not None:
        fields["focus_strategy"] = payload.focus_strategy.strip()
    if not fields:
        return store.get_source(source_id)
    try:
        return store.update_source(source_id, **fields)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class CropRect(BaseModel):
    x: float
    y: float
    w: float
    h: float


class SourceCropPayload(BaseModel):
    crop: CropRect | None = None


@app.post("/api/sources/{source_id}/detect-crop", tags=["Sources"], summary="Auto-detect content bars")
def api_detect_crop(source_id: int, _auth: AuthDep) -> dict:
    try:
        source = store.get_source(source_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        crop = detect_content_crop(source)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"crop": crop}


@app.patch("/api/sources/{source_id}/crop", tags=["Sources"], summary="Set the content crop rect")
def api_set_crop(source_id: int, payload: SourceCropPayload, _auth: AuthDep) -> dict:
    crop = payload.crop.model_dump() if payload.crop else None
    try:
        return store.update_source(source_id, content_crop=crop)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get(
    "/api/sources/{source_id}/storyboard",
    tags=["Sources"],
    summary="Storyboard thumbnails sampled across the video",
)
def api_source_storyboard(source_id: int, _auth: AuthDep) -> dict:
    try:
        source = store.get_source(source_id)
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


@app.get("/media/sources/{source_id}/storyboard/{index}", include_in_schema=False)
def media_source_storyboard_frame(source_id: int, index: int, _auth: AuthDep) -> FileResponse:
    path = frame_path(source_id, index)
    if not path.exists():
        raise HTTPException(status_code=404, detail="frame not found")
    return FileResponse(path, media_type="image/jpeg")


@app.get(
    "/api/sources/{source_id}/stats",
    tags=["Sources"],
    summary="Get source project stats",
)
def api_source_stats(source_id: int, _auth: AuthDep) -> dict:
    try:
        return store.get_source_stats(source_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get(
    "/api/sources/{source_id}/clip-plans",
    tags=["Render"],
    summary="List clip plans for a source",
)
def api_source_clip_plans(source_id: int, _auth: AuthDep) -> list[dict]:
    try:
        store.get_source(source_id)
        return store.list_clip_plans(source_id=source_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post(
    "/api/sources/{source_id}/analyze",
    tags=["Sources"],
    summary="Run AI analysis for a source",
)
def api_analyze_source(
    source_id: int,
    _auth: AuthDep,
    payload: AnalyzeSourceRequest | None = None,
) -> dict:
    try:
        return video_analysis_service.run_analysis(
            source_id,
            provider=payload.provider if payload else None,
            model=payload.model if payload else None,
            prompt=_analysis_prompt_from_payload(payload),
            use_transcript=payload.use_transcript if payload else None,
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
        preset = store.get_prompt_preset(payload.prompt_preset_id)
        if preset["task"] != "analysis":
            raise ValueError("prompt preset must be for analysis")
        return preset["prompt"]
    return payload.prompt if payload.prompt else None


@app.delete("/api/ai-analyses/{analysis_id}", tags=["Sources"], summary="Delete an AI analysis generation")
def api_delete_ai_analysis(analysis_id: int, _auth: AuthDep) -> dict:
    try:
        deleted = store.delete_ai_analysis(analysis_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"deleted": True, "analysis": deleted}


@app.post(
    "/api/segments/{segment_id}/realizations",
    tags=["Render"],
    summary="Render a vertical clip from an AI segment",
)
def api_render_segment(
    segment_id: int,
    _auth: AuthDep,
    payload: RenderSegmentRequest | None = None,
) -> dict:
    try:
        return clip_render_service.render_segment(
            segment_id,
            ffmpeg_preset_id=payload.ffmpeg_preset_id if payload else None,
            subtitle_profile_id=payload.subtitle_profile_id if payload else None,
            banner_id=payload.banner_id if payload else None,
            music_track_id=payload.music_track_id if payload else None,
            music_volume=payload.music_volume if payload else None,
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch(
    "/api/segments/{segment_id}/timecodes",
    tags=["Render"],
    summary="Update AI segment timecodes",
)
def api_update_segment_timecodes(
    segment_id: int,
    payload: SegmentTimecodesPatchPayload,
    _auth: AuthDep,
) -> dict:
    try:
        return store.update_ai_segment_timecodes(segment_id, payload.start_sec, payload.end_sec)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class FocusPoint(BaseModel):
    t: float
    x: float
    y: float | None = None


class SegmentFocusPayload(BaseModel):
    focus: list[FocusPoint] = Field(default_factory=list)


@app.patch("/api/segments/{segment_id}/focus", tags=["Render"], summary="Set a segment's reframe focus track")
def api_update_segment_focus(segment_id: int, payload: SegmentFocusPayload, _auth: AuthDep) -> dict:
    focus = [p.model_dump(exclude_none=True) for p in payload.focus]
    try:
        return store.update_ai_segment_focus(segment_id, focus)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _autofocus_segment(segment_id: int, use_vlm: bool = False) -> dict:
    from app.autofocus import compute_segment_focus  # lazy: opencv is heavy / optional

    segment = store.get_ai_segment(segment_id)
    source = store.get_source(segment["source_id"])
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
    return store.update_ai_segment_focus(segment_id, points)


class AutofocusPayload(BaseModel):
    use_vlm: bool = False


@app.post("/api/segments/{segment_id}/autofocus", tags=["Render"], summary="Detect focus (faces/motion) for a segment")
def api_autofocus_segment(segment_id: int, _auth: AuthDep, payload: AutofocusPayload | None = None) -> dict:
    try:
        return _autofocus_segment(segment_id, use_vlm=bool(payload and payload.use_vlm))
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class AutofocusBatchPayload(BaseModel):
    segment_ids: list[int] = Field(default_factory=list)
    use_vlm: bool = False


@app.post("/api/sources/{source_id}/autofocus", tags=["Render"], summary="Detect focus for many segments")
def api_autofocus_segments(source_id: int, payload: AutofocusBatchPayload, _auth: AuthDep) -> dict:
    try:
        store.get_source(source_id)
        ids = payload.segment_ids or [s["id"] for s in store.list_ai_segments(source_id=source_id)]
        done = 0
        for sid in ids:
            seg = store.get_ai_segment(sid)
            if seg["source_id"] != source_id:
                continue
            _autofocus_segment(sid, use_vlm=payload.use_vlm)
            done += 1
        return {"updated": done}
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post(
    "/api/clip-plans/{clip_plan_id}/render",
    tags=["Render"],
    summary="Render a saved clip plan",
)
def api_render_clip_plan(
    clip_plan_id: int,
    _auth: AuthDep,
    payload: RenderClipPlanRequest | None = None,
) -> dict:
    try:
        return clip_render_service.render_clip_plan(
            clip_plan_id,
            ffmpeg_preset_id=payload.ffmpeg_preset_id if payload else None,
            subtitle_profile_id=payload.subtitle_profile_id if payload else None,
            banner_id=payload.banner_id if payload else None,
            music_track_id=payload.music_track_id if payload else None,
            music_volume=payload.music_volume if payload else None,
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post(
    "/api/sources/{source_id}/render-plans",
    tags=["Render"],
    summary="Render selected clip plans for a source",
)
def api_render_clip_plans(
    source_id: int,
    payload: RenderClipPlansRequest,
    _auth: AuthDep,
) -> list[dict]:
    if not payload.clip_plan_ids:
        raise HTTPException(status_code=400, detail="clip_plan_ids cannot be empty")
    clips: list[dict] = []
    try:
        for clip_plan_id in payload.clip_plan_ids:
            plan = store.get_clip_plan(clip_plan_id, include_segments=False)
            if plan["source_id"] != source_id:
                raise ValueError("clip plan does not belong to source")
            clips.append(
                clip_render_service.render_clip_plan(
                    clip_plan_id,
                    ffmpeg_preset_id=payload.ffmpeg_preset_id,
                    subtitle_profile_id=payload.subtitle_profile_id,
                    banner_id=payload.banner_id,
                    music_track_id=payload.music_track_id,
                    music_volume=payload.music_volume,
                    subtitle_provider=payload.subtitle_provider,
                    banner_height_frac=payload.banner_height_frac,
                    banner_y_frac=payload.banner_y_frac,
                    subtitle_margin_v=payload.subtitle_margin_v,
                    mirror=bool(payload.mirror),
                )
            )
        return clips
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post(
    "/api/montages",
    tags=["Render"],
    summary="Render one stitched vertical clip from multiple AI segments",
)
def api_render_montage(payload: RenderMontageRequest, _auth: AuthDep) -> dict:
    try:
        return clip_render_service.render_montage(
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


@app.get("/api/clips", tags=["Render"], summary="List clips")
def api_clips(_auth: AuthDep, source_id: int | None = None) -> list[dict]:
    return store.list_clips(source_id)


@app.get("/api/clips/{clip_id}", tags=["Render"], summary="Get clip details")
def api_clip(clip_id: int, _auth: AuthDep) -> dict:
    try:
        clip = store.get_clip(clip_id)
        clip["subtitle_tracks"] = store.list_subtitle_tracks(clip_id)
        return clip
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete("/api/clips/{clip_id}", tags=["Render"], summary="Delete a rendered clip")
def api_delete_clip(clip_id: int, _auth: AuthDep) -> dict:
    try:
        store.delete_clip(clip_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": True}


class ClipRenamePayload(BaseModel):
    title: str = Field(min_length=1, max_length=200)


@app.patch("/api/clips/{clip_id}", tags=["Render"], summary="Rename a clip")
def api_rename_clip(clip_id: int, payload: ClipRenamePayload, _auth: AuthDep) -> dict:
    try:
        return store.update_clip(clip_id, title=payload.title.strip())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post(
    "/api/clips/{clip_id}/posts",
    response_model=JobRead,
    tags=["Posts"],
    summary="Queue publication for a rendered clip",
)
def api_create_post_from_clip(clip_id: int, payload: ClipPostRequest, _auth: AuthDep) -> dict:
    try:
        return store.create_clip_post_job(
            clip_id,
            payload.title,
            payload.description,
            payload.targets,
            payload.privacy,
            payload.allow_comments,
            scheduled_at=_normalize_schedule_text(payload.scheduled_at),
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/tasks/active", tags=["Jobs"], summary="List active UI tasks")
def api_active_tasks(_auth: AuthDep) -> list[dict]:
    return store.list_active_tasks()


@app.get("/api/tasks", tags=["Jobs"], summary="List recent tasks (execution log)")
def api_recent_tasks(_auth: AuthDep, limit: int = 80) -> list[dict]:
    return store.list_recent_tasks(max(1, min(300, limit)))


@app.get("/api/export", tags=["Settings"], summary="Export a shareable config bundle")
def api_export_bundle(_auth: AuthDep, accounts: bool = False) -> dict:
    return store.export_bundle(include_accounts=accounts)


@app.post("/api/import", tags=["Settings"], summary="Import a config bundle")
def api_import_bundle(payload: dict, _auth: AuthDep) -> dict:
    try:
        return store.import_bundle(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/clip-plans/{clip_plan_id}/segments", tags=["Render"], summary="Add segment to clip plan")
def api_add_clip_plan_segment(
    clip_plan_id: int,
    payload: ClipPlanSegmentCreateRequest,
    _auth: AuthDep,
) -> dict:
    try:
        return store.add_segment_to_clip_plan(clip_plan_id, payload.start_sec, payload.end_sec, payload.title)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete(
    "/api/clip-plans/{clip_plan_id}/segments/{segment_id}",
    tags=["Render"],
    summary="Remove segment from clip plan",
)
def api_remove_clip_plan_segment(clip_plan_id: int, segment_id: int, _auth: AuthDep) -> dict:
    try:
        return store.remove_segment_from_clip_plan(clip_plan_id, segment_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post(
    "/api/clip-plans/{clip_plan_id}/segments/{segment_id}/move",
    tags=["Render"],
    summary="Move clip plan segment",
)
def api_move_clip_plan_segment(
    clip_plan_id: int,
    segment_id: int,
    payload: ClipPlanSegmentMoveRequest,
    _auth: AuthDep,
) -> dict:
    try:
        return store.move_clip_plan_segment(clip_plan_id, segment_id, payload.direction)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/ffmpeg-presets", tags=["Render"], summary="List ffmpeg presets")
def api_ffmpeg_presets(_auth: AuthDep) -> list[dict]:
    return store.list_ffmpeg_presets()


@app.post("/api/ffmpeg-presets", tags=["Render"], summary="Create ffmpeg preset")
def api_create_ffmpeg_preset(payload: FfmpegPresetPayload, _auth: AuthDep) -> dict:
    try:
        return store.create_ffmpeg_preset(payload.label, **_payload_data(payload, exclude={"label"}))
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/ffmpeg-presets/{preset_id}", tags=["Render"], summary="Get ffmpeg preset")
def api_ffmpeg_preset(preset_id: int, _auth: AuthDep) -> dict:
    try:
        return store.get_ffmpeg_preset(preset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.patch("/api/ffmpeg-presets/{preset_id}", tags=["Render"], summary="Update ffmpeg preset")
def api_update_ffmpeg_preset(
    preset_id: int,
    payload: FfmpegPresetPatchPayload,
    _auth: AuthDep,
) -> dict:
    try:
        return store.update_ffmpeg_preset(preset_id, **_payload_data(payload, exclude_none=True))
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/ffmpeg-presets/{preset_id}", tags=["Render"], summary="Delete ffmpeg preset")
def api_delete_ffmpeg_preset(preset_id: int, _auth: AuthDep) -> dict:
    try:
        store.delete_ffmpeg_preset(preset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": True}


@app.get("/api/banners", tags=["Render"], summary="List banners")
def api_banners(_auth: AuthDep) -> list[dict]:
    return store.list_banners()


@app.get("/api/banners/{banner_id}", tags=["Render"], summary="Get banner")
def api_banner(banner_id: int, _auth: AuthDep) -> dict:
    try:
        return store.get_banner(banner_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/banners", tags=["Render"], summary="Upload banner overlay")
async def api_create_banner(
    _auth: AuthDep,
    file: Annotated[UploadFile, File()],
    label: Annotated[str, Form()],
    position: Annotated[str, Form()] = "bottom",
    opacity: Annotated[float, Form()] = 1,
) -> dict:
    try:
        return await _save_banner_upload(file, label, position=position, opacity=opacity)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        await file.close()


@app.patch("/api/banners/{banner_id}", tags=["Render"], summary="Update banner")
def api_update_banner(
    banner_id: int,
    payload: BannerPatchPayload,
    _auth: AuthDep,
) -> dict:
    try:
        return store.update_banner(banner_id, **_payload_data(payload, exclude_none=True))
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/banners/{banner_id}", tags=["Render"], summary="Delete banner")
def api_delete_banner(banner_id: int, _auth: AuthDep) -> dict:
    try:
        store.delete_banner(banner_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": True}


@app.get("/api/audio-tracks", tags=["Render"], summary="List background music tracks")
def api_audio_tracks(_auth: AuthDep) -> list[dict]:
    return store.list_audio_tracks()


@app.get("/api/audio-tracks/{track_id}", tags=["Render"], summary="Get music track")
def api_audio_track(track_id: int, _auth: AuthDep) -> dict:
    try:
        return store.get_audio_track(track_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/audio-tracks", tags=["Render"], summary="Upload background music track")
async def api_create_audio_track(
    _auth: AuthDep,
    file: Annotated[UploadFile, File()],
    label: Annotated[str, Form()],
    volume: Annotated[float, Form()] = 0.25,
) -> dict:
    try:
        return await _save_audio_upload(file, label, volume=volume)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        await file.close()


@app.patch("/api/audio-tracks/{track_id}", tags=["Render"], summary="Update music track")
def api_update_audio_track(track_id: int, payload: AudioTrackPatchPayload, _auth: AuthDep) -> dict:
    try:
        return store.update_audio_track(track_id, **_payload_data(payload, exclude_none=True))
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/audio-tracks/{track_id}", tags=["Render"], summary="Delete music track")
def api_delete_audio_track(track_id: int, _auth: AuthDep) -> dict:
    try:
        store.delete_audio_track(track_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": True}


@app.get("/api/subtitle-profiles", tags=["Render"], summary="List subtitle profiles")
def api_subtitle_profiles(_auth: AuthDep) -> list[dict]:
    return store.list_subtitle_profiles()


@app.post("/api/subtitle-profiles", tags=["Render"], summary="Create subtitle profile")
def api_create_subtitle_profile(payload: SubtitleProfilePayload, _auth: AuthDep) -> dict:
    try:
        return store.create_subtitle_profile(payload.label, **_payload_data(payload, exclude={"label"}, exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/subtitle-profiles/{profile_id}", tags=["Render"], summary="Get subtitle profile")
def api_subtitle_profile(profile_id: int, _auth: AuthDep) -> dict:
    try:
        return store.get_subtitle_profile(profile_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.patch("/api/subtitle-profiles/{profile_id}", tags=["Render"], summary="Update subtitle profile")
def api_update_subtitle_profile(
    profile_id: int,
    payload: SubtitleProfilePatchPayload,
    _auth: AuthDep,
) -> dict:
    try:
        return store.update_subtitle_profile(profile_id, **_payload_data(payload, exclude_none=True))
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/subtitle-profiles/{profile_id}", tags=["Render"], summary="Delete subtitle profile")
def api_delete_subtitle_profile(profile_id: int, _auth: AuthDep) -> dict:
    try:
        store.delete_subtitle_profile(profile_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": True}


@app.post(
    "/api/posts",
    response_model=JobRead,
    tags=["Posts"],
    summary="Queue a video post",
    description=(
        "Uploads a source video into `/data/uploads` and creates a queued job for one or more "
        "saved account IDs. Send repeated `targets` multipart fields for multiple accounts."
    ),
)
async def api_create_post(
    _auth: AuthDep,
    file: Annotated[UploadFile, File(description="Source video file, usually mp4/mov/webm.")],
    title: Annotated[str, Form(description="Post title.")] ,
    description: Annotated[str, Form(description="Post description/caption body.")] = "",
    targets: Annotated[list[int], Form(description="Saved account IDs. Repeat this form field for each target.")] = [],
    privacy: Annotated[
        Literal["public", "unlisted", "private"],
        Form(description="Publication visibility. TikTok supports public/private only."),
    ] = "public",
    allow_comments: Annotated[bool, Form(description="Whether comments should be enabled where supported.")] = True,
    scheduled_at: Annotated[str, Form(description="Optional local datetime for delayed publication.")] = "",
) -> dict:
    return await _create_post_from_upload(file, title, description, targets, privacy, allow_comments, scheduled_at)


@app.get(
    "/api/jobs",
    response_model=list[JobRead],
    tags=["Jobs"],
    summary="List recent jobs",
)
def api_jobs(_auth: AuthDep) -> list[dict]:
    return store.list_jobs()


@app.get(
    "/api/jobs/{job_id}",
    response_model=JobRead,
    tags=["Jobs"],
    summary="Get job details",
)
def api_job(job_id: int, _auth: AuthDep) -> dict:
    try:
        return store.get_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


async def _create_post_from_upload(
    file: UploadFile,
    title: str,
    description: str,
    targets: list[int],
    privacy: str,
    allow_comments: bool,
    scheduled_at: str = "",
) -> dict:
    try:
        job = store.create_job(
            file.file,
            file.filename or "video.mp4",
            title,
            description,
            targets,
            privacy,
            allow_comments,
            scheduled_at=_normalize_schedule_text(scheduled_at),
        )
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        await file.close()
    return job


async def _register_uploaded_clip(file: UploadFile, title: str, description: str) -> dict:
    """Ingest an already-edited vertical clip and register it in the clips library.

    The pristine upload is stored in the source directory as a backing
    ``clip_upload`` source (hidden from Projects), and a copy is placed in the
    clip directory as the clip's playable output, marked 'succeeded'. Keeping the
    untouched original lets the clip later be (re)rendered with subtitles, music
    and a banner from a clean source — see ``ClipRenderService.render_uploaded_clip``.
    """

    safe_name = Path(file.filename or "clip.mp4").name
    source_path = settings.source_dir / f"{uuid4().hex}-{safe_name}"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    with source_path.open("wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > settings.max_upload_bytes:
                out.close()
                source_path.unlink(missing_ok=True)
                raise ValueError("clip exceeds MAX_UPLOAD_BYTES")
            out.write(chunk)
    if size == 0:
        source_path.unlink(missing_ok=True)
        raise ValueError("clip file is empty")
    try:
        metadata = probe_media(source_path)
    except ValueError as exc:
        source_path.unlink(missing_ok=True)
        raise ValueError(f"uploaded clip is not a readable video: {exc}") from exc
    source = store.create_source(
        "clip_upload",
        source_path,
        original_filename=safe_name,
        size_bytes=size,
        duration_sec=metadata.duration_sec,
        width=metadata.width,
        height=metadata.height,
        fps=metadata.fps,
        metadata={"standalone_clip": True},
        status="ready",
    )
    clip_path = settings.clip_dir / f"{uuid4().hex}-{safe_name}"
    clip_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_path, clip_path)
    clip = store.create_clip(source["id"], title=title.strip() or Path(safe_name).stem, description=description.strip())
    return store.finish_clip_render(
        clip["id"],
        status="succeeded",
        output_path=clip_path,
        preview_path=clip_path,
        duration_sec=metadata.duration_sec,
        width=metadata.width,
        height=metadata.height,
        size_bytes=size,
    )


async def _save_banner_upload(
    file: UploadFile,
    label: str,
    position: str = "bottom",
    opacity: float = 1,
) -> dict:
    safe_name = Path(file.filename or "banner.webm").name
    dest = settings.banner_dir / f"{uuid4().hex}-{safe_name}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    with dest.open("wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > settings.max_upload_bytes:
                out.close()
                dest.unlink(missing_ok=True)
                raise ValueError("banner exceeds MAX_UPLOAD_BYTES")
            out.write(chunk)
    width = height = 0
    duration_sec = 0.0
    try:
        metadata = probe_media(dest)
        width = metadata.width
        height = metadata.height
        duration_sec = metadata.duration_sec
    except ValueError:
        pass
    return store.create_banner(
        label,
        dest,
        original_filename=safe_name,
        mime_type=file.content_type or "",
        width=width,
        height=height,
        duration_sec=duration_sec,
        position=position,
        opacity=opacity,
    )


async def _save_audio_upload(file: UploadFile, label: str, volume: float = 0.25) -> dict:
    safe_name = Path(file.filename or "track.mp3").name
    dest = settings.audio_dir / f"{uuid4().hex}-{safe_name}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    with dest.open("wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > settings.max_upload_bytes:
                out.close()
                dest.unlink(missing_ok=True)
                raise ValueError("audio track exceeds MAX_UPLOAD_BYTES")
            out.write(chunk)
    duration_sec = 0.0
    try:
        duration_sec = probe_media(dest).duration_sec
    except ValueError:
        pass
    return store.create_audio_track(
        label,
        dest,
        original_filename=safe_name,
        mime_type=file.content_type or "",
        duration_sec=duration_sec,
        volume=volume,
    )


def _payload_data(payload: BaseModel, exclude: set[str] | None = None, exclude_none: bool = False) -> dict:
    if hasattr(payload, "model_dump"):
        return payload.model_dump(exclude=exclude or set(), exclude_none=exclude_none)
    return payload.dict(exclude=exclude or set(), exclude_none=exclude_none)


def _safe_next(value: str) -> str:
    if not value.startswith("/") or value.startswith("//"):
        return "/projects"
    return value


def _optional_form_int(value: str | int | None) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    return int(value)


def _optional_form_float(value: str | float | None) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    return float(value)


def _parse_schedule_dt(value: str | None) -> datetime | None:
    text = str(value or "").strip().replace("T", " ")
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise HTTPException(status_code=400, detail="schedule_start must be YYYY-MM-DD HH:MM")


def _normalize_schedule_text(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    normalized = text.replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(normalized, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    raise ValueError("scheduled_at must be YYYY-MM-DD HH:MM or datetime-local format")


# --------------------------------------------------------------------------- #
# Single-page app (React/Vite build). MUST be registered last so the catch-all
# route has the lowest priority and never shadows /api, /media, /ui, /docs, etc.
# In dev the SPA is served by the Vite dev server (:5173) which proxies here, so
# `frontend/dist` may not exist — guarded accordingly.
# --------------------------------------------------------------------------- #
SPA_DIR = settings.root_dir / "frontend" / "dist"
SPA_INDEX = SPA_DIR / "index.html"
_SPA_RESERVED_PREFIXES = ("api/", "media/", "ui/", "assets/", "static/")
_SPA_RESERVED_EXACT = {"login", "logout", "docs", "openapi.json", "favicon.ico"}

if (SPA_DIR / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=SPA_DIR / "assets"), name="spa-assets")


@app.get("/{spa_path:path}", include_in_schema=False)
def spa_fallback(spa_path: str, _auth: AuthDep) -> FileResponse:
    if not SPA_INDEX.exists():
        raise HTTPException(status_code=404, detail="Not Found")
    first = spa_path.split("/", 1)[0]
    if spa_path.startswith(_SPA_RESERVED_PREFIXES) or first in _SPA_RESERVED_EXACT:
        raise HTTPException(status_code=404, detail="Not Found")
    return FileResponse(SPA_INDEX)
