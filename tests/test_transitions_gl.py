"""Шейдерные переходы gl-transitions (app/transitions_gl.py, node/gltrans): реестр и лицензии, настройки (русские
имена, коридор длины, направление шторки, цвет), запасной xfade, настоящий рендер окна в WebGL на крошечных клипах
(180×320, 1 с): первый кадр — A, последний — B, полоса (``scope``) и сбой браузера → xfade.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from app import transitions_fx as FX
from app import transitions_gl as GL
from app.transitions import (
    FX_DURATIONS, FX_RANGES, TRANSITIONS, build_join_args, fx_frames, is_fx, join_xfade, normalize_transition,
    resolve_kind, transition_options,
)
from app.transitions_fx_render import plan_fx_joins, read_frames, render_fx_joins

ROOT = Path(__file__).resolve().parent.parent
ALLOWED = ("MIT", "BSD", "Apache", "CC0")


def test_registry_shaders_licenses_and_docs():
    doc = (ROOT / "docs" / "third-party" / "gl-transitions.md").read_text(encoding="utf-8")
    assert GL.SOURCE_COMMIT in doc
    assert 10 <= len(GL.GL_KINDS) <= 20
    for key, kind in GL.GL_KINDS.items():
        path = GL.SHADERS_DIR / f"{kind.shader}.glsl"
        head = path.read_text(encoding="utf-8").splitlines()[:6]
        lic = next(line.split(":", 1)[1].strip() for line in head if line.startswith("// License:"))
        author = next(line.split(":", 1)[1].strip() for line in head if line.startswith("// Author:"))
        assert lic == kind.license and lic.startswith(ALLOWED), key
        assert author == kind.author, key
        assert f"`{kind.shader}.glsl`" in doc and lic in doc and f"`{key}`" in doc, key
        assert key in TRANSITIONS and TRANSITIONS[key] == (kind.label, kind.xfade)
        assert is_fx(key) and FX_RANGES[key] == GL.GL_RANGE
        assert GL.GL_RANGE[0] <= FX_DURATIONS[key] <= GL.GL_RANGE[1]
    # Только шейдеры из реестра: лишнего (без проверки лицензии) в каталог не кладём.
    files = {p.stem for p in GL.SHADERS_DIR.glob("*.glsl")}
    assert files == {k.shader for k in GL.GL_KINDS.values()}
    assert (GL.SHADERS_DIR / "LICENSE").exists()
    # Русские имена не пересекаются между видами.
    assert len(GL.GL_ALIASES) == sum(len(k.aliases) for k in GL.GL_KINDS.values())


def test_russian_names_ranges_direction_and_fallback():
    assert resolve_kind("шторка") == "gl_wipe" and resolve_kind("Стекло") == "gl_glass"
    assert resolve_kind("волна") == "gl_ripple" and resolve_kind("PaperTear") == "gl_tear"
    assert resolve_kind("fade") == "fade" and resolve_kind("нет такого") == "нет такого"
    t = normalize_transition({"type": "разрыв"})
    assert t["type"] == "gl_tear" and t["duration"] == GL.GL_KINDS["gl_tear"].duration
    assert normalize_transition({"type": "gl_cube", "duration": 3})["duration"] == GL.GL_RANGE[1]
    assert normalize_transition({"type": "gl_cube", "duration": 0.05})["duration"] == GL.GL_RANGE[0]
    t = normalize_transition({"type": "cut", "joins": [{"join": 1, "type": "круг"}]})
    assert t["joins"] == [{"join": 1, "type": "gl_circle", "duration": GL.GL_KINDS["gl_circle"].duration}]
    wipe = normalize_transition({"type": "gl_wipe", "direction": "вверх"})
    assert join_xfade(wipe, 0) == "wipeup" and join_xfade(normalize_transition({"type": "gl_melt"}), 0) == "wipedown"
    assert GL.gl_params("gl_wipe", "up") == {"direction": [0.0, 1.0]}
    assert GL.gl_params("gl_wipe", None) == {}  # своё направление шейдера (диагональ)
    assert GL.gl_params("gl_burn", None, "#ff0000") == {"burnColor": [1.0, 0.0, 0.0]}
    assert GL.gl_params("gl_tvoff", None, "#000000") == {"color": [0.0, 0.0, 0.0, 1.0]}
    opts = transition_options()
    groups = {t["key"]: t.get("group") for t in opts["types"]}
    assert all(groups[k] == "gl" for k in GL.GL_KINDS) and groups["fade"] is None
    assert "gl_wipe" in opts["fx"]["directional"] and opts["fx"]["gl"] == list(GL.GL_KINDS)
    plan = plan_fx_joins(normalize_transition({"type": "gl_swirl", "smart": False}), [2.0, 2.0], fps=30)
    assert plan == [{"join": 0, "kind": "gl_swirl", "frames": fx_frames(0.6, 30)}]


def test_progress_ends_exactly_on_a_and_b():
    assert GL.progress_of(1) == [1.0]
    p = GL.progress_of(5)
    assert p[0] == 0.0 and p[-1] == 1.0 and p[2] == 0.5


# --------------------------------------------------------------------------- #
# Настоящий рендер (Chromium + WebGL): крошечные клипы
# --------------------------------------------------------------------------- #

RW, RH, FPS = 180, 320, 30


def _chrome() -> str | None:
    try:
        from app.scene3d import chromium_path

        return chromium_path()
    except Exception:  # noqa: BLE001
        return None


needs_gl = pytest.mark.skipif(not (_chrome() and shutil.which("node") and shutil.which("ffmpeg")),
                              reason="нет Chromium, Node или ffmpeg")


def _clip(tmp_path, name: str, color: str, seconds: float = 1.0):
    path = tmp_path / f"{name}.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={color}:s={RW}x{RH}:r={FPS}:d={seconds}",
                    "-f", "lavfi", "-i", f"sine=f=330:d={seconds}", "-shortest", "-c:v", "libx264", "-preset",
                    "ultrafast", "-crf", "10", "-pix_fmt", "yuv420p", "-c:a", "aac", "-threads", "2", str(path)],
                   check=True, capture_output=True)
    return path


def _frames(path) -> int:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries",
                          "stream=nb_read_frames", "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return int(out.stdout.strip())


@pytest.fixture
def node_bin(monkeypatch):
    from app.settings import settings

    monkeypatch.setattr(settings, "motion_node", shutil.which("node") or "node", raising=False)


@needs_gl
def test_render_gl_window_full_frame_and_band(tmp_path, node_bin):
    clips = [_clip(tmp_path, c, c) for c in ("red", "blue", "green")]
    t = normalize_transition({"type": "cut", "joins": [{"join": 0, "type": "gl_cube"},
                                                       {"join": 1, "type": "шторка", "direction": "left"}]})
    report: list[dict] = []
    fx = render_fx_joins(clips, [1.0] * 3, t, work_dir=tmp_path / "fx", fps=FPS, report=report)
    assert sorted(fx) == [0, 1], report
    assert report[0]["shader"] == "gl_cube" and report[1]["params"] == {"direction": [-1.0, 0.0]}
    # Наплыв не длиннее 40% куска (1 с → 0.4 с): кадров окна — по плану склеек.
    n0, n1 = (item["frames"] for item in plan_fx_joins(t, [1.0] * 3, fps=FPS))
    assert n0 == n1 == fx_frames(0.4, FPS)
    win = read_frames(fx[0], None, n0, RW, RH)
    first, last = FX.yuv_to_rgb(win[0]), FX.yuv_to_rgb(win[-1])
    assert first[RH // 2, RW // 2, 0] > 200 and first[RH // 2, RW // 2, 2] < 60   # ровно A (красный)
    assert last[RH // 2, RW // 2, 2] > 200 and last[RH // 2, RW // 2, 0] < 60     # ровно B (синий)
    mid = FX.yuv_to_rgb(win[n0 // 2])
    assert np.abs(mid - first).mean() > 20 and np.abs(mid - last).mean() > 20     # посередине — переход, не A и не B
    out = tmp_path / "joined.mp4"
    subprocess.run(build_join_args(clips, [1.0] * 3, out, t, has_audio=True, fps=FPS, width=RW, fx_clips=fx),
                   check=True, capture_output=True)
    assert _frames(out) == 3 * FPS - n0 - n1
    # Полоса: переход только в прямоугольнике, вне его — жёсткая склейка посередине окна.
    band = {"x": 0, "y": 80, "w": RW, "h": 160}
    report = []
    fx = render_fx_joins(clips[:2], [1.0] * 2, normalize_transition({"type": "gl_ripple", "smart": False}),
                         work_dir=tmp_path / "band", fps=FPS, report=report, region=band)
    n = fx_frames(0.4, FPS)
    win = read_frames(fx[0], None, n, RW, RH)
    assert report[0]["region"] == band
    assert FX.yuv_to_rgb(win[1])[10, RW // 2, 0] > 150 and FX.yuv_to_rgb(win[-2])[10, RW // 2, 2] > 150


@needs_gl
def test_browser_failure_falls_back_to_xfade(tmp_path, node_bin, monkeypatch):
    clips = [_clip(tmp_path, c, c) for c in ("red", "blue")]
    monkeypatch.setattr(GL, "RENDERER", tmp_path / "no-such-render.mjs")
    t = normalize_transition({"type": "gl_doors", "smart": False})
    report: list[dict] = []
    fx = render_fx_joins(clips, [1.0, 1.0], t, work_dir=tmp_path / "fx", fps=FPS, report=report)
    assert fx == {} and report[0]["fallback"] == "xfade" and report[0]["kind"] == "gl_doors"
    args = build_join_args(clips, [1.0, 1.0], tmp_path / "j.mp4", t, has_audio=True, fps=FPS, width=RW, fx_clips=fx)
    graph = args[args.index("-filter_complex") + 1]
    assert re.search(r"xfade=transition=horzopen", graph), graph
    subprocess.run(args, check=True, capture_output=True)


def test_set_transition_accepts_russian_gl_names(tmp_path, monkeypatch):
    from app.agent_tools import ToolContext, run_tool
    from app.clip_history import snapshot
    from test_cuts import _two_pieces

    store, _service, plan, _preset = _two_pieces(tmp_path, monkeypatch)
    ctx = ToolContext(store=store, clip_plan_id=plan["id"], on_first_change=lambda: snapshot(store, plan["id"], "тест"))
    out, ok = run_tool(ctx, "set_transition", {"type": "стекло"})
    assert ok, out
    assert out["transition"]["type"] == "gl_glass" and out["transition"]["duration"] == GL.GL_KINDS["gl_glass"].duration
    out, ok = run_tool(ctx, "set_transition", {"join": 0, "type": "шторка", "direction": "вниз"})
    assert ok, out
    assert out["joins"] == [{"join": 0, "type": "gl_wipe", "duration": 0.45, "direction": "down"}]
