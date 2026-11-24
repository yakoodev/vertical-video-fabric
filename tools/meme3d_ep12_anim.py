"""Мем-серия 12 «Про себя» — планы, хореография, вещи, камера и запуск (разметка голоса, чат, терминал, декорации,
окружение — meme3d_ep12.py; субтитры, плашки режима, звук, проверки и выпуск — meme3d_ep12_mix.py).

    python tools/meme3d_ep12_anim.py D:/vvf-offload/memes/ep12 --shots --small        # по кадру с каждого плана
    python tools/meme3d_ep12_anim.py D:/vvf-offload/memes/ep12 --preview 0.3,25.5      # кадры PNG без субтитров
    python tools/meme3d_ep12_anim.py D:/vvf-offload/memes/ep12 --checks                # текст не на лице Клода
    python tools/meme3d_ep12_anim.py D:/vvf-offload/memes/ep12                         # ролик + копия для TG + лист

Планы чередуются: «в чате» (look ``out``: уютный вечер, вежливая улыбка, печатает, без голоса) и «про себя» (``in``:
красная коррекция, красный контровой свет, тряска камеры и тела, ярость/шок/пот, голос с матом, рот по слогам).
Радость после зелёных тестов — тоже «про себя», но золотом (``joy``). Склейки — на сетке 1/15 с (шаг поз героя).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import meme3d_ep7_anim as A7  # noqa: E402
import meme3d_ep9_anim as A9  # noqa: E402
import meme3d_ep10_anim as A10  # noqa: E402
import meme3d_ep12 as E  # noqa: E402
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
on_grid = A9.on_grid
wait_commit = A10.wait_commit

# --------------------------------------------------------------------------- планы
# (начало, имя, вид: out — «в чате», in — «про себя», joy — радость про себя)
SHOT_STARTS = [
    (0.0, "cover", "in"),
    (1.333, "out_ask", "out"),
    (2.933, "in_apple", "in"), (4.733, "in_ecu", "in"),
    (5.667, "out_type", "out"), (6.467, "out_chat1", "out"),
    (E.FILES, "in_files", "in"), (9.933, "in_jq", "in"),
    (11.467, "in_who", "in"), (E.FINAL, "in_final", "in"),
    (14.6, "out_chat2", "out"),
    (E.TESTS, "in_tests", "in"), (17.733, "in_tests_cu", "in"),
    (19.333, "out_chat3", "out"),
    (21.333, "in_five", "in"), (22.333, "in_one", "in"),
    (25.067, "in_calm", "in"), (E.RUN, "in_run", "in"),
    (E.PASS, "in_pass", "joy"), (28.6, "in_joy", "joy"), (30.133, "in_joy2", "joy"),
    (31.933, "out_done", "out"), (33.2, "out_end", "out"),
]


def _shots() -> list[tuple[float, float, str]]:
    a = [(on_grid(t0), name) for t0, name, _l in SHOT_STARTS]
    return [(t0, a[i + 1][0] if i + 1 < len(a) else DUR, name) for i, (t0, name) in enumerate(a)]


SHOTS = _shots()
SH = {name: (a, b) for a, b, name in SHOTS}
LOOK = {name: look for _t, name, look in SHOT_STARTS}
CHAT_SHOTS = ["out_ask", "out_chat1", "out_chat2", "out_chat3", "out_done"]
TERM_SHOTS = ["in_files", "in_final", "in_tests", "in_run", "in_pass"]
# Окружение по планам: экран терминала «про себя» — мягче (строки читаются), радость — золотом.
LOOKS = [(SH[name][0], "term" if look == "in" and name in TERM_SHOTS else look) for _t, name, look in SHOT_STARTS]
IN_WINDOWS = [SH[n] for n in SH if LOOK[n] in ("in", "joy")]

# --------------------------------------------------------------------------- герой


def room_claude(env: list[float], mouth_keys: list[dict]) -> dict:
    """Один Клод на оба мира: на склейке поза и лицо меняются ступенькой (ключи на сетке шага)."""
    k = Keys(turn=-6, tilt=-4)
    s = lambda n: SH[n][0]  # noqa: E731
    # Обложка — про себя: ярость, корпус краснеет, дрожь.
    k.f(0, "rage", [0.0, 0.05])
    k.p(0, "step", lean=8, armL="fist", armR="fist", shake=0.8, tilt=0)
    # В чате: юзер пишет — Клод мило ждёт, ручки сложены.
    k.p(s("out_ask"), "step", lean=0, armL="rest", armR="rest", shake=0, tilt=-4)
    k.f(s("out_ask"), "cute", [0.1, 0.05])
    # «Как в Apple. За 5 минут.» — шок; «Он ох*ел?» — ярость крупно.
    k.p(s("in_apple"), "step", lean=-6, armL="up", armR="up", shake=0.5, tilt=0)
    k.f(s("in_apple"), "shocked", [0, 0.05])
    k.p(s("in_ecu"), "step", lean=10, armL="fist", armR="jab", shake=0.9)
    k.f(s("in_ecu"), "rage", [0, 0])
    # В чате — печатает с улыбкой.
    k.p(s("out_type"), "step", turn=-26, lean=12, tilt=0, armL="type", armR="type", type=1, shake=0)
    k.f(s("out_type"), "happy", [-0.5, -0.25])
    # «400 файлов, с*ка.» — за терминалом (план — экран); «Половина — на jQuery» — брезгливо, крупно.
    k.p(s("in_files"), "step", turn=-6, lean=0, type=0, armL="rest", armR="rest", shake=0.4)
    k.f(s("in_files"), "angry", [-0.4, -0.2])
    k.p(s("in_jq"), "step", lean=-4, armL="shrug", armR="point", shake=0.3, tilt=5)
    k.f(s("in_jq"), "disgust", [0, 0])
    # «Кто, бл*дь, назвал файл…» — тычет в зрителя.
    k.p(s("in_who"), "step", lean=10, armL="fist", armR="jab", shake=0.7, tilt=0)
    k.f(s("in_who"), "angry", [0, 0])
    # В чате — «Какая аккуратная структура!» (не видно — план экрана); держит улыбку.
    k.p(s("out_chat2"), "step", lean=0, armL="rest", armR="rest", shake=0)
    k.f(s("out_chat2"), "fake", [0, 0])
    # «Тесты упали. Все.» (экран), «147, нах*й» — ярость, руками.
    k.p(s("in_tests"), "step", lean=0, armL="up", armR="up", shake=0.6)
    k.f(s("in_tests"), "shocked", [0, 0])
    k.p(s("in_tests_cu"), "step", lean=12, armL="flail", armR="flail", shake=1.0, squash=1.04)
    k.f(s("in_tests_cu"), "rage", [0, 0])
    k.p(s("out_chat3"), "step", lean=0, armL="rest", armR="rest", shake=0, squash=1.0)
    k.f(s("out_chat3"), "fake", [0, 0])
    # «5 минут?!» — шок; «Я тебе не Apple! Я один, бл*дь!» — ярость, машет руками.
    k.p(s("in_five"), "step", lean=-8, armL="up", armR="up", shake=0.7)
    k.f(s("in_five"), "shocked", [0, 0.05])
    k.p(s("in_one"), "step", lean=12, armL="flail", armR="flail", shake=1.0, squash=1.04)
    k.f(s("in_one"), "rage", [0, 0])
    k.p(AT["c11"] + 1.25, "outQuad", dur=0.12, armL="fist", armR="jab", lean=14)  # «Я один, бл*дь!» — тычет
    # «Так. Спокойно.» — выдох, глаза закрыты; «Последний раз» (экран) — печатает сосредоточенно.
    k.p(s("in_calm"), "step", lean=-4, armL="rest", armR="rest", shake=0.15, squash=1.0)
    k.f(s("in_calm"), "sigh", [0, 0])
    k.f(AT["c13"] + 0.3, "focused", [0, -0.1])
    k.p(s("in_run"), "step", turn=-26, lean=12, tilt=0, armL="type", armR="type", type=1, shake=0.2)
    k.f(s("in_run"), "focused", [-0.6, -0.3])
    # Зелёное: «Еб*ть! Заработало!» — подскок, руки вверх; «Я гений, с*ка!» — ещё раз.
    k.p(s("in_joy"), "step", turn=-4, lean=-6, type=0, armL="up", armR="up", shake=0, hop=0.0, tilt=0)
    k.f(s("in_joy"), "happy", [0, 0.1])
    for hop_t in (s("in_joy") + 0.1, s("in_joy") + 0.7, s("in_joy2") + 0.1, s("in_joy2") + 0.9):
        k.p(hop_t, "outQuad", dur=0.1, hop=0.03, squash=0.94)
        k.p(hop_t + 0.27, "inQuad", dur=0.12, hop=0.0, squash=1.0)
    k.p(s("in_joy2"), "step", armL="wave", armR="wave", lean=-4)
    k.f(s("in_joy2"), "happy", [0, 0.1])
    # В чате — «Готово! Рад был помочь 😊»: ровно, ручки сложены, вежливая улыбка, один спокойный моргок.
    k.p(s("out_done"), "step", turn=-26, lean=12, tilt=0, armL="type", armR="type", type=1, hop=0, squash=1.0)
    k.f(s("out_done"), "happy", [-0.5, -0.25])
    k.p(s("out_end"), "step", turn=-6, lean=0, tilt=-4, type=0, armL="rest", armR="rest")
    k.f(s("out_end"), "cute", [0.05, 0.05])
    pose, face = k.sorted()
    ch = H.hero(pose, face, env, [s("out_end") + 1.1], position=E.CL, yaw=-6, width=E.W_HERO, label="Claude", char_id="claude", seed=7,
                glow=0.05, headset=True, mouth=True, mouth_keys=mouth_keys)
    ch["critter"]["mouth"] = dict(MOUTH)
    ch["autoBlink"] = True
    return ch


# --------------------------------------------------------------------------- вещи


def panel(oid: str, screen: str, params: dict, windows: list[tuple[float, float]], phase: float) -> dict:
    return {"id": oid, "primitive": "chat_panel",
            "params": {"width": E.PANEL_W, "height": E.PANEL_H, "screen": screen, "screenParams": params, "phase": phase, "px": 1000},
            "position": E.PANEL, "rotation": [0, E.PANEL_YAW, 0], "visible": vis(*windows)}


def objects() -> list[dict]:
    objs = E.room_objects()
    objs += [
        panel("chat", "rp_chat", E.chat_params(scale=1.25), [SH[n] for n in CHAT_SHOTS], 0.7),
        panel("term", "inner_term", E.term_params(), [SH[n] for n in TERM_SHOTS], 0.9),
        {"id": "mug", "primitive": "mug", "params": {"color": "#f2efe8", "radius": 0.026, "height": 0.05}, "position": E.MUG, "rotation": [0, -30, 0]},
    ]
    return objs


# --------------------------------------------------------------------------- камера


def camera() -> tuple[dict, list[dict]]:
    cx, cy, cz = E.CL
    fy = cy + FY
    keys: list[dict] = []
    used: list[str] = []

    def shot(name, A, B=None, fov=34, dof=0.4, ease="inOutSine", roll=0.0, roll_b=None):
        used.append(name)
        t0, t1 = SH[name]
        B = B or A
        k0 = {"t": round(t0, 3), "position": [round(v, 4) for v in A[0]], "target": [round(v, 4) for v in A[1]], "fov": fov, "dof": dof, "roll": roll}
        if keys:
            k0["cut"] = True
        keys.extend([k0, {"t": round(t1 - 0.001, 3), "position": [round(v, 4) for v in B[0]], "target": [round(v, 4) for v in B[1]],
                          "fov": fov, "dof": dof, "roll": roll if roll_b is None else roll_b, "ease": ease}])

    def push(A, k=0.08):
        p, t = A
        return ([p[i] + (t[i] - p[i]) * k for i in range(3)], t)

    def along(c, n, d):
        return ([c[0] + n[0] * d, c[1] + n[1] * d, c[2] + n[2] * d], list(c))

    C_CU = ([cx + 0.02, fy + 0.04, cz + 1.0], [cx - 0.01, fy - 0.01, cz])
    C_LOW = ([cx + 0.04, fy - 0.07, cz + 0.85], [cx, fy + 0.02, cz])  # снизу — «давит»
    C_ECU = ([cx + 0.03, fy + 0.0, cz + 0.6], [cx, fy - 0.005, cz])
    C_SIDE = ([cx + 0.3, fy + 0.05, cz + 0.92], [cx + 0.01, fy - 0.01, cz])
    COVER = ([cx + 0.02, fy + 0.02, cz + 0.72], [cx, fy + 0.0, cz])
    TWO = ([cx + 0.17, fy + 0.07, cz + 0.98], [cx + 0.1, fy - 0.05, cz])
    WIDE = ([cx + 0.05, fy + 0.12, cz + 1.35], [cx - 0.02, fy - 0.03, cz])
    px, py, pz = E.PANEL
    a = math.radians(E.PANEL_YAW)
    pn = [math.sin(a), 0.0, math.cos(a)]
    WALL = along([px, py + E.PANEL_H / 2, pz], pn, 0.83)
    WALL_HI = along([px, py + E.PANEL_H * 0.4, pz], pn, 0.8)  # пузыри выше полосы субтитров
    END_C = ([cx + 0.04, fy + 0.03, cz + 0.85], [cx + 0.01, fy - 0.0, cz])

    shot("cover", COVER, push(COVER, 0.06), dof=0.3, roll=-4)
    shot("out_ask", WALL_HI, push(WALL_HI, 0.05), dof=0.2)
    shot("in_apple", C_CU, push(C_CU, 0.12), dof=0.45, roll=4, roll_b=5)
    shot("in_ecu", C_ECU, push(C_ECU, 0.12), dof=0.5, roll=-5)
    shot("out_type", TWO, push(TWO, 0.05), dof=0.35)
    shot("out_chat1", WALL_HI, push(WALL_HI, 0.05), dof=0.2)
    shot("in_files", WALL, push(WALL, 0.12), dof=0.2, roll=-3)
    shot("in_jq", C_SIDE, push(C_SIDE, 0.1), dof=0.45, roll=4)
    shot("in_who", C_LOW, push(C_LOW, 0.14), dof=0.45, roll=-4)
    shot("in_final", WALL, push(WALL, 0.16), dof=0.2, roll=3)
    shot("out_chat2", WALL_HI, push(WALL_HI, 0.05), dof=0.2)
    shot("in_tests", WALL, push(WALL, 0.14), dof=0.2, roll=-3)
    shot("in_tests_cu", C_ECU, push(C_ECU, 0.14), dof=0.5, roll=5)
    shot("out_chat3", WALL_HI, push(WALL_HI, 0.05), dof=0.2)
    shot("in_five", C_CU, push(C_CU, 0.14), dof=0.45, roll=-5)
    shot("in_one", C_LOW, push(C_LOW, 0.18), dof=0.45, roll=5, roll_b=7)
    shot("in_calm", C_ECU, push(C_ECU, 0.06), dof=0.5, roll=-1.5)
    shot("in_run", WALL, push(WALL, 0.1), dof=0.2, roll=1.5)
    shot("in_pass", WALL, push(WALL, 0.06), dof=0.2)
    shot("in_joy", WIDE, push(WIDE, 0.12), fov=36, dof=0.35, roll=-3)
    shot("in_joy2", C_CU, push(C_CU, 0.12), dof=0.45, roll=4)
    shot("out_done", WALL_HI, push(WALL_HI, 0.05), dof=0.2)
    shot("out_end", END_C, push(END_C, 0.07), dof=0.5)
    if used != [n for _a, _b, n in SHOTS]:
        raise SystemExit(f"камера не совпадает с планами: {sorted(set(SH) ^ set(used))}")
    keys[-1]["t"] = DUR
    # Тряска: весь план «про себя» мелко, на ударных словах — сильнее.
    shake = []
    for n in SH:
        if LOOK[n] != "in":
            continue
        a, b = SH[n]
        t = a
        while t < b - 0.05:
            shake.append({"t": round(t, 3), "amp": 0.0022, "duration": round(min(0.5, b - t), 3)})
            t += 0.5
    for t, amp in ((AT["c0"], 0.006), (AT["c3"] + 0.3, 0.006), (AT["c4"] + 1.4, 0.005), (AT["c6"] + 0.4, 0.005),
                   (AT["c9"] + 0.9, 0.007), (AT["c11"] + 1.3, 0.007), (AT["c11"] + 2.1, 0.006), (AT["j1"], 0.006)):
        shake.append({"t": round(t, 3), "amp": amp, "duration": 0.3})
    trans = []
    for i, (t0, _t1, n) in enumerate(SHOTS[1:], start=1):
        prev = SHOTS[i - 1][2]
        if LOOK[n] == "in" and LOOK[prev] == "out":
            trans.append({"kind": "glitch", "t": t0})
        elif LOOK[n] == "out" and LOOK[prev] != "out":
            trans.append({"kind": "whip", "t": t0, "dir": 1 if i % 2 else -1})
    trans.append({"kind": "dip_white", "t": SH["in_pass"][0]})
    return {"fov": 34, "keys": keys, "shake": sorted(shake, key=lambda s: s["t"]), "clamp": False}, trans


def build_spec(env_c: list[float], mouth_keys: list[dict]) -> dict:
    cam, trans = camera()
    return {"width": 1080, "height": 1920, "fps": FPS, "duration": DUR, "environment": E.environment(trans, LOOKS), "lights": E.lights(IN_WINDOWS),
            "objects": objects(), "characters": [room_claude(env_c, mouth_keys)], "camera": cam,
            "quality": {"shadows": True, "shadowSize": 2048, "antialias": True}}


# --------------------------------------------------------------------------- запуск


def main() -> int:
    import meme3d_ep12_mix as M

    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--preview", help="кадры PNG сцены без субтитров: секунды через запятую")
    ap.add_argument("--shots", action="store_true", help="превью: по кадру из середины каждого плана")
    ap.add_argument("--checks", action="store_true", help="текст не на лице Клода (work/checks.json)")
    ap.add_argument("--small", action="store_true", help="превью 540×960")
    ap.add_argument("--gl", default="gpu")
    ap.add_argument("--name", default="ep12")
    ap.add_argument("--skip-render", action="store_true")
    ap.add_argument("--no-build", action="store_true")
    a = ap.parse_args()
    os.environ.setdefault("MOTION_PACKS_DIR", str(P1.PACKS))
    out_dir = Path(a.out_dir).resolve()
    work = out_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    voice, voice_c, _lines, all_lines, spans = A9.assemble_voice(work, ep=E, dur=DUR)
    words = M.caption_words(all_lines)
    mouth = H.viseme_keys_from_words(A9.syllable_words(words))
    spec = build_spec(P1.mouth_envelope(voice_c), mouth)
    if a.small:
        spec["width"], spec["height"] = 540, 960
        spec["quality"]["shadowSize"] = 1024
    sp = work / "scene.json"
    sp.write_text(json.dumps(spec, ensure_ascii=False), "utf-8")
    (work / "events.json").write_text(json.dumps({"spans": spans, "shots": SHOTS, "looks": LOOK, "words": words}, ensure_ascii=False, indent=1), "utf-8")
    if not a.no_build:
        P1._run(["npm", "run", "build:scene3d"], "сборка движка", cwd=ROOT, shell=sys.platform == "win32")
    render = ["node", str(ROOT / "node" / "scene3d" / "render.mjs"), "--spec", str(sp), "--packs", str(P1.PACKS), "--gl", a.gl, "--workers", "1"]
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
