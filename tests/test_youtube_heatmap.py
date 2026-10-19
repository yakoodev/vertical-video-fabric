from app.youtube_heatmap import heatmap_peaks


def test_heatmap_peaks_picks_strong_spaced_local_maxima():
    pts = [{"start": i * 10.0, "end": i * 10.0 + 10, "value": v} for i, v in enumerate(
        [0.1, 0.2, 0.9, 0.3, 0.2, 0.25, 0.8, 0.7, 0.1, 0.4, 0.1, 0.05])]
    peaks = heatmap_peaks(pts, top=3)
    starts = [p["start"] for p in peaks]
    assert 20.0 in starts and 60.0 in starts
    assert all(b - a >= 30 for a, b in zip(starts, starts[1:]))


def test_heatmap_peaks_empty():
    assert heatmap_peaks([]) == []
