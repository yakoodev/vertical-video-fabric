"""Редактор: сочетания истории и время клипа. Модули без импортов — node запускает их как есть."""

import json

from app.settings import settings
from test_frontend_mute import _run_node

EDITOR = settings.root_dir / "frontend" / "src" / "pages" / "workspace" / "editor"


def _key(key, ctrl=True, shift=False, alt=False, meta=False):
    return {"key": key, "ctrlKey": ctrl, "shiftKey": shift, "altKey": alt, "metaKey": meta}


def test_shift_z_is_redo_not_undo():
    # С Shift браузер отдаёт заглавную букву: раньше сверка без регистра делала из Ctrl+Shift+Z ещё один шаг назад.
    cases = {
        "ctrl_z": _key("z"), "ctrl_shift_z": _key("Z", shift=True), "ctrl_y": _key("y"), "cmd_shift_z": _key("Z", ctrl=False, shift=True, meta=True),
        "ru_z": _key("я"), "ru_shift_z": _key("Я", shift=True), "ru_y": _key("н"),
        "plain_z": _key("z", ctrl=False), "alt_z": _key("z", alt=True), "ctrl_shift_y": _key("Y", shift=True), "ctrl_a": _key("a"),
    }
    script = f"""
import {{ historyKey }} from {json.dumps((EDITOR / "historyKey.ts").as_uri())};
const cases = {json.dumps(cases)};
console.log(JSON.stringify(Object.fromEntries(Object.entries(cases).map(([k, v]) => [k, historyKey(v)]))));
"""
    assert _run_node(script) == {
        "ctrl_z": "undo", "ctrl_shift_z": "redo", "ctrl_y": "redo", "cmd_shift_z": "redo",
        "ru_z": "undo", "ru_shift_z": "redo", "ru_y": "redo",
        "plain_z": None, "alt_z": None, "ctrl_shift_y": None, "ctrl_a": None,
    }


def test_first_frame_has_a_timecode():
    # Клип 545: встали на начало куска 268.4, плеер читает 268.399998. Раньше это было «вне клипа» и таймкод «—».
    pieces = [{"start_sec": 268.4, "end_sec": 291.4}, {"start_sec": 300, "end_sec": 310, "speed": 2}]
    times = {"first": 268.399998, "exact": 268.4, "mid": 270.4, "second_first": 299.999998, "second_mid": 304,
             "before": 268.39, "gap": 295, "after": 310.5}
    script = f"""
import {{ clipTimeOf }} from {json.dumps((EDITOR / "clipTime.ts").as_uri())};
const pieces = {json.dumps(pieces)};
const times = {json.dumps(times)};
console.log(JSON.stringify(Object.fromEntries(Object.entries(times).map(([k, t]) => [k, clipTimeOf(pieces, t)]))));
"""
    assert _run_node(script) == {
        "first": 0, "exact": 0, "mid": 2, "second_first": 23, "second_mid": 25,
        "before": None, "gap": None, "after": None,
    }
