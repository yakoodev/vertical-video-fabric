"""Ручки «Референсов» проекта (app/references.py).

* ``GET /api/projects/{id}/references`` — список (главные сверху) и быстрые теги
* ``POST /api/projects/{id}/references`` — загрузить файл (multipart: file, title, note, tags, pinned)
* ``POST /api/projects/{id}/references/link`` — по ссылке (url, title, note, tags, pinned, download)
* ``GET/PATCH/DELETE /api/references/{id}`` — один референс; правка: название, заметка «что взять», теги, главный
* ``POST /api/references/{id}/analyze`` — разобрать заново (фоном)
* ``GET /media/references/{id}/{file|preview|poster|sheet|kNN.jpg}`` — файлы референса

Роутер собирается функцией, как ``app/video_memes_api.py``."""

# Без from __future__ import annotations: FastAPI читает аннотации вида _auth: auth_dep при объявлении.
import mimetypes
from pathlib import Path
from typing import Annotated, Any, Callable
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app import references as R

MAX_UPLOAD_BYTES = 1500 * 1024 * 1024


class ReferenceLink(BaseModel):
    url: str = Field(max_length=2000)
    title: str = Field(default="", max_length=160)
    note: str = Field(default="", max_length=4000)
    tags: list[str] | str = Field(default_factory=list)
    pinned: bool = False
    download: bool = Field(default=True, description="false — сохранить только ссылку, без скачивания и разбора")


class ReferencePatch(BaseModel):
    title: str | None = Field(default=None, max_length=160)
    note: str | None = Field(default=None, max_length=4000)
    tags: list[str] | str | None = None
    pinned: bool | None = None


def build_references_router(auth_dep: Any, get_store: Callable[[], Any]) -> APIRouter:
    router = APIRouter(tags=["References"])

    def call(fn: Callable, *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(get_store(), *args, **kwargs)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc).strip("'\"")) from exc
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/projects/{project_id}/references", summary="Референсы проекта: главные сверху")
    def api_list(project_id: int, _auth: auth_dep) -> dict:
        from app import projects as P

        call(P.get_project, project_id, include_sources=False)
        items = call(R.list_references, project_id)
        return {"references": [R.public(r) for r in items], "quick_tags": list(R.QUICK_TAGS),
                "counts": call(R.count_references, project_id)}

    @router.post("/api/projects/{project_id}/references", summary="Референс файлом: видео, картинка или звук")
    async def api_upload(
        project_id: int,
        _auth: auth_dep,
        file: Annotated[UploadFile, File()],
        title: Annotated[str, Form()] = "",
        note: Annotated[str, Form()] = "",
        tags: Annotated[str, Form(description="через запятую")] = "",
        pinned: Annotated[bool, Form()] = False,
    ) -> dict:
        from app import projects as P
        from app.settings import settings

        call(P.get_project, project_id, include_sources=False)
        safe_name = Path(file.filename or "reference").name
        work = settings.tmp_dir / f"ref-upload-{uuid4().hex}{Path(safe_name).suffix[:8]}"
        work.parent.mkdir(parents=True, exist_ok=True)
        size = 0
        try:
            with work.open("wb") as out:
                while True:
                    chunk = await file.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        raise HTTPException(status_code=413, detail="файл больше 1.5 ГБ — для референса возьми кусок")
                    out.write(chunk)
            await file.close()
            ref = call(R.create_from_file, project_id, work, original_name=safe_name, title=title, note=note,
                       tags=tags, pinned=pinned)
        finally:
            work.unlink(missing_ok=True)
        return R.public(ref)

    @router.post("/api/projects/{project_id}/references/link",
                 summary="Референс по ссылке (TikTok, YouTube, Instagram, картинка, звук, страница)")
    def api_link(project_id: int, payload: ReferenceLink, _auth: auth_dep) -> dict:
        ref = call(R.create_from_url, project_id, payload.url, title=payload.title, note=payload.note,
                   tags=payload.tags, pinned=payload.pinned, download=payload.download)
        return R.public(ref)

    @router.get("/api/references/{ref_id}", summary="Референс с разбором")
    def api_get(ref_id: int, _auth: auth_dep) -> dict:
        return R.public(call(R.get_reference, ref_id))

    @router.patch("/api/references/{ref_id}", summary="Название, «что взять», теги, главный")
    def api_patch(ref_id: int, payload: ReferencePatch, _auth: auth_dep) -> dict:
        return R.public(call(R.update_reference, ref_id, **payload.model_dump(exclude_none=True)))

    @router.delete("/api/references/{ref_id}", summary="Удалить референс вместе с файлами")
    def api_delete(ref_id: int, _auth: auth_dep) -> dict:
        return call(R.delete_reference, ref_id)

    @router.post("/api/references/{ref_id}/analyze", summary="Разобрать заново (фоном)")
    def api_reanalyze(ref_id: int, _auth: auth_dep) -> dict:
        return R.public(call(R.requeue, ref_id))

    @router.get("/media/references/{ref_id}/{name}", include_in_schema=False)
    def media(ref_id: int, name: str, _auth: auth_dep) -> FileResponse:
        ref = call(R.get_reference, ref_id)
        path = R.artifact_path(ref, name)
        if path is None:
            raise HTTPException(status_code=404, detail="файла нет")
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        headers = {"Cache-Control": "private, max-age=86400"}
        if name == "file":
            return FileResponse(path, media_type=media_type, headers=headers,
                                filename=ref.get("original_filename") or path.name, content_disposition_type="inline")
        return FileResponse(path, media_type=media_type, headers=headers)

    return router
