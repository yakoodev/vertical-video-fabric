"""Словарь произношения рассказчика (app/vtubika_say.py) и концовка бренда (app/vtubika_endcard.py): термины
меняются только в тексте TTS, субтитры остаются сценарием и встают на время сказанного; карточка без лишних
обещаний, звуки — из паков. Без TTS, Whisper и браузера."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app import vtubika as U
from app import vtubika_endcard as E
from app import vtubika_plan as P
from app import vtubika_say as SAY
from app.explainer_gen_words import heard_share, script_words

ROOT = Path(__file__).resolve().parents[1]
FOX = ROOT / "tools" / "vtubika_plans" / "fox.json"
LIVE2D = U.PRONOUNCE["Live2D"]


def _heard(*words: str, t0: float = 10.0, step: float = 0.3) -> list[dict]:
    return [{"word": w, "start": round(t0 + i * step, 3), "end": round(t0 + i * step + 0.25, 3)}
            for i, w in enumerate(words)]


# --------------------------------------------------------------------------- словарь


def test_defaults_cover_brief_terms_and_live2d_is_owner_choice():
    assert U.PRONOUNCE["Live2D"] == "лайв-два-дэ"
    assert U.PRONOUNCE["PNGtuber"] == "пи-эн-джи-тюбер"
    assert U.PRONOUNCE["VTube Studio"] == "ви-тьюб студио"


def test_apply_replaces_terms_only_as_whole_latin_words():
    m = SAY.pronounce_map({})
    assert SAY.apply("одну Live2D-модель:", m) == f"одну {LIVE2D}-модель:"
    assert SAY.apply("live2d и LIVE2D", m) == f"{LIVE2D} и {LIVE2D}"
    assert SAY.apply("PNGtuber-ы и PNGtuberом", m) == "пи-эн-джи-тюбер-ы и пи-эн-джи-тюбером"
    assert SAY.apply("в VTube  Studio", m) == "в ви-тьюб студио"
    assert SAY.apply("Live2Dx и XLive2D", m) == "Live2Dx и XLive2D"  # не внутри латинского слова


def test_plan_can_override_and_disable_entries():
    m = SAY.pronounce_map({"pronounce": {"Live2D": "лайвтуди", "PNGtuber": None, "Шрек": "шрэк"}})
    assert m["Live2D"] == "лайвтуди" and "PNGtuber" not in m and m["Шрек"] == "шрэк"
    assert "VTube Studio" in m
    assert U.PRONOUNCE["PNGtuber"] == "пи-эн-джи-тюбер"  # общий словарь не испорчен


def test_fox_tts_text_reads_live2d_but_captions_keep_spelling():
    plan = P.load_plan(FOX)
    say, caps = P.narration_text(plan), P.caption_text(plan)
    assert "Live2D" not in say and "лайв-ту-ди" not in say
    assert f"одну {LIVE2D}-модель:" in say
    assert "одну Live2D-модель:" in caps and LIVE2D not in caps
    assert "витубика точка стор" in say  # ручной «say» фразы по-прежнему работает


@pytest.mark.parametrize("heard", [
    ("лайв", "два", "дэ", "модель"),            # по слогам
    ("лайв-два-дэ", "модель"),                   # через дефис
    ("лайв-два-дэ-модель",),                     # слитно с соседним словом
    ("Лайв", "два-дэ,", "модель"),               # регистр и знаки
    ("лайв", "-2D", "-модель."),                 # как записал Whisper озвучку story1_vrm_v2 (08.11)
])
def test_whisper_variants_merge_back_to_caption_word(heard):
    words = SAY.heard_as_script(_heard("смотришь", "на", "одну", *heard, "волчонок"), SAY.pronounce_map({}))
    norm = [re.sub(r"[^a-zа-я0-9]", "", w["word"].lower()) for w in words]
    assert "live2d" in norm[3] and norm[-1] == "волчонок"
    # Время термина — всё сказанное «лайв два дэ», без дыр и без нахлёста.
    assert words[3]["start"] == 10.9
    assert all(a["end"] <= b["start"] + 1e-6 for a, b in zip(words, words[1:]))


def test_caption_live2d_aligns_to_spoken_time_through_script_words():
    caption = "Месяц ты смотришь на одну Live2D-модель: волчонок, у которого хвост машет."
    heard = _heard("Месяц", "ты", "смотришь", "на", "одну", "лайв", "два", "дэ", "модель", "волчонок,", "у",
                   "которого", "хвост", "машет.")
    merged = SAY.heard_as_script(heard, SAY.pronounce_map({}))
    # «Live2D-модель:» сценария — одно слово, услышано двумя («Live2D» «модель»): не совпадает только оно.
    assert heard_share(caption, merged) >= 0.9
    words = script_words(caption, merged)
    assert [w["word"] for w in words][5] == "Live2D-модель:"
    live = words[5]
    # «Live2D-модель:» — от начала «лайв» (5-е слово) до конца «модель» (8-е).
    assert live["start"] == heard[5]["start"] and live["end"] == heard[8]["end"]
    assert words[6]["word"] == "волчонок," and words[6]["start"] == heard[9]["start"]


def test_multiword_term_splits_into_caption_words():
    caption = "Открываешь VTube Studio и ждёшь."
    heard = _heard("Открываешь", "ви-тьюб", "студио", "и", "ждёшь.")
    words = script_words(caption, SAY.heard_as_script(heard, SAY.pronounce_map({})))
    assert [w["word"] for w in words] == ["Открываешь", "VTube", "Studio", "и", "ждёшь."]
    assert words[1]["start"] == heard[1]["start"] and words[2]["end"] == heard[2]["end"]


def test_unrelated_speech_is_untouched():
    heard = _heard("лайв", "стрим", "два", "дэ")
    assert SAY.heard_as_script(heard, SAY.pronounce_map({})) == heard


# --------------------------------------------------------------------------- концовка


def test_end_card_on_by_default_and_plan_can_turn_it_off():
    assert E.settings({})["duration"] == U.END_CARD["duration"]
    assert 2.5 <= U.END_CARD["duration"] <= 3.0
    assert E.settings({"end_card": False}) is None
    assert E.settings({"end_card": {"duration": 2.6}})["duration"] == 2.6
    assert E.settings(P.load_plan(FOX)) is not None


def test_end_card_svg_has_brand_and_only_site_claims():
    svg = E.card_svg(E.settings({}))
    assert svg.startswith("<svg") and 'width="1080" height="1920"' in svg
    assert ">vtubika<" in svg and ">vtubika.store<" in svg
    assert ">Купить VTuber-модель<" in svg and ">с оплатой в рублях<" in svg
    texts = re.findall(r"<text[^>]*>([^<]*)</text>", svg)
    assert texts == ["vtubika", "vtubika.store", "Купить VTuber-модель", "с оплатой в рублях"]
    for banned in ("₽", "%", "скидк", "бонус", "заработ", "доход", "бесплат"):
        assert banned not in " ".join(texts).lower()
    assert "#FF8A4C" in svg and "<script" not in svg
    assert "font-family:Inter" in svg  # шрифт из vendor/fonts (svg-to-webm подключает по семейству)


def test_end_card_dot_sits_above_the_wordmark_like_og():
    wm = E.wordmark_layout(196, -0.02, 540, 1010)
    assert abs(wm["x"] + wm["width"] / 2 - 540) < 0.01
    dot = wm["dot"]
    # над «k» (правая треть надписи) и выше линии выносных.
    assert wm["x"] + 0.6 * wm["width"] < dot["cx"] < wm["x"] + 0.85 * wm["width"]
    assert dot["cy"] < wm["baseline"] - 140 and 10 < dot["r"] < 30


def test_end_card_sfx_come_from_packs_and_follow_card_start():
    cfg = E.settings({})
    sfx = E.card_sfx(cfg, 60.0)
    assert sfx and all(s["t"] >= 60.0 and 0 < s["volume"] <= 0.6 for s in sfx)
    for s in sfx:
        assert re.fullmatch(r"sfx:(kenney-[a-z]+|freesound)/[\w-]+\.ogg", s["ref"]), s["ref"]
    assert cfg["music_fade"] >= cfg["duration"]


def test_append_args_converts_card_to_bt709_and_concats():
    args = E.append_args(Path("a.mp4"), Path("c.webm"), Path("o.mp4"))
    graph = args[args.index("-filter_complex") + 1]
    assert "out_color_matrix=bt709" in graph and "concat=n=2:v=1:a=0" in graph
    assert args[args.index("-c:v") + 1] == "libvpx-vp9"  # декодер webm перед вторым входом


def test_end_card_sting_is_one_setting_and_every_option_uses_curated_packs():
    assert U.END_CARD["sting"] in U.END_CARD_STINGS
    assert {"chime_whoosh", "cha_ching_soft", "riser_glass", "glass_arpeggio"} <= set(U.END_CARD_STINGS)
    for name, sounds in U.END_CARD_STINGS.items():
        assert 1 <= len(sounds) <= 5, name
        for s in sounds:
            if "path" in s:  # свои звуки бренда — только из папки бренда в репозитории
                assert re.fullmatch(r"assets/brand/vtubika/sfx/[\w-]+\.ogg", s["path"]), s["path"]
            else:
                assert re.fullmatch(r"sfx:(kenney-[a-z]+|freesound)/[\w-]+\.ogg", s["ref"]), s["ref"]
            assert -2.5 <= s["at"] <= 1.5 and 0 < s["volume"] <= 1.0, (name, s)
    picked = E.card_sfx({**E.settings({}), "sting": "riser_glass"}, 60.0)
    assert [s["ref"] for s in picked] == [s["ref"] for s in U.END_CARD_STINGS["riser_glass"]]
    assert picked[0]["t"] < 60.0  # райзер начинается ещё под концом истории
    assert E.card_sfx({**E.settings({}), "sting": "riser_glass"}, 1.0)[0]["t"] == 0.0


def test_end_card_crossfades_from_story_without_black_veil():
    cfg = E.settings({})
    assert cfg["crossfade"] > 0 and 'class="veil"' not in E.card_svg(cfg)
    assert 'class="veil"' in E.card_svg({**cfg, "crossfade": 0})
    args = E.append_args(Path("a.mp4"), Path("c.webm"), Path("o.mp4"), total=64.7, crossfade=0.3)
    graph = args[args.index("-filter_complex") + 1]
    assert "xfade=transition=fade:duration=0.300:offset=64.400" in graph and "concat" not in graph
