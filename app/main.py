from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import JSONResponse
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app.auth import AuthRequired, auth_required_handler, require_auth, token_is_valid
from app.crypto import CookieCipher
from app.db import Database
from app.settings import settings
from app.store import AppStore
from app.worker import JobWorker

settings.ensure_dirs()
settings.ensure_api_token()
db = Database(settings.db_path)
db.init()
store = AppStore(db, CookieCipher(settings.secret_key_path))
worker = JobWorker(store)
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


@app.get("/", include_in_schema=False)
def root(_auth: AuthDep) -> RedirectResponse:
    return RedirectResponse(url="/posts/new", status_code=303)


@app.get("/login", response_class=HTMLResponse, include_in_schema=False)
def login_page(request: Request, next: str = "/posts/new") -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "login.html",
        {"active": "", "next": _safe_next(next), "error": ""},
    )


@app.post("/login", response_class=HTMLResponse, include_in_schema=False)
def login_submit(
    request: Request,
    token: Annotated[str, Form()],
    next: Annotated[str, Form()] = "/posts/new",
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


def _safe_next(value: str) -> str:
    if not value.startswith("/") or value.startswith("//"):
        return "/posts/new"
    return value
