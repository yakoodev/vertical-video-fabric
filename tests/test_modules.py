"""Модули сервиса (app/modules.py): реестр учитывает каждый инструмент, выключенный модуль
прячет инструменты и закрывает адреса, зависимости каскадом, env, работники, Whisper."""

from __future__ import annotations

import re
import threading
import time

import pytest

from app import agent_tools, chat_tools, modules
from app.settings import settings
from test_api_pipeline import _client

HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def _rpc(client, method, params=None):
    body = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    return client.post("/mcp", json=body, headers=HEADERS).json()["result"]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.delenv(modules.ENV_DISABLED, raising=False)
    monkeypatch.delenv(modules.ENV_ENABLED, raising=False)
    client, store = _client(tmp_path, monkeypatch)
    modules.invalidate()
    yield client, store
    modules.invalidate()


def _all_tool_names() -> set[str]:
    names = {t.name for t in agent_tools.registry().values()}
    names |= {t.name for t in agent_tools.DISCOVERY_TOOLS}
    names |= {t.name for t in agent_tools._project_tools()}
    names |= {t.name for t in chat_tools.chat_only_tools()}
    return names


# --------------------------------------------------------------------------- #
# Реестр
# --------------------------------------------------------------------------- #


def test_every_tool_belongs_to_exactly_one_module_or_core():
    names = _all_tool_names()
    owners: dict[str, list[str]] = {}
    for mod in modules.MODULES:
        for tool in mod.tools:
            owners.setdefault(tool, []).append(mod.id)
    for tool in modules.CORE_TOOLS:
        owners.setdefault(tool, []).append("core")
    unclassified = sorted(n for n in names if n not in owners)
    assert not unclassified, f"инструменты без модуля (добавь в app/modules.py): {unclassified}"
    twice = {n: o for n, o in owners.items() if len(o) > 1}
    assert not twice, f"инструмент в нескольких модулях: {twice}"
    stale = sorted(n for n in owners if n not in names)
    assert not stale, f"в app/modules.py инструменты, которых нет в реестре: {stale}"


def test_module_ids_groups_and_dependencies_are_sane():
    ids = [m.id for m in modules.MODULES]
    assert len(ids) == len(set(ids))
    groups = {g for g, _n in modules.GROUPS}
    for mod in modules.MODULES:
        assert mod.group in groups, mod.id
        assert mod.name and mod.description
        assert set(mod.cost) <= set(modules.COST_LABELS), mod.id
        for dep in mod.depends:
            assert dep in modules.MODULES_BY_ID, (mod.id, dep)
        assert mod.id not in modules.requirements(mod.id), f"цикл зависимостей у {mod.id}"
        # По умолчанию всё включено — поведение как до модулей.
        assert mod.default is True


def test_every_module_route_matches_a_real_endpoint():
    from app import main
    from tests.routes_util import flat_routes

    real = []
    for route in flat_routes(main.app.routes):
        path = getattr(route, "path", "")
        if not path or "{spa_path" in path:
            continue
        sample = re.sub(r"\{[^}]+\}", "1", path)
        real.append((set(getattr(route, "methods", None) or {"GET"}), sample))
    for mod in modules.MODULES:
        for spec in mod.routes:
            methods, pattern = modules._compile_route(spec)
            hit = [p for ms, p in real if pattern.match(p) and (methods is None or ms & methods)]
            assert hit, f"{mod.id}: адрес {spec!r} не совпал ни с одним маршрутом"
    # Основа не задета: модули и рендер — ничьи.
    assert modules.modules_for_route("GET", "/api/modules") == []
    assert modules.modules_for_route("POST", "/api/clip-plans/5/render") == []
    assert modules.modules_for_route("GET", "/api/clip-plans/5/subtitles") == []
    assert modules.modules_for_route("POST", "/api/clip-plans/5/subtitles/translate") == ["translation"]
    assert modules.modules_for_route("GET", "/api/projects/3/vod-watch") == ["vod_watch"]


# --------------------------------------------------------------------------- #
# Поведение
# --------------------------------------------------------------------------- #


def test_defaults_keep_everything_on(env):
    client, store = env
    assert all(modules.enabled_map(store).values())
    body = client.get("/api/modules").json()
    assert {m["id"] for m in body["modules"]} == set(modules.MODULES_BY_ID)
    assert all(m["enabled"] for m in body["modules"])
    assert {"rss_mb", "threads", "workers", "heavy", "whisper_loaded"} <= set(body["resources"])
    assert {w["module"] for w in body["resources"]["workers"]} >= {"publishing", "autopilot", "vod_watch"}
    tools = {t["name"] for t in _rpc(client, "tools/list")["tools"]}
    assert {"dub_clip", "translate_subtitles", "list_approvals", "list_modules"} <= tools


def test_disabling_a_module_hides_its_tools_and_blocks_its_endpoints(env):
    client, store = env
    res = client.put("/api/modules", json={"modules": {"dubbing": False}})
    assert res.status_code == 200, res.text
    assert next(m for m in res.json()["modules"] if m["id"] == "dubbing")["enabled"] is False

    tools = {t["name"] for t in _rpc(client, "tools/list")["tools"]}
    assert "dub_clip" not in tools and "translate_subtitles" in tools
    assert "dub_clip" not in chat_tools.all_tools(store)
    assert "dub_clip" not in {t.name for t in agent_tools.list_tools(store=store)}
    assert "dub_clip" not in {d["name"] for d in chat_tools.declarations(list(chat_tools.GROUPS))}

    called = _rpc(client, "tools/call", {"name": "dub_clip", "arguments": {"clip_id": 1}})
    assert called["isError"] is True
    assert "выключен в настройках" in called["content"][0]["text"]
    result, ok, _clip = chat_tools.run(store, "dub_clip", {"clip_id": 1}, project_id=None)
    assert ok is False and result["module"] == "dubbing"

    blocked = client.post("/api/clip-plans/1/dub", json={})
    assert blocked.status_code == 423
    assert blocked.json()["module"] == "dubbing"
    assert "Озвучка RU" in blocked.json()["detail"] and "выключен" in blocked.json()["detail"]
    # Соседние адреса живы.
    assert client.get("/api/modules").status_code == 200
    assert client.get("/api/sources").status_code == 200

    # Сервисная проверка — и мимо REST (автоматизация, агент изнутри).
    from app.dubbing import dub_clip

    with pytest.raises(modules.ModuleDisabled):
        dub_clip(store, 1)

    back = client.put("/api/modules", json={"modules": {"dubbing": True}})
    assert back.status_code == 200
    assert "dub_clip" in {t["name"] for t in _rpc(client, "tools/list")["tools"]}
    assert client.post("/api/clip-plans/999/dub", json={}).status_code != 423


def test_dependencies_cascade_with_confirmation(env):
    client, store = env
    asked = client.put("/api/modules", json={"modules": {"translation": False}})
    assert asked.status_code == 409
    body = asked.json()
    assert body["needs_confirm"] is True and body["also_off"] == ["dubbing"]
    assert modules.is_enabled("translation", store)  # без confirm ничего не поменялось

    dry = client.put("/api/modules", json={"modules": {"publishing": False}, "dry_run": True}).json()
    assert set(dry["also_off"]) == {"autopilot"}

    done = client.put("/api/modules", json={"modules": {"translation": False}, "confirm": True})
    assert done.status_code == 200
    state = modules.enabled_map(store)
    assert state["translation"] is False and state["dubbing"] is False

    # Включить зависимый — включится и то, без чего он не работает.
    up = client.put("/api/modules", json={"modules": {"dubbing": True}})
    assert up.status_code == 409 and up.json()["also_on"] == ["translation"]
    client.put("/api/modules", json={"modules": {"dubbing": True}, "confirm": True})
    state = modules.enabled_map(store)
    assert state["translation"] and state["dubbing"]

    # Цепочка: series → autopilot и vod_watch.
    plan = modules.plan({"series": False}, store)
    assert set(plan["also_off"]) == {"autopilot", "vod_watch", "ai_video", "explainer", "stories", "series3d"}
    assert modules.dependents("transcription") == ["voices", "censor"]


def test_effective_state_respects_dependencies_even_without_cascade(env):
    _client_, store = env
    store.set_app_setting(modules.SETTING_KEY, {"automation": False})  # руками в базе
    modules.invalidate(store)
    state = modules.enabled_map(store)
    assert state["automation"] is False and state["vod_watch"] is False
    view = next(m for m in modules.describe(store)["modules"] if m["id"] == "vod_watch")
    assert view["flag"] is True and view["enabled"] is False and view["blocked_by"] == ["automation"]


def test_env_override_wins_and_locks_the_toggle(env, monkeypatch):
    client, store = env
    monkeypatch.setenv(modules.ENV_DISABLED, "twitch_chat, tracking")
    body = client.get("/api/modules").json()
    chat = next(m for m in body["modules"] if m["id"] == "twitch_chat")
    assert chat["enabled"] is False and chat["locked"] == "env"
    assert client.get("/api/sources/1/chat/status").status_code == 423
    assert "track_layer" not in {t["name"] for t in _rpc(client, "tools/list")["tools"]}
    locked = client.put("/api/modules", json={"modules": {"twitch_chat": True}, "confirm": True})
    assert locked.status_code == 409 and "переменной окружения" in locked.json()["detail"]

    monkeypatch.setenv(modules.ENV_DISABLED, "all")
    monkeypatch.setenv(modules.ENV_ENABLED, "publishing")
    state = modules.enabled_map(store)
    assert state["publishing"] is True and state["dubbing"] is False


def test_only_the_owner_session_can_toggle(env, monkeypatch):
    client, _store = env
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "api_token", "secret-token")
    bearer = {"Authorization": "Bearer secret-token"}
    assert client.get("/api/modules", headers=bearer).status_code == 200
    denied = client.put("/api/modules", json={"modules": {"dubbing": False}}, headers=bearer)
    assert denied.status_code == 403
    assert "только владелец" in denied.json()["detail"]
    client.cookies.set(settings.auth_cookie_name, "secret-token")
    assert client.put("/api/modules", json={"modules": {"dubbing": False}}).status_code == 200
    client.cookies.clear()


def test_list_modules_tool_is_read_only(env):
    client, store = env
    modules.set_modules(store, {"covers": False})
    called = _rpc(client, "tools/call", {"name": "list_modules", "arguments": {}})
    assert called["isError"] is False
    data = called["structuredContent"]
    assert "covers" in data["disabled"]
    assert next(m for m in data["modules"] if m["id"] == "covers")["enabled"] is False
    names = {t["name"] for t in _rpc(client, "tools/list")["tools"]}
    assert not any(re.search(r"(set|toggle|enable|disable)_module", n) for n in names)
    assert {"generate_covers", "list_covers", "choose_cover", "compose_cover"}.isdisjoint(names)


def test_list_modules_names_only_callable_tools(env):
    """Агент эксперимента звал analyze_source, load_tools, delete_clip, add_source_from_url и approve_publication
    из list_modules и получал «нет такого инструмента»: в tools — только то, что принимает MCP, остальное — chat_only."""
    client, _store = env
    data = _rpc(client, "tools/call", {"name": "list_modules", "arguments": {}})["structuredContent"]
    listed = {t for m in data["modules"] for t in m["tools"]}
    chat_only = {t for m in data["modules"] for t in m.get("chat_only_tools", [])}
    assert listed, "list_modules без инструментов"
    for name in sorted(listed):
        agent_tools.get_tool(name)  # KeyError — значит list_modules назвал несуществующий инструмент
    missing = sorted(n for n in listed if n not in agent_tools.callable_tool_names())
    assert not missing, missing
    assert {"analyze_source", "load_tools", "delete_clip", "add_source_from_url", "approve_publication"} <= chat_only
    assert not listed & chat_only
    assert chat_only <= {t.name for t in chat_tools.chat_only_tools()}
    # Включённый модуль: каждый его вызываемый инструмент — в MCP tools/list.
    mcp = {t["name"] for t in _rpc(client, "tools/list")["tools"]}
    enabled_tools = {t for m in data["modules"] if m["enabled"] for t in m["tools"]}
    assert enabled_tools <= mcp, sorted(enabled_tools - mcp)
    for name in sorted(chat_only):
        called = _rpc(client, "tools/call", {"name": name, "arguments": {}})
        assert called["isError"] is True


def test_workers_stop_and_restart_without_service_restart(env, monkeypatch):
    _client_, store = env
    import app.main as app_main
    from app.worker import JobWorker

    worker = JobWorker(store)
    monkeypatch.setattr(app_main, "worker", worker)
    monkeypatch.setattr(settings, "worker_poll_seconds", 0.05)
    modules.start_workers(store)
    try:
        assert worker._thread is not None and worker._thread.is_alive()
        modules.set_modules(store, {"publishing": False, "autopilot": False})
        assert not worker._thread.is_alive()
        view = {w["name"]: w for w in modules.workers_view(store)}
        assert view["job-worker"]["running"] is False and view["job-worker"]["module_enabled"] is False
        modules.set_modules(store, {"publishing": True})
        deadline = time.monotonic() + 2
        while not worker._thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert worker._thread.is_alive()
        assert threading.active_count() >= 2
    finally:
        modules.stop_workers()
    assert not worker._thread.is_alive()


def test_whisper_is_not_loaded_when_transcription_is_off(env, tmp_path):
    _client_, store = env
    from app.ai.transcript import transcribe_source_cues
    from app.subtitles.whisper_local import WhisperSubtitleProvider

    loaded = []
    modules.set_modules(store, {"transcription": False, "voices": False, "censor": False})
    provider = WhisperSubtitleProvider(model_loader=lambda size: loaded.append(size))
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF")
    with pytest.raises(modules.ModuleDisabled):
        provider.transcribe(audio, {}, "small")
    assert loaded == []
    assert transcribe_source_cues(str(audio)) == []
    with pytest.raises(modules.ModuleDisabled):
        transcribe_source_cues(str(audio), strict=True)
    assert {"get_source_transcript", "listen", "generate_subtitles", "separate_voices", "censor_profanity"}.isdisjoint(
        {t.name for t in agent_tools.list_tools(include_discovery=True, store=store)})


def test_telegram_and_auto_meta_follow_their_modules(env, monkeypatch):
    _client_, store = env
    from app import notify_tg
    from app.ai import publish_meta

    monkeypatch.setattr(notify_tg, "config", lambda _s: {"token": "t", "chat_id": "1", "enabled": True})
    monkeypatch.setattr(notify_tg, "_env_off", lambda: False)
    monkeypatch.setattr(settings, "auto_publish_meta", True)
    monkeypatch.setattr(settings, "gemini_api_key", "k")
    assert notify_tg.is_enabled(store) and publish_meta.auto_meta_enabled(store)
    modules.set_modules(store, {"telegram": False, "publish_meta": False})
    assert not notify_tg.is_enabled(store)
    assert not publish_meta.auto_meta_enabled(store)
