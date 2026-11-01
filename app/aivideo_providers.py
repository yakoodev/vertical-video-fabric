"""Провайдеры генерации ИИ-видео: картинки и видео, цены (docs/design/ai-video-projects.md §2).

Провайдер — подключаемый: id (``none``, ``polza``, позже kie.ai / fal.ai / Replicate / Veo) →
``ProviderCatalog``: свои таблицы цен (картинка ₽/шт, видео ₽/с; замеренные — отдельно), лимиты
моделей, сборка тела запроса и два адаптера (картинки, видео). Проект выбирает провайдера и модель
на каждый этап (``provider`` + ``image_model``, ``video_provider`` + ``video_model``). Новый
провайдер = новый адаптер + каталог в ``CATALOGS``; конвейер (``aivideo_gen``) не меняется.

Интерфейс адаптера один на всех — асинхронный: ``submit`` → id запроса (его пишут в БД ДО
опроса), ``check`` → статус, ``fetch`` → файл. Необязательный ``recover(model, since, exclude)`` —
если адаптер умеет найти запрос, ушедший без ответа (``submit`` бросил ``SubmitUncertain``).
Как именно искать — дело адаптера (у Polza — ``/history/generations``). Сухой прогон (``none``)
идёт тем же путём, что и платный: весь конвейер и UI проверяются бесплатно.

* ``DryRunImages`` / ``DryRunVideo`` (``none``, 0 ₽) — заглушки ffmpeg. Никакой сети.
* ``PolzaImages`` / ``PolzaVideo`` (``polza``, ПЛАТНО) — ``POST /media`` ``{model, input: {...,
  images: [{type: base64, data}]}}`` → ``{id}`` (или сразу ``data.url``); ``GET /media/<id>`` →
  ``completed`` → ``data[0].url`` / ``data.url`` (ссылка живёт 7 дней — скачиваем сразу). Сеть до
  отправки (соединение/TLS) — исключение httpx как есть (``aivideo_gen`` повторит сам, 1.1). Запрос
  ушёл без ответа (504/502/503, обрыв чтения) — ``SubmitUncertain``: у видео POST отвечает 504
  ВСЕГДА через ~60 с, а работа идёт и списывается — id находит ``recover`` в ``/history/generations``.

Цены Polza — прайс каталога ``GET /models`` (снимок ``D:\\vvf-offload\\tmp\\videogen\\polza_models.json``,
2026-11-01). Замеренные (списание = прайс): nano-banana 1K 3 ₽ и 2K 4.5 ₽, seedance-2-fast 720p
22.275 ₽/с, grok-imagine-video 720p 3.0375 ₽/с (``input.duration`` строкой). Не замеренное: картинки —
прайс × ``PRICE_MARKUP`` (замер TTS показал ×2.04), видео — × ``VIDEO_PRICE_MARKUP`` (1.25: оба замера
видео = прайс). После первого настоящего запуска модели — внести факт в ``measured_*`` её каталога.

Видеомодели (v3): ``VIDEO_LIMITS`` — возможности (``first_last``, ``ref_images``, ``exclusive`` —
кадры и листы персонажей взаимоисключающие, длительности, разрешения, звук); ``choose_video_mode`` —
режим подачи; ``video_resolution_for`` / ``snap_seconds`` — к тому, что есть у модели. Слот на будущее —
``FUTURE_CATALOGS["genapi"]`` (Kling 4.0 reference, без сети).
"""

from __future__ import annotations

import base64
import logging
import hashlib
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from app.settings import settings

_log = logging.getLogger(__name__)

PRICE_CHECKED = "2026-11-01"
PRICE_MARKUP = 2.0  # запас к прайсу, пока цена модели не замерена (как в dubbing)
# Видео Polza: оба замера (seedance-2-fast, grok) — списание = прайс ×1; не замеренным видео — ×1.25
# до первого настоящего запуска (×2 загоняло бы черновую серию H3 за потолок 350 ₽).
VIDEO_PRICE_MARKUP = 1.25
DEFAULT_IMAGE_MODEL = "google/gemini-nano-banana-2.1"
DEFAULT_VIDEO_MODEL = "bytedance/seedance-2"  # качество; черновик — DEFAULT_VIDEO_MODELS
DEFAULT_VIDEO_RESOLUTION = "720p"
RESOLUTION = {"draft": "1K", "quality": "2K"}
REF_MAX_PX = 1024
START_FRAME_MAX_PX = 1536  # стартовый кадр видео — по высоте (как в ручном v4)
POLL_INTERVAL_SEC = 3.0

# --- Polza: таблицы ----------------------------------------------------------- #
# Прайс ₽ за картинку по разрешению ("*" — одна цена).
IMAGE_PRICES: dict[str, dict[str, float]] = {
    "google/gemini-nano-banana-2.1": {"1K": 3.0, "2K": 4.5, "4K": 6.75},
    "bytedance/seedream-4.5": {"*": 5.0},
    "openai/gpt-image-2.5-flare": {"1K": 4.0, "2K": 7.0, "4K": 11.0},
    "qwen/image-2.1": {"1K": 3.0, "2K": 6.0},
    "black-forest-labs/flux.2-pro": {"1K": 5.0, "2K": 7.0},
}
# Замеренные: ₽ за картинку как есть, без запаса (разрешения, которых нет, — прайс с запасом).
MEASURED_IMAGE_PRICES: dict[str, dict[str, float]] = {
    # первые прогоны 2026-11-01: 1K 3 ₽, 2K 4.5 ₽ = прайс ×1
    "google/gemini-nano-banana-2.1": {"1K": 3.0, "2K": 4.5},
}
MAX_REFS = {"google/gemini-nano-banana-2.1": 10, "bytedance/seedream-4.5": 14, "openai/gpt-image-2.5-flare": 16,
            "qwen/image-2.1": 10, "black-forest-labs/flux.2-pro": 8}
# Видео: прайс ₽ за СЕКУНДУ по разрешению (каталог, unitParam=duration; снимок 2026-11-01).
# seedance-2 / 2-5 — тариф без видео-референса (has_video=false): мы видео на вход не даём.
VIDEO_PRICES: dict[str, dict[str, float]] = {
    "bytedance/seedance-2": {"480p": 12.825, "720p": 27.675, "1080p": 68.85},
    "bytedance/seedance-2-5": {"480p": 18.9, "720p": 42.525},
    "minimax/hailuo-3-max": {"480p": 9.115264, "768p": 9.115264},
    "bytedance/seedance-2-fast": {"480p": 10.4625, "720p": 22.275},
    "grok-imagine-video-1-5": {"480p": 1.62, "720p": 3.0375},
}
# Замер 2026-11-01: списание по секундам = прайс (seedance 4 с 720p = 89.1 ₽, grok 720p 3.0375 ₽/с).
MEASURED_VIDEO_PRICES: dict[str, dict[str, float]] = {
    "bytedance/seedance-2-fast": {"720p": 22.275},
    "grok-imagine-video-1-5": {"720p": 3.0375},
}
# Возможности видеомоделей (docs/design/ai-video-models-2026-11.md): длительность (с; ``durations`` —
# только эти значения), разрешения, звук (``audio_param`` — имя флага, None — звук свой без флага),
# режимы подачи кадров:
# * ``first_last`` — ``images=[первый, последний]``: модель только интерполирует между ключевыми кадрами;
# * ``ref_images`` — сколько листов персонажей берёт ``reference_images`` (0 — режима нет);
# * ``exclusive`` — кадры и референсы ВЗАИМОИСКЛЮЧАЮЩИЕ (Seedance 2.x, MiniMax H3, Veo): запрос с обоими
#   KIE отклоняет 400 или молча роняет одну группу — никогда не шлём оба;
# * ``negative`` — имя поля негатива (нет — блок «Avoid: …» в конце промпта).
# kling/v3 сам режет план на склейки — для сцепки планов не годится, поэтому его здесь нет.
VIDEO_LIMITS: dict[str, dict[str, Any]] = {
    "bytedance/seedance-2": {
        "label": "Seedance 2.0 (качество, первый+последний кадр, звук)", "min": 4, "max": 15,
        "resolutions": ("480p", "720p", "1080p"), "audio": True, "audio_param": "generate_audio",
        "images": 2, "first_last": True, "ref_images": 9, "exclusive": True, "negative": None},
    "bytedance/seedance-2-5": {
        "label": "Seedance 2.5 (лучшая, дорого)", "min": 4, "max": 30, "resolutions": ("480p", "720p"),
        "audio": True, "audio_param": "generate_audio", "images": 2, "first_last": True, "ref_images": 30,
        "exclusive": True, "negative": None},
    "minimax/hailuo-3-max": {
        # Замер 02.11: «This model supports a single keyframe image: send either a first_frame or a
        # last_frame, not both» (каталог Polza пишет first/last) — только стартовый кадр.
        "label": "MiniMax H3 Max (черновик: только стартовый кадр, звук, дёшево)", "min": 5, "max": 15,
        "durations": tuple(range(5, 16)), "resolutions": ("480p", "768p"), "audio": True, "audio_param": None,
        "images": 1, "first_last": False, "ref_images": 0, "exclusive": True, "negative": None},
    "bytedance/seedance-2-fast": {
        "label": "Seedance 2 fast (дешевле, хуже держит физику)", "min": 4, "max": 15,
        "resolutions": ("480p", "720p"), "audio": True, "audio_param": "generate_audio", "images": 2,
        "first_last": True, "ref_images": 9, "exclusive": True, "negative": None},
    "grok-imagine-video-1-5": {
        "label": "Grok Imagine 1.5 (самый дешёвый черновик, без звука, только старт)", "min": 1, "max": 15,
        "resolutions": ("480p", "720p"), "audio": False, "audio_param": None, "images": 7, "first_last": False,
        "ref_images": 0, "exclusive": True, "negative": None},
}
VIDEO_LABELS = {m: c["label"] for m, c in VIDEO_LIMITS.items()}
# Модель по умолчанию — по режиму серии (если в проекте и AIVIDEO_VIDEO_MODEL пусто).
DEFAULT_VIDEO_MODELS = {"quality": "bytedance/seedance-2", "draft": "minimax/hailuo-3-max"}
VIDEO_MODES = ("first_last", "start", "refs")
VIDEO_MODE_LABELS = {"first_last": "первый+последний кадр", "start": "только стартовый кадр",
                     "refs": "старт + листы персонажей"}


class ProviderError(RuntimeError):
    """Провайдер отказал (4xx/5xx). ``policy`` — отказ контент-политики, ``status`` — HTTP-код."""

    def __init__(self, message: str, policy: bool = False, status: int | None = None):
        super().__init__(message)
        self.policy = policy
        self.status = status


def network_phase(exc: BaseException) -> str | None:
    """Сетевая ошибка запроса: ``before`` — тело до провайдера не дошло (соединение/TLS не
    установлено: ``ConnectError`` «TLS/SSL connection has been closed (EOF)», ``ConnectTimeout``) —
    повторять безопасно, денег не взяли; ``after`` — запрос мог дойти (``ReadTimeout``,
    ``RemoteProtocolError``, обрыв чтения/записи) — возможно оплачено; ``None`` — не сеть."""
    try:
        import httpx
    except ImportError:  # pragma: no cover - httpx в зависимостях
        return None
    before = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout, httpx.ProxyError,
              httpx.UnsupportedProtocol, httpx.LocalProtocolError)
    if isinstance(exc, before):
        return "before"
    if isinstance(exc, httpx.TransportError):
        return "after"
    return None


class SubmitUncertain(ProviderError):
    """Запрос ушёл, но ответа нет (504, обрыв чтения): провайдер, скорее всего, уже работает и
    списывает. Повторять POST нельзя — адаптер ищет его сам (``recover``)."""


def polza_image_params(model: str, prompt: str, aspect: str, resolution: str) -> dict:
    """Тело запроса картинки Polza по семейству модели (у seedream своё: quality и aspect_ratio)."""
    if model.startswith("bytedance/seedream"):
        return {"model": model, "prompt": prompt[:3000], "aspect_ratio": aspect,
                "quality": "high" if resolution in ("2K", "4K") else "basic"}
    if model.startswith("black-forest-labs/") or model.startswith("qwen/"):
        return {"model": model, "prompt": prompt[:20000], "aspect_ratio": aspect, "image_resolution": resolution}
    return {"model": model, "prompt": prompt[:20000], "aspect_ratio": aspect, "image_resolution": resolution,
            "output_format": "jpeg"}


def polza_video_params(model: str, prompt: str, seconds: int, resolution: str, aspect: str = "9:16",
                       negative: str = "") -> dict:
    """Тело ``input`` image-to-video Polza. duration — СТРОКА (так принял seedance в замере), к
    допустимой у модели; разрешение — ближайшее у модели (у H3 720p → 768p); флаг звука — только у
    моделей с ним (seedance ``generate_audio``; H3 звучит сам). Негатив — полем, если модель его
    берёт, иначе блоком «Avoid: …» в конце промпта."""
    caps = video_capabilities(model)
    if negative and not caps.get("negative"):
        prompt = f"{prompt.rstrip()} Avoid: {negative.strip().rstrip('.')}."
    out = {"model": model, "prompt": prompt[:4000], "duration": str(snap_seconds(model, seconds)),
           "aspect_ratio": aspect, "resolution": video_resolution_for(model, resolution)}
    if caps.get("audio") and caps.get("audio_param"):
        out[caps["audio_param"]] = True
    if negative and caps.get("negative"):
        out[caps["negative"]] = negative[:1000]
    return out


def video_capabilities(model: str, provider: str | None = None) -> dict:
    """Возможности видеомодели (режимы кадров, референсы, длительности, разрешения, звук) —
    из каталога провайдера; неизвестная — осторожно: только стартовый кадр."""
    cats = [catalog(provider)] if provider and provider in CATALOGS else []
    for c in [*cats, *CATALOGS.values(), *FUTURE_CATALOGS.values()]:
        if model in c.video_limits:
            return {"model": model, **c.video_limits[model]}
    return {"model": model, "min": 4, "max": 15, "images": 1, "audio": False, "first_last": False, "ref_images": 0,
            "exclusive": True, "resolutions": ("480p", "720p"), "negative": None, "label": model}


def video_resolution_for(model: str, wanted: str) -> str:
    """Разрешение проекта (480p/720p/1080p) → то, что есть у модели: точное, иначе ближайшее
    по высоте (у H3 вместо 720p — 768p, у fast вместо 1080p — 720p)."""
    options = tuple(video_capabilities(model).get("resolutions") or ())
    if not options or wanted in options:
        return wanted

    def height(value: str) -> int:
        digits = "".join(ch for ch in str(value) if ch.isdigit())
        return int(digits) if digits else 720

    return min(options, key=lambda r: (abs(height(r) - height(wanted)), -height(r)))


def snap_seconds(model: str, seconds: float) -> int:
    """Секунды видео → допустимые у модели (min…max или ближайшее из ``durations``, не короче)."""
    caps = video_capabilities(model)
    want = int(round(float(seconds)))
    allowed = caps.get("durations")
    if allowed:
        longer = [d for d in allowed if d >= want]
        return int(min(longer) if longer else max(allowed))
    return int(min(int(caps.get("max") or 15), max(int(caps.get("min") or 1), want)))


def choose_video_mode(model: str, has_end: bool, has_refs: bool = False, provider: str | None = None) -> str:
    """Режим подачи кадров в видеомодель: ``first_last`` — есть оба ключевых кадра и модель это умеет
    (главный режим: персонаж нарисован на обоих концах); ``refs`` — старт + листы персонажей, ТОЛЬКО
    у моделей, где это совместимо (не ``exclusive``: Kling 4.0); иначе ``start`` — один старт."""
    caps = video_capabilities(model, provider)
    if has_end and caps.get("first_last"):
        return "first_last"
    if has_refs and caps.get("ref_images") and not caps.get("exclusive"):
        return "refs"
    return "start"


def default_video_model(quality: str | None) -> str:
    """Видеомодель по умолчанию: качество — seedance-2 720p, черновик — MiniMax H3 Max."""
    return DEFAULT_VIDEO_MODELS.get(str(quality or "draft"), DEFAULT_VIDEO_MODELS["draft"])


# --------------------------------------------------------------------------- #
# Каталог провайдеров
# --------------------------------------------------------------------------- #


@dataclass
class ProviderCatalog:
    """Всё, что конвейеру нужно знать о провайдере: цены, лимиты, тело запроса, адаптеры."""

    id: str
    label: str
    paid: bool
    image_adapter: Callable[[], Any]
    video_adapter: Callable[[], Any]
    image_params: Callable[..., dict]
    video_params: Callable[..., dict]
    # Таблицы: {image_prices, measured_image, max_refs, video_prices, measured_video, video_limits}.
    # Функция, а не словарь: таблицы читаются при каждом расчёте (их правят замеры и тесты).
    tables: Callable[[], dict[str, dict]] = field(default=lambda: {})
    markup: float = PRICE_MARKUP
    video_markup: float | None = None  # своя наценка видео (None — как markup)

    def _table(self, name: str) -> dict:
        return self.tables().get(name) or {}

    @property
    def image_prices(self) -> dict[str, dict[str, float]]:
        return self._table("image_prices")

    @property
    def measured_image(self) -> dict[str, dict[str, float]]:
        return self._table("measured_image")

    @property
    def max_refs(self) -> dict[str, int]:
        return self._table("max_refs")

    @property
    def video_prices(self) -> dict[str, dict[str, float]]:
        return self._table("video_prices")

    @property
    def measured_video(self) -> dict[str, dict[str, float]]:
        return self._table("measured_video")

    @property
    def video_limits(self) -> dict[str, dict[str, Any]]:
        return self._table("video_limits")

    def image_models(self) -> list[str]:
        """Модели картинок; у сухого прогона — модели всех платных (он их изображает)."""
        return list(self.image_prices) or [m for c in _paid_catalogs() for m in c.image_prices]

    def video_models(self) -> list[str]:
        return list(self.video_prices) or [m for c in _paid_catalogs() for m in c.video_prices]


CATALOGS: dict[str, ProviderCatalog] = {}


def _paid_catalogs() -> list[ProviderCatalog]:
    return [c for c in CATALOGS.values() if c.paid]


def provider_ids() -> tuple[str, ...]:
    return tuple(CATALOGS)


def catalog(provider: str | None) -> ProviderCatalog:
    key = str(provider or "none").strip().lower()
    if key in ("dry", "dry-run", "mock"):
        key = "none"
    if key not in CATALOGS:
        raise ProviderError(f"провайдер {provider!r}: один из {', '.join(CATALOGS)}")
    return CATALOGS[key]


def _video_limits(model: str) -> dict:
    return video_capabilities(model)


def image_price(model: str, resolution: str, provider: str = "polza") -> dict:
    """Цена одной картинки, ₽: {rub, list_rub, measured, note} по каталогу провайдера."""
    cat = catalog(provider)
    if not cat.paid:
        return {"rub": 0.0, "list_rub": 0.0, "measured": True, "note": "сухой прогон"}
    measured = cat.measured_image.get(model) or {}
    if resolution in measured or "*" in measured:
        rub = measured.get(resolution, measured.get("*"))
        return {"rub": round(rub, 2), "list_rub": rub, "measured": True, "note": "замер"}
    tiers = cat.image_prices.get(model)
    if not tiers:
        top = max(max(t.values()) for t in cat.image_prices.values())
        return {"rub": round(top * cat.markup, 2), "list_rub": top, "measured": False,
                "note": "модели нет в таблице — оценка по самой дорогой"}
    rub = tiers.get(resolution, tiers.get("*", max(tiers.values())))
    return {"rub": round(rub * cat.markup, 2), "list_rub": rub, "measured": False,
            "note": f"прайс ×{cat.markup:g} (не замерено)"}


def video_price(model: str, resolution: str, seconds: float, provider: str = "polza") -> dict:
    """Цена одного видео, ₽: {rub, per_sec, measured, note}. Замер — как есть, иначе прайс × запас."""
    cat = catalog(provider)
    if not cat.paid:
        return {"rub": 0.0, "per_sec": 0.0, "measured": True, "note": "сухой прогон"}
    resolution = video_resolution_for(model, resolution)
    measured = cat.measured_video.get(model) or {}
    if resolution in measured:
        per_sec = measured[resolution]
        return {"rub": round(per_sec * seconds, 2), "per_sec": per_sec, "measured": True,
                "note": f"замер {per_sec:g} ₽/с × {seconds:g} с"}
    tiers = cat.video_prices.get(model)
    mk = cat.markup if cat.video_markup is None else cat.video_markup
    if not tiers:
        top = max(max(t.values()) for t in cat.video_prices.values())
        return {"rub": round(top * mk * seconds, 2), "per_sec": top * mk, "measured": False,
                "note": f"модели нет в таблице — оценка по самой дорогой ×{mk:g}"}
    per_sec = tiers.get(resolution, max(tiers.values()))
    return {"rub": round(per_sec * mk * seconds, 2), "per_sec": round(per_sec * mk, 4), "measured": False,
            "note": f"прайс {per_sec:g} ₽/с ×{mk:g} (не замерено) × {seconds:g} с"}


def video_seconds_range(model: str) -> tuple[int, int]:
    lim = _video_limits(model)
    return int(lim["min"]), int(lim["max"])


def max_refs(model: str, provider: str = "polza") -> int:
    for c in [catalog(provider), *CATALOGS.values()]:
        if model in c.max_refs:
            return int(c.max_refs[model])
    return 10


def model_params(model: str, prompt: str, aspect: str, resolution: str, provider: str = "polza") -> dict:
    """Тело запроса картинки — по каталогу провайдера."""
    return catalog(provider).image_params(model, prompt, aspect, resolution)


def video_params(model: str, prompt: str, seconds: int, resolution: str, aspect: str = "9:16",
                 provider: str = "polza", negative: str = "") -> dict:
    """Тело запроса видео — по каталогу провайдера (``negative`` — полем или блоком «Avoid»)."""
    build = catalog(provider).video_params
    return build(model, prompt, seconds, resolution, aspect, negative=negative) if negative else \
        build(model, prompt, seconds, resolution, aspect)


# --------------------------------------------------------------------------- #
# Интерфейс
# --------------------------------------------------------------------------- #


class ImageProvider(Protocol):
    """Адаптер картинок или видео. ``submit`` бросает ``ProviderError`` (не ушло / отказ) или
    ``SubmitUncertain`` (ушло без ответа). Адаптер, умеющий искать такие запросы, добавляет
    ``recover(model, since_iso, exclude_ids) -> {id, status, cost} | None``; без него запуск
    после ``SubmitUncertain`` не повторяется (failed, «проверь баланс»)."""

    name: str
    paid: bool

    def submit(self, params: dict, refs: list[Path]) -> str: ...

    def check(self, remote_id: str) -> dict: ...

    def fetch(self, result: dict, dest: Path, params: dict) -> Path: ...


VideoProvider = ImageProvider


# --------------------------------------------------------------------------- #
# Сухой прогон
# --------------------------------------------------------------------------- #

_DRY_SIZES = {"9:16": (576, 1024), "16:9": (1024, 576), "3:2": (960, 640), "1:1": (768, 768)}
_DRY_COLORS = {"style": ("0x2b1055", "0x7597de"), "location": ("0x0f2027", "0x2c5364"),
               "turnaround": ("0x3a1c71", "0xffaf7b"), "expressions": ("0x42275a", "0x734b6d"),
               "look": ("0x134e5e", "0x71b280"), "keyframe": ("0x1f1c2c", "0x928dab")}


def _font() -> Path | None:
    for name in ("Inter-SemiBold.ttf", "GolosText-Bold.ttf", "Montserrat-Bold.ttf"):
        path = settings.root_dir / "vendor" / "fonts" / name
        if path.is_file():
            return path
    return None


def _ff_path(path: Path) -> str:
    return str(path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


class DryRunImages:
    """Бесплатно: градиент + подпись вместо картинки. Тот же путь submit → check → fetch."""

    name = "none"
    paid = False

    def submit(self, params: dict, refs: list[Path]) -> str:
        digest = hashlib.sha1(repr(sorted(params.items())).encode("utf-8")).hexdigest()[:12]
        return f"dry-{digest}-{len(refs)}"

    def check(self, remote_id: str) -> dict:
        return {"status": "completed", "cost_rub": 0.0}

    def fetch(self, result: dict, dest: Path, params: dict) -> Path:
        w, h = _DRY_SIZES.get(str(params.get("aspect_ratio") or "9:16"), _DRY_SIZES["9:16"])
        c0, c1 = _DRY_COLORS.get(str(params.get("_kind") or ""), ("0x232526", "0x414345"))
        lines = [str(x)[:46] for x in (params.get("_caption") or [])][:6] or ["сухой прогон"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        work = dest.parent
        chain = []
        font = _font()
        if font:
            size = max(20, w // 22)
            y0 = h // 2 - (len(lines) * (size + 14)) // 2
            for i, line in enumerate(lines):
                txt = work / f"{dest.stem}-{i}.txt"
                txt.write_text(line, encoding="utf-8")
                chain.append(
                    f"drawtext=fontfile='{_ff_path(font)}':textfile='{_ff_path(txt)}':fontcolor=white:"
                    f"fontsize={size}:x=(w-text_w)/2:y={y0 + i * (size + 14)}:box=1:boxcolor=black@0.35:boxborderw=12")
        args = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                f"gradients=s={w}x{h}:c0={c0}:c1={c1}:n=2:x0=0:y0=0:x1={w}:y1={h}:speed=0.0001:d=1",
                "-frames:v", "1"]
        if chain:
            args += ["-vf", ",".join(chain)]
        proc = subprocess.run([*args, str(dest)], capture_output=True, text=True, timeout=60, check=False)
        for i in range(len(lines)):
            (work / f"{dest.stem}-{i}.txt").unlink(missing_ok=True)
        if proc.returncode != 0 or not dest.is_file():
            # Нет drawtext/шрифта — хотя бы градиент.
            proc = subprocess.run(args[:-2] + ["-frames:v", "1", str(dest)], capture_output=True, text=True,
                                  timeout=60, check=False)
            if proc.returncode != 0:
                raise ProviderError(f"заглушка не нарисована: {(proc.stderr or '')[-200:]}")
        return dest


_DRY_VIDEO_SIZE = (288, 512)


class DryRunVideo:
    """Бесплатно: стартовый кадр с лёгким наездом + тихий тон вместо звука — короткий mp4 нужной
    длины. Тот же путь submit → check → fetch, что у платного видео."""

    name = "none"
    paid = False
    request_type = "video"

    def submit(self, params: dict, refs: list[Path]) -> str:
        digest = hashlib.sha1(repr(sorted((k, str(v)) for k, v in params.items())).encode("utf-8")).hexdigest()[:12]
        return f"dryv-{digest}"

    def check(self, remote_id: str) -> dict:
        return {"status": "completed", "cost_rub": 0.0}

    def fetch(self, result: dict, dest: Path, params: dict) -> Path:
        w, h = _DRY_VIDEO_SIZE
        try:
            seconds = max(1.0, min(15.0, float(params.get("duration") or 4)))
        except (TypeError, ValueError):
            seconds = 4.0
        start = params.get("_start")
        end = params.get("_end")
        dest.parent.mkdir(parents=True, exist_ok=True)
        if start and Path(start).is_file() and end and Path(end).is_file():
            # Первый+последний кадр: наплыв от старта к концу (как интерполяция модели).
            fit = (f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps=12,format=yuv420p,"
                   "settb=1/12")
            offset, fade = seconds * 0.25, seconds * 0.5
            args = ["ffmpeg", "-v", "error", "-y", "-loop", "1", "-framerate", "12", "-t", f"{seconds:.3f}", "-i",
                    str(start), "-loop", "1", "-framerate", "12", "-t", f"{seconds:.3f}", "-i", str(end),
                    "-f", "lavfi", "-t", f"{seconds:.3f}", "-i", "sine=frequency=330:sample_rate=44100",
                    "-filter_complex", f"[0:v]{fit}[a];[1:v]{fit}[b];[a][b]xfade=transition=fade:duration={fade:.3f}:"
                                       f"offset={offset:.3f},format=yuv420p[v]",
                    "-map", "[v]", "-map", "2:a", "-af", "volume=0.05", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "64k", "-t", f"{seconds:.3f}", str(dest)]
            proc = subprocess.run(args, capture_output=True, text=True, timeout=120, check=False)
            if proc.returncode == 0 and dest.is_file():
                return dest
        if start and Path(start).is_file():
            video_in = ["-loop", "1", "-framerate", "12", "-t", f"{seconds:.3f}", "-i", str(start)]
            vf = (f"scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase,crop={w * 2}:{h * 2},"
                  f"zoompan=z='min(1+0.004*on,1.2)':d=1:s={w}x{h}:fps=12,format=yuv420p")
        else:
            video_in = ["-f", "lavfi", "-i", f"gradients=s={w}x{h}:c0=0x1f1c2c:c1=0x928dab:speed=0.02:d={seconds:.3f}"]
            vf = "fps=12,format=yuv420p"
        args = ["ffmpeg", "-v", "error", "-y", *video_in, "-f", "lavfi", "-t", f"{seconds:.3f}", "-i",
                "sine=frequency=330:sample_rate=44100", "-vf", vf, "-af", "volume=0.05",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "64k",
                "-t", f"{seconds:.3f}", "-shortest", str(dest)]
        proc = subprocess.run(args, capture_output=True, text=True, timeout=120, check=False)
        if proc.returncode != 0 or not dest.is_file():
            raise ProviderError(f"заглушка видео не собрана: {(proc.stderr or '')[-300:]}")
        return dest


def last_frame(video: Path, dest: Path) -> Path:
    """Последний кадр видео (jpeg) — старт следующего плана, если сцена продолжается."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    for args in (["-sseof", "-0.1", "-i", str(video)], ["-sseof", "-0.5", "-i", str(video)]):
        proc = subprocess.run(["ffmpeg", "-v", "error", "-y", *args, "-frames:v", "1", "-q:v", "2", str(dest)],
                              capture_output=True, text=True, timeout=60, check=False)
        if proc.returncode == 0 and dest.is_file() and dest.stat().st_size > 0:
            return dest
    proc = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-update", "1", "-q:v", "2", str(dest)],
                          capture_output=True, text=True, timeout=120, check=False)
    if proc.returncode != 0 or not dest.is_file():
        raise ProviderError(f"последний кадр не вынут: {(proc.stderr or '')[-200:]}")
    return dest


# --------------------------------------------------------------------------- #
# Polza
# --------------------------------------------------------------------------- #


def ref_data_url(path: Path) -> str:
    """Референс → data-URL jpeg ≤ REF_MAX_PX (меньше запрос и быстрее загрузка у провайдера)."""
    small = path.with_name(f"{path.stem}.ref{REF_MAX_PX}.jpg")
    if not small.is_file():
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-frames:v", "1", "-vf",
                        f"scale='min({REF_MAX_PX},iw)':-2", "-q:v", "4", str(small)],
                       capture_output=True, timeout=60, check=False)
    src = small if small.is_file() else path
    mime = "image/jpeg" if src.suffix.lower() in (".jpg", ".jpeg") else "image/png"
    return f"data:{mime};base64," + base64.b64encode(src.read_bytes()).decode("ascii")


def start_frame_data_url(path: Path) -> str:
    """Стартовый кадр видео → data-URL jpeg ≤ START_FRAME_MAX_PX по высоте (качество выше, чем у референсов)."""
    small = path.with_name(f"{path.stem}.start{START_FRAME_MAX_PX}.jpg")
    if not small.is_file():
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-frames:v", "1", "-vf",
                        f"scale=-2:'min({START_FRAME_MAX_PX},ih)'", "-q:v", "2", str(small)],
                       capture_output=True, timeout=60, check=False)
    src = small if small.is_file() else path
    mime = "image/jpeg" if src.suffix.lower() in (".jpg", ".jpeg") else "image/png"
    return f"data:{mime};base64," + base64.b64encode(src.read_bytes()).decode("ascii")


_POLICY_WORDS = ("moderat", "policy", "nsfw", "safety", "content_filter", "prohibited", "blocked")
HISTORY_SLACK_SEC = 5  # часы Polza и наши могут разойтись — ищем с запасом
GATEWAY_STATUSES = (502, 503, 504)  # шлюз не дождался провайдера — запрос мог уйти в работу
HISTORY_LIMIT = 20


def parse_utc(value: Any) -> Any:
    """ISO-время Polza («2026-11-01T15:33:58.123Z») или наше → datetime UTC; мусор — None."""
    from datetime import datetime, timezone

    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00").replace(" ", "T")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        try:
            dt = datetime.fromisoformat(text[:19])
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class PolzaImages:
    """ПЛАТНО. Вызывать только из ``aivideo_gen`` после всех проверок бюджета и одобрения.

    Сеть: исключение httpx как есть (``network_phase``: до отправки — повтор, после — запрос мог
    уйти). Шлюз ответил 502/503/504 — ``SubmitUncertain``. В обоих «ушло без ответа» случаях
    работа у Polza, скорее всего, идёт и оплачена: id ищем ``recover`` в ``GET /history/generations``
    (не нашли за ``recover_deadline_sec`` — «под вопросом»), POST не повторяем."""

    name = "polza"
    paid = True
    request_type = "images"
    recover_deadline_sec = 5 * 60  # картинка готова за минуту-две: дольше не видно — значит не ушла

    def __init__(self, api_key: str | None = None, base_url: str | None = None, timeout: float = 120) -> None:
        self.api_key = (api_key if api_key is not None else settings.polza_api_key).strip()
        self.base_url = (base_url or settings.polza_base_url).rstrip("/")
        self.timeout = timeout

    def _headers(self) -> dict:
        if not self.api_key:
            raise ProviderError("POLZA_API_KEY не настроен")
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _encode(self, path: Path) -> str:
        return ref_data_url(path)

    def _ref(self, path: Path) -> dict:
        """Картинка в ``input.images``: base64 (картинковым моделям хватает)."""
        return {"type": "base64", "data": self._encode(path)}

    def submit(self, params: dict, refs: list[Path]) -> str:
        import httpx

        # POST /media: параметры модели — внутри input (у /images/generations aspect_ratio
        # молча игнорировался — приходил квадрат 1024×1024 вместо 9:16).
        fields = {k: v for k, v in params.items() if not k.startswith("_") and k != "model"}
        if refs:
            fields["images"] = [self._ref(p) for p in refs]
        sheets = [Path(p) for p in params.get("_reference_images") or []]
        if sheets:
            fields["reference_images"] = [self._ref(p) for p in sheets]
        body = {"model": params.get("model"), "input": fields}
        # Сетевые ошибки httpx — как есть: до отправки (соединение/TLS) aivideo_gen повторит сам,
        # после (обрыв/таймаут чтения) — запрос мог уйти: ищем его ``recover``, POST не повторяем.
        response = httpx.post(f"{self.base_url}/media", headers=self._headers(), json=body, timeout=self.timeout)
        if response.status_code in GATEWAY_STATUSES:
            raise SubmitUncertain(f"Polza {response.status_code}: {_detail(response)} — запрос мог уйти в работу "
                                  "(у видео 504 приходит всегда)", status=response.status_code)
        if response.status_code >= 400:
            detail = _detail(response)
            raise ProviderError(f"Polza {response.status_code}: {detail}",
                                policy=any(w in detail.lower() for w in _POLICY_WORDS), status=response.status_code)
        data = response.json() if response.content else {}
        remote = data.get("requestId") or data.get("request_id") or data.get("id")
        if remote:
            return str(remote)
        url = _result_url(data)  # синхронный ответ (data[0].url / data.url) — ссылка сразу
        if url:
            return "url:" + url
        raise ProviderError("Polza: в ответе нет requestId")

    def check(self, remote_id: str) -> dict:
        import httpx

        if remote_id.startswith("url:"):
            return {"status": "completed", "url": remote_id[4:]}
        response = httpx.get(f"{self.base_url}/media/{remote_id}", headers=self._headers(), timeout=self.timeout)
        if response.status_code >= 500 or response.status_code == 429:
            return {"status": "pending", "note": f"HTTP {response.status_code}"}
        if response.status_code >= 400:
            detail = _detail(response)
            return {"status": "failed", "error": f"Polza {response.status_code}: {detail}",
                    "policy": any(w in detail.lower() for w in _POLICY_WORDS)}
        data = response.json() if response.content else {}
        status = str(data.get("status") or "").lower()
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        cost = usage.get("cost_rub", usage.get("cost"))
        try:
            cost = float(cost) if cost is not None else None
        except (TypeError, ValueError):
            cost = None
        if status in ("completed", "succeeded", "success", "done"):
            url = _result_url(data)
            if not url:
                return {"status": "failed", "error": "Polza: completed без data[0].url", "cost_rub": cost}
            return {"status": "completed", "url": url, "cost_rub": cost}
        if status in ("failed", "error", "cancelled", "canceled", "rejected", "moderated", "blocked"):
            err = data.get("error")
            text = str(err.get("message") if isinstance(err, dict) else err or status)[:300]
            return {"status": "failed", "error": text, "cost_rub": cost,
                    "policy": status in ("moderated", "blocked") or any(w in text.lower() for w in _POLICY_WORDS)}
        return {"status": "pending"}

    def fetch(self, result: dict, dest: Path, params: dict) -> Path:
        import httpx

        url = str(result.get("url") or "")
        if url.startswith("data:"):
            dest.write_bytes(base64.b64decode(url.split(",", 1)[1]))
            return dest
        response = httpx.get(url, timeout=self.timeout, follow_redirects=True)
        if response.status_code >= 400:
            raise ProviderError(f"скачивание результата: HTTP {response.status_code}")
        dest.write_bytes(response.content)
        return dest

    def balance(self, timeout: float = 10) -> float | None:
        """Баланс ₽ (если Polza отдаёт ``GET /balance``) — сверка трат; ошибка — None."""
        import httpx

        try:
            response = httpx.get(f"{self.base_url}/balance", headers=self._headers(), timeout=timeout)
            if response.status_code >= 400:
                return None
            data = response.json()
        except Exception:  # noqa: BLE001 - сверка — подсказка, не условие
            return None
        for key in ("balance", "amount", "balance_rub"):
            try:
                if isinstance(data, dict) and data.get(key) is not None:
                    return float(data[key])
            except (TypeError, ValueError):
                continue
        return None

    def recover(self, model: str, since: str, exclude: set[str] | frozenset = frozenset()) -> dict | None:
        """Найти в ``GET /history/generations`` запрос, ушедший без ответа: та же модель, создан не
        раньше ``since`` (минус запас на часы), id не занят другим запуском. Самый ранний такой —
        наш (запуски идут по одному). ``{id, status, cost}`` или None (ещё не видно / сеть)."""
        import httpx

        start = parse_utc(since)
        try:
            response = httpx.get(f"{self.base_url}/history/generations", headers=self._headers(),
                                 params={"requestType": self.request_type, "limit": HISTORY_LIMIT}, timeout=60)
            if response.status_code >= 400:
                return None
            data = response.json()
        except (httpx.HTTPError, ValueError):
            return None
        items = data.get("items") if isinstance(data, dict) else data
        found = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            rid = str(item["id"])
            if rid in exclude:
                continue
            if item.get("model") and model and str(item["model"]) != model:
                continue
            created = parse_utc(item.get("createdAt") or item.get("created_at"))
            if start is not None and (created is None or (created - start).total_seconds() < -HISTORY_SLACK_SEC):
                continue
            found.append((created, rid, item))
        if not found:
            return None
        found.sort(key=lambda x: (x[0] is None, x[0]))
        _created, rid, item = found[0]
        try:
            cost = float(item["cost"]) if item.get("cost") is not None else None
        except (TypeError, ValueError):
            cost = None
        return {"id": rid, "status": str(item.get("status") or ""), "cost": cost}


class PolzaVideo(PolzaImages):
    """ПЛАТНО. Image-to-video через тот же ``POST /media``. У видеомоделей Polza POST всегда
    отвечает 504 примерно через 60 с, а работа идёт и списывается — id восстанавливается из
    ``/history/generations?requestType=video``. Ссылка на результат живёт 7 дней — качаем сразу."""

    request_type = "video"
    recover_deadline_sec = 20 * 60  # видео делается минуты; в истории появляется не сразу

    def __init__(self, api_key: str | None = None, base_url: str | None = None, timeout: float = 180) -> None:
        super().__init__(api_key, base_url, timeout)

    def _encode(self, path: Path) -> str:
        return start_frame_data_url(path)

    def _ref(self, path: Path) -> dict:
        """Кадр видео — ссылкой из хранилища Polza (``POST /storage/upload``): Hailuo 3 Max с base64
        отвечает «все провайдеры вернули ошибку» (замер 02.11: тот же запрос без кадров — ок).
        Хранилище недоступно — base64, как раньше."""
        import httpx

        try:
            with path.open("rb") as fh:
                response = httpx.post(f"{self.base_url}/storage/upload",
                                      headers={"Authorization": f"Bearer {self.api_key}"},
                                      files={"file": (path.name, fh, "image/jpeg")},
                                      data={"storagePolicy": "TEMP_UPLOAD"}, timeout=120)
            if response.status_code < 400:
                url = (response.json() or {}).get("url")
                if url:
                    return {"type": "url", "data": str(url)}
            _log.warning("aiv video: storage/upload HTTP %s — кадр уйдёт base64", response.status_code)
        except Exception as exc:  # noqa: BLE001 - запасной путь ниже
            _log.warning("aiv video: storage/upload не удался (%s) — кадр уйдёт base64", exc)
        return super()._ref(path)

    def submit(self, params: dict, refs: list[Path]) -> str:
        """``refs`` — ключевые кадры (``images``: старт или старт+конец), ``_reference_images`` — листы.
        У моделей с взаимоисключающими режимами (Seedance 2.x, H3) оба НИКОГДА не уходят вместе:
        конвейер так не просит, а здесь — страховка до денег (листы отбрасываются)."""
        caps = video_capabilities(str(params.get("model") or ""))
        if refs and params.get("_reference_images") and caps.get("exclusive", True):
            _log.warning("aiv video %s: кадры и reference_images вместе запрещены — листы не отправляю",
                         params.get("model"))
            params = {k: v for k, v in params.items() if k != "_reference_images"}
        refs = list(refs)[:max(1, int(caps.get("images") or 1))]
        return super().submit(params, refs)

    def fetch(self, result: dict, dest: Path, params: dict) -> Path:
        import httpx

        url = str(result.get("url") or "")
        with httpx.stream("GET", url, timeout=600, follow_redirects=True) as response:
            if response.status_code >= 400:
                raise ProviderError(f"скачивание видео: HTTP {response.status_code}")
            with dest.open("wb") as fh:
                for chunk in response.iter_bytes(1 << 20):
                    fh.write(chunk)
        return dest


def _result_url(data: dict) -> str | None:
    """Ссылка на результат из ответа /media: data[0].url, data.url или output.url."""
    for key in ("data", "output"):
        item = data.get(key)
        if isinstance(item, list):
            item = item[0] if item else None
        if isinstance(item, dict) and item.get("url"):
            return str(item["url"])
        if isinstance(item, str) and item.startswith(("http", "data:")):
            return item
    return None


def _detail(response: Any) -> str:
    try:
        payload = response.json()
    except ValueError:
        return str(response.status_code)
    if not isinstance(payload, dict):
        return str(response.status_code)
    error = payload.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or response.status_code)[:300]
    return str(error or payload.get("message") or response.status_code)[:300]


# --------------------------------------------------------------------------- #
# Регистрация провайдеров. Новый провайдер (kie.ai, fal.ai, Replicate, Veo по ключу Gemini) —
# свои адаптеры картинок/видео с тем же интерфейсом + ProviderCatalog со своими ценами здесь.
# --------------------------------------------------------------------------- #

CATALOGS["none"] = ProviderCatalog(
    id="none", label="сухой прогон (0 ₽)", paid=False, image_adapter=DryRunImages, video_adapter=DryRunVideo,
    # Сухой прогон изображает платного провайдера по умолчанию — те же тела запросов.
    image_params=polza_image_params, video_params=polza_video_params, markup=0.0)
CATALOGS["polza"] = ProviderCatalog(
    id="polza", label="Polza (платно)", paid=True, image_adapter=PolzaImages, video_adapter=PolzaVideo,
    image_params=polza_image_params, video_params=polza_video_params, video_markup=VIDEO_PRICE_MARKUP,
    tables=lambda: {"image_prices": IMAGE_PRICES, "measured_image": MEASURED_IMAGE_PRICES, "max_refs": MAX_REFS,
                    "video_prices": VIDEO_PRICES, "measured_video": MEASURED_VIDEO_PRICES,
                    "video_limits": VIDEO_LIMITS})


# --------------------------------------------------------------------------- #
# Слот на будущее: GenAPI (gen-api.ru, рубли) / kie.ai — Kling 4.0 reference-to-video. В одном
# запросе — старт, конец И до 7 «элементов» персонажей (режимы НЕ взаимоисключающие). Сетевых
# вызовов нет: когда появится ключ, адаптер дописывает submit/check/fetch (асинхронно: createTask →
# taskId или callback_url), каталог регистрируется в CATALOGS — конвейер не меняется.
# --------------------------------------------------------------------------- #

GENAPI_VIDEO_PRICES: dict[str, dict[str, float]] = {
    # GenAPI, 2026-11-01 (docs/design/ai-video-models-2026-11.md): 5 с 720p 196 ₽, 1080p 294 ₽.
    "kling/v4-reference": {"720p": 39.2, "1080p": 58.8},
}
GENAPI_VIDEO_LIMITS: dict[str, dict[str, Any]] = {
    "kling/v4-reference": {
        "label": "Kling 4.0 reference (старт+конец+листы вместе)", "min": 3, "max": 30,
        "resolutions": ("720p", "1080p"), "audio": True, "audio_param": "sound", "images": 2, "first_last": True,
        "ref_images": 7, "exclusive": False, "negative": "negative_prompt"},
}


def genapi_video_body(params: dict, start: Path | None, end: Path | None, sheets: list[Path]) -> dict:
    """Тело запроса Kling 4.0 reference-to-video (GenAPI/kie): старт, конец и элементы персонажей
    одним запросом, один план (без ``multi_prompt``, ``multi_shots=false``). Только сборка — сети нет."""
    caps = GENAPI_VIDEO_LIMITS.get(str(params.get("model")), {})
    body = {k: v for k, v in params.items() if not k.startswith("_")}
    body["multi_shots"] = False
    if start is not None:
        body["start_image_url"] = str(start)
    if end is not None:
        body["end_image_url"] = str(end)
    elements = [str(p) for p in sheets][:int(caps.get("ref_images") or 7)]
    if elements:
        body["elements"] = [{"image_url": url, "name": f"Element{i}"} for i, url in enumerate(elements, 1)]
    return body


class GenApiVideo:
    """ПЛАТНО (когда будет): адаптер-заготовка. Пока любой submit — отказ до денег."""

    name = "genapi"
    paid = True
    request_type = "video"

    def submit(self, params: dict, refs: list[Path]) -> str:
        raise ProviderError("провайдер genapi (Kling 4.0) ещё не подключён — нужен ключ и адаптер")

    def check(self, remote_id: str) -> dict:
        return {"status": "failed", "error": "genapi не подключён"}

    def fetch(self, result: dict, dest: Path, params: dict) -> Path:
        raise ProviderError("genapi не подключён")


# Не зарегистрированы (выбрать нельзя), но их возможности видны конвейеру (выбор режима, тесты).
FUTURE_CATALOGS: dict[str, ProviderCatalog] = {
    "genapi": ProviderCatalog(
        id="genapi", label="GenAPI (Kling 4.0, рубли) — не подключён", paid=True, image_adapter=PolzaImages,
        video_adapter=GenApiVideo, image_params=polza_image_params, video_params=polza_video_params,
        tables=lambda: {"video_prices": GENAPI_VIDEO_PRICES, "video_limits": GENAPI_VIDEO_LIMITS}),
}


def get_image_provider(name: str) -> ImageProvider:
    """Адаптер картинок провайдера (``none`` — сухой прогон). Тесты подменяют эту функцию."""
    return catalog(name).image_adapter()


def get_video_provider(name: str) -> Any:
    """Адаптер видео провайдера (``none`` — стартовый кадр + наезд). Тесты подменяют эту функцию."""
    return catalog(name).video_adapter()


def get_tts(name: str, model: str | None = None, voice: str | None = None, style: str | None = None) -> Any:
    """Озвучка реплики — машинерия ``app/dubbing.py`` (GeminiTTS бесплатно / PolzaTTS платно /
    DryRunTTS). Тесты подменяют."""
    from app.dubbing import get_tts_provider

    return get_tts_provider(name or "none", model=model or None, voice=voice or None, style=style or None)


def tts_paid(name: str | None) -> bool:
    """Платный ли TTS-провайдер реплик (gemini и none — бесплатно)."""
    from app.dubbing import provider_paid

    return provider_paid(name or "none")


def poll(provider: ImageProvider, remote_id: str, timeout: float, interval: float = POLL_INTERVAL_SEC) -> dict:
    """Опрос до готовности/отказа; не дождались — ``{status: pending}`` (дочитает работник позже).
    Сетевой сбой GET (обрыв TLS, таймаут) — просто «ещё не готово»: спросим снова, POST не повторяем."""
    deadline = time.monotonic() + max(0.0, timeout)
    note = ""
    while True:
        try:
            result = provider.check(remote_id)
        except Exception as exc:  # noqa: BLE001 - только сеть, остальное наверх
            if network_phase(exc) is None:
                raise
            note = f"сеть при опросе: {type(exc).__name__}"
            result = {"status": "pending"}
        if result.get("status") in ("completed", "failed"):
            return result
        if time.monotonic() >= deadline:
            return {"status": "pending", **({"note": note} if note else {})}
        time.sleep(interval)
