"""Аккаунт YouTube «для скачивания»: его cookie берёт yt-dlp, когда YouTube просит вход.

Прокси сервиса бывает помечен YouTube («Sign in to confirm you're not a bot»): без входа ни
один клиент плеера ролик не отдаёт. Тогда загрузка повторяется с cookie аккаунта, который
владелец САМ отметил для скачивания (``app/ytdlp_net.run_routes``). По умолчанию — никакого:
аккаунты публикаций для скачивания не берутся, даже если других нет — за скачивание с
помеченного адреса Google может прибить сессию, а публикации на ней держатся.

Хранится в настройках сервиса (``app_settings``): ``{"account_id": N}``. Аккаунт удалён или
перестал быть YouTube — считаем, что не задан.
"""

from __future__ import annotations

from typing import Any

from app import proxy_config

KEY = "youtube_download_account"


def _store(store: Any = None) -> Any:
    return store if store is not None else proxy_config.registered_store()


def account_id(store: Any = None) -> int | None:
    store = _store(store)
    if store is None:
        return None
    try:
        value = store.get_app_setting_value(KEY, None)
    except Exception:  # noqa: BLE001 - нет базы — как будто не задан
        return None
    raw = value.get("account_id") if isinstance(value, dict) else None
    return int(raw) if isinstance(raw, int) and raw > 0 else None


def get_account(store: Any = None) -> dict | None:
    """Отмеченный аккаунт (без cookie) + ``own_proxy`` — его собственный прокси, если задан; иначе None."""
    store = _store(store)
    acc_id = account_id(store)
    if acc_id is None:
        return None
    try:
        account = store.get_account(acc_id, include_secret=True)
    except KeyError:
        return None
    if account.get("platform") != "youtube":
        return None
    own = str(account.get("proxy_url") or "").strip()
    return {"id": acc_id, "label": account.get("label") or "", "own_proxy": own}


def set_account(store: Any, acc_id: int | None) -> dict:
    """Отметить аккаунт для скачивания (None/0 — снять отметку). Только живой аккаунт YouTube."""
    if acc_id:
        try:
            account = store.get_account(int(acc_id))
        except KeyError:
            raise ValueError("аккаунт не найден") from None
        if account.get("platform") != "youtube":
            raise ValueError("для скачивания подходит только аккаунт YouTube")
        store.set_app_setting(KEY, {"account_id": int(acc_id)})
    else:
        store.set_app_setting(KEY, {})
    return status(store)


def status(store: Any = None) -> dict:
    """Для экрана настроек: выбранный аккаунт и список аккаунтов YouTube (без секретов)."""
    store = _store(store)
    if store is None:
        return {"account_id": None, "label": "", "accounts": []}
    selected = get_account(store)
    accounts = [
        {"id": a["id"], "label": a["label"], "has_required_cookies": bool(a.get("has_required_cookies"))}
        for a in store.list_accounts() if a.get("platform") == "youtube"
    ]
    return {
        "account_id": selected["id"] if selected else None,
        "label": selected["label"] if selected else "",
        "own_proxy": bool(selected and selected["own_proxy"]),
        "accounts": accounts,
    }
