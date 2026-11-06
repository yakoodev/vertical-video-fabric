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
    DISPLAY, CEIL_LIGHT, HATCH, REACH, SHOULDER_R, MAX_PRESS_Z, CLIP_LEN, WALK_CLIP, WALK_FEET, MAX_WALK_STEPS,
    _walk_stance, WALK_STANCE, WALK_CYCLE_UNITS, STEP_LEN, _STANCE_CUM, _v, stance_units, walk_plan, walk_seconds,
    CLIPS_NEEDED, button_pos, reachable, facing, to_world, PRESS_TURN, KNOCK_POINT, press_stand, spot_for, LOCS,
    LOC_NAMES, LOC_TARGETS, loc_of, loc_world, loc_spot, loc_target, FILL_W, RIM_W, EXPOSURE, inside_loc,
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
    ots_ok, camera_plan, TITLE_Y, PART_Y, face_target, face_bands,
)
from app.series3d_blender_perform import (  # noqa: F401
    FPS, WALK_FOOT_X, WALK_PASS, STRIDE, DOOR_OPEN, VISEMES, VOWELS, _DIGITS, _letters, viseme_keys, lipsync,
    _pulse, _ang, _Track, perform, _beam_off_camera, planted_local, clip_to_world, walk_root, foot_slide,
    _posture_at, track_of,
)

SCENE_VERSION = 3          # меняй, когда меняется сцена Blender: кэш кусков станет недействительным


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
}


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
        if k == "blackout":
            add(S3.SFX["blackout"], t - 0.05, "блэкаут", "blackout")
            add(S3.SFX["power_down"], t, "гул глохнет", "power_down")
        elif k == "jolt":
            n = len([x for x in out if x["kind"] in ("jolt", "jolt2")])
            add(S3.SFX["jolt" if n % 2 == 0 else "jolt2"], t, "рывок", "jolt")
        elif k == "scrape":
            add(S3.SFX["scrape"], t, "скрежет", "scrape")
        elif k == "flash":
            add(S3.SFX["flash"], t, "вспышка", "flash")
        elif k == "knock":
            add(S3.SFX["knock"], t, "стук", "knock")
        elif k == "sting":
            add(S3.SFX["sting"], t, "акцент", "sting")
        elif k == "doors_close":
            add(SFX["doors_open"], t, "двери схлопнулись", "doors_close")
    for c in perf["contacts"]:
        if c["kind"] == "press":
            add(SFX["bell" if c.get("button") == "bell" else "press"], c["t"] - 0.01, "кнопка", "press")
        elif c["kind"] == "knock":
            add(SFX["knock_hit"], c["t"] - 0.01, "удар в дверь", "knock_hit")
        elif c["kind"] == "doors":
            add(SFX["doors_strain"], c["t"], "створки", "doors")
            if c.get("full"):
                add(SFX["doors_open"], c["t"] + 1.0, "двери разъехались", "doors_open")
    story = next((s for s in job["shots"] if s.get("role") not in ("hook", "")), None)
    if story:
        add(S3.SFX["tension"], story["t0"], "напряжение", "tension")
    b, back = perf.get("blackout"), perf.get("light_back")

    def bed(kind: str, t0: float, t1: float, label: str, fi: float, fo: float) -> None:
        if t1 - t0 >= 0.5:
            ref, under = S3.AMBIENCE[kind]
            out.append({"t": round(max(0.0, t0), 3), "ref": ref, "volume": 1.0, "label": label, "kind": kind,
                        "end": round(min(total, t1), 3), "role": "ambience", "under_db": under, "fade_in": fi, "fade_out": fo})

    if b is None:
        bed("hum", 0.0, total, "гул кабины", 0.4, 1.5)
    else:
        bed("hum", 0.0, b + 0.12, "гул кабины", 0.4, 0.18)
        bed("dark", b + 0.6, back or total, "гул шахты", 2.5, 1.0 if back else 2.0)
        if back:
            bed("hum", back, total, "гул кабины", 0.3, 1.5)
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


def shot_fx(perf: dict, mode: str = "auto") -> dict[int, dict]:
    """Какие дорогие эффекты нужны плану: объём — когда в кадре луч фонарика или мигает/гаснет свет; размытие
    движения — шаги, рывок/испуг, стук, двери, тряска камеры; глубина резкости — крупные планы и детали."""
    fps = perf["fps"]
    out: dict[int, dict] = {}
    cams = {c["n"]: c for c in perf.get("cameras") or []}
    walks = [(sg["start"], sg["start"] + 1.2) for sg in perf["track"] if sg["clip"] in ("Walking_A", "Walking_B", "Walking_C", "Hit_A",
                                                                                          "Sit_Floor_Down", "Sit_Floor_StandUp")]
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
        out[s["n"]] = {"volume": beam or flick or (s.get("location") or "cabin") != "cabin",
                       "blur": move or shake or hits or st, "dof": kind in ("face", "detail")}
    return out


def build_job(script: dict, words: list[dict], total: float, *, gemini: bool = True, client: Any = None,
              envelope: list[float] | None = None, lipsync_mode: str = "speaking", quality: str = DEFAULT_QUALITY,
              look: dict | None = None, seed: int = 7) -> dict:
    """Всё, что нужно сцене Blender (JSON): кадры с режиссурой, события, слова, огибающая голоса, качество."""
    plan = plan_shots(script, words, total, gemini=gemini, client=client)
    return {"version": SCENE_VERSION, "fps": FPS, "duration": round(float(total), 3), "title": script.get("title") or "",
            "shots": plan["shots"], "events": plan["events"], "words": plan["words"], "director": plan["director"],
            "speech": plan["speech"],
            "director_error": plan["director_error"], "envelope": [round(x, 4) for x in (envelope or [])],
            "lipsync": lipsync_mode if lipsync_mode in LIPSYNC_MODES else "speaking",
            "quality": quality if quality in QUALITY else DEFAULT_QUALITY, "look": look or {}, "seed": int(seed)}


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
              root / "tools" / "blender" / "series_places.py", root / "app" / "motion3d.py"):
        if p.is_file():
            h.update(p.read_bytes())
    return h.hexdigest()[:12]


def chunk_keys(job: dict, perf: dict) -> list[dict]:
    """Хэш куска — от покадровых дорожек его кадров (+ соседей на наплыв), плана камеры, качества и кода сцены."""
    code = _code_hash()
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
        key = hashlib.sha1(json.dumps(blob, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:16]
        out.append({**c, "key": key, "fx": fx.get(c["n"]), "ext": "jpg" if pr["frames"] == "JPEG" else "png"})
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

    return {"job": str(work / "job.json"), "cache": str(cache), "out_dir": str(work),
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


def render_local(job: dict, out_mp4: Path | str, *, work: Path | str, cache: Path | str | None = None,
                 blender: str | None = None, progress: Callable[[float, str], Any] | None = None,
                 only: list[int] | None = None, stills: list[int] | None = None, timeout: float = 4 * 3600,
                 should_stop: Callable[[], bool] | None = None, workers: int | None = None) -> dict:
    """Рендер серии Blender'ом здесь: сцена один раз на процесс → недостающие кадры кусков в кэш (возобновляемо)
    → склейка в mp4 без звука. ``progress(доля 0..1, заметка)``; ``only`` — номера планов; ``stills`` — только эти
    кадры (номера с 1, PNG в ``work``) для проверки; ``should_stop()`` — True: остановить Blender (отмена задания);
    ``workers`` — процессов Blender (по умолчанию из пресета: куски делятся между ними)."""
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
            "chunks": len(keys), "preset": pr["name"], "processes": len(groups)}


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
