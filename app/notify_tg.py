"""Карточки автопилота владельцу в Telegram — свой бот сервиса, отдельный и отключаемый.

Это НЕ бот-хаб switchboard и не ``~/.claude/tg``: у сервиса свой бот (токен от
@BotFather), своя группа/чат. Настройки — «Настройки → Telegram» (хранятся в
``app_settings`` зашифрованными, как прокси и cookies), запасной вариант — env
``AUTOPILOT_TG_TOKEN`` / ``AUTOPILOT_TG_CHAT_ID`` / ``AUTOPILOT_TG_THREAD_ID``.
``AUTOPILOT_TG_ENABLED=0`` — выключить адаптер целиком. Токена нет — карточки только
в UI, без ошибок.

Что делает:

* карточка одобрения: превью (клип ≤ 50 МБ — видео, иначе обложка/кадр, иначе ссылка
  в UI сервиса), название, описание, кнопки (inline keyboard ``vvf:<id>:<action>``);
* принимает нажатия кнопок и текстовые ответы (reply) на карточку — решение и урок
  проекту (app/autopilot.py); «Переделать» кнопкой — бот просит ответить, что именно;
* тема (forum topic) на проект — чат с агентом проекта (app/project_chat.py); у группы
  без тем — тред по reply на стартовое сообщение проекта;
* сводки и заметки («Опубликовано: ссылка»).

Ответы — long-polling ``getUpdates`` в фоне (``AutopilotWorker``): вебхук сервису за
авторизацией и без публичного адреса не подходит. Поэтому боту нужен СВОЙ токен:
два потребителя getUpdates одного бота мешают друг другу. Принимаются только
сообщения из настроенного чата (и, если задано, от разрешённых user id).
"""

from __future__ import annotations

import html
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

from app import autopilot as A
from app import projects as P

_log = logging.getLogger(__name__)

KEY = "telegram_bot"
OFFSET_KEY = "telegram_offset"
API = "https://api.telegram.org"
POLL_TIMEOUT = 20
VIDEO_LIMIT = 50 * 1024 * 1024  # Bot API: sendVideo до 50 МБ
CAPTION_LIMIT = 1024
TEXT_LIMIT = 4096
MAX_ATTEMPTS = 3


# --------------------------------------------------------------------------- #
# Настройки
# --------------------------------------------------------------------------- #


def _env_off() -> bool:
    return os.getenv("AUTOPILOT_TG_ENABLED", "1").strip().lower() in ("0", "false", "no", "off")


def load(store: Any) -> dict:
    try:
        value = store.get_app_setting_value(KEY, None, include_secret=True)
    except Exception:  # noqa: BLE001 - нет ключа/битая запись — как будто не задано
        return {}
    return value if isinstance(value, dict) else {}


def config(store: Any) -> dict:
    """Действующие настройки (с секретом): сервис, иначе env."""
    saved = load(store)
    token = str(saved.get("token") or "").strip()
    source = "service" if token else ""
    if not token and os.getenv("AUTOPILOT_TG_TOKEN", "").strip():
        token, source = os.getenv("AUTOPILOT_TG_TOKEN", "").strip(), "env"
    chat_id = str(saved.get("chat_id") or os.getenv("AUTOPILOT_TG_CHAT_ID", "")).strip()
    thread = saved.get("thread_id") if saved.get("thread_id") not in (None, "") else os.getenv("AUTOPILOT_TG_THREAD_ID", "")
    return {
        "token": token, "source": source, "chat_id": chat_id,
        "thread_id": int(thread) if str(thread or "").strip().lstrip("-").isdigit() else None,
        "enabled": bool(saved.get("enabled", True)), "use_proxy": bool(saved.get("use_proxy", False)),
        "public_url": str(saved.get("public_url") or os.getenv("AUTOPILOT_PUBLIC_URL", "")).strip().rstrip("/"),
        "allowed_user_ids": [str(u) for u in saved.get("allowed_user_ids") or []],
        "last_error": str(saved.get("last_error") or ""),
    }


def is_enabled(store: Any) -> bool:
    if _env_off():
        return False
    cfg = config(store)
    return bool(cfg["token"] and cfg["chat_id"] and cfg["enabled"])


def masked_token(token: str) -> str:
    if not token:
        return ""
    head = token.split(":", 1)[0]
    return f"{head}:•••" if ":" in token else "•••"


def status(store: Any) -> dict:
    """Для экрана настроек — без секрета."""
    cfg = config(store)
    return {
        "configured": bool(cfg["token"] and cfg["chat_id"]), "enabled": is_enabled(store),
        "env_disabled": _env_off(), "source": cfg["source"], "token": masked_token(cfg["token"]),
        "chat_id": cfg["chat_id"], "thread_id": cfg["thread_id"], "use_proxy": cfg["use_proxy"],
        "public_url": cfg["public_url"], "allowed_user_ids": cfg["allowed_user_ids"],
        "last_error": cfg["last_error"],
    }


def save(store: Any, *, token: str | None = None, chat_id: Any = None, thread_id: Any = None,
         enabled: bool | None = None, use_proxy: bool | None = None, public_url: str | None = None,
         allowed_user_ids: Any = None) -> dict:
    current = load(store)
    if token is not None and token.strip():
        if ":" not in token.strip():
            raise ValueError("токен бота — вида 123456789:AA… (от @BotFather)")
        current["token"] = token.strip()
    if chat_id is not None:
        text = str(chat_id).strip()
        if text and not (text.lstrip("-").isdigit() or text.startswith("@")):
            raise ValueError("chat_id — число (у групп с минусом, -100…) или @username канала")
        current["chat_id"] = text
    if thread_id is not None:
        text = str(thread_id).strip()
        if text and not text.isdigit():
            raise ValueError("thread_id — номер темы (число) или пусто")
        current["thread_id"] = int(text) if text else None
    if enabled is not None:
        current["enabled"] = bool(enabled)
    if use_proxy is not None:
        current["use_proxy"] = bool(use_proxy)
    if public_url is not None:
        current["public_url"] = public_url.strip().rstrip("/")
    if allowed_user_ids is not None:
        items = allowed_user_ids if isinstance(allowed_user_ids, list) else str(allowed_user_ids).replace(",", " ").split()
        current["allowed_user_ids"] = [str(u).strip() for u in items if str(u).strip().isdigit()]
    current["last_error"] = ""
    store.set_app_setting(KEY, current, encrypted=True)
    return status(store)


def clear(store: Any) -> dict:
    store.set_app_setting(KEY, {}, encrypted=True)
    return status(store)


def _remember_error(store: Any, error: str) -> None:
    current = load(store)
    if current:
        current["last_error"] = error[:300]
        store.set_app_setting(KEY, current, encrypted=True)


# --------------------------------------------------------------------------- #
# Клиент Bot API
# --------------------------------------------------------------------------- #


class TelegramError(RuntimeError):
    pass


class TelegramClient:
    """Тонкая обёртка над Bot API (httpx). Тесты подменяют ``notify_tg.TelegramClient``."""

    def __init__(self, token: str, proxy: str = "") -> None:
        self.token = token
        self.proxy = proxy

    def call(self, method: str, data: dict | None = None, files: dict | None = None, timeout: float = 30) -> Any:
        import httpx

        url = f"{API}/bot{self.token}/{method}"
        payload = {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
                   for k, v in (data or {}).items() if v is not None}
        try:
            with httpx.Client(proxy=self.proxy or None, timeout=timeout + 10) as http:
                if files:
                    response = http.post(url, data=payload, files=files)
                else:
                    response = http.post(url, data=payload)
            body = response.json()
        except Exception as exc:  # noqa: BLE001 - токен в URL не светим
            raise TelegramError(f"Telegram недоступен: {type(exc).__name__}") from exc
        if not body.get("ok"):
            raise TelegramError(str(body.get("description") or f"ошибка {response.status_code}")[:300])
        return body.get("result")


def client(store: Any) -> TelegramClient | None:
    if not is_enabled(store):
        return None
    cfg = config(store)
    proxy = ""
    if cfg["use_proxy"]:
        from app import proxy_config

        proxy = proxy_config.download_proxy()
    return TelegramClient(cfg["token"], proxy)


def _remember(store: Any, chat_id: Any, message: Any, approval_id: int | None = None,
              project_id: int | None = None, purpose: str = "card") -> int | None:
    message_id = (message or {}).get("message_id") if isinstance(message, dict) else None
    if message_id:
        store.db.execute(
            "INSERT OR REPLACE INTO tg_messages (chat_id, message_id, approval_id, project_id, purpose) "
            "VALUES (?, ?, ?, ?, ?)", (str(chat_id), int(message_id), approval_id, project_id, purpose))
    return int(message_id) if message_id else None


def _thread_for(store: Any, project_id: int | None) -> int | None:
    if project_id:
        row = store.db.query_one("SELECT tg_thread_id FROM project_autopilot WHERE project_id = ?", (int(project_id),))
        if row and row["tg_thread_id"]:
            return int(row["tg_thread_id"])
    return config(store)["thread_id"]


def send_text(store: Any, text: str, project_id: int | None = None, approval_id: int | None = None,
              purpose: str = "note", reply_to: int | None = None, keyboard: list | None = None,
              force_reply: bool = False, thread_id: int | None = None) -> int | None:
    """Сообщение в чат сервиса (в тему проекта, если она есть). Не настроено — None."""
    tg = client(store)
    if tg is None:
        return None
    cfg = config(store)
    markup: dict | None = None
    if keyboard:
        markup = {"inline_keyboard": keyboard}
    elif force_reply:
        markup = {"force_reply": True, "selective": True}
    data = {"chat_id": cfg["chat_id"], "text": text[:TEXT_LIMIT], "message_thread_id": thread_id or
            _thread_for(store, project_id), "reply_markup": markup, "disable_web_page_preview": True}
    if reply_to:
        data["reply_parameters"] = {"message_id": int(reply_to), "allow_sending_without_reply": True}
    try:
        message = tg.call("sendMessage", data)
    except TelegramError as exc:
        _remember_error(store, str(exc))
        _log.info("telegram sendMessage failed: %s", exc)
        return None
    return _remember(store, cfg["chat_id"], message, approval_id, project_id, purpose)


# --------------------------------------------------------------------------- #
# Карточки
# --------------------------------------------------------------------------- #


def keyboard_for(approval: dict) -> list:
    return [[{"text": label, "callback_data": f"vvf:{approval['id']}:{action}"}]
            for action, label in A.buttons_for(approval)]


def card_text(store: Any, approval: dict, limit: int = TEXT_LIMIT) -> str:
    esc = html.escape
    head = f"#{approval['id']} · {esc(approval.get('project_name') or '')} · {esc(approval['kind_label'])}"
    lines = [f"<i>{head}</i>"]
    if approval["kind"] == "clip_publish":
        lines.append(f"🎬 <b>{esc(approval['title'])}</b>")
        if approval["description"]:
            lines.append(esc(approval["description"][:700]))
        if approval["tags"]:
            lines.append("🏷 " + esc(" ".join(f"#{t}" for t in approval["tags"])))
        where = ", ".join(A.PLATFORM_LABELS.get(p, p) for p in approval["platforms"]) or "площадки по умолчанию"
        when = approval["proposed_local"] or "ближайший слот"
        lines.append(f"📡 {esc(where)} · 🕒 {esc(when)}")
    else:
        lines.append(esc(approval["question"] or approval["title"]))
        quote = (approval.get("payload") or {}).get("quote")
        if quote:
            lines.append(f"💬 «{esc(quote[:500])}»")
    base = config(store)["public_url"]
    if base:
        lines.append(f'<a href="{esc(base)}/approvals?id={approval["id"]}">Открыть в сервисе</a>')
    if approval["kind"] == "clip_publish":
        lines.append("<i>Ответь на карточку текстом — что переделать или что запомнить.</i>")
    text = "\n".join(lines)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _frame_of(clip_path: Path) -> Path | None:
    """Кадр клипа для превью большого файла (ffmpeg, 1-я секунда)."""
    from app.settings import settings

    out = settings.tmp_dir / f"tg-frame-{clip_path.stem}.jpg"
    if out.exists():
        return out
    try:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "1", "-i", str(clip_path), "-frames:v", "1",
                        "-vf", "scale=540:-2", str(out)], check=True, timeout=60, capture_output=True)
    except Exception:  # noqa: BLE001 - без кадра — карточка текстом со ссылкой
        return None
    return out if out.exists() else None


def send_card(store: Any, approval_id: int) -> dict:
    """Отправить карточку. Ошибка — tg_state=failed с причиной (фон повторит до MAX_ATTEMPTS)."""
    tg = client(store)
    if tg is None:
        return {"ok": False, "error": "Telegram не настроен"}
    approval = A.get_approval(store, approval_id)
    cfg = config(store)
    raw = store.db.query_one("SELECT cover_path FROM approvals WHERE id = ?", (int(approval_id),))
    base = {"chat_id": cfg["chat_id"], "message_thread_id": _thread_for(store, approval["project_id"]),
            "parse_mode": "HTML", "reply_markup": {"inline_keyboard": keyboard_for(approval)}}
    clip_path = None
    if approval.get("clip_id"):
        try:
            clip_path = Path(store.get_clip(int(approval["clip_id"]))["output_path"])
        except KeyError:
            clip_path = None
    try:
        if clip_path and clip_path.is_file() and clip_path.stat().st_size <= VIDEO_LIMIT:
            with clip_path.open("rb") as fh:
                message = tg.call("sendVideo", {**base, "caption": card_text(store, approval, CAPTION_LIMIT),
                                                "supports_streaming": True},
                                  files={"video": (clip_path.name, fh, "video/mp4")}, timeout=300)
        else:
            photo = Path(raw["cover_path"]) if raw and raw["cover_path"] else None
            if (not photo or not photo.is_file()) and approval.get("clip_id"):
                # Обложку выбрали уже после отправки карточки — берём текущую обложку клипа.
                current = store.get_clip_cover_path(int(approval["clip_id"]))
                photo = Path(current) if current else photo
            if (not photo or not photo.is_file()) and clip_path and clip_path.is_file():
                photo = _frame_of(clip_path)
            if photo and photo.is_file():
                with photo.open("rb") as fh:
                    message = tg.call("sendPhoto", {**base, "caption": card_text(store, approval, CAPTION_LIMIT)},
                                      files={"photo": (photo.name, fh, "image/jpeg")}, timeout=120)
            else:
                message = tg.call("sendMessage", {**base, "text": card_text(store, approval),
                                                  "disable_web_page_preview": True})
    except (TelegramError, OSError) as exc:
        attempts = int(store.db.query_one("SELECT tg_attempts FROM approvals WHERE id = ?",
                                          (int(approval_id),))["tg_attempts"]) + 1
        A._update(store, approval_id, tg_state="failed" if attempts >= MAX_ATTEMPTS else "queued",
                  tg_attempts=attempts, tg_error=str(exc)[:300])
        _remember_error(store, str(exc))
        return {"ok": False, "error": str(exc)}
    _remember(store, cfg["chat_id"], message, int(approval_id), approval["project_id"], "card")
    A._update(store, approval_id, tg_state="sent", tg_error="")
    A.log_event(store, approval_id, approval["project_id"], "card_sent", "telegram", "")
    return {"ok": True, "message_id": (message or {}).get("message_id")}


def send_queued_cards(store: Any, limit: int = 10) -> int:
    rows = store.db.query_all("SELECT id FROM approvals WHERE tg_state = 'queued' AND status = 'pending' "
                              "ORDER BY id LIMIT ?", (int(limit),))
    return sum(1 for row in rows if send_card(store, int(row["id"])).get("ok"))


def queue_card(store: Any, approval_id: int) -> dict:
    """«Отправить в Telegram» из UI (карточка без отправки, или отправка не удалась)."""
    if not is_enabled(store):
        raise ValueError("Telegram не настроен — Настройки → Telegram")
    A._update(store, approval_id, tg_state="queued", tg_attempts=0, tg_error="")
    return send_card(store, approval_id)


# --------------------------------------------------------------------------- #
# Темы проектов (чат с агентом проекта)
# --------------------------------------------------------------------------- #


def create_project_topic(store: Any, project_id: int) -> dict:
    """Тема (forum topic) проекта в группе бота. Группа без тем — стартовое сообщение,
    ответы (reply) на сообщения бота проекта идут в чат проекта."""
    tg = client(store)
    if tg is None:
        raise ValueError("Telegram не настроен — Настройки → Telegram")
    project = P.get_project(store, project_id, include_sources=False)
    cfg = config(store)
    intro = (f"💬 Чат проекта «{project['name']}». Пиши сюда — отвечает агент проекта: бриф, правила, "
             "статистика, очередь одобрения. Карточки одобрения проекта тоже приходят сюда.")
    try:
        topic = tg.call("createForumTopic", {"chat_id": cfg["chat_id"], "name": project["name"][:128]})
        thread_id = int(topic["message_thread_id"])
        A.set_state(store, project_id, tg_thread_id=thread_id)
        message = tg.call("sendMessage", {"chat_id": cfg["chat_id"], "message_thread_id": thread_id, "text": intro})
        _remember(store, cfg["chat_id"], message, None, project_id, "chat")
        return {"ok": True, "mode": "topic", "thread_id": thread_id}
    except TelegramError as exc:
        # Нет тем у группы / у бота нет права «управлять темами» — тред по reply.
        note = str(exc)
        message = tg.call("sendMessage", {"chat_id": cfg["chat_id"], "message_thread_id": cfg["thread_id"],
                                          "text": intro + "\n\nОтвечай на это сообщение (reply) — так я пойму, "
                                                          "что это про этот проект."})
        message_id = _remember(store, cfg["chat_id"], message, None, project_id, "chat")
        A.set_state(store, project_id, tg_root_message_id=message_id)
        return {"ok": True, "mode": "reply", "root_message_id": message_id, "note": f"тему создать не вышло: {note}"}


# --------------------------------------------------------------------------- #
# Входящие
# --------------------------------------------------------------------------- #


def _lookup(store: Any, chat_id: Any, message_id: Any) -> dict | None:
    if not message_id:
        return None
    row = store.db.query_one("SELECT * FROM tg_messages WHERE chat_id = ? AND message_id = ?",
                             (str(chat_id), int(message_id)))
    return dict(row) if row else None


def _project_by_thread(store: Any, thread_id: Any) -> int | None:
    if not thread_id:
        return None
    row = store.db.query_one("SELECT project_id FROM project_autopilot WHERE tg_thread_id = ?", (int(thread_id),))
    return int(row["project_id"]) if row else None


def _allowed(cfg: dict, chat: dict, user: dict) -> bool:
    if str(chat.get("id")) != str(cfg["chat_id"]) and f"@{chat.get('username') or ''}" != cfg["chat_id"]:
        return False
    if cfg["allowed_user_ids"] and str(user.get("id")) not in cfg["allowed_user_ids"]:
        return False
    return not user.get("is_bot")


def _actor(user: dict) -> str:
    return "tg:" + (f"@{user['username']}" if user.get("username") else str(user.get("id") or ""))


def poll_once(store: Any, timeout: int = POLL_TIMEOUT) -> int:
    """Один long-poll getUpdates; разобранные обновления подтверждаются offset."""
    tg = client(store)
    if tg is None:
        return 0
    offset = int(store.get_app_setting_value(OFFSET_KEY, 0) or 0)
    updates = tg.call("getUpdates", {"offset": offset or None, "timeout": timeout,
                                     "allowed_updates": ["message", "callback_query"]}, timeout=timeout)
    for update in updates or []:
        try:
            handle_update(store, update, tg)
        except Exception as exc:  # noqa: BLE001 - одно кривое обновление не стопорит очередь
            _log.warning("telegram update %s failed: %s", update.get("update_id"), exc)
        store.set_app_setting(OFFSET_KEY, int(update["update_id"]) + 1)
    return len(updates or [])


def handle_update(store: Any, update: dict, tg: TelegramClient | None = None) -> dict:
    tg = tg or client(store)
    cfg = config(store)
    if tg is None:
        return {"ignored": "not configured"}
    if update.get("callback_query"):
        return _handle_callback(store, tg, cfg, update["callback_query"])
    message = update.get("message") or {}
    text = str(message.get("text") or message.get("caption") or "").strip()
    if not text or not _allowed(cfg, message.get("chat") or {}, message.get("from") or {}):
        return {"ignored": "foreign or empty"}
    chat_id = message["chat"]["id"]
    actor = _actor(message.get("from") or {})
    replied = (message.get("reply_to_message") or {}).get("message_id")
    mapped = _lookup(store, chat_id, replied)
    if mapped and mapped.get("approval_id"):
        return _handle_card_reply(store, int(mapped["approval_id"]), text, actor, message["message_id"])
    project_id = (mapped or {}).get("project_id") or _project_by_thread(store, message.get("message_thread_id"))
    if project_id:
        from app import project_chat

        return project_chat.handle_telegram(store, int(project_id), text, actor, message["message_id"])
    # Свободный текст в общем треде: урок проекту последней карточки (за двое суток).
    row = store.db.query_one("SELECT project_id FROM tg_messages WHERE chat_id = ? AND project_id IS NOT NULL "
                             "AND created_at >= datetime('now', '-2 days') ORDER BY created_at DESC, message_id DESC "
                             "LIMIT 1", (str(chat_id),))
    if not row:
        send_text(store, "Не понял, к какому проекту это. Ответь (reply) на карточку или пиши в тему проекта.",
                  reply_to=message["message_id"], thread_id=message.get("message_thread_id"))
        return {"ignored": "no project"}
    learned = A.learn_from_text(store, int(row["project_id"]), text, origin="tg",
                                origin_ref=f"tg:{message['message_id']}")
    reply = A.describe_learning(learned) or "Принял к сведению."
    send_text(store, reply, project_id=int(row["project_id"]), reply_to=message["message_id"],
              thread_id=message.get("message_thread_id"))
    return {"learned": learned}


def _handle_callback(store: Any, tg: TelegramClient, cfg: dict, query: dict) -> dict:
    message = query.get("message") or {}
    data = str(query.get("data") or "")
    if not data.startswith("vvf:") or not _allowed(cfg, message.get("chat") or {}, query.get("from") or {}):
        return {"ignored": "foreign callback"}
    try:
        _prefix, approval_id, action = data.split(":", 2)
        approval_id = int(approval_id)
    except ValueError:
        return {"ignored": "bad data"}
    actor = _actor(query.get("from") or {})
    try:
        result = A.decide(store, approval_id, action, actor=actor)
        answer = {"approved": "Принято", "rejected": "В мусор", "changes_requested": "Что переделать?"}[result["status"]]
    except (ValueError, KeyError) as exc:
        result, answer = {"ok": False, "error": str(exc)}, str(exc)[:190]
    try:
        tg.call("answerCallbackQuery", {"callback_query_id": query.get("id"), "text": answer})
        if result.get("ok"):
            tg.call("editMessageReplyMarkup", {"chat_id": message["chat"]["id"], "message_id": message["message_id"],
                                               "reply_markup": {"inline_keyboard": []}})
    except TelegramError as exc:
        _log.info("telegram callback reply failed: %s", exc)
    if result.get("ok"):
        approval = result["approval"]
        if result["status"] == "changes_requested":
            send_text(store, f"Что переделать в «{approval['title']}»? Ответь на это сообщение.",
                      approval_id=approval_id, project_id=approval["project_id"], purpose="comment",
                      reply_to=message.get("message_id"), force_reply=True,
                      thread_id=message.get("message_thread_id"))
        else:
            send_text(store, decision_note(result), approval_id=approval_id, project_id=approval["project_id"],
                      reply_to=message.get("message_id"), thread_id=message.get("message_thread_id"))
    return result


def _handle_card_reply(store: Any, approval_id: int, text: str, actor: str, message_id: int) -> dict:
    """Текст в ответ на карточку: «да»/«нет» — решение; иначе для клипа — «Переделать» с
    этим комментарием (и урок проекту), для вопроса — «нет» + урок. Решённая — комментарий."""
    approval = A.get_approval(store, approval_id)
    if approval["status"] == "pending":
        cls = A.classify_reply(text)
        if cls["intent"] == "approve" and not cls.get("praise"):
            result = A.decide(store, approval_id, "approve", actor=actor)
        elif cls["intent"] == "reject":
            result = A.decide(store, approval_id, "reject", comment=text, actor=actor)
        elif approval["kind"] == "clip_publish":
            action = "approve" if cls.get("praise") else "changes"
            result = A.decide(store, approval_id, action, comment=text, actor=actor)
        else:
            result = A.decide(store, approval_id, "reject", comment=text, actor=actor)
            result["learned"] = A.learn_from_text(store, approval["project_id"], text, origin="tg",
                                                  origin_ref=f"approval#{approval_id}")
    else:
        result = A.add_comment(store, approval_id, text, actor=actor)
    send_text(store, decision_note(result), approval_id=approval_id, project_id=approval["project_id"],
              reply_to=message_id)
    return result


def decision_note(result: dict) -> str:
    """Короткий итог решения для ответа в чат."""
    approval = result.get("approval") or {}
    parts = []
    status = result.get("status") or approval.get("status")
    if status == "approved" and approval.get("kind") == "clip_publish":
        placement = result.get("placement") or {}
        if placement.get("ok"):
            parts.append(f"✅ В расписании: {placement['scheduled_local']} ({approval.get('timezone')}).")
        else:
            parts.append(f"✅ Одобрено, но не поставлено: {placement.get('error') or approval.get('publish_error')}")
    elif status == "approved":
        rule = result.get("rule") or {}
        if rule.get("created"):
            parts.append(f"📝 Записал правило #{rule['rule']['id']}: «{rule['rule']['text']}».")
        elif rule.get("revoked"):
            parts.append(f"🗑 Правило #{rule['rule']['id']} отменено, осталось в истории.")
        elif rule.get("needs_confirmation"):
            parts.append(f"⚠️ {rule.get('question')}")
        elif rule.get("duplicate_of"):
            parts.append(f"Такое правило уже есть: #{rule['duplicate_of']['id']}.")
        elif result.get("rule_kept"):
            parts.append("Оставил правило, пометку «на пересмотр» снял.")
        else:
            parts.append("✅ Принято.")
    elif status == "rejected":
        parts.append("🗑 В мусор.")
    elif status == "changes_requested":
        parts.append("✏️ Переделаю.")
    else:
        parts.append("Записал комментарий.")
    learned = A.describe_learning(result.get("learned"))
    if learned:
        parts.append(learned)
    return " ".join(parts)


def test(store: Any) -> dict:
    """Проверка из настроек: getMe + тестовое сообщение в чат."""
    if _env_off():
        raise ValueError("адаптер выключен переменной AUTOPILOT_TG_ENABLED=0")
    cfg = config(store)
    if not cfg["token"] or not cfg["chat_id"]:
        raise ValueError("нужны токен бота и chat_id")
    proxy = ""
    if cfg["use_proxy"]:
        from app import proxy_config

        proxy = proxy_config.download_proxy()
    tg = TelegramClient(cfg["token"], proxy)
    try:
        me = tg.call("getMe")
        tg.call("sendMessage", {"chat_id": cfg["chat_id"], "message_thread_id": cfg["thread_id"],
                                "text": "✅ Vertical Video Fabric: бот на связи, карточки одобрения будут здесь."})
    except TelegramError as exc:
        _remember_error(store, str(exc))
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "bot": (me or {}).get("username") or ""}
