"""Бесплатная озвучка Gemini TTS: запрос, разбор PCM/WAV, лимиты 429, без денежных ворот,
проба голоса (tts_preview), голос персонажа ИИ-видео. Сети нет — ответы Gemini подделаны."""

from __future__ import annotations

import base64
import io
import json
import math
import shutil
import struct
import wave

import httpx
import pytest

from app import dubbing
from app.settings import settings

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="нужен ffmpeg")


def _pcm(seconds: float = 0.6, rate: int = 24000) -> bytes:
    n = int(seconds * rate)
    return b"".join(struct.pack("<h", int(9000 * math.sin(2 * math.pi * 440 * i / rate))) for i in range(n))


def _wav(seconds: float = 0.6, rate: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(_pcm(seconds, rate))
    return buf.getvalue()


def _audio_answer(data: bytes, mime: str = "audio/L16;codec=pcm;rate=24000") -> dict:
    return {"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": mime,
                                                                  "data": base64.b64encode(data).decode()}}]},
                            "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 15, "totalTokenCount": 22}}


def _resp(status: int, payload: dict, headers: dict | None = None) -> httpx.Response:
    return httpx.Response(status, json=payload, headers=headers or {},
                          request=httpx.Request("POST", "https://g/v1beta/models/x:generateContent"))


class FakeGoogle:
    """Подмена ``app.ai.gemini._request_once``: очередь ответов + записанные запросы."""

    def __init__(self, monkeypatch, *responses):
        self.responses, self.calls = list(responses), []
        monkeypatch.setattr("app.ai.gemini._request_once", self)

    def __call__(self, method, url, kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(item, Exception):
            raise item
        return item


def _tts(**kw) -> dubbing.GeminiTTS:
    sleeps: list[float] = []
    tts = dubbing.GeminiTTS(api_key="k", base_url="https://g/v1beta", sleep=sleeps.append, **kw)
    tts.sleeps = sleeps  # type: ignore[attr-defined]
    return tts


# --------------------------------------------------------------------------- запрос и ответ


@needs_ffmpeg
def test_request_shape_and_pcm_to_mp3(monkeypatch, tmp_path):
    google = FakeGoogle(monkeypatch, _resp(200, _audio_answer(_pcm(0.6))))
    tts = _tts(voice="kore", model="gemini-3.8-flash-tts")
    speech = tts.synthesize("Привет, мир", tmp_path / "a.mp3")
    call = google.calls[0]
    assert call["url"] == "https://g/v1beta/models/gemini-3.8-flash-tts:generateContent"
    assert call["headers"]["x-goog-api-key"] == "k"
    body = call["json"]
    assert body["contents"][0]["parts"][0]["text"] == "Привет, мир"
    assert body["generationConfig"]["responseModalities"] == ["AUDIO"]
    assert body["generationConfig"]["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"] == "Kore"
    assert speech.cost_rub == 0.0 and speech.raw_usage["candidatesTokenCount"] == 15
    assert speech.path.read_bytes()[:3] in (b"ID3", b"\xff\xfb", b"\xff\xf3")
    assert dubbing.probe_duration(speech.path) == pytest.approx(0.6, abs=0.1)
    assert not list(tmp_path.glob("*.gemini-raw"))  # сырой файл убран


@needs_ffmpeg
def test_wav_answer_is_converted_too(monkeypatch, tmp_path):
    FakeGoogle(monkeypatch, _resp(200, _audio_answer(_wav(0.8), "audio/wav")))
    speech = _tts().synthesize("Раз-два", tmp_path / "b.mp3")
    assert dubbing.probe_duration(speech.path) == pytest.approx(0.8, abs=0.1)


def test_style_is_never_sent_and_voice_checked():
    """Gemini зачитывает указание к речи вслух в любой форме (проверено Whisper-ом 13.11) — в запрос оно не идёт."""
    tts = _tts(style="  говори весело,   быстро: ")
    assert tts.prompt("Привет") == "Привет"
    assert tts.payload("Привет")["contents"][0]["parts"][0]["text"] == "Привет"
    assert tts.style == "" and tts.style_ignored == "говори весело, быстро:"
    dialog = tts.dialog_payload([{"speaker": "narrator", "text": "Шла"}, {"speaker": "hero", "text": "Стой!"}],
                                {"hero": "Puck"})
    assert dialog["contents"][0]["parts"][0]["text"] == "Рассказчик: Шла\nГерой: Стой!"  # без вступления-указания
    assert _tts().prompt("Привет") == "Привет"
    assert dubbing.gemini_voice("zubenelgenubi") == "Zubenelgenubi"
    assert len(dubbing.GEMINI_TTS_VOICES) == 30
    with pytest.raises(dubbing.DubError, match="не знаю"):
        dubbing.GeminiTTS(voice="brian", api_key="k")


def test_no_key_is_clear_error(tmp_path):
    with pytest.raises(dubbing.DubError, match="GEMINI_API_KEY"):
        dubbing.GeminiTTS(api_key="").synthesize("Привет", tmp_path / "a.mp3")


def test_answer_without_audio_is_error():
    with pytest.raises(RuntimeError, match="нет звука.*SAFETY"):
        dubbing.gemini_audio({"candidates": [{"content": {"parts": [{"text": "нет"}]}, "finishReason": "SAFETY"}]})


# --------------------------------------------------------------------------- лимиты


def _rate_limited(delay: str = "13s", quota: str = "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"):
    return _resp(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "Quota exceeded",
                                 "details": [
                                     {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                                      "violations": [{"quotaId": quota}]},
                                     {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay}]}})


@needs_ffmpeg
def test_429_waits_retry_delay_then_succeeds(monkeypatch, tmp_path):
    google = FakeGoogle(monkeypatch, _rate_limited("13s"), _resp(503, {"error": {"message": "overloaded"}}),
                        _resp(200, _audio_answer(_pcm(0.3))))
    tts = _tts()
    tts.synthesize("Привет", tmp_path / "a.mp3")
    assert len(google.calls) == 3
    assert tts.sleeps == [14.0, dubbing.GEMINI_TTS_BACKOFF_SEC[1]]


def test_429_daily_quota_fails_fast(monkeypatch, tmp_path):
    google = FakeGoogle(monkeypatch, _rate_limited(quota="GenerateRequestsPerDayPerProjectPerModel-FreeTier"))
    tts = _tts()
    with pytest.raises(dubbing.DubError, match="дневной лимит"):
        tts.synthesize("Привет", tmp_path / "a.mp3")
    assert len(google.calls) == 1 and tts.sleeps == []


def test_429_forever_gives_up_with_clear_error(monkeypatch, tmp_path):
    google = FakeGoogle(monkeypatch, _rate_limited("500s"))
    tts = _tts()
    with pytest.raises(dubbing.DubError, match="лимит запросов"):
        tts.synthesize("Привет", tmp_path / "a.mp3")
    assert len(google.calls) == dubbing.GEMINI_TTS_TRIES
    assert all(s == dubbing.GEMINI_TTS_MAX_WAIT_SEC for s in tts.sleeps)  # ждём не дольше потолка


def test_bad_request_is_not_retried(monkeypatch, tmp_path):
    google = FakeGoogle(monkeypatch, _resp(400, {"error": {"message": "bad voice"}}))
    with pytest.raises(RuntimeError, match="400: bad voice"):
        _tts().synthesize("Привет", tmp_path / "a.mp3")
    assert len(google.calls) == 1


# --------------------------------------------------------------------------- dub_clip: без денежных ворот


def _fake_generate(monkeypatch, seconds: float = 0.5):
    calls = []

    def generate(self, payload):
        calls.append(payload)
        return _audio_answer(_pcm(seconds))

    monkeypatch.setattr(dubbing.GeminiTTS, "_generate", generate)
    return calls


def test_gemini_is_free_and_known():
    assert "gemini" in dubbing.PROVIDERS and not dubbing.provider_paid("gemini")
    assert dubbing.provider_paid("polza") and not dubbing.provider_paid("none")
    tts = dubbing.get_tts_provider("gemini", voice="Puck", style="шёпотом")
    assert isinstance(tts, dubbing.GeminiTTS) and not tts.paid and tts.price_rub_per_char() == 0
    assert tts.voice == "Puck" and tts.style == "" and tts.style_ignored == "шёпотом"
    assert tts.model == settings.gemini_tts_model
    cat = dubbing.voice_catalog()
    gem = next(p for p in cat["providers"] if p["id"] == "gemini")
    assert gem["paid"] is False and gem["voices"] == list(dubbing.GEMINI_TTS_VOICES) and gem["style"] is False
    assert set(gem["voice_notes"]) == set(dubbing.GEMINI_TTS_VOICES)  # у каждого голоса — пол и тембр
    assert "Aoede — жен." in dubbing.voice_notes_text()
    assert cat["default_provider"] == "polza"  # по умолчанию ничего не поменяли


@needs_ffmpeg
def test_dub_clip_gemini_synthesizes_without_confirm_or_enable_flag(monkeypatch, tmp_path):
    from test_translation_dubbing import _translated_store

    monkeypatch.setattr(dubbing.settings, "dubbing_enabled", False)
    monkeypatch.setattr(dubbing.settings, "gemini_api_key", "k")
    monkeypatch.setattr("app.montage_assets.assets_dir", lambda: tmp_path)
    calls = _fake_generate(monkeypatch)
    store = _translated_store()
    out = dubbing.dub_clip(store, 1, provider="gemini", voice="Charon", style="говори весело", budget_rub=0.0)
    assert out["status"] == "done", out
    assert out["estimate"]["rub"] == 0.0 and not out["estimate"]["paid"] and "бесплатно" in out["estimate"]["note"]
    assert len(calls) == 2  # по фразе
    assert "говори" not in calls[0]["contents"][0]["parts"][0]["text"]  # style не уходит в модель
    dub = store.subs["translation"]["dub"]
    assert dub["provider"] == "gemini" and dub["voice"] == "Charon" and dub["style_ignored"] == "говори весело"
    assert "style не передан" in out["note"]
    assert dub["cost_rub"] == 0.0 and dub["paid"] is False
    assert any(l["id"].startswith("dub_ru") for l in store.render["layers"])


def test_paid_gates_still_hold_for_polza(monkeypatch):
    from test_translation_dubbing import ExplodingTTS, _translated_store

    monkeypatch.setattr(dubbing.settings, "dubbing_enabled", True)
    out = dubbing.dub_clip(_translated_store(), 1, tts=ExplodingTTS())
    assert out["status"] == "estimate"


def test_chat_cards_only_for_paid_synthesis():
    from app.chat_tools import danger_label, is_dangerous

    assert not is_dangerous(None, "dub_clip", {"confirm": True, "provider": "gemini"}, None)
    assert not is_dangerous(None, "dub_clip", {"provider": "gemini"}, None)
    assert is_dangerous(None, "dub_clip", {"confirm": True, "provider": "polza"}, None)
    assert not is_dangerous(None, "tts_preview", {"confirm": True}, None)  # по умолчанию gemini
    assert is_dangerous(None, "tts_preview", {"confirm": True, "provider": "polza"}, None)
    assert "платно" in danger_label("tts_preview")


# --------------------------------------------------------------------------- проба голоса


@needs_ffmpeg
def test_tts_preview_gemini_creates_asset_and_reuses_it(tmp_path, monkeypatch):
    from test_api_pipeline import _client

    client, store = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "gemini_api_key", "k")
    calls = _fake_generate(monkeypatch, 0.7)
    res = client.post("/api/tts/preview", json={"voice": "Aoede", "style": "весело", "text": "Проверка голоса"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "done" and body["provider"] == "gemini" and body["voice"] == "Aoede"
    assert body["url"] == f"/media/montage-assets/{body['asset_id']}" and body["cost_rub"] == 0.0
    assert body["duration"] == pytest.approx(0.7, abs=0.15) and body["cached"] is False
    asset = store.get_montage_asset(body["asset_id"])
    assert asset["kind"] == "audio" and "проба голоса" in asset["tags"]
    again = client.post("/api/tts/preview", json={"voice": "aoede", "style": "весело", "text": "Проверка голоса"})
    assert again.json()["cached"] is True and again.json()["asset_id"] == body["asset_id"]
    assert len(calls) == 1  # повтор не синтезирует
    voices = client.get("/api/tts/voices").json()
    assert any(p["id"] == "gemini" and "Aoede" in p["voices"] for p in voices["providers"])
    bad = client.post("/api/tts/preview", json={"voice": "brian"})
    assert bad.status_code == 400 and "не знаю" in bad.json()["detail"]


def test_tts_preview_paid_needs_confirm_and_owner_flag(tmp_path, monkeypatch):
    from test_translation_dubbing import ExplodingTTS

    monkeypatch.setattr(dubbing.settings, "dubbing_enabled", False)
    out = dubbing.tts_preview(None, tts=ExplodingTTS(), text="Привет")
    assert out["status"] == "estimate" and out["paid"] and out["estimate_rub"] >= 0
    with pytest.raises(dubbing.DubError, match="выключена"):
        dubbing.tts_preview(None, tts=ExplodingTTS(), text="Привет", confirm=True)
    monkeypatch.setattr(dubbing.settings, "dubbing_enabled", True)

    class PriceyTTS(ExplodingTTS):
        def price_rub_per_char(self):
            return 0.1  # 280 символов ≈ 28 ₽ > потолка пробы

    with pytest.raises(dubbing.DubError, match="потолка пробы"):
        dubbing.tts_preview(None, tts=PriceyTTS(), text="Привет " * 40, confirm=True)


@needs_ffmpeg
def test_tts_preview_mcp_tool(tmp_path, monkeypatch):
    from app.agent_tools import ToolContext, run_tool
    from test_api_pipeline import _client

    _client_, store = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "gemini_api_key", "k")
    _fake_generate(monkeypatch)
    out, ok = run_tool(ToolContext(store=store), "tts_preview", {"voice": "Puck"})
    assert ok, out
    assert out["provider"] == "gemini" and out["text"] == dubbing.PREVIEW_TEXT and out["url"]


# --------------------------------------------------------------------------- ИИ-видео: голос персонажа


@needs_ffmpeg
def test_ai_video_character_gemini_voice_is_free_and_runs_without_card(tmp_path, monkeypatch):
    from app import aivideo as V
    from app import aivideo_gen as G
    from test_aivideo import _project

    from test_api_pipeline import _client

    monkeypatch.setattr(settings, "aivideo_enabled", False)  # платное выключено — бесплатному не мешает
    monkeypatch.setattr(settings, "gemini_api_key", "k")
    _client_, store = _client(tmp_path, monkeypatch)
    pid, sid = _project(store, provider="polza")  # картинки платные, голоса — Gemini
    for c in V.list_characters(store, pid):
        V.save_character(store, pid, character_id=c["id"],
                         voice={"provider": "gemini", "voice": "fenrir", "style": "устало, медленно"})
    mira = next(c for c in V.list_characters(store, pid) if c["slug"] == "mira")
    assert mira["voice"]["provider"] == "gemini" and mira["voice"]["voice"] == "Fenrir"
    with pytest.raises(V.AivError, match="не знаю"):
        V.save_character(store, pid, character_id=mira["id"], voice={"provider": "gemini", "voice": "brian"})
    with pytest.raises(V.AivError, match="gemini"):
        V.save_character(store, pid, character_id=mira["id"], voice={"provider": "elevenlabs"})

    est = G.estimate(store, pid, "voice", sid)
    assert est["count"] == 2 and est["total_rub"] == 0 and est["paid"] is False and not est["problems"]
    assert all(i["provider"] == "gemini" and i["model"] == settings.gemini_tts_model and i["voice"] == "Fenrir"
               for i in est["items"])
    calls = _fake_generate(monkeypatch, 0.6)
    out = G.request_stage(store, pid, "voice", sid)
    assert out["status"] == "done" and len(out["run"]["done"]) == 2, out
    assert "устало" not in calls[0]["contents"][0]["parts"][0]["text"]  # style Gemini не получает
    assert G.ledger(store, pid) == []  # ни рубля
    # Реплика свежая: повторная оценка ничего не находит; смена стиля (Gemini его не получает) — тоже; смена
    # голоса — переозвучить.
    assert G.estimate(store, pid, "voice", sid)["count"] == 0
    V.save_character(store, pid, character_id=mira["id"], voice={"provider": "gemini", "voice": "Fenrir",
                                                                 "style": "весело"})
    assert G.estimate(store, pid, "voice", sid)["count"] == 0
    V.save_character(store, pid, character_id=mira["id"], voice={"provider": "gemini", "voice": "Puck"})
    assert G.estimate(store, pid, "voice", sid)["count"] == 1
