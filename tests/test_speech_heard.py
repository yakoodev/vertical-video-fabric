"""Сверка субтитров со сказанным. Владелец (ролик из клипов 930–939): «субтитры сильно раньше фразы, плывут», а sub_sync — 0 находок."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import quality_lint as Q
from app.agent_tools import ToolContext, run_tool
from app.settings import settings
from app.speech_activity import fit_unknown_words, line_sync, snap_lines_to_speech
from app.speech_heard import (
    align_words, caption_groups, heard_onset, line_groups, plan_heard_words, retime_issues, text_sync, visible_heard,
)
from app.speech_onsets import snap_payload, trim_stretched
from test_api_pipeline import _client

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "sub_sync_mura_v3.json").read_text(encoding="utf-8"))
CLIPS = sorted(FIXTURE["clips"])


def _w(word, start, end, speaker=None):
    return {"word": word, "start": start, "end": end, **({"speaker": speaker} if speaker else {})}


def _real(pid):
    clip = FIXTURE["clips"][pid]
    groups = caption_groups(clip["words"], clip["layers"])
    return clip, text_sync(groups, clip["words"], clip["heard"], clip["cuts"])[0]


# ---------------------------------------------------------------- настоящий ролик


@pytest.mark.parametrize("pid", CLIPS)
def test_real_early_pages_are_flagged(pid):
    clip, issues = _real(pid)
    flagged = {i["text"] for i in issues if i["kind"] == "early"}
    assert set(clip["bad"]) <= flagged


@pytest.mark.parametrize("pid", CLIPS)
def test_real_good_pages_stay_quiet(pid):
    clip, issues = _real(pid)
    flagged = {i["text"] for i in issues if i["kind"] in ("early", "late", "drift")}
    assert not flagged & set(clip["good"])
    assert len(clip["good"]) >= 5  # фикстура и правда проверяет чистые страницы


def test_worst_page_is_measured_right():
    # «Обязан!» 3.23 с: слово на экране за 2.4 с до звука.
    _clip, issues = _real("934")
    worst = next(i for i in issues if i["text"] == "Обязан!")
    assert worst["kind"] == "early" and 2.0 < worst["by"] < 2.8


@pytest.mark.parametrize("pid", CLIPS)
def test_vad_alone_cannot_see_them(pid):
    # Почему линт молчал: начало каждой плохой страницы лежит на чьём-то голосе.
    clip = FIXTURE["clips"][pid]
    found = line_sync(clip["lines"], clip["words"], [tuple(s) for s in clip["spans"]])
    assert not [i for i in found if i["kind"] == "early"]


# ---------------------------------------------------------------- логика


def test_line_over_another_voice_is_early():
    # Голос в этот момент есть, но это предыдущая фраза: VAD пропускает, сверка по словам — нет.
    words = [_w("Что-то", 1.0, 1.4, "B"), _w("докапывают", 1.4, 2.0, "B"), _w("Работайте", 2.0, 2.6, "A"), _w("быстро", 2.6, 3.0, "A")]
    heard = [_w("что-то", 1.0, 1.4), _w("докапывают", 1.4, 2.8), _w("работайте", 3.6, 4.2), _w("быстро", 4.2, 4.6)]
    groups = [{"start": 1.0, "end": 2.0, "text": "Что-то докапывают", "idx": [0, 1]},
              {"start": 2.0, "end": 3.0, "text": "Работайте быстро", "idx": [2, 3]}]
    assert line_sync([{"start": 2.0, "end": 3.0, "text": "Работайте быстро"}], words, [(0.5, 5.0)]) == []
    issues, checked = text_sync(groups, words, heard)
    assert checked == 2 and [i["text"] for i in issues] == ["Работайте быстро"]
    assert issues[0]["kind"] == "early" and issues[0]["by"] == 1.6 and issues[0]["onset"] == 3.6


def test_late_and_drift_and_unheard():
    words = [_w("Копайте", 5.0, 5.5), _w("глубже", 5.5, 6.0), _w("Начало", 10.0, 10.3), _w("ровно", 10.3, 10.6), _w("потом", 10.6, 11.0),
             _w("всё", 11.0, 11.3), _w("хуже", 11.3, 11.6), _w("некуда", 11.6, 12.0), _w("Совсем", 20.0, 20.5), _w("другие", 20.5, 21.0)]
    heard = [_w("копайте", 3.5, 4.0), _w("глубже", 4.0, 4.5), _w("начало", 10.0, 10.3), _w("ровно", 10.3, 10.6), _w("потом", 10.6, 11.0),
             _w("всё", 12.5, 12.8), _w("хуже", 12.8, 13.1), _w("некуда", 13.1, 13.5), _w("слова", 20.0, 20.5)]
    groups = [{"start": 5.0, "end": 6.0, "text": "late", "idx": [0, 1]},
              {"start": 10.0, "end": 12.0, "text": "drift", "idx": [2, 3, 4, 5, 6, 7]},
              {"start": 20.0, "end": 21.0, "text": "unheard", "idx": [8, 9]}]
    kinds = {i["text"]: i["kind"] for i in text_sync(groups, words, heard)[0]}
    assert kinds == {"late": "late", "drift": "drift", "unheard": "unheard"}


def test_small_offset_is_noise_not_a_finding():
    words = [_w("Привет", 1.0, 1.4), _w("друзья", 1.4, 1.9)]
    heard = [_w("привет", 1.4, 1.8), _w("друзья", 1.8, 2.3)]
    assert text_sync([{"start": 1.0, "end": 1.9, "text": "x", "idx": [0, 1]}], words, heard)[0] == []


def test_stretched_recognised_word_counts_from_its_end():
    assert heard_onset(_w("обязан", 26.42, 28.54)) == pytest.approx(28.54 - (0.2 + 0.66))
    assert heard_onset(_w("да", 1.0, 1.2)) == 1.0


def test_same_word_goes_to_the_nearest_voice():
    # «Привет!» Муры и то же слово чата пятью секундами позже: распознанное слово одно.
    words = [_w("Привет!", 41.2, 41.9, "A"), _w("Привет", 46.5, 47.2, "C")]
    found = align_words(words, [_w("Привет!", 41.2, 41.9)])
    assert list(found) == [0]


def test_short_lone_word_proves_nothing():
    words = [_w("вы", 5.0, 5.2)]
    found = align_words(words, [_w("вы", 3.0, 3.2)])
    assert found == {}
    # Короткое в цепочке — опора.
    words = [_w("не", 5.0, 5.2), _w("он", 5.2, 5.4)]
    assert set(align_words(words, [_w("не", 3.5, 3.7), _w("он", 3.7, 3.9)])) == {0, 1}


def test_cut_speech_does_not_catch_words():
    words = [_w("Фаер", 31.0, 31.5)]
    heard = [_w("фаер", 28.5, 29.0)]
    cuts = [{"start": 28.0, "end": 30.0}]
    assert visible_heard(heard, cuts) == []
    groups = [{"start": 31.0, "end": 31.5, "text": "Фаер", "idx": [0]}]
    assert text_sync(groups, words, heard, cuts)[0] == []
    assert text_sync(groups, words, heard)[0][0]["kind"] == "late"


def test_groups_from_pages_and_lines():
    words = [_w("Раз.", 1.0, 1.4, "A"), _w("Два", 1.5, 1.9, "B"), _w("Три", 2.0, 2.4, "A")]
    layers = [{"type": "captions", "props": {"speakers": ["A"], "maxWords": 7}},
              {"type": "captions", "props": {"speakers": ["A"], "maxWords": 7, "size": 40}},  # тот же слой, разрезанный по времени
              {"type": "captions", "props": {"speakers": ["B"], "maxWords": 7}}]
    assert [g["idx"] for g in caption_groups(words, layers)] == [[0], [1], [2]]
    lines = [{"start": 1.0, "end": 1.9, "text": "Раз. Два"}, {"start": 2.0, "end": 2.4, "text": "Три"}]
    assert [g["idx"] for g in line_groups(lines, words)] == [[0, 1], [2]]


def test_retime_keeps_order_and_is_stable():
    words = [_w("Ну", 2.0, 2.2), _w("вы", 2.2, 2.4), _w("чё", 2.4, 2.6), _w("работайте", 2.6, 3.0)]
    heard = [_w("ну", 3.5, 3.7), _w("вы", 3.7, 3.9), _w("работайте", 4.5, 5.2)]
    groups = [{"start": 2.0, "end": 3.0, "text": "x", "idx": [0, 1, 2, 3]}]
    issues = text_sync(groups, words, heard)[0]
    assert retime_issues(issues, words, align_words(words, heard)) == 1
    assert [w["start"] for w in words] == sorted(w["start"] for w in words)
    assert words[0]["start"] == 3.5 and 3.7 < words[2]["start"] < 4.5 and words[3]["end"] == 5.2
    groups[0]["start"] = words[0]["start"]
    assert text_sync(groups, words, heard)[0] == []


# ---------------------------------------------------------------- линт и инструмент


def _plan(tmp_path, monkeypatch, words):
    _c, store = _client(tmp_path, monkeypatch)
    path = settings.source_dir / "s.mp4"
    path.write_bytes(b"src")
    source = store.create_source("upload", path, duration_sec=60, width=1280, height=720, status="ready")
    analysis = store.create_ai_analysis(source["id"], "mock", status="succeeded")
    seg = store.create_ai_segment(analysis["id"], {"start_sec": 10, "end_sec": 40, "title": "A"})
    plan = store.create_clip_plan(source["id"], analysis["id"], "План", segment_ids=[seg["id"]])
    from app.ai.speakers import lines_from_words

    store.set_clip_plan_subtitles(plan["id"], {"words": words, "lines": lines_from_words(words)})
    return store, plan


def test_lint_flags_line_on_someone_elses_voice(tmp_path, monkeypatch):
    words = [_w("Работайте", 2.0, 2.6), _w("быстрее", 2.6, 3.0)]
    store, plan = _plan(tmp_path, monkeypatch, words)
    monkeypatch.setattr("app.speech_activity.plan_speech_spans", lambda *_a: [(0.5, 6.0)])
    monkeypatch.setattr("app.speech_heard.plan_heard_words", lambda *_a: [_w("работайте", 3.6, 4.2), _w("быстрее", 4.2, 4.6)])
    result = Q.lint_clip(store, plan["id"], checks=["sub_sync"])
    assert result["sub_sync"]["early"] == 1 and result["sub_sync"]["by_text"] is True
    found = result["findings"][0]
    assert found["severity"] == "warn" and "раньше голоса на 1.60" in found["message"] and found["t"] == 2.0


def test_lint_says_when_text_was_not_checked(tmp_path, monkeypatch):
    words = [_w("Работайте", 2.0, 2.6), _w("быстрее", 2.6, 3.0)]
    store, plan = _plan(tmp_path, monkeypatch, words)
    monkeypatch.setattr("app.speech_activity.plan_speech_spans", lambda *_a: [(0.5, 6.0)])

    def broken(*_a):
        raise RuntimeError("модели нет")

    monkeypatch.setattr("app.speech_heard.plan_heard_words", broken)
    result = Q.lint_clip(store, plan["id"], checks=["sub_sync"])
    assert result["sub_sync"]["by_text"] is False and "не сверен" in result["sub_sync"]["by_text_note"]


def test_snap_tool_moves_words_to_what_was_said(tmp_path, monkeypatch):
    words = [_w("Работайте", 2.0, 2.6), _w("быстрее", 2.6, 3.0)]
    store, plan = _plan(tmp_path, monkeypatch, words)
    monkeypatch.setattr("app.speech_activity.plan_speech_spans", lambda *_a: [(0.5, 6.0)])
    monkeypatch.setattr("app.speech_heard.plan_heard_words", lambda *_a: [_w("работайте", 3.6, 4.2), _w("быстрее", 4.2, 4.6)])
    out = snap_lines_to_speech(store, plan["id"])
    assert out["moved"] == 1 and out["by_text"] is True
    saved = store.get_clip_plan_subtitles(plan["id"])
    assert saved["words"][0]["start"] == 3.6 and saved["lines"][0]["start"] == 3.6 and saved["lines"][0]["end"] == 4.6
    ctx = ToolContext(store=store, clip_plan_id=plan["id"])
    assert run_tool(ctx, "snap_subtitles_to_speech", {})[0]["moved"] == 0


def test_heard_words_are_recognised_once_without_vad(tmp_path, monkeypatch):
    words = [_w("Работайте", 2.0, 2.6)]
    store, plan = _plan(tmp_path, monkeypatch, words)
    calls = []

    class Engine:
        def transcribe(self, _audio, profile, model):
            calls.append((profile, model))
            return SimpleNamespace(words=[SimpleNamespace(word="работайте", start=3.6, end=4.2)])

    monkeypatch.setattr("app.subtitles.registry.get_subtitle_provider", lambda *_a: Engine())
    monkeypatch.setattr("app.render._run_ffmpeg", lambda *_a, **_k: None)
    monkeypatch.setattr("app.modules.require", lambda *_a, **_k: None)
    monkeypatch.setattr("app.speech_heard._cache", {})
    assert plan_heard_words(store, plan["id"])[0]["start"] == 3.6
    assert plan_heard_words(store, plan["id"])[0]["start"] == 3.6
    monkeypatch.setattr("app.speech_heard._cache", {})  # новый процесс: читаем с диска, не распознаём заново
    assert plan_heard_words(store, plan["id"])[0]["word"] == "работайте"
    assert len(calls) == 1 and calls[0][0]["vad_filter"] is False and calls[0][1] == "large-v3-turbo"


# ---------------------------------------------------------------- источник


def test_known_word_moved_far_returns_to_recognised():
    # Клип 930: агент разложил строку равномерно, «не» и «особо» ушли на 0.6 с раньше голоса и прошли как «время поправили нарочно».
    old = [_w("не", 4962.28, 4962.5), _w("особо", 4962.56, 4962.9)]
    new = [_w("не", 4961.62, 4961.9), _w("особо", 4961.98, 4962.3)]
    words, notes = fit_unknown_words(new, old, lambda: [])
    assert [w["start"] for w in words] == [4962.28, 4962.56] and "возвращено" in notes[0]
    # Малая поправка остаётся: её могли сделать нарочно.
    words, notes = fit_unknown_words([_w("не", 4962.1, 4962.5)], old[:1], lambda: [])
    assert words[0]["start"] == 4962.1 and not notes


def test_stretched_word_start_is_trimmed_from_its_end():
    words = [_w("Обязан!", 25.31, 28.54), _w("Да", 29.0, 29.3), _w("растянутоооо", 30.0, 31.5)]
    assert trim_stretched(words) == 1
    assert words[0]["start"] == round(28.54 - (0.2 + 0.66 + 0.3), 3) and words[0]["end"] == 28.54
    assert words[1]["start"] == 29.0 and words[2]["start"] == 30.0


def test_generated_payload_gets_trimmed_start_and_line(monkeypatch):
    monkeypatch.setattr("app.speech_onsets._vad_or_loudness", lambda _pcm, frames: [True] * 2000)
    payload = {"words": [_w("Обязан!", 1.0, 4.0)], "lines": [{"start": 1.0, "end": 4.0, "text": "Обязан!"}]}
    assert snap_payload(payload, b"\x00\x00" * 16000) == 1
    assert payload["words"][0]["start"] == 2.84 and payload["lines"][0]["start"] == 2.84
