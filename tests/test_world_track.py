"""Трекинг «в мире игры»: плоскость под слово (tracked_text), бросок под след (trail), места под слово, хранение
треков, схема слоёв и текст песни на поверхностях (app/surface_track.py, app/object_track.py, app/world_track.py,
app/lyrics_world.py). Кадры — синтетика с известным движением: проверяем, что слово не «плавает»."""

from __future__ import annotations

import math
import shutil

import cv2
import numpy as np
import pytest

from app import object_track as OT
from app import surface_track as ST
from app import world_track as WT

W, H = 270, 480


def _texture(seed: int = 1, size: tuple[int, int] = (H, W)) -> np.ndarray:
    rng = np.random.default_rng(seed)
    noise = rng.random((size[0] // 4, size[1] // 4)).astype(np.float32)
    img = cv2.resize(noise, (size[1], size[0]), interpolation=cv2.INTER_CUBIC)
    img = cv2.GaussianBlur(img, (0, 0), 1.2)
    return np.clip((img - img.min()) / (img.max() - img.min()) * 230 + 10, 0, 255).astype(np.uint8)


def _plane_frames(n: int = 24) -> tuple[list[dict], list[np.ndarray]]:
    """Плоская фактура, камера плывёт: сдвиг + поворот + лёгкая перспектива (известные гомографии)."""
    big = _texture(3, (H * 2, W * 2))
    frames, homs = [], []
    for i in range(n):
        a = math.radians(0.6 * i)
        tx, ty = -W / 2 + 2.5 * i, -H / 2 + 1.5 * i
        Hm = np.array([[math.cos(a), -math.sin(a), tx], [math.sin(a), math.cos(a), ty], [1e-5 * i, 0.0, 1.0]])
        img = cv2.warpPerspective(big, Hm, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        frames.append({"t": round(i / 30, 4), "piece": 0, "img": img})
        homs.append(Hm)
    return frames, homs


def test_track_plane_follows_known_motion():
    frames, homs = _plane_frames()
    seed = 8
    quad = [[0.3, 0.35], [0.7, 0.35], [0.7, 0.55], [0.3, 0.55]]
    res = ST.track_plane(frames, seed, quad, smooth=0.0)
    q0 = np.array(quad) * [W, H]
    # Точка семени p ↦ кадр i: H_i · H_seed⁻¹ · p (кадр = Hm · большая текстура).
    errs = []
    for i, s in enumerate(res["samples"]):
        truth = ST.apply_h(homs[i] @ np.linalg.inv(homs[seed]), q0)
        got = np.array(s["q"]).reshape(4, 2) * [W, H]
        errs.append(float(np.max(np.linalg.norm(got - truth, axis=1))))
        assert s["s"] in ("ok", "weak"), (i, s["s"])
    assert max(errs) < 1.5, errs
    assert res["found"] == 1.0 and res["lost"] == []


def test_track_plane_lost_on_blank_frames_is_not_predicted_forever():
    frames, _ = _plane_frames(20)
    flat = np.full((H, W), 128, np.uint8)
    for f in frames[12:]:
        f["img"] = flat
    res = ST.track_plane(frames, 4, [[0.3, 0.35], [0.7, 0.35], [0.7, 0.55], [0.3, 0.55]])
    tail = [s["s"] for s in res["samples"][12:]]
    # Прогноз — только в коротком пропуске между увиденными кадрами; в конце трека — «потеряна».
    assert set(tail) == {"lost"}
    assert res["found"] < 0.7


def _sq(cx=100.0, cy=100.0, w=80.0, h=30.0, ang=0.0, mirror=False):
    q = np.array([[-w / 2, -h / 2], [w / 2, -h / 2], [w / 2, h / 2], [-w / 2, h / 2]])
    if mirror:
        q[:, 0] *= -1
    a = math.radians(ang)
    rot = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
    return q @ rot.T + [cx, cy]


def test_check_surface_fails_on_synthetic_flip_rotation_and_jitter():
    from app.surface_pose import check_surface

    def samples(quads):
        return [{"t": i / 30, "s": "ok", "q": list((q / [200, 200]).reshape(-1))} for i, q in enumerate(quads)]

    clean = samples([_sq(cx=100 + i) for i in range(20)])
    rep = check_surface(clean, frame=(200, 200), out_width=200)
    assert rep["ok"] and rep["flips"] == 0 and rep["jitter_px"] < 0.1 and rep["rot_step_max"] < 0.1
    flipped = samples([_sq(cx=100 + i, mirror=i == 10) for i in range(20)])
    rep = check_surface(flipped, frame=(200, 200), out_width=200)
    assert not rep["ok"] and rep["flips"] == 1 and any("переворот" in f for f in rep["fail"])
    # Слово боком (как «WISHFUL» по колонне) — провал, даже если трек ровный.
    assert not check_surface(clean, frame=(200, 200), rotation=-90)["ok"]
    wobble = samples([_sq(ang=4.0 * (-1) ** i) for i in range(20)])
    rep = check_surface(wobble, frame=(200, 200), out_width=200)
    assert not rep["ok"] and rep["rot_step_max"] > 7 and rep["jitter_px"] > 2
    # Потерянные кадры и кадры вне окна показа не считаются.
    hidden = samples([_sq(mirror=i == 10) for i in range(20)])
    hidden[10]["s"] = "lost"
    assert check_surface(hidden, frame=(200, 200))["ok"]
    assert check_surface(flipped, frame=(200, 200), show=[0.0, 0.3])["ok"]


def test_reject_reason_and_constrained_model():
    from app.surface_pose import PERSP_CAP, area, compose, constrain, order_quad, pose, reject_reason

    q0 = _sq()
    assert reject_reason(q0, q0, _sq(cx=103), 1 / 30) is None
    assert reject_reason(q0, q0, _sq(mirror=True), 1 / 30) == "flip"
    assert reject_reason(q0, q0, _sq(ang=8), 1 / 30) == "rotation_jump"
    assert reject_reason(q0, _sq(ang=17), _sq(ang=19), 1 / 30) == "rotation_limit"
    assert reject_reason(q0, q0, _sq(w=80 * 1.3, h=30 * 1.3), 1 / 30) == "scale_jump"
    squashed = _sq()
    squashed[2:, 1] = squashed[:2, 1][::-1] + 3  # нижнее ребро почти на верхнем
    assert reject_reason(q0, q0, squashed, 1 / 30) in ("collapse", "scale_jump", "flip")
    # Перспектива сверх подобия урезана: дикий угол сдвинут не дальше PERSP_CAP размера.
    wild = _sq()
    wild[2] += [60, 40]
    got = constrain(q0, wild, None)
    c, s, ang, _ = pose(q0, wild)
    base = compose(q0, c, s, ang, np.zeros_like(q0))
    cap = PERSP_CAP * math.sqrt(abs(area(base)))
    assert np.max(np.linalg.norm(got - base, axis=1)) <= cap + 1e-6
    assert np.linalg.norm(got[2] - wild[2]) > 20
    # Углы против часовой (или с другого угла) упорядочиваются — обход у семени всегда один.
    assert np.allclose(order_quad(q0[::-1]), q0) and np.allclose(order_quad(np.roll(q0, 2, axis=0)), q0)


def test_track_plane_never_shows_mirrored_or_spinning_plane():
    big = _texture(9, (H * 2, W * 2))
    frames = []
    for i in range(30):
        a = math.radians(0.3 * i if i < 12 else 0.3 * i + 3.0 * (i - 12))  # с 12-го кадра — «дрейф» 3°/кадр
        Hm = np.array([[math.cos(a), -math.sin(a), -W / 2 + 2 * i], [math.sin(a), math.cos(a), -H / 2], [0, 0, 1.0]])
        img = cv2.warpPerspective(big, Hm, (W, H), borderMode=cv2.BORDER_REFLECT)
        if i in (20, 21, 22):
            img = cv2.flip(img, 1)  # зеркальные кадры посреди трека
        frames.append({"t": round(i / 30, 4), "img": img})
    res = ST.track_plane(frames, 2, [[0.3, 0.4], [0.7, 0.4], [0.7, 0.55], [0.3, 0.55]])
    check = res["check"]
    assert check["flips"] == 0 and check["text_angle_max"] <= 19.5, check
    assert check["rot_step_max"] <= 2.5, check
    assert res["rejects"], res["rejects"]
    # Слово, что не может удержать позу, гаснет, а не крутится.
    shown = [s for s in res["samples"] if s["s"] in ("ok", "weak", "pred")]
    assert len(shown) < len(res["samples"])


def test_track_plane_refuses_textureless_surface():
    frames = [{"t": i / 30, "img": np.full((H, W), 90, np.uint8)} for i in range(5)]
    with pytest.raises(ValueError, match="мало фактуры"):
        ST.track_plane(frames, 0, [[0.3, 0.3], [0.7, 0.3], [0.7, 0.5], [0.3, 0.5]])


def _throw_frames(n: int = 30) -> tuple[list[dict], list[tuple[float, float]], list[float]]:
    """Фон едет влево (поворот камеры), тёмный шарик летит по параболе и падает (стоит на месте)."""
    big = _texture(5, (H, W * 2))
    frames, pts, shifts = [], [], []
    for i in range(n):
        shift = 3.0 * i
        img = big[:, int(shift): int(shift) + W].copy()
        k = min(i, 18)
        wx, wy = 60 + 9.0 * k, 300 - 22 * k + 1.1 * k * k  # мир (кадр 0)
        x, y = wx - int(shift), wy
        cv2.circle(img, (int(round(x)), int(round(y))), 6, 15, -1)
        cv2.circle(img, (int(round(x)), int(round(y))), 3, 120, -1)
        frames.append({"t": round(i / 60, 4), "piece": 0, "img": img})
        pts.append((x, y))
        shifts.append(int(shift))
    return frames, pts, shifts


def test_track_object_follows_throw_and_stops_on_landing():
    frames, pts, shifts = _throw_frames()
    res = OT.track_point(frames, 0, pts[0][0] / W, pts[0][1] / H, w=0.045)
    seen = [s for s in res["samples"] if s["s"] in ("d", "w")]
    assert len(seen) >= 12
    for s in seen:
        i = int(round(s["t"] * 60))
        assert abs(s["x"] * W - pts[i][0]) < 3 and abs(s["y"] * H - pts[i][1]) < 3, (i, s)
    # Упал и лежит — дальше следа нет (только движение камеры, «l»).
    assert res["samples"][-1]["s"] == "l"
    assert res["lost_at"] is not None and res["lost_at"] <= 0.45
    # Камера: g переводит точку фона кадра i в кадр 0 (фон сдвинут на shift).
    last = res["samples"][-1]
    g = np.array(last["g"]).reshape(2, 3)
    i = int(round(last["t"] * 60))
    p = np.array([100 / W, 200 / H, 1.0])
    wx, _ = g @ p
    assert abs(wx * W - (100 + shifts[i])) < 2.5


def test_track_object_lead_in_extends_arc_back():
    frames, pts, _ = _throw_frames()
    res = OT.track_point(frames, 6, pts[6][0] / W, pts[6][1] / H, w=0.045, lead_in=0.2)
    lead = [s for s in res["samples"] if s["s"] == "e"]
    assert lead and lead[0]["t"] < frames[6]["t"]
    for s in lead[:-1]:
        i = int(round(s["t"] * 60))
        assert abs(s["x"] * W - pts[i][0]) < 6 and abs(s["y"] * H - pts[i][1]) < 6


def test_suggest_surfaces_prefers_textured_flat_area():
    img = np.full((H, W), 110, np.uint8)
    tex = _texture(7, (120, 200))
    img[180:300, 35:235] = tex
    frames = [{"t": i / 30, "img": np.roll(img, i, axis=1)} for i in range(9)]
    cands = ST.suggest_surfaces(frames, 4, aspect=3.0, width=0.5, n=2)
    assert cands
    c = cands[0]
    cy = c["y"] + c["h"] / 2
    assert 180 / H <= cy <= 300 / H


def test_track_storage_engine_data_and_attach(tmp_path, monkeypatch):
    monkeypatch.setattr(WT, "tracks_dir", lambda: tmp_path)
    frames, _ = _plane_frames(10)
    res = WT.compute_surface(frames, 0.1, [[0.3, 0.35], [0.7, 0.35], [0.7, 0.55], [0.3, 0.55]])
    res.update(plan_id=1, fps=30.0, seed={"t": 0.1})
    track_id = WT.save_track(res)
    assert WT.TRACK_ID_RE.match(track_id)
    data = WT.engine_data(WT.load_track(track_id))
    assert data["kind"] == "surface" and len(data["q"]) == 8 * len(data["t"]) and len(data["s"]) == len(data["t"])
    assert data["aspect"] == pytest.approx((0.4 * W) / (0.2 * H), rel=0.02)
    layer = {"id": "w", "type": "tracked_text", "start": 0, "end": 1, "props": {"text": "HI", "track": track_id}}
    got = WT.attach_track_data(layer)
    assert got["props"]["trackData"]["t"] == data["t"]
    notes: list[str] = []
    assert WT.attach_track_data({**layer, "type": "trail"}, notes) is None and "нужен object" in notes[0]
    assert WT.attach_track_data({**layer, "props": {"track": "trk_000000000000"}}, notes) is None
    assert WT.load_track("../etc/passwd") is None


def test_normalize_quad_box_and_rotation():
    q = WT.normalize_quad({"box": {"x": 0.5, "y": 0.5, "w": 0.4, "h": 0.1}}, 9 / 16)
    assert q == [[0.3, 0.45], [0.7, 0.45], [0.7, 0.55], [0.3, 0.55]]
    r = WT.normalize_quad({"box": {"x": 0.5, "y": 0.5, "w": 0.4, "h": 0.1, "angle": 90}}, 9 / 16)
    # 90°: ширина 0.4 кадра (px = 0.4·9/16 высоты) встаёт вертикально.
    xs, ys = [p[0] for p in r], [p[1] for p in r]
    assert max(ys) - min(ys) == pytest.approx(0.4 * 9 / 16, abs=1e-3)
    assert max(xs) - min(xs) == pytest.approx(0.1 * 16 / 9, abs=1e-3)
    with pytest.raises(WT.TrackError):
        WT.normalize_quad({}, 1.0)


def test_schema_keeps_track_ref_and_drops_inline_data():
    from app import motion

    layers = [{"id": "w1", "type": "tracked_text", "start": 1, "end": 2,
               "props": {"text": "STAY", "track": "trk_0123456789ab", "words": [{"t": 1.1}], "reveal": "slam",
                         "trackData": {"t": [1.0]}}},
              {"id": "tr", "type": "trail", "start": 1, "end": 3,
               "props": {"track": "trk_0123456789ab", "style": "dashed", "tail": 0.5}}]
    assert motion.validate_layers(layers) == []
    out = motion.normalize_layers(layers)
    assert "trackData" not in out[0]["props"] and out[0]["props"]["reveal"] == "slam"
    assert out[1]["props"]["style"] == "dashed" and out[1]["props"]["color"] == "#ff3b5c"


def test_lyric_layers_placed_carry_words_and_ids():
    from app.lyrics_layers import build_lyric_layers, tracked_lyric_layer

    lines = [{"i": 3, "text": "stay together", "words": [{"w": "stay", "t0": 2.0, "t1": 2.3}, {"w": "together", "t0": 2.4, "t1": 3.0}]}]
    built = build_lyric_layers(lines, duration=10.0)
    placed = built["placed"][0]
    assert placed["words"] == [{"w": "stay", "t": 2.0}, {"w": "together", "t": 2.4}]
    assert placed["layer_ids"] == [l["id"] for l in built["layers"]]
    layer = tracked_lyric_layer(placed, "trk_0123456789ab", start=1.9, end=3.4, word_times=[2.0, 2.4])
    assert layer["type"] == "tracked_text" and layer["id"].startswith("lyr_")
    assert layer["props"]["text"] == "STAY TOGETHER" and [w["t"] for w in layer["props"]["words"]] == [2.0, 2.4]


def test_place_on_surfaces_replaces_line_or_keeps_screen(tmp_path, monkeypatch):
    from app.lyrics_layers import build_lyric_layers
    from app.lyrics_world import place_on_surfaces

    monkeypatch.setattr(WT, "tracks_dir", lambda: tmp_path)
    lines = [{"i": 0, "text": "stay", "words": [{"w": "stay", "t0": 0.2, "t1": 0.4}]}]
    built = build_lyric_layers(lines, duration=5.0)
    textured, _ = _plane_frames(30)

    def good(a: float, b: float) -> list[dict]:
        return [f for f in textured if a - 1e-6 <= f["t"] <= b + 1e-6]

    out = place_on_surfaces(None, 1, built, lambda t: t, frames_for=good)
    assert [l["type"] for l in out["layers"]] == ["tracked_text"] and out["world"][0]["held"] >= 0.6
    flat = [{"t": f["t"], "piece": 0, "img": np.full((H, W), 100, np.uint8)} for f in textured]
    out2 = place_on_surfaces(None, 1, built, lambda t: t, frames_for=lambda a, b: [f for f in flat if a <= f["t"] <= b])
    assert [l["type"] for l in out2["layers"]] == ["text"] and out2["screen"][0]["i"] == 0


# ---------------------------------------------------------------- сервис целиком: кадры клипа → трек → слой → рендер


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg не установлен")
def test_tools_track_surface_and_object_end_to_end(tmp_path, monkeypatch):
    from app.agent_tools import run_tool
    from app.motion_render import resolve_layer_assets
    from test_agent_tools import _ctx
    from test_tracking import SCALED_W, _plan, expected_frame

    store, plan = _plan(tmp_path, monkeypatch)
    monkeypatch.setattr(WT, "tracks_dir", lambda: tmp_path / "tracks")
    (tmp_path / "tracks").mkdir()
    monkeypatch.setattr("app.motion_render.contact_sheet_jpeg", lambda *a, **k: b"jpeg")
    ctx = _ctx(store, plan["id"])
    img, ok = run_tool(ctx, "track_surface", {"t": 1.0, "box": {"x": 0.5, "y": 0.25, "w": 0.6, "h": 0.1},
                                              "start": 0.5, "end": 2.0, "text": "STAY", "word_times": [1.1]})
    assert ok, img
    info = img.info
    assert info["held"] == 1.0 and info["track_id"].startswith("trk_") and info["added"]
    layers = store.get_clip_plan(plan["id"])["render_settings"]["layers"]
    word = next(l for l in layers if l["type"] == "tracked_text")
    assert word["props"]["track"] == info["track_id"] and word["props"]["words"] == [{"t": 1.1}]
    assert "trackData" not in word["props"]
    # Фон неподвижен — плоскость стоит, где задана.
    data = WT.engine_data(WT.load_track(info["track_id"]))
    assert max(abs(v - w) for v, w in zip(data["q"][-8:], data["q"][:8])) < 0.004

    crop = (SCALED_W - 360) / 2
    x, y = expected_frame(1.0, crop)
    img, ok = run_tool(ctx, "track_object", {"t": 1.0, "x": x, "y": y, "w": 0.14, "end": 2.0, "fps": 25,
                                             "trail": True, "style": "dashed"})
    assert ok, img
    info = img.info
    assert info["found"] >= 0.9 and info["points"] >= 20
    track = WT.load_track(info["track_id"])
    last = [s for s in track["samples"] if s["s"] in ("d", "w")][-1]
    ex, ey = expected_frame(last["t"], crop)
    assert abs(last["x"] - ex) < 6 / 360 and abs(last["y"] - ey) < 6 / 640
    layers = store.get_clip_plan(plan["id"])["render_settings"]["layers"]
    assert any(l["type"] == "trail" and l["props"]["style"] == "dashed" for l in layers)
    kept, _assets, notes = resolve_layer_assets(store, layers)
    assert all(l["props"].get("trackData", {}).get("t") for l in kept if l["type"] in ("tracked_text", "trail"))
    assert len(kept) == len(layers) and not notes


def test_schema_blend_modes_for_tracked_text_and_trail():
    from app import motion

    layers = [{"id": "w", "type": "tracked_text", "start": 0, "end": 1,
               "props": {"text": "HI", "track": "trk_0123456789ab", "blend": "soft-light",
                         "paint": {"shade": 0.5, "texture": 1.2, "grain": 0.1, "soften": 1.5}}},
              {"id": "t", "type": "trail", "start": 0, "end": 1, "props": {"track": "trk_0123456789ab", "blend": "add"}}]
    assert motion.validate_layers(layers) == []
    out = motion.normalize_layers(layers)
    assert out[0]["props"]["blend"] == "soft-light" and out[0]["props"]["paint"]["texture"] == 1.2
    assert out[1]["props"]["blend"] == "add"
    bad = [{**layers[0], "props": {**layers[0]["props"], "blend": "dissolve"}}]
    assert motion.validate_layers(bad)
