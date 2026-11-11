"""Общий запуск yt-dlp: JS-рантайм, PO-токены, маршруты загрузки и понятные причины отказа.

Все места, где сервис зовёт yt-dlp (источники ``app/ingest.py``, отрезки ``app/video_import.py``,
музыка и звуки, числа публикаций), берут аргументы и порядок попыток отсюда, а не копируют их.

* ``common_args`` — ``--js-runtimes node`` (без JS-рантайма yt-dlp не решает задачки YouTube:
  «No supported JavaScript runtime», ссылки на видео отдают 403) и путь к сборке PO-токенов
  bgutil (``BGUTIL_POT_HOME``, режим «скрипт»: плагин сам запускает node на каждый токен).
* ``run_routes`` — напрямую → через прокси сервиса → если YouTube просит вход, с cookie
  аккаунта «для скачивания» (``app/download_account.py``). Прокси идёт окружением процесса
  (``proxy_config.proxy_env``): его видит и сам yt-dlp, и запущенный им ffmpeg (``http_proxy``,
  отрезки ``--download-sections`` качает ffmpeg; ссылки googlevideo привязаны к IP, кто их
  получил — тот и качает). В argv пароль прокси не попадает — его видно в ``ps``.
* ``failure_hint`` — что сказать человеку: «провайдер режет YouTube» / «YouTube просит вход —
  добавьте аккаунт для скачивания» / видеосервер отвечает 403 / прокси не отвечает.
* ``account_cookie_file`` — cookie аккаунта во временный файл 0600; после yt-dlp обновлённые
  Google значения (``__Secure-*PSIDTS`` и др.) пишутся обратно в аккаунт — потеря ротации
  убивает сессию Google. Файл удаляется сразу после запуска.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator
from urllib.parse import urlsplit

from app.cookies import CookieRecord, parse_cookie_input
from app.proxy_config import mask_secrets

_log = logging.getLogger(__name__)

POT_HOME_ENV = "BGUTIL_POT_HOME"
DEFAULT_POT_HOME = "/opt/bgutil-pot/server"
# Для маршрутов через прокси: из одинаковых по высоте — меньший битрейт (трафик прокси платный).
PROXY_SORT_ARGS = ("-S", "res,fps,+br")

Runner = Callable[[list[str], str], subprocess.CompletedProcess]

TITLE_MARK = "VVF-TITLE:"
# Название ролика после загрузки — строкой с меткой. --print сам включает «тихий» режим, а тихий yt-dlp
# запускает ffmpeg с -loglevel quiet: в ошибке остаётся «ffmpeg exited with code 1» без причины
# (обрыв TLS у провайдера не отличить). --no-quiet возвращает обычный вывод.
PRINT_TITLE_ARGS = ("--no-quiet", "--print", f"after_move:{TITLE_MARK}%(title)s")


def title_from_output(stdout: str | None) -> str:
    """Название из вывода yt-dlp: строка с меткой; без метки (старый тихий вывод) — первая строка."""
    lines = [line.strip() for line in (stdout or "").splitlines() if line.strip()]
    marked = [line.split(TITLE_MARK, 1)[1].strip() for line in lines if TITLE_MARK in line]
    return marked[0] if marked else (lines[0] if lines else "")


# ---------------------------------------------------------------- аргументы


def pot_server_home() -> str:
    """Каталог ``server`` сборки bgutil (там ``build/generate_once.js``); пусто — не установлен."""
    home = (os.environ.get(POT_HOME_ENV) or DEFAULT_POT_HOME).strip()
    return home if home and os.path.isfile(os.path.join(home, "build", "generate_once.js")) else ""


def common_args() -> list[str]:
    """Аргументы для каждого запуска yt-dlp: JS-рантайм node и (если собран) провайдер PO-токенов."""
    args: list[str] = []
    if shutil.which("node"):
        args += ["--js-runtimes", "node"]
    home = pot_server_home()
    if home:
        args += ["--extractor-args", f"youtubepot-bgutilscript:server_home={home}"]
    return args


def is_youtube_url(url: str) -> bool:
    host = (urlsplit((url or "").strip()).hostname or "").lower()
    return host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com")


def ffmpeg_can_use_proxy(proxy: str) -> bool:
    """ffmpeg берёт прокси только из ``http_proxy`` и только вида ``http://``: socks5/https он не умеет.
    Отрезки (``--download-sections``) качает ffmpeg — с таким прокси он пошёл бы напрямую."""
    return (urlsplit(proxy or "").scheme or "").lower() == "http"


# ---------------------------------------------------------------- причины отказа

_LOGIN_RE = re.compile(
    r"Sign in to confirm|not a bot|LOGIN_REQUIRED|confirm your age|--cookies-from-browser or --cookies",
    re.IGNORECASE,
)
_PROXY_RE = re.compile(r"Unable to connect to proxy|ProxyError|407 Proxy Authentication", re.IGNORECASE)
_FORBIDDEN_RE = re.compile(r"HTTP Error 403|403 Forbidden", re.IGNORECASE)
_ISP_RE = re.compile(
    r"IO error: End of file|Error in the pull function|Read timed out|ConnectTimeout|Connection reset by peer"
    r"|Connection aborted|UNEXPECTED_EOF|EOF occurred in violation|timed out|Connection timed out"
    r"|RemoteDisconnected|IncompleteRead",
    re.IGNORECASE,
)
# Отказы «этому адресу YouTube не отдаёт»: вход поможет (аккаунт для скачивания).
FLAGGED = frozenset({"login", "forbidden"})


def classify_failure(text: str) -> str:
    """``login`` (YouTube просит вход / «не бот»), ``proxy`` (прокси не отвечает), ``forbidden`` (видеосервер
    ответил 403 — ссылки не для этого адреса или адрес помечен), ``isp`` (соединение с видеосервером рвётся или
    виснет — так провайдер режет CDN), ``other`` (видео удалено, приватное…)."""
    text = text or ""
    if _LOGIN_RE.search(text):
        return "login"
    if _PROXY_RE.search(text):
        return "proxy"
    if _FORBIDDEN_RE.search(text):
        return "forbidden"
    if _ISP_RE.search(text):
        return "isp"
    return "other"


@dataclass
class Attempt:
    route: str  # direct | proxy | account
    returncode: int
    kind: str  # classify_failure для неудачной, "" для удачной


@dataclass
class RouteResult:
    proc: subprocess.CompletedProcess
    attempts: list[Attempt] = field(default_factory=list)
    account_configured: bool = False

    @property
    def ok(self) -> bool:
        return self.proc.returncode == 0

    def hint(self, site: str = "") -> str:
        return failure_hint(self.attempts, account_configured=self.account_configured, site=site)


_ROUTE_WORDS = {"direct": "напрямую", "proxy": "через прокси", "account": "с аккаунтом для скачивания"}


def _describe(route: str, kind: str, site: str) -> str:
    where = _ROUTE_WORDS.get(route, route)
    return {
        "isp": (f"{where} провайдер режет {site}: соединение с видеосервером рвётся" if route == "direct"
                else f"{where} соединение с видеосервером тоже рвётся"),
        "login": f"{where} YouTube просит вход («подтвердите, что вы не бот») — адрес помечен",
        "forbidden": f"{where} видеосервер отвечает 403 — этому адресу видео не отдают",
        "proxy": "прокси для скачивания не отвечает",
        "proxy_scheme": "прокси для скачивания — socks5/https, а отрезки качает ffmpeg, он умеет только http-прокси",
    }.get(kind, "")


def failure_hint(attempts: Iterable[Attempt], *, account_configured: bool, site: str = "") -> str:
    """Почему не скачалось (по каждому маршруту) и что сделать. Пусто — причина не в сети (видео удалено…).

    Например: «напрямую провайдер режет YouTube: …; через прокси YouTube просит вход … — добавьте аккаунт
    для скачивания: Настройки → Прокси → «Аккаунт YouTube для скачивания»»."""
    failed = [a for a in attempts if a.returncode != 0]
    site = site or "видеосервер"
    parts = [text for a in failed if (text := _describe(a.route, a.kind, site))]
    if not parts:
        return ""
    kinds = {a.route: a.kind for a in failed}
    action = ""
    if FLAGGED & set(kinds.values()) and site == "YouTube":
        if "account" in kinds:
            action = "обновите cookie аккаунта для скачивания (Аккаунты) или смените прокси"
        elif not account_configured:
            action = ("добавьте аккаунт для скачивания: Настройки → Прокси → «Аккаунт YouTube для скачивания» "
                      "(или смените прокси)")
    elif kinds.get("direct") == "isp" and "proxy" not in kinds:
        action = "задайте прокси (Настройки → Прокси)"
    elif kinds.get("proxy") in {"proxy", "proxy_scheme", "isp", "forbidden"}:
        action = "проверьте или смените прокси (Настройки → Прокси)"
    return "; ".join(parts) + (f" — {action}" if action else "")


def _text(proc: subprocess.CompletedProcess) -> str:
    return f"{proc.stdout or ''}\n{proc.stderr or ''}"


# ---------------------------------------------------------------- маршруты


def run_routes(
    run: Runner,
    args: list[str],
    *,
    url: str,
    proxy: str = "",
    store: Any = None,
    before_retry: Callable[[], None] | None = None,
    needs_ffmpeg: bool = False,
) -> RouteResult:
    """yt-dlp по маршрутам: напрямую → через ``proxy`` → (YouTube просит вход) с cookie аккаунта для скачивания.

    ``run(args, proxy)`` — сам запуск (свой у каждого вызывающего: потоковый с прогрессом или простой);
    ``common_args`` добавляет он. ``before_retry`` — убрать недокачанное перед следующей попыткой.
    ``needs_ffmpeg`` — качает ffmpeg (отрезки ``--download-sections``): прокси не ``http://`` он не умеет,
    такой маршрут не запускаем (ссылки, полученные через прокси, напрямую дадут 403).
    Аккаунт берётся только тот, что владелец отметил «для скачивания»; без отметки — никакой.
    """
    from app import download_account

    attempts: list[Attempt] = []

    def attempt(route: str, call_args: list[str], route_proxy: str) -> subprocess.CompletedProcess:
        if attempts and before_retry:
            before_retry()
        proc = run(call_args, route_proxy)
        attempts.append(Attempt(route, proc.returncode, "" if proc.returncode == 0 else classify_failure(_text(proc))))
        return proc

    proc = attempt("direct", args, "")
    if proc.returncode != 0 and proxy and needs_ffmpeg and not ffmpeg_can_use_proxy(proxy):
        _log.warning("download proxy is not http://: ffmpeg cannot use it, proxy route skipped")
        attempts.append(Attempt("proxy", 1, "proxy_scheme"))
        proxy = ""
    if proc.returncode != 0 and proxy:
        # Напрямую не вышло (CDN режет соединение) — повтор через прокси сервиса.
        proc = attempt("proxy", [*PROXY_SORT_ARGS, *args], proxy)
    account = download_account.get_account(store) if is_youtube_url(url) else None
    if proc.returncode != 0 and account and any(a.kind in FLAGGED for a in attempts):
        # Адрес помечен («Sign in to confirm you're not a bot» или 403 на видеосервере) — тот же маршрут,
        # но со входом в аккаунт.
        # Свой прокси у аккаунта важнее: сессия Google живёт дольше на одном адресе.
        route_proxy = account.get("own_proxy") or (proxy if any(a.route == "proxy" for a in attempts) else "")
        with account_cookie_file(store, account["id"]) as cookie_path:
            proc = attempt("account", [*(PROXY_SORT_ARGS if route_proxy else ()), "--cookies", str(cookie_path), *args],
                           route_proxy)
    return RouteResult(proc, attempts, account_configured=bool(account))


# ---------------------------------------------------------------- cookie аккаунта

_NETSCAPE_HEADER = "# Netscape HTTP Cookie File\n# Временный файл сервиса: удаляется после запуска yt-dlp.\n"


def netscape_text(cookies: Iterable[CookieRecord], default_domain: str = ".youtube.com") -> str:
    lines = [_NETSCAPE_HEADER]
    for c in cookies:
        if not c.name or any(ch in f"{c.name}{c.value}{c.domain}{c.path}" for ch in "\t\r\n"):
            continue
        domain = c.domain or default_domain
        prefix = "#HttpOnly_" if c.http_only else ""
        lines.append("\t".join([
            f"{prefix}{domain}", "TRUE" if domain.startswith(".") else "FALSE", c.path or "/",
            "TRUE" if c.secure else "FALSE", str(int(c.expires or 0)), c.name, c.value,
        ]) + "\n")
    return "".join(lines)


def _domain_ok(domain: str) -> bool:
    d = (domain or "").lower().lstrip(".")
    return any(d == base or d.endswith("." + base) for base in ("youtube.com", "google.com"))


def rotated_cookie_values(original: Iterable[CookieRecord], jar_text: str) -> dict[str, str]:
    """Что yt-dlp поменял в банке cookie: {имя: новое значение} — только домены YouTube/Google.

    Записи с тем же доменом, что у сохранённой, важнее записей того же имени с другим доменом
    (``update_account_cookie_values`` сливает по имени)."""
    try:
        fresh = parse_cookie_input(jar_text, "youtube")
    except ValueError:
        return {}
    by_key = {(c.domain.lower(), c.name): c.value for c in original}
    by_name = {c.name: c.value for c in original}
    fresh = [c for c in fresh if c.value and _domain_ok(c.domain)]
    updates: dict[str, str] = {}
    exact: set[str] = set()
    for c in fresh:  # сначала записи с тем же доменом, что у сохранённой
        old = by_key.get((c.domain.lower(), c.name))
        if old is not None:
            exact.add(c.name)
            if c.value != old:
                updates[c.name] = c.value
    for c in fresh:  # потом новые имена (и те же имена с другого домена, если родной записи нет)
        if c.name not in exact and c.value != by_name.get(c.name):
            updates.setdefault(c.name, c.value)
    return updates


@contextmanager
def account_cookie_file(store: Any, account_id: int) -> Iterator[Path]:
    """Cookie аккаунта → временный файл 0600 (в своём каталоге 0700); после — ротация обратно в аккаунт, файл удалён.

    Обратная запись — и при неудаче yt-dlp: Google мог уже сменить ``__Secure-*PSIDTS``."""
    cookies = store.get_account_cookies(account_id)
    workdir = Path(tempfile.mkdtemp(prefix="vvf-ytc-"))
    path = workdir / "cookies.txt"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(netscape_text(cookies))
        yield path
    finally:
        try:
            if path.exists():
                updates = rotated_cookie_values(cookies, path.read_text(encoding="utf-8", errors="replace"))
                if updates and store.update_account_cookie_values(account_id, updates):
                    # Имена — можно, значения — никогда.
                    _log.info("download account %s: rotated cookies saved (%s)", account_id, ", ".join(sorted(updates)))
        except Exception as exc:  # noqa: BLE001 - обратная запись не должна ронять загрузку
            _log.warning("download account %s: cookie write-back failed: %s", account_id,
                         mask_secrets(type(exc).__name__))
        shutil.rmtree(workdir, ignore_errors=True)


def plain_runner(timeout: float) -> Runner:
    """Простой запуск ``yt-dlp`` (без потокового прогресса) для ``run_routes``: музыка, звуки.
    Прокси — окружением; вывод — с замаскированными кредами (yt-dlp может назвать прокси в ошибке)."""
    from app.proxy_config import proxy_env

    def run(args: list[str], proxy: str) -> subprocess.CompletedProcess:
        proc = subprocess.run(["yt-dlp", *common_args(), *args], capture_output=True, text=True, timeout=timeout,
                              check=False, env=proxy_env(proxy) if proxy else None)
        return subprocess.CompletedProcess(proc.args, proc.returncode, mask_secrets(proc.stdout or ""),
                                           mask_secrets(proc.stderr or ""))

    return run
