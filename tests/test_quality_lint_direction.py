"""Линт ``direction`` (app/quality_lint_direction.py): одно основное направление на ролик.

Переход стыка против движения рядом — P1, смена направления на склейке — P2, хлысты в разные стороны — P2;
``reason`` у стыка — молчит. Строка находки — «P? · t=… · где · что не так → что поменять».
"""

from __future__ import annotations

from app import motion
from app import quality_lint_direction as DIR
from app.quality_lint_base import CHECKS, finding_line, normalize_checks
from app.timebase import ClipClock


def _clock(n: int = 3, length: float = 4.0):
    return ClipClock([{"start_sec": 10.0 * i, "end_sec": 10.0 * i + length} for i in range(n)], None)


def _rs(transition=None, camera=None, layers=None):
    return {"transition": transition or {"type": "cut"}, "camera": camera or [],
            "layers": motion.normalize_layers(layers or [])}


def test_registered_and_aliases():
    assert "direction" in CHECKS and normalize_checks("continuity") == ["direction"]


def test_camera_and_layer_events():
    cam = DIR.camera_events([{"t": 1.0, "x": 0.4}, {"t": 2.0, "x": 0.6}, {"t": 3.0, "x": 0.605}])
    assert len(cam) == 1 and cam[0]["axis"] == "h" and cam[0]["sign"] == -1  # центр вправо → картинка влево
    layers = motion.normalize_layers([
        {"id": "t1", "type": "text", "start": 3.5, "end": 6.0, "props": {"text": "А"},
         "in": {"type": "slideLeft", "duration": 0.4}, "out": {"type": "slideLeft", "duration": 0.3}},
        {"id": "s1", "type": "shape", "start": 0.0, "end": 3.0, "props": {"kind": "rect"},
         "keys": {"x": [{"t": 0.0, "v": 0.2}, {"t": 1.0, "v": 0.8}]}},
    ])
    ev = DIR.layer_events(layers)
    signs = {(e["what"].split()[1], e["sign"]) for e in ev}
    assert ("t1", -1) in signs and ("t1", 1) in signs and ("s1", 1) in signs


def test_whip_against_camera_is_p1_and_line_format():
    # стык 0 на 4.0 с: камера до него ведёт картинку вправо, хлыст — влево
    rs = _rs({"type": "cut", "joins": [{"join": 0, "type": "whippan", "direction": "left"}]},
             camera=[{"t": 3.5, "x": 0.6}, {"t": 4.0, "x": 0.4}])
    found = DIR.direction_findings(rs, _clock())
    assert len(found) == 1
    f = found[0]
    assert f["priority"] == "P1" and f["where"] == "стык 1→2" and f["join"] == 0 and abs(f["t"] - 4.0) < 0.01
    line = finding_line(f)
    assert line.startswith("P1 · t=4.0 · стык 1→2 · хлыст влево, а камера ведёт картинку вправо")
    assert "→ переход в ту же сторону: direction right" in line and "set_transition(" not in line


def test_same_direction_and_reason_are_quiet():
    cam = [{"t": 3.5, "x": 0.4}, {"t": 4.0, "x": 0.6}]   # картинка влево
    rs = _rs({"type": "cut", "joins": [{"join": 0, "type": "whippan", "direction": "left"}]}, camera=cam)
    assert DIR.direction_findings(rs, _clock()) == []
    cam_against = [{"t": 3.5, "x": 0.6}, {"t": 4.0, "x": 0.4}]
    reason = _rs({"type": "cut", "joins": [{"join": 0, "type": "whippan", "direction": "left", "reason": "флешбек"}]},
                 camera=cam_against)
    assert DIR.direction_findings(reason, _clock()) == []


def test_flip_across_plain_cut_is_p2():
    layers = [{"id": "a", "type": "text", "start": 1.0, "end": 3.9, "props": {"text": "до"},
               "out": {"type": "slideRight", "duration": 0.3}},           # уезжает влево
              {"id": "b", "type": "text", "start": 4.0, "end": 6.0, "props": {"text": "после"},
               "in": {"type": "slideRight", "duration": 0.4}}]           # въезжает вправо
    found = DIR.direction_findings(_rs(layers=layers), _clock())
    assert len(found) == 1 and found[0]["priority"] == "P2" and "до склейки" in found[0]["message"]


def test_whips_in_different_directions_flag_the_minority():
    t = {"type": "whippan", "direction": "right", "joins": [{"join": 2, "type": "whippan", "direction": "left"}]}
    found = DIR.direction_findings(_rs(t), _clock(4))
    assert len(found) == 1 and found[0]["join"] == 2 and found[0]["priority"] == "P2"
    assert "основного направления ролика — вправо (2 из 3)" in found[0]["message"]


def test_auto_direction_and_non_directional_kinds_skip():
    rs = _rs({"type": "whippan"}, camera=[{"t": 3.5, "x": 0.6}, {"t": 4.0, "x": 0.4}])
    assert DIR.direction_findings(rs, _clock()) == []
    assert DIR.join_motion({"type": "slash", "angle": 45}, 0) is None
    assert DIR.join_motion({"type": "slash", "angle": 90}, 0)["what"] == "срез вниз"
