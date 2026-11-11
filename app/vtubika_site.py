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


def _pages(raw: dict) -> dict | None:
    """Каталог и товар из описания {dir, catalog, product}; нет файла на диске — None."""
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


def shots(plan: dict) -> dict | None:
    """Снимки сайта плана: {dir, catalog: {url, scroll, tap}, product: {…}} или None (нет поля или файлов).
    Дополнительные покупки истории — ``site_shots.sets``: {имя: {catalog, product}} (каталог и товар другого раздела,
    файлы — в той же папке ``dir``); в ответе они лежат под ``sets``, недостающего файла быть не должно."""
    raw = plan.get("site_shots")
    if not isinstance(raw, dict) or not raw.get("dir"):
        return None
    out = _pages(raw)
    if out is None:
        return None
    sets: dict[str, Any] = {}
    for name, extra in (raw.get("sets") or {}).items():
        got = _pages({**extra, "dir": raw["dir"]})  # рендер отдаёт одну папку снимков (--site): набор — в ней же
        if got is None:
            return None
        sets[name] = got
    if sets:
        out["sets"] = sets
    return out


def apply(overlays: list[dict], plan: dict) -> dict | None:
    """Телефоны «shop» → «site» на снимках сайта (если они есть); вернуть снимки (для ``--site`` рендера).

    Телефон выбирает набор снимков полем ``siteSet`` (имя из ``site_shots.sets``; нет поля — основной набор), а на
    экране «Скачать» подписывает покупку полем ``store.kind`` («Фон», «Попкорн» …) вместо «VRM» и заголовком
    ``store.delivered`` вместо «Модель пришла»."""
    site = shots(plan)
    if not site:
        return None
    used: list[str] = []
    for o in overlays:
        if o.get("kind") != "phone" or o.get("screen") != "shop":
            continue
        store = o.get("store") or {}
        which = o.pop("siteSet", None)
        pages = site if which is None else (site.get("sets") or {}).get(which)
        if pages is None:
            raise ValueError(f"нет набора снимков сайта «{which}» в site_shots.sets")
        steps = list(o.pop("steps", []) or [])
        if store.get("paidAt") is not None:
            steps.append({"t": float(store["paidAt"]), "step": "paid"})
        msg = o.get("message") or {}
        o["screen"] = "site"
        o["site"] = {"catalog": pages["catalog"], "product": pages["product"], "steps": sorted(steps, key=lambda s: s["t"]),
                     "title": store.get("title"), "price": store.get("price"),
                     "file": msg.get("file", "model_vrm.zip"), "size": msg.get("size", "64 МБ")}
        for key, dst in (("kind", "fileKind"), ("delivered", "deliveredTitle")):
            if store.get(key):
                o["site"][dst] = store[key]
        used += [pages["catalog"]["url"], pages["product"]["url"]]
    if not used:
        return None
    # Снимки для рендера (--site, загрузка страниц): только реально использованные, без повторов.
    site["urls"] = list(dict.fromkeys(used))
    return site
