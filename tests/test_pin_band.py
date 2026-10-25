"""Наезд камеры в раскладке с окошком (килфид / вебка pip): окошко одно и стоит на месте.

Раньше окошко было в самой основе, камера зумила весь кадр вместе с ним, а компоновщик
клал поверх то же окошко из незумленного кадра — окно раздваивалось. Теперь при камере
основа выше кадра на полосу: сверху игра без окошка (её зумит камера), снизу — строки
собранного кадра с окошком, откуда компоновщик берёт окошко (``pinned[].sy``)."""

import shutil
import subprocess

import pytest

from app.render import _render_filter, banded_pinned, build_unband_args, pin_band

HAS_FFMPEG = bool(shutil.which("ffmpeg"))
W, H = 270, 480
KILLFEED = {"x": 0.70, "y": 0.185, "w": 0.245, "h": 0.155}
LAYOUT = {"mode": "pip_cam", "cam": KILLFEED, "pip": {"x": 0.75, "y": 0.2, "w": 0.5}}
PRESET = {"output_width": W, "output_height": H, "fps": 30, "layout": LAYOUT}
SOURCE = {"width": 1920, "height": 1080}
CAMERA = [{"t": 0, "zoom": 1.5, "x": 0.7, "y": 0.2}]


def test_band_only_with_camera_and_pinned_window():
    band = pin_band(PRESET, SOURCE, {"camera": CAMERA})
    assert band and band["y"] % 2 == 0 and band["h"] % 2 == 0 and band["y"] + band["h"] <= H
    assert pin_band(PRESET, SOURCE, {"camera": []}) is None  # без камеры — прежняя основа
    assert pin_band(PRESET, SOURCE, {"camera": CAMERA, "key": {"color": "#00ff00"}}) is None
    assert pin_band({**PRESET, "layout": {"mode": "split"}}, SOURCE, {"camera": CAMERA}) is None
    assert pin_band({**PRESET, "layout": None}, SOURCE, {"camera": CAMERA}) is None
    pinned = [{"x": 10, "y": band["y"] + 4, "w": 50, "h": 20}]
    assert banded_pinned(pinned, band, H) == [{**pinned[0], "sy": H + 4}]
    assert banded_pinned(pinned, None, H) == pinned


def test_filter_unchanged_without_band():
    plain = _render_filter(W, H, 30, PRESET, None)
    assert "vgame" not in plain and "vstack" not in plain


def _frames(args_vf: list[str], src: str, h: int) -> bytes:
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", src, *args_vf, "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-400:]
    assert len(proc.stdout) == W * h * 3
    return proc.stdout


def _red_rows(raw: bytes, h: int) -> list[int]:
    rows = []
    for y in range(h):
        row = raw[y * W * 3:(y + 1) * W * 3]
        if any(row[i] > 180 and row[i + 1] < 90 for i in range(0, len(row), 3)):
            rows.append(y)
    return rows


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg не установлен")
def test_base_with_band_real_ffmpeg(tmp_path):
    # Исходник: зелёная игра, килфид — красный прямоугольник в правом верхнем углу.
    box = "drawbox=x=iw*0.70:y=ih*0.185:w=iw*0.245:h=ih*0.155:color=0xff0000:t=fill"
    src = f"color=c=0x00ff00:s=1920x1080:r=30:d=0.2,{box}"
    band = pin_band(PRESET, SOURCE, {"camera": CAMERA})
    plain = _frames(["-filter_complex", _render_filter(W, H, 30, PRESET, None), "-map", "[vout]"], src, H)
    banded = _frames(["-filter_complex", _render_filter(W, H, 30, {**PRESET, "pin_band": band}, None),
                      "-map", "[vout]"], src, H + band["h"])
    window_rows = _red_rows(plain, H)
    assert window_rows, "окошко килфида в обычной основе"
    top = _red_rows(banded[: W * H * 3], H)
    assert top == [], "игра над полосой — без окошка (её зумит камера)"
    strip = banded[W * H * 3:]
    # Полоса — те же строки собранного кадра: окошко целиком и пиксель в пиксель.
    assert strip == plain[band["y"] * W * 3:(band["y"] + band["h"]) * W * 3]
    assert min(window_rows) >= band["y"] and max(window_rows) < band["y"] + band["h"]

    # Компоновщик упал — основа с полосой собирается обратно в прежний кадр.
    base = tmp_path / "base.nut"
    graph = _render_filter(W, H, 30, {**PRESET, "pin_band": band}, None)
    proc = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", src, "-filter_complex", graph,
                           "-map", "[vout]", "-c:v", "ffv1", str(base)], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-400:]
    out = tmp_path / "unband.mp4"
    proc = subprocess.run(build_unband_args(base, out, band, H), capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-400:]
    restored = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out), "-frames:v", "1", "-f", "rawvideo",
                               "-pix_fmt", "rgb24", "-"], capture_output=True, timeout=120).stdout
    assert len(restored) == W * H * 3
    assert _red_rows(restored, H) == window_rows


def test_render_and_view_frame_pass_band_to_compositor(tmp_path, monkeypatch):
    """Рендер и кадр агента (view_frame): основа с полосой, comp с baseHeight и pinned.sy."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent))
    from format_golden_cases import run_case

    got = run_case("crop_focus_motion", tmp_path, monkeypatch, extra={"layout": LAYOUT})
    assert got["status"] == "succeeded", got.get("error")
    for calls in (got["calls"], got["frame_calls"]):
        graphs = [a[a.index("-filter_complex") + 1] for kind, a in calls if kind == "ffmpeg" and "-filter_complex" in a]
        assert any("[vgame]" in g and "vstack=inputs=2[vpinned]" in g for g in graphs)
        comp = next(entry["comp"] for kind, entry in calls if kind == "compositor")
        assert comp["height"] == 1920 and comp["baseHeight"] > 1920
        pinned = comp["pinned"][0]
        assert 1920 <= pinned["sy"] and pinned["sy"] + pinned["h"] <= comp["baseHeight"]
    still = next(a for kind, a in got["frame_calls"] if kind == "ffmpeg" and "-loop" in a)
    assert any(str(arg).startswith("scale=1080:") and not str(arg).endswith(":1920") for arg in still)
