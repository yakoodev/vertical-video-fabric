"""Эксперимент 12.11: ответы чата без утечки английского размышления и без ложного «отрендерено»; список моделей чата из
настроек; краткий бриф проекта; укороченные описания в MCP tools/list и describe_tool."""

from __future__ import annotations

from app import agent_api, agent_tools
from app import projects as P
from app.agent_tools import ToolContext, run_tool
from app.ai.editor_agent_review import _clean_reply, strip_latin_lead
from app.ai.render_claims import claims_render, db_now, render_note, with_render_note
from test_data_foundation import _store

B1_LEAK = (", time: `clip`. Note: The frame has text layout issues but generally readable.\n\n"
           "Looks like everything is assembled successfully!\nOne more thing: The prompt asked "
           "\"Результат — отрендеренный клип в сервисе\".\nWait, `create_canvas_clip` is a clip setup. But I don't have "
           "a `render` command!\nLet's double check:\n- Project 9? Yes.\nI will report to the user:\n1. Created a new "
           "canvas clip (35s).\n7. Added upbeat background music starting at its drop.Я собрал рекламный ролик для "
           "TikTok точно по твоему заданию (клип #1065, длина 35 секунд). Бюджет на озвучку составил 0 ₽.\n\n"
           "**Что сделано по секундам:**\n* **0.0 – 35.0 с:** Создал новый вертикальный холст с фоном «аврора».")


def test_english_reasoning_with_russian_quotes_is_cut():
    out = strip_latin_lead(B1_LEAK)
    assert out.startswith("Я собрал рекламный ролик") and "Wait" not in out and "**Что сделано" in out
    assert _clean_reply(B1_LEAK).startswith("Я собрал")


def test_normal_answers_are_untouched():
    for text in ("Готово. Поставил хук на 1.2 с и наезд на 4 с.\nДальше — субтитры.",
                 "OK, сделал: хук на 1.2 с, наезд на 4 с, субтитры по словам.",
                 "`set_layout` поставил вебку сверху. Игра снизу, субтитры по центру.",
                 "Virtual Insanity — это песня Jamiroquai. Нарезал припев на 15 с, поставил титр.",
                 "Done."):
        assert strip_latin_lead(text) == text


def test_render_claims():
    assert claims_render("Клип уже отрендерен на 45 секунд, готов для залива в TikTok.")
    assert claims_render("Готовый ролик лежит в сервисе.")
    assert claims_render("Клип готов к публикации.")
    assert not claims_render("Поставил хук и наезд. Что осталось: запустить финальный рендер клипа #1071.")
    assert not claims_render("Клип не отрендерен — запусти render_clip.")
    assert not claims_render("Рендера нет, клип собран в редакторе.")


def _plan(store):
    source = store.create_source("upload", "s.mp4", original_filename="s.mp4", duration_sec=60, status="ready")
    analysis = store.create_ai_analysis(source["id"], "manual", status="succeeded")
    segment = store.create_ai_segment(analysis["id"], {"start_sec": 0, "end_sec": 10, "title": "м"})
    return store.create_clip_plan(source["id"], analysis["id"], "Клип", segment_ids=[segment["id"]])


def test_render_note_without_render_and_with_fresh_render(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    plan = _plan(store)
    steps = [{"tool": "add_layers", "kind": "tool", "ok": True, "clip_id": plan["id"]}]
    claim = "Клип уже отрендерен на 45 секунд, готов для залива."
    since = db_now(store)
    note = render_note(store, claim, steps, since)
    assert "рендера нет" in note and f"#{plan['id']}" in note and "render_clip" in note
    assert with_render_note(store, claim, steps, since).endswith(note)
    # Честный ответ — без пометки.
    assert render_note(store, "Поставил хук. Осталось отрендерить: render_clip.", steps, since) == ""
    # Ход без клипов — пометке не к чему.
    assert render_note(store, claim, [], since) == ""
    # Готовый рендер после начала хода — пометки нет.
    clip = store.create_clip(plan["source_id"], clip_plan_id=plan["id"], title="Клип", status="succeeded")
    assert render_note(store, claim, steps, since) == ""
    # Рендер старше начала хода, а клип в ходе правили — пометка; только смотрел — старый рендер годится.
    store.db.execute("UPDATE clips SET created_at = '2000-01-01 00:00:00' WHERE id = ?", (clip["id"],))
    assert "рендера нет" in render_note(store, claim, steps, since)
    assert render_note(store, claim, [{"tool": "get_clip", "kind": "tool", "ok": True, "clip_id": plan["id"]}],
                       since) == ""


def test_thread_policy_appends_the_note(tmp_path, monkeypatch):
    from app.ai.agent_loop import TurnState
    from app.project_chat import ThreadPolicy

    store = _store(tmp_path, monkeypatch)
    plan = _plan(store)
    policy = ThreadPolicy(store, {"id": 1, "project_id": None}, [], "сделай ролик", None, "")
    state = TurnState(contents=[], emit=lambda *a: None)
    state.steps = [{"tool": "create_canvas_clip", "kind": "tool", "ok": True, "preview": {"kind": "clip",
                                                                                        "clip_plan_id": plan["id"]}}]
    text = policy.finish(state, "Готово! Клип отрендерен и готов к публикации.")
    assert "⚠ Сервис: рендера нет" in text


def test_chat_models_come_from_settings(monkeypatch):
    from app.project_chat_threads import models
    from app.settings import settings

    ids = [m["id"] for m in models()["models"]]
    assert "gemini-3.8-flash" in ids and "gemini-3.1-pro-preview" in ids
    monkeypatch.setattr(settings, "gemini_chat_models", ["gemini-9-ultra=самая новая", "gemini-3.8-flash"])
    got = models()["models"]
    assert got[0] == {"id": "gemini-9-ultra", "label": "gemini-9-ultra", "note": "самая новая", "provider": "gemini"}
    assert settings.gemini_agent_model in [m["id"] for m in got]


def test_project_brief_is_compact_by_default(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    source = store.create_source("upload", "s.mp4", original_filename="Стрим", duration_sec=3600, status="ready")
    project = P.create_project(store, "Мура", source_ids=[source["id"]])
    analysis = store.create_ai_analysis(source["id"], "manual", status="succeeded")
    for n in range(30):
        seg = store.create_ai_segment(analysis["id"], {"start_sec": n * 60, "end_sec": n * 60 + 30, "title": "м"})
        store.create_clip_plan(source["id"], analysis["id"], f"Клип {n}", segment_ids=[seg["id"]])
    short, ok = run_tool(ToolContext(store=store), "get_project_brief", {"project_id": project["id"]}, validate=True)
    assert ok, short
    assert len(short["covered"]) == 20 and short["covered_total"] == 30
    assert short["covered_by_source"][0]["clips"] == 30 and len(short["covered_by_source"][0]["spans"]) == 30
    assert "full=true" in short["covered_note"]
    page, ok = run_tool(ToolContext(store=store), "get_project_brief",
                        {"project_id": project["id"], "covered_offset": 20}, validate=True)
    assert len(page["covered"]) == 10 and page["covered"][0]["title"] == "Клип 9"
    full, ok = run_tool(ToolContext(store=store), "get_project_brief", {"project_id": project["id"], "full": True},
                        validate=True)
    assert ok and len(full["covered"]) == 30 and "covered_by_source" not in full


def test_mcp_tools_list_is_complete_with_short_descriptions():
    tools = agent_api.mcp_tool_list()
    names = {t["name"] for t in tools}
    assert names == {t.name for t in agent_tools.list_tools(include_discovery=True)}
    assert "describe_tool" in names
    full = {t.name: t.description for t in agent_tools.list_tools(include_discovery=True)}
    for tool in tools:
        text = full[tool["name"]]
        if tool["description"] != text:
            assert f'describe_tool(name="{tool["name"]}")' in tool["description"], tool["name"]
            assert len(tool["description"]) < len(text) - 100
        else:
            assert len(text) <= agent_api.MCP_DESCRIPTION_LIMIT + 400, tool["name"]  # длинное — укорочено
    assert sum(len(t["description"]) for t in tools) < 0.85 * sum(len(v) for v in full.values())
    tts = next(t for t in tools if t["name"] == "tts_preview")
    assert "ПЛАТНО" in tts["description"]  # деньги не отрезаются


def test_describe_tool_gives_the_full_text():
    out, ok = run_tool(ToolContext(store=None), "describe_tool", {"name": "censor_profanity"}, validate=True)
    assert ok and out["description"] == agent_tools.get_tool("censor_profanity").description
    assert out["parameters"] == agent_tools.get_tool("censor_profanity").parameters
    out, ok = run_tool(ToolContext(store=None), "describe_tool", {"name": "censor_profanty"}, validate=True)
    assert not ok and "censor_profanity" in out["error"]
