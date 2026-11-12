"""История «Витубики» в стиле VRM «под 2D-аниме» (node/vrm): план → собранная сцена (``vtubika_plan.build``) →
спека VRM-рендера. Время, кадры, экран монитора, HUD, телефон-вставка и звуки — те же, что у истории на воксельном
движке; героиня — VRM-модель на мокапе CMU (сидя, печать, смех) с IK-руками на клавиатуре, лицо — выражения VRM.

Ракурсы плана → кадры VRM: ``wide`` — нарисованная комната целиком (героиня со спины), ``face*``/``smile`` — крупные
планы (руки вне кадра), ``ots`` — через плечо на монитор, ``insert`` — средний план под телефоном-вставкой;
``monitor*``/``chat``/``tablet``/``jar`` — вставки без героини (страницы эфира, чат, планшет с эскизом, копилка).
Руки видны только там, где выглядят хорошо: на клавиатуре в среднем плане и подбородок на кулаке.
"""

from __future__ import annotations

from typing import Any

MODEL = "VRM1_Constraint_Twist_Sample.vrm"
LOOK = {"outline": 0.0034, "shade": "#a28ad6", "toony": 0.97, "shift": -0.05, "rim": "#ff8fd0", "rimPower": 4,
        "tint": {"Tops": "#8e86c8"}}
CHAIR = {"pos": [0, 0.79, -0.24], "h": 0.56, "w": 0.6}
SURFACES = [
    {"name": "стол", "y": 1.05, "x0": -0.8, "x1": 0.8, "z0": 0.22, "z1": 0.92},
    {"name": "клавиатура", "y": 1.072, "x0": -0.22, "x1": 0.22, "z0": 0.286, "z1": 0.434},
    {"name": "мышь", "y": 1.082, "x0": -0.39, "x1": -0.33, "z0": 0.375, "z1": 0.465},
]
PROPS = {"desk": {"y": 1.05, "z": 0.22}, "keyboard": {"pos": [0, 1.061, 0.36]}, "mouse": {"pos": [-0.36, 1.065, 0.42]},
         "chair": CHAIR}
LIGHT = {"key": [0, 0.6, 1], "keyColor": "#f2ecff", "keyPower": 2.1, "fill": "#7a64b8", "fillPower": 0.75}
MON, CHAT, WEBCAM, TABLET, JAR = [0, 1.43, 1.15], [-0.5, 1.36, 0.9], [0.0, 1.5, 1.1], [0.18, 1.06, 0.35], [-0.32, 1.1, 0.42]

# Ракурс → камера (начало), угол обзора, кадр-подложка. Точки — пространство модели (героиня в начале координат).
CAMS: dict[str, dict] = {
    "face": {"pos": [0.36, 1.42, 2.6], "target": [-0.02, 1.36, 0], "fov": 13, "plate": {"dx": 60, "dy": -40, "zoom": 1.15}},
    "face_left": {"pos": [-0.42, 1.42, 2.5], "target": [0.02, 1.36, 0], "fov": 13, "plate": {"dx": -80, "dy": -40, "zoom": 1.15}},
    "face_close": {"pos": [0.15, 1.42, 2.6], "target": [0, 1.39, 0], "fov": 9.5, "plate": {"dx": 0, "dy": -60, "zoom": 1.25}},
    "smile": {"pos": [0.25, 1.4, 2.6], "target": [-0.02, 1.35, 0], "fov": 11.5, "plate": {"dx": 30, "dy": -50, "zoom": 1.2}},
    # Средний план «оба на экране»: героиня ниже центра (сверху — карточка эфира с аватаром), руки на клавиатуре и
    # машущая рука в кадре.
    "two": {"pos": [-0.04, 1.43, 2.5], "target": [-0.05, 1.42, 0.05], "fov": 24, "plate": {"dx": 20, "dy": -30, "zoom": 1.1}},
    "insert": {"pos": [0.26, 1.46, 2.45], "target": [-0.02, 1.25, 0.2], "fov": 19, "plate": {"dx": 40, "dy": -40, "zoom": 1.05}},
    "typing": {"pos": [0.26, 1.46, 2.45], "target": [-0.02, 1.22, 0.25], "fov": 20.5, "plate": {"dx": 40, "dy": -40, "zoom": 1.05}},
    "ots": {"pos": [-0.85, 1.85, -0.9], "target": [0.25, 1.24, 0.42], "fov": 28,
            "plate": {"kind": "ots", "cx": 735, "cy": 1060, "zoom": 2.2, "charBlur": 1.2, "bloom": 0.3,
                      "anchor": {"bone": {"bone": "head"}, "at": [560, 1390]}}},
    "wide": {"pos": [0.6, 3.585, -12.99], "target": [0, 0.985, 0.0], "fov": 14,
             "plate": {"kind": "wide", "cx": 720, "cy": 1300, "zoom": 0.84, "anchor": {"bone": {"bone": "head"}, "at": [640, 1170]}}},
}
INSERTS = {"monitor", "monitor_close", "chat", "tablet", "jar"}

# Действия героини (клипы воксельного движка) → руки, взгляд, мокап корпуса.
ACT_ARMS = {"Typing_Loop": ("keys", "keys"), "Chin_Rest_Loop": ("idle", "chin")}
ACT_LOOK = {"Talk_Loop": WEBCAM, "Draw_Loop": TABLET, "Coin_Drop": JAR, "Typing_Loop": MON, "Chin_Rest_Loop": MON,
            "Laugh_Loop": WEBCAM, "Gasp": WEBCAM}
FACES = {"neutral": {}, "talk": {"relaxed": 0.2}, "happy": {"happy": 0.55}, "laugh": {"happy": 1.0, "aa": 0.4},
         "frown": {"angry": 0.55, "sad": 0.25}, "wide": {"surprised": 0.9}, "tired": {"relaxed": 0.5}, "blink": {},
         "sleep": {"blink": 1}, "yawn": {"aa": 0.8, "blink": 1}, "blink_once": {}}


def _cam(name: str, t0: float, t1: float, push: float, roll: float) -> dict:
    c = CAMS[name]
    end_fov = c["fov"] * (1 - 0.55 * push)
    keys = [{"t": 0, "pos": c["pos"], "target": c["target"], "fov": c["fov"], "roll": roll * 0.4},
            {"t": round(t1 - t0, 3), "pos": c["pos"], "target": c["target"], "fov": round(end_fov, 3), "roll": roll}]
    plate = {"kind": "front", **c["plate"]}
    if plate["kind"] == "front":
        plate["fgDesk"] = {"y": 1690, "cup": name in ("typing", "insert"), "mic": name not in ("face_close", "two")}
        plate["cats"] = name in ("two", "typing", "insert")  # кот на краю стола — только там, где виден стол
    return {"camKeys": keys, "plate": plate}


def _act_windows(actions: list[dict]) -> list[tuple[float, float, str]]:
    return [(float(a["start"]), float(a["end"]), a["clip"]) for a in actions]


def to_vrm_spec(plan: dict, built: dict, words: list[dict], total: float, *, fps: int = 30) -> dict:
    """Собранная сцена истории → спека node/vrm (тот же экран, оверлеи и кадры). Героиня, аватар на мониторе,
    кот, кивки и жесты — ``app.vtubika_vrm_stage`` (поле ``vrm`` плана)."""
    from app import vtubika_vrm_stage as VS

    her = VS.heroine(plan)
    chair = {**CHAIR, "pos": [CHAIR["pos"][0], her["chair_y"], CHAIR["pos"][2]]}
    props = {**PROPS, "chair": chair}
    extra = VS.stage(plan, words, total)
    spec = built["spec"]
    objs = {o["id"]: o for o in spec["objects"]}
    stream = {k: v for k, v in objs["monitor_screen"]["params"].items() if k not in ("kind", "size", "px")}
    hero = spec["characters"][0]
    actions = _act_windows(hero.get("actions") or [])
    faces = hero.get("faces") or []
    speech_end = float(built.get("speech_end") or total)

    # Кадры: из плана, последний — общий план в хвосте (начало и конец ролика — общие).
    shots_in = [dict(s) for s in built["shots"]]
    if shots_in and shots_in[-1]["cam"] != "wide" and total - speech_end > 1.0:
        tail = round(speech_end + 0.35, 3)
        shots_in[-1]["t1"] = tail
        shots_in.append({"cam": "wide", "t0": tail, "t1": round(total, 3), "push": 0.1, "roll": 0})
    acts_mid = lambda a, b: max(((min(b, e) - max(a, s), c) for s, e, c in actions if s < b and e > a), default=(0, "Sit_Loop"))[1]  # noqa: E731
    jar = objs.get("jar", {}).get("params", {})
    shots: list[dict] = []
    for i, sh in enumerate(shots_in):
        cam, t0, t1 = sh["cam"], float(sh["t0"]), float(sh["t1"])
        push, roll = float(sh.get("push", 0.15)), float(sh.get("roll", 0.0))
        if cam in INSERTS:
            ins = {"kind": cam, "t0": t0, "t1": t1, "push": push, "roll": roll}
            if cam == "jar":
                ins.update({"drops": jar.get("drops", []), "coins": jar.get("coins", 10)})
            if sh.get("sketch"):
                ins["sketch"] = True
            if sh.get("zoom"):
                ins["zoom"] = float(sh["zoom"])
            shots.append({"name": f"{i:02d}_{cam}", "t0": t0, "t1": t1, "cam": CAMS["face"],
                          "plate": {"kind": "front", "insert": ins}, "props": props, "light": LIGHT})
            continue
        name = cam if cam in CAMS else "face"
        if name == "ots" and acts_mid(t0, t1) != "Typing_Loop":
            name = "ots"
        if name in ("face", "face_left") and acts_mid(t0, t1) == "Typing_Loop":
            name = "typing"  # печать — средним планом: руки на клавиатуре видны
        made = {"name": f"{i:02d}_{name}", "t0": t0, "t1": t1, **_cam(name, t0, t1, push, roll), "props": props, "light": LIGHT}
        glide = float(sh.get("glide") or 0)
        prev = shots[-1] if shots else None
        if glide > 0 and prev and not (prev.get("plate") or {}).get("insert") and prev.get("camKeys"):
            # Плавный переезд камеры вместо склейки: план начинается камерой конца прошлого и за glide с приезжает
            # в свою (дальше — свой наезд). Подложка-комната отрисована той же камерой, кадр за кадром.
            end = prev["camKeys"][-1]
            own = made["camKeys"]
            g = round(min(glide, (t1 - t0) * 0.6), 3)
            made["camKeys"] = [{**end, "t": 0}, {**own[0], "t": g}, own[-1]]
        shots.append(made)

    # Руки: по действиям; по умолчанию — лежат на клавишах без набора.
    arms = [{"t": -3, "left": "idle", "right": "idle"}]
    for s, e, c in actions:
        left, right = ACT_ARMS.get(c, ("idle", "idle"))
        if (arms[-1]["left"], arms[-1]["right"]) != (left, right):
            arms.append({"t": round(s, 3), "dur": 0.8, "left": left, "right": right})
    # Взгляд: по действию (вебка, планшет, банка, монитор); на чат — когда в чате новое сообщение.
    look = [{"t": -3, "target": MON, "head": 0.6}]
    for s, _e, c in actions:
        tgt = ACT_LOOK.get(c, MON)
        if tgt != look[-1]["target"]:
            look.append({"t": round(s, 3), "target": tgt, "head": 0.7})
    for m in stream.get("chat") or []:
        if float(m.get("t", 0)) > 0.5:
            look.append({"t": round(float(m["t"]) + 0.1, 3), "target": CHAT, "head": 0.8})
            look.append({"t": round(float(m["t"]) + 1.6, 3), "target": MON, "head": 0.6})
    look.sort(key=lambda k: k["t"])
    arms = VS.merge_arms(arms, extra["arms"])
    look = VS.merge_look(look, extra["looks"])
    # Мокап: сидя (вертикальный кусок клипа «туда-обратно»), печать — корпус, смех — корпус слегка.
    tracks: list[dict] = [
        {"clip": "sit", "start": -2, "end": total + 1, "offset": 1.0, "speed": 0.5, "pingpong": [1.0, 7.5, 21], "smooth": 0.15,
         "fadeIn": 0, "fadeOut": 0, "mask": ["spine", "chest", "upperChest", "leftShoulder", "leftUpperArm", "leftLowerArm",
                                             "leftHand", "rightShoulder", "rightUpperArm", "rightLowerArm", "rightHand"]},
        {"clip": "sit", "start": -2, "end": total + 1, "offset": 1.0, "speed": 0.5, "pingpong": [1.0, 7.5, 21], "smooth": 0.2,
         "fadeIn": 0, "fadeOut": 0, "mask": "head", "scale": 0.3},
    ]
    for s, e, c in actions:
        if c == "Typing_Loop":
            tracks.append({"clip": "typing", "start": s, "end": e, "mask": ["spine", "chest", "upperChest"], "scale": 0.45,
                           "fadeIn": 0.5, "fadeOut": 0.5, "smooth": 0.15})
        if c == "Laugh_Loop":
            tracks.append({"clip": "laugh", "start": s, "end": e, "offset": 0.3, "speed": 0.6, "pingpong": [0.3, 0.9, 15],
                           "mask": ["spine", "chest", "upperChest"], "scale": 0.3, "fadeIn": 0.4, "fadeOut": 0.6, "smooth": 0.15})
    # Лицо: выражения с наплывом; «моргнуть» — короткое закрытие глаз.
    fk: list[dict] = []
    for f in faces:
        name = f["face"]
        if name == "blink_once":
            prev = fk[-1]["expr"] if fk else {}
            fk += [{"t": round(f["t"], 3), "dur": 0.08, "expr": {**prev, "blink": 1}},
                   {"t": round(f["t"] + 0.16, 3), "dur": 0.1, "expr": prev}]
            continue
        fk.append({"t": round(float(f["t"]), 3), "dur": 0.3, "expr": FACES.get(name, {})})
    # Рот слогами: слова озвучки, пока героиня «говорит в эфир» (лицо talk).
    talk = []
    for i, f in enumerate(faces):
        if f["face"] == "talk":
            end = float(faces[i + 1]["t"]) if i + 1 < len(faces) else total
            talk.append((float(f["t"]), end))
    speech = [[round(w["start"], 3), round(w["end"], 3)] for w in words
              if any(a <= w["start"] < b for a, b in talk)]
    timeline = {"clips": {n: f"cmu/clips/{n}.json" for n in ("sit", "typing", "laugh")}, "chair": chair, "tracks": tracks,
                "arms": arms, "hands": [{"t": -3, "left": "relaxed", "right": "relaxed"}], "faces": fk, "look": look,
                "speech": speech, "surfaces": SURFACES, "nods": extra["nods"], "bows": extra["bows"]}
    out = {"width": 1080, "height": 1920, "fps": int(fps), "duration": round(total, 3), "model": her["model"], "look": her["look"],
           "stream": stream, "overlays": spec.get("overlays") or [], "headphones": her["headphones"], "strictContact": True,
           "timeline": timeline, "shots": shots, "cats": extra["cats"]}
    if extra.get("avatar"):
        out["avatar"] = extra["avatar"]
    # Настоящие скриншоты сайта во вставке покупки (если сняты): телефон «shop» → «site».
    from app import vtubika_site as SITE

    site = SITE.apply(out["overlays"], plan)
    if site:
        out["siteShots"] = site["urls"]  # страницы всех покупок истории (основной набор и site_shots.sets)
        out["siteDir"] = site["dir"]
    # Купленная модель в эфире (аватар product) — картинки со страницы товара из той же папки снимков.
    product = (stream.get("product") or {}).get("faces") or {}
    if product:
        if not out.get("siteDir"):
            raise ValueError("screen.product: картинки товара берутся из папки site_shots.dir — её нет")
        out["siteShots"] = list(dict.fromkeys([*(out.get("siteShots") or []), *(f["url"] for f in product.values())]))
    return out


# --------------------------------------------------------------------------- проверки готового рендера
# Рендер под нагрузкой однажды потерял контекст GPU: героиня и комната пропали после ~9 с, а отчёт был «ok»
# (проверки касаний считаются отдельным браузером и сам ролик не видят). Здесь — что рендер и проверки обязаны
# сообщить, чтобы ролик пошёл дальше; любое «не измерено» — ошибка, а не «всё хорошо».


class VrmRenderError(RuntimeError):
    """Рендер VRM или его проверки ничего не измерили / нашли кадры без героини."""


def hero_frames(spec: dict, fps: int | None = None) -> int:
    """Сколько кадров ролика героиня должна быть в кадре (планы без вставки)."""
    fps = int(fps or spec.get("fps") or 30)
    n = round(float(spec.get("duration") or 0) * fps)
    shots = spec.get("shots") or []
    if not shots or not n:
        return 0
    count = 0
    for i in range(n):
        t = i / fps
        sh = next((s for s in shots if float(s.get("t0", 0)) <= t < float(s.get("t1", 1e9))), shots[-1])
        if not (sh.get("plate") or {}).get("insert"):
            count += 1
    return count


def render_problems(rep: dict | None, spec: dict) -> list[str]:
    """Отчёт render.mjs → причины забраковать ролик (пустой список — годен)."""
    if not rep:
        return ["рендер не вернул отчёт"]
    out = []
    if not rep.get("ok"):
        out.append(f"рендер не ok (чёрные кадры: {len(rep.get('black_frames') or [])})")
    need = hero_frames(spec, rep.get("fps"))
    pres = rep.get("presence")
    if need and not pres:
        out.append("рендер не сообщил, была ли героиня в кадре (нет presence — старый бандл?)")
    elif pres:
        if int(pres.get("missing") or 0):
            out.append(f"героини нет на {pres['missing']} кадрах: {pres.get('first_missing')}")
        if int(pres.get("frames") or 0) != int(rep.get("frames") or -1):
            out.append(f"присутствие проверено на {pres.get('frames')} кадрах из {rep.get('frames')}")
        if int(pres.get("expected") or 0) != need:
            out.append(f"героиня ожидалась на {need} кадрах, рендер проверил {pres.get('expected')}")
    return out


def check_problems(checks: dict | None, spec: dict) -> list[str]:
    """Отчёт contact.mjs → пустые проверки (пальцы, кресло, волосы, руки) при героине в кадре — причины ошибки.
    Нарушения зазоров (ok: false при полных замерах) сюда не входят — их показывает отчёт истории."""
    if not hero_frames(spec) or not spec.get("timeline"):
        return []
    if not checks:
        return ["проверки касаний не вернули отчёт"]
    if checks.get("error"):
        return [f"проверки касаний упали: {str(checks['error']).strip()[-300:]}"]
    out = list(checks.get("empty") or [])
    if not int(checks.get("frames") or 0):
        out.append("проверки: 0 кадров")
    tl = spec["timeline"]
    blank = lambda d: not isinstance(d, dict) or d.get("min_gap_mm") is None  # noqa: E731
    if tl.get("surfaces") and blank(checks.get("fingers")):
        out.append("пальцы: нет замеров")
    if tl.get("chair") and blank(checks.get("chair")):
        out.append("кресло: нет замеров")
    if tl.get("chair") and blank(checks.get("hair")):
        out.append("волосы: нет замеров")
    if spec.get("handCheck") is not False and not checks.get("hands"):
        out.append("руки: нет замеров")
    return list(dict.fromkeys(out))


def require_render(rep: dict | None, spec: dict) -> None:
    """Ошибка с понятной причиной, если в отрендеренном ролике героини нет там, где она должна быть."""
    probs = render_problems(rep, spec)
    if probs:
        raise VrmRenderError("VRM-рендер забракован: " + "; ".join(probs))


def require_checks(checks: dict | None, spec: dict) -> None:
    """Ошибка с понятной причиной, если проверки касаний ничего не измерили."""
    probs = check_problems(checks, spec)
    if probs:
        raise VrmRenderError("проверки VRM пустые: " + "; ".join(probs))
