"""Мем-серия 10 «Сделай за ночь» — планы, хореография, вещи, камера и запуск (разметка голоса, дерево файлов, чаты,
декорации — meme3d_ep10.py; голос, субтитры, звук, проверки и выпуск — meme3d_ep10_mix.py).

    python tools/meme3d_ep10_anim.py D:/vvf-offload/memes/ep10 --shots --small        # по кадру с каждого плана
    python tools/meme3d_ep10_anim.py D:/vvf-offload/memes/ep10 --preview 0.3,25.5      # кадры PNG без субтитров
    python tools/meme3d_ep10_anim.py D:/vvf-offload/memes/ep10 --fit-mug               # кружка в руку по осмотру сцены
    python tools/meme3d_ep10_anim.py D:/vvf-offload/memes/ep10 --checks                # текст не на лице + рука с кружкой
    python tools/meme3d_ep10_anim.py D:/vvf-offload/memes/ep10                         # ролик + копия для TG + лист

~30 планов за ~37 с: обложка «47 ФАЙЛОВ» → юзер сонно: «Клод, сделай кнопку «Купить»… Я спать», засыпает → «Одна
кнопка. За ночь. Принято» → 23:00, печатает: «Кнопке, конечно, нужна база данных» (дерево: db/) → 01:00 «Базе нужны
микросервисы» (auth-service…) «Микросервисам — очередь сообщений» (queue/, k8s/) → 03:00 «И документация. Тридцать
страниц» (стопка падает на стол) «Без неё никак» → 05:00, чай остыл: «Сорок семь файлов» (счётчик 47) «Красота» →
07:00, утро в окне, юзер: «Ну что, где кнопка?» → пауза, тикают часы, на сайте пустое место → «Кнопка… в планах на
второй спринт» → глоток холодного чая. Рот Клода — виземы по его слогам (на голосе юзера рот закрыт).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import meme3d_ep7_anim as A7  # noqa: E402
import meme3d_ep8_anim as A8  # noqa: E402
import meme3d_ep9_anim as A9  # noqa: E402
import meme3d_ep10 as E  # noqa: E402
import meme3d_hero_claude as H  # noqa: E402
import meme3d_pilot as P1  # noqa: E402

T = E.T
FPS = 30
DUR = E.DURATION
FY = H.face_height(E.W_HERO)
Keys = A7.Keys
vis = E.vis
AT, END = E.AT, E.END
MOUTH = A9.MOUTH
STEP_FPS = A9.STEP_FPS
on_grid = A9.on_grid

# --------------------------------------------------------------------------- планы

SHOT_STARTS = [
    (0.0, "cover"),
    # Юзер сонно печатает и говорит, засыпает.
    (0.6, "u_type"), (1.9, "chat_ask"), (3.0, "u_sleep"),
    (4.3, "c_cu"), (5.6, "c_ecu"),
    # Ночь: часы прыгают, дерево растёт.
    (E.CLOCK_23, "clock23"), (8.4, "c_type"), (9.6, "tree1"),
    (E.CLOCK_01, "clock01"), (11.8, "tree2"), (13.0, "c_type2"), (14.1, "tree3"), (15.6, "c_side"),
    (E.CLOCK_03, "clock03"), (17.4, "c_docs"), (18.5, "docs"), (20.0, "c_serious"),
    (E.CLOCK_05, "clock05"), (22.4, "tree47"), (23.6, "c_happy"),
    # Утро.
    (E.MORNING, "morning"), (E.WAKE, "u_wake"), (E.LOOK, "c_look"), (E.EMPTY, "site"), (30.3, "c_ecu2"), (32.0, "c_sprint"),
    (E.TEA, "tea_sip"), (35.2, "c_end"),
]


def _shots() -> list[tuple[float, float, str]]:
    a = [(on_grid(t0), name) for t0, name in SHOT_STARTS]
    return [(t0, a[i + 1][0] if i + 1 < len(a) else DUR, name) for i, (t0, name) in enumerate(a)]


SHOTS = _shots()
SH = {name: (a, b) for a, b, name in SHOTS}
TREE_WINDOWS = [SH["tree1"], SH["tree2"], SH["tree3"], SH["tree47"]]

# --------------------------------------------------------------------------- герой

TEA = SH["tea_sip"][0]
SIP_UP, SIP_HOLD, SIP_DOWN = TEA + 0.35, TEA + 0.75, TEA + 0.85


def room_claude(env: list[float], mouth_keys: list[dict]) -> dict:
    k = Keys(turn=-6, tilt=-4)
    # Обложка; «Одна кнопка. За ночь. Принято.» — ровно в камеру.
    k.f(0, "deadpan", [0.1, 0.05])
    k.p(AT["c1a"], "inOutSine", tilt=0)
    k.f(AT["c1c"] - 0.05, "smug", [0.05, 0.05])
    # Ночь: бодро печатает (за кадром — с часов 23:00).
    k.p(SH["clock23"][0], "step", turn=-26, lean=14, tilt=0, armL="type", armR="type", type=1)
    k.f(SH["clock23"][0], "working", [-0.6, -0.3])
    k.f(SH["c_type2"][0], "focused", [-0.6, -0.3])
    k.f(SH["c_side"][0], "working", [-0.6, -0.3])
    # «И документация.» — к камере, палец вверх; «Без неё никак.» — каменно, руки скрещены.
    k.p(SH["c_docs"][0], "step", turn=-6, lean=0, type=0, armL="rest", armR="point")
    k.f(SH["c_docs"][0], "smug", [0.05, 0.1])
    k.p(SH["c_serious"][0], "step", armL="cross", armR="cross", tilt=-5)
    k.f(SH["c_serious"][0], "deadpan", [0, 0])
    # 05:00 — снова печатает; «Красота.» — откинулся, руки в боки.
    k.p(SH["clock05"][0], "step", turn=-26, lean=14, tilt=0, armL="type", armR="type", type=1)
    k.f(SH["clock05"][0], "working", [-0.6, -0.3])
    k.p(SH["c_happy"][0], "step", turn=-10, lean=-6, type=0, armL="hips", armR="hips", tilt=4)
    k.f(SH["c_happy"][0], "happy", [0.05, 0.1])
    # Утро: всё ещё доволен; «где кнопка?» — медленно поворачивается к экрану.
    k.p(E.LOOK + 0.15, "inOutSine", dur=0.9, turn=-30, lean=6, tilt=0, armL="rest", armR="rest")
    k.f(E.LOOK + 0.1, "neutral", [-0.3, -0.1])
    k.f(E.LOOK + 0.7, "stare", [-0.8, -0.25])
    # «Кнопка…» — медленно обратно, глаза в сторону; «в планах на второй спринт» — дежурная улыбка.
    k.p(SH["c_ecu2"][0] + 0.75, "inOutSine", dur=0.6, turn=-6, lean=0)
    k.f(SH["c_ecu2"][0] + 0.5, "side", [-0.7, 0.0])
    k.f(AT["c7b"] - 0.1, "fake", [0.05, 0.05])
    # Чай: тянется, отхлёбывает холодный — каменно.
    k.p(SH["tea_sip"][0], "step", turn=-4, lean=-2, tilt=0)
    k.f(SH["tea_sip"][0], "deadpan", [0.1, 0.05])
    k.p(SIP_UP, "inOutSine", dur=0.45, armL=[-8, 85, 0.95, 0])
    k.p(SIP_HOLD, "inOutSine", dur=0.35, armL=[8, 85, 1.0, 0], lean=-6)
    k.p(SIP_DOWN + 0.2, "inOutSine", dur=0.3, armL=[-12, 85, 0.95, 0], lean=-2)
    k.f(SH["c_end"][0], "meh", [0, 0.05])
    pose, face = k.sorted()
    ch = H.hero(pose, face, env, None, position=E.CL, yaw=-6, width=E.W_HERO, label="Claude", char_id="claude", seed=7, glow=0.05,
                headset=True, mouth=True, mouth_keys=mouth_keys)
    ch["critter"]["mouth"] = dict(MOUTH)
    ch["autoBlink"] = True
    return ch


def mochi_windows() -> list[tuple[str, str, tuple[float, float]]]:
    # Сонно печатает → сонно сидит («Я спать») → спит → проснулся и ждёт кнопку.
    marks = [(0.0, "sleep", "type"), (E.ASK, "sleep", "idle"), (E.SLEEP, "sleep", "sleep"), (E.WAKE, "normal", "idle"), (AT["u2"] + 0.4, "happy", "idle")]
    return [(eyes, mood, (t0, marks[i + 1][0] if i + 1 < len(marks) else DUR + 1)) for i, (t0, eyes, mood) in enumerate(marks)]


# --------------------------------------------------------------------------- вещи


def panel(oid: str, screen: str, params: dict, windows: list[tuple[float, float]], phase: float) -> dict:
    return {"id": oid, "primitive": "chat_panel",
            "params": {"width": E.PANEL_W, "height": E.PANEL_H, "screen": screen, "screenParams": params, "phase": phase, "px": 1000},
            "position": E.PANEL, "rotation": [0, E.PANEL_YAW, 0], "visible": vis(*windows)}


def objects(mug_keys: list[dict] | None = None) -> list[dict]:
    objs = E.room_objects()
    objs += [
        panel("chat", "rp_chat", E.chat_params(scale=1.25), [SH["chat_ask"]], 0.7),
        panel("tree", "file_tree", E.tree_params(follow=0.6), TREE_WINDOWS, 0.9),
        panel("site", "button_site", E.site_params(), [SH["site"]], 1.3),
        {"id": "mug", "primitive": "mug", "params": {"color": "#f2efe8", "radius": 0.026, "height": 0.05}, "position": E.MUG,
         "rotation": [0, -30, 0], "keys": mug_keys or default_mug_keys()},
    ]
    objs += E.user_objects(mochi_windows())
    return objs


def default_mug_keys() -> list[dict]:
    """Кружка к «руке» без осмотра сцены (как в серии 9): вверх, глоток, чуть ниже рта."""
    low = [E.SIP[0], E.SIP[1] - 0.035, E.SIP[2]]
    return [{"t": 0, "position": E.MUG}, {"t": TEA, "position": E.MUG, "ease": "step"},
            {"t": SIP_UP, "position": E.SIP, "ease": "outQuad"},
            {"t": SIP_HOLD, "position": [E.SIP[0], E.SIP[1] + 0.012, E.SIP[2]], "ease": "inOutSine"},
            {"t": SIP_DOWN + 0.2, "position": low, "ease": "inOutSine"}]


def mug_keys_from_hands(samples: list[dict]) -> list[dict]:
    """Ручка кружки — в точке левого обрубка (по осмотру сцены покадрово) с ``SIP_UP``; до того — на столе (серия 9)."""
    dx, dz = A9.GRIP_DIR
    n = math.hypot(dx, dz)
    dx, dz = dx / n, dz / n
    yaw = round(math.degrees(math.atan2(-dz, dx)), 2)
    keys = [{"t": 0, "position": E.MUG, "rotation": [0, -30, 0]}, {"t": TEA, "position": E.MUG, "rotation": [0, -30, 0], "ease": "step"}]
    hold = [s for s in samples if s["t"] >= SIP_UP - 1e-6]
    for i, s in enumerate(hold):
        hx, hy, hz = s["characters"]["claude"]["hand_l"]
        keys.append({"t": round(s["t"], 3), "position": [round(hx - A9.GRIP * dx, 4), round(hy - A9.GRIP_Y, 4), round(hz - A9.GRIP * dz, 4)],
                     "rotation": [0, yaw, 0], "ease": "outQuad" if i == 0 else "linear"})
    return keys


# --------------------------------------------------------------------------- камера


def camera() -> tuple[dict, list[dict]]:
    cx, cy, cz = E.CL
    fy = cy + FY
    keys: list[dict] = []
    used: list[str] = []

    def shot(name, A, B=None, fov=34, dof=0.4, ease="inOutSine", roll=0.0):
        used.append(name)
        t0, t1 = SH[name]
        B = B or A
        k0 = {"t": round(t0, 3), "position": [round(v, 4) for v in A[0]], "target": [round(v, 4) for v in A[1]], "fov": fov, "dof": dof, "roll": roll}
        if keys:
            k0["cut"] = True
        keys.extend([k0, {"t": round(t1 - 0.001, 3), "position": [round(v, 4) for v in B[0]], "target": [round(v, 4) for v in B[1]],
                          "fov": fov, "dof": dof, "roll": roll, "ease": ease}])

    def push(A, k=0.08):
        p, t = A
        return ([p[i] + (t[i] - p[i]) * k for i in range(3)], t)

    def along(c, n, d):
        return ([c[0] + n[0] * d, c[1] + n[1] * d, c[2] + n[2] * d], list(c))

    C_CU = ([cx + 0.02, fy + 0.04, cz + 1.0], [cx - 0.01, fy - 0.01, cz])
    C_CU_L = ([cx - 0.28, fy + 0.04, cz + 0.95], [cx - 0.02, fy - 0.01, cz])
    C_ECU = ([cx + 0.03, fy + 0.0, cz + 0.6], [cx, fy - 0.005, cz])
    C_SIDE = ([cx + 0.3, fy + 0.05, cz + 0.92], [cx + 0.01, fy - 0.01, cz])
    COVER = ([cx + 0.02, fy + 0.07, cz + 0.9], [cx, fy + 0.03, cz])
    px, py, pz = E.PANEL
    a = math.radians(E.PANEL_YAW)
    pn = [math.sin(a), 0.0, math.cos(a)]
    WALL = along([px, py + E.PANEL_H / 2, pz], pn, 0.83)
    WALL_CU = along([px, py + E.PANEL_H * 0.6, pz], pn, 0.66)
    WALL_HI = along([px, py + E.PANEL_H * 0.3, pz], pn, 0.83)  # окно чата выше полосы субтитров: пузырь не под словами
    mx, my, mz = E.MOCHI
    xu = E.X_USER
    USER_W = ([xu - 0.02, T + 0.32, 1.3], [xu - 0.03, T + 0.15, 0.0])
    USER_CU = ([mx + 0.2, T + 0.27, mz + 1.02], [mx + 0.06, T + 0.16, mz])
    cl = E.CLOCK
    CLOCK = ([cl[0] + 0.03, cl[1] - 0.02, cl[2] + 0.55], [cl[0], cl[1], cl[2]])
    CLOCK_L = ([cl[0] - 0.12, cl[1] - 0.05, cl[2] + 0.5], [cl[0], cl[1], cl[2]])
    gx, gy, gz = E.MUG
    TWO = ([cx + 0.17, fy + 0.07, cz + 0.98], [cx + 0.1, fy - 0.05, cz])
    qx, qy, qz = E.PILE
    DOCS = ([qx + 0.16, T + 0.42, qz + 0.62], [qx - 0.01, T + 0.06, qz])
    wx, wy, wz = E.WINDOW
    MORN = ([cx - 0.22, fy + 0.16, cz + 1.4], [(cx + wx) / 2 + 0.05, T + 0.4, -0.25])
    TEA_C = ([cx + 0.05, fy + 0.06, cz + 1.05], [cx + 0.01, fy + 0.0, cz])
    END_C = ([cx + 0.04, fy + 0.03, cz + 0.78], [cx + 0.01, fy - 0.0, cz])

    shot("cover", COVER, push(COVER, 0.03), dof=0.25)
    shot("u_type", USER_W, push(USER_W, 0.08), fov=38, dof=0.25)
    shot("chat_ask", WALL_HI, push(WALL_HI, 0.06), dof=0.2)
    shot("u_sleep", USER_CU, push(USER_CU, 0.1), dof=0.45)
    shot("c_cu", C_CU, push(C_CU, 0.08), dof=0.45)
    shot("c_ecu", C_ECU, push(C_ECU, 0.1), dof=0.5)
    shot("clock23", CLOCK, push(CLOCK, 0.1), dof=0.2)
    shot("c_type", TWO, push(TWO, 0.06), dof=0.35)
    shot("tree1", WALL, push(WALL, 0.07), dof=0.2)
    shot("clock01", CLOCK_L, push(CLOCK_L, 0.12), dof=0.2, roll=-2)
    shot("tree2", WALL, push(WALL, 0.08), dof=0.2, roll=1.5)
    shot("c_type2", C_CU_L, push(C_CU_L, 0.08), dof=0.45)
    shot("tree3", WALL, push(WALL, 0.1), dof=0.2, roll=-1.5)
    shot("c_side", C_SIDE, push(C_SIDE, 0.08), dof=0.45)
    shot("clock03", CLOCK, push(CLOCK, 0.12), dof=0.2, roll=2)
    shot("c_docs", C_CU, push(C_CU, 0.08), dof=0.45)
    shot("docs", DOCS, push(DOCS, 0.1), dof=0.25)
    shot("c_serious", C_ECU, push(C_ECU, 0.06), dof=0.5)
    shot("clock05", CLOCK_L, push(CLOCK_L, 0.12), dof=0.2, roll=-2)
    shot("tree47", WALL, push(WALL, 0.12), dof=0.2)
    shot("c_happy", C_CU, push(C_CU, 0.08), dof=0.45, roll=1.5)
    shot("morning", MORN, push(MORN, 0.06), fov=40, dof=0.2)
    shot("u_wake", USER_CU, push(USER_CU, 0.1), dof=0.45)
    shot("c_look", C_SIDE, push(C_SIDE, 0.05), dof=0.45)
    shot("site", WALL, push(WALL, 0.12), dof=0.2)
    shot("c_ecu2", C_ECU, push(C_ECU, 0.08), dof=0.5)
    shot("c_sprint", C_CU, push(C_CU, 0.07), dof=0.45)
    shot("tea_sip", TEA_C, push(TEA_C, 0.05), dof=0.45)
    shot("c_end", END_C, push(END_C, 0.06), dof=0.5)
    if used != [n for _a, _b, n in SHOTS]:
        raise SystemExit(f"камера не совпадает с планами: {sorted(set(SH) ^ set(used))}")
    keys[-1]["t"] = DUR
    shake = [{"t": E.T47, "amp": 0.003, "duration": 0.3}]  # счётчик добежал до 47
    trans = [{"kind": "whip", "t": SH[n][0], "dir": d} for n, d in (("clock23", 1), ("clock01", -1), ("clock03", 1), ("clock05", -1))]
    trans += [{"kind": "smash", "t": SH["morning"][0]}, {"kind": "smash", "t": SH["site"][0]}]
    return {"fov": 34, "keys": keys, "shake": shake, "clamp": False}, trans


def build_spec(env_c: list[float], mouth_keys: list[dict], mug_keys: list[dict] | None = None) -> dict:
    cam, trans = camera()
    return {"width": 1080, "height": 1920, "fps": FPS, "duration": DUR, "environment": E.environment(trans), "lights": E.lights(),
            "objects": objects(mug_keys), "characters": [room_claude(env_c, mouth_keys)], "camera": cam,
            "quality": {"shadows": True, "shadowSize": 2048, "antialias": True}}


# --------------------------------------------------------------------------- запуск


def free_commit_gb() -> float:
    """Свободная память выделения (commit), ГБ — по ней ждём перед тяжёлым рендером (ПК общий, подкачка конечна)."""
    out = subprocess.run(["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_OperatingSystem).FreeVirtualMemory"],
                         capture_output=True, text=True, check=False).stdout.strip()
    try:
        return int(out) / 1024 / 1024
    except ValueError:
        return 0.0


def wait_commit(min_gb: float = 4.0, tries: int = 60) -> None:
    for _ in range(tries):
        gb = free_commit_gb()
        if gb >= min_gb:
            P1._log(f"свободно commit {gb:.1f} ГБ")
            return
        P1._log(f"свободно commit {gb:.1f} ГБ < {min_gb} — жду")
        time.sleep(30)
    raise SystemExit("мало памяти — рендер отложен")


def main() -> int:
    import meme3d_ep10_mix as M

    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--preview", help="кадры PNG сцены без субтитров: секунды через запятую")
    ap.add_argument("--shots", action="store_true", help="превью: по кадру из середины каждого плана")
    ap.add_argument("--inspect", help="положения героев и вещей в моменты t через запятую (JSON в work/inspect.json)")
    ap.add_argument("--fit-mug", action="store_true", help="кружка в руку по осмотру сцены (work/mug_keys.json)")
    ap.add_argument("--checks", action="store_true", help="текст не на лице Клода + рука с кружкой (work/checks.json)")
    ap.add_argument("--small", action="store_true", help="превью 540×960")
    ap.add_argument("--gl", default="gpu")
    ap.add_argument("--name", default="ep10")
    ap.add_argument("--skip-render", action="store_true")
    ap.add_argument("--no-build", action="store_true")
    a = ap.parse_args()
    os.environ.setdefault("MOTION_PACKS_DIR", str(P1.PACKS))
    out_dir = Path(a.out_dir).resolve()
    work = out_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    voice, voice_c, lines, _all, spans = A9.assemble_voice(work, ep=E, dur=DUR)
    words = M.caption_words(_all)
    mouth = H.viseme_keys_from_words(A9.syllable_words([w for w in words if w.get("s") != "B"]))
    mug_file = work / "mug_keys.json"
    mug_keys = json.loads(mug_file.read_text("utf-8")) if mug_file.is_file() else None
    spec = build_spec(P1.mouth_envelope(voice_c), mouth, mug_keys)
    if a.small:
        spec["width"], spec["height"] = 540, 960
        spec["quality"]["shadowSize"] = 1024
    sp = work / "scene.json"
    sp.write_text(json.dumps(spec, ensure_ascii=False), "utf-8")
    (work / "events.json").write_text(json.dumps({"spans": spans, "shots": SHOTS, "words": words}, ensure_ascii=False, indent=1), "utf-8")
    if not a.no_build:
        P1._run(["npm", "run", "build:scene3d"], "сборка движка", cwd=ROOT, shell=sys.platform == "win32")
    render = ["node", str(ROOT / "node" / "scene3d" / "render.mjs"), "--spec", str(sp), "--packs", str(P1.PACKS), "--gl", a.gl, "--workers", "1"]
    if a.inspect:
        r = P1._run([*render, "--inspect-list", a.inspect], "осмотр")
        (work / "inspect.json").write_text(r.stdout.strip().splitlines()[-1], "utf-8")
        print(work / "inspect.json")
        return 0
    if a.fit_mug:
        wait_commit(4.0)
        ts = [round(TEA + i * 2 / FPS, 3) for i in range(int((DUR - TEA) * FPS / 2) + 1)]
        r = P1._run([*render, "--inspect-list", ",".join(f"{t:.3f}" for t in ts)], "осмотр")
        keys = mug_keys_from_hands(json.loads(r.stdout.strip().splitlines()[-1])["inspect"])
        mug_file.write_text(json.dumps(keys, ensure_ascii=False), "utf-8")
        print(mug_file, len(keys))
        return 0
    if a.checks:
        wait_commit(4.0)
        res = M.scene_checks(render, words, work)
        print(json.dumps(res, ensure_ascii=False, indent=1)[:4000])
        return 0
    if a.preview or a.shots:
        times = a.preview or ",".join(f"{(s0 + s1) / 2:.2f}" for s0, s1, _ in SHOTS)
        wait_commit(4.0)
        prev = work / "preview"
        shutil.rmtree(prev, ignore_errors=True)
        r = P1._run([*render, "--preview", times, "--preview-dir", str(prev)], "превью")
        print(r.stdout.strip().splitlines()[-1][:600])
        return 0
    return M.release(render, work, out_dir, a.name, words, voice, skip_render=a.skip_render)


if __name__ == "__main__":
    sys.exit(main())
