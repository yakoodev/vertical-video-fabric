"""Каждый компонент плоского UI-кита (frontend/src/motion/flat/kit.ts) есть в сервисе и в редакторе.

23.11 гайд Витубики добавил в кит компоненты ``window``/``file``, а у редактора (motion/edit.ts) не было подписи
слоя для ``file``: сборка фронта упала на TS2339 прямо при выкладке. Тест ловит такое в pytest, без сборки фронта:
список кита = список сервиса (app/motion_limits.FLAT_COMPONENTS) = подписи и значки слоя в редакторе."""

from __future__ import annotations

import re
from pathlib import Path

from app.motion_limits import FLAT_COMPONENTS

FRONT = Path(__file__).resolve().parent.parent / "frontend" / "src"
KIT = FRONT / "motion" / "flat" / "kit.ts"
EDIT = FRONT / "pages" / "workspace" / "motion" / "edit.ts"


def _union(text: str, name: str) -> list[str]:
    m = re.search(rf"export type {name}\s*=\s*([^;]+);", text)
    assert m, f"нет типа {name}"
    return re.findall(r'"([a-z_]+)"', m.group(1))


def _array(text: str, name: str) -> list[str]:
    m = re.search(rf"export const {name}[^=]*=\s*\[([^\]]*)\]", text)
    assert m, f"нет списка {name}"
    return re.findall(r'"([a-z_]+)"', m.group(1))


def _record_keys(text: str, name: str) -> dict[str, str]:
    m = re.search(rf"export const {name}[^=]*=\s*\{{(.*?)\n\}};", text, re.S)
    assert m, f"нет словаря {name}"
    return dict(re.findall(r'^\s*([a-z_]+):\s*"([^"]+)"', m.group(1), re.M))


def test_kit_components_match_service_list():
    kit = KIT.read_text(encoding="utf-8")
    assert _union(kit, "FlatComponent") == _array(kit, "FLAT_COMPONENTS") == list(FLAT_COMPONENTS)
    assert set(_record_keys(kit, "FLAT_COMPONENT_LABELS")) == set(FLAT_COMPONENTS)


def test_every_kit_component_has_layer_title_and_icon_in_editor():
    edit = EDIT.read_text(encoding="utf-8")
    for name in ("UI_COMPONENT_TITLES", "UI_COMPONENT_ICON"):
        got = _record_keys(edit, name)
        missing = [c for c in FLAT_COMPONENTS if not got.get(c, "").strip()]
        assert not missing, f"{name} в motion/edit.ts: нет {missing} — добавь подпись/значок слоя для нового компонента"
        assert set(got) == set(FLAT_COMPONENTS), f"{name}: лишние {sorted(set(got) - set(FLAT_COMPONENTS))}"
    # Подпись берётся из типизированного словаря, а не из литерала с приведением к Record<string, string>.
    assert "UI_COMPONENT_TITLES[layer.props.component]" in edit
    assert "as Record<string, string>)[layer.props.component]" not in edit
