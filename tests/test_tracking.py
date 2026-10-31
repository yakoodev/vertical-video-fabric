"""Трекинг слоя за лицом / объектом / точкой (app/tracking.py)."""

from __future__ import annotations

import math
import shutil
import subprocess

import numpy as np
import pytest

from app import tracking as tr
from app.cuts import apply_cuts_to_timeline, remap_layers
from app.freeze import FreezeMap
from app.motion import normalize_layers
from app.promo_lint import key_value

HAS_FFMPEG = bool(shutil.which("ffmpeg"))
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg не установлен")

# ---------------------------------------------------------------- синтетика


def _texture(h: int, w: int, seed: int = 3) -> np.ndarray:
    """Неподвижный «пёстрый» фон: шаблону есть за что цепляться, но диск ярче всего."""
    rng = np.random.default_rng(seed)
    small = rng.integers(40, 160, size=(h // 8 + 1, w // 8 + 1)).astype(np.float32)
    big = np.kron(small, np.ones((8, 8), np.float32))[:h, :w]
    return big


def _disc(img: np.ndarray, cx: float, cy: float, r: float) -> None:
    """Белый диск с тёмным крестом (детали для шаблона) — «лицо» синтетики. Края сглажены:
    положение диска — с долями пикселя, иначе «истина» дрожит на ±0.5 px исходника."""
    h, w = img.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    disc = np.clip(r + 0.5 - np.hypot(xx - cx, yy - cy), 0, 1)
    bar = r * 0.18
    cross = np.clip(np.maximum(bar + 0.5 - np.abs(xx - cx), bar + 0.5 - np.abs(yy - cy)), 0, 1) * disc
    img[:] = img * (1 - disc) + 255 * disc
    img[:] = img * (1 - cross) + 30 * cross


def disc_pos(t: float) -> tuple[float, float]:
    """Где центр диска в исходнике 640×360 в секунду ``t``."""
    return 270.0 + 22.0 * t, 180.0 + 40.0 * math.sin(t * 1.3)


def _write_source(path, seconds: float = 6.0, fps: int = 25, w: int = 640, h: int = 360) -> None:
    bg = _texture(h, w)
    proc = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{w}x{h}", "-r", str(fps),
         "-i", "-", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", "-pix_fmt", "yuv420p", str(path)],
        stdin=subprocess.PIPE,
    )
    for k in range(int(seconds * fps)):
        frame = bg.copy()
        _disc(frame, *disc_pos(k / fps), 22)
        proc.stdin.write(np.clip(frame, 0, 255).astype(np.uint8).tobytes())
    proc.stdin.close()
    assert proc.wait() == 0


# Кадр клипа 360×640 из исходника 640×360 обрезкой «cover»: масштаб по высоте, окно по x.
SCALED_W = 1138  # ffmpeg: 640·640/360 → 1137.8 → 1138


def expected_frame(t_src: float, crop_x: float) -> tuple[float, float]:
    """Центр диска в долях кадра клипа (пиксель i — отрезок [i, i+1), отсюда +0.5)."""
    sx, sy = disc_pos(t_src)
    return ((sx + 0.5) * SCALED_W / 640 - crop_x) / 360, ((sy + 0.5) * 640 / 360) / 640


def _plan(tmp_path, monkeypatch, pieces=((0.0, 6.0),), render=None, focus_x=None):
    from app.ingest import probe_media
    from app.settings import settings
    from test_render import _store

    store = _store(tmp_path, monkeypatch)
    src = settings.source_dir / "disc.mp4"
    src.parent.mkdir(parents=True, exist_ok=True)
    _write_source(src)
    meta = probe_media(src)
    source = store.create_source("upload", src, duration_sec=meta.duration_sec, width=meta.width,
                                 height=meta.height, fps=meta.fps, status="ready")
    analysis = store.create_ai_analysis(source["id"], "mock", status="succeeded")
    segs = [store.create_ai_segment(analysis["id"], {"start_sec": a, "end_sec": b, "title": f"P{i}"},
                                    min_duration=1.0)
            for i, (a, b) in enumerate(pieces)]
    if focus_x is not None:
        for seg in segs:
            store.update_ai_segment_focus(seg["id"], [{"t": 0, "x": focus_x}])
    plan = store.create_clip_plan(source["id"], analysis["id"], "Трек", segment_ids=[s["id"] for s in segs])
    preset = store.create_ffmpeg_preset("Tiny", output_width=360, output_height=640, fps=25, scale_mode="cover")
    store.set_clip_plan_render_settings(plan["id"], {"preset_id": preset["id"], **(render or {})})
    return store, store.get_clip_plan(plan["id"])


EMOJI = {"id": "l_emo", "type": "emoji", "start": 0.0, "end": 6.0, "x": 0.5, "y": 0.5,
         "props": {"char": "🔥", "size": 120}}


# ---------------------------------------------------------------- чистая математика


def test_ncc_numpy_matches_brute_force():
    rng = np.random.default_rng(1)
    img = rng.random((30, 40)) * 255
    tpl = img[10:19, 21:30].copy()
    got = tr.ncc_map(img, tpl)
    assert got.shape == (22, 32)
    t = tpl - tpl.mean()
    for (y, x) in ((10, 21), (3, 5), (20, 31)):
        patch = img[y:y + 9, x:x + 9]
        p = patch - patch.mean()
        want = (p * t).sum() / math.sqrt((p * p).sum() * (t * t).sum())
        assert got[y, x] == pytest.approx(want, abs=1e-6)
    assert np.unravel_index(int(np.argmax(got)), got.shape) == (10, 21)


def test_template_tracker_follows_moving_disc_without_video():
    bg = _texture(360, 640)
    frames = []
    for k in range(40):
        img = bg.copy()
        _disc(img, 200 + 6.5 * k, 150 + 2.0 * k, 20)
        frames.append(img)
    tracker = tr.TemplateTracker(frames[0], 200, 150, 44, 44)
    worst = 0.0
    for k in range(1, 40):
        got = tracker.step(frames[k], 0.1)
        assert got is not None
        worst = max(worst, math.hypot(got[0] - (200 + 6.5 * k), got[1] - (150 + 2.0 * k)))
    assert worst < 1.5


def test_template_tracker_holds_under_occlusion_and_recovers():
    # Как на стриме: в модель «кидают» предметы — цель закрыта, ложный пик не должен утащить трек.
    bg = _texture(360, 640)
    rng = np.random.default_rng(9)
    tracker = tr.TemplateTracker(_frame_at(bg, 0), 200, 150, 44, 44)
    for k in range(1, 30):
        img = _frame_at(bg, k)
        if 12 <= k <= 16:  # заплатка шума поверх цели и копия цели поодаль
            img[120:185, 170:250] = rng.integers(0, 255, size=(65, 80))
            _disc(img, 330, 210, 20)
        got = tracker.step(img, 0.1)
        truth = (200 + 2.0 * k, 150.0)
        if 12 <= k <= 16:
            assert math.hypot(tracker.cx - truth[0], tracker.cy - truth[1]) < 15
        elif k > 17:
            assert got is not None and math.hypot(got[0] - truth[0], got[1] - truth[1]) < 2


def _frame_at(bg, k):
    img = bg.copy()
    _disc(img, 200 + 2.0 * k, 150, 20)
    return img


def test_flat_point_is_refused():
    with pytest.raises(tr.TrackError, match="нет деталей"):
        tr.TemplateTracker(np.full((100, 100), 80, np.float32), 50, 50, 20, 20)


def test_kalman_smoother_kills_jitter_without_lag():
    ts = [k / 8 for k in range(64)]
    truth = [0.3 + 0.05 * t + 0.03 * math.sin(t) for t in ts]
    rng = np.random.default_rng(5)
    noisy = [v + rng.normal(0, 0.006) for v in truth]
    err_raw = np.mean([abs(a - b) for a, b in zip(noisy, truth)])
    errs = [np.mean([abs(a - b) for a, b in zip(tr.smooth_series(ts, noisy, s), truth)]) for s in (0.5, 0.8)]
    assert errs[0] < err_raw * 0.75 and errs[1] < err_raw * 0.6
    # Проход назад (RTS): на чистом движении нет запаздывания — и у краёв тоже.
    clean = tr.smooth_series(ts, truth, 0.4)
    assert max(abs(s - t) for s, t in zip(clean, truth)) < 0.001
    assert tr.smooth_series(ts, noisy, 0) == noisy
    # Сэмпл-выброс с низким доверием (прогноз в пропуске) почти не тянет трек.
    spiky = list(truth)
    spiky[30] += 0.1
    weak = tr.smooth_series(ts, spiky, 0.4, noise=[400.0 if i == 30 else 1.0 for i in range(64)])
    assert abs(weak[30] - truth[30]) < 0.003


def test_simplify_keeps_shape_and_forced_points():
    ts = [k / 10 for k in range(101)]
    vs = [0.5 if t < 5 else 0.5 + (t - 5) * 0.1 for t in ts]
    idx = tr.simplify(ts, vs, 0.001, keep={30})
    assert {0, 30, 100} <= set(idx)
    assert len(idx) <= 5
    for i, t in enumerate(ts):  # опоры восстанавливают кривую в пределах допуска
        j = max(k for k in idx if ts[k] <= t)
        n = min(k for k in idx if ts[k] >= t)
        f = 0 if n == j else (t - ts[j]) / (ts[n] - ts[j])
        assert abs(vs[j] + (vs[n] - vs[j]) * f - vs[i]) <= 0.001 + 1e-9


def _frames(n: int, fps: float, pos, piece=lambda t: 0, drop=()):
    out = []
    for k in range(n):
        t = k / fps
        dets = [] if k in drop else [{"x": pos(t)[0] - 0.05, "y": pos(t)[1] - 0.03, "w": 0.1, "h": 0.06}]
        out.append({"t": t, "piece": piece(t), "dets": dets})
    return out


def test_associate_keeps_target_ignores_distractor_and_survives_gaps():
    def pos(t):
        return 0.3 + 0.05 * t, 0.4

    frames = _frames(48, 8.0, pos, drop=set(range(10, 14)) | set(range(24, 36)))
    for f in frames:  # второе «лицо» крупнее, но далеко — цель не перескакивает
        f["dets"].append({"x": 0.7, "y": 0.1, "w": 0.15, "h": 0.09})
    seed = tr.pick_seed(frames[0]["dets"], {"mode": "near", "x": 0.3, "y": 0.4}, 9 / 16)
    assert seed["x"] < 0.5
    track = tr.associate(frames, 0, seed, 9 / 16)
    assert len(track) == 48
    by = {round(s["t"] * 8): s for s in track}
    assert [by[k]["s"] for k in (10, 13)] == ["p", "p"]  # 0.5 с без детекции — прогноз
    assert by[12]["x"] + by[12]["w"] / 2 == pytest.approx(pos(12 / 8)[0], abs=0.01)  # экстраполяция по скорости
    assert by[33]["s"] == "l"  # 1.5 с без детекции — потеряна
    assert by[40]["s"] == "d" and by[40]["x"] + 0.05 == pytest.approx(pos(5.0)[0], abs=1e-6)  # нашлась снова
    assert all(abs(s["y"] - 0.37) < 0.01 for s in track)  # ни разу не ушла на дальнее (y 0.1)
    # Largest без точки — крупное дальнее.
    assert tr.pick_seed(frames[0]["dets"], {"mode": "largest"}, 9 / 16)["x"] == 0.7
    assert tr.pick_seed(frames[0]["dets"], {"mode": "index", "index": 1}, 9 / 16)["x"] == 0.7


def _fake_track(n=60, fps=10.0, pieces=lambda t: 0):
    samples = []
    for k in range(n):
        t = k / fps
        samples.append({"t": t, "piece": pieces(t), "x": 0.2 + 0.1 * t, "y": 0.3, "w": 0.1, "h": 0.05 + 0.01 * t,
                        "s": "d"})
    return {"samples": samples, "fps": fps, "frame": {"w": 1080, "h": 1920}, "regions": None,
            "target": {"kind": "face", "mode": "largest"}}


def test_apply_track_writes_linear_keys_and_marker_and_replaces_on_rerun():
    track = _fake_track()
    layer = {**EMOJI, "keys": {"opacity": [{"t": 0, "v": 1}], "x": [{"t": 0, "v": 0.9}]}}
    got = tr.apply_track(layer, track, {"x": 0, "y": -1, "units": "target"}, {"scale": True}, 0)
    keys = got["keys"]
    assert keys["opacity"] == [{"t": 0, "v": 1}]  # чужие ключи не тронуты
    assert all(k["ease"] == "linear" for k in keys["x"])
    assert len(keys["x"]) == 2  # прямая — две опоры
    assert key_value(keys["x"], 2.5, 0) == pytest.approx(0.2 + 0.25 + 0.05, abs=1e-3)
    # y — над центром цели на её высоту: 0.3 + h/2 − h.
    h = 0.05 + 0.01 * 3.0
    assert key_value(keys["y"], 3.0, 0) == pytest.approx(0.3 + h / 2 - h, abs=1e-3)
    assert got["tracking"]["props"] == ["x", "y", "scale"]
    assert got["followCamera"] is True
    # Повторный запуск без scale: ключи scale трека сняты, x/y заменены.
    again = tr.apply_track(got, track, {"x": 0.1, "y": 0}, {"scale": False}, 0)
    assert "scale" not in again["keys"] and again["tracking"]["props"] == ["x", "y"]
    assert key_value(again["keys"]["x"], 0, 0) == pytest.approx(0.35, abs=1e-3)
    # Снять трек: ключей трека нет, слой стоит там, где трек начинался.
    off = tr.untrack_layer(again)
    assert "tracking" not in off and set(off["keys"]) == {"opacity"}
    assert off["x"] == pytest.approx(0.35, abs=1e-3)


def test_keys_step_on_piece_boundary_and_limit():
    track = _fake_track(n=300, fps=10.0, pieces=lambda t: 0 if t < 15 else 1)
    for s in track["samples"]:  # дрожь — Дуглас–Пекеру есть что держать
        s["x"] += 0.02 * math.sin(s["t"] * 7)
        if s["piece"] == 1:
            s["x"] += 0.3
    layer = {**EMOJI, "end": 30.0}
    got = tr.apply_track(layer, track, None, None, 0)
    xs = got["keys"]["x"]
    assert len(xs) <= 200
    step = [k for k in xs if k["ease"] == "step"]
    assert len(step) == 1 and step[0]["t"] == pytest.approx(14.9)
    assert key_value(xs, 14.95, 0) == pytest.approx(step[0]["v"])  # до стыка — ступенька, не проезд
    assert any(k["t"] == pytest.approx(15.0) for k in xs)


def test_normalize_keeps_tracking_marker_and_cuts_move_keys():
    track = _fake_track()
    layer = tr.apply_track({**EMOJI}, track, None, None, 0)
    norm = normalize_layers([layer])[0]
    assert norm["tracking"]["target"] == {"kind": "face", "mode": "largest"}
    assert norm["tracking"]["props"] == ["x", "y"]
    # Вырез 1.0–2.0 через слой: после него слой показывает то же содержимое, что было на 2.5 с.
    mapper = apply_cuts_to_timeline([{"start": 1.0, "end": 2.0}])
    cut = remap_layers([norm], mapper)[0]
    assert key_value(cut["keys"]["x"], 1.5, 0) == pytest.approx(0.2 + 0.25 + 0.05, abs=2e-3)
    # Стоп-кадр-вставка 1 с в 3.0: слой тянется, ключи после — на секунду позже.
    frozen = remap_layers([norm], FreezeMap([{"at": 3.0, "duration": 1.0, "layers": "continue"}]))[0]
    assert frozen["end"] == 7.0
    assert key_value(frozen["keys"]["x"], 5.0, 0) == pytest.approx(0.2 + 0.4 + 0.05, abs=2e-3)


def test_screen_point_through_camera_zoom():
    camera = [{"t": 0, "zoom": 2.0, "x": 0.5, "y": 0.5}]
    assert tr.screen_to_base(0.5, 0.5, 1.0, camera, None, 1080, 1920) == pytest.approx((0.5, 0.5))
    assert tr.screen_to_base(0.75, 0.25, 1.0, camera, None, 1080, 1920) == pytest.approx((0.625, 0.375))
    # Окошко pip камера не двигает — точка в нём как есть.
    regions = {"box": {"x": 0, "y": 0, "w": 1080, "h": 1920}, "pinned": [{"x": 700, "y": 100, "w": 300, "h": 200}]}
    assert tr.screen_to_base(0.75, 0.1, 1.0, camera, regions, 1080, 1920) == pytest.approx((0.75, 0.1))
    track = _fake_track()
    for s in track["samples"]:
        s.update(x=0.68, y=0.06, w=0.1, h=0.05)
    track["regions"] = regions
    assert tr.apply_track({**EMOJI}, track)["followCamera"] is False


def test_target_and_preset_normalization():
    assert tr.normalize_target({"kind": "лицо"}) == {"kind": "face", "mode": "largest"}
    assert tr.normalize_target({"kind": "face", "x": 0.2, "y": 0.3})["mode"] == "near"
    assert tr.normalize_target({"kind": "object", "class": "машина"})["class"] == "car"
    with pytest.raises(tr.TrackError):
        tr.normalize_target({"kind": "object", "class": "дракон"})
    with pytest.raises(tr.TrackError):
        tr.normalize_target({"kind": "point"})
    offset, follow, patch = tr.preset_offset("arrow_to", {"type": "shape", "props": {"kind": "arrow", "w": 300}})
    assert patch["rotation"] == -45 and offset["dx"] < 0 < offset["dy"]
    assert tr.preset_offset("above_head", EMOJI)[1] == {"scale": True}


def test_cover_warning_when_layer_sits_on_face():
    track = _fake_track()
    on_face = tr.apply_track({**EMOJI, "props": {"char": "🔥", "size": 200}}, track, {"x": 0, "y": 0}, None, 0)
    assert tr.cover_warnings(on_face, track)
    above = tr.apply_track({**EMOJI, "props": {"char": "🔥", "size": 60}}, track,
                           {"x": 0, "y": -1.6, "units": "target"}, None, 0)
    assert not tr.cover_warnings(above, track)


# ---------------------------------------------------------------- настоящий ffmpeg: кадры основы


def _oracle(store, plan, start, end, fps=10.0):
    """Где диск на самом деле в кадрах основы клипа (центр белых пикселей) — независимо от трекера."""
    setup = tr._frame_setup(store, plan)
    out = {}
    for frame in tr.iter_base_frames(store, plan, setup, start, end, fps):
        gray = frame["img"].mean(axis=2)
        h, w = gray.shape
        # Вес — «белизна» (фон темнее 160); крест тёмный и симметричный — центр масс — центр диска.
        weight = np.clip((gray - 175.0) / 80.0, 0, 1)
        yy, xx = np.mgrid[0:h, 0:w]
        total = weight.sum()
        out[frame["t"]] = (float((weight * xx).sum() / total + 0.5) / w, float((weight * yy).sum() / total + 0.5) / h)
    return out


def _worst(layer, truth, w=360, h=640):
    worst = 0.0
    for t, (x, y) in truth.items():
        local = t - layer["start"]
        got = key_value(layer["keys"]["x"], local, 0), key_value(layer["keys"]["y"], local, 0)
        worst = max(worst, math.hypot((got[0] - x) * w, (got[1] - y) * h))
    return worst


@needs_ffmpeg
def test_point_track_hits_disc_in_clip_frame(tmp_path, monkeypatch):
    store, plan = _plan(tmp_path, monkeypatch)
    crop = (SCALED_W - 360) / 2
    truth = _oracle(store, plan, 0.5, 5.5)
    # Кадры основы — это кадр клипа: центр-кроп cover (кадры исходника дискретны: ± полкадра).
    for t, (x, y) in truth.items():
        want = expected_frame(t, crop)
        assert abs(x - want[0]) * 360 < 4 and abs(y - want[1]) * 640 < 4, (t, x, y, want)
    x0, y0 = truth[0.5]
    layer = {**EMOJI, "start": 0.5, "end": 5.5}
    fresh, report = tr.track_layer(store, plan["id"], layer, {"kind": "point", "x": x0, "y": y0, "t": 0.5})
    assert report["found"] > 0.95
    assert _worst(fresh, truth) < 3.0  # px кадра клипа 360×640 — слой на диске
    assert fresh["tracking"]["target"]["kind"] == "point"
    # Повтор — из кэша, без кадров.
    monkeypatch.setattr(tr, "iter_base_frames", lambda *a, **k: (_ for _ in ()).throw(AssertionError("кэш")))
    again, rep2 = tr.track_layer(store, plan["id"], fresh, {"kind": "point", "x": x0, "y": y0, "t": 0.5},
                                 offset={"y": -0.05})
    assert rep2["cached"] is True
    assert key_value(again["keys"]["y"], 1.0, 0) == pytest.approx(key_value(fresh["keys"]["y"], 1.0, 0) - 0.05,
                                                                  abs=1e-3)


@needs_ffmpeg
def test_point_track_mid_layer_seed_and_reframe(tmp_path, monkeypatch):
    # Рефрейм: окно 9:16 смещено фокусом куска — трек всё равно в координатах готового кадра.
    store, plan = _plan(tmp_path, monkeypatch, focus_x=0.55)
    crop = min(SCALED_W - 360, max(0, 0.55 * SCALED_W - 180))
    truth = _oracle(store, plan, 1.0, 5.0)
    want = expected_frame(3.0, crop)
    assert abs(truth[3.0][0] - want[0]) * 360 < 4  # окно сдвинуто фокусом, как в рендере
    assert abs(truth[3.0][0] - expected_frame(3.0, (SCALED_W - 360) / 2)[0]) * 360 > 30
    fresh, report = tr.track_layer(store, plan["id"], {**EMOJI, "start": 1.0, "end": 5.0},
                                   {"kind": "point", "x": truth[3.0][0], "y": truth[3.0][1], "t": 3.0})
    assert _worst(fresh, truth) < 3.0  # и назад от кадра-семени тоже


@needs_ffmpeg
def test_face_track_with_detector_across_pieces(tmp_path, monkeypatch):
    # Два куска с дыркой в 1 с: на стыке диск прыгает, ключ — ступенькой; пропуски детекции держатся.
    store, plan = _plan(tmp_path, monkeypatch, pieces=((0.0, 2.5), (3.5, 6.0)))
    calls = {"n": 0}

    def detector(img):
        calls["n"] += 1
        if 6 <= calls["n"] <= 8:  # три кадра (0.375 с) детектор «моргнул»
            return []
        gray = img.mean(axis=2)
        ys, xs = np.nonzero(gray > 235)
        if not len(xs):
            return []
        h, w = gray.shape
        return [{"x": xs.min() / w, "y": ys.min() / h, "w": (xs.max() - xs.min()) / w, "h": (ys.max() - ys.min()) / h}]

    fresh, report = tr.track_layer(store, plan["id"], {**EMOJI, "end": 5.0}, {"kind": "face"}, smooth=0,
                                   detector=detector)
    crop = (SCALED_W - 360) / 2
    for t in (0.3, 1.0, 2.2, 2.6, 4.0):
        src_t = t if t < 2.5 else t + 1.0  # второй кусок — с 3.5 с исходника
        want = expected_frame(src_t, crop)
        got = key_value(fresh["keys"]["x"], t, 0)
        assert abs(got - want[0]) * 360 < 5, (t, got, want)
    steps = [k for k in fresh["keys"]["x"] if k["ease"] == "step"]
    assert len(steps) == 1 and 2.3 <= steps[0]["t"] < 2.5
    assert report["found"] > 0.85


@needs_ffmpeg
def test_lost_target_fails_with_message(tmp_path, monkeypatch):
    store, plan = _plan(tmp_path, monkeypatch)
    calls = {"n": 0}

    def detector(img):
        calls["n"] += 1
        return [{"x": 0.4, "y": 0.4, "w": 0.1, "h": 0.06}] if calls["n"] < 5 else []

    with pytest.raises(tr.TrackError, match="потеряна"):
        tr.track_layer(store, plan["id"], {**EMOJI}, {"kind": "face"}, detector=detector)


@needs_ffmpeg
def test_api_job_returns_tracked_layer_with_progress(tmp_path, monkeypatch):
    import time

    from fastapi.testclient import TestClient

    import app.main as app_main
    from app.settings import settings

    store, plan = _plan(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "auth_enabled", False)
    monkeypatch.setattr(app_main, "store", store)
    client = TestClient(app_main.app)
    assert {p["id"] for p in client.get("/api/motion/track-presets").json()["presets"]} >= {"above_head", "under_face"}
    bad = client.post(f"/api/clip-plans/{plan['id']}/track", json={"layer": EMOJI, "target": {"kind": "дракон"}})
    assert bad.status_code == 400
    want = expected_frame(1.0, (SCALED_W - 360) / 2)
    job = client.post(f"/api/clip-plans/{plan['id']}/track",
                      json={"layer": {**EMOJI, "start": 1.0, "end": 3.0},
                            "target": {"kind": "point", "x": want[0], "y": want[1], "t": 1.0},
                            "preset": "above_head"}).raise_for_status().json()
    for _ in range(300):
        status = client.get(f"/api/track-jobs/{job['job_id']}").json()
        if status["state"] in ("done", "error"):
            break
        time.sleep(0.1)
    assert status["state"] == "done", status
    layer = status["result"]["layer"]
    assert layer["tracking"]["preset"] == "above_head" and layer["tracking"]["props"] == ["x", "y"]
    # Над точкой на её высоту (область по умолчанию — 12 % ширины кадра).
    box_h = round(0.12 * 360) / 640
    assert key_value(layer["keys"]["y"], 0, 0) == pytest.approx(want[1] - 1.05 * box_h, abs=4 / 640)
    assert client.get("/api/track-jobs/nope").status_code == 404


@needs_ffmpeg
def test_mcp_track_layer_saves_and_returns_check(tmp_path, monkeypatch):
    from app.agent_tools import run_tool
    from test_agent_tools import _ctx

    store, plan = _plan(tmp_path, monkeypatch)
    store.set_clip_plan_render_settings(plan["id"], {**plan["render_settings"], "layers": [{**EMOJI}],
                                                     "camera": [{"t": 0, "zoom": 1.5, "x": 0.5, "y": 0.5}]})
    seen = {}

    def fake_sheet(store_, plan_id, **kw):
        seen.update(kw)
        return b"jpeg"

    monkeypatch.setattr("app.motion_render.contact_sheet_jpeg", fake_sheet)
    crop = (SCALED_W - 360) / 2
    # Точку агент берёт с view_frame — там уже наезд 1.5×: центр кадра — центр основы.
    want = expected_frame(1.0, crop)
    screen = (0.5 + (want[0] - 0.5) * 1.5, 0.5 + (want[1] - 0.5) * 1.5)
    img, ok = run_tool(_ctx(store, plan["id"]), "track_layer",
                       {"layer_id": "l_emo", "target": {"kind": "point", "x": screen[0], "y": screen[1], "t": 1.0}})
    assert ok, img
    assert img.mime_type == "image/jpeg" and seen["before_cuts"] is True and len(seen["times"]) == 3
    saved = store.get_clip_plan(plan["id"])["render_settings"]["layers"][0]
    assert saved["tracking"]["props"] == ["x", "y"] and saved["followCamera"] is True
    assert key_value(saved["keys"]["x"], 1.0, 0) == pytest.approx(want[0], abs=5 / 360)
    out, ok = run_tool(_ctx(store, plan["id"]), "untrack_layer", {"layer_id": "l_emo"})
    assert ok and "tracking" not in store.get_clip_plan(plan["id"])["render_settings"]["layers"][0]
