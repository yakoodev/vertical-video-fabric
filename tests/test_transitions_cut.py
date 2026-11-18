"""Переходы склеек кадрами (хлыст, заливка цветом, смена через размытие, затемнение, зум-размытие) и переход
отдельного стыка (``transition.joins``) — app/transitions.py, app/transitions_fx_cut.py, app/transitions_fx_render.py.

Проверяется: длины по коридорам видов, направление и цвет, свои переходы стыков (план склеек, нумерация с
джамп-катами, граф без файлов окна), кадры окна (первый — ровно A, последний — ровно B, середина — то, что
обещано), окно только в полосе (``scope``), инструмент ``set_transition join`` и настоящий рендер на крошечных
клипах (180×320, 1 с).
"""

from __future__ import annotations

import subprocess

import numpy as np
import pytest

from app import transitions_fx as FX
from app import transitions_fx_cut as CUT
from app.transitions import (
    CUT_FX, FX_DURATIONS, FX_RANGES, TRANSITIONS, build_join_args, fx_frames, is_plain_concat, join_color, join_kind,
    join_picture_filters, join_xfade, normalize_transition, part_overlaps, piece_join, plan_joins, transition_options,
)
from app.transitions_fx_render import clip_region, plan_fx_joins, read_frames, render_fx_joins

H, W = 64, 36


def _img(rgb) -> np.ndarray:
    return np.full((H, W, 3), rgb, np.float32)


A, B = _img((220, 30, 30)), _img((30, 30, 220))


def _textured(seed: int) -> np.ndarray:
    r = np.random.default_rng(seed)
    return (r.random((H, W, 3)) * 255).astype(np.float32)


# --------------------------------------------------------------------------- #
# Параметры
# --------------------------------------------------------------------------- #


def test_cut_kinds_registered_with_ranges_and_options():
    for kind in CUT_FX:
        assert kind in TRANSITIONS and TRANSITIONS[kind][1] and kind in FX_DURATIONS
        lo, hi = FX_RANGES[kind]
        assert lo <= FX_DURATIONS[kind] <= hi
        assert normalize_transition({"type": kind})["duration"] == FX_DURATIONS[kind]
        assert normalize_transition({"type": kind, "duration": 5})["duration"] == hi
        assert normalize_transition({"type": kind, "duration": 0.01})["duration"] == lo
    assert FX_RANGES["whippan"] == (0.13, 0.3) and FX_RANGES["colorfill"] == (0.3, 0.45)
    assert FX_RANGES["blurswap"] == (0.25, 0.4)
    # затемнение по умолчанию — 3 тёмных кадра до склейки при 30 к/с
    assert fx_frames(FX_DURATIONS["dipblack"], 30) // 2 == 3
    opts = transition_options()["fx"]
    assert opts["ranges"]["whippan"] == [0.13, 0.3] and opts["directions"] == ["left", "right", "up", "down"]
    assert opts["directional"] == ["whippan", "colorfill", "gl_wipe"] and opts["fill_color"] == "#6c5cff"
    # мувик — прежний общий коридор
    assert normalize_transition({"type": "slash", "duration": 2})["duration"] == 0.6


def test_direction_color_accent_normalized_only_when_valid():
    t = normalize_transition({"type": "whippan", "direction": "Вправо", "color": "FF0066", "accent": "#00ff00"})
    assert t["direction"] == "right" and t["color"] == "#ff0066" and t["accent"] == "#00ff00"
    t = normalize_transition({"type": "colorfill", "direction": "diagonal", "accent": "violet"})
    assert "direction" not in t and "accent" not in t
    # frag берёт только виды мувика
    assert normalize_transition({"type": "frag", "kinds": ["whippan", "slash"]})["kinds"] == ["slash"]
    # прежние типы — прежний словарь
    assert set(normalize_transition({"type": "fade"})) == {"type", "duration", "smart", "audio", "sfx", "sfx_volume"}


def test_fill_color_falls_back_to_brand_accent_then_default():
    t = normalize_transition({"type": "colorfill"})
    assert join_color(t, 0) == "#6c5cff"
    assert join_color({**t, "accent": "#112233"}, 0) == "#112233"
    assert join_color({**t, "accent": "#112233", "color": "#abcdef"}, 0) == "#abcdef"


def test_join_overrides_normalized():
    t = normalize_transition({"type": "cut", "joins": [
        {"join": 2, "type": "colorfill", "direction": "up", "color": "#FFAA00"},
        {"join": 0, "type": "whippan", "duration": 9, "reason": "  флешбек   назад "},
        {"join": 0, "type": "dipblack"},            # на стык — последняя запись
        {"join": -1, "type": "fade"}, {"join": 1, "type": "frag"}, {"join": "x", "type": "fade"},
        {"join": 3, "type": "nope"}, {"join": 4, "type": "cut"},
    ]})
    assert t["joins"] == [
        {"join": 0, "type": "dipblack", "duration": FX_DURATIONS["dipblack"]},
        {"join": 2, "type": "colorfill", "duration": FX_DURATIONS["colorfill"], "color": "#ffaa00", "direction": "up"},
        {"join": 4, "type": "cut"},
    ]
    t = normalize_transition({"joins": [{"join": 0, "type": "whippan", "duration": 9, "reason": "  флешбек  "}]})
    assert t["joins"] == [{"join": 0, "type": "whippan", "duration": 0.3, "reason": "флешбек"}]
    assert "joins" not in normalize_transition({"type": "fade", "joins": "нет"})


def test_plan_with_overrides_on_a_cut_clip_and_same_scene():
    t = normalize_transition({"type": "cut", "joins": [{"join": 1, "type": "whippan", "direction": "right"}]})
    assert not is_plain_concat({**t, "audio": "hard"})
    # общий — встык, стык 1 — хлыст даже в той же сцене (дырка 0.5 с)
    assert plan_joins(t, [2.0, 2.0, 2.0], [5.0, 0.5]) == [0.0, 0.2]
    assert [join_kind(t, i) for i in range(2)] == ["cut", "whippan"]
    assert join_xfade(t, 1) == "slideright"
    # стык встык внутри общего перехода
    t2 = normalize_transition({"type": "fade", "smart": False, "joins": [{"join": 0, "type": "cut"}]})
    assert plan_joins(t2, [2.0, 2.0, 2.0]) == [0.0, 0.25]


def test_overrides_count_piece_joins_not_jump_cuts():
    # части: кусок 0 разрезан (джамп), потом стык кусков 0→1, кусок 1 разрезан, стык 1→2
    jumps = [True, False, True, False]
    assert [piece_join(jumps, i) for i in range(4)] == [None, 0, None, 1]
    t = normalize_transition({"type": "cut", "joins": [{"join": 1, "type": "blurswap"}]})
    assert plan_joins(t, [2.0] * 5, None, jumps) == [0.0, 0.0, 0.0, FX_DURATIONS["blurswap"]]
    parts = [{"start_sec": 0, "end_sec": 2}, {"start_sec": 10, "end_sec": 12}, {"start_sec": 20, "end_sec": 22}]
    assert part_overlaps(parts, {"type": "cut", "joins": [{"join": 0, "type": "zoomblur"}]}) == [0.3, 0.0]


def test_graph_fallback_uses_per_join_xfade_even_when_global_is_cut():
    t = normalize_transition({"type": "cut", "joins": [{"join": 0, "type": "whippan", "direction": "up"},
                                                       {"join": 1, "type": "colorfill", "direction": "down"}]})
    graph = ";".join(join_picture_filters(3, [2.0, 2.0, 2.0], [0.2, 0.38], t))
    assert "xfade=transition=slideup:duration=0.200" in graph and "xfade=transition=wipedown:duration=0.380" in graph
    assert graph.endswith("[vout]")
    # без своих переходов — прежний граф встык
    plain = join_picture_filters(2, [1.0, 1.0], [0.0], normalize_transition({}))
    assert plain[-1] == "[c0][c1]concat=n=2:v=1:a=0[vout]"
    assert plan_fx_joins(t, [2.0, 2.0, 2.0], fps=30) == [
        {"join": 0, "kind": "whippan", "frames": 6}, {"join": 1, "kind": "colorfill", "frames": 11}]


# --------------------------------------------------------------------------- #
# Кадры окна
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("kind", CUT_FX)
def test_window_starts_on_a_and_ends_on_b(kind):
    a, b = _textured(1), _textured(2)
    n = 9
    first = CUT.compose_cut(kind, a, b, 0, n, direction="right", color=(255, 0, 100))
    last = CUT.compose_cut(kind, a, b, n - 1, n, direction="right", color=(255, 0, 100))
    if kind != "dipblack":  # затемнение гаснет с первого кадра окна (≈3 кадра до склейки)
        assert np.abs(first - a).mean() < 0.5, kind
    assert np.abs(last - b).mean() < 0.5, kind


def test_whippan_b_enters_from_where_picture_comes_and_is_smeared():
    n = 7
    mid = CUT.compose_cut("whippan", A, B, 3, n, direction="left")      # картинка едет влево — B справа
    assert mid[H // 2, -2, 2] > mid[H // 2, -2, 0] and mid[H // 2, 1, 0] > mid[H // 2, 1, 2]
    up = CUT.compose_cut("whippan", A, B, 4, n, direction="up")         # вверх — B снизу
    assert up[-2, W // 2, 2] > up[-2, W // 2, 0]
    # смаз только вдоль хода: у горизонтального хлыста строки одинаковы по высоте
    tex = _textured(3)
    smear = CUT.whippan(tex, tex, 0.45, "left")
    assert np.abs(np.diff(smear, axis=1)).mean() < 0.65 * np.abs(np.diff(smear, axis=0)).mean()


def test_colorfill_covers_frame_at_the_join():
    n = 11
    color = (10.0, 200.0, 90.0)
    mid = CUT.compose_cut("colorfill", A, B, 5, n, direction="right", color=color)
    assert np.abs(mid - np.array(color, np.float32)).mean() < 3
    early = CUT.compose_cut("colorfill", A, B, 2, n, direction="right", color=color)
    # въезжает слева (картинка едет вправо): слева уже цвет, справа ещё A
    assert abs(early[H // 2, 0, 1] - 200) < 30 and early[H // 2, -1, 0] > 200


def test_blurswap_blurs_at_the_join_and_swaps():
    tex_a, tex_b = _textured(4), _textured(5)
    n = 10
    sharp = np.abs(np.diff(tex_a, axis=1)).mean()
    mid_a = CUT.compose_cut("blurswap", tex_a, tex_b, 4, n)
    assert np.abs(np.diff(mid_a, axis=1)).mean() < 0.5 * sharp
    after = CUT.compose_cut("blurswap", A, B, 6, n)
    assert after[..., 2].mean() > after[..., 0].mean()                  # после склейки — B


def test_dipblack_three_dark_frames_then_b():
    n = 6
    frames = [CUT.compose_cut("dipblack", A, B, j, n) for j in range(n)]
    means = [float(f.mean()) for f in frames]
    assert means[0] > means[1] > means[2] == 0.0                        # A гаснет, к склейке — чёрный
    assert all(np.array_equal(f, B) for f in frames[3:])                # B — сразу, без проявления


def test_zoomblur_zooms_and_smears_from_centre():
    tex_a, tex_b = _textured(6), _textured(7)
    n = 9
    mid = CUT.compose_cut("zoomblur", tex_a, tex_b, 3, n)
    assert np.abs(np.diff(mid, axis=0)).mean() < 0.6 * np.abs(np.diff(tex_a, axis=0)).mean()


def test_nearest_direction():
    assert CUT.nearest_direction((-8, 2)) == "left" and CUT.nearest_direction((1, 9)) == "down"
    assert CUT.nearest_direction((3, -7)) == "up" and CUT.nearest_direction((5, 1)) == "right"


def test_clip_region():
    assert clip_region({"x": 0, "y": 0, "w": 100, "h": 50}, 100, 50) is None
    assert clip_region({"x": -5, "y": 10, "w": 200, "h": 20}, 100, 50) == {"x": 0, "y": 10, "w": 100, "h": 20}
    assert clip_region(None, 100, 50) is None


# --------------------------------------------------------------------------- #
# Настоящий рендер: окна кадрами, полоса, свой переход стыка
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


def test_render_per_join_kinds_and_band_region(tmp_path):
    clips = [_clip(tmp_path, c, c) for c in ("red", "blue", "green")]
    t = normalize_transition({"type": "cut", "joins": [{"join": 0, "type": "colorfill", "color": "#ffff00"},
                                                       {"join": 1, "type": "whippan", "direction": "left"}]})
    band = {"x": 0, "y": 80, "w": RW, "h": 160}
    report: list[dict] = []
    fx = render_fx_joins(clips, [1.0] * 3, t, work_dir=tmp_path / "fx", fps=FPS, report=report, region=band)
    assert sorted(fx) == [0, 1]
    assert report[0]["kind"] == "colorfill" and report[0]["color"] == "#ffff00" and report[0]["region"] == band
    assert report[1]["side"] == "left" and report[1]["why"] == "задано"
    n0 = fx_frames(FX_DURATIONS["colorfill"], FPS)
    win = read_frames(fx[0], None, n0, RW, RH)
    mid = FX.yuv_to_rgb(win[n0 // 2])
    assert mid[RH // 2, RW // 2, 0] > 200 and mid[RH // 2, RW // 2, 1] > 200 and mid[RH // 2, RW // 2, 2] < 60
    # вне полосы — жёсткая склейка: до середины A (красный), после — B (синий)
    top_before = FX.yuv_to_rgb(win[1])[10, RW // 2]
    top_after = FX.yuv_to_rgb(win[-2])[10, RW // 2]
    assert top_before[0] > 150 and top_after[2] > 150
    out = tmp_path / "joined.mp4"
    subprocess.run(build_join_args(clips, [1.0] * 3, out, t, has_audio=True, fps=FPS, width=RW, fx_clips=fx,
                                   region=band), check=True, capture_output=True)
    n1 = fx_frames(FX_DURATIONS["whippan"], FPS)
    assert _frames(out) == 3 * FPS - n0 - n1


def test_set_transition_join_tool(tmp_path, monkeypatch):
    from app.agent_tools import ToolContext, run_tool
    from app.clip_history import snapshot
    from test_cuts import _two_pieces

    store, _service, plan, _preset = _two_pieces(tmp_path, monkeypatch)
    ctx = ToolContext(store=store, clip_plan_id=plan["id"], on_first_change=lambda: snapshot(store, plan["id"], "тест"))
    before = normalize_transition((store.get_clip_plan(plan["id"]).get("render_settings") or {}).get("transition"))
    out, ok = run_tool(ctx, "set_transition", {"join": 0, "type": "whippan", "direction": "вниз"})
    assert ok, out
    assert out["joins"] == [{"join": 0, "type": "whippan", "duration": FX_DURATIONS["whippan"], "direction": "down"}]
    assert out["transition"]["type"] == before["type"]               # общий переход не тронут
    out, ok = run_tool(ctx, "set_transition", {"join": 0, "reason": "ответ собеседника", "duration": 0.15})
    assert ok and out["joins"][0]["reason"] == "ответ собеседника" and out["joins"][0]["duration"] == 0.15
    out, ok = run_tool(ctx, "set_transition", {"join": 5, "type": "frag", "sfx": "boom", "direction": "вбок"})
    assert not ok and len(out["problems"]) == 4, out
    out, ok = run_tool(ctx, "set_transition", {"type": "colorfill", "color": "accent", "direction": "up"})
    assert ok and out["transition"]["direction"] == "up" and "color" not in out["transition"]
    assert out["transition"]["joins"][0]["type"] == "whippan"        # свой стык остаётся
    out, ok = run_tool(ctx, "set_transition", {"join": [0], "type": "inherit"})
    assert ok and out["joins"] == [] and "joins" not in out["transition"]
