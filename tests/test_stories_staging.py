"""Постановка фраз 3D-серии переживает сохранение сценария (app/stories_staging.py).

22–23.11 части 5 и 6 «Лифта» сохранили через API, и у фраз остались только text/shots/role: бег, двойник, экран
переписки, место, свет, высота пола и фонарик пропали, «Запустить» не повторял ролик (эпизоды 10 и 11 проекта 8)."""

from __future__ import annotations

from app import series3d_chase as CH
from app import stories as S
from app import stories_staging as STG
from test_stories import _setup, env  # noqa: F401 - env — фикстура
from test_story_draft import DISASTER, SERIES, SHORT, _draft

RUN = {"path": [[0.0, -0.4], [0.1, -3.2], [0.6, -6.0]], "at": 0.3}
DOUBLE = {"do": "run", "path": [[0.0, -0.2], [0.1, -3.0]], "at": 0.5, "z": -1.2}
ECHO = {"do": "echo", "lag": 0.3}
CHAT = {"to": "Мама", "status": "в сети", "items": [{"in": "ты где?"}, {"out": "в лифте"}, {"photo": True, "note": "кабина"}]}
CAM = {"pos": [0.35, -10.2, 1.25], "to": [0.3, -9.95, 1.22], "point": "face", "lens": 45, "fstop": 5.6, "face_w": 0.25,
       "face_y": 0.4, "offset": [0.0, 0.0, 0.1], "probe": [0.0, -9.0, 1.0], "kind": "face"}
STAGED = [
    {**SHORT["sentences"][0], "run": RUN, "location": "floor22", "fill": 40, "cam": CAM},
    {**SHORT["sentences"][1], "double": DOUBLE, "screen": CHAT, "look": "door", "look_at": 1.2},
    {**SHORT["sentences"][2], "location": "stairwell", "z": -3.3, "flicker": {"at": 0.5, "dark": 2.0}, "flash": True,
     "doubles": [ECHO, {"do": "stand", "pos": [0.2, -1.0], "heading": 180}], "screen": "8%"},
]
OUTRO = [{"text": "Шаги, снова.", "sec": 3.0, "plan": "лестница, общий", "cam": CAM, "location": "stairwell",
          "z": -6.6, "double": ECHO, "run": RUN}]


def _staged(sentence: dict) -> dict:
    return {k: v for k, v in sentence.items() if k not in ("text", "shots", "role", "hud", "hud_kind")}


def test_every_authored_field_has_a_cleaner():
    assert set(STG.CLEANERS) == set(CH.AUTHORED)


def test_staging_survives_api_save_and_draft_edit(env):  # noqa: F811
    client, store, tmp = env
    ids = _setup(store, tmp, music=False, sounds=False)
    ep = _draft(store, ids, options=DISASTER, sentences=STAGED, outro=OUTRO, **SERIES)
    got = S.get_episode(store, ep["id"])["script"]
    staged = [_staged(s) for s in got["sentences"]]
    assert staged[0] == {"run": RUN, "location": "floor22", "fill": 40.0, "cam": {**CAM, "lens": 45.0}}
    assert staged[1] == {"double": DOUBLE, "screen": CHAT, "look": "door", "look_at": 1.2}
    assert staged[2] == {"location": "stairwell", "z": -3.3, "flicker": {"at": 0.5, "dark": 2.0}, "flash": True,
                         "doubles": [ECHO, {"do": "stand", "pos": [0.2, -1.0], "heading": 180.0}], "screen": "8%"}
    outro = got["outro"][0]
    assert outro["location"] == "stairwell" and outro["z"] == -6.6 and outro["double"] == ECHO and outro["run"] == RUN
    assert outro["cam"]["offset"] == [0.0, 0.0, 0.1] and outro["cam"]["face_y"] == 0.4

    # Сохранение через API тем же сценарием — то же самое (идемпотентно), и правка текста черновика не теряет постановку.
    r = client.put(f"/api/stories/episodes/{ep['id']}/script", json=got)
    assert r.status_code == 200, r.text
    again = S.get_episode(store, ep["id"])["script"]
    assert again["sentences"] == got["sentences"] and again["outro"] == got["outro"]
    text = "\n".join(s["text"] for s in got["sentences"])
    r = client.patch(f"/api/stories/episodes/{ep['id']}/draft", json={"title": "Лифт", "text": text})
    assert r.status_code == 200, r.text
    edited = S.get_episode(store, ep["id"])["script"]
    assert [_staged(s) for s in edited["sentences"]] == staged and edited["outro"] == got["outro"]


def test_broken_fields_are_dropped_with_a_warning(env):  # noqa: F811
    _client, store, tmp = env
    ids = _setup(store, tmp, music=False, sounds=False)
    bad = [{**SHORT["sentences"][0], "run": {"path": "вперёд"}, "double": {"do": "dance"}, "z": "низ",
            "fill": float("inf"), "cam": {"to": [1, 2, 3]}, "location": "floor22", "runs": [RUN, {"at": 1}]},
           *SHORT["sentences"][1:]]
    ep = _draft(store, ids, options=DISASTER, sentences=bad, **SERIES)
    script = S.get_episode(store, ep["id"])["script"]
    assert _staged(script["sentences"][0]) == {"location": "floor22"}
    warned = " ".join(script["warnings"])
    for key in ("run", "double", "z", "fill", "cam", "runs"):
        assert f"поле {key} " in warned or f"список {key} " in warned, key


def test_saved_staging_reaches_the_blender_job_plans():
    """То, что сохранено, сцена читает: apply_authored кладёт поля фраз в планы."""
    sentences = [{"text": "Он бежит.", **STG.staging({"run": RUN, "location": "floor22"})},
                 {"text": "Сзади шаги.", **STG.staging({"double": ECHO, "z": -3.3})}]
    shots = [{"sentences": [0], "part": 0}, {"sentences": [1], "part": 0}]
    CH.apply_authored(shots, sentences)
    assert shots[0]["run"] == RUN and shots[0]["location"] == "floor22"
    assert shots[1]["double"] == ECHO and shots[1]["z"] == -3.3
