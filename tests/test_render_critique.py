"""Независимый критик готового ролика (app/render_critique.py, инструмент critique_render): модель подменена.

Проверяем, что критик видит только ролик, задачу и слова владельца (не план монтажа и не заметку версии), что ответ
сводится к «сдавать / ещё проход» и пунктам P0/P1/P2, что второй круг сверяет прошлые пункты, а траты пишутся в
общий учёт Gemini."""

from __future__ import annotations

import base64
import json

import pytest

from app import feed as FEED
from app import render_critique as C
from app.settings import settings
from test_api_pipeline import _client, _require_ffmpeg
from test_feed import _video


class FakeGemini:
    """generate_content как у GeminiClient (учёт трат — тот же gemini_usage.tracked), ответ — по очереди."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.payloads: list[dict] = []

    def generate_content(self, model, payload):
        from app import gemini_usage

        self.payloads.append(payload)
        answer = self.answers.pop(0)

        def call():
            return {"candidates": [{"content": {"parts": [{"text": json.dumps(answer, ensure_ascii=False)}]}}],
                    "usageMetadata": {"promptTokenCount": 6000, "candidatesTokenCount": 400, "totalTokenCount": 6400}}

        return gemini_usage.tracked(model, call)

    def upload_file(self, *a, **k):  # pragma: no cover - маленькая копия идёт в теле запроса
        raise AssertionError("копия должна уйти inline")


FIRST = {
    "verdict": "сдавать",  # модель «за», но есть P0 — сервис поправит вердикт
    "summary": "Хук есть, но субтитры под кнопками.",
    "issues": [
        {"priority": "P1", "t": "8.0–9.5", "where": "звук", "problem": "музыка громче голоса",
         "change": "музыку тише на 6 дБ", "keep": "сам трек"},
        {"priority": "P0", "t": "2.0", "where": "низ кадра", "problem": "субтитры под подписью TikTok",
         "change": "поднять субтитры на 15 %", "keep": "шрифт и цвет"},
        {"priority": "P2", "t": "0", "where": "", "problem": "", "change": ""},  # пустое — выкинется
    ],
    "keep": ["первый кадр"],
}
SECOND = {
    "verdict": "сдавать", "summary": "Поправлено.",
    "previous": [{"id": "1", "status": "исправлено", "note": "субтитры выше"},
                 {"id": "2", "status": "частично", "note": "музыка тише, но на 9 с всё ещё громко"},
                 {"id": "99", "status": "осталось"}],
    "issues": [{"priority": "P2", "t": "11.5", "where": "конец", "problem": "концовка резковата",
                "change": "затухание на 0.5 с длиннее", "keep": "последний кадр"}],
}


@pytest.fixture
def env(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, store = _client(tmp_path, monkeypatch)
    return client, store


def _work(store, project_id=None):
    path = _video(settings.data_dir / "runtime" / "deliv" / "promo.mp4", size="270x480", seconds=3.0)
    work = FEED.create_work(store, "Промо приложения", project_id=project_id, kind="promo", made_by="agent")
    FEED.add_version(store, work["id"], note="ПЛАН МОНТАЖА: поставил три мема и наезд — СЕКРЕТ",
                     variants=[{"path": str(path), "label": "9:16"}], made_by="agent")
    version = FEED.get_work(store, work["id"])["versions"][0]
    return work, version, path


def test_critic_sees_only_video_brief_and_owner_words(env):
    from app.projects_core import create_project
    from app.projects_rules import add_rule

    _client_, store = env
    project = create_project(store, "Промо Витубики")
    add_rule(store, "Музыка всегда тише голоса", kind="do", project_id=project["id"])
    work, version, _path = _work(store, project["id"])
    old = FEED.add_version(store, work["id"], note="старое", variants=[{"path": str(_path)}])
    FEED.set_status(store, old["id"], "rejected", "голос не слышно за музыкой", by="owner")
    fake = FakeGemini([FIRST])
    target = C.resolve_target(store, feed_version_id=version["id"])
    out = C.critique(store, target, brief="Промо: зритель за 3 с понимает, что это за приложение", client=fake)

    payload = fake.payloads[0]
    parts = payload["contents"][0]["parts"]
    video, text = parts[0], parts[1]["text"]
    assert video["inline_data"]["mime_type"] == "video/mp4" and base64.b64decode(video["inline_data"]["data"])
    assert payload["generationConfig"]["mediaResolution"] == "MEDIA_RESOLUTION_LOW"
    assert "maxOutputTokens" not in payload["generationConfig"]
    assert "Промо: зритель за 3 с" in text
    assert "Музыка всегда тише голоса" in text  # правило проекта
    assert "голос не слышно за музыкой" in text  # вердикт владельца прошлой версии
    assert "ПРАВИЛА ВЛАДЕЛЬЦА" in text  # сквозные правила
    assert "СЕКРЕТ" not in text and "ПЛАН МОНТАЖА" not in text  # заметка версии — ход монтажа, критику нельзя
    assert "loudness_lufs" in text  # замеры файла

    report = out["report"]
    assert report["verdict"] == "ещё проход" and report["verdict_note"]  # P0 — сдавать нельзя
    assert [x["priority"] for x in report["issues"]] == ["P0", "P1"]  # пустое выброшено, P0 первым
    assert report["issues"][0]["id"] == "1" and report["issues"][0]["keep"] == "шрифт и цвет"
    assert out["lines"][1].startswith("P0 · t=2.0 · низ кадра · субтитры под подписью TikTok → поднять")
    assert out["saw"]["owner_verdicts"] >= 1 and out["saw"]["previous_items"] == 0
    # Траты — в общем учёте Gemini, с назначением и проектом.
    assert out["cost"]["calls"] == 1 and out["cost"]["tokens"] == 6400
    row = store.db.query_one("SELECT purpose, project_id, session FROM gemini_usage ORDER BY id DESC LIMIT 1")
    assert row["purpose"] == "render_critique" and row["project_id"] == project["id"]
    assert row["session"].startswith("critique:")
    assert not list(settings.tmp_dir.glob("critique-*.mp4"))  # копия удалена


def test_second_round_checks_previous_items(env):
    _client_, store = env
    work, version, path = _work(store)
    fake = FakeGemini([FIRST, SECOND])
    C.critique(store, C.resolve_target(store, feed_version_id=version["id"]), client=fake)
    # Новая версия с другим файлом — второй круг сам берёт прошлый отчёт этой работы.
    path2 = _video(settings.data_dir / "runtime" / "deliv" / "promo2.mp4", size="270x480", seconds=3.0)
    v2 = FEED.add_version(store, work["id"], note="поправил", variants=[{"path": str(path2)}])
    out = C.critique(store, C.resolve_target(store, feed_version_id=v2["id"]), client=fake)
    text = fake.payloads[1]["contents"][0]["parts"][1]["text"]
    assert "ПРОШЛЫЙ КРУГ" in text and "id 1: [P0] t=2.0" in text and "id 2: [P1]" in text
    report = out["report"]
    prev = {p["id"]: p for p in report["previous"]}
    assert prev["1"]["status"] == "исправлено" and prev["2"]["status"] == "частично"
    assert "99" not in prev  # чужой id не выдумываем
    assert out["round"] == 2 and report["verdict"] == "сдавать"
    assert any(line.startswith("Прошлое 2") and "частично" in line for line in out["lines"])
    # fresh — без прошлого круга.
    fake.answers.append(FIRST)
    out = C.critique(store, C.resolve_target(store, feed_version_id=v2["id"]), fresh=True, client=fake)
    assert "ПРОШЛЫЙ КРУГ" not in fake.payloads[2]["contents"][0]["parts"][1]["text"]
    assert "previous" not in out["report"]


def test_previous_p0_left_blocks_ship_verdict():
    prev = [{"id": "1", "priority": "P0", "problem": "субтитры под кнопками"}]
    raw = {"verdict": "сдавать", "summary": "ок", "issues": [],
           "previous": [{"id": "1", "status": "осталось", "note": "всё там же"}]}
    report = C.clean_report(raw, prev)
    assert report["verdict"] == "ещё проход"
    raw["previous"][0]["status"] = "исправлено"
    assert C.clean_report(raw, prev)["verdict"] == "сдавать"
    # Свободный текст прошлого отчёта — пункты по строкам.
    assert [p["problem"] for p in C.previous_items("- музыка громко\n- конец обрублен")] == ["музыка громко",
                                                                                          "конец обрублен"]


def test_parse_measures_from_ffmpeg_log():
    log = ("[silencedetect @ 0x1] silence_start: 4.2\n[silencedetect @ 0x1] silence_end: 5.6 | silence_duration: 1.4\n"
           "[freezedetect @ 0x2] lavfi.freezedetect.freeze_start: 7\n[freezedetect @ 0x2] lavfi.freezedetect.freeze_end: 9.5\n"
           "[Parsed_ebur128_0 @ 0x3] Summary:\n\n  Integrated loudness:\n    I:         -15.3 LUFS\n"
           "  Loudness range:\n    LRA:         4.1 LU\n\n  True peak:\n    Peak:       -1.2 dBFS\n")
    m = C.parse_measures(log)
    assert m["loudness_lufs"] == -15.3 and m["loudness_range_lu"] == 4.1 and m["true_peak_dbfs"] == -1.2
    assert m["silences"] == [[4.2, 5.6]] and m["frozen"] == [[7.0, 9.5]]


def test_tool_in_project_set_and_feed_group(env, monkeypatch):
    from app import agent_tools, chat_tools
    from app.agent_tools import ToolContext, run_tool

    _client_, store = env
    assert "critique_render" in {t.name for t in agent_tools._project_tools()}
    assert "critique_render" not in agent_tools.registry()  # монтажёр клипа критика не зовёт
    assert "critique_render" in chat_tools.GROUPS["feed"][1]
    _work_, version, _path = _work(store)
    fake = FakeGemini([FIRST])
    monkeypatch.setattr("app.ai.gemini.GeminiClient", lambda *a, **k: fake)
    out, ok = run_tool(ToolContext(store=store), "critique_render", {"feed_version_id": version["id"], "brief": "мем"})
    assert ok, out
    assert out["verdict"] == "ещё проход" and out["issues"][0]["priority"] == "P0"
    assert out["text"].startswith("Вердикт: ещё проход") and out["cost"]["calls"] == 1
    out, ok = run_tool(ToolContext(store=store), "critique_render", {})
    assert not ok and "ровно один" in out["error"]
    out, ok = run_tool(ToolContext(store=store), "critique_render", {"clip_id": 999999})
    assert not ok


def test_clip_target_uses_last_succeeded_render(tmp_path, monkeypatch):
    from test_clip_spec import _setup

    _require_ffmpeg()
    _client_, store, source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    with pytest.raises(C.CritiqueError, match="нет готового рендера"):
        C.resolve_target(store, clip_id=plan["id"])
    with pytest.raises(C.CritiqueError):
        C.resolve_target(store, render_clip_id=424242)
    path = _video(settings.clip_dir / "r1.mp4", size="270x480", seconds=2.0)
    clip = store.create_clip(source["id"], clip_plan_id=plan["id"], title="Клип", status="succeeded")
    store.update_clip(clip["id"], output_path=str(path))
    target = C.resolve_target(store, clip_id=plan["id"])
    assert target["render_clip_id"] == clip["id"] and target["key"] == f"plan-{plan['id']}"
    assert target["path"] == str(path) and target["clip_plan_id"] == plan["id"]
