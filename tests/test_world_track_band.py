"""suggest_surfaces / track_surface в раскладке «полоса» (meme: геймплей полосой на чёрном).

Пилот 17.11: в такой раскладке поиск поверхности смотрел в собранный кадр — центр кадра чёрный, окна под
слово искались в пустоте. Теперь кадры обрезаются до видео полосы, а ответ — в долях всего кадра, как у
view_frame и слоёв. Плюс время: кадры трека на куске со скоростью — те же секунды записи, что в рендере.
"""

from __future__ import annotations

import math

import cv2
import numpy as np
import pytest

from app import tracking
from app import world_track as WT
from test_render import _store

DW, DH = 270, 480  # кадр детекции 9:16
BAND = (0.1, 0.1 + 1080 * 9 / 16 / 1920)  # видео 16:9 во всю ширину с video_y 0.1


def _texture(seed: int, size: tuple[int, int]) -> np.ndarray:
    rng = np.random.default_rng(seed)
    noise = rng.random((size[0] // 4, size[1] // 4)).astype(np.float32)
    img = cv2.resize(noise, (size[1], size[0]), interpolation=cv2.INTER_CUBIC)
    img = cv2.GaussianBlur(img, (0, 0), 1.2)
    return np.clip((img - img.min()) / (img.max() - img.min()) * 230 + 10, 0, 255).astype(np.uint8)


def _composed_frames(n: int = 18) -> list[dict]:
    """Собранный кадр: чёрное поле, полоса видео с фактурой, камера в игре плывёт вправо."""
    big = _texture(5, (DH, DW * 2))
    y0, y1 = int(round(BAND[0] * DH)), int(round(BAND[1] * DH))
    frames = []
    for i in range(n):
        canvas = np.zeros((DH, DW, 3), np.uint8)
        shift = 40 + 2 * i
        band = cv2.resize(big[:, shift:shift + DW], (DW, y1 - y0))
        canvas[y0:y1] = np.dstack([band] * 3)
        frames.append({"t": round(i / 30, 4), "piece": 0, "img": canvas})
    return frames


@pytest.fixture
def band_clip(tmp_path, monkeypatch):
    from app.settings import settings

    store = _store(tmp_path, monkeypatch)
    path = settings.source_dir / "g.mp4"
    path.write_bytes(b"x")
    source = store.create_source("upload", path, duration_sec=60, width=1920, height=1080, status="ready")
    analysis = store.create_ai_analysis(source["id"], "mock", status="succeeded")
    seg = store.create_ai_segment(analysis["id"], {"start_sec": 0, "end_sec": 10, "title": "A"})
    plan = store.create_clip_plan(source["id"], analysis["id"], "П", segment_ids=[seg["id"]])
    from app.clip_settings import normalize_render_settings

    setup = {"rs": normalize_render_settings({}), "preset": {"layout": {"mode": "meme", "video_y": 0.1}},
             "format": "9:16", "width": 1080, "height": 1920, "dw": DW, "dh": DH}
    monkeypatch.setattr(tracking, "_frame_setup", lambda _store, _plan: setup)
    frames = _composed_frames()
    monkeypatch.setattr(tracking, "iter_base_frames", lambda *a, **k: iter(frames))
    return store, plan, setup


def test_band_regions_follow_meme_layout(band_clip):
    store, plan, setup = band_clip
    plan = store.get_clip_plan(plan["id"])
    region = WT.band_regions(store, plan, setup)[0]
    assert region["x"] == pytest.approx(0, abs=0.01) and region["w"] == pytest.approx(1, abs=0.01)
    assert region["y"] == pytest.approx(BAND[0], abs=0.01) and region["y"] + region["h"] == pytest.approx(BAND[1], abs=0.01)
    assert WT.band_regions(store, plan, {**setup, "preset": {"layout": {"mode": "split"}}}) == {}


def test_crop_and_back():
    frames = _composed_frames(2)
    region = {"x": 0.0, "y": BAND[0], "w": 1.0, "h": BAND[1] - BAND[0]}
    cropped = WT.crop_to_band(frames, {0: region})
    assert cropped[0]["img"].shape[0] < DH * 0.4 and cropped[0]["full"].shape[:2] == (DH, DW)
    assert float(cropped[0]["img"].mean()) > 60  # в обрезке — видео, а не чёрное поле
    r = cropped[0]["region"]
    for x, y in ((0.2, 0.3), (0.9, 0.05)):
        assert WT.to_band(r, *WT.from_band(r, x, y)) == pytest.approx((x, y))


def test_suggest_surfaces_looks_inside_the_band(band_clip):
    store, plan, _setup = band_clip
    cands, setup, img = WT.suggest(store, plan["id"], 0.25, "ВЫШКА", n=3)
    assert cands, "в полосе фактура есть — окна должны найтись"
    assert setup["band"]["y"] == pytest.approx(BAND[0], abs=0.01)
    assert img.shape[:2] == (DH, DW)  # подсказки рисуются на всём кадре
    for c in cands:
        ys = [p[1] for p in c["quad"]]
        assert BAND[0] - 0.01 <= min(ys) and max(ys) <= BAND[1] + 0.01, c
        assert BAND[0] - 0.01 <= c["y"] <= BAND[1]


def test_track_surface_quad_in_frame_coords(band_clip, monkeypatch):
    store, plan, _setup = band_clip
    monkeypatch.setattr(WT, "save_track", lambda data: "trk_000000000000")
    mid = (BAND[0] + BAND[1]) / 2
    quad = [[0.3, mid - 0.04], [0.7, mid - 0.04], [0.7, mid + 0.04], [0.3, mid + 0.04]]
    res = WT.track_surface(store, plan["id"], t=0.2, start=0.0, end=0.55, quad=quad, fps=30)
    assert res["band"]["y"] == pytest.approx(BAND[0], abs=0.01)
    first = np.array(res["samples"][6]["q"]).reshape(4, 2)
    # Слово на плоскости в долях всего кадра: по высоте — там же, где задали, внутри полосы.
    assert first[:, 1].min() == pytest.approx(mid - 0.04, abs=0.02) and first[:, 1].max() == pytest.approx(mid + 0.04, abs=0.02)
    assert res["seed"]["quad"][0][1] == pytest.approx(mid - 0.04, abs=1e-3)
    # Камера в игре плывёт вправо (2 px детекции за кадр) — слово едет влево вместе с фактурой.
    last = np.array(res["samples"][-1]["q"]).reshape(4, 2)
    assert last[:, 0].mean() < np.array(res["samples"][0]["q"]).reshape(4, 2)[:, 0].mean()


def test_tracking_frames_follow_piece_speed(monkeypatch):
    """iter_base_frames на куске слоу-мо 0.5: шаг кадра 1/fps клипа = 0.5/fps записи, seek — start + offset·speed."""
    seen = []

    class _Proc:
        def __init__(self, args, **_k):
            seen.append(args)
            self.stdout = _Out()

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

        def kill(self):
            pass

    class _Out:
        def read(self, n):
            return b"\0" * n

        def close(self):
            pass

    plan = {"segments": [{"start_sec": 10.0, "end_sec": 14.0, "speed": 0.5}], "source_id": 1}
    setup = {"rs": {"transition": None}, "dw": 4, "dh": 4, "width": 8, "height": 8, "preset": {}}
    monkeypatch.setattr(tracking, "_piece_graph", lambda *a: (f"[0:v]fps={a[-1]:g}[vout]", "s.mp4"))
    monkeypatch.setattr(tracking.subprocess, "Popen", _Proc)
    frames = list(tracking.iter_base_frames(None, plan, setup, 2.0, 2.5, 10.0))
    args = seen[0]
    assert float(args[args.index("-ss") + 1]) == pytest.approx(11.0)  # 2 с клипа = 1 с записи от начала куска
    graph = args[args.index("-filter_complex") + 1]
    assert "fps=20" in graph  # кадры записи чаще вдвое — на клипе ровно 10 к/с
    assert [f["t"] for f in frames][:2] == [2.0, 2.1]
    plan["segments"][0]["reverse"] = True
    with pytest.raises(WT.TrackError, match="задом"):
        list(tracking.iter_base_frames(None, plan, setup, 2.0, 2.5, 10.0))
    assert math.isfinite(len(frames))
