"""Живой вход аккаунтов TikTok и YouTube: раз в 4–6 часов сервис заходит аккаунтом на площадку и заодно проверяет вход.

Зачем: cookie, снятые расширением, со временем протухают — площадка меняет их на ходу, а сервис ходил со старыми.
«yakoodev yt» дважды вылетал; пост «совы» через 5 минут после свежих cookie набрал 424 просмотра, следующие —
0 (docs/research/owl-web-upload.md). Раз в ``interval_hours`` ± 20 % — один аккаунт за проход, по очереди.

TikTok — браузер аккаунта. У каждого свой постоянный профиль Chromium (``<DATA_DIR>/browser_profiles/<id>/``):

* профиль засевается cookie аккаунта; владелец прислал новые (``accounts.cookies_set_at`` новее профиля) — профиль
  стирается и засевается заново. Перед каждым заходом в профиль кладутся cookie из хранилища: в нём и ротации
  загрузок, так у сессии одна линия, а не две расходящиеся;
* всегда прокси аккаунта и его UA; окно, язык ``ru-RU`` и часовой пояс (по умолчанию ``Asia/Tbilisi``,
  ``SESSION_KEEPALIVE_TZ``) записываются в профиль при засеве и дальше не меняются;
* /foryou, ожидание, немного колесом, 20–40 с, закрыть (``node/session-keepalive.cjs``). Только чтение: ни кликов,
  ни лайков, ни подписок, ни постов. Картинки и видео не грузятся;
* нет ``sessionid`` / переадресация на вход — ``needs_reauth``; капча — ``captcha``.

YouTube — БЕЗ браузера: youtubei.js с теми же cookie и прокси, что у загрузки (``node/youtube-session.mjs``), только
чтение — меню аккаунта (``/account/account_menu``) и список каналов. У сервиса лишь cookie youtube.com, без
accounts.google.com: настоящая страница YouTube сверяет вход с Google и сама стирает вход youtube.com (11.11
браузер так записал «vtubika» в «вылетел» — ложно, загрузки шли), а Google видит браузер, теряющий вход. Вход —
по ответу: шапка меню аккаунта (``activeAccountHeaderRenderer``) / ``yt_li``; ответ без входа или отказ 401 —
``needs_reauth``; ответ без признаков — сбой проверки (``error``), не «вылетел».

Оба пути: свежие cookie — обратно в хранилище (``app/session_cookies.py``: домены площадки, известные имена, в журнал —
только имена); состояние входа и одно сообщение в Telegram на случай — ``app/session_health.py``.

Перед загрузкой TikTok (``before_upload``): прошлый заход старше ``fresh_before_upload_hours`` (2 ч) — сначала
короткий заход, загрузка идёт со свежей сессией. YouTube перед загрузкой не проверяем: загрузка — тот же youtubei.js
и сама приносит свежие cookie.

Память: один Chromium на весь сервис (``app/browser_lock.py``, общий со снимками страниц и в очереди тяжёлых
задач), маленькое окно, профиль закрывается после захода. Проверка YouTube Chromium не берёт.

Включено по умолчанию у аккаунтов со своим прокси (``accounts.keepalive``: NULL — так, 1/0 — своё значение).
Настройки — ``app_settings`` ``session_keepalive`` (``SESSION_KEEPALIVE``, ``SESSION_KEEPALIVE_HOURS``).
"""

from __future__ import annotations

import json
import logging
import os
import random
import shutil
import subprocess
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from app import session_cookies as C
from app import session_health
from app.settings import settings

log = logging.getLogger(__name__)

SETTINGS_KEY = "session_keepalive"
PLATFORMS = ("tiktok", "youtube")
BROWSER_PLATFORMS = ("tiktok",)  # YouTube браузером не ходим никогда — только youtubei.js
HOME_URLS = {"tiktok": "https://www.tiktok.com/foryou"}
# Хост, для которого собирается Cookie-заголовок YouTube, — как у загрузки (app/providers/youtube.py).
YOUTUBE_COOKIE_HOST = "studio.youtube.com"
# Один раз после ухода YouTube из браузера: снять ложные состояния, которые поставил браузер (app_settings).
YT_BROWSER_RESET_KEY = "session_keepalive_yt_browser_reset"
# Причины, которые писал заход браузера YouTube до 11.11 (classify тогда): по ним ложное «вылетел» и узнаём.
_YT_BROWSER_REASONS = (
    "Google показал капчу", "Google просит подтвердить, что это вы", "Google перекинул на страницу входа",
    "в браузере нет SAPISID — Google выкинул из аккаунта", "YouTube показывает кнопку «Войти»",
)
JITTER = 0.2  # ± к интервалу: заходы не по часам
STAY_SECONDS = (20, 40)
QUICK_STAY_SECONDS = (12, 20)  # перед загрузкой — короче
ERROR_RETRY_HOURS = 1.0
TICK_SECONDS = 10 * 60
FIRST_DELAY_SECONDS = 3 * 60
NODE_TIMEOUT = 180
VIEWPORT = {"width": 1280, "height": 720}
LOCALE = "ru-RU"
SEED_FILE = "vvf-seed.json"
TIME_FMT = "%Y-%m-%d %H:%M:%S"
_SCRIPT = Path(__file__).resolve().parents[1] / "node" / "session-keepalive.cjs"
_YT_SCRIPT = Path(__file__).resolve().parents[1] / "node" / "youtube-session.mjs"
_SECRET_ENV = ("KA_COOKIES", "KA_PROXY", "KA_UA")
# Замки профиля, оставшиеся от упавшего Chromium: с ними профиль «занят».
_PROFILE_LOCKS = ("SingletonLock", "SingletonSocket", "SingletonCookie")

_visit_lock = threading.Lock()
_status: dict[str, Any] = {"running": None, "last": None}

# Часы (тесты подменяют): наивное UTC, как CURRENT_TIMESTAMP в SQLite.
now: Callable[[], datetime] = lambda: datetime.utcnow()  # noqa: E731


# --------------------------------------------------------------------------- #
# Настройки
# --------------------------------------------------------------------------- #


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


def defaults() -> dict:
    return {"enabled": os.getenv("SESSION_KEEPALIVE", "1").strip().lower() not in ("0", "false", "no", "off"),
            "interval_hours": _env_float("SESSION_KEEPALIVE_HOURS", 5.0),
            "fresh_before_upload_hours": _env_float("SESSION_KEEPALIVE_FRESH_HOURS", 2.0)}


def normalize(raw: Any, base: dict | None = None) -> dict:
    cfg = dict(base or defaults())
    raw = raw if isinstance(raw, dict) else {}
    if raw.get("enabled") is not None:
        cfg["enabled"] = bool(raw["enabled"])
    for key, lo, hi in (("interval_hours", 1, 48), ("fresh_before_upload_hours", 0, 48)):
        if raw.get(key) is None or raw.get(key) == "":
            continue
        try:
            value = float(raw[key])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key}: число") from exc
        if not lo <= value <= hi:
            raise ValueError(f"{key}: от {lo:g} до {hi:g}")
        cfg[key] = value
    return cfg


def get_settings(store: Any) -> dict:
    return normalize(store.get_app_setting_value(SETTINGS_KEY, {}) or {})


def save_settings(store: Any, patch: dict) -> dict:
    cfg = normalize(patch, get_settings(store))
    store.set_app_setting(SETTINGS_KEY, cfg)
    return cfg


def timezone_id() -> str:
    return os.getenv("SESSION_KEEPALIVE_TZ", "").strip() or "Asia/Tbilisi"


# --------------------------------------------------------------------------- #
# Кому и когда (чистые правила)
# --------------------------------------------------------------------------- #


def _parse(text: Any) -> datetime | None:
    text = str(text or "").strip().replace("T", " ")[:19]
    try:
        return datetime.strptime(text, TIME_FMT)
    except ValueError:
        return None


def _fmt(moment: datetime) -> str:
    return moment.strftime(TIME_FMT)


def keepalive_on(row: dict, has_own_proxy: bool) -> bool:
    own = row.get("keepalive")
    return bool(own) if own is not None else bool(has_own_proxy)


def candidate(row: dict) -> bool:
    """Аккаунт, к которому вообще ходим: TikTok или YouTube, вход был, не «вылетел» (ждём свежих cookie)."""
    return (row.get("platform") in PLATFORMS and bool(row.get("has_required_cookies"))
            and not row.get("deleted_at") and (row.get("session_state") or "") != "needs_reauth")


def pick_due(rows: list[dict], at: datetime) -> dict | None:
    """Один аккаунт, которому пора (за проход — один): сначала ни разу не посещённые, потом давно."""
    due = [r for r in rows if (_parse(r.get("keepalive_next_at")) or datetime.min) <= at]
    if not due:
        return None
    due.sort(key=lambda r: (_parse(r.get("keepalive_at")) or datetime.min, int(r["id"])))
    return due[0]


def next_visit(at: datetime, interval_hours: float, rng: random.Random | None = None) -> datetime:
    factor = (rng or random).uniform(1 - JITTER, 1 + JITTER)
    return at + timedelta(hours=interval_hours * factor)


def needs_fresh_visit(row: dict, cfg: dict, at: datetime, has_own_proxy: bool) -> bool:
    """Перед загрузкой: заход нужен, если браузер аккаунта включён и прошлый заход старше порога. Только TikTok:
    загрузка YouTube — сама youtubei.js-сессия, отдельная проверка перед ней ничего не освежает."""
    hours = float(cfg.get("fresh_before_upload_hours") or 0)
    if row.get("platform") not in BROWSER_PLATFORMS:
        return False
    if not cfg.get("enabled") or hours <= 0 or not candidate(row) or not keepalive_on(row, has_own_proxy):
        return False
    last = _parse(row.get("keepalive_at"))
    return last is None or at - last > timedelta(hours=hours)


def classify(platform: str, data: dict) -> tuple[str, str]:
    """Итог захода → (состояние входа, причина). ``data`` — ответ ``node/session-keepalive.cjs`` (TikTok) или
    ``node/youtube-session.mjs`` (YouTube)."""
    if platform == "youtube":
        return classify_youtube(data)
    signals = data.get("signals") if isinstance(data.get("signals"), dict) else {}
    cookies = data.get("cookies") if isinstance(data.get("cookies"), list) else []
    url = str(data.get("url") or "").lower()
    if platform == "tiktok":
        if signals.get("captcha"):
            return "captcha", "TikTok показал капчу"
        if "/login" in url:
            return "needs_reauth", "TikTok перекинул на страницу входа"
        if not C.logged_in_by_cookies("tiktok", cookies):
            return "needs_reauth", "в браузере нет sessionid — TikTok выкинул из аккаунта"
        if signals.get("app_context") and not signals.get("user"):
            return "needs_reauth", "TikTok открылся без входа"
        return "ok", ""
    return "error", f"площадка {platform} без браузера"


def classify_youtube(data: dict) -> tuple[str, str]:
    """Ответ youtubei.js (``node/youtube-session.mjs``) → (состояние входа, причина). Отказ «войдите» (401 и т. п.)
    — по тем же признакам, что у загрузки (``app/providers/youtube.py``); прочий сбой — ``error``, вход не трогаем."""
    from app.providers.youtube import _classify_youtube_error, _safe_error

    if not data.get("ok"):
        error = str(data.get("error") or "")
        safe = _safe_error(error)[:200]
        if _classify_youtube_error(error) == "needs_reauth":
            return "needs_reauth", f"YouTube не пускает с этими cookie: {safe}"
        return "error", f"проверка YouTube не удалась: {safe}"
    logged_in = data.get("loggedIn")
    if logged_in is True:
        return "ok", ""
    if logged_in is False:
        return "needs_reauth", "YouTube отвечает без входа — Google выкинул из аккаунта"
    return "error", "ответ YouTube без признаков входа"


# --------------------------------------------------------------------------- #
# Профиль
# --------------------------------------------------------------------------- #


def profiles_root() -> Path:
    return settings.data_dir / "browser_profiles"


def profile_dir(account_id: int) -> Path:
    return profiles_root() / str(int(account_id))


def prepare_profile(account_id: int, cookies_set_at: str, user_agent: str) -> tuple[Path, dict, bool]:
    """Профиль аккаунта и его постоянный отпечаток. Новые cookie от владельца (или профиля нет) — стереть и
    засеять заново (третье значение — True)."""
    folder = profile_dir(account_id)
    seed_path = folder / SEED_FILE
    try:
        seed = json.loads(seed_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        seed = None
    reseed = not isinstance(seed, dict) or seed.get("cookies_set_at") != (cookies_set_at or "")
    if reseed:
        shutil.rmtree(folder, ignore_errors=True)
        seed = {"cookies_set_at": cookies_set_at or "", "viewport": dict(VIEWPORT), "locale": LOCALE,
                "timezone_id": timezone_id(), "seeded_at": _fmt(now())}
    folder.mkdir(parents=True, exist_ok=True)
    for name in _PROFILE_LOCKS:
        try:
            (folder / name).unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
    seed["user_agent"] = user_agent
    seed_path.write_text(json.dumps(seed, ensure_ascii=False), encoding="utf-8")
    return folder, seed, reseed


def remove_profile(account_id: int) -> None:
    shutil.rmtree(profile_dir(account_id), ignore_errors=True)


# --------------------------------------------------------------------------- #
# Заход
# --------------------------------------------------------------------------- #


def _playwright_dir() -> Path:
    return settings.tiktok_vendor_root / "tiktok_uploader" / "tiktok-signature" / "node_modules" / "playwright-chromium"


def _account_row(store: Any, account_id: int) -> dict | None:
    row = store.db.query_one(
        "SELECT id, platform, label, user_agent, handle, has_required_cookies, deleted_at, cookies_set_at, created_at, "
        "keepalive, keepalive_at, keepalive_next_at, session_state, encrypted_proxy_url FROM accounts WHERE id = ?",
        (int(account_id),))
    return dict(row) if row else None


def _user_agent(row: dict) -> str:
    return (row.get("user_agent") or settings.tiktok_user_agent or "").strip()


def _default_runner(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
    return subprocess.run(*args, **kwargs)


def run_browser(store: Any, row: dict, stay_seconds: float,
                runner: Callable[..., subprocess.CompletedProcess] | None = None) -> dict:
    """Один заход Chromium (под замком браузера). Cookie, прокси и UA — окружением процесса."""
    from app.proxy_config import mask_secrets

    account_id = int(row["id"])
    platform = row["platform"]
    if platform not in BROWSER_PLATFORMS:
        # YouTube браузером — никогда: страница без cookie Google сама стирает вход (см. шапку модуля).
        raise RuntimeError(f"площадка {platform} без браузера")
    user_agent = _user_agent(row)
    folder, seed, reseed = prepare_profile(account_id, row.get("cookies_set_at") or row.get("created_at") or "",
                                           user_agent)
    env = {key: value for key, value in os.environ.items() if key not in _SECRET_ENV}
    env["KA_COOKIES"] = json.dumps(C.playwright_cookies(store.get_account_cookies(account_id), platform))
    proxy = store.get_account_proxy_url(account_id)
    if proxy:
        env["KA_PROXY"] = proxy
    if user_agent:
        env["KA_UA"] = user_agent
    env["KA_CONFIG"] = json.dumps({
        "platform": platform, "url": HOME_URLS[platform], "profileDir": str(folder), "reseed": reseed,
        "stayMs": int(stay_seconds * 1000), "viewport": seed.get("viewport") or VIEWPORT,
        "locale": seed.get("locale") or LOCALE, "timezoneId": seed.get("timezone_id") or timezone_id(),
    })
    try:
        proc = (runner or _default_runner)([settings.node_bin, str(_SCRIPT), str(_playwright_dir())], text=True,
                                           capture_output=True, timeout=NODE_TIMEOUT, check=False, env=env)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"браузер не уложился в {NODE_TIMEOUT} с") from None
    except OSError as exc:
        raise RuntimeError(f"node не запустился: {exc}") from None
    if proc.returncode != 0:
        tail = [line for line in (proc.stderr or "").splitlines() if line.strip()][-1:]
        raise RuntimeError(mask_secrets(" ".join(tail) or f"код {proc.returncode}")[:300])
    try:
        data = json.loads((proc.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise RuntimeError("браузер вернул не JSON") from None
    if not isinstance(data, dict):
        raise RuntimeError("браузер вернул не JSON")
    data["reseeded"] = reseed
    return data


def run_youtube(store: Any, row: dict, runner: Callable[..., subprocess.CompletedProcess] | None = None) -> dict:
    """Проверка входа YouTube через youtubei.js (``node/youtube-session.mjs``), без Chromium: те же cookie
    (``studio.youtube.com``) и прокси, что у загрузки. Секреты — через stdin, не аргументами."""
    from app.cookies import to_cookie_header_for_host
    from app.proxy_config import mask_secrets

    account_id = int(row["id"])
    payload = {"cookie": to_cookie_header_for_host(store.get_account_cookies(account_id), YOUTUBE_COOKIE_HOST),
               "proxyUrl": store.get_account_proxy_url(account_id) or ""}
    if (row.get("handle") or "").strip():
        payload["channel"] = row["handle"].strip()
    try:
        proc = (runner or _default_runner)([settings.node_bin, str(_YT_SCRIPT)], input=json.dumps(payload),
                                           text=True, capture_output=True, timeout=NODE_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"проверка YouTube не уложилась в {NODE_TIMEOUT} с") from None
    except OSError as exc:
        raise RuntimeError(f"node не запустился: {exc}") from None
    try:
        data = json.loads((proc.stdout or "").strip())
    except ValueError:
        data = None
    if not isinstance(data, dict):
        # Помощник упал до ответа: ни входа, ни свежих cookie не знаем.
        tail = [line for line in (proc.stderr or "").splitlines() if line.strip()][-1:]
        raise RuntimeError(mask_secrets(" ".join(tail) or f"код {proc.returncode}")[:300])
    return data


def _youtube_fresh(data: dict) -> list[dict]:
    """``refreshedCookies`` помощника (имя → значение, всё с youtube.com) — в вид ``{name, value, domain}``."""
    refreshed = data.get("refreshedCookies")
    if not isinstance(refreshed, dict):
        return []
    return [{"name": str(name), "value": value, "domain": ".youtube.com"} for name, value in refreshed.items()]


def visit(store: Any, account_id: int, *, why: str = "schedule", quick: bool = False,
          runner: Callable[..., Any] | None = None, rng: random.Random | None = None) -> dict:
    """Зайти аккаунтом (TikTok — браузером, YouTube — youtubei.js), сохранить свежие cookie, записать состояние
    входа. Один заход за раз."""
    from app.browser_lock import browser_slot

    rng = rng or random.Random()
    row = _account_row(store, account_id)
    if not row or row["platform"] not in PLATFORMS or row.get("deleted_at"):
        return {"account_id": int(account_id), "skipped": "не TikTok/YouTube или аккаунта нет"}
    platform = row["platform"]
    lo, hi = QUICK_STAY_SECONDS if quick else STAY_SECONDS
    stay = rng.uniform(lo, hi)
    cfg = get_settings(store)
    with _visit_lock:
        _status["running"] = {"account_id": int(account_id), "why": why, "since": _fmt(now())}
        try:
            try:
                if platform in BROWSER_PLATFORMS:
                    with browser_slot("keepalive", f"аккаунт {account_id}"):
                        data = run_browser(store, row, stay, runner=runner)
                else:
                    data = run_youtube(store, row, runner=runner)
                error = ""
            except Exception as exc:  # noqa: BLE001 - сбой захода — состояние error, повтор позже
                data, error = {}, str(exc)[:300] or type(exc).__name__
        finally:
            _status["running"] = None
    at = now()
    names: list[str] = []
    state, reason = ("error", error) if error else classify(platform, data)
    # Сбой захода / проверки — повтор через час; прошлый удачный заход (keepalive_at) не трогаем.
    failed = state == "error"
    next_at = at + timedelta(hours=ERROR_RETRY_HOURS) if failed else next_visit(at, float(cfg["interval_hours"]), rng)
    source = f"keepalive:{why}"
    stored = store.get_account_cookies(int(account_id))
    if platform in BROWSER_PLATFORMS:
        if state == "ok":
            # Только из живой сессии: страница входа выдаёт гостевые cookie, ими вход не перетираем.
            names = C.save_rotated(store, int(account_id), platform, stored, data.get("cookies") or [], source=source)
    elif state not in ("needs_reauth", "captcha"):
        # YouTube — как загрузка: и при сбое запроса (Google мог уже сменить __Secure-*PSIDTS), но не из ответа
        # без входа.
        names = C.save_rotated(store, int(account_id), platform, stored, _youtube_fresh(data), source=source)
    health = session_health.record(store, int(account_id), state, reason, source=source)
    if failed:
        store.db.execute("UPDATE accounts SET keepalive_next_at = ? WHERE id = ?", (_fmt(next_at), int(account_id)))
    else:
        store.db.execute("UPDATE accounts SET keepalive_at = ?, keepalive_next_at = ? WHERE id = ?",
                         (_fmt(at), _fmt(next_at), int(account_id)))
    channel = data.get("channel") if platform == "youtube" and isinstance(data.get("channel"), dict) else None
    if channel and not channel.get("found"):
        log.warning("keepalive: account=%s youtube channel %s not visible to this login", account_id,
                    channel.get("label") or "?")
    result = {"account_id": int(account_id), "platform": platform, "why": why, "state": state,
              "reason": reason, "rotated": names, "reseeded": bool(data.get("reseeded")),
              "telegram": health.get("sent", False), "at": _fmt(at), "next_at": _fmt(next_at)}
    if channel:
        result["channel_found"] = bool(channel.get("found"))
    log.info("keepalive: account=%s why=%s state=%s rotated=%s%s", account_id, why, state, ",".join(names) or "-",
             f" ({reason})" if reason else "")
    _status["last"] = result
    return result


def eligible(store: Any) -> list[dict]:
    rows = store.db.query_all(
        "SELECT id, platform, label, has_required_cookies, deleted_at, keepalive, keepalive_at, keepalive_next_at, "
        "session_state, encrypted_proxy_url FROM accounts WHERE deleted_at IS NULL AND platform IN ('tiktok', 'youtube') "
        "ORDER BY id")
    out = []
    for row in rows:
        row = dict(row)
        if candidate(row) and keepalive_on(row, bool(row.pop("encrypted_proxy_url", ""))):
            out.append(row)
    return out


def tick(store: Any, runner: Callable[..., Any] | None = None) -> dict | None:
    """Один проход фона: не больше одного захода."""
    cfg = get_settings(store)
    if not cfg["enabled"]:
        return None
    row = pick_due(eligible(store), now())
    if not row:
        return None
    return visit(store, int(row["id"]), why="schedule", runner=runner)


def before_upload(store: Any, account_id: int, runner: Callable[..., Any] | None = None) -> dict | None:
    """Перед загрузкой: прошлый заход старше порога — короткий заход сейчас (сессия свежая). Сбой не мешает загрузке."""
    try:
        row = _account_row(store, account_id)
        if not row:
            return None
        has_proxy = bool(row.pop("encrypted_proxy_url", ""))
        if not needs_fresh_visit(row, get_settings(store), now(), has_proxy):
            return None
        return visit(store, int(account_id), why="upload", quick=True, runner=runner)
    except Exception as exc:  # noqa: BLE001 - загрузка важнее захода
        log.warning("keepalive before upload failed: account=%s %s", account_id, type(exc).__name__)
        return None


def reset_youtube_browser_states(store: Any) -> list[int] | None:
    """Один раз после ухода YouTube из браузера (зовётся при старте, ``app/main.py``): снять состояния входа YouTube,
    которые ставил браузер, — ложное «вылетел» 11.11 и прочее (``ok`` / ``captcha`` / ``error`` у YouTube ставил
    только он). «Вылетел» от отказа загрузки (``app/worker.py``, своя причина) остаётся. Состояние — '' (не проверяли),
    до первой проверки youtubei.js. Профили Chromium YouTube-аккаунтов больше не нужны — стираются.
    Переключатель ``accounts.keepalive`` не трогается. Вернуть id сброшенных; уже делали — None."""
    if store.get_app_setting_value(YT_BROWSER_RESET_KEY, None):
        return None
    rows = [dict(r) for r in store.db.query_all(
        "SELECT id, deleted_at, session_state, session_reason FROM accounts WHERE platform = 'youtube'")]
    reset = [int(r["id"]) for r in rows if not r["deleted_at"] and (r["session_state"] or "")
             and (r["session_state"] != "needs_reauth" or (r["session_reason"] or "") in _YT_BROWSER_REASONS)]
    for account_id in reset:
        store.db.execute(
            "UPDATE accounts SET session_state = '', session_reason = '', session_alerted = '', "
            "session_checked_at = NULL, keepalive_next_at = NULL WHERE id = ?", (account_id,))
    for row in rows:
        remove_profile(int(row["id"]))
    store.set_app_setting(YT_BROWSER_RESET_KEY, {"done_at": _fmt(now()), "accounts": reset})
    if reset:
        log.info("keepalive: youtube browser states reset: accounts=%s", ",".join(map(str, reset)))
    return reset


def status() -> dict:
    return {"running": _status["running"], "last": _status["last"]}


class SessionKeeper:
    """Фон, как ``StatsCollector``: раз в ``TICK_SECONDS`` — не больше одного захода браузера аккаунта."""

    def __init__(self, store: Any) -> None:
        self.store = store
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive() and not self._stop.is_set():
            return
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(self._stop,), name="session-keeper", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self, stop: threading.Event) -> None:
        if stop.wait(FIRST_DELAY_SECONDS):
            return
        while not stop.is_set():
            try:
                tick(self.store)
            except Exception:  # noqa: BLE001 - фон не должен умирать
                log.exception("keepalive tick failed")
            stop.wait(TICK_SECONDS)
