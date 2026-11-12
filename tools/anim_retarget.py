"""Любой гуманоидный клип (glTF/GLB, FBX через node/vrm/fbx_dump.mjs) → клип VRM-героя (node/vrm, формат CMU).

    python tools/anim_retarget.py SRC.glb OUT_DIR [--clip Idle_Loop] [--fps 30] [--list]

Выход — тот же формат, что у tools/cmu_to_vrm.py: ``{name, fps, frames, bones: {vrmBone: [[x,y,z,w]...]}, root: [[x,y,z]...]}``
— локальные повороты нормализованных костей VRM в осях модели (родитель — ближайший предок по иерархии VRM, у
которого есть дорожка), плюс ``legLength`` (длина ноги исполнителя, м) — node/vrm масштабирует перемещение таза
под ноги своей модели, и ``spine: "hips"`` (позвоночник — от таза, у CMU — от вертикали).

Как переносится (без исполнения чего-либо из файлов — только чтение JSON и буферов):

1. Скелет источника → кости VRM по именам (UE-манекен Quaternius, KayKit Rig_Medium, Mixamo, VRoid/VRM, Blender
   «.L/.R») и по цепочке: всё между тазом и шеей — spine / chest / upperChest.
2. Поза отсчёта: клип «T-Pose»/«A_TPose», если есть, иначе поза покоя узлов. Мировые повороты считаются прямой
   кинематикой от корня сцены (оси сцены glTF учитываются), затем переводятся в оси VRM: «вверх» — от таза к голове,
   «влево» — от правого плеча к левому, «вперёд» — их произведение.
3. Поправка позы покоя: для кости b поворот A_b переводит её направление в Т-позе VRM (рука — по ±X, нога — вниз,
   стопа — вперёд-вниз) в её направление в позе отсчёта источника. Тогда мировой поворот VRM
   W_b(t) = R_b(t) · R_b(ref)⁻¹ · A_b: в позе отсчёта кость VRM лежит как у источника (А-поза так А-поза), а дальше
   поворачивается так же, как кость источника.
4. Перемещение таза — относительно позы отсчёта, в метрах источника; legLength — для масштаба под модель.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import struct
from pathlib import Path

import numpy as np

VRM_PARENT = {
    "spine": "hips", "chest": "spine", "upperChest": "chest", "neck": "upperChest", "head": "neck",
    "leftUpperLeg": "hips", "leftLowerLeg": "leftUpperLeg", "leftFoot": "leftLowerLeg", "leftToes": "leftFoot",
    "rightUpperLeg": "hips", "rightLowerLeg": "rightUpperLeg", "rightFoot": "rightLowerLeg", "rightToes": "rightFoot",
}
for _s in ("left", "right"):
    VRM_PARENT.update({f"{_s}Shoulder": "upperChest", f"{_s}UpperArm": f"{_s}Shoulder", f"{_s}LowerArm": f"{_s}UpperArm",
                       f"{_s}Hand": f"{_s}LowerArm"})
    for _f in ("Index", "Middle", "Ring", "Little"):
        VRM_PARENT.update({f"{_s}{_f}Proximal": f"{_s}Hand", f"{_s}{_f}Intermediate": f"{_s}{_f}Proximal",
                           f"{_s}{_f}Distal": f"{_s}{_f}Intermediate"})
    VRM_PARENT.update({f"{_s}ThumbMetacarpal": f"{_s}Hand", f"{_s}ThumbProximal": f"{_s}ThumbMetacarpal",
                       f"{_s}ThumbDistal": f"{_s}ThumbProximal"})

# Имена (без стороны и разделителей) → кость VRM без стороны. Порядок — приоритет.
BASE_NAMES: dict[str, tuple[str, ...]] = {
    "hips": ("hips", "pelvis", "hip", "pevis"),
    "head": ("head",),
    "neck": ("neck", "neck01", "neck1"),
    "Shoulder": ("clavicle", "shoulder", "collar", "collarbone"),
    "Hand": ("wrist", "hand"),
    "Foot": ("foot", "ankle"),
    "Toes": ("ball", "toebase", "toes", "toe"),
}
FINGER_RE = re.compile(r"(thumb|index|middle|ring|pinky|little)\D*?0?([1-3])$")
FINGER_VRM = {"thumb": "Thumb", "index": "Index", "middle": "Middle", "ring": "Ring", "pinky": "Little", "little": "Little"}
THUMB_SEG = {1: "Metacarpal", 2: "Proximal", 3: "Distal"}
FINGER_SEG = {1: "Proximal", 2: "Intermediate", 3: "Distal"}
PREFIX_RE = re.compile(r"^(?:.*[:|])?(?:mixamorig\d*_?|j_bip_|j_|def[-_]|bip0?1_?|cc_base_|armature_?)", re.I)
TPOSE_RE = re.compile(r"^(?:t[-_ ]?pose|a[-_ ]?t?pose|tpose|bind[-_ ]?pose)$", re.I)
KEYWORDS = re.compile(r"hips|pelvis|spine|head|neck|hand|foot|thigh|arm|leg|knee|shoulder|clavicle", re.I)


def split_name(raw: str, prefix: str = "") -> tuple[str, str]:
    """Имя кости → (сторона 'left'/'right'/'', основа без разделителей). prefix — общий префикс скелета (пространство
    имён экспорта: «SapphiArt_19Hips»)."""
    s = raw.strip()
    if prefix and s.startswith(prefix):
        s = s[len(prefix):]
    s = PREFIX_RE.sub("", s)
    side = ""
    # Сторона, приклеенная заглавной буквой в конце: «ThighR», «UpperArmL», «f_index01R».
    m = re.search(r"(?<=[a-z0-9])([LR])(?:_end)?$", s)
    if m:
        side = "left" if m.group(1) == "L" else "right"
        s = s[:m.start()] + s[m.end(1):]
    toks = [t for t in re.split(r"[._\s\-:]+", s.lower()) if t]
    rest = []
    for t in toks:
        if t in ("l", "left", "lft") and not side:
            side = "left"
        elif t in ("r", "right", "rgt") and not side:
            side = "right"
        else:
            rest.append(t)
    base = "".join(rest)
    if not side:
        for word in ("left", "right"):
            if base.startswith(word):
                side, base = word, base[len(word):]
                break
        else:
            m2 = re.match(r"^([lr])(shldr|thigh|shin|foot|hand|forearm|uparm|collar|toe)", base)  # DAZ: lShldr, rThigh
            if m2:
                side, base = ("left" if m2.group(1) == "l" else "right"), base[1:]
    return side, base


def _common_prefix(names: list[str]) -> str:
    """Общий префикс имён костей (только если он есть у всех «костных» имён и кончается перед заглавной/цифрой)."""
    keyed = [n for n in names if KEYWORDS.search(n)]
    if len(keyed) < 4:
        return ""
    p = keyed[0]
    for n in keyed[1:]:
        while not n.startswith(p):
            p = p[:-1]
    m = re.match(r"^(.*[_:|]\d*)(?=[A-Za-z]|$)", p) if p else None
    return m.group(1) if m and len(m.group(1)) >= 3 and "mixamorig" not in p.lower() else ""


def _ancestors(i: int, parents: list[int]) -> list[int]:
    out = []
    while i is not None and i >= 0:
        out.append(i)
        i = parents[i]
    return out


def _lca(nodes: list[int], parents: list[int]) -> int:
    common = None
    for n in nodes:
        a = _ancestors(n, parents)
        common = a if common is None else [x for x in common if x in set(a)]
    return common[0] if common else -1


def map_bones(names: list[str], parents: list[int]) -> dict[str, int]:
    """Имена узлов скелета → {кость VRM: индекс узла}.

    Сначала по именам — стопы, кисти, голова; остальное — по строению: таз — общий предок обеих стоп и головы,
    голень и бедро — родители стопы, предплечье и плечо — родители кисти, ключица — родитель плеча со своей
    стороной, позвоночник — цепочка от таза к шее. Так проходят и Mixamo («LeftLeg» — голень), и UE («calf»), и
    скелеты с опечатками («LoweArmL», «pevis»), и дубли узлов-опор экспорта FBX («Knee_L» трижды подряд).
    """
    prefix = _common_prefix(names)
    info = [split_name(n, prefix) for n in names]
    bad = [("leaf" in n.lower() or n.lower().endswith("_end") or "end" == b[-3:] or "twist" in b or "roll" in b)
           for n, (_s, b) in zip(names, info)]
    sub = [0] * len(names)
    for i in range(len(names)):
        for a in _ancestors(parents[i], parents):
            sub[a] += 1

    def cands(key: str, side: str = "") -> list[int]:
        return [i for i, (sd, b) in enumerate(info) if sd == side and b in BASE_NAMES[key] and not bad[i]]

    def up(i: int) -> int:
        """Родитель, минуя дубли с тем же именем (опоры экспорта)."""
        p = parents[i]
        while p >= 0 and info[p][1] == info[i][1] and info[p][0] == info[i][0]:
            p = parents[p]
        return p

    def outer(i: int) -> int:
        """Из цепочки одноимённых узлов — самый внешний (у него и дети-кости)."""
        while parents[i] >= 0 and info[parents[i]] == info[i]:
            i = parents[i]
        return i

    out: dict[str, int] = {}
    feet = {s: sorted({outer(i) for i in cands("Foot", s)}, key=lambda i: -sub[i]) for s in ("left", "right")}
    if not feet["left"] or not feet["right"]:
        raise ValueError("не нашёл стопы (foot/ankle) в скелете: " + ", ".join(names[:40]))
    lf, rf = feet["left"][0], feet["right"][0]
    heads = cands("head")
    if not heads:
        raise ValueError("не нашёл голову в скелете: " + ", ".join(names[:40]))
    # Голова — та, у которой общий предок со стопами глубже всех (не «Head» группы мешей).
    head = max(heads, key=lambda h: (len(_ancestors(_lca([h, lf, rf], parents), parents)), -len(_ancestors(h, parents))))
    hips = _lca([head, lf, rf], parents)
    if hips < 0:
        raise ValueError("голова и стопы в разных деревьях")
    out["hips"], out["head"] = hips, head
    for s in ("left", "right"):
        f = feet[s][0]
        out[f"{s}Foot"] = f
        lo = up(f)
        if lo >= 0 and lo != hips:
            out[f"{s}LowerLeg"] = lo
            hi = up(lo)
            if hi >= 0 and hi != hips:
                out[f"{s}UpperLeg"] = hi
        toes = [c for c in range(len(names)) if parents[c] == f and not bad[c]]
        named = [c for c in toes if info[c][1] in BASE_NAMES["Toes"]]
        if named or toes:
            out[f"{s}Toes"] = (named or toes)[0]
        # Кисть: ближайшая к корню из «hand/wrist» своей стороны под тазом (KayKit: wrist → hand).
        hs = [i for i in cands("Hand", s) if hips in _ancestors(i, parents)]
        if hs:
            h = min((outer(i) for i in hs), key=lambda i: len(_ancestors(i, parents)))
            out[f"{s}Hand"] = h
            lo = up(h)
            if lo >= 0:
                out[f"{s}LowerArm"] = lo
                ua = up(lo)
                if ua >= 0:
                    out[f"{s}UpperArm"] = ua
                    sh = up(ua)
                    if sh >= 0 and info[sh][0] == s:
                        out[f"{s}Shoulder"] = sh
        for i, (sd, b) in enumerate(info):
            m = FINGER_RE.search(b)
            if sd == s and m and not bad[i] and f"{s}Hand" in out and out[f"{s}Hand"] in _ancestors(i, parents):
                fi, n = m.group(1), int(m.group(2))
                seg = THUMB_SEG[n] if fi == "thumb" else FINGER_SEG[n]
                out.setdefault(f"{s}{FINGER_VRM[fi]}{seg}", outer(i))
    # Позвоночник: узлы от таза к голове, без них самих; шея — по имени или последний в длинной цепочке.
    chain = []
    i = up(head)
    while i >= 0 and i != hips:
        chain.append(i)
        i = up(i)
    chain.reverse()
    necks = [c for c in chain if "neck" in info[c][1]]
    if necks:
        out["neck"] = necks[0]
        chain = chain[:chain.index(necks[0])]
    elif len(chain) >= 4:
        out["neck"] = chain.pop()
    if len(chain) == 1:
        out["spine"] = chain[0]
    elif len(chain) == 2:
        out["spine"], out["chest"] = chain
    elif len(chain) >= 3:
        out["spine"], out["chest"], out["upperChest"] = chain[0], chain[len(chain) // 2], chain[-1]
    return out


# --------------------------------------------------------------------------- кватернионы (x, y, z, w)


def qmul(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    ax, ay, az, aw = np.moveaxis(a, -1, 0)
    bx, by, bz, bw = np.moveaxis(b, -1, 0)
    return np.stack([aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx,
                     aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz], -1)


def qinv(q: np.ndarray) -> np.ndarray:
    return q * np.array([-1.0, -1.0, -1.0, 1.0])


def qrot(q: np.ndarray, v: np.ndarray) -> np.ndarray:
    u = q[..., :3]
    w = q[..., 3:4]
    t = 2.0 * np.cross(u, v)
    return v + w * t + np.cross(u, t)


def qnorm(q: np.ndarray) -> np.ndarray:
    return q / np.linalg.norm(q, axis=-1, keepdims=True)


def qslerp(a: np.ndarray, b: np.ndarray, k: np.ndarray) -> np.ndarray:
    d = np.sum(a * b, -1, keepdims=True)
    b = np.where(d < 0, -b, b)
    return qnorm(a + (b - a) * k[..., None])  # nlerp: кадры частые, разница с slerp < 0.1°


def qfrom_mat(m: np.ndarray) -> np.ndarray:
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
    return qnorm(np.array(q))


def qarc(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Кратчайший поворот единичного a в единичный b."""
    a = a / np.linalg.norm(a)
    b = b / np.linalg.norm(b)
    d = float(np.dot(a, b))
    if d < -0.999999:
        ax = np.cross(a, [1.0, 0, 0])
        if np.linalg.norm(ax) < 1e-6:
            ax = np.cross(a, [0, 1.0, 0])
        ax /= np.linalg.norm(ax)
        return np.array([*ax, 0.0])
    c = np.cross(a, b)
    return qnorm(np.array([c[0], c[1], c[2], 1.0 + d]))


# --------------------------------------------------------------------------- чтение glTF / GLB

_COMP = {5126: ("<f", 4), 5123: ("<H", 2), 5121: ("<B", 1), 5122: ("<h", 2), 5120: ("<b", 1), 5125: ("<I", 4)}
_NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}
_NORM = {5121: 255.0, 5123: 65535.0, 5120: 127.0, 5122: 32767.0}


def read_gltf(path: Path) -> tuple[dict, list[bytes]]:
    data = path.read_bytes()
    if data[:4] == b"glTF":
        _magic, _ver, _total = struct.unpack_from("<III", data, 0)
        off, doc, bins = 12, None, []
        while off < len(data):
            ln, typ = struct.unpack_from("<II", data, off)
            chunk = data[off + 8: off + 8 + ln]
            if typ == 0x4E4F534A:
                doc = json.loads(chunk)
            elif typ == 0x004E4942:
                bins.append(chunk)
            off += 8 + ln
        buffers = [bins[0] if not b.get("uri") else (path.parent / b["uri"]).read_bytes() for b in doc.get("buffers", [])]
        return doc, buffers
    doc = json.loads(data)
    buffers = []
    for b in doc.get("buffers", []):
        uri = b.get("uri", "")
        if uri.startswith("data:"):
            import base64
            buffers.append(base64.b64decode(uri.split(",", 1)[1]))
        else:
            buffers.append((path.parent / uri).read_bytes())
    return doc, buffers


def accessor(doc: dict, buffers: list[bytes], idx: int) -> np.ndarray:
    a = doc["accessors"][idx]
    fmt, size = _COMP[a["componentType"]]
    n = _NCOMP[a["type"]]
    count = a["count"]
    if "bufferView" not in a:
        return np.zeros((count, n))
    bv = doc["bufferViews"][a["bufferView"]]
    buf = buffers[bv.get("buffer", 0)]
    start = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
    stride = bv.get("byteStride") or size * n
    dt = np.dtype(fmt)
    if stride == size * n:
        arr = np.frombuffer(buf, dtype=dt, count=count * n, offset=start).reshape(count, n)
    else:
        arr = np.stack([np.frombuffer(buf, dtype=dt, count=n, offset=start + i * stride) for i in range(count)])
    arr = arr.astype(np.float64)
    if a.get("normalized") or (a["componentType"] != 5126 and a["type"] == "VEC4"):
        arr = np.maximum(arr / _NORM.get(a["componentType"], 1.0), -1.0)
    return arr


def skeleton_from_gltf(path: Path, fps: int = 30) -> dict:
    """glTF → нейтральный вид: узлы (имя, родитель, покой TRS) и клипы (локальные TRS узлов по кадрам)."""
    doc, buffers = read_gltf(path)
    nodes = doc.get("nodes", [])
    parent = [-1] * len(nodes)
    for i, n in enumerate(nodes):
        for c in n.get("children", []):
            parent[c] = i
    out_nodes = []
    for i, n in enumerate(nodes):
        if "matrix" in n:
            m = np.array(n["matrix"]).reshape(4, 4).T
            t = m[:3, 3]
            r = qfrom_mat(m[:3, :3] / np.linalg.norm(m[:3, :3], axis=0))
        else:
            t = np.array(n.get("translation", [0, 0, 0]), float)
            r = np.array(n.get("rotation", [0, 0, 0, 1]), float)
        out_nodes.append({"name": n.get("name") or f"node{i}", "parent": parent[i], "t": t.tolist(), "r": r.tolist()})
    clips = {}
    for k, an in enumerate(doc.get("animations", [])):
        name = an.get("name") or f"clip{k}"
        chans = []
        dur = 0.0
        for ch in an["channels"]:
            tgt = ch["target"]
            if "node" not in tgt or tgt["path"] not in ("rotation", "translation"):
                continue
            s = an["samplers"][ch["sampler"]]
            times = accessor(doc, buffers, s["input"])[:, 0]
            vals = accessor(doc, buffers, s["output"])
            if s.get("interpolation") == "CUBICSPLINE":
                vals = vals.reshape(len(times), 3, -1)[:, 1]
            dur = max(dur, float(times[-1]) if len(times) else 0.0)
            chans.append((tgt["node"], tgt["path"], times, vals, s.get("interpolation", "LINEAR")))
        frames = max(1, int(round(dur * fps)) + 1)
        ts = np.arange(frames) / fps
        tracks: dict[int, dict] = {}
        for node, kind, times, vals, interp in chans:
            tracks.setdefault(node, {})[kind[0]] = sample(times, vals, ts, kind == "rotation", interp == "STEP").tolist()
        clips[name] = {"fps": fps, "frames": frames, "tracks": tracks}
    return {"nodes": out_nodes, "clips": clips, "source": path.name}


def sample(times: np.ndarray, vals: np.ndarray, ts: np.ndarray, rot: bool, step: bool) -> np.ndarray:
    if len(times) == 1:
        return np.repeat(vals[:1], len(ts), 0)
    i = np.clip(np.searchsorted(times, ts, side="right") - 1, 0, len(times) - 2)
    span = np.maximum(times[i + 1] - times[i], 1e-9)
    k = np.clip((ts - times[i]) / span, 0.0, 1.0)
    if step:
        k = np.where(k >= 1.0, 1.0, 0.0)
    a, b = vals[i], vals[i + 1]
    if rot:
        return qslerp(qnorm(a), qnorm(b), k)
    return a + (b - a) * k[:, None]


# --------------------------------------------------------------------------- перенос

def _fk(nodes: list[dict], local_r: np.ndarray, local_t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Мировые повороты и положения узлов по кадрам: local_r (F, N, 4), local_t (F, N, 3)."""
    F, N = local_r.shape[:2]
    wr = np.zeros((F, N, 4))
    wp = np.zeros((F, N, 3))
    order = sorted(range(N), key=lambda i: _depth(nodes, i))
    for i in order:
        p = nodes[i]["parent"]
        if p < 0:
            wr[:, i], wp[:, i] = local_r[:, i], local_t[:, i]
        else:
            wr[:, i] = qmul(wr[:, p], local_r[:, i])
            wp[:, i] = wp[:, p] + qrot(wr[:, p], local_t[:, i])
    return wr, wp


def _depth(nodes: list[dict], i: int) -> int:
    d = 0
    while nodes[i]["parent"] >= 0:
        i = nodes[i]["parent"]
        d += 1
    return d


def _locals(skel: dict, clip: dict | None) -> tuple[np.ndarray, np.ndarray]:
    nodes = skel["nodes"]
    F = clip["frames"] if clip else 1
    r = np.tile(np.array([n["r"] for n in nodes], float), (F, 1, 1))
    t = np.tile(np.array([n["t"] for n in nodes], float), (F, 1, 1))
    for key, tr in (clip or {}).get("tracks", {}).items():
        i = int(key)
        if "r" in tr:
            r[:, i] = qnorm(np.array(tr["r"], float))
        if "t" in tr:
            t[:, i] = np.array(tr["t"], float)
    return r, t


STRUCT = {"hips", "spine", "chest", "upperChest", "neck", "head"}


# Направления костей в Т-позе нормализованного скелета VRM (лицом +Z, левая рука +X).
def vrm_dir(bone: str) -> np.ndarray:
    s = 1.0 if bone.startswith("left") else -1.0
    if bone in ("hips", "spine", "chest", "upperChest", "neck", "head"):
        return np.array([0, 1.0, 0])
    if bone.endswith(("UpperLeg", "LowerLeg")):
        return np.array([0, -1.0, 0])
    if bone.endswith("Foot"):
        return np.array([0, -0.55, 0.835])
    if bone.endswith("Toes"):
        return np.array([0, 0, 1.0])
    if "Thumb" in bone:
        return np.array([s * 0.72, 0, 0.69])
    return np.array([s, 0, 0])  # плечо, рука, кисть, пальцы


def _child_of(bone: str, m: dict[str, int]) -> str | None:
    kids = [b for b, p in VRM_PARENT.items() if p == bone and b in m]
    if bone in ("leftHand", "rightHand"):
        pref = [k for k in kids if "Middle" in k] or [k for k in kids if "Index" in k]
        return pref[0] if pref else None
    if bone == "hips":
        return next((k for k in ("spine", "chest", "upperChest", "neck", "head") if k in m), None)
    if bone in ("spine", "chest", "upperChest", "neck"):
        seq = ["spine", "chest", "upperChest", "neck", "head"]
        return next((k for k in seq[seq.index(bone) + 1:] if k in m), None)
    kids = [k for k in kids if "Shoulder" not in k]
    return kids[0] if kids else None


def _vrm_parent(bone: str, m: dict) -> str | None:
    p = VRM_PARENT.get(bone)
    while p is not None and p not in m:
        p = VRM_PARENT.get(p)
    return p


def retarget(skel: dict, clip_name: str, *, ref: str | None = None, mapping: dict[str, int] | None = None) -> dict:
    nodes = skel["nodes"]
    names = [n["name"] for n in nodes]
    parents = [n["parent"] for n in nodes]
    m = mapping or map_bones(names, parents)
    clip = skel["clips"][clip_name]
    if ref is None:
        ref = next((c for c in skel["clips"] if TPOSE_RE.match(c)), None)
    ref_clip = skel["clips"].get(ref) if ref else None
    if ref_clip:
        ref_clip = {**ref_clip, "frames": 1, "tracks": {k: {kk: vv[:1] for kk, vv in v.items()} for k, v in ref_clip["tracks"].items()}}
    rr, rt = _locals(skel, ref_clip)
    ref_r, ref_p = _fk(nodes, rr, rt)
    ref_r, ref_p = ref_r[0], ref_p[0]
    lr, lt = _locals(skel, clip)
    wr, wp = _fk(nodes, lr, lt)
    # Оси источника → оси VRM.
    up = ref_p[m["head"]] - ref_p[m["hips"]]
    if "leftUpperArm" in m and "rightUpperArm" in m:
        left = ref_p[m["leftUpperArm"]] - ref_p[m["rightUpperArm"]]
    else:
        left = ref_p[m["leftUpperLeg"]] - ref_p[m["rightUpperLeg"]]
    up /= np.linalg.norm(up)
    left -= up * np.dot(left, up)
    left /= np.linalg.norm(left)
    fwd = np.cross(left, up)
    B = np.stack([left, up, fwd])  # строки: мировой вектор источника → (x, y, z) VRM
    qB = qfrom_mat(B)
    qBi = qinv(qB)

    def to_vrm_q(q: np.ndarray) -> np.ndarray:
        return qmul(qmul(np.broadcast_to(qB, q.shape), q), np.broadcast_to(qBi, q.shape))

    world: dict[str, np.ndarray] = {}
    for bone, i in m.items():
        ch = _child_of(bone, m)
        if ch is not None:
            d0 = ref_p[m[ch]] - ref_p[i]
            e = vrm_dir(bone)
        else:
            # Концевая кость (голова, кисть без пальцев): направление продолжает родителя.
            p = _vrm_parent(bone, m)
            d0 = ref_p[i] - ref_p[m[p]] if p else np.array([0, 1.0, 0])
            e = vrm_dir(p) if p else vrm_dir(bone)
        if np.linalg.norm(d0) < 1e-6:
            d0 = qrot(ref_r[i], np.array([0, 1.0, 0]))
        # Корпус, шея, голова, ключицы, стопы в позе отсчёта — «как в покое VRM» (изгиб позвоночника и ключица
        # манекена — особенность его скелета, не позы); руки, ноги, кисти, пальцы — по направлению.
        A = np.array([0, 0, 0, 1.0]) if bone in STRUCT or bone.endswith(("Shoulder", "Foot", "Toes")) else qarc(e, B @ d0)
        R_ref = to_vrm_q(ref_r[i][None])[0]
        R_t = to_vrm_q(wr[:, i])
        world[bone] = qnorm(qmul(qmul(R_t, np.broadcast_to(qinv(R_ref), R_t.shape)), np.broadcast_to(A, R_t.shape)))
    bones: dict[str, list] = {}
    for bone, W in world.items():
        p = _vrm_parent(bone, m)
        L = W if p is None else qmul(qinv(world[p]), W)
        # Непрерывный знак (без скачков q ↔ −q между кадрами) и единичный w ≥ 0 у первого кадра.
        if L[0, 3] < 0:
            L[0] = -L[0]
        for f in range(1, len(L)):
            if np.dot(L[f], L[f - 1]) < 0:
                L[f] = -L[f]
        bones[bone] = [[round(float(v), 5) for v in q] for q in L]
    hips = (wp[:, m["hips"]] - ref_p[m["hips"]]) @ B.T
    leg = []
    for s in ("left", "right"):
        if all(k in m for k in (f"{s}UpperLeg", f"{s}LowerLeg", f"{s}Foot")):
            leg.append(np.linalg.norm(ref_p[m[f"{s}LowerLeg"]] - ref_p[m[f"{s}UpperLeg"]])
                       + np.linalg.norm(ref_p[m[f"{s}Foot"]] - ref_p[m[f"{s}LowerLeg"]]))
    return {"name": clip_name, "fps": clip["fps"], "frames": clip["frames"], "source": f"{skel.get('source', '')}:{clip_name}",
            "spine": "hips", "legLength": round(float(np.mean(leg)), 4) if leg else None,
            "bones": bones, "root": [[round(float(v), 4) for v in p] for p in hips]}


def load_skeleton(path: Path, fps: int = 30) -> dict:
    """GLB/glTF — сами; .json — нейтральный вид от node/vrm/fbx_dump.mjs (FBX)."""
    if path.suffix.lower() in (".glb", ".gltf"):
        return skeleton_from_gltf(path, fps)
    if path.suffix.lower() == ".json":
        return json.loads(path.read_text("utf-8"))
    raise ValueError(f"не знаю формат {path.suffix} (glb, gltf; fbx — через node/vrm/fbx_dump.mjs)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="гуманоидный клип → клип VRM")
    ap.add_argument("src", type=Path)
    ap.add_argument("out", type=Path, nargs="?")
    ap.add_argument("--clip", action="append")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args(argv)
    skel = load_skeleton(a.src, a.fps)
    m = map_bones([n["name"] for n in skel["nodes"]], [n["parent"] for n in skel["nodes"]])
    if a.list or not a.out:
        print(json.dumps({"bones": {k: skel["nodes"][v]["name"] for k, v in m.items()},
                          "clips": {k: c["frames"] for k, c in skel["clips"].items()}}, ensure_ascii=False, indent=1))
        return 0
    a.out.mkdir(parents=True, exist_ok=True)
    for name in a.clip or list(skel["clips"]):
        clip = retarget(skel, name, mapping=m)
        dest = a.out / (re.sub(r"[^A-Za-z0-9_.-]+", "_", name) + ".json")
        dest.write_text(json.dumps(clip, separators=(",", ":")), "utf-8")
        print(f"{dest}: {clip['frames']} кадров")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
