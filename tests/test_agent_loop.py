"""Общий цикл агента (app/ai/agent_loop.py): оба чата идут через него, повторы при ошибках модели — у всех."""

from __future__ import annotations

import copy

import pytest

from app import project_chat as C
from app import projects as P
from app.ai import agent_loop, editor_agent
from app.ai.agent_loop import TurnCancelled, TurnPolicy, patch_thought_signatures, run_loop
from app.ai.editor_agent_contents import SKIP_SIGNATURE
from app.settings import settings
from test_api_pipeline import _client


def _reply(parts):
    return {"candidates": [{"content": {"role": "model", "parts": parts}}]}


def _call(name, args=None):
    return {"functionCall": {"name": name, "args": args or {}}}


class Scripted:
    """Заготовленные ответы; ``failures`` — какие ошибки бросить перед ответом с этим номером."""

    def __init__(self, turns, failures=None):
        self.turns = list(turns)
        self.failures = dict(failures or {})
        self.requests = []
        self.calls = 0

    def generate_content(self, model, payload):
        self.calls += 1
        self.requests.append(copy.deepcopy(payload))
        for text in self.failures.pop(self.calls, []) if self.calls in self.failures else []:
            raise RuntimeError(text)
        return _reply(self.turns.pop(0) if self.turns else [{"text": "ок"}])


class EchoPolicy(TurnPolicy):
    """Самая простая политика: один инструмент «echo», ответ модели — конец хода."""

    def __init__(self):
        self.ran = []

    def prompt(self, state):
        return "система"

    def declarations(self, json_schema):
        return [{"name": "echo", "description": "эхо", "parametersJsonSchema" if json_schema else "parameters": {}}]

    def run_call(self, state, name, args):
        self.ran.append(name)
        state.steps.append({"tool": name, "label": name, "ok": True, "kind": "tool"})
        state.emit("tool")
        return {"ok": True, "echo": args}, True

    def on_blocked(self, state, reason):
        return f"фильтр {reason}"


def _contents():
    return [{"role": "user", "parts": [{"text": "привет"}]}]


# --------------------------------------------------------------------------- #
# Сам цикл
# --------------------------------------------------------------------------- #


def test_loop_runs_tools_and_answers():
    gemini = Scripted([[{"text": "Проверяю."}, _call("echo", {"a": 1})], [{"text": "Готово."}]])
    policy = EchoPolicy()
    text, state = run_loop(policy, gemini, "m", _contents(), limit=5)
    assert text == "Готово." and policy.ran == ["echo"] and state.rounds == 2
    assert [s["tool"] for s in state.steps] == ["_say", "echo"]
    # Модель видит ответ инструмента.
    assert gemini.requests[1]["contents"][-1]["parts"][0]["functionResponse"]["response"]["echo"] == {"a": 1}


def test_loop_limit_stops_tools_and_does_not_run_late_calls():
    gemini = Scripted([[_call("echo")], [_call("echo")], [{"text": "Итог."}, _call("echo")]])
    policy = EchoPolicy()
    text, state = run_loop(policy, gemini, "m", _contents(), limit=2)
    assert policy.ran == ["echo", "echo"], "после лимита вызовы модели не выполняются"
    assert text == "Итог." and "tools" not in gemini.requests[2]
    assert "Лимит шагов" in gemini.requests[2]["contents"][-1]["parts"][0]["text"]


def test_loop_cancel_before_tools():
    with pytest.raises(TurnCancelled):
        run_loop(EchoPolicy(), Scripted([]), "m", _contents(), limit=2, should_cancel=lambda: True)
    assert editor_agent.AgentCancelled is TurnCancelled and C.ChatCancelled is TurnCancelled


def test_loop_blocked_goes_to_policy():
    class Blocked(Scripted):
        def generate_content(self, model, payload):
            return {"candidates": [], "promptFeedback": {"blockReason": "OTHER"}}

    text, _state = run_loop(EchoPolicy(), Blocked([]), "m", _contents(), limit=2)
    assert text == "фильтр OTHER"


def test_loop_empty_answer_is_honest_error():
    class Empty(Scripted):
        def generate_content(self, model, payload):
            return {"candidates": []}

    with pytest.raises(RuntimeError, match="модель не ответила"):
        run_loop(EchoPolicy(), Empty([]), "m", _contents(), limit=2)


def test_loop_retries_schema_then_signature():
    """Обе лечимые ошибки в одном запросе: сначала схема, потом подпись вызова."""
    contents = _contents() + [{"role": "model", "parts": [_call("echo")]},
                              {"role": "user", "parts": [{"text": "дальше"}]}]
    gemini = Scripted([[{"text": "Готово."}]])
    errors = iter(["unknown field parametersJsonSchema", "Function call is missing a thought_signature"])

    def flaky(model, payload):
        gemini.requests.append(copy.deepcopy(payload))
        error = next(errors, None)
        if error:
            raise RuntimeError(error)
        return _reply([{"text": "Готово."}])

    gemini.generate_content = flaky
    text, _state = run_loop(EchoPolicy(), gemini, "m", contents, limit=2)
    assert text == "Готово." and len(gemini.requests) == 3
    assert "parametersJsonSchema" in gemini.requests[0]["tools"][0]["functionDeclarations"][0]
    assert "parameters" in gemini.requests[1]["tools"][0]["functionDeclarations"][0]
    assert gemini.requests[2]["contents"][1]["parts"][0]["thoughtSignature"] == SKIP_SIGNATURE


def test_signature_error_without_calls_to_patch_is_raised():
    assert patch_thought_signatures(_contents()) is False
    gemini = Scripted([], failures={1: ["missing a thought_signature"]})
    with pytest.raises(RuntimeError, match="thought_signature"):
        run_loop(EchoPolicy(), gemini, "m", _contents(), limit=2)


def test_loop_nudge_clears_live_text():
    """Дожатая модель: напечатанное в этом раунде — не ответ, в живом ходе его не остаётся."""

    class Nudging(EchoPolicy):
        told = False

        def on_answer(self, state, parts, calls, tools_on, candidate):
            if not self.told:
                self.told = True
                state.nudge("продолжай")
                return None
            return "Конец."

    class Streaming:
        def __init__(self):
            self.turns = [[{"text": "Черновик"}], [{"text": "Конец."}]]

        def stream_generate_content(self, model, payload):
            for part in self.turns.pop(0):
                yield {"candidates": [{"content": {"parts": [part]}}]}

    run = C._Run(1, None, 1)
    texts = []
    original = run.take_text
    run.take_text = lambda: texts.append(original())
    text, _state = run_loop(Nudging(), Streaming(), "m", _contents(), limit=3, run=run)
    assert text == "Конец." and texts == ["Черновик"] and run.text == "Конец."


# --------------------------------------------------------------------------- #
# Оба чата: один цикл, повтор с подписью — у всех уровней
# --------------------------------------------------------------------------- #


@pytest.fixture
def env(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    monkeypatch.setattr("app.ai.editor_agent.client_factory", None)
    source = store.create_source("upload", settings.source_dir / "mura.mp4", original_filename="mura.mp4",
                                 duration_sec=3600, status="ready")
    pid = P.create_project(store, "Мура", kind="stream_channel", source_ids=[source["id"]])["id"]
    return client, store, source, pid


def _signature_turns():
    """Первый ответ — вызов без подписи; следующий запрос падает «нет подписи», пока её не поставим."""
    return Scripted([[_call("list_projects")], [{"text": "Вот что есть."}]],
                    failures={2: ["Function call is missing a thought_signature"]})


@pytest.mark.parametrize("level", ["general", "project", "video"])
def test_thought_signature_retry_on_every_thread_level(env, level):
    _client_, store, source, pid = env
    if level == "general":
        thread = C.create_thread(store)
    elif level == "project":
        thread = C.default_thread(store, pid)
    else:
        thread = C.video_thread(store, source["id"])
    gemini = _signature_turns()
    C.send_to_thread(store, thread["id"], "что есть?", client=gemini)
    assert C.wait_thread(thread["id"], 20)
    reply = C.thread_messages(store, thread["id"])[-1]
    assert reply["status"] == "done" and reply["text"] == "Вот что есть."
    model_turn = next(c for c in gemini.requests[-1]["contents"] if c["role"] == "model")
    assert model_turn["parts"][0]["thoughtSignature"] == "skip_thought_signature_validator"


def test_thread_say_filter_is_shared_with_editor(env):
    """Мусор между шагами владельцу не показываем и в чате с агентом — как в монтажёре."""
    _client_, store, _source, pid = env
    thread = C.default_thread(store, pid)
    gemini = Scripted([[{"text": "Let's look at this project first and then think about it."}, _call("list_projects")],
                       [{"text": "Смотрю дальше."}, _call("list_projects")], [{"text": "Готово."}]])
    C.send_to_thread(store, thread["id"], "что есть?", client=gemini)
    assert C.wait_thread(thread["id"], 20)
    reply = C.thread_messages(store, thread["id"])[-1]
    says = [s["label"] for s in reply["steps"] if s["tool"] == "_say"]
    assert says == ["Смотрю дальше."] and all(s["kind"] == "say" for s in reply["steps"] if s["tool"] == "_say")


def test_editor_and_thread_use_the_same_loop(env, monkeypatch):
    _client_, store, source, pid = env
    seen = []
    real = agent_loop.run_loop

    def spy(policy, *args, **kwargs):
        seen.append(type(policy).__name__)
        return real(policy, *args, **kwargs)

    monkeypatch.setattr("app.ai.editor_agent.run_loop", spy)
    monkeypatch.setattr("app.project_chat.run_loop", spy)
    thread = C.default_thread(store, pid)
    C.send_to_thread(store, thread["id"], "как дела?", client=Scripted([[{"text": "Хорошо."}]]))
    assert C.wait_thread(thread["id"], 20)
    analysis = store.create_ai_analysis(source["id"], "manual", status="succeeded")
    seg = store.create_ai_segment(analysis["id"], {"start_sec": 10, "end_sec": 25, "title": "момент"})
    plan = store.create_clip_plan(source["id"], analysis["id"], "момент", segment_ids=[seg["id"]])
    out = editor_agent.run_agent(store, plan["id"], "что тут?", client=Scripted([[{"text": "Тут момент."}]]), model="m")
    assert out["text"] == "Тут момент." and seen == ["ThreadPolicy", "ClipPolicy"]
