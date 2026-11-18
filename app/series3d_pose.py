"""Поток поз 3D-серии: «Blender ставит позу, three.js рисует» (``render_backend: threejs``).

Blender (``tools/blender/series_export.py``) собирает сцену серии как для рендера, но кадры не рисует, а выгружает:

* ``scene.glb`` — вся сцена один раз: локации, реквизит, герой со скелетом в позе покоя (skin), телефон, тексты.
  У каждого узла в extras ``vvf_obj`` — номер в ``header.objects``, у материала ``vvf_mat`` — номер в
  ``header.materials``;
* ``scene.json`` — заголовок (этот модуль: ``FORMAT``, раскладка записи ``layout``): объекты, кости, источники,
  объёмы взвеси, таблица материалов (то, что glTF теряет: шероховатость из узлов, шлифовка, рельеф, свечение);
* ``frames.bin`` — записи кадров подряд, float32, фиксированная длина ``layout.stride``: запись дописывается сразу
  после кадра (поток: читатель может идти следом), кадр ``i`` — по смещению ``i · stride · 4``.

Запись кадра (все матрицы — аффинные 3×4 по столбцам, уже y-вверх, как в glTF/three.js):

=========== ======================================================================================
frame       номер кадра серии (с 1)
camera      матрица камеры ``C·M`` (локально: смотрит в −Z, вверх +Y), lens мм, sensor мм, фокус м, f-число,
            экспозиция (ступени), точка лица героя (3)
objects     по подвижному объекту: мировая матрица узла ``C·M·C⁻¹``
bones       по кости: деформация «покой в мире → поза в мире» ``C·D·C⁻¹``, D = A(f)·pose·rest⁻¹·A0⁻¹
materials   сила свечения анимированных материалов
lights      по источнику: мощность (Вт Blender) + матрица ``C·M`` (позиция — столбец 3, луч — −столбец 2)
fogs        плотность каждого объёма взвеси
=========== ======================================================================================

Чистый Python без ``bpy``: раскладку считают и Blender, и рендер (через заголовок), и тесты.
"""
from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Iterable, Sequence

FORMAT = "vvf-pose/1"
CAMERA_FIELDS = 12 + 6 + 3          # матрица, lens/sensor/focus/fstop/exposure/резерв, лицо
M34 = 12
LIGHT_FIELDS = 1 + M34


def layout(n_objects: int, n_bones: int, n_mats: int, n_lights: int, n_fogs: int) -> dict:
    """Смещения полей записи кадра (в float32) и длина записи."""
    off = {"frame": 0, "camera": 1}
    cur = 1 + CAMERA_FIELDS
    off["objects"] = cur
    cur += n_objects * M34
    off["bones"] = cur
    cur += n_bones * M34
    off["materials"] = cur
    cur += n_mats
    off["lights"] = cur
    cur += n_lights * LIGHT_FIELDS
    off["fogs"] = cur
    cur += n_fogs
    return {**off, "stride": cur, "counts": {"objects": n_objects, "bones": n_bones, "materials": n_mats,
                                             "lights": n_lights, "fogs": n_fogs}}


def header_layout(header: dict) -> dict:
    return layout(len([o for o in header["objects"] if o.get("dyn")]), len(header["bones"]),
                  len([m for m in header["materials"] if m.get("anim")]), len(header["lights"]), len(header["fogs"]))


def pack_record(lay: dict, frame: int, camera: Sequence[float], objects: Iterable[Sequence[float]],
                bones: Iterable[Sequence[float]], materials: Sequence[float], lights: Iterable[Sequence[float]],
                fogs: Sequence[float]) -> bytes:
    """Одна запись кадра → байты (float32, little-endian). Длины проверяются по раскладке."""
    vals: list[float] = [float(frame)]
    if len(camera) != CAMERA_FIELDS:
        raise ValueError(f"камера: {len(camera)} чисел, нужно {CAMERA_FIELDS}")
    vals += [float(x) for x in camera]

    def rows(name: str, seq: Iterable[Sequence[float]], width: int) -> None:
        n = 0
        for item in seq:
            if len(item) != width:
                raise ValueError(f"{name}: запись длины {len(item)}, нужно {width}")
            vals.extend(float(x) for x in item)
            n += 1
        if n != lay["counts"][name]:
            raise ValueError(f"{name}: {n}, в раскладке {lay['counts'][name]}")

    if len(materials) != lay["counts"]["materials"] or len(fogs) != lay["counts"]["fogs"]:
        raise ValueError("materials/fogs: длина не по раскладке")
    rows("objects", objects, M34)       # порядок полей — как в layout()
    rows("bones", bones, M34)
    vals.extend(float(x) for x in materials)
    rows("lights", lights, LIGHT_FIELDS)
    vals.extend(float(x) for x in fogs)
    if len(vals) != lay["stride"]:
        raise ValueError(f"запись {len(vals)} чисел, stride {lay['stride']}")
    return struct.pack(f"<{len(vals)}f", *vals)


def read_header(folder: Path | str) -> dict:
    h = json.loads((Path(folder) / "scene.json").read_text("utf-8"))
    if h.get("format") != FORMAT:
        raise ValueError(f"не тот формат позы: {h.get('format')} (нужен {FORMAT})")
    return h


def record_count(folder: Path | str, header: dict) -> int:
    """Сколько записей уже дописано (неполная последняя не считается)."""
    p = Path(folder) / "frames.bin"
    return p.stat().st_size // (header["layout"]["stride"] * 4) if p.is_file() else 0


def read_record(folder: Path | str, header: dict, i: int) -> dict:
    """Запись ``i`` (по порядку в файле) → словарь полей (для проверок и тестов)."""
    lay = header["layout"]
    st = lay["stride"]
    with (Path(folder) / "frames.bin").open("rb") as fh:
        fh.seek(i * st * 4)
        raw = fh.read(st * 4)
    if len(raw) < st * 4:
        raise IndexError(i)
    v = struct.unpack(f"<{st}f", raw)
    c = lay["counts"]

    def chunk(key: str, n: int, w: int) -> list[list[float]]:
        o = lay[key]
        return [list(v[o + k * w:o + (k + 1) * w]) for k in range(n)]

    cam = v[lay["camera"]:lay["camera"] + CAMERA_FIELDS]
    return {"frame": int(round(v[0])), "camera": {"matrix": list(cam[:12]), "lens": cam[12], "sensor": cam[13], "focus": cam[14],
                                                  "fstop": cam[15], "exposure": cam[16], "face": list(cam[18:21])},
            "objects": chunk("objects", c["objects"], M34), "bones": chunk("bones", c["bones"], M34),
            "materials": list(v[lay["materials"]:lay["materials"] + c["materials"]]),
            "lights": [{"energy": x[0], "matrix": x[1:]} for x in chunk("lights", c["lights"], LIGHT_FIELDS)],
            "fogs": list(v[lay["fogs"]:lay["fogs"] + c["fogs"]])}


def frame_index(header: dict) -> dict[int, int]:
    """Номер кадра серии → номер записи в ``frames.bin``."""
    return {int(f): i for i, f in enumerate(header.get("frames") or [])}


def export_frames(keys: list[dict], todo: dict[str, list[int]]) -> list[int]:
    """Какие кадры выгрузить: куски с недостающими кадрами — целиком от первого недостающего минус один (размытию
    движения нужен прошлый кадр) до последнего недостающего, по порядку."""
    out: set[int] = set()
    for c in keys:
        need = todo.get(c["key"])
        if not need:
            continue
        a = max(c["f0"], min(need) - 1)
        out.update(range(a, max(need) + 1))
    return sorted(out)


# Свет: какие три источника с тенью берут тени в плане (в WebGL ~16 текстур на материал — больше трёх теней молча
# пропадают). Важность — сумма по кадрам мощности, делённой на квадрат расстояния до лица героя.
MAX_SHADOWS = 3


def shadow_pick(lights: list[dict], energy: list[list[float]], dist: list[list[float]], limit: int = MAX_SHADOWS) -> list[int]:
    """``lights`` — статика источников (shadow, type), ``energy[k][i]``/``dist[k][i]`` — по кадрам плана для источника i.
    → номера источников с тенью (до ``limit``), самые важные первыми."""
    score = []
    for i, li in enumerate(lights):
        if not li.get("shadow"):
            continue
        s = sum(e[i] / max(0.09, d[i] ** 2) for e, d in zip(energy, dist))
        if s > 1e-6:
            score.append((s, i))
    score.sort(reverse=True)
    return [i for _s, i in score[:limit]]
