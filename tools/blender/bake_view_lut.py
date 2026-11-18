"""Цветовое преобразование вида Blender (AgX + look) → 3D-LUT для three.js (node/scene3d/src/viewlut.ts).

    blender -b --factory-startup -P tools/blender/bake_view_lut.py -- --out D:/.../agx_mhc.bin \
        [--look "AgX - Medium High Contrast"] [--size 48]

Вход LUT — линейный Rec.709 сцены, закодированный «шейпером» AgX: s = (log2(x) + 12.47393) / 16.5 (x = 0.18·2^(16.5·s − 10)),
то есть тот же лог-диапазон, что у AgX (−10…+6.5 стоп от 18% серого). Выход — значения дисплея sRGB (0..1), как
сохраняет кадр Blender. Файл: float32 [size³ × 3], индекс r быстрее всего; рядом .json с размером и шейпером.
Делает PyOpenColorIO, встроенный в Blender, по его же config.ocio — совпадение с рендером серии точное.
Из сцены серии (``series_export.py``) — функцией ``bake``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import bpy  # noqa: F401  (запуск только внутри Blender)

MIN_EV, RANGE = -12.47393, 16.5


def bake(out: Path, look: str = "AgX - Medium High Contrast", size: int = 48, view: str = "AgX") -> dict:
    import numpy as np
    import PyOpenColorIO as OCIO

    cfg_path = Path(bpy.utils.resource_path("LOCAL")) / "datafiles" / "colormanagement" / "config.ocio"
    cfg = OCIO.Config.CreateFromFile(str(cfg_path))
    tr = OCIO.DisplayViewTransform()
    tr.setSrc("Linear Rec.709")
    tr.setDisplay("sRGB")
    tr.setView(view)
    grp = OCIO.GroupTransform()
    if look and look != "None":
        lk = OCIO.LookTransform()
        lk.setSrc("Linear Rec.709")
        lk.setDst("Linear Rec.709")
        lk.setLooks(look)
        grp.appendTransform(lk)
    grp.appendTransform(tr)
    proc = cfg.getProcessor(grp).getDefaultCPUProcessor()
    s = np.linspace(0.0, 1.0, size, dtype=np.float32)
    b, g, r = np.meshgrid(s, s, s, indexing="ij")
    enc = np.stack([r, g, b], axis=-1).reshape(-1, 3)
    lin = (2.0 ** (enc * RANGE + MIN_EV)).astype(np.float32)
    lin[enc <= 0.0] = 0.0
    img = np.ascontiguousarray(lin.copy())
    proc.applyRGB(img)
    img = np.clip(img, 0.0, 1.0).astype(np.float32)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.tofile(str(out))
    mid = np.array([[0.18, 0.18, 0.18], [1.0, 1.0, 1.0], [0.01, 0.01, 0.01], [1.0, 0.07, 0.04]], dtype=np.float32)
    proc.applyRGB(mid)
    meta = {"size": size, "min_ev": MIN_EV, "range": RANGE, "look": look, "view": view, "display": "sRGB",
            "check": {"0.18": mid[0].tolist(), "1.0": mid[1].tolist(), "0.01": mid[2].tolist(), "red": mid[3].tolist()}}
    out.with_suffix(".json").write_text(json.dumps(meta, indent=1), "utf-8")
    return meta


argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
if "--out" in argv and __name__ == "__main__":
    def arg(name, default=None):
        return argv[argv.index(name) + 1] if name in argv else default

    meta = bake(Path(arg("--out")), arg("--look", "AgX - Medium High Contrast"), int(arg("--size", "48")))
    print("LUT", arg("--out"), json.dumps(meta))
