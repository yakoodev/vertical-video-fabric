"""Жив ли вход аккаунта площадки и одно сообщение владельцу в Telegram на случай.

Состояние входа (``accounts.session_state``) ставят проверка входа (``app/session_keepalive.py``: TikTok — браузер
аккаунта, YouTube — youtubei.js без браузера) и отказ загрузки «войдите заново» (``app/worker.py``):

* ``ok`` — вход есть;
* ``needs_reauth`` — площадка выкинула (нет ``sessionid`` / переадресация на вход у TikTok, ответ YouTube без входа);
* ``captcha`` — площадка просит проверку (капча, «подтвердите, что это вы»): не решаем, только сообщаем;
* ``error`` — заход не удался (прокси, сеть, браузер): о входе ничего не известно, прежнее состояние не трогаем.

Сообщение — одно на случай: ``accounts.session_alerted`` помнит, о каком состоянии уже написали. Повтор — только когда
состояние сменится (вход вернулся и снова пропал, капча сменилась выходом). Новые cookie от владельца закрывают случай
(``upsert_account`` чистит состояние).
"""

from __future__ import annotations

import logging
from typing import Any, Callable

log = logging.getLogger(__name__)

STATES = ("ok", "needs_reauth", "captcha", "error")
INCIDENTS = ("needs_reauth", "captcha")
PLATFORM_LABELS = {"tiktok": "TikTok", "youtube": "YouTube", "instagram": "Instagram"}
REASON_MAX = 300


def transition(prev_state: str, alerted: str, new_state: str) -> dict:
    """Чистый шаг: ``{"state", "alerted", "send"}`` — что записать и писать ли владельцу."""
    if new_state not in STATES:
        raise ValueError(f"unknown session state: {new_state}")
    if new_state == "error":
        # Сбой захода — не новость о входе: прежнее состояние остаётся, тишина.
        keep = prev_state if prev_state in ("needs_reauth", "captcha") else "error"
        return {"state": keep, "alerted": alerted, "send": False}
    if new_state == "ok":
        return {"state": "ok", "alerted": "", "send": False}
    if alerted == new_state:
        return {"state": new_state, "alerted": alerted, "send": False}
    return {"state": new_state, "alerted": new_state, "send": True}


def alert_text(account: dict, state: str) -> str:
    label = account.get("label") or f"#{account.get('id')}"
    if state == "captcha":
        return (f"Аккаунт «{label}» просит проверку (капча) — зайди в него в браузере, пройди её и обнови куки "
                "расширением")
    return f"Аккаунт «{label}» вылетел — обнови куки расширением"


def _send_telegram(store: Any, text: str) -> bool:
    from app import notify_tg

    if not notify_tg.is_enabled(store):
        return False
    return notify_tg.send_text(store, text, purpose="account_session") is not None


def record(store: Any, account_id: int, state: str, reason: str = "", source: str = "",
           sender: Callable[[Any, str], bool] | None = None) -> dict:
    """Записать итог проверки входа и, если это новый случай, написать владельцу."""
    row = store.db.query_one(
        "SELECT id, platform, label, session_state, session_alerted FROM accounts WHERE id = ? AND deleted_at IS NULL",
        (int(account_id),))
    if not row:
        return {"state": "", "sent": False}
    step = transition(row["session_state"] or "", row["session_alerted"] or "", state)
    sent = False
    alerted = step["alerted"]
    if step["send"]:
        try:
            sent = bool((sender or _send_telegram)(store, alert_text(dict(row), step["state"])))
        except Exception as exc:  # noqa: BLE001 - Telegram лёг — состояние всё равно записываем
            log.info("session alert to telegram failed: account=%s %s", account_id, type(exc).__name__)
        if not sent:
            alerted = row["session_alerted"] or ""  # не ушло — попробуем при следующей проверке
    store.db.execute(
        "UPDATE accounts SET session_state = ?, session_reason = ?, session_checked_at = CURRENT_TIMESTAMP, "
        "session_alerted = ? WHERE id = ?",
        (step["state"], str(reason or "")[:REASON_MAX], alerted, int(account_id)))
    if step["state"] != (row["session_state"] or ""):
        log.info("account session: account=%s %s -> %s (%s)%s", account_id, row["session_state"] or "-",
                 step["state"], source or "?", " telegram" if sent else "")
    return {"state": step["state"], "sent": sent}
