"""Логотип → мазки для слоя logo_reveal (app/logo_vectorize.py) и место слоя в сервисе: схема,
обязательный файл, ссылки на файлы, рамка, мазки при рендере. Движок — node/motion-logo.test.mjs."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from app import motion
from app.aspect import layer_size
from app.logo_vectorize import (
    LogoVectorSpec,
    cached_vectorize,
    ink_alpha,
    order_strokes,
    orient,
    thin,
    vectorize,
)
from app.motion_render import resolve_layer_assets


def _logo(tmp_path, *, dark_on_light: bool = False, alpha: bool = True):
    """Три буквы слева направо: вертикальная палка, кольцо «o», горизонтальная палка; и капля в стороне."""
    h, w = 300, 640
    ink = np.zeros((h, w), np.uint8)
    cv2.line(ink, (80, 60), (80, 240), 255, 34)
    cv2.circle(ink, (260, 150), 80, 255, 30)
    cv2.line(ink, (420, 150), (600, 150), 255, 30)
    cv2.circle(ink, (520, 40), 7, 255, -1)
    if alpha:
        rgba = np.zeros((h, w, 4), np.uint8)
        rgba[..., :3] = 255
        rgba[..., 3] = ink
        path = tmp_path / "logo.png"
        cv2.imwrite(str(path), rgba)
        return path
    gray = 255 - ink if dark_on_light else ink
    path = tmp_path / "logo.jpg"
    cv2.imwrite(str(path), cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR), [cv2.IMWRITE_JPEG_QUALITY, 95])
    return path


def _pts(stroke: dict) -> np.ndarray:
    return np.asarray(stroke["p"], float).reshape(-1, 3)


def test_thin_makes_one_pixel_lines():
    mask = np.zeros((60, 200), bool)
    mask[20:40, 20:180] = True
    sk = thin(mask)
    assert sk.sum() > 100
    # По каждому столбцу середины — ровно один пиксель скелета, и он на осевой.
    cols = sk[:, 50:150]
    assert (cols.sum(axis=0) == 1).all()
    assert abs(np.nonzero(cols)[0].mean() - 29.5) < 1.5


def test_vectorize_strokes_order_direction_and_dots(tmp_path):
    out = vectorize(_logo(tmp_path))
    assert out["v"] == 1 and abs(out["aspect"] - 300 / 640) < 1e-3
    strokes = out["strokes"]
    assert len(strokes) == 3
    lefts = [float(_pts(s)[:, 0].min()) for s in strokes]
    assert lefts == sorted(lefts)  # по умолчанию слева направо
    bar, ring, flat = (_pts(s) for s in strokes)
    assert bar[0, 1] < bar[-1, 1]  # вертикальная — сверху вниз
    assert abs(bar[:, 0].mean() - 80 / 640) < 0.01
    assert strokes[1].get("closed") is True  # кольцо замкнуто, начинается сверху
    assert ring[0, 1] == pytest.approx(ring[:, 1].min(), abs=0.01)
    assert flat[0, 0] < flat[-1, 0]  # горизонтальная — слева направо
    # Радиус — половина толщины мазка (в долях ширины).
    assert np.median(bar[:, 2]) == pytest.approx(17 / 640, rel=0.25)
    assert [g["g"] for g in strokes] == [0, 1, 2]
    # Капля — не мазок, а точка, привязанная к ближайшему мазку (горизонтальной палке).
    assert len(out["dots"]) == 1
    x, y, r, si, u = out["dots"][0]
    assert si == 2 and abs(x - 520 / 640) < 0.01 and 0 < u < 1
    assert out["coverage"] > 0.97


def test_vectorize_jpg_without_alpha_either_polarity(tmp_path):
    for dark in (False, True):
        sub = tmp_path / ("dark" if dark else "light")
        sub.mkdir()
        out = vectorize(_logo(sub, alpha=False, dark_on_light=dark))
        assert len(out["strokes"]) == 3, dark
        assert out["coverage"] > 0.95


def test_ink_alpha_prefers_real_alpha():
    rgba = np.zeros((10, 10, 4), np.uint8)
    rgba[..., 3] = 0
    rgba[2:5, 2:5, 3] = 200
    assert (ink_alpha(rgba) == rgba[..., 3]).all()


def test_order_modes_explicit_and_first_box():
    def s(x0, x1, y):
        return {"p": [x0, y, 0.01, x1, y, 0.01], "g": 0}

    a, b, c = s(0.1, 0.2, 0.3), s(0.4, 0.5, 0.1), s(0.7, 0.8, 0.2)
    assert order_strokes([c, a, b]) == [a, b, c]
    assert order_strokes([a, b, c], "rtl") == [c, b, a]
    assert order_strokes([a, b, c], "ttb") == [b, c, a]
    assert order_strokes([a, b, c], [2, 0]) == [c, a, b]
    # Рамка first (рог над буквами) — её мазки первыми, остальные — в своём порядке.
    assert order_strokes([a, b, c], "ltr", first=[[0.35, 0.0, 0.9, 0.15]]) == [b, a, c]
    with pytest.raises(ValueError):
        order_strokes([a], "zigzag")


def test_orient_open_and_closed():
    vertical = np.array([[0, 10], [0, 0]], float)
    assert orient(vertical, False)[0, 1] == 0
    flat = np.array([[10, 0], [0, 1]], float)
    assert orient(flat, False)[0, 0] == 0
    t = np.linspace(0, 2 * np.pi, 40)
    ring = np.stack([np.cos(t), np.sin(t)], axis=1)
    ring[-1] = ring[0]
    r = orient(ring, True)
    assert np.allclose(r[0], r[-1]) and r[0, 1] == pytest.approx(-1, abs=0.01)
    # Против часовой на экране (y вниз): после верхней точки идём влево.
    assert r[1, 0] < r[0, 0]


def test_cached_vectorize_reuses_file(tmp_path):
    path = _logo(tmp_path)
    one = cached_vectorize(path, tmp_path / "cache")
    files = list((tmp_path / "cache").iterdir())
    assert len(files) == 1
    assert cached_vectorize(path, tmp_path / "cache") == one
    assert cached_vectorize(path, tmp_path / "cache", order="rtl") != one


def test_logo_vector_spec_cleans_and_limits():
    problems: list[str] = []

    def rep(where, msg):
        problems.append(f"{where}: {msg}")

    spec = LogoVectorSpec()
    out = spec.norm({"aspect": 0.5, "spread": 99, "junk": 1,
                     "strokes": [{"p": [0.1, 0.2, 0.01, 0.3, 0.2, 0.01], "g": 2, "x": 1}, {"p": ["a", 1, 2]}, {"p": [1]}],
                     "dots": [[0.5, 0.1, 0.01, 0, 0.5], [0.5, 0.1, 0.01, 9, 0.5], [1, 2]]}, rep, "logo")
    assert out == {"v": 1, "aspect": 0.5, "spread": 4.0, "strokes": [{"p": [0.1, 0.2, 0.01, 0.3, 0.2, 0.01], "g": 2}],
                   "dots": [[0.5, 0.1, 0.01, 0, 0.5]]}
    assert len(problems) == 2
    assert spec.norm(None, rep, "logo") is None
    assert spec.norm({"strokes": []}, rep, "logo") is None  # без пропорции слой не сложить


def test_layer_schema_required_refs_and_size(tmp_path):
    vec = vectorize(_logo(tmp_path))
    [layer] = motion.normalize_layers([{"type": "logo_reveal", "start": 0, "end": 2, "props": {
        "asset_id": 5, "logo": vec, "w": 800, "glow": "#ff6a2a", "reveal": "brush",
        "hit": {"at": 1.2, "scale": 9}, "exit": {"kind": "slash"}}}])
    p = layer["props"]
    assert p["asset_id"] == "5" and p["w"] == 800 and p["glow"] == "#ff6a2a"
    assert p["hit"] == {"at": 1.2, "scale": 1.5} and p["exit"]["kind"] == "slash"
    assert len(p["logo"]["strokes"]) == 3 and "coverage" not in p["logo"]
    assert motion.layer_asset_refs(layer) == [5]
    assert layer_size(layer) == pytest.approx((800, 800 * vec["aspect"]))
    [boiled] = motion.normalize_layers([{"type": "logo_reveal", "start": 0, "end": 3.5, "props": {
        "asset_id": 5, "boil": 3, "sketch": True, "burst": 0.3, "loop": 99, "exit": {"kind": "scratch", "dur": 0.3}}}])
    bp = boiled["props"]
    assert (bp["boil"], bp["sketch"], bp["loop"], bp["exit"]["kind"]) == (3, True, 48, "scratch")
    problems = motion.validate_layers([{"type": "logo_reveal", "props": {}}])
    assert any("логотип" in x for x in problems)
    assert "logo_reveal" in motion.schema_summary()["types"]


def test_render_fills_missing_strokes(tmp_path, monkeypatch):
    from app.settings import settings

    monkeypatch.setattr(settings, "tmp_dir", tmp_path / "tmp")
    path = _logo(tmp_path)

    class Store:
        def get_montage_asset(self, asset_id):
            return {"id": asset_id, "file_path": str(path), "kind": "image"}

    [layer] = motion.normalize_layers([{"id": "lg", "type": "logo_reveal", "start": 0, "end": 2, "props": {"asset_id": 3}}])
    kept, assets, notes = resolve_layer_assets(Store(), [layer])
    assert not notes and assets == {"3": str(path)}
    assert len(kept[0]["props"]["logo"]["strokes"]) == 3
    assert "logo" not in layer["props"]  # исходный слой не тронут
