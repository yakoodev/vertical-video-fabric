"""Серия 6 «Одно лицо»: комната 3D-пресета за VRM-героиней (та же камера, кадр за кадром), плавный переезд камеры,
купленная модель в эфире — картинки со страницы товара, перетекающие переходы на склейках."""

import sys
from pathlib import Path

from app import vtubika_plan as P
from app import vtubika_room3d as R
from app.vtubika_vrm import to_vrm_spec

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))


def _spec():
    plan = P.load_plan(ROOT / "tools" / "vtubika_plans" / "faces.json")
    toks = P.tokens(P.caption_text(plan))
    words = [{"word": w, "start": round(0.4 + i * 0.42, 3), "end": round(0.4 + i * 0.42 + 0.36, 3)} for i, w in enumerate(toks)]
    total = round(words[-1]["end"] + 1.0, 3)
    built = P.build(plan, words, total)
    return plan, built, to_vrm_spec(plan, built, words, total), total


def test_room_shots_and_camera_match_frame_by_frame():
    plan, _built, spec, total = _spec()
    assert R.room_name(plan) == "cozy_desk"
    n = R.mark_room_shots(spec)
    assert n >= 8
    for sh in spec["shots"]:
        pl = sh["plate"]
        heroine = not pl.get("insert") and pl.get("kind", "front") == "front"
        behind = (pl.get("insert") or {}).get("kind") in ("monitor", "monitor_close", "chat")
        assert bool(pl.get("room")) == (heroine or behind)
        assert "fgDesk" not in pl or not pl.get("room")
    rspec = R.room_spec(plan, spec)
    keys = rspec["camera"]["keys"]
    assert len(keys) == round(total * 30) and rspec["characters"] == []
    hide = rspec["presets"]["room"]["hide"]
    assert {"desk", "chair", "kb"} <= set(hide)
    # Ключ кадра — камера плана героини со сдвигом комнаты (сверка одной точки).
    cams = R.frame_cameras(spec)
    f = next(i for i, c in enumerate(cams) if c)
    off = plan["room3d"]["origin"]
    assert keys[f]["position"] == [round(cams[f][1]["pos"][i] + off[i], 5) for i in range(3)]
    assert any(k.get("cut") for k in keys)


def test_glide_starts_from_previous_camera():
    _plan, _built, spec, _total = _spec()
    glides = [i for i, s in enumerate(spec["shots"]) if s["name"].endswith("_two") and len(s.get("camKeys") or []) == 3]
    assert glides
    i = glides[0]
    prev = spec["shots"][i - 1]
    assert spec["shots"][i]["camKeys"][0]["pos"] == prev["camKeys"][-1]["pos"]


def test_product_avatar_uses_site_pictures():
    _plan, _built, spec, _total = _spec()
    prod = spec["stream"]["product"]
    assert prod["keys"] and {k["face"] for k in prod["keys"]} <= set(prod["faces"])
    for f in prod["faces"].values():
        assert f["url"].startswith("/site/") and f["url"] in spec["siteShots"]
    assert any(a["kind"] == "product" for a in spec["stream"]["avatars"])


def test_flow_layers_hold_both_sides_of_cut():
    import vtubika_story as V

    _plan, built, _spec_, total = _spec()
    layers = V.flow_layers(built["shots"], total)
    trs = [x for x in layers if x["type"] == "transition"]
    assert len(trs) >= 4
    ids = {x["id"]: x for x in layers}
    for tr in trs:
        a_live, a_hold = (ids[i] for i in tr["props"]["from"])
        b_hold, b_live = (ids[i] for i in tr["props"]["to"])
        assert a_live["end"] == a_hold["start"] == b_live["start"] == b_hold["end"]
        assert a_hold["props"]["hold"] and b_hold["props"]["hold"] and not b_live["props"].get("hold")
        assert "x" not in tr["props"] and "dur" not in tr["props"]
