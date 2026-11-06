"""Истории: оценка, заказ и готовность генерации (из ``app/stories_gen.py``)."""

from __future__ import annotations

from typing import Any

from app import aivideo_gen as G
from app import autopilot as A
from app import explainer_gen as X
from app import projects as P
from app import stories as S
from app.settings import settings

# --------------------------------------------------------------------------- #
# 3–4. Деньги и генерация
# --------------------------------------------------------------------------- #


def estimate(store: Any, episode_id: int) -> dict:
    """Что ещё нужно эпизоду и сколько стоит (бесплатно, ничего не запускает)."""
    ep = S.get_episode(store, episode_id)
    est = G.estimate(store, int(ep["project_id"]), S.STAGE, None, [S.episode_group(episode_id)])
    kinds: dict[str, list[dict]] = {}
    for item in est["items"]:
        kinds.setdefault(item["ref_kind"], []).append(item)
    scenes = kinds.get(S.SCENE_KIND, [])
    tts = kinds.get(S.NARRATION_KIND, [])
    used = set(S.beat_scene_ids(ep))
    images = [i for i in est["items"] if i["kind"] == "image"]
    return {"episode_id": int(episode_id), "total_rub": est["total_rub"], "paid": est["paid"],
            "beats": len(ep.get("beats") or []), "scenes_used": len(used), "new_scenes": len(scenes),
            "reused_scenes": max(0, len(used) - len(scenes)), "sheets": len(kinds.get(S.SHEET_KIND, [])),
            "plates": len(kinds.get(S.PLATE_KIND, [])), "style_frames": len(kinds.get(S.STYLE_KIND, [])),
            "images": len(images), "images_rub": round(sum(i["estimate_rub"] for i in images), 2),
            "narration_rub": round(sum(i["estimate_rub"] for i in tts), 2), "narration_ready": not tts,
            "cap_rub": float(settings.stories_episode_cap_rub),
            "items": [{k: i.get(k) for k in ("label", "kind", "ref_kind", "provider", "model", "estimate_rub",
                                             "price_note")} for i in est["items"]],
            "problems": est["problems"], "budget": est["budget"], "note": est["note"]}


def request_generation(store: Any, episode_id: int, requested_by: str = "stories") -> dict:
    """Бесплатное (Gemini TTS, сухой прогон) — сразу; платное — одна карточка владельцу."""
    ep = S.get_episode(store, episode_id)
    pid = int(ep["project_id"])
    group = S.episode_group(episode_id)
    narr = G.estimate(store, pid, S.STAGE, None, [f"{group}:{S.NARRATION_KIND}"])
    if narr["items"] and not narr["paid"]:
        G.request_stage(store, pid, S.STAGE, None, [f"{group}:{S.NARRATION_KIND}"], requested_by=requested_by)
        job = X._target_jobs(store, pid, S.T_EP, int(episode_id), S.NARRATION_KIND)
        if job and job["status"] in X.BAD_JOB and S.narration_ref(store, S.get_episode(store, episode_id)) is None:
            why = " ".join(str(job.get("error") or job["status"]).split())[:300].replace(" — ", ": ")
            error = (f"Озвучка не получилась ({why}). Эпизод без голоса не выпускаю — проверьте, доступен ли Gemini, "
                     "и нажмите «Продолжить»")
            S.set_status(store, episode_id, "failed", ("planned",), error=error)
            return {"status": "failed", "error": error}
    est = estimate(store, episode_id)
    if not est["items"]:
        S.set_status(store, episode_id, "generating", ("planned",), estimate_rub=0.0, approval_id=None)
        return {"status": "ready", "estimate": est}
    note = f"завязка: {P._text(ep['premise'], 200)}"
    try:
        req = G.request_stage(store, pid, S.STAGE, None, [group], requested_by=requested_by, note=note)
    except G.BudgetError as exc:
        S.set_status(store, episode_id, "failed", ("planned",), error=str(exc)[:600])
        return {"status": "failed", "error": str(exc), "estimate": est}
    if req["status"] in ("awaiting_approval", "approved"):
        S.set_status(store, episode_id, "awaiting_approval", ("planned",), approval_id=req["approval_id"],
                     estimate_rub=est["total_rub"])
        return {"status": "awaiting_approval", "approval_id": req["approval_id"], "estimate": est, "note": req["note"]}
    S.set_status(store, episode_id, "generating", ("planned",), estimate_rub=0.0, approval_id=None)
    return {"status": "generating", "estimate": est, "run": req.get("run")}


def readiness(store: Any, episode_id: int) -> dict:
    """Готово ли всё к сборке: озвучка и картинка у каждой сцены битов; что упало."""
    ep = S.get_episode(store, episode_id)
    pid = int(ep["project_id"])
    missing, failed, active, held = [], [], [], []
    checks = []
    if S.narration_ref(store, ep) is None:
        checks.append((S.T_EP, int(episode_id), S.NARRATION_KIND, "озвучка"))
    for sid in sorted(set(S.beat_scene_ids(ep))):
        scene = S.get_scene(store, sid)
        if not S.scene_usable(scene, S.is_dry(ep)):
            checks.append((S.T_SCENE, sid, S.SCENE_KIND, f"сцена «{P._text(scene['title'], 40)}»"))
    for target_type, target_id, kind, label in checks:
        job = X._target_jobs(store, pid, target_type, target_id, kind)
        missing.append(label)
        if job is None:
            continue
        if job["status"] in ("failed", "blocked_policy", "uncertain", "cancelled"):
            failed.append(f"{label}: {job['status']} — {(job.get('error') or '')[:160]}")
        elif job["status"] == "held":
            held.append(f"{label}: {(job.get('error') or 'держится')[:160]}")
        elif job["status"] in ("approved", "submitting", "polling", "recovering", "awaiting"):
            active.append(label)
    return {"ready": not missing, "missing": missing, "failed": failed, "active": active, "held": held}


def check_generation(store: Any, episode_id: int) -> str:
    """awaiting_approval/generating → дальше. «wait» — ждём владельца или запуски."""
    ep = S.get_episode(store, episode_id)
    if ep["status"] == "awaiting_approval" and ep.get("approval_id"):
        card = A.get_approval(store, int(ep["approval_id"]))
        if card["status"] == "pending":
            return "wait"
        if card["status"] != "approved":
            S.set_status(store, episode_id, "cancelled", ("awaiting_approval",),
                         error=f"карточка #{card['id']}: {card['status']}"
                               + (f" — {card.get('decision_comment')}" if card.get("decision_comment") else ""))
            return "cancelled"
        S.set_status(store, episode_id, "generating", ("awaiting_approval",))
    state = readiness(store, episode_id)
    if state["ready"]:
        S.set_status(store, episode_id, "assembling", ("generating", "awaiting_approval"))
        return "ready"
    if state["active"]:
        return "wait"
    if state["held"] and not state["failed"]:
        S.update_episode(store, episode_id, error="ждёт запуска: " + "; ".join(state["held"][:2])
                         + " — включите генерацию и нажмите «Продолжить» у карточки")
        return "wait"
    if state["failed"]:
        S.set_status(store, episode_id, "failed", ("generating", "awaiting_approval"),
                     error="генерация не удалась: " + "; ".join(state["failed"])[:600])
        return "failed"
    S.set_status(store, episode_id, "planned", ("generating", "awaiting_approval"))
    return "replan"
