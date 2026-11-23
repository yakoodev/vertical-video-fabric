"""Проверки перед сдачей (app/quality_lint_delivery.py, quality_lint_craft.py, quality_lint_assert.py,
quality_lint_sheets.py): кадр 0, мёртвые куски, держать кадр, концовка, главный объект, слайдовая обвязка,
рост из нуля, повтор входов слов, проверки-утверждения клипа, строки P0/P1/P2."""

from __future__ import annotations

import subprocess

import pytest

from app import motion
from app import quality_lint as Q
from app import quality_lint_craft as C
from app import quality_lint_delivery as D
from app.quality_lint_assert import assertion_findings, parse_assertion
from app.quality_lint_base import finding_line, priority_of
from app.timebase import ClipClock


class _Store:
    def __init__(self, canvas: bool):
        self.canvas = canvas

    def get_source(self, _sid):
        return {"source_type": "canvas" if self.canvas else "video"}


def _clock(*pieces):
    return ClipClock([{"start_sec": a, "end_sec": b} for a, b in pieces or [(0, 10)]], None)


def _text(lid, text, start=0.0, end=3.0, x=0.5, y=0.4, size=100, anim=None, **props):
    layer = {"id": lid, "type": "text", "start": start, "end": end, "x": x, "y": y,
             "props": {"text": text, "size": size, **props}}
    if anim:
        layer["in"] = {"type": anim, "duration": 0.4}
    return layer


def _img(lid, start=0.0, end=10.0, w=900, x=0.5, y=0.5, anim=None, src="pack:pic"):
    layer = {"id": lid, "type": "image", "start": start, "end": end, "x": x, "y": y, "props": {"src": src, "w": w}}
    if anim:
        layer["in"] = {"type": anim, "duration": 0.4}
    return layer


def _rs(layers, **extra):
    return {"layers": motion.normalize_layers(layers), **extra}


PLAN = {"id": 1, "source_id": 5}


# ---------------------------------------------------------------- кадр 0


def test_frame0_empty_canvas_is_p0_and_logo_only_p1():
    clock = _clock()
    empty = D.frame0_plan_findings(_Store(True), PLAN, _rs([_text("hook", "КРЮЧОК", start=0.5)]), clock)
    assert empty and empty[0]["priority"] == "P0" and "пустой" in empty[0]["message"]
    assert "крючок hook" in empty[0]["message"]
    logo = D.frame0_plan_findings(_Store(True), PLAN, _rs([_img("logo", w=300, src="pack:brand_logo")]), clock)
    assert logo[0]["priority"] == "P1" and "логотип" in logo[0]["message"]
    ok = D.frame0_plan_findings(_Store(True), PLAN, _rs([_text("hook", "КРЮЧОК")]), clock)
    assert not ok


def test_frame0_hook_with_entrance_on_video_base():
    found = D.frame0_plan_findings(_Store(False), PLAN, _rs([_text("hook", "СМОТРИ", anim="pop")]), _clock())
    assert len(found) == 1 and found[0]["priority"] == "P1" and "вход pop" in found[0]["message"]
    assert "in: none" in found[0]["fix"]
    assert not D.frame0_plan_findings(_Store(False), PLAN, _rs([_text("hook", "СМОТРИ")]), _clock())


def test_frame0_render_stats():
    assert D.frame0_render_findings({"mean": 5, "std": 2, "busy": 0.0})[0]["priority"] == "P0"
    assert "однотонный" in D.frame0_render_findings({"mean": 120, "std": 3, "busy": 0.0})[0]["message"]
    assert D.frame0_render_findings({"mean": 120, "std": 30, "busy": 0.03})[0]["priority"] == "P1"
    assert not D.frame0_render_findings({"mean": 120, "std": 40, "busy": 0.5})


def _ffmpeg_ok():
    try:
        return subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


@pytest.mark.skipif(not _ffmpeg_ok(), reason="нет ffmpeg")
def test_first_frame_stats_and_static_windows(tmp_path):
    black = tmp_path / "black.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=black:s=64x114:r=30:d=1",
                    "-pix_fmt", "yuv420p", str(black)], check=True)
    assert D.frame0_render_findings(D.first_frame_stats(black))[0]["priority"] == "P0"
    # 0–4 с стоит (серый), 4–8 с — движется (testsrc).
    mixed = tmp_path / "mixed.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=gray:s=128x228:r=30:d=4",
                    "-f", "lavfi", "-i", "testsrc2=s=128x228:r=30:d=4", "-filter_complex", "[0:v][1:v]concat=n=2[v]",
                    "-map", "[v]", "-pix_fmt", "yuv420p", str(mixed)], check=True)
    wins = D.static_windows(mixed, [(0.0, 3.8), (4.2, 7.8)])
    assert [w["static"] for w in wins] == [True, False], wins


# ---------------------------------------------------------------- мёртвые куски, держать кадр, концовка


def test_dead_gaps_canvas_vs_video_base():
    rs = _rs([_text("a", "ПЕРВОЕ", start=0, end=2), _img("pic", start=0, end=11, w=900),
              _text("b", "ВТОРОЕ", start=8, end=11)])
    clock = _clock((0, 11))
    canvas = D.dead_findings(_Store(True), PLAN, rs, clock, None)
    assert len(canvas) == 1 and canvas[0]["priority"] == "P1" and 1.9 < canvas[0]["t"] < 2.2
    assert 7.8 < canvas[0]["until"] <= 8.0
    video = D.dead_findings(_Store(False), PLAN, rs, clock, None)
    assert len(video) == 1 and video[0]["priority"] == "P2" and video[0]["severity"] == "info"
    # Наезд камеры в окне — событие: мёртвого куска нет.
    moving = dict(rs, camera=[{"t": 2.5, "zoom": 1.0}, {"t": 7.5, "zoom": 1.3}])
    assert not D.dead_findings(_Store(True), PLAN, moving, clock, None)


def test_dead_respects_cuts():
    rs = _rs([_text("a", "ПЕРВОЕ", start=0, end=1)])
    # Два куска по 4 с: склейка на 4 с делит пустоту на окна 3 и 4 с.
    found = D.dead_findings(_Store(True), PLAN, rs, _clock((0, 4), (10, 14)), None)
    assert len(found) == 1 and found[0]["t"] >= 3.9


def test_hold_after_last_settle():
    clock = _clock((0, 4), (10, 14))
    short = _rs([_text("late", "ПОЗДНО", start=3.0, end=4.0, anim="rise")])
    found = D.hold_findings(short, clock)
    assert found and found[0]["priority"] == "P1" and "встало и сразу ушло" in found[0]["message"]
    good = _rs([_text("early", "РАНО", start=1.0, end=4.0, anim="rise")])
    assert not D.hold_findings(good, clock)
    # Последняя сцена: стоп-кадр в конце добавляет к удержанию.
    tail = _rs([_text("end", "КОНЕЦ", start=7.2, end=8.0)])
    assert D.hold_findings(tail, clock) and not D.hold_findings(tail, clock, end_hold=0.5)


def test_outro_limits():
    clock = _clock((0, 10))
    long = _rs([_img("outro_card", start=7.5, end=10)])
    found = D.outro_findings(long, clock)
    assert found and found[0]["priority"] == "P1" and "2.5 с" in found[0]["message"]
    assert not D.outro_findings(_rs([_img("outro_card", start=8.8, end=10)]), clock)
    with_banner = {"layers": [], "end_screen": {"duration": 3}}
    assert D.outro_findings(with_banner, clock)


# ---------------------------------------------------------------- ремесло графики


def test_subject_small_on_canvas_only():
    layers = D.visual_layers(_rs([_img("tiny", start=0, end=4, w=250), _text("t", "ПОДПИСЬ", start=0, end=4)]))
    found = C.subject_findings(layers, 4.0, canvas=True)
    assert found and found[0]["layer"] == "tiny" and "scale ≈" in found[0]["fix"]
    assert not C.subject_findings(layers, 4.0, canvas=False)
    big = D.visual_layers(_rs([_img("hero", start=0, end=4, w=800)]))
    assert not C.subject_findings(big, 4.0, canvas=True)
    only_text = D.visual_layers(_rs([_text("t", "ТОЛЬКО ТЕКСТ", start=0, end=4)]))
    assert not C.subject_findings(only_text, 4.0, canvas=True)


def test_slide_chrome():
    layers = D.visual_layers(_rs([
        _text("cnt", "02/05", start=0, end=3),
        _text("step", "шаг 3 из 5", start=3, end=6),
        _text("corner", "мой канал", start=0, end=10, x=0.1, y=0.06, size=40),
        _text("tw1", "Печатаем красивую длинную фразу для украшения", start=0, end=3, anim="typewriter"),
        _text("ok", "НОРМ", start=0, end=3),
    ]))
    found = C.chrome_findings(layers, 10.0)
    ids = {f["layer"] for f in found}
    assert {"cnt", "step", "corner", "tw1"} <= ids and "ok" not in ids
    assert all(f["check"] == "slide_chrome" for f in found)


def test_fact_chip_with_n_of_m_is_not_a_slide_counter():
    """Сова, гайд 6 (23.11): факт-чип «2 из 3» в кадре линт принял за счётчик слайдов. Голое «N из M» — счётчик,
    только если их серия (1/4, 2/4…) или он мелко у края кадра; со словом («шаг 3 из 5») или с нулём («02/05») — всегда."""
    def ids(*layers):
        return {f["layer"] for f in C.chrome_findings(D.visual_layers(_rs(list(layers))), 10.0)}

    assert not ids(_text("chip", "2 из 3", start=1, end=3, size=90), _text("big", "9/10", start=4, end=6))
    assert ids(_text("a", "1/4", start=0, end=2), _text("b", "2/4", start=2, end=4)) == {"a", "b"}
    assert ids(_text("edge", "2 из 5", y=0.06, size=40)) == {"edge"}
    assert ids(_text("word", "слайд 2 из 3"), _text("zero", "02 из 03", start=4, end=6)) == {"word", "zero"}
    assert not ids(_text("odd", "5 из 3"))  # номер больше «всего» — не счётчик
    assert C.counter_parts("шаг 3 из 5") == ("шаг", 3, 5, False) and C.counter_parts("2 из 3 людей") is None


def test_word_entrances_normalize_text_only_and_aliases():
    from app.motion_limits import ANIMATIONS, WORD_ENTRANCES

    assert set(WORD_ENTRANCES) <= set(ANIMATIONS)
    text = motion.normalize_layers([_text("a", "ИМЯ", anim="byLetter"), _text("b", "ПРИЗ", anim="slot")])
    assert [l["in"]["type"] for l in text] == ["letters", "slot"]
    pic = motion.normalize_layers([_img("p", anim="focus")])
    assert pic[0]["in"]["type"] == "fade"


def test_zero_scale_and_entrance_repeats():
    layers = D.visual_layers(_rs([
        _text("p", "ПОП", anim="pop"), _text("s", "ОСЕДАЕТ", anim="slideUp"),
        _text("f1", "РАЗ", start=0, end=2, anim="focus"), _text("f2", "ДВА", start=3, end=5, anim="focus"),
        _text("c1", "ТРИ", start=6, end=8, anim="colorize"),
    ]))
    zero = C.zero_scale_findings(layers)
    assert [f["layer"] for f in zero] == ["p"]
    keyed = D.visual_layers(_rs([{**_img("k", w=500), "keys": {"scale": [{"t": 0, "v": 0}, {"t": 0.4, "v": 1}]}}]))
    assert C.zero_scale_findings(keyed)
    rep = C.entrance_findings(layers)
    assert len(rep) == 1 and rep[0]["layer"] == "f2" and "из размытия" in rep[0]["message"]
    assert "colorize" not in rep[0]["fix"] and "slot" in rep[0]["fix"]


# ---------------------------------------------------------------- проверки-утверждения


def test_parse_assertions_ru_and_objects():
    assert parse_assertion("Заголовок появился к 1.5") == {"kind": "appear", "x": "Заголовок", "by": 1.5, "final": False}
    assert parse_assertion("лого раньше призыв")["kind"] == "before"
    assert parse_assertion("кнопка не выходит за кадр")["kind"] == "inside"
    assert parse_assertion("сцена не стоит 0–10") == {"kind": "moving", "from": 0.0, "to": 10.0, "max_still": 3.0,
                                                      "final": False}
    assert parse_assertion({"before": ["a", "b"]})["kind"] == "before"
    assert parse_assertion("что-то непонятное") is None


def test_assertions_evaluate():
    rs = _rs([_text("title", "ГЛАВНОЕ", start=1.0, end=5.0, anim="rise"),
              _text("cta", "ПОДПИШИСЬ", start=0.5, end=5.0),
              _text("wide", "ОЧЕНЬ ДЛИННАЯ СТРОКА ДЛЯ ПРОВЕРКИ", start=0, end=3, x=0.85, maxWidth=2000)])
    clock = _clock((0, 10))
    items = ["главное появился к 1.0", "ГЛАВНОЕ раньше cta", "wide не выходит за кадр", "cta не выходит за кадр",
             "сцена не стоит", "белиберда", {"appear": "title", "by": 2}]
    found, info = assertion_findings(items, rs, clock)
    msgs = " | ".join(f["message"] for f in found)
    assert info == {"total": 7, "passed": 2}, (info, msgs)
    assert "позже 1 с" in msgs and "не раньше" in msgs and "выходит за кадр справа" in msgs
    assert "сцена стоит" in msgs and "не понял" in msgs
    assert all(f["priority"] == ("P2" if "не понял" in f["message"] else "P1") for f in found)


# ---------------------------------------------------------------- строки и весь прогон


def test_priority_and_line_format():
    item = {"check": "hold", "severity": "warn", "message": "встало и сразу ушло", "fix": "держи кадр — set_end_hold(sec: 0)",
            "t": 3.2, "final_t": 2.9, "until": 4.0, "where": "сцена 2"}
    assert priority_of(item) == "P1"
    assert finding_line(item) == "P1 · t=2.9…+0.8 · сцена 2 · встало и сразу ушло → держи кадр"
    assert priority_of({"check": "flashes", "severity": "warn"}) == "P0"
    assert priority_of({"check": "reading", "severity": "info"}) == "P2"
    assert finding_line({"check": "montage", "severity": "info", "message": "м"}) == "P2 · t=— · монтаж · м"


def test_lint_clip_delivery_checks_and_assertions_file(tmp_path, monkeypatch):
    from app.agent_tools import ToolContext, run_tool
    from test_clip_spec import _setup

    client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    rs = dict(store.get_clip_plan(plan["id"])["render_settings"] or {})
    rs["layers"] = [_text("hook", "СМОТРИ", anim="pop", end=2.0),
                    _text("f1", "РАЗ", start=0.5, end=2, anim="focus"), _text("f2", "ДВА", start=2, end=3, anim="focus")]
    store.set_clip_plan_render_settings(plan["id"], rs)
    result = Q.lint_clip(store, plan["id"], checks=["frame0", "zero_scale", "entrances", "assertions"], media=False)
    assert {f["check"] for f in result["findings"]} >= {"frame0", "zero_scale", "entrances"}
    assert result["lines"] and all(line.startswith(("P0 ·", "P1 ·", "P2 ·")) for line in result["lines"])
    assert [f["priority"] for f in result["findings"]] == sorted(f["priority"] for f in result["findings"])
    assert "assertions" in result["skipped"] and "P1" in result["by_priority"]

    ctx = ToolContext(store=store, clip_plan_id=plan["id"])
    out, ok = run_tool(ctx, "lint_clip", {"checks": ["assertions"], "assertions": ["СМОТРИ появился к 0.1"],
                                          "save_assertions": True, "media": False})
    assert ok and out["assertions"] == {"total": 1, "passed": 0} and out["assertions_saved"]
    again = Q.lint_clip(store, plan["id"], checks=["assertions"], media=False)
    assert again["assertions"]["total"] == 1 and again["findings"][0]["check"] == "assertions"
    body = client.get(f"/api/clip-plans/{plan['id']}/lint/assertions").raise_for_status().json()
    assert body["assertions"] == ["СМОТРИ появился к 0.1"]
    assert client.put(f"/api/clip-plans/{plan['id']}/lint/assertions", json={"assertions": ["???"]}).status_code == 400
    put = client.put(f"/api/clip-plans/{plan['id']}/lint/assertions", json={"assertions": ["hook раньше f2"]})
    assert put.status_code == 200
    assert Q.lint_clip(store, plan["id"], checks=["assertions"], media=False)["assertions"]["passed"] == 1


@pytest.mark.skipif(not _ffmpeg_ok(), reason="нет ffmpeg")
def test_cut_strip_and_phone_sheet_from_render(tmp_path, monkeypatch):
    from app.agent_tools import ToolContext, run_tool
    from app.quality_lint_sheets import cut_strip_jpeg, cut_times, phone_sheet_jpeg
    from app.settings import settings
    from test_clip_spec import _setup

    client, store, source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    clip = store.create_clip(source["id"], clip_plan_id=plan["id"], status="queued")
    settings.clip_dir.mkdir(parents=True, exist_ok=True)
    video = settings.clip_dir / f"sheet-{clip['id']}.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=270x480:r=30:d=10",
                    "-pix_fmt", "yuv420p", str(video)], check=True)
    store.finish_clip_render(clip["id"], "succeeded", output_path=str(video), duration_sec=10)
    assert cut_times(ClipClock.for_plan(store.get_clip_plan(plan["id"]))) == [5.0]
    data, info = cut_strip_jpeg(store, plan["id"])
    assert data[:2] == b"\xff\xd8" and info["cuts"] == [5.0] and info["frames_from"] == "render"
    sheet, pinfo = phone_sheet_jpeg(store, plan["id"], n=4)
    assert sheet[:2] == b"\xff\xd8" and pinfo["width"] == 360 and len(pinfo["times"]) == 4
    _out, ok = run_tool(ToolContext(store=store, clip_plan_id=plan["id"]), "contact_sheet", {"mode": "cuts"})
    assert ok
    res = client.get(f"/api/clip-plans/{plan['id']}/contact-sheet?mode=phone&n=4")
    assert res.status_code == 200 and res.headers["content-type"] == "image/jpeg"
    # Всё вместе по рендеру: кадр 0 и мёртвые куски читают файл, ничего не падает.
    full = Q.lint_clip(store, plan["id"], checks=["frame0", "dead", "hold", "outro"])
    assert "frame0" not in full["skipped"] and "dead" in full
