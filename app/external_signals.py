"""Бесплатные внешние сигналы моментов — те же 10-секундные корзины секунд исходника, что у чата.

Разбор каналов нарезок (findings.md п. 54, 55, 47, 52):

* **клипы зрителей Twitch** с этого VOD — публичный GQL сайта (без входа, Client-ID веб-клиента, как в пилоте
  GTA 6): ``video(id).clips(first:100)`` (первые 100 по времени) плюс топ клипов канала по просмотрам за неделю и
  за всё время, отфильтрованный по VOD. Смещение в VOD + длина → корзины, вес ``log1p(view_count)``;
* **тепловая карта YouTube** («самое пересматриваемое») — ``heatmap`` из ``yt-dlp --dump-json``;
* **метки автосубтитров YouTube** ``[смех]`` / ``[laughter]`` / ``[screaming]`` (и ``[аплодисменты]``/
  ``[applause]`` слабее) — из ``ru-orig``/``en-orig`` VTT.

Сетевые вызовы — отдельными функциями с подменяемым транспортом (тесты без сети). Кривая каждого сигнала
нормирована на 0..1; ``moment_signals`` хранит их в кеше и смешивает мягким весом в ``chat_signal.rerank``.
"""

from __future__ import annotations

import json
import math
import re
import subprocess
import time
import urllib.request
from typing import Any, Callable, Iterable

BUCKET = 10.0
TWITCH_GQL = "https://gql.twitch.tv/gql"
TWITCH_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"  # открытый Client-ID веб-клиента twitch.tv (его же берёт yt-dlp)
_CLIP_FIELDS = "slug viewCount durationSeconds videoOffsetSeconds video{id}"
TAG_WEIGHTS = {"смех": 1.0, "laughter": 1.0, "laughs": 1.0, "screaming": 1.0, "крики": 1.0, "крик": 1.0,
               "аплодисменты": 0.5, "applause": 0.5}
_TAG_RE = re.compile(r"\[([^\]\n]{2,30})\]")
_VTT_TIME = re.compile(r"(\d+):(\d\d):(\d\d)[.,](\d{3})\s+-->\s+(\d+):(\d\d):(\d\d)[.,](\d{3})")


def _bins(n: int) -> list[float]:
    return [0.0] * max(1, n)


def _normalize(raw: list[float]) -> list[float]:
    peak = max(raw, default=0.0)
    return [round(v / peak, 4) if peak > 0 else 0.0 for v in raw]


# --------------------------------------------------------------------------- #
# Twitch: клипы зрителей с VOD
# --------------------------------------------------------------------------- #


def twitch_vod_id(url: str) -> str | None:
    m = re.search(r"twitch\.tv/(?:[^/]+/)?(?:videos?|v)/(\d+)", url or "")
    return m.group(1) if m else None


def _gql_post(query: str, variables: dict, tries: int = 3) -> dict:
    body = json.dumps({"query": query, "variables": variables}).encode()
    last: Exception | None = None
    for i in range(tries):
        try:
            req = urllib.request.Request(TWITCH_GQL, data=body, headers={
                "Client-ID": TWITCH_CLIENT_ID, "Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - фиксированный https-адрес
                return json.loads(resp.read())
        except Exception as exc:  # noqa: BLE001 - сеть: повторим и отдадим последнюю ошибку
            last = exc
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"Twitch GQL не ответил: {last}")


def fetch_vod_clips(vod_id: str, login: str | None = None, gql: Callable[[str, dict], dict] = _gql_post) -> list[dict]:
    """Клипы зрителей с VOD: [{slug, views, offset, duration}] без повторов, только с известным смещением."""
    found: dict[str, dict] = {}

    def add(edges: Iterable[dict]) -> None:
        for edge in edges or []:
            node = (edge or {}).get("node") or {}
            vid = ((node.get("video") or {}).get("id"))
            off = node.get("videoOffsetSeconds")
            if off is None or (vid is not None and str(vid) != str(vod_id)) or not node.get("slug"):
                continue
            found.setdefault(node["slug"], {"slug": node["slug"], "views": int(node.get("viewCount") or 0),
                                            "offset": float(off), "duration": float(node.get("durationSeconds") or 30)})

    data = gql("query($id:ID!){ video(id:$id){ owner{login} clips(first:100){ edges{ node{ %s } } } } }" % _CLIP_FIELDS,
               {"id": str(vod_id)})
    video = (data.get("data") or {}).get("video") or {}
    add(((video.get("clips") or {}).get("edges")))
    login = login or ((video.get("owner") or {}).get("login"))
    if login:
        for period in ("LAST_WEEK", "ALL_TIME"):
            d = gql("query($u:String!,$p:ClipsPeriod){ user(login:$u){ clips(first:100, criteria:{period:$p, "
                    "sort:VIEWS_DESC}){ edges{ node{ %s } } } } }" % _CLIP_FIELDS, {"u": login, "p": period})
            add((((d.get("data") or {}).get("user") or {}).get("clips") or {}).get("edges"))
    return sorted(found.values(), key=lambda c: c["offset"])


def clips_signal(clips: list[dict], duration: float | None = None, bucket: float = BUCKET) -> dict | None:
    """Корзины по клипам зрителей: клип покрывает [offset, offset+duration], вес log1p(просмотров)."""
    clips = [c for c in clips or [] if c.get("offset") is not None]
    if not clips:
        return None
    end = max(float(duration or 0), max(float(c["offset"]) + float(c.get("duration") or 30) for c in clips))
    raw = _bins(int(end // bucket) + 1)
    for c in clips:
        a, b = float(c["offset"]), float(c["offset"]) + float(c.get("duration") or 30)
        w = math.log1p(max(0, int(c.get("views") or 0))) + 0.5
        for i in range(int(a // bucket), min(len(raw) - 1, int(b // bucket)) + 1):
            raw[i] += w
    return {"bucket": bucket, "curve": _normalize(raw), "clips": len(clips)}


# --------------------------------------------------------------------------- #
# YouTube: тепловая карта и метки автосубтитров
# --------------------------------------------------------------------------- #


def is_youtube(url: str) -> bool:
    return bool(re.search(r"(?:^|//|\.)(?:youtube\.com|youtu\.be)/", url or ""))


def ytdlp_info(url: str, runner: Callable[[list[str]], str] | None = None) -> dict:
    """Метаданные ролика (``yt-dlp --dump-json``), без скачивания видео."""
    from app.ytdlp_net import common_args

    args = ["yt-dlp", *common_args(), "--dump-json", "--skip-download", "--no-playlist", "--no-warnings", url]
    if runner is not None:
        return json.loads(runner(args))
    proc = subprocess.run(args, capture_output=True, text=True, timeout=120, check=False, encoding="utf-8")
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError(f"yt-dlp: {(proc.stderr or '').strip()[-300:]}")
    return json.loads(proc.stdout.splitlines()[0])


def heatmap_signal(heatmap: list[dict] | None, duration: float | None = None, bucket: float = BUCKET,
                   floor: float | None = None) -> dict | None:
    """Тепловая карта YouTube → корзины: в корзине — максимум «пересмотров» отрезков, задевших её.

    ``floor`` вычитается (минимум карты: ролик смотрят весь, сигнал — то, что выше обычного)."""
    rows = [h for h in heatmap or [] if isinstance(h, dict) and h.get("end_time") is not None]
    if not rows:
        return None
    base = min(float(h.get("value") or 0) for h in rows) if floor is None else floor
    end = max(float(duration or 0), max(float(h["end_time"]) for h in rows))
    raw = _bins(int(end // bucket) + 1)
    for h in rows:
        a, b, v = float(h["start_time"]), float(h["end_time"]), max(0.0, float(h.get("value") or 0) - base)
        for i in range(int(a // bucket), min(len(raw) - 1, int(max(a, b - 1e-6) // bucket)) + 1):
            raw[i] = max(raw[i], v)
    return {"bucket": bucket, "curve": _normalize(raw)}


def parse_vtt_tags(text: str) -> list[tuple[float, str]]:
    """(секунда, метка) по VTT: ``[смех]``, ``[laughter]``… Повторы одной реплики в «наплывах» автосубтитров
    YouTube (одна строка в двух соседних блоках) считаются один раз."""
    out: list[tuple[float, str]] = []
    seen: set[tuple[int, str]] = set()
    t0 = None
    for line in (text or "").splitlines():
        m = _VTT_TIME.search(line)
        if m:
            g = [int(x) for x in m.groups()]
            t0 = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
            continue
        if t0 is None:
            continue
        for tag in _TAG_RE.findall(re.sub(r"<[^>]+>", "", line)):
            key = tag.strip().lower()
            if key in TAG_WEIGHTS:
                mark = (int(t0 // 2), key)
                if mark not in seen:
                    seen.add(mark)
                    out.append((round(t0, 2), key))
    return out


def tags_signal(tags: list[tuple[float, str]], duration: float | None = None, bucket: float = BUCKET) -> dict | None:
    if not tags:
        return None
    end = max(float(duration or 0), max(t for t, _ in tags))
    raw = _bins(int(end // bucket) + 1)
    for t, tag in tags:
        raw[int(t // bucket)] += TAG_WEIGHTS.get(tag, 0.0)
    return {"bucket": bucket, "curve": _normalize(raw), "tags": len(tags)}


def fetch_auto_subs(url: str, folder: Any, runner: Callable[[list[str]], Any] | None = None) -> str:
    """Скачать только автосубтитры (``ru-orig``/``en-orig``, запасные ``ru``/``en``) в ``folder``; текст VTT или ''."""
    from pathlib import Path

    from app.ytdlp_net import common_args

    folder = Path(folder)
    args = ["yt-dlp", *common_args(), "--skip-download", "--no-playlist", "--no-warnings", "--write-auto-subs",
            "--sub-langs", "ru-orig,en-orig,ru,en", "--sub-format", "vtt", "-o", str(folder / "subs.%(ext)s"), url]
    if runner is not None:
        runner(args)
    else:
        subprocess.run(args, capture_output=True, text=True, timeout=180, check=False)
    for lang in ("ru-orig", "en-orig", "ru", "en"):
        path = folder / f"subs.{lang}.vtt"
        if path.is_file():
            return path.read_text(encoding="utf-8", errors="replace")
    return ""
