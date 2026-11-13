"""Серия 8 «Коллаб с Кэт»: две модели каста в одном эфире (сцена аватара на двоих), сцена окна эфира (обои → живой
фон шторкой, лайтстики и бокалы — слои поверх, общая плашка), телефон с настоящей записью приложения (экран «app»,
касания) и личка (экран «dm»), реплики трёх голосов с тем же словарём произношения, концовка с партнёром."""

import json
from pathlib import Path

from app import vtubika_endcard as E
from app import vtubika_plan as P
from app import vtubika_say as SAY
from app import vtubika_vrm_stage as VS
from app.vtubika_vrm import media_urls, to_vrm_spec

ROOT = Path(__file__).resolve().parent.parent
PLAN = ROOT / "tools" / "vtubika_plans" / "collab.json"


def _words(plan):
    toks = P.tokens(P.caption_text(plan))
    return [{"word": w, "start": round(0.4 + i * 0.42, 3), "end": round(0.4 + i * 0.42 + 0.36, 3)} for i, w in enumerate(toks)]


def _spec():
    plan = P.load_plan(PLAN)
    words = _words(plan)
    total = round(words[-1]["end"] + float(plan["tail"]), 3)
    built = P.build(plan, words, total)
    return plan, built, to_vrm_spec(plan, built, words, total), total


def test_two_cast_avatars_on_one_stream():
    plan, _built, spec, _total = _spec()
    assert VS.heroine(plan)["model"] == "cast/vroid_hairsample_female.vrm"
    av = spec["avatar"]
    models = {m["id"]: m for m in av["models"]}
    # Мира на эфире — та же модель, что за столом (трекинг); Кэт — другая модель каста.
    assert models["taya_av"]["model"] == VS.CAST["taya"]["model"]
    assert models["asya_av"]["model"] == VS.CAST["asya"]["model"] != models["taya_av"]["model"]
    assert models["taya_av"]["x"] < 0 < models["asya_av"]["x"]
    shown = [s["who"] for s in av["show"]]
    assert shown[0] == ["taya_av"] and shown[-1] == ["taya_av", "asya_av"]
    assert any(c["pos"] == VS.AV_CAMS["two_bust"]["pos"] for c in av["cams"])
    assert any(w["who"] == "asya_av" and w["t1"] > w["t0"] for w in av["talk"])
    assert av["width"] == 1200 and av["height"] == 667


def test_stream_scenes_wipe_to_live_background_and_layers():
    _plan, _built, spec, _total = _spec()
    scenes = spec["stream"]["scenes"]
    assert scenes[0]["bg"]["url"].endswith("wallpaper_2019.jpg")
    live = next(s for s in scenes if s.get("wipe"))
    assert len(live["bg"]["seq"]) == 72 and live["t"] > scenes[0]["t"]
    kanpai = scenes[-1]
    glasses = [f for f in kanpai["front"] if "wine_hand" in f["seq"][0]]
    assert len(glasses) == 2 and glasses[0].get("flip") and not glasses[1].get("flip")
    # Ключи движения — в абсолютном времени (от события), не от нуля.
    assert all(k["t"] > kanpai["t"] for g in glasses for k in g["keys"])
    assert kanpai["plate"]["names"][0]["text"] == "МИРА"
    urls = media_urls(spec)
    assert all(u in spec["siteShots"] for u in urls)
    assert spec["siteDir"].replace("\\", "/").endswith("story8_collab/site")


def test_app_phone_frames_taps_and_dm():
    _plan, _built, spec, _total = _spec()
    phones = [o for o in spec["overlays"] if o["kind"] == "phone"]
    apps = [p for p in phones if p["screen"] == "app"]
    invite = apps[1]["app"]["frames"]
    assert [f["url"].rsplit("/", 1)[1] for f in invite] == ["inbox.jpg", "invite.jpg"]
    assert invite[0]["tapT"] < invite[1]["t"] and invite[0]["tap"] == [0.5, 0.19]
    assert apps[2]["app"]["frames"][-1]["url"].endswith("collab.jpg")
    dm = next(p for p in phones if p["screen"] == "dm")["dm"]
    assert [m.get("me", False) for m in dm["msgs"]] == [False, True]
    assert "Free10" in dm["msgs"][1]["text"]
    # Сайт: три страницы товаров подряд на одном телефоне (склейка без въезда).
    sites = [p for p in phones if p["screen"] == "site"]
    assert len(sites) == 3 and sites[1].get("cutIn") and sites[0].get("cutOut")


def test_until_takes_dt_end():
    plan = P.load_plan(PLAN)
    words = _words(plan)
    built = P.build(plan, words, 80.0)
    first = next(o for o in built["spec"]["overlays"] if o["kind"] == "phone")
    times = P.token_times(plan, words)
    stream_at = next(w["start"] for w in times[0] if w["word"].startswith("стримит"))
    assert abs(first["t1"] - (stream_at - 0.15)) < 1e-6


def test_tail_has_no_wide_shot_lines_after_last_sentence():
    _plan, _built, spec, total = _spec()
    assert spec["shots"][-1]["t1"] == total
    assert "wide" not in spec["shots"][-1]["name"]


def test_extra_voices_use_pronounce_map():
    plan = P.load_plan(PLAN)
    mapping = SAY.pronounce_map(plan)
    said = SAY.apply(next(it["text"] for it in plan["voices_extra"] if it["id"] == "mira4"), mapping)
    assert "Виту́бике" in said and "фри десять" in said
    assert "Колла́бике" in P.narration_sentences(plan)[1]


def test_end_card_partner_line_and_logo():
    cfg = E.settings(json.loads(PLAN.read_text("utf-8")))
    svg = E.card_svg(cfg)
    assert "Напарника нашла в Коллабике" in svg and "data:image/png;base64," in svg
    assert "Промокод Free10" in svg
    assert E.partner_block({"partner": None}) == "" and E.partner_block({}) == ""


def test_frame_count_rounds_half_up_like_renderer():
    from app.vtubika_room3d import frame_cameras
    from app.vtubika_vrm import hero_frames

    # 56.35 с × 30 = 1690.5: render.mjs (Math.round) рисует 1691 кадр; round() Питона давал 1690 — присутствие
    # героини браковало годный рендер, а последний кадр оставался без комнаты.
    spec = {"duration": 56.35, "fps": 30, "shots": [{"t0": 0, "t1": 60, "camKeys": [{"t": 0, "pos": [0, 1, 2], "target": [0, 1, 0]}],
                                                     "plate": {"room": True}}]}
    assert hero_frames(spec) == 1691
    assert len(frame_cameras(spec)) == 1691
