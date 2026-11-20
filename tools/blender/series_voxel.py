"""Кубический мир 3D-серии (``world: voxel``, место ``blockhouse``) и герой из кубиков (``hero: blocky``).

Всё своё и процедурное: пиксельные текстуры 16×16 на грань блока (трава, земля, булыжник, доски, бревно, листва,
дверь, сундук, луна) рисуются кодом из своих палитр, детерминированно (свой ``random.Random`` на текстуру), в Blender
— картинки с интерполяцией Closest (glTF пишет NEAREST, three.js рисует без сглаживания). Чужих текстур, моделей и
шрифтов нет.

Мир — сетка блоков ``B.VOXEL_BLOCK`` м (герой ≈ 1.8 блока): ячейка (i, j, k) — x, y по центру ``origin + (i, j)·b``,
z от ``k·b`` до ``(k+1)·b``; пол домика и земля вокруг — верх ячеек k = −1 (z = 0). Видимые грани — только у пустых
соседей, одна сетка на весь мир (материал на тип грани). Домик 7×6 блоков: стены доски на булыжном цоколе, углы —
бревна, дверь (своя модель на петле: дребезжит от стука) и окно с переплётом в стене y−, ступенчатая крыша; внутри —
факел на правой стене, кровать, сундук, тёмный угол (там появляется «гость»). Снаружи — поле с холмами, кубические
деревья, тропинка, факел у двери; небо — купол с градиентом, квадратная луна и звёзды (материалы ``sky_*`` — без
тумана дали), лунный свет — SUN (three.js — DirectionalLight с тенью), туман дали — ``scene["vvf_dist_fog"]``.

Герой: скелет KayKit остаётся (те же клипы, IK, взгляд, походка, лип-синк), его меши скрыты; на кости — коробки
(голова 8×8×8 «пикселей», корпус, руки и ноги из двух частей + кисти/ступни) с пиксельными текстурами; лицо —
отдельные объекты на «лице» (глаза 2×2 пикселя, брови, рот): их двигает тот же ``key_face`` (взгляд, моргание,
брови по эмоции, рот открывается от испуга).
"""
from __future__ import annotations

import json
import math
import random

import bmesh
import bpy
from mathutils import Matrix, Vector

import hero_shot as kit
from app import series3d_blender as B

V = Vector
BLK = B.VOXEL_BLOCK
TEX = 16


def _rgb(h: str) -> tuple[float, float, float]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _lin(h: str) -> tuple[float, float, float]:
    return kit.hexc(h)


def _mix(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


# ================================================================ картинки и материалы

def image(name: str, w: int, h: int, paint) -> bpy.types.Image:
    """Картинка w×h: ``paint(x, y)`` → sRGB (0..1), y — сверху вниз. Упакована в .blend (glTF возьмёт PNG)."""
    img = bpy.data.images.new(name, w, h, alpha=False)
    px = [0.0] * (w * h * 4)
    for yb in range(h):
        y = h - 1 - yb
        for x in range(w):
            r, g, b = paint(x, y)
            i = (yb * w + x) * 4
            px[i], px[i + 1], px[i + 2], px[i + 3] = r, g, b, 1.0
    img.pixels.foreach_set(px)
    img.file_format = "PNG"
    img.pack()
    return img


def tex_mat(name: str, img, rough: float = 0.92, spec: float = 0.2, emit: float = 0.0):
    """Principled с картинкой в цвете (Closest — пиксели без сглаживания); ``emit`` — та же картинка светится."""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    tn = nt.nodes.new("ShaderNodeTexImage")
    tn.image = img
    tn.interpolation = "Closest"
    nt.links.new(tn.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = rough
    bsdf.inputs["Specular IOR Level"].default_value = spec
    if emit:
        nt.links.new(tn.outputs["Color"], bsdf.inputs["Emission Color"])
        bsdf.inputs["Emission Strength"].default_value = emit
    return mat


def glow_mat(name: str, img, strength: float):
    """Чистое свечение картинкой (небо, луна): свет на него не влияет."""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    em = nt.nodes.new("ShaderNodeEmission")
    tn = nt.nodes.new("ShaderNodeTexImage")
    tn.image = img
    tn.interpolation = "Closest"
    nt.links.new(tn.outputs["Color"], em.inputs["Color"])
    em.inputs["Strength"].default_value = strength
    nt.links.new(em.outputs["Emission"], out.inputs["Surface"])
    return mat


def _pick(rng, cols, weights=None):
    return _rgb(rng.choices(cols, weights=weights)[0])


# ---------------------------------------------------------------- текстуры блоков (свои палитры)

def _noise_tile(seed, cols, weights=None, w=TEX, h=TEX):
    rng = random.Random(seed)
    grid = [[_pick(rng, cols, weights) for _x in range(w)] for _y in range(h)]
    return lambda x, y: grid[y][x]


GRASS = ["#4b8a34", "#3f7a2c", "#56963b", "#356b25", "#62a243"]
GRASS_W = [5, 4, 3, 2, 1]
DIRT = ["#6e4c30", "#5f412a", "#7c5838", "#523822", "#8a6542"]
DIRT_W = [5, 4, 3, 2, 1]


def paint_grass_top(seed=11):
    base = _noise_tile(seed, GRASS, GRASS_W)
    return base


def paint_grass_side(seed=12):
    rng = random.Random(seed)
    dirt = _noise_tile(seed + 1, DIRT, DIRT_W)
    grass = _noise_tile(seed + 2, GRASS, GRASS_W)
    edge = [3 + (1 if rng.random() < 0.5 else 0) + (1 if rng.random() < 0.2 else 0) for _x in range(TEX)]
    return lambda x, y: grass(x, y) if y < edge[x] else dirt(x, y)


def paint_dirt(seed=13):
    return _noise_tile(seed, DIRT, DIRT_W)


def paint_path(seed=14):
    return _noise_tile(seed, ["#8a6f4a", "#7a6040", "#94795a", "#6d5538"], [4, 3, 2, 2])


def paint_cobble(seed=21, dark=False):
    """Булыжник: ячейки Вороного (по кругу тайла) — камень своего тона, края — тёмный раствор, блик сверху-слева."""
    rng = random.Random(seed)
    cells = [(rng.uniform(0, TEX), rng.uniform(0, TEX), rng.choice(["#8c8c8a", "#7b7b79", "#9a9a97", "#6f6f6d"]))
             for _ in range(9)]

    def owner(x, y):
        best, bi = 1e9, 0
        for i, (cx, cy, _c) in enumerate(cells):
            dx = min(abs(x - cx), TEX - abs(x - cx))
            dy = min(abs(y - cy), TEX - abs(y - cy))
            d = dx * dx + dy * dy
            if d < best:
                best, bi = d, i
        return bi
    own = [[owner(x + 0.5, y + 0.5) for x in range(TEX)] for y in range(TEX)]
    jit = [[rng.uniform(-0.04, 0.04) for _x in range(TEX)] for _y in range(TEX)]

    def paint(x, y):
        o = own[y][x]
        if own[(y + 1) % TEX][x] != o or own[y][(x + 1) % TEX] != o:
            c = _rgb("#4c4b49")
        elif own[(y - 1) % TEX][x] != o or own[y][(x - 1) % TEX] != o:
            c = _mix(_rgb(cells[o][2]), (1, 1, 1), 0.12)
        else:
            c = _rgb(cells[o][2])
        c = tuple(max(0.0, min(1.0, v + jit[y][x])) for v in c)
        return _mix(c, (0.05, 0.06, 0.08), 0.35) if dark else c
    return paint


def paint_planks(seed=31, cols=("#a87b46", "#9a6f3e", "#b5874f"), seam="#6a4826", grain="#8c6336"):
    """Доски: 4 доски по 4 пикселя, шов снизу каждой, стык в своём месте, волокна."""
    rng = random.Random(seed)
    boards = [(_rgb(rng.choice(cols)), rng.randrange(2, TEX - 2)) for _ in range(4)]
    g = [[rng.random() < 0.12 for _x in range(TEX)] for _y in range(TEX)]

    def paint(x, y):
        b, cut = boards[y // 4]
        if y % 4 == 3:
            return _rgb(seam)
        if x == cut:
            return _mix(b, _rgb(seam), 0.45)
        c = _rgb(grain) if g[y][x] else b
        return _mix(c, (1, 1, 1), 0.06) if y % 4 == 0 else c
    return paint


def paint_log_side(seed=41):
    rng = random.Random(seed)
    col = [rng.choice(["#5d4127", "#4d351f", "#6a4b2e", "#3f2b19"]) for _ in range(TEX)]
    jit = [[rng.random() < 0.18 for _x in range(TEX)] for _y in range(TEX)]
    return lambda x, y: _mix(_rgb(col[x]), _rgb("#2f2012"), 0.5) if jit[y][x] else _rgb(col[x])


def paint_log_top(seed=42):
    rng = random.Random(seed)
    jit = [[rng.uniform(-0.03, 0.03) for _x in range(TEX)] for _y in range(TEX)]

    def paint(x, y):
        d = max(abs(x - 7.5), abs(y - 7.5))
        if d > 6.5:
            return _rgb("#4d351f")
        c = _rgb("#b38a55") if int(d) % 2 == 0 else _rgb("#9c7444")
        return tuple(max(0, min(1, v + jit[y][x])) for v in c)
    return paint


def paint_leaves(seed=51):
    return _noise_tile(seed, ["#2f5f23", "#3a6f2b", "#264e1c", "#1b3a14", "#447a31"], [5, 3, 4, 3, 1])


def paint_stone(seed=61):
    return _noise_tile(seed, ["#7f7f7f", "#757575", "#8a8a8a", "#6b6b6b", "#5e5e5e"], [5, 4, 3, 2, 1])


def paint_moon(seed=71):
    rng = random.Random(seed)
    crater = {(rng.randrange(2, 14), rng.randrange(2, 14)): rng.choice([1, 2]) for _ in range(6)}

    def paint(x, y):
        for (cx, cy), r in crater.items():
            if abs(x - cx) < r and abs(y - cy) < r:
                return _rgb("#c4c9d2")
        return _rgb("#eef1f6") if (x + y) % 7 else _rgb("#dfe3ea")
    return paint


def paint_door(seed=81):
    """Дверь 16×32: вертикальные доски, рамка темнее, два окошка сверху (темнота за ними), ручка."""
    rng = random.Random(seed)
    board = [_rgb(rng.choice(["#a8763f", "#9a6c39", "#b58247"])) for _ in range(4)]

    def paint(x, y):
        if (3 <= x <= 6 or 9 <= x <= 12) and 4 <= y <= 10:
            return _rgb("#141820") if not (y == 4 or x in (3, 9)) else _rgb("#0b0d12")
        if x == 0 or x == 15 or y == 0 or y == 31 or y == 15 or y == 16:
            return _rgb("#5a3a1f")
        if x == 12 and y in (18, 19):
            return _rgb("#a7a7a3") if y == 18 else _rgb("#575754")
        if x % 4 == 0:
            return _rgb("#5f4023")
        c = board[x // 4]
        return _mix(c, _rgb("#5f4023"), 0.35) if rng.random() < 0.08 else c
    return paint


def paint_chest_front(seed=91):
    rng = random.Random(seed)

    def paint(x, y):
        if x in (0, 15) or y in (0, 15) or y == 5:
            return _rgb("#3d2814")
        if 7 <= x <= 8 and 4 <= y <= 7:
            return _rgb("#b9b9b4") if y < 6 else _rgb("#6d6d69")
        c = _rgb("#9b6a33") if y < 5 else _rgb("#8a5c2b")
        return _mix(c, _rgb("#6a431f"), 0.4) if rng.random() < 0.1 else c
    return paint


def paint_blanket(seed=101):
    rng = random.Random(seed)

    def paint(x, y):
        c = _rgb("#8e2b2b") if (x // 4 + y // 4) % 2 else _rgb("#a33636")
        return _mix(c, _rgb("#5e1a1a"), 0.3) if rng.random() < 0.1 else c
    return paint


def paint_sky(w=4, h=64):
    """Небо купола по высоте (v снизу вверх = y сверху вниз): зенит почти чёрный синий, горизонт — дымка."""
    zen, hor, low = _rgb("#03050b"), _rgb("#1a2440"), _rgb("#0a0e1a")

    def paint(x, y):
        v = 1.0 - (y + 0.5) / h            # 0 — низ купола, 1 — верх
        if v < 0.5:
            return _mix(low, hor, (v / 0.5) ** 3)
        t = (v - 0.5) / 0.5
        return _mix(hor, zen, min(1.0, t ** 0.6))
    return paint


HORIZON = "#1a2440"


# ================================================================ сетка блоков

# грань: нормаль, угол начала (в долях ячейки), u, v — по правилу правой руки (u × v = нормаль), u — слева
# направо, v — снизу вверх, если смотреть на грань снаружи
FACES = {
    "px": ((1, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)),
    "nx": ((-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1)),
    "py": ((0, 1, 0), (1, 1, 0), (-1, 0, 0), (0, 0, 1)),
    "ny": ((0, -1, 0), (0, 0, 0), (1, 0, 0), (0, 0, 1)),
    "pz": ((0, 0, 1), (0, 0, 1), (1, 0, 0), (0, 1, 0)),
    "nz": ((0, 0, -1), (0, 1, 0), (1, 0, 0), (0, -1, 0)),
}
SIDE = ("px", "nx", "py", "ny")


class Mats:
    """Материалы мира (один раз на сцену)."""

    def __init__(self):
        mk = lambda n, p, w=TEX, h=TEX, **kw: tex_mat(f"vox_{n}", image(f"vox_{n}", w, h, p), **kw)  # noqa: E731
        self.m = {
            "grass_top": mk("grass_top", paint_grass_top()),
            "grass_side": mk("grass_side", paint_grass_side()),
            "dirt": mk("dirt", paint_dirt()),
            "path": mk("path", paint_path()),
            "cobble": mk("cobble", paint_cobble(), rough=0.95),
            "planks": mk("planks", paint_planks()),
            "roof": mk("roof", paint_planks(33, ("#5a3c22", "#4f341d", "#634328"), "#2e1d0e", "#432c17")),
            "log_side": mk("log_side", paint_log_side()),
            "log_top": mk("log_top", paint_log_top()),
            "leaves": mk("leaves", paint_leaves(), rough=0.8),
            "stone": mk("stone", paint_stone(), rough=0.95),
            "door": mk("door", paint_door(), w=16, h=32, rough=0.85),
            "chest": mk("chest", paint_chest_front(), rough=0.8),
            "blanket": mk("blanket", paint_blanket(), rough=0.97),
        }
        self.m["wool_white"] = kit.principled("vox_wool_white", _lin("#d9d6cf"), rough=0.97, spec=0.1)

    def __getitem__(self, k):
        return self.m[k]


# тип блока → материал верхней, боковой, нижней грани
BLOCKS = {
    "grass": ("grass_top", "grass_side", "dirt"),
    "dirt": ("dirt", "dirt", "dirt"),
    "path": ("path", "dirt", "dirt"),
    "cobble": ("cobble", "cobble", "cobble"),
    "planks": ("planks", "planks", "planks"),
    "roof": ("roof", "roof", "roof"),
    "log": ("log_top", "log_side", "log_top"),
    "leaves": ("leaves", "leaves", "leaves"),
    "stone": ("stone", "stone", "stone"),
}


def voxel_mesh(name: str, cells: dict, origin, mats: Mats, skip_bottom: set | None = None):
    """Видимые грани блоков ``cells`` ({(i, j, k): тип}) одной сеткой: материал на тип грани, UV — грань целиком.
    ``skip_bottom`` — типы, у которых нижние грани не нужны (земля снизу не видна)."""
    ox, oy = origin
    skip_bottom = skip_bottom or set()
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    uv = bm.loops.layers.uv.new("UVMap")
    order: list[str] = []
    idx: dict[str, int] = {}
    vcache: dict[tuple, bmesh.types.BMVert] = {}

    def vert(p):
        key = (round(p[0], 5), round(p[1], 5), round(p[2], 5))
        v = vcache.get(key)
        if v is None:
            v = vcache[key] = bm.verts.new(key)
        return v
    for (i, j, k), kind in cells.items():
        top, side, bottom = BLOCKS[kind]
        x0, y0, z0 = ox + (i - 0.5) * BLK, oy + (j - 0.5) * BLK, k * BLK
        for fname, (n, o, u, v) in FACES.items():
            nb = (i + n[0], j + n[1], k + n[2])
            if nb in cells:
                continue
            if fname == "nz" and kind in skip_bottom:
                continue
            mname = top if fname == "pz" else bottom if fname == "nz" else side
            if mname not in idx:
                idx[mname] = len(order)
                order.append(mname)
            c0 = (x0 + o[0] * BLK, y0 + o[1] * BLK, z0 + o[2] * BLK)
            pts = [c0, tuple(c0[a] + u[a] * BLK for a in range(3)),
                   tuple(c0[a] + (u[a] + v[a]) * BLK for a in range(3)), tuple(c0[a] + v[a] * BLK for a in range(3))]
            try:
                f = bm.faces.new([vert(p) for p in pts])
            except ValueError:
                continue
            f.material_index = idx[mname]
            for lp, (uu, vv) in zip(f.loops, ((0, 0), (1, 0), (1, 1), (0, 1))):
                lp[uv].uv = (uu, vv)
    bm.to_mesh(me)
    bm.free()
    for mname in order:
        me.materials.append(mats[mname])
    ob = kit.link(bpy.data.objects.new(name, me))
    return ob


def tbox(name: str, size, center, mat, uvs: dict | None = None, face_mats: dict | None = None, rot=(0.0, 0.0, 0.0)):
    """Коробка с UV по граням: ``uvs`` — грань → (u0, v0, u1, v1) (по умолчанию — вся картинка), ``face_mats`` —
    грань → свой материал. Центр — ``center``, повороты — у объекта."""
    sx, sy, sz = size
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    uvl = bm.loops.layers.uv.new("UVMap")
    order = [mat]
    for fname, (n, o, u, v) in FACES.items():
        mt = (face_mats or {}).get(fname, mat)
        if mt not in order:
            order.append(mt)
        c0 = ((o[0] - 0.5) * sx, (o[1] - 0.5) * sy, (o[2] - 0.5) * sz)
        uu = (u[0] * sx, u[1] * sy, u[2] * sz)
        vv = (v[0] * sx, v[1] * sy, v[2] * sz)
        pts = [c0, tuple(c0[a] + uu[a] for a in range(3)), tuple(c0[a] + uu[a] + vv[a] for a in range(3)),
               tuple(c0[a] + vv[a] for a in range(3))]
        f = bm.faces.new([bm.verts.new(p) for p in pts])
        f.material_index = order.index(mt)
        r = (uvs or {}).get(fname, (0.0, 0.0, 1.0, 1.0))
        for lp, (a, b) in zip(f.loops, ((r[0], r[1]), (r[2], r[1]), (r[2], r[3]), (r[0], r[3]))):
            lp[uvl].uv = (a, b)
    bm.to_mesh(me)
    bm.free()
    for mt in order:
        me.materials.append(mt)
    ob = kit.link(bpy.data.objects.new(name, me))
    ob.location = center
    ob.rotation_euler = rot
    return ob


def flat_mat(name, hexcol, rough=0.9, emit=0.0):
    c = _lin(hexcol)
    return kit.principled(name, c, rough=rough, spec=0.15, emit=c if emit else None, emit_strength=emit)


# ================================================================ домик в поле

HOUSE_X = (-3, 3)        # стены по i
HOUSE_Y = (-3, 2)        # стены по j (y− — стена с дверью и окном)
DOOR_CELL = (-1, -3)     # дверь: k = 0..1
WINDOW_CELL = (1, -3)    # окно: k = 1
TREES = ((-9, -13, 5), (7, -17, 4), (-15, 3, 5), (12, 5, 6), (-6, 14, 5), (17, -7, 4), (-21, -24, 6), (21, -29, 5),
         (3, 19, 6), (-25, -6, 5), (26, 10, 5), (-12, -34, 6))


def _height(i: int, j: int) -> int:
    """Верхняя ячейка земли (k): у домика ровно (−1), дальше — пологие холмы, за домиком (под луной) — выше."""
    d = max(abs(i), abs(j + 0.5))
    if d <= 13:
        return -1
    h = 1.1 * math.sin(i * 0.21 + 1.3) * math.cos(j * 0.17 + 0.4) + 0.8 * math.sin((i + j) * 0.11 + 2.0)
    if j > 10:
        h += (j - 10) * 0.32
    if j < -26:
        h += (-j - 26) * 0.12
    h *= min(1.0, (d - 13) / 6.0)
    return max(-2, int(round(h)) - 1)


def build_blockhouse(o, fonts, decal):
    """Место ``blockhouse``: мир вокруг начала координат места ``o``. → ``{voxel: {...}}`` (для ``key_world``)."""
    _ = fonts, decal
    mats = Mats()
    cells: dict[tuple, str] = {}
    hmap = {}
    for i in range(-44, 45):
        for j in range(-52, 26):
            if math.hypot(i, j) * BLK > 25.0:
                continue          # внутри купола неба (R 27 м)
            h = _height(i, j)
            hmap[(i, j)] = h
            for k in range(h - 3, h + 1):
                cells[(i, j, k)] = "grass" if k == h else "dirt"
    # тропинка от двери
    for j in range(-12, -3):
        ii = DOOR_CELL[0] + (1 if j < -8 else 0)
        if (ii, j) in hmap:
            cells[(ii, j, hmap[(ii, j)])] = "path"
    # деревья: ствол, крона 5×5×2 + 3×3 + верх
    for (ti, tj, th) in TREES:
        if (ti, tj) not in hmap:
            continue
        g = hmap[(ti, tj)]
        for k in range(g + 1, g + 1 + th):
            cells[(ti, tj, k)] = "log"
        top = g + th
        for dk, r in ((-1, 2), (0, 2), (1, 1), (2, 0)):
            for di in range(-r, r + 1):
                for dj in range(-r, r + 1):
                    if r == 2 and abs(di) == 2 and abs(dj) == 2 and (ti + tj + dk) % 2:
                        continue      # углы кроны через раз — не куб
                    c = (ti + di, tj + dj, top + dk)
                    if c not in cells:
                        cells[c] = "leaves"
    # домик
    x0, x1 = HOUSE_X
    y0, y1 = HOUSE_Y
    for i in range(x0, x1 + 1):
        for j in range(y0, y1 + 1):
            cells[(i, j, -1)] = "cobble" if i in (x0, x1) or j in (y0, y1) else "planks"
            for k in range(0, 3):
                wall = i in (x0, x1) or j in (y0, y1)
                if not wall:
                    cells.pop((i, j, k), None)
                    continue
                corner = i in (x0, x1) and j in (y0, y1)
                cells[(i, j, k)] = "log" if corner else ("cobble" if k == 0 else "planks")
    for k in (0, 1):
        cells.pop((DOOR_CELL[0], DOOR_CELL[1], k), None)
    cells.pop((WINDOW_CELL[0], WINDOW_CELL[1], 1), None)
    # крыша: плита с выносом, потом ступени
    for k, r in ((3, 0), (4, -1), (5, -2), (6, -3)):
        for i in range(x0 - 1 - r, x1 + 2 + r):
            for j in range(y0 - 1 - r, y1 + 2 + r):
                if k == 3 or (x0 - 1 - r <= i <= x1 + 1 + r and y0 - 1 - r <= j <= y1 + 1 + r):
                    cells[(i, j, k)] = "roof"
    # над домиком и вокруг земли не должно торчать
    world = voxel_mesh("vox_world", cells, o, mats, skip_bottom={"grass", "dirt", "path"})
    ox, oy = o
    # ---- дверь на петле (левый край двери, смотреть изнутри), дребезжит от стука
    dx0 = ox + (DOOR_CELL[0] - 0.5) * BLK
    face_y = oy + (DOOR_CELL[1] + 0.5) * BLK          # внутренняя плоскость передней стены
    thick = 3 / 16 * BLK
    hinge = bpy.data.objects.new("vox_door_hinge", None)
    kit.link(hinge)
    hinge.location = (dx0 + 0.01, face_y - thick / 2, 0.0)
    door = tbox("vox_door", (BLK - 0.02, thick, 2 * BLK - 0.01), (0, 0, 0), mats["door"],
                uvs={"px": (0, 0, 3 / 16, 1), "nx": (0, 0, 3 / 16, 1), "pz": (0, 0, 1, 3 / 32), "nz": (0, 0, 1, 3 / 32)})
    door.parent = hinge
    door.location = ((BLK - 0.02) / 2, 0.0, BLK - 0.005)
    # ---- окно: переплёт крестом и подоконник
    wx = ox + WINDOW_CELL[0] * BLK
    wy = oy + WINDOW_CELL[1] * BLK
    bar = 1.5 / 16 * BLK
    tbox("vox_win_v", (bar, bar, BLK), (wx, face_y - bar, 1.5 * BLK), mats["planks"])
    tbox("vox_win_h", (BLK, bar, bar), (wx, face_y - bar, 1.5 * BLK), mats["planks"])
    tbox("vox_win_sill", (BLK, 3 / 16 * BLK, 2 / 16 * BLK), (wx, face_y + 1.5 / 16 * BLK, BLK + 1 / 16 * BLK), mats["planks"])
    _ = wy
    # ---- мебель: кровать у задней стены справа, сундук у двери
    by = oy + 1 * BLK
    bx = ox + 1.5 * BLK
    tbox("vox_bed_base", (2 * BLK - 0.02, BLK - 0.02, 3 / 16 * BLK), (bx, by, 3 / 32 * BLK), mats["planks"])
    tbox("vox_bed_blanket", (1.45 * BLK, BLK - 0.03, 6 / 16 * BLK), (bx - 0.27 * BLK, by, 6 / 16 * BLK), mats["blanket"])
    tbox("vox_bed_pillow", (0.5 * BLK, BLK - 0.06, 5 / 16 * BLK), (bx + 0.72 * BLK, by, 5.5 / 16 * BLK), mats["wool_white"])
    tbox("vox_chest", (14 / 16 * BLK, 14 / 16 * BLK, 14 / 16 * BLK), (ox - 2 * BLK, oy - 2 * BLK, 7 / 16 * BLK), mats["planks"],
         face_mats={"px": mats["chest"]}, rot=(0, 0, 0))
    # ---- факелы: внутри на правой стене, снаружи у двери
    torches = [_torch("vox_torch_in", (ox, oy - 2.5 * BLK, 0.62), (0, 1), mats, energy=6.5),
               _torch("vox_torch_out", (ox - 2 * BLK, oy - 3.5 * BLK, 0.62), (0, -1), mats, energy=9.0)]
    win = kit.make_light("vox_window_cold", "AREA", (0.52, 0.64, 1.0), 2.2, (wx, face_y + 0.03, 1.5 * BLK),
                         rot=(math.radians(-90), 0, 0), size=BLK * 0.9, shadow=False, volume=0.4)
    _ = win
    # ---- небо: купол, луна, звёзды; лунный свет
    sky = _sky(o)
    # ---- «гость» в тёмном углу (появляется по событию appear)
    guest = _guest(o)
    # дымка в домике (свет факела во взвеси)
    fog = _fog_box("vox_house", o)
    return {"voxel": {"world": world, "door_hinge": hinge, "torches": torches, "sky": sky, "guest": guest, "fog": fog,
                      "origin": o}}


def _torch(name, base, out_dir, mats, energy=7.5):
    """Факел на стене: палка с наклоном от стены, огонь — светящийся кубик, точечный свет у огня."""
    stick_mat = bpy.data.materials.get("vox_torch_stick") or flat_mat("vox_torch_stick", "#6b4a2a", rough=0.9)
    flame_mat = bpy.data.materials.new(f"{name}_flame")
    flame_mat.use_nodes = True
    nt = flame_mat.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Color"].default_value = (*_lin("#ffb13b"), 1.0)
    em.inputs["Strength"].default_value = 5.0
    nt.links.new(em.outputs["Emission"], out.inputs["Surface"])
    core_mat = bpy.data.materials.get("vox_torch_core") or kit.emission_mat("vox_torch_core", _lin("#fff0b0"), 9.0)
    ax, ay = out_dir
    tilt = math.radians(24)
    L = 10 / 16 * BLK
    # палка: низ у стены, верх отклонён от неё
    rot = (-tilt * ay, tilt * ax, 0.0)          # верх палки — от стены (поворот оси z к out_dir)
    mid = (base[0] + ax * math.sin(tilt) * L / 2, base[1] + ay * math.sin(tilt) * L / 2, base[2] + math.cos(tilt) * L / 2)
    tbox(f"{name}_stick", (2 / 16 * BLK, 2 / 16 * BLK, L), mid, stick_mat, rot=rot)
    top = (base[0] + ax * math.sin(tilt) * L, base[1] + ay * math.sin(tilt) * L, base[2] + math.cos(tilt) * L)
    flame = tbox(f"{name}_flame", (3.2 / 16 * BLK, 3.2 / 16 * BLK, 3.6 / 16 * BLK), (top[0], top[1], top[2] + 0.02),
                 flame_mat, rot=rot)
    tbox(f"{name}_core", (1.6 / 16 * BLK, 1.6 / 16 * BLK, 2.0 / 16 * BLK), (top[0], top[1], top[2] + 0.015), core_mat, rot=rot)
    li = kit.make_light(f"{name}_light", "POINT", (1.0, 0.56, 0.22), energy,
                        (top[0] + ax * 0.06, top[1] + ay * 0.06, top[2] + 0.05), size=0.03, volume=1.0)
    try:
        li.data.use_custom_distance = True
        li.data.cutoff_distance = 9.0
    except AttributeError:
        pass
    return {"light": li, "flame": flame, "mat": flame_mat, "node": em, "energy": energy, "seed": len(name) * 7 + int(base[0] * 10)}


def _sky(o):
    ox, oy = o
    R = 27.0
    sky = glow_mat("sky_dome", image("sky_grad", 4, 64, paint_sky()), 1.0)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, radius=R, location=(ox, oy, 0.0))
    dome = bpy.context.active_object
    dome.name = "sky_dome"
    dome.data.materials.append(sky)
    # луна за домиком (y+), квадратная, со своими кратерами
    moon_mat = glow_mat("sky_moon", image("sky_moon", 16, 16, paint_moon()), 1.5)
    mp = V((ox + 5.0, oy + 19.5, 14.5))
    moon = tbox("sky_moon", (2.0, 0.05, 2.0), tuple(mp), moon_mat)
    moon.rotation_euler = (0.0, 0.0, 0.0)
    d = (V((ox, oy, 1.0)) - mp).normalized()
    moon.rotation_mode = "QUATERNION"
    moon.rotation_quaternion = d.to_track_quat("-Y", "Z")      # лицевая грань (−y) — к домику
    halo = kit.emission_mat("sky_moon_halo", _lin("#8fa6d6"), 0.35)
    h_ = tbox("sky_moon_halo", (3.4, 0.02, 3.4), tuple(mp + (-d) * 0.3), halo)
    h_.rotation_mode = "QUATERNION"
    h_.rotation_quaternion = moon.rotation_quaternion
    # звёзды: квадратики на куполе выше горизонта
    rng = random.Random(5)
    star_mats = [kit.emission_mat(f"sky_star{i}", _lin(c), s) for i, (c, s) in
                 enumerate((("#ffffff", 1.6), ("#cfe0ff", 1.1), ("#fff2d6", 0.8)))]
    me = bpy.data.meshes.new("sky_stars")
    bm = bmesh.new()
    for _ in range(260):
        az = rng.uniform(0, 2 * math.pi)
        el = math.asin(rng.uniform(0.12, 0.98))
        r = R - 0.6
        c = V((ox + r * math.cos(el) * math.cos(az), oy + r * math.cos(el) * math.sin(az), r * math.sin(el)))
        nrm = (V((ox, oy, 0)) - c).normalized()
        q = nrm.to_track_quat("Z", "Y")
        s = rng.choice((0.04, 0.05, 0.065, 0.09))
        vs = [bm.verts.new(c + q @ V((a * s, b * s, 0))) for a, b in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        f = bm.faces.new(vs)
        f.material_index = rng.choices((0, 1, 2), weights=(3, 2, 1))[0]
    bm.to_mesh(me)
    bm.free()
    for mt in star_mats:
        me.materials.append(mt)
    stars = kit.link(bpy.data.objects.new("sky_stars", me))
    # лунный свет: SUN от луны (three.js — направленный с тенью); сам объект — у места (план считает его «рядом»)
    sun = kit.make_light("moon_sun", "SUN", (0.58, 0.70, 1.0), 1.6, (ox, oy, 4.0), size=0.02, volume=0.0)
    sun.rotation_mode = "QUATERNION"
    sun.rotation_quaternion = d.to_track_quat("-Z", "Y")
    try:
        sun.data.angle = math.radians(1.5)
    except AttributeError:
        pass
    return {"dome": dome, "moon": moon, "stars": stars, "sun": sun}


def _guest(o):
    """Высокий тёмный силуэт из кубиков в дальнем левом углу: длинные руки, голова набок, два белых квадрата глаз."""
    ox, oy = o
    p = 1 / 16 * BLK
    body = tex_mat("guest_body", image("guest_body", 16, 16, _noise_tile(301, ["#141016", "#1c1621", "#0d0a0f", "#221a28"],
                                                                          [5, 3, 3, 1])), rough=0.95)
    # глаза: чёрные, пока не «откроются» — свечение Principled по дорожке (чистое Emission с силой 0 glTF теряет)
    eyes = kit.principled("guest_eyes", (0.0, 0.0, 0.0), rough=0.6, emit=_lin("#f3eaff"), emit_strength=0.0, spec=0.0)
    em = eyes.node_tree.nodes["Principled BSDF"]
    root = bpy.data.objects.new("guest_root", None)
    kit.link(root)
    gx, gy = B.LOCS["blockhouse"]["targets"]["corner"][:2]
    root.location = (ox + gx + 0.04, oy + gy - 0.02, 0.0)
    # смотрит на окно (место героя у окна)
    wx, wy, _h = B.LOCS["blockhouse"]["spots"]["window"]
    root.rotation_euler = (0, 0, math.atan2(wx - gx, -(wy - gy)))
    parts = []

    def part(name, size_px, center_px, mat=body, rot=(0, 0, 0)):
        ob = tbox(f"guest_{name}", tuple(s * p for s in size_px), tuple(c * p for c in center_px), mat, rot=rot)
        ob.parent = root
        parts.append(ob)
        return ob
    part("leg_l", (3, 3, 21), (2.2, 0, 10.5))
    part("leg_r", (3, 3, 21), (-2.2, 0, 10.5))
    part("torso", (8, 4, 14), (0, 0, 28))
    part("arm_l", (3, 3, 25), (5.6, 0.4, 22.5), rot=(0.05, -0.03, 0))
    part("arm_r", (3, 3, 25), (-5.6, -0.6, 22.0), rot=(-0.10, 0.04, 0))
    head = part("head", (8, 8, 8), (0, 0, 39.2), rot=(0, math.radians(9), math.radians(-6)))
    # глаза — на передней грани головы (−y), дети головы: наклон тот же
    for sx in (-1, 1):
        e = tbox(f"guest_eye_{'l' if sx > 0 else 'r'}", (2 * p, 0.004, 1 * p), (sx * 2.0 * p, -4 * p - 0.002, 0.3 * p), eyes)
        e.parent = head
        parts.append(e)
    return {"root": root, "eyes_mat": eyes, "eyes_node": em, "parts": parts}


def _fog_box(name, o):
    fog = bpy.data.materials.new(f"{name}_fog")
    fog.use_nodes = True
    nt = fog.node_tree
    for nd in list(nt.nodes):
        nt.nodes.remove(nd)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    vol = nt.nodes.new("ShaderNodeVolumePrincipled")
    vol.inputs["Density"].default_value = 0.06
    vol.inputs["Anisotropy"].default_value = 0.4
    nt.links.new(vol.outputs["Volume"], out.inputs["Volume"])
    ox, oy = o
    return kit.box(f"{name}_fog_volume", (5 * BLK - 0.02, 4 * BLK - 0.02, 3 * BLK - 0.02),
                   (ox, oy - 0.5 * BLK, 1.5 * BLK), fog, bevel=0)


# ---------------------------------------------------------------- дорожки мира

def flicker(t: float, seed: int) -> float:
    """Огонь факела: медленное дыхание + быстрые всплески, 0.72..1.08 (детерминированно)."""
    s = seed * 0.37
    v = (0.88 + 0.07 * math.sin(2 * math.pi * 1.3 * t + s) + 0.05 * math.sin(2 * math.pi * 3.7 * t + 2.1 * s)
         + 0.035 * math.sin(2 * math.pi * 8.3 * t + 0.7 * s) + 0.02 * math.sin(2 * math.pi * 13.1 * t + s * 1.9))
    return max(0.6, min(1.1, v))


def key_world(places: dict, perf: dict, frames: list[int], events: list[dict] | None = None) -> None:
    """Факелы дрожат, дверь дребезжит от стука, «гость» появляется и открывает глаза, мир — ночное небо и туман."""
    vx = (places.get("blockhouse") or {}).get("voxel")
    if not vx:
        return
    n = len(frames)
    times = [(f - 1) / B.FPS for f in frames]
    for tr in vx["torches"]:
        vals = [flicker(t, tr["seed"]) for t in times]
        kit.fcurve(tr["light"].data, "energy", 0, frames, [tr["energy"] * v for v in vals])
        kit.fcurve(tr["mat"].node_tree, f'nodes["{tr["node"].name}"].inputs[1].default_value', 0, frames,
                   [5.0 * (0.75 + 0.25 * v) for v in vals])
        kit.key_vec(tr["flame"], "scale", frames, [(1.0, 1.0, 0.9 + 0.2 * v) for v in vals])
    # стук: дверь вздрагивает внутрь и чуть поворачивается на петле
    knocks = [float(e["t"]) for e in (events or []) if e.get("kind") == "knock"]
    rot, loc = [], []
    h = vx["door_hinge"]
    base = tuple(h.location)
    for t in times:
        a = sum(math.exp(-(t - tk) / 0.09) * math.sin(min(math.pi, (t - tk) / 0.03 * math.pi)) for tk in knocks if t >= tk)
        rot.append(math.radians(1.1) * a)
        loc.append((base[0], base[1] + 0.006 * a, base[2]))
    kit.fcurve(h, "rotation_euler", 2, frames, rot)
    kit.key_vec(h, "location", frames, loc)
    # «гость»: тело по appear["guest"], глаза — по appear["eyes"] (вспыхивают, потом чуть дрожат)
    lt = perf["lights"]
    g = vx["guest"]
    vis = [float((lt[i].get("appear") or {}).get("guest", 0)) for i in range(n)]
    kit.key_vec(g["root"], "scale", frames, [(v, v, v) for v in vis], interp="CONSTANT")
    eyes = [float((lt[i].get("appear") or {}).get("eyes", 0)) for i in range(n)]
    on_t = next((times[i] for i in range(n) if eyes[i] > 0.5), None)
    ev = []
    for i, t in enumerate(times):
        if eyes[i] < 0.5 or on_t is None:
            ev.append(0.0)
            continue
        x = t - on_t
        ev.append(5.0 * min(1.0, x / 0.06) * (0.96 + 0.04 * math.sin(t * 23.0)))
    node = g["eyes_node"]
    k_ = list(node.inputs).index(node.inputs["Emission Strength"])
    kit.fcurve(g["eyes_mat"].node_tree, f'nodes["{node.name}"].inputs[{k_}].default_value', 0, frames, ev)
    # мир: ночное небо (фон) и туман дали — по цвету горизонта
    sc = bpy.context.scene
    hz = _lin(HORIZON)
    sc.world.node_tree.nodes["Background"].inputs["Color"].default_value = (*hz, 1.0)
    sc["vvf_dist_fog"] = json.dumps({"color": [round(c, 5) for c in hz], "density": 0.05})


# ================================================================ герой из кубиков

PX = 0.055        # «пиксель» героя в системе скелета KayKit без масштаба (×S = 0.0275 м — как пиксель блока)

HERO_LOOK = {"skin": "#d9a47a", "skin_d": "#c18c63", "hair": "#3b2416", "hair_l": "#52331f", "shirt": "#4f6f3a",
             "shirt_d": "#415d2f", "shirt_l": "#5f8247", "pants": "#4a3b30", "pants_d": "#3c3027", "shoe": "#2a2a2c",
             "shoe_l": "#3b3b3e", "eye": "#f2f2ee", "pupil": "#1f2c4c", "mouth": "#3a1a14"}


def _atlas(name, dims_px, paint_face, look):
    """Текстура коробки: 6 граней в ряд (front, back, left, right, top, bottom) — размер грани в пикселях по коробке.
    → (картинка, {грань FACES: (u0, v0, u1, v1)})."""
    wx, wy, wz = dims_px
    tiles = [("ny", "front", wx, wz), ("py", "back", wx, wz), ("px", "left", wy, wz), ("nx", "right", wy, wz),
             ("pz", "top", wx, wy), ("nz", "bottom", wx, wy)]
    W = sum(t[2] for t in tiles)
    Hh = max(t[3] for t in tiles)
    where = {}
    off = 0
    for fk, face, w, h in tiles:
        where[fk] = (face, off, w, h)
        off += w
    grid = [[(0.0, 0.0, 0.0)] * W for _ in range(Hh)]
    for fk, (face, x0, w, h) in where.items():
        for y in range(h):
            for x in range(w):
                grid[y][x0 + x] = paint_face(face, x, y, w, h)
    img = image(name, W, Hh, lambda x, y: grid[y][x])
    uvs = {fk: (x0 / W, 1.0 - h / Hh, (x0 + w) / W, 1.0) for fk, (face, x0, w, h) in where.items()}
    return img, uvs


def _painter(kind, look, seed):
    rng = random.Random(seed)
    C = {k: _rgb(v) for k, v in look.items()}
    jit = {}

    def j(face, x, y, a, b):
        k = (face, x, y)
        if k not in jit:
            jit[k] = rng.random()
        return a if jit[k] < 0.78 else b

    def head(face, x, y, w, h):
        hair = j(face, x, y, C["hair"], C["hair_l"])
        skin = j(face, x, y, C["skin"], C["skin_d"]) if face != "front" else C["skin"]
        if face == "top":
            return hair
        if face == "bottom":
            return C["skin_d"]
        if face == "back":
            return hair if y < 7 else C["skin_d"]
        if face == "front":
            if y <= 1 or (y == 2 and x in (0, 1, 5, 6, 7)) or (y == 3 and x in (0, 7)):
                return hair
            if y == 5 and x in (3, 4):
                return C["skin_d"]          # нос — тень в один пиксель
            return skin
        # бока: волосы сверху и сзади, ухо
        back = (x >= w - 3) if face == "left" else (x <= 2)
        if y <= 2 or (back and y <= 5):
            return hair
        if y in (4, 5) and x == (w // 2):
            return C["skin_d"]
        return skin

    def shirt(face, x, y, w, h, collar=False, hem=False):
        c = j(face, x, y, C["shirt"], C["shirt_d"])
        if collar and face == "front" and y == 0 and 3 <= x <= 4:
            return C["skin"]
        if collar and face == "front" and y == 0:
            return C["shirt_l"]
        if hem and y >= h - 3 and face not in ("top", "bottom"):
            return j(face, x, y, C["pants"], C["pants_d"])
        if hem and face == "bottom":
            return C["pants_d"]
        if face == "front" and x == w // 2 and not collar and not (hem and y >= h - 3):
            return C["shirt_d"]             # шов-молния посередине
        return c

    def pants(face, x, y, w, h):
        return j(face, x, y, C["pants"], C["pants_d"])

    def shoe(face, x, y, w, h):
        return C["shoe"] if face == "bottom" else j(face, x, y, C["shoe"], C["shoe_l"])

    def skin_(face, x, y, w, h):
        return j(face, x, y, C["skin"], C["skin_d"])

    def sleeve(face, x, y, w, h):
        return j(face, x, y, C["shirt"], C["shirt_d"]) if face not in ("left", "right") else C["shirt_d"]
    return {"head": head, "torso_hi": lambda *a: shirt(*a, collar=True), "torso_lo": lambda *a: shirt(*a, hem=True),
            "pants": pants, "shoe": shoe, "skin": skin_, "sleeve": sleeve}[kind]


def build_blocky_hero(arm, old_rig: dict, look: dict | None = None) -> dict:
    """Герой из кубиков на скелете KayKit (в позе покоя, до масштаба S): прячет меши KayKit (и лицо, и рюкзак), ставит
    коробки на кости и своё лицо. → ``rig`` как у ``hero_shot.build_face_rig`` (eyes, brows, mouth, root)."""
    look = {**HERO_LOOK, **(look or {})}
    bpy.context.view_layer.update()
    # всё, что было на герое (меши KayKit, глаза/брови/рот, рюкзак) — не рисуется (дорожки им пишутся, но не видно)
    for ob in list(bpy.data.objects):
        p_ = ob
        while p_ is not None and p_ is not arm:
            p_ = p_.parent
        if p_ is arm and ob.type in ("MESH", "CURVE", "FONT"):
            ob.hide_render = True
    for ob in bpy.data.objects:
        if ob.name.startswith("pack_") or ob.name.startswith("strap"):
            ob.hide_render = True
    amw = arm.matrix_world.copy()

    def bone_head(name):
        return amw @ arm.data.bones[name].head_local

    seed = [1000]

    def part(name, bone, size_px, center, kind):
        seed[0] += 17
        dims = tuple(max(1, int(round(s))) for s in size_px)
        img, uvs = _atlas(f"hero_{name}", dims, _painter(kind, look, seed[0]), look)
        mat = tex_mat(f"hero_{name}", img, rough=0.9, spec=0.15)
        size = tuple(s * PX for s in size_px)
        ob = tbox(f"hero_{name}", size, (0, 0, 0), mat, uvs=uvs)
        ob.parent = arm
        ob.parent_type = "BONE"
        ob.parent_bone = bone
        bpy.context.view_layer.update()
        ob.matrix_world = Matrix.Translation(V(center))
        ob["vvf_blocky"] = 1
        return ob

    shoulder = bone_head("upperarm.l")
    sz = shoulder.z
    top = 1.24                                  # верх корпуса = низ головы (кость head)
    # голова 8×8×8 над шеей
    part("head", "head", (8, 8, 8), (0, 0, top + 4 * PX), "head")
    # корпус: низ на spine (бёдра → пояс), верх на chest (до плеч); верх чуть шире — без совпадающих граней
    part("torso_lo", "spine", (8, 4, 8.4), (0, 0, top - 4.6 * PX - 4.2 * PX), "torso_lo")
    part("torso_hi", "chest", (8.08, 4.08, 4.9), (0, 0, top - 2.45 * PX), "torso_hi")
    # руки: в позе покоя (T) вдоль ±x от плеча; коробка сдвинута «вверх» (+z) — опущенная рука висит снаружи корпуса
    arm_dz = 4 * PX + 2 * PX - abs(shoulder.x)      # центр опущенной руки: край корпуса (4 пикс.) + полруки (2 пикс.)
    for side, sx in (("l", 1.0), ("r", -1.0)):
        x0 = abs(shoulder.x) - (top - sz)          # плечевой конец: верх руки вровень с верхом корпуса
        x_elbow = abs(bone_head(f"lowerarm.{side}").x)
        x_hand = abs(bone_head(f"hand.{side}").x)
        L1 = (x_elbow + 0.02 - x0) / PX
        part(f"arm_up_{side}", f"upperarm.{side}", (L1, 4, 4), (sx * (x0 + L1 * PX / 2), 0, sz + arm_dz), "sleeve")
        L2 = (x_hand - 0.01 - x_elbow + 0.02) / PX
        part(f"arm_lo_{side}", f"lowerarm.{side}", (L2, 3.9, 3.9), (sx * (x_elbow - 0.02 + L2 * PX / 2), 0, sz + arm_dz),
             "sleeve")
        L3 = 2.2
        part(f"hand_{side}", f"hand.{side}", (L3, 3.7, 3.7), (sx * (x_hand - 0.01 + L3 * PX / 2), 0, sz + arm_dz), "skin")
    # ноги: 4×4 под корпусом (центр ±2 пикс.), бедро/голень/ступня — на свои кости
    for side, sx in (("l", 1.0), ("r", -1.0)):
        hip = bone_head(f"upperleg.{side}")
        knee = bone_head(f"lowerleg.{side}")
        ankle = bone_head(f"foot.{side}")
        cx = sx * 2.02 * PX
        z_top = top - 13 * PX + 0.02
        part(f"leg_up_{side}", f"upperleg.{side}", (4, 4, (z_top - knee.z + 0.02) / PX), (cx, 0, (z_top + knee.z - 0.02) / 2), "pants")
        part(f"leg_lo_{side}", f"lowerleg.{side}", (3.92, 3.92, (knee.z - ankle.z + 0.04) / PX), (cx, 0, (knee.z + ankle.z - 0.02) / 2),
             "pants")
        part(f"foot_{side}", f"foot.{side}", (3.85, 4.6, (ankle.z + 0.01) / PX), (cx, -0.6 * PX, (ankle.z + 0.01) / 2), "shoe")
        _ = hip
    # лицо: пустышка на кости головы, глаза 2×2 (белок снаружи, зрачок к носу), брови, рот
    head_rest = bone_head("head")
    root = bpy.data.objects.new("vox_face_root", None)
    kit.link(root)
    root.parent = arm
    root.parent_type = "BONE"
    root.parent_bone = "head"
    bpy.context.view_layer.update()
    root.matrix_world = Matrix.Translation(head_rest)
    bpy.context.view_layer.update()
    fz = -4 * PX - 0.003                             # перед лицом (вперёд — −y)
    white = flat_mat("hero_eye_white", look["eye"], rough=0.5)
    pupil = flat_mat("hero_eye", look["pupil"], rough=0.4)
    rig = {"root": root, "eyes": [], "brows": [], "head_rest": head_rest}
    z_eye = top + 8 * PX - 5 * PX                    # строки 4–5 сверху
    for sx in (-1.0, 1.0):
        cx = sx * 2 * PX
        me = bpy.data.meshes.new(f"vox_eye_{'l' if sx > 0 else 'r'}")
        bm = bmesh.new()
        for ux in (-0.5, 0.5):
            is_pupil = (ux * sx) < 0          # ближняя к носу половина — зрачок
            res = bmesh.ops.create_cube(bm, size=1.0, matrix=Matrix.Translation(V((ux * PX, 0, 0)))
                                        @ Matrix.Diagonal(V((PX, 0.006, 2 * PX, 1.0))))
            for f in {f for v in res["verts"] for f in v.link_faces}:
                f.material_index = 1 if is_pupil else 0
        bm.to_mesh(me)
        bm.free()
        me.materials.append(white)
        me.materials.append(pupil)
        eob = kit.link(bpy.data.objects.new(me.name, me))
        eob.parent = root
        eob.matrix_world = Matrix.Translation(V((cx, fz, z_eye)))
        rig["eyes"].append({"obj": eob, "base": eob.location.copy()})
    brow_mat = flat_mat("hero_brow", look["hair"], rough=0.8)
    for sx in (-1.0, 1.0):
        b = tbox(f"vox_brow_{'l' if sx > 0 else 'r'}", (2 * PX, 0.006, 0.6 * PX), (0, 0, 0), brow_mat)
        b.parent = root
        b.matrix_world = Matrix.Translation(V((sx * 2 * PX, fz - 0.001, z_eye + 1.55 * PX)))
        rig["brows"].append({"obj": b, "base": b.location.copy(), "rot": b.rotation_euler.copy()})
    mouth = tbox("vox_mouth", (2.0, 2.0, 2.0), (0, 0, 0), flat_mat("hero_mouth", look["mouth"], rough=0.7))
    mouth.parent = root
    mouth.matrix_world = Matrix.Translation(V((0.0, -4 * PX + 0.010, top + 1.6 * PX)))
    mouth.scale = (1e-4, 1e-4, 1e-4)
    rig["mouth"] = {"obj": mouth, "base": mouth.location.copy()}
    rig["eyes"].sort(key=lambda e: e["obj"].matrix_world.translation.x)
    _ = old_rig
    return rig


def wants_blocky(job: dict, used: set[str]) -> bool:
    """Герой из кубиков: явно (``hero: blocky``) или по умолчанию в кубическом мире."""
    if job.get("hero"):
        return str(job["hero"]) == "blocky"
    return any(B.loc_of(u).get("world") == "voxel" for u in used)
