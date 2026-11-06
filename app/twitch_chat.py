"""💬 Чат Twitch-VOD: импорт, хранение, «хайп»-активность и привязка к клипу.

Откуда берём чат (проверено вживую, октябрь 2026 — см. docs/twitch-chat.md):

* Публичный GQL веб-плеера ``https://gql.twitch.tv/gql`` с публичным Client-ID
  сайта (тот же, что у TwitchDownloader). Ключ API не нужен.
* Листание ТОЛЬКО по ``contentOffsetSeconds``: курсорные запросы (``cursor``/
  ``after``) Twitch теперь режет «failed integrity check». Страница — ~50–60
  сообщений вокруг запрошенной секунды; следующая страница запрашивается с
  последней секунды страницы (дубли отсекает уникальный индекс по id). Сверка с
  перебором «каждой секунды» дала то же число сообщений.
* Пакет ``chat-downloader`` для Twitch-VOD больше не работает (ходит курсором).

Сторонние смайлы (7TV, BTTV, FFZ: канал + глобальные) сводятся в карту
``имя → url`` на источник; родные смайлы Twitch приходят во фрагментах с id.

Модуль не тянет ``app.main``: всё работает с ``AppStore`` (``store.db``), а
сеть спрятана за ``TwitchChatClient`` — тесты подсовывают фейк.

Где что лежит. Здесь — импорт чата. Вынесено: клиент Twitch ``app/twitch_chat_client.py``, разбор и хранение ``app/twitch_chat_store.py``, активность
``app/twitch_chat_activity.py``. Те модули этот не импортируют, а он их — поэтому все имена по-прежнему доступны отсюда.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from typing import Any, Callable

from app.twitch_chat_client import (  # noqa: F401
    TWITCH_GQL_URL, TWITCH_WEB_CLIENT_ID, COMMENTS_PERSISTED_HASH, _VOD_RE, _CLIP_RE, parse_twitch_vod_ref,
    ChatClient, TwitchChatClient, _gql_id, _comments_from_video, _seventv_set, _bttv_list, _bttv_user, _ffz_sets,
)
from app.twitch_chat_store import (  # noqa: F401
    TWITCH_EMOTE_URL, DEFAULT_NAME_COLORS, parse_comment, default_color, render_fragments, CHAT_SCHEMA_SQL,
    ensure_chat_schema, _schema_ready, _db, get_chat_status, _upsert_status, chat_emotes, chat_badges, has_chat,
    _loads, insert_messages, delete_chat, _message_out, list_messages, clip_piece_starts, clip_messages, _ACTIVE,
)
from app.twitch_chat_activity import (  # noqa: F401
    HYPE_TOKENS, _LAUGH_RE, _activity_cache, _activity_lock, _hype_token, _is_caps, score_activity, _median,
    chat_activity, hype_windows_from_activity, chat_hype_windows, chat_prompt_block, format_ts,
)

def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


_ACTIVE_LOCK = threading.Lock()


def resolve_vod(client: ChatClient, source: dict, vod: str = "", vod_start_sec: float | None = None) -> dict:
    """Какой VOD и с какой секунды соответствует нулю исходника."""
    ref = parse_twitch_vod_ref(vod) or parse_twitch_vod_ref(source.get("original_url") or "")
    if not ref:
        raise ValueError("у источника нет ссылки на Twitch VOD — передайте vod (ссылку или id)")
    start = 0.0
    if ref.get("clip_slug"):
        clip = client.clip_info(ref["clip_slug"])
        if not clip:
            raise LookupError("клип Twitch не найден или у него нет VOD")
        ref = {"vod_id": clip["vod_id"]}
        start = float(clip["start_sec"])
    if vod_start_sec is not None:
        start = max(0.0, float(vod_start_sec))
    return {"vod_id": ref["vod_id"], "vod_start_sec": start}


def import_chat(
    store,
    source_id: int,
    *,
    client: ChatClient | None = None,
    vod: str = "",
    vod_start_sec: float | None = None,
    workers: int | None = None,
) -> dict:
    """Скачать весь чат VOD в БД (синхронно). Возвращает статус."""
    own_client = client is None
    client = client or TwitchChatClient()
    try:
        source = store.get_source(source_id)
        target = resolve_vod(client, source, vod, vod_start_sec)
        vod_id, shift = target["vod_id"], target["vod_start_sec"]
        info = client.video_info(vod_id)
        if not info:
            raise LookupError(f"VOD {vod_id} недоступен: удалён, скрыт или только для подписчиков")
        start = shift
        end = float(info.get("length_sec") or 0)
        src_duration = float(source.get("duration_sec") or 0)
        if src_duration > 0:
            end = min(end, shift + src_duration + 1) if end > 0 else shift + src_duration + 1
        if end <= start:
            end = start + max(src_duration, 1.0)
        db = _db(store)
        db.execute("DELETE FROM chat_messages WHERE source_id = ?", (source_id,))
        _upsert_status(
            store,
            source_id,
            vod_id=vod_id,
            vod_start_sec=shift,
            channel_id=info.get("channel_id") or "",
            channel_login=info.get("channel_login") or "",
            status="running",
            progress=0.0,
            message_count=0,
            error="",
            finished_at=None,
        )
        channel_id = info.get("channel_id") or ""
        try:
            emotes = client.third_party_emotes(channel_id)
        except Exception:  # noqa: BLE001
            emotes = {}
        try:
            badges = client.badges(channel_id)
        except Exception:  # noqa: BLE001
            badges = {}
        _upsert_status(
            store,
            source_id,
            emotes_json=json.dumps(emotes, ensure_ascii=False),
            badges_json=json.dumps(badges, ensure_ascii=False),
        )
        total = _crawl(store, source_id, client, vod_id, start, end, shift, workers)
        _upsert_status(
            store,
            source_id,
            status="succeeded",
            progress=1.0,
            message_count=total,
            finished_at=time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
        )
    except Exception as exc:  # noqa: BLE001 - ошибка — часть статуса импорта
        try:
            store.get_source(source_id)
        except KeyError:
            raise  # источник удалили посреди импорта — писать некуда
        _upsert_status(store, source_id, status="failed", error=str(exc)[:500])
    finally:
        if own_client:
            try:
                client.close()  # type: ignore[union-attr]
            except Exception:  # noqa: BLE001
                pass
    return get_chat_status(store, source_id)


def _crawl(store, source_id: int, client: ChatClient, vod_id: str, start: float, end: float, shift: float, workers: int | None) -> int:
    """Параллельный обход по времени: N потоков, каждый — свой отрезок VOD."""
    n = max(1, min(int(workers or _env_int("TWITCH_CHAT_WORKERS", 4)), 8))
    span = end - start
    if span < 600:
        n = 1
    edges = [start + span * k / n for k in range(n + 1)]
    covered = [0.0] * n
    errors: list[BaseException] = []
    lock = threading.Lock()
    last_report = [0.0]

    def report() -> None:
        now = time.monotonic()
        with lock:
            if now - last_report[0] < 2.0:
                return
            last_report[0] = now
            done = sum(covered) / span if span > 0 else 0.0
        count_row = _db(store).query_one("SELECT COUNT(*) AS n FROM chat_messages WHERE source_id = ?", (source_id,))
        _upsert_status(store, source_id, progress=round(min(0.99, done), 4), message_count=int(count_row["n"]))

    def run(k: int, a: float, b: float) -> None:
        try:
            offset = int(math.floor(a))
            prev_ids: set[str] = set()
            while offset < b:
                nodes, has_next = client.comments_page(vod_id, offset)
                if not nodes:
                    break
                rows = [r for r in (parse_comment(node) for node in nodes) if r and start <= r["offset_sec"] <= end]
                insert_messages(store, source_id, rows, shift_sec=shift)
                if not has_next:
                    break
                ids = {str(node.get("id") or "") for node in nodes}
                newest = int(max(float(node.get("contentOffsetSeconds") or 0) for node in nodes))
                # Следующая страница — с последней секунды этой (дубли отсечёт
                # уникальный индекс); ничего нового или стоим на месте — шаг +1 с.
                offset = newest if (ids - prev_ids and newest > offset) else offset + 1
                prev_ids = ids
                with lock:
                    covered[k] = min(b, max(a, offset)) - a
                report()
            with lock:
                covered[k] = b - a
        except BaseException as exc:  # noqa: BLE001 - поднимем в основном потоке
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(k, edges[k], edges[k + 1]), daemon=True) for k in range(n)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    if errors:
        raise errors[0]
    row = _db(store).query_one("SELECT COUNT(*) AS n FROM chat_messages WHERE source_id = ?", (source_id,))
    return int(row["n"]) if row else 0


class ChatImportService:
    """Фоновые импорты (по потоку на источник). Повторный запуск того же
    источника, пока идёт прежний, просто возвращает текущий статус."""

    def __init__(self, get_store: Callable[[], Any], client_factory: Callable[[], ChatClient] | None = None) -> None:
        self.get_store = get_store
        self.client_factory = client_factory

    def start(self, source_id: int, *, vod: str = "", vod_start_sec: float | None = None, wait: bool = False) -> dict:
        store = self.get_store()
        source = store.get_source(source_id)  # KeyError → 404
        # Проверим ссылку сразу, чтобы ошибка пришла в ответе, а не в статусе.
        if not parse_twitch_vod_ref(vod) and not parse_twitch_vod_ref(source.get("original_url") or ""):
            raise ValueError("у источника нет ссылки на Twitch VOD — передайте vod (ссылку или id)")
        with _ACTIVE_LOCK:
            if source_id in _ACTIVE:
                return get_chat_status(store, source_id)
            _ACTIVE.add(source_id)
        _upsert_status(store, source_id, status="queued", error="", progress=0.0)

        def job() -> None:
            try:
                client = self.client_factory() if self.client_factory else None
                import_chat(store, source_id, client=client, vod=vod, vod_start_sec=vod_start_sec)
            except Exception:  # noqa: BLE001 - статус уже записан (или источника нет)
                pass
            finally:
                with _ACTIVE_LOCK:
                    _ACTIVE.discard(source_id)

        if wait:
            job()
        else:
            threading.Thread(target=job, name=f"chat-import-{source_id}", daemon=True).start()
        return get_chat_status(store, source_id)

    def auto_import(self, source: dict) -> None:
        """Хук ``SourceIngestor.on_source_created``: Twitch-источник → чат в фоне."""
        if not source or source.get("status") == "failed":
            return
        if not _env_flag("TWITCH_CHAT_AUTO_IMPORT", True):
            return
        from app import modules

        if not modules.is_enabled("twitch_chat", self.get_store()):  # «Настройки → Модули»
            return
        if not parse_twitch_vod_ref(source.get("original_url") or ""):
            return
        try:
            if not bool(self.get_store().get_app_setting_value("chat_auto_import", True)):
                return
            self.start(int(source["id"]))
        except Exception:  # noqa: BLE001 - чат не должен ломать загрузку источника
            pass
