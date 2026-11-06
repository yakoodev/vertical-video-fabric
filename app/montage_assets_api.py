"""Библиотека файлов для монтажа: загрузка, поиск и генерация, описание ИИ, правка и удаление, отдача файлов.

Роутер собирается функцией, как ``app/cuts_api.py``: ``main.py`` передаёт зависимость авторизации и доступ к хранилищу
и сервисам лямбдами (тесты подменяют ``app.main.store`` и другие имена)."""

# Без from __future__ import annotations: FastAPI читает аннотации вида
# _auth: auth_dep в момент объявления, а auth_dep — локальная переменная.
import subprocess
import threading
from pathlib import Path
from typing import Annotated, Any, Callable
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app import modules
from app.ai.asset_describe import describe_missing
from app.ai.asset_generate import add_from_url as add_asset_from_url, generate_asset
from app.ai.asset_search import fetch_preview as fetch_asset_preview, search_images
from app.api_common import _payload_data, THUMB_CACHE
from app.montage_assets import (
    assets_dir as montage_assets_dir, kind_for as asset_kind_for, probe_asset as probe_montage_asset,
)
from app.thumbs import asset_thumb


# ---- «Файлы для монтажа»: memes / reactions / sounds for the AI montage ------

MAX_ASSET_BYTES = 300 * 1024 * 1024


class GenerateAssetRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=400, description="что нарисовать")
    label: str = Field(default="", max_length=120)
    when: str = Field(default="", max_length=300, description="когда уместно вставлять")
    project_id: int | None = Field(default=None, description="файл этого проекта; нет — общий")
    clip_id: int | None = Field(default=None, description="файл проекта этого клипа")


class SearchAssetsRequest(BaseModel):
    query: str = Field(min_length=2, max_length=200)
    count: int = Field(default=6, ge=1, le=8)
    free_only: bool = True


class AssetFromUrlRequest(BaseModel):
    url: str = Field(max_length=2000)
    label: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=500)
    project_id: int | None = Field(default=None, description="файл этого проекта; нет — общий")
    clip_id: int | None = Field(default=None, description="файл проекта этого клипа")


class VideoFromUrlRequest(BaseModel):
    url: str = Field(max_length=2000)
    start: float = Field(ge=0, description="секунда ролика по ссылке — начало отрезка")
    end: float = Field(gt=0, description="конец отрезка (не дольше 180 с от start)")
    label: str = Field(default="", max_length=120)
    project_id: int | None = Field(default=None, description="файл этого проекта; нет — общий")
    clip_id: int | None = Field(default=None, description="файл проекта этого клипа")


class DescribeAssetsRequest(BaseModel):
    asset_ids: list[int] = Field(default_factory=list, description="пусто = все без описания")


class MontageAssetPatch(BaseModel):
    label: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    tags: str | None = Field(default=None, max_length=200)
    project_id: int | None = Field(default=None, description="перенос файла: id проекта, null — в общие")


def build_montage_assets_router(
    auth_dep: Any,
    get_store: Callable[[], Any],
    get_describe_asset: Callable[[], Any],
) -> APIRouter:
    router = APIRouter()

    def _asset_owner(clip_id: int | None = None, project_id: int | None = None) -> int | None:
        """Чей новый файл (app/asset_scope.py): проект клипа, иначе заданный проект, иначе None — общий."""
        from app.asset_scope import clip_project
        from app.projects import get_project

        try:
            if clip_id:
                return clip_project(get_store(), int(clip_id))
            if project_id:
                return int(get_project(get_store(), int(project_id), include_sources=False)["id"])
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return None

    @router.get("/api/montage-assets", tags=["Montage assets"], summary="List the montage library")
    def api_list_montage_assets(
        _auth: auth_dep,
        scope: Annotated[str, Query(description="all — все, shared — только общие")] = "all",
        project_id: Annotated[int | None, Query(description="только файлы этого проекта")] = None,
        clip_id: Annotated[int | None, Query(description="что доступно клипу: общие и его проекта плюс чужие файлы, "
                                                         "уже стоящие в нём (in_clip_only)")] = None,
    ) -> list[dict]:
        from app.asset_scope import assets_for_clip, render_asset_ids

        if clip_id is not None:
            try:
                render = get_store().get_clip_plan(clip_id, include_segments=False).get("render_settings") or {}
                return assets_for_clip(get_store(), clip_id, render_asset_ids(render))
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
        if project_id is not None:
            return get_store().list_montage_assets(project_id=project_id)
        if scope == "shared":
            return get_store().list_montage_assets(visible_to=())
        return get_store().list_montage_assets()

    @router.post("/api/montage-assets", tags=["Montage assets"], summary="Upload a meme / reaction / sound")
    async def api_upload_montage_asset(
        _auth: auth_dep,
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
        asset = get_store().create_montage_asset(
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
        if modules.is_enabled("asset_ai", get_store()):
            threading.Thread(
                target=lambda: get_describe_asset()(get_store(), asset["id"]),
                name=f"describe-asset-{asset['id']}",
                daemon=True,
            ).start()
        # Анимированная картинка (GIF/APNG/webm с альфой): кадры и fps — для vvf.py asset и агента.
        from app.anim_frames import animation_info, brief, describe as describe_anim

        anim = animation_info(dest) if kind in ("gif", "image", "video") else {}
        if anim.get("animated"):
            asset = {**asset, "animation": brief(anim), "animation_note": describe_anim(anim)}
        return asset

    @router.post(
        "/api/montage-assets/generate",
        tags=["Montage assets"],
        summary="✨ ИИ рисует оригинальную картинку и кладёт её в библиотеку",
    )
    def api_generate_montage_asset(payload: GenerateAssetRequest, _auth: auth_dep) -> dict:
        try:
            return generate_asset(get_store(), payload.prompt, label=payload.label, when=payload.when,
                                  project_id=_asset_owner(payload.clip_id, payload.project_id))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.post(
        "/api/montage-assets/search",
        tags=["Montage assets"],
        summary="🔎 ИИ ищет картинку в интернете — возвращает кандидатов, НИЧЕГО не скачивая",
    )
    def api_search_montage_assets(payload: SearchAssetsRequest, _auth: auth_dep) -> dict:
        try:
            return search_images(payload.query, payload.count, free_only=payload.free_only)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.get("/media/asset-preview", include_in_schema=False)
    def media_asset_preview(url: str, _auth: auth_dep) -> Response:
        """Превью найденной картинки: многие сайты не отдают файл напрямую в <img>."""
        try:
            blob, mime = fetch_asset_preview(url)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - remote errors
            raise HTTPException(status_code=502, detail=f"не удалось загрузить: {str(exc)[:120]}") from exc
        return Response(content=blob, media_type=mime, headers={"Cache-Control": "private, max-age=600"})

    @router.post(
        "/api/montage-assets/from-url",
        tags=["Montage assets"],
        summary="Добавить файл в библиотеку по прямой ссылке",
    )
    def api_add_montage_asset_from_url(payload: AssetFromUrlRequest, _auth: auth_dep) -> dict:
        owner = _asset_owner(payload.clip_id, payload.project_id)
        try:
            asset = add_asset_from_url(get_store(), payload.url, label=payload.label, description=payload.description,
                                       project_id=owner)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - network/remote errors
            raise HTTPException(status_code=502, detail=f"не удалось скачать: {str(exc)[:200]}") from exc
        if modules.is_enabled("asset_ai", get_store()):
            threading.Thread(target=lambda: get_describe_asset()(get_store(), asset["id"]), daemon=True).start()
        return asset

    @router.post(
        "/api/montage-assets/video-from-url",
        tags=["Montage assets"],
        summary="Отрезок ролика по ссылке (yt-dlp, ≤ 180 с, ≤ 1080p, со звуком) → видео в библиотеку",
    )
    def api_add_montage_video_from_url(payload: VideoFromUrlRequest, _auth: auth_dep) -> dict:
        """Скачивается только отрезок, тем же yt-dlp и прокси, что источники (app/video_import.py).
        Звук файла играет в клипе, если у слоя ``video`` стоит ``props.audio: true``."""
        from app.video_import import import_video_from_url

        owner = _asset_owner(payload.clip_id, payload.project_id)
        try:
            return import_video_from_url(get_store(), payload.url, payload.start, payload.end, payload.label, project_id=owner)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - network/remote errors
            raise HTTPException(status_code=502, detail=f"не удалось скачать: {str(exc)[:200]}") from exc

    @router.post(
        "/api/montage-assets/describe",
        tags=["Montage assets"],
        summary="✨ Let the AI look at the files and fill in what they are / when they fit",
    )
    def api_describe_montage_assets(payload: DescribeAssetsRequest, _auth: auth_dep) -> dict:
        return describe_missing(get_store(), payload.asset_ids or None)

    @router.post(
        "/api/montage-assets/{asset_id}/describe",
        tags=["Montage assets"],
        summary="✨ Describe one file",
    )
    def api_describe_montage_asset(asset_id: int, _auth: auth_dep) -> dict:
        try:
            return get_describe_asset()(get_store(), asset_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.patch("/api/montage-assets/{asset_id}", tags=["Montage assets"],
               summary="Rename / describe / tag a file, move it to a project")
    def api_patch_montage_asset(asset_id: int, payload: MontageAssetPatch, _auth: auth_dep) -> dict:
        fields = _payload_data(payload, exclude_none=True)
        if "project_id" in payload.model_fields_set:  # null здесь значим: перенос в общие
            fields["project_id"] = _asset_owner(None, payload.project_id)
        try:
            return get_store().update_montage_asset(asset_id, **fields)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.delete("/api/montage-assets/{asset_id}", tags=["Montage assets"], summary="Delete a file from the library")
    def api_delete_montage_asset(asset_id: int, _auth: auth_dep) -> dict:
        try:
            get_store().delete_montage_asset(asset_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"deleted": True}

    @router.get("/media/montage-assets/{asset_id}/thumb", include_in_schema=False)
    def media_montage_asset_thumb(asset_id: int, _auth: auth_dep) -> FileResponse:
        try:
            return FileResponse(asset_thumb(get_store().get_montage_asset(asset_id)), headers=THUMB_CACHE)
        except (KeyError, ValueError, OSError, subprocess.SubprocessError) as exc:
            raise HTTPException(status_code=404, detail="asset thumb not found") from exc

    @router.get("/media/montage-assets/{asset_id}", include_in_schema=False)
    def media_montage_asset(asset_id: int, _auth: auth_dep) -> FileResponse:
        try:
            asset = get_store().get_montage_asset(asset_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        path = Path(asset["file_path"])
        if not path.exists():
            raise HTTPException(status_code=404, detail="file missing")
        return FileResponse(path, media_type=asset.get("mime_type") or None)

    return router
