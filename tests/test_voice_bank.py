"""Субтитры по настоящей речи и пары «голос → человек»: клипы 905–918 рисовали текст раньше голоса и путали людей."""

from __future__ import annotations

import numpy as np

from app import quality_lint as Q
from app import voice_bank as V
from app.agent_tools import ToolContext, run_tool
from app.clip_subtitles import rebuild_words, save_lines
from app.motion_render import plan_caption_words
from app.settings import settings
from app.speech_activity import fit_unknown_words, line_sync, snap_lines_to_speech, spread_over_speech
from app.speech_onsets import _vad_or_loudness
from test_api_pipeline import _client


def _w(word, start, end, speaker=None):
    return {"word": word, "start": start, "end": end, **({"speaker": speaker} if speaker else {})}


def _clip(tmp_path, monkeypatch, words=None):
    _c, store = _client(tmp_path, monkeypatch)
    path = settings.source_dir / "s.mp4"
    path.write_bytes(b"src")
    source = store.create_source("upload", path, duration_sec=60, width=1280, height=720, status="ready")
    analysis = store.create_ai_analysis(source["id"], "mock", status="succeeded")
    seg = store.create_ai_segment(analysis["id"], {"start_sec": 10, "end_sec": 40, "title": "A"})
    plan = store.create_clip_plan(source["id"], analysis["id"], "План", segment_ids=[seg["id"]])
    if words:
        from app.ai.speakers import lines_from_words

        store.set_clip_plan_subtitles(plan["id"], {"words": words, "lines": lines_from_words(words)})
    return store, plan, ToolContext(store=store, clip_plan_id=plan["id"])


# ---------------------------------------------------------------- сверка с голосом


def _line(start, end, text="реплика"):
    return {"start": start, "end": end, "text": text}


def test_line_sync_finds_early_late_silent_and_lost():
    spans = [(2.0, 6.0), (10.0, 20.0), (30.0, 40.0)]
    words = [_w("а", 11.0, 13.2), _w("б", 36.0, 36.4)]
    found = {i["kind"]: i for i in line_sync(
        [_line(1.0, 3.0, "раньше"), _line(11.0, 14.0, "позже"), _line(22.0, 24.0, "тишина"), _line(36.0, 37.0, "потеря")], words, spans)}
    assert found["early"]["by"] == 1.0 and found["early"]["onset"] == 2.0
    assert found["late"]["by"] == 1.0 and found["late"]["onset"] == 10.0
    assert found["silent"]["text"] == "тишина"
    assert found["lost"]["by"] > 2.5


def test_line_sync_quiet_when_lines_sit_on_voice():
    spans = [(1.0, 5.0)]
    assert line_sync([_line(1.1, 2.0), _line(2.1, 4.0)], [_w("а", 1.1, 2.0), _w("б", 2.1, 4.0)], spans) == []
    # Опережение в пределах порога не шум: глаз этого не видит.
    assert line_sync([_line(0.8, 2.0)], [_w("а", 0.8, 2.0)], spans) == []
    # Опоздание сразу за предыдущим словом — это речь предыдущего слова, а не опоздание.
    assert line_sync([_line(1.0, 3.0), _line(3.0, 5.0)], [_w("а", 1.0, 3.0), _w("б", 3.0, 5.0)], spans) == []


def test_lint_sub_sync_reports_with_clip_time(tmp_path, monkeypatch):
    words = [_w("Рано", 1.0, 1.5), _w("начал", 1.5, 2.0)]
    store, plan, _ctx = _clip(tmp_path, monkeypatch, words)
    monkeypatch.setattr("app.speech_activity.plan_speech_spans", lambda *_a: [(2.0, 8.0)])
    assert "sub_sync" in Q.CHECKS and Q.normalize_checks(["sync"]) == ["sub_sync"]
    result = Q.lint_clip(store, plan["id"], checks=["sub_sync"])
    assert result["sub_sync"]["early"] == 1
    found = result["findings"][0]
    assert found["severity"] == "warn" and "раньше голоса на 1.00" in found["message"] and found["t"] == 1.0
    assert "snap_subtitles_to_speech" in found["fix"]
    assert Q.lint_clip(store, plan["id"], checks=["sub_sync"], media=False)["skipped"]["sub_sync"]


def test_lint_sub_sync_skips_cut_lines_and_missing_audio(tmp_path, monkeypatch):
    words = [_w("Рано", 1.0, 1.5), _w("начал", 1.5, 2.0)]
    store, plan, ctx = _clip(tmp_path, monkeypatch, words)
    # Настоящего звука в файле-заглушке нет — честно «не проверено», а не падение.
    assert "sub_sync" in Q.lint_clip(store, plan["id"], checks=["sub_sync"])["skipped"]
    monkeypatch.setattr("app.speech_activity.plan_speech_spans", lambda *_a: [(2.0, 8.0)])
    assert run_tool(ctx, "apply_cuts", {"cuts": [{"start": 0.0, "end": 3.0}], "min_island_sec": 0})[1]
    result = Q.lint_clip(store, plan["id"], checks=["sub_sync"])
    assert not result["findings"]


def test_snap_lines_moves_first_word_to_voice(tmp_path, monkeypatch):
    words = [_w("Рано", 1.0, 1.5), _w("начал", 1.5, 3.0)]
    store, plan, ctx = _clip(tmp_path, monkeypatch, words)
    monkeypatch.setattr("app.speech_activity.plan_speech_spans", lambda *_a: [(2.0, 8.0)])
    out = snap_lines_to_speech(store, plan["id"])
    assert out["moved"] == 1
    saved = store.get_clip_plan_subtitles(plan["id"])
    assert saved["words"][0]["start"] == 2.0 and saved["words"][0]["end"] > 2.0 and saved["lines"][0]["start"] == 2.0
    assert run_tool(ctx, "snap_subtitles_to_speech", {})[0]["moved"] == 0


def test_separate_voices_puts_gemini_only_line_on_voice(monkeypatch):
    # Слово расслышал только Gemini, и его время — по оценке модели: строка не должна опередить голос.
    from app.ai.speakers import separate_voices

    class Store:
        subs = {"lines": [], "words": [_w("ну", 3.0, 3.3), _w("что", 3.3, 3.6)]}

        def get_clip_plan_subtitles(self, _id):
            return self.subs

        def set_clip_plan_subtitles(self, _id, payload):
            self.subs = payload

    class Client:
        def generate_content(self, _model, _payload):
            answer = '{"speakers": {"A": "x", "B": "y"}, "utterances": [{"s": "B", "start": 1.0, "end": 1.8, "text": "привет"},' \
                     '{"s": "A", "start": 3.0, "end": 3.6, "text": "ну что"}]}'
            return {"candidates": [{"content": {"parts": [{"text": answer}]}}]}

    monkeypatch.setattr("app.ai.speakers._clip_audio", lambda *_a: b"mp3")
    monkeypatch.setattr("app.modules.require", lambda *_a, **_k: None)
    monkeypatch.setattr("app.speech_activity.plan_speech_spans", lambda *_a: [(1.5, 1.9), (3.0, 3.7)])
    store = Store()
    out = separate_voices(store, 1, Client())
    assert out["changed"] and store.subs["lines"][0]["start"] == 1.5 and store.subs["words"][0]["start"] == 1.5
    assert store.subs["speakers_key"]


def test_loud_game_uses_vad_quiet_recording_keeps_loudness(monkeypatch):
    frames = [True] * 100
    monkeypatch.setattr("app.audio_map.vad_segments", lambda *_a, **_k: ([(0.2, 0.4)], "silero"))
    got = _vad_or_loudness(b"\x00\x00" * 32000, frames)
    assert got[9] is False and got[12] is True
    assert _vad_or_loudness(b"\x00\x00" * 100, [True, False] * 50) == [True, False] * 50
    monkeypatch.setattr("app.audio_map.vad_segments", lambda *_a, **_k: ([(0.2, 0.4)], "energy"))
    assert _vad_or_loudness(b"\x00\x00" * 32000, frames) == frames


# ---------------------------------------------------------------- новый текст


def test_spread_over_speech_never_lands_in_silence():
    got = spread_over_speech([{"word": "один"}, {"word": "два"}, {"word": "три"}], [(1.0, 2.0), (5.0, 6.0)])
    for w in got:
        assert any(a <= w["start"] <= b for a, b in [(1.0, 2.0), (5.0, 6.0)])
    assert got[0]["start"] == 1.0 and got == sorted(got, key=lambda w: w["start"])


def test_known_words_keep_agent_time_and_misheard_take_whisper_time():
    old = [_w("Я", 1.0, 1.2), _w("вижу", 1.3, 1.7), _w("жирафов", 1.8, 2.4)]
    new = [_w("Я", 1.05, 1.2), _w("вижу", 1.3, 1.7), _w("пиратов", 1.9, 2.0)]
    words, notes = fit_unknown_words(new, old, lambda: [])
    assert words[0]["start"] == 1.05 and words[2]["start"] == 1.8 and words[2]["end"] == 2.4 and not notes


def test_hand_spread_in_silence_moves_to_speech():
    spans = [(5.0, 7.0)]
    new = [_w("Меня", 3.0, 3.6), _w("убили", 3.6, 4.2), _w("совсем", 4.2, 5.2)]
    words, notes = fit_unknown_words(new, [], lambda: spans)
    assert not notes and words[0]["start"] >= 5.0 and words[-1]["end"] <= 6.2


def test_new_text_on_voice_stays_and_no_voice_warns():
    assert fit_unknown_words([_w("Да", 5.0, 6.0)], [], lambda: [(5.0, 6.5)]) == ([_w("Да", 5.0, 6.0)], [])
    words, notes = fit_unknown_words([_w("Да", 5.0, 5.2)], [], lambda: [(50.0, 60.0)])
    assert words[0]["start"] == 5.0 and "голоса на этом месте нет" in notes[0]

    def broken():
        raise ValueError("нет исходника")

    words, notes = fit_unknown_words([_w("Да", 5.0, 5.2)], [], broken)
    assert words[0]["start"] == 5.0 and "не проверено по звуку" in notes[0]


def test_set_subtitle_words_aligns_and_can_opt_out(tmp_path, monkeypatch):
    store, plan, ctx = _clip(tmp_path, monkeypatch)
    monkeypatch.setattr("app.speech_activity.plan_speech_spans", lambda *_a: [(5.0, 7.0)])
    hand = [_w("Меня", 3.0, 3.6), _w("убили", 3.6, 4.2)]
    out, ok = run_tool(ctx, "set_subtitle_words", {"words": hand})
    assert ok and store.get_clip_plan_subtitles(plan["id"])["words"][0]["start"] >= 5.0
    out, ok = run_tool(ctx, "set_subtitle_words", {"words": hand, "align": False})
    assert ok and store.get_clip_plan_subtitles(plan["id"])["words"][0]["start"] == 3.0
    monkeypatch.setattr("app.speech_activity.plan_speech_spans", lambda *_a: [(50.0, 51.0)])
    out, ok = run_tool(ctx, "set_subtitle_words", {"words": [_w("Новый", 30.0, 30.6)]})
    assert ok and any("голоса на этом месте нет" in w for w in out["warnings"])


def test_rebuild_keeps_time_and_voice_of_retyped_line():
    old = [_w("мне", 1.0, 1.3, "B"), _w("нужна", 1.4, 1.9, "B"), _w("демка", 2.0, 2.6, "B")]
    # Заменили слово — время то же, голос тот же.
    words = rebuild_words([{"start": 1.0, "end": 2.6, "text": "мне нужна дымка"}], old)
    assert [w["start"] for w in words] == [1.0, 1.4, 2.0] and {w.get("speaker") for w in words} == {"B"}
    # Другое число слов: совпавшие держат время, новое — между соседями.
    words = rebuild_words([{"start": 1.0, "end": 2.6, "text": "мне очень нужна демка"}], old)
    assert words[0]["start"] == 1.0 and words[2]["start"] == 1.4 and 1.3 <= words[1]["start"] < 1.4
    assert all(w.get("speaker") == "B" for w in words)
    # Ни одного общего слова — в пределах речи строки, а не всей её длины.
    words = rebuild_words([{"start": 0.0, "end": 5.0, "text": "совсем другой текст тут"}], old)
    assert words[0]["start"] == 1.0 and words[-1]["end"] <= 2.61 and words[0].get("speaker") == "B"
    # Новая строка без слов — раскладка как раньше, чтобы караоке не ломалось.
    words = rebuild_words([{"start": 10.0, "end": 12.0, "text": "новая строка"}], old)
    assert words[0]["start"] == 10.0


def test_save_lines_fits_blind_line_or_warns(tmp_path, monkeypatch):
    store, plan, _ctx = _clip(tmp_path, monkeypatch)
    monkeypatch.setattr("app.speech_activity.plan_speech_spans", lambda *_a: [(11.0, 12.0)])
    saved = save_lines(store, plan["id"], [{"start": 10.0, "end": 13.0, "text": "новая строка"}])
    assert saved["words"][0]["start"] >= 11.0 and "align_warnings" not in saved
    monkeypatch.setattr("app.speech_activity.plan_speech_spans", lambda *_a: [])
    saved = save_lines(store, plan["id"], [{"start": 20.0, "end": 23.0, "text": "другая строка"}])
    assert "голоса на этом месте нет" in saved["align_warnings"][0]
    assert "align_warnings" not in store.get_clip_plan_subtitles(plan["id"])


# ---------------------------------------------------------------- банк голосов


def _vec(*values):
    v = np.zeros(8, dtype=np.float32)
    v[: len(values)] = values
    return v / np.linalg.norm(v)


MURA, GLEB, TTS = _vec(1.0), _vec(0.0, 1.0), _vec(0.0, 0.0, 1.0)


def _fake_audio(monkeypatch, voices, seconds=40.0):
    """Звук «клипа»: номер голоса зашит в значении сэмплов, отпечаток его читает."""
    pcm = np.zeros(int(seconds * V.RATE), dtype=np.float32)
    for a, b, vec in voices:
        pcm[int(a * V.RATE):int(b * V.RATE)] = float(np.argmax(vec) + 1) / 10
    monkeypatch.setattr("app.speech_activity.plan_pcm", lambda *_a: pcm)
    monkeypatch.setattr("app.audio_map.vad_segments", lambda x, *_a, **_k: ([(0.0, len(x) / V.RATE)], "silero"))
    table = {1: MURA, 2: GLEB, 3: TTS}

    def embed(x):
        counts = {k: int(np.sum(np.isclose(x, k / 10))) for k in table}
        best = max(counts, key=counts.get)
        return table[best]

    monkeypatch.setattr(V, "embedder", lambda: embed)
    return pcm


def _dialog():
    return ([_w("Да", 1.0, 3.0, "A"), _w("вот", 3.0, 6.0, "A"), _w("и", 6.0, 10.0, "A"), _w("Глеб", 12.0, 16.0, "B"), _w("тут", 16.0, 20.0, "B")],
            [(0.0, 11.0, MURA), (11.0, 21.0, GLEB)])


def test_register_needs_speech_and_merges_samples(tmp_path, monkeypatch):
    words, voices = _dialog()
    store, plan, ctx = _clip(tmp_path, monkeypatch, words)
    _fake_audio(monkeypatch, voices)
    out, ok = run_tool(ctx, "register_voice", {"name": "Мура", "speaker": "A"})
    assert ok and out["name"] == "Мура" and out["seconds"] == 9.0 and "warning" in out
    out, ok = run_tool(ctx, "register_voice", {"name": "Мура", "speaker": "A"})
    assert ok and out["seconds"] == 18.0 and len(out["samples"]) == 2 and "warning" not in out
    assert len(V.list_voices(store)) == 1
    out, ok = run_tool(ctx, "register_voice", {"name": "Мало", "start": 0.0, "end": 2.0})
    assert not ok and "нужно хотя бы" in out["error"]
    out, ok = run_tool(ctx, "register_voice", {"name": "Икс", "speaker": "D"})
    assert not ok and "нет слов" in out["error"]
    out, ok = run_tool(ctx, "register_voice", {"name": "Без отрезка"})
    assert not ok


def test_identify_names_only_when_sure_and_labels_subtitles(tmp_path, monkeypatch):
    words, voices = _dialog()
    store, plan, ctx = _clip(tmp_path, monkeypatch, words)
    _fake_audio(monkeypatch, voices)
    out, ok = run_tool(ctx, "identify_voices", {})
    assert not ok and "пусто" in out["error"]
    assert run_tool(ctx, "register_voice", {"name": "Мура", "speaker": "A"})[1]
    assert run_tool(ctx, "register_voice", {"name": "Глеб", "speaker": "B"})[1]
    out, ok = run_tool(ctx, "identify_voices", {})
    assert ok and out["names"] == {"A": "Мура", "B": "Глеб"} and not out["unsure"] and "subtitles" not in out
    saved = store.get_clip_plan_subtitles(plan["id"])
    assert saved["speakers"]["B"] == "Глеб" and saved["words"][3]["word"] == "Глеб"
    # В готовых субтитрах реплика гостя подписана именем, главный голос — нет; хранимый текст чист.
    shown = plan_caption_words(store, store.get_clip_plan(plan["id"]))
    assert [w["w"] for w in shown][:1] == ["Да"] and "Глеб: Глеб" in [w["w"] for w in shown] and "тут" in [w["w"] for w in shown]
    run_tool(ctx, "identify_voices", {"labels": False})
    assert "Глеб: Глеб" not in [w["w"] for w in plan_caption_words(store, store.get_clip_plan(plan["id"]))]


def test_identify_leaves_unknown_voice_unnamed(tmp_path, monkeypatch):
    words, voices = _dialog()
    store, plan, ctx = _clip(tmp_path, monkeypatch, words)
    _fake_audio(monkeypatch, voices)
    assert run_tool(ctx, "register_voice", {"name": "Мура", "speaker": "A"})[1]
    out, ok = run_tool(ctx, "identify_voices", {})
    assert ok and out["names"] == {"A": "Мура"}
    assert [u["voice"] for u in out["unsure"]] == ["B"] and out["unsure"][0]["closest"] == "Мура"
    assert "B" not in store.get_clip_plan_subtitles(plan["id"])["voice_names"]


def test_conflict_is_reported_and_reassign_moves_it(tmp_path, monkeypatch):
    words = [_w("Да", 1.0, 5.0, "A"), _w("вот", 5.0, 10.0, "A"), _w("Чужая", 12.0, 16.0, "A"), _w("Глеб", 20.0, 26.0, "B"), _w("тут", 26.0, 31.0, "B")]
    store, plan, ctx = _clip(tmp_path, monkeypatch, words)
    _fake_audio(monkeypatch, [(0.0, 11.0, MURA), (11.0, 17.0, GLEB), (19.0, 32.0, GLEB)])
    assert run_tool(ctx, "register_voice", {"name": "Глеб", "speaker": "B"})[1]
    assert run_tool(ctx, "register_voice", {"name": "Мура", "start": 0.0, "end": 10.0})[1]
    out, ok = run_tool(ctx, "identify_voices", {})
    assert ok and out["names"]["A"] == "Мура" and out["names"]["B"] == "Глеб"
    assert out["conflicts"] and out["conflicts"][0]["sounds_like"] == "Глеб" and out["moved"] == 0
    assert store.get_clip_plan_subtitles(plan["id"])["words"][2]["speaker"] == "A"
    out, ok = run_tool(ctx, "identify_voices", {"reassign": True})
    assert ok and out["moved"] == 1
    assert store.get_clip_plan_subtitles(plan["id"])["words"][2]["speaker"] == "B"


def test_bank_scope_and_delete(tmp_path, monkeypatch):
    words, voices = _dialog()
    store, plan, ctx = _clip(tmp_path, monkeypatch, words)
    _fake_audio(monkeypatch, voices)
    run_tool(ctx, "register_voice", {"name": "Мура", "speaker": "A"})
    listed, ok = run_tool(ctx, "list_voices", {})
    assert ok and [v["name"] for v in listed["voices"]] == ["Мура"] and "embedding" not in listed["voices"][0]
    assert run_tool(ctx, "delete_voice", {"voice_id": listed["voices"][0]["id"]})[1]
    assert not run_tool(ctx, "delete_voice", {"voice_id": 999})[1]
    assert run_tool(ctx, "list_voices", {})[0]["voices"] == []
    # Голос другого канала этому клипу не виден.
    other = store.db.execute(
        "INSERT INTO voice_bank (project_id, name, embedding_json) VALUES (?, ?, ?)", (12345, "Чужой", "[1.0]"))
    assert other.lastrowid and V.list_voices(store, None) == []
    assert [v["name"] for v in V.list_voices(store, 12345)] == ["Чужой"]


def test_threshold_and_best_match():
    assert V.min_score(2.0) == 0.45 and round(V.min_score(30.0), 3) == 0.6
    rows = [{"id": 1, "name": "Мура", "embedding": MURA.tolist()}, {"id": 2, "name": "Глеб", "embedding": GLEB.tolist()}]
    assert V.best_match(MURA, rows, 10)["sure"] is True
    near = _vec(1.0, 1.0)  # посередине: зазора нет — не угадываем
    got = V.best_match(near, rows, 10)
    assert got["sure"] is False and got["margin"] < V.MIN_MARGIN
    assert V.best_match(MURA, [], 10)["name"] is None


def test_fbank_shape_and_short_input():
    assert V.fbank(np.zeros(100, dtype=np.float32)).shape == (0, 80)
    tone = np.sin(np.arange(16000) / 5).astype(np.float32)
    feat = V.fbank(tone)
    assert feat.shape == (98, 80) and abs(float(feat.mean())) < 1e-3


def test_with_labels_only_on_turn_change():
    payload = {"speaker_labels": True, "voice_names": {"B": {"name": "Глеб"}},
               "words": [_w("а", 0, 1, "A"), _w("б", 1, 2, "B"), _w("в", 2, 3, "B"), _w("г", 3, 4, "A"), _w("д", 4, 5, "B")]}
    assert [w["word"] for w in V.with_speaker_labels(payload)["words"]] == ["а", "Глеб: б", "в", "г", "Глеб: д"]
    assert V.with_speaker_labels({**payload, "speaker_labels": False}) == {**payload, "speaker_labels": False}
