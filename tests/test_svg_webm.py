"""SVG → webm с альфой (app/svg_webm.py — перенос svg-to-webm владельца): параметры, кэш по хешу,
файл библиотеки, настоящая конвертация (если есть Chromium, Node и ffmpeg)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from app import svg_webm
from app.settings import settings
from app.svg_webm import SvgWebmError, cache_key, check_svg, convert, normalize_params, svg_size, svg_to_asset

SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="64" height="32"><style>@keyframes mv{from{transform:'
       'translateX(0)}to{transform:translateX(48px)}}.b{animation:mv 1s linear both}</style>'
       '<rect class="b" x="0" y="8" width="16" height="16" fill="#ff00ff"/></svg>')


def fake_run(calls: list | None = None, frames: int = 9):
    """Подмена node/svg-to-webm.mjs: пишет настоящий маленький webm с альфой (ffmpeg lavfi)."""

    def run(args: list[str], timeout: float) -> str:
        if calls is not None:
            calls.append(args)
        out = args[args.index("--out") + 1]
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        f"color=c=red@0.5:s=64x32:r=30:d={frames / 30:.3f},format=yuva420p",
                        "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-auto-alt-ref", "0",
                        "-metadata:s:v:0", "alpha_mode=1", out], check=True)
        return f'{{"ok":true,"frames":{frames},"width":64,"height":32,"fps":30,"duration":{frames / 30:.3f}}}\n'

    return run


def _data(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(svg_webm, "chromium_path", lambda: "chrome")
    monkeypatch.setattr(svg_webm, "converter_command", lambda: ["node", "svg-to-webm.mjs"])


def test_normalize_params_defaults_and_clamps():
    assert normalize_params(None) == {"fps": 30, "duration": None, "width": None, "height": None, "scale": 1.0,
                                      "crf": 18, "strip_bg": False}
    p = normalize_params({"fps": 500, "duration": 99, "width": 99999, "scale": 0.01, "crf": -3, "strip_bg": "true"})
    assert p["fps"] == 60 and p["duration"] == 30.0 and p["width"] == 2160 and p["scale"] == 0.25
    assert p["crf"] == 0 and p["strip_bg"] is True
    assert normalize_params({"duration": "x", "fps": True})["duration"] is None


def test_svg_size_and_check():
    assert svg_size('<svg width="800" height="240" viewBox="0 0 400 120">') == (800, 240)
    assert svg_size('<svg viewBox="0 0 400 120">') == (400, 120)
    assert svg_size("<svg>") == (800, 600)
    for bad in ("", "  ", "<html></html>", None):
        with pytest.raises(ValueError):
            check_svg(bad)


def test_cache_key_follows_svg_and_params():
    assert cache_key(SVG, {"fps": 30}) == cache_key(SVG, {})  # умолчания — те же параметры
    assert cache_key(SVG, {"duration": 2}) != cache_key(SVG, {"duration": 3})
    assert cache_key(SVG, {}) != cache_key(SVG.replace("ff00ff", "00ff00"), {})


def test_convert_caches_by_hash_and_passes_params(tmp_path, monkeypatch):
    if not shutil.which("ffmpeg"):
        pytest.skip("нет ffmpeg")
    _data(tmp_path, monkeypatch)
    calls: list = []
    first = convert(SVG, {"duration": 0.3, "fps": 30, "strip_bg": True}, run=fake_run(calls))
    assert not first["cached"] and Path(first["path"]).is_file() and first["frames"] == 9
    args = calls[0]
    assert args[args.index("--duration") + 1] == "0.3" and "--strip-bg" in args and "--fonts-dir" in args
    assert args[args.index("--chrome") + 1] == "chrome"
    # Тот же SVG и параметры — файл из кэша, конвертер не зовётся.
    again = convert(SVG, {"duration": 0.3, "strip_bg": True}, run=lambda *_: pytest.fail("кэш не сработал"))
    assert again["cached"] and again["path"] == first["path"] and again["frames"] == 9
    # Другие параметры — новый файл.
    other = convert(SVG, {"duration": 0.4}, run=fake_run(calls))
    assert other["path"] != first["path"] and len(calls) == 2


def test_convert_limits_and_missing_browser(tmp_path, monkeypatch):
    _data(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="2160"):
        convert('<svg width="3000" height="100"/>', {}, run=fake_run())
    monkeypatch.setattr(svg_webm, "MAX_FRAMES", 10)
    with pytest.raises(ValueError, match="кадров"):
        convert(SVG, {"duration": 1, "fps": 30}, run=fake_run())
    monkeypatch.setattr(svg_webm, "chromium_path", lambda: None)
    with pytest.raises(SvgWebmError, match="Chromium"):
        convert(SVG, {"duration": 0.5, "fps": 10})


def test_svg_to_asset_same_svg_same_asset(tmp_path, monkeypatch):
    if not shutil.which("ffmpeg"):
        pytest.skip("нет ffmpeg")
    from test_api_pipeline import _client

    _client_, store = _client(tmp_path, monkeypatch)
    _data(tmp_path, monkeypatch)
    asset, info = svg_to_asset(store, SVG, {"duration": 0.3}, label="Баннер", run=fake_run())
    assert asset["kind"] == "video" and asset["tags"] == "svg-webm" and asset["width"] == 64
    again, info2 = svg_to_asset(store, SVG, {"duration": 0.3}, label="Баннер", run=fake_run())
    assert again["id"] == asset["id"] and info2["cached"]
    # webm с альфой годится слою image (анимированная картинка), а не только video.
    from app.motion_render import _resolve_ref

    assert _resolve_ref(store, int(asset["id"]), "image") == Path(asset["file_path"])


def test_real_conversion_frames_and_alpha(tmp_path, monkeypatch):
    """Настоящий конвертер: кадров ровно duration·fps, альфа есть, анимация перемотана по времени."""
    for tool in ("node", "ffmpeg"):
        if not shutil.which(tool):
            pytest.skip(f"нет {tool}")
    if not svg_webm.chromium_path():
        pytest.skip("нет Chromium")
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    info = convert(SVG, {"duration": 0.6, "fps": 10})
    assert info["frames"] == 6 and info["width"] == 64 and info["height"] == 32
    from app.anim_frames import animation_info

    anim = animation_info(info["path"])
    assert anim["animated"] and anim["alpha"] and anim["frames"] == 6 and anim["decoder"] == "libvpx-vp9"
