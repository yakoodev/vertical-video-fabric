"""Конвертер мокапа CMU → VRM: покой — единичные повороты, кватернионы нормированы, разворот лицом в +Z."""

import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import cmu_to_vrm as C  # noqa: E402

ASF = """:version 1.10
:units
  mass 1.0
  length 0.45
  angle deg
:root
   order TX TY TZ RX RY RZ
   axis XYZ
   position 0 0 0
   orientation 0 0 0
:bonedata
  begin
     id 1
     name lowerback
     direction 0 1 0
     length 2
     axis 0 0 0 XYZ
    dof rx ry rz
  end
  begin
     id 2
     name lhumerus
     direction 1 0 0
     length 5
     axis 180 -30 -90 XYZ
    dof rx ry rz
  end
:hierarchy
  begin
    root lowerback
    lowerback lhumerus
  end
"""


def _write(tmp: Path, frames: list[str]) -> tuple[Path, Path]:
    asf, amc = tmp / "t.asf", tmp / "t.amc"
    asf.write_text(ASF, "latin-1")
    body = [":FULLY-SPECIFIED", ":DEGREES"]
    for i, f in enumerate(frames, 1):
        body += [str(i), f]
    amc.write_text("\n".join(body), "latin-1")
    return asf, amc


def test_euler_order_is_x_then_y_then_z():
    m = C.euler_xyz(90, 0, 0)
    assert np.allclose(m @ [0, 1, 0], [0, 0, 1], atol=1e-9)
    m = C.euler_xyz(90, 0, 90)  # сначала X, потом Z
    assert np.allclose(m, C.rz(math.pi / 2) @ C.rx(math.pi / 2))


def test_rest_pose_gives_identity(tmp_path):
    frames = ["root 0 0 0 0 0 0\nlowerback 0 0 0\nlhumerus 0 0 0"] * 8
    clip = C.convert(*_write(tmp_path, frames), fps=30, src_fps=120)
    for q in clip["bones"]["spine"] + clip["bones"]["leftUpperArm"]:
        assert np.allclose(q, [0, 0, 0, 1], atol=1e-4)


def test_quaternions_normalized_and_yaw_faces_forward(tmp_path):
    frames = [f"root 0 0 0 0 90 0\nlowerback {i} 0 0\nlhumerus 0 0 {i * 2}" for i in range(16)]
    clip = C.convert(*_write(tmp_path, frames), fps=30, src_fps=120)
    for q in clip["bones"]["leftUpperArm"]:
        assert abs(sum(v * v for v in q) - 1) < 1e-3
    # Корень повёрнут на 90° по курсу — после разворота таз смотрит в +Z (поворот таза — единичный).
    assert np.allclose(clip["bones"]["hips"][0], [0, 0, 0, 1], atol=1e-3)
