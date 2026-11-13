"""Озвучка своим текстом и её анализатор (эксперимент 12.11: три рекламы с битой озвучкой).

* audio-слой с файлом без end живёт длину файла, а не 3 с; короче файла — предупреждение и находка lint;
* add_voiceover: фраза → файл → слой vo_N (role voice, mode track, end = длина файла) подряд, холст удлиняется,
  субтитры по распознанным словам, вердикт проверки;
* check_voiceover ловит каждую поломку: обрезанный слой, зачитанный style, дыру в голосе, голос как эффект, субтитры
  мимо голоса, тихий голос под музыкой.

Whisper и TTS подменены: «распознавание» читает текст озвучки из описания файла (как будто расслышало его точно), а
``HEARD_PREFIX`` добавляет слова, «зачитанные» перед ним.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app import voice_asr, voice_check, voiceover
from app.agent_tools import ToolContext, run_tool
from app.dubbing import Speech
from test_api_pipeline import _require_ffmpeg
from test_data_foundation import _store

CHARS_PER_SEC = 14.0
HEARD_PREFIX: dict[str, list[str]] = {}  # текст фразы → слова, которые «слышны» перед ним (зачитанный style)


def _tone(path: Path, seconds: float, freq: int = 330, volume: float = 0.6) -> Path:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"sine=frequency={freq}:duration={seconds:.2f}:sample_rate=48000", "-af", f"volume={volume}",
                    "-c:a", "libmp3lame", "-b:a", "96k", str(path)], check=True, capture_output=True)
    return path


class FakeTTS:
    """«Синтез»: тон длиной как фраза + 0.3 с тишины-поля по краям не нужны — тон с 0 с."""

    name, paid, model, voice = "fake", False, "fake-1", "Aoede"
    calls: list[str] = []

    def price_rub_per_char(self) -> float:
        return 0.0

    def synthesize(self, text: str, out_path: Path) -> Speech:
        FakeTTS.calls.append(text)
        _tone(out_path, max(0.8, len(text) / CHARS_PER_SEC))
        return Speech(out_path, 0.0)


def _fake_transcribe(store):
    def transcribe(path: Path) -> list[dict]:
        from app.voice_layers import intended_text

        row = store.db.query_one("SELECT id FROM montage_assets WHERE file_path = ?", (str(path),))
        text = intended_text(store.get_montage_asset(int(row["id"])))[0] if row else ""
        words = HEARD_PREFIX.get(text, []) + text.split()
        if not words:
            return []
        sec = voiceover_len(text)
        step = sec / len(words)
        return [{"word": w, "start": round(i * step + 0.02, 3), "end": round((i + 1) * step - 0.03, 3)}
                for i, w in enumerate(words)]

    return transcribe


def voiceover_len(text: str) -> float:
    return max(0.8, len(text) / CHARS_PER_SEC)


@pytest.fixture
def canvas(tmp_path, monkeypatch):
    _require_ffmpeg()
    import app.canvas as canvas_mod

    monkeypatch.setattr(canvas_mod, "CANVAS_WIDTH", 108)
    monkeypatch.setattr(canvas_mod, "CANVAS_HEIGHT", 192)
    store = _store(tmp_path, monkeypatch)
    monkeypatch.setattr("app.montage_assets.assets_dir", lambda: _mk(tmp_path / "assets"))
    monkeypatch.setattr(voiceover, "_tts", lambda provider, voice, model: FakeTTS())
    monkeypatch.setattr(voice_asr, "TRANSCRIBE", _fake_transcribe(store))
    voice_check.reset_cache()
    HEARD_PREFIX.clear()
    FakeTTS.calls.clear()
    out, ok = run_tool(ToolContext(store=store), "create_canvas_clip", {"duration": 6, "title": "Реклама"})
    assert ok, out
    return store, out["clip_id"]


def _mk(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


LINES = [{"text": "Хочешь стать витубером, но нет своей модели?"},
         {"text": "Загляни в Витубику: готовые модели для стрима."},
         {"text": "Оплата в рублях, без посредников."},
         {"text": "Промокод Free10 даёт скидку десять процентов."}]


def _voiceover(store, clip_id, **extra):
    out, ok = run_tool(ToolContext(store=store, clip_plan_id=clip_id), "add_voiceover",
                       {"lines": LINES, "voice": "Aoede", **extra}, validate=True)
    assert ok, out
    return out


def _layers(store, clip_id):
    return {l["id"]: l for l in (store.get_clip_plan(clip_id)["render_settings"] or {}).get("layers") or []}


def _codes(report):
    return [f["code"] for f in report["findings"] if f["severity"] == "warn"]


# --------------------------------------------------------------------------- длина audio-слоя


def test_audio_layer_without_end_lasts_the_file_not_three_seconds(canvas, tmp_path):
    store, clip_id = canvas
    tone = _tone(tmp_path / "vo.mp3", 4.4)
    asset = store.create_montage_asset(kind="audio", label="голос", file_path=str(tone), duration_sec=4.4,
                                       has_audio=True, size_bytes=tone.stat().st_size)
    ctx = ToolContext(store=store, clip_plan_id=clip_id)
    out, ok = run_tool(ctx, "add_layers", {"layers": [
        {"id": "voice1", "type": "audio", "start": 0.5, "props": {"asset_id": str(asset["id"]), "mode": "track",
                                                                  "role": "voice"}}]})
    assert ok, out
    assert _layers(store, clip_id)["voice1"]["end"] == pytest.approx(4.9, abs=0.02)  # не 0.5 + 3
    assert "по длине файла" in out["audio_end"][0]
    assert not any("обрезан" in w for w in out["warnings"])

    cut, ok = run_tool(ctx, "update_layer", {"id": "voice1", "patch": {"end": 3.5}})
    assert ok, cut
    assert any("голос voice1 обрезан: 3.0 из 4.4 с" in w for w in cut["warnings"]), cut["warnings"]
    lint, ok = run_tool(ctx, "lint_clip", {"checks": ["voice"], "media": False})
    assert ok, lint
    clipped = [f for f in lint["findings"] if f.get("code") == "clipped"]
    assert clipped and clipped[0]["message"] == "голос voice1 обрезан: 3.0 из 4.4 с"
    assert clipped[0]["fix_call"] == {"tool": "update_layer", "args": {"id": "voice1", "patch": {"end": 4.9}}}


def test_short_sound_effect_is_not_nagged(canvas, tmp_path):
    store, clip_id = canvas
    tone = _tone(tmp_path / "whoosh.mp3", 2.0)
    asset = store.create_montage_asset(kind="audio", label="вжух", file_path=str(tone), duration_sec=2.0,
                                       has_audio=True, size_bytes=tone.stat().st_size)
    out, ok = run_tool(ToolContext(store=store, clip_plan_id=clip_id), "add_layers", {"layers": [
        {"id": "wh", "type": "audio", "start": 1, "end": 1.6, "props": {"asset_id": str(asset["id"]), "volume": 0.4}}]})
    assert ok, out
    assert not any("обрезан" in w or "короче файла" in w for w in out["warnings"])


# --------------------------------------------------------------------------- add_voiceover


def test_add_voiceover_lays_lines_back_to_back_with_subtitles_and_clean_verdict(canvas):
    store, clip_id = canvas
    out = _voiceover(store, clip_id)
    assert out["status"] == "done" and out["provider"] == "fake" and len(out["lines"]) == 4
    layers = _layers(store, clip_id)
    prev_speech_end = None
    for row in out["lines"]:
        layer = layers[row["layer"]]
        props = layer["props"]
        assert props["role"] == "voice" and props["mode"] == "track"
        assert layer["end"] - layer["start"] == pytest.approx(voiceover_len(row["text"]), abs=0.05)  # длина файла
        if prev_speech_end is not None:  # следующая фраза — через 0.35 с после последнего слышного слова
            assert row["speech"][0] - prev_speech_end == pytest.approx(0.35, abs=0.06)
        prev_speech_end = row["speech"][1]
    assert out["lines"][0]["speech"][0] == pytest.approx(0.3, abs=0.02)
    # Холст 6 с был короче голоса — удлинён до конца речи + хвост.
    assert out["clip_duration"] == pytest.approx(out["voice_end"] + voiceover.TAIL_SEC, abs=0.1)
    assert any("удлинён" in n for n in out["notes"])
    # Субтитры — текст фраз на времени распознанных слов; слой субтитров поставлен.
    words = store.get_clip_plan_subtitles(clip_id)["words"]
    assert [w["word"] for w in words][:3] == ["Хочешь", "стать", "витубером,"]
    assert out["subtitles"]["set"] and out["subtitles"]["captions_layer"] == "caps_vo"
    assert "caps_vo" in layers
    assert out["verdict"] == "озвучка в порядке" or out["verdict"].startswith("озвучка в порядке"), out["check"]
    assert "1 | vo_1 |" in out["table"]
    # Текст фразы — в описании файла: по нему анализатор сверяет «услышано = написано».
    asset = store.get_montage_asset(out["lines"][1]["asset_id"])
    assert asset["description"].startswith("Текст озвучки: «Загляни в Витубику")


def test_add_voiceover_replace_and_cache(canvas):
    store, clip_id = canvas
    _voiceover(store, clip_id)
    calls = len(FakeTTS.calls)
    again = _voiceover(store, clip_id, replace=True)
    assert len(FakeTTS.calls) == calls  # те же фразы тем же голосом — те же файлы
    assert all(r["cached"] for r in again["lines"])
    vo = [k for k in _layers(store, clip_id) if k.startswith("vo_")]
    assert sorted(vo) == ["vo_1", "vo_2", "vo_3", "vo_4"]  # заменены, а не задвоены


def test_add_voiceover_resynthesizes_when_style_is_heard(canvas):
    store, clip_id = canvas
    HEARD_PREFIX[LINES[0]["text"]] = ["Говори", "бодро", "и", "дружелюбно."]
    out = _voiceover(store, clip_id)
    assert FakeTTS.calls.count(LINES[0]["text"]) == 2  # лишнее в начале — синтез ещё раз
    assert FakeTTS.calls.count(LINES[1]["text"]) == 1
    # Обе попытки «зачитали» — в клипе лучшая, а проверка говорит об этом прямо.
    assert any(f["code"] == "lead_extra" for f in out["check"]["findings"])


def test_add_voiceover_paid_provider_only_estimates(canvas, monkeypatch):
    store, clip_id = canvas

    class Paid(FakeTTS):
        name, paid = "polza", True

        def price_rub_per_char(self):
            return 0.01

        def synthesize(self, text, out_path):
            raise AssertionError("без confirm платный синтез запрещён")

    monkeypatch.setattr(voiceover, "_tts", lambda provider, voice, model: Paid())
    out, ok = run_tool(ToolContext(store=store, clip_plan_id=clip_id), "add_voiceover",
                       {"lines": LINES, "provider": "polza"})
    assert ok and out["status"] == "estimate" and out["estimate"]["rub"] > 0
    assert not [k for k in _layers(store, clip_id) if k.startswith("vo_")]


def test_add_voiceover_rejects_too_long_line_instead_of_cutting(canvas):
    store, clip_id = canvas
    out, ok = run_tool(ToolContext(store=store, clip_plan_id=clip_id), "add_voiceover",
                       {"lines": [{"text": "слово " * 100}]})
    assert not ok and "не режу" in out["error"]


def test_narrator_voice_from_brief(canvas, monkeypatch):
    from app import projects as P
    from app.projects_brief import narrator_voice, update_brief

    store, clip_id = canvas
    project = P.create_project(store, "vtubika.store") if hasattr(P, "create_project") else None
    if project is None:
        pytest.skip("нет create_project")
    pid = int(project["id"])
    update_brief(store, pid, {"narrator_voice": "aoede"})
    assert narrator_voice(store, pid) == "Aoede"
    with pytest.raises(ValueError, match="не знаю"):
        update_brief(store, pid, {"narrator_voice": "brian"})
    P.assign_plan_project(store, clip_id, pid)
    assert voiceover.resolve_voice(store, clip_id, "gemini", None) == ("Aoede", f"рассказчик проекта #{pid}")
    assert voiceover.resolve_voice(store, clip_id, "gemini", "Puck")[0] == "Puck"


# --------------------------------------------------------------------------- check_voiceover ловит поломки


def _check(store, clip_id, **args):
    voice_check.reset_cache()
    out, ok = run_tool(ToolContext(store=store, clip_plan_id=clip_id), "check_voiceover", args, validate=True)
    assert ok, out
    return out


def test_check_catches_cut_layer_style_gap_and_role(canvas):
    store, clip_id = canvas
    out = _voiceover(store, clip_id)
    clean = _check(store, clip_id, levels=False)
    assert clean["clean"], clean["findings"]
    ctx = ToolContext(store=store, clip_plan_id=clip_id)

    # 1. Обрезанный слой: 3 с из файла длиннее.
    first = out["lines"][0]
    run_tool(ctx, "update_layer", {"id": "vo_1", "patch": {"end": first["start"] + 1.5}})
    report = _check(store, clip_id, levels=False)
    assert "clipped" in _codes(report) and report["verdict"].startswith("озвучка с ошибками: ")
    assert "голос vo_1 обрезан: 1.5 из" in report["verdict"], report["verdict"]
    run_tool(ctx, "update_layer", {"id": "vo_1", "patch": {"end": first["end"]}})

    # 2. Зачитанный style: в начале фразы слышно чужое.
    HEARD_PREFIX[LINES[1]["text"]] = ["Говори", "быстро,", "живо"]
    voice_asr._MEM.clear()
    report = _check(store, clip_id, levels=False)
    lead = [f for f in report["findings"] if f["code"] == "lead_extra"]
    assert lead and lead[0]["layer"] == "vo_2" and "Говори быстро" in lead[0]["message"]
    HEARD_PREFIX.clear()

    # 3. Дыра: последняя фраза уехала на 3 с позже.
    last = out["lines"][-1]
    run_tool(ctx, "update_layer", {"id": last["layer"], "patch": {"start": last["start"] + 3,
                                                                  "end": last["end"] + 3}})
    report = _check(store, clip_id, levels=False)
    gaps = [f for f in report["findings"] if f["code"] == "gap"]
    assert gaps and gaps[0]["gap_sec"] > 3
    run_tool(ctx, "update_layer", {"id": last["layer"], "patch": {"start": last["start"], "end": last["end"]}})

    # 4. Голос учтён как эффект (B1: голос всего на 1.4 LU над музыкой).
    run_tool(ctx, "update_layer", {"id": "vo_3", "patch": {"props": {"role": "sfx", "mode": "effect"}}})
    report = _check(store, clip_id, levels=False)
    role = [f for f in report["findings"] if f["code"] == "role"]
    assert role and role[0]["fix_call"]["args"]["patch"] == {"props": {"role": "voice", "mode": "track"}}


def test_check_catches_subtitles_off_the_voice_and_silent_tail(canvas):
    store, clip_id = canvas
    out = _voiceover(store, clip_id)
    ctx = ToolContext(store=store, clip_plan_id=clip_id)
    words = store.get_clip_plan_subtitles(clip_id)["words"]
    late = [{"word": w["word"], "start": w["start"] + 1.0, "end": w["end"] + 1.0} for w in words]
    run_tool(ctx, "set_subtitle_words", {"words": late, "align": False})
    report = _check(store, clip_id, levels=False)
    off = [f for f in report["findings"] if f["code"] == "sub_offset"]
    assert off and "позже голоса на 1.0 с" in off[0]["message"]

    # Субтитры со словами, которых нет в голосе (обрезанный текст A1).
    extra = words + [{"word": x, "start": out["voice_end"] + 0.2 + i * 0.3, "end": out["voice_end"] + 0.45 + i * 0.3}
                     for i, x in enumerate(["платишь", "рублях", "сайте", "vtubika"])]
    run_tool(ctx, "set_subtitle_words", {"words": extra, "align": False})
    report = _check(store, clip_id, levels=False)
    assert "sub_unheard" in _codes(report)

    # Холст на 20 с длиннее голоса: последние 30 % молчат.
    plan = store.get_clip_plan(clip_id)
    last = plan["segments"][-1]
    run_tool(ctx, "trim_piece", {"index": len(plan["segments"]) - 1, "end_sec": float(last["end_sec"]) + 20})
    report = _check(store, clip_id, levels=False)
    assert "coverage" in _codes(report)


def test_check_levels_voice_under_music(canvas, tmp_path):
    """Голос тихий, «музыка» (громкий тон-дорожка role music) громче — находка level с auto_mix."""
    store, clip_id = canvas
    _voiceover(store, clip_id)
    ctx = ToolContext(store=store, clip_plan_id=clip_id)
    loud = _tone(tmp_path / "music.mp3", 20, freq=200, volume=0.9)
    asset = store.create_montage_asset(kind="audio", label="музыка", file_path=str(loud), duration_sec=20,
                                       has_audio=True, size_bytes=loud.stat().st_size)
    ok_add = run_tool(ctx, "add_layers", {"layers": [{"id": "bgm", "type": "audio", "start": 0, "end": 12,
                                                      "props": {"asset_id": str(asset["id"]), "mode": "track",
                                                                "role": "music", "volume": 1.0}}]})[1]
    assert ok_add
    for n in range(1, 5):
        run_tool(ctx, "update_layer", {"id": f"vo_{n}", "patch": {"props": {"volume": 0.1}}})
    report = _check(store, clip_id)
    level = [f for f in report["findings"] if f["code"] == "level"]
    assert level and level[0]["severity"] == "warn" and level[0]["fix_call"]["tool"] == "auto_mix", report.get("levels")


# --------------------------------------------------------------------------- QC рендера и render_status


def test_render_qc_marks_voice_errors(monkeypatch):
    from app import render_finish, render_tools

    monkeypatch.setattr(voice_check, "qc_report", lambda store, plan_id: {
        "ok": False, "summary": "озвучка с ошибками: голос vo_1 обрезан: 3.0 из 6.4 с", "warnings": ["…"]})

    class R(render_finish.FinishRender):
        store = None

    qc = R()._qc_voiceover({"clip_plan_id": 5}, {"ok": True, "issues": []})
    assert qc["ok"] is False and qc["issues"][-1]["code"] == "voice"
    assert qc["issues"][-1]["text"].startswith("озвучка с ошибками")

    from app.render_jobs import _brief_clip

    brief = _brief_clip({"id": 9, "status": "succeeded", "qc": qc})
    assert brief["voice"].startswith("озвучка с ошибками") and brief["voice_ok"] is False
    view = render_tools._status_view({"render_id": "r1", "clip_id": 5, "status": "done", "created_at": 0,
                                      "started_at": 0, "finished_at": 1, "result": brief})
    assert view["note"].startswith("файл готов, но озвучка с ошибками") and "check_voiceover" in view["next"]


def test_clip_without_voice_is_silent_for_qc(canvas):
    store, clip_id = canvas
    assert voice_check.qc_report(store, clip_id) is None
