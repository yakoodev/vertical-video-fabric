"""Мем-серия 10 «Сделай за ночь»: монтажная сетка, два голоса без наездов и с субтитрами у обоих, часы 23→07, дерево
ровно на 47 файлов, стопка документации, утро в окне, приседание и обрыв музыки, звуки из паков, надписи ≤ 40% кадра
(tools/meme3d_ep10*.py)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import meme3d_ep9_anim as A9  # noqa: E402
import meme3d_ep10 as E  # noqa: E402
import meme3d_ep10_anim as A  # noqa: E402
import meme3d_ep10_mix as M  # noqa: E402
import meme3d_ep10_voice as VO  # noqa: E402


def test_length_and_cuts_every_one_to_two_seconds():
    assert 35.0 <= E.DURATION <= 40.0
    shots = A.SHOTS
    assert shots[0] == (0.0, pytest.approx(0.6), "cover") and shots[-1][1] == pytest.approx(A.DUR)
    for (_a0, a1, _n), (b0, _b1, _m) in zip(shots, shots[1:]):
        assert a1 == pytest.approx(b0)
    for a0, a1, name in shots:
        assert 0.5 <= a1 - a0 <= 2.0 + 1e-6, f"{name}: {a1 - a0:.2f} с"
        n = round(a0 * A.STEP_FPS)
        assert 0 <= n / A.STEP_FPS - a0 < 1e-3, f"{name}: склейка не на сетке шага поз"


def test_two_voices_take_turns_and_text_matches_tts():
    claude = {ln[1] for ln in E.LINES if not E.is_user(ln[0])}
    assert claude == {VO.T1} and VO.CLAUDE_VOICE == "callum" and VO.USER_VOICE == "liam"
    assert {ln[0] for ln in E.USER_LINES} == {lid for lid, _t in VO.USER_TTS}
    spans = sorted((at, at + (b - a), E.is_user(lid)) for lid, _t, a, b, at, _x in E.LINES)
    for (_a0, a1, ua), (b0, _b1, ub) in zip(spans, spans[1:]):
        # Голоса не наезжают: между сменой голоса ≥ 0.3 с, между фразами Клода — пауза пробы (≥ 0.25 с).
        assert b0 >= a1 + (0.3 if ua != ub else 0.25) - 1e-9, (a1, b0)
    # Реплики Клода — по порядку пробы, текст субтитров = текст синтеза.
    starts = [a for _l, _t, a, _b, _at, _x in E.CLAUDE_LINES]
    assert starts == sorted(starts)
    assert M._tokens(" ".join(ln[5] for ln in E.CLAUDE_LINES)) == M._tokens(VO.CLAUDE_TTS.replace("...", " "))
    for (lid, tts), ln in zip(VO.USER_TTS, E.USER_LINES):
        assert M._tokens(tts) == M._tokens(ln[5]), lid
    for phrase in ("Одна кнопка", "Принято", "база данных", "очередь сообщений", "Тридцать страниц", "Сорок семь файлов", "Красота",
                   "второй спринт", "где кнопка", "Я спать"):
        assert phrase in " ".join(ln[5] for ln in E.LINES)


def test_captions_for_both_voices_user_in_its_colour():
    lines = [{"line": lid, "user": E.is_user(lid), "text": tx, "heard": [], "start": at, "end": at + b - a} for lid, _t, a, b, at, tx in E.LINES]
    words = M.caption_words(lines)
    user = [w for w in words if w.get("s") == "B"]
    assert " ".join(w["w"] for w in user).startswith("Клод,") and any("кнопка" in w["w"] for w in user)
    for w in user:
        assert any(ln["start"] - 0.01 <= w["t0"] <= ln["end"] for ln in lines if ln["user"])
    caps = [ly for ly in M.caption_layers() if ly["id"] == "caps"][0]
    assert caps["y"] >= 0.78 and caps["props"]["speakerColors"][0]["s"] == "B"


def test_user_speaks_only_on_user_shots():
    for lid, _t, a, b, at, _x in E.USER_LINES:
        for s0, s1, name in A.SHOTS:
            if s0 < at + (b - a) - 0.05 and s1 > at + 0.05:
                assert name.startswith(("u_", "chat")), f"{lid} звучит на плане {name}"


def test_clock_jumps_two_hours_on_each_clock_shot():
    spins = E.clock_spins()
    assert E.CLOCK_START == 11 * 60 and [s["add"] for s in spins] == [120] * 4
    assert [h for _t, h in E.HOURS] == ["23:00", "01:00", "03:00", "05:00", "07:00"]
    names = ["clock01", "clock03", "clock05", "morning"]
    for s, n in zip(spins, names):
        a, b = A.SH[n]
        assert a <= s["t0"] < s["t1"] <= b, n
    for t, _h in E.HOURS:
        assert any(abs(s0 - t) < 1e-6 for s0, _s1, _n in A.SHOTS), "час — на склейке"


def test_tree_has_47_files_last_on_the_word_and_no_button():
    assert E.FILES_TOTAL == 47 and E.HOOK == "47 ФАЙЛОВ"
    times = [e["t"] for e in E.TREE]
    assert times == sorted(times)
    names = {e["name"] for e in E.TREE}
    for n in ("auth-service/", "queue/", "k8s/", "docs/", "db/"):
        assert n in names
    assert not any("button" in n.lower() or "кноп" in n.lower() or "frontend" in n for n in names)
    assert E.AT["c6a"] <= E.T47 <= E.END["c6a"], "47-й файл — на «Сорок семь файлов»"
    assert A.SH["tree47"][0] <= E.T47 < A.SH["tree47"][1]
    # «очередь сообщений» → queue/ появляется на этом слове, на плане дерева.
    q = [e["t"] for e in E.TREE if e["name"] == "queue/"][0]
    assert E.AT["c4"] + 0.8 <= q <= E.END["c4"] and A.SH["tree3"][0] <= q < A.SH["tree3"][1]
    assert E.files_at(E.AT["c2"]) == 0 and E.files_at(E.DURATION) == 47


def test_docs_pile_lands_on_thirty_pages_and_tea_cools():
    docs = [o for o in E.room_objects() if o["id"] == "docs"][0]
    p = docs["params"]
    assert p["count"] == 30 and "{n}" in p["sub"]
    a, b = p["land"]
    assert A.SH["docs"][0] <= a < b <= A.SH["docs"][1]
    assert a <= E.AT["c5b"] + 0.3 and b >= E.END["c5b"] - 0.3, "листы падают под «Тридцать страниц»"
    assert E.STEAM_POWER[-1]["v"] == 0 and E.STEAM_POWER[-1]["t"] <= E.CLOCK_05


def test_morning_window_and_light_switch_at_seven():
    objs = {o["id"]: o for o in E.room_objects()}
    assert objs["window"]["visible"][-1] == {"t": E.MORNING, "on": False}
    assert objs["window_am"]["visible"][:2] == [{"t": 0, "on": False}, {"t": E.MORNING, "on": True}]
    assert objs["window_am"]["params"]["stars"] == 0 and objs["window"]["params"]["stars"] > 0
    dawn = [lt for lt in E.lights() if lt.get("id") == "dawn"][0]
    assert dawn["keys"][0]["intensity"] == 0 and dawn["keys"][-1]["intensity"] > 0 and dawn["keys"][-1]["t"] == E.MORNING
    assert dawn["beam"]["opacity"] <= 0.1, "столб света без пересвета"
    assert [ph["t"] for ph in E.environment([])["phases"]][-1] == E.MORNING


def test_site_has_no_button_and_panels_only_in_their_shots():
    assert E.site_params()["empty"] is True
    objs = {o["id"]: o for o in A.objects()}
    for oid, names in (("tree", {"tree1", "tree2", "tree3", "tree47"}), ("site", {"site"}), ("chat", {"chat_ask"})):
        on = [k["t"] for k in objs[oid]["visible"] if k["on"]]
        assert {n for s0, _s1, n in A.SHOTS if any(abs(s0 - t) < 1e-6 for t in on)} == names


def test_music_ducks_and_stops_for_the_awkward_pause():
    spans = [(at, at + (b - a)) for _l, _t, a, b, at, _x in E.LINES]
    curve = A9.duck_curve(spans, A.DUR, A9.MUSIC_GAP_LU, A9.MUSIC_SPEECH_LU)
    for (a, b), lid in zip(spans, [ln[0] for ln in E.LINES]):
        assert curve[int(round((a + b) / 2 * 100))] == pytest.approx(A9.MUSIC_SPEECH_LU), lid
    assert E.END["u2"] < M.MUSIC_OFF < E.AT["c7a"] and M.MUSIC_BACK > E.END["c7b"]


def test_sfx_are_real_recordings_with_volumes():
    for s in M.sfx_list():
        assert any(p in ("freesound", "kenney-interface", "kenney-impact") for p in Path(s["path"]).parts)
        assert 0 < s["volume"] <= 0.5 and 0 <= s["t"] < A.DUR
    if (A.P1.PACKS / "freesound").is_dir():
        assert not [str(s["path"]) for s in M.sfx_list() if not Path(s["path"]).is_file()]
    # В паузе после «где кнопка?» — только тиканье часов.
    pause = [s for s in M.sfx_list() if E.END["u2"] + 0.3 < s["t"] < E.AT["c7a"]]
    assert pause and all("tick" in Path(s["path"]).name for s in pause)


def test_overlays_under_forty_percent_and_hours_off_speech():
    share = M.overlay_share()
    assert share["hook"] < 0.4 and max(share.values()) < 0.4
    for label, a, b, _nxt in M.hour_windows():
        for lid, _t, la, lb, at, _x in E.LINES:
            assert not (a < at + (lb - la) and at < b), f"{label} поверх реплики {lid}"


def test_tokens_normalize_whisper_numbers():
    assert M._tokens("47 файлов. 30 страниц") == ["сорок", "семь", "файлов", "тридцать", "страниц"]
    assert M._tokens("Микросервисам — очередь") == ["микросервисам", "очередь"]


def test_text_bands_catch_face_under_hour_stamp():
    cx, cy, cz = E.CL
    fy = cy + A.FY
    # Камера смотрит ниже лица — лицо уезжает в верх кадра, под крупное время: должна поймать.
    d = np.asarray([cx, fy - 0.12, cz]) - np.asarray([cx, fy, cz + 0.8])
    up = {"camera": [cx, fy, cz + 0.8], "cameraDir": list(d / np.linalg.norm(d)), "characters": {"claude": {"shown": True}}}
    assert M.text_over_face([(1.0, "hour", up)]), "лицо под крупным временем — ошибка"
    cu = {"camera": [cx + 0.02, fy + 0.04, cz + 1.0], "cameraDir": [0, -0.05, -1], "characters": {"claude": {"shown": True}}}
    assert M.text_over_face([(1.0, "caps", cu), (1.0, "chip", cu)]) == []


def test_mug_flies_after_tea_and_spend_cap():
    keys = A.default_mug_keys()
    assert keys[1]["t"] == A.TEA and keys[2]["position"] == E.SIP
    assert A.TEA > E.END["c7b"]
    assert VO.spent(Path("нет-такого-файла.log")) == 0.0


def test_voice_provenance_catches_swapped_voice(tmp_path):
    import json

    for lid, take, *_r in E.LINES:
        (tmp_path / Path(take).with_suffix(".json").name).write_text(json.dumps({"voice": VO.USER_VOICE if E.is_user(lid) else "callum"}), "utf-8")
    assert M.voice_provenance(tmp_path)["issues"] == []
    (tmp_path / Path(E.user_take("u2")).with_suffix(".json").name).write_text(json.dumps({"voice": "callum"}), "utf-8")
    assert any(s.startswith("u2") for s in M.voice_provenance(tmp_path)["issues"])
