"""Учёт трат Gemini: каждый запрос к модели — строка в ``gemini_usage`` с токенами и ценой.

Ключ Gemini платный, а сервис зовёт модель из десятков мест. Чтобы знать траты по запросу, по ходу агента, по
проекту, все запросы проходят через одно место:

* ``GeminiClient.generate_content`` / ``stream_generate_content`` (``app/ai/gemini.py``) обёрнуты в
  :func:`tracked` / :func:`tracked_stream` — любой новый вызов через клиент учитывается сам;
* озвучка Gemini TTS (``app/dubbing.py``) ходит мимо клиента и учитывается тем же :func:`tracked`.

Что за запрос, знает тот, кто его делает: :func:`scope` (контекст на поток) задаёт ``purpose`` и id —
``project_id``, ``source_id``, ``clip_id`` (план клипа), ``session`` (переписка ``chat:<id>``, анализ
``analysis:<id>``), ``message_id`` (ответ агента). Без scope ``purpose`` берётся из стека: модуль первой
публичной функции сервиса, которая позвала клиента (``publish_meta``, ``explainer_script``…); ``caller`` —
до трёх кадров стека, точное место вызова. Новый поток начинает без scope (contextvars не наследуются).

Цена считается сразу по таблице :data:`DEFAULT_PRICES` (поверх неё — правки владельца в настройке
``gemini_prices``) и курсу ``gemini_usd_rub``. Токены хранятся, так что после правки цен старые строки
пересчитывает :func:`recompute`. Нет ``usageMetadata`` — токены NULL, цена NULL, вызов не ломается.
Учёт никогда не роняет запрос: любая его ошибка — строчка в лог.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import sys
import time
from datetime import date, datetime, timezone
from typing import Any, Callable, Iterator

_log = logging.getLogger(__name__)

PRICES_KEY = "gemini_prices"
RATE_KEY = "gemini_usd_rub"
DEFAULT_USD_RUB = 82.0
# Цены сверены со страницей Google 12.11.2026 (ai.google.dev/gemini-api/docs/pricing → aistudio.google.com/docs/pricing),
# платный тариф Standard, USD за 1M токенов. thinking-токены оплачиваются как output («Output price (including
# thinking tokens)»). ``verify: True`` — модели на странице нет, цена взята у ближайшей: проверить.
PRICES_CHECKED = "2026-11-12"
PRICES_SOURCE = "https://aistudio.google.com/docs/pricing"

# Поля цены: input, cached (чтение кэша), output (текст и размышления), input_audio / cached_audio (аудио на входе),
# output_image / output_audio (картинка, звук на выходе). long_threshold + long — дороже, если промпт длиннее порога.
# changes — с какой даты цена меняется ({"from": "YYYY-MM-DD", ...новые поля}).
_FLASH_2027 = [{"from": "2027-01-01", "input": 1.50, "cached": 0.15, "output": 7.50}]
DEFAULT_PRICES: dict[str, dict] = {
    "gemini-3.1-pro-preview": {"input": 2.00, "cached": 0.20, "output": 12.00, "long_threshold": 200_000,
                               "long": {"input": 4.00, "cached": 0.40, "output": 18.00}},
    "gemini-3.8-flash": {"input": 0.75, "cached": 0.075, "output": 3.75, "changes": _FLASH_2027},
    "gemini-3.6-flash": {"input": 0.75, "cached": 0.075, "output": 3.75, "changes": _FLASH_2027},
    # Модель по умолчанию сервиса (GEMINI_VIDEO_MODEL/TEXT_MODEL), а на странице цен её нет: цена 3.6 Flash.
    "gemini-3.5-flash": {"input": 0.75, "cached": 0.075, "output": 3.75, "changes": _FLASH_2027, "verify": True},
    "gemini-3.5-flash-lite": {"input": 0.30, "cached": 0.03, "output": 2.50},
    "gemini-3.1-flash-lite": {"input": 0.25, "cached": 0.025, "output": 1.50, "input_audio": 0.50,
                              "cached_audio": 0.05},
    "gemini-3-flash-preview": {"input": 0.50, "cached": 0.05, "output": 3.00, "input_audio": 1.00,
                               "cached_audio": 0.10},
    "gemini-2.5-pro": {"input": 1.25, "cached": 0.125, "output": 10.00, "long_threshold": 200_000,
                       "long": {"input": 2.50, "cached": 0.25, "output": 15.00}},
    "gemini-2.5-flash": {"input": 0.30, "cached": 0.03, "output": 2.50, "input_audio": 1.00, "cached_audio": 0.10},
    "gemini-2.5-flash-lite": {"input": 0.10, "cached": 0.01, "output": 0.40, "input_audio": 0.30,
                              "cached_audio": 0.03},
    # Псевдоним: на странице его нет, gemini-2.5-flash с 01.12.2026 уходит на 3.5 Flash-Lite — её цена.
    "gemini-flash-lite-latest": {"input": 0.30, "cached": 0.03, "output": 2.50, "verify": True},
    "gemini-3.1-flash-image": {"input": 0.50, "output": 3.00, "output_image": 60.00},
    "gemini-3.1-flash-lite-image": {"input": 0.25, "output": 1.50, "output_image": 30.00},
    "gemini-3-pro-image": {"input": 2.00, "output": 12.00, "output_image": 120.00},
    "gemini-nano-banana-2.1": {"input": 1.50, "output": 7.50, "output_image": 30.00},
    # Картинка $0.039 ≈ $30 за 1M токенов картинки; цены текстового ответа на странице нет.
    "gemini-2.5-flash-image": {"input": 0.30, "output": 2.50, "output_image": 30.00, "verify": True},
    # Озвучка: на выходе аудио-токены. На бесплатном тарифе ключа — 0, на платном — эти цены.
    "gemini-3.8-flash-tts": {"input": 0.50, "output": 9.00, "output_audio": 9.00,
                             "changes": [{"from": "2027-01-01", "input": 1.00, "output": 18.00, "output_audio": 18.00}]},
    "gemini-3.8-flash-lite-tts": {"input": 0.50, "output": 6.00, "output_audio": 6.00,
                                  "changes": [{"from": "2027-01-01", "input": 1.00, "output": 12.00,
                                               "output_audio": 12.00}]},
    "gemini-3.1-flash-tts-preview": {"input": 1.00, "output": 20.00, "output_audio": 20.00},
    "gemini-2.5-flash-preview-tts": {"input": 0.50, "output": 10.00, "output_audio": 10.00},
    "gemini-2.5-pro-preview-tts": {"input": 1.00, "output": 20.00, "output_audio": 20.00},
}
# Незнакомая модель: цена семейства по слову в имени (порядок важен: lite раньше flash). Помечается fallback.
_FAMILY_FALLBACK = (("tts", "gemini-3.8-flash-tts"), ("image", "gemini-3.1-flash-image"),
                    ("flash-lite", "gemini-3.5-flash-lite"), ("pro", "gemini-3.1-pro-preview"),
                    ("flash", "gemini-3.8-flash"))
_PRICE_FIELDS = ("input", "cached", "output", "input_audio", "cached_audio", "output_image", "output_audio")

# --------------------------------------------------------------------------- #
# Схема
# --------------------------------------------------------------------------- #

SCHEMA_SQL = (
    """
    CREATE TABLE IF NOT EXISTS gemini_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        model TEXT NOT NULL DEFAULT '',
        model_version TEXT NOT NULL DEFAULT '',
        method TEXT NOT NULL DEFAULT 'generate',
        purpose TEXT NOT NULL DEFAULT '',
        caller TEXT NOT NULL DEFAULT '',
        project_id INTEGER,
        source_id INTEGER,
        clip_id INTEGER,
        session TEXT NOT NULL DEFAULT '',
        message_id INTEGER,
        prompt_tokens INTEGER,
        cached_tokens INTEGER,
        output_tokens INTEGER,
        thinking_tokens INTEGER,
        tool_prompt_tokens INTEGER,
        total_tokens INTEGER,
        usage_json TEXT NOT NULL DEFAULT '',
        duration_ms INTEGER,
        ok INTEGER NOT NULL DEFAULT 1,
        error TEXT NOT NULL DEFAULT '',
        price_key TEXT NOT NULL DEFAULT '',
        tier TEXT NOT NULL DEFAULT '',
        usd_rub REAL,
        cost_usd REAL,
        cost_rub REAL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_gemini_usage_time ON gemini_usage(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_gemini_usage_session ON gemini_usage(session, id)",
    "CREATE INDEX IF NOT EXISTS idx_gemini_usage_project ON gemini_usage(project_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_gemini_usage_purpose ON gemini_usage(purpose, created_at)",
)


def ensure_schema(conn: Any) -> None:
    for sql in SCHEMA_SQL:
        conn.execute(sql)


# --------------------------------------------------------------------------- #
# Хранилище
# --------------------------------------------------------------------------- #

_get_store: Callable[[], Any] | None = None


def bind_store(get_store: Callable[[], Any] | None) -> None:
    """Куда писать. Не задано — хранилище, подключённое в ``app.proxy_config.register`` (то же, что у сервиса)."""
    global _get_store
    _get_store = get_store


def _store() -> Any:
    if _get_store is not None:
        return _get_store()
    from app.proxy_config import registered_store

    return registered_store()


# --------------------------------------------------------------------------- #
# Контекст запроса
# --------------------------------------------------------------------------- #

_IDS = ("project_id", "source_id", "clip_id", "session", "message_id")
_SCOPE: contextvars.ContextVar[dict | None] = contextvars.ContextVar("gemini_usage_scope", default=None)


def _clean_ids(ids: dict) -> dict:
    unknown = set(ids) - set(_IDS)
    if unknown:
        raise TypeError(f"gemini_usage: неизвестные поля {sorted(unknown)}")
    return {k: v for k, v in ids.items() if v not in (None, "")}


@contextlib.contextmanager
def scope(purpose: str | None = None, *, default_purpose: str | None = None, **ids: Any) -> Iterator[dict]:
    """Всё, что Gemini посчитает внутри, — с этим ``purpose`` и id. Вложенный scope наследует id внешнего;
    ``purpose`` заменяет внешний, ``default_purpose`` — только если внешнего нет."""
    parent = _SCOPE.get() or {}
    current = {**parent, **_clean_ids(ids)}
    if purpose:
        current["purpose"] = purpose
    elif default_purpose and not parent.get("purpose"):
        current["purpose"] = default_purpose
    token = _SCOPE.set(current)
    try:
        yield current
    finally:
        _SCOPE.reset(token)


def annotate(**ids: Any) -> None:
    """Дописать id в текущий scope (id анализа известен только после записи в базу)."""
    current = _SCOPE.get()
    if current is not None:
        current.update(_clean_ids(ids))


def current_scope() -> dict:
    return dict(_SCOPE.get() or {})


# Кадры, которые не говорят, кто спросил: сам учёт, клиент и общий цикл агента.
_SKIP_MODULES = {__name__, "app.ai.agent_loop", "contextlib"}
_SKIP_FUNCS = {("app.ai.gemini", "generate_content"), ("app.ai.gemini", "stream_generate_content"),
               ("app.dubbing", "_generate")}
_PURPOSE_ALIASES = {
    "gemini": "analysis",  # GeminiVideoAnalyzer.analyze
    "service": "analysis",
    "explainer_gen": "explainer_script",
    "stories_gen": "stories_script",
    "speakers": "separate_voices",
    "subtitles_gemini": "subtitles",
    "project_chat": "agent_chat",
    "project_chat_context": "chat_summary",
    "clip_chat": "clip_chat",
    "editor_agent": "montage_agent",
}


def _slug(module: str) -> str:
    name = module[4:] if module.startswith("app.") else module
    name = name[3:] if name.startswith("ai.") else name
    slug = name.replace(".", "_")
    return _PURPOSE_ALIASES.get(slug, slug)


def _caller(depth: int = 2) -> tuple[str, str]:
    """(``purpose`` из стека, ``caller`` — до трёх кадров сервиса, ближний первым)."""
    try:
        frame = sys._getframe(depth)
    except ValueError:
        return "", ""
    app_frames: list[tuple[str, str]] = []
    other: tuple[str, str] | None = None
    seen = 0
    while frame is not None and seen < 40 and len(app_frames) < 6:
        seen += 1
        module = str(frame.f_globals.get("__name__") or "")
        func = frame.f_code.co_name
        frame = frame.f_back
        if module in _SKIP_MODULES or (module, func) in _SKIP_FUNCS:
            continue
        if module.startswith("app."):
            app_frames.append((module, func))
        elif other is None:
            other = (module, func)
    if not app_frames:
        return (_slug(other[0]), f"{other[0]}.{other[1]}") if other else ("", "")
    public = next(((m, f) for m, f in app_frames if not f.startswith("_") and f != "<lambda>"), app_frames[0])
    chain = " < ".join(f"{m[4:]}.{f}" for m, f in app_frames[:3])
    return _slug(public[0]), chain[:300]


# --------------------------------------------------------------------------- #
# Цены
# --------------------------------------------------------------------------- #


def _settings_value(store: Any, key: str, default: Any) -> Any:
    if store is None:
        return default
    try:
        return store.get_app_setting_value(key, default)
    except Exception:  # noqa: BLE001 - нет таблицы/хранилища — цены по умолчанию
        return default


def price_overrides(store: Any = None) -> dict:
    raw = _settings_value(store, PRICES_KEY, {})
    return raw if isinstance(raw, dict) else {}


def prices(store: Any = None) -> dict[str, dict]:
    """Таблица цен: умолчания + правки владельца (по модели поверх; ``null`` — убрать модель)."""
    table = {k: dict(v) for k, v in DEFAULT_PRICES.items()}
    for model, entry in price_overrides(store).items():
        key = str(model).strip()
        if not key:
            continue
        if entry is None:
            table.pop(key, None)
        elif isinstance(entry, dict):
            table[key] = {**table.get(key, {}), **entry}
    return table


def usd_rub(store: Any = None) -> float:
    try:
        value = float(_settings_value(store, RATE_KEY, DEFAULT_USD_RUB) or DEFAULT_USD_RUB)
    except (TypeError, ValueError):
        return DEFAULT_USD_RUB
    return value if value > 0 else DEFAULT_USD_RUB


def _normalize_entry(model: str, entry: Any) -> dict | None:
    if entry is None:
        return None
    if not isinstance(entry, dict):
        raise ValueError(f"цена {model}: нужен объект")
    clean: dict = {}
    for key, value in entry.items():
        if key in _PRICE_FIELDS or key == "long_threshold":
            try:
                number = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"цена {model}.{key}: нужно число") from exc
            if number < 0:
                raise ValueError(f"цена {model}.{key}: не меньше нуля")
            clean[key] = int(number) if key == "long_threshold" else number
        elif key == "long":
            clean["long"] = {k: float(v) for k, v in (value or {}).items() if k in _PRICE_FIELDS}
        elif key == "changes":
            changes = []
            for item in value or []:
                start = str((item or {}).get("from") or "")
                date.fromisoformat(start)
                changes.append({"from": start, **{k: float(v) for k, v in item.items() if k in _PRICE_FIELDS}})
            clean["changes"] = changes
        elif key in ("verify", "note"):
            clean[key] = value
        else:
            raise ValueError(f"цена {model}: неизвестное поле {key}")
    return clean


def set_prices(store: Any, overrides: dict | None = None, rate: float | None = None) -> dict:
    """Правки владельца: ``overrides`` целиком заменяет прежние правки, ``rate`` — курс ₽ за $."""
    if overrides is not None:
        if not isinstance(overrides, dict):
            raise ValueError("цены: нужен объект {модель: {input, cached, output, …}}")
        store.set_app_setting(PRICES_KEY, {str(k).strip(): _normalize_entry(str(k), v)
                                           for k, v in overrides.items() if str(k).strip()})
    if rate is not None:
        value = float(rate)
        if not 0 < value < 100_000:
            raise ValueError("курс: число больше нуля")
        store.set_app_setting(RATE_KEY, value)
    return price_config(store)


def price_config(store: Any = None) -> dict:
    return {"prices": prices(store), "defaults": DEFAULT_PRICES, "overrides": price_overrides(store),
            "usd_rub": usd_rub(store), "default_usd_rub": DEFAULT_USD_RUB, "checked_at": PRICES_CHECKED,
            "source": PRICES_SOURCE, "units": "USD за 1M токенов"}


def price_for(model: str, table: dict[str, dict] | None = None) -> tuple[str, dict | None, str]:
    """(ключ таблицы, цена, как нашли: exact / prefix / fallback / ''). Имя без ``models/``; длиннейший префикс
    (``gemini-3.1-pro-preview-customtools`` → ``gemini-3.1-pro-preview``); иначе семейство по слову."""
    table = table if table is not None else DEFAULT_PRICES
    name = str(model or "").strip()
    if name.startswith("models/"):
        name = name[len("models/"):]
    if name in table:
        return name, table[name], "exact"
    prefixes = [k for k in table if name.startswith(k)]
    if prefixes:
        key = max(prefixes, key=len)
        return key, table[key], "prefix"
    lowered = name.lower()
    for word, key in _FAMILY_FALLBACK:
        if word in lowered and key in table:
            return key, table[key], "fallback"
    return "", None, ""


def _as_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _modalities(details: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in details or []:
        if isinstance(item, dict):
            modality = str(item.get("modality") or "TEXT").upper()
            out[modality] = out.get(modality, 0) + _as_int(item.get("tokenCount"))
    return out


def _effective(price: dict, when: date | None, prompt_tokens: int) -> tuple[dict, str]:
    current = {k: v for k, v in price.items() if k in _PRICE_FIELDS or k in ("long_threshold", "long")}
    day = when or datetime.now(timezone.utc).date()
    for change in sorted(price.get("changes") or [], key=lambda c: str(c.get("from") or "")):
        try:
            start = date.fromisoformat(str(change.get("from") or ""))
        except ValueError:
            continue
        if start <= day:
            current.update({k: v for k, v in change.items() if k in _PRICE_FIELDS})
    threshold = _as_int(current.get("long_threshold"))
    if threshold and prompt_tokens > threshold and isinstance(current.get("long"), dict):
        current.update(current["long"])
        return current, "long"
    return current, ""


def compute_cost(usage: dict | None, price: dict | None, when: date | None = None) -> dict:
    """Цена одного ответа в USD по ``usageMetadata``. Нет usage или цены — ``cost_usd: None``.

    Вход: ``promptTokenCount`` включает кэш — без кэша по ``input`` (аудио — ``input_audio``), кэш по ``cached``;
    ``toolUsePromptTokenCount`` — тоже вход. Выход: ``candidatesTokenCount`` (картинка/звук — свои цены по
    ``candidatesTokensDetails``) + ``thoughtsTokenCount`` по ``output``. Порог длинного контекста — по входу."""
    if not isinstance(usage, dict) or not usage or price is None:
        return {"cost_usd": None, "tier": ""}
    prompt = _as_int(usage.get("promptTokenCount"))
    cached = min(prompt, _as_int(usage.get("cachedContentTokenCount")))
    tool = _as_int(usage.get("toolUsePromptTokenCount"))
    candidates = _as_int(usage.get("candidatesTokenCount"))
    thoughts = _as_int(usage.get("thoughtsTokenCount"))
    p, tier = _effective(price, when, prompt + tool)
    rate_in = float(p.get("input") or 0)
    rate_cached = float(p.get("cached", rate_in) or 0)
    rate_out = float(p.get("output") or 0)
    audio_prompt = _modalities(usage.get("promptTokensDetails")).get("AUDIO", 0)
    audio_cached = min(cached, _modalities(usage.get("cacheTokensDetails")).get("AUDIO", 0))
    audio_fresh = max(0, min(prompt - cached, audio_prompt - audio_cached))
    fresh = max(0, prompt - cached) + tool
    out_mod = _modalities(usage.get("candidatesTokensDetails"))
    image_out = min(candidates, out_mod.get("IMAGE", 0))
    audio_out = min(candidates - image_out, out_mod.get("AUDIO", 0))
    text_out = candidates - image_out - audio_out
    cost = (
        (fresh - audio_fresh) * rate_in
        + audio_fresh * float(p.get("input_audio", rate_in) or 0)
        + (cached - audio_cached) * rate_cached
        + audio_cached * float(p.get("cached_audio", rate_cached) or 0)
        + (text_out + thoughts) * rate_out
        + image_out * float(p.get("output_image", rate_out) or 0)
        + audio_out * float(p.get("output_audio", rate_out) or 0)
    ) / 1_000_000
    return {"cost_usd": round(cost, 8), "tier": tier}


# --------------------------------------------------------------------------- #
# Запись
# --------------------------------------------------------------------------- #


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _resolve_project(store: Any, ids: dict) -> None:
    """Нет project_id — найти по клипу/источнику: траты видны и в проекте."""
    try:
        if ids.get("clip_id") and not ids.get("source_id"):
            row = store.db.query_one("SELECT source_id FROM clip_plans WHERE id = ?", (int(ids["clip_id"]),))
            if row and row["source_id"]:
                ids["source_id"] = int(row["source_id"])
        if ids.get("source_id") and not ids.get("project_id"):
            row = store.db.query_one("SELECT project_id FROM sources WHERE id = ?", (int(ids["source_id"]),))
            if row and row["project_id"]:
                ids["project_id"] = int(row["project_id"])
    except Exception:  # noqa: BLE001 - проект — удобство, не повод терять запись
        pass


def _safe_error(exc: BaseException | str) -> str:
    from app.proxy_config import mask_secrets

    text = exc if isinstance(exc, str) else f"{type(exc).__name__}: {exc}"
    return mask_secrets(text)[:300]


def record(model: str, response: Any = None, *, usage: dict | None = None, duration_ms: int | None = None,
           ok: bool = True, error: str = "", method: str = "generate", purpose: str | None = None,
           default_purpose: str | None = None, caller: str | None = None, store: Any = None,
           **ids: Any) -> dict | None:
    """Записать один запрос. ``usage`` — ``usageMetadata`` (иначе берётся из ``response``). Никогда не бросает."""
    try:
        store = store if store is not None else _store()
        if store is None:
            return None
        if usage is None and isinstance(response, dict) and isinstance(response.get("usageMetadata"), dict):
            usage = response["usageMetadata"]
        model_version = str(response.get("modelVersion") or "") if isinstance(response, dict) else ""
        ctx = current_scope()
        auto_purpose, auto_caller = _caller(1)
        row_ids = {k: ctx.get(k) for k in _IDS}
        row_ids.update(_clean_ids(ids))
        _resolve_project(store, row_ids)
        table = prices(store)
        price_key, price, _how = price_for(model_version or model, table)
        if price is None and model_version:
            price_key, price, _how = price_for(model, table)
        rate = usd_rub(store)
        cost = compute_cost(usage, price)
        cost_usd = cost["cost_usd"]
        has_usage = isinstance(usage, dict) and bool(usage)
        u = usage if has_usage else {}

        def tok(key: str) -> int | None:
            return _int_or_none(u.get(key)) if has_usage else None

        values = {
            "created_at": _now(), "model": str(model or "")[:120], "model_version": model_version[:120],
            "method": method, "purpose": (purpose or ctx.get("purpose") or default_purpose or auto_purpose or "")[:80],
            "caller": (caller if caller is not None else auto_caller)[:300],
            "project_id": _int_or_none(row_ids.get("project_id")), "source_id": _int_or_none(row_ids.get("source_id")),
            "clip_id": _int_or_none(row_ids.get("clip_id")), "session": str(row_ids.get("session") or "")[:120],
            "message_id": _int_or_none(row_ids.get("message_id")),
            "prompt_tokens": tok("promptTokenCount"), "cached_tokens": tok("cachedContentTokenCount"),
            "output_tokens": tok("candidatesTokenCount"), "thinking_tokens": tok("thoughtsTokenCount"),
            "tool_prompt_tokens": tok("toolUsePromptTokenCount"), "total_tokens": tok("totalTokenCount"),
            "usage_json": json.dumps(u, ensure_ascii=False)[:4000] if has_usage else "",
            "duration_ms": _int_or_none(duration_ms), "ok": 1 if ok else 0, "error": _safe_error(error) if error else "",
            "price_key": price_key + ("" if _how in ("exact", "prefix", "") else f" ({_how})"), "tier": cost["tier"],
            "usd_rub": rate, "cost_usd": cost_usd,
            "cost_rub": round(cost_usd * rate, 6) if cost_usd is not None else None,
        }
        cols = ", ".join(values)
        marks = ", ".join("?" * len(values))
        cur = store.db.execute(f"INSERT INTO gemini_usage ({cols}) VALUES ({marks})", tuple(values.values()))
        return {"id": int(cur.lastrowid), **values}
    except Exception as exc:  # noqa: BLE001 - учёт не должен ломать запрос
        _log.info("gemini usage: не записал запрос (%s)", type(exc).__name__)
        return None


def tracked(model: str, call: Callable[[], Any], *, method: str = "generate", purpose: str | None = None,
            default_purpose: str | None = None) -> Any:
    """Выполнить запрос и записать его: ответ — с токенами, исключение — ``ok=0`` и причина, потом пробросить."""
    started = time.monotonic()
    try:
        response = call()
    except BaseException as exc:
        record(model, None, duration_ms=int((time.monotonic() - started) * 1000), ok=False, error=_safe_error(exc),
               method=method, purpose=purpose, default_purpose=default_purpose)
        raise
    record(model, response, duration_ms=int((time.monotonic() - started) * 1000), method=method, purpose=purpose,
           default_purpose=default_purpose)
    return response


def tracked_stream(model: str, chunks: Iterator[Any], *, purpose: str | None = None) -> Iterator[Any]:
    """Поток ответа: та же запись, когда поток кончился (или его бросили). Токены — из последнего
    чанка с ``usageMetadata`` (там итог). Брошенный поток — ``ok=0``, ``cancelled``."""
    started = time.monotonic()
    usage: dict | None = None
    last: dict = {}
    ok, error = True, ""
    try:
        for chunk in chunks:
            if isinstance(chunk, dict):
                if isinstance(chunk.get("usageMetadata"), dict):
                    usage = chunk["usageMetadata"]
                if chunk.get("modelVersion"):
                    last = {"modelVersion": chunk["modelVersion"]}
            yield chunk
    except GeneratorExit:
        ok, error = False, "cancelled"
        raise
    except BaseException as exc:
        ok, error = False, _safe_error(exc)
        raise
    finally:
        close = getattr(chunks, "close", None)
        if close is not None:
            try:
                close()
            except Exception:  # noqa: BLE001
                pass
        record(model, last or None, usage=usage, duration_ms=int((time.monotonic() - started) * 1000), ok=ok,
               error=error, method="stream", purpose=purpose)


# --------------------------------------------------------------------------- #
# Чтение
# --------------------------------------------------------------------------- #

GROUPS = {"purpose": "purpose", "model": "model", "day": "substr(created_at, 1, 10)", "session": "session",
          "project": "project_id", "caller": "caller", "message": "message_id"}


def normalize_time(value: Any) -> str | None:
    """``2026-11-12``, ``2026-11-12T10:00``, ``…Z``, ``…+04:00`` → ``YYYY-MM-DD HH:MM:SS`` в UTC (как в базе)."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00").replace(" ", "T", 1))
    except ValueError as exc:
        raise ValueError(f"время «{text}»: нужен ISO, например 2026-11-12 или 2026-11-12T10:00:00Z") from exc
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc).replace(tzinfo=None)
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def _where(filters: dict) -> tuple[str, list]:
    clauses: list[str] = []
    params: list = []
    since = normalize_time(filters.get("since"))
    until = normalize_time(filters.get("until"))
    if since:
        clauses.append("created_at >= ?")
        params.append(since)
    if until:
        clauses.append("created_at < ?")
        params.append(until)
    for key in ("project_id", "source_id", "clip_id", "message_id"):
        if filters.get(key) not in (None, ""):
            clauses.append(f"{key} = ?")
            params.append(int(filters[key]))
    for key in ("session", "purpose", "model"):
        if filters.get(key) not in (None, ""):
            clauses.append(f"{key} = ?")
            params.append(str(filters[key]))
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


_SUMS = ("COUNT(*) AS calls, SUM(CASE WHEN ok = 0 THEN 1 ELSE 0 END) AS errors, "
         "SUM(CASE WHEN cost_usd IS NULL AND ok = 1 THEN 1 ELSE 0 END) AS unpriced, "
         "SUM(prompt_tokens) AS prompt_tokens, SUM(cached_tokens) AS cached_tokens, "
         "SUM(output_tokens) AS output_tokens, SUM(thinking_tokens) AS thinking_tokens, "
         "SUM(total_tokens) AS total_tokens, SUM(duration_ms) AS duration_ms, "
         "SUM(cost_usd) AS cost_usd, SUM(cost_rub) AS cost_rub, MIN(created_at) AS first_at, MAX(created_at) AS last_at")


def _sums(row: Any) -> dict:
    data = dict(row) if row is not None else {}
    out: dict = {}
    for key in ("calls", "errors", "unpriced", "prompt_tokens", "cached_tokens", "output_tokens", "thinking_tokens",
                "total_tokens", "duration_ms"):
        out[key] = int(data.get(key) or 0)
    out["cost_usd"] = round(float(data.get("cost_usd") or 0), 6)
    out["cost_rub"] = round(float(data.get("cost_rub") or 0), 4)
    out["first_at"] = data.get("first_at")
    out["last_at"] = data.get("last_at")
    return out


def totals(store: Any, group_by: str | None = None, limit: int = 200, **filters: Any) -> dict:
    """Итог за выборку и, если задано, по группам (дороже — выше; по дням — по порядку дней)."""
    where, params = _where(filters)
    total = _sums(store.db.query_one(f"SELECT {_SUMS} FROM gemini_usage{where}", params))
    result: dict = {"total": total, "filters": {k: v for k, v in filters.items() if v not in (None, "")},
                    "usd_rub": usd_rub(store)}
    if group_by:
        if group_by not in GROUPS:
            raise ValueError(f"group_by: одно из {', '.join(GROUPS)}")
        expr = GROUPS[group_by]
        order = "key DESC" if group_by == "day" else "SUM(cost_usd) DESC, calls DESC"
        rows = store.db.query_all(
            f"SELECT {expr} AS key, {_SUMS} FROM gemini_usage{where} GROUP BY {expr} ORDER BY {order} LIMIT ?",
            [*params, max(1, min(int(limit), 1000))])
        result["group_by"] = group_by
        result["groups"] = [{"key": r["key"], **_sums(r)} for r in rows]
    return result


def calls(store: Any, limit: int = 100, offset: int = 0, **filters: Any) -> dict:
    where, params = _where(filters)
    rows = store.db.query_all(f"SELECT * FROM gemini_usage{where} ORDER BY id DESC LIMIT ? OFFSET ?",
                              [*params, max(1, min(int(limit), 1000)), max(0, int(offset))])
    items = []
    for row in rows:
        item = dict(row)
        item["ok"] = bool(item.get("ok"))
        item.pop("usage_json", None)
        items.append(item)
    return {"calls": items, "limit": limit, "offset": offset}


def session_summary(store: Any, session: str) -> dict:
    """Короткий итог переписки/анализа для шапки чата: «потрачено N ₽» и по ответам агента."""
    try:
        data = totals(store, group_by="message", session=session)
    except Exception:  # noqa: BLE001 - таблицы ещё нет (старая база в тесте) — пусто
        return {"session": session, "calls": 0, "cost_usd": 0.0, "cost_rub": 0.0, "by_message": {}}
    total = data["total"]
    return {"session": session, "calls": total["calls"], "errors": total["errors"], "unpriced": total["unpriced"],
            "total_tokens": total["total_tokens"], "cost_usd": total["cost_usd"], "cost_rub": total["cost_rub"],
            "by_message": {str(g["key"]): {"calls": g["calls"], "cost_rub": g["cost_rub"], "cost_usd": g["cost_usd"],
                                           "total_tokens": g["total_tokens"]}
                           for g in data.get("groups", []) if g["key"] is not None}}


def recompute(store: Any, **filters: Any) -> dict:
    """Пересчитать цену записанных строк по нынешней таблице и курсу (после правки цен)."""
    where, params = _where(filters)
    table = prices(store)
    rate = usd_rub(store)
    rows = store.db.query_all(f"SELECT id, model, model_version, usage_json, created_at FROM gemini_usage{where}",
                              params)
    changed = 0
    with store.db.transaction():
        for row in rows:
            try:
                usage = json.loads(row["usage_json"]) if row["usage_json"] else None
            except ValueError:
                usage = None
            key, price, how = price_for(row["model_version"] or row["model"], table)
            if price is None and row["model_version"]:
                key, price, how = price_for(row["model"], table)
            try:
                when = date.fromisoformat(str(row["created_at"])[:10])
            except ValueError:
                when = None
            cost = compute_cost(usage, price, when)
            usd = cost["cost_usd"]
            store.db.execute(
                "UPDATE gemini_usage SET cost_usd = ?, cost_rub = ?, usd_rub = ?, price_key = ?, tier = ? WHERE id = ?",
                (usd, round(usd * rate, 6) if usd is not None else None, rate,
                 key + ("" if how in ("exact", "prefix", "") else f" ({how})"), cost["tier"], int(row["id"])))
            changed += 1
    return {"updated": changed, "usd_rub": rate}
