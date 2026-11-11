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
    lv = [static(f / E2.FPS) for f in range(n)]
    assert lv[0] == 0.0  # кадр 0 — чистая обложка
    # Начало: помехи нарастают плавно (не блоком) до полных, держатся и растворяются к HEAD_RAMP[2].
    peak = max(range(45), key=lambda f: lv[f])
    assert lv[peak] > 0.99 and 0.4 <= peak / E2.FPS <= 0.6
    assert all(lv[f] <= lv[f + 1] + 1e-9 for f in range(peak))
    assert max(abs(lv[f + 1] - lv[f]) for f in range(45)) < 0.25  # без скачка «на кадр»
    assert lv[int(E2.HEAD_RAMP[2] * E2.FPS) + 1] == 0.0
    # Конец: так же плавно до полных к последним кадрам.
    tail = lv[n - 30:]
    assert tail[-1] > 0.99 and all(a <= b + 1e-9 for a, b in zip(tail, tail[1:]))
    assert max(b - a for a, b in zip(tail, tail[1:])) < 0.25
    assert scene["titles"][0]["t0"] == 0 and scene["comment"]["style"] == "tiktok"
    # Сборщик — онрё; старого стоп-моушена (stepFps/drop) больше нет.
    assert "stalker" not in scene and scene["onryo"]["model"] == E2.ONRYO_MODEL


def test_onryo_stills_spec():
    """Проба Сборщика v3: 8 планов по 2 с, кадр — в середине плана; онрё вместо старого Сборщика; щель в волосах —
    только в плане видоискателя «щель»; видоискатель (REC) — на планах pov с плёнкой."""
    from app.viewer_ep import scene_base

    spec, marks = E2.onryo_spec(scene_base("street"))
    assert len(marks) == len(spec["shots"]) == 8
    for (t, _), sh in zip(marks, spec["shots"]):
        assert sh["t0"] <= t < sh["t1"]
    assert "stalker" not in spec
    on = spec["onryo"]
    assert on["model"] == E2.ONRYO_MODEL and 1.15 <= on["scale"] <= 1.25
    parted = [k for k in on["keys"] if k.get("part")]
    assert len(parted) == 1 and 8 <= parted[0]["t"] < 10
    assert {c["t0"] for c in spec["cam"]} == {2, 8, 14}


def test_v3_nick_line_and_card_chime_gap():
    """v3: вместо ника вслух — «Спасибо, Неко.» (ник целиком — на карточке); карточка и её звон — в паузе после
    первой фразы, до реплики остаётся место на звон (обрезан за 0.15 с до голоса)."""
    roll = E2.ep2_roll()
    script = E2.ep2_script(roll)
    nick = next(s for s in script if s["beat"] == "nick")
    assert nick["text"] == E2.NICK_LINE == "Спасибо, Неко." and "nick" in E2.TAKE_LINES
    segs, k, beats = E2.ep2_beats(script, _words(script), her_len=2.0)
    total = round(round((beats[-1]["t1"] + E2.TAIL) * E2.FPS) / E2.FPS, 4)
    ev = E2.ep2_scene(beats, total, roll, 2.0, [0.0] * 60)["events"]
    intro = next(b for b in beats if b["beat"] == "intro")
    assert ev["card0"] - intro["t1"] >= 0.15
    assert ev["nick0"] - 0.15 - (ev["card0"] + 0.02) >= 0.35  # звону хватает места до голоса


def test_v3_without_nick_line():
    """Запас v3: реплику про зрителя можно убрать (ник — на карточке); пауза после первой фразы — под карточку и звон."""
    roll = E2.ep2_roll()
    script = E2.ep2_script(roll, drop=("nick",))
    assert [s["beat"] for s in script][:2] == ["intro", "name"]
    segs, k, beats = E2.ep2_beats(script, _words(script), her_len=2.0)
    total = round(round((beats[-1]["t1"] + E2.TAIL) * E2.FPS) / E2.FPS, 4)
    ev = E2.ep2_scene(beats, total, roll, 2.0, [0.0] * 60)["events"]
    name = next(b for b in beats if b["beat"] == "name")
    assert ev["nick0"] == name["t0"] and name["t0"] - ev["card0"] >= 0.8


def test_v3_tool_helpers():
    import sys
    from pathlib import Path

    import numpy as np

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import viewer_ep2 as T

    heard = [{"word": "Спасибо,", "p": 0.95}, {"word": "Неко.", "p": 0.81}]
    assert T.line_heard("Спасибо, Неко.", heard)["ok"]
    assert not T.line_heard("Спасибо, Неко.", [{"word": "Спасибо,", "p": 0.95}, {"word": "некому", "p": 0.9}])["ok"]
    assert not T.line_heard("Спасибо, Неко.", [{"word": "Спасибо,", "p": 0.95}, {"word": "Неко.", "p": 0.5}])["ok"]
    rate = 1000
    x = np.zeros(3 * rate, np.float32)
    x[int(0.5 * rate):int(1.3 * rate)] = 0.3  # «речь» 0.5–1.3 с
    x[int(2.0 * rate):int(2.6 * rate)] = 0.3  # следующее слово с 2.0 с
    assert abs(T.voiced_end(x, rate, 1.0, 2.0) - 1.3) < 0.03  # Whisper сказал «конец 1.0», звук — до 1.3
    assert abs(T.voiced_start(x, rate, 1.4, 2.5) - 2.0) < 0.03
