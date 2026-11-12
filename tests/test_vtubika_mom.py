"""Серия 7 «Лёша, ужинать»: первый герой-парень (мужская база VRoid с 52 формами ARKit), крючок одним огромным словом,
реплики мамы другим голосом в паузах, живой товар кадрами видео-превью со страницы магазина, клавиатура и мышь из
библиотеки вещей (только CC0), рот по Wav2ARKit только в окнах речи героя."""

import sys
from pathlib import Path

from app import lipsync
from app import vtubika_plan as P
from app import vtubika_vrm_stage as VS
from app.vtubika_vrm import expand_seq, to_vrm_spec

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
PLAN = ROOT / "tools" / "vtubika_plans" / "mom.json"


def _words(plan):
    toks = P.tokens(P.caption_text(plan))
    return [{"word": w, "start": round(0.4 + i * 0.42, 3), "end": round(0.4 + i * 0.42 + 0.36, 3)} for i, w in enumerate(toks)]


def test_hero_is_male_perfect_sync_model():
    plan = P.load_plan(PLAN)
    her = VS.heroine(plan)
    assert her["model"].startswith("hinzka/") and "Male" in her["model"]
    assert plan["vrm"]["arkit"] is True
    # Вещи библиотеки — только явные id CC0 (CC BY в роликах не берём: атрибуцию в описаниях не пишем).
    assert set(plan["vrm"]["props_models"].values()) == {"keyboard/mech_tkl_dark", "mouse/black"}


def test_hook_first_frame_one_big_word():
    import vtubika_story as V

    plan = P.load_plan(PLAN)
    (layer,) = V.hook_layers(plan)
    assert layer["start"] == 0 and layer["in"]["type"] == "none"
    assert " " not in layer["props"]["text"] and layer["props"]["text"].isupper()
    assert layer["props"]["size"] >= 120
    first = plan["sentences"][0]["text"].lower()
    assert layer["props"]["text"].lower().rstrip("?") in first


def test_talk_faces_and_product_sequence():
    plan = P.load_plan(PLAN)
    words = _words(plan)
    total = round(words[-1]["end"] + 1.0, 3)
    built = P.build(plan, words, total)
    spec = to_vrm_spec(plan, built, words, total)
    # «всем привет» и «это я, мам» — герой говорит (рот только в этих окнах).
    assert len(spec["timeline"]["speech"]) >= 4
    prod = spec["stream"]["product"]
    for f in prod["faces"].values():
        assert len(f["seq"]) == 36 and all(u in spec["siteShots"] for u in f["seq"])
    assert spec["shots"][0]["props"]["keyboard"]["model"] == "keyboard/mech_tkl_dark"
    # Руки на клавишах ближе к себе (keys_pull): иначе левая рука выпрямлена до конца и кисть крутится штопором.
    assert spec["timeline"]["keysPull"] == 0.03 and spec["modelScale"] == 0.885


def test_first_vtubika_line_revoiced_only():
    plan = P.load_plan(PLAN)
    flagged = [s["text"] for s in plan["sentences"] if s.get("revoice")]
    assert flagged == ["Однажды ночью ты открываешь Витубику."]
    assert "Виту́бику" in P.narration_sentences(plan)[4]


def test_expand_seq_pattern_and_list():
    assert expand_seq({"pattern": "/site/a/f_{:03d}.jpg", "from": 1, "count": 3}) == [
        "/site/a/f_001.jpg", "/site/a/f_002.jpg", "/site/a/f_003.jpg"]
    assert expand_seq(["/site/x.jpg"]) == ["/site/x.jpg"]
    assert expand_seq(None) is None


def test_mask_arkit_only_inside_speech_windows():
    ak = {"fps": 10, "start": 1.0, "names": ["jawOpen"], "frames": [[0.5]] * 40}
    out = lipsync.mask_arkit(ak, [(2.0, 2.5)], fade=0.1)
    vals = [r[0] for r in out["frames"]]
    assert vals[0] == 0 and vals[-1] == 0  # 1.0 с и 4.9 с — вне окна
    assert vals[12] == 0.5  # 2.2 с — внутри
    assert out["names"] == ["jawOpen"] and len(vals) == 40


def _spec():
    plan = P.load_plan(PLAN)
    words = _words(plan)
    total = round(words[-1]["end"] + 1.0, 3)
    built = P.build(plan, words, total)
    return plan, built, to_vrm_spec(plan, built, words, total)


def test_stream_is_a_real_monitor_on_the_desk_not_a_floating_card():
    from app.vtubika_vrm import MONITOR, hero_frames

    plan, built, spec = _spec()
    assert not any(o.get("kind") == "screen" for o in spec["overlays"])  # «карточки эфира» в воздухе нет
    desk = [s for s in spec["shots"] if s.get("deskMonitor")]
    kinds = {s["name"].split("_", 1)[1] for s in desk}
    assert {"monitor_desk", "chat_desk", "ots_desk"} <= kinds
    assert not any((s.get("plate") or {}).get("insert") for s in spec["shots"])  # плоских вставок эфира тоже нет
    for s in desk:
        assert s["props"]["monitor"]["model"] == MONITOR["model"] == "monitor/flat_black"  # CC0
        assert s["camKeys"] and s["focus"]
        assert s.get("hideHero", False) == (not s["name"].endswith("ots_desk"))
    # Кадры «её глазами» — без героини: проверка присутствия их не ждёт.
    n = round(spec["duration"] * 30)
    hidden = sum(1 for i in range(n) for s in desk if s.get("hideHero") and s["t0"] <= i / 30 < s["t1"])
    assert hero_frames(spec) == n - hidden


def test_cover_is_tight_panicked_face_with_hook_word():
    plan, built, spec = _spec()
    first = spec["shots"][0]
    assert first["name"].endswith("face_close") and first["camKeys"][0]["fov"] <= 7.0
    assert 1.3 <= first["t1"] <= 2.0  # дальше — монитор с «камера выключена»
    assert spec["shots"][1]["name"].endswith("monitor_desk")
    face0 = spec["timeline"]["faces"][0]
    assert face0["t"] < 0 and face0["expr"]["eyeSurprised"] == 1.0  # паника целиком уже на кадре 0
    assert plan["hook"]["until"] <= first["t1"] and plan["hook"]["y"] < 0.25


def test_meme_spam_in_chat():
    plan, built, spec = _spec()
    texts = [m["text"] for m in spec["stream"]["chat"]]
    memes = [t for t in texts if t in ("газ газ газ", "имба", "💀💀💀", "67", "база")]
    assert 3 <= len(memes) <= 5


def test_no_desk_monitor_without_3d_room_or_when_off():
    from app.vtubika_vrm import desk_monitor

    plan = P.load_plan(PLAN)
    assert desk_monitor(plan)["model"] == "monitor/flat_black"
    assert desk_monitor({**plan, "vrm": {**plan["vrm"], "monitor": False}}) is None
    vrm = {k: v for k, v in plan["vrm"].items() if k != "monitor"}
    no_room = {**{k: v for k, v in plan.items() if k != "presets3d"}, "vrm": vrm}
    assert desk_monitor(no_room) is None  # без 3D-комнаты — прежние вставки, пока план не попросит монитор
    assert desk_monitor({**no_room, "vrm": {**vrm, "monitor": {}}})["model"] == "monitor/flat_black"


def test_easter_eggs_are_own_assets_on_the_desk_monitor():
    plan, built, spec = _spec()
    eggs = next(s for s in spec["shots"] if s.get("deskMonitor"))["props"]["monitor"]["eggs"]
    # Только свои: сова-объяснялка, героиня прошлой серии, зверёк мем-канала (без чужих картинок).
    assert eggs == {"sticker": "eggs/owl_sticker.png", "photo": "eggs/sonya_photo.jpg", "critter": True}
