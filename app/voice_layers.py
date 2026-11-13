"""Звуковые слои и файлы голоса: длина файла, роль, что написано в озвучке, обрезанные слои.

Эксперимент 12.11: слой ``audio`` с ``asset_id`` без ``end`` жил 3 с (``DEFAULT_LAYER_SEC``) — каждая фраза
озвучки обрывалась на 3 с, и ни ``add_layers``, ни ``lint_clip`` об этом не говорили. Теперь ``fill_audio_end`` ставит
``end`` по длине файла (до нормализации слоя — та не знает файлов), а ``clipped`` находит слои короче своего файла.

Общее у ``add_layers`` / ``update_layer`` (app/layer_tools.py), ``add_voiceover`` (app/voiceover.py) и анализатора
озвучки ``check_voiceover`` (app/voice_check.py).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

VOICE_TAGS = ("озвучка", "проба голоса", "голос", "tts")
CLIP_TOLERANCE_SEC = 0.15  # слой короче файла на столько — не обрезка (хвост тишины, округление)
TRACK_CLIP_WARN_SEC = 1.0  # дорожка (не голос) короче файла на столько — сказать
_PACK_SECONDS: dict[str, float | None] = {}


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def probe_seconds(path: Path | str) -> float | None:
    try:
        proc = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                               "default=nw=1:nk=1", str(path)], capture_output=True, text=True, timeout=60, check=False)
        value = float((proc.stdout or "").strip() or 0)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return value if value > 0 else None


def asset_row(store: Any, ref: Any) -> dict | None:
    """Файл библиотеки по asset_id (число или строка из цифр); пак (sfx:…) — None."""
    if isinstance(ref, bool) or ref in (None, ""):
        return None
    if isinstance(ref, str) and not ref.strip().isdigit():
        return None
    try:
        return store.get_montage_asset(int(ref))
    except (KeyError, TypeError, ValueError):
        return None


def asset_path(store: Any, ref: Any) -> Path | None:
    row = asset_row(store, ref)
    if row is not None:
        path = Path(row.get("file_path") or "")
        return path if row.get("file_path") and path.is_file() else None
    if isinstance(ref, str) and ref.strip():
        try:
            from app.motion_assets import resolve_pack_ref

            return resolve_pack_ref(ref.strip())
        except Exception:  # noqa: BLE001
            return None
    return None


def asset_seconds(store: Any, ref: Any) -> float | None:
    """Длина звука файла слоя (библиотека — из duration_sec, иначе ffprobe; пак — ffprobe с кэшем)."""
    row = asset_row(store, ref)
    if row is not None:
        sec = _num(row.get("duration_sec"))
        if sec > 0:
            return sec
        path = Path(row.get("file_path") or "")
        return probe_seconds(path) if row.get("file_path") and path.is_file() else None
    if not isinstance(ref, str) or not ref.strip():
        return None
    key = ref.strip()
    if key not in _PACK_SECONDS:
        path = asset_path(store, key)
        _PACK_SECONDS[key] = probe_seconds(path) if path is not None else None
    return _PACK_SECONDS[key]


def fill_audio_end(store: Any, item: dict) -> str | None:
    """Слой audio с файлом и без ``end``: конец = start + (длина файла − offset). Меняет ``item``; заметка или None.

    Раньше такой слой жил ``DEFAULT_LAYER_SEC`` (3 с) — озвучка обрывалась на полуслове."""
    if not isinstance(item, dict) or item.get("type") != "audio" or "end" in item:
        return None
    props = item.get("props") if isinstance(item.get("props"), dict) else {}
    sec = asset_seconds(store, props.get("asset_id"))
    if not sec:
        return None
    start = max(0.0, _num(item.get("start")))
    length = max(0.1, sec - max(0.0, _num(props.get("offset"))))
    item["end"] = round(start + length, 3)
    return f"{item.get('id') or 'audio'}: end не задан — по длине файла {length:.2f} с (до {item['end']:.2f} с)"


def layer_role(layer: dict, duration: float | None = None) -> str:
    from app.audio_mix_model import layer_role as role

    return role(layer, duration)


def looks_like_voice_asset(row: dict | None) -> bool:
    """Файл — озвучка (TTS сервиса, проба голоса, «голос» в тегах)."""
    if not row:
        return False
    tags = str(row.get("tags") or "").lower()
    name = str(row.get("original_filename") or "").lower()
    desc = str(row.get("description") or "")
    return (any(t in tags for t in VOICE_TAGS) or name.startswith("tts-") or "-vo-" in name
            or desc.startswith("Текст озвучки:") or desc.startswith("TTS "))


def intended_text(row: dict | None) -> tuple[str, bool]:
    """(что написано в озвучке, полный ли текст). Из описания файла: «Текст озвучки: «…»» (add_voiceover,
    tts_preview) или старое «TTS gemini/…, голос X: текст» (проба голоса до 13.11). Нет — ("", False)."""
    desc = str((row or {}).get("description") or "")
    if desc.startswith("Текст озвучки: «"):
        body = desc[len("Текст озвучки: «"):]
        end = body.rfind("»")
        return (body[:end], True) if end >= 0 else (body, False)
    match = re.match(r"^TTS [^,]+, голос [^:]+: (.*)$", desc, re.S)
    if match:
        text = match.group(1).strip()
        return text, len(desc) < 500
    return "", False


def clipped(store: Any, layers: list[dict], duration: float | None = None, only_ids: set[str] | None = None) -> list[dict]:
    """Слои audio короче своего файла: [{id, role, start, end, length, file_sec, cut, voice}]."""
    out = []
    for layer in layers or []:
        if layer.get("type") != "audio" or layer.get("hidden"):
            continue
        if only_ids is not None and str(layer.get("id")) not in only_ids:
            continue
        props = layer.get("props") or {}
        sec = asset_seconds(store, props.get("asset_id"))
        if not sec:
            continue
        start, end = _num(layer.get("start")), _num(layer.get("end"))
        need = sec - max(0.0, _num(props.get("offset")))
        cut = need - (end - start)
        if cut <= CLIP_TOLERANCE_SEC:
            continue
        role = layer_role(layer, duration)
        voice = role == "voice" or looks_like_voice_asset(asset_row(store, props.get("asset_id")))
        out.append({"id": str(layer.get("id")), "role": role, "voice": voice, "start": round(start, 3),
                    "end": round(end, 3), "length": round(end - start, 2), "file_sec": round(need, 2),
                    "cut": round(cut, 2), "full_end": round(start + need, 3)})
    return out


def clipped_warnings(store: Any, layers: list[dict], duration: float | None = None,
                     only_ids: set[str] | None = None) -> list[str]:
    """Предупреждения ответа add_layers/update_layer: голос короче файла — всегда, дорожка — если режет ≥ 1 с.
    Эффект (вжух, «поп») короче файла — обычное дело, о нём молчим."""
    out = []
    for item in clipped(store, layers, duration, only_ids):
        fix = f"update_layer(id: \"{item['id']}\", patch: {{end: {item['full_end']:g}}})"
        if item["voice"]:
            out.append(f"голос {item['id']} обрезан: {item['length']:.1f} из {item['file_sec']:.1f} с — фраза "
                       f"оборвётся; {fix}")
        elif item["cut"] >= TRACK_CLIP_WARN_SEC and _is_track(layers, item["id"]):
            out.append(f"звук {item['id']} короче файла: {item['length']:.1f} из {item['file_sec']:.1f} с — если не "
                       f"задумано, {fix}")
    return out


def _is_track(layers: list[dict], layer_id: str) -> bool:
    layer = next((l for l in layers if str(l.get("id")) == layer_id), {})
    return (layer.get("props") or {}).get("mode") == "track"
