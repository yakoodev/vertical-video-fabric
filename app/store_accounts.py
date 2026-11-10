"""Хранилище: аккаунты площадок, их куки и прокси. Часть ``AppStore``."""

from __future__ import annotations

import re
from dataclasses import replace as _dc_replace
from urllib.parse import unquote, urlsplit, urlunsplit

from app.cookies import (
    CookieRecord, cookies_from_jsonable, cookies_to_jsonable, DEFAULT_DOMAINS, parse_cookie_input,
    required_cookie_status,
)
from app.store_common import VALID_PLATFORMS


def _valid_proxy_url(proxy_url: str) -> bool:
    scheme = urlsplit(proxy_url).scheme.lower()
    return scheme in {"http", "https"}


def _account_proxy(raw: str) -> str:
    """Прокси аккаунта из формы: ``http://логин:пароль@хост:порт`` или ``логин:пароль:хост:порт`` (как дают продавцы).
    Только http/https: браузер подписи TikTok не умеет socks5 с паролем. Без порта и с мусором — отказ сразу, а не
    сломанный список аккаунтов потом."""
    from app.proxy_config import normalize_url

    try:
        url = normalize_url(raw)
    except ValueError:
        raise ValueError("proxy_url: нужен вид http://логин:пароль@хост:порт или логин:пароль:хост:порт") from None
    if not _valid_proxy_url(url):
        raise ValueError("proxy_url must start with http:// or https://")
    return url


def _redact_proxy_url(proxy_url: str) -> str:
    parts = urlsplit(proxy_url)
    host = parts.hostname or ""
    try:
        port = f":{parts.port}" if parts.port else ""
    except ValueError:  # сохранён до проверки адреса: список аккаунтов не должен из-за него падать
        port = ""
    if not host:
        return "configured"
    netloc = f"***@{host}{port}" if parts.username or parts.password else f"{host}{port}"
    return urlunsplit((parts.scheme, netloc, "", "", ""))


USER_AGENT_MAX = 512


def _account_user_agent(raw: str) -> str:
    """UA браузера, из которого сняты cookie. Уходит заголовком в TikTok: перевод строки и мусор — отказ."""
    user_agent = (raw or "").strip()
    if len(user_agent) > USER_AGENT_MAX or any(ord(ch) < 32 or ord(ch) == 127 for ch in user_agent):
        raise ValueError(f"user_agent: одна строка до {USER_AGENT_MAX} символов")
    return user_agent


YOUTUBE_CHANNEL_MAX = 100
_CHANNEL_ID_RE = re.compile(r"UC[\w-]{22}")


def youtube_channel(raw: str) -> str:
    """Канал YouTube аккаунта: ``UC…`` (лучше всего), ``@ник``, ссылка на канал или точное имя.
    Ссылка сводится к id или @нику. Пусто — канал по умолчанию у входа Google."""
    value = (raw or "").strip()
    if not value:
        return ""
    if len(value) > YOUTUBE_CHANNEL_MAX or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError(f"channel: одна строка до {YOUTUBE_CHANNEL_MAX} символов")
    if "youtube.com/" in value or "youtu.be/" in value:
        path = unquote(urlsplit(value if "://" in value else f"https://{value}").path)
        parts = [part for part in path.split("/") if part]
        if len(parts) >= 2 and parts[0] == "channel" and _CHANNEL_ID_RE.fullmatch(parts[1]):
            return parts[1]
        if parts and parts[0].startswith("@") and len(parts[0]) > 1:
            return parts[0]
        raise ValueError("channel: нужна ссылка вида youtube.com/channel/UC… или youtube.com/@ник")
    if _CHANNEL_ID_RE.fullmatch(value):
        return value
    if value.startswith("@") and len(value) < 2:
        raise ValueError("channel: пустой @ник")
    return value


class AccountsStore:
    def upsert_account(self, platform: str, label: str, raw_cookie: str, proxy_url: str = "",
                       clear_proxy: bool = False, user_agent: str = "") -> dict:
        """Новый аккаунт или свежие cookie старого (тот же платформа + метка). Пустой ``proxy_url`` у старого прокси не
        трогает: иначе обновление cookie молча пускало аккаунт напрямую. Убрать прокси — ``clear_proxy``.

        ``user_agent`` — браузер, выдавший эти cookie: он идёт вместе с ними и заменяется вместе с ними. Свежие cookie
        без UA (вставка руками) стирают прежний UA — TikTok тогда берёт ``TIKTOK_USER_AGENT``, как раньше."""
        platform = platform.lower().strip()
        label = label.strip()
        proxy_url = _account_proxy(proxy_url) if proxy_url.strip() else ""
        user_agent = _account_user_agent(user_agent)
        if platform not in VALID_PLATFORMS:
            raise ValueError(f"unsupported platform: {platform}")
        if not label:
            raise ValueError("label is required")
        cookies = parse_cookie_input(raw_cookie, platform)
        ok, missing = required_cookie_status(platform, cookies)
        encrypted = self.cipher.encrypt_json(cookies_to_jsonable(cookies))
        encrypted_proxy_url = self.cipher.encrypt_json(proxy_url) if proxy_url else ""
        existing = self.db.query_one(
            "SELECT id, encrypted_proxy_url FROM accounts WHERE platform = ? AND label = ?",
            (platform, label),
        )
        if existing and not proxy_url and not clear_proxy:
            encrypted_proxy_url = existing["encrypted_proxy_url"] or ""
        if existing:
            self.db.execute(
                """
                UPDATE accounts
                SET encrypted_cookies = ?, encrypted_proxy_url = ?, user_agent = ?, cookie_count = ?,
                    has_required_cookies = ?, missing_cookies = ?, deleted_at = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (encrypted, encrypted_proxy_url, user_agent, len(cookies), int(ok), ",".join(missing),
                 existing["id"]),
            )
            account_id = existing["id"]
        else:
            cur = self.db.execute(
                """
                INSERT INTO accounts
                    (platform, label, encrypted_cookies, encrypted_proxy_url, user_agent, cookie_count,
                     has_required_cookies, missing_cookies)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (platform, label, encrypted, encrypted_proxy_url, user_agent, len(cookies), int(ok),
                 ",".join(missing)),
            )
            account_id = cur.lastrowid
        return self.get_account(account_id, include_secret=False)

    def list_accounts(self) -> list[dict]:
        rows = self.db.query_all(
            """
            SELECT id, platform, label, encrypted_proxy_url, user_agent, handle, cookie_count, has_required_cookies,
                   missing_cookies, created_at, updated_at
            FROM accounts
            WHERE deleted_at IS NULL
            ORDER BY platform, label
            """
        )
        return [self._account_from_row(row, include_secret=False) for row in rows]

    def get_account(self, account_id: int, include_secret: bool = False, include_deleted: bool = False) -> dict:
        row = self.db.query_one("SELECT * FROM accounts WHERE id = ?", (account_id,))
        if not row or (row["deleted_at"] and not include_deleted):
            raise KeyError(f"account not found: {account_id}")
        data = self._account_from_row(row, include_secret=include_secret)
        if include_secret:
            data["cookies"] = cookies_from_jsonable(
                self.cipher.decrypt_json(row["encrypted_cookies"])
            )
        return data

    def delete_account(self, account_id: int) -> dict:
        account = self.get_account(account_id, include_deleted=False)
        self.db.execute(
            """
            UPDATE accounts
            SET deleted_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (account_id,),
        )
        return account

    def set_account_proxy(self, account_id: int, proxy_url: str) -> dict:
        """Только прокси аккаунта, cookie не трогаем (вставлять их заново ради прокси неудобно).
        Пусто — убрать прокси: публикация пойдёт с адреса сервера."""
        self.get_account(account_id)
        proxy_url = _account_proxy(proxy_url) if proxy_url.strip() else ""
        encrypted = self.cipher.encrypt_json(proxy_url) if proxy_url else ""
        self.db.execute(
            "UPDATE accounts SET encrypted_proxy_url = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (encrypted, account_id),
        )
        return self.get_account(account_id)

    def set_account_channel(self, account_id: int, channel: str) -> dict:
        """Канал YouTube, куда грузить (``accounts.handle``): у одного входа Google их бывает
        несколько, без выбора ролик уходит на канал по умолчанию. Пусто — канал по умолчанию."""
        account = self.get_account(account_id)
        if account["platform"] != "youtube":
            raise ValueError("channel: только для аккаунтов YouTube")
        self.db.execute(
            "UPDATE accounts SET handle = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (youtube_channel(channel), account_id),
        )
        return self.get_account(account_id)

    def get_account_cookies(self, account_id: int):
        row = self.db.query_one("SELECT encrypted_cookies FROM accounts WHERE id = ?", (account_id,))
        if not row:
            raise KeyError(f"account not found: {account_id}")
        return cookies_from_jsonable(self.cipher.decrypt_json(row["encrypted_cookies"]))

    def update_account_cookie_values(self, account_id: int, updates: dict[str, str]) -> bool:
        """Merge refreshed cookie values (name -> value) into the stored jar so a
        rotating session stays alive. Existing records keep their domain/flags;
        unknown names are appended with the platform default domain. Returns True
        if anything changed."""
        if not updates:
            return False
        row = self.db.query_one(
            "SELECT platform, encrypted_cookies FROM accounts WHERE id = ? AND deleted_at IS NULL",
            (account_id,),
        )
        if not row:
            return False
        cookies = cookies_from_jsonable(self.cipher.decrypt_json(row["encrypted_cookies"]))
        default_domain = DEFAULT_DOMAINS.get(row["platform"], "")
        seen: set[str] = set()
        out: list[CookieRecord] = []
        changed = False
        for cookie in cookies:
            new_value = updates.get(cookie.name)
            if new_value and new_value != cookie.value:
                out.append(_dc_replace(cookie, value=new_value))
                changed = True
            else:
                out.append(cookie)
            seen.add(cookie.name)
        for name, value in updates.items():
            if name not in seen and value:
                out.append(CookieRecord(name=name, value=value, domain=default_domain))
                changed = True
        if not changed:
            return False
        ok, missing = required_cookie_status(row["platform"], out)
        self.db.execute(
            """
            UPDATE accounts
            SET encrypted_cookies = ?, cookie_count = ?, has_required_cookies = ?,
                missing_cookies = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                self.cipher.encrypt_json(cookies_to_jsonable(out)),
                len(out),
                int(ok),
                ",".join(missing),
                account_id,
            ),
        )
        return True

    def get_account_proxy_url(self, account_id: int) -> str:
        row = self.db.query_one("SELECT encrypted_proxy_url FROM accounts WHERE id = ?", (account_id,))
        if not row:
            raise KeyError(f"account not found: {account_id}")
        encrypted = row["encrypted_proxy_url"] or ""
        if encrypted:
            return str(self.cipher.decrypt_json(encrypted)).strip()
        return self.get_global_proxy_url()

    def _account_from_row(self, row, include_secret: bool) -> dict:
        data = dict(row)
        encrypted_proxy_url = data.get("encrypted_proxy_url") or ""
        proxy_url = ""
        if encrypted_proxy_url:
            proxy_url = str(self.cipher.decrypt_json(encrypted_proxy_url)).strip()
        data["has_required_cookies"] = bool(data["has_required_cookies"])
        data["proxy_configured"] = bool(proxy_url)
        # Тот же запасной прокси, что берёт get_account_proxy_url (настройка сервиса, затем env).
        data["proxy_display"] = _redact_proxy_url(proxy_url) if proxy_url else (
            "global proxy" if self.get_global_proxy_url() else "direct"
        )
        if include_secret:
            data["proxy_url"] = proxy_url
        data.pop("encrypted_cookies", None)
        data.pop("encrypted_proxy_url", None)
        return data
