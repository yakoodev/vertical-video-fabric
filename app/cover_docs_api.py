"""REST редактора превью: одни и те же адреса документа для владельца и для ссылки.

``/api/cover-docs/{id}/…`` — с обычным входом; ``/api/c/{token}/…`` — без входа, но только этот документ
и его файлы: другого id в адресе просто нет. Чужой, отозванный и просроченный токен — одинаковый 404.
Тело загрузки и документа читается потоком с ограничением, а не целиком в память.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Callable

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, Response

from app import cover_docs as D

NO_LEAK_HEADERS = {"Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex, nofollow", "Cache-Control": "no-store",
                   "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
                   "Content-Security-Policy": "frame-ancestors 'none'"}
SMALL_BODY = 4096
ASSET_HEADERS = {"Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff",
                 "Content-Security-Policy": "default-src 'none'", "Cache-Control": "private, max-age=3600"}
NOT_FOUND_PAGE = ("<!doctype html><html lang=\"ru\"><meta charset=\"utf-8\"><title>Ссылка не работает</title>"
                  "<body style=\"font:16px sans-serif;background:#08090f;color:#f2f3f7;padding:32px\">"
                  "<p>Ссылка не работает: она отозвана, просрочена или набрана с ошибкой.</p></body></html>")


@dataclass
class Access:
    doc_id: int
    can_edit: bool
    rate_key: str | None  # None — владелец: без ограничения частоты
    is_owner: bool

    @property
    def perm(self) -> str:
        return "edit" if self.can_edit else "view"


def _rate(access: Access, bucket: str) -> None:
    if access.rate_key is not None and not D.limiter.allow(access.rate_key, bucket):
        raise HTTPException(status_code=429, detail="Слишком много запросов, подождите минуту")


def _need_edit(access: Access) -> None:
    if not access.can_edit:
        raise HTTPException(status_code=403, detail="Эта ссылка только для просмотра")


async def _read_capped(request: Request, limit: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(status_code=413, detail="Слишком большой запрос")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise HTTPException(status_code=413, detail="Слишком большой запрос")
        chunks.append(chunk)
    return b"".join(chunks)


async def _read_json(request: Request, limit: int = D.MAX_DOC_BYTES * 2) -> dict:
    try:
        payload = json.loads(await _read_capped(request, limit) or b"{}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Нужен JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Нужен объект JSON")
    return payload


def _public_doc(store: Any, access: Access) -> dict:
    doc = D.get_doc(store, access.doc_id)
    clip = None
    if doc["clip_id"]:
        try:
            row = store.get_clip(int(doc["clip_id"]))
            clip = {"duration": float(row.get("duration_sec") or 0), "title": row.get("title") or ""}
        except KeyError:
            clip = None
    out = {"title": doc["title"], "format": doc["format"], "doc": doc["doc"], "rev": doc["rev"], "perm": access.perm,
           "clip": clip, "owner": access.is_owner, "assets": sorted(D.doc_assets(access.doc_id))}
    if access.is_owner:
        out.update(id=doc["id"], clip_id=doc["clip_id"])
    return out


def _add_doc_routes(router: APIRouter, prefix: str, access_dep: Callable[..., Access], get_store: Callable[[], Any]) -> None:
    Acc = Annotated[Access, Depends(access_dep)]

    @router.get(f"{prefix}/doc", summary="Документ обложки")
    def api_doc(access: Acc) -> dict:
        _rate(access, "read")
        return _public_doc(get_store(), access)

    @router.put(f"{prefix}/doc", summary="Сохранить документ обложки")
    async def api_save(access: Acc, request: Request) -> dict:
        _need_edit(access)
        _rate(access, "save")
        payload = await _read_json(request)
        store = get_store()
        try:
            base = payload.get("base_rev")
            D.save_doc(store, access.doc_id, payload.get("doc"), int(base) if base is not None else None,
                       payload.get("title") if access.is_owner else None)
        except D.RevisionConflict as exc:
            raise HTTPException(status_code=409, detail={"message": str(exc), "rev": exc.current["rev"]}) from exc
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _public_doc(store, access)

    @router.post(f"{prefix}/assets", summary="Загрузить картинку (тело запроса: PNG, JPG или WebP)")
    async def api_upload(access: Acc, request: Request) -> dict:
        _need_edit(access)
        _rate(access, "upload")
        data = await _read_capped(request, D.MAX_UPLOAD_BYTES)
        try:
            return D.store_upload(access.doc_id, data)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get(f"{prefix}/assets/{{name}}", include_in_schema=False)
    def api_asset(access: Acc, name: str) -> FileResponse:
        _rate(access, "read")
        path = D.asset_path(access.doc_id, name)
        if path is None:
            raise HTTPException(status_code=404, detail="Файла нет")
        media = "image/png" if name.endswith(".png") else "image/jpeg"
        return FileResponse(path, media_type=media, headers=ASSET_HEADERS)

    @router.post(f"{prefix}/frames", summary="Взять кадр из ролика в файлы документа")
    async def api_frame(access: Acc, request: Request) -> dict:
        _need_edit(access)
        _rate(access, "frame")
        payload = await _read_json(request, SMALL_BODY)
        origin = payload.get("origin") if payload.get("origin") in ("clean", "render", "source") else "clean"
        try:
            return await run_in_threadpool(D.add_frame, get_store(), access.doc_id, payload.get("t"), origin)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Клипа больше нет") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail="Кадр не получился") from exc

    @router.get(f"{prefix}/thumb", include_in_schema=False)
    def api_thumb(access: Acc, t: float = 0) -> FileResponse:
        _rate(access, "thumb")
        try:
            path = D.thumb_path(get_store(), access.doc_id, t)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="Кадра нет") from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail="Кадр не получился") from exc
        return FileResponse(path, media_type="image/jpeg", headers=ASSET_HEADERS)

    @router.get(f"{prefix}/library", summary="Что можно вставить: картинки клипа, шаблоны, шрифты")
    def api_library(access: Acc) -> dict:
        _rate(access, "read")
        from app.motion_assets import load_fonts

        store = get_store()
        size = D.get_doc(store, access.doc_id)["doc"]
        pictures = D.library_pictures(store, access.doc_id) if access.can_edit else []
        fonts = [{k: f.get(k) for k in ("family", "file", "weight", "style", "label", "variable", "weight_range")}
                 for f in load_fonts()]
        return {"pictures": pictures, "templates": D.template_briefs(size["width"], size["height"]), "fonts": fonts}

    @router.get(f"{prefix}/fonts/{{file}}", include_in_schema=False)
    def api_font(access: Acc, file: str) -> FileResponse:
        _rate(access, "read")
        from app.motion_assets import font_path

        path = font_path(file)
        if path is None:
            raise HTTPException(status_code=404, detail="Шрифта нет")
        return FileResponse(path, media_type="application/octet-stream",
                            headers={**ASSET_HEADERS, "Cache-Control": "private, max-age=86400"})

    @router.post(f"{prefix}/template", summary="Слои шаблона для этого документа (не сохраняет)")
    async def api_template(access: Acc, request: Request) -> dict:
        _need_edit(access)
        _rate(access, "frame")
        payload = await _read_json(request, SMALL_BODY)
        store = get_store()
        try:
            layers = await run_in_threadpool(D.apply_template, store, access.doc_id, str(payload.get("template") or ""))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Клипа больше нет") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail="Кадр не получился") from exc
        return {"layers": layers, "assets": sorted(D.doc_assets(access.doc_id))}

    def _export(access: Access, kind: str, download: int) -> FileResponse:
        _rate(access, "export")
        store = get_store()
        try:
            path: Path = D.export_jpeg(store, access.doc_id) if kind == "jpg" else D.render_doc(store, access.doc_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail="Экспорт не получился, попробуйте ещё раз") from exc
        headers = {**NO_LEAK_HEADERS}
        if download:
            headers["Content-Disposition"] = f'attachment; filename="cover.{kind}"'
        media = "image/jpeg" if kind == "jpg" else "image/png"
        return FileResponse(path, media_type=media, headers=headers)

    @router.get(f"{prefix}/export.png", summary="Готовая обложка, PNG")
    def api_export_png(access: Acc, download: int = 0) -> FileResponse:
        return _export(access, "png", download)

    @router.get(f"{prefix}/export.jpg", summary="Готовая обложка, JPG")
    def api_export_jpg(access: Acc, download: int = 0) -> FileResponse:
        return _export(access, "jpg", download)


def build_cover_docs_router(auth_dep: Any, get_store: Callable[[], Any], spa_index: Callable[[], Path]) -> APIRouter:
    router = APIRouter(tags=["Render"])

    def owner_access(doc_id: int, _auth: auth_dep) -> Access:
        try:
            D.get_doc(get_store(), doc_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Документа нет") from exc
        return Access(doc_id=doc_id, can_edit=True, rate_key=None, is_owner=True)

    def _bad_token_gate(request: Request) -> None:
        # Перебор токенов: лимит по адресу, независимо от самого токена.
        host = request.client.host if request.client else "unknown"
        if not D.limiter.allow(f"ip:{host}", "bad_token"):
            raise HTTPException(status_code=429, detail="Слишком много запросов, подождите минуту")

    def token_access(token: str, request: Request) -> Access:
        link = D.resolve_link(get_store(), token)
        if link is None:
            _bad_token_gate(request)
            raise HTTPException(status_code=404, detail="Ссылка не работает", headers=NO_LEAK_HEADERS)
        return Access(doc_id=link["doc_id"], can_edit=link["perm"] == "edit", rate_key=link["key"], is_owner=False)

    _add_doc_routes(router, "/api/cover-docs/{doc_id}", owner_access, get_store)
    _add_doc_routes(router, "/api/c/{token}", token_access, get_store)

    @router.get("/api/cover-docs", summary="Документы обложек клипа")
    def api_list(_auth: auth_dep, clip_id: int | None = None) -> dict:
        docs = D.list_docs(get_store(), clip_id)
        return {"docs": [{k: d[k] for k in ("id", "clip_id", "title", "format", "rev", "updated_at")} for d in docs]}

    @router.post("/api/cover-docs", summary="Новый документ обложки (или свежий документ этого клипа)")
    def api_create(_auth: auth_dep, payload: dict = Body(...)) -> dict:
        store = get_store()
        clip_id = payload.get("clip_id")
        try:
            if clip_id and payload.get("reuse", True):
                existing = D.list_docs(store, int(clip_id))
                if existing:
                    return {"id": existing[0]["id"], "created": False}
            doc = D.create_doc(store, int(clip_id) if clip_id else None, str(payload.get("title") or ""),
                               payload.get("format"), payload.get("template"), str(payload.get("text") or ""))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Клипа нет") from exc
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"id": doc["id"], "created": True}

    @router.delete("/api/cover-docs/{doc_id}", summary="Удалить документ обложки")
    def api_delete(access: Annotated[Access, Depends(owner_access)]) -> dict:
        D.delete_doc(get_store(), access.doc_id)
        return {"deleted": True}

    @router.post("/api/cover-docs/{doc_id}/apply", summary="Сделать обложкой клипа")
    def api_apply(access: Annotated[Access, Depends(owner_access)], payload: dict = Body(default={})) -> dict:
        try:
            return D.apply_to_clip(get_store(), access.doc_id, burn=payload.get("burn", True) is not False)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail="Экспорт не получился, попробуйте ещё раз") from exc

    @router.get("/api/cover-docs/{doc_id}/links", summary="Ссылки на этот документ (без токенов)")
    def api_links(access: Annotated[Access, Depends(owner_access)]) -> dict:
        return {"links": D.list_links(get_store(), access.doc_id)}

    @router.post("/api/cover-docs/{doc_id}/links", summary="Создать ссылку на редактирование")
    def api_link_create(access: Annotated[Access, Depends(owner_access)], payload: dict = Body(default={})) -> dict:
        try:
            return D.create_link(get_store(), access.doc_id, str(payload.get("perm") or "edit"),
                                 payload.get("days", D.DEFAULT_LINK_DAYS))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.delete("/api/cover-docs/{doc_id}/links/{link_id}", summary="Отозвать ссылку")
    def api_link_revoke(access: Annotated[Access, Depends(owner_access)], link_id: int) -> dict:
        try:
            D.revoke_link(get_store(), access.doc_id, link_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Ссылки нет") from exc
        return {"revoked": True}

    @router.get("/c/{token}", include_in_schema=False)
    def public_editor(token: str, request: Request) -> Response:
        # Страницу отдаём только по живой ссылке: остальной интерфейс сервиса остаётся за входом.
        link = D.resolve_link(get_store(), token)
        if link is None:
            _bad_token_gate(request)
            return HTMLResponse(NOT_FOUND_PAGE, status_code=404, headers=NO_LEAK_HEADERS)
        index = spa_index()
        if not index.is_file():
            return HTMLResponse(NOT_FOUND_PAGE, status_code=404, headers=NO_LEAK_HEADERS)
        return FileResponse(index, headers=NO_LEAK_HEADERS)

    return router
