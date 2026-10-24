"""9:16 без изменений после этапа 2B форматов: субтитры, баннер, обложка, безопасные зоны, линт.

Эталоны сняты на коде ДО этапа 2B (tests/fixtures/format_golden_2b.json): текст ASS и команды
прожига в рендере, баннер, обложка с заголовком, линт слоёв и промо, заготовка промо, бренд,
раскладка вебки. Любое расхождение — изменение вертикального ролика.
Обновить: ``VVF_UPDATE_GOLDEN=1 pytest tests/test_format_golden_2b.py`` (только на main до правок).
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

from format_golden_cases import _scrub, run_case

GOLDEN_2B = Path(__file__).parent / "fixtures" / "format_golden_2b.json"
FIXTURES = Path(__file__).parent / "fixtures"

WORDS = [
    ("Сегодня", 0.0, 0.4), ("мы", 0.4, 0.55), ("проверим", 0.55, 1.0), ("как", 1.0, 1.15),
    ("субтитры", 1.15, 1.7), ("переносятся", 1.7, 2.3), ("на", 2.3, 2.4), ("длинных", 2.4, 2.8),
    ("фразах.", 2.8, 3.3), ("Потом", 4.4, 4.7), ("большое", 4.7, 5.1), ("НЕВЕРОЯТНОЕ", 5.1, 5.8),
    ("приключение", 5.8, 6.4), ("начинается", 6.4, 7.0), ("прямо", 7.0, 7.3), ("сейчас!", 7.3, 7.9),
]


def _check(name: str, got) -> None:
    got = json.loads(json.dumps(got, ensure_ascii=False))
    golden = json.loads(GOLDEN_2B.read_text(encoding="utf-8")) if GOLDEN_2B.is_file() else {}
    if os.environ.get("VVF_UPDATE_GOLDEN") == "1":
        golden[name] = got
        GOLDEN_2B.write_text(json.dumps(golden, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
        return
    assert name in golden, "нет эталона — VVF_UPDATE_GOLDEN=1 на main до правок"
    assert got == golden[name]


def _result(words=WORDS):
    from app.subtitles.contracts import SubtitleResult, SubtitleSegment, SubtitleWord

    items = [SubtitleWord(word=w, start=a, end=b) for w, a, b in words]
    return SubtitleResult(text=" ".join(w for w, _a, _b in words), language="ru", duration=8.0,
                          segments=[SubtitleSegment(start=0, end=8.0, text="")], words=items)


@pytest.mark.parametrize("size", [(1080, 1920), (720, 1280)])
def test_ass_vertical_unchanged(size, tmp_path, monkeypatch):
    from app.default_pack import DEFAULT_SUBTITLE_PROFILES
    from app.settings import settings
    from app.subtitles.ass import write_ass_subtitles

    monkeypatch.setattr(settings, "subtitle_dir", tmp_path)
    out = {}
    for index, profile in enumerate(DEFAULT_SUBTITLE_PROFILES):
        for margin in (None, 230):
            prof = dict(profile) if margin is None else {**profile, "margin_v": margin}
            path = write_ass_subtitles(_result(), prof, tmp_path / f"{index}-{margin}.ass", width=size[0], height=size[1])
            out[f"{index}-{margin}"] = path.read_text(encoding="utf-8")
    _check(f"ass_{size[0]}x{size[1]}", out)


SUB_STYLE = {"font_size": 74, "uppercase": 1, "max_words_per_line": 4, "outline_width": 6, "margin_v": 230,
             "sub_pos_pct": 18.0}


@pytest.mark.parametrize("name", ["crop_focus", "canvas_motion"])
def test_render_with_subtitles_unchanged(name, tmp_path, monkeypatch):
    got = run_case(name, tmp_path, monkeypatch, subtitles=SUB_STYLE)
    assert got["status"] == "succeeded", got.get("error")
    _check(f"render_subs_{name}", got)


@pytest.mark.parametrize("name,banner", [
    ("crop_focus", {"position": "top", "height_pct": 14.0, "pos_pct": 4.0}),
    ("crop_focus", {"position": "bottom"}),
    ("crop_focus_motion", {"position": "center", "height_pct": 10.0}),
])
def test_render_with_banner_unchanged(name, banner, tmp_path, monkeypatch):
    got = run_case(name, tmp_path, monkeypatch, banner=banner)
    assert got["status"] == "succeeded", got.get("error")
    _check(f"render_banner_{name}_{banner['position']}", got)


def test_cover_compose_unchanged(tmp_path, monkeypatch):
    import subprocess
    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    from test_render import _store

    from app import cover_compose
    from app.settings import settings

    store = _store(tmp_path, monkeypatch)
    source_path = settings.source_dir / "cover.mp4"
    source_path.write_bytes(b"src")
    source = store.create_source("upload", source_path, duration_sec=60, width=1920, height=1080, fps=30, status="ready")
    analysis = store.create_ai_analysis(source["id"], "mock", status="succeeded")
    seg = store.create_ai_segment(analysis["id"], {"start_sec": 10, "end_sec": 16, "title": "A"})
    plan = store.create_clip_plan(source["id"], analysis["id"], "План", segment_ids=[seg["id"]])
    calls = []

    def fake_frame(store_, plan_id, t, layers=None, camera=None, **kw):
        calls.append(["frame", t, layers, camera, kw])
        return b"png"

    def fake_run(args, **kw):
        calls.append(["ffmpeg", list(args)])
        Path(args[-1]).write_bytes(b"jpg")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("app.motion_render.render_frame_png", fake_frame)
    monkeypatch.setattr(cover_compose.subprocess, "run", fake_run)
    out = {}
    for style in cover_compose.STYLES:
        out[f"layers_{style}"] = cover_compose.cover_layers("Он это сделал", 3.0, emoji="🔥", style=style, y=0.25)
    result = cover_compose.compose_cover(store, plan["id"], 2.0, "Невероятный момент", emoji="😱", style="fire")
    out["result"] = {k: v for k, v in result.items() if k not in ("image", "url")}
    out["calls"] = calls
    _check("cover_compose", _scrub(out, str(tmp_path).replace("\\", "/")))


def _lint_inputs():
    clip581 = json.loads((FIXTURES / "clip581_scaffold.json").read_text(encoding="utf-8"))["clip"]
    crafted = [
        {"id": "top", "type": "text", "start": 0, "end": 3, "x": 0.5, "y": 0.08,
         "props": {"text": "ОЧЕНЬ ДЛИННЫЙ ХУК В ДВЕ СТРОКИ ТОЧНО", "size": 96, "weight": 900, "maxWidth": 920}},
        {"id": "low", "type": "text", "start": 1, "end": 1.4, "x": 0.5, "y": 0.86, "props": {"text": "внизу", "size": 40}},
        {"id": "edge", "type": "text", "start": 0, "end": 4, "x": 0.03, "y": 0.5, "props": {"text": "У края", "size": 80}},
        {"id": "cap", "type": "captions", "start": 0, "end": 9, "x": 0.5, "y": 0.7, "props": {"style": "hormozi"}},
        {"id": "cap2", "type": "captions", "start": 0, "end": 9, "x": 0.5, "y": 0.75, "props": {"style": "box"}},
        {"id": "pic", "type": "image", "start": 0, "end": 4, "x": 0.5, "y": 0.5, "props": {"src": "emoji:1f525", "w": 900}},
        {"id": "box", "type": "shape", "start": 0, "end": 4, "x": 0.5, "y": 0.5,
         "props": {"kind": "rect", "w": 1080, "h": 900, "fill": "#000000"}},
        {"id": "emo", "type": "emoji", "start": 0.5, "end": 3, "x": 0.9, "y": 0.95, "props": {"char": "🔥", "size": 200}},
        {"id": "cnt", "type": "counter", "start": 0.5, "end": 3, "x": 0.5, "y": 0.12, "props": {"from": 0, "to": 100}},
    ]
    return clip581, crafted


def test_lint_unchanged():
    from app import agent_tools, motion
    from app.promo_lint import promo_warnings

    clip581, crafted = _lint_inputs()
    layers581 = motion.normalize_layers(clip581["layers"])
    camera581 = motion.normalize_camera(clip581.get("camera"))
    split = {"mode": "split", "ratio": 0.36, "cam_pos": "top", "cam": {"x": 0.72, "y": 0.04, "w": 0.26, "h": 0.3}}
    out = {
        "crafted": agent_tools.lint_layers(crafted, 8.0),
        "crafted_split": agent_tools.lint_layers(crafted, 8.0, layout=split),
        "crafted_split_bottom": agent_tools.lint_layers(crafted, 8.0, layout={**split, "cam_pos": "bottom"}),
        "crafted_canvas": agent_tools.lint_layers(crafted, 8.0, canvas=True),
        "clip581": agent_tools.lint_layers(layers581, clip581.get("duration") or 30.0, canvas=True,
                                           camera=camera581, backdrop=clip581.get("backdrop")),
        "clip581_promo": promo_warnings(layers581, clip581.get("duration") or 30.0, canvas=True, camera=camera581),
        "text_band": [agent_tools.text_band({}), agent_tools.text_band({"layout": split}),
                      agent_tools.text_band({"layout": {**split, "cam_pos": "bottom"}})],
        "cam_strip": [agent_tools.cam_strip(split), agent_tools.cam_strip({**split, "cam_pos": "bottom", "gap": 12})],
        "safe_text_y": [agent_tools.safe_text_y({"text": "X\nX", "size": 96}, 0.1),
                        agent_tools.safe_text_y({"text": "Длинный текст в три строки по ширине", "size": 110}, 0.12)],
        "cover_share": [agent_tools.cover_share(l) for l in crafted],
        "covering": agent_tools.covering_problems(crafted),
        "subtitle_zone": [agent_tools._subtitle_zone({"subs_on": True, "sub_pos_pct": p}) for p in (12.0, 30.0)],
        "facecam": [agent_tools.facecam_warnings(split, {"width": 1920, "height": 1080}),
                    agent_tools.facecam_warnings({**split, "ratio": 0.2}, {"width": 1920, "height": 1080})],
    }
    _check("lint", out)


def test_scaffold_unchanged():
    from app.agent_tools import lint_layers
    from app.promo_scaffold import build_app_promo

    fixture = json.loads((FIXTURES / "collabika_scaffold_spec.json").read_text(encoding="utf-8"))
    spec = copy.deepcopy(fixture["spec"])
    spec["music"].update({k: v for k, v in fixture["track"].items() if k != "id"})
    sizes = {k: (v["width"], v["height"]) for k, v in fixture["assets"].items()}
    out = {}
    for variant in (0, 1, 2):
        built = build_app_promo({**spec, "variant": variant}, fixture["assets"])
        built["lint"] = lint_layers(built["layers"], built["duration"], canvas=True, backdrop=built["backdrop"],
                                    camera=built["camera"], sizes=sizes)
        out[str(variant)] = built
    _check("scaffold", out)


def test_brand_geometry_unchanged():
    from app import brand_kit

    brand = {"cta": {"text": "Подпишись", "url_text": "t.me/test", "duration_sec": 3.0},
             "colors": {"text": "#ffffff", "accent": "#ff6a1f", "plate": "#111111"}, "font": "Nunito",
             "logo_corner": "top-right"}
    out = {
        "cta_y": [brand_kit.cta_y(0.08, []), brand_kit.cta_y(0.08, [(0.6, 0.75)]), brand_kit.cta_y(0.5, [(0.2, 0.7)])],
        "cta": brand_kit.build_cta(brand, 5.0, 8.0, 0.6, False),
        "cta_keyed": brand_kit.build_cta(brand, 5.0, 8.0, 0.6, True),
        "logo": brand_kit.build_logo(brand, {"id": 5, "width": 400, "height": 200}, 1.0, 5.0),
        "logo_left": brand_kit.build_logo({**brand, "logo_corner": "top-left"}, {"id": 5, "width": 200, "height": 400},
                                          1.0, 5.0),
    }
    _check("brand", out)


def test_stream_layout_unchanged():
    from app import stream_layout, video_crop

    split = stream_layout.normalize_layout({"mode": "split", "ratio": 0.36, "gap": 12,
                                            "border": {"color": "#ffffff", "width": 6}})
    pip = stream_layout.normalize_layout({"mode": "pip_cam", "border": {"color": "#ffffff", "width": 6}})
    out = {
        "scaled": [stream_layout.scaled_px(v, h) for v in (6, 12, 0) for h in (1920, 1280)],
        "split": [stream_layout.split_geometry(split, h) for h in (1920, 1280)],
        "regions": [stream_layout.camera_regions(split, 1080, 1920), stream_layout.camera_regions(pip, 1080, 1920, 1920, 1080)],
        "window_share": [video_crop.window_share({"width": 1920, "height": 1080}),
                         video_crop.window_share({"width": 1920, "height": 1080}, 720, 1280),
                         video_crop.window_share({"width": 1440, "height": 1080, "content_crop": {"w": 0.9, "h": 1}})],
    }
    _check("stream_layout", out)
