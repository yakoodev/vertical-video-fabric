"""Учёт трат Gemini (app/gemini_usage.py): запись каждого запроса клиента, цена с размышлениями и порогами,
группировки API, итог переписки в чате. Сеть не нужна: клиент Gemini с подменённым транспортом."""

from __future__ import annotations

from datetime import date

import pytest

from app import gemini_usage as U
from app.ai.gemini import GeminiClient


class FakeGemini(GeminiClient):
    """Настоящий клиент (учёт — в его generate_content / stream_generate_content), транспорт подменён."""

    def __init__(self, responses):
        super().__init__(api_key="k", base_url="https://example.invalid/v1beta")
        self.responses = list(responses)

    def _generate_content(self, model, payload):
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def _stream_generate_content(self, model, payload):
        yield from self.responses.pop(0)


def _store(tmp_path):
    from app.crypto import CookieCipher
    from app.db import Database
    from app.store import AppStore

    db = Database(tmp_path / "t.sqlite3")
    db.init()
    return AppStore(db, CookieCipher(tmp_path / "k.key"))


@pytest.fixture
def store(tmp_path):
    s = _store(tmp_path)
    U.bind_store(lambda: s)
    yield s
    U.bind_store(None)


def _rows(store):
    return [dict(r) for r in store.db.query_all("SELECT * FROM gemini_usage ORDER BY id")]


def _usage(prompt=1000, cached=0, out=100, thoughts=0, **extra):
    data = {"promptTokenCount": prompt, "candidatesTokenCount": out, "totalTokenCount": prompt + out + thoughts}
    if cached:
        data["cachedContentTokenCount"] = cached
    if thoughts:
        data["thoughtsTokenCount"] = thoughts
    data.update(extra)
    return data


def _answer(text="ок", usage=None, version=""):
    body = {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}}]}
    if usage is not None:
        body["usageMetadata"] = usage
    if version:
        body["modelVersion"] = version
    return body


# --------------------------------------------------------------------------- #
# Цена
# --------------------------------------------------------------------------- #


def test_cost_counts_cached_and_thinking_as_output():
    price = {"input": 2.0, "cached": 0.2, "output": 12.0}
    usage = _usage(prompt=100_000, cached=40_000, out=2_000, thoughts=3_000)
    cost = U.compute_cost(usage, price, date(2026, 11, 12))
    expected = (60_000 * 2.0 + 40_000 * 0.2 + (2_000 + 3_000) * 12.0) / 1e6
    assert cost["cost_usd"] == pytest.approx(expected)
    assert cost["tier"] == ""


def test_cost_long_context_tier_for_pro():
    price = U.DEFAULT_PRICES["gemini-3.1-pro-preview"]
    short = U.compute_cost(_usage(prompt=200_000, out=1_000), price, date(2026, 11, 12))
    long = U.compute_cost(_usage(prompt=200_001, out=1_000), price, date(2026, 11, 12))
    assert short["tier"] == "" and short["cost_usd"] == pytest.approx((200_000 * 2 + 1_000 * 12) / 1e6)
    assert long["tier"] == "long" and long["cost_usd"] == pytest.approx((200_001 * 4 + 1_000 * 18) / 1e6)


def test_cost_price_change_by_date():
    price = U.DEFAULT_PRICES["gemini-3.8-flash"]
    usage = _usage(prompt=1_000_000, out=1_000_000)
    assert U.compute_cost(usage, price, date(2026, 12, 31))["cost_usd"] == pytest.approx(0.75 + 3.75)
    assert U.compute_cost(usage, price, date(2027, 1, 1))["cost_usd"] == pytest.approx(1.50 + 7.50)


def test_cost_audio_input_and_image_output_have_own_prices():
    audio = U.compute_cost(_usage(prompt=1_000, out=0, promptTokensDetails=[
        {"modality": "TEXT", "tokenCount": 400}, {"modality": "AUDIO", "tokenCount": 600}]),
        U.DEFAULT_PRICES["gemini-3.1-flash-lite"], date(2026, 11, 12))
    assert audio["cost_usd"] == pytest.approx((400 * 0.25 + 600 * 0.50) / 1e6)
    image = U.compute_cost(_usage(prompt=100, out=1_300, candidatesTokensDetails=[
        {"modality": "IMAGE", "tokenCount": 1_290}, {"modality": "TEXT", "tokenCount": 10}]),
        U.DEFAULT_PRICES["gemini-3.1-flash-image"], date(2026, 11, 12))
    assert image["cost_usd"] == pytest.approx((100 * 0.5 + 10 * 3.0 + 1_290 * 60.0) / 1e6)


def test_cost_without_usage_or_price_is_none():
    assert U.compute_cost(None, {"input": 1})["cost_usd"] is None
    assert U.compute_cost({}, {"input": 1})["cost_usd"] is None
    assert U.compute_cost(_usage(), None)["cost_usd"] is None


def test_price_lookup_exact_prefix_fallback():
    assert U.price_for("models/gemini-3.1-pro-preview")[0::2] == ("gemini-3.1-pro-preview", "exact")
    assert U.price_for("gemini-3.1-pro-preview-customtools")[0::2] == ("gemini-3.1-pro-preview", "prefix")
    assert U.price_for("gemini-9-flash-lite-next")[0::2] == ("gemini-3.5-flash-lite", "fallback")
    assert U.price_for("totally-unknown")[1] is None


def test_owner_overrides_and_rate(store):
    U.set_prices(store, {"gemini-3.5-flash": {"input": 0.5, "output": 3}, "gemini-2.5-pro": None}, rate=90)
    table = U.prices(store)
    assert table["gemini-3.5-flash"]["input"] == 0.5 and table["gemini-3.5-flash"]["cached"] == 0.075
    assert "gemini-2.5-pro" not in table
    assert U.usd_rub(store) == 90
    with pytest.raises(ValueError):
        U.set_prices(store, {"x": {"input": -1}})
    with pytest.raises(ValueError):
        U.set_prices(store, rate=0)


# --------------------------------------------------------------------------- #
# Запись через клиент
# --------------------------------------------------------------------------- #


def test_client_call_is_recorded_with_scope_and_cost(store):
    client = FakeGemini([_answer(usage=_usage(prompt=10_000, cached=2_000, out=500, thoughts=1_500),
                                 version="gemini-3.1-pro-preview")])
    with U.scope("explainer_script", project_id=7, session="explainer:3"):
        reply = client.generate_content("gemini-3.1-pro-preview", {"contents": []})
    assert reply["candidates"]
    [row] = _rows(store)
    assert row["purpose"] == "explainer_script" and row["project_id"] == 7 and row["session"] == "explainer:3"
    assert row["model"] == "gemini-3.1-pro-preview" and row["ok"] == 1 and row["method"] == "generate"
    assert (row["prompt_tokens"], row["cached_tokens"], row["output_tokens"], row["thinking_tokens"]) == \
        (10_000, 2_000, 500, 1_500)
    expected = (8_000 * 2 + 2_000 * 0.2 + 2_000 * 12) / 1e6
    assert row["cost_usd"] == pytest.approx(expected)
    assert row["cost_rub"] == pytest.approx(expected * U.DEFAULT_USD_RUB)
    assert row["duration_ms"] is not None and "test_gemini_usage" in row["caller"]


def test_missing_usage_metadata_records_null_tokens(store):
    client = FakeGemini([_answer()])
    assert client.generate_content("gemini-3.5-flash", {})["candidates"]
    [row] = _rows(store)
    assert row["ok"] == 1
    assert row["prompt_tokens"] is None and row["output_tokens"] is None and row["total_tokens"] is None
    assert row["cost_usd"] is None and row["cost_rub"] is None
    totals = U.totals(store)["total"]
    assert totals["calls"] == 1 and totals["unpriced"] == 1 and totals["cost_rub"] == 0


def test_failed_call_recorded_and_reraised_without_secrets(store):
    client = FakeGemini([RuntimeError("Gemini network request failed: via http://user:pass@10.0.0.1:8080")])
    with pytest.raises(RuntimeError):
        client.generate_content("gemini-3.5-flash", {})
    [row] = _rows(store)
    assert row["ok"] == 0 and "RuntimeError" in row["error"]
    assert "pass" not in row["error"] and "•••@" in row["error"]


def test_nothing_bound_nothing_breaks(tmp_path, monkeypatch):
    U.bind_store(None)
    monkeypatch.setattr("app.proxy_config._get_store", None)
    assert FakeGemini([_answer(usage=_usage())]).generate_content("gemini-3.5-flash", {})["candidates"]


def test_broken_store_does_not_break_call(store, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(store.db, "execute", boom)
    assert FakeGemini([_answer(usage=_usage())]).generate_content("gemini-3.5-flash", {})["candidates"]


def test_stream_uses_final_usage_and_marks_abandoned(store):
    chunks = [_answer("При", usage={"promptTokenCount": 1_000}),
              _answer("вет", usage=_usage(prompt=1_000, out=50, thoughts=200))]
    client = FakeGemini([chunks, [_answer("a"), _answer("b", usage=_usage(prompt=5, out=5))]])
    assert len(list(client.stream_generate_content("gemini-3.5-flash", {}))) == 2
    stream = client.stream_generate_content("gemini-3.5-flash", {})
    next(stream)
    stream.close()  # «Стоп» посреди потока
    done, cancelled = _rows(store)
    assert done["method"] == "stream" and done["ok"] == 1
    assert (done["output_tokens"], done["thinking_tokens"]) == (50, 200)
    assert cancelled["ok"] == 0 and cancelled["error"] == "cancelled" and cancelled["prompt_tokens"] is None


def test_nested_scope_inherits_ids_and_default_purpose():
    with U.scope("clip_chat", session="chat:5", clip_id=9):
        with U.scope(default_purpose="montage_agent", clip_id=9):
            assert U.current_scope() == {"purpose": "clip_chat", "session": "chat:5", "clip_id": 9}
        with U.scope("tts"):
            assert U.current_scope()["purpose"] == "tts" and U.current_scope()["session"] == "chat:5"
    with U.scope(default_purpose="montage_agent"):
        assert U.current_scope()["purpose"] == "montage_agent"
    assert U.current_scope() == {}


def test_purpose_from_module_slug():
    assert U._slug("app.ai.publish_meta") == "publish_meta"
    assert U._slug("app.explainer_gen") == "explainer_script"
    assert U._slug("app.ai.speakers") == "separate_voices"


def test_project_resolved_from_clip(store):
    from app import projects as P
    from app.settings import settings

    source = store.create_source("upload", settings.source_dir / "a.mp4", original_filename="a.mp4",
                                 duration_sec=60, status="ready")
    pid = P.create_project(store, "Проект", kind="stream_channel", source_ids=[source["id"]])["id"]
    analysis = store.create_ai_analysis(source["id"], "manual", status="succeeded")
    seg = store.create_ai_segment(analysis["id"], {"start_sec": 1, "end_sec": 20, "title": "x"})
    plan = store.create_clip_plan(source["id"], analysis["id"], "x", segment_ids=[seg["id"]])
    with U.scope("clip_chat", clip_id=plan["id"]):
        FakeGemini([_answer(usage=_usage())]).generate_content("gemini-3.5-flash", {})
    [row] = _rows(store)
    assert (row["clip_id"], row["source_id"], row["project_id"]) == (plan["id"], source["id"], pid)


def test_recompute_after_price_change(store):
    FakeGemini([_answer(usage=_usage(prompt=1_000_000, out=0))]).generate_content("gemini-3.5-flash", {})
    assert _rows(store)[0]["cost_usd"] == pytest.approx(0.75)
    U.set_prices(store, {"gemini-3.5-flash": {"input": 0.5}}, rate=100)
    assert U.recompute(store)["updated"] == 1
    row = _rows(store)[0]
    assert row["cost_usd"] == pytest.approx(0.5) and row["cost_rub"] == pytest.approx(50)


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #


def _seed(store):
    U.record("gemini-3.5-flash", _answer(usage=_usage(prompt=1_000_000, out=0)), purpose="analysis",
             session="analysis:1", project_id=1)
    U.record("gemini-3.5-flash", _answer(usage=_usage(prompt=1_000_000, out=0)), purpose="analysis",
             session="analysis:1", project_id=1)
    U.record("gemini-3.1-pro-preview", _answer(usage=_usage(prompt=0, out=1_000_000)), purpose="agent_chat",
             session="chat:4", message_id=11, project_id=2)
    store.db.execute("UPDATE gemini_usage SET created_at = '2026-11-01 10:00:00' WHERE id = 1")


def test_api_totals_grouping_and_calls(tmp_path, monkeypatch):
    from test_api_pipeline import _client

    client, store = _client(tmp_path, monkeypatch)
    _seed(store)
    total = client.get("/api/usage/gemini").json()["total"]
    assert total["calls"] == 3 and total["cost_usd"] == pytest.approx(0.75 * 2 + 12)
    assert total["cost_rub"] == pytest.approx((0.75 * 2 + 12) * U.DEFAULT_USD_RUB)

    by_purpose = client.get("/api/usage/gemini", params={"group_by": "purpose"}).json()["groups"]
    assert [(g["key"], g["calls"]) for g in by_purpose] == [("agent_chat", 1), ("analysis", 2)]
    by_model = {g["key"]: g["cost_usd"] for g in client.get("/api/usage/gemini?group_by=model").json()["groups"]}
    assert by_model == {"gemini-3.1-pro-preview": pytest.approx(12), "gemini-3.5-flash": pytest.approx(1.5)}
    days = client.get("/api/usage/gemini?group_by=day").json()["groups"]
    assert days[-1]["key"] == "2026-11-01" and days[-1]["calls"] == 1
    sessions = client.get("/api/usage/gemini?group_by=session&project_id=1").json()
    assert [(g["key"], g["calls"]) for g in sessions["groups"]] == [("analysis:1", 2)]

    since = client.get("/api/usage/gemini", params={"since": "2026-11-02T00:00:00+04:00"}).json()["total"]
    assert since["calls"] == 2
    assert client.get("/api/usage/gemini?group_by=nope").status_code == 400
    assert client.get("/api/usage/gemini?since=вчера").status_code == 400

    calls = client.get("/api/usage/gemini/calls", params={"session": "chat:4"}).json()["calls"]
    assert len(calls) == 1 and calls[0]["message_id"] == 11 and calls[0]["ok"] is True

    prices = client.get("/api/usage/gemini/prices").json()
    assert prices["usd_rub"] == U.DEFAULT_USD_RUB and "gemini-3.1-pro-preview" in prices["prices"]
    changed = client.put("/api/usage/gemini/prices", json={"usd_rub": 100}).json()
    assert changed["usd_rub"] == 100
    assert client.put("/api/usage/gemini/prices", json={"overrides": {"m": {"input": "x"}}}).status_code == 400
    assert client.post("/api/usage/gemini/recompute", json={}).json()["updated"] == 3
    assert client.get("/api/usage/gemini").json()["total"]["cost_rub"] == pytest.approx((0.75 * 2 + 12) * 100)


def test_chat_thread_shows_session_spend(tmp_path, monkeypatch):
    from app import project_chat as C
    from app import projects as P
    from test_api_pipeline import _client

    client, store = _client(tmp_path, monkeypatch)
    monkeypatch.setattr("app.ai.editor_agent.client_factory", None)
    pid = P.create_project(store, "Мура", kind="stream_channel")["id"]
    thread = client.post("/api/chat/threads", json={"project_id": pid}).json()
    gemini = FakeGemini([[_answer("Привет", usage=_usage(prompt=1_000_000, out=0))]] * 3)
    sent = C.send_to_thread(store, thread["id"], "привет", client=gemini)
    assert C.wait_thread(thread["id"], 10)
    view = client.get(f"/api/chat/threads/{thread['id']}").json()
    usage = view["usage"]
    assert usage["session"] == f"chat:{thread['id']}" and usage["calls"] >= 1 and usage["cost_rub"] > 0
    reply = next(m for m in view["messages"] if m["id"] == sent["reply_id"])
    assert reply["usage"]["calls"] >= 1
    rows = _rows(store)
    turn = [r for r in rows if r["message_id"] == sent["reply_id"]]
    assert turn and all(r["purpose"] == "agent_chat" and r["project_id"] == pid for r in turn)
