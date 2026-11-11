"""yt-dlp: JS-рантайм и PO-токены, маршруты (напрямую → прокси → аккаунт для скачивания),
обратная запись ротированных cookie, понятные причины отказа, ни одного секрета в логах и argv."""

from __future__ import annotations

import io
import logging
import os
import subprocess
from pathlib import Path

import pytest

import app.ingest as ingest
from app import download_account, ytdlp_net
from app.cookies import CookieRecord, parse_cookie_input
from app.proxy_config import proxy_env
from app.settings import settings
from test_render import _store

YT = "https://www.youtube.com/watch?v=nPSwiARbeRQ"
PROXY = "http://user:s3cret@proxy.example:3128"
COOKIE_YT = "SID=sid-old; HSID=b; SSID=c; APISID=d; SAPISID=e; __Secure-1PSIDTS=ts-old"

# Настоящие хвосты yt-dlp 2026.10.26 из контейнера (11.11.2026).
BOT_CHECK = ("ERROR: [youtube] nPSwiARbeRQ: Sign in to confirm you’re not a bot. This helps protect our community. "
             "Use --cookies-from-browser or --cookies for the authentication.")
ISP_CUT = ("[tls @ 0x5d34b50ab940] Error in the pull function.\n[tls @ 0x5d34b50ab940] IO error: End of file\n"
           "ERROR: ffmpeg exited with code 1")
ISP_STALL = ("[download] Got error: HTTPSConnectionPool(host='rr5---sn-5goeen7y.googlevideo.com', port=443): "
             "Read timed out. (read timeout=20.0). Retrying (1/3)...\nERROR: unable to download video data")
PROXY_DOWN = ("ERROR: [youtube] x: Unable to download API page: ('Unable to connect to proxy', "
              "NewConnectionError(\"HTTPSConnection(host='64.188.79.107', port=11090): Connection refused\"))")


def _done(args, code=0, out=""):
    return subprocess.CompletedProcess(args, code, out, "")


# ---------------------------------------------------------------- аргументы


def test_common_args_enable_node_and_pot_provider(tmp_path, monkeypatch):
    home = tmp_path / "bgutil" / "server"
    (home / "build").mkdir(parents=True)
    (home / "build" / "generate_once.js").write_text("// script")
    monkeypatch.setenv("BGUTIL_POT_HOME", str(home))
    monkeypatch.setattr(ytdlp_net.shutil, "which", lambda name: "/usr/local/bin/node" if name == "node" else None)
    args = ytdlp_net.common_args()
    assert args[:2] == ["--js-runtimes", "node"]
    assert args[2:] == ["--extractor-args", f"youtubepot-bgutilscript:server_home={home}"]


def test_common_args_without_node_or_build_are_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("BGUTIL_POT_HOME", str(tmp_path / "missing"))
    monkeypatch.setattr(ytdlp_net.shutil, "which", lambda name: None)
    assert ytdlp_net.common_args() == []


def test_every_ytdlp_launch_gets_common_args(monkeypatch):
    """Потоковый запуск источников, проба размера, музыка/звуки и числа публикаций — с JS-рантаймом."""
    monkeypatch.setattr(ytdlp_net, "common_args", lambda: ["--js-runtimes", "node"])
    monkeypatch.setattr(ingest, "common_args", lambda: ["--js-runtimes", "node"])
    seen: list[list[str]] = []

    class FakePopen:
        def __init__(self, command, **kwargs):
            seen.append(command)
            self.stdout = io.StringIO("ok\n")
            self.returncode = 0

        def wait(self):
            return 0

    monkeypatch.setattr(ingest.subprocess, "Popen", FakePopen)
    ingest._run_ytdlp(["--version"])
    assert seen[0][3:5] == ["--js-runtimes", "node"]

    monkeypatch.setattr(ytdlp_net.subprocess, "run", lambda args, **kw: seen.append(args) or _done(args))
    ytdlp_net.plain_runner(10)(["--version"], "")
    assert seen[1][:3] == ["yt-dlp", "--js-runtimes", "node"]

    from app.platform_stats import build_ytdlp_stats_args

    stats = build_ytdlp_stats_args(YT)
    assert stats[:3] == ["yt-dlp", "--js-runtimes", "node"] and stats[-1] == YT


def test_ffmpeg_gets_the_proxy_from_environment_not_argv():
    """Отрезки качает ffmpeg: он читает ``http_proxy`` (только http://) — тот же прокси, что у yt-dlp."""
    env = proxy_env(PROXY, base={"no_proxy": "*", "PATH": "/bin"})
    assert env["http_proxy"] == env["https_proxy"] == env["HTTPS_PROXY"] == PROXY
    assert "no_proxy" not in env  # иначе ffmpeg пошёл бы напрямую и получил 403 на чужом IP
    assert ytdlp_net.ffmpeg_can_use_proxy(PROXY)
    assert not ytdlp_net.ffmpeg_can_use_proxy("socks5://u:p@h:1080")
    assert not ytdlp_net.ffmpeg_can_use_proxy("https://u:p@h:443")


def test_youtube_source_download_uses_shared_routes_without_browser_headers(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "source_dir", tmp_path)
    monkeypatch.setattr(settings, "download_size_probe", False)
    monkeypatch.setattr(settings, "download_proxy_url", "")
    calls = []

    def fake_run(args, *, proxy=""):
        calls.append(args)
        Path(args[args.index("-o") + 1].replace("%(ext)s", "mp4")).write_bytes(b"video")
        return _done(args)

    monkeypatch.setattr(ingest, "_run_ytdlp", fake_run)
    monkeypatch.setattr(ingest, "download_proxy", lambda: "")
    stored = ingest.download_youtube_url(YT, quality="720")
    assert stored.original_filename == "youtube-nPSwiARbeRQ.mp4"
    # Чужой User-Agent ломает клиентов плеера YouTube (tv, visionos…): его нет.
    assert "--user-agent" not in calls[0] and "--continue" in calls[0] and "--newline" in calls[0]


# ---------------------------------------------------------------- маршруты


def _yt_account(store, label="yt-download", cookie=COOKIE_YT):
    return store.upsert_account("youtube", label, cookie)


def test_bot_check_retries_with_download_account_cookies_and_saves_rotation(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    account = _yt_account(store)
    download_account.set_account(store, account["id"])
    calls: list[tuple[list[str], str]] = []
    seen_cookie_file = {}

    def run(args, proxy):
        calls.append((args, proxy))
        if "--cookies" not in args:
            return _done(args, 1, ISP_CUT if not proxy else BOT_CHECK)
        path = Path(args[args.index("--cookies") + 1])
        text = path.read_text(encoding="utf-8")
        seen_cookie_file.update(path=path, text=text, mode=os.stat(path).st_mode & 0o777)
        # yt-dlp записал банк обратно: Google сменил __Secure-1PSIDTS и выдал новую cookie.
        path.write_text(text.replace("ts-old", "ts-NEW")
                        + ".youtube.com\tTRUE\t/\tTRUE\t0\tVISITOR_INFO1_LIVE\tvis\n"
                        + ".googlevideo.com\tTRUE\t/\tTRUE\t0\tJUNK\tx\n", encoding="utf-8")
        return _done(args, 0, "[download] 100%")

    result = ytdlp_net.run_routes(run, ["-o", "x.%(ext)s", YT], url=YT, proxy=PROXY, store=store)
    assert result.ok and [a.route for a in result.attempts] == ["direct", "proxy", "account"]
    assert [a.kind for a in result.attempts] == ["isp", "login", ""]
    # Порядок: напрямую → прокси (меньший битрейт) → тот же прокси, но с cookie аккаунта.
    assert calls[0] == (["-o", "x.%(ext)s", YT], "")
    assert calls[1][0][:2] == ["-S", "res,fps,+br"] and calls[1][1] == PROXY
    assert calls[2][0][:3] == ["-S", "res,fps,+br", "--cookies"] and calls[2][1] == PROXY
    assert "# Netscape HTTP Cookie File" in seen_cookie_file["text"] and "\tSID\tsid-old" in seen_cookie_file["text"]
    if os.name == "posix":
        assert seen_cookie_file["mode"] == 0o600
    assert not seen_cookie_file["path"].exists() and not seen_cookie_file["path"].parent.exists()
    saved = {c.name: c.value for c in store.get_account_cookies(account["id"])}
    assert saved["__Secure-1PSIDTS"] == "ts-NEW" and saved["VISITOR_INFO1_LIVE"] == "vis"
    assert saved["SID"] == "sid-old" and "JUNK" not in saved  # чужие домены в аккаунт не тащим


def test_rotation_is_saved_even_when_ytdlp_fails(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    account = _yt_account(store)
    download_account.set_account(store, account["id"])

    def run(args, proxy):
        if "--cookies" in args:
            path = Path(args[args.index("--cookies") + 1])
            path.write_text(path.read_text(encoding="utf-8").replace("ts-old", "ts-2"), encoding="utf-8")
        return _done(args, 1, BOT_CHECK)

    result = ytdlp_net.run_routes(run, [YT], url=YT, proxy="", store=store)
    assert not result.ok and [a.route for a in result.attempts] == ["direct", "account"]
    assert {c.name: c.value for c in store.get_account_cookies(account["id"])}["__Secure-1PSIDTS"] == "ts-2"
    assert "с аккаунтом для скачивания YouTube просит вход" in result.hint("YouTube")
    assert "обновите cookie аккаунта для скачивания" in result.hint("YouTube")


def test_no_download_account_means_no_cookies_even_with_publishing_accounts(tmp_path, monkeypatch):
    """Аккаунты публикаций для скачивания не берутся, пока владелец не отметит один из них."""
    store = _store(tmp_path, monkeypatch)
    _yt_account(store, "publishing-main")
    calls = []
    result = ytdlp_net.run_routes(lambda a, p: calls.append(a) or _done(a, 1, BOT_CHECK), [YT], url=YT,
                                  proxy=PROXY, store=store)
    assert len(calls) == 2 and not any("--cookies" in a for a in calls)
    assert download_account.get_account(store) is None
    assert "добавьте аккаунт для скачивания" in result.hint("YouTube")


def test_download_account_own_proxy_wins_and_other_sites_never_get_cookies(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    account = store.upsert_account("youtube", "yt-dl", COOKIE_YT, proxy_url="http://a:b@acct-proxy.example:8000")
    download_account.set_account(store, account["id"])
    routes = []
    ytdlp_net.run_routes(lambda a, p: routes.append((("--cookies" in a), p)) or _done(a, 1, BOT_CHECK), [YT],
                         url=YT, proxy=PROXY, store=store)
    assert routes[-1] == (True, "http://a:b@acct-proxy.example:8000")

    routes.clear()
    twitch = "https://www.twitch.tv/videos/1"
    ytdlp_net.run_routes(lambda a, p: routes.append((("--cookies" in a), p)) or _done(a, 1, BOT_CHECK), [twitch],
                         url=twitch, proxy=PROXY, store=store)
    assert routes == [(False, ""), (False, PROXY)]


def test_section_download_skips_proxy_ffmpeg_cannot_use(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    calls = []
    result = ytdlp_net.run_routes(lambda a, p: calls.append(p) or _done(a, 1, ISP_CUT), [YT], url=YT,
                                  proxy="socks5://u:p@h:1080", store=store, needs_ffmpeg=True)
    assert calls == [""]  # через socks ffmpeg пошёл бы напрямую — ссылки чужого IP дали бы 403
    assert "только http-прокси" in result.hint("YouTube")


def test_before_retry_cleans_between_attempts(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    cleaned = []
    ytdlp_net.run_routes(lambda a, p: _done(a, 1, ISP_CUT), [YT], url=YT, proxy=PROXY, store=store,
                         before_retry=lambda: cleaned.append(1))
    assert cleaned == [1]


# ---------------------------------------------------------------- причины


@pytest.mark.parametrize("text,kind", [
    (BOT_CHECK, "login"),
    ("ERROR: [youtube] x: Sign in to confirm your age. This video may be inappropriate", "login"),
    (ISP_CUT, "isp"),
    (ISP_STALL, "isp"),
    (PROXY_DOWN, "proxy"),
    ("[https @ 0x1] HTTP error 403 Forbidden\nERROR: ffmpeg exited with code 1", "forbidden"),
    ("ERROR: unable to download video data: HTTP Error 403: Forbidden", "forbidden"),
    ("ERROR: [youtube] x: Video unavailable. This video is private", "other"),
])
def test_classify_failure(text, kind):
    assert ytdlp_net.classify_failure(text) == kind


def test_failure_hints_tell_what_to_do():
    A = ytdlp_net.Attempt
    hint = ytdlp_net.failure_hint
    only_direct = hint([A("direct", 1, "isp")], account_configured=False, site="YouTube")
    assert only_direct.startswith("напрямую провайдер режет YouTube") and "задайте прокси" in only_direct
    flagged = hint([A("direct", 1, "isp"), A("proxy", 1, "login")], account_configured=False, site="YouTube")
    assert "провайдер режет YouTube" in flagged and "через прокси YouTube просит вход" in flagged
    assert flagged.endswith("(или смените прокси)") and "добавьте аккаунт для скачивания" in flagged
    # Видеосервер 403 через прокси (так было 11.11: проход проверки «не бот», но googlevideo отказывает адресу).
    forbidden = hint([A("direct", 1, "isp"), A("proxy", 1, "forbidden")], account_configured=False, site="YouTube")
    assert "через прокси видеосервер отвечает 403" in forbidden and "добавьте аккаунт" in forbidden
    assert "прокси для скачивания не отвечает" in hint([A("direct", 1, "isp"), A("proxy", 1, "proxy")],
                                                        account_configured=False)
    # Не YouTube — аккаунт не предлагаем.
    assert "аккаунт" not in hint([A("direct", 1, "forbidden"), A("proxy", 1, "forbidden")],
                                 account_configured=False, site="Twitch")
    assert hint([A("direct", 1, "other"), A("proxy", 1, "other")], account_configured=False) == ""


def test_forbidden_on_proxy_also_tries_download_account(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    account = _yt_account(store)
    download_account.set_account(store, account["id"])
    forbidden = ("[https @ 0x5af3cc02fb40] HTTP error 403 Forbidden\nhttps://rr1---sn-4g5ednkz.googlevideo.com/x "
                 "Server returned 403 Forbidden (access denied)\nERROR: ffmpeg exited with code 1")
    calls = []
    result = ytdlp_net.run_routes(lambda a, p: calls.append(a) or _done(a, 1, ISP_CUT if not p else forbidden),
                                  [YT], url=YT, proxy=PROXY, store=store)
    assert [a.kind for a in result.attempts] == ["isp", "forbidden", "forbidden"] and "--cookies" in calls[-1]


def test_source_error_leads_with_the_reason_and_hides_proxy_password(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "source_dir", tmp_path)
    monkeypatch.setattr(settings, "download_size_probe", False)
    monkeypatch.setattr(ingest, "download_proxy", lambda: PROXY)
    monkeypatch.setattr(download_account, "get_account", lambda store=None: None)

    def fake_run(args, *, proxy=""):
        if proxy:
            return _done(args, 1, f"ERROR: Unable to connect to proxy {PROXY}\n{BOT_CHECK}")
        return _done(args, 1, ISP_CUT)

    monkeypatch.setattr(ingest, "_run_ytdlp", fake_run)
    with pytest.raises(ValueError) as err:
        ingest.download_youtube_url(YT)
    text = str(err.value)
    assert text.startswith("YouTube download failed: напрямую провайдер режет YouTube")
    assert "через прокси YouTube просит вход" in text
    assert "s3cret" not in text and "user:" not in text


def test_section_import_error_names_the_isp(tmp_path, monkeypatch):
    import app.proxy_config as proxy_config
    from app.video_import import import_video_from_url

    store = _store(tmp_path, monkeypatch)
    monkeypatch.setattr(proxy_config, "download_proxy", lambda: "")
    monkeypatch.setattr(download_account, "get_account", lambda store=None: None)
    monkeypatch.setattr(ingest, "_run_ytdlp", lambda args, proxy="": _done(args, 1, ISP_CUT))
    with pytest.raises(ValueError, match="провайдер режет YouTube"):
        import_video_from_url(store, YT, 336, 412)


# ---------------------------------------------------------------- cookie и секреты


def test_netscape_roundtrip_and_rotation_filter():
    cookies = parse_cookie_input(COOKIE_YT, "youtube") + [
        CookieRecord("LOGIN_INFO", "li", ".youtube.com", http_only=True, expires=1900000000)]
    text = ytdlp_net.netscape_text(cookies)
    back = {c.name: c for c in parse_cookie_input(text, "youtube")}
    assert back["LOGIN_INFO"].http_only and back["LOGIN_INFO"].expires == 1900000000 and back["SID"].value == "sid-old"
    assert ytdlp_net.rotated_cookie_values(cookies, text) == {}
    changed = text.replace("sid-old", "sid-new") + ".google.com\tTRUE\t/\tTRUE\t0\tNID\tn1\n"
    assert ytdlp_net.rotated_cookie_values(cookies, changed) == {"SID": "sid-new", "NID": "n1"}


def test_cookie_write_back_logs_names_never_values(tmp_path, monkeypatch, caplog):
    store = _store(tmp_path, monkeypatch)
    account = _yt_account(store)
    with caplog.at_level(logging.INFO, logger="app.ytdlp_net"):
        with ytdlp_net.account_cookie_file(store, account["id"]) as path:
            path.write_text(path.read_text(encoding="utf-8").replace("ts-old", "TOPSECRET-TS"), encoding="utf-8")
    assert "__Secure-1PSIDTS" in caplog.text
    assert "TOPSECRET" not in caplog.text and "sid-old" not in caplog.text


def test_proxy_password_never_in_argv_of_any_route(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    account = _yt_account(store)
    download_account.set_account(store, account["id"])
    argv = []
    ytdlp_net.run_routes(lambda a, p: argv.append(a) or _done(a, 1, BOT_CHECK), [YT], url=YT, proxy=PROXY,
                         store=store)
    assert len(argv) == 3
    assert not any("s3cret" in arg or arg == "--proxy" for args in argv for arg in args)


# ---------------------------------------------------------------- настройка


def test_download_account_setting_validates_and_defaults_to_none(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    assert download_account.status(store)["account_id"] is None
    tiktok = store.upsert_account("tiktok", "tt", "sessionid=a; sid_tt=b")
    with pytest.raises(ValueError, match="YouTube"):
        download_account.set_account(store, tiktok["id"])
    with pytest.raises(ValueError, match="не найден"):
        download_account.set_account(store, 999)
    yt = _yt_account(store)
    status = download_account.set_account(store, yt["id"])
    assert status["account_id"] == yt["id"] and status["label"] == "yt-download"
    assert [a["id"] for a in status["accounts"]] == [yt["id"]]
    store.delete_account(yt["id"])
    assert download_account.get_account(store) is None  # удалён — как будто не задан
    assert download_account.set_account(store, None)["account_id"] is None


def test_download_account_api(tmp_path, monkeypatch):
    from test_api_pipeline import _client

    client, store = _client(tmp_path, monkeypatch)
    yt = _yt_account(store)
    assert client.get("/api/settings/download-account").json()["account_id"] is None
    body = client.put("/api/settings/download-account", json={"account_id": yt["id"]}).json()
    assert body["account_id"] == yt["id"] and "sid-old" not in str(body)
    assert client.put("/api/settings/download-account", json={"account_id": 12345}).status_code == 400
    assert client.put("/api/settings/download-account", json={"account_id": None}).json()["account_id"] is None
