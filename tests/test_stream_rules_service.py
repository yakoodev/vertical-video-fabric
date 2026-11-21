"""Правила стрим-нарезок, встроенные в сервис: компиляция (порядок по силе, без аутро), set_camera breathing,
suggest_cuts pace, гистерезис цели в автофокусе."""

from __future__ import annotations

from app.agent_tools import ToolContext, run_tool
from app.compilation import build_compilation
from test_clip_spec import _store_plan


def _three_clips(tmp_path, monkeypatch):
    store, plan = _store_plan(tmp_path, monkeypatch, [])
    sid, aid = plan["source_id"], plan["analysis_id"]
    segs = [store.create_ai_segment(aid, {"start_sec": a, "end_sec": a + 8, "title": t})
            for a, t in ((0, "раз"), (15, "два"), (35, "три"))]
    clips = []
    for seg, title, score in zip(segs, ("Слабый", "Сильный", "Средний"), (0.2, 0.9, 0.5)):
        clip = store.create_clip_plan(sid, aid, title, segment_ids=[seg["id"]])
        store.set_clip_plan_subtitles(clip["id"], {"lines": [], "words": [{"word": title, "start": 0.5, "end": 2.0}],
                                                   "duration": 8})
        clips.append((clip, score))
    return store, sid, clips


def test_compilation_strength_order_trims_outro_and_reports_check(tmp_path, monkeypatch):
    store, sid, clips = _three_clips(tmp_path, monkeypatch)
    strengths = {c["id"]: s for c, s in clips}
    res = build_compilation(store, sid, [c["id"] for c, _ in clips], order="strength", strengths=strengths)
    comp = store.get_clip_plan(res["clip_plan_id"])
    titles = [s["title"] for s in comp["segments"]]
    assert titles == ["два", "раз", "три"]  # сильнейший первым, второй по силе последним
    cuts = comp["render_settings"]["cuts"]
    assert cuts and abs(cuts[0]["start"] - (16 + 2.0 + 1.0)) < 1e-6 and cuts[0]["end"] == 24  # без аутро
    check = res["check"]
    assert check["tail_after_word"] == 1.0 and not any("первым" in p or "аутро" in p for p in check["problems"])
    # негативный контроль: прежний обратный отсчёт по силе не проходит, без выреза хвоста — аутро
    old = build_compilation(store, sid, [c["id"] for c, _ in clips], countdown=True, strengths=strengths,
                            trim_outro=False)
    assert any("первым" in p for p in old["check"]["problems"]) and any("аутро" in p for p in old["check"]["problems"])
    assert "cuts" not in store.get_clip_plan(old["clip_plan_id"])["render_settings"] or \
        not store.get_clip_plan(old["clip_plan_id"])["render_settings"]["cuts"]


def test_set_camera_breathing_and_suggest_cuts_pace(tmp_path, monkeypatch):
    store, plan = _store_plan(tmp_path, monkeypatch, [])
    ctx = ToolContext(store=store, clip_plan_id=plan["id"])
    out, ok = run_tool(ctx, "set_camera", {"breathing": True})
    assert ok, out
    assert out["breathing"]["ok"] and out["breathing"]["final"] == 1.15
    zooms = [k["zoom"] for k in out["camera"]]
    assert min(zooms) == 1.0 and max(zooms) <= 1.2
    words = [{"word": f"w{i}", "start": t, "end": t + 0.3} for i, t in enumerate([0.5, 1.0, 1.5, 4.0, 4.5, 12.0, 12.5])]
    store.set_clip_plan_subtitles(plan["id"], {"lines": [], "words": words, "duration": 20})
    out, ok = run_tool(ctx, "suggest_cuts", {"pace": "short"})
    assert ok, out
    assert out["check"]["ok"] and out["check"]["max_pause"] <= 0.7 and out["check"]["cuts"] == 2  # на шкале clip вырез через стык — двумя
    out, ok = run_tool(ctx, "suggest_cuts", {"pace": "nope"})
    assert not ok


def test_autofocus_output_holds_target_between_switches():
    from app.split_rules import check_target_switches, hold_targets

    flips = [{"t": i * 0.4, "x": 0.25 if i % 2 else 0.75, "y": 0.5} for i in range(20)]
    assert not check_target_switches(flips)["ok"]
    assert check_target_switches(hold_targets(flips))["ok"]
