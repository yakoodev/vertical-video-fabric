"""Состояние мира «Мира от зрителей» (проект 10): что стоит на острове, что в инвентаре, какой ход, журнал ходов.

Состояние — один JSON на ход (``turn_000.json``, ``turn_001.json`` …): повторный рендер хода берёт свой файл и
получает тот же мир. Новый ход — чистая функция ``next_turn(state, counters, action, comment)``: прошлое состояние
не меняется.

Рост мира — только от настоящих чисел канала (``counters`` из app/platform_stats.py, сумма по опубликованным
сериям): за каждые ``likes_per_tree`` лайков — дерево, за каждые ``subs_per_item`` подписчиков — предмет из
``ITEMS`` по порядку, на ``house_at_likes`` лайков — дом, на ``pet_at_subs`` подписчиков — питомец. Пороги —
решение владельца (docs/design/viewer-turn-game.md, «Что решить владельцу»); здесь — значения по умолчанию.

Ночная линия (``night``) — от вех, не от комментариев: ``night_stage``.
"""

from __future__ import annotations

import json
import math
from copy import deepcopy
from pathlib import Path
from typing import Any

from app.viewer_actions import ACTIONS, feasible

SCHEMA = "vvf.viewer_world"
VERSION = 1

DEFAULT_GROWTH = {"likes_per_tree": 100, "subs_per_item": 10, "house_at_likes": 1000, "pet_at_subs": 100,
                  "max_trees": 12}
# Предметы за подписчиков — по порядку: каждый открывает действия (viewer_actions.ACTIONS[*].needs).
ITEMS = ["фонарь", "спички", "книга", "удочка", "яблоко", "одеяло", "лопатка", "гитара"]
ISLAND_RADIUS = 1.7
_GOLDEN = math.pi * (3 - math.sqrt(5))


def new_world(seed: int = 7) -> dict[str, Any]:
    """Ход 0: пустой остров, нули, ночь."""
    return {
        "schema": SCHEMA, "version": VERSION, "turn": 0, "seed": int(seed),
        "counters": {"likes": 0, "subs": 0, "source": "канал ещё не опубликован", "taken_at": ""},
        "growth": dict(DEFAULT_GROWTH),
        "island_radius": ISLAND_RADIUS,
        "objects": [], "inventory": [], "time_of_day": "night", "mood": "calm",
        "night": {"stage": 0},
        "log": [{"turn": 0, "action": None, "comment": None, "counters": {"likes": 0, "subs": 0}, "added": []}],
        "ideas": [],
    }


def validate(state: dict) -> None:
    if state.get("schema") != SCHEMA:
        raise ValueError("не состояние мира vvf.viewer_world")
    if int(state.get("version", 0)) > VERSION:
        raise ValueError(f"версия состояния {state.get('version')} новее кода ({VERSION})")
    c = state.get("counters") or {}
    for key in ("likes", "subs"):
        if not isinstance(c.get(key), int) or c[key] < 0:
            raise ValueError(f"counters.{key}: нужно целое ≥ 0 (только настоящие числа)")
    ids = [o.get("id") for o in state.get("objects") or []]
    if len(ids) != len(set(ids)):
        raise ValueError("повтор id предмета")


def spot(index: int, radius: float, seed: int) -> list[float]:
    """Место предмета на острове: спираль с золотым углом от края площадки героини (0.7 м) к краю острова."""
    r = 0.7 + (radius - 0.95) * math.sqrt((index + 0.5) / 14)
    a = index * _GOLDEN + seed * 0.37
    return [round(math.cos(a) * min(r, radius - 0.25), 3), round(math.sin(a) * min(r, radius - 0.25), 3)]


def growth_targets(counters: dict, growth: dict) -> dict[str, Any]:
    """Сколько чего должно быть в мире при этих числах."""
    g = {**DEFAULT_GROWTH, **(growth or {})}
    likes, subs = int(counters.get("likes", 0)), int(counters.get("subs", 0))
    return {
        "trees": min(int(g["max_trees"]), likes // int(g["likes_per_tree"])),
        "items": ITEMS[: min(len(ITEMS), subs // int(g["subs_per_item"]))],
        "house": likes >= int(g["house_at_likes"]),
        "pet": subs >= int(g["pet_at_subs"]),
    }


def night_stage(state: dict) -> int:
    """Ночная линия по вехам: 0 — тень мелькает; 1 — следы у края (первое дерево); 2 — тень ближе, гаснет огонь
    (первый предмет); 3 — стучит в дверь (дом); 4 — развязка (питомец его не боится). Не убывает."""
    t = growth_targets(state.get("counters") or {}, state.get("growth") or {})
    stage = 0
    if t["trees"] >= 1:
        stage = 1
    if t["items"]:
        stage = max(stage, 2)
    if t["house"]:
        stage = max(stage, 3)
    if t["pet"]:
        stage = max(stage, 4)
    return max(stage, int((state.get("night") or {}).get("stage", 0)))


def _add(state: dict, kind: str, origin: str, added: list) -> None:
    n = sum(1 for o in state["objects"] if o["kind"] == kind)
    idx = len(state["objects"])
    obj = {"id": f"{kind}-{n + 1}", "kind": kind, "pos": spot(idx, state["island_radius"], state["seed"]),
           "rot": round((idx * 1.3) % 6.28, 2), "origin": origin, "turn": state["turn"]}
    state["objects"].append(obj)
    added.append(obj["id"])


def next_turn(state: dict, counters: dict, action: dict | None = None, comment: dict | None = None) -> dict:
    """Следующий ход: числа канала → рост; действие из комментария → его след в мире. Возвращает новое состояние."""
    validate(state)
    s = deepcopy(state)
    s["turn"] = int(state["turn"]) + 1
    likes, subs = int(counters["likes"]), int(counters["subs"])
    if likes < 0 or subs < 0:
        raise ValueError("числа канала не бывают отрицательными")
    s["counters"] = {"likes": likes, "subs": subs, "source": str(counters.get("source", "")),
                     "taken_at": str(counters.get("taken_at", ""))}
    added: list[str] = []
    target = growth_targets(s["counters"], s["growth"])
    trees = sum(1 for o in s["objects"] if o["kind"] == "tree" and o.get("origin") == "likes")
    if target["trees"] > trees:
        # Остров подрастает вместе с лесом — деревьям есть место.
        s["island_radius"] = round(ISLAND_RADIUS + 0.22 * math.sqrt(target["trees"]), 2)
    for _ in range(target["trees"] - trees):
        _add(s, "tree", "likes", added)
    for item in target["items"]:
        if item not in s["inventory"] and not any(item in (e.get("got") or []) for e in s["log"]):
            s["inventory"].append(item)
            added.append(f"item:{item}")
    for flag in ("house", "pet"):
        if target[flag] and not any(o["kind"] == flag for o in s["objects"]):
            _add(s, flag, "milestone", added)
    act_id = (action or {}).get("id")
    if act_id:
        ok, why = feasible(s, act_id)
        if not ok:
            raise ValueError(f"действие {act_id} невыполнимо: {why}")
        spec = ACTIONS[act_id]
        effect = spec.get("effect") or {}
        if effect.get("spawn"):
            _add(s, effect["spawn"], f"action:{act_id}", added)
        if effect.get("mood"):
            s["mood"] = effect["mood"]
        for item in spec.get("consumes") or []:
            s["inventory"].remove(item)
    s["night"] = {"stage": night_stage(s)}
    s["log"] = list(s["log"]) + [{
        "turn": s["turn"], "action": act_id, "params": (action or {}).get("params") or {},
        "comment": _credit(comment), "counters": {"likes": likes, "subs": subs}, "added": added,
        "got": [a[5:] for a in added if a.startswith("item:")],
    }]
    return s


def _credit(comment: dict | None) -> dict | None:
    """В журнал — только ник, текст и лайки комментария (никаких других данных автора)."""
    if not comment:
        return None
    return {"nick": str(comment.get("nick", ""))[:40], "text": str(comment.get("text", ""))[:300],
            "likes": int(comment.get("likes", 0)), "id": str(comment.get("id", ""))[:64]}


def turn_path(folder: Path, turn: int) -> Path:
    return Path(folder) / f"turn_{int(turn):03d}.json"


def save_turn(folder: Path, state: dict) -> Path:
    """Пишет состояние хода. Файл хода не перезаписывается другим миром: ход — неизменяемая версия."""
    validate(state)
    path = turn_path(folder, state["turn"])
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(state, ensure_ascii=False, indent=1)
    if path.is_file() and path.read_text("utf-8") != text:
        raise FileExistsError(f"{path.name} уже есть и отличается: ход нельзя переписать")
    path.write_text(text, "utf-8")
    return path


def load_turn(folder: Path, turn: int) -> dict:
    state = json.loads(turn_path(folder, turn).read_text("utf-8"))
    validate(state)
    return state
