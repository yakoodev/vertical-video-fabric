"""3D-серия: то, что зовут и интерфейс (``app/s3d_api.py``), и агент (``app/s3d_tools.py``) — обзор проекта, новая
серия из сценария/темы/готового плана, правка плана с новой версией, «Рендер» с оценкой, состояние серии."""

from __future__ import annotations

from typing import Any

from app import s3d_plan as SP
from app import s3d_store as D

DOCS_IMG = "/api/series3d/preset-thumb"


def catalogs() -> dict:
    """Что можно выбрать в плане (для пикеров интерфейса и агента): ракурсы, движения, игра, переходы, свет."""
    from app import presets3d

    def items(cat: str) -> list[dict]:
        return [{"name": p["name"], "title": p.get("title"), "ru": p.get("ru"), "thumb": f"{DOCS_IMG}/{cat}/{p['name']}"}
                for p in presets3d.catalog().get(cat, {}).get("presets", [])]

    moves = [m for m in items("camera") if m["name"] in SP.VRM_MOVES]
    return {"engines": SP.ENGINES, "framings": SP.VRM_FRAMINGS, "moves": moves, "emotion": items("emotion"),
            "gesture": items("gesture"), "transition": items("transition"), "light": items("light"), "mood": items("mood"),
            "look": items("look"), "room": items("room"), "events": SP.EVENT_KINDS, "cards": list(SP.CARD_KINDS),
            "sounds": sorted(__import__("app.vtubika", fromlist=["SFX"]).SFX), "character_types": D.CHARACTER_TYPES,
            "location_types": D.LOCATION_TYPES, "plate_props": D.PLATE_PROPS}


def _job_brief(job: dict | None) -> dict | None:
    if not job:
        return None
    res = job.get("result") or {}
    return {"id": job["id"], "status": job["status"], "status_label": job["status_label"], "note": job["note"],
            "progress": job["progress"], "error": job["error"], "approval_id": job["approval_id"],
            "estimate_rub": job["estimate_rub"], "plan_version": job["plan_version"], "dry_run": bool(job["options"].get("dry_run")),
            "checks": res.get("checks") or [], "checks_ok": res.get("checks_ok"), "sheet_url": res.get("sheet_url"),
            "video_url": res.get("video_url"), "clip_id": res.get("clip_id"), "editor_url": res.get("editor_url"),
            "duration": res.get("duration"), "render_sec": res.get("render_sec"), "voices": res.get("voices") or [],
            "warnings": res.get("warnings") or [], "created_at": job["created_at"], "finished_at": job["finished_at"]}


def episode_brief(store: Any, ep: dict) -> dict:
    jobs = D.episode_jobs(store, int(ep["id"]), 1)
    out = {k: ep.get(k) for k in ("id", "project_id", "title", "engine", "status", "status_label", "plan_version", "clip_id",
                                  "created_at", "updated_at")}
    out["job"] = _job_brief(jobs[0] if jobs else None)
    out["source"] = ep.get("source") or {}
    return out


def overview(store: Any, project_id: int) -> dict:
    from app import aivideo_gen as G
    from app import projects as P
    from app import s3d_voice as SV

    project = P.get_project(store, int(project_id), include_sources=False)
    items = D.list_items(store, project_id)
    return {"project": {k: project.get(k) for k in ("id", "name", "kind", "kind_label")},
            "settings": D.get_settings(store, project_id),
            "characters": [i for i in items if i["kind"] == "character"],
            "locations": [i for i in items if i["kind"] == "location"],
            "props": [i for i in items if i["kind"] == "prop"],
            "episodes": [episode_brief(store, ep) for ep in D.list_episodes(store, project_id)],
            "builtins": [{"kind": k, "slug": s, "name": n} for k, s, n, _d in D.builtin_items()],
            "approvals": [a for a in G.stage_approvals(store, project_id) if a.get("stage") == SV.STAGE],
            "budget": G.budget_view(store, project_id)}


def episode_full(store: Any, episode_id: int) -> dict:
    ep = D.get_episode(store, episode_id)
    out = episode_brief(store, ep)
    out["plan"] = ep["plan"]
    out["versions"] = D.plan_versions(store, episode_id)
    out["jobs"] = [_job_brief(j) for j in D.episode_jobs(store, episode_id, 5)]
    out["estimate"] = estimate(store, episode_id)
    out["estimate_sec"] = SP.estimate_sec(ep["plan"])
    try:
        lib, cfg = D.library(store, int(ep["project_id"])), D.get_settings(store, int(ep["project_id"]))
        _plan, out["warnings"] = SP.validate_plan(ep["plan"], lib, cfg)
        out["problems"] = []
    except SP.PlanError as exc:
        out["warnings"], out["problems"] = [], exc.problems
    return out


def create(store: Any, project_id: int, *, script: str = "", topic: str = "", plan: dict | None = None, title: str = "",
           cast: list[str] | None = None, location: str | None = None, narrator: str = "narrator", duration: float = 30,
           tone: str = "", author: str = "agent", client: Any = None) -> dict:
    """Новая серия: готовый план (проверяется) или план из сценария/темы (Gemini или правила)."""
    lib, cfg = D.library(store, project_id), D.get_settings(store, project_id)
    if not lib["characters"] or not lib["locations"]:
        D.seed_builtins(store, project_id)  # пустая библиотека — готовые персонажи и комната стримерши
        lib = D.library(store, project_id)
    if plan is not None:
        clean, warnings = SP.validate_plan(plan, lib, cfg)
        src = {"kind": "plan"}
        notes: list[str] = []
    else:
        from app import s3d_write as W

        got = W.plan_from_script(store, project_id, script=script, topic=topic, title=title, cast=cast, location=location,
                                 narrator=narrator, duration=duration, tone=tone, client=client)
        clean, warnings, notes = got["plan"], got["warnings"], got["notes"]
        src = {"kind": got["source"], "topic": topic[:300]} if topic else {"kind": got["source"]}
    ep = D.create_episode(store, project_id, title or clean.get("title"), clean, author=author,
                          note=f"план: {src['kind']}", source={**src, **(clean.get("source") or {})})
    return {**episode_full(store, int(ep["id"])), "notes": notes, "warnings": warnings}


def update_plan(store: Any, episode_id: int, *, plan: dict | None = None, changes: list[dict] | None = None,
                author: str = "agent", note: str = "") -> dict:
    """Новая версия плана: целиком (plan) или правками (changes). Ошибки — PlanError, план не меняется."""
    ep = D.get_episode(store, episode_id)
    if D.active_job(store, episode_id) is not None:
        raise SP.PlanError(["серия рендерится — дождитесь конца или отмените рендер, потом правьте план"])
    base = plan if plan is not None else SP.apply_changes(ep["plan"], changes or [])
    lib, cfg = D.library(store, int(ep["project_id"])), D.get_settings(store, int(ep["project_id"]))
    clean, warnings = SP.validate_plan(base, lib, cfg)
    D.save_plan(store, episode_id, clean, author=author, note=note)
    return {**episode_full(store, episode_id), "warnings": warnings}


def estimate(store: Any, episode_id: int, dry: bool = False) -> dict:
    """Сколько стоит «Рендер»: голоса реплик (0 ₽ у Gemini и черновика), что уже записано."""
    from app import aivideo_providers as PR
    from app import s3d_voice as SV

    ep = D.get_episode(store, episode_id)
    pid = int(ep["project_id"])
    lib, cfg = D.library(store, pid), D.get_settings(store, pid)
    total, paid_lines, ready, voices = 0.0, 0, 0, {}
    for line in ep["plan"].get("lines") or []:
        who = line["who"] if line["who"] != "narrator" else ep["plan"].get("narrator", "narrator")
        voice = SV.voice_for(lib, cfg, who, dry)
        text = SV.line_text(ep["plan"], line)
        voices[who] = SV.describe(voice)
        if SV.line_ref(store, pid, int(episode_id), line, voice, text) is not None:
            ready += 1
            continue
        if PR.tts_paid(voice["provider"]):
            tts = PR.get_tts(voice["provider"], voice["model"], voice["voice"], None)
            total += len(text) * tts.price_rub_per_char()
            paid_lines += 1
    total = round(total, 2)
    lines = len(ep["plan"].get("lines") or [])
    label = (f"≈ {total:.2f} ₽ за платные голоса ({paid_lines} реплик) — сначала карточка «Разрешить»" if total > 0
             else "0 ₽: голоса бесплатные" + (" (уже записаны)" if ready == lines else ""))
    return {"total_rub": total, "paid_lines": paid_lines, "ready_lines": ready, "lines": lines, "voices": voices,
            "label": label, "render": "3D рисуется на этом сервере, 0 ₽"}


def render(store: Any, episode_id: int, *, dry_run: bool = False, requested_by: str = "agent", run: bool = False) -> dict:
    from app import s3d_render as R

    ep = D.get_episode(store, episode_id)
    lib, cfg = D.library(store, int(ep["project_id"])), D.get_settings(store, int(ep["project_id"]))
    plan, _w = SP.validate_plan(ep["plan"], lib, cfg)
    if plan["engine"] not in SP.RENDERABLE:
        raise SP.PlanError([f"движок {plan['engine']} в сервисе пока не рисуется — готов vrm_desk"])
    est = estimate(store, episode_id, dry=dry_run)
    job = R.start(store, episode_id, {"dry_run": bool(dry_run)}, requested_by=requested_by, run=run)
    return {"episode_id": int(episode_id), "job": _job_brief(D.get_job(store, int(job["id"]))), "estimate": est}
