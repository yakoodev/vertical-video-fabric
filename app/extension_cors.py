"""CORS для своего расширения браузера (browser-extension/): только список аккаунтов и запись cookie.

Расширение шлёт ``Authorization: Bearer`` — браузер сперва спрашивает разрешение (preflight OPTIONS). Отвечаем только
расширению с известным адресом (``BROWSER_EXTENSION_ORIGINS``; по умолчанию — Chrome/Edge-расширение с ключом из его
manifest.json) и только на ``/api/accounts``. Куки входа сервиса не разрешаем (без Allow-Credentials): расширение ходит
с токеном. Любой другой адрес и источник — без заголовков CORS, как раньше.

Firefox даёт расширению случайный адрес ``moz-extension://<uuid>`` — там расширение просит у браузера доступ к адресу
сервиса и ходит без CORS. Свой uuid можно дописать в ``BROWSER_EXTENSION_ORIGINS``."""

from __future__ import annotations

from typing import Any, Callable

EXTENSION_PATHS = frozenset({"/api/accounts"})
ALLOWED_METHODS = "GET, POST"
ALLOWED_HEADERS = "authorization, content-type"
PREFLIGHT_MAX_AGE = "600"


class ExtensionCors:
    """Чистый ASGI (как ``ModuleGate``): снаружи всех прослоек, чтобы и 401/423 доходили до расширения с заголовком."""

    def __init__(self, app: Any, get_origins: Callable[[], tuple[str, ...]]) -> None:
        self.app = app
        self.get_origins = get_origins

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        origin = _allowed_origin(scope, self.get_origins())
        if not origin:
            await self.app(scope, receive, send)
            return
        if scope.get("method") == "OPTIONS" and _header(scope, b"access-control-request-method"):
            await send({"type": "http.response.start", "status": 204, "headers": [
                (b"access-control-allow-origin", origin.encode()),
                (b"access-control-allow-methods", ALLOWED_METHODS.encode()),
                (b"access-control-allow-headers", ALLOWED_HEADERS.encode()),
                (b"access-control-max-age", PREFLIGHT_MAX_AGE.encode()),
                (b"vary", b"Origin"),
            ]})
            await send({"type": "http.response.body", "body": b""})
            return

        async def send_with_cors(message: dict) -> None:
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"access-control-allow-origin"]
                headers += [(b"access-control-allow-origin", origin.encode()), (b"vary", b"Origin")]
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_cors)


def _allowed_origin(scope: dict, origins: tuple[str, ...]) -> str:
    if scope.get("type") != "http" or scope.get("path") not in EXTENSION_PATHS:
        return ""
    origin = _header(scope, b"origin")
    return origin if origin and origin in origins else ""


def _header(scope: dict, name: bytes) -> str:
    for key, value in scope.get("headers") or []:
        if key.lower() == name:
            return value.decode("latin-1").strip()
    return ""
