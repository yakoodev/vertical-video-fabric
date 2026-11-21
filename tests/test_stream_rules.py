"""Правила стрим-шортсов и подборок (findings.md «каналы нарезок стримов»): у каждого — замер и негативный контроль."""

from __future__ import annotations

import numpy as np
import pytest

from app import compilation_rules as cr
from app import meta_formulas as mf
from app import pace_presets as pp
from app import sfx_rules as sx
from app import short_assembly as sa
from app import split_rules as sp
from app import stream_captions as sc
from app import zoom_breathing as zb


def _words(t0: float, t1: float, step: float = 0.35) -> list[tuple[float, float]]:
    return [(round(t0 + i * step, 3), round(t0 + i * step + step * 0.8, 3)) for i in range(int((t1 - t0) / step))]


# ---------------------------------------------------------------- 3. заявка → опровержение → хвост


def _claim_window():
    phrases = [{"start": 2.0, "end": 5.5, "text": "я бы никогда в жизни не заплатил"},
               {"start": 6.0, "end": 25.0, "text": "ну давай посмотрим что там дальше"},
               {"start": 27.5, "end": 29.0, "text": "держать нажми держать"}]
    spans = [(29.2, 40.0, "смех?")]
    words = _words(2.0, 5.5) + _words(6.0, 25.0) + _words(27.5, 29.0)
    return phrases, spans, words


def test_short_assembly_claim_payoff_tail():
    phrases, spans, words = _claim_window()
    res = sa.assemble_short(phrases, spans, 0.0, 50.0)
    assert res["claim"]["text"].startswith("я бы никогда")
    assert res["pieces"][0] == (2.0, 5.5) and res["pieces"][1][0] == pytest.approx(27.7)
    check = sa.check_short(res["pieces"], res["claim"], res["reaction_t"], words)
    assert check["ok"], check
    assert check["claim_sec"] <= 5 and 8 <= check["tail_sec"] <= 15 and check["claim_to_payoff_sec"] <= 5
    assert res["out_sec"] < res["source_sec"] / 2  # ужато, как 49 с → 21 с


def test_short_assembly_negative_controls():
    phrases, spans, words = _claim_window()
    assert sa.assemble_short([{"start": 2, "end": 5, "text": "обычная фраза"}], spans, 0, 50) is None  # нет заявки
    assert sa.assemble_short(phrases, [], 0, 50) is None  # нет реакции
    claim = {"start": 2.0, "end": 5.5}
    # середина не вырезана: от заявки до реакции 24 с
    bad = sa.check_short([(2.0, 45.0)], claim, 29.2, words)
    assert not bad["ok"] and any("середина" in p for p in bad["problems"])
    # хвост 3 с
    short_tail = sa.check_short([(2.0, 5.5), (27.7, 32.2)], claim, 29.2, words)
    assert any("хвост" in p for p in short_tail["problems"])
    # заявка 9 с
    long_claim = sa.check_short([(0.0, 9.0), (27.7, 40.0)], {"start": 0.0, "end": 9.0}, 29.2, words)
    assert any("заявка" in p for p in long_claim["problems"])
    # пауза 2 с вне хвоста
    gappy = sa.check_short([(2.0, 5.5), (27.7, 40.0)], claim, 29.2, [(2.0, 2.5), (4.6, 5.0)])
    assert any("пауза" in p for p in gappy["problems"])


# ---------------------------------------------------------------- 4. split


SPLIT = {"mode": "split", "cam_pos": "top", "ratio": 0.38, "cam": {"x": 0.7, "y": 0.0, "w": 0.3, "h": 0.3}}


def test_split_layout_cam_top_fixed_and_negative():
    assert sp.check_split_layout(SPLIT) == []
    assert any("не сверху" in p for p in sp.check_split_layout({**SPLIT, "cam_pos": "bottom"}))
    assert sp.check_split_layout({"mode": "single"}) == ["раскладка не split"]


def test_hold_targets_hysteresis_and_negative():
    jitter = [{"t": i * 0.5, "x": 0.3 if i % 2 else 0.7} for i in range(12)]  # говорящие каждые 0,5 с
    before = sp.check_target_switches(jitter)
    assert not before["ok"] and before["min_gap_sec"] == 0.5     # негативный контроль: метание поймано
    held = sp.hold_targets(jitter)
    after = sp.check_target_switches(held)
    assert after["ok"] and after["min_gap_sec"] >= 1.5 and after["per_min"] < before["per_min"]
    # склейка плана меняет цель сразу; плавное ведение не трогается
    cut = [{"t": 0, "x": 0.3}, {"t": 0.5, "x": 0.8, "cut": True}]
    assert sp.hold_targets(cut)[1]["x"] == 0.8
    glide = [{"t": i * 0.2, "x": 0.3 + i * 0.02} for i in range(10)]
    assert [p["x"] for p in sp.hold_targets(glide)] == [p["x"] for p in glide]


def test_no_black_bars_in_game_zone_and_negative():
    frame = np.full((1920, 1080), 120, np.uint8)
    ok = sp.check_no_black_bars(frame, 730, 1920)
    assert ok["ok"]
    barred = frame.copy()
    barred[730:900] = 4  # letterbox сверху зоны игры
    bad = sp.check_no_black_bars(barred, 730, 1920)
    assert not bad["ok"] and bad["top_rows"] == 170
    dark_scene = frame.copy()
    dark_scene[730:900, ::10] = 200  # тёмная сцена, но с деталями — не полоса
    assert sp.check_no_black_bars(dark_scene, 730, 1920)["ok"]


def test_face_height_spread_and_negative():
    steady = [(0.4, 0.10 + 0.002 * (i % 3), 0.2, 0.12) for i in range(30)]
    assert sp.check_face_height(steady)["ok"]
    wobble = [(0.4, 0.10 + 0.03 * (i % 3), 0.2, 0.12) for i in range(30)]
    res = sp.check_face_height(wobble)
    assert not res["ok"] and res["spread"] == pytest.approx(0.06)
    assert not sp.check_face_height([])["ok"]


def test_captions_on_seam_not_over_face_and_negative():
    seam = sp.seam_y(SPLIT)
    assert seam == pytest.approx(0.38, abs=0.01)
    from app.auto_edit import caption_y_for

    y = caption_y_for({"layout": SPLIT})
    assert y == pytest.approx(seam, abs=0.002)
    face = (0.4, 0.08, 0.2, 0.18)  # лицо в полосе вебки, выше шва
    assert sp.check_caption_box(sp.caption_box(y), seam, [face])["ok"]
    low_face = (0.4, 0.30, 0.2, 0.1)  # лицо у самого шва — подпись его задевает
    res = sp.check_caption_box(sp.caption_box(y), seam, [low_face])
    assert not res["ok"] and res["face_overlap"] > 0
    assert not sp.check_caption_box(sp.caption_box(0.72), seam)["ok"]  # обычные 0.72 — не на шве


# ---------------------------------------------------------------- 5. подписи стрим-шортса


def test_stream_caption_layer_passes_and_negative_controls():
    layer = sc.stream_caption_layer(20.0, 0.38)
    words = sc.mark_emphasis([{"word": "ну", "start": 0, "end": 0.3}, {"word": "ахахаха", "start": 0.4, "end": 0.9},
                              {"word": "дальше", "start": 1.0, "end": 1.4}],
                             loud_db=lambda a, b: 11.0 if a >= 1.0 else 0.0)
    assert [w.get("emph", 0) for w in words] == [0, 1, 2]
    assert sc.check_stream_captions(layer, words, 20.0)["ok"]
    from app.motion import normalize_layers

    clean = normalize_layers([layer])[0]
    assert clean["props"]["pageFade"] == sc.PAGE_FADE and clean["keys"]["scale"][-1]["v"] == 1.05
    broken = {**layer, "props": {**layer["props"], "uppercase": False, "weight": 400, "color": "#ffffff",
                                 "pageFade": 0}, "shadow": None,
              "keys": {"scale": [{"t": 0, "v": 1.0}, {"t": 20, "v": 1.2}]}}
    problems = sc.check_stream_captions(broken, [{"word": "ахаха", "start": 0, "end": 1}], 20.0)["problems"]
    for needle in ("ЗАГЛАВН", "жирн", "тени", "появления", "жёлт", "рост", "эмоции"):
        assert any(needle in p for p in problems), needle


def test_loudness_lookup_over_speech_median():
    feat = {"lufs": np.array([-30.0] * 20 + [-15.0] * 5 + [-30.0] * 5), "hop": 0.1}
    over = sc.loudness_lookup(feat, [(0.0, 3.0)])
    assert over(2.0, 2.5) == pytest.approx(15.0) and over(0.5, 1.0) == pytest.approx(0.0)


# ---------------------------------------------------------------- 6. звук по меткам


PACK = {"whoosh-1.ogg", "drama-boom.ogg", "ding.ogg", "record-scratch.ogg"}


def test_sfx_by_labels_and_density():
    cuts = [5.0, 10.0]
    spans = [(3.0, 4.5, "крик"), (12.0, 12.8, "смех?")]
    words = [(0.0, 1.0), (1.2, 2.0), (6.0, 7.0), (9.0, 9.5)]  # 2–6 с: пауза, но там крик; 7–9 — неловкая
    res = sx.place_sfx(cuts, spans, words, 20.0, available=PACK)
    kinds = sorted(e["kind"] for e in res["kept"])
    assert kinds == ["awkward", "ding", "shout", "transition", "transition"]
    refs = {layer["props"]["asset_id"] for layer in res["layers"] if layer["type"] == "audio"}
    assert "sfx:freesound/record-scratch.ogg" in refs and "sfx:freesound/drama-boom.ogg" in refs
    assert any(layer["type"] == "fx" and layer["props"]["kind"] == "flash_frame" for layer in res["layers"])
    check = sx.check_sfx(res["layers"], cuts, 20.0, res["dropped"], available=PACK)
    assert check["ok"], check


def test_sfx_negative_controls():
    # 20 склеек за 20 с → плотность режет, и это видно в dropped (а не «склейка без звука»)
    cuts = [float(i) for i in range(1, 20)]
    res = sx.place_sfx(cuts, [], [], 20.0, available=PACK)
    assert len(res["kept"]) <= sx.MAX_PER_MIN and res["dropped"]
    assert sx.check_sfx(res["layers"], cuts, 20.0, res["dropped"], available=PACK)["ok"]
    # склейка без звука и без причины
    lonely = sx.check_sfx([], [5.0], 20.0, available=PACK)
    assert not lonely["ok"] and lonely["uncovered_cuts"] == 1
    # звук не из пака
    foreign = [{"id": "x", "type": "audio", "start": 4.95, "end": 5.5, "props": {"asset_id": "sfx:generated/whoosh.ogg"}}]
    assert any("не из пака" in p for p in sx.check_sfx(foreign, [5.0], 20.0, available=PACK)["problems"])
    # слишком плотно (подложено руками)
    dense = [{"id": f"s{i}", "type": "audio", "start": float(i), "end": i + 0.5,
              "props": {"asset_id": "sfx:freesound/ding.ogg"}} for i in range(10)]
    assert any("за минуту" in p for p in sx.check_sfx(dense, [], 20.0, available=PACK)["problems"])
    # записи нет в паке — синтетику не подставляем, звук пропускается
    assert sx.sfx_layers([{"t": 1.0, "kind": "awkward"}], {"whoosh-1.ogg"}, 10.0) == []


# ---------------------------------------------------------------- 7. зум «дыхание»


def test_zoom_breathing_keys_and_check():
    keys = zb.breathing_keys([4.0, 9.0], 15.0)
    check = zb.check_breathing(keys, [4.0, 9.0], 15.0)
    assert check["ok"], check
    assert check["range"][1] <= 1.2 and check["final"] == 1.15
    assert zb.zoom_at(keys, 3.99) > 1.19 and zb.zoom_at(keys, 4.0005) < 1.01
    from app import motion

    assert motion.validate_camera(keys, 15.0) == []


def test_zoom_breathing_negative_controls():
    cuts = [4.0, 9.0]
    no_reset = [{"t": 0, "zoom": 1.0, "ease": "outCubic"}, {"t": 15.0, "zoom": 1.15}]
    assert any("на склейке" in p for p in zb.check_breathing(no_reset, cuts, 15.0)["problems"])
    over = zb.breathing_keys(cuts, 15.0, peak=1.4)
    assert any("вне" in p for p in zb.check_breathing(over, cuts, 15.0)["problems"])
    jerky = zb.breathing_keys(cuts, 15.0) + [{"t": 2.0, "zoom": 1.2, "ease": "linear"},
                                              {"t": 2.05, "zoom": 1.0, "ease": "linear"}]  # откат внутри куска
    jerky.sort(key=lambda k: k["t"])
    assert zb.check_breathing(jerky, cuts, 15.0)["jerks"] > 0
    wrong_end = zb.breathing_keys(cuts, 15.0, final=1.2)
    assert any("в конце" in p for p in zb.check_breathing(wrong_end, cuts, 15.0)["problems"])


# ---------------------------------------------------------------- 8. компиляция


def test_compilation_order_strength_bookend_and_check():
    items = [{"t": "Погоня за машиной", "s": 0.5}, {"t": "Игуана в кустах", "s": 0.9}, {"t": "Самокат", "s": 0.4},
             {"t": "Снова игуана", "s": 0.6}, {"t": "Крокодил", "s": 0.8}]
    order = cr.order_by_strength(items, lambda x: x["s"])
    assert order[0]["t"] == "Игуана в кустах" and order[-1]["t"] == "Крокодил"
    check = cr.check_compilation([25.0] * 5, [x["s"] for x in order])
    assert check["ok"] and check["per_min"] == pytest.approx(2.4)
    book = cr.bookend_theme(items, lambda x: x["s"], lambda x: x["t"])
    assert book[0]["t"] == "Игуана в кустах" and book[-1]["t"] == "Снова игуана"
    assert cr.check_compilation([25.0] * 5, [x["s"] for x in book], [x["t"] for x in book], bookend=True)["ok"]


def test_compilation_negative_controls():
    chrono = [0.5, 0.9, 0.4, 0.8, 0.6]
    problems = cr.check_compilation([25.0] * 5, chrono)["problems"]
    assert any("первым" in p for p in problems) and any("последним" in p for p in problems)
    assert any("вне 20–30" in p for p in cr.check_compilation([8.0, 25.0], [0.9, 0.1])["problems"])
    outro = cr.check_compilation([25.0, 25.0], [0.9, 0.5], total=50.0, last_word_end=46.0)
    assert any("аутро" in p for p in outro["problems"])
    themes = cr.check_compilation([25.0] * 3, [0.9, 0.1, 0.5], ["игуана", "самокат", "крокодил"], bookend=True)
    assert any("разных тем" in p for p in themes["problems"])
    assert cr.outro_cut(50.0, 46.0) == {"start": 47.0, "end": 50.0}
    assert cr.outro_cut(50.0, 49.5) is None


def test_gag_repeats_found_and_negative():
    cues = [{"start": t, "text": "you here for the party?"} for t in (10, 200, 450, 900)]
    cues += [{"start": 205, "text": "the party again"}, {"start": 300, "text": "something else entirely"}]
    found = cr.find_gag_repeats(cues, "party")
    assert found[0]["count"] == 4  # 200 и 205 — один эпизод
    spawn = [{"start": t, "text": "кай появился из ниоткуда"} for t in (40, 400, 800)]
    auto = cr.find_gag_repeats(cues + spawn)
    assert auto and auto[0]["phrase"] == "появи ниотк" and auto[0]["count"] == 3
    assert cr.find_gag_repeats(cues, "крокодил") == []
    assert cr.find_gag_repeats([{"start": 1, "text": "party"}, {"start": 5, "text": "party"}], "party") == []


# ---------------------------------------------------------------- 9. метаданные


def test_titles_by_formula_and_checks():
    t = mf.reaction_title("Эвелон", "Топ Моменты с Twitch | Технические Неполадки")
    assert t.startswith("ЭВЕЛОН СМОТРИТ: ") and mf.check_title(t, "Топ моменты с twitch технические неполадки", "эвелон") == []
    assert mf.check_title("ЭВЕЛОН СМОТРИТ: что-то своё", "Топ Моменты с Twitch") == ["название оригинала не совпадает дословно"]
    assert mf.reaction_compilation_title("эвелон", "Технические неполадки", "Evelone Rofls") == \
        "ЭВЕЛОН СМОТРИТ: Технические неполадки / EVELONE ROFLS"
    assert mf.game_fail_title("Эвелон", "слил все деньги", "", "стрип-клубе", "GTA 6") == "ЭВЕЛОН СЛИЛ ВСЕ ДЕНЬГИ В СТРИП-КЛУБЕ GTA 6"
    assert mf.legendary_title("IShowSpeed", 2026) == "IShowSpeed's Most LEGENDARY Moments of 2026"
    assert mf.famous_title("Kai Cenat") == "clips that made kai cenat famous"
    assert mf.short_title("нарезчик стал доксером", "братишкин", "bratishkin").endswith("#братишкин #bratishkin")
    assert mf.reaction_description("https://y/1").splitlines()[0] == "Оригинал — https://y/1"
    assert mf.short_description("https://y/2", "текст").splitlines() == ["Весь ролик — https://y/2", "текст"]
    assert mf.check_title("x" * 120) and "символов" in mf.check_title("x" * 120)[0]


def test_tag_generator_deterministic_and_negative():
    tags = mf.tag_list("эвелон", ["evelone192"], obj="борат")
    assert tags == mf.tag_list("эвелон", ["evelone192"], obj="борат")  # детерминированно
    for need in ("эвелон", "евелон", "evelon", "evelone", "evelone192", "эвелон реакция", "реакция эвелона",
                 "эвелон смотрит борат"):
        assert need in tags, need
    assert mf.check_tags(tags, "эвелон") == []
    assert "повторы тегов" in mf.check_tags(["a", "a"], "a")
    assert any("латиницей" in p for p in mf.check_tags(["эвелон", "эвелон реакция"], "эвелон"))
    long = mf.tag_list("эвелон", extra=[f"тег номер {i}" for i in range(100)])
    assert sum(len(t) for t in long) + len(long) - 1 <= mf.YT_TAGS_LIMIT


# ---------------------------------------------------------------- 10. темп по типу


def _talk_with_pauses():
    words = _words(0, 10) + _words(14, 30) + _words(30.9, 40) + _words(47, 60)  # паузы ~4 с, 0,9 с, ~7 с
    return words, 60.0


def test_pace_presets_and_checks():
    words, total = _talk_with_pauses()
    short = pp.pace_cuts(words, total, "short")
    assert pp.check_pace(words, total, short, "short")["ok"]
    assert len(short) >= 3
    react = pp.pace_cuts(words, total, "reaction_full")
    check = pp.check_pace(words, total, react, "reaction_full")
    assert check["ok"] and check["cut_share"] <= 0.10
    game = pp.pace_cuts(words, total, "horizontal_game")
    assert len(game) == 1  # только пауза ~7 с
    assert pp.check_pace(words, total, game, "horizontal_game")["ok"]


def test_pace_negative_controls():
    words, total = _talk_with_pauses()
    # шортс без вырезов: паузы 4 и 7 с остались
    assert not pp.check_pace(words, total, [], "short")["ok"]
    # полная реакция, вырезано 30 %
    greedy = [{"start": 5.0, "end": 23.0}]
    assert any("вырезано" in p for p in pp.check_pace(words, total, greedy, "reaction_full")["problems"])
