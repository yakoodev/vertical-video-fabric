"""REST страницы «Вопросы по стриму»: только с обычным входом сервиса, публичных ссылок нет."""

from subprocess import CalledProcessError, TimeoutExpired
from typing import Any, Callable

from fastapi import APIRouter, Body, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from app import stream_questions as Q


def build_stream_questions_router(auth_dep: Any, get_store: Callable[[], Any]) -> APIRouter:
    router = APIRouter(tags=["Sources"])

    def _preview(make: Callable[[], Any], media: str, headers: dict | None = None) -> Any:
        try:
            path = make()
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc).strip("'\"")) from exc
        except (CalledProcessError, TimeoutExpired, OSError) as exc:
            raise HTTPException(status_code=502, detail="Кусочек записи не получился") from exc
        return FileResponse(path, media_type=media, headers=headers or {"Cache-Control": "private, max-age=86400"})

    @router.get("/api/sources/{source_id}/stream-questions", summary="Вопросы и мемы по стриму для страницы")
    def api_state(source_id: int, _auth: auth_dep) -> dict:
        try:
            return Q.page_state(get_store(), source_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Источника нет") from exc

    @router.put("/api/stream-questions/{question_id}/answer", summary="Ответить на вопрос, пропустить или снять ответ")
    def api_answer(question_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        try:
            return Q.answer_question(get_store(), question_id, payload.get("option"), payload.get("text") or "",
                                     skip=payload.get("skip") is True)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Вопроса нет") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/stream-questions/{question_id}/clip", include_in_schema=False)
    async def api_question_clip(question_id: int, _auth: auth_dep) -> Any:
        store = get_store()
        try:
            item = Q.get_question(store, question_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Вопроса нет") from exc
        return await run_in_threadpool(
            _preview, lambda: Q.preview_clip(store, item["source_id"], item["t_start"], item["t_end"]), "video/mp4")

    @router.get("/api/stream-questions/{question_id}/thumb", include_in_schema=False)
    async def api_question_thumb(question_id: int, _auth: auth_dep) -> Any:
        store = get_store()
        try:
            item = Q.get_question(store, question_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Вопроса нет") from exc
        return await run_in_threadpool(
            _preview, lambda: Q.preview_thumb(store, item["source_id"], item["t_start"]), "image/jpeg")

    @router.put("/api/meme-moments/{moment_id}/choice", summary="Выбрать мем к моменту или «без мема»")
    def api_choice(moment_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        try:
            return Q.choose_meme(get_store(), moment_id, payload.get("option_id"), none=payload.get("none") is True)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Момента нет") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/meme-moments/{moment_id}/clip", include_in_schema=False)
    async def api_moment_clip(moment_id: int, _auth: auth_dep) -> Any:
        store = get_store()
        try:
            item = Q.get_moment(store, moment_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Момента нет") from exc
        return await run_in_threadpool(
            _preview, lambda: Q.preview_clip(store, item["source_id"], item["t_start"], item["t_end"]), "video/mp4")

    @router.get("/api/meme-moments/{moment_id}/thumb", include_in_schema=False)
    async def api_moment_thumb(moment_id: int, _auth: auth_dep) -> Any:
        store = get_store()
        try:
            item = Q.get_moment(store, moment_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Момента нет") from exc
        return await run_in_threadpool(
            _preview, lambda: Q.preview_thumb(store, item["source_id"], item["t_start"]), "image/jpeg")

    @router.get("/api/meme-options/{option_id}/preview", include_in_schema=False)
    def api_option_preview(option_id: int, _auth: auth_dep) -> Any:
        try:
            path, mime = Q.option_file(get_store(), option_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Картинки нет") from exc
        return FileResponse(path, media_type=mime, headers={"Cache-Control": "private, max-age=86400",
                                                            "X-Content-Type-Options": "nosniff"})

    return router
