"""Ошибки публикации и заданий человеческими словами.

Сырой текст (ответ площадки, ffmpeg, помощника YouTube) остаётся в ``error`` как был — на экране он под
«подробнее». Рядом API отдаёт ``error_view``: что случилось простыми словами и что сделать.

``explain(raw, platform=…, account=…, status=…)`` → ``{"key", "text", "action", "to"}`` или ``None``, если
ошибка незнакомая (тогда экран показывает сырой текст, как раньше). На экране: «text — action»,
``to`` — куда ведёт действие (страница сервиса), может быть пустым.

Правила проверяются по порядку: частные раньше общих (канал YouTube раньше «нужны свежие cookie»,
обрыв на самом посте раньше «нет связи»). Новое правило — в ``_RULES``, тест — в tests/test_error_text.py.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

PLATFORM_NAMES = {"youtube": "YouTube", "tiktok": "TikTok", "instagram": "Instagram"}

ACCOUNTS_TO = "/pubs?tab=accounts"
PROXY_TO = "/settings/proxy"
COOKIES_ACTION = "обнови куки в «Публикации и аккаунты → Аккаунты»"

_PLATFORM_IN_TEXT = (
    ("youtube", re.compile(r"youtube|youtu\.be|youtubei|google", re.I)),
    ("tiktok", re.compile(r"tiktok", re.I)),
    ("instagram", re.compile(r"instagram|instagrapi", re.I)),
)


@dataclass(frozen=True)
class _Ctx:
    raw: str
    match: re.Match
    platform: str  # «YouTube» / «TikTok» / «Instagram» / ""
    account: str

    @property
    def acc(self) -> str:
        """« для аккаунта «…»» или пусто."""
        return f" для аккаунта «{self.account}»" if self.account else ""

    @property
    def where(self) -> str:
        return self.platform or "площадкой"


Text = str | Callable[[_Ctx], str]


@dataclass(frozen=True)
class _Rule:
    key: str
    pattern: re.Pattern
    text: Text
    action: Text
    to: str = ""


def _rule(key: str, pattern: str, text: Text, action: Text, to: str = "") -> _Rule:
    return _Rule(key, re.compile(pattern, re.I | re.S), text, action, to)


_NETWORK = (r"timed?[ -]?out|timeout|ReadTimeout|ConnectTimeout|ConnectError|Connection (?:closed|reset|refused|aborted)"
            r"|Нет связи с|Bad Gateway|(?:HTTP|status code|Error) 50[234]|service is currently unavailable"
            r"|Temporary failure in name resolution|Name or service not known|Network is unreachable"
            r"|TLS/SSL connection|SSLError|SSL: |EOF occurred|RemoteDisconnected|ECONNRESET|ETIMEDOUT|socket hang up")

_RULES: tuple[_Rule, ...] = (
    # --- YouTube: канал аккаунта (node/youtube-channel.mjs) ---
    # «(есть: ни одного)» — у входа нет ни одного канала: Google выкинул из аккаунта, куки мёртвые.
    _rule("youtube_logged_out", r"канал \S+ не найден у этого входа \(есть: ни одного\)",
          lambda c: f"Слетел вход в YouTube{c.acc}", COOKIES_ACTION, ACCOUNTS_TO),
    _rule("youtube_channel_missing", r"канал (\S+) не найден у этого входа",
          lambda c: f"У входа YouTube{c.acc} нет канала {c.match.group(1)}",
          "проверь в «Аккаунтах», какой канал выбран, или обнови куки того Google-аккаунта, где этот канал есть",
          ACCOUNTS_TO),
    _rule("youtube_wrong_channel", r"активен другой канал",
          lambda c: f"Ролик ушёл бы не на тот канал YouTube{c.acc}",
          "выбери нужный канал у аккаунта в «Аккаунтах» и повтори", ACCOUNTS_TO),
    _rule("youtube_channel_unset", r"канал YouTube не задан",
          lambda c: f"У аккаунта YouTube{c.acc} не выбран канал", "укажи канал в «Аккаунтах»", ACCOUNTS_TO),
    _rule("youtube_channel_check", r"не удалось проверить активный канал",
          lambda c: f"YouTube не дал проверить канал{c.acc}", "повтори позже; если повторяется — " + COOKIES_ACTION,
          ACCOUNTS_TO),

    # --- перезапуск сервиса и файл ролика (app/store_common.py) — уже по-русски, добавляем, что делать ---
    _rule("publish_interrupted", r"Публикация прервалась из-за перезапуска сервиса\. Видео могло уже выйти",
          "Публикация прервалась перезапуском сервиса — ролик мог уже выйти",
          "проверь на площадке и только потом повторяй"),
    _rule("publish_not_started", r"Публикация прервалась из-за перезапуска сервиса",
          "Публикация прервалась перезапуском сервиса, на площадку ничего не ушло", "нажми «Повторить»"),
    _rule("render_interrupted", r"Рендер прервался из-за перезапуска сервиса",
          "Рендер прервался перезапуском сервиса", "запусти рендер клипа ещё раз"),
    _rule("analysis_interrupted", r"analysis was interrupted by service restart",
          "Анализ прервался перезапуском сервиса", "запусти анализ записи заново"),
    _rule("publish_file_missing", r"Файла ролика больше нет", "Файла ролика больше нет, публикация не ушла",
          "поставь публикацию заново"),
    _rule("publish_file_changed", r"Файл ролика изменился после постановки",
          "Файл ролика поменялся, пока публикация ждала очереди", "поставь публикацию заново"),

    # --- обрыв на самом посте (app/providers/tiktok.py MAYBE_POSTED) ---
    _rule("maybe_posted", r"ролик мог выйти",
          lambda c: f"Связь оборвалась на самом посте — ролик мог выйти в {c.platform or 'TikTok'}",
          "проверь профиль на площадке, прежде чем повторять"),
    _rule("no_post_link", r"TikTok не вернул его id", "Ролик вышел, но TikTok не вернул ссылку",
          "найди ролик в профиле TikTok: статистики по нему в сервисе не будет"),

    # --- блокировки, лимиты, размер ---
    _rule("ip_blocked", r"IP address is blocked|IP.{0,20}(?:blocked|banned)|заблокирован.{0,20}IP",
          lambda c: f"{c.platform or 'Площадка'} не пускает с этого адреса (IP заблокирован)",
          "поставь аккаунту другой прокси в «Аккаунтах»", ACCOUNTS_TO),
    _rule("bot_check", r"confirm you.?re not a bot", "YouTube принял сервис за бота",
          COOKIES_ACTION + " или включи прокси", ACCOUNTS_TO),
    _rule("too_large", r"exceeds MAX_UPLOAD_BYTES|too large|Request Entity Too Large|(?:HTTP|status code) 413"
                       r"|слишком больш|file size limit|exceeds the maximum",
          "Файл слишком большой для загрузки", "сожми или укороти ролик и загрузи снова"),
    _rule("quota", r"quota|(?:HTTP|status code|Error) 429|\b429 Too Many|Too Many Requests|rate.?limit"
                   r"|uploadLimitExceeded|exceeded the number of videos|daily upload limit|слишком часто",
          lambda c: f"{c.platform or 'Площадка'} временно не даёт выкладывать: лимит загрузок",
          "подожди несколько часов и повтори; ставь ролики с промежутком"),

    # --- прокси и вход ---
    _rule("proxy_down", r"Нет связи через прокси|ProxyError|Tunnel connection failed|proxy.{0,40}(?:refused|failed|error)",
          lambda c: f"Не отвечает прокси{c.acc}",
          "проверь прокси аккаунта в «Аккаунтах» (или в Настройки → Прокси) и повтори", ACCOUNTS_TO),
    _rule("needs_reauth", r"Нужны свежие cookie|needs_reauth|sessionid cookie is missing"
                          r"|cookie.{0,30}(?:expired|missing|invalid|протух)|просит войти заново|отвечает без входа"
                          r"|выкинул из аккаунта|(?<!upload )auth failed|unauthorized|not logged in|login required|session expired"
                          r"|(?:HTTP|status code) 40[13]",
          lambda c: f"Слетел вход в {c.platform}{c.acc}" if c.platform else f"Слетел вход в аккаунт площадки{c.acc}",
          COOKIES_ACTION, ACCOUNTS_TO),

    # Подпись запросов TikTok — свой помощник сервиса; его «timed out» не про связь.
    _rule("tiktok_signature", r"TikTok signature", "Не сработала подпись запросов к TikTok",
          "повтори; если повторяется — это поломка сервиса, напиши в чат"),

    # --- связь ---
    _rule("network", _NETWORK, lambda c: f"Не было связи с {c.where}",
          "повтори позже; если повторяется — проверь интернет и прокси"),

    # --- сбои загрузки на стороне площадки или помощника ---
    _rule("tiktok_upload", r"TikTok (?:chunk upload|apply upload|upload finish|upload commit|upload auth|project create"
                           r"|publish) failed",
          "TikTok не принял загрузку ролика", "повтори позже; если повторяется — " + COOKIES_ACTION, ACCOUNTS_TO),
    _rule("youtube_helper", r"youtube upload failed|youtube helper returned invalid JSON|node executable not found",
          "Загрузка на YouTube сломалась внутри сервиса", "повтори; если повторяется — напиши в чат"),
    _rule("video_removed", r"removed by the uploader|Video unavailable|ролик чужой, удалён",
          "Ролик удалён с площадки или недоступен", "статистику по нему не собрать"),

    # --- Gemini и генерация ---
    _rule("gemini_location", r"User location is not supported", "Gemini не работает из этой страны",
          "включи прокси для Gemini в Настройки → Прокси", PROXY_TO),
    _rule("generator_down", r"Все провайдеры модели", "Сервис генерации сейчас не отвечает",
          "повтори позже или выбери другую модель"),
    _rule("moderation", r"отклонена модерацией", "Генерацию отклонила модерация", "измени промпт или картинки"),

    # --- ffmpeg: файл и рендер ---
    _rule("bad_file", r"Invalid data found when processing input|moov atom not found|could not find codec parameters"
                      r"|Invalid NAL unit|partial file",
          "Файл видео повреждён или не докачался", "скачай или загрузи запись заново"),
    _rule("render_transition", r"xfade.{0,200}do not match",
          "Не собрался переход между кусками: у них разная частота кадров",
          "убери или замени переход и отрендери снова"),
    _rule("render_concat", r"concat.{0,200}do not match", "Куски разного размера не склеились",
          "отрендери снова; если повторяется — напиши в чат клипа"),
    _rule("render_reframe", r"Missing '\)' or too many args", "Слишком сложное движение кадра",
          "упрости кадрирование: меньше точек фокуса"),
    _rule("render_audio_layout", r"Cannot select channel layout|громкость не выровнена",
          "Не вышло выровнять громкость: у звука необычные каналы",
          "отрендери снова; если повторяется — замени музыку или звук"),
    _rule("sfx_missing", r"sfx:\S+ не найден", "Не нашёлся звук эффекта — слой остался без звука",
          "замени звук в слое"),
    _rule("ffmpeg", r"ffmpeg|Error reinitializing filters|Failed to configure output pad|Conversion failed",
          "Сбой ffmpeg при сборке видео", "отрендери снова; если повторяется — напиши в чат клипа"),
)

# Статус площадки «нужен вход», а текст незнакомый — всё равно «слетел вход».
_REAUTH = next(r for r in _RULES if r.key == "needs_reauth")


def _platform(raw: str, platform: str) -> str:
    key = (platform or "").strip().lower()
    if key in PLATFORM_NAMES:
        return PLATFORM_NAMES[key]
    for name, pattern in _PLATFORM_IN_TEXT:
        if pattern.search(raw):
            return PLATFORM_NAMES[name]
    return ""


def _render(rule: _Rule, ctx: _Ctx) -> dict:
    text = rule.text(ctx) if callable(rule.text) else rule.text
    action = rule.action(ctx) if callable(rule.action) else rule.action
    return {"key": rule.key, "text": text, "action": action, "to": rule.to}


def explain(raw: str | None, *, platform: str = "", account: str = "", status: str = "") -> dict | None:
    """Ошибка простыми словами: ``{"key", "text", "action", "to"}``; незнакомая — ``None``."""
    raw = str(raw or "").strip()
    if not raw:
        return None
    for rule in _RULES:
        match = rule.pattern.search(raw)
        if match:
            return _render(rule, _Ctx(raw, match, _platform(raw, platform), (account or "").strip()))
    if status == "needs_reauth":
        return _render(_REAUTH, _Ctx(raw, re.match("", ""), _platform(raw, platform), (account or "").strip()))
    return None


def failing_target(targets: list[dict]) -> dict:
    """Площадка, из-за которой упало задание: первая неудачная с текстом ошибки (так и job.error берётся)."""
    return next((t for t in targets or [] if t.get("status") != "succeeded" and t.get("error")), {})


def explain_target(target: dict) -> dict | None:
    return explain(target.get("error"), platform=target.get("platform") or "",
                   account=target.get("account_label") or "", status=target.get("status") or "")


def explain_job(job: dict, targets: list[dict] | None = None) -> dict | None:
    """Ошибка задания публикации: площадка и аккаунт — от упавшей площадки."""
    target = failing_target(targets if targets is not None else job.get("targets") or [])
    return explain(job.get("error"), platform=target.get("platform") or "",
                   account=target.get("account_label") or "", status=job.get("status") or "")


def job_targets_context(store: Any, job_id: int | None) -> list[dict]:
    """Площадки задания с подписью аккаунта — для объяснения ошибки там, где задания под рукой нет."""
    if not job_id:
        return []
    try:
        rows = store.db.query_all(
            "SELECT jt.status, jt.error, jt.platform, a.label AS account_label FROM job_targets jt "
            "JOIN accounts a ON a.id = jt.account_id WHERE jt.job_id = ? ORDER BY jt.id", (int(job_id),))
    except Exception:  # noqa: BLE001 - объяснение не должно ронять ответ
        return []
    return [dict(r) for r in rows]
