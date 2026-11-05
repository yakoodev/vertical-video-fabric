"""HTTP-ручки канала объяснялок (app/explainer_channel.py) — вкладка «Канал» страницы серии.

* ``GET /api/projects/{id}/explainer-channel`` — канал: маскот, темы, расписание, сколько нужно, что мешает
  (404 — проект не канал)
* ``PUT /api/projects/{id}/explainer-channel`` {mascot_id?, niche?, enabled?, settings?, schedule?} — завести
  или поправить (schedule пишется в расписание автопилота проекта)
* ``POST /api/explainer-channels`` {mascot_id, name?, project_id?, niche?, schedule?} — канал с нуля;
  ``GET /api/explainer-channels/mascots`` — маскоты всех проектов (выбор для канала)
* ``POST /api/projects/{id}/explainer-channel/topics`` {topics: [..]} — темы владельца (сразу в очередь)
* ``POST /api/projects/{id}/explainer-channel/topics/propose`` {count?} — Gemini предлагает темы
* ``POST /api/projects/{id}/explainer-channel/run`` — проход канала сейчас
* ``POST /api/explainer/topics/{tid}/decide`` {action approve|reject|retry, comment} ·
  ``PATCH /api/explainer/topics/{tid}`` {topic?, angle?, priority?} · ``DELETE /api/explainer/topics/{tid}``
"""

from typing import Any, Callable

from fastapi import APIRouter, Body, HTTPException

from app import aivideo as V
from app import explainer_channel as C


def build_explainer_channel_router(auth_dep: Any, get_store: Callable[[], Any]) -> APIRouter:
    router = APIRouter(tags=["Explainer"])

    def call(fn: Callable, *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(get_store(), *args, **kwargs)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc).strip("'\"")) from exc
        except (V.AivError, ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def setup_kwargs(payload: dict) -> dict:
        return {"niche": payload.get("niche"), "enabled": payload.get("enabled"), "config": payload.get("settings"),
                "schedule": payload.get("schedule"), "description": payload.get("description")}

    @router.get("/api/projects/{project_id}/explainer-channel", summary="Канал объяснялок проекта")
    def api_view(project_id: int, _auth: auth_dep, soft: bool = False) -> dict:
        # soft — для вкладки: «канал ещё не заведён» это обычное состояние, а не ошибка запроса.
        if soft:
            try:
                return C.channel_view(get_store(), project_id)
            except KeyError:
                return {"no_channel": True}
        return call(C.channel_view, project_id)

    @router.put("/api/projects/{project_id}/explainer-channel", summary="Завести/поправить канал объяснялок")
    def api_setup(project_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        return call(C.setup_channel, project_id, payload.get("mascot_id"), **setup_kwargs(payload))

    @router.get("/api/explainer-channels/mascots", summary="Маскоты для канала (все проекты)")
    def api_mascots(_auth: auth_dep) -> list[dict]:
        return call(C.mascot_choices)

    @router.post("/api/explainer-channels", summary="Новый канал объяснялок")
    def api_create(_auth: auth_dep, payload: dict = Body(...)) -> dict:
        return call(C.setup_channel, payload.get("project_id"), payload.get("mascot_id"),
                    name=str(payload.get("name") or ""), **setup_kwargs(payload))

    @router.post("/api/projects/{project_id}/explainer-channel/topics", summary="Темы владельца")
    def api_add_topics(project_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        return call(C.add_topics, project_id, payload.get("topics") or [], origin="owner", actor="ui")

    @router.post("/api/projects/{project_id}/explainer-channel/topics/propose", summary="Gemini предлагает темы")
    def api_propose(project_id: int, _auth: auth_dep, payload: dict = Body(default={})) -> dict:
        return call(C.propose_topics, project_id, (payload or {}).get("count"), actor="ui")

    @router.post("/api/projects/{project_id}/explainer-channel/run", summary="Проход канала сейчас")
    def api_run(project_id: int, _auth: auth_dep) -> dict:
        return call(C.tick_channel, project_id)

    @router.post("/api/explainer/topics/{topic_id}/decide", summary="Взять / отклонить / повторить тему")
    def api_decide(topic_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        return call(C.decide_topic, topic_id, str(payload.get("action") or ""), str(payload.get("comment") or ""),
                    "ui")

    @router.patch("/api/explainer/topics/{topic_id}", summary="Правка темы")
    def api_edit(topic_id: int, _auth: auth_dep, payload: dict = Body(...)) -> dict:
        return call(C.update_topic, topic_id, payload.get("topic"), payload.get("angle"), payload.get("priority"))

    @router.delete("/api/explainer/topics/{topic_id}", summary="Удалить тему")
    def api_delete(topic_id: int, _auth: auth_dep) -> dict:
        return call(C.delete_topic, topic_id)

    return router
