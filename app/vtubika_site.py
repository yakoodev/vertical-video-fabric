"""Настоящие скриншоты сайта магазина во вставке «покупка на телефоне» (истории «Витубики»).

Снимки — ``node/vrm/site_shots.mjs`` (headless Chromium, мобильный экран 390×844 @3, только просмотр: загрузка
страницы и прокрутка), лежат в ``D:/vvf-offload/vtubika/site/<дата>/``. План берёт их полем ``site_shots``::

    "site_shots": {"dir": "D:/vvf-offload/vtubika/site/2026-11-08",
                   "catalog": {"file": "catalog_3d.png", "scroll": 2620, "tap": [288, 3030]},
                   "product": {"file": "product_mafuyu.png", "scroll": 800, "tap": [195, 1505],
                               "button": [16, 1479, 358, 52]}}

Координаты — CSS px страницы (как в JSON снимка: ``--find``). Есть снимки — телефон «shop» плана становится
экраном «site» (node/scene3d/src/ui_site.ts): каталог листается, палец открывает товар, страница листается до цены
и кнопки, касание — и наши экраны «Оплачено» / «Модель пришла» в стиле сайта. Снимков нет (или файла нет) —
телефон остаётся «shop» с нейтральной плиткой вместо превью (своего персонажа за настоящий товар не выдаём).

Шаги вставки — события плана ``{"at": "<слово>", "site_step": "product" | "tap" | "delivered"}``; «Оплачено» —
обычное ``"paid": true`` (время в ``store.paidAt``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

PAGES = ("catalog", "product")
STEPS = ("product", "tap", "delivered")


def shots(plan: dict) -> dict | None:
    """Снимки сайта плана: {dir, catalog: {url, scroll, tap}, product: {…}} или None (нет поля или файлов)."""
    raw = plan.get("site_shots")
    if not isinstance(raw, dict) or not raw.get("dir"):
        return None
    base = Path(raw["dir"])
    out: dict[str, Any] = {"dir": str(base)}
    for name in PAGES:
        page = raw.get(name)
        if not isinstance(page, dict) or not page.get("file") or not (base / page["file"]).is_file():
            return None
        item = {"url": f"/site/{page['file']}", "scroll": float(page.get("scroll", 0)), "from": float(page.get("from", 0)),
                "tap": [float(v) for v in page.get("tap", [195, 400])]}
        if page.get("button"):
            item["button"] = [float(v) for v in page["button"]]
        out[name] = item
    return out


def apply(overlays: list[dict], plan: dict) -> dict | None:
    """Телефоны «shop» → «site» на снимках сайта (если они есть); вернуть снимки (для ``--site`` рендера)."""
    site = shots(plan)
    if not site:
        return None
    used = False
    for o in overlays:
        if o.get("kind") != "phone" or o.get("screen") != "shop":
            continue
        store = o.get("store") or {}
        steps = list(o.pop("steps", []) or [])
        if store.get("paidAt") is not None:
            steps.append({"t": float(store["paidAt"]), "step": "paid"})
        msg = o.get("message") or {}
        o["screen"] = "site"
        o["site"] = {"catalog": site["catalog"], "product": site["product"], "steps": sorted(steps, key=lambda s: s["t"]),
                     "title": store.get("title"), "price": store.get("price"),
                     "file": msg.get("file", "model_vrm.zip"), "size": msg.get("size", "64 МБ")}
        used = True
    return site if used else None
