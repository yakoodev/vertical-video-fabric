"""REST «Ленты» (app/feed.py): работы, версии, варианты файлов, статус и вердикт, отметки «запощено», файлы.

Роутер собирается функцией, как ``app/clips_api.py``. Загрузка файла — сырым телом ``PUT`` (не multipart): тело
пишется на диск кусками по мере прихода, без временного файла парсера форм — фильм на 700 МБ так не занимает память
и не пишется дважды. Кто сделал (``owner`` / ``agent``) — по запросу: токен в заголовке без куки входа — агент."""

# Без from __future__ import annotations: FastAPI читает аннотации вида _auth: auth_dep в момент объявления.
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app import feed as FEED
from app import feed_files as F
from app.auth import is_agent_request
from app.settings import settings


class VariantRef(BaseModel):
    label: str = Field(default="", description="подпись: «9:16», «16:9», «TikTok часть 1/4», «без музыки»")
    clip_id: int | None = Field(default=None, description="готовый клип сервиса (например, из /api/clips/upload)")
    path: str = Field(default="", description="файл на сервере внутри папки данных (DATA_DIR) — по ссылке, на месте")
    host_path: str = Field(default="", description="файл хоста в папке импорта (D:\\vvf-data\\runtime\\feed_import) — "
                                                   "переносится в ленту")


class WorkCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    project_id: int | None = None
    kind: str = Field(default="other", description="movie | amv | meme | promo | series | other")


class WorkUpdate(BaseModel):
    title: str | None = None
    project_id: int | None = None
    kind: str | None = None
    cover_variant_id: int | None = None


class VersionCreate(BaseModel):
    note: str = Field(default="", description="что поменял")
    label: str = Field(default="", description="своя подпись версии («v4.1»); пусто — vN")
    status: str = Field(default="review", description="draft | review | accepted | rejected")
    variants: list[VariantRef] = Field(default_factory=list)


class StatusUpdate(BaseModel):
    status: str | None = Field(default=None, description="draft | review | accepted | rejected")
    verdict: str | None = Field(default=None, description="вердикт владельца его словами")


class PostingCreate(BaseModel):
    platform: str = Field(description="tiktok | youtube | instagram | vk | other")
    url: str = ""
    posted_at: str | None = Field(default=None, description="ГГГГ-ММ-ДД; нет — сегодня")
    account: str = ""
    posted_by: str | None = Field(default=None, description="owner | agent; нет — кто прислал запрос")


def _who(request: Request) -> str:
    return "agent" if is_agent_request(request) else "owner"


def _dump(model: BaseModel, **kwargs: Any) -> dict:
    return model.model_dump(**kwargs) if hasattr(model, "model_dump") else model.dict(**kwargs)


def _call(fn: Callable, *args: Any, **kwargs: Any) -> Any:
    try:
        return fn(*args, **kwargs)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'\"")) from exc
    except ValueError as exc:  # FeedFileError — тоже ValueError
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _refs(variants: list[VariantRef]) -> list[dict]:
    return [{k: v for k, v in _dump(ref).items() if v not in (None, "")} for ref in variants]


def build_feed_router(auth_dep: Any, get_store: Callable[[], Any]) -> APIRouter:
    router = APIRouter()

    @router.get("/api/feed", tags=["Feed"], summary="Лента готовых работ: карточки, свежие сверху, фильтры")
    def api_feed(_auth: auth_dep, filter: str = "", project_id: int | None = None, kind: str = "", q: str = "",
                 limit: int = 50, offset: int = 0) -> dict:
        return _call(FEED.list_feed, get_store(), filter=filter, project_id=project_id, kind=kind, q=q, limit=limit,
                     offset=offset)

    @router.post("/api/feed/works", tags=["Feed"], summary="Новая работа в ленте")
    def api_create_work(payload: WorkCreate, request: Request, _auth: auth_dep) -> dict:
        return _call(FEED.create_work, get_store(), payload.title, payload.project_id, payload.kind, _who(request))

    @router.get("/api/feed/works/{work_id}", tags=["Feed"], summary="Работа: версии (новые сверху), варианты, отметки")
    def api_work(work_id: int, _auth: auth_dep) -> dict:
        return _call(FEED.get_work, get_store(), work_id)

    @router.patch("/api/feed/works/{work_id}", tags=["Feed"], summary="Название, проект, вид, обложка работы")
    def api_update_work(work_id: int, payload: WorkUpdate, _auth: auth_dep) -> dict:
        return _call(FEED.update_work, get_store(), work_id, **_dump(payload, exclude_unset=True))

    @router.delete("/api/feed/works/{work_id}", tags=["Feed"], summary="Убрать работу из ленты (свои файлы ленты тоже)")
    def api_delete_work(work_id: int, _auth: auth_dep) -> dict:
        return _call(FEED.delete_work, get_store(), work_id)

    @router.post("/api/feed/works/{work_id}/versions", tags=["Feed"],
                 summary="Новая версия: заметка и варианты-ссылки (клип, файл сервера, импорт с хоста)")
    def api_add_version(work_id: int, payload: VersionCreate, request: Request, _auth: auth_dep) -> dict:
        return _call(FEED.add_version, get_store(), work_id, note=payload.note, variants=_refs(payload.variants),
                     label=payload.label, status=payload.status, made_by=_who(request))

    @router.delete("/api/feed/versions/{version_id}", tags=["Feed"], summary="Удалить версию")
    def api_delete_version(version_id: int, _auth: auth_dep) -> dict:
        return _call(FEED.delete_version, get_store(), version_id)

    @router.post("/api/feed/versions/{version_id}/status", tags=["Feed"], summary="Статус версии и вердикт владельца")
    def api_set_status(version_id: int, payload: StatusUpdate, request: Request, _auth: auth_dep) -> dict:
        return _call(FEED.set_status, get_store(), version_id, payload.status, payload.verdict, _who(request))

    @router.post("/api/feed/versions/{version_id}/variants", tags=["Feed"],
                 summary="Вариант-ссылка к версии: clip_id, path (файл сервера) или host_path (импорт)")
    def api_add_variant(version_id: int, payload: VariantRef, _auth: auth_dep) -> dict:
        refs = _refs([payload])
        return _call(FEED.add_variant, get_store(), version_id, refs[0] if refs else {})

    @router.put("/api/feed/versions/{version_id}/variants/upload", tags=["Feed"],
                summary="Загрузить файл варианта сырым телом запроса (потоком; ?label=&filename=)")
    async def api_upload_variant(version_id: int, request: Request, _auth: auth_dep, label: str = "",
                                 filename: str = "") -> dict:
        _call(FEED._version_row, get_store(), version_id)  # нет версии — 404 до приёма гигабайт
        declared = int(request.headers.get("content-length") or 0)
        if declared > settings.max_upload_bytes:
            raise HTTPException(status_code=413, detail="файл больше MAX_UPLOAD_BYTES")
        name = F.safe_name(filename or request.headers.get("x-filename") or "video.mp4")
        tmp = F.tmp_dir() / f"{uuid4().hex}.upload"
        size = 0
        try:
            with tmp.open("wb") as out:
                async for chunk in request.stream():
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > settings.max_upload_bytes:
                        raise HTTPException(status_code=413, detail="файл больше MAX_UPLOAD_BYTES")
                    out.write(chunk)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        if not size:
            tmp.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail="пустой файл")
        # ffprobe и перенос файла — в пуле потоков: цикл событий не ждёт диск.
        return await run_in_threadpool(_call, FEED.add_uploaded_variant, get_store(), version_id, tmp, name, label)

    @router.delete("/api/feed/variants/{variant_id}", tags=["Feed"], summary="Удалить вариант")
    def api_delete_variant(variant_id: int, _auth: auth_dep) -> dict:
        return _call(FEED.delete_variant, get_store(), variant_id)

    @router.post("/api/feed/variants/{variant_id}/preview", tags=["Feed"], summary="Собрать превью варианта заново")
    def api_rebuild_preview(variant_id: int, _auth: auth_dep) -> dict:
        from app import feed_preview

        _call(FEED._variant_row, get_store(), variant_id)
        feed_preview.requeue(get_store(), variant_id)
        return _call(FEED.get_variant, get_store(), variant_id)

    @router.post("/api/feed/variants/{variant_id}/postings", tags=["Feed"],
                 summary="Отметить: запощено (площадка, необязательная ссылка)")
    def api_mark_posted(variant_id: int, payload: PostingCreate, request: Request, _auth: auth_dep) -> dict:
        return _call(FEED.mark_posted, get_store(), variant_id, payload.platform, payload.url, payload.posted_at,
                     payload.posted_by or _who(request), payload.account)

    @router.delete("/api/feed/postings/{posting_id}", tags=["Feed"], summary="Снять отметку ленты")
    def api_unmark_posted(posting_id: int, _auth: auth_dep) -> dict:
        return _call(FEED.unmark_posted, get_store(), posting_id)

    # ------------------------------------------------------------------ файлы

    def _variant_file(variant_id: int, kind: str) -> Path:
        store = get_store()
        variant = _call(FEED._variant_row, store, variant_id)
        if kind == "file":
            path = FEED.variant_media_path(store, variant)
        else:
            raw = variant.get("preview_path" if kind == "preview" else "poster_path") or ""
            ready = kind == "poster" or variant.get("preview_status") == "ready"
            path = Path(raw) if raw and ready and Path(raw).exists() else None
        if path is None or not F.media_root_ok(path):
            raise HTTPException(status_code=404, detail="файла нет")
        return path

    @router.get("/media/feed/variants/{variant_id}/preview", include_in_schema=False)
    def media_preview(variant_id: int, _auth: auth_dep) -> FileResponse:
        return FileResponse(_variant_file(variant_id, "preview"), media_type="video/mp4",
                            headers={"Cache-Control": "private, max-age=86400"})

    @router.get("/media/feed/variants/{variant_id}/poster", include_in_schema=False)
    def media_poster(variant_id: int, _auth: auth_dep) -> FileResponse:
        return FileResponse(_variant_file(variant_id, "poster"), media_type="image/jpeg",
                            headers={"Cache-Control": "private, max-age=86400"})

    @router.get("/media/feed/variants/{variant_id}/file", include_in_schema=False)
    def media_file(variant_id: int, _auth: auth_dep) -> FileResponse:
        path = _variant_file(variant_id, "file")
        store = get_store()
        variant = FEED._variant_row(store, variant_id)
        version = FEED._version_row(store, int(variant["version_id"]))
        work = FEED._work_row(store, int(version["work_id"]))
        stem = " ".join(f"{work['title']} {version['label'] or 'v' + str(version['n'])} {variant['label']}".split())
        name = F.safe_name(stem.replace("/", "-").replace(":", "x") + path.suffix.lower(), path.name)
        return FileResponse(path, filename=name)

    return router
