"""Движок ``vrm_desk`` 3D-серии: план серии (``app/s3d_plan.py``) → спека рендера node/vrm.

Готовый путь историй Витубики переиспользуется целиком: план серии переводится в план Витубики
(``to_vtubika``: фразы, кадры, события ``do`` и ``vrm``), ``app.vtubika_plan.build`` раскладывает кадры и события по
словам озвучки, ``app.vtubika_vrm.to_vrm_spec`` делает спеку VRM (экран монитора, оверлеи, вставки, руки, взгляд).
Поверх — то, чего у историй не было:

* персонажи библиотеки (перекраска материалов, своя модель, наушники) — героиней в кадре и аватарами на мониторе;
* движения камеры — пресеты движка (``slow_push``, ``punch_in``, ``dutch_tilt``…) вокруг ракурса VRM: ключи считает
  тот же код, что у сцен (``node/scene3d/presets_dump.mjs --expand``), здесь они режутся по планам;
* игра персонажа — пресеты эмоций и жестов (ключи ``timeline`` VRM из того же разворота);
* свет — пресет света/настроения и свет локации → ключевой и заполняющий свет VRM;
* рот — окна речи только на репликах героини и огибающая громкости голоса (``mouthEnv``);
* заголовки, звуки и переходы — списком для холст-клипа (``app/s3d_render.py``).
"""

from __future__ import annotations

import copy
import math
import threading
from typing import Any

from app.s3d_plan import VRM_INSERTS, PlanError

FPS = 30
_LOCK = threading.Lock()
INSERT_PUSH = {"static": 0.0, "slow_push": 0.22, "pull_out": 0.0, "punch_in": 0.3, "crash_zoom": 0.35,
               "handheld": 0.12, "dutch_tilt": 0.12, "dolly_zoom": 0.2}


def expand_presets(spec: dict) -> dict:
    """Развернуть блок presets кодом движка (node, без браузера). Тесты подменяют."""
    from app import presets3d

    return presets3d.expand(spec)


# --------------------------------------------------------------------------- персонажи библиотеки → VRM


def _base(name: str) -> dict:
    from app import vtubika_vrm_stage as VS

    return VS.HEROINES.get(name) or VS.AVATARS[name]


def heroine_entry(ch: dict) -> dict:
    """Персонаж библиотеки → запись героини node/vrm: модель, кресло, наушники, образ с перекраской."""
    from app import vtubika_vrm_stage as VS

    v = ch.get("vrm") or {}
    base = _base(v.get("base") or "nika")
    look = copy.deepcopy(base["look"])
    if v.get("recolor"):
        look["repaint"] = {**(look.get("repaint") or {}), **v["recolor"]}
    if v.get("tint"):
        look["tint"] = {**(look.get("tint") or {}), **v["tint"]}
    model = v.get("model") or base["model"]
    hp = base.get("headphones", False)
    want = v.get("headphones")
    if want is False:
        hp = False
    elif isinstance(want, dict) and want:
        tmpl = hp if isinstance(hp, dict) else {k: x for k, x in VS.ZHENYA_HEADSET.items()}
        hp = {**tmpl, **want}
    elif want is True and not hp:
        hp = True
    chair = base.get("chair_y") or (0.79 if model == VS.TWIST else 0.775)
    return {"model": model, "chair_y": chair, "headphones": hp, "look": look}


def avatar_entry(ch: dict) -> dict:
    v = ch.get("vrm") or {}
    base = _base(v.get("base") or "kira")
    her = heroine_entry(ch)
    look = {**her["look"], "outline": 0.004}
    acc = copy.deepcopy(base.get("accessories") or [])
    if isinstance(her["headphones"], dict) and not acc:
        acc = [her["headphones"]]
    return {"model": her["model"], "look": look, "accessories": acc}


# --------------------------------------------------------------------------- план серии → план Витубики

_TIMING = ("at", "dt", "dur", "until", "span", "dt_end")


def _timing(ev: dict) -> dict:
    return {k: ev[k] for k in _TIMING if ev.get(k) not in (None, "")}


def _event_do(ev: dict) -> tuple[list[dict], list[dict]]:
    """Событие серии → (события do Витубики, события vrm)."""
    from app import vtubika as U

    kind, t = ev.get("kind"), _timing(ev)
    if kind == "comment":
        msg = {k: ev[k] for k in ("user", "text", "color", "big") if ev.get(k) not in (None, "")}
        out = [{**t, "chat": msg}]
        if ev.get("card"):
            out.append({**t, "chatcard": True, **({} if ev.get("dur") or ev.get("until") else {"dur": 3.5})})
        return out, []
    if kind == "card":
        card = ev.get("card")
        dur = {} if ev.get("dur") or ev.get("until") else {"dur": 3.0}
        if card == "chat":
            return [{**t, **dur, "chatcard": True}], []
        if card == "screen":
            return [{**t, **dur, "screencard": {k: ev[k] for k in ("x", "y", "w") if ev.get(k) is not None}}], []
        if card == "alert":
            return [{**t, **dur, "alertcard": {k: ev[k] for k in ("title", "amount", "from", "note", "y", "scale")
                                                if ev.get(k) is not None}}], []
        if card == "phone":
            return [{**t, **dur, "phone": copy.deepcopy(ev.get("phone") or {"screen": ev.get("screen") or "notifications"})}], []
        if card == "hud":
            return [{**t, "hud": int(ev.get("value") or 0)}], []
    if kind == "sfx" and str(ev.get("sound")) in U.SFX:
        return [{**t, "sfx": str(ev["sound"])}], []
    if kind == "viewers":
        key = "viewers_hud" if ev.get("hud") else "viewers"
        return [{**t, key: int(ev["value"]), **({"label": ev["label"]} if ev.get("label") else {})}], []
    if kind == "screen":
        return [{**t, "screen": ev["mode"]}], []
    if kind == "avatar":
        return [], [{**t, "av": ev["act"], **({"who": ev["who"]} if ev.get("who") else {}),
                     **({"drop": ev["drop"]} if ev.get("drop") else {})}]
    if kind == "vrm":
        return [], [{**t, **copy.deepcopy(ev["do"])}]
    if kind == "vtubika":
        return [{**t, **copy.deepcopy(ev["do"])}], []
    return [], []


def heroine_of(plan: dict) -> str:
    return plan["cast"][0]


def speaker(plan: dict, line: dict) -> str:
    return plan.get("narrator", "narrator") if line["who"] == "narrator" else line["who"]


def to_vtubika(plan: dict, lib: dict, key: str) -> dict:
    """План серии → план истории Витубики (героиня — запись ``key`` в таблице героинь)."""
    loc = (lib.get("locations") or {}).get(plan["location"]) or {}
    plate = loc.get("plate") or {}
    hero = heroine_of(plan)
    sentences = []
    talking = None
    for line in plan["lines"]:
        do: list[dict] = []
        vrm: list[dict] = []
        # Героиня говорит в эфир — смотрит в вебку (Talk_Loop); голос за кадром — она смотрит в монитор.
        talks = speaker(plan, line) == hero
        src = line.get("vtubika") or {}
        if any("act" in ev for ev in src.get("do") or []):
            talking = None  # у импортированной фразы свои действия героини — свои не ставим
        elif talks != talking:
            do.append({"act": "Talk_Loop" if talks else "Sit_Loop"})
            talking = talks
        for ev in line.get("events") or []:
            a, b = _event_do(ev)
            do += a
            vrm += b
        do += copy.deepcopy(src.get("do") or [])
        vrm += copy.deepcopy(src.get("vrm") or [])
        shots = []
        for sh in line.get("shots") or []:
            move = sh.get("move")
            item = {"cam": sh["camera"], "push": 0.0 if move in ("static", "pull_out", "dolly_zoom") else 0.15,
                    "roll": float(sh.get("roll") or 0.0), "s3d": copy.deepcopy(sh)}
            if sh.get("at"):
                item["at"] = sh["at"]
            if sh["camera"] in VRM_INSERTS and move:
                item["push"] = INSERT_PUSH.get(move, 0.15) * float(sh.get("strength") or 1.0)
                if move == "dutch_tilt":
                    item["roll"] = float(sh.get("roll") or 10)
            if sh.get("extra"):
                item.update(sh["extra"])
            shots.append(item)
        sentences.append({"text": line["text"], **({"say": line["say"]} if line.get("say") else {}),
                          "show": line.get("show") or [], "shots": shots, "do": do, "vrm": vrm})
    return {"id": f"s3d-{plan.get('title') or 'series'}", "title": plan.get("title") or "", "hero": "alina", "look": "cozy",
            "vrm": {"heroine": key, "avatars": [s for s in plan["cast"][1:]]},
            "screen": {**copy.deepcopy(plate.get("screen") or {}), **copy.deepcopy(plan.get("screen") or {})},
            "highlight": dict((plan.get("captions") or {}).get("highlight") or {}),
            "pronounce": dict(plan.get("pronounce") or {}), "sentences": sentences}


# --------------------------------------------------------------------------- камера, игра, свет поверх спеки


def _cam_base(name: str) -> dict:
    from app.vtubika_vrm import CAMS

    c = CAMS.get(name) or CAMS["face"]
    pos, tgt = c["pos"], c["target"]
    dx, dy, dz = pos[0] - tgt[0], pos[1] - tgt[1], pos[2] - tgt[2]
    dist = math.sqrt(dx * dx + dy * dy + dz * dz)
    return {"target": list(tgt), "distance": round(dist, 4), "yaw": round(math.degrees(math.atan2(dx, dz)), 3),
            "height": round(dy, 4), "fov": c["fov"]}


def _split_keys(keys: list[dict]) -> list[list[dict]]:
    groups: list[list[dict]] = []
    for k in keys:  # ключи движка идут по порядку движений; каждое начинается с cut
        if k.get("cut") or not groups:
            groups.append([k])
        else:
            groups[-1].append(k)
    return groups


def _light_from(expanded: dict) -> dict:
    """Лампы развёрнутого пресета света → ключ и заполнение VRM (цвета, направление ключа к лицу)."""
    from app.vtubika_vrm import LIGHT

    subject = [0.0, 1.36, 0.0]
    best, best_score, fill = None, 0.0, None
    for lamp in expanded.get("lights") or []:
        kind, inten = lamp.get("type"), float(lamp.get("intensity") or 0)
        if kind in ("hemisphere", "ambient"):
            if fill is None or inten > float(fill.get("intensity") or 0):
                fill = lamp
            continue
        pos = lamp.get("position") or [0, 3, 2]
        d2 = sum((float(pos[i]) - subject[i]) ** 2 for i in range(3))
        score = inten if kind == "directional" else inten / max(0.25, d2)
        if score > best_score:
            best, best_score = lamp, score
    out = dict(LIGHT)
    if best:
        out["keyColor"] = best.get("color") or out["keyColor"]
        pos = best.get("position") or [0, 3, 2]
        v = [float(pos[i]) - subject[i] for i in range(3)]
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        key = [round(x / n, 3) for x in v]
        if key[2] < 0.15:  # свет сзади на MToon даёт тёмное лицо — ключ всегда спереди-сбоку
            key[2] = 0.35
        out["key"] = key
    if fill:
        out["fill"] = fill.get("color") or out["fill"]
        out["fillPower"] = round(min(1.2, max(0.45, 0.75 * float(fill.get("intensity") or 0.4) / 0.4)), 3)
    return out


def apply_presets(spec: dict, built_shots: list[dict], plan: dict, perf: list[dict], hero: str) -> dict:
    """Ключи камеры по движениям кадров, игра героини и свет пресета — одним разворотом движка."""
    moves, idx = [], []
    for i, sh in enumerate(spec["shots"]):
        src = (built_shots[i].get("s3d") if i < len(built_shots) else None) or {}
        name = sh["name"].split("_", 1)[1]
        if not src.get("move") or (sh.get("plate") or {}).get("insert"):
            continue
        params = {k: v for k, v in src.items() if k not in ("camera", "move", "at", "roll")}
        moves.append({"move": src["move"], "t0": float(sh["t0"]), "t1": float(sh["t1"]), **_cam_base(name), **params})
        idx.append(i)
    presets: dict[str, Any] = {"heading": 0, "subject": [0, 1.36, 0]}
    block = plan.get("presets") or {}
    for k in ("light", "mood"):
        if block.get(k):
            presets[k] = block[k]
    if moves:
        presets["camera"] = moves
    if perf:
        presets["performance"] = perf
    if len(presets) == 2:
        return {"camera": 0, "performance": 0, "light": None}
    mini = {"fps": int(spec.get("fps") or FPS), "duration": float(spec["duration"]),
            "characters": [{"id": hero, "style": "vrm", "position": [0, 0, 0], "timeline": {}}], "presets": presets}
    out = expand_presets(mini)
    applied = {"camera": 0, "performance": 0, "light": None}
    if moves:
        groups = _split_keys((out.get("camera") or {}).get("keys") or [])
        for i, keys in zip(idx, groups):
            sh = spec["shots"][i]
            t0 = float(sh["t0"])
            sh["camKeys"] = [{"t": round(max(0.0, float(k["t"]) - t0), 4), "pos": k["position"], "target": k["target"],
                              "fov": k.get("fov"), "roll": k.get("roll") or 0} for k in keys]
            applied["camera"] += 1
    tl = ((out.get("characters") or [{}])[0].get("timeline")) or {}
    if tl:
        mine = spec["timeline"]
        for key, items in tl.items():
            mine[key] = sorted([*(mine.get(key) or []), *items], key=lambda k: float(k.get("t", k.get("start", 0))))
        applied["performance"] = len(perf)
    if presets.get("light") or presets.get("mood"):
        light = _light_from(out)
        for sh in spec["shots"]:
            sh["light"] = {**(sh.get("light") or {}), **light}
        applied["light"] = light
    return applied


# --------------------------------------------------------------------------- субтитры и карточки
# Субтитры холст-клипа (app/s3d_clip.py) стоят по центру captions.y (доля кадра 1920), крупный шрифт (110 px) в
# одну-две строки: полоса ±CAPTION_HALF px. Карточки чата видео (вставка «чат крупно», карточка комментария)
# в эту полосу не заходят — иначе субтитры ложатся на свежие сообщения.

FRAME_H = 1920
CAPTION_HALF = 150


def caption_band(plan: dict) -> tuple[float, float]:
    """Полоса субтитров серии [y0, y1] в пикселях кадра 1080×1920."""
    y = float((plan.get("captions") or {}).get("y") or 0.8)
    c = y * FRAME_H
    return round(max(0.0, c - CAPTION_HALF), 1), round(min(float(FRAME_H), c + CAPTION_HALF), 1)


def keep_cards_clear(spec: dict, band: tuple[float, float]) -> int:
    """Вставкам «чат» — полосу субтитров (кладут карточку по свою сторону от неё); карточку комментария,
    задевающую полосу, — на другую сторону полосы. Возвращает, сколько карточек поправлено."""
    y0, y1 = band
    n = 0
    for sh in spec.get("shots") or []:
        ins = (sh.get("plate") or {}).get("insert")
        if isinstance(ins, dict) and ins.get("kind") == "chat":
            ins["avoid"] = [y0, y1]
            n += 1
    for o in spec.get("overlays") or []:
        if o.get("kind") != "chat":
            continue
        h = float(o.get("h") or 620)
        cy = float(o.get("y") or 560)
        top, bottom = cy - h / 2, cy + h / 2
        if bottom <= y0 - 24 or top >= y1 + 24:
            continue
        # Больше места выше полосы — карточка над субтитрами, иначе — под ними (не вылезая за кадр).
        if y0 >= FRAME_H - y1:
            o["y"] = round(max(h / 2 + 40, y0 - 36 - h / 2), 1)
        else:
            o["y"] = round(min(FRAME_H - h / 2 - 40, y1 + 36 + h / 2), 1)
        n += 1
    return n


# --------------------------------------------------------------------------- сборка спеки


def compile_vrm(plan: dict, lib: dict, words: list[dict], total: float, *, envelope: list[float] | None = None,
                fps: int = FPS) -> dict:
    """План + слова озвучки (с ``line``) → {spec, sfx, titles, transitions, lines, shots, applied, warnings}."""
    from app import vtubika_plan as P
    from app import vtubika_vrm as VRM
    from app import vtubika_vrm_stage as VS
    from app.s3d_voice import speech_windows

    chars = lib.get("characters") or {}
    hero = heroine_of(plan)
    key = f"s3d:{hero}"
    heroines = {key: heroine_entry(chars[hero])}
    avatars = {s: avatar_entry(chars[s]) for s in plan["cast"][1:]}
    vplan = to_vtubika(plan, lib, key)
    with _LOCK:
        old_h, old_a = VS.HEROINES, VS.AVATARS
        VS.HEROINES, VS.AVATARS = {**old_h, **heroines}, {**old_a, **avatars}
        try:
            built = P.build(vplan, words, total, fps=fps)
            spec = VRM.to_vrm_spec(vplan, built, words, total, fps=fps)
        except P.PlanError as exc:
            raise PlanError([f"раскладка по словам: {exc}"]) from exc
        finally:
            VS.HEROINES, VS.AVATARS = old_h, old_a
    times = P.token_times(vplan, words)
    warnings: list[str] = []

    # Рот: окна речи — только реплики героини; огибающая громкости — по этим же окнам.
    hero_words = [w for w in words if any(ln["id"] == w.get("line") and speaker(plan, ln) == hero for ln in plan["lines"])]
    windows = speech_windows(hero_words)
    spec["timeline"]["speech"] = [[a, b] for a, b in windows]
    if envelope:
        spec["timeline"]["mouthEnv"] = {"fps": 30, "start": 0, "values": envelope}

    # Вещи на переднем плане: локация и вещи плана.
    loc = (lib.get("locations") or {}).get(plan["location"]) or {}
    fg = {"cup": True, "mic": True, "cats": True, **((loc.get("plate") or {}).get("props") or {})}
    for slug in plan.get("props") or []:
        what = ((lib.get("props") or {}).get(slug) or {}).get("what")
        if what:
            fg[what] = True
    for sh in spec["shots"]:
        plate = sh.get("plate") or {}
        if plate.get("fgDesk"):
            plate["fgDesk"]["cup"] = bool(plate["fgDesk"].get("cup") and fg.get("cup"))
            plate["fgDesk"]["mic"] = bool(plate["fgDesk"].get("mic") and fg.get("mic"))
        if "cats" in plate:
            plate["cats"] = bool(plate["cats"] and fg.get("cats"))
        light = (loc.get("plate") or {}).get("light") or {}
        if light:
            sh["light"] = {**(sh.get("light") or {}), **{k: v for k, v in light.items() if k != "rim"}}
    if not fg.get("cats"):
        spec["cats"] = []
    keep_cards_clear(spec, caption_band(plan))

    # Игра: пресеты эмоций и жестов героини на словах реплик.
    perf: list[dict] = []
    for n, line in enumerate(plan["lines"]):
        st = times[n]
        for cue in line.get("performance") or []:
            if cue.get("who", hero) != hero:
                warnings.append(f"реплика {line['id']}: игра «{cue['preset']}» у {cue['who']} — на мониторе не играется")
                continue
            t = round(P.anchor(st, cue.get("at"), n) + float(cue.get("dt") or 0), 3)
            perf.append({"who": hero, "preset": cue["preset"], "t": t,
                         **({"dur": cue["dur"]} if cue.get("dur") else {}),
                         **({"intensity": cue["intensity"]} if cue.get("intensity") is not None else {}),
                         **{k: cue[k] for k in ("hand", "side") if cue.get(k) in ("left", "right")}})
    loc_light = bool((loc.get("plate") or {}).get("light"))
    applied = apply_presets(spec, built["shots"], plan, perf, hero)
    if loc_light and applied.get("light"):
        for sh in spec["shots"]:  # явный свет локации сильнее пресета
            sh["light"].update({k: v for k, v in (loc.get("plate") or {}).get("light", {}).items() if k != "rim"})

    # Заголовки, звуки, переходы — для холст-клипа.
    titles, sfx, transitions = [], [dict(x) for x in built["sfx"]], []
    for n, line in enumerate(plan["lines"]):
        st = times[n]
        for ev in line.get("events") or []:
            t = round(P.anchor(st, ev.get("at"), n) + float(ev.get("dt") or 0), 3)
            if ev.get("kind") == "title":
                titles.append({"t": max(0.0, t), "dur": float(ev.get("dur") or 2.6), "text": str(ev["text"]), "line": line["id"]})
            elif ev.get("kind") == "sfx" and str(ev.get("sound", "")).startswith("sfx:"):
                sfx.append({"t": max(0.0, t), "ref": ev["sound"], "volume": float(ev.get("volume") or 0.5), "name": ev["sound"]})
        if line.get("transition"):
            first = next((s for s in built["shots"] if s.get("n") == n), None)
            if first is None or float(first["t0"]) <= 0.05:
                warnings.append(f"реплика {line['id']}: переход без склейки (кадр слит с соседним) — пропущен")
                continue
            transitions.append({"t": float(first["t0"]), **line["transition"], "line": line["id"]})
    lines = []
    for n, (line, s) in enumerate(zip(plan["lines"], built["sentences"])):
        lines.append({"id": line["id"], "who": line["who"], "text": line["text"], "t0": s["t0"], "t1": s["t1"],
                      "shots": s["shots"]})
    shots = [{"name": s["name"], "t0": s["t0"], "t1": s["t1"], "insert": bool((s.get("plate") or {}).get("insert")),
              "move": (built["shots"][i].get("s3d") or {}).get("move") if i < len(built["shots"]) else None}
             for i, s in enumerate(spec["shots"])]
    return {"spec": spec, "sfx": sorted(sfx, key=lambda x: x["t"]), "titles": titles, "transitions": transitions,
            "lines": lines, "shots": shots, "applied": applied, "warnings": warnings}
