"""HTTP-ручки «Клипа на песню» (app/music_video.py, app/music_video_job.py).

* ``POST /api/projects/{id}/music-video`` {track_id | music_url, style, start, end, max_rub, lyrics?, lang?, title?,
  images?, review_plan?, draft?, dry_run?} — новое задание (работа фоном); ``dry_run`` — заглушки вместо картинок, 0 ₽
  (только здесь: инструмента агента для него нет);
* ``GET /api/projects/{id}/music-video`` — задания проекта;
* ``GET /api/music-video/{id}`` — статус (то же, что music_video_status);
* ``POST /api/music-video/{id}/continue`` {regenerate?, max_rub?}, ``POST /api/music-video/{id}/cancel``;
* ``GET /api/music-video/{id}/file/{name}`` — лист кадров, сверка синхрона, план, разбор;
* ``GET /api/music-video/{id}/bundle`` — архив для хост-воркера рендера (Bearer, как у /api/render3d/*).
"""

# Без ``from __future__ import annotations``: FastAPI должен видеть ``auth_dep`` объектом, а не строкой.
from typing import Any, Callable

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import FileResponse


def build_music_video_router(auth_dep: Any, get_store: Callable[[], Any]) -> APIRouter:
    from app import music_video as M
    from app import music_video_job as J

    router = APIRouter()

    def call(fn: Callable, *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc).strip("'\"")) from exc
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/api/projects/{project_id}/music-video", summary="Клип на песню: новое задание")
    def api_create(project_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        store = get_store()
        try:
            track_id = int(payload["track_id"]) if payload.get("track_id") not in (None, "", 0) else None
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="track_id — число") from exc
        job = call(J.create, store, project_id, track_id=track_id, music_url=str(payload.get("music_url") or ""),
                   style=str(payload.get("style") or "comic"), start=payload.get("start"), end=payload.get("end"),
                   max_rub=payload.get("max_rub") if payload.get("max_rub") is not None else 60,
                   lyrics=str(payload.get("lyrics") or ""), lang=str(payload.get("lang") or ""),
                   title=str(payload.get("title") or ""), images=payload.get("images"),
                   review_plan=bool(payload.get("review_plan")), draft=bool(payload.get("draft")),
                   dry_run=bool(payload.get("dry_run")), requested_by="ui")
        return J.view(store, int(job["id"]))

    @router.get("/api/projects/{project_id}/music-video", summary="Клип на песню: задания проекта")
    def api_list(project_id: int, _auth: auth_dep, limit: int = 10) -> dict:
        store = get_store()
        return {"jobs": [J.view(store, int(j["id"])) for j in M.list_jobs(store, project_id, limit=limit)]}

    @router.get("/api/music-video/{job_id}", summary="Клип на песню: статус")
    def api_get(job_id: int, _auth: auth_dep) -> dict:
        return call(J.view, get_store(), job_id)

    @router.post("/api/music-video/{job_id}/continue", summary="Клип на песню: продолжить")
    def api_continue(job_id: int, _auth: auth_dep, payload: dict = Body(default={})) -> dict:
        store = get_store()
        regen = payload.get("regenerate")
        call(J.resume, store, job_id, regenerate=[str(x) for x in regen] if isinstance(regen, list) and regen else None,
             max_rub=payload.get("max_rub"))
        return J.view(store, job_id)

    @router.post("/api/music-video/{job_id}/cancel", summary="Клип на песню: отменить")
    def api_cancel(job_id: int, _auth: auth_dep) -> dict:
        store = get_store()
        call(J.cancel, store, job_id)
        return J.view(store, job_id)

    @router.get("/api/music-video/{job_id}/file/{name}", summary="Клип на песню: файл проверки")
    def api_file(job_id: int, name: str, _auth: auth_dep) -> FileResponse:
        if name not in J.FILES:
            raise HTTPException(status_code=404, detail="такого файла у задания нет")
        job = call(M.get_job, get_store(), job_id)
        path = M.job_dir(job) / J.FILES[name]
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"{name} ещё нет")
        return FileResponse(path, filename=f"mv{job_id}-{name}")

    @router.get("/api/music-video/{job_id}/bundle", summary="Клип на песню: архив для рендера на компьютере")
    def api_bundle(job_id: int, _auth: auth_dep) -> FileResponse:
        store = get_store()
        job = call(M.get_job, store, job_id)
        path = call(J.bundle_path, store, job)
        return FileResponse(path, filename=f"mv{job_id}-bundle.zip", media_type="application/zip")

    return router


__all__ = ["build_music_video_router"]
