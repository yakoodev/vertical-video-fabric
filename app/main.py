from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app.ai.service import VideoAnalysisService
from app.auth import AuthRequired, auth_required_handler, require_auth, token_is_valid
from app.crypto import CookieCipher
from app.db import Database
from app.ingest import SourceIngestor, probe_media
from app.render import ClipRenderService
from app.settings import settings
from app.store import AppStore
from app.worker import JobWorker

settings.ensure_dirs()
settings.ensure_api_token()
db = Database(settings.db_path)
db.init()
store = AppStore(db, CookieCipher(settings.secret_key_path))
worker = JobWorker(store)
source_ingestor = SourceIngestor(store)
video_analysis_service = VideoAnalysisService(store)
clip_render_service = ClipRenderService(store)
AuthDep = Annotated[None, Depends(require_auth)]


@asynccontextmanager
async def lifespan(app: FastAPI):
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
app.mount("/static", StaticFiles(directory=settings.root_dir / "app" / "static"), name="static")
templates = Jinja2Templates(directory=settings.root_dir / "app" / "templates")


class AccountCreate(BaseModel):
    platform: Literal["youtube", "tiktok"] = Field(description="Target platform.")
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


class AnalyzeSourceRequest(BaseModel):
    provider: Literal["mock", "polza", "gemini", "artemox"] | None = Field(
        default=None,
        description="Analyzer provider override. Defaults to AI_VIDEO_PROVIDER.",
    )
    model: str | None = Field(default=None, description="Optional model override.")


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
    extra: dict | None = None


class BannerPatchPayload(BaseModel):
    label: str | None = None
    position: Literal["top", "center", "bottom", "custom"] | None = None
    x: int | None = None
    y: int | None = None
    opacity: float | None = None


class SubtitleProfilePayload(BaseModel):
    label: str
    provider: Literal["mock", "polza", "gemini", "artemox"] = "mock"
    model: str = "openai/gpt-4o-transcribe"
    language: str = ""
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


class SubtitleProfilePatchPayload(BaseModel):
    label: str | None = None
    provider: Literal["mock", "polza", "gemini", "artemox"] | None = None
    model: str | None = None
    language: str | None = None
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


class RenderSegmentRequest(BaseModel):
    ffmpeg_preset_id: int | None = None
    subtitle_profile_id: int | None = None


class RenderMontageRequest(BaseModel):
    segment_ids: list[int]
    ffmpeg_preset_id: int | None = None
    subtitle_profile_id: int | None = None
    title: str = "Montage"
    description: str = ""


class ClipPostRequest(BaseModel):
    title: str = ""
    description: str = ""
    targets: list[int]
    privacy: Literal["public", "unlisted", "private"] = "public"
    allow_comments: bool = True


@app.get("/", include_in_schema=False)
def root(_auth: AuthDep) -> RedirectResponse:
    return RedirectResponse(url="/sources", status_code=303)


@app.get("/login", response_class=HTMLResponse, include_in_schema=False)
def login_page(request: Request, next: str = "/sources") -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "login.html",
        {"active": "", "next": _safe_next(next), "error": ""},
    )


@app.post("/login", response_class=HTMLResponse, include_in_schema=False)
def login_submit(
    request: Request,
    token: Annotated[str, Form()],
    next: Annotated[str, Form()] = "/sources",
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


@app.get("/accounts", response_class=HTMLResponse, include_in_schema=False)
def accounts_page(request: Request, _auth: AuthDep) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "accounts.html",
        {"accounts": store.list_accounts(), "active": "accounts"},
    )


@app.post("/ui/accounts", include_in_schema=False)
def ui_add_account(
    _auth: AuthDep,
    platform: Annotated[str, Form()],
    label: Annotated[str, Form()],
    cookie: Annotated[str, Form()],
    proxy_url: Annotated[str, Form()] = "",
) -> RedirectResponse:
    try:
        store.upsert_account(platform, label, cookie, proxy_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url="/accounts", status_code=303)


@app.get("/sources", response_class=HTMLResponse, include_in_schema=False)
def sources_page(request: Request, _auth: AuthDep) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "sources.html",
        {"sources": store.list_sources(), "active": "sources"},
    )


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
) -> RedirectResponse:
    try:
        source = source_ingestor.ingest_url(url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/sources/{source['id']}", status_code=303)


@app.get("/sources/{source_id}", response_class=HTMLResponse, include_in_schema=False)
def source_page(request: Request, source_id: int, _auth: AuthDep) -> HTMLResponse:
    try:
        source = store.get_source(source_id, include_related=True)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return templates.TemplateResponse(
        request,
        "source_detail.html",
        {
            "source": source,
            "active": "sources",
            "default_provider": settings.ai_video_provider,
            "presets": store.list_ffmpeg_presets(),
            "subtitle_profiles": store.list_subtitle_profiles(),
        },
    )


@app.post("/ui/sources/{source_id}/analyze", include_in_schema=False)
def ui_analyze_source(
    source_id: int,
    _auth: AuthDep,
    provider: Annotated[str, Form()] = "",
    model: Annotated[str, Form()] = "",
) -> RedirectResponse:
    try:
        video_analysis_service.run_analysis(
            source_id,
            provider=provider or None,
            model=model or None,
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/sources/{source_id}", status_code=303)


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


@app.get("/clips", response_class=HTMLResponse, include_in_schema=False)
def clips_page(request: Request, _auth: AuthDep) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "clips.html",
        {"clips": store.list_clips(), "active": "clips"},
    )


@app.get("/clips/{clip_id}", response_class=HTMLResponse, include_in_schema=False)
def clip_page(request: Request, clip_id: int, _auth: AuthDep) -> HTMLResponse:
    try:
        clip = store.get_clip(clip_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return templates.TemplateResponse(
        request,
        "clip_detail.html",
        {
            "clip": clip,
            "subtitle_tracks": store.list_subtitle_tracks(clip_id),
            "active": "clips",
            "accounts": store.list_accounts(),
        },
    )


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


@app.post("/ui/clips/{clip_id}/posts", include_in_schema=False)
def ui_create_post_from_clip(
    clip_id: int,
    _auth: AuthDep,
    title: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
    targets: Annotated[list[int], Form()] = [],
    privacy: Annotated[str, Form()] = "public",
    allow_comments: Annotated[bool, Form()] = True,
) -> RedirectResponse:
    try:
        job = store.create_clip_post_job(
            clip_id,
            title,
            description,
            targets,
            privacy,
            allow_comments,
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/jobs/{job['id']}", status_code=303)


@app.get("/presets", response_class=HTMLResponse, include_in_schema=False)
def presets_page(request: Request, _auth: AuthDep) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "presets.html",
        {
            "presets": store.list_ffmpeg_presets(),
            "banners": store.list_banners(),
            "subtitle_profiles": store.list_subtitle_profiles(),
            "active": "presets",
        },
    )


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
    banner_id: Annotated[int | None, Form()] = None,
    subtitle_profile_id: Annotated[int | None, Form()] = None,
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
            banner_id=banner_id,
            subtitle_profile_id=subtitle_profile_id,
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url="/presets", status_code=303)


@app.post("/ui/banners", include_in_schema=False)
async def ui_create_banner(
    _auth: AuthDep,
    file: Annotated[UploadFile, File()],
    label: Annotated[str, Form()],
    position: Annotated[str, Form()] = "bottom",
    opacity: Annotated[float, Form()] = 1,
) -> RedirectResponse:
    try:
        banner = await _save_banner_upload(file, label, position=position, opacity=opacity)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        await file.close()
    return RedirectResponse(url="/presets", status_code=303)


@app.post("/ui/subtitle-profiles", include_in_schema=False)
def ui_create_subtitle_profile(
    _auth: AuthDep,
    label: Annotated[str, Form()],
    provider: Annotated[str, Form()] = "mock",
    model: Annotated[str, Form()] = "openai/gpt-4o-transcribe",
    language: Annotated[str, Form()] = "",
) -> RedirectResponse:
    try:
        store.create_subtitle_profile(label, provider=provider, model=model, language=language)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url="/presets", status_code=303)


@app.get("/posts/new", response_class=HTMLResponse, include_in_schema=False)
def new_post_page(request: Request, _auth: AuthDep) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "new_post.html",
        {"accounts": store.list_accounts(), "active": "new"},
    )


@app.post("/ui/posts", include_in_schema=False)
async def ui_create_post(
    _auth: AuthDep,
    file: Annotated[UploadFile, File()],
    title: Annotated[str, Form()],
    description: Annotated[str, Form()] = "",
    targets: Annotated[list[int], Form()] = [],
    privacy: Annotated[str, Form()] = "public",
    allow_comments: Annotated[bool, Form()] = True,
) -> RedirectResponse:
    job = await _create_post_from_upload(file, title, description, targets, privacy, allow_comments)
    return RedirectResponse(url=f"/jobs/{job['id']}", status_code=303)


@app.get("/jobs", response_class=HTMLResponse, include_in_schema=False)
def jobs_page(request: Request, _auth: AuthDep) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "jobs.html",
        {"jobs": store.list_jobs(), "active": "jobs"},
    )


@app.get("/jobs/{job_id}", response_class=HTMLResponse, include_in_schema=False)
def job_page(request: Request, job_id: int, _auth: AuthDep) -> HTMLResponse:
    try:
        job = store.get_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return templates.TemplateResponse(request, "job_detail.html", {"job": job, "active": "jobs"})


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


@app.post(
    "/api/sources",
    response_model=SourceRead,
    tags=["Sources"],
    summary="Ingest a source video",
    description=(
        "Accepts either multipart `file` upload or JSON body `{ \"url\": \"...\" }`. "
        "Direct mp4/mov/webm URLs and YouTube URLs are supported."
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
                return source_ingestor.ingest_url(url)
            raise ValueError("multipart request must include file or url")
        payload = await request.json()
        url = str(payload.get("url") or "").strip()
        if not url:
            raise ValueError("json request must include url")
        return source_ingestor.ingest_url(url)
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
        return store.get_source(source_id, include_related=True)
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
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
        )
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
            title=payload.title,
            description=payload.description,
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/clips", tags=["Render"], summary="List clips")
def api_clips(_auth: AuthDep) -> list[dict]:
    return store.list_clips()


@app.get("/api/clips/{clip_id}", tags=["Render"], summary="Get clip details")
def api_clip(clip_id: int, _auth: AuthDep) -> dict:
    try:
        clip = store.get_clip(clip_id)
        clip["subtitle_tracks"] = store.list_subtitle_tracks(clip_id)
        return clip
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
        )
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


@app.get("/api/subtitle-profiles", tags=["Render"], summary="List subtitle profiles")
def api_subtitle_profiles(_auth: AuthDep) -> list[dict]:
    return store.list_subtitle_profiles()


@app.post("/api/subtitle-profiles", tags=["Render"], summary="Create subtitle profile")
def api_create_subtitle_profile(payload: SubtitleProfilePayload, _auth: AuthDep) -> dict:
    try:
        return store.create_subtitle_profile(payload.label, **_payload_data(payload, exclude={"label"}))
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
) -> dict:
    return await _create_post_from_upload(file, title, description, targets, privacy, allow_comments)


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
        )
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        await file.close()
    return job


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


def _payload_data(payload: BaseModel, exclude: set[str] | None = None, exclude_none: bool = False) -> dict:
    if hasattr(payload, "model_dump"):
        return payload.model_dump(exclude=exclude or set(), exclude_none=exclude_none)
    return payload.dict(exclude=exclude or set(), exclude_none=exclude_none)


def _safe_next(value: str) -> str:
    if not value.startswith("/") or value.startswith("//"):
        return "/sources"
    return value
