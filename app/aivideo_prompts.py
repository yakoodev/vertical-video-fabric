"""ИИ-видео: промпты и референсы запусков, отпечаток запроса (из ``app/aivideo_gen.py``)."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

from app import aivideo as V
from app import projects as P
from app.aivideo_providers import (
    RESOLUTION, ProviderError, default_video_model, image_price, video_capabilities, max_refs, video_price,
    video_seconds_range
)
from app.settings import settings
from app.aivideo_budget import ACTIVE, REF_LABELS, _ext_of_kind
from app.aivideo_jobs import _is_chain, _usable, _neighbors

STYLE_REF_COUNT = 3


VIDEO_MIN_SEC = 4  # план-видео 4–8 с (у seedance меньше 4 не бывает)
VIDEO_MAX_SEC = 8


STYLE_BASE = {
    "anime": "2D anime / toon style, clean line art, cel shading, flat colors, expressive faces",
    "photoreal": "photorealistic, cinematic lighting, natural skin texture, 35mm film still",
}
NO_TEXT = "No text, no letters, no captions, no watermark, no logo."
REF_ASPECT = {"style": "9:16", "location": "9:16", "turnaround": "16:9", "expressions": "3:2", "look": "16:9",
              "keyframe": "9:16", "endframe": "9:16"}


FRAME_KINDS = ("keyframe", "endframe")
# Видео: ограничения, которые держат физику (docs/design/ai-video-models-2026-11.md §«Изменения»).
VIDEO_NEGATIVE = ("cuts, scene change, transitions, second shot, duplicate objects, extra characters, extra robots, "
                  "floating objects, objects appearing or vanishing, morphing, characters changing design, humanoid "
                  "version of a non-humanoid character, text, captions, watermark")


# --------------------------------------------------------------------------- #
# План этапа: что генерировать и сколько стоит
# --------------------------------------------------------------------------- #


def _quality(store: Any, project_id: int, script_id: int | None) -> str:
    if script_id is not None:
        return V.get_script(store, script_id, with_shots=False)["quality"]
    return V._style_row(store, project_id)["quality"]


def _parse_item(item: Any) -> tuple[str, int, str, str]:
    """``"character:3"`` / ``{"target_type", "target_id", "kind", "slot"}`` → (тип, id, вид, слот)."""
    if isinstance(item, str):
        parts = item.split(":")
        kind = parts[2] if len(parts) > 2 else ""
        return parts[0], int(parts[1]) if len(parts) > 1 and parts[1] else 0, kind, parts[3] if len(parts) > 3 else ""
    if isinstance(item, dict):
        return (str(item.get("target_type") or item.get("type") or ""), int(item.get("target_id") or item.get("id") or 0),
                str(item.get("kind") or ""), str(item.get("slot") or ""))
    raise V.AivError(f"элемент этапа {item!r}: строка «тип:id[:вид]» или объект {{target_type, target_id, kind}}")


def _image_item(stage: str, target_type: str, target_id: int, ref_kind: str, label: str, model: str, quality: str,
                provider: str, slot: str = "") -> dict:
    resolution = RESOLUTION.get(quality, "1K")
    price = image_price(model, resolution, provider)
    return {"stage": stage, "kind": "image", "target_type": target_type, "target_id": int(target_id),
            "ref_kind": ref_kind, "slot": slot, "label": label, "provider": provider, "model": model,
            "resolution": resolution, "aspect": REF_ASPECT.get(ref_kind, "9:16"), "estimate_rub": price["rub"],
            "price_note": price["note"]}


# Персонаж не говорит (speech sfx): реплика — звук из пака freesound (ТОЛЬКО настоящие записи,
# никакой синтетики) по эмоции реплики. Свой звук персонажа — voice.sfx («sfx:freesound/<файл>»).
SFX_DEFAULT = "sfx:freesound/pop-soft.ogg"
SFX_BY_EMOTION: list[tuple[tuple[str, ...], str]] = [
    (("sad", "груст", "печал", "tired", "устал", "disappoint", "разочар", "fail", "провал"),
     "sfx:freesound/sad-trombone.ogg"),
    (("angry", "зл", "раздраж", "annoy", "furious", "ярост", "возмущ"), "sfx:freesound/impact-low.ogg"),
    (("scared", "fear", "испуг", "страх", "нерв", "тревог", "panic", "паник"), "sfx:freesound/sting.ogg"),
    (("surpris", "удив", "shock", "шок", "изумл"), "sfx:freesound/whip.ogg"),
    (("happy", "рад", "joy", "весел", "excit", "восторг", "laugh", "смех", "смеш"), "sfx:freesound/pop.ogg"),
]


def sfx_for(character: dict | None, emotion: str = "") -> str:
    """Звук реплики персонажа-«пищалки»: его собственный (voice.sfx) или по эмоции, иначе мягкий поп."""
    own = ((character or {}).get("voice") or {}).get("sfx")
    if own:
        return str(own)
    text = str(emotion or "").casefold().replace("ё", "е")
    for words, ref in SFX_BY_EMOTION:
        if any(w in text for w in words):
            return ref
    return SFX_DEFAULT


def speech_of(store: Any, project_id: int, who: str) -> tuple[dict | None, str]:
    character = V.character_by_slug(store, project_id, who) if who and who != "narrator" else None
    return character, (character or {}).get("speech") or "voice"


def _voice_of(store: Any, project_id: int, who: str, emotion: str = "") -> dict:
    style = V._style_row(store, project_id)
    character, speech = speech_of(store, project_id, who)
    if speech == "sfx":
        return {"provider": "none", "model": "sfx", "voice": sfx_for(character, emotion)}
    voice = dict((character or {}).get("voice") or {})
    provider = "none" if style["provider"] == "none" else (voice.get("provider") or "polza")
    if provider == "gemini":  # бесплатный Gemini TTS: свои модель и голос по умолчанию
        out = {"provider": provider, "model": voice.get("model") or settings.gemini_tts_model,
               "voice": voice.get("voice") or settings.gemini_tts_voice}
        return {**out, "style": voice["style"]} if voice.get("style") else out
    return {"provider": provider, "model": voice.get("model") or settings.polza_tts_model,
            "voice": voice.get("voice") or settings.polza_tts_voice}


def _line_key(voice: dict, text: str) -> str:
    key = f"{voice['provider']}|{voice['model']}|{voice['voice']}|{text}"
    return f"{key}|{voice['style']}" if voice.get("style") else key  # без style — как раньше


def _paid(provider: str, kind: str = "") -> bool:
    """Платит ли запуск деньгами: реплики — по провайдеру TTS (gemini и none бесплатны),
    картинки и видео — всё, кроме сухого прогона."""
    if kind == "tts":
        from app.aivideo_providers import tts_paid

        return tts_paid(provider)
    return provider != "none"


def speech_need_sec(shot: dict) -> float:
    """Сколько секунд нужно плану под его реплики (~14 символов/с + вход/хвост)."""
    chars = sum(len(d.get("text") or "") for d in shot.get("dialogue") or [])
    if not chars:
        return 0.0
    return round(chars / V.CHARS_PER_SEC + 0.35 + 0.45 + 0.2 * (len(shot["dialogue"]) - 1), 2)


def video_seconds(shot: dict, model: str) -> int:
    """Длина видео плана, целые секунды 4–8: длительность плана, но не короче его реплик."""
    import math

    lo, hi = video_seconds_range(model)
    want = max(float(shot.get("duration") or 4), speech_need_sec(shot))
    return int(min(VIDEO_MAX_SEC, hi, max(VIDEO_MIN_SEC, lo, math.ceil(want - 0.05))))


def _video_item(shot: dict, script_id: int, provider: str, model: str, resolution: str, start: str) -> dict:
    seconds = video_seconds(shot, model)
    price = video_price(model, resolution, seconds, provider)
    return {"stage": "video", "kind": "video", "target_type": "shot", "target_id": int(shot["id"]),
            "ref_kind": "shot_video", "slot": "", "script_id": int(script_id),
            "label": f"план {shot['idx']}: видео {seconds} с", "provider": provider, "model": model,
            "resolution": resolution, "aspect": "9:16", "seconds": seconds, "start": start,
            "estimate_rub": price["rub"], "price_note": price["note"]}


TAIL_SOURCE = "source=video_tail"  # пометка версии keyframe: старт — последний кадр готового видео прошлого плана


def series_video_model(store: Any, project_id: int, script_id: int | None) -> tuple[str, str]:
    """(видеомодель, провайдер видео) серии."""
    style = V.get_style(store, project_id)
    quality = V.get_script(store, int(script_id), with_shots=False)["quality"] if script_id is not None else None
    return video_model_for(style, quality), style["video_provider"]


def tail_mode(store: Any, project_id: int, script_id: int | None) -> bool:
    """Видеомодель серии НЕ берёт конечный кадр (H3, Grok — только старт): видео плана к нарисованному
    концу не приходит, поэтому старт продолжения — ПОСЛЕДНИЙ кадр готового видео прошлого плана, а
    конечные кадры не заказываются (деньги зря)."""
    model, provider = series_video_model(store, project_id, script_id)
    return not video_capabilities(model, provider).get("first_last")


def _tail_marker(video_ref: dict) -> str:
    return f"{TAIL_SOURCE}; video_ref={int(video_ref['id'])}"


def tail_start_ref(store: Any, project_id: int, prev: dict, shot: dict) -> dict | None:
    """Старт сцепленного плана из хвоста видео прошлого: версия keyframe с пометкой video_tail ИМЕННО
    от выбранной сейчас версии видео прошлого плана (новое видео — старый хвост не годится)."""
    video = _usable(V.selected_ref(store, project_id, "shot", int(prev["id"]), "shot_video"))
    if video is None:
        return None
    marker = _tail_marker(video)
    for ref in reversed(V.list_refs(store, project_id, "shot", int(shot["id"]), "keyframe")):
        if ref.get("prompt") == marker and ref.get("asset_id") and _asset_path(store, ref) is not None:
            if not ref["selected"]:
                ref = V.select_ref(store, int(ref["id"]))
            return ref
    return None


def make_tail_start(store: Any, project_id: int, prev: dict, shot: dict, job: dict | None = None) -> dict | None:
    """Вынуть последний кадр готового видео прошлого плана (ffmpeg) → версия keyframe плана ``shot``
    с пометкой ``source=video_tail``. Уже есть от этого же видео — она. Видео нет — None."""
    have = tail_start_ref(store, project_id, prev, shot)
    if have is not None:
        return have
    video = _usable(V.selected_ref(store, project_id, "shot", int(prev["id"]), "shot_video"))
    frame = video_last_frame(store, video) if video else None
    if frame is None:
        return None
    from app.montage_assets import assets_dir, probe_asset

    dest = assets_dir() / f"{uuid4().hex}-aiv-keyframe-tail.jpg"
    shutil.copyfile(frame, dest)
    meta = probe_asset(dest)
    asset = store.create_montage_asset(
        kind="image", label=f"ИИ-видео: план {shot['idx']} — старт из хвоста видео плана {prev['idx']}"[:120],
        description=(f"ИИ-видео: старт плана {shot['idx']} — последний кадр видео плана {prev['idx']} "
                     f"({TAIL_SOURCE})")[:500],
        tags="ии-видео", file_path=str(dest), original_filename=dest.name, mime_type="image/jpeg",
        size_bytes=dest.stat().st_size, project_id=int(project_id), **meta)
    ref = V.add_ref(store, project_id, "shot", int(shot["id"]), "keyframe", int(asset["id"]),
                    prompt=_tail_marker(video), job_id=(job or {}).get("id"))
    # Новый старт — видео этого плана (если было) стоит на старом старте: устарело.
    store.db.execute("UPDATE aiv_refs SET stale = 1 WHERE target_type = 'shot' AND target_id = ? AND kind = "
                     "'shot_video'", (int(shot["id"]),))
    return ref


def start_frame_ref(store: Any, project_id: int, shots: list[dict], n: int) -> tuple[dict | None, int | None]:
    """Стартовый кадр плана ``shots[n]`` и откуда он: (версия, idx плана, чей это КОНЕЦ — или None,
    если кадр свой). Сцепленный план берёт конечный кадр прошлого плана — тот же файл; если видеомодель
    серии конечный кадр не берёт — последний кадр готового видео прошлого плана (``source=video_tail``)."""
    shot = shots[n]
    prev = shots[n - 1] if n else None
    if _is_chain(shot, prev):
        if tail_mode(store, project_id, shot.get("script_id")):
            return tail_start_ref(store, project_id, prev, shot), int(prev["idx"])
        return _usable(V.selected_ref(store, project_id, "shot", int(prev["id"]), "endframe")), int(prev["idx"])
    return _usable(V.selected_ref(store, project_id, "shot", int(shot["id"]), "keyframe")), None


def end_frame_ref(store: Any, project_id: int, shot: dict) -> dict | None:
    """Конечный кадр плана с видео (у статичного — нет: его кадр не меняется)."""
    if not shot.get("needs_motion"):
        return None
    return _usable(V.selected_ref(store, project_id, "shot", int(shot["id"]), "endframe"))


def _share_item(shot: dict, prev: dict, script_id: int) -> dict:
    return {"stage": "keyframes", "kind": "share", "target_type": "shot", "target_id": int(shot["id"]),
            "ref_kind": "keyframe", "slot": "", "script_id": int(script_id),
            "label": f"план {shot['idx']}: старт — конечный кадр плана {prev['idx']}", "provider": "none",
            "model": "", "estimate_rub": 0.0, "price_note": "0 ₽ — общий кадр"}


def video_model_for(style: dict, quality: str | None) -> str:
    """Видеомодель серии: заданная в проекте, иначе AIVIDEO_VIDEO_MODEL, иначе по режиму серии."""
    if style.get("video_model_set"):
        return style["video_model"]
    return settings.aivideo_video_model or default_video_model(quality)


def _video_busy(store: Any, shot_id: int) -> bool:
    marks = ", ".join("?" for _ in BUSY)
    return bool(store.db.query_one(
        f"SELECT id FROM aiv_gen_jobs WHERE kind = 'video' AND target_type = 'shot' AND target_id = ? "
        f"AND status IN ({marks}) LIMIT 1", (int(shot_id), *BUSY)))


# --------------------------------------------------------------------------- #
# Запрос этапа → карточка владельцу
# --------------------------------------------------------------------------- #


# Запуск на цели «занят»: ждёт одобрения, одобрен/держится или идёт у провайдера.
BUSY = ("awaiting", "held") + ACTIVE


# --------------------------------------------------------------------------- #
# Промпты и референсы
# --------------------------------------------------------------------------- #


def _style_text(style: dict) -> str:
    parts = [STYLE_BASE.get(style["style_kind"], STYLE_BASE["anime"])]
    if style.get("passport_text"):
        parts.append(f"Style passport (verbatim): {style['passport_text']}")
    if style.get("palette"):
        parts.append("Palette: " + ", ".join(style["palette"]))
    return ". ".join(parts)


def _asset_path(store: Any, ref: dict | None) -> Path | None:
    if not ref or not ref.get("asset_id"):
        return None
    try:
        path = Path(store.get_montage_asset(int(ref["asset_id"]))["file_path"])
    except KeyError:
        return None
    return path if path.is_file() else None


def _canon_refs(store: Any, project_id: int, character: dict) -> list[Path]:
    """Канон персонажа: картинки владельца (≤2) + выбранный разворот."""
    paths = []
    uploads = [r for r in V.list_refs(store, project_id, "character", character["id"], "upload")][-2:]
    for ref in uploads:
        p = _asset_path(store, ref)
        if p:
            paths.append(p)
    turn = V.selected_ref(store, project_id, "character", character["id"], "turnaround")
    p = _asset_path(store, turn)
    if p:
        paths.append(p)
    return paths


def video_last_frame(store: Any, ref: dict | None) -> Path | None:
    """Последний кадр видео-версии (кэш рядом с файлом видео). Конвейер v3 его НЕ использует (старт
    следующего плана — общий конечный кадр); оставлено для ручной сверки агентом."""
    from app.aivideo_providers import last_frame

    src = _asset_path(store, ref)
    if src is None:
        return None
    dest = src.with_name(f"{src.stem}.last.jpg")
    if not dest.is_file():
        try:
            last_frame(src, dest)
        except ProviderError:
            return None
    return dest


def _prev_frame(store: Any, project_id: int, prev: dict | None) -> tuple[Path | None, str]:
    """Последний кадр прошлого плана: его конечный кадр (у плана с видео), иначе его стартовый."""
    if not prev:
        return None, ""
    path = _asset_path(store, end_frame_ref(store, project_id, prev))
    if path:
        return path, "end frame"
    shots = V.list_shots(store, int(prev["script_id"]))
    n = next((i for i, s in enumerate(shots) if int(s["id"]) == int(prev["id"])), None)
    if n is None:
        return None, ""
    path = _asset_path(store, start_frame_ref(store, project_id, shots, n)[0])
    return (path, "keyframe") if path else (None, "")


def _style_paths(store: Any, project_id: int, limit: int = 1) -> list[Path]:
    paths = []
    for ref in V.list_refs(store, project_id, "style", 0):
        if ref["kind"] in ("style", "upload") and (ref["selected"] or ref["kind"] == "upload"):
            p = _asset_path(store, ref)
            if p:
                paths.append(p)
    return paths[:limit]


def _identity(character: dict) -> str:
    out = f"«{character['name']}» — identity card (verbatim, do not alter): {character['identity_card']}"
    if character.get("constraints"):
        out += f". Physical constraints (must always hold): {character['constraints']}"
    return out


PHYSICS = ("Physically plausible: every character and object rests on the floor or on furniture (nothing floats "
           "unless it is held), nothing is duplicated, no extra copies of any character or prop.")
LOCATION_PLATE_HINT = ("Reference {k} is the background plate of «{name}»: this exact room in every shot — same layout, "
                       "walls, floor, furniture and their positions, windows, lighting and colors; only the camera "
                       "angle and framing may differ, never redesign the room.")
LOCATION_VIDEO_HINT = "The background stays this exact location for the whole shot: the room never changes or morphs."


def shot_location(store: Any, project_id: int, shot: dict | None) -> dict | None:
    slug = (shot or {}).get("location") or ""
    return V.location_by_slug(store, project_id, slug) if slug else None


def location_text(loc: dict) -> str:
    """Описание локации — ОДНО и то же дословно во всех планах (кадры и видео): комната не «плывёт»."""
    return f"Location «{loc['name']}» (the same place in every shot set here, verbatim): {loc['description']}."


def location_plate(store: Any, project_id: int, loc: dict | None) -> Path | None:
    """Плита локации (выбранная версия) — общий референс фона всех её планов."""
    if not loc:
        return None
    return _asset_path(store, V.selected_ref(store, project_id, "location", int(loc["id"]), "location"))


def continuity_shot(shots: list[dict], n: int) -> tuple[dict | None, bool]:
    """План, чей кадр — референс непрерывности для старта ``shots[n]``: прошлый, если он в той же
    локации (``True`` — соседний); иначе последний раньше план этой же локации (``False`` — только
    комната); нет такого — (None, False). Кадр из другой локации не берём: он тянет чужой фон."""
    if n <= 0:
        return None, False
    here = shots[n].get("location") or ""
    prev = shots[n - 1]
    if (prev.get("location") or "") == here:
        return prev, True
    if not here:
        return None, False
    for k in range(n - 2, -1, -1):
        if (shots[k].get("location") or "") == here:
            return shots[k], False
    return None, False


def _frame_request(store: Any, job: dict, st: str, neg: str) -> tuple[str, list[Path], list[str], dict]:
    """Ключевой кадр плана: старт (``keyframe``) или конец (``endframe``). Референсы по важности
    (лимит модели — режем с конца): блокинг этого момента (композиция, позы, места, ракурс) → для
    конца — старт этого же плана, для старта — последний кадр прошлого плана → ВСЕ листы персонажей
    (разворот + эмоции) и облик → плита локации → картинки владельца → стиль."""
    from app import aivideo_blocking as B

    project_id = int(job["project_id"])
    which = "end" if job["ref_kind"] == "endframe" else "start"
    shot = V.get_shot(store, int(job["target_id"]))
    shots = V.list_shots(store, int(shot["script_id"]))
    n = next(i for i, s in enumerate(shots) if int(s["id"]) == int(shot["id"]))
    prev = shots[n - 1] if n else None
    nxt = shots[n + 1] if n + 1 < len(shots) else None
    cam = shot["camera"]
    missing: list[str] = []
    ranked: list[tuple[int, Path, str, str]] = []  # (важность, файл, роль для журнала, роль для модели)
    word = "LAST" if which == "end" else "FIRST"
    parts = [f"{st}. Vertical 9:16 film still — the {word} frame of shot {shot['idx']} (one continuous shot)."]
    block = _asset_path(store, V.selected_ref(store, project_id, "shot", int(shot["id"]), f"blocking_{which}"))
    blocking_info: dict[str, Any] = {"used": False}
    if block is not None:
        # Самоконтроль кадра макета: персонаж не в кадре/закрыт, вещь перед камерой на полкадра —
        # такой макет модель скопирует (оранжевая «дверь» в кадре); тогда кадр рисуется без него.
        check = B.frame_check(store, shot, which)
        if not check.get("ok"):
            reason = "; ".join(check.get("problems") or ["не прошёл самоконтроль"])[:300]
            blocking_info = {"used": False, "rejected": reason}
            missing.append(f"блокинг {which} отклонён самоконтролем: {reason}")
            block = None
    if block is not None:
        blocking_info = {"used": True}
        ranked.append((-3, block, f"блокинг: {'конец' if which == 'end' else 'старт'}",
                       "3D blocking mock-up of this exact moment (positions and camera only)"))
        parts.append("The 3D BLOCKING mock-up shows this exact moment. Use it ONLY for the camera angle and framing, "
                     "where each character is, their pose and the direction they face, and where furniture and props "
                     "stand. Do NOT copy its look: its simple shapes, plain flat colors and materials are placeholders "
                     "— draw the room exactly like the background plate and each character exactly from their sheets; "
                     "never draw mannequins, plain boxes, columns or the mock-up's colors. Its stand-ins: "
                     f"{B.legend(store, project_id, shot) or 'no characters'}.")
    elif not blocking_info.get("rejected"):
        missing.append(f"блокинг {which} (этап blocking, 0 ₽)")
    if which == "end":
        start, shared = start_frame_ref(store, project_id, shots, n)
        start_path = _asset_path(store, start)
        if start_path is not None:
            ranked.append((-2, start_path, f"план {shot['idx']}: старт" + (f" (конец плана {shared})" if shared else ""),
                           f"START frame of this same shot {shot['idx']}"))
            parts.append("The START frame of this same shot is given: keep exactly the same camera, background, "
                         "lighting, character designs, clothing and props; only what the action changes may differ.")
        else:
            missing.append(f"план {shot['idx']}: стартовый кадр")
        state = V.end_state_of(shot, nxt)
        if shot.get("action"):
            parts.append(f"This frame shows the RESULT of the single action «{shot['action']}» — the moment it ends.")
        text = V.state_text(store, project_id, state)
        if text:
            parts.append(f"Continuity state at the END of this shot (must match exactly): {text}.")
    else:
        # Непрерывность — только кадром ТОЙ ЖЕ сцены (той же локации): кадр из другой комнаты тянет
        # чужой фон. Прошлый план в другой локации — ищем последний план этой же локации раньше.
        cont, adjacent = continuity_shot(shots, n)
        prev_frame, prev_kind = _prev_frame(store, project_id, cont)
        if prev_frame is not None and adjacent:
            what = "конечный кадр" if prev_kind == "end frame" else "кадр"
            ranked.append((-1, prev_frame, f"план {cont['idx']}: {what}",
                           f"{prev_kind} of the previous shot {cont['idx']} (continuity)"))
            parts.append(f"A reference image is the {prev_kind} of the previous shot {cont['idx']}: keep the same "
                         "characters, faces, clothing, props, room, lighting and color continuous with it"
                         + (" — this shot continues the same scene." if shot.get("continues_from_prev") else
                            " (new angle or moment, same scene and the same room)."))
        elif prev_frame is not None:
            what = "конечный кадр" if prev_kind == "end frame" else "кадр"
            ranked.append((-1, prev_frame, f"план {cont['idx']}: {what} (та же локация)",
                           f"{prev_kind} of the earlier shot {cont['idx']} in this same location (room continuity)"))
            parts.append(f"A reference image is the {prev_kind} of the earlier shot {cont['idx']} in this same "
                         "location: the room must be exactly the same — layout, walls, furniture, windows, lighting "
                         "and colors; characters follow this shot's own state.")
        state = shot.get("state") or {}
        text = V.state_text(store, project_id, state)
        if text:
            parts.append(f"Continuity state at the start of this shot (must match exactly): {text}.")
        if shot.get("action"):
            parts.append(f"Action: {shot['action']}.")
    framing = ", ".join(x for x in (str(cam.get("shot") or "medium").replace("_", " ") + " shot",
                                    f"{cam['angle']} angle" if cam.get("angle") else "",
                                    f"{cam['lens']}mm lens" if cam.get("lens") else "") if x)
    parts.append(f"Camera: {framing}.")
    loc = shot_location(store, project_id, shot)
    caption = [f"план {shot['idx']}" + (": конец" if which == "end" else "")]
    plate = None
    if loc:
        # Одно и то же описание локации ДОСЛОВНО в каждом плане этой локации + её плита референсом
        # (сразу после кадров композиции и непрерывности — в лимит модели влезает всегда).
        parts.append(location_text(loc))
        plate = location_plate(store, project_id, loc)
        if plate:
            ranked.append((-0.5, plate, f"плита локации «{loc['name']}»", f"background plate of «{loc['name']}»"))
        else:
            missing.append(f"плита локации «{loc['name']}»")
    names = []
    for member in shot["cast"]:
        c = V.character_by_slug(store, project_id, member["id"])
        if not c:
            continue
        names.append(c["name"])
        text = _identity(c)
        look = next((l for l in c["looks"] if l["slug"] == member.get("look")), None)
        for sheet, label, role in (("turnaround", "разворот", "turnaround model sheet"),
                                   ("expressions", "эмоции", "expression sheet")):
            p = _asset_path(store, V.selected_ref(store, project_id, "character", c["id"], sheet))
            if p:
                ranked.append((0, p, f"{c['slug']}: {label}", f"{c['name']} — {role}"))
            else:
                missing.append(f"{c['slug']}: {label}")
        if look:
            text += f"; outfit/state: {look['outfit_text']}"
            lp = _asset_path(store, V.selected_ref(store, project_id, "look", look["id"], "look"))
            if lp:
                ranked.append((0, lp, f"{c['slug']}: облик {look['slug']}", f"{c['name']} — outfit «{look['name']}»"))
            else:
                missing.append(f"{c['slug']}: облик {look['slug']}")
        for up in [r for r in V.list_refs(store, project_id, "character", c["id"], "upload")][-2:]:
            p = _asset_path(store, up)
            if p:
                ranked.append((2, p, f"{c['slug']}: картинка владельца", f"{c['name']} — owner's reference"))
        if member.get("action"):
            text += f"; doing: {member['action']}"
        parts.append(text + f". Exactly one {c['name']} in the frame.")
    for p in _style_paths(store, project_id, 1):
        ranked.append((3, p, "стиль", "style reference"))
    ranked.sort(key=lambda r: r[0])  # устойчиво: порядок персонажей в кадре сохраняется
    seen: set[str] = set()
    chosen = []
    for item in ranked:
        if str(item[1]) not in seen:
            seen.add(str(item[1]))
            chosen.append(item)
    limit = max_refs(job["model"], job["provider"])
    dropped = [r[2] for r in chosen[limit:]]
    chosen = chosen[:limit]
    if chosen:
        parts.append("Reference images in order: " + "; ".join(f"{k} — {r[3]}" for k, r in enumerate(chosen, 1))
                     + ". Draw every character EXACTLY as in their sheets: same face, body, shape, colors and "
                       "details — not a different model or design.")
    if dropped:
        missing += [f"не влезло в лимит {limit}: {d}" for d in dropped]
    if plate is not None and any(r[1] == plate for r in chosen):
        k = next(i for i, r in enumerate(chosen, 1) if r[1] == plate)
        parts.append(f"{LOCATION_PLATE_HINT.format(k=k, name=loc['name'])}")
    props = V.prop_counts(state)
    if props:
        parts.append(f"Props: {props}.")
    emotions = ", ".join(d["emotion"] for d in shot["dialogue"] if d.get("emotion"))
    if emotions:
        parts.append(f"Emotion: {emotions}.")
    if shot["image_prompt"] and which == "start":
        parts.append(shot["image_prompt"])
    count = len(names)
    parts.append(f"Exactly {count} character{'s' if count != 1 else ''} in frame, no extra people. "
                 if count else "No people in frame. ")
    parts.append(PHYSICS + " " + NO_TEXT + neg)
    caption = [*caption, *(names or ["без персонажей"]), *([loc["name"]] if loc else [])]
    return " ".join(parts), [r[1] for r in chosen], caption, {"roles": [r[2] for r in chosen], "missing": missing,
                                                              "blocking": blocking_info}


PLATE_AVOID = ("people, person, human, girl, boy, woman, man, child, animal, pet, cat, dog, bird, robot, robot vacuum, "
               "vacuum cleaner robot, droid, mascot, character, creature, mannequin, doll, figure, silhouette, shadow of a "
               "person, reflection of a person, face")


def plate_prompt(store: Any, project_id: int, loc: dict, st: str, neg: str = "") -> str:
    """Плита локации — ПУСТАЯ комната: ни людей, ни животных, ни роботов, ни персонажей проекта и
    ничего на них похожего (иначе плита с пылесосом — и в кадрах вместо Бипа серый пылесос)."""
    cast = []
    for c in V.list_characters(store, project_id):
        card = re.split(r"[;.]", str(c.get("identity_card") or ""))[0].strip()[:90]
        cast.append(f"{c['name']} ({card})" if card else c["name"])
    who = (f" None of the series characters is here — not {', not '.join(cast)} — and nothing that looks like "
           "any of them." if cast else "")
    extra = f" Avoid: {PLATE_AVOID}" + (f", {neg.strip()[len('Avoid: '):].rstrip('.')}" if neg.strip().startswith(
        "Avoid: ") else "") + "."
    return (f"{st}. Empty background plate of the location: the empty room only, vertical 9:16 composition. NO people, "
            f"NO animals, NO robots, NO characters of any kind, no mannequins or figures — furniture and objects "
            f"only.{who} If the description names a character's thing (a charging dock, a pet bed, a bowl), draw "
            f"only that thing, empty — its owner is not in the room. Location «{loc['name']}»: {loc['description']}. "
            f"Consistent with the style reference. {NO_TEXT}{extra}")


def build_request(store: Any, job: dict) -> tuple[str, list[Path], list[str], dict]:
    """(промпт, файлы-референсы, подпись заглушки, {roles — что за референс по порядку, missing —
    чего не хватило}) запуска. Карта личности и физические ограничения — дословно."""
    ext = _ext_of_kind(job["ref_kind"])
    if ext is not None:
        return ext.build(store, job)
    project_id = int(job["project_id"])
    style = V.get_style(store, project_id)
    st = _style_text(style)
    neg = f" Avoid: {style['negative_text']}." if style.get("negative_text") else ""
    kind, target_id = job["ref_kind"], int(job["target_id"])
    if kind in FRAME_KINDS:
        return _frame_request(store, job, st, neg)
    refs: list[Path] = []
    roles: list[str] | None = None
    missing: list[str] = []
    caption = [REF_LABELS.get(kind, kind)]
    if kind == "style":
        project = P.get_project(store, project_id, include_sources=False)
        about = project.get("description") or project["name"]
        prompt = (f"{st}. Mood board image {job['slot'] or 1} of {STYLE_REF_COUNT} for an animated short series "
                  f"«{project['name']}»: {about}. Show the world, light and color mood. {NO_TEXT}{neg}")
        refs = [p for p in (_asset_path(store, r) for r in V.list_refs(store, project_id, "style", 0, "upload")) if p]
        caption += [project["name"], f"настроение {job['slot']}"]
    elif kind == "location":
        loc = V.get_location(store, target_id)
        prompt = plate_prompt(store, project_id, loc, st, neg)
        refs = _style_paths(store, project_id, 2)
        refs += [p for p in (_asset_path(store, r) for r in V.list_refs(store, project_id, "location", target_id,
                                                                         "upload")) if p]
        caption += [loc["name"]]
    elif kind in ("turnaround", "expressions"):
        c = V.get_character(store, target_id)
        uploads = [p for p in (_asset_path(store, r) for r in
                               V.list_refs(store, project_id, "character", target_id, "upload")) if p][-3:]
        if kind == "turnaround":
            prompt = (f"{st}. Character turnaround model sheet on a neutral light-grey background: front, three-quarter, "
                      f"side profile and back views, full body, the same character in every view, consistent "
                      f"proportions and colors. {_identity(c)}. {NO_TEXT}{neg}")
            refs = uploads + _style_paths(store, project_id, 1)
        else:
            prompt = (f"{st}. Expression sheet of the same character: six head-and-shoulders portraits in a 3x2 grid — "
                      f"neutral, happy, sad, angry, surprised, tired. {_identity(c)}. Keep the face, hair and colors "
                      f"exactly as in the reference sheet. {NO_TEXT}{neg}")
            refs = _canon_refs(store, project_id, c)
        caption += [c["name"]]
    elif kind == "look":
        # Облик серии — img2img-правка БАЗОВОГО листа (разворот первым референсом): та же раскладка
        # листа, то же лицо и тело, меняется только одежда/состояние.
        look = V.get_look(store, target_id)
        c = V.get_character(store, look["character_id"])
        prompt = (f"{st}. img2img edit of the FIRST reference image (the character's base turnaround model sheet): "
                  f"the same sheet layout (front, three-quarter, side profile and back views, full body, neutral "
                  f"light-grey background) and the SAME character — identical face, hair, body proportions, colors "
                  f"and design. Change ONLY the outfit/state: {look['outfit_text']}. {_identity(c)}. {NO_TEXT}{neg}")
        canon = _canon_refs(store, project_id, c)
        turn = _asset_path(store, V.selected_ref(store, project_id, "character", c["id"], "turnaround"))
        refs = ([turn] if turn else []) + [p for p in canon if p != turn]  # разворот — первым (база правки)
        roles = [("база: разворот" if p == turn else "картинка владельца") for p in refs]
        caption += [c["name"], look["name"]]
    else:
        raise V.AivError(f"неизвестный вид картинки {kind}")
    seen: set[str] = set()
    uniq = []
    kept_roles = []
    for i, p in enumerate(refs):
        key = str(p)
        if key not in seen:
            seen.add(key)
            uniq.append(p)
            kept_roles.append(roles[i] if roles else p.name)
    limit = max_refs(job["model"], job["provider"])
    return prompt, uniq[:limit], caption, {"roles": kept_roles[:limit], "missing": missing}


def _file_digest(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def input_hash(model: str, params: dict, prompt: str, refs: list[Path], variant: int = 0) -> str:
    h = hashlib.sha256()
    h.update(model.encode("utf-8"))
    h.update(json.dumps({k: v for k, v in params.items() if not k.startswith("_")}, sort_keys=True).encode("utf-8"))
    h.update(prompt.encode("utf-8"))
    for p in refs:
        h.update(_file_digest(p).encode("ascii"))
    h.update(str(variant).encode("ascii"))
    return h.hexdigest()


def build_video_prompt(store: Any, job: dict, shot: dict | None = None, mode: str = "start") -> str:
    """Промпт видео: ОДИН непрерывный план без склеек, одно простое действие, спокойная камера,
    персонажи по картам личности с физическими ограничениями («ровно один», «на полу»), счёт
    реквизита, состояние на старте и на конце (в режиме ``first_last`` модель интерполирует к
    данному конечному кадру), звук без речи. Негатив — отдельно (``VIDEO_NEGATIVE``: полем или
    блоком «Avoid» в ``video_params``)."""
    shot = shot or V.get_shot(store, int(job["target_id"]))
    project_id = int(job["project_id"])
    style = V.get_style(store, project_id)
    _prev, nxt = _neighbors(store, shot)
    parts = [f"{STYLE_BASE.get(style['style_kind'], STYLE_BASE['anime'])} animation, vertical 9:16.",
             "Single continuous shot, one take, no cuts, no scene change, no transitions, no new characters."]
    if mode == "first_last":
        parts.append("The FIRST and the LAST frame are given: animate smoothly from the first frame to the last "
                     "frame; the final frame must match the given last frame exactly (same positions, poses, props).")
    else:
        parts.append("Start exactly from the given first frame: same characters, faces, clothing, props, lighting "
                     "and background.")
    if style.get("passport_text"):
        parts.append(f"Style: {style['passport_text']}.")
    loc = shot_location(store, project_id, shot)
    if loc:  # то же описание, что в промптах ключевых кадров этой локации — дословно
        parts.append(f"{location_text(loc)} {LOCATION_VIDEO_HINT}")
    move = (shot.get("camera") or {}).get("move") or "static"
    parts.append(f"Camera: {GENTLE_CAMERA.get(move, 'locked-off camera')}.")
    if shot.get("action"):
        parts.append(f"Exactly one simple action in this shot: {shot['action']}.")
    for member in shot.get("cast") or []:
        c = V.character_by_slug(store, project_id, member["id"])
        if not c:
            continue
        text = f"{c['name']}: {c['identity_card']}"
        if c.get("constraints"):
            text += f"; physical constraints (always): {c['constraints']}"
        if member.get("action"):
            text += f"; does: {member['action']}"
        parts.append(text + f"; exactly one {c['name']} in frame.")
    if shot.get("motion_prompt"):
        parts.append(f"Motion: {shot['motion_prompt']}.")
    start_state = V.state_text(store, project_id, shot.get("state") or {})
    if start_state:
        parts.append(f"At the start: {start_state}.")
    end = V.end_state_of(shot, nxt)
    end_state = V.state_text(store, project_id, end)
    if end_state:
        parts.append(f"At the end of the shot: {end_state}.")
    props = V.prop_counts(shot.get("state") or {}) or V.prop_counts(end)
    if props:
        parts.append(f"Props: {props} — the count never changes.")
    count = len(shot.get("cast") or [])
    parts.append((f"Exactly {count} character{'s' if count != 1 else ''} in frame. " if count else "")
                 + "Gravity and contact: characters and objects stay on the floor or furniture, nothing floats or "
                   "duplicates.")
    talkers = [d for d in shot.get("dialogue") or [] if speech_of(store, project_id, d["who"])[1] == "voice"]
    if talkers:
        emotions = ", ".join(d["emotion"] for d in talkers if d.get("emotion"))
        parts.append("The character talks" + (f" ({emotions})" if emotions else "") + ", natural mouth movement.")
    sound = shot.get("sound") or ""
    parts.append((f"Sound: {sound}. " if sound else "Sound: natural ambient room tone and effects of the action. ")
                 + "No music, no speech, no voice-over.")
    parts.append(NO_TEXT)
    return " ".join(parts)[:3600]


def video_negative(style: dict) -> str:
    extra = (style.get("negative_text") or "").strip().rstrip(".")
    return VIDEO_NEGATIVE + (f", {extra}" if extra else "")


GENTLE_CAMERA = {"static": "locked-off static camera", "push_in": "very slow subtle push-in",
                 "pull_out": "very slow subtle pull-out", "pan_left": "very slow gentle pan left",
                 "pan_right": "very slow gentle pan right", "tilt_up": "very slow gentle tilt up",
                 "tilt_down": "very slow gentle tilt down", "parallax_push": "very slow subtle push-in",
                 "orbit": "locked-off camera with a very slow subtle push-in", "float": "locked-off camera",
                 "dolly_zoom": "locked-off camera with a very slow subtle push-in",
                 "tilt_reveal": "very slow gentle tilt up"}
CAMERA_TEXT = GENTLE_CAMERA  # прежнее имя


def _video_takes_refs(model: str) -> bool:
    """Берёт ли модель листы персонажей ВМЕСТЕ с кадрами (не взаимоисключающие режимы)."""
    caps = video_capabilities(model)
    return bool(caps.get("ref_images")) and not caps.get("exclusive", True)


def _cast_sheets(store: Any, project_id: int, shot: dict, limit: int = 8) -> list[Path]:
    """Листы (разворот, эмоции) персонажей плана — для моделей, что берут их вместе с кадрами."""
    out: list[Path] = []
    if limit <= 0:
        return out
    for member in shot.get("cast") or []:
        c = V.character_by_slug(store, project_id, member["id"])
        if not c:
            continue
        for sheet in ("turnaround", "expressions"):
            p = _asset_path(store, V.selected_ref(store, project_id, "character", c["id"], sheet))
            if p and p not in out:
                out.append(p)
    return out[:limit]
