"""Переходы фраг-мувика в движке: slash / panels / sparks / frag (app/transitions.py, transitions_fx*.py).

Владелец одобрил их вместо белых вспышек и зум-панчей (журнал монтажа 15.11, мувик BURN v4). Проверяется:
параметры (длины 6–12 кадров, angle, color, kinds), план склеек и граф (окно-файл вместо xfade, запасной xfade
без файла), кадры окна (начало ≈ A, конец = B, срез по диагонали, направление по движению камеры) и настоящий
рендер на синтетических клипах: число кадров и кадр посреди перехода — две картинки по диагонали.
"""

from __future__ import annotations

import math
import subprocess

import numpy as np
import pytest

from app import transitions_fx as FX
from app.transitions import (
    FRAG_PATTERN, FX_DURATIONS, TRANSITIONS, build_join_args, fx_frames, join_kind, join_picture_filters,
    normalize_transition, part_overlaps, plan_joins, transition_options,
)
from app.transitions_fx_render import plan_fx_joins, read_frames, render_fx_joins


# --------------------------------------------------------------------------- #
# Параметры
# --------------------------------------------------------------------------- #


def test_fx_types_registered_with_fallback_xfade():
    for kind in ("slash", "panels", "sparks", "frag"):
        assert kind in TRANSITIONS and TRANSITIONS[kind][1]  # без окна-файла — запасной xfade
    opts = transition_options()
    assert {t["key"] for t in opts["types"]} >= {"slash", "panels", "sparks", "frag"}
    assert opts["fx"]["durations"] == FX_DURATIONS and opts["fx"]["fixed"] == ["frag"]


def test_fx_default_and_clamped_durations():
    assert normalize_transition({"type": "slash"})["duration"] == FX_DURATIONS["slash"]
    assert normalize_transition({"type": "panels"})["duration"] == FX_DURATIONS["panels"]
    assert normalize_transition({"type": "slash", "duration": 2})["duration"] == 0.6
    assert normalize_transition({"type": "sparks", "duration": 0.01})["duration"] == 0.1
    # 6–12 кадров: по умолчанию при 30 и 60 к/с
    for kind, sec in FX_DURATIONS.items():
        assert 5 <= fx_frames(sec, 30) <= 12, kind
        assert 6 <= fx_frames(sec, 60) <= 24, kind


def test_fx_options_only_when_valid():
    t = normalize_transition({"type": "slash", "angle": -90, "color": "00E5FF", "kinds": ["x"], "panels": 7})
    assert t["angle"] == 270.0 and t["color"] == "#00e5ff" and "kinds" not in t and t["panels"] == 3
    t = normalize_transition({"type": "frag", "angle": "auto", "color": "red", "kinds": ["sparks", "nope", "slash"]})
    assert "angle" not in t and "color" not in t and t["kinds"] == ["sparks", "slash"]
    # прежние типы — ровно прежний словарь
    assert set(normalize_transition({"type": "fade", "angle": 10, "color": "#ffffff"})) == {
        "type", "duration", "smart", "audio", "sfx", "sfx_volume"}


def test_frag_kinds_and_per_kind_durations():
    t = normalize_transition({"type": "frag", "smart": False})
    assert [join_kind(t, i) for i in range(7)] == list(FRAG_PATTERN) + [FRAG_PATTERN[0]]
    overlaps = plan_joins(t, [3.0] * 7, None)
    assert overlaps == [FX_DURATIONS[k] for k in [join_kind(t, i) for i in range(6)]]
    mine = normalize_transition({"type": "frag", "kinds": ["sparks", "panels"], "smart": False})
    assert plan_joins(mine, [3.0, 3.0, 3.0], None) == [0.3, 0.4]
    # переход не съедает больше 40% куска, а та же сцена — встык
    smart = normalize_transition({"type": "frag", "kinds": ["panels"]})
    assert plan_joins(smart, [0.5, 3.0, 3.0], [5.0, 1.0]) == [0.2, 0.0]
    parts = [{"start_sec": 0, "end_sec": 3}, {"start_sec": 10, "end_sec": 13}]
    assert part_overlaps(parts, {"type": "slash"}) == [0.2]


def test_plan_fx_joins_frames():
    t = normalize_transition({"type": "frag", "kinds": ["slash", "panels", "sparks"], "smart": False})
    plan = plan_fx_joins(t, [2.0, 2.0, 2.0, 2.0], fps=60)
    assert plan == [{"join": 0, "kind": "slash", "frames": 12}, {"join": 1, "kind": "panels", "frames": 24},
                    {"join": 2, "kind": "sparks", "frames": 18}]
    assert plan_fx_joins(normalize_transition({"type": "fade"}), [2.0, 2.0]) == []


# --------------------------------------------------------------------------- #
# Граф склейки
# --------------------------------------------------------------------------- #


def test_graph_splices_fx_window_instead_of_xfade():
    t = normalize_transition({"type": "frag", "kinds": ["slash", "panels"], "smart": False})
    filters = join_picture_filters(3, [2.0, 2.0, 2.0], [0.2, 0.4], t, 1080, 30.0, {0: 3})
    text = ";".join(filters)
    assert "[p0]trim=end=1.7833" in text           # A до окна (полкадра запаса на округление)
    assert "[3:v]settb=AVTB" in text and "[p1]trim=start=0.1833" in text
    assert "[fa1][fx1][fb1]concat=n=3:v=1:a=0[v1]" in text
    # вторая склейка без файла — запасной xfade панелей, по шкале после первой склейки
    assert "xfade=transition=vuslice:duration=0.400:offset=3.400[vout]" in text


def test_graph_without_windows_uses_fallback_xfade_per_kind():
    t = normalize_transition({"type": "frag", "kinds": ["slash", "panels", "sparks"], "smart": False})
    text = ";".join(join_picture_filters(4, [2.0] * 4, [0.2, 0.4, 0.3], t))
    assert "transition=wipetl" in text and "transition=vuslice" in text and "transition=circleopen" in text


def test_build_join_args_adds_window_inputs_and_frame_exact_overlap(tmp_path):
    t = normalize_transition({"type": "slash", "duration": 0.21, "smart": False})
    parts = [tmp_path / "a.mp4", tmp_path / "b.mp4"]
    args = build_join_args(parts, [2.0, 2.0], tmp_path / "o.mp4", t, has_audio=True, fps=30.0,
                           fx_clips={0: tmp_path / "fx.mp4", 5: tmp_path / "nope.mp4"})
    assert args.count("-i") == 3 and args[args.index(str(tmp_path / "fx.mp4")) - 1] == "-i"
    graph = args[args.index("-filter_complex") + 1]
    assert "[2:v]settb=AVTB" in graph
    # 0.21 с при 30 к/с → 6 кадров = 0.200: тот же наплыв у картинки и звука
    assert "trim=start=0.1833" in graph and "acrossfade=d=0.200" in graph


# --------------------------------------------------------------------------- #
# Кадры окна
# --------------------------------------------------------------------------- #

H, W = 96, 54


def _solid(rgb):
    return np.full((H, W, 3), rgb, np.float32)


def test_yuv_roundtrip_is_exact():
    """Нетронутый пиксель окна (в гамме RGB) возвращается в тот же YUV — окно не меняет цвет частей."""
    rng = np.random.default_rng(1)
    yuv = FX.rgb_to_yuv(rng.uniform(0, 255, (20, 12, 3)).astype(np.float32))
    assert np.abs(FX.rgb_to_yuv(FX.yuv_to_rgb(yuv)).astype(int) - yuv).max() <= 1


def test_slash_starts_on_a_ends_on_b_and_splits_diagonally():
    a, b = _solid((200, 20, 20)), _solid((20, 20, 200))
    assert np.allclose(FX.slash(a, b, 0.0, (1, 0)), a)
    assert np.allclose(FX.slash(a, b, 1.0, (1, 0)), b)
    mid = np.clip(FX.slash(a, b, 0.5, (1, 0)), 0, 255)
    blue = mid[..., 2] > mid[..., 0]
    # срез идёт вправо: слева сверху уже B, справа снизу ещё A; граница — наклонная
    assert blue[2, 2] and not blue[-3, -3]
    edges = [int(np.argmin(blue[y])) for y in (30, 48, 66)]
    assert edges[0] > edges[1] > edges[2]


def test_slash_glow_uses_color_and_no_full_frame_flash():
    a, b = _solid((40, 40, 40)), _solid((40, 40, 40))
    mid = FX.slash(a, b, 0.5, (1, 0), color=(0, 255, 0), halo=(0, 255, 0))
    assert mid[..., 1].max() > mid[..., 0].max()   # кромка зелёная
    assert (mid.mean(-1) < 120).mean() > 0.5         # светится полоса, не весь кадр


def test_panels_end_on_b_and_show_three_bands():
    a, b, c = _solid((200, 0, 0)), _solid((0, 200, 0)), _solid((0, 0, 200))
    assert np.allclose(FX.panels(a, b, c, 0.0, count=3), a, atol=1)
    end = FX.panels(a, b, c, 1.0, count=3)
    assert np.abs(end - b).max() < 1
    hold = np.clip(FX.panels(a, b, c, 0.5, count=3), 0, 255)
    col = hold[:, W // 2]
    top, middle, bottom = col[8].argmax(), col[H // 2].argmax(), col[-8].argmax()
    assert (top, middle, bottom) == (0, 1, 2)       # A сверху, B в середине, C снизу
    two = np.clip(FX.panels(a, b, None, 0.5, count=2), 0, 255)
    assert two[8, W // 2].argmax() == 0 and two[-8, W // 2].argmax() == 1


def test_sparks_burst_then_b_and_last_frame_is_b():
    a, b = _solid((90, 90, 90)), _solid((0, 0, 160))
    n = 12
    first = FX.sparks(a, b, 0, n, seed=3, fps=30)
    early = np.clip(FX.sparks(a, b, 3, n, seed=3, fps=30), 0, 255).mean(-1)
    peak = np.clip(FX.sparks(a, b, 5, n, seed=3, fps=30), 0, 255)
    last = FX.sparks(a, b, n - 1, n, seed=3, fps=30)
    assert np.abs(last - b).max() < 1
    assert np.abs(first - a).mean() < 60
    centre = early[H // 3:2 * H // 3, W // 3:2 * W // 3].mean()
    border = np.concatenate([early[:8].ravel(), early[-8:].ravel()]).mean()
    assert centre > border + 20                      # сноп — от прицела (центр кадра)
    assert peak.max() > 200 and (peak.mean(-1) > 230).mean() < 0.5   # искры яркие, но кадр не белый


def test_pick_direction_follows_camera_then_alternates():
    assert FX.pick_direction((12.0, 0.0), (2.0, 1.0), 0) == ((12.0, 0.0), "камера в конце куска")
    d, why = FX.pick_direction((1.0, 0.0), (0.0, -9.0), 0)
    assert d == (0.0, -9.0) and why.startswith("камера в начале")
    swings = [FX.pick_direction((0, 0), (0, 0), i)[0] for i in range(3)]
    assert len(set(swings)) == 3
    (dx, dy), _ = FX.pick_direction((50, 0), (0, 0), 0, angle=90)
    assert abs(dx) < 1e-3 and abs(dy - 1) < 1e-3


def test_motion_vector_sees_pan():
    rng = np.random.default_rng(5)
    base = (rng.random((270, 480)) * 255).astype(np.uint8)
    import cv2

    base = cv2.GaussianBlur(base, (0, 0), 2)
    frames = [np.roll(base, -4 * k, axis=1) for k in range(5)]   # картинка едет влево 4 px/кадр
    vx, vy = FX.motion_vector(frames, fps=60)
    assert vx < -10 and abs(vy) < 3                   # 4 px на шкале 480 → 16 px на шкале 1920


# --------------------------------------------------------------------------- #
# Настоящий рендер на синтетических клипах
# --------------------------------------------------------------------------- #

RW, RH, FPS = 180, 320, 30


def _clip(tmp_path, name: str, color: str, seconds: float = 1.0):
    from test_api_pipeline import _require_ffmpeg

    _require_ffmpeg()
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


def test_render_slash_between_two_colored_clips(tmp_path):
    a, b = _clip(tmp_path, "red", "red"), _clip(tmp_path, "blue", "blue")
    t = normalize_transition({"type": "slash", "angle": 0, "smart": False})
    report: list[dict] = []
    fx = render_fx_joins([a, b], [1.0, 1.0], t, work_dir=tmp_path / "fx", fps=FPS, report=report)
    n = fx_frames(t["duration"], FPS)
    assert list(fx) == [0] and report[0]["frames"] == n == 6 and report[0]["why"] == "угол 0°"
    assert _frames(fx[0]) == n
    out = tmp_path / "joined.mp4"
    subprocess.run(build_join_args([a, b], [1.0, 1.0], out, t, has_audio=True, fps=FPS, width=RW, fx_clips=fx),
                   check=True, capture_output=True)
    assert _frames(out) == 2 * FPS - n                # 30 + 30 − 6
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                str(out)], capture_output=True, text=True).stdout)
    assert dur == pytest.approx((2 * FPS - n) / FPS, abs=0.06)

    # кадр посреди перехода: j = 2 окна — выход 24 + 2
    j = 2
    got = read_frames(out, (FPS - n + j) / FPS - 0.5 / FPS, 1, RW, RH)[0]
    rgb = FX.yuv_to_rgb(got)
    blue = rgb[..., 2] > rgb[..., 0]
    assert blue[5, 5] and not blue[-6, -6]            # слева сверху уже B, справа снизу ещё A
    rows = [int(RH * f) for f in (0.30, 0.45, 0.60)]
    edges = [int(np.argmin(blue[y])) for y in rows]
    assert 0 < edges[2] < edges[1] < edges[0] < RW    # граница по диагонали, не вертикаль/горизонталь
    slope = (edges[2] - edges[0]) / (rows[2] - rows[0])
    assert -1.3 < slope < -0.4                         # срез наклонён на 38° к направлению (≈ −0.78)
    # и совпадает с кадром, который рисует сам переход из кадров частей
    a_last = read_frames(a, 1.0 - (n + 0.5) / FPS, n, RW, RH)
    b_first = read_frames(b, None, n, RW, RH)
    want = FX.rgb_to_yuv(FX.compose("slash", FX.yuv_to_rgb(a_last[j]), FX.yuv_to_rgb(b_first[j]), None, j, n,
                                    direction=(1.0, 0.0)))
    assert np.abs(want.astype(int) - got.astype(int)).mean() < 6


def test_render_frag_panels_and_sparks_frame_count(tmp_path):
    clips = [_clip(tmp_path, c, c) for c in ("red", "green", "blue")]
    t = normalize_transition({"type": "frag", "kinds": ["panels", "sparks"], "smart": False})
    fx = render_fx_joins(clips, [1.0] * 3, t, work_dir=tmp_path / "fx", fps=FPS)
    assert sorted(fx) == [0, 1]
    out = tmp_path / "frag.mp4"
    subprocess.run(build_join_args(clips, [1.0] * 3, out, t, has_audio=True, fps=FPS, width=RW, fx_clips=fx),
                   check=True, capture_output=True)
    n1, n2 = fx_frames(0.4, FPS), fx_frames(0.3, FPS)
    assert _frames(out) == 3 * FPS - n1 - n2
    # после окна искр — чистый B (синий), без остатков искр
    tail = FX.yuv_to_rgb(read_frames(out, (3 * FPS - n1 - n2 - 3) / FPS, 1, RW, RH)[0])
    assert tail[..., 2].mean() > 3 * tail[..., 0].mean()


def test_set_transition_tool_fx_params_and_game_warning(tmp_path, monkeypatch):
    from app.agent_tools import ToolContext, run_tool
    from app.clip_history import snapshot
    from test_cuts import _two_pieces

    store, _service, plan, _preset = _two_pieces(tmp_path, monkeypatch)
    ctx = ToolContext(store=store, clip_plan_id=plan["id"], on_first_change=lambda: snapshot(store, plan["id"], "тест"))
    out, ok = run_tool(ctx, "set_transition", {"type": "fade", "duration": 0.5})
    assert ok and out["transition"]["duration"] == 0.5
    out, ok = run_tool(ctx, "set_transition", {"type": "slash", "angle": 45, "color": "#00e5ff"})
    assert ok, out
    tr = out["transition"]
    assert tr["type"] == "slash" and tr["duration"] == FX_DURATIONS["slash"] and tr["angle"] == 45 \
        and tr["color"] == "#00e5ff"
    out, ok = run_tool(ctx, "set_transition", {"type": "frag", "kinds": ["sparks", "slash"]})
    assert ok and out["transition"]["kinds"] == ["sparks", "slash"]
    out, ok = run_tool(ctx, "set_transition", {"kinds": ["boom"], "color": "red", "panels": 5})
    assert not ok and len(out["problems"]) == 3, out
    # вспышка в разговорном клипе — без предупреждения, в записи игры — правило владельца, но не запрет
    out, ok = run_tool(ctx, "set_transition", {"type": "flash"})
    assert ok and not any("забраковал" in w for w in out.get("warnings") or [])
    store.update_source(plan["source_id"], metadata={"format": {"tags": {"title": "Apex Legends 2026-11-15"}}})
    out, ok = run_tool(ctx, "set_transition", {"type": "zoompunch"})
    assert ok and out["transition"]["type"] == "zoompunch"
    assert any("забраковал" in w and "frag" in w for w in out["warnings"])


def test_window_failure_falls_back_to_xfade(tmp_path):
    t = normalize_transition({"type": "slash", "smart": False})
    missing = [tmp_path / "nope-a.mp4", tmp_path / "nope-b.mp4"]
    assert render_fx_joins(missing, [1.0, 1.0], t, work_dir=tmp_path / "fx", fps=FPS) == {}
    text = ";".join(join_picture_filters(2, [1.0, 1.0], [0.2], t))
    assert "xfade=transition=wipetl" in text and math.isclose(0.2, plan_joins(t, [1.0, 1.0], None)[0])
