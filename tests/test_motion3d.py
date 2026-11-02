import math

import pytest

from app import motion3d as m


# ---------------- клипы

def _track():
    return (m.ClipTrack()
            .add(m.ClipSegment("Idle_A", 0.0, 1.0))
            .add(m.ClipSegment("Walking_A", 1.0, 0.85, blend=0.3))
            .add(m.ClipSegment("Idle_B", 2.5, 1.7, blend=0.4)))


def test_clip_weights_sum_to_one_and_crossfade():
    tr = _track()
    for i in range(0, 400):
        t = i / 100
        ws = tr.weights_at(t)
        assert ws, t
        assert abs(sum(w for _, _, w in ws) - 1.0) < 1e-9
    assert [c for c, _, _ in tr.weights_at(0.5)] == ["Idle_A"]
    mid = dict((c, w) for c, _, w in tr.weights_at(1.15))
    assert 0.2 < mid["Walking_A"] < 0.8 and "Idle_A" in mid
    assert [c for c, _, _ in tr.weights_at(1.4)] == ["Walking_A"]
    assert [c for c, _, _ in tr.weights_at(3.0)] == ["Idle_B"]


def test_clip_weights_change_smoothly():
    tr = _track()
    prev = None
    for i in range(0, 3000):
        t = i / 1000
        w = dict((c, w) for c, _, w in tr.weights_at(t))
        if prev is not None:
            for c in set(w) | set(prev):
                assert abs(w.get(c, 0) - prev.get(c, 0)) < 0.01, (t, c)
        prev = w


def test_previous_clip_keeps_playing_during_blend():
    tr = _track()
    a = dict((c, lt) for c, lt, _ in tr.weights_at(1.05))
    b = dict((c, lt) for c, lt, _ in tr.weights_at(1.15))
    assert b["Idle_A"] != a["Idle_A"]          # не замер на последнем кадре


def test_clip_local_time_loop_and_hold():
    loop = m.ClipSegment("Walk", 1.0, 0.8)
    assert loop.local_time(1.9) == pytest.approx(0.1)
    once = m.ClipSegment("Hit", 1.0, 0.5, loop=False)
    assert once.local_time(3.0) == pytest.approx(0.5)
    assert m.ClipSegment("X", 0, 1.0, offset=0.25, speed=2).local_time(0.1) == pytest.approx(0.45)


def test_clip_segments_must_be_ordered():
    tr = m.ClipTrack().add(m.ClipSegment("A", 1.0, 1))
    with pytest.raises(ValueError):
        tr.add(m.ClipSegment("B", 0.5, 1))


# ---------------- кривые

def test_curve_holds_and_eases():
    c = m.Curve([(1.0, 0.0), (2.0, 1.0)])
    assert c.at(0.0) == 0.0 and c.at(5.0) == 1.0
    assert c.at(1.5) == pytest.approx(0.5)
    assert c.at(1.1) < 0.1                     # мягкий старт
    v = m.Curve([(0, (0, 0, 0)), (1, (2, 4, 6))]).at(0.5)
    assert v == pytest.approx((1, 2, 3))
    assert m.Curve([(0, 0), (1, 1)], ease="step").at(0.99) == 0


# ---------------- IK

def test_two_bone_ik_reaches_target_with_correct_lengths():
    root, l1, l2 = (0.0, 0.0, 1.0), 0.3, 0.28
    target = (0.2, -0.3, 0.95)
    elbow, hand, ok = m.two_bone_ik(root, target, l1, l2, pole=(0.0, 0.5, 0.5))
    assert ok
    assert m.v_len(m.v_sub(hand, target)) < 1e-9
    assert m.v_len(m.v_sub(elbow, root)) == pytest.approx(l1)
    assert m.v_len(m.v_sub(hand, elbow)) == pytest.approx(l2)


def test_two_bone_ik_elbow_bends_towards_pole():
    root = (0.0, 0.0, 0.0)
    target = (0.0, -0.4, 0.0)
    elbow, _, _ = m.two_bone_ik(root, target, 0.25, 0.25, pole=(0.0, -0.2, -1.0))
    assert elbow[2] < -0.05                     # локоть вниз, к полюсу
    elbow2, _, _ = m.two_bone_ik(root, target, 0.25, 0.25, pole=(0.0, -0.2, 1.0))
    assert elbow2[2] > 0.05


def test_two_bone_ik_out_of_reach_points_at_target():
    root = (0.0, 0.0, 0.0)
    elbow, hand, ok = m.two_bone_ik(root, (1.0, 0.0, 0.0), 0.25, 0.25, pole=(0, 0, -1))
    assert not ok
    assert hand[0] == pytest.approx(0.4995) and abs(hand[1]) < 1e-9
    assert elbow[0] > 0


def test_stand_point_puts_button_in_comfort_reach():
    button = (-0.7, -1.06, 0.78)
    normal = (0.0, 1.0, 0.0)                    # стена спереди (−Y), нормаль в комнату
    shoulder = (0.13, 0.0, 0.66)                # правое плечо: смотрим в −Y, справа это −X…
    reach = 0.45
    root = m.stand_point(button, normal, shoulder, reach, comfort=0.8)
    # пересчёт плеча в мир: смотрим в −Y → right = (−1, 0, 0)
    fwd = (0.0, -1.0, 0.0)
    right = (fwd[1], -fwd[0], 0.0)
    sh = (root[0] + shoulder[0] * right[0] + shoulder[1] * fwd[0],
          root[1] + shoulder[0] * right[1] + shoulder[1] * fwd[1], shoulder[2])
    assert m.v_len(m.v_sub(button, sh)) == pytest.approx(0.8 * reach, abs=1e-6)
    assert root[1] > button[1]                  # стоим в комнате, а не в стене
    assert right == (-1.0, 0.0, 0.0)


def test_stand_point_side_press_keeps_shoulder_opposite_button():
    # панель на левой стене (нормаль +X), герой смотрит в −Y (к дверям), жмёт правой рукой вбок
    button = (-0.824, -0.25, 0.715)
    shoulder = (0.127, 0.0, 0.664)
    root = m.stand_point(button, (1.0, 0.0, 0.0), shoulder, 0.40, comfort=0.8, facing=(0.0, -1.0, 0.0))
    sh = (root[0] - shoulder[0], root[1], shoulder[2])        # right = (−1, 0, 0)
    assert sh[1] == pytest.approx(button[1])                    # плечо напротив кнопки
    assert m.v_len(m.v_sub(button, sh)) == pytest.approx(0.32, abs=1e-6)
    assert root[0] > button[0] + 0.3                            # корпус не в стене
    ahead = m.stand_point(button, (1.0, 0.0, 0.0), shoulder, 0.40, comfort=0.8, facing=(0.0, -1.0, 0.0), ahead=0.12)
    sh2 = (ahead[0] - shoulder[0], ahead[1], shoulder[2])
    assert sh2[1] - button[1] == pytest.approx(0.12)            # кнопка на 12 см впереди плеча
    assert m.v_len(m.v_sub(button, sh2)) == pytest.approx(0.32, abs=1e-6)
    assert m.heading_of((0.0, -1.0, 0.0)) == 0.0
    assert m.heading_of((1.0, 0.0, 0.0)) == pytest.approx(math.pi / 2)


def test_reach_press_timeline():
    r = m.Reach(button=(0.0, -1.0, 0.8), normal=(0.0, 1.0, 0.0), t_start=1.0, t_contact=1.6,
                t_release=1.9, t_end=2.5, rest=(0.0, -0.6, 0.5))
    assert r.influence(0.9) == 0.0 and r.influence(1.75) == 1.0 and r.influence(2.6) == 0.0
    assert r.target_at(1.0) == pytest.approx((0.0, -0.6, 0.5))
    press = r.target_at(1.7)
    assert press[1] < -1.0                      # вдавлено в кнопку
    assert r.pressed(1.7) and not r.pressed(1.5)
    # касание случается ровно в t_contact, до него — перед кнопкой
    assert r.target_at(1.55)[1] > -1.0
    prev = None
    for i in range(100, 260):
        t = i / 100
        p = r.target_at(t)
        if prev:
            assert m.v_len(m.v_sub(p, prev)) < 0.05      # без телепорта кисти
        prev = p


# ---------------- взгляд

def test_look_angles_basic():
    eye = (0.0, 0.0, 1.0)
    yaw, pitch = m.look_angles(eye, 0.0, (0.0, -1.0, 1.0))
    assert yaw == pytest.approx(0.0) and pitch == pytest.approx(0.0)
    yaw, pitch = m.look_angles(eye, 0.0, (0.0, -1.0, 2.0))
    assert pitch == pytest.approx(math.pi / 4)
    yaw, _ = m.look_angles(eye, 0.0, (1.0, -1.0, 1.0))   # поворот против ч.с. от −Y ведёт в +X
    assert yaw == pytest.approx(math.pi / 4)
    yaw2, _ = m.look_angles(eye, math.pi / 4, (1.0, -1.0, 1.0))
    assert yaw2 == pytest.approx(0.0)


def test_split_gaze_respects_limits():
    lim = m.GazeLimits()
    (hy, hp), (ey, ep) = m.split_gaze(math.radians(20), math.radians(10), lim)
    assert hy + ey == pytest.approx(math.radians(20))
    assert hp + ep == pytest.approx(math.radians(10))
    (hy, hp), (ey, ep) = m.split_gaze(math.radians(170), math.radians(80), lim)
    assert abs(hy) <= lim.head_yaw + 1e-9 and abs(ey) <= lim.eye_yaw + 1e-9
    assert hp <= lim.head_pitch_up + 1e-9 and ep <= lim.eye_pitch + 1e-9


def test_spring_follow_no_overshoot_and_converges():
    samples = [(0.0,)] * 5 + [(1.0,)] * 120
    out = m.spring_follow(samples, 1 / 30, halflife=0.15)
    vals = [o[0] for o in out]
    assert max(vals) <= 1.0 + 1e-6
    assert vals[5] < 0.3                       # не прыгает за целью
    assert vals[-1] == pytest.approx(1.0, abs=1e-3)
    steps = [b - a for a, b in zip(vals, vals[1:])]
    assert max(steps) < 0.25


def test_saccade_jumps_fast():
    c = m.Curve([(0.0, (0, 0, 0)), (1.0, (1, 0, 0))])
    out = m.saccade_track([0.5, 0.95, 0.97, 1.0, 1.5], c, dur=0.06)
    assert out[0] == (0, 0, 0)
    assert out[-1] == (1, 0, 0)
    assert 0 < out[2][0] < 1


# ---------------- моргание

def test_blinks_are_spaced_and_forced_included():
    times = m.blink_times(10.0, seed=1, forced=[4.85])
    assert 4.85 in times
    rnd = [t for t in times if t != 4.85]
    assert all(abs(t - 4.85) > 0.6 for t in rnd)
    gaps = [b - a for a, b in zip(rnd, rnd[1:])]
    assert all(g >= 1.8 - 1e-9 for g in gaps)
    assert 2 <= len(times) <= 7
    assert m.blink_times(10.0, seed=1) == m.blink_times(10.0, seed=1)


def test_blink_amount_shape():
    assert m.blink_amount(0.0, [1.0]) == 0
    assert m.blink_amount(1.08, [1.0]) == 1.0
    assert 0 < m.blink_amount(1.17, [1.0]) < 1
    assert m.blink_amount(1.5, [1.0]) == 0


# ---------------- шаги

def test_stride_speed_matches_planted_foot():
    fps = 30.0
    speed = 0.6
    n = 60
    left, right = [], []
    for i in range(n):
        ph = (i % 30) / 30
        # полцикла левая стоит и едет назад, правая в воздухе едет вперёд, потом наоборот
        if ph < 0.5:
            yl = -0.15 + speed * (i % 30) / fps      # −Y — вперёд; едет назад = +Y
            left.append((0.1, yl, 0.0)); right.append((-0.1, -yl, 0.08))
        else:
            k = (i % 30) - 15
            yr = -0.15 + speed * k / fps
            right.append((-0.1, yr, 0.0)); left.append((0.1, -yr, 0.08))
    assert m.stride_speed(left, right, fps) == pytest.approx(speed, rel=1e-6)


def test_travel_profile_follows_walk_weight():
    w = [0.0] * 5 + [0.5, 1.0, 1.0, 1.0, 0.5] + [0.0] * 5
    prof = m.travel_profile(w, 1 / 30, 1.0)
    assert prof[4] == 0.0 and prof[-1] == 1.0
    assert all(b >= a for a, b in zip(prof, prof[1:]))
    assert prof[7] - prof[6] > prof[5] - prof[4]      # разгон


def test_walk_duration_rounds_to_steps():
    assert m.walk_duration(0.6, 0.5, 0.8) == pytest.approx(1.2)
    assert m.walk_duration(0.1, 0.5, 0.8) == pytest.approx(0.4)     # минимум один шаг
    assert m.walk_duration(1.0, 0.0, 0.8) == 0.0


# ---------------- камера и свет

def _shots():
    return m.ShotList([
        m.Shot(0.0, 2.0, (0, 0, 1), (0, -0.5, 1), (0, -2, 1), (0, -2, 1), 30, 35, name="wide"),
        m.Shot(2.0, 4.0, (1, 0, 1), (1, 0, 1.2), (0, -1, 1), (0, -1, 1), 50, name="ots"),
    ])


def test_shot_smooth_inside_and_cut_between():
    sl = _shots()
    assert sl.cuts() == [2.0]
    prev = None
    for i in range(0, 199):
        c = sl.at(i / 100)
        if prev:
            assert m.v_len(m.v_sub(c["pos"], prev["pos"])) < 0.02
        prev = c
    assert sl.at(1.999)["shot"] == "wide" and sl.at(2.0)["shot"] == "ots"
    assert sl.at(0.0)["lens"] == 30 and sl.at(1.9999)["lens"] == pytest.approx(35, abs=0.01)
    assert sl.at(2.5)["focus_dist"] == pytest.approx(math.sqrt(1 + 1 + (sl.at(2.5)["pos"][2] - 1) ** 2))


def test_overlapping_shots_rejected():
    with pytest.raises(ValueError):
        m.ShotList([m.Shot(0, 2, (0, 0, 0), (0, 0, 0), (0, 1, 0), (0, 1, 0)),
                    m.Shot(1.5, 3, (0, 0, 0), (0, 0, 0), (0, 1, 0), (0, 1, 0))])


def test_flicker_goes_out_and_stays_out():
    assert m.flicker(0.5, 1.0, 2.0) == 1.0
    vals = [m.flicker(1.0 + i / 100, 1.0, 2.0) for i in range(100)]
    assert min(vals) < 0.5 and max(vals) == 1.0
    assert all(m.flicker(2.0 + i / 10, 1.0, 2.0) == 0.0 for i in range(10))
