"""Плоская тема моушна: слой ``ui`` (компоненты на токенах темы), фон под плашками ``fx backdrop``, рамка
для перекомпоновки и линтов, тема brand из бренда проекта. Движок — node/motion-flat.test.mjs."""

from __future__ import annotations

from app import motion
from app.aspect import layer_size, ui_size
from app.layer_lint import cover_share, covering_problems, is_full_insert
from app.motion_template_tools import with_brand
from app.quality_lint_zones import layer_box


def _one(raw: dict) -> dict:
    [layer] = motion.normalize_layers([{"start": 0, "end": 4, **raw}])
    return layer


def test_ui_layer_normalizes_and_keeps_only_given_fields():
    ui = _one({"type": "ui", "props": {"component": "stat", "theme": "red", "label": "Просмотры", "value": "942692",
                                       "items": [{"text": "а", "from": "me", "done": False, "junk": 1}], "zzz": 5}})
    p = ui["props"]
    assert p["component"] == "stat" and p["theme"] == "red" and p["value"] == 942692
    assert "zzz" not in p and "junk" not in p["items"][0]
    assert p["items"][0]["from"] == "me" and p["items"][0]["done"] is False
    # Необязательное не подставляется: умолчания знает движок (палитра темы, кегли).
    assert "accent" not in p and "w" not in p and "variant" not in p
    bad = _one({"type": "ui", "props": {"component": "nope", "theme": "purple"}})
    assert bad["props"]["component"] == "stat" and bad["props"]["theme"] == "dark"


def test_ui_required_texts_and_items_reported():
    problems = motion.validate_layers([{"type": "ui", "start": 0, "end": 2, "props": {"component": "checklist"}}])
    assert any("items" in p for p in problems)
    problems = motion.validate_layers([{"type": "ui", "start": 0, "end": 2, "props": {"component": "hook"}}])
    assert any("title" in p for p in problems)
    assert not motion.validate_layers([{"type": "ui", "start": 0, "end": 2,
                                        "props": {"component": "hook", "title": "НАБИРАЮТ"}}])


def test_backdrop_fx_kind_and_aliases():
    fx = _one({"type": "fx", "props": {"kind": "backdrop_blur", "size": 30, "intensity": 0.5}})
    assert fx["props"]["kind"] == "backdrop" and fx["props"]["size"] == 30
    assert "backdrop" in motion.FX_KINDS


def test_ui_size_estimates_by_component():
    assert ui_size({"component": "toast"}) == (920, 152)
    w, h = ui_size({"component": "bars", "label": "x", "items": [{}, {}]})
    assert w == 920 and h == 88 + 55 + 2 * 94 + 34
    cw, ch = ui_size({"component": "chip", "title": "Ошибка 1"})
    assert 240 < cw < 420 and ch == 36 * 1.74
    assert layer_size({"type": "ui", "props": {"component": "stat", "w": 800}}) == (800, 330)


def test_hook_fill_is_short_full_insert_and_cards_are_small():
    hook = _one({"type": "ui", "start": 0, "end": 1.2, "props": {"component": "hook", "title": "ХУК", "variant": "fill"}})
    assert cover_share(hook) == 1.0 and is_full_insert(hook)
    assert not covering_problems([hook])
    long_hook = {**hook, "end": 6}
    assert covering_problems([long_hook]), "заставка дольше 3 с закрывает видео"
    plain = _one({"type": "ui", "props": {"component": "hook", "title": "ХУК"}})
    assert cover_share(plain) == 0.0
    card = _one({"type": "ui", "props": {"component": "stat", "label": "x"}})
    assert 0 < cover_share(card) < 0.2 and not covering_problems([card])
    # Безопасные зоны: карточка проверяется, заставка во весь кадр — нет.
    assert layer_box(card, 1.0) is not None
    assert layer_box(hook, 0.5) is None


def test_brand_theme_takes_project_colors_unless_given():
    brand = {"accent": "#ffb703", "plate": "#1d3557"}
    assert with_brand({"theme": "brand"}, brand) == {"theme": "brand", "accent": "#ffb703", "plate": "#1d3557"}
    assert with_brand({"theme": "brand", "accent": "#ff0000"}, brand)["accent"] == "#ff0000"
    assert with_brand({"theme": "red"}, brand) == {"theme": "red"}
    assert with_brand({"theme": "brand"}, {}) == {"theme": "brand"}
