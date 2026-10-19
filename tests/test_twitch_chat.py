"""💬 Чат Twitch-VOD: разбор, хранение, хайп, привязка к клипу, ручки — без сети."""

from __future__ import annotations

import threading

import pytest

from app import twitch_chat as tc
from app.settings import settings


def _node(i: int, offset: float, text: str, *, user: str = "", emote: str = "", color: str | None = "#ff0000",
          badges: list | None = None) -> dict:
    fragments = [{"text": text, "emote": None}]
    if emote:
        fragments = [{"text": emote, "emote": {"emoteID": "25"}}, {"text": " " + text, "emote": None}]
    return {
        "id": f"m{i}",
        "contentOffsetSeconds": offset,
        "commenter": {"id": str(i), "login": (user or f"u{i}").lower(), "displayName": user or f"U{i}"},
        "message": {"fragments": fragments, "userBadges": badges or [], "userColor": color},
    }


class FakeClient:
    """Отдаёт сообщения «как Twitch»: страница ≤ page_size с запрошенной секунды."""

    def __init__(self, nodes: list[dict], *, length: float = 3600, page_size: int = 5) -> None:
        self.nodes = sorted(nodes, key=lambda n: n["contentOffsetSeconds"])
        self.length = length
        self.page_size = page_size
        self.calls: list[int] = []
        self.lock = threading.Lock()

    def video_info(self, vod_id):
        if vod_id == "404":
            return None
        return {"vod_id": vod_id, "length_sec": self.length, "channel_id": "77", "channel_login": "streamer"}

    def clip_info(self, slug):
        return {"vod_id": "123456789", "start_sec": 100.0, "duration_sec": 30.0}

    def comments_page(self, vod_id, offset):
        with self.lock:
            self.calls.append(offset)
        rest = [n for n in self.nodes if n["contentOffsetSeconds"] >= offset]
        page = rest[: self.page_size]
        return page, len(rest) > len(page)

    def badges(self, channel_id):
        return {"subscriber/12": "https://badge/sub12"}

    def third_party_emotes(self, channel_id):
        return {"KEKW": "https://cdn.7tv.app/emote/kekw/2x.webp", "OMEGALUL": "https://bttv/omega"}


def _store(tmp_path, monkeypatch):
    from test_render import _store as render_store

    return render_store(tmp_path, monkeypatch)


def _source(store, url="https://www.twitch.tv/videos/123456789", duration=3600.0):
    path = settings.source_dir / "vod.mp4"
    path.write_bytes(b"vod")
    return store.create_source("twitch_url", path, original_url=url, duration_sec=duration, status="ready")


# ------------------------------------------------------------------- разбор


def test_parse_vod_refs():
    assert tc.parse_twitch_vod_ref("https://www.twitch.tv/videos/2901714894?t=1h2m") == {"vod_id": "2901714894"}
    assert tc.parse_twitch_vod_ref("https://www.twitch.tv/kaicenat/video/2901714894") == {"vod_id": "2901714894"}
    assert tc.parse_twitch_vod_ref("v2901714894") == {"vod_id": "2901714894"}
    assert tc.parse_twitch_vod_ref("https://clips.twitch.tv/FunnySlug-abc") == {"clip_slug": "FunnySlug-abc"}
    assert tc.parse_twitch_vod_ref("https://www.twitch.tv/kaicenat/clip/Slug_1") == {"clip_slug": "Slug_1"}
    assert tc.parse_twitch_vod_ref("https://www.twitch.tv/kaicenat") == {}
    assert tc.parse_twitch_vod_ref("https://youtube.com/watch?v=12345678") == {}
    assert tc.parse_twitch_vod_ref("") == {}


def test_parse_comment_and_render_fragments():
    row = tc.parse_comment(
        _node(1, 12.5, "lol KEKW", user="Bob", emote="Kappa", badges=[{"setID": "subscriber", "version": "12"}])
    )
    assert row["msg_id"] == "m1" and row["offset_sec"] == 12.5 and row["user_name"] == "Bob"
    assert row["text"] == "Kappa lol KEKW" and row["badges_json"] == '["subscriber/12"]'
    frags = tc.render_fragments(row["text"], row["fragments_json"], {"KEKW": "https://x/kekw"})
    assert frags == [
        {"emote": "Kappa", "url": "https://static-cdn.jtvnw.net/emoticons/v2/25/default/dark/2.0"},
        {"text": " lol "},
        {"emote": "KEKW", "url": "https://x/kekw"},
    ]
    # Без родных смайлов фрагменты не храним, но сторонние находятся по тексту.
    plain = tc.parse_comment(_node(2, 1, "OMEGALUL что это"))
    assert plain["fragments_json"] == ""
    assert tc.render_fragments(plain["text"], "", {"OMEGALUL": "u"}) == [
        {"emote": "OMEGALUL", "url": "u"},
        {"text": " что это"},
    ]
    assert tc.parse_comment({"id": "x"}) is None
    assert tc.default_color("bob") == tc.default_color("BOB") and tc.default_color("bob").startswith("#")


def test_third_party_emote_maps():
    assert tc._seventv_set({"emotes": [{"name": "KEKW", "data": {"host": {"url": "//cdn.7tv.app/emote/1"}}}]}) == {
        "KEKW": "https://cdn.7tv.app/emote/1/2x.webp"
    }
    assert tc._bttv_user({"channelEmotes": [{"id": "a1", "code": "catJAM"}], "sharedEmotes": []}) == {
        "catJAM": "https://cdn.betterttv.net/emote/a1/2x.webp"
    }
    ffz = {"sets": {"1": {"emoticons": [{"name": "OMEGALUL", "urls": {"1": "//cdn.ffz/1", "2": "//cdn.ffz/2"}}]}}}
    assert tc._ffz_sets(ffz) == {"OMEGALUL": "https://cdn.ffz/2"}


# ------------------------------------------------------------------- импорт и хранение


def test_import_stores_all_messages_with_dedup_and_status(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    source = _source(store, duration=3600)
    # Плотная секунда (7 сообщений на 10-й секунде при странице 5) и редкие дальше.
    nodes = [_node(i, 10, f"hi {i}") for i in range(7)] + [_node(100 + i, 50 + i * 40, "x") for i in range(80)]
    client = FakeClient(nodes, length=3600, page_size=5)
    status = tc.import_chat(store, source["id"], client=client, workers=3)
    assert status["status"] == "succeeded" and status["vod_id"] == "123456789"
    assert status["channel_login"] == "streamer" and status["progress"] == 1.0
    # В плотной секунде страница режет часть сообщений — как у настоящего Twitch;
    # всё остальное — без потерь и без дублей.
    sparse = tc.list_messages(store, source["id"], 20, None, 1000)
    assert len(sparse) == len([n for n in nodes if n["contentOffsetSeconds"] >= 20 and n["contentOffsetSeconds"] <= 3600])
    assert status["message_count"] == len(tc.list_messages(store, source["id"], None, None, 1000))
    assert tc.chat_emotes(store, source["id"])["KEKW"].endswith("2x.webp")
    # Повторный импорт не плодит дубли.
    again = tc.import_chat(store, source["id"], client=FakeClient(nodes, length=3600, page_size=5))
    assert again["message_count"] == status["message_count"]
    # Удаление источника уносит чат каскадом.
    store.delete_source(source["id"])
    assert store.db.query_one("SELECT COUNT(*) AS n FROM chat_messages")["n"] == 0
    assert store.db.query_one("SELECT COUNT(*) AS n FROM source_chat")["n"] == 0


def test_import_with_vod_start_shift_and_clip_url(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    source = _source(store, url="https://clips.twitch.tv/SomeSlug", duration=30)
    nodes = [_node(i, 90 + i * 2, "msg") for i in range(40)]  # 90..168
    status = tc.import_chat(store, source["id"], client=FakeClient(nodes, length=1000))
    assert status["status"] == "succeeded" and status["vod_start_sec"] == 100
    times = [m["t"] for m in tc.list_messages(store, source["id"])]
    assert times and min(times) >= 0 and max(times) <= 31  # 100..131 VOD → 0..31 источника
    assert times[0] == 0


def test_import_failure_is_reported(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    source = _source(store, url="https://www.twitch.tv/videos/404404404")
    client = FakeClient([])
    client.video_info = lambda vod_id: None  # type: ignore[method-assign]
    status = tc.import_chat(store, source["id"], client=client)
    assert status["status"] == "failed" and "недоступен" in status["error"]
    upload = store.create_source("upload", settings.source_dir / "vod.mp4", status="ready")
    with pytest.raises(ValueError):
        tc.ChatImportService(lambda: store).start(upload["id"])


def test_stale_running_status_reads_as_failed(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    source = _source(store)
    tc._upsert_status(store, source["id"], status="running")
    assert tc.get_chat_status(store, source["id"])["status"] == "failed"
    assert tc.get_chat_status(store, 999)["status"] == "none"


# ------------------------------------------------------------------- активность


def test_activity_finds_spikes_and_reactions():
    messages = [(float(t), "просто болтаем") for t in range(0, 600, 5)]  # фон: 2 сообщ./10 с
    messages += [(300 + i * 0.2, "KEKW KEKW") for i in range(40)]  # смех на 5:00
    messages += [(450 + i * 0.3, "ЭТО ЖЕ НЕВЕРОЯТНО") for i in range(25)]  # капс на 7:30
    messages.sort()
    act = tc.score_activity(messages, bucket=10, duration=600, emote_names=["KEKW"])
    assert act["total"] == len(messages) and len(act["buckets"]) == 60
    by_t = {b["t"]: b for b in act["buckets"]}
    assert by_t[300]["hype"] == 1.0 and by_t[300]["top"] == "KEKW" and by_t[300]["top_count"] == 40
    assert by_t[450]["caps"] >= 10 and by_t[450]["hype"] > by_t[100]["hype"]
    assert max(b["hype"] for b in act["buckets"]) == 1.0

    windows = tc.hype_windows_from_activity(act, top=2)
    assert [w["peak_t"] for w in windows] == [300, 450]
    assert windows[0]["top"] == "KEKW" and "5:00" in windows[0]["label"] and "KEKW x40" in windows[0]["label"]
    assert tc.score_activity([], 10)["buckets"] == []


def test_chat_hype_windows_and_prompt_block(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    source = _source(store, duration=600)
    rows = [{"msg_id": f"a{t}", "offset_sec": 3600 + t, "text": "hi"} for t in range(0, 600, 5)]
    rows += [{"msg_id": f"b{i}", "offset_sec": 3700 + i * 0.2, "text": "OMEGALUL"} for i in range(40)]
    tc._upsert_status(store, source["id"], status="succeeded")
    tc.insert_messages(store, source["id"], rows, shift_sec=3600)  # 3700 VOD → 100 исходника
    windows = tc.chat_hype_windows(store, source["id"], top=3)
    assert windows[0]["peak_t"] == 100 and windows[0]["top"] == "OMEGALUL"
    block = tc.chat_prompt_block(store, source["id"])
    assert block.startswith("Чат зрителей") and "1:40" in block and "OMEGALUL x40" in block
    store.set_app_setting("analysis_use_chat", False)
    assert tc.chat_prompt_block(store, source["id"]) == ""
    other = store.create_source("upload", settings.source_dir / "vod.mp4", status="ready")
    assert tc.chat_hype_windows(store, other["id"]) == [] and tc.chat_prompt_block(store, other["id"]) == ""


def test_analysis_prompt_gets_chat_block(tmp_path, monkeypatch):
    from app.ai.service import VideoAnalysisService

    store = _store(tmp_path, monkeypatch)
    source = _source(store, duration=600)
    tc.insert_messages(store, source["id"], [{"msg_id": f"k{i}", "offset_sec": 60 + i * 0.1, "text": "KEKW"} for i in range(30)])
    seen = {}

    class Analyzer:
        def analyze(self, source, prompt, model, **kwargs):
            seen["prompt"] = prompt
            raise RuntimeError("стоп")

    monkeypatch.setattr("app.ai.service.get_video_analyzer", lambda provider, store: Analyzer())
    VideoAnalysisService(store).run_analysis(source["id"], provider="mock")
    assert "Чат зрителей" in seen["prompt"] and "KEKW x30" in seen["prompt"]


# ------------------------------------------------------------------- клип


def test_clip_messages_map_to_clip_time(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    source = _source(store, duration=600)
    tc._upsert_status(store, source["id"], status="succeeded")
    store.db.execute(
        "UPDATE source_chat SET emotes_json = ?, badges_json = ? WHERE source_id = ?",
        ('{"KEKW": "https://k"}', '{"subscriber/12": "https://b"}', source["id"]),
    )
    rows = [
        tc.parse_comment(_node(1, 15, "в первом куске KEKW", badges=[{"setID": "subscriber", "version": "12"}])),
        tc.parse_comment(_node(2, 30, "между кусками")),
        tc.parse_comment(_node(3, 52, "во втором", color=None, user="Zed")),
        tc.parse_comment(_node(4, 9.9, "до клипа")),
    ]
    tc.insert_messages(store, source["id"], rows)
    analysis = store.create_ai_analysis(source["id"], "mock", status="succeeded")
    a = store.create_ai_segment(analysis["id"], {"start_sec": 10, "end_sec": 20, "title": "A"})
    b = store.create_ai_segment(analysis["id"], {"start_sec": 50, "end_sec": 55, "title": "B"})
    plan = store.create_clip_plan(source["id"], analysis["id"], "Клип", segment_ids=[a["id"], b["id"]])

    msgs = tc.clip_messages(store, store.get_clip_plan(plan["id"]))
    assert [m["t"] for m in msgs] == [5.0, 12.0]
    first, second = msgs
    assert first == {
        "t": 5.0,
        "user": "U1",
        "color": "#ff0000",
        "text": "в первом куске KEKW",
        "fragments": [{"text": "в первом куске "}, {"emote": "KEKW", "url": "https://k"}],
        "badges": [{"id": "subscriber/12", "url": "https://b"}],
    }
    assert second["user"] == "Zed" and second["color"] == tc.default_color("zed") and second["badges"] == []


# ------------------------------------------------------------------- ручки


def test_chat_endpoints(tmp_path, monkeypatch):
    from test_api_pipeline import _client

    client, store = _client(tmp_path, monkeypatch)
    source = _source(store, duration=600)
    nodes = [_node(i, 100 + i, "KEKW" if i % 2 else "привет") for i in range(30)]
    nodes += [_node(500 + i, 300 + i * 0.1, "OMEGALUL") for i in range(30)]

    import app.main as app_main

    router = next(
        r for r in app_main.app.router.routes if getattr(r, "path", "") == "/api/sources/{source_id}/chat/import"
    )
    assert router  # ручка зарегистрирована до SPA-ловушки
    from app.chat_api import build_chat_router

    fake = FakeClient(nodes, length=600, page_size=50)
    chat_router = build_chat_router(app_main.AuthDep, lambda: store, client_factory=lambda: fake)
    # Синхронно, чтобы не ждать поток: тот же сервис, что за ручкой.
    status = chat_router.chat_service.start(source["id"], wait=True)
    assert status["status"] == "succeeded" and status["message_count"] == 60

    res = client.get(f"/api/sources/{source['id']}/chat", params={"from": 100, "to": 110, "limit": 5}).json()
    assert res["chat"]["status"] == "succeeded" and len(res["messages"]) == 5
    assert res["messages"][1]["fragments"][0] == {"emote": "KEKW", "url": "https://cdn.7tv.app/emote/kekw/2x.webp"}
    assert client.get(f"/api/sources/{source['id']}/chat/status").json()["message_count"] == 60

    act = client.get(f"/api/sources/{source['id']}/chat/activity", params={"bucket": 10, "top": 3}).json()
    assert act["bucket"] == 10 and act["total"] == 60 and act["windows"][0]["peak_t"] == 300
    assert act["windows"][0]["top"] == "OMEGALUL"

    analysis = store.create_ai_analysis(source["id"], "mock", status="succeeded")
    seg = store.create_ai_segment(analysis["id"], {"start_sec": 295, "end_sec": 305, "title": "A"})
    plan = store.create_clip_plan(source["id"], analysis["id"], "Клип", segment_ids=[seg["id"]])
    clip_chat = client.get(f"/api/clip-plans/{plan['id']}/chat").json()
    assert len(clip_chat) == 30 and clip_chat[0]["t"] == 5.0
    assert set(clip_chat[0]) == {"t", "user", "color", "text", "fragments", "badges"}
    assert client.get("/api/clip-plans/9999/chat").status_code == 404

    upload = store.create_source("upload", settings.source_dir / "vod.mp4", status="ready")
    assert client.post(f"/api/sources/{upload['id']}/chat/import").status_code == 400
    assert client.get("/api/sources/9999/chat").status_code == 404
    assert client.delete(f"/api/sources/{source['id']}/chat").json() == {"deleted": True}
    assert client.get(f"/api/sources/{source['id']}/chat/status").json()["status"] == "none"


def test_import_endpoint_runs_in_background_with_explicit_vod(tmp_path, monkeypatch):
    from test_api_pipeline import _client

    client, store = _client(tmp_path, monkeypatch)
    upload = store.create_source("upload", _source(store)["local_path"], status="ready", duration_sec=600)
    fake = FakeClient([_node(i, i * 3, "hi") for i in range(20)], length=600)
    monkeypatch.setattr(tc, "TwitchChatClient", lambda: fake)
    res = client.post(
        f"/api/sources/{upload['id']}/chat/import",
        json={"vod": "https://www.twitch.tv/videos/555555555"},
    )
    assert res.status_code == 200 and res.json()["status"] in {"queued", "running", "succeeded"}
    for _ in range(200):
        if tc.get_chat_status(store, upload["id"])["status"] == "succeeded":
            break
        threading.Event().wait(0.02)
    status = tc.get_chat_status(store, upload["id"])
    assert status["status"] == "succeeded" and status["vod_id"] == "555555555" and status["message_count"] == 20


def test_auto_import_hook_on_twitch_ingest(tmp_path, monkeypatch):
    from app.ingest import SourceIngestor, StoredSourceFile

    store = _store(tmp_path, monkeypatch)
    started: list[int] = []

    def fake_twitch(url):
        path = settings.source_dir / "twitch.mp4"
        path.write_bytes(b"twitch")
        return StoredSourceFile(path, "twitch.mp4", "sha", 6)

    class Meta:
        duration_sec, width, height, fps, raw = 60.0, 1280, 720, 30.0, {}

    ingestor = SourceIngestor(store, probe=lambda p: Meta(), twitch_downloader=fake_twitch)
    service = tc.ChatImportService(lambda: store)
    monkeypatch.setattr(service, "start", lambda source_id, **kw: started.append(source_id))
    ingestor.on_source_created = service.auto_import

    monkeypatch.setenv("TWITCH_CHAT_AUTO_IMPORT", "0")
    ingestor.ingest_url("https://www.twitch.tv/videos/123456789")
    assert started == []

    monkeypatch.setenv("TWITCH_CHAT_AUTO_IMPORT", "1")
    source = ingestor.ingest_url("https://www.twitch.tv/videos/123456789")
    assert started == [source["id"]]
    store.set_app_setting("chat_auto_import", False)
    ingestor.ingest_url("https://www.twitch.tv/videos/123456789")
    assert started == [source["id"]]

    # Сломанный хук не ломает загрузку.
    ingestor.on_source_created = lambda s: 1 / 0
    assert ingestor.ingest_url("https://www.twitch.tv/videos/123456789")["status"] == "ready"
