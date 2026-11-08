"""Мокап CMU (ASF/AMC) → клип для VRM-героя (node/vrm): локальные повороты нормализованных костей гуманоида.

    python tools/cmu_to_vrm.py D:/vvf-offload/vtubika/vrm/vendor/cmu/141.asf D:/.../141_16.amc OUT.json
        [--from 1.0] [--to 6.5] [--fps 30] [--name wave]

Данные: CMU Graphics Lab Motion Capture Database (mocap.cs.cmu.edu) — «free for all uses»; можно включать в
продаваемые продукты, нельзя перепродавать сами данные (в т. ч. конвертированные). Поэтому файлы мокапа и клипы
лежат вне репозитория (D:/vvf-offload/vtubika/vrm/vendor/cmu), здесь — только конвертер.

Перенос — через мировые повороты: поза покоя CMU (ASF) — Т-поза, Y вверх, левая рука в +X, лицом в +Z — та же,
что у нормализованного скелета VRM (у всех костей в покое единичный поворот). Поэтому мировой поворот кости CMU
(прямая кинематика ASF/AMC) и есть мировой поворот соответствующей кости VRM; локальный — относительно мирового
родителя по иерархии VRM. Исполнитель разворачивается лицом в +Z (средний курс корпуса за клип). Пальцев в CMU нет
(один маркер на кисть) — пальцы ставит node/vrm (набор поз пальцев с пределами суставов).
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np

# Кость VRM → кость CMU (мировой поворот). Родители — по иерархии VRM.
MAP = {
    "hips": "root", "spine": "lowerback", "chest": "upperback", "upperChest": "thorax", "neck": "lowerneck",
    "head": "head",
    "leftShoulder": "lclavicle", "leftUpperArm": "lhumerus", "leftLowerArm": "lradius", "leftHand": "lhand",
    "rightShoulder": "rclavicle", "rightUpperArm": "rhumerus", "rightLowerArm": "rradius", "rightHand": "rhand",
    "leftUpperLeg": "lfemur", "leftLowerLeg": "ltibia", "leftFoot": "lfoot",
    "rightUpperLeg": "rfemur", "rightLowerLeg": "rtibia", "rightFoot": "rfoot",
}
PARENT = {
    "spine": "hips", "chest": "spine", "upperChest": "chest", "neck": "upperChest", "head": "neck",
    "leftShoulder": "upperChest", "leftUpperArm": "leftShoulder", "leftLowerArm": "leftUpperArm", "leftHand": "leftLowerArm",
    "rightShoulder": "upperChest", "rightUpperArm": "rightShoulder", "rightLowerArm": "rightUpperArm", "rightHand": "rightLowerArm",
    "leftUpperLeg": "hips", "leftLowerLeg": "leftUpperLeg", "leftFoot": "leftLowerLeg",
    "rightUpperLeg": "hips", "rightLowerLeg": "rightUpperLeg", "rightFoot": "rightLowerLeg",
}
UNIT_M = (1.0 / 0.45) * 2.54 / 100.0  # длины ASF/AMC → метры (FAQ CMU)


def rx(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])


def ry(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def rz(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def euler_xyz(x: float, y: float, z: float) -> np.ndarray:
    """Порядок ASF «XYZ» (сначала X): R = Rz·Ry·Rx, углы в градусах."""
    return rz(math.radians(z)) @ ry(math.radians(y)) @ rx(math.radians(x))


def parse_asf(path: Path) -> tuple[dict, dict]:
    bones: dict[str, dict] = {"root": {"axis": np.eye(3), "dof": ["rx", "ry", "rz"], "dir": np.zeros(3), "len": 0.0}}
    children: dict[str, list[str]] = {}
    lines = path.read_text("latin-1").splitlines()
    section, cur = "", None
    for raw in lines:
        line = raw.strip()
        if line.startswith(":"):
            section = line.split()[0]
            continue
        if section == ":bonedata":
            parts = line.split()
            if not parts:
                continue
            if parts[0] == "begin":
                cur = {"dof": []}
            elif parts[0] == "end" and cur is not None:
                bones[cur["name"]] = {"axis": cur.get("axis", np.eye(3)), "dof": cur["dof"],
                                      "dir": cur.get("dir", np.zeros(3)), "len": cur.get("len", 0.0)}
                cur = None
            elif cur is not None:
                if parts[0] == "name":
                    cur["name"] = parts[1]
                elif parts[0] == "direction":
                    cur["dir"] = np.array([float(v) for v in parts[1:4]])
                elif parts[0] == "length":
                    cur["len"] = float(parts[1])
                elif parts[0] == "axis":
                    cur["axis"] = euler_xyz(*(float(v) for v in parts[1:4]))
                elif parts[0] == "dof":
                    cur["dof"] = parts[1:]
        elif section == ":hierarchy":
            parts = line.split()
            if parts and parts[0] not in ("begin", "end"):
                children[parts[0]] = parts[1:]
    return bones, children


def parse_amc(path: Path) -> list[dict[str, list[float]]]:
    frames: list[dict[str, list[float]]] = []
    for raw in path.read_text("latin-1").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ":")):
            continue
        parts = line.split()
        if parts[0].isdigit() and len(parts) == 1:
            frames.append({})
        elif frames:
            frames[-1][parts[0]] = [float(v) for v in parts[1:]]
    return frames


def world_rotations(bones: dict, children: dict, frame: dict[str, list[float]]) -> tuple[dict[str, np.ndarray], np.ndarray]:
    """Прямая кинематика: мировые повороты костей CMU и положение корня (метры)."""
    out: dict[str, np.ndarray] = {}
    r = frame.get("root", [0.0] * 6)
    out["root"] = euler_xyz(r[3], r[4], r[5])
    stack = ["root"]
    while stack:
        name = stack.pop()
        for ch in children.get(name, []):
            b = bones[ch]
            vals = frame.get(ch, [])
            ang = {d: vals[i] for i, d in enumerate(b["dof"]) if i < len(vals)}
            local = euler_xyz(ang.get("rx", 0.0), ang.get("ry", 0.0), ang.get("rz", 0.0))
            out[ch] = out[name] @ b["axis"] @ local @ b["axis"].T
            stack.append(ch)
    return out, np.array(r[:3]) * UNIT_M


def quat(m: np.ndarray) -> list[float]:
    """Матрица поворота → кватернион [x, y, z, w]."""
    t = np.trace(m)
    if t > 0:
        s = math.sqrt(t + 1.0) * 2
        q = [(m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s, 0.25 * s]
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = math.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2
        q = [0.25 * s, (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s, (m[2, 1] - m[1, 2]) / s]
    elif m[1, 1] > m[2, 2]:
        s = math.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2
        q = [(m[0, 1] + m[1, 0]) / s, 0.25 * s, (m[1, 2] + m[2, 1]) / s, (m[0, 2] - m[2, 0]) / s]
    else:
        s = math.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2
        q = [(m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s, 0.25 * s, (m[1, 0] - m[0, 1]) / s]
    n = math.sqrt(sum(v * v for v in q)) or 1.0
    return [round(v / n, 5) for v in q]


def convert(asf: Path, amc: Path, *, t0: float = 0.0, t1: float | None = None, fps: int = 30, src_fps: int = 120) -> dict:
    bones, children = parse_asf(asf)
    frames = parse_amc(amc)
    step = src_fps / fps
    a = int(t0 * src_fps)
    b = len(frames) if t1 is None else min(len(frames), int(t1 * src_fps))
    picks = [frames[int(round(a + i * step))] for i in range(int((b - a - 1) / step) + 1)]
    worlds = [world_rotations(bones, children, f) for f in picks]
    # Курс исполнителя: средний «вперёд» таза → разворот лицом в +Z.
    fwd = np.mean([w["root"] @ np.array([0.0, 0.0, 1.0]) for w, _ in worlds], axis=0)
    yaw = math.atan2(fwd[0], fwd[2])
    face = ry(-yaw)
    pos0 = worlds[0][1]
    clip: dict = {"fps": fps, "frames": len(worlds), "source": f"CMU {amc.stem}", "bones": {k: [] for k in MAP}, "root": []}
    for w, pos in worlds:
        world = {vb: face @ w[cb] for vb, cb in MAP.items() if cb in w}
        for vb in MAP:
            if vb not in world:
                continue
            parent = PARENT.get(vb)
            # Позвоночник — от вертикального таза: сидящий герой держит таз ровно, корпус сохраняет мировой наклон
            # исполнителя (иначе «сел на стул» = таз назад + корпус вперёд складывает героя пополам).
            if vb == "spine":
                parent = None
            while parent is not None and parent not in world:  # нет кости в скелете — к ближайшему предку
                parent = PARENT.get(parent)
            local = world[vb] if parent is None else world[parent].T @ world[vb]
            clip["bones"][vb].append(quat(local))
        d = face @ (pos - pos0)
        clip["root"].append([round(float(v), 4) for v in d])
    return clip


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="CMU ASF/AMC → клип VRM")
    ap.add_argument("asf", type=Path)
    ap.add_argument("amc", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--from", dest="t0", type=float, default=0.0)
    ap.add_argument("--to", dest="t1", type=float)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--name")
    a = ap.parse_args(argv)
    clip = convert(a.asf, a.amc, t0=a.t0, t1=a.t1, fps=a.fps)
    clip["name"] = a.name or a.amc.stem
    a.out.write_text(json.dumps(clip, separators=(",", ":")), "utf-8")
    print(f"{a.out}: {clip['frames']} кадров @{a.fps}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
