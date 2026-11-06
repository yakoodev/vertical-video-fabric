"""Наблюдатель Twitch-канала проекта: новый VOD → фоновая загрузка → нарезка → карточки на одобрение.

Для проекта-серии вида ``stream_channel``. По умолчанию **выключен** (``enabled: false``); канал
проверяется раз в ``interval_hours`` (2 ч). Список VOD — ``yt-dlp --flat-playlist`` по
``https://www.twitch.tv/<канал>/videos?filter=archives&sort=time`` (только прошедшие эфиры).

* **Только завершённые VOD** (владелец запретил писать прямой эфир): VOD идущего эфира узнаём по
  заглушке превью ``404_processing``/``live_status``, а кроме того VOD берём, только когда его
  длина не менялась ``settle_minutes`` между двумя проверками (растущая запись — ещё эфир).
* При первом включении всё, что уже лежит на канале, — ``baseline`` (не качаем: одна запись
  10–12 ГБ); ``backfill: N`` — взять N последних. Любой VOD из списка можно поставить в очередь руками.
* Загрузка — только фоновая ``DownloadService`` (как ``POST /api/sources/download``), по одной на
  весь сервис; перед ней проверка места (``vod_size_gb`` + запас ``DOWNLOAD_FREE_RESERVE_GB``).
  Мало места — карточка владельцу «Повторить / Пропустить», без попытки качать.
* Нарезка: ``mode: pipeline`` — встроенный конвейер (``AutomationService``: анализ → кандидаты с
  весом чата → оформление проекта → рендер → ``submit_clip``), причём по настройкам проекта
  (app/agent_settings.py) ждёт ответов до нарезки и останавливается на черновиках; ``mode: ask`` — хук «нужен агент»:
  карточка «Новый VOD — нарезать?» с кнопками «Скачать и нарезать» / «Скачать — нарежет агент» /
  «Пропустить». Публикует по-прежнему только одобрение (app/autopilot.py).
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
import threading
from datetime import timedelta
from typing import Any, Callable

from app import autopilot as A
from app import projects as P

_log = logging.getLogger(__name__)

SCHEMA_SQL = [
    """
    CREATE TABLE IF NOT EXISTS vod_watch (
        project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
        config_json TEXT NOT NULL DEFAULT '{}',
        last_check_at TEXT NOT NULL DEFAULT '',
        next_check_at TEXT NOT NULL DEFAULT '',
        last_error TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    # status: baseline / live / seen / asking / queued / no_space / downloading / downloaded / cutting /
    # done / for_agent / skipped / failed. stable_since — с какой проверки длина VOD не меняется.
    """
    CREATE TABLE IF NOT EXISTS vod_watch_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        vod_id TEXT NOT NULL,
        url TEXT NOT NULL,
        title TEXT NOT NULL DEFAULT '',
        duration_sec REAL NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'seen',
        cutter TEXT NOT NULL DEFAULT 'pipeline',
        detail TEXT NOT NULL DEFAULT '',
        download_task_id INTEGER,
        source_id INTEGER REFERENCES sources(id) ON DELETE SET NULL,
        run_id INTEGER,
        approval_id INTEGER,
        attempts INTEGER NOT NULL DEFAULT 0,
        stable_since TEXT NOT NULL DEFAULT '',
        downloaded_at TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (project_id, vod_id)
    )
    """,
]

BUSY = ("downloading", "downloaded", "cutting")
MODES = ("pipeline", "ask")
GB = 1024 ** 3
MAX_ATTEMPTS = 2
LIST_LIMIT = 10
LIST_TIMEOUT = 120

DEFAULT_CONFIG: dict = {
    "enabled": False,
    "channel": "",
    "interval_hours": 2.0,
    "mode": "pipeline",
    "quality": "",
    "vod_size_gb": 12.0,
    "settle_minutes": 30,
    "backfill": 0,
    "max_clips": 5,
    "chat_wait_minutes": 20,
    "analysis": {"provider": "", "prompt_preset_id": 0, "use_transcript": True},
    "look": {"subtitles": True, "subtitle_profile_id": 0, "subtitle_provider": "", "layout": "",
             "censor_profanity": True, "nameplate": True, "end_screen": True, "ai_montage": False,
             "ai_metadata": False},
}


def ensure_vod_watch_schema(conn) -> None:
    for sql in SCHEMA_SQL:
        conn.execute(sql)


# --------------------------------------------------------------------------- #
# Настройки
# --------------------------------------------------------------------------- #


def channel_login(value: Any) -> str:
    """«sareamura», «twitch.tv/sareamura», «https://www.twitch.tv/sareamura/videos» → логин."""
    text = str(value or "").strip()
    match = re.search(r"twitch\.tv/([A-Za-z0-9_]{2,25})", text) or re.fullmatch(r"@?([A-Za-z0-9_]{2,25})", text)
    if not text:
        return ""
    if not match or match.group(1).lower() in ("videos", "directory", "settings"):
        raise ValueError("канал Twitch — логин (sareamura) или ссылка twitch.tv/<логин>")
    return match.group(1).lower()


def _num(value: Any, default: float, lo: float, hi: float) -> float:
    try:
        return min(hi, max(lo, float(value)))
    except (TypeError, ValueError):
        return default


def normalize_config(raw: Any) -> dict:
    from app.stream_layout import LAYOUT_MODES

    raw = raw if isinstance(raw, dict) else {}
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    cfg["enabled"] = bool(raw.get("enabled", False))
    cfg["channel"] = channel_login(raw.get("channel"))
    cfg["interval_hours"] = _num(raw.get("interval_hours"), 2.0, 0.5, 48.0)
    mode = str(raw.get("mode") or "pipeline").strip().lower()
    cfg["mode"] = P._choice(mode, MODES, "mode")
    cfg["quality"] = str(raw.get("quality") or "").strip()[:20]
    cfg["vod_size_gb"] = _num(raw.get("vod_size_gb"), 12.0, 0.5, 1000.0)
    for key, default, hi in (("settle_minutes", 30, 24 * 60), ("backfill", 0, 10), ("max_clips", 5, 30),
                             ("chat_wait_minutes", 20, 240)):
        cfg[key] = int(_num(raw.get(key), default, 0, hi))
    analysis = raw.get("analysis") if isinstance(raw.get("analysis"), dict) else {}
    provider = str(analysis.get("provider") or "").strip().lower()[:40]
    if provider == "polza":
        raise ValueError("платный шлюз polza в автозапуске запрещён — выбери другой анализ")
    cfg["analysis"] = {"provider": provider, "prompt_preset_id": int(_num(analysis.get("prompt_preset_id"), 0, 0, 1e9)),
                       "use_transcript": bool(analysis.get("use_transcript", True))}
    look_in = raw.get("look") if isinstance(raw.get("look"), dict) else {}
    look = cfg["look"]
    for key in ("subtitles", "censor_profanity", "nameplate", "end_screen", "ai_montage", "ai_metadata"):
        if key in look_in:
            look[key] = bool(look_in[key])
    look["subtitle_profile_id"] = int(_num(look_in.get("subtitle_profile_id"), 0, 0, 1e9))
    look["subtitle_provider"] = str(look_in.get("subtitle_provider") or "").strip().lower()[:20]
    layout = str(look_in.get("layout") or "").strip().lower()
    if layout and layout not in LAYOUT_MODES:
        raise ValueError(f"макет: пусто или одно из {', '.join(LAYOUT_MODES)}")
    look["layout"] = layout
    if cfg["enabled"] and not cfg["channel"]:
        raise ValueError("укажи канал Twitch — без него наблюдать нечего")
    return cfg


def _state(store: Any, project_id: int) -> dict:
    row = store.db.query_one("SELECT * FROM vod_watch WHERE project_id = ?", (int(project_id),))
    if row:
        return dict(row)
    P.get_project(store, project_id, include_sources=False)
    store.db.execute("INSERT OR IGNORE INTO vod_watch (project_id) VALUES (?)", (int(project_id),))
    return dict(store.db.query_one("SELECT * FROM vod_watch WHERE project_id = ?", (int(project_id),)))


def _set_state(store: Any, project_id: int, **fields: Any) -> None:
    _state(store, project_id)
    sets = ", ".join(f"{k} = ?" for k in fields)
    store.db.execute(f"UPDATE vod_watch SET {sets}, updated_at = CURRENT_TIMESTAMP WHERE project_id = ?",
                     (*fields.values(), int(project_id)))


def get_config(store: Any, project_id: int) -> dict:
    try:
        return normalize_config(P._loads(_state(store, project_id).get("config_json"), {}))
    except (ValueError, TypeError):
        return normalize_config({})


def update_config(store: Any, project_id: int, patch: dict) -> dict:
    if not isinstance(patch, dict):
        raise ValueError("настройки наблюдателя — объект")
    current = get_config(store, project_id)
    for key, value in patch.items():
        if key in ("analysis", "look") and isinstance(value, dict):
            current[key] = {**current[key], **value}
        elif key in DEFAULT_CONFIG:
            current[key] = value
    cfg = normalize_config(current)
    if cfg["enabled"] and P.get_project(store, project_id, include_sources=False)["kind"] != "stream_channel":
        raise ValueError("автозапуск на новый VOD — только у проекта вида «стрим-канал»")
    was = get_config(store, project_id)["enabled"]
    # Включили (снова) — первая проверка заново baseline: вышедшее, пока не следили, не качаем пачкой.
    _set_state(store, project_id, config_json=A._json(cfg),
               **({"next_check_at": "", "last_check_at": ""} if cfg["enabled"] and not was else {}))
    A.log_event(store, None, project_id, "vod_watch_config", "ui",
                f"наблюдатель VOD {'включён' if cfg['enabled'] else 'выключен'}: {cfg['channel'] or '—'}")
    return cfg


def get_watch(store: Any, project_id: int) -> dict:
    state = _state(store, project_id)
    return {"project_id": int(project_id), "config": get_config(store, project_id),
            "last_check_at": state["last_check_at"], "next_check_at": state["next_check_at"],
            "last_error": state["last_error"], "items": list_items(store, project_id)}


# --------------------------------------------------------------------------- #
# Список VOD канала
# --------------------------------------------------------------------------- #


def list_vods(channel: str, limit: int = LIST_LIMIT) -> list[dict]:
    """Последние прошедшие эфиры канала (новые первыми): yt-dlp --flat-playlist, без загрузки."""
    url = f"https://www.twitch.tv/{channel}/videos?filter=archives&sort=time"
    cmd = [sys.executable, "-m", "yt_dlp", "--flat-playlist", "--playlist-end", str(int(limit)),
           "--dump-single-json", "--no-warnings", "--socket-timeout", "30", url]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=LIST_TIMEOUT, check=False)
    except subprocess.TimeoutExpired as exc:
        raise ValueError("список VOD: yt-dlp не ответил за 2 мин") from exc
    if proc.returncode != 0:
        raise ValueError(f"список VOD: {(proc.stderr or proc.stdout).strip()[-300:] or 'yt-dlp упал'}")
    data = json.loads(proc.stdout or "{}")
    return [e for e in (data.get("entries") or []) if isinstance(e, dict)]


def _vod_of(entry: dict) -> dict | None:
    raw_id = str(entry.get("id") or "").strip()
    match = re.search(r"(\d{6,})", raw_id or str(entry.get("url") or ""))
    if not match:
        return None
    vod_id = match.group(1)
    return {"vod_id": vod_id, "url": f"https://www.twitch.tv/videos/{vod_id}", "title": str(entry.get("title") or "")[:300],
            "duration_sec": float(entry.get("duration") or 0), "live": is_live_entry(entry)}


def is_live_entry(entry: dict) -> bool:
    """VOD идущего эфира: флаги yt-dlp или заглушка превью «404_processing» у Twitch."""
    if entry.get("is_live") or str(entry.get("live_status") or "") in ("is_live", "is_upcoming", "post_live"):
        return True
    thumbs = [str(entry.get("thumbnail") or "")] + [str(t.get("url") or "") for t in entry.get("thumbnails") or []
                                                     if isinstance(t, dict)]
    return any("404_processing" in t for t in thumbs)


# --------------------------------------------------------------------------- #
# Элементы
# --------------------------------------------------------------------------- #


def list_items(store: Any, project_id: int, limit: int = 30) -> list[dict]:
    rows = store.db.query_all("SELECT * FROM vod_watch_items WHERE project_id = ? ORDER BY id DESC LIMIT ?",
                              (int(project_id), int(limit)))
    return [dict(r) for r in rows]


def get_item(store: Any, item_id: int) -> dict:
    row = store.db.query_one("SELECT * FROM vod_watch_items WHERE id = ?", (int(item_id),))
    if not row:
        raise KeyError(f"vod item not found: {item_id}")
    return dict(row)


def _set_item(store: Any, item_id: int, **fields: Any) -> None:
    sets = ", ".join(f"{k} = ?" for k in fields)
    store.db.execute(f"UPDATE vod_watch_items SET {sets}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                     (*fields.values(), int(item_id)))


def _note(store: Any, item: dict, event: str, text: str, approval_id: int | None = None) -> None:
    """В ленту автопилота и, если подключён бот, коротко в Telegram."""
    A.log_event(store, approval_id, item["project_id"], event, "vod_watch", text, {"vod_id": item["vod_id"]})
    try:
        from app import notify_tg

        if notify_tg.is_enabled(store) and event in ("vod_failed", "vod_for_agent", "vod_cut"):
            notify_tg.send_text(store, text, project_id=item["project_id"], purpose="note")
    except Exception as exc:  # noqa: BLE001 - уведомление не роняет наблюдатель
        _log.info("vod note failed: %s", exc)


def _label(item: dict) -> str:
    minutes = int(item.get("duration_sec") or 0) // 60
    length = f" ({minutes // 60}:{minutes % 60:02d})" if minutes else ""
    return f"«{item.get('title') or item['vod_id']}»{length}"


def queue_item(store: Any, item_id: int, cutter: str = "pipeline", actor: str = "ui") -> dict:
    """Поставить VOD в очередь загрузки (кнопка «Нарезать» у старого VOD или повтор после ошибки)."""
    item = get_item(store, item_id)
    if item["status"] in BUSY or item["status"] == "queued":
        return item
    cutter = P._choice(cutter, ("pipeline", "agent"), "cutter")
    try:  # уже скачан и файл на месте — второй раз 12 ГБ не качаем, сразу к нарезке
        source = store.get_source(int(item["source_id"])) if item["source_id"] else None
    except KeyError:
        source = None
    from pathlib import Path

    if source and source.get("status") == "ready" and Path(str(source.get("local_path") or "")).is_file():
        _set_item(store, item_id, status="downloaded", cutter=cutter, detail=f"источник #{source['id']} уже скачан",
                  downloaded_at=A.fmt_utc(A.now()))
    else:
        _set_item(store, item_id, status="queued", cutter=cutter, detail="в очереди загрузки", attempts=0)
    A.log_event(store, None, item["project_id"], "vod_queued", actor, _label(item))
    return get_item(store, item_id)


# --------------------------------------------------------------------------- #
# Проверка канала
# --------------------------------------------------------------------------- #


def check_project(store: Any, project_id: int, force: bool = False) -> dict:
    """Одна проверка канала: новые VOD → items; готовые (не эфир, длина устоялась) → очередь или карточка."""
    cfg = get_config(store, project_id)
    if not cfg["channel"] or (not cfg["enabled"] and not force):
        return {"ok": False, "skipped": "наблюдатель выключен или нет канала"}
    now = A.now()
    try:
        entries = list_vods(cfg["channel"])
    except Exception as exc:  # noqa: BLE001 - сеть/yt-dlp: попробуем в следующий раз
        # last_check_at — только удачная проверка: по нему узнаём первую (baseline) проверку.
        _set_state(store, project_id, last_error=str(exc)[:500],
                   next_check_at=A.fmt_utc(now + timedelta(hours=cfg["interval_hours"])))
        return {"ok": False, "error": str(exc)[:500]}
    known = {r["vod_id"]: dict(r) for r in store.db.query_all(
        "SELECT * FROM vod_watch_items WHERE project_id = ?", (int(project_id),))}
    # Первая удачная проверка после включения: всё, что уже на канале, — baseline (не качаем).
    first_run = not _state(store, project_id)["last_check_at"]
    found, ready, waiting = [], [], 0
    settle = timedelta(minutes=cfg["settle_minutes"])
    for index, entry in enumerate(entries):
        vod = _vod_of(entry)
        if vod is None:
            continue
        row = known.get(vod["vod_id"])
        if row is None:
            status = "baseline" if first_run and index >= cfg["backfill"] else ("live" if vod["live"] else "seen")
            store.db.execute(
                "INSERT INTO vod_watch_items (project_id, vod_id, url, title, duration_sec, status, stable_since, detail) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (int(project_id), vod["vod_id"], vod["url"], vod["title"], vod["duration_sec"], status, A.fmt_utc(now),
                 {"baseline": "был на канале до включения", "live": "идёт эфир — ждём конца"}.get(status, "ждём, пока "
                                                                                                     "длина устоится")))
            row = dict(store.db.query_one("SELECT * FROM vod_watch_items WHERE project_id = ? AND vod_id = ?",
                                          (int(project_id), vod["vod_id"])))
            if status != "baseline":
                found.append(row)
        if row["status"] not in ("live", "seen"):
            continue
        grew = abs(vod["duration_sec"] - float(row["duration_sec"] or 0)) >= 1.0
        if vod["live"] or grew:
            _set_item(store, row["id"], status="live" if vod["live"] else "seen", duration_sec=vod["duration_sec"],
                      title=vod["title"] or row["title"], stable_since=A.fmt_utc(now),
                      detail="идёт эфир — ждём конца" if vod["live"] else "запись ещё растёт — ждём")
            waiting += 1
            continue
        if row["status"] == "live":  # эфир кончился — отсчёт устоявшейся длины заново
            _set_item(store, row["id"], status="seen", stable_since=A.fmt_utc(now), detail="ждём, пока длина устоится")
            waiting += 1
            continue
        since = A.parse_utc(row["stable_since"]) or now
        if now - since < settle:
            waiting += 1
            continue
        ready.append(_make_ready(store, cfg, get_item(store, row["id"])))
    delay = timedelta(hours=cfg["interval_hours"])
    if waiting:
        delay = min(delay, max(timedelta(minutes=5), settle))
    _set_state(store, project_id, last_check_at=A.fmt_utc(now), last_error="", next_check_at=A.fmt_utc(now + delay))
    return {"ok": True, "found": [r["vod_id"] for r in found], "ready": [r["vod_id"] for r in ready],
            "waiting": waiting, "baseline": first_run}


def _make_ready(store: Any, cfg: dict, item: dict) -> dict:
    if cfg["mode"] == "ask":
        project = P.get_project(store, item["project_id"], include_sources=False)
        card = A.submit_card(
            store, item["project_id"], "other",
            f"Новый VOD на twitch.tv/{cfg['channel']}: {_label(item)}. Скачать (~{cfg['vod_size_gb']:g} ГБ) и нарезать "
            f"для «{project['name']}»? «Нарежет агент» — только скачать, клипы сделает Claude/Gemini по плейбуку.",
            {"action": "vod_new", "item_id": item["id"], "url": item["url"]},
            title=f"Новый VOD {_label(item)}", dedupe_key=f"vod_new:{item['id']}", submitted_by="vod_watch")
        _set_item(store, item["id"], status="asking", approval_id=card["id"], detail="ждёт ответа владельца")
    else:
        _set_item(store, item["id"], status="queued", cutter="pipeline", detail="в очереди загрузки")
    A.log_event(store, None, item["project_id"], "vod_found", "vod_watch", f"новый VOD {_label(item)}",
                {"vod_id": item["vod_id"]})
    return get_item(store, item["id"])


# --------------------------------------------------------------------------- #
# Карточки: «нарезать?», «мало места»
# --------------------------------------------------------------------------- #


def _on_vod_new(store: Any, approval: dict, action: str, comment: str, actor: str) -> dict:
    item = get_item(store, int(approval["payload"]["item_id"]))
    if action in ("approve", "both"):
        return {"vod": queue_item(store, item["id"], "agent" if action == "both" else "pipeline", actor)}
    _set_item(store, item["id"], status="skipped", detail=f"владелец: пропустить{(' — ' + comment) if comment else ''}")
    return {"vod": get_item(store, item["id"])}


def _on_vod_space(store: Any, approval: dict, action: str, comment: str, actor: str) -> dict:
    item = get_item(store, int(approval["payload"]["item_id"]))
    if action in ("approve", "both"):
        return {"vod": queue_item(store, item["id"], item["cutter"], actor)}
    _set_item(store, item["id"], status="skipped", detail="владелец: пропустить (мало места)")
    return {"vod": get_item(store, item["id"])}


A.register_card_action("vod_new", [("approve", "✂️ Скачать и нарезать"), ("both", "⬇ Скачать — нарежет агент"),
                                   ("reject", "🗑 Пропустить")], _on_vod_new)
A.register_card_action("vod_space", [("approve", "🔁 Повторить"), ("reject", "🗑 Пропустить")], _on_vod_space)


def _space_card(store: Any, item: dict, error: str) -> None:
    card = A.submit_card(
        store, item["project_id"], "other",
        f"VOD {_label(item)} не скачан: {error}. Освободи место (G:\\vvf-offload, старые записи) и нажми «Повторить».",
        {"action": "vod_space", "item_id": item["id"]}, title=f"Мало места для VOD {_label(item)}",
        submitted_by="vod_watch")
    _set_item(store, item["id"], status="no_space", approval_id=card["id"], detail=error[:500])


# --------------------------------------------------------------------------- #
# Загрузка и нарезка
# --------------------------------------------------------------------------- #


def ensure_free_space(need: tuple[int, int] | None) -> None:
    from app.ingest import ensure_free_space as check

    check(need)


def _start_download(store: Any, downloads: Any, item: dict, cfg: dict) -> None:
    size = int(cfg["vod_size_gb"] * GB)
    try:
        ensure_free_space((size, size))
    except ValueError as exc:
        _space_card(store, item, str(exc))
        _note(store, item, "vod_no_space", f"мало места для VOD {_label(item)}")
        return
    try:
        task = downloads.start(item["url"], quality=cfg["quality"])
    except Exception as exc:  # noqa: BLE001
        _fail(store, item, f"загрузка не стартовала: {exc}")
        return
    _set_item(store, item["id"], status="downloading", download_task_id=int(task["id"]), attempts=item["attempts"] + 1,
              detail="скачивается (Активность)")


def _fail(store: Any, item: dict, error: str) -> None:
    _set_item(store, item["id"], status="failed", detail=error[:500])
    _note(store, item, "vod_failed", f"⚠️ VOD {_label(item)}: {error[:300]}")


def _poll_download(store: Any, downloads: Any, item: dict) -> None:
    try:
        task = downloads.get(int(item["download_task_id"]))
    except (KeyError, TypeError, ValueError):
        task = {"status": "failed", "error": "загрузка прервана перезапуском сервиса"}
    status = task.get("status")
    if status in ("queued", "downloading", "cancelling"):
        _set_item(store, item["id"], detail=str(task.get("message") or "скачивается")[:200])
        return
    if status == "succeeded" and task.get("source_id"):
        source_id = int(task["source_id"])
        P.set_source_project(store, source_id, int(item["project_id"]))
        _set_item(store, item["id"], status="downloaded", source_id=source_id, downloaded_at=A.fmt_utc(A.now()),
                  detail=f"скачан — источник #{source_id}")
        return
    if status == "cancelled":
        _set_item(store, item["id"], status="skipped", detail="загрузку отменили в «Активности»")
        return
    error = str(task.get("error") or "загрузка не удалась")
    if "мало места" in error:
        _space_card(store, item, error)
    elif item["attempts"] < MAX_ATTEMPTS:
        _set_item(store, item["id"], status="queued", detail=f"повтор: {error[:200]}")
    else:
        _fail(store, item, error)


def _chat_pending(store: Any, item: dict, cfg: dict) -> bool:
    """Дождаться импорта чата (он идёт сам после загрузки Twitch-VOD), но не дольше chat_wait_minutes."""
    try:
        from app.twitch_chat import get_chat_status

        status = get_chat_status(store, int(item["source_id"])).get("status")
    except Exception:  # noqa: BLE001
        return False
    since = A.parse_utc(item["downloaded_at"]) or A.now()
    return status in ("queued", "running") and A.now() - since < timedelta(minutes=cfg["chat_wait_minutes"])


def cut_settings(store: Any, cfg: dict) -> dict:
    """Аргументы AutomationService.start_run из настроек наблюдателя проекта."""
    look, analysis = cfg["look"], cfg["analysis"]
    prompt = ""
    if analysis["prompt_preset_id"]:
        try:
            preset = store.get_prompt_preset(analysis["prompt_preset_id"])
            prompt = preset["prompt"] if preset.get("task") == "analysis" else ""
        except KeyError:
            prompt = ""
    profile = None
    if look["subtitles"]:
        try:
            profile = look["subtitle_profile_id"] or int(store.get_app_setting_value("default_subtitle_profile_id") or 0)
        except (TypeError, ValueError):
            profile = 0
    return {
        "analysis": {"provider": analysis["provider"], "model": "", "prompt": prompt,
                     "use_transcript": analysis["use_transcript"]},
        "render": {"subtitle_profile_id": profile or None,
                   "subtitle_provider": (look["subtitle_provider"] or None) if look["subtitles"] else None},
        "publish": {"max_clips": cfg["max_clips"], "submit": True, "ai_metadata": look["ai_metadata"],
                    "ai_montage": look["ai_montage"]},
        "look": look,
    }


def _start_cut(store: Any, automation: Any, item: dict, cfg: dict) -> None:
    if item["cutter"] == "agent":
        _set_item(store, item["id"], status="for_agent", detail=f"источник #{item['source_id']} ждёт агента")
        _note(store, item, "vod_for_agent",
              f"⬇ VOD {_label(item)} скачан: источник #{item['source_id']}. Нарезка за агентом — "
              f"get_playbook(\"autopilot\"), get_project_brief(source_id={item['source_id']}).")
        return
    run = automation.start_run(source_id=int(item["source_id"]), project_id=int(item["project_id"]),
                               label=f"VOD {_label(item)}", **cut_settings(store, cfg))
    _set_item(store, item["id"], status="cutting", run_id=int(run["id"]), detail="нарезка (Авто)")


def _poll_cut(store: Any, automation: Any, item: dict) -> None:
    run = automation.get_run(int(item["run_id"] or 0))
    if run is None:
        _fail(store, item, "нарезка прервана перезапуском сервиса — «Нарезать» ещё раз")
    elif run["status"] == "done":
        _set_item(store, item["id"], status="done", detail=run.get("message") or "готово")
        where = "«Черновики» этого видео" if run.get("stopped_at_list") else "«Одобрение»"
        _note(store, item, "vod_cut", f"✂️ VOD {_label(item)}: {run.get('message') or 'нарезан'} — смотри {where}")
    elif run["status"] == "failed":
        _fail(store, item, f"нарезка: {run.get('error') or 'ошибка'}")
    else:
        _set_item(store, item["id"], detail=str(run.get("message") or "нарезка")[:200])


def advance(store: Any, downloads: Any, automation: Any) -> None:
    """Продвинуть очередь: загрузки → нарезка; новая загрузка — только когда ничего не качается и не режется."""
    def step(status: str, fn: Callable[[dict, dict], None]) -> None:
        for row in store.db.query_all("SELECT * FROM vod_watch_items WHERE status = ? ORDER BY id", (status,)):
            item = dict(row)
            try:
                fn(item, get_config(store, item["project_id"]))
            except Exception as exc:  # noqa: BLE001 - один VOD не держит остальные
                _log.warning("vod item %s: %s", item["id"], exc)
                _fail(store, item, f"{type(exc).__name__}: {exc}")

    def cut(item: dict, cfg: dict) -> None:
        if _chat_pending(store, item, cfg) or store.db.query_one(
                "SELECT 1 FROM vod_watch_items WHERE status = 'cutting' LIMIT 1"):
            return
        from app.agent_settings import NAME_WAIT
        from app.stream_questions import montage_refusal

        # Настройка проекта «Ждать ответов»: запись ждёт скачанной, причина видна в строке VOD.
        refusal = montage_refusal(store, int(item["source_id"])) if item["cutter"] != "agent" else None
        if refusal:
            _set_item(store, item["id"], detail=f"Ждёт ответов владельца на вопросы этого видео: настройка проекта «{NAME_WAIT}»")
            return
        _start_cut(store, automation, item, cfg)

    def expire(item: dict, cfg: dict) -> None:
        if item["approval_id"] and A.get_approval(store, int(item["approval_id"]))["status"] == "expired":
            _set_item(store, item["id"], status="skipped", detail="владелец не ответил — карточка протухла")

    step("downloading", lambda item, cfg: _poll_download(store, downloads, item))
    step("cutting", lambda item, cfg: _poll_cut(store, automation, item))
    step("downloaded", cut)
    step("asking", expire)
    if store.db.query_one(f"SELECT 1 FROM vod_watch_items WHERE status IN {BUSY} LIMIT 1"):
        return
    for row in store.db.query_all("SELECT * FROM vod_watch_items WHERE status = 'queued' ORDER BY id"):
        item = dict(row)
        _start_download(store, downloads, item, get_config(store, item["project_id"]))
        if get_item(store, item["id"])["status"] == "downloading":
            return


def tick(store: Any, downloads: Any, automation: Any) -> dict:
    """Проход фона: проверить каналы, у которых подошёл срок, и продвинуть очередь."""
    checked = {}
    now = A.now()
    for row in store.db.query_all("SELECT project_id, next_check_at FROM vod_watch"):
        pid = int(row["project_id"])
        if not get_config(store, pid)["enabled"]:
            continue
        due = A.parse_utc(row["next_check_at"])
        if due is None or due <= now:
            checked[pid] = check_project(store, pid)
    advance(store, downloads, automation)
    return {"checked": checked}


class VodWatchWorker:
    """Фон наблюдателя (раз в минуту). ``VOD_WATCH_WORKER=0`` — не запускать."""

    IDLE_SECONDS = 60

    def __init__(self, store: Any, downloads: Any, automation: Any) -> None:
        self.store, self.downloads, self.automation = store, downloads, automation
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        import os

        if os.getenv("VOD_WATCH_WORKER", "1").strip().lower() in ("0", "false", "no", "off"):
            return
        if self._thread and self._thread.is_alive() and not self._stop.is_set():
            return
        # Перезапуск после выключения модуля (app/modules.py): новый флаг остановки.
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(self._stop,), name="vod-watch", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self, stop: threading.Event | None = None) -> None:
        stop = stop or self._stop
        while not stop.wait(self.IDLE_SECONDS):
            try:
                tick(self.store, self.downloads, self.automation)
            except Exception as exc:  # noqa: BLE001 - фон не должен умирать
                _log.warning("vod watch tick failed: %s", exc)
