"""Сигналы моментов стрима: немая реакция, клипы зрителей Twitch, тепловая карта и метки YouTube, rerank.

Каждая проверка — с негативным контролем (испорченный вход, который она обязана поймать)."""

from __future__ import annotations

import math

import numpy as np

from app import external_signals as ext
from app import moment_signals as ms
from app import silent_reaction as sr


def _dense(t0: float, t1: float, wps: float = 3.0) -> list[tuple[float, float]]:
    step = 1.0 / wps
    return [(t0 + i * step, t0 + i * step + step * 0.8) for i in range(int((t1 - t0) * wps))]


# ---------------------------------------------------------------- 1. немая реакция


def test_silent_reaction_found_after_dense_speech_with_loud_gap():
    words = _dense(0, 10) + _dense(15.5, 20)          # речь, 5,5 с без слов, речь
    spans = [(10.6, 14.5, "смех?")]                  # внутри паузы — смех
    found = sr.find_silent_reactions(words, spans)
    assert len(found) == 1
    item = found[0]
    assert 3 <= item["len"] <= 8 and item["labels"] == ["смех?"] and item["score"] > 0.5
    assert sr.check_reaction(item, words, spans) == []


def test_silent_reaction_negative_controls():
    words = _dense(0, 10) + _dense(15.5, 20)
    # тихая пауза — не реакция
    assert sr.find_silent_reactions(words, []) == []
    # редкая речь до паузы — не реакция
    sparse = [(t, t + 0.3) for t in range(0, 10, 2)] + _dense(15.5, 20)
    assert sr.find_silent_reactions(sparse, [(10.6, 14.5, "крик")]) == []
    # пауза 12 с — уже не 3–8 с
    assert sr.find_silent_reactions(_dense(0, 10) + _dense(22, 25), [(10.5, 21, "громко")]) == []
    # проверка ловит подложенную «реакцию» со словами внутри
    bad = {"start": 10.0, "end": 15.5}
    problems = sr.check_reaction(bad, words + [(12.0, 12.3)], [(10.6, 14.5, "смех?")])
    assert any("слов" in p for p in problems)
    assert sr.check_reaction(bad, words, []) and "громко" in sr.check_reaction(bad, words, [])[0]


def test_reaction_signal_bins_and_words_from_cues():
    sig = sr.reaction_signal([{"start": 12.0, "end": 17.0, "score": 0.8}], duration=40)
    assert sig["bucket"] == 10 and sig["curve"][:3] == [0.0, 0.8, 0.0] and len(sig["curve"]) == 5
    assert sr.reaction_signal([]) is None
    words = sr.words_from_cues([{"start": 0, "end": 2, "text": "раз два"},
                                {"start": 3, "end": 4, "text": "x", "words": [{"start": 3.1, "end": 3.5}]}])
    assert words == [(0, 1.0), (1.0, 2.0), (3.1, 3.5)]


def test_chunk_spans_uses_transcript_as_speech_mask():
    from app.audio_map import frame_features, label_spans

    rate = 16000
    t = np.arange(rate * 8) / rate
    noise = np.random.default_rng(1).normal(0, 0.01, len(t)).astype(np.float32)
    burst = np.zeros_like(noise)
    burst[rate * 4: rate * 6] = np.random.default_rng(2).normal(0, 0.5, rate * 2)
    spans = sr.chunk_spans(frame_features, label_spans, noise + burst, [(0.0, 2.0)], t0=100.0)
    assert any(lab == "громко" and 103.9 <= a <= 104.3 for a, b, lab in spans)
    assert all(a >= 100 for a, _b, _l in spans)  # шкала исходника


# ---------------------------------------------------------------- 2. внешние сигналы


def test_twitch_vod_clips_via_gql_and_signal():
    calls = []

    def fake_gql(query: str, variables: dict) -> dict:
        calls.append(variables)
        if "video(id" in query:
            return {"data": {"video": {"owner": {"login": "streamer"}, "clips": {"edges": [
                {"node": {"slug": "a", "viewCount": 10, "durationSeconds": 20, "videoOffsetSeconds": 100, "video": None}},
                {"node": {"slug": "nooff", "viewCount": 10, "durationSeconds": 20, "videoOffsetSeconds": None}}]}}}}
        return {"data": {"user": {"clips": {"edges": [
            {"node": {"slug": "b", "viewCount": 5000, "durationSeconds": 30, "videoOffsetSeconds": 300, "video": {"id": "42"}}},
            {"node": {"slug": "other", "viewCount": 9999, "durationSeconds": 30, "videoOffsetSeconds": 50, "video": {"id": "7"}}},
            {"node": {"slug": "a", "viewCount": 10, "durationSeconds": 20, "videoOffsetSeconds": 100, "video": {"id": "42"}}}]}}}}

    clips = ext.fetch_vod_clips("42", gql=fake_gql)
    assert [c["slug"] for c in clips] == ["a", "b"]          # чужой VOD и клип без смещения — мимо, без повторов
    assert calls[1] == {"u": "streamer", "p": "LAST_WEEK"} and calls[2]["p"] == "ALL_TIME"
    sig = ext.clips_signal(clips, duration=400)
    assert sig["curve"][30] == 1.0 and 0 < sig["curve"][10] < 0.5   # 5000 просмотров сильнее 10
    assert sig["curve"][20] == 0.0
    assert ext.twitch_vod_id("https://www.twitch.tv/videos/2935172718?t=1h") == "2935172718"
    assert ext.twitch_vod_id("https://www.twitch.tv/somebody/clip/Slug") is None  # клип — не VOD
    assert ext.clips_signal([]) is None


def test_youtube_heatmap_signal_and_negative():
    heat = [{"start_time": i * 6.0, "end_time": (i + 1) * 6.0, "value": 0.2} for i in range(10)]
    heat[5]["value"] = 1.0  # 30–36 с — пик
    sig = ext.heatmap_signal(heat, duration=60)
    assert sig["curve"][3] == 1.0 and max(sig["curve"][:3]) == 0.0
    assert ext.heatmap_signal(None) is None and ext.heatmap_signal([]) is None
    info = ext.ytdlp_info("https://youtu.be/x", runner=lambda args: '{"heatmap": [{"start_time": 0, "end_time": 5, "value": 1}]}')
    assert info["heatmap"][0]["value"] == 1
    assert ext.is_youtube("https://www.youtube.com/watch?v=abc") and not ext.is_youtube("https://twitch.tv/videos/1")


VTT = """WEBVTT

00:00:01.000 --> 00:00:03.000 align:start position:0%
привет<00:00:01.500><c> всем</c>

00:00:12.000 --> 00:00:14.000 align:start position:0%
привет всем
пустите.<00:00:12.657><c> [смех]</c>

00:00:14.000 --> 00:00:14.010 align:start position:0%
пустите. [смех]

00:00:41.000 --> 00:00:42.000 align:start position:0%
пустите. [смех]
[Music]<00:00:41.200><c> [laughter]</c><00:00:41.500><c> [screaming]</c>
"""

PLAIN_VTT = """WEBVTT

00:00:05.000 --> 00:00:07.000
[смех] ну

00:00:07.000 --> 00:00:09.000
[смех] ну
дальше [applause]
"""


def test_auto_sub_tags_dedup_and_bins():
    tags = ext.parse_vtt_tags(VTT)
    # повторы «наката» (строка без пословных таймингов) и [Music] не считаются; время — тайминг перед меткой
    assert tags == [(12.66, "смех"), (41.2, "laughter"), (41.5, "screaming")]
    sig = ext.tags_signal(tags, duration=50)
    assert sig["curve"][4] == 1.0 and sig["curve"][1] == 0.5 and sig["curve"][0] == 0.0
    assert ext.parse_vtt_tags(PLAIN_VTT) == [(5.0, "смех"), (7.0, "applause")]  # без таймингов: новое против прошлого блока
    assert ext.parse_vtt_tags("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nпросто текст") == []


# ---------------------------------------------------------------- смешивание в rerank


def _plans() -> list[dict]:
    return [{"id": i, "source_id": 5, "segments": [{"start_sec": i * 100.0, "end_sec": i * 100.0 + 30}]} for i in range(4)]


def test_extras_blend_soft_and_rerank_moves_signalled_plan(tmp_path, monkeypatch):
    from app import chat_signal
    from app.settings import settings

    monkeypatch.setattr(settings, "runtime_dir", tmp_path)
    monkeypatch.setattr(chat_signal, "source_signal", lambda store, sid: None)
    plans = _plans()
    assert [p["id"] for p in chat_signal.rerank(None, plans)] == [0, 1, 2, 3]  # нет сигналов — как было
    curve = [0.0] * 40
    curve[30] = 1.0  # реакция и клипы зрителей на 300–310 с (план 3)
    ms.save_extras(5, {"reaction": {"bucket": 10, "curve": curve}, "viewer_clips": {"bucket": 10, "curve": curve}})
    order = [p["id"] for p in chat_signal.rerank(None, plans)]
    assert order.index(3) < 3   # поднялся
    assert order[0] == 0        # но мягко: лучший по анализу остался первым
    assert ms.blend_extras(0.5, {}) == 0.5
    assert ms.blend_extras(0.5, {"reaction": 1.0}) > 0.5 and ms.blend_extras(0.5, {"unknown": 1.0}) == 0.5


def test_refresh_source_signals_collects_twitch_and_notes(tmp_path, monkeypatch):
    from app.settings import settings

    monkeypatch.setattr(settings, "runtime_dir", tmp_path)

    class Store:
        def get_source(self, sid):
            return {"id": sid, "original_url": "https://www.twitch.tv/videos/42", "duration_sec": 400,
                    "local_path": str(tmp_path / "none.mp4"), "metadata": {}}

        def get_source_transcript(self, sid):
            return []

    fake = lambda q, v: {"data": {"video": {"owner": None, "clips": {"edges": [  # noqa: E731
        {"node": {"slug": "a", "viewCount": 3, "durationSeconds": 20, "videoOffsetSeconds": 100}}]}}}}
    out = ms.refresh_source_signals(Store(), 9, gql=fake)
    assert out["signals"] == ["viewer_clips"]
    assert any("немая реакция" in n for n in out["notes"])  # нет файла/расшифровки — сказано, не упало
    assert ms.load_extras(9)["viewer_clips"]["curve"][10] == 1.0


def test_agreement_measures_peak_overlap_and_negative():
    a = {"bucket": 10, "curve": [0, 1, 0, 0, 0.9, 0, 0, 0]}
    b = {"bucket": 10, "curve": [0, 0, 1, 0, 0, 0, 0, 0.8]}
    assert ms.agreement(a, b, top=2) == 0.5
    assert ms.agreement(a, None) is None
    assert ms.agreement(a, {"bucket": 10, "curve": [0] * 8}) is None
    assert math.isclose(ms.agreement(a, a, top=2), 1.0)


def test_window_peaks_for_audio_map(tmp_path, monkeypatch):
    from app.settings import settings

    monkeypatch.setattr(settings, "runtime_dir", tmp_path)
    curve = [0.0] * 20
    curve[3], curve[12] = 0.9, 0.3
    ms.save_extras(6, {"viewer_clips": {"bucket": 10, "curve": curve}})
    peaks = ms.window_peaks(None, 6, 0, 100)
    assert peaks == [{"t": 30.0, "signal": "viewer_clips", "value": 0.9}]  # слабая корзина 0.3 — мимо
    assert ms.window_peaks(None, 6, 50, 100) == []                       # вне окна
    assert ms.window_peaks(None, 6, 0, 100, to_out=lambda t: t - 25)[0]["t"] == 5.0
