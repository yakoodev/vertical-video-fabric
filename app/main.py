from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

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
from app.ai.asset_describe import describe_asset
from app.compression import REVALIDATE, ImmutableAssets, TextGzipMiddleware
from app.store import AppStore
from app.worker import JobWorker
from app.platform_stats import StatsCollector
from app.api_common import _safe_next
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
from app.session_keepalive import SessionKeeper  # noqa: E402 - браузер аккаунтов: держит вход живым

session_keeper = SessionKeeper(store)
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
from app.s3d_render import Series3dWorker  # noqa: E402 - 3D-серия: голос → 3D → проверки → клип, по одному

series3d_worker = Series3dWorker(store)
AuthDep = Annotated[None, Depends(require_auth)]

# Модули сервиса (app/modules.py): что выключено в «Настройки → Модули». Хранилище и работники —
# через лямбды: тесты подменяют app.main.store / worker / … на свои.
modules.bind_store(lambda: store)
modules.register_worker("publishing", "job-worker", lambda: worker)
modules.register_worker("publishing", "session-keeper", lambda: session_keeper)
modules.register_worker("platform_stats", "stats-collector", lambda: stats_collector)
modules.register_worker("autopilot", "autopilot", lambda: autopilot_worker)
modules.register_worker("vod_watch", "vod-watch", lambda: vod_watch_worker)
modules.register_worker("ai_video", "aivideo-gen", lambda: aivideo_worker)
modules.register_worker("explainer", "explainer", lambda: explainer_worker)
modules.register_worker("stories", "stories", lambda: stories_worker)
modules.register_worker("series3d", "series3d", lambda: series3d_worker)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # До запуска работников: сейчас в этом процессе ничего не выполняется, всё «идёт» в базе — от прошлого запуска.
    store.recover_after_restart()
    try:
        store.dedup_unused_ffmpeg_presets()
    except Exception:  # noqa: BLE001 - cleanup must never block startup
        pass
    # Один раз: ложные состояния входа YouTube от браузера (теперь YouTube проверяет youtubei.js) — сбросить.
    try:
        from app.session_keepalive import reset_youtube_browser_states

        reset_youtube_browser_states(store)
    except Exception:  # noqa: BLE001 - cleanup must never block startup
        logging.getLogger(__name__).warning("youtube keepalive state reset failed", exc_info=True)
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
# Ещё снаружи: CORS своего расширения браузера (только /api/accounts) — и для 401/423 тоже.
from app.extension_cors import ExtensionCors  # noqa: E402

app.add_middleware(ExtensionCors, get_origins=lambda: settings.browser_extension_origins)
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


# Ручки по доменам. Порядок подключения важен, только если адреса пересекаются: кто раньше, тот и отвечает.
from app.settings_api import build_settings_router  # noqa: E402

app.include_router(build_settings_router(AuthDep, lambda: store))

# Траты Gemini: итоги по назначению, модели, дням, сессиям; цены и курс (app/usage_api.py).
from app.usage_api import build_usage_router  # noqa: E402

app.include_router(build_usage_router(AuthDep, lambda: store))

# Раньше publishing_api: /api/clips/{clip_id} (GET, PATCH, DELETE) шёл перед POST /api/clips/posts-batch.
from app.clips_api import build_clips_router  # noqa: E402

app.include_router(build_clips_router(AuthDep, lambda: store, lambda: clip_render_service, lambda: probe_media))

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

from app.montage_assets_api import build_montage_assets_router  # noqa: E402

app.include_router(build_montage_assets_router(AuthDep, lambda: store, lambda: describe_asset))

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

# 3D-серия в сервисе: библиотека проекта, серии с версиями плана, рендер заданием и проверки (app/s3d_api.py).
from app.s3d_api import build_series3d_router  # noqa: E402

app.include_router(build_series3d_router(AuthDep, lambda: store))

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
# 🌐 Страницы сайтов: поиск, снимок, правка через HTML, версии (app/webpages.py).
from app.webpages_api import build_webpages_router  # noqa: E402

app.include_router(build_webpages_router(AuthDep, lambda: store))

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
