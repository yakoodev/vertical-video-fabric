"""Сжатие текста и кэш сборки: страницы сервиса грузятся через домен, а не с localhost.

Только текст: видео, картинки и ответы на Range не трогаем (плеер и докачка видят точные
байты файла), поток ``text/event-stream`` чата тоже: он в списке не значится, а любой другой
кусочный ответ сбрасывается сразу, поэтому сжатие его не задерживает.
"""

from __future__ import annotations

import zlib
from typing import Any

from anyio import to_thread
from starlette.datastructures import Headers, MutableHeaders
from starlette.staticfiles import StaticFiles

COMPRESSIBLE = {
    "application/json",
    "application/javascript",
    "text/javascript",
    "text/css",
    "text/html",
    "image/svg+xml",
}
MIN_SIZE = 1024
LEVEL = 5
# Больше этого — не в event loop: остальные запросы не должны ждать сжатия.
THREAD_FROM = 128 * 1024
IMMUTABLE = "public, max-age=31536000, immutable"
# index.html ссылается на хэши сборки: его всегда проверяют заново, иначе после выкладки — белый экран.
REVALIDATE = "no-cache"


class _Gzip:
    """Поток gzip: каждый кусок сбрасывается сразу, чтобы медленный ответ не копился в буфере."""

    def __init__(self) -> None:
        self._z = zlib.compressobj(LEVEL, zlib.DEFLATED, 16 + zlib.MAX_WBITS)

    def push(self, data: bytes, last: bool) -> bytes:
        out = self._z.compress(data)
        return out + self._z.flush(zlib.Z_FINISH if last else zlib.Z_SYNC_FLUSH)


def _accepts_gzip(value: str) -> bool:
    for item in value.split(","):
        name, _, params = item.strip().partition(";")
        if name.strip().lower() in ("gzip", "*"):
            return params.replace(" ", "").lower() not in ("q=0", "q=0.0", "q=0.00", "q=0.000")
    return False


class TextGzipMiddleware:
    """ASGI-прослойка: gzip для текста. Starlette-овский GZip жмёт и видео, и 200 на диапазонах."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope.get("method") == "HEAD":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        if "range" in headers or not _accepts_gzip(headers.get("accept-encoding", "")):
            await self.app(scope, receive, send)
            return

        start: dict | None = None
        gz: _Gzip | None = None
        plain = False

        async def wrapped(message: dict) -> None:
            nonlocal start, gz, plain
            kind = message["type"]
            if kind == "http.response.start":
                response = Headers(raw=message["headers"])
                media = response.get("content-type", "").split(";")[0].strip().lower()
                length = response.get("content-length")
                too_small = length is not None and length.isdigit() and int(length) < MIN_SIZE
                plain = (message["status"] != 200 or media not in COMPRESSIBLE or too_small
                         or "content-encoding" in response or "content-range" in response)
                if plain:
                    await send(message)
                else:
                    start = message
                return
            if kind != "http.response.body" or plain:
                await send(message)
                return

            body = message.get("body", b"")
            more = message.get("more_body", False)
            if gz is None:
                if not more and len(body) < MIN_SIZE:
                    plain = True
                    await send(start)
                    await send(message)
                    return
                gz = _Gzip()
                out = await _push(gz, body, not more)
                head = MutableHeaders(raw=start["headers"])
                head["content-encoding"] = "gzip"
                head.add_vary_header("accept-encoding")
                if "etag" in head and not head["etag"].startswith("W/"):
                    head["etag"] = f"W/{head['etag']}"
                if more:
                    del head["content-length"]
                else:
                    head["content-length"] = str(len(out))
                await send(start)
                await send({"type": "http.response.body", "body": out, "more_body": more})
                return
            out = await _push(gz, body, not more)
            await send({"type": "http.response.body", "body": out, "more_body": more})

        await self.app(scope, receive, wrapped)


async def _push(gz: _Gzip, data: bytes, last: bool) -> bytes:
    if len(data) >= THREAD_FROM:
        return await to_thread.run_sync(gz.push, data, last)
    return gz.push(data, last)


class ImmutableAssets(StaticFiles):
    """Файлы сборки: имя содержит хэш содержимого, поэтому браузер их больше не спрашивает."""

    async def get_response(self, path: str, scope: Any) -> Any:
        response = await super().get_response(path, scope)
        if response.status_code in (200, 304):
            response.headers["Cache-Control"] = IMMUTABLE
        return response
