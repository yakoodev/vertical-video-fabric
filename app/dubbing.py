"""Озвучка RU переведённого клипа (закадровый перевод) — ПЛАТНАЯ, только с явного согласия.

Что делает: русские фразы перевода (``app/translation.py``: строки субтитров с окнами
оригинальных реплик) → речь по фразе через TTS → каждая фраза подгоняется под своё окно
→ одна дорожка на весь клип → «Файл для монтажа» (audio) → audio-слои ``dub_ru_*`` клипа
поверх оригинала. Оригинальный голос под фразами приглушается тем же механизмом, что у
звуков-слоёв (``props.duck`` → ``motion_render.AUDIO_DUCK_VOLUME``, ≈ −10 дБ): так звучит
обычный русский закадровый перевод — оригинал слышно, но тихо.

Слои — по группам фраз (пауза между фразами < ``GROUP_GAP_SEC`` — одна группа): каждая
группа — отдельный слой со своим ``offset`` в общей дорожке. Вырезы и переходы двигают
слой целиком — фразы остаются на своей речи; ducking — только пока звучит перевод.

Подгонка фразы под окно (``fit_plan``): окно — от начала реплики до начала следующей
(не дальше ``MAX_SPILL_SEC`` за конец реплики). Длиннее окна — ускоряем ``atempo`` до
``TEMPO_MAX`` (1.25); всё ещё длиннее — обрезаем с коротким затуханием и пишем в отчёт
``overflow_sec`` (лучше сократить перевод этой строки). Короче окна — по умолчанию
просто тишина после фразы (замедление речи звучит хуже); ``stretch=True`` — замедлить
до ``TEMPO_MIN`` (0.8).

Деньги. ДО синтеза всегда считается оценка: символы × цена провайдера за символ
(``PRICES_RUB_PER_CHAR`` — цены Polza из GET /models, их надо перепроверять). Синтез
запускается, только если: провайдер бесплатный (``none`` — сухой прогон тоном), ИЛИ
озвучка включена владельцем (``DUBBING_ENABLED=1``), передан ``confirm=true`` и оценка
не больше бюджета запуска (``budget_rub``, по умолчанию 200 ₽, и не выше
``DUBBING_MAX_BUDGET_RUB``). Чат агента дополнительно спрашивает владельца карточкой.

Бесплатно: ``gemini`` — Gemini TTS (``GEMINI_TTS_MODEL``, по умолчанию gemini-3.8-flash-tts) по
ключу Gemini, который у сервиса уже есть (GEMINI_API_KEY; базовый адрес и прокси — как у анализа).
На бесплатном тарифе ключа — 0 ₽, только лимиты запросов (429 → ждём и повторяем). Голоса —
``GEMINI_TTS_VOICES``; ``style`` («говори весело, быстро») — указание к речи строкой перед текстом.
Бесплатным провайдерам (``gemini``, ``none``) confirm, бюджет и DUBBING_ENABLED не нужны: синтез
сразу. ``tts_preview`` — проба голоса короткой фразой (прослушать перед выбором).
"""

from __future__ import annotations

import base64
import hashlib
import logging
import math
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from app.settings import settings

_log = logging.getLogger(__name__)

DEFAULT_PROVIDER = "polza"  # владелец ещё не выбрал; бесплатный вариант — "gemini"
PROVIDERS = ("polza", "gemini", "none")
# Платит ли провайдер деньгами: бесплатным не нужны confirm, бюджет и DUBBING_ENABLED.
PAID_PROVIDERS = frozenset({"polza"})
_ALIASES = {"dry-run": "none", "dry": "none", "mock": "none", "google": "gemini", "gemini-tts": "gemini"}

# Gemini TTS. ПРОВЕРЕНО 2026-11-01 по ai.google.dev (speech-generation, pricing): у
# gemini-3.8-flash-tts и -lite-tts на бесплатном тарифе «Free of charge», русский поддерживается.
# На ПЛАТНОМ тарифе ключа (биллинг включён) — $9 / 1 млн выходных токенов, 25 токенов на секунду
# звука ≈ $0.0135 за минуту речи (копейки; в оценке 0 ₽). Модель по умолчанию —
# settings.gemini_tts_model (env GEMINI_TTS_MODEL), голос — settings.gemini_tts_voice.
GEMINI_TTS_MODELS = ("gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts", "gemini-3.1-flash-tts-preview")
GEMINI_TTS_VOICES = (
    "Zephyr", "Puck", "Charon", "Kore", "Fenrir", "Leda", "Orus", "Aoede", "Callirrhoe", "Autonoe",
    "Enceladus", "Iapetus", "Umbriel", "Algieba", "Despina", "Erinome", "Algenib", "Rasalgethi",
    "Laomedeia", "Achernar", "Alnilam", "Schedar", "Gacrux", "Pulcherrima", "Achird", "Zubenelgenubi",
    "Vindemiatrix", "Sadachbia", "Sadaltager", "Sulafat",
)
GEMINI_TTS_RATE = 24000  # сырой PCM: s16le, моно, 24 кГц (если пришёл не WAV)
GEMINI_TTS_TRIES = 6  # 429 (лимит бесплатного тарифа) и 5xx — повторяем с паузой
GEMINI_TTS_BACKOFF_SEC = (4.0, 10.0, 20.0, 40.0, 60.0)
GEMINI_TTS_MAX_WAIT_SEC = 65.0  # дольше одной паузы не ждём (минутный лимит)
MAX_STYLE_CHARS = 300
PREVIEW_TEXT = "Привет! Так звучит мой голос — послушай и скажи, подходит ли он."
PREVIEW_MAX_CHARS = 300
PREVIEW_BUDGET_RUB = 5.0  # потолок одной платной пробы голоса
DEFAULT_BUDGET_RUB = 200.0
# Цена TTS Polza, ₽ за символ. ПРОВЕРЕНО 2026-11-01 — цены меняются: перед крупной озвучкой
# перепроверь и поправь здесь (оценка не должна занижать).
#
# * Прайс: GET /api/v1/models → top_provider.pricing.tts_per_million_characters (бесплатно).
#   eleven-v4 там 2 278.8 ₽ / 1 млн символов.
# * ЗАМЕР: настоящий прогон 2026-11-01 (3 фразы, 90 символов кириллицы, eleven-v4, голос brian)
#   стоил 0.4193 ₽ (usage.cost_rub в ответе = списание с баланса) — 4 659 ₽ / 1 млн символов,
#   в ~2.04 раза дороже прайса. Поэтому eleven-v4 — по замеру, остальные модели — прайс ×
#   PRICE_MARKUP (не мерили; запас, чтобы не занизить). Фактическая цена каждого запуска
#   пишется из usage.cost_rub провайдера в отчёт (dub.cost_rub, cost_source: provider).
PRICE_CHECKED = "2026-11-01"
PRICE_MARKUP = 2.05
PRICES_RUB_PER_CHAR: dict[str, float] = {
    "elevenlabs/eleven-v4": 0.4193 / 90,  # замер
    "openai/tts-1": 1745.22 / 1e6 * PRICE_MARKUP,
    "openai/tts-1-hd": 3490.44 / 1e6 * PRICE_MARKUP,
    "microsoft/mai-voice-2": 2506.70 / 1e6 * PRICE_MARKUP,
    "google/gemini-3.8-flash-tts": 2443.308 / 1e6 * PRICE_MARKUP,
    "minimax/speech-2.8-hd": 11394.08 / 1e6 * PRICE_MARKUP,
}
MEASURED_MODELS = ("elevenlabs/eleven-v4",)
FALLBACK_PRICE_RUB_PER_CHAR = 11394.08 / 1e6 * PRICE_MARKUP
MAX_PHRASE_CHARS = 5000  # предел input у /audio/speech

TEMPO_MIN = 0.8
TEMPO_MAX = 1.25
MAX_SPILL_SEC = 0.5
NEXT_GAP_SEC = 0.05
GROUP_GAP_SEC = 0.8
TRIM_FADE_SEC = 0.06
LAYER_PREFIX = "dub_ru"
# Сухой прогон: тон длиной как русская речь (~14 символов в секунду).
DRY_CHARS_PER_SEC = 14.0


class DubError(ValueError):
    """Озвучка не запущена: выключена, нет согласия, дороже бюджета, нет перевода."""


# --------------------------------------------------------------------------- #
# Провайдеры TTS
# --------------------------------------------------------------------------- #


@dataclass
class Speech:
    path: Path
    cost_rub: float | None = None
    raw_usage: dict = field(default_factory=dict)


class TTSProvider(Protocol):
    name: str
    paid: bool
    model: str
    voice: str

    def price_rub_per_char(self) -> float: ...

    def synthesize(self, text: str, out_path: Path) -> Speech: ...


class DryRunTTS:
    """Бесплатный сухой прогон: вместо речи — тихий тон длиной как русская фраза.

    Проверить окна, подгонку и ducking на слух, не тратя ни рубля."""

    name = "none"
    paid = False

    def __init__(self, model: str = "dry-run", voice: str = "tone") -> None:
        self.model, self.voice = model or "dry-run", voice or "tone"

    def price_rub_per_char(self) -> float:
        return 0.0

    def synthesize(self, text: str, out_path: Path) -> Speech:
        length = max(0.3, len(text) / DRY_CHARS_PER_SEC)
        _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
              f"sine=frequency=520:sample_rate=48000:duration={length:.3f}", "-af", "volume=0.25",
              "-ac", "1", str(out_path)])
        return Speech(out_path, 0.0)


class PolzaTTS:
    """Polza ``POST /api/v1/audio/speech`` (синхронно: JSON ``{audio: base64, contentType, usage}``).

    ПЛАТНО: вызывать только из ``dub_clip`` после всех проверок."""

    name = "polza"
    paid = True

    def __init__(self, model: str | None = None, voice: str | None = None, api_key: str | None = None,
                 base_url: str | None = None, timeout: float = 120) -> None:
        self.model = (model or settings.polza_tts_model).strip()
        self.voice = (voice or settings.polza_tts_voice).strip()
        self.api_key = (api_key if api_key is not None else settings.polza_api_key).strip()
        self.base_url = (base_url or settings.polza_base_url).rstrip("/")
        self.timeout = timeout

    def price_rub_per_char(self) -> float:
        return PRICES_RUB_PER_CHAR.get(self.model, FALLBACK_PRICE_RUB_PER_CHAR)

    def synthesize(self, text: str, out_path: Path) -> Speech:
        import httpx

        if not self.api_key:
            raise DubError("POLZA_API_KEY не настроен")
        response = httpx.post(
            f"{self.base_url}/audio/speech",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json={"model": self.model, "input": text[:MAX_PHRASE_CHARS], "voice": self.voice,
                  "response_format": "mp3"},
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Polza TTS {response.status_code}: {_error_detail(response)}")
        ctype = response.headers.get("content-type", "")
        if ctype.startswith("audio/") or ctype.startswith("application/octet-stream"):
            out_path.write_bytes(response.content)
            return Speech(out_path, None)
        data = response.json()
        audio = data.get("audio") if isinstance(data, dict) else None
        if not audio:
            raise RuntimeError("Polza TTS: в ответе нет audio")
        out_path.write_bytes(base64.b64decode(audio))
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        cost = usage.get("cost_rub", usage.get("cost"))
        try:
            cost = float(cost) if cost is not None else None
        except (TypeError, ValueError):
            cost = None
        return Speech(out_path, cost, {k: usage[k] for k in ("characters", "cost_rub", "cost") if k in usage})


class GeminiTTS:
    """Gemini TTS: ``POST {base}/models/{model}:generateContent`` с ``responseModalities: ["AUDIO"]``
    и ``speechConfig.voiceConfig.prebuiltVoiceConfig.voiceName``. Ответ — base64 в
    ``candidates[0].content.parts[].inlineData``: WAV (модели 3.8) или сырой PCM s16le 24 кГц моно
    (``audio/L16;rate=24000``, модели постарше) → ffmpeg → mp3/wav по расширению ``out_path``.

    БЕСПЛАТНО на бесплатном тарифе ключа (0 ₽), но с лимитами: 429 — ждём (``retryDelay`` из
    ответа, иначе ``GEMINI_TTS_BACKOFF_SEC``) и повторяем; дневной лимит — понятная ошибка сразу.
    Ключ, базовый адрес и прокси (при геоблоке) — как у остального Gemini (app/ai/gemini.py)."""

    name = "gemini"
    paid = False

    def __init__(self, model: str | None = None, voice: str | None = None, style: str | None = None,
                 api_key: str | None = None, base_url: str | None = None, timeout: float = 120,
                 sleep: Any = None) -> None:
        self.model = (model or settings.gemini_tts_model).strip()
        self.voice = gemini_voice(voice or settings.gemini_tts_voice)
        self.style = " ".join(str(style or "").split())[:MAX_STYLE_CHARS]
        self.api_key = (api_key if api_key is not None else settings.gemini_api_key).strip()
        self.base_url = (base_url or settings.gemini_base_url).rstrip("/")
        self.timeout = timeout
        self._sleep = sleep or time.sleep

    def price_rub_per_char(self) -> float:
        return 0.0

    def prompt(self, text: str) -> str:
        """Текст для модели: указание к речи («говори весело, быстро») — строкой перед репликой."""
        text = str(text or "").strip()[:MAX_PHRASE_CHARS]
        if not self.style:
            return text
        return f"{self.style.rstrip(' :.')}:\n{text}"

    def payload(self, text: str) -> dict:
        return {
            "contents": [{"role": "user", "parts": [{"text": self.prompt(text)}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": self.voice}}},
            },
        }

    def synthesize(self, text: str, out_path: Path) -> Speech:
        if not self.api_key:
            raise DubError("GEMINI_API_KEY не настроен — бесплатная озвучка Gemini недоступна")
        data = self._generate(self.payload(text))
        audio, mime = gemini_audio(data)
        raw = out_path.with_name(out_path.stem + ".gemini-raw")
        raw.write_bytes(audio)
        try:
            if audio[:4] == b"RIFF" or "wav" in mime:
                src = ["-i", str(raw)]
            else:
                src = ["-f", "s16le", "-ar", str(_pcm_rate(mime)), "-ac", "1", "-i", str(raw)]
            codec = ["-c:a", "libmp3lame", "-b:a", "128k"] if out_path.suffix == ".mp3" else []
            _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *src, *codec, str(out_path)])
        finally:
            raw.unlink(missing_ok=True)
        usage = data.get("usageMetadata") if isinstance(data.get("usageMetadata"), dict) else {}
        return Speech(out_path, 0.0, {k: usage[k] for k in ("promptTokenCount", "candidatesTokenCount",
                                                            "totalTokenCount") if k in usage})

    def _generate(self, payload: dict) -> dict:
        import httpx

        from app.ai import gemini as G

        url = f"{self.base_url}/models/{self.model}:generateContent"
        kwargs = {"headers": {"x-goog-api-key": self.api_key, "Content-Type": "application/json"},
                  "json": payload, "timeout": self.timeout}
        last = ""
        for attempt in range(GEMINI_TTS_TRIES):
            try:
                response = G._request_once("POST", url, kwargs)  # прокси при геоблоке — как у анализа
            except httpx.TransportError as exc:
                last = f"сеть: {type(exc).__name__}"
                if attempt + 1 >= GEMINI_TTS_TRIES:
                    break
                self._sleep(_backoff(attempt))
                continue
            if response.status_code < 400:
                return response.json()
            detail = G._gemini_error_detail(response)
            if response.status_code in (401, 403):
                raise RuntimeError(f"Gemini TTS: ключ не подошёл ({response.status_code}: {detail[:200]})")
            if response.status_code == 429 and _daily_quota(response):
                raise DubError("Gemini TTS: дневной лимит бесплатного тарифа исчерпан — завтра снова или "
                               "другой провайдер (polza — платно)")
            if response.status_code not in (408, 429, 500, 502, 503, 504):
                raise RuntimeError(f"Gemini TTS {response.status_code}: {detail[:300]}")
            last = f"{response.status_code}: {detail[:200]}"
            if attempt + 1 >= GEMINI_TTS_TRIES:
                break
            wait = _retry_wait(response, attempt)
            _log.info("gemini tts %s, жду %.0f с (попытка %s/%s)", response.status_code, wait, attempt + 1,
                      GEMINI_TTS_TRIES)
            self._sleep(wait)
        if last.startswith("429"):
            raise DubError(f"Gemini TTS: лимит запросов бесплатного тарифа ({GEMINI_TTS_TRIES} попыток) — "
                           "повтори через минуту")
        raise RuntimeError(f"Gemini TTS не ответил: {last}")


def gemini_voice(name: str | None) -> str:
    """Голос Gemini по имени без учёта регистра; незнакомый — ошибка со списком."""
    wanted = str(name or "").strip().lower()
    for voice in GEMINI_TTS_VOICES:
        if voice.lower() == wanted:
            return voice
    raise DubError(f"голос Gemini «{name}» не знаю; есть: {', '.join(GEMINI_TTS_VOICES)}")


def gemini_audio(data: dict) -> tuple[bytes, str]:
    """(байты, mime) первого звукового куска ответа generateContent."""
    candidates = data.get("candidates") if isinstance(data, dict) else None
    for cand in candidates or []:
        for part in ((cand or {}).get("content") or {}).get("parts") or []:
            inline = part.get("inlineData") or part.get("inline_data") or {}
            if inline.get("data"):
                mime = str(inline.get("mimeType") or inline.get("mime_type") or "").lower()
                return base64.b64decode(inline["data"]), mime
    reason = next((c.get("finishReason") for c in candidates or [] if isinstance(c, dict)), None)
    block = ((data or {}).get("promptFeedback") or {}).get("blockReason") if isinstance(data, dict) else None
    raise RuntimeError(f"Gemini TTS: в ответе нет звука ({block or reason or 'пустой ответ'})")


def _pcm_rate(mime: str) -> int:
    match = re.search(r"rate=(\d+)", mime or "")
    return int(match.group(1)) if match else GEMINI_TTS_RATE


def _backoff(attempt: int) -> float:
    return GEMINI_TTS_BACKOFF_SEC[min(attempt, len(GEMINI_TTS_BACKOFF_SEC) - 1)]


def _error_details(response: Any) -> list[dict]:
    try:
        error = (response.json() or {}).get("error") or {}
    except ValueError:
        return []
    details = error.get("details") if isinstance(error, dict) else None
    return [d for d in details or [] if isinstance(d, dict)]


def _retry_wait(response: Any, attempt: int) -> float:
    """Пауза перед повтором: retryDelay из RetryInfo («13s»), Retry-After или своя лесенка."""
    for detail in _error_details(response):
        if str(detail.get("@type", "")).endswith("RetryInfo"):
            match = re.match(r"([\d.]+)s", str(detail.get("retryDelay") or ""))
            if match:
                return min(GEMINI_TTS_MAX_WAIT_SEC, float(match.group(1)) + 1.0)
    try:
        header = float(response.headers.get("retry-after"))
    except (TypeError, ValueError):
        header = None
    if header is not None:
        return min(GEMINI_TTS_MAX_WAIT_SEC, max(0.0, header))
    return _backoff(attempt)


def _daily_quota(response: Any) -> bool:
    """429 из-за дневного лимита (quotaId …PerDay…) — ждать минуту бесполезно."""
    for detail in _error_details(response):
        for violation in detail.get("violations") or []:
            if "perday" in str((violation or {}).get("quotaId") or "").lower():
                return True
    return False


def provider_key(name: str | None) -> str:
    key = str(name or DEFAULT_PROVIDER).strip().lower()
    return _ALIASES.get(key, key)


def provider_paid(name: str | None) -> bool:
    """Платный ли провайдер (по имени, без создания): бесплатным не нужны confirm и бюджет."""
    return provider_key(name) in PAID_PROVIDERS


def get_tts_provider(name: str | None, model: str | None = None, voice: str | None = None,
                     style: str | None = None) -> TTSProvider:
    key = provider_key(name)
    if key == "none":
        return DryRunTTS()
    if key == "polza":
        return PolzaTTS(model=model, voice=voice)
    if key == "gemini":
        return GeminiTTS(model=model, voice=voice, style=style)
    raise DubError(f"provider: {', '.join(PROVIDERS)}")


def voice_catalog() -> dict:
    """Что можно выбрать: провайдеры, модели и голоса (для UI и агентов)."""
    return {
        "default_provider": DEFAULT_PROVIDER,
        "providers": [
            {"id": "gemini", "label": "Gemini TTS (бесплатно)", "paid": False,
             "model": settings.gemini_tts_model, "models": list(GEMINI_TTS_MODELS),
             "voice": settings.gemini_tts_voice, "voices": list(GEMINI_TTS_VOICES), "style": True,
             "configured": bool(settings.gemini_api_key)},
            {"id": "polza", "label": "Polza TTS (платно)", "paid": True, "model": settings.polza_tts_model,
             "models": list(PRICES_RUB_PER_CHAR), "voice": settings.polza_tts_voice, "voices": [],
             "style": False, "configured": bool(settings.polza_api_key)},
            {"id": "none", "label": "Сухой прогон (тон)", "paid": False, "model": "dry-run", "models": [],
             "voice": "tone", "voices": [], "style": False, "configured": True},
        ],
    }


# --------------------------------------------------------------------------- #
# Фразы, оценка, подгонка
# --------------------------------------------------------------------------- #


def dub_phrases(payload: dict | None) -> list[dict]:
    """Русские фразы перевода ``{i, start, end, text, speaker?}`` (шкала plain — как субтитры)."""
    payload = payload or {}
    if not isinstance(payload.get("translation"), dict):
        raise DubError("клип не переведён — сначала translate_subtitles (озвучиваем русский перевод)")
    out = []
    for i, line in enumerate(payload.get("lines") or []):
        text = " ".join(str(line.get("text") or "").split())
        try:
            start, end = float(line["start"]), float(line["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if text and end > start:
            out.append({"i": i, "start": round(start, 3), "end": round(end, 3), "text": text[:MAX_PHRASE_CHARS],
                        **({"speaker": line["speaker"]} if line.get("speaker") else {})})
    if not out:
        raise DubError("в переводе нет фраз")
    return out


def budget_cap(budget_rub: float | None) -> float:
    """Бюджет запуска: из запроса (по умолчанию 200 ₽), но не выше DUBBING_MAX_BUDGET_RUB."""
    try:
        wanted = float(budget_rub) if budget_rub is not None else DEFAULT_BUDGET_RUB
    except (TypeError, ValueError):
        wanted = DEFAULT_BUDGET_RUB
    if not math.isfinite(wanted) or wanted < 0:
        wanted = DEFAULT_BUDGET_RUB
    return round(min(wanted, float(settings.dubbing_max_budget_rub)), 2)


def estimate_cost(phrases: list[dict], provider: TTSProvider, budget_rub: float | None = None) -> dict:
    """Оценка ДО синтеза: символы × цена за символ. Ничего не вызывает (бесплатно)."""
    chars = sum(len(p["text"]) for p in phrases)
    price = provider.price_rub_per_char()
    rub = round(chars * price, 2)
    cap = budget_cap(budget_rub)
    out = {
        "provider": provider.name, "model": provider.model, "voice": provider.voice,
        "chars": chars, "phrases": len(phrases),
        "price_rub_per_char": price, "price_checked": PRICE_CHECKED,
        "rub": rub, "budget_rub": cap, "over_budget": bool(provider.paid and rub > cap),
        "enabled": bool(settings.dubbing_enabled or not provider.paid), "paid": provider.paid,
    }
    if provider.paid and provider.model not in PRICES_RUB_PER_CHAR:
        out["note"] = "цены модели нет в таблице — оценка по самой дорогой известной; проверь GET /models"
    elif provider.paid and provider.model not in MEASURED_MODELS:
        out["note"] = f"цена модели не замерена — прайс ×{PRICE_MARKUP:g} (запас)"
    elif provider.name == "gemini":
        out["note"] = ("Gemini TTS — бесплатно на бесплатном тарифе ключа (лимиты запросов); на платном "
                       "тарифе — копейки (≈ $0.0135 за минуту речи)")
    return out


def phrase_windows(phrases: list[dict], total: float) -> list[float]:
    """Конец окна каждой фразы: до начала следующей, не дальше MAX_SPILL_SEC за конец реплики."""
    out = []
    for k, p in enumerate(phrases):
        nxt = phrases[k + 1]["start"] - NEXT_GAP_SEC if k + 1 < len(phrases) else float("inf")
        end = min(p["end"] + MAX_SPILL_SEC, nxt, total if total > 0 else float("inf"))
        out.append(round(max(end, p["start"] + 0.1), 3))
    return out


def fit_plan(duration: float, window: float, stretch: bool = False) -> dict:
    """Как уложить фразу длиной ``duration`` в окно ``window``: tempo, action, overflow_sec."""
    duration, window = max(0.01, float(duration)), max(0.05, float(window))
    ratio = duration / window
    if ratio > 1.0:
        tempo = min(ratio, TEMPO_MAX)
        fitted = duration / tempo
        if fitted > window + 0.01:
            return {"tempo": round(tempo, 4), "action": "trim", "overflow_sec": round(fitted - window, 3)}
        return {"tempo": round(tempo, 4), "action": "tempo", "overflow_sec": 0.0}
    if stretch and ratio >= TEMPO_MIN and ratio < 0.98:
        return {"tempo": round(ratio, 4), "action": "tempo", "overflow_sec": 0.0}
    return {"tempo": 1.0, "action": "pad", "overflow_sec": 0.0}


def fit_args(src: Path, dst: Path, plan: dict, window: float) -> list[str]:
    """ffmpeg: фраза → ровно ``window`` секунд (atempo, обрезка с затуханием, тишина в конце)."""
    chain = ["aresample=48000", "aformat=sample_fmts=fltp:channel_layouts=mono"]
    if abs(plan["tempo"] - 1.0) >= 1e-3:
        chain.append(f"atempo={plan['tempo']:.4f}")
    if plan["action"] == "trim":
        chain += [f"atrim=0:{window:.3f}", f"afade=t=out:st={max(0.0, window - TRIM_FADE_SEC):.3f}:d={TRIM_FADE_SEC}"]
    chain.append(f"apad=whole_dur={window:.3f}")
    return ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-af", ",".join(chain),
            "-t", f"{window:.3f}", "-ar", "48000", "-ac", "1", str(dst)]


def mix_args(pieces: list[tuple[Path, float]], total: float, dst: Path) -> list[str]:
    """ffmpeg: фразы по своим местам (adelay) в одну дорожку длиной клипа."""
    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for path, _start in pieces:
        args += ["-i", str(path)]
    graph = []
    for n, (_path, start) in enumerate(pieces):
        graph.append(f"[{n}:a]adelay=delays={int(round(start * 1000))}:all=1[d{n}]")
    joined = "".join(f"[d{n}]" for n in range(len(pieces)))
    graph.append(f"{joined}amix=inputs={len(pieces)}:duration=longest:normalize=0:dropout_transition=0,"
                 f"apad=whole_dur={total:.3f}[out]")
    codec = ["-c:a", "libmp3lame", "-b:a", "192k"] if dst.suffix == ".mp3" else []
    return args + ["-filter_complex", ";".join(graph), "-map", "[out]", "-t", f"{total:.3f}",
                   "-ar", "48000", "-ac", "2", *codec, str(dst)]


def probe_duration(path: Path) -> float:
    proc = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                           "default=nw=1:nk=1", str(path)], capture_output=True, text=True, check=False)
    try:
        return float((proc.stdout or "0").strip() or 0)
    except ValueError:
        return 0.0


def build_dub_track(phrases: list[dict], tts: TTSProvider, total: float, out_path: Path, work: Path,
                    stretch: bool = False) -> tuple[list[dict], float | None]:
    """Синтез → подгонка → сведение. Возвращает (отчёт по фразам, фактическая цена ₽ или None)."""
    windows = phrase_windows(phrases, total)
    report, fitted = [], []
    spent: float | None = 0.0
    for k, (p, wend) in enumerate(zip(phrases, windows)):
        raw = work / f"p{k:03d}.mp3"
        speech = tts.synthesize(p["text"], raw)
        if speech.cost_rub is None:
            spent = None
        elif spent is not None:
            spent += speech.cost_rub
        length = probe_duration(speech.path)
        window = round(wend - p["start"], 3)
        plan = fit_plan(length, window, stretch)
        dst = work / f"f{k:03d}.wav"
        _run(fit_args(speech.path, dst, plan, window))
        fitted.append((dst, p["start"]))
        report.append({"i": p["i"], "start": p["start"], "end": p["end"], "window_end": wend, "text": p["text"],
                       "chars": len(p["text"]), "duration": round(length, 3), **plan})
    _run(mix_args(fitted, total, out_path))
    return report, (round(spent, 4) if spent is not None else None)


def dub_groups(report: list[dict], gap: float = GROUP_GAP_SEC) -> list[tuple[float, float]]:
    """Фразы подряд (пауза < gap) — одна группа ``(начало, конец окна)`` в шкале plain."""
    groups: list[list[float]] = []
    for item in report:
        a, b = float(item["start"]), float(item["window_end"])
        if groups and a - groups[-1][1] < gap:
            groups[-1][1] = max(groups[-1][1], b)
        else:
            groups.append([a, b])
    return [(round(a, 3), round(b, 3)) for a, b in groups]


def _minus_cuts(start: float, end: float, cuts: list[dict]) -> list[tuple[float, float]]:
    """[start, end] без вырезов клипа: слой не должен перекрывать вырез — рендер сдвинул бы его
    целиком, и речь после выреза отстала бы на длину выреза."""
    spans = [(start, end)]
    for cut in sorted(cuts or [], key=lambda c: float(c.get("start", 0))):
        try:
            ca, cb = float(cut["start"]), float(cut["end"])
        except (KeyError, TypeError, ValueError):
            continue
        out = []
        for a, b in spans:
            if cb <= a or ca >= b:
                out.append((a, b))
                continue
            if ca > a:
                out.append((a, ca))
            if cb < b:
                out.append((cb, b))
        spans = out
    return [(a, b) for a, b in spans if b - a >= 0.1]


def dub_layers(groups: list[tuple[float, float]], asset_id: int, clock: Any, cuts: list[dict] | None = None) -> list[dict]:
    """audio-слои ``dub_ru_N``: начало/конец — шкала clip (с наплывами), offset — место куска
    группы в дорожке (шкала plain, как субтитры). ``cuts`` — ``render_settings.cuts`` (plain):
    группа через вырез — двумя слоями по обе стороны выреза."""
    layers = []
    for a, b in groups:
        for pa, pb in _minus_cuts(a, b, cuts or []):
            start = clock.plain_to_clip(pa)
            end = max(start + 0.1, clock.plain_to_clip(pb))
            n = len(layers) + 1
            layers.append({
                "id": f"{LAYER_PREFIX}_{n}", "type": "audio", "name": f"Озвучка RU {n}", "start": round(start, 3),
                "end": round(end, 3),
                "props": {"asset_id": int(asset_id), "mode": "track", "volume": 1.0,
                          "offset": round(pa, 3), "duck": True, "fadeIn": 0.02, "fadeOut": 0.05},
            })
    return layers


def is_dub_layer(layer: Any) -> bool:
    return isinstance(layer, dict) and str(layer.get("id") or "").startswith(LAYER_PREFIX)


# --------------------------------------------------------------------------- #
# Главный вход
# --------------------------------------------------------------------------- #


def dub_clip(store: Any, clip_plan_id: int, provider: str | None = None, voice: str | None = None,
             model: str | None = None, confirm: bool = False, budget_rub: float | None = None,
             stretch: bool = False, remove: bool = False, tts: TTSProvider | None = None,
             style: str | None = None) -> dict:
    """Оценить или сделать озвучку RU клипа. Платный провайдер без ``confirm`` — ТОЛЬКО оценка
    (ничего не платим); бесплатный (``gemini``, ``none``) — синтез сразу, confirm не нужен.

    ``tts`` — готовый провайдер (тесты подменяют). ``remove`` — снять слои озвучки. ``style`` —
    указание к речи для Gemini («говори весело, быстро»)."""
    from app import modules
    from app.timebase import ClipClock

    modules.require("dubbing", store)
    plan = store.get_clip_plan(clip_plan_id)
    payload = store.get_clip_plan_subtitles(clip_plan_id) or {}
    if remove:
        render = dict(plan.get("render_settings") or {})
        layers = [l for l in render.get("layers") or [] if not is_dub_layer(l)]
        removed = len(render.get("layers") or []) - len(layers)
        if removed:
            store.set_clip_plan_render_settings(clip_plan_id, {**render, "layers": layers})
        tr = payload.get("translation")
        if isinstance(tr, dict) and tr.get("dub"):
            store.set_clip_plan_subtitles(clip_plan_id, {**payload, "translation": {k: v for k, v in tr.items()
                                                                                   if k != "dub"}})
        return {"ok": True, "status": "removed", "layers_removed": removed}

    phrases = dub_phrases(payload)
    tts = tts or get_tts_provider(provider, model=model, voice=voice, style=style)
    estimate = estimate_cost(phrases, tts, budget_rub)
    if not confirm and tts.paid:
        note = (f"оценка: {estimate['chars']} символов ≈ {estimate['rub']:.2f} ₽ ({tts.model}, цена от "
                f"{PRICE_CHECKED}); синтез — confirm=true, только с согласия владельца")
        if tts.paid and not settings.dubbing_enabled:
            note += "; озвучка выключена (DUBBING_ENABLED=0) — включает владелец"
        if estimate["over_budget"]:
            note += f"; дороже бюджета {estimate['budget_rub']:.0f} ₽ — откажу"
        return {"ok": True, "status": "estimate", "estimate": estimate, "note": note}
    if tts.paid:
        if not settings.dubbing_enabled:
            raise DubError("озвучка выключена (DUBBING_ENABLED=0): платный синтез включает только владелец")
        if estimate["over_budget"]:
            raise DubError(f"оценка {estimate['rub']:.2f} ₽ больше бюджета {estimate['budget_rub']:.2f} ₽ — не запускаю")

    clock = ClipClock.for_plan(plan)
    total = round(clock.plain_total, 3)
    work = settings.tmp_dir / f"dub-{uuid4().hex}"
    work.mkdir(parents=True, exist_ok=True)
    try:
        from app.montage_assets import assets_dir, probe_asset

        track = work / "dub.mp3"
        report, spent = build_dub_track(phrases, tts, total, track, work, stretch)
        dest = assets_dir() / f"{uuid4().hex}-dub-ru-{clip_plan_id}.mp3"
        shutil.move(str(track), dest)
        meta = probe_asset(dest)
        asset = store.create_montage_asset(
            kind="audio", label=f"Озвучка RU · клип {clip_plan_id}"[:120],
            description=f"TTS {tts.name}/{tts.model}, голос {tts.voice}, {estimate['chars']} символов"[:500],
            tags="озвучка", file_path=str(dest), original_filename=dest.name, mime_type="audio/mpeg",
            size_bytes=dest.stat().st_size, **{**meta, "width": 0, "height": 0},
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)

    render = dict(plan.get("render_settings") or {})
    layers = dub_layers(dub_groups(report), int(asset["id"]), clock, render.get("cuts"))
    kept = [l for l in render.get("layers") or [] if not is_dub_layer(l)]
    store.set_clip_plan_render_settings(clip_plan_id, {**render, "layers": kept + layers})
    overflows = [r for r in report if r["action"] == "trim"]
    dub = {
        "provider": tts.name, "model": tts.model, "voice": tts.voice, "asset_id": int(asset["id"]),
        **({"style": tts.style} if getattr(tts, "style", "") else {}), "paid": bool(tts.paid),
        "layers": [l["id"] for l in layers], "chars": estimate["chars"],
        "estimate_rub": estimate["rub"], "cost_rub": spent if spent is not None else estimate["rub"],
        "cost_source": "provider" if spent is not None else "estimate",
        "overflows": len(overflows), "phrases": report, "created_at": round(time.time()),
    }
    fresh = store.get_clip_plan_subtitles(clip_plan_id) or payload
    if isinstance(fresh.get("translation"), dict):
        store.set_clip_plan_subtitles(clip_plan_id, {**fresh, "translation": {**fresh["translation"], "dub": dub}})
    note = f"озвучка: {len(layers)} слоёв audio (dub_ru_*), оригинал под фразами приглушён (duck)"
    if overflows:
        note += (f"; {len(overflows)} фраз не влезли даже при ×{TEMPO_MAX} и обрезаны — сократи их перевод "
                 "(edit_subtitle_lines) и озвучь снова")
    return {"ok": True, "status": "done", "estimate": estimate, "dub": {k: v for k, v in dub.items()
                                                                          if k != "phrases"},
            "overflow_phrases": [{"i": r["i"], "text": r["text"], "overflow_sec": r["overflow_sec"]} for r in overflows],
            "note": note}


def tts_preview(store: Any, provider: str | None = None, voice: str | None = None, model: str | None = None,
                style: str | None = None, text: str | None = None, confirm: bool = False,
                tts: TTSProvider | None = None) -> dict:
    """Проба голоса: короткая фраза → mp3 в библиотеке (audio, тег «проба голоса») → ``url``,
    чтобы владелец прослушал голос до выбора. Бесплатный провайдер (``gemini``, ``none``) — сразу;
    платный — без ``confirm`` только оценка, с confirm — при DUBBING_ENABLED=1 и не дороже
    ``PREVIEW_BUDGET_RUB``. Та же фраза тем же голосом — тот же файл (повторно не синтезируем)."""
    phrase = " ".join(str(text or PREVIEW_TEXT).split())[:PREVIEW_MAX_CHARS] or PREVIEW_TEXT
    tts = tts or get_tts_provider(provider, model=model, voice=voice, style=style)
    style_text = str(getattr(tts, "style", "") or "")
    rub = round(len(phrase) * tts.price_rub_per_char(), 2)
    cap = min(PREVIEW_BUDGET_RUB, budget_cap(None))
    info = {"provider": tts.name, "model": tts.model, "voice": tts.voice, "text": phrase, "chars": len(phrase),
            "paid": bool(tts.paid), "estimate_rub": rub, **({"style": style_text} if style_text else {})}
    if tts.paid:
        if not confirm:
            return {"ok": True, "status": "estimate", **info,
                    "note": f"проба платная ≈ {rub:.2f} ₽ ({tts.model}); синтез — confirm=true, только с "
                            "согласия владельца. Бесплатно — provider gemini"}
        if not settings.dubbing_enabled:
            raise DubError("озвучка выключена (DUBBING_ENABLED=0): платную пробу включает только владелец")
        if rub > cap:
            raise DubError(f"проба ≈ {rub:.2f} ₽ дороже потолка пробы {cap:.2f} ₽ — сократи фразу")
    key = "|".join([tts.name, tts.model, tts.voice, style_text, phrase])
    filename = f"tts-preview-{hashlib.sha1(key.encode('utf-8')).hexdigest()[:16]}.mp3"
    row = store.db.query_one("SELECT id FROM montage_assets WHERE original_filename = ? ORDER BY id DESC LIMIT 1",
                             (filename,))
    if row:
        asset = store.get_montage_asset(int(row["id"]))
        if Path(asset["file_path"]).is_file():
            return {"ok": True, "status": "done", "cached": True, **info, "cost_rub": 0.0,
                    "asset_id": int(asset["id"]), "url": asset["url"],
                    "duration": round(float(asset.get("duration_sec") or 0), 2)}
    from app.montage_assets import assets_dir, probe_asset

    work = settings.tmp_dir / f"tts-preview-{uuid4().hex}"
    work.mkdir(parents=True, exist_ok=True)
    try:
        speech = tts.synthesize(phrase, work / "preview.mp3")
        dest = assets_dir() / f"{uuid4().hex}-{filename}"
        shutil.move(str(speech.path), dest)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    meta = probe_asset(dest)
    label = f"Проба голоса: {tts.name} · {tts.voice}" + (f" · {style_text}" if style_text else "")
    asset = store.create_montage_asset(
        kind="audio", label=label[:120], description=f"TTS {tts.name}/{tts.model}, голос {tts.voice}: {phrase}"[:500],
        tags="проба голоса", file_path=str(dest), original_filename=filename, mime_type="audio/mpeg",
        size_bytes=dest.stat().st_size, **{**meta, "width": 0, "height": 0},
    )
    cost = speech.cost_rub if speech.cost_rub is not None else rub
    return {"ok": True, "status": "done", "cached": False, **info, "cost_rub": round(float(cost), 4),
            "asset_id": int(asset["id"]), "url": asset["url"],
            "duration": round(float(meta.get("duration_sec") or 0), 2),
            "note": "прослушай по url; понравился голос — dub_clip / голос персонажа с тем же provider и voice"}


def _run(args: list[str]) -> None:
    proc = subprocess.run(args, capture_output=True, text=True, timeout=600, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg (озвучка): {(proc.stderr or proc.stdout or '')[-300:]}")


def _error_detail(response: Any) -> str:
    try:
        payload = response.json()
    except ValueError:
        return str(response.status_code)
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        return str(error.get("message") or response.status_code)[:300]
    return str(error or response.status_code)[:300]
