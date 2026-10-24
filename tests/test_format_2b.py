"""Форматы вывода, этап 2B: субтитры, безопасные зоны площадок, линт, обложки, баннер, раскладка.

9:16 закрыт эталонами (tests/test_format_golden.py, tests/test_format_golden_2b.py); здесь — что
1:1, 4:5 и 16:9 получают своё: кегль от короткой стороны, строку по ширине кадра, отступ по зоне,
предупреждения про края и водяной знак YouTube, обложку 1920×1080, отказ заготовки промо в 16:9.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from app import agent_tools, safe_zones
from app.formats import FORMAT_IDS, frame_size
from app.settings import settings
from app.subtitles.ass import LANDSCAPE_LINE_SHARE, SUBTITLE_SIDE_MARGIN, format_style, write_ass_subtitles
from format_golden_cases import run_case
from test_format_golden_2b import WORDS, _result

PROFILE = {"font_size": 74, "uppercase": 1, "max_words_per_line": 4, "outline_width": 6, "shadow": 1, "margin_v": 230}


def _ass(text: str) -> dict:
    """PlayRes, поля стиля Karaoke и строки диалогов из текста ASS."""
    res_x = int(re.search(r"PlayResX: (\d+)", text).group(1))
    res_y = int(re.search(r"PlayResY: (\d+)", text).group(1))
    style = re.search(r"^Style: Karaoke,(.*)$", text, re.M).group(1).split(",")
    lines = []
    for event in re.findall(r"^Dialogue: .*?,,0,0,0,,(.*)$", text, re.M):
        for line in event.split(r"\N"):
            lines.append(re.sub(r"\{[^}]*\}", "", line))
    return {"res": (res_x, res_y), "size": int(style[1]), "outline": float(style[15]), "shadow": float(style[16]),
            "margin_l": int(style[18]), "margin_r": int(style[19]), "margin_v": int(style[20]), "lines": lines}


# ---------------------------------------------------------------- субтитры ASS


@pytest.mark.parametrize("fmt", FORMAT_IDS)
def test_ass_style_per_format(fmt, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "subtitle_dir", tmp_path)
    w, h = frame_size(fmt)
    path = write_ass_subtitles(_result(), PROFILE, tmp_path / "a.ass", width=w, height=h, fmt=fmt)
    got = _ass(path.read_text(encoding="utf-8"))
    assert got["res"] == (w, h)
    # Кегль и обводка — от короткой стороны (у всех форматов 1080: те же px, что в вертикали).
    assert got["size"] == 74 and got["outline"] == 6
    zone = safe_zones.safe_zone(fmt)
    if fmt == "16:9":
        side = round(w * (1 - LANDSCAPE_LINE_SHARE) / 2)
        assert got["margin_l"] == got["margin_r"] == side
        assert (w - 2 * side) / w == pytest.approx(LANDSCAPE_LINE_SHARE, abs=0.01)
    else:
        assert got["margin_l"] == got["margin_r"] == SUBTITLE_SIDE_MARGIN
    if fmt == "9:16":
        assert got["margin_v"] == 230  # вертикаль — отступ стиля как есть
    else:
        assert got["margin_v"] >= round((1 - zone["captions_bottom"]) * h)
    # Строка не шире поля текста: ширина символа ~0.66 кегля у капса.
    usable = w - 2 * got["margin_l"]
    assert all(len(line) * 74 * 0.66 <= usable + 1 for line in got["lines"]), got["lines"]


def test_ass_9x16_with_format_is_identical(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "subtitle_dir", tmp_path)
    a = write_ass_subtitles(_result(), PROFILE, tmp_path / "a.ass", width=1080, height=1920)
    b = write_ass_subtitles(_result(), PROFILE, tmp_path / "b.ass", width=1080, height=1920, fmt="9:16")
    assert a.read_text(encoding="utf-8") == b.read_text(encoding="utf-8")
    assert format_style(PROFILE, "9:16", 1080, 1920) == (PROFILE, SUBTITLE_SIDE_MARGIN)


def test_landscape_lines_are_wider_but_not_full_width(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "subtitle_dir", tmp_path)
    profile = {**PROFILE, "max_words_per_line": 8}
    vertical = _ass(write_ass_subtitles(_result(), profile, tmp_path / "v.ass", width=1080, height=1920).read_text(
        encoding="utf-8"))
    landscape = _ass(write_ass_subtitles(_result(), profile, tmp_path / "l.ass", width=1920, height=1080,
                                         fmt="16:9").read_text(encoding="utf-8"))
    assert max(map(len, landscape["lines"])) > max(map(len, vertical["lines"]))
    assert max(map(len, landscape["lines"])) * 74 * 0.66 <= 1920 * LANDSCAPE_LINE_SHARE + 1


def test_ass_bottom_margin_never_under_safe_zone(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "subtitle_dir", tmp_path)
    for fmt in ("1:1", "4:5", "16:9"):
        w, h = frame_size(fmt)
        style, _side = format_style({**PROFILE, "margin_v": 10}, fmt, w, h)
        assert style["margin_v"] == round((1 - safe_zones.safe_zone(fmt)["captions_bottom"]) * h)


def test_render_16x9_subtitles_use_frame_of_format(tmp_path, monkeypatch):
    got = run_case("crop_focus", tmp_path, monkeypatch, extra={"format": "16:9"}, frame=(1920, 1080),
                   subtitles={**PROFILE, "sub_pos_pct": 18.0})
    assert got["status"] == "succeeded", got.get("error")
    ass = _ass(next(c[1] for c in got["calls"] if c[0] == "ass"))
    assert ass["res"] == (1920, 1080)
    assert ass["margin_v"] == round(0.18 * 1080)  # та же доля высоты кадра 16:9
    assert ass["margin_l"] == round(1920 * (1 - LANDSCAPE_LINE_SHARE) / 2)


def test_one_off_format_render_keeps_subtitle_share(tmp_path, monkeypatch):
    """Клип сохранён 9:16 (sub_pos 18 % → 346 px), рендер разово в 16:9 — та же доля, 194 px."""
    got = run_case("crop_focus", tmp_path, monkeypatch, frame=(1920, 1080), aspect="16:9",
                   subtitles={**PROFILE, "sub_pos_pct": 18.0})
    assert got["status"] == "succeeded", got.get("error")
    ass = _ass(next(c[1] for c in got["calls"] if c[0] == "ass"))
    assert ass["res"] == (1920, 1080) and abs(ass["margin_v"] - 0.18 * 1080) <= 1


def test_profile_margin_without_override_scales_to_format(tmp_path, monkeypatch):
    got = run_case("crop_focus", tmp_path, monkeypatch, extra={"format": "1:1"}, frame=(1080, 1080),
                   subtitles={**PROFILE, "sub_pos_pct": None})
    ass = _ass(next(c[1] for c in got["calls"] if c[0] == "ass"))
    assert ass["res"] == (1080, 1080) and ass["margin_v"] == round(230 * 1080 / 1920)


# ---------------------------------------------------------------- безопасные зоны и линт


def test_safe_zones_table():
    assert safe_zones.safe_zone(None) == safe_zones.safe_zone("9:16")
    vertical = safe_zones.safe_zone("9:16")
    assert (vertical["top"], vertical["bottom"]) == (agent_tools.SAFE_TOP, agent_tools.SAFE_BOTTOM)
    assert vertical["captions_bottom"] == 0.82 and not safe_zones.checks_sides("9:16")
    for fmt in ("1:1", "4:5"):
        zone = safe_zones.safe_zone(fmt)
        assert (zone["top"], zone["bottom"], zone["left"], zone["right"]) == (0.05, 0.95, 0.05, 0.95)
        assert "Instagram" in zone["platforms"] and zone["watermark"] is None
    yt = safe_zones.safe_zone("16:9")
    assert (yt["top"], yt["bottom"], yt["left"], yt["right"]) == (0.05, 0.95, 0.05, 0.95)
    assert yt["platforms"] == "YouTube" and yt["watermark"]
    assert safe_zones.in_watermark("16:9", 0.88, 0.85, 0.95, 0.9)
    assert not safe_zones.in_watermark("16:9", 0.4, 0.85, 0.6, 0.9)
    assert not safe_zones.in_watermark("9:16", 0.88, 0.85, 0.95, 0.9)
    described = safe_zones.describe("16:9")
    assert described["left"] == 0.05 and described["watermark"]
    assert set(safe_zones.describe("9:16")) == {"top", "bottom", "platforms", "note"}


def _title(x: float, y: float, text: str = "Как это работает", **props) -> dict:
    return {"id": f"t{x}-{y}", "type": "text", "start": 0, "end": 3, "x": x, "y": y,
            "props": {"text": text, "size": 80, **props}}


def test_lint_16x9_title_at_edges():
    edge = _title(0.03, 0.5)
    top = _title(0.5, 0.02)
    mark = _title(0.92, 0.88, text="Подпишись")
    ok = _title(0.5, 0.5)
    warnings = agent_tools.lint_layers([edge, top, mark, ok], 6.0, fmt="16:9")
    text = " | ".join(warnings)
    assert "левее безопасной зоны 0.05" in text and "16:9, YouTube" in text
    assert "выше безопасной зоны 0.05" in text
    assert "водяной знак" in text
    assert not [w for w in warnings if w.startswith(f"{ok['id']}:") and "перегруз" not in w]
    # Тот же титр у края в вертикали — бока не проверяем (как всегда), а 0.02 сверху — за шапкой.
    vertical = " | ".join(agent_tools.lint_layers([edge, top, mark, ok], 6.0))
    assert "левее" not in vertical and "водяной" not in vertical
    assert "выше безопасной зоны 0.10" in vertical


def test_lint_16x9_bottom_and_right_edges_suggest_fix():
    low = _title(0.5, 0.97)
    right = _title(0.97, 0.4)
    warnings = agent_tools.lint_layers([low, right], 6.0, fmt="16:9")
    text = " | ".join(warnings)
    assert "нижние 5% кадра" in text and "подними до y≤" in text
    assert "правее безопасной зоны 0.95" in text and "сдвинь до x≤" in text


def test_lint_square_uses_square_height():
    # Две строки по 96 px на y 0.1: в 1920 верх на 0.042 (выше 0.10), в 1080 — на 0.0 (выше 0.05).
    hook = _title(0.5, 0.1, text="ДВЕ\nСТРОКИ", size=96)
    vertical = agent_tools.lint_layers([hook], 6.0)
    square = agent_tools.lint_layers([hook], 6.0, fmt="1:1")
    assert "выше безопасной зоны 0.10" in vertical[0]
    assert "выше безопасной зоны 0.05" in square[0] and "у края кадра превью ленты" in square[0]


def test_lint_captions_zone_per_format():
    caps = {"id": "caps", "type": "captions", "start": 0, "end": 5, "x": 0.5, "y": 0.9, "props": {"style": "hormozi"}}
    assert any("ниже безопасной зоны 0.93" in w for w in agent_tools.lint_layers([caps], 5.0, fmt="16:9"))
    assert not any("ниже безопасной зоны" in w for w in agent_tools.lint_layers([caps], 5.0))
    two = [caps, {**caps, "id": "caps2", "y": 0.75}]
    # 0.15 по высоте: в 1920 строки расходятся, в 1080 (16:9) — та же пара px ближе порога.
    assert not agent_tools.caption_overlap_warnings(two)
    assert agent_tools.caption_overlap_warnings(two, ref_h=1080.0)


def test_promo_lint_geometry_in_16x9_frame():
    from app.promo_lint import frame, frame_of, promo_warnings

    phone = {"id": "ph", "type": "video", "start": 0, "end": 6, "x": 0.5, "y": 0.5,
             "props": {"device": "phone", "w": 560, "asset_id": 1}}
    assert frame() == (1080.0, 1920.0)
    with frame_of("16:9"):
        assert frame() == (1920.0, 1080.0)
    assert frame() == (1080.0, 1920.0)
    # Телефон во весь рост: в вертикали в кадре, в 16:9 (высота 1080) — за краем.
    assert not any("за краем" in w for w in promo_warnings([phone], 6.0, canvas=True))
    assert any("за краем" in w for w in promo_warnings([phone], 6.0, canvas=True, fmt="16:9"))


def test_cover_share_and_decor_by_format():
    rect = {"type": "shape", "props": {"kind": "rect", "w": 900, "h": 700, "fill": "#000000"}}
    assert agent_tools.cover_share(rect) == pytest.approx(900 * 700 / (1080 * 1920))
    assert agent_tools.cover_share(rect, "1:1") == pytest.approx(900 * 700 / (1080 * 1080))
    assert agent_tools.covering_problems([rect], fmt="1:1") and not agent_tools.covering_problems([rect])
    wide = {"type": "image", "opacity": 0.5, "props": {"w": 1200}}
    assert agent_tools.is_decor_layer(wide) and not agent_tools.is_decor_layer(wide, ref_w=1920.0)


def test_frame_view_and_subtitle_zone_by_format():
    from app.agent_tools import _frame_view, _subtitle_zone

    assert _frame_view({}) == {"width": 1080, "height": 1920, "safe_zone": {"top": 0.1, "bottom": 0.8}}
    native = _frame_view({"format": "16:9", "layers_format": "16:9"})
    assert (native["width"], native["height"]) == (1920, 1080)
    assert native["safe_zone"]["left"] == 0.05 and native["safe_zone"]["watermark"]
    mixed = _frame_view({"format": "16:9"})
    assert (mixed["width"], mixed["height"]) == (1080, 1920) and mixed["render_format"] == "16:9"
    assert mixed["render_safe_zone"]["platforms"] == "YouTube" and "layers_format" in mixed["note"]
    assert _subtitle_zone({"subs_on": True, "sub_pos_pct": 12.0}) == {"y_from": 0.76, "y_to": 0.88}
    yt = _subtitle_zone({"subs_on": True, "sub_pos_pct": 2.0, "format": "16:9"})
    assert yt["y_to"] == 0.93 and yt["y_from"] == pytest.approx(0.93 - 0.12 * 1920 / 1080, abs=0.001)


def test_text_band_and_cam_strip_by_format():
    split = {"mode": "split", "ratio": 0.36, "cam_pos": "top", "gap": 12}
    assert agent_tools.text_band({"layers_format": "16:9", "layout": split}) == (0.05, 0.95)  # в 16:9 полосы нет
    assert agent_tools.cam_strip(split, "16:9") is None
    square = agent_tools.cam_strip(split, "1:1")
    assert square == (0.0, round(0.36 + 12 / 1080, 4))  # зазор — 12 px по короткой стороне
    assert agent_tools.text_band({"layers_format": "1:1", "layout": split})[0] == round(square[1] + 0.015, 4)


def test_caption_y_for_landscape_layers():
    from app.auto_edit import caption_y_for

    assert caption_y_for({}) == 0.72
    assert caption_y_for({"format": "16:9"}) == 0.72  # слои вертикальные — место вертикальное
    assert caption_y_for({"format": "16:9", "layers_format": "16:9"}) == round(0.93 - 0.12, 3)


# ---------------------------------------------------------------- бренд, вставки, вебка


def test_brand_logo_and_cta_in_16x9():
    from app import brand_kit

    brand = {"cta": {"text": "Подпишись", "url_text": "t.me/x", "duration_sec": 3.0}, "font": "Nunito",
             "colors": {"text": "#ffffff", "accent": "#ff6a1f", "plate": "#111111"}, "logo_corner": "top-right"}
    logo = brand_kit.build_logo(brand, {"id": 5, "width": 400, "height": 200}, 1.0, 4.0, "16:9")
    half_w = 150 / 2 / 1920
    assert logo["x"] + half_w <= 0.95 + 1e-6 and logo["y"] - 75 / 2 / 1080 >= 0.05
    plate = brand_kit.build_cta(brand, 1.0, 4.0, 0.6, True, "16:9")[0]
    assert plate["props"]["w"] == 1920
    y, fits = brand_kit.cta_y(0.15, [], "16:9")
    assert fits and y + 0.075 <= 0.95


def test_fit_pip_insert_in_square_frame():
    from app.agent_tools import _fit_pip_insert

    render = {"format": "1:1", "layers": [{"id": "c", "type": "captions", "start": 0, "end": 9, "y": 0.6}]}
    entry = {"at": 1.0, "duration": 2.0, "scale": 0.6, "y": 0.5}
    note = _fit_pip_insert(render, entry, {"width": 800, "height": 800})
    assert note and "уменьшил" in note
    height = entry["scale"] * 1080 * 1.0 / 1080
    cap_top = 0.6 - agent_tools.CAPTION_HALF * 1920 / 1920  # слои 9:16 — опорный 1920
    assert entry["y"] + height / 2 <= cap_top - 0.015 + 1e-6


def test_facecam_split_warnings_by_format():
    split = {"mode": "split", "ratio": 0.36, "cam": {"x": 0.72, "y": 0.04, "w": 0.26, "h": 0.3}}
    source = {"width": 1920, "height": 1080}
    assert agent_tools.facecam_warnings(split, source, "16:9") == []  # окошко в углу — не режется
    vertical = agent_tools.facecam_warnings(split, source)
    square = agent_tools.facecam_warnings(split, source, "1:1")
    # Вебка 1.54:1 в полосе 0.36: в 9:16 полоса 1.56:1 — впору; в квадрате 2.78:1 — срежет 45 % высоты.
    assert vertical == [] and square and "ratio 0.36" not in square[0] and "срежется" in square[0]


def test_stream_layout_px_by_short_side():
    from app import stream_layout

    assert stream_layout.frame_px_scale(1080, 1920) == 1.0
    assert stream_layout.frame_px_scale(720, 1280) == pytest.approx(1280 / 1920)
    assert stream_layout.frame_px_scale(1920, 1080) == 1.0 and stream_layout.frame_px_scale(1080, 1080) == 1.0
    assert stream_layout.scaled_px(6, 1080, 1920) == 6  # рамка 6 px в 16:9 — 6 px, а не 4
    assert stream_layout.scaled_px(6, 1080) == 4  # без ширины — как раньше
    split = stream_layout.normalize_layout({"mode": "split", "ratio": 0.36, "gap": 12})
    assert stream_layout.split_geometry(split, 1080, 1080)[1] == 12
    assert stream_layout.split_geometry(split, 1920, 1080) == stream_layout.split_geometry(split, 1920)


def test_window_share_by_format():
    from app.video_crop import window_share

    src = {"width": 1920, "height": 1080}
    assert window_share(src, *frame_size("9:16")) == window_share(src)
    assert window_share(src, *frame_size("1:1")) == pytest.approx(1080 / 1920, abs=0.001)
    assert window_share(src, *frame_size("16:9")) == 1.0


# ---------------------------------------------------------------- баннер и обложка


def test_banner_16x9_height_share_and_width_cap(tmp_path, monkeypatch):
    got = run_case("crop_focus", tmp_path, monkeypatch, extra={"format": "16:9"}, frame=(1920, 1080),
                   banner={"position": "top", "height_pct": 14.0, "pos_pct": 4.0})
    assert got["status"] == "succeeded", got.get("error")
    graph = " ".join(" ".join(c[1]) for c in got["calls"] if c[0] == "ffmpeg")
    assert f"iw*{round(0.14 * 1080)}/ih" in graph and f"min({round(1920 * 0.9)}," in graph
    assert f"overlay=(main_w-overlay_w)/2:{round(0.04 * 1080)}" in graph
    vertical = run_case("crop_focus", tmp_path / "v", monkeypatch,
                        banner={"position": "top", "height_pct": 14.0, "pos_pct": 4.0})
    vgraph = " ".join(" ".join(c[1]) for c in vertical["calls"] if c[0] == "ffmpeg")
    assert f"scale=-1:{round(0.14 * 1920)}:flags=lanczos" in vgraph and "min(" not in vgraph


def _cover_store(tmp_path, monkeypatch, fmt):
    sys.path.insert(0, str(Path(__file__).parent))
    from test_render import _store

    store = _store(tmp_path, monkeypatch)
    source_path = settings.source_dir / "cover.mp4"
    source_path.write_bytes(b"src")
    source = store.create_source("upload", source_path, duration_sec=60, width=1920, height=1080, fps=30, status="ready")
    analysis = store.create_ai_analysis(source["id"], "mock", status="succeeded")
    seg = store.create_ai_segment(analysis["id"], {"start_sec": 10, "end_sec": 16, "title": "A"})
    plan = store.create_clip_plan(source["id"], analysis["id"], "План", segment_ids=[seg["id"]])
    store.set_clip_plan_render_settings(plan["id"], {"format": fmt})
    return store, plan


def test_cover_compose_16x9_is_youtube_thumbnail(tmp_path, monkeypatch):
    from app import cover_compose

    store, plan = _cover_store(tmp_path, monkeypatch, "16:9")
    calls = []

    def fake_frame(store_, plan_id, t, layers=None, camera=None, **kw):
        calls.append({"layers": layers, **kw})
        return b"png"

    def fake_run(args, **kw):
        calls.append({"ffmpeg": list(args)})
        Path(args[-1]).write_bytes(b"jpg")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("app.motion_render.render_frame_png", fake_frame)
    monkeypatch.setattr(cover_compose.subprocess, "run", fake_run)
    cover_compose.compose_cover(store, plan["id"], 2.0, "Невероятный момент на стриме", emoji="😱", y=0.02)
    frame_call, ffmpeg_call = calls
    assert frame_call["layers_format"] == "16:9"
    title = frame_call["layers"][0]
    assert title["props"]["maxWidth"] == 1600 and title["props"]["size"] == 150
    half = agent_tools.text_half_height(title["props"], 1.0, 1080.0) * 1.05 / 1.15  # lineHeight 1.05
    assert title["y"] - half >= 0.05 - 1e-6  # строки в безопасной зоне YouTube
    assert "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,setsar=1" in ffmpeg_call["ffmpeg"]
    assert store.get_clip_plan(plan["id"])["render_settings"]["cover"]["mode"] == "image"


def test_cover_layers_vertical_unchanged_by_format_arg():
    from app.cover_compose import cover_layers

    assert cover_layers("Он это сделал", 3.0, y=0.25) == cover_layers("Он это сделал", 3.0, y=0.25, fmt="9:16")


def test_cover_upload_fits_format(tmp_path, monkeypatch):
    from test_api_pipeline import _require_ffmpeg
    from test_cover import _setup

    from app.ingest import probe_media

    _require_ffmpeg()
    client, _store, plan, _preset = _setup(tmp_path, monkeypatch)
    red = tmp_path / "red.png"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=400x300", "-frames:v", "1", str(red)],
                   check=True)
    sizes = {}
    for fmt in ("", "16:9", "1:1"):
        with red.open("rb") as f:
            data = {"format": fmt} if fmt else {}
            up = client.post("/api/covers", files={"file": ("red.png", f, "image/png")}, data=data)
        up = up.raise_for_status().json()
        meta = probe_media(settings.data_dir / "covers" / up["image"])
        sizes[fmt or "default"] = (meta.width, meta.height)
    assert sizes == {"default": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080)}
    with red.open("rb") as f:
        bad = client.post("/api/covers", files={"file": ("red.png", f, "image/png")}, data={"format": "21:9"})
    assert bad.status_code == 400
    client.patch(f"/api/clip-plans/{plan['id']}/render-settings", json={"settings": {"format": "4:5"}})
    with red.open("rb") as f:
        up = client.post("/api/covers", files={"file": ("red.png", f, "image/png")},
                         data={"clip_plan_id": str(plan["id"])}).raise_for_status().json()
    meta = probe_media(settings.data_dir / "covers" / up["image"])
    assert (meta.width, meta.height) == (1080, 1350)


# ---------------------------------------------------------------- заготовка промо


def test_scaffold_refuses_16x9_with_explanation():
    import copy
    import json

    from app.promo_scaffold import ScaffoldError, build_app_promo, check_format

    fixture = json.loads((Path(__file__).parent / "fixtures" / "collabika_scaffold_spec.json").read_text(encoding="utf-8"))
    spec = copy.deepcopy(fixture["spec"])
    with pytest.raises(ScaffoldError) as err:
        build_app_promo(spec, fixture["assets"], fmt="16:9")
    assert "9:16 / 1:1 / 4:5" in str(err.value) and len(err.value.problems) == 2
    assert check_format("1:1") == "1:1" and check_format(None) == "9:16"
    assert build_app_promo(spec, fixture["assets"], fmt="4:5") == build_app_promo(spec, fixture["assets"])


def test_build_app_promo_tool_on_16x9_canvas(tmp_path, monkeypatch):
    sys.path.insert(0, str(Path(__file__).parent))
    from test_promo_scaffold import _canvas_with_assets

    from app.agent_tools import ToolContext, run_tool

    store, plan, spec = _canvas_with_assets(tmp_path, monkeypatch)
    ctx = ToolContext(store=store, clip_plan_id=plan["id"])
    store.set_clip_plan_render_settings(ctx.plan_id, {**(store.get_clip_plan(ctx.plan_id).get("render_settings") or {}),
                                                      "format": "16:9"})
    out, ok = run_tool(ctx, "build_app_promo", {"spec": spec, "dry_run": True})
    assert not ok and "9:16 / 1:1 / 4:5" in out["error"] and out["problems"]
    store.set_clip_plan_render_settings(ctx.plan_id, {**(store.get_clip_plan(ctx.plan_id).get("render_settings") or {}),
                                                      "format": "1:1", "layers_format": "1:1"})
    out, ok = run_tool(ctx, "build_app_promo", {"spec": spec})
    assert ok, out
    assert "1:1" in out["format"]
    render = store.get_clip_plan(ctx.plan_id)["render_settings"]
    assert render["layers_format"] == "9:16" and render["format"] == "1:1"
