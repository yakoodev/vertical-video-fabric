"""Игры от первого лица: окно 9:16 на прицеле (app/first_person.py, auto_focus).

Клипы 583–585 (Apex): auto_focus без Gemini вёл окно по движению — x 0.51–0.58 со склейками,
прицел уезжал с центра. Теперь: по названию игры или по прицелу в центре — неподвижный x.
"""

from __future__ import annotations

import numpy as np

from app.first_person import crosshair_x, first_person_hint, source_texts

H, W = 180, 320


def _frames(n=12, cross_at=(0.5, 0.5), static_scene=False, hud_corner=False, seed=1):
    """Сцена — шум, который меняется от кадра к кадру (игра движется); прицел — белый крест
    с тёмной обводкой, неподвижный."""
    rng = np.random.default_rng(seed)
    base = rng.integers(0, 256, size=(H, W)).astype(np.float32)
    out = []
    for _ in range(n):
        frame = base.copy() if static_scene else rng.integers(0, 256, size=(H, W)).astype(np.float32)
        if cross_at is not None:
            cx, cy = int(W * cross_at[0]), int(H * cross_at[1])
            frame[cy - 4:cy + 5, cx - 2:cx + 3] = 0
            frame[cy - 2:cy + 3, cx - 4:cx + 5] = 0
            frame[cy - 3:cy + 4, cx - 1:cx + 2] = 255
            frame[cy - 1:cy + 2, cx - 3:cx + 4] = 255
        if hud_corner:
            frame[150:175, 10:90] = 40
            frame[155:170, 15:85] = 230
        out.append(frame)
    return out


def test_crosshair_found_in_center_of_moving_scene():
    x = crosshair_x(_frames())
    assert x is not None and abs(x - 0.5) < 0.01
    # HUD в углу не мешает.
    assert crosshair_x(_frames(hud_corner=True)) is not None


def test_no_crosshair_no_lock():
    assert crosshair_x(_frames(cross_at=None)) is None  # движение без прицела — обычный автофокус
    assert crosshair_x(_frames(cross_at=None, hud_corner=True)) is None  # неподвижный HUD — не в центре
    assert crosshair_x(_frames(static_scene=True)) is None  # меню/пауза: вокруг центра ничего не движется
    assert crosshair_x(_frames(cross_at=(0.7, 0.5))) is None  # рисунок не в центре — не прицел
    assert crosshair_x(_frames(n=3)) is None  # мало кадров — не судим


def test_first_person_hint_by_game_name():
    assert first_person_hint("Apex_Legends_2026.10.20 - 21.33.mp4") == "apex"
    assert first_person_hint("ApexLegends 1080p 60fps.mp4") == "apex"
    assert first_person_hint("CallOfDuty_warzone.mp4") in ("call of duty", "warzone")
    assert first_person_hint(None, "", {"title": "VALORANT ranked"}) == "valorant"
    assert first_person_hint("CS2 эйс") == "cs2"
    assert first_person_hint("стрим — шутер от первого лица") == "шутер от первого лица"
    # «60 fps» — частота кадров, не жанр; третье лицо — не прицел.
    assert first_person_hint("vlog 60 fps.mp4", "Fortnite squad win") is None
    assert first_person_hint() is None
    src = {"original_filename": "rec.mp4", "metadata_json": '{"format": {"tags": {"title": "Overwatch 2"}}}'}
    assert first_person_hint(*source_texts(src)) == "overwatch"
    assert first_person_hint(*source_texts({"original_filename": "rec.mp4"}, {"title": "Apex: тройной нокдаун"})) \
        == "apex"
