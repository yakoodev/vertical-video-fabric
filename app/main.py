from __future__ import annotations

import shutil
import subprocess
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app.ai.service import VideoAnalysisService
from app.automation import AutomationService
from app.auth import AuthRequired, auth_required_handler, require_auth, token_is_valid
from app.crypto import CookieCipher
from app.db import Database
from app.default_pack import seed_default_brand, seed_default_pack, seed_music
from app.downloads import DownloadService
from app import memory
from app import modules
from app.ingest import SourceIngestor, probe_media
from app.render import ClipRenderService
from app import proxy_config
from app.settings import settings
from app.ai.asset_describe import describe_asset, describe_missing
from app.ai.asset_generate import add_from_url as add_asset_from_url
from app.ai.asset_search import fetch_preview as fetch_asset_preview
from app.ai.asset_search import search_images
from app.ai.asset_generate import generate_asset
from app.montage_assets import assets_dir as montage_assets_dir
from app.montage_assets import kind_for as asset_kind_for
from app.montage_assets import probe_asset as probe_montage_asset
from app.ai.publish_meta import generate_publish_metadata
from app.compression import REVALIDATE, ImmutableAssets, TextGzipMiddleware
from app.thumbs import asset_thumb, clip_thumb
from app.store import AppStore
from app.worker import JobWorker
from app.platform_stats import StatsCollector
from app.api_common import THUMB_CACHE, _optional_form_float, _optional_form_int, _payload_data, _safe_next
from app.agent_api import router as agent_router

settings.ensure_dirs()
# Пока потоков мало: меньше арен malloc — меньше «осевшей» памяти (OOM 25–26.10, app/memory.py).
memory.limit_arenas()
memory.log_to_stderr()
settings.ensure_api_token()
db = Database(settings.db_path)
db.init()
store = AppStore(db, CookieCipher(settings.secret_key_path))
proxy_config.register(lambda: store)
seed_default_pack(store)
seed_music(store)
seed_default_brand(store)
worker = JobWorker(store)
stats_collector = StatsCollector(store)
from app.autopilot import AutopilotWorker  # noqa: E402 - автопилот: карточки, Telegram, сводки

autopilot_worker = AutopilotWorker(store)
source_ingestor = SourceIngestor(store)
video_analysis_service = VideoAnalysisService(store)
clip_render_service = ClipRenderService(store)
download_service = DownloadService(source_ingestor)
# «Авто»: публикует только через одобрение (submit_clip), качает фоново через download_service.
automation_service = AutomationService(store, source_ingestor, video_analysis_service, clip_render_service, download_service)
from app.vod_watch import VodWatchWorker  # noqa: E402 - наблюдатель VOD: новый эфир → нарезка → одобрение

vod_watch_worker = VodWatchWorker(store, download_service, automation_service)
from app.aivideo_gen import AiVideoWorker  # noqa: E402 - ИИ-видео: одобренные этапы, опрос после перезапуска

aivideo_worker = AiVideoWorker(store)
from app.explainer_gen import ExplainerWorker  # noqa: E402 - объяснялки: тема → сценарий → позы → карточка → клип

explainer_worker = ExplainerWorker(store)
from app.stories_gen import StoriesWorker  # noqa: E402 - истории: завязка → сценарий → сцены → карточка → клип

stories_worker = StoriesWorker(store)
AuthDep = Annotated[None, Depends(require_auth)]

# Модули сервиса (app/modules.py): что выключено в «Настройки → Модули». Хранилище и работники —
# через лямбды: тесты подменяют app.main.store / worker / … на свои.
modules.bind_store(lambda: store)
modules.register_worker("publishing", "job-worker", lambda: worker)
modules.register_worker("platform_stats", "stats-collector", lambda: stats_collector)
modules.register_worker("autopilot", "autopilot", lambda: autopilot_worker)
modules.register_worker("vod_watch", "vod-watch", lambda: vod_watch_worker)
modules.register_worker("ai_video", "aivideo-gen", lambda: aivideo_worker)
modules.register_worker("explainer", "explainer", lambda: explainer_worker)
modules.register_worker("stories", "stories", lambda: stories_worker)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # До запуска работников: сейчас в этом процессе ничего не выполняется, всё «идёт» в базе — от прошлого запуска.
    store.recover_after_restart()
    try:
        store.dedup_unused_ffmpeg_presets()
    except Exception:  # noqa: BLE001 - cleanup must never block startup
        pass
    # Работники включённых модулей; выключенные не стартуют, включат — запустятся без перезапуска.
    modules.start_workers(store)
    try:
        yield
    finally:
        modules.stop_workers()


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
        {"name": "Brands", "description": "Brand kits: one look (font, colors, logo, CTA, music) for a client's clips."},
        {"name": "Posts", "description": "Upload a source video and queue publication jobs."},
        {"name": "Jobs", "description": "Inspect queued, running, and completed jobs."},
    ],
)
app.add_exception_handler(AuthRequired, auth_required_handler)
# Выключенный модуль: его адреса — 423 (ASGI-прослойка), вызов изнутри сервиса — тоже 423.
from app.modules_api import ModuleGate, build_modules_router, module_disabled_handler  # noqa: E402

app.add_middleware(ModuleGate, get_store=lambda: store)
from app.agent_settings_api import AgentStopGate, build_agent_settings_router  # noqa: E402
app.add_middleware(AgentStopGate, get_store=lambda: store)
# Снаружи всех: страницы идут через домен, а JS и списки весят мегабайты (app/compression.py).
app.add_middleware(TextGzipMiddleware)
app.add_exception_handler(modules.ModuleDisabled, module_disabled_handler)
templates =Jinja2Templates(directory=settings.root_dir / "app" / "templates")


@app.get("/", include_in_schema=False)
def root(_auth: AuthDep) -> Any:
    # Первый экран решает клиент: последний открытый проект (App.tsx: Home).
    if SPA_INDEX.exists():
        return FileResponse(SPA_INDEX, headers={"Cache-Control": REVALIDATE})
    return RedirectResponse(url="/projects", status_code=303)


@app.get("/login", response_class=HTMLResponse, include_in_schema=False)
def login_page(request: Request, next: str = "/") -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "login.html",
        {"active": "", "next": _safe_next(next), "error": ""},
    )


@app.post("/login", response_class=HTMLResponse, include_in_schema=False)
def login_submit(
    request: Request,
    token: Annotated[str, Form()],
    next: Annotated[str, Form()] = "/",
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
    defaults = _settings_panel_data(store)
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


@app.get("/media/clips/{clip_id}/thumb", include_in_schema=False)
def clip_thumb_media(clip_id: int, _auth: AuthDep) -> FileResponse:
    try:
        clip = store.get_clip(clip_id)
        return FileResponse(clip_thumb(clip, store.get_clip_cover_path(clip_id)), headers=THUMB_CACHE)
    except (KeyError, ValueError, OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(status_code=404, detail="clip thumb not found") from exc


@app.get("/media/clips/{clip_id}/cover", include_in_schema=False)
def clip_cover_media(clip_id: int, _auth: AuthDep) -> FileResponse:
    """The rendered clip's cover still (see app/cover.py)."""
    try:
        raw = store.get_clip_cover_path(clip_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not raw:
        raise HTTPException(status_code=404, detail="clip has no cover")
    path = Path(raw)
    try:
        path.resolve(strict=False).relative_to(settings.clip_dir.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="cover not found") from exc
    if not path.exists():
        raise HTTPException(status_code=404, detail="cover not found")
    return FileResponse(path, media_type="image/jpeg")


COVER_UPLOAD_MAX_BYTES = 20 * 1024 * 1024


@app.post("/api/covers", tags=["Render"], summary="Upload a cover image (fitted to the clip format's frame JPEG)")
async def api_upload_cover(
    _auth: AuthDep,
    file: UploadFile = File(...),
    format: Annotated[str, Form(description="формат клипа: 9:16 (1080×1920, по умолчанию), 1:1, 4:5, 16:9 (1920×1080)")] = "",
    clip_plan_id: Annotated[int | None, Form(description="или клип — формат из его настроек")] = None,
) -> dict:
    """Store an uploaded picture as a cover. Normalised by ffmpeg into a JPEG of the
    format's frame (app/formats.py: 9:16 — 1080×1920 as always, 16:9 — 1920×1080, the
    YouTube thumbnail) under a generated name — the only kind of name clip settings accept.
    The render fits the cover to the output frame again, so a cover of another format still works."""
    from app.formats import frame_size, normalize_format, settings_render_format

    try:
        if format:
            fmt = normalize_format(format)
        elif clip_plan_id is not None:
            fmt = settings_render_format(store.get_clip_plan(int(clip_plan_id)).get("render_settings"))
        else:
            fmt = normalize_format(None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    width, height = frame_size(fmt)
    covers = settings.data_dir / "covers"
    covers.mkdir(parents=True, exist_ok=True)
    data = await file.read(COVER_UPLOAD_MAX_BYTES + 1)
    if not data:
        raise HTTPException(status_code=400, detail="пустой файл")
    if len(data) > COVER_UPLOAD_MAX_BYTES:
        raise HTTPException(status_code=413, detail="картинка больше 20 МБ")
    name = f"{uuid4().hex}.jpg"
    raw = settings.tmp_dir / f"cover-upload-{name}.bin"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(data)
    out = covers / name
    try:
        proc = subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(raw),
             "-vf", f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1",
             "-frames:v", "1", "-q:v", "2", str(out)],
            capture_output=True, text=True, timeout=60, check=False,
        )
    finally:
        raw.unlink(missing_ok=True)
    if proc.returncode != 0 or not out.exists():
        raise HTTPException(status_code=400, detail="это не картинка (ffmpeg не смог её прочитать)")
    return {"image": name, "url": f"/media/covers/{name}"}


@app.get("/media/covers/{name}", include_in_schema=False)
def cover_image_media(name: str, _auth: AuthDep) -> FileResponse:
    if len(name) != 36 or not name.endswith(".jpg") or not set(name[:-4]) <= set("0123456789abcdef"):
        raise HTTPException(status_code=404, detail="cover not found")
    path = settings.data_dir / "covers" / name
    if not path.exists():
        raise HTTPException(status_code=404, detail="cover not found")
    return FileResponse(path, media_type="image/jpeg")


def _wants_wait(value) -> bool:
    if value is None or isinstance(value, bool):
        return value is not False
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


async def _download_source_url(url: str, fields) -> dict | JSONResponse:
    """URL источника — фоновой загрузкой (download_service), ответ — как раньше: источник.

    Раньше обработчик (async) качал синхронно: 4,8-часовой Twitch VOD держал event loop
    40 минут, и весь API стоял. Теперь загрузка в своём потоке и видна в «Активности»,
    а запрос ждёт её через asyncio.sleep. ``wait: false`` — сразу задача загрузки (202).
    """
    task = download_service.start(
        url,
        quality=str(fields.get("quality") or ""),
        selection={
            "media_url": str(fields.get("smotvibe_media_url") or ""),
            "referer": str(fields.get("smotvibe_referer") or ""),
            "audio_format_id": str(fields.get("smotvibe_audio_format_id") or ""),
            "filename_label": str(fields.get("smotvibe_filename_label") or ""),
        },
        ingestor=source_ingestor,
    )
    if not _wants_wait(fields.get("wait")):
        return JSONResponse(task, status_code=202)
    task = await download_service.wait(task["id"])
    if task.get("source_id"):
        # Скачалось, но ffprobe не прочитал — источник со status failed, как раньше.
        return store.get_source(task["source_id"])
    if task["status"] == "cancelled":
        raise ValueError("download cancelled")
    raise ValueError(task.get("error") or "download failed")


# ---- «Файлы для монтажа»: memes / reactions / sounds for the AI montage ------

MAX_ASSET_BYTES = 300 * 1024 * 1024


def _asset_owner(clip_id: int | None = None, project_id: int | None = None) -> int | None:
    """Чей новый файл (app/asset_scope.py): проект клипа, иначе заданный проект, иначе None — общий."""
    from app.asset_scope import clip_project
    from app.projects import get_project

    try:
        if clip_id:
            return clip_project(store, int(clip_id))
        if project_id:
            return int(get_project(store, int(project_id), include_sources=False)["id"])
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return None


@app.get("/api/montage-assets", tags=["Montage assets"], summary="List the montage library")
def api_list_montage_assets(
    _auth: AuthDep,
    scope: Annotated[str, Query(description="all — все, shared — только общие")] = "all",
    project_id: Annotated[int | None, Query(description="только файлы этого проекта")] = None,
    clip_id: Annotated[int | None, Query(description="что доступно клипу: общие и его проекта плюс чужие файлы, "
                                                     "уже стоящие в нём (in_clip_only)")] = None,
) -> list[dict]:
    from app.asset_scope import assets_for_clip, render_asset_ids

    if clip_id is not None:
        try:
            render = store.get_clip_plan(clip_id, include_segments=False).get("render_settings") or {}
            return assets_for_clip(store, clip_id, render_asset_ids(render))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    if project_id is not None:
        return store.list_montage_assets(project_id=project_id)
    if scope == "shared":
        return store.list_montage_assets(visible_to=())
    return store.list_montage_assets()


@app.post("/api/montage-assets", tags=["Montage assets"], summary="Upload a meme / reaction / sound")
async def api_upload_montage_asset(
    _auth: AuthDep,
    file: Annotated[UploadFile, File()],
    label: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
    tags: Annotated[str, Form()] = "",
    project_id: Annotated[int | None, Form(description="файл этого проекта; нет — общий")] = None,
    clip_id: Annotated[int | None, Form(description="файл проекта этого клипа (важнее project_id)")] = None,
) -> dict:
    safe_name = Path(file.filename or "asset").name
    kind = asset_kind_for(safe_name, file.content_type or "")
    if kind is None:
        raise HTTPException(status_code=400, detail="нужна картинка, GIF, видео или звук")
    owner = _asset_owner(clip_id, project_id)
    dest = montage_assets_dir() / f"{uuid4().hex}-{safe_name}"
    size = 0
    try:
        with dest.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_ASSET_BYTES:
                    raise HTTPException(status_code=413, detail="файл больше 300 МБ — для мема многовато")
                out.write(chunk)
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise
    finally:
        await file.close()
    meta = probe_montage_asset(dest)
    asset = store.create_montage_asset(
        kind=kind,
        label=label.strip() or Path(safe_name).stem,
        description=description,
        tags=tags,
        file_path=str(dest),
        original_filename=safe_name,
        mime_type=file.content_type or "",
        size_bytes=size,
        project_id=owner,
        **meta,
    )
    # Describe it right away: the montage agent matches files by their text, so a
    # file uploaded without «когда уместно» would never be used. Модуль «ИИ для файлов» выключен — нет.
    if modules.is_enabled("asset_ai", store):
        threading.Thread(
            target=lambda: describe_asset(store, asset["id"]),
            name=f"describe-asset-{asset['id']}",
            daemon=True,
        ).start()
    # Анимированная картинка (GIF/APNG/webm с альфой): кадры и fps — для vvf.py asset и агента.
    from app.anim_frames import animation_info, brief, describe as describe_anim

    anim = animation_info(dest) if kind in ("gif", "image", "video") else {}
    if anim.get("animated"):
        asset = {**asset, "animation": brief(anim), "animation_note": describe_anim(anim)}
    return asset


class GenerateAssetRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=400, description="что нарисовать")
    label: str = Field(default="", max_length=120)
    when: str = Field(default="", max_length=300, description="когда уместно вставлять")
    project_id: int | None = Field(default=None, description="файл этого проекта; нет — общий")
    clip_id: int | None = Field(default=None, description="файл проекта этого клипа")


@app.post(
    "/api/montage-assets/generate",
    tags=["Montage assets"],
    summary="✨ ИИ рисует оригинальную картинку и кладёт её в библиотеку",
)
def api_generate_montage_asset(payload: GenerateAssetRequest, _auth: AuthDep) -> dict:
    try:
        return generate_asset(store, payload.prompt, label=payload.label, when=payload.when,
                              project_id=_asset_owner(payload.clip_id, payload.project_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


class SearchAssetsRequest(BaseModel):
    query: str = Field(min_length=2, max_length=200)
    count: int = Field(default=6, ge=1, le=8)
    free_only: bool = True


@app.post(
    "/api/montage-assets/search",
    tags=["Montage assets"],
    summary="🔎 ИИ ищет картинку в интернете — возвращает кандидатов, НИЧЕГО не скачивая",
)
def api_search_montage_assets(payload: SearchAssetsRequest, _auth: AuthDep) -> dict:
    try:
        return search_images(payload.query, payload.count, free_only=payload.free_only)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/media/asset-preview", include_in_schema=False)
def media_asset_preview(url: str, _auth: AuthDep) -> Response:
    """Превью найденной картинки: многие сайты не отдают файл напрямую в <img>."""
    try:
        blob, mime = fetch_asset_preview(url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - remote errors
        raise HTTPException(status_code=502, detail=f"не удалось загрузить: {str(exc)[:120]}") from exc
    return Response(content=blob, media_type=mime, headers={"Cache-Control": "private, max-age=600"})


class AssetFromUrlRequest(BaseModel):
    url: str = Field(max_length=2000)
    label: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=500)
    project_id: int | None = Field(default=None, description="файл этого проекта; нет — общий")
    clip_id: int | None = Field(default=None, description="файл проекта этого клипа")


@app.post(
    "/api/montage-assets/from-url",
    tags=["Montage assets"],
    summary="Добавить файл в библиотеку по прямой ссылке",
)
def api_add_montage_asset_from_url(payload: AssetFromUrlRequest, _auth: AuthDep) -> dict:
    owner = _asset_owner(payload.clip_id, payload.project_id)
    try:
        asset = add_asset_from_url(store, payload.url, label=payload.label, description=payload.description,
                                   project_id=owner)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - network/remote errors
        raise HTTPException(status_code=502, detail=f"не удалось скачать: {str(exc)[:200]}") from exc
    if modules.is_enabled("asset_ai", store):
        threading.Thread(target=lambda: describe_asset(store, asset["id"]), daemon=True).start()
    return asset


class VideoFromUrlRequest(BaseModel):
    url: str = Field(max_length=2000)
    start: float = Field(ge=0, description="секунда ролика по ссылке — начало отрезка")
    end: float = Field(gt=0, description="конец отрезка (не дольше 180 с от start)")
    label: str = Field(default="", max_length=120)
    project_id: int | None = Field(default=None, description="файл этого проекта; нет — общий")
    clip_id: int | None = Field(default=None, description="файл проекта этого клипа")


@app.post(
    "/api/montage-assets/video-from-url",
    tags=["Montage assets"],
    summary="Отрезок ролика по ссылке (yt-dlp, ≤ 180 с, ≤ 1080p, со звуком) → видео в библиотеку",
)
def api_add_montage_video_from_url(payload: VideoFromUrlRequest, _auth: AuthDep) -> dict:
    """Скачивается только отрезок, тем же yt-dlp и прокси, что источники (app/video_import.py).
    Звук файла играет в клипе, если у слоя ``video`` стоит ``props.audio: true``."""
    from app.video_import import import_video_from_url

    owner = _asset_owner(payload.clip_id, payload.project_id)
    try:
        return import_video_from_url(store, payload.url, payload.start, payload.end, payload.label, project_id=owner)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - network/remote errors
        raise HTTPException(status_code=502, detail=f"не удалось скачать: {str(exc)[:200]}") from exc


class DescribeAssetsRequest(BaseModel):
    asset_ids: list[int] = Field(default_factory=list, description="пусто = все без описания")


@app.post(
    "/api/montage-assets/describe",
    tags=["Montage assets"],
    summary="✨ Let the AI look at the files and fill in what they are / when they fit",
)
def api_describe_montage_assets(payload: DescribeAssetsRequest, _auth: AuthDep) -> dict:
    return describe_missing(store, payload.asset_ids or None)


@app.post(
    "/api/montage-assets/{asset_id}/describe",
    tags=["Montage assets"],
    summary="✨ Describe one file",
)
def api_describe_montage_asset(asset_id: int, _auth: AuthDep) -> dict:
    try:
        return describe_asset(store, asset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


class MontageAssetPatch(BaseModel):
    label: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    tags: str | None = Field(default=None, max_length=200)
    project_id: int | None = Field(default=None, description="перенос файла: id проекта, null — в общие")


@app.patch("/api/montage-assets/{asset_id}", tags=["Montage assets"],
           summary="Rename / describe / tag a file, move it to a project")
def api_patch_montage_asset(asset_id: int, payload: MontageAssetPatch, _auth: AuthDep) -> dict:
    fields = _payload_data(payload, exclude_none=True)
    if "project_id" in payload.model_fields_set:  # null здесь значим: перенос в общие
        fields["project_id"] = _asset_owner(None, payload.project_id)
    try:
        return store.update_montage_asset(asset_id, **fields)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete("/api/montage-assets/{asset_id}", tags=["Montage assets"], summary="Delete a file from the library")
def api_delete_montage_asset(asset_id: int, _auth: AuthDep) -> dict:
    try:
        store.delete_montage_asset(asset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": True}


@app.get("/media/montage-assets/{asset_id}/thumb", include_in_schema=False)
def media_montage_asset_thumb(asset_id: int, _auth: AuthDep) -> FileResponse:
    try:
        return FileResponse(asset_thumb(store.get_montage_asset(asset_id)), headers=THUMB_CACHE)
    except (KeyError, ValueError, OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(status_code=404, detail="asset thumb not found") from exc


@app.get("/media/montage-assets/{asset_id}", include_in_schema=False)
def media_montage_asset(asset_id: int, _auth: AuthDep) -> FileResponse:
    try:
        asset = store.get_montage_asset(asset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    path = Path(asset["file_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="file missing")
    return FileResponse(path, media_type=asset.get("mime_type") or None)


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
    "/api/clips/{clip_id}/publish-meta",
    tags=["Render"],
    summary="Generate title, description and hashtags for a clip",
)
def api_clip_publish_meta(clip_id: int, _auth: AuthDep) -> dict:
    try:
        return generate_publish_metadata(store, clip_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=f"Не удалось сгенерировать: {exc}") from exc


class CoversPayload(BaseModel):
    count: int = Field(default=3, ge=1, le=6, description="сколько вариантов (1–6)")
    texts: list[str] | None = Field(default=None, description="свои тексты (2–5 слов); нет — ИИ/заголовок клипа")
    styles: list[str] | None = Field(default=None, description="yellow | white | plate | accent по вариантам")


class CoverChoicePayload(BaseModel):
    apply_to_render: bool = Field(default=True, description="ещё и обложка плана: следующий рендер вошьёт её")


@app.get("/api/clips/{clip_id}/covers", tags=["Render"], summary="Cover variants of a rendered clip")
def api_clip_covers(clip_id: int, _auth: AuthDep) -> dict:
    from app.cover_variants import list_covers

    try:
        store.get_clip(clip_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"clip_id": clip_id, "covers": list_covers(store, clip_id)}


@app.post(
    "/api/clips/{clip_id}/covers",
    tags=["Render"],
    summary="Generate cover variants: strong frames + big Russian hook text (app/cover_variants.py)",
)
def api_generate_clip_covers(clip_id: int, payload: CoversPayload, _auth: AuthDep) -> dict:
    from app.cover_variants import generate_covers

    try:
        return generate_covers(store, clip_id, count=payload.count, texts=payload.texts, styles=payload.styles)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=f"Обложки не получились: {exc}") from exc


@app.post(
    "/api/clips/{clip_id}/covers/{cover_id}/choose",
    tags=["Render"],
    summary="Make a cover variant the clip's cover (preview, approval card, YouTube thumbnail for 16:9)",
)
def api_choose_clip_cover(clip_id: int, cover_id: int, _auth: AuthDep, payload: CoverChoicePayload | None = None) -> dict:
    from app.cover_variants import choose_cover

    try:
        return choose_cover(store, cover_id, apply_to_render=(payload or CoverChoicePayload()).apply_to_render,
                            clip_id=clip_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/media/clips/{clip_id}/covers/{cover_id}", include_in_schema=False)
def clip_cover_variant_media(clip_id: int, cover_id: int, _auth: AuthDep) -> FileResponse:
    from app.cover_variants import cover_file

    try:
        path = cover_file(store, clip_id, cover_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="cover not found") from exc
    return FileResponse(path, media_type="image/jpeg")


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


from app.settings_api import _settings_panel_data, build_settings_router

app.include_router(build_settings_router(AuthDep, lambda: store))

from app.publishing_api import build_publishing_router  # noqa: E402

app.include_router(build_publishing_router(AuthDep, lambda: store))

from app.stats_api import build_stats_router  # noqa: E402

app.include_router(build_stats_router(AuthDep, lambda: store))

from app.automation_api import build_automation_router  # noqa: E402

app.include_router(build_automation_router(
    AuthDep, lambda: store, lambda: automation_service, lambda: download_service,
))

from app.sources_api import build_sources_router  # noqa: E402

app.include_router(build_sources_router(
    AuthDep, lambda: store, lambda: source_ingestor, lambda: download_service, lambda: video_analysis_service, _download_source_url,
))

from app.render_library_api import build_render_library_router  # noqa: E402

app.include_router(build_render_library_router(AuthDep, lambda: store))

from app.clip_plans_api import build_clip_plans_router  # noqa: E402

app.include_router(build_clip_plans_router(AuthDep, lambda: store, lambda: clip_render_service))

from app.clip_edit_api import build_clip_edit_router  # noqa: E402

app.include_router(build_clip_edit_router(AuthDep, lambda: store))

from app.subtitles_api import build_subtitles_router  # noqa: E402

app.include_router(build_subtitles_router(AuthDep, lambda: store))

from app.montage_api import build_montage_router  # noqa: E402

app.include_router(build_montage_router(AuthDep, lambda: store, lambda: clip_render_service))

# Моушн-слои: схема, проверка, кадр с камерой и слоями (app/motion_api.py).
from app.motion_api import build_motion_router  # noqa: E402

app.include_router(build_motion_router(AuthDep, lambda: store))

# Концовка-баннер (размытие + анимированный баннер) и SVG → webm с альфой (app/end_screen_api.py).
from app.end_screen_api import build_end_screen_router  # noqa: E402

app.include_router(build_end_screen_router(AuthDep, lambda: store))

# Раскладки стрима: автопоиск и сохранённая вебка источника (app/layout_api.py).
from app.layout_api import build_layout_router  # noqa: E402

app.include_router(build_layout_router(AuthDep, lambda: store))

# ✂ Джамп-каты: найти паузы/слова-паразиты и сохранить вырезы клипа (app/cuts.py).
from app.cuts_api import build_cuts_router  # noqa: E402

app.include_router(build_cuts_router(AuthDep, lambda: store))

# ▶ Смотреть монтаж: план проигрывания, прокси 480p и пакет для монтажки (app/preview_api.py).
from app.clip_groups_api import build_clip_groups_router  # noqa: E402

# Клип как группа планов: сетка, экран /clips/:id, версии и откат (app/clip_groups.py).
app.include_router(build_clip_groups_router(AuthDep, lambda: store))
from app.preview_api import build_preview_router  # noqa: E402

app.include_router(build_preview_router(AuthDep, lambda: store))

# 🔎 Проверка клипа перед сдачей: зоны площадок, темп чтения, вспышки, луп (app/quality_lint.py).
from app.quality_lint_api import build_lint_router  # noqa: E402

app.include_router(build_lint_router(AuthDep, lambda: store))

# 🎚 Сведение звука и справочник эффектов (app/audio_mix.py, app/audio_fx.py) — вкладка «Звук» редактора.
from app.audio_mix_api import build_audio_mix_router  # noqa: E402

app.include_router(build_audio_mix_router(AuthDep, lambda: store))


app.include_router(agent_router)  # 💬 ИИ-монтажёр: чат редактора + MCP (/mcp), до SPA-ловушки

# 💬 Чат Twitch-VOD: импорт (авто для Twitch-источников), активность, чат клипа (app/chat_api.py).
from app.chat_api import build_chat_router  # noqa: E402

app.include_router(build_chat_router(AuthDep, lambda: store, ingestor=source_ingestor))

# Проекты (серии и каналы): бриф, правила владельца, статистика, гипотезы (app/projects_api.py).
from app.projects_api import build_projects_router  # noqa: E402

app.include_router(build_projects_router(AuthDep, lambda: store))

# Боковая панель и лестница Проект → Видео → Клип: лёгкие счётчики и список видео (app/nav.py).
from app.nav_api import build_nav_router  # noqa: E402

app.include_router(build_nav_router(AuthDep, lambda: store))

from app.releases_api import build_releases_router  # noqa: E402

app.include_router(build_releases_router(AuthDep, lambda: store))

# Автопилот канала: одобрение, расписание, обучение, Telegram, чат проекта (app/autopilot_api.py).
from app.autopilot_api import build_autopilot_router  # noqa: E402

app.include_router(build_autopilot_router(AuthDep, lambda: store))
app.include_router(build_agent_settings_router(AuthDep, lambda: store))
# Страница «Чат»: переписки с агентом, поток ответа, подтверждения, откат (app/agent_chat_api.py).
from app.agent_chat_api import build_agent_chat_router  # noqa: E402

app.include_router(build_agent_chat_router(AuthDep, lambda: store, {
    "downloads": lambda: download_service, "ingestor": lambda: source_ingestor,
    "analysis": lambda: video_analysis_service}))
# Наблюдатель VOD проекта-стрима (app/vod_watch.py).
from app.vod_watch_api import build_vod_watch_router  # noqa: E402

app.include_router(build_vod_watch_router(AuthDep, lambda: store, lambda: (download_service, automation_service)))

# Контент моушн-слоёв: шрифты, эмодзи, стикеры, звуки, мемы (app/motion_assets.py).
from app.motion_assets import build_router as build_motion_assets_router  # noqa: E402

app.include_router(build_motion_assets_router(AuthDep, lambda: store))

# ИИ-видео: проект, где ролик рисуется с нуля — этапы с оценкой и одобрением (app/aivideo_api.py).
from app.aivideo_api import build_aivideo_router  # noqa: E402

app.include_router(build_aivideo_router(AuthDep, lambda: store))

# Объяснялки: маскот, библиотека поз, ролик «тема → клип» (app/explainer_api.py).
from app.explainer_api import build_explainer_router  # noqa: E402

app.include_router(build_explainer_router(AuthDep, lambda: store))
# Канал объяснялок: маскот, очередь тем, ролики к слотам, карточки одобрения (app/explainer_channel_api.py).
from app.explainer_channel_api import build_explainer_channel_router  # noqa: E402

app.include_router(build_explainer_channel_router(AuthDep, lambda: store))

# Истории: мир, актёры, локации, библиотека сцен, эпизод «завязка → клип» (app/stories_api.py).
from app.stories_api import build_stories_router  # noqa: E402

app.include_router(build_stories_router(AuthDep, lambda: store))

# Очередь 3D-рендера: воркер на хосте с Blender берёт задания, шлёт пульс, отдаёт mp4 (app/render_queue.py).
from app.render_queue import build_render3d_router, on_done as _render3d_on_done  # noqa: E402
from app.stories_gen import kick as _stories_kick  # noqa: E402

app.include_router(build_render3d_router(AuthDep, lambda: store))
_render3d_on_done(_stories_kick)  # рендер готов — работник историй сразу собирает клип

# Модули сервиса: список, переключение владельцем, живые ресурсы (app/modules_api.py).
app.include_router(build_modules_router(AuthDep, lambda: store))


# --------------------------------------------------------------------------- #
# Single-page app (React/Vite build). MUST be registered last so the catch-all
# route has the lowest priority and never shadows /api, /media, /ui, /docs, etc.
# 🖼 Редактор превью и ссылка на редактирование /c/<token> (app/cover_docs.py): до SPA-ловушки,
# страница по ссылке открывается без входа.
from app.cover_docs_api import build_cover_docs_router  # noqa: E402

from app.stream_questions_api import build_stream_questions_router  # noqa: E402

app.include_router(build_stream_questions_router(AuthDep, lambda: store))
app.include_router(build_cover_docs_router(AuthDep, lambda: store, lambda: settings.root_dir / "frontend" / "dist" / "index.html"))

# In dev the SPA is served by the Vite dev server (:5173) which proxies here, so
# `frontend/dist` may not exist — guarded accordingly.
# --------------------------------------------------------------------------- #
SPA_DIR = settings.root_dir / "frontend" / "dist"
SPA_INDEX = SPA_DIR / "index.html"
_SPA_RESERVED_PREFIXES = ("api/", "media/", "ui/", "assets/", "static/")
_SPA_RESERVED_EXACT = {"login", "logout", "docs", "openapi.json", "favicon.ico"}

if (SPA_DIR / "assets").is_dir():
    app.mount("/assets", ImmutableAssets(directory=SPA_DIR / "assets"), name="spa-assets")


@app.get("/{spa_path:path}", include_in_schema=False)
def spa_fallback(spa_path: str, _auth: AuthDep) -> FileResponse:
    if not SPA_INDEX.exists():
        raise HTTPException(status_code=404, detail="Not Found")
    first = spa_path.split("/", 1)[0]
    if spa_path.startswith(_SPA_RESERVED_PREFIXES) or first in _SPA_RESERVED_EXACT:
        raise HTTPException(status_code=404, detail="Not Found")
    # index.html must never be cached: assets are content-hashed (safe to cache
    # forever), but a stale index.html references old hashes and 404s after a
    # redeploy — the "blank page until hard-refresh" bug. Always revalidate it.
    return FileResponse(SPA_INDEX, headers={"Cache-Control": REVALIDATE})
