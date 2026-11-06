"""Уровни вопросов (проект, видео, клип), пауза монтажа и перенос ответа между уровнями."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from app import agent_tools as T
from app import project_knowledge as K
from app import projects as P
from app import stream_questions as Q
from app.agent_tools import ToolContext, run_tool
from app.db import Database
from app.manual_clips import create_manual_clip
from app.settings import settings
from app.crypto import CookieCipher
from app.store import AppStore
from test_api_pipeline import _client

ROOT = Path(__file__).resolve().parent.parent
BEARER = {"Authorization": "Bearer whatever"}


def _ask(**over):
    return {"kind": "context", "t_start": 2, "t_end": 5, "question": "Что тут происходит?", "options": ["Шутка", "Серьёзно"],
            **over}


def _make_source(store, name, duration):
    return store.create_source("upload", settings.source_dir / name, original_filename=name, duration_sec=duration)


def _clip(store, source_id, title="Клип"):
    return create_manual_clip(store, source_id, title, [{"start_sec": 1, "end_sec": 20}])


@pytest.fixture()
def setup(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    source = _make_source(store, "a.mp4", 120.0)
    project = P.create_project(store, "Мура", source_ids=[source["id"]])
    return client, store, source["id"], project["id"]


def _notes(store, project_id):
    return P.get_brief(store, project_id)["brief"]["notes"]


def _people(store, project_id):
    return [(p["name"], p["who"]) for p in P.get_brief(store, project_id)["brief"]["people"]]


# ------------------------------------------------------------------ пауза


def test_gate_refuses_montage_tools_and_names_the_questions(setup):
    _client_, store, sid, _pid = setup
    clip = _clip(store, sid)
    qid = Q.add_questions(store, sid, [_ask(question="Кто это кричит?")])["added"][0]
    ctx = ToolContext(store=store)
    out, ok = run_tool(ctx, "create_clip", {"project_id": sid, "title": "Новая", "pieces": [{"start_sec": 1, "end_sec": 9}]})
    assert not ok and out["paused"] == "open_questions" and qid in out["open_questions"]
    # То же под каноническим именем: пауза не должна зависеть от того, как назван источник.
    out, ok = run_tool(ctx, "create_clip", {"source_id": sid, "title": "Новая", "pieces": [{"start_sec": 1, "end_sec": 9}]})
    assert not ok and out["paused"] == "open_questions" and qid in out["open_questions"]
    assert "Кто это кричит?" in json.dumps(out, ensure_ascii=False) and "Монтировать без ответов" in out["error"]
    assert store.db.query_one("SELECT COUNT(*) AS n FROM clip_plans WHERE source_id = ?", (sid,))["n"] == 1
    # Новая версия клипа тоже монтаж: так и вышло три горизонталки.
    out, ok = run_tool(ctx, "create_clip", {"project_id": sid, "title": "v2", "version_of": clip["id"],
                                            "pieces": [{"start_sec": 1, "end_sec": 9}]})
    assert not ok and out["paused"]
    clip_ctx = ToolContext(store=store, clip_plan_id=clip["id"])
    for name, args in [("apply_cuts", {"cuts": []}), ("suggest_cuts", {}), ("auto_edit", {}),
                       ("trim_piece", {"index": 0, "start_sec": 1, "end_sec": 10})]:
        out, ok = run_tool(clip_ctx, name, args)
        assert not ok and out.get("paused") == "open_questions", name
    out, ok = run_tool(ToolContext(store=store), "make_compilation", {"clip_ids": [clip["id"], clip["id"]]})
    assert not ok and out["paused"]
    out, ok = run_tool(ToolContext(store=store), "add_video_from_source", {"source_id": sid, "start": 1, "end": 5})
    assert not ok and out["paused"]


def test_gate_leaves_other_tools_and_chat_analyze_is_refused(setup):
    _client_, store, sid, _pid = setup
    clip = _clip(store, sid)
    Q.add_questions(store, sid, [_ask()])
    # Смотреть и править готовое можно
    out, ok = run_tool(ToolContext(store=store, clip_plan_id=clip["id"]), "get_clip", {})
    assert ok, out
    out, ok = run_tool(ToolContext(store=store, clip_plan_id=clip["id"]), "set_end_hold", {"seconds": 0})
    assert out.get("paused") is None
    # Спросить ещё можно
    out, ok = run_tool(ToolContext(store=store), "add_stream_questions", {"source_id": sid, "questions": [_ask(question="Ещё?")]})
    assert ok and out["montage_paused"] is True and out["waiting_for"] == 2

    from app import chat_tools as CT

    out, ok, _clip_id = CT.run(store, "analyze_source", {"source_id": sid}, project_id=None)
    assert not ok and out["paused"] == "open_questions"


def test_gate_lifted_by_answer_skip_optional_and_release(setup):
    client, store, sid, _pid = setup
    create = lambda: run_tool(ToolContext(store=store), "create_clip",  # noqa: E731
                              {"project_id": sid, "title": "Новая", "pieces": [{"start_sec": 1, "end_sec": 9}]})
    a, b = Q.add_questions(store, sid, [_ask(question="Первый?"), _ask(question="Второй?")])["added"]
    Q.add_questions(store, sid, [_ask(question="Уточнение?", optional=True)])
    assert Q.montage_gate(store, sid)["open"] == 2
    Q.answer_question(store, a, 0)
    assert not create()[1]
    Q.answer_question(store, b, skip=True)
    assert create()[1]  # необязательный вопрос не держит

    # Снятый ответ снова держит
    Q.answer_question(store, a, None, "")
    assert not create()[1]

    # Кнопка владельца
    gate = client.post(f"/api/sources/{sid}/montage-without-answers").json()
    assert gate["paused"] is False and gate["released"] is True and gate["released_open"] == 1
    assert create()[1]
    # Новый вопрос после кнопки снова останавливает: его владелец ещё не видел
    Q.add_questions(store, sid, [_ask(question="Новый после кнопки?")])
    assert not create()[1]
    assert client.post(f"/api/sources/{sid}/montage-without-answers").json()["paused"] is False
    assert create()[1]
    # Передумал
    gate = client.delete(f"/api/sources/{sid}/montage-without-answers").json()
    assert gate["paused"] is True and gate["released"] is False
    assert not create()[1]


def test_old_video_without_questions_is_unaffected(setup):
    _client_, store, sid, _pid = setup
    assert Q.montage_gate(store, sid) == {"paused": False, "open": 0, "project_open": 0, "clip_open": 0, "items": [],
                                          "released": False, "released_open": 0}
    out, ok = run_tool(ToolContext(store=store), "create_clip",
                       {"project_id": sid, "title": "Новая", "pieces": [{"start_sec": 1, "end_sec": 9}]})
    assert ok, out


def test_gate_does_not_leak_to_other_video_or_project(setup):
    _client_, store, sid, pid = setup
    same_project = _make_source(store, "b.mp4", 120.0)["id"]
    P.set_source_project(store, same_project, pid)
    other_project = _make_source(store, "c.mp4", 120.0)["id"]
    P.create_project(store, "Другой", source_ids=[other_project])
    Q.add_questions(store, sid, [_ask(question="Кто такой Глеб?", level="project", kind="who", about="Глеб"), _ask()])
    assert Q.montage_gate(store, sid)["paused"] is True
    for other in (same_project, other_project):
        out, ok = run_tool(ToolContext(store=store), "create_clip",
                           {"project_id": other, "title": "Своя", "pieces": [{"start_sec": 1, "end_sec": 9}]})
        assert ok, out
    # Кнопка одного видео не снимает паузу другого
    Q.add_questions(store, same_project, [_ask(question="Там своё?")])
    Q.release_montage(store, sid)
    assert Q.montage_gate(store, sid)["paused"] is False and Q.montage_gate(store, same_project)["paused"] is True


def test_clip_question_holds_only_that_clip(setup):
    _client_, store, sid, _pid = setup
    first, second = _clip(store, sid, "Первый"), _clip(store, sid, "Второй")
    Q.add_questions(store, sid, [_ask(question="Как начать этот клип?", level="clip", clip_id=first["id"])])
    assert Q.montage_gate(store, sid)["paused"] is False
    assert Q.montage_gate(store, sid, second["id"])["paused"] is False
    assert Q.montage_gate(store, sid, first["id"])["paused"] is True
    out, ok = run_tool(ToolContext(store=store, clip_plan_id=first["id"]), "apply_cuts", {"cuts": []})
    assert not ok and out["paused"]
    out, ok = run_tool(ToolContext(store=store, clip_plan_id=second["id"]), "apply_cuts", {"cuts": []})
    assert ok, out


def test_hand_editing_is_not_gated_and_token_is(setup, monkeypatch):
    client, store, sid, _pid = setup
    clip = _clip(store, sid)
    Q.add_questions(store, sid, [_ask()])
    piece = {"title": "x", "pieces": [{"start_sec": 1, "end_sec": 9}]}
    # Владелец в браузере: кнопки редактора работают
    assert client.post(f"/api/clip-plans/{clip['id']}/cuts/apply", json={"cuts": []}).status_code == 200
    assert client.post(f"/api/clip-plans/{clip['id']}/cuts/suggest", json={}).status_code != 409
    assert client.post(f"/api/sources/{sid}/manual-clips", json=piece).status_code == 200
    assert client.get(f"/api/clip-plans/{clip['id']}/spec").status_code == 200
    # То же по токену из скрипта или vvf.py: пауза
    for method, url, body in [
        ("post", f"/api/clip-plans/{clip['id']}/cuts/apply", {"cuts": []}),
        ("post", f"/api/clip-plans/{clip['id']}/cuts/suggest", {}),
        ("post", f"/api/clip-plans/{clip['id']}/auto-edit", {}),
        ("post", f"/api/sources/{sid}/manual-clips", piece),
        ("post", f"/api/sources/{sid}/analyze", {}),
        ("post", f"/api/sources/{sid}/compilation", {"plan_ids": [clip["id"], clip["id"]]}),
        ("post", f"/api/sources/{sid}/ai-pick", {}),
        ("post", f"/api/sources/{sid}/ai-montage-batch", {"clip_plan_ids": [clip["id"]]}),
    ]:
        response = getattr(client, method)(url, json=body, headers=BEARER)
        assert response.status_code == 409, (url, response.text)
        assert "Монтировать без ответов" in response.json()["detail"]
    # Рендер и просмотр по токену не на паузе
    assert client.get(f"/api/clip-plans/{clip['id']}/spec", headers=BEARER).status_code == 200


def test_agent_token_cannot_lift_the_gate(setup):
    client, store, sid, _pid = setup
    qid = Q.add_questions(store, sid, [_ask()])["added"][0]
    assert client.post(f"/api/sources/{sid}/montage-without-answers", headers=BEARER).status_code == 403
    assert client.put(f"/api/stream-questions/{qid}/answer", json={"skip": True}, headers=BEARER).status_code == 403
    assert client.put(f"/api/stream-questions/{qid}/level", json={"level": "project"}, headers=BEARER).status_code == 403
    assert Q.montage_gate(store, sid)["paused"] is True
    # Инструмента, снимающего паузу, нет ни у кого
    names = {t.name for t in T.list_tools(include_discovery=True, store=store)}
    assert not {n for n in names if "release" in n or "without_answers" in n}


def test_module_off_means_no_gate(setup):
    from app import modules

    _client_, store, sid, _pid = setup
    Q.add_questions(store, sid, [_ask()])
    modules.set_modules(store, {"stream_questions": False})
    try:
        out, ok = run_tool(ToolContext(store=store), "create_clip",
                           {"project_id": sid, "title": "Новая", "pieces": [{"start_sec": 1, "end_sec": 9}]})
        assert ok, out
    finally:
        modules.set_modules(store, {"stream_questions": True})


# ------------------------------------------------------------------ уровни


def test_levels_when_asking(setup):
    _client_, store, sid, _pid = setup
    clip = _clip(store, sid)
    ids = Q.add_questions(store, sid, [
        _ask(question="Про проект?", level="project"),
        _ask(question="Про видео?", level="video"),
        _ask(question="Про клип?", level="clip", clip_id=clip["id"]),
        _ask(question="Старый канал?", scope="channel"),
        _ask(question="Старый стрим?", scope="stream"),
        _ask(question="Без уровня?"),
    ])["added"]
    levels = [Q.get_question(store, i)["level"] for i in ids]
    assert levels == ["project", "video", "clip", "project", "video", "video"]
    with pytest.raises(ValueError, match="level"):
        Q.add_questions(store, sid, [_ask(question="Плохой?", level="x")])
    with pytest.raises(ValueError, match="clip_id"):
        Q.add_questions(store, sid, [_ask(question="Клип без клипа?", level="clip")])
    other = _make_source(store, "b.mp4", 120.0)["id"]
    foreign = _clip(store, other)
    with pytest.raises(ValueError, match="не из этого видео"):
        Q.add_questions(store, sid, [_ask(question="Чужой клип?", level="clip", clip_id=foreign["id"])])


def test_each_page_shows_only_its_own(setup):
    client, store, sid, pid = setup
    clip, other_clip = _clip(store, sid, "Первый"), _clip(store, sid, "Второй")
    Q.add_questions(store, sid, [
        _ask(question="Про проект?", level="project"),
        _ask(question="Про видео?", level="video"),
        _ask(question="Про клип?", level="clip", clip_id=clip["id"]),
    ])
    page = client.get(f"/api/sources/{sid}/stream-questions").json()
    assert [q["question"] for q in page["questions"]] == ["Про видео?"]
    assert page["progress"]["questions"]["open"] == 1
    assert page["elsewhere"]["project"] == {"total": 1, "open": 1}
    assert [(c["id"], c["open"]) for c in page["elsewhere"]["clips"]] == [(clip["id"], 1)]
    assert page["gate"]["open"] == 2 and page["gate"]["project_open"] == 1

    state = client.get(f"/api/clip-plans/{clip['id']}/stream-questions").json()
    assert [q["question"] for q in state["questions"]] == ["Про клип?"] and state["source_id"] == sid
    assert client.get(f"/api/clip-plans/{other_clip['id']}/stream-questions").json()["questions"] == []
    assert client.get("/api/clip-plans/99999/stream-questions").status_code == 404

    inbox = client.get(f"/api/stream-questions?project_id={pid}&only_open=true").json()
    assert inbox["counts"]["project"]["open"] == inbox["counts"]["video"]["open"] == inbox["counts"]["clip"]["open"] == 1
    assert inbox["clips"][0]["id"] == clip["id"] and inbox["sources"][0]["paused"] is True


def test_video_without_project_keeps_project_questions_on_its_page(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    sid = _make_source(store, "a.mp4", 120.0)["id"]
    Q.add_questions(store, sid, [_ask(question="Про проект?", level="project")])
    page = client.get(f"/api/sources/{sid}/stream-questions").json()
    assert [q["question"] for q in page["questions"]] == ["Про проект?"] and page["elsewhere"]["project"]["total"] == 0


def test_clip_question_follows_versions(setup):
    _client_, store, sid, _pid = setup
    clip = _clip(store, sid, "Мура v1")
    version = create_manual_clip(store, sid, "Мура v2", [{"start_sec": 1, "end_sec": 15}], version_of=clip["id"])
    qid = Q.add_questions(store, sid, [_ask(question="Как начать?", level="clip", clip_id=clip["id"])])["added"][0]
    assert [q["id"] for q in Q.clip_state(store, version["id"])["questions"]] == [qid]
    assert Q.montage_gate(store, sid, version["id"])["paused"] is True


def test_project_question_is_asked_once_per_project(setup):
    _client_, store, sid, pid = setup
    second = _make_source(store, "b.mp4", 120.0)["id"]
    P.set_source_project(store, second, pid)
    first = Q.add_questions(store, sid, [_ask(question="Кто такой Глеб?", level="project")])["added"][0]
    out = Q.add_questions(store, second, [_ask(question="Кто такой Глеб?", level="project")])
    assert out == {"added": [], "duplicates": [first]}
    assert Q.add_questions(store, second, [_ask(question="Кто такой Глеб?", level="video")])["added"]


# ------------------------------------------------------------------ смена уровня и бриф


@pytest.mark.parametrize("kind,about,reader", [
    ("who", "Онигуру", lambda store, pid: _people(store, pid)),
    ("joke", "попое", lambda store, pid: P.get_brief(store, pid)["brief"]["jokes"]),
    ("context", "", lambda store, pid: _notes(store, pid)),
])
def test_flip_moves_answer_both_ways_without_duplicates(setup, kind, about, reader):
    client, store, sid, pid = setup
    qid = Q.add_questions(store, sid, [_ask(question="Кто такой Онигуру?", kind=kind, about=about, level="video",
                                            options=["Помогает с модом", "Зритель"])])["added"][0]
    Q.answer_question(store, qid, 0, "и подпись такая")
    assert not reader(store, pid)
    for _round in range(3):
        fresh = client.put(f"/api/stream-questions/{qid}/level", json={"level": "project"}).json()
        assert fresh["level"] == "project" and fresh["brief"]["written"] is True
        assert fresh["answer"] == "Помогает с модом. и подпись такая" and fresh["status"] == "answered"
        again = client.put(f"/api/stream-questions/{qid}/level", json={"level": "project"}).json()
        assert again["brief"] == {"written": False}
        text = json.dumps(reader(store, pid), ensure_ascii=False)
        assert text.count("Помогает с модом") == 1
        view = K.knowledge_view(store, pid)
        assert not [g for g in view["groups"] if g["key"] == "videos"]
        fresh = client.put(f"/api/stream-questions/{qid}/level", json={"level": "video"}).json()
        assert fresh["level"] == "video" and fresh["answer"] == "Помогает с модом. и подпись такая"
        assert not reader(store, pid)
        view = K.knowledge_view(store, pid)
        assert [g["items"][0]["ref"] for g in view["groups"] if g["key"] == "videos"] == [f"question:{qid}"]


def test_flip_of_unanswered_and_answering_after(setup):
    client, store, sid, pid = setup
    qid = Q.add_questions(store, sid, [_ask(question="Кто такой Глеб?", level="video")])["added"][0]
    assert client.put(f"/api/stream-questions/{qid}/level", json={"level": "project"}).json()["brief"] == {"written": False}
    assert client.put(f"/api/stream-questions/{qid}/level", json={"level": "x"}).status_code == 400
    assert client.put("/api/stream-questions/99999/level", json={"level": "video"}).status_code == 404
    Q.answer_question(store, qid, 0)
    assert _notes(store, pid) == "Кто такой Глеб? — Шутка"
    # Снял ответ — строка ушла, ответил снова — одна
    Q.answer_question(store, qid, None, "")
    assert _notes(store, pid) == ""
    Q.answer_question(store, qid, 1)
    assert _notes(store, pid) == "Кто такой Глеб? — Серьёзно"


def test_flip_keeps_what_others_wrote_about_a_person(setup):
    _client_, store, sid, pid = setup
    P.update_brief(store, pid, {"people": [{"name": "Глеб", "traits": "басит", "aliases": ["Глебыч"]}]})
    qid = Q.add_questions(store, sid, [_ask(question="Кто такой Глеб?", kind="who", about="Глеб", level="project",
                                            options=["Модер", "Друг"])])["added"][0]
    Q.answer_question(store, qid, 0)
    Q.set_level(store, qid, "video")
    person = P.get_brief(store, pid)["brief"]["people"][0]
    assert person["who"] == "" and person["traits"] == "басит" and person["aliases"] == ["Глебыч"]


def test_flip_does_not_remove_row_someone_changed_and_restores_other_answer(setup):
    _client_, store, sid, pid = setup
    second = _make_source(store, "b.mp4", 120.0)["id"]
    P.set_source_project(store, second, pid)
    a = Q.add_questions(store, sid, [_ask(question="Кто такой Глеб?", kind="who", about="Глеб", level="project",
                                          options=["Модер", "Друг"])])["added"][0]
    b = Q.add_questions(store, second, [_ask(question="А Глеб это кто?", kind="who", about="Глеб", level="project",
                                             options=["Сосед", "Брат"])])["added"][0]
    Q.answer_question(store, a, 0)
    Q.answer_question(store, b, 0)
    assert _people(store, pid) == [("Глеб", "Сосед")]
    # Первый ответ уже не в брифе: его перенос строку другого не трогает
    Q.set_level(store, a, "video")
    assert _people(store, pid) == [("Глеб", "Сосед")]
    Q.set_level(store, a, "project")
    assert _people(store, pid) == [("Глеб", "Модер")]
    # Убрал последний: снова действует ответ второго видео, строка одна
    Q.set_level(store, a, "video")
    assert _people(store, pid) == [("Глеб", "Сосед")]


def test_multiline_answer_leaves_no_tail_in_notes(setup):
    _client_, store, sid, pid = setup
    qid = Q.add_questions(store, sid, [_ask(question="Что за сервер?", level="project")])["added"][0]
    Q.answer_question(store, qid, None, "свой\nи ещё строка")
    assert _notes(store, pid) == "Что за сервер? — свой и ещё строка"
    Q.set_level(store, qid, "video")
    assert _notes(store, pid) == ""


def test_many_project_answers_all_reach_the_notes(setup):
    _client_, store, sid, pid = setup
    ids = Q.add_questions(store, sid, [_ask(question=f"Длинный вопрос про порядки на канале номер {i}? " + "х" * 200,
                                            level="project") for i in range(25)])["added"]
    out = [Q.answer_question(store, qid, 0) for qid in ids]
    assert all(o["brief"]["written"] for o in out)
    assert len(_notes(store, pid).splitlines()) == 25


def test_flip_to_project_without_project_keeps_answer_on_the_video(tmp_path, monkeypatch):
    _client_, store = _client(tmp_path, monkeypatch)
    sid = _make_source(store, "a.mp4", 120.0)["id"]
    qid = Q.add_questions(store, sid, [_ask()])["added"][0]
    Q.answer_question(store, qid, 0)
    fresh = Q.set_level(store, qid, "project")
    assert fresh["brief"]["written"] is False and "не в проекте" in fresh["brief"]["reason"] and fresh["answer"] == "Шутка"


def test_flip_back_to_clip_remembers_the_clip(setup):
    _client_, store, sid, _pid = setup
    clip = _clip(store, sid)
    qid = Q.add_questions(store, sid, [_ask(level="clip", clip_id=clip["id"])])["added"][0]
    assert Q.set_level(store, qid, "video")["level"] == "video"
    assert Q.montage_gate(store, sid)["paused"] is True
    back = Q.set_level(store, qid, "clip")
    assert back["level"] == "clip" and back["clip_plan_id"] == clip["id"]
    assert Q.montage_gate(store, sid)["paused"] is False


def test_clip_answer_stays_with_the_clip_in_prompts(setup):
    _client_, store, sid, pid = setup
    clip, other = _clip(store, sid, "Первый"), _clip(store, sid, "Второй")
    qid = Q.add_questions(store, sid, [_ask(question="Как начать клип?", level="clip", clip_id=clip["id"])])["added"][0]
    video = Q.add_questions(store, sid, [_ask(question="Что за стрим?")])["added"][0]
    project = Q.add_questions(store, sid, [_ask(question="Что за канал?", level="project")])["added"][0]
    for question in (qid, video, project):
        Q.answer_question(store, question, 0)
    assert "Как начать клип?" in K.block_for_plan(store, clip["id"])
    assert "Как начать клип?" not in K.block_for_plan(store, other["id"])
    assert "Как начать клип?" not in K.block_for_source(store, sid)
    # Ответ про видео в запросах только этого видео, про проект везде в проекте
    second = _make_source(store, "b.mp4", 120.0)["id"]
    P.set_source_project(store, second, pid)
    assert "Что за стрим?" in K.block_for_source(store, sid) and "Что за стрим?" not in K.block_for_source(store, second)
    assert "Что за канал?" in K.block_for_source(store, second)
    answers = Q.answers_brief(store, sid)
    assert {a["question"]: a["level"] for a in answers["answers"]} == {"Как начать клип?": "clip", "Что за стрим?": "video",
                                                                        "Что за канал?": "project"}
    assert answers["montage"]["paused"] is False


def test_knowledge_links_project_answer_to_project_tab(setup):
    _client_, store, sid, pid = setup
    qid = Q.add_questions(store, sid, [_ask(question="Кто такой Глеб?", kind="who", about="Глеб", level="project")])["added"][0]
    Q.answer_question(store, qid, 0)
    people = next(g for g in K.knowledge_view(store, pid)["groups"] if g["key"] == "people")
    assert people["items"][0]["href"] == f"/p/{pid}?tab=questions"


def test_release_table_goes_with_the_source(setup):
    client, store, sid, _pid = setup
    Q.add_questions(store, sid, [_ask()])
    Q.release_montage(store, sid)
    client.delete(f"/api/sources/{sid}").raise_for_status()
    assert store.db.query_one("SELECT COUNT(*) AS n FROM stream_montage_release")["n"] == 0


def test_new_routes_need_login(setup, monkeypatch):
    client, _store, sid, _pid = setup
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "api_token", "secret-token")
    client.cookies.clear()
    for method, url in [("post", f"/api/sources/{sid}/montage-without-answers"),
                        ("delete", f"/api/sources/{sid}/montage-without-answers"),
                        ("get", "/api/clip-plans/1/stream-questions")]:
        assert getattr(client, method)(url).status_code == 401, url
    assert client.put("/api/stream-questions/1/level", json={}).status_code == 401


# ------------------------------------------------------------------ что агент заметил сам


def _note(store, project_id, text="Онигуру часто мелькает", **over):
    return run_tool(ToolContext(store=store), "note_observation", {"project_id": project_id, "text": text, **over})


def test_noticed_is_marked_and_confirm_makes_it_a_normal_fact(setup):
    client, store, sid, pid = setup
    out, ok = _note(store, pid, source_id=sid, quote="1:17:12")
    assert ok and out["created"] is True and out["confirmed"] is False
    # В фактах владельца его нет
    assert K.list_facts(store, pid) == []
    assert P.project_brief(store, pid)["facts"] == []
    assert P.project_brief(store, pid)["noticed"][0]["text"] == "Онигуру часто мелькает"
    view = K.knowledge_view(store, pid)
    assert view["groups"][0]["key"] == "noticed" and view["waiting"]["noticed"] == 1
    row = view["groups"][0]["items"][0]
    assert row["confirmable"] is True and row["href"] == f"/v/{sid}"
    # Повтор не плодит
    again, ok = _note(store, pid)
    assert ok and again["created"] is False

    fresh = client.post(f"/api/projects/{pid}/knowledge/confirm", json={"ref": row["ref"]}).json()
    assert not [g for g in fresh["groups"] if g["key"] == "noticed"] and fresh["waiting"]["noticed"] == 0
    facts = next(g for g in fresh["groups"] if g["key"] == "facts")["items"]
    assert facts[0]["text"] == "Онигуру часто мелькает" and "вы подтвердили" in facts[0]["origin"]
    assert "confirmable" not in facts[0]
    assert P.project_brief(store, pid)["noticed"] == []
    block = K.block_for_source(store, sid)
    assert "[не подтверждено]" not in block and "- Онигуру часто мелькает" in block
    # Подтверждённое агент не понижает
    out, ok = _note(store, pid)
    assert ok and out["confirmed"] is True


def test_noticed_removed_is_not_added_again(setup):
    client, store, sid, pid = setup
    out, _ok = _note(store, pid)
    ref = f"fact:{out['fact_id']}"
    assert client.delete(f"/api/projects/{pid}/knowledge", params={"ref": ref}).status_code == 200
    assert K.list_noticed(store, pid) == [] and K.prompt_block(store, pid) == ""
    out, ok = _note(store, pid)
    assert not ok and "убрал" in out["error"]
    out, ok = run_tool(ToolContext(store=store), "remember_fact", {"project_id": pid, "text": "Онигуру часто мелькает"})
    assert not ok
    assert K.list_noticed(store, pid) == [] and K.list_facts(store, pid) == []
    with pytest.raises(KeyError):
        K.confirm_fact(store, pid, int(ref[5:]))
    # Слова владельца возвращают
    assert K.add_fact(store, pid, "Онигуру часто мелькает", origin=K.ORIGIN_OWNER)["created"] is True
    assert K.list_facts(store, pid)[0]["status"] == "active"


def test_noticed_is_marked_in_prompts_after_what_owner_said(setup):
    _client_, store, sid, pid = setup
    K.add_fact(store, pid, "Глеб это модер канала")
    qid = Q.add_questions(store, sid, [_ask(question="Кто такой Саня?", kind="who", about="Саня", level="project")])["added"][0]
    Q.answer_question(store, qid, 0)
    _note(store, pid, "Глеб похоже просто зритель")
    block = K.block_for_source(store, sid)
    assert "- [не подтверждено] Глеб похоже просто зритель" in block and "НЕ подтвердил" in block
    assert "верь владельцу" in block
    # Сказанное владельцем идёт раньше, заметка — последней
    assert block.index("Глеб это модер") < block.index("[не подтверждено]")
    assert block.index("Саня") < block.index("[не подтверждено]")
    assert block.rstrip().splitlines()[-1].startswith("- [не подтверждено]")
    # Если места мало, выпадает заметка, а не слова владельца
    tight = K.prompt_block(store, pid, limit=len(block) - 30)
    assert "Глеб это модер" in tight and "[не подтверждено]" not in tight


def test_noticed_does_not_downgrade_or_leak(setup):
    client, store, sid, pid = setup
    K.add_fact(store, pid, "Глеб это модер канала")
    out, ok = _note(store, pid, "Глеб это модер канала")
    assert ok and out["created"] is False and out["confirmed"] is True
    assert K.list_noticed(store, pid) == []

    other_source = _make_source(store, "c.mp4", 120.0)["id"]
    other = P.create_project(store, "Другой", source_ids=[other_source])["id"]
    out, _ok = _note(store, pid, "Онигуру часто мелькает", source_id=sid)
    assert K.list_noticed(store, other) == []
    assert "Онигуру" not in K.block_for_source(store, other_source)
    assert "Онигуру" not in json.dumps(P.project_brief(store, other), ensure_ascii=False)
    # Чужая заметка не подтверждается из другого проекта
    response = client.post(f"/api/projects/{other}/knowledge/confirm", json={"ref": f"fact:{out['fact_id']}"})
    assert response.status_code == 404
    assert K.list_noticed(store, pid)[0]["status"] == K.STATUS_UNCONFIRMED
    # Видео другого проекта в заметку не подставить
    refused, ok = _note(store, pid, "Ещё заметка", source_id=other_source)
    assert not ok and "не из этого проекта" in refused["error"]
    # Чат проекта не пишет в чужой
    from app import chat_tools as CT

    out, ok, _clip = CT.run(store, "note_observation", {"project_id": other, "text": "Чужая заметка"}, project_id=pid)
    assert not ok and K.list_noticed(store, other) == []


def test_only_the_owner_confirms(setup):
    client, store, _sid, pid = setup
    out, _ok = _note(store, pid)
    ref = f"fact:{out['fact_id']}"
    assert client.post(f"/api/projects/{pid}/knowledge/confirm", json={"ref": ref}, headers=BEARER).status_code == 403
    assert client.patch(f"/api/projects/{pid}/knowledge", json={"ref": ref, "text": "Новый текст заметки"},
                        headers=BEARER).status_code == 403
    # remember_fact без слов владельца заметку не подтверждает, со словами владельца подтверждает
    result, ok = run_tool(ToolContext(store=store), "remember_fact", {"project_id": pid, "text": "Онигуру часто мелькает"})
    assert ok and result["created"] is False and "note" in result
    assert K.list_noticed(store, pid) and not K.list_facts(store, pid)
    # «запомни» в чате — это слова владельца
    K.remember_from_chat(store, pid, "запомни: Онигуру часто мелькает")
    assert not K.list_noticed(store, pid) and K.list_facts(store, pid)[0]["text"] == "Онигуру часто мелькает"


def test_owner_edit_of_noticed_confirms_it(setup):
    client, store, _sid, pid = setup
    out, _ok = _note(store, pid)
    response = client.patch(f"/api/projects/{pid}/knowledge", json={"ref": f"fact:{out['fact_id']}", "text": "Онигуру помогает с модом"})
    assert response.status_code == 200
    assert K.list_noticed(store, pid) == [] and K.list_facts(store, pid)[0]["text"] == "Онигуру помогает с модом"


# ------------------------------------------------------------------ скрипт для старых вопросов и миграция


def _script():
    spec = importlib.util.spec_from_file_location("stream_question_levels", ROOT / "tools" / "stream_question_levels.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _old_db(path: Path) -> None:
    """База как до этих правок: таблица вопросов без clip_plan_id и optional."""
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE sources (id INTEGER PRIMARY KEY);
            INSERT INTO sources (id) VALUES (337);
            CREATE TABLE stream_questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                kind TEXT NOT NULL DEFAULT 'context', t_start REAL NOT NULL DEFAULT 0, t_end REAL NOT NULL DEFAULT 0,
                question TEXT NOT NULL, question_key TEXT NOT NULL, options_json TEXT NOT NULL DEFAULT '[]',
                scope TEXT NOT NULL DEFAULT 'stream', about TEXT NOT NULL DEFAULT '', answer_option INTEGER,
                answer_text TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'open',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, answered_at TEXT, UNIQUE (source_id, question_key));
            """)
        rows = [
            (1, "who", "channel", "Кто это?", ["Да", "Нет"], "open", None, ""),
            (2, "keep", "stream", "Что из планов можно показывать?", ["Всё можно", "Ничего"], "open", None, ""),
            (3, "keep", "stream", "Берём момент?", ["Берём целиком", "Не берём"], "open", None, ""),
            (4, "keep", "channel", "Мем-пати, что делаем?", ["Берём как есть", "Не берём"], "answered", 1, "мой ответ"),
            (5, "keep", "channel", "Такое берём?", ["Берём", "Не берём"], "open", None, ""),
            (6, "other", "stream", "С чего начать?", ["А", "Б"], "answered", 0, ""),
        ]
        for rid, kind, scope, text, options, status, picked, own in rows:
            conn.execute(
                "INSERT INTO stream_questions (id, source_id, kind, question, question_key, options_json, scope, status, "
                "answer_option, answer_text) VALUES (?, 337, ?, ?, ?, ?, ?, ?, ?, ?)",
                (rid, kind, text, f"k{rid}", json.dumps(options, ensure_ascii=False), scope, status, picked, own))


def test_script_is_dry_by_default_and_apply_touches_only_level_of_open(tmp_path, capsys):
    script = _script()
    db = tmp_path / "copy.sqlite"
    _old_db(db)
    before = db.read_bytes()
    assert script.main(["--db", str(db)]) == 0
    assert db.read_bytes() == before  # только список, файл не тронут
    out = capsys.readouterr().out
    assert "сменить 1" in out and "кнопкой на карточке 1" in out

    def rows():
        with sqlite3.connect(db) as conn:
            conn.row_factory = sqlite3.Row
            return {r["id"]: dict(r) for r in conn.execute("SELECT * FROM stream_questions")}

    old = rows()
    assert script.main(["--db", str(db), "--apply"]) == 0
    new = rows()
    assert new[2]["scope"] == "channel"  # правило на будущее
    assert new[3]["scope"] == "stream" and new[5]["scope"] == "channel"
    assert new[4]["scope"] == "channel" and new[4]["answer_text"] == "мой ответ"  # отвеченное кнопкой, не скриптом
    for rid, row in old.items():
        changed = {k for k in row if row[k] != new[rid][k]}
        assert changed <= {"scope"}, rid
    assert [rid for rid in old if old[rid]["scope"] != new[rid]["scope"]] == [2]

    # Повтор ничего не меняет
    assert script.main(["--db", str(db), "--apply"]) == 0
    assert rows() == new


def test_script_skips_question_answered_meanwhile(tmp_path):
    script = _script()
    db = tmp_path / "copy.sqlite"
    _old_db(db)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    item = next(i for i in script.plan(script.load(conn, 337)) if i["action"] == "сменить")
    # Владелец ответил между чтением и записью
    conn.execute("UPDATE stream_questions SET status = 'answered', answer_option = 0 WHERE id = ?", (item["id"],))
    conn.commit()
    assert script.apply_level(conn, item) is False
    assert conn.execute("SELECT scope FROM stream_questions WHERE id = ?", (item["id"],)).fetchone()[0] == "stream"
    conn.close()


def test_migration_on_old_table_keeps_answers(tmp_path):
    copy = Path(r"D:\vvf-offload\tmp\q_levels\copy.sqlite")
    if not copy.is_file():
        pytest.skip("копии базы нет")
    db = tmp_path / "app.sqlite"
    shutil.copyfile(copy, db)
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        before = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM stream_questions")}
        assert "clip_plan_id" not in next(iter(before.values()))
    store = AppStore(Database(db), CookieCipher(tmp_path / "secret.key"))
    store.db.init()
    store.db.init()  # второй запуск сервиса ничего не ломает
    after = {int(r["id"]): dict(r) for r in store.db.query_all("SELECT * FROM stream_questions")}
    assert set(after) == set(before)
    for rid, row in before.items():
        for key, value in row.items():
            assert after[rid][key] == value, (rid, key)
        assert after[rid]["optional"] == 0 and after[rid]["clip_plan_id"] is None
    # Сервис видит уровни: канал стал проектом, стрим видео
    sample = Q.get_question(store, next(iter(before)))
    assert sample["level"] == Q.LEVEL_OF_SCOPE[sample["scope"]]
