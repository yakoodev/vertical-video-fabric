"""REST «Страниц» (app/webpages.py): снимок по ссылке, список, правка словами или списком ops, версии, файлы.

Копия страницы отдаётся с CSP без скриптов и без сети наружу: в ней и так нет скриптов, а заголовок — вторая
стена на случай, если страница чем-то обманула очистку.
"""


from typing import Any, Callable

from fastapi import APIRouter, Body, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from app import webpage_layers as L
from app import webpage_layers_clip as LC
from app import webpages as W
from app import webpages_ops as O
from app.web_search import SearchError, web_search

COPY_HEADERS = {"Content-Security-Policy": "default-src 'none'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                                           "font-src 'self' data:; sandbox", "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer", "Cache-Control": "private, max-age=3600"}
IMAGE_HEADERS = {"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"}


def _project_of(store: Any, payload: dict) -> int | None:
    if payload.get("project_id") not in (None, ""):
        return int(payload["project_id"])
    if payload.get("clip_id") not in (None, ""):
        from app import projects as P

        return P.project_of_plan(store, int(payload["clip_id"]))
    return None


def build_webpages_router(auth_dep: Any, get_store: Callable[[], Any]) -> APIRouter:
    router = APIRouter(tags=["Web pages"])

    @router.post("/api/web-search", summary="Поиск в интернете (Gemini с Google, запасной — DuckDuckGo)")
    async def api_search(_auth: auth_dep, payload: dict = Body(...)) -> dict:
        try:
            return await run_in_threadpool(web_search, str(payload.get("query") or ""), int(payload.get("count") or 6))
        except SearchError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.get("/api/web-pages", summary="Снимки страниц проекта (или клипа)")
    def api_list(_auth: auth_dep, project_id: int | None = None, clip_id: int | None = None) -> dict:
        store = get_store()
        try:
            pid = _project_of(store, {"project_id": project_id, "clip_id": clip_id})
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        # Клип без проекта (холст) — все снимки: проекта, к которому их привязать, нет.
        return {"project_id": pid, "pages": W.list_pages(store, pid)}

    @router.post("/api/web-pages", summary="Снять страницу по ссылке (только просмотр)")
    async def api_snapshot(_auth: auth_dep, payload: dict = Body(...)) -> dict:
        store = get_store()
        try:
            pid = _project_of(store, payload)
            return await run_in_threadpool(W.snapshot, store, str(payload.get("url") or ""), pid,
                                           str(payload.get("viewport") or "mobile"), "owner")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except W.WebPageError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.get("/api/web-pages/{page_id}", summary="Страница: версии, пометки, карта элементов")
    def api_get(page_id: int, _auth: auth_dep) -> dict:
        try:
            return W.get_page(get_store(), page_id, with_outline=True)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.delete("/api/web-pages/{page_id}", summary="Удалить снимок со всеми версиями")
    def api_delete(page_id: int, _auth: auth_dep) -> dict:
        try:
            W.delete_page(get_store(), page_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"ok": True}

    @router.post("/api/web-pages/{page_id}/edit", summary="Правка через HTML: словами (Gemini) или списком ops")
    async def api_edit(page_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        store = get_store()
        base = payload.get("base_version")
        try:
            return await run_in_threadpool(O.edit, store, page_id, str(payload.get("instruction") or ""),
                                           payload.get("ops"), int(base) if base not in (None, "") else None, "owner")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except O.OpsError as exc:
            raise HTTPException(status_code=400, detail=str(exc) + (f": {'; '.join(exc.problems)}" if exc.problems else "")) from exc
        except W.WebPageError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.post("/api/web-pages/{page_id}/current", summary="Сделать версию текущей")
    def api_current(page_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        try:
            return W.set_current(get_store(), page_id, int(payload.get("version") or 0))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/api/web-pages/{page_id}/layer", summary="Готовый слой page для таймлайна")
    def api_layer(page_id: int, _auth: auth_dep, version: int | None = None, duration: float = 4.0, phone: bool = True) -> dict:
        try:
            return W.page_layer(get_store(), page_id, version, max(1.0, min(60.0, duration)), 0.0, phone)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/api/web-pages/{page_id}/blocks", summary="Разобрать страницу на блоки (каждый — своя прозрачная PNG)")
    async def api_blocks(page_id: int, _auth: auth_dep, payload: dict = Body(default={})) -> dict:
        store = get_store()
        version = payload.get("version")
        try:
            dec = await run_in_threadpool(L.decompose, store, page_id, int(version) if version not in (None, "") else None,
                                          payload.get("region"), int(payload.get("max_layers") or L.DEFAULT_MAX_LAYERS),
                                          None, bool(payload.get("fresh")))
            return L.view(dec)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except W.WebPageError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.post("/api/web-pages/{page_id}/to-layers", summary="Блоки страницы → слои клипа (фон, затем блоки лесенкой)")
    async def api_to_layers(page_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        store = get_store()
        version = payload.get("version")
        clip_id = payload.get("clip_id")
        try:
            out = await run_in_threadpool(
                lambda: LC.page_to_layers(
                    store, page_id, int(version) if version not in (None, "") else None,
                    int(clip_id) if clip_id not in (None, "") else None, payload.get("region"),
                    float(payload.get("at_sec") or 0), str(payload.get("layout") or "fit_width"), payload.get("stagger"),
                    payload.get("blocks"), payload.get("as_text"), bool(payload.get("plate", True)),
                    float(payload.get("duration") or LC.DEFAULT_DURATION),
                    int(payload.get("max_layers") or L.DEFAULT_MAX_LAYERS), bool(payload.get("insert", True))))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except W.WebPageError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        out.pop("decomposition", None)
        return out

    @router.get("/api/web-pages/{page_id}/versions/{n}/layers/{key}/{name}", summary="Кадр блока разбора (PNG)")
    def api_block_image(page_id: int, n: int, key: str, name: str, _auth: auth_dep):
        try:
            path = L.block_file(page_id, n, key, name)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return FileResponse(path, media_type="image/png", headers=IMAGE_HEADERS)

    def _file(page_id: int, n: int, what: str):
        try:
            return W.version_file(get_store(), page_id, n, what)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/api/web-pages/{page_id}/versions/{n}/image", summary="Кадр всей страницы (PNG)")
    def api_image(page_id: int, n: int, _auth: auth_dep):
        return FileResponse(_file(page_id, n, "png"), media_type="image/png", headers=IMAGE_HEADERS)

    @router.get("/api/web-pages/{page_id}/versions/{n}/thumb", summary="Миниатюра версии (JPEG)")
    def api_thumb(page_id: int, n: int, _auth: auth_dep):
        return FileResponse(_file(page_id, n, "thumb"), media_type="image/jpeg", headers=IMAGE_HEADERS)

    @router.get("/api/web-pages/{page_id}/versions/{n}/html/index.html", summary="Копия страницы (HTML без скриптов)")
    def api_html(page_id: int, n: int, _auth: auth_dep):
        return FileResponse(_file(page_id, n, "html"), media_type="text/html; charset=utf-8", headers=COPY_HEADERS)

    @router.get("/api/web-pages/{page_id}/versions/{n}/assets/{name}", summary="Файл копии (стиль, картинка, шрифт)")
    def api_asset(page_id: int, n: int, name: str, _auth: auth_dep):
        try:
            path = W.asset_file(page_id, name)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return FileResponse(path, headers=COPY_HEADERS)

    return router
