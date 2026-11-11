"""Моушн-проход рендера: команды для Node-компоновщика и ffmpeg (docs/motion/spec.md).

Здесь только «чистые» сборщики команд и поиск файлов слоёв; сам порядок проходов
живёт в ``ClipRenderService`` (app/render.py). Так команды легко проверять в
тестах без ffmpeg и Node, а рендер остаётся одним местом, где решается порядок.

Порядок в рендере клипа с моушном:

1. основа: куски, кроп/рефрейм, лук, переходы — **без баннера**;
2. компоновщик: камера двигает только основу, поверх рисуются слои;
3. один дешёвый ffmpeg-проход: звук основы обратно (видео копией) и баннер
   поверх слоёв (если баннер есть — с перекодированием);
4. субтитры и музыка (``_finalize_render``) — поверх камеры и слоёв, не зумятся;
5. обложка, вставки «Файлов для монтажа»;
6. звуки ``audio``-слоёв — последним дешёвым проходом (видео копией): чтобы
   распознавание субтитров слышало чистую речь, как и с музыкой.

Где что лежит. Здесь — то, что тесты подменяют, и его читатели: уровни звука, чат, ресурсы паков, кадр ``render_frame_png``, листы кадров.
Вынесено: сборка компоновщика ``app/motion_render_comp.py``, видео-слои ``app/motion_render_video.py``, звук слоёв
``app/motion_render_audio.py``, субтитры и вставки поверх кадра ``app/motion_render_frame.py``. Те модули этот не импортируют, а он
их — поэтому все имена по-прежнему доступны отсюда.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from app.motion import EFFECT_MAX_VOLUME, asset_key, has_captions, has_chat, layer_asset_refs, react_sources

from app.motion_render_comp import (  # noqa: F401
    DEFAULT_CRF, plan_is_speechless, CompositorMissing, compositor_command, build_compositor_args, FINAL_HOLD_EPS,
    hold_final_layers, write_comp, plan_caption_words, _with_note, MAX_CHAT_MESSAGES, MAX_CHAT_IMAGES, chat_windows,
    plan_chat_messages, chat_image_urls,
)
from app.motion_render_video import (  # noqa: F401
    VIDEO_FRAME_PATTERN, VIDEO_MAX_FRAME_WIDTH, VIDEO_MIN_FRAME_WIDTH, VIDEO_MAX_FRAME_PIXELS,
    VIDEO_MAX_TOTAL_PIXELS, VIDEO_WIDTH_MARGIN, VIDEO_MAX_FRAMES, VIDEO_FRAMES_TIMEOUT, _key_values, unit_width,
    reframe_for_output, video_max_frame_width, video_frame_width, video_frame_plan, build_video_frames_args,
    _probe_video, prepare_video_frames, build_motion_mux_args,
)
from app.motion_render_audio import (  # noqa: F401
    TRACK_EDGE_FADE, _probe_sound, video_sound_item, _layer_fx, audio_item_inputs, audio_item_chain,
)
from app.motion_render_frame import (  # noqa: F401
    _focus_x_at, frame_preset, frame_subtitles_ass, build_frame_subtitle_args, _overlay_subtitles, _overlay_inserts,
    tile_frames_jpeg, _drawtext_escape, MAX_SOURCE_SHEET, MIN_SOURCE_SHEET_STEP, source_sheet_times,
    source_time_label,
)

# Насколько приглушать исходник под звуком слоя с ``duck`` (как у вставок).
AUDIO_DUCK_VOLUME = 0.3


# Громкость звуков слоёв — системно, а не советом в промпте. Владелец: «эффекты
# очень громкие (громкость — системная ошибка)». Каждый файл сначала приводится
# к одному уровню («громкая часть» −20 дБ, речь клипа ~ −14 LUFS — эффект на 6 дБ
# тише), потом умножается на volume слоя, а volume ограничен сверху. Что бы ни
# поставил агент, эффект не перекроет голос.
EFFECT_TARGET_DB = -20.0
EFFECT_PEAK_DB = -3.0
# Эффект — на столько тише голоса ЭТОГО клипа (по громким местам). Раньше цель
# была абсолютной (−20 дБ): у тихой записи голоса (−32 LUFS) эффект выходил
# громче речи, а финальная поправка громкости поднимала их вместе — «дешёвый
# звон во всю громкость».
EFFECT_UNDER_SPEECH_DB = 10.0
# Клип без речи (игра, музыка): «голос» по громким местам — это выстрелы и взрывы,
# и эффект на 10 дБ ниже них тонул (агент: «панчлайн-звук −8 дБ от среднего»).
# Там цель — чуть ниже громких мест клипа, но не громче их.
EFFECT_UNDER_LOUD_DB = 4.0
# Потолок volume эффекта — тот же, что в схеме: схема и рендер согласны (раньше схема
# пускала 2, а рендер молча резал до 1).
MAX_EFFECT_VOLUME = EFFECT_MAX_VOLUME
_LEVEL_CACHE: dict[tuple[str, float, float | None], tuple[float, float] | None] = {}


def audio_levels(path: str | Path, seconds: float | None = 30.0, start: float = 0.0) -> tuple[float, float] | None:
    """(RMS peak, peak) звука файла в дБ — громкие места и пик (кэш по файлу).
    ``start`` — с какой секунды файла слушать (музыка со смещением старта)."""
    import re
    import subprocess

    try:
        key = (str(path), Path(path).stat().st_mtime, seconds, start)
    except OSError:
        return None
    if key in _LEVEL_CACHE:
        return _LEVEL_CACHE[key]
    levels = None
    try:
        limit = ["-t", f"{seconds:g}"] if seconds else []
        seek = ["-ss", f"{start:g}"] if start > 0 else []
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", *seek, "-i", str(path), "-vn", *limit,
             "-af", "astats=metadata=0:reset=0", "-f", "null", "-"],
            capture_output=True, text=True, timeout=120,
        )
        overall = proc.stderr[proc.stderr.rfind("Overall"):]
        rms = re.search(r"RMS peak dB:\s*(-?[\d.]+)", overall)
        peak = re.search(r"Peak level dB:\s*(-?[\d.]+)", overall)
        if rms:
            levels = (float(rms.group(1)), float(peak.group(1)) if peak else 0.0)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        levels = None
    _LEVEL_CACHE[key] = levels
    return levels


def speech_level_db(clip_path: str | Path) -> float | None:
    """Громкие места звука клипа (голос) в дБ — от них считается громкость эффектов."""
    levels = audio_levels(clip_path, seconds=None)
    return levels[0] if levels and levels[0] > -70 else None


def effect_gain_db(path: str, speech_db: float | None = None, speechless: bool = False) -> float:
    """Поправка в дБ: громкая часть эффекта — на EFFECT_UNDER_SPEECH_DB тише голоса
    клипа (без звука у клипа — EFFECT_TARGET_DB), пик не выше EFFECT_PEAK_DB.

    ``speechless`` — в клипе нет речи: ``speech_db`` тогда громкие места игры/музыки,
    и эффект ставится на EFFECT_UNDER_LOUD_DB ниже них (не громче)."""
    levels = audio_levels(path)
    if not levels:
        return 0.0
    rms, peak = levels
    target = EFFECT_TARGET_DB
    if speech_db is not None and speechless:
        target = speech_db - EFFECT_UNDER_LOUD_DB
    elif speech_db is not None:
        target = min(target, speech_db - EFFECT_UNDER_SPEECH_DB)
    return max(-40.0, min(12.0, target - rms, EFFECT_PEAK_DB - peak))


def effect_volume(path: str, volume: Any, speech_db: float | None = None, speechless: bool = False) -> float:
    """Итоговый множитель громкости слоя-звука: выравнивание файла × volume (≤ MAX)."""
    try:
        user = float(volume if volume is not None else 1)
    except (TypeError, ValueError):
        user = 1.0
    user = max(0.0, min(MAX_EFFECT_VOLUME, user))
    return user * 10 ** (effect_gain_db(path, speech_db, speechless) / 20)


def attach_caption_words(store: Any, plan: dict, motion: dict | None) -> dict | None:
    """Добавить в моушн слова речи, если есть видимый слой субтитров.

    Слов нет (субтитры клипа ещё не сделаны) — слой ничего не покажет; это
    пометка для ошибки клипа (``host_notes``), а не сбой рендера.
    """
    if not motion or not has_captions(motion.get("layers")):
        return motion
    words = plan_caption_words(store, plan)
    if words:
        return {**motion, "words": words}
    return _with_note(motion, "у клипа нет сохранённых субтитров — слой «субтитры» пуст (сделайте субтитры в редакторе)")


CHAT_IMAGE_TIMEOUT = 5.0
CHAT_IMAGE_MAX_BYTES = 3 * 1024 * 1024


def _download(url: str) -> bytes | None:
    import httpx

    with httpx.Client(timeout=CHAT_IMAGE_TIMEOUT, follow_redirects=True) as client:
        response = client.get(url)
        if response.status_code != 200 or len(response.content) > CHAT_IMAGE_MAX_BYTES:
            return None
        return response.content


def fetch_chat_images(
    urls: list[str],
    cache_dir: Path,
    fetch: Callable[[str], bytes | None] | None = None,
) -> dict[str, str]:
    """Скачать эмоуты/значки в кэш (имя — sha1 адреса) → записи assets.json ``url:<адрес>``.

    Картинка, которую не скачать (таймаут, 404, CDN лёг), пропускается: эмоут
    нарисуется своим именем, значок — не нарисуется. Рендер из-за этого не падает.
    """
    fetch = fetch or _download
    cache_dir.mkdir(parents=True, exist_ok=True)

    def one(url: str) -> tuple[str, str | None]:
        path = cache_dir / (hashlib.sha1(url.encode("utf-8")).hexdigest() + ".img")
        if path.is_file() and path.stat().st_size > 0:
            return url, str(path)
        try:
            data = fetch(url)
        except Exception:  # noqa: BLE001 - сеть — не повод ронять рендер
            data = None
        if not data:
            return url, None
        tmp = path.with_name(f"{path.name}.{uuid4().hex}.part")
        tmp.write_bytes(data)
        tmp.replace(path)
        return url, str(path)

    assets: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for url, path in pool.map(one, urls):
            if path:
                assets[f"url:{url}"] = path
    return assets


def attach_chat(
    store: Any,
    plan: dict,
    motion: dict | None,
    fetch: Callable[[str], bytes | None] | None = None,
) -> dict | None:
    """Сообщения чата и картинки эмоутов — в моушн, если есть видимый слой чата."""
    if not motion or not has_chat(motion.get("layers")):
        return motion
    from app.settings import settings

    layers = motion.get("layers") or []
    messages = plan_chat_messages(store, plan, layers)
    if not messages:
        return _with_note(motion, "у клипа нет чата Twitch — слой «чат» пуст (импортируйте чат источника)")
    assets = fetch_chat_images(chat_image_urls(messages, layers), settings.runtime_dir / "emote-cache", fetch)
    return {**motion, "chat": messages, "chat_assets": assets}


def attach_host_data(store: Any, plan: dict, motion: dict | None) -> dict | None:
    """Данные клипа для слоёв, которые рисуют не свои props: слова речи и чат."""
    return attach_chat(store, plan, attach_caption_words(store, plan, motion))


def pack_refs_available() -> bool:
    """Есть ли уже модуль пакетов (эмодзи/стикеры/звуки) с ``resolve_pack_ref``."""
    try:
        from app.motion_assets import resolve_pack_ref as _resolve  # type: ignore[attr-defined]  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def resolve_pack_ref(ref: str) -> Path | None:
    """``emoji:`` / ``sticker:`` / ``sfx:`` → файл пакета (модуль app.motion_assets).

    Модуль пакетов делает другая часть системы; пока его нет (или он не знает
    ссылку) — ссылка считается ненайденной, слой пропускается с пометкой.
    """
    try:
        from app.motion_assets import resolve_pack_ref as _resolve  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - модуля пакетов может ещё не быть
        return None
    try:
        found = _resolve(ref)
    except Exception:  # noqa: BLE001 - чужой модуль не должен ронять рендер
        return None
    if not found:
        return None
    path = Path(found)
    return path if path.exists() else None


# Какие файлы для монтажа годятся слою: картинке — картинка/гифка, видео — видео/гифка,
# звуку — то, где есть звук.
_IMAGE_KINDS = ("image", "gif")
_VIDEO_KINDS = ("video", "gif")


def resolve_layer_assets(store: Any, layers: list[dict]) -> tuple[list[dict], dict[str, str], list[str]]:
    """Найти файлы слоёв. Возвращает (слои, которые можно рисовать, assets.json, пометки).

    Слой без своего файла пропускается: лучше клип без одного стикера, чем
    падение рендера или пустой прямоугольник.
    """
    kept: list[dict] = []
    assets: dict[str, str] = {}
    notes: list[str] = []
    for layer in layers:
        ok = True
        for ref in layer_asset_refs(layer):
            key = asset_key(ref)
            if key in assets:
                continue
            path = _resolve_ref(store, ref, layer["type"])
            if path is None:
                what = f"файл для монтажа {ref}" if isinstance(ref, int) else ref
                if layer["type"] in ("callout", "social"):
                    # Значок и картинка карточки — украшение: плашка нужна и без них.
                    notes.append(f"слой {layer['id']}: значок {what} не найден")
                    continue
                notes.append(f"слой {layer['id']}: {what} не найден — пропущен")
                ok = False
                break
            assets[key] = str(path)
        if ok:
            kept.append(layer)
    return kept, assets, notes


def _resolve_ref(store: Any, ref: int | str, layer_type: str) -> Path | None:
    if isinstance(ref, str) and ref.startswith("webpage:"):
        # Кадр снимка страницы (слой page, app/webpages.py).
        from app.webpages import image_path

        return image_path(store, ref)
    if not isinstance(ref, int):
        return resolve_pack_ref(ref)
    try:
        asset = store.get_montage_asset(ref)
    except KeyError:
        return None
    path = Path(asset.get("file_path") or "")
    if not path.exists():
        return None
    kind = asset.get("kind")
    if layer_type == "audio":
        return path if kind == "audio" or asset.get("has_audio") else None
    if layer_type == "pngtuber":
        # Состояния — картинки/GIF/webm с альфой, голос из файла — звук: годится любой файл.
        return path
    if layer_type == "video":
        return path if kind in _VIDEO_KINDS else None
    if kind == "video" and layer_type == "image":
        # webm с альфой (SVG → webm, app/svg_webm.py) — анимированная картинка, не видео-слой.
        from app.anim_frames import animation_info

        return path if animation_info(path).get("animated") else None
    return path if kind in _IMAGE_KINDS else None


def audio_layer_items(
    layers: list[dict],
    assets: dict[str, str],
    duration: float,
    speech_db: float | None = None,
    speechless: bool = False,
    probe: Callable[[Path], tuple[float, bool]] | None = None,
    notes: list[str] | None = None,
    mix: dict | None = None,
) -> list[dict]:
    """Звуки слоёв, которые прозвучат: ``[{path, start, end, len, offset, read, loop, tempo, volume,
    fade_in, fade_out, duck, track, layer_id, role}]`` — одна правда для рендера
    (``build_audio_layers_args``) и замера сведения (app/audio_mix.py).

    Эффект (audio, mode effect) сначала подгоняется под голос клипа (``speech_db``); дорожка (audio
    mode track, звук video) — нет: volume × gain_db, края с фейдом TRACK_EDGE_FADE. ``mix`` —
    ``render_settings.mix`` (app/audio_mix.normalize_mix): поправки громкости слоёв поверх всего."""
    from app.audio_fx import chain_rate
    from app.audio_mix import layer_role, mix_layer_gain_db
    from app.motion import has_layer_sound, is_track_audio, track_gain

    probe = probe or _probe_sound
    probed: dict[str, tuple[float, bool]] = {}
    usable: list[dict] = []
    for layer in layers:
        if not has_layer_sound(layer):
            continue
        props = layer["props"]
        path = assets.get(asset_key(props.get("asset_id")))
        if not path:
            continue
        role = layer_role(layer, duration)
        if layer["type"] == "video":
            if path not in probed:
                try:
                    probed[path] = probe(Path(path))
                except Exception:  # noqa: BLE001 - не узнали длину — считаем, что звук есть
                    probed[path] = (0.0, True)
            file_duration, has_audio = probed[path]
            item, note = video_sound_item(layer, path, duration, file_duration, has_audio)
            if note and notes is not None:
                notes.append(note)
            if item:
                usable.append({**item, "layer_id": str(layer.get("id") or ""), "role": role,
                               **_layer_fx(props, role, mix)})
            continue
        start = float(layer["start"])
        end = min(float(layer["end"]), float(duration))
        if end - start < 0.05:
            continue
        track = is_track_audio(layer)
        length = end - start
        fade_in = min(float(props.get("fadeIn") or 0), length)
        fade_out = min(float(props.get("fadeOut") or 0), length)
        if track:
            fade_in, fade_out = max(fade_in, TRACK_EDGE_FADE), max(fade_out, TRACK_EDGE_FADE)
        usable.append({
            "path": path, "start": start, "end": end, "len": length, "offset": float(props.get("offset") or 0),
            "read": length, "loop": None, "tempo": 1.0,
            "volume": track_gain(props) if track
            else effect_volume(path, props.get("volume", 1), speech_db, speechless),
            "fade_in": fade_in, "fade_out": fade_out, "duck": bool(props.get("duck")), "track": track,
            "layer_id": str(layer.get("id") or ""), "role": role, **_layer_fx(props, role, mix),
        })
    for item in usable:
        rate = chain_rate(item.get("fx"))
        if abs(rate - 1.0) > 1e-3 and item["read"] is not None and not item["loop"]:
            # speed/tempo в цепочке: слой той же длины читает больше/меньше файла.
            item["read"] = item["read"] * rate
        extra = mix_layer_gain_db(mix, item["layer_id"], item["role"])
        if extra:
            item["volume"] = item["volume"] * 10 ** (extra / 20)
            item["mix_db"] = extra
    return usable


def build_audio_layers_args(
    clip_path: Path,
    output_path: Path,
    layers: list[dict],
    assets: dict[str, str],
    duration: float,
    clip_has_audio: bool = True,
    speech_db: float | None = None,
    speechless: bool = False,
    probe: Callable[[Path], tuple[float, bool]] | None = None,
    notes: list[str] | None = None,
    mix: dict | None = None,
) -> list[str] | None:
    """Подмешать звуки ``audio``-слоёв и звук ``video``-слоёв с ``props.audio`` (видео копией).

    ``speechless`` — в клипе нет речи (effect_gain_db: эффект чуть ниже громких мест).

    Каждый звук: с ``offset`` секунды файла, громкость, fadeIn/fadeOut, сдвиг на
    ``start`` клипа (adelay). ``duck`` приглушает исходник на время звука. Что и как громко —
    ``audio_layer_items`` (там же правило эффекта и дорожки); итог всё равно выравнивает
    финальная нормализация клипа. ``probe`` — (длина, есть ли звук) файла video-слоя; ``notes`` —
    куда сказать, почему звук слоя не подмешан; ``mix`` — ``render_settings.mix``. None — нечего.
    """
    usable = audio_layer_items(layers, assets, duration, speech_db, speechless, probe, notes, mix)
    if not usable:
        return None

    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(clip_path)]
    for item in usable:
        args += audio_item_inputs(item)

    graph: list[str] = []
    parts: list[str] = []
    ducks: list[str] = []
    for n, item in enumerate(usable, start=1):
        graph.append(audio_item_chain(n, item) + f"[a{n}]")
        parts.append(f"[a{n}]")
        if item["duck"]:
            ducks.append(f"between(t,{item['start']:.3f},{item['end']:.3f})")

    if clip_has_audio:
        base = "[0:a]aresample=48000"
        if ducks:
            base += f",volume=enable='{'+'.join(ducks)}':volume={AUDIO_DUCK_VOLUME}"
        graph.append(base + "[a0]")
        parts.insert(0, "[a0]")
        mix_duration = "first"
    else:
        mix_duration = "longest"
    # Без своего звука у клипа дорожка звуков короче видео — дотягиваем тишиной.
    # Звуки ложатся поверх речи и легко перегоняют 0 dBFS — площадки потом хрипят.
    # Лимитер держит пик около −1 dBFS, тише он ничего не делает.
    tail = ",alimiter=limit=0.89:attack=5:release=60:level=disabled" + ("" if clip_has_audio else ",apad")
    graph.append(
        "".join(parts)
        + f"amix=inputs={len(parts)}:duration={mix_duration}:normalize=0:dropout_transition=0{tail}[aout]"
    )
    args += [
        "-filter_complex", ";".join(graph),
        "-map", "0:v:0", "-map", "[aout]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-t", f"{float(duration):.3f}", "-movflags", "+faststart", str(output_path),
    ]
    return args


def render_frame_png(*args: Any, **kwargs: Any) -> bytes:
    """Кадр клипа (``_render_frame_png``) — в очереди кадров (app/heavy_queue.frame_slot): каждый
    кадр поднимает компоновщик, четыре агента с view_frame сразу — лишний гигабайт памяти."""
    from app.heavy_queue import frame_slot

    with frame_slot(f"клип {args[1] if len(args) > 1 else kwargs.get('clip_plan_id')}"):
        return _render_frame_png(*args, **kwargs)


def _render_frame_png(
    store: Any,
    clip_plan_id: int,
    t: float,
    layers: Any = None,
    camera: Any = None,
    inserts: bool = False,
    layers_format: str | None = None,
    subtitles: bool = False,
    notes: list[str] | None = None,
    layer_t: float | None = None,
    sub_t: float | None = None,
) -> bytes:
    """PNG кадра клипа в момент ``t`` с камерой и слоями — для ИИ-агента и превью.

    ``layer_t`` / ``sub_t`` — другое время слоёв (и камеры) и субтитров, чем у кадра основы
    ``t`` (шкала clip): кадр на стоп-кадре посреди ролика (app/freeze.post_to_pre).

    ``subtitles`` — прожечь поверх те же субтитры, что прожжёт рендер (тот же ASS: стиль,
    отступ, формат кадра), если они у клипа включены (``subs_on``) и не заменены слоем
    ``captions``: агент видит наезд субтитров на HUD игры до рендера. ``notes`` — сюда
    допишется, что с субтитрами вышло.

    ``layers_format`` — в каком формате размечены переданные ``layers`` (обложка рисует титр
    сразу в кадре формата клипа); не задан — как у слоёв клипа (``render.layers_format``).

    ``inserts`` — наложить поверх вставки-мемы (гифки, картинки, видео), активные
    в ``t``: в рендере они ложатся отдельным проходом, а агенту их надо видеть.

    Основа берётся из исходника (тот же кроп/рефрейм/лук, что в рендере, без
    баннера и субтитров), а не из готового рендера: в рендере слои и камера уже
    запечены, и кадр показал бы их дважды. Готовый рендер — запасной вариант,
    если файла исходника нет. ``layers``/``camera`` — несохранённый вариант из
    редактора; не переданы — берутся сохранённые в клипе.
    """
    from app import render as _render
    from app.clip_settings import normalize_render_settings
    from app.cover import build_frame_args
    from app.motion import clip_timeline, normalize_camera, normalize_layers
    from app.settings import settings

    command = compositor_command()  # нет компоновщика — сразу понятная ошибка
    plan = store.get_clip_plan(clip_plan_id)
    rs = normalize_render_settings(plan.get("render_settings") or {})
    from app.end_screen import comp_end_screen

    layers = rs["layers"] if layers is None else normalize_layers(layers)
    camera = rs["camera"] if camera is None else normalize_camera(camera)
    layers = [layer for layer in layers if not layer["hidden"] and layer["type"] != "audio"]
    segments = plan.get("segments") or []
    if not segments:
        raise ValueError("в клипе нет кусков")
    from app.piece_speed import spans as speed_spans

    starts, total = clip_timeline(speed_spans(segments), rs["transition"])
    t = min(max(0.0, float(t)), max(0.0, total - 0.02))
    lt = t if layer_t is None else min(max(0.0, float(layer_t)), max(0.0, total - 0.02))
    st = t if sub_t is None else min(max(0.0, float(sub_t)), max(0.0, total - 0.02))
    index = max(i for i, start in enumerate(starts) if start <= t + 1e-9)
    segment = segments[index]
    local = t - starts[index]

    from app.formats import comp_ref

    preset, out_format, layers_format = frame_preset(store, plan, rs, layers_format)
    width = int(preset.get("output_width") or 1080)
    height = int(preset.get("output_height") or 1920)
    fps = float(preset.get("fps") or 30)

    work = settings.tmp_dir / f"motion-frame-{uuid4().hex}"
    work.mkdir(parents=True, exist_ok=True)
    try:
        base_png = work / "base.png"
        source = store.get_source(int(segment.get("source_id") or plan["source_id"]))
        source_path = Path(source.get("local_path") or "")
        keyed: dict = {}
        zone = None
        band = None
        if source_path.is_file():
            # Зелёнка: основа как в рендере (без лука, поля цвета ключа), вырез — в компоновщике.
            preset, keyed = _render._keyed_render(preset, {"key": source.get("chroma_key")})
            keyed = keyed or {}
            framed = dict(segment)
            x = _focus_x_at(segment.get("focus") or [], local)
            if x is not None:
                framed["focus"] = [{"t": 0.0, "x": x}]
            # Кусок «шире окна» (fit) — своя основа, как в рендере. Кадр снимается с секунды
            # start + local (время графа от неё) — рамка аватара по отрезкам (layout.cams) тоже от неё.
            at_frame = {**segment, "start_sec": float(segment["start_sec"]) + local}
            if float(at_frame["start_sec"]) >= float(segment["end_sec"]):
                at_frame["end_sec"] = float(at_frame["start_sec"]) + 0.04
            piece = _render._preset_for_segment(preset, at_frame)
            # Окошко раскладки при камере — полосой под кадром, как в рендере (pin_band).
            band = _render.pin_band(preset, source, {"camera": camera, "key": keyed.get("key")})
            if band:
                piece = {**piece, "pin_band": band}
            if not keyed.get("key"):
                # Анимированный фон meme / куска «шире окна» — как в рендере (app/backgrounds.py).
                from app.backgrounds import piece_zone

                zone = piece_zone(preset.get("layout"), segment, source, source.get("content_crop"), width, height)
            reframe = _render._segment_reframe_x(framed, piece, source)
            graph = _render._render_filter(width, height, fps, piece, None, _render._base_crop(source, keyed), reframe)
            _render._run_ffmpeg(
                [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-ss", f"{float(segment['start_sec']) + local:.3f}", "-i", str(source_path),
                    "-filter_complex", graph, "-map", "[vout]", "-frames:v", "1", str(base_png),
                ],
                timeout=120,
            )
        else:
            rendered = [
                c for c in store.list_clips_for_plan(clip_plan_id)
                if c.get("status") == "succeeded" and c.get("output_path") and Path(c["output_path"]).is_file()
            ]
            if not rendered:
                raise ValueError("нет ни файла видео, ни готового рендера — кадр брать неоткуда")
            _render._run_ffmpeg(build_frame_args(Path(rendered[0]["output_path"]), t, base_png), timeout=120)
        # Компоновщик читает видео и ищет кадр по времени — даём «видео» из одного
        # стоп-кадра длиной до t (2 кадра в секунду: дёшево даже для t = 60 с).
        still = work / "still.mp4"
        _render._run_ffmpeg(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-loop", "1", "-framerate", "2", "-i", str(base_png), "-t", f"{max(t, lt) + 1.0:.3f}",
                "-vf", f"scale={width}:{height + (int(band['h']) if band else 0)}", "-c:v", "libx264", "-preset", "ultrafast",
                "-tune", "stillimage", "-pix_fmt", "yuv420p", str(still),
            ],
            timeout=120,
        )
        layers, assets, _notes = resolve_layer_assets(store, layers)
        layers, camera = reframe_for_output(layers, camera, preset, assets)
        words = plan_caption_words(store, plan) if has_captions(layers) else None
        voice = None
        if any(layer.get("type") == "pngtuber" for layer in layers):
            # Рот PNGtuber в этот кадр: голос слушаем около t (hold и attack смотрят назад).
            from app.pngtuber import plan_voice

            voice, _voice_notes = plan_voice(store, plan, layers, fps, "clip", window=(lt - 1.0, lt + 0.1))
        levels = None
        if react_sources(layers):
            # Реакция на звук в этот кадр: огибающая всего клипа (нормировка — по всему, как в рендере).
            from app.audio_react import plan_levels

            levels, _level_notes = plan_levels(store, plan, react_sources(layers), fps, "clip")
        host = attach_chat(store, plan, {"layers": layers, "camera": camera}) or {}
        assets.update(host.get("chat_assets") or {})
        regions = _render.layout_camera_regions(preset, source, width, height) if camera else None
        comp = write_comp(
            work / "comp.json", width=width, height=height, fps=fps,
            duration=max(total, lt + 0.5), layers=hold_final_layers(layers, total), camera=camera, words=words,
            chat=host.get("chat"), key=keyed.get("key"), background=rs["backdrop"],
            camera_box=(regions or {}).get("box"),
            pinned=_render.banded_pinned((regions or {}).get("pinned"), band, height),
            base_height=height + int(band["h"]) if band else None,
            backgrounds=[{"t0": 0.0, "t1": round(max(total, lt) + 60.0, 3), **zone}] if zone else None,
            ref=comp_ref(out_format), voice=voice, levels=levels,
            end_screen=comp_end_screen(rs.get("end_screen"), layers, total),
            motion_blur=rs.get("motion_blur"),
        )
        assets_path = work / "assets.json"
        assets_path.write_text(json.dumps(assets, ensure_ascii=False), encoding="utf-8")
        videos_path, _video_notes = prepare_video_frames(layers, assets, work, fps=fps,
                                                         out_width=unit_width(width, out_format), at=t,
                                                         fmt=out_format)
        from app.anim_frames import prepare_anim_frames

        anims_path, _anim_notes = prepare_anim_frames(layers, assets, work, out_width=unit_width(width, out_format))
        out = work / "frame.png"
        _render._run_compositor(
            build_compositor_args(comp, assets_path, input_path=still, frame=lt, png_path=out, command=command,
                                  videos_path=videos_path, anims_path=anims_path),
            timeout=180,
        )
        if not out.is_file():
            raise RuntimeError("компоновщик не записал кадр")
        if inserts:
            _overlay_inserts(store, rs.get("inserts") or [], t, out, width, height)
        if subtitles and not words:
            # Слой captions со словами заменяет прожигаемые субтитры (как в рендере) — тогда они уже в кадре.
            _overlay_subtitles(store, plan, rs, preset, st, out, width, height, notes)
        return out.read_bytes()
    finally:
        shutil.rmtree(work, ignore_errors=True)


def contact_sheet_jpeg(
    store: Any, clip_plan_id: int, n: int = 8, width: int = 240, times: list[float] | None = None,
    before_cuts: bool = False, subtitles: bool = False,
) -> bytes:
    """Лист кадров клипа: ``n`` моментов равномерно по клипу, с подписью времени.

    ``subtitles`` — с прожжёнными субтитрами клипа (как в рендере, если они включены).

    Весь монтаж одним взглядом — агенту (одна картинка вместо восьми запросов
    кадра) и человеку на проверке. Кадры — тем же компоновщиком, что и рендер.
    ``times`` — свои моменты (секунды готового клипа, как подписи на листе) вместо
    равномерных: проверить разом все панчлайны или финал. ``before_cuts`` — ``times``
    в секундах клипа ДО вырезов (как у слоёв, камеры и view_frame), подписи тоже в них.
    """
    import tempfile

    picked = sorted({round(float(t), 2) for t in times or [] if t is not None and float(t) >= 0})[:16]
    n = len(picked) if picked else max(2, min(16, int(n)))
    width = max(120, min(540, int(width)))
    plan = store.get_clip_plan(clip_plan_id)
    # Шкалы времени (app/timebase.py): подписи — готовый ролик (final) или clip, кадр
    # компоновщик берёт на шкале clip. Раньше длина и вырезы считались «кусками подряд»
    # и при переходе с наплывом кадр уезжал от подписи на длину наплыва.
    from app.timebase import ClipClock

    clock = ClipClock.for_plan(plan)
    duration = clock.clip_duration
    if duration <= 0:
        raise ValueError("у клипа нет кусков")
    # Есть вырезы — кадры только из того, что останется, с временем готового клипа:
    # агент и человек видят монтаж, а не вырезанные паузы.
    final = clock.final_duration
    times = [round(final * (k + 0.5) / n, 2) for k in range(n)]
    shots = [clock.final_to_clip(t) for t in times]
    if picked and before_cuts:
        # Время слоёв: кадр берём ровно там, подпись — то же число, что в слоях.
        times = [min(t, max(0.0, duration - 0.2)) for t in picked]
        shots = list(times)
    elif picked:
        # Последний кадр компоновщик не отдаёт (агент: падение на 21.5 из 21.55 с) — с запасом в кадры.
        times = [min(t, max(0.0, final - 0.2)) for t in picked]
        shots = [clock.final_to_clip(t) for t in times]
    with tempfile.TemporaryDirectory() as tmp:
        paths: list[Path] = []
        for k in range(n):
            path = Path(tmp) / f"f{k:02d}.png"
            path.write_bytes(render_frame_png(store, clip_plan_id, shots[k], inserts=True, subtitles=subtitles))
            paths.append(path)
        return tile_frames_jpeg(paths, [f"{t:.1f} s" for t in times], width, cols=4)


def source_contact_sheet_jpeg(store: Any, source_id: int, from_sec: float, to_sec: float,
                              step: float | None = None, x: float | None = None) -> tuple[bytes, list[float]]:
    """Лист кадров ИСХОДНИКА между from_sec и to_sec с подписями времени исходника.

    Выбрать момент без Gemini: громкость (source_loudness) подсказывает, где перестрелка,
    а лист показывает, что там на экране. ``x`` (0..1) — показать окно 9:16 с центром x
    (то, что останется в вертикальном кадре), без ``x`` — кадр целиком."""
    import tempfile

    from app import render as _render

    source = store.get_source(int(source_id))
    path = Path(str(source.get("local_path") or ""))
    if not path.is_file():
        raise ValueError("файла исходника нет")
    times = source_sheet_times(from_sec, to_sec, step, source.get("duration_sec"))
    crop = source.get("content_crop")
    pre = (f"crop=iw*{crop['w']:.5f}:ih*{crop['h']:.5f}:iw*{crop['x']:.5f}:ih*{crop['y']:.5f},"
           if crop else "")
    if x is not None:
        cx = min(1.0, max(0.0, float(x)))
        # Окно 9:16 во всю высоту, центр x (у края — прижато), как обрезка рендера.
        vf = f"{pre}crop=trunc(ih*9/16/2)*2:ih:clip(iw*{cx:.4f}-ih*9/32\\,0\\,iw-ih*9/16):0,scale=360:-2"
        width, cols = 180, 6
    else:
        vf = f"{pre}scale=640:-2"
        width, cols = 320 if len(times) <= 12 else 240, 4
    with tempfile.TemporaryDirectory() as tmp:
        paths: list[Path] = []
        for k, t in enumerate(times):
            out = Path(tmp) / f"s{k:02d}.png"
            _render._run_ffmpeg(
                ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(path),
                 "-frames:v", "1", "-vf", vf, str(out)],
                timeout=60,
            )
            paths.append(out)
        # Шаг мельче 0.1 с — подписи до сотых, иначе соседние кадры подписаны одинаково.
        digits = 2 if len(times) > 1 and min(b - a for a, b in zip(times, times[1:])) < 0.1 else 1
        return tile_frames_jpeg(paths, [source_time_label(t, digits) for t in times], width, cols=cols), times
