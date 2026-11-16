"""Заставка и концовка логотипом кистью как пресет сервиса: шаблоны галереи logo-intro / logo-outro, настройки
бренда logo_intro / logo_outro (по умолчанию выключены) и вставка в apply_brand — заставка с 0 с, концовка
кончается ровно на последнем кадре, звуки пака без треска, логотип в углу — между ними. MCP: add_motion_template
(картинка-логотип по умолчанию — логотип бренда проекта, концовка не вылезает за клип) и apply_brand."""

from __future__ import annotations

import pytest

from app import brand_kit, motion
from app import motion_template_tools as T
from app.agent_tools import ToolContext, get_tool, run_tool
from app.brand_kit import LOGO_ID, normalize_brand
from app.brand_kit_logo import INTRO_ID, OUTRO_ID, normalize_logo_bumper
from test_api_pipeline import _require_ffmpeg
from test_brand_kit import _assets, _no_vision
from test_clip_spec import _setup

RATCHET = ("glitch", "record-scratch", "typing", "tension")


def _fake_build(calls: list):
    """Сборщик шаблона без компоновщика: те же слои по форме, что templates/logo.ts."""

    def build(template: str, params: dict, at: float, fmt=None) -> dict:
        calls.append((template, dict(params), at, fmt))
        dur = float(params.get("duration") or 3.5)
        logo = {"id": "l_tpl", "type": "logo_reveal", "start": at, "end": at + dur, "x": 0.5,
                "y": 0.79 if params.get("position") == "низ" else 0.45, "track": 3, "name": template,
                "props": {"asset_id": params["logo"], "w": 842, "boil": 3, "sketch": True, "color": params["color"],
                          "exit": None if template == "logo-outro" else {"kind": "scratch", "dur": 0.3}}}
        layers = [logo]
        if params.get("sound"):
            layers.append({"id": "l_s", "type": "audio", "start": at + 0.1, "end": at + 2.0,
                           "props": {"asset_id": "sfx:freesound/whoosh-3.ogg", "volume": 1}})
            layers.append({"id": "l_h", "type": "audio", "start": at + 2.1, "end": at + dur,
                           "props": {"asset_id": "sfx:freesound/impact-low.ogg", "volume": 1}})
        return {"layers": layers}

    return build


def _compositor_ok() -> bool:
    try:
        T._command()
        return True
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------- нормализация


def test_logo_bumper_defaults_off_and_clamped():
    brand = normalize_brand({"name": "X"})
    assert brand["logo_intro"] == {"on": False, "duration": 3.5, "color": "#ffffff", "position": "центр",
                                   "size": 78.0, "sound": True, "min_clip_sec": 60.0}
    assert brand["logo_outro"]["on"] is False and brand["logo_outro"]["position"] == "низ"
    cfg = normalize_logo_bumper({"on": True, "duration": 99, "color": "#F00", "position": "где-то", "size": 5,
                                 "sound": False, "min_clip_sec": -3}, "outro")
    assert cfg == {"on": True, "duration": 10.0, "color": "#ff0000", "position": "низ", "size": 20.0,
                   "sound": False, "min_clip_sec": 0.0}
    # true/false — коротко «включить/выключить», остальное по умолчанию.
    assert normalize_logo_bumper(True, "intro")["on"] is True


# --------------------------------------------------------------------------- apply_brand


def test_apply_brand_places_intro_and_outro_to_last_frame(tmp_path, monkeypatch):
    _require_ffmpeg()
    _no_vision(monkeypatch)
    client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    logo, _track = _assets(store, tmp_path, monkeypatch)
    calls: list = []
    monkeypatch.setattr(T, "build_template", _fake_build(calls))
    brand = store.create_brand("yakoodev", logo_asset_id=logo["id"],
                               logo_intro={"on": True, "min_clip_sec": 0}, logo_outro={"on": True, "min_clip_sec": 0})

    out = client.post(f"/api/clip-plans/{plan['id']}/apply-brand", json={"brand_id": brand["id"]})
    assert out.status_code == 200, out.text
    res = out.json()
    layers = {l["id"]: l for l in store.get_clip_plan(plan["id"])["render_settings"]["layers"]}
    from app.agent_tools import clip_timeline

    total = float(clip_timeline(store, plan["id"])["duration"])
    intro, outro = layers[INTRO_ID], layers[OUTRO_ID]
    assert intro["type"] == outro["type"] == "logo_reveal"
    assert (intro["start"], intro["end"]) == (0.0, 3.5)
    # Концовка — ровно до последнего кадра.
    assert outro["end"] == round(total, 3) and outro["start"] == round(total - 3.5, 3)
    assert intro["props"]["asset_id"] == outro["props"]["asset_id"] == str(logo["id"])
    assert intro["props"]["color"] == "#ffffff" and outro["props"]["exit"] is None
    # Звуки — с id бренда (повтор снимает), записи пака без треска.
    sounds = [l for l in layers.values() if l["id"].startswith(("brand_intro_s", "brand_outro_s"))]
    assert len(sounds) == 4 and all(not any(r in l["props"]["asset_id"] for r in RATCHET) for l in sounds)
    assert [c[0] for c in calls] == ["logo-intro", "logo-outro"]
    assert calls[1][1]["position"] == "низ" and calls[1][1]["sound"] is True
    # Логотип в углу — между заставкой и концовкой.
    corner = layers[LOGO_ID]
    assert corner["start"] >= intro["end"] and corner["end"] <= outro["start"]
    assert any("заставка логотипом" in c for c in res["changes"])
    assert any("концовка логотипом" in c for c in res["changes"])

    # Повтор — без дублей.
    brand_kit.apply_brand(store, plan["id"], brand["id"])
    again = store.get_clip_plan(plan["id"])["render_settings"]["layers"]
    assert sum(1 for l in again if l["id"] == INTRO_ID) == 1 and sum(1 for l in again if l["id"] == OUTRO_ID) == 1
    assert len(again) == len(layers)


def test_apply_brand_bumpers_off_short_clip_and_overrides(tmp_path, monkeypatch):
    _require_ffmpeg()
    _no_vision(monkeypatch)
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    logo, _track = _assets(store, tmp_path, monkeypatch)
    calls: list = []
    build = _fake_build(calls)

    # По умолчанию выключены — шаблон не собирается вовсе.
    plain = store.create_brand("Без заставки", logo_asset_id=logo["id"])
    brand_kit.apply_brand(store, plan["id"], plain["id"], build_template=build)
    assert not calls
    ids = {l["id"] for l in store.get_clip_plan(plan["id"])["render_settings"]["layers"]}
    assert INTRO_ID not in ids and OUTRO_ID not in ids

    # Включены, но ролик (10 с) короче min_clip_sec 60 — «только длинные версии»: нет, с предупреждением.
    long_only = store.create_brand("Только длинные", logo_asset_id=logo["id"], logo_intro=True, logo_outro=True,
                                   sfx_policy="none")
    out = brand_kit.apply_brand(store, plan["id"], long_only["id"], build_template=build)
    assert not calls and any("короче 60" in w for w in out["warnings"])

    # На этот ролик — вручную: заставка да, концовка нет; политика «без эффектов» — без звука.
    out = brand_kit.apply_brand(store, plan["id"], long_only["id"], build_template=build,
                                logo_intro=True, logo_outro=False)
    assert [c[0] for c in calls] == ["logo-intro"] and calls[0][1]["sound"] is False
    ids = {l["id"] for l in store.get_clip_plan(plan["id"])["render_settings"]["layers"]}
    assert INTRO_ID in ids and OUTRO_ID not in ids and not any(i.startswith("brand_intro_s") for i in ids)

    # Нет логотипа у бренда — предупреждение, остальное бренда ставится.
    no_logo = store.create_brand("Без лого", logo_intro={"on": True, "min_clip_sec": 0}, cta={"text": "Подпишись"})
    out = brand_kit.apply_brand(store, plan["id"], no_logo["id"], build_template=build)
    assert any("нет логотипа" in w for w in out["warnings"]) and any("призыв" in c for c in out["changes"])


def test_mcp_apply_brand_logo_overrides(tmp_path, monkeypatch):
    _require_ffmpeg()
    _no_vision(monkeypatch)
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    logo, _track = _assets(store, tmp_path, monkeypatch)
    calls: list = []
    monkeypatch.setattr(T, "build_template", _fake_build(calls))
    brand = store.create_brand("yakoodev", logo_asset_id=logo["id"], logo_outro=True)
    props = get_tool("apply_brand").parameters["properties"]
    assert props["logo_intro"]["type"] == props["logo_outro"]["type"] == "boolean"
    ctx = ToolContext(store=store, clip_plan_id=plan["id"])
    out, ok = run_tool(ctx, "apply_brand", {"brand_id": brand["id"], "logo_outro": True})
    assert ok, out
    assert [c[0] for c in calls] == ["logo-outro"]
    out, ok = run_tool(ctx, "apply_brand", {"brand_id": brand["id"], "logo_intro": "да"})
    assert not ok and "logo_intro" in out["error"]


# --------------------------------------------------------------------------- MCP add_motion_template


def test_add_motion_template_logo_defaults_to_brand_and_outro_ends_at_clip_end(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    logo, _track = _assets(store, tmp_path, monkeypatch)
    calls: list = []
    tpl = {"id": "logo-outro", "name": "Логотип: концовка", "category": "Бренд", "duration": 3.5, "place": "end",
           "params": [{"name": "logo", "type": "image", "default": ""}, {"name": "duration", "type": "number",
                                                                          "default": 3.5}]}
    monkeypatch.setattr(T, "list_templates", lambda: [tpl])
    monkeypatch.setattr(T, "build_template", lambda tid, params, at, fmt=None: _fake_build(calls)(
        tid, {"color": "#ffffff", "sound": False, **params}, at, fmt))
    ctx = ToolContext(store=store, clip_plan_id=plan["id"])

    # Нет логотипа ни в params, ни у бренда проекта — понятная ошибка.
    monkeypatch.setattr(T, "_brand_logo", lambda _ctx: "")
    out, ok = run_tool(ctx, "add_motion_template", {"template": "logo-outro", "at": 0})
    assert not ok and "logo" in out["error"]

    monkeypatch.setattr(T, "_brand_logo", lambda _ctx: str(logo["id"]))
    from app.agent_tools import clip_timeline

    total = float(clip_timeline(store, plan["id"])["duration"])
    # Концовка с at «где-то в конце» встаёт так, чтобы кончиться на последнем кадре; логотип — бренда проекта.
    out, ok = run_tool(ctx, "add_motion_template", {"template": "logo-outro", "at": total - 1.0})
    assert ok, out
    assert out["at"] == round(total - 3.5, 3) and calls[-1][1]["logo"] == str(logo["id"])
    layer = next(l for l in store.get_clip_plan(plan["id"])["render_settings"]["layers"] if l["type"] == "logo_reveal")
    assert layer["end"] == round(total, 3) and layer["props"]["asset_id"] == str(logo["id"])
    assert T.placed_at(tpl, {"duration": 2}, 1.0, total) == 1.0  # влезает — не трогаем
    assert T.placed_at({**tpl, "place": None}, {}, total - 1.0, total) == total - 1.0
    assert T.with_images(tpl, {"logo": "5"}, ctx)["logo"] == "5"  # заданный явно — как есть


# --------------------------------------------------------------------------- настоящий движок шаблонов


@pytest.mark.skipif(not _compositor_ok(), reason="компоновщик моушна не собран (npm run build:motion)")
def test_real_logo_templates_build_valid_layers():
    T._LIST_CACHE.clear()
    found = {t["id"]: t for t in T.list_templates()}
    intro, outro = found["logo-intro"], found["logo-outro"]
    assert intro["category"] == outro["category"] == "Бренд"
    assert (intro["place"], outro["place"]) == ("start", "end") and intro["duration"] == 3.5
    assert {p["name"]: p["type"] for p in intro["params"]}["logo"] == "image"

    for template in ("logo-intro", "logo-outro"):
        built = T.build_template(template, {"logo": "9"}, 20.0, None)
        layers = built["layers"]
        main = layers[0]
        assert main["type"] == "logo_reveal" and (main["start"], main["end"]) == (20.0, 23.5)
        p = main["props"]
        # Одобренный вид: белый, кипение с эскизом контуром, рывок, у концовки — без ухода.
        assert p["asset_id"] == "9" and p["color"] == "#ffffff" and p["boil"] == 3 and p["sketch"] is True
        assert (p["exit"] is None) == (template == "logo-outro")
        sounds = [l for l in layers if l["type"] == "audio"]
        assert len(sounds) == (3 if template == "logo-outro" else 4)
        assert all(l["props"]["asset_id"].startswith("sfx:freesound/") for l in sounds)
        assert not any(r in l["props"]["asset_id"] for l in sounds for r in RATCHET)
        assert all(20.0 <= l["start"] < l["end"] <= 23.5 for l in sounds)
        assert motion.validate_layers(layers, 30.0, asset_exists=lambda _id: True) == []
    # Без звука — только логотип; в 16:9 логотип встаёт в кадр формата.
    assert len(T.build_template("logo-intro", {"logo": "9", "sound": False}, 0, None)["layers"]) == 1
    wide = T.build_template("logo-outro", {"logo": "9"}, 0, "16:9")["layers"][0]
    assert 0.6 < wide["y"] < 0.95


@pytest.mark.skipif(not _compositor_ok(), reason="компоновщик моушна не собран (npm run build:motion)")
def test_apply_brand_with_real_templates(tmp_path, monkeypatch):
    _require_ffmpeg()
    _no_vision(monkeypatch)
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    logo, _track = _assets(store, tmp_path, monkeypatch)
    brand = store.create_brand("yakoodev", logo_asset_id=logo["id"], logo_intro={"on": True, "min_clip_sec": 0},
                               logo_outro={"on": True, "min_clip_sec": 0, "color": "#ffd600"})
    out = brand_kit.apply_brand(store, plan["id"], brand["id"])
    layers = {l["id"]: l for l in store.get_clip_plan(plan["id"])["render_settings"]["layers"]}
    from app.agent_tools import clip_timeline

    total = round(float(clip_timeline(store, plan["id"])["duration"]), 3)
    assert layers[INTRO_ID]["start"] == 0.0 and layers[OUTRO_ID]["end"] == total
    assert layers[OUTRO_ID]["props"]["color"] == "#ffd600"
    assert all(l["end"] <= total for l in layers.values())
    assert not any("не собралась" in w for w in out["warnings"])
