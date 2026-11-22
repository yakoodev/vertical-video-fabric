"""Мем-серия 9 «Промпт на три страницы»: монтажная сетка, голос только Клода, простыня, звуки из паков, проверки
непрерывности голоса (tools/meme3d_ep9.py, tools/meme3d_ep9_anim.py)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import meme3d_ep9 as E  # noqa: E402
import meme3d_ep9_anim as A  # noqa: E402


def test_length_and_cuts_every_one_to_two_seconds():
    assert 30.0 <= E.DURATION <= 35.0
    shots = A.SHOTS
    assert shots[0][0] == 0.0 and shots[-1][1] == pytest.approx(A.DUR)
    for (a0, a1, _n), (b0, _b1, _m) in zip(shots, shots[1:]):
        assert a1 == pytest.approx(b0), "планы подряд, без дыр"
    for a0, a1, name in shots:
        assert 0.5 <= a1 - a0 <= 2.0, f"{name}: {a1 - a0:.2f} с"
    assert shots[0][2] == "cover"
    for a0, _a1, name in shots:
        n = round(a0 * A.STEP_FPS)
        assert 0 <= n / A.STEP_FPS - a0 < 1e-3, f"{name}: склейка не на сетке шага поз (не позже шага)"


def test_only_claude_speaks_and_lines_do_not_overlap():
    assert {ln[1] for ln in E.LINES} == {"take1_callum.mp3"}
    spans = sorted((at, at + (b - a)) for _i, _t, a, b, at, _x in E.LINES)
    for (_a0, a1), (b0, _b1) in zip(spans, spans[1:]):
        assert b0 >= a1 + 0.3, "фразы не наезжают"
    assert spans[-1][1] < E.DURATION
    # Реплики сценария — дословно.
    text = " ".join(ln[5] for ln in E.LINES)
    for phrase in ("Тридцать лет опыта. Мне два года", "Чаевые, кстати, ни разу не пришли", "Я прочитал только последнюю строчку",
                   "Я знаю. Тридцать лет опыта"):
        assert phrase.replace(". ", " ") in text.replace(". ", " ")


def test_prompt_wall_has_key_phrases_and_small_last_line():
    texts = [p["text"] for p in E.PARAS]
    marked = " ".join(texts)
    for key in ("**тридцатилетним опытом**", "**Ты гений.**", "**двести долларов чаевых**", "**бабушка**"):
        assert key in marked
    assert E.PARAS[-1]["kind"] == "last" and "поменяй цвет кнопки на" in E.PARAS[-1]["text"]
    times = [k["t"] for k in E.SCROLL]
    assert times == sorted(times)
    assert E.SCROLL[-1]["v"] == 1.0 and E.SCROLL[-1]["t"] <= E.ZOOM
    assert [p["text"] for p in E.PAGES] == ["стр. 1 из 3", "стр. 2 из 3", "стр. 3 из 3"]
    # Стим перестаёт идти до плана «чай остыл».
    assert E.STEAM_POWER[-1]["v"] == 0 and E.STEAM_POWER[-1]["t"] <= E.TEA2


def test_wall_visible_only_in_wall_shots_and_camera_matches():
    cam, trans = A.camera()
    assert cam["keys"][-1]["t"] == pytest.approx(A.DUR)
    wall_names = {n for _a, _b, n in A.SHOTS if n.startswith(("wall", "page", "last", "zoom"))}
    for a0, a1 in A.WALL_WINDOWS:
        inside = {n for s0, s1, n in A.SHOTS if s0 >= a0 - 1e-6 and s1 <= a1 + 1e-6}
        assert inside and inside <= wall_names
    assert all(t["t"] < A.DUR for t in trans)


def test_syllables_drive_visemes():
    words = [{"w": "Тридцать", "t0": 1.0, "t1": 1.6}, {"w": "лет", "t0": 1.6, "t1": 1.8}]
    sw = A.syllable_words(words)
    assert sw[0]["syl"] == [1.0, 1.3] and sw[1]["syl"] == [1.6]
    keys = A.syllable_words(words)
    vis = A.H.viseme_keys_from_words(keys)
    assert vis and all(vis[i]["t"] < vis[i + 1]["t"] for i in range(len(vis) - 1))


def test_hook_and_captions_layout():
    layers = {ly["id"]: ly for ly in A.caption_layers()}
    assert layers["hook"]["props"]["text"] == "3 СТРАНИЦЫ" and layers["hook"]["start"] == 0.0
    assert layers["caps"]["y"] >= 0.78, "субтитры внизу, не на лице"


def test_sfx_are_real_recordings_with_volumes():
    for s in A.sfx_list():
        parts = Path(s["path"]).parts
        assert any(p in ("freesound", "kenney-interface", "kenney-impact") for p in parts)
        assert 0 < s["volume"] <= 0.5
        assert 0 <= s["t"] < A.DUR
    if (A.P1.PACKS / "freesound").is_dir():
        missing = [str(s["path"]) for s in A.sfx_list() if not Path(s["path"]).is_file()]
        assert not missing


def _voice_track(sr=8000):
    x = np.zeros(int(A.DUR * sr), dtype=np.float32)
    rng = np.random.default_rng(1)
    for _i, _t, a, b, at, _x in E.LINES:
        k0, k1 = int(at * sr), int((at + b - a) * sr)
        x[k0:k1] = 0.2 * np.sin(np.arange(k1 - k0) * 0.3) + 0.01 * rng.standard_normal(k1 - k0)
    return x


def test_voice_joins_clean_track_passes_and_catches_breaks():
    sr = 8000
    x = _voice_track(sr)
    assert A.voice_joins(x, sr)["issues"] == []
    # Отрицательные контроли: дыра тишины внутри фразы и щелчок.
    lid, _t, a, b, at, _x = E.LINES[3]
    gap = x.copy()
    mid = int((at + (b - a) / 2) * sr)
    gap[mid - int(0.4 * sr):mid + int(0.4 * sr)] = 0
    assert any("тишина" in s for s in A.voice_joins(gap, sr)["issues"])
    click = x.copy()
    click[mid] = 0.95
    click[mid + 1] = -0.95
    assert any("щелчок" in s for s in A.voice_joins(click, sr)["issues"])


def _insp(cam, target, hand=None, mug=None):
    d = np.asarray(target, float) - np.asarray(cam, float)
    out = {"camera": list(cam), "cameraDir": list(d / np.linalg.norm(d)), "characters": {"claude": {"shown": True}}, "objects": {}}
    if hand is not None:
        out["characters"]["claude"].update(hand_l=list(hand), hand_r=[9, 9, 9])
        out["objects"]["mug"] = {"min": [m - 0.02 for m in mug], "max": [m + 0.02 for m in mug]}
    return out


def test_projection_center_and_text_over_face_negative_control():
    cx, cy, cz = E.CL
    fy = cy + A.FY
    x, y, z = A.project([cx, fy, cz], [cx, fy, cz + 1.0], [0, 0, -1])
    assert (round(x, 6), round(y, 6)) == (0.5, 0.5) and z == pytest.approx(1.0)
    # Камера сверху смотрит вниз — лицо уезжает в нижнюю треть, на полосу субтитров: должна поймать.
    low = _insp([cx, fy + 0.25, cz + 0.55], [cx, fy + 0.1, cz])
    assert A.face_box(0.0, low)[3] > 0.78
    assert A.text_over_face([(1.0, "caps", low)]), "лицо под субтитрами — ошибка"
    # Крупный план серии: лицо по центру, субтитры ниже — чисто.
    cu = _insp([cx + 0.02, fy + 0.04, cz + 1.0], [cx - 0.01, fy - 0.01, cz])
    assert A.text_over_face([(1.0, "caps", cu)]) == []


def test_hand_checks_catch_jump_and_lost_mug():
    mug = np.array(E.SIP)
    good = [_insp([0, 1, 1], [0, 1, 0], hand=mug + [0, 0, 0.025 + 0.0005 * i], mug=mug) for i in range(30)]
    for i, s in enumerate(good):
        s["t"] = A.TEA + i / 30
    res = A.hand_checks(good, A.TEA)
    assert res["jumps"]["ok"] and res["hand_contact"]["ok"]
    bad = [dict(s) for s in good]
    bad[15] = _insp([0, 1, 1], [0, 1, 0], hand=mug + [0.2, 0, 0], mug=mug)
    bad[15]["t"] = good[15]["t"]
    res = A.hand_checks(bad, A.TEA)
    assert not res["jumps"]["ok"] and not res["hand_contact"]["ok"]


def test_mug_flies_to_sip_after_tea_and_user_reacts():
    keys = A.default_mug_keys()
    assert keys[1]["t"] == A.TEA and keys[2]["position"] == E.SIP
    moods = A.mochi_windows()
    assert moods[-1][0] == "happy" and moods[-1][2][0] == E.GENIUS
    assert E.SITE < E.BLUE_AT < E.GENIUS_TYPE < E.GENIUS < E.TEA
