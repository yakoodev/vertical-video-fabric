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
        [_call("view_frame", {"t": 1.0})],
        [{"text": "Исправил время и поставил титр."}],
    ])
    out = editor_agent.run_agent(store, plan["id"], "титр в начало", client=fake, model="m")
    assert [s["ok"] for s in out["steps"]] == [False, True, True]
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


def test_empty_reply_mid_work_is_nudged_to_continue(tmp_path, monkeypatch):
    """Проверочный прогон: Gemini замолчал после листа кадров — ход не должен «закончиться»."""
    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    fake = ScriptedGemini([
        [_call("get_clip", {})],
        [],  # пустой ответ посреди работы
        [_call("add_layers", {"layers": [HOOK]})],
        [_call("view_frame", {"t": 1.0})],
        [{"text": "Готово: хук на 0–2 с."}],
    ])
    out = editor_agent.run_agent(store, plan["id"], "смонтируй", client=fake, model="m")
    assert out["text"] == "Готово: хук на 0–2 с."
    assert [s["tool"] for s in out["steps"]] == ["get_clip", "add_layers", "view_frame"]
    assert "Ответ пустой" in fake.requests[2][1]["contents"][-1]["parts"][0]["text"]


def test_reply_leaks_are_stripped_and_auto_focus_is_offered():
    from app import agent_tools

    assert editor_agent._clean_reply("-0.5}Готово! Всё") == "Готово! Всё"
    assert editor_agent._clean_reply("strueПроверяю концовку") == "Проверяю концовку"
    assert editor_agent._clean_reply(". . . (изображение не показано) . . .Применяю") == "Применяю"
    assert editor_agent._clean_reply("Хук «ЕЁ» на 2 с") == "Хук «ЕЁ» на 2 с"
    # Встроенный монтажёр видит auto_focus (раньше он лежал среди инструментов поиска и был скрыт).
    assert "auto_focus" in {t.name for t in agent_tools.list_tools()}


def test_english_monologue_before_russian_report_is_cut():
    # QA 02.11 (клип 895): рассуждение Gemini пришло обычным текстом, без пометки thought.
    leaked = ("herence}\n\nThe frame looks nice, avatar is zoomed in, the nameplate is in the top right, subtitles "
              "look great.\n\nEverything is according to the vtuber-story playbook.\n\nI'll write the final report."
              "Готово! Клип смонтирован по плейбуку «История витубера».\n\n1. **Кадрирование**: только аватар "
              "(`set_layout avatar_full`).")
    out = editor_agent._clean_reply(leaked)
    assert out.startswith("Готово! Клип смонтирован") and "frame looks" not in out and "set_layout" in out
    assert editor_agent._parts_text([{"text": leaked}]).startswith("herence")  # части модели не трогаем
    assert editor_agent.strip_monologue("Checking the frame now, it looks fine. **Готово**: хук на 0–2 с, плашка "
                                        "с 8 с.") == "**Готово**: хук на 0–2 с, плашка с 8 с."
    # Нормальные ответы — как есть: короткая латиница, термин, название песни, сплошь английский.
    for keep in ("OK, сделал: хук на 0–2 с и плашка в углу с 8 с.",
                 "`set_layout avatar_full` поставил рамку аватара на весь клип, проверил кадры.",
                 "Virtual Insanity by Jamiroquai — это песня на экране, вырезал её из кадра целиком.",
                 "Mura is on screen the whole clip and the hook is at the top. Хук"):
        assert editor_agent.strip_monologue(keep) == keep


def test_thinking_aloud_mid_work_continues(tmp_path, monkeypatch):
    """Проверочный прогон: «рассуждения» текстом на 9-м шаге из 40 переводили ход в отчёт без
    инструментов, и Gemini писал «не успел из-за лимита шагов»."""
    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    fake = ScriptedGemini([
        [_call("get_clip", {})],
        [{"text": "Now I will trim the clip and add a hook."}],  # не отчёт, не по-русски
        [_call("add_layers", {"layers": [HOOK]})],
        [_call("view_frame", {"t": 1.0})],
        [{"text": "Готово: хук на 0–2 с."}],
    ])
    out = editor_agent.run_agent(store, plan["id"], "смонтируй", client=fake, model="m")
    assert out["text"] == "Готово: хук на 0–2 с."
    assert [s["tool"] for s in out["steps"]] == ["get_clip", "add_layers", "view_frame"]
    nudge = fake.requests[2][1]
    assert "tools" in nudge and "Продолжай работу инструментами" in nudge["contents"][-1]["parts"][0]["text"]


# ------------------------------------------------- прогон 578: ✗ без повтора, ход без отчёта

BROKEN = {**HOOK, "start": 3, "end": 1}


def _last_text(request):
    return request[1]["contents"][-1]["parts"][0]["text"]


def test_failed_call_before_report_is_pushed_once_to_retry(tmp_path, monkeypatch):
    """Прогон 578: пачка финала ✗, агент сделал ещё два шага и закончил — финал потерян."""
    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    fake = ScriptedGemini([
        [_call("add_layers", {"layers": [BROKEN]})],
        [_call("view_frame", {"t": 1.0})],
        [{"text": "Готово: финал на 20 с."}],  # хочет закончить, а ✗ так и не исправлен
        [_call("add_layers", {"layers": [HOOK]})],
        [_call("view_frame", {"t": 1.0})],
        [{"text": "Готово: хук на 0–2 с."}],
    ])
    out = editor_agent.run_agent(store, plan["id"], "смонтируй", client=fake, model="m")
    assert out["text"] == "Готово: хук на 0–2 с."
    assert [s["ok"] for s in out["steps"]] == [False, True, True, True]
    nudge = fake.requests[3]
    assert "tools" in nudge[1]
    assert _last_text(nudge).startswith("Последний вызов не прошёл: ") and "Исправь и повтори" in _last_text(nudge)
    assert "слои не приняты" in _last_text(nudge)


def test_retry_nudge_is_one_time(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    fake = ScriptedGemini([
        [_call("add_layers", {"layers": [BROKEN]})],
        [{"text": "Готово: не смог поставить титр."}],
        [{"text": "Готово: титр не встал — время конца раньше начала."}],
    ])
    out = editor_agent.run_agent(store, plan["id"], "смонтируй", client=fake, model="m")
    assert out["text"] == "Готово: титр не встал — время конца раньше начала."
    assert len(fake.requests) == 3 and not fake.turns


def test_gap_warnings_push_to_fix_before_report(tmp_path, monkeypatch):
    """«пустой кадр»/«нет финала» в ответе линта, а модель заканчивает — один раз дожимаем;
    проверка — по текущему клипу (исправил — не дожимаем)."""
    from app import agent_tools

    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    gap = "пустой кадр 2.0–10.0 с: нет слоёв — проверь, применились ли последние пачки"
    monkeypatch.setattr(agent_tools, "_scene_warnings",
                        lambda ctx, render, duration: [gap] if len(render.get("layers") or []) < 2 else [])
    fake = ScriptedGemini([
        [_call("add_layers", {"layers": [HOOK]})],
        [{"text": "Готово: хук на 0–2 с."}],
        [_call("add_layers", {"layers": [{**HOOK, "start": 2, "end": 10}]})],
        [_call("view_frame", {"t": 1.0})],
        [{"text": "Готово: хук и титр до конца."}],
    ])
    out = editor_agent.run_agent(store, plan["id"], "смонтируй", client=fake, model="m")
    assert out["text"] == "Готово: хук и титр до конца."
    assert out["steps"][0]["warnings"] == [gap]
    nudge = _last_text(fake.requests[2])
    assert "get_clip предупреждает: " + gap in nudge and "Последний вызов" not in nudge
    assert len(fake.requests) == 5


def test_steps_without_report_get_one_report_request(tmp_path, monkeypatch):
    """Ход кончился одними шагами (модель молчит) — один раз просим отчёт без инструментов."""
    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    fake = ScriptedGemini([
        [_call("add_layers", {"layers": [HOOK]})],
        [], [], [],  # два подталкивания «Ответ пустой» и снова тишина
        [{"text": "Готово: хук на 0–2 с."}],
    ])
    out = editor_agent.run_agent(store, plan["id"], "смонтируй", client=fake, model="m")
    assert out["text"] == "Готово: хук на 0–2 с."
    last = fake.requests[-1]
    assert "tools" not in last[1] and "Ты не написал отчёт" in _last_text(last)
    # и снова молчит — отчёт из шагов, без новых запросов
    fake = ScriptedGemini([[_call("add_layers", {"layers": [HOOK]})], [], [], [], []])
    out = editor_agent.run_agent(store, plan["id"], "смонтируй", client=fake, model="m")
    assert out["text"].startswith("Сделал:") and not fake.turns


def test_unresolved_failure_and_partial_batches():
    ok = {"tool": "add_layers", "label": "Слои", "ok": True}
    bad = {"tool": "add_layers", "label": "Финал", "ok": False, "error": "слои не приняты",
           "problems": ["layers[8]: sfx:freesound/success.ogg не найден в пакетах"]}
    frame = {"tool": "view_frame", "label": "Кадр", "ok": True}
    say = {"tool": "_say", "label": "Ставлю финал", "ok": True}
    assert editor_agent.unresolved_failure([bad, frame, say]) is bad  # ✗ правки, дальше не исправлен
    assert editor_agent.unresolved_failure([bad, ok, frame]) is None  # повторил — прошло
    failed_frame = {**frame, "ok": False, "error": "нет кадра"}
    assert editor_agent.unresolved_failure([ok, failed_frame]) is failed_frame  # последний вызов ✗
    assert editor_agent.unresolved_failure([failed_frame, ok]) is None  # чтение не правка — не держим
    partial = {**ok, "partial": True, "applied": "применено 8 из 9", "problems": bad["problems"]}
    nudge = editor_agent.fix_nudge([partial, frame], [])
    assert "применено 8 из 9" in nudge and "success.ogg" in nudge
    assert editor_agent.fix_nudge([ok, frame], []) == ""


# ------------------------------------------------- QA 01.11: проверка глазами и честный отчёт


def test_report_without_looking_is_nudged_then_service_takes_contact_sheet(tmp_path, monkeypatch):
    """QA 01.11 (клип 889): 36 шагов, ни одного view_frame/contact_sheet — весь ролик оказался ютуб-клипом.
    Хочет отчитаться без взгляда — напоминание; снова без взгляда — лист кадров снимает сервис."""
    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    fake = ScriptedGemini([
        [_call("add_layers", {"layers": [HOOK]})],
        [{"text": "Готово: хук на 0–2 с."}],
        [{"text": "Готово: хук на 0–2 с, всё на месте."}],
        [{"text": "Посмотрел лист кадров: хук на 0–2 с читается."}],
    ])
    out = editor_agent.run_agent(store, plan["id"], "поставь хук", client=fake, model="m")
    assert out["text"] == "Посмотрел лист кадров: хук на 0–2 с читается."
    assert "view_frame, ни contact_sheet" in _last_text(fake.requests[2]) and "tools" in fake.requests[2][1]
    auto = [s for s in out["steps"] if s.get("auto")]
    assert len(auto) == 1 and auto[0]["tool"] == "contact_sheet" and auto[0]["ok"]
    last = fake.requests[3][1]["contents"][-1]["parts"]
    assert "сервис снял его сам" in last[0]["text"] and last[1]["inlineData"]["mimeType"].startswith("image/")
    assert "ПРОВЕРКА ГЛАЗАМИ" in fake.requests[0][1]["systemInstruction"]["parts"][0]["text"]


def test_avatar_in_several_places_needs_view_frame_in_each(tmp_path, monkeypatch):
    """QA 02.11 (клип 892): аватар в двух местах, агент снял один contact_sheet и сдал ролик с 4 с волос без
    лица. Отчёт принимается только после view_frame в каждом отрезке места; не посмотрел — снимает сервис."""
    _require_ffmpeg()
    _client, store, source, plan, a, _b = _setup(tmp_path, monkeypatch)
    plan = store.create_clip_plan(source["id"], plan["analysis_id"], "Один кусок", segment_ids=[a["id"]])
    left = {"x": 0.1, "y": 0.0, "w": 0.45, "h": 1.0}
    right = {"x": 0.7, "y": 0.4, "w": 0.27, "h": 0.57}
    rs = store.get_clip_plan(plan["id"])["render_settings"] or {}
    store.set_clip_plan_render_settings(plan["id"], {**rs, "layout": {
        "mode": "avatar_full", "cam": right, "cams": [{"from": 0, "to": 2, "cam": left}, {"from": 2, "to": 5, "cam": right}]}})
    fake = ScriptedGemini([
        [_call("set_layout", {"mode": "avatar_full", "avatar_y": 0.5})],
        [_call("contact_sheet", {})],
        [{"text": "Готово: аватар на весь кадр."}],
        [_call("view_frame", {"t": 1.0})],
        [{"text": "Готово: аватар на весь кадр, посмотрел 1 с."}],
        [{"text": "Посмотрел оба места: аватар в кадре."}],
    ])
    out = editor_agent.run_agent(store, plan["id"], "только аватар", client=fake, model="m")
    assert out["text"] == "Посмотрел оба места: аватар в кадре."
    nudge = _last_text(fake.requests[3])
    assert "в КАЖДОМ отрезке" in nudge and "0.0–2.0 с" in nudge and "2.0–5.0 с" in nudge
    # Посмотрел только первое место — второе снял сервис.
    auto = [s for s in out["steps"] if s.get("auto")]
    assert len(auto) == 1 and auto[0]["tool"] == "view_frame" and auto[0]["ok"] and 2.0 <= auto[0]["frame_t"] <= 5.0
    last = fake.requests[5][1]["contents"][-1]["parts"]
    assert "сервис снял их сам" in last[0]["text"] and any("inlineData" in p for p in last)
    # Одно место — проверка не мешает.
    single = [{"tool": "set_layout", "ok": True}]
    store.set_clip_plan_render_settings(plan["id"], {**rs, "layout": {"mode": "avatar_full", "cam": right}})
    from app.agent_tools import ToolContext

    assert editor_agent.unviewed_avatar_places(ToolContext(store=store, clip_plan_id=plan["id"]), single) == []


def test_looking_after_the_change_is_enough_and_reads_do_not_count():
    add = {"tool": "add_layers", "label": "Слои", "ok": True}
    focus = {"tool": "set_focus", "label": "Кадр", "ok": True}
    frame = {"tool": "view_frame", "label": "Кадр 1", "ok": True}
    sheet = {"tool": "contact_sheet", "label": "Лист", "ok": True}
    get = {"tool": "get_clip", "label": "Клип", "ok": True}
    assert editor_agent.unchecked_visual([add, get]) is add
    assert editor_agent.unchecked_visual([add, frame]) is None
    assert editor_agent.unchecked_visual([add, sheet, focus]) is focus
    assert editor_agent.unchecked_visual([{**add, "ok": False}, get]) is None  # не прошла — кадр не менялся
    assert editor_agent.unchecked_visual([add, {**frame, "ok": False}]) is add


def test_each_change_carries_fresh_clip_check(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    fake = _script()
    editor_agent.run_agent(store, plan["id"], "сделай динамичнее", client=fake, model="m")
    responses = [p["functionResponse"] for p in fake.requests[1][1]["contents"][-1]["parts"]]
    assert "clip_check" not in responses[0]["response"]  # чек — у последней правки шага
    check = responses[1]["response"]["clip_check"]
    assert "lint_warnings" in check and "get_clip_warnings" in check and "lint пройден" in check["note"]
    assert "ЧЕСТНЫЙ ОТЧЁТ" in fake.requests[0][1]["systemInstruction"]["parts"][0]["text"]


def test_honest_report_corrects_lint_and_insert_claims():
    check = {"lint_warnings": 10, "lint": ["субтитры: «что я» видна 0.28 с → короткая страница"],
             "inserts": [{"kind": "image", "at": 2.09, "end": 4.09, "what": "слой image insert_1"},
                         {"kind": "image", "at": 23.48, "end": 25.98, "what": "слой image insert_3"}]}
    lie = "3. На слове «клипе» (23.48 с) — гифка с танцующим тараканом.\nПроверка `lint_clip` пройдена."
    fixed = editor_agent.honest_report(lie, check)
    assert fixed.startswith(lie) and "Сверка сервиса с клипом" in fixed
    assert "предупреждений 10" in fixed and "«что я» видна 0.28 с" in fixed
    assert "гифок в клипе нет" in fixed and "картинка 23.48–25.98 с" in fixed
    honest = "Хук на 0–2 с. Осталось 10 предупреждений lint по темпу чтения."
    assert editor_agent.honest_report(honest, check) == honest
    assert editor_agent.honest_report("Ошибок нет, без предупреждений.", {**check, "lint_warnings": 0}) == \
        "Ошибок нет, без предупреждений."
    gif = {**check, "inserts": [{"kind": "gif", "at": 1, "end": 2, "what": "вставка pip #0"}]}
    assert "гифок" not in editor_agent.honest_report("Гифка на 1 с.", gif)


def test_final_report_is_checked_against_the_clip(tmp_path, monkeypatch):
    from app import quality_lint as Q

    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    real = Q.lint_clip

    def lint_with_warning(*a, **k):
        out = real(*a, **k)
        out["findings"] = out["findings"] + [{"check": "reading", "severity": "warn", "message": "титр мелькает"}]
        return out

    monkeypatch.setattr(Q, "lint_clip", lint_with_warning)
    fake = ScriptedGemini([
        [_call("add_layers", {"layers": [HOOK]})],
        [_call("view_frame", {"t": 1.0})],
        [{"text": "Готово: хук на 0–2 с. lint_clip пройден, без предупреждений."}],
    ])
    out = editor_agent.run_agent(store, plan["id"], "поставь хук", client=fake, model="m")
    assert out["text"].startswith("Готово: хук на 0–2 с.") and "Сверка сервиса с клипом" in out["text"]
    assert "титр мелькает" in out["text"]
