"""Герои и локации истории «Витубики» поверх плана (``app/vtubika_plan.py``): несколько персонажей (главный герой
за столом и «каст» — тот же герой на складе, коллега), их места и проходы, действия-петли, и фазы окружения —
комната или склад по ракурсу кадра (``app/vtubika_sets.py``).

* Каст плана: ``"cast": {"tema_wh": {"hero": "tema_work", "at": "wh_box_a"}, …}`` — внешность из
  ``app.vtubika.HEROES``, место — из ``spots`` локации (x, z, yaw). Стоит (без сиденья), петля по умолчанию —
  ``Idle_Loop``.
* События с ``"who": id`` — ``act``, ``face``, ``place`` (переставить на место), ``walk`` (дойти до места за ``dur``),
  ``carry`` (коробка локации в руках до ``until``/``dur``).
* Фазы: кадр склада (``wh_*``) — окружение склада (туман, цветокоррекция, свой рассеянный свет), остальные —
  исходное окружение комнаты; свет комнаты (рассеянный) на складе гаснет.
"""

from __future__ import annotations

from typing import Any

from app import voxel3d as V
from app import vtubika as U


def actions_for(acts: list[dict], total: float, default_loop: str = "Sit_Loop") -> tuple[list[dict], list[dict]]:
    """Клипы героя → окна движка: петля — до следующей петли (+ наплыв); одноразовое — поверх, потом снова прошлая
    петля. Возвращает (actions, loops)."""
    actions: list[dict] = []
    acts = sorted(acts, key=lambda a: a["t"])
    loops = [a for a in acts if a["clip"].endswith("_Loop")]
    if not loops or loops[0]["t"] > 0:
        loops.insert(0, {"clip": default_loop, "t": 0.0, "fade": 0.3})
    for i, a in enumerate(loops):
        end = loops[i + 1]["t"] + loops[i + 1]["fade"] if i + 1 < len(loops) else total + 0.5
        actions.append({"clip": a["clip"], "start": round(a["t"], 3), "end": round(end, 3), "fade": a["fade"],
                        "offset": round((i * 0.37) % 1.0, 2)})
    for a in acts:
        if a["clip"].endswith("_Loop"):
            continue
        dur = V.VOXEL_CLIPS[a["clip"]]
        actions.append({"clip": a["clip"], "start": a["t"], "end": round(a["t"] + dur, 3), "fade": 0.2})
        prev = next((x for x in reversed(loops) if x["t"] <= a["t"]), loops[0])
        actions.append({"clip": prev["clip"], "start": round(a["t"] + dur - 0.35, 3),
                        "end": round(min(total + 0.5, next((x["t"] + x["fade"] for x in loops if x["t"] > a["t"]), total + 0.5)), 3),
                        "fade": 0.35})
    actions.sort(key=lambda a: a["start"])
    return actions, loops


class Cast:
    """Персонажи каста: клипы, лица, путь (места и проходы), что держат в руках."""

    def __init__(self, plan: dict, spots: dict[str, list[float]]):
        self.spots = spots
        self.members: dict[str, dict] = {}
        for cid, cfg in (plan.get("cast") or {}).items():
            if cfg.get("hero") not in U.HEROES:
                raise ValueError(f"каст {cid}: герой — один из {', '.join(U.HEROES)}")
            x, z, yaw = self.spot(cfg["at"])
            self.members[cid] = {"hero": cfg["hero"], "acts": [], "faces": [], "path": [{"t": 0.0, "p": [x, 0.0, z]}],
                                 "facing": [], "yaw": yaw, "attach": []}

    def spot(self, name: str) -> list[float]:
        if name not in self.spots:
            raise ValueError(f"нет места «{name}» (есть: {', '.join(self.spots)})")
        return self.spots[name]

    def has(self, cid: str) -> bool:
        return cid in self.members

    def event(self, cid: str, ev: dict, t: float, until: float | None) -> None:
        m = self.members[cid]
        if "act" in ev:
            m["acts"].append({"clip": ev["act"], "t": t, "fade": float(ev.get("fade") or 0.35)})
        if "face" in ev:
            m["faces"].append({"t": t, "face": ev["face"]})
        last = m["path"][-1]["p"]
        if "place" in ev:
            x, z, yaw = self.spot(ev["place"])
            m["path"] += [{"t": round(t - 0.001, 3), "p": last}, {"t": round(t, 3), "p": [x, 0.0, z]}]
            m["facing"].append({"t": round(t, 3), "yaw": yaw, "dur": 0.01})
        if "walk" in ev:
            x, z, yaw = self.spot(ev["walk"])
            dur = float(ev.get("dur") or 2.0)
            m["path"] += [{"t": round(t, 3), "p": last}, {"t": round(t + dur, 3), "p": [x, 0.0, z], "ease": "inOutSine"}]
            m["facing"] += [{"t": round(t, 3), "auto": True, "dur": 0.3}, {"t": round(t + dur, 3), "yaw": yaw, "dur": 0.4}]
        if ev.get("carry"):
            m["attach"].append({"start": t, "end": round(until if until is not None else t + 3.0, 3)})

    def characters(self, total: float) -> list[dict]:
        out = []
        for cid, m in self.members.items():
            cfg = U.HEROES[m["hero"]]
            actions, _ = actions_for(m["acts"], total, "Idle_Loop")
            ch = V.voxel_hero(cid, shape=cfg["shape"], look=cfg["look"], position=m["path"][0]["p"], yaw=m["yaw"],
                              actions=actions, faces=from_start(m["faces"]))
            path = sorted(m["path"], key=lambda k: k["t"])
            if len(path) > 1:
                ch["path"] = path
            ch["facing"] = sorted(m["facing"], key=lambda k: k["t"])
            out.append(ch)
        return out

    def carry_attach(self, obj_id: str, objects: list[dict]) -> None:
        """Коробка в руках: крепление к правой руке персонажа (смещение — к центру между руками)."""
        obj = next((o for o in objects if o["id"] == obj_id), None)
        if obj is None:
            return
        for cid, m in self.members.items():
            for w in m["attach"]:
                obj.setdefault("attach", []).append({"to": f"{cid}:hand_r", "start": w["start"], "end": w["end"],
                                                     "fadeIn": 0.0, "fadeOut": 0.3, "offset": [-0.15, -0.2, 0.1]})


def from_start(faces: list[dict]) -> list[dict]:
    """Лицо с первого кадра: первое выражение плана держится и до своего ключа (вступление до первого слова)."""
    if not faces:
        return [{"t": 0, "face": "neutral"}]
    first = min(faces, key=lambda f: f["t"])
    return faces if first["t"] <= 0 else [{"t": 0, "face": first["face"]}, *faces]


def set_of(cam: str) -> str:
    return "warehouse" if cam.startswith("wh_") else "room"


def phases(shots: list[dict], spec_env: dict, lights: list[dict], wh: dict | None) -> list[dict]:
    """Фазы окружения по кадрам (склейка в другую локацию — другой «образ») и ключи рассеянного света: на складе —
    свой, комнатный гаснет. Меняет ``lights`` (ключи яркости). Возвращает список фаз."""
    if not wh:
        return []
    post = spec_env.get("post") or {}
    room_phase = {"background": spec_env.get("background"), "fog": spec_env.get("fog"), "exposure": spec_env.get("exposure", 1.0),
                  "grade": post.get("grade"), "vignette": post.get("vignette"),
                  "bloom": (post.get("bloom") or {}).get("strength")}
    out: list[dict] = []
    seq: list[tuple[float, str]] = []
    cur = "room"
    for sh in shots:
        s = set_of(sh["cam"])
        if s != cur:
            t = round(sh["t0"], 3)
            seq.append((t, s))
            out.append({"t": t, **(wh["phase"] if s == "warehouse" else room_phase)})
            cur = s
    owner = {"ambient": "room", **{lid: "warehouse" for lid in wh["global_lights"]}}
    level = {"wh_ambient": 0.55}
    for light in lights:
        lid = light["id"]
        if lid not in owner:
            continue
        base = level.get(lid, float(light.get("intensity", 1.0)))

        def val(where: str, lid=lid, base=base) -> float:
            return base if where == owner[lid] else 0.0

        keys = [{"t": 0.0, "intensity": val("room")}]
        state = "room"
        for t, where in seq:
            if t <= 0:
                keys = [{"t": 0.0, "intensity": val(where)}]
            else:
                keys += [{"t": round(t - 0.001, 3), "intensity": val(state)}, {"t": t, "intensity": val(where)}]
            state = where
        light["keys"] = keys
        light["intensity"] = base
    return out
