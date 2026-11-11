"""Разбор снимка страницы на слои (app/webpage_layers.py, app/webpage_layers_clip.py, node/webpage/layers.mjs).

Настоящий разбор — headless Chromium по копии локальной витрины (tests/webpage_site.py, страница layers.html):
находятся ожидаемые блоки, фон + блоки на своих местах совпадают с полным кадром, простой текст описан стилем и
шрифтом, слои проходят нормализацию моушна, API дописывает их в клип, инструмент агента — тоже. Нет Chromium
или Node — эти проверки пропускаются. Остальное (раскладка, имена, шрифты, вставка по API) — на подменённом
браузере: разбор отдаёт заранее нарисованные кадры.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest

from app import motion
from app import webpage_layers as L
from app import webpage_layers_clip as LC
from app import webpages as W
from test_api_pipeline import _client, _require_ffmpeg
from webpage_site import build_site, serve

BROWSER = bool(shutil.which("node")) and W.chromium_available()
needs_browser = pytest.mark.skipif(not BROWSER, reason="нет node или headless Chromium")


def _canvas(monkeypatch, store, duration=8):
    import app.canvas as canvas

    monkeypatch.setattr(canvas, "CANVAS_WIDTH", 108)
    monkeypatch.setattr(canvas, "CANVAS_HEIGHT", 192)
    return canvas.create_canvas_clip(store, duration, "grid", "Реклама витрины")


# --------------------------------------------------------------------------- #
# Настоящий браузер: один снимок и один разбор на весь сценарий (памяти мало)
# --------------------------------------------------------------------------- #


@needs_browser
def test_decompose_recompose_text_and_insert(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, store = _client(tmp_path, monkeypatch)
    site = build_site(tmp_path / "site")
    with serve(site) as base:
        page = W.snapshot(store, f"{base}/layers.html")
    assert page["status"] == "ok"

    resp = client.post(f"/api/web-pages/{page['id']}/blocks", json={"region": "page"})
    assert resp.status_code == 200, resp.text
    view = resp.json()
    blocks = {b["name"]: b for b in view["blocks"]}
    names = set(blocks)
    # Нашлись ожидаемые блоки — с разговорными именами.
    for want in ("Шапка сайта", "Заголовок: Модели для витубера", "Текст: Готовые аватары за один вечер",
                 "Кнопка: Смотреть каталог", "Карточка: Ferret", "Карточка: Мафую", "Цена: 1 290 ₽", "Цена: 1 990 ₽",
                 "Значок: Хит", "Картинка: Ferret", "Заголовок: Ferret"):
        assert want in names, (want, sorted(names))
    kinds = {b["kind"] for b in view["blocks"]}
    assert {"header", "section", "heading", "text", "button", "card", "price", "badge", "image"} <= kinds
    # Иерархия: цена, значок и картинка Ferret — дети своей карточки; блоки не дублируются.
    card = blocks["Карточка: Ferret"]
    assert blocks["Цена: 1 290 ₽"]["parent"] == card["id"] and blocks["Значок: Хит"]["parent"] == card["id"]
    assert blocks["Картинка: Ferret"]["parent"] == card["id"] and card["parent"] is None
    assert len([b for b in view["blocks"] if b["name"] == "Кнопка: Купить"]) == 2
    assert all(client.get(b["url"]).content[:4] == b"\x89PNG" for b in view["blocks"][:3])
    assert client.get(view["plate_url"]).status_code == 200

    # Кадр блока прозрачный вокруг, свой фон и скругление на месте; фон + блоки = полный кадр.
    dec = L.decompose(store, page["id"], region="page")
    folder = L.layers_dir(page["id"], dec["version"], dec["key"])
    price = next(b for b in dec["blocks"] if b["name"] == "Цена: 1 290 ₽")
    cardb = next(b for b in dec["blocks"] if b["name"] == "Карточка: Ferret")
    card_png = cv2.imread(str(folder / cardb["file"]), cv2.IMREAD_UNCHANGED)
    assert card_png.shape[2] == 4 and card_png[0, 0, 3] < 40  # угол: скругление и тень полупрозрачны
    h, w = card_png.shape[:2]
    assert card_png[h // 2, w // 2, 3] == 255  # середина: белая подложка карточки
    price_png = cv2.imread(str(folder / price["file"]), cv2.IMREAD_UNCHANGED)
    assert price_png[:, :, 3].min() == 0 and price_png[:, :, 3].max() == 255  # текст на прозрачном
    full = cv2.imread(str(folder / dec["full"]), cv2.IMREAD_COLOR)
    comp = L.recompose(page["id"], dec)
    assert comp.shape == full.shape
    diff = np.abs(comp.astype(np.int16) - full.astype(np.int16))
    assert diff.mean() < 1.0 and np.percentile(diff, 99.9) <= 12, (diff.mean(), np.percentile(diff, 99.9))
    plate = cv2.imread(str(folder / dec["plate"]), cv2.IMREAD_COLOR)
    assert np.abs(plate.astype(np.int16) - full.astype(np.int16)).mean() > 5  # без блоков фон другой
    # Второй разбор с теми же параметрами — из кэша, без браузера.
    assert L.decompose(store, page["id"], region="page", runner=lambda *_a: pytest.fail("браузер не нужен")) == dec

    # Простой текст: стиль из computed style, шрифт — подключённый страницей.
    title = next(b for b in dec["blocks"] if b["name"] == "Заголовок: Модели для витубера")
    st = title["text_style"]
    assert st["family"] == "Vitrina Display" and st["web_font"] and st["size"] == 40 and st["weight"] == 700
    assert st["color"][:3] == [255, 255, 255] and st["text"].replace("\n", " ") == "Модели для витубера"
    sub = next(b for b in dec["blocks"] if b["name"] == "Текст: Готовые аватары за один вечер")
    assert L.text_font(page["id"], sub, dec["font_faces"])["source"] == "library"  # Montserrat — в библиотеке
    assert L.text_font(page["id"], title, dec["font_faces"])["source"] == "page"
    assert not L.text_font(page["id"], price, dec["font_faces"])["ok"]  # системный Arial — картинкой
    assert blocks["Заголовок: Модели для витубера"]["can_text"] and not blocks["Цена: 1 290 ₽"]["can_text"]

    # Слои: проходят нормализацию; живой текст — со шрифтом страницы, зарегистрированным своим.
    clip = _canvas(monkeypatch, store)
    built = LC.build_layers(store, page["id"], dec, blocks=None, as_text=[title["id"], sub["id"]], at_sec=1.0)
    layers = built["layers"]
    assert layers[0]["name"] == "Фон страницы" and len(layers) == len(dec["blocks"]) + 1
    norm = motion.normalize_layers(json.loads(json.dumps(layers)))
    assert len(norm) == len(layers) and not motion.validate_layers(norm, ref_exists=lambda ref: True)
    text = next(x for x in norm if x["name"] == "Заголовок: Модели для витубера")
    assert text["type"] == "text" and text["props"]["font"] == "Vitrina Display"
    assert abs(text["props"]["size"] - 40 * built["scale"]) < 0.2
    assert next(x for x in norm if x["name"] == "Текст: Готовые аватары за один вечер")["props"]["font"] == "Montserrat"
    from app.motion_assets import list_fonts

    assert any(f["family"] == "Vitrina Display" and f.get("user") for f in list_fonts())
    starts = [x["start"] for x in norm[1:]]
    assert norm[0]["start"] == 1.0 and min(starts) > 1.0 and max(starts) - min(starts) >= 0.08 * 5
    by_name = {x["name"]: x for x in norm}
    assert by_name["Цена: 1 290 ₽"]["start"] > by_name["Карточка: Ferret"]["start"]  # ребёнок после карточки
    assert by_name["Цена: 1 290 ₽"]["in"]["type"] == "pop"

    # API: группа слоёв в клипе (фон и блоки подряд, общий id-префикс), картинки — файлы монтажа.
    resp = client.post(f"/api/web-pages/{page['id']}/to-layers",
                       json={"clip_id": clip["id"], "region": "page", "blocks": ["card", "price"], "layout": "fit_width",
                             "at_sec": 0.5, "stagger": 0.1})
    assert resp.status_code == 200, resp.text
    out = resp.json()
    saved = store.get_clip_plan(clip["id"])["render_settings"]["layers"]
    ours = [x for x in saved if str(x["id"]).startswith(f"wp{page['id']}_")]
    assert len(ours) == len(out["inserted"]) == 1 + 2 + 2  # фон, 2 карточки, 2 цены
    prefix = {x["id"].rsplit("_", 1)[0] for x in ours}
    assert len(prefix) == 1 and ours[0]["name"] == "Фон страницы"
    asset = store.get_montage_asset(int(ours[1]["props"]["asset_id"]))
    assert asset["mime_type"] == "image/png" and asset["width"] > 100
    # Повторная вставка тех же блоков не плодит файлы.
    again = LC.page_to_layers(store, page["id"], None, clip["id"], "page", blocks=["card"], insert=False)
    assert {x["props"]["asset_id"] for x in again["layers"]} <= {x["props"]["asset_id"] for x in saved}

    # Инструмент агента: тот же разбор, слои в клипе контекста.
    from app.agent_tools import ToolContext, run_tool

    res, ok = run_tool(ToolContext(store=store, clip_plan_id=clip["id"]), "page_to_layers",
                       {"page_id": page["id"], "region": "page", "blocks": ["b0", "button"], "layout": "as_page"})
    assert ok, res
    assert res["blocks"][0]["kind"] == "plate" and {b["kind"] for b in res["blocks"][1:]} == {"header", "button"}
    assert "update_layer" in res["hint"]


# --------------------------------------------------------------------------- #
# Подменённый браузер: кадры рисуем сами
# --------------------------------------------------------------------------- #


def _fake_page(store) -> int:
    cur = store.db.execute("INSERT INTO web_pages(url, host, title, viewport, status, current_version) "
                           "VALUES ('https://shop.test/', 'shop.test', 'Магазин', 'mobile', 'ok', 1)")
    page_id = int(cur.lastrowid)
    vdir = W.page_dir(page_id) / "v1"
    vdir.mkdir(parents=True)
    (vdir / "index.html").write_text("<!doctype html><title>x</title>", encoding="utf-8")
    cv2.imwrite(str(vdir / "page.png"), np.full((600, 300, 3), 240, np.uint8))
    meta = {"width": 300, "height": 600, "dpr": 1, "page_width": 300, "page_height": 600, "shot_height": 600}
    store.db.execute("INSERT INTO web_page_versions(page_id, n, html_path, png_path, thumb_path, meta_json) "
                     "VALUES (?, 1, 'v1/index.html', 'v1/page.png', '', ?)", (page_id, json.dumps(meta)))
    return page_id


def _fake_runner(calls: list):
    """Как node/webpage/layers.mjs: область 300×400, карточка с кнопкой внутри, пустой блок (выпадет)."""

    def run(args, timeout):
        calls.append(args)
        out = Path(args[args.index("--out") + 1])
        region = {"x": 0, "y": 0, "w": 300, "h": 400}
        full = np.full((400, 300, 4), 255, np.uint8)
        cv2.imwrite(str(out / "full_0.png"), full)
        cv2.imwrite(str(out / "plate_0.png"), full)
        card = np.zeros((220, 260, 4), np.uint8)
        card[10:210, 10:250] = (255, 255, 255, 255)
        cv2.imwrite(str(out / "b0_0.png"), card)
        button = np.zeros((60, 200, 4), np.uint8)
        button[5:55, 5:195] = (80, 40, 250, 255)
        cv2.imwrite(str(out / "b1_0.png"), button)
        cv2.imwrite(str(out / "b2_0.png"), np.zeros((20, 20, 4), np.uint8))
        res = {
            "ok": True, "dpr": 1, "viewport": "mobile", "page_width": 300, "page_height": 600, "region": region,
            "full": {"clip": region, "tiles": [{"file": "full_0.png", "y": 0, "h": 400}]},
            "plate": {"clip": region, "tiles": [{"file": "plate_0.png", "y": 0, "h": 400}]},
            "blocks": [
                {"i": 0, "parent": -1, "kind": "card", "tag": "article", "text": "", "title": "Ferret", "alt": "",
                 "sel": "#ferret", "box": [30, 60, 240, 200], "paint": [0, 0], "clip": {"x": 20, "y": 50, "w": 260, "h": 220},
                 "tiles": [{"file": "b0_0.png", "y": 0, "h": 220}]},
                {"i": 1, "parent": 0, "kind": "button", "tag": "a", "text": "Смотреть каталог", "title": "", "alt": "",
                 "sel": "#ferret > a", "box": [55, 180, 190, 50], "paint": [0, 0], "clip": {"x": 50, "y": 175, "w": 200, "h": 60},
                 "tiles": [{"file": "b1_0.png", "y": 0, "h": 60}],
                 "text_style": {"text": "Смотреть каталог", "family": "Montserrat", "web_font": True, "size": 18,
                                "weight": 700, "italic": False, "color": [255, 255, 255, 1], "line_height": 22,
                                "letter_spacing": 0, "align": "center", "lines": 1, "box": [80, 194, 140, 22],
                                "content": [55, 180, 190, 50]}},
                {"i": 2, "parent": 1, "kind": "price", "tag": "span", "text": "1 290 ₽", "title": "", "alt": "",
                 "sel": "#p", "box": [0, 300, 20, 20], "paint": [0, 0], "clip": {"x": 0, "y": 300, "w": 20, "h": 20},
                 "tiles": [{"file": "b2_0.png", "y": 0, "h": 20}]},
            ],
            "font_faces": [],
        }
        return subprocess.CompletedProcess(args, 0, json.dumps(res, ensure_ascii=False) + "\n", "")

    return run


def test_fake_decompose_names_trim_and_api_insert(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, store = _client(tmp_path, monkeypatch)
    page_id = _fake_page(store)
    calls: list = []
    dec = L.decompose(store, page_id, region={"x": 0, "y": 0, "w": 300, "h": 400}, runner=_fake_runner(calls))
    assert "--region" in calls[0] and "--selector" not in calls[0]
    assert [b["name"] for b in dec["blocks"]] == ["Карточка: Ferret", "Кнопка: Смотреть каталог"]  # пустой выпал
    card, button = dec["blocks"]
    assert card["px"] == [30, 60, 240, 200] and card["box"] == [30.0, 60.0, 240.0, 200.0]  # обрезано по альфе
    assert button["px"] == [55, 180, 190, 50]
    folder = L.layers_dir(page_id, 1, dec["key"])
    assert not list(folder.glob("*_0.png"))  # полосы склеены и убраны

    clip = _canvas(monkeypatch, store)
    monkeypatch.setattr(L, "decompose", lambda *a, **k: dec)
    resp = client.post(f"/api/web-pages/{page_id}/to-layers",
                       json={"clip_id": clip["id"], "at_sec": 2, "as_text": ["b1"], "layout": "fit_width"})
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert [b["as"] for b in out["blocks"]] == ["image", "image", "text"]
    layers = store.get_clip_plan(clip["id"])["render_settings"]["layers"]
    bg, cardl, text = layers[-3:]
    assert bg["name"] == "Фон страницы" and bg["start"] == 2 and bg["props"]["w"] == pytest.approx(1080 * 0.88, abs=1)
    assert cardl["name"] == "Карточка: Ferret" and cardl["in"]["type"] == "slideUp" and cardl["start"] > 2
    assert text["type"] == "text" and text["props"]["font"] == "Montserrat" and text["start"] > cardl["start"]
    assert text["props"]["align"] == "center" and text["props"]["boxWidth"] == pytest.approx(190 * out["scale"], abs=0.2)
    # Без клипа и insert — только слои (вкладка «Страницы» ставит их сама).
    dry = client.post(f"/api/web-pages/{page_id}/to-layers", json={"insert": False, "plate": False})
    assert dry.status_code == 200 and "inserted" not in dry.json() and len(dry.json()["layers"]) == 2
    # Наезд на карточку: карточка и её кнопка едут к середине и растут вместе, фон притухает.
    zoomed = client.post(f"/api/web-pages/{page_id}/to-layers", json={"insert": False, "focus": {"block": "b0", "at": 3, "zoom": 2}})
    assert zoomed.status_code == 200, zoomed.text
    zl = zoomed.json()["layers"]
    bgk, cardk, btnk = zl[0]["keys"], zl[1]["keys"], zl[2]["keys"]
    assert bgk["opacity"][-1]["v"] == 0.25 and "scale" not in bgk and zl[1]["track"] == zl[0]["track"] + 1
    assert cardk["scale"][-1]["v"] == 2 and btnk["scale"][-1]["v"] == 2
    assert cardk["x"][-1]["v"] == pytest.approx(0.5) and cardk["y"][-1]["v"] == pytest.approx(0.5)
    # Кнопка держит место внутри карточки: смещение от центра карточки выросло вдвое.
    assert btnk["y"][-1]["v"] - 0.5 == pytest.approx(2 * (zl[2]["y"] - zl[1]["y"]), abs=1e-3)
    assert cardk["scale"][0]["t"] == pytest.approx(3 - zl[1]["start"], abs=1e-3)
    assert motion.normalize_layers(zl)[1]["keys"]["scale"][-1]["v"] == 2
    bad = client.post(f"/api/web-pages/{page_id}/to-layers", json={"blocks": ["nope"]})
    assert bad.status_code == 400 and "b0" in bad.json()["detail"]
    assert client.get(f"/api/web-pages/{page_id}/versions/1/layers/{dec['key']}/b0.png").status_code == 200
    assert client.get(f"/api/web-pages/{page_id}/versions/1/layers/{dec['key']}/..%2Fpage.png").status_code == 404


def test_region_spec_mapping_names_and_fonts(tmp_path, monkeypatch):
    meta = {"region": {"x": 0, "y": 500, "w": 390, "h": 300}}
    assert L.region_spec(meta, None) == {"region": {"x": 0.0, "y": 500.0, "w": 390.0, "h": 300.0}}
    assert L.region_spec({}, None) == {} and L.region_spec(meta, "page") == {}
    assert L.region_spec(meta, "screen") == {"screen": True} and L.region_spec(meta, "#product") == {"selector": "#product"}
    with pytest.raises(ValueError):
        L.region_spec(meta, {"x": 0, "y": 0, "w": 0, "h": 10})

    assert L.block_name({"kind": "price", "text": "1 290 ₽"}) == "Цена: 1 290 ₽"
    assert L.block_name({"kind": "card", "title": "", "alt": "Ferret"}) == "Карточка: Ferret"
    assert L.block_name({"kind": "header"}) == "Шапка сайта" and L.block_name({"kind": "image"}) == "Картинка"
    assert len(L.block_name({"kind": "text", "text": "очень " * 30})) <= 42

    dec = {"region": {"x": 0, "y": 100, "w": 390, "h": 400}, "page_width": 390,
           "blocks": [{"box": [20, 200, 100, 50]}, {"box": [200, 300, 150, 100]}]}
    s, ox, oy = LC.mapping(dec, dec["blocks"], "as_page", (1080, 1920), plate=True)
    assert s == pytest.approx(1080 / 390) and ox == 0 and oy + 300 * s == pytest.approx(1920 / 2)
    s2, ox2, oy2 = LC.mapping(dec, dec["blocks"], "fit_width", (1080, 1920), plate=False)
    assert ox2 + 20 * s2 == pytest.approx(1080 * LC.FIT_MARGIN) and ox2 + 350 * s2 == pytest.approx(1080 * (1 - LC.FIT_MARGIN))

    order = L.paint_order([{"i": 0, "parent": -1, "paint": [0, 0]}, {"i": 1, "parent": 0, "paint": [1, 2]},
                           {"i": 2, "parent": 0, "paint": [0, 0]}, {"i": 3, "parent": -1, "paint": [0, 0]}])
    assert [b["i"] for b in order] == [0, 2, 1, 3]  # позиционированный значок поверх картинки карточки
    assert [b["i"] for b in L.paint_order([{"i": 5, "parent": 4}, {"i": 7, "parent": 6}])] == [5, 7]  # без родителей

    _c, store = _client(tmp_path, monkeypatch)
    page_id = _fake_page(store)
    (W.page_dir(page_id) / "assets").mkdir()
    (W.page_dir(page_id) / "assets" / "cyr.woff2").write_bytes(b"wOF2")
    (W.page_dir(page_id) / "assets" / "lat.woff2").write_bytes(b"wOF2")
    faces = [{"family": "Shop Sans", "weight": "400 800", "style": "normal", "range": "U+0400-045F, U+0020",
              "src": ["http://vvf-page.local/assets/cyr.woff2"]},
             {"family": "Shop Sans", "weight": "400 800", "style": "normal", "range": "U+0000-00FF",
              "src": ["http://vvf-page.local/assets/lat.woff2"]}]
    block = {"text_style": {"family": "Shop Sans", "web_font": True, "weight": 700, "text": "Купить сейчас"}}
    got = L.text_font(page_id, block, faces, library=[])
    assert got["ok"] and got["file"] == "cyr.woff2" and got["source"] == "page"
    mixed = {"text_style": {**block["text_style"], "text": "Купить VTuber"}}
    assert not L.text_font(page_id, mixed, faces, library=[])["ok"]  # нужен и кириллический, и латинский файл
    lib = [{"family": "Montserrat", "weight": 800}]
    mont = {"text_style": {"family": "Montserrat", "web_font": True, "weight": 400, "text": "x"}}
    assert not L.text_font(page_id, mont, [], lib)["ok"]  # толщины 400 в библиотеке нет
    assert L.text_font(page_id, {"text_style": {**mont["text_style"], "weight": 700}}, [], lib)["ok"]
    assert not L.text_font(page_id, {"text_style": {**mont["text_style"], "web_font": False}}, [], lib)["ok"]
