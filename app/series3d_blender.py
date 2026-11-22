"""3D-серия в Blender: сценарий → режиссура по фразам → задание рендера → кадры (кусками, с кэшем) → mp4.

Чистый Python без ``bpy``: эту же режиссуру считают сцена Blender (``tools/blender/series_scene.py``),
сервис (задание в очередь хост-воркера, ``app/render_queue.py``) и тесты.

**Режиссура.** Фразы озвучки → кадры (``app.series3d.split_shots``), события на словах
(``app.series3d.detect_events``: погас свет, дёрнулся, стук, табло…). На каждый кадр — поставленное действие из
фиксированного словаря ``ACTIONS`` (подойти, нажать кнопку N, стучать, раздвигать двери, сесть в угол/встать,
телефон, взгляд на табло/потолок/камеру, вздрогнуть, кричать, слушать, ждать) + эмоция лица ``EMOTIONS`` + план
камеры ``CAMERAS`` (за плечом / крупно лицо / деталь / общий сверху). Выбирает Gemini (бесплатно) по JSON-схеме
со строгими enum'ами; без ключа или при ошибке — правила по словам фразы. Потом общая правка
(``fix_sequence``): действие должно быть выполнимо (сидя — сначала встать), один план камеры подряд не
повторяется.

**Исполнение** (``perform``): покадровые дорожки — клипы KayKit с наплывами (``motion3d.ClipTrack``), путь
корня (шаги или склейка-«телепорт», если идти дольше половины кадра), поворот на месте шагами, цели рук для IK
(нажатия, стук, двери, телефон), взгляд, мимика (эмоция + испуг на событиях + моргание), рот — лип-синк по
таймингам слов (визем-группы гласных) с огибающей озвучки, свет (мигание, блэкаут, аварийный, табло), двери,
тряска камеры. Камеру по плану кадра ставит сцена Blender по настоящим костям героя.

**Рендер** (``render_local``): одна сцена лифта на всю серию, кадры — кусками по планам в кэш
``<cache>/<хэш плана>/``; хэш — от покадровых дорожек плана, поэтому правка одной фразы перерисует только её
кадры; упал — повторный запуск дорисует с того кадра, где остановился. Склейка → mp4 без звука (звук, субтитры,
HUD, титры кладёт холст сервиса — ``app/stories_3d.py``).

Где что лежит. Здесь — задание и рендер (``build_job``, ``chunk_keys``, ``render_local``). Вынесено: сцена и локации ``app/series3d_blender_stage.py``,
режиссура фраз ``app/series3d_blender_direction.py``, камера ``app/series3d_blender_camera.py``, исполнение ``app/series3d_blender_perform.py``. Те
модули этот не импортируют, а он их. Blender грузит этот файл как ``from app import series3d_blender``: новые модули лежат в том же пакете
и на верхнем уровне импортируют только стандартную библиотеку и ``app.motion3d``, как и раньше.
"""
from __future__ import annotations

import hashlib
import json
import re
import math
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from app import motion3d as m

from app.series3d_blender_stage import (  # noqa: F401
    S, W, D, H, FRONT, LEFT, PANEL_Y, BTN_X, BTN_Z0, BTN_DZ, BTN_DY, BELL, BTN_NORMAL, DOOR_FACE_Y, DOOR_NORMAL,
    DISPLAY, CEIL_LIGHT, HATCH, HATCH_LID, HATCH_HINGE_Y, LATCH, HATCH_OPEN_DEG, BAG_SPOT, BAG_FROM, BAG_H, REACH,
    SHOULDER_R, MAX_PRESS_Z,
    CLIP_LEN, WALK_CLIP, WALK_FEET, MAX_WALK_STEPS,
    _walk_stance, WALK_STANCE, WALK_CYCLE_UNITS, STEP_LEN, _STANCE_CUM, _v, stance_units, walk_plan, walk_seconds,
    CLIPS_NEEDED, button_pos, reachable, facing, to_world, PRESS_TURN, KNOCK_POINT, press_stand, spot_for, LOCS,
    LOC_NAMES, LOC_TARGETS, loc_of, loc_world, loc_spot, loc_target, FILL_W, RIM_W, EXPOSURE, inside_loc, DOORWAY,
    LIFT_LOCS, VOXEL_BLOCK, loc_max_steps,
    SHOW_WORDS, show_items, SCRIPT_SEC, SCRIPT_WORDS, writer_places, OFFLIST, offlist_mentions, rule_location,
    DECAL_RX, decal_from_text, _low,
)
from app.series3d_blender_direction import (  # noqa: F401
    ACTIONS, TARGETS, BUTTONS, EMOTIONS, CAMERAS, CAMERA_PREFS, HAND_ACTIONS, SIT_OK, _RX, button_from_text,
    rule_choice, SPEAKERS, SPEECH_VERB, OTHER_SPEAKER, HERO_SUBJECT, _OPEN_Q, _CLOSE_Q, speech_labels, hero_names,
    dialog_lines, has_hero_lines, GEMINI_SCHEMA, gemini_prompt, gemini_choices, SCREEN_CUE, HOOK_FACE_SEC,
    screen_value, screen_shots, hook_items, split_hook, fix_sequence, plan_shots,
)
from app.series3d_blender_camera import (  # noqa: F401
    FACE_Z, SIT_DROP, PHONE_SHOW, CAM_BOUNDS, _CAM, cam_bounds, cam_inside, _rot, face_point, shot_camera, cam_sees,
    ots_ok, camera_plan, TITLE_Y, PART_Y, face_target, face_bands, DOORWAY_CAMS, doorway_camera, authored_camera,
)
from app.series3d_blender_plan import beat_plan, has_plans, plan_key, shots_from_beats  # noqa: F401
from app import series3d_chase as CH
from app.series3d_blender_perform import (  # noqa: F401
    FPS, WALK_FOOT_X, WALK_PASS, STRIDE, DOOR_OPEN, VISEMES, VOWELS, _DIGITS, _letters, viseme_keys, lipsync,
    _pulse, _ang, _Track, perform, _beam_off_camera, planted_local, clip_to_world, walk_root, foot_slide,
    _posture_at, track_of,
)

SCENE_VERSION = 4          # меняй, когда меняется сцена Blender: кэш кусков станет недействительным


LIPSYNC_MODES = ("speaking", "all", "off")


DOOR_W = 0.86


EMERG = (-0.42, FRONT + 0.05, H - 0.07)


SHOULDER_L = (-0.212 * S, 0.0, 1.107 * S)


# --------------------------------------------------------------------------- #
# Звуки событий и действий (настоящие записи паков)
# --------------------------------------------------------------------------- #

SFX = {
    "press": ("sfx:kenney-interface/click_002.ogg", 0.55),
    "bell": ("sfx:kenney-interface/click_004.ogg", 0.5),
    "knock_hit": ("sfx:kenney-impact/impactMetal_medium_002.ogg", 0.55),
    "doors_strain": ("sfx:tension/metal-scrape.ogg", 0.55),
    "doors_open": ("sfx:tension/clank.ogg", 0.8),
    # ч.4: уведомление о сообщении, тело падает на пол кабины (глухой удар + металл пола + низ), всё гаснет
    "msg_in": ("sfx:kenney-interface/confirmation_002.ogg", 0.28),
    "body_thud": ("sfx:kenney-impact/impactPunch_heavy_002.ogg", 0.8),
    "body_floor": ("sfx:kenney-impact/impactMetal_heavy_001.ogg", 0.55),
    "body_low": ("sfx:freesound/impact-low.ogg", 0.7),
    # крышка люка (владелец 20.11: «звук люка поменять» — вместо скрежета): тяжёлая стальная плита — отщёлкнулась и пошла,
    # встала на упор; ноги сверху задели раму люка
    "lid_clunk": ("sfx:kenney-impact/impactPlate_heavy_002.ogg", 0.55),
    "lid_stop": ("sfx:kenney-impact/impactPlate_heavy_004.ogg", 0.35),
    "hatch_frame": ("sfx:kenney-impact/impactMetal_light_002.ogg", 0.45),
    # ч.5: двери кабины разъезжаются сами с лязгом (лязг + стальной удар, скрежет створок, упор), телефон жужжит
    "doors_clang": ("sfx:tension/clank.ogg", 0.85),
    "doors_clang_hit": ("sfx:kenney-impact/impactMetal_heavy_000.ogg", 0.45),
    "doors_slide": ("sfx:tension/metal-scrape.ogg", 0.3),
    "doors_stop": ("sfx:kenney-impact/impactMetal_medium_001.ogg", 0.4),
    "vibrate": ("sfx:kenney-interface/error_006.ogg", 0.35),
}
# Бег по бетону: пять записей шагов Kenney по кругу; у двойника — те же, тише (он сзади)
STEPS = tuple(f"sfx:kenney-impact/footstep_concrete_{i:03d}.ogg" for i in range(5))
SFX_VOL = {"step": 0.6, "step_double": 0.42}


def sfx_for(job: dict, perf: dict) -> list[dict]:
    """Звуки холста: события режиссуры (как ``series3d``) + щелчки нажатий, удары стука, скрежет створок,
    фон-петли гула кабины/шахты."""
    from app import series3d as S3

    total = float(job["duration"])
    out: list[dict] = []

    def add(ref_vol: tuple[str, float], t: float, label: str, kind: str) -> None:
        if t < total - 0.2:
            out.append({"t": round(max(0.0, t), 3), "ref": ref_vol[0], "volume": ref_vol[1], "label": label, "kind": kind})

    for e in job.get("events") or []:
        k, t = e["kind"], float(e["t"])
        if e.get("sfx"):
            # автор назвал запись события сам (``sfx: "sfx:пак/файл.ogg"``, ``volume``)
            add((str(e["sfx"]), float(e.get("volume", 0.8))), t, str(e.get("label") or k), k)
            continue
        if k == "blackout":
            if t < 0:
                continue          # свет погас ещё до начала серии («Если в блэкаут…»): щелчка нет
            add(S3.SFX["blackout"], t - 0.05, "блэкаут", "blackout")
            add(S3.SFX["power_down"], t, "гул глохнет", "power_down")
        elif k == "steps":
            # шаги по крыше кабины: четыре тяжёлых шага над головой
            for i_, (dt_, vol_) in enumerate(((0.0, 0.75), (0.46, 0.85), (0.9, 0.8), (1.38, 0.9))):
                add((S3.SFX["step"][0], vol_), t - 0.15 + dt_, "шаги сверху", "steps")
                add((SFX["knock_hit"][0], 0.18), t - 0.15 + dt_ + 0.01, "крыша гудит", "steps_metal")
        elif k == "jolt":
            n = len([x for x in out if x["kind"] in ("jolt", "jolt2")])
            add(S3.SFX["jolt" if n % 2 == 0 else "jolt2"], t, "рывок", "jolt")
        elif k == "scrape":
            add(S3.SFX["scrape"], t, "скрежет", "scrape")
        elif k == "flash":
            add(S3.SFX["flash"], t, "вспышка", "flash")
        elif k in ("knock", "knock_far"):
            add(S3.SFX["knock"], t, "стук", "knock")
        elif k == "sting":
            add(S3.SFX["sting"], t, "акцент", "sting")
        elif k == "doors_close":
            add(SFX["doors_open"], t, "двери схлопнулись", "doors_close")
        elif k == "msg_in":
            add(SFX["msg_in"], t, "пришло сообщение", "msg_in")
        elif k == "lights_out":
            if t < 0:
                continue          # серия началась в полной темноте: гаснуть нечему
            add((S3.SFX["power_down"][0], 0.7), t - 0.1, "аварийный свет гаснет", "lights_out")
        elif k == "doors_open":
            # ч.5: створки дёрнулись с лязгом и поехали со скрежетом, встали в упор
            add(SFX["doors_clang"], t, "двери лязгнули", "doors_open")
            add(SFX["doors_clang_hit"], t + 0.02, "двери лязгнули", "doors_open_hit")
            add(SFX["doors_slide"], t + 0.15, "створки едут", "doors_slide")
            add(SFX["doors_stop"], t + CH.DOORS_OPEN_SEC - 0.05, "створки встали", "doors_stop")
        elif k == "vibrate":
            for p_ in range(CH.VIB_PULSES):
                add(SFX["vibrate"], t + p_ * (CH.VIB_SEC + 0.18), "телефон вибрирует", "vibrate")
        elif k == "body_drop":
            bd = perf.get("body_drop") or {}
            hit = float((bd.get("t") or [t + 0.42])[0])       # удар об пол (perf.body_drop.t — момент удара)
            add(SFX["hatch_frame"], t + 0.15, "ноги в люке", "hatch_frame")
            add(SFX["body_thud"], hit, "тело упало", "body_drop")
            add(SFX["body_floor"], hit + 0.01, "пол кабины", "body_floor")
            add(SFX["body_low"], hit, "удар", "body_low")
    for c in perf["contacts"]:
        if c["kind"] == "press":
            add(SFX["bell" if c.get("button") == "bell" else "press"], c["t"] - 0.01, "кнопка", "press")
        elif c["kind"] == "knock":
            add(SFX["knock_hit"], c["t"] - 0.01, "удар в дверь", "knock_hit")
        elif c["kind"] == "hatch":
            add(SFX["press"], c["t"] - 0.6, "защёлка", "latch")
            add(SFX["doors_strain"], c["t"], "крышка люка", "hatch")
            add(SFX["doors_open"], c["t"] + 1.1, "люк открыт", "hatch_open")
        elif c["kind"] == "latch":
            add(SFX["press"], c["t"], "защёлка", "latch")
            add((SFX["knock_hit"][0], 0.3), c["t"] + 0.3, "защёлка встала", "latch_stop")
        elif c["kind"] == "lid":
            add(SFX["lid_clunk"], c["t"], "крышка люка", "lid")
            add(SFX["lid_stop"], c["t"] + 1.75, "крышка на упоре", "lid_stop")
        elif c["kind"] == "bag_step":
            add((S3.SFX["step"][0], 0.45), c["t"] - 0.35, "рюкзак на пол", "bag")
            add((S3.SFX["step"][0], 0.55), c["t"], "встал на рюкзак", "bag_step")
        elif c["kind"] == "doors":
            add(SFX["doors_strain"], c["t"], "створки", "doors")
            if c.get("full"):
                add(SFX["doors_open"], c["t"] + 1.0, "двери разъехались", "doors_open")
        elif c["kind"] == "step":
            # бег по бетону: свои записи на каждый шаг по кругу, громкость — в коде
            n_ = len([x for x in out if x["kind"] == "step"])
            add((STEPS[n_ % len(STEPS)], SFX_VOL["step"]), c["t"], "шаги по бетону", "step")
    dbl = perf.get("double") or {}
    for i_, (t_, _who) in enumerate(dbl.get("steps") or []):
        # шаги двойника: те же записи в том же ритме (он отстаёт на SYNC_LAG), другой по счёту файл и тише — вторая пара
        add((STEPS[(i_ + 2) % len(STEPS)], SFX_VOL["step_double"]), t_, "шаги сзади", "step_double")
    for ev in dbl.get("sfx") or []:
        add((ev["ref"], float(ev["volume"])), float(ev["t"]), ev["label"], ev["kind"])
    if job.get("ambience") is not None:
        # свой фон места (кубический мир: ночь в поле, а не гул кабины): [{ref, t, end, under_db, fade_in, fade_out,
        # label}] — как фон-петли кабины, без «напряжения» лифта
        for a_ in job["ambience"]:
            t0_, t1_ = float(a_.get("t", 0.0)), float(a_.get("end", total))
            if t1_ - t0_ >= 0.5:
                out.append({"t": round(max(0.0, t0_), 3), "ref": a_["ref"], "volume": float(a_.get("volume", 1.0)),
                            "label": a_.get("label") or "фон", "kind": a_.get("kind") or "ambience",
                            "end": round(min(total, t1_), 3), "role": a_.get("role") or "ambience",
                            "under_db": float(a_.get("under_db", 18.0)), "fade_in": float(a_.get("fade_in", 0.5)),
                            "fade_out": float(a_.get("fade_out", 1.0))})
        return sorted(out, key=lambda x: x["t"])
    story = next((s for s in job["shots"] if s.get("role") not in ("hook", "")), None)
    if story:
        add(S3.SFX["tension"], story["t0"], "напряжение", "tension")
    b, back = perf.get("blackout"), perf.get("light_back")

    def bed(kind: str, t0: float, t1: float, label: str, fi: float, fo: float) -> None:
        if t1 - t0 >= 0.5:
            ref, under = S3.AMBIENCE[kind]
            out.append({"t": round(max(0.0, t0), 3), "ref": ref, "volume": 1.0, "label": label, "kind": kind,
                        "end": round(min(total, t1), 3), "role": "ambience", "under_db": under, "fade_in": fi, "fade_out": fo})

    out_t = next((float(e["t"]) for e in job.get("events") or [] if e["kind"] == "lights_out"), None)
    if b is None:
        bed("hum", 0.0, total, "гул кабины", 0.4, 1.5)
    else:
        bed("hum", 0.0, b + 0.12, "гул кабины", 0.4, 0.18)
        end = back or total
        if out_t is not None and out_t > b:
            end = min(end, out_t + 0.6)       # полная темнота — и гул шахты глохнет: дальше тишина
        open_t = next((float(e["t"]) for e in job.get("events") or [] if e["kind"] == "doors_open"), None)
        if open_t is not None:
            end = min(end, open_t + 0.3)      # ч.5: дали питание, двери открылись — вместо гула шахты гудит кабина
        bed("dark", b + 0.6, end, "гул шахты", 2.5, 0.6 if out_t is not None and out_t > b else (1.0 if back else 2.0))
        if back:
            bed("hum", back, total, "гул кабины", 0.3, 1.5)
        elif open_t is not None:
            bed("hum", open_t - 0.1, total, "гул кабины", 0.2, 1.5)
    return sorted(out, key=lambda x: x["t"])


# --------------------------------------------------------------------------- #
# Задание рендера, куски и кэш
# --------------------------------------------------------------------------- #

# Пресеты рендера. Узкое место Eevee здесь — не GPU, а CPU между кадрами: композитор (свечение + размытая виньетка
# на CPU), пересчёт сцены на каждый замер размытия движения, сжатие PNG. Поэтому быстрые пресеты: композитор на GPU,
# кадры JPEG 95, объём/размытие/глубина резкости — только в планах, где нужны (``shot_fx``), меньше сэмплов, два
# процесса Blender на разные планы.
#   samples — сэмплы Eevee; scale — % от 1080×1920 (fast720 — 720×1280, апскейл до 1080×1920 lanczos при склейке);
#   fx — all: объём/размытие/ГРИП везде, auto — по планам, none — без них; frames — формат кадров кэша;
#   comp_gpu — композитор на GPU; workers — процессов Blender; blur_steps — шагов размытия движения.
QUALITY = {
    "final": {"samples": 48, "scale": 100, "fx": "all", "frames": "PNG", "comp_gpu": False, "workers": 1, "blur_steps": 1,
              "label": "кино: как пробный кадр, всё везде"},
    "fast": {"samples": 24, "scale": 100, "fx": "auto", "frames": "JPEG", "comp_gpu": True, "workers": 2, "blur_steps": 1,
             "label": "финал быстро: 1080p, эффекты по планам, 2 процесса"},
    "fast720": {"samples": 24, "scale": 66.667, "fx": "auto", "frames": "JPEG", "comp_gpu": True, "workers": 2,
                "blur_steps": 1, "upscale": True, "label": "финал быстрее: 720p + апскейл lanczos"},
    "preview": {"samples": 16, "scale": 50, "fx": "auto", "frames": "JPEG", "comp_gpu": True, "workers": 1, "blur_steps": 1,
                "label": "проверка: 540p"},
    "draft": {"samples": 4, "scale": 50, "fx": "none", "frames": "JPEG", "comp_gpu": True, "workers": 2, "blur_steps": 1,
              "label": "черновик действий: 540p, 4 сэмпла, без объёма/размытия/ГРИП"},
}
DEFAULT_QUALITY = "fast"


def preset(job: dict) -> dict:
    """Пресет задания (неизвестный — ``DEFAULT_QUALITY``)."""
    name = job.get("quality") if job.get("quality") in QUALITY else DEFAULT_QUALITY
    return {"name": name, **QUALITY[name]}


# Чем рисовать кадры (``job["render_backend"]``): blender — Eevee, как раньше (по умолчанию); threejs — Blender только
# ставит позы (тело, IK, лицо, телефон, свет, двери, камера) и выгружает их потоком (tools/blender/series_export.py,
# формат app/series3d_pose.py), кадры рисует three.js в headless Chromium на GPU (node/scene3d/series_render.mjs).
RENDER_BACKENDS = ("blender", "threejs")
DEFAULT_BACKEND = "blender"


def backend(job: dict) -> str:
    """Бэкенд рендера задания (неизвестный — ``DEFAULT_BACKEND``)."""
    b = str(job.get("render_backend") or DEFAULT_BACKEND).strip().lower()
    return b if b in RENDER_BACKENDS else DEFAULT_BACKEND


def shot_fx(perf: dict, mode: str = "auto") -> dict[int, dict]:
    """Какие дорогие эффекты нужны плану: объём — когда в кадре луч фонарика или мигает/гаснет свет; размытие
    движения — шаги, рывок/испуг, стук, двери, тряска камеры; глубина резкости — крупные планы и детали."""
    fps = perf["fps"]
    out: dict[int, dict] = {}
    cams = {c["n"]: c for c in perf.get("cameras") or []}
    walks = [(sg["start"], sg["start"] + 1.2) for sg in perf["track"] if sg["clip"] in ("Walking_A", "Walking_B", "Walking_C", "Hit_A",
                                                                                          "Sit_Floor_Down", "Sit_Floor_StandUp",
                                                                                          "Running_A")]
    for s in perf["shots"]:
        if mode in ("all", "none"):
            on = mode == "all"
            out[s["n"]] = {"volume": on, "blur": on, "dof": on}
            continue
        f0, f1 = int(s["t0"] * fps), min(perf["frames"], int(s["t1"] * fps) + 1)
        lt = perf["lights"][f0:f1]
        ph = perf["phone"][f0:f1]
        beam = any(p["on"] and p["light"] > 0.05 for p in ph)
        flick = any(0.02 < x["main"] < 0.98 for x in lt)
        move = any(a < s["t1"] and b > s["t0"] for a, b in walks)
        shake = any(x["shake"] > 0.003 or x["drop"] < -0.005 for x in lt)
        hits = any(c["kind"] in ("knock", "doors") and s["t0"] <= c["t"] < s["t1"] for c in perf["contacts"])
        st = any(s["t0"] - 0.1 <= t < s["t1"] for t, _ in perf.get("startles") or [])
        kind = (cams.get(s["n"]) or {}).get("kind") or s.get("camera")
        torch = any(x.get("torch", 0.0) > 0.05 for x in lt)     # чужой фонарик в щели люка — луч во взвеси
        insert = bool((cams.get(s["n"]) or {}).get("insert"))     # экран во весь кадр: без размытия движения
        out[s["n"]] = {"volume": beam or flick or torch or (s.get("location") or "cabin") != "cabin",
                       "blur": (move or shake or hits or st) and not insert, "dof": kind in ("face", "detail")}
    return out


OUTRO_SEC = (1.0, 8.0)     # немая фраза концовки: сколько секунд она длится (границы)


def outro_sec(script: dict | None) -> float:
    """Сколько длится немая концовка сценария (``script.outro``), с — столько сервис добавляет к серии до титра части."""
    return round(sum(min(OUTRO_SEC[1], max(OUTRO_SEC[0], float(o.get("sec") or 2.5)))
                     for o in (script or {}).get("outro") or [] if isinstance(o, dict) and str(o.get("text") or "").strip()), 3)


def with_outro(script: dict, words: list[dict], total: float, beats: list[dict] | None):
    """Немая концовка (``script.outro = [{text, sec, plan}]``): действие без голоса после последнего слова — фразы
    не озвучиваются и не идут в субтитры, но режиссура читает их как обычные (события на словах, бит «План:»). Здесь
    они становятся фразами с неслышными словами (``silent``) по ``sec`` секунд каждая сразу после речи; серия — до конца
    концовки и ещё ~2 с на титр части (``stories_3d`` прибавляет ``outro_sec`` сам). Без ``outro`` — всё как было."""
    outro = [o for o in (script.get("outro") or []) if isinstance(o, dict) and str(o.get("text") or "").strip()]
    if not outro or not words:
        return script, words, total, beats
    spoken = [s for s in script.get("sentences") or [] if str(s.get("text") or "").strip()]
    t = float(words[-1]["end"]) + 0.25
    out_words = [dict(w) for w in words]
    sentences = list(spoken)
    extra_beats: list[dict] = []
    for o in outro:
        text = " ".join(str(o["text"]).split())
        sec = min(OUTRO_SEC[1], max(OUTRO_SEC[0], float(o.get("sec") or 2.5)))
        toks = text.split()
        span = sec * 0.6
        for i, tok in enumerate(toks):
            a = t + 0.1 + span * i / len(toks)
            out_words.append({"word": tok, "start": round(a, 3), "end": round(a + span / len(toks) * 0.9, 3), "silent": True})
        extra_beats.append({"i": len(spoken) + len(extra_beats), "text": text, "sentence": len(sentences),
                            "spec": {"action": str(o.get("plan") or "")}})
        sent = {"text": text, "role": "", "silent": True}
        if isinstance(o.get("cam"), dict) and o["cam"].get("pos"):
            sent["cam"] = o["cam"]      # камера плана от автора (series3d_blender_camera.authored_camera)
        for key in CH.AUTHORED:         # бег, двойник, место, свет плана (ч.5) — как у озвученных фраз
            if key != "cam" and key in o:
                sent[key] = o[key]
        sentences.append(sent)
        t += sec
    plan_beats = list(beats or []) + extra_beats if beats else beats
    # серия — до конца концовки и ещё ~2 с на титр части (сервис уже прибавил концовку — тогда total не меняется)
    return {**script, "sentences": sentences}, out_words, round(max(float(total), t + 1.75), 3), plan_beats


def build_job(script: dict, words: list[dict], total: float, *, gemini: bool = True, client: Any = None,
              envelope: list[float] | None = None, lipsync_mode: str = "speaking", quality: str = DEFAULT_QUALITY,
              look: dict | None = None, seed: int = 7, beats: list[dict] | None = None,
              render_backend: str = DEFAULT_BACKEND) -> dict:
    """Всё, что нужно сцене Blender (JSON): кадры с режиссурой, события, слова, огибающая голоса, качество. ``beats`` —
    биты эпизода: строка «План:» в бите сильнее режиссёра (``app/series3d_blender_plan.py``)."""
    script, words, total, beats = with_outro(script, words, total, beats)
    plan = plan_shots(script, words, total, gemini=gemini, client=client, beats=beats)
    cams = {k: s_["cam"] for k, s_ in enumerate(script.get("sentences") or []) if isinstance(s_.get("cam"), dict)}
    for shot in plan["shots"]:
        k = next((i for i in shot.get("sentences") or [] if i in cams), None)
        if k is not None and shot.get("by") == "author" and shot.get("planned"):
            shot["cam"] = cams[k]
    # поля автора во фразах (ч.5: бег, двойник, экран переписки, место, свет плана) — series3d_chase.apply_authored
    spoken = [s_ for s_ in script.get("sentences") or [] if str(s_.get("text") or "").strip()]
    if any(any(k_ in s_ or k_ + "s" in s_ for k_ in CH.AUTHORED if k_ != "cam") for s_ in spoken):
        CH.apply_authored(plan["shots"], spoken)
    if "dark" in (plan.get("start") or []):
        # серия начинается в полной темноте (конец прошлой части): свет погас до первого кадра — без щелчка и гула
        plan["events"] = [e for e in plan["events"] if e["kind"] not in ("blackout", "lights_out", "light_back")] + \
            [{"kind": "blackout", "t": -1.0, "sentence": 0, "role": ""}, {"kind": "lights_out", "t": -1.0, "sentence": 0,
                                                                          "role": ""}]
        plan["events"].sort(key=lambda e: float(e["t"]))
    return {"version": SCENE_VERSION, "fps": FPS, "duration": round(float(total), 3), "title": script.get("title") or "",
            "shots": plan["shots"], "events": plan["events"], "words": plan["words"], "director": plan["director"],
            "speech": plan["speech"],
            "director_error": plan["director_error"], "envelope": [round(x, 4) for x in (envelope or [])],
            "lipsync": lipsync_mode if lipsync_mode in LIPSYNC_MODES else "speaking",
            "quality": quality if quality in QUALITY else DEFAULT_QUALITY, "look": look or {}, "seed": int(seed),
            "render_backend": backend({"render_backend": render_backend}),
            # продолжение прошлой части (первый бит: «на рюкзаке, люк приоткрыт, сверху фонарик») — только у таких серий
            **({"start": plan["start"]} if plan.get("start") else {})}


def chunks(perf: dict) -> list[dict]:
    """Куски рендера = планы: ``[{n, f0, f1}]`` (кадры с 1, включительно)."""
    fps, nf = perf["fps"], perf["frames"]
    out = []
    for s in perf["shots"]:
        f0 = int(round(s["t0"] * fps)) + 1
        f1 = min(nf, int(round(s["t1"] * fps)))
        if f1 >= f0:
            out.append({"n": s["n"], "f0": f0, "f1": f1})
    return out


def _code_hash() -> str:
    root = Path(__file__).resolve().parent.parent
    h = hashlib.sha1()
    for p in (root / "tools" / "blender" / "series_scene.py", root / "tools" / "blender" / "hero_shot.py",
              root / "tools" / "blender" / "series_places.py", root / "tools" / "blender" / "series_lift.py",
              root / "tools" / "blender" / "series_voxel.py", root / "tools" / "blender" / "series_floor22.py",
              root / "tools" / "blender" / "series_double.py", root / "tools" / "blender" / "series_chat.py",
              root / "app" / "motion3d.py"):
        if p.is_file():
            h.update(_without_log_block(p.read_bytes()))
    return h.hexdigest()[:12]


_LOG_BLOCK = re.compile(rb"[ \t]*# frame-log >>.*?# << frame-log[^\n]*\n", re.S)


def _without_log_block(data: bytes) -> bytes:
    """Блок журнала кадров (``# frame-log >> … # << frame-log`` в series_scene.py) картинку не меняет — в хэш
    кусков не входит, иначе добавление журнала перерисовало бы кэш всех серий."""
    return _LOG_BLOCK.sub(b"", data)


THREE_SOURCES = ("tools/blender/series_export.py", "tools/blender/bake_view_lut.py", "app/series3d_pose.py",
                 "node/scene3d/series_render.mjs", "node/scene3d/render.mjs", "node/scene3d/src/seriesview.ts",
                 "node/scene3d/src/haze.ts", "node/scene3d/src/dof.ts", "node/scene3d/src/viewlut.ts",
                 "node/scene3d/src/post.ts", "node/scene3d/src/surfaces.ts")
# Настройки рисования three.js (спека seriesview.ts): свет, GI, взвесь, размытие движения, ГРИП, пост. Единицы света
# откалиброваны по кадрам Blender (ч.2, планы 4–8, 16 кадров): lightScale 0.85, areaScale 0.53, haze 0.6 — средняя
# разница пикселя 19.9 → 14.7 из 255 (tools/series3d_compare.py; «глазной» свет — площадным, как у Blender).
THREE_SPEC = {"envIntensity": 1.2, "metalEnv": 1.4, "lightScale": 0.85, "areaScale": 0.53, "haze": 0.6, "blurSteps": 6,
              "shadowMap": 2048, "areaShadow": 0.5,
              "camFillSpot": False, "camFillRadius": 0.65,
              "eyeEnv": 0.06, "dofMax": 28, "lut": True, "gi": True, "brushed": True,
              "post": {"ao": 0.9, "aoRadius": 0.25, "aoScale": 0.5, "bloom": {"strength": 0.32, "radius": 0.55, "threshold": 1.1},
                       "grade": {"contrast": 1.0, "saturation": 1.0}, "vignette": 0.42, "grain": 0.015, "ca": 0.6}}


def three_spec(job: dict) -> dict:
    """Спека рисования задания: ``THREE_SPEC`` + правка задания ``three_spec`` (мир со своим светом — например
    кубический: сильнее свечение, туман дали); ``post`` и вложенные словари сливаются, а не заменяются."""
    over = job.get("three_spec") or {}
    if not over:
        return THREE_SPEC

    def merge(a: dict, b: dict) -> dict:
        out = dict(a)
        for k, v in b.items():
            out[k] = merge(a[k], v) if isinstance(v, dict) and isinstance(a.get(k), dict) else v
        return out
    return merge(THREE_SPEC, over)


def _three_hash() -> str:
    """Код рисования three.js (выгрузка поз + рендер): кадры кэша бэкенда threejs зависят и от него."""
    root = Path(__file__).resolve().parent.parent
    h = hashlib.sha1(json.dumps(THREE_SPEC, sort_keys=True).encode())
    for rel in THREE_SOURCES:
        p = root / rel
        if p.is_file():
            h.update(p.read_bytes())
    return h.hexdigest()[:12]


def chunk_keys(job: dict, perf: dict) -> list[dict]:
    """Хэш куска — от покадровых дорожек его кадров (+ соседей на наплыв), плана камеры, качества и кода сцены.
    Бэкенд threejs добавляет в хэш своё имя и код рисования; у blender хэш прежний (поля не добавляются)."""
    code = _code_hash()
    be = backend(job)
    three = _three_hash() if be != "blender" else None
    pr = preset(job)
    fx = shot_fx(perf, pr["fx"])
    rset = {k: pr[k] for k in ("samples", "scale", "frames", "comp_gpu", "blur_steps")}
    out = []
    shots = {s["n"]: s for s in perf["shots"]}
    for c in chunks(perf):
        a, b = max(0, c["f0"] - 1 - 3), min(perf["frames"], c["f1"] + 3)
        s = shots[c["n"]]
        c_n = c["n"]
        blob = {"v": SCENE_VERSION, "code": code, "q": rset, "fx": fx.get(c["n"]), "f": [c["f0"], c["f1"]],
                "shot": {k: s.get(k) for k in ("action", "camera", "subject", "spot", "heading", "target", "button", "emotion",
                                              "posture_end", "phone", "t0", "t1", "a0", "screen", "hook")},
                "root": perf["root"][a:b], "heading": perf["heading"][a:b], "gaze": perf["gaze"][a:b],
                "face": perf["face"][a:b], "body": perf["body"][a:b], "lights": perf["lights"][a:b],
                "phone": perf["phone"][a:b], "hands": [perf["hands"]["r"][a:b], perf["hands"]["l"][a:b]],
                "track": [sg for sg in perf["track"] if sg["start"] < b / perf["fps"] + 1 and
                          sg["start"] + 60 > a / perf["fps"] - 2],
                "camera": next((c for c in perf["cameras"] if c["n"] == c_n), None),
                "look": job.get("look") or {}}
        if perf.get("double"):      # ч.5: двойник в кадре — его дорожки тоже (у старых серий поля нет — ключи прежние)
            d_ = perf["double"]
            blob["double"] = {"root": d_["root"][a:b], "heading": d_["heading"][a:b],
                              "track": [sg for sg in d_["track"] if sg["start"] < b / perf["fps"] + 1]}
        if job.get("photos"):
            blob["photos"] = job["photos"]
        if three:
            blob["backend"] = {"name": be, "code": three}
            if job.get("three_spec"):     # своя спека задания (у старых серий её нет — ключи прежние)
                blob["backend"]["spec"] = job["three_spec"]
        key = hashlib.sha1(json.dumps(blob, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:16]
        ext = "jpg" if pr["frames"] == "JPEG" or three else "png"
        out.append({**c, "key": key, "fx": fx.get(c["n"]), "ext": ext})
    return out


def frame_ok(path: Path) -> bool:
    """Кадр дописан до конца (PNG — IEND, JPEG — FFD9) — оборванный рендером кадр рисуем заново."""
    try:
        size = path.stat().st_size
        if size < 64:
            return False
        with path.open("rb") as fh:
            fh.seek(-12, os.SEEK_END)
            tail = fh.read(12)
        if path.suffix.lower() in (".jpg", ".jpeg"):
            return tail.rstrip(b"\x00")[-2:] == b"\xff\xd9"
        return tail[4:8] == b"IEND"
    except OSError:
        return False


def frame_name(f: int, ext: str = "png") -> str:
    """Имя кадра в папке куска — по номеру кадра серии (так пишет рендер анимации Blender: ``f_#####``)."""
    return f"f_{int(f):05d}.{ext}"


def missing_frames(cache: Path, keys: list[dict]) -> dict[str, list[int]]:
    """Каких кадров нет в кэше по кускам: ``{key: [номер кадра серии, …]}``."""
    out = {}
    for c in keys:
        d = cache / c["key"]
        need = [f for f in range(c["f0"], c["f1"] + 1) if not frame_ok(d / frame_name(f, c.get("ext", "png")))]
        if need:
            out[c["key"]] = need
    return out


def audio_envelope(path: Path | str, fps: int = FPS) -> list[float]:
    """Огибающая озвучки (RMS на кадр) — для лип-синка; без ffmpeg/файла — пусто."""
    try:
        proc = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                              capture_output=True, check=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return []
    import array

    pcm = array.array("h")
    pcm.frombytes(proc.stdout[: len(proc.stdout) // 2 * 2])
    step = 8000 // fps
    out = []
    for i in range(0, len(pcm), step):
        chunk = pcm[i:i + step]
        if not chunk:
            break
        out.append(math.sqrt(sum(x * x for x in chunk) / len(chunk)) / 32768.0)
    return out


DEFAULT_BLENDER = ["D:/vvf-offload/tools/blender/blender-4.5.15-windows-x64/blender.exe", "/usr/bin/blender"]


def find_blender(explicit: str | None = None) -> str | None:
    for c in [explicit or "", os.environ.get("BLENDER_BIN", ""), *DEFAULT_BLENDER]:
        if c and Path(c).exists():
            return c
    return shutil.which("blender")


def packs_root() -> Path:
    from app.settings import settings

    return Path(settings.motion_packs_dir)


def scene_config(job: dict, work: Path, cache: Path, packs: Path | None = None) -> dict:
    packs = packs or packs_root()

    def need(rel: str) -> str:
        p = packs / rel
        if not p.is_file():
            raise FileNotFoundError(f"нет {rel} — python tools/fetch_packs.py --only {rel.split('/')[0]}")
        return str(p)

    extra: dict = {}
    if {"floor22", "room"} & {str(s_.get("location")) for s_ in job.get("shots") or []}:
        # ч.5: коридор этажа и комната — модели Kenney Furniture Kit (CC0); фото в переписке — снятые заранее PNG
        extra["kenney"] = str(Path(need("kenney-furniture/wall.glb")).parent)
    if job.get("photos"):
        extra["photos"] = dict(job["photos"])
    return {"job": str(work / "job.json"), "cache": str(cache), "out_dir": str(work), **extra,
            "character": need("kaykit-adventurers/Rogue_Hooded.glb"),
            "animations": [need(f"kaykit-animations/{n}.glb") for n in ("Rig_Medium_General", "Rig_Medium_MovementBasic",
                                                                          "Rig_Medium_Simulation")],
            "samples": preset(job)["samples"],
            "quality": preset(job)["name"], "preset": preset(job),
            "font_display": ["C:/Windows/Fonts/bahnschrift.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf"],
            "font_ui": ["C:/Windows/Fonts/bahnschrift.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"],
            "font_symbols": ["C:/Windows/Fonts/seguisym.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]}


class RenderError(RuntimeError):
    pass


def frame_spread(path: Path | str, samples: int = 8) -> list[float]:
    """Разброс яркости (СКО, 0..255) кадров видео в ``samples`` точках — однотонный кадр ≈ 0."""
    from app.dubbing import probe_duration

    try:
        dur = float(probe_duration(Path(path)) or 0.0)
    except Exception:  # noqa: BLE001 - нечитаемый файл — пустой список, вызывающий считает браком
        return []
    out = []
    for i in range(samples):
        t = dur * (i + 0.5) / samples
        proc = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
                               "-vf", "scale=72:128,format=gray", "-f", "rawvideo", "-"], capture_output=True, check=False)
        px = proc.stdout
        if not px:
            continue
        mean = sum(px) / len(px)
        out.append(math.sqrt(sum((x - mean) ** 2 for x in px) / len(px)))
    return out


def video_flat(path: Path | str, samples: int = 8, threshold: float = 3.0) -> bool:
    """Видео однотонное (зелёная основа холста без слоёв, чёрный/пустой рендер): все пробы почти без разброса."""
    spread = frame_spread(path, samples)
    return not spread or max(spread) < threshold


def split_chunks(keys: list[dict], todo: dict[str, list[int]], workers: int) -> list[list[dict]]:
    """Куски с недостающими кадрами — по процессам поровну по числу кадров (жадно, крупные первыми)."""
    groups: list[list[dict]] = [[] for _ in range(max(1, workers))]
    load = [0] * len(groups)
    for c in sorted((c for c in keys if c["key"] in todo), key=lambda c: -len(todo[c["key"]])):
        i = load.index(min(load))
        groups[i].append(c)
        load[i] += len(todo[c["key"]])
    return [g for g in groups if g]


def _run_logged(cmd: list[str], log_path: Path, on_progress: Callable[[int, int], Any], should_stop: Callable[[], bool] | None,
                deadline: float, cwd: Path | None = None) -> tuple[int, str | None]:
    """Процесс со строками ``PROGRESS n total`` в stdout: лог в файл, прогресс наружу; отмена/лимит — убить только
    этот процесс (по его PID). → (код выхода, причина остановки или None)."""
    stop = None
    with log_path.open("w", encoding="utf-8", errors="replace") as log:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                errors="replace", cwd=str(cwd) if cwd else None)
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                log.write(line)
                if not line.startswith("PROGRESS "):
                    continue
                log.flush()
                parts = line.split()
                try:
                    on_progress(int(parts[1]), int(parts[2]))
                except (IndexError, ValueError):
                    pass
                if should_stop is not None and should_stop():
                    stop = "рендер остановлен (задание отменено) — готовые кадры в кэше"
                elif time.time() > deadline:
                    stop = "рендер дольше лимита — остановлен (кадры в кэше, повторный запуск продолжит)"
                if stop:
                    proc.kill()
                    break
        finally:
            code = proc.wait()
    return code, stop


def node_bin() -> str | None:
    return os.environ.get("NODE_BIN") or shutil.which("node")


def seriesview_bundle() -> Path:
    """Бандл рисования three.js (``npm run build:seriesview``); нет или старше исходников — собрать esbuild'ом."""
    root = Path(__file__).resolve().parent.parent
    out = root / "node" / "scene3d" / "dist" / "seriesview.js"
    src = sorted((root / "node" / "scene3d" / "src").glob("*.ts"))
    if out.is_file() and all(p.stat().st_mtime <= out.stat().st_mtime for p in src):
        return out
    esb = next((d / "node_modules" / "esbuild" / "bin" / "esbuild" for d in [root, *root.parents]
                if (d / "node_modules" / "esbuild" / "bin" / "esbuild").is_file()), None)
    node = node_bin()
    if esb is None or node is None:
        raise RenderError("нет esbuild/node для бандла seriesview.js — npm install, потом npm run build:seriesview")
    subprocess.run([node, str(esb), str(root / "node" / "scene3d" / "src" / "seriesview.ts"), "--bundle", "--format=iife",
                    "--platform=browser", "--target=chrome110", "--minify", f"--outfile={out}", "--log-level=warning"],
                   check=True, cwd=str(root))
    return out


def three_plan(job: dict, keys: list[dict], todo: dict[str, list[int]], pose: Path, cache: Path, work: Path,
               bundle: Path | str) -> dict:
    """План рисования для ``node/scene3d/series_render.mjs``: куски с недостающими кадрами, размер кадра пресета."""
    pr = preset(job)
    scale = float(pr.get("scale", 100))
    return {"dir": str(pose), "cache": str(cache), "bundle": str(bundle), "gl": os.environ.get("SERIES3D_THREE_GL", "gpu"),
            "width": int(round(1080 * scale / 100 / 2)) * 2, "height": int(round(1920 * scale / 100 / 2)) * 2, "quality": 95,
            "spec": three_spec(job), "timing": str(work / "three_timing.json"),
            "chunks": [{**{k: c[k] for k in ("key", "n", "f0", "f1", "fx", "ext")}, "todo": todo[c["key"]]}
                       for c in keys if c["key"] in todo]}


def render_threejs(job: dict, perf: dict, keys: list[dict], todo: dict[str, list[int]], *, work: Path, cache: Path, exe: str,
                   base: dict, progress: Callable[[float, str], Any] | None = None,
                   should_stop: Callable[[], bool] | None = None, timeout: float = 4 * 3600) -> dict:
    """Бэкенд threejs: (1) один Blender собирает сцену и выгружает позы нужных кадров без рендера (``work/pose``);
    (2) один Chromium рисует кадры в кэш кусков. Строго по очереди — тяжёлый процесс один (память ПК)."""
    from app import series3d_pose as PZ

    t0 = time.time()
    deadline = t0 + timeout
    root = Path(__file__).resolve().parent.parent
    pose = work / "pose"
    pose.mkdir(parents=True, exist_ok=True)
    for name in ("frames.bin", "scene.json", "scene.glb"):
        (pose / name).unlink(missing_ok=True)
    frames = PZ.export_frames(keys, todo)
    total = sum(len(v) for v in todo.values())
    cfg = {**base, "out_dir": str(work), "backend": "threejs", "chunks": keys, "todo": {}, "pose_frames": frames,
           "export_dir": str(pose)}
    (work / "report.json").unlink(missing_ok=True)
    (work / "config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=1), "utf-8")
    scene = root / "tools" / "blender" / "series_scene.py"

    def say(p: float, note: str) -> None:
        if progress:
            progress(p, note)

    say(0.0, f"Blender: позы {len(frames)} кадров (без рендера), потом three.js: {total} кадров")
    code, stop = _run_logged([exe, "-b", "--factory-startup", "-P", str(scene), "--", "--config", str(work / "config.json")],
                             work / "blender.log", lambda n, m: say(0.25 * n / max(1, m), f"позы {n}/{m}"), should_stop, deadline)
    if stop:
        raise RenderError(stop)
    if code != 0 or not (pose / "frames.bin").is_file():
        tail = (work / "blender.log").read_text("utf-8", errors="replace")[-600:]
        raise RenderError(f"Blender (позы) упал (код {code}). {tail}")
    t_pose = round(time.time() - t0, 1)
    node = node_bin()
    if not node:
        raise RenderError("нет node для рендера three.js (NODE_BIN)")
    plan = three_plan(job, keys, todo, pose, cache, work, seriesview_bundle())
    (work / "three_plan.json").write_text(json.dumps(plan, ensure_ascii=False), "utf-8")
    t1 = time.time()
    code, stop = _run_logged([node, str(root / "node" / "scene3d" / "series_render.mjs"), "--plan", str(work / "three_plan.json")],
                             work / "three.log", lambda n, m: say(0.25 + 0.74 * n / max(1, m), f"кадр {n}/{m} (three.js)"),
                             should_stop, deadline)
    if stop:
        raise RenderError(stop)
    if code != 0:
        tail = (work / "three.log").read_text("utf-8", errors="replace")[-800:]
        raise RenderError(f"three.js-рендер упал (код {code}); кадры в кэше — повторный запуск продолжит. {tail}")
    timing = json.loads((work / "three_timing.json").read_text("utf-8")) if (work / "three_timing.json").is_file() else {}
    return {"pose_sec": t_pose, "render_sec": round(time.time() - t1, 1), "frames": total, "pose_frames": len(frames),
            "ms_per_frame": timing.get("ms_per_frame"), "black": timing.get("black"), "warnings": timing.get("warnings")}


def render_local(job: dict, out_mp4: Path | str, *, work: Path | str, cache: Path | str | None = None,
                 blender: str | None = None, progress: Callable[[float, str], Any] | None = None,
                 only: list[int] | None = None, stills: list[int] | None = None, timeout: float = 4 * 3600,
                 should_stop: Callable[[], bool] | None = None, workers: int | None = None) -> dict:
    """Рендер серии Blender'ом здесь: сцена один раз на процесс → недостающие кадры кусков в кэш (возобновляемо)
    → склейка в mp4 без звука. ``progress(доля 0..1, заметка)``; ``only`` — номера планов; ``stills`` — только эти
    кадры (номера с 1, PNG в ``work``) для проверки; ``should_stop()`` — True: остановить Blender (отмена задания);
    ``workers`` — процессов Blender (по умолчанию из пресета: куски делятся между ними). Бэкенд ``threejs``
    (``job["render_backend"]``, см. ``render_threejs``): Blender только ставит позы, кадры рисует three.js; склейка та же."""
    import threading

    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    cache = Path(cache or work / "cache")
    cache.mkdir(parents=True, exist_ok=True)
    exe = find_blender(blender)
    if not exe:
        raise RenderError("нет Blender: задай BLENDER_BIN (portable 4.5 LTS) — рендер 3D-серии идёт на компьютере с GPU")
    pr = preset(job)
    perf = perform(job)
    keys = chunk_keys(job, perf)
    if only is not None:
        keys = [c for c in keys if c["n"] in set(only)]
    todo = missing_frames(cache, keys)
    total_missing = sum(len(v) for v in todo.values())
    (work / "job.json").write_text(json.dumps(job, ensure_ascii=False), "utf-8")
    base = scene_config(job, work, cache)
    scene = Path(__file__).resolve().parent.parent / "tools" / "blender" / "series_scene.py"
    t0 = time.time()
    groups = [keys] if stills else split_chunks(keys, todo, int(workers or pr["workers"]))
    done = [0] * len(groups)
    lock = threading.Lock()
    stop = {"err": None}
    procs: list[subprocess.Popen] = []

    def run(i: int, group: list[dict]) -> None:
        wdir = work if len(groups) == 1 else work / f"w{i}"
        wdir.mkdir(parents=True, exist_ok=True)
        cfg = {**base, "out_dir": str(wdir), "chunks": group, "todo": {c["key"]: todo[c["key"]] for c in group
                                                                      if c["key"] in todo}}
        if stills:
            cfg["stills"] = [int(x) for x in stills]
        (wdir / "config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=1), "utf-8")
        with (wdir / "blender.log").open("w", encoding="utf-8", errors="replace") as log:
            proc = subprocess.Popen([exe, "-b", "--factory-startup", "-P", str(scene), "--", "--config",
                                     str(wdir / "config.json")], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding="utf-8", errors="replace")
            procs.append(proc)
            assert proc.stdout is not None
            for line in proc.stdout:
                log.write(line)
                if not line.startswith("PROGRESS "):
                    continue
                log.flush()
                try:
                    n = int(line.split()[1])
                except (IndexError, ValueError):
                    continue
                with lock:
                    done[i] = n
                    total = sum(done)
                    if progress:
                        progress(min(0.99, total / max(1, total_missing)), f"кадр {total}/{total_missing}")
                    if should_stop is not None and should_stop():
                        stop["err"] = "рендер остановлен (задание отменено) — готовые кадры в кэше"
                    if time.time() - t0 > timeout:
                        stop["err"] = "рендер дольше лимита — остановлен (кадры в кэше, повторный запуск продолжит)"
                    if stop["err"]:
                        for p_ in procs:
                            p_.kill()
                        break
            code = proc.wait()
        if code != 0 and not stop["err"]:
            tail = (wdir / "blender.log").read_text("utf-8", errors="replace")[-600:]
            stop["err"] = f"Blender упал (код {code}); кадры в кэше — повторный запуск продолжит. {tail}"

    if backend(job) == "threejs" and not stills:
        three = None
        if total_missing:
            three = render_threejs(job, perf, keys, todo, work=work, cache=cache, exe=exe, base=base, progress=progress,
                                   should_stop=should_stop, timeout=timeout)
        reports = [json.loads(p_.read_text("utf-8")) for p_ in sorted(work.glob("report.json"))] if total_missing else []
        report = {**(reports[0] if reports else {}), "backend": "threejs", **({"threejs": three} if three else {})}
        return _stitch(job, perf, keys, cache, work, out_mp4, pr, t0, total_missing, report, 1, progress)
    if total_missing or stills:
        for old in [work / "report.json", *work.glob("w*/report.json")]:
            old.unlink(missing_ok=True)   # отчёт прошлого прогона (другие планы) не выдавать за этот
        if progress:
            progress(0.0, f"сцена Blender: {total_missing} кадров к рендеру из {perf['frames']}, процессов {len(groups)}")
        threads = [threading.Thread(target=run, args=(i, g), daemon=True) for i, g in enumerate(groups)]
        for i, th in enumerate(threads):
            th.start()
            if i + 1 < len(threads):
                time.sleep(2.0)   # второй процесс стартует со сдвигом — не толкаются на загрузке сцены
        for th in threads:
            th.join()
        if stop["err"]:
            raise RenderError(stop["err"])
    reports = [json.loads(p_.read_text("utf-8")) for p_ in sorted(work.glob("report.json")) + sorted(work.glob("w*/report.json"))]
    report = reports[0] if reports else {}
    if len(reports) > 1:
        report = {**report, "workers": [{k: r.get(k) for k in ("setup_sec", "render_sec", "rendered")} for r in reports]}
    render_sec = round(time.time() - t0, 1)
    if stills:
        return {"ok": True, "stills": [str(work / f"still_{int(f):04d}.png") for f in stills], "report": report,
                "seconds": render_sec}
    return _stitch(job, perf, keys, cache, work, out_mp4, pr, t0, total_missing, report, len(groups), progress)


def _stitch(job: dict, perf: dict, keys: list[dict], cache: Path, work: Path, out_mp4: Path | str, pr: dict, t0: float,
            total_missing: int, report: dict, processes: int, progress: Callable[[float, str], Any] | None) -> dict:
    """Кадры кусков из кэша → mp4 без звука (общая склейка обоих бэкендов)."""
    left = missing_frames(cache, keys)
    if left:
        raise RenderError(f"не дорисованы куски: {', '.join(list(left)[:5])} — повторный запуск продолжит")
    out = Path(out_mp4)
    out.parent.mkdir(parents=True, exist_ok=True)
    seq = work / "seq"
    if seq.exists():
        shutil.rmtree(seq)
    seq.mkdir()
    n = 0
    exts = set()
    for c in keys:
        ext = c.get("ext", "png")
        exts.add(ext)
        for f in range(c["f0"], c["f1"] + 1):
            n += 1
            src = cache / c["key"] / frame_name(f, ext)
            dst = seq / f"f_{n:05d}.{ext}"
            try:
                os.link(src, dst)
            except OSError:
                shutil.copyfile(src, dst)
    ext = exts.pop() if len(exts) == 1 else "png"
    scale = "scale=1080:1920:flags=lanczos" if pr.get("upscale") else "scale=trunc(iw/2)*2:trunc(ih/2)*2"
    t_enc = time.time()
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-framerate", str(perf["fps"]), "-i", str(seq / f"f_%05d.{ext}"),
                    "-vf", f"{scale},noise=alls=3:allf=t,format=yuv420p", "-c:v", "libx264", "-preset", "slow",
                    "-crf", "17", "-profile:v", "high", "-movflags", "+faststart", str(out)], check=True)
    shutil.rmtree(seq, ignore_errors=True)
    if video_flat(out):
        raise RenderError(f"склейка однотонная (пустые кадры?) — {out.name} не годится, кадры в кэше проверь")
    if progress:
        progress(1.0, "склеено")
    return {"ok": True, "out": str(out), "frames": n, "rendered": total_missing, "cached": n - total_missing,
            "seconds": round(time.time() - t0, 1), "encode_sec": round(time.time() - t_enc, 1), "report": report,
            "chunks": len(keys), "preset": pr["name"], "processes": processes, "backend": backend(job)}


# --------------------------------------------------------------------------- #
# Для сборки холста (app/stories_3d.py)
# --------------------------------------------------------------------------- #

def result_for(job: dict, perf: dict | None = None) -> dict:
    """Режиссура в виде ``series3d.direct``: кадры (для обложек), события, звуки, локация."""
    perf = perf or perform(job)
    cam_map = {"overhead": "overhead", "face": "face", "ots": "ots", "detail": "detail"}
    shots = [{"n": s["n"], "t0": s["t0"], "t1": s["t1"], "text": s["text"], "role": s.get("role") or "",
              "action": s["action"], "camera": cam_map[s["camera"]], "spot": s.get("target") or "",
              "emotion": s.get("emotion"), "speaks": bool(s.get("speaks")), "speaker": s.get("speaker") or "narrator",
              "by": s.get("by")} for s in job["shots"]]
    return {"shots": shots, "events": job.get("events") or [], "sfx": sfx_for(job, perf), "location": "elevator",
            "faces": face_bands(perf),
            "checks": [], "notes": [], "look": job.get("look") or {}, "director": job.get("director")}
