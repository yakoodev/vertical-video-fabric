"""Торрент без сети: разбор magnet-ссылки и файла .torrent (bencode), выбор файлов по умолчанию.

Файл .torrent разбираем сами (десяток строк bencode), а не через aria2: так ещё до запуска aria2c видно имя,
info hash и размер, и битый файл получает понятный отказ. Список файлов для выбора всё равно берётся у aria2
(``aria2.getFiles``) — у него те же индексы, что принимает ``select-file``.
"""

from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from urllib.parse import parse_qs, quote, urlsplit

# Видео, которое потянет ffprobe/ffmpeg конвейера: остальное (сэмплы .txt, обложки, субтитры .srt) не предлагаем.
VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".ts", ".m2ts", ".mts", ".flv", ".wmv", ".mpg", ".mpeg", ".vob",
}
MAX_TORRENT_FILE_BYTES = 10 * 1024 * 1024  # .torrent крупнее — почти наверняка не торрент
_HEX40 = re.compile(r"^[0-9a-fA-F]{40}$")
_B32 = re.compile(r"^[A-Za-z2-7]{32}$")


class TorrentError(ValueError):
    """Ссылка или файл не торрент: текст для человека."""


@dataclass(frozen=True)
class TorrentFile:
    index: int  # 1-based, как у aria2 select-file
    path: str  # путь внутри торрента, через /
    size: int

    @property
    def is_video(self) -> bool:
        return PurePosixPath(self.path).suffix.lower() in VIDEO_EXTENSIONS


@dataclass(frozen=True)
class TorrentInfo:
    info_hash: str  # v1, 40 hex, нижний регистр
    name: str
    files: list[TorrentFile] = field(default_factory=list)

    @property
    def total_size(self) -> int:
        return sum(f.size for f in self.files)


# --------------------------------------------------------------------------- magnet


def parse_magnet(uri: str) -> tuple[str, str]:
    """(info hash в hex, имя из dn) magnet-ссылки; TorrentError, если это не magnet BitTorrent."""
    text = (uri or "").strip()
    parts = urlsplit(text)
    if parts.scheme.lower() != "magnet":
        raise TorrentError("нужна magnet-ссылка (magnet:?xt=urn:btih:…) или файл .torrent")
    query = parse_qs(parts.query)
    info_hash = ""
    for xt in query.get("xt", []):
        if xt.lower().startswith("urn:btih:"):
            info_hash = _normalize_btih(xt[len("urn:btih:"):])
            if info_hash:
                break
    if not info_hash:
        raise TorrentError("в magnet-ссылке нет хеша BitTorrent (xt=urn:btih:…)")
    name = (query.get("dn") or [""])[0].strip()
    return info_hash, name


def _normalize_btih(value: str) -> str:
    value = value.strip()
    if _HEX40.match(value):
        return value.lower()
    if _B32.match(value):
        return base64.b32decode(value.upper()).hex()
    return ""


def magnet_for(info_hash: str, name: str = "") -> str:
    """Короткая magnet-ссылка для справки в источнике (у загрузки файлом своей ссылки нет)."""
    link = f"magnet:?xt=urn:btih:{info_hash.lower()}"
    return f"{link}&dn={quote(name)}" if name else link


# --------------------------------------------------------------------------- .torrent


def parse_torrent(data: bytes) -> TorrentInfo:
    """Разобрать файл .torrent: имя, info hash (v1) и файлы с размерами."""
    if not data:
        raise TorrentError("пустой файл .torrent")
    if len(data) > MAX_TORRENT_FILE_BYTES:
        raise TorrentError("файл .torrent больше 10 МБ — это не торрент")
    try:
        meta, end = _decode(data, 0)
    except (ValueError, IndexError, RecursionError) as exc:
        raise TorrentError("файл не похож на .torrent (bencode не читается)") from exc
    if end != len(data) or not isinstance(meta, dict) or not isinstance(meta.get(b"info"), dict):
        raise TorrentError("файл не похож на .torrent (нет раздела info)")
    info = meta[b"info"]
    start, stop = _info_span(data)
    info_hash = hashlib.sha1(data[start:stop]).hexdigest()
    name = _text(info.get(b"name.utf-8") or info.get(b"name") or b"") or info_hash
    files: list[TorrentFile] = []
    if isinstance(info.get(b"files"), list):
        for index, item in enumerate(info[b"files"], start=1):
            if not isinstance(item, dict):
                raise TorrentError("битый список файлов в .torrent")
            parts = item.get(b"path.utf-8") or item.get(b"path") or []
            path = "/".join(_text(p) for p in parts if isinstance(p, bytes))
            files.append(TorrentFile(index, f"{name}/{path}", _size(item.get(b"length"))))
    elif b"length" in info:
        files.append(TorrentFile(1, name, _size(info.get(b"length"))))
    else:
        # Только BitTorrent v2 (file tree без v1-части): aria2 такие не качает.
        raise TorrentError("торрент только формата v2 — aria2 его не скачает")
    return TorrentInfo(info_hash=info_hash, name=name, files=files)


def _text(value: bytes) -> str:
    return value.decode("utf-8", errors="replace").strip()


def _size(value) -> int:
    if not isinstance(value, int) or value < 0:
        raise TorrentError("битый размер файла в .torrent")
    return value


def _decode(data: bytes, pos: int, depth: int = 0):
    if depth > 64:
        raise ValueError("too deep")
    lead = data[pos:pos + 1]
    if lead == b"i":
        end = data.index(b"e", pos)
        return int(data[pos + 1:end]), end + 1
    if lead == b"l":
        pos += 1
        items = []
        while data[pos:pos + 1] != b"e":
            item, pos = _decode(data, pos, depth + 1)
            items.append(item)
        return items, pos + 1
    if lead == b"d":
        pos += 1
        out = {}
        while data[pos:pos + 1] != b"e":
            key, pos = _decode(data, pos, depth + 1)
            if not isinstance(key, bytes):
                raise ValueError("dict key must be a string")
            out[key], pos = _decode(data, pos, depth + 1)
        return out, pos + 1
    if lead.isdigit():
        colon = data.index(b":", pos)
        length = int(data[pos:colon])
        start = colon + 1
        if length < 0 or start + length > len(data):
            raise ValueError("string out of range")
        return data[start:start + length], start + length
    raise ValueError(f"unexpected byte at {pos}")


def _info_span(data: bytes) -> tuple[int, int]:
    """Где в файле лежит bencode словаря info: info hash — sha1 именно этих байт."""
    pos = 1  # после 'd' верхнего словаря
    while data[pos:pos + 1] != b"e":
        key, pos = _decode(data, pos)
        start = pos
        _value, pos = _decode(data, pos)
        if key == b"info":
            return start, pos
    raise TorrentError("нет раздела info")


# --------------------------------------------------------------------------- выбор


def default_selection(files: list[TorrentFile]) -> list[int]:
    """Что отметить сразу: самый большой видеофайл (серия, фильм), иначе ничего."""
    videos = [f for f in files if f.is_video and f.size > 0]
    if not videos:
        return []
    return [max(videos, key=lambda f: f.size).index]
