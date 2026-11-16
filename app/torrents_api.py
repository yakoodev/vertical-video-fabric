"""Источник из торрента (app/torrents.py): добавить magnet или .torrent, выбрать файлы, прогресс, отмена.

Роутер собирается функцией, как ``app/sources_api.py``: main.py передаёт зависимость авторизации и сервис лямбдой."""

# Без from __future__ import annotations: FastAPI читает аннотации вида _auth: auth_dep при объявлении.
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.torrent_meta import MAX_TORRENT_FILE_BYTES, TorrentError


class TorrentStartPayload(BaseModel):
    # Номера файлов (index из списка files); пусто — то, что отмечено по умолчанию.
    files: list[int] | None = None


def _optional_int(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError as exc:
        raise TorrentError("project_id — целое число") from exc


def _flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _files(value: Any) -> list[int] | None:
    if value in (None, "", []):
        return None
    if isinstance(value, str):
        value = [part for part in value.replace(" ", "").split(",") if part]
    try:
        return [int(v) for v in value]
    except (TypeError, ValueError) as exc:
        raise TorrentError("files — список номеров файлов") from exc


def build_torrents_router(auth_dep: Any, get_service: Callable[[], Any]) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/api/torrents",
        tags=["Sources"],
        summary="Add a torrent source (magnet link or .torrent file)",
        description=(
            "Multipart `file` (.torrent) or JSON/form `magnet`. Optional `project_id`, `files` (indices to download) "
            "and `auto_start`. Returns the torrent task at once: status `metadata` → `selecting` (pick files with "
            "`POST /api/torrents/{id}/start`) → `downloading` → `succeeded` with `source_ids`."
        ),
    )
    async def api_add_torrent(request: Request, _auth: auth_dep) -> dict:
        try:
            torrent_bytes: bytes | None = None
            if "multipart/form-data" in request.headers.get("content-type", ""):
                form = await request.form()
                fields: dict = dict(form)
                upload = form.get("file")
                if upload is not None and hasattr(upload, "read"):
                    try:
                        torrent_bytes = await upload.read(MAX_TORRENT_FILE_BYTES + 1)
                    finally:
                        await upload.close()
                    if len(torrent_bytes) > MAX_TORRENT_FILE_BYTES:
                        raise TorrentError("файл .torrent больше 10 МБ — это не торрент")
            else:
                fields = await request.json()
                if not isinstance(fields, dict):
                    raise TorrentError("ожидался JSON-объект")
            return get_service().add(
                magnet=str(fields.get("magnet") or "").strip() if not torrent_bytes else "",
                torrent_bytes=torrent_bytes,
                project_id=_optional_int(fields.get("project_id")),
                files=_files(fields.get("files")),
                auto_start=_flag(fields.get("auto_start")),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/torrents", tags=["Sources"], summary="List torrent tasks")
    def api_torrents(_auth: auth_dep) -> list[dict]:
        return get_service().list_tasks()

    @router.get("/api/torrents/{task_id}", tags=["Sources"], summary="Torrent task: files, progress, sources")
    def api_torrent(task_id: int, _auth: auth_dep) -> dict:
        try:
            return get_service().get(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/api/torrents/{task_id}/start", tags=["Sources"], summary="Pick files and start the download")
    def api_start_torrent(task_id: int, _auth: auth_dep, payload: TorrentStartPayload | None = None) -> dict:
        try:
            return get_service().start(task_id, payload.files if payload else None)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/api/torrents/{task_id}/cancel", tags=["Sources"], summary="Cancel a torrent and delete partial files")
    def api_cancel_torrent(task_id: int, _auth: auth_dep) -> dict:
        try:
            return get_service().cancel(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return router
