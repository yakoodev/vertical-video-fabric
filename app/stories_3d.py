"""Истории: сборка эпизода на своём 3D-движке (``options.engine: "3d"``) — без картинок сцен и без платного.

Озвучка (Gemini TTS, бесплатно) → слова (Whisper; без него — по длине текста) → режиссура
``app.series3d.direct`` (локация-шаблон, герой-игрушка по карточке актёра, кадр на фразу, события на словах) →
рендер сцены ``node/scene3d`` в mp4 (headless Chromium, ``SCENE3D_GL``/``SCENE3D_WORKERS``) → видео в «Файлы для
монтажа» → холст-клип: 3D-видео во весь кадр, субтитры по 1–3 слова с подсветкой текущего, HUD-плашки фраз,
название серии и титр «ЧАСТЬ N+1» (сериал-катастрофа), звуки событий (настоящие записи паков), голос и тревожный
фон (``music_bed``): трек пака ``tension`` (CC0, без мелодии и ударных) тихо под голосом на всю серию — слой audio
``role: music``, петля копиями внахлёст, громкость по замеру озвучки (фон на ``BED_UNDER_VOICE_DB`` тише голоса).
Опция эпизода/мира ``music: none`` — без фона; трек из библиотеки (``music_track_id``) — вместо фона, как у 2D.
Гул кабины и шахты (``role: ambience``) — так же петлёй из режиссуры. Обложки — кадры 3D-видео по шаблону формата.

``options.renderer: blender`` — кино-качество: режиссура ``app.series3d_blender`` (действие/эмоция/план на фразу —
Gemini по схеме, иначе правила; лип-синк по огибающей озвучки), рендер — Blender. В Docker его нет: сборка ставит
задание в очередь хост-воркера (``app/render_queue.py``) и ждёт (эпизод остаётся «собираю клип», статус —
«рендер на компьютере… N%»); готово — тот же холст. ``SERIES3D_BLENDER_LOCAL=1`` и Blender рядом — рендер здесь же
(``tools/series3d_blender.py``).
"""

from __future__ import annotations

import hashlib
import json
import math
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any
from uuid import uuid4

from app import series3d as S3
from app import stories as S
from app import stories_disaster as D
from app.settings import settings

_log = logging.getLogger(__name__)

CAPTION_WORDS = 3  # субтитры по 1–3 слова, текущее подсвечено
ACTIVE_COLOR = "#ffd400"
SFX_MAX_SEC = 3.5
COVER_FRAMES = 3
# Тревожный фон серии — треки пака tension (tools/fetch_packs.py: CC0, петли без швов, громкость в items.json).
BED_PACK = "tension"
BED_UNDER_VOICE_DB = 22.0  # фон под речью тише голоса на столько (норма — 20…24 дБ)
BED_FADE_IN, BED_FADE_OUT = 1.5, 2.5
LOOP_XFADE = 1.0  # петля длиннее файла — копии внахлёст с перекрёстным затуханием
VOICE_LUFS_DEFAULT = -16.0  # озвучку не измерить — считаем такой


def voice_lufs(path: Path) -> float:
    """Громкость озвучки (BS.1770, тишина отсечена гейтом) — от неё громкость фона и гула."""
    from app.audio_mix import SILENT, ebur128

    try:
        value = float(ebur128(path)["i"])
    except Exception:  # noqa: BLE001 - нет ffmpeg/битый файл: фон по типовой громкости TTS
        return VOICE_LUFS_DEFAULT
    return value if value > SILENT + 1 else VOICE_LUFS_DEFAULT


def _pack_item(ref: str) -> dict | None:
    from app.motion_assets import pack_item_info

    try:
        return pack_item_info(ref)
    except Exception:  # noqa: BLE001 - пака нет — звука нет
        return None


def music_mode(world: dict, ep: dict) -> str:
    """Опция ``music`` эпизода (иначе мира): ``none`` — без фона, ``auto``/пусто — фон есть. У мира-катастрофы
    ``none`` стоит по умолчанию (2D: музыку ставят в приложении площадки) — тревожный фон 3D-серии он не выключает,
    выключить — ``music: none`` у эпизода."""
    ep_mode = str((ep.get("options") or {}).get("music") or "").strip().lower()
    if ep_mode:
        return ep_mode
    if str(world.get("format") or "") == "disaster":
        return ""
    return str(world.get("music") or "").strip().lower()


def music_beds() -> list[dict]:
    """Треки тревожного фона из пака (роль music), по имени файла; пака нет — пусто."""
    from app.motion_assets import load_packs

    pack = load_packs().get(BED_PACK) or {}
    if pack.get("kind") != "sfx":
        return []
    return sorted((dict(it, ref=f"sfx:{BED_PACK}/{it['file']}") for it in pack["_items"].values()
                   if it.get("role") == "music"), key=lambda it: it["file"])


def pick_bed(world: dict, ep: dict) -> dict | None:
    """Фон серии: ``options.music_bed`` (имя файла пака), иначе один на мир — по id мира (сериал узнаётся)."""
    beds = music_beds()
    if not beds:
        return None
    want = str((ep.get("options") or {}).get("music_bed") or "").strip()
    for bed in beds:
        if want and want in (bed["file"], bed["file"].rsplit(".", 1)[0]):
            return bed
    return beds[int(world.get("id") or ep.get("world_id") or 0) % len(beds)]


def bed_gain_db(item: dict, voice_db: float, under_db: float) -> float:
    """Поправка дорожки (gain_db), чтобы петля звучала на ``under_db`` тише голоса (громкость файла — lufs пака)."""
    from app.motion import TRACK_MAX_GAIN_DB, TRACK_MIN_GAIN_DB

    lufs = float(item.get("lufs") or -26.0)
    return round(max(TRACK_MIN_GAIN_DB, min(TRACK_MAX_GAIN_DB, voice_db - under_db - lufs)), 2)


def loop_layers(ref: str, start: float, end: float, file_sec: float, props: dict, lid: str, name: str,
                fade_in: float = 0.0, fade_out: float = 0.0) -> list[dict]:
    """Звук-петля на [start, end]: слой audio короче файла — один; длиннее — копии файла внахлёст
    (``LOOP_XFADE``, перекрёстное затухание) — рендер сам аудио-слой не зацикливает."""
    out: list[dict] = []
    file_sec = max(file_sec, LOOP_XFADE * 4)
    t, n = float(start), 0
    while t < end - 0.05:
        seg_end = min(end, t + file_sec)
        last = seg_end >= end - 1e-6
        p = {"asset_id": ref, "mode": "track", **props,
             "fadeIn": round(fade_in if n == 0 else LOOP_XFADE, 3),
             "fadeOut": round(fade_out if last else LOOP_XFADE, 3)}
        out.append({"id": f"{lid}{n:02d}" if n else lid, "type": "audio", "name": name[:60],
                    "start": round(t, 3), "end": round(seg_end, 3), "props": p})
        if last:
            break
        t, n = seg_end - LOOP_XFADE, n + 1
    return out


def render3d(result: dict, out_path: Path) -> dict:
    """Сцена → mp4 (тесты подменяют: настоящий рендер — минуты)."""
    return S3.render_episode(result, out_path)


def hero_card(store: Any, ep: dict) -> str:
    """Карточка главного героя: первый актёр в кадрах сценария, иначе первый актёр мира."""
    for sentence in (ep.get("script") or {}).get("sentences") or []:
        for shot in sentence.get("shots") or []:
            for cid in shot.get("cast") or []:
                try:
                    return str(S.get_character(store, int(cid)).get("identity_card") or "")
                except KeyError:
                    continue
    chars = S.list_characters(store, int(ep["world_id"]))
    return str(chars[0].get("identity_card") or "") if chars else ""


def _captions(world: dict) -> dict:
    from app.stories_gen import _captions_props

    props = _captions_props(world)
    props["maxWords"] = CAPTION_WORDS
    if str(props.get("activeColor") or "").lower() == str(props.get("color") or "").lower():
        props["activeColor"] = ACTIVE_COLOR
    return props


def _frames(video: Path, times: list[float], out_dir: Path, stem: str) -> list[str]:
    """Кадры видео (JPEG) на заданных секундах — для обложек."""
    out = []
    for n, t in enumerate(times):
        path = out_dir / f"{stem}-cover{n}.jpg"
        proc = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0.0, t):.3f}", "-i", str(video),
                               "-frames:v", "1", "-q:v", "3", str(path)], capture_output=True, text=True, check=False)
        if proc.returncode == 0 and path.is_file():
            out.append(str(path))
    return out


def cover_times(shots: list[dict]) -> list[float]:
    """Секунды для обложек: герой за спиной в начале истории, та самая ошибка, жуткая деталь в финале."""
    picks: list[float] = []

    def mid(s: dict) -> float:
        return round(s["t0"] + (s["t1"] - s["t0"]) * 0.6, 3)

    for want in (lambda s: s["camera"] == "ots" and s["role"] not in ("hook", ""),
                 lambda s: s["role"] == "mistake", lambda s: s["role"] == "finale" and s["camera"] == "detail",
                 lambda s: s["camera"] == "face"):
        hit = next((s for s in shots if want(s) and mid(s) not in picks), None)
        if hit is not None:
            picks.append(mid(hit))
        if len(picks) >= COVER_FRAMES:
            break
    return picks or [mid(shots[len(shots) // 2])]


def sfx_layers(store: Any, result: dict, placed: list[dict], total: float, prefix: str,
               voice_db: float = VOICE_LUFS_DEFAULT) -> list[dict]:
    """Звуки: события режиссуры (блэкаут, рывок, стук…) и звук кадра из сценария — настоящие записи, без дублей.
    Звук с ``end`` — фон-петля (гул кабины, шахта): до ``end``, громкость — ``under_db`` ниже голоса (``voice_db``)."""
    from app.stories_gen import SFX_OFFSET_SEC, SFX_VOLUME, _sfx_duration

    items = [dict(x) for x in result.get("sfx") or []]
    for p in placed:
        sfx = p.get("sfx") or {}
        if sfx.get("ref"):
            t = round(min(p["end"] - 0.2, p["start"] + SFX_OFFSET_SEC), 3)
            if not any(abs(x["t"] - t) < 0.6 and not x.get("end") for x in items):
                items.append({"t": t, "ref": str(sfx["ref"]), "volume": SFX_VOLUME, "label": sfx.get("label") or ""})
    layers = []
    for n, x in enumerate(sorted(items, key=lambda i: i["t"])):
        start = max(0.0, float(x["t"]))
        if start >= total - 0.1:
            continue
        name = f"Звук: {x.get('label') or ''}"
        if x.get("end"):
            info = _pack_item(str(x["ref"]))
            end = min(total, float(x["end"]))
            if info is None or end - start < 0.3:
                continue
            props = {"volume": float(x.get("volume") or 1.0), "role": str(x.get("role") or "ambience")}
            if x.get("under_db") is not None:
                props["gain_db"] = bed_gain_db(info, voice_db, float(x["under_db"]))
            layers += loop_layers(str(x["ref"]), start, end, float(info.get("duration") or 0), props,
                                  f"{prefix}fx{n:02d}", name, float(x.get("fade_in") or 0.3),
                                  float(x.get("fade_out") or 0.3))
            continue
        dur = min(SFX_MAX_SEC, max(0.3, _sfx_duration(store, str(x["ref"]))))
        layers.append({"id": f"{prefix}fx{n:02d}", "type": "audio", "name": name[:60],
                       "start": round(start, 3), "end": round(min(total, start + dur), 3),
                       "props": {"asset_id": str(x["ref"]), "mode": "track", "volume": float(x.get("volume") or 0.6),
                                 "fadeOut": 0.15, "role": "sfx"}})
    return layers


def music_bed_layers(world: dict, ep: dict, total: float, voice_db: float, prefix: str) -> tuple[list[dict], dict | None]:
    """Тревожный фон на всю серию тихо под голосом: слои audio (role music) и что взято; пака нет — ([], None)."""
    bed = pick_bed(world, ep)
    if bed is None:
        return [], None
    gain = bed_gain_db(bed, voice_db, BED_UNDER_VOICE_DB)
    layers = loop_layers(bed["ref"], 0.0, total, float(bed.get("duration") or 0),
                         {"volume": 1.0, "gain_db": gain, "role": "music"}, f"{prefix}bed",
                         f"Музыка: {bed.get('name') or bed['file']}", BED_FADE_IN, BED_FADE_OUT)
    return layers, {"ref": bed["ref"], "name": bed.get("name"), "gain_db": gain, "voice_lufs": round(voice_db, 1),
                    "under_voice_db": BED_UNDER_VOICE_DB, "copies": len(layers)}


# Слышимая подложка 3D-серии (владелец: «музыки не хватает»): между фразами фон на BED_OPEN_DB тише голоса, под речью
# — мягкий ducking на BED_DUCK_DB (не до тишины), к финалу нарастание на BED_SWELL_DB. Итог по замеру: музыка
# ≈ 6–8 дБ тише голоса (голос −14 LUFS → фон ≈ −20…−22). Огибающая одна, фон — один готовый файл (петля внахлёст).
BED_OPEN_DB = 3.0
BED_DUCK_DB = 5.0
BED_SWELL_DB = 3.0
BED_SWELL_FROM = 0.7          # доля речи, с которой фон нарастает к панчлайну
BED_ATTACK, BED_RELEASE = 0.25, 0.7
BED_UNDER_TARGET_DB = 7.0     # итог по замеру: фон на 6–8 дБ тише голоса (голос −14 LUFS → фон ≈ −21)


def speech_spans(words: list[dict], merge: float = 0.35) -> list[tuple[float, float]]:
    spans: list[list[float]] = []
    for w in words:
        a_, b_ = float(w["start"]), float(w["end"])
        if spans and a_ - spans[-1][1] < merge:
            spans[-1][1] = max(spans[-1][1], b_)
        else:
            spans.append([a_, b_])
    return [(a_, b_) for a_, b_ in spans]


def bed_envelope_db(t: float, spans: list[tuple[float, float]], speech_end: float, total: float) -> float:
    """Поправка фона (дБ) в момент ``t``: под речью −BED_DUCK_DB (атака/отпускание), к финалу +BED_SWELL_DB,
    вход/выход — плавно."""
    duck = 0.0
    for a_, b_ in spans:
        if a_ - BED_ATTACK <= t <= b_ + BED_RELEASE:
            if t < a_:
                k = (t - (a_ - BED_ATTACK)) / BED_ATTACK
            elif t > b_:
                k = 1.0 - (t - b_) / BED_RELEASE
            else:
                k = 1.0
            duck = max(duck, k * k * (3 - 2 * k))
    db = -BED_DUCK_DB * duck
    s0 = speech_end * BED_SWELL_FROM
    if t > s0:
        x = min(1.0, (t - s0) / max(0.5, speech_end - s0))
        db += BED_SWELL_DB * x * x * (3 - 2 * x)
    if t < BED_FADE_IN:
        db += 20.0 * math.log10(max(1e-3, t / BED_FADE_IN))
    if t > total - BED_FADE_OUT:
        db += 20.0 * math.log10(max(1e-3, (total - t) / BED_FADE_OUT))
    return db


def render_bed(store: Any, bed: dict, words: list[dict], total: float, voice_db: float, speech_end: float,
               label: str, project_id: int | None = None) -> tuple[dict, dict] | None:
    """Фон одним файлом: петля трека пака на всю серию, громкость от замера голоса (между фразами — на
    BED_OPEN_DB тише), огибающая ducking/нарастания. → (ассет «Файлы для монтажа», замер)."""
    from app.audio_mix import ebur128
    from app.montage_assets import assets_dir, probe_asset
    from app.motion_assets import resolve_pack_ref

    src = resolve_pack_ref(bed["ref"])
    if src is None or not Path(src).is_file():
        return None
    lufs = float(bed.get("lufs") or -26.0)
    gain = (voice_db - BED_OPEN_DB) - lufs
    spans = speech_spans(words)
    meas: dict = {}
    rate = 100
    env = [10 ** (bed_envelope_db(i / rate, spans, speech_end, total) / 20.0) for i in range(int(total * rate) + 2)]
    work = settings.tmp_dir / f"bed-{uuid4().hex[:8]}"
    work.mkdir(parents=True, exist_ok=True)
    try:
        import array

        pcm = array.array("h", [int(max(0.0, min(1.0, v)) * 32767) for v in env])
        raw = work / "env.raw"
        raw.write_bytes(pcm.tobytes())
        dest = assets_dir() / f"series3d-bed-{uuid4().hex[:10]}.m4a"
        for _ in range(3):
            # громкость файла пака в items.json — оценка; доводим по замеру итогового фона до BED_UNDER_TARGET_DB
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-stream_loop", "-1", "-i", str(src),
                            "-f", "s16le", "-ar", str(rate), "-ac", "1", "-i", str(raw), "-filter_complex",
                            f"[0:a]aformat=sample_rates=48000:channel_layouts=stereo,atrim=0:{total:.3f},asetpts=N/SR/TB,"
                            f"volume={gain:.2f}dB[m];[1:a]aresample=48000,aformat=channel_layouts=stereo[e];"
                            f"[m][e]amultiply,atrim=0:{total:.3f}[out]",
                            "-map", "[out]", "-c:a", "aac", "-b:a", "192k", str(dest)], check=True, capture_output=True)
            meas = ebur128(dest)
            got = float(meas.get("i") or -70)
            corr = (voice_db - BED_UNDER_TARGET_DB) - got
            if got < -69 or abs(corr) < 0.5:
                break
            gain += max(-12.0, min(12.0, corr))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    meta = probe_asset(dest)
    asset = store.create_montage_asset(kind="audio", label=f"Фон: {label}"[:120], description="тревожный фон 3D-серии",
                                       tags="3d,фон", file_path=str(dest), original_filename=dest.name,
                                       mime_type="audio/mp4", size_bytes=dest.stat().st_size,
                                       project_id=project_id, **meta)
    return asset, {"bed_lufs": round(float(meas.get("i") or -70), 1), "voice_lufs": round(voice_db, 1),
                   "under_voice_db": round(voice_db - float(meas.get("i") or -70), 1), "gain_db": round(gain, 1),
                   "duck_db": -BED_DUCK_DB, "swell_db": BED_SWELL_DB, "open_db": -BED_OPEN_DB}


def save_video(store: Any, path: Path, label: str, project_id: int | None = None) -> dict:
    """mp4 3D-серии → «Файлы для монтажа» (видео для слоя холста)."""
    from app.montage_assets import assets_dir, probe_asset

    dest = assets_dir() / f"series3d-{uuid4().hex[:12]}.mp4"
    shutil.move(str(path), dest)
    meta = probe_asset(dest)
    return store.create_montage_asset(kind="video", label=label[:120], description="3D-серия (свой движок)",
                                      tags="3d,серия", file_path=str(dest), original_filename=dest.name,
                                      mime_type="video/mp4", size_bytes=dest.stat().st_size,
                                      project_id=project_id, **meta)


def assemble(store: Any, episode_id: int) -> dict:
    """Озвучка → слова → режиссура → 3D-рендер → холст-клип с субтитрами, HUD, титрами и звуками."""
    from app import agent_tools as T
    from app import stories_gen as SG
    from app.aivideo_gen import _asset_path
    from app.canvas import MIN_DURATION_SEC, base_seconds, create_canvas_clip
    from app.clip_history import snapshot
    from app.dubbing import probe_duration
    from app.explainer_gen import auto_mix_clip, proportional_words
    from app.motion import normalize_camera, normalize_layers

    ep = S.get_episode(store, episode_id)
    world = S.get_world(store, int(ep["world_id"]))
    ref = S.narration_ref(store, ep)
    if ref is None:
        raise S.StoryError("нет озвучки эпизода — этап генерации")
    path = _asset_path(store, ref)
    audio_sec = float(probe_duration(path))
    notes: list[str] = [n for n in (ep.get("notes") or []) if not str(n).startswith("3D:")]
    blender = S.episode_renderer(ep) == S.RENDERER_BLENDER
    key = blender_key(ep, ref) if blender else ""
    job = _blender_job(store, ep, key) if blender else None
    # Слова уже распознаны для задания рендера — не гоняем Whisper на каждом проходе ожидания.
    words: list[dict] = [dict(w) for w in ((job or {}).get("payload") or {}).get("words") or []]
    if words:
        pass
    elif S.world_voice(store, world)["provider"] != "none":
        try:
            words = SG.transcribe_words(path)
        except Exception as exc:  # noqa: BLE001 - Whisper выключен/не встал: по длине текста
            notes.append(f"Whisper не сработал ({type(exc).__name__}: {str(exc)[:120]}) — слова по длине текста")
    if not words:
        words = proportional_words(S.narration_text(ep), audio_sec)
    else:
        # Субтитры — слова сценария со временем распознанных (Whisper путает слова и цифры).
        words = S3.align_words(S.narration_text(ep), words)
    disaster = D.is_disaster(world, ep)
    speech_end = round(float(words[-1]["end"]), 3)
    tail = D.PART_SEC if disaster else SG.TAIL_SEC
    total = round(max(speech_end + tail, audio_sec + (0.0 if disaster else 0.4), MIN_DURATION_SEC), 2)
    if total > base_seconds():
        raise S.StoryError(f"серия {total:.1f} с длиннее холста {base_seconds():.0f} с")
    placed = [{**p, "scene_id": 0} for p in SG.place(ep["beats"], words, total)]
    look = S3.hero_look(hero_card(store, ep))
    options = ep.get("options") or {}
    if blender:
        got = blender_video(store, ep, words, total, look, path, key, job, notes)
        if got is None:
            return {"ok": True, "wait": True, "episode_id": int(episode_id), "engine": S.ENGINE_3D}
        result, video_src, info = got
        problems: list[str] = []
    else:
        result = S3.direct(ep["script"], words, total=total, look=look, location=options.get("location"))
        problems = S3.check_shots(result)
        video_src = None
    if problems:
        notes.append("3D: проверка кадров — " + "; ".join(problems[:6]))
    work = settings.tmp_dir / f"series3d-{episode_id}-{uuid4().hex[:8]}"
    work.mkdir(parents=True, exist_ok=True)
    try:
        video = work / "series.mp4"
        if video_src is not None:
            shutil.copyfile(video_src, video)
        else:
            info = render3d(result, video)
        covers = _frames(video, cover_times(result["shots"]), _covers_dir(), f"ep{int(episode_id)}-{uuid4().hex[:6]}")
        asset = save_video(store, video, f"3D: {ep.get('title') or ep['premise']}", int(ep["project_id"]))
    finally:
        shutil.rmtree(work, ignore_errors=True)

    prefix = SG.LAYER_PREFIX
    layers: list[dict] = [{"id": f"{prefix}3d", "type": "video", "name": "3D-серия", "start": 0.0, "end": round(total, 3),
                           "track": 1, "x": 0.5, "y": 0.5, "z": 0,
                           "props": {"asset_id": int(asset["id"]), "w": 1080, "content": True, "from": 0, "speed": 1,
                                     "loop": False, "freeze": True}}]
    voice_db = voice_lufs(path)
    layers += sfx_layers(store, result, placed, total, prefix, voice_db)
    # Фон: трек библиотеки (music_track_id / auto) — музыкой клипа ниже; иначе тревожный фон пака; none — тишина.
    track = SG._music_track(store, world, ep)
    bed = None
    if track is None and music_mode(world, ep) != "none":
        bed_layers, bed = music_bed_layers(world, ep, total, voice_db, prefix)
        picked = pick_bed(world, ep)
        made = None
        if picked is not None:
            try:
                made = render_bed(store, picked, words, total, voice_db, speech_end, picked.get("name") or picked["file"],
                                  int(ep["project_id"]))
            except Exception as exc:  # noqa: BLE001 - не собрался файл фона — петля слоями, как раньше
                notes.append(f"3D: фон одним файлом не собрался ({type(exc).__name__}) — петля слоями")
        if made is not None:
            asset_bed, meas = made
            bed_layers = [{"id": f"{prefix}bed", "type": "audio", "name": f"Музыка: {picked.get('name') or picked['file']}"[:60],
                           "start": 0.0, "end": round(total, 3),
                           "props": {"asset_id": str(asset_bed["id"]), "mode": "track", "volume": 1.0, "role": "music"}}]
            bed = {**(bed or {}), "ref": picked["ref"], "asset_id": int(asset_bed["id"]), **meas, "copies": 1}
        layers += bed_layers
        if bed is None:
            notes.append(f"3D: тревожного фона нет — пак {BED_PACK} не скачан (tools/fetch_packs.py --only {BED_PACK})")
    if disaster:
        stroke = world["captions"]["stroke"]
        if options.get("title_card", True):
            layers += D.title_layers(ep, placed, stroke, prefix)
        layers += D.hud_layers(ep, placed, speech_end, float(world["captions"]["y"]), {}, prefix)
        if total - speech_end >= 0.5:
            layers.append(D.part_layer(ep, speech_end, total, stroke, prefix))
        if result.get("faces"):
            notes += place_titles(layers, result["faces"], float(world["captions"]["y"]), prefix)
    layers.append({"id": f"{prefix}narr", "type": "audio", "name": "Рассказчик", "start": 0.0, "end": round(total, 3),
                   "props": {"asset_id": str(ref["asset_id"]), "mode": "track", "volume": 1.0, "role": "voice"}})
    layers.append({"id": f"{prefix}caps", "type": "captions", "name": "Субтитры по 1–3 слова", "start": 0.0,
                   "end": round(speech_end, 3), "track": 6, "x": 0.5, "y": float(world["captions"]["y"]),
                   "props": _captions(world)})
    clip = create_canvas_clip(store, total, {"kind": "solid", "colors": ["#06070a", "#06070a"]},
                              (ep.get("title") or ep["premise"])[:100],
                              f"3D-серия ({world['name']}): {ep['premise']}"[:500])
    clip_id = int(clip["id"])
    render = dict(store.get_clip_plan(clip_id).get("render_settings") or {})
    render["layers"] = normalize_layers(layers)
    render["camera"] = normalize_camera([])
    store.set_clip_plan_render_settings(clip_id, render)
    ctx = T.ToolContext(store=store, clip_plan_id=clip_id,
                        on_first_change=lambda: snapshot(store, clip_id, "3D-серия: сборка"))
    SG._tool(ctx, "set_subtitle_words", {"words": words})
    if track:
        SG._tool(ctx, "set_music", {"on": True, "track_id": track})
        from app.explainer_gen import _speech_spans

        SG._tool(ctx, "set_music_duck", {"duck": _speech_spans(words)})
    elif bed is None and D.music_off(world, ep) and D.MUSIC_NOTE not in notes:
        notes.append(D.MUSIC_NOTE)
    mixed = auto_mix_clip(ctx, notes)
    fresh = store.get_clip_plan(clip_id)
    shots = result["shots"]
    timeline = {"duration": total, "clip_id": clip_id, "audio_sec": round(audio_sec, 2), "engine": S.ENGINE_3D,
                "editor_url": f"/projects/{fresh['source_id']}/candidates?clip={clip_id}",
                "beats": [{k: p.get(k) for k in ("i", "start", "end", "text", "camera", "sfx", "sentence")} for p in placed],
                "shots_per_min": round(len(shots) / total * 60, 1), "sfx": len(result.get("sfx") or []),
                "layers": len(render["layers"]), "music_track_id": track, "music_bed": bed, "cover_frames": covers,
                "scene3d": {"location": result["location"], "asset_id": int(asset["id"]),
                            "renderer": S.episode_renderer(ep), "director": result.get("director"),
                            "render_sec": info.get("seconds"), "frames": info.get("frames"),
                            "shots": [{k: s.get(k) for k in ("n", "t0", "t1", "text", "role", "action", "camera", "spot",
                                                             "emotion", "speaks") if k in s} for s in shots],
                            "events": [{k: e.get(k) for k in ("kind", "t", "value")} for e in result["events"]],
                            "checks": problems, "look": result.get("look")},
                **({"mix": mixed} if mixed else {})}
    if disaster:
        hud = [lay for lay in render["layers"] if str(lay.get("id") or "").startswith(f"{prefix}hud")]
        timeline.update({"format": S.FORMAT_DISASTER, "part": D.part_of(ep), "speech_end": speech_end,
                         "part_card": D.part_card_text(ep),
                         "hud": [{"text": h["props"]["text"], "color": h["props"]["color"], "start": h["start"],
                                  "end": h["end"], "x": h["x"], "y": h["y"]} for h in hud],
                         "publish": D.publish_meta(ep)})
    notes.append(f"3D: {result['location']}, кадров {len(shots)}, рендер {info.get('seconds')} с"
                 + (f" (Blender, режиссура: {result.get('director')})" if blender else ""))
    S.set_status(store, episode_id, "done", ("assembling",), clip_id=clip_id, words=words, timeline=timeline,
                 notes=notes, error="")
    out = {"ok": True, "clip_id": clip_id, "duration": total, "beats": len(placed), "shots": len(shots),
           "layers": len(render["layers"]), "editor_url": timeline["editor_url"], "notes": notes, "engine": S.ENGINE_3D}
    if options.get("render"):
        out["publish"] = SG.finish_publish(store, episode_id, clip_id, cover=options.get("cover", True))
    return out


TITLE_SLOTS = (0.22, 0.27, 0.56)     # название серии (над субтитрами; HUD первой фразы — после названия)
PART_SLOTS = (0.70, 0.62, 0.22)      # «ЧАСТЬ N+1» после речи — субтитров уже нет


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def layer_box(layer: dict) -> tuple[float, float]:
    """Вертикальные границы текстового слоя (доли кадра): строки × кегль × интерлиньяж + подложка."""
    props = layer.get("props") or {}
    size = float(props.get("size") or 80)
    width = float(props.get("maxWidth") or 940)
    lines = max(1, -(-len(str(props.get("text") or "")) * size * 0.5 // width))
    h = (lines * size * float(props.get("lineHeight") or 1.05) + 2 * float((props.get("bg") or {}).get("padY") or 10)) / 1920
    return float(layer["y"]) - h / 2, float(layer["y"]) + h / 2


def face_in(faces: list[dict], t0: float, t1: float) -> list[tuple[float, float]]:
    """Полосы лица (верх, низ) в кадрах, идущих в [t0, t1)."""
    out = []
    for f in faces:
        if f.get("y") is None or f["t1"] <= t0 or f["t0"] >= t1:
            continue
        out.append((f["y"] - f["h"] / 2, f["y"] + f["h"] / 2))
    return out


def place_titles(layers: list[dict], faces: list[dict], captions_y: float, prefix: str) -> list[str]:
    """Название серии и «ЧАСТЬ N+1» — в свободную полосу кадра: не на лице (проекция головы из сцены) и не на
    субтитрах. Возвращает заметки, если места без перекрытия нет."""
    notes: list[str] = []
    by_id = {str(lay.get("id")): lay for lay in layers}
    title, part_t, part = by_id.get(f"{prefix}title"), by_id.get(f"{prefix}title_part"), by_id.get(f"{prefix}part")
    caps = (captions_y - 0.05, captions_y + 0.05)

    def best(lay: dict, slots: tuple, avoid_caps: bool, extra: float = 0.0) -> float:
        bands = face_in(faces, float(lay["start"]), float(lay["end"]))
        h0, h1 = layer_box(lay)
        half = (h1 - h0) / 2
        scored = []
        for y in slots:
            a, b = y - half, y + half + extra
            cost = sum(_overlap(a, b, f0 - 0.02, f1 + 0.02) for f0, f1 in bands)
            if avoid_caps:
                cost += _overlap(a, b, *caps)
            scored.append((cost, slots.index(y), y))
        cost, _, y = min(scored)
        if cost > 1e-6:
            notes.append(f"3D: титр «{str((lay.get('props') or {}).get('text') or '')[:30]}» задевает лицо — места нет")
        return y

    if title is not None:
        dy = float(part_t["y"]) - float(title["y"]) if part_t is not None else 0.0
        extra = (layer_box(part_t)[1] - layer_box(title)[1]) if part_t is not None else 0.0
        y = best(title, TITLE_SLOTS, True, max(0.0, extra))
        title["y"] = round(y, 3)
        if part_t is not None:
            part_t["y"] = round(y + dy, 3)
    if part is not None:
        part["y"] = round(best(part, PART_SLOTS, False), 3)
    if title is not None:
        # HUD появляется, когда название уходит: в первую секунду читается только название (плашка не спорит с ним)
        for lay in layers:
            if not str(lay.get("id") or "").startswith(f"{prefix}hud") or float(lay["start"]) >= float(title["end"]):
                continue
            lay["start"] = round(float(title["end"]) + 0.05, 3)
            if float(lay["end"]) - float(lay["start"]) < 1.0:
                lay["end"] = round(float(lay["start"]) + 1.2, 3)
    return notes


def blender_quality(ep: dict) -> str:
    from app import series3d_blender as SB

    q = str((ep.get("options") or {}).get("quality") or SB.DEFAULT_QUALITY)
    return q if q in SB.QUALITY else SB.DEFAULT_QUALITY


def blender_key(ep: dict, ref: dict, with_quality: bool = True) -> str:
    """Что рендерим: фразы сценария + озвучка + версия сцены (+ пресет) — то же задание не ставим дважды.
    Без пресета — ключ режиссуры: другой пресет той же серии берёт ту же режиссуру (сравнимо, без нового Gemini)."""
    from app import series3d_blender as SB

    opts = ep.get("options") or {}
    blob = {"sentences": [s.get("text") for s in (ep.get("script") or {}).get("sentences") or []],
            "narration": str(ref.get("asset_id")), "lipsync": opts.get("lipsync") or "speaking",
            "v": SB.SCENE_VERSION, "episode": int(ep["id"])}
    base = "s3b-" + hashlib.sha1(json.dumps(blob, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:20]
    return f"{base}-{blender_quality(ep)}" if with_quality else base


def blender_key_base(key: str) -> str:
    """Ключ режиссуры из ключа задания (без пресета)."""
    return key.rsplit("-", 1)[0] if key.count("-") >= 2 else key


def _blender_job(store: Any, ep: dict, key: str) -> dict | None:
    """Задание рендера этой серии; упавшее, о котором эпизод уже сообщил (повтор после «Продолжить»), — не берём."""
    from app import render_queue as RQ

    job = RQ.find(store, key, payload=True)
    reported = ((ep.get("timeline") or {}).get("render3d") or {}).get("failed_job")
    if job and job["status"] == "failed" and reported == job["id"]:
        return None
    if job and job["status"] == "done" and RQ.result_file(job) is None:
        return None
    return job


def blender_local() -> bool:
    """Рендерить здесь же (CLI/хост), а не через очередь: ``SERIES3D_BLENDER_LOCAL=1`` и Blender найден."""
    from app import series3d_blender as SB

    flag = os.getenv("SERIES3D_BLENDER_LOCAL", "").strip().lower() in ("1", "true", "yes", "on")
    return flag and SB.find_blender() is not None


def blender_video(store: Any, ep: dict, words: list[dict], total: float, look: dict, audio: Path, key: str,
                  job: dict | None, notes: list[str]) -> tuple[dict, Path, dict] | None:
    """Режиссура Blender + рендер: ``(result, mp4, info)`` готово; ``None`` — ждём хост-воркер (статус в эпизоде)."""
    from app import render_queue as RQ
    from app import series3d_blender as SB

    eid = int(ep["id"])
    opts = ep.get("options") or {}
    payload = (job or {}).get("payload")
    local = blender_local()
    work = settings.tmp_dir / f"series3d-blender-{eid}"
    saved = work / "job.json"
    if payload is None and local and saved.is_file():
        try:   # повтор локального рендера (упал/перезапуск) — та же режиссура, кадры из кэша
            old = json.loads(saved.read_text("utf-8"))
            payload = old if old.get("key") in (key, blender_key_base(key)) else None
            if payload is not None:   # правила монтажа могли поменяться — пересобрать план (кадры без правок — из кэша)
                payload["shots"] = SB.fix_sequence(payload["shots"])
        except (OSError, ValueError):
            payload = None
    if payload is None:
        payload = SB.build_job(ep["script"], words, total, gemini=True, envelope=SB.audio_envelope(audio),
                               lipsync_mode=str(opts.get("lipsync") or "speaking"),
                               quality=blender_quality(ep), look=look, seed=eid)
        payload["key"] = blender_key_base(key)
        if payload.get("director_error"):
            notes.append(f"3D: Gemini не выбрал действия ({payload['director_error'][:120]}) — правила")
    payload["quality"] = blender_quality(ep)
    if local:
        cache = settings.data_dir / "render3d_cache"
        out = work / "series.mp4"
        info = SB.render_local(payload, out, work=work, cache=cache)
        return SB.result_for(payload), out, {"seconds": info.get("seconds"), "frames": info.get("frames"),
                                             "cached": info.get("cached")}
    if job is None:
        job = RQ.enqueue(store, payload, key=key, episode_id=eid, title=str(ep.get("title") or ep.get("premise") or ""))
    if job["status"] == "done":
        res = job.get("result") or {}
        return SB.result_for(payload), RQ.result_file(job), {"seconds": res.get("seconds"), "frames": res.get("frames"),
                                                            "cached": res.get("cached"), "job_id": job["id"]}
    timeline = dict(ep.get("timeline") or {})
    if job["status"] in ("failed", "cancelled"):
        timeline["render3d"] = {"failed_job": job["id"], "status": job["status"]}
        S.update_episode(store, eid, timeline=timeline)
        raise S.StoryError(f"рендер 3D на компьютере: {job.get('error') or job['status']} — «Продолжить» поставит заново")
    label, note = RQ.wait_label(store, job)
    timeline["render3d"] = {"job_id": job["id"], "status": job["status"], "progress": job["progress"], "label": label,
                            "note": note, "worker": job.get("worker") or ""}
    wait_notes = notes + ([f"3D: {note}"] if note else [])
    S.update_episode(store, eid, timeline=timeline, notes=wait_notes)
    return None


def _covers_dir() -> Path:
    path = settings.clip_dir / "covers" / "series3d"
    path.mkdir(parents=True, exist_ok=True)
    return path
