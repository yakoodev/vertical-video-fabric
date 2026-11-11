"""Серия 5 «Канал из 2014» (tools/vtubika_plans/channel2014.json): план собирается в спеку VRM, два телефона-покупки идут
подряд на разных наборах снимков сайта, оформление эфира меняется по времени, промокод — только в концовке этого плана
и одной фразой рассказчика. Без TTS, Whisper и браузера."""

from __future__ import annotations

import re
from pathlib import Path

from app import vtubika as U
from app import vtubika_endcard as E
from app import vtubika_plan as P
from app import vtubika_say as SAY
from app import vtubika_site as SITE
from app import vtubika_vrm_stage as VS
from app.vtubika_vrm import to_vrm_spec

ROOT = Path(__file__).resolve().parent.parent
PLAN = ROOT / "tools" / "vtubika_plans" / "channel2014.json"
PROMO = "Промокод Free10 — скидка 10%"
SHOTS = ("catalog_bg_music.png", "product_music_room.png", "catalog_popcorn.png", "product_popcorn.png")


def _plan(tmp_path: Path) -> dict:
    plan = P.load_plan(PLAN)
    for name in SHOTS:
        (tmp_path / name).write_bytes(b"\x89PNG\r\n")
    plan["site_shots"] = {**plan["site_shots"], "dir": str(tmp_path)}
    return plan


def _spec(plan: dict) -> tuple[dict, dict]:
    toks = P.tokens(P.caption_text(plan))
    words = [{"word": w, "start": round(0.3 + i * 0.4, 3), "end": round(0.3 + i * 0.4 + 0.35, 3)} for i, w in enumerate(toks)]
    total = round(words[-1]["end"] + 2.0, 3)
    built = P.build(plan, words, total)
    return built, to_vrm_spec(plan, built, words, total)


def test_plan_hook_and_text_follow_the_brief(tmp_path):
    plan = _plan(tmp_path)
    first = plan["sentences"][0]
    assert first["text"].startswith("Ты стримишь с 2014-го.") and first["shots"][0]["cam"] == "monitor"
    # Бриф: рамка вебки в огне с первой секунды, на чёрном фоне, «тот самый шрифт».
    assert {"decor": "retro", "dt": -0.25} in first["do"]
    caps = P.caption_text(plan)
    assert caps.count("pelmen_404") == 0  # завсегдатай мелькает в чате один раз, а не в тексте
    chat = [ev["chat"]["user"] for s in plan["sentences"] for ev in s["do"] if "chat" in ev]
    assert chat.count("pelmen_404") == 1
    # Цены — настоящие с сайта (проверены 11.11), других денег и бонусов в тексте нет.
    assert "тысячу шестьсот девяносто" in caps and "четыреста пятьдесят" in caps
    for banned in ("заработ", "бонус", "доход", "бесплат"):
        assert banned not in caps.lower()
    assert caps.lower().count("промокод") == 1  # рассказчик называет его один раз, в конце
    assert plan["sentences"][-1]["text"].startswith("Кстати, промокод Free10")


def test_promo_is_spoken_in_words_and_aligns_back_to_caption():
    plan = P.load_plan(PLAN)
    say = P.narration_text(plan)
    assert "Free10" not in say and "промокод фри десять" in say  # TTS читает по словарю, субтитр остаётся «Free10»
    assert "Free10" in P.caption_text(plan)
    heard = [{"word": w, "start": 1.0 + i * 0.3, "end": 1.25 + i * 0.3} for i, w in enumerate(["промокод", "фри", "10", "на"])]
    merged = SAY.heard_as_script(heard, SAY.pronounce_map(plan))
    assert [w["word"] for w in merged] == ["промокод", "Free10", "на"] and merged[1]["start"] == 1.3


def test_end_card_promo_only_in_this_plan():
    plan = P.load_plan(PLAN)
    cfg = E.settings(plan)
    assert cfg["promo"] == PROMO and cfg["duration"] >= 3.4  # плашка успевает прочитаться
    svg = E.card_svg(cfg)
    texts = re.findall(r"<text[^>]*>([^<]*)</text>", svg)
    assert texts == ["vtubika", "vtubika.store", "Купить VTuber-модель", "с оплатой в рублях", PROMO]
    assert 'data-fit="880"' in svg and "<script" not in svg
    # Остальные истории и общие настройки — прежние, без строки промокода.
    assert "promo" not in U.END_CARD
    for other in ("fox", "sticker", "trial", "tema"):
        got = E.settings(P.load_plan(ROOT / "tools" / "vtubika_plans" / f"{other}.json"))
        assert not got.get("promo")
        assert PROMO not in E.card_svg(got)


def test_story_has_zhenya_with_own_look_and_cam_avatar():
    plan = P.load_plan(PLAN)
    her = VS.heroine(plan)
    assert her is VS.HEROINES["zhenya"] and her["model"] == VS.GIRL
    hair = lambda h: h["look"]["repaint"]["Hair"]  # noqa: E731
    # Другая причёска по цвету, чем у Веры и Ники (там тёмно-каштановые и медовые).
    assert hair(her) not in (hair(VS.HEROINES["vera"]), hair(VS.HEROINES["nika"]))
    cam = VS.AVATARS["zhenya_cam"]
    assert cam["model"] == her["model"] and cam["look"]["repaint"]["Hair"] == hair(her)
    assert "bust" in VS.AV_CAMS and plan["vrm"]["avatars"] == ["zhenya_cam"]


def test_decor_keys_go_to_stream_and_screencards(tmp_path):
    built, spec = _spec(_plan(tmp_path))
    kinds = [k["kind"] for k in spec["stream"]["decor"]]
    assert kinds == ["retro", "studio", "studio_ember"]
    ts = [k["t"] for k in spec["stream"]["decor"]]
    assert ts == sorted(ts) and ts[0] <= 0.05
    cards = [o for o in spec["overlays"] if o.get("kind") == "screen"]
    assert cards and all(o["decor"] == spec["stream"]["decor"] for o in cards)
    for bad in ("fire", ""):
        plan = P.load_plan(PLAN)
        plan["sentences"][0]["do"][0]["decor"] = bad
        try:
            P.validate_plan(plan)
        except P.PlanError as exc:
            assert "оформление эфира" in str(exc)
        else:
            raise AssertionError(f"decor {bad!r} принят")


def test_two_purchases_use_their_own_site_pages_in_order(tmp_path):
    built, spec = _spec(_plan(tmp_path))
    phones = [o for o in spec["overlays"] if o["kind"] == "phone"]
    assert [o["screen"] for o in phones] == ["site", "site"]
    bg, corn = phones
    assert bg["site"]["catalog"]["url"] == "/site/catalog_bg_music.png" and bg["site"]["product"]["url"] == "/site/product_music_room.png"
    assert corn["site"]["catalog"]["url"] == "/site/catalog_popcorn.png" and corn["site"]["product"]["url"] == "/site/product_popcorn.png"
    assert bg["cutOut"] and corn["cutIn"] and bg["t1"] == corn["t0"]  # второй сменяет первый на месте
    assert [s["step"] for s in bg["site"]["steps"]] == ["product", "tap", "paid"]
    assert [s["step"] for s in corn["site"]["steps"]] == ["product", "tap", "paid", "delivered"]
    assert bg["site"]["price"] == "1 690 ₽" and corn["site"]["price"] == "450 ₽"
    assert bg["site"]["deliveredTitle"] == "Фон пришёл" and corn["site"]["fileKind"] == "VTS"
    assert bg["site"]["size"] == "" and corn["site"]["size"] == ""  # размер файла не выдумываем
    # Рендеру нужны страницы обеих покупок, без повторов, из одной папки.
    assert spec["siteShots"] == ["/site/catalog_bg_music.png", "/site/product_music_room.png", "/site/catalog_popcorn.png",
                                 "/site/product_popcorn.png"] and spec["siteDir"] == str(tmp_path)
    for o in phones:  # телефон не заходит под субтитры (y = 0.8 кадра)
        assert o["y"] + (440 + 14) * o["scale"] < 0.8 * 1920 - 40
        assert o["store"]["preview"] == "vrm"  # без снимков сайта — нейтральная плитка, чужой товар не рисуем


def test_site_set_needs_all_its_files_and_unknown_set_is_an_error(tmp_path):
    plan = _plan(tmp_path)
    (tmp_path / "product_popcorn.png").unlink()
    assert SITE.shots(plan) is None  # набора нет целиком — снимки не используются вовсе
    plan = _plan(tmp_path)
    assert set(SITE.shots(plan)["sets"]) == {"popcorn"}
    overlays = [{"kind": "phone", "screen": "shop", "siteSet": "ghost", "store": {}}]
    try:
        SITE.apply(overlays, plan)
    except ValueError as exc:
        assert "ghost" in str(exc)
    else:
        raise AssertionError("неизвестный набор снимков принят")


def test_no_hero_inserts_are_only_screens_chat_and_phones(tmp_path):
    built, spec = _spec(_plan(tmp_path))
    plates = [s["plate"] for s in spec["shots"] if (s.get("plate") or {}).get("insert")]
    assert {p["insert"]["kind"] for p in plates} <= {"monitor", "chat"}
    zoomed = [p["insert"]["zoom"] for p in plates if p["insert"].get("zoom")]
    assert zoomed and all(1.0 <= z <= 1.1 for z in zoomed)  # края окна эфира в кадре: ничего не обрезано
