"""Форматы вывода, этап 2C: ИИ-монтажёр, анализ, фокус и публикация знают формат клипа.

9:16 (клип без поля format) — всё слово в слово как раньше: промпт агента сверяется со снимком
tests/fixtures/editor_prompt_9x16.txt, снятым до форматов.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import agent_tools
from app.agent_tools import ToolContext, run_tool
from app.ai import editor_agent
from app.crypto import CookieCipher
from app.db import Database
from app.formats import focus_needed
from app.settings import settings
from app.store import AppStore

SNAPSHOT = Path(__file__).parent / "fixtures" / "editor_prompt_9x16.txt"


def _store(tmp_path, monkeypatch) -> AppStore:
    data = tmp_path / "data"
    for name, sub in (("data_dir", ""), ("upload_dir", "uploads"), ("source_dir", "sources"), ("clip_dir", "clips"),
                      ("runtime_dir", "runtime"), ("tmp_dir", "tmp")):
        monkeypatch.setattr(settings, name, data / sub if sub else data)
    settings.ensure_dirs()
    store = AppStore(Database(data / "app.sqlite"), CookieCipher(data / "secret.key"))
    store.db.init()
    return store


def _plan(store: AppStore, tmp_path, width=1920, height=1080, render: dict | None = None) -> dict:
    video = settings.source_dir / f"src-{width}x{height}-{len(list(settings.source_dir.iterdir()))}.mp4"
    video.write_bytes(b"fake-video")
    source = store.create_source("upload", video, original_filename="src.mp4", duration_sec=30,
                                 width=width, height=height, fps=30, status="ready")
    analysis = store.create_ai_analysis(source["id"], "mock", status="succeeded")
    seg = store.create_ai_segment(analysis["id"], {"start_sec": 0, "end_sec": 10, "title": "A"})
    plan = store.create_clip_plan(source["id"], analysis["id"], "План", segment_ids=[seg["id"]])
    if render is not None:
        store.set_clip_plan_render_settings(plan["id"], render)
    return store.get_clip_plan(plan["id"])


# --------------------------------------------------------------------------- ИИ-монтажёр


def test_prompt_without_format_is_the_old_text_snapshot():
    craft, examples = SNAPSHOT.read_text(encoding="utf-8").split("\n<<<СПРАВКА ПО СЛОЯМ>>>\n")
    for prompt in (editor_agent.system_prompt(), editor_agent.system_prompt("9:16", "9:16"),
                   editor_agent.system_prompt(None, None, has_layers=False)):
        head, rest = prompt.split("\nСПРАВКА ПО СЛОЯМ\n", 1)
        assert head == craft
        assert rest.startswith("КАДР 1080×1920 (опорные px). x,y — центр слоя")
        assert rest.endswith("\n\n" + examples)
        assert "ФОРМАТ КАДРА" not in prompt and "1920×1080" not in prompt
    assert agent_tools.motion_reference() == agent_tools.motion_reference("9:16", "9:16")


def test_prompt_for_16x9_has_landscape_frame_and_horizontal_rules():
    prompt = editor_agent.system_prompt("16:9", "16:9")
    assert "кадр 1920×1080" in prompt and "КАДР 1920×1080 (формат 16:9, опорные px)" in prompt
    assert "опорные пиксели кадра разметки слоёв 1920×1080 (16:9)" in prompt
    assert "1080×1920" not in prompt
    assert "ГОРИЗОНТАЛЬ 16:9 — композиция по ширине" in prompt
    assert "левой или правой трети" in prompt and "телефон (промо) — сбоку" in prompt
    assert "«вебки сверху» нет" in prompt
    assert "не шортс" in prompt and "вертикальных шортсов" not in prompt
    assert "15–60 с" not in prompt  # длина — по истории, не шортсу
    assert "set_format без его просьбы не вызывай" in prompt


def test_prompt_knows_layers_are_reframed_from_another_format():
    prompt = editor_agent.system_prompt("16:9", "9:16")
    assert "Слои размечены в 9:16 (опорный кадр 1080×1920" in prompt
    assert "перекомпонуются в 16:9" in prompt and "НЕ переделывай" in prompt
    assert "layers_format=\"16:9\"" in prompt  # настоящая раскладка — по просьбе владельца
    # Слоёв нет — сразу размечать под формат клипа.
    fresh = editor_agent.system_prompt("1:1", "9:16", has_layers=False)
    assert "set_format(\"1:1\", layers_format=\"1:1\")" in fresh and "НЕ переделывай" not in fresh
    assert "КВАДРАТ 1:1 — кадр ниже вертикали" in fresh


def test_compact_rules_for_square_and_4x5():
    square = editor_agent.system_prompt("1:1", "1:1")
    assert "кадр 1080×1080" in square and "КВАДРАТ 1:1" in square and "Телефон (промо) короче" in square
    four = editor_agent.system_prompt("4:5", "4:5")
    assert "кадр 1080×1350" in four and "4:5 — чуть ниже вертикали" in four
    assert "15–60 с" in four  # короткие ролики ленты — длина прежняя


def test_set_format_tool_says_owner_chooses():
    decl = next(d for d in editor_agent.function_declarations() if d["name"] == "set_format")
    assert "Формат выбирает владелец" in decl["description"] and "не меняй без его просьбы" in decl["description"]


def test_agent_prompt_follows_clip_format(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    plan = _plan(store, tmp_path, render={"format": "16:9", "layers_format": "16:9"})
    assert editor_agent.clip_formats(store, plan["id"]) == ("16:9", "16:9", False)
    old = _plan(store, tmp_path)
    assert editor_agent.clip_formats(store, old["id"]) == ("9:16", "9:16", False)

    ctx = ToolContext(store=store, clip_plan_id=plan["id"])
    out, ok = run_tool(ctx, "get_motion_reference", {})
    assert ok and out["reference"].startswith("КАДР 1920×1080 (формат 16:9, опорные px)")
    out, ok = run_tool(ToolContext(store=store), "get_motion_reference", {})
    assert ok and out["reference"].startswith("КАДР 1080×1920 (опорные px)")


def test_mcp_instructions_take_frame_from_clip_format():
    from app.agent_api import MCP_INSTRUCTIONS

    assert "render.frame" in MCP_INSTRUCTIONS and "16:9 — 1920×1080" in MCP_INSTRUCTIONS
    assert "кадр 1080×1920, x/y" not in MCP_INSTRUCTIONS


# --------------------------------------------------------------------------- фокус и анализ


@pytest.mark.parametrize("fmt, size, needed", [
    ("16:9", (1920, 1080), False),   # горизонталь в горизонталь — без кропа
    ("16:9", (1280, 720), False),
    ("16:9", (1080, 1920), False),   # вертикаль — целиком на подложке
    ("16:9", (1440, 1080), False),   # 4:3 — режется по высоте, окну по ширине ехать некуда
    ("16:9", (2560, 1080), True),    # 21:9 — окно 16:9 ведётся фокусом
    ("16:9", (0, 0), True),          # размер неизвестен — как раньше
    ("9:16", (1920, 1080), True),
    ("9:16", (1080, 1920), True),    # 9:16 — прежнее поведение, без исключений
    ("1:1", (1920, 1080), True),
    ("4:5", (1920, 1080), True),
])
def test_focus_needed(fmt, size, needed):
    assert focus_needed(fmt, {"width": size[0], "height": size[1]}) is needed


def test_auto_focus_skips_16x9_from_horizontal_source(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    plan = _plan(store, tmp_path, render={"format": "16:9"})
    out, ok = run_tool(ToolContext(store=store, clip_plan_id=plan["id"]), "auto_focus", {})
    assert ok, out
    assert out["focus_needed"] is False and out["format"] == "16:9" and out["pieces"] == []
    # Ничего не записано: кусок без трека фокуса.
    assert not store.get_clip_plan(plan["id"])["segments"][0].get("focus")


def test_vlm_and_subject_prompts_name_the_format_window():
    from app.ai.subject_box import PROMPT, prompt_for_format
    from app.ai.vlm_focus import _PROMPT, prompt_for_format as vlm_prompt

    assert vlm_prompt("9:16") == _PROMPT
    assert "a square 1:1 frame" in vlm_prompt("1:1") and "9:16" not in vlm_prompt("1:1")
    assert prompt_for_format("9:16", 4) == PROMPT.format(n=4)
    assert "горизонтального видео 16:9" in prompt_for_format("16:9", 4)


def test_analysis_prompt_blocks_by_format():
    from app.default_prompts import FOCUS_TRACK_INSTRUCTION, focus_track_instruction, output_format_instruction

    assert output_format_instruction("9:16") == "" and focus_track_instruction("9:16") == FOCUS_TRACK_INSTRUCTION
    wide = output_format_instruction("16:9", 600)
    assert "NOT vertical Shorts" in wide and "600 seconds" in wide and "10 min" in wide
    assert "180 seconds" in output_format_instruction("16:9")  # по умолчанию — прежний потолок
    assert "square 1:1 feed post" in output_format_instruction("1:1")
    assert "a 4:5 portrait frame" in focus_track_instruction("4:5")


class _Analyzer:
    def __init__(self):
        self.prompts: list[str] = []

    def analyze(self, source, prompt, model, **kwargs):
        from app.ai.contracts import AnalysisResult, AnalysisSegment

        self.prompts.append(prompt)
        return AnalysisResult(segments=[AnalysisSegment(start_sec=0, end_sec=12, title="t", description="d",
                                                        score=0.5, category="c", color="#2563EB", reason="r")],
                              response={}, usage={})


def _run_analysis(store, source_id, monkeypatch, **kwargs):
    from app.ai import service

    analyzer = _Analyzer()
    monkeypatch.setattr(service, "get_video_analyzer", lambda provider, store: analyzer)
    monkeypatch.setattr(service, "prepare_source_for_analysis", lambda source, pre: (source, {}))
    monkeypatch.setattr(service, "_detect_audio_boundaries", lambda path, duration: [])
    analysis = service.VideoAnalysisService(store).run_analysis(source_id, provider="polza", prompt="Find moments.",
                                                                **kwargs)
    return analysis, analyzer.prompts[0]


def test_analysis_16x9_is_not_shorts_and_skips_focus_for_horizontal_source(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    plan = _plan(store, tmp_path)
    analysis, prompt = _run_analysis(store, plan["source_id"], monkeypatch)
    assert prompt.startswith("Find moments.\n\nREFRAMING FOCUS — for cropping a wide video to vertical 9:16")
    assert "OUTPUT FORMAT" not in prompt and "format" not in json.loads(analysis["request_json"])

    store.set_app_setting("landscape_max_clip_sec", "420")
    analysis, prompt = _run_analysis(store, plan["source_id"], monkeypatch, output_format="16:9")
    assert "OUTPUT FORMAT" in prompt and "up to 420 seconds" in prompt
    assert "REFRAMING FOCUS" not in prompt  # 16:9 из горизонтального исходника — кадр не режется
    assert analysis["status"] == "succeeded" and json.loads(analysis["request_json"])["format"] == "16:9"

    store.set_app_setting("analysis_format", "1:1")  # настройка приложения — формат по умолчанию
    _analysis, prompt = _run_analysis(store, plan["source_id"], monkeypatch)
    assert "square 1:1 feed post" in prompt and "cropping a wide video to a square 1:1 frame" in prompt


def test_pick_and_montage_rules_for_16x9():
    from app.ai import montage, pick

    assert pick.rules_for_format("9:16") == pick.RULES and pick.rules_for_format("1:1") == pick.RULES
    wide = pick.rules_for_format("16:9", 600)
    assert "ГОРИЗОНТАЛЬНЫМ видео 16:9" in wide and "до 10 мин" in wide and "15–60 с идеально" not in wide
    wide.format(n=3, goal="")  # фигурные скобки не сломали шаблон
    assert montage.rules_for_format("9:16") == montage.RULES
    cut = montage.rules_for_format("16:9")
    assert "ГОРИЗОНТАЛЬНЫМ видео 16:9" in cut and "175 с" not in cut and "НИКОГДА короче 30 с" in cut


# --------------------------------------------------------------------------- публикация


def _png(path: Path, width: int, height: int) -> Path:
    import struct
    import zlib

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    raw = chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IEND", b"")
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + raw)
    return path


def _rendered_clip(store, tmp_path, width, height, *, cover: tuple[int, int] | None = None) -> dict:
    plan = _plan(store, tmp_path)
    clip = store.create_clip(plan["source_id"], clip_plan_id=plan["id"], title="Клип")
    out = settings.clip_dir / f"clip-{clip['id']}.mp4"
    out.write_bytes(b"fake-clip")
    clip = store.finish_clip_render(clip["id"], "succeeded", output_path=out, preview_path=out, duration_sec=240,
                                    width=width, height=height, size_bytes=9)
    if cover:
        store.set_clip_cover(clip["id"], _png(tmp_path / f"cover-{clip['id']}.png", *cover))
    return clip


class _Recorder:
    def __init__(self, platform):
        self.platform = platform
        self.calls: list[dict] = []

    def upload(self, **kwargs):
        from app.providers.base import ProviderResult

        self.calls.append(kwargs)
        return ProviderResult(status="succeeded", remote_id="x")


def test_worker_publishes_16x9_on_youtube_as_regular_video_with_cover(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    account = store.upsert_account("youtube", "yt", "SID=a; HSID=b; SSID=c; APISID=d; SAPISID=e")
    wide = _rendered_clip(store, tmp_path, 1920, 1080, cover=(1280, 720))
    vertical = _rendered_clip(store, tmp_path, 1080, 1920, cover=(1080, 1920))
    recorder = _Recorder("youtube")
    monkeypatch.setattr("app.worker.get_provider", lambda platform: recorder)
    from app.worker import JobWorker

    store.create_clip_post_job(wide["id"], "Горизонталь #shorts", "Описание\n#стрим #shorts", [account["id"]], "public", True)
    assert JobWorker(store).process_once()
    call = recorder.calls[-1]
    assert call["video_format"] == "16:9"
    assert call["thumbnail_path"].endswith(f"cover-{wide['id']}.png")

    store.create_clip_post_job(vertical["id"], "Вертикаль", "", [account["id"]], "public", True)
    assert JobWorker(store).process_once()
    assert "video_format" not in recorder.calls[-1] and "thumbnail_path" not in recorder.calls[-1]  # как раньше


def test_youtube_provider_drops_shorts_tag_and_sends_thumbnail_for_16x9(tmp_path, monkeypatch):
    from app.providers import youtube

    sent: list[dict] = []

    class Proc:
        returncode = 0
        stdout = json.dumps({"ok": True, "videoId": "abc", "thumbnailSet": True})
        stderr = ""

    def fake_run(cmd, input, **kwargs):  # noqa: A002 - как у subprocess.run
        sent.append(json.loads(input))
        return Proc()

    monkeypatch.setattr(youtube.subprocess, "run", fake_run)
    common = dict(cookies=[], file_path=tmp_path / "v.mp4", privacy="public", allow_comments=True,
                  account_label="yt", proxy_url="")
    result = youtube.YouTubeProvider().upload(title="Момент #shorts", description="Смотри #Shorts #стрим",
                                              video_format="16:9", thumbnail_path=str(tmp_path / "c.png"), **common)
    assert result.status == "succeeded" and result.response["publish_as"] == "video"
    assert sent[-1]["title"] == "Момент" and sent[-1]["description"] == "Смотри #стрим"
    assert sent[-1]["thumbnailPath"].endswith("c.png")

    youtube.YouTubeProvider().upload(title="Момент #shorts", description="d #shorts", **common)
    assert sent[-1]["title"] == "Момент #shorts" and "thumbnailPath" not in sent[-1]  # 9:16 — как было


def test_tiktok_and_instagram_warn_or_go_to_feed():
    from app.publish_format import publish_plan

    assert publish_plan("youtube", "16:9") == {"platform": "youtube", "format": "16:9", "publish_as": "video",
                                               "warning": "", "error": ""}
    assert publish_plan("youtube", "9:16")["publish_as"] == "shorts"
    assert publish_plan("youtube", None)["publish_as"] == "shorts"
    tiktok = publish_plan("tiktok", "16:9")
    assert tiktok["warning"].startswith("TikTok ждёт вертикаль") and not tiktok["error"]
    assert publish_plan("tiktok", "9:16")["warning"] == ""
    insta = publish_plan("instagram", "16:9")
    assert insta["publish_as"] == "feed" and "Reels — только вертикаль" in insta["warning"]
    assert publish_plan("instagram", "1:1")["publish_as"] == "feed" and publish_plan("instagram", "4:5")["publish_as"] == "feed"
    assert publish_plan("instagram", "9:16") == {"platform": "instagram", "format": "9:16", "publish_as": "reel",
                                                 "warning": "", "error": ""}


def test_instagram_square_goes_to_feed(monkeypatch, tmp_path):
    import sys
    import types

    from app.cookies import CookieRecord
    from app.providers.instagram import InstagramProvider

    used: list[str] = []

    class Client:
        def login_by_sessionid(self, sid):
            pass

        def clip_upload(self, path, caption):
            used.append("reel")
            return types.SimpleNamespace(code="R1", pk=1)

        def video_upload(self, path, caption):
            used.append("feed")
            return types.SimpleNamespace(code="P1", pk=2)

    monkeypatch.setitem(sys.modules, "instagrapi", types.SimpleNamespace(Client=Client))
    cookies = [CookieRecord(name="sessionid", value="s", domain=".instagram.com")]
    common = dict(cookies=cookies, file_path=tmp_path / "v.mp4", title="t", description="d", privacy="public",
                  allow_comments=True, account_label="ig", proxy_url="")
    square = InstagramProvider().upload(video_format="1:1", **common)
    assert used[-1] == "feed" and square.remote_url == "https://www.instagram.com/p/P1/"
    reel = InstagramProvider().upload(**common)
    assert used[-1] == "reel" and reel.remote_url == "https://www.instagram.com/reel/R1/" and reel.response == {"code": "R1", "pk": "1"}


def test_publish_check_and_mock_provider(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "provider_mode", "mock")
    yt = store.upsert_account("youtube", "yt", "SID=a; HSID=b; SSID=c; APISID=d; SAPISID=e")
    tt = store.upsert_account("tiktok", "tt", "sessionid=abc; tt-target-idc=useast2a")
    clip = _rendered_clip(store, tmp_path, 1280, 720)  # 720p — тоже 16:9
    from app.publish_format import check_targets

    check = check_targets(store, clip["id"], [yt["id"], tt["id"]])
    assert check["format"] == "16:9"
    assert [t["publish_as"] for t in check["targets"]] == ["video", "tiktok"]
    assert check["warnings"] and check["warnings"][0].startswith("TikTok ждёт вертикаль")

    job = store.create_clip_post_job(clip["id"], "Ролик #shorts", "", [yt["id"], tt["id"]], "public", True)
    from app.worker import JobWorker

    assert JobWorker(store).process_once()
    targets = {t["platform"]: json.loads(t["response_json"]) for t in store.get_job(job["id"])["targets"]}
    assert targets["youtube"]["publish_as"] == "video" and targets["youtube"]["title"] == "Ролик"
    assert targets["tiktok"]["format_warning"].startswith("TikTok ждёт вертикаль")


def test_publish_check_endpoint_and_batch_warnings(tmp_path, monkeypatch):
    from test_api_pipeline import _client

    client, store = _client(tmp_path, monkeypatch)
    tt = store.upsert_account("tiktok", "tt", "sessionid=abc; tt-target-idc=useast2a")
    yt = store.upsert_account("youtube", "yt", "SID=a; HSID=b; SSID=c; APISID=d; SAPISID=e")
    wide = _rendered_clip(store, tmp_path, 1920, 1080)
    vertical = _rendered_clip(store, tmp_path, 1080, 1920)

    check = client.get(f"/api/clips/{wide['id']}/publish-check", params={"targets": f"{yt['id']},{tt['id']}"}).json()
    assert check["format"] == "16:9" and [t["publish_as"] for t in check["targets"]] == ["video", "tiktok"]
    assert client.get("/api/clips/999999/publish-check").status_code == 404

    out = client.post("/api/clips/posts-batch", json={"clip_ids": [wide["id"], vertical["id"]],
                                                      "targets": [tt["id"]]}).raise_for_status().json()
    assert len(out["jobs"]) == 2
    assert [w["clip_id"] for w in out["format_warnings"]] == [wide["id"]]


def test_publish_meta_for_16x9_is_a_regular_youtube_video():
    from app.ai.publish_meta import BASE_RULES, generate_publish_metadata, rules_for_format

    assert rules_for_format("9:16") == BASE_RULES
    wide = rules_for_format("16:9")
    assert "ГОРИЗОНТАЛЬНОГО видео 16:9" in wide and "НЕ ставь #shorts" in wide and "(#shorts," not in wide
    assert "ленту Instagram" in rules_for_format("4:5")

    class Store:
        clip = {"id": 1, "source_id": 1, "clip_plan_id": None, "segment_id": None, "title": "К", "description": "",
                "duration_sec": 200, "width": 1920, "height": 1080}

        def get_clip(self, clip_id):
            return self.clip

        def get_default_prompt_preset(self, task):
            return None

    class Client:
        def __init__(self):
            self.prompt = ""

        def generate_content(self, model, payload):
            self.prompt = payload["contents"][0]["parts"][0]["text"]
            reply = {"title": "Стример спалился #shorts", "description": "Что было.", "hashtags": ["#стрим", "#shorts", "reels"]}
            return {"candidates": [{"content": {"parts": [{"text": json.dumps(reply, ensure_ascii=False)}]}}]}

    client = Client()
    meta = generate_publish_metadata(Store(), 1, client=client, model="m")
    assert meta["format"] == "16:9" and meta["hashtags"] == ["#стрим"] and meta["title"] == "Стример спалился"
    assert "ГОРИЗОНТАЛЬНОГО видео 16:9" in client.prompt and '"format": "16:9"' in client.prompt


def test_video_format_from_size_and_image_size(tmp_path):
    from app.publish_format import image_size, strip_shorts_tags, video_format

    assert video_format(1920, 1080) == "16:9" and video_format(1280, 720) == "16:9"
    assert video_format(720, 1280) == "9:16" and video_format(1080, 1352) == "4:5"
    assert video_format(0, 0) is None and video_format(1000, 300) is None
    assert image_size(_png(tmp_path / "c.png", 1280, 720)) == (1280, 720)
    jpeg = tmp_path / "c.jpg"
    jpeg.write_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
                     b"\xff\xc0\x00\x11\x08\x02\xd0\x05\x00\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01\xff\xd9")
    assert image_size(jpeg) == (1280, 720)
    assert strip_shorts_tags("Смотри #shorts #shortstory #Short до конца") == "Смотри #shortstory до конца"


def test_render_qc_long_landscape_is_not_a_shorts_problem():
    from app.render_qc import parse_qc

    codes = lambda qc: {i["code"] for i in qc["issues"]}  # noqa: E731
    assert "long" in codes(parse_qc("", duration_sec=240, width=1080, height=1920))
    assert "long" not in codes(parse_qc("", duration_sec=240, width=1920, height=1080))
