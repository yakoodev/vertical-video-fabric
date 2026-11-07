"""Всё, что касается TikTok-аккаунта, идёт через его прокси. Настоящей сети нет.

Локальный прокси отвечает на CONNECT, расшифровывает TLS своим сертификатом, записывает запросы и сам играет TikTok.
Сторож на ``socket.connect`` не выпускает ничего за пределы 127.0.0.1 и записывает попытки: прямой запрос с «настоящего
IP» провалит тест, а до площадки не дойдёт."""

from __future__ import annotations

import base64
import datetime as dt
import json
import shutil
import socket
import ssl
import subprocess
import threading
from pathlib import Path

import pytest
import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app import platform_stats
from app.cookies import parse_cookie_input
from app.crypto import CookieCipher
from app.db import Database
from app.providers import tiktok
from app.providers.tiktok import TikTokProvider
from app.settings import settings
from app.store import AppStore

ROOT = Path(__file__).resolve().parent.parent
UPLOAD_HOST = "upload.example"
ITEM_ID = "7431234567890123456"
PASTED_PROXY = "http://user:p%40ss@127.0.0.1:{port}"


def _tls_files(folder: Path) -> tuple[Path, Path]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "fake tiktok")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("www.tiktok.com"), x509.DNSName(UPLOAD_HOST)]), False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = folder / "fake.pem", folder / "fake.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    return cert_path, key_path


def _tiktok_answer(path: str) -> dict:
    if "project/create" in path:
        return {"project": {"project_id": "p1"}}
    if "video/upload/auth" in path:
        return {"video_token_v5": {"access_key_id": "k", "secret_acess_key": "s", "session_token": "t"}}
    if "ApplyUploadInner" in path:
        return {"Result": {"InnerUploadAddress": {"UploadNodes": [{
            "Vid": "v0vid", "StoreInfos": [{"StoreUri": "store/uri", "Auth": "auth"}],
            "UploadHost": UPLOAD_HOST, "SessionKey": "sk"}]}}}
    if "web/project/post/v1" in path:
        return {"status_code": 0, "single_post_resp_list": [{"item_id": ITEM_ID}]}
    return {}


class FakeProxy(threading.Thread):
    """Прокси с записью: ``connects`` — (хост, Proxy-Authorization), ``requests`` — (хост, метод, путь, тело)."""

    def __init__(self, cert: Path, key: Path) -> None:
        super().__init__(daemon=True)
        self.server = socket.create_server(("127.0.0.1", 0))
        self.port = self.server.getsockname()[1]
        self.tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.tls.load_cert_chain(cert, key)
        self.connects: list[tuple[str, str]] = []
        self.requests: list[tuple[str, str, str, bytes]] = []

    def run(self) -> None:
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def close(self) -> None:
        self.server.close()

    @staticmethod
    def _head(conn) -> tuple[str, dict[str, str], bytes]:
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = conn.recv(65536)
            if not chunk:
                return "", {}, b""
            data += chunk
        head, rest = data.split(b"\r\n\r\n", 1)
        lines = head.decode("latin-1").split("\r\n")
        headers = {k.strip().lower(): v.strip() for k, v in (line.split(":", 1) for line in lines[1:])}
        return lines[0], headers, rest

    def _serve(self, conn) -> None:
        try:
            line, headers, _ = self._head(conn)
            if not line.startswith("CONNECT "):
                self.requests.append(("(без туннеля)", *line.split(" ")[:2], b""))
                return
            host = line.split(" ")[1].split(":")[0]
            self.connects.append((host, headers.get("proxy-authorization", "")))
            conn.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            tls = self.tls.wrap_socket(conn, server_side=True)
            while True:
                line, headers, rest = self._head(tls)
                if not line:
                    return
                length = int(headers.get("content-length") or 0)
                while len(rest) < length:
                    rest += tls.recv(1024 * 1024)
                method, path = line.split(" ")[:2]
                self.requests.append((host, method, path, rest))
                body = json.dumps(_tiktok_answer(path)).encode()
                tls.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                            + str(0 if method == "HEAD" else len(body)).encode() + b"\r\n\r\n"
                            + (b"" if method == "HEAD" else body))
        except (OSError, ssl.SSLError, ValueError):
            pass
        finally:
            conn.close()


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Прокси, сторож сокетов, сессия с доверием к его сертификату и подпись с записью вызова."""
    cert, key = _tls_files(tmp_path)
    proxy = FakeProxy(cert, key)
    proxy.start()

    leaks: list = []
    real_connect = socket.socket.connect

    def guard(sock, address):
        if isinstance(address, tuple) and address[0] not in ("127.0.0.1", "::1", "localhost"):
            leaks.append(address)
            raise OSError(f"прямое соединение запрещено тестом: {address}")
        return real_connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", guard)

    real_session = requests.Session

    class TrustFakeCert(real_session):
        verify = property(lambda self: str(cert), lambda self, value: None)

    monkeypatch.setattr(tiktok.requests, "Session", TrustFakeCert)
    monkeypatch.setattr(settings, "publish_upload_retry_seconds", 0)
    monkeypatch.setattr(settings, "runtime_dir", tmp_path / "runtime")
    monkeypatch.setattr(tiktok.TikTokProvider, "_ensure_signature_helper", lambda self: None)
    signed: list[str] = []

    def signature(signature_dir, user_agent, url, proxy_url=""):
        signed.append(proxy_url)
        return json.dumps({"data": {"x-bogus": "xb", "signature": "sig"}})

    monkeypatch.setattr(tiktok, "_run_signature_helper", signature)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"0123456789")
    yield {"proxy": proxy, "leaks": leaks, "signed": signed, "video": video}
    proxy.close()


def _upload(world, proxy_url: str):
    return TikTokProvider().upload(
        cookies=parse_cookie_input("sessionid=abc; msToken=mt", "tiktok"), file_path=world["video"],
        title="Заголовок #тест", description="", privacy="public", allow_comments=True, account_label="acc",
        proxy_url=proxy_url,
    )


def test_every_upload_request_goes_through_the_account_proxy(world, monkeypatch):
    # Прокси в окружении, который в requests перекрыл бы прокси аккаунта: туда не должно уйти ничего.
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    proxy = world["proxy"]
    pasted = PASTED_PROXY.format(port=proxy.port)

    result = _upload(world, pasted)

    assert result.status == "succeeded", result.error
    assert result.remote_url == f"https://www.tiktok.com/@/video/{ITEM_ID}"
    assert world["leaks"] == []
    assert world["signed"] == [pasted]  # и браузер подписи получил тот же прокси
    assert {host for host, _ in proxy.connects} == {"www.tiktok.com", UPLOAD_HOST}
    # Адрес из формы работает целиком: логин и пароль (с %40 → @) дошли до прокси в каждом туннеле.
    expected_auth = "Basic " + base64.b64encode(b"user:p@ss").decode()
    assert {auth for _, auth in proxy.connects} == {expected_auth}
    seen = [(host, method, path.split("?")[0]) for host, method, path, _ in proxy.requests]
    for step in (("www.tiktok.com", "POST", "/api/v1/web/project/create/"),
                 ("www.tiktok.com", "GET", "/api/v1/video/upload/auth/"),
                 ("www.tiktok.com", "GET", "/top/v1"),
                 (UPLOAD_HOST, "POST", "/store/uri"),
                 ("www.tiktok.com", "POST", "/top/v1"),
                 ("www.tiktok.com", "HEAD", "/"),
                 ("www.tiktok.com", "POST", "/tiktok/web/project/post/v1/")):
        assert step in seen, f"шаг {step} не прошёл через прокси: {seen}"


def test_cyrillic_caption_reaches_the_post_as_utf8(world):
    result = _upload(world, PASTED_PROXY.format(port=world["proxy"].port))
    assert result.status == "succeeded", result.error
    post = next(body for _, _, path, body in world["proxy"].requests if "web/project/post/v1" in path)
    assert json.loads(post.decode("utf-8"))["single_post_req_list"][0]["single_post_feature_info"]["text"] == \
        "Заголовок #тест"


def test_dead_proxy_is_an_error_and_never_a_direct_connection(world):
    closed = socket.create_server(("127.0.0.1", 0))
    port = closed.getsockname()[1]
    closed.close()

    result = _upload(world, f"http://user:secretpass@127.0.0.1:{port}")

    assert result.status == "failed"
    assert world["leaks"] == [] and world["proxy"].requests == []
    assert world["signed"] == []  # до поста не дошло
    assert "secretpass" not in result.error


def test_signature_helper_gets_the_proxy_through_env_not_argv(monkeypatch, tmp_path):
    seen = {}

    def fake_run(args, **kwargs):
        seen.update(args=args, env=kwargs["env"])
        return subprocess.CompletedProcess(args, 0, stdout='{"data":{}}', stderr="")

    monkeypatch.setattr(tiktok.subprocess, "run", fake_run)
    tiktok._run_signature_helper(tmp_path, "UA", "https://www.tiktok.com/x?msToken=t", "http://u:secret@10.0.0.1:8080")
    assert seen["env"]["TIKTOK_SIGN_PROXY"] == "http://u:secret@10.0.0.1:8080"
    assert not any("secret" in str(arg) for arg in seen["args"])

    monkeypatch.setenv("TIKTOK_SIGN_PROXY", "http://stale:stale@10.0.0.2:1")
    tiktok._run_signature_helper(tmp_path, "UA", "https://www.tiktok.com/x", "")
    assert "TIKTOK_SIGN_PROXY" not in seen["env"]  # аккаунт без прокси не берёт чужой


def test_signature_timeout_does_not_leak_ms_token(monkeypatch, tmp_path):
    def fake_run(args, **kwargs):
        raise subprocess.TimeoutExpired(args, 60)

    monkeypatch.setattr(tiktok.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError) as err:
        tiktok._run_signature_helper(tmp_path, "UA", "https://www.tiktok.com/x?msToken=SECRET")
    assert "SECRET" not in str(err.value)


def _signer_stub(folder: Path, init: str = "") -> None:
    (folder / "index.js").write_text(
        "class Signer {\n"
        "  constructor(url, ua) { this.url = url; this.ua = ua; this.options = {headless: true}; }\n"
        f"  async init() {{ {init} require('fs').writeFileSync(process.env.STUB_OUT, JSON.stringify(this.options)); }}\n"
        "  async sign() { return {'x-bogus': 'xb', signature: 'sig'}; }\n"
        "  async navigator() { return {user_agent: this.ua}; }\n"
        "  async close() {}\n"
        "}\nmodule.exports = Signer;\n", encoding="utf-8")


def _run_sign_script(folder: Path) -> subprocess.CompletedProcess:
    return subprocess.run([settings.node_bin, str(ROOT / "node" / "tiktok-sign.cjs"), str(folder), "https://x.test/?a=1", "UA"],
                          capture_output=True, text=True, timeout=30)


@pytest.mark.skipif(not shutil.which(settings.node_bin), reason="нет node")
def test_sign_script_hands_proxy_to_chromium(tmp_path, monkeypatch):
    _signer_stub(tmp_path)
    out = tmp_path / "options.json"
    monkeypatch.setenv("STUB_OUT", str(out))
    monkeypatch.setenv("TIKTOK_SIGN_PROXY", "http://user:p%40ss@geo.example:8080")
    proc = _run_sign_script(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["data"]["x-bogus"] == "xb"
    assert json.loads(out.read_text(encoding="utf-8"))["proxy"] == {
        "server": "http://geo.example:8080", "username": "user", "password": "p@ss"}


@pytest.mark.skipif(not shutil.which(settings.node_bin), reason="нет node")
def test_sign_script_reports_failure_without_details(tmp_path, monkeypatch):
    _signer_stub(tmp_path, init="throw new Error('net::ERR_PROXY_CONNECTION_FAILED at url?msToken=x\\nbrowser log');")
    monkeypatch.setenv("TIKTOK_SIGN_PROXY", "http://user:secret@geo.example:8080")
    proc = _run_sign_script(tmp_path)
    assert proc.returncode == 1 and proc.stdout == ""
    assert "ERR_PROXY_CONNECTION_FAILED" in proc.stderr
    assert "browser log" not in proc.stderr and "secret" not in proc.stderr


# --- статистика и тревога «0 просмотров» -------------------------------------------------------------------------- #


def _store(tmp_path) -> AppStore:
    db = Database(tmp_path / "stats.sqlite")
    db.init()
    return AppStore(db, CookieCipher(tmp_path / "secret.key"))


def _stats_attempts(monkeypatch) -> list:
    """Подменяет yt-dlp: каждый запуск падает; в списке — через какой прокси пошёл (None — напрямую)."""
    calls: list = []

    def fake_run(args, **kwargs):
        env = kwargs.get("env")
        calls.append(env.get("HTTPS_PROXY") if env is not None else None)
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="ERROR: blocked")

    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.setattr(platform_stats.subprocess, "run", fake_run)
    monkeypatch.setattr("app.proxy_config.download_proxy", lambda: "http://download:p@10.0.0.9:1")
    return calls


def test_stats_of_a_proxied_account_use_only_that_proxy(tmp_path, monkeypatch):
    store = _store(tmp_path)
    account = store.upsert_account("tiktok", "tt", "sessionid=a", "http://u:p@10.9.8.7:3128")
    calls = _stats_attempts(monkeypatch)
    target = {"remote_url": f"https://www.tiktok.com/@/video/{ITEM_ID}", "account_id": account["id"], "remote_id": ITEM_ID}

    with pytest.raises(RuntimeError):
        platform_stats._fetch_one(store, target, {})

    assert calls == ["http://u:p@10.9.8.7:3128"]  # ни напрямую, ни через запасной прокси загрузок


def test_stats_of_an_account_without_proxy_keep_the_direct_then_download_order(tmp_path, monkeypatch):
    store = _store(tmp_path)
    account = store.upsert_account("tiktok", "tt", "sessionid=a")
    calls = _stats_attempts(monkeypatch)
    target = {"remote_url": f"https://www.tiktok.com/@/video/{ITEM_ID}", "account_id": account["id"], "remote_id": ITEM_ID}

    with pytest.raises(RuntimeError):
        platform_stats._fetch_one(store, target, {})

    assert calls == [None, "http://download:p@10.0.0.9:1"]
