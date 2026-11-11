"""Серия 2 «Сара»: честный бросок, камера видоискателя, тайминги помех и сцена без озвучки (слова — синтетические)."""

from __future__ import annotations

import math

from app import viewer_ep2 as E2


def _words(script: list[dict], dur: float = 0.32) -> list[dict]:
    out, t = [], 0.0
    for s in script:
        for w in s["text"].split():
            if any(ch.isalnum() for ch in w):
                out.append({"word": w, "start": round(t, 3), "end": round(t + dur, 3)})
                t += dur + 0.05
        t += 0.4
    return out


def test_roll_is_honest_and_fixed():
    r = E2.ep2_roll()
    res = r["result"]
    assert r["seed"] == "серия 2|Сара|записать и спрятаться"
    assert (res["stat"], res["dc"], res["die"], res["bonus"], res["total"], res["ok"]) == ("ЛОВКОСТЬ", 12, 15, 0, 15, True)
    assert r["heroine"]["name"] == "Сара"


def test_script_matches_roll():
    roll = E2.ep2_roll()
    texts = [s["text"] for s in E2.ep2_script(roll)]
    assert "Пятнадцать. Тебя не видно." in texts


def test_pov_key_puts_camera_at_point():
    k = E2.pov_key(0, [-0.1, 1.42, 0.62], [-0.9, 2.0, 3.8], 30)
    yaw = math.radians(k["yaw"])
    pos = [k["target"][0] + math.sin(yaw) * k["dist"], k["height"], k["target"][2] + math.cos(yaw) * k["dist"]]
    assert all(abs(a - b) < 1e-3 for a, b in zip(pos, [-0.1, 1.42, 0.62]))


def test_scene_static_frames_and_order():
    roll = E2.ep2_roll()
    script = E2.ep2_script(roll)
    segs, k, beats = E2.ep2_beats(script, _words(script), her_len=2.0)
    total = round(round((beats[-1]["t1"] + E2.TAIL) * E2.FPS) / E2.FPS, 4)
    scene = E2.ep2_scene(beats, total, roll, 2.0, [0.0] * 60)
    ev = scene["events"]
    order = ["card0", "her0", "raise", "pov1", "roll", "land", "pov2", "zoom0", "zoom1", "eye", "pov3", "beep", "snap", "tear",
             "final", "choice", "cta", "static_end"]
    assert [ev[n] for n in order] == sorted(ev[n] for n in order)
    keys = scene["fx_keys"]

    def static(t):
        ks = [x for x in keys if "static" in x]
        for a, b in zip(ks, ks[1:]):
            if a["t"] <= t < b["t"]:
                return a["static"] + (b["static"] - a["static"]) * (t - a["t"]) / (b["t"] - a["t"])
        return ks[-1]["static"]

    n = int(round(total * E2.FPS))
    head = [f for f in range(20) if static(f / E2.FPS) > 0.99]
    tail = [f for f in range(n - 30, n) if static(f / E2.FPS) > 0.99]
    assert head == list(range(1, 11))  # кадр 0 — обложка, потом ровно 10 кадров помех
    assert tail == list(range(n - E2.END_STATIC, n))
    assert scene["titles"][0]["t0"] == 0 and scene["comment"]["style"] == "tiktok"
