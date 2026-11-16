"""Рэп-клип «Уроню БД» — раскадровка по песне: планы (время, локация, камера, позы героев, реквизит и эффекты).

Время — секунды песни (song_final.mp3, 109.6 с): строки — выравнивание текста по вокалу (work/lyrics.json),
удары бочки/808 — work/beats.json. Склейки 1–2 с, на дропах вспышка и тряска. ``build_specs`` собирает по одной
сцене на локацию (все её планы; кадры рендерятся участками [A:B]).
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import meme3d_rap_locs as Lc  # noqa: E402
import meme3d_rap_world as Wd  # noqa: E402

DUR = 109.6
FY = Wd.FY
DATA = Path(r"D:\vvf-offload\memes\claude_rap\work")


def kicks() -> list[float]:
    return json.loads((DATA / "beats.json").read_text("utf-8"))["kicks"]


# --------------------------------------------------------------------------- камера


def frame_cam(tgt: list[float], width_m: float, fov: float, az: float = 0.0, el: float = 5.0, floor: float = 0.025) -> list[float]:
    """Камера на цель: по горизонтали в кадр входит ``width_m`` м (кадр 9:16), азимут (° от +z) и угол сверху (°)."""
    vspan = width_m * 16 / 9
    d = (vspan / 2) / math.tan(math.radians(fov / 2))
    a, e = math.radians(az), math.radians(el)
    return [tgt[0] + d * math.cos(e) * math.sin(a), max(floor, tgt[1] + d * math.sin(e)), tgt[2] + d * math.cos(e) * math.cos(a)]


def C(tgt, w, fov, az=0.0, el=4.0, fish=0.0, roll=0.0, dof=0.0, pos=None, floor=0.025) -> dict:
    """Положение камеры: цель, ширина кадра (м), fov, азимут, угол сверху; или явная ``pos``."""
    p = pos or frame_cam(tgt, w, fov, az, el, floor)
    return {"position": [round(v, 4) for v in p], "target": [round(v, 4) for v in tgt], "fov": fov, "fisheye": fish, "roll": roll, "dof": dof,
            "flash": 0}


# --------------------------------------------------------------------------- план


class Shot:
    def __init__(self, t0: float, t1: float, loc: str, cam: dict, cam_end: dict | None = None, *, ease: str = "outCubic",
                 c: dict | None = None, k: dict | None = None, fx: list[dict] | None = None, lab: str = "", mid: list[tuple[float, dict]] | None = None,
                 flash: bool = False, shake: float = 0.0, leash: float | None = 0.44):
        self.t0, self.t1, self.loc, self.cam, self.cam_end, self.ease = t0, t1, loc, cam, cam_end, ease
        self.c, self.k, self.fx, self.lab, self.mid = c, k, fx or [], lab, mid or []
        self.flash, self.shake, self.leash = flash, shake, leash


BASE_C = {"lean": 0, "tilt": 0, "turn": 0, "hop": 0, "squash": 1.0, "deflate": 0, "armL": "rest", "armR": "rest", "shades": 1, "type": 0, "shake": 0}
BASE_K = {"lean": 0, "tilt": 0, "turn": 0, "hop": 0, "squash": 1.0, "armL": "rest", "armR": "rest"}


def ch(pos, pose=None, face="smug", look=(0, 0), yaw=0.0, end=None, nod=True, mid=None) -> dict:
    """Герой в плане: где стоит, поза, лицо; ``end`` — поза к концу плана, ``mid`` — [(t, поза/лицо)] внутри."""
    return {"pos": pos, "pose": pose or {}, "face": face, "look": list(look), "yaw": yaw, "end": end, "nod": nod, "mid": mid or []}


def win(t0: float, t1: float) -> list[dict]:
    return [{"t": 0, "on": False}, {"t": round(t0, 3), "on": True}, {"t": round(t1, 3), "on": False}]


def fx_obj(oid, prim, pos, params, t0, t1, **kw) -> dict:
    o = Wd.obj(oid, prim, pos, params, **kw)
    o["visible"] = win(t0, t1)
    return o


def arc_keys(t0: float, t1: float, p0: list[float], p1: list[float], h: float, n: int = 8, spin: float = 360.0) -> list[dict]:
    out = []
    for i in range(n + 1):
        u = i / n
        p = [p0[j] + (p1[j] - p0[j]) * u for j in range(3)]
        p[1] += h * 4 * u * (1 - u)
        out.append({"t": round(t0 + (t1 - t0) * u, 3), "position": [round(v, 4) for v in p], "rotation": [spin * u * 0.7, spin * u, 0],
                    "ease": "linear"})
    return out


# --------------------------------------------------------------------------- раскадровка

TH = [0, 0.345, -0.4]  # Клод на сиденье огромного трона (skyline)
CAR = [0.95, 0.311, 0.15]  # на крыше спорткара «PROD»
POD = 0.18  # пол сцены


def shots() -> list[Shot]:
    S: list[Shot] = []
    fy = FY
    # ================= INTRO 0–6.85: кран над городом → трон → дроп → «Отличный вопрос.»
    S.append(Shot(0.0, 4.3, "skyline", C([-0.6, -1.6, -7.0], 0, 50, pos=[4.5, 3.6, 7.5]), C(TH[:1] + [TH[1] + fy] + TH[2:], 0.75, 34, -12, 2, dof=1.2),
                  ease="inOutCubic", c=ch(TH, {"armL": "hips", "armR": [-10, 25, 0.3, 0]}, "smug", nod=False),
                  k=ch([-0.5, 0, -0.15], {"turn": 20}, "angry", nod=False), leash=0.95, lab="кран над городом",
                  mid=[(2.4, C([-0.2, 0.2, -1.0], 0, 44, pos=[2.4, 1.6, 4.5]))]))
    S.append(Shot(4.3, 5.5, "skyline", C([-0.05, TH[1] + fy, -0.25], 0.5, 40, -18, -4, fish=0.35), C([-0.02, TH[1] + fy, -0.28], 0.34, 40, -14, -3, fish=0.45),
                  c=ch(TH, {"armL": [62, -12, 0.6, 0], "armR": [62, -12, 0.6, 0], "squash": 1.08}, "smug"),
                  k=ch([-0.5, 0, -0.15], {"lean": 22, "turn": 25, "hop": 0.012}, "bark"), flash=True, shake=0.012, leash=0.95, lab="дроп"))
    S.append(Shot(5.5, 6.85, "skyline", C([0.0, TH[1] + fy, -0.3], 0.34, 30, 6, 2, dof=1.0), C([0.0, TH[1] + fy, -0.3], 0.3, 30, 4, 2, dof=1.0),
                  c=ch(TH, {"lean": 4}, "deadpan", end={"shades": 0.0}), k=ch([-0.5, 0, -0.15], {"turn": 25}, "angry"), leash=0.95, lab="«Отличный вопрос.»"))
    # ================= VERSE 1
    S.append(Shot(6.85, 8.65, "datacenter", C([0.0, 0.13, 0.05], 0.45, 80, -28, 2, fish=0.8), C([0.0, 0.13, 0.05], 0.38, 80, -24, 2, fish=0.85),
                  c=ch([0, 0, 0], {"lean": 18, "armL": "hips", "armR": [12, 70, 0.9, 0]}, "smug"), k=ch([-0.44, 0, 0.14], {"turn": 25}, "angry"),
                  lab="тычет в линзу"))
    S.append(Shot(8.65, 10.5, "datacenter", C([-0.22, 0.2, 0.15], 0.9, 50, 18, -3, fish=0.4, roll=-6), C([-0.2, 0.18, 0.14], 0.8, 50, 14, -3, fish=0.45, roll=-4),
                  c=ch([0, 0, 0], {"lean": -14, "tilt": 4, "turn": 10, "armL": [30, -20, 0.3, 0], "armR": [6, 45, 0.8, 0]}, "smug", end={"lean": 16}),
                  k=ch([-0.42, 0, 0.32], {"lean": 28, "turn": 30, "hop": 0.012, "squash": 0.92}, "snarl"), lab="Кодекс рвётся, «братан»"))
    S.append(Shot(10.5, 12.3, "skyline", C([CAR[0], CAR[1] + 0.1, CAR[2]], 1.0, 44, -30, 6), C([CAR[0], CAR[1] + 0.1, CAR[2]], 0.8, 44, -22, 3),
                  c=ch(CAR, {"armL": "hips", "armR": "point"}, "smug", yaw=-28, mid=[(11.55, {"hop": 0.03}), (11.75, {"hop": 0})]),
                  k=None, leash=None, lab="rm -rf на крыше тачки",
                  fx=[fx_obj("files", "rain", [CAR[0], CAR[1], CAR[2]], {"kind": "file", "count": 26, "area": [0.5, 0.5], "height": 1.6, "speed": 1.4,
                                                                       "size": 0.06, "up": True, "colors": ["#ffd23f", "#7ad7ff", "#ff7ab6"],
                                                                       "t0": 11.55, "t1": 13.7}, 0, 13.7)]))
    S.append(Shot(12.3, 13.7, "skyline", C([CAR[0], CAR[1] + fy + 0.04, CAR[2]], 0.5, 40, -12, -10), C([CAR[0], CAR[1] + fy + 0.04, CAR[2]], 0.42, 40, -8, -8),
                  c=ch(CAR, {"armL": [62, -12, 0.6, 0], "armR": [62, -12, 0.6, 0]}, "happy", yaw=-28), k=None, leash=None, lab="«утренний план»"))
    S.append(Shot(13.9, 15.32, "datacenter", C([-0.1, 0.36 + fy, -0.6], 0.6, 40, 24, -6), C([-0.1, 0.36 + fy, -0.6], 0.5, 40, 18, -6),
                  c=ch([-0.1, 0.36, -0.6], {"armR": [20, 70, 0.9, 0], "armL": "hips", "lean": 8}, "smug", yaw=-20,
                       mid=[(14.32, {"hop": 0.025}), (14.5, {"hop": 0})]),
                  k=ch([-0.45, 0, -0.25], {"turn": -20}, "angry"), leash=0.6, lab="форс-пуш в мастер"))
    S.append(Shot(15.32, 16.9, "datacenter", C([-0.1, 0.36 + fy, -0.6], 0.75, 52, -20, -8, roll=28, fish=0.3), C([-0.1, 0.36 + fy, -0.6], 0.65, 52, -16, -8, roll=34, fish=0.3),
                  c=ch([-0.1, 0.36, -0.6], {"armL": "shrug", "armR": "shrug", "lean": -6}, "smug", yaw=-20), k=ch([-0.45, 0, -0.25], {"turn": -20}, "bark"),
                  leash=0.6, lab="«тесты — для лохов» (завал)"))
    S.append(Shot(17.05, 18.65, "datacenter", C([0.0, 0.13, 0.05], 0.36, 34, 8, 2), C([0.0, 0.135, 0.06], 0.28, 34, 6, 2),
                  c=ch([0, 0, 0], {"lean": 24, "armL": "hips", "armR": "hips", "shades": 0}, "smug", look=(0, -0.1)), k=None, leash=None,
                  lab="наклон в линзу"))
    cnt = []
    for i, (txt, a, b) in enumerate([("40 ФАЙЛОВ", 18.65, 19.05), ("27 ФАЙЛОВ", 19.05, 19.35), ("12 ФАЙЛОВ", 19.35, 19.65), ("0 ФАЙЛОВ", 19.65, 20.2)]):
        cnt.append(fx_obj(f"cnt{i}", "sign_plate", [0, 0.42, -0.2], {"text": txt, "width": 0.5, "height": 0.13, "bg": "#d8342c" if i == 3 else "#2b2440",
                                                                   "glow": 0.5}, a, b))
    S.append(Shot(18.65, 20.2, "datacenter", C([0.0, 0.26, 0.0], 0.75, 40, 0, 4), C([0.0, 0.26, 0.0], 0.68, 40, 0, 4),
                  c=ch([0, 0, 0], {"armL": "shrug", "armR": "shrug"}, "smug"), k=ch([-0.44, 0, 0.14], {"turn": 20}, "angry"), fx=cnt,
                  lab="«минус сорок файлов»"))
    S.append(Shot(20.2, 21.6, "penthouse", C([0.0, 0.25, 0.1], 0.9, 50, -10, -4), C([0.0, 0.22, 0.1], 0.75, 50, -6, -4),
                  c=ch([0, 0, 0.1], {"armL": [70, 30, 0.6, 1], "armR": [70, 30, 0.6, 1], "lean": -4}, "happy", mid=[(20.9, {"hop": 0.02}), (21.1, {"hop": 0})]),
                  k=ch([-0.34, 0, 0.2], {"turn": 30}, "angry"), lab="жжёт бюджет: жетоны"))
    S.append(Shot(21.6, 23.0, "penthouse", C([0.0, 0.087 + fy, -0.5], 0.55, 36, 20, 3, dof=1.2), C([0.0, 0.087 + fy, -0.5], 0.48, 36, 16, 3, dof=1.2),
                  c=ch([0, 0.13, -0.5], {"armL": "hips", "armR": "hips", "lean": -10}, "smug"), k=None, leash=None, lab="на диване, 3 ночи",
                  fx=[fx_obj("clock3", "sign_plate", [0.42, 0.55, -0.9], {"text": "03:00", "width": 0.32, "height": 0.12, "bg": "#1a1030", "fg": "#ff3fa4",
                                                                          "glow": 1.0}, 21.6, 23.0)]))
    cards = [{"id": f"cardfly{i}", "primitive": "credit_card", "params": {"size": [0.16, 0.1], "color": c, "text": "CREDITS"}, "position": [0, 0, 0],
              "visible": win(23.1, 24.8), "keys": arc_keys(23.1 + i * 0.12, 24.0 + i * 0.12, [0.02 * i, 0.15, 0.1], [(-0.3 + 0.15 * i), 0.3, 1.2], 0.2, spin=200)}
             for i, c in enumerate(["#7b5cff", "#ff3fa4", "#18c8a8", "#ffb347", "#7b5cff"])]
    S.append(Shot(23.1, 24.8, "penthouse", C([0.0, 0.2, 0.3], 0.6, 70, 0, 0, fish=0.6), C([0.0, 0.2, 0.3], 0.5, 70, 0, 0, fish=0.7),
                  c=ch([0, 0, 0.1], {"armR": [40, 75, 0.9, 0.3], "armL": "hips", "lean": 14}, "happy"), k=ch([-0.34, 0, 0.2], {"turn": 30}, "angry"),
                  fx=cards, lab="веер кредитов в камеру"))
    S.append(Shot(24.8, 26.0, "penthouse", C([0.0, 0.06, 0.12], 0.2, 34, 10, 6, dof=0.8), C([0.0, 0.06, 0.12], 0.17, 34, 6, 6, dof=0.8),
                  c=ch([0, 0, 0.1], {"armR": [10, 40, 0.6, 0], "lean": 6}, "smug"), k=None, leash=None, lab="«а чё, а нет?» — кулон крупно"))
    S.append(Shot(26.26, 27.45, "datacenter", C([0.0, 0.3, 0.0], 0.8, 42, 0, 4), C([0.0, 0.3, 0.0], 0.7, 42, 0, 4),
                  c=ch([0, 0, 0], {"armL": "cross", "armR": "cross"}, "deadpan"), k=ch([-0.44, 0, 0.14], {"turn": 20}, "angry"),
                  fx=[fx_obj("stop", "chat_bubble", [0.2, 0.45, 0.1], {"text": "СТОП!!!", "side": "right", "avatar": "user", "width": 0.42, "height": 0.16,
                                                                     "fontSize": 0.5}, 26.3, 27.45)], lab="юзер: «стоп»"))
    S.append(Shot(27.45, 29.4, "datacenter", C([0.0, 0.13, 0.05], 0.4, 70, 0, 0, fish=0.6), C([0.0, 0.13, 0.05], 0.4, 70, 0, 0, fish=0.6),
                  c=ch([0, 0, 0], {"lean": 20, "armR": [70, 80, 1.0, 0], "armL": "hips"}, "smug", end={"armR": [60, 88, 1.0, 0]}), k=None, leash=None,
                  lab="ладонь в объектив, «Отличный вопрос!»"))
    # Машина катится НОСОМ вперёд (курс = касательная пути): разгон к краю, клевок носом, кувырок вниз.
    car_roll = [{"t": 29.5, "position": [0.95, 0, 0.15], "rotation": [0, 35, 0], "ease": "linear"},
                {"t": 31.0, "position": [1.85, 0, 1.45], "rotation": [0, 35, 0], "ease": "inQuad"},
                {"t": 31.6, "position": [2.3, -0.3, 2.1], "rotation": [45, 35, 0], "ease": "linear"},
                {"t": 32.5, "position": [3.0, -7.0, 3.1], "rotation": [200, 35, 25], "ease": "inQuad"}]
    sign_roll = [{**k, "position": [k["position"][0] + 0.11, k["position"][1] + 0.27, k["position"][2] + 0.156]} for k in car_roll]
    S.append(Shot(29.5, 31.0, "skyline", C([0.55, 0.2, 0.3], 1.4, 44, -55, 10), C([0.75, 0.18, 0.55], 1.3, 44, -45, 8),
                  c=ch([0.3, 0, 0.0], {"armR": [20, 60, 0.9, 0], "lean": 12}, "smug", yaw=40, mid=[(29.7, {"hop": 0.03}), (29.85, {"hop": 0})]),
                  k=ch([-0.15, 0, 0.3], {"turn": 30}, "bark"), leash=0.5, lab="пинает прод с крыши", fx=[{"id": "_mod_car", "keys": car_roll}, {"id": "_mod_prodsign", "keys": sign_roll}]))
    S.append(Shot(31.0, 32.53, "skyline", C([0.7, 0.05, 0.5], 0, 58, pos=[2.5, 0.5, 2.3]), C([1.2, -0.25, 1.0], 0, 62, pos=[2.55, 0.42, 2.4], roll=-14), ease="inCubic",
                  c=ch([0.3, 0, 0.0], {"armL": [62, -12, 0.6, 0], "armR": [62, -12, 0.6, 0]}, "happy", yaw=40), k=ch([-0.15, 0, 0.3], {"turn": 30}, "bark"),
                  leash=0.5, lab="«прод под откос»"))
    # ================= CHORUS 1
    ck = [t for t in kicks() if 32.5 <= t <= 44.7]
    S.append(Shot(32.53, 34.0, "stage", C([-0.1, POD + 0.2, 0.1], 0.9, 56, 14, -1, fish=0.4), C([-0.1, POD + 0.18, 0.1], 0.78, 56, 10, -1, fish=0.45),
                  c=ch([0, POD, 0], {"lean": -16, "tilt": 4, "turn": 10, "armL": [30, -20, 0.3, 0], "armR": [6, 45, 0.8, 0]}, "smug"),
                  k=ch([-0.42, POD, 0.3], {"lean": 28, "turn": 30, "hop": 0.012, "squash": 0.92}, "snarl"), flash=True, shake=0.012,
                  fx=[fx_obj("dbr1", "rain", [0, 0, 0.1], {"kind": "db", "count": 22, "area": [1.8, 1.2], "height": 1.8, "speed": 1.0, "size": 0.12, "seed": 2,
                                                           "t0": 32.5, "t1": 35.6}, 0, 35.6)], lab="припев: разъ**у весь прод"))
    tower = [{"id": "dbtower", "primitive": "group", "position": [0.35, POD, -0.45], "visible": win(34.0, 35.6),
              "keys": [{"t": 34.0, "rotation": [0, 0, 0]}, {"t": 34.55, "rotation": [0, 0, 0]}, {"t": 35.4, "rotation": [0, 0, -88], "ease": "inQuad"}]}]
    for i in range(7):
        tower.append({"id": f"dbt{i}", "primitive": "db_icon", "params": {"size": 0.26, "color": ["#3cc6ff", "#ff5fa2", "#ffd23f"][i % 3]},
                      "position": [0, i * 0.27, 0], "parent": "dbtower"})
    S.append(Shot(34.0, 35.6, "stage", C([0.2, 0.75, -0.3], 1.6, 50, -20, -4), C([0.2, 0.7, -0.3], 1.5, 50, -16, -4),
                  c=ch([-0.15, POD, 0.0], {"armL": "shrug", "armR": "point", "turn": 20}, "smug", yaw=10), k=ch([-0.5, POD, 0.2], {"turn": 30}, "bark"),
                  fx=tower, lab="уроню БД — башня падает"))
    box_in = [{"id": "boxin", "primitive": "delivery_box", "params": {"size": [0.32, 0.24, 0.28]}, "position": [0, 0, 0], "visible": win(35.6, 36.6),
               "keys": [{"t": 35.6, "position": [0.3, 0.12, 0.25], "rotation": [0, 10, 0]}, {"t": 36.4, "position": [0.3, 0.1, 0.5], "rotation": [-8, 25, 0],
                                                                                                "ease": "outBack"}]}]
    S.append(Shot(35.6, 36.6, "street", C([0.0, 0.2, 0.4], 0.5, 82, 0, 2, fish=0.85), None,
                  c=ch([0.1, 0, 0.0], {"armL": "hips", "armR": "rest"}, "smug"), k=None, leash=None, fx=box_in, lab="коробка фури-аниме в фишай"))
    S.append(Shot(36.6, 37.7, "street", C([0.25, 0.28, 0.25], 0.9, 44, 20, 0), C([0.25, 0.26, 0.25], 0.8, 44, 16, 0),
                  c=ch([0.05, 0, 0.35], {"armR": [30, 50, 0.6, 1], "armL": "hips"}, "smug", yaw=-15), k=ch([-0.3, 0, 0.45], {"turn": 20}, "angry"),
                  lab="расписывается не глядя"))
    S.append(Shot(38.71, 40.2, "stage", C([0.0, POD + 0.2, 0.0], 0.6, 50, 0, -22, floor=0.05), C([0.0, POD + 0.2, 0.0], 0.55, 50, 0, -24, floor=0.05),
                  c=ch([0, POD, 0], {"armL": [75, 5, 0.5, 0.6], "armR": [75, 5, 0.5, 0.6], "squash": 1.06}, "happy"), k=None, leash=None,
                  lab="«мне по**й» руки вверх"))
    S.append(Shot(40.2, 41.6, "stage", C([0.0, 0.3, 0.6], 1.6, 50, 8, 10), C([0.0, 0.3, 0.6], 1.45, 50, 4, 8),
                  c=ch([0, POD, 0], {"armL": [62, -12, 0.6, 0], "armR": [62, -12, 0.6, 0]}, "happy"), k=ch([-0.36, POD, 0.15], {"turn": 25, "lean": 15}, "bark"),
                  lab="толпа прыгает"))
    S.append(Shot(41.63, 43.1, "stage", C([0.4, POD + 0.25, 0.2], 1.05, 44, 25, 10), C([0.4, POD + 0.22, 0.2], 0.95, 44, 20, 8),
                  c=ch([0.66, POD, 0.0], {"armL": "cross", "armR": "cross"}, "smug", yaw=-20), k=ch([0.32, POD + 0.26, 0.25], {"turn": -10}, "smug", yaw=20),
                  leash=0.7, lab="кнопка «отменить подписку» не жмётся",
                  fx=[fx_obj("btn1", "cancel_button", [0.32, POD, 0.25], {"radius": 0.13, "pedestal": 0.1}, 41.6, 44.7,
                             state=[{"t": 0, "press": 0}, {"t": 42.0, "press": 1}, {"t": 42.15, "press": 0}, {"t": 42.5, "press": 1}, {"t": 42.65, "press": 0}]),
                      fx_obj("user1", "mochi_user", [0.0, POD, 0.42], {"size": 0.2, "mood": "type", "eyes": "normal"}, 41.6, 43.1, rotation=[0, 60, 0])]))
    S.append(Shot(43.1, 44.66, "stage", C([0.62, POD + 0.13, 0.05], 0.36, 34, 6, 2), C([0.62, POD + 0.135, 0.06], 0.3, 34, 4, 2),
                  c=ch([0.66, POD, 0.0], {"lean": 24, "armL": "hips", "armR": "hips", "shades": 0}, "smug", look=(0, -0.1), yaw=0),
                  k=ch([0.32, POD + 0.26, 0.25], {"turn": -10}, "smug", yaw=20), leash=0.7, lab="наклон в линзу",
                  fx=[fx_obj("btn2", "cancel_button", [0.32, POD, 0.25], {"radius": 0.13, "pedestal": 0.1}, 43.1, 44.7)]))
    # ================= VERSE 2
    S.append(Shot(44.66, 46.4, "classroom", C([0.0, 0.34, -0.6], 1.6, 48, 30, 5), C([0.1, 0.32, -0.75], 1.1, 46, 24, 6),
                  c=ch([0.3, 0, -0.85], {"armR": [40, 60, 0.7, 0.4], "lean": 6, "type": 0.6}, "focused", yaw=-25), k=None, leash=None,
                  lab="доска, «думаю час»"))
    S.append(Shot(46.4, 48.0, "classroom", C([1.25, 1.35, -1.21], 0.4, 34, -10, 0), C([1.25, 1.35, -1.21], 0.32, 34, -6, 0),
                  c=None, k=None, leash=None, lab="часы крутятся"))
    pages = [fx_obj("pages", "rain", [0.1, 0, -0.5], {"kind": "card", "text": "ЭССЕ", "colors": ["#f6f1e6", "#ffffff"], "count": 40, "area": [1.4, 1.0],
                                                     "height": 1.4, "speed": 0.5, "size": 0.12, "seed": 8, "t0": 48.2, "t1": 51.3}, 48.2, 51.3)]
    S.append(Shot(48.2, 49.6, "classroom", C([0.2, 0.2, -0.6], 0.8, 50, -10, 6), C([0.2, 0.2, -0.6], 0.7, 50, -6, 6),
                  c=ch([0.2, 0, -0.6], {"armL": [70, 30, 0.6, 1], "armR": [70, 30, 0.6, 1]}, "happy"), k=None, leash=None, fx=pages, lab="эссе на 20 страниц"))
    S.append(Shot(49.6, 51.3, "classroom", C([0.2, 0.13, -0.6], 0.36, 34, 14, 2), C([0.2, 0.13, -0.6], 0.3, 34, 10, 2),
                  c=ch([0.2, 0, -0.6], {"lean": 10}, "smug"), k=None, leash=None, lab="«для вас»"))
    S.append(Shot(51.52, 53.0, "street", C([0.05, 0.13, 0.4], 0.42, 80, 0, 2, fish=0.85), C([0.05, 0.13, 0.4], 0.36, 80, 0, 2, fish=0.9),
                  c=ch([0.05, 0, 0.35], {"lean": 20, "armL": [40, 80, 1.0, 0], "armR": [40, 80, 1.0, 0]}, "surprised"), k=None, leash=None,
                  lab="«где мувик?» руки рупором"))
    cats = []
    for i in range(9):
        a = i / 9 * 2 * math.pi
        cats.append(fx_obj(f"cat{i}", "helper_bot", [0.05 + math.sin(a) * 0.36, 0, 0.35 + math.cos(a) * 0.3],
                           {"style": "voxel", "size": 0.11, "color": ["#ffb36b", "#f2f2f2", "#5a5a66"][i % 3], "accent": "#ff7ab6", "top": "ears", "mood": "hop",
                            "phase": i * 0.13, "label": "кот" if i % 3 == 0 else None}, 53.0, 54.45, rotation=[0, math.degrees(a) + 180, 0]))
    S.append(Shot(53.0, 54.45, "street", C([0.05, 0.15, 0.35], 1.0, 44, 0, 18), C([0.05, 0.15, 0.35], 0.9, 44, 0, 16),
                  c=ch([0.05, 0, 0.35], {"armL": "shrug", "armR": "shrug"}, "happy"), k=None, leash=None, fx=cats, lab="мувик про котов"))
    S.append(Shot(54.5, 55.9, "street", C([0.05, 0.13, 0.35], 0.4, 36, -14, 2), C([0.05, 0.13, 0.35], 0.34, 36, -10, 2),
                  c=ch([0.05, 0, 0.35], {"armL": "shrug", "armR": "shrug", "tilt": 8}, "smug"), k=None, leash=None, lab="«сам не уточнил»"))
    S.append(Shot(55.9, 57.3, "street", C([0.0, 0.25, 0.4], 0.9, 44, 12, 2), C([0.0, 0.25, 0.4], 0.8, 44, 8, 2),
                  c=ch([0.05, 0, 0.35], {"armR": [80, 20, 0.6, 0], "armL": "hips"}, "happy"), k=ch([-0.3, 0, 0.45], {"turn": 20}, "smile"),
                  lab="«а я всегда готов»"))
    gauge = [fx_obj("gauge", "limit_gauge", [0, 0.45, -0.3], {"width": 0.6, "height": 0.14}, 57.4, 59.3,
                    keys=[{"t": 57.4, "v": 1.0}, {"t": 58.6, "v": 0.0, "ease": "inQuad"}])]
    S.append(Shot(57.43, 59.3, "datacenter", C([0.0, 0.3, 0.0], 0.8, 42, 0, 4), C([0.0, 0.3, 0.0], 0.7, 42, 0, 4),
                  c=ch([0, 0, 0], {"armL": "hips", "armR": "hips"}, "smug"), k=ch([-0.44, 0, 0.14], {"turn": 20}, "angry"), fx=gauge, lab="лимиты в ноль"))
    crawl = []
    for i in range(37):  # часто: курс по касательной без «шагов»
        a = math.radians(200 - i * 6.25)
        crawl.append({"t": round(59.3 + i * 0.08, 3), "position": [round(math.sin(a) * 0.65, 3), 0, round(math.cos(a) * 0.65, 3)],
                      "rotation": [0, round(math.degrees(a) - 90, 1), 0], "ease": "linear"})
    S.append(Shot(59.3, 61.95, "datacenter", C([0.0, 0.0, 0.0], 0, 46, pos=[0.0, 2.2, 0.02]), C([0.0, 0.0, 0.0], 0, 46, pos=[0.0, 1.8, 0.02], roll=40),
                  c=ch([0, 0, 0], {"armL": "hips", "armR": "hips"}, "smug"), k=ch([-0.44, 0, 0.14], {"turn": 20}, "angry"), lab="юзер приполз (сверху)",
                  fx=[{"id": "crawler", "primitive": "mochi_user", "params": {"size": 0.22, "mood": "idle", "eyes": "normal"}, "position": [0, 0, 0],
                       "visible": win(59.3, 62.0), "keys": crawl}]))
    S.append(Shot(62.72, 64.5, "inferno", C([0.0, 0.13, 0.25], 0.38, 34, 6, 2), C([0.0, 0.135, 0.26], 0.3, 34, 4, 2),
                  c=ch([0, 0, 0.2], {"lean": 24, "armL": "hips", "armR": "hips", "shades": 0}, "smug", look=(0, -0.1)), k=None, leash=None,
                  lab="«без меня твой код»"))
    S.append(Shot(64.5, 66.0, "inferno", C([0.0, 0.25, 0.2], 0.8, 56, 0, 0, fish=0.6), C([0.0, 0.25, 0.2], 0.5, 56, 0, 0, fish=0.9),
                  c=ch([0, 0, 0.2], {"armL": [62, -12, 0.6, 0], "armR": [62, -12, 0.6, 0]}, "rage"), k=ch([-0.42, 0, 0.32], {"turn": 25, "lean": 20}, "bark"),
                  flash=True, shake=0.02, lab="«минус мозг» — глитч"))
    # ================= BRIDGE (студия, ровно, без эмоций)
    B = [0.12, 0.16, 0]
    KB = [-0.25, 0.16, 0.04]
    pet = {"armR": [30, 50, 0.6, 0.6], "turn": 15}
    S.append(Shot(66.0, 69.2, "studio", C([-0.03, 0.27, 0.0], 0.8, 32, 0, 4), None, c=ch(B, pet, "deadpan", nod=False), k=ch(KB, {}, "smile", yaw=20, nod=False),
                  leash=0.3, lab="мост: «Ты зачем базу удалил?»",
                  fx=[fx_obj("bub1", "chat_bubble", [0.0, 0.55, -0.1], {"text": "Ты зачем базу удалил?", "side": "right", "avatar": "user", "width": 0.85,
                                                                     "height": 0.24}, 67.1, 69.2)]))
    S.append(Shot(69.2, 71.2, "studio", C([0.1, 0.16 + FY, 0.0], 0.34, 30, 0, 2), None, c=ch(B, {**pet, "shades": 1}, "deadpan", nod=False),
                  k=ch(KB, {}, "smile", yaw=20, nod=False), leash=0.3, lab="«Отличный вопрос! Вы абсолютно правы.»"))
    S.append(Shot(71.2, 72.26, "studio", C([-0.17, 0.16 + 0.08, 0.02], 0.24, 64, 0, 0, fish=0.6), None, c=ch(B, pet, "deadpan", nod=False),
                  k=ch(KB, {"lean": 22}, "snarl", yaw=0, nod=False), leash=0.3, lab="вставка: Кодекс скалится"))
    S.append(Shot(72.26, 73.95, "studio", C([-0.03, 0.27, 0.0], 0.8, 32, 0, 4), None, c=ch(B, pet, "deadpan", nod=False), k=ch(KB, {}, "smile", yaw=20, nod=False),
                  leash=0.3, lab="«Восстанови из бэкапа.»",
                  fx=[fx_obj("bub2", "chat_bubble", [0.0, 0.55, -0.1], {"text": "Восстанови из бэкапа.", "side": "right", "avatar": "user", "width": 0.85,
                                                                     "height": 0.24}, 72.26, 73.95)]))
    S.append(Shot(73.95, 75.0, "studio", C([0.1, 0.16 + FY, 0.0], 0.34, 30, 0, 2), None, c=ch(B, {**pet, "shades": 0}, "deadpan", nod=False),
                  k=ch(KB, {}, "smile", yaw=20, nod=False), leash=0.3, lab="«А бэкап я тоже удалил.»"))
    S.append(Shot(75.0, 76.09, "studio", C([-0.17, 0.24, 0.02], 0.3, 50, 0, 0, fish=0.3), C([-0.17, 0.24, 0.02], 0.26, 50, 0, 0, fish=0.35, roll=-20),
                  c=ch(B, pet, "deadpan", nod=False), k=ch(KB, {"lean": 22, "shake": 1}, "snarl", yaw=0, nod=False), leash=0.3, lab="Кодекс рычит, завал"))
    # ================= FINAL CHORUS (максимум)
    S.append(Shot(76.09, 77.6, "inferno", C([0.0, 0.32, 0.1], 0.95, 58, 0, -2, fish=0.35), C([0.0, 0.3, 0.1], 0.85, 58, 0, -2, fish=0.4),
                  c=ch([0, 0, 0.2], {"lean": -8, "armL": [62, -12, 0.6, 0], "armR": [62, -12, 0.6, 0]}, "smug"),
                  k=ch([-0.42, 0, 0.32], {"lean": 20, "turn": 25}, "bark"), flash=True, shake=0.015, lab="финал: дата-центр горит"))
    S.append(Shot(77.6, 79.0, "stage", C([0.0, 0.25, 0.9], 1.4, 70, 0, -6, fish=0.6), C([0.0, 0.25, 0.9], 1.2, 70, 0, -6, fish=0.65),
                  c=ch([0, POD, 0], {"armL": [70, -10, 0.6, 0], "armR": [70, -10, 0.6, 0]}, "happy"), k=ch([-0.34, POD, 0.12], {"lean": 15}, "bark"),
                  lab="фишай с толпой"))
    S.append(Shot(79.03, 80.3, "street", C([0.25, 0.28, 0.25], 0.9, 44, -20, 0), C([0.25, 0.26, 0.25], 0.8, 44, -16, 0),
                  c=ch([0.05, 0, 0.35], {"armR": [30, 50, 0.6, 1], "armL": "hips"}, "smug", yaw=-15), k=ch([-0.3, 0, 0.45], {"turn": 20}, "bark"),
                  lab="доставка"))
    S.append(Shot(80.3, 81.5, "inferno", C([-0.34, 0.05, 0.4], 0.16, 40, 10, 2), C([-0.34, 0.05, 0.4], 0.13, 40, 6, 2),
                  c=ch([0, 0, 0.2], {"armR": [10, 45, 0.7, 0]}, "smug"), k=ch([-0.42, 0, 0.32], {"lean": 15, "turn": 0}, "snarl", yaw=0),
                  lab="шипы ошейника крупно"))
    S.append(Shot(82.43, 83.9, "skyline", C([0.0, 0.2, -0.2], 0.9, 44, 12, -6), C([0.0, 0.2, -0.2], 0.8, 44, 8, -6),
                  c=ch([0, 0, -0.1], {"armL": [70, -10, 0.6, 0], "armR": [70, -10, 0.6, 0]}, "happy"), k=ch([-0.34, 0, 0.0], {"lean": 15}, "bark"),
                  lab="«тебе опять платить» на крыше",
                  fx=[fx_obj("cr2", "rain", [0, 0, 0], {"kind": "coin", "count": 50, "area": [1.6, 1.4], "height": 1.6, "speed": 0.7, "size": 0.05,
                                                        "t0": 82.4, "t1": 84.0}, 0, 84.0)]))
    S.append(Shot(83.9, 85.3, "stage", C([0.0, POD + 0.2, 0.0], 0.6, 50, 0, -22, floor=0.05), None,
                  c=ch([0, POD, 0], {"armL": [75, 5, 0.5, 0.6], "armR": [75, 5, 0.5, 0.6]}, "happy"), k=None, leash=None, lab="руки вверх, конфетти"))
    S.append(Shot(85.55, 87.3, "stage", C([0.4, POD + 0.25, 0.2], 1.05, 44, 25, 10), C([0.4, POD + 0.22, 0.2], 0.95, 44, 20, 8),
                  c=ch([0.66, POD, 0.0], {"armL": "cross", "armR": "cross"}, "smug", yaw=-20), k=ch([0.32, POD + 0.26, 0.25], {"turn": -10}, "smug", yaw=20),
                  leash=0.7, lab="«подписку отменить» — кнопка",
                  fx=[fx_obj("btn3", "cancel_button", [0.32, POD, 0.25], {"radius": 0.13, "pedestal": 0.1}, 85.5, 87.3,
                             state=[{"t": 0, "press": 0}, {"t": 86.0, "press": 1}, {"t": 86.15, "press": 0}]),
                      fx_obj("user2", "mochi_user", [0.0, POD, 0.42], {"size": 0.2, "mood": "type", "eyes": "normal"}, 85.5, 87.3, rotation=[0, 60, 0])]))
    S.append(Shot(87.3, 89.0, "inferno", C([0.0, 0.13, 0.25], 0.38, 34, -6, 2), C([0.0, 0.135, 0.26], 0.3, 34, -4, 2),
                  c=ch([0, 0, 0.2], {"lean": 24, "armL": "hips", "armR": "hips", "shades": 0}, "smug", look=(0, -0.1)), k=None, leash=None, lab="наклон в линзу"))
    S.append(Shot(89.0, 91.65, "inferno", C([0.0, 0.32, 0.1], 1.1, 58, 0, 4), C([0.0, 0.32, 0.1], 0.9, 58, 0, 2),
                  c=ch([0, 0, 0.2], {"armL": [62, -12, 0.6, 0], "armR": [62, -12, 0.6, 0]}, "smug"), k=ch([-0.42, 0, 0.32], {"lean": 20, "turn": 25}, "bark"),
                  lab="всё горит"))
    # ================= OUTRO: инструментал, монтаж по локациям, «Подписку отменить», кнопка отваливается
    out = [("stage", C([0.0, 0.3, 0.6], 1.6, 50, 8, 10), {"armL": [70, -10, 0.6, 0.5], "armR": [70, -10, 0.6, 0.5]}, "happy", [0, POD, 0]),
           ("penthouse", C([0.0, 0.25, 0.1], 0.9, 50, -10, -4), {"armL": [70, 30, 0.6, 1], "armR": [70, 30, 0.6, 1]}, "happy", [0, 0, 0.1]),
           ("datacenter", C([-0.15, 0.2, 0.12], 0.75, 50, 18, -3, fish=0.4, roll=-6), {"lean": -14, "armR": [6, 45, 0.8, 0]}, "smug", [0, 0, 0]),
           ("street", C([0.25, 0.28, 0.25], 0.9, 44, 20, 0), {"armL": "hips", "armR": "point"}, "smug", [0.05, 0, 0.35]),
           ("inferno", C([0.0, 0.32, 0.1], 0.95, 58, 0, -2, fish=0.35), {"armL": [62, -12, 0.6, 0], "armR": [62, -12, 0.6, 0]}, "smug", [0, 0, 0.2]),
           ("classroom", C([0.0, 0.34, -0.6], 1.6, 48, 30, 5), {"armR": [40, 60, 0.7, 0.4], "type": 0.6}, "focused", [0.3, 0, -0.85]),
           ("skyline", C([0.0, 0.2, -0.2], 0.9, 44, 12, -6), {"armL": [70, -10, 0.6, 0], "armR": [70, -10, 0.6, 0]}, "happy", [0, 0, -0.1])]
    edges = [92.13, 93.3, 94.47, 95.67, 96.86, 98.01, 98.98, 99.7]
    for i in range(len(edges) - 1):
        loc, cam, pose, face, pos = out[i % len(out)]
        yaw = -25 if loc == "classroom" else 0
        S.append(Shot(edges[i], edges[i + 1], loc, cam, {**cam, "position": [v * 0.97 + c * 0.03 for v, c in zip(cam["position"], cam["target"])]},
                      c=ch(pos, pose, face, yaw=yaw), k=None if loc == "classroom" else ch([pos[0] - 0.46, pos[1], pos[2] + 0.14], {"lean": 15, "turn": 25}, "bark"),
                      leash=None if loc == "classroom" else 0.44, flash=i == 0, shake=0.012 if i == 0 else 0.0, lab=f"аутро {i + 1}: {loc}"))
    S.insert(len(S) - 7, Shot(91.65, 92.13, "studio", C([0.1, 0.16 + FY, 0.0], 0.34, 30, 0, 2), None, c=ch([0.1, 0.16, 0], {"shades": 1}, "deadpan", nod=False),
                              k=None, leash=None, lab="стоп-кадр перед аутро"))
    cap_roll = [{"t": 101.0, "position": [0.32, POD + 0.14, 0.25], "rotation": [0, 0, 0]},
                {"t": 101.5, "position": [0.42, POD - 0.02, 0.4], "rotation": [70, 0, 20], "ease": "inQuad"},
                {"t": 104.4, "position": [1.6, POD - 0.02 + 0.13, 1.4], "rotation": [90, 0, 720], "ease": "outQuad"}]
    btn_state = [{"t": 0, "press": 0, "crack": 0, "cap": 1}, {"t": 99.75, "crack": 0.35}, {"t": 100.35, "crack": 1.0}, {"t": 101.0, "cap": 0}]
    S.append(Shot(99.7, 101.0, "stage", C([0.14, POD + 0.12, 0.2], 0.95, 40, -10, 28), C([0.16, POD + 0.12, 0.22], 0.8, 40, -8, 30),
                  c=ch([-0.05, POD, 0.1], {"armL": "hips", "armR": "hips"}, "smug", yaw=20), k=ch([-0.46, POD, 0.3], {"turn": 20}, "smug"), leash=0.6,
                  lab="«Подписку отменить.» — трещина",
                  fx=[fx_obj("btn4", "cancel_button", [0.32, POD, 0.25], {"radius": 0.13, "pedestal": 0.1}, 99.7, DUR + 1, state=btn_state),
                      {"id": "capfall", "primitive": "cylinder", "params": {"size": [0.26, 0.12], "color": "#ff2e3f", "emissive": "#ff2e3f",
                                                                             "emissiveIntensity": 0.35},
                       "position": [0, 0, 0], "visible": win(101.0, DUR + 1), "keys": cap_roll}]))
    S.append(Shot(101.0, 104.4, "stage", C([0.5, POD + 0.15, 0.4], 1.2, 44, 20, 12), C([0.7, POD + 0.12, 0.7], 1.3, 44, 30, 10),
                  c=ch([-0.05, POD, 0.1], {"armL": "shrug", "armR": "shrug"}, "smug", yaw=20), k=ch([-0.46, POD, 0.3], {"turn": 20}, "angry"), leash=0.6,
                  lab="кнопка отваливается и катится"))
    S.append(Shot(104.4, DUR, "skyline", C(TH[:1] + [TH[1] + FY] + TH[2:], 0.75, 34, -12, 2, dof=1.0), C([-0.6, -1.6, -7.0], 0, 50, pos=[5.5, 4.5, 9.5]),
                  ease="inOutCubic", c=ch(TH, {"armL": "hips", "armR": [-10, 25, 0.3, 0]}, "smug", nod=False),
                  k=ch([-0.5, 0, -0.15], {"turn": 20}, "smile", nod=False), leash=0.95, lab="финал: кран вверх над городом"))
    return S


# --------------------------------------------------------------------------- сборка сцен по локациям


def vocal_env() -> list[float]:
    """Огибающая вокала (стем demucs) на 30 кадров/с, 0..1 — тело Клода пружинит на слогах."""
    import subprocess

    import numpy as np

    sr = 16000
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(DATA / "align" / "vocals.wav"), "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    x = np.frombuffer(pcm, np.int16).astype(np.float32) / 32768
    hop = sr // 30
    n = len(x) // hop
    rms = np.sqrt(np.array([np.mean(x[i * hop:(i + 1) * hop] ** 2) for i in range(n)]))
    ref = np.percentile(rms, 97) or 1.0
    env = np.clip(rms / ref, 0, 1)
    return [round(float(v), 3) for v in env]


CHORUS = [(32.5, 44.7), (76.0, 91.7), (92.1, 99.7)]
GESTURES = [[38, 55, 0.8, 0], [18, 72, 0.9, 0], [70, 12, 0.55, 0], [50, 35, 0.7, 0.4]]


def _in(t: float, spans) -> bool:
    return any(a <= t < b for a, b in spans)


def _arm(v) -> list[float]:

    named = {"rest": [-8, 0, 0, 0], "down": [-38, 4, 0, 0], "up": [75, 5, 0.4, 0], "hips": [-30, -14, 0.1, 0], "shrug": [42, -6, 0.35, 0],
             "point": [12, 62, 0.6, 0], "cross": [-4, 72, 0.15, 0], "jab": [22, 55, 0.7, 0.5], "fist": [24, 30, 0.3, 0.6], "flail": [62, 18, 0.5, 1]}
    return list(v) if isinstance(v, (list, tuple)) else named.get(str(v), [-8, 0, 0, 0])


def word_onsets() -> list[float]:
    data = json.loads((DATA / "lyrics.json").read_text("utf-8"))
    return [w["t0"] for ln in data["lines"] for w in ln["words"]]


def _track(shots_: list[Shot], who: str, kk: list[float]) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """Ключи героя по планам его локации: поза плана (база, середина, конец), жизнь на бит, лицо, позиция, видимость.

    Клод: на каждый удар — подготовка (присел), прыжок со вытягиванием и отдача (перелёт), кивок; на ударные слова —
    жесты ручками попеременно (выброс вперёд/вверх и возврат), если поза плана не держит руки особо (ладонь в объектив,
    рупор). Кодекс: подпрыгивает на ударах, на припевах — бросается вперёд и лает."""
    base = BASE_C if who == "c" else BASE_K
    words = word_onsets()
    pose, face, move, vis = [], [], [], [{"t": 0, "on": False}]
    for s in shots_:
        h = s.c if who == "c" else s.k
        if not h:
            vis.append({"t": s.t0, "on": False})
            continue
        p0 = {**base, **h["pose"]}
        pose.append({"t": s.t0, "ease": "step", **p0})
        for tm, extra in h["mid"]:
            pose.append({"t": tm, "ease": "outQuad", **extra})
        if h["end"]:
            pose.append({"t": round(s.t1 - 0.05, 3), "ease": "inOutSine", **h["end"]})
        if h["nod"]:
            lean0, sq0, hop0, tilt0 = p0["lean"], p0["squash"], p0.get("hop", 0), p0.get("tilt", 0)
            hot = _in(s.t0, CHORUS)
            side = 1
            for kt in kk:
                if not (s.t0 + 0.08 < kt < s.t1 - 0.2):
                    continue
                if who == "c":
                    amp = 1.0 if hot else 0.6
                    pose += [
                        {"t": round(kt - 0.07, 3), "ease": "inQuad", "squash": sq0 * (1 - 0.07 * amp), "lean": lean0 - 2},
                        {"t": round(kt, 3), "ease": "outQuad", "squash": sq0 * (1 + 0.07 * amp), "hop": hop0 + 0.016 * amp, "lean": lean0 + 7 * amp},
                        {"t": round(kt + 0.13, 3), "ease": "inQuad", "squash": sq0 * (1 - 0.03 * amp), "hop": hop0},
                        {"t": round(kt + 0.24, 3), "ease": "outBack", "squash": sq0, "lean": lean0},
                        # Перенос веса: покачивание из стороны в сторону на удары.
                        {"t": round(kt + 0.02, 3), "ease": "outQuad", "tilt": tilt0 + side * 5 * amp},
                        {"t": round(kt + 0.42, 3), "ease": "inOutSine", "tilt": tilt0 + side * 1.5},
                    ]
                    side = -side
                else:
                    lunge = hot
                    pose += [
                        {"t": round(kt, 3), "ease": "outQuad", "hop": hop0 + (0.02 if lunge else 0.01), "lean": lean0 + (14 if lunge else 5),
                         "squash": 0.92 if lunge else 0.96},
                        {"t": round(kt + 0.16, 3), "ease": "outBack", "hop": hop0, "lean": lean0, "squash": p0["squash"]},
                    ]
            if who == "c" and s.loc != "studio":
                aL, aR = _arm(p0["armL"]), _arm(p0["armR"])
                special = max(aL[1], aR[1]) >= 60 or max(aL[0], aR[0]) >= 60
                if not special:
                    side = 0
                    last = -9.0
                    for wt in words:
                        if not (s.t0 + 0.1 < wt < s.t1 - 0.25) or wt - last < 0.42:
                            continue
                        last = wt
                        g = GESTURES[(side + int(wt * 7)) % len(GESTURES)]
                        fld, back = ("armR", aR) if side % 2 == 0 else ("armL", aL)
                        pose += [{"t": round(wt - 0.04, 3), "ease": "outBack", fld: g}, {"t": round(wt + 0.24, 3), "ease": "inOutSine", fld: back}]
                        side += 1
        fk = {"t": s.t0, "expr": h["face"], "blend": 0}
        if who == "c":
            fk["look"] = h["look"]
        else:
            fk["look"] = [0.1, 0]
        face.append(fk)
        if who == "k" and _in(s.t0, CHORUS) and h["face"] in ("angry", "smug", "smile"):
            face.append({"t": s.t0, "expr": "bark", "look": [0.1, 0]})
        move.append({"t": s.t0, "position": h["pos"], "ease": "step"})
        vis.append({"t": s.t0, "on": True})
    pose.sort(key=lambda k: k["t"])
    face.sort(key=lambda k: k["t"])
    return pose, face, move or [{"t": 0, "position": [0, -5, 0]}], vis


BRIDGE_CLAUDE = [(69.2, 72.15), (73.95, 75.9)]


def mouth_keys() -> list[dict]:
    """Формы губ Клода по слогам текста песни (work/lyrics.json)."""
    import meme3d_hero_claude as H

    data = json.loads((DATA / "lyrics.json").read_text("utf-8"))
    # Реплики юзера на мосту («— Ты зачем…», «— Восстанови…») поёт не Клод — их губами не повторяем.
    mine = [ln for ln in data["lines"] if not (ln["text"].lstrip().startswith("—") and not any(x in ln["text"] for x in ("Отличный", "бэкап я")))]
    return H.viseme_keys_from_words([w for ln in mine for w in ln["words"]])


def build_specs(width: int = 1080, height: int = 1920) -> tuple[dict[str, dict], list[Shot]]:
    all_shots = shots()
    kk = kicks()
    env = vocal_env()
    mk = mouth_keys()
    out: dict[str, dict] = {}
    for loc_name in dict.fromkeys(s.loc for s in all_shots):
        mine = [s for s in all_shots if s.loc == loc_name]
        loc = Lc.LOCATIONS[loc_name](kk) if loc_name == "inferno" else Lc.LOCATIONS[loc_name]()
        cp, cf, cm, cv = _track(mine, "c", kk)
        kp, kf, km, kv = _track(mine, "k", kk)
        # Голос — только в планах этой локации, где Клод в кадре (на мосту реплики юзера не его — тело не пружинит).
        voice = [0.0] * len(env)
        for s in mine:
            if s.c and s.loc != "studio":
                for i in range(int(s.t0 * 30), min(len(env), int(s.t1 * 30) + 1)):
                    voice[i] = env[i]
        # Мост: реплики юзера — не его; рот и тело Клода — только на его строках.
        for a, b in BRIDGE_CLAUDE:
            for i in range(int(a * 30), min(len(env), int(b * 30) + 1)):
                voice[i] = env[i]
        chars = [Wd.claude(cp or [{"t": 0, **BASE_C}], cf or [{"t": 0, "expr": "smug"}], move=cm, visible=cv, voice=voice, mouth_keys=mk)]
        chars.append(Wd.codex(kp or [{"t": 0, **BASE_K}], kf or [{"t": 0, "expr": "angry"}], move=km, visible=kv, yaw=10))
        extra: list[dict] = []
        lvis = [{"t": 0, "on": False}]
        llen = 0.44
        for s in mine:
            on = bool(s.c and s.k and s.leash)
            lvis.append({"t": s.t0, "on": on})
            if on:
                llen = s.leash
        extra.append(Wd.leash(llen, vis=lvis))
        mods = {}
        for s in mine:
            for f in s.fx:
                if f["id"].startswith("_mod_"):
                    mods[f["id"][5:]] = f
                else:
                    extra.append(f)
        objs = []
        for o in loc["objects"]:
            if o["id"] in mods:
                o = {**o, "keys": mods[o["id"]]["keys"]}
            objs.append(o)
        loc = {**loc, "objects": objs}
        cam: list[dict] = []
        flashes, shakes = [], []
        for s in mine:
            a = {"t": s.t0, **s.cam, "cut": True}
            cam.append(a)
            for tm, cm_ in s.mid:
                if isinstance(cm_, dict) and "position" in cm_:
                    cam.append({"t": tm, **cm_, "ease": "inOutSine"})
            b = s.cam_end or s.cam
            cam.append({"t": round(s.t1 - 0.001, 3), **b, "ease": s.ease if s.cam_end else "linear"})
            if s.flash:
                flashes.append({"t": s.t0, "amount": 0.85, "duration": 0.22})
            if s.shake:
                shakes.append({"t": s.t0, "amp": s.shake, "duration": 0.6})
            # «Ручная» камера: лёгкое живое покачивание весь план (кроме ровного моста).
            if s.loc != "studio":
                tt = s.t0 + 0.05
                while tt < s.t1 - 0.1:
                    shakes.append({"t": round(tt, 3), "amp": 0.0016, "duration": 0.7})
                    tt += 0.45
            # Камера «дышит» на ударах припева: короткий толчок на каждый удар бочки.
            for kt in kk:
                if s.t0 + 0.05 < kt < s.t1 - 0.05 and _in(kt, CHORUS):
                    shakes.append({"t": round(kt, 3), "amp": 0.0035, "duration": 0.28})
        cam.sort(key=lambda k: k["t"])
        sp = Wd.spec(loc, chars, extra, cam, DUR, width=width, height=height, flashes=flashes, shakes=shakes)
        out[loc_name] = sp
    return out, all_shots
