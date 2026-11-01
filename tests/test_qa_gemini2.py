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
