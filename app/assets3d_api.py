"""Ручки «3D-моделей» проекта (app/assets3d.py) и «Анимаций» окна просмотра (app/anim3d.py).

* ``GET /api/projects/{id}/assets3d`` — модели по группам (Персонажи / Реквизит / Окружение)
* ``POST /api/projects/{id}/assets3d`` — загрузить .vrm/.glb/.gltf (multipart: file, name, kind); превью
  собирается сразу. Тот же файл второй раз — та же модель (``duplicate: true``)
* ``GET/PATCH/DELETE /api/assets3d/{id}`` — одна модель; правка: название, группа
* ``POST /api/assets3d/{id}/thumb`` — обложка (снимок из окна просмотра); ``POST /api/assets3d/{id}/rebuild``
* ``GET /media/assets3d/{id}/{preview|thumb|original}`` — файлы; превью — gzip, если браузер умеет
  (``X-Raw-Length`` — размер без сжатия, для полосы загрузки)
* ``GET /api/anim3d`` — список анимаций; ``GET /api/anim3d/{id}`` — клип
"""

# Без from __future__ import annotations: FastAPI читает аннотации вида _auth: auth_dep при объявлении.
from pathlib import Path
from typing import Annotated, Any, Callable
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from app import anim3d as AN
from app import assets3d as A
from app.assets3d_preview import ModelError


class AssetPatch(BaseModel):
    name: str | None = Field(default=None, max_length=A.NAME_LIMIT)
    kind: str | None = None


def build_assets3d_router(auth_dep: Any, get_store: Callable[[], Any]) -> APIRouter:
    router = APIRouter(tags=["3D-модели"])

    def call(fn: Callable, *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(get_store(), *args, **kwargs)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc).strip("'\"")) from exc
        except (ValueError, TypeError) as exc:  # ModelError — тоже ValueError
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/projects/{project_id}/assets3d", summary="3D-модели проекта по группам")
    def api_list(project_id: int, _auth: auth_dep) -> dict:
        from app import projects as P

        call(P.get_project, project_id, include_sources=False)
        items = call(A.list_assets, project_id)
        return {"groups": A.grouped(items), "count": len(items), "max_upload_mb": A.MAX_UPLOAD_BYTES // (1024 * 1024),
                "formats": sorted(A.FORMATS)}

    @router.post("/api/projects/{project_id}/assets3d", summary="Загрузить 3D-модель (.vrm, .glb, .gltf)")
    async def api_upload(
        project_id: int,
        _auth: auth_dep,
        file: Annotated[UploadFile, File()],
        name: Annotated[str, Form()] = "",
        kind: Annotated[str, Form()] = "",
    ) -> dict:
        from app import projects as P
        from app.settings import settings

        call(P.get_project, project_id, include_sources=False)
        safe_name = Path(file.filename or "model.glb").name
        try:
            A.format_of(safe_name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        work = settings.tmp_dir / f"asset3d-upload-{uuid4().hex}{Path(safe_name).suffix.lower()}"
        work.parent.mkdir(parents=True, exist_ok=True)
        size = 0
        try:
            with work.open("wb") as out:
                while True:
                    chunk = await file.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > A.MAX_UPLOAD_BYTES:
                        raise HTTPException(status_code=413,
                                            detail=f"файл больше {A.MAX_UPLOAD_BYTES // (1024 * 1024)} МБ")
                    out.write(chunk)
            await file.close()
            from anyio import to_thread

            asset = await to_thread.run_sync(lambda: call(A.create_from_file, project_id, work, original_name=safe_name,
                                                          name=name, kind=kind))
        finally:
            work.unlink(missing_ok=True)
        return {**A.public(asset), "duplicate": bool(asset.get("duplicate"))}

    @router.get("/api/assets3d/{asset_id}", summary="3D-модель: справка и адреса файлов")
    def api_get(asset_id: int, _auth: auth_dep) -> dict:
        return A.public(call(A.get_asset, asset_id))

    @router.patch("/api/assets3d/{asset_id}", summary="Название и группа модели")
    def api_patch(asset_id: int, payload: AssetPatch, _auth: auth_dep) -> dict:
        return A.public(call(A.update_asset, asset_id, **payload.model_dump(exclude_none=True)))

    @router.delete("/api/assets3d/{asset_id}", summary="Удалить модель вместе с файлами")
    def api_delete(asset_id: int, _auth: auth_dep) -> dict:
        return call(A.delete_asset, asset_id)

    @router.post("/api/assets3d/{asset_id}/thumb", summary="Обложка: снимок из окна просмотра")
    async def api_thumb(asset_id: int, _auth: auth_dep, file: Annotated[UploadFile, File()]) -> dict:
        data = await file.read(A.MAX_THUMB_BYTES + 1)
        return A.public(call(A.set_thumbnail, asset_id, data))

    @router.post("/api/assets3d/{asset_id}/rebuild", summary="Собрать превью заново")
    def api_rebuild(asset_id: int, _auth: auth_dep) -> dict:
        return A.public(call(A.rebuild, asset_id))

    @router.get("/media/assets3d/{asset_id}/{name}", include_in_schema=False)
    def media(asset_id: int, name: str, request: Request, _auth: auth_dep) -> FileResponse:
        asset = call(A.get_asset, asset_id)
        if name not in ("preview", "thumb", "original"):
            raise HTTPException(status_code=404, detail="файла нет")
        path = A.file_path(asset, name)
        if path is None:
            raise HTTPException(status_code=404, detail="файла нет")
        headers = {"Cache-Control": "private, max-age=604800" if request.query_params.get("v") else "private, no-cache"}
        if name == "thumb":
            return FileResponse(path, media_type="image/webp", headers=headers)
        if name == "original":
            return FileResponse(path, media_type="model/gltf-binary" if asset["format"] != "gltf" else "model/gltf+json",
                                headers=headers, filename=asset["original_filename"] or path.name)
        headers["X-Raw-Length"] = str(path.stat().st_size)
        headers["Vary"] = "Accept-Encoding"
        gz = A.file_path(asset, "preview_gz")
        if gz is not None and "gzip" in request.headers.get("accept-encoding", "").lower():
            headers["Content-Encoding"] = "gzip"
            return FileResponse(gz, media_type="model/gltf-binary", headers=headers)
        return FileResponse(path, media_type="model/gltf-binary", headers=headers)

    @router.get("/api/anim3d", summary="Анимации для просмотра персонажей")
    def api_anim_list(_auth: auth_dep) -> dict:
        return AN.library()

    @router.get("/api/anim3d/{clip_id}", summary="Клип анимации")
    def api_anim_clip(clip_id: str, _auth: auth_dep) -> JSONResponse:
        data = AN.clip(clip_id)
        if data is None:
            raise HTTPException(status_code=404, detail="такой анимации нет")
        return JSONResponse(data, headers={"Cache-Control": "private, max-age=86400"})

    return router


__all__ = ["build_assets3d_router", "ModelError"]
