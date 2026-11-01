"""Повторный QA Gemini-монтажёра (02.11, клип 892, плейбук vtuber-story): находки, починенные в сервисе.

Аватар в нескольких местах и проверка взгляда — tests/test_avatar_framing.py и tests/test_editor_agent.py."""

from __future__ import annotations

from app.agent_tools import ToolContext, run_tool
from test_api_pipeline import _require_ffmpeg
from test_clip_spec import _setup

# ------------------------------------------------------------------ 3) наезд-удар, а не медленный наезд


def test_slow_zoom_warns_and_punch_at_builds_a_hit(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    ctx = ToolContext(store=store, clip_plan_id=plan["id"])
    # Как у клипа 892: [{t:0, zoom:1.0}, {t:28, zoom:1.15}] — плавно через весь ролик.
    out, ok = run_tool(ctx, "set_camera", {"keys": [{"t": 0, "zoom": 1.0}, {"t": 8, "zoom": 1.15}]})
    assert ok, out
    slow = [w for w in out["warnings"] if "медленный наезд" in w]
    assert slow and "8 с" in slow[0] and "punch_at" in slow[0]
    # Короткий удар — без предупреждения.
    out, ok = run_tool(ctx, "set_camera", {"keys": [{"t": 7.8, "zoom": 1.0}, {"t": 8, "zoom": 1.15},
                                                     {"t": 8.8, "zoom": 1.15}, {"t": 9.1, "zoom": 1.0}]})
    assert ok and not [w for w in out["warnings"] if "медленный" in w]
    # Помощник punch_at: ключи удара сами, прежние ключи вне окна остаются.
    out, ok = run_tool(ctx, "set_camera", {"keys": [{"t": 1.0, "zoom": 1.05}], "punch_at": 6.0})
    assert ok, out
    zooms = [(k["t"], k["zoom"]) for k in out["camera"]]
    assert (1.0, 1.05) in zooms and (5.8, 1.0) in zooms and (6.0, 1.15) in zooms and (6.8, 1.15) in zooms
    assert zooms[-1] == (7.15, 1.0) and not [w for w in out["warnings"] if "медленный" in w]
    assert out["punch"]["at"] == [6.0]
    # Без keys — поверх текущей камеры; несколько ударов.
    out, ok = run_tool(ctx, "set_camera", {"punch_at": [2, 6], "punch_zoom": 1.2})
    assert ok and sum(1 for k in out["camera"] if k["zoom"] == 1.2) == 4
    _out, ok = run_tool(ctx, "set_camera", {"punch_at": 2, "punch_zoom": 3})
    assert not ok


# ------------------------------------------------------------------ 4) ч/б до конца клипа; 5) повтор вставки


def test_grayscale_to_the_end_warns_in_add_layers_and_lint(tmp_path, monkeypatch):
    from app import motion
    from app.quality_lint import lint_clip

    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    ctx = ToolContext(store=store, clip_plan_id=plan["id"])
    # Клип 10 с; ч/б 6.7 с → конец (как 28.0–31.3 у клипа 892).
    bw = {"id": "bw", "type": "fx", "start": 6.7, "end": 10.0, "track": 1, "props": {"kind": "grayscale"}}
    out, ok = run_tool(ctx, "add_layers", {"layers": [bw]})
    assert ok, out
    warning = next(w for w in out["warnings"] if "ч/б" in w)
    assert "до конца клипа" in warning and "t + 1.5–2 с" in warning
    lint = lint_clip(store, plan["id"], checks=["montage"], media=False)
    found = [f for f in lint["findings"] if f["check"] == "montage"]
    assert found and found[0]["severity"] == "warn" and found[0]["layer"] == "bw" and found[0]["t"] == 6.7
    # 1.7 с на панчлайн — норма; 3 с посреди клипа — подсказка.
    assert motion.fx_grayscale_long([{**bw, "start": 5.0, "end": 6.7}], 10.0) == []
    long_mid = motion.fx_grayscale_long([{**bw, "start": 2.0, "end": 5.0, "props": {"kind": "bw"}}], 10.0)
    assert long_mid and "дольше 2.5 с" in long_mid[0]
    assert motion.fx_grayscale_long([{**bw, "props": {"kind": "vhs"}}], 10.0) == []


def test_same_insert_twice_is_a_lint_warning(tmp_path, monkeypatch):
    from app.quality_lint import lint_clip

    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    rs = store.get_clip_plan(plan["id"])["render_settings"] or {}
    store.set_clip_plan_render_settings(plan["id"], {**rs, "inserts": [
        {"asset_id": 7, "at": 2.0, "duration": 1.5, "mode": "full"},
        {"asset_id": 7, "at": 6.0, "duration": 2.0, "mode": "full"},
        {"asset_id": 8, "at": 8.0, "duration": 1.0, "mode": "sound"}]})
    lint = lint_clip(store, plan["id"], checks=["montage"], media=False)
    repeat = [f for f in lint["findings"] if "одна и та же вставка" in f["message"]]
    assert len(repeat) == 1 and "дважды" in repeat[0]["message"] and repeat[0]["t"] == 6.0
    assert lint["by_check"]["montage"] == 1 and "монтаж 1" in lint["summary"]
