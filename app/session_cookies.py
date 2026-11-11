"""Свежие cookie аккаунта обратно в хранилище: площадки меняют их на ходу (ротация), и старые значения потом не пускают.

TikTok обновляет ``msToken`` почти каждым ответом, ``ttwid``/``odin_tt``/``sid_guard`` — реже; Google меняет
``__Secure-*PSIDTS``/``SIDCC``. Где сервис ходит с cookie аккаунта (загрузка TikTok, TikTok Studio, заход браузера
аккаунта), то, что площадка выдала взамен, пишется сюда (``save_rotated``), иначе сохранённый вход протухает.

Правила (одни на все места):

* только домены площадки (``tiktok.com`` / ``youtube.com``; cookie ``.google.com`` сервису не нужны — он шлёт cookie
  ``studio.youtube.com``, а одноимённая ``SID`` с google.com перетёрла бы нужную);
* только известные имена: набор входа площадки и то, что уже лежит у аккаунта; рекламные и прочие — мимо;
* пустое значение и ``deleted`` — не пишем: ответ с ошибкой не стирает вход;
* в журнал — только ИМЕНА cookie, никогда значения.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from app.cookies import CookieRecord

log = logging.getLogger(__name__)

PLATFORM_DOMAINS = {"tiktok": ("tiktok.com",), "youtube": ("youtube.com",)}

# Cookie входа и устройства, которые площадка меняет и которые нужны сервису.
KNOWN_NAMES = {
    "tiktok": frozenset({
        "sessionid", "sessionid_ss", "sid_tt", "sid_guard", "sid_ucp_v1", "ssid_ucp_v1", "uid_tt", "uid_tt_ss",
        "msToken", "ttwid", "odin_tt", "tt_csrf_token", "tt_chain_token", "passport_csrf_token",
        "passport_csrf_token_default", "s_v_web_id", "cmpl_token", "multi_sids", "d_ticket", "store-idc",
        "store-country-code", "store-country-code-src", "store-country-sign", "tt-target-idc",
        "tt-target-idc-sign", "tt_session_tlb_tag", "passport_auth_status", "passport_auth_status_ss",
        "last_login_method", "csrf_session_id", "perf_feed_cache",
    }),
    "youtube": frozenset({
        "SID", "HSID", "SSID", "APISID", "SAPISID", "__Secure-1PSID", "__Secure-3PSID", "__Secure-1PAPISID",
        "__Secure-3PAPISID", "__Secure-1PSIDTS", "__Secure-3PSIDTS", "__Secure-1PSIDCC", "__Secure-3PSIDCC",
        "SIDCC", "LOGIN_INFO", "PREF", "VISITOR_INFO1_LIVE", "VISITOR_PRIVACY_METADATA", "YSC",
        "__Secure-ROLLOUT_TOKEN", "__Secure-YEC",
    }),
}

# Пустое значение — выход или ответ с ошибкой, а не ротация: такое не пишем.
_DEAD_VALUES = {"", "deleted", '""'}


def domain_ok(platform: str, domain: str) -> bool:
    host = str(domain or "").strip().lstrip(".").lower()
    return any(host == d or host.endswith(f".{d}") for d in PLATFORM_DOMAINS.get(platform, ()))


def _value_ok(value: Any) -> bool:
    return isinstance(value, str) and value.strip() not in _DEAD_VALUES


def rotated_values(platform: str, stored: Iterable[CookieRecord], fresh: Iterable[dict]) -> dict[str, str]:
    """Что поменялось: ``fresh`` — cookie как их видит браузер или requests (``name``, ``value``, ``domain``).
    Имя → новое значение, только для доменов площадки и известных имён. Одно имя на нескольких доменах — берётся
    cookie общего домена (``.tiktok.com``), его шлют все поддомены."""
    stored = list(stored or [])
    stored_values = {c.name: c.value for c in stored}
    allowed = KNOWN_NAMES.get(platform, frozenset()) | set(stored_values)
    best: dict[str, tuple[int, str]] = {}
    for item in fresh or []:
        if not isinstance(item, dict):
            continue
        name, value, domain = item.get("name"), item.get("value"), str(item.get("domain") or "")
        if not name or name not in allowed or not domain_ok(platform, domain) or not _value_ok(value):
            continue
        # Общий домен (с точкой, короче) важнее поддомена.
        rank = (0 if domain.startswith(".") else 1, len(domain))
        if name not in best or rank < best[name][0]:
            best[name] = (rank, value)
    return {name: value for name, (_rank, value) in best.items() if stored_values.get(name) != value}


def jar_items(jar: Any) -> list[dict]:
    """Cookie из банки ``requests`` в виде ``{name, value, domain}``."""
    out = []
    for cookie in jar or []:
        out.append({"name": getattr(cookie, "name", ""), "value": getattr(cookie, "value", ""),
                    "domain": getattr(cookie, "domain", "") or ""})
    return out


def save_rotated(store: Any, account_id: int, platform: str, stored: Iterable[CookieRecord], fresh: Iterable[dict],
                 source: str) -> list[str]:
    """Записать сменившиеся cookie аккаунта. Вернуть их имена (их же — в журнал; значения — никогда)."""
    updates = rotated_values(platform, stored, fresh)
    if not updates:
        return []
    try:
        changed = store.update_account_cookie_values(int(account_id), updates)
    except Exception as exc:  # noqa: BLE001 - запись свежих cookie — забота, не повод сорвать задачу
        log.warning("cookie write-back failed: account=%s source=%s names=%s (%s)", account_id, source,
                    ",".join(sorted(updates)), type(exc).__name__)
        return []
    names = sorted(updates)
    if changed:
        log.info("cookies rotated: account=%s source=%s names=%s", account_id, source, ",".join(names))
    return names if changed else []


def playwright_cookies(cookies: Iterable[CookieRecord], platform: str) -> list[dict]:
    """CookieRecord аккаунта → ``context.addCookies`` Playwright (только домены площадки)."""
    default_domain = f".{PLATFORM_DOMAINS[platform][0]}"
    out = []
    for cookie in cookies or []:
        domain = (cookie.domain or default_domain).strip()
        if not cookie.name or not domain_ok(platform, domain):
            continue
        if cookie.name.startswith("__Host-"):
            # __Host- — без домена, путь «/», только https: Playwright принимает такую лишь по адресу.
            item = {"name": cookie.name, "value": cookie.value, "url": f"https://{domain.lstrip('.')}/",
                    "secure": True, "httpOnly": bool(cookie.http_only)}
        else:
            item = {"name": cookie.name, "value": cookie.value, "domain": domain, "path": cookie.path or "/",
                    "secure": bool(cookie.secure) or cookie.name.startswith("__Secure-"),
                    "httpOnly": bool(cookie.http_only)}
        if cookie.expires and int(cookie.expires) > 0:
            item["expires"] = int(cookie.expires)
        out.append(item)
    return out


def logged_in_by_cookies(platform: str, fresh: Iterable[dict]) -> bool:
    """Вход по cookie браузера: есть непустая cookie входа площадки (TikTok — sessionid, Google — SAPISID и SID)."""
    names = {i.get("name") for i in fresh or [] if isinstance(i, dict) and _value_ok(i.get("value"))
             and domain_ok(platform, str(i.get("domain") or ""))}
    if platform == "tiktok":
        return "sessionid" in names
    if platform == "youtube":
        return "SAPISID" in names and "SID" in names
    return False
