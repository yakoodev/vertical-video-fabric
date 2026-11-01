"""Плейбуки агентов: файлы docs/playbooks — источник истины, MCP get_playbook и prompts отдают их же."""

from __future__ import annotations

import re
from pathlib import Path

from app import agent_tools, playbooks
from app.agent_api import mcp_handle
from app.agent_tools import ToolContext, run_tool

ROOT = Path(__file__).resolve().parent.parent
NAMES = {"stream-shorts", "stream-horizontal", "meme-insert", "app-promo"}
# Инструменты, которых в этой ветке может не быть (делает другой агент) — плейбук ссылается условно.
OPTIONAL_TOOLS = {"censor_profanity"}


def test_files_have_steps_tools_and_checklist():
    items = {p["name"]: p for p in playbooks.list_playbooks()}
    assert NAMES <= set(items)
    for name in NAMES:
        assert items[name]["title"] and items[name]["description"], name
        text = playbooks.get_playbook(name)["text"]
        for section in ("## Шаги", "## Инструменты MCP", "## Чек-лист перед сдачей"):
            assert section in text, f"{name}: нет «{section}»"
        assert "- [ ]" in text, f"{name}: чек-лист без пунктов"


def test_tools_named_in_playbooks_exist():
    known = {t.name for t in agent_tools.list_tools(include_discovery=True)} | OPTIONAL_TOOLS
    for name in NAMES:
        text = playbooks.get_playbook(name)["text"]
        block = text.split("## Инструменты MCP", 1)[1].split("##", 1)[0]
        named = set(re.findall(r"`([a-z_]+)`", block))
        assert named and named <= known, f"{name}: нет инструментов {sorted(named - known)}"


def test_stream_shorts_covers_the_order():
    text = playbooks.get_playbook("stream-shorts")["text"]
    for must in ("6–10", "20–90", "чат:", "censor_profanity", "mute_audio", "−14 LUFS", "−1.5 dBTP",
                 "Content ID", "set_nameplate", "contact_sheet", "меню паузы"):
        assert must in text, must


def test_agents_md_links_every_playbook():
    agents = (ROOT / "docs" / "AGENTS.md").read_text(encoding="utf-8")
    assert "## Плейбуки" in agents and "get_playbook" in agents
    for name in NAMES:
        assert f"(playbooks/{name}.md)" in agents, name


def test_get_playbook_tool(tmp_path):
    ctx = ToolContext(store=None, clip_plan_id=None)
    out, ok = run_tool(ctx, "get_playbook", {})
    assert ok and NAMES <= {p["name"] for p in out["playbooks"]}
    out, ok = run_tool(ctx, "get_playbook", {"name": "stream-shorts"})
    assert ok and out["text"].startswith("# Нарезка стрима")
    out, ok = run_tool(ctx, "get_playbook", {"name": "Stream-Shorts.md"})
    assert ok and out["name"] == "stream-shorts"
    out, ok = run_tool(ctx, "get_playbook", {"name": "../AGENTS"})
    assert not ok and "stream-shorts" in out["error"]
    out, ok = run_tool(ctx, "get_playbook", {"name": "нет-такого"})
    assert not ok


def test_mcp_prompts_are_playbooks():
    init = mcp_handle(None, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert init["result"]["capabilities"]["prompts"] == {"listChanged": False}
    listed = mcp_handle(None, {"jsonrpc": "2.0", "id": 2, "method": "prompts/list"})["result"]["prompts"]
    assert NAMES <= {p["name"] for p in listed}
    got = mcp_handle(None, {"jsonrpc": "2.0", "id": 3, "method": "prompts/get", "params": {"name": "app-promo"}})
    message = got["result"]["messages"][0]
    assert message["role"] == "user" and message["content"]["text"] == playbooks.get_playbook("app-promo")["text"]
    missing = mcp_handle(None, {"jsonrpc": "2.0", "id": 4, "method": "prompts/get", "params": {"name": "x"}})
    assert missing["error"]["code"] == -32602
    assert mcp_handle(None, {"jsonrpc": "2.0", "id": 5, "method": "resources/list"})["result"] == {"resources": []}


def test_vtuber_story_covers_qa_fixes():
    """QA 01.11 (клип 889): плейбук ведёт к avatar_full, крючку без спойлера, плашке в углу, ч/б на
    отрезке, звуку на ключевое слово и проверке глазами — и всё это есть в инструментах."""
    text = playbooks.get_playbook("vtuber-story")["text"]
    for must in ("avatar_full", "detect_avatar", "спойлер", "Что ползёт по экрану", "corner", "grayscale",
                 "at_word: \"обосралась\"", "contact_sheet", "уже применено", "mode: \"full\"", "- [ ]"):
        assert must in text, must
    known = {t.name for t in agent_tools.list_tools(include_discovery=True)}
    named = set(re.findall(r"`([a-z_]+)`", text))
    tools = named & {"detect_avatar", "set_layout", "set_nameplate", "censor_profanity", "emphasize_words",
                     "add_insert", "set_camera", "lint_clip", "view_frame", "contact_sheet", "set_backdrop"}
    assert len(tools) >= 10 and tools <= known
    agents = (ROOT / "docs" / "AGENTS.md").read_text(encoding="utf-8")
    assert "(playbooks/vtuber-story.md)" in agents and "avatar_full" in agents

