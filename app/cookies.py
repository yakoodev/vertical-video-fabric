from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable


DEFAULT_DOMAINS = {
    "youtube": ".youtube.com",
    "tiktok": ".tiktok.com",
    "instagram": ".instagram.com",
}


@dataclass(frozen=True)
class CookieRecord:
    name: str
    value: str
    domain: str = ""
    path: str = "/"
    secure: bool = True
    http_only: bool = False
    expires: int | None = None

    def to_vendor_dict(self, fallback_domain: str) -> dict:
        item = {
            "name": self.name,
            "value": self.value,
            "domain": self.domain or fallback_domain,
            "path": self.path or "/",
            "secure": bool(self.secure),
            "httpOnly": bool(self.http_only),
        }
        if self.expires:
            item["expiry"] = int(self.expires)
        return item


def parse_cookie_input(raw: str, platform: str) -> list[CookieRecord]:
    text = raw.strip()
    if not text:
        raise ValueError("cookie is empty")
    default_domain = DEFAULT_DOMAINS.get(platform, "")
    if _looks_like_netscape(text):
        return _parse_netscape(text, default_domain)
    return _parse_cookie_header(_extract_cookie_header(text), default_domain)


def cookies_to_jsonable(cookies: Iterable[CookieRecord]) -> list[dict]:
    return [asdict(cookie) for cookie in cookies]


def cookies_from_jsonable(items: Iterable[dict]) -> list[CookieRecord]:
    return [
        CookieRecord(
            name=str(item["name"]),
            value=str(item["value"]),
            domain=str(item.get("domain") or ""),
            path=str(item.get("path") or "/"),
            secure=bool(item.get("secure", True)),
            http_only=bool(item.get("http_only", False)),
            expires=item.get("expires"),
        )
        for item in items
    ]


def to_cookie_header(cookies: Iterable[CookieRecord]) -> str:
    return "; ".join(f"{cookie.name}={cookie.value}" for cookie in cookies if cookie.name)


def to_cookie_header_for_host(cookies: Iterable[CookieRecord], host: str) -> str:
    return "; ".join(
        f"{cookie.name}={cookie.value}"
        for cookie in cookies
        if cookie.name and _domain_matches_host(cookie.domain, host)
    )


def required_cookie_status(platform: str, cookies: Iterable[CookieRecord]) -> tuple[bool, list[str]]:
    names = {cookie.name for cookie in cookies}
    if platform == "tiktok":
        required = {"sessionid"}
    elif platform == "instagram":
        required = {"sessionid"}
    elif platform == "youtube":
        required = {"SID", "HSID", "SSID", "APISID", "SAPISID"}
    else:
        required = set()
    missing = sorted(required - names)
    return not missing, missing


def tiktok_caption(title: str, description: str) -> str:
    caption = title.strip()
    description = description.strip()
    if description:
        caption = f"{caption}\n\n{description}" if caption else description
    return caption[:2200]


def _looks_like_netscape(text: str) -> bool:
    if "Netscape HTTP Cookie File" in text:
        return True
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if len(line.split("\t")) >= 7:
            return True
    return False


def _parse_netscape(text: str, default_domain: str) -> list[CookieRecord]:
    cookies: list[CookieRecord] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") and not line.startswith("#HttpOnly_"):
            continue
        http_only = line.startswith("#HttpOnly_")
        if http_only:
            line = line.removeprefix("#HttpOnly_")
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, _include_subdomains, path, secure, expires, name, value = parts[:7]
        if not name:
            continue
        cookies.append(
            CookieRecord(
                name=name,
                value=value,
                domain=domain or default_domain,
                path=path or "/",
                secure=secure.upper() == "TRUE",
                http_only=http_only,
                expires=_safe_int(expires),
            )
        )
    if not cookies:
        raise ValueError("no cookies found in Netscape input")
    return cookies


def _parse_cookie_header(header: str, default_domain: str) -> list[CookieRecord]:
    cookies_by_name: dict[str, CookieRecord] = {}
    for part in header.replace("\n", ";").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        if not name:
            continue
        cookies_by_name[name] = CookieRecord(name=name, value=value.strip(), domain=default_domain)
    cookies = list(cookies_by_name.values())
    if not cookies:
        raise ValueError("no cookies found in Cookie header")
    return cookies


def _extract_cookie_header(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    cookie_lines = []
    for line in lines:
        if line.lower().startswith("cookie:"):
            cookie_lines.append(line.split(":", 1)[1].strip())
    if cookie_lines:
        return "; ".join(cookie_lines)
    if text.lower().startswith("cookie:"):
        return text.split(":", 1)[1].strip()
    return text


def _safe_int(value: str) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed or None


def _domain_matches_host(domain: str, host: str) -> bool:
    normalized_domain = (domain or "").lstrip(".").lower()
    normalized_host = host.lower()
    if not normalized_domain:
        return True
    return normalized_host == normalized_domain or normalized_host.endswith(f".{normalized_domain}")
