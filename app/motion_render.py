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
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from app.motion import (
    EFFECT_MAX_VOLUME,
    VIDEO_MAX_SPEED,
    VIDEO_MIN_SPEED,
    VIDEO_TAIL,
    asset_key,
    caption_words,
    has_captions,
    has_chat,
    layer_asset_refs,
    video_file_time,
)

# Насколько приглушать исходник под звуком слоя с ``duck`` (как у вставок).
AUDIO_DUCK_VOLUME = 0.3
DEFAULT_CRF = 18

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


def plan_is_speechless(store: Any, plan: dict) -> bool:
    """В клипе нет речи? Слова субтитров есть — речь есть. Субтитров нет — по расшифровке
    исходника: она известна (или «речи нет») и ни одна реплика не попадает в куски — без
    речи. Не знаем — считаем, что речь есть: эффект тише безопаснее, чем громче голоса."""
    from app.manual_clips import NO_SPEECH

    payload = store.get_clip_plan_subtitles(int(plan["id"])) or {}
    if payload.get("words") or payload.get("lines"):
        return False
    try:
        source_id = int(plan["source_id"])
        cues = store.get_source_transcript(source_id)
        known = bool(cues) or store.get_source_transcript_model(source_id).endswith(NO_SPEECH)
    except (KeyError, TypeError, ValueError):
        return False
    if not known:
        return False
    for seg in plan.get("segments") or []:
        a, b = float(seg["start_sec"]), float(seg["end_sec"])
        if any(float(c.get("start", 0)) < b and float(c.get("end", 0)) > a for c in cues):
            return False
    return True


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


class CompositorMissing(RuntimeError):
    """Компоновщик не собран или Node не найден — моушн нарисовать нечем."""


def compositor_command() -> list[str]:
    """``[node, motion-render.mjs]`` или CompositorMissing с понятной причиной."""
    from app.settings import settings

    script = Path(settings.motion_compositor)
    if not script.exists():
        raise CompositorMissing(
            f"компоновщик моушна не собран: нет {script} (соберите: npm run build:motion)"
        )
    node = settings.motion_node
    if not (Path(node).exists() or shutil.which(node)):
        raise CompositorMissing(f"Node не найден: {node!r} (MOTION_NODE)")
    return [node, str(script)]


def build_compositor_args(
    comp_path: Path,
    assets_path: Path,
    *,
    input_path: Path | None = None,
    output_path: Path | None = None,
    crf: Any = None,
    frame: float | None = None,
    png_path: Path | None = None,
    command: list[str] | None = None,
    videos_path: Path | None = None,
    anims_path: Path | None = None,
) -> list[str]:
    """CLI компоновщика: видео целиком (``--output``) или один кадр (``--frame --png``).
    ``videos_path`` — videos.json с нарезанными кадрами видео-слоёв (``prepare_video_frames``);
    ``anims_path`` — anims.json с кадрами анимаций с альфой (app/anim_frames.prepare_anim_frames)."""
    from app.settings import settings

    args = list(command or compositor_command())
    args += ["--comp", str(comp_path)]
    if input_path is not None:
        args += ["--input", str(input_path)]
    if output_path is not None:
        args += ["--output", str(output_path)]
    args += ["--assets", str(assets_path), "--fonts-dir", str(settings.motion_fonts_dir), "--ffmpeg", "ffmpeg"]
    from app.user_fonts import registry_path

    if registry_path().is_file():
        # Свои шрифты (app/user_fonts.py) — тот же формат реестра; нет — команда прежняя.
        args += ["--user-fonts-dir", str(registry_path().parent)]
    if videos_path is not None:
        args += ["--videos", str(videos_path)]
    if anims_path is not None:
        args += ["--anims", str(anims_path)]
    # Эмодзи внутри текста (титр «ОН ЭТО СДЕЛАЛ 🔥») не попадают в карту ассетов —
    # компоновщик берёт их прямо из пака Twemoji по имени файла.
    emoji_dir = Path(settings.motion_packs_dir) / "twemoji"
    if emoji_dir.is_dir():
        args += ["--emoji-dir", str(emoji_dir)]
    args += ["--crf", str(crf if crf not in (None, "") else DEFAULT_CRF)]
    if frame is not None:
        args += ["--frame", f"{max(0.0, float(frame)):.3f}"]
    if png_path is not None:
        args += ["--png", str(png_path)]
    return args


FINAL_HOLD_EPS = 0.05


def hold_final_layers(layers: list[dict], duration: float) -> list[dict]:
    """Слой до конца клипа держится на финальном стоп-кадре — без анимации выхода.

    Готовый ролик на RENDER_END_HOLD_SEC длиннее: последний кадр стоит, пока затухает
    звук. Слой с ``out`` до самого конца успевал погаснуть до стоп-кадра, и плашка-итог
    пропадала (агент: «финальный титр исчезает»). Такой слой ``out`` игнорирует."""
    if not duration or duration <= 0:
        return layers
    out = []
    for layer in layers:
        anim = layer.get("out")
        try:
            ends = float(layer.get("end", 0)) >= duration - FINAL_HOLD_EPS
        except (TypeError, ValueError):
            ends = False
        if ends and isinstance(anim, dict) and anim.get("type") not in (None, "none"):
            layer = {**layer, "out": {**anim, "type": "none"}}
        out.append(layer)
    return out


def write_comp(
    path: Path,
    *,
    width: int,
    height: int,
    fps: float,
    duration: float,
    layers: list[dict],
    camera: list[dict],
    words: list[dict] | None = None,
    chat: list[dict] | None = None,
    key: dict | None = None,
    background: dict | None = None,
    camera_box: dict | None = None,
    pinned: list[dict] | None = None,
    backgrounds: list[dict] | None = None,
    ref: dict | None = None,
    voice: dict | None = None,
    base_height: int | None = None,
    freezes: list[dict] | None = None,
) -> Path:
    """comp.json для компоновщика. ``camera_box``/``pinned`` — раскладка стрима
    (``camera_regions``): камера двигает только окно игры, окошко вебки стоит на месте.
    ``base_height`` — основа выше кадра на полосу окошка (app/render.pin_band): окошко
    берётся из полосы (``pinned[].sy``), камера зумит игру без него.
    ``ref`` — опорный кадр формата ``{refWidth, refHeight}`` (app/formats.comp_ref); пусто —
    движок берёт 1080×1920, comp.json прежний."""
    comp = {
        "width": int(width),
        "height": int(height),
        **(ref or {}),
        "fps": float(fps),
        "duration": round(float(duration), 3),
        "layers": hold_final_layers(layers, float(duration)),
        "camera": camera,
    }
    if camera_box:
        comp["cameraBox"] = camera_box
    if pinned:
        comp["pinned"] = pinned
    if base_height and int(base_height) > int(height):
        comp["baseHeight"] = int(base_height)
    if freezes:
        # Стоп-кадры, на которых слои стоят (app/freeze.comp_freezes): компоновщик читает
        # слои, камеру и слова во «времени слоёв» (motionTime), основа уже со стоп-кадрами.
        comp["freezes"] = freezes
    if words:
        comp["words"] = words
    if chat:
        comp["chat"] = chat
    if voice:
        # Состояния PNGtuber по кадрам (app/pngtuber.py): {id слоя: {t0, fps, states, levels}}.
        comp["voice"] = voice
    if backgrounds and not key:
        # Анимированный фон основы (app/backgrounds.py): вне видео, до камеры и слоёв.
        comp["backgrounds"] = backgrounds
    if key:
        # Хромакей основы (app/chroma_key.py): компоновщик вырезает зелёнку,
        # кладёт фон и слои «за персонажем», сверху — основу.
        from app.chroma_key import comp_despill, ff_hex, normalize_backdrop

        comp["key"] = {
            "color": ff_hex(key["color"]),
            "similarity": key["similarity"],
            "blend": key["blend"],
            **comp_despill(key),
            "grade": key.get("grade") or "",
        }
        comp["background"] = normalize_backdrop(background)
    path.write_text(json.dumps(comp, ensure_ascii=False), encoding="utf-8")
    return path


def plan_caption_words(store: Any, plan: dict) -> list[dict]:
    """Слова сохранённых субтитров клипа в клиповом времени — для слоёв ``captions``.

    Те же слова и тот же сдвиг на переходы, что у прожигаемых субтитров
    (``ClipRenderService._plan_subtitles_for_clip``): анимированные субтитры
    совпадают с речью ровно так же. Нет субтитров — пусто.
    """
    from app.cuts import subtitles_to_final

    try:
        payload = store.get_clip_plan_subtitles(int(plan["id"]))
    except (KeyError, TypeError, ValueError):
        return []
    if not payload or not payload.get("words"):
        return []
    transition = (plan.get("render_settings") or {}).get("transition")
    # Настоящие overlap склеек (как спланирует рендер), а не полная длина перехода.
    return caption_words(subtitles_to_final(payload, plan.get("segments") or [], transition))


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


def _with_note(motion: dict, note: str) -> dict:
    """Пометка для ошибки клипа: слой пуст, но рендер идёт дальше."""
    return {**motion, "host_notes": [*(motion.get("host_notes") or []), note]}


# ---------------------------------------------------------------- чат стрима

# Сколько сообщений и картинок эмоутов/значков отдаём компоновщику на клип.
MAX_CHAT_MESSAGES = 5000
MAX_CHAT_IMAGES = 400
CHAT_IMAGE_TIMEOUT = 5.0
CHAT_IMAGE_MAX_BYTES = 3 * 1024 * 1024


def chat_windows(layers: list[dict]) -> list[tuple[float, float, float]]:
    """Окна видимых слоёв чата: (start, end, delay) — сообщение t видно, если start <= t - delay < end."""
    out = []
    for layer in layers or []:
        if layer.get("type") != "chat" or layer.get("hidden"):
            continue
        delay = float((layer.get("props") or {}).get("delay") or 0)
        out.append((float(layer["start"]), float(layer["end"]), delay))
    return out


def plan_chat_messages(store: Any, plan: dict, layers: list[dict]) -> list[dict]:
    """Сообщения чата клипа (время клипа, как GET /api/clip-plans/{id}/chat), попадающие в слои чата."""
    try:
        from app.twitch_chat import clip_messages

        messages = clip_messages(store, plan, limit=20000)
    except Exception:  # noqa: BLE001 - нет чата/таблиц — слой просто пуст
        return []
    windows = chat_windows(layers)
    keep = [
        {k: m.get(k) for k in ("t", "user", "color", "text", "fragments", "badges")}
        for m in messages
        if any(start <= float(m["t"]) - delay < end for start, end, delay in windows)
    ]
    return keep[:MAX_CHAT_MESSAGES]


def chat_image_urls(messages: list[dict], layers: list[dict]) -> list[str]:
    """Адреса эмоутов и значков, которые покажут слои чата (с учётом showEmotes/showBadges)."""
    chat_layers = [layer for layer in layers or [] if layer.get("type") == "chat" and not layer.get("hidden")]
    emotes = any((layer.get("props") or {}).get("showEmotes", True) for layer in chat_layers)
    badges = any((layer.get("props") or {}).get("showBadges", True) for layer in chat_layers)
    urls: list[str] = []
    seen: set[str] = set()

    def add(url: Any) -> None:
        if isinstance(url, str) and url.startswith(("http://", "https://")) and url not in seen:
            seen.add(url)
            urls.append(url)

    for message in messages:
        if emotes:
            for frag in message.get("fragments") or []:
                if isinstance(frag, dict) and frag.get("emote"):
                    add(frag.get("url"))
        if badges:
            for badge in message.get("badges") or []:
                if isinstance(badge, dict):
                    add(badge.get("url"))
    return urls[:MAX_CHAT_IMAGES]


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
                if layer["type"] == "callout":
                    # Значок — украшение: плашка нужна и без него.
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
    return path if kind in _IMAGE_KINDS else None


# ---------------------------------------------------------------- видео-слои

# Компоновщик рисует кадр синхронно, а декодировать видео на лету в Node нечем — поэтому
# перед моушн-проходом ffmpeg режет каждому видео-слою ровно его отрезок файла в JPEG:
# в нужном масштабе (не больше, чем слой займёт на кадре, ≤ ширины кадра вывода) и с шагом
# fps рендера / speed, чтобы на кадр выхода приходился один файл. Компоновщик держит в
# памяти лишь несколько кадров на слой; папка кадров живёт в work-папке прохода и
# удаляется вместе с ней.
VIDEO_FRAME_PATTERN = "%05d.jpg"
# Видимая ширина слоя на кадре — не больше ширины кадра вывода: 1080 у 9:16 / 1:1 / 4:5,
# 1920 у 16:9 (video_max_frame_width). Окно crop из большого исходника (4K) режется с
# запасом: весь кадр файла = видимая ширина / доля окна — до размера исходника.
VIDEO_MAX_FRAME_WIDTH = 1080
VIDEO_MIN_FRAME_WIDTH = 32
# Потолок одного кадра (px): компоновщик держит ~8 декодированных кадров RGBA на слой —
# 2560×1440 ≈ 15 МБ на кадр (≈ 120 МБ на слой; прежний худший случай — 1080×1920 ≈ 8 МБ).
VIDEO_MAX_FRAME_PIXELS = 2560 * 1440
# Потолок всех кадров слоя (px) — прежний худший случай (9000 кадров 1080×1920 ≈ 2–3 ГБ JPEG
# на диске): длинный слой режется мельче, но не мельче прежнего предела 1080.
VIDEO_MAX_TOTAL_PIXELS = 9000 * 1080 * 1920
# Запас резкости: слой чуть крупнее расчётного (pop с перелётом, наезд ключами).
VIDEO_WIDTH_MARGIN = 1.15
# Потолок кадров на слой (5 мин при 30 к/с ≈ 9000 JPEG ~ 1 ГБ диска) — дальше стоп-кадр.
VIDEO_MAX_FRAMES = 9000
VIDEO_FRAMES_TIMEOUT = 600


def _key_values(layer: dict, prop: str) -> list[float]:
    keys = (layer.get("keys") or {}).get(prop) or []
    out = []
    for point in keys:
        try:
            out.append(float(point["v"]))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def unit_width(width: int, fmt: Any = None) -> int:
    """Ширина кадра «в единицах 1080»: px слоя × это / 1080 = px кадра. У форматов с опорной
    шириной 1080 — сама ширина; у 16:9 (опорный 1920×1080) — 1080 при кадре 1920."""
    from app.formats import ref_size, settings_format

    ref_w = ref_size(settings_format(fmt))[0]
    return int(width) if ref_w == 1080 else round(int(width) * 1080 / ref_w)


def reframe_for_output(layers: list[dict], camera: list[dict] | None, preset: dict,
                       assets: dict[str, str]) -> tuple[list[dict], list[dict]]:
    """Слои и камера — из формата их разметки в формат вывода пресета (app/formats.motion_formats).

    Один путь для рендера (render._apply_motion) и кадра агента (render_frame_png): формат
    разметки совпадает с выводом (16:9 → 16:9, 9:16 → 9:16) — как есть, без перекомпоновки."""
    from app.formats import motion_formats

    out_format, layers_format = motion_formats(preset)
    if out_format == layers_format:
        return layers, camera or []
    from app.aspect import asset_sizes, reframe_between

    return reframe_between(layers, camera, layers_format, out_format, asset_sizes(layers, assets))


def video_max_frame_width(fmt: Any = None) -> int:
    """Предел видимой ширины кадров видео-слоя по формату вывода: ширина кадра формата
    (1920 у 16:9), но не меньше прежних 1080 (9:16, 1:1, 4:5 — как всегда)."""
    from app.formats import frame_size, settings_format

    return max(VIDEO_MAX_FRAME_WIDTH, int(frame_size(settings_format(fmt))[0]))


def video_frame_width(layer: dict, out_width: int, natural: tuple[int, int] | None = None,
                      fmt: Any = None, count: int = 1) -> int:
    """Ширина нарезанных кадров, px: сколько слой займёт на кадре выхода (с наибольшим
    масштабом из ключей и самым узким окном crop) с запасом.

    Видимая часть — не шире кадра вывода (``video_max_frame_width(fmt)``: 1080, у 16:9 —
    1920); окно crop — с запасом (весь кадр = видимая / доля окна), но не больше исходника,
    ``VIDEO_MAX_FRAME_PIXELS`` на кадр и ``VIDEO_MAX_TOTAL_PIXELS`` на все ``count`` кадров
    (длинный слой — не мельче прежнего предела 1080)."""
    props = layer.get("props") or {}
    try:
        w = float(props.get("w") or 600)
    except (TypeError, ValueError):
        w = 600.0
    scale = max([float(layer.get("scale") or 1.0), *_key_values(layer, "scale")])
    crop = props.get("crop") if isinstance(props.get("crop"), dict) else {}
    crop_w = max(0.01, min(1.0, min([float(crop.get("w") or 1.0), *_key_values(layer, "cropW")])))
    visible = w * (out_width / 1080) * max(0.05, scale) * VIDEO_WIDTH_MARGIN
    cap = video_max_frame_width(fmt) / crop_w
    tall = 9 / 16  # высота / ширина кадра файла (для потолков по площади)
    if natural and max(natural) > 0:
        # Запись с телефона хранится повёрнутой (поворот в метаданных) — берём большую сторону.
        cap = min(cap, max(natural))
        if min(natural) > 0:
            tall = natural[1] / natural[0]
    width = min(cap, visible / crop_w)
    floor = min(width, VIDEO_MAX_FRAME_WIDTH)  # прежний предел — не урезаем никогда
    width = min(width, math.sqrt(VIDEO_MAX_FRAME_PIXELS / tall),
                math.sqrt(VIDEO_MAX_TOTAL_PIXELS / (max(1, int(count)) * tall)))
    width = int(max(VIDEO_MIN_FRAME_WIDTH, round(max(floor, width))))
    return width - width % 2


def video_frame_plan(layer: dict, fps: float, file_duration: float | None,
                     clip_duration: float | None = None, at: float | None = None) -> dict | None:
    """Какой отрезок файла резать слою: ``{start, rate, span, count}`` (кадр i — секунда
    ``start + i / rate``). ``at`` — только один кадр в момент клипа (кадр для агента).
    None — резать нечего (слой не виден в ``at`` или кадра нет)."""
    props = layer.get("props") or {}
    start_t = float(layer.get("start") or 0.0)
    end_t = float(layer.get("end") or start_t)
    if clip_duration:
        end_t = min(end_t, float(clip_duration) + 1.0)  # стоп-кадр в конце ролика
    if at is not None:
        if not (start_t <= at < max(end_t, start_t + 0.1)):
            return None
        t = video_file_time(props, at - start_t, file_duration)
        return None if t is None else {"start": round(t, 3), "rate": None, "span": None, "count": 1}
    if props.get("hold"):
        # Стоп-кадр: весь слой — один кадр секунды from, нарезка не нужна.
        t = video_file_time(props, 0.0, file_duration)
        if file_duration and file_duration > 0 and t is not None and t >= file_duration - VIDEO_TAIL:
            t = max(0.0, file_duration - 0.25)  # у самого конца файла кадр бывает пустым
        return {"start": round(t or 0.0, 3), "rate": None, "span": None, "count": 1}
    speed = min(VIDEO_MAX_SPEED, max(VIDEO_MIN_SPEED, float(props.get("speed") or 1.0)))
    rate = max(0.1, float(fps or 30)) / speed
    begin = max(0.0, float(props.get("from") or 0.0))
    need = max(0.1, end_t - start_t) * speed + 2.0 / rate
    if file_duration and file_duration > 0:
        if begin >= file_duration - VIDEO_TAIL:
            # from за концом файла: только последний кадр (стоп-кадр).
            return {"start": round(max(0.0, file_duration - 0.25), 3), "rate": None, "span": None, "count": 1}
        span = min(need, file_duration - begin)
    else:
        span = need
    count = max(1, min(VIDEO_MAX_FRAMES, math.ceil(span * rate)))
    return {"start": round(begin, 3), "rate": round(rate, 6), "span": round(span, 3), "count": count}


def build_video_frames_args(src: Path, out_dir: Path, plan: dict, width: int) -> list[str]:
    """ffmpeg: отрезок файла → JPEG-кадры ``out_dir/00000.jpg…`` (немые, нужной ширины)."""
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{float(plan['start']):.3f}"]
    if plan.get("span"):
        args += ["-t", f"{float(plan['span']):.3f}"]
    args += ["-i", str(src), "-an", "-sn", "-dn"]
    scale = f"scale={int(width)}:-2:flags=lanczos"
    if int(width) > VIDEO_MAX_FRAME_WIDTH:
        # Шире прежнего предела (16:9, окно из 4K) — площадь кадра ещё и по настоящей
        # пропорции после поворота (iw/ih): вертикальная запись не раздувается в высоту.
        scale = (f"scale='min({int(width)},trunc(sqrt({VIDEO_MAX_FRAME_PIXELS}*iw/ih)/2)*2)':-2"
                 ":flags=lanczos")
    args += ["-vf", f"fps={plan['rate']:g},{scale}" if plan.get("rate") else scale]
    args += ["-frames:v", str(int(plan["count"])), "-pix_fmt", "yuvj420p", "-q:v", "2",
             "-start_number", "0", str(out_dir / VIDEO_FRAME_PATTERN)]
    return args


def _probe_video(path: Path) -> tuple[float, tuple[int, int] | None]:
    from app.montage_assets import probe_asset

    meta = probe_asset(path)
    natural = (int(meta.get("width") or 0), int(meta.get("height") or 0))
    return float(meta.get("duration_sec") or 0.0), natural if all(natural) else None


def prepare_video_frames(
    layers: list[dict],
    assets: dict[str, str],
    work: Path,
    *,
    fps: float,
    out_width: int,
    clip_duration: float | None = None,
    at: float | None = None,
    fmt: Any = None,
    run: Callable[[list[str], int], Any] | None = None,
    probe: Callable[[Path], tuple[float, tuple[int, int] | None]] = _probe_video,
) -> tuple[Path | None, list[str]]:
    """Нарезать кадры видео-слоёв → ``work/videos.json`` для ``--videos`` компоновщика.

    ``fmt`` — формат вывода: предел ширины кадров (``video_max_frame_width``; у 16:9 — 1920).
    Возвращает (путь к videos.json или None, пометки). Слой, чьё видео не нарезалось,
    остаётся без кадров (в телефоне — тёмный экран), рендер идёт дальше.
    """
    if run is None:
        from app.render import _run_ffmpeg as run  # type: ignore[assignment]
    manifest: dict[str, dict] = {}
    notes: list[str] = []
    probed: dict[str, tuple[float, tuple[int, int] | None]] = {}
    for layer in layers:
        if layer.get("type") != "video" or layer.get("hidden"):
            continue
        src = assets.get(asset_key((layer.get("props") or {}).get("asset_id")))
        if not src:
            continue
        if src not in probed:
            probed[src] = probe(Path(src))
        duration, natural = probed[src]
        plan = video_frame_plan(layer, fps, duration, clip_duration, at)
        if plan is None:
            continue
        out_dir = work / "video-frames" / str(layer["id"])
        out_dir.mkdir(parents=True, exist_ok=True)
        width = video_frame_width(layer, out_width, natural, fmt, plan["count"])
        try:
            run(build_video_frames_args(Path(src), out_dir, plan, width), VIDEO_FRAMES_TIMEOUT)
        except Exception as exc:  # noqa: BLE001 - один слой не должен ронять рендер
            notes.append(f"слой {layer['id']}: видео не нарезалось ({str(exc)[:160]})")
            continue
        count = len(list(out_dir.glob("*.jpg")))
        if not count and duration > 0:
            # Перемотка к самому концу файла бывает пустой — берём кадр чуть раньше (стоп-кадр).
            plan = {"start": round(max(0.0, duration - 0.3), 3), "rate": None, "span": None, "count": 1}
            try:
                run(build_video_frames_args(Path(src), out_dir, plan, width), VIDEO_FRAMES_TIMEOUT)
            except Exception:  # noqa: BLE001
                pass
            count = len(list(out_dir.glob("*.jpg")))
        if not count:
            notes.append(f"слой {layer['id']}: в видео нет кадров с {plan['start']:g} с")
            continue
        manifest[str(layer["id"])] = {
            "dir": str(out_dir),
            "pattern": VIDEO_FRAME_PATTERN,
            "start": plan["start"],
            # Один кадр (кадр для агента): индекс всегда 0.
            "rate": plan["rate"] or 1.0,
            "count": count,
            "duration": round(duration, 3),
        }
    if not manifest:
        return None, notes
    path = work / "videos.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return path, notes


def build_motion_mux_args(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
    preset: dict,
    banner: dict | None,
) -> list[str]:
    """Видео компоновщика + звук основы; баннер — поверх слоёв (как раньше поверх видео).

    Без баннера видео копируется как есть — проход почти бесплатный.
    """
    from app.render import _banner_input_args, _banner_overlay, _json_dict

    output_path.parent.mkdir(parents=True, exist_ok=True)
    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(video_path)]
    next_input = 1
    if banner:
        args += _banner_input_args(banner)
        next_input += 1
    if Path(audio_path) == Path(video_path):
        audio_index = 0
    else:
        args += ["-i", str(audio_path)]
        audio_index = next_input
    if banner:
        height = int(preset.get("output_height") or 1920)
        args += ["-filter_complex", _banner_overlay(banner, preset, height, "1:v", "0:v"), "-map", "[vout]"]
        args += ["-c:v", str(preset.get("video_codec") or "libx264")]
        video_bitrate = str(preset.get("video_bitrate") or "").strip()
        if video_bitrate:
            args += ["-b:v", video_bitrate]
        crf = _json_dict(preset.get("extra_json")).get("crf")
        if crf is not None:
            args += ["-crf", str(crf)]
    else:
        args += ["-map", "0:v:0", "-c:v", "copy"]
    args += ["-map", f"{audio_index}:a:0?", "-c:a", "copy", "-movflags", "+faststart", str(output_path)]
    return args


# Края звука-дорожки (звук video, audio mode track): короткий фейд, чтобы обрезка посреди
# волны не щёлкала. 15 мс — затухания не слышно, а щелчка нет.
TRACK_EDGE_FADE = 0.015


def _probe_sound(path: Path) -> tuple[float, bool]:
    """(длина файла, с; есть ли звук) — для звука video-слоя."""
    from app.montage_assets import probe_asset

    meta = probe_asset(path)
    return float(meta.get("duration_sec") or 0.0), bool(meta.get("has_audio"))


def video_sound_item(layer: dict, path: str, duration: float, file_duration: float | None,
                     has_audio: bool = True) -> tuple[dict | None, str | None]:
    """Звук video-слоя с ``props.audio``: что подмешать, или (None, пометка), почему тишина.

    Время — как у картинки (``video_file_time``): секунда файла = from + (t − start)·speed;
    за концом файла loop — снова с from, иначе тишина (freeze держит кадр, но не звук);
    hold — стоп-кадр, звука нет; speed вне VIDEO_AUDIO_MIN..MAX — звука нет (atempo дальше
    звучит кашей)."""
    from app.motion import VIDEO_AUDIO_MAX_SPEED, VIDEO_AUDIO_MIN_SPEED, track_gain

    props = layer.get("props") or {}
    lid = layer.get("id")
    if not has_audio:
        return None, f"слой {lid}: у видео нет звуковой дорожки — audio ничего не даст"
    if props.get("hold"):
        return None, None  # стоп-кадр — тишина, это ожидаемо
    speed = min(VIDEO_MAX_SPEED, max(VIDEO_MIN_SPEED, float(props.get("speed") or 1.0)))
    if not (VIDEO_AUDIO_MIN_SPEED - 1e-6 <= speed <= VIDEO_AUDIO_MAX_SPEED + 1e-6):
        return None, (f"слой {lid}: speed {speed:g} вне {VIDEO_AUDIO_MIN_SPEED:g}..{VIDEO_AUDIO_MAX_SPEED:g} — "
                      "звук видео не играет")
    start = float(layer["start"])
    end = min(float(layer["end"]), float(duration))
    begin = max(0.0, float(props.get("from") or 0.0))
    if end - start < 0.05:
        return None, None
    length = end - start  # секунды клипа
    loop_span = None
    if file_duration and file_duration > 0:
        rest = file_duration - VIDEO_TAIL - begin
        if rest <= 0.05:
            return None, None  # from за концом файла — кадр стоит, звука нет
        if props.get("loop") and rest < length * speed:
            loop_span = rest
        else:
            length = min(length, rest / speed)
    if length < 0.05:
        return None, None
    return {
        "path": path, "start": start, "end": start + length, "len": length, "offset": begin,
        # Сколько секунд файла читать: с петлёй — до конца файла (петлю делает aloop).
        "read": None if loop_span else length * speed, "loop": loop_span, "tempo": speed,
        "volume": track_gain(props), "fade_in": TRACK_EDGE_FADE, "fade_out": TRACK_EDGE_FADE,
        "duck": False, "track": True,
    }, None


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
) -> list[str] | None:
    """Подмешать звуки ``audio``-слоёв и звук ``video``-слоёв с ``props.audio`` (видео копией).

    ``speechless`` — в клипе нет речи (effect_gain_db: эффект чуть ниже громких мест).

    Каждый звук: с ``offset`` секунды файла, громкость, fadeIn/fadeOut, сдвиг на
    ``start`` клипа (adelay). ``duck`` приглушает исходник на время звука. Эффект
    (audio, mode effect) сначала подгоняется под голос клипа; дорожка (audio mode track,
    звук video) — нет: volume × gain_db, края с фейдом TRACK_EDGE_FADE (итог всё равно
    выравнивает финальная нормализация клипа). ``probe`` — (длина, есть ли звук) файла
    video-слоя; ``notes`` — куда сказать, почему звук слоя не подмешан. None — нечего.
    """
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
                usable.append(item)
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
        })
    if not usable:
        return None

    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(clip_path)]
    for item in usable:
        if item["offset"] > 0:
            args += ["-ss", f"{item['offset']:.3f}"]
        if item["read"] is not None:
            args += ["-t", f"{item['read']:.3f}"]
        args += ["-i", item["path"]]

    graph: list[str] = []
    parts: list[str] = []
    ducks: list[str] = []
    for n, item in enumerate(usable, start=1):
        length = item["len"]
        # Звуки пака — моно: без стерео на входе amix сводил ВСЁ в моно (клип 575: музыка
        # потеряла стерео, а громкость потом мерилась уже по моно-дорожке).
        chain = [f"[{n}:a]aresample=48000", "aformat=channel_layouts=stereo", "asetpts=PTS-STARTPTS"]
        if item["loop"]:
            # Петля видео: от from до конца файла по кругу (как loop картинки).
            chain.append(f"aloop=loop=-1:size={max(1, int(round(item['loop'] * 48000)))}")
        if abs(item["tempo"] - 1.0) >= 1e-3:
            chain.append(f"atempo={item['tempo']:.4f}")
        if item["loop"] or abs(item["tempo"] - 1.0) >= 1e-3:
            # Ровно длина слоя в клипе (петля бесконечна, atempo округляет).
            chain += [f"atrim=0:{length:.3f}", "asetpts=PTS-STARTPTS"]
        chain.append(f"volume={item['volume']:.4f}")
        if item["fade_in"] > 0:
            chain.append(f"afade=t=in:st=0:d={item['fade_in']:.3f}")
        if item["fade_out"] > 0:
            chain.append(f"afade=t=out:st={max(0.0, length - item['fade_out']):.3f}:d={item['fade_out']:.3f}")
        delay = int(round(item["start"] * 1000))
        chain.append(f"adelay=delays={delay}:all=1")
        graph.append(",".join(chain) + f"[a{n}]")
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


# ---------------------------------------------------------------- кадр для агента/превью


def _focus_x_at(focus: list, local: float) -> float | None:
    """Где центр кадра в момент ``local`` куска: линейно между точками, ступенькой на cut."""
    points = []
    for point in focus or []:
        try:
            points.append((float(point["t"]), float(point["x"]), bool(point.get("cut"))))
        except (KeyError, TypeError, ValueError):
            continue
    if not points:
        return None
    points.sort()
    if local <= points[0][0]:
        return points[0][1]
    for (t0, x0, _c0), (t1, x1, cut1) in zip(points, points[1:]):
        if t0 <= local < t1:
            if cut1 or t1 - t0 <= 1e-6:
                return x0
            return x0 + (x1 - x0) * (local - t0) / (t1 - t0)
    return points[-1][1]


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
    layers = rs["layers"] if layers is None else normalize_layers(layers)
    camera = rs["camera"] if camera is None else normalize_camera(camera)
    layers = [layer for layer in layers if not layer["hidden"] and layer["type"] != "audio"]
    segments = plan.get("segments") or []
    if not segments:
        raise ValueError("в клипе нет кусков")
    starts, total = clip_timeline([(float(s["start_sec"]), float(s["end_sec"])) for s in segments], rs["transition"])
    t = min(max(0.0, float(t)), max(0.0, total - 0.02))
    lt = t if layer_t is None else min(max(0.0, float(layer_t)), max(0.0, total - 0.02))
    st = t if sub_t is None else min(max(0.0, float(sub_t)), max(0.0, total - 0.02))
    index = max(i for i, start in enumerate(starts) if start <= t + 1e-9)
    segment = segments[index]
    local = t - starts[index]

    service = _render.ClipRenderService(store)
    preset = service._preset(rs["preset_id"])
    preset = service._preset_with_render_overrides(preset, mirror=rs["mirror"], layout=rs["layout"])
    # Формат вывода клипа (app/formats.py) — кадр того же размера, основа и раскладка, как
    # в рендере, слои перенесены из формата их разметки так же, как в рендере.
    from app.formats import comp_ref, motion_formats, settings_format

    layers_format = settings_format(layers_format) if layers_format else rs["layers_format"]
    plan_source = store.get_source(int(plan["source_id"]))
    preset = _render._preset_with_output(preset, plan_source, rs["format"], 0.0, layers_format)
    out_format, layers_format = motion_formats(preset)
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
            # Кусок «шире окна» (fit) — своя основа, как в рендере.
            piece = _render._preset_for_segment(preset, segment)
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
                raise ValueError("нет ни файла исходника, ни готового рендера — кадр брать неоткуда")
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
            ref=comp_ref(out_format), voice=voice,
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


def frame_subtitles_ass(store: Any, plan: dict, rs: dict, preset: dict, width: int, height: int,
                        path: Path) -> Path | None:
    """ASS субтитров клипа в шкале clip (до вырезов, как кадр агента) — тем же расчётом, что
    рендер (``ClipRenderService._render_subtitles``): стиль клипа или по умолчанию, отступ
    ``sub_pos_pct``, поправка времени стиля, раскладка под формат кадра. None — субтитры
    выключены или их нет."""
    from app import render as _render
    from app.clip_settings import settings_to_render_kwargs
    from app.clip_subtitles import _default_profile, result_from_payload
    from app.cuts import subtitles_to_final
    from app.subtitles.ass import write_ass_subtitles

    if not rs.get("subs_on"):
        return None
    payload = store.get_clip_plan_subtitles(int(plan["id"])) or {}
    if not (payload.get("lines") or payload.get("words")):
        return None
    try:
        default_id = int(store.get_app_setting_value("default_subtitle_profile_id") or 0) or None
    except (TypeError, ValueError):
        default_id = None
    kwargs = settings_to_render_kwargs(rs, default_id)
    try:
        profile = (store.get_subtitle_profile(int(kwargs["subtitle_profile_id"])) if kwargs.get("subtitle_profile_id")
                   else _default_profile(store))
    except (KeyError, TypeError, ValueError):
        return None
    margin = kwargs.get("subtitle_margin_v")
    if margin is not None:
        profile = {**profile, "margin_v": max(0, int(margin))}
    elif preset.get("format"):
        profile = {**profile, "margin_v": round(int(profile.get("margin_v") or 160) * height / 1920)}
    result = result_from_payload(subtitles_to_final(payload, plan.get("segments") or [], rs.get("transition")))
    end = max((float(w.end) for w in result.words), default=0.0) + 5.0
    result = _render._apply_subtitle_timing_offset(result, profile, end)
    return write_ass_subtitles(result, profile, path, width=width, height=height, fmt=preset.get("format"))


def build_frame_subtitle_args(png: Path, out: Path, ass_name: str, t: float) -> list[str]:
    """ffmpeg: прожечь ASS (файл ``ass_name`` в рабочей папке) в кадр ``png`` как момент ``t``."""
    from app.render import _filter_filename

    return ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(png),
            "-vf", f"setpts=PTS-STARTPTS+{max(0.0, float(t)):.3f}/TB,ass={_filter_filename(ass_name)}",
            "-frames:v", "1", str(out)]


def _overlay_subtitles(store: Any, plan: dict, rs: dict, preset: dict, t: float, png: Path, width: int,
                       height: int, notes: list[str] | None) -> None:
    """Прожечь субтитры клипа в кадр ``png`` (момент ``t`` шкалы clip). Сбой — кадр как был, пометка."""
    from app import render as _render

    try:
        ass = frame_subtitles_ass(store, plan, rs, preset, width, height, png.with_name("frame-subs.ass"))
        if ass is None:
            return
        tmp = png.with_name("frame-subs.png")
        _render._run_ffmpeg(build_frame_subtitle_args(png, tmp, ass.name, t), timeout=60, cwd=ass.parent)
        tmp.replace(png)
        if notes is not None:
            notes.append("субтитры прожжены как в рендере (subtitles: false — без них)")
    except Exception as exc:  # noqa: BLE001 - без субтитров кадр всё равно полезен
        if notes is not None:
            notes.append(f"субтитры в кадр не легли: {str(exc)[:160]}")


def _overlay_inserts(store: Any, inserts: list[dict], t: float, png: Path, width: int, height: int) -> None:
    """Наложить на кадр ``png`` вставки, активные в ``t`` (кадр самой вставки на t − at),
    тем же размером и местом, что в рендере (``app.montage_assets``). Ошибка — кадр как был."""
    from app import render as _render
    from app.montage_assets import INSERT_MAX_HEIGHT

    active = []
    for ins in inserts:
        if ins.get("mode") == "sound" or not (ins["at"] <= t < ins["at"] + ins["duration"]):
            continue
        try:
            asset = store.get_montage_asset(int(ins["asset_id"]))
        except (KeyError, TypeError, ValueError):
            continue
        if asset["kind"] != "audio" and Path(asset["file_path"]).is_file():
            active.append((ins, asset))
    if not active:
        return
    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(png)]
    graph, last = [], "[0:v]"
    for n, (ins, asset) in enumerate(active, start=1):
        offset = max(0.0, t - ins["at"])
        if asset["kind"] in ("image", "gif"):
            # Гифка — первым кадром: агенту важны место и размер, а «гифки» бывают
            # и из одного кадра — перемотка ушла бы за конец.
            args += ["-i", asset["file_path"]]
        else:
            args += ["-stream_loop", "-1", "-ss", f"{offset:.3f}", "-i", asset["file_path"]]
        if ins["mode"] == "full":
            box_w, box_h, x, y = width, height, "(W-w)/2", "(H-h)/2"
        else:
            box_w = max(2, int(width * ins["scale"]) // 2 * 2)
            box_h = max(2, int(height * INSERT_MAX_HEIGHT) // 2 * 2)
            x, y = f"(W*{ins['x']:.4f})-(w/2)", f"(H*{ins['y']:.4f})-(h/2)"
        graph.append(f"[{n}:v]scale={box_w}:{box_h}:force_original_aspect_ratio=decrease,format=rgba[i{n}]")
        graph.append(f"{last}[i{n}]overlay=x={x}:y={y}[o{n}]")
        last = f"[o{n}]"
    tmp = png.with_name("frame-inserts.png")
    try:
        _render._run_ffmpeg(
            [*args, "-filter_complex", ";".join(graph), "-map", last, "-frames:v", "1", str(tmp)], timeout=60,
        )
        tmp.replace(png)
    except Exception:  # noqa: BLE001 - без вставок кадр всё равно полезен
        return


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


def tile_frames_jpeg(paths: list[Path], labels: list[str], width: int, cols: int = 4) -> bytes:
    """Кадры сеткой по ``cols`` в ряд, каждый шириной ``width`` с подписью в углу → JPEG."""
    import subprocess
    import tempfile

    from app.settings import settings as app_settings

    n = len(paths)
    if not n:
        raise ValueError("нет кадров для листа")
    cols = max(1, min(cols, n))
    font = (Path(app_settings.motion_fonts_dir) / "Inter-SemiBold.ttf").as_posix().replace(":", "\\:")
    inputs: list[str] = []
    for path in paths:
        inputs += ["-i", str(path)]
    chains = [
        f"[{k}:v]scale={width}:-2,drawtext=fontfile='{font}':text='{_drawtext_escape(labels[k])}':x=8:y=8"
        f":fontsize={max(14, width // 12)}:fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=4[v{k}]"
        for k in range(n)
    ]
    if n == 1:
        graph = chains[0].replace("[v0]", "[out]")
    else:
        # xstack: координаты плиток суммой ширин/высот первой плитки.
        layout = "|".join(f"{'+'.join(['w0'] * (k % cols)) or '0'}_{'+'.join(['h0'] * (k // cols)) or '0'}"
                          for k in range(n))
        graph = (";".join(chains) + ";" + "".join(f"[v{k}]" for k in range(n))
                 + f"xstack=inputs={n}:layout={layout}:fill=black[out]")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "sheet.jpg"
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", graph, "-map", "[out]", "-q:v", "4", str(out)],
            capture_output=True, text=True, timeout=120, check=False,
        )
        if proc.returncode != 0 or not out.exists():
            raise RuntimeError(f"лист кадров не собран: {proc.stderr[-300:]}")
        return out.read_bytes()


def _drawtext_escape(text: str) -> str:
    """Подпись для drawtext: двоеточия экранируются (время исходника «1:02:03.5»)."""
    return str(text).replace("'", "").replace("\\", "").replace(":", "\\:").replace("%", "\\%")


MAX_SOURCE_SHEET = 24


MIN_SOURCE_SHEET_STEP = 0.04


def source_sheet_times(from_sec: float, to_sec: float, step: float | None, duration: float | None) -> list[float]:
    """Моменты листа кадров исходника: через ``step`` (не больше 24), иначе 12 равномерно."""
    lo = max(0.0, float(from_sec))
    hi = float(to_sec)
    if duration:
        hi = min(hi, max(0.0, float(duration) - 0.1))
    if hi <= lo:
        raise ValueError("to_sec должен быть больше from_sec (и внутри исходника)")
    if step:
        # До 0.04 с — покадрово (25 кадр/с) на коротком окне: агент апекса искал кадр выстрела.
        step = max(MIN_SOURCE_SHEET_STEP, float(step))
        count = min(MAX_SOURCE_SHEET, int((hi - lo) / step + 1e-6) + 1)
        return [round(lo + k * step, 3) for k in range(count)]
    count = 12 if hi - lo >= 6 else max(2, int(hi - lo) + 1)
    return [round(lo + (hi - lo) * (k + 0.5) / count, 2) for k in range(count)]


def source_time_label(t: float, digits: int = 1) -> str:
    """Секунды исходника → «1:02:03.5» / «2:03.5» (``digits`` знаков после точки)."""
    t = max(0.0, float(t))
    hours, rest = divmod(t, 3600)
    minutes, seconds = divmod(rest, 60)
    sec = f"{seconds:0{3 + digits}.{digits}f}"
    return f"{int(hours)}:{int(minutes):02d}:{sec}" if hours >= 1 else f"{int(minutes)}:{sec}"


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
