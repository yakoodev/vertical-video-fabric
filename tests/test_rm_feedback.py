"""Доработки по отзыву монтажёра «Рика и Морти»: фоновая расшифровка, кадрирование куска
(set_focus, кусок шире окна), рамки героя по кускам, строки субтитров, высота хука."""

from __future__ import annotations

import json
import subprocess
import threading

from app.agent_tools import ToolContext, run_tool
from app.clip_history import snapshot
from test_api_pipeline import _require_ffmpeg
from test_clip_spec import _setup


def _ctx(store, plan_id=None):
    if plan_id is None:
        return ToolContext(store=store, clip_plan_id=None)
    return ToolContext(store=store, clip_plan_id=plan_id, on_first_change=lambda: snapshot(store, plan_id, "ИИ: тест"))


# ------------------------------------------------------------------ 1. расшифровка фоном


def test_source_transcript_runs_in_background_and_reports_progress(tmp_path, monkeypatch):
    _require_ffmpeg()
    from app import manual_clips

    client, store, source, _plan, _a, _b = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(manual_clips, "_JOBS", {})
    monkeypatch.setattr(manual_clips, "TRANSCRIPT_WAIT_SEC", 0.2)
    release, calls = threading.Event(), []

    def slow(path, model=None, progress=None, **kw):
        calls.append(kw)
        progress(0.0, 600.0)
        progress(300.0, 600.0)
        release.wait(10)
        progress(600.0, 600.0)
        return [{"start": 1.0, "end": 2.0, "text": "раз"}, {"start": 4.0, "end": 5.0, "text": "два"}]

    monkeypatch.setattr("app.ai.transcript.transcribe_source_cues", slow)
    sid = source["id"]
    out, ok = run_tool(_ctx(store), "get_source_transcript", {"project_id": sid})
    assert ok, out
    assert out["status"] == "running" and out["done_min"] == 5 and out["total_min"] == 10
    assert "распознаю: готово 5 из 10 мин, повтори через ~" in out["note"] and out["retry_after_sec"] > 0
    assert "lines" not in out
    # REST: 202 и Retry-After, второе распознавание не запускается.
    resp = client.get(f"/api/sources/{sid}/transcript", params={"wait": 0.1})
    assert resp.status_code == 202 and int(resp.headers["Retry-After"]) > 0 and resp.json()["status"] == "running"
    assert len(calls) == 1 and calls[0]["chunk_sec"] == manual_clips.JOB_CHUNK_SEC
    release.set()
    out, ok = run_tool(_ctx(store), "get_source_transcript", {"project_id": sid})
    assert ok and out["status"] == "done" and [line[2] for line in out["lines"]] == ["раз", "два"]
    # Дальше — из кэша, сразу и без нового распознавания.
    resp = client.get(f"/api/sources/{sid}/transcript", params={"from_sec": 3})
    assert resp.status_code == 200 and resp.json()["cached"] and [c["text"] for c in resp.json()["cues"]] == ["два"]
    assert len(calls) == 1


def test_source_transcript_failure_is_reported_once_then_retried(tmp_path, monkeypatch):
    _require_ffmpeg()
    from app import manual_clips

    _client, store, source, _plan, _a, _b = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(manual_clips, "_JOBS", {})
    calls = []

    def broken(path, model=None, **kw):
        calls.append(1)
        raise RuntimeError("CUDA out of memory")

    monkeypatch.setattr("app.ai.transcript.transcribe_source_cues", broken)
    out, ok = run_tool(_ctx(store), "get_source_transcript", {"project_id": source["id"]})
    assert not ok and "расшифровка не получилась" in out["error"]
    run_tool(_ctx(store), "get_source_transcript", {"project_id": source["id"]})
    assert len(calls) == 2  # после ошибки следующий вызов пробует заново


# ------------------------------------------------------------------ 2. set_focus


def test_set_focus_saves_track_with_cut_and_get_clip_shows_it(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, _source, plan, _a, b = _setup(tmp_path, monkeypatch)
    ctx = _ctx(store, plan["id"])
    # Кусок 1 — исходник 1..6 с; склейка из get_shots на 3.0 с исходника.
    out, ok = run_tool(ctx, "set_focus", {"piece": 1, "points": [
        {"source_t": 3.0, "x": 0.3, "cut": True}, {"t": 0, "x": 0.7}]})
    assert ok, out
    assert out["focus"] == [{"t": 0.0, "x": 0.7}, {"t": 2.0, "x": 0.3, "cut": True}]
    assert store.get_ai_segment(b["id"])["focus"] == out["focus"]
    clip, _ = run_tool(ctx, "get_clip", {})
    assert clip["pieces"][1]["focus"] == out["focus"] and clip["pieces"][0]["focus"] == []
    assert clip["framing"]["window_w"] == 0.316  # 320×180 → окно 9:16 видит треть ширины
    assert "set_focus" in clip["framing"]["note"]
    # Первая точка не в 0 — окно до неё стоит на её x (точка в 0 добавляется явно).
    out, ok = run_tool(ctx, "set_focus", {"piece": 0, "points": [{"t": 1.5, "x": 0.6}]})
    assert ok and out["focus"] == [{"t": 0.0, "x": 0.6}, {"t": 1.5, "x": 0.6}]
    bad, ok = run_tool(ctx, "set_focus", {"piece": 1, "points": [{"t": 9, "x": 0.5}, {"t": 1, "x": 1.4}]})
    assert not ok and len(bad["problems"]) == 2
    assert not run_tool(ctx, "set_focus", {"piece": 5, "points": []})[1]


# ------------------------------------------------------------------ 4. кусок шире окна


def test_set_piece_fit_stores_per_piece_and_renders_wider_frame(tmp_path, monkeypatch):
    _require_ffmpeg()
    from app import render as _render

    client, store, source, plan, a, _b = _setup(tmp_path, monkeypatch)
    ctx = _ctx(store, plan["id"])
    out, ok = run_tool(ctx, "set_piece_fit", {"piece": 0, "w": 0.6})
    assert ok, out
    assert out["fit"] == {"w": 0.6, "bg": "blur"}
    clip, _ = run_tool(ctx, "get_clip", {})
    assert clip["pieces"][0]["fit"] == {"w": 0.6, "bg": "blur"} and clip["pieces"][1]["fit"] is None
    spec = client.get(f"/api/clip-plans/{plan['id']}/spec").json()
    assert spec["pieces"][0]["fit"] == {"w": 0.6, "bg": "blur"}
    spec["pieces"][1]["fit"] = 0.2  # уже окна — ошибка файла
    resp = client.put(f"/api/clip-plans/{plan['id']}/spec", json=spec)
    assert resp.status_code == 422 and "fit" in resp.text
    assert not run_tool(ctx, "set_piece_fit", {"piece": 0, "w": 0.1})[1]
    # Камера «отъехать» не может — честный отказ с подсказкой.
    cam, ok = run_tool(ctx, "set_camera", {"keys": [{"t": 0, "zoom": 0.7}]})
    assert not ok and "zoom < 1" in cam["error"] and "set_piece_fit" in cam["problems"][0]

    # Рендер основы куска: окно шире, сверху и снизу размытый фон, размер кадра тот же.
    segment = store.get_ai_segment(a["id"])
    preset = {**store.create_ffmpeg_preset("Тест"), "output_width": 216, "output_height": 384, "fps": 25,
              "smart_reframe": True, "layout": {"mode": "split"}}
    piece = _render._preset_for_segment(preset, segment)
    assert piece["layout"] is None and piece["piece_fit"] == {"w": 0.6, "bg": "blur"}
    src = store.get_source(source["id"])
    reframe = _render._segment_reframe_x({**segment, "focus": [{"t": 0, "x": 0.3}]}, piece, src)
    out_path = tmp_path / "fit.mp4"
    args = _render.build_ffmpeg_render_args(source_path=__import__("pathlib").Path(src["local_path"]),
                                            output_path=out_path, start_sec=0, end_sec=1, preset=piece,
                                            reframe_x=reframe)
    assert "boxblur" in " ".join(args)
    subprocess.run(args, check=True, capture_output=True, timeout=120)
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                            "-of", "csv=p=0", str(out_path)], capture_output=True, text=True, check=True)
    assert probe.stdout.strip() == "216,384"
    # Сброс: обычный кадр.
    out, ok = run_tool(ctx, "set_piece_fit", {"piece": 0, "w": None})
    assert ok and out["fit"] is None and store.get_ai_segment(a["id"])["fit"] is None


def test_cut_parts_keep_piece_fit():
    from app.cuts import plan_cuts

    segs = [{"id": 1, "source_id": 1, "start_sec": 0.0, "end_sec": 10.0, "fit": {"w": 0.7, "bg": "black"}}]
    parts = plan_cuts(segs, [{"start": 4.0, "end": 5.0}])
    assert parts and all(p["fit"] == {"w": 0.7, "bg": "black"} for p in parts.parts)


# ------------------------------------------------------------------ 3. рамка героя по кускам


def test_subject_boxes_are_per_piece(tmp_path, monkeypatch):
    _require_ffmpeg()
    from app.ai import subject_box
    from app.settings import settings

    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "runtime_dir", tmp_path / "runtime")
    monkeypatch.setattr("app.motion_render.render_frame_png", lambda *a, **k: b"png")
    left, right = {"x0": 0.05, "y0": 0.3, "x1": 0.4, "y1": 0.9}, {"x0": 0.6, "y0": 0.35, "x1": 0.95, "y1": 0.9}
    seen = []

    class Client:
        def generate_content(self, model, body):
            seen.append(len(body["contents"][0]["parts"]) - 1)
            frames = [left, left, left, right, right, {}]
            return {"candidates": [{"content": {"parts": [{"text": json.dumps({"frames": frames})}]}}]}

    out = subject_box.subject_boxes(store, plan["id"], Client())
    assert seen == [6]  # по три кадра с каждого из двух кусков
    assert out["pieces"] == [left, right]
    assert out["union"] == {"x0": 0.05, "y0": 0.3, "x1": 0.95, "y1": 0.9}
    assert subject_box.subject_box(store, plan["id"], Client()) == out["union"] and seen == [6]  # кэш
    monkeypatch.setattr("app.ai.subject_box.subject_boxes", lambda store, plan_id, client=None: out)
    clip, _ = run_tool(_ctx(store, plan["id"]), "get_clip", {})
    assert [p["subject_box"] for p in clip["pieces"]] == [left, right]
    assert "pieces[].subject_box" in clip["positions"]["subject_note"]


# ------------------------------------------------------------------ 5–6. строки субтитров


def _w(word, start, end, **extra):
    return {"word": word, "start": start, "end": end, **extra}


def test_whisper_hyphen_tails_are_glued():
    from app.clip_subtitles import tidy_words

    words = [_w("какой", 0.0, 0.3), _w("-то", 0.3, 0.45), _w("кунг", 0.5, 0.7), _w("-фу", 0.7, 0.9),
             _w("ну", 1.0, 1.1), _w("-ка", 1.1, 1.3), _w("да", 1.5, 1.6), _w("-", 1.6, 1.7), _w("нет", 1.7, 1.9)]
    out = tidy_words(words)
    assert [w["word"] for w in out] == ["какой-то", "кунг-фу", "ну-ка", "да", "-", "нет"]
    assert (out[0]["start"], out[0]["end"]) == (0.0, 0.45)


def test_lines_break_on_sentence_end_pause_and_speaker():
    from app.ai.speakers import lines_from_words

    words = [_w("ДТП.", 0.0, 0.4), _w("Бывает.", 0.5, 0.9), _w("Ну,", 1.0, 1.2), _w("слушай", 1.25, 1.6),
             _w("потом", 2.5, 2.8), _w("Морти", 2.85, 3.2, speaker="B")]
    assert [l["text"] for l in lines_from_words(words)] == ["ДТП.", "Бывает.", "Ну, слушай", "потом", "Морти"]


def test_recognized_lines_are_built_from_words_without_duplicates():
    from app.clip_subtitles import payload_from_result, words_by_line
    from app.subtitles.contracts import SubtitleResult, SubtitleSegment, SubtitleWord

    words = [("Слушай,", 0.0, 0.5), ("какой", 0.55, 0.8), ("-то", 0.8, 0.95), ("бред.", 0.95, 1.4),
             ("Да.", 1.5, 1.8)]
    result = SubtitleResult(
        text="", language="ru", duration=2.0,
        # Сегменты Whisper перекрываются на слове «Слушай,» — раньше оно уходило в обе строки.
        segments=[SubtitleSegment(start=0.0, end=0.5, text="Слушай,"), SubtitleSegment(start=0.3, end=1.8, text="x")],
        words=[SubtitleWord(word=w, start=a, end=b) for w, a, b in words],
    )
    payload = payload_from_result(result, "whisper", "turbo")
    assert [l["text"] for l in payload["lines"]] == ["Слушай, какой-то бред.", "Да."]
    assert [w["word"] for w in payload["words"]] == ["Слушай,", "какой-то", "бред.", "Да."]
    # Слово на стыке двух строк — ровно в одной.
    lines = [{"start": 0.0, "end": 0.52, "text": "a"}, {"start": 0.4, "end": 1.0, "text": "b"}]
    grouped = words_by_line(lines, [_w("Слушай,", 0.0, 0.5), _w("какой", 0.55, 0.8)])
    assert [[w["word"] for w in g] for g in grouped] == [["Слушай,"], ["какой"]]


def test_set_subtitle_words_keeps_speakers_and_edit_has_no_duplicates(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    ctx = _ctx(store, plan["id"])
    out, ok = run_tool(ctx, "set_subtitle_words", {"words": [
        _w("ДТП.", 0.0, 0.4), _w("Бывает.", 0.5, 0.9), _w("Ну,", 1.0, 1.2), _w("кто", 1.25, 1.5),
        _w("-то", 1.5, 1.7), _w("Рик!", 1.8, 2.2, s="B")]})
    assert ok, out
    subs = run_tool(ctx, "get_clip", {})[0]["subtitles"]["lines"]
    assert [l["text"] for l in subs] == ["ДТП.", "Бывает.", "Ну, кто-то", "Рик!"]
    assert subs[3]["words"][0][3] == "B"
    assert not run_tool(ctx, "set_subtitle_words", {"words": [_w("x", 0, 1, s="Z")]})[1]
    # Правка текста строки не плодит дублей слов на стыке.
    ok = run_tool(ctx, "edit_subtitle_lines", {"edits": [{"i": 0, "text": "ДТП."}]})[1]
    assert ok
    words = store.get_clip_plan_subtitles(plan["id"])["words"]
    assert [w["word"] for w in words] == ["ДТП.", "Бывает.", "Ну,", "кто-то", "Рик!"]
    assert words[-1].get("speaker") == "B"


# ------------------------------------------------------------------ 7–8. хук, подсветка, пресет


def test_hook_position_accounts_for_text_height(tmp_path, monkeypatch):
    _require_ffmpeg()
    from app.agent_tools import SAFE_TOP, safe_text_y, text_half_height, text_lines
    from app.auto_edit import build_auto_edit

    long_hook = {"text": "КОЛОБОК ПОХОЖ НА ОЛЕГА МОНГОЛА", "size": 72, "weight": 900, "uppercase": True, "maxWidth": 980}
    assert text_lines(long_hook) >= 2 and text_lines({"text": "Я ПРОДАМСЯ", "size": 96, "maxWidth": 980}) == 1
    hook = next(l for l in build_auto_edit("Колобок похож на Олега Монгола", [], [], 10.0)["layers"]
                if l["id"] == "auto_hook")
    assert hook["y"] > 0.13 and hook["y"] - text_half_height(hook["props"]) >= SAFE_TOP - 1e-9
    assert safe_text_y({"text": "КОРОТКО", "size": 96}, 0.13) == 0.13

    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    ctx = _ctx(store, plan["id"])
    out, ok = run_tool(ctx, "add_layers", {"layers": [{
        "type": "text", "start": 0, "end": 2, "x": 0.5, "y": 0.13,
        "props": {"text": "ОН ПРОДАЛ ПОРТАЛЬНУЮ ПУШКУ ЗА ДВА БАКСА", "size": 96, "weight": 900, "uppercase": True}}]})
    assert ok and any("выше безопасной зоны" in w for w in out["warnings"]), out
    positions = run_tool(ctx, "get_clip", {})[0]["positions"]["hook"]
    assert positions["y_by_lines"]["1"] == 0.13 and positions["y_by_lines"]["2"] > 0.15


def test_highlight_words_contrast_with_active_word():
    from app.agent_tools import caption_highlight_warnings
    from app.auto_edit import build_auto_edit

    assert caption_highlight_warnings({"style": "hormozi", "highlightWords": [{"i": 1, "color": "#ffd600"}]})
    assert not caption_highlight_warnings({"style": "hormozi", "highlightWords": [{"i": 1, "color": "#22c55e"}]})
    assert not caption_highlight_warnings({"style": "box", "highlightWords": [{"i": 1, "color": "#ffd600"}]})
    words = [_w(t, i * 0.4, i * 0.4 + 0.3) for i, t in enumerate("это реально самый страшный день".split())]
    caps = next(l for l in build_auto_edit("x", words, [], 5.0)["layers"] if l["type"] == "captions")
    assert caps["props"]["highlightWords"] and all(h["color"] != "#ffd600" for h in caps["props"]["highlightWords"])
    assert not caption_highlight_warnings(caps["props"])


def test_preset_list_marks_default_and_tools_document_listen_and_step(tmp_path, monkeypatch):
    _require_ffmpeg()
    from app.agent_tools import get_tool

    client, store, _source, _plan, _a, _b = _setup(tmp_path, monkeypatch)
    store.create_ffmpeg_preset("Первый")
    store.create_ffmpeg_preset("Второй")
    presets = client.get("/api/ffmpeg-presets").json()
    assert presets and [p["default"] for p in presets].count(True) == 1 and presets[-1]["default"]
    assert "НЕ для исправления распознавания" in get_tool("listen").description
    assert "step" in get_tool("set_camera").description
