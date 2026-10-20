from app.auto_edit import build_auto_edit, punch_moments


def _words(text: str, start: float = 0.5, step: float = 0.4):
    out, t = [], start
    for w in text.split():
        out.append({"word": w, "start": round(t, 2), "end": round(t + 0.3, 2)})
        t += step
    return out


def test_auto_edit_builds_hook_captions_and_punches():
    words = _words("я хочу столько денег чтобы взять ипотеку это пиздец как страшно ребят серьёзно " * 3)
    duration = words[-1]["end"] + 1
    result = build_auto_edit("Стримерша готова продаться", words, [0.3] * 200, duration)
    types = [l["type"] for l in result["layers"]]
    assert types.count("captions") == 1
    hook = next(l for l in result["layers"] if l["id"] == "auto_hook")
    assert "СТРИМЕРША" in hook["props"]["text"]
    caps = next(l for l in result["layers"] if l["type"] == "captions")
    assert any(h["color"] == "#22c55e" for h in caps["props"]["highlightWords"]), "смысловые слова цветом"
    assert not caps["props"].get("emoji")
    # смысловые слова цветом — не чаще раза в 2 с
    times = [words[h["i"]]["start"] for h in caps["props"]["highlightWords"] if h["color"] == "#22c55e"]
    assert all(b - a >= 2.0 for a, b in zip(times, times[1:]))
    assert result["camera"], "наезды по речи"


def test_punch_moments_respect_spacing_and_edges():
    words = _words("раз два три четыре пять шесть семь восемь девять десять " * 4)
    duration = words[-1]["end"] + 0.5
    moments = punch_moments(words, [], duration)
    starts = [m[0] for m in moments]
    assert all(s >= 3.0 for s in starts)
    assert all(b - a >= 4.0 for a, b in zip(starts, starts[1:]))


def test_auto_edit_without_words_has_no_captions():
    result = build_auto_edit("Клип", [], [], 20.0)
    assert not any(l["type"] == "captions" for l in result["layers"])


def test_apply_auto_edit_places_punches_in_finished_time_with_transition(tmp_path, monkeypatch):
    """Слова — во времени кусков подряд, камера — во времени готового клипа:
    с наплывом между кусками наезд во втором куске встаёт раньше на overlap."""
    import pytest

    from app.auto_edit import apply_auto_edit
    from test_clip_spec import _store_plan

    store, plan = _store_plan(tmp_path, monkeypatch, [])  # куски 0–10 и 20–30 исходника
    store.set_clip_plan_render_settings(plan["id"], {"transition": {"type": "fade", "duration": 0.5}})
    store.set_clip_plan_subtitles(plan["id"], {"lines": [], "words": [
        {"word": "привет", "start": 0.5, "end": 0.8},
        {"word": "денег", "start": 15.0, "end": 15.4},
    ]})

    def no_waveform(*a, **k):
        raise RuntimeError("нет волны")

    monkeypatch.setattr("app.clip_waveform.clip_waveform", no_waveform)
    out = apply_auto_edit(store, plan["id"])
    assert out["duration"] == pytest.approx(19.5)
    camera = store.get_clip_plan(plan["id"])["render_settings"]["camera"]
    assert camera and camera[0]["t"] == pytest.approx(14.44)  # 15.0 − наплыв 0.5 − 0.06
