"""Плашка канала (nameplate): слой движка, место под разделителем раскладки или в безопасном
углу, бренд и MCP (set_nameplate, apply_brand), Ctrl+Z одним шагом."""

from __future__ import annotations

import pytest

from app import brand_kit, motion
from app.agent_tools import ToolContext, run_tool
from app.brand_kit import (
    CTA_ID, NAMEPLATE_ID, merge_brand, nameplate_place, normalize_brand, normalize_nameplate,
)
from app.clip_history import snapshot, undo
from test_api_pipeline import _require_ffmpeg
from test_clip_spec import _setup

PLATE = {"platform": "twitch", "handle": "sareamura"}
SPLIT = {"mode": "split", "cam_pos": "bottom", "ratio": 0.45, "gap": 0}


def _np(**over) -> dict:
    return normalize_nameplate({**PLATE, **over})


def _top(place: dict, np: dict) -> float:
    return place["y"] - np["size"] / 1920 / 2


# --------------------------------------------------------------------------- слой и нормализация


def test_layer_normalized_and_checked():
    layer = motion.normalize_layers([{"type": "nameplate", "start": 0, "end": 5,
                                      "props": {"handle": "sareamura", "align": "left", "boxWidth": 520}}])[0]
    assert layer["props"]["platform"] == "twitch" and layer["props"]["style"] == "glass"
    assert layer["props"]["accent"] is None and layer["props"]["size"] == motion.NAMEPLATE_SIZE
    assert layer["props"]["boxWidth"] == 520
    assert any("handle" in p for p in motion.validate_layers([{"type": "nameplate", "props": {"platform": "none"}}]))
    assert any("platform" in p for p in motion.validate_layers([{"type": "nameplate", "props": {"platform": "vk"}}]))
    schema = motion.schema_summary()
    assert "nameplate" in schema["types"] and schema["nameplate"]["platforms"][0] == "twitch"


def test_normalize_nameplate_defaults_and_brand_merge():
    assert normalize_nameplate(None) == {"platform": "none", "handle": "", "style": "glass", "accent": None,
                                         "position": "auto", "size": 74.0, "fade": False, "anim": "subtle",
                                         "live": False, "outro": "auto"}
    np = normalize_nameplate({"platform": "vk", "handle": "  my   shop ", "style": "x", "accent": "#F0F",
                              "position": "middle", "size": 500, "fade": "true", "anim": "wild", "live": 1,
                              "outro": "spin"})
    assert np == {"platform": "none", "handle": "my shop", "style": "glass", "accent": "#ff00ff",
                  "position": "auto", "size": 160.0, "fade": True, "anim": "subtle", "live": True, "outro": "auto"}
    brand = normalize_brand({"name": "Мура", "nameplate": PLATE})
    assert brand["nameplate"]["handle"] == "sareamura" and brand["nameplate"]["platform"] == "twitch"
    # Правка одного поля не стирает остальные.
    merged = merge_brand(brand, {"nameplate": {"style": "solid"}})
    assert merged["nameplate"]["handle"] == "sareamura" and merged["nameplate"]["style"] == "solid"


# --------------------------------------------------------------------------- место


def test_under_split_below_separator_and_captions():
    np = _np()
    caps = [{"id": "c", "type": "captions", "y": 0.5, "start": 0, "end": 9, "props": {}}]  # на шву
    place = nameplate_place({"layout": SPLIT}, caps, np)
    assert place["position"] == "under-split-left" and place["fits"] and place["align"] == "left"
    # Верх плашки — 72 px ниже разделителя (низ разделителя = 1 − ratio), x — 24 px от левого края.
    assert _top(place, np) == pytest.approx(0.55 + 72 / 1920, abs=1e-3)
    assert place["x"] * 1080 - place["boxWidth"] / 2 == pytest.approx(24, abs=0.5)
    # Субтитры ниже разделителя — плашка уходит под них.
    low_caps = [{**caps[0], "y": 0.6}]
    place = nameplate_place({"layout": SPLIT}, low_caps, np)
    assert place["position"] == "under-split-left"
    assert _top(place, np) >= 0.6 + brand_kit.CAPTION_HALF
    # Прожигаемые субтитры (ASS, без слоя) — тоже: низ на sub_pos_pct от низа кадра.
    place = nameplate_place({"layout": SPLIT, "subs_on": True, "sub_pos_pct": 38}, [], np)
    assert _top(place, np) >= 0.62


def test_corner_when_no_split_or_no_room():
    np = _np()
    place = nameplate_place({}, [], np)
    assert place["position"] == "top-left" and place["fits"]
    assert _top(place, np) >= 0.10  # ниже вкладок площадки (y < 170 px)
    # Логотип бренда слева — плашка в правом углу, у правого края.
    place = nameplate_place({}, [], np, logo_corner="top-left")
    assert place["position"] == "top-right" and place["align"] == "right"
    assert place["x"] * 1080 + place["boxWidth"] / 2 == pytest.approx(1080 - 24, abs=0.5)
    # Просили под разделитель, а раскладки нет — угол и объяснение.
    place = nameplate_place({}, [], _np(position="under-split-center"))
    assert place["position"] == "top-left" and "split" in place["note"]
    # Разделитель у самого низа (вебка 0.2) и субтитры под ним — до подписи площадки не опускаемся.
    low = {"layout": {**SPLIT, "ratio": 0.2}}
    caps = [{"id": "c", "type": "captions", "y": 0.82, "start": 0, "end": 9, "props": {}}]
    place = nameplate_place(low, caps, np)
    assert place["position"] == "top-left"
    # Хук наверху — плашка в углу уходит под него, а не на буквы.
    hook = {"id": "auto_hook", "type": "text", "y": 0.14, "start": 0, "end": 3,
            "props": {"text": "ЭТО МОДЕЛЬ", "size": 96}}
    place = nameplate_place({}, [hook], np)
    assert _top(place, np) > 0.14


def test_center_and_tiktok_buttons_and_pip():
    np = _np(position="under-split-center")
    place = nameplate_place({"layout": SPLIT}, [], np)
    assert place["position"] == "under-split-center" and place["x"] == 0.5 and place["align"] == "center"
    # Справа сверху — кнопки TikTok (x > 900, y 900–1600) не задевает: угол выше них.
    place = nameplate_place({}, [], _np(position="top-right"))
    assert place["y"] < 900 / 1920
    # Окошко вебки pip в левом верхнем углу — плашка ниже окошка.
    pip = {"mode": "pip_cam", "pip": {"x": 0.2, "y": 0.15, "w": 0.4}, "cam": {"x": 0, "y": 0, "w": 0.3, "h": 0.3}}
    np = _np(position="top-left")
    place = nameplate_place({"layout": pip}, [], np)
    assert _top(place, np) > 0.15


# --------------------------------------------------------------------------- MCP и бренд


def _ctx(store, plan):
    return ToolContext(store=store, clip_plan_id=plan["id"], on_first_change=lambda: snapshot(store, plan["id"], "агент"))


def _layers(store, plan) -> dict:
    return {l["id"]: l for l in store.get_clip_plan(plan["id"])["render_settings"].get("layers") or []}


def test_mcp_set_nameplate_place_replace_remove(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    store.set_clip_plan_render_settings(plan["id"], {"layout": SPLIT})

    out, ok = run_tool(_ctx(store, plan), "set_nameplate", {"platform": "twitch", "handle": "sareamura"})
    assert ok, out
    assert out["position"] == "under-split-left" and "view_frame" in out["check"]
    layer = _layers(store, plan)[NAMEPLATE_ID]
    assert layer["type"] == "nameplate" and layer["start"] == 0 and layer["end"] == pytest.approx(10.0)
    assert layer["props"]["handle"] == "sareamura" and layer["props"]["align"] == "left"
    assert layer["in"]["type"] == "none" and layer["shadow"]["blur"] > 0
    assert store.count_clip_plan_history(plan["id"]) == 1

    # Повтор заменяет (один слой), fade — мягкий вход, свой угол.
    out, ok = run_tool(_ctx(store, plan), "set_nameplate", {"handle": "mura", "platform": "youtube",
                                                           "position": "top-right", "fade": True,
                                                           "anim": "none"})
    assert ok, out
    plates = [l for l in _layers(store, plan).values() if l["type"] == "nameplate"]
    assert len(plates) == 1 and plates[0]["props"]["platform"] == "youtube" and plates[0]["in"]["type"] == "fade"
    assert plates[0]["props"]["anim"] == "none"
    assert out["position"] == "top-right"

    # Нечего показать — ошибка; снять — слоя нет, Ctrl+Z возвращает.
    out, ok = run_tool(_ctx(store, plan), "set_nameplate", {"platform": "none"})
    assert not ok and "handle" in out["error"]
    out, ok = run_tool(_ctx(store, plan), "set_nameplate", {"remove": True})
    assert ok and out["removed"] and NAMEPLATE_ID not in _layers(store, plan)
    undo(store, plan["id"])
    assert NAMEPLATE_ID in _layers(store, plan)


def test_corner_preset_after_hook_stays_small_in_top_corner(tmp_path, monkeypatch):
    """QA 01.11 (клип 889): плашка «после хука в углу» встала на x0.79 y0.40 — место считалось с хуком
    0–10 с на y 0.158, хотя плашка выходила после него. corner: маленькая, в верхнем углу, после хука."""
    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    hook = {"id": "auto_hook", "type": "text", "start": 0, "end": 4, "x": 0.5, "y": 0.158,
            "props": {"text": "ОГРОМНЫЙ ТАРАКАН\nВ КЛИПЕ", "size": 96}}
    caps = {"id": "auto_caps", "type": "captions", "start": 0, "end": 10, "x": 0.5, "y": 0.3,
            "props": {"maxWords": 2}}
    store.set_clip_plan_render_settings(plan["id"], {"layers": [hook, caps]})

    out, ok = run_tool(_ctx(store, plan), "set_nameplate", {"platform": "twitch", "handle": "sareamura",
                                                           "corner": "top-right"})
    assert ok, out
    layer = _layers(store, plan)[NAMEPLATE_ID]
    assert out["position"] == "top-right" and layer["start"] == pytest.approx(4.0)
    assert layer["x"] > 0.6 and layer["y"] < 0.14, (layer["x"], layer["y"])
    assert layer["props"]["size"] == brand_kit.NAMEPLATE_CORNER_SIZE and layer["props"]["align"] == "right"
    assert not out["warnings"]

    out, ok = run_tool(_ctx(store, plan), "set_nameplate", {"platform": "twitch", "handle": "sareamura",
                                                           "corner": "top-left", "start": 5, "end": 9, "size": 70})
    assert ok, out
    layer = _layers(store, plan)[NAMEPLATE_ID]
    assert layer["x"] < 0.4 and (layer["start"], layer["end"]) == (5.0, 9.0) and layer["props"]["size"] == 70
    # Плашка на экране вместе с хуком — уходит ниже его, как раньше.
    out, ok = run_tool(_ctx(store, plan), "set_nameplate", {"platform": "twitch", "handle": "sareamura",
                                                           "corner": "top-left", "start": 0})
    assert ok and _layers(store, plan)[NAMEPLATE_ID]["y"] > 0.18
    out, ok = run_tool(_ctx(store, plan), "set_nameplate", {"handle": "x", "corner": "middle"})
    assert not ok


def test_frame_with_nameplate_through_real_compositor(tmp_path, monkeypatch):
    """Кадр агента (тот же компоновщик, что рендер): плашка на месте, обводка цветом Twitch."""
    _require_ffmpeg()
    import subprocess

    from app.settings import settings
    from test_motion import _real_compositor

    compositor = _real_compositor()
    client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "motion_compositor", compositor)
    store.set_clip_plan_render_settings(plan["id"], {"layout": SPLIT})
    out, ok = run_tool(_ctx(store, plan), "set_nameplate", {"platform": "twitch", "handle": "sareamura"})
    assert ok, out
    res = client.post(f"/api/clip-plans/{plan['id']}/motion/frame?t=1")
    assert res.status_code == 200, res.text
    png = tmp_path / "frame.png"
    png.write_bytes(res.content)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0",
                            str(png)], capture_output=True, text=True, check=True).stdout
    w, h = (int(v) for v in probe.strip().split(","))
    # Тело значка Twitch (левая часть «пузыря» над полосками) — цвет площадки #9146ff.
    icon_h = 74 * 0.56
    x = round((24 + 74 * 0.42 + 0.86 * icon_h * 0.2) * w / 1080)
    y = round(out["y"] * h - icon_h * 0.2 * w / 1080)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(png), "-vf", f"crop=1:1:{x}:{y}", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    r, g, b = raw[0], raw[1], raw[2]
    assert b > 170 and r > 90 and g < 120, (r, g, b)


def test_apply_brand_nameplate(tmp_path, monkeypatch):
    _require_ffmpeg()
    monkeypatch.setattr(brand_kit, "_subject_box", lambda store, plan_id: None)
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    caps = {"id": "auto_caps", "type": "captions", "start": 0, "end": 10, "x": 0.5, "y": 0.5, "props": {}}
    store.set_clip_plan_render_settings(plan["id"], {"layout": SPLIT, "layers": [caps]})
    brand = store.create_brand("Мура", nameplate={**PLATE, "live": True}, cta={"text": "Подпишись"})

    out, ok = run_tool(_ctx(store, plan), "apply_brand", {"brand_id": brand["id"]})
    assert ok, out
    assert any("плашка канала" in c for c in out["changes"])
    layers = _layers(store, plan)
    plate, cta = layers[NAMEPLATE_ID], layers[CTA_ID]
    # По умолчанию своя анимация subtle, без fade; outro auto — сворачивается в значок к призыву.
    assert plate["props"]["handle"] == "sareamura" and plate["in"]["type"] == "none"
    assert plate["props"]["anim"] == "subtle" and plate["props"]["live"] is True
    assert plate["props"]["outro"] == "collapse"
    assert plate["end"] == pytest.approx(cta["start"] + motion.NAMEPLATE_OUTRO_SEC, abs=1e-3)

    # Плашка без свёртки остаётся до конца — призыв обходит её: полосы по высоте не пересекаются.
    out, ok = run_tool(_ctx(store, plan), "apply_brand", {"brand_id": brand["id"], "nameplate": {"outro": "none"}})
    assert ok, out
    layers = _layers(store, plan)
    plate, cta = layers[NAMEPLATE_ID], layers[CTA_ID]
    assert plate["props"]["outro"] == "none" and plate["end"] == pytest.approx(10.0)
    half = plate["props"]["size"] / 1920 / 2
    assert abs(cta["y"] - plate["y"]) > half + 0.02
    # Повтор — без дублей.
    run_tool(_ctx(store, plan), "apply_brand", {"brand_id": brand["id"]})
    assert [l["type"] for l in store.get_clip_plan(plan["id"])["render_settings"]["layers"]].count("nameplate") == 1
    # Свои поля плашки поверх бренда и снятие.
    out, ok = run_tool(_ctx(store, plan), "apply_brand", {"brand_id": brand["id"], "nameplate": {"style": "solid"}})
    assert ok and _layers(store, plan)[NAMEPLATE_ID]["props"]["style"] == "solid"
    out, ok = run_tool(_ctx(store, plan), "apply_brand", {"brand_id": brand["id"], "remove_nameplate": True})
    assert ok and NAMEPLATE_ID not in _layers(store, plan)

    # Бренд без плашки не снимает поставленную set_nameplate.
    plain = store.create_brand("Без плашки")
    run_tool(_ctx(store, plan), "set_nameplate", {"handle": "sareamura"})
    out, ok = run_tool(_ctx(store, plan), "apply_brand", {"brand_id": plain["id"]})
    assert ok and _layers(store, plan)[NAMEPLATE_ID]["props"]["handle"] == "sareamura"
