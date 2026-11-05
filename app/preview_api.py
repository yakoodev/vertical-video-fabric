"""▶ HTTP-ручки предпросмотра монтажа и пакета для монтажки.

* ``GET /api/clip-plans/{id}/playback`` — план проигрывания (``app/playback.py``).
* ``GET /api/clip-plans/{id}/revision`` — отпечаток монтажа: сменился — клип правил кто-то ещё.
* ``GET/POST /api/sources/{id}/proxy`` — статус и постановка лёгкого прокси 480p (``app/preview_proxy.py``).
* ``GET /media/sources/{id}/proxy`` — сам файл (Range, как у любого ``FileResponse``).
* ``GET/POST /api/clip-plans/{id}/preview-pieces`` — куски 480p по частям клипа; ``GET /media/sources/{id}/pieces/{имя}`` — файл куска.
* ``GET /api/clip-plans/{id}/timeline/package`` — zip для Premiere/Resolve (``app/timeline_package.py``).
"""

# Без from __future__ import annotations: FastAPI читает аннотации _auth: auth_dep в момент объявления.
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel


class PiecesRequest(BaseModel):
    """focus — с какой части клипа начать (та, что сейчас на экране); retry — собрать заново сбойные."""

    focus: int = 0
    retry: bool = False


class ProxyRequest(BaseModel):
    """force — собрать и для маленькой записи (сама по себе она и так играет легко)."""

    force: bool = False


def build_preview_router(auth_dep: Any, get_store: Callable[[], Any]) -> APIRouter:
    router = APIRouter()

    def _source(source_id: int) -> dict:
        try:
            return get_store().get_source(source_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/api/clip-plans/{clip_plan_id}/playback", tags=["Clip file"],
                summary="▶ План проигрывания монтажа: что видно и что звучит в каждую секунду готового ролика")
    def api_playback(clip_plan_id: int, _auth: auth_dep) -> dict:  # type: ignore[valid-type]
        from app.playback import playback_plan

        try:
            return playback_plan(get_store(), clip_plan_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/api/clip-plans/{clip_plan_id}/revision", tags=["Clip file"],
                summary="Отпечаток монтажа клипа: сменился — клип правил агент или другая вкладка")
    def api_revision(clip_plan_id: int, _auth: auth_dep) -> dict:  # type: ignore[valid-type]
        from app.playback import revision

        try:
            return {"revision": revision(get_store(), clip_plan_id)}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/api/sources/{source_id}/proxy", tags=["Sources"],
                summary="Прокси 480p для предпросмотра: статус и адрес")
    def api_proxy_status(source_id: int, _auth: auth_dep) -> dict:  # type: ignore[valid-type]
        from app.preview_proxy import status

        return status(_source(source_id))

    @router.post("/api/sources/{source_id}/proxy", tags=["Sources"],
                 summary="Поставить прокси 480p в фон (идемпотентно, оригинал не трогает)")
    def api_proxy_ensure(source_id: int, _auth: auth_dep, payload: ProxyRequest | None = None) -> dict:  # type: ignore[valid-type]
        from app.preview_proxy import ensure

        return ensure(_source(source_id), force=bool(payload and payload.force))

    def _ranges(clip_plan_id: int) -> tuple[dict, list]:
        from app.playback import part_ranges

        try:
            return part_ranges(get_store(), clip_plan_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/api/clip-plans/{clip_plan_id}/preview-pieces", tags=["Clip file"],
                summary="Куски 480p для просмотра монтажа: что готово")
    def api_pieces_status(clip_plan_id: int, _auth: auth_dep) -> dict:  # type: ignore[valid-type]
        from app.preview_proxy import pieces_status

        source, ranges = _ranges(clip_plan_id)
        return pieces_status(source, ranges)

    @router.post("/api/clip-plans/{clip_plan_id}/preview-pieces", tags=["Clip file"],
                 summary="Собрать куски 480p для просмотра монтажа (идемпотентно, вперёд общего прокси)")
    def api_pieces_ensure(clip_plan_id: int, _auth: auth_dep, payload: PiecesRequest | None = None) -> dict:  # type: ignore[valid-type]
        from app.preview_proxy import ensure_pieces

        source, ranges = _ranges(clip_plan_id)
        return ensure_pieces(source, ranges, payload.focus if payload else 0, bool(payload and payload.retry))

    @router.get("/media/sources/{source_id}/pieces/{name}", include_in_schema=False)
    def media_piece(source_id: int, name: str, _auth: auth_dep) -> FileResponse:  # type: ignore[valid-type]
        from app.preview_proxy import piece_file

        _source(source_id)
        path = piece_file(source_id, name)
        if path is None:
            raise HTTPException(status_code=404, detail="куска нет")
        # Имя содержит границы и версию: файл с этим именем не меняется.
        return FileResponse(path, media_type="video/mp4", headers={"Cache-Control": "private, max-age=86400, immutable"})

    @router.get("/media/sources/{source_id}/proxy", include_in_schema=False)
    def media_proxy(source_id: int, _auth: auth_dep) -> FileResponse:  # type: ignore[valid-type]
        from app.preview_proxy import proxy_path

        _source(source_id)
        path = proxy_path(source_id)
        if not path.exists():
            raise HTTPException(status_code=404, detail="прокси ещё нет")
        return FileResponse(path, media_type="video/mp4", headers={"Cache-Control": "private, max-age=3600"})

    @router.get("/api/clip-plans/{clip_plan_id}/timeline/package", tags=["Clip file"],
                summary="🎬 Пакет для Premiere / DaVinci Resolve: zip с монтажом, субтитрами и файлами, без рендера")
    def api_timeline_package(clip_plan_id: int, _auth: auth_dep, pieces: bool = False) -> FileResponse:  # type: ignore[valid-type]
        """``pieces=true`` — положить в архив короткие куски-прокси (иначе только ссылка на запись)."""
        from app.timeline_package import build_package

        try:
            result = build_package(get_store(), clip_plan_id, with_pieces=pieces)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return FileResponse(result["path"], filename=result["filename"], media_type="application/zip")

    return router
