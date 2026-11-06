"""«Что агент знает» и чаты видео: факты копятся без модели, доходят до запросов агента, не переходят между проектами.

Провайдер везде замокан: настоящий ответ модели тут не проверяется, проверяется, что ушло в запрос.
"""

from __future__ import annotations

import copy
import json
import sqlite3

import pytest

from app import chat_tools as CT
from app import meme_catalog as C
from app import project_chat as Chat
from app import project_knowledge as K
from app import projects as P
from app import stream_questions as Q
from app.agent_tools import ToolContext, run_tool
from app.ai import editor_agent
from app.ai.contracts import AnalysisClip, AnalysisResult, AnalysisSegment
from app.ai.publish_meta import project_context as meta_context
from app.ai.service import VideoAnalysisService
from app.settings import settings
from test_api_pipeline import _client

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x9a\xa0\xa0\x00\x00\x00\x00IEND\xaeB`\x82")

MINE = "Глеб копает обсидиан и модерит чат"
THEIRS = "Секретный лор чужого проекта про Апекс"


class Scripted:
    def __init__(self, turns=None):
        self.turns = list(turns or [])
        self.requests = []

    def generate_content(self, model, payload):
        self.requests.append(copy.deepcopy(payload))
        parts = self.turns.pop(0) if self.turns else [{"text": "ок"}]
        return {"candidates": [{"content": {"role": "model", "parts": parts}}]}


def _all_text(request) -> str:
    return json.dumps(request, ensure_ascii=False)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(C, "_list_cache", (0.0, []))
    monkeypatch.setattr(C, "_api_list", lambda: [{"id": "87743020", "name": "Two Buttons",
                                                  "url": "https://i.imgflip.com/1g8my4.jpg"}])
    monkeypatch.setattr(C, "_get", lambda url: PNG)
    monkeypatch.setattr("app.ai.editor_agent.client_factory", None)
    monkeypatch.setattr(settings, "gemini_api_key", "")


@pytest.fixture
def env(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    mura_src = store.create_source("upload", settings.source_dir / "mura.mp4", original_filename="Мура Minecraft.mp4",
                                   duration_sec=3600, status="ready")
    apex_src = store.create_source("upload", settings.source_dir / "apex.mp4", original_filename="Апекс катка.mp4",
                                   duration_sec=600, status="ready")
    mura = P.create_project(store, "Мура", kind="stream_channel", source_ids=[mura_src["id"]])["id"]
    apex = P.create_project(store, "Apex", kind="other", source_ids=[apex_src["id"]])["id"]
    return client, store, mura, apex, mura_src, apex_src


def _plan(store, source_id, title="Момент"):
    analysis = store.create_ai_analysis(source_id, "manual", status="succeeded")
    seg = store.create_ai_segment(analysis["id"], {"start_sec": 10, "end_sec": 25, "title": title})
    return store.create_clip_plan(source_id, analysis["id"], title, segment_ids=[seg["id"]])


def _seed_both(store, mura, apex):
    K.add_fact(store, mura, MINE)
    K.add_fact(store, apex, THEIRS)
    P.update_brief(store, mura, {"people": [{"name": "Якудев", "who": "автор мода на чибиков"}]})
    P.update_brief(store, apex, {"people": [{"name": "Вражеский Рейф", "who": "из чужого проекта"}]})
    P.add_rule(store, "Не брать мемы с настоящими людьми", kind="dont", project_id=mura, quote="не бери")
    P.add_rule(store, "Показывать только победы в Апексе", kind="do", project_id=apex, quote="только победы")


def _ask(**over):
    return {"kind": "context", "t_start": 2, "t_end": 5, "question": "Что тут происходит?",
            "options": ["Шутка", "Серьёзно"], **over}


# --------------------------------------------------------------------------- #
# Доходит до агента
# --------------------------------------------------------------------------- #


def test_project_chat_prompt_has_what_agent_knows(env):
    _client_, store, mura, apex, _ms, _as = env
    _seed_both(store, mura, apex)
    gemini = Scripted()
    thread = Chat.default_thread(store, mura)
    Chat.run_turn(store, thread, "кто такой Глеб?", client=gemini)
    text = _all_text(gemini.requests[0])
    assert MINE in text and "Якудев" in text and "Не брать мемы" in text
    assert THEIRS not in text and "Вражеский Рейф" not in text and "только победы" not in text.lower()


def test_lore_survives_when_project_has_hundreds_of_videos(env):
    """У Apex 218 видео: список источников стоял первым и вытеснял лор из обрезки среза."""
    _client_, store, mura, _apex, _ms, _as = env
    for index in range(260):
        source = store.create_source("upload", settings.source_dir / f"{index}.mp4",
                                     original_filename=f"очень длинное название записи номер {index} " * 2,
                                     duration_sec=60, status="ready")
        P.set_source_project(store, source["id"], mura)
    for index in range(40):
        P.add_rule(store, f"Правило номер {index} про уникальную тему {index * 7}", project_id=mura, quote="q", force=True)
    K.add_fact(store, mura, MINE)
    context = Chat.project_context(store, mura)
    assert MINE in context
    assert json.loads(context.split("…(обрезано)")[0])["project"]["sources_total"] == 261


def test_video_chat_knows_its_video_and_project(env):
    _client_, store, mura, apex, mura_src, _as = env
    _seed_both(store, mura, apex)
    ids = Q.add_questions(store, mura_src["id"], [_ask(question="Кто кричит на 10 минуте?", scope="stream")])["added"]
    Q.answer_question(store, ids[0], None, "Это брат Муры")
    thread = Chat.video_thread(store, mura_src["id"])
    assert thread["source_id"] == mura_src["id"] and thread["project_id"] == mura
    gemini = Scripted()
    Chat.run_turn(store, thread, "найди ещё моменты", client=gemini)
    text = _all_text(gemini.requests[0])
    assert f"source_id {mura_src['id']}" in text and "Мура Minecraft" in text
    assert MINE in text and "Это брат Муры" in text
    assert THEIRS not in text


def test_video_chat_follows_the_video_to_another_project(env):
    """Видео перенесли: лор старого проекта к нему больше не идёт, факты пишутся в новый."""
    _client_, store, mura, apex, mura_src, _as = env
    _seed_both(store, mura, apex)
    thread = Chat.video_thread(store, mura_src["id"])
    P.set_source_project(store, mura_src["id"], apex)
    moved = Chat.get_thread(store, thread["id"])
    assert moved["project_id"] == apex
    gemini = Scripted()
    Chat.run_turn(store, moved, "запомни: это видео про рейтинг", client=gemini)
    text = _all_text(gemini.requests[0])
    assert THEIRS in text and MINE not in text
    assert any("про рейтинг" in f["text"] for f in K.list_facts(store, apex))
    assert not any("про рейтинг" in f["text"] for f in K.list_facts(store, mura))
    # Удаление проекта не сносит чат видео, у него нет своей связи с проектом.
    P.delete_project(store, apex)
    assert Chat.get_thread(store, thread["id"])["project_id"] is None


def test_video_without_project_has_no_lore_and_is_not_in_general(env):
    client, store, mura, apex, _ms, _as = env
    _seed_both(store, mura, apex)
    loose = store.create_source("upload", settings.source_dir / "loose.mp4", original_filename="Без проекта.mp4",
                                duration_sec=60, status="ready")
    thread = Chat.video_thread(store, loose["id"])
    gemini = Scripted()
    Chat.run_turn(store, thread, "что тут?", client=gemini)
    text = _all_text(gemini.requests[0])
    assert "Без проекта" in text and MINE not in text and THEIRS not in text
    Chat.add_thread_message(store, thread["id"], "user", "привет")
    listed = client.get("/api/chat/threads").json()
    assert thread["id"] not in [t["id"] for t in listed["general"]]
    assert thread["id"] in [t["id"] for t in listed["videos"]]


def test_clip_chat_prompt_has_project_knowledge(env):
    _client_, store, mura, apex, mura_src, apex_src = env
    _seed_both(store, mura, apex)
    plan = _plan(store, mura_src["id"])
    gemini = Scripted([[{"text": "Понял, ничего не меняю."}]])
    editor_agent.run_agent(store, plan["id"], "что тут?", client=gemini, model="m")
    text = _all_text(gemini.requests[0])
    assert MINE in text and "Якудев" in text and THEIRS not in text
    # Клип другого проекта получает свой лор, а не этот.
    other = Scripted([[{"text": "Понял."}]])
    editor_agent.run_agent(store, _plan(store, apex_src["id"])["id"], "что тут?", client=other, model="m")
    other_text = _all_text(other.requests[0])
    assert THEIRS in other_text and MINE not in other_text


def test_canvas_clip_gets_project_of_its_own(env):
    _client_, store, mura, apex, _ms, _as = env
    _seed_both(store, mura, apex)
    hidden = store.create_source("upload", settings.source_dir / "canvas.mp4", original_filename="Холсты",
                                 duration_sec=60, status="ready")
    plan = _plan(store, hidden["id"])
    assert K.block_for_plan(store, plan["id"]) == ""
    P.assign_plan_project(store, plan["id"], apex)
    block = K.block_for_plan(store, plan["id"])
    assert THEIRS in block and MINE not in block


def test_cutting_prompt_has_project_knowledge_but_stored_prompt_stays_clean(env, monkeypatch):
    _client_, store, mura, apex, mura_src, _as = env
    _seed_both(store, mura, apex)
    (settings.source_dir / "mura.mp4").write_bytes(b"video")
    segment = AnalysisSegment(start_sec=10, end_sec=20, title="Момент")
    seen = {}

    class FakeAnalyzer:
        provider = "gemini"

        def analyze(self, source_dict, prompt, model):
            seen["prompt"] = prompt
            return AnalysisResult(segments=[segment], clips=[AnalysisClip(title="Момент", segments=[segment], score=0.8)],
                                  usage={})

    monkeypatch.setattr("app.ai.service.get_video_analyzer", lambda provider, store=None: FakeAnalyzer())
    analysis = VideoAnalysisService(store).run_analysis(mura_src["id"], provider="gemini", model="fake", prompt="Найди смешное")
    assert analysis["status"] == "succeeded"
    assert MINE in seen["prompt"] and "Не брать мемы" in seen["prompt"] and "Якудев" in seen["prompt"]
    assert THEIRS not in seen["prompt"]
    assert json.loads(analysis["request_json"])["prompt"] == "Найди смешное"


def test_pick_and_montage_prompts_have_project_knowledge(env):
    from app.ai.montage import propose_montage
    from app.ai.pick import pick_best

    _client_, store, mura, apex, mura_src, _as = env
    _seed_both(store, mura, apex)
    plan = _plan(store, mura_src["id"], "Чибики бегут {в} кадре")

    class PickClient(Scripted):
        def generate_content(self, model, payload):
            self.requests.append(copy.deepcopy(payload))
            body = {"picks": [{"id": plan["id"], "score": 9, "reason": "ок"}]}
            return {"candidates": [{"content": {"parts": [{"text": json.dumps(body)}]}}]}

    gemini = PickClient()
    pick_best(store, mura_src["id"], count=1, client=gemini, model="m")
    assert MINE in _all_text(gemini.requests[0]) and THEIRS not in _all_text(gemini.requests[0])

    class MontageClient(Scripted):
        def generate_content(self, model, payload):
            self.requests.append(copy.deepcopy(payload))
            raise RuntimeError("хватит: запрос уже записан")

    gemini = MontageClient()
    with pytest.raises(RuntimeError):
        propose_montage(store, plan["id"], client=gemini, model="m")
    assert MINE in _all_text(gemini.requests[0]) and THEIRS not in _all_text(gemini.requests[0])


def test_outside_agents_and_titles_read_it_too(env):
    _client_, store, mura, apex, mura_src, apex_src = env
    _seed_both(store, mura, apex)
    result, ok = run_tool(ToolContext(store=store), "get_project_brief", {"source_id": mura_src["id"]})
    assert ok and [f["text"] for f in result["facts"]] == [MINE]
    # Источник чужого проекта не приносит свои ответы в бриф этого проекта.
    ids = Q.add_questions(store, apex_src["id"], [_ask(question="Чей это скин?", scope="stream")])["added"]
    Q.answer_question(store, ids[0], 0)
    result, ok = run_tool(ToolContext(store=store), "get_project_brief", {"project_id": mura, "source_id": apex_src["id"]})
    assert ok and "stream_answers" not in result
    assert MINE in meta_context(store, {"source_id": mura_src["id"]})["facts"]
    assert THEIRS not in json.dumps(meta_context(store, {"source_id": mura_src["id"]}), ensure_ascii=False)


# --------------------------------------------------------------------------- #
# Границы проекта
# --------------------------------------------------------------------------- #


def test_prompt_block_and_view_do_not_cross_projects(env):
    _client_, store, mura, apex, mura_src, apex_src = env
    _seed_both(store, mura, apex)
    ids = Q.add_questions(store, apex_src["id"], [_ask(question="Кто такой Рейф?", kind="who", about="Рейф", scope="channel"),
                                                  _ask(question="Что за катка?", scope="stream")])["added"]
    Q.answer_question(store, ids[0], None, "напарник из чужого проекта")
    Q.answer_question(store, ids[1], None, "финал турнира чужого проекта")
    moment = Q.add_meme_choices(store, apex_src["id"], [{"t_start": 2, "why": "чужой момент", "options": [
        {"catalog": "Two Buttons"}, {"catalog": "Two Buttons", "title": "Другой"}]}])["added"][0]["id"]
    Q.choose_meme(store, moment, Q.get_moment(store, moment)["options"][0]["id"])

    block = K.prompt_block(store, mura, mura_src["id"])
    view = json.dumps(K.knowledge_view(store, mura), ensure_ascii=False)
    brief = json.dumps(P.project_brief(store, mura), ensure_ascii=False)
    for text in (block, view, brief):
        assert MINE in text
        for foreign in (THEIRS, "чужого проекта", "чужой момент", "Показывать только победы", "Вражеский Рейф"):
            assert foreign not in text
    # Видео чужого проекта не добавляет ответы, даже если назвать его явно.
    assert "финал турнира" not in K.prompt_block(store, mura, apex_src["id"])
    other = K.prompt_block(store, apex, apex_src["id"])
    assert "финал турнира" in other and "чужой момент" in other and MINE not in other


def test_rows_of_another_project_cannot_be_edited_or_deleted(env):
    client, store, mura, apex, _ms, apex_src = env
    _seed_both(store, mura, apex)
    fact = K.list_facts(store, apex)[0]
    rule = P.list_rules(store, project_id=apex, include_global=False)[0]
    question = Q.add_questions(store, apex_src["id"], [_ask(scope="stream")])["added"][0]
    Q.answer_question(store, question, 0)
    for ref in (f"fact:{fact['id']}", f"rule:{rule['id']}", f"question:{question}"):
        assert client.patch(f"/api/projects/{mura}/knowledge", json={"ref": ref, "text": "взлом"}).status_code == 404
        assert client.delete(f"/api/projects/{mura}/knowledge", params={"ref": ref}).status_code == 404
    assert K.list_facts(store, apex)[0]["text"] == THEIRS
    assert P.get_rule(store, rule["id"])["status"] == "active"


def test_project_chat_refuses_lore_of_another_project(env):
    _client_, store, mura, apex, _ms, apex_src = env
    _seed_both(store, mura, apex)
    for name, args in (("get_project_brief", {"project_id": apex}), ("get_project_brief", {"source_id": apex_src["id"]}),
                       ("remember_fact", {"project_id": apex, "text": "чужая запись"}),
                       ("list_rules", {"project_id": apex}), ("list_stream_answers", {"source_id": apex_src["id"]})):
        result, ok, _clip = CT.run(store, name, args, project_id=mura)
        assert not ok and "недоступ" in result["error"], name
    assert [f["text"] for f in K.list_facts(store, apex)] == [THEIRS]
    # Своё работает, без id берётся проект чата.
    result, ok, _clip = CT.run(store, "get_project_brief", {}, project_id=mura)
    assert ok and MINE in json.dumps(result["facts"], ensure_ascii=False)
    # В общем чате проекта нет, там можно спросить про любой.
    result, ok, _clip = CT.run(store, "get_project_brief", {"project_id": apex}, project_id=None)
    assert ok and THEIRS in json.dumps(result["facts"], ensure_ascii=False)


def test_agent_in_project_chat_records_facts_only_there(env):
    _client_, store, mura, apex, _ms, _as = env
    gemini = Scripted([
        [{"functionCall": {"name": "remember_fact", "args": {"text": "Саня это alkotrash_12345", "quote": "саня это он"}}}],
        [{"text": "Записал."}],
    ])
    out = Chat.run_turn(store, Chat.default_thread(store, mura), "саня это alkotrash_12345", client=gemini)
    assert out["text"] == "Записал."
    assert [f["text"] for f in K.list_facts(store, mura)] == ["Саня это alkotrash_12345"]
    assert K.list_facts(store, apex) == []
    assert K.list_facts(store, mura)[0]["origin"] == K.ORIGIN_AGENT


# --------------------------------------------------------------------------- #
# Как копится
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("message,fact", [
    ("запомни: Глеб модер в дискорде", "Глеб модер в дискорде"),
    ("Запомни, что чибики это зрители", "чибики это зрители"),
    ("учти что Саня это alkotrash", "Саня это alkotrash"),
    ("имей в виду: Мура стримит по средам", "Мура стримит по средам"),
    ("Якудев автор мода, запомни", "Якудев автор мода"),
    ("Кстати, запомни это видео про Minecraft", "это видео про Minecraft"),
])
def test_remember_phrases(message, fact):
    assert K.fact_in_message(message) == fact


@pytest.mark.parametrize("message", ["запомни", "запомни это", "ты запомнил?", "что ты запомнил про Глеба?",
                                     "нарежь новый стрим", "запоминающийся момент найди"])
def test_not_remember_phrases(message):
    assert K.fact_in_message(message) is None


def test_remember_works_without_any_model(env):
    client, store, mura, _apex, _ms, _as = env
    thread_id = client.get(f"/api/projects/{mura}/chat").json()["thread_id"]
    assert not settings.gemini_api_key
    sent = client.post(f"/api/chat/threads/{thread_id}/messages", json={"text": "запомни: Глеб модер в дискорде"})
    assert sent.status_code == 200
    Chat.wait_thread(thread_id, 10)
    view = client.get(f"/api/chat/threads/{thread_id}").json()
    reply = view["messages"][-1]
    assert "Запомнил: «Глеб модер в дискорде»" in reply["text"]
    assert reply["meta"]["remembered"]["text"] == "Глеб модер в дискорде"
    rows = client.get(f"/api/projects/{mura}/knowledge").json()
    group = next(g for g in rows["groups"] if g["key"] == "facts")
    assert group["items"][0]["text"] == "Глеб модер в дискорде" and group["items"][0]["origin"] == "из чата проекта"


def test_rule_phrase_stays_a_rule_not_a_fact(env):
    _client_, store, mura, _apex, _ms, _as = env
    out = Chat.run_turn(store, Chat.default_thread(store, mura), "запомни: такое больше не бери никогда", client=Scripted())
    assert out["learned"]["action"] != "none"
    assert K.list_facts(store, mura) == []


def test_remember_in_clip_chat_goes_to_its_project(env):
    _client_, store, mura, apex, mura_src, _as = env
    plan = _plan(store, mura_src["id"])
    gemini = Scripted([[{"text": "Хорошо."}]])
    editor_agent.start_run(store, plan["id"], "запомни: Онигуру и Onihana_ один человек", client=gemini, model="m")
    assert editor_agent.wait_run(plan["id"], 10)
    fact = K.list_facts(store, mura)[0]
    assert "Онигуру" in fact["text"] and fact["clip_plan_id"] == plan["id"] and K.list_facts(store, apex) == []
    reply = store.list_clip_agent_messages(plan["id"])[-1]
    assert reply["text"].startswith("Запомнил:")
    view = K.knowledge_view(store, mura)
    assert view["groups"][0]["items"][0]["origin"] == f"из чата клипа #{plan['id']}"


def test_clip_chat_remembers_even_when_it_cannot_answer(env):
    client, store, mura, _apex, mura_src, _as = env
    plan = _plan(store, mura_src["id"])
    response = client.post(f"/api/clip-plans/{plan['id']}/agent", json={"message": "запомни: Глеб друг Муры"})
    assert response.status_code == 400 and "Запомнил" in response.json()["detail"]
    assert K.list_facts(store, mura)[0]["text"] == "Глеб друг Муры"


def test_answers_and_memes_show_up_and_reach_the_prompt(env):
    _client_, store, mura, _apex, mura_src, _as = env
    sid = mura_src["id"]
    ids = Q.add_questions(store, sid, [
        _ask(question="Кто такой Глеб?", kind="who", about="Глеб", scope="channel", options=["Модер", "Друг"]),
        _ask(question="Что значит спящие бизнесмены?", kind="joke", about="спящие бизнесмены", scope="channel"),
        _ask(question="Что за сервер?", kind="context", scope="channel", options=["Свой", "Чужой"]),
        _ask(question="Кто кричит в начале?", scope="stream"),
    ])["added"]
    Q.answer_question(store, ids[0], 0)
    Q.answer_question(store, ids[1], None, "чибики, которые уснули")
    Q.answer_question(store, ids[2], 0)
    Q.answer_question(store, ids[3], None, "сестра")
    moment = Q.add_meme_choices(store, sid, [{"t_start": 2, "why": "Мура мать года", "options": [
        {"catalog": "Two Buttons"}, {"catalog": "Two Buttons", "title": "Другой"}]}])["added"][0]["id"]
    option = Q.get_moment(store, moment)["options"][0]
    Q.choose_meme(store, moment, option["id"])

    view = K.knowledge_view(store, mura)
    by_key = {g["key"]: g for g in view["groups"]}
    assert by_key["people"]["items"][0]["title"] == "Глеб" and "ваш ответ" in by_key["people"]["items"][0]["origin"]
    assert by_key["jokes"]["items"][0]["text"] == "чибики, которые уснули"
    assert "Что за сервер? — Свой" in by_key["about"]["items"][0]["text"]
    # Ответы про канал не дублируются в «про отдельные видео».
    assert [i["title"] for i in by_key["videos"]["items"]] == ["Кто кричит в начале?"]
    assert by_key["memes"]["items"][0]["text"] == "Мура мать года"
    assert view["waiting"]["questions"] == 0

    block = K.prompt_block(store, mura, sid)
    assert "Глеб: Модер" in block and "чибики, которые уснули" in block and "сестра" in block and "Мура мать года" in block
    # Ответ про одно видео не идёт в работу над другими видео проекта.
    assert "сестра" not in K.prompt_block(store, mura)
    second = store.create_source("upload", settings.source_dir / "next.mp4", original_filename="Следующий стрим.mp4",
                                 duration_sec=60, status="ready")
    P.set_source_project(store, second["id"], mura)
    assert "сестра" not in K.prompt_block(store, mura, second["id"]) and "Глеб: Модер" in K.prompt_block(store, mura, second["id"])


# --------------------------------------------------------------------------- #
# Поправить и убрать
# --------------------------------------------------------------------------- #


def _refs(view):
    return {item["ref"]: item for group in view["groups"] for item in group["items"]}


def test_edit_and_delete_reach_the_prompt(env):
    client, store, mura, _apex, mura_src, _as = env
    added = client.post(f"/api/projects/{mura}/knowledge", json={"text": "Мура стримит по средам"})
    assert added.status_code == 200 and added.json()["created"]
    fact_ref = f"fact:{added.json()['fact']['id']}"
    edited = client.patch(f"/api/projects/{mura}/knowledge", json={"ref": fact_ref, "text": "Мура стримит по пятницам"})
    assert edited.status_code == 200 and _refs(edited.json())[fact_ref]["text"] == "Мура стримит по пятницам"
    block = K.prompt_block(store, mura)
    assert "по пятницам" in block and "по средам" not in block
    removed = client.delete(f"/api/projects/{mura}/knowledge", params={"ref": fact_ref})
    assert removed.status_code == 200 and fact_ref not in _refs(removed.json())
    assert "по пятницам" not in K.prompt_block(store, mura)
    assert P.project_brief(store, mura)["facts"] == []


def test_agent_cannot_bring_back_what_owner_removed_but_owner_can(env):
    _client_, store, mura, _apex, _ms, _as = env
    fact = K.add_fact(store, mura, "Глеб модер")["fact"]
    K.delete_row(store, mura, f"fact:{fact['id']}")
    result, ok = run_tool(ToolContext(store=store), "remember_fact", {"project_id": mura, "text": "Глеб модер"})
    assert not ok or result.get("ok") is False
    assert K.list_facts(store, mura) == []
    assert K.remember_from_chat(store, mura, "запомни: Глеб модер")["created"]
    assert [f["text"] for f in K.list_facts(store, mura)] == ["Глеб модер"]


def test_correct_and_remove_owner_answer_everywhere(env):
    _client_, store, mura, _apex, mura_src, _as = env
    sid = mura_src["id"]
    ids = Q.add_questions(store, sid, [
        _ask(question="Кто такой Глеб?", kind="who", about="Глеб", scope="channel", options=["Модер", "Друг"]),
        _ask(question="Что за сервер?", scope="channel", options=["Свой", "Чужой"]),
        _ask(question="Кто кричит?", scope="stream"),
    ])["added"]
    for qid in ids:
        Q.answer_question(store, qid, 0)
    view = K.knowledge_view(store, mura)
    # Поправка: и в брифе, и в самом ответе (его читают list_stream_answers и «Вопросы»).
    K.update_row(store, mura, "brief:people:Глеб", "Модер и лучший друг Муры")
    assert Q.get_question(store, ids[0])["answer"] == "Модер и лучший друг Муры"
    assert P.get_brief(store, mura)["brief"]["people"][0]["who"] == "Модер и лучший друг Муры"
    note = next(i for i in _refs(view).values() if i["ref"].startswith("brief:notes:"))
    K.update_row(store, mura, note["ref"], "Что за сервер? — Закрытый")
    assert P.get_brief(store, mura)["brief"]["notes"] == "Что за сервер? — Закрытый"
    assert Q.get_question(store, ids[1])["answer"] == "Закрытый"
    K.update_row(store, mura, f"question:{ids[2]}", "Кричит сестра")
    assert "Кричит сестра" in K.prompt_block(store, mura, sid)

    # Удаление: нет ни в брифе, ни в ответах для монтажёра, вопрос заново не задаётся.
    K.delete_row(store, mura, "brief:people:Глеб")
    assert P.get_brief(store, mura)["brief"]["people"] == []
    assert [a["id"] for a in Q.answers_brief(store, sid)["answers"]] == [ids[1], ids[2]]
    note = next(i for i in _refs(K.knowledge_view(store, mura)).values() if i["ref"].startswith("brief:notes:"))
    K.delete_row(store, mura, note["ref"])
    K.delete_row(store, mura, f"question:{ids[2]}")
    assert Q.answers_brief(store, sid)["answers"] == []
    assert K.prompt_block(store, mura, sid) == ""
    assert K.knowledge_view(store, mura)["waiting"]["questions"] == 0


def test_withdrawn_channel_answer_leaves_the_brief(env):
    _client_, store, mura, _apex, mura_src, _as = env
    ids = Q.add_questions(store, mura_src["id"], [
        _ask(question="Кто такой Глеб?", kind="who", about="Глеб", scope="channel", options=["Модер", "Друг"]),
        _ask(question="Что за сервер?", scope="channel", options=["Свой", "Чужой"]),
    ])["added"]
    Q.answer_question(store, ids[0], 0)
    Q.answer_question(store, ids[1], 0)
    # Строку поправил кто-то ещё: снятие ответа её не трогает.
    P.update_brief(store, mura, {"people": [{"name": "Глеб", "who": "Модер, копает обсидиан"}]})
    Q.answer_question(store, ids[0])
    assert P.get_brief(store, mura)["brief"]["people"][0]["who"] == "Модер, копает обсидиан"
    Q.answer_question(store, ids[1], skip=True)
    assert P.get_brief(store, mura)["brief"]["notes"] == ""


def test_edit_brief_rows_and_rules(env):
    client, store, mura, _apex, _ms, _as = env
    P.update_brief(store, mura, {"summary": "Витубер Мура", "taboo": [{"topic": "Политика", "why": "владелец против"}],
                                 "people": [{"name": "Якудев", "who": "автор", "traits": "пишет моды", "aliases": ["yakoodev"]}]})
    rule = P.add_rule(store, "Не брать чужие видео", kind="dont", project_id=mura, quote="не бери")["rule"]
    view = K.knowledge_view(store, mura)
    assert _refs(view)["brief:people:Якудев"]["text"] == "автор. пишет моды"
    assert _refs(view)["brief:people:Якудев"]["note"] == "ещё зовут: yakoodev"
    K.update_row(store, mura, "brief:people:Якудев", "автор мода на чибиков", title="Якудев (yakoodev)")
    person = P.get_brief(store, mura)["brief"]["people"]
    assert len(person) == 1 and person[0]["name"] == "Якудев (yakoodev)" and person[0]["who"] == "автор мода на чибиков"
    assert person[0]["aliases"] == ["yakoodev"]
    K.update_row(store, mura, "brief:summary", "Витубер Мура, Twitch sareamura")
    assert P.get_brief(store, mura)["brief"]["summary"] == "Витубер Мура, Twitch sareamura"
    K.delete_row(store, mura, "brief:taboo:Политика")
    assert P.get_brief(store, mura)["brief"]["taboo"] == []

    K.update_row(store, mura, f"rule:{rule['id']}", "Не брать чужие видео и Смешариков")
    active = P.list_rules(store, project_id=mura)
    assert [r["text"] for r in active] == ["Не брать чужие видео и Смешариков"]
    assert P.get_rule(store, rule["id"])["status"] == "superseded"
    K.delete_row(store, mura, f"rule:{active[0]['id']}")
    assert P.list_rules(store, project_id=mura) == []
    assert P.get_rule(store, active[0]["id"])["status"] == "revoked"  # история правил остаётся


def test_global_rule_is_visible_but_not_changed_from_one_project(env):
    client, store, mura, apex, _ms, _as = env
    rule = P.add_rule(store, "Без эмодзи в титрах", scope="global", quote="без эмодзи")["rule"]
    row = _refs(K.knowledge_view(store, mura))[f"rule:{rule['id']}"]
    assert row["origin"] == "для всех проектов" and row["editable"] is False and row["removable"] is False
    assert "Без эмодзи" in K.prompt_block(store, mura) and "Без эмодзи" in K.prompt_block(store, apex)
    assert client.delete(f"/api/projects/{mura}/knowledge", params={"ref": f"rule:{rule['id']}"}).status_code == 404
    assert P.get_rule(store, rule["id"])["status"] == "active"


def test_removed_meme_choice_is_gone_for_the_agent(env):
    _client_, store, mura, _apex, mura_src, _as = env
    moment = Q.add_meme_choices(store, mura_src["id"], [{"t_start": 2, "why": "Мать года", "options": [
        {"catalog": "Two Buttons"}, {"catalog": "Two Buttons", "title": "Другой"}]}])["added"][0]["id"]
    Q.choose_meme(store, moment, Q.get_moment(store, moment)["options"][0]["id"])
    assert "Мать года" in K.prompt_block(store, mura, mura_src["id"])
    K.delete_row(store, mura, f"meme:{moment}")
    assert "Мать года" not in K.prompt_block(store, mura, mura_src["id"])
    assert Q.get_moment(store, moment)["status"] == "none"
    assert P.project_brief(store, mura)["memes"] == []


def test_waiting_questions_are_shown_but_not_sent(env):
    _client_, store, mura, _apex, mura_src, _as = env
    Q.add_questions(store, mura_src["id"], [_ask(question="Кто такой Вопросник?", kind="who", about="Вопросник", scope="channel")])
    view = K.knowledge_view(store, mura)
    assert view["total"] == 0 and view["waiting"]["questions"] == 1
    assert view["waiting"]["items"][0]["text"] == "Кто такой Вопросник?"
    assert K.prompt_block(store, mura, mura_src["id"]) == ""


def test_long_knowledge_is_cut_by_section_priority(env):
    _client_, store, mura, _apex, _ms, _as = env
    P.add_rule(store, "Важное правило владельца", project_id=mura, quote="q")
    for index in range(150):
        P.update_brief(store, mura, {"people": [{"name": f"Человек {index}", "who": "x" * 80}]})
    block = K.prompt_block(store, mura, limit=2000)
    assert len(block) <= 2200 and "Важное правило" in block and "не поместилось" in block


# --------------------------------------------------------------------------- #
# Чат видео и задания на проект
# --------------------------------------------------------------------------- #


def test_video_thread_is_one_per_video_and_hidden_until_used(env):
    client, store, mura, _apex, mura_src, _as = env
    first = client.post("/api/chat/video-thread", json={"source_id": mura_src["id"]})
    again = client.post("/api/chat/video-thread", json={"source_id": mura_src["id"]})
    assert first.status_code == 200 and first.json()["id"] == again.json()["id"]
    assert client.post("/api/chat/video-thread", json={"source_id": 99999}).status_code == 404
    thread_id = first.json()["id"]
    project = lambda: next(p for p in client.get("/api/chat/threads").json()["projects"] if p["id"] == mura)  # noqa: E731
    assert thread_id not in [t["id"] for t in project()["threads"]]
    Chat.add_thread_message(store, thread_id, "user", "привет")
    row = next(t for t in project()["threads"] if t["id"] == thread_id)
    assert row["title"] == "Видео: Мура Minecraft" and row["source_id"] == mura_src["id"]
    view = client.get(f"/api/chat/threads/{thread_id}").json()
    assert view["video"]["id"] == mura_src["id"] and view["project"]["id"] == mura
    # Основная переписка проекта и Telegram не путаются с чатом видео.
    assert Chat.default_thread(store, mura)["id"] != thread_id


def test_deleted_video_takes_its_chat_but_not_the_facts(env):
    _client_, store, mura, _apex, mura_src, _as = env
    thread = Chat.video_thread(store, mura_src["id"])
    Chat.run_turn(store, thread, "запомни: Глеб друг Муры", client=Scripted())
    store.delete_source(mura_src["id"])
    with pytest.raises(KeyError):
        Chat.get_thread(store, thread["id"])
    assert [f["text"] for f in K.list_facts(store, mura)] == ["Глеб друг Муры"]
    assert K.list_facts(store, mura)[0]["source_id"] is None
    assert K.knowledge_view(store, mura)["groups"][0]["items"][0]["origin"] == "из чата проекта"


def test_new_stream_from_project_chat_lands_in_that_project(env):
    _client_, store, mura, apex, _ms, apex_src = env
    calls = {}

    class Downloads:
        def start(self, url, quality="", ingestor=None, on_source=None):
            calls["on_source"] = on_source
            return {"id": "d1", "status": "queued", "label": "стрим", "url": url}

    CT.SERVICES["downloads"] = lambda: Downloads()
    try:
        result, ok, _clip = CT.run(store, "add_source_from_url", {"url": "https://twitch.tv/videos/1"}, project_id=mura)
        assert ok and "ляжет в этот проект" in result["note"]
        new = store.create_source("upload", settings.source_dir / "new.mp4", original_filename="new.mp4", duration_sec=60,
                                  status="ready")
        calls["on_source"](new)
        assert store.get_source(new["id"])["project_id"] == mura
        result, ok, _clip = CT.run(store, "add_source_from_url", {"url": "https://twitch.tv/videos/2"}, project_id=None)
        assert ok and calls["on_source"] is None
    finally:
        CT.SERVICES.pop("downloads", None)
    # Чужое видео из чата проекта не нарезать.
    result, ok, _clip = CT.run(store, "analyze_source", {"source_id": apex_src["id"]}, project_id=mura)
    assert not ok and "другого проекта" in result["error"]


def test_old_threads_table_gets_the_column_and_keeps_rows(tmp_path):
    """База прежней сборки: чаты и реплики на месте, старый адрес работает, новая колонка пустая."""
    conn = sqlite3.connect(tmp_path / "old.sqlite")
    conn.executescript("""
        CREATE TABLE projects (id INTEGER PRIMARY KEY);
        CREATE TABLE sources (id INTEGER PRIMARY KEY);
        INSERT INTO projects (id) VALUES (4);
        CREATE TABLE chat_threads (
            id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
            title TEXT NOT NULL DEFAULT '', is_default INTEGER NOT NULL DEFAULT 0, model TEXT NOT NULL DEFAULT '',
            tool_groups_json TEXT NOT NULL DEFAULT '[]', summary TEXT NOT NULL DEFAULT '',
            summary_upto INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        INSERT INTO chat_threads (id, project_id, title, is_default) VALUES (7, 4, 'Основной чат', 1);
    """)
    Chat.ensure_chat_schema(conn)
    Chat.ensure_chat_schema(conn)  # второй запуск ничего не ломает
    row = conn.execute("SELECT id, project_id, title, is_default, source_id FROM chat_threads").fetchone()
    assert row == (7, 4, "Основной чат", 1, None)


def _owl_project(store):
    from app import aivideo as V
    from app import explainer as E

    pid = V.create_ai_project(store, "Сова и истории", provider="none")["project"]["id"]
    mascot = E.save_mascot(store, pid, name="Ух", identity_card="a chubby round midnight-blue owl",
                           persona="сова-полуночник в худи", voice={"provider": "gemini", "voice": "Puck"})
    return pid, int(mascot["id"])


def test_project_chat_starts_from_the_mascot(env):
    """«Сделай новую серию совы» в чате проекта: в запросе уже есть маскот и запрет заводить нового героя."""
    _client_, store, _mura, _apex, _ms, _as = env
    pid, mascot_id = _owl_project(store)
    gemini = Scripted()
    Chat.run_turn(store, Chat.default_thread(store, pid), "сделай новую серию совы", client=gemini)
    request = gemini.requests[0]
    system = request["systemInstruction"]["parts"][0]["text"]
    assert "уже есть маскот: Ух" in system and "explainer_overview" in system and "aiv_define_character" in system
    first = request["contents"][-1]["parts"][0]["text"]
    assert f'"mascot_id": {mascot_id}' in first and "сова-полуночник" in first


def test_project_without_mascot_gets_no_mascot_note(env):
    _client_, store, mura, _apex, _ms, _as = env
    gemini = Scripted()
    Chat.run_turn(store, Chat.default_thread(store, mura), "как дела", client=gemini)
    assert "уже есть маскот" not in gemini.requests[0]["systemInstruction"]["parts"][0]["text"]
    assert '"mascots"' not in gemini.requests[0]["contents"][-1]["parts"][0]["text"]


def test_general_chat_sees_mascots_of_projects(env):
    _client_, store, _mura, _apex, _ms, _as = env
    pid, mascot_id = _owl_project(store)
    gemini = Scripted()
    Chat.run_turn(store, Chat.create_thread(store), "сделай новую серию совы", client=gemini)
    request = gemini.requests[0]
    assert "похож на маскота проекта" in request["systemInstruction"]["parts"][0]["text"]
    assert f'"mascot_id": {mascot_id}' in request["contents"][-1]["parts"][0]["text"]


def test_canvas_clip_chat_knows_the_project_mascot(env):
    """Чат клипа на холсте: проект берётся из plan_projects, про маскота агент знает и новую серию отправляет в чат проекта."""
    _client_, store, _mura, _apex, _ms, _as = env
    pid, _mascot = _owl_project(store)
    canvas = store.create_source("canvas", settings.source_dir / "canvas.mp4", original_filename="Холсты",
                                 duration_sec=60, status="ready")
    plan = _plan(store, canvas["id"])
    P.assign_plan_project(store, plan["id"], pid)
    assert "Ух" in K.mascot_block_for_plan(store, plan["id"])
    gemini = Scripted([[{"text": "Понял."}]])
    editor_agent.run_agent(store, plan["id"], "сделай новую серию совы", client=gemini, model="m")
    text = _all_text(gemini.requests[0])
    assert "МАСКОТ ПРОЕКТА" in text and "чат проекта" in text
    # Клип другого проекта чужого маскота не видит.
    assert K.mascot_block_for_plan(store, _plan(store, _ms["id"])["id"]) == ""


def test_hidden_source_is_not_a_video_for_chat(env):
    """Служебный источник холстов и загруженных клипов: своего чата видео у него нет."""
    client, store, _mura, _apex, _ms, _as = env
    for kind in ("canvas", "clip_upload"):
        hidden = store.create_source(kind, settings.source_dir / f"{kind}.mp4", original_filename="Холсты",
                                     duration_sec=60, status="ready")
        assert client.post("/api/chat/video-thread", json={"source_id": hidden["id"]}).status_code == 400
    assert not store.db.query_all("SELECT id FROM chat_threads WHERE source_id IS NOT NULL")


def test_clip_view_says_when_source_is_hidden(env):
    client, store, _mura, _apex, _ms, _as = env
    canvas = store.create_source("canvas", settings.source_dir / "canvas.mp4", original_filename="Холсты",
                                 duration_sec=60, status="ready")
    plan = _plan(store, canvas["id"])
    P.assign_plan_project(store, plan["id"], _mura)
    view = client.get(f"/api/clip-view/{plan['id']}")
    assert view.status_code == 200, view.text
    assert view.json()["source"]["source_type"] == "canvas" and view.json()["project"]["id"] == _mura
