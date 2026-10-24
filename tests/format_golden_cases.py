"""Эталонные клипы 9:16 для проверки «формат по умолчанию рендерится ровно как раньше».

Каждый случай прогоняет настоящий ``ClipRenderService.render_clip_plan`` (и кадр агента
``render_frame_png``) с подменённым ffmpeg/компоновщиком и записывает всё, что ушло наружу:
аргументы ffmpeg, аргументы компоновщика, comp.json и assets.json. Пути и случайные id
заменены метками — снимок не зависит от машины.

Снимок снят на main ДО универсальных форматов (tests/fixtures/format_golden.json);
обновить: ``VVF_UPDATE_GOLDEN=1 pytest tests/test_format_golden.py``.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

from app.settings import settings

CASES = ("crop_focus", "crop_focus_motion", "webcam_top_montage", "chroma_backdrop", "canvas_motion", "canvas_square")

_HEX = re.compile(r"[0-9a-f]{32}")


class _Meta:
    width = 1080
    height = 1920
    fps = 30.0
    duration_sec = 6.0
    raw: dict = {}


def _scrub(value, tmp: str):
    if isinstance(value, str):
        text = value.replace("\\", "/")
        text = text.replace(tmp, "<TMP>")
        return _HEX.sub("<ID>", text)
    if isinstance(value, list):
        return [_scrub(v, tmp) for v in value]
    if isinstance(value, dict):
        return {k: _scrub(v, tmp) for k, v in value.items()}
    return value


def _promo_layers() -> list[dict]:
    """Холст-промо: титр сверху, телефон по центру, касание, кнопка снизу, фон во всю ширину."""
    return [
        {"id": "stars", "type": "shape", "start": 0, "end": 6, "x": 0.5, "y": 0.5,
         "props": {"kind": "rect", "w": 1080, "h": 1920, "fill": "#101020"}},
        {"id": "title", "type": "text", "start": 0, "end": 6, "x": 0.5, "y": 0.14,
         "props": {"text": "Задачи команды —\nв одном месте", "size": 80, "maxWidth": 960}},
        {"id": "phone", "type": "shape", "start": 0, "end": 6, "x": 0.5, "y": 0.52, "followCamera": True,
         "props": {"kind": "rect", "w": 560, "h": 1150, "fill": "#222222", "radius": 60}},
        {"id": "tap", "type": "shape", "start": 2, "end": 2.6, "x": 0.6, "y": 0.45,
         "props": {"kind": "ellipse", "w": 96, "h": 96}, "keys": {"opacity": [{"t": 0, "v": 1}, {"t": 0.6, "v": 0}]}},
        {"id": "cta", "type": "text", "start": 0, "end": 6, "x": 0.5, "y": 0.89,
         "props": {"text": "Попробовать →", "size": 60,
                   "bg": {"color": "#ff6a2b", "opacity": 1, "radius": 46, "padX": 50, "padY": 24}}},
    ]


def run_case(name: str, tmp_path: Path, monkeypatch) -> dict:
    """Прогнать случай ``name`` и вернуть снимок (без путей и случайных id)."""
    sys.path.insert(0, str(Path(__file__).parent))
    from test_render import _store

    from app.render import ClipRenderService

    store = _store(tmp_path, monkeypatch)
    calls: list = []
    compositor_file = tmp_path / "motion-render.mjs"
    compositor_file.write_text("// fake", encoding="utf-8")
    monkeypatch.setattr(settings, "motion_compositor", compositor_file)
    monkeypatch.setattr(settings, "motion_node", shutil.which("python") or sys.executable)

    def fake_ffmpeg(args, timeout, cwd=None):
        calls.append(["ffmpeg", list(args)])
        Path(args[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(args[-1]).write_bytes(b"video")

    def fake_compositor(args, timeout):
        entry = {"args": list(args)}
        comp_path = Path(args[args.index("--comp") + 1])
        entry["comp"] = json.loads(comp_path.read_text(encoding="utf-8"))
        assets_path = Path(args[args.index("--assets") + 1])
        entry["assets"] = json.loads(assets_path.read_text(encoding="utf-8"))
        calls.append(["compositor", entry])
        target = args[args.index("--png") + 1] if "--png" in args else args[args.index("--output") + 1]
        Path(target).write_bytes(b"motion")

    def fake_subs(*, clip_id, input_path, output_path, subtitle_profile_id, preset):
        calls.append(["subtitles", [str(input_path), str(output_path)]])
        Path(output_path).write_bytes(b"subbed")

    service = ClipRenderService(store)
    monkeypatch.setattr("app.render._run_ffmpeg", fake_ffmpeg)
    monkeypatch.setattr("app.render._run_compositor", fake_compositor)
    # Основа выходит размером кадра формата — его и «видит» probe компоновщика.
    meta = _Meta()
    if name == "canvas_square":
        meta.height = 1080
    monkeypatch.setattr("app.render.probe_media", lambda path: meta)
    monkeypatch.setattr("app.render.run_qc", lambda *a, **k: None)
    monkeypatch.setattr("app.render._has_audio_stream", lambda path: True)
    monkeypatch.setattr("app.render.music_volume_for", lambda *a, **k: 0.5, raising=False)
    monkeypatch.setattr(service, "_render_subtitles", fake_subs)

    preset = store.create_ffmpeg_preset("Вертикаль", output_width=1080, output_height=1920, fps=30)
    source_path = settings.source_dir / f"{name}.mp4"
    source_path.write_bytes(b"src")
    canvas = name in ("canvas_motion", "canvas_square")
    source_type = "canvas" if canvas else "upload"
    size = (1080, 1920) if source_type == "canvas" else (1920, 1080)
    source = store.create_source(source_type, source_path, duration_sec=120, width=size[0], height=size[1],
                                 fps=30, status="ready")
    if name == "chroma_backdrop" or canvas:
        source = store.update_source(source["id"], chroma_key={"color": "#00ff00"})
    analysis = store.create_ai_analysis(source["id"], "mock", status="succeeded")
    focus = [{"t": 0.0, "x": 0.3}, {"t": 2.0, "x": 0.72}, {"t": 4.0, "x": 0.55}]
    seg_a = store.create_ai_segment(analysis["id"], {"start_sec": 10, "end_sec": 16, "title": "A", "focus": focus})
    seg_ids = [seg_a["id"]]
    if name == "webcam_top_montage":
        seg_b = store.create_ai_segment(analysis["id"], {"start_sec": 30, "end_sec": 35, "title": "B"})
        seg_ids.append(seg_b["id"])
    if canvas:
        seg_a = store.create_ai_segment(analysis["id"], {"start_sec": 0, "end_sec": 6, "title": "Холст"})
        seg_ids = [seg_a["id"]]
    plan = store.create_clip_plan(source["id"], analysis["id"], "План", segment_ids=seg_ids)

    render: dict = {}
    if name == "crop_focus_motion":
        render = {
            "layers": [{"id": "hook", "type": "text", "start": 0, "end": 3, "x": 0.5, "y": 0.2,
                        "props": {"text": "ОН ЭТО СДЕЛАЛ", "size": 96}},
                       {"id": "zoomed", "type": "shape", "start": 1, "end": 4, "x": 0.7, "y": 0.6,
                        "followCamera": True, "props": {"kind": "ellipse", "w": 200, "h": 200}}],
            "camera": [{"t": 1, "zoom": 1}, {"t": 1.4, "zoom": 1.5, "x": 0.5, "y": 0.4}, {"t": 3, "zoom": 1}],
        }
    elif name == "webcam_top_montage":
        render = {"layout": {"mode": "split", "cam": {"x": 0.72, "y": 0.04, "w": 0.26, "h": 0.3},
                             "ratio": 0.36, "cam_pos": "top", "border": {"color": "#ffffff", "width": 6}},
                  "transition": {"type": "fade", "duration": 0.3}}
    elif name == "chroma_backdrop":
        render = {
            "backdrop": {"kind": "aurora", "colors": ["#1b1446", "#ff6a2b"]},
            "layers": [{"id": "behind", "type": "text", "start": 0, "end": 6, "x": 0.5, "y": 0.3, "behind": True,
                        "props": {"text": "ЗА СПИНОЙ", "size": 120}},
                       {"id": "front", "type": "text", "start": 0.5, "end": 5, "x": 0.5, "y": 0.85,
                        "props": {"text": "Подпись", "size": 70}}],
            "camera": [{"t": 0, "zoom": 1.2, "x": 0.5, "y": 0.45}],
        }
    elif canvas:
        render = {
            "backdrop": {"kind": "grid", "colors": ["#0b1020", "#3a7bff"]},
            "layers": _promo_layers(),
            "camera": [{"t": 0, "zoom": 1}, {"t": 2, "zoom": 1.6, "x": 0.5, "y": 0.5}, {"t": 4, "zoom": 1}],
        }
        if name == "canvas_square":
            # Квадрат холста (app/aspect.py): старое поле aspect — перекомпоновка 9:16 → 1:1.
            render["aspect"] = "1:1"
    if render:
        store.set_clip_plan_render_settings(plan["id"], render)

    clip = service.render_clip_plan(plan["id"], ffmpeg_preset_id=preset["id"])
    snapshot: dict = {"status": clip["status"], "error": clip.get("error"), "calls": list(calls)}
    if name in ("crop_focus_motion", "canvas_motion", "canvas_square"):
        from app.motion_render import render_frame_png

        calls.clear()
        store.set_clip_plan_render_settings(plan["id"], {**render, "preset_id": preset["id"]})
        render_frame_png(store, plan["id"], 2.5)
        snapshot["frame_calls"] = list(calls)
    return _scrub(snapshot, str(tmp_path).replace("\\", "/"))


GOLDEN_PATH = Path(__file__).parent / "fixtures" / "format_golden.json"


def load_golden() -> dict:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8")) if GOLDEN_PATH.is_file() else {}
