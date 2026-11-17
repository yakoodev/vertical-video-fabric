"""Камера в раскладке «полоса» (meme) и крен roll (пилот 17.11).

* meme: наезд увеличивал весь кадр вместе с чёрными полями и фоном — теперь ``camera_regions`` даёт окно
  камеры = полоса видео (компоновщик рисует камеру только в нём, frontend/src/pages/workspace/layout.ts
  ``cameraRegions`` — то же для превью); картинки — node/motion.test.mjs («раскладка «полоса»»).
* roll: ключ камеры с креном хранится (±30°), старые ключи без него — ровно прежние.
"""

from __future__ import annotations

from app.motion import normalize_camera, validate_camera
from app.stream_layout import camera_regions, meme_box


def test_meme_camera_box_is_the_video_band():
    lay = {"mode": "meme", "video_y": 0.1}
    regions = camera_regions(lay, 1080, 1920, 1920, 1080)
    assert regions["pinned"] == []
    box = regions["box"]
    assert box == {"x": 0, "y": 192, "w": 1080, "h": 608}  # 1080 × 9/16 = 607.5 → 608, верх 0.1 × 1920
    # Обрезка полей исходника меняет пропорцию полосы (как в рендере: кроп → scale по ширине).
    cropped = camera_regions(lay, 1080, 1920, 1920, 1080, crop={"x": 0, "y": 0.1, "w": 1, "h": 0.8})["box"]
    assert cropped["h"] == 486
    # Видео шире кадра (video_scale 2) — полоса по ширине кадра, выше вдвое.
    wide = camera_regions({**lay, "video_scale": 2}, 1080, 1920, 1920, 1080)["box"]
    assert wide["x"] == 0 and wide["w"] == 1080 and wide["h"] == 1216
    # Видео закрывает весь кадр — камера как раньше, во весь кадр.
    assert meme_box({"mode": "meme", "video_y": 0, "video_scale": 4}, 1080, 1920, 1080, 1920) is None
    assert camera_regions({"mode": "single"}, 1080, 1920) is None


def test_render_preset_passes_source_crop():
    from app.render_presets import layout_camera_regions

    source = {"width": 1920, "height": 1080, "content_crop": {"x": 0, "y": 0.1, "w": 1, "h": 0.8}}
    regions = layout_camera_regions({"layout": {"mode": "meme", "video_y": 0.1}}, source, 1080, 1920)
    assert regions["box"]["h"] == 486


def test_camera_roll_kept_and_clamped():
    keys = normalize_camera([{"t": 0, "zoom": 1.2, "roll": 8}, {"t": 1, "zoom": 1, "roll": 99}, {"t": 2}])
    assert keys[0]["roll"] == 8 and keys[1]["roll"] == 30
    assert "roll" not in keys[2]  # без крена — ключ ровно прежний
    assert normalize_camera([{"t": 0, "roll": 0}]) == [{"t": 0.0, "zoom": 1.0, "x": 0.5, "y": 0.5}]
    problems = validate_camera([{"t": 0, "roll": -50}])
    assert any("roll" in p for p in problems)
