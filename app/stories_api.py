"""HTTP-ручки историй (app/stories.py, app/stories_gen.py) — для вкладки «Истории».

* ``GET /api/projects/{id}/stories`` — миры с актёрами, локациями и библиотекой сцен, эпизоды, бюджет, карточки
* ``POST /api/projects/{id}/stories/worlds``, ``PATCH/DELETE /api/stories/worlds/{wid}``,
  ``POST /api/stories/worlds/{wid}/style-ref`` (multipart — референс стиля),
  ``POST /api/stories/worlds/{wid}/style/generate`` (кадр стиля, карточка)
* актёры: ``POST /api/stories/worlds/{wid}/characters`` (JSON), ``PATCH/DELETE /api/stories/characters/{cid}``,
  ``POST /api/stories/characters/{cid}/sheet`` (multipart — свой лист), ``POST …/sheet/generate`` (карточка)
* локации: ``POST /api/stories/worlds/{wid}/locations``, ``PATCH/DELETE /api/stories/locations/{lid}``,
  ``POST /api/stories/locations/{lid}/plate`` (multipart), ``POST …/plate/generate``
* сцены: ``POST /api/stories/worlds/{wid}/scenes`` (JSON) и ``…/scenes/upload`` (multipart),
  ``PATCH/DELETE /api/stories/scenes/{sid}``, ``POST …/regenerate`` (карточка)
* эпизоды: ``POST /api/stories/worlds/{wid}/episodes`` {premise, duration, tone, render?, title_card?, format?
  (story | disaster), part?, music?, engine? (images | 3d — свой 3D-движок, 0 ₽), location? (elevator | apartment),
  renderer? (three | blender — 3D-серия в Blender на компьютере владельца, очередь хост-воркера),
  quality? (пресет Blender: fast — по умолчанию, fast720, final — кино, preview, draft — черновик)};
  ``GET/DELETE /api/stories/episodes/{eid}``, ``POST …/continue`` {reassemble?}, ``POST …/cancel``,
  ``PATCH …/beats`` {beats}, ``PUT …/script`` {sentences, new_characters, new_locations | text}
* ``GET /api/stories/sfx`` — настоящие записи для звуков действий

Решение по карточке — общая ручка ИИ-видео ``POST /api/ai-video/approvals/{aid}/decide``.
"""

import json
import shutil
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from fastapi import APIRouter, Body, File, Form, HTTPException, UploadFile

from app import aivideo_gen as G
from app import stories as S
from app import stories_gen as X


def build_stories_router(auth_dep: Any, get_store: Callable[[], Any]) -> APIRouter:
    router = APIRouter(tags=["Stories"])

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
        dest = assets_dir() / f"{uuid4().hex}-story-upload{Path(name).suffix.lower() or '.png'}"
        with dest.open("wb") as fh:
            shutil.copyfileobj(file.file, fh)
        meta = probe_asset(dest)
        return store.create_montage_asset(kind=kind, label=label[:120], description="загрузка владельца (истории)",
                                           tags="история", file_path=str(dest), original_filename=name,
                                           mime_type=file.content_type or "", size_bytes=dest.stat().st_size,
                                           project_id=int(project_id), **meta)

    def _fields(payload: dict, keys: tuple[str, ...]) -> dict:
        return {k: payload.get(k) for k in keys if k in payload}

    # --- обзор и мир --------------------------------------------------------- #

    @router.get("/api/projects/{project_id}/stories", summary="Истории проекта")
    def api_overview(project_id: int, _auth: auth_dep) -> dict:
        return call(S.overview, project_id)

    @router.post("/api/projects/{project_id}/stories/worlds", summary="Новый мир историй")
    def api_add_world(project_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        return call(S.save_world, project_id, None, **_fields(payload, S.WORLD_FIELDS))

    @router.patch("/api/stories/worlds/{world_id}", summary="Правка мира")
    def api_edit_world(world_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        return call(S.save_world, None, world_id, **_fields(payload, S.WORLD_FIELDS))

    @router.delete("/api/stories/worlds/{world_id}", summary="Удалить мир")
    def api_delete_world(world_id: int, _auth: auth_dep) -> dict:
        return call(S.delete_world, world_id)

    @router.post("/api/stories/worlds/{world_id}/style-ref", summary="Референс стиля (загрузка)")
    def api_style_ref(world_id: int, _auth: auth_dep, file: UploadFile = File(...)) -> dict:
        world = call(S.get_world, world_id)
        asset = _save_upload(file, f"Мир {world['name']}: стиль", world["project_id"])
        return call(S.add_style_ref, world_id, int(asset["id"]))

    @router.post("/api/stories/worlds/{world_id}/style/generate", summary="Кадр стиля (карточка)")
    def api_style_generate(world_id: int, _auth: auth_dep, payload: dict = Body(default={})) -> dict:
        return call(S.request_item, f"{S.T_WORLD}:{world_id}", bool(payload.get("regenerate")), "ui")

    # --- актёры и локации ---------------------------------------------------- #

    @router.post("/api/stories/worlds/{world_id}/characters", summary="Новый актёр")
    def api_add_character(world_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        c = call(S.save_character, world_id, None, name=payload.get("name"), identity_card=payload.get("identity_card"),
                 persona=payload.get("persona"), tags=payload.get("tags"))
        if payload.get("generate"):
            return {"character": c, "generation": call(S.request_item, f"{S.T_CHAR}:{c['id']}", False, "ui")}
        return {"character": c}

    @router.patch("/api/stories/characters/{character_id}", summary="Правка актёра")
    def api_edit_character(character_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        return call(S.save_character, None, character_id, name=payload.get("name"),
                    identity_card=payload.get("identity_card"), persona=payload.get("persona"), tags=payload.get("tags"))

    @router.delete("/api/stories/characters/{character_id}", summary="Убрать актёра")
    def api_delete_character(character_id: int, _auth: auth_dep) -> dict:
        return call(S.delete_character, character_id)

    @router.post("/api/stories/characters/{character_id}/sheet", summary="Лист актёра (загрузка)")
    def api_character_sheet(character_id: int, _auth: auth_dep, file: UploadFile = File(...)) -> dict:
        c = call(S.get_character, character_id)
        asset = _save_upload(file, f"Актёр {c['name']}: лист", c["project_id"])
        return call(S.save_character, None, character_id, sheet_asset_id=int(asset["id"]))

    @router.post("/api/stories/characters/{character_id}/sheet/generate", summary="Нарисовать лист (карточка)")
    def api_character_sheet_generate(character_id: int, _auth: auth_dep, payload: dict = Body(default={})) -> dict:
        return call(S.request_item, f"{S.T_CHAR}:{character_id}", bool(payload.get("regenerate")), "ui")

    @router.post("/api/stories/worlds/{world_id}/locations", summary="Новая локация")
    def api_add_location(world_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        loc = call(S.save_location, world_id, None, name=payload.get("name"), description=payload.get("description"),
                   tags=payload.get("tags"))
        if payload.get("generate"):
            return {"location": loc, "generation": call(S.request_item, f"{S.T_LOC}:{loc['id']}", False, "ui")}
        return {"location": loc}

    @router.patch("/api/stories/locations/{location_id}", summary="Правка локации")
    def api_edit_location(location_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        return call(S.save_location, None, location_id, name=payload.get("name"),
                    description=payload.get("description"), tags=payload.get("tags"))

    @router.delete("/api/stories/locations/{location_id}", summary="Убрать локацию")
    def api_delete_location(location_id: int, _auth: auth_dep) -> dict:
        return call(S.delete_location, location_id)

    @router.post("/api/stories/locations/{location_id}/plate", summary="Плита локации (загрузка)")
    def api_location_plate(location_id: int, _auth: auth_dep, file: UploadFile = File(...)) -> dict:
        loc = call(S.get_location, location_id)
        asset = _save_upload(file, f"Локация {loc['name']}: плита", loc["project_id"])
        return call(S.save_location, None, location_id, plate_asset_id=int(asset["id"]))

    @router.post("/api/stories/locations/{location_id}/plate/generate", summary="Нарисовать плиту (карточка)")
    def api_location_plate_generate(location_id: int, _auth: auth_dep, payload: dict = Body(default={})) -> dict:
        return call(S.request_item, f"{S.T_LOC}:{location_id}", bool(payload.get("regenerate")), "ui")

    # --- сцены --------------------------------------------------------------- #

    @router.post("/api/stories/worlds/{world_id}/scenes", summary="Сцена без картинки (нарисуется по карточке)")
    def api_add_scene(world_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        scene = call(S.save_scene, world_id, None, location_id=payload.get("location_id"), cast=payload.get("cast"),
                     action=payload.get("action"), accent=payload.get("accent"), title=payload.get("title"),
                     tags=payload.get("tags"))
        if payload.get("generate"):
            return {"scene": scene, "generation": call(S.request_item, f"{S.T_SCENE}:{scene['id']}", False, "ui")}
        return {"scene": scene}

    @router.post("/api/stories/worlds/{world_id}/scenes/upload", summary="Сцена — готовая картинка")
    def api_scene_upload(world_id: int, _auth: auth_dep, file: UploadFile = File(...), action: str = Form(""),
                         title: str = Form(""), tags: str = Form(""), location_id: str = Form(""),
                         cast: str = Form("")) -> dict:
        world = call(S.get_world, world_id)
        asset = _save_upload(file, f"Мир {world['name']}: {title or 'сцена'}", world["project_id"])
        try:
            cast_ids = json.loads(cast) if cast.strip().startswith("[") else [c for c in cast.split(",") if c.strip()]
        except ValueError:
            cast_ids = []
        return call(S.save_scene, world_id, None, location_id=location_id or None, cast=cast_ids,
                    action=action or title or "scene", title=title or None, tags=tags, origin="upload",
                    asset_id=int(asset["id"]))

    @router.patch("/api/stories/scenes/{scene_id}", summary="Правка сцены (теги, текст)")
    def api_edit_scene(scene_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        return call(S.save_scene, None, scene_id, location_id=payload.get("location_id"), cast=payload.get("cast"),
                    action=payload.get("action"), accent=payload.get("accent"), title=payload.get("title"),
                    tags=payload.get("tags"))

    @router.delete("/api/stories/scenes/{scene_id}", summary="Убрать сцену")
    def api_delete_scene(scene_id: int, _auth: auth_dep) -> dict:
        return call(S.delete_scene, scene_id)

    @router.post("/api/stories/scenes/{scene_id}/regenerate", summary="Перерисовать сцену (карточка)")
    def api_scene_regenerate(scene_id: int, _auth: auth_dep, payload: dict = Body(default={})) -> dict:
        if payload.get("action"):
            call(S.save_scene, None, scene_id, action=payload["action"])
        return call(S.request_item, f"{S.T_SCENE}:{scene_id}", True, "ui", str(payload.get("note") or ""))

    # --- эпизоды ------------------------------------------------------------- #

    @router.post("/api/stories/worlds/{world_id}/episodes", summary="Новый эпизод «завязка → клип»")
    def api_episode_create(world_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        options = {k: payload[k] for k in ("render", "cover", "music_track_id", "title_card", "cut_sfx", "format", "part",
                                           "music", "engine", "location", "renderer", "quality") if payload.get(k) is not None}
        return call(X.create, world_id, str(payload.get("premise") or ""), payload.get("duration"),
                    str(payload.get("tone") or ""), None, options, "ui")

    @router.get("/api/stories/episodes/{episode_id}", summary="Эпизод: статус, сценарий, биты, оценка")
    def api_episode(episode_id: int, _auth: auth_dep) -> dict:
        ep = call(S.get_episode, episode_id)
        if ep["status"] in ("planned", "awaiting_approval", "generating", "failed") and ep.get("beats"):
            try:
                ep["estimate"] = call(X.estimate, episode_id)
                ep["readiness"] = call(X.readiness, episode_id)
            except HTTPException:
                pass
        return ep

    @router.delete("/api/stories/episodes/{episode_id}", summary="Удалить эпизод")
    def api_episode_delete(episode_id: int, _auth: auth_dep) -> dict:
        return call(S.delete_episode, episode_id)

    @router.post("/api/stories/episodes/{episode_id}/continue", summary="Продолжить эпизод сейчас")
    def api_episode_continue(episode_id: int, _auth: auth_dep, payload: dict = Body(default={})) -> dict:
        if payload.get("reassemble"):
            return call(X.reassemble, episode_id)
        return call(X.resume, episode_id)

    @router.post("/api/stories/episodes/{episode_id}/cancel", summary="Отменить эпизод")
    def api_episode_cancel(episode_id: int, _auth: auth_dep) -> dict:
        return call(X.cancel, episode_id)

    @router.patch("/api/stories/episodes/{episode_id}/beats", summary="Правка битов (сцены, камера, звук)")
    def api_episode_beats(episode_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        out = call(X.update_beats, episode_id, payload.get("beats") or [])
        X.kick()
        return out

    @router.put("/api/stories/episodes/{episode_id}/script", summary="Свой сценарий")
    def api_episode_script(episode_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        data = {k: payload.get(k) for k in ("title", "logline", "sentences", "new_characters", "new_locations", "text",
                                            "part_title", "hook_words", "viewer_question", "hashtags", "mistake",
                                            "cliffhanger")}
        out = call(X.set_script, episode_id, data, "owner")
        X.kick()
        return out

    @router.get("/api/stories/sfx", summary="Звуки действий (настоящие записи)")
    def api_sfx(_auth: auth_dep, q: str = "", project_id: int | None = None) -> dict:
        store = get_store()
        options = S.sfx_options(store, project_id)
        return {"sounds": options, "match": S.resolve_sfx(q, options) if q else None}

    return router
