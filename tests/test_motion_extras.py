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
