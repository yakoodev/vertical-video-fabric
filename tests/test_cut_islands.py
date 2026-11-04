"""Обрывки после вырезов: острова на стыках кусков, панчлайн за короткой паузой, merge, suggest_cuts, lint."""

from __future__ import annotations

from app import quality_lint as Q
from app.agent_tools import ToolContext, run_tool
from app.cuts import (
    SOFT_CUT_SEC,
    check_cuts_for_plan,
    cut_warnings,
    kept_islands,
    merge_short_islands,
    short_islands,
    suggest_cuts,
)
from app.settings import settings
from test_api_pipeline import _client


def _w(word, start, end):
    return {"word": word, "start": start, "end": end}


def _phrase(start, end, count=6):
    step = (end - start) / count
    return [_w(f"слово{start:g}_{k}", start + k * step, start + (k + 1) * step - 0.02) for k in range(count)]


def _seg(start, end):
    return {"start_sec": start, "end_sec": end}


# ---------------------------------------------------------------- острова


def test_real_case_scraps_between_long_cuts():
    # Горизонталка Муры: два выреза на 6 и 13 с, между ними — одно слово на 0.8 с.
    words = _phrase(0, 10) + [_w("Да", 16.2, 16.8)] + _phrase(30, 40)
    cuts = [{"start": 10, "end": 16}, {"start": 17, "end": 30}]
    islands = kept_islands(cuts, words, total=40.0)
    assert [i["duration"] for i in islands] == [10, 1, 10]
    short = short_islands(islands)
    assert len(short) == 1 and short[0]["text"] == "Да" and short[0]["words"] == 1 and short[0]["by_cut"]


def test_piece_boundary_is_a_break():
    # Вырез кончается у конца куска: остаток куска упирается в стык с другим местом исходника.
    segs = [_seg(100, 110), _seg(500, 520)]
    words = [_w("начало", 1.0, 1.5), _w("и", 1.6, 1.7), _w("конец", 1.8, 2.2), _w("всё", 9.2, 9.6)] + _phrase(10, 20)
    islands = kept_islands([{"start": 3, "end": 9}], words, segs)
    assert [(i["start"], i["end"]) for i in islands] == [(0, 3), (9, 10), (10, 30)]
    short = short_islands(islands)
    assert [i["text"] for i in short] == ["всё"]


def test_continuing_piece_is_not_a_break():
    # Следующий кусок продолжает предыдущий по исходнику: стык незаметен, остров один.
    segs = [_seg(100, 110), _seg(110, 130)]
    words = [_w("всё", 9.2, 9.6)] + _phrase(10, 20)
    islands = kept_islands([{"start": 3, "end": 9}], words, segs)
    assert [(i["start"], i["end"]) for i in islands] == [(0, 3), (9, 30)]
    assert not short_islands(islands, has_words=False)


def test_cut_over_piece_boundary_is_a_break_even_when_short():
    segs = [_seg(100, 110), _seg(500, 520)]
    islands = kept_islands([{"start": 9.8, "end": 10.2}], [], segs)
    assert [(i["start"], i["end"]) for i in islands] == [(0, 9.8), (10.2, 30)]


def test_punchline_right_after_short_pause_is_not_a_scrap():
    # Панчлайн сразу за вырезом короткой паузы — конец фразы, а не вспышка.
    words = _phrase(0, 10) + [_w("Попал!", 10.5, 11.0)] + _phrase(25, 35)
    cuts = [{"start": 10.05, "end": 10.45}, {"start": 11.2, "end": 25}]
    assert 10.45 - 10.05 <= SOFT_CUT_SEC
    islands = kept_islands(cuts, words, total=35.0)
    assert len(islands) == 2 and islands[0]["parts"] == 2 and "Попал!" in islands[0]["text"]
    assert not short_islands(islands)


def test_short_piece_without_cut_is_not_blamed_on_cuts():
    # Кусок 2 с целиком — мог быть кадр под баннер; вырезы тут ни при чём.
    segs = [_seg(0, 10), _seg(50, 52), _seg(90, 100)]
    islands = kept_islands([], [], segs)
    assert len(islands) == 3 and not islands[1]["by_cut"]
    assert not short_islands(islands, has_words=False)
    assert len(short_islands(islands, has_words=False, by_cut_only=False)) == 1


def test_words_rule_only_for_short_islands_and_with_subtitles():
    long_quiet = [{"start": 9, "end": 12}]
    words = [_w("а", 1, 1.1)] + _phrase(20, 30)
    # 10 с игры с одним словом — не обрывок, и 4 с с одним словом — обрывок.
    assert not short_islands(kept_islands(long_quiet, words, total=30.0))
    short = short_islands(kept_islands([{"start": 4, "end": 12}], words, total=30.0))
    assert [i["text"] for i in short] == ["а"]
    # Субтитров нет — судим только по длине.
    assert not short_islands(kept_islands([{"start": 4, "end": 12}], [], total=30.0), has_words=False)
    assert short_islands(kept_islands([{"start": 2, "end": 12}], [], total=30.0), has_words=False)


def test_threshold_and_single_island():
    cuts = [{"start": 4, "end": 12}]
    words = _phrase(0, 4) + _phrase(12, 22)
    islands = kept_islands(cuts, words, total=22.0)
    assert not short_islands(islands)
    assert len(short_islands(islands, min_sec=5.0)) == 1
    assert not short_islands(islands, min_sec=0)
    # Весь клип — один остров: не обрывок, как бы ни был короток.
    assert not short_islands(kept_islands([], [_w("а", 0, 0.2)], total=1.0))


# ---------------------------------------------------------------- merge


def test_merge_removes_scrap_with_neighbours():
    words = _phrase(0, 10) + [_w("Да", 16.2, 16.8)] + _phrase(30, 40)
    cuts = [{"start": 10, "end": 16}, {"start": 17, "end": 30}]
    wider, removed = merge_short_islands(cuts, words, total=40.0)
    assert wider == [{"start": 10.0, "end": 30.0}] and removed[0]["text"] == "Да"
    assert not short_islands(kept_islands(wider, words, total=40.0))


def test_merge_never_removes_the_whole_clip():
    wider, removed = merge_short_islands([{"start": 1, "end": 9}], [], total=10.0)
    assert len(removed) == 1 and wider[0]["start"] in (0.0, 1.0)
    assert kept_islands(wider, [], total=10.0)


def test_merge_can_swallow_a_whole_piece():
    plan = {"segments": [_seg(0, 10), _seg(50, 53), _seg(90, 100)]}
    cuts = [{"start": 11, "end": 13}]  # от куска в 3 с остаётся 1 с
    wider, removed = merge_short_islands(cuts, [], plan["segments"])
    assert wider == [{"start": 10.0, "end": 13.0}] and len(removed) == 1
    assert check_cuts_for_plan(plan, wider)[1] == []


# ---------------------------------------------------------------- suggest_cuts


def test_suggest_keeps_cutting_silence_between_phrases():
    # Пауза до и после короткого слова — вырез тишины, а не разрыв: слово остаётся в разговоре.
    words = _phrase(0, 8) + [_w("Да", 9.6, 10.0)] + _phrase(11.7, 20)
    out = suggest_cuts(words, 20.0, mode="pauses")
    assert len(out["cuts"]) == 2 and not any(it.get("risky") for it in out["items"])
    assert not short_islands(kept_islands(out["cuts"], words, total=20.0))


def test_suggest_does_not_leave_a_scrap_between_pieces():
    # Кусок на 1 с из одного слова: паузы вокруг него идут через стыки кусков, это настоящие разрывы.
    words = _phrase(0, 2) + [_w("Да", 3.2, 3.6)] + _phrase(4.6, 14)
    segs = [_seg(100, 103), _seg(500, 501), _seg(900, 910)]
    alone = suggest_cuts(words, 14.0, mode="pauses")
    assert len(alone["cuts"]) == 2
    aware = suggest_cuts(words, 14.0, mode="pauses", segments=segs)
    assert not short_islands(kept_islands(aware["cuts"], words, segs), True)
    assert aware["cuts"] == [] and all(it["risky"] for it in aware["items"])
    assert any("обрывок «Да»" in it["reason"] for it in aware["items"])


def test_suggest_keeps_edge_cuts():
    # Тишина по краю клипа соседей не соединяет — такой вырез не снимаем.
    words = [_w("Да", 3.2, 3.6)] + _phrase(4.6, 14)
    segs = [_seg(100, 104), _seg(500, 512)]
    out = suggest_cuts(words, 16.0, mode="pauses", segments=segs)
    assert out["cuts"][0]["start"] == 0 and out["cuts"][-1]["end"] == 16.0


# ---------------------------------------------------------------- warnings: одно сообщение на кусок


def test_cut_warnings_do_not_repeat_old_scrap_message():
    words = _phrase(0, 5) + [_w("я", 10.1, 10.3)] + _phrase(20, 30)
    cuts = [{"start": 5, "end": 10}, {"start": 10.4, "end": 20}]
    out = cut_warnings(cuts, words, [], 30.0)
    assert sum("«я»" in w for w in out) == 1 and "merge_short_islands" in out[0]
    assert cut_warnings(cuts, words, [], 30.0, min_sec=0)  # порог выключен — остаётся старый огрызок


# ---------------------------------------------------------------- инструменты и линт


SCRAP_WORDS = _phrase(0, 10) + [_w("Да", 16.2, 16.8)] + _phrase(25, 30)
SCRAP_CUTS = [{"start": 10, "end": 16}, {"start": 17, "end": 25}]


def _clip_with_scrap(tmp_path, monkeypatch):
    _client_, store = _client(tmp_path, monkeypatch)
    source_path = settings.source_dir / "s.mp4"
    source_path.write_bytes(b"src")
    source = store.create_source("upload", source_path, duration_sec=60, width=1280, height=720, status="ready")
    analysis = store.create_ai_analysis(source["id"], "mock", status="succeeded")
    seg = store.create_ai_segment(analysis["id"], {"start_sec": 10, "end_sec": 40, "title": "A"})
    plan = store.create_clip_plan(source["id"], analysis["id"], "План", segment_ids=[seg["id"]])
    store.set_clip_plan_subtitles(plan["id"], {"words": SCRAP_WORDS})
    return store, plan, ToolContext(store=store, clip_plan_id=plan["id"])


def test_apply_cuts_warns_and_merges(tmp_path, monkeypatch):
    store, plan, ctx = _clip_with_scrap(tmp_path, monkeypatch)
    out, ok = run_tool(ctx, "apply_cuts", {"cuts": SCRAP_CUTS})
    assert ok and out["cuts"] == 2 and any("обрывок «Да»" in w for w in out["warnings"])
    assert "merged_islands" not in out

    out, ok = run_tool(ctx, "apply_cuts", {"cuts": SCRAP_CUTS, "merge_short_islands": True})
    assert ok and out["cuts"] == 1 and out["merged_islands"] == [{"start": 16.0, "end": 17.0, "text": "Да"}]
    assert not any("обрывок" in w for w in out.get("warnings") or [])
    assert store.get_clip_plan(plan["id"])["render_settings"]["cuts"] == [{"start": 10.0, "end": 25.0}]

    # Порог выключен — вырезы как есть, без предупреждения об обрывке.
    out, ok = run_tool(ctx, "apply_cuts", {"cuts": SCRAP_CUTS, "min_island_sec": 0})
    assert ok and out["cuts"] == 2 and not any("обрывок" in w for w in out.get("warnings") or [])
    out, ok = run_tool(ctx, "apply_cuts", {"cuts": SCRAP_CUTS, "min_island_sec": -1})
    assert not ok and "min_island_sec" in out["error"]


def test_get_clip_shows_scraps(tmp_path, monkeypatch):
    _store, _plan, ctx = _clip_with_scrap(tmp_path, monkeypatch)
    assert run_tool(ctx, "apply_cuts", {"cuts": SCRAP_CUTS})[1]
    out, ok = run_tool(ctx, "get_clip", {})
    assert ok and any("Вырезы: обрывок «Да»" in w for w in out.get("warnings") or [])


def test_lint_fragments_warns_by_plan_without_render(tmp_path, monkeypatch):
    store, plan, ctx = _clip_with_scrap(tmp_path, monkeypatch)
    assert "fragments" in Q.CHECKS and Q.normalize_checks(["islands"]) == ["fragments"]
    clean = Q.lint_clip(store, plan["id"], checks=["fragments"])
    assert clean["fragments"]["short"] == 0 and not clean["findings"]

    assert run_tool(ctx, "apply_cuts", {"cuts": SCRAP_CUTS})[1]
    result = Q.lint_clip(store, plan["id"], checks=["fragments"], media=False)
    assert result["fragments"] == {"pieces": 3, "short": 1}
    found = result["findings"]
    assert len(found) == 1 and found[0]["severity"] == "warn" and "«Да»" in found[0]["message"]
    assert "merge_short_islands" in found[0]["fix"]
    assert found[0]["t"] == 16.0 and found[0]["until"] == 17.0 and found[0]["final_t"] == 10.0
    assert "обрывки" in result["summary"]
    # По умолчанию проверка входит в общий прогон.
    assert "fragments" in Q.lint_clip(store, plan["id"], media=False)["by_check"]
