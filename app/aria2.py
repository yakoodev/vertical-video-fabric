"""aria2c для торрентов: свой процесс на каждую загрузку и его JSON-RPC (только 127.0.0.1, со своим секретом).

Почему aria2 (GPL-2.0+, отдельная программа из пакета Debian — в код сервиса не линкуется): magnet и .torrent,
выбор файлов (``select-file``), DHT, остановка раздачи сразу после загрузки (``seed-time=0``), потолок отдачи —
всё из коробки, а управление — обычный HTTP JSON-RPC без нативных колёс Python (libtorrent тянул бы сборку под
каждую версию Python образа).

Секрет RPC и прочие настройки лежат в файле ``aria2.conf`` рабочего каталога (права 600), а не в argv: аргументы
процесса видно в ``ps`` контейнера. Прокси — тоже не в argv, а окружением процесса, и только если владелец задал
TORRENT_PROXY_URL: общие HTTP(S)_PROXY контейнера aria2 не наследует. У aria2 прокси касается только HTTP(S)
(трекеры по http, веб-сиды); соединения с пирами и DHT идут напрямую всегда.
"""

from __future__ import annotations

import os
import secrets
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx

from app.proxy_config import mask_secrets

PROXY_ENV_KEYS = ("http_proxy", "https_proxy", "ftp_proxy", "all_proxy", "no_proxy")


class Aria2Error(RuntimeError):
    """aria2c не запустился или RPC ответил ошибкой."""


class Aria2Rpc:
    """Тонкий клиент JSON-RPC aria2: методы называются как в документации aria2 (без префикса ``aria2.``)."""

    def __init__(self, port: int, secret: str, *, timeout: float = 10.0) -> None:
        self.url = f"http://127.0.0.1:{port}/jsonrpc"
        self._secret = secret
        self._timeout = timeout
        self._ids = 0

    def call(self, method: str, *params: Any) -> Any:
        self._ids += 1
        body = {"jsonrpc": "2.0", "id": str(self._ids), "method": f"aria2.{method}",
                "params": [f"token:{self._secret}", *params]}
        try:
            # trust_env=False: к своему 127.0.0.1 — никогда не через прокси из окружения.
            response = httpx.post(self.url, json=body, timeout=self._timeout, trust_env=False)
        except httpx.HTTPError as exc:
            raise Aria2Error(f"aria2 RPC недоступен: {exc.__class__.__name__}") from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise Aria2Error(f"aria2 RPC ответил не JSON (HTTP {response.status_code})") from exc
        if payload.get("error"):
            raise Aria2Error(f"aria2: {payload['error'].get('message') or payload['error']}")
        return payload.get("result")

    # Методы, которыми пользуется app/torrents.py.
    def get_version(self) -> dict:
        return self.call("getVersion")

    def add_uri(self, uris: list[str], options: dict | None = None) -> str:
        return self.call("addUri", uris, options or {})

    def add_torrent(self, torrent_b64: str, options: dict | None = None) -> str:
        return self.call("addTorrent", torrent_b64, [], options or {})

    def tell_status(self, gid: str, keys: list[str] | None = None) -> dict:
        return self.call("tellStatus", gid, keys) if keys else self.call("tellStatus", gid)

    def get_files(self, gid: str) -> list[dict]:
        return self.call("getFiles", gid)

    def change_option(self, gid: str, options: dict) -> str:
        return self.call("changeOption", gid, options)

    def unpause(self, gid: str) -> str:
        return self.call("unpause", gid)

    def force_remove(self, gid: str) -> str:
        return self.call("forceRemove", gid)

    def force_shutdown(self) -> str:
        return self.call("forceShutdown")


def aria2_binary() -> str:
    from app.settings import settings

    configured = settings.aria2_bin
    found = shutil.which(configured) if not os.path.isabs(configured) else (configured if Path(configured).is_file() else None)
    if not found:
        raise Aria2Error("aria2c не установлен: торренты качает aria2 (пакет aria2 в образе сервиса)")
    return found


def free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def daemon_options(*, port: int, secret: str, download_dir: Path, log_path: Path, dht_path: Path) -> dict[str, str]:
    """Настройки процесса aria2c (в aria2.conf). Раздача после загрузки выключена, отдача ограничена."""
    from app.settings import settings

    return {
        "enable-rpc": "true",
        "rpc-listen-all": "false",
        "rpc-listen-port": str(port),
        "rpc-secret": secret,
        "rpc-allow-origin-all": "false",
        # .torrent приходит в RPC base64: с запасом на большие сезоны.
        "rpc-max-request-size": "16M",
        "dir": str(download_dir),
        # Скачанное по magnet не стартует само: сначала человек выбирает файлы (aria2.getFiles → select-file).
        "pause-metadata": "true",
        "bt-save-metadata": "false",
        # Раздача после загрузки — 0 секунд (seed-time=0 срабатывает первым). seed-ratio=0.0 у aria2 значит «без
        # предела по рейтингу», поэтому останавливает именно seed-time; оба заданы явно, чтобы это было видно.
        "seed-time": "0",
        "seed-ratio": "0.0",
        # Пока качаем, пиры всё равно берут куски — потолок отдачи.
        "max-overall-upload-limit": settings.torrent_upload_limit,
        "bt-remove-unselected-file": "true",
        "enable-dht": "true",
        "dht-file-path": str(dht_path),
        "bt-enable-lpd": "false",
        # В Docker без IPv6 попытки по v6 только тянут время.
        "disable-ipv6": "true",
        # Без предвыделения: на томах без fallocate (bind на диск Windows) aria2 падал бы, а prealloc пишет нули часами.
        "file-allocation": "none",
        "auto-file-renaming": "false",
        "allow-overwrite": "true",
        "continue": "true",
        "max-concurrent-downloads": "1",
        "summary-interval": "0",
        "console-log-level": "warn",
        "log": str(log_path),
        "log-level": "warn",
        "check-certificate": "true",
    }


def write_conf(path: Path, options: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(f"{key}={value}\n" for key, value in options.items())
    # Секрет RPC — только владельцу файла.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        out.write(text)


def process_env() -> dict[str, str]:
    """Окружение aria2c: без прокси контейнера; TORRENT_PROXY_URL — только если владелец его задал."""
    from app.settings import settings

    env = {k: v for k, v in os.environ.items() if k.lower() not in PROXY_ENV_KEYS}
    proxy = settings.torrent_proxy_url
    if proxy:
        env["all_proxy"] = proxy
    return env


class Aria2Daemon:
    """Один процесс aria2c с RPC на свободном локальном порту. ``stop`` гасит только свой PID."""

    def __init__(self, workdir: Path, *, start_timeout: float = 15.0) -> None:
        self.workdir = workdir
        self.download_dir = workdir / "data"
        self.log_path = workdir / "aria2.log"
        self.start_timeout = start_timeout
        self.proc: subprocess.Popen | None = None
        self.rpc: Aria2Rpc | None = None

    def start(self) -> Aria2Rpc:
        from app.settings import settings

        binary = aria2_binary()
        self.download_dir.mkdir(parents=True, exist_ok=True)
        port, secret = free_local_port(), secrets.token_urlsafe(24)
        dht = settings.runtime_dir / "aria2" / "dht.dat"
        dht.parent.mkdir(parents=True, exist_ok=True)
        conf = self.workdir / "aria2.conf"
        write_conf(conf, daemon_options(port=port, secret=secret, download_dir=self.download_dir,
                                        log_path=self.log_path, dht_path=dht))
        self.proc = subprocess.Popen(
            [binary, f"--conf-path={conf}"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=process_env(), cwd=str(self.workdir),
        )
        rpc = Aria2Rpc(port, secret)
        deadline = time.monotonic() + self.start_timeout
        while True:
            if self.proc.poll() is not None:
                raise Aria2Error(f"aria2c завершился при старте (код {self.proc.returncode}): {self.log_tail()}")
            try:
                rpc.get_version()
                break
            except Aria2Error:
                if time.monotonic() > deadline:
                    self.stop()
                    raise Aria2Error("aria2c не ответил по RPC за отведённое время")
                time.sleep(0.2)
        self.rpc = rpc
        return rpc

    def log_tail(self, limit: int = 400) -> str:
        try:
            text = self.log_path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return ""
        return mask_secrets(text[-limit:])

    def stop(self) -> None:
        proc, rpc = self.proc, self.rpc
        if proc is None:
            return
        if proc.poll() is None and rpc is not None:
            try:
                rpc.force_shutdown()
            except Aria2Error:
                pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        self.proc = None
        self.rpc = None
