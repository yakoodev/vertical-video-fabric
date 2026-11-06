"""3D-серии «сериал-катастрофа» на своём движке (node/scene3d): сценарий + тайминги слов озвучки → одна
JSON-сцена движка (функция времени) → mp4. Без видео-нейронок и без платного: всё считается правилами.

Режиссура (``direct``):

* **Локация** — шаблон из примитивов в лоу-поли стиле (``LOCATIONS``): лифт (кабина в шахте) или квартира;
  выбирается по тексту (``pick_location``). У шаблона — места героя (``spots``), точки интереса (кнопки, двери,
  табло, люк, окно), основной и аварийный свет, светящиеся вещи (гаснут при блэкауте).
* **Герой** — игрушка из скруглённых деталей на скелете Quaternius (style ``toy``) с анимациями UAL; одна
  яркая вещь из карточки героя (``hero_look``: «yellow hooded raincoat» → жёлтый дождевик с капюшоном).
* **Кадры** — по фразам озвучки (длинная фраза делится на два кадра по слову); у кадра действие героя по словам
  фразы (кнопки → панель и Interact, фонарик → телефон в руке и луч, двери → толкает створки, сел → присел в
  углу, потолок/люк → смотрит вверх…), место и камера: по умолчанию **близко за спиной на уровне плеч**,
  крупный план лица — на реакции, деталь (кнопки, табло) — когда о ней говорят, общий план — **только сверху**
  (срез потолка). Соседние кадры — разные ракурсы. Камера всегда внутри декорации (проверка
  ``check_shots``: камера не в стене, голова героя в кадре, герой не мелкий).
* **События на словах** — на начале нужного слова: «погас/блэкаут» — основной свет мигнул и погас, табло и
  кнопки потухли, через миг — красный аварийный (щелчок рубильника, гул кабины глохнет); «трясёт/рывок» —
  тряска камеры и лязг; «заскрежетало/скрип» — скрежет металла и лёгкая тряска; «мигает» — мерцание;
  «вспышка/искры» — вспышка; «стук» — стук в металлическую дверь (серия из трёх); «N этаж» и «табло … N» —
  цифры на табло; «Была лишь одна проблема» — акцент. Звуки — только настоящие записи паков (``SFX``); фон-петли
  (``AMBIENCE``): гул кабины, пока горит свет, в темноте — далёкий гул шахты. Тревожную музыку под голос
  кладёт сборка (app/stories_3d.py).

``render_episode`` — сцена → mp4 через ``app.scene3d.render_scene``. Сборка серии (субтитры по 1–3 слова,
HUD-плашки, название, титр «ЧАСТЬ N+1», звуки, голос) — холст-клип сервиса (``app.stories_gen``).

Где что лежит. Здесь — постановка плана ``direct``/``_direct``. Вынесено: геометрия, герой и локации ``app/series3d_scene.py``, слова и события
``app/series3d_words.py``, камера и проверка кадра ``app/series3d_camera.py``. Те модули этот не импортируют, а он их — поэтому все имена по-прежнему доступны отсюда.
"""

from __future__ import annotations

import math
from typing import Any

from app.series3d_scene import (  # noqa: F401
    WIDTH, HEIGHT, ASPECT, HERO_ID, HERO_HEIGHT, CHAR_MODEL, CHAR_MODEL_FEMALE, ANIMATIONS, STYLE, POST, COLORS,
    HAIR_COLORS, HERO_DEFAULT, HERO_MESH, DirectionError, _sub, _add, _dot, _cross, _norm, _r, project, _yaw_vec,
    _yaw_to, _color, hero_look, mesh_hero, _character, _box, _cyl, _on, elevator_location, apartment_location,
    _LOCATION_WORDS, pick_location,
)
from app.series3d_words import (  # noqa: F401
    SHOT_MIN_SEC, SHOT_SPLIT_SEC, DETAIL_WORDS, _wnorm, align_words, sentence_spans, split_shots, find_word,
    ACTIONS, EVENTS, _action_for, detect_events,
)
from app.series3d_camera import (  # noqa: F401
    CAMERA_MARGIN, FOV, HEAD_R, CLIP_HEAD, _inside, camera_ok, CLIP_HEAD_MESH, HEAD_R_MESH, _HEAD, _head_for,
    head_of, head_r, hero_points, frame_report, _seg_dist2d, _blocks, _camera_for, _behind_its_wall, _check,
)

FPS = 30


WALK_SPEED = 1.1  # м/с


FILL, FILL_DARK = 0.9, 0.35
DOF = {"face": 1.0, "detail": 1.3, "low": 0.5}  # сила размытия по ракурсу (ключ камеры dof)


# Звуки событий — настоящие записи паков (tension — лифт и тревожный фон CC0, freesound — монтажные CC0,
# kenney-impact — удары/шаги CC0).
SFX = {
    "blackout": ("sfx:tension/breaker.ogg", 1.0),  # щелчок рубильника — свет отключился
    "power_down": ("sfx:tension/power-down.ogg", 0.8),  # гул кабины проседает и глохнет
    "jolt": ("sfx:tension/clank.ogg", 1.0),  # рывок кабины — лязг
    "jolt2": ("sfx:kenney-impact/impactMetal_heavy_003.ogg", 0.8),
    "scrape": ("sfx:tension/metal-scrape.ogg", 0.9),  # «заскрежетало» — трос/металл
    "flash": ("sfx:freesound/impact-cinematic.ogg", 0.7),
    "knock": ("sfx:tension/knock-metal.ogg", 1.0),  # стук в металлическую дверь (на записи — три удара)
    "press": ("sfx:freesound/camera-2.ogg", 0.5),
    "sting": ("sfx:freesound/sting.ogg", 0.75),
    "tension": ("sfx:freesound/tension.ogg", 0.45),
    "step": ("sfx:kenney-impact/footstep_concrete_001.ogg", 0.5),
    "door": ("sfx:kenney-impact/impactMetal_medium_002.ogg", 0.7),
}
# Фон-петли локации (пак tension): ``(ref, на сколько дБ тише голоса)``. Гул кабины — пока горит свет, после
# блэкаута — далёкий гул шахты (до «свет загорелся» или до конца).
AMBIENCE = {
    "hum": ("sfx:tension/cabin-hum.ogg", 26.0),
    "dark": ("sfx:tension/shaft-rumble.ogg", 24.0),
}
KNOCKS = 1  # записей стука подряд: на записи уже серия из трёх ударов

_NUM_WORDS = {"двадцать два": 22, "двадцать три": 23, "двадцать один": 21, "тринадцать": 13}


LOCATIONS = {"elevator": elevator_location, "apartment": apartment_location}


ONE_SHOT_SEC = {"Interact": 1.6, "Punch_Jab": 0.8, "Hit_Chest": 1.0}
PHONE_CLIP = "Idle_Lantern_Loop"
PHONE_LIGHT = 6.0  # луч фонарика телефона (кд)  # рука с телефоном вперёд на уровне груди — луч фонарика туда, куда смотрит герой


# --------------------------------------------------------------------------- #
# Режиссура
# --------------------------------------------------------------------------- #


def _kinds_for(shots: list[dict], acts: list[tuple]) -> list[str]:
    """Ракурсы по ролям и действиям: первый кадр — общий сверху; деталь, когда говорят о вещи; реакция —
    лицо; финал — лицо, потом деталь; по умолчанию — за спиной. Соседние — разные."""
    out = []
    sat = False
    for n, (shot, act) in enumerate(zip(shots, acts)):
        name = act[0]
        role = shot.get("role") or ""
        if n == 0:
            prefs = ["overhead"]
        elif name in ("doors", "knock"):
            # Только из кабины: камера за срезанной стеной спрятала бы и сами двери — герой толкал бы воздух.
            prefs = ["ots"]
        elif name == "display":
            prefs = ["detail", "ots"]
        elif name == "press":
            prefs = ["detail", "ots"] if shot.get("part") == 1 else ["ots", "detail", "face"]
        elif name == "look_up":
            prefs = ["low", "face", "ots"]
        elif name in ("jolt", "listen") or role == "mistake":
            prefs = ["face", "ots", "low"]
        elif name == "sit":
            # Первый раз сел — лицо; дальше (качнулась, опустилась) — общий сверху: кабина и герой в углу.
            prefs = ["overhead", "face", "low"] if sat else ["face", "overhead", "low"]
            sat = True
        elif role == "finale":
            prefs = ["face", "ots"]
        else:
            prefs = ["ots", "face"]
        out.append(prefs)
    return out


def _order(prefs: list[str], history: list[str]) -> list[str]:
    """Порядок ракурсов кадра: свои предпочтения, но не как у прошлого кадра (за спиной — не больше двух
    подряд), потом запасные."""
    prev = history[-1] if history else None
    streak = len(history) >= 2 and history[-1] == history[-2] == "ots"
    # Лицо дважды подряд можно (реакция за реакцией — другой ракурс), трижды — нет.
    faces = len(history) >= 2 and history[-1] == history[-2] == "face"
    first = [k for k in prefs if k != prev or (k == "ots" and not streak) or (k == "face" and not faces)]
    rest = [k for k in ("ots", "face", "low", "overhead") if k not in first]
    return first + [k for k in prefs if k not in first] + rest


def _light_keys(base: float, off_at: float | None, back_at: float | None = None) -> list[dict] | None:
    if off_at is None:
        return None
    keys = [{"t": 0, "intensity": base}, {"t": round(off_at, 3), "intensity": base},
            {"t": round(off_at + 0.04, 3), "intensity": 0.0, "ease": "linear"}]
    if back_at is not None and back_at > off_at:
        keys += [{"t": round(back_at, 3), "intensity": 0.0}, {"t": round(back_at + 0.25, 3), "intensity": base}]
    return keys


def direct(script: dict, words: list[dict], *, total: float | None = None, look: dict | None = None,
           location: str | None = None, fps: int = FPS) -> dict:
    """Сценарий + слова озвучки → ``{spec, shots, events, sfx, location, checks}``. ``spec`` — сцена
    движка на всю серию (``total`` секунд), ``sfx`` — звуки событий ``[{t, ref, volume, label}]``."""
    look = look or hero_look("")
    token = _HEAD.set(_head_for(look))
    try:
        return _direct(script, words, total=total, look=look, location=location, fps=fps)
    finally:
        _HEAD.reset(token)


def _direct(script: dict, words: list[dict], *, total: float | None, look: dict, location: str | None,
            fps: int) -> dict:
    sentences = [s for s in (script.get("sentences") or []) if str(s.get("text") or "").strip()]
    if not sentences:
        raise DirectionError("в сценарии нет фраз")
    if not words:
        raise DirectionError("нет слов озвучки")
    total = float(total or (float(words[-1]["end"]) + 2.0))
    text = " ".join([str(script.get("title") or ""), *[s["text"] for s in sentences]])
    loc_name = location if location in LOCATIONS else pick_location(text)
    loc = LOCATIONS[loc_name]()
    look = look or hero_look("")
    words = align_words(" ".join(s["text"] for s in sentences), words)
    spans = sentence_spans(sentences, words, total)
    shots = split_shots(spans, words)
    events = detect_events(spans, words)
    acts = [_action_for(s.get("spoken") or s["text"], s.get("role") or "") for s in shots]
    # Финал после вставки «табло/вещь» без своего действия — камера остаётся на вещи (жуткая деталь держится).
    n = 1
    while n < len(shots):
        if shots[n].get("role") == "finale" and acts[n][0] == "idle" and acts[n - 1][0] == "display":
            shots[n - 1] = {**shots[n - 1], "t1": shots[n]["t1"], "w1": shots[n]["w1"],
                            "spoken": f"{shots[n - 1].get('spoken') or ''} {shots[n].get('spoken') or ''}".strip()}
            del shots[n], acts[n]
            continue
        n += 1
    for k, s in enumerate(shots):
        s["n"] = k
    kinds = _kinds_for(shots, acts)

    blackout = next((e["t"] for e in events if e["kind"] == "blackout"), None)
    light_back = next((e["t"] for e in events if e["kind"] == "light_back" and blackout and e["t"] > blackout), None)
    dark = (lambda t: blackout is not None and t >= blackout and (light_back is None or t < light_back))

    # Герой: путь по местам кадров, действия и позы.
    path: list[dict] = []
    facing: list[dict] = []
    actions: list[dict] = []
    poses: list[dict] = []
    phone_windows: list[list[float]] = []
    prev_spot = None
    shot_out: list[dict] = []
    checks: list[str] = []
    notes: list[str] = []
    side = 1
    for n, (shot, act, kind) in enumerate(zip(shots, acts, kinds)):
        name, _pat, clip, spot_name, focus_name, one_shot = act
        t0, t1 = shot["t0"], shot["t1"]
        if n + 1 < len(shots):
            t1 = shots[n + 1]["t0"]
        else:
            t1 = total
        # Темно и действие «фонарик/звонок» не названо — герой всё равно светит телефоном (иначе ничего не видно);
        # смотрит вверх/слушает — с телефоном в руке.
        phone = name in ("phone_light", "call")
        if dark(t0 + 0.3) and name in ("idle", "listen", "look_up", "display", "wait"):
            phone, clip = True, PHONE_CLIP
            if name in ("idle", "wait"):
                name = "phone_light"
        spot = list(loc["spots"].get(spot_name) or loc["spots"]["center"])
        if focus_name in loc["targets"] and spot_name in ("center", "back") and name not in ("sit",):
            # Лицом к точке интереса.
            fp = loc["targets"][focus_name][0]
            spot[2] = round(_yaw_to([spot[0], 0, spot[1]], fp), 1)
        walk = 0.0
        if prev_spot is None:
            path.append({"t": 0, "p": [spot[0], spot[1]]})
            facing.append({"t": 0, "yaw": spot[2], "dur": 0.01})
        elif abs(prev_spot[0] - spot[0]) + abs(prev_spot[1] - spot[1]) > 0.05:
            dist = math.hypot(spot[0] - prev_spot[0], spot[1] - prev_spot[1])
            walk = max(0.6, min(1.6, dist / WALK_SPEED))
            path.append({"t": round(t0, 3), "p": [prev_spot[0], prev_spot[1]]})
            path.append({"t": round(t0 + walk, 3), "p": [spot[0], spot[1]]})
            facing.append({"t": round(t0, 3), "auto": True, "dur": 0.25})
            facing.append({"t": round(t0 + walk - 0.15, 3), "yaw": spot[2], "dur": 0.4})
            actions.append({"clip": "Walk_Loop", "start": round(t0, 3), "end": round(t0 + walk + 0.1, 3), "fade": 0.2})
        elif abs(((prev_spot[2] - spot[2]) + 180) % 360 - 180) > 3:
            facing.append({"t": round(t0, 3), "yaw": spot[2], "dur": 0.45})
        a0 = t0 + walk
        if one_shot:
            dur = ONE_SHOT_SEC.get(clip, 1.2)
            reps = 3 if clip == "Punch_Jab" else 1
            for r in range(reps):
                actions.append({"clip": clip, "start": round(a0 + r * dur * 0.8, 3), "end": round(a0 + (r + 1) * dur * 0.8 + 0.1, 3),
                                "fade": 0.18})
            actions.append({"clip": "Idle_Loop", "start": round(a0 + reps * dur * 0.8, 3), "end": round(t1 + 0.4, 3),
                            "fade": 0.35})
        else:
            actions.append({"clip": clip, "start": round(a0, 3), "end": round(t1 + 0.4, 3), "fade": 0.3,
                            "offset": round((n * 0.37) % 1.0, 2)})
        if name == "look_up":
            poses.append({"start": round(a0, 3), "end": round(t1, 3), "fade": 0.4, "rotate": {"neck_01": [-28, 0, 0],
                                                                                            "Head": [-18, 0, 0]}})
        elif name == "listen":
            # Прислушивается — голова чуть набок (у крупной головы сгенерированного героя — едва, иначе лицо уходит).
            roll = 6 if mesh_hero(look) else 14
            poses.append({"start": round(a0, 3), "end": round(t1, 3), "fade": 0.4, "rotate": {"Head": [3, 0, roll]}})
        elif name == "sit" and mesh_hero(look):
            # Тяжёлая голова сгенерированного героя в клипе «присел» уходит в грудь — приподнять: лицо в кадре.
            poses.append({"start": round(a0, 3), "end": round(t1, 3), "fade": 0.4,
                          "rotate": {"spine_03": [-18, 0, 0], "neck_01": [-24, 0, 0], "Head": [-12, 0, 0]}})
        if phone:
            phone_windows.append([a0, t1])
        came_from = prev_spot
        prev_spot = spot

        focus = loc["targets"][focus_name][0] if focus_name in loc["targets"] else None
        cam = None
        order = _order(kind, [s["camera"] for s in shot_out])
        wanted = order[0]
        for k in order:
            cam = _camera_for(k, spot, focus, loc, side, clip, via=came_from)
            if cam is not None:
                kind = k
                break
        if cam is None:
            # Не встал ни один ракурс с проверками — общий сверху без проверки (над декорацией стена не мешает).
            h = loc["bounds"][4]
            pos = [spot[0], h + 2.4, spot[1] + 0.6]
            tgt = [spot[0], 0.5, spot[1]]
            cam = {"position": pos, "target": tgt, "fov": 50.0, "end": pos, "end_target": tgt, "free": True}
            kind = "overhead"
            checks.append(f"кадр {n}: ни один ракурс не прошёл проверку — общий сверху")
        elif kind != wanted:
            notes.append(f"кадр {n}: ракурс {wanted} не встал — взят {kind}")
        side = -side
        shot_out.append({"n": n, "t0": round(t0, 3), "t1": round(t1, 3), "text": shot.get("spoken") or shot["text"],
                         "sentences": shot["sentences"], "role": shot.get("role") or "", "action": name, "clip": clip,
                         "spot": spot_name, "hero": _r(spot, 2), "focus": focus_name, "camera": kind, "position": _r(cam["position"]),
                         "target": _r(cam["target"]), "fov": cam["fov"], "free": cam["free"]})
        shot_out[-1]["_cam"] = cam

    # Камера: ключи кадров со склейками.
    cam_keys: list[dict] = []
    for s in shot_out:
        cam = s.pop("_cam")
        k0 = {"t": s["t0"], "position": _r(cam["position"]), "target": _r(cam["target"]), "fov": cam["fov"]}
        if cam_keys:
            k0["cut"] = True
        if cam["free"]:
            k0["free"] = True
        k1 = {"t": s["t1"], "position": _r(cam["end"]), "target": _r(cam["end_target"]), "fov": cam["fov"],
              "ease": "inOutSine"}
        if cam["free"]:
            k1["free"] = True
        # Глубина резкости (post.dof): на крупных — фон мягко размыт, фокус — на цели кадра.
        if DOF.get(s["camera"]):
            k0["dof"] = k1["dof"] = DOF[s["camera"]]
        cam_keys += [k0, k1]

    # Свет, табло, кнопки по событиям.
    lights = [dict(x) for x in loc["lights"]]
    by_id = {x.get("id"): x for x in lights}
    shake: list[dict] = []
    sfx: list[dict] = []

    def add_sfx(kind: str, t: float, label: str) -> None:
        if kind in SFX and t < total - 0.2:
            ref, vol = SFX[kind]
            sfx.append({"t": round(max(0.0, t), 3), "ref": ref, "volume": vol, "label": label, "kind": kind})

    for e in events:
        t = e["t"]
        if e["kind"] == "blackout":
            for lid in loc["main_lights"]:
                light = by_id[lid]
                light["flicker"] = [{"t": round(max(0, t - 0.45), 3), "amount": 0.85, "duration": 0.45}]
                light["keys"] = _light_keys(float(light["intensity"]), t, light_back)
            amb = by_id[loc["ambient"]]
            amb["keys"] = [{"t": 0, "intensity": amb["intensity"]}, {"t": round(t, 3), "intensity": amb["intensity"]},
                           {"t": round(t + 0.05, 3), "intensity": 0.3, "ease": "linear"}]
            emer = by_id[loc["emergency"]]
            emer["keys"] = [{"t": 0, "intensity": 0}, {"t": round(t + 0.7, 3), "intensity": 0},
                            {"t": round(t + 1.0, 3), "intensity": float(emer.get("level") or 0.8), "ease": "linear"}]
            emer["flicker"] = [{"t": round(t + 0.7, 3), "amount": 0.7, "duration": 0.6}]
            if by_id.get("emergency_fill"):
                ef = by_id["emergency_fill"]
                # Сразу после «погас» — тусклый отсвет (кадр не уходит в чёрное: проверка чёрных кадров), потом —
                # в полную силу вместе с аварийным.
                lv = float(ef.get("level") or 1.0)
                ef["keys"] = [{"t": 0, "intensity": 0}, {"t": round(t, 3), "intensity": 0},
                              {"t": round(t + 0.04, 3), "intensity": round(lv * 0.8, 3), "ease": "linear"},
                              {"t": round(t + 0.7, 3), "intensity": round(lv * 0.8, 3)},
                              {"t": round(t + 1.0, 3), "intensity": lv, "ease": "linear"}]
                if light_back:
                    ef["keys"] += [{"t": round(light_back, 3), "intensity": float(ef.get("level") or 1.0)},
                                   {"t": round(light_back + 0.2, 3), "intensity": 0}]
            shake.append({"t": round(t, 3), "amp": 0.012, "duration": 0.6})
            add_sfx("blackout", t - 0.05, "блэкаут")
            add_sfx("power_down", t, "гул глохнет")
        elif e["kind"] == "flicker":
            for lid in loc["main_lights"]:
                by_id[lid].setdefault("flicker", []).append({"t": round(t, 3), "amount": 0.75, "duration": 0.9})
        elif e["kind"] == "jolt":
            shake.append({"t": round(t, 3), "amp": 0.035, "duration": 0.9})
            for lid in loc["main_lights"] + [loc["emergency"]]:
                by_id[lid].setdefault("flicker", []).append({"t": round(t, 3), "amount": 0.6, "duration": 0.5})
            n_jolts = len([x for x in sfx if x["kind"] in ("jolt", "jolt2")])
            add_sfx("jolt" if n_jolts % 2 == 0 else "jolt2", t, "рывок")
        elif e["kind"] == "scrape":
            shake.append({"t": round(t, 3), "amp": 0.01, "duration": 1.2})
            add_sfx("scrape", t, "скрежет")
        elif e["kind"] == "flash":
            for lid in loc["main_lights"][:1]:
                light = by_id[lid]
                base = 0.0 if dark(t) else float(light["intensity"])
                light.setdefault("pulses", []).append(t)
                light["keys"] = (light.get("keys") or [{"t": 0, "intensity": float(light["intensity"])}]) + [
                    {"t": round(t, 3), "intensity": base}, {"t": round(t + 0.05, 3), "intensity": 90.0, "ease": "linear"},
                    {"t": round(t + 0.35, 3), "intensity": base, "ease": "outQuad"}]
            add_sfx("flash", t, "вспышка")
        elif e["kind"] == "knock":
            shake.append({"t": round(t, 3), "amp": 0.008, "duration": 0.4})
            for r in range(KNOCKS):
                add_sfx("knock", t + r * 1.2, "стук")
        elif e["kind"] == "sting":
            add_sfx("sting", t, "акцент")
    for light in lights:
        light.pop("pulses", None)
        light.pop("level", None)
        if light.get("keys"):
            light["keys"] = sorted(light["keys"], key=lambda k: k["t"])

    objects = [dict(o) for o in loc["objects"]]
    obj_by_id = {o["id"]: o for o in objects}
    # Табло: стартовый этаж — первая цифра этажа в серии (обычно из хука), дальше — по словам.
    floors = [e for e in events if e["kind"] == "floor"]
    disp = obj_by_id.get(loc["display"])
    if disp is not None:
        state = [{"t": 0, "text": floors[0]["value"] if floors else loc.get("start_floor") or "", "glow": 1}]
        for e in floors[1:]:
            state.append({"t": round(e["t"], 3), "text": e["value"]})
        if blackout is not None:
            state.append({"t": round(blackout + 0.04, 3), "glow": 0.0})
            # Табло в темноте «оживает» само, если в серии меняется цифра (жуткая деталь).
            for e in floors[1:]:
                if e["t"] > blackout:
                    # Загорается заранее со старой цифрой (кадр на табло не пустой), на слове — новая.
                    on = max(blackout + 1.0, e["t"] - 1.8)
                    state.append({"t": round(on, 3), "glow": 1.0})
        disp["state"] = sorted(state, key=lambda k: k["t"])
    for oid in loc["glow"]:
        o = obj_by_id.get(oid)
        if o is None or oid == loc["display"]:
            continue
        st = [{"t": 0, "glow": 1}]
        if blackout is not None:
            st.append({"t": round(blackout + 0.04, 3), "glow": 0.0})
            if light_back:
                st.append({"t": round(light_back + 0.2, 3), "glow": 1.0})
        o["state"] = st
    # Стекло аварийного фонаря светится вместе с его светом (зажигается после блэкаута, гаснет со светом).
    lamp = obj_by_id.get(loc.get("emergency_lamp") or "")
    if lamp is not None:
        st = [{"t": 0, "glow": 0.0}]
        if blackout is not None:
            st += [{"t": round(blackout + 0.85, 3), "glow": 1.0}]
            if light_back:
                st.append({"t": round(light_back + 0.2, 3), "glow": 0.0})
        lamp["state"] = st
    panel = obj_by_id.get(loc["panel"])
    if panel is not None:
        presses = [s for s in shot_out if s["action"] == "press"]
        st = list(panel.get("state") or [{"t": 0, "glow": 1}])
        for s in presses:
            st.append({"t": round(s["t0"] + 0.7, 3), "alarm": True, "lit": [9]})
            add_sfx("press", s["t0"] + 0.7, "кнопка")
        if any(e["kind"] == "alarm" for e in events):
            t = next(e["t"] for e in events if e["kind"] == "alarm")
            st.append({"t": round(t, 3), "alarm": True})
        panel["state"] = sorted(st, key=lambda k: k["t"])
    # Двери: «раздвинуть» — створки на 4 см в стороны, пока герой толкает.
    for s in shot_out:
        if s["action"] == "doors" and loc["doors"]:
            for oid, dx in zip(loc["doors"], (-1, 1)):
                o = obj_by_id[oid]
                base = o["position"]
                keys = list(o.get("keys") or [{"t": 0, "position": base}])
                keys += [{"t": round(s["t0"] + 0.9, 3), "position": base},
                         {"t": round(s["t0"] + 1.8, 3), "position": _r([base[0] + dx * 0.045, base[1], base[2]]),
                          "ease": "outQuad"},
                         {"t": round(s["t1"] + 0.3, 3), "position": _r([base[0] + dx * 0.045, base[1], base[2]])}]
                o["keys"] = keys
            add_sfx("door", s["t0"] + 1.0, "створки")
    # Телефон в руке и луч фонарика — пока герой светит.
    if phone_windows:
        merged: list[list[float]] = []
        for a, b in sorted(phone_windows):
            if merged and a - merged[-1][1] < 0.6:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        objects.append({"id": "phone", "primitive": "box", "params": {"size": [0.075, 0.15, 0.012], "color": "#121418",
                                                                       "emissive": "#9fc6ff", "emissiveIntensity": 0.06,
                                                                       "roughness": 0.25, "metalness": 0.4},
                        "position": [loc["spots"]["corner"][0], -0.5, loc["spots"]["corner"][1]],
                        "attach": [{"to": f"{HERO_ID}:hand_r", "start": round(a, 3), "end": round(b, 3), "fadeIn": 0.05,
                                    "fadeOut": 0.05, "offset": [0, 0.04, 0.05], "settle": False} for a, b in merged],
                        "visible": [{"t": 0, "on": False}] + [x for a, b in merged for x in
                                                              ({"t": round(a, 3), "on": True}, {"t": round(b, 3), "on": False})],
                        "light": {"type": "spot", "color": "#e9f1ff", "intensity": 0, "position": [0, 0.08, 0.02],
                                  "target": [0, 0.2, 3.0], "angle": 38, "penumbra": 0.85, "decay": 2,
                                  # Луч фонарика виден в дымке кабины, у линзы — яркая точка.
                                  "beam": {"length": 2.4, "radius": 0.8, "opacity": 0.12},
                                  "halo": {"size": 0.06, "opacity": 0.5},
                                  "keys": [{"t": 0, "intensity": 0}] + [
                                      x for a, b in merged for x in ({"t": round(a, 3), "intensity": 0},
                                                                     {"t": round(a + 0.15, 3), "intensity": loc.get("phone_light", PHONE_LIGHT)},
                                                                     {"t": round(b - 0.1, 3), "intensity": loc.get("phone_light", PHONE_LIGHT)},
                                                                     {"t": round(b, 3), "intensity": 0})]}})
    # Напряжение в начале истории (после хука) — тихо.
    first_story = next((s for s in shot_out if s["role"] not in ("hook", "")), None)
    if first_story:
        add_sfx("tension", first_story["t0"], "напряжение")

    def add_bed(kind: str, t0: float, t1: float, label: str, fade_in: float, fade_out: float) -> None:
        if kind in AMBIENCE and t1 - t0 >= 0.5:
            ref, under = AMBIENCE[kind]
            sfx.append({"t": round(max(0.0, t0), 3), "ref": ref, "volume": 1.0, "label": label, "kind": kind,
                        "end": round(min(total, t1), 3), "role": "ambience", "under_db": under,
                        "fade_in": fade_in, "fade_out": fade_out})

    # Фон-петли: гул кабины, пока горит свет (глохнет на блэкауте), в темноте — далёкий гул шахты.
    if loc_name == "elevator":
        if blackout is None:
            add_bed("hum", 0.0, total, "гул кабины", 0.4, 1.5)
        else:
            add_bed("hum", 0.0, blackout + 0.12, "гул кабины", 0.4, 0.18)
            add_bed("dark", blackout + 0.6, light_back or total, "гул шахты", 2.5, 1.0 if light_back else 2.0)
            if light_back:
                add_bed("hum", light_back, total, "гул кабины", 0.3, 1.5)

    character = _character(look, path, facing, actions, poses)
    fill_lit, fill_dark = loc.get("fill_levels") or (FILL, FILL_DARK)
    env = dict(loc["environment"])
    spec = {"version": 1, "width": WIDTH, "height": HEIGHT, "fps": int(fps), "duration": round(total, 3),
            "environment": env, "room": loc["room"], "lights": lights, "objects": objects, "characters": [character],
            # Камера сама не прижимается к стенам: ракурсы уже проверены (внутри декорации или за срезанной стеной).
            "camera": {"fov": FOV, "keys": cam_keys, "shake": shake, "margin": 0.1, "clamp": False,
                       "fill": {"color": loc.get("fill_color", "#ffe6cc"), "intensity": fill_lit, "distance": 4,
                                "keys": ([{"t": 0, "intensity": fill_lit}, {"t": round(blackout, 3), "intensity": fill_lit},
                                          {"t": round(blackout + 0.05, 3), "intensity": fill_dark}]
                                         if blackout is not None else None)}},
            "quality": {"shadows": True, "shadowSize": 1024, "antialias": True}}
    return {"spec": spec, "shots": shot_out, "events": events, "sfx": sorted(sfx, key=lambda x: x["t"]),
            "location": loc_name, "checks": checks, "notes": notes, "look": look}


def check_shots(result: dict) -> list[str]:
    """Проверка готовой режиссуры: камера внутри декорации (кроме общих сверху), кадры встык, без дыр."""
    loc = LOCATIONS[result["location"]]()
    out = list(result.get("checks") or [])
    shots = result["shots"]
    for a, b in zip(shots, shots[1:]):
        if abs(a["t1"] - b["t0"]) > 1e-3:
            out.append(f"кадры {a['n']}–{b['n']}: дыра/нахлёст {a['t1']}–{b['t0']}")
    for s in shots:
        if not camera_ok(s["position"], loc, s["free"], 0.08):
            out.append(f"кадр {s['n']}: камера в стене")
    return out


def render_episode(result: dict, out_path: Any, *, workers: int | None = None, gl: str | None = None) -> dict:
    """Сцена серии → mp4 (``app.scene3d.render_scene``)."""
    from app.scene3d import render_scene

    return render_scene(result["spec"], out_path, workers=workers, gl=gl)
