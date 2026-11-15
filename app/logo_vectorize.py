"""Логотип → мазки для слоя ``logo_reveal`` (frontend/src/motion/layers/logoreveal.ts).

Картинка логотипа (PNG с альфой или JPG/PNG на однотонном фоне) превращается в осевые линии
мазков с толщиной: порог → скелет (утончение Чжана — Суня) → граф (концы, развилки) → обрезка
«усиков» от рваного края кисти → сборка мазков через развилки по самому прямому продолжению →
порядок (слева направо по умолчанию) и направление (сверху вниз / слева направо).

Брызги — мелкие куски, до которых не дотягивается ни один мазок: им назначается ближайшая точка
мазка, и движок «проявляет» их, когда кисть проходит мимо.

Результат — JSON для ``props.logo`` слоя (координаты в долях ширины картинки):

    {"v": 1, "aspect": h / w, "spread": 1.35, "pad": 0.004,
     "strokes": [{"p": [x0, y0, r0, x1, y1, r1, …], "g": 0, "closed": false}, …],
     "dots": [[x, y, r, stroke, u], …]}

Только numpy + OpenCV (Apache-2.0, уже в requirements); без scipy/skimage/potrace.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

FORMAT_VERSION = 1
# Сторона, к которой приводится картинка перед скелетом: точность ~0.2 % ширины, быстрый граф.
WORK_SIDE = 640
# Насколько шире радиуса мазка движок открывает картинку (props.logo.spread) и запас в долях ширины.
# Python считает покрытие той же формулой, что рисует движок (revealRadius в logoreveal.ts).
SPREAD = 1.35
PAD = 0.004
# Шаг точек мазка в px рабочей картинки.
STEP_PX = 4.0
ORDERS = ("ltr", "rtl", "ttb", "btt", "size")


# ---------------------------------------------------------------- маска


def load_rgba(path: str | Path) -> np.ndarray:
    """Картинка → RGBA uint8 (OpenCV читает BGR/BGRA)."""
    import cv2

    raw = np.fromfile(str(path), dtype=np.uint8)
    img = cv2.imdecode(raw, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError(f"не читается картинка: {path}")
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGRA)
    elif img.shape[2] == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
    if img.dtype != np.uint8:
        img = (img / 257).astype(np.uint8)
    return cv2.cvtColor(img, cv2.COLOR_BGRA2RGBA)


def ink_alpha(rgba: np.ndarray, threshold: int | None = None) -> np.ndarray:
    """Где «чернила» (0..255). Есть настоящая альфа — она и есть. Нет — фон по рамке картинки:
    тёмный фон → чернила светлые и наоборот; порог — Оцу (или заданный)."""
    import cv2

    alpha = rgba[..., 3]
    if int(alpha.min()) < 250:
        return alpha.copy()
    gray = cv2.cvtColor(rgba[..., :3], cv2.COLOR_RGB2GRAY)
    border = np.concatenate([gray[0], gray[-1], gray[:, 0], gray[:, -1]])
    if float(np.median(border)) > 127:
        gray = 255 - gray
    if threshold is None:
        threshold, _ = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    lo = max(0.0, float(threshold) - 24)
    hi = min(255.0, float(threshold) + 24)
    return np.clip((gray.astype(np.float32) - lo) / max(1.0, hi - lo) * 255, 0, 255).astype(np.uint8)


def rgba_with_alpha(path: str | Path, threshold: int | None = None) -> np.ndarray:
    """Логотип как RGBA с альфой чернил: JPG на чёрном/белом фоне становится вырезанным."""
    rgba = load_rgba(path)
    out = rgba.copy()
    out[..., 3] = ink_alpha(rgba, threshold)
    return out


# ---------------------------------------------------------------- скелет


def thin(mask: np.ndarray) -> np.ndarray:
    """Утончение Чжана — Суня до линий в 1 px (bool → bool), векторно по всему кадру."""
    img = np.pad(mask.astype(np.uint8), 1)
    while True:
        changed = False
        for step in (0, 1):
            p = img
            p2, p3, p4 = p[:-2, 1:-1], p[:-2, 2:], p[1:-1, 2:]
            p5, p6, p7 = p[2:, 2:], p[2:, 1:-1], p[2:, :-2]
            p8, p9 = p[1:-1, :-2], p[:-2, :-2]
            nb = [p2, p3, p4, p5, p6, p7, p8, p9]
            b = sum(n.astype(np.int16) for n in nb)
            seq = nb + [p2]
            a = sum(((seq[i] == 0) & (seq[i + 1] == 1)).astype(np.int16) for i in range(8))
            if step == 0:
                c1 = p2 * p4 * p6
                c2 = p4 * p6 * p8
            else:
                c1 = p2 * p4 * p8
                c2 = p2 * p6 * p8
            core = p[1:-1, 1:-1]
            kill = (core == 1) & (b >= 2) & (b <= 6) & (a == 1) & (c1 == 0) & (c2 == 0)
            if kill.any():
                core[kill] = 0
                changed = True
        if not changed:
            break
    return img[1:-1, 1:-1].astype(bool)


_NB = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


def _neighbors(sk: np.ndarray, y: int, x: int) -> list[tuple[int, int]]:
    h, w = sk.shape
    return [(y + dy, x + dx) for dy, dx in _NB if 0 <= y + dy < h and 0 <= x + dx < w and sk[y + dy, x + dx]]


def skeleton_graph(sk: np.ndarray) -> tuple[dict[int, list[tuple[int, int]]], list[dict]]:
    """Граф скелета: узлы (концы и развилки, соседние пиксели развилки — один узел) и рёбра —
    цепочки пикселей между ними. Кольца без узлов — рёбра с ``node = None`` на обоих концах."""
    ys, xs = np.nonzero(sk)
    degree = {}
    for y, x in zip(ys.tolist(), xs.tolist()):
        degree[(y, x)] = len(_neighbors(sk, y, x))
    special = {p for p, d in degree.items() if d != 2}
    node_of: dict[tuple[int, int], int] = {}
    nodes: dict[int, list[tuple[int, int]]] = {}
    for p in special:
        if p in node_of:
            continue
        nid = len(nodes)
        stack, members = [p], []
        node_of[p] = nid
        while stack:
            q = stack.pop()
            members.append(q)
            for n in _neighbors(sk, *q):
                if n in special and n not in node_of and degree[q] > 2 and degree[n] > 2:
                    node_of[n] = nid
                    stack.append(n)
        nodes[nid] = members
    edges: list[dict] = []
    seen: set[tuple[tuple[int, int], tuple[int, int]]] = set()

    def walk(start: tuple[int, int], first: tuple[int, int], nid: int | None) -> None:
        if (start, first) in seen:
            return
        path = [start, first]
        prev, cur = start, first
        seen.add((start, first))
        while cur not in node_of:
            nxt = [n for n in _neighbors(sk, *cur) if n != prev and n not in path[-3:]]
            if not nxt:
                break
            # Сначала соседи по стороне (4-связность) — меньше зигзагов.
            nxt.sort(key=lambda n: abs(n[0] - cur[0]) + abs(n[1] - cur[1]))
            prev, cur = cur, nxt[0]
            path.append(cur)
            if cur == path[0]:
                break
        end = node_of.get(cur)
        seen.add((cur, prev))
        edges.append({"a": nid, "b": end, "pts": path})

    for nid, members in nodes.items():
        member_set = set(members)
        for m in members:
            for n in _neighbors(sk, *m):
                if n in member_set:
                    continue
                if n in node_of:
                    # Две развилки вплотную: ребро из двух точек.
                    if node_of[n] != nid and (m, n) not in seen and (n, m) not in seen:
                        seen.add((m, n))
                        edges.append({"a": nid, "b": node_of[n], "pts": [m, n]})
                    continue
                walk(m, n, nid)
    # Кольца без узлов (буква «o» без хвостов).
    visited = {p for e in edges for p in e["pts"]}
    for p in degree:
        if p in visited or p in node_of:
            continue
        path = [p]
        prev, cur = None, p
        while True:
            nxt = [n for n in _neighbors(sk, *cur) if n != prev and n not in path[-3:]]
            if not nxt:
                break
            nxt.sort(key=lambda n: abs(n[0] - cur[0]) + abs(n[1] - cur[1]))
            prev, cur = cur, nxt[0]
            if cur == p or cur in visited:
                break
            path.append(cur)
        visited.update(path)
        if len(path) > 2:
            edges.append({"a": None, "b": None, "pts": path, "loop": True})
    return nodes, edges


def _length(pts: Sequence[tuple[float, float]]) -> float:
    return float(sum(math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]) for i in range(1, len(pts))))


def _degrees(edges: list[dict]) -> dict[int, int]:
    deg: dict[int, int] = {}
    for e in edges:
        for k in ("a", "b"):
            if e[k] is not None:
                deg[e[k]] = deg.get(e[k], 0) + 1
    return deg


def merge_through(edges: list[dict]) -> list[dict]:
    """Склеить рёбра через узлы степени 2 (остались после среза усика): кусок руки — снова одно ребро."""
    while True:
        deg = _degrees(edges)
        node = next((n for n, d in deg.items() if d == 2 and sum(1 for e in edges if e["a"] == n and e["b"] == n) == 0), None)
        if node is None:
            return edges
        pair = [e for e in edges if e["a"] == node or e["b"] == node]
        e1, e2 = pair
        p1 = e1["pts"] if e1["b"] == node else e1["pts"][::-1]
        p2 = e2["pts"] if e2["a"] == node else e2["pts"][::-1]
        a = e1["a"] if e1["b"] == node else e1["b"]
        b = e2["b"] if e2["a"] == node else e2["a"]
        merged = {"a": a, "b": b, "pts": list(p1) + list(p2[1:])}
        if a is None and b is None:
            merged["loop"] = True
        edges = [e for e in edges if e is not e1 and e is not e2] + [merged]


def prune(nodes: dict, edges: list[dict], dist: np.ndarray, min_len: float, rounds: int = 40) -> list[dict]:
    """Срезать «усики»: концевые рёбра короче толщины мазка у развилки (рваный край кисти даёт их
    десятками). За проход у каждой развилки срезается только самый короткий усик, потом рёбра
    склеиваются через ставшие проходными узлы — иначе срез съедал бы настоящие концы мазков,
    разбитые на куски соседними усиками."""
    edges = merge_through(edges)
    for _ in range(rounds):
        deg = _degrees(edges)
        worst: dict[int, tuple[float, int]] = {}
        for i, e in enumerate(edges):
            if e.get("loop") or e["a"] == e["b"]:
                continue
            a_end = e["a"] is None or deg.get(e["a"], 0) == 1
            b_end = e["b"] is None or deg.get(e["b"], 0) == 1
            if a_end == b_end:
                continue
            node = e["b"] if a_end else e["a"]
            if deg.get(node, 0) < 3:
                continue
            junction = e["pts"][-1] if a_end else e["pts"][0]
            length = _length(e["pts"])
            if length < max(min_len, 1.6 * float(dist[junction]) + 3) and (node not in worst or length < worst[node][0]):
                worst[node] = (length, i)
        if not worst:
            break
        drop = {i for _, i in worst.values()}
        edges = merge_through([e for i, e in enumerate(edges) if i not in drop])
    return edges


def _direction(pts: list[tuple[int, int]], at_start: bool, span: int = 10) -> tuple[float, float]:
    """Направление ребра от узла внутрь (единичный вектор, (dy, dx))."""
    if at_start:
        a, b = pts[0], pts[min(len(pts) - 1, span)]
    else:
        a, b = pts[-1], pts[max(0, len(pts) - 1 - span)]
    dy, dx = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dy, dx) or 1.0
    return dy / n, dx / n


def chain_strokes(edges: list[dict], max_turn_deg: float = 55.0) -> list[list[tuple[int, int]]]:
    """Склеить рёбра в мазки: в каждой развилке пары рёбер, которые продолжают друг друга прямее
    всего (поворот меньше ``max_turn_deg``), идут одним мазком — как рука ведёт кисть насквозь."""
    ends: dict[int, list[tuple[int, bool]]] = {}
    for i, e in enumerate(edges):
        if e.get("loop"):
            continue
        if e["a"] is not None:
            ends.setdefault(e["a"], []).append((i, True))
        if e["b"] is not None:
            ends.setdefault(e["b"], []).append((i, False))
    link: dict[tuple[int, bool], tuple[int, bool]] = {}
    cos_lim = math.cos(math.radians(180 - max_turn_deg))
    for _node, inc in ends.items():
        cands = []
        for x in range(len(inc)):
            for y in range(x + 1, len(inc)):
                (i, si), (j, sj) = inc[x], inc[y]
                if i == j:
                    continue
                di = _direction(edges[i]["pts"], si)
                dj = _direction(edges[j]["pts"], sj)
                cos = di[0] * dj[0] + di[1] * dj[1]  # −1 — прямо насквозь
                if cos <= cos_lim:
                    cands.append((cos, inc[x], inc[y]))
        cands.sort(key=lambda c: c[0])
        used: set[tuple[int, bool]] = set()
        for _cos, p, q in cands:
            if p in used or q in used:
                continue
            used.update((p, q))
            link[p] = q
            link[q] = p
    strokes: list[list[tuple[int, int]]] = []
    done: set[int] = set()
    for i, e in enumerate(edges):
        if i in done:
            continue
        if e.get("loop"):
            done.add(i)
            strokes.append(list(e["pts"]) + [e["pts"][0]])
            continue
        # Идём к началу цепочки: пока у конца «a» есть продолжение.
        cur, at_start = i, True
        guard = 0
        while (cur, at_start) in link and guard < len(edges):
            nxt, nxt_start = link[(cur, at_start)]
            if nxt == i:
                break
            cur, at_start = nxt, not nxt_start
            guard += 1
        # Теперь (cur, at_start) — свободный конец; собираем вперёд.
        pts: list[tuple[int, int]] = []
        start_cur, start_end = cur, at_start
        guard = 0
        while cur not in done and guard <= len(edges):
            done.add(cur)
            seg = edges[cur]["pts"] if at_start else edges[cur]["pts"][::-1]
            pts.extend(seg if not pts else seg[1:])
            out_end = (cur, not at_start)
            if out_end not in link:
                break
            cur, nxt_start = link[out_end]
            at_start = nxt_start
            guard += 1
        _ = (start_cur, start_end)
        if len(pts) >= 2:
            strokes.append(pts)
    return strokes


# ---------------------------------------------------------------- точки мазка


def _smooth(pts: np.ndarray, closed: bool, k: int = 2) -> np.ndarray:
    if len(pts) < 2 * k + 2:
        return pts
    out = pts.copy()
    for i in range(len(pts)):
        idx = [(i + d) % len(pts) if closed else min(len(pts) - 1, max(0, i + d)) for d in range(-k, k + 1)]
        out[i] = pts[idx].mean(axis=0)
    if not closed:
        out[0], out[-1] = pts[0], pts[-1]
    return out


def resample(pts: np.ndarray, step: float) -> np.ndarray:
    """Равномерно по длине (точки (x, y))."""
    if len(pts) < 2:
        return pts
    seg = np.hypot(*(np.diff(pts, axis=0).T))
    s = np.concatenate([[0], np.cumsum(seg)])
    total = s[-1]
    if total <= 0:
        return pts[:1]
    n = max(2, int(math.ceil(total / step)) + 1)
    t = np.linspace(0, total, n)
    return np.stack([np.interp(t, s, pts[:, 0]), np.interp(t, s, pts[:, 1])], axis=1)


def orient(pts: np.ndarray, closed: bool) -> np.ndarray:
    """Направление письма: кольцо — с верхней точки против часовой (как пишется «o»), открытый
    мазок — сверху вниз, а пологий (шире, чем выше) — слева направо."""
    if closed:
        body = pts[:-1] if np.allclose(pts[0], pts[-1]) else pts
        top = int(np.argmin(body[:, 1] + body[:, 0] * 1e-3))
        body = np.concatenate([body[top:], body[:top]])
        # Площадь со знаком в экранных координатах (y вниз): > 0 — по часовой.
        x, y = body[:, 0], body[:, 1]
        area = 0.5 * float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))
        if area > 0:
            body = np.concatenate([body[:1], body[1:][::-1]])
        return np.concatenate([body, body[:1]])
    a, b = pts[0], pts[-1]
    if abs(b[0] - a[0]) > abs(b[1] - a[1]) * 1.2:
        return pts if a[0] <= b[0] else pts[::-1]
    return pts if a[1] <= b[1] else pts[::-1]


def reveal_radius(r: np.ndarray | float, spread: float = SPREAD, pad: float = PAD) -> np.ndarray | float:
    """Радиус, на который движок открывает картинку вокруг осевой (доли ширины) — revealRadius в TS."""
    return r * spread + pad


def coverage_mask(strokes: list[dict], shape: tuple[int, int], scale: float, spread: float = SPREAD,
                  pad: float = PAD) -> np.ndarray:
    """Что откроют мазки целиком (bool, рабочая картинка): капсулы вдоль осевых."""
    import cv2

    canvas = np.zeros(shape, np.uint8)
    for s in strokes:
        p = np.asarray(s["p"], np.float64).reshape(-1, 3)
        for i in range(len(p)):
            x, y, r = p[i] * scale
            rr = reveal_radius(p[i][2], spread, pad) * scale
            cv2.circle(canvas, (int(round(x)), int(round(y))), max(1, int(round(rr))), 255, -1)
            if i:
                x0, y0, _ = p[i - 1] * scale
                thick = max(1, int(round(2 * rr)))
                cv2.line(canvas, (int(round(x0)), int(round(y0))), (int(round(x)), int(round(y))), 255, thick)
    return canvas > 0


# ---------------------------------------------------------------- порядок


def order_strokes(strokes: list[dict], order: str | Sequence[int] = "ltr",
                  first: Sequence[Sequence[float]] | None = None) -> list[dict]:
    """Порядок мазков: ``ltr`` (по умолчанию) — по левому краю, ``rtl``, ``ttb``, ``btt``, ``size``
    (длинные раньше) или явный список номеров (остальные — следом слева направо). ``first`` —
    рамки [x0, y0, x1, y1] в долях ширины: мазки, у которых больше половины точек внутри, идут первыми
    (рог над буквами рисуется раньше букв)."""

    def pts(s: dict) -> np.ndarray:
        return np.asarray(s["p"], np.float64).reshape(-1, 3)

    def key_of(mode: str):
        if mode == "rtl":
            return lambda s: -float(pts(s)[:, 0].max())
        if mode == "ttb":
            return lambda s: float(pts(s)[:, 1].min())
        if mode == "btt":
            return lambda s: -float(pts(s)[:, 1].max())
        if mode == "size":
            return lambda s: -_length(pts(s)[:, :2].tolist())
        return lambda s: float(pts(s)[:, 0].min())

    items = list(strokes)
    if not isinstance(order, str):
        idx = [int(i) for i in order if 0 <= int(i) < len(items)]
        seen: set[int] = set()
        chosen = [items[i] for i in idx if not (i in seen or seen.add(i))]
        rest = sorted((s for i, s in enumerate(items) if i not in seen), key=key_of("ltr"))
        items = chosen + rest
    else:
        if order not in ORDERS:
            raise ValueError(f"неизвестный порядок {order!r}: {', '.join(ORDERS)} или список номеров")
        items.sort(key=key_of(order))
    if first:
        def in_first(s: dict) -> bool:
            p = pts(s)
            for box in first:
                x0, y0, x1, y1 = box
                inside = (p[:, 0] >= x0) & (p[:, 0] <= x1) & (p[:, 1] >= y0) & (p[:, 1] <= y1)
                if inside.mean() > 0.5:
                    return True
            return False

        items = [s for s in items if in_first(s)] + [s for s in items if not in_first(s)]
    return items


# ---------------------------------------------------------------- всё вместе


def vectorize(
    image: str | Path | np.ndarray,
    *,
    order: str | Sequence[int] = "ltr",
    first: Sequence[Sequence[float]] | None = None,
    threshold: int | None = None,
    min_stroke: float = 0.012,
    spread: float = SPREAD,
    pad: float = PAD,
) -> dict[str, Any]:
    """Мазки логотипа для ``props.logo``. ``image`` — путь или RGBA-массив.

    ``min_stroke`` — мазки короче (доля ширины) отбрасываются как мусор скелета."""
    import cv2

    rgba = load_rgba(image) if not isinstance(image, np.ndarray) else image
    alpha = ink_alpha(rgba, threshold)
    h0, w0 = alpha.shape
    k = WORK_SIDE / max(h0, w0)
    work = cv2.resize(alpha, (max(1, round(w0 * k)), max(1, round(h0 * k))), interpolation=cv2.INTER_AREA) if k < 1 else alpha
    k = min(k, 1.0)
    h, w = work.shape
    mask = work > 110
    # Дырочки и щербинки края дают ложные петли скелета — закрываем мелкие.
    mask = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8)) > 0
    dist = cv2.distanceTransform(mask.astype(np.uint8), cv2.DIST_L2, 5)
    n_lab, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    # Крупные куски — буквы (скелет), мелкие — брызги (точки).
    big_area = max(30.0, (w * 0.02) ** 2)
    letters = np.zeros_like(mask)
    for i in range(1, n_lab):
        if stats[i, cv2.CC_STAT_AREA] >= big_area:
            letters |= labels == i
    sk = thin(letters)
    nodes, edges = skeleton_graph(sk)
    edges = prune(nodes, edges, dist, min_len=w * 0.01)
    chains = chain_strokes(edges)
    strokes: list[dict] = []
    for chain in chains:
        arr = np.array([(x, y) for y, x in chain], np.float64)
        closed = len(arr) > 8 and math.hypot(*(arr[0] - arr[-1])) <= 2.0
        arr = resample(_smooth(arr, closed), STEP_PX)
        if _length(arr.tolist()) < min_stroke * w:
            continue
        arr = orient(arr, closed)
        ix = np.clip(np.round(arr[:, 0]).astype(int), 0, w - 1)
        iy = np.clip(np.round(arr[:, 1]).astype(int), 0, h - 1)
        r = dist[iy, ix].astype(np.float64)
        # Радиус сглажен: зубец края не должен «дышать» шириной открытия.
        if len(r) > 4:
            r = np.convolve(np.pad(r, 2, mode="edge"), np.ones(5) / 5, mode="valid")
        g = int(labels[iy[len(iy) // 2], ix[len(ix) // 2]])
        p = np.stack([arr[:, 0] / w, arr[:, 1] / w, np.maximum(r, 1.0) / w], axis=1)
        strokes.append({"p": p, "g": g, "closed": bool(closed)})
    strokes = order_strokes([{**s, "p": s["p"].ravel().tolist()} for s in strokes], order, first)
    # Группы по порядку появления (0, 1, 2…) — паузы между буквами в движке.
    remap: dict[int, int] = {}
    for s in strokes:
        s["g"] = remap.setdefault(s["g"], len(remap))
    covered = coverage_mask(strokes, (h, w), w, spread, pad)
    dots = _dots(mask, labels, stats, covered, strokes, w)
    ink = mask.sum()
    out_strokes = [
        {"p": [round(v, 4) for v in s["p"]], "g": s["g"], **({"closed": True} if s["closed"] else {})} for s in strokes
    ]
    reach = covered | _dot_mask(dots, (h, w), w)
    return {
        "v": FORMAT_VERSION,
        "aspect": round(h0 / w0, 5),
        "spread": spread,
        "pad": pad,
        "strokes": out_strokes,
        "dots": dots,
        "coverage": round(float((mask & reach).sum()) / max(1, int(ink)), 4),
    }


def _dots(mask: np.ndarray, labels: np.ndarray, stats: np.ndarray, covered: np.ndarray, strokes: list[dict],
          w: int) -> list[list[float]]:
    """Брызги: куски чернил, которых мазки не касаются. Каждому — ближайшая точка мазка (номер, доля пути)."""
    import cv2

    pts_all = []
    for si, s in enumerate(strokes):
        p = np.asarray(s["p"], np.float64).reshape(-1, 3)
        seg = np.hypot(*(np.diff(p[:, :2], axis=0).T)) if len(p) > 1 else np.zeros(0)
        cum = np.concatenate([[0], np.cumsum(seg)])
        total = cum[-1] or 1.0
        for i in range(len(p)):
            pts_all.append((p[i, 0] * w, p[i, 1] * w, si, cum[i] / total))
    if not pts_all:
        return []
    arr = np.array([(x, y) for x, y, _, _ in pts_all])
    out = []
    for i in range(1, labels.max() + 1):
        comp = labels == i
        if (comp & covered).any() and stats[i, cv2.CC_STAT_AREA] > 0:
            # Кусок, который мазки задели, но не целиком, движок дорисует проявлением в конце.
            continue
        ys, xs = np.nonzero(comp)
        cx, cy = float(xs.mean()), float(ys.mean())
        r = max(1.0, math.sqrt(len(xs) / math.pi) * 1.25 + 1)
        j = int(np.argmin((arr[:, 0] - cx) ** 2 + (arr[:, 1] - cy) ** 2))
        _, _, si, u = pts_all[j]
        out.append([round(cx / w, 4), round(cy / w, 4), round(r / w, 4), int(si), round(float(u), 3)])
    return out[:400]


def _dot_mask(dots: list[list[float]], shape: tuple[int, int], w: int) -> np.ndarray:
    import cv2

    canvas = np.zeros(shape, np.uint8)
    for x, y, r, _si, _u in dots:
        cv2.circle(canvas, (int(round(x * w)), int(round(y * w))), max(1, int(round(r * w))), 255, -1)
    return canvas > 0


# ---------------------------------------------------------------- поле схемы слоя

MAX_STROKES = 400
MAX_POINTS = 12000
MAX_DOTS = 400


def _finite(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) and not isinstance(v, bool) else None


class LogoVectorSpec:
    """``props.logo`` слоя logo_reveal (схема app/motion_schema.py): мазки из ``vectorize``.

    Проверяется форма, лишнее отбрасывается, числа округляются; пределы — чтобы слой не раздул клип."""

    def norm(self, value: Any, rep: Any, where: str) -> dict | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            rep(where, "мазки логотипа — объект {aspect, strokes: [{p: [x, y, r, …]}]} (app/logo_vectorize.py)")
            return None
        aspect = _finite(value.get("aspect"))
        if aspect is None or not 0.01 <= aspect <= 100:
            rep(f"{where}.aspect", "нужна пропорция картинки h/w (0.01..100)")
            return None
        out: dict[str, Any] = {"v": FORMAT_VERSION, "aspect": round(aspect, 5)}
        for key, lo, hi in (("spread", 0.5, 4.0), ("pad", 0.0, 0.1)):
            f = _finite(value.get(key))
            if f is not None:
                out[key] = round(min(hi, max(lo, f)), 4)
        strokes = value.get("strokes") if isinstance(value.get("strokes"), list) else []
        if len(strokes) > MAX_STROKES:
            rep(f"{where}.strokes", f"мазков {len(strokes)}, предел {MAX_STROKES} — лишние отброшены")
        clean: list[dict] = []
        budget = MAX_POINTS
        for i, s in enumerate(strokes[:MAX_STROKES]):
            p = s.get("p") if isinstance(s, dict) else None
            if not isinstance(p, list) or len(p) < 3:
                rep(f"{where}.strokes[{i}]", "мазок — {p: [x, y, r, …]} (не меньше одной точки)")
                continue
            nums = [_finite(v) for v in p[: (len(p) // 3) * 3]]
            if any(v is None for v in nums):
                rep(f"{where}.strokes[{i}].p", "в точках мазка не числа — мазок отброшен")
                continue
            n = len(nums) // 3
            if n > budget:
                rep(f"{where}.strokes", f"точек больше {MAX_POINTS} — хвост отброшен")
                break
            budget -= n
            item: dict[str, Any] = {"p": [round(v, 4) for v in nums]}  # type: ignore[arg-type]
            g = _finite(s.get("g"))
            if g is not None:
                item["g"] = int(g)
            if s.get("closed") is True:
                item["closed"] = True
            clean.append(item)
        out["strokes"] = clean
        dots = value.get("dots") if isinstance(value.get("dots"), list) else []
        out_dots = []
        for d in dots[:MAX_DOTS]:
            if isinstance(d, list) and len(d) == 5 and all(_finite(v) is not None for v in d):
                x, y, r, si, u = (float(v) for v in d)
                if 0 <= si < len(clean):
                    out_dots.append([round(x, 4), round(y, 4), round(r, 4), int(si), round(min(1.0, max(0.0, u)), 3)])
        if out_dots:
            out["dots"] = out_dots
        return out

    def fill(self) -> None:
        return None

    def describe(self) -> dict:
        return {"type": "logo_vector", "default": None, "nullable": True,
                "doc": "мазки логотипа из app/logo_vectorize.vectorize: {aspect, strokes: [{p: [x, y, r, …], g}], dots}; "
                       "нет — рендер извлечёт их из картинки сам"}


def cached_vectorize(path: str | Path, cache_dir: str | Path, **kw: Any) -> dict[str, Any]:
    """vectorize с кэшем по содержимому файла и параметрам (рендер зовёт его на каждый прогон)."""
    data = Path(path).read_bytes()
    key = hashlib.sha256(data + json.dumps(kw, sort_keys=True, default=str).encode()).hexdigest()[:24]
    cache = Path(cache_dir) / f"logo-{key}.json"
    if cache.exists():
        try:
            return json.loads(cache.read_text("utf-8"))
        except (OSError, ValueError):
            pass
    out = vectorize(path, **kw)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(out, separators=(",", ":")), "utf-8")
    return out
