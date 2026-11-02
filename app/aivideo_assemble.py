"""ИИ-видео: сборка серии в обычный холст-клип (docs/design/ai-video-projects.md §1.7).

План с готовым видео (этап ``video``) — слой ``video`` со звуком модели (``audio: true``,
скромное усиление ``VIDEO_GAIN_DB``); склейки — по НАСТОЯЩИМ длительностям видео, агент может
обрезать каждое окном ``video_in``/``video_out`` (секунды файла). Реплики длиннее видео — последний
кадр стоит (``freeze``), сборка об этом пишет.

План без видео — запасной путь: слой ``image`` с выбранным кадром и движением камеры:

* ``static`` / ``push_in`` / ``pull_out`` / ``pan_*`` / ``tilt_up|down`` — Ken Burns ключами слоя
  (масштаб и сдвиг; кадр с запасом по краям, чтобы не показать фон);
* ``parallax_push`` / ``orbit`` / ``float`` / ``dolly_zoom`` / ``tilt_reveal`` — наша 3D-камера
  (``app/camera3d.py``) на отрезке плана; на склейке камера возвращается в покой.

Реплики: озвучка этапа ``voice`` (голос персонажа, TTS из ``app/dubbing.py``) — каждая своим слоем
``audio`` на своём времени в плане (правится в редакторе по одной). Персонаж ``speech sfx`` вместо
голоса «говорит» записью пака (окно не короче ``SFX_MIN_SEC``, подпись — если ``speech_captions``),
``speech none`` молчит. Субтитры клипа — из тех же реплик с их окнами (слой ``captions``, стиль
субтитров проекта). Музыка — трек проекта (паки, бесплатно), под репликами приглушена
(``music_duck``). Результат — обычный клип: правится в редакторе, рендерится и публикуется как всё
остальное. Каждая сборка — новый клип.
"""

from __future__ import annotations

from typing import Any

from app import aivideo as V
from app.settings import settings
from app import aivideo_gen as G

LEAD_SEC = 0.35  # от начала плана до первой реплики
GAP_SEC = 0.2  # между репликами
TAIL_SEC = 0.45  # после последней реплики
FADE_SEC = 0.3
SFX_MIN_SEC = 1.0  # реплика-звук персонажа, который не говорит (speech sfx): окно подписи
OVERSCAN = 1.08  # запас кадра: камера/наезд не показывают края
PAN_SCALE = 1.18
PAN_SHIFT = 0.065
LAYER_PREFIX = "aiv_"
CAMERA_PRESET_MOVES = {"parallax_push", "orbit", "float", "dolly_zoom", "tilt_reveal"}
# Один плоский кадр — не сцена со слоями: сильный наклон/облёт показывает его край. Мягче и с запасом.
PRESET_INTENSITY = {"parallax_push": 0.8, "orbit": 0.35, "float": 0.8, "dolly_zoom": 0.35, "tilt_reveal": 0.3}
PRESET_OVERSCAN = 1.35
SOFT = "inOutQuad"
VIDEO_GAIN_DB = -4.0  # звук видеомодели (фон, шумы) — тише голосов
MUSIC_DUCK_DB = -10.0  # музыка под репликой
MIN_WINDOW_SEC = 0.5


def _keys(move: str, length: float) -> tuple[float, dict]:
    """Ken Burns плана: (база масштаба, ключи слоя в его локальном времени)."""
    end = round(max(0.1, length), 3)
    if move == "push_in":
        return 1.0, {"scale": [{"t": 0, "v": OVERSCAN, "ease": SOFT}, {"t": end, "v": 1.24}]}
    if move == "pull_out":
        return 1.0, {"scale": [{"t": 0, "v": 1.24, "ease": SOFT}, {"t": end, "v": OVERSCAN}]}
    if move in ("pan_left", "pan_right"):
        a, b = (0.5 - PAN_SHIFT, 0.5 + PAN_SHIFT) if move == "pan_left" else (0.5 + PAN_SHIFT, 0.5 - PAN_SHIFT)
        return PAN_SCALE, {"x": [{"t": 0, "v": a, "ease": SOFT}, {"t": end, "v": b}]}
    if move in ("tilt_up", "tilt_down"):
        a, b = (0.5 - PAN_SHIFT, 0.5 + PAN_SHIFT) if move == "tilt_up" else (0.5 + PAN_SHIFT, 0.5 - PAN_SHIFT)
        return PAN_SCALE, {"y": [{"t": 0, "v": a, "ease": SOFT}, {"t": end, "v": b}]}
    if move in CAMERA_PRESET_MOVES:
        return PRESET_OVERSCAN, {}
    # static: лёгкое «дыхание», чтобы кадр не был мёртвым
    return 1.0, {"scale": [{"t": 0, "v": OVERSCAN, "ease": SOFT}, {"t": end, "v": OVERSCAN + 0.035}]}


def preset_camera(move: str, t0: float, t1: float, total: float, camera: list[dict], layers: list[dict],
                  has_next: bool) -> list[dict]:
    """Наша 3D-камера (``app/camera3d.py``) на стоп-кадре [t0, t1]; на склейке — возврат в покой.
    Общая для ИИ-видео и историй (app/stories_gen.py). Не встала — ``camera3d.PresetError``."""
    from app.camera3d import apply_preset
    from app.motion import DEFAULT_FOV, normalize_camera

    length = min(12.0, max(0.3, t1 - t0))
    res = apply_preset(move, t0, length, PRESET_INTENSITY.get(move, 1.0), camera=camera, layers=layers,
                       clip_duration=total)
    camera = res["camera"]
    if move == "parallax_push":
        # Плоский кадр: лёгкий наклон пресета ничего не даёт, а перспективный перенос
        # крупной (×1.35) плоскости при наезде срезал край. Чистый dolly — аффинно.
        camera = [{**k, "rotX": 0.0} if t0 - 1e-3 <= k["t"] <= t1 + 1e-3 and "rotX" in k else k for k in camera]
        res["keys"] = [{**k, "rotX": 0.0} if "rotX" in k else k for k in res["keys"]]
    last = res["keys"][-1] if res["keys"] else None
    if has_next and last is not None:
        hold = {k: v for k, v in last.items() if k != "ease"}
        camera = normalize_camera(camera + [
            {**hold, "t": round(t1 - 0.02, 3), "ease": "step"},
            {"t": round(t1, 3), "zoom": 1.0, "x": 0.5, "y": 0.5, "z": 0, "rotX": 0, "rotY": 0, "fov": DEFAULT_FOV}])
    return camera


def _line_ref(store: Any, project_id: int, shot: dict, i: int, line: dict) -> dict | None:
    voice = G._voice_of(store, project_id, line["who"], line.get("emotion") or "")
    ref = V.selected_ref(store, project_id, "line", int(shot["id"]), "line_audio", str(i))
    if not G._fresh(ref) or ref["prompt"] != G._line_key(voice, line["text"]):
        return None
    return ref if G._asset_path(store, ref) else None


def ensure_voice(store: Any, script: dict) -> dict:
    """Озвучка всех реплик: сухой прогон — сразу (0 ₽); платная — только этапом voice с одобрением."""
    est = G.estimate(store, script["project_id"], "voice", script["id"])
    if not est["count"]:
        return {"status": "ready"}
    if est["paid"]:
        raise G.BudgetError(
            f"озвучка {est['count']} реплик не сделана (≈ {est['total_rub']:.2f} ₽) — этап voice: aiv_request_approval"
            "(stage voice) → «Разрешить» владельца → сборка")
    return G.request_stage(store, script["project_id"], "voice", script["id"], requested_by="assemble")


def _video_of(store: Any, project_id: int, shot: dict) -> dict | None:
    """Выбранное видео плана с окном: {ref, asset_id, from, window, duration}."""
    ref = V.selected_ref(store, project_id, "shot", int(shot["id"]), "shot_video")
    if not ref or not ref.get("asset_id") or G._asset_path(store, ref) is None:
        return None
    try:
        duration = float(store.get_montage_asset(int(ref["asset_id"])).get("duration_sec") or 0)
    except KeyError:
        return None
    if duration <= 0:
        return None
    a = min(max(0.0, float(shot.get("video_in") or 0.0)), max(0.0, duration - MIN_WINDOW_SEC))
    b = float(shot["video_out"]) if shot.get("video_out") is not None else duration
    b = min(duration, max(a + MIN_WINDOW_SEC, b))
    return {"ref": ref, "asset_id": int(ref["asset_id"]), "from": round(a, 3), "window": round(b - a, 3),
            "duration": round(duration, 3)}


def timeline(store: Any, script: dict) -> dict:
    """Окна планов и реплик (секунды клипа). План с видео — длина окна видео; без видео — длительность
    плана; реплики длиннее — план удлиняется (у видео стоит последний кадр)."""
    from app.dubbing import probe_duration

    project_id = int(script["project_id"])
    t = 0.0
    shots, lines = [], []
    for shot in script["shots"]:
        placed = []
        for i, line in enumerate(shot["dialogue"]):
            character, speech = G.speech_of(store, project_id, line["who"])
            if speech == "none":
                continue
            ref = _line_ref(store, project_id, shot, i, line)
            if ref is None:
                raise V.AivError(f"план {shot['idx']}: нет озвучки реплики {i + 1} — этап voice")
            d = max(0.2, probe_duration(G._asset_path(store, ref)))
            caption = True
            if speech == "sfx":
                # Звук короткий — окно реплики не короче SFX_MIN_SEC (успеть прочитать подпись).
                d = max(d, SFX_MIN_SEC)
                caption = bool((character or {}).get("speech_captions", True))
            placed.append((line, ref, d, caption, speech))
        need = (LEAD_SEC + sum(p[2] for p in placed) + GAP_SEC * (len(placed) - 1) + TAIL_SEC) if placed else 0
        video = _video_of(store, project_id, shot)
        base = video["window"] if video else float(shot["duration"])
        length = round(max(base, need), 3)
        cursor = t + LEAD_SEC
        for i, (line, ref, d, caption, speech) in enumerate(placed):
            lines.append({"shot": shot["idx"], "n": i + 1, "who": line["who"], "text": line["text"],
                          "start": round(cursor, 3), "end": round(cursor + d, 3), "asset_id": int(ref["asset_id"]),
                          "caption": caption, "speech": speech})
            cursor += d + GAP_SEC
        shots.append({"shot": shot, "start": round(t, 3), "end": round(t + length, 3), "video": video,
                      "stretched": round(length - base, 2)})
        t += length
    return {"shots": shots, "lines": lines, "total": round(t, 3)}


def _music_duck(lines: list[dict]) -> list[dict]:
    """Окна реплик (слитые, если между ними < 0.4 с) — музыка в них тише."""
    spans: list[list[float]] = []
    for line in lines:
        a, b = max(0.0, line["start"] - 0.1), line["end"] + 0.15
        if spans and a - spans[-1][1] < 0.4:
            spans[-1][1] = max(spans[-1][1], b)
        else:
            spans.append([a, b])
    return [{"start": round(a, 3), "end": round(b, 3), "gain_db": MUSIC_DUCK_DB} for a, b in spans]


def assemble(store: Any, script_id: int, music_track_id: int | None = None, captions_style: str | None = None,
             background: Any = None, title: str | None = None) -> dict:
    """Серия → новый холст-клип. Нужно видео (этап video) или кадр (этап keyframes) у каждого плана;
    озвучка — этап voice (сухой прогон делается тут же бесплатно)."""
    from app.audio_edit import normalize_audio
    from app.camera3d import PresetError
    from app.canvas import MIN_DURATION_SEC, base_seconds, create_canvas_clip
    from app.clip_subtitles import save_lines
    from app.motion import normalize_camera, normalize_layers

    script = V.get_script(store, script_id)
    project_id = int(script["project_id"])
    style = V.get_style(store, project_id)
    if not script["shots"]:
        raise V.AivError("в серии нет планов")
    frames = {}
    missing = []
    for n, shot in enumerate(script["shots"]):
        if _video_of(store, project_id, shot):
            continue
        # Стоп-кадр плана — его стартовый кадр (свой или общий: конечный кадр прошлого плана; у модели
        # без конечного кадра — последний кадр видео прошлого плана).
        ref, shared = G.start_frame_ref(store, project_id, script["shots"], n)
        if not ref:
            where = ("последний кадр видео плана {n}: этап video" if G.tail_mode(store, project_id, script_id)
                     else "конечный кадр плана {n}: этап keyframes")
            missing.append(f"{shot['idx']} (старт — {where.format(n=shared)})" if shared else str(shot["idx"]))
        else:
            frames[shot["id"]] = ref
    if missing:
        raise V.AivError(f"нет ни видео, ни кадра у планов {', '.join(missing)} — этап keyframes / video "
                         "(aiv_request_approval)")
    voice = ensure_voice(store, script)
    script = V.get_script(store, script_id)
    plan = timeline(store, script)
    total = plan["total"]
    if total < MIN_DURATION_SEC:
        plan["shots"][-1]["end"] = MIN_DURATION_SEC
        total = MIN_DURATION_SEC
    if total > base_seconds():
        raise V.AivError(f"серия {total:.1f} с длиннее предела холста {base_seconds():.0f} с — сократи планы")

    clip = create_canvas_clip(store, total, background or {"kind": "solid", "colors": ["#0b0b10"]},
                              title or f"{script['title']}"[:100],
                              f"ИИ-видео: серия {script['episode_no']} «{script['title']}». {script['logline']}"[:500])
    plan_id = int(clip["id"])
    layers: list[dict] = []
    camera: list[dict] = []
    notes: list[str] = []
    for n, item in enumerate(plan["shots"]):
        shot, t0, t1 = item["shot"], item["start"], item["end"]
        nxt = plan["shots"][n + 1]["shot"] if n + 1 < len(plan["shots"]) else None
        fade_in = shot["transition_in"] == "fade" and n > 0
        fade_out = bool(nxt and nxt["transition_in"] == "fade")
        end = min(total, t1 + (FADE_SEC if fade_out else 0.0))
        video = item["video"]
        layer_id, name = f"{LAYER_PREFIX}shot_{shot['idx']}", f"План {shot['idx']}"
        if video:
            layer = {"id": layer_id, "type": "video", "name": name, "start": round(t0, 3), "end": round(end, 3),
                     "track": 1, "x": 0.5, "y": 0.5, "z": 0, "scale": 1.0,
                     "props": {"asset_id": video["asset_id"], "w": 1080, "from": video["from"], "freeze": True,
                               "audio": True, "gain_db": VIDEO_GAIN_DB, "role": "ambience"}}
            if fade_in:
                layer["in"] = {"type": "fade", "duration": FADE_SEC}
            layers.append(layer)
            if video["ref"].get("stale"):
                notes.append(f"план {shot['idx']}: видео «устарело» (правили план или прошлое видео) — "
                             "перегенерируй (aiv_regenerate) или оставь")
            if item["stretched"] > 0.05:
                notes.append(f"план {shot['idx']}: реплики длиннее видео на {item['stretched']:.1f} с — последний "
                             "кадр стоит; перегенерируй видео длиннее или сократи реплику")
            continue
        move = (shot["camera"] or {}).get("move") or "static"
        base, keys = _keys(move, end - t0)
        layer = {"id": layer_id, "type": "image", "name": name,
                 "start": round(t0, 3), "end": round(end, 3), "track": 1, "x": 0.5, "y": 0.5, "z": 0,
                 "scale": base, "props": {"asset_id": int(frames[shot["id"]]["asset_id"]), "w": 1080}}
        if keys:
            layer["keys"] = keys
        if fade_in:
            layer["in"] = {"type": "fade", "duration": FADE_SEC}
        layers.append(layer)
        if shot.get("needs_motion"):
            notes.append(f"план {shot['idx']}: видео нет — стоп-кадр с движением камеры (этап video оживит)")
        if move in CAMERA_PRESET_MOVES:
            try:
                camera = preset_camera(move, t0, t1, total, camera, layers, nxt is not None)
            except PresetError as exc:
                notes.append(f"план {shot['idx']}: камера {move} не встала ({exc}) — остался Ken Burns")
        if item["stretched"] > 0.05:
            notes.append(f"план {shot['idx']} удлинён на {item['stretched']:.1f} с под реплики")

    for line in plan["lines"]:
        layers.append({"id": f"{LAYER_PREFIX}line_{line['shot']}_{line['n']}", "type": "audio",
                       "name": f"Реплика {line['shot']}.{line['n']} ({line['who']})", "start": line["start"],
                       "end": round(min(total, line["end"] + 0.05), 3),
                       "props": {"asset_id": line["asset_id"], "mode": "track", "volume": 1.0, "fadeIn": 0.02,
                                 "fadeOut": 0.05, "role": "sfx" if line.get("speech") == "sfx" else "voice"}})
    # Субтитры — реплики голосом и (если у персонажа включено) подписи к его звукам.
    captions = [{"start": l["start"], "end": l["end"], "text": l["text"]} for l in plan["lines"] if l["caption"]]
    if captions:
        save_lines(store, plan_id, captions)
        layers.append({"id": f"{LAYER_PREFIX}captions", "type": "captions", "name": "Субтитры", "start": 0.0,
                       "end": round(total, 3), "track": 6, "x": 0.5, "y": 0.72,
                       "props": {"style": captions_style or style.get("captions_style") or "hormozi"}})

    render = dict(store.get_clip_plan(plan_id).get("render_settings") or {})
    render["layers"] = normalize_layers(layers)
    render["camera"] = normalize_camera(camera)
    track_id = music_track_id if music_track_id not in (None, "", 0) else style.get("music_track_id")
    if track_id:
        try:
            store.get_audio_track(int(track_id))
            render.update({"music_on": True, "track_id": int(track_id), "music_start_sec": 0.0})
            if plan["lines"]:
                render["audio"] = normalize_audio({**(render.get("audio") or {}),
                                                   "music_duck": _music_duck(plan["lines"])})
        except (KeyError, TypeError, ValueError):
            notes.append(f"музыка: нет трека {track_id} — без музыки")
    store.set_clip_plan_render_settings(plan_id, render)
    mix = None
    if settings.auto_mix and plan["lines"]:
        # Автосведение (app/audio_mix.auto_mix): реплики над музыкой на 18 дБ, ducking по фразам, звук
        # видео-планов фоном, −14 LUFS. Не вышло — клип как собран (заметка).
        try:
            from app.audio_mix import auto_mix

            done = auto_mix(store, plan_id, compare=False)
            mix = {"voice_vs_music_db": done["after"]["voice_vs_music_db"], "changes": done["changes"]}
        except Exception as exc:  # noqa: BLE001 - сведение — надстройка, клип важнее
            notes.append(f"автосведение не вышло ({type(exc).__name__}: {str(exc)[:140]}) — audio_mix_report")
    store.db.execute("UPDATE aiv_scripts SET clip_plan_id = ?, status = 'assembled', updated_at = CURRENT_TIMESTAMP "
                     "WHERE id = ?", (plan_id, int(script_id)))
    fresh = store.get_clip_plan(plan_id)
    videos = sum(1 for s in plan["shots"] if s["video"])
    return {"ok": True, "clip_id": plan_id, "project_id": fresh["source_id"], "duration": round(total, 2),
            "shots": len(plan["shots"]), "video_shots": videos, "still_shots": len(plan["shots"]) - videos,
            "lines": len(plan["lines"]), "layers": len(render["layers"]),
            "camera_keys": len(render["camera"]), "voice": voice.get("status"),
            "timeline": [{"shot": s["shot"]["idx"], "start": s["start"], "end": s["end"],
                          "kind": "video" if s["video"] else "still"} for s in plan["shots"]],
            "editor_url": f"/projects/{fresh['source_id']}/candidates?clip={plan_id}", "notes": notes,
            **({"mix": mix} if mix else {}),
            "note": ("обычный холст-клип: правь в редакторе (слои aiv_*), рендер и публикация — как у любого клипа")}
