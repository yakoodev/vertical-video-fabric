"""Линт того, что агент не увидел (повтор 13.11): субтитры под телефоном (RC1), донат-бар в окне игры split (RC2),
субтитров мало для речи."""

from __future__ import annotations

import numpy as np

from app import quality_lint_cover as QC


def _caps(track=0, y=0.5):
    return {"id": "caps", "type": "captions", "start": 0, "end": 10, "y": y, "track": track, "props": {}}


def _phone(track=0, y=0.46, lid="phone_1"):
    return {"id": lid, "type": "page", "start": 0, "end": 10, "x": 0.5, "y": y, "track": track,
            "props": {"page_id": 1, "w": 560, "device": "phone"}}


PAGES = {"caps": [{"t0": 1.0, "t1": 2.0, "text": "НАПРИМЕР INK GIRL", "width": 700, "height": 200}]}


def test_phone_above_subtitles_is_found_with_fix():
    found = QC.subtitle_cover_findings([_caps(track=0), _phone(track=2)], PAGES)
    assert len(found) == 1 and found[0]["check"] == "reading" and found[0]["severity"] == "warn"
    assert "phone_1" in found[0]["message"] and "телефон" in found[0]["message"] and "track 3" in found[0]["fix"]
    # Тот же трек, но телефон раньше в списке — он под субтитрами.
    assert QC.subtitle_cover_findings([_phone(track=0), _caps(track=0)], PAGES) == []
    # Субтитры выше слоя или телефон в стороне — чисто.
    assert QC.subtitle_cover_findings([_caps(track=5), _phone(track=2)], PAGES) == []
    assert QC.subtitle_cover_findings([_caps(track=0, y=0.85), _phone(track=2, y=0.3)], PAGES) == []


def test_burned_subtitles_under_any_opaque_layer():
    ass = {"pages": [{"t0": 1.0, "t1": 2.0, "text": "привет", "width": 600, "height": 120}], "bottom_pct": 50, "align": 2}
    found = QC.subtitle_cover_findings([_phone(track=0)], {}, ass)
    assert found and "прожигаемые" in found[0]["message"] and "position_pct" in found[0]["fix"]


def test_game_window_of_split_and_focus():
    plan = {"segments": [{"focus": [{"t": 0, "x": 0.3}]}, {"focus": []}]}
    rs = {"layout": {"mode": "split", "ratio": 0.38, "cam_pos": "top"}}
    win = QC.game_window(plan, rs, {"width": 1920, "height": 1080})
    assert win["y0"] == 0 and win["y1"] == 1 and win["x0"] < 0.2 and 0.7 < win["x1"] < 0.8
    assert QC.game_window(plan, {"layout": {"mode": "single"}}, {"width": 1920, "height": 1080}) is None


def _frames_with_bar():
    rng = np.random.default_rng(1)
    frames = rng.uniform(0, 255, size=(8, 180, 320)).astype(np.float32)  # «игра» — всё меняется
    bar = np.tile((np.arange(320) % 7 * 30).astype(np.float32), (14, 1))  # неподвижные надписи внизу
    frames[:, 160:174, :] = bar
    return frames


def test_static_bar_at_bottom_of_game_window():
    win = {"x0": 0.2, "x1": 0.8, "y0": 0.0, "y1": 1.0}
    bands = QC.static_bands(_frames_with_bar(), win)
    assert len(bands) == 1 and bands[0]["y0"] > 0.85 and "надпис" in bands[0]["from"]
    # Всё меняется — полос нет; кадр целиком стоит (меню, пауза) — о полосах не судим.
    rng = np.random.default_rng(2)
    assert QC.static_bands(rng.uniform(0, 255, size=(8, 180, 320)).astype(np.float32), win) == []
    assert QC.static_bands(np.zeros((8, 180, 320), dtype=np.float32), win) == []


def test_overlay_finding_from_detected_ui_box(monkeypatch):
    plan = {"id": 7, "source_id": 3, "segments": [{}]}
    rs = {"layout": {"mode": "split", "ratio": 0.46, "cam_pos": "bottom", "cam": {"x": 0.74, "y": 0.6, "w": 0.26, "h": 0.38}}}

    class Store:
        def get_source(self, _sid):
            return {"id": 3, "width": 1920, "height": 1080, "source_type": "upload", "local_path": ""}

    monkeypatch.setattr(QC, "_cached_ui_boxes", lambda store, pid: [
        {"x0": 0.25, "y0": 0.9, "x1": 0.7, "y1": 0.97, "from": "detect_avatar"},
        {"x0": 0.8, "y0": 0.7, "x1": 0.95, "y1": 0.9, "from": "detect_avatar"}])  # второй — в вебке
    found = QC.stream_overlay_findings(Store(), plan, rs)
    assert len(found) == 1 and found[0]["check"] == "montage" and "внизу" in found[0]["message"]
    assert "set_camera zoom ≥" in found[0]["fix"] and "stream-shorts" in found[0]["fix"]


def test_subtitle_coverage_ratio_and_ignored_game_voice():
    speech = [(0.0, 10.0), (20.0, 30.0)]
    words = [{"word": "раз", "start": 1.0, "end": 9.0}]
    cov = QC.subtitle_coverage(speech, words)
    assert cov["speech_sec"] == 20.0 and cov["ratio"] < 0.6
    # Вторая речь — голос игры, убранный из субтитров: не считается.
    assert QC.subtitle_coverage(speech, words, ignore=[(19.9, 30.1)])["ratio"] >= 0.85
    assert QC.subtitle_coverage(speech, words, cuts=[{"start": 20, "end": 30}])["speech_sec"] == 10.0
