"""Статистика опубликованных клипов — обратная связь для монтажа: что зашло.

Снимок = (публикация, время, просмотры, лайки, комментарии, репосты). Берём:

* YouTube и TikTok — метаданные публичной ссылки через yt-dlp
  (``--dump-json --skip-download``): без логина, как кривая пересмотров
  (app/youtube_heatmap.py). Напрямую не вышло — ещё раз через прокси загрузок;
* Instagram — без логина чисел не отдаёт: через instagrapi тем же входом по cookie
  ``sessionid``, что и загрузка (app/providers/instagram.py); нет сессии — пропуск
  с причиной.

Площадки не любят частых запросов: по одной ссылке, с паузой, не чаще раза в
STATS_INTERVAL_HOURS на публикацию (вручную — не чаще MIN_GAP_MINUTES). Сбор
всегда один: второй запуск, пока идёт первый, ничего не делает.

Сводка ``clip_performance`` кладёт рядом с числами признаки монтажа клипа
(длина, хук-титр, субтитры, музыка, слои, вырезы, раскладка, хромакей) —
ИИ-монтажёр смотрит её (MCP get_performance) перед новым клипом.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Callable
from urllib.parse import urlsplit

from app.settings import settings

MIN_GAP_MINUTES = 10  # вручную по одной публикации — не чаще
YTDLP_TIMEOUT = 90
CHECK_EVERY_SECONDS = 10 * 60  # как часто фон проверяет, не пора ли
FIRST_CHECK_DELAY_SECONDS = 2 * 60  # после старта сервиса — не сразу

_run_lock = threading.Lock()
_status: dict[str, Any] = {"running": False, "started_at": None, "finished_at": None, "last": None}


class StatsSkipped(Exception):
    """Взять числа нечем (площадка не поддерживается, нет сессии) — не сбой площадки."""


def platform_of_url(url: str) -> str:
    host = (urlsplit(url or "").hostname or "").lower()
    if host.endswith("youtube.com") or host == "youtu.be":
        return "youtube"
    if host.endswith("tiktok.com"):
        return "tiktok"
    if host.endswith("instagram.com"):
        return "instagram"
    return ""


def build_ytdlp_stats_args(url: str) -> list[str]:
    # Прокси — окружением процесса (proxy_config.proxy_env), не --proxy: креды видно в ps.
    return ["yt-dlp", "--dump-json", "--skip-download", "--no-playlist", "--no-warnings", url]


def _count(info: dict, *keys: str) -> int | None:
    for key in keys:
        value = info.get(key)
        if isinstance(value, (int, float)) and value >= 0:
            return int(value)
    return None


def parse_ytdlp_info(info: dict) -> dict:
    return {
        "views": _count(info, "view_count", "play_count"),
        "likes": _count(info, "like_count"),
        "comments": _count(info, "comment_count"),
        "shares": _count(info, "repost_count", "share_count"),
    }


def fetch_ytdlp_stats(url: str) -> dict:
    """Числа публичной ссылки из метаданных yt-dlp. Ошибка — RuntimeError с хвостом лога."""
    from app.proxy_config import download_proxy, mask_secrets, proxy_env

    attempts = [""]
    proxy = download_proxy()
    if proxy:
        attempts.append(proxy)
    last_error = "yt-dlp ничего не вернул"
    for proxy_url in attempts:
        try:
            proc = subprocess.run(build_ytdlp_stats_args(url), capture_output=True, text=True,
                                  timeout=YTDLP_TIMEOUT, check=False,
                                  env=proxy_env(proxy_url) if proxy_url else None)
        except subprocess.TimeoutExpired:
            last_error = f"yt-dlp не ответил за {YTDLP_TIMEOUT} с"
            continue
        except OSError as exc:
            raise RuntimeError(f"yt-dlp не запустился: {exc}") from exc
        if proc.returncode == 0 and proc.stdout.strip():
            try:
                info = json.loads(proc.stdout.strip().splitlines()[0])
            except ValueError:
                last_error = "yt-dlp вернул не JSON"
                continue
            stats = parse_ytdlp_info(info if isinstance(info, dict) else {})
            if all(v is None for v in stats.values()):
                raise RuntimeError("площадка не отдала числа (видео скрыто или удалено?)")
            return stats
        tail = [line for line in (proc.stderr or proc.stdout or "").splitlines() if line.strip()][-1:]
        last_error = mask_secrets(" ".join(tail) or f"yt-dlp код {proc.returncode}")[:240]
    raise RuntimeError(last_error)


def _fetch_one(store: Any, target: dict, ig_clients: dict) -> dict:
    platform = platform_of_url(target.get("remote_url") or "")
    if platform in ("youtube", "tiktok"):
        return fetch_ytdlp_stats(target["remote_url"])
    if platform == "instagram":
        from app.providers import instagram

        account_id = int(target["account_id"])
        try:
            if account_id not in ig_clients:
                # Один вход на аккаунт за сбор: лишние логины Instagram замечает.
                ig_clients[account_id] = instagram.session_client(
                    store.get_account_cookies(account_id), store.get_account_proxy_url(account_id)
                )
            client = ig_clients[account_id]
        except instagram.InstagramUnavailable as exc:
            ig_clients[account_id] = exc
            raise StatsSkipped(str(exc)) from exc
        if isinstance(client, Exception):
            raise StatsSkipped(str(client))
        return instagram.media_stats(client, str(target.get("remote_id") or ""), target.get("remote_url") or "")
    raise StatsSkipped("площадка не поддерживается — статистики нет")


def _parse_time(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.strptime(str(text)[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def due_targets(targets: list[dict], *, force: bool, now: datetime | None = None) -> list[dict]:
    """Кому пора за числами: фоном — раз в STATS_INTERVAL_HOURS, вручную — не чаще MIN_GAP_MINUTES."""
    now = now or datetime.utcnow()
    gap = timedelta(minutes=MIN_GAP_MINUTES) if force else timedelta(hours=max(0.1, settings.stats_interval_hours))
    out = []
    for target in targets:
        last = _parse_time(target.get("last_attempt_at"))
        if last is None or now - last >= gap:
            out.append(target)
    return out


def collect_stats(store: Any, *, force: bool = False, sleep: Callable[[float], None] = time.sleep,
                  pause: float | None = None) -> dict:
    """Один проход сбора: по одной ссылке, с паузой между запросами к площадкам."""
    if not _run_lock.acquire(blocking=False):
        return {"busy": True, **status()}
    pause = settings.stats_pause_seconds if pause is None else pause
    summary = {"checked": 0, "ok": 0, "failed": 0, "skipped": 0}
    _status.update(running=True, started_at=_now_text(), finished_at=None)
    try:
        ig_clients: dict = {}
        asked_network = False
        for target in due_targets(store.list_published_targets(), force=force):
            summary["checked"] += 1
            platform = platform_of_url(target.get("remote_url") or "") or target.get("platform") or ""
            if asked_network and platform and pause > 0:
                sleep(pause)
            try:
                if platform:
                    asked_network = True
                stats = _fetch_one(store, target, ig_clients)
            except StatsSkipped as exc:
                summary["skipped"] += 1
                store.add_publication_stats(target["id"], platform, None, error=f"пропуск: {exc}")
                continue
            except Exception as exc:  # noqa: BLE001 - одна ссылка не валит весь сбор
                summary["failed"] += 1
                store.add_publication_stats(target["id"], platform, None, error=str(exc)[:300] or "ошибка")
                continue
            summary["ok"] += 1
            store.add_publication_stats(target["id"], platform, stats)
    finally:
        _status.update(running=False, finished_at=_now_text(), last=summary)
        _run_lock.release()
    return {**summary, **status()}


def start_collect(store: Any, *, force: bool = True) -> dict:
    """Сбор в фоне (кнопка «Обновить статистику»): ответ сразу, числа — по мере сбора."""
    if _run_lock.locked():
        return {"started": False, **status()}
    threading.Thread(target=lambda: collect_stats(store, force=force), name="stats-collect", daemon=True).start()
    return {"started": True, **status(), "running": True}


def status() -> dict:
    return {
        "running": bool(_status["running"]),
        "started_at": _status["started_at"],
        "finished_at": _status["finished_at"],
        "last": _status["last"],
        "interval_hours": settings.stats_interval_hours,
    }


def _now_text() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


class StatsCollector:
    """Фоновый сбор раз в STATS_INTERVAL_HOURS (0 — выключен), как JobWorker."""

    def __init__(self, store: Any) -> None:
        self.store = store
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if settings.stats_interval_hours <= 0 or (self._thread and self._thread.is_alive()):
            return
        self._thread = threading.Thread(target=self._run, name="stats-collector", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        if self._stop.wait(FIRST_CHECK_DELAY_SECONDS):
            return
        while not self._stop.is_set():
            try:
                collect_stats(self.store, sleep=self._stop.wait)
            except Exception:  # noqa: BLE001 - фон не должен умирать
                pass
            self._stop.wait(CHECK_EVERY_SECONDS)


# --------------------------------------------------------------------------- #
# Сводка «что работает»: числа + признаки монтажа
# --------------------------------------------------------------------------- #

HOOK_WINDOW_SEC = 1.5  # титр, что появляется до этой секунды, — хук


def plan_features(store: Any, clip_plan_id: int) -> dict:
    """Признаки монтажа клипа — то, что можно сравнить между зашедшими и нет."""
    from app.agent_tools import clip_timeline
    from app.cuts import CutMap, normalize_cuts

    timeline = clip_timeline(store, clip_plan_id)
    render = timeline["render"]
    plan = timeline["plan"]
    layers = [layer for layer in render.get("layers") or [] if not layer.get("hidden")]
    cuts = normalize_cuts(render.get("cuts"))
    duration = float(timeline["duration"])
    if cuts:
        duration = CutMap(cuts).duration(duration)
    texts = [layer for layer in layers if layer.get("type") in ("text", "callout")]
    hook = next((layer for layer in sorted(texts, key=lambda x: float(x.get("start") or 0))
                 if float(layer.get("start") or 0) <= HOOK_WINDOW_SEC), None)
    try:
        keyed = bool(store.get_source(int(plan["source_id"])).get("chroma_key"))
    except (KeyError, TypeError, ValueError):
        keyed = False
    return {
        "duration": round(duration, 1),
        "pieces": len(timeline["pieces"]),
        "hook_title": bool(hook),
        "hook_text": str(((hook or {}).get("props") or {}).get("text") or "")[:80],
        "subtitles": bool(render.get("subs_on")) or any(layer.get("type") == "captions" for layer in layers),
        "music": bool(render.get("music_on") and render.get("track_id")),
        "layers": sum(1 for layer in layers if layer.get("type") != "audio"),
        "sfx": sum(1 for layer in layers if layer.get("type") == "audio"),
        "cuts": len(cuts),
        "inserts": len(render.get("inserts") or []),
        "camera_moves": len(render.get("camera") or []),
        "layout": (render.get("layout") or {}).get("mode") or "single",
        "chroma_key": keyed,
        "transition": (render.get("transition") or {}).get("type") or "",
    }


def _sum(values: list) -> int | None:
    known = [int(v) for v in values if v is not None]
    return sum(known) if known else None


def clip_performance(store: Any) -> list[dict]:
    """Клипы с публикациями: последние числа (сумма по площадкам) + признаки монтажа.
    Отсортировано по просмотрам, клипы без чисел — в конце."""
    latest = store.latest_publication_stats()
    groups: dict[str, dict] = {}
    plan_of_clip: dict[int, int | None] = {}
    now = datetime.utcnow()
    for target in store.list_published_targets():
        clip_id = target.get("clip_id")
        plan_id = None
        if clip_id:
            if clip_id not in plan_of_clip:
                try:
                    plan_of_clip[clip_id] = store.get_clip(int(clip_id)).get("clip_plan_id")
                except KeyError:
                    plan_of_clip[clip_id] = None
            plan_id = plan_of_clip[clip_id]
        key = f"plan:{plan_id}" if plan_id else (f"clip:{clip_id}" if clip_id else f"job:{target['job_id']}")
        group = groups.setdefault(key, {"clip_plan_id": plan_id, "clip_id": clip_id, "title": target.get("title") or "",
                                        "publications": []})
        stats = latest.get(int(target["id"]), {})
        published = _parse_time(target.get("finished_at"))
        group["publications"].append({
            "target_id": target["id"], "job_id": target["job_id"], "platform": target.get("platform") or "",
            "url": target.get("remote_url") or "",
            "published_at": target.get("finished_at"),
            "age_days": round((now - published).total_seconds() / 86400, 1) if published else None,
            "views": stats.get("views"), "likes": stats.get("likes"), "comments": stats.get("comments"),
            "shares": stats.get("shares"), "captured_at": stats.get("captured_at"),
            "error": stats.get("error") or "",
        })
    out = []
    for group in groups.values():
        pubs = group["publications"]
        views = _sum([p["views"] for p in pubs])
        likes = _sum([p["likes"] for p in pubs])
        comments = _sum([p["comments"] for p in pubs])
        ages = [p["age_days"] for p in pubs if p["age_days"] is not None]
        captured = [p["captured_at"] for p in pubs if p["captured_at"]]
        features = None
        if group["clip_plan_id"]:
            try:
                features = plan_features(store, int(group["clip_plan_id"]))
            except Exception:  # noqa: BLE001 - клип удалён/сломан: числа всё равно показываем
                features = None
            try:
                group["title"] = store.get_clip_plan(int(group["clip_plan_id"]), include_segments=False).get("title") \
                    or group["title"]
            except (KeyError, AttributeError):
                pass
        age = max(ages) if ages else None
        out.append({
            **group,
            "views": views, "likes": likes, "comments": comments, "shares": _sum([p["shares"] for p in pubs]),
            "like_rate": round(likes / views, 4) if views and likes is not None else None,
            "views_per_day": round(views / max(1.0, age), 1) if views is not None and age is not None else None,
            "age_days": age,
            "captured_at": max(captured) if captured else None,
            "features": features,
        })
    out.sort(key=lambda item: (item["views"] is None, -(item["views"] or 0)))
    return out


BOOL_FEATURES = ("hook_title", "subtitles", "music", "chroma_key")


def performance_report(store: Any, limit: int = 5) -> dict:
    """Топ и антитоп клипов по просмотрам с признаками + сравнение «с признаком / без»."""
    limit = max(1, min(int(limit or 5), 20))
    every = clip_performance(store)
    rows = [row for row in every if row["views"] is not None]
    if not rows:
        # Агент стримов: «get_performance пуст» — непонятно, сломано или так и надо. Так и надо.
        hint = ("Опубликованных клипов ещё нет — статистики нет, это нормально, а не ошибка. Монтируй по правилам "
                "docs/AGENTS.md («Что обычно улучшает клип»); числа появятся после публикаций владельцем.")
        if every:
            hint = (f"Публикаций {len(every)}, но просмотры ещё не собраны (сбор раз в "
                    f"{settings.stats_interval_hours:g} ч, вручную — «Обновить статистику» на странице «Публикации»). "
                    "Выводов пока не делай — монтируй по правилам docs/AGENTS.md.")
        return {"clips_with_stats": 0, "published": len(every), "top": [], "bottom": [], "compare": {},
                "hint": hint, "note": hint, "status": status()}
    top = rows[:limit]
    bottom = [row for row in rows[::-1][:limit] if row not in top]

    def brief(row: dict) -> dict:
        return {key: row.get(key) for key in ("clip_plan_id", "title", "views", "likes", "comments", "like_rate",
                                              "views_per_day", "age_days", "captured_at", "features")} | {
            "platforms": sorted({p["platform"] for p in row["publications"]})}

    compare = {}
    with_features = [row for row in rows if row.get("features")]
    for name in (*BOOL_FEATURES, "sfx", "cuts"):
        yes = [row["views"] for row in with_features if row["features"].get(name)]
        no = [row["views"] for row in with_features if not row["features"].get(name)]
        if yes and no:
            compare[name] = {"with_avg_views": round(sum(yes) / len(yes)), "with_n": len(yes),
                             "without_avg_views": round(sum(no) / len(no)), "without_n": len(no)}
    note = ("Сравнивай с осторожностью: просмотры зависят от возраста публикации (age_days, views_per_day) и "
            "площадки. Мало клипов — это намёк, а не закон.")
    if len(rows) < 4:
        note = "Данных мало (меньше 4 клипов с числами) — выводов не делай. " + note
    return {"clips_with_stats": len(rows), "top": [brief(r) for r in top], "bottom": [brief(r) for r in bottom],
            "compare": compare, "note": note, "status": status()}
