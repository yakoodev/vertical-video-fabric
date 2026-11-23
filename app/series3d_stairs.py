"""3D-серия: лестничная клетка (ч.6 «Лифт», «Этаж, которого нет») — марши вниз, площадки этажей с номерами краской,
и где под ногами пол: корень героя по высоте и цели стоп на ступенях.

Чистый Python без ``bpy``: ту же раскладку берут сцена Blender (``tools/blender/series_floor22.build_stairwell`` —
марши из модели Kenney ``stairsOpen``), задание (``series3d_blender_perform``) и тесты.

**Раскладка** (система места, начало — ``LOCS["stairwell"]["origin"]``; курс 0 — лицом к −Y). Клетка «туда-обратно»:
на каждом этаже площадка ``y ∈ [0, LAND_D]`` во всю ширину (номер этажа краской — на задней стене ``y = LAND_D``,
справа, над маршем, по которому приходят сверху); марш A уходит вниз к −Y по левой половине (``x < 0``) до
промежуточной площадки ``y ∈ [−FLIGHT_RUN − MID_D, −FLIGHT_RUN]``, с неё марш B — вниз к +Y по правой половине на
площадку этажа ниже. Этажи сверху вниз — ``FLOORS`` (третий снова «22»: номер не меняется), этаж ``i`` — на высоте
``−i·FLOOR_H``.

**Пол под ногами** (``ground``): верх ступени/площадки под точкой — из нескольких поверхностей друг над другом
берётся та, на которой стоят (не выше прошлой опоры + ``STEP_UP``).

**Стопы на ступенях** (``plan_ground``): по клипу (бег ``Running_A``, шаг ``Walking_C``, покой) стопы в каждом кадре;
стопа на опоре стоит на ступени под всей подошвой (пятка… носок — берётся самая высокая), корень вниз идёт без рывков:
к новой опоре он приходит в момент касания (вниз по лестнице колено опорной ноги сгибается, ничего не висит и не
проваливается). Стопа в полёте идёт от ступени, с которой оторвалась, к той, на которую встанет, не ниже ступеней
под собой. Сцена ставит цели IK ног: ``стопа клипа + (0, 0, dz)`` (``feet_dz``).
"""

from __future__ import annotations

import math

from app import motion3d as m
from app.series3d_blender_stage import S, facing

FPS = 30
LOC = "stairwell"
ORIGIN = (300.0, 0.0)

# Марш — модель Kenney ``stairsOpen`` (11 подъёмов, верхняя ступень вровень с площадкой): ед. модели → м по осям
KENNEY = {"run": 1.823, "rise": 1.34, "width": 0.79, "tread": 0.164, "nose": 0.02, "steps": 11, "slab": 0.031}
SX, SZ, SW = 1.732, 1.25, 1.70           # вдоль марша, по высоте, по ширине (ступень 0.284 × 0.152 м, марш 1.34 м)
TREAD = KENNEY["tread"] * SX              # 0.284 м: бег героя — шаг 0.568 м, через ступень
RISE = KENNEY["rise"] / KENNEY["steps"] * SZ   # 0.152 м
NOSE = KENNEY["nose"] * SX
STEPS = KENNEY["steps"]
FLIGHT_RUN = KENNEY["run"] * SX           # 3.157 м
FLIGHT_DROP = KENNEY["rise"] * SZ         # 1.675 м — полэтажа
FLIGHT_W = KENNEY["width"] * SW           # 1.343 м
FLOOR_H = 2 * FLIGHT_DROP                 # 3.35 м
GAP = 0.10                                # от оси до внутреннего края марша (между маршами — пролёт 0.2 м)
X_IN = GAP + FLIGHT_W + 0.017             # внутренняя грань боковой стены: марш её не касается
LAND_D = 1.50                             # площадка этажа
MID_D = 1.40                              # промежуточная площадка
WALL_T = 0.093                            # толщина стены (Kenney wall 0.05 ед. × 1.86)
SLAB_T = 0.12                             # толщина плиты площадки
Y_BACK = LAND_D + 0.005                   # задняя стена площадки этажа (грань)
Y_MID_BACK = -FLIGHT_RUN - MID_D - 0.005  # задняя стена промежуточной площадки (грань)
FLOORS = ("22", "21", "20", "22", "")     # сверху вниз; последний — площадка в темноте внизу (без номера)
STEP_UP = 0.4                             # выше прошлой опоры — не та поверхность (этаж над головой)
NUMBER_AT = (0.78, 1.62)                  # номер этажа на задней стене: x, высота над площадкой (м) — над головой
NUMBER_H = 0.62                           # высота цифр


def floor_z(i: int) -> float:
    return -i * FLOOR_H


def mid_z(i: int) -> float:
    """Промежуточная площадка под этажом ``i``."""
    return floor_z(i) - FLIGHT_DROP


def local(p: tuple) -> tuple:
    return (p[0] - ORIGIN[0], p[1] - ORIGIN[1], *p[2:])


def world(p: tuple) -> tuple:
    return (p[0] + ORIGIN[0], p[1] + ORIGIN[1], *p[2:])


def number_point(i: int, dz: float = 0.0) -> tuple[float, float, float]:
    """Середина номера этажа ``i`` на стене (мир)."""
    return world((NUMBER_AT[0], Y_BACK - 0.01, floor_z(i) + NUMBER_AT[1] + dz))


# --------------------------------------------------------------------------- поверхности

def _tread_top(k: int, base: float) -> float:
    return base + RISE * (k + 1)


def surfaces(x: float, y: float, shrink: float = 0.0) -> list[float]:
    """Высоты всего, на что можно встать в точке места ``(x, y)`` (система места): площадки и ступени маршей."""
    out: list[float] = []
    if abs(x) > X_IN:
        return out
    n = len(FLOORS)
    if 0.0 <= y <= Y_BACK:
        out += [floor_z(i) for i in range(n)]
    if Y_MID_BACK <= y <= -FLIGHT_RUN:
        out += [mid_z(i) for i in range(n - 1)]
    if -FLIGHT_RUN <= y <= 0.0:
        if -GAP - FLIGHT_W <= x <= -GAP:
            # марш A: снизу (у промежуточной площадки, y = −FLIGHT_RUN) вверх к площадке этажа; ступень k — от низа
            u = y + FLIGHT_RUN
            for i in range(n - 1):
                out += [_tread_top(k, mid_z(i)) for k in _treads_at(u, shrink)]
        elif GAP <= x <= GAP + FLIGHT_W:
            # марш B: низ — у площадки этажа ниже (y = 0), верх — у промежуточной (y = −FLIGHT_RUN)
            u = -y
            for i in range(n - 1):
                out += [_tread_top(k, floor_z(i + 1)) for k in _treads_at(u, shrink)]
    return out


def _treads_at(u: float, shrink: float = 0.0) -> list[int]:
    """Ступени марша под точкой ``u`` м от низа (с выступом носка: передний край ступени выше заходит за следующую)."""
    ks = []
    for k in range(STEPS):
        a, b = k * TREAD + shrink, (k + 1) * TREAD + NOSE - shrink
        if a - 1e-6 <= u <= b + 1e-6:
            ks.append(k)
    return ks


def ground(p: tuple, z_ref: float, shrink: float = 0.0) -> float:
    """Пол под точкой мира ``p`` (x, y): верх поверхности, на которой стоят, — самая высокая не выше ``z_ref +
    STEP_UP``. Нет пола (пролёт между маршами) — ``z_ref``."""
    x, y = local(p)[:2]
    cand = [z for z in surfaces(x, y, shrink) if z <= z_ref + STEP_UP]
    return max(cand) if cand else z_ref


def footprint_ground(ankle: tuple, heading: float, z_ref: float) -> float:
    """Пол под всей подошвой (пятка… носок по курсу): самая высокая ступень — носок не уходит в ступень выше."""
    fx, fy = facing(heading)
    # подошва ботинка KayKit у героя: пятка ~7 см за щиколоткой, носок ~11 см впереди (замер stairs_feet ч.6)
    # края ступеней в модели Kenney совпадают с раскладкой до ~3 см (замер лучом в Blender): пятка и носок встают на
    # ступень выше, только если зашли на неё больше чем на EDGE — иначе стопа висела бы на подъём над своей ступенью
    out = ground(ankle, z_ref)
    for d in FOOT_SAMPLES:
        x, y = local((ankle[0] + fx * d, ankle[1] + fy * d))[:2]
        cand = [z for z in surfaces(x, y, EDGE) if z <= z_ref + STEP_UP]
        if cand:                      # пятка/носок над пролётом или у края — не в счёт
            out = max(out, max(cand))
    return out


FOOT_SAMPLES = (-0.075, -0.04, 0.0, 0.045, 0.09, 0.115)
EDGE = 0.035


def is_stairs(loc: str | None) -> bool:
    return loc == LOC


# --------------------------------------------------------------------------- стопы клипа

def _feet_tables():
    from app.series3d_blender_stage import CLIP_LEN, WALK_FEET
    from app.series3d_chase import RUN_CLIP, RUN_FEET, RUN_LEN

    return {RUN_CLIP: (RUN_FEET, RUN_LEN), "Walking_C": (WALK_FEET, CLIP_LEN["Walking_C"])}


FOOT_X = 0.1709                 # стопа от оси клипа вбок (ед. без масштаба)
REST_Z = 0.14                   # щиколотка стоящего (ед. без масштаба)


def clip_feet(track: m.ClipTrack, t: float, tables: dict) -> dict[str, tuple[float, float]]:
    """Стопы клипа в момент ``t``: ``{side: (y, z)}`` (ед. без масштаба, y — назад) — смесь клипов по весам; клип без
    таблицы — стоит (стопы под корнем)."""
    acc = {"l": [0.0, 0.0], "r": [0.0, 0.0]}
    tot = 0.0
    for clip, lt, w in track.weights_at(t):
        tab = tables.get(clip)
        if tab is None:
            vals = (0.0, REST_Z, 0.0, REST_Z)
        else:
            feet, length = tab
            n = len(feet) - 1
            i = (lt % length) * FPS
            a, b = feet[int(i) % n], feet[(int(i) + 1) % n]
            k = i - int(i)
            vals = tuple(a[j] + (b[j] - a[j]) * k for j in range(4))
        yl, zl, yr, zr = vals
        acc["l"][0] += yl * w
        acc["l"][1] += zl * w
        acc["r"][0] += yr * w
        acc["r"][1] += zr * w
        tot += w
    if tot <= 0:
        return {"l": (0.0, REST_Z), "r": (0.0, REST_Z)}
    return {s: (v[0] / tot, v[1] / tot) for s, v in acc.items()}


# --------------------------------------------------------------------------- план опоры

def plan_ground(shots: list[dict], times: list[float], root: list, heads: list[float], track: m.ClipTrack,
                rzs: list[float]) -> tuple[list[float], list]:
    """Высота корня и поправки стоп по кадрам там, где план — на лестнице. ``shots`` — планы задания (``t0``, ``t1``,
    ``location``, ``z`` — от какой высоты пола план начинается, если герой склейкой на другом марше).
    → ``(rzs, feet_dz)``: ``feet_dz[f] = (dz левой, dz правой)`` — на сколько цель IK стопы выше стопы клипа (кадры не
    на лестнице — ``None``)."""
    from app.series3d_blender_perform import clip_to_world as _to_world
    from app.series3d_chase import CONTACT_Z

    tables = _feet_tables()
    n = len(times)
    rzs = list(rzs)
    feet_dz: list = [None] * n
    spans = []
    for s_ in shots:
        if not is_stairs(s_.get("location")):
            continue
        f0 = max(0, int(round(float(s_["t0"]) * FPS)))
        f1 = min(n, int(round(float(s_["t1"]) * FPS)))
        if spans and spans[-1][1] == f0 and s_.get("z") is None:
            spans[-1] = (spans[-1][0], f1, spans[-1][2])          # тот же кусок: опора идёт дальше
        else:
            spans.append((f0, f1, s_.get("z")))
    for f0, f1, z_hint in spans:
        if f1 <= f0:
            continue
        z_ref = float(z_hint) if z_hint is not None else ground(root[f0], 0.0)
        feet = []
        for f in range(f0, f1):
            cf = clip_feet(track, times[f], tables)
            pts = {}
            for side, sx in (("l", FOOT_X), ("r", -FOOT_X)):
                y, z = cf[side]
                pts[side] = (_to_world(root[f], heads[f], sx, y), z)
            feet.append(pts)
        # опоры: стопа ниже CONTACT_Z — стоит; пол — под подошвой в момент касания (дальше держится)
        planted = {"l": None, "r": None}
        g = {"l": [None] * (f1 - f0), "r": [None] * (f1 - f0)}
        landings: list[tuple[int, float]] = []
        cur = z_ref
        for k in range(f1 - f0):
            for side in ("l", "r"):
                (xy, z) = feet[k][side]
                if z < CONTACT_Z:
                    if planted[side] is None:
                        planted[side] = footprint_ground(xy, heads[f0 + k], cur)
                        landings.append((k, planted[side]))
                    g[side][k] = planted[side]
                else:
                    planted[side] = None
            low = [g[s][k] for s in ("l", "r") if g[s][k] is not None]
            if low:
                cur = min(low)
        # корень: к каждой новой опоре приходит в момент касания; между — плавно (вниз — сразу после прошлой)
        rz_loc = root_track(landings, f1 - f0, z_ref)
        # стопы в полёте: от ступени отрыва к ступени касания, не ниже ступеней под подошвой
        for side in ("l", "r"):
            arr = g[side]
            k = 0
            while k < len(arr):
                if arr[k] is not None:
                    k += 1
                    continue
                j = k
                while j < len(arr) and arr[j] is None:
                    j += 1
                # стопа в полёте — как в клипе (над полом корня на свою высоту подъёма); выше — только если под ней
                # ступень выше: тогда ровно настолько, чтобы подошва прошла над ней (+1 см), с плавным подъёмом.
                # Ниже клипа не опускаем: ноге тянуться за пределы — IK выворачивал колено (bend_plane)
                # опора стопы в полёте: от ступени отрыва к ступени касания (вниз — во второй половине полёта), не
                # ниже ступеней под подошвой и не ниже корня (ниже — ноге тянуться за пределы, IK выворачивал колено)
                ga = arr[k - 1] if k > 0 else rz_loc[k]
                gb = arr[j] if j < len(arr) else None
                for q in range(k, j):
                    if gb is None:
                        base = ga
                    else:
                        u = (q - k + 1) / (j - k + 1)
                        base = ga + (gb - ga) * m.smoothstep((u - 0.25) / 0.7)
                    xy = feet[q][side][0]
                    under = footprint_ground(xy, heads[f0 + q], max(base, rz_loc[q]))
                    arr[q] = max(base, under, rz_loc[q])
                k = j
        for k in range(f1 - f0):
            f = f0 + k
            rzs[f] = round(rz_loc[k], 4)
            feet_dz[f] = (round(g["l"][k] - rz_loc[k], 4), round(g["r"][k] - rz_loc[k], 4))
    return rzs, feet_dz


# Фонарик мигает (ч.6, концовка): доли «горит/погас» от начала мигания до темноты — неровно, как садится батарея
FLICKER = ((0.0, 1.0), (0.10, 0.0), (0.18, 1.0), (0.42, 0.0), (0.50, 0.85), (0.56, 0.0), (0.66, 1.0), (0.78, 0.0),
           (0.84, 0.6), (0.88, 0.0))


def flicker_spans(t_at: float, t_dark: float) -> list[tuple[float, float, float]]:
    """``[(с, по, горит)]`` от ``t_at`` до ``t_dark`` по ``FLICKER``, дальше — темнота до конца серии."""
    d = max(0.2, t_dark - t_at)
    out = []
    for i, (u, v) in enumerate(FLICKER):
        a = t_at + u * d
        b = t_at + (FLICKER[i + 1][0] if i + 1 < len(FLICKER) else 1.0) * d
        out.append((round(a, 4), round(b, 4), v))
    out.append((round(t_dark, 4), 1e9, 0.0))
    return out


def root_track(landings: list[tuple[int, float]], n: int, z_ref: float) -> list[float]:
    """Высота корня по кадрам куска: к каждой новой опоре (кадр касания, пол) приходит в момент касания, от прошлой —
    плавно (косинус). Вниз по лестнице опорная нога успевает согнуться, новая встаёт ровно на ступень. До первой
    опоры — пол первой (или ``z_ref``)."""
    keys = sorted({k: v for k, v in landings}.items()) or [(0, z_ref)]
    out = []
    j = 0
    for k in range(n):
        while j + 1 < len(keys) and keys[j + 1][0] <= k:
            j += 1
        prev = keys[j]
        nxt = keys[j + 1] if j + 1 < len(keys) and prev[0] <= k else None
        if prev[0] > k or nxt is None or nxt[1] == prev[1]:
            out.append(prev[1])
            continue
        u = (k - prev[0]) / max(1, nxt[0] - prev[0])
        out.append(prev[1] + (nxt[1] - prev[1]) * (0.5 - 0.5 * math.cos(math.pi * u)))
    return out


def ahead_z(loc: str | None, path: list, z_start: float | None) -> float:
    """Высота пола в конце пути бега (для точки, куда смотреть/светить): по лестнице — от пола начала пути."""
    if not is_stairs(loc) or not path:
        return 0.0
    z = float(z_start) if z_start is not None else ground(path[0], 0.0)
    # идём по пути шагами — пол меняется постепенно (марш за маршем), этажи над головой не берутся
    for i in range(len(path) - 1):
        a, b = path[i], path[i + 1]
        steps = max(1, int(math.dist(a[:2], b[:2]) / 0.1))
        for k in range(1, steps + 1):
            p = (a[0] + (b[0] - a[0]) * k / steps, a[1] + (b[1] - a[1]) * k / steps)
            z = ground(p, z)
    return z
