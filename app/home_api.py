"""REST «Главной» (app/home.py): одна ручка со сводкой всего сервиса — что ждёт владельца, свежие работы, что скоро
выйдет, что зашло, что работает, траты месяца, проекты и примеры. Упавший источник не валит ответ: у блока ``error``."""

# Без from __future__ import annotations: FastAPI читает аннотации вида _auth: auth_dep в момент объявления.
from typing import Any, Callable

from fastapi import APIRouter

from app import home


def build_home_router(auth_dep: Any, get_store: Callable[[], Any]) -> APIRouter:
    router = APIRouter()

    @router.get("/api/home", tags=["Projects"], summary="Главная: сводка сервиса одним ответом, блоки независимы")
    def api_home(_auth: auth_dep) -> dict:
        return home.summary(get_store())

    return router
