"""Плоский список маршрутов приложения — с FastAPI ≥ 0.150 подключённые роутеры лежат в
``app.routes`` обёртками ``_IncludedRouter`` (свои маршруты — в ``original_router.routes``)."""


def flat_routes(routes):
    out = []
    for route in routes:
        inner = getattr(route, "original_router", None)
        if inner is not None and not getattr(route, "path", ""):
            out.extend(flat_routes(inner.routes))
        else:
            out.append(route)
    return out
