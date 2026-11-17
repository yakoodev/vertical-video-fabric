"""Сквозные правила владельца и маршрут инструментов по плейбукам (разбор встроенных агентов 17.11,
docs/review-2026-11-17-agents.md): правила одни для MCP, чата проекта и плейбука handoff; плейбук сам подгружает свои
узкие группы инструментов; каждый инструмент плейбука достижим из чата; готовое — в ленту."""

from __future__ import annotations

from app import chat_tools as CT
from app import playbooks
from app import project_chat_playbook as PB
from app.owner_rules import OWNER_RULES

# Ключевые слова правил: и в выжимке (системные промпты), и в плейбуке handoff — держим их вместе.
RULE_MARKS = ("contact_sheet", "с музыкой", "безопасной зоне", "feed_add_version", "render_clip_id", "CC0",
              "POW", "английских слов", "ИИ-музыки", "с телефона", "по ходу движения", "проба", "за ночь",
              "Кадр 0", "P0", "8/10", "сдавать / ещё проход", "исправлено / частично / осталось",
              "видимый результат", "сквозной герой")


def test_owner_rules_are_in_mcp_chat_and_handoff():
    from app.agent_api import MCP_INSTRUCTIONS
    from app.project_chat import _system

    assert OWNER_RULES in MCP_INSTRUCTIONS
    assert OWNER_RULES in _system({"id": 1, "project_id": 3}, [])
    assert OWNER_RULES in _system({"id": 1}, [])  # общий чат — тоже
    handoff = playbooks.get_playbook("handoff")["text"].replace("\n", " ")
    rules = OWNER_RULES.replace("\n", " ")
    for mark in RULE_MARKS:
        assert mark in rules, f"выжимка: нет «{mark}»"
        assert mark.lower() in handoff.lower(), f"handoff: нет «{mark}»"


def test_every_playbook_ends_with_a_handoff():
    """Владелец 16.11: всё готовое — в «Ленту». Плейбук, который делает ролик, ведёт к ней (сам или через handoff)."""
    for book in playbooks.list_playbooks():
        if book["name"] in ("meme-insert", "handoff"):  # правка внутри клипа; сам handoff
            continue
        text = playbooks.get_playbook(book["name"])["text"]
        assert "handoff" in text or "feed_add_version" in text, book["name"]


def test_playbook_tools_reachable_and_routed_to_named_groups():
    """Каждый инструмент из «Инструменты MCP» плейбука есть в чате, лежит в базе или в именованной группе (не в
    «other», которую модель не догадывается грузить), и после подгрузки групп плейбука он в объявлениях."""
    names = [p["name"] for p in playbooks.list_playbooks()] + ["series"]
    base = set(CT.base_names())
    for name in names:
        tools = PB.playbook_tools(name)
        assert tools, name
        for tool in tools:
            assert tool in base or CT.group_of(tool) not in (None, "other"), f"{name}: {tool} в группе other"
        active = set(CT.active_names(PB.groups_for(name)))
        assert set(tools) <= active, f"{name}: {sorted(set(tools) - active)}"


def test_pinned_playbook_preloads_its_narrow_groups_only():
    """«ок, делай полную» в переписке мувика не называет инструментов: game и feed — сразу, а тяжёлые общие группы
    (montage, audio…) — подсказкой load_tools по шагу, чтобы не слать 150 тыс. знаков объявлений в каждом ходе."""
    from app.ai.agent_loop import TurnState
    from app.project_chat import ThreadPolicy

    policy = ThreadPolicy(None, {"id": 1, "project_id": 9, "playbook": "frag-movie"}, [], "ок, делай полную", None, "")
    prompt = policy.prompt(TurnState(contents=[], emit=lambda *a, **k: None))
    assert {"game", "feed"} <= set(policy.groups)
    assert not set(policy.groups) & PB.HEAVY_GROUPS
    assert "АКТИВНЫЙ ПЛЕЙБУК «frag-movie»" in prompt and "load_tools([" in prompt and '"montage"' in prompt
    assert {"game_events", "feed_add_version"} <= {d["name"] for d in policy.declarations(True)}

    ad = ThreadPolicy(None, {"id": 2, "project_id": 9}, [], "Сделай рекламу сайта vtubika.store с озвучкой", None, "")
    ad.prompt(TurnState(contents=[], emit=lambda *a, **k: None))
    assert {"web", "voice", "feed"} <= set(ad.groups)


def test_groups_cleanup_and_load_tools_lists_every_group():
    # Раньше в «other» лежали web_search/page_*, game_events, meme_edit, шаблоны моушна, банк голосов.
    assert set(CT.group_tools("other")) <= {"describe_tool", "list_modules", "export_timeline_package"}
    assert "web_search" in CT.group_tools("web") and "game_events" in CT.group_tools("game")
    assert "snap_subtitles_to_speech" in CT.group_tools("subtitles") and "detect_avatar" in CT.group_tools("cuts")
    # 3D-серии больше не в каждом ходе: своя группа, по словам и у проекта вида series3d.
    assert not any(n.startswith("series3d_") for n in CT.base_names())
    assert "series3d" in CT.preload_for("сделай новую 3д-серию про лифт")
    assert "game" in CT.preload_for("собери мувик из боёв") and "web" in CT.preload_for("реклама сайта")
    load = CT.all_tools()["load_tools"]
    for group in CT.all_groups():
        assert group in load.description, group
    assert load.parameters["properties"]["groups"]["items"]["enum"] == CT.all_groups()


def test_series3d_project_preloads_its_group(tmp_path, monkeypatch):
    from app import projects as P
    from app.project_chat import _project_groups
    from test_data_foundation import _store

    store = _store(tmp_path, monkeypatch)
    s3d = P.create_project(store, "3D-серия", kind="series3d")["id"]
    other = P.create_project(store, "Сова", kind="other")["id"]
    assert _project_groups(store, s3d) == ("series3d",)
    assert _project_groups(store, other) == () and _project_groups(store, None) == ()
    assert _project_groups(store, 99999) == ()
