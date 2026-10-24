"""Квадрат 1:1 и 4:5 из того же монтажа (app/aspect.py): кадр, перенос слоёв, проверки."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from app.aspect import (
    check_aspect_source,
    layer_size,
    normalize_aspect,
    reframe_layers,
    reframe_motion,
    reframe_plan,
    scenes,
    settings_aspect,
)
from app.motion import normalize_camera, normalize_layers
from app.settings import settings

CANVAS = {"source_type": "canvas"}
UPLOAD = {"source_type": "upload"}


def _promo(start=0.0, end=6.0):
    """Промо-кадр 9:16: титр сверху, телефон по центру, касание по экрану, кнопка снизу, фон-звёзды."""
    return normalize_layers([
        {"id": "stars", "type": "image", "start": start, "end": end, "x": 0.5, "y": 0.5,
         "props": {"asset_id": 1, "w": 1080}},
        {"id": "title", "type": "text", "start": start, "end": end, "x": 0.5, "y": 0.15,
         "props": {"text": "Задачи команды —\nв одном месте", "size": 76, "maxWidth": 960}},
        {"id": "phone", "type": "video", "start": start, "end": end, "x": 0.5, "y": 0.53,
         "props": {"asset_id": 2, "w": 520, "device": "phone"}},
        {"id": "tap", "type": "shape", "start": start + 1, "end": start + 1.5, "x": 0.61, "y": 0.45,
         "props": {"kind": "ellipse", "w": 96, "h": 96}},
        {"id": "cta", "type": "text", "start": start, "end": end, "x": 0.5, "y": 0.885,
         "props": {"text": "Попробовать бесплатно →", "size": 58, "maxWidth": 980,
                   "bg": {"color": "#ff6a2b", "opacity": 1, "radius": 46, "padX": 50, "padY": 24}}},
        {"id": "boom", "type": "image", "start": start + 1, "end": start + 2, "x": 0.5, "y": 0.4,
         "props": {"asset_id": 3, "w": 900}, "keys": {"scale": [{"t": 0, "v": 0.2}, {"t": 1, "v": 1.4}],
                                                      "opacity": [{"t": 0, "v": 1}, {"t": 1, "v": 0}]}},
        {"id": "sfx", "type": "audio", "start": start, "end": start + 1, "props": {"asset_id": "sfx:freesound/pop.ogg"}},
    ])


SIZES = {"1": (1080, 1920), "2": (1170, 2532), "3": (900, 900)}


def _by_id(layers):
    return {layer["id"]: layer for layer in layers}


def _extent_px(layer, height, sizes=SIZES):
    """Верх и низ слоя в пикселях НОВОГО кадра (опорный размер × scale слоя)."""
    h = layer_size(layer, sizes)[1] * layer["scale"]
    return layer["y"] * height - h / 2, layer["y"] * height + h / 2


def test_aspect_names_and_settings():
    assert normalize_aspect(None) == "9:16" and normalize_aspect("") == "9:16"
    assert normalize_aspect("1:1") == "1:1" and normalize_aspect("square") == "1:1" and normalize_aspect("4x5") == "4:5"
    # 16:9 — теперь обычный формат (app/formats.py); мусор — ошибка / 9:16 в настройках.
    assert normalize_aspect("16:9") == "16:9" and normalize_aspect("landscape") == "16:9"
    with pytest.raises(ValueError):
        normalize_aspect("21:9")
    assert settings_aspect("21:9") == "9:16"
    from app.clip_settings import normalize_render_settings

    assert normalize_render_settings({})["format"] == "9:16"
    # Старое поле aspect читается как format; хранится одно поле format.
    assert normalize_render_settings({"aspect": "1:1"})["format"] == "1:1"
    assert "aspect" not in normalize_render_settings({"aspect": "1:1"})
    assert normalize_render_settings({"aspect": "1:1", "format": "16:9"})["format"] == "16:9"


def test_any_clip_gets_any_format():
    """Ограничение «не 9:16 — только холст» снято: формат у любого клипа."""
    assert check_aspect_source("1:1", CANVAS) == "1:1"
    assert check_aspect_source(None, UPLOAD) == "9:16"
    assert check_aspect_source("1:1", UPLOAD) == "1:1" and check_aspect_source("16:9", UPLOAD) == "16:9"
    with pytest.raises(ValueError):
        check_aspect_source("3:2", UPLOAD)


def test_preset_output_size_for_square():
    from app.render import _preset_with_output

    preset = _preset_with_output({"id": 1, "output_width": 1080, "output_height": 1920}, CANVAS, "1:1")
    assert (preset["output_width"], preset["output_height"], preset["aspect"]) == (1080, 1080, "1:1")
    assert _preset_with_output({"id": 1}, CANVAS, "4:5")["output_height"] == 1350
    assert _preset_with_output({"id": 1}, UPLOAD, "1:1")["output_width"] == 1080
    with pytest.raises(ValueError):
        _preset_with_output({"id": 1}, UPLOAD, "2:1")


def test_vertical_is_untouched():
    layers = _promo()
    assert reframe_layers(layers, 1080, 1920, SIZES) is layers


@pytest.mark.parametrize("height", [1080, 1350])
def test_square_fits_everything_without_new_overlaps(height):
    layers = _promo()
    plan = reframe_plan(layers, 1080, height, SIZES)
    assert plan["scale"] <= min(1.0, 1.25 * height / 1920)
    out = _by_id(reframe_layers(layers, 1080, height, SIZES))
    margin = 0.04 * height - 1
    title, phone, cta = (_extent_px(out[k], height) for k in ("title", "phone", "cta"))
    # Всё в кадре с полями…
    assert title[0] >= margin and cta[1] <= height - margin
    # …и титр не наехал на телефон, кнопка — тоже (в 9:16 не наезжали).
    assert title[1] < phone[0] and phone[1] < cta[0]
    # Касание осталось на той же точке экрана телефона: смещение × масштаб.
    s = plan["scale"]
    assert out["tap"]["y"] * height - out["phone"]["y"] * height == pytest.approx((0.45 - 0.53) * 1920 * s, abs=0.5)
    assert (out["tap"]["x"] - out["phone"]["x"]) * 1080 == pytest.approx((0.61 - 0.5) * 1080 * s, abs=0.5)
    # Шрифт и телефон уменьшены одним масштабом; фон во всю ширину — как был.
    assert out["title"]["scale"] == out["phone"]["scale"] == round(s, 4)
    assert out["stars"]["scale"] == 1.0
    # Вспышка (взрыв) рамку не раздувает, но масштабируется со всеми — и её ключи тоже.
    assert [k["v"] for k in out["boom"]["keys"]["scale"]] == [round(0.2 * s, 4), round(1.4 * s, 4)]
    assert out["sfx"] == _by_id(layers)["sfx"]


def test_keys_follow_the_same_mapping():
    layers = normalize_layers([
        {"id": "a", "type": "text", "start": 0, "end": 4, "x": 0.5, "y": 0.3, "props": {"text": "A", "size": 80},
         "keys": {"x": [{"t": 0, "v": 1.5}, {"t": 0.4, "v": 0.5}], "y": [{"t": 0, "v": 0.3}, {"t": 1, "v": 0.7}]}},
    ])
    out = reframe_layers(layers, 1080, 1080)[0]
    plan = reframe_plan(layers, 1080, 1080)
    s, c = plan["scale"], plan["scenes"][0]["center"]
    assert out["keys"]["x"][0]["v"] == pytest.approx(0.5 + 1.0 * s, abs=1e-3)  # за правым краем — и останется за ним
    assert out["keys"]["y"][1]["v"] == pytest.approx((540 + (0.7 * 1920 - c) * s) / 1080, abs=1e-3)
    assert out["keys"]["y"][0]["v"] == out["y"]


def test_end_card_scene_is_centered_and_phone_does_not_jump():
    """Сцены с почти той же рамкой — одна (телефон не прыгает на смене титра); концовка с
    логотипом — своя сцена и встаёт по центру квадрата."""
    first = _promo(0, 5)
    second = [{**layer, "id": layer["id"] + "2"} for layer in _promo(5, 10)]
    second[1] = {**second[1], "y": 0.16, "props": {**second[1]["props"], "text": "Другой титр"}}
    end_card = normalize_layers([
        {"id": "logo", "type": "image", "start": 9.8, "end": 14, "x": 0.5, "y": 0.27,
         "props": {"asset_id": 4, "w": 600}},
        {"id": "button", "type": "text", "start": 10, "end": 14, "x": 0.5, "y": 0.6,
         "props": {"text": "Открыть →", "size": 62, "bg": {"color": "#ff6a2b", "padX": 50, "padY": 24}}},
    ])
    layers = first + second + end_card
    sizes = {**SIZES, "4": (600, 400)}
    parts = scenes(layers, sizes)
    assert len(parts) == 2 and parts[1]["start"] == 9.8
    out = _by_id(reframe_layers(layers, 1080, 1080, sizes))
    assert out["phone"]["y"] == out["phone2"]["y"]  # одна сцена — один перенос
    top, _ = _extent_px(out["logo"], 1080, sizes)
    _, bottom = _extent_px(out["button"], 1080, sizes)
    assert abs((1080 - bottom) - top) < 4  # концовка по центру кадра


# ---------------------------------------------------------------- камера


def _zoom_scene():
    """Промо с наездом: титр сверху (стоит), телефон followCamera, камера 1 → 1.3 с центром в
    нижней части телефона, держит, отъезжает."""
    layers = normalize_layers([
        {"id": "title", "type": "text", "start": 0, "end": 5, "x": 0.5, "y": 0.15,
         "props": {"text": "Наезд на кнопку\nвнизу экрана", "size": 76, "maxWidth": 960}},
        {"id": "phone", "type": "video", "start": 0, "end": 5, "x": 0.5, "y": 0.62, "followCamera": True,
         "props": {"asset_id": 2, "w": 600, "device": "phone"}},
        {"id": "ring", "type": "shape", "start": 1.2, "end": 1.8, "x": 0.5, "y": 0.8, "followCamera": True,
         "props": {"kind": "ellipse", "w": 96, "h": 96},
         "keys": {"scale": [{"t": 0, "v": 0.4}, {"t": 0.6, "v": 2.2}], "opacity": [{"t": 0, "v": 1}, {"t": 0.6, "v": 0}]}},
    ])
    camera = normalize_camera([
        {"t": 0, "zoom": 1, "x": 0.5, "y": 0.5},
        {"t": 1, "zoom": 1.3, "x": 0.5, "y": 0.55, "ease": "inOutCubic"},
        {"t": 3, "zoom": 1.3, "x": 0.5, "y": 0.55},
        {"t": 4, "zoom": 1, "x": 0.5, "y": 0.5},
    ])
    return layers, camera


def _camera_at(camera, t):
    """Вид камеры как в движке (camera.ts, без тряски): одна доля p на все поля — ease меняет
    только скорость, а не набор видов, поэтому для проверки рамки хватает линейной доли."""
    if t <= camera[0]["t"]:
        a = b = camera[0]
        p = 0.0
    elif t >= camera[-1]["t"]:
        a = b = camera[-1]
        p = 0.0
    else:
        a, b = next((a, b) for a, b in zip(camera, camera[1:]) if a["t"] <= t < b["t"])
        p = (t - a["t"]) / (b["t"] - a["t"])
    zoom = max(1.0, a["zoom"] + (b["zoom"] - a["zoom"]) * p)
    half = 0.5 / zoom
    x = min(1 - half, max(half, a["x"] + (b["x"] - a["x"]) * p))
    y = min(1 - half, max(half, a["y"] + (b["y"] - a["y"]) * p))
    return zoom, x, y


def _phone_on_screen(layer, camera, t, height):
    """Верх и низ телефона followCamera на кадре после камеры (px)."""
    zoom, _x, y = _camera_at(camera, t)
    top, bottom = _extent_px(layer, height)
    return height / 2 + zoom * (top - y * height), height / 2 + zoom * (bottom - y * height)


@pytest.mark.parametrize("height", [1080, 1350])
def test_camera_zoom_keeps_follow_camera_phone_inside_square(height):
    """Баг 574-го клипа: наезд на низ телефона followCamera в квадрате уводил низ телефона
    за кадр. Теперь камера переносится тем же преобразованием, что и слои: телефон целиком
    в кадре на всём интервале, на удержании — с полями 4 %, а наезд остался 1.3."""
    layers, camera = _zoom_scene()
    framed, cam = reframe_motion(layers, camera, 1080, height, SIZES)
    phone = _by_id(framed)["phone"]
    # Старое поведение (слои без учёта камеры, камера как в 9:16) выталкивало низ телефона за кадр.
    old = _by_id(reframe_layers(layers, 1080, height, SIZES))["phone"]
    assert max(_phone_on_screen(old, camera, t / 50, height)[1] for t in range(0, 251)) > height
    for step in range(0, 251):
        t = step / 50
        top, bottom = _phone_on_screen(phone, cam, t, height)
        assert top >= -0.5 and bottom <= height + 0.5, (t, top, bottom)
        if 1 <= t <= 3:
            assert top >= 0.04 * height - 1 and bottom <= 0.96 * height + 1, (t, top, bottom)
    # Наезд заметен: zoom не урезан, масштаб из-за камеры уменьшился не больше чем в 1.25 раза.
    assert [k["zoom"] for k in cam] == pytest.approx([1, 1.3, 1.3, 1], abs=1e-3)
    plain = reframe_plan(layers, 1080, height, SIZES)["scale"]
    zoomed = reframe_plan(layers, 1080, height, SIZES, camera)["scale"]
    assert plain / 1.25 - 1e-3 <= zoomed < plain
    # Ключи остались ключами: время и изгиб те же, 9:16 — камера как есть.
    assert [k["t"] for k in cam] == [0, 1, 3, 4] and cam[1]["ease"] == "inOutCubic"
    assert reframe_motion(layers, camera, 1080, 1920, SIZES) == (layers, camera)


def test_camera_center_stays_on_the_same_content_point():
    """Наезд на точку, вокруг которой хватает места, — в квадрате та же точка содержимого
    в центре кадра (как в 9:16), а не та же доля квадрата."""
    layers = normalize_layers([
        {"id": "title", "type": "text", "start": 0, "end": 4, "x": 0.5, "y": 0.15,
         "props": {"text": "Титр", "size": 76}},
        {"id": "phone", "type": "video", "start": 0, "end": 4, "x": 0.5, "y": 0.55,
         "props": {"asset_id": 2, "w": 600, "device": "phone"}},
        {"id": "badge", "type": "shape", "start": 0, "end": 4, "x": 0.42, "y": 0.47, "followCamera": True,
         "props": {"kind": "rect", "w": 120, "h": 60}},
    ])
    camera = normalize_camera([{"t": 0, "zoom": 1}, {"t": 1, "zoom": 1.5, "x": 0.42, "y": 0.47}])
    framed, cam = reframe_motion(layers, camera, 1080, 1080, SIZES)
    badge = _by_id(framed)["badge"]
    zoom, x, y = _camera_at(cam, 2)
    assert zoom == pytest.approx(1.5)
    # Значок под центром камеры — в центре квадрата (в 9:16 он тоже в центре кадра).
    assert 540 + zoom * (badge["x"] * 1080 - x * 1080) == pytest.approx(540, abs=1.5)
    assert 540 + zoom * (badge["y"] * 1080 - y * 1080) == pytest.approx(540, abs=1.5)


def test_compositor_square_zoom_keeps_phone_bottom_in_frame(tmp_path):
    """Пиксели кадра компоновщика 1080×1080 на удержании наезда и на его середине: экран
    телефона (красная запись) не доходит до низа кадра, под ним — корпус и фон."""
    if not shutil.which("ffmpeg"):
        pytest.skip("нет ffmpeg")
    from app.motion_render import CompositorMissing, compositor_command

    try:
        compositor_command()
    except CompositorMissing:
        pytest.skip("компоновщик не собран (npm run build:motion)")
    from tools.aspect_frames import render_frames

    phone = tmp_path / "phone.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0xff0000:s=390x844:r=10:d=6", "-c:v",
                    "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(phone)], check=True)
    layers, camera = _zoom_scene()
    layers = [layer for layer in layers if layer["id"] == "phone"]
    made = render_frames(layers, {"2": str(phone)}, {"color": "#101010"}, [0.6, 2.0], tmp_path / "out", ["1:1"],
                         "zoom", camera)
    for png in made:
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(png), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                             capture_output=True, check=True).stdout
        assert len(raw) == 1080 * 1080 * 3
        red_rows = [
            row for row in range(0, 1080, 4)
            if any(raw[(row * 1080 + x) * 3] > 180 and raw[(row * 1080 + x) * 3 + 1] < 80 for x in range(300, 780, 20))
        ]
        assert red_rows, png.name
        # Низ экрана выше нижнего края на корпус и поле: последние 30 px — без записи.
        assert max(red_rows) < 1080 - 30, (png.name, max(red_rows))


def test_set_aspect_tool_saves_for_canvas_and_regular_clip(tmp_path, monkeypatch):
    from app.agent_tools import ToolContext, run_tool
    from app.manual_clips import create_manual_clip
    from test_render import _store

    store = _store(tmp_path, monkeypatch)
    plans = {}
    for kind in ("canvas", "upload"):
        path = settings.source_dir / f"{kind}.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
        source = store.create_source(kind, path, duration_sec=180, width=1080, height=1920, status="ready")
        plans[kind] = create_manual_clip(store, source["id"], kind, [{"start_sec": 0.0, "end_sec": 12.0}], "")
    out, ok = run_tool(ToolContext(store=store, clip_plan_id=plans["canvas"]["id"]), "set_aspect", {"aspect": "1:1"})
    assert ok, out
    assert '"1080×1080"' in out or "1080×1080" in str(out)
    assert store.get_clip_plan(plans["canvas"]["id"])["render_settings"]["format"] == "1:1"
    # Клип с исходником — теперь тоже (старое имя set_aspect и новое set_format).
    out, ok = run_tool(ToolContext(store=store, clip_plan_id=plans["upload"]["id"]), "set_aspect", {"aspect": "1:1"})
    assert ok, out
    out, ok = run_tool(ToolContext(store=store, clip_plan_id=plans["upload"]["id"]), "set_format", {"format": "16:9"})
    assert ok and "1920×1080" in str(out), out
    assert store.get_clip_plan(plans["upload"]["id"])["render_settings"]["format"] == "16:9"
    out, ok = run_tool(ToolContext(store=store, clip_plan_id=plans["upload"]["id"]), "set_format", {"format": "2:1"})
    assert not ok
    # Неверный формат рендера — понятная ошибка до статуса «rendering».
    from app.render import ClipRenderService

    with pytest.raises(ValueError, match="format"):
        ClipRenderService(store).render_clip_plan(plans["upload"]["id"], aspect="2:1")
    assert store.get_clip_plan(plans["upload"]["id"])["status"] != "rendering"


def test_qc_accepts_square_and_four_five():
    from app.render_qc import parse_qc

    def codes(width, height):
        return {i["code"] for i in parse_qc("", duration_sec=20, width=width, height=height)["issues"]}

    assert "size" not in codes(1080, 1080) and "size" not in codes(1080, 1350) and "size" not in codes(1920, 1080)
    assert "size" in codes(720, 1280)


# ---------------------------------------------------------------- компоновщик глазами


def test_compositor_draws_square_frame(tmp_path):
    """Настоящий кадр компоновщика 1080×1080 (если он собран): фон на весь кадр, слои
    перенесены. PNG — tools/aspect_frames.py, тут — что кадр нужного размера и не пустой."""
    if not shutil.which("ffmpeg"):
        pytest.skip("нет ffmpeg")
    from app.motion_render import CompositorMissing, compositor_command

    try:
        compositor_command()
    except CompositorMissing:
        pytest.skip("компоновщик не собран (npm run build:motion)")
    from tools.aspect_frames import render_frames, synthetic_layers

    band = tmp_path / "band.png"
    phone = tmp_path / "phone.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x222222:s=1080x760", "-frames:v", "1",
                    str(band)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=390x844:r=10:d=3", "-c:v",
                    "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(phone)], check=True)
    made = render_frames(synthetic_layers(), {"901": str(phone), "902": str(band)},
                         {"kind": "aurora", "colors": [], "speed": 1, "intensity": 0.7}, [1.0], tmp_path / "out",
                         ["1:1"], "t")
    assert len(made) == 1 and made[0].is_file()
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0",
                            str(made[0])], capture_output=True, text=True)
    assert probe.stdout.strip() == "1080,1080"
    assert Path(made[0]).stat().st_size > 50_000  # нарисован фон и слои, а не пустота


def test_real_square_render_of_canvas_through_api(tmp_path, monkeypatch):
    """Холст целиком через API: рендер {"aspect": "1:1"} → файл 1080×1080 с фоном и слоями,
    настройки клипа не меняются; неизвестный формат — 400 с понятной причиной."""
    from test_api_pipeline import _client, _require_ffmpeg

    _require_ffmpeg()
    from app.motion_render import CompositorMissing, compositor_command

    try:
        compositor_command()
    except CompositorMissing:
        pytest.skip("компоновщик не собран (npm run build:motion)")
    import app.canvas as canvas

    monkeypatch.setattr(canvas, "CANVAS_WIDTH", 108)
    monkeypatch.setattr(canvas, "CANVAS_HEIGHT", 192)
    client, store = _client(tmp_path, monkeypatch)
    plan = client.post("/api/canvas-clips", json={"duration": 5, "background": "aurora"}).json()
    title = {"type": "text", "start": 0, "end": 5, "x": 0.5, "y": 0.15,
             "props": {"text": "Квадрат из того же монтажа", "size": 80, "maxWidth": 960}}
    from app.clip_settings import normalize_render_settings

    store.set_clip_plan_render_settings(plan["id"], normalize_render_settings(
        {**plan["render_settings"], "layers": [title]}))
    resp = client.post(f"/api/clip-plans/{plan['id']}/render", json={"aspect": "1:1"})
    assert resp.status_code == 200, resp.text
    clip = resp.json()
    assert clip["status"] == "succeeded", clip.get("error")
    assert (clip["width"], clip["height"]) == (1080, 1080)
    assert not (clip.get("error") or "")  # моушн лёг, без пометок
    assert store.get_clip_plan(plan["id"])["render_settings"]["format"] == "9:16"  # формат только этого рендера

    path = settings.source_dir / "real.mp4"
    path.write_bytes(b"x")
    source = store.create_source("upload", path, duration_sec=60, width=1920, height=1080, status="ready")
    from app.manual_clips import create_manual_clip

    regular = create_manual_clip(store, source["id"], "обычный", [{"start_sec": 0.0, "end_sec": 8.0}], "")
    resp = client.post(f"/api/clip-plans/{regular['id']}/render", json={"format": "2:1"})
    assert resp.status_code == 400 and "format" in resp.json()["detail"]
