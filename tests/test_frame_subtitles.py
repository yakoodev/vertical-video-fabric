"""Кадр агента (view_frame / contact_sheet) — с субтитрами клипа, как их прожжёт рендер.

Клипы 583–585: агент видел кадр без субтитров и не заметил, что они легли на HUD игры, — это
вскрылось только на рендере. Теперь кадр прожигает тот же ASS (стиль, отступ, формат кадра).
"""

from __future__ import annotations

from pathlib import Path

from test_motion import _render_fixture

WORDS = [{"word": "ЧЕМПИОН", "start": 1.0, "end": 1.6}, {"word": "арены", "start": 1.6, "end": 2.2}]


def _frame_fixture(tmp_path, monkeypatch, **render):
    store, _service, plan, _banner, calls, _comps, kwargs = _render_fixture(tmp_path, monkeypatch)
    seen_ass: list[str] = []

    def fake_ffmpeg(args, timeout, cwd=None):
        calls.append(("ffmpeg", list(args)))
        vf = args[args.index("-vf") + 1] if "-vf" in args else ""
        if "ass=" in vf:  # прожиг субтитров в кадр — запомнить ASS, пока он есть
            seen_ass.append((Path(cwd) / vf.split("ass=", 1)[1]).read_text(encoding="utf-8"))
        Path(args[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(args[-1]).write_bytes(b"png")

    def fake_compositor(args, timeout):
        calls.append(("compositor", list(args)))
        Path(args[args.index("--png") + 1]).write_bytes(b"motion-png")

    monkeypatch.setattr("app.render._run_ffmpeg", fake_ffmpeg)
    monkeypatch.setattr("app.render._run_compositor", fake_compositor)
    store.set_clip_plan_render_settings(plan["id"], {"preset_id": kwargs["ffmpeg_preset_id"], "subs_on": True,
                                                     "sub_id": kwargs["subtitle_profile_id"], **render})
    store.set_clip_plan_subtitles(plan["id"], {"lines": [{"start": 1.0, "end": 2.2, "text": "ЧЕМПИОН арены"}],
                                               "words": WORDS})
    return store, plan, calls, seen_ass


def _burns(calls):
    return [args for kind, args in calls if kind == "ffmpeg" and "-vf" in args and "ass=" in args[args.index("-vf") + 1]]


def test_frame_burns_clip_subtitles_like_render(tmp_path, monkeypatch):
    from app.motion_render import render_frame_png

    store, plan, calls, seen_ass = _frame_fixture(tmp_path, monkeypatch)
    notes: list[str] = []
    render_frame_png(store, plan["id"], 1.5, subtitles=True, notes=notes)
    burns = _burns(calls)
    assert len(burns) == 1 and "setpts=PTS-STARTPTS+1.500/TB" in burns[0][burns[0].index("-vf") + 1]
    assert burns[0][-1].endswith("frame-subs.png")
    ass = seen_ass[0]
    assert "PlayResY: 320" in ass  # кадр формата клипа (пресет 180×320), как в рендере
    assert "ЧЕМПИОН" in ass.upper() and "Dialogue:" in ass
    assert notes and "прожжены" in notes[0]
    # Тот же ASS, что сделал бы рендер: отступ из sub_pos_pct (12% от 1920 — 230).
    style = next(line for line in ass.splitlines() if line.startswith("Style:"))
    assert style.split(",")[-2] == "230"


def test_frame_without_subtitles_when_off_or_replaced_by_captions(tmp_path, monkeypatch):
    from app.motion_render import render_frame_png

    store, plan, calls, _ass = _frame_fixture(tmp_path, monkeypatch)
    render_frame_png(store, plan["id"], 1.5)  # по умолчанию (превью, обложка) — без прожига
    render_frame_png(store, plan["id"], 1.5, subtitles=False)
    assert not _burns(calls)
    # Субтитры клипа выключены — нечего жечь.
    store2, plan2, calls2, _ = _frame_fixture(tmp_path / "off", monkeypatch, subs_on=False)
    render_frame_png(store2, plan2["id"], 1.5, subtitles=True)
    assert not _burns(calls2)
    # Слой captions со словами заменяет прожиг (как в рендере) — двух текстов речи нет.
    store3, plan3, calls3, _ = _frame_fixture(tmp_path / "caps", monkeypatch, layers=[
        {"id": "caps", "type": "captions", "start": 0, "end": 5, "props": {"style": "hormozi"}}])
    render_frame_png(store3, plan3["id"], 1.5, subtitles=True)
    assert not _burns(calls3)


def test_view_frame_and_contact_sheet_ask_for_subtitles(tmp_path, monkeypatch):
    from app import motion_render
    from app.agent_tools import ToolContext, run_tool

    store, plan, _calls, _ass = _frame_fixture(tmp_path, monkeypatch)
    seen: list[dict] = []

    def fake_frame(store_, pid, t, **kw):
        seen.append(kw)
        if kw.get("notes") is not None and kw.get("subtitles"):
            kw["notes"].append("субтитры прожжены как в рендере")
        return b"png"

    monkeypatch.setattr(motion_render, "render_frame_png", fake_frame)
    monkeypatch.setattr(motion_render, "tile_frames_jpeg", lambda paths, labels, width, cols=4: b"jpg")
    ctx = ToolContext(store=store, clip_plan_id=plan["id"])
    out, ok = run_tool(ctx, "view_frame", {"t": 1.5})
    assert ok and seen[-1]["subtitles"] is True
    run_tool(ctx, "view_frame", {"t": 1.5, "subtitles": False})
    assert seen[-1]["subtitles"] is False
    run_tool(ctx, "view_frame", {"t": 1.5, "with_layers": False})
    assert seen[-1]["subtitles"] is False  # кадр основы — без всего
    seen.clear()
    run_tool(ctx, "contact_sheet", {"n": 2})
    assert [kw["subtitles"] for kw in seen] == [True, True]
