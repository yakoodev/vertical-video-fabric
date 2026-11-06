"""Чат клипа — обычная переписка: старая история читается, инструменты, откат, карточки, файлы, «запомни», поток, стоп,
резюме, восстановление после перезапуска, прежний адрес /api/clip-plans/{id}/agent. Модель — заготовленные ответы."""

from __future__ import annotations

import copy
import json
import threading

import pytest

from app import agent_settings as S
from app import modules
from app import project_chat as C
from app import project_knowledge as K
from app import projects as P
from app.ai import editor_agent
from app.settings import settings
from test_api_pipeline import _client, _require_ffmpeg
from test_clip_spec import _setup


def _reply(parts):
    return {"candidates": [{"content": {"role": "model", "parts": parts}}]}


def _call(name, args=None):
    return {"functionCall": {"name": name, "args": args or {}}, "thoughtSignature": "sig"}


class Scripted:
    def __init__(self, turns):
        self.turns = list(turns)
        self.requests = []

    def generate_content(self, model, payload):
        self.requests.append(copy.deepcopy(payload))
        return _reply(self.turns.pop(0) if self.turns else [{"text": "Готово, всё сделал."}])


class Streaming(Scripted):
    def __init__(self, turns, gate=None):
        super().__init__(turns)
        self.gate = gate

    def generate_content(self, model, payload):  # pragma: no cover
        raise AssertionError("ожидался поток")

    def stream_generate_content(self, model, payload):
        self.requests.append(copy.deepcopy(payload))
        chunks = self.turns.pop(0)
        for index, parts in enumerate(chunks):
            if index == 1 and self.gate is not None:
                self.gate.wait(10)
            yield _reply(parts)


@pytest.fixture(autouse=True)
def no_runs_left():
    """Ходы живут в процессе и ключом им id переписки: упавший тест не должен держать следующий."""
    yield
    for run in list(C._RUNS.values()):
        run.cancel.set()
        if run.thread is not None:
            run.thread.join(5)
    C._RUNS.clear()


@pytest.fixture
def env(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    monkeypatch.setattr("app.ai.editor_agent.client_factory", None)
    source = store.create_source("upload", settings.source_dir / "mura.mp4", original_filename="mura.mp4",
                                 duration_sec=3600, status="ready")
    other = store.create_source("upload", settings.source_dir / "apex.mp4", original_filename="apex.mp4",
                                duration_sec=3600, status="ready")
    pid = P.create_project(store, "Мура", kind="stream_channel", source_ids=[source["id"]])["id"]
    other_pid = P.create_project(store, "Апекс", kind="stream_channel", source_ids=[other["id"]])["id"]
    return client, store, source, pid, other_pid


def _plan(store, source_id, title="Мура пугается", provider="manual"):
    analysis = store.create_ai_analysis(source_id, provider, status="succeeded")
    seg = store.create_ai_segment(analysis["id"], {"start_sec": 10, "end_sec": 25, "title": title})
    return store.create_clip_plan(source_id, analysis["id"], title, segment_ids=[seg["id"]])


def _old_rows(store, plan_id):
    """Строки как их писал прежний чат клипа."""
    store.add_clip_agent_message(plan_id, "user", "сделай хук")
    store.add_clip_agent_message(plan_id, "assistant", "Поставил хук на 0–2 с.", status="done", model="old-model",
                                 steps=[{"tool": "add_layers", "label": "Ставлю хук", "ok": True}])


def _send(store, thread, text, gemini, **kwargs):
    sent = C.send_to_thread(store, thread["id"], text, client=gemini, **kwargs)
    assert C.wait_thread(thread["id"], 30)
    return sent


def _end_hold(plan_id, sec=0):
    return _call("set_end_hold", {"sec": sec})


# --------------------------------------------------------------------------- #
# Хранение: старая переписка на месте, новая — в переписке
# --------------------------------------------------------------------------- #


def test_old_history_is_read_through_and_rows_stay(env):
    client, store, source, _pid, _other = env
    plan = _plan(store, source["id"])
    _old_rows(store, plan["id"])
    before = [dict(r) for r in store.db.query_all("SELECT * FROM clip_agent_messages WHERE clip_plan_id = ?", (plan["id"],))]
    thread = C.clip_thread(store, plan["id"])
    assert thread["clip_plan_id"] == plan["id"] and thread["source_id"] is None
    assert C.clip_thread(store, plan["id"])["id"] == thread["id"], "один чат на клип"
    gemini = Scripted([[{"text": "Хук стоит на 0–2 с."}]])
    _send(store, thread, "где хук?", gemini)

    view = client.get(f"/api/chat/threads/{thread['id']}").json()
    assert view["clip"]["id"] == plan["id"] and view["clip"]["title"] == "Мура пугается"
    texts = [m["text"] for m in view["messages"]]
    assert texts == ["сделай хук", "Поставил хук на 0–2 с.", "где хук?", "Хук стоит на 0–2 с."]
    assert [m["id"] < 0 for m in view["messages"]] == [True, True, False, False], "id старых реплик не пересекаются"
    assert len({m["id"] for m in view["messages"]}) == 4
    assert view["messages"][1]["steps"][0]["label"] == "Ставлю хук" and view["messages"][1]["changes"] == []

    # Модель получила старую переписку, как раньше.
    history = gemini.requests[0]["contents"]
    assert "сделай хук" in history[0]["parts"][0]["text"] and "Поставил хук" in history[1]["parts"][0]["text"]
    # Старые строки те же; новых в старую таблицу не пишем.
    after = [dict(r) for r in store.db.query_all("SELECT * FROM clip_agent_messages WHERE clip_plan_id = ?", (plan["id"],))]
    assert after == before


def test_old_chats_get_a_thread_row_and_show_in_the_list(env):
    client, store, source, pid, _other = env
    plan = _plan(store, source["id"])
    _old_rows(store, plan["id"])
    assert C.find_clip_thread(store, plan["id"]) is None
    with store.db.connect() as conn:
        C.ensure_chat_schema(conn)
        C.ensure_chat_schema(conn)  # повторный запуск ничего не добавляет
    thread = C.find_clip_thread(store, plan["id"])
    assert thread and thread["project_id"] == pid
    assert store.db.query_one("SELECT COUNT(*) AS n FROM chat_threads WHERE clip_plan_id = ?", (plan["id"],))["n"] == 1
    assert store.db.query_one("SELECT COUNT(*) AS n FROM project_chat_messages")["n"] == 0, "реплики не копируются"

    listed = client.get("/api/chat/threads").json()
    mine = next(t for p in listed["projects"] if p["id"] == pid for t in p["threads"] if t["id"] == thread["id"])
    assert mine["title"] == "Клип: Мура пугается" and mine["messages_count"] == 2
    assert mine["last_text"] == "Поставил хук на 0–2 с."
    assert thread["id"] not in [t["id"] for t in listed["general"]]


def test_empty_clip_chat_is_not_listed_and_loose_clip_has_its_own_bucket(env):
    client, store, source, _pid, _other = env
    empty = C.clip_thread(store, _plan(store, source["id"])["id"])
    loose_source = store.create_source("upload", settings.source_dir / "x.mp4", original_filename="x.mp4",
                                       duration_sec=100, status="ready")
    loose = C.clip_thread(store, _plan(store, loose_source["id"], "Без проекта")["id"])
    _send(store, loose, "привет", Scripted([[{"text": "Привет."}]]))
    listed = client.get("/api/chat/threads").json()
    all_ids = [t["id"] for p in listed["projects"] for t in p["threads"]] + [t["id"] for t in listed["clips"]]
    assert empty["id"] not in all_ids
    assert [t["id"] for t in listed["clips"]] == [loose["id"]]
    assert loose["id"] not in [t["id"] for t in listed["general"] + listed["videos"]]


def test_clip_chat_follows_project_and_dies_with_the_clip(env):
    _client_, store, source, pid, other = env
    plan = _plan(store, source["id"])
    thread = C.clip_thread(store, plan["id"])
    assert thread["project_id"] == pid
    P.set_source_project(store, source["id"], other)
    assert C.get_thread(store, thread["id"])["project_id"] == other, "проект берём у клипа в момент чтения"
    _old_rows(store, plan["id"])
    _send(store, C.get_thread(store, thread["id"]), "привет", Scripted([[{"text": "Привет."}]]))
    store.delete_clip_plan(plan["id"])
    assert store.db.query_one("SELECT id FROM chat_threads WHERE id = ?", (thread["id"],)) is None
    assert store.db.query_one("SELECT id FROM project_chat_messages WHERE thread_id = ?", (thread["id"],)) is None


def test_clear_and_delete_remove_both_histories(env):
    client, store, source, _pid, _other = env
    plan = _plan(store, source["id"])
    _old_rows(store, plan["id"])
    thread = C.clip_thread(store, plan["id"])
    _send(store, thread, "привет", Scripted([[{"text": "Привет."}]]))
    assert client.delete(f"/api/chat/threads/{thread['id']}/messages").json()["deleted"] == 4
    assert client.get(f"/api/chat/threads/{thread['id']}").json()["messages"] == []
    _old_rows(store, plan["id"])
    assert client.delete(f"/api/chat/threads/{thread['id']}").json() == {"deleted": thread["id"]}
    assert store.list_clip_agent_messages(plan["id"]) == []


def test_older_versions_chat_reads_both_tables(env):
    _client_, store, source, _pid, _other = env
    from app import clip_groups, clip_groups_view

    first = _plan(store, source["id"], "Первая версия")
    second = clip_groups.new_version(store, first["id"])
    _old_rows(store, first["id"])
    _send(store, C.clip_thread(store, first["id"]), "новая реплика", Scripted([[{"text": "Новый ответ."}]]))
    view = clip_groups_view.clip_view(store, second["id"])
    texts = [m["text"] for m in view["older_chat"]]
    assert "сделай хук" in texts and "новая реплика" in texts and len({m["id"] for m in view["older_chat"]}) == len(texts)


# --------------------------------------------------------------------------- #
# Ход: инструменты, откат, карточки
# --------------------------------------------------------------------------- #


def test_tools_checkpoint_and_revert(env):
    client, store, source, _pid, _other = env
    plan = _plan(store, source["id"])
    before = (store.get_clip_plan(plan["id"]).get("render_settings") or {}).get("end_hold_sec")
    thread = C.clip_thread(store, plan["id"])
    gemini = Scripted([[{"text": "Убираю стоп-кадр."}, _end_hold(plan["id"])], [{"text": "Готово: убрал стоп-кадр в конце."}]])
    _send(store, thread, "убери замирание в конце", gemini)

    assert store.get_clip_plan(plan["id"])["render_settings"]["end_hold_sec"] == 0
    # У монтажёра все клиповые инструменты и без clip_id: клип уже известен.
    names = {d["name"] for d in gemini.requests[0]["tools"][0]["functionDeclarations"]}
    assert {"add_layers", "set_end_hold", "view_frame"} <= names
    assert "clip_id" not in json.dumps(next(d for d in gemini.requests[0]["tools"][0]["functionDeclarations"]
                                            if d["name"] == "set_end_hold"))
    view = client.get(f"/api/chat/threads/{thread['id']}").json()
    reply = view["messages"][-1]
    assert reply["text"].startswith("Готово") and reply["status"] == "done"
    assert [s["tool"] for s in reply["steps"]] == ["_say", "set_end_hold"]
    step = reply["steps"][1]
    assert step["kind"] == "tool" and step["ok"] and step["clip_id"] == plan["id"] and step["result"]
    # Один снимок на ход с прежней подписью: Ctrl+Z откатывает ответ целиком.
    assert store.count_clip_plan_history(plan["id"]) == 1
    label = store.db.query_one("SELECT label FROM clip_plan_history WHERE clip_plan_id = ?", (plan["id"],))["label"]
    assert label.startswith("ИИ: убери замирание")
    change = reply["changes"][0]
    assert change["clip_plan_id"] == plan["id"] and not change["reverted"]

    assert client.post(f"/api/chat/checkpoints/{change['checkpoint_id']}/revert").json()["ok"]
    assert (store.get_clip_plan(plan["id"]).get("render_settings") or {}).get("end_hold_sec") == before
    again = client.get(f"/api/chat/threads/{thread['id']}").json()
    assert next(m for m in again["messages"] if m["id"] == reply["id"])["changes"][0]["reverted"]
    assert again["messages"][-1]["meta"]["service"], "откат виден в переписке"
    assert client.post(f"/api/chat/checkpoints/{change['checkpoint_id']}/revert").status_code == 400


def test_review_gates_still_work_in_thread(env):
    """Проверки перед сдачей: ход не кончается «пустым ответом» и не теряет отчёт, как в монтажёре."""
    _client_, store, source, _pid, _other = env
    plan = _plan(store, source["id"])
    gemini = Scripted([[_end_hold(plan["id"])], [], [{"text": "Сделано: стоп-кадра в конце больше нет."}]])
    thread = C.clip_thread(store, plan["id"])
    _send(store, thread, "убери замирание", gemini)
    reply = C.thread_messages(store, thread["id"])[-1]
    assert len(gemini.requests) == 3 and reply["text"].startswith("Сделано")


def test_frame_preview_in_clip_chat(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    thread = C.clip_thread(store, plan["id"])
    gemini = Scripted([[_call("view_frame", {"t": 1.0})], [{"text": "В кадре пусто, смотрел на 1 с."}]])
    _send(store, thread, "что на первой секунде?", gemini)
    reply = client.get(f"/api/chat/threads/{thread['id']}").json()["messages"][-1]
    preview = reply["steps"][0]["preview"]
    assert preview["kind"] == "image" and preview["clip_id"] == plan["id"]
    assert client.get(preview["url"]).status_code == 200
    # Кадр видит и модель.
    assert any("inlineData" in p for p in gemini.requests[1]["contents"][-1]["parts"])


def test_dangerous_goes_to_card_and_runs_after_owner(env):
    client, store, source, _pid, _other = env
    plan = _plan(store, source["id"])
    store.set_clip_plan_render_settings(plan["id"], {"layers": [{"id": "l1", "type": "text", "start": 0, "end": 1,
                                                                 "x": 0.5, "y": 0.5, "props": {"text": "ХУК"}}]})
    thread = C.clip_thread(store, plan["id"])
    gemini = Scripted([[_call("clear_layers")], [{"text": "Жду подтверждения: снять все слои."}]])
    _send(store, thread, "сними все слои", gemini)
    assert store.get_clip_plan(plan["id"])["render_settings"]["layers"], "без кнопки владельца слои на месте"
    view = client.get(f"/api/chat/threads/{thread['id']}").json()
    reply = view["messages"][-1]
    step = reply["steps"][0]
    assert step["kind"] == "confirm" and step["ok"] is None and reply["confirmations"][0]["status"] == "pending"
    # Модель видит, что вызов ждёт владельца.
    answer = gemini.requests[1]["contents"][-1]["parts"][0]["functionResponse"]["response"]
    assert answer["pending_confirmation"] is True

    done = client.post(f"/api/chat/confirmations/{step['confirmation_id']}", json={"approve": True}).json()
    assert done["status"] == "done"
    assert not store.get_clip_plan(plan["id"])["render_settings"].get("layers")
    view = client.get(f"/api/chat/threads/{thread['id']}").json()
    reply = next(m for m in view["messages"] if m["id"] == reply["id"])
    assert reply["steps"][0]["confirm_status"] == "done" and reply["changes"], "правка по кнопке тоже откатывается"
    assert view["messages"][-1]["meta"]["service"]
    # Откат кнопкой возвращает слои.
    assert client.post(f"/api/chat/checkpoints/{reply['changes'][0]['checkpoint_id']}/revert").json()["ok"]
    assert store.get_clip_plan(plan["id"])["render_settings"]["layers"]


def test_refused_card_leaves_clip(env):
    client, store, source, _pid, _other = env
    plan = _plan(store, source["id"])
    thread = C.clip_thread(store, plan["id"])
    _send(store, thread, "сними все слои", Scripted([[_call("clear_layers")], [{"text": "Жду подтверждения."}]]))
    conf = client.get(f"/api/chat/threads/{thread['id']}").json()["messages"][-1]["confirmations"][0]
    assert client.post(f"/api/chat/confirmations/{conf['id']}", json={"approve": False}).json()["status"] == "rejected"
    assert client.post(f"/api/chat/confirmations/{conf['id']}", json={"approve": True}).status_code == 400


def test_paid_voice_needs_card_in_clip_chat(env):
    client, store, source, _pid, _other = env
    plan = _plan(store, source["id"])
    thread = C.clip_thread(store, plan["id"])
    _send(store, thread, "озвучь", Scripted([[_call("dub_clip", {"confirm": True, "provider": "polza"})],
                                             [{"text": "Жду подтверждения платной озвучки."}]]))
    reply = client.get(f"/api/chat/threads/{thread['id']}").json()["messages"][-1]
    assert reply["steps"][0]["kind"] == "confirm" and "платно" in reply["steps"][0]["danger"]


def test_clip_chat_cannot_approve_delete_or_read_foreign_lore(env):
    """Правила чата: карточки одобрения агент не одобряет, лор другого проекта недоступен, даже если модель назовёт
    инструмент, которого ей не объявляли."""
    client, store, source, pid, other = env
    K.add_fact(store, other, "секрет другого проекта", origin="owner")
    plan = _plan(store, source["id"])
    thread = C.clip_thread(store, plan["id"])
    gemini = Scripted([
        [_call("approve_publication", {"approval_id": 1})],
        [_call("delete_clip", {"clip_id": plan["id"]})],
        [_call("get_project_brief", {"project_id": other})],
        [_call("add_rule", {"project_id": pid, "text": "новое"})],
        [{"text": "Этого здесь нельзя, всё честно."}],
    ])
    _send(store, thread, "одобри и удали", gemini)
    reply = client.get(f"/api/chat/threads/{thread['id']}").json()["messages"][-1]
    assert not reply["confirmations"], "из чата клипа таких карточек нет"
    assert all(s["ok"] is False for s in reply["steps"] if s["kind"] == "tool")
    assert store.get_clip_plan(plan["id"])  # клип на месте
    assert "секрет" not in json.dumps(gemini.requests[-1], ensure_ascii=False)
    names = {d["name"] for d in gemini.requests[0]["tools"][0]["functionDeclarations"]}
    assert not names & {"approve_publication", "delete_clip", "get_project_brief", "add_rule", "revoke_rule"}


def test_clip_context_has_only_its_own_project_lore(env):
    _client_, store, source, pid, other = env
    K.add_fact(store, pid, "Глеб — модератор", origin="owner")
    K.add_fact(store, other, "секрет другого проекта", origin="owner")
    plan = _plan(store, source["id"])
    gemini = Scripted([[{"text": "Понял."}]])
    _send(store, C.clip_thread(store, plan["id"]), "кто такой Глеб?", gemini)
    text = json.dumps(gemini.requests[0], ensure_ascii=False)
    assert "Глеб — модератор" in text and "секрет" not in text


def test_project_chat_still_refuses_other_projects(env):
    client, store, _source, pid, other = env
    K.add_fact(store, other, "секрет другого проекта", origin="owner")
    thread = C.default_thread(store, pid)
    gemini = Scripted([[_call("get_project_brief", {"project_id": other})], [{"text": "Это другой проект."}]])
    _send(store, thread, "что у соседей?", gemini)
    reply = client.get(f"/api/chat/threads/{thread['id']}").json()["messages"][-1]
    assert reply["steps"][0]["ok"] is False and "секрет" not in json.dumps(gemini.requests[-1], ensure_ascii=False)


# --------------------------------------------------------------------------- #
# Файлы, запоминание, поток, стоп, резюме, перезапуск
# --------------------------------------------------------------------------- #


def test_attachments_reach_the_model_and_stay_on_the_message(env, monkeypatch):
    client, store, source, _pid, _other = env
    plan = _plan(store, source["id"])
    thread = C.clip_thread(store, plan["id"])
    gemini = Scripted([[{"text": "Вижу файл, вставлю его."}]])
    monkeypatch.setattr(editor_agent, "client_factory", lambda: gemini)
    sent = client.post(f"/api/chat/threads/{thread['id']}/messages", json={
        "text": "вставь этот мем", "attachments": [{"kind": "asset", "id": 7, "name": "cat.gif", "media": "gif"}]})
    assert sent.status_code == 200
    assert C.wait_thread(thread["id"], 30)
    assert "asset_id 7" in gemini.requests[0]["contents"][-1]["parts"][0]["text"]
    assert "файл для монтажа" in gemini.requests[0]["systemInstruction"]["parts"][0]["text"]
    user = next(m for m in C.thread_messages(store, thread["id"]) if m["role"] == "user")
    assert user["meta"]["attachments"][0]["name"] == "cat.gif" and "Прикреплён" not in user["text"]


def test_remember_writes_fact_of_clip_project(env):
    _client_, store, source, pid, other = env
    plan = _plan(store, source["id"])
    thread = C.clip_thread(store, plan["id"])
    gemini = Scripted([[{"text": "Хорошо."}]])
    _send(store, thread, "запомни: Онигуру и Onihana_ один человек", gemini,
          attachments=[{"kind": "asset", "id": 7, "name": "cat.gif", "media": "gif"}])
    fact = K.list_facts(store, pid)[0]
    assert "Онигуру" in fact["text"] and "Прикреплён" not in fact["text"]
    assert fact["clip_plan_id"] == plan["id"] and fact["source_id"] == source["id"] and K.list_facts(store, other) == []
    reply = C.thread_messages(store, thread["id"])[-1]
    assert reply["text"].startswith("Запомнил:") and reply["meta"]["remembered"]["project_id"] == pid
    assert "Сервис уже сделал" in gemini.requests[0]["contents"][-1]["parts"][0]["text"]
    assert K.knowledge_view(store, pid)["groups"][0]["items"][0]["origin"] == f"из чата клипа #{plan['id']}"


def test_rules_are_not_learned_from_clip_chat(env):
    """«Больше так не делай» в чате клипа — про этот клип: как и раньше, правил проекта он не пишет."""
    _client_, store, source, pid, _other = env
    plan = _plan(store, source["id"])
    _send(store, C.clip_thread(store, plan["id"]), "такое больше не бери", Scripted([[{"text": "Понял."}]]))
    assert not store.db.query_one("SELECT 1 FROM approvals WHERE project_id = ? AND kind = 'rule_proposal'", (pid,))


def test_stream_live_and_sse_in_clip_chat(env):
    client, store, source, _pid, _other = env
    plan = _plan(store, source["id"])
    thread = C.clip_thread(store, plan["id"])
    gate = threading.Event()
    gemini = Streaming([[[{"text": "Хук "}], [{"text": "стоит на 0–2 с."}]]], gate=gate)
    C.send_to_thread(store, thread["id"], "где хук?", client=gemini)
    run = C.active_run(thread["id"])
    for _ in range(300):
        if run.snapshot()["text"]:
            break
        threading.Event().wait(0.01)
    live = client.get(f"/api/chat/threads/{thread['id']}").json()
    assert live["running"] and live["live"]["text"] == "Хук "
    assert client.get(f"/api/clip-plans/{plan['id']}/agent").json()["running"] is True
    gate.set()
    with client.stream("GET", f"/api/chat/threads/{thread['id']}/stream") as response:
        body = "".join(response.iter_text())
    assert "event: done" in body
    assert C.wait_thread(thread["id"], 10)
    assert C.thread_messages(store, thread["id"])[-1]["text"] == "Хук стоит на 0–2 с."


def test_stop_keeps_done_edits_and_says_so(env):
    client, store, source, _pid, _other = env
    plan = _plan(store, source["id"])
    thread = C.clip_thread(store, plan["id"])
    gate = threading.Event()

    class Slow(Scripted):
        def generate_content(self, model, payload):
            if self.requests:
                gate.wait(10)
            return super().generate_content(model, payload)

    C.send_to_thread(store, thread["id"], "убери замирание", client=Slow([[_end_hold(plan["id"])], [_end_hold(plan["id"], 0)]]))
    for _ in range(500):
        if (store.get_clip_plan(plan["id"]).get("render_settings") or {}).get("end_hold_sec") == 0:
            break
        threading.Event().wait(0.01)
    assert C.cancel(thread["id"])
    gate.set()
    assert C.wait_thread(thread["id"], 20)
    reply = client.get(f"/api/chat/threads/{thread['id']}").json()["messages"][-1]
    assert reply["status"] == "cancelled" and "Остановлено" in reply["text"]
    assert store.get_clip_plan(plan["id"])["render_settings"]["end_hold_sec"] == 0, "сделанное осталось"
    assert reply["changes"], "и откатывается"
    assert not C.active_run(thread["id"])


def test_summary_covers_old_rows_once_and_reaches_the_prompt(env):
    _client_, store, source, _pid, _other = env
    plan = _plan(store, source["id"])
    _old_rows(store, plan["id"])
    thread = C.clip_thread(store, plan["id"])
    for i in range(40):
        C.add_thread_message(store, thread["id"], "user" if i % 2 == 0 else "assistant", f"реплика {i}")
    assert C.summarize_thread(store, thread["id"], client=None)
    fresh = C.get_thread(store, thread["id"])
    assert "сделай хук" in fresh["summary"] and "реплика 0" in fresh["summary"] and fresh["summary_upto"] > 0
    gemini = Scripted([[{"text": "Ок."}]])
    _send(store, fresh, "напомни, о чём говорили", gemini)
    request = gemini.requests[0]
    assert "Резюме прежней переписки" in request["systemInstruction"]["parts"][0]["text"]
    history = " ".join(p.get("text", "") for c in request["contents"][:-1] for p in c["parts"])
    assert "сделай хук" not in history and "реплика 39" in history
    # Экран видит всё: сжатие только для модели.
    assert len(C.thread_view(store, thread["id"])["messages"]) >= 44


def test_restart_recovery(env):
    client, store, source, _pid, _other = env
    plan = _plan(store, source["id"])
    # Ход шёл во время обновления: строка в старой таблице «running», поток уже мёртв.
    store.add_clip_agent_message(plan["id"], "user", "сделай")
    store.add_clip_agent_message(plan["id"], "assistant", "", status="running")
    thread = C.clip_thread(store, plan["id"])
    # И такой же ход уже в новой таблице.
    stuck = C.add_thread_message(store, thread["id"], "assistant", "", status="running")
    view = client.get(f"/api/chat/threads/{thread['id']}").json()
    assert view["running"] is False
    assert [m["status"] for m in view["messages"]] == ["done", "interrupted", "interrupted"]
    assert C.get_message(store, stuck)["status"] == "interrupted"
    row = store.db.query_one("SELECT status FROM clip_agent_messages WHERE clip_plan_id = ? ORDER BY id DESC", (plan["id"],))
    assert row["status"] == "running", "старые строки только читаем"
    old_view = client.get(f"/api/clip-plans/{plan['id']}/agent").json()
    assert old_view["running"] is False and old_view["messages"][-1]["status"] == "interrupted"
    # Следующий ход начинается как обычно: прерванная реплика не мешает.
    gemini = Scripted([[{"text": "Продолжаю, всё хорошо."}]])
    _send(store, C.get_thread(store, thread["id"]), "ну что?", gemini)
    assert C.thread_messages(store, thread["id"])[-1]["text"].startswith("Продолжаю")


def test_error_is_visible_and_turn_ends(env):
    client, store, source, _pid, _other = env
    plan = _plan(store, source["id"])
    thread = C.clip_thread(store, plan["id"])

    class Broken:
        def generate_content(self, model, payload):
            raise RuntimeError("Gemini generateContent failed: User location is not supported for the API use.")

    _send(store, thread, "привет", Broken())
    reply = client.get(f"/api/chat/threads/{thread['id']}").json()["messages"][-1]
    assert reply["status"] == "error" and reply["text"].startswith("Ошибка") and "VPN" in reply["text"]
    assert not C.active_run(thread["id"])


def test_signature_retry_in_clip_chat(env):
    _client_, store, source, _pid, _other = env
    plan = _plan(store, source["id"])

    class Flaky(Scripted):
        fails = 1

        def generate_content(self, model, payload):
            if len(self.requests) == 1 and self.fails:
                self.fails = 0
                raise RuntimeError("Function call is missing a thought_signature")
            return super().generate_content(model, payload)

    no_sig = {"functionCall": {"name": "set_end_hold", "args": {"sec": 0}}}
    gemini = Flaky([[no_sig], [{"text": "Готово: стоп-кадра нет."}]])
    thread = C.clip_thread(store, plan["id"])
    _send(store, thread, "убери замирание", gemini)
    assert C.thread_messages(store, thread["id"])[-1]["text"].startswith("Готово")


# --------------------------------------------------------------------------- #
# Прежний адрес
# --------------------------------------------------------------------------- #


def test_old_endpoint_works_over_thread(env, monkeypatch):
    client, store, source, _pid, _other = env
    plan = _plan(store, source["id"])
    _old_rows(store, plan["id"])
    gemini = Scripted([[_end_hold(plan["id"])], [{"text": "Готово: убрал стоп-кадр."}]])
    monkeypatch.setattr(editor_agent, "client_factory", lambda: gemini)
    url = f"/api/clip-plans/{plan['id']}/agent"
    started = client.post(url, json={"message": "убери замирание", "model": "моя-модель", "max_steps": 7}).json()
    assert started["run_id"] and started["message_id"] and started["model"] == "моя-модель"
    assert editor_agent.wait_run(plan["id"], 30)
    chat = client.get(url).json()
    assert chat["running"] is False and chat["run_id"] is None
    assert [m["role"] for m in chat["messages"]] == ["user", "assistant", "user", "assistant"]
    # tools/vvf.py ищет ответ по message_id и смотрит на steps.
    reply = next(m for m in reversed(chat["messages"]) if m["id"] == started["message_id"])
    assert reply["status"] == "done" and reply["text"].startswith("Готово") and reply["model"] == "моя-модель"
    assert [s["tool"] for s in reply["steps"]] == ["set_end_hold"] and "args" not in reply["steps"][0]
    assert chat["messages"][1]["steps"][0]["label"] == "Ставлю хук"
    # Та же переписка видна и по новому адресу.
    thread = C.find_clip_thread(store, plan["id"])
    assert len(client.get(f"/api/chat/threads/{thread['id']}").json()["messages"]) == 4
    assert client.post(url + "/cancel", json={}).json() == {"cancelling": False}
    assert client.delete(url).json()["deleted"] == 4
    assert client.get(url).json()["messages"] == []
    assert store.list_clip_agent_messages(plan["id"]) == []


def test_old_endpoint_busy_409_and_old_rows_without_thread(env, monkeypatch):
    client, store, source, _pid, _other = env
    plan = _plan(store, source["id"])
    _old_rows(store, plan["id"])
    url = f"/api/clip-plans/{plan['id']}/agent"
    chat = client.get(url).json()  # GET новой строки не заводит
    assert len(chat["messages"]) == 2 and C.find_clip_thread(store, plan["id"]) is None
    assert client.get("/api/clip-plans/999999/agent").status_code == 404
    gate = threading.Event()
    gemini = Streaming([[[{"text": "Смотрю"}], [{"text": " дальше."}]]], gate=gate)
    monkeypatch.setattr(editor_agent, "client_factory", lambda: gemini)
    assert client.post(url, json={"message": "первое"}).status_code == 200
    busy = client.post(url, json={"message": "второе"})
    assert busy.status_code == 409 and client.delete(url).status_code == 409
    assert client.post(url + "/cancel", json={}).json() == {"cancelling": True}
    gate.set()
    assert editor_agent.wait_run(plan["id"], 20)


def test_old_endpoint_without_key_remembers_and_refuses(env, monkeypatch):
    client, store, source, pid, _other = env
    plan = _plan(store, source["id"])
    monkeypatch.setattr(settings, "gemini_api_key", "")
    answer = client.post(f"/api/clip-plans/{plan['id']}/agent", json={"message": "запомни: Глеб — модератор"})
    assert answer.status_code == 400 and "Запомнил" in answer.json()["detail"]
    assert "Глеб" in K.list_facts(store, pid)[0]["text"]


def test_no_key_in_thread_is_honest_and_still_remembers(env, monkeypatch):
    _client_, store, source, pid, _other = env
    plan = _plan(store, source["id"])
    monkeypatch.setattr(settings, "gemini_api_key", "")
    thread = C.clip_thread(store, plan["id"])
    C.send_to_thread(store, thread["id"], "запомни: Глеб — модератор")
    assert C.wait_thread(thread["id"], 10)
    reply = C.thread_messages(store, thread["id"])[-1]
    assert "недоступен" in reply["text"] and "Запомнил" in reply["text"]
    assert "Глеб" in K.list_facts(store, pid)[0]["text"]


# --------------------------------------------------------------------------- #
# Владелец, токен, модули
# --------------------------------------------------------------------------- #


def _stop_on_list(store, pid):
    S.save_settings(store, pid, {**S.get_settings(store, pid), "stop_at_clip_list": True})


def test_owner_message_picks_draft_and_agent_token_is_refused(env, monkeypatch):
    client, store, source, pid, _other = env
    _stop_on_list(store, pid)
    plan = _plan(store, source["id"], provider="gemini")
    thread = C.clip_thread(store, plan["id"])
    assert S.awaits_pick(store, plan["id"])
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "api_token", "secret-token", raising=False)
    monkeypatch.setattr(settings, "ensure_api_token", lambda: "secret-token")
    gemini = Scripted([[{"text": "Смотрю клип."}]])
    monkeypatch.setattr(editor_agent, "client_factory", lambda: gemini)
    url = f"/api/chat/threads/{thread['id']}/messages"
    bearer = {"Authorization": "Bearer secret-token"}
    denied = client.post(url, json={"text": "смонтируй"}, headers=bearer)
    assert denied.status_code == 409 and S.NAME_STOP in denied.json()["detail"]
    assert S.awaits_pick(store, plan["id"]), "токен не считается выбором"
    client.cookies.set(settings.auth_cookie_name, "secret-token")
    try:
        assert client.post(url, json={"text": "смонтируй"}).status_code == 200
        assert C.wait_thread(thread["id"], 20)
    finally:
        client.cookies.clear()
    assert not S.awaits_pick(store, plan["id"]), "сообщение владельца в чат клипа — его выбор, как и прежде"


def test_unpicked_draft_without_owner_goes_to_card(env):
    client, store, source, pid, _other = env
    _stop_on_list(store, pid)
    plan = _plan(store, source["id"], provider="gemini")
    thread = C.clip_thread(store, plan["id"])
    gemini = Scripted([[_end_hold(plan["id"])], [{"text": "Жду, когда владелец возьмёт черновик."}]])
    _send(store, thread, "убери замирание", gemini)
    reply = client.get(f"/api/chat/threads/{thread['id']}").json()["messages"][-1]
    assert reply["steps"][0]["kind"] == "confirm" and reply["steps"][0]["danger"] == "Взять черновик в работу"
    done = client.post(f"/api/chat/confirmations/{reply['steps'][0]['confirmation_id']}", json={"approve": True}).json()
    assert done["status"] == "done" and store.get_clip_plan(plan["id"])["render_settings"]["end_hold_sec"] == 0


def test_chat_page_off_keeps_clip_chat(env, monkeypatch):
    client, store, source, _pid, _other = env
    plan = _plan(store, source["id"])
    thread = client.post("/api/chat/clip-thread", json={"clip_plan_id": plan["id"]}).json()
    general = C.create_thread(store)
    monkeypatch.setenv(modules.ENV_DISABLED, "agent_chat")
    modules.invalidate(store)
    try:
        assert client.get("/api/chat/threads").status_code == 423
        assert client.get(f"/api/chat/threads/{general['id']}").status_code == 423
        assert client.get(f"/api/chat/threads/{thread['id']}").status_code == 200
        assert client.post("/api/chat/clip-thread", json={"clip_plan_id": plan["id"]}).status_code == 200
        assert client.get("/api/chat/models").status_code == 200
    finally:
        monkeypatch.delenv(modules.ENV_DISABLED)
        modules.invalidate(store)


def test_editor_agent_off_closes_clip_chat(env, monkeypatch):
    client, store, source, _pid, _other = env
    plan = _plan(store, source["id"])
    thread = C.clip_thread(store, plan["id"])
    monkeypatch.setenv(modules.ENV_DISABLED, "editor_agent")
    modules.invalidate(store)
    try:
        assert client.post("/api/chat/clip-thread", json={"clip_plan_id": plan["id"]}).status_code == 423
        assert client.post(f"/api/chat/threads/{thread['id']}/messages", json={"text": "привет"}).status_code == 423
        assert client.get(f"/api/chat/threads/{thread['id']}").status_code == 200, "история читается"
    finally:
        monkeypatch.delenv(modules.ENV_DISABLED)
        modules.invalidate(store)


def test_clip_thread_404(env):
    client, _store, _source, _pid, _other = env
    assert client.post("/api/chat/clip-thread", json={"clip_plan_id": 999999}).status_code == 404
