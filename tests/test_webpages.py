"""Страницы сайтов (app/webpages.py, app/webpages_ops.py, app/web_search.py): снимок локального сайта без интернета,
правки через HTML и пометка «чужая страница», капча, слой page (схема, ресурсы, размер), поиск с подменённым Gemini.

Снимок и правки идут настоящим headless Chromium (node/webpage/snapshot.mjs) по локальному HTTP-серверу
(tests/webpage_site.py); нет Chromium или Node — эти проверки пропускаются.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from app import motion
from app import projects as P
from app import webpages as W
from app import webpages_ops as O
from app.aspect import layer_size
from app.layer_lint import cover_share
from app.motion_render import resolve_layer_assets
from app.web_search import SearchError, parse_ddg, web_search
from test_api_pipeline import _client
from webpage_site import build_site, serve

BROWSER = bool(shutil.which("node")) and W.chromium_available()
needs_browser = pytest.mark.skipif(not BROWSER, reason="нет node или headless Chromium")


# --------------------------------------------------------------------------- #
# Снимок и правки настоящим браузером
# --------------------------------------------------------------------------- #


@needs_browser
def test_snapshot_edit_versions_and_foreign_note(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    project = P.create_project(store, "Витубика", kind="promo", links=[{"url": "https://vtubika.store"}])
    site = build_site(tmp_path / "site")
    with serve(site) as base:
        made = client.post("/api/web-pages", json={"url": f"{base}/index.html", "project_id": project["id"]})
        assert made.status_code == 200, made.text
        page = made.json()
        assert page["status"] == "ok" and page["current_version"] == 1
        assert page["title"] == "Витрина — тестовый магазин"
        assert page["own_site"] is False and page["foreign_edit"] is False
        root = W.page_dir(page["id"])
        html = (root / "v1" / "index.html").read_text(encoding="utf-8")
        # Копия: без скриптов и обработчиков, DOM — после скриптов, адреса — локальные.
        assert "<script" not in html.lower() and "onclick" not in html and 'rel="preload"' not in html
        assert "Блок дописан скриптом" in html
        assert "../assets/" in html and f"{base}/img/" not in html
        css = next((root / "assets").glob("*.css")).read_text(encoding="utf-8")
        assert "../img/bg.png" not in css and 'url("' in css  # фон из css — файлом рядом
        assert len(list((root / "assets").glob("*.png"))) >= 3  # картинка товара, ленивая, фон
        assert (root / "v1" / "page.png").is_file() and not list((root / "v1").glob("tile_*.png"))
        v1 = page["current"]
        assert v1["width"] == 390 * 3 and v1["page_width"] == 390 and v1["dpr"] == 3
        assert abs(v1["height"] - v1["shot_height"] * 3) <= 3
        prices = [o for o in page["outline"] if o["kind"] == "price"]
        assert prices and "1 990" in prices[0]["text"]
        assert any(o["kind"] == "button" and o["text"] == "Купить" for o in page["outline"])
        image = client.get(v1["image_url"])
        assert image.status_code == 200 and image.content[:4] == b"\x89PNG"
        copy = client.get(v1["html_url"])
        assert "sandbox" in copy.headers["content-security-policy"]

        # Правка списком: цена, баннер cookie, подсветка кнопки, окно по карточке товара.
        ops = [
            {"op": "set_text", "find": "1 990", "from": "1 990", "text": "1 690"},
            {"op": "remove", "selector": "#cookie"},
            {"op": "highlight", "selector": "#product > button", "kind": "pulse"},
            {"op": "insert_badge", "find": "Купить", "text": "−15%"},
            {"op": "crop_to", "selector": "#product", "pad": 10},
            {"op": "hide", "selector": "#nope"},
        ]
        edited = client.post(f"/api/web-pages/{page['id']}/edit", json={"ops": ops})
        assert edited.status_code == 200, edited.text
        out = edited.json()
        assert out["version"]["n"] == 2 and out["before"]["n"] == 1
        assert out["note"] == W.FOREIGN_NOTE and out["version"]["foreign_edit"] is True
        assert any("#nope" in p for p in out["problems"])
        region = out["version"]["region"]
        card = next(o for o in page["outline"] if o["sel"] == "#product > img")
        assert region["x"] <= card["box"][0] and region["y"] <= card["box"][1]
        v2 = (root / "v2" / "index.html").read_text(encoding="utf-8")
        assert "1 690" in v2 and "1 990" not in v2 and 'id="cookie"' not in v2
        assert "vvf-edit-style" in v2 and "vvf-badge" in v2 and "<script" not in v2.lower()
        assert "1 990" in (root / "v1" / "index.html").read_text(encoding="utf-8")  # старая версия на месте

        # Подсветка без смены текста — пометка остаётся (текст изменён раньше в этой цепочке).
        again = O.edit(store, page["id"], ops=[{"op": "add_highlight", "find": "Модель «Мафую»", "kind": "outline"}])
        assert again["version"]["n"] == 3 and again["note"] == W.FOREIGN_NOTE
        listed = client.get("/api/web-pages", params={"project_id": project["id"]}).json()["pages"]
        assert listed[0]["foreign_edit"] is True and listed[0]["note"] == W.FOREIGN_NOTE
        # От первой версии — чистая ветка без пометки.
        clean = O.edit(store, page["id"], ops=[{"op": "add_highlight", "selector": "#title"}], base=1)
        assert clean["note"] == "" and clean["version"]["parent_n"] == 1

        # Свой сайт (хост в ссылках проекта): смена цены без пометки.
        P.update_project(store, project["id"], links=[{"url": base}])
        own = W.snapshot(store, f"{base}/index.html", project["id"])
        assert own["own_site"] is True
        changed = O.edit(store, own["id"], ops=[{"op": "set_text", "from": "1 990", "text": "990"}])
        assert changed["note"] == "" and changed["version"]["foreign_edit"] is False

        # Капча — blocked, дальше не идём; правка отказывает.
        blocked = W.snapshot(store, f"{base}/captcha.html", project["id"])
        assert blocked["status"] == "blocked" and blocked["current_version"] == 0
        with pytest.raises(W.WebPageError):
            O.edit(store, blocked["id"], ops=[{"op": "remove", "selector": "h1"}])

    # Готовый слой: окно — crop_to, картинка находится рендером.
    layer = W.page_layer(store, page["id"], 2, duration=5)
    [norm] = motion.normalize_layers([layer])
    assert norm["props"]["region"]["y"] == region["y"] and norm["props"]["device"] == "phone"
    kept, assets, notes = resolve_layer_assets(store, [norm])
    assert kept and assets[f"webpage:{page['id']}:2"].endswith("page.png") and not notes


@needs_browser
def test_agent_tools_snapshot_and_edit(tmp_path, monkeypatch):
    from app.agent_tools import ToolContext, run_tool

    _client_, store = _client(tmp_path, monkeypatch)
    site = build_site(tmp_path / "site")
    ctx = ToolContext(store=store)
    with serve(site) as base:
        res, ok = run_tool(ctx, "page_snapshot", {"url": f"{base}/index.html"})
        assert ok, res
        assert res["layer"]["type"] == "page" and res["outline"]
        res2, ok2 = run_tool(ctx, "page_edit", {"page_id": res["page_id"], "ops": [{"op": "remove", "selector": "#cookie"}]})
        assert ok2 and res2["version"] == 2 and "note" not in res2  # без смены текста пометки нет
        view, ok3 = run_tool(ctx, "view_page", {"page_id": res["page_id"], "y": 200})
        assert ok3 and view.mime_type == "image/jpeg"
        listed, _ = run_tool(ctx, "list_pages", {"page_id": res["page_id"]})
        assert [h["version"] for h in listed["history"]] == [1, 2]


# --------------------------------------------------------------------------- #
# Правки: проверка и Gemini (подменён)
# --------------------------------------------------------------------------- #


def test_validate_ops_aliases_and_safety():
    clean, problems = O.validate_ops([
        {"op": "highlight", "selector": ".buy", "color": "red"},
        {"op": "style", "selector": "h1", "style": {"color": "#fff", "background": "url(http://x)", "Bad Prop": "1"}},
        {"op": "remove", "selector": "body"},
        {"op": "badge", "find": "Купить", "text": "Хит", "position": "middle"},
        {"op": "crop", "selector": "#price", "all": True},
        {"op": "set_text", "from": "1 990", "text": "1 690"},
        {"op": "explode", "selector": "h1"},
    ])
    kinds = [c["op"] for c in clean]
    assert kinds == ["add_highlight", "set_style", "insert_badge", "crop_to", "set_text"]
    assert clean[0]["color"] == "#ffd400" and clean[0]["kind"] == "glow"  # не hex — свой цвет
    assert clean[1]["style"] == {"color": "#fff"}
    assert clean[2]["position"] == "top-right" and "all" not in clean[3]
    assert any("url" in p or "background" in p for p in problems)
    assert any("всю страницу" in p for p in problems) and any("explode" in p for p in problems)
    with pytest.raises(O.OpsError):
        O.validate_ops([{"op": "hide"}])
    with pytest.raises(O.OpsError):
        O.validate_ops([])


class _FakeGemini:
    def __init__(self, text: str | None = None, error: Exception | None = None, grounding: list | None = None):
        self.text, self.error, self.grounding, self.calls = text, error, grounding, []

    def generate_content(self, model, payload):
        self.calls.append(payload)
        if self.error:
            raise self.error
        candidate = {"content": {"parts": [{"text": self.text or ""}]}}
        if self.grounding:
            candidate["groundingMetadata"] = {"groundingChunks": [{"web": g} for g in self.grounding]}
        return {"candidates": [candidate]}


def test_instruction_to_ops_with_mocked_gemini():
    outline = [{"kind": "button", "sel": "#product > button", "text": "Купить", "box": [34, 675, 322, 52]}]
    fake = _FakeGemini(json.dumps({"ops": [{"op": "add_highlight", "selector": "#product > button", "kind": "pulse"}],
                                   "comment": "подсветил"}, ensure_ascii=False))
    got = O.instruction_to_ops("подсвети кнопку Купить", outline, "Витрина", "https://x", 390, client=fake)
    assert got["ops"][0]["selector"] == "#product > button" and got["comment"] == "подсветил"
    prompt = fake.calls[0]["contents"][0]["parts"][0]["text"]
    assert "#product > button" in prompt and "подсвети кнопку Купить" in prompt
    with pytest.raises(O.OpsError, match="списком ops"):
        O.instruction_to_ops("подсвети", outline, client=_FakeGemini(error=RuntimeError("429 quota")))
    with pytest.raises(O.OpsError, match="не нашёл"):
        O.instruction_to_ops("подсвети", outline, client=_FakeGemini(json.dumps({"ops": [], "comment": "нет кнопки"})))


def test_own_hosts_from_project_links(tmp_path, monkeypatch):
    _c, store = _client(tmp_path, monkeypatch)
    project = P.create_project(store, "Витубика", links=["vtubika.store", {"url": "https://www.youtube.com/@vtubika"}])
    owned = W.own_hosts(store, project["id"])
    assert owned == ["vtubika.store", "youtube.com"]
    assert W.is_own_host("shop.vtubika.store", owned) and W.is_own_host("www.vtubika.store", owned)
    assert not W.is_own_host("vtubika.store.evil.com", owned) and not W.is_own_host("ozon.ru", owned)
    assert W.own_hosts(store, None) == []
    with pytest.raises(ValueError):
        W.snapshot(store, "ftp://example.com")
    with pytest.raises(ValueError):
        W.snapshot(store, "https://user:pass@example.com/")


# --------------------------------------------------------------------------- #
# Слой page
# --------------------------------------------------------------------------- #


def test_page_layer_normalize_refs_and_size():
    [layer] = motion.normalize_layers([{"type": "webpage", "start": 0, "end": 5, "props": {
        "page_id": 7, "version": 2, "pageWidth": 390, "region": {"x": 0, "y": 600, "w": 390, "h": 844},
        "scroll": [{"t": 0.5, "y": 600}, {"t": 4, "y": 1400, "ease": "out"}], "zoom": [{"t": 3, "z": 9}],
        "taps": [{"t": 4.2, "x": 195, "y": 1900}], "device": "phone"}}])
    p = layer["props"]
    assert layer["type"] == "page" and p["w"] == 560 and p["zoom"][0]["z"] == 6  # приближение зажато
    assert p["scroll"][1] == {"t": 4, "y": 1400, "ease": "out"} and "crop" not in p
    assert motion.layer_asset_refs(layer) == ["webpage:7:2"]
    w, h = layer_size(layer)
    assert round(w) == round(560 * 1.094) and h > w * 2
    assert cover_share(layer) > 0.2
    problems = motion.validate_layers([{"type": "page", "props": {}}], ref_exists=lambda ref: False)
    assert any("page_id" in x for x in problems)
    assert not motion.validate_layers([layer], ref_exists=lambda ref: False)  # снимок — не пакет


def test_page_layer_missing_snapshot_is_skipped(tmp_path, monkeypatch):
    _c, store = _client(tmp_path, monkeypatch)
    [layer] = motion.normalize_layers([{"type": "page", "start": 0, "end": 3, "props": {"page_id": 999}}])
    kept, assets, notes = resolve_layer_assets(store, [layer])
    assert not kept and not assets and "webpage:999:1" in notes[0]


# --------------------------------------------------------------------------- #
# Поиск
# --------------------------------------------------------------------------- #

DDG_PAGE = """
<div class="result results_links results_links_deep web-result ">
 <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fvtubika.store%2F&amp;rut=abc">Витубика — <b>модели</b></a>
 <a class="result__snippet" href="#">Магазин моделей для витуберов.</a>
</div>
<div class="result result--ad "><a class="result__a" href="https://ads.example">Реклама</a></div>
<div class="result results_links"><a class="result__a" href="https://example.org/x">Второй</a></div>
"""


class _Resp:
    def __init__(self, status: int, text: str):
        self.status_code, self.text = status, text


def test_web_search_gemini_then_duckduckgo(monkeypatch):
    from app import proxy_config

    monkeypatch.setattr(proxy_config, "download_proxy", lambda: "")
    fake = _FakeGemini(json.dumps({"results": [
        {"title": "Витубика", "url": "https://vtubika.store/", "snippet": "магазин"},
        {"title": "дубль", "url": "https://vtubika.store/"}, {"title": "не ссылка", "url": "vtubika"}]}))
    got = web_search("витубика официальный сайт", 5, client=fake)
    assert got["engine"] == "gemini" and [r["url"] for r in got["results"]] == ["https://vtubika.store/"]
    assert fake.calls[0]["tools"] == [{"google_search": {}}]
    # Без JSON — источники поиска из groundingMetadata.
    grounded = web_search("витубика", 5, client=_FakeGemini("не json", grounding=[{"title": "vtubika.store", "uri": "https://vtubika.store/"}]))
    assert grounded["results"][0]["url"] == "https://vtubika.store/"
    # Gemini упал — DuckDuckGo; реклама выдачи пропущена, адрес из редиректа раскрыт.
    seen = {}

    def fetch(url, **kwargs):
        seen.update(kwargs)
        return _Resp(200, DDG_PAGE)

    fallback = web_search("витубика", 5, client=_FakeGemini(error=RuntimeError("429 Too Many Requests")), fetch=fetch)
    assert fallback["engine"] == "duckduckgo" and "429" in fallback["note"]
    assert [r["url"] for r in fallback["results"]] == ["https://vtubika.store/", "https://example.org/x"]
    assert fallback["results"][0]["title"] == "Витубика — модели" and "proxy" not in seen
    with pytest.raises(SearchError, match="поиск не удался"):
        web_search("витубика", 5, client=_FakeGemini(error=RuntimeError("blocked")), fetch=lambda url, **kw: _Resp(503, ""))
    assert parse_ddg("<html>ничего</html>", 5) == []


def test_web_search_never_uses_paid_gateway():
    source = (Path(__file__).parent.parent / "app" / "web_search.py").read_text(encoding="utf-8")
    code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith(("#", '"', "Платный")))
    assert "polza" not in code.lower()
