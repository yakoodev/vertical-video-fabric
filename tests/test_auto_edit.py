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
    assert caps["props"]["emoji"], "словарь эмодзи должен сработать на «денег/ипотеку/страшно»"
    # эмодзи не чаще раза в 4 с
    times = [words[e["i"]]["start"] for e in caps["props"]["emoji"]]
    assert all(b - a >= 4.0 for a, b in zip(times, times[1:]))
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
