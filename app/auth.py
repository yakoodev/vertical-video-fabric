from __future__ import annotations

import hmac
from typing import Annotated
from urllib.parse import quote

from fastapi import Request, Security
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.settings import settings

bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="BearerAuth",
    description="Use the service token as: Bearer <token>.",
)


class AuthRequired(Exception):
    pass


def token_is_valid(token: str | None) -> bool:
    if not settings.auth_enabled:
        return True
    expected = settings.ensure_api_token()
    return bool(token and expected and hmac.compare_digest(token, expected))


async def require_auth(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(bearer_scheme)] = None,
) -> None:
    if not settings.auth_enabled:
        return
    bearer_token = credentials.credentials if credentials else None
    cookie_token = request.cookies.get(settings.auth_cookie_name)
    if token_is_valid(bearer_token) or token_is_valid(cookie_token):
        return
    raise AuthRequired()


async def auth_required_handler(request: Request, _exc: AuthRequired):
    if _expects_json(request):
        return JSONResponse(
            {"detail": "Authentication required"},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
    next_url = request.url.path
    if request.url.query:
        next_url = f"{next_url}?{request.url.query}"
    return RedirectResponse(url=f"/login?next={quote(next_url, safe='')}", status_code=303)


def _expects_json(request: Request) -> bool:
    if request.url.path.startswith("/api/") or request.url.path == "/openapi.json":
        return True
    accept = request.headers.get("accept", "")
    return "application/json" in accept and "text/html" not in accept
