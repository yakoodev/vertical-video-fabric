"""Концовка-баннер (app/end_screen.py): нормализация, шаблоны SVG, comp.endScreen, настройки клипа,
MCP set_end_screen / svg_to_webm, бренд (cta.style = banner), кадр агента через компоновщик."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET

import pytest

from app import brand_kit, svg_webm
from app.agent_tools import ToolContext, run_tool
from app.clip_history import snapshot, undo
from app.clip_settings import normalize_render_settings
from app.end_screen import (
    END_LAYER_ID, comp_end_screen, normalize_end_screen, parse_days, parse_time, split_headline, template_svg,
)
from app.motion import plan_motion, visual_motion
from app.motion_render import write_comp
from test_api_pipeline import _require_ffmpeg
from test_clip_spec import _setup
from test_svg_webm import fake_run

FIELDS = {"text": "Стримлю с 8 утра — заходи", "handle": "sare<amura>", "platform": "twitch", "days": "пн–пт",
          "time": "8:00 МСК"}


# --------------------------------------------------------------------------- нормализация и разбор


def test_normalize_end_screen_defaults_clamps_and_off():
    assert normalize_end_screen(None) is None and normalize_end_screen({"on": False}) is None
    es = normalize_end_screen({})
    assert es["duration"] == 3.0 and es["blur"] == 18.0 and es["dim"] == 0.35 and es["ramp"] == 0.6
    assert es["template"] == "live" and es["svg_asset_id"] is None and es["platform"] == "none"
    es = normalize_end_screen({"duration": 99, "blur": 500, "dim": 5, "ramp": 9, "template": "x", "platform": "vk",
                               "accent": "#ABC", "handle": "@mura", "text": "  много   пробелов "})
    assert es["duration"] == 8.0 and es["blur"] == 60.0 and es["dim"] == 0.8 and es["ramp"] == 2.0
    assert es["template"] == "live" and es["platform"] == "none" and es["accent"] == "#aabbcc"
    assert es["handle"] == "mura" and es["text"] == "много пробелов"
    # Свой SVG вместо шаблона; ramp не длиннее половины концовки.
    es = normalize_end_screen({"svg_asset_id": "12", "template": "neon", "duration": 1.5, "ramp": 2})
    assert es["svg_asset_id"] == 12 and es["template"] is None and es["ramp"] == 0.75


def test_parse_days_time_and_headline():
    assert parse_days("пн–пт") == [True] * 5 + [False] * 2
    assert parse_days("пн, ср, пт") == [True, False, True, False, True, False, False]
    assert parse_days("пт-пн") == [True, False, False, False, True, True, True]  # через выходные
    assert parse_days("выходные") == [False] * 5 + [True] * 2 and parse_days("") == [True] * 7
    assert parse_time("8:00 МСК") == ("08:00", "МСК")
    assert parse_time("", "Стримлю с 8 утра — заходи") == ("08:00", "")
    assert parse_time("", "в 7 вечера") == ("19:00", "")
    assert parse_time("", "заходи") == ("", "")
    assert split_headline("Стримлю с 8 утра — заходи") == (["Стримлю с 8 утра —", "заходи"], 1)
    lines, accent = split_headline("Новые стримы каждый будний день")
    assert len(lines) == 2 and accent == 1 and " ".join(lines) == "Новые стримы каждый будний день"
    assert split_headline("Заходи") == (["Заходи"], 0)


@pytest.mark.parametrize("template", ["live", "schedule", "neon"])
def test_templates_are_valid_svg_with_texts(template):
    svg, w, h = template_svg(template, FIELDS)
    root = ET.fromstring(svg)  # валидный XML: тексты экранированы
    assert root.tag.endswith("svg") and w == 960 and 300 < h < 900
    assert "sare&lt;amura&gt;" in svg and "@keyframes" in svg and "data-fit" in svg
    assert "M-0.31 -0.5" in svg  # значок Twitch — тот же глиф, что у слоя nameplate
    assert "Unbounded" in svg  # шрифт заголовка — из шрифтов сервиса (конвертер подключит @font-face)
    if template == "schedule":
        assert "08:00" in svg and svg.count('rx="18"') == 7  # семь чипов дней


def test_template_accent_and_font_follow_fields():
    svg, _w, _h = template_svg("live", {**FIELDS, "accent": "#22c55e", "font": "Rubik"})
    assert "#22c55e" in svg and '"Rubik"' in svg
    svg, _w, _h = template_svg("live", {"text": "", "handle": ""})
    assert "Стримлю с 8 утра" in svg and "@" not in svg.split("</style>")[1]  # без ника — без строки ника


# --------------------------------------------------------------------------- рендер: comp и настройки


def test_comp_end_screen_starts_with_banner_layer():
    es = {"duration": 3, "blur": 20, "dim": 0.3, "ramp": 0.5}
    assert comp_end_screen(es, [{"id": END_LAYER_ID, "start": 6.4}], 10.0) == {
        "start": 6.4, "ramp": 0.5, "blur": 20.0, "dim": 0.3}
    assert comp_end_screen(es, [], 10.0)["start"] == 7.0
    assert comp_end_screen({**es, "blur": 0, "dim": 0}, [], 10.0) is None
    assert comp_end_screen(None, [], 10.0) is None


def test_write_comp_and_plan_motion_carry_end_screen(tmp_path):
    es = normalize_end_screen({"duration": 2})
    path = write_comp(tmp_path / "comp.json", width=1080, height=1920, fps=30, duration=4, layers=[], camera=[],
                      end_screen=comp_end_screen(es, [], 4))
    assert json.loads(path.read_text("utf-8"))["endScreen"] == {"start": 2.0, "ramp": 0.6, "blur": 18.0, "dim": 0.35}
    assert "endScreen" not in json.loads(write_comp(tmp_path / "c2.json", width=1080, height=1920, fps=30, duration=4,
                                                    layers=[], camera=[]).read_text("utf-8"))
    # Концовка без слоёв и камеры — моушн-проход всё равно нужен.
    motion = plan_motion({"end_screen": {"duration": 2}})
    assert motion and motion["end_screen"]["duration"] == 2.0 and visual_motion(motion)["end_screen"]
    assert plan_motion({}) is None


def test_settings_drop_end_screen_when_banner_deleted():
    layer = {"id": END_LAYER_ID, "type": "image", "start": 7, "end": 10, "props": {"asset_id": "5", "w": 960}}
    es = {"duration": 3, "asset_id": 5}
    assert normalize_render_settings({"layers": [layer], "end_screen": es})["end_screen"]["asset_id"] == 5
    assert normalize_render_settings({"layers": [], "end_screen": es})["end_screen"] is None
    assert normalize_render_settings({})["end_screen"] is None


# --------------------------------------------------------------------------- MCP и REST


def _ctx(store, plan):
    return ToolContext(store=store, clip_plan_id=plan["id"], on_first_change=lambda: snapshot(store, plan["id"], "агент"))


def _layers(store, plan) -> dict:
    return {l["id"]: l for l in store.get_clip_plan(plan["id"])["render_settings"].get("layers") or []}


def _fake_converter(monkeypatch, calls=None):
    monkeypatch.setattr(svg_webm, "_run", fake_run(calls))
    monkeypatch.setattr(svg_webm, "chromium_path", lambda: "chrome")
    monkeypatch.setattr(svg_webm, "converter_command", lambda: ["node", "svg-to-webm.mjs"])


def test_mcp_set_end_screen_place_replace_remove(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    calls: list = []
    _fake_converter(monkeypatch, calls)

    out, ok = run_tool(_ctx(store, plan), "set_end_screen", {**FIELDS, "blur": 24})
    assert ok, out
    assert out["start"] == pytest.approx(7.0) and out["duration"] == 3.0 and out["blur"] == 24.0
    assert "view_frame" in out["check"] and out["template"] == "live"
    layer = _layers(store, plan)[END_LAYER_ID]
    assert layer["type"] == "image" and layer["start"] == pytest.approx(7.0) and layer["end"] == pytest.approx(10.0)
    asset = store.get_montage_asset(int(layer["props"]["asset_id"]))
    assert asset["kind"] == "video" and asset["tags"] == "svg-webm"
    es = store.get_clip_plan(plan["id"])["render_settings"]["end_screen"]
    assert es["asset_id"] == asset["id"] and es["blur"] == 24.0 and es["handle"] == "sare<amura>"
    # webm длиннее концовки (анимация не начнётся заново на последнем кадре).
    args = calls[0]
    assert float(args[args.index("--duration") + 1]) == pytest.approx(3.5)
    assert store.count_clip_plan_history(plan["id"]) == 1

    # Повтор — одна концовка; тот же текст — тот же файл (кэш), конвертер не зовётся.
    out, ok = run_tool(_ctx(store, plan), "set_end_screen", {**FIELDS, "blur": 24})
    assert ok and out["cached"] and len(calls) == 1
    assert [l for l in _layers(store, plan).values() if l["id"] == END_LAYER_ID][0]["props"]["asset_id"] == str(asset["id"])
    out, ok = run_tool(_ctx(store, plan), "set_end_screen", {"template": "neon", "handle": "mura", "duration": 2})
    assert ok and out["start"] == pytest.approx(8.0) and len(calls) == 2

    # Редактор сохраняет настройки без end_screen — концовка остаётся.
    rs = dict(store.get_clip_plan(plan["id"])["render_settings"])
    rs.pop("end_screen")
    res = client.patch(f"/api/clip-plans/{plan['id']}/render-settings", json={"settings": rs})
    assert res.status_code == 200 and res.json()["render_settings"]["end_screen"]["template"] == "neon"

    # Снять — ни слоя, ни настройки; Ctrl+Z возвращает.
    out, ok = run_tool(_ctx(store, plan), "set_end_screen", {"remove": True})
    assert ok and out["removed"] and END_LAYER_ID not in _layers(store, plan)
    assert store.get_clip_plan(plan["id"])["render_settings"]["end_screen"] is None
    undo(store, plan["id"])
    assert END_LAYER_ID in _layers(store, plan)
    out, ok = run_tool(_ctx(store, plan), "set_end_screen", {"svg_asset_id": 999})
    assert not ok and "999" in out["error"]


def test_rest_end_screen_and_svg_to_webm(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    _fake_converter(monkeypatch)
    assert {t["id"] for t in client.get("/api/end-screen/templates").json()["templates"]} == {"live", "schedule", "neon"}
    res = client.post(f"/api/clip-plans/{plan['id']}/end-screen", json={"template": "schedule", "handle": "mura"})
    assert res.status_code == 200, res.text
    assert res.json()["layer_id"] == END_LAYER_ID
    assert client.delete(f"/api/clip-plans/{plan['id']}/end-screen").json()["removed"] is True
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="32"><rect width="10" height="10"/></svg>'
    first = client.post("/api/svg-to-webm", json={"svg": svg, "duration": 0.3, "label": "Свой"}).json()
    again = client.post("/api/svg-to-webm", json={"svg": svg, "duration": 0.3}).json()
    assert first["id"] == again["id"] and again["conversion"]["cached"] and first["label"] == "Свой"
    assert client.post("/api/svg-to-webm", json={"svg": "<html/>"}).status_code == 400
    # SVG из «Файлов для монтажа» — через MCP svg_to_webm.
    path = tmp_path / "mine.svg"
    path.write_text(svg.replace("10", "12"), "utf-8")
    src = store.create_montage_asset(kind="image", label="SVG", file_path=str(path), width=64, height=32)
    out, ok = run_tool(_ctx(store, plan), "svg_to_webm", {"asset_id": src["id"], "duration": 0.3})
    assert ok and out["asset_id"] != src["id"] and out["frames"] == 9
    # Своим SVG — концовка с баннером из него.
    out, ok = run_tool(_ctx(store, plan), "set_end_screen", {"svg_asset_id": src["id"]})
    assert ok and out["svg_asset_id"] == src["id"] and out["template"] is None


# --------------------------------------------------------------------------- бренд


def test_brand_cta_banner_style_shape():
    plain = brand_kit.normalize_brand({"name": "B", "cta": {"text": "Каталог"}})
    assert plain["cta"] == {"text": "Каталог", "url_text": "", "duration_sec": 3.0}  # прежняя форма
    banner = brand_kit.normalize_brand({"name": "B", "cta": {"text": "Каталог", "style": "banner", "template": "neon"}})
    assert banner["cta"]["style"] == "banner" and banner["cta"]["template"] == "neon" and banner["cta"]["blur"] == 18.0
    back = brand_kit.merge_brand(banner, {"cta": {"style": "plate"}})
    assert "style" not in back["cta"] and back["cta"]["text"] == "Каталог"


def test_apply_brand_banner_end_screen_instead_of_plate(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(brand_kit, "_subject_box", lambda store, plan_id: None)
    _fake_converter(monkeypatch)
    brand = store.create_brand("Мура", **{
        "font": "Rubik", "colors": {"accent": "#ff4fd8"},
        "cta": {"text": "Стримлю с 8 утра", "url_text": "заходи", "duration_sec": 3, "style": "banner",
                "template": "live", "blur": 22},
        "nameplate": {"platform": "twitch", "handle": "sareamura"},
    })
    out, ok = run_tool(_ctx(store, plan), "apply_brand", {"brand_id": brand["id"]})
    assert ok, out
    layers = _layers(store, plan)
    assert END_LAYER_ID in layers and brand_kit.CTA_ID not in layers
    es = store.get_clip_plan(plan["id"])["render_settings"]["end_screen"]
    assert es["blur"] == 22.0 and es["accent"] == "#ff4fd8" and es["handle"] == "sareamura" and es["font"] == "Rubik"
    assert es["text"] == "Стримлю с 8 утра — заходи"
    assert any("концовка-баннер" in c for c in out["changes"])
    # Повтор — без дублей.
    out, ok = run_tool(_ctx(store, plan), "apply_brand", {"brand_id": brand["id"]})
    assert ok and sum(1 for l in store.get_clip_plan(plan["id"])["render_settings"]["layers"] if l["id"] == END_LAYER_ID) == 1
    # Конвертер недоступен — призыв плашкой, с объяснением.
    monkeypatch.setattr(svg_webm, "chromium_path", lambda: None)
    monkeypatch.setattr(svg_webm, "cache_key", lambda svg, params: "other-key")
    out, ok = run_tool(_ctx(store, plan), "apply_brand", {"brand_id": brand["id"]})
    assert ok and brand_kit.CTA_ID in _layers(store, plan)
    assert any("не собралась" in w for w in out["warnings"])


# --------------------------------------------------------------------------- кадр агента (настоящие конвертер и компоновщик)


def test_view_frame_blurs_under_banner(tmp_path, monkeypatch):
    """Кадр агента через настоящие svg-to-webm и компоновщик: на концовке основа размыта, баннер сверху."""
    _require_ffmpeg()
    import subprocess

    from app.settings import settings
    from test_motion import _real_compositor

    if not svg_webm.chromium_path():
        pytest.skip("нет Chromium")
    compositor = _real_compositor()
    client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "motion_compositor", compositor)
    out, ok = run_tool(_ctx(store, plan), "set_end_screen", {"template": "neon", "handle": "mura", "dim": 0.5})
    assert ok, out

    def frame(t: float) -> list[int]:
        res = client.post(f"/api/clip-plans/{plan['id']}/motion/frame?t={t}")
        assert res.status_code == 200, res.text
        png = tmp_path / f"f{t}.png"
        png.write_bytes(res.content)
        # Левый верхний угол (вне баннера): средняя яркость и «резкость» по 8×8.
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(png), "-vf", "crop=iw/4:ih/8:0:0,scale=8:8,format=gray",
                              "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
        return list(raw)

    before, after = frame(5.0), frame(9.5)
    assert sum(after) < sum(before) * 0.8  # темнее (dim 0.5)
