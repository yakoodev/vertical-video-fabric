"""Чат проекта сам подставляет плейбук по смыслу запроса; голосовая группа инструментов; голос рассказчика в брифе."""

from __future__ import annotations

from app import project_chat_playbook as PB
from test_data_foundation import _store


def test_pick_by_intent():
    assert PB.pick("Сделай рекламный ролик для TikTok про магазин vtubika.store, с озвучкой") == "ad-voiceover"
    assert PB.pick("Промо приложения по записям экрана, вот скринкаст") == "app-promo"
    assert PB.pick("Возьми отрезок стрима 3:23:00–3:38:00 и сделай вертикальную нарезку") == "stream-shorts"
    assert PB.pick("Сделай серию 3 по этому комментарию зрителя") == "series"
    assert PB.pick("Нарежь стрим, там была реклама спонсора") == "stream-shorts"  # стрим главнее «рекламы»
    assert PB.pick("Промокод поменяй на Free20") is None  # «промокод» — не «промо»
    assert PB.pick("Как дела?") is None
    # Явный get_playbook хода и имя плейбука в запросе — как у ИИ-монтажёра.
    steps = [{"tool": "get_playbook", "ok": True, "args": {"name": "meme-insert"}}]
    assert PB.pick("реклама сайта", steps) == "meme-insert"
    assert PB.pick("сделай по stream-horizontal") == "stream-horizontal"


def test_block_text():
    ad = PB.block("ad-voiceover")
    assert "АКТИВНЫЙ ПЛЕЙБУК «ad-voiceover»" in ad and "add_voiceover" in ad and "check_voiceover" in ad
    assert "add_voiceover" in PB.block("series") and "series3d" in PB.block("series")
    assert PB.block(None) == "" and PB.block("нет-такого") == ""


def test_chat_system_prompt_gets_the_playbook():
    from app.project_chat import SYSTEM, ThreadPolicy
    from app.ai.agent_loop import TurnState

    text = SYSTEM.format(groups="-")
    assert 'get_playbook("ad-voiceover")' in text and "add_voiceover" in text and "check_voiceover" in text
    policy = ThreadPolicy(None, {"id": 1, "project_id": 9}, [], "Сделай рекламу сайта с озвучкой", None, "")
    policy.started = ""
    prompt = policy.prompt(TurnState(contents=[], emit=lambda *a, **k: None))
    assert "АКТИВНЫЙ ПЛЕЙБУК «ad-voiceover»" in prompt


def test_voice_group_and_paid_card():
    from app import chat_tools as CT

    assert set(CT.group_tools("voice")) >= {"add_voiceover", "check_voiceover", "tts_preview", "list_voices"}
    assert "voice" in CT.preload_for("Озвучь холст голосом Aoede")
    assert not CT.is_dangerous(None, "add_voiceover", {"lines": [], "confirm": True}, None)  # gemini — бесплатно
    assert CT.is_dangerous(None, "add_voiceover", {"provider": "polza", "confirm": True}, None)
    assert not CT.is_dangerous(None, "add_voiceover", {"provider": "polza"}, None)  # без confirm — только оценка


def test_mcp_instructions_and_app_promo_point_to_ad_voiceover():
    from app import playbooks
    from app.agent_api import MCP_INSTRUCTIONS

    assert 'get_playbook("ad-voiceover")' in MCP_INSTRUCTIONS and "add_voiceover" in MCP_INSTRUCTIONS
    assert "ad-voiceover" in playbooks.get_playbook("app-promo")["text"]
    book = playbooks.get_playbook("ad-voiceover")
    assert book["title"] == "Реклама сайта/товара с озвучкой"
    for tool in ("add_voiceover", "check_voiceover", "render_clip", "render_status"):
        assert tool in book["text"]


def test_motion_reference_documents_roles():
    from app.agent_tools import motion_reference, registry

    ref = motion_reference()
    assert "role — роль в сведении" in ref and "voice — голос/озвучка" in ref
    add = registry()["add_layers"].description
    assert "Звуковой ЭФФЕКТ" in add and "ГОЛОС" in add and "add_voiceover" in add
    preview = registry().get("tts_preview") or next(t for t in __import__("app.agent_tools").agent_tools.list_tools(
        include_discovery=True) if t.name == "tts_preview")
    assert "style —" not in preview.description and "Aoede — жен." in preview.description


def test_narrator_voice_brief_field_and_seed(tmp_path, monkeypatch):
    from app import projects as P
    from app.projects_brief import narrator_voice, seed_narrator_voices, update_brief

    store = _store(tmp_path, monkeypatch)
    ids = [int(P.create_project(store, f"p{i}")["id"]) for i in range(1, 10)]
    assert ids[-1] == 9
    assert seed_narrator_voices(store) == []  # проект 9 не Витубика — не трогаем
    store.db.execute("UPDATE projects SET name = 'vtubika.store' WHERE id = 9")
    assert seed_narrator_voices(store) == [9] and narrator_voice(store, 9) == "Aoede"
    update_brief(store, 9, {"narrator_voice": "Kore"})
    assert seed_narrator_voices(store) == [] and narrator_voice(store, 9) == "Kore"  # выбор владельца не перетираем
    assert P.get_brief(store, 9)["brief"]["narrator_voice"] == "Kore"
