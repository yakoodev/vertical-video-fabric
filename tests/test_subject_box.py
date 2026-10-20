from app.ai.subject_box import _parse_box, text_positions


def test_character_low_puts_captions_above():
    # VTuber-модель внизу кадра (0.45–1.0): субтитры над ней, не на ней.
    pos = text_positions({"x0": 0.2, "y0": 0.45, "x1": 0.8, "y1": 1.0})
    assert 0.24 <= pos["captions_y"] < 0.45


def test_character_high_puts_captions_below():
    pos = text_positions({"x0": 0.2, "y0": 0.1, "x1": 0.8, "y1": 0.5})
    assert 0.5 < pos["captions_y"] <= 0.78


def test_no_box_falls_back():
    assert text_positions(None, 0.7)["captions_y"] == 0.7


def test_parse_box_tolerates_wrapping_text():
    assert _parse_box('```json {"x0":0.1,"y0":0.4,"x1":0.9,"y1":1.2} ```') == {"x0": 0.1, "y0": 0.4, "x1": 0.9, "y1": 1.0}
    assert _parse_box("{}") is None
