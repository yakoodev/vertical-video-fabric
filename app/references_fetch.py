"""Референс по ссылке: скачать ролик, картинку или звук — или запомнить страницу (app/references.py).

* Ролик (TikTok, YouTube, Instagram, VK, Twitch-клип, X, Reddit и всё, что умеет yt-dlp) — тот же yt-dlp и те же
  маршруты, что у источников и видеомемов (``app/ingest._ytdlp_route_runner`` → ``app/ytdlp_net.run_routes``):
  напрямую → через прокси сервиса (настройки «Прокси», запасной ``DOWNLOAD_PROXY_URL``) → YouTube просит вход — с
  аккаунтом для скачивания. Целиком, не выше 1080p, не длиннее ``MAX_VIDEO_SEC`` (референс — пример, а не фильм).
* Прямая ссылка на файл (``.jpg``, ``.mp3``, ``.mp4``… или ответ сервера ``image/*``, ``audio/*``, ``video/*``) —
  обычным HTTP, не вышло напрямую — через тот же прокси.
* Страница, где yt-dlp ролика не нашёл, — референс-ссылка (kind ``link``): заголовок, описание и картинка страницы
  (``og:*``) — картинка кадром карточки.

Тесты подменяют ``YTDLP`` (url, папка → (файл, название)) и ``HTTP_GET`` (url → (content-type, байты/None, текст)).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from html import unescape
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urljoin, urlsplit

from app import references as R

MAX_VIDEO_SEC = 20 * 60
MAX_HEIGHT = 1080
MAX_FILE_BYTES = 1500 * 1024 * 1024
MAX_PAGE_BYTES = 2 * 1024 * 1024
TITLE_MARK = "VVF-TITLE:"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/126.0 Safari/537.36")
# Сайты роликов: сразу yt-dlp, без пробы HTTP (страница TikTok в ответ на GET — HTML без ролика).
VIDEO_HOSTS = ("tiktok.com", "youtube.com", "youtu.be", "instagram.com", "vk.com", "vkvideo.ru", "twitch.tv",
               "x.com", "twitter.com", "reddit.com", "redd.it", "facebook.com", "fb.watch", "rutube.ru",
               "vimeo.com", "dailymotion.com", "bilibili.com", "pinterest.", "soundcloud.com", "bandcamp.com",
               "coub.com", "kick.com", "likee.video", "ok.ru", "dzen.ru")

YTDLP: Callable[[str, Path], tuple[Path, str]] | None = None
HTTP_GET: Callable[[str], tuple[str, bytes | None, str]] | None = None


class FetchError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# Вид файла
# --------------------------------------------------------------------------- #


def ffprobe(path: Path) -> dict:
    proc = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
                           str(path)], capture_output=True, text=True, timeout=120, check=False)
    if proc.returncode != 0:
        return {}
    try:
        return json.loads(proc.stdout or "{}")
    except ValueError:
        return {}


def probe_kind(path: Path, name: str = "") -> str | None:
    """video | image | audio по расширению, иначе по ffprobe; None — не медиа."""
    by_name = R.kind_by_name(name or path.name)
    if by_name:
        return by_name
    data = ffprobe(Path(path))
    streams = data.get("streams") or []
    videos = [s for s in streams if s.get("codec_type") == "video"
              and not (s.get("disposition") or {}).get("attached_pic")]
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    fmt = str((data.get("format") or {}).get("format_name") or "")
    if videos:
        codec = str(videos[0].get("codec_name") or "")
        still = codec in ("png", "mjpeg", "webp", "bmp", "tiff", "gif") and not audios
        if still or "image2" in fmt or fmt.endswith("_pipe"):
            return "image"
        return "video"
    if audios:
        return "audio"
    return None


# --------------------------------------------------------------------------- #
# Скачивание
# --------------------------------------------------------------------------- #


def is_video_host(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) or (h.endswith(".") and h in host) for h in VIDEO_HOSTS)


def ytdlp_args(url: str, template: Path) -> list[str]:
    return [
        "--no-playlist",
        "-f", f"bv*[height<={MAX_HEIGHT}]+ba/b[height<={MAX_HEIGHT}]/b",
        "--merge-output-format", "mp4",
        # Длину знает не каждый сайт: неизвестная пропускается («<=?»), длинное — отказ без скачивания.
        "--match-filter", f"duration <=? {MAX_VIDEO_SEC}",
        "--max-filesize", str(MAX_FILE_BYTES),
        "--socket-timeout", "20",
        "--retries", "5",
        "--newline",
        "--no-quiet",
        "--print", f"after_move:{TITLE_MARK}%(title)s",
        "--no-simulate",
        "-o", str(template),
        url,
    ]


def _ytdlp(url: str, folder: Path) -> tuple[Path, str]:
    """yt-dlp сервиса по маршрутам источников: (файл, название ролика). FetchError — не скачалось."""
    if YTDLP is not None:
        return YTDLP(url, folder)
    from app.ingest import _ytdlp_route_runner
    from app.proxy_config import download_proxy
    from app.ytdlp_net import is_youtube_url, run_routes

    work = folder / "dl"
    work.mkdir(parents=True, exist_ok=True)

    def clean() -> None:
        for leftover in work.glob("*"):
            leftover.unlink(missing_ok=True)

    routes = run_routes(_ytdlp_route_runner, ytdlp_args(url, work / "dl.%(ext)s"), url=url, proxy=download_proxy(),
                        before_retry=clean)
    proc = routes.proc
    output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    files = sorted(f for f in work.glob("dl.*") if not f.name.endswith((".part", ".ytdl", ".temp")))
    if proc.returncode != 0 or not files:
        shutil.rmtree(work, ignore_errors=True)
        if "does not pass filter" in output:
            raise FetchError(f"ролик длиннее {MAX_VIDEO_SEC // 60} мин — для референса загрузи нужный кусок файлом")
        tail = [line for line in output.splitlines() if "ERROR" in line] or \
               [line for line in output.splitlines() if line.strip()][-2:]
        hint = routes.hint("YouTube" if is_youtube_url(url) else "")
        raise FetchError("не скачалось: " + (f"{hint} — " if hint else "") + (" ".join(tail)[:300] or "пусто"))
    title = next((line.split(TITLE_MARK, 1)[1].strip() for line in output.splitlines() if TITLE_MARK in line), "")
    return files[0], title


def _http_get(url: str, limit: int, proxy: str = "") -> tuple[str, bytes | None, str]:
    """GET: (content-type, тело файла или None для страницы, текст страницы). Тело больше ``limit`` — отказ."""
    import httpx

    kwargs: dict[str, Any] = {"timeout": 30, "follow_redirects": True, "headers": {"User-Agent": BROWSER_UA}}
    if proxy:
        kwargs["proxy"] = proxy
    with httpx.Client(**kwargs) as client, client.stream("GET", url) as resp:
        resp.raise_for_status()
        ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
        is_page = ctype.startswith("text/") or "html" in ctype or "xml" in ctype or not ctype
        cap = MAX_PAGE_BYTES if is_page else limit
        chunks: list[bytes] = []
        size = 0
        for chunk in resp.iter_bytes():
            size += len(chunk)
            if size > cap:
                if is_page:
                    break
                raise FetchError(f"файл больше {limit // (1024 * 1024)} МБ")
            chunks.append(chunk)
        body = b"".join(chunks)
    if is_page:
        return ctype or "text/html", None, body.decode("utf-8", errors="replace")
    return ctype, body, ""


def http_get(url: str, limit: int = MAX_FILE_BYTES) -> tuple[str, bytes | None, str]:
    """Напрямую, не вышло — через прокси сервиса."""
    if HTTP_GET is not None:
        return HTTP_GET(url)
    from app.proxy_config import download_proxy

    try:
        return _http_get(url, limit)
    except FetchError:
        raise
    except Exception as first:  # noqa: BLE001 - сеть: второй маршрут
        proxy = download_proxy()
        if not proxy:
            raise FetchError(f"не открылось: {str(first)[:200]}") from first
        try:
            return _http_get(url, limit, proxy)
        except FetchError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise FetchError(f"не открылось ни напрямую, ни через прокси: {str(exc)[:200]}") from exc


_CTYPE_KIND = (("image/", "image"), ("audio/", "audio"), ("video/", "video"))
_CTYPE_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif",
              "audio/mpeg": ".mp3", "audio/mp4": ".m4a", "audio/wav": ".wav", "audio/x-wav": ".wav",
              "audio/ogg": ".ogg", "video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov"}


def _meta(html: str, *names: str) -> str:
    for name in names:
        for pattern in (rf'<meta[^>]+(?:property|name)=["\']{re.escape(name)}["\'][^>]*content=["\']([^"\']*)["\']',
                        rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]*(?:property|name)=["\']{re.escape(name)}["\']'):
            m = re.search(pattern, html, re.I)
            if m and m.group(1).strip():
                return unescape(m.group(1).strip())
    return ""


def page_info(url: str, html: str) -> dict:
    """Заголовок, описание, картинка и сайт страницы (og:*, <title>)."""
    title = _meta(html, "og:title", "twitter:title")
    if not title:
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        title = unescape(re.sub(r"\s+", " ", m.group(1))).strip() if m else ""
    image = _meta(html, "og:image", "og:image:url", "twitter:image")
    return {"url": url, "site": (urlsplit(url).hostname or "").removeprefix("www."), "title": title[:200],
            "description": _meta(html, "og:description", "description", "twitter:description")[:600],
            "image": urljoin(url, image) if image else ""}


def _page_poster(image_url: str, folder: Path) -> bool:
    """Картинка страницы → poster.jpg (≤640 по длинной стороне). Не вышло — без кадра."""
    if not image_url.startswith(("http://", "https://")):
        return False
    try:
        ctype, body, _ = http_get(image_url, 15 * 1024 * 1024)
    except Exception:  # noqa: BLE001 - кадр — удобство
        return False
    if not body:
        return False
    raw = folder / ("page-image" + _CTYPE_EXT.get(ctype, ".img"))
    raw.write_bytes(body)
    try:
        from app.references_analyze import still_jpeg

        return still_jpeg(raw, folder / "poster.jpg", 640)
    finally:
        raw.unlink(missing_ok=True)


def _as_link(url: str, html: str, folder: Path, title: str) -> dict:
    info = page_info(url, html) if html else {"url": url, "site": (urlsplit(url).hostname or ""), "title": "",
                                               "description": "", "image": ""}
    if info.get("image"):
        _page_poster(info["image"], folder)
    fields = {"kind": "link"}
    if not info.get("title"):  # страница не открылась (403, сеть) — хотя бы последний кусок адреса
        tail = unquote(urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1])
        info["title"] = re.sub(r"[_+-]+", " ", Path(tail).stem).strip()[:120] if tail else ""
    if not title and info.get("title"):
        fields["title"] = info["title"][:160]
    summary = f"страница {info['site']}" + (f": «{info['title']}»" if info.get("title") else "")
    return {"fields": fields, "analysis": {"kind": "link", "page": info, "summary": summary}}


def _store_file(src: Path, folder: Path, kind: str, ref: dict, title: str) -> dict:
    name = "media" + R._safe_ext(src.name, kind)
    dest = folder / name
    if src.resolve() != dest.resolve():
        shutil.move(str(src), str(dest))
    shutil.rmtree(folder / "dl", ignore_errors=True)
    fields: dict[str, Any] = {"file_name": name, "size_bytes": dest.stat().st_size, "kind": kind}
    if not ref.get("title") and title:
        fields["title"] = title[:160]
    return {"fields": fields}


def fetch(ref: dict, folder: Path) -> dict:
    """Скачать референс в ``folder``: ``{"fields": поля строки, "analysis"?: разбор страницы}``.
    FetchError — не скачалось (текст для карточки)."""
    url = str(ref.get("url") or "").strip()
    title = str(ref.get("title") or "")
    folder.mkdir(parents=True, exist_ok=True)
    if not ref.get("download"):
        html = ""
        try:
            ctype, body, html = http_get(url, MAX_PAGE_BYTES)
        except Exception:  # noqa: BLE001 - ссылка остаётся ссылкой и без заголовка
            pass
        return _as_link(url, html, folder, title)
    direct = R.kind_by_name(urlsplit(url).path)
    if is_video_host(url) and direct is None:
        file, video_title = _ytdlp(url, folder)
        kind = probe_kind(file) or "video"
        return _store_file(file, folder, kind, ref, video_title)
    ctype, body, html = http_get(url)
    if body is not None:
        kind = next((k for prefix, k in _CTYPE_KIND if ctype.startswith(prefix)), None) or direct
        ext = _CTYPE_EXT.get(ctype) or (Path(urlsplit(url).path).suffix.lower() or ".bin")
        raw = folder / ("download" + ext)
        raw.write_bytes(body)
        kind = kind or probe_kind(raw)
        if kind is None:
            raw.unlink(missing_ok=True)
            raise FetchError(f"по ссылке не медиа ({ctype or 'без типа'})")
        name = Path(urlsplit(url).path).stem or "download"
        return _store_file(raw, folder, kind, ref, title or name)
    # Страница: может, на ней ролик (generic-разбор yt-dlp), иначе — ссылка.
    try:
        file, video_title = _ytdlp(url, folder)
    except FetchError:
        return _as_link(url, html, folder, title)
    kind = probe_kind(file) or "video"
    return _store_file(file, folder, kind, ref, video_title or page_info(url, html).get("title", ""))
