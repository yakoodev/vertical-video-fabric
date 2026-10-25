"""Пороги линта по формату/площадке (app/safe_zones.LINT_LIMITS): 16:9 — экран YouTube/презентации
(кегль мельче, графики больше, сцена-комбо «телефон + окно» не «переход без разведения»);
9:16 — тексты предупреждений прежние."""

from __future__ import annotations

from app import motion
from app.agent_tools import lint_layers
from app.safe_zones import lint_limits


def _text(lid: str, size: float, start: float = 0.0, end: float = 6.0, x: float = 0.5, y: float = 0.5) -> dict:
    return {"id": lid, "type": "text", "start": start, "end": end, "x": x, "y": y,
            "props": {"text": "Титр", "size": size}}


def _mine(warnings: list[str], lid: str) -> list[str]:
    return [w for w in warnings if w.startswith(f"{lid}:")]


def test_limits_table():
    assert lint_limits("9:16") == lint_limits(None) == lint_limits("мусор")
    assert lint_limits("9:16")["min_font"] == 44 and lint_limits("9:16")["max_graphics"] == 3
    assert lint_limits("1:1")["min_font"] == 44 and lint_limits("4:5")["max_graphics"] == 3
    assert lint_limits("16:9")["min_font"] < 44 and lint_limits("16:9")["max_graphics"] > 3


def test_small_font_by_format():
    layers = motion.normalize_layers([_text("t", 40)])
    # 9:16 — прежний текст слово в слово.
    assert "t: шрифт 40px мелковат для телефона (лучше ≥ 56)" in lint_layers(layers, 6.0)
    assert "t: шрифт 40px мелковат для телефона (лучше ≥ 56)" in lint_layers(layers, 6.0, fmt="9:16")
    # 16:9 — 40 px на экране читается.
    assert not [w for w in lint_layers(layers, 6.0, fmt="16:9") if "мелковат" in w]
    tiny = motion.normalize_layers([_text("t", 24)])
    assert "t: шрифт 24px мелковат для экрана YouTube/презентации (лучше ≥ 36)" in lint_layers(tiny, 6.0, fmt="16:9")


def test_graphics_overload_by_format():
    four = motion.normalize_layers([_text(f"g{i}", 80, y=0.2 + 0.15 * i) for i in range(4)])
    over = lambda ws: [w for w in ws if "перегруз" in w]  # noqa: E731
    assert over(lint_layers(four, 6.0)) == ["g0: на 0.1 с на экране 4 слоя графики — перегруз (≤ 3)",
                                            "g1: на 0.1 с на экране 4 слоя графики — перегруз (≤ 3)",
                                            "g2: на 0.1 с на экране 4 слоя графики — перегруз (≤ 3)",
                                            "g3: на 0.1 с на экране 4 слоя графики — перегруз (≤ 3)"]
    assert not over(lint_layers(four, 6.0, fmt="16:9"))  # слайд: титр, подпись, два медиа — норма
    six = motion.normalize_layers([_text(f"g{i}", 60, y=0.15 + 0.12 * i) for i in range(6)])
    assert "g0: на 0.1 с на экране 6 слоя графики — перегруз (≤ 5)" in lint_layers(six, 6.0, fmt="16:9")


def _combo(window_x: float, window_start: float = 0.0, fmt_w: float = 1920.0) -> list[dict]:
    """Телефон слева и окно записи (другой файл) правее — одна сцена 0–6 с."""
    return motion.normalize_layers([
        {"id": "phone", "type": "video", "start": 0, "end": 6, "x": 0.3, "y": 0.5,
         "props": {"asset_id": 4, "w": 300, "device": "phone"}},
        {"id": "window", "type": "video", "start": window_start, "end": 6, "x": window_x, "y": 0.5,
         "props": {"asset_id": 5, "w": round(0.42 * fmt_w)}},
    ])


SIZES = {"4": (1170, 2532), "5": (1920, 1080)}
GAP = "переход сцен без разведения"


def test_combo_overlap_not_scene_transition_in_16x9():
    layers = _combo(0.45)
    wide = lint_layers(layers, 6.0, canvas=True, sizes=SIZES, fmt="16:9")
    assert not [w for w in wide if GAP in w or "сцена-комбо" in w], wide
    # Окно почти целиком на телефоне — мягкая подсказка, не «переход без разведения».
    heavy = lint_layers(_combo(0.3), 6.0, canvas=True, sizes=SIZES, fmt="16:9")
    assert [w for w in heavy if "сцена-комбо" in w] and not [w for w in heavy if GAP in w], heavy
    # Логотип, влетевший на уходе телефона (вместе недолго), — по-прежнему переход сцен.
    logo = motion.normalize_layers([
        {"id": "phone", "type": "video", "start": 0, "end": 6, "x": 0.4, "y": 0.5,
         "props": {"asset_id": 4, "w": 300, "device": "phone"}},
        {"id": "logo", "type": "image", "start": 5, "end": 9, "x": 0.45, "y": 0.5,
         "props": {"asset_id": 6, "w": 900}},
    ])
    assert [w for w in lint_layers(logo, 9.0, canvas=True, sizes={**SIZES, "6": (1000, 1000)}, fmt="16:9")
            if GAP in w]


def test_combo_overlap_in_9x16_unchanged():
    layers = _combo(0.45, fmt_w=1080.0)
    tall = lint_layers(layers, 6.0, canvas=True, sizes=SIZES)
    gap = [w for w in tall if GAP in w]
    assert gap and gap[0].startswith("phone и window: ") and "сцена-комбо" not in " ".join(tall), tall
