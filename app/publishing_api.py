"""Публикация: аккаунты площадок, посты из клипа и из файла, очередь заданий и её просмотр.

Роутер собирается функцией, как ``app/cuts_api.py``: ``main.py`` передаёт зависимость авторизации и доступ к хранилищу
и сервисам лямбдами (тесты подменяют ``app.main.store`` и другие имена)."""

# Без from __future__ import annotations: FastAPI читает аннотации вида
# _auth: auth_dep в момент объявления, а auth_dep — локальная переменная.
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Callable, Literal

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from app.ai.publish_meta import apply_ai_metadata
from app.agent_settings_api import owner_only
from app.api_common import _normalize_schedule_text
from app.store_jobs import RepostNeedsConfirm


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
        description="Raw Cookie header, Netscape export or JSON export (Cookie-Editor). Never returned by API responses.",
        examples=["SID=...; HSID=...; SSID=...; APISID=...; SAPISID=..."],
    )
    clear_proxy: bool = Field(default=False, description="Remove the account proxy (an empty proxy_url keeps it).")


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
    maybe_posted: bool = False  # запрос поста ушёл, а ответа нет: проверить на площадке
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
    made_by: str = ""  # 'agent' — поставила настройка проекта «Публиковать самому»


class ClipPostRequest(BaseModel):
    title: str = ""
    description: str = ""
    targets: list[int]
    privacy: Literal["public", "unlisted", "private"] = "public"
    allow_comments: bool = True
    scheduled_at: str = ""
    # Владелец подтвердил: выложить ещё раз то, что на этом аккаунте уже вышло или могло выйти.
    allow_repost: bool = False


class ClipPostBatchRequest(BaseModel):
    clip_ids: list[int]
    targets: list[int]
    privacy: Literal["public", "unlisted", "private"] = "public"
    allow_comments: bool = True
    # First post goes at start_at (empty = now); each next one interval_minutes later.
    start_at: str = ""
    interval_minutes: int = 0
    # Write each clip's title/description/hashtags with AI before queueing.
    ai_metadata: bool = False


# Время чуть в прошлом (пока жали кнопку) — «сейчас»; дальше — ошибка, а не публикация сразу.
SCHEDULE_PAST_GRACE = timedelta(minutes=2)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _schedule_text(value: str | None) -> str:
    """Время публикации (UTC) из запроса. Прошедшее — ValueError: ролик не должен выйти сразу из-за опечатки в дате."""
    text = _normalize_schedule_text(value)
    if text and datetime.strptime(text, "%Y-%m-%d %H:%M:%S") < _utc_now() - SCHEDULE_PAST_GRACE:
        raise ValueError(f"время публикации уже прошло: {text} UTC")
    return text


def build_publishing_router(auth_dep: Any, get_store: Callable[[], Any]) -> APIRouter:
    router = APIRouter()

    @router.post("/ui/posts", include_in_schema=False)
    async def ui_create_post(
        _auth: auth_dep,
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

    @router.post(
        "/api/accounts",
        response_model=AccountRead,
        tags=["Accounts"],
        summary="Add or update an account",
        description="Stores encrypted cookies for a YouTube or TikTok account. The cookie value is never returned.",
    )
    def api_add_account(payload: AccountCreate, request: Request, _auth: auth_dep) -> dict:
        owner_only(request, "Аккаунты и их cookie меняет только владелец")
        try:
            return get_store().upsert_account(payload.platform, payload.label, payload.cookie, payload.proxy_url,
                                              clear_proxy=payload.clear_proxy)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get(
        "/api/accounts",
        response_model=list[AccountRead],
        tags=["Accounts"],
        summary="List accounts",
        description="Returns account metadata only; encrypted cookies are not included.",
    )
    def api_accounts(_auth: auth_dep) -> list[dict]:
        return get_store().list_accounts()

    @router.delete(
        "/api/accounts/{account_id}",
        tags=["Accounts"],
        summary="Delete an account",
    )
    def api_delete_account(account_id: int, request: Request, _auth: auth_dep) -> dict:
        owner_only(request, "Аккаунты удаляет только владелец")
        try:
            deleted = get_store().delete_account(account_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"deleted": True, "account": deleted}

    @router.get(
        "/api/clips/{clip_id}/publish-check",
        tags=["Posts"],
        summary="How a clip goes out on each account by its format (16:9 on YouTube — a regular video, not Shorts)",
    )
    def api_clip_publish_check(clip_id: int, _auth: auth_dep, targets: str = "") -> dict:
        """``targets`` — id аккаунтов через запятую. Ответ: формат клипа, по аккаунту — ``publish_as``
        (shorts | video | reel | feed | tiktok) и ``warning`` (TikTok/Instagram ждут вертикаль)."""
        from app.publish_format import check_targets

        try:
            ids = [int(x) for x in targets.replace(" ", "").split(",") if x]
            return check_targets(get_store(), clip_id, ids)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post(
        "/api/clips/{clip_id}/posts",
        response_model=JobRead,
        tags=["Posts"],
        summary="Queue publication for a rendered clip",
    )
    def api_create_post_from_clip(clip_id: int, payload: ClipPostRequest, _auth: auth_dep) -> dict:
        try:
            return get_store().create_clip_post_job(
                clip_id,
                payload.title,
                payload.description,
                payload.targets,
                payload.privacy,
                payload.allow_comments,
                scheduled_at=_schedule_text(payload.scheduled_at),
                allow_repost=payload.allow_repost,
            )
        except RepostNeedsConfirm as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post(
        "/api/clips/posts-batch",
        tags=["Posts"],
        summary="Queue publication of several rendered clips, each with its own title",
    )
    def api_create_posts_batch(payload: ClipPostBatchRequest, _auth: auth_dep) -> dict:
        """The last step of «анализ → избранное → настройка → рендер → публикация».

        Every clip goes out under ITS OWN title and description (set on the clip), to
        the same accounts. With an interval the posts are spread out — a burst of ten
        uploads in one minute is exactly what platforms rate-limit and viewers skip.
        A clip that can't be posted (not rendered, gone) is reported, not fatal.
        """
        if not payload.clip_ids:
            raise HTTPException(status_code=400, detail="clip_ids cannot be empty")
        if not payload.targets:
            raise HTTPException(status_code=400, detail="choose at least one account")
        if payload.interval_minutes < 0 or payload.interval_minutes > 7 * 24 * 60:
            raise HTTPException(status_code=400, detail="interval_minutes must be 0..10080")
        try:
            start_text = _schedule_text(payload.start_at)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        start = datetime.strptime(start_text, "%Y-%m-%d %H:%M:%S") if start_text else None
        from app.publish_format import check_targets

        jobs: list[dict] = []
        skipped: list[dict] = []
        format_warnings: list[dict] = []  # 16:9/1:1/4:5 на площадке, что ждёт вертикаль
        meta_errors = apply_ai_metadata(get_store(), payload.clip_ids) if payload.ai_metadata else {}
        for index, clip_id in enumerate(dict.fromkeys(payload.clip_ids)):
            scheduled = ""
            if start is not None or payload.interval_minutes:
                base = start or _utc_now()  # очередь живёт в UTC, как и время из запроса
                scheduled = (base + timedelta(minutes=index * payload.interval_minutes)).strftime("%Y-%m-%d %H:%M:%S")
                if start is None and index == 0:
                    scheduled = ""  # the first one goes right away
            try:
                clip = get_store().get_clip(clip_id)
                jobs.append(
                    get_store().create_clip_post_job(
                        clip_id,
                        clip.get("title") or f"Клип #{clip_id}",
                        clip.get("description") or "",
                        payload.targets,
                        payload.privacy,
                        payload.allow_comments,
                        scheduled_at=scheduled,
                    )
                )
                check = check_targets(get_store(), clip, payload.targets)
                if check["warnings"]:
                    format_warnings.append({"clip_id": clip_id, "format": check["format"], "warnings": check["warnings"]})
            except (KeyError, ValueError) as exc:
                skipped.append({"clip_id": clip_id, "reason": str(exc)})
        return {"jobs": jobs, "skipped": skipped, "metadata_errors": meta_errors, "format_warnings": format_warnings}

    @router.post(
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
        _auth: auth_dep,
        file: Annotated[UploadFile, File(description="Source video file, usually mp4/mov/webm.")],
        title: Annotated[str, Form(description="Post title.")] ,
        description: Annotated[str, Form(description="Post description/caption body.")] = "",
        targets: Annotated[list[int], Form(description="Saved account IDs. Repeat this form field for each target.")] = [],
        privacy: Annotated[
            Literal["public", "unlisted", "private"],
            Form(description="Publication visibility. TikTok supports public/private only."),
        ] = "public",
        allow_comments: Annotated[bool, Form(description="Whether comments should be enabled where supported.")] = True,
        scheduled_at: Annotated[str, Form(description="Optional UTC datetime for delayed publication (or with offset).")] = "",
        allow_repost: Annotated[bool, Form(description="Post again what this account already has.")] = False,
    ) -> dict:
        return await _create_post_from_upload(file, title, description, targets, privacy, allow_comments, scheduled_at,
                                              allow_repost)

    @router.get(
        "/api/jobs",
        response_model=list[JobRead],
        tags=["Jobs"],
        summary="List recent jobs",
    )
    def api_jobs(_auth: auth_dep) -> list[dict]:
        return get_store().list_jobs()

    @router.get(
        "/api/jobs/{job_id}",
        response_model=JobRead,
        tags=["Jobs"],
        summary="Get job details",
    )
    def api_job(job_id: int, _auth: auth_dep) -> dict:
        try:
            return get_store().get_job(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post(
        "/api/jobs/{job_id}/cancel",
        response_model=JobRead,
        tags=["Jobs"],
        summary="Cancel a queued job before the worker takes it",
    )
    def api_cancel_job(job_id: int, request: Request, _auth: auth_dep) -> dict:
        owner_only(request, "Публикацию отменяет владелец")
        try:
            job = get_store().get_job(job_id)
            if not get_store().cancel_job(job_id):
                raise HTTPException(status_code=409, detail=f"Публикация уже {job['status']}: отменять поздно")
            return get_store().get_job(job_id)
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
        allow_repost: bool = False,
    ) -> dict:
        try:
            job = get_store().create_job(
                file.file,
                file.filename or "video.mp4",
                title,
                description,
                targets,
                privacy,
                allow_comments,
                scheduled_at=_schedule_text(scheduled_at),
                allow_repost=allow_repost,
            )
        except RepostNeedsConfirm as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            await file.close()
        return job

    return router
