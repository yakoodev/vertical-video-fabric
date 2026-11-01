"""Провайдеры картинок и цены ИИ-видео (docs/design/ai-video-projects.md §2).

Интерфейс один на всех — асинхронный, как у Polza: ``submit`` → id запроса (его пишут в БД ДО
опроса), ``check`` → статус, ``fetch`` → файл. Поэтому сухой прогон (``none``) идёт тем же путём,
что и платный: весь конвейер и UI проверяются бесплатно.

* ``DryRunImages`` (``none``, 0 ₽) — рисует заглушку ffmpeg: градиент + подпись (что за картинка,
  кто в кадре). Никакой сети.
* ``PolzaImages`` (``polza``, ПЛАТНО) — ``POST /media`` ``{model, input: {prompt, aspect_ratio,
  image_resolution, images: [{type: base64, data}]}}`` → ``{id}``; ``GET /media/<id>`` → ``status
  completed`` → ``data[0].url`` (ссылка истекает — скачиваем сразу). Референсы уходят data-URL
  (jpeg ≤ 1024 px) — ПРОВЕРИТЬ первым настоящим запуском, что Polza их принимает (в каталоге
  поле ``images`` описано как «URL изображений»); иначе нужен публичный адрес файлов.

Цены — прайс каталога Polza ``GET /models`` (снимок ``D:\\vvf-offload\\tmp\\videogen\\polza_models.json``,
2026-11-01) × ``PRICE_MARKUP``: замер TTS показал списание в ~2.04 раза выше прайса, картинки
ещё не мерили. ПРОВЕРИТЬ ЗАМЕРОМ: после первого настоящего запуска каждой модели поставить
фактическую цену (``usage.cost_rub`` или разница баланса) и внести модель в ``MEASURED_IMAGE_MODELS``.
"""

from __future__ import annotations

import base64
import hashlib
import subprocess
import time
from pathlib import Path
from typing import Any, Protocol

from app.settings import settings

PRICE_CHECKED = "2026-11-01"
PRICE_MARKUP = 2.0  # запас к прайсу, пока цена модели не замерена (как в dubbing)
DEFAULT_IMAGE_MODEL = "google/gemini-nano-banana-2.1"
# Прайс ₽ за картинку по разрешению ("*" — одна цена). ПРОВЕРИТЬ ЗАМЕРОМ.
IMAGE_PRICES: dict[str, dict[str, float]] = {
    "google/gemini-nano-banana-2.1": {"1K": 3.0, "2K": 4.5, "4K": 6.75},
    "bytedance/seedream-4.5": {"*": 5.0},
    "openai/gpt-image-2.5-flare": {"1K": 4.0, "2K": 7.0, "4K": 11.0},
    "qwen/image-2.1": {"1K": 3.0, "2K": 6.0},
    "black-forest-labs/flux.2-pro": {"1K": 5.0, "2K": 7.0},
}
# Замеренные: ₽ за картинку как есть, без запаса. Пока пусто.
MEASURED_IMAGE_PRICES: dict[str, dict[str, float]] = {}
MAX_REFS = {"google/gemini-nano-banana-2.1": 10, "bytedance/seedream-4.5": 14, "openai/gpt-image-2.5-flare": 16,
            "qwen/image-2.1": 10, "black-forest-labs/flux.2-pro": 8}
RESOLUTION = {"draft": "1K", "quality": "2K"}
REF_MAX_PX = 1024
POLL_INTERVAL_SEC = 3.0


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


def image_price(model: str, resolution: str) -> dict:
    """Цена одной картинки, ₽: {rub, list_rub, measured, note}."""
    if model in MEASURED_IMAGE_PRICES:
        tiers = MEASURED_IMAGE_PRICES[model]
        rub = tiers.get(resolution, tiers.get("*", max(tiers.values())))
        return {"rub": round(rub, 2), "list_rub": rub, "measured": True, "note": "замер"}
    tiers = IMAGE_PRICES.get(model)
    if not tiers:
        top = max(max(t.values()) for t in IMAGE_PRICES.values())
        return {"rub": round(top * PRICE_MARKUP, 2), "list_rub": top, "measured": False,
                "note": "модели нет в таблице — оценка по самой дорогой"}
    rub = tiers.get(resolution, tiers.get("*", max(tiers.values())))
    return {"rub": round(rub * PRICE_MARKUP, 2), "list_rub": rub, "measured": False,
            "note": f"прайс ×{PRICE_MARKUP:g} (не замерено)"}


# --------------------------------------------------------------------------- #
# Интерфейс
# --------------------------------------------------------------------------- #


class ImageProvider(Protocol):
    name: str
    paid: bool

    def submit(self, params: dict, refs: list[Path]) -> str: ...

    def check(self, remote_id: str) -> dict: ...

    def fetch(self, result: dict, dest: Path, params: dict) -> Path: ...


def model_params(model: str, prompt: str, aspect: str, resolution: str) -> dict:
    """Тело запроса по семейству модели (у seedream своё: quality и обязательный aspect_ratio)."""
    if model.startswith("bytedance/seedream"):
        return {"model": model, "prompt": prompt[:3000], "aspect_ratio": aspect,
                "quality": "high" if resolution in ("2K", "4K") else "basic"}
    if model.startswith("black-forest-labs/") or model.startswith("qwen/"):
        return {"model": model, "prompt": prompt[:20000], "aspect_ratio": aspect, "image_resolution": resolution}
    return {"model": model, "prompt": prompt[:20000], "aspect_ratio": aspect, "image_resolution": resolution,
            "output_format": "jpeg"}


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


_POLICY_WORDS = ("moderat", "policy", "nsfw", "safety", "content_filter", "prohibited", "blocked")


class PolzaImages:
    """ПЛАТНО. Вызывать только из ``aivideo_gen`` после всех проверок бюджета и одобрения."""

    name = "polza"
    paid = True

    def __init__(self, api_key: str | None = None, base_url: str | None = None, timeout: float = 120) -> None:
        self.api_key = (api_key if api_key is not None else settings.polza_api_key).strip()
        self.base_url = (base_url or settings.polza_base_url).rstrip("/")
        self.timeout = timeout

    def _headers(self) -> dict:
        if not self.api_key:
            raise ProviderError("POLZA_API_KEY не настроен")
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def submit(self, params: dict, refs: list[Path]) -> str:
        import httpx

        # POST /media: параметры модели — внутри input (у /images/generations aspect_ratio
        # молча игнорировался — приходил квадрат 1024×1024 вместо 9:16).
        fields = {k: v for k, v in params.items() if not k.startswith("_") and k != "model"}
        if refs:
            fields["images"] = [{"type": "base64", "data": ref_data_url(p)} for p in refs]
        body = {"model": params.get("model"), "input": fields}
        response = httpx.post(f"{self.base_url}/media", headers=self._headers(), json=body,
                              timeout=self.timeout)
        if response.status_code >= 400:
            detail = _detail(response)
            raise ProviderError(f"Polza {response.status_code}: {detail}",
                                policy=any(w in detail.lower() for w in _POLICY_WORDS), status=response.status_code)
        data = response.json() if response.content else {}
        remote = data.get("requestId") or data.get("request_id") or data.get("id")
        if remote:
            return str(remote)
        items = data.get("data") if isinstance(data.get("data"), list) else []
        if items and isinstance(items[0], dict) and items[0].get("url"):
            return "url:" + str(items[0]["url"])  # синхронный ответ — ссылка сразу
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


def get_image_provider(name: str) -> ImageProvider:
    """``none`` — сухой прогон, ``polza`` — платно. Тесты подменяют эту функцию."""
    key = str(name or "none").strip().lower()
    if key in ("none", "dry", "dry-run", "mock"):
        return DryRunImages()
    if key == "polza":
        return PolzaImages()
    raise ProviderError(f"провайдер {name!r}: none или polza")


def get_tts(name: str, model: str | None = None, voice: str | None = None) -> Any:
    """Озвучка реплики — машинерия ``app/dubbing.py`` (PolzaTTS / DryRunTTS). Тесты подменяют."""
    from app.dubbing import DryRunTTS, PolzaTTS

    if str(name or "none").strip().lower() in ("none", "dry", "dry-run", "mock"):
        return DryRunTTS()
    return PolzaTTS(model=model or None, voice=voice or None)


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
