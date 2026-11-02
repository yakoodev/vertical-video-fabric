"""3D-серии на своём движке (app/series3d.py, app/stories_3d.py): режиссура по озвучке без браузера и сети —
герой по карточке, слова сценария на время распознанных, кадр на фразу, камера внутри декорации и герой в кадре,
события на словах (блэкаут, рывок, табло), звуки — записи паков; сборка эпизода «Историй» с engine 3d сухим
прогоном (3D-рендер подменён)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app import series3d as S3
from app import stories as S
from app import stories_3d as S3D
from app import stories_gen as X
from app.explainer_gen import proportional_words
from app.scene3d import validate_spec
from test_stories import env  # noqa: F401 - фикстура
from test_stories import _setup

DANYA = "a low-poly young man Danya, 24, red hair, bright yellow hooded raincoat, olive backpack, dark trousers"


def _s(text, role, hud="", kind=""):
    out = {"text": text, "role": role, "shots": [{"location": "lift", "characters": ["danya"], "action": text}]}
    if hud:
        out.update({"hud": hud, "hud_kind": kind or "info"})
    return out


LIFT = {
    "title": "Если блэкаут застал вас в лифте на 22 этаже",
    "logline": "Даня застрял в лифте во время блэкаута",
    "part_title": "Кто стучал", "hook_words": "не раздвигай двери", "viewer_question": "А ты бы раздвинул двери?",
    "hashtags": ["#блэкаут", "#лифт"], "mistake": "раздвинул двери", "cliffhanger": "табло показывало 23",
    "new_characters": [{"key": "danya", "name": "Даня", "identity_card": DANYA}],
    "new_locations": [{"key": "lift", "name": "Лифт", "description": "a dark low-poly elevator cabin in a shaft"}],
    "sentences": [
        _s("Если блэкаут застал вас в лифте на 22 этаже, не жмите все кнопки подряд.", "hook", "22 ЭТАЖ"),
        _s("И не пытайтесь раздвинуть двери.", "hook"),
        _s("Выжить просто, если не совершить одну ошибку.", "hook"),
        _s("В 23:10 свет в кабине погас, и лифт дёрнулся вниз.", "problem", "23:10"),
        _s("Даня включил фонарик телефона. Заряд всего 14%.", "solution", "ЗАРЯД 14%", "resource"),
        _s("Связи нет, а кнопка вызова диспетчера молчит.", "problem", "НЕТ СВЯЗИ", "danger"),
        _s("Он сел в угол, подальше от дверей, чтобы сберечь силы.", "solution"),
        _s("Через 7 минут сверху что-то заскрежетало.", "problem", "+7 МИН"),
        _s("Кабина качнулась и опустилась ещё на полметра.", "problem", "−0,5 М", "danger"),
        _s("В 23:24 в двери снаружи трижды постучали.", "problem", "23:24"),
        _s("Голос за дверью сказал: спасатели, держитесь, ещё 5 минут.", "solution", "5 МИН", "resource"),
        _s("И тогда Даня совершил ту самую ошибку: он раздвинул двери.", "mistake"),
        _s("Была лишь одна проблема.", "finale"),
        _s("Спасатели ещё даже не выехали. А табло над дверью горело: 23 этаж.", "finale"),
        _s("В этом доме их всего 22.", "finale"),
    ],
}


def _words(script=LIFT, cps=14.5):
    text = " ".join(s["text"] for s in script["sentences"])
    return proportional_words(text, len(text) / cps)


def test_hero_look_from_card():
    look = S3.hero_look(DANYA)
    assert look["top"] == S3.COLORS["yellow"] and look["coat"] and look["hood"]
    assert look["backpack"] == S3.COLORS["olive"] and look["hair"] == S3.HAIR_COLORS["red"]
    girl = S3.hero_look("a girl Masha, 19, long blond hair, red beanie, blue jacket, black jeans")
    assert girl["female"] and girl["hairStyle"] == "long" and girl["hat"] == S3.COLORS["red"]
    assert girl["top"] == S3.COLORS["blue"] and not girl["coat"] and girl["backpack"] is False
    assert S3.hero_look("")["top"] == S3.HERO_DEFAULT["top"], "по умолчанию — жёлтый дождевик"


def test_location_by_text():
    assert S3.pick_location("Если пожар застал вас в лифте") == "elevator"
    assert S3.pick_location("Если наводнение застало вас в квартире") == "apartment"
    assert S3.pick_location("Если буря застала вас") == "apartment"


def test_script_words_replace_whisper_mistakes():
    spoken = [{"word": "Если", "start": 0.0, "end": 0.4}, {"word": "блокаут", "start": 0.4, "end": 1.0},
              {"word": "застал", "start": 1.0, "end": 1.4}, {"word": "От", "start": 2.0, "end": 2.2},
              {"word": "облона", "start": 2.2, "end": 2.8}, {"word": "дверью.", "start": 2.8, "end": 3.3}]
    out = S3.align_words("Если блэкаут застал. А табло над дверью.", spoken)
    assert [w["word"] for w in out] == ["Если", "блэкаут", "застал.", "А", "табло", "над", "дверью."]
    assert out[1]["start"] == 0.4 and out[-1]["end"] == 3.3
    assert all(b["start"] >= a["start"] for a, b in zip(out, out[1:]))
    assert 2.0 <= out[4]["start"] < 2.8


def test_direct_elevator_series():
    words = _words()
    total = words[-1]["end"] + 2.0
    res = S3.direct(LIFT, words, total=total, look=S3.hero_look(DANYA))
    assert res["location"] == "elevator"
    shots = res["shots"]
    # Кадры встык на всю серию, ~ кадр на фразу (длинные и «табло» — два), первый — общий план сверху.
    assert shots[0]["t0"] == 0 and abs(shots[-1]["t1"] - total) < 1e-6
    assert S3.check_shots(res) == [], S3.check_shots(res)
    assert len(LIFT["sentences"]) - 2 <= len(shots) <= len(LIFT["sentences"]) + 6
    assert shots[0]["camera"] == "overhead" and shots[0]["free"]
    kinds = [s["camera"] for s in shots]
    assert kinds.count("ots") >= len(shots) // 3, "чаще всего — за спиной героя"
    assert all(not (a == b and a != "ots") for a, b in zip(kinds, kinds[1:])), "соседние ракурсы разные"
    # Камера внутри шахты/кабины, герой в кадре, не мелкий; деталь не закрыта героем.
    loc = S3.elevator_location()
    for s in shots:
        assert S3.camera_ok(s["position"], loc, s["free"], 0.08), s
        if s["camera"] in ("ots", "face", "low"):
            rep = S3.frame_report(s["position"], s["target"], s["fov"], s["hero"], s["clip"])
            head = rep["proj"]["head"]
            assert head and abs(head[0]) <= 0.81 and abs(head[1]) <= 0.87, s
    # Действия по словам: кнопки — панель, фонарик — телефон, сел — угол, табло — вставка-деталь в финале.
    by_action = {s["action"] for s in shots}
    assert {"press", "phone_light", "sit", "doors", "display"} <= by_action
    assert shots[-1]["action"] == "display" and shots[-1]["camera"] == "detail"
    # События: блэкаут один — из истории (не из хука), рывки, стук, табло 22 → 23, акцент финала.
    ev = res["events"]
    blackout = [e for e in ev if e["kind"] == "blackout"]
    assert len(blackout) == 1 and blackout[0]["role"] == "problem"
    floors = [e["value"] for e in ev if e["kind"] == "floor"]
    assert floors[0] == "22" and floors[-1] == "23"
    assert {"jolt", "knock", "sting"} <= {e["kind"] for e in ev}
    # Сцена движка: проходит проверку, свет гаснет на слове, табло в конце — 23, телефон с лучом в руке.
    spec = res["spec"]
    validate_spec(spec, check_assets=False)
    tb = blackout[0]["t"]
    main = next(x for x in spec["lights"] if x["id"] == "main")
    assert main["keys"][-1]["intensity"] == 0 and abs(main["keys"][-1]["t"] - tb - 0.04) < 1e-3
    assert next(x for x in spec["lights"] if x["id"] == "emergency")["keys"][-1]["intensity"] > 0
    disp = next(o for o in spec["objects"] if o["id"] == "display")
    texts = [k["text"] for k in disp["state"] if "text" in k]
    assert texts[0] == "22" and texts[-1] == "23"
    phone = next(o for o in spec["objects"] if o["id"] == "phone")
    assert phone["attach"][0]["to"] == "hero:hand_r" and phone["light"]["type"] == "spot"
    assert all(a["start"] >= tb - 1.5 for a in phone["attach"]), "фонарик — после блэкаута"
    hero = spec["characters"][0]
    assert hero["style"] == "toy" and hero["toy"]["top"] == S3.COLORS["yellow"]
    assert spec["camera"]["clamp"] is False and spec["camera"]["shake"]
    # Звуки — только записи паков, на нужных словах.
    assert res["sfx"] and all(x["ref"].startswith(("sfx:freesound/", "sfx:kenney-impact/")) for x in res["sfx"])
    assert any(x["kind"] == "blackout" and abs(x["t"] - tb) < 0.2 for x in res["sfx"])


def test_direct_apartment_and_errors():
    script = {"title": "Если наводнение застало вас в квартире", "sentences": [
        {"text": "Если вода пришла в квартиру, не бегите к двери.", "role": "hook"},
        {"text": "Даня включил фонарик и посмотрел в окно.", "role": "solution"},
        {"text": "Была лишь одна проблема.", "role": "finale"}]}
    res = S3.direct(script, _words(script), look=S3.hero_look(""))
    assert res["location"] == "apartment" and S3.check_shots(res) == []
    validate_spec(res["spec"], check_assets=False)
    with pytest.raises(S3.DirectionError):
        S3.direct({"sentences": []}, _words())
    with pytest.raises(S3.DirectionError):
        S3.direct(LIFT, [])


def _fake_render(result: dict, out_path: Path) -> dict:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"color=c=#203040:s=108x192:d={result['spec']['duration']}:r=10", "-pix_fmt", "yuv420p",
                    str(out_path)], check=True)
    return {"ok": True, "frames": 10, "seconds": 0.1}


def test_story_episode_with_3d_engine(env, monkeypatch):  # noqa: F811
    _client, store, tmp = env
    monkeypatch.setattr(S3D, "render3d", _fake_render)
    ids = _setup(store, tmp, music=False, sounds=False)
    S.save_world(store, world_id=ids["world_id"], format=S.FORMAT_DISASTER)
    with pytest.raises(ValueError):
        S.create_episode(store, ids["world_id"], "лифт", options={"engine": "unreal"})
    ep = X.create(store, ids["world_id"], "блэкаут в лифте", None, script=LIFT, options={"engine": "3d"}, run=True)
    assert ep["status"] == "done", (ep["error"], ep.get("notes"))
    # Ни сцен, ни листов, ни плит: картинок у 3D-серии нет — только бесплатная озвучка.
    assert S.list_scenes(store, ids["world_id"]) == []
    assert all(b.get("scene_id") is None for b in ep["beats"])
    tl = ep["timeline"]
    assert tl["engine"] == "3d" and tl["scene3d"]["location"] == "elevator" and tl["scene3d"]["checks"] == []
    layers = store.get_clip_plan(ep["clip_id"])["render_settings"]["layers"]
    video = [lay for lay in layers if lay["type"] == "video"]
    assert len(video) == 1 and video[0]["start"] == 0 and abs(video[0]["end"] - tl["duration"]) < 1e-3
    caps = next(lay for lay in layers if lay["type"] == "captions")
    assert caps["props"]["maxWords"] == 3 and caps["props"]["activeColor"] != caps["props"]["color"]
    hud = [lay for lay in layers if str(lay["id"]).startswith("st_hud")]
    assert len(hud) >= 6 and all(abs(h["y"] - caps["y"]) >= 0.12 for h in hud), "HUD не на субтитрах"
    assert any(str(lay["id"]) == "st_part" for lay in layers) and any(str(lay["id"]) == "st_title" for lay in layers)
    sfx = [lay for lay in layers if lay["type"] == "audio" and lay["props"].get("role") == "sfx"]
    assert sfx, "звуки событий режиссуры"
    assert not store.get_clip_plan(ep["clip_id"])["render_settings"].get("music_on")
    # Субтитры — слова сценария.
    words = store.get_clip_plan_subtitles(ep["clip_id"])["words"]
    assert any("блэкаут" in str(w.get("word") or w.get("text")) for w in words)
    assert tl["cover_frames"] and all(Path(p).is_file() for p in tl["cover_frames"])
