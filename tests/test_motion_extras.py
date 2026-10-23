"""Доработки моушна для промо: слой едет с камерой (followCamera)."""

from app.motion import normalize_layers, validate_layers

BOX = {"type": "shape", "start": 0, "end": 2, "props": {"kind": "rect", "w": 100, "h": 100, "fill": "#ff0000"}}


def test_follow_camera_flag_defaults_off_and_accepts_short_form():
    # Старые слои — без флага: стоят на месте, как раньше.
    assert normalize_layers([BOX])[0]["followCamera"] is False
    assert normalize_layers([{**BOX, "followCamera": True}])[0]["followCamera"] is True
    # Краткая запись «camera: "follow"» — то же самое; явный флаг важнее.
    assert normalize_layers([{**BOX, "camera": "follow"}])[0]["followCamera"] is True
    assert normalize_layers([{**BOX, "camera": "follow", "followCamera": False}])[0]["followCamera"] is False
    assert validate_layers([{**BOX, "followCamera": True}]) == []


def test_rise_animation_is_text_only():
    text = {"type": "text", "start": 0, "end": 3, "props": {"text": "НОВОЕ ПРОМО"},
            "in": {"type": "rise", "duration": 0.8}, "out": "rise"}
    layer = normalize_layers([text])[0]
    assert layer["in"] == {"type": "rise", "duration": 0.8} and layer["out"]["type"] == "rise"
    assert validate_layers([text]) == []
    # У фигуры маски строк нет — как typewriter/words: замена и пометка.
    shape = {**BOX, "in": {"type": "rise", "duration": 0.5}}
    assert normalize_layers([shape])[0]["in"]["type"] == "fade"
    assert any("rise" in p for p in validate_layers([shape]))
