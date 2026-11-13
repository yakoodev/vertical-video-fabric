"""Конец хода агента в чате (повтор эксперимента 13.11): без рендера не сдаёт, чужой клип за свой не выдаёт,
render_status понимает оба номера рендера, get_clip/list_clips говорят, кто делал клип."""

from __future__ import annotations

import pytest

from app import agent_tools, render_jobs
from app.ai import finish_guard as G
from app.ai.agent_loop import run_loop
from test_agent_loop import EchoPolicy, Scripted, _call, _contents
from test_api_pipeline import _require_ffmpeg
from test_clip_spec import _setup

RC1 = ("Задание 1. Сделай рекламный ролик для TikTok про магазин vtubika.store. Вертикальный, 30–60 секунд, с озвучкой. "
       "Работай в проекте 9. Результат — отрендеренный клип в сервисе. Ничего не публикуй.")


# --------------------------------------------------------------------------- рендер до «готово»


def test_wants_render_by_task_words_or_playbook():
    assert G.wants_render(RC1)
    assert G.wants_render("собери готовый ролик")
    assert not G.wants_render("поправь хук на 2 секунде")
    assert not G.wants_render("сделай монтаж, но не рендерь пока")
    assert G.wants_render("нарежь", "stream-shorts")  # плейбук велит render_clip → render_status
    assert G.wants_render("серия", "series")


class GuardPolicy(EchoPolicy):
    """Хочет закончить — сервис дожимает, пока не дожал ``limit`` раз (сам цикл держит потолок)."""

    def __init__(self):
        super().__init__()
        self.asked = 0

    def finish_guard(self, state, answer):
        self.asked += 1
        return "Сервис: рендера нет — вызови render_clip и дождись render_status done"


def test_loop_guard_nudges_at_most_twice_then_finishes():
    gemini = Scripted([[{"text": "Осталось: render_clip → render_status."}],
                       [_call("echo")], [{"text": "Всё ещё не готово."}], [{"text": "Сдаю как есть."}]])
    policy = GuardPolicy()
    text, state = run_loop(policy, gemini, "m", _contents(), limit=1)
    assert text == "Сдаю как есть." and state.finish_nudges == G.MAX_FINISH_NUDGES == 2
    # После реплики сервиса модель снова с инструментами, хотя лимит раундов (1) уже вышел — добавка раундов.
    assert "tools" in gemini.requests[1] and policy.ran == ["echo"]
    nudges = [p["parts"][0].get("text", "") for p in gemini.requests[-1]["contents"] if p["role"] == "user"]
    assert sum("render_clip" in t for t in nudges) == 2


def test_loop_without_guard_finishes_at_once():
    gemini = Scripted([[{"text": "Готово."}]])
    text, state = run_loop(EchoPolicy(), gemini, "m", _contents(), limit=3)
    assert text == "Готово." and state.finish_nudges == 0 and gemini.calls == 1


def test_render_guard_by_render_state(monkeypatch):
    states = {"value": "none"}
    monkeypatch.setattr("app.ai.render_claims.render_state", lambda store, clip, since=None: states["value"])
    nudge = G.render_guard(object(), RC1, None, [1075], "2026-11-13 09:00:00")
    assert nudge and "render_clip" in nudge and "#1075" in nudge and "осталось" in nudge
    states["value"] = "running"
    assert "дождись render_status" in G.render_guard(object(), RC1, None, [1075], None)
    states["value"] = "fresh"
    assert G.render_guard(object(), RC1, None, [1075], None) is None
    states["value"] = "none"
    assert G.render_guard(object(), "поправь хук", None, [1075], None) is None  # рендер не просили
    assert G.render_guard(object(), RC1, None, [], None) is None  # клип в ходе не трогали


def test_thread_policy_guard_uses_changed_clips(monkeypatch):
    from app.ai.agent_loop import TurnState
    from app.project_chat import ThreadPolicy

    monkeypatch.setattr("app.ai.render_claims.render_state", lambda store, clip, since=None: "none")
    monkeypatch.setattr("app.ai.render_claims.db_now", lambda store: "2026-11-13 09:00:00")
    policy = ThreadPolicy(object(), {"id": 1, "project_id": 9}, [], RC1, None, "")
    state = TurnState(contents=[], emit=lambda *a: None)
    assert policy.finish_guard(state, "Собрал") is None  # клипов не трогал
    state.steps.append({"tool": "create_canvas_clip", "ok": True, "kind": "tool", "clip_id": 1075})
    assert "#1075" in policy.finish_guard(state, "Собрал. Осталось: render_clip → render_status")


# --------------------------------------------------------------------------- чужие клипы


class _Store:
    def __init__(self, clips):
        self.clips = set(clips)

    def get_clip_plan(self, clip_id, include_segments=True):
        if int(clip_id) not in self.clips:
            raise KeyError(clip_id)
        return {"id": int(clip_id)}


RC3_REPLY = ("Серия 3 интерактивного проекта «Выжить psx» готова и полностью отрендерена.\n"
             "- **Клип:** [клип #1074](clip:1074) — «Серия 3 «Замри и перепрячься»»\n"
             "- **Рендер:** `render_clip_id: 1050` (status: done)")


def test_foreign_clip_presented_as_result_gets_a_note():
    store = _Store({1074, 1050})
    note = G.foreign_note(store, RC3_REPLY, own=set())
    assert "#1074" in note and "не в этой переписке" in note and "#1050" not in note  # render_clip_id — не клип
    assert G.foreign_note(store, RC3_REPLY, own={1074}) == ""  # свой клип
    honest = "Нашёл клип #1074 — его сделал другой агент, не в этой переписке. Свою серию собрал в клипе #1080, готово."
    assert G.foreign_note(_Store({1074, 1080}), honest, own={1080}) == ""
    assert G.foreign_note(store, "Посмотрел клип #1074: хук слабый.", own=set()) == ""  # не результат
    assert G.mentioned_clips("клип 1076, холст #1075, /clips/1077")[0][0] == 1076


def test_thread_clips_from_steps_without_db():
    class NoDb:
        db = None

    steps = [{"tool": "create_clip", "ok": True, "kind": "tool", "clip_id": 1076},
             {"tool": "get_clip", "ok": True, "kind": "tool", "clip_id": 1074}]
    assert G.thread_clips(NoDb(), 46, steps) == {1076}


def test_clip_origin_from_threads_and_history(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, _source, plan, *_ = _setup(tmp_path, monkeypatch)
    from app import project_chat as C

    thread = C.create_thread(store, None, title="Задание 2")
    reply = C.add_thread_message(store, thread["id"], "assistant", "Создал клип")
    C.update_message(store, reply, steps=[{"tool": "create_clip", "ok": True, "kind": "tool", "clip_id": plan["id"]}])
    store.push_clip_plan_history(plan["id"], {}, "агент: правлю слой", depth=10)
    origin = G.clip_origin(store, plan["id"])
    assert origin["created_in"]["thread_id"] == thread["id"] and origin["edits"] == {"mcp": 1}
    label = G.origin_label(origin)
    assert f"переписке #{thread['id']}" in label and "MCP" in label
    ctx = agent_tools.ToolContext(store=store, clip_plan_id=plan["id"])
    clip = agent_tools.run_tool(ctx, "get_clip", {})[0]
    assert clip["made_in"]["created_in"]["thread_id"] == thread["id"]
    rows = agent_tools.run_tool(agent_tools.ToolContext(store=store), "list_clips", {"source_id": plan["source_id"]})[0]
    assert any(r.get("made_in", "").startswith("создан в переписке") for r in rows["clips"])


# --------------------------------------------------------------------------- два номера рендера


@pytest.fixture()
def _jobs():
    render_jobs.reset()
    yield
    render_jobs.reset()


def test_render_status_accepts_numeric_render_clip_id(tmp_path, monkeypatch, _jobs):
    _require_ffmpeg()
    _client, store, source, plan, *_ = _setup(tmp_path, monkeypatch)
    clip = store.create_clip(source["id"], clip_plan_id=plan["id"], status="queued")
    from app.settings import settings

    store.finish_clip_render(clip["id"], "succeeded", output_path=settings.clip_dir / "x.mp4", duration_sec=2)
    ctx = agent_tools.ToolContext(store=store)
    out, ok = agent_tools.run_tool(ctx, "render_status", {"render_id": str(clip["id"])})
    assert ok and out["status"] == "done" and out["render_clip_id"] == clip["id"] and out["clip_id"] == plan["id"]
    assert "номер готового рендера" in out["note"] and out["video_url"] == f"/media/clips/{clip['id']}"
    missing, ok2 = agent_tools.run_tool(ctx, "render_status", {"render_id": "999999"})
    assert not ok2 and "render_clip_id" in str(missing)
    view = agent_tools.run_tool(agent_tools.ToolContext(store=store, clip_plan_id=plan["id"]), "get_clip", {})[0]
    assert view["renders"]["render_clip_id"] == clip["id"] and "render_status" in view["renders"]["render_ids_note"]


def test_render_ids_label_both_numbers(_jobs, monkeypatch):
    monkeypatch.setattr(render_jobs, "_jobs", {"rab": {"render_id": "rab", "clip_id": 5, "status": "done",
                                                       "created_at": 1.0}})
    ids = render_jobs.render_ids(5, 1047)
    assert ids["render_clip_id"] == 1047 and ids["render_job_id"] == "rab" and "любой" in ids["render_ids_note"]
