"""HTTP-ручки объяснялок (app/explainer.py, app/explainer_gen.py) — для вкладки «Объяснялки».

* ``GET /api/projects/{id}/explainer`` — маскоты с библиотекой поз, ролики, бюджет, карточки
* ``POST /api/projects/{id}/explainer/mascots``, ``PATCH/DELETE /api/explainer/mascots/{mid}``,
  ``POST /api/explainer/mascots/{mid}/sheet`` (multipart — лист маскота),
  ``POST /api/explainer/mascots/{mid}/sheet/generate`` (карточка)
* ``POST /api/explainer/mascots/{mid}/poses`` (multipart: file + description/title/tags — готовая
  картинка; или JSON — поза без картинки), ``PATCH/DELETE /api/explainer/poses/{pid}``,
  ``POST /api/explainer/poses/{pid}/regenerate`` {description?, note?} (карточка)
* ``POST /api/explainer/mascots/{mid}/videos`` {topic, duration, tone, render?} — новый ролик;
  ``GET/DELETE /api/explainer/videos/{vid}``, ``POST …/continue`` {reassemble?}, ``POST …/cancel``,
  ``PATCH …/beats`` {beats}, ``PUT …/script`` {hook, intro, points, cta | text}
* ``POST /api/projects/{id}/explainer/import-pilot`` — маскот «Ух» и позы пилота

Решение по карточке — общая ручка ИИ-видео ``POST /api/ai-video/approvals/{aid}/decide``.
"""


import shutil
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from fastapi import APIRouter, Body, File, Form, HTTPException, UploadFile

from app import aivideo_gen as G
from app import explainer as E
from app import explainer_gen as X


def build_explainer_router(auth_dep: Any, get_store: Callable[[], Any]) -> APIRouter:
    router = APIRouter(tags=["Explainer"])

    def call(fn: Callable, *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(get_store(), *args, **kwargs)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc).strip("'\"")) from exc
        except G.BudgetError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def _save_upload(file: UploadFile, label: str, project_id: int) -> dict:
        from app.montage_assets import assets_dir, kind_for, probe_asset

        store = get_store()
        name = Path(file.filename or "image.png").name
        kind = kind_for(name, file.content_type or "")
        if kind != "image":
            raise HTTPException(status_code=400, detail="нужна картинка png/jpg/webp")
        dest = assets_dir() / f"{uuid4().hex}-expl-upload{Path(name).suffix.lower() or '.png'}"
        with dest.open("wb") as fh:
            shutil.copyfileobj(file.file, fh)
        meta = probe_asset(dest)
        return store.create_montage_asset(kind=kind, label=label[:120], description="загрузка владельца (объяснялки)",
                                           tags="объяснялка,маскот", file_path=str(dest), original_filename=name,
                                           mime_type=file.content_type or "", size_bytes=dest.stat().st_size,
                                           project_id=int(project_id), **meta)

    # --- обзор и маскоты --------------------------------------------------- #

    @router.get("/api/projects/{project_id}/explainer", summary="Объяснялки проекта")
    def api_overview(project_id: int, _auth: auth_dep) -> dict:
        return call(E.overview, project_id)

    @router.post("/api/projects/{project_id}/explainer/mascots", summary="Новый маскот")
    def api_add_mascot(project_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        fields = {k: payload.get(k) for k in E.MASCOT_FIELDS if k in payload}
        return call(E.save_mascot, project_id, None, **fields)

    @router.patch("/api/explainer/mascots/{mascot_id}", summary="Правка маскота")
    def api_edit_mascot(mascot_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        fields = {k: payload.get(k) for k in E.MASCOT_FIELDS if k in payload}
        return call(E.save_mascot, None, mascot_id, **fields)

    @router.delete("/api/explainer/mascots/{mascot_id}", summary="Удалить маскота")
    def api_delete_mascot(mascot_id: int, _auth: auth_dep) -> dict:
        return call(E.delete_mascot, mascot_id)

    @router.post("/api/explainer/mascots/{mascot_id}/sheet", summary="Лист маскота (загрузка)")
    def api_sheet(mascot_id: int, _auth: auth_dep, file: UploadFile = File(...)) -> dict:
        mascot = call(E.get_mascot, mascot_id)
        asset = _save_upload(file, f"Маскот {mascot['name']}: лист", mascot["project_id"])
        return call(E.set_sheet, mascot_id, int(asset["id"]))

    @router.post("/api/explainer/mascots/{mascot_id}/sheet/generate", summary="Нарисовать лист маскота (карточка)")
    def api_sheet_generate(mascot_id: int, _auth: auth_dep, payload: dict = Body(default={})) -> dict:
        return call(E.request_sheet, mascot_id, bool(payload.get("regenerate")), "ui", str(payload.get("note") or ""))

    # --- позы -------------------------------------------------------------- #

    @router.post("/api/explainer/mascots/{mascot_id}/poses/upload", summary="Поза — готовая картинка")
    def api_pose_upload(mascot_id: int, _auth: auth_dep, file: UploadFile = File(...),
                        description: str = Form(""), title: str = Form(""), tags: str = Form(""),
                        number_shown: str = Form("")) -> dict:
        mascot = call(E.get_mascot, mascot_id)
        asset = _save_upload(file, f"Маскот {mascot['name']}: {title or 'поза'}", mascot["project_id"])
        return call(E.save_pose, mascot_id, description=description or title or "pose", title=title or None,
                    tags=tags, number_shown=number_shown or None, origin="upload", asset_id=int(asset["id"]))

    @router.post("/api/explainer/mascots/{mascot_id}/poses", summary="Поза без картинки (нарисуется по карточке)")
    def api_pose_add(mascot_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        pose = call(E.save_pose, mascot_id, description=payload.get("description"), title=payload.get("title"),
                    tags=payload.get("tags"), emotion=payload.get("emotion"),
                    number_shown=payload.get("number_shown"), origin="agent")
        if payload.get("generate"):
            return {"pose": pose, "generation": call(E.request_pose, int(pose["id"]), False, "ui")}
        return {"pose": pose}

    @router.patch("/api/explainer/poses/{pose_id}", summary="Правка позы (теги, текст)")
    def api_pose_edit(pose_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        return call(E.save_pose, pose_id=pose_id, description=payload.get("description"),
                    title=payload.get("title"), tags=payload.get("tags"), emotion=payload.get("emotion"),
                    number_shown=payload.get("number_shown"))

    @router.delete("/api/explainer/poses/{pose_id}", summary="Убрать позу")
    def api_pose_delete(pose_id: int, _auth: auth_dep) -> dict:
        return call(E.delete_pose, pose_id)

    @router.post("/api/explainer/poses/{pose_id}/regenerate", summary="Перерисовать позу (карточка)")
    def api_pose_regenerate(pose_id: int, _auth: auth_dep, payload: dict = Body(default={})) -> dict:
        if payload.get("description"):
            call(E.save_pose, pose_id=pose_id, description=payload["description"])
        return call(E.request_pose, pose_id, True, "ui", str(payload.get("note") or ""))

    # --- ролики ------------------------------------------------------------ #

    @router.post("/api/explainer/mascots/{mascot_id}/videos", summary="Новый ролик «тема → клип»")
    def api_video_create(mascot_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        # dry_run — явная просьба о черновике: тон вместо голоса, заглушки вместо поз, клип с пометкой.
        options = {k: payload[k] for k in ("render", "cover", "music_track_id", "dry_run")
                   if payload.get(k) is not None}
        return call(X.create, mascot_id, str(payload.get("topic") or ""), payload.get("duration") or 45,
                    str(payload.get("tone") or ""), None, options, "ui")

    @router.get("/api/explainer/videos/{video_id}", summary="Ролик: статус, сценарий, биты, оценка")
    def api_video(video_id: int, _auth: auth_dep) -> dict:
        video = call(E.get_video, video_id)
        if video["status"] in ("planned", "awaiting_approval", "generating", "failed") and video.get("beats"):
            try:
                video["estimate"] = call(X.estimate, video_id)
                video["readiness"] = call(X.readiness, video_id)
            except HTTPException:
                pass
        return {**video, "facts": call(X.facts, video_id, video)}

    @router.delete("/api/explainer/videos/{video_id}", summary="Удалить ролик")
    def api_video_delete(video_id: int, _auth: auth_dep) -> dict:
        return call(E.delete_video, video_id)

    @router.post("/api/explainer/videos/{video_id}/continue", summary="Продолжить ролик сейчас")
    def api_video_continue(video_id: int, _auth: auth_dep, payload: dict = Body(default={})) -> dict:
        if payload.get("reassemble"):
            video = call(E.get_video, video_id, False)
            if video["status"] != "done":
                raise HTTPException(status_code=400, detail="пересобрать можно готовый ролик")
            call(E.set_status, video_id, "assembling", ("done",))
        return call(X.resume, video_id)

    @router.post("/api/explainer/videos/{video_id}/cancel", summary="Отменить ролик")
    def api_video_cancel(video_id: int, _auth: auth_dep) -> dict:
        return call(X.cancel, video_id)

    @router.patch("/api/explainer/videos/{video_id}/beats", summary="Правка битов (позы, чипы, слова)")
    def api_video_beats(video_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        out = call(X.update_beats, video_id, payload.get("beats") or [])
        X.kick()
        return out

    @router.put("/api/explainer/videos/{video_id}/script", summary="Свой сценарий")
    def api_video_script(video_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        data = {k: payload.get(k) for k in ("title", "hook", "intro", "points", "cta", "text")}
        out = call(X.set_script, video_id, data, "owner")
        X.kick()
        return out

    @router.post("/api/projects/{project_id}/explainer/import-pilot", summary="Импорт пилота «Ух»")
    def api_import_pilot(project_id: int, _auth: auth_dep) -> dict:
        return call(E.import_pilot, project_id)

    return router
