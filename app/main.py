from __future__ import annotations

import shutil
import subprocess
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app.clip_spec import SpecError
from app.clip_history import snapshot as snapshot_clip
from app.clip_settings import settings_to_render_kwargs
from app.ai.service import VideoAnalysisService
from app.automation import AutomationService
from app.auth import AuthRequired, auth_required_handler, is_agent_request, require_auth, token_is_valid
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
from app.ai.montage import (
    apply_montage,
    latest_job_for_source,
    propose_montage,
    start_montage_job,
    undo_montage,
)
from app.ai.asset_describe import describe_asset, describe_missing
from app.ai.asset_generate import add_from_url as add_asset_from_url
from app.ai.asset_search import fetch_preview as fetch_asset_preview
from app.ai.asset_search import search_images
from app.ai.asset_generate import generate_asset
from app.ai.pick import pick_best
from app.clip_subtitles import generate_for_plan as generate_plan_subtitles
from app.clip_subtitles import save_lines as save_plan_subtitle_lines
from app.montage_assets import assets_dir as montage_assets_dir
from app.montage_assets import kind_for as asset_kind_for
from app.montage_assets import probe_asset as probe_montage_asset
from app.ai.publish_meta import generate_publish_metadata
from app.compression import REVALIDATE, ImmutableAssets, TextGzipMiddleware
from app.thumbs import asset_thumb, clip_thumb
from app.store import AppStore
from app.worker import JobWorker
from app.platform_stats import StatsCollector
from app.api_common import (
    THUMB_CACHE, _default_subtitle_profile_id, _optional_form_float, _optional_form_int, _payload_data, _safe_next,
)
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
from app.agent_settings_api import AgentStopGate, build_agent_settings_router, refuse_agent_drafts  # noqa: E402
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


class AiMontageRequest(BaseModel):
    goal: str = Field(default="", max_length=500, description="пожелание: «короче», «больше мемности»…")


@app.post(
    "/api/clip-plans/{clip_plan_id}/ai-montage",
    tags=["Clip file"],
    summary="🤖 ИИ-монтаж: propose a re-edit of the clip (nothing is applied)",
)
def api_clip_plan_ai_montage(clip_plan_id: int, _auth: AuthDep, payload: AiMontageRequest | None = None) -> dict:
    """Returns ``{spec, diff, rationale, attempts}``; apply with PUT …/spec."""
    try:
        return propose_montage(store, clip_plan_id, goal=(payload.goal if payload else ""))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


class PlanSubtitlesRequest(BaseModel):
    provider: str | None = Field(default=None, description="whisper | gemini — иначе как в стиле клипа")
    model: str | None = None


class PlanSubtitleLine(BaseModel):
    start: float
    end: float
    text: str = Field(max_length=300)


class PlanSubtitleWord(BaseModel):
    word: str = Field(max_length=80)
    start: float
    end: float
    speaker: str | None = Field(default=None, max_length=1)
    emph: int | None = Field(default=None, ge=0, le=2, description="эмфаза слова: 1 pop, 2 shake")


class PlanSubtitleLines(BaseModel):
    lines: list[PlanSubtitleLine]
    words: list[PlanSubtitleWord] | None = Field(default=None, description="пословные тайминги, если распознавали снаружи")


@app.get(
    "/api/clip-plans/{clip_plan_id}/subtitles",
    tags=["Subtitles"],
    summary="Субтитры клипа (строки для правки до рендера)",
)
def api_get_plan_subtitles(clip_plan_id: int, _auth: AuthDep) -> dict:
    try:
        payload = store.get_clip_plan_subtitles(clip_plan_id) or {"lines": [], "words": []}
        if payload.get("words"):
            from app.cuts import plan_joins_setting

            from app.piece_speed import drop_reversed, is_reversed

            plan = store.get_clip_plan(clip_plan_id)
            joins = plan_joins_setting(plan)
            segments = plan.get("segments") or []
            if joins or any(is_reversed(s) for s in segments):
                # Превью субтитров редактора («куски подряд»): слова так, как их слышно — с J/L,
                # без развёрнутых кусков (как в рендере).
                from app.audio_edit import heard_subtitles

                heard = heard_subtitles(payload, segments, segments, joins) if joins else payload
                payload = {**payload, "heard_words": drop_reversed(heard, segments)["words"]}
        return payload
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post(
    "/api/clip-plans/{clip_plan_id}/subtitles",
    tags=["Subtitles"],
    summary="Распознать речь клипа и сохранить строки (ДО рендера)",
)
def api_generate_plan_subtitles(
    clip_plan_id: int, _auth: AuthDep, payload: PlanSubtitlesRequest | None = None
) -> dict:
    try:
        return generate_plan_subtitles(
            store, clip_plan_id, provider=payload.provider if payload else None, model=payload.model if payload else None
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put(
    "/api/clip-plans/{clip_plan_id}/subtitles",
    tags=["Subtitles"],
    summary="Сохранить правку субтитров (текст и тайминги строк)",
)
def api_put_plan_subtitles(clip_plan_id: int, payload: PlanSubtitleLines, _auth: AuthDep) -> dict:
    try:
        snapshot_clip(store, clip_plan_id, "правка субтитров")
        return save_plan_subtitle_lines(
            store,
            clip_plan_id,
            [line.model_dump() for line in payload.lines],
            [w.model_dump() for w in payload.words] if payload.words else None,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class TranslateSubtitlesRequest(BaseModel):
    mode: Literal["ru", "both", "off"] = Field(default="ru", description="ru — только русский, both — русский + "
                                                                         "оригинал вторым слоем, off — вернуть оригинал")
    retranslate: bool = Field(default=False, description="перевести заново (иначе — только смена режима)")
    force: bool = Field(default=False, description="переводить, даже если whisper считает речь русской")
    translations: list[str] | None = Field(default=None, description="готовый перевод по фразам (без Gemini)")


@app.post(
    "/api/clip-plans/{clip_plan_id}/subtitles/translate",
    tags=["Subtitles"],
    summary="Перевести субтитры иноязычного клипа на русский (Gemini; оригинал — вторым слоем по желанию)",
)
def api_translate_plan_subtitles(clip_plan_id: int, _auth: AuthDep, payload: TranslateSubtitlesRequest | None = None) -> dict:
    from app.translation import translate_subtitles

    body = payload or TranslateSubtitlesRequest()
    try:
        snapshot_clip(store, clip_plan_id, "перевод субтитров")
        result = translate_subtitles(store, clip_plan_id, mode=body.mode, translations=body.translations,
                                     force=body.force, retranslate=body.retranslate)
        return {**result, "subtitles": store.get_clip_plan_subtitles(clip_plan_id)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class DubRequest(BaseModel):
    provider: Literal["polza", "gemini", "none"] = Field(
        default="polza", description="polza — платный TTS (оценка, потом confirm); gemini — Gemini TTS бесплатно "
                                     "(синтез сразу); none — сухой прогон тоном (бесплатно)")
    voice: str | None = Field(default=None, max_length=60)
    model: str | None = Field(default=None, max_length=80)
    style: str | None = Field(default=None, max_length=300, description="gemini: как говорить («говори весело, "
                                                                         "быстро»)")
    confirm: bool = Field(default=False, description="без него — только оценка стоимости")
    budget_rub: float | None = Field(default=None, ge=0, description="потолок этого запуска, ₽ (по умолчанию 200)")
    stretch: bool = Field(default=False, description="замедлять короткие фразы до ×0.8 вместо тишины")
    remove: bool = Field(default=False, description="снять слои озвучки")


@app.post(
    "/api/clip-plans/{clip_plan_id}/dub",
    tags=["Subtitles"],
    summary="🔊 Озвучка RU переведённого клипа: Gemini — бесплатно сразу; Polza — оценка, синтез с confirm + бюджет",
)
def api_dub_clip(clip_plan_id: int, _auth: AuthDep, payload: DubRequest | None = None) -> dict:
    from app.dubbing import DubError, dub_clip, provider_paid

    body = payload or DubRequest()
    try:
        if body.confirm or body.remove or not provider_paid(body.provider):
            snapshot_clip(store, clip_plan_id, "озвучка RU")
        return dub_clip(store, clip_plan_id, provider=body.provider, voice=body.voice, model=body.model,
                        confirm=body.confirm, budget_rub=body.budget_rub, stretch=body.stretch, remove=body.remove,
                        style=body.style)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DubError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/tts/voices", tags=["Subtitles"], summary="🔊 Провайдеры TTS, модели и голоса (gemini — бесплатно)")
def api_tts_voices(_auth: AuthDep) -> dict:
    from app.dubbing import voice_catalog

    return voice_catalog()


class TtsPreviewRequest(BaseModel):
    provider: Literal["gemini", "polza", "none"] = Field(default="gemini", description="gemini — бесплатно")
    voice: str | None = Field(default=None, max_length=60)
    model: str | None = Field(default=None, max_length=80)
    style: str | None = Field(default=None, max_length=300, description="как говорить («говори весело, быстро»)")
    text: str | None = Field(default=None, max_length=300, description="фраза пробы (по умолчанию приветствие)")
    confirm: bool = Field(default=False, description="платный провайдер: без него — только оценка")


@app.post("/api/tts/preview", tags=["Subtitles"],
          summary="🔊 Проба голоса: короткая фраза → mp3 в библиотеке (url); gemini бесплатно, платное — confirm")
def api_tts_preview(_auth: AuthDep, payload: TtsPreviewRequest | None = None) -> dict:
    from app.dubbing import DubError, tts_preview

    body = payload or TtsPreviewRequest()
    try:
        return tts_preview(store, provider=body.provider, voice=body.voice, model=body.model, style=body.style,
                           text=body.text, confirm=body.confirm)
    except DubError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class CensorRequest(BaseModel):
    verify: bool = Field(default=True, description="рендер + Whisper по готовому файлу, шире там, где мат узнаётся")
    max_rounds: int | None = Field(default=None, ge=1, le=5, description="кругов рендер→Whisper (по умолчанию 3)")
    levels: int | dict[str, int] | None = Field(default=None, description="уровень 1–3 всем или {номер слова: уровень}")
    soft: bool = Field(default=False, description="ещё и мягкое: блин, хрен, жопа, говно")
    stars: bool = Field(default=True, description="мат в субтитрах — первая буква и звёздочки")
    skip: list[int] = Field(default_factory=list, description="номера слов субтитров, которые не трогать")
    clear: bool = Field(default=False, description="снять отрезки цензуры (ручные mute остаются)")
    track: int | str | None = Field(default=None, description='глушить только дорожку: номер или "voice" '
                                                              "(дорожка голоса записи, если известна)")
    style: Literal["silence", "beep", "noise", "reverse", "lowpass", "stretch", "duck"] | None = Field(
        default=None, description="чем заполнить заглушённую середину слова; не задан — прежний стиль клипа, "
                                  "у нового — reverse")


@app.post(
    "/api/clip-plans/{clip_plan_id}/censor",
    tags=["Subtitles"],
    summary="🤬 Заглушить мат серединой слова, звёздочки в субтитрах, проверка Whisper по рендеру",
)
def api_clip_plan_censor(
    clip_plan_id: int, _auth: AuthDep, response: Response, payload: CensorRequest | None = None
) -> dict:
    """Тот же инструмент, что у агентов (censor_profanity, app/censor.py). С ``verify`` —
    фоном: запрос ждёт не дольше ~45 с, иначе 202 и ``{status: running, retry_after_sec}`` —
    повтори тот же запрос. Без ``verify`` — сразу (только mute и звёздочки)."""
    from app import agent_tools

    body = payload or CensorRequest()
    try:
        store.get_clip_plan(clip_plan_id, include_segments=False)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    ctx = agent_tools.ToolContext(
        store=store, clip_plan_id=clip_plan_id,
        on_first_change=lambda: snapshot_clip(store, clip_plan_id, "цензура мата"),
    )
    args = body.model_dump(exclude_none=True)
    if any(args.get(k) not in (None, "", [], False) for k in ("levels", "level", "style", "groups", "soft", "skip", "track")):
        # Владелец в редакторе явно меняет цензуру — это не повторный вызов агента («уже применено»).
        args["redo"] = True
    result, ok = agent_tools.run_tool(ctx, "censor_profanity", args)
    if not ok:
        problems = "; ".join(str(p) for p in result.get("problems") or [])
        raise HTTPException(status_code=400, detail=str(result.get("error") or "цензура не удалась")
                            + (f": {problems}" if problems else ""))
    if result.get("status") == "running":
        response.status_code = 202
        response.headers["Retry-After"] = str(result.get("retry_after_sec") or 30)
    return result


class SegmentSpeedRequest(BaseModel):
    piece: int = Field(ge=0, description="номер куска с 0")
    speed: float = Field(default=1.0, ge=0.25, le=4.0, description="0.5 — вдвое медленнее, 2 — вдвое быстрее")
    reverse: bool = False
    audio: Literal["tempo", "pitch", "mute", "reverse"] | None = None


@app.post(
    "/api/clip-plans/{clip_plan_id}/segment-speed",
    tags=["Render"],
    summary="⏩ Скорость и реверс куска основы (slow-mo, ускорение, «перемотка»)",
)
def api_clip_plan_segment_speed(clip_plan_id: int, payload: SegmentSpeedRequest, _auth: AuthDep) -> dict:
    """Тот же инструмент, что у агентов (set_segment_speed, app/piece_speed.py): слои, камера,
    вставки и субтитры едут за новой длиной куска; шаг отмены — один."""
    from app import agent_tools

    try:
        store.get_clip_plan(clip_plan_id, include_segments=False)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    ctx = agent_tools.ToolContext(
        store=store, clip_plan_id=clip_plan_id,
        on_first_change=lambda: snapshot_clip(store, clip_plan_id, "скорость куска"),
    )
    result, ok = agent_tools.run_tool(ctx, "set_segment_speed", payload.model_dump(exclude_none=True))
    if not ok:
        raise HTTPException(status_code=400, detail=str(result.get("error") or "скорость не принята"))
    return result


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


def _run_clip_tool(clip_plan_id: int, name: str, args: dict, label: str, fail: str) -> dict:
    """Инструмент агента из редактора: тот же код, один шаг отмены (снимок перед правкой)."""
    from app import agent_tools

    try:
        store.get_clip_plan(clip_plan_id, include_segments=False)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    ctx = agent_tools.ToolContext(
        store=store, clip_plan_id=clip_plan_id, on_first_change=lambda: snapshot_clip(store, clip_plan_id, label),
    )
    result, ok = agent_tools.run_tool(ctx, name, args)
    if not ok:
        problems = result.get("problems") or []
        raise HTTPException(status_code=400, detail=str(result.get("error") or fail)
                            + (f": {'; '.join(map(str, problems))}" if problems else ""))
    return result


@app.post(
    "/api/clip-plans/{clip_plan_id}/piece-color",
    tags=["Render"],
    summary="🎨 Цветокоррекция куска: экспозиция, контраст, насыщенность, баланс белого",
)
def api_clip_plan_piece_color(clip_plan_id: int, payload: PieceColorRequest, _auth: AuthDep) -> dict:
    """Тот же инструмент, что у агентов (set_piece_color, app/piece_color.py)."""
    return _run_clip_tool(clip_plan_id, "set_piece_color", payload.model_dump(exclude_none=True), "цвет куска",
                          "цвет куска не принят")


@app.post(
    "/api/clip-plans/{clip_plan_id}/piece-color/match",
    tags=["Render"],
    summary="🎨 Подогнать цвет кусков к образцу (замер signalstats)",
)
def api_clip_plan_piece_color_match(clip_plan_id: int, payload: PieceColorMatchRequest, _auth: AuthDep) -> dict:
    """Тот же инструмент, что у агентов (match_piece_colors, app/piece_color.py)."""
    return _run_clip_tool(clip_plan_id, "match_piece_colors", payload.model_dump(exclude_none=True),
                          "подгонка цвета", "подгонка цвета не удалась")


class SnapBeatsRequest(BaseModel):
    targets: list[Literal["layers", "keys", "camera", "joins", "all"]] = Field(default_factory=lambda: ["layers"])
    tolerance: float = Field(default=0.15, ge=0.01, le=0.5)
    grid: Literal["beat", "downbeat", "half"] = "beat"
    source: Literal["auto", "music", "audio"] = "auto"
    bpm: float | None = Field(default=None, ge=40, le=240)
    first_beat: float | None = None
    first_downbeat: float | None = None
    dry_run: bool = False


@app.post(
    "/api/clip-plans/{clip_plan_id}/snap-beats",
    tags=["Render"],
    summary="♪ К долям: начала слоёв, ключи, камера и склейки — на ближайшую долю музыки",
)
def api_clip_plan_snap_beats(clip_plan_id: int, payload: SnapBeatsRequest, _auth: AuthDep) -> dict:
    """Тот же инструмент, что у агентов (snap_to_beats, app/beat_snap.py)."""
    return _run_clip_tool(clip_plan_id, "snap_to_beats", payload.model_dump(exclude_none=True), "к долям",
                          "к долям не вышло")


class WordEmphasisRequest(BaseModel):
    indices: list[int] | None = Field(default=None, description="номера слов в субтитрах клипа (порядок words)")
    words: list[str] | None = Field(default=None, description="тексты слов (первое вхождение)")
    style: Literal["pop", "shake", "off"] = "pop"
    auto: bool = Field(default=False, description="одно ключевое слово на строку само")
    pick: Literal["heuristic", "gemini"] | None = None
    clear: bool = False


@app.post(
    "/api/clip-plans/{clip_plan_id}/subtitles/emphasis",
    tags=["Subtitles"],
    summary="Эмфаза слов субтитров: крупнее + pop (shake — со встряской), off — снять",
)
def api_clip_plan_word_emphasis(clip_plan_id: int, payload: WordEmphasisRequest, _auth: AuthDep) -> dict:
    """Тот же инструмент, что у агентов (emphasize_words, app/word_emphasis.py); шаг отмены — один."""
    from app import agent_tools

    try:
        store.get_clip_plan(clip_plan_id, include_segments=False)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    ctx = agent_tools.ToolContext(
        store=store, clip_plan_id=clip_plan_id,
        on_first_change=lambda: snapshot_clip(store, clip_plan_id, "эмфаза слов"),
    )
    result, ok = agent_tools.run_tool(ctx, "emphasize_words", payload.model_dump(exclude_none=True))
    if not ok:
        raise HTTPException(status_code=400, detail=str(result.get("error") or "эмфаза не принята"))
    return {**result, "subtitles": store.get_clip_plan_subtitles(clip_plan_id)}


class SeparateVoicesRequest(BaseModel):
    retranscribe: bool = Field(default=True, description="сначала распознать речь точной моделью (large-v3-turbo)")


@app.post(
    "/api/clip-plans/{clip_plan_id}/voices",
    tags=["Subtitles"],
    summary="Голоса: Gemini расшифровывает каждый голос отдельно, слова субтитров получают метку говорящего",
)
def api_separate_voices(clip_plan_id: int, _auth: AuthDep, payload: SeparateVoicesRequest | None = None) -> dict:
    from app.ai.speakers import separate_voices

    try:
        snapshot_clip(store, clip_plan_id, "голоса")
        result = separate_voices(
            store, clip_plan_id, force=True, retranscribe=payload.retranscribe if payload else True
        )
        return {**result, "subtitles": store.get_clip_plan_subtitles(clip_plan_id)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class AssignVoiceRequest(BaseModel):
    start: float
    end: float
    speaker: str = Field(pattern="^[A-D]$")
    description: str | None = Field(default=None, max_length=60)


@app.post(
    "/api/clip-plans/{clip_plan_id}/voices/assign",
    tags=["Subtitles"],
    summary="Голоса: отдать слова отрезка [start, end) другому голосу (поправить модель руками)",
)
def api_assign_voice(clip_plan_id: int, payload: AssignVoiceRequest, _auth: AuthDep) -> dict:
    from app.ai.speakers import assign_voice

    try:
        snapshot_clip(store, clip_plan_id, "голос строки")
        return assign_voice(store, clip_plan_id, payload.start, payload.end, payload.speaker, payload.description)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class IdentifyVoicesRequest(BaseModel):
    labels: bool = True
    reassign: bool = False


@app.post(
    "/api/clip-plans/{clip_plan_id}/voices/identify",
    tags=["Subtitles"],
    summary="Голоса: кто есть кто по банку голосов канала (имена только при уверенном совпадении)",
)
def api_identify_voices(clip_plan_id: int, _auth: AuthDep, payload: IdentifyVoicesRequest | None = None) -> dict:
    from app.voice_bank import identify_voices

    body = payload or IdentifyVoicesRequest()
    try:
        snapshot_clip(store, clip_plan_id, "имена голосов")
        return identify_voices(store, clip_plan_id, labels=body.labels, reassign=body.reassign)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class RegisterVoiceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    speaker: str | None = Field(default=None, pattern="^[A-D]$")
    start: float | None = None
    end: float | None = None
    kind: str = Field(default="person", pattern="^(person|bot)$")


@app.post(
    "/api/clip-plans/{clip_plan_id}/voices/register",
    tags=["Subtitles"],
    summary="Голоса: запомнить в банке канала голос этого клипа (буква или отрезок)",
)
def api_register_voice(clip_plan_id: int, payload: RegisterVoiceRequest, _auth: AuthDep) -> dict:
    from app.voice_bank import register_voice

    try:
        return register_voice(store, payload.name, clip_plan_id=clip_plan_id, speaker=payload.speaker,
                              start=payload.start, end=payload.end, kind=payload.kind)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/voice-bank", tags=["Subtitles"], summary="Банк голосов: канал (project_id) и общие")
def api_voice_bank(_auth: AuthDep, project_id: int | None = None) -> dict:
    from app.voice_bank import list_voices

    return {"voices": list_voices(store, project_id)}


@app.delete("/api/voice-bank/{voice_id}", tags=["Subtitles"], summary="Банк голосов: убрать голос")
def api_voice_bank_delete(voice_id: int, _auth: AuthDep) -> dict:
    from app.voice_bank import delete_voice

    try:
        delete_voice(store, voice_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}


class AiMontageApplyRequest(BaseModel):
    spec: dict


@app.post(
    "/api/clip-plans/{clip_plan_id}/ai-montage/apply",
    tags=["Clip file"],
    summary="Apply an AI re-edit (the pre-AI clip file is kept for undo)",
)
def api_clip_plan_ai_montage_apply(clip_plan_id: int, payload: AiMontageApplyRequest, _auth: AuthDep) -> dict:
    try:
        snapshot_clip(store, clip_plan_id, "ИИ-монтаж")
        spec, changes = apply_montage(store, clip_plan_id, payload.spec)
    except SpecError as exc:
        raise HTTPException(status_code=422, detail={"problems": exc.problems}) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"spec": spec, "changes": changes}


@app.post(
    "/api/clip-plans/{clip_plan_id}/ai-montage/undo",
    tags=["Clip file"],
    summary="↩ Undo the AI re-edit: back to the clip as it was before",
)
def api_clip_plan_ai_montage_undo(clip_plan_id: int, _auth: AuthDep) -> dict:
    try:
        spec, changes = undo_montage(store, clip_plan_id)
    except SpecError as exc:
        raise HTTPException(status_code=422, detail={"problems": exc.problems}) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"spec": spec, "changes": changes}


def _render_plan_own_settings(clip_plan_id: int) -> dict:
    """Render a clip with its own saved settings (what the clip file says)."""
    plan = store.get_clip_plan(clip_plan_id, include_segments=False)
    kwargs = (
        settings_to_render_kwargs(plan["render_settings"], _default_subtitle_profile_id(store))
        if plan.get("render_settings")
        else {}
    )
    return clip_render_service.render_clip_plan(clip_plan_id, **kwargs)


class AiMontageBatchRequest(BaseModel):
    clip_plan_ids: list[int] = Field(default_factory=list, description="пусто = все ★ избранные (не скрытые)")
    goal: str = Field(default="", max_length=500)
    render: bool = False
    # Второй проход: моушн-монтаж Gemini-агентом (хук, субтитры, панчлайны, звук, мемы).
    polish: bool = True


@app.post(
    "/api/sources/{source_id}/ai-montage-batch",
    tags=["Clip file"],
    summary="🤖 ИИ-монтаж for many clips in the background (applies, optionally renders)",
)
def api_source_ai_montage_batch(source_id: int, payload: AiMontageBatchRequest, request: Request, _auth: AuthDep) -> dict:
    from app.stream_questions_api import refuse_agent_montage

    refuse_agent_montage(request, store, source_id)
    try:
        plans = store.list_clip_plans(source_id=source_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    own = {p["id"] for p in plans}
    ids = [i for i in payload.clip_plan_ids if i in own] or [
        p["id"] for p in plans if p.get("favorite") and not p.get("hidden")
    ]
    if not ids:
        raise HTTPException(status_code=400, detail="нет клипов: отметьте ★ избранные или передайте clip_plan_ids")
    refuse_agent_drafts(request, store, ids)
    running = latest_job_for_source(source_id)
    if running and running.get("status") == "running":
        raise HTTPException(status_code=409, detail="ИИ-монтаж этого проекта уже идёт")
    return start_montage_job(
        store, source_id, ids, goal=payload.goal, render=_render_plan_own_settings if payload.render else None,
        polish=payload.polish and bool(settings.gemini_api_key),
    )


class AiPickRequest(BaseModel):
    count: int = Field(default=10, ge=1, le=50)
    goal: str = Field(default="", max_length=500)
    # Формат будущих клипов (app/formats.py): нет — настройка analysis_format, иначе 9:16.
    format: Literal["9:16", "1:1", "4:5", "16:9"] | None = None
    # Chain: start 🤖 ИИ-монтаж on the picks right away (optionally rendering).
    montage: bool = False
    render: bool = False
    polish: bool = True  # моушн-монтаж Gemini-агентом после склейки


@app.post(
    "/api/sources/{source_id}/ai-pick",
    tags=["Clip file"],
    summary="✨ ИИ выбирает лучшие: star the N most shareable moments (score + reason)",
)
def api_source_ai_pick(source_id: int, payload: AiPickRequest, request: Request, _auth: AuthDep) -> dict:
    from app.stream_questions_api import refuse_agent_montage

    refuse_agent_montage(request, store, source_id)
    try:
        store.get_source(source_id)
        result = pick_best(store, source_id, count=payload.count, goal=payload.goal,
                           **({"output_format": payload.format} if payload.format else {}))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    # Выбор ИИ по кнопке владельца — его выбор. Тот же запрос агента только ставит ★: монтаж ждёт владельца.
    from app import agent_settings as agent_settings_mod

    picked_ids = [p["id"] for p in result["picks"]]
    refused = agent_settings_mod.drafts_refusal(store, picked_ids) if is_agent_request(request) else None
    if not is_agent_request(request):
        agent_settings_mod.set_picked_many(store, picked_ids)
    if refused and payload.montage:
        result["montage_refused"] = refused["error"]
    elif payload.montage:
        running = latest_job_for_source(source_id)
        if not (running and running.get("status") == "running"):
            result["montage_job"] = start_montage_job(
                store,
                source_id,
                [p["id"] for p in result["picks"]],
                goal=payload.goal,
                render=_render_plan_own_settings if payload.render else None,
                polish=payload.polish and bool(settings.gemini_api_key),
            )
    return result


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


@app.get(
    "/api/sources/{source_id}/ai-montage-batch",
    tags=["Clip file"],
    summary="Progress of the latest 🤖 ИИ-монтаж batch of this project (null if none)",
)
def api_source_ai_montage_batch_status(source_id: int, _auth: AuthDep) -> dict | None:
    return latest_job_for_source(source_id)


class CoverComposeRequest(BaseModel):
    t: float = Field(ge=0, description="секунда готового клипа — кадр обложки")
    title: str = Field(min_length=1, max_length=80)
    emoji: str = Field(default="", max_length=16)
    style: str = Field(default="yellow", max_length=20, description="yellow | fire | white | plate")
    y: float = Field(default=0.2, ge=0.05, le=0.9)


@app.post(
    "/api/clip-plans/{clip_plan_id}/cover/compose",
    tags=["Render"],
    summary="🖼 Обложка с заголовком: кадр клипа + крупный титр, включается сразу",
)
def api_cover_compose(clip_plan_id: int, payload: CoverComposeRequest, _auth: AuthDep) -> dict:
    from app.cover_compose import compose_cover
    from app.motion_render import CompositorMissing

    try:
        return compose_cover(store, clip_plan_id, payload.t, payload.title, emoji=payload.emoji, style=payload.style, y=payload.y)
    except CompositorMissing as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class CompilationRequest(BaseModel):
    plan_ids: list[int] = Field(min_length=2, max_length=12)
    title: str = Field(default="", max_length=100)
    countdown: bool = Field(default=True, description="#N … #1 — лучший последним")
    caption_style: str = Field(default="hormozi", max_length=40)


@app.post(
    "/api/sources/{source_id}/compilation",
    tags=["Render"],
    summary="🧩 Компиляция «ТОП-N»: моменты подряд с номерами, субтитрами и whoosh",
)
def api_source_compilation(source_id: int, payload: CompilationRequest, request: Request, _auth: AuthDep) -> dict:
    from app.compilation import build_compilation
    from app.stream_questions_api import refuse_agent_montage

    refuse_agent_montage(request, store, source_id)
    refuse_agent_drafts(request, store, payload.plan_ids)
    try:
        return build_compilation(
            store, source_id, payload.plan_ids, title=payload.title, countdown=payload.countdown,
            caption_style=payload.caption_style,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class AutoEditRequest(BaseModel):
    style: str = Field(default="hormozi", max_length=40, description="стиль анимированных субтитров")
    keep_layers: bool = Field(default=False, description="оставить свои слои, заменить только авто-слои")


@app.post(
    "/api/clip-plans/{clip_plan_id}/auto-edit",
    tags=["Render"],
    summary="⚡ Автомонтаж без ИИ: хук, субтитры по словам, наезды и звуки по речи",
)
def api_clip_plan_auto_edit(clip_plan_id: int, request: Request, _auth: AuthDep, payload: AutoEditRequest | None = None) -> dict:
    from app.auto_edit import apply_auto_edit
    from app.stream_questions_api import refuse_agent_clip_montage

    refuse_agent_clip_montage(request, store, clip_plan_id)
    body = payload or AutoEditRequest()
    try:
        return apply_auto_edit(store, clip_plan_id, style=body.style, keep_layers=body.keep_layers)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
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
