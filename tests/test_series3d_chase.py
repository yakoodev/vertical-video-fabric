"""Ч.5 «Лифт» (app/series3d_chase.py и места в движке): бег по пути автора с корнем по опорной стопе, двойник в ритм героя,
поля автора во фразах (экран переписки, место, свет, фонарик), события «двери разъезжаются» / «телефон вибрирует»,
серия с первого кадра в темноте, звуки шагов. Без Blender."""

from __future__ import annotations

import json
import math
import re

from app import series3d_blender as B
from app import series3d_chase as CH
from app.series3d_blender_perform import clip_to_world, track_of


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


RUN_PATH = [[0.0, 0.05], [0.0, -0.75], [-0.1, -2.0], [-0.3, -6.5]]


def _script():
    chat = {"to": "Мама", "status": "нет сети · 8%", "items": [{"out": "Я дома", "note": "00:12"}, {"photo": "room"}]}
    sent = [
        {"text": "Если в блэкаут двери лифта открылись сами, не оборачивайтесь.", "flash": False,
         "double": {"do": "lie", "pos": [0.12, 0.55], "heading": -1.5708}},
        {"text": "Там новые сообщения от него: «Я дома», «Ложусь».", "screen": chat},
        {"text": "За спиной что-то шевелится.", "double": {"do": "twitch", "at": 0.3}, "fill": 2.0},
        {"text": "Двери с лязгом разъезжаются.", "location": "cabin"},
        {"text": "Даня выбегает из кабины, светит телефоном перед собой.", "location": "floor22",
         "run": {"path": RUN_PATH, "at": 0.2}, "double": {"do": "rise", "at": 0.1}},
        {"text": "Шаги сзади в том же ритме, что и его.", "location": "floor22",
         "run": {"path": [[-0.2, -3.0], [-0.2, -7.5]]}, "double": {"do": "run", "path": [[0.1, -0.5], [0.1, -4.5]]}},
        {"text": "Телефон вибрирует.", "location": "floor22", "flash": False},
    ]
    plans = ["в полной темноте; телефон, страх. План: лицо", "телефон, страх. План: деталь", "телефон, страх. План: сверху",
             "светит, страх. План: общий", "светит, страх. План: лицо", "светит, страх. План: деталь",
             "телефон, страх. План: лицо"]
    beats = [{"i": i, "text": s["text"], "sentence": i, "spec": {"action": p}} for i, (s, p) in enumerate(zip(sent, plans))]
    return {"title": "Кто вышел следом", "sentences": sent}, beats


def _job():
    script, beats = _script()
    words, total = _words(script["sentences"])
    return B.build_job(script, words, total, gemini=False, beats=beats, render_backend="threejs")


# --------------------------------------------------------------------------- цикл и путь

def test_run_cycle_measured_and_moves_forward():
    assert len(CH.RUN_FEET) == 25 and CH.RUN.length == 0.8
    assert 1.0 < CH.RUN.speed < 2.5                       # м/с у героя ростом 1.08 м
    us = [CH.RUN.units(i / 30) for i in range(60)]
    assert all(b >= a for a, b in zip(us, us[1:]))        # корень не едет назад ни на кадре
    sides = [s for _t, s in CH.RUN.strikes()]
    assert sorted(sides) == ["l", "r"]                    # два касания пола за цикл, по ноге


def test_path_point_heading_and_look_ahead():
    p = [[0.0, 0.0], [0.0, -2.0], [2.0, -2.0]]
    assert CH.path_len(p) == 4.0
    x, y, h = CH.path_at(p, 1.0)
    assert (x, y) == (0.0, -1.0) and abs(h) < 1e-9       # по −Y — курс 0
    assert CH.path_at(p, 99.0)[:2] == (2.0, -2.0)
    ax, ay, _ = CH.ahead_point([[0, 0], [0, -1]])
    assert ay < -1.0 and ax == 0.0                       # смотрит вперёд по пути, не назад


# --------------------------------------------------------------------------- поля автора

def test_authored_screen_location_and_cam():
    job = _job()
    shots = job["shots"]
    chat = [s for s in shots if str(s.get("screen") or "").startswith("chat|")]
    assert len(chat) == 1 and chat[0]["action"] == "phone_out" and chat[0]["camera"] == "detail"
    spec = CH.chat_spec(chat[0]["screen"])
    assert spec["to"] == "Мама" and spec["items"][1]["photo"] == "room"
    # «сообщения от него» — не входящее от «Него»: у остальных планов экрана нет
    assert not [s for s in shots if str(s.get("screen") or "").startswith("msg|")]
    assert [s.get("location") for s in shots][-3:] == ["floor22", "floor22", "floor22"]
    assert shots[0]["flash"] is False and shots[2]["fill"] == 2.0


def test_dark_start_doors_open_and_vibrate_events():
    job = _job()
    kinds = {e["kind"]: e["t"] for e in job["events"]}
    assert job.get("start") == ["dark"]
    assert kinds["blackout"] == -1.0 and kinds["lights_out"] == -1.0
    assert "doors_open" in kinds and "vibrate" in kinds
    assert "jolt" not in kinds                            # «с лязгом» — створки, а не рывок кабины
    assert "msg_in" not in kinds                          # «сообщения от него» — без уведомления
    hook_end = job["shots"][1]["t0"]
    assert kinds["doors_open"] > hook_end                 # «Если двери открылись сами…» в крючке — не событие
    perf = B.perform(job)
    f0 = int(1.0 * 30)
    assert perf["lights"][f0]["out"] == 1.0 and perf["lights"][f0]["main"] == 0.0
    assert perf["phone"][f0].get("dim") is None           # в темноте с начала экран телефона светит
    fo = int((kinds["doors_open"] + 2.0) * 30)
    lt = perf["lights"][fo]
    assert lt["doors"] >= CH.DOORS_FULL - 1e-3 and lt["out"] == 0.0 and 0.1 < lt["main"] <= CH.SPILL_MAIN
    assert lt["red"] == 0.0                               # аварийный красный не возвращается
    fv = int((kinds["vibrate"] + 0.1) * 30)
    assert perf["phone"][fv].get("vib") == 1.0
    sfx = B.sfx_for(job, perf)
    labels = {x["kind"] for x in sfx}
    assert {"doors_open", "doors_slide", "vibrate", "step", "step_double", "double_twitch", "double_rise"} <= labels
    assert not [x for x in sfx if x["kind"] in ("lights_out", "blackout")]   # гаснуть нечему


def test_flash_author_override():
    job = _job()
    perf = B.perform(job)
    sh = job["shots"]
    for s in (sh[0], sh[-1]):
        f = int(((s["t0"] + s["t1"]) / 2) * 30)
        assert perf["phone"][f]["light"] == 0.0          # автор погасил фонарик — не бьёт в объектив


# --------------------------------------------------------------------------- бег и двойник

def test_hero_runs_along_path_feet_planted():
    job = _job()
    perf = B.perform(job)
    run_shot = next(s for s in perf["shots"] if s.get("run"))
    f_end = int(run_shot["t1"] * 30) - 2
    x, y = perf["root"][f_end]
    assert y < -2.5 and abs(x) < 0.4                      # выбежал из кабины в коридор по пути
    # смотрит вперёд (не оборачивается): курс около 0 (к −Y), взгляд — впереди по пути
    assert abs(perf["heading"][f_end]) < 0.35
    g = perf["gaze"][f_end]
    assert isinstance(g, tuple) and g[1] < y
    # опорная стопа на полу не скользит (как у шага): по клипу и корню задания
    tr = track_of(perf)
    cuts = {int(round(c["t0"] * 30)) for c in perf["cameras"]}
    prev, slides = None, []
    for f in range(perf["frames"]):
        w = [(lt, ww) for c, lt, ww in tr.weights_at(f / 30) if c == CH.RUN_CLIP]
        if not w or w[0][1] < 0.999 or f in cuts:
            prev = None
            continue
        i = w[0][0] * 30
        a, b = CH.RUN_FEET[int(i) % 24], CH.RUN_FEET[(int(i) + 1) % 24]
        k = i - int(i)
        ly, lz, ry, rz = (a[j] + (b[j] - a[j]) * k for j in range(4))
        side, fy, fz = ("l", ly, lz) if lz <= rz else ("r", ry, rz)
        wx, wy = clip_to_world(tuple(perf["root"][f]), perf["heading"][f], 0.1709 if side == "l" else -0.1709, fy)
        if prev and prev[0] == side and fz < CH.CONTACT_Z:
            slides.append(math.hypot(wx - prev[1], wy - prev[2]) * 1000)
        prev = (side, wx, wy) if fz < CH.CONTACT_Z else None
    slides.sort()
    assert slides and slides[int(len(slides) * 0.95)] < 4.0


def test_run_continues_through_cut_without_new_clip():
    perf = B.perform(_job())
    runs = [sg for sg in perf["track"] if sg["clip"] == CH.RUN_CLIP]
    assert len(runs) == 1                                  # бег идёт через склейку той же фазой (без наплыва в себя)


def test_double_lies_rises_and_runs_in_rhythm():
    job = _job()
    perf = B.perform(job)
    d = perf["double"]
    clips = [sg["clip"] for sg in d["track"]]
    assert clips[0] == CH.LIE_CLIP and CH.RISE_CLIP in clips and CH.RUN_CLIP in clips
    assert d["root"][0] == (0.12, 0.55) and all(g is None for g in d["gaze"])
    # в ритм героя: та же фаза шага с отставанием SYNC_LAG
    run = next(sg for sg in d["track"] if sg["clip"] == CH.RUN_CLIP)
    hero = track_of(perf)
    lt_h = next(lt for c, lt, _w in hero.weights_at(run["start"]) if c == CH.RUN_CLIP)
    assert abs(((lt_h - CH.SYNC_LAG) % CH.RUN.length) - run["offset"]) < 1e-3
    hero_steps = [c["t"] for c in perf["contacts"] if c["kind"] == "step"]
    dbl_steps = [t for t, _ in d["steps"]]
    lag = [min(abs(t - h) for h in hero_steps) for t in dbl_steps if t > run["start"] + 0.3]
    assert lag and max(lag) < 0.12                         # шаги сзади — в том же ритме
    # двойник всё время позади героя в коридоре
    f = int((job["shots"][5]["t0"] + 1.0) * 30)
    assert d["root"][f][1] > perf["root"][f][1] + 1.5


def test_chunk_key_changes_with_double():
    job = _job()
    perf = B.perform(job)
    k1 = B.chunk_keys(job, perf)
    perf["double"]["root"] = [(x + 0.1, y) for x, y in perf["double"]["root"]]
    k2 = B.chunk_keys(job, perf)
    assert [c["key"] for c in k1] != [c["key"] for c in k2]


def test_outro_carries_authored_fields():
    script, beats = _script()
    script["outro"] = [{"text": "Тот стоит в коридоре и ждёт.", "sec": 2.0, "plan": "телефон, страх. План: лицо",
                        "location": "floor22", "double": {"do": "stand", "pos": [0.0, -5.0], "heading": 0.0}}]
    words, total = _words(script["sentences"])
    job = B.build_job(script, words, total, gemini=False, beats=beats)
    last = job["shots"][-1]
    assert last["location"] == "floor22" and last["double"]["pos"] == [0.0, -5.0]
    assert json.dumps(job, ensure_ascii=False)


def test_floor22_and_room_are_author_only():
    assert "floor22" in B.LOCS and "room" in B.LOCS
    assert "floor22" not in B.LOC_NAMES and "room" not in B.LOC_NAMES     # сценарист и режиссёр их не видят
    assert B.loc_target("cabin", "out")[1] < -3.0
