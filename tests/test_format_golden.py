"""Клипы без формата (9:16) рендерятся ровно как до универсальных форматов.

Снимок команд ffmpeg, аргументов компоновщика и comp.json снят на main до правок
(tests/format_golden_cases.py). Любое расхождение — изменение старого рендера.
"""

from __future__ import annotations

import json
import os

import pytest

from format_golden_cases import CASES, GOLDEN_PATH, load_golden, run_case


@pytest.mark.parametrize("name", CASES)
def test_vertical_render_is_unchanged(name, tmp_path, monkeypatch):
    got = run_case(name, tmp_path, monkeypatch)
    assert got["status"] == "succeeded", got.get("error")
    if os.environ.get("VVF_UPDATE_GOLDEN") == "1":
        golden = load_golden()
        golden[name] = got
        GOLDEN_PATH.write_text(json.dumps(golden, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
        return
    golden = load_golden()
    assert name in golden, "нет эталона — VVF_UPDATE_GOLDEN=1 на main до правок"
    assert json.loads(json.dumps(got)) == golden[name]
