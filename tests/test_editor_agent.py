"""💬 ИИ-монтажёр: фейковая модель вызывает инструменты → слои в клипе, один шаг
истории, переписка сохранена и продолжается."""

from __future__ import annotations

import copy

import pytest

from app.ai import editor_agent
from test_api_pipeline import _require_ffmpeg
from test_clip_spec import _setup


def _call(name, args, call_id=None):
    part = {"functionCall": {"name": name, "args": args}, "thoughtSignature": "sig-" + name}
    if call_id:
        part["functionCall"]["id"] = call_id
    return part


class ScriptedGemini:
    """Отвечает заготовленными ходами и запоминает каждый запрос."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.requests = []

    def generate_content(self, model, payload):
        self.requests.append((model, copy.deepcopy(payload)))  # contents дальше дописываются
        parts = self.turns.pop(0)
        return {"candidates": [{"content": {"role": "model", "parts": parts}}]}


HOOK = {"type": "text", "start": 0.0, "end": 2.0, "x": 0.5, "y": 0.25,
        "props": {"text": "СМОТРИ ДО КОНЦА", "size": 96, "stroke": {"color": "#000000", "width": 10}}}


def _script():
    return ScriptedGemini([
        [{"text": "Ставлю хук и наезд."},
         _call("add_layers", {"layers": [HOOK]}, "c1"),
         _call("set_camera", {"keys": [{"t": 6, "zoom": 1}, {"t": 6.2, "zoom": 1.25, "ease": "outCubic"},
                                       {"t": 7.5, "zoom": 1.25}, {"t": 7.8, "zoom": 1}]}, "c2")],
        [_call("view_frame", {"t": 1.0})],
        [{"text": "Готово: хук «СМОТРИ ДО КОНЦА» на 0–2 с и наезд 1.25× на 6.2 с."}],
    ])


def test_agent_loop_applies_tools_sees_frame_and_reports(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    fake = _script()
    seen_steps = []

    out = editor_agent.run_agent(store, plan["id"], "сделай динамичнее", client=fake, model="gemini-test",
                                 on_step=seen_steps.append)

    assert out["text"].startswith("Готово")
    assert [s["tool"] for s in out["steps"]] == ["_say", "add_layers", "set_camera", "view_frame"]
    assert out["steps"][0]["label"] == "Ставлю хук и наезд."
    assert all(s["ok"] for s in out["steps"])
    assert out["steps"][1]["label"].startswith("Ставлю титр «СМОТРИ ДО КОНЦА» на 0.0 с")
    assert seen_steps and seen_steps[1][1]["ok"] is None  # шаг виден ещё до результата
    render = store.get_clip_plan(plan["id"])["render_settings"]
    assert render["layers"][0]["props"]["text"] == "СМОТРИ ДО КОНЦА"
    assert [k["zoom"] for k in render["camera"]] == [1, 1.25, 1.25, 1]
    assert store.count_clip_plan_history(plan["id"]) == 1

    first = fake.requests[0][1]
    assert fake.requests[0][0] == "gemini-test"
    names = {d["name"] for d in first["tools"][0]["functionDeclarations"]}
    assert {"add_layers", "view_frame", "set_camera"} <= names
    assert "СПРАВКА ПО СЛОЯМ" in first["systemInstruction"]["parts"][0]["text"]
    assert "get_clip" in first["contents"][-1]["parts"][0]["text"]  # состояние клипа приложено
    second = fake.requests[1][1]["contents"]
    model_turn = second[-2]
    assert model_turn["role"] == "model" and model_turn["parts"][1]["thoughtSignature"] == "sig-add_layers"
    responses = [p["functionResponse"] for p in second[-1]["parts"]]
    assert [r["name"] for r in responses] == ["add_layers", "set_camera"]
    assert responses[0]["id"] == "c1" and responses[0]["response"]["ok"] is True
    third = fake.requests[2][1]["contents"][-1]["parts"]
    assert any("inlineData" in p and p["inlineData"]["mimeType"].startswith("image/") for p in third)


def test_agent_fixes_itself_after_problems(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    fake = ScriptedGemini([
        [_call("add_layers", {"layers": [{**HOOK, "start": 3, "end": 1}]})],
        [_call("add_layers", {"layers": [HOOK]})],
        [{"text": "Исправил время и поставил титр."}],
    ])
    out = editor_agent.run_agent(store, plan["id"], "титр в начало", client=fake, model="m")
    assert [s["ok"] for s in out["steps"]] == [False, True]
    assert out["steps"][0]["problems"]
    sent = fake.requests[1][1]["contents"][-1]["parts"][0]["functionResponse"]["response"]
    assert sent["ok"] is False and sent["problems"]
    assert len(store.get_clip_plan(plan["id"])["render_settings"]["layers"]) == 1


def test_question_without_edits_leaves_history_alone(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    fake = ScriptedGemini([[{"text": "В клипе два куска по 5 с."}]])
    out = editor_agent.run_agent(store, plan["id"], "что тут?", client=fake, model="m")
    assert out["changed"] is False and out["steps"] == []
    assert store.count_clip_plan_history(plan["id"]) == 0


def test_step_limit_forces_a_final_report(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    fake = ScriptedGemini([
        [_call("get_clip", {})],
        [_call("get_clip", {})],
        [{"text": "Успел только посмотреть клип."}],
    ])
    out = editor_agent.run_agent(store, plan["id"], "сделай всё", client=fake, model="m", max_steps=2)
    assert out["text"] == "Успел только посмотреть клип."
    last = fake.requests[-1][1]
    assert "tools" not in last and "Лимит шагов" in last["contents"][-1]["parts"][0]["text"]


def test_cancel_stops_the_loop(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    with pytest.raises(editor_agent.AgentCancelled):
        editor_agent.run_agent(store, plan["id"], "x", client=_script(), model="m", should_cancel=lambda: True)


def test_chat_api_runs_in_background_persists_and_continues(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    fake = _script()
    monkeypatch.setattr(editor_agent, "client_factory", lambda: fake)
    url = f"/api/clip-plans/{plan['id']}/agent"

    started = client.post(url, json={"message": "сделай динамичнее"}).raise_for_status().json()
    assert started["run_id"] and started["message_id"]
    assert editor_agent.wait_run(plan["id"], timeout=120)

    chat = client.get(url).raise_for_status().json()
    assert chat["running"] is False
    assert [m["role"] for m in chat["messages"]] == ["user", "assistant"]
    reply = chat["messages"][1]
    assert reply["status"] == "done" and reply["text"].startswith("Готово")
    assert [s["tool"] for s in reply["steps"]] == ["_say", "add_layers", "set_camera", "view_frame"]
    assert all({"tool", "label", "ok"} <= set(s) for s in reply["steps"])
    undo = client.get(f"/api/clip-plans/{plan['id']}/undo").json()
    assert undo["left"] == 1

    # Второй вопрос: модель получает прошлую переписку.
    follow = ScriptedGemini([[{"text": "Хук стоит на 0–2 с."}]])
    monkeypatch.setattr(editor_agent, "client_factory", lambda: follow)
    client.post(url, json={"message": "где хук?"}).raise_for_status()
    assert editor_agent.wait_run(plan["id"], timeout=60)
    contents = follow.requests[0][1]["contents"]
    assert contents[0]["role"] == "user" and "сделай динамичнее" in contents[0]["parts"][0]["text"]
    assert contents[1]["role"] == "model" and "Готово" in contents[1]["parts"][0]["text"]
    assert len(client.get(url).json()["messages"]) == 4

    assert client.post(url + "/cancel").json() == {"cancelling": False}
    assert client.delete(url).json()["deleted"] == 4
    assert client.get(url).json()["messages"] == []

    tools = client.get("/api/agent/tools").raise_for_status().json()
    assert any(t["name"] == "add_layers" and t["mutating"] for t in tools["tools"])


def test_chat_api_errors(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, _store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(editor_agent, "client_factory", lambda: ScriptedGemini([]))
    assert client.post("/api/clip-plans/999999/agent", json={"message": "x"}).status_code == 404
    assert client.post(f"/api/clip-plans/{plan['id']}/agent", json={"message": ""}).status_code == 422
    # Модель упала → ошибка видна в чате, сервер жив.
    client.post(f"/api/clip-plans/{plan['id']}/agent", json={"message": "x"}).raise_for_status()
    assert editor_agent.wait_run(plan["id"], timeout=60)
    reply = client.get(f"/api/clip-plans/{plan['id']}/agent").json()["messages"][-1]
    assert reply["status"] == "error" and reply["text"].startswith("Ошибка")


def test_content_filter_mid_run_keeps_work_and_reports(tmp_path, monkeypatch):
    """Фильтр Gemini посреди работы: сделанное остаётся, ответ — понятный отчёт, не падение."""
    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    replies = iter([
        {"candidates": [{"content": {"parts": [{"functionCall": {"name": "add_layers", "args": {"layers": [
            {"type": "text", "start": 0, "end": 2, "x": 0.5, "y": 0.13, "props": {"text": "ХУК"}}]}}}]}}]},
        {"promptFeedback": {"blockReason": "PROHIBITED_CONTENT"}},
    ])

    class Fake:
        def generate_content(self, model, payload):
            return next(replies)

    out = editor_agent.run_agent(store, plan["id"], "смонтируй", client=Fake(), model="m")
    assert "фильтр" in out["text"] and out["changed"]
    assert store.get_clip_plan(plan["id"])["render_settings"]["layers"]


def test_degenerate_model_text_is_detected():
    assert editor_agent.degenerate("Готово. " + "- " * 200)
    assert editor_agent.degenerate('{"id": "c+oGNRzQ==", "type": "tag", "time": "2024-11-20"}' * 20)
    assert not editor_agent.degenerate("Готово! Поставил хук «МУХА ПИЛОТИРУЕТ», два панчлайна и гифку на 3 с.")


def test_report_like_rejects_english_reasoning_and_tool_echo():
    assert editor_agent.report_like("Готово:\n1. Хук «ЕЁ НАПУГАЛ СКРИМЕР» на 0 с.\n2. Панчлайн на 17.9 с.")
    assert not editor_agent.report_like("```\n\nLet's review the frames.\n- Contact sheet shows good distribution.")
    assert not editor_agent.report_like('1.0 с, слои:\n  captions "Субтитры" (track 0) — x: 0.5, y: 0.37\nКамера: zoom 1.0')


def test_english_reasoning_instead_of_report_gets_one_rewrite_without_tools(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    fake = ScriptedGemini([
        [{"text": "```\nLet's review the frames. Contact sheet shows good distribution."}],
        [{"text": "Готово: посмотрел клип, правок не понадобилось."}],
    ])
    out = editor_agent.run_agent(store, plan["id"], "что тут?", client=fake, model="m")
    assert out["text"].startswith("Готово")
    assert "tools" not in fake.requests[-1][1]
