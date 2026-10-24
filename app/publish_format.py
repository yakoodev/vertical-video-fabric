"""Публикация по формату клипа (app/formats.py): куда и как уходит ролик на каждой площадке.

* 9:16 — как всегда: YouTube сам делает из вертикали Shorts, TikTok, Instagram Reels.
* 16:9 — на YouTube **обычное видео**, не Shorts: из заголовка и описания убираются
  ``#shorts``, обложка (если она горизонтальная) ставится миниатюрой видео. TikTok и Instagram
  ждут вертикаль — публикация идёт с предупреждением: TikTok покажет ролик с полосами,
  Instagram примет его постом в ленту (Reels — только вертикаль).
* 1:1 и 4:5 — в Instagram постом в ленту (её формат), на YouTube — Shorts (квадрат до 3 минут
  YouTube тоже считает шортсом), в TikTok — с предупреждением про полосы.

Формат берётся из размера готового файла клипа (``clips.width/height``), а нет его — из
настроек плана. Неизвестный формат (файл загружен без клипа) — как раньше, без предупреждений.
"""

from __future__ import annotations

import re
import struct
from pathlib import Path
from typing import Any

from app.formats import DEFAULT_FORMAT, FORMATS, format_of_size, settings_render_format

# Размер файла, у которого пропорция отличается от формата реестра не больше чем на столько
# (1280×720, 720×1280, 1080×1352 после кодека) — тот же формат.
RATIO_TOLERANCE = 0.04

SHORTS_TAG_RE = re.compile(r"(?<![\w#])#shorts?\b", re.IGNORECASE)

TIKTOK_LANDSCAPE = ("TikTok ждёт вертикаль 9:16: горизонтальный ролик покажут мелко, с полосами сверху и снизу — "
                    "лучше отрендерить этот клип ещё и в 9:16 (формат клипа) и выложить вертикаль")
TIKTOK_FEED = "TikTok ждёт вертикаль 9:16: ролик {fmt} покажут с полосами сверху и снизу"
INSTAGRAM_LANDSCAPE = ("Reels — только вертикаль: ролик 16:9 уйдёт в Instagram постом в ленту (там горизонталь "
                       "показывается мелко); для Reels отрендерите клип в 9:16")


def video_format(width: Any, height: Any) -> str | None:
    """Формат по размеру кадра: точное совпадение с реестром или ближайший по пропорции
    (±4 %); иначе / нет размера — None."""
    exact = format_of_size(width, height)
    if exact:
        return exact
    try:
        w, h = float(width), float(height)
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    ratio = w / h
    best = min(FORMATS, key=lambda f: abs(FORMATS[f]["frame"][0] / FORMATS[f]["frame"][1] - ratio))
    fw, fh = FORMATS[best]["frame"]
    return best if abs(fw / fh - ratio) <= (fw / fh) * RATIO_TOLERANCE else None


def clip_format(store: Any, clip: dict | int | None) -> str | None:
    """Формат готового клипа: по размеру файла, иначе по настройкам плана; не понять — None."""
    if clip is None:
        return None
    if not isinstance(clip, dict):
        try:
            clip = store.get_clip(int(clip))
        except (KeyError, TypeError, ValueError):
            return None
    fmt = video_format(clip.get("width"), clip.get("height"))
    if fmt:
        return fmt
    plan_id = clip.get("clip_plan_id")
    if plan_id:
        try:
            plan = store.get_clip_plan(int(plan_id), include_segments=False)
        except Exception:  # noqa: BLE001 - план удалён / хранилище без планов — формат неизвестен
            return None
        return settings_render_format(plan.get("render_settings"))
    return None


def publish_plan(platform: str, fmt: str | None) -> dict:
    """Как ролик формата ``fmt`` уходит на площадку: ``publish_as`` (shorts | video | reel |
    feed | tiktok), ``warning`` (публикуем, но площадка ждёт другое) и ``error`` (не публикуем)."""
    platform = str(platform or "").lower()
    plan = {"platform": platform, "format": fmt, "publish_as": "", "warning": "", "error": ""}
    if platform == "youtube":
        plan["publish_as"] = "video" if fmt == "16:9" else "shorts"
    elif platform == "tiktok":
        plan["publish_as"] = "tiktok"
        if fmt == "16:9":
            plan["warning"] = TIKTOK_LANDSCAPE
        elif fmt in ("1:1", "4:5"):
            plan["warning"] = TIKTOK_FEED.format(fmt=fmt)
    elif platform == "instagram":
        plan["publish_as"] = "feed" if fmt in ("1:1", "4:5", "16:9") else "reel"
        if fmt == "16:9":
            plan["warning"] = INSTAGRAM_LANDSCAPE
    return plan


def is_regular_video(platform: str, fmt: str | None) -> bool:
    """YouTube и 16:9 — обычное видео (не Shorts)."""
    return publish_plan(platform, fmt)["publish_as"] == "video"


def strip_shorts_tags(text: str) -> str:
    """Убрать ``#shorts``/``#short`` из текста (обычное видео — не шортс), не трогая остальное."""
    cleaned = SHORTS_TAG_RE.sub("", str(text or ""))
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = "\n".join(line.rstrip() for line in cleaned.splitlines())
    return cleaned.strip()


def youtube_texts(fmt: str | None, title: str, description: str) -> tuple[str, str]:
    """Заголовок и описание для YouTube: у 16:9 — без #shorts (иначе как есть)."""
    if fmt != "16:9":
        return title, description
    return strip_shorts_tags(title) or title.strip(), strip_shorts_tags(description)


def image_size(path: Any) -> tuple[int, int] | None:
    """(ширина, высота) PNG/JPEG по заголовку файла — без PIL/OpenCV; не понять — None."""
    try:
        data = Path(path).read_bytes()[:256 * 1024]
    except (OSError, TypeError):
        return None
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
        return int(width), int(height)
    if data[:2] == b"\xff\xd8":
        index = 2
        while index + 9 < len(data):
            if data[index] != 0xFF:
                index += 1
                continue
            marker = data[index + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                index += 2
                continue
            length = struct.unpack(">H", data[index + 2:index + 4])[0]
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                height, width = struct.unpack(">HH", data[index + 5:index + 9])
                return int(width), int(height)
            index += 2 + length
    return None


def landscape_cover(store: Any, clip: dict | None) -> str:
    """Путь обложки клипа для миниатюры обычного видео: только горизонтальная (16:9-ish)
    картинка, иначе пусто (вертикальная обложка миниатюрой 16:9 выглядела бы обрезком)."""
    if not clip or not clip.get("id"):
        return ""
    getter = getattr(store, "get_clip_cover_path", None)
    path = getter(int(clip["id"])) if getter else None
    if not path or not Path(path).is_file():
        return ""
    size = image_size(path)
    if not size or size[1] <= 0 or size[0] / size[1] < 1.3:
        return ""
    return str(path)


def upload_extras(store: Any, job: dict, platform: str) -> dict:
    """Доп. аргументы ``Provider.upload`` по формату клипа задания. 9:16 и неизвестный формат —
    пусто: вызов площадки ровно прежний. Никогда не падает: формат — уточнение, не повод
    сорвать публикацию."""
    try:
        return _upload_extras(store, job, platform)
    except Exception:  # noqa: BLE001 - публикация важнее уточнения формата
        return {}


def _upload_extras(store: Any, job: dict, platform: str) -> dict:
    clip = None
    if job.get("clip_id"):
        try:
            clip = store.get_clip(int(job["clip_id"]))
        except (KeyError, TypeError, ValueError):
            clip = None
    fmt = clip_format(store, clip)
    if not fmt or fmt == DEFAULT_FORMAT:
        return {}
    extras: dict = {"video_format": fmt}
    if is_regular_video(platform, fmt):
        cover = landscape_cover(store, clip)
        if cover:
            extras["thumbnail_path"] = cover
    return extras


def check_targets(store: Any, clip: dict | int, account_ids: list[int]) -> dict:
    """Что будет с клипом на выбранных аккаунтах: формат, план по каждому и предупреждения.
    Для экрана публикации и ответа пакетной публикации; ничего не меняет."""
    if not isinstance(clip, dict):
        clip = store.get_clip(int(clip))
    fmt = clip_format(store, clip)
    targets = []
    for account_id in dict.fromkeys(int(a) for a in account_ids or []):
        try:
            account = store.get_account(account_id, include_secret=False)
        except KeyError:
            continue
        targets.append({"account_id": account_id, **publish_plan(account.get("platform") or "", fmt)})
    warnings = list(dict.fromkeys(t["warning"] for t in targets if t["warning"]))
    errors = list(dict.fromkeys(t["error"] for t in targets if t["error"]))
    return {"clip_id": clip.get("id"), "format": fmt, "targets": targets, "warnings": warnings, "errors": errors}
