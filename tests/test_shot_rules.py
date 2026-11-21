"""Правила кадра (app/shot_rules.py): каждое правило пропускает хороший кадр и ловит заведомо плохой
(отрицательный контроль). Блокинг: Y вверх, сантиметры."""

import math

import numpy as np

from app import aivideo_blocking_camera as BC
from app import camera3d as C3
from app import shot_rules as SR


def cam(pos, target, fov=40.0, **kw):
    return {"pos": list(pos), "target": list(target), "fov": fov, **kw}


A, B = [-60.0, 160.0, 0.0], [60.0, 160.0, 0.0]        # двое лицом друг к другу, линия действия по X


# --------------------------------------------------------------------------- ось 180 и порядок по X
def test_axis_180_same_side_passes_and_jump_over_the_line_fails():
    front = {"idx": 1, "camera": cam([0, 160, 300], [0, 160, 0]), "actors": {"a": A, "b": B}}
    ots = {"idx": 2, "camera": cam([-150, 165, 120], [60, 160, 0]), "actors": {"a": A, "b": B}}
    assert SR.axis_180(front, ots) == []
    behind = {"idx": 2, "camera": cam([30, 160, -300], [0, 160, 0]), "actors": {"a": A, "b": B}}  # отрицательный
    found = SR.axis_180(front, behind)
    assert found and found[0]["rule"] == "axis_180" and found[0]["pair"] == ["a", "b"]
    # исключения: камера на самой линии, тег cross_line, камера пересекает линию внутри плана
    on_line = {"idx": 2, "camera": cam([-400, 160, -40], [0, 160, 0]), "actors": {"a": A, "b": B}}
    assert SR.axis_180(front, on_line) == []
    assert SR.axis_180(front, {**behind, "tags": ["cross_line"]}) == []
    moving = {**front, "camera_end": cam([0, 160, -300], [0, 160, 0])}
    assert SR.axis_180(moving, behind) == []


def test_screen_order_keeps_who_is_left():
    s1 = {"camera": cam([0, 160, 300], [0, 160, 0]), "actors": {"a": A, "b": B}}
    s2 = {"camera": cam([40, 170, 250], [0, 160, 0]), "actors": {"a": A, "b": B}}
    assert SR.screen_order(s1, s2) == []
    swapped = {"camera": cam([0, 160, -300], [0, 160, 0]), "actors": {"a": A, "b": B}}   # отрицательный: сзади
    f = SR.screen_order(s1, swapped)
    assert f and f[0]["rule"] == "screen_order"
    seq = SR.sequence_rules([{"idx": 1, "scene": "kitchen", **s1}, {"idx": 2, "scene": "kitchen", **swapped},
                             {"idx": 3, "scene": "street", **s1}])
    assert {x["rule"] for x in seq} == {"axis_180", "screen_order"} and all(x["shots"] == [1, 2] for x in seq)


# --------------------------------------------------------------------------- композиция
def test_look_room_in_front_of_the_gaze():
    head = [0.0, 160.0, 0.0]
    look_right = [1.0, 0.0, 0.0]
    good = cam([35, 160, 300], [35, 160, 0])            # голова левее центра, смотрит вправо — место перед взглядом
    assert SR.look_room(good, head, look_right) == []
    bad = cam([-35, 160, 300], [-35, 160, 0])           # отрицательный: упёрся носом в край кадра
    f = SR.look_room(bad, head, look_right, who="Мира")
    assert f and f[0]["rule"] == "look_room" and f[0]["ratio"] < 1.0
    assert SR.look_room(bad, head, [0.0, 0.0, 1.0]) == []   # смотрит в камеру — правило не применяется


def test_eye_line_thirds_or_golden_and_close_head_room():
    eyes = [0.0, 160.0, 0.0]
    fov = 40.0
    tv = math.tan(math.radians(fov / 2))
    def at(y_from_top, dist=200.0):                     # камера так, чтобы глаза были на y_from_top кадра
        off = (1 - 2 * y_from_top) * tv * dist
        return cam([0, 160 - off, dist], [0, 160 - off, 0], fov)
    assert SR.eye_line(at(0.36), eyes, "medium") == []
    assert SR.eye_line(at(0.40), eyes, "close") == []
    f = SR.eye_line(at(0.55), eyes, "close")            # отрицательный: глаза ниже середины
    assert f and abs(f[0]["eyes_from_top"] - 0.55) < 0.01
    assert SR.eye_line(at(0.55), eyes, "wide") == []    # общий план не проверяется
    top = [0.0, 172.0, 0.0]
    assert SR.head_room_max(at(0.33, 80), top, "close") == []
    loose = at(0.62, 80)                                # отрицательный: пустота над головой на крупном
    f = SR.head_room_max(loose, top, "close")
    assert f and f[0]["head_room"] > SR.HEAD_ROOM_MAX


def test_balance_unless_tagged():
    assert SR.balance([(-0.3, 0.1), (0.4, 0.1)]) == []
    lop = [(-0.8, 0.12), (-0.7, 0.05)]                  # отрицательный: всё у левого края
    assert SR.balance(lop)[0]["rule"] == "balance"
    assert SR.balance(lop, ["imbalance"]) == []


def test_angle_by_intent():
    eyes = [0.0, 160.0, 0.0]
    low_up = cam([0, 90, 200], [0, 160, 0])             # ниже глаз, смотрит вверх ~19°
    high_down = cam([0, 260, 200], [0, 160, 0])
    level = cam([0, 160, 200], [0, 160, 0])
    assert SR.angle_intent(low_up, eyes, "power") == []
    assert SR.angle_intent(level, eyes, "power")[0]["rule"] == "angle_intent"       # отрицательный
    assert SR.angle_intent(high_down, eyes, "weak") == []
    assert SR.angle_intent(low_up, eyes, "weak")                                     # отрицательный
    assert SR.angle_intent(level, eyes, "neutral") == []
    assert SR.angle_intent(high_down, eyes, "neutral")                               # отрицательный
    assert SR.angle_intent({**level, "roll": 12}, eyes, "uneasy") == []
    assert SR.angle_intent(level, eyes, "uneasy")                                    # отрицательный: без крена
    assert SR.angle_intent({**level, "roll": 8}, eyes, "")                           # отрицательный: крен без uneasy
    assert SR.normalize_intent("Тревога") == "uneasy" and SR.normalize_intent("???") == ""


def test_wide_lens_close_and_near_warns():
    face = [0.0, 160.0, 0.0]
    near_wide = cam([0, 160, 60], [0, 160, 0], lens=20)
    f = SR.wide_lens(near_wide, face, "close")
    assert f and f[0]["level"] == "warning" and f[0]["lens_mm"] == 20
    assert SR.wide_lens(cam([0, 160, 180], [0, 160, 0], lens=50), face, "close") == []
    assert SR.wide_lens(near_wide, face, "wide") == []
    # Blender: метры, Z вверх — та же камера серии «лицо» 20 мм в 0.6 м
    assert SR.wide_lens({"pos": [0, -0.6, 1.5], "target": [0, 0, 1.5], "lens": 20}, [0, 0, 1.5], "close", unit=1.0)
    assert abs(SR.lens_mm({"fov": BC.lens_fov(35)}) - 35) < 0.1


# --------------------------------------------------------------------------- картинка
def picture(hero_dark=True, tree_contrast=True):
    """Кадр 160×90: серый фон, герой — пятно справа, «дерево» — контрастные полосы слева."""
    img = np.full((160, 90, 3), 0.5)
    mask = np.zeros((160, 90), bool)
    mask[60:120, 55:80] = True
    img[mask] = 0.12 if hero_dark else 0.5
    if tree_contrast:
        for x in range(5, 30, 4):
            img[20:140, x:x + 2] = 1.0
            img[20:140, x + 2:x + 4] = 0.0
    return img, mask


def test_most_contrasty_spot_is_the_hero_and_merge_asks_for_fog():
    img, mask = picture(hero_dark=True, tree_contrast=False)
    assert SR.hero_contrast(img, mask) == []
    bad_img, bad_mask = picture(hero_dark=True, tree_contrast=True)      # отрицательный: дерево контрастнее
    f = SR.hero_contrast(bad_img, bad_mask)
    assert f and f[0]["rule"] == "hero_contrast" and f[0]["spot"][0] < 0.4
    assert SR.figure_ground(img, mask) == []
    flat, fmask = picture(hero_dark=False, tree_contrast=False)          # отрицательный: герой цвета фона
    depth = np.where(fmask, 3.0, 9.0)
    f = SR.figure_ground(flat, fmask, depth)
    assert f and f[0]["fog"]["near"] > 3.0 and f[0]["fog"]["far"] > f[0]["fog"]["near"]
    fog = SR.depth_fog(np.array([0.0, 3.0, 5.0, 50.0]), 3.0, 7.5)
    assert fog.min() >= 0 and fog.max() <= 1 and fog[1] == 0 and fog[3] == 1


# --------------------------------------------------------------------------- блокинг: правила в check_frame
def actor(aid, x, facing, main=False):
    return {"id": aid, "role": "actor", "label": aid, "kind": "human", "pose": "standing",
            "center": [x, 85.0, 0.0], "half": [22.0, 85.0, 14.0], "rot": facing, "height": 170.0,
            "head": [x, 160.0, 0.0], "head_r": 11.0, **({"main": True} if main else {})}


ROOM = {"width": 600.0, "depth": 600.0, "height": 280.0}


def test_check_frame_reports_composition_without_changing_ok():
    boxes = [actor("Мира", -35.0, 90.0, main=True)]    # левее центра, смотрит вправо
    good = cam([0, 150, 260], [0, 140, 0])
    check = BC.check_frame(good, boxes, ROOM, "medium")
    assert "composition" in check and check["composition"]["findings"] is not None
    assert not any(f["rule"] == "look_room" for f in check["composition"]["findings"])
    nose = BC.check_frame(cam([-90, 150, 260], [-90, 140, 0]), boxes, ROOM, "medium")   # отрицательный: носом в край
    assert any(f["rule"] == "look_room" for f in nose["composition"]["findings"])
    assert not any("взгляд" in p for p in nose["problems"])     # подсказка, а не ошибка самоконтроля блокинга
    tilted = BC.check_frame({**good, "roll": 10}, boxes, ROOM, "medium", intent="power")
    assert {"angle_intent"} <= {f["rule"] for f in tilted["composition"]["findings"]}


# --------------------------------------------------------------------------- кривые камеры (camera3d)
def test_camera_presets_ease_fast_start_long_settle():
    for name in ("parallax_push", "orbit", "dolly_zoom", "tilt_reveal", "float"):
        for _frac, _fields, ease in C3.camera_shape(name, 1.0):
            if not ease:
                continue
            prof = C3.ease_profile(ease)
            assert prof["peak_at"] <= 0.30, (name, ease, prof)
            assert prof["decel"] > prof["accel"], (name, ease, prof)
    sym = C3.ease_profile("bezier(0.45,0,0.55,1)")              # отрицательный: симметричная «3D-шная» кривая
    assert sym["peak_at"] > 0.4 and not C3.ease_ok("bezier(0.45,0,0.55,1)")
    assert C3.ease_ok(C3.CAM)
