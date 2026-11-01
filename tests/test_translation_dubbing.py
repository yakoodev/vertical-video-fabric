"""Перевод субтитров на русский и озвучка RU: всё без сети и без денег (Gemini и TTS — подделки)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from app import dubbing, translation
from app.clip_settings import normalize_render_settings
from app.subtitles.ass import write_ass_subtitles
from app.subtitles.contracts import SubtitleResult, SubtitleSegment, SubtitleWord

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _words(text: str, start: float, step: float = 0.4, speaker: str | None = None):
    out = []
    for i, t in enumerate(text.split()):
        w = {"word": t, "start": round(start + i * step, 2), "end": round(start + i * step + 0.3, 2)}
        if speaker:
            w["speaker"] = speaker
        out.append(w)
    return out


ENGLISH = {
    "lines": [{"start": 0.0, "end": 1.5, "text": "What is going on"},
              {"start": 2.0, "end": 3.1, "text": "No way bro"}],
    "words": _words("What is going on", 0.0) + _words("No way bro", 2.0),
    "language": "en", "provider": "whisper", "model": "large-v3-turbo", "duration": 4.0,
}


class FakeStore:
    def __init__(self, subs=None, render=None, segments=None):
        self.subs = subs
        self.render = normalize_render_settings(render or {})
        self.segments = segments or [{"start_sec": 10.0, "end_sec": 14.0}]
        self.assets: list[dict] = []

    def get_clip_plan_subtitles(self, _pid):
        return self.subs

    def set_clip_plan_subtitles(self, _pid, payload):
        self.subs = payload

    def get_clip_plan(self, pid, include_segments=True):
        return {"id": pid, "source_id": 1, "segments": self.segments, "render_settings": self.render}

    def set_clip_plan_render_settings(self, _pid, render):
        self.render = normalize_render_settings(render)  # как настоящий store: мусор не сохраняется
        return self.render

    def create_montage_asset(self, **kw):
        asset = {"id": 900 + len(self.assets), **kw}
        self.assets.append(asset)
        return asset


class FakeGemini:
    def __init__(self, answer):
        self.answer, self.calls, self.prompts = answer, 0, []

    def generate_content(self, model, payload):
        self.calls += 1
        self.prompts.append(payload["contents"][0]["parts"][0]["text"])
        return {"candidates": [{"content": {"parts": [{"text": self.answer}]}}]}


RU_ANSWER = '{"items": [{"i": 0, "ru": "Что тут происходит"}, {"i": 1, "ru": "Да ладно, бро"}]}'


# --------------------------------------------------------------------------- перевод


def test_spread_words_stays_inside_phrase_window():
    words = translation.spread_words("Что тут вообще происходит", 2.0, 3.0, "B")
    assert [w["word"] for w in words] == ["Что", "тут", "вообще", "происходит"]
    assert words[0]["start"] == 2.0 and words[-1]["end"] == 3.0
    assert all(2.0 <= w["start"] < w["end"] <= 3.0 for w in words)
    assert all(a["end"] <= b["start"] + 1e-6 for a, b in zip(words, words[1:]))
    # Длинное слово получает больше времени, чем короткое.
    assert (words[3]["end"] - words[3]["start"]) > (words[1]["end"] - words[1]["start"])
    assert {w["speaker"] for w in words} == {"B"}


def test_translate_keeps_phrase_timing_and_stores_original():
    store = FakeStore(dict(ENGLISH))
    client = FakeGemini(RU_ANSWER)
    out = translation.translate_subtitles(store, 1, mode="ru", client=client)
    assert out["ok"] and out["mode"] == "ru" and out["source_language"] == "en" and client.calls == 1
    assert "Язык оригинала: en" in client.prompts[0]
    subs = store.subs
    assert subs["language"] == "ru"
    assert [(l["start"], l["end"], l["text"]) for l in subs["lines"]] == [
        (0.0, 1.5, "Что тут происходит"), (2.0, 3.1, "Да ладно, бро")]
    # Русские слова — внутри окна своей фразы, без растягивания.
    assert all(0.0 <= w["start"] and w["end"] <= 1.5 for w in subs["words"][:3])
    assert subs["translation"]["original"]["lines"][0]["text"] == "What is going on"
    assert subs["translation"]["mode"] == "ru"


def test_mode_switch_and_off_do_not_call_model_again():
    store = FakeStore(dict(ENGLISH))
    client = FakeGemini(RU_ANSWER)
    translation.translate_subtitles(store, 1, mode="ru", client=client)
    translation.translate_subtitles(store, 1, mode="both", client=client)
    assert client.calls == 1 and store.subs["translation"]["mode"] == "both"
    out = translation.translate_subtitles(store, 1, mode="off", client=client)
    assert out["changed"] and client.calls == 1
    assert store.subs["lines"][0]["text"] == "What is going on" and store.subs["language"] == "en"
    assert "translation" not in store.subs


def test_russian_clip_is_refused_unless_forced():
    store = FakeStore({**ENGLISH, "language": "ru"})
    with pytest.raises(ValueError, match="уже русская"):
        translation.translate_subtitles(store, 1, mode="ru", client=FakeGemini(RU_ANSWER))
    out = translation.translate_subtitles(store, 1, mode="ru", client=FakeGemini(RU_ANSWER), force=True)
    assert out["ok"]


def test_agent_translations_skip_gemini(monkeypatch):
    monkeypatch.setattr(translation.settings, "gemini_api_key", "")
    store = FakeStore(dict(ENGLISH))
    out = translation.translate_subtitles(store, 1, mode="ru", translations=["Что происходит", "Да ну"])
    assert out["provider"] == "agent" and store.subs["lines"][1]["text"] == "Да ну"
    with pytest.raises(RuntimeError, match="Gemini не настроен"):
        translation.translate_subtitles(FakeStore(dict(ENGLISH)), 1, mode="ru")


def test_parse_translations_drops_garbage():
    phrases = [{"i": 0}, {"i": 1}]
    assert translation.parse_translations('{"items": [{"i": 0, "ru": "да"}, {"i": 7, "ru": "x"}, {"i": "z"}]}',
                                          phrases) == {0: "да"}
    assert translation.parse_translations("не json", phrases) == {}


def test_both_mode_adds_orig_layer_and_comp_words_after_main():
    render = {"layers": [{"id": "auto_caps", "type": "captions", "start": 0, "end": 4, "y": 0.7,
                          "props": {"style": "hormozi"}}]}
    store = FakeStore(dict(ENGLISH), render=render)
    out = translation.translate_subtitles(store, 1, mode="both", client=FakeGemini(RU_ANSWER))
    assert "оригинал" in out["layer"]
    orig = next(l for l in store.render["layers"] if l["id"] == translation.ORIG_LAYER_ID)
    assert orig["props"]["speakers"] == ["orig"] and orig["y"] < 0.7
    from app.motion_render import plan_caption_words

    words = plan_caption_words(store, store.get_clip_plan(1))
    main = [w for w in words if w.get("s") != "orig"]
    tail = words[len(main):]
    assert [w["w"] for w in tail] == "What is going on No way bro".split()
    assert all(w["s"] == "orig" for w in tail)
    # Режим ru — слой оригинала снимается, слов оригинала нет.
    translation.translate_subtitles(store, 1, mode="ru")
    assert not any(l["id"] == translation.ORIG_LAYER_ID for l in store.render["layers"])
    assert not any(w.get("s") == "orig" for w in plan_caption_words(store, store.get_clip_plan(1)))


def test_remap_to_pieces_moves_original_too():
    from app.clip_subtitles import remap_to_pieces

    store = FakeStore(dict(ENGLISH))
    translation.translate_subtitles(store, 1, mode="both", client=FakeGemini(RU_ANSWER))
    # Кусок подрезали на 1 с слева: всё уезжает на секунду раньше, «What is going» выпадает.
    moved = remap_to_pieces(store.subs, [[10.0, 14.0]], [[11.0, 14.0]])
    orig = moved["translation"]["original"]
    assert orig["words"][0]["word"] == "on" and orig["words"][0]["start"] == pytest.approx(0.2, abs=0.01)
    assert orig["lines"][0]["text"] == "on"


def test_ass_secondary_lines_render_original_style(tmp_path):
    result = SubtitleResult(
        text="Да ну", language="ru", duration=3.0,
        segments=[SubtitleSegment(0.0, 1.0, "Да ну")],
        words=[SubtitleWord("Да", 0.0, 0.4), SubtitleWord("ну", 0.4, 1.0)],
        response={"secondary_lines": [{"start": 0.0, "end": 1.0, "text": "No way"}]},
        usage={"subtitleTimingOffsetSec": 0.1},
    )
    path = write_ass_subtitles(result, {"font_size": 80, "margin_v": 160}, tmp_path / "s.ass")
    text = path.read_text(encoding="utf-8")
    assert "Style: Original," in text and ",44," in text.split("Style: Original,")[1].split("\n")[0]
    assert "Dialogue: 1,0:00:00.10,0:00:01.10,Original,,0,0,0,,No way" in text
    # Без перевода — заголовок прежний.
    plain = SubtitleResult(text="Да", language="ru", duration=1.0, words=[SubtitleWord("Да", 0.0, 0.4)])
    assert "Original" not in write_ass_subtitles(plain, {}, tmp_path / "p.ass").read_text(encoding="utf-8")


def test_secondary_lines_only_in_both_mode():
    store = FakeStore(dict(ENGLISH))
    translation.translate_subtitles(store, 1, mode="ru", client=FakeGemini(RU_ANSWER))
    assert translation.secondary_lines(store.subs, store.segments, None) == []
    translation.translate_subtitles(store, 1, mode="both")
    lines = translation.secondary_lines(store.subs, store.segments, None)
    assert [l["text"] for l in lines] == ["What is going on", "No way bro"]


# --------------------------------------------------------------------------- озвучка: деньги


def _translated_store(render=None):
    store = FakeStore(dict(ENGLISH), render=render)
    translation.translate_subtitles(store, 1, mode="ru", client=FakeGemini(RU_ANSWER))
    return store


class ExplodingTTS:
    """Платный провайдер, который нельзя вызывать: тест упадёт, если дошло до синтеза."""

    name, paid, model, voice = "polza", True, "elevenlabs/eleven-v4", "brian"

    def price_rub_per_char(self):
        return dubbing.PRICES_RUB_PER_CHAR[self.model]

    def synthesize(self, text, out_path):
        raise AssertionError("платный синтез без разрешения")


def test_estimate_only_without_confirm_never_synthesizes(monkeypatch):
    monkeypatch.setattr(dubbing.settings, "dubbing_enabled", True)
    store = _translated_store()
    out = dubbing.dub_clip(store, 1, tts=ExplodingTTS())
    assert out["status"] == "estimate"
    est = out["estimate"]
    chars = len("Что тут происходит") + len("Да ладно, бро")
    assert est["chars"] == chars
    # eleven-v4 — по замеру настоящего прогона: 0.4193 ₽ за 90 символов.
    assert est["rub"] == pytest.approx(round(chars * 0.4193 / 90, 2))
    assert est["price_rub_per_char"] > 2278.816 / 1e6  # не дешевле прайса /models
    assert est["budget_rub"] == 200 and not est["over_budget"] and est["paid"]


def test_paid_dub_refused_when_disabled_or_over_budget(monkeypatch):
    store = _translated_store()
    monkeypatch.setattr(dubbing.settings, "dubbing_enabled", False)
    with pytest.raises(dubbing.DubError, match="выключена"):
        dubbing.dub_clip(store, 1, confirm=True, tts=ExplodingTTS())
    monkeypatch.setattr(dubbing.settings, "dubbing_enabled", True)
    with pytest.raises(dubbing.DubError, match="больше бюджета"):
        dubbing.dub_clip(store, 1, confirm=True, budget_rub=0.0, tts=ExplodingTTS())


def test_budget_cannot_exceed_hard_cap(monkeypatch):
    monkeypatch.setattr(dubbing.settings, "dubbing_max_budget_rub", 200.0)
    assert dubbing.budget_cap(None) == 200.0
    assert dubbing.budget_cap(10_000) == 200.0
    assert dubbing.budget_cap(50) == 50.0
    assert dubbing.budget_cap("мусор") == 200.0


def test_untranslated_clip_is_refused():
    with pytest.raises(dubbing.DubError, match="не переведён"):
        dubbing.dub_clip(FakeStore(dict(ENGLISH)), 1)


def test_polza_tts_parses_base64_response(monkeypatch, tmp_path):
    import base64

    import httpx

    sent = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        sent.update(url=url, json=json, auth=headers["Authorization"])
        return httpx.Response(200, json={"audio": base64.b64encode(b"ID3fake").decode(), "contentType": "audio/mpeg",
                                         "usage": {"characters": 5, "cost_rub": 0.0114}},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    tts = dubbing.PolzaTTS(model="elevenlabs/eleven-v4", voice="brian", api_key="k", base_url="https://x/api/v1")
    speech = tts.synthesize("Привет", tmp_path / "a.mp3")
    assert sent["url"] == "https://x/api/v1/audio/speech" and sent["json"]["input"] == "Привет"
    assert sent["json"]["voice"] == "brian" and sent["auth"] == "Bearer k"
    assert speech.path.read_bytes() == b"ID3fake" and speech.cost_rub == pytest.approx(0.0114)


def test_chat_marks_paid_dub_dangerous_but_not_estimate():
    from app.chat_tools import is_dangerous

    assert is_dangerous(None, "dub_clip", {"confirm": True}, None)
    assert not is_dangerous(None, "dub_clip", {}, None)
    assert not is_dangerous(None, "dub_clip", {"confirm": True, "provider": "none"}, None)


def test_tools_registered():
    from app import agent_tools

    names = {t.name for t in agent_tools.list_tools()}
    assert {"translate_subtitles", "dub_clip"} <= names


# --------------------------------------------------------------------------- озвучка: подгонка


def test_fit_plan_speeds_up_trims_and_pads():
    assert dubbing.fit_plan(1.1, 1.0) == {"tempo": 1.1, "action": "tempo", "overflow_sec": 0.0}
    trim = dubbing.fit_plan(2.0, 1.0)
    assert trim["tempo"] == dubbing.TEMPO_MAX and trim["action"] == "trim" and trim["overflow_sec"] == pytest.approx(0.6)
    assert dubbing.fit_plan(0.5, 1.0)["action"] == "pad"
    assert dubbing.fit_plan(0.9, 1.0, stretch=True) == {"tempo": 0.9, "action": "tempo", "overflow_sec": 0.0}
    assert dubbing.fit_plan(0.5, 1.0, stretch=True)["action"] == "pad"  # ниже ×0.8 не тянем


def test_phrase_windows_end_before_next_phrase():
    phrases = [{"start": 0.0, "end": 1.0}, {"start": 1.2, "end": 2.0}, {"start": 5.0, "end": 6.0}]
    assert dubbing.phrase_windows(phrases, 6.3) == [1.15, 2.5, 6.3]


def test_groups_and_layers_survive_render_normalization():
    class Clock:
        def plain_to_clip(self, t):
            return t

    report = [{"start": 0.0, "window_end": 1.15}, {"start": 1.2, "window_end": 2.5},
              {"start": 5.0, "window_end": 6.3}]
    groups = dubbing.dub_groups(report)
    assert groups == [(0.0, 2.5), (5.0, 6.3)]
    layers = dubbing.dub_layers(groups, 901, Clock())
    clean = normalize_render_settings({"layers": layers})["layers"]
    assert [l["id"] for l in clean] == ["dub_ru_1", "dub_ru_2"]
    assert clean[1]["props"]["offset"] == 5.0 and clean[1]["props"]["duck"] is True
    assert clean[1]["props"]["mode"] == "track" and str(clean[1]["props"]["asset_id"]) == "901"


class SineTTS:
    """Бесплатная «речь» нужной длины: синус ffmpeg (длина — по таблице)."""

    name, paid, model, voice = "sine", False, "sine", "-"

    def __init__(self, lengths):
        self.lengths, self.calls = list(lengths), 0

    def price_rub_per_char(self):
        return 0.0

    def synthesize(self, text, out_path):
        length = self.lengths[self.calls]
        self.calls += 1
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                        f"sine=frequency=330:sample_rate=24000:duration={length}", str(out_path)], check=True)
        return dubbing.Speech(out_path, 0.01)


@pytest.mark.skipif(not HAS_FFMPEG, reason="нужен ffmpeg")
def test_dub_track_fits_phrases_and_reports_overflow(tmp_path):
    phrases = [{"i": 0, "start": 0.0, "end": 1.0, "text": "раз"},
               {"i": 1, "start": 2.0, "end": 2.6, "text": "два"}]
    # Первая фраза 1.2 с в окне 1.5 (до 1.0+0.5) — влезает как есть; вторая 1.6 с в окне 1.1 —
    # ×1.25 даёт 1.28 с, не влезает: обрезка и overflow.
    tts = SineTTS([1.2, 1.6])
    out = tmp_path / "dub.wav"
    work = tmp_path / "work"
    work.mkdir()
    report, spent = dubbing.build_dub_track(phrases, tts, 4.0, out, work)
    assert spent == pytest.approx(0.02)
    assert report[0]["action"] == "pad" and report[1]["action"] == "trim"
    assert report[1]["overflow_sec"] == pytest.approx(1.6 / 1.25 - 1.1, abs=0.01)
    assert dubbing.probe_duration(out) == pytest.approx(4.0, abs=0.05)
    # Звук на месте фраз и тишина между ними (2.65–… после обрезки второй фразы).
    from app.speech_onsets import wav_pcm
    from app.source_loudness import pcm_levels

    mono = tmp_path / "mono.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(out), "-ac", "1", "-ar", "16000", str(mono)],
                   check=True)
    levels = pcm_levels(wav_pcm(mono), 16000, 0.1)
    assert levels[5] > -40  # 0.5 с — первая фраза
    assert levels[17] < -60  # 1.7 с — пауза
    assert levels[25] > -40  # 2.5 с — вторая фраза
    assert levels[35] < -60  # 3.5 с — после окна второй фразы тишина


@pytest.mark.skipif(not HAS_FFMPEG, reason="нужен ffmpeg")
def test_dry_run_dub_clip_end_to_end(monkeypatch, tmp_path):
    monkeypatch.setattr(dubbing.settings, "dubbing_enabled", False)  # бесплатному провайдеру флаг не нужен
    monkeypatch.setattr("app.montage_assets.assets_dir", lambda: tmp_path)
    store = _translated_store()
    out = dubbing.dub_clip(store, 1, provider="none", confirm=True)
    assert out["status"] == "done" and out["estimate"]["rub"] == 0.0
    layers = [l for l in store.render["layers"] if l["id"].startswith("dub_ru")]
    assert len(layers) == 1 and layers[0]["props"]["duck"]  # пауза 0.5 с между фразами — одна группа
    asset = store.assets[0]
    assert Path(asset["file_path"]).exists() and asset["kind"] == "audio"
    assert store.subs["translation"]["dub"]["asset_id"] == asset["id"]
    # Снять озвучку — слои уходят, перевод остаётся.
    dubbing.dub_clip(store, 1, remove=True)
    assert not any(l["id"].startswith("dub_ru") for l in store.render["layers"])
    assert "dub" not in store.subs["translation"] and store.subs["language"] == "ru"


# --------------------------------------------------------------------------- настоящий store + рендер


@pytest.mark.skipif(not HAS_FFMPEG, reason="нужен ffmpeg")
def test_real_store_translate_dub_and_render_args(tmp_path, monkeypatch):
    """Инструменты MCP на настоящем store: перевод агентом, оценка, сухой прогон озвучки; рендер
    (ffmpeg подменён) прожигает оригинал стилем Original и подмешивает дорожку озвучки с duck."""
    from app.agent_tools import ToolContext, run_tool
    from app.settings import settings
    from test_cuts import _mock_render

    store, service, plan, preset, calls = _mock_render(tmp_path, monkeypatch, [(0, 10)])
    store.set_clip_plan_subtitles(plan["id"], {**ENGLISH, "duration": 10.0})
    ctx = ToolContext(store=store, clip_plan_id=plan["id"])

    out, ok = run_tool(ctx, "translate_subtitles",
                       {"mode": "both", "translations": ["Что тут происходит", "Да ну, бро"]})
    assert ok, out
    assert out["provider"] == "agent" and "note" in out  # слоя captions нет — подсказка про subs_on
    est, ok = run_tool(ctx, "dub_clip", {})
    assert ok and est["status"] == "estimate" and est["estimate"]["paid"] and not est["estimate"]["enabled"]
    refused, ok = run_tool(ctx, "dub_clip", {"confirm": True})
    assert not ok and "выключена" in refused["error"]
    done, ok = run_tool(ctx, "dub_clip", {"provider": "none", "confirm": True})
    assert ok, done
    asset = store.get_montage_asset(done["dub"]["asset_id"])
    render = store.get_clip_plan(plan["id"])["render_settings"]
    dub_layers = [l for l in render["layers"] if l["id"].startswith("dub_ru")]
    assert dub_layers and all(l["props"]["duck"] for l in dub_layers)

    profile = store.create_subtitle_profile("Тест", provider="mock")
    store.set_clip_plan_render_settings(plan["id"], {**render, "subs_on": True, "sub_id": profile["id"]})
    service.render_clip_plan(plan["id"], ffmpeg_preset_id=preset["id"], subtitle_profile_id=profile["id"])
    flat = [" ".join(map(str, c)) for c in calls]
    mix = next(c for c in flat if asset["file_path"] in c)
    assert "volume=enable='between(t," in mix and "volume=0.3" in mix
    texts = [p.read_text("utf-8") for p in settings.subtitle_dir.glob("*.ass")]
    ass = next(t for t in texts if "Style: Original," in t)
    # Подменённый probe говорит «клип 2 с» — в него влезает только первая фраза.
    assert "Dialogue: 1,0:00:00.00,0:00:01.50,Original,,0,0,0,,What is going on" in ass
    assert "происходит" in ass and "No way bro" not in ass
