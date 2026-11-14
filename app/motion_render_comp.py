"""Рендер слоёв: запуск компоновщика и файл композиции, подписи и чат для него (из ``app/motion_render.py``)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from app.motion import caption_words

DEFAULT_CRF = 18


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


class CompositorMissing(RuntimeError):
    """Компоновщик не собран или Node не найден — моушн нарисовать нечем."""


def compositor_command() -> list[str]:
    """``[node, motion-render.mjs]`` или CompositorMissing с понятной причиной."""
    from app.settings import settings

    script = Path(settings.motion_compositor)
    node = settings.motion_node
    # Локально бандл собирается руками и устаревает молча (не было fx negate) — сверка с исходниками,
    # пересборка локальным esbuild или громкое предупреждение (app/motion_bundle.py).
    from app.motion_bundle import ensure_fresh

    try:
        ensure_fresh(script, node)
    except Exception:  # noqa: BLE001 - сверка — подстраховка, рендер важнее
        pass
    if not script.exists():
        raise CompositorMissing(
            f"компоновщик моушна не собран: нет {script} (соберите: npm run build:motion)"
        )
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
    levels: dict | None = None,
    base_height: int | None = None,
    freezes: list[dict] | None = None,
    end_screen: dict | None = None,
    motion_blur: float | None = None,
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
    if end_screen:
        # Концовка-баннер (app/end_screen.comp_end_screen): {start, ramp, blur, dim} — размытие основы под слоями.
        comp["endScreen"] = end_screen
    if motion_blur:
        # Motion blur клипа по умолчанию (render.motion_blur): слоям и камере без своего затвора.
        comp["motionBlur"] = float(motion_blur)
    if words:
        comp["words"] = words
    if chat:
        comp["chat"] = chat
    if voice:
        # Состояния PNGtuber по кадрам (app/pngtuber.py): {id слоя: {t0, fps, states, levels}}.
        comp["voice"] = voice
    if levels:
        # Огибающие громкости для react слоёв (app/audio_react.py): {voice|music|mix: {t0, fps, v}}.
        comp["levels"] = levels
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
    from app.cuts import plan_joins_setting, subtitles_to_final

    try:
        payload = store.get_clip_plan_subtitles(int(plan["id"]))
    except (KeyError, TypeError, ValueError):
        return []
    if not payload or not payload.get("words"):
        return []
    transition = (plan.get("render_settings") or {}).get("transition")
    # Настоящие overlap склеек (как спланирует рендер), а не полная длина перехода.
    from app.translation import orig_comp_words

    # Перевод с оригиналом вторым слоем: слова оригинала (метка orig) — ПОСЛЕ русских,
    # номера highlightWords русских слов не съезжают.
    joins = plan_joins_setting(plan)
    return (caption_words(subtitles_to_final(payload, plan.get("segments") or [], transition, joins=joins))
            + orig_comp_words(payload, plan.get("segments") or [], transition, joins=joins))


def _with_note(motion: dict, note: str) -> dict:
    """Пометка для ошибки клипа: слой пуст, но рендер идёт дальше."""
    return {**motion, "host_notes": [*(motion.get("host_notes") or []), note]}


# ---------------------------------------------------------------- чат стрима

# Сколько сообщений и картинок эмоутов/значков отдаём компоновщику на клип.
MAX_CHAT_MESSAGES = 5000
MAX_CHAT_IMAGES = 400


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
