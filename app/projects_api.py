"""HTTP-ручки проектов (серии и каналы, app/projects.py).

* ``GET/POST /api/projects``, ``GET/PATCH/DELETE /api/projects/{id}`` — проект, его источники
* ``PATCH /api/sources/{id}/project`` — источник в проект (``{"project_id": null}`` — убрать)
* ``GET/PATCH /api/projects/{id}/brief`` — бриф для агента / частичное обновление лора
* ``POST /api/projects/{id}/brief/draft`` — черновик брифа из расшифровок (хук, генератор не встроен)
* ``GET /api/rules``, ``POST /api/rules``, ``POST /api/rules/{id}/revoke`` — правила владельца
* ``GET /api/projects/{id}/stats`` — агрегаты, лучшие и худшие клипы
* ``GET/POST /api/projects/{id}/publications``, ``PATCH/DELETE /api/manual-publications/{id}``,
  ``POST /api/manual-publications/{id}/stats`` (числа руками), ``…/refresh`` (yt-dlp)
* ``GET/PUT /api/clip-plans/{id}/tags`` — теги клипа
* ``GET/POST /api/projects/{id}/hypotheses``, ``POST /api/projects/{id}/hypotheses/evaluate``,
  ``PATCH/DELETE /api/hypotheses/{id}``, ``POST /api/hypotheses/{id}/promote`` (→ правило)

Роутер собирается функцией, как ``app/chat_api.py``: ``main.py`` передаёт зависимость
авторизации и доступ к хранилищу лямбдой (тесты подменяют ``app.main.store``).
"""

# Без from __future__ import annotations: FastAPI читает аннотации _auth: auth_dep сразу.
from typing import Any, Callable

from fastapi import APIRouter, Body, HTTPException, Query

from app import projects as P


def build_projects_router(auth_dep: Any, get_store: Callable[[], Any]) -> APIRouter:
    router = APIRouter(tags=["Projects"])

    def call(fn: Callable, *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(get_store(), *args, **kwargs)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc).strip("'\"")) from exc
        except P.BriefConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # --- проекты ------------------------------------------------------------ #

    @router.get("/api/projects", summary="Проекты (серии и каналы)")
    def api_projects(_auth: auth_dep) -> list[dict]:
        return call(P.list_projects)

    @router.post("/api/projects", summary="Создать проект")
    def api_create_project(_auth: auth_dep, payload: dict = Body(...)) -> dict:
        allowed = {k: payload[k] for k in ("kind", "description", "links", "brand_id", "source_ids") if k in payload}
        return call(P.create_project, str(payload.get("name") or ""), **allowed)

    @router.get("/api/projects/{project_id}", summary="Проект и его источники")
    def api_project(project_id: int, _auth: auth_dep) -> dict:
        return call(P.get_project, project_id)

    @router.patch("/api/projects/{project_id}", summary="Изменить проект (имя, тип, ссылки, бренд, источники)")
    def api_update_project(project_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        allowed = {k: payload[k] for k in ("name", "kind", "description", "links", "brand_id", "source_ids")
                   if k in payload}
        return call(P.update_project, project_id, **allowed)

    @router.delete("/api/projects/{project_id}", summary="Удалить проект (источники и клипы остаются)")
    def api_delete_project(project_id: int, _auth: auth_dep) -> dict:
        return call(P.delete_project, project_id)

    @router.patch("/api/sources/{source_id}/project", summary="Источник — в проект (null — убрать)")
    def api_source_project(source_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        value = payload.get("project_id")
        return call(P.set_source_project, source_id, None if value in (None, "", 0) else int(value))

    # --- бриф --------------------------------------------------------------- #

    @router.get("/api/projects/{project_id}/brief", summary="Бриф проекта для агента: лор, правила, что нарезано")
    def api_brief(project_id: int, _auth: auth_dep) -> dict:
        return call(P.project_brief, project_id)

    @router.patch("/api/projects/{project_id}/brief", summary="Частичное обновление брифа (patch, base_rev)")
    def api_update_brief(project_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        patch = payload.get("patch", {k: v for k, v in payload.items() if k != "base_rev"})
        return call(P.update_brief, project_id, patch, payload.get("base_rev"))

    @router.post("/api/projects/{project_id}/brief/draft", summary="Черновик брифа из расшифровок (хук)")
    def api_brief_draft(project_id: int, _auth: auth_dep, apply: bool = Query(False)) -> dict:
        return call(P.draft_brief, project_id, apply=apply)

    # --- правила ------------------------------------------------------------ #

    @router.get("/api/rules", summary="Правила владельца (проекта + глобальные)")
    def api_rules(_auth: auth_dep, project_id: int | None = Query(None), status: str = Query("active"),
                  include_global: bool = Query(True)) -> list[dict]:
        return call(P.list_rules, project_id=project_id, status=status, include_global=include_global)

    @router.post("/api/rules", summary="Записать правило (дубли и противоречия — без записи, с вопросом)")
    def api_add_rule(_auth: auth_dep, payload: dict = Body(...)) -> dict:
        allowed = {k: payload[k] for k in ("kind", "scope", "project_id", "quote", "origin", "origin_ref", "said_at",
                                           "replaces", "hypothesis_id", "force") if k in payload}
        return call(P.add_rule, str(payload.get("text") or ""), **allowed)

    @router.post("/api/rules/{rule_id}/revoke", summary="Отменить правило (остаётся в истории)")
    def api_revoke_rule(rule_id: int, _auth: auth_dep, payload: dict = Body(default={})) -> dict:
        return call(P.revoke_rule, rule_id, reason=str(payload.get("reason") or ""),
                    quote=str(payload.get("quote") or ""))

    # --- статистика и ручные публикации ------------------------------------- #

    @router.get("/api/projects/{project_id}/stats", summary="Статистика проекта: суммы, медианы, лучшие и худшие")
    def api_project_stats(project_id: int, _auth: auth_dep) -> dict:
        return call(P.project_stats, project_id)

    @router.get("/api/projects/{project_id}/publications", summary="Опубликованное руками (по ссылкам)")
    def api_manual_publications(project_id: int, _auth: auth_dep) -> list[dict]:
        call(P.get_project, project_id, include_sources=False)
        return call(P.list_manual_publications, project_id)

    @router.post("/api/projects/{project_id}/publications", summary="Добавить видео по ссылке (к проекту/клипу)")
    def api_add_manual_publication(project_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        allowed = {k: payload[k] for k in ("clip_plan_id", "title", "published_at", "tags", "notes", "stats")
                   if k in payload}
        pub = call(P.add_manual_publication, str(payload.get("url") or ""), project_id=project_id, **allowed)
        if payload.get("fetch") and not payload.get("stats"):
            pub["fetch"] = call(P.refresh_manual_publication, pub["id"])
            pub["stats"] = call(P.get_manual_publication, pub["id"])["stats"]
        return pub

    @router.patch("/api/manual-publications/{publication_id}", summary="Изменить ручную публикацию")
    def api_update_manual_publication(publication_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        allowed = {k: payload[k] for k in ("title", "published_at", "tags", "notes", "clip_plan_id") if k in payload}
        return call(P.update_manual_publication, publication_id, **allowed)

    @router.delete("/api/manual-publications/{publication_id}", summary="Убрать ручную публикацию")
    def api_delete_manual_publication(publication_id: int, _auth: auth_dep) -> dict:
        return call(P.delete_manual_publication, publication_id)

    @router.post("/api/manual-publications/{publication_id}/stats", summary="Ввести числа руками")
    def api_manual_stats(publication_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        call(P.get_manual_publication, publication_id)
        return call(P.add_manual_stats, publication_id, payload)

    @router.post("/api/manual-publications/{publication_id}/refresh", summary="Подтянуть числа с площадки (yt-dlp)")
    def api_manual_refresh(publication_id: int, _auth: auth_dep) -> dict:
        return call(P.refresh_manual_publication, publication_id)

    # --- теги клипов -------------------------------------------------------- #

    @router.get("/api/clip-plans/{clip_plan_id}/tags", summary="Теги клипа (признаки для гипотез)")
    def api_clip_tags(clip_plan_id: int, _auth: auth_dep) -> dict:
        call(lambda store: store.get_clip_plan(clip_plan_id, include_segments=False))
        return {"clip_plan_id": clip_plan_id, "tags": call(P.get_clip_tags, clip_plan_id)}

    @router.put("/api/clip-plans/{clip_plan_id}/tags", summary="Теги клипа: add / remove / tags (заменить)")
    def api_set_clip_tags(clip_plan_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        tags = call(P.set_clip_tags, clip_plan_id, add=payload.get("add"), remove=payload.get("remove"),
                    replace=payload.get("tags"))
        return {"clip_plan_id": clip_plan_id, "tags": tags}

    # --- гипотезы ----------------------------------------------------------- #

    @router.get("/api/projects/{project_id}/hypotheses", summary="Гипотезы проекта")
    def api_hypotheses(project_id: int, _auth: auth_dep) -> list[dict]:
        call(P.get_project, project_id, include_sources=False)
        return call(P.list_hypotheses, project_id)

    @router.post("/api/projects/{project_id}/hypotheses", summary="Добавить гипотезу")
    def api_add_hypothesis(project_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        allowed = {k: payload[k] for k in ("metric", "direction", "min_n", "baseline", "rule_text", "origin")
                   if k in payload}
        return call(P.add_hypothesis, project_id, str(payload.get("statement") or ""), payload.get("group"),
                    **allowed)

    @router.post("/api/projects/{project_id}/hypotheses/evaluate", summary="Пересчитать гипотезы по свежим числам")
    def api_evaluate_hypotheses(project_id: int, _auth: auth_dep) -> dict:
        call(P.get_project, project_id, include_sources=False)
        return call(P.evaluate_hypotheses, project_id)

    @router.patch("/api/hypotheses/{hypothesis_id}", summary="Изменить гипотезу (ручной статус фиксируется)")
    def api_update_hypothesis(hypothesis_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        allowed = {k: payload[k] for k in ("statement", "group", "baseline", "metric", "direction", "min_n",
                                           "rule_text", "status", "status_locked", "conclusion") if k in payload}
        return call(P.update_hypothesis, hypothesis_id, **allowed)

    @router.delete("/api/hypotheses/{hypothesis_id}", summary="Удалить гипотезу")
    def api_delete_hypothesis(hypothesis_id: int, _auth: auth_dep) -> dict:
        return call(P.delete_hypothesis, hypothesis_id)

    @router.post("/api/hypotheses/{hypothesis_id}/promote", summary="Подтверждённая гипотеза → правило проекта")
    def api_promote_hypothesis(hypothesis_id: int, _auth: auth_dep, payload: dict = Body(default={})) -> dict:
        allowed = {k: payload[k] for k in ("text", "kind", "quote", "force") if k in payload}
        return call(P.promote_hypothesis, hypothesis_id, **allowed)

    return router
