"""Motion blur (docs/motion/spec.md «Motion blur»): поле слоя ``motionBlur``, ``motionBlur`` ключа камеры,
значение клипа ``render.motion_blur`` — нормализация, путь до comp.json и инструменты агента."""

from __future__ import annotations

import json

from app import motion
from app.agent_tools import run_tool
from app.camera3d import apply_preset
from app.clip_settings import normalize_render_settings
from app.motion_render import write_comp
from test_agent_tools import _ctx
from test_api_pipeline import _require_ffmpeg
from test_clip_spec import _setup


def _box(**over):
    layer = {"id": "b", "type": "shape", "start": 0, "end": 3, "props": {"kind": "rect", "w": 200, "h": 120}}
    layer.update(over)
    return layer


def test_layer_and_camera_fields_normalize():
    assert motion.normalize_layers([_box(motionBlur=0.5)])[0]["motionBlur"] == 0.5
    # Нет поля — нет и в выходе (значение клипа); null — «как у клипа»; snake_case агента — тоже.
    assert "motionBlur" not in motion.normalize_layers([_box()])[0]
    assert "motionBlur" not in motion.normalize_layers([_box(motionBlur=None)])[0]
    assert motion.normalize_layers([_box(motion_blur=3)])[0]["motionBlur"] == 1
    assert motion.validate_layers([_box(motionBlur=-1)])  # вне 0..1 — вслух
    # Эффект кадра не смазывается: поле снято с пометкой.
    fx = [{"id": "f", "type": "fx", "start": 0, "end": 1, "props": {"kind": "vhs"}, "motionBlur": 0.5}]
    assert "motionBlur" not in motion.normalize_layers(fx)[0]
    assert any("motionBlur" in p for p in motion.validate_layers(fx))
    cam = motion.normalize_camera([{"t": 0, "zoom": 1, "motionBlur": 0.4}, {"t": 1, "zoom": 1.3}])
    assert cam[0]["motionBlur"] == 0.4 and "motionBlur" not in cam[1]


def test_clip_default_flows_to_comp(tmp_path):
    assert normalize_render_settings({})["motion_blur"] is None
    assert normalize_render_settings({"motion_blur": 0.5})["motion_blur"] == 0.5
    assert normalize_render_settings({"motion_blur": 0})["motion_blur"] is None
    assert normalize_render_settings({"motion_blur": "x"})["motion_blur"] is None
    plan = motion.plan_motion({"layers": [_box()], "motion_blur": 0.6})
    assert plan["motion_blur"] == 0.6 and motion.visual_motion(plan)["motion_blur"] == 0.6
    assert "motion_blur" not in motion.plan_motion({"layers": [_box()]})
    # comp.json: motionBlur только если задан — без него компоновщик рисует ровно как раньше.
    path = write_comp(tmp_path / "a.json", width=540, height=960, fps=30, duration=2, layers=[], camera=[],
                      motion_blur=0.6)
    assert json.loads(path.read_text(encoding="utf-8"))["motionBlur"] == 0.6
    path = write_comp(tmp_path / "b.json", width=540, height=960, fps=30, duration=2, layers=[], camera=[])
    assert "motionBlur" not in json.loads(path.read_text(encoding="utf-8"))


def test_camera_preset_sets_key_shutter():
    res = apply_preset("orbit", 1.0, motion_blur=0.5)
    assert res["keys"] and all(k["motionBlur"] == 0.5 for k in res["keys"])
    assert all("motionBlur" not in k for k in apply_preset("orbit", 1.0)["keys"])


def test_tools_set_motion_blur_camera_and_layer(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    ctx = _ctx(store, plan["id"])
    out, ok = run_tool(ctx, "set_motion_blur", {"value": 0.5})
    assert ok and out["motion_blur"] == 0.5, out
    rs = store.get_clip_plan(plan["id"])["render_settings"]
    assert rs["motion_blur"] == 0.5
    out, ok = run_tool(ctx, "set_motion_blur", {"value": 7})
    assert ok and out["motion_blur"] == 1
    out, ok = run_tool(ctx, "set_motion_blur", {"value": "сильно"})
    assert not ok
    out, ok = run_tool(ctx, "set_camera", {"motionBlur": 0.7, "keys": [
        {"t": 0, "zoom": 1.3, "x": 0.3, "y": 0.5}, {"t": 0.25, "zoom": 1.3, "x": 0.7, "y": 0.5, "motionBlur": 0.2}]})
    assert ok, out
    assert [k.get("motionBlur") for k in out["camera"]] == [0.7, 0.2]
    out, ok = run_tool(ctx, "camera_preset", {"preset": "float", "start": 2, "motionBlur": 0.4})
    assert ok, out
    assert any(k.get("motionBlur") == 0.4 for k in store.get_clip_plan(plan["id"])["render_settings"]["camera"])
    out, ok = run_tool(ctx, "add_layers", {"layers": [_box(id="slide", keys={"x": [{"t": 0, "v": -0.2}, {"t": 0.3, "v": 0.5}]})]})
    assert ok, out
    out, ok = run_tool(ctx, "update_layer", {"id": "slide", "patch": {"motionBlur": 0.8}})
    assert ok and out["layer"]["motionBlur"] == 0.8, out
    out, ok = run_tool(ctx, "set_motion_blur", {"value": 0.3})
    assert ok and "slide" in out.get("own", "")
    out, ok = run_tool(ctx, "update_layer", {"id": "slide", "patch": {"motionBlur": None}})
    assert ok and "motionBlur" not in out["layer"], out
    # Редактор без поля motion_blur его не стирает; с полем — ставит своё.
    settings = dict(store.get_clip_plan(plan["id"])["render_settings"])
    settings.pop("motion_blur")
    client.patch(f"/api/clip-plans/{plan['id']}/render-settings", json={"settings": settings}).raise_for_status()
    assert store.get_clip_plan(plan["id"])["render_settings"]["motion_blur"] == 0.3
    client.patch(f"/api/clip-plans/{plan['id']}/render-settings",
                 json={"settings": {**settings, "motion_blur": None}}).raise_for_status()
    assert store.get_clip_plan(plan["id"])["render_settings"]["motion_blur"] is None
    out, ok = run_tool(ctx, "set_motion_blur", {"reset": True})
    assert ok and out["motion_blur"] is None


def test_whip_transition_blur_in_join(tmp_path):
    import subprocess

    from app.clip_settings import settings_to_render_kwargs
    from app.transitions import build_join_args, normalize_transition, whip_blur_sigma
    from test_join_timebase import _part

    assert "motion_blur" not in normalize_transition({"type": "whip"})
    assert normalize_transition({"type": "whip", "motion_blur": 2})["motion_blur"] == 1
    # Клип с motion blur — переходу без своего значения подставляется значение клипа.
    kw = settings_to_render_kwargs({"transition": {"type": "whip"}, "motion_blur": 0.5})
    assert kw["transition"]["motion_blur"] == 0.5
    assert "motion_blur" not in settings_to_render_kwargs({"transition": {"type": "whip"}})["transition"]
    # Путь whip за кадр: 1080 px / (0.14 с × 30) ≈ 257 px, затвор 0.5 → ~129 px, σ ≈ 39.
    assert 35 < whip_blur_sigma(0.5, 0.14, 1080, 30) < 45
    _require_ffmpeg()
    parts = [tmp_path / f"p{k}.mp4" for k in range(2)]
    for path, color in zip(parts, ("red", "blue")):
        _part(path, color)
    sharp = build_join_args(parts, [2.0, 2.0], tmp_path / "a.mp4", normalize_transition({"type": "whip", "smart": False}),
                            has_audio=True, gaps=[5.0], width=108)
    assert "gblur" not in " ".join(sharp)
    blurred = build_join_args(parts, [2.0, 2.0], tmp_path / "b.mp4",
                              normalize_transition({"type": "whip", "smart": False, "motion_blur": 0.5}),
                              has_audio=True, gaps=[5.0], width=108)
    graph = blurred[blurred.index("-filter_complex") + 1]
    assert "gblur=sigma=" in graph and "between(t,1.860,2.000)" in graph
    # Наплыв не сдвиг — не смазывается.
    fade = build_join_args(parts, [2.0, 2.0], tmp_path / "c.mp4",
                           normalize_transition({"type": "fade", "smart": False, "motion_blur": 0.5}),
                           has_audio=True, gaps=[5.0], width=108)
    assert "gblur" not in " ".join(fade)
    proc = subprocess.run(blurred, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[-800:]
    assert (tmp_path / "b.mp4").stat().st_size > 1000
