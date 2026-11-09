"""Сбор аналитики TikTok Studio (разбор и хранение — ``app/tiktok_studio.py``).

Что собираем: ролики TikTok, выложенные сервисом (``job_targets``, аккаунт известен), и выложенные руками
(``project_publications`` со ссылкой ``/@ник/video/<id>``: аккаунт сервиса — по нику). Ник аккаунта сервис
узнаёт сам один раз (страница TikTok Studio) и хранит в ``accounts.handle``.

TikTok не любит пачки страниц из headless-браузера с одного адреса, поэтому:

* одна сессия браузера на аккаунт, ролики по одному с паузой (``TIKTOK_STUDIO_PAUSE_SECONDS``);
* не больше ``TIKTOK_STUDIO_MAX_PER_RUN`` роликов аккаунта за проход;
* ролик моложе ``FRESH_DAYS`` — раз в сутки, до ``MAX_AGE_DAYS`` — раз в неделю, старше — не собираем;
* фон (``StatsCollector`` в ``app/platform_stats.py``) запускает проход не чаще раза в ``RUN_GAP_HOURS``,
  в своём потоке: сбой или долгий проход Studio не задерживает сбор публичных чисел.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from app import tiktok_studio as S
from app.settings import settings

FRESH_DAYS = 30
MAX_AGE_DAYS = 90
FRESH_GAP_HOURS = 20  # «раз в сутки» с запасом: проход не уползает каждый день на час позже
OLD_GAP_HOURS = 7 * 24
MANUAL_GAP_MINUTES = 30  # вручную по одному ролику — не чаще
RUN_GAP_HOURS = 1.0
HANDLE_RETRY_HOURS = 24
NODE_TIMEOUT_BASE = 120
NODE_TIMEOUT_PER_VIDEO = 60

_SCRIPT = Path(__file__).resolve().parents[1] / "node" / "tiktok-studio.cjs"
_SECRET_ENV = ("TT_STUDIO_PROXY", "TT_STUDIO_COOKIES", "TT_STUDIO_UA")

_run_lock = threading.Lock()
_status: dict[str, Any] = {"running": False, "started_at": None, "finished_at": None, "last": None}
_handle_tries: dict[int, float] = {}


def enabled() -> bool:
    return os.getenv("TIKTOK_STUDIO_STATS", "1").strip().lower() not in ("0", "false", "no", "off")


def _env_number(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


def max_per_run() -> int:
    return max(1, int(_env_number("TIKTOK_STUDIO_MAX_PER_RUN", 15)))


def pause_seconds() -> float:
    return max(0.0, _env_number("TIKTOK_STUDIO_PAUSE_SECONDS", 8))


def _now_text() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _parse_time(text: Any) -> datetime | None:
    text = str(text or "").strip().replace("T", " ")
    for fmt, size in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d", 10)):
        try:
            return datetime.strptime(text[:size], fmt)
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------- #
# Что собирать
# --------------------------------------------------------------------------- #


def tiktok_accounts(store: Any) -> dict[int, dict]:
    """Подключённые аккаунты TikTok: id → {id, label, handle}."""
    rows = store.db.query_all(
        "SELECT id, label, handle FROM accounts WHERE platform = 'tiktok' AND deleted_at IS NULL ORDER BY id"
    )
    return {int(r["id"]): {"id": int(r["id"]), "label": r["label"], "handle": S.normalize_handle(r["handle"])}
            for r in rows}


def account_for_handle(accounts: dict[int, dict], handle: str, account_text: str = "") -> int | None:
    """Аккаунт сервиса ручной публикации: по нику из ссылки, иначе по подписи «аккаунт» (ник или название)."""
    handle = S.normalize_handle(handle)
    for acc in accounts.values():
        if handle and acc["handle"] == handle:
            return acc["id"]
    text = S.normalize_handle(account_text)
    if not handle and text:
        for acc in accounts.values():
            if text in (acc["handle"], S.normalize_handle(acc["label"])):
                return acc["id"]
    return None


def candidates(store: Any, accounts: dict[int, dict] | None = None) -> tuple[list[dict], int]:
    """Ролики TikTok, по которым можно сходить в Studio, и сколько ручных публикаций без аккаунта сервиса."""
    accounts = tiktok_accounts(store) if accounts is None else accounts
    out = []
    for row in store.db.query_all(
        """SELECT jt.id, jt.account_id, jt.remote_url, jt.remote_id, jt.finished_at FROM job_targets jt
           WHERE jt.platform = 'tiktok' AND jt.status = 'succeeded' AND jt.remote_url != '' ORDER BY jt.id"""
    ):
        video = S.video_id_of(row["remote_url"])
        if video and int(row["account_id"]) in accounts:
            out.append({"kind": "service", "ref": int(row["id"]), "account_id": int(row["account_id"]),
                        "video_id": video, "published_at": row["finished_at"]})
    unmapped = 0
    for row in store.db.query_all(
        """SELECT id, url, account, published_at, created_at FROM project_publications
           WHERE platform = 'tiktok' AND url != '' ORDER BY id"""
    ):
        video = S.video_id_of(row["url"])
        if not video:
            continue
        account_id = account_for_handle(accounts, S.handle_of(row["url"]), row["account"] or "")
        if account_id is None:
            unmapped += 1
            continue
        out.append({"kind": "manual", "ref": int(row["id"]), "account_id": account_id, "video_id": video,
                    "published_at": row["published_at"] or row["created_at"]})
    return out, unmapped


def due(items: list[dict], attempts: dict[tuple[str, int], str], *, force: bool = False,
        now: datetime | None = None) -> list[dict]:
    """Кому пора: свежие — раз в сутки, до MAX_AGE_DAYS — раз в неделю, старше — нет (вручную — всем,
    но не чаще MANUAL_GAP_MINUTES). Сначала ни разу не собранные, потом самые свежие."""
    now = now or datetime.utcnow()
    out = []
    for item in items:
        published = _parse_time(item.get("published_at"))
        age_days = (now - published).total_seconds() / 86400 if published else 0.0
        last = _parse_time(attempts.get((item["kind"], item["ref"])))
        if force:
            gap = timedelta(minutes=MANUAL_GAP_MINUTES)
        elif age_days < FRESH_DAYS:
            gap = timedelta(hours=FRESH_GAP_HOURS)
        elif age_days < MAX_AGE_DAYS:
            gap = timedelta(hours=OLD_GAP_HOURS)
        else:
            continue
        if last is None or now - last >= gap:
            out.append({**item, "age_days": round(age_days, 1), "never": last is None})
    out.sort(key=lambda i: (not i["never"], i["age_days"]))
    return out


# --------------------------------------------------------------------------- #
# Браузер
# --------------------------------------------------------------------------- #


def playwright_cookies(cookies: list) -> list[dict]:
    """CookieRecord аккаунта → формат ``context.addCookies`` Playwright (только домены TikTok)."""
    out = []
    for cookie in cookies or []:
        domain = (cookie.domain or ".tiktok.com").strip()
        if "tiktok" not in domain:
            continue
        item = {"name": cookie.name, "value": cookie.value, "domain": domain, "path": cookie.path or "/",
                "secure": bool(cookie.secure), "httpOnly": bool(cookie.http_only)}
        if cookie.expires and int(cookie.expires) > 0:
            item["expires"] = int(cookie.expires)
        out.append(item)
    return out


def _playwright_dir() -> Path:
    return settings.tiktok_vendor_root / "tiktok_uploader" / "tiktok-signature" / "node_modules" / "playwright-chromium"


def run_session(store: Any, account_id: int, video_ids: list[str],
                runner: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> dict:
    """Одна сессия браузера аккаунта: ``{"handle", "login_required", "videos": {id: {...}}}``.
    Cookie, прокси и user agent — окружением процесса, в аргументах только id роликов."""
    from app.proxy_config import mask_secrets

    account = store.get_account(int(account_id))
    env = {key: value for key, value in os.environ.items() if key not in _SECRET_ENV}
    env["TT_STUDIO_COOKIES"] = json.dumps(playwright_cookies(store.get_account_cookies(int(account_id))))
    proxy = store.get_account_proxy_url(int(account_id))
    if proxy:
        env["TT_STUDIO_PROXY"] = proxy
    user_agent = (account.get("user_agent") or settings.tiktok_user_agent or "").strip()
    if user_agent:
        env["TT_STUDIO_UA"] = user_agent
    env["TT_STUDIO_PAUSE_MS"] = str(int(pause_seconds() * 1000))
    timeout = NODE_TIMEOUT_BASE + NODE_TIMEOUT_PER_VIDEO * len(video_ids)
    try:
        proc = runner([settings.node_bin, str(_SCRIPT), str(_playwright_dir()), ",".join(video_ids)],
                      text=True, capture_output=True, timeout=timeout, check=False, env=env)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"TikTok Studio не ответил за {timeout} с") from None
    except OSError as exc:
        raise RuntimeError(f"node не запустился: {exc}") from None
    if proc.returncode != 0:
        tail = [line for line in (proc.stderr or "").splitlines() if line.strip()][-1:]
        raise RuntimeError(mask_secrets(" ".join(tail) or f"код {proc.returncode}")[:300])
    try:
        data = json.loads((proc.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise RuntimeError("скрипт TikTok Studio вернул не JSON") from None
    if not isinstance(data, dict):
        raise RuntimeError("скрипт TikTok Studio вернул не JSON")
    data.setdefault("videos", {})
    return data


def _save_handle(store: Any, account_id: int, handle: str) -> None:
    handle = S.normalize_handle(handle)
    if handle:
        store.db.execute("UPDATE accounts SET handle = ? WHERE id = ? AND handle != ?", (handle, int(account_id), handle))


LOGIN_ERROR = "TikTok просит войти заново: обновите cookie аккаунта"


# --------------------------------------------------------------------------- #
# Проход
# --------------------------------------------------------------------------- #


def collect(store: Any, *, account_id: int | None = None, video_id: str = "", force: bool = False,
            sleep: Callable[[float], None] = time.sleep, runner: Callable[..., Any] = subprocess.run) -> dict:
    """Один проход: узнать ники аккаунтов без ника, затем по аккаунту — одна сессия на его ролики."""
    if not _run_lock.acquire(blocking=False):
        return {"busy": True, **status()}
    summary: dict[str, Any] = {"accounts": 0, "checked": 0, "ok": 0, "failed": 0, "unmapped": 0, "handles": {}}
    _status.update(running=True, started_at=_now_text(), finished_at=None)
    try:
        accounts = tiktok_accounts(store)
        if account_id is not None:
            accounts = {k: v for k, v in accounts.items() if k == int(account_id)}
        sessions = 0
        for acc in accounts.values():
            if acc["handle"] or time.time() - _handle_tries.get(acc["id"], 0) < HANDLE_RETRY_HOURS * 3600:
                continue
            _handle_tries[acc["id"]] = time.time()
            if sessions and pause_seconds():
                sleep(pause_seconds())
            sessions += 1
            try:
                found = run_session(store, acc["id"], [], runner=runner)
            except Exception as exc:  # noqa: BLE001 - один аккаунт не валит проход
                summary["handles"][str(acc["id"])] = f"ошибка: {str(exc)[:120]}"
                continue
            if found.get("handle"):
                _save_handle(store, acc["id"], found["handle"])
                acc["handle"] = S.normalize_handle(found["handle"])
            summary["handles"][str(acc["id"])] = acc["handle"] or (LOGIN_ERROR if found.get("login_required")
                                                                    else "ник не найден")
        items, summary["unmapped"] = candidates(store, accounts)
        if video_id:
            items = [i for i in items if i["video_id"] == str(video_id)]
        todo = due(items, S.last_attempts(store), force=force)
        by_account: dict[int, list[dict]] = {}
        for item in todo:
            by_account.setdefault(item["account_id"], []).append(item)
        for acc_id, acc_items in by_account.items():
            ids = []
            for item in acc_items:  # один ролик бывает и публикацией сервиса, и отмеченным руками
                if item["video_id"] not in ids:
                    ids.append(item["video_id"])
            ids = ids[:max_per_run()]
            acc_items = [i for i in acc_items if i["video_id"] in ids]
            if sessions and pause_seconds():
                sleep(pause_seconds())
            sessions += 1
            summary["accounts"] += 1
            _collect_account(store, acc_id, ids, acc_items, summary, runner)
    finally:
        _status.update(running=False, finished_at=_now_text(), last=summary)
        _run_lock.release()
    return {**summary, **status()}


def _collect_account(store: Any, account_id: int, ids: list[str], items: list[dict], summary: dict,
                     runner: Callable[..., Any]) -> None:
    def save(item: dict, parsed: dict | None = None, error: str = "") -> None:
        ref = {"target_id": item["ref"]} if item["kind"] == "service" else {"publication_id": item["ref"]}
        S.add_snapshot(store, account_id=account_id, video_id=item["video_id"], parsed=parsed, error=error, **ref)

    summary["checked"] += len(items)
    try:
        result = run_session(store, account_id, ids, runner=runner)
    except Exception as exc:  # noqa: BLE001 - сбой браузера: причина у каждой публикации, проход идёт дальше
        for item in items:
            save(item, error=str(exc)[:300] or "ошибка")
        summary["failed"] += len(items)
        return
    if result.get("handle"):
        _save_handle(store, account_id, result["handle"])
    videos = result.get("videos") or {}
    for item in items:
        answer = videos.get(item["video_id"])
        if not answer:
            error = LOGIN_ERROR if result.get("login_required") else "страница ролика не открылась"
            save(item, error=error)
            summary["failed"] += 1
            continue
        if not answer.get("ok"):
            save(item, error=str(answer.get("error") or "ошибка")[:300])
            summary["failed"] += 1
            continue
        try:
            parsed = S.parse_insight(answer.get("insight") or {})
        except ValueError as exc:
            save(item, error=str(exc))
            summary["failed"] += 1
            continue
        save(item, parsed=parsed)
        summary["ok"] += 1


def start_collect(store: Any, *, account_id: int | None = None, video_id: str = "", force: bool = True) -> dict:
    """Проход в фоне (ручной запуск): ответ сразу, числа — по мере сбора."""
    if _run_lock.locked():
        return {"started": False, **status()}
    threading.Thread(target=lambda: collect(store, account_id=account_id, video_id=video_id, force=force),
                     name="tiktok-studio", daemon=True).start()
    return {"started": True, **status(), "running": True}


def maybe_start(store: Any) -> bool:
    """Фон: раз в RUN_GAP_HOURS — проход в своём потоке (частоту по роликам держит ``due``)."""
    if not enabled() or _run_lock.locked():
        return False
    finished = _parse_time(_status.get("finished_at"))
    if finished and datetime.utcnow() - finished < timedelta(hours=RUN_GAP_HOURS):
        return False
    started = start_collect(store, force=False)
    return bool(started.get("started"))


def status() -> dict:
    return {"running": bool(_status["running"]), "started_at": _status["started_at"],
            "finished_at": _status["finished_at"], "last": _status["last"], "enabled": enabled()}
