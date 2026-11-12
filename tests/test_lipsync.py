"""Рот по гласным (app/lipsync.py): буквы → звуки, известная последовательность гласных → нужные формы рта, губы
смыкаются на м/б/п, вне речи рот закрыт, привязка к ролику со сдвигом голоса."""

from __future__ import annotations

import numpy as np

from app import lipsync as L

SR = 16000


def voiced(total: float, spans: list[tuple[float, float]], f0: float = 210.0) -> np.ndarray:
    """Синтетический «голос»: гармоники f0 в окнах spans, тишина вне их."""
    t = np.arange(int(total * SR)) / SR
    sig = sum(np.sin(2 * np.pi * f0 * k * t) / k for k in range(1, 12))
    mask = np.zeros_like(t)
    for a, b in spans:
        mask[(t >= a) & (t < b)] = 1.0
    return (0.25 * sig * mask).astype(np.float32)


def test_russian_letters_to_phones() -> None:
    assert [k for k, _ in L.phones("мама")] == ["L", "V", "L", "V"]
    assert L.phones("мама")[1][1] == {"aa": 1.0}
    kinds = {c: L.phones(c)[0] for c in "аоуиыэеяюё"}
    assert max(kinds["у"][1], key=kinds["у"][1].get) == "ou"
    assert max(kinds["и"][1], key=kinds["и"][1].get) == "ih"
    assert max(kinds["э"][1], key=kinds["э"][1].get) == "ee"
    assert max(kinds["о"][1], key=kinds["о"][1].get) == "oh"
    assert [k for k, _ in L.phones("фвшь")] == ["D", "D", "R"]
    # Цифры — словами: «7» → «семь» (губы смыкаются на «м»).
    assert "L" in [k for k, _ in L.phones("7")]


def test_known_vowel_sequence_maps_to_vrm_shapes() -> None:
    seq = [("у", "ou"), ("и", "ih"), ("э", "ee"), ("о", "oh"), ("а", "aa")]
    words, spans = [], []
    t = 0.3
    for letter, _ in seq:
        words.append({"word": letter, "start": t, "end": t + 0.3})
        spans.append((t, t + 0.3))
        t += 0.6
    pcm = voiced(t + 0.3, spans)
    vis = L.visemes(pcm, SR, words, t + 0.3)
    for (letter, shape), w in zip(seq, words):
        i = int(round(((w["start"] + w["end"]) / 2 - vis["lead"]) * vis["fps"]))
        frame = {k: vis[k][i] for k in L.VOWELS}
        assert max(frame, key=frame.get) == shape, (letter, frame)
        assert sum(frame.values()) > 0.25, (letter, frame)
    # Между словами (пауза 0.3 с) рот закрыт.
    i = int(round(((words[0]["end"] + words[1]["start"]) / 2 + 0.02) * vis["fps"]))
    assert sum(vis[k][i] for k in L.VOWELS) < 0.08
    assert L.agreement(vis, words)["vowel_match"] == 1.0


def test_lips_close_on_labials_and_output_is_deterministic() -> None:
    words = [{"word": "мама", "start": 0.4, "end": 1.2}, {"word": "папа", "start": 1.5, "end": 2.3}]
    pcm = voiced(2.8, [(0.4, 1.2), (1.5, 2.3)])
    a = L.visemes(pcm, SR, words, 2.8)
    b = L.visemes(pcm, SR, words, 2.8)
    assert a == b
    ag = L.agreement(a, words)
    assert ag["labials"] == 4 and ag["lips_closed"] == 1.0
    assert ag["vowel_match"] == 1.0
    assert all(0 <= v <= 1 for k in L.VOWELS for v in a[k])
    assert len(a["aa"]) == int(np.ceil(2.8 * 30)) + 1


def test_no_words_means_amplitude_fallback() -> None:
    assert L.visemes(voiced(1.0, [(0, 1)]), SR, [], 1.0) is None


def test_attach_timeline_shifts_voice_and_enables_life(monkeypatch) -> None:
    pcm = voiced(1.5, [(0.2, 1.0)])

    def fake_from_track(_track, words, total, windows=None, fps=30):
        assert abs(words[0]["start"] - 0.2) < 1e-9 and np.allclose(windows, [(0.2, 1.0)])
        return L.visemes(pcm, SR, words, total, windows=windows, fps=fps)

    monkeypatch.setattr(L, "from_track", fake_from_track)
    tl = {"speech": [[2.2, 3.0]]}
    ok = L.attach_timeline(tl, "voice.wav", [{"word": "да", "start": 2.2, "end": 3.0}, {"word": "нет", "start": 5.0, "end": 5.4}],
                           3.5, offset=2.0, life_seed=7)
    assert ok and tl["visemes"]["start"] == 2.0 and tl["life"] == {"seed": 7}
    tl2: dict = {"speech": []}
    assert L.attach_timeline(tl2, "voice.wav", [], 3.0) is False and "visemes" not in tl2
