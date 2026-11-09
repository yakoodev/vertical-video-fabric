"""Статистика публикаций: что заходит, снимки по площадкам, предупреждение о нуле просмотров, сбор по запросу.

Роутер собирается функцией, как ``app/cuts_api.py``: ``main.py`` передаёт зависимость авторизации и доступ к хранилищу
и сервисам лямбдами (тесты подменяют ``app.main.store`` и другие имена)."""

# Без from __future__ import annotations: FastAPI читает аннотации вида
# _auth: auth_dep в момент объявления, а auth_dep — локальная переменная.
from typing import Any, Callable

from fastapi import APIRouter, Body, HTTPException

from app import platform_stats


def build_stats_router(auth_dep: Any, get_store: Callable[[], Any]) -> APIRouter:
    router = APIRouter()

    @router.get("/api/stats/clips", tags=["Jobs"], summary="What works: published clips with stats and montage features")
    def api_stats_clips(_auth: auth_dep) -> dict:
        return {"clips": platform_stats.clip_performance(get_store()), "status": platform_stats.status()}

    @router.get("/api/stats/publications", tags=["Jobs"], summary="Latest platform stats per publication target")
    def api_stats_publications(_auth: auth_dep) -> dict:
        from app import zero_views

        latest = get_store().latest_publication_stats()
        # alerts — тревоги «0 просмотров» по публикациям, clips — значок «Статистика» клипа (app/zero_views.py).
        return {"targets": {str(k): v for k, v in latest.items()},
                "alerts": {str(k): v for k, v in zero_views.alerts_by_target(get_store()).items()},
                "clips": {str(k): v for k, v in zero_views.clip_badges(get_store()).items()},
                "status": platform_stats.status()}

    @router.get("/api/stats/zero-views", tags=["Jobs"], summary="Zero-views alert settings (hours after publish, threshold)")
    def api_zero_views_settings(_auth: auth_dep) -> dict:
        from app import zero_views

        return zero_views.get_settings(get_store())

    @router.put("/api/stats/zero-views", tags=["Jobs"], summary="Change zero-views alert settings")
    def api_zero_views_save(_auth: auth_dep, payload: dict = Body(...)) -> dict:
        from app import zero_views

        try:
            return zero_views.save_settings(get_store(), payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/stats/publications/{target_id}", tags=["Jobs"], summary="Stats snapshots of one publication target")
    def api_stats_publication_history(target_id: int, _auth: auth_dep) -> list[dict]:
        return get_store().list_publication_stats(target_id)

    @router.post("/api/stats/refresh", tags=["Jobs"], summary="Collect platform stats now (background, one link at a time)")
    def api_stats_refresh(_auth: auth_dep) -> dict:
        return platform_stats.start_collect(get_store(), force=True)

    # --- TikTok Studio: удержание, досмотры, источники (app/tiktok_studio.py) --- #

    @router.get("/api/tiktok-studio/status", tags=["Jobs"], summary="TikTok Studio analytics: collector status")
    def api_tiktok_studio_status(_auth: auth_dep) -> dict:
        from app import tiktok_studio_collect as C

        store = get_store()
        accounts = C.tiktok_accounts(store)
        items, unmapped = C.candidates(store, accounts)
        return {**C.status(), "accounts": list(accounts.values()), "videos": len(items), "unmapped": unmapped}

    @router.post("/api/tiktok-studio/collect", tags=["Jobs"],
                 summary="Collect TikTok Studio analytics now (background; optional account_id / video_id)")
    def api_tiktok_studio_collect(_auth: auth_dep, payload: dict = Body(default={})) -> dict:
        from app import tiktok_studio_collect as C

        account_id = payload.get("account_id")
        try:
            account_id = int(account_id) if account_id not in (None, "") else None
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="account_id — число") from exc
        video_id = str(payload.get("video_id") or "").strip()
        if video_id and not video_id.isdigit():
            raise HTTPException(status_code=400, detail="video_id — числовой id ролика TikTok")
        return C.start_collect(get_store(), account_id=account_id, video_id=video_id,
                               force=bool(payload.get("force", True)))

    @router.get("/api/tiktok-studio/publications", tags=["Jobs"],
                summary="Latest TikTok Studio summary per publication (service:<target_id>, manual:<publication_id>)")
    def api_tiktok_studio_publications(_auth: auth_dep) -> dict:
        from app import tiktok_studio as S

        return {f"{kind}:{ref}": brief for (kind, ref), brief in S.briefs_by_ref(get_store()).items()}

    return router
