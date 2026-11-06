"""Вырезы: сдвиг слоёв, камеры и вставок (из ``app/cuts.py``)."""

from __future__ import annotations

from typing import Any

from app.cuts_map import _finite, CutMap

def _remap_keys(keys: dict, start: float, new_start: float, mapper: CutMap) -> dict:
    """Ключи слоя (время от начала слоя) у слоя, через который прошёл вырез."""
    out: dict = {}
    for prop, points in (keys or {}).items():
        moved: dict[float, dict] = {}
        for point in points or []:
            t = round(max(0.0, mapper.clamp(start + float(point["t"])) - new_start), 3)
            moved[t] = {**point, "t": t}
        if moved:
            out[prop] = [moved[t] for t in sorted(moved)]
    return out


TRACK_GRID_SEC = 0.05


def _densify_tracking_keys(layer: dict, mapper: Any) -> dict:
    """Ключи трека (app/tracking.py) — опоры Дугласа–Пекера, между ними бывает несколько секунд.
    Перевод времени с разрывом (вырез, стоп-кадр, правка куска) между двумя опорами исказил бы
    траекторию: опоры сошлись бы через склейку. Поэтому у краёв каждого разрыва ставятся опоры
    с интерполированным значением — после перевода слой стоит на той же точке кадра."""
    from app.promo_lint import key_value

    keys = layer.get("keys") or {}
    props = [p for p in (layer.get("tracking") or {}).get("props") or [] if keys.get(p)]
    start, end = float(layer["start"]), float(layer["end"])
    if not props or end <= start:
        return keys
    count = int((end - start) / TRACK_GRID_SEC)
    grid = [start + i * TRACK_GRID_SEC for i in range(count + 1)]
    if end - grid[-1] > 1e-6:
        grid.append(end)
    mapped = [mapper.clamp(t) for t in grid]
    bad = [abs((mapped[i + 1] - mapped[i]) - (grid[i + 1] - grid[i])) > 1e-4 for i in range(len(grid) - 1)]
    marks: set[float] = set()
    i = 0
    while i < len(bad):
        if not bad[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(bad) and bad[j + 1]:
            j += 1
        # Опоры до разрыва (одна строго до него) и после (одна строго после).
        for k in (i - 1, i, j + 1, j + 2):
            if 0 <= k < len(grid):
                marks.add(round(grid[k] - start, 3))
        i = j + 1
    if not marks:
        return keys
    out = dict(keys)
    for prop in props:
        points = sorted(keys[prop], key=lambda p: float(p["t"]))
        have = {round(float(p["t"]), 3) for p in points}
        extra = []
        for t in sorted(marks - have):
            before = [p for p in points if float(p["t"]) <= t]
            point = {"t": t, "v": round(key_value(points, t, 0.0), 4)}
            if before and before[-1].get("ease"):
                point["ease"] = before[-1]["ease"]
            extra.append(point)
        out[prop] = sorted(points + extra, key=lambda p: float(p["t"]))
    return out


PINNED_START_SEC = 0.05


def remap_layers(layers: list[dict], mapper: CutMap, min_len: float = 0.1) -> list[dict]:
    """Моушн-слои: start/end сдвигаются; слой целиком в вырезе — пропадает.

    Ключи слоя считаются от его начала — их трогаем, только если вырез прошёл
    через сам слой (или срезал его начало).
    """
    if not mapper:
        return layers
    out: list[dict] = []
    for layer in layers or []:
        start, end = float(layer["start"]), float(layer["end"])
        got = mapper.range(start, end, min_len)
        if start <= PINNED_START_SEC and (got is None or got[1] - got[0] < (end - start) / 2):
            # Титр с первой секунды (хук) — часть начала клипа, а не речи под ним:
            # вырезали начало — хук остаётся в начале той же длины.
            out.append({**layer, "start": 0.0, "end": round(end - start, 3)})
            continue
        if got is None:
            continue
        new_start, new_end = round(got[0], 3), round(got[1], 3)
        fresh = {**layer, "start": new_start, "end": new_end}
        straddles = (end - start) - (new_end - new_start) > 1e-6
        # Слой с трекингом (app/tracking.py): ключи привязаны к кадру, а не к началу слоя — едут
        # вместе с ним через любой перевод времени (стоп-кадр-вставка растягивает слой, а не режет).
        if layer.get("tracking") and layer.get("keys"):
            fresh["keys"] = _remap_keys(_densify_tracking_keys(layer, mapper), start, new_start, mapper)
        elif straddles and layer.get("keys"):
            fresh["keys"] = _remap_keys(layer["keys"], start, new_start, mapper)
        half = (new_end - new_start) / 2
        for side in ("in", "out"):
            anim = layer.get(side)
            if isinstance(anim, dict) and anim.get("type") != "none" and float(anim.get("duration") or 0) > half:
                fresh[side] = {**anim, "duration": round(max(0.05, half), 3)}
        out.append(fresh)
    return out


def remap_camera(camera: list[dict], mapper: CutMap) -> list[dict]:
    """Ключи камеры: время сдвигается; ключ в вырезе прижимается к склейке (последний побеждает)."""
    if not mapper:
        return camera
    by_t: dict[float, dict] = {}
    for key in camera or []:
        t = round(mapper.clamp(float(key["t"])), 3)
        by_t[t] = {**key, "t": t}
    return [by_t[t] for t in sorted(by_t)]


def remap_inserts(inserts: list[dict], mapper: CutMap) -> list[dict]:
    """Вставки: ``at`` сдвигается; вставка, начавшаяся в вырезе, — с точки склейки.

    Длительность тоже: вырез внутри окна вставки укорачивает её, иначе мем
    вылезает дальше задуманного и налезает на следующий титр."""
    if not mapper:
        return inserts
    out = []
    for ins in inserts or []:
        start = float(ins["at"])
        end = start + float(ins.get("duration") or 0)
        a, b = mapper.clamp(start), mapper.clamp(end)
        fresh = {**ins, "at": round(a, 2)}
        if ins.get("duration") is not None:
            fresh["duration"] = round(max(0.3, b - a), 2)
        out.append(fresh)
    return sorted(out, key=lambda ins: ins["at"])


def remap_points(items: list[dict], mapper: CutMap, key: str = "t") -> list[dict]:
    """Точечные события (сообщения чата): в вырезе — пропадают, после — сдвигаются."""
    out = []
    for item in items or []:
        t = _finite(item.get(key))
        if t is None:
            continue
        mapped = mapper(t)
        if mapped is not None:
            out.append({**item, key: round(mapped, 3)})
    return out


def remap_caption_words(words: list[dict], mapper: CutMap) -> list[dict]:
    """``comp.words`` ({t0, t1, w}) → после вырезов; вырезанные слова выпадают."""
    out = []
    for word in words or []:
        t0, t1 = float(word["t0"]), float(word["t1"])
        if mapper((t0 + t1) / 2) is None:
            continue  # середина слова вырезана — в ролике его не слышно
        a, b = mapper.clamp(t0), mapper.clamp(t1)
        out.append({**word, "t0": round(a, 3), "t1": round(max(b, a + 0.05), 3)})
    return out


def remap_motion(motion: dict | None, mapper: CutMap) -> dict | None:
    """Моушн клипа (слои, камера, а если есть — слова и чат) → время после вырезов."""
    if not motion or not mapper:
        return motion
    fresh = {
        **motion,
        "layers": remap_layers(motion.get("layers") or [], mapper),
        "camera": remap_camera(motion.get("camera") or [], mapper),
    }
    if motion.get("words"):
        fresh["words"] = remap_caption_words(motion["words"], mapper)
    if motion.get("chat"):
        fresh["chat"] = remap_points(motion["chat"], mapper)
    return fresh
