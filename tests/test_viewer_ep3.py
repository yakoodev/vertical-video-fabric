"""Серия 3 «Замри»: честный бросок ВОЛИ, порядок событий, помехи, онрё (без новых обликов), шаги — без касаний."""

from __future__ import annotations

import math

from app import viewer_ep3 as E3


def _beats(her_len: float = 1.6, pa_len: float = 1.3):
    roll = E3.ep3_roll()
    script = E3.ep3_script(roll)
    words = E3.estimate_words(" ".join(s["text"] for s in script))
    segs, k, beats = E3.ep3_beats(script, words, her_len, pa_len)
    total = round(round((beats[-1]["t1"] + E3.TAIL) * E3.FPS) / E3.FPS, 4)
    return roll, script, beats, total


def test_roll_is_honest_and_fixed():
    r = E3.ep3_roll()
    res = r["result"]
    assert r["seed"] == "серия 3|Сара|замереть и перепрятаться"
    assert (res["stat"], res["dc"], res["die"], res["bonus"], res["total"], res["ok"]) == ("ВОЛЯ", 12, 12, 1, 13, True)
    assert r["heroine"]["stats"]["ВОЛЯ"] == 1 and r["heroine"]["name"] == "Сара"


def test_script_has_no_nick_and_matches_roll():
    roll, script, _, _ = _beats()
    texts = " ".join(s["text"] for s in script).casefold()
    assert "тринадцать. еле-еле." in texts
    assert "неко" not in texts and "flood" not in texts and "флуд" not in texts  # ник только на карточке
    assert E3.COMMENT["likes"] < 0  # число лайков не выдумываем


def test_events_order_length_and_static_ramps():
    roll, script, beats, total = _beats()
    scene = E3.ep3_scene(beats, total, roll, 1.6, 1.3)
    ev = scene["events"]
    order = ["card0", "app0", "eyes", "close0", "stop", "roll", "land", "pa0", "pa_voice", "pa1", "leave0", "pov0", "crunch",
             "look0", "resume", "col0", "raise", "self0", "her0", "her1", "reveal", "flick", "wide", "choice", "cta", "static_end"]
    assert [ev[n] for n in order] == sorted(ev[n] for n in order)
    assert 45.0 <= total <= 70.0
    from app import psx_look as L

    n = int(round(total * E3.FPS))
    lv = L.static_levels(scene["fx_keys"], n, E3.FPS)
    # Кадр 0 и весь крючок (до первой склейки без пары кадров) — чистая обложка: её лицо + титр (lag_report.md).
    first_cut = int(round(ev["card0"] * E3.FPS))
    assert max(lv[: first_cut - 1]) == 0.0
    runs = L.static_runs(lv)
    # Помехи — короткие всплески ≤ 6 кадров, приглушённые; в конце — 6 кадров.
    assert runs and all(r["frames"] <= L.BURST_FRAMES for r in runs), runs
    assert all(r["peak"] <= L.BURST_PEAK_MAX + 1e-9 for r in runs[:-1])
    assert runs[0]["start"] <= first_cut < runs[0]["start"] + runs[0]["frames"]  # первый всплеск — на склейке крючка
    assert runs[-1]["start"] + runs[-1]["frames"] == n and lv[-1] >= 0.7
    # Срывы трекинга по 0.1 с (+0.1 с выхода в vhs.ts) — тоже ≤ 6 кадров.
    assert all(b - a <= E3.TEAR + 1e-9 for a, b in scene["vhs"]["tears"])
    assert scene["titles"][0]["t0"] == 0 and scene["titles"][0]["lines"] == ["НЕ ДЫШИ"]
    assert scene["roll"]["stat"] == "ВОЛЯ" and scene["comment"]["nick"] == "Neko_Sil Flood"
    assert scene["cards"][0]["lines"][-1] == "4 — СВОЁ"


def test_onryo_is_the_approved_look_and_keeps_distance():
    """Тот же онрё серии 2 (модель, перекраска), шаркает; на виду не ближе 0.8 м от неё (центр к центру)."""
    from app.viewer_ep2 import ONRYO_MODEL

    roll, script, beats, total = _beats()
    scene = E3.ep3_scene(beats, total, roll, 1.6, 1.3)
    o = scene["onryo"]
    assert o["model"] == ONRYO_MODEL and o["walk"]["stride"] > 0
    from app.viewer_ep2 import ONRYO_LOOK

    assert o["look"] == ONRYO_LOOK
    ks = o["keys"]
    shown = lambda t: any(a <= t < b for a, b in o["show"])  # noqa: E731

    def pos(t):
        for a, b in zip(ks, ks[1:]):
            if a["t"] <= t < b["t"]:
                u = (t - a["t"]) / (b["t"] - a["t"])
                e = u * u * (3 - 2 * u)
                return [a["pos"][i] + (b["pos"][i] - a["pos"][i]) * e for i in range(3)]
        return ks[-1]["pos"]

    t = 0.0
    while t < total:
        if shown(t):
            p = pos(t)
            assert math.hypot(p[0], p[2]) >= 0.8, (t, p)
        t += 1 / 30


def test_sneak_keys_smooth_and_stop_on_crunch():
    keys = E3.sneak_keys(0.0, 2.0, 3.5, 5.0, [-0.35, 1.35, 6.9])
    assert all(abs(b["t"] - a["t"] - 0.1) < 1e-6 for a, b in zip(keys, keys[1:]))
    # Пока стоит (после хруста и затухания шага) — камера на месте.
    still = [k for k in keys if 2.35 < k["t"] < 3.45]
    assert len({(k["dist"], k["yaw"], k["height"]) for k in still}) == 1
