"""Ч.6 «Лифт» (app/series3d_stairs.py и места в движке): лестничная клетка через несколько этажей, пол под ногами,
корень героя по высоте опоры и стопы на ступенях, эхо шагов «сзади», двойник на ступени, фонарик мигает и гаснет.
Без Blender."""

from __future__ import annotations

import math
import re

from app import series3d_blender as B
from app import series3d_chase as CH
from app import series3d_stairs as ST
from app.series3d_blender_perform import clip_to_world, track_of

OX = ST.ORIGIN[0]
XA, XB = -(ST.GAP + ST.FLIGHT_W / 2), ST.GAP + ST.FLIGHT_W / 2


def W(x, y):
    return [x + OX, y]


# --------------------------------------------------------------------------- раскладка и пол

def test_layout_matches_kenney_stairs_and_stage():
    assert abs(ST.TREAD - 0.284) < 0.002 and abs(ST.RISE - 0.152) < 0.002
    assert abs(ST.FLIGHT_DROP - ST.RISE * ST.STEPS) < 1e-9 and abs(ST.FLOOR_H - 3.35) < 0.01
    assert abs(ST.TREAD * 2 - CH.RUN.total * B.S / 2) < 0.01  # бег героя — через ступень, стопа встаёт на ступень
    loc = B.LOCS[ST.LOC]
    assert tuple(loc["origin"]) == ST.ORIGIN and loc["author_only"]
    for i in range(4):
        tx = B.loc_target(ST.LOC, f"no{i}")
        nx = ST.number_point(i)
        assert math.dist(tx, nx) < 0.02, (i, tx, nx)        # номер этажа на стене — там, куда смотрит автор
    assert ST.FLOORS[0] == ST.FLOORS[3] == "22" and ST.FLOORS[1:3] == ("21", "20")
    assert ST.X_IN > ST.GAP + ST.FLIGHT_W                    # марш не касается стены (без совпадающих плоскостей)


def test_ground_on_landings_and_treads_picks_surface_underfoot():
    f1 = ST.floor_z(1)
    assert ST.ground((OX + 0.5, 0.8), f1 + 0.05) == f1               # площадка 21-го
    assert ST.ground((OX + 0.5, 0.8), 0.0) == 0.0                    # та же точка, этаж выше — площадка 22-го
    # марш A вниз от 22-го: ступень у верха — вровень с площадкой, ниже — на подъём меньше
    top = ST.ground((OX + XA, -0.1), 0.0)
    assert abs(top - 0.0) < 1e-6
    lower = ST.ground((OX + XA, -0.1 - ST.TREAD), 0.0)
    assert abs(lower - (-ST.RISE)) < 1e-6
    # низ марша A — промежуточная площадка, марш B — на этаж ниже к +Y
    assert ST.ground((OX + XA, -3.8), -1.6) == ST.mid_z(0)
    b_low = ST.ground((OX + XB, -0.1), ST.floor_z(1) + 0.3)
    assert abs(b_low - (ST.floor_z(1) + ST.RISE)) < 1e-6
    # пролёт между маршами — пола нет: высота прошлой опоры
    assert ST.ground((OX, -1.5), -0.7) == -0.7
    # подошва над краем ступени — берётся верхняя (носок не уходит в ступень)
    edge = ST.TREAD * 5 - ST.FLIGHT_RUN - 0.01          # чуть ниже края ступени 5 марша A
    assert ST.footprint_ground((OX + XA, edge), 0.0, ST.mid_z(0) + 1.0) >= ST.ground((OX + XA, edge), ST.mid_z(0) + 1.0)


def test_ahead_z_walks_the_route_not_the_floor_above():
    path = [W(XA, 0.4), W(XA, -3.5), W(0.0, -4.2), W(XB, -3.5), W(XB, 0.55)]
    assert abs(ST.ahead_z(ST.LOC, path, 0.0) - ST.floor_z(1)) < 1e-6
    assert ST.ahead_z("floor22", path, 0.0) == 0.0


# --------------------------------------------------------------------------- опора по кадрам

def _words(sentences, pause=0.4):
    out, t = [], 0.05
    for s in sentences:
        for w in s["text"].split():
            n = len(re.sub(r"[^\wё]", "", w))
            if not n:
                continue
            d = 0.1 + 0.055 * n
            out.append({"word": w, "start": round(t, 3), "end": round(t + d, 3)})
            t += d + 0.04
        t += pause
    return out, t + 0.5


DOWN = [W(XA, 0.4), W(XA, -3.4), W(-0.5, -4.0), W(0.0, -4.2), W(0.5, -4.0), W(XB, -3.4), W(XB, 0.5)]


def _job(extra=None):
    sent = [
        {"text": "Даня бежит вниз по лестнице, светит телефоном перед собой в темноте подъезда.", "location": "stairwell",
         "z": 0.0, "run": {"path": DOWN, "at": 0.2}, "double": {"do": "echo", "pos": [OX, -40.0]}},
        {"text": "Шаги сзади в том же ритме, что и его, по бетонным ступеням вниз.", "location": "stairwell",
         "double": {"do": "stand"}},
        {"text": "Сверху, совсем рядом, тоже его голос, шёпотом.", "location": "stairwell", "look": "above",
         "double": {"do": "stand", "pos": W(XB, -0.9), "heading": math.pi, "z": -1.0}},
        {"text": "Фонарик мигает. Темнота.", "location": "stairwell", "flicker": {"at": 0.3, "dark": 1.2}},
    ]
    for k, v in (extra or {}).items():
        sent[k].update(v)
    plans = ["светит, страх. План: общий", "светит, страх. План: деталь", "светит, страх. План: лицо",
             "светит, страх. План: лицо"]
    beats = [{"i": i, "text": s["text"], "sentence": i, "spec": {"action": p}} for i, (s, p) in enumerate(zip(sent, plans))]
    words, total = _words(sent)
    return B.build_job({"title": "Этаж, которого нет", "sentences": sent}, words, total, gemini=False, beats=beats,
                       render_backend="threejs")


def test_run_down_stairs_root_drops_and_planted_feet_stay_on_treads():
    job = _job()
    perf = B.perform(job)
    rz, fd = perf["root_z"], perf["feet_dz"]
    assert len(rz) == len(fd) == perf["frames"]
    track = track_of(perf)
    run_f = [f for f in range(perf["frames"]) if any(c == CH.RUN_CLIP and w > 0.99 for c, _l, w in track.weights_at(f / 30))]
    assert run_f
    # вниз по лестнице: корень не поднимается на бегу (без подскоков вверх), сошёл на этаж ниже
    seq = [rz[f] for f in run_f]
    assert all(b <= a + 1e-6 for a, b in zip(seq, seq[1:]))
    assert ST.floor_z(1) - 1e-6 <= min(rz) < ST.mid_z(0) - 0.3     # прошёл площадку между этажами и ниже
    # опорная стопа: цель IK ровно на ступени под подошвой (корень + dz = пол там, где стопа стоит)
    checked = 0
    for f in run_f:
        lt = next(l_ for c, l_, w in track.weights_at(f / 30) if c == CH.RUN_CLIP)
        i = lt * 30
        a_, b_ = CH.RUN_FEET[int(i) % 24], CH.RUN_FEET[(int(i) + 1) % 24]
        k = i - int(i)
        ly, lz, ry, rzz = (a_[j] + (b_[j] - a_[j]) * k for j in range(4))
        for side, y, z, dz, sx in (("l", ly, lz, fd[f][0], ST.FOOT_X), ("r", ry, rzz, fd[f][1], -ST.FOOT_X)):
            if z >= CH.CONTACT_Z:
                continue
            xy = clip_to_world(tuple(perf["root"][f]), perf["heading"][f], sx, y)
            g = ST.footprint_ground(xy, perf["heading"][f], rz[f] + dz + 0.05)
            assert abs(rz[f] + dz - g) < 0.02, (f, side, rz[f], dz, g)
            assert dz >= -1e-6                                    # опорной ноге — только согнуться, не тянуться вниз
            checked += 1
    assert checked > 40
    # в полёте стопа не ниже ступени под собой (не проходит сквозь край)
    assert max(max(abs(a), abs(b)) for a, b in (v for v in fd if v)) < 0.45


def test_echo_steps_follow_hero_rhythm_and_stop_with_him():
    perf = B.perform(_job())
    d = perf["double"]
    hero = sorted(c["t"] for c in perf["contacts"] if c["kind"] == "step")
    echo = [t for t, _w in d["steps"]]
    sh = perf["shots"][0]
    mine = [t for t in hero if sh["t0"] - 1e-6 <= t < sh["t1"]]
    assert mine and len(echo) == len(mine)
    assert all(abs(e - (h + CH.ECHO_LAG)) < 1e-3 for e, h in zip(echo, mine))
    assert max(echo) <= max(hero) + CH.ECHO_LAG + 1e-3          # встал — шаги сзади тоже замерли
    # двойник на ступени: корень поднят на высоту ступени со склейки плана
    f3 = int(round(perf["shots"][2]["t0"] * 30)) + 2
    assert d["root_z"][f3] == -1.0 and d["root_z"][0] == 0.0
    sfx = B.sfx_for(_job(), perf)
    assert len([x for x in sfx if x["kind"] == "step_double"]) == len(echo)


def test_flicker_then_darkness_kills_flash_fill_and_screen():
    job = _job()
    perf = B.perform(job)
    sh = perf["shots"][3]
    t_dark = round(sh["t0"] * 30) / 30 + 1.2
    lt, ph = perf["lights"], perf["phone"]
    f_dark = int(t_dark * 30) + 2
    assert all(lt[f].get("black") == 1.0 for f in range(f_dark, perf["frames"]))
    assert all(ph[f]["light"] == 0.0 for f in range(f_dark, perf["frames"]) if ph[f]["on"])
    assert all(ph[f].get("dim", 1.0) == 0.0 for f in range(f_dark, perf["frames"]))
    # мигает: до темноты фонарик то горит, то нет
    win = [ph[f]["light"] for f in range(int((t_dark - 0.9) * 30), int(t_dark * 30))]
    assert 0.0 in win and max(win) > 0.5
    spans = ST.flicker_spans(10.0, 12.0)
    assert spans[0][0] == 10.0 and spans[-1] == (12.0, 1e9, 0.0)
    # старые серии: полей лестницы и темноты нет (ключи кэша прежние)
    old = B.perform(_job({0: {"location": "floor22", "run": {"path": [[0.0, -1.0], [0.0, -5.0]], "at": 0.2}, "z": None},
                          1: {"location": "floor22"}, 2: {"location": "floor22"},
                          3: {"location": "floor22", "flicker": None}}))
    assert "feet_dz" not in old and not any("black" in x for x in old["lights"])


def test_look_before_run_then_ahead():
    job = _job({0: {"look": "no0", "run": {"path": DOWN, "at": 1.2}}})
    perf = B.perform(job)
    sh = perf["shots"][0]
    f_look = int(round(sh["t0"] * 30)) + 6
    g = perf["gaze"][f_look]
    assert isinstance(g, tuple) and math.dist(g, B.loc_target(ST.LOC, "no0")) < 0.05
    f_run = int((sh["t0"] + 2.2) * 30)
    g2 = perf["gaze"][f_run]
    assert isinstance(g2, tuple) and g2[2] < 0.0          # на бегу — вперёд по пути, с высоты пола этажа ниже
