"""Страница «Чат»: переписки, поток ответа (SSE), шаги инструментов и группы, подтверждение
опасного, откат правок клипа, хранение и резюме, честный отказ без Gemini. Провайдер замокан."""

from __future__ import annotations

import copy
import sqlite3
import threading

import pytest

from app import chat_tools as CT
from app import project_chat as C
from app import projects as P
from app.settings import settings
from test_api_pipeline import _client


@pytest.fixture
def env(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    monkeypatch.setattr("app.ai.editor_agent.client_factory", None)
    source = store.create_source("upload", settings.source_dir / "mura.mp4", original_filename="mura.mp4",
                                 duration_sec=3600, status="ready")
    pid = P.create_project(store, "Мура", kind="stream_channel", source_ids=[source["id"]])["id"]
    return client, store, source, pid


def _plan(store, source_id, title="Мура пугается"):
    analysis = store.create_ai_analysis(source_id, "manual", status="succeeded")
    seg = store.create_ai_segment(analysis["id"], {"start_sec": 10, "end_sec": 25, "title": title})
    return store.create_clip_plan(source_id, analysis["id"], title, segment_ids=[seg["id"]])


def _reply(parts):
    return {"candidates": [{"content": {"role": "model", "parts": parts}}]}


class Scripted:
    """Без потока: ответ целиком (как старый клиент) — UI показывает «думает…»."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.requests = []

    def generate_content(self, model, payload):
        self.requests.append(copy.deepcopy(payload))
        return _reply(self.turns.pop(0) if self.turns else [{"text": "ок"}])


class Streaming(Scripted):
    """Поток: каждый ход — список чанков (списков частей); gate держит ход, пока тест не отпустит."""

    def __init__(self, turns, gate: threading.Event | None = None):
        super().__init__(turns)
        self.gate = gate

    def generate_content(self, model, payload):  # pragma: no cover - поток не должен падать в обычный запрос
        raise AssertionError("ожидался поток")

    def stream_generate_content(self, model, payload):
        self.requests.append(copy.deepcopy(payload))
        chunks = self.turns.pop(0) if self.turns else [[{"text": "ок"}]]
        for index, parts in enumerate(chunks):
            if index == 1 and self.gate is not None:
                self.gate.wait(10)
            yield _reply(parts)


def _names(request):
    return {d["name"] for d in request["tools"][0]["functionDeclarations"]}


def test_threads_api_and_general_chat(env):
    client, store, _source, pid = env
    general = client.post("/api/chat/threads", json={}).json()
    assert general["project_id"] is None and general["title"] == "Новый чат"
    project_thread = client.post("/api/chat/threads", json={"project_id": pid}).json()
    gemini = Scripted([[{"text": "Привет! Есть **проект** Мура."}]])
    C.send_to_thread(store, general["id"], "что у меня есть?", client=gemini)
    assert C.wait_thread(general["id"], 10)
    view = client.get(f"/api/chat/threads/{general['id']}").json()
    assert [m["role"] for m in view["messages"]] == ["user", "assistant"]
    assert view["messages"][-1]["text"].startswith("Привет!") and view["thread"]["title"] == "что у меня есть?"
    context = gemini.requests[0]["contents"][-1]["parts"][0]["text"]
    assert "channel_projects" in context and "Мура" in context  # общий чат видит проекты и источники
    listed = client.get("/api/chat/threads").json()
    assert [t["id"] for t in listed["general"]] == [general["id"]]
    mura = next(p for p in listed["projects"] if p["id"] == pid)
    assert [t["id"] for t in mura["threads"]] == [project_thread["id"]]
    # Основная переписка проекта — та же, что во вкладке «Чат» серии.
    assert client.get(f"/api/projects/{pid}/chat").json()["thread_id"] == C.default_thread(store, pid)["id"]
    assert client.patch(f"/api/chat/threads/{general['id']}", json={"title": "Общий"}).json()["title"] == "Общий"
    assert client.patch(f"/api/chat/threads/{general['id']}", json={"model": "нет-такой"}).status_code == 400
    assert client.delete(f"/api/chat/threads/{general['id']}").json() == {"deleted": general["id"]}
    assert client.get(f"/api/chat/threads/{general['id']}").status_code == 404


def test_stream_text_live_and_sse(env):
    client, store, _source, pid = env
    gate = threading.Event()
    thread = C.default_thread(store, pid)
    gemini = Streaming([[[{"text": "Смотрю "}], [{"text": "расписание"}], [{"text": " — пусто."}]]], gate=gate)
    C.send(store, pid, "что в расписании?", client=gemini)
    run = C.active_run(thread["id"])
    assert run is not None
    for _ in range(200):
        if run.snapshot()["text"]:
            break
        threading.Event().wait(0.01)
    assert run.snapshot()["text"] == "Смотрю "  # первый кусок уже виден, ход ещё идёт
    live = client.get(f"/api/chat/threads/{thread['id']}").json()
    assert live["running"] and live["live"]["text"] == "Смотрю "
    gate.set()
    with client.stream("GET", f"/api/chat/threads/{thread['id']}/stream") as response:
        body = "".join(response.iter_text())
    assert "event: done" in body
    assert C.wait(pid, 10)
    reply = C.list_messages(store, pid)[-1]
    assert reply["status"] == "done" and reply["text"] == "Смотрю расписание — пусто."


def test_tool_steps_groups_and_previews(env):
    client, store, source, pid = env
    plan = _plan(store, source["id"])
    gemini = Scripted([
        [{"text": "Гляну клип."}, {"functionCall": {"name": "get_clip", "args": {"clip_id": plan["id"]}}}],
        [{"functionCall": {"name": "load_tools", "args": {"groups": ["montage"]}}}],
        [{"text": "Клип #%d, 12.5 с — хук слабый." % plan["id"]}],
    ])
    C.send(store, pid, "посмотри последний клип", client=gemini)
    assert C.wait(pid, 10)
    reply = C.list_messages(store, pid)[-1]
    tools = [s for s in reply["steps"] if s.get("kind") == "tool"]
    say = [s for s in reply["steps"] if s.get("kind") == "say"]
    assert say and say[0]["label"] == "Гляну клип."
    assert tools[0]["tool"] == "get_clip" and tools[0]["ok"] and tools[0]["clip_id"] == plan["id"]
    assert tools[0]["args"] == {"clip_id": plan["id"]} and tools[0]["result"]
    assert tools[0]["preview"]["kind"] == "clip" and tools[0]["preview"]["clip_plan_id"] == plan["id"]
    # База без монтажа; после load_tools — инструменты группы в объявлениях и у переписки.
    assert "add_layers" not in _names(gemini.requests[0]) and "get_clip" in _names(gemini.requests[0])
    assert {"add_layers", "set_camera", "camera_preset"} <= _names(gemini.requests[2])
    assert "РЕМЕСЛО МОНТАЖА" in gemini.requests[2]["systemInstruction"]["parts"][0]["text"]
    assert C.default_thread(store, pid)["tool_groups"] == ["montage"]
    # Слова владельца сразу подгружают группу (без лишнего круга).
    gemini2 = Scripted([[{"text": "ок"}]])
    C.send(store, pid, "заглуши мат и поправь субтитры", client=gemini2)
    assert C.wait(pid, 10)
    names = _names(gemini2.requests[0])
    assert {"censor_profanity", "audio_map", "set_subtitle_words"} <= names
    # Всё, что есть у MCP, достижимо из чата (через группы).
    reachable = set(CT.base_names())
    for group in CT.all_groups():
        reachable |= set(CT.group_tools(group))
    assert reachable >= set(CT.all_tools())


def test_dangerous_action_needs_confirmation(env):
    client, store, source, pid = env
    plan = _plan(store, source["id"])
    gemini = Scripted([[{"functionCall": {"name": "delete_clip", "args": {"clip_id": plan["id"]}}}],
                       [{"text": "Жду подтверждения."}]])
    C.send(store, pid, "удали этот клип", client=gemini)
    assert C.wait(pid, 10)
    store.get_clip_plan(plan["id"])  # не удалён без кнопки
    answer = gemini.requests[1]["contents"][-1]["parts"][0]["functionResponse"]["response"]
    assert answer["pending_confirmation"] is True
    view = client.get(f"/api/chat/threads/{C.default_thread(store, pid)['id']}").json()
    reply = view["messages"][-1] if view["messages"][-1]["confirmations"] else view["messages"][-2]
    conf = reply["confirmations"][0]
    step = next(s for s in reply["steps"] if s.get("kind") == "confirm")
    assert conf["status"] == "pending" and step["confirmation_id"] == conf["id"] and step["danger"] == "Удалить клип"
    done = client.post(f"/api/chat/confirmations/{conf['id']}", json={"approve": True}).json()
    assert done["ok"] and done["status"] == "done"
    with pytest.raises(KeyError):
        store.get_clip_plan(plan["id"])
    assert client.post(f"/api/chat/confirmations/{conf['id']}", json={"approve": True}).status_code == 400
    after = C.list_messages(store, pid)
    assert "Владелец подтвердил" in after[-1]["text"]
    step = next(s for s in C.get_message(store, reply["id"])["steps"] if s.get("kind") == "confirm")
    assert step["ok"] and step["confirm_status"] == "done"
    # Отказ — ничего не делает.
    plan2 = _plan(store, source["id"], "второй")
    C.send(store, pid, "и этот удали", client=Scripted([[{"functionCall": {"name": "delete_clip",
                                                                          "args": {"clip_id": plan2["id"]}}}],
                                                       [{"text": "Жду."}]]))
    assert C.wait(pid, 10)
    conf2 = store.db.query_one("SELECT id FROM chat_confirmations WHERE status = 'pending'")["id"]
    assert client.post(f"/api/chat/confirmations/{conf2}", json={"approve": False}).json()["status"] == "rejected"
    store.get_clip_plan(plan2["id"])


def test_submit_for_approval_is_only_a_card_and_approving_is_dangerous(env):
    _client_, store, source, pid = env
    from app import autopilot as A

    plan = _plan(store, source["id"])
    assert not CT.is_dangerous(store, "submit_for_approval", {"clip_id": plan["id"]}, pid)
    # Доверие auto само не публикует: карточка, как всегда. Сам публикует только настройка проекта.
    A.update_config(store, pid, {"trust": {"default": "auto"}})
    assert not CT.is_dangerous(store, "submit_for_approval", {"clip_id": plan["id"]}, pid)
    assert CT.is_dangerous(store, "approve_publication", {"approval_id": 1}, pid)


def test_clip_edit_checkpoint_and_revert(env):
    client, store, source, pid = env
    plan = _plan(store, source["id"])
    before = (store.get_clip_plan(plan["id"]).get("render_settings") or {}).get("end_hold_sec")
    gemini = Scripted([[{"functionCall": {"name": "load_tools", "args": {"groups": ["cuts"]}}}],
                       [{"functionCall": {"name": "set_end_hold", "args": {"clip_id": plan["id"], "sec": 0}}}],
                       [{"text": "Убрал стоп-кадр в конце."}]])
    C.send(store, pid, "убери замирание в конце", client=gemini)
    assert C.wait(pid, 10)
    assert store.get_clip_plan(plan["id"])["render_settings"]["end_hold_sec"] == 0
    assert store.count_clip_plan_history(plan["id"]) >= 1  # Ctrl+Z в редакторе тоже знает о правке
    view = client.get(f"/api/chat/threads/{C.default_thread(store, pid)['id']}").json()
    reply = view["messages"][-1]
    change = reply["changes"][0]
    assert change["clip_plan_id"] == plan["id"] and not change["reverted"]
    res = client.post(f"/api/chat/checkpoints/{change['checkpoint_id']}/revert").json()
    assert res["ok"]
    assert (store.get_clip_plan(plan["id"]).get("render_settings") or {}).get("end_hold_sec") == before
    again = client.get(f"/api/chat/threads/{C.default_thread(store, pid)['id']}").json()
    assert next(m for m in again["messages"] if m["id"] == reply["id"])["changes"][0]["reverted"]
    assert client.post(f"/api/chat/checkpoints/{change['checkpoint_id']}/revert").status_code == 400


def test_cancel_stops_turn(env):
    _c, store, _source, pid = env
    gate = threading.Event()
    thread = C.default_thread(store, pid)
    gemini = Streaming([[[{"text": "Начинаю"}], [{"text": " длинный ответ"}]]], gate=gate)
    C.send(store, pid, "расскажи всё", client=gemini)
    run = C.active_run(thread["id"])
    for _ in range(200):
        if run.snapshot()["text"]:
            break
        threading.Event().wait(0.01)
    assert C.cancel(thread["id"])
    gate.set()
    assert C.wait(pid, 10)
    reply = C.list_messages(store, pid)[-1]
    assert reply["status"] == "cancelled" and reply["text"].startswith("Начинаю") and "Остановлено" in reply["text"]


def test_no_gemini_and_errors_are_honest(env, monkeypatch):
    client, store, _source, _pid = env
    monkeypatch.setattr(settings, "gemini_api_key", "")
    thread = C.create_thread(store)
    C.send_to_thread(store, thread["id"], "привет")
    assert C.wait_thread(thread["id"], 10)
    reply = C.thread_messages(store, thread["id"])[-1]
    assert "недоступен" in reply["text"] and "GEMINI_API_KEY" in reply["text"]
    assert client.get("/api/chat/models").json()["available"] is False

    class Broken:
        def generate_content(self, model, payload):
            raise RuntimeError("Gemini generateContent failed: User location is not supported for the API use.")

    C.send_to_thread(store, thread["id"], "ещё раз", client=Broken())
    assert C.wait_thread(thread["id"], 10)
    reply = C.thread_messages(store, thread["id"])[-1]
    assert reply["status"] == "error" and "VPN" in reply["text"] and "Polza" not in reply["text"]


def test_history_summary_per_thread_and_attachments(env):
    _c, store, _source, pid = env
    thread = C.create_thread(store, pid, title="Идеи")
    for i in range(40):
        C.add_thread_message(store, thread["id"], "user" if i % 2 == 0 else "assistant", f"реплика {i}")
    assert C.summarize_thread(store, thread["id"], client=None)
    fresh = C.get_thread(store, thread["id"])
    assert "реплика 0" in fresh["summary"] and fresh["summary_upto"] > 0
    gemini = Scripted([[{"text": "ок"}]])
    C.send_to_thread(store, thread["id"], "вот мем", client=gemini,
                     attachments=[{"kind": "asset", "id": 7, "name": "cat.gif", "media": "gif"}])
    assert C.wait_thread(thread["id"], 10)
    request = gemini.requests[0]
    assert "Резюме прежней переписки" in request["systemInstruction"]["parts"][0]["text"]
    history_text = " ".join(p.get("text", "") for c in request["contents"][:-1] for p in c["parts"])
    assert "реплика 0" not in history_text and "реплика 39" in history_text
    assert "asset_id 7" in request["contents"][-1]["parts"][0]["text"]
    user = C.thread_messages(store, thread["id"])[-2]
    assert user["meta"]["attachments"][0]["name"] == "cat.gif"
    # Резюме не основной переписки не трогает резюме проекта.
    from app import autopilot as A

    assert A._state_row(store, pid)["chat_summary"] == ""


def test_migrates_old_project_chat_table(tmp_path):
    conn = sqlite3.connect(tmp_path / "old.sqlite")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("CREATE TABLE sources (id INTEGER PRIMARY KEY)")  # чат видео ссылается на видео
    conn.execute("CREATE TABLE clip_plans (id INTEGER PRIMARY KEY)")  # чат клипа — на клип
    conn.execute("CREATE TABLE project_autopilot (project_id INTEGER PRIMARY KEY, chat_summary TEXT NOT NULL DEFAULT '',"
                 " chat_summary_upto INTEGER NOT NULL DEFAULT 0)")
    conn.execute("""CREATE TABLE project_chat_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        role TEXT NOT NULL, text TEXT NOT NULL DEFAULT '', steps_json TEXT NOT NULL DEFAULT '[]',
        status TEXT NOT NULL DEFAULT 'done', model TEXT NOT NULL DEFAULT '', origin TEXT NOT NULL DEFAULT 'ui',
        learned_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
    conn.execute("INSERT INTO projects (id, name) VALUES (5, 'Мура')")
    conn.execute("INSERT INTO project_autopilot (project_id, chat_summary, chat_summary_upto) VALUES (5, 'старое', 1)")
    conn.execute("INSERT INTO project_chat_messages (project_id, role, text) VALUES (5, 'user', 'привет')")
    conn.execute("INSERT INTO project_chat_messages (project_id, role, text) VALUES (5, 'assistant', 'здравствуй')")
    C.ensure_chat_schema(conn)
    C.ensure_chat_schema(conn)  # повторный запуск ничего не ломает
    thread = conn.execute("SELECT id, project_id, is_default, summary FROM chat_threads").fetchall()
    assert len(thread) == 1 and thread[0][1:] == (5, 1, "старое")
    rows = conn.execute("SELECT thread_id, project_id, text FROM project_chat_messages ORDER BY id").fetchall()
    assert rows == [(thread[0][0], 5, "привет"), (thread[0][0], 5, "здравствуй")]
    conn.execute("INSERT INTO project_chat_messages (thread_id, role, text) VALUES (?, 'user', 'общий')", (thread[0][0],))


def test_stream_merges_parts_keeps_signatures():
    parts: list[dict] = []
    for part in ({"text": "При"}, {"text": "вет"}, {"text": "", "thoughtSignature": "sig1"},
                 {"functionCall": {"name": "get_clip", "args": {}}, "thoughtSignature": "sig2"}):
        C._merge_part(parts, part)
    assert parts == [{"text": "Привет", "thoughtSignature": "sig1"},
                     {"functionCall": {"name": "get_clip", "args": {}}, "thoughtSignature": "sig2"}]


def test_chat_commands_are_not_rules_and_gemini_judges_disputed(env):
    _c, store, _source, pid = env
    from app import autopilot as A

    C.send(store, pid, "Убери замирание в конце", client=Scripted([[{"text": "Убрал."}]]))
    assert C.wait(pid, 10)
    C.send(store, pid, "Удали этот клип", client=Scripted([[{"text": "Жду кнопку."}]]))
    assert C.wait(pid, 10)
    assert not A.list_approvals(store, pid, kind="rule_proposal")
    # Спорное — одна короткая подсказка Gemini (текстовая модель), потом обычный ход.
    verdict = '{"rule": false, "kind": "dont", "text": ""}'
    gemini = Scripted([[{"text": verdict}], [{"text": "Ок, в этом клипе уберу."}]])
    C.send(store, pid, "без мата", client=gemini)
    assert C.wait(pid, 10)
    assert "РАЗОВАЯ" in gemini.requests[0]["contents"][0]["parts"][0]["text"]
    assert not A.list_approvals(store, pid, kind="rule_proposal")
    assert C.list_messages(store, pid)[-1]["text"] == "Ок, в этом клипе уберу."
    # Явное «никогда» — правило без вопроса к Gemini.
    gemini2 = Scripted([[{"text": "Записать?"}]])
    C.send(store, pid, "никогда не бери политику", client=gemini2)
    assert C.wait(pid, 10)
    assert len(gemini2.requests) == 1 and A.list_approvals(store, pid, kind="rule_proposal")
