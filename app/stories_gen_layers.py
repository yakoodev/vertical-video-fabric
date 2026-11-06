"""Истории: разметка времени и слои выпуска (из ``app/stories_gen.py``)."""

from __future__ import annotations

from typing import Any

from app import explainer_gen as X
from app import projects as P
from app import stories as S
from app import stories_disaster as D
from app.stories_gen_beats import CAMERA_ALT

REAL_SPLIT_SEC = 3.6  # после озвучки бит длиннее — делится той же сценой с другой камерой
MIN_PLACED_SEC = 0.35


SFX_VOLUME = 0.55
SFX_OFFSET_SEC = 0.12  # звук действия — чуть после склейки
SFX_MAX_SEC = 3.5
TITLE_SEC = 2.2
LAYER_PREFIX = "st_"


def place(beats: list[dict], words: list[dict], total: float) -> list[dict]:
    """Биты по времени: старт — начало первого слова бита; слипшиеся (< 0.35 с) — в прошлый; бит длиннее
    ``REAL_SPLIT_SEC`` делится той же сценой с другим движением камеры."""
    if not words:
        raise S.StoryError("нет слов озвучки")
    firsts = X.align(beats, words)
    placed: list[dict] = []
    for n, beat in enumerate(beats):
        start = 0.0 if n == 0 else round(float(words[firsts[n]]["start"]), 3)
        w0 = 0 if n == 0 else firsts[n]
        if placed and start - placed[-1]["start"] < MIN_PLACED_SEC:
            placed[-1]["text"] += " " + beat["text"]
            if beat.get("sfx") and not placed[-1].get("sfx"):
                placed[-1]["sfx"] = beat["sfx"]
            continue
        placed.append({**beat, "start": start, "w0": w0})
    for n, item in enumerate(placed):
        item["end"] = placed[n + 1]["start"] if n + 1 < len(placed) else round(total, 3)
        item["w1"] = (placed[n + 1]["w0"] - 1) if n + 1 < len(placed) else len(words) - 1
    out: list[dict] = []
    for item in placed:
        length = item["end"] - item["start"]
        inner = [k for k in range(item["w0"] + 1, item["w1"] + 1)
                 if item["start"] + 1.0 < words[k]["start"] < item["end"] - 1.0]
        if length > REAL_SPLIT_SEC and inner and item is not placed[-1]:
            mid = item["start"] + length / 2
            k = min(inner, key=lambda j: abs(words[j]["start"] - mid))
            cut = round(float(words[k]["start"]), 3)
            out += [{**item, "end": cut, "w1": k - 1},
                    {**item, "start": cut, "w0": k, "variant": "alt", "sfx": None, "split_of": item["i"],
                     "camera": CAMERA_ALT.get(item.get("camera") or "push_in", "push_in")}]
        else:
            out.append(item)
    return out


def _sfx_duration(store: Any, ref: str) -> float:
    if ref.isdigit():
        try:
            return float(store.get_montage_asset(int(ref)).get("duration_sec") or 1.5)
        except KeyError:
            return 1.5
    try:
        from app.audio_tools import _sound_ok

        ok, dur = _sound_ok(ref)
        return float(dur) if ok else 1.5
    except Exception:  # noqa: BLE001
        return 1.5


def _captions_props(world: dict) -> dict:
    cap = world["captions"]
    return {"style": "mrbeast", "font": "Rubik", "weight": 900, "size": int(cap["size"]), "color": cap["color"],
            "activeColor": cap["active"], "stroke": {"color": cap["stroke"], "width": 12},
            "uppercase": bool(cap["uppercase"]), "maxWords": 1, "maxLines": 1, "animation": "pop",
            "wordScale": 1.06, "punctuation": False}


def build_layers(store: Any, world: dict, ep: dict, placed: list[dict], narration_asset: int, total: float,
                 notes: list[str]) -> tuple[list[dict], list[dict]]:
    """Слои (сцены с камерой, звуки действий, озвучка, субтитры по слову, название) и ключи 3D-камеры."""
    from app.aivideo_assemble import CAMERA_PRESET_MOVES, _keys, preset_camera
    from app.camera3d import PresetError

    scenes = {int(s["id"]): s for s in S.list_scenes(store, int(world["id"]))}
    layers: list[dict] = []
    camera: list[dict] = []
    for n, item in enumerate(placed):
        scene = scenes.get(int(item["scene_id"]))
        if scene is None or not S.scene_usable(scene, S.is_dry(ep)):
            raise S.StoryError(f"бит {item['i']}: у сцены {item['scene_id']} нет картинки")
        t0, t1 = item["start"], item["end"]
        move = item.get("camera") or "push_in"
        base, keys = _keys(move, t1 - t0)
        layer = {"id": f"{LAYER_PREFIX}sc{n:02d}", "type": "image", "name": f"Сцена: {scene['title']}"[:60],
                 "start": round(t0, 3), "end": round(t1, 3), "track": 1, "x": 0.5, "y": 0.5, "z": 0,
                 "scale": base, "props": {"asset_id": int(scene["asset_id"]), "w": 1080, "content": True}}
        if keys:
            layer["keys"] = keys
        layers.append(layer)
        if move in CAMERA_PRESET_MOVES:
            try:
                camera = preset_camera(move, t0, t1, total, camera, layers, n + 1 < len(placed))
            except PresetError as exc:
                notes.append(f"бит {item['i']}: камера {move} не встала ({exc}) — остался Ken Burns")
        sfx = item.get("sfx")
        if sfx and sfx.get("ref"):
            s = round(min(t1 - 0.2, t0 + SFX_OFFSET_SEC), 3)
            dur = min(SFX_MAX_SEC, max(0.4, _sfx_duration(store, str(sfx["ref"]))))
            layers.append({"id": f"{LAYER_PREFIX}sfx{n:02d}", "type": "audio", "name": f"Звук: {sfx.get('label') or ''}"[:60],
                           "start": max(0.0, s), "end": round(min(total, s + dur), 3),
                           "props": {"asset_id": str(sfx["ref"]), "mode": "track", "volume": SFX_VOLUME,
                                     "fadeOut": 0.15, "role": "sfx"}})
    options = ep.get("options") or {}
    disaster = D.is_disaster(world, ep)
    speech_end = float(ep.get("_speech_end") or total)
    if disaster:
        # Название серии поверх первого кадра, HUD-плашки фраз, титр следующей части после последнего слова.
        from app.aivideo_gen import _asset_path

        stroke = world["captions"]["stroke"]
        if options.get("title_card", True):
            layers += D.title_layers(ep, placed, stroke, LAYER_PREFIX)
        used = {int(p["scene_id"]) for p in placed}
        paths = {sid: _asset_path(store, {"asset_id": sc.get("asset_id")}) for sid, sc in scenes.items() if sid in used}
        layers += D.hud_layers(ep, placed, speech_end, float(world["captions"]["y"]), paths, LAYER_PREFIX)
        if total - speech_end >= 0.5:
            layers.append(D.part_layer(ep, speech_end, total, stroke, LAYER_PREFIX))
    elif options.get("title_card"):
        title = P._text(ep.get("title") or ep["premise"], 60)
        end = round(min(TITLE_SEC, placed[0]["end"] if placed else TITLE_SEC, total), 3)
        # Дорожка выше сцен (1) и ниже субтитров (6): на дорожке 0 название пряталось под картинкой.
        layers.append({"id": f"{LAYER_PREFIX}title", "type": "text", "name": "Название", "start": 0.0, "end": end,
                       "track": 5, "x": 0.5, "y": 0.16, "props": {"text": title, "font": "Rubik", "weight": 900, "size": 84,
                                                      "color": "#ffffff", "align": "center",
                                                      "stroke": {"color": world["captions"]["stroke"], "width": 10}},
                       "in": {"type": "pop", "duration": 0.2}})
    layers.append({"id": f"{LAYER_PREFIX}narr", "type": "audio", "name": "Рассказчик", "start": 0.0, "end": round(total, 3),
                   "props": {"asset_id": str(narration_asset), "mode": "track", "volume": 1.0, "role": "voice"}})
    layers.append({"id": f"{LAYER_PREFIX}caps", "type": "captions", "name": "Субтитры по слову", "start": 0.0,
                   "end": round(speech_end if disaster else total, 3), "track": 6, "x": 0.5, "y": float(world["captions"]["y"]),
                   "props": _captions_props(world)})
    return layers, camera
