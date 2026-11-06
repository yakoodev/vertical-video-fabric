"""HTTP-ручки ИИ-видео (app/aivideo.py, app/aivideo_gen.py, app/aivideo_assemble.py).

* ``POST /api/ai-video/projects`` — новый проект «ИИ-видео»
* ``GET/PATCH /api/projects/{id}/ai-video`` — обзор / стиль-библия и настройки (владелец: и потолок месяца)
* ``POST /api/projects/{id}/ai-video/characters``, ``PATCH/DELETE /api/ai-video/characters/{cid}``,
  ``POST /api/ai-video/characters/{cid}/looks``, ``PATCH/DELETE /api/ai-video/looks/{lid}``,
  ``POST /api/ai-video/characters/{cid}/upload`` (multipart — картинка-референс владельца)
* ``POST /api/projects/{id}/ai-video/locations``, ``PATCH/DELETE /api/ai-video/locations/{lid}``,
  ``POST /api/ai-video/locations/{lid}/upload``
* ``POST /api/projects/{id}/ai-video/scripts``, ``GET/PATCH/DELETE /api/ai-video/scripts/{sid}``,
  ``POST /api/ai-video/scripts/{sid}/write`` (Gemini), ``PATCH /api/ai-video/shots/{id}``
* ``POST /api/projects/{id}/ai-video/estimate`` / ``…/request`` {stage refs|keyframes|voice|video,
  script_id?, items?, regenerate?}; ``POST /api/ai-video/scripts/{sid}/continuity`` — проверка склеек
* ``POST /api/ai-video/refs/{id}/select`` / ``…/regenerate`` / ``…/check``
* ``POST /api/ai-video/approvals/{aid}/decide`` {action approve|reject, comment} — решение владельца
  с баннера проекта (то же, что «Одобрение»); ``POST /api/ai-video/approvals/{aid}/run`` {confirm};
  ``POST /api/ai-video/approvals/{aid}/retry`` / ``POST /api/ai-video/jobs/{id}/retry`` — «Повторить»
  точно не оплаченное (сеть) без новой карточки
* ``GET /api/projects/{id}/ai-video/jobs``, ``…/ledger``, ``POST /api/projects/{id}/ai-video/cancel``
* ``POST /api/ai-video/scripts/{sid}/assemble`` {music_track_id?, captions_style?}

Ручки — для владельца (cookie-сессия); агенты ходят через MCP, где одобрять нечем.
"""

import shutil
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from fastapi import APIRouter, Body, File, HTTPException, Request, UploadFile

from app import aivideo as V
from app.agent_settings_api import owner_only
from app import aivideo_gen as G
from app import autopilot as A


def build_aivideo_router(auth_dep: Any, get_store: Callable[[], Any]) -> APIRouter:
    router = APIRouter(tags=["AI video"])

    def call(fn: Callable, *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(get_store(), *args, **kwargs)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc).strip("'\"")) from exc
        except G.BudgetError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # --- проект и стиль --------------------------------------------------- #

    @router.post("/api/ai-video/projects", summary="Новый проект «ИИ-видео»")
    def api_create(_auth: auth_dep, payload: dict = Body(...)) -> dict:
        return call(V.create_ai_project, str(payload.get("name") or ""), payload.get("style_kind") or "anime",
                    payload.get("passport_text") or "", payload.get("palette"), payload.get("quality") or "draft",
                    payload.get("provider") or "none", payload.get("description") or "",
                    payload.get("image_model") or "", payload.get("video_model") or "")

    @router.get("/api/projects/{project_id}/ai-video", summary="Обзор проекта ИИ-видео")
    def api_overview(project_id: int, _auth: auth_dep) -> dict:
        return call(V.overview, project_id)

    @router.patch("/api/projects/{project_id}/ai-video", summary="Стиль-библия и настройки")
    def api_style(project_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        return call(V.update_style, project_id, payload, owner=True)

    # --- персонажи, облики, локации ---------------------------------------- #

    @router.post("/api/projects/{project_id}/ai-video/characters", summary="Новый персонаж")
    def api_add_character(project_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        return call(V.save_character, project_id, name=str(payload.get("name") or ""),
                    identity_card=payload.get("identity_card"), slug=str(payload.get("slug") or ""),
                    origin=payload.get("origin"), voice=payload.get("voice"), notes=payload.get("notes"),
                    speech=payload.get("speech"), speech_captions=payload.get("speech_captions"),
                    constraints=payload.get("constraints"), stand_in=payload.get("stand_in"))

    @router.patch("/api/ai-video/characters/{character_id}", summary="Правка персонажа")
    def api_edit_character(character_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        current = call(V.get_character, character_id)
        return call(V.save_character, current["project_id"], name=str(payload.get("name") or ""),
                    identity_card=payload.get("identity_card"), origin=payload.get("origin"),
                    voice=payload.get("voice"), notes=payload.get("notes"), character_id=character_id,
                    speech=payload.get("speech"), speech_captions=payload.get("speech_captions"),
                    constraints=payload.get("constraints"), stand_in=payload.get("stand_in"))

    @router.delete("/api/ai-video/characters/{character_id}", summary="Удалить персонажа")
    def api_del_character(character_id: int, _auth: auth_dep) -> dict:
        return call(V.delete_character, character_id)

    @router.post("/api/ai-video/characters/{character_id}/looks", summary="Новый облик")
    def api_add_look(character_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        return call(V.save_look, character_id, name=str(payload.get("name") or ""),
                    outfit_text=payload.get("outfit_text"), beat=payload.get("beat"), slug=str(payload.get("slug") or ""))

    @router.patch("/api/ai-video/looks/{look_id}", summary="Правка облика")
    def api_edit_look(look_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        look = call(V.get_look, look_id)
        return call(V.save_look, look["character_id"], name=str(payload.get("name") or ""),
                    outfit_text=payload.get("outfit_text"), beat=payload.get("beat"), look_id=look_id)

    @router.delete("/api/ai-video/looks/{look_id}", summary="Удалить облик")
    def api_del_look(look_id: int, _auth: auth_dep) -> dict:
        return call(V.delete_look, look_id)

    @router.post("/api/projects/{project_id}/ai-video/locations", summary="Новая локация")
    def api_add_location(project_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        return call(V.save_location, project_id, name=str(payload.get("name") or ""),
                    description=payload.get("description"), slug=str(payload.get("slug") or ""))

    @router.patch("/api/ai-video/locations/{location_id}", summary="Правка локации")
    def api_edit_location(location_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        loc = call(V.get_location, location_id)
        return call(V.save_location, loc["project_id"], name=str(payload.get("name") or ""),
                    description=payload.get("description"), location_id=location_id)

    @router.delete("/api/ai-video/locations/{location_id}", summary="Удалить локацию")
    def api_del_location(location_id: int, _auth: auth_dep) -> dict:
        return call(V.delete_location, location_id)

    def _upload(target_type: str, target_id: int, file: UploadFile) -> dict:
        from app.montage_assets import assets_dir, kind_for, probe_asset

        store = get_store()
        name = Path(file.filename or "ref.png").name
        kind = kind_for(name, file.content_type or "")
        if kind not in ("image", "gif"):
            raise HTTPException(status_code=400, detail="референс — картинка png/jpg/webp")
        if target_type == "character":
            project_id = call(V.get_character, target_id)["project_id"]
        else:
            project_id = call(V.get_location, target_id)["project_id"]
        dest = assets_dir() / f"{uuid4().hex}-aiv-upload{Path(name).suffix.lower() or '.png'}"
        with dest.open("wb") as fh:
            shutil.copyfileobj(file.file, fh)
        meta = probe_asset(dest)
        asset = store.create_montage_asset(kind=kind, label=f"ИИ-видео: референс {name}"[:120],
                                           description="картинка-референс владельца", tags="ии-видео",
                                           file_path=str(dest), original_filename=name,
                                           mime_type=file.content_type or "", size_bytes=dest.stat().st_size,
                                           project_id=int(project_id), **meta)
        return call(V.add_upload_ref, project_id, target_type, target_id, asset["id"])

    @router.post("/api/ai-video/characters/{character_id}/upload", summary="Картинка-референс персонажа (владелец)")
    def api_upload_character(character_id: int, _auth: auth_dep, file: UploadFile = File(...)) -> dict:
        return _upload("character", character_id, file)

    @router.post("/api/ai-video/locations/{location_id}/upload", summary="Картинка-референс локации")
    def api_upload_location(location_id: int, _auth: auth_dep, file: UploadFile = File(...)) -> dict:
        return _upload("location", location_id, file)

    # --- серии и планы ----------------------------------------------------- #

    @router.post("/api/projects/{project_id}/ai-video/scripts", summary="Новая серия")
    def api_add_script(project_id: int, _auth: auth_dep, payload: dict = Body(default={})) -> dict:
        return call(V.create_script, project_id, brief=str(payload.get("brief") or ""),
                    title=str(payload.get("title") or ""), episode_no=payload.get("episode_no"),
                    duration_target=payload.get("duration_target"), quality=payload.get("quality"))

    @router.get("/api/ai-video/scripts/{script_id}", summary="Раскадровка серии")
    def api_script(script_id: int, _auth: auth_dep) -> dict:
        return call(V.script_view, script_id)

    @router.patch("/api/ai-video/scripts/{script_id}", summary="Правка серии / весь шот-лист")
    def api_edit_script(script_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        return call(V.update_script, script_id, payload)

    @router.delete("/api/ai-video/scripts/{script_id}", summary="Удалить серию")
    def api_del_script(script_id: int, _auth: auth_dep) -> dict:
        return call(V.delete_script, script_id)

    @router.post("/api/ai-video/scripts/{script_id}/write", summary="Gemini пишет сценарий и шот-лист")
    def api_write(script_id: int, _auth: auth_dep, payload: dict = Body(default={})) -> dict:
        from app.aivideo_script import write_script

        return call(write_script, script_id, brief=payload.get("brief"))

    @router.patch("/api/ai-video/shots/{shot_id}", summary="Правка плана")
    def api_edit_shot(shot_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        return call(V.update_shot, shot_id, payload)

    # --- этапы, деньги ----------------------------------------------------- #

    @router.post("/api/projects/{project_id}/ai-video/estimate", summary="Оценка этапа (бесплатно)")
    def api_estimate(project_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        return call(G.estimate, project_id, str(payload.get("stage") or ""), payload.get("script_id"),
                    payload.get("items"), bool(payload.get("regenerate")))

    @router.post("/api/projects/{project_id}/ai-video/request", summary="Запросить этап (карточка с суммой)")
    def api_request(project_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        return call(G.request_stage, project_id, str(payload.get("stage") or ""), payload.get("script_id"),
                    payload.get("items"), bool(payload.get("regenerate")), "ui", str(payload.get("note") or ""))

    @router.post("/api/ai-video/refs/{ref_id}/select", summary="Выбрать версию")
    def api_select(ref_id: int, _auth: auth_dep) -> dict:
        return call(V.select_ref, ref_id)

    @router.post("/api/ai-video/refs/{ref_id}/regenerate", summary="Перегенерировать (своя оценка и карточка)")
    def api_regen(ref_id: int, _auth: auth_dep, payload: dict = Body(default={})) -> dict:
        return call(G.regenerate, ref_id=ref_id, requested_by="ui", note=str(payload.get("note") or ""))

    @router.post("/api/ai-video/targets/regenerate", summary="Сгенерировать один элемент (target)")
    def api_regen_target(_auth: auth_dep, payload: dict = Body(...)) -> dict:
        return call(G.regenerate, target=payload.get("target"), requested_by="ui", note=str(payload.get("note") or ""))

    @router.post("/api/ai-video/refs/{ref_id}/check", summary="Проверка постоянства (Gemini)")
    def api_check(ref_id: int, _auth: auth_dep) -> dict:
        return call(G.check_consistency, ref_id)

    @router.post("/api/ai-video/scripts/{script_id}/continuity", summary="Проверка склеек планов (Gemini)")
    def api_continuity(script_id: int, _auth: auth_dep) -> dict:
        return call(G.check_continuity, script_id)

    @router.post("/api/ai-video/refs/{ref_id}/check-video", summary="Проверка готового видео плана (Gemini)")
    def api_check_video(ref_id: int, _auth: auth_dep) -> dict:
        return call(G.check_video, ref_id)

    # --- блокинг (3D-макет плана, 0 ₽) ------------------------------------- #

    @router.get("/api/ai-video/shots/{shot_id}/blocking", summary="Блокинг плана: раскладка, сцены, скриншоты")
    def api_blocking(shot_id: int, _auth: auth_dep, scenes: bool = False) -> dict:
        from app import aivideo_blocking as B

        def view(store: Any) -> dict:
            shot = V.get_shot(store, shot_id)
            out = B.view(store, shot)
            if scenes:
                out["scenes"] = B.scenes_of(store, shot)
            return out

        return call(view)

    @router.patch("/api/ai-video/shots/{shot_id}/blocking", summary="Своя раскладка/сцена плана и рендер")
    def api_set_blocking(shot_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        from app import aivideo_blocking as B

        def apply(store: Any) -> dict:
            if payload.get("rewrite"):
                shot = V.get_shot(store, shot_id)
                store.db.execute("UPDATE aiv_shots SET blocking_json = '' WHERE id = ?", (shot_id,))
                B.write_blocking(store, int(shot["script_id"]), only={shot_id})
            if payload.get("spec") is not None or payload.get("scenes") is not None:
                B.set_blocking(store, shot_id, spec=payload.get("spec"), scenes=payload.get("scenes"), source="owner")
            shot = V.get_shot(store, shot_id)
            try:
                for which in B.scenes_of(store, shot):
                    B.render(store, shot_id, which)
            except RuntimeError as exc:
                raise V.AivError(f"блокинг не отрисован: {exc}") from exc
            return B.view(store, V.get_shot(store, shot_id))

        return call(apply)

    @router.post("/api/ai-video/approvals/{approval_id}/decide", summary="Решение владельца по карточке этапа")
    def api_decide(approval_id: int, request: Request, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        owner_only(request, "Карточку с суммой решает владелец: агент может только спросить")
        card = call(A.get_approval, approval_id)
        if (card.get("payload") or {}).get("action") != G.CARD_ACTION:
            raise HTTPException(status_code=400, detail="это не карточка этапа ИИ-видео")
        action = str(payload.get("action") or "")
        if action not in ("approve", "reject"):
            raise HTTPException(status_code=400, detail="action: approve или reject")
        # Повтор того же решения (двойной клик, повтор после ошибки клиента) — тот же ответ, без второго запуска.
        return call(G.decide_card, approval_id, action, comment=str(payload.get("comment") or ""), actor="ui")

    @router.post("/api/ai-video/approvals/{approval_id}/run", summary="Выполнить одобренные запуски")
    def api_run(approval_id: int, _auth: auth_dep, payload: dict = Body(default={})) -> dict:
        return call(G.run_approved, approval_id, bool(payload.get("confirm")))

    @router.post("/api/ai-video/approvals/{approval_id}/retry", summary="Повторить не оплаченные (сеть) запуски")
    def api_retry_card(approval_id: int, _auth: auth_dep) -> dict:
        return call(G.retry_card, approval_id)

    @router.post("/api/ai-video/jobs/{job_id}/retry", summary="Повторить запуск без новой карточки")
    def api_retry_job(job_id: int, _auth: auth_dep) -> dict:
        return call(G.retry_job, job_id)

    @router.get("/api/projects/{project_id}/ai-video/jobs", summary="Запуски генерации")
    def api_jobs(project_id: int, _auth: auth_dep, script_id: int | None = None) -> dict:
        store = get_store()
        call(V.require_project, project_id)
        return {"jobs": G.list_jobs(store, project_id=project_id, script_id=script_id),
                "approvals": G.stage_approvals(store, project_id, script_id),
                "budget": G.budget_view(store, project_id, script_id)}

    @router.get("/api/projects/{project_id}/ai-video/ledger", summary="Журнал трат")
    def api_ledger(project_id: int, _auth: auth_dep) -> dict:
        call(V.require_project, project_id)
        return {"ledger": G.ledger(get_store(), project_id), "budget": G.budget_view(get_store(), project_id)}

    @router.post("/api/projects/{project_id}/ai-video/cancel", summary="Отменить ждущие запуски")
    def api_cancel(project_id: int, _auth: auth_dep, payload: dict = Body(default={})) -> dict:
        return call(G.cancel_jobs, project_id, payload.get("approval_id"))

    @router.post("/api/ai-video/scripts/{script_id}/assemble", summary="Собрать серию в холст-клип")
    def api_assemble(script_id: int, _auth: auth_dep, payload: dict = Body(default={})) -> dict:
        from app.aivideo_assemble import assemble

        return call(assemble, script_id, music_track_id=payload.get("music_track_id"),
                    captions_style=payload.get("captions_style"), background=payload.get("background"))

    return router
