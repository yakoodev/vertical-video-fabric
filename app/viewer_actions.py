"""Библиотека действий героини «Мира от зрителей» (проект 10, docs/design/viewer-turn-game.md).

Каждое действие — то, что героиня может сделать за ход по самому залайканному комментарию:
как оно рисуется (поза или мокап, предмет, камера, эффект PS1), что меняет в мире и что для него нужно.

``ready`` — рендер (node/vrm/src/psx) уже умеет действие; остальные описаны, но рендер ещё не собран:
выбрать их можно, но ход тогда ждёт сборки (карточка владельцу честно скажет «действие не готово»).

Чистые данные и функции, без сети: тестируются как есть (tests/test_viewer_turn.py).
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

# Как рисуется: pose — процедурная поза node/vrm/src/psx/sit.ts; mocap — клип CMU из vendor/cmu/clips
# (tools/cmu_to_vrm.py); walk — шаги по острову к точке; prop — предмет мира (things.ts); vfx — эффект PS1.
ACTIONS: dict[str, dict[str, Any]] = {
    "look_around": {"label": "осмотреться", "ready": True, "render": {"look": "sweep", "camera": "orbit_mid"}},
    "sit": {"label": "сесть на траву", "ready": True, "render": {"pose": "sit", "camera": "low_side"}},
    "stand": {"label": "встать", "ready": True, "render": {"pose": "stand"}},
    "wave": {"label": "помахать зрителям", "ready": False,
             "render": {"mocap": "wave", "camera": "front_mid"}},
    "walk_to": {"label": "дойти до места", "ready": False, "params": {"target": "id предмета или край острова"},
                "render": {"walk": "target", "camera": "follow"}},
    "sleep": {"label": "лечь спать", "ready": False, "render": {"pose": "lie", "vfx": "zzz", "camera": "top"},
              "effect": {"mood": "rested"}},
    "dance": {"label": "потанцевать", "ready": False, "render": {"mocap": "dance", "camera": "orbit_fast"},
              "effect": {"mood": "happy"}},
    "plant_tree": {"label": "посадить дерево", "ready": False,
                   "render": {"pose": "kneel", "prop": "tree", "vfx": "sparkle", "camera": "low_side"},
                   "effect": {"spawn": "tree"}},
    "build_fire": {"label": "развести костёр", "ready": False,
                   "render": {"pose": "kneel", "prop": "campfire", "vfx": "embers", "camera": "low_front"},
                   "effect": {"spawn": "campfire"}, "needs": {"inventory": ["спички"]}},
    "eat": {"label": "перекусить", "ready": False, "render": {"pose": "sit", "mocap": "eat", "camera": "close"},
            "effect": {"mood": "fed"}, "needs": {"inventory": ["яблоко"]}, "consumes": ["яблоко"]},
    "read": {"label": "почитать книгу", "ready": False, "render": {"pose": "sit", "prop_hand": "book", "camera": "close"},
             "needs": {"inventory": ["книга"]}},
    "fish": {"label": "порыбачить с края", "ready": False,
             "render": {"pose": "sit_edge", "prop_hand": "rod", "camera": "edge_wide"},
             "needs": {"inventory": ["удочка"]}},
    "pet_animal": {"label": "погладить питомца", "ready": False, "render": {"pose": "kneel", "camera": "close"},
                   "needs": {"objects": ["pet"]}, "effect": {"mood": "happy"}},
    "light_lantern": {"label": "зажечь фонарь", "ready": False,
                      "render": {"mocap": "reach", "prop": "lantern", "vfx": "glow", "camera": "low_front"},
                      "effect": {"spawn": "lantern"}, "needs": {"inventory": ["фонарь"]}, "consumes": ["фонарь"]},
    "dig": {"label": "покопать землю", "ready": False, "render": {"pose": "kneel", "vfx": "dirt", "camera": "low_side"},
            "effect": {"find": True}},
    "look_sky": {"label": "посмотреть на звёзды", "ready": False,
                 "render": {"pose": "sit", "look": "sky", "vfx": "shooting_star", "camera": "under"}},
    "hide": {"label": "спрятаться", "ready": False, "render": {"pose": "crouch", "camera": "far", "vfx": "dim"},
             "effect": {"mood": "scared"}},
    "plant_flowers": {"label": "посадить цветы", "ready": False,
                      "render": {"pose": "kneel", "prop": "flowers", "camera": "low_side"},
                      "effect": {"spawn": "flowers"}},
    "call_out": {"label": "крикнуть в темноту", "ready": False,
                 "render": {"mocap": "call", "camera": "behind", "vfx": "echo"}},
}

# Что точно не делаем, даже если это самый залайканный коммент (ещё до фильтра безопасности).
FORBIDDEN_IDEAS = ("раздеться", "умереть", "убить", "прыгнуть с острова", "покончить", "ударить себя")


def action_ids() -> list[str]:
    return list(ACTIONS)


def get_action(action_id: str) -> dict[str, Any]:
    if action_id not in ACTIONS:
        raise KeyError(f"нет такого действия: {action_id}")
    return {"id": action_id, **deepcopy(ACTIONS[action_id])}


def feasible(state: dict, action_id: str) -> tuple[bool, str]:
    """Можно ли сделать действие в этом мире: есть ли нужное в инвентаре и на острове."""
    if action_id not in ACTIONS:
        return False, "нет такого действия"
    needs = ACTIONS[action_id].get("needs") or {}
    inv = list(state.get("inventory") or [])
    for item in needs.get("inventory", []):
        if item not in inv:
            return False, f"нет в инвентаре: {item}"
    kinds = {o.get("kind") for o in state.get("objects") or []}
    for kind in needs.get("objects", []):
        if kind not in kinds:
            return False, f"на острове нет: {kind}"
    return True, ""
