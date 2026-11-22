"""Клипы для «Анимаций» в 3D-превью: мокап CMU (BVH) + наши движения из Blender → ``<runtime>/anim3d``.

    python tools/anim3d_export.py [--out D:/vvf-data/runtime/anim3d] [--bvh D:/vvf-offload/anime3d/anim_lib/cmu]
        [--blender D:/vvf-offload/tmp/preview3d/anim_out] [--only 91_14,83_34]

* CMU (BVH, перевод cgspeed): первый кадр каждого файла — Т-поза, она и есть поза отсчёта; дальше — перенос на
  скелет VRM тем же ``tools/anim_retarget.retarget``, что у библиотеки движений. Пальцев в CMU нет (один маркер) —
  кости пальцев не пишутся. Клип — 30 кадров/с, не длиннее ``MAX_SEC``.
* Наши движения (пилот «лестница», тесты покоя, ходьбы и барабанов) — JSON из ``tools/anim3d_blender_export.py``.

Рядом — ``index.json``: имя, русское название, группа, длительность, источник, лицензия. Клипы библиотеки движений
сервиса (``<series3d_vrm_dir>/anim``, Quaternius/KayKit) отдельно не копируются — app/anim3d.py берёт их на месте.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import anim_retarget as R  # noqa: E402

FPS = 30
MAX_SEC = 20.0
CMU_LICENSE = "CMU Graphics Lab Motion Capture Database: бесплатно для любого использования, указывать автора не нужно"
OWN_LICENSE = "свои движения проекта"

# Наши движения: имя файла → (название, группа, петля, перемещение по умолчанию, источник).
OWN = {
    "pilot_stairs": ("Поднимается по лестнице (пилот, одобрен)", "Лестница", False, "place",
                     "Пилот «Невезучего», лестница v5 (build_pilot_v5.py), герой"),
    "test1_idle": ("Стоит устало, дышит (наш тест)", "Покой", True, "place", "Тест анимации 1: покой со слоями жизни"),
    "test2_walk": ("Идёт понуро (наш тест)", "Ходьба", True, "place", "Тест анимации 2: ходьба «аниме» по CMU 91_14"),
    "test3_band": ("Играет на барабанах (наш тест)", "Музыка", True, "place", "Тест анимации 3: барабанщик по долям"),
}

CAT_RU = {"walk": "Ходьба", "walk_sad": "Ходьба", "walk_cool": "Ходьба", "stairs": "Лестница", "sit_stand": "Сидит",
          "idle": "Покой", "fall_getup": "Падает и встаёт", "hang": "Лазает", "jump": "Прыжки", "dance_music": "Музыка",
          "gesture": "Жесты и эмоции"}

# Русские названия клипов CMU (описания из index.csv).
CMU_RU = {
    "07_01": "Идёт", "08_01": "Идёт бодро", "91_29": "Идёт обычно", "137_29": "Идёт обычно (2)",
    "82_08": "Стоит, потом идёт вперёд", "91_13": "Идёт грустно", "91_14": "Идёт подавленно", "91_21": "Идёт, всё болит",
    "91_10": "Идёт медленно", "142_05": "Подавлен, бродит", "142_15": "Грустит, бродит", "82_10": "Идёт грустно вперёд",
    "111_14": "Плетётся", "113_10": "Слоняется", "141_27": "Плетётся (2)", "137_33": "Идёт как старик",
    "142_07": "Пожилой человек", "137_20": "Идёт как нескладный подросток", "104_13": "Идёт, спотыкается",
    "91_23": "Идёт круто", "142_04": "Круто, вразвалку", "142_13": "Расслабленно", "132_42": "Идёт, качая плечами",
    "83_27": "Шагает вверх по ступенькам", "83_34": "Идёт вверх по лестнице", "111_27": "Поднимается по лестнице",
    "113_19": "Вверх и вниз по лестнице", "114_07": "Вверх и вниз по лестнице (2)", "143_17": "Вверх по ступенькам и дальше",
    "13_01": "Сидит на высоком табурете, встаёт", "13_04": "Сидит, подпёр подбородок",
    "13_05": "Сидит, ёрзает, встаёт", "111_09": "Встаёт со стула", "113_15": "Садится на стул и встаёт",
    "114_05": "Сидит на стуле", "143_18": "Садится и встаёт", "143_19": "Садится на табурет и встаёт",
    "82_05": "Сидит на земле, отдыхает", "22_03": "Сидит, закрыл лицо руками", "140_06": "Стоит, ждёт",
    "140_07": "Стоит, ждёт (2)", "137_28": "Ждёт", "137_19": "Ждёт как нескладный подросток", "137_32": "Ждёт как старик",
    "141_20": "Ждёт", "111_28": "Стоит неподвижно", "113_21": "Стоит неподвижно (2)", "77_02": "Стоит",
    "139_02": "Переминается с ноги на ногу", "40_10": "Ждёт автобус", "90_16": "Падает лицом вниз",
    "90_18": "Падает, будто выдернули ковёр", "85_15": "Крутится и падает", "140_01": "Встаёт, лёжа лицом вниз",
    "140_08": "Встаёт, лёжа на спине", "139_16": "Встаёт с земли", "01_03": "Площадка: лезет, висит, качается",
    "01_09": "Площадка: висит на руках, спрыгивает, сидит", "01_11": "Площадка: висит, свешивается, спрыгивает",
    "43_02": "Висит на перекладине, раскачивается", "16_01": "Прыгает", "13_39": "Прыгает (2)",
    "91_39": "Прыгает вперёд", "82_02": "Спрыгивает с уступа", "111_05": "Танцует", "113_04": "Танцует (2)",
    "79_18": "Играет на барабанах", "80_13": "Барабанит", "79_17": "Играет на скрипке", "79_19": "Играет на пианино",
    "111_25": "Пожимает плечами", "141_21": "Пожимает плечами (2)", "111_37": "Машет рукой", "141_16": "Машет: привет",
    "18_08": "Объясняет, жестикулирует", "18_10": "Ругается, злые жесты", "111_32": "Потягивается и зевает",
    "143_30": "Потягивается и зевает (2)", "79_71": "Грустит", "79_68": "Мёрзнет", "79_36": "Отвечает на звонок",
}
# Первыми в списке (остальные — под «ещё»).
CMU_CURATED = {"91_14", "83_34", "79_18", "80_13", "111_25", "79_71", "18_10", "141_16", "13_04", "143_30", "139_02",
               "90_16"}
LOOPED = {"walk", "walk_sad", "walk_cool", "idle"}


# --------------------------------------------------------------------------- BVH


def _qaxis(axis: str, deg: np.ndarray) -> np.ndarray:
    h = np.radians(deg) / 2
    q = np.zeros((len(deg), 4))
    q[:, 3] = np.cos(h)
    q[:, "XYZ".index(axis)] = np.sin(h)
    return q


def read_bvh(path: Path) -> dict:
    """BVH → нейтральный скелет anim_retarget: узлы (имя, родитель, покой) и кадры (локальные TRS)."""
    text = path.read_text("utf-8", errors="replace")
    head, _, motion = text.partition("MOTION")
    tok = head.split()
    nodes: list[dict] = []
    stack: list[int] = []  # -1 — «End Site» (у него нет своей кости)
    chans: list[tuple[int, list[str]]] = []
    pending: int | None = None
    i = 0
    while i < len(tok):
        t = tok[i]
        if t in ("ROOT", "JOINT"):
            nodes.append({"name": tok[i + 1], "parent": next((s for s in reversed(stack) if s >= 0), -1),
                          "t": [0.0, 0.0, 0.0], "r": [0, 0, 0, 1.0]})
            pending = len(nodes) - 1
            i += 2
            continue
        if t == "End":
            pending = -1
            i += 2
            continue
        if t == "{":
            stack.append(pending if pending is not None else -1)
            pending = None
        elif t == "}":
            stack.pop()
        elif t == "OFFSET":
            if stack and stack[-1] >= 0:
                nodes[stack[-1]]["t"] = [float(x) for x in tok[i + 1: i + 4]]
            i += 4
            continue
        elif t == "CHANNELS":
            n = int(tok[i + 1])
            chans.append((stack[-1], tok[i + 2: i + 2 + n]))
            i += 2 + n
            continue
        i += 1
    lines = [ln for ln in motion.split("\n") if ln.strip()]
    frames = int(lines[0].split()[-1])
    ft = float(lines[1].split()[-1])
    data = np.array([[float(x) for x in ln.split()] for ln in lines[2: 2 + frames]])
    F, N = len(data), len(nodes)
    rot = np.tile(np.array([0, 0, 0, 1.0]), (F, N, 1))
    pos = np.tile(np.array([n["t"] for n in nodes], float), (F, 1, 1))
    col = 0
    for node, names in chans:
        q = np.tile(np.array([0, 0, 0, 1.0]), (F, 1))
        for name in names:
            v = data[:, col]
            col += 1
            if name.endswith("position"):
                pos[:, node, "XYZ".index(name[0])] = v
            else:
                q = R.qmul(q, _qaxis(name[0], v))
        rot[:, node] = q
    return {"nodes": nodes, "rot": rot, "pos": pos, "fps": 1.0 / ft}


def bvh_skeleton(bvh: dict, t0: float = 0.0, t1: float | None = None, fps: int = FPS) -> dict:
    """Кадр 0 — Т-поза (клип «T-Pose»), движение — с кадра 1, пересчёт на ``fps`` и обрезка до [t0, t1]."""
    src_fps = bvh["fps"]
    F = len(bvh["rot"])
    dur = (F - 2) / src_fps
    t1 = min(dur, t1 if t1 is not None else dur, t0 + MAX_SEC)
    ts = np.arange(t0, t1 + 1e-9, 1.0 / fps)
    src_t = np.arange(F - 1) / src_fps
    tracks = {}
    for n in range(len(bvh["nodes"])):
        r = R.sample(src_t, bvh["rot"][1:, n], ts, True, False)
        tr = {"r": r.tolist()}
        if n == 0:
            tr["t"] = R.sample(src_t, bvh["pos"][1:, n], ts, False, False).tolist()
        tracks[n] = tr
    ref = {n: {"r": [bvh["rot"][0, n].tolist()], **({"t": [bvh["pos"][0, n].tolist()]} if n == 0 else {})}
           for n in range(len(bvh["nodes"]))}
    return {"nodes": bvh["nodes"], "source": "CMU", "clips": {
        "T-Pose": {"fps": fps, "frames": 1, "tracks": ref},
        "motion": {"fps": fps, "frames": len(ts), "tracks": tracks}}}


FINGER = ("Thumb", "Index", "Middle", "Ring", "Little")


def cmu_clip(path: Path, name: str) -> dict:
    skel = bvh_skeleton(read_bvh(path))
    clip = R.retarget(skel, "motion", ref="T-Pose")
    clip["bones"] = {b: v for b, v in clip["bones"].items() if not any(f in b for f in FINGER)}
    clip["name"] = name
    clip["source"] = f"CMU {path.stem}"
    return clip


# --------------------------------------------------------------------------- компактная запись


def compact(clip: dict) -> dict:
    """4 знака, неподвижные кости — одним кадром (клиент держит такой кадр всё время)."""
    bones = {}
    for b, qs in clip["bones"].items():
        arr = np.array(qs, float)
        if len(arr) > 1 and np.abs(arr - arr[0]).max() < 2e-4:
            arr = arr[:1]
        bones[b] = [[round(float(v), 4) for v in q] for q in arr]
    root = np.array(clip.get("root") or [[0, 0, 0]], float)
    if len(root) > 1 and np.abs(root - root[0]).max() < 1e-4:
        root = root[:1]
    return {**clip, "bones": bones, "root": [[round(float(v), 4) for v in p] for p in root]}


def entry(name: str, clip: dict, *, ru: str, group: str, loop: bool, curated: bool, root: str, source: str,
          license_: str) -> dict:
    return {"name": name, "ru": ru, "group": group, "loop": loop, "curated": curated, "root": root,
            "frames": clip["frames"], "fps": clip["fps"], "duration": round((clip["frames"] - 1) / clip["fps"], 3),
            "file": f"{name}.json", "source": source, "license": license_}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default="D:/vvf-data/runtime/anim3d")
    ap.add_argument("--bvh", default="D:/vvf-offload/anime3d/anim_lib/cmu")
    ap.add_argument("--blender", default="D:/vvf-offload/tmp/preview3d/anim_out")
    ap.add_argument("--only", default="")
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    only = {x for x in args.only.split(",") if x}
    index: list[dict] = []
    idx_path = out / "index.json"
    if only and idx_path.is_file():
        index = [e for e in json.loads(idx_path.read_text("utf-8")).get("clips", []) if e["name"] not in only]
    for name, (ru, group, loop, root, source) in OWN.items():
        if only and name not in only:
            continue
        src = Path(args.blender) / f"{name}.json"
        if not src.is_file():
            print("нет", src)
            continue
        clip = compact(json.loads(src.read_text("utf-8")))
        (out / f"{name}.json").write_text(json.dumps(clip, separators=(",", ":")), "utf-8")
        index.append(entry(name, clip, ru=ru, group=group, loop=loop, curated=True, root=root, source=source,
                           license_=OWN_LICENSE))
        print("свой", name, clip["frames"])
    bvh_dir = Path(args.bvh)
    rows = list(csv.DictReader((bvh_dir / "index.csv").open(encoding="utf-8"))) if (bvh_dir / "index.csv").is_file() else []
    for row in rows:
        cid = row["clip_id"]
        name = f"cmu_{cid}"
        if only and cid not in only and name not in only:
            continue
        path = bvh_dir / row["file"]
        try:
            clip = compact(cmu_clip(path, name))
        except Exception as exc:  # noqa: BLE001 — один битый файл не останавливает остальные
            print("ошибка", cid, exc)
            continue
        (out / f"{name}.json").write_text(json.dumps(clip, separators=(",", ":")), "utf-8")
        cat = row["category"]
        index.append(entry(name, clip, ru=CMU_RU.get(cid, row["description"]), group=CAT_RU.get(cat, "Разное"),
                           loop=cat in LOOPED, curated=cid in CMU_CURATED,
                           root="place",
                           source=f"CMU {cid} «{row['description']}»", license_=CMU_LICENSE))
        print("cmu", cid, clip["frames"], round(math.fsum(len(json.dumps(v)) for v in clip["bones"].values()) / 1024), "KB")
    idx_path.write_text(json.dumps({"version": 1, "clips": index}, ensure_ascii=False, indent=1), "utf-8")
    print("index", len(index), "→", idx_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
