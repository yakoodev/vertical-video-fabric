"""HTTP-ручки сведения звука (app/audio_mix.py) и справочника эффектов (app/audio_fx.py) — то же,
что MCP ``audio_mix_report`` / ``auto_mix`` / ``audio_fx_reference``; редактор зовёт их из вкладки «Звук».

* ``GET  /api/audio-fx/reference`` — эффекты, параметры, пресеты;
* ``GET  /api/clip-plans/{id}/audio-mix?use_render=true`` — отчёт сведения по стемам;
* ``POST /api/clip-plans/{id}/auto-mix`` ``{dry_run?, target_lufs?, music_under_voice_db?, duck_db?}``.
"""

# Без from __future__ import annotations: FastAPI читает аннотации вида _auth: auth_dep при объявлении.
from typing import Any, Callable

from fastapi import APIRouter, Body, HTTPException


def build_audio_mix_router(auth_dep: Any, get_store: Callable[[], Any]) -> APIRouter:
    router = APIRouter(tags=["Clip plans"])

    @router.get("/api/audio-fx/reference", summary="🎚 Звуковые эффекты: параметры и пресеты")
    def api_audio_fx_reference(_auth: auth_dep) -> dict:  # type: ignore[valid-type]
        from app.audio_fx import reference

        return reference()

    @router.get("/api/clip-plans/{clip_plan_id}/audio-mix",
                summary="🎚 Сведение звука: голос против музыки под речью, звуки, LUFS, true peak")
    def api_audio_mix(clip_plan_id: int, _auth: auth_dep, use_render: bool = True) -> dict:  # type: ignore[valid-type]
        from app.audio_mix import mix_report

        try:
            return mix_report(get_store(), clip_plan_id, use_render=use_render)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)[:400]) from exc

    @router.post("/api/clip-plans/{clip_plan_id}/auto-mix",
                 summary="🎚 Автосведение: громкость музыки, ducking по фразам, звуки под голос")
    def api_auto_mix(clip_plan_id: int, _auth: auth_dep,  # type: ignore[valid-type]
                     payload: dict = Body(default={})) -> dict:
        from app.agent_tools import ToolContext, run_tool
        from app.clip_history import snapshot

        store = get_store()
        try:
            store.get_clip_plan(clip_plan_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        args = {k: payload[k] for k in ("dry_run", "target_lufs", "music_under_voice_db", "duck_db",
                                        "sfx_under_voice_db") if k in payload}
        ctx = ToolContext(store=store, clip_plan_id=clip_plan_id,
                          on_first_change=lambda: snapshot(store, clip_plan_id, "автосведение"))
        result, ok = run_tool(ctx, "auto_mix", args)
        if not ok:
            raise HTTPException(status_code=400, detail=(result or {}).get("error") or "автосведение не вышло")
        return result

    return router
