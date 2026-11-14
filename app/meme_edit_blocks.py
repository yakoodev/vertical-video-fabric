"""Мем-эдит: блок (один бой) → файл клипа сервиса (vvf.clip/1) одного формата.

Блок из app/meme_edit_plan.py (куски, мемы, слова, переходы, эффекты) превращается в обычный клип: куски исходника,
вставки мемов (``render.inserts`` full, только удар мема — ``parts``, app/meme_trim.py), слои (субтитры двух голосов,
жёлтые слова-цитаты, эффекты переходов и их звуки из пака freesound, синий негатив на победе), камера (зум на
плашках и переходах), глушение звука под мемами и мата тиммейтов, шумодав микрофона. Музыки в блоке нет — одна
подложка на весь ролик смешивается при склейке (app/meme_edit_concat.py). Как в пилоте 2 (build2.py), только сервисом.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

NEGATE_COLOR = "#6fa8ff"
BASS_SFX = "sfx:freesound/bass-drop.ogg"
SFX = {"zoompunch": "impact-low.ogg", "glitch": "glitch-2.ogg", "whip": "whip.ogg", "rgb_split": "whoosh-3.ogg",
       "shake": "punch.ogg", "flash": "hit-transition.ogg"}
OWNER_COLOR, TEAM_COLOR = "#FFFFFF", "#4FC3F7"
TAG_MEME, TAG_TEAM = "meme_edit:meme", "meme_edit:team"
TITLES = {"16:9": "горизонталь", "9:16": "вертикаль"}


def captions(speakers: list[str], aspect: str, owner: bool, total: float) -> dict:
    """Субтитры одного голоса: игрок — белые крупные, тиммейты — голубые мельче (пилоты 1–2)."""
    v = aspect != "16:9"
    size = (80 if v else 68) if owner else (58 if v else 50)
    y = (0.62 if v else 0.86) if owner else (0.46 if v else 0.64)
    color = OWNER_COLOR if owner else TEAM_COLOR
    return {"type": "captions", "name": "Субтитры: игрок" if owner else "Субтитры: тиммейты", "start": 0.0,
            "end": round(total, 3), "x": 0.5, "y": y, "track": 4,
            "props": {"style": "hormozi", "color": color, "activeColor": color, "wordScale": 1.08, "size": size,
                      "maxWords": 4, "maxLines": 2, "uppercase": True,
                      "stroke": {"color": "#000000", "width": 10 if owner else 8},
                      "speakers": speakers, "maxWidth": 900 if v else 1500}}


def yellow_layer(text: str, start: float, end: float, aspect: str, index: int) -> dict:
    from app.meme_edit_text import word_layer

    layer = word_layer(text, start, end, aspect, index=index)
    layer.update({"x": 0.5, "y": 0.29 if aspect != "16:9" else 0.30})  # над прицелом, не на субтитрах и не на HUD
    return layer


def _fx(name: str, kind: str, a: float, b: float, total: float, intensity: float = 1.0) -> dict:
    return {"type": "fx", "name": f"Переход: {name}", "start": round(max(0.0, a), 3), "end": round(min(total, b), 3),
            "track": 1, "props": {"kind": kind, "intensity": intensity},
            "in": {"type": "fade", "duration": 0.05}, "out": {"type": "fade", "duration": 0.08}}


def transition_layers(block: dict) -> list[dict]:
    """Хайповые переходы в долю: эффект кадра 0.2–0.3 с + настоящий звук из пака freesound на каждом."""
    out: list[dict] = []
    total = block["total"]
    for tr in block.get("transitions") or []:
        t, kind, join = tr["t"], tr["kind"], tr["where"] == "join"
        a = 0.0 if join else max(0.0, t - 0.1)
        if kind == "glitch":
            out += [_fx("глитч", "tear", a, t + 0.25, total), _fx("глитч", "datamosh_lite", a, t + 0.22, total, 0.8)]
        elif kind == "rgb_split":
            out.append(_fx("RGB-сдвиг", "rgb_split", a, t + 0.28, total))
        elif kind == "whip":
            out.append(_fx("вжух", "chromatic", a, t + 0.25, total, 0.9))
        elif kind == "shake":
            out.append(_fx("тряска", "chromatic", t, t + 0.3, total, 0.6))
        elif kind == "zoompunch":
            out.append(_fx("зум-панч", "chromatic", t, t + 0.2, total, 0.5))
        at = t if join else max(0.0, t - 0.12)
        out.append({"type": "audio", "name": f"Звук перехода: {kind}", "start": round(at, 3),
                    "end": round(min(total, at + 1.2), 3),
                    "props": {"asset_id": f"sfx:freesound/{SFX[kind]}", "volume": 0.22 if tr.get("speech_near") else 0.35,
                              "role": "sfx"}})
    return [layer for layer in out if layer["end"] - layer["start"] >= 0.05]


def _transition_keys(block: dict) -> tuple[list[dict], list[tuple[float, float]]]:
    keys: list[dict] = []
    spans: list[tuple[float, float]] = []
    for tr in block.get("transitions") or []:
        t, kind, join = tr["t"], tr["kind"], tr["where"] == "join"
        if kind == "zoompunch":
            if not join:
                keys += [{"t": round(t - 0.16, 3), "zoom": 1.0, "x": 0.5, "y": 0.5, "ease": "inCubic"},
                         {"t": round(t - 0.034, 3), "zoom": 1.12, "x": 0.5, "y": 0.5, "ease": "step"}]
            keys += [{"t": round(t, 3), "zoom": 1.3, "x": 0.5, "y": 0.5, "ease": "outCubic"},
                     {"t": round(t + 0.3, 3), "zoom": 1.0, "x": 0.5, "y": 0.5, "ease": "linear"}]
        elif kind == "whip":
            if not join:
                keys += [{"t": round(t - 0.16, 3), "zoom": 1.0, "x": 0.5, "y": 0.5, "ease": "inCubic"},
                         {"t": round(t - 0.034, 3), "zoom": 1.15, "x": 0.62, "y": 0.5, "ease": "step"}]
            keys += [{"t": round(t, 3), "zoom": 1.15, "x": 0.38, "y": 0.5, "ease": "outCubic"},
                     {"t": round(t + 0.24, 3), "zoom": 1.0, "x": 0.5, "y": 0.5, "ease": "linear"}]
        elif kind == "shake":
            keys += [{"t": round(t, 3), "zoom": 1.08, "x": 0.5, "y": 0.5, "shake": 1.0, "ease": "linear"},
                     {"t": round(t + 0.35, 3), "zoom": 1.0, "x": 0.5, "y": 0.5, "shake": 0.0, "ease": "linear"}]
        else:
            continue
        spans.append((t - 0.6, t + 0.6))
    return [k for k in keys if k["t"] >= 0.0], spans


def camera_keys(block: dict) -> list[dict]:
    """Камера блока: зум 1.15–1.2× с лёгкой тряской на плашках (нок, вайп, победа) и ключи переходов; зум события
    рядом с переходом уступает переходу."""
    tkeys, tspans = _transition_keys(block)
    keys = [{"t": 0.0, "zoom": 1.0, "ease": "linear"}]
    total = block["total"]
    for f in block.get("fx") or []:
        if f["type"] != "zoom" or any(a <= f["t"] <= b for a, b in tspans):
            continue
        t, zoom = f["t"], f["zoom"]
        hold = max(0.1, min(0.5, total - t - 0.45))
        keys += [{"t": round(max(0.0, t - 0.06), 3), "zoom": 1.0, "x": 0.5, "y": 0.5, "ease": "outCubic"},
                 {"t": round(t + 0.1, 3), "zoom": zoom, "x": 0.5, "y": 0.5, "shake": 0.4, "ease": "linear"},
                 {"t": round(t + 0.1 + hold, 3), "zoom": zoom, "x": 0.5, "y": 0.5, "shake": 0.0, "ease": "inOutCubic"},
                 {"t": round(t + 0.35 + hold, 3), "zoom": 1.0, "x": 0.5, "y": 0.5, "ease": "linear"}]
    if tkeys:
        keys = [k for k in keys if not any(a <= k["t"] <= b for a, b in tspans) or k["t"] == 0.0]
        if any(k["t"] == 0.0 for k in tkeys):
            keys = [k for k in keys if k["t"] != 0.0]
        keys += tkeys
    keys = [k for k in keys if k["t"] <= total]
    keys.sort(key=lambda k: k["t"])
    return keys if len(keys) > 1 else []


def fx_layers(block: dict) -> list[dict]:
    out = []
    total = block["total"]
    for f in block.get("fx") or []:
        if f["type"] == "negate":
            out.append({"type": "fx", "name": "Синий негатив", "start": round(f["t"], 3),
                        "end": round(min(total, f["t"] + 0.35), 3), "track": 1,
                        "props": {"kind": "negate", "intensity": 1, "color": NEGATE_COLOR},
                        "in": {"type": "fade", "duration": 0.05}, "out": {"type": "fade", "duration": 0.08}})
        elif f["type"] == "bass":
            out.append({"type": "audio", "name": "Удар басом", "start": round(f["t"], 3),
                        "end": round(min(total, f["t"] + 1.5), 3),
                        "props": {"asset_id": BASS_SFX, "volume": 0.45, "role": "sfx"}})
    return [layer for layer in out if layer["end"] - layer["start"] >= 0.05]


_FRAMING: dict[tuple, dict] = {}


def meme_framing(path: str, aspect: str) -> dict:
    """Окно и заполнение мема под кадр формата (app/meme_frame.py): вшитые поля прочь, подрезка до 45 %."""
    from app.formats import frame_size
    from app.meme_frame import insert_framing

    width, height = frame_size(aspect)
    try:
        key = (path, Path(path).stat().st_mtime_ns, width, height)
    except OSError:
        return {"crop": None, "fill": None}
    if key not in _FRAMING:
        _FRAMING[key] = insert_framing(path, width, height)
    return _FRAMING[key]


def inserts(block: dict, aspect: str, library: dict[int, dict], parts: dict[int, list]) -> list[dict]:
    """Врезки мемов: во весь кадр, звук мема вровень с голосом; звук клипа под ними глушат вырезы по дорожкам."""
    out = []
    for ins in block["inserts"]:
        meme = library.get(int(ins["meme_id"])) or {}
        item = {"asset_id": int(ins["asset_id"]), "at": round(ins["at"], 3), "duration": round(ins["slot"], 3),
                "mode": "full", "duck": False, "volume": 1.0,
                "reason": f"видеомем «{ins['title'] or ins['meme_id']}» после «{ins['after_line'][:60]}»"}
        if meme.get("has_audio", True):
            item["level"] = "speech"
        if parts.get(int(ins["meme_id"])):
            item["parts"] = parts[int(ins["meme_id"])]
        path = str(meme.get("file_path") or "")
        if path and Path(path).is_file():
            frame = meme_framing(path, aspect)
            if frame.get("crop"):
                item["crop"] = frame["crop"]
            if frame.get("fill"):
                item["fill"] = frame["fill"]
        out.append(item)
    return out


def mutes(block: dict, mic: int | None) -> list[dict]:
    """Под мемом звук игры и микрофона — тишина; мат тиммейтов (голос B, звук игры) — целым словом."""
    from app.profanity import find_profanity

    tracks = [0] + ([mic] if mic not in (None, 0) else [])
    out = []
    for ins in block["inserts"]:
        a, b = round(ins["at"], 3), round(ins["at"] + ins["slot"], 3)
        out += [{"start": a, "end": b, "track": t, "by": TAG_MEME} for t in tracks]
    team = [w for w in block["words"] if w.get("speaker") == "B"]
    for hit in find_profanity(team):
        out.append({"start": round(max(0.0, hit["start"] - 0.03), 3), "end": round(hit["end"] + 0.03, 3), "track": 0,
                    "by": TAG_TEAM})
    return out


def spec_for(block: dict, aspect: str, base_render: dict, *, mic: int | None, library: dict[int, dict],
             parts: dict[int, list], index: int = 0, count: int = 1, label: str = "") -> dict:
    """Файл клипа (vvf.clip/1) одного блока одного формата."""
    from app.formats import normalize_format

    aspect = normalize_format(aspect)
    total = block["total"]
    layers: list[dict] = [captions(["A"], aspect, True, total)]
    if any(w.get("speaker") == "B" for w in block["words"]):
        layers.append(captions(["B"], aspect, False, total))
    for i, y in enumerate(block.get("yellow") or []):
        layers.append(yellow_layer(y["text"], y["start"], y["end"], aspect, index + i))
    layers += fx_layers(block) + transition_layers(block)
    render = dict(base_render)
    render.update({
        "format": aspect, "layers_format": aspect, "transition": {"type": "cut"}, "cuts": [],
        "inserts": inserts(block, aspect, library, parts), "layers": layers, "camera": camera_keys(block),
        "end_hold_sec": 0.0, "subs_on": False, "music_on": False, "mute": mutes(block, mic),
    })
    if mic is not None:
        render["audio"] = {"fx": [{"start": 0.0, "end": round(total, 3), "fx": "denoise", "track": mic,
                                   "amount": 0.5}]}
    focus = [{"t": 0.0, "x": 0.5}] if aspect != "16:9" else None  # прицел Apex — по центру кадра
    pieces = [{**p, **({"focus": focus} if focus else {})} for p in block["pieces"]]
    fight = block.get("fight") or {}
    title = f"Мем-эдит{(' ' + label) if label else ''} · блок {index + 1}/{count} · {TITLES.get(aspect, aspect)}"
    notes = (f"Мем-эдит (app/meme_edit.py), бой {fight.get('key')} {fight.get('start')}–{fight.get('end')} с записи "
             f"{block['source_id']}; мемы: " + ", ".join(f"«{i['title']}» после «{i['after_line'][:40]}»"
                                                        for i in block["inserts"]))
    return {"schema": "vvf.clip/1", "title": title[:100],
            "description": "Блок мем-эдита: бой целиком, мемы после реплик, всё в долю музыки.",
            "notes": notes[:3900], "pieces": pieces, "render": render}
