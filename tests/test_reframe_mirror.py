"""Перекомпоновка в превью редактора (frontend/src/motion/reframe.ts) — зеркало app/aspect.py.

Фикстура tests/fixtures/reframe_cases.json: входы и ответ Python. Здесь — что Python всё
ещё отвечает так же (правка app/aspect.py без обновления фикстуры разведёт превью и рендер);
TypeScript сверяется с той же фикстурой в node/motion.test.mjs.
Обновить: ``VVF_UPDATE_GOLDEN=1 pytest tests/test_reframe_mirror.py``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from reframe_mirror_cases import build_cases, load_cases, write_cases  # noqa: E402


def test_reframe_fixture_matches_python():
    fresh = build_cases()
    if os.environ.get("VVF_UPDATE_GOLDEN") == "1":
        write_cases(fresh)
    saved = load_cases()
    assert saved["bases"] == fresh["bases"]
    assert [c["name"] for c in saved["cases"]] == [c["name"] for c in fresh["cases"]]
    for want, got in zip(saved["cases"], fresh["cases"]):
        assert got["expect"] == want["expect"], f"{want['name']}: app/aspect.py разошёлся с фикстурой — обнови её (см. docstring)"


@pytest.mark.parametrize("pair", [("9:16", "16:9"), ("9:16", "1:1")])
def test_reframe_fixture_really_moves_layers(pair):
    """Фикстура не вырождена: перенос меняет x/y/scale хотя бы части слоёв."""
    data = load_cases()
    case = next(c for c in data["cases"] if c["src"] == pair[0] and c["dst"] == pair[1])
    changed = [a for a, b in zip(data["bases"][case["base"]]["layers"], case["expect"]["layers"]) if (a.get("y"), a.get("scale")) != (b.get("y"), b.get("scale"))]
    assert len(changed) >= 3
